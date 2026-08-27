from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path

from src.keithley_6517_contracts import InstrumentSnapshot, ViewState
from src.keithley_6517_ui import (
    _build_automatic_acquisition_filename,
    _format_reading_value,
    _reading_is_plottable,
)


PROJECT_DIR = Path(__file__).resolve().parents[1]
UI_SOURCE = PROJECT_DIR / "src" / "keithley_6517_ui.py"


class BrandingTests(unittest.TestCase):
    def test_automatic_csv_name_contains_mode_function_date_and_time(self) -> None:
        state = ViewState(
            instrument_snapshot=InstrumentSnapshot(
                function="VOLTage:DC",
                auto_range=True,
                nplc=1.0,
                digits=6,
                zero_check=False,
                zero_correct=True,
                rel_enabled=False,
                average_enabled=True,
                average_type="SCALar",
                average_mode="MOVing",
                average_count=10,
                median_enabled=False,
            ),
            hv_active=True,
            hv_voltage=1000.0,
        )

        filename = _build_automatic_acquisition_filename(
            state, "LIVE", datetime(2026, 8, 25, 10, 25, 27)
        )

        self.assertEqual(
            filename,
            "live_tensao_dc_20260825_102527.csv",
        )

    def test_acquisition_timeout_is_exposed_and_dispatched_by_ui(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn('labels = ("Modo", "Tempo de leitura", "Intervalo", "Timeout")', source)
        self.assertIn('self._build_time_control(controls, "timeout", "60", 3)', source)
        self.assertIn('values=list(TIME_UNIT_FACTORS)', source)
        self.assertIn("def _acquisition_unit_changed", source)
        self.assertIn("def _acquisition_value_in_seconds", source)
        self.assertIn('timeout=self._acquisition_value_in_seconds("timeout")', source)

    def test_zero_check_blocks_acquisition_with_operator_warning(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn("self.current_state.instrument_snapshot.zero_check is True", source)
        self.assertIn('messagebox.showwarning(', source)
        self.assertIn('"Zero Check ligado"', source)
        self.assertIn("Desligue o Zero Check", source)

    def test_acquisition_completion_does_not_open_export_question(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertNotIn("messagebox.askyesnocancel", source)
        application_source = (PROJECT_DIR / "src" / "keithley_6517_application.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("export_csv_to_xlsx", application_source)
        self.assertIn("CSV e XLSX salvos", application_source)

    def test_measurement_page_excludes_removed_summary_cards(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        for removed_title in (
            "LEITURA ATUAL",
            "CONFIGURAÇÕES COMUNS",
            "CONFIGURAÇÃO CONFIRMADA",
        ):
            self.assertNotIn(removed_title, source)
        self.assertIn("self._build_measurement_controls(page, start_row=1)", source)

    def test_connection_defaults_to_gpib_address_27(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn('values=["GPIB0::27::INSTR"]', source)
        self.assertIn('self.resource_combo.set("GPIB0::27::INSTR")', source)
        self.assertIn('preferred_resource = "GPIB0::27::INSTR"', source)

    def test_measurement_page_is_a_read_only_monitor(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn('"PARÂMETROS DO INSTRUMENTO"', source)
        self.assertIn('rowspan=3', source)
        self.assertIn('sticky="e"', source)
        self.assertNotIn('text="Ler instrumento agora"', source)
        self.assertNotIn('text="Liberar painel"', source)
        self.assertNotIn('text="Painel manual"', source)
        self.assertIn('text="Aplicar parâmetros"', source)
        self.assertIn("self.advanced_function_option = ctk.CTkOptionMenu", source)
        self.assertIn("self.advanced_nplc_entry = ctk.CTkEntry", source)
        self.assertIn("self.reset_parameters_button = ctk.CTkButton", source)
        self.assertIn("def _reset_parameters(self)", source)
        self.assertIn("IntentKind.RESET_INSTRUMENT", source)
        self.assertIn('"NPLC (0.01 a 10)"', source)
        self.assertNotIn('"NPLC (0,01 a 10)"', source)
        self.assertNotIn("Autorange", source)
        self.assertNotIn("advanced_auto_range_switch", source)
        self.assertNotIn("advanced_auto_range_var", source)
        self.assertNotIn("average_mode_value", source)
        self.assertNotIn("average_count_value", source)
        self.assertNotIn("median_rank_value", source)
        self.assertNotIn('text="Quantidade de leituras"', source)
        self.assertNotIn('text="Rank (1 a 5)"', source)
        self.assertNotIn(' · faixa {2}', source)
        self.assertIn("monitoramento", source)
        self.assertIn("MONITOR_MS = 2500", source)
        for removed_indicator in (
            "Faixa configurada",
            "Faixa efetiva",
            "Pontos",
            "Repetição",
            "Atraso fonte → medição (s)",
            "Dígitos (4 a 7)",
        ):
            self.assertNotIn(removed_indicator, source)
        self.assertNotIn("advanced_resolution", source)
        self.assertNotIn("advanced_accuracy", source)
        self.assertNotIn('text="Aplicar alterações"', source)
        self.assertNotIn('text="Adquirir Zero Correct"', source)
        self.assertNotIn('text="Adquirir REL"', source)
        self.assertNotIn("changes = self._card(page, start_row + 4)", source)

    def test_invalid_instrument_sentinel_is_not_presented_as_measurement(self) -> None:
        text = _format_reading_value(
            9.91e37,
            "INVALID",
            "V",
            zero_check=True,
        )

        self.assertEqual(text, "Inválida — Zero Check ligado")
        self.assertNotIn("E+37", text)
        self.assertFalse(_reading_is_plottable("INVALID"))
        self.assertTrue(_reading_is_plottable("OK"))
        self.assertTrue(_reading_is_plottable("COMPLIANCE"))

    def test_periodic_render_does_not_reconfigure_unchanged_widget_states(self) -> None:
        source = UI_SOURCE.read_text(encoding="utf-8")

        self.assertIn("def _set_widget_state(widget: Any, desired_state: str)", source)
        self.assertIn('if str(widget.cget("state")) != desired_state:', source)
        self.assertNotIn("refresh_instrument_button", source)
        self.assertNotIn("release_front_panel_button", source)
        self.assertIn("self.apply_measurement_button", source)

    def test_monitor_does_not_lock_operator_controls(self) -> None:
        source = (PROJECT_DIR / "src" / "keithley_6517_application.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("self._monitor_in_flight = True", source)
        self.assertNotIn(
            'busy_message="Atualizando monitor sem escrever no instrumento…"',
            source,
        )

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
        self.assertIn('dialog.title("Sobre")', source)
        self.assertNotIn('dialog.title("Sobre — Keithley 6517 Control Studio")', source)
        self.assertIn("webbrowser.open_new_tab(RAD_WEBSITE)", source)
        self.assertIn('image=self._branding_images["wordmark"]', source)
        self.assertIn('image=self._branding_images["qr"]', source)
        self.assertNotIn('text="SITE DA RAD"', source)
        self.assertIn("width=320", source)
        self.assertIn("height=164", source)
        self.assertIn('brand_card.grid(row=0, column=0, sticky="nsew", padx=6, pady=8)', source)
        self.assertIn('qr_card.grid(row=0, column=1, sticky="ne", padx=6, pady=8)', source)
        self.assertIn("qr_card.grid(row=0, column=1, sticky=\"ne\"", source)
        self.assertIn('.place(relx=1.0, x=-16, rely=0.5, anchor="e")', source)
        self.assertIn('brand_grid.grid_columnconfigure(0, weight=4', source)
        self.assertNotIn("Aponte a câmera do celular", source)
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
