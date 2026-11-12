"""Build a flat AstrBot plugin ZIP from the repository root.

AstrBot's archive validator expects ``metadata.yaml`` at the ZIP root, with
forward-slash entry names. Windows ``Compress-Archive`` can produce backslash
entries and an extra top-level folder, which fails installation.

Usage:
    python tools/package.py
"""

from __future__ import annotations

import shutil
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
STABLE = DIST / "astrbot_plugin_img_api.zip"
LATEST = DIST / "astrbot_plugin_img_api_latest.zip"
TIMESTAMPED = DIST / f"astrbot_plugin_img_api_{time.strftime('%Y%m%d_%H%M%S')}.zip"

EXCLUDED_DIRS = {
    ".git",
    ".github",
    "__pycache__",
    ".ruff_cache",
    ".pytest_cache",
    ".venv",
    "venv",
    "dist",
    "node_modules",
}
EXCLUDED_NAMES = {".DS_Store"}
EXCLUDED_PREFIXES = ("_preview", "preview-image")


def iter_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if path.is_dir():
            continue
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.name in EXCLUDED_NAMES or path.name.startswith(EXCLUDED_PREFIXES):
            continue
        if path.suffix.lower() == ".zip":
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(ROOT).as_posix())


def main() -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    files = iter_files()
    with zipfile.ZipFile(STABLE, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())

    shutil.copy2(STABLE, LATEST)
    shutil.copy2(STABLE, TIMESTAMPED)

    with zipfile.ZipFile(STABLE) as archive:
        names = archive.namelist()

    print(f"created: {STABLE}")
    print(f"latest:  {LATEST}")
    print(f"archive: {TIMESTAMPED}")
    print(f"entries: {len(names)}")
    print(f"root metadata: {'metadata.yaml' in names}")
    print(f"backslashes: {any(chr(92) in name for name in names)}")


if __name__ == "__main__":
    main()
