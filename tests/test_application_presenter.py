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
from keithley_6517_contracts import AppIntent, IntentKind  # noqa: E402
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
        self.profile = None
        self.hv_enabled = False
        self.identity = ""
        self._reading = 0
        self.last_commands: List[str] = []

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

    def identify(self) -> str:
        return self.identity

    def query(self, command: str) -> str:
        if command.upper().startswith(":SYSTEM:VERSION"):
            return "1991.0"
        return "0"

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
        self.assertEqual(len(self.application.state.readings), 5)
        self.assertIn("status", output.read_text(encoding="utf-8").splitlines()[0])

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
