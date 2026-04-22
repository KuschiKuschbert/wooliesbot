#!/usr/bin/env python3
"""
Generate docs/env.local.js from .env.local for dashboard runtime bootstrap.

Reads:
  WOOLIESBOT_WRITE_API_URL
  WOOLIESBOT_WRITE_API_SECRET
"""

from __future__ import annotations

from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env.local"
OUT_PATH = ROOT / "docs" / "env.local.js"


def parse_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        values[key] = val
    return values


def main() -> None:
    env = parse_env(ENV_PATH)
    payload = {
        "writeApiUrl": env.get("WOOLIESBOT_WRITE_API_URL", "").strip(),
        "writeApiSecret": env.get("WOOLIESBOT_WRITE_API_SECRET", ""),
    }
    out = (
        "// Runtime config for local/dev usage.\n"
        "// Auto-generated from .env.local by scripts/generate_runtime_env.py\n"
        f"window.__WOOLIESBOT_ENV__ = {json.dumps(payload, indent=4)};\n"
    )
    OUT_PATH.write_text(out, encoding="utf-8")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
