from __future__ import annotations

import math
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.keithley_6517_file_browser import (
    list_acquisition_files,
    read_acquisition_file,
)
from src.keithley_6517_storage import CsvAcquisitionWriter, export_csv_to_xlsx


class FileBrowserTests(unittest.TestCase):
    def test_lists_dated_csv_and_xlsx_and_reads_their_points(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dated_folder = root / "2026" / "09" / "24"
            csv_path = dated_folder / "live_tensao_dc_20260924_090820.csv"
            with CsvAcquisitionWriter(csv_path) as writer:
                writer.write(1, 0.0, 3.091, "3.091", "V", "OK", "6517B", "", "")
                writer.write(2, 0.1, 3.092, "3.092", "V", "OK", "6517B", "", "")
            xlsx_path = csv_path.with_suffix(".xlsx")
            export_csv_to_xlsx(csv_path, xlsx_path)

            all_files = list_acquisition_files(root, date(2026, 9, 24))
            self.assertEqual({item.path for item in all_files}, {csv_path, xlsx_path})
            self.assertEqual(
                [item.path for item in list_acquisition_files(root, date(2026, 9, 24), "CSV")],
                [csv_path],
            )
            self.assertEqual(list_acquisition_files(root, date(2026, 9, 23)), ())

            for path in (csv_path, xlsx_path):
                points = read_acquisition_file(path)
                self.assertEqual(len(points), 2)
                self.assertEqual((points[0].index, points[0].timestamp, points[0].value, points[0].unit), (1, 0.0, 3.091, "V"))
                self.assertEqual((points[1].index, points[1].timestamp, points[1].value, points[1].unit), (2, 0.1, 3.092, "V"))

    def test_preserves_invalid_measurement_without_plotting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sample.csv"
            path.write_text("#,Tempo (s),Valor,Un.\n1,0,nan,V\n", encoding="utf-8")
            point = read_acquisition_file(path)[0]
            self.assertTrue(math.isnan(point.value))
            self.assertEqual(point.status, "INVALID")
            self.assertEqual(point.raw_value, "nan")

    def test_rejects_unrelated_spreadsheet_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "other.csv"
            path.write_text("a,b,c,d\n1,2,3,4\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "colunas"):
                read_acquisition_file(path)


if __name__ == "__main__":
    unittest.main()
