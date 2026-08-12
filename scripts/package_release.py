#!/usr/bin/env python3
"""Create a distributable source ZIP without patient data or local secrets."""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent / f"clinic-ai-assistant-{(ROOT / 'VERSION').read_text().strip()}.zip"
EXCLUDED_PARTS = {'.git', '__pycache__', '.pytest_cache', '.ruff_cache', 'data', 'dist', 'build', '.review-venv', '.venv', '.clinic-venv', 'node_modules'}
EXCLUDED_SUFFIXES = {'.pyc', '.pyo', '.db', '.log', '.key', '.enc'}

def keep(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    # Editable installs leave ``*.egg-info`` metadata in the source tree.
    # It is not patient data, but it is an environment-specific build
    # artifact and should never become part of a reproducible source release.
    return (not any(part in EXCLUDED_PARTS or part.endswith('.egg-info') for part in rel.parts)
            and path.suffix not in EXCLUDED_SUFFIXES)

if OUT.exists(): OUT.unlink()
# Build explicitly: shutil.make_archive cannot filter sensitive runtime files.
import zipfile
with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED) as z:
    for path in ROOT.rglob('*'):
        if path.is_file() and keep(path): z.write(path, Path(ROOT.name) / path.relative_to(ROOT))
print(f'Created {OUT}')
