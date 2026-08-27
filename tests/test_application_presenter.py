from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple


PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from keithley_6517_application import Keithley6517Application  # noqa: E402
from keithley_6517_acquisition import (  # noqa: E402
    AcquisitionMetadata,
    AcquisitionRequest,
    AcquisitionRunner,
)
from keithley_6517_contracts import (  # noqa: E402
    AppIntent,
    InstrumentSnapshot,
    IntentKind,
)
from keithley_6517_driver import (  # noqa: E402
    ControllerState,
    MeasurementReading,
    PROFILE_6517A,
    ReadingStatus,
    VoltageSourceStatus,
)


class FakeFunctionalController:
    def __init__(self) -> None:
        self.state = ControllerState.DISCONNECTED
        self.connected = False
        self.front_panel_local = False
        self.profile = None
        self.hv_enabled = False
        self.identity = ""
        self._reading = 0
        self.last_commands: List[str] = []
        self.snapshot_revision = 0
        self.measurement_function = "CURRent:DC"
        self.nplc = 1.0
        self.range_value = 2.0e-9
        self.auto_range = True
        self.digits = 6
        self.zero_check = False

    def list_resources(self) -> Tuple[str, ...]:
        return ("GPIB0::27::INSTR",)

    def connect(
        self,
        resource: str,
        timeout_ms: int = 5000,
        expected_model: Optional[str] = None,
    ) -> str:
        del resource, timeout_ms
        if expected_model not in (None, "6517A"):
            raise RuntimeError("modelo divergente")
        self.connected = True
        self.profile = PROFILE_6517A
        self.state = ControllerState.SAFE
        self.identity = "KEITHLEY INSTRUMENTS INC., MODEL 6517A, 1234, A01"
        return self.identity

    def disconnect(self) -> None:
        self.connected = False
        self.profile = None
        self.hv_enabled = False
        self.state = ControllerState.DISCONNECTED

    def shutdown(self) -> None:
        self.disconnect()

    def release_front_panel(self) -> bool:
        self.front_panel_local = True
        self.last_commands.append("GTL")
        return True

    def reset(self) -> None:
        self.last_commands.append("*RST")
        self.measurement_function = "CURRent:DC"
        self.nplc = 1.0
        self.range_value = 2.0e-9
        self.auto_range = True
        self.digits = 6
        self.zero_check = True
        self.hv_enabled = False
        self.state = ControllerState.SAFE

    def identify(self) -> str:
        return self.identity

    def query(self, command: str) -> str:
        if command.upper().startswith(":SYSTEM:VERSION"):
            return "1991.0"
        return "0"

    def read_instrument_snapshot(self) -> InstrumentSnapshot:
        self.snapshot_revision += 1
        if self.connected and self.state == ControllerState.SAFE:
            self.state = ControllerState.CONFIGURED
        return InstrumentSnapshot(
            revision=self.snapshot_revision,
            captured_at=time.time(),
            model="6517A",
            resource_name="GPIB0::27::INSTR",
            function=self.measurement_function,
            auto_range=self.auto_range,
            range_value=self.range_value,
            nplc=self.nplc,
            digits=self.digits,
            aperture_s=self.nplc / 60.0,
            zero_check=self.zero_check,
            zero_correct=False,
            rel_enabled=False,
            rel_value=0.0,
            average_enabled=False,
            average_type="SCALar",
            average_mode="MOVing",
            average_count=10,
            advanced_noise_tolerance=1.0,
            median_enabled=True,
            median_rank=1,
            source_sweep_points=1.0,
            repeat_count=0.0,
            source_measure_delay_s=0.0,
            hv_output_enabled=self.hv_enabled,
        )

    def apply_advanced_changes(self, changes: Any) -> InstrumentSnapshot:
        if "function" in changes:
            self.measurement_function = str(changes["function"])
        if "nplc" in changes:
            self.nplc = float(changes["nplc"])
        if "auto_range" in changes:
            self.auto_range = bool(changes["auto_range"])
        if "range_value" in changes:
            self.range_value = float(changes["range_value"])
        if "digits" in changes:
            self.digits = int(changes["digits"])
        self.state = ControllerState.CONFIGURED
        return self.read_instrument_snapshot()

    def acquire_zero_correct(self) -> InstrumentSnapshot:
        return self.read_instrument_snapshot()

    def acquire_rel(self) -> InstrumentSnapshot:
        return self.read_instrument_snapshot()

    def disable_rel(self) -> InstrumentSnapshot:
        return self.read_instrument_snapshot()

    def configure_measurement(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.state = ControllerState.CONFIGURED

    def set_format_elements(self, elements: str = "") -> None:
        del elements

    def one_shot_read(self) -> MeasurementReading:
        return self._next_reading()

    def start_live(self) -> None:
        self.state = ControllerState.ACQUIRING

    def read_live(self) -> MeasurementReading:
        return self._next_reading()

    def abort(self) -> None:
        self.state = ControllerState.HV_ENABLED if self.hv_enabled else ControllerState.CONFIGURED

    def prepare_buffer(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.state = ControllerState.ARMED

    def start_buffer(self) -> None:
        self.state = ControllerState.ACQUIRING

    def wait_buffer_complete(self, timeout: float, stop_event: Any = None) -> int:
        del timeout, stop_event
        return 1

    def read_buffer_readings(self) -> List[MeasurementReading]:
        self.state = ControllerState.CONFIGURED
        return [self._next_reading()]

    def configure_voltage_source(self, voltage: float, limit: float) -> VoltageSourceStatus:
        return VoltageSourceStatus(voltage, 100.0 if abs(voltage) <= 100 else 1000.0, limit, True, False, True, False, "1")

    def enable_voltage_source(self, physical_interlock_confirmed: bool = False) -> VoltageSourceStatus:
        if not physical_interlock_confirmed:
            raise RuntimeError("confirmação ausente")
        self.hv_enabled = True
        self.state = ControllerState.HV_ENABLED
        return VoltageSourceStatus(25.0, 100.0, 50.0, True, True, True, False, "1")

    def disable_voltage_source(self) -> VoltageSourceStatus:
        self.hv_enabled = False
        self.state = ControllerState.CONFIGURED
        return VoltageSourceStatus(0.0, 100.0, 50.0, True, False, True, False, "1")

    def get_voltage_source_status(self) -> VoltageSourceStatus:
        return VoltageSourceStatus(0.0, 100.0, 50.0, True, self.hv_enabled, True, False, "1")

    def execute_scpi_batch(
        self,
        commands: Sequence[str],
        check_errors: bool = True,
        allow_hv: bool = False,
        physical_interlock_confirmed: bool = False,
    ) -> List[Tuple[str, Optional[str], List[str]]]:
        del check_errors, allow_hv, physical_interlock_confirmed
        self.last_commands.extend(commands)
        return [(commands[0], self.identity if commands[0] == "*IDN?" else None, [])]

    def _next_reading(self) -> MeasurementReading:
        self._reading += 1
        value = self._reading * 1.0e-12
        return MeasurementReading(
            raw_value="{0:.6E}".format(value),
            raw_timestamp=str(self._reading / 10.0),
            value=value,
            timestamp=self._reading / 10.0,
            instrument_status="N",
            status=ReadingStatus.OK,
        )


class ApplicationPresenterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.controller = FakeFunctionalController()
        self.application = Keithley6517Application(
            Path(self.temporary.name), controller=self.controller
        )

    def tearDown(self) -> None:
        self.controller.shutdown()
        self.application.finalize()
        self.temporary.cleanup()

    def wait_for(self, predicate: Any, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("tempo esgotado aguardando estado da aplicação")

    def test_connection_measurement_and_one_shot_are_functional(self) -> None:
        self.application.dispatch(AppIntent(IntentKind.SET_EXPECTED_MODEL, {"model": "6517A"}))
        self.application.dispatch(AppIntent(IntentKind.CONNECT, {"resource": "GPIB0::27::INSTR"}))
        self.wait_for(lambda: self.application.state.connected and not self.application.state.busy)
        self.assertEqual(self.application.state.detected_model, "6517A")
        self.assertEqual(self.application.state.serial_number, "1234")

        self.application.dispatch(
            AppIntent(
                IntentKind.CONFIGURE_MEASUREMENT,
                {
                    "function": "CURRent:DC",
                    "auto_range": True,
                    "nplc": "1.0",
                    "digits": "6",
                },
            )
        )
        self.wait_for(lambda: self.application.state.measurement_configured and not self.application.state.busy)
        self.application.dispatch(AppIntent(IntentKind.ONE_SHOT))
        self.wait_for(lambda: self.application.state.reading_value is not None and not self.application.state.busy)
        self.assertEqual(self.application.state.reading_status, "OK")

    def test_live_acquisition_writes_csv_and_updates_view(self) -> None:
        self.application.dispatch(AppIntent(IntentKind.CONNECT, {"resource": "GPIB0::27::INSTR"}))
        self.wait_for(lambda: self.application.state.connected and not self.application.state.busy)
        self.application.dispatch(
            AppIntent(IntentKind.CONFIGURE_MEASUREMENT, {"function": "CURRent:DC", "auto_range": True, "nplc": 1, "digits": 6})
        )
        self.wait_for(lambda: self.application.state.measurement_configured and not self.application.state.busy)
        output = Path(self.temporary.name) / "acquisition.csv"
        self.application.dispatch(
            AppIntent(IntentKind.START_ACQUISITION, {"mode": "LIVE", "points": 5, "interval": 0.1, "timeout": 5, "path": str(output)})
        )
        self.wait_for(lambda: not self.application.state.acquisition_running and self.application.state.acquisition_count == 5)
        self.assertTrue(output.exists())
        self.assertTrue(output.with_suffix(".xlsx").exists())
        self.assertEqual(len(self.application.state.readings), 5)
        self.assertFalse(self.application.state.panel_manual_mode)
        self.assertEqual(
            output.read_text(encoding="utf-8").splitlines()[0],
            "#,Tempo (s),Valor,Un.",
        )

    def test_manual_panel_mode_pauses_monitor_and_resumes_from_panel_state(self) -> None:
        self.application.dispatch(
            AppIntent(IntentKind.CONNECT, {"resource": "GPIB0::27::INSTR"})
        )
        self.wait_for(lambda: self.application.state.connected and not self.application.state.busy)

        self.application.dispatch(AppIntent(IntentKind.RELEASE_FRONT_PANEL))
        self.wait_for(
            lambda: self.application.state.panel_manual_mode
            and not self.application.state.busy
        )
        self.assertTrue(self.controller.front_panel_local)

        self.controller.measurement_function = "VOLTage:DC"
        self.application.dispatch(AppIntent(IntentKind.MONITOR_INSTRUMENT))
        time.sleep(0.1)
        self.assertTrue(self.application.state.panel_manual_mode)
        self.assertEqual(self.application.state.measurement_function, "CURRent:DC")

        self.application.dispatch(AppIntent(IntentKind.RESUME_MONITOR))
        self.wait_for(
            lambda: not self.application.state.panel_manual_mode
            and not self.application.state.busy
            and self.application.state.measurement_function == "VOLTage:DC"
        )

    def test_monitor_adopts_manual_measurement_changes_automatically(self) -> None:
        self.application.dispatch(
            AppIntent(IntentKind.CONNECT, {"resource": "GPIB0::27::INSTR"})
        )
        self.wait_for(lambda: self.application.state.connected and not self.application.state.busy)
        self.assertEqual(self.application.state.measurement_function, "CURRent:DC")

        self.controller.measurement_function = "VOLTage:DC"
        self.controller.nplc = 0.1
        self.application.dispatch(AppIntent(IntentKind.MONITOR_INSTRUMENT))
        self.wait_for(
            lambda: not self.application.state.busy
            and self.application.state.measurement_function == "VOLTage:DC"
            and self.application.state.instrument_snapshot.nplc == 0.1
        )

        self.assertFalse(self.application.state.panel_manual_mode)
        draft_values = dict(self.application.state.draft_values)
        self.assertEqual(draft_values["function"], "VOLTage:DC")
        self.assertEqual(draft_values["nplc"], "0.1")

    def test_reset_instrument_restores_defaults_and_refreshes_snapshot(self) -> None:
        self.application.dispatch(
            AppIntent(IntentKind.CONNECT, {"resource": "GPIB0::27::INSTR"})
        )
        self.wait_for(lambda: self.application.state.connected and not self.application.state.busy)
        self.application.dispatch(AppIntent(IntentKind.RESET_INSTRUMENT))
        self.wait_for(lambda: not self.application.state.busy)

        self.assertIn("*RST", self.controller.last_commands)
        self.assertEqual(self.application.state.instrument_snapshot.nplc, 1.0)
        self.assertTrue(self.application.state.instrument_snapshot.zero_check)
        self.assertFalse(self.application.state.dirty_fields)

    def test_acquisition_is_blocked_when_zero_check_is_on(self) -> None:
        self.application.dispatch(AppIntent(IntentKind.CONNECT, {"resource": "GPIB0::27::INSTR"}))
        self.wait_for(lambda: self.application.state.connected and not self.application.state.busy)
        self.controller.zero_check = True
        output = Path(self.temporary.name) / "zero-check.csv"
        self.application.dispatch(
            AppIntent(
                IntentKind.START_ACQUISITION,
                {"mode": "LIVE", "points": 2, "interval": 0.1, "timeout": 5, "path": str(output)},
            )
        )
        self.wait_for(lambda: not self.application.state.acquisition_running and not self.application.state.busy)

        self.assertIn("Zero Check ligado", self.application.state.error_banner)
        self.assertEqual(self.application.state.acquisition_count, 0)
        self.assertFalse(output.exists())

    def test_live_acquisition_is_allowed_with_hv_active(self) -> None:
        self.application.dispatch(AppIntent(IntentKind.CONNECT, {"resource": "GPIB0::27::INSTR"}))
        self.wait_for(lambda: self.application.state.connected and not self.application.state.busy)
        self.application.dispatch(
            AppIntent(
                IntentKind.CONFIGURE_MEASUREMENT,
                {"function": "VOLTage:DC", "auto_range": True, "nplc": 1, "digits": 6},
            )
        )
        self.wait_for(lambda: self.application.state.measurement_configured and not self.application.state.busy)

        self.controller.hv_enabled = True
        self.controller.state = ControllerState.HV_ENABLED
        output = Path(self.temporary.name) / "high-voltage-acquisition.csv"
        self.application.dispatch(
            AppIntent(
                IntentKind.START_ACQUISITION,
                {"mode": "LIVE", "points": 2, "interval": 0.1, "timeout": 5, "path": str(output)},
            )
        )
        self.wait_for(
            lambda: not self.application.state.acquisition_running
            and self.application.state.acquisition_count == 2
        )

        self.assertTrue(output.exists())
        self.assertTrue(self.controller.hv_enabled)
        self.assertTrue(self.application.state.hv_active)

    def test_duration_and_interval_calculate_sample_count(self) -> None:
        self.application.dispatch(AppIntent(IntentKind.CONNECT, {"resource": "GPIB0::27::INSTR"}))
        self.wait_for(lambda: self.application.state.connected and not self.application.state.busy)
        self.application.dispatch(
            AppIntent(
                IntentKind.CONFIGURE_MEASUREMENT,
                {"function": "CURRent:DC", "auto_range": True, "nplc": 1, "digits": 6},
            )
        )
        self.wait_for(lambda: self.application.state.measurement_configured and not self.application.state.busy)
        output = Path(self.temporary.name) / "duration.csv"
        self.application.dispatch(
            AppIntent(
                IntentKind.START_ACQUISITION,
                {
                    "mode": "LIVE",
                    "duration": 0.3,
                    "interval": 0.1,
                    "timeout": 5,
                    "path": str(output),
                },
            )
        )
        self.wait_for(lambda: not self.application.state.acquisition_running)
        self.assertEqual(self.application.state.acquisition_count, 3)

    def test_acquisition_adopts_latest_front_panel_setup(self) -> None:
        self.application.dispatch(
            AppIntent(IntentKind.CONNECT, {"resource": "GPIB0::27::INSTR"})
        )
        self.wait_for(lambda: self.application.state.connected and not self.application.state.busy)
        self.application.dispatch(
            AppIntent(
                IntentKind.EDIT_ADVANCED_DRAFT,
                {"field": "nplc", "value": "2"},
            )
        )
        self.controller.measurement_function = "VOLTage:DC"
        self.controller.nplc = 10.0
        self.controller.auto_range = False
        self.controller.range_value = 20.0
        output = Path(self.temporary.name) / "panel-authority.csv"

        self.application.dispatch(
            AppIntent(
                IntentKind.START_ACQUISITION,
                {
                    "mode": "LIVE",
                    "points": 2,
                    "interval": 0.1,
                    "timeout": 5,
                    "path": str(output),
                },
            )
        )
        self.wait_for(
            lambda: not self.application.state.acquisition_running
            and self.application.state.acquisition_count == 2
        )

        self.assertEqual(self.application.state.measurement_function, "VOLTage:DC")
        self.assertEqual(self.application.state.instrument_snapshot.nplc, 10.0)
        self.assertEqual(self.application.state.reading_unit, "V")
        self.assertFalse(self.application.state.dirty_fields)
        self.assertIn(",V", output.read_text(encoding="utf-8"))

    def test_acquisition_time_is_relative_to_each_run(self) -> None:
        self.controller.connect("GPIB0::27::INSTR")
        self.controller.configure_measurement("CURRent:DC", True, 1, 6)
        output = Path(self.temporary.name) / "relative-time.csv"
        received: List[MeasurementReading] = []
        AcquisitionRunner(self.controller).run(
            AcquisitionRequest("LIVE", output, points=3, timer_interval_s=0),
            AcquisitionMetadata("6517A", "1234", "C05", "A"),
            lambda _index, reading: received.append(reading),
        )
        self.assertEqual(len(received), 3)
        self.assertAlmostEqual(received[0].timestamp, 0.0)
        self.assertAlmostEqual(received[1].timestamp, 0.1)
        self.assertAlmostEqual(received[2].timestamp, 0.2)

    def test_scpi_console_executes_only_preflighted_command(self) -> None:
        self.application.dispatch(AppIntent(IntentKind.CONNECT, {"resource": "GPIB0::27::INSTR"}))
        self.wait_for(lambda: self.application.state.connected and not self.application.state.busy)
        self.application.dispatch(AppIntent(IntentKind.PREVIEW_SCPI, {"command": "*IDN?"}))
        self.assertTrue(self.application.state.scpi_preview.valid)
        self.application.dispatch(AppIntent(IntentKind.EXECUTE_SCPI, {"command": "*IDN?"}))
        self.wait_for(lambda: bool(self.application.state.scpi_output) and not self.application.state.busy)
        self.assertEqual(self.controller.last_commands, ["*IDN?"])

    def test_external_change_preserves_local_draft_and_creates_conflict(self) -> None:
        self.application.dispatch(
            AppIntent(IntentKind.CONNECT, {"resource": "GPIB0::27::INSTR"})
        )
        self.wait_for(
            lambda: self.application.state.connected
            and self.application.state.instrument_snapshot.revision > 0
            and not self.application.state.busy
        )
        self.application.dispatch(
            AppIntent(
                IntentKind.EDIT_ADVANCED_DRAFT,
                {"field": "nplc", "value": "2"},
            )
        )
        self.controller.nplc = 10.0
        self.application.dispatch(AppIntent(IntentKind.REFRESH_INSTRUMENT))
        self.wait_for(
            lambda: self.application.state.instrument_snapshot.nplc == 10.0
            and not self.application.state.busy
        )
        self.assertIn("nplc", self.application.state.conflict_fields)
        self.assertEqual(dict(self.application.state.draft_values)["nplc"], "2")
        self.application.dispatch(AppIntent(IntentKind.ADOPT_INSTRUMENT_VALUES))
        self.wait_for(lambda: not self.application.state.busy)
        self.assertNotIn("nplc", self.application.state.conflict_fields)
        self.assertEqual(dict(self.application.state.draft_values)["nplc"], "10")

    def test_autorange_effective_range_change_is_not_a_conflict(self) -> None:
        self.application.dispatch(
            AppIntent(IntentKind.CONNECT, {"resource": "GPIB0::27::INSTR"})
        )
        self.wait_for(
            lambda: self.application.state.connected
            and self.application.state.instrument_snapshot.revision > 0
            and not self.application.state.busy
        )
        self.application.dispatch(
            AppIntent(
                IntentKind.EDIT_ADVANCED_DRAFT,
                {"field": "range_value", "value": "1e-9"},
            )
        )
        self.controller.range_value = 20e-9
        self.application.dispatch(AppIntent(IntentKind.REFRESH_INSTRUMENT))
        self.wait_for(
            lambda: self.application.state.instrument_snapshot.range_value == 20e-9
            and not self.application.state.busy
        )
        self.assertNotIn("range_value", self.application.state.conflict_fields)

    def test_50000_samples_keep_bounded_visual_state_and_queue(self) -> None:
        reading = MeasurementReading(
            raw_value="1.0E-12",
            raw_timestamp="0.1",
            value=1.0e-12,
            timestamp=0.1,
            instrument_status="N",
            status=ReadingStatus.OK,
        )
        for index in range(1, 50001):
            self.application._on_acquisition_reading(index, reading)
        self.assertEqual(len(self.application._visual_readings), 2000)
        self.assertLessEqual(self.application._states.qsize(), 64)


if __name__ == "__main__":
    unittest.main()
