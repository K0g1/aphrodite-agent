"""Aphrodite Agent - Core package"""

from __future__ import annotations

import sys

__version__ = "0.1.0"


def enable_utf8_stdio() -> None:
    """Reconfigure stdout/stderr to UTF-8 where possible.

    Windows consoles default to the ANSI codepage (e.g. cp1252), which cannot
    encode the emoji and CJK text this app prints. Reconfiguring avoids
    UnicodeEncodeError crashes on Windows; no-op elsewhere.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
