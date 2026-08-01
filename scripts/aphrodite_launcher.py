#!/usr/bin/env python3
"""Standalone launcher for Aphrodite Agent.

PyInstaller one-file entry point. Handles both frozen (bundled) and
source runs. On Windows, bundled data lives in sys._MEIPASS, not next
to the .exe.
"""

from __future__ import annotations

import sys
import os
import traceback
from pathlib import Path


def _meipass() -> Path:
    """Return the PyInstaller extraction directory, or repo root."""
    if getattr(sys, "frozen", False):
        # PyInstaller one-file extracts to sys._MEIPASS at runtime
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    # Running from source
    return Path(__file__).parent.parent.resolve()


def _exe_dir() -> Path:
    """Directory containing the executable (for config/data paths)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path.home()


def _ensure_home_dirs() -> tuple[Path, Path]:
    """Create and return (data_dir, config_dir)."""
    if sys.platform == "win32":
        base = Path.home()
        data_dir = base / ".local" / "share" / "aphrodite-agent"
        config_dir = base / ".config" / "aphrodite-agent"
    else:
        data_dir = Path.home() / ".local" / "share" / "aphrodite-agent"
        config_dir = Path.home() / ".config" / "aphrodite-agent"

    data_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    return data_dir, config_dir


def main():
    # --- 1. Locate bundled files ---
    meipass = _meipass()
    exe_dir = _exe_dir()

    # Add bundled src/ to Python path so imports work
    src_candidates = [
        meipass / "src",
        meipass,
        exe_dir / "src",
        exe_dir,
    ]
    for cand in src_candidates:
        if cand.exists() and str(cand) not in sys.path:
            sys.path.insert(0, str(cand))

    # --- 2. Ensure config directories exist ---
    data_dir, config_dir = _ensure_home_dirs()

    # Copy sample config if missing
    default_config = config_dir / "aphrodite.toml"
    if not default_config.exists():
        sample_candidates = [
            meipass / "aphrodite.toml",
            exe_dir / "aphrodite.toml",
            Path(__file__).parent.parent / "aphrodite.toml",
        ]
        for sample in sample_candidates:
            if sample.exists():
                import shutil

                shutil.copy(sample, default_config)
                break

    # --- 3. Tell the API server where to find bundled static files ---
    static_candidates = [
        meipass / "aphrodite" / "api" / "static",
        meipass / "src" / "aphrodite" / "api" / "static",
        exe_dir / "aphrodite" / "api" / "static",
    ]
    for static in static_candidates:
        if static.exists():
            os.environ["APHRODITE_STATIC_DIR"] = str(static)
            break

    # --- 4. Import and run CLI ---
    try:
        from aphrodite_cli.main import cli
    except ImportError:
        print("=" * 60, file=sys.stderr)
        print("Aphrodite Agent launcher failed to import.", file=sys.stderr)
        print(f"  sys.path: {sys.path}", file=sys.stderr)
        print(f"  meipass:  {meipass}", file=sys.stderr)
        print(f"  exe_dir:  {exe_dir}", file=sys.stderr)
        print("-" * 60, file=sys.stderr)
        traceback.print_exc()
        print("=" * 60, file=sys.stderr)
        sys.exit(1)

    cli()


if __name__ == "__main__":
    main()
