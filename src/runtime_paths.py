"""Runtime paths for source runs and frozen Windows executables."""

from __future__ import annotations

import sys
from pathlib import Path


PROGRAM_DATA_FOLDER = "Keithley6517ControlStudio"


def resource_root() -> Path:
    """Return the directory containing bundled read-only assets."""

    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[1]


def project_root() -> Path:
    """Return the writable directory used for data, logs and preferences."""

    if getattr(sys, "frozen", False):
        # Keep the one-file executable and its writable files separate.  The
        # executable may be placed anywhere, while acquisitions, logs and
        # preferences have a stable user-owned location.
        return Path.home() / "Documents" / PROGRAM_DATA_FOLDER
    return resource_root()
