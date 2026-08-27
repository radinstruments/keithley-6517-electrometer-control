"""Functional application coordinator for the CustomTkinter interface.

The coordinator owns asynchronous workflows, translates semantic UI intents
into controller calls and publishes immutable ``ViewState`` snapshots.  It has
no dependency on Tk or CustomTkinter.
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

try:
    from .keithley_6517_acquisition import (
        AcquisitionMetadata,
        AcquisitionRequest,
        AcquisitionRunner,
    )
    from .keithley_6517_contracts import (
        AppIntent,
        InstrumentSnapshot,
        IntentKind,
        LogEntry,
        LogLevel,
        PageId,
        ReadingView,
        ScpiPreviewState,
        ViewState,
    )
    from .keithley_6517_driver import (
        AcquisitionCancelled,
        ControllerState,
        KeithleyController,
        MeasurementReading,
        VoltageSourceStatus,
    )
    from .keithley_6517_profiles import capabilities_for_model
    from .keithley_6517_scpi import (
        ScpiRisk,
        issue_authorization,
        preflight_scpi,
    )
    from .keithley_6517_storage import (
        ProjectPaths,
        acquisition_path_for_name,
        default_acquisition_path,
        export_csv_to_xlsx,
        load_preferences,
        next_available_acquisition_path,
        save_preferences,
    )
except ImportError:  # pragma: no cover - direct src execution compatibility
    from keithley_6517_acquisition import (
        AcquisitionMetadata,
        AcquisitionRequest,
        AcquisitionRunner,
    )
    from keithley_6517_contracts import (
        AppIntent,
        InstrumentSnapshot,
        IntentKind,
        LogEntry,
        LogLevel,
        PageId,
        ReadingView,
        ScpiPreviewState,
        ViewState,
    )
    from keithley_6517_driver import (
        AcquisitionCancelled,
        ControllerState,
        KeithleyController,
        MeasurementReading,
        VoltageSourceStatus,
    )
    from keithley_6517_profiles import capabilities_for_model
    from keithley_6517_scpi import ScpiRisk, issue_authorization, preflight_scpi
    from keithley_6517_storage import (
        ProjectPaths,
        acquisition_path_for_name,
        default_acquisition_path,
        export_csv_to_xlsx,
        load_preferences,
        next_available_acquisition_path,
        save_preferences,
    )


class Keithley6517Application:
    """Coordinate UI intents, background work and deterministic view state."""

    MAX_LOG_ENTRIES = 500
    MAX_VISUAL_READINGS = 2000
    VISUAL_PUBLISH_INTERVAL_S = 0.05
    DRAFT_FIELDS = (
        "function",
        "auto_range",
        "range_value",
        "nplc",
        "digits",
        "source_sweep_points",
        "repeat_count",
        "source_measure_delay_s",
        "zero_check",
        "zero_correct",
        "rel_enabled",
        "rel_value",
        "average_enabled",
        "average_type",
        "average_mode",
        "average_count",
        "advanced_noise_tolerance",
        "median_enabled",
        "median_rank",
    )
    FIELD_LABELS = {
        "function": "função",
        "auto_range": "autorange",
        "range_value": "faixa manual",
        "nplc": "NPLC",
        "digits": "dígitos",
        "source_sweep_points": "pontos",
        "repeat_count": "repetição",
        "source_measure_delay_s": "atraso fonte→medição",
        "zero_check": "Zero Check",
        "zero_correct": "Zero Correct",
        "rel_enabled": "REL",
        "rel_value": "referência REL",
        "average_enabled": "filtro digital",
        "average_type": "tipo de média",
        "average_mode": "modo da média",
        "average_count": "leituras da média",
        "advanced_noise_tolerance": "janela de ruído",
        "median_enabled": "mediana",
        "median_rank": "rank da mediana",
    }

    def __init__(
        self,
        project_root: Path,
        controller: Optional[KeithleyController] = None,
    ) -> None:
        self.paths = ProjectPaths.from_root(project_root)
        self._auto_connect_enabled = controller is None
        self.controller = controller or KeithleyController()
        self._executor = ThreadPoolExecutor(
            max_workers=3, thread_name_prefix="KeithleyApplication"
        )
        self._lock = threading.RLock()
        self._states: "queue.Queue[ViewState]" = queue.Queue(maxsize=64)
        self._session_id = 0
        self._operation_id = 0
        self._last_visual_publish = 0.0
        self._visual_readings: Deque[ReadingView] = deque(
            maxlen=self.MAX_VISUAL_READINGS
        )
        self._draft_values: Dict[str, str] = {}
        self._draft_base_values: Dict[str, str] = {}
        self._dirty_fields: Set[str] = set()
        self._conflict_fields: Set[str] = set()
        self._monitor_in_flight = False
        self._acquisition = AcquisitionRunner(self.controller)
        preferences = load_preferences(self.paths)
        theme = str(preferences.get("theme", "Dark"))
        if theme not in ("Dark", "Light"):
            theme = "Dark"
        self._state = ViewState(
            theme=theme,
            sidebar_expanded=bool(preferences.get("sidebar_expanded", True)),
        )
        self._publish(self._state)
        if self._auto_connect_enabled:
            self._executor.submit(self._auto_connect_observer)

    @property
    def state(self) -> ViewState:
        with self._lock:
            return self._state

    def drain_states(self, maximum: int = 64) -> Tuple[ViewState, ...]:
        snapshots: List[ViewState] = []
        for _index in range(max(1, int(maximum))):
            try:
                snapshots.append(self._states.get_nowait())
            except queue.Empty:
                break
        return tuple(snapshots)

    def dispatch(self, intent: AppIntent) -> None:
        """Accept an intent quickly; blocking work is always submitted."""

        handlers: Dict[IntentKind, Callable[[AppIntent], None]] = {
            IntentKind.NAVIGATE: self._navigate,
            IntentKind.SET_THEME: self._set_theme,
            IntentKind.SET_EXPECTED_MODEL: self._set_expected_model,
            IntentKind.DISCOVER_RESOURCES: self._discover_resources,
            IntentKind.CONNECT: self._connect,
            IntentKind.DISCONNECT: self._disconnect,
            IntentKind.REFRESH_IDENTITY: self._refresh_identity,
            IntentKind.REFRESH_INSTRUMENT: self._refresh_instrument,
            IntentKind.MONITOR_INSTRUMENT: self._monitor_instrument,
            IntentKind.RELEASE_FRONT_PANEL: self._release_front_panel,
            IntentKind.RESUME_MONITOR: self._resume_monitor,
            IntentKind.EDIT_ADVANCED_DRAFT: self._edit_advanced_draft,
            IntentKind.DISCARD_ADVANCED_DRAFT: self._discard_advanced_draft,
            IntentKind.ADOPT_INSTRUMENT_VALUES: self._adopt_instrument_values,
            IntentKind.APPLY_ADVANCED_CHANGES: self._apply_advanced_changes,
            IntentKind.RESET_INSTRUMENT: self._reset_instrument,
            IntentKind.ACQUIRE_ZERO_CORRECT: self._acquire_zero_correct,
            IntentKind.ACQUIRE_REL: self._acquire_rel,
            IntentKind.DISABLE_REL: self._disable_rel,
            IntentKind.CONFIGURE_MEASUREMENT: self._configure_measurement,
            IntentKind.ONE_SHOT: self._one_shot,
            IntentKind.START_ACQUISITION: self._start_acquisition,
            IntentKind.STOP_ACQUISITION: self._stop_acquisition,
            IntentKind.CONFIGURE_HV: self._configure_hv,
            IntentKind.ENABLE_HV: self._enable_hv,
            IntentKind.DISABLE_HV: self._disable_hv,
            IntentKind.REFRESH_HV: self._refresh_hv,
            IntentKind.PREVIEW_SCPI: self._preview_scpi,
            IntentKind.EXECUTE_SCPI: self._execute_scpi,
            IntentKind.CLEAR_SCPI_OUTPUT: self._clear_scpi_output,
            IntentKind.CLEAR_LOGS: self._clear_logs,
            IntentKind.SHUTDOWN: self._shutdown,
        }
        handler = handlers.get(intent.kind)
        if handler is None:
            self._log(LogLevel.ERROR, "Intent não reconhecida: {0}".format(intent.kind))
            return
        try:
            handler(intent)
        except Exception as error:
            self._operation_failed(intent.kind.value, error)

    def finalize(self) -> None:
        """Release the executor after the graphical mainloop has exited."""

        self._acquisition.stop()
        self._executor.shutdown(wait=False)

    def _publish(self, state: ViewState) -> None:
        try:
            self._states.put_nowait(state)
        except queue.Full:
            try:
                self._states.get_nowait()
            except queue.Empty:
                pass
            self._states.put_nowait(state)

    def _update(self, publish: bool = True, **changes: Any) -> ViewState:
        with self._lock:
            revision = self._state.revision + 1
            self._state = replace(self._state, revision=revision, **changes)
            snapshot = self._state
        if publish:
            self._publish(snapshot)
        return snapshot

    def _log(self, level: LogLevel, message: str) -> None:
        entry = LogEntry(time.time(), level, message)
        with self._lock:
            entries = (self._state.logs + (entry,))[-self.MAX_LOG_ENTRIES :]
        self._update(logs=entries)
        log_method = {
            LogLevel.ERROR: logging.error,
            LogLevel.WARNING: logging.warning,
            LogLevel.SUCCESS: logging.info,
            LogLevel.INFO: logging.info,
        }[level]
        log_method(message)

    def _begin(self, message: str) -> int:
        with self._lock:
            self._operation_id += 1
            operation_id = self._operation_id
        self._update(
            busy=True,
            busy_message=message,
            error_banner="",
            status_message=message,
            panel_manual_mode=False,
        )
        return operation_id

    def _finish(self, message: str) -> None:
        self._update(busy=False, busy_message="", status_message=message)

    def _operation_failed(self, context: str, error: BaseException) -> None:
        message = "{0}: {1}".format(context, error)
        self._update(
            busy=False,
            busy_message="",
            error_banner=message,
            status_message="Falha",
            controller_state=self.controller.state.value,
            connected=self.controller.connected,
        )
        self._log(LogLevel.ERROR, message)

    def _submit(self, context: str, function: Callable[[], None]) -> None:
        def guarded() -> None:
            try:
                function()
            except BaseException as error:
                self._operation_failed(context, error)

        self._executor.submit(guarded)

    @staticmethod
    def _value_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, float):
            if math.isinf(value):
                return "∞"
            return "{0:.12g}".format(value)
        return str(value)

    def _snapshot_field_values(
        self, snapshot: InstrumentSnapshot
    ) -> Dict[str, str]:
        return {
            field: self._value_text(getattr(snapshot, field))
            for field in self.DRAFT_FIELDS
        }

    def _draft_summary(self) -> str:
        if not self._dirty_fields:
            return "Nenhuma alteração local"
        labels = [
            self.FIELD_LABELS.get(field, field)
            for field in sorted(self._dirty_fields)
        ]
        return "Alterado: " + ", ".join(labels)

    @staticmethod
    def _resolution_and_accuracy(
        snapshot: InstrumentSnapshot,
    ) -> Tuple[str, str]:
        if snapshot.range_value is None:
            return "Faixa ainda não confirmada", "Especificação indisponível"
        range_value = snapshot.range_value
        function = snapshot.function
        digits = snapshot.digits
        resolution = "Faixa efetiva {0:.6g}".format(range_value)
        accuracy = (
            "A exatidão depende de zeragem, NPLC, filtros, temperatura, "
            "acomodação e calibração."
        )
        if function == "VOLTage:DC":
            voltage_rows = {
                2.0: (1e-6, "±(0,025% da leitura + 40 µV)"),
                20.0: (10e-6, "±(0,025% da leitura + 300 µV)"),
                200.0: (100e-6, "±(0,06% da leitura + 3 mV)"),
            }
            nearest = min(voltage_rows, key=lambda item: abs(item - range_value))
            nominal, specification = voltage_rows[nearest]
            resolution = (
                "Faixa {0:g} V · resolução nominal {1:.3g} V · {2} dígitos"
            ).format(nearest, nominal, digits if digits is not None else "—")
            if snapshot.model == "6517B":
                accuracy = specification + " (1 ano, 18–28 °C; confirme condições)"
            else:
                accuracy = (
                    "Resolução nominal exibida; confirme a tabela de exatidão "
                    "específica do 6517A/firmware."
                )
        elif function == "CURRent:DC" and snapshot.model == "6517B":
            rows = (
                (20e-12, 10e-18, "±(1% + 3 fA)"),
                (200e-12, 100e-18, "±(1% + 5 fA)"),
                (2e-9, 1e-15, "±(0,2% + 300 fA)"),
                (20e-9, 10e-15, "±(0,2% + 500 fA)"),
                (200e-9, 100e-15, "±(0,2% + 5 pA)"),
                (2e-6, 1e-12, "±(0,1% + 100 pA)"),
                (20e-6, 10e-12, "±(0,1% + 500 pA)"),
                (200e-6, 100e-12, "±(0,1% + 5 nA)"),
                (2e-3, 1e-9, "±(0,1% + 100 nA)"),
                (20e-3, 10e-9, "±(0,1% + 500 nA)"),
            )
            selected = min(rows, key=lambda row: abs(row[0] - range_value))
            resolution = "Faixa {0:.3g} A · resolução {1:.3g} A".format(
                selected[0], selected[1]
            )
            accuracy = selected[2] + (
                " (1 ano; 6½ dígitos, 1 PLC, mediana e média de 10; "
                "confirme todas as condições)"
            )
        return resolution, accuracy

    def _apply_instrument_snapshot(
        self,
        snapshot: InstrumentSnapshot,
        adopt_all: bool = False,
        force_adopt_fields: Tuple[str, ...] = (),
    ) -> None:
        new_values = self._snapshot_field_values(snapshot)
        previous_snapshot = self._state.instrument_snapshot
        previous_values = self._snapshot_field_values(previous_snapshot)
        manual_change = False
        if adopt_all or previous_snapshot.revision == 0:
            self._draft_values = dict(new_values)
            self._draft_base_values.clear()
            self._dirty_fields.clear()
            self._conflict_fields.clear()
        else:
            for field in self.DRAFT_FIELDS:
                if field in force_adopt_fields:
                    self._draft_values[field] = new_values[field]
                    self._draft_base_values.pop(field, None)
                    self._dirty_fields.discard(field)
                    self._conflict_fields.discard(field)
                    continue
                ignore_autorange_range = (
                    field == "range_value" and snapshot.auto_range is True
                )
                changed = previous_values.get(field, "") != new_values[field]
                if ignore_autorange_range:
                    changed = False
                manual_change = manual_change or changed
                if field not in self._dirty_fields:
                    self._draft_values[field] = new_values[field]
                    continue
                if ignore_autorange_range:
                    continue
                if self._draft_values.get(field, "") == new_values[field]:
                    self._dirty_fields.discard(field)
                    self._conflict_fields.discard(field)
                    self._draft_base_values.pop(field, None)
                elif new_values[field] != self._draft_base_values.get(
                    field, previous_values.get(field, "")
                ):
                    self._conflict_fields.add(field)

        model = snapshot.model or self._state.detected_model
        unit = self._state.reading_unit
        if model and snapshot.function:
            unit = capabilities_for_model(model).measurement(snapshot.function).unit
        resolution, accuracy = self._resolution_and_accuracy(snapshot)
        sync_status = (
            "Leitura parcial"
            if snapshot.query_errors
            else ("Conflito" if self._conflict_fields else "Sincronizado")
        )
        manual_change_flag = (
            False
            if adopt_all
            else (manual_change or self._state.manual_change_detected)
        )
        self._update(
            instrument_snapshot=snapshot,
            sync_status=sync_status,
            last_instrument_read=snapshot.captured_at,
            manual_change_detected=manual_change_flag,
            draft_values=tuple(sorted(self._draft_values.items())),
            dirty_fields=tuple(sorted(self._dirty_fields)),
            conflict_fields=tuple(sorted(self._conflict_fields)),
            change_summary=self._draft_summary(),
            resolution_summary=resolution,
            accuracy_summary=accuracy,
            measurement_function=snapshot.function or self._state.measurement_function,
            measurement_configured=bool(
                snapshot.function
                and snapshot.nplc is not None
                and snapshot.hv_output_enabled is not None
            ),
            reading_unit=unit,
            hv_active=bool(snapshot.hv_output_enabled),
            hv_state="ATIVA" if snapshot.hv_output_enabled else "Standby",
            controller_state=self.controller.state.value,
        )

    def _auto_connect_observer(self) -> None:
        """Adopt the only available VISA resource at startup, without writes."""

        time.sleep(0.15)
        if self.state.connected or self.state.closing:
            return
        try:
            resources = tuple(self.controller.list_resources())
            self._update(available_resources=resources)
            preferred_resource = "GPIB0::27::INSTR"
            if preferred_resource in resources and not self.state.connected:
                self._connect(
                    AppIntent(IntentKind.CONNECT, {"resource": preferred_resource})
                )
            elif len(resources) == 1 and not self.state.connected:
                self._connect(
                    AppIntent(IntentKind.CONNECT, {"resource": resources[0]})
                )
            elif resources:
                self._update(
                    status_message="Selecione um dos recursos VISA encontrados"
                )
        except Exception as error:
            self._update(
                status_message="Descoberta VISA automática: {0}".format(error)
            )

    def _edit_advanced_draft(self, intent: AppIntent) -> None:
        field = str(intent.payload.get("field", ""))
        if field not in self.DRAFT_FIELDS:
            raise ValueError("Campo avançado desconhecido: {0}".format(field))
        value = str(intent.payload.get("value", "")).strip()
        confirmed = self._snapshot_field_values(
            self._state.instrument_snapshot
        ).get(field, "")
        self._draft_values.setdefault(field, confirmed)
        self._draft_values[field] = value
        if value == confirmed:
            self._dirty_fields.discard(field)
            self._conflict_fields.discard(field)
            self._draft_base_values.pop(field, None)
        else:
            if field not in self._dirty_fields:
                self._draft_base_values[field] = confirmed
            self._dirty_fields.add(field)
        self._update(
            draft_values=tuple(sorted(self._draft_values.items())),
            dirty_fields=tuple(sorted(self._dirty_fields)),
            conflict_fields=tuple(sorted(self._conflict_fields)),
            change_summary=self._draft_summary(),
        )

    def _discard_advanced_draft(self, _intent: AppIntent) -> None:
        self._apply_instrument_snapshot(
            self._state.instrument_snapshot, adopt_all=True
        )
        self._log(LogLevel.INFO, "Alterações locais descartadas.")

    def _adopt_instrument_values(self, _intent: AppIntent) -> None:
        self._begin("Lendo e adotando valores do instrumento…")

        def work() -> None:
            snapshot = self.controller.read_instrument_snapshot()
            self._apply_instrument_snapshot(snapshot, adopt_all=True)
            self._finish("Valores atuais do instrumento adotados")

        self._submit("Adotar instrumento", work)

    @staticmethod
    def _draft_bool(value: str) -> bool:
        text = str(value).strip().upper()
        if text in ("1", "ON", "TRUE", "SIM"):
            return True
        if text in ("0", "OFF", "FALSE", "NÃO", "NAO"):
            return False
        raise ValueError("Valor liga/desliga inválido: {0}".format(value))

    def _typed_draft_changes(self) -> Dict[str, Any]:
        changes: Dict[str, Any] = {}
        bool_fields = {
            "auto_range",
            "zero_check",
            "zero_correct",
            "rel_enabled",
            "average_enabled",
            "median_enabled",
        }
        int_fields = {
            "digits",
            "source_sweep_points",
            "repeat_count",
            "average_count",
            "median_rank",
        }
        float_fields = {
            "range_value",
            "nplc",
            "source_measure_delay_s",
            "rel_value",
            "advanced_noise_tolerance",
        }
        for field in self._dirty_fields:
            raw = self._draft_values.get(field, "")
            if field in bool_fields:
                changes[field] = self._draft_bool(raw)
            elif field in int_fields:
                changes[field] = int(float(raw.replace(",", ".")))
            elif field in float_fields:
                changes[field] = self._number(raw, self.FIELD_LABELS.get(field, field))
            else:
                changes[field] = raw
        if changes.get("auto_range") is True:
            changes.pop("range_value", None)
        return changes

    def _apply_advanced_changes(self, _intent: AppIntent) -> None:
        changes = self._typed_draft_changes()
        if not changes:
            self._finish("Nenhuma alteração para aplicar")
            return
        self._begin("Aplicando somente os campos modificados…")

        def work() -> None:
            snapshot = self.controller.apply_advanced_changes(changes)
            self._apply_instrument_snapshot(snapshot, adopt_all=True)
            self._finish("Alterações confirmadas pelo instrumento")
            self._log(
                LogLevel.SUCCESS,
                "Delta confirmado: {0}".format(
                    ", ".join(self.FIELD_LABELS.get(key, key) for key in changes)
                ),
            )

        self._submit("Aplicar controle avançado", work)

    def _reset_instrument(self, _intent: AppIntent) -> None:
        if not self._state.connected:
            raise RuntimeError("Conecte o instrumento antes de resetar os parâmetros.")
        if self._state.acquisition_running:
            raise RuntimeError("Pare a aquisição antes de resetar os parâmetros.")
        if self._state.hv_active:
            raise RuntimeError("Desligue a alta tensão antes de resetar os parâmetros.")
        if self._state.controller_state not in ("Safe", "Configured"):
            raise RuntimeError(
                "O reset só pode ser executado com o instrumento em estado seguro."
            )
        self._begin("Restaurando parâmetros padrão com *RST…")

        def work() -> None:
            self.controller.reset()
            snapshot = self.controller.read_instrument_snapshot()
            self._apply_instrument_snapshot(snapshot, adopt_all=True)
            self._update(hv_configured=False)
            self._finish("Parâmetros padrão restaurados")
            self._log(
                LogLevel.WARNING,
                "*RST enviado; parâmetros do instrumento restaurados e estado seguro reaplicado.",
            )

        self._submit("Reset do instrumento", work)

    def _refresh_instrument(self, _intent: AppIntent) -> None:
        self._begin("Consultando instrumento sem escrever…")

        def work() -> None:
            snapshot = self.controller.read_instrument_snapshot()
            self._apply_instrument_snapshot(snapshot)
            self._finish("Instrumento lido em modo somente leitura")

        self._submit("Leitura do instrumento", work)

    def _monitor_instrument(self, _intent: AppIntent) -> None:
        """Refresh the read-only monitor without changing instrument state."""

        with self._lock:
            if (
                self._monitor_in_flight
                or not self._state.connected
                or self._state.busy
                or self._state.acquisition_running
                or self._state.panel_manual_mode
                or self._state.closing
            ):
                return
            self._monitor_in_flight = True

        def work() -> None:
            try:
                with self._lock:
                    if (
                        self._state.acquisition_running
                        or self._state.panel_manual_mode
                        or self._state.closing
                    ):
                        return
                snapshot = self.controller.read_instrument_snapshot()
                self._apply_instrument_snapshot(snapshot)
            except BaseException as error:
                self._log(
                    LogLevel.WARNING,
                    "Monitor: não foi possível atualizar os parâmetros: {0}".format(
                        error
                    ),
                )
            finally:
                with self._lock:
                    self._monitor_in_flight = False

        self._submit("Monitor do instrumento", work)

    def _release_front_panel(self, _intent: AppIntent) -> None:
        if self._state.acquisition_running:
            raise RuntimeError(
                "Libere o painel depois que a aquisição terminar ou for parada."
            )
        self._begin("Liberando o painel físico…")

        def work() -> None:
            if not self.controller.release_front_panel():
                raise RuntimeError(
                    "Não foi possível liberar o painel; confirme a conexão GPIB."
                )
            self._finish("Painel físico liberado para operação manual")
            self._log(
                LogLevel.SUCCESS,
                "Painel frontal liberado; nenhuma configuração foi alterada.",
            )

        self._update(panel_manual_mode=True)
        self._submit("Liberar painel", work)

    def _resume_monitor(self, _intent: AppIntent) -> None:
        if not self._state.connected:
            raise RuntimeError("Conecte o instrumento antes de retomar o monitor.")
        if self._state.acquisition_running:
            raise RuntimeError("Retome o monitor depois que a aquisiÃ§Ã£o terminar.")
        self._begin("Retomando monitor do instrumentoâ€¦")

        def work() -> None:
            snapshot = self.controller.read_instrument_snapshot()
            # Values changed from the physical panel are authoritative when
            # the operator explicitly resumes monitoring.
            self._apply_instrument_snapshot(snapshot, adopt_all=True)
            self._finish("Monitor do instrumento retomado")

        self._submit("Retomar monitor", work)

    def _acquire_zero_correct(self, _intent: AppIntent) -> None:
        self._begin("Adquirindo Zero Correct…")

        def work() -> None:
            snapshot = self.controller.acquire_zero_correct()
            self._apply_instrument_snapshot(
                snapshot, force_adopt_fields=("zero_correct",)
            )
            self._finish("Zero Correct adquirido e confirmado")

        self._submit("Zero Correct", work)

    def _acquire_rel(self, _intent: AppIntent) -> None:
        self._begin("Adquirindo referência REL…")

        def work() -> None:
            snapshot = self.controller.acquire_rel()
            self._apply_instrument_snapshot(
                snapshot, force_adopt_fields=("rel_enabled", "rel_value")
            )
            self._finish("Referência REL adquirida e confirmada")

        self._submit("REL", work)

    def _disable_rel(self, _intent: AppIntent) -> None:
        self._begin("Desativando REL…")

        def work() -> None:
            snapshot = self.controller.disable_rel()
            self._apply_instrument_snapshot(
                snapshot, force_adopt_fields=("rel_enabled",)
            )
            self._finish("REL desativado e confirmado")

        self._submit("REL", work)

    def _navigate(self, intent: AppIntent) -> None:
        raw = intent.payload.get("page", PageId.DASHBOARD)
        page = raw if isinstance(raw, PageId) else PageId(str(raw))
        self._update(active_page=page)

    def _set_theme(self, intent: AppIntent) -> None:
        theme = str(intent.payload.get("theme", "Dark")).title()
        if theme not in ("Dark", "Light"):
            raise ValueError("Tema deve ser Dark ou Light.")
        self._update(theme=theme)
        self._executor.submit(
            save_preferences,
            self.paths,
            {"theme": theme, "sidebar_expanded": self._state.sidebar_expanded},
        )

    def set_sidebar_expanded(self, expanded: bool) -> None:
        self._update(sidebar_expanded=bool(expanded))
        self._executor.submit(
            save_preferences,
            self.paths,
            {"theme": self._state.theme, "sidebar_expanded": bool(expanded)},
        )

    def _set_expected_model(self, intent: AppIntent) -> None:
        if self.controller.connected:
            raise RuntimeError("Desconecte antes de alterar o modelo esperado.")
        model = str(intent.payload.get("model", "AUTO")).upper().strip()
        if model not in ("AUTO", "6517A", "6517B"):
            raise ValueError("Modelo esperado deve ser AUTO, 6517A ou 6517B.")
        self._update(expected_model=model)

    def _discover_resources(self, _intent: AppIntent) -> None:
        self._begin("Procurando recursos VISA…")

        def work() -> None:
            resources = tuple(self.controller.list_resources())
            self._update(available_resources=resources)
            self._finish("{0} recurso(s) VISA encontrado(s)".format(len(resources)))
            self._log(LogLevel.SUCCESS, "Busca VISA concluída: {0}".format(resources or "nenhum"))

        self._submit("Busca VISA", work)

    def _connect(self, intent: AppIntent) -> None:
        resource = str(intent.payload.get("resource", "")).strip()
        if not resource:
            raise ValueError("Informe um recurso VISA.")
        expected = self._state.expected_model
        self._begin("Conectando a {0}…".format(resource))

        def work() -> None:
            identity = self.controller.connect(
                resource,
                expected_model=None if expected == "AUTO" else expected,
            )
            with self._lock:
                self._session_id += 1
            model = self.controller.profile.model if self.controller.profile else "—"
            serial, firmware = self._identity_metadata(identity)
            scpi_version = self.controller.profile.scpi_version if self.controller.profile else "—"
            try:
                reported = self.controller.query(":SYSTem:VERSion?").strip()
                if reported:
                    scpi_version = reported
            except Exception as error:
                self._log(LogLevel.WARNING, "Não foi possível consultar SYST:VERS?: {0}".format(error))
            self._update(
                connected=True,
                panel_manual_mode=False,
                connection_status="Conectado",
                detected_model=model,
                resource_name=resource,
                identity=identity,
                serial_number=serial,
                firmware=firmware,
                scpi_version=scpi_version,
                controller_state=self.controller.state.value,
                measurement_configured=False,
                sync_status="Lendo instrumento…",
                hv_active=False,
                hv_configured=False,
                hv_state="Standby",
                interlock_state="Não consultado",
            )
            snapshot = self.controller.read_instrument_snapshot()
            self._apply_instrument_snapshot(snapshot, adopt_all=True)
            if snapshot.hv_output_enabled:
                status = self.controller.get_voltage_source_status()
                self._apply_hv_status(status, configured=False)
            self._finish("Conectado em modo observador a {0}".format(model))
            self._log(
                LogLevel.SUCCESS,
                "Conectado sem escrita; estado do painel adotado: {0}".format(
                    identity
                ),
            )

        self._submit("Conexão", work)

    def _disconnect(self, _intent: AppIntent) -> None:
        if self._state.acquisition_running:
            raise RuntimeError("Pare a aquisição antes de desconectar.")
        self._acquisition.stop()
        self._begin("Encerrando sessão VISA…")

        def work() -> None:
            self.controller.disconnect()
            self._visual_readings.clear()
            self._draft_values.clear()
            self._draft_base_values.clear()
            self._dirty_fields.clear()
            self._conflict_fields.clear()
            self._update(
                connected=False,
                panel_manual_mode=False,
                connection_status="Desconectado",
                detected_model="—",
                resource_name="",
                identity="",
                serial_number="—",
                firmware="—",
                scpi_version="—",
                controller_state=ControllerState.DISCONNECTED.value,
                measurement_configured=False,
                instrument_snapshot=InstrumentSnapshot(),
                sync_status="Não sincronizado",
                last_instrument_read=0.0,
                manual_change_detected=False,
                draft_values=(),
                dirty_fields=(),
                conflict_fields=(),
                change_summary="Nenhuma alteração local",
                acquisition_running=False,
                readings=(),
                hv_active=False,
                hv_configured=False,
                hv_state="Standby",
                interlock_state="Não consultado",
                compliance=False,
            )
            self._finish("Desconectado")
            self._log(LogLevel.INFO, "Sessão VISA encerrada.")

        self._submit("Desconexão", work)

    def _refresh_identity(self, _intent: AppIntent) -> None:
        self._begin("Atualizando identidade…")

        def work() -> None:
            identity = self.controller.identify()
            serial, firmware = self._identity_metadata(identity)
            self._update(identity=identity, serial_number=serial, firmware=firmware)
            self._finish("Identidade atualizada")

        self._submit("Identidade", work)

    def _configure_measurement(self, intent: AppIntent) -> None:
        payload = intent.payload
        function = str(payload.get("function", "CURRent:DC"))
        auto_range = bool(payload.get("auto_range", True))
        range_value = None
        if not auto_range:
            range_value = self._number(payload.get("range_value"), "Faixa")
        nplc = self._number(payload.get("nplc", 1.0), "NPLC")
        digits = int(payload.get("digits", 6))
        changes: Dict[str, Any] = {
            "function": function,
            "auto_range": auto_range,
            "nplc": nplc,
            "digits": digits,
        }
        if range_value is not None:
            changes["range_value"] = range_value
        self._begin("Aplicando configuração de medição…")

        def work() -> None:
            snapshot = self.controller.apply_advanced_changes(changes)
            self._apply_instrument_snapshot(snapshot, adopt_all=True)
            self._finish("Configuração confirmada pelo instrumento")
            self._log(LogLevel.SUCCESS, "Medição configurada: {0}, NPLC {1:g}".format(function, nplc))

        self._submit("Configuração de medição", work)

    def _one_shot(self, _intent: AppIntent) -> None:
        self._begin("Obtendo leitura única…")

        def work() -> None:
            reading = self.controller.one_shot_read()
            self._update_from_reading(1, reading, force_publish=True)
            snapshot = self.controller.read_instrument_snapshot()
            self._apply_instrument_snapshot(snapshot)
            self._update(controller_state=self.controller.state.value)
            # A one-shot only borrows the instrument briefly. Keep the
            # passive monitor running afterwards so manual panel changes are
            # reflected in the UI and in the next automatic file name.
            self._update(panel_manual_mode=False)
            self._finish("Leitura concluída")

        self._submit("Leitura única", work)

    def _start_acquisition(self, intent: AppIntent) -> None:
        if self._state.acquisition_running:
            raise RuntimeError("Já existe uma aquisição em andamento.")
        if self._state.busy:
            raise RuntimeError("Aguarde a atualização do monitor terminar.")
        payload = intent.payload
        mode = str(payload.get("mode", "LIVE")).upper()
        interval = self._number(payload.get("interval", 0.1), "Intervalo")
        if not 0.001 <= interval <= 99999.999:
            raise ValueError("O intervalo deve estar entre 0.001 e 99999.999 segundos.")
        if "duration" in payload:
            duration = self._number(payload.get("duration"), "Tempo de leitura")
            if duration <= 0:
                raise ValueError("O tempo de leitura deve ser positivo.")
            points = max(1, int(math.ceil(duration / interval)))
            timeout = max(60.0, duration + 30.0)
        else:
            # Backward compatibility for programmatic callers using points.
            points = int(payload.get("points", 100))
        timeout = self._number(payload.get("timeout", 60.0), "Timeout")
        raw_path = str(payload.get("path", "")).strip()
        if raw_path and Path(raw_path).is_absolute():
            # Keep the programmatic API compatible with callers that provide
            # a fully qualified output path (the UI supplies only a filename).
            path = Path(raw_path).resolve()
        elif raw_path:
            path = acquisition_path_for_name(self.paths, raw_path, mode=mode)
        else:
            path = default_acquisition_path(self.paths, mode=mode)
        # Never overwrite an existing CSV. Repeated manual names receive a
        # numeric suffix as well, so a new acquisition is not interrupted.
        path = next_available_acquisition_path(path)
        model = self.controller.profile.model if self.controller.profile else ""
        capabilities = capabilities_for_model(model)
        if points > capabilities.max_buffer_points_with_timestamp and mode == "BUFFER":
            raise ValueError(
                "{0} aceita no máximo {1} pontos com timestamp.".format(
                    model, capabilities.max_buffer_points_with_timestamp
                )
            )
        request = AcquisitionRequest(mode, path, points, interval, timeout)
        self._visual_readings.clear()
        self._update(
            acquisition_running=True,
            panel_manual_mode=False,
            acquisition_mode=mode,
            acquisition_count=0,
            acquisition_target=points,
            acquisition_file=str(path),
            readings=(),
            busy=False,
            status_message="Aquisição em andamento",
            error_banner="",
        )
        self._log(LogLevel.INFO, "Aquisição {0} iniciada: {1}".format(mode, path))

        def work() -> None:
            def refresh_panel_snapshot() -> None:
                try:
                    confirmed = self.controller.read_instrument_snapshot()
                except Exception as error:
                    self._log(
                        LogLevel.WARNING,
                        "Não foi possível reler o painel após a aquisição: {0}".format(
                            error
                        ),
                    )
                    return
                self._apply_instrument_snapshot(confirmed, adopt_all=True)

            try:
                # The instrument is authoritative.  Re-read it immediately
                # before acquisition so a last-second front-panel change is
                # adopted without writing any measurement parameter back.
                snapshot = self.controller.read_instrument_snapshot()
                self._apply_instrument_snapshot(snapshot, adopt_all=True)
                if snapshot.zero_check is True:
                    raise RuntimeError(
                        "Zero Check ligado. Desligue-o no painel do eletrometro antes de iniciar a aquisi\u00e7\u00e3o."
                    )
                if not snapshot.function or snapshot.nplc is None:
                    raise RuntimeError(
                        "O 6517 não confirmou uma configuração de medição completa."
                    )
                confirmed_unit = capabilities_for_model(model).measurement(
                    snapshot.function
                ).unit
                metadata = AcquisitionMetadata(
                    model=model,
                    serial=self._state.serial_number,
                    firmware=self._state.firmware,
                    unit=confirmed_unit,
                )
                self._log(
                    LogLevel.INFO,
                    "Configuração do painel confirmada para aquisição: "
                    "{0}, NPLC {1:g}.".format(snapshot.function, snapshot.nplc),
                )
                count = self._acquisition.run(
                    request,
                    metadata,
                    self._on_acquisition_reading,
                    self._on_acquisition_progress,
                )
            except AcquisitionCancelled:
                refresh_panel_snapshot()
                self._update(
                    acquisition_running=False,
                    panel_manual_mode=False,
                    controller_state=self.controller.state.value,
                    readings=tuple(self._visual_readings),
                    status_message="Aquisição cancelada",
                )
                self._log(LogLevel.WARNING, "Aquisição cancelada pelo operador.")
                return
            except BaseException as error:
                refresh_panel_snapshot()
                self._update(
                    acquisition_running=False,
                    panel_manual_mode=False,
                    controller_state=self.controller.state.value,
                    readings=tuple(self._visual_readings),
                    status_message="Falha na aquisição",
                    error_banner="Aquisição: {0}".format(error),
                )
                self._log(LogLevel.ERROR, "Aquisição: {0}".format(error))
                return
            refresh_panel_snapshot()
            export_status = "Aquisi\u00e7\u00e3o conclu\u00edda; CSV salvo"
            try:
                xlsx_path = next_available_acquisition_path(
                    request.output_path.with_suffix(".xlsx")
                )
                export_csv_to_xlsx(request.output_path, xlsx_path)
                export_status = "Aquisi\u00e7\u00e3o conclu\u00edda; CSV e XLSX salvos"
                self._log(
                    LogLevel.SUCCESS,
                    "C\u00f3pia XLSX salva automaticamente: {0}".format(xlsx_path),
                )
            except BaseException as export_error:
                self._log(
                    LogLevel.WARNING,
                    "N\u00e3o foi poss\u00edvel criar a c\u00f3pia XLSX; o CSV foi preservado: {0}".format(
                        export_error
                    ),
                )
            self._update(
                acquisition_running=False,
                panel_manual_mode=False,
                acquisition_count=count,
                controller_state=self.controller.state.value,
                readings=tuple(self._visual_readings),
                status_message=export_status,
            )
            self._log(LogLevel.SUCCESS, "Aquisição concluída com {0} amostras.".format(count))

        self._submit("Aquisição", work)

    def _stop_acquisition(self, _intent: AppIntent) -> None:
        self._acquisition.stop()
        self._update(status_message="Cancelamento solicitado…")
        self._log(LogLevel.WARNING, "Cancelamento da aquisição solicitado.")

    def _on_acquisition_reading(self, index: int, reading: MeasurementReading) -> None:
        self._update_from_reading(index, reading, force_publish=False)

    def _on_acquisition_progress(self, current: int, target: int) -> None:
        now = time.monotonic()
        publish = now - self._last_visual_publish >= self.VISUAL_PUBLISH_INTERVAL_S
        if publish:
            self._last_visual_publish = now
        self._update(
            publish=publish,
            acquisition_count=current,
            acquisition_target=target,
            readings=tuple(self._visual_readings) if publish else self._state.readings,
        )

    def _update_from_reading(
        self, index: int, reading: MeasurementReading, force_publish: bool
    ) -> None:
        item = ReadingView(
            index=index,
            timestamp=reading.timestamp,
            value=reading.value,
            raw_value=reading.raw_value,
            unit=self._state.reading_unit,
            status=reading.status.value,
        )
        self._visual_readings.append(item)
        now = time.monotonic()
        publish = force_publish or (
            now - self._last_visual_publish >= self.VISUAL_PUBLISH_INTERVAL_S
        )
        if publish:
            self._last_visual_publish = now
        self._update(
            publish=publish,
            reading_value=reading.value,
            reading_status=reading.status.value,
            reading_timestamp=reading.timestamp,
            readings=tuple(self._visual_readings) if publish else self._state.readings,
        )

    def _configure_hv(self, intent: AppIntent) -> None:
        voltage = self._number(intent.payload.get("voltage"), "Tensão")
        limit = self._number(intent.payload.get("limit"), "Limite")
        self._begin("Configurando fonte em standby…")

        def work() -> None:
            status = self.controller.configure_voltage_source(voltage, limit)
            self._apply_hv_status(status, configured=True)
            self._finish("Fonte configurada em standby")
            self._log(LogLevel.SUCCESS, "Fonte configurada: {0:g} V, limite {1:g} V".format(voltage, limit))

        self._submit("Configuração HV", work)

    def _enable_hv(self, intent: AppIntent) -> None:
        physical = bool(intent.payload.get("physical_confirmed", False))
        if not physical:
            raise ValueError("Confirme fisicamente cabo, fixture, tampa e circuito.")
        self._begin("Habilitando alta tensão…")

        def work() -> None:
            status = self.controller.enable_voltage_source(
                physical_interlock_confirmed=True
            )
            self._apply_hv_status(status, configured=True)
            self._finish("ALTA TENSÃO ATIVA")
            self._log(LogLevel.WARNING, "Fonte de alta tensão habilitada.")

        self._submit("Ativação HV", work)

    def _disable_hv(self, _intent: AppIntent) -> None:
        self._acquisition.stop()
        self._update(status_message="DESLIGAMENTO HV SOLICITADO", busy=True, busy_message="Desligando HV…")

        def work() -> None:
            status = self.controller.disable_voltage_source()
            self._apply_hv_status(status, configured=False)
            self._finish("Fonte em standby e nível zerado")
            self._log(LogLevel.SUCCESS, "Fonte HV colocada em standby e zerada.")

        self._submit("Desligamento HV", work)

    def _refresh_hv(self, _intent: AppIntent) -> None:
        self._begin("Consultando estado da fonte…")

        def work() -> None:
            status = self.controller.get_voltage_source_status()
            self._apply_hv_status(status, configured=self._state.hv_configured)
            self._finish("Estado da fonte atualizado")

        self._submit("Estado HV", work)

    def _apply_hv_status(self, status: VoltageSourceStatus, configured: bool) -> None:
        interlock = (
            "Fechado OU cabo ausente indeterminado"
            if status.interlock_ok
            else "Aberto/bloqueado"
        )
        self._update(
            hv_active=status.output_enabled,
            hv_configured=configured,
            hv_state="ATIVA" if status.output_enabled else "Standby",
            hv_voltage=status.voltage,
            hv_range=status.range_value,
            hv_voltage_limit=status.voltage_limit,
            hv_current_limit_ma=status.nominal_current_limit_a * 1000.0,
            interlock_state=interlock,
            compliance=status.compliance,
            controller_state=self.controller.state.value,
        )

    def _preview_scpi(self, intent: AppIntent) -> None:
        command = str(intent.payload.get("command", ""))
        model = self._state.detected_model
        result = preflight_scpi(command, model, self._session_id)
        preview = ScpiPreviewState(
            source_text=result.source_text,
            normalized_command=result.normalized_command,
            valid=result.valid,
            is_query=result.is_query,
            risk=result.risk.value,
            summary=result.error or result.summary,
            manual_reference=result.manual_reference,
            confirmation_required=result.confirmation_required,
            preview_digest=result.digest,
        )
        self._update(scpi_preview=preview, status_message="Pré-análise SCPI concluída")

    def _execute_scpi(self, intent: AppIntent) -> None:
        command = str(intent.payload.get("command", ""))
        confirmed = bool(intent.payload.get("confirmed", False))
        physical = bool(intent.payload.get("physical_confirmed", False))
        model = self._state.detected_model
        preview = preflight_scpi(command, model, self._session_id)
        if not preview.valid:
            raise ValueError(preview.error or "Comando SCPI rejeitado.")
        if preview.confirmation_required:
            if not confirmed:
                raise ValueError("Confirme o comando exatamente como foi pré-analisado.")
            token = issue_authorization(preview, model, self._session_id)
            if not token.valid_for(preview, model, self._session_id):
                raise RuntimeError("Token SCPI expirou ou não corresponde ao comando.")
        if preview.risk == ScpiRisk.HV_ENABLE and not physical:
            raise ValueError("Ativação HV exige confirmação física além do token SCPI.")
        self._begin("Executando transação SCPI validada…")

        def work() -> None:
            results = self.controller.execute_scpi_batch(
                [preview.normalized_command],
                check_errors=True,
                allow_hv=preview.risk in (ScpiRisk.HV_CONFIG, ScpiRisk.HV_ENABLE),
                physical_interlock_confirmed=physical,
            )
            lines = list(self._state.scpi_output)
            for sent, response, errors in results:
                lines.append("> " + sent)
                if response is not None:
                    lines.append("< " + response)
                for error in errors:
                    lines.append("! " + error)
            self._update(
                scpi_output=tuple(lines[-500:]),
                controller_state=self.controller.state.value,
                hv_active=self.controller.hv_enabled,
            )
            if not preview.is_query:
                snapshot = self.controller.read_instrument_snapshot()
                self._apply_instrument_snapshot(snapshot)
            self._finish("Transação SCPI concluída")
            self._log(LogLevel.INFO, "SCPI validado executado: {0}".format(preview.normalized_command))

        self._submit("Console SCPI", work)

    def _clear_scpi_output(self, _intent: AppIntent) -> None:
        self._update(scpi_output=())

    def _clear_logs(self, _intent: AppIntent) -> None:
        self._update(logs=())

    def _shutdown(self, _intent: AppIntent) -> None:
        if self._state.closing:
            return
        self._acquisition.stop()
        self._update(closing=True, busy=True, busy_message="Executando parada segura…")

        def work() -> None:
            self.controller.shutdown()
            self._update(
                connected=False,
                connection_status="Encerrado",
                controller_state=ControllerState.DISCONNECTED.value,
                acquisition_running=False,
                hv_active=False,
                hv_configured=False,
                hv_state="Standby",
                busy=False,
                busy_message="",
                status_message="Encerramento seguro concluído",
            )

        self._submit("Encerramento", work)

    @staticmethod
    def _identity_metadata(identity: str) -> Tuple[str, str]:
        fields = [field.strip() for field in (identity or "").split(",")]
        serial = fields[2] if len(fields) > 2 and fields[2] else "—"
        firmware = fields[3] if len(fields) > 3 and fields[3] else "—"
        return serial, firmware

    @staticmethod
    def _number(value: Any, field: str) -> float:
        text = str(value).strip().replace(",", ".")
        if not text:
            raise ValueError("{0} é obrigatório.".format(field))
        try:
            return float(text)
        except ValueError as error:
            raise ValueError("{0} deve ser numérico.".format(field)) from error


__all__ = ["Keithley6517Application"]
