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
    "IntentKind",
    "LogEntry",
    "LogLevel",
    "PageId",
    "ReadingView",
    "ScpiPreviewState",
    "ViewState",
]
