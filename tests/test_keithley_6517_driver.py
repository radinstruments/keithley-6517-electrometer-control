from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from keithley_6517_driver import (  # noqa: E402
    AcquisitionTimeout,
    ControllerState,
    InstrumentCommandError,
    KeithleyController,
    ModelMismatchError,
    PROFILE_6517A,
    PROFILE_6517B,
    ReadingStatus,
    UnsupportedInstrumentError,
    UnsafeCommandError,
    analyze_scpi_safety,
    classify_reading,
    detect_instrument_profile,
    parse_buffer_response,
    parse_reading_response,
)
from keithley_6517_storage import (  # noqa: E402
    CsvAcquisitionWriter,
    export_csv_to_xlsx,
)


class FakeInterface:
    def __init__(self, log: List[Tuple[str, str, int]]) -> None:
        self.log = log

    def send_ifc(self) -> None:
        self.log.append(("ifc", "send_ifc", threading.get_ident()))

    def close(self) -> None:
        self.log.append(("ifc", "close", threading.get_ident()))


class FakeInstrument:
    def __init__(
        self,
        identity: str = "KEITHLEY INSTRUMENTS INC., MODEL 6517A, 1234, A01",
    ) -> None:
        self.identity = identity
        self.timeout = 5000
        self.write_termination = ""
        self.read_termination = ""
        self.log: List[Tuple[str, str, int]] = []
        self.error_queue: List[str] = []
        self.fresh_responses = [
            "+1.000000E-12,0.100000,N",
            "+2.000000E-12,0.200000,N",
        ]
        self.read_response = "+3.000000E-12,0.300000,N"
        self.buffer_response = (
            "+1.000000E-12,0.000000,N,+9.910000E+37,0.100000,O"
        )
        self.buffer_points = 0
        self.buffer_actual = 0
        self.auto_complete_buffer = True
        self.compliance = False
        self.interlock_ok = True
        self.source_voltage = 0.0
        self.source_range = 100.0
        self.source_limit = 1000.0
        self.source_limit_enabled = False
        self.source_output_enabled = False
        self.resistance_vsource_mode = "AUTO"
        self.resistance_manual_operate = False
        self.resistance_manual_amplitude = 100.0
        self.resistance_manual_range = 100.0
        self.measurement_function = "CURR"
        self.auto_range = True
        self.measurement_range = 2.0e-9
        self.nplc = 1.0
        self.digits = 6
        self.aperture = 1.0 / 60.0
        self.zero_check = True
        self.zero_correct = False
        self.rel_enabled = False
        self.rel_value = 0.0
        self.average_enabled = False
        self.average_type = "SCAL"
        self.average_mode = "MOV"
        self.average_count = 10
        self.noise_tolerance = 1.0
        self.median_enabled = True
        self.median_rank = 1
        self.line_sync = False
        self.trigger_count = 1
        self.arm_count = 1
        self.arm_source = "IMM"
        self.arm2_count = 1
        self.arm2_source = "IMM"
        self.trigger_source = "IMM"
        self.trigger_timer = 0.1
        self.trigger_delay = 0.0
        self.continuous_initiation = True
        self.query_overrides: Dict[str, Any] = {}
        self.query_delay = 0.0
        self.closed = False
        self.front_panel_local = False

    def front_panel_set(self, **changes: Any) -> None:
        for name, value in changes.items():
            if not hasattr(self, name):
                raise AttributeError(name)
            setattr(self, name, value)

    def write(self, command: str) -> None:
        self.log.append(("write", command, threading.get_ident()))
        upper = command.upper()
        allowed_roots = (
            "*CLS",
            "*RST",
            ":OUTP",
            ":OUTPUT1",
            ":SOURCE:",
            ":INITIATE",
            ":ABORT",
            ":TRACE:",
            ":SYSTEM:",
            ":SENSE:",
            ":FORMAT:",
            ":ARM:",
            ":TRIGGER:",
        )
        if not upper.startswith(allowed_roots):
            raise AssertionError("Fake estrito rejeitou write desconhecido: " + command)
        if upper.startswith(":TRACE:POINTS "):
            self.buffer_points = int(float(command.rsplit(" ", 1)[1]))
        if upper == ":OUTPUT1 ON":
            self.source_output_enabled = True
        elif upper == ":OUTPUT1 OFF":
            self.source_output_enabled = False
        elif upper.startswith(":SOURCE:VOLTAGE:RANGE "):
            self.source_range = float(command.rsplit(" ", 1)[1])
        elif upper.startswith(":SOURCE:VOLTAGE:LIMIT:STATE "):
            self.source_limit_enabled = command.rsplit(" ", 1)[1].upper() in (
                "ON",
                "1",
            )
        elif upper.startswith(":SOURCE:VOLTAGE:LIMIT "):
            self.source_limit = float(command.rsplit(" ", 1)[1])
        elif upper.startswith(":SOURCE:VOLTAGE "):
            self.source_voltage = float(command.rsplit(" ", 1)[1])
        elif upper.startswith(":SENSE:RESISTANCE:VSC "):
            self.resistance_vsource_mode = command.rsplit(" ", 1)[1].upper()
        elif upper.startswith(":SENSE:FUNCTION "):
            self.measurement_function = command.rsplit(" ", 1)[1].strip("'\"").upper()
        elif ":RANGE:AUTO " in upper:
            self.auto_range = command.rsplit(" ", 1)[1].upper() in ("ON", "1")
        elif ":RANGE:UPPER " in upper:
            self.measurement_range = float(command.rsplit(" ", 1)[1])
        elif ":NPLCYCLES " in upper:
            self.nplc = float(command.rsplit(" ", 1)[1])
            self.aperture = self.nplc / 60.0
        elif ":DIGITS " in upper:
            self.digits = int(float(command.rsplit(" ", 1)[1]))
        elif upper.startswith(":SYSTEM:ZCHECK "):
            self.zero_check = command.rsplit(" ", 1)[1].upper() in ("ON", "1")
        elif upper.startswith(":SYSTEM:ZCORRECT:STATE "):
            self.zero_correct = command.rsplit(" ", 1)[1].upper() in ("ON", "1")
        elif ":REFERENCE:STATE " in upper:
            self.rel_enabled = command.rsplit(" ", 1)[1].upper() in ("ON", "1")
        elif ":REFERENCE " in upper:
            self.rel_value = float(command.rsplit(" ", 1)[1])
        elif ":AVERAGE:TYPE " in upper:
            self.average_type = command.rsplit(" ", 1)[1]
        elif ":AVERAGE:TCONTROL " in upper:
            self.average_mode = command.rsplit(" ", 1)[1]
        elif ":AVERAGE:COUNT " in upper:
            self.average_count = int(float(command.rsplit(" ", 1)[1]))
        elif ":AVERAGE:ADVANCED:NTOLERANCE " in upper:
            self.noise_tolerance = float(command.rsplit(" ", 1)[1])
        elif ":AVERAGE:STATE " in upper:
            self.average_enabled = command.rsplit(" ", 1)[1].upper() in ("ON", "1")
        elif ":MEDIAN:RANK " in upper:
            self.median_rank = int(float(command.rsplit(" ", 1)[1]))
        elif ":MEDIAN:STATE " in upper:
            self.median_enabled = command.rsplit(" ", 1)[1].upper() in ("ON", "1")
        elif upper.startswith(":TRIGGER:COUNT "):
            self.trigger_count = int(float(command.rsplit(" ", 1)[1]))
        elif upper.startswith(":ARM:LAYER1:COUNT "):
            self.arm_count = int(float(command.rsplit(" ", 1)[1]))
        elif upper.startswith(":ARM:LAYER1:SOURCE "):
            self.arm_source = command.rsplit(" ", 1)[1].upper()
        elif upper.startswith(":ARM:LAYER2:COUNT "):
            self.arm2_count = int(float(command.rsplit(" ", 1)[1]))
        elif upper.startswith(":ARM:LAYER2:SOURCE "):
            self.arm2_source = command.rsplit(" ", 1)[1].upper()
        elif upper.startswith(":TRIGGER:SOURCE "):
            self.trigger_source = command.rsplit(" ", 1)[1].upper()
        elif upper.startswith(":TRIGGER:TIMER "):
            self.trigger_timer = float(command.rsplit(" ", 1)[1])
        elif upper.startswith(":TRIGGER:DELAY "):
            self.trigger_delay = float(command.rsplit(" ", 1)[1])
        elif upper.startswith(":INITIATE:CONTINUOUS "):
            self.continuous_initiation = command.rsplit(" ", 1)[1].upper() in (
                "ON",
                "1",
            )
        elif upper.startswith(":SENSE:RESISTANCE:MANUAL:VSOURCE:OPERATE "):
            self.resistance_manual_operate = command.rsplit(" ", 1)[1].upper() in (
                "ON",
                "1",
            )
        elif upper.startswith(":SENSE:RESISTANCE:MANUAL:VSOURCE:AMPLITUDE "):
            self.resistance_manual_amplitude = float(command.rsplit(" ", 1)[1])
        elif upper.startswith(":SENSE:RESISTANCE:MANUAL:VSOURCE:RANGE "):
            self.resistance_manual_range = float(command.rsplit(" ", 1)[1])
        if upper == ":INITIATE" and self.auto_complete_buffer:
            self.buffer_actual = self.buffer_points

    def query(self, command: str) -> str:
        self.log.append(("query", command, threading.get_ident()))
        if self.query_delay:
            time.sleep(self.query_delay)
        upper = command.upper()
        if upper in self.query_overrides:
            override = self.query_overrides[upper]
            if isinstance(override, BaseException):
                raise override
            return str(override)
        if upper == "*IDN?":
            return self.identity
        if upper == "*OPT?":
            return "0"
        if upper == "*OPC?":
            return "1"
        if upper == ":SYSTEM:VERSION?":
            return "1996.0" if "6517B" in self.identity else "1991.0"
        if upper == ":SYSTEM:ERROR?":
            return self.error_queue.pop(0) if self.error_queue else '0,"No error"'
        if upper == ":SENSE:DATA:FRESH?":
            if self.fresh_responses:
                return self.fresh_responses.pop(0)
            return "+4.000000E-12,0.400000,N"
        if upper == ":SENSE:FUNCTION?":
            return '"' + self.measurement_function + '"'
        if upper.startswith(":SENSE:") and ":MANUAL:" not in upper and upper.endswith(":RANGE:AUTO?"):
            return "1" if self.auto_range else "0"
        if upper.startswith(":SENSE:") and ":MANUAL:" not in upper and upper.endswith(":RANGE?"):
            return str(self.measurement_range)
        if upper.startswith(":SENSE:") and upper.endswith(":NPLCYCLES?"):
            return str(self.nplc)
        if upper.startswith(":SENSE:") and upper.endswith(":DIGITS?"):
            return str(self.digits)
        if upper.startswith(":SENSE:") and upper.endswith(":APERTURE?"):
            return str(self.aperture)
        if upper == ":SYSTEM:ZCHECK?":
            return "1" if self.zero_check else "0"
        if upper == ":SYSTEM:ZCORRECT?":
            return "1" if self.zero_correct else "0"
        if upper.endswith(":REFERENCE:STATE?"):
            return "1" if self.rel_enabled else "0"
        if upper.endswith(":REFERENCE?"):
            return str(self.rel_value)
        if upper.endswith(":AVERAGE:STATE?"):
            return "1" if self.average_enabled else "0"
        if upper.endswith(":AVERAGE:TYPE?"):
            return self.average_type
        if upper.endswith(":AVERAGE:TCONTROL?"):
            return self.average_mode
        if upper.endswith(":AVERAGE:COUNT?"):
            return str(self.average_count)
        if upper.endswith(":AVERAGE:ADVANCED:NTOLERANCE?"):
            return str(self.noise_tolerance)
        if upper.endswith(":MEDIAN:STATE?"):
            return "1" if self.median_enabled else "0"
        if upper.endswith(":MEDIAN:RANK?"):
            return str(self.median_rank)
        if upper == ":SYSTEM:LSYNC:STATE?":
            return "1" if self.line_sync else "0"
        if upper == ":TRIGGER:COUNT?":
            return str(self.trigger_count)
        if upper == ":ARM:LAYER1:COUNT?":
            return str(self.arm_count)
        if upper == ":ARM:LAYER1:SOURCE?":
            return self.arm_source
        if upper == ":ARM:LAYER2:COUNT?":
            return str(self.arm2_count)
        if upper == ":ARM:LAYER2:SOURCE?":
            return self.arm2_source
        if upper == ":TRIGGER:SOURCE?":
            return self.trigger_source
        if upper == ":TRIGGER:TIMER?":
            return str(self.trigger_timer)
        if upper == ":TRIGGER:DELAY?":
            return str(self.trigger_delay)
        if upper == ":INITIATE:CONTINUOUS?":
            return "1" if self.continuous_initiation else "0"
        if upper == ":READ?":
            return self.read_response
        if upper == ":TRACE:POINTS:ACTUAL?":
            return str(self.buffer_actual)
        if upper == ":TRACE:DATA?":
            return self.buffer_response
        if upper == ":FORMAT:ELEMENTS?":
            return "READ,TST,STAT"
        if upper == ":TRACE:ELEMENTS?":
            return "TST"
        if upper == ":SENSE:RESISTANCE:VSC?":
            return self.resistance_vsource_mode
        if upper == ":SENSE:RESISTANCE:MANUAL:VSOURCE:OPERATE?":
            return "1" if self.resistance_manual_operate else "0"
        if upper == ":SENSE:RESISTANCE:MANUAL:VSOURCE:AMPLITUDE?":
            return str(self.resistance_manual_amplitude)
        if upper == ":SENSE:RESISTANCE:MANUAL:VSOURCE:RANGE?":
            return str(self.resistance_manual_range)
        if upper == ":SOURCE:CURRENT:LIMIT:STATE?":
            return "1" if self.compliance else "0"
        if upper == ":SYSTEM:INTERLOCK?":
            return "1" if self.interlock_ok else "0"
        if upper == ":SOURCE:VOLTAGE?":
            return str(self.source_voltage)
        if upper == ":SOURCE:VOLTAGE:RANGE?":
            return str(self.source_range)
        if upper == ":SOURCE:VOLTAGE:LIMIT?":
            return str(self.source_limit)
        if upper == ":SOURCE:VOLTAGE:LIMIT:STATE?":
            return "1" if self.source_limit_enabled else "0"
        if upper == ":OUTPUT1:STATE?":
            return "1" if self.source_output_enabled else "0"
        raise AssertionError("Fake estrito rejeitou query desconhecida: " + command)

    def close(self) -> None:
        self.log.append(("close", "resource", threading.get_ident()))
        self.closed = True

    def control_ren(self, mode: Any) -> int:
        self.log.append(("ren", str(int(mode)), threading.get_ident()))
        self.front_panel_local = True
        return 0


