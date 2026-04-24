"""Validate that every locale JSON file has the same key-tree as en.json.

Usage:
    python scripts/validate_locales.py [--locales-dir locales]

Exit code 0 = all locales match, non-zero = missing/extra keys detected.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MASTER = "en"
SUPPORTED = ["en", "de", "fr", "es", "it"]


def flatten_keys(obj, prefix: str = "") -> set[str]:
    """Return the set of dotted leaf-key paths in a nested dict."""
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict) and v:
                keys |= flatten_keys(v, path)
            else:
                keys.add(path)
    return keys


def load(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--locales-dir", default="locales",
                    help="Path to the directory holding {en,de,...}.json")
    args = ap.parse_args()

    base = Path(args.locales_dir)
    if not base.is_dir():
        print(f"ERROR: {base} is not a directory", file=sys.stderr)
        return 2

    master_path = base / f"{MASTER}.json"
    if not master_path.is_file():
        print(f"ERROR: master locale {master_path} not found", file=sys.stderr)
        return 2

    master_keys = flatten_keys(load(master_path))
    print(f"[{MASTER}] {len(master_keys)} keys (master)")

    fail = False
    for lang in SUPPORTED:
        if lang == MASTER:
            continue
        path = base / f"{lang}.json"
        if not path.is_file():
            print(f"[{lang}] MISSING FILE ({path})")
            fail = True
            continue
        keys = flatten_keys(load(path))
        missing = master_keys - keys
        extra = keys - master_keys
        if not missing and not extra:
            print(f"[{lang}] OK ({len(keys)} keys)")
            continue
        fail = True
        print(f"[{lang}] MISMATCH")
        for k in sorted(missing):
            print(f"  - missing: {k}")
        for k in sorted(extra):
            print(f"  - extra:   {k}")

    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
