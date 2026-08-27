"""Filesystem services for logs, preferences and acquisition CSV files.

Importing this module has no side effects. Directories and handlers are created
only by explicit calls from the composition root or application coordinator.
"""

from __future__ import annotations

import csv
import json
import logging
import math
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO
from xml.etree import ElementTree


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    data: Path
    logs: Path
    preferences: Path

    @classmethod
    def from_root(cls, root: Path) -> "ProjectPaths":
        resolved = Path(root).resolve()
        return cls(
            root=resolved,
            data=resolved / "data",
            logs=resolved / "log",
            preferences=resolved / "config" / "preferences.json",
        )

    def ensure_runtime_directories(self) -> None:
        self.data.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        self.preferences.parent.mkdir(parents=True, exist_ok=True)


def configure_logging(paths: ProjectPaths, level: int = logging.INFO) -> Path:
    paths.ensure_runtime_directories()
    log_path = paths.logs / "keithley_ui_{0}.log".format(
        datetime.now().strftime("%Y%m%d")
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    resolved_target = str(log_path.resolve())
    if not any(
        isinstance(handler, logging.FileHandler)
        and str(Path(handler.baseFilename).resolve()) == resolved_target
        for handler in root_logger.handlers
    ):
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(threadName)s | %(message)s"
            )
        )
        root_logger.addHandler(handler)
    protocol_dir = paths.logs
    protocol_dir.mkdir(parents=True, exist_ok=True)
    protocol_logger = logging.getLogger("keithley.protocol")
    protocol_logger.setLevel(logging.INFO)
    protocol_logger.propagate = False
    protocol_path = protocol_dir / "protocolo_{0}.log".format(
        datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    resolved_protocol = str(protocol_path.resolve())
    if not any(
        isinstance(handler, logging.FileHandler)
        and str(Path(handler.baseFilename).resolve()) == resolved_protocol
        for handler in protocol_logger.handlers
    ):
        protocol_handler = logging.FileHandler(protocol_path, encoding="utf-8")
        protocol_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(threadName)s | %(message)s"
            )
        )
        protocol_logger.addHandler(protocol_handler)
    protocol_logger.info("=== Início do protocolo de comunicação ===")
    return log_path


def acquisition_day_folder(paths: ProjectPaths, when: Optional[datetime] = None) -> Path:
    """Return the date-organized folder used for acquisition CSVs."""

    paths.ensure_runtime_directories()
    stamp = when or datetime.now()
    folder = paths.data / stamp.strftime("%Y") / stamp.strftime("%m") / stamp.strftime("%d")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def acquisition_path_for_name(
    paths: ProjectPaths,
    filename: str,
    mode: str = "LIVE",
    when: Optional[datetime] = None,
) -> Path:
    """Build a dated CSV path from an operator-editable filename."""

    stamp = when or datetime.now()
    clean_name = str(filename).strip()
    if not clean_name:
        clean_name = "{0}_{1}.csv".format(
            str(mode).strip().lower() or "acquisition",
            stamp.strftime("%Y%m%d_%H%M%S"),
        )
    if any(separator in clean_name for separator in ("/", "\\")):
        raise ValueError("Informe somente o nome do arquivo, sem pastas.")
    if clean_name in (".", "..") or any(char in clean_name for char in '<>:"|?*'):
        raise ValueError("O nome do arquivo contém caracteres inválidos.")
    if not clean_name.lower().endswith(".csv"):
        clean_name += ".csv"
    return acquisition_day_folder(paths, stamp) / clean_name


def default_acquisition_path(paths: ProjectPaths, mode: str = "LIVE") -> Path:
    """Return a unique, date-organized default path for an acquisition."""

    return acquisition_path_for_name(paths, "", mode=mode)


def next_available_acquisition_path(path: Path) -> Path:
    """Avoid collisions for automatically generated acquisition names."""

    candidate = Path(path)
    if not candidate.exists():
        return candidate
    for sequence in range(1, 10000):
        candidate = path.with_name(
            "{0}_{1:02d}{2}".format(path.stem, sequence, path.suffix)
        )
        if not candidate.exists():
            return candidate
    raise FileExistsError("Não foi possível gerar um nome livre para o CSV: {0}".format(path))


