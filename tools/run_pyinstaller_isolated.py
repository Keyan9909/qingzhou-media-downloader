"""Run PyInstaller without probing the current Windows user's site-packages."""

from __future__ import annotations

import runpy
import site


# PyInstaller 6.10 probes this directory during binary dependency analysis even
# when Python is started with -s. Commercial builds only use the active Python
# environment, so the per-user package directory is deliberately excluded.
site.ENABLE_USER_SITE = False
site.getusersitepackages = lambda: ""  # type: ignore[assignment]

runpy.run_module("PyInstaller", run_name="__main__")
