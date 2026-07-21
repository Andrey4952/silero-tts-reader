#!/usr/bin/env python3
"""Launcher — auto-activates .venv if present, then starts the application."""
import sys
import os
import warnings
from pathlib import Path


def _activate_venv() -> None:
    """If a .venv exists next to this script, prepend its site-packages to sys.path."""
    script_dir = Path(__file__).parent.resolve()
    venv_dir = script_dir / ".venv"

    if not venv_dir.exists():
        return

    if sys.prefix == str(venv_dir):
        return

    lib_dir = venv_dir / "lib"
    if not lib_dir.exists():
        return

    site_packages = sorted(lib_dir.glob("python*/site-packages"))
    if not site_packages:
        return

    for sp in site_packages:
        sp_str = str(sp)
        if sp_str not in sys.path:
            sys.path.insert(0, sp_str)

    os.environ.setdefault("VIRTUAL_ENV", str(venv_dir))


def _suppress_noise() -> None:
    """Suppress known harmless warnings."""
    # pynput Xlib warning when XAUTHORITY is not set
    os.environ.setdefault("PYNPUT_BACKEND", "xorg")
    # Point to default Xauthority location if not set
    xauth = os.path.expanduser("~/.Xauthority")
    if os.path.exists(xauth):
        os.environ.setdefault("XAUTHORITY", xauth)

    import logging
    logging.getLogger("Xlib").setLevel(logging.ERROR)

    # Redirect stderr temporarily to swallow Xlib xauth warnings
    # that come from native C code (can't be caught by Python logging)
    import io

    class _XlibFilter(io.TextIOWrapper):
        def write(self, s: str) -> int:
            if "Xlib.xauth" in s or "xauthority" in s.lower():
                return len(s)
            return super().write(s)

    # Only wrap if stderr is a real TTY (don't break pipes/redirects)
    if hasattr(sys.stderr, "buffer"):
        try:
            sys.stderr = _XlibFilter(sys.stderr.buffer, line_buffering=True)
        except Exception:
            pass


_activate_venv()
_suppress_noise()

sys.path.insert(0, str(Path(__file__).parent.resolve()))

from silero_tts_reader.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
