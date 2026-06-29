"""File-listing helpers for the NAM plugin panel.

Scans the user NAM library at ``$MOD_USER_FILES_DIR/NAM Models`` (or
``~/data/user-files/NAM Models`` as a fallback) recursively and resolves
the currently-loaded model against the list.

The list is built once when the panel opens and never changes for the
panel's lifetime — model picking is a single selection action, not a
live-watcher. Cheap to re-scan if the user really did drop a new file in
mid-session; we re-scan on every panel open so missing a recent file
just means "reopen the panel".
"""

from __future__ import annotations

import os
import urllib.parse
from pathlib import Path


def _default_nam_dir() -> Path:
    """Resolve the user NAM library root, honouring the project's standard
    ``$MOD_USER_FILES_DIR`` override (used by the rest of pi-stomp — see
    the NAM capture panel in ``pistomp/nam/panel.py``) and falling back
    to ``~/data/user-files/NAM Models``.
    """
    env = os.environ.get("MOD_USER_FILES_DIR")
    if env:
        return Path(env) / "NAM Models"
    return Path(os.path.expanduser("~/data/user-files/NAM Models"))


# File extensions mod-host's NAM LV2 advertises as valid model files
# (`mod:fileTypes "nam,nammodel,json,aidax,aidadspmodel"` in
# neural_amp_modeler.lv2/neural_amp_modeler.ttl).
_NAM_EXTS = frozenset({".nam", ".nammodel", ".json", ".aidax", ".aidadspmodel"})


def list_nam_files(root: Path | str | None = None) -> list[Path]:
    """Return NAM model files under *root*, sorted case-insensitively.

    Missing directories yield an empty list (no exception) so the panel
    can still open on a fresh install with no user files yet.
    """
    base = Path(os.path.expanduser(root)) if root else _default_nam_dir()
    if not base.exists():
        return []
    return sorted(
        (p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in _NAM_EXTS),
        key=lambda p: str(p).lower(),
    )


def current_index(files: list[Path], model_path: str | None) -> int:
    """Index of the file in *files* whose basename matches the loaded model.

    The plugin TTL stores the path URL-encoded (e.g. spaces as ``%20``),
    so we decode before comparing. Returns ``-1`` if no match.
    """
    if not model_path:
        return -1
    decoded = urllib.parse.unquote(model_path)
    target = Path(decoded).name
    for i, p in enumerate(files):
        if p.name == target:
            return i
    return -1
