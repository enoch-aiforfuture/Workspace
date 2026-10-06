#!/usr/bin/env python3
"""Rebrand an upstream Odysseus snapshot as Workspace.

Run from the repo root on a tree imported from upstream. Rewrites tracked text
files and renames tracked paths, then reports any remaining matches.

    python3 scripts/rebrand.py            # apply
    python3 scripts/rebrand.py --dry-run  # report only
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

# AGPL-3.0 requires third-party copyright and license notices to stay intact.
EXCLUDE = {
    "LICENSE",
    "ACKNOWLEDGMENTS.md",
    "NOTICE",
    "scripts/rebrand.py",
}
EXCLUDE_PREFIXES = ("licenses/",)

# Ordered: specific upstream URLs before the bare name.
REPLACEMENTS = [
    ("ghcr.io/odysseus-dev/odysseus", "workspace"),
    ("odysseus-dev.github.io/odysseus", "enoch-aiforfuture.github.io/Workspace"),
    ("odysseus-dev/odysseus", "enoch-aiforfuture/Workspace"),
    ("odysseus-dev", "enoch-aiforfuture"),
    ("ODYSSEUS", "WORKSPACE"),
    ("Odysseus", "Workspace"),
    ("odysseus", "workspace"),
]
PATTERN = re.compile("|".join(re.escape(old) for old, _ in REPLACEMENTS))
LOOKUP = dict(REPLACEMENTS)


def rebrand(text: str) -> str:
    text = PATTERN.sub(lambda m: LOOKUP[m.group(0)], text)
    # API token / shell prefix leftovers. Must NOT use a bare "ody_" replace:
    # that substring sits inside ordinary English identifiers like "body_".
    text = text.replace("Bearer ody_", "Bearer wsp_")
    text = text.replace('"ody_', '"wsp_')
    text = text.replace("'ody_", "'wsp_")
    text = text.replace("ODY_USER", "WSP_USER")
    text = text.replace("_ODY_", "_WSP_")
    text = text.replace("_ody_", "_wsp_")
    text = text.replace("__ody_", "__wsp_")
    return text


def excluded(path: str) -> bool:
    return path in EXCLUDE or path.startswith(EXCLUDE_PREFIXES)


def tracked_files() -> list[str]:
    out = subprocess.check_output(["git", "ls-files", "-z"])
    return [p for p in out.decode().split("\0") if p]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    files = tracked_files()
    changed = 0
    skipped_binary = []
    for path in files:
        if excluded(path) or not Path(path).is_file():
            continue
        raw = Path(path).read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            if re.search(rb"odysseus", raw, re.I):
                skipped_binary.append(path)
            continue
        new = rebrand(text)
        if new != text:
            changed += 1
            if not args.dry_run:
                Path(path).write_bytes(new.encode("utf-8"))

    renames = []
    for path in files:
        if excluded(path):
            continue
        new_path = rebrand(path)
        if new_path != path:
            renames.append((path, new_path))
    collisions = [(o, n) for o, n in renames if n in files]
    if collisions:
        for o, n in collisions:
            print(f"path collision: {o} -> {n}", file=sys.stderr)
        return 1
    if not args.dry_run:
        for old, new in renames:
            Path(new).parent.mkdir(parents=True, exist_ok=True)
            subprocess.check_call(["git", "mv", "-k", old, new])
        subprocess.call(["find", ".", "-type", "d", "-empty", "-not", "-path", "./.git/*", "-delete"])

    print(f"files rewritten: {changed}")
    print(f"paths renamed: {len(renames)}")
    if skipped_binary:
        print("binary files mentioning the old name (replace by hand):")
        for p in skipped_binary:
            print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
