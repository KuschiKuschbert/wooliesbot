#!/usr/bin/env python3
"""Soft-ratchet file size guardrails for authored source files."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "scripts" / "file_size_guardrails.json"


@dataclass(frozen=True)
class Issue:
    level: str
    path: str
    message: str


def _load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _run_git(args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _tracked_files() -> list[str]:
    out = _run_git(["ls-files"])
    return [line for line in out.splitlines() if line]


def _detect_base_ref() -> str:
    base_ref = (
        sys.argv[1]
        if len(sys.argv) > 1
        else (os.environ.get("GITHUB_BASE_REF") or "").strip()
    )
    if base_ref:
        return f"origin/{base_ref}"
    for candidate in ("origin/main", "origin/master"):
        if _run_git(["rev-parse", "--verify", candidate]):
            return candidate
    return ""


def _diff_paths(diff_range: str) -> set[str]:
    out = _run_git(["diff", "--name-only", diff_range, "--diff-filter=ACMR"])
    return {line.strip() for line in out.splitlines() if line.strip()}


def _changed_files() -> tuple[set[str], str]:
    base_ref = _detect_base_ref()
    if base_ref:
        changed = _diff_paths(f"{base_ref}...HEAD")
        if changed:
            return changed, f"{base_ref}...HEAD"

    for candidate in ("origin/main", "origin/master"):
        if not _run_git(["rev-parse", "--verify", candidate]):
            continue
        changed = _diff_paths(f"{candidate}...HEAD")
        if changed:
            return changed, f"{candidate}...HEAD"

    if _run_git(["rev-parse", "--verify", "HEAD~1"]):
        changed = _diff_paths("HEAD~1..HEAD")
        if changed:
            return changed, "HEAD~1..HEAD"

    return set(), ""


def _line_count(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="ignore")
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _is_excluded(path: str, exclude_globs: Iterable[str]) -> bool:
    for pattern in exclude_globs:
        if fnmatch(path, pattern):
            return True
    return False


def main() -> int:
    config = _load_config()
    include_exts = set(config["include_extensions"])
    exclude_globs = config["exclude_globs"]
    default_max = config["default_max_lines"]
    hard_max_new = config["new_file_hard_max_lines"]
    allowance = int(config["legacy_growth_allowance_lines"])
    baseline = {k: int(v) for k, v in config["baseline_max_lines"].items()}
    exceptions = {item["path"]: item for item in config.get("exceptions", [])}
    changed, changed_source = _changed_files()
    if not changed:
        print("ERROR: unable to determine changed files for guardrail scope.")
        print(
            "Expected one of: origin/<base>...HEAD, origin/main...HEAD, origin/master...HEAD, or HEAD~1..HEAD."
        )
        print("Guardrail failure: provide base refs/history in CI checkout or run against a branch with commits.")
        return 1
    print(f"Guardrail scope determined from: {changed_source} ({len(changed)} changed files)")

    issues: list[Issue] = []
    checked = 0

    for rel_path in _tracked_files():
        ext = Path(rel_path).suffix
        if ext not in include_exts:
            continue
        if _is_excluded(rel_path, exclude_globs):
            continue
        file_path = ROOT / rel_path
        if not file_path.exists():
            continue

        checked += 1
        lines = _line_count(file_path)
        is_changed = rel_path in changed
        baseline_limit = baseline.get(rel_path)

        if baseline_limit is not None:
            growth_cap = baseline_limit + allowance
            if lines > growth_cap and is_changed:
                issues.append(
                    Issue(
                        "ERROR",
                        rel_path,
                        f"{lines} lines exceeds ratchet cap {growth_cap} (baseline {baseline_limit}, allowance +{allowance})",
                    )
                )
            elif lines > baseline_limit and is_changed:
                issues.append(
                    Issue(
                        "WARN",
                        rel_path,
                        f"{lines} lines grew above baseline {baseline_limit}; keep reducing over time",
                    )
                )
            continue

        soft = int(default_max[ext])
        hard = int(hard_max_new[ext])
        if lines > hard and is_changed:
            issues.append(
                Issue(
                    "ERROR",
                    rel_path,
                    f"{lines} lines exceeds hard cap {hard} for new/unbaselined {ext} file",
                )
            )
        elif lines > soft and is_changed:
            issues.append(
                Issue(
                    "WARN",
                    rel_path,
                    f"{lines} lines exceeds soft cap {soft} for {ext} file",
                )
            )

    errors = [issue for issue in issues if issue.level == "ERROR"]
    warns = [issue for issue in issues if issue.level == "WARN"]

    print(f"File size guardrails checked {checked} files.")
    if exceptions:
        print("Active exceptions:")
        for path, meta in sorted(exceptions.items()):
            print(
                f"- {path}: {meta.get('reason', 'n/a')} "
                f"(owner={meta.get('owner', 'n/a')}, expires={meta.get('expires', 'n/a')})"
            )

    for issue in warns:
        print(f"WARN: {issue.path} -> {issue.message}")
    for issue in errors:
        print(f"ERROR: {issue.path} -> {issue.message}")

    if errors:
        print(
            "\nGuardrail failure: split files, extract modules, or update baseline with rationale."
        )
        return 1
    print("Guardrail check passed (soft-ratchet mode).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