class FakeResourceManager:
    def __init__(self, instrument: FakeInstrument) -> None:
        self.instrument = instrument
        self.log: List[Tuple[str, str, int]] = []
        self.closed = False

    def list_resources(self) -> Tuple[str, ...]:
        self.log.append(("manager", "list", threading.get_ident()))
        return ("GPIB0::27::INSTR",)

    def open_resource(self, name: str) -> Any:
        self.log.append(("manager", "open:" + name, threading.get_ident()))
        if name.endswith("::INTFC"):
            return FakeInterface(self.log)
        return self.instrument

    def close(self) -> None:
        self.log.append(("manager", "close", threading.get_ident()))
        self.closed = True


class ControllerFixture(unittest.TestCase):
    identity = "KEITHLEY INSTRUMENTS INC., MODEL 6517A, 1234, A01"

    def make_controller(
        self, identity: Optional[str] = None
    ) -> Tuple[KeithleyController, FakeInstrument, FakeResourceManager]:
        instrument = FakeInstrument(identity or self.identity)
        manager = FakeResourceManager(instrument)
        controller = KeithleyController(lambda: manager)
        self.addCleanup(controller.shutdown)
        return controller, instrument, manager

    @staticmethod
    def configure_voltage(controller: KeithleyController) -> None:
        controller.configure_measurement(
            function="VOLTage:DC",
            range_value=None,
            auto_range=True,
            nplc=1.0,
            digits=6,
        )
        controller.set_format_elements("READing,TSTamp")

    def test_detects_independent_a_and_b_profiles(self) -> None:
        self.assertIs(
            detect_instrument_profile(
                "KEITHLEY INSTRUMENTS INC., MODEL 6517A, 1, A"
            ),
            PROFILE_6517A,
        )
        self.assertIs(
            detect_instrument_profile(
                "KEITHLEY INSTRUMENTS INC., MODEL 6517B, 1, B"
            ),
            PROFILE_6517B,
        )

    def test_controller_selects_b_profile_and_its_buffer_limit(self) -> None:
        controller, _instrument, _manager = self.make_controller(
            "KEITHLEY INSTRUMENTS INC., MODEL 6517B, 5678, B01"
        )
        controller.connect("GPIB0::27::INSTR")
        self.assertIs(controller.profile, PROFILE_6517B)
        self.configure_voltage(controller)
        controller.prepare_buffer(20000, source="TIMer", timer_interval=0.1)
        self.assertEqual(controller.state, ControllerState.ARMED)

    def test_unknown_model_is_rejected_and_closed(self) -> None:
        controller, instrument, _manager = self.make_controller(
            "ACME, MODEL 1234, 1, 1"
        )
        with self.assertRaises(UnsupportedInstrumentError):
            controller.connect("GPIB0::27::INSTR")
        self.assertTrue(instrument.closed)
        self.assertEqual(controller.state, ControllerState.DISCONNECTED)

    def test_expected_model_mismatch_is_rejected_before_configuration(self) -> None:
        controller, instrument, _manager = self.make_controller()
        with self.assertRaises(ModelMismatchError):
            controller.connect("GPIB0::27::INSTR", expected_model="6517B")
        self.assertTrue(instrument.closed)
        writes = [command for kind, command, _tid in instrument.log if kind == "write"]
        self.assertEqual(writes, [])

    def test_connect_observer_is_write_free_and_uses_worker_only(self) -> None:
        controller, instrument, manager = self.make_controller()
        identity = controller.connect("GPIB0::27::INSTR")
        self.assertIn("6517A", identity)
        self.assertEqual(controller.state, ControllerState.SAFE)
        owner = controller._worker.owner_thread_ident
        access_threads = {
            item[2]
            for item in instrument.log + manager.log
            if item[0] in ("write", "query", "close", "manager", "ifc")
        }
        self.assertEqual(access_threads, {owner})
        self.assertFalse(any(item[0] == "ifc" for item in manager.log))
        self.assertEqual(
            [command for kind, command, _tid in instrument.log if kind == "write"],
            [],
        )
        self.assertTrue(instrument.front_panel_local)

    def test_disconnect_sends_go_to_local_before_closing_gpib(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        instrument.front_panel_local = False
        instrument.log.clear()

        controller.disconnect()

        operations = [(kind, command) for kind, command, _tid in instrument.log]
        ren_index = next(
            index for index, item in enumerate(operations) if item[0] == "ren"
        )
        close_index = operations.index(("close", "resource"))
        self.assertLess(ren_index, close_index)
        self.assertTrue(instrument.front_panel_local)
        self.assertFalse(any(kind == "write" for kind, _command in operations))

    def test_snapshot_releases_front_panel_without_scpi_write(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        instrument.front_panel_local = False
        instrument.log.clear()

        controller.read_instrument_snapshot()

        self.assertTrue(instrument.front_panel_local)
        self.assertTrue(any(kind == "ren" for kind, _command, _tid in instrument.log))
        self.assertFalse(
            any(kind == "write" for kind, _command, _tid in instrument.log)
        )

    def test_explicit_front_panel_release_uses_gtl_without_scpi_write(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        instrument.front_panel_local = False
        instrument.log.clear()

        self.assertTrue(controller.release_front_panel())
        self.assertTrue(instrument.front_panel_local)
        self.assertTrue(any(kind == "ren" for kind, _command, _tid in instrument.log))
        self.assertFalse(
            any(kind == "write" for kind, _command, _tid in instrument.log)
        )

    def test_read_only_snapshot_follows_front_panel_changes(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        first = controller.read_instrument_snapshot()
        self.assertEqual(controller.state, ControllerState.CONFIGURED)
        self.assertEqual(first.nplc, 1.0)
        instrument.front_panel_set(
            nplc=2.0,
            aperture=2.0 / 60.0,
            average_enabled=True,
            average_count=25,
        )
        instrument.log.clear()
        second = controller.read_instrument_snapshot()
        self.assertEqual(second.nplc, 2.0)
        self.assertTrue(second.average_enabled)
        self.assertEqual(second.average_count, 25)
        self.assertFalse(any(kind == "write" for kind, _command, _tid in instrument.log))

    def test_observer_snapshot_enables_acquisition_without_configuration_write(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        instrument.log.clear()

        snapshot = controller.read_instrument_snapshot()

        self.assertEqual(snapshot.function, "CURRent:DC")
        self.assertEqual(controller.state, ControllerState.CONFIGURED)
        self.assertEqual(controller._configured_function, "CURRent:DC")
        self.assertEqual(controller._configured_nplc, 1.0)
        self.assertFalse(
            any(kind == "write" for kind, _command, _tid in instrument.log)
        )

        controller.start_live()
        self.assertEqual(controller.state, ControllerState.ACQUIRING)
        controller.abort()

    def test_snapshot_keeps_valid_fields_when_one_response_is_invalid(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        instrument.query_overrides[":SENSE:CURRENT:DC:NPLCYCLES?"] = "inválido"
        snapshot = controller.read_instrument_snapshot()
        self.assertIsNone(snapshot.nplc)
        self.assertEqual(snapshot.digits, 6)
        self.assertTrue(
            any(command.endswith("NPLCycles?") for command, _error in snapshot.query_errors)
        )
        self.assertFalse(any(kind == "write" for kind, _command, _tid in instrument.log))

    def test_snapshot_timeout_marks_only_the_affected_field_unknown(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        instrument.query_overrides[":SENSE:CURRENT:DC:MEDIAN:RANK?"] = TimeoutError(
            "timeout simulado"
        )
        snapshot = controller.read_instrument_snapshot()
        self.assertIsNone(snapshot.median_rank)
        self.assertEqual(snapshot.nplc, 1.0)
        self.assertTrue(
            any(command.endswith("MEDian:RANK?") for command, _error in snapshot.query_errors)
        )

    def test_apply_advanced_delta_writes_only_changed_field_then_confirms(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        controller.read_instrument_snapshot()
        instrument.log.clear()
        confirmed = controller.apply_advanced_changes({"nplc": 2.0})
        writes = [command for kind, command, _tid in instrument.log if kind == "write"]
        self.assertEqual(writes, [":SENSe:CURRent:DC:NPLCycles 2"])
        self.assertEqual(confirmed.nplc, 2.0)
        operations = [(kind, command) for kind, command, _tid in instrument.log]
        write_index = operations.index(("write", ":SENSe:CURRent:DC:NPLCycles 2"))
        confirm_index = max(
            index
            for index, item in enumerate(operations)
            if item == ("query", ":SENSe:CURRent:DC:NPLCycles?")
        )
        self.assertLess(write_index, confirm_index)

    def test_zero_correct_uses_front_panel_sequence_and_keeps_zero_check_on(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        controller.read_instrument_snapshot()
        instrument.log.clear()

        confirmed = controller.acquire_zero_correct()

        writes = [command for kind, command, _tid in instrument.log if kind == "write"]
        self.assertEqual(
            writes,
            [
                ":SYSTem:ZCORrect:STATe OFF",
                ":SYSTem:ZCORrect:STATe ON",
            ],
        )
        self.assertTrue(confirmed.zero_check)
        self.assertTrue(confirmed.zero_correct)
        self.assertNotIn(":SYSTem:ZCORrect:ACQuire", writes)

    def test_enable_rel_delta_rewrites_cached_value_before_state_on(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        controller.read_instrument_snapshot()
        instrument.rel_value = 0.125
        instrument.log.clear()

        confirmed = controller.apply_advanced_changes({"rel_enabled": True})

        reference_writes = [
            command
            for kind, command, _tid in instrument.log
            if kind == "write" and ":REFerence" in command
        ]
        self.assertEqual(
            reference_writes,
            [
                ":SENSe:CURRent:DC:REFerence 1.250000000000E-01",
                ":SENSe:CURRent:DC:REFerence:STATe ON",
            ],
        )
        self.assertTrue(confirmed.rel_enabled)
        self.assertEqual(confirmed.rel_value, 0.125)

    def test_acquire_rel_takes_valid_reading_and_programs_it_explicitly(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        instrument.zero_check = False
        controller.read_instrument_snapshot()
        instrument.log.clear()

        confirmed = controller.acquire_rel()

        writes = [command for kind, command, _tid in instrument.log if kind == "write"]
        queries = [command for kind, command, _tid in instrument.log if kind == "query"]
        self.assertIn(":READ?", queries)
        self.assertNotIn(":SENSe:CURRent:DC:REFerence:ACQuire", writes)
        reference_writes = [command for command in writes if ":REFerence" in command]
        self.assertEqual(
            reference_writes,
            [
                ":SENSe:CURRent:DC:REFerence:STATe OFF",
                ":SENSe:CURRent:DC:REFerence 3.000000000000E-12",
                ":SENSe:CURRent:DC:REFerence:STATe ON",
            ],
        )
        self.assertTrue(confirmed.rel_enabled)
        self.assertAlmostEqual(confirmed.rel_value or 0.0, 3.0e-12)

    def test_reconnect_adopts_current_panel_state_without_cache_writeback(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        controller.read_instrument_snapshot()
        controller.disconnect()
        instrument.front_panel_set(nplc=10.0, aperture=10.0 / 60.0)
        instrument.log.clear()
        controller.connect("GPIB0::27::INSTR")
        snapshot = controller.read_instrument_snapshot()
        self.assertEqual(snapshot.nplc, 10.0)
        self.assertFalse(any(kind == "write" for kind, _command, _tid in instrument.log))

    def test_every_explicit_abort_is_preceded_by_continuous_off(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        self.configure_voltage(controller)
        controller.prepare_buffer(2, source="TIMer", timer_interval=0.1)
        commands = [command for kind, command, _tid in instrument.log if kind == "write"]
        for index, command in enumerate(commands):
            if command.upper() == ":ABORT":
                self.assertGreater(index, 0)
                self.assertEqual(
                    commands[index - 1].upper(), ":INITIATE:CONTINUOUS OFF"
                )
        self.assertFalse(any("FEED BEST" in command.upper() for command in commands))
        self.assertIn(":FORMat:ELEMents READing,TSTamp,STATus", commands)
        self.assertIn(":TRACe:ELEMents TSTamp", commands)
        self.assertEqual(controller.state, ControllerState.ARMED)

    def test_live_starts_once_and_uses_only_fresh_queries(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        self.configure_voltage(controller)
        instrument.log.clear()
        controller.start_live()
        first = controller.read_live()
        second = controller.read_live()
        queries = [command for kind, command, _tid in instrument.log if kind == "query"]
        writes = [command for kind, command, _tid in instrument.log if kind == "write"]
        self.assertNotIn(":READ?", queries)
        self.assertEqual(queries.count(":SENSe:DATA:FRESh?"), 2)
        self.assertEqual(writes.count(":INITiate:CONTinuous ON"), 1)
        operations = [(kind, command) for kind, command, _tid in instrument.log]
        self.assertLess(
            operations.index(("query", "*OPC?")),
            operations.index(("write", ":INITiate:CONTinuous ON")),
        )
        self.assertEqual(instrument.timeout, 5000)
        self.assertEqual(first.status, ReadingStatus.OK)
        self.assertEqual(second.status, ReadingStatus.OK)

    def test_live_restores_manual_trigger_setup_after_abort(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        controller.read_instrument_snapshot()
        instrument.front_panel_set(
            arm_count=4,
            arm_source="BUS",
            arm2_count=3,
            arm2_source="TLIN",
            trigger_source="TIM",
            trigger_count=11,
            trigger_timer=0.25,
            trigger_delay=0.005,
        )

        controller.start_live()
        self.assertEqual(instrument.trigger_count, 1)
        self.assertEqual(instrument.trigger_delay, 0.0)
        controller.abort()

        self.assertEqual(instrument.arm_count, 4)
        self.assertEqual(instrument.arm_source, "BUS")
        self.assertEqual(instrument.arm2_count, 3)
        self.assertEqual(instrument.arm2_source, "TLIN")
        self.assertEqual(instrument.trigger_source, "TIM")
        self.assertEqual(instrument.trigger_count, 11)
        self.assertEqual(instrument.trigger_timer, 0.25)
        self.assertEqual(instrument.trigger_delay, 0.005)

    def test_live_discards_stale_error_before_start(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        controller.read_instrument_snapshot()
        instrument.error_queue.append('-410,"Query INTERRUPTED"')
        instrument.log.clear()

        controller.start_live()

        self.assertEqual(controller.state, ControllerState.ACQUIRING)
        operations = [(kind, command) for kind, command, _tid in instrument.log]
        first_write = next(
            index for index, item in enumerate(operations) if item[0] == "write"
        )
        stale_error_read = operations.index(("query", ":SYSTem:ERRor?"))
        self.assertLess(stale_error_read, first_write)
        controller.abort()

    def test_successful_abort_recovers_error_state_to_configuration(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        controller.read_instrument_snapshot()
        controller.state_machine.force_error("erro simulado de aquisição")

        controller.abort()

        self.assertEqual(controller.state, ControllerState.CONFIGURED)
        self.assertFalse(instrument.error_queue)

    def test_one_shot_has_idle_sequence_then_read(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        self.configure_voltage(controller)
        instrument.log.clear()
        reading = controller.one_shot_read()
        operations = [(kind, command) for kind, command, _tid in instrument.log]
        off_index = operations.index(("write", ":INITiate:CONTinuous OFF"))
        abort_index = operations.index(("write", ":ABORt"))
        read_index = operations.index(("query", ":READ?"))
        self.assertLess(off_index, abort_index)
        self.assertLess(abort_index, read_index)
        self.assertEqual(reading.value, 3.0e-12)
        self.assertEqual(controller.state, ControllerState.CONFIGURED)

    def test_one_shot_restores_continuous_front_panel_display(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        self.configure_voltage(controller)
        instrument.continuous_initiation = True

        controller.one_shot_read()

        self.assertTrue(instrument.continuous_initiation)
        writes = [command for kind, command, _tid in instrument.log if kind == "write"]
        self.assertEqual(writes[-1], ":INITiate:CONTinuous ON")

    def test_live_abort_restores_continuous_front_panel_display(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        self.configure_voltage(controller)
        instrument.continuous_initiation = True

        controller.start_live()
        controller.abort()

        self.assertTrue(instrument.continuous_initiation)
        writes = [command for kind, command, _tid in instrument.log if kind == "write"]
        self.assertEqual(writes[-1], ":INITiate:CONTinuous ON")

    def test_live_abort_resumes_display_after_reset_left_continuous_off(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        self.configure_voltage(controller)
        # *RST/safe idle leaves the front-panel display stopped.
        instrument.continuous_initiation = False

        controller.start_live()
        controller.abort()

        self.assertTrue(instrument.continuous_initiation)
        writes = [command for kind, command, _tid in instrument.log if kind == "write"]
        self.assertEqual(writes[-1], ":INITiate:CONTinuous ON")

    def test_buffer_poll_and_parse_statuses(self) -> None:
        controller, _instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        self.configure_voltage(controller)
        controller.prepare_buffer(2, source="TIMer", timer_interval=0.01)
        controller.start_buffer()
        self.assertEqual(controller.wait_buffer_complete(1.0), 2)
        readings = controller.read_buffer_readings()
        self.assertEqual(
            [reading.status for reading in readings],
            [ReadingStatus.OK, ReadingStatus.OVERLOAD],
        )
        self.assertEqual(controller.state, ControllerState.CONFIGURED)

    def test_buffer_restores_manual_trigger_setup_after_transfer(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        controller.read_instrument_snapshot()
        instrument.front_panel_set(
            arm_count=5,
            arm_source="BUS",
            arm2_count=2,
            arm2_source="EXT",
            trigger_source="IMM",
            trigger_count=11,
            trigger_timer=0.5,
            trigger_delay=0.005,
        )

        controller.prepare_buffer(2, source="TIMer", timer_interval=0.01)
        controller.start_buffer()
        controller.wait_buffer_complete(1.0)
        controller.read_buffer_readings()

        self.assertEqual(instrument.arm_count, 5)
        self.assertEqual(instrument.arm_source, "BUS")
        self.assertEqual(instrument.arm2_count, 2)
        self.assertEqual(instrument.arm2_source, "EXT")
        self.assertEqual(instrument.trigger_source, "IMM")
        self.assertEqual(instrument.trigger_count, 11)
        self.assertEqual(instrument.trigger_timer, 0.5)
        self.assertEqual(instrument.trigger_delay, 0.005)

    def test_buffer_timeout_enters_error_and_can_recover_safe(self) -> None:
        controller, instrument, _manager = self.make_controller()
        instrument.auto_complete_buffer = False
        controller.connect("GPIB0::27::INSTR")
        self.configure_voltage(controller)
        controller.prepare_buffer(2, source="TIMer", timer_interval=0.01)
        controller.start_buffer()
        with self.assertRaises(AcquisitionTimeout):
            controller.wait_buffer_complete(0.04, poll_interval_s=0.02)
        self.assertEqual(controller.state, ControllerState.ERROR)
        controller.safe_shutdown()
        self.assertEqual(controller.state, ControllerState.SAFE)

    def test_buffer_validates_timer_and_model_limit(self) -> None:
        controller, _instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        self.configure_voltage(controller)
        with self.assertRaises(ValueError):
            controller.prepare_buffer(2, source="TIMer", timer_interval=0.0)
        with self.assertRaises(ValueError):
            controller.prepare_buffer(
                PROFILE_6517A.max_buffer_points_with_timestamp + 1,
                source="TIMer",
                timer_interval=0.1,
            )

    def test_charge_recipe_compensates_zero_check_hop(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        instrument.log.clear()
        controller.configure_measurement(
            function="CHARge",
            range_value=None,
            auto_range=True,
            nplc=1.0,
            digits=6,
        )
        operations = [(kind, command) for kind, command, _tid in instrument.log]
        z_on = operations.index(("write", ":SYSTem:ZCHeck ON"))
        function = operations.index(("write", ":SENSe:FUNCtion 'CHARge'"))
        z_off = operations.index(("write", ":SYSTem:ZCHeck OFF"))
        read = operations.index(("query", ":READ?"))
        acquire = operations.index(
            ("write", ":SENSe:CHARge:REFerence:ACQuire")
        )
        rel_on = operations.index(
            ("write", ":SENSe:CHARge:REFerence:STATe ON")
        )
        self.assertLess(z_on, function)
        self.assertLess(function, z_off)
        self.assertLess(z_off, read)
        self.assertLess(read, acquire)
        self.assertLess(acquire, rel_on)

    def test_scpi_safety_parser_covers_abbreviations_and_program_messages(self) -> None:
        dangerous = (
            ":OUTP ON",
            ":OUTP +1.0",
            "CMD1;:OUTPut1:STATe 1",
            ":SENS:RES:MAN:VSOUR:OPER ON",
            ":SOUR:VOLT 500",
            ":TSEQ:ARM",
        )
        for command in dangerous:
            with self.subTest(command=command):
                self.assertTrue(analyze_scpi_safety(command))
        self.assertFalse(analyze_scpi_safety(":OUTPut1 OFF"))
        self.assertFalse(analyze_scpi_safety(":OUTPut1?"))

    def test_hv_requires_authorisation_and_interlock(self) -> None:
        controller, _instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        with self.assertRaises(UnsafeCommandError):
            controller.write(":OUTP ON")
        with self.assertRaises(UnsafeCommandError):
            controller.write(":OUTP ON", allow_hv=True)
        controller.write(
            ":OUTP ON",
            allow_hv=True,
            physical_interlock_confirmed=True,
        )
        self.assertEqual(controller.state, ControllerState.HV_ENABLED)
        controller.write(":OUTP OFF")
        self.assertEqual(controller.state, ControllerState.SAFE)

    def test_dedicated_hv_configuration_is_standby_limited_and_verified(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        instrument.log.clear()
        status = controller.configure_voltage_source(250.0, 300.0)
        writes = [command for kind, command, _tid in instrument.log if kind == "write"]
        self.assertEqual(writes[0], ":OUTPut1 OFF")
        self.assertEqual(status.voltage, 250.0)
        self.assertEqual(status.range_value, 1000.0)
        self.assertEqual(status.voltage_limit, 300.0)
        self.assertTrue(status.limit_enabled)
        self.assertFalse(status.output_enabled)
        with self.assertRaises(ValueError):
            controller.configure_voltage_source(250.0, 200.0)
        low_range = controller.configure_voltage_source(50.0, 300.0)
        self.assertEqual(low_range.range_value, 100.0)

    def test_resistance_manual_source_uses_dedicated_commands(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        controller.configure_measurement(
            "RESistance", None, True, 1.0, 6, resistance_vsource_mode="MAN"
        )
        instrument.log.clear()
        status = controller.configure_voltage_source(1.0, 5.0)
        writes = [command for kind, command, _tid in instrument.log if kind == "write"]
        self.assertIn(":SENSe:RESistance:VSC MAN", writes)
        self.assertIn(":SENSe:RESistance:MANual:VSOurce:RANGe 100", writes)
        self.assertIn(":SENSe:RESistance:MANual:VSOurce:AMPLitude 1.000000E+00", writes)
        self.assertNotIn(":SOURce:VOLTage 0", writes)
        self.assertEqual(status.voltage, 1.0)
        self.assertEqual(status.range_value, 100.0)
        self.assertEqual(status.voltage_limit, 5.0)
        self.assertTrue(status.limit_enabled)
        self.assertFalse(status.output_enabled)

    def test_resistance_auto_rejects_generic_hv_configuration(self) -> None:
        controller, _instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        controller.configure_measurement(
            "RESistance", None, True, 1.0, 6, resistance_vsource_mode="AUTO"
        )
        with self.assertRaises(Exception) as caught:
            controller.configure_voltage_source(1.0, 5.0)
        self.assertIn("Selecione MAN", str(caught.exception))

    def test_dedicated_hv_enable_acquisition_and_disable(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        self.configure_voltage(controller)
        controller.configure_voltage_source(-75.0, 80.0)
        enabled = controller.enable_voltage_source(physical_interlock_confirmed=True)
        self.assertTrue(enabled.output_enabled)
        self.assertTrue(controller.hv_enabled)
        self.assertEqual(controller.state, ControllerState.HV_ENABLED)
        controller.start_live()
        self.assertEqual(controller.state, ControllerState.ACQUIRING)
        controller.abort()
        self.assertEqual(controller.state, ControllerState.HV_ENABLED)
        disabled = controller.disable_voltage_source()
        self.assertFalse(disabled.output_enabled)
        self.assertEqual(disabled.voltage, 0.0)
        self.assertFalse(controller.hv_enabled)
        self.assertEqual(controller.state, ControllerState.CONFIGURED)
        self.assertFalse(instrument.source_output_enabled)

    def test_dedicated_hv_enable_is_blocked_by_interlock(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        controller.configure_voltage_source(50.0, 60.0)
        instrument.interlock_ok = False
        with self.assertRaises(UnsafeCommandError):
            controller.enable_voltage_source(physical_interlock_confirmed=True)
        self.assertFalse(instrument.source_output_enabled)
        self.assertEqual(controller.state, ControllerState.SAFE)

    def test_raw_abort_is_normalised_to_required_order(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        instrument.log.clear()
        controller.write(":ABORt")
        writes = [command for kind, command, _tid in instrument.log if kind == "write"]
        self.assertEqual(
            writes[:2], [":INITiate:CONTinuous OFF", ":ABORt"]
        )

    def test_raw_console_rejects_compound_program_messages(self) -> None:
        controller, _instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        with self.assertRaises(ValueError):
            controller.query("*IDN?;*OPT?")

    def test_resistance_reading_reports_compliance(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        instrument.compliance = True
        controller.configure_measurement(
            "RESistance", None, True, 1.0, 6
        )
        controller.set_format_elements()
        controller.start_live()
        reading = controller.read_live()
        self.assertEqual(reading.status, ReadingStatus.COMPLIANCE)

    def test_concurrent_callers_are_serialised_on_one_visa_thread(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        instrument.log.clear()
        instrument.query_delay = 0.005
        errors: List[Exception] = []

        def query_identity() -> None:
            try:
                controller.identify()
            except Exception as error:  # pragma: no cover - asserted below
                errors.append(error)

        threads = [threading.Thread(target=query_identity) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(errors)
        access_threads = {
            tid for _kind, _command, tid in instrument.log
        }
        self.assertEqual(access_threads, {controller._worker.owner_thread_ident})

    def test_instrument_error_changes_state(self) -> None:
        controller, instrument, _manager = self.make_controller()
        controller.connect("GPIB0::27::INSTR")
        instrument.error_queue.extend(
            ['-213,"Init ignored"', '-350,"Queue overflow"']
        )
        with self.assertRaises(InstrumentCommandError) as caught:
            controller.configure_measurement(
                "VOLTage:DC", None, True, 1.0, 6
            )
        self.assertTrue(any("-213" in error for error in caught.exception.errors))
        self.assertTrue(any("-350" in error for error in caught.exception.errors))
        self.assertEqual(controller.state, ControllerState.ERROR)


class ParsingTests(unittest.TestCase):
    def test_reading_classification(self) -> None:
        cases: Dict[str, ReadingStatus] = {
            "+1.0E-12,0.1,N": ReadingStatus.OK,
            "+9.910000E+37,0.1,O": ReadingStatus.OVERLOAD,
            "+0.000000E+00,0.1,U": ReadingStatus.UNDERFLOW,
            "+9.910000E+37,0.1,Z": ReadingStatus.INVALID,
            "nan,0.1,N": ReadingStatus.INVALID,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_reading_response(raw).status, expected)
        inline = parse_reading_response("-0000.001E-09R")
        self.assertEqual(inline.raw_value, "-0000.001E-09R")
        self.assertEqual(inline.instrument_status, "R")
        self.assertAlmostEqual(inline.value, -1.0e-12)
        self.assertEqual(inline.status, ReadingStatus.OK)
        self.assertEqual(
            classify_reading("1.0", compliance=True), ReadingStatus.COMPLIANCE
        )

    def test_malformed_buffer_is_rejected(self) -> None:
        with self.assertRaises(Exception):
            parse_buffer_response("1.0,0.0")

    def test_buffer_parser_accepts_6517a_inline_status_columns(self) -> None:
        readings = parse_buffer_response(
            "-0000.001E-09R,0.0,+9.910000E+37O,0.1,+2.000000E-12N,0.2"
        )
        self.assertEqual(len(readings), 3)
        self.assertAlmostEqual(readings[0].value, -1.0e-12)
        self.assertEqual(readings[0].status, ReadingStatus.OK)
        self.assertEqual(readings[0].instrument_status, "R")
        self.assertEqual(readings[1].status, ReadingStatus.OVERLOAD)
        self.assertEqual(readings[1].instrument_status, "O")
        self.assertEqual(readings[2].status, ReadingStatus.OK)

    def test_buffer_parser_preserves_empty_timestamp_column(self) -> None:
        readings = parse_buffer_response("1.0,,N")
        self.assertEqual(len(readings), 1)
        self.assertEqual(readings[0].raw_timestamp, "0")
        self.assertEqual(readings[0].status, ReadingStatus.OK)

    def test_csv_contains_only_display_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reading.csv"
            with CsvAcquisitionWriter(path) as writer:
                writer.write(
                    1,
                    1.25,
                    9.91e37,
                    "+9.910000E+37",
                    "A",
                    "OVERLOAD",
                    "6517A",
                    "1234",
                    "A01",
                )
            contents = path.read_text(encoding="utf-8")
            self.assertEqual(
                contents.splitlines()[0],
                "#,Tempo (s),Valor,Un.",
            )
            self.assertEqual(contents.splitlines()[1], "1,1.25,9.91e+37,A")

    def test_xlsx_export_contains_only_display_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "reading.csv"
            xlsx_path = Path(directory) / "reading.xlsx"
            with CsvAcquisitionWriter(csv_path) as writer:
                writer.write(
                    1,
                    1.25,
                    2.5,
                    "+2.500000E+00",
                    "V",
                    "OK",
                    "6517A",
                    "1234",
                    "A01",
                )
            export_csv_to_xlsx(csv_path, xlsx_path)
            with zipfile.ZipFile(xlsx_path) as archive:
                sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
            self.assertIn("Tempo (s)", sheet)
            self.assertIn("Valor", sheet)
            self.assertIn("Un.", sheet)
            self.assertNotIn("media", sheet)
            self.assertNotIn("desvpad", sheet)
            self.assertNotIn("erro%", sheet)
            self.assertNotIn("<f>", sheet)
            self.assertNotIn("raw_value", sheet)
            self.assertNotIn("status", sheet)


if __name__ == "__main__":
    unittest.main()
