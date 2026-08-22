"""Deterministic control layer for Keithley 6517A/6517B electrometers.

The module deliberately contains no GUI code.  It implements the layers used by
the current application:

* model-specific SCPI profiles selected from ``*IDN?``;
* an explicit instrument state machine;
* measurement recipes (with a specialised charge recipe);
* a SCPI command builder and response parser;
* one VISA owner thread with a FIFO request queue.

Only :class:`VisaWorker` touches a ResourceManager or instrument session.  All
public controller methods are safe to call from different application threads.
"""

from __future__ import annotations

import logging
import math
import queue
import re
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import pyvisa
except ImportError:  # pragma: no cover - hardware dependency is optional in tests
    pyvisa = None


PROTOCOL_LOGGER = logging.getLogger("keithley.protocol")


DEFAULT_TIMEOUT_MS = 5000
MAX_ERRORS_PER_CHECK = 20


class KeithleyError(RuntimeError):
    """Base error raised by the control layer."""


class UnsupportedInstrumentError(KeithleyError):
    """The connected resource is not a supported 6517A/6517B."""


class ModelMismatchError(UnsupportedInstrumentError):
    """The detected model differs from the model selected by the operator."""


class StateError(KeithleyError):
    """An operation was requested in an invalid instrument state."""


class InstrumentCommandError(KeithleyError):
    """The instrument reported one or more SCPI errors."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__(" | ".join(self.errors))


class AcquisitionTimeout(KeithleyError):
    """A finite acquisition did not complete before its deadline."""


class AcquisitionCancelled(KeithleyError):
    """A finite acquisition was cancelled by the application."""


class UnsafeCommandError(KeithleyError):
    """A raw SCPI command needs an explicit high-voltage authorisation."""


class ControllerState(Enum):
    DISCONNECTED = "Disconnected"
    CONNECTED = "Connected"
    SAFE = "Safe"
    CONFIGURED = "Configured"
    ARMED = "Armed"
    ACQUIRING = "Acquiring"
    HV_ENABLED = "HV Enabled"
    ERROR = "Error"


class AcquisitionMode(Enum):
    ONE_SHOT = "ONE-SHOT"
    LIVE = "LIVE"
    BUFFER = "BUFFER"


class ReadingStatus(Enum):
    OK = "OK"
    OVERLOAD = "OVERLOAD"
    UNDERFLOW = "UNDERFLOW"
    COMPLIANCE = "COMPLIANCE"
    INVALID = "INVALID"
    ERROR = "ERROR"


class InterlockState(Enum):
    """Meaning of the 6517 interlock query.

    The instruments do not distinguish a correctly closed fixture from an
    interlock cable that is absent at the rear panel.  A truthy SCPI response
    is therefore deliberately *not* named SAFE or CLOSED.
    """

    BLOCKED = "Blocked"
    CLOSED_OR_CABLE_ABSENT = "Closed or cable absent"


@dataclass(frozen=True)
class InstrumentProfile:
    model: str
    idn_pattern: str
    max_buffer_points_with_timestamp: int
    fresh_timeout_ms: int
    fresh_query: str
    error_query: str
    buffer_actual_query: str
    buffer_data_query: str
    compliance_query: str
    interlock_query: str
    output_on: str
    output_off: str
    manual_vsource_off: str
    zero_check_on: str
    zero_check_off: str
    format_commands: Tuple[str, ...]
    trace_element_command: str
    response_elements: Tuple[str, ...]
    format_elements_query: str
    trace_elements_query: str
    scpi_version: str


# The command spellings are deliberately stored in two independent profiles.
# They currently overlap in several places, but are not implicitly shared: a
# future firmware-specific difference can be changed without affecting the
# other model.
PROFILE_6517A = InstrumentProfile(
    model="6517A",
    idn_pattern=r"\bMODEL\s*6517A\b|\b6517A\b",
    max_buffer_points_with_timestamp=10470,
    fresh_timeout_ms=7000,
    fresh_query=":SENSe:DATA:FRESh?",
    error_query=":SYSTem:ERRor?",
    buffer_actual_query=":TRACe:POINts:ACTual?",
    buffer_data_query=":TRACe:DATA?",
    compliance_query=":SOURce:CURRent:LIMit:STATe?",
    interlock_query=":SYSTem:INTerlock?",
    output_on=":OUTPut1 ON",
    output_off=":OUTPut1 OFF",
    manual_vsource_off=":SENSe:RESistance:MANual:VSOurce:OPERate OFF",
    zero_check_on=":SYSTem:ZCHeck ON",
    zero_check_off=":SYSTem:ZCHeck OFF",
    format_commands=(
        ":FORMat:DATA ASCii",
        ":FORMat:ELEMents READing,TSTamp,STATus",
    ),
    # The format query includes READing, TSTamp and STATus.  On the 6517A,
    # :TRACe:DATA? may serialize each record as READing,TSTamp with STATus
    # attached to the reading (for example ``-0000.001E-09R``).
    trace_element_command=":TRACe:ELEMents TSTamp",
    response_elements=("READING", "TSTAMP", "STATUS"),
    format_elements_query=":FORMat:ELEMents?",
    trace_elements_query=":TRACe:ELEMents?",
    scpi_version="1991.0",
)

PROFILE_6517B = InstrumentProfile(
    model="6517B",
    idn_pattern=r"\bMODEL\s*6517B\b|\b6517B\b",
    max_buffer_points_with_timestamp=50000,
    fresh_timeout_ms=7000,
    fresh_query=":SENSe:DATA:FRESh?",
    error_query=":SYSTem:ERRor?",
    buffer_actual_query=":TRACe:POINts:ACTual?",
    buffer_data_query=":TRACe:DATA?",
    compliance_query=":SOURce:CURRent:LIMit:STATe?",
    interlock_query=":SYSTem:INTerlock?",
    output_on=":OUTPut1 ON",
    output_off=":OUTPut1 OFF",
    manual_vsource_off=":SENSe:RESistance:MANual:VSOurce:OPERate OFF",
    zero_check_on=":SYSTem:ZCHeck ON",
    zero_check_off=":SYSTem:ZCHeck OFF",
    format_commands=(
        ":FORMat:DATA ASCii",
        ":FORMat:ELEMents READing,TSTamp,STATus",
    ),
    trace_element_command=":TRACe:ELEMents TSTamp",
    response_elements=("READING", "TSTAMP", "STATUS"),
    format_elements_query=":FORMat:ELEMents?",
    trace_elements_query=":TRACe:ELEMents?",
    scpi_version="1996.0",
)

SUPPORTED_PROFILES: Tuple[InstrumentProfile, ...] = (PROFILE_6517A, PROFILE_6517B)


def detect_instrument_profile(identity: str) -> InstrumentProfile:
    """Return the exact 6517 profile identified by an ``*IDN?`` response."""

    normalised = " ".join((identity or "").upper().split())
    if "KEITHLEY" not in normalised:
        raise UnsupportedInstrumentError(
            "O recurso respondeu a *IDN?, mas não se identificou como Keithley."
        )
    for profile in SUPPORTED_PROFILES:
        if re.search(profile.idn_pattern, normalised, re.IGNORECASE):
            return profile
    raise UnsupportedInstrumentError(
        "Modelo não suportado. São aceitos somente Keithley 6517A e 6517B: "
        + identity.strip()
    )


class InstrumentStateMachine:
    """Small, strict and thread-safe state machine for instrument operations."""

    _ALLOWED: Dict[ControllerState, Tuple[ControllerState, ...]] = {
        ControllerState.DISCONNECTED: (ControllerState.CONNECTED,),
        ControllerState.CONNECTED: (
            ControllerState.SAFE,
            ControllerState.ERROR,
            ControllerState.DISCONNECTED,
        ),
        ControllerState.SAFE: (
            ControllerState.CONFIGURED,
            ControllerState.HV_ENABLED,
            ControllerState.ERROR,
            ControllerState.DISCONNECTED,
        ),
        ControllerState.CONFIGURED: (
            ControllerState.SAFE,
            ControllerState.ARMED,
            ControllerState.HV_ENABLED,
            ControllerState.ERROR,
            ControllerState.DISCONNECTED,
        ),
        ControllerState.ARMED: (
            ControllerState.ACQUIRING,
            ControllerState.CONFIGURED,
            ControllerState.HV_ENABLED,
            ControllerState.SAFE,
            ControllerState.ERROR,
            ControllerState.DISCONNECTED,
        ),
        ControllerState.ACQUIRING: (
            ControllerState.CONFIGURED,
            ControllerState.HV_ENABLED,
            ControllerState.SAFE,
            ControllerState.ERROR,
            ControllerState.DISCONNECTED,
        ),
        ControllerState.HV_ENABLED: (
            ControllerState.ARMED,
            ControllerState.SAFE,
            ControllerState.CONFIGURED,
            ControllerState.ERROR,
            ControllerState.DISCONNECTED,
        ),
        ControllerState.ERROR: (
            ControllerState.SAFE,
            ControllerState.DISCONNECTED,
        ),
    }

    def __init__(self) -> None:
        self._state = ControllerState.DISCONNECTED
        self._lock = threading.RLock()
        self._history: List[Tuple[float, ControllerState, ControllerState, str]] = []

    @property
    def state(self) -> ControllerState:
        with self._lock:
            return self._state

    @property
    def history(self) -> List[Tuple[float, ControllerState, ControllerState, str]]:
        with self._lock:
            return list(self._history)

    def require(self, *allowed: ControllerState) -> None:
        with self._lock:
            if self._state not in allowed:
                expected = ", ".join(state.value for state in allowed)
                raise StateError(
                    "Operação inválida no estado {0}; esperado: {1}.".format(
                        self._state.value, expected
                    )
                )

    def transition(self, target: ControllerState, reason: str = "") -> None:
        with self._lock:
            source = self._state
            if target == source:
                return
            if target not in self._ALLOWED[source]:
                raise StateError(
                    "Transição inválida: {0} -> {1}.".format(
                        source.value, target.value
                    )
                )
            self._state = target
            self._history.append((time.monotonic(), source, target, reason))
            logging.info("Estado do instrumento: %s -> %s (%s)", source.value, target.value, reason)

    def force_error(self, reason: str) -> None:
        with self._lock:
            source = self._state
            if source == ControllerState.DISCONNECTED:
                return
            self._state = ControllerState.ERROR
            self._history.append(
                (time.monotonic(), source, ControllerState.ERROR, reason)
            )
            logging.error("Estado do instrumento: %s -> Error (%s)", source.value, reason)


@dataclass(frozen=True)
class SafetyFinding:
    command: str
    category: str
    description: str


def _split_scpi_program(message: str) -> List[str]:
    """Split a SCPI program message on semicolons outside quoted strings."""

    parts: List[str] = []
    current: List[str] = []
    quote: Optional[str] = None
    for char in message or "":
        if quote:
            current.append(char)
            if char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
            current.append(char)
        elif char == ";":
            token = "".join(current).strip()
            if token:
                parts.append(token)
            current = []
        else:
            current.append(char)
    token = "".join(current).strip()
    if token:
        parts.append(token)
    return parts


def _is_query_program_unit(unit: str) -> bool:
    """Return true only when the program-unit header itself is a query."""

    header = (unit or "").strip().partition(" ")[0]
    return bool(header) and header.endswith("?") and header.count("?") == 1


def _scpi_word_matches(word: str, short: str, long: str) -> bool:
    candidate = re.sub(r"\d+$", "", word.upper())
    return len(candidate) >= len(short) and long.startswith(candidate)


def _contains_path(path: Sequence[str], specification: Sequence[Tuple[str, str]]) -> bool:
    if len(path) < len(specification):
        return False
    for start in range(0, len(path) - len(specification) + 1):
        if all(
            _scpi_word_matches(path[start + offset], short, long)
            for offset, (short, long) in enumerate(specification)
        ):
            return True
    return False


def analyze_scpi_safety(message: str) -> List[SafetyFinding]:
    """Detect high-voltage enable/configuration hidden in SCPI program messages.

    Every semicolon-separated program unit is inspected, including abbreviated
    spellings such as ``:OUTP ON`` and the resistance manual-source path.
    """

    findings: List[SafetyFinding] = []
    previous_path: List[str] = []
    for unit in _split_scpi_program(message):
        header, separator, parameter = unit.partition(" ")
        header = header.strip()
        parameter = parameter.strip()
        if not separator:
            parameter = ""
        is_query = "?" in header
        clean_header = header.replace("?", "").strip()
        absolute = clean_header.startswith(":") or clean_header.startswith("*")
        path = [part for part in clean_header.lstrip(":").split(":") if part]

        candidate_paths: List[List[str]] = [path]
        if not absolute and previous_path:
            candidate_paths.append(previous_path[:-1] + path)
            candidate_paths.append(previous_path + path)
        if absolute and not clean_header.startswith("*"):
            previous_path = path
        elif path and not clean_header.startswith("*"):
            previous_path = candidate_paths[1] if len(candidate_paths) > 1 else path

        parameter_word = parameter.split(",", 1)[0].strip().upper()
        enabled = parameter_word in ("ON", "ONCE")
        try:
            boolean_number = float(parameter_word)
            enabled = enabled or boolean_number != 0.0
        except ValueError:
            pass
        for candidate in candidate_paths:
            output_path = bool(candidate) and _scpi_word_matches(
                candidate[0], "OUTP", "OUTPUT"
            )
            output_state_path = output_path and (
                len(candidate) == 1
                or (
                    len(candidate) == 2
                    and _scpi_word_matches(candidate[-1], "STAT", "STATE")
                )
            )
            manual_vsource_path = _contains_path(
                candidate,
                (
                    ("SENS", "SENSE"),
                    ("RES", "RESISTANCE"),
                    ("MAN", "MANUAL"),
                    ("VSOU", "VSOURCE"),
                    ("OPER", "OPERATE"),
                ),
            )
            test_sequence_arm = _contains_path(
                candidate, (("TSEQ", "TSEQUENCE"), ("ARM", "ARM"))
            )
            source_voltage = _contains_path(
                candidate, (("SOUR", "SOURCE"), ("VOLT", "VOLTAGE"))
            )
            manual_vsource_config = _contains_path(
                candidate,
                (
                    ("SENS", "SENSE"),
                    ("RES", "RESISTANCE"),
                    ("MAN", "MANUAL"),
                    ("VSOU", "VSOURCE"),
                ),
            ) and not manual_vsource_path

            if not is_query and enabled and (output_state_path or manual_vsource_path):
                findings.append(
                    SafetyFinding(unit, "HV_ENABLE", "habilita a fonte de alta tensão")
                )
                break
            if not is_query and test_sequence_arm:
                findings.append(
                    SafetyFinding(
                        unit,
                        "HV_ENABLE",
                        "arma uma sequência que pode operar a fonte de alta tensão",
                    )
                )
                break
            numeric_nonzero = False
            if parameter and not is_query:
                try:
                    numeric_nonzero = float(parameter.split(",", 1)[0]) != 0.0
                except ValueError:
                    numeric_nonzero = parameter_word not in ("OFF", "0")
            if not is_query and numeric_nonzero and (source_voltage or manual_vsource_config):
                findings.append(
                    SafetyFinding(unit, "HV_CONFIG", "programa um nível de alta tensão")
                )
                break
    return findings


def is_dangerous_command(command: str) -> bool:
    return bool(analyze_scpi_safety(command))


def _message_hv_effect(messages: Sequence[str]) -> Optional[bool]:
    """Return the final explicit HV output state in a raw message batch."""

    effect: Optional[bool] = None
    for message in messages:
        for unit in _split_scpi_program(message):
            header, _space, parameter = unit.partition(" ")
            path = [part for part in header.replace("?", "").lstrip(":").split(":") if part]
            output = bool(path) and _scpi_word_matches(path[0], "OUTP", "OUTPUT")
            manual = _contains_path(
                path,
                (
                    ("SENS", "SENSE"),
                    ("RES", "RESISTANCE"),
                    ("MAN", "MANUAL"),
                    ("VSOU", "VSOURCE"),
                    ("OPER", "OPERATE"),
                ),
            )
            value = parameter.strip().upper()
            numeric_value: Optional[float]
            try:
                numeric_value = float(value)
            except ValueError:
                numeric_value = None
            if (output or manual) and (
                value in ("ON", "ONCE")
                or (numeric_value is not None and numeric_value != 0.0)
            ):
                effect = True
            elif (output or manual) and (
                value == "OFF"
                or (numeric_value is not None and numeric_value == 0.0)
            ):
                effect = False
    return effect


def _is_abort_program_unit(unit: str) -> bool:
    header = unit.strip().partition(" ")[0].lstrip(":").replace("?", "")
    return _scpi_word_matches(header, "ABOR", "ABORT")


def _normalise_raw_abort_commands(commands: Sequence[str]) -> List[str]:
    """Guarantee CONTinuous OFF -> ABORt even from the advanced console."""

    normalised: List[str] = []
    for command in commands:
        units = _split_scpi_program(command)
        if len(units) == 1 and _is_abort_program_unit(units[0]):
            normalised.extend(SCPICommandBuilder.idle_commands())
            continue
        if any(_is_abort_program_unit(unit) for unit in units):
            rebuilt: List[str] = []
            for unit in units:
                if _is_abort_program_unit(unit):
                    rebuilt.extend(SCPICommandBuilder.idle_commands())
                else:
                    rebuilt.append(unit)
            normalised.append(";".join(rebuilt))
        else:
            normalised.append(command)
    return normalised


@dataclass(frozen=True)
class MeasurementConfig:
    function: str
    range_value: Optional[float]
    auto_range: bool
    nplc: float
    digits: Optional[int]
    charge_auto_discharge: bool = False


@dataclass(frozen=True)
class MeasurementReading:
    raw_value: str
    raw_timestamp: str
    value: float
    timestamp: float
    instrument_status: str
    status: ReadingStatus
    error: str = ""

    @classmethod
    def from_error(cls, error: Exception) -> "MeasurementReading":
        return cls(
            raw_value="nan",
            raw_timestamp="0",
            value=float("nan"),
            timestamp=0.0,
            instrument_status="",
            status=ReadingStatus.ERROR,
            error=str(error),
        )


@dataclass(frozen=True)
class VoltageSourceStatus:
    """Readback of the 6517 programmable voltage source."""

    voltage: float
    range_value: float
    voltage_limit: float
    limit_enabled: bool
    output_enabled: bool
    interlock_ok: bool
    compliance: bool
    interlock_raw: str = ""

    @property
    def interlock_state(self) -> InterlockState:
        """Return the documented, intentionally ambiguous interlock state."""

        return (
            InterlockState.CLOSED_OR_CABLE_ABSENT
            if self.interlock_ok
            else InterlockState.BLOCKED
        )

    @property
    def nominal_current_limit_a(self) -> float:
        """Nominal V-source current capability for the selected source range."""

        return 10.0e-3 if abs(self.range_value) <= 100.0 else 1.0e-3


def classify_reading(
    raw_value: str,
    value: Optional[float] = None,
    instrument_status: str = "",
    compliance: bool = False,
    error: Optional[Exception] = None,
) -> ReadingStatus:
    """Classify a 6517 reading using status element and numeric sentinels."""

    if error is not None:
        return ReadingStatus.ERROR
    status = (instrument_status or "").strip().upper()
    status_words = set(re.findall(r"[A-Z]+", status))
    if compliance:
        return ReadingStatus.COMPLIANCE
    if status_words.intersection(("O", "OFLO", "OVER", "OVERFLOW")):
        return ReadingStatus.OVERLOAD
    if status_words.intersection(("U", "UFLO", "UNDER", "UNDERFLOW")):
        return ReadingStatus.UNDERFLOW
    # Z is a zero-check placeholder and L is out-of-limits, not a valid sample.
    if status_words.intersection(("Z", "L")):
        return ReadingStatus.INVALID
    try:
        numeric = float(raw_value) if value is None else float(value)
    except (TypeError, ValueError):
        return ReadingStatus.INVALID
    if not math.isfinite(numeric):
        return ReadingStatus.INVALID
    # Keithley uses approximately +9.91E37 as the ASCII overload sentinel.
    # When STATus=Z is present it has already been classified as INVALID above.
    if abs(numeric) >= 9.9e37:
        return ReadingStatus.OVERLOAD
    return ReadingStatus.OK


def _split_inline_reading_status(raw_value: str) -> Tuple[str, str]:
    """Split a Keithley ASCII status suffix when it is attached to the value.

    The 6517A returns LIVE charge readings in the compact form
    ``-0000.001E-09R``.  Buffered responses normally expose ``STATus`` as a
    separate comma-delimited field, but ``:SENSe:DATA:FRESh?`` may append the
    one-character status directly to the numeric value.
    """

    if len(raw_value) <= 1:
        return raw_value, ""
    suffix = raw_value[-1].upper()
    if suffix not in ("N", "O", "U", "Z", "R", "L"):
        return raw_value, ""
    numeric_part = raw_value[:-1]
    try:
        float(numeric_part)
    except ValueError:
        return raw_value, ""
    return numeric_part, suffix


def _is_float_or_empty(value: str) -> bool:
    if not value:
        return True
    try:
        float(value)
    except ValueError:
        return False
    return True


def parse_reading_response(raw: str, compliance: bool = False) -> MeasurementReading:
    tokens = [token.strip() for token in (raw or "").split(",")]
    if not tokens or not tokens[0]:
        return MeasurementReading.from_error(ValueError("Resposta de leitura vazia."))
    raw_value = tokens[0]
    numeric_value, inline_status = _split_inline_reading_status(raw_value)
    raw_timestamp = tokens[1] if len(tokens) > 1 and tokens[1] else "0"
    instrument_status = tokens[2] if len(tokens) > 2 and tokens[2] else inline_status
    try:
        value = float(numeric_value)
        timestamp = float(raw_timestamp)
    except ValueError as error:
        status = classify_reading(
            numeric_value,
            instrument_status=instrument_status,
            compliance=compliance,
        )
        return MeasurementReading(
            raw_value,
            raw_timestamp,
            float("nan"),
            0.0,
            instrument_status,
            status,
            str(error),
        )
    return MeasurementReading(
        raw_value=raw_value,
        raw_timestamp=raw_timestamp,
        value=value,
        timestamp=timestamp,
        instrument_status=instrument_status,
        status=classify_reading(
            raw_value,
            value=value,
            instrument_status=instrument_status,
            compliance=compliance,
        ),
    )


def _canonical_element_name(value: str) -> str:
    token = re.sub(r"[^A-Z]", "", (value or "").upper())
    aliases = {
        "READ": "READING",
        "READING": "READING",
        "TST": "TSTAMP",
        "TSTAMP": "TSTAMP",
        "TIMESTAMP": "TSTAMP",
        "STAT": "STATUS",
        "STATUS": "STATUS",
        "RNUM": "RNUMBER",
        "RNUMBER": "RNUMBER",
        "UNIT": "UNITS",
        "UNITS": "UNITS",
    }
    return aliases.get(token, token)


def parse_buffer_response(
    raw: str,
    compliance: bool = False,
    elements: Sequence[str] = ("READING", "TSTAMP", "STATUS"),
) -> List[MeasurementReading]:
    """Parse an ASCII trace using the confirmed FORMAT element schema.

    Empty fields are intentionally retained so a malformed instrument response
    cannot silently shift timestamp or status columns.  The 6517A can omit the
    separate STATUS column in the trace transfer and append its one-character
    status to each reading instead; that compact two-column form is accepted
    only when every reading contains a recognized inline status.
    """

    tokens = [token.strip() for token in (raw or "").split(",")]
    if len(tokens) == 1 and not tokens[0]:
        return []
    schema = tuple(_canonical_element_name(element) for element in elements)
    if not schema or "READING" not in schema:
        raise KeithleyError("O esquema do buffer precisa conter READing.")
    if not tokens:
        return []
    effective_schema = schema
    compact_schema = ("READING", "TSTAMP")
    inline_statuses = (
        schema == ("READING", "TSTAMP", "STATUS")
        and len(tokens) % len(compact_schema) == 0
        and all(
            _split_inline_reading_status(tokens[index])[1]
            and _is_float_or_empty(tokens[index + 1])
            for index in range(0, len(tokens), len(compact_schema))
        )
    )
    if inline_statuses:
        effective_schema = compact_schema
    if len(tokens) % len(effective_schema) != 0:
        raise KeithleyError(
            ":TRACe:DATA? retornou {0} elementos; o esquema confirmado possui {1}: {2}.".format(
                len(tokens), len(schema), ",".join(schema)
            )
        )
    readings: List[MeasurementReading] = []
    for index in range(0, len(tokens), len(effective_schema)):
        record = dict(
            zip(effective_schema, tokens[index : index + len(effective_schema)])
        )
        readings.append(
            parse_reading_response(
                ",".join(
                    (
                        record.get("READING", ""),
                        record.get("TSTAMP", "0"),
                        record.get("STATUS", ""),
                    )
                ),
                compliance=compliance,
            )
        )
    return readings


class SCPICommandBuilder:
    """Build validated, complete command blocks for both instrument profiles."""

    _FUNCTIONS: Dict[str, str] = {
        "VOLTAGE:DC": "VOLTage:DC",
        "VOLTAGE": "VOLTage:DC",
        "VOLT": "VOLTage:DC",
        "VOLT:DC": "VOLTage:DC",
        "CURRENT:DC": "CURRent:DC",
        "CURRENT": "CURRent:DC",
        "CURR": "CURRent:DC",
        "CURR:DC": "CURRent:DC",
        "RESISTANCE": "RESistance",
        "RES": "RESistance",
        "CHARGE": "CHARge",
        "CHAR": "CHARge",
    }

    @classmethod
    def function_path(cls, function: str) -> str:
        key = (function or "").strip().strip("'\"").upper()
        if key not in cls._FUNCTIONS:
            raise ValueError("Função de medição não suportada: {0}".format(function))
        return cls._FUNCTIONS[key]

    @staticmethod
    def idle_commands() -> List[str]:
        # Required ordering from both reference manuals.
        return [":INITiate:CONTinuous OFF", ":ABORt"]

    @staticmethod
    def voltage_source_commands(voltage: float, voltage_limit: float) -> List[str]:
        """Build a standby-only V-source configuration block.

        The 100 V range is preferred whenever possible.  The programmed
        absolute limit is always enabled and must contain the requested level.
        """

        level = float(voltage)
        limit = float(voltage_limit)
        if not math.isfinite(level) or not -1000.0 <= level <= 1000.0:
            raise ValueError("Tensão da fonte deve estar entre -1000 e +1000 V.")
        if not math.isfinite(limit) or not 0.0 <= limit <= 1000.0:
            raise ValueError("Limite de tensão deve estar entre 0 e 1000 V.")
        if abs(level) > limit:
            raise ValueError(
                "O limite de tensão deve ser maior ou igual ao módulo da tensão desejada."
            )
        range_value = 100.0 if abs(level) <= 100.0 else 1000.0
        return [
            ":SOURce:VOLTage:RANGe {0:g}".format(range_value),
            ":SOURce:VOLTage:LIMit {0:.6E}".format(limit),
            ":SOURce:VOLTage:LIMit:STATe ON",
            ":SOURce:VOLTage {0:.6E}".format(level),
        ]

    @classmethod
    def measurement_commands(
        cls, config: MeasurementConfig, resistance_vsource_mode: str = "AUTO"
    ) -> List[str]:
        path = cls.function_path(config.function)
        commands = [":SENSe:FUNCtion '{0}'".format(path)]
        if path == "RESistance":
            mode = resistance_vsource_mode.strip().upper()
            if mode not in ("AUTO", "MAN"):
                raise ValueError("Fonte de resistência deve ser AUTO ou MAN.")
            commands.append(":SENSe:RESistance:VSC {0}".format(mode))
        if config.auto_range:
            commands.append(":SENSe:{0}:RANGe:AUTO ON".format(path))
        elif config.range_value is not None:
            commands.extend(
                [
                    ":SENSe:{0}:RANGe:AUTO OFF".format(path),
                    ":SENSe:{0}:RANGe:UPPer {1:.6E}".format(
                        path, config.range_value
                    ),
                ]
            )
        commands.append(":SENSe:{0}:NPLCycles {1:g}".format(path, config.nplc))
        if config.digits is not None:
            commands.append(":SENSe:{0}:DIGits {1}".format(path, config.digits))
        return commands

    @staticmethod
    def finite_trigger_commands(
        source: str, count: int, timer_interval: Optional[float], delay: Optional[float]
    ) -> List[str]:
        source_name = source.strip()
        valid_sources = {
            "IMMEDIATE",
            "IMM",
            "TIMER",
            "TIM",
            "MANUAL",
            "MAN",
            "BUS",
            "EXTERNAL",
            "EXT",
            "TLINK",
            "TLIN",
        }
        if source_name.upper() not in valid_sources:
            raise ValueError("Fonte de trigger não suportada: {0}".format(source))
        commands = [
            ":ARM:LAYer1:COUNt 1",
            ":ARM:LAYer1:SOURce IMMediate",
            ":ARM:LAYer2:COUNt 1",
            ":ARM:LAYer2:SOURce IMMediate",
            ":TRIGger:SOURce {0}".format(source_name),
            ":TRIGger:COUNt {0}".format(count),
        ]
        if source_name.upper().startswith("TIM") and timer_interval is not None:
            commands.append(":TRIGger:TIMer {0:.6f}".format(timer_interval))
        if delay is not None:
            commands.append(":TRIGger:DELay {0:.6f}".format(delay))
        return commands


class MeasurementRecipe:
    """Complete generic measurement configuration recipe."""

    name = "generic"
    physical_precondition = "Entrada conectada conforme a função selecionada."

    def build(
        self,
        profile: InstrumentProfile,
        config: MeasurementConfig,
        resistance_vsource_mode: str = "AUTO",
    ) -> List[str]:
        commands = SCPICommandBuilder.idle_commands()
        commands.append(profile.zero_check_on)
        commands.extend(
            SCPICommandBuilder.measurement_commands(
                config, resistance_vsource_mode=resistance_vsource_mode
            )
        )
        commands.append(profile.zero_check_off)
        return commands


class ChargeMeasurementRecipe(MeasurementRecipe):
    """6517 charge recipe including zero-check-hop compensation (REL)."""

    name = "charge"
    physical_precondition = (
        "Preparar o cabo com a entrada aberta; conectar o circuito somente depois "
        "da compensação do Zero Check Hop."
    )

    def build(
        self,
        profile: InstrumentProfile,
        config: MeasurementConfig,
        resistance_vsource_mode: str = "AUTO",
    ) -> List[str]:
        del resistance_vsource_mode
        commands = SCPICommandBuilder.idle_commands()
        commands.extend(
            [
                profile.zero_check_on,
                ":SENSe:CHARge:REFerence:STATe OFF",
            ]
        )
        commands.extend(SCPICommandBuilder.measurement_commands(config))
        commands.append(
            ":SENSe:CHARge:ADIScharge:STATe {0}".format(
                "ON" if config.charge_auto_discharge else "OFF"
            )
        )
        commands.extend(
            [
                profile.zero_check_off,
                # One deliberate one-shot conversion establishes the hop value.
                # Continuous acquisition never uses READ?.
                ":READ?",
                ":SENSe:CHARge:REFerence:ACQuire",
                ":SENSe:CHARge:REFerence:STATe ON",
            ]
        )
        return commands


@dataclass
class _WorkerRequest:
    function: Callable[["VisaWorker"], Any]
    future: Future


class VisaWorker:
    """The sole owner of all VISA objects, driven by one FIFO queue."""

    def __init__(
        self,
        resource_manager_factory: Optional[Callable[[], Any]] = None,
        thread_name: str = "KeithleyVisaWorker",
        send_ifc_before_open: bool = False,
    ) -> None:
        if resource_manager_factory is None:
            if pyvisa is None:
                resource_manager_factory = self._missing_pyvisa
            else:
                resource_manager_factory = pyvisa.ResourceManager
        self._resource_manager_factory = resource_manager_factory
        self._manager: Any = None
        self._instrument: Any = None
        self._resource_name: Optional[str] = None
        self._default_timeout_ms = DEFAULT_TIMEOUT_MS
        self._send_ifc_before_open = bool(send_ifc_before_open)
        self._requests: "queue.Queue[Optional[_WorkerRequest]]" = queue.Queue()
        self._accepting = True
        self._is_open = False
        self._state_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run, name=thread_name, daemon=True
        )
        self._thread.start()

    @staticmethod
    def _missing_pyvisa() -> Any:
        raise RuntimeError("PyVISA não está instalado.")

    @property
    def is_open(self) -> bool:
        with self._state_lock:
            return self._is_open

    @property
    def owner_thread_ident(self) -> Optional[int]:
        return self._thread.ident

    def _submit(self, function: Callable[["VisaWorker"], Any]) -> Any:
        with self._state_lock:
            if not self._accepting:
                raise RuntimeError("Worker VISA já foi encerrado.")
        future: Future = Future()
        self._requests.put(_WorkerRequest(function=function, future=future))
        return future.result()

    def _run(self) -> None:
        while True:
            request = self._requests.get()
            if request is None:
                return
            if not request.future.set_running_or_notify_cancel():
                continue
            try:
                request.future.set_result(request.function(self))
            except BaseException as error:
                request.future.set_exception(error)

    def _ensure_manager_local(self) -> Any:
        if self._manager is None:
            self._manager = self._resource_manager_factory()
            logging.info("Gerenciador VISA aberto no worker dedicado.")
        return self._manager

    def list_resources(self) -> Tuple[str, ...]:
        return tuple(
            self._submit(
                lambda worker: tuple(worker._ensure_manager_local().list_resources())
            )
        )

    def open_resource(
        self,
        resource_name: str,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        attempts: int = 3,
        retry_delay: float = 0.5,
    ) -> str:
        def operation(worker: "VisaWorker") -> str:
            worker._close_resource_local()
            manager = worker._ensure_manager_local()
            last_error: Optional[Exception] = None
            for attempt in range(1, attempts + 1):
                instrument: Any = None
                try:
                    if worker._send_ifc_before_open:
                        worker._reset_gpib_local(manager, resource_name)
                    instrument = manager.open_resource(resource_name)
                    instrument.timeout = timeout_ms
                    instrument.write_termination = "\n"
                    instrument.read_termination = "\n"
                    PROTOCOL_LOGGER.info(
                        "OPEN resource=%s timeout_ms=%s attempt=%s",
                        resource_name,
                        timeout_ms,
                        attempt,
                    )
                    PROTOCOL_LOGGER.info("QUERY> *IDN?")
                    identity = str(instrument.query("*IDN?")).strip()
                    PROTOCOL_LOGGER.info("RESPN< %s", identity)
                    worker._instrument = instrument
                    worker._resource_name = resource_name
                    worker._default_timeout_ms = timeout_ms
                    with worker._state_lock:
                        worker._is_open = True
                    logging.info(
                        "Instrumento aberto no worker: %s (tentativa %d)",
                        resource_name,
                        attempt,
                    )
                    return identity
                except Exception as error:
                    last_error = error
                    logging.warning(
                        "Tentativa VISA %d/%d falhou: %s", attempt, attempts, error
                    )
                    if instrument is not None:
                        try:
                            instrument.close()
                        except Exception:
                            logging.exception("Falha fechando tentativa VISA incompleta.")
                    if attempt < attempts and retry_delay > 0:
                        time.sleep(retry_delay)
            if last_error is None:
                raise RuntimeError("Não foi possível abrir o recurso VISA.")
            raise last_error

        return str(self._submit(operation))

    @staticmethod
    def _reset_gpib_local(manager: Any, resource_name: str) -> None:
        match = re.search(r"GPIB(\d+)", resource_name, re.IGNORECASE)
        if not match:
            return
        interface: Any = None
        try:
            interface = manager.open_resource("GPIB{0}::INTFC".format(match.group(1)))
            if hasattr(interface, "send_ifc"):
                interface.send_ifc()
        except Exception as error:
            logging.warning("IFC GPIB falhou e foi ignorado: %s", error)
        finally:
            if interface is not None:
                try:
                    interface.close()
                except Exception:
                    logging.exception("Falha fechando recurso GPIB INTFC.")

    def execute(self, function: Callable[[Any], Any]) -> Any:
        def operation(worker: "VisaWorker") -> Any:
            if worker._instrument is None:
                raise StateError("Instrumento desconectado.")
            return function(worker._instrument)

        return self._submit(operation)

    def close_resource(self) -> None:
        self._submit(lambda worker: worker._close_resource_local())

    def _close_resource_local(self) -> None:
        instrument = self._instrument
        resource_name = self._resource_name or "unknown"
        self._instrument = None
        self._resource_name = None
        with self._state_lock:
            self._is_open = False
        if instrument is not None:
            instrument.close()
            PROTOCOL_LOGGER.info("CLOSE resource=%s", resource_name)
            logging.info("Sessão VISA fechada pelo worker.")

    def shutdown(self) -> None:
        with self._state_lock:
            if not self._accepting:
                return

        def operation(worker: "VisaWorker") -> None:
            try:
                worker._close_resource_local()
            finally:
                if worker._manager is not None:
                    worker._manager.close()
                    worker._manager = None

        try:
            self._submit(operation)
        finally:
            with self._state_lock:
                self._accepting = False
            self._requests.put(None)
            self._thread.join(timeout=5.0)


def _instrument_write(instrument: Any, command: str) -> None:
    logging.debug("WRITE> %s", command)
    PROTOCOL_LOGGER.info("WRITE> %s", command)
    try:
        instrument.write(command)
    except BaseException:
        PROTOCOL_LOGGER.exception("WRITE ERROR> %s", command)
        raise


def _instrument_query(instrument: Any, command: str) -> str:
    logging.debug("QUERY> %s", command)
    PROTOCOL_LOGGER.info("QUERY> %s", command)
    try:
        response = str(instrument.query(command)).strip()
    except BaseException:
        PROTOCOL_LOGGER.exception("QUERY ERROR> %s", command)
        raise
    logging.debug("RESPN< %s", response)
    PROTOCOL_LOGGER.info("RESPN< %s", response)
    return response


def _instrument_query_with_timeout(
    instrument: Any, command: str, timeout_ms: int
) -> str:
    previous_timeout = getattr(instrument, "timeout", None)
    instrument.timeout = int(timeout_ms)
    try:
        return _instrument_query(instrument, command)
    finally:
        if previous_timeout is not None:
            instrument.timeout = previous_timeout


def _drain_errors(
    instrument: Any, profile: InstrumentProfile, maximum: int = MAX_ERRORS_PER_CHECK
) -> List[str]:
    errors: List[str] = []
    for _index in range(maximum):
        line = _instrument_query(instrument, profile.error_query)
        if not line:
            break
        code = line.split(",", 1)[0].strip()
        if code in ("0", "+0") or "NO ERROR" in line.upper():
            break
        errors.append(line)
    return errors


def _raise_instrument_errors(instrument: Any, profile: InstrumentProfile) -> None:
    errors = _drain_errors(instrument, profile)
    if errors:
        raise InstrumentCommandError(errors)


def _parse_scpi_bool(value: Any) -> bool:
    text = str(value).strip().upper()
    if text in ("1", "+1", "ON"):
        return True
    if text in ("0", "+0", "OFF"):
        return False
    try:
        return float(text) != 0.0
    except ValueError as error:
        raise KeithleyError("Resposta booleana SCPI inválida: {0!r}.".format(value)) from error


def _parse_element_list(raw: str) -> Tuple[str, ...]:
    return tuple(
        _canonical_element_name(token.strip().strip("'\""))
        for token in (raw or "").split(",")
        if token.strip()
    )


def _validate_format_contract(
    instrument: Any, profile: InstrumentProfile
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Read back FORMAT/TRACE and reject an unexpected response schema."""

    format_elements = _parse_element_list(
        _instrument_query(instrument, profile.format_elements_query)
    )
    trace_elements = _parse_element_list(
        _instrument_query(instrument, profile.trace_elements_query)
    )
    missing = [
        element for element in profile.response_elements if element not in format_elements
    ]
    if missing:
        raise KeithleyError(
            "FORMat:ELEMents? não confirmou o esquema esperado; ausentes: {0}. "
            "Resposta: {1}.".format(",".join(missing), ",".join(format_elements))
        )
    if "TSTAMP" not in trace_elements:
        raise KeithleyError(
            "TRACe:ELEMents? não confirmou TSTamp. Resposta: {0}.".format(
                ",".join(trace_elements)
            )
        )
    return format_elements, trace_elements


