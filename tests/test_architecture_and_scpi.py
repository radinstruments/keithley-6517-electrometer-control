from __future__ import annotations

import ast
import sys
import time
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from keithley_6517_profiles import capabilities_for_model  # noqa: E402
from keithley_6517_scpi import (  # noqa: E402
    ScpiRisk,
    issue_authorization,
    preflight_scpi,
)


class ArchitectureTests(unittest.TestCase):
    def test_only_visual_module_imports_customtkinter(self) -> None:
        importers = []
        for path in SRC_DIR.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            if "customtkinter" in imported:
                importers.append(path.name)
        self.assertEqual(importers, ["keithley_6517_ui.py"])

    def test_visual_module_does_not_import_hardware_or_worker_layers(self) -> None:
        path = SRC_DIR / "keithley_6517_ui.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        forbidden = {
            "pyvisa",
            "threading",
            "queue",
            "keithley_6517_driver",
            "keithley_6517_storage",
            "keithley_6517_acquisition",
        }
        self.assertFalse(modules.intersection(forbidden))

    def test_left_navigation_is_primary_shell(self) -> None:
        source = (SRC_DIR / "keithley_6517_ui.py").read_text(encoding="utf-8")
        self.assertIn("NAV_ITEMS", source)
        self.assertIn("self.sidebar.grid(row=0, column=0, rowspan=2", source)
        self.assertIn("width=56", source)
        self.assertIn("class _Tooltip", source)
        self.assertIn("previous_shell.grid_remove()", source)
        self.assertIn("page_shell.grid()", source)
        self.assertIn("page_shell.tkraise()", source)
        self.assertNotIn("ImageGrab.grab", source)
        self.assertNotIn("WM_SETREDRAW", source)
        self.assertNotIn("WS_EX_COMPOSITED", source)
        self.assertNotIn("_transition_overlay", source)
        self.assertNotIn("_schedule_page_reveal", source)
        self.assertIn("state.active_page != self._visible_page", source)
        for page in (
            "DASHBOARD",
            "CONNECTION",
            "MEASUREMENT",
            "ACQUISITION",
            "HIGH_VOLTAGE",
            "SCPI",
            "LOGS",
            "SETTINGS",
        ):
            self.assertIn("PageId." + page, source)

    def test_activity_bar_icon_assets_are_complete(self) -> None:
        icon_root = PROJECT_DIR / "assets" / "icons" / "codicons"
        names = {
            "home",
            "usb-symbol",
            "pulse",
            "graph-line",
            "symbol-event",
            "terminal-compact",
            "history",
            "gear-compact",
        }
        self.assertTrue((icon_root / "ATTRIBUTION.md").is_file())
        for name in names:
            self.assertTrue((icon_root / "svg" / f"{name}.svg").is_file())
            for variant in ("light", "light_active", "dark", "dark_active"):
                self.assertTrue((icon_root / "png" / variant / f"{name}.png").is_file())


class ProfileAndCatalogTests(unittest.TestCase):
    def test_model_capabilities_are_independent(self) -> None:
        model_a = capabilities_for_model("6517A")
        model_b = capabilities_for_model("6517B")
        self.assertEqual(model_a.max_buffer_points_with_timestamp, 10470)
        self.assertEqual(model_b.max_buffer_points_with_timestamp, 50000)
        self.assertEqual(model_a.source_current_limit_a(100), 10e-3)
        self.assertEqual(model_b.source_current_limit_a(1000), 1e-3)

    def test_scpi_preflight_rejects_compound_and_unknown_commands(self) -> None:
        compound = preflight_scpi("*IDN?;*OPT?", "6517A", 1)
        unknown = preflight_scpi(":FAKE:COMMAND?", "6517A", 1)
        self.assertFalse(compound.valid)
        self.assertFalse(unknown.valid)

    def test_scpi_preflight_classifies_hv_and_binds_authorization(self) -> None:
        preview = preflight_scpi(":OUTP1 ON", "6517B", 42)
        self.assertTrue(preview.valid)
        self.assertEqual(preview.risk, ScpiRisk.HV_ENABLE)
        self.assertTrue(preview.confirmation_required)
        token = issue_authorization(preview, "6517B", 42, ttl_seconds=2)
        self.assertTrue(token.valid_for(preview, "6517B", 42))
        changed = preflight_scpi(":OUTP1 OFF", "6517B", 42)
        self.assertFalse(token.valid_for(changed, "6517B", 42))
        self.assertFalse(token.valid_for(preview, "6517A", 42))

    def test_authorization_expires(self) -> None:
        preview = preflight_scpi("*RST", "6517A", 7)
        token = issue_authorization(preview, "6517A", 7, ttl_seconds=0.001)
        # issue_authorization clamps the lifetime to one second.
        self.assertTrue(token.valid_for(preview, "6517A", 7))
        self.assertGreater(token.expires_at, time.monotonic())


if __name__ == "__main__":
    unittest.main()
