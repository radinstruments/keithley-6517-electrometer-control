"""Composition root for the functional CustomTkinter application."""

from __future__ import annotations

import logging
from pathlib import Path

try:
    from .keithley_6517_application import Keithley6517Application
    from .keithley_6517_storage import ProjectPaths, configure_logging
    from .keithley_6517_ui import Keithley6517UI
except ImportError:  # pragma: no cover - supports ``python src/main.py``
    from keithley_6517_application import Keithley6517Application
    from keithley_6517_storage import ProjectPaths, configure_logging
    from keithley_6517_ui import Keithley6517UI


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    paths = ProjectPaths.from_root(project_root)
    log_path = configure_logging(paths)
    logging.info("Iniciando Keithley 6517 Control Studio; log=%s", log_path)
    application = Keithley6517Application(project_root)
    window = Keithley6517UI(application)
    try:
        window.run()
    finally:
        application.finalize()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