def _read_voltage_source_status(
    instrument: Any, profile: InstrumentProfile
) -> VoltageSourceStatus:
    interlock_raw = _instrument_query(instrument, profile.interlock_query)
    return VoltageSourceStatus(
        voltage=float(_instrument_query(instrument, ":SOURce:VOLTage?")),
        range_value=float(
            _instrument_query(instrument, ":SOURce:VOLTage:RANGe?")
        ),
        voltage_limit=float(
            _instrument_query(instrument, ":SOURce:VOLTage:LIMit?")
        ),
        limit_enabled=_parse_scpi_bool(
            _instrument_query(instrument, ":SOURce:VOLTage:LIMit:STATe?")
        ),
        output_enabled=_parse_scpi_bool(
            _instrument_query(instrument, ":OUTPut1:STATe?")
        ),
        interlock_ok=_parse_scpi_bool(interlock_raw),
        compliance=_parse_scpi_bool(
            _instrument_query(instrument, profile.compliance_query)
        ),
        interlock_raw=interlock_raw.strip(),
    )


def _read_manual_resistance_source_status(
    instrument: Any, profile: InstrumentProfile
) -> VoltageSourceStatus:
    """Read the resistance function's independent manual voltage source."""

    interlock_raw = _instrument_query(instrument, profile.interlock_query)
    range_value = float(
        _instrument_query(instrument, ":SENSe:RESistance:MANual:VSOurce:RANGe?")
    )
    return VoltageSourceStatus(
        voltage=float(
            _instrument_query(
                instrument, ":SENSe:RESistance:MANual:VSOurce:AMPLitude?"
            )
        ),
        range_value=range_value,
        voltage_limit=float(
            _instrument_query(instrument, ":SOURce:VOLTage:LIMit?")
        ),
        limit_enabled=_parse_scpi_bool(
            _instrument_query(instrument, ":SOURce:VOLTage:LIMit:STATe?")
        ),
        output_enabled=_parse_scpi_bool(
            _instrument_query(
                instrument, ":SENSe:RESistance:MANual:VSOurce:OPERate?"
            )
        ),
        interlock_ok=_parse_scpi_bool(interlock_raw),
        compliance=_parse_scpi_bool(
            _instrument_query(instrument, profile.compliance_query)
        ),
        interlock_raw=interlock_raw.strip(),
    )