class CsvAcquisitionWriter:
    HEADER = (
        "#",
        "Tempo (s)",
        "Valor",
        "Un.",
    )

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._handle: Optional[TextIO] = None
        self._writer: Optional[Any] = None

    def __enter__(self) -> "CsvAcquisitionWriter":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Never overwrite an existing acquisition selected by the operator.
        # Automatic paths are timestamped; explicit collisions must be visible.
        self._handle = self.path.open("x", newline="", encoding="utf-8")
        self._writer = csv.writer(self._handle)
        self._writer.writerow(self.HEADER)
        self._handle.flush()
        return self

    def write(
        self,
        sample: int,
        instrument_time_s: float,
        value: float,
        raw_value: str,
        unit: str,
        status: str,
        model: str,
        serial: str,
        firmware: str,
    ) -> None:
        if self._writer is None or self._handle is None:
            raise RuntimeError("O arquivo CSV não está aberto.")
        self._writer.writerow(
            (
                sample,
                "{0:.9g}".format(instrument_time_s),
                "{0:.12g}".format(value),
                unit,
            )
        )
        self._handle.flush()

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        if self._handle is not None:
            self._handle.close()
        self._handle = None
        self._writer = None


_XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _xlsx_column_name(index: int) -> str:
    name = ""
    value = index
    while value:
        value, remainder = divmod(value - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _xlsx_cell(row: int, column: int, value: Any) -> ElementTree.Element:
    cell = ElementTree.Element(
        "{{{0}}}c".format(_XLSX_NS),
        {"r": "{0}{1}".format(_xlsx_column_name(column), row)},
    )
    number: Optional[float] = None
    if column in (1, 2, 3):
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = None
        if number is not None and not math.isfinite(number):
            number = None
    if number is not None:
        cell.set("t", "n")
        ElementTree.SubElement(cell, "{{{0}}}v".format(_XLSX_NS)).text = (
            str(int(number)) if number.is_integer() else "{0:.15g}".format(number)
        )
    else:
        cell.set("t", "inlineStr")
        inline = ElementTree.SubElement(cell, "{{{0}}}is".format(_XLSX_NS))
        ElementTree.SubElement(inline, "{{{0}}}t".format(_XLSX_NS)).text = str(value or "")
    return cell


def export_csv_to_xlsx(csv_path: Path, xlsx_path: Path) -> None:
    """Convert the four-column acquisition CSV into a simple XLSX workbook."""

    source = Path(csv_path)
    destination = Path(xlsx_path)
    with source.open("r", newline="", encoding="utf-8") as handle:
        rows: List[List[str]] = list(csv.reader(handle))
    if not rows:
        raise ValueError("O arquivo CSV da aquisição está vazio.")

    ElementTree.register_namespace("", _XLSX_NS)
    ElementTree.register_namespace("r", _REL_NS)
    worksheet = ElementTree.Element("{{{0}}}worksheet".format(_XLSX_NS))
    sheet_data = ElementTree.SubElement(
        worksheet, "{{{0}}}sheetData".format(_XLSX_NS)
    )
    for row_number, values in enumerate(rows, start=1):
        row = ElementTree.SubElement(
            sheet_data,
            "{{{0}}}row".format(_XLSX_NS),
            {"r": str(row_number)},
        )
        for column_number, value in enumerate(values, start=1):
            row.append(_xlsx_cell(row_number, column_number, value))

    workbook = ElementTree.Element("{{{0}}}workbook".format(_XLSX_NS))
    sheets = ElementTree.SubElement(workbook, "{{{0}}}sheets".format(_XLSX_NS))
    ElementTree.SubElement(
        sheets,
        "{{{0}}}sheet".format(_XLSX_NS),
        {
            "name": "Medições",
            "sheetId": "1",
            "{{{0}}}id".format(_REL_NS): "rId1",
        },
    )

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""
    package_relationships = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    workbook_relationships = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""

    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", package_relationships)
        archive.writestr(
            "xl/workbook.xml",
            ElementTree.tostring(workbook, encoding="utf-8", xml_declaration=True),
        )
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_relationships)
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            ElementTree.tostring(worksheet, encoding="utf-8", xml_declaration=True),
        )


def load_preferences(paths: ProjectPaths) -> Dict[str, Any]:
    try:
        with paths.preferences.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_preferences(paths: ProjectPaths, preferences: Dict[str, Any]) -> None:
    paths.ensure_runtime_directories()
    safe = {
        "theme": preferences.get("theme", "Dark"),
        "sidebar_expanded": bool(preferences.get("sidebar_expanded", True)),
    }
    with paths.preferences.open("w", encoding="utf-8") as handle:
        json.dump(safe, handle, ensure_ascii=False, indent=2)


__all__ = [
    "CsvAcquisitionWriter",
    "export_csv_to_xlsx",
    "ProjectPaths",
    "configure_logging",
    "acquisition_day_folder",
    "acquisition_path_for_name",
    "default_acquisition_path",
    "next_available_acquisition_path",
    "load_preferences",
    "save_preferences",
]
