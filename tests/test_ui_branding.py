from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
UI_SOURCE = PROJECT_DIR / "src" / "keithley_6517_ui.py"


class BrandingTests(unittest.TestCase):
    def test_rad_website_qr_assets_are_present(self) -> None:
        branding_dir = PROJECT_DIR / "assets" / "branding"
        png = branding_dir / "radinstruments_qr.png"
        svg = branding_dir / "radinstruments_qr.svg"

        self.assertTrue(png.is_file())
        self.assertTrue(svg.is_file())
        self.assertGreater(png.stat().st_size, 1000)
        self.assertGreater(svg.stat().st_size, 1000)

    def test_rad_branding_assets_and_about_navigation_are_present(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn('RAD_WEBSITE = "https://radinstruments.com.br/"', source)
        self.assertIn("command=self._open_about", source)
        self.assertIn("webbrowser.open_new_tab(RAD_WEBSITE)", source)
        self.assertIn('image=self._branding_images["wordmark"]', source)
        self.assertIn('image=self._branding_images["qr"]', source)
        self.assertTrue(
            (PROJECT_DIR / "assets" / "branding" / "radinstruments_250x37.png").is_file()
        )
        self.assertTrue(
            (PROJECT_DIR / "assets" / "icons" / "codicons" / "svg" / "info.svg").is_file()
        )

        for variant in ("light", "light_active", "dark", "dark_active"):
            self.assertTrue(
                (
                    PROJECT_DIR
                    / "assets"
                    / "icons"
                    / "codicons"
                    / "png"
                    / variant
                    / "info.png"
                ).is_file()
            )

    def test_interface_does_not_use_osl_meter_branding(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8").lower()
        branding_names = {
            path.name.lower()
            for path in (PROJECT_DIR / "assets" / "branding").iterdir()
            if path.is_file()
        }

        self.assertNotIn("osl meter", source)
        self.assertNotIn("oslmeter", source)
        self.assertFalse(any("osl" in name for name in branding_names))


if __name__ == "__main__":
    unittest.main()
