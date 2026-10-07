"""Shared directory-walk pruning for personal-document indexing (#5559).

Single source of the hidden-dir / junk-dir / hidden-file skip so the vector
index (``rag_vector.index_personal_documents``) and the keyword index
(``personal_docs.load_personal_index``) apply the exact same policy and cannot
drift — the drift is what left the keyword path sweeping in `.obsidian/`,
`.git/`, and `node_modules/` after the vector path was fixed.
"""
import os
from typing import List, Set

# Well-known non-hidden junk directories to skip. Matched case-insensitively so
# a `Node_Modules` on a case-insensitive filesystem (macOS default) is still
# pruned. Hidden directories (dot-prefixed) are pruned separately. Kept
# deliberately small: over-pruning would silently drop a user's real content
# (e.g. a notes directory legitimately named "build").
EXCLUDED_DIR_NAMES: Set[str] = {'node_modules', '__pycache__', 'venv'}


def prune_index_dirs(dirs: List[str]) -> None:
    """In-place ``os.walk`` (topdown) directory prune: drop hidden and known
    junk directories so the walk never descends into them.

    The explicitly-targeted walk root is never a member of ``dirs`` (it is the
    ``dirpath`` argument), so it stays exempt — a user who deliberately points
    indexing at a hidden directory gets its contents, minus nested junk.
    """
    dirs[:] = [
        d for d in dirs
        if not d.startswith('.') and d.lower() not in EXCLUDED_DIR_NAMES
    ]


def is_indexable_file(name: str) -> bool:
    """A file is indexable only if it is not hidden (dot-prefixed)."""
    return not name.startswith('.')


def path_stays_inside(path: str, root: str) -> bool:
    """True when ``path`` resolves to ``root`` or a location inside it.

    ``os.walk`` does not follow directory symlinks, but opening a file symlink
    reads the target. A notes file inside the personal-docs tree that points
    at another user's file or ``/etc`` must not be indexed. The walk root
    itself is also checked so a tracked directory that is a symlink out of
    the tree is skipped (``os.walk`` still enters its start path).
    """
    try:
        real = os.path.realpath(path)
        base = os.path.realpath(root)
        return os.path.commonpath([real, base]) == base
    except (ValueError, OSError):
        return False
