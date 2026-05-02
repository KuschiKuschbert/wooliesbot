#!/usr/bin/env python3
"""Helpers for chef_os.sync_to_github push handling.

Kept as a separate module so chef_os.py stays under its size guardrail and
because the PR-fallback path is independent of the rest of the scraper.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time


def push_main_with_pr_fallback() -> None:
    """Push the current local commit to origin/main, with retry-after-rebase and
    a PR auto-merge fallback if direct push is rejected by branch protection.

    Raises RuntimeError if the push cannot be made to land on main by any path.
    """
    push_r = subprocess.run(
        ["git", "push", "origin", "main"], capture_output=True, text=True
    )
    if push_r.returncode == 0:
        logging.info("Successfully pushed updated data & heartbeat to GitHub.")
        return

    err_text = (push_r.stderr or push_r.stdout or "").strip()
    logging.warning(
        f"git push failed (exit {push_r.returncode}), retrying after pull — {err_text}"
    )
    pull2 = subprocess.run(
        ["git", "pull", "--rebase", "origin", "main"], capture_output=True, text=True
    )
    if pull2.returncode == 0:
        push2 = subprocess.run(
            ["git", "push", "origin", "main"], capture_output=True, text=True
        )
        if push2.returncode == 0:
            logging.info("Successfully pushed updated data & heartbeat to GitHub (after rebase).")
            return
        err_text = (push2.stderr or push2.stdout or "").strip()
        logging.warning(f"git push failed after rebase — {err_text}")
    else:
        err_text = (pull2.stderr or pull2.stdout or "").strip()
        logging.warning(f"git pull --rebase retry failed — {err_text}")

    if looks_like_protection_rejection(err_text):
        if open_auto_merge_pr_fallback():
            logging.info("Direct push rejected by branch protection; opened auto-merge PR fallback.")
            return
        raise RuntimeError(
            "Direct push to main rejected by branch protection AND PR auto-merge fallback failed. "
            f"Last error: {err_text[:500]}"
        )

    raise RuntimeError(f"git push failed: {err_text[:500]}")


def looks_like_protection_rejection(err_text: str) -> bool:
    """Heuristic: did `git push` fail because of branch protection / repository rules?"""
    if not err_text:
        return False
    needles = ("rule violations", "GH013", "remote rejected", "protected branch", "push declined")
    low = err_text.lower()
    return any(n.lower() in low for n in needles)


def open_auto_merge_pr_fallback() -> bool:
    """Move the most recent local commit to a fresh bot branch and open an auto-merging PR.

    Used as a fallback in `chef_os.sync_to_github` when direct-to-main push is rejected
    by repository rules (e.g. the deploy-key bypass is removed). Requires `gh` CLI in PATH
    and a `GH_TOKEN`/`GITHUB_TOKEN` env var with `contents: write` and `pull-requests: write`.
    Returns True on success (PR created and auto-merge enabled), False otherwise.
    """
    if not shutil.which("gh"):
        logging.error("Cannot open auto-merge PR: `gh` CLI not available on PATH.")
        return False
    gh_token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not gh_token:
        logging.error("Cannot open auto-merge PR: no GH_TOKEN/GITHUB_TOKEN in env.")
        return False
    env = {**os.environ, "GH_TOKEN": gh_token}

    branch_name = f"bot/auto-update-{int(time.time())}"
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    if not head_sha:
        logging.error("Cannot open auto-merge PR: failed to read HEAD sha.")
        return False

    create_r = subprocess.run(
        ["git", "branch", branch_name, head_sha], capture_output=True, text=True
    )
    if create_r.returncode != 0:
        logging.error(f"failed to create bot branch {branch_name}: {(create_r.stderr or '').strip()}")
        return False
    reset_r = subprocess.run(
        ["git", "reset", "--hard", "origin/main"], capture_output=True, text=True
    )
    if reset_r.returncode != 0:
        logging.error(f"failed to reset main to origin/main: {(reset_r.stderr or '').strip()}")
        return False
    co_r = subprocess.run(
        ["git", "checkout", branch_name], capture_output=True, text=True
    )
    if co_r.returncode != 0:
        logging.error(f"failed to checkout {branch_name}: {(co_r.stderr or '').strip()}")
        return False
    push_r = subprocess.run(
        ["git", "push", "-u", "origin", branch_name], capture_output=True, text=True
    )
    if push_r.returncode != 0:
        logging.error(
            f"failed to push {branch_name}: {(push_r.stderr or push_r.stdout or '').strip()}"
        )
        return False

    pr_create = subprocess.run(
        [
            "gh", "pr", "create",
            "--base", "main",
            "--head", branch_name,
            "--title", "Auto-update dashboard data",
            "--body", (
                "Automated 4h scrape data update.\n\n"
                "Created by `chef_os.sync_to_github` PR-fallback because direct push to main "
                "was rejected by branch protection. Auto-merging once required checks pass."
            ),
        ],
        env=env, capture_output=True, text=True, check=False,
    )
    if pr_create.returncode != 0:
        logging.error(f"gh pr create failed: {(pr_create.stderr or '').strip()}")
        return False
    pr_url = (pr_create.stdout or "").strip().splitlines()[-1] if pr_create.stdout else ""
    if not pr_url:
        logging.error("gh pr create succeeded but returned no PR URL.")
        return False

    pr_merge = subprocess.run(
        ["gh", "pr", "merge", pr_url, "--auto", "--squash", "--delete-branch"],
        env=env, capture_output=True, text=True, check=False,
    )
    if pr_merge.returncode != 0:
        logging.error(f"gh pr merge --auto failed: {(pr_merge.stderr or '').strip()}")
        return False

    logging.info(f"Auto-merge PR opened: {pr_url}")
    return True
