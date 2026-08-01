"""Shared test configuration.

Reconfigures stdio to UTF-8 so assertion output and CLI tests that print
emoji/CJK text do not crash on Windows (default ANSI codepage).
"""

from __future__ import annotations

import sys

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is None:
        continue
    try:
        _reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass
