"""Immutable contracts shared by the application coordinator and visual UI.

This module deliberately has no Tk, VISA, filesystem or threading imports.  It
defines semantic intents emitted by the UI and snapshots rendered by it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from time import time
from typing import Any, Mapping, Optional, Tuple


class PageId(Enum):
    DASHBOARD = "dashboard"
    CONNECTION = "connection"
    MEASUREMENT = "measurement"
    ACQUISITION = "acquisition"
    HIGH_VOLTAGE = "high_voltage"
    SCPI = "scpi"
    LOGS = "logs"
    SETTINGS = "settings"


class IntentKind(Enum):
    NAVIGATE = "navigate"
    SET_THEME = "set_theme"
    SET_EXPECTED_MODEL = "set_expected_model"
    DISCOVER_RESOURCES = "discover_resources"
    CONNECT = "connect"
    DISCONNECT = "disconnect"
    REFRESH_IDENTITY = "refresh_identity"
    REFRESH_INSTRUMENT = "refresh_instrument"
    MONITOR_INSTRUMENT = "monitor_instrument"
    RELEASE_FRONT_PANEL = "release_front_panel"
    RESUME_MONITOR = "resume_monitor"
    EDIT_ADVANCED_DRAFT = "edit_advanced_draft"
    DISCARD_ADVANCED_DRAFT = "discard_advanced_draft"
    ADOPT_INSTRUMENT_VALUES = "adopt_instrument_values"
    APPLY_ADVANCED_CHANGES = "apply_advanced_changes"
    RESET_INSTRUMENT = "reset_instrument"
    ACQUIRE_ZERO_CORRECT = "acquire_zero_correct"
    ACQUIRE_REL = "acquire_rel"
    DISABLE_REL = "disable_rel"
    CONFIGURE_MEASUREMENT = "configure_measurement"
    ONE_SHOT = "one_shot"
    START_ACQUISITION = "start_acquisition"
    STOP_ACQUISITION = "stop_acquisition"
    CONFIGURE_HV = "configure_hv"
    ENABLE_HV = "enable_hv"
    DISABLE_HV = "disable_hv"
    REFRESH_HV = "refresh_hv"
    PREVIEW_SCPI = "preview_scpi"
    EXECUTE_SCPI = "execute_scpi"
    CLEAR_SCPI_OUTPUT = "clear_scpi_output"
    CLEAR_LOGS = "clear_logs"
    SHUTDOWN = "shutdown"


class LogLevel(Enum):
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True)
class AppIntent:
    kind: IntentKind
    payload: Mapping[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time)


@dataclass(frozen=True)
class LogEntry:
    timestamp: float
    level: LogLevel
    message: str


@dataclass(frozen=True)
class ReadingView:
    index: int
    timestamp: float
    value: float
    raw_value: str
    unit: str
    status: str


@dataclass(frozen=True)
class ScpiPreviewState:
    source_text: str = ""
    normalized_command: str = ""
    valid: bool = False
    is_query: bool = False
    risk: str = "NONE"
    summary: str = "Digite um comando para validar."
    manual_reference: str = ""
    confirmation_required: bool = False
    preview_digest: str = ""


@dataclass(frozen=True)
class InstrumentSnapshot:
    """Read-only view of parameters confirmed directly by the instrument."""

    revision: int = 0
    captured_at: float = 0.0
    model: str = ""
    resource_name: str = ""
    function: str = ""
    auto_range: Optional[bool] = None
    range_value: Optional[float] = None
    nplc: Optional[float] = None
    digits: Optional[int] = None
    aperture_s: Optional[float] = None
    zero_check: Optional[bool] = None
    zero_correct: Optional[bool] = None
    rel_enabled: Optional[bool] = None
    rel_value: Optional[float] = None
    average_enabled: Optional[bool] = None
    average_type: str = ""
    average_mode: str = ""
    average_count: Optional[int] = None
    advanced_noise_tolerance: Optional[float] = None
    median_enabled: Optional[bool] = None
    median_rank: Optional[int] = None
    line_sync: Optional[bool] = None
    source_sweep_points: Optional[float] = None
    repeat_count: Optional[float] = None
    source_measure_delay_s: Optional[float] = None
    hv_output_enabled: Optional[bool] = None
    query_errors: Tuple[Tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ViewState:
    revision: int = 0
    active_page: PageId = PageId.DASHBOARD
    theme: str = "Dark"
    sidebar_expanded: bool = True

    busy: bool = False
    busy_message: str = ""
    closing: bool = False
    error_banner: str = ""
    status_message: str = "Pronto"

    connected: bool = False
    connection_status: str = "Desconectado"
    panel_manual_mode: bool = False
    expected_model: str = "AUTO"
    detected_model: str = "—"
    resource_name: str = ""
    identity: str = ""
    serial_number: str = "—"
    firmware: str = "—"
    scpi_version: str = "—"
    controller_state: str = "Disconnected"
    available_resources: Tuple[str, ...] = ()

    measurement_function: str = "CURRent:DC"
    measurement_configured: bool = False
    instrument_snapshot: InstrumentSnapshot = field(default_factory=InstrumentSnapshot)
    sync_status: str = "Não sincronizado"
    last_instrument_read: float = 0.0
    manual_change_detected: bool = False
    draft_values: Tuple[Tuple[str, str], ...] = ()
    dirty_fields: Tuple[str, ...] = ()
    conflict_fields: Tuple[str, ...] = ()
    change_summary: str = "Nenhuma alteração local"
    resolution_summary: str = "—"
    accuracy_summary: str = "—"
    reading_value: Optional[float] = None
    reading_unit: str = "A"
    reading_status: str = "—"
    reading_timestamp: float = 0.0

    acquisition_running: bool = False
    acquisition_mode: str = "LIVE"
    acquisition_count: int = 0
    acquisition_target: int = 0
    acquisition_file: str = ""
    readings: Tuple[ReadingView, ...] = ()

    hv_active: bool = False
    hv_configured: bool = False
    hv_state: str = "Standby"
    hv_voltage: float = 0.0
    hv_range: float = 100.0
    hv_voltage_limit: float = 0.0
    hv_current_limit_ma: float = 10.0
    interlock_state: str = "Não consultado"
    compliance: bool = False

    scpi_preview: ScpiPreviewState = field(default_factory=ScpiPreviewState)
    scpi_output: Tuple[str, ...] = ()
    logs: Tuple[LogEntry, ...] = ()


__all__ = [
    "AppIntent",
    "InstrumentSnapshot",
    "IntentKind",
    "LogEntry",
    "LogLevel",
    "PageId",
    "ReadingView",
    "ScpiPreviewState",
    "ViewState",
]