class KeithleyController:
    """Application controller coordinating state, recipes and one VISA worker."""

    def __init__(
        self,
        resource_manager_factory: Optional[Callable[[], Any]] = None,
        worker: Optional[VisaWorker] = None,
    ) -> None:
        self._worker = worker or VisaWorker(resource_manager_factory)
        self._state_machine = InstrumentStateMachine()
        self._operation_lock = threading.RLock()
        self._profile: Optional[InstrumentProfile] = None
        self._identity = ""
        self._resource_name: Optional[str] = None
        self._default_timeout_ms = DEFAULT_TIMEOUT_MS
        self._configured_function = ""
        self._configured_resistance_vsource_mode = "AUTO"
        self._configured_voltage_limit: Optional[float] = None
        self._configured_nplc = 1.0
        self._buffer_points = 0
        self._last_buffer_compliance_final = False
        self._hv_enabled = False
        self._voltage_source_configured = False
        self._state_before_hv = ControllerState.SAFE
        self._closed = False

    @property
    def state(self) -> ControllerState:
        return self._state_machine.state

    @property
    def state_machine(self) -> InstrumentStateMachine:
        return self._state_machine

    @property
    def profile(self) -> Optional[InstrumentProfile]:
        return self._profile

    @property
    def identity(self) -> str:
        return self._identity

    @property
    def connected(self) -> bool:
        return (
            self._worker.is_open
            and self.state != ControllerState.DISCONNECTED
        )

    @property
    def resource_name(self) -> Optional[str]:
        return self._resource_name

    @property
    def hv_enabled(self) -> bool:
        return self._hv_enabled

    @property
    def last_buffer_compliance_final(self) -> bool:
        """Compliance observed after transfer; it is not a per-reading status."""

        return self._last_buffer_compliance_final

    @property
    def manager(self) -> None:
        """Compatibility guard: VISA managers are intentionally not exposed."""

        return None

    def _require_profile(self) -> InstrumentProfile:
        if self._profile is None:
            raise StateError("Nenhum perfil de instrumento foi detectado.")
        return self._profile

    def _mark_communication_error(self, error: BaseException) -> None:
        if not isinstance(error, (StateError, UnsafeCommandError, ValueError)):
            self._state_machine.force_error(str(error))

    def list_resources(self) -> Tuple[str, ...]:
        return self._worker.list_resources()

    def _ensure_manager(self) -> Any:
        raise StateError(
            "O ResourceManager não é exposto; use controller.list_resources()."
        )

    def connect(
        self,
        resource_name: str,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        expected_model: Optional[str] = None,
    ) -> str:
        with self._operation_lock:
            if self._closed:
                raise RuntimeError("Controlador já foi encerrado.")
            if self.state != ControllerState.DISCONNECTED:
                self.disconnect(silent=True)
            identity = self._worker.open_resource(resource_name, timeout_ms=timeout_ms)
            try:
                profile = detect_instrument_profile(identity)
            except Exception:
                self._worker.close_resource()
                raise
            expected = (expected_model or "AUTO").strip().upper().replace("MODEL", "")
            expected = expected.replace("KEITHLEY", "").strip()
            if expected not in ("", "AUTO", "AUTOMATICO", "AUTOMÁTICO"):
                if expected not in ("6517A", "6517B"):
                    self._worker.close_resource()
                    raise ValueError("Modelo esperado deve ser AUTO, 6517A ou 6517B.")
                if profile.model != expected:
                    self._worker.close_resource()
                    raise ModelMismatchError(
                        "Modelo esperado {0}, mas *IDN? detectou {1}. A sessão foi fechada.".format(
                            expected, profile.model
                        )
                    )
            self._profile = profile
            self._identity = identity
            self._resource_name = resource_name
            self._default_timeout_ms = timeout_ms
            self._state_machine.transition(
                ControllerState.CONNECTED, "*IDN? detectou " + profile.model
            )
            try:
                self._worker.execute(lambda inst: _instrument_write(inst, "*CLS"))
                self._safe_shutdown_transaction()
                self._state_machine.transition(
                    ControllerState.SAFE, "inicialização defensiva"
                )
            except BaseException as error:
                self._mark_communication_error(error)
                try:
                    self._worker.close_resource()
                finally:
                    self._state_machine.transition(
                        ControllerState.DISCONNECTED, "conexão falhou"
                    )
                raise
            return identity

    def _safe_shutdown_transaction(self) -> None:
        profile = self._require_profile()

        def operation(instrument: Any) -> None:
            instrument_function = ""
            try:
                instrument_function = _instrument_query(
                    instrument, ":SENSe:FUNCtion?"
                ).strip().upper()
            except Exception:
                # Some older firmware revisions do not expose the query;
                # retain the generic safe sequence in that case.
                logging.debug("Não foi possível consultar a função no shutdown.")
            resistance_function = "RES" in instrument_function
            commands = [profile.output_off, profile.manual_vsource_off]
            if not resistance_function:
                commands.append(":SOURce:VOLTage 0")
            else:
                try:
                    resistance_mode = _instrument_query(
                        instrument, ":SENSe:RESistance:VSC?"
                    ).strip().upper()
                except Exception:
                    resistance_mode = "AUTO"
                if resistance_mode.startswith("MAN"):
                    commands.append(
                        ":SENSe:RESistance:MANual:VSOurce:AMPLitude 0"
                    )
                    commands.append(":SOURce:VOLTage:LIMit:STATe OFF")
            commands.extend(SCPICommandBuilder.idle_commands())
            commands.extend(
                [":TRACe:FEED:CONTrol NEVer", profile.zero_check_on]
            )
            first_error: Optional[BaseException] = None
            for command in commands:
                try:
                    _instrument_write(instrument, command)
                except BaseException as error:
                    if first_error is None:
                        first_error = error
                    logging.exception("Comando de parada segura falhou: %s", command)
            try:
                _raise_instrument_errors(instrument, profile)
            except BaseException as error:
                if first_error is None:
                    first_error = error
            if first_error is not None:
                raise first_error

        self._worker.execute(operation)
        self._hv_enabled = False
        self._voltage_source_configured = False
        self._configured_voltage_limit = None

    def safe_shutdown(self) -> None:
        with self._operation_lock:
            self._state_machine.require(
                ControllerState.CONNECTED,
                ControllerState.SAFE,
                ControllerState.CONFIGURED,
                ControllerState.ARMED,
                ControllerState.ACQUIRING,
                ControllerState.HV_ENABLED,
                ControllerState.ERROR,
            )
            try:
                self._safe_shutdown_transaction()
                if self.state != ControllerState.SAFE:
                    self._state_machine.transition(
                        ControllerState.SAFE, "parada segura"
                    )
            except BaseException as error:
                self._mark_communication_error(error)
                raise

    def disconnect(self, silent: bool = False) -> None:
        del silent  # retained for compatibility with the original GUI
        with self._operation_lock:
            if self.state == ControllerState.DISCONNECTED:
                return
            try:
                if self._worker.is_open and self._profile is not None:
                    self._safe_shutdown_transaction()
            except Exception:
                logging.exception("Parada segura falhou durante desconexão.")
            finally:
                try:
                    self._worker.close_resource()
                finally:
                    self._profile = None
                    self._identity = ""
                    self._resource_name = None
                    self._hv_enabled = False
                    self._voltage_source_configured = False
                    self._state_machine.transition(
                        ControllerState.DISCONNECTED, "sessão VISA fechada"
                    )

    def shutdown(self) -> None:
        with self._operation_lock:
            if self._closed:
                return
            try:
                self.disconnect(silent=True)
            finally:
                self._worker.shutdown()
                self._closed = True

    def identify(self) -> str:
        return self.query("*IDN?")

    def options(self) -> str:
        return self.query("*OPT?")

    def clear_status(self) -> None:
        self.write("*CLS")
        if self.state == ControllerState.ERROR:
            self.safe_shutdown()

    def reset(self) -> None:
        with self._operation_lock:
            self._state_machine.require(
                ControllerState.SAFE, ControllerState.CONFIGURED
            )
            try:
                def operation(instrument: Any) -> None:
                    _instrument_write(instrument, "*RST")
                    _instrument_write(instrument, "*CLS")

                self._worker.execute(operation)
                self._safe_shutdown_transaction()
                if self.state != ControllerState.SAFE:
                    self._state_machine.transition(
                        ControllerState.SAFE, "reset defensivo"
                    )
                self._configured_function = ""
            except BaseException as error:
                self._mark_communication_error(error)
                raise

    def check_errors(self) -> List[str]:
        with self._operation_lock:
            self._state_machine.require(
                ControllerState.CONNECTED,
                ControllerState.SAFE,
                ControllerState.CONFIGURED,
                ControllerState.HV_ENABLED,
                ControllerState.ERROR,
            )
            profile = self._require_profile()
            try:
                return list(
                    self._worker.execute(
                        lambda inst: _drain_errors(inst, profile)
                    )
                )
            except BaseException as error:
                self._mark_communication_error(error)
                raise

    def configure_voltage_source(
        self, voltage: float, voltage_limit: float
    ) -> VoltageSourceStatus:
        """Configure the V-source while forcing its output to standby first."""

        expected_voltage = float(voltage)
        expected_limit = float(voltage_limit)
        if (
            not math.isfinite(expected_voltage)
            or not -1000.0 <= expected_voltage <= 1000.0
        ):
            raise ValueError("Tensão da fonte deve estar entre -1000 e +1000 V.")
        if (
            not math.isfinite(expected_limit)
            or not 0.0 <= expected_limit <= 1000.0
        ):
            raise ValueError("Limite de tensão deve estar entre 0 e 1000 V.")
        if abs(expected_voltage) > expected_limit:
            raise ValueError(
                "O limite de tensão deve ser maior ou igual ao módulo da tensão desejada."
            )
        expected_range = 100.0 if abs(expected_voltage) <= 100.0 else 1000.0
        resistance_manual = (
            self._configured_function == "RESistance"
            and self._configured_resistance_vsource_mode == "MAN"
        )
        resistance_auto = (
            self._configured_function == "RESistance"
            and self._configured_resistance_vsource_mode == "AUTO"
        )
        if resistance_auto:
            raise StateError(
                "A fonte da função Resistência está em AUTO. Selecione MAN "
                "na configuração de medição para controlar a tensão manualmente."
            )
        if resistance_manual:
            if not 0.0 <= expected_limit <= expected_range:
                raise ValueError(
                    "No modo MAN, o limite deve estar entre 0 e a faixa "
                    "selecionada ({0:g} V).".format(expected_range)
                )
            source_commands = [
                ":SENSe:RESistance:VSC MAN",
                ":SENSe:RESistance:MANual:VSOurce:OPERate OFF",
                ":SENSe:RESistance:MANual:VSOurce:RANGe {0:g}".format(
                    expected_range
                ),
                ":SOURce:VOLTage:LIMit {0:.6E}".format(expected_limit),
                ":SOURce:VOLTage:LIMit:STATe ON",
                ":SENSe:RESistance:MANual:VSOurce:AMPLitude {0:.6E}".format(
                    expected_voltage
                ),
            ]
        else:
            source_commands = SCPICommandBuilder.voltage_source_commands(
                expected_voltage, expected_limit
            )
        with self._operation_lock:
            self._state_machine.require(
                ControllerState.SAFE, ControllerState.CONFIGURED
            )
            profile = self._require_profile()
            try:
                def operation(instrument: Any) -> VoltageSourceStatus:
                    _instrument_write(instrument, profile.output_off)
                    if resistance_manual:
                        _instrument_write(
                            instrument,
                            ":SENSe:RESistance:MANual:VSOurce:OPERate OFF",
                        )
                    else:
                        # Clear a previous high-range level before selecting
                        # the generic 100 V range.
                        _instrument_write(instrument, ":SOURce:VOLTage 0")
                    for command in source_commands:
                        _instrument_write(instrument, command)
                    _raise_instrument_errors(instrument, profile)
                    return (
                        _read_manual_resistance_source_status(instrument, profile)
                        if resistance_manual
                        else _read_voltage_source_status(instrument, profile)
                    )

                status = self._worker.execute(operation)
                if status.output_enabled:
                    raise KeithleyError(
                        "A fonte permaneceu em operate após a configuração; "
                        "execute a parada segura."
                    )
                if not status.limit_enabled:
                    raise KeithleyError("O limite de tensão não foi habilitado.")
                if not math.isclose(
                    status.voltage, expected_voltage, rel_tol=1e-9, abs_tol=1e-6
                ):
                    raise KeithleyError(
                        "O nível lido ({0:g} V) difere do solicitado ({1:g} V).".format(
                            status.voltage, expected_voltage
                        )
                    )
                if not resistance_manual and not math.isclose(
                    status.voltage_limit,
                    expected_limit,
                    rel_tol=1e-9,
                    abs_tol=1e-6,
                ):
                    raise KeithleyError(
                        "O limite lido ({0:g} V) difere do solicitado ({1:g} V).".format(
                            status.voltage_limit, expected_limit
                        )
                    )
                if not math.isclose(
                    status.range_value, expected_range, rel_tol=0.0, abs_tol=1e-6
                ):
                    raise KeithleyError(
                        "A faixa lida ({0:g} V) difere da faixa segura esperada ({1:g} V).".format(
                            status.range_value, expected_range
                        )
                    )
                self._hv_enabled = False
                self._voltage_source_configured = True
                self._configured_voltage_limit = expected_limit
                return status
            except BaseException as error:
                self._voltage_source_configured = False
                self._configured_voltage_limit = None
                self._mark_communication_error(error)
                raise

    def enable_voltage_source(
        self, physical_interlock_confirmed: bool = False
    ) -> VoltageSourceStatus:
        """Enable the configured source after SCPI and physical checks.

        A truthy interlock query also occurs when the cable is absent. An
        explicit physical confirmation is therefore required in addition to
        the instrument response.
        """

        with self._operation_lock:
            self._state_machine.require(
                ControllerState.SAFE, ControllerState.CONFIGURED
            )
            if not self._voltage_source_configured:
                raise StateError(
                    "Aplique a tensão e o limite com a saída em standby antes de ativar."
                )
            if not physical_interlock_confirmed:
                raise UnsafeCommandError(
                    "A resposta SCPI do interlock é ambígua. Confirme fisicamente o cabo, "
                    "a fixture e a tampa antes de habilitar alta tensão."
                )
            profile = self._require_profile()
            try:
                def operation(instrument: Any) -> VoltageSourceStatus:
                    interlock = _instrument_query(
                        instrument, profile.interlock_query
                    )
                    if not _parse_scpi_bool(interlock):
                        raise UnsafeCommandError(
                            "Interlock/fixture não permite habilitar alta tensão."
                        )
                    before = (
                        _read_manual_resistance_source_status(instrument, profile)
                        if self._configured_function == "RESistance"
                        and self._configured_resistance_vsource_mode == "MAN"
                        else _read_voltage_source_status(instrument, profile)
                    )
                    if not before.limit_enabled:
                        raise UnsafeCommandError(
                            "O limite de tensão está desabilitado; reaplique a configuração."
                        )
                    configured_limit = (
                        self._configured_voltage_limit
                        if self._configured_voltage_limit is not None
                        and self._configured_function == "RESistance"
                        and self._configured_resistance_vsource_mode == "MAN"
                        else before.voltage_limit
                    )
                    if abs(before.voltage) > configured_limit + 1e-9:
                        raise UnsafeCommandError(
                            "A tensão programada excede o limite ativo."
                        )
                    _instrument_write(
                        instrument,
                        ":SENSe:RESistance:MANual:VSOurce:OPERate ON"
                        if self._configured_function == "RESistance"
                        and self._configured_resistance_vsource_mode == "MAN"
                        else profile.output_on,
                    )
                    _raise_instrument_errors(instrument, profile)
                    return (
                        _read_manual_resistance_source_status(instrument, profile)
                        if self._configured_function == "RESistance"
                        and self._configured_resistance_vsource_mode == "MAN"
                        else _read_voltage_source_status(instrument, profile)
                    )

                status = self._worker.execute(operation)
                if not status.output_enabled:
                    raise KeithleyError(
                        "O instrumento não confirmou a saída de alta tensão em operate."
                    )
                self._state_before_hv = self.state
                self._state_machine.transition(
                    ControllerState.HV_ENABLED, "fonte HV habilitada pela interface"
                )
                self._hv_enabled = True
                return status
            except BaseException as error:
                try:
                    def emergency_off(instrument: Any) -> None:
                        _instrument_write(instrument, profile.output_off)
                        if (
                            self._configured_function == "RESistance"
                            and self._configured_resistance_vsource_mode == "MAN"
                        ):
                            _instrument_write(
                                instrument,
                                ":SENSe:RESistance:MANual:VSOurce:OPERate OFF",
                            )
                        _instrument_write(instrument, ":SOURce:VOLTage 0")

                    self._worker.execute(emergency_off)
                except Exception:
                    logging.exception("Falha no desligamento HV compensatório.")
                self._hv_enabled = False
                self._voltage_source_configured = False
                self._mark_communication_error(error)
                raise

    def disable_voltage_source(self) -> VoltageSourceStatus:
        """Place the V-source in standby and clear its programmed amplitude."""

        with self._operation_lock:
            self._state_machine.require(
                ControllerState.CONNECTED,
                ControllerState.SAFE,
                ControllerState.CONFIGURED,
                ControllerState.ARMED,
                ControllerState.ACQUIRING,
                ControllerState.HV_ENABLED,
                ControllerState.ERROR,
            )
            profile = self._require_profile()
            try:
                def operation(instrument: Any) -> VoltageSourceStatus:
                    manual_resistance = (
                        self._configured_function == "RESistance"
                        and self._configured_resistance_vsource_mode == "MAN"
                    )
                    _instrument_write(instrument, profile.output_off)
                    _instrument_write(
                        instrument,
                        ":SENSe:RESistance:MANual:VSOurce:OPERate OFF"
                        if manual_resistance
                        else ":SOURce:VOLTage 0",
                    )
                    if manual_resistance:
                        _instrument_write(
                            instrument,
                            ":SENSe:RESistance:MANual:VSOurce:AMPLitude 0",
                        )
                        _instrument_write(
                            instrument, ":SOURce:VOLTage:LIMit:STATe OFF"
                        )
                    errors = _drain_errors(instrument, profile)
                    if errors:
                        logging.warning(
                            "Fila de erros após desligamento HV: %s", " | ".join(errors)
                        )
                    return (
                        _read_manual_resistance_source_status(instrument, profile)
                        if manual_resistance
                        else _read_voltage_source_status(instrument, profile)
                    )

                status = self._worker.execute(operation)
                if status.output_enabled:
                    raise KeithleyError(
                        "O instrumento ainda informa a fonte em operate."
                    )
                if not math.isclose(status.voltage, 0.0, abs_tol=1e-9):
                    raise KeithleyError(
                        "A saída está em standby, mas o nível programado não foi zerado."
                    )
                self._hv_enabled = False
                self._voltage_source_configured = False
                self._configured_voltage_limit = None
                if self.state == ControllerState.HV_ENABLED:
                    self._state_machine.transition(
                        self._state_before_hv, "fonte HV colocada em standby"
                    )
                return status
            except BaseException as error:
                self._hv_enabled = False
                self._voltage_source_configured = False
                self._configured_voltage_limit = None
                self._mark_communication_error(error)
                raise

    def get_voltage_source_status(self) -> VoltageSourceStatus:
        """Read V-source settings and synchronise the idle HV state."""

        with self._operation_lock:
            self._state_machine.require(
                ControllerState.SAFE,
                ControllerState.CONFIGURED,
                ControllerState.ARMED,
                ControllerState.ACQUIRING,
                ControllerState.HV_ENABLED,
                ControllerState.ERROR,
            )
            profile = self._require_profile()
            try:
                manual_resistance = (
                    self._configured_function == "RESistance"
                    and self._configured_resistance_vsource_mode == "MAN"
                )
                status = self._worker.execute(
                    lambda instrument: (
                        _read_manual_resistance_source_status(instrument, profile)
                        if manual_resistance
                        else _read_voltage_source_status(instrument, profile)
                    )
                )
                self._hv_enabled = status.output_enabled
                if status.output_enabled and self.state in (
                    ControllerState.SAFE,
                    ControllerState.CONFIGURED,
                ):
                    self._state_before_hv = self.state
                    self._state_machine.transition(
                        ControllerState.HV_ENABLED, "estado HV lido do instrumento"
                    )
                elif (
                    not status.output_enabled
                    and self.state == ControllerState.HV_ENABLED
                ):
                    self._state_machine.transition(
                        self._state_before_hv, "standby lido do instrumento"
                    )
                return status
            except BaseException as error:
                self._mark_communication_error(error)
                raise

    def _post_acquisition_state(self) -> ControllerState:
        return (
            ControllerState.HV_ENABLED
            if self._hv_enabled
            else ControllerState.CONFIGURED
        )

    def configure_measurement(
        self,
        function: str,
        range_value: Optional[float],
        auto_range: bool,
        nplc: float,
        digits: Optional[int],
        resistance_vsource_mode: str = "AUTO",
    ) -> None:
        if not 0.01 <= float(nplc) <= 10.0:
            raise ValueError("NPLCycles deve estar entre 0,01 e 10.")
        if digits is not None and not 4 <= int(digits) <= 7:
            raise ValueError("Dígitos deve estar entre 4 e 7.")
        if not auto_range and (range_value is None or range_value <= 0):
            raise ValueError("Faixa manual deve ser maior que zero.")
        config = MeasurementConfig(
            function=function,
            range_value=range_value,
            auto_range=bool(auto_range),
            nplc=float(nplc),
            digits=digits,
        )
        path = SCPICommandBuilder.function_path(function)
        maximum_range = {
            "VOLTage:DC": 210.0,
            "CURRent:DC": 21.0e-3,
            "RESistance": 100.0e18,
            "CHARge": 2.1e-6,
        }[path]
        if not auto_range and range_value is not None and range_value > maximum_range:
            raise ValueError(
                "Faixa manual excede o limite de {0:g} para {1}.".format(
                    maximum_range, path
                )
            )
        recipe: MeasurementRecipe
        if path == "CHARge":
            recipe = ChargeMeasurementRecipe()
        else:
            recipe = MeasurementRecipe()

        with self._operation_lock:
            self._state_machine.require(
                ControllerState.SAFE, ControllerState.CONFIGURED
            )
            profile = self._require_profile()
            resistance_mode = resistance_vsource_mode.strip().upper()
            commands = recipe.build(
                profile, config, resistance_vsource_mode=resistance_mode
            )
            try:
                def operation(instrument: Any) -> None:
                    for command in commands:
                        if command.endswith("?"):
                            _instrument_query(instrument, command)
                        else:
                            _instrument_write(instrument, command)
                    if path == "RESistance":
                        confirmed_mode = _instrument_query(
                            instrument, ":SENSe:RESistance:VSC?"
                        ).strip().upper()
                        if not confirmed_mode.startswith(resistance_mode[:3]):
                            raise KeithleyError(
                                "O instrumento não confirmou a fonte de resistência {0}: {1}.".format(
                                    resistance_mode, confirmed_mode
                                )
                            )
                    _raise_instrument_errors(instrument, profile)

                self._worker.execute(operation)
                self._configured_function = path
                self._configured_resistance_vsource_mode = resistance_mode
                self._configured_nplc = float(nplc)
                if self.state == ControllerState.SAFE:
                    self._state_machine.transition(
                        ControllerState.CONFIGURED, recipe.name + " recipe"
                    )
            except BaseException as error:
                self._mark_communication_error(error)
                raise

    def set_format_elements(self, elements: str = "") -> None:
        del elements  # the deterministic format always includes sample status
        with self._operation_lock:
            self._state_machine.require(ControllerState.CONFIGURED)
            profile = self._require_profile()
            try:
                def operation(instrument: Any) -> None:
                    _instrument_write(instrument, ":TRACe:FEED:CONTrol NEVer")
                    _instrument_write(instrument, ":TRACe:CLEar")
                    for command in profile.format_commands:
                        _instrument_write(instrument, command)
                    _instrument_write(instrument, profile.trace_element_command)
                    _validate_format_contract(instrument, profile)
                    _raise_instrument_errors(instrument, profile)

                self._worker.execute(operation)
            except BaseException as error:
                self._mark_communication_error(error)
                raise

    def _format_instrument(self, instrument: Any, profile: InstrumentProfile) -> None:
        for command in profile.format_commands:
            _instrument_write(instrument, command)
        _instrument_write(instrument, profile.trace_element_command)
        _validate_format_contract(instrument, profile)

    def _should_check_compliance(self) -> bool:
        return self._hv_enabled or self._configured_function == "RESistance"

    def one_shot_read(self) -> MeasurementReading:
        with self._operation_lock:
            self._state_machine.require(
                ControllerState.CONFIGURED, ControllerState.HV_ENABLED
            )
            profile = self._require_profile()
            self._state_machine.transition(ControllerState.ARMED, "one-shot armado")
            self._state_machine.transition(
                ControllerState.ACQUIRING, "one-shot iniciado"
            )
            try:
                def operation(instrument: Any) -> Tuple[str, bool]:
                    self._format_instrument(instrument, profile)
                    for command in SCPICommandBuilder.idle_commands():
                        _instrument_write(instrument, command)
                    response = _instrument_query(instrument, ":READ?")
                    compliance = False
                    if self._should_check_compliance():
                        compliance = _instrument_query(
                            instrument, profile.compliance_query
                        ).strip() in ("1", "+1", "ON")
                    _raise_instrument_errors(instrument, profile)
                    return response, compliance

                raw, compliance = self._worker.execute(operation)
                self._state_machine.transition(
                    self._post_acquisition_state(), "one-shot concluído"
                )
                return parse_reading_response(raw, compliance=compliance)
            except BaseException as error:
                self._mark_communication_error(error)
                raise

    def single_read(self) -> Tuple[str, str, float, float]:
        reading = self.one_shot_read()
        return (
            reading.raw_value,
            reading.raw_timestamp,
            reading.value,
            reading.timestamp,
        )

    def start_live(self) -> None:
        with self._operation_lock:
            self._state_machine.require(
                ControllerState.CONFIGURED, ControllerState.HV_ENABLED
            )
            profile = self._require_profile()
            self._state_machine.transition(ControllerState.ARMED, "live armado")
            try:
                def operation(instrument: Any) -> None:
                    self._format_instrument(instrument, profile)
                    for command in SCPICommandBuilder.idle_commands():
                        _instrument_write(instrument, command)
                    for command in SCPICommandBuilder.finite_trigger_commands(
                        "IMMediate", 1, None, 0.0
                    ):
                        _instrument_write(instrument, command)
                    # Complete all sequential configuration while continuous
                    # initiation is still OFF. Never place *OPC? after CONT ON.
                    _instrument_query(instrument, "*OPC?")
                    _instrument_write(instrument, ":INITiate:CONTinuous ON")
                    time.sleep(max(0.05, self._configured_nplc / 50.0 + 0.02))
                    # Do not use *OPC? here: continuous initiation never returns idle.
                    _raise_instrument_errors(instrument, profile)

                self._worker.execute(operation)
                self._state_machine.transition(
                    ControllerState.ACQUIRING, "live iniciado uma vez"
                )
            except BaseException as error:
                self._mark_communication_error(error)
                raise

    def read_live(self) -> MeasurementReading:
        with self._operation_lock:
            self._state_machine.require(ControllerState.ACQUIRING)
            profile = self._require_profile()
            try:
                def operation(instrument: Any) -> Tuple[str, bool]:
                    response = _instrument_query_with_timeout(
                        instrument, profile.fresh_query, profile.fresh_timeout_ms
                    )
                    compliance = False
                    if self._should_check_compliance():
                        compliance = _instrument_query(
                            instrument, profile.compliance_query
                        ).strip() in ("1", "+1", "ON")
                    return response, compliance

                raw, compliance = self._worker.execute(operation)
                return parse_reading_response(raw, compliance=compliance)
            except BaseException as error:
                self._mark_communication_error(error)
                raise

    def prepare_buffer(
        self,
        points: int,
        source: str = "TIMer",
        timer_interval: Optional[float] = None,
        delay: Optional[float] = None,
    ) -> None:
        points = int(points)
        if points < 1:
            raise ValueError("Número de pontos deve ser no mínimo 1.")
        if source.strip().upper() in ("TIMER", "TIM"):
            if timer_interval is None or not 0.001 <= timer_interval <= 99999.999:
                raise ValueError(
                    "Intervalo TIMer deve estar entre 0,001 e 99999,999 s."
                )
        if delay is not None and not 0.0 <= delay <= 999999.999:
            raise ValueError("Delay deve estar entre 0 e 999999,999 s.")
        with self._operation_lock:
            self._state_machine.require(
                ControllerState.CONFIGURED, ControllerState.HV_ENABLED
            )
            profile = self._require_profile()
            if points > profile.max_buffer_points_with_timestamp:
                raise ValueError(
                    "{0} com timestamp aceita no máximo {1} pontos de buffer.".format(
                        profile.model, profile.max_buffer_points_with_timestamp
                    )
                )
            try:
                def operation(instrument: Any) -> None:
                    for command in SCPICommandBuilder.idle_commands():
                        _instrument_write(instrument, command)
                    self._format_instrument(instrument, profile)
                    for command in (
                        ":TRACe:FEED:CONTrol NEVer",
                        ":TRACe:CLEar",
                        ":TRACe:POINts {0}".format(points),
                        ":TRACe:TSTamp:FORMat ABSolute",
                    ):
                        _instrument_write(instrument, command)
                    for command in SCPICommandBuilder.finite_trigger_commands(
                        source, points, timer_interval, delay
                    ):
                        _instrument_write(instrument, command)
                    # Officially supported buffer-control command.
                    _instrument_write(instrument, ":TRACe:FEED:CONTrol NEXT")
                    _raise_instrument_errors(instrument, profile)

                self._worker.execute(operation)
                self._buffer_points = points
                self._state_machine.transition(
                    ControllerState.ARMED, "buffer configurado e armado"
                )
            except BaseException as error:
                self._mark_communication_error(error)
                raise

    def start_buffer(self) -> None:
        with self._operation_lock:
            self._state_machine.require(ControllerState.ARMED)
            try:
                self._worker.execute(
                    lambda instrument: _instrument_write(instrument, ":INITiate")
                )
                self._state_machine.transition(
                    ControllerState.ACQUIRING, "buffer iniciado"
                )
            except BaseException as error:
                self._mark_communication_error(error)
                raise

    def wait_buffer_complete(
        self,
        timeout_s: float,
        stop_event: Optional[Any] = None,
        poll_interval_s: float = 0.1,
    ) -> int:
        if timeout_s <= 0:
            raise ValueError("Timeout do buffer deve ser positivo.")
        deadline = time.monotonic() + timeout_s
        poll_interval_s = max(0.02, min(float(poll_interval_s), 1.0))
        profile = self._require_profile()
        while True:
            if stop_event is not None and stop_event.is_set():
                raise AcquisitionCancelled("Aquisição de buffer cancelada.")
            with self._operation_lock:
                self._state_machine.require(ControllerState.ACQUIRING)
                try:
                    raw = self._worker.execute(
                        lambda instrument: _instrument_query(
                            instrument, profile.buffer_actual_query
                        )
                    )
                    actual = int(float(str(raw).split(",", 1)[0]))
                except BaseException as error:
                    self._mark_communication_error(error)
                    raise
            if actual >= self._buffer_points:
                return actual
            if time.monotonic() >= deadline:
                error = AcquisitionTimeout(
                    "Buffer recebeu {0}/{1} pontos antes do timeout de {2:.3f}s.".format(
                        actual, self._buffer_points, timeout_s
                    )
                )
                self._state_machine.force_error(str(error))
                raise error
            time.sleep(poll_interval_s)

    def read_buffer_readings(self) -> List[MeasurementReading]:
        with self._operation_lock:
            self._state_machine.require(ControllerState.ACQUIRING)
            profile = self._require_profile()
            try:
                def operation(instrument: Any) -> Tuple[str, bool]:
                    transfer_timeout_ms = max(
                        self._default_timeout_ms,
                        5000 + int(self._buffer_points / 1000.0 * 1000.0),
                    )
                    response = _instrument_query_with_timeout(
                        instrument,
                        profile.buffer_data_query,
                        transfer_timeout_ms,
                    )
                    compliance = False
                    if self._should_check_compliance():
                        compliance = _instrument_query(
                            instrument, profile.compliance_query
                        ).strip() in ("1", "+1", "ON")
                    _raise_instrument_errors(instrument, profile)
                    return response, compliance

                raw, compliance = self._worker.execute(operation)
                self._last_buffer_compliance_final = compliance
                readings = parse_buffer_response(
                    raw,
                    compliance=False,
                    elements=profile.response_elements,
                )
                self._state_machine.transition(
                    self._post_acquisition_state(), "buffer lido"
                )
                return readings
            except BaseException as error:
                self._mark_communication_error(error)
                raise

    def read_buffer_lines(self) -> List[Tuple[str, str, float, float]]:
        return [
            (
                reading.raw_value,
                reading.raw_timestamp,
                reading.value,
                reading.timestamp,
            )
            for reading in self.read_buffer_readings()
        ]

    def read_buffer_values(self) -> List[float]:
        values: List[float] = []
        for _raw_value, _raw_ts, value, timestamp in self.read_buffer_lines():
            values.extend((value, timestamp))
        return values

    def configure_buffer(self, points: int) -> None:
        # Compatibility method; high-level code should call prepare_buffer.
        self.prepare_buffer(points, source="IMMediate")

    def configure_trigger(
        self,
        source: str,
        count: int,
        timer_interval: Optional[float],
        delay: Optional[float],
    ) -> None:
        del count
        if self.state != ControllerState.ARMED:
            raise StateError("Configure o buffer antes do trigger.")
        # Re-arm atomically with the requested trigger source.
        with self._operation_lock:
            points = self._buffer_points
            profile = self._require_profile()
            try:
                def operation(instrument: Any) -> None:
                    for command in SCPICommandBuilder.idle_commands():
                        _instrument_write(instrument, command)
                    for command in SCPICommandBuilder.finite_trigger_commands(
                        source, points, timer_interval, delay
                    ):
                        _instrument_write(instrument, command)
                    _instrument_write(instrument, ":TRACe:FEED:CONTrol NEXT")
                    _raise_instrument_errors(instrument, profile)

                self._worker.execute(operation)
            except BaseException as error:
                self._mark_communication_error(error)
                raise

    def initiate(self) -> None:
        if self.state == ControllerState.ARMED:
            self.start_buffer()
        else:
            raise StateError("INITiate requer o estado Armed.")

    def opc_wait(self, timeout_ms: int) -> None:
        del timeout_ms
        raise StateError(
            "*OPC? não é usado em aquisição; use wait_buffer_complete()."
        )

    def abort(self) -> None:
        with self._operation_lock:
            if self.state == ControllerState.DISCONNECTED:
                return
            profile = self._require_profile()
            try:
                def operation(instrument: Any) -> None:
                    for command in SCPICommandBuilder.idle_commands():
                        _instrument_write(instrument, command)
                    _instrument_write(instrument, ":TRACe:FEED:CONTrol NEVer")
                    _raise_instrument_errors(instrument, profile)

                self._worker.execute(operation)
                if self.state in (
                    ControllerState.ARMED,
                    ControllerState.ACQUIRING,
                ):
                    self._state_machine.transition(
                        self._post_acquisition_state(), "abort determinístico"
                    )
            except BaseException as error:
                self._mark_communication_error(error)
                raise

    def execute_scpi_batch(
        self,
        commands: Sequence[str],
        check_errors: bool = True,
        allow_hv: bool = False,
        physical_interlock_confirmed: bool = False,
    ) -> List[Tuple[str, Optional[str], List[str]]]:
        requested_commands = [
            command.strip() for command in commands if command.strip()
        ]
        for command in requested_commands:
            units = _split_scpi_program(command)
            if len(units) != 1 or ";" in command:
                raise ValueError(
                    "O console seguro aceita uma única unidade SCPI por transação; "
                    "mensagens com ';' são bloqueadas."
                )
        clean_commands = _normalise_raw_abort_commands(requested_commands)
        findings = [
            finding
            for command in clean_commands
            for finding in analyze_scpi_safety(command)
        ]
        if findings and not allow_hv:
            raise UnsafeCommandError(
                "Autorização explícita necessária: "
                + " | ".join(finding.command for finding in findings)
            )
        if (
            any(finding.category == "HV_ENABLE" for finding in findings)
            and not physical_interlock_confirmed
        ):
            raise UnsafeCommandError(
                "A ativação de HV exige confirmação física do cabo, fixture e tampa; "
                "a consulta SCPI do interlock é ambígua."
            )
        with self._operation_lock:
            self._state_machine.require(
                ControllerState.CONNECTED,
                ControllerState.SAFE,
                ControllerState.CONFIGURED,
                ControllerState.HV_ENABLED,
                ControllerState.ERROR,
            )
            if findings and self.state == ControllerState.ERROR:
                raise StateError(
                    "Comandos de alta tensão são bloqueados no estado Error; "
                    "execute primeiro a parada segura."
                )
            profile = self._require_profile()
            try:
                def operation(
                    instrument: Any,
                ) -> List[Tuple[str, Optional[str], List[str]]]:
                    if any(finding.category == "HV_ENABLE" for finding in findings):
                        interlock = _instrument_query(
                            instrument, profile.interlock_query
                        ).strip()
                        if interlock not in ("1", "+1", "ON"):
                            raise UnsafeCommandError(
                                "Interlock/fixture não permite habilitar alta tensão."
                            )
                    results: List[Tuple[str, Optional[str], List[str]]] = []
                    for command in clean_commands:
                        response: Optional[str] = None
                        if _is_query_program_unit(command):
                            response = _instrument_query(instrument, command)
                        else:
                            _instrument_write(instrument, command)
                        errors = (
                            _drain_errors(instrument, profile)
                            if check_errors
                            else []
                        )
                        results.append((command, response, errors))
                    return results

                results = self._worker.execute(operation)
                reported_errors = [
                    error
                    for _command, _response, errors in results
                    for error in errors
                ]
                if reported_errors:
                    self._state_machine.force_error(
                        " | ".join(reported_errors)
                    )
                effect = _message_hv_effect(clean_commands)
                if effect is None and any(
                    finding.category == "HV_ENABLE" for finding in findings
                ):
                    # Conservatively track test-sequence arms and unusual
                    # model-specific enable paths as HV enabled.
                    effect = True
                if effect is True:
                    if self.state != ControllerState.HV_ENABLED:
                        self._state_before_hv = (
                            self.state
                            if self.state in (
                                ControllerState.SAFE,
                                ControllerState.CONFIGURED,
                            )
                            else ControllerState.SAFE
                        )
                        self._state_machine.transition(
                            ControllerState.HV_ENABLED, "SCPI autorizado"
                        )
                    self._hv_enabled = True
                elif effect is False:
                    self._hv_enabled = False
                    if self.state == ControllerState.HV_ENABLED:
                        self._state_machine.transition(
                            self._state_before_hv, "fonte HV desabilitada"
                        )
                return results
            except BaseException as error:
                self._mark_communication_error(error)
                raise

    def write(
        self,
        command: str,
        allow_hv: bool = False,
        physical_interlock_confirmed: bool = False,
    ) -> None:
        self.execute_scpi_batch(
            [command],
            check_errors=False,
            allow_hv=allow_hv,
            physical_interlock_confirmed=physical_interlock_confirmed,
        )

    def query(self, command: str, allow_hv: bool = False) -> str:
        result = self.execute_scpi_batch(
            [command], check_errors=False, allow_hv=allow_hv
        )
        response = result[0][1]
        return "" if response is None else response

    def query_ascii_floats(self, command: str) -> List[float]:
        raw = self.query(command)
        return [float(token) for token in raw.split(",") if token.strip()]


# Explicit architectural name, while retaining the public name used by the GUI.
KeithleyApplicationController = KeithleyController


__all__ = [
    "AcquisitionCancelled",
    "AcquisitionMode",
    "AcquisitionTimeout",
    "ChargeMeasurementRecipe",
    "ControllerState",
    "InstrumentProfile",
    "InterlockState",
    "InstrumentCommandError",
    "InstrumentStateMachine",
    "KeithleyError",
    "KeithleyApplicationController",
    "KeithleyController",
    "MeasurementConfig",
    "MeasurementReading",
    "ModelMismatchError",
    "VoltageSourceStatus",
    "PROFILE_6517A",
    "PROFILE_6517B",
    "ReadingStatus",
    "SCPICommandBuilder",
    "StateError",
    "UnsafeCommandError",
    "UnsupportedInstrumentError",
    "VisaWorker",
    "analyze_scpi_safety",
    "classify_reading",
    "detect_instrument_profile",
    "is_dangerous_command",
    "parse_buffer_response",
    "parse_reading_response",
]
