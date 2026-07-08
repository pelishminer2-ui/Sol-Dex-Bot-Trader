"""Revert the regime-aware ENTRY tuning to the old bottom line.

Reads data/regime_tuning_revert.json and writes the ``revert_to_old`` keys back
into .env. Only touches ENTRY-selection keys; exits/learning are never changed.
Restart the Flask server after running so the new config loads.
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
REVERT_FILE = PROJECT_ROOT / "data" / "regime_tuning_revert.json"
ENV_FILE = PROJECT_ROOT / ".env"


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def main() -> int:
    if not REVERT_FILE.exists():
        print(f"ERROR: revert reference not found: {REVERT_FILE}")
        return 1
    data = json.loads(REVERT_FILE.read_text(encoding="utf-8"))
    revert = data.get("revert_to_old") or {}
    if not revert:
        print("ERROR: no revert_to_old values in revert reference")
        return 1

    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    pending = {k: _fmt(v) for k, v in revert.items()}
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        matched = False
        for key, formatted in pending.items():
            if line.startswith(f"{key}="):
                out.append(f"{key}={formatted}")
                seen.add(key)
                matched = True
                break
        if not matched:
            out.append(line)
    for key, formatted in pending.items():
        if key not in seen:
            out.append(f"{key}={formatted}")

    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
    for key, formatted in pending.items():
        print(f"  reverted {key}={formatted}")
    print("Done. Restart the Flask server so config reloads.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
