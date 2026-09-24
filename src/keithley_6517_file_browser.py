"""Read saved acquisition files for the independent file browser page."""

from __future__ import annotations

import csv
import math
import posixpath
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Tuple
from xml.etree import ElementTree


SUPPORTED_SUFFIXES = frozenset((".csv", ".xlsx"))
EXPECTED_HEADER = ("#", "Tempo (s)", "Valor", "Un.")
_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DOCUMENT_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


@dataclass(frozen=True)
class AcquisitionFile:
    path: Path
    day: date
    modified_at: datetime
    size_bytes: int

    @property
    def kind(self) -> str:
        return self.path.suffix.lower().lstrip(".").upper()


@dataclass(frozen=True)
class AcquisitionPoint:
    index: int
    timestamp: float
    value: float
    raw_value: str
    unit: str
    status: str


def _acquisition_day(path: Path, modified_at: datetime) -> date:
    """Use the application's YYYY/MM/DD folders, falling back to file time."""

    try:
        return date(
            int(path.parent.parent.parent.name),
            int(path.parent.parent.name),
            int(path.parent.name),
        )
    except (ValueError, OSError):
        match = re.search(r"(?<!\d)(\d{8})_\d{6}(?!\d)", path.stem)
        if match is not None:
            try:
                return datetime.strptime(match.group(1), "%Y%m%d").date()
            except ValueError:
                pass
        return modified_at.date()


def list_acquisition_files(
    root: Path,
    day: Optional[date] = None,
    kind: str = "Todos",
) -> Tuple[AcquisitionFile, ...]:
    """List CSV/XLSX files below a chosen folder, newest first."""

    directory = Path(root).expanduser()
    if not directory.is_dir():
        raise ValueError("A pasta selecionada não existe: {0}".format(directory))
    requested_kind = kind.strip().upper()
    if requested_kind not in ("TODOS", "CSV", "XLSX"):
        raise ValueError("Tipo de arquivo inválido: {0}".format(kind))
    files: List[AcquisitionFile] = []
    for path in directory.rglob("*"):
        if (
            not path.is_file()
            or path.name.startswith("~$")
            or path.suffix.lower() not in SUPPORTED_SUFFIXES
        ):
            continue
        if requested_kind != "TODOS" and path.suffix[1:].upper() != requested_kind:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        modified_at = datetime.fromtimestamp(stat.st_mtime)
        file_day = _acquisition_day(path, modified_at)
        if day is not None and file_day != day:
            continue
        files.append(AcquisitionFile(path.resolve(), file_day, modified_at, stat.st_size))
    files.sort(key=lambda item: (item.modified_at, str(item.path)), reverse=True)
    return tuple(files)


def _csv_rows(path: Path) -> Iterator[List[str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        yield from csv.reader(handle)


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> Tuple[str, ...]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return ()
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return tuple(
        "".join(node.itertext())
        for node in root.findall("{{{0}}}si".format(_MAIN_NS))
    )


def _xlsx_sheet_name(archive: zipfile.ZipFile) -> str:
    """Find the first worksheet via workbook relationships."""

    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    sheet = workbook.find(".//{{{0}}}sheet".format(_MAIN_NS))
    if sheet is None:
        raise ValueError("A planilha XLSX não contém abas.")
    relationship_id = sheet.get("{{{0}}}id".format(_DOCUMENT_REL_NS))
    relationships = ElementTree.fromstring(
        archive.read("xl/_rels/workbook.xml.rels")
    )
    for relationship in relationships.findall(
        "{{{0}}}Relationship".format(_PACKAGE_REL_NS)
    ):
        if relationship.get("Id") == relationship_id:
            target = relationship.get("Target", "")
            if target.startswith("/"):
                return target.lstrip("/")
            return posixpath.normpath(posixpath.join("xl", target))
    raise ValueError("A primeira aba do XLSX não foi encontrada.")


def _xlsx_cell_text(cell: ElementTree.Element, shared_strings: Sequence[str]) -> str:
    cell_type = cell.get("t", "")
    if cell_type == "inlineStr":
        return "".join(
            element.text or ""
            for element in cell.findall(".//{{{0}}}t".format(_MAIN_NS))
        )
    value = cell.find("{{{0}}}v".format(_MAIN_NS))
    text = "" if value is None or value.text is None else value.text
    if cell_type == "s":
        try:
            return shared_strings[int(text)]
        except (IndexError, ValueError):
            raise ValueError("Índice de texto inválido no XLSX.")
    return text


def _xlsx_rows(path: Path) -> Iterator[List[str]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _xlsx_shared_strings(archive)
        sheet_name = _xlsx_sheet_name(archive)
        with archive.open(sheet_name) as handle:
            for _event, row in ElementTree.iterparse(handle, events=("end",)):
                if row.tag != "{{{0}}}row".format(_MAIN_NS):
                    continue
                values = ["", "", "", ""]
                for cell in row.findall("{{{0}}}c".format(_MAIN_NS)):
                    match = re.match(r"([A-Z]+)", cell.get("r", ""))
                    if match is None:
                        continue
                    column = 0
                    for character in match.group(1):
                        column = column * 26 + ord(character) - ord("A") + 1
                    if 1 <= column <= 4:
                        values[column - 1] = _xlsx_cell_text(cell, shared_strings)
                yield values
                row.clear()


def read_acquisition_file(path: Path) -> Tuple[AcquisitionPoint, ...]:
    """Read the four-column format exported by this application."""

    source = Path(path)
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError("Selecione um arquivo CSV ou XLSX.")
    rows = _csv_rows(source) if suffix == ".csv" else _xlsx_rows(source)
    iterator = iter(rows)
    try:
        header = next(iterator)
        if tuple(value.strip() for value in header[:4]) != EXPECTED_HEADER:
            raise ValueError("O arquivo não possui as colunas #, Tempo (s), Valor e Un.")
        points: List[AcquisitionPoint] = []
        for row_number, row in enumerate(iterator, start=2):
            if not row or all(not str(value).strip() for value in row[:4]):
                continue
            if len(row) < 4:
                raise ValueError("Linha {0} incompleta.".format(row_number))
            try:
                index = int(float(row[0]))
                timestamp = float(str(row[1]).replace(",", "."))
                value = float(str(row[2]).replace(",", "."))
            except ValueError:
                raise ValueError("Linha {0} contém número inválido.".format(row_number))
            if not math.isfinite(timestamp):
                raise ValueError("Linha {0} contém tempo inválido.".format(row_number))
            points.append(
                AcquisitionPoint(
                    index=index,
                    timestamp=timestamp,
                    value=value,
                    raw_value=str(row[2]),
                    unit=str(row[3]),
                    status="OK" if math.isfinite(value) else "INVALID",
                )
            )
        return tuple(points)
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            close()
