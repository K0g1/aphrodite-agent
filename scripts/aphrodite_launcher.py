#!/usr/bin/env python3
"""Standalone launcher for Aphrodite Agent.

This script is the PyInstaller entry point. It ensures the bundled
application finds its assets and runs the CLI regardless of CWD.
"""

import sys
import os
from pathlib import Path


def _get_bundle_dir() -> Path:
    """Return the directory containing the bundled executable."""
    if getattr(sys, "frozen", False):
        # PyInstaller sets sys.frozen and sys._MEIPASS
        return Path(sys.executable).parent
    return Path(__file__).parent.parent.resolve()


def main():
    bundle_dir = _get_bundle_dir()

    # Ensure bundled static assets are found
    static_dir = bundle_dir / "aphrodite" / "api" / "static"
    os.environ.setdefault("APHRODITE_STATIC_DIR", str(static_dir))

    # Add bundled src to path if running from bundle
    src_dir = bundle_dir / "src"
    if src_dir.exists() and str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    # Create default config directories if missing
    data_dir = Path.home() / ".local" / "share" / "aphrodite-agent"
    config_dir = Path.home() / ".config" / "aphrodite-agent"
    data_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    # Default config
    default_config = config_dir / "aphrodite.toml"
    if not default_config.exists():
        sample = bundle_dir / "aphrodite.toml"
        if sample.exists():
            import shutil
            shutil.copy(sample, default_config)

    # Import and run CLI
    try:
        from aphrodite_cli.main import cli
    except ImportError:
        # Fallback: add src/ to path
        repo_src = bundle_dir / "src"
        if str(repo_src) not in sys.path:
            sys.path.insert(0, str(repo_src))
        from aphrodite_cli.main import cli

    cli()


if __name__ == "__main__":
    main()
