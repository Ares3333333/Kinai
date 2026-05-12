"""Force UTF-8 everywhere on Windows.

Why: on Windows the default console code page is cp1252/cp866, which silently
replaces unsupported chars with ``?`` when libraries write through stdout
that's wired to a pipe (Start-Process -RedirectStandardOutput, child of
PowerShell, etc). That's how Russian coach commands ("Сделай выдох") got
written into ``coach_memory.jsonl`` as ``"?????? ???????"``.

Two safeguards:

1. ``sys.stdout`` and ``sys.stderr`` are reconfigured to ``utf-8`` with
   ``errors="replace"`` so even if a downstream consumer can't render a
   glyph the underlying bytes stay clean.
2. ``PYTHONIOENCODING`` and ``PYTHONUTF8`` are set on os.environ so that any
   subprocess we spawn (LLM CLI helpers, ffmpeg, etc) inherits the same
   default.

Call ``force_utf8()`` as the very first thing in any entrypoint script
(``app.py``, ``site_server.py``, migration tools, tests).
"""

from __future__ import annotations

import io
import os
import sys


def force_utf8() -> None:
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
                continue
            except (ValueError, OSError):
                pass
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            try:
                wrapped = io.TextIOWrapper(buffer, encoding="utf-8", errors="replace", line_buffering=True)
                setattr(sys, stream_name, wrapped)
            except (ValueError, OSError):
                pass


__all__ = ["force_utf8"]
