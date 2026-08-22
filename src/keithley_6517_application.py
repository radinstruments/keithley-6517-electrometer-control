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
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

try:
    from .keithley_6517_acquisition import (
        AcquisitionMetadata,
        AcquisitionRequest,
        AcquisitionRunner,
    )
    from .keithley_6517_contracts import (
        AppIntent,
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
        load_preferences,
        next_available_acquisition_path,
        save_preferences,
    )


class Keithley6517Application:
    """Coordinate UI intents, background work and deterministic view state."""

    MAX_LOG_ENTRIES = 500
    MAX_VISUAL_READINGS = 2000
    VISUAL_PUBLISH_INTERVAL_S = 0.05

    def __init__(
        self,
        project_root: Path,
        controller: Optional[KeithleyController] = None,
    ) -> None:
        self.paths = ProjectPaths.from_root(project_root)
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
        self._update(busy=True, busy_message=message, error_banner="", status_message=message)
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
                connection_status="Conectado",
                detected_model=model,
                resource_name=resource,
                identity=identity,
                serial_number=serial,
                firmware=firmware,
                scpi_version=scpi_version,
                controller_state=self.controller.state.value,
                measurement_configured=False,
                hv_active=False,
                hv_configured=False,
                hv_state="Standby",
                interlock_state="Não consultado",
            )
            self._finish("Conectado com segurança a {0}".format(model))
            self._log(LogLevel.SUCCESS, "Conectado: {0}".format(identity))

        self._submit("Conexão", work)

    def _disconnect(self, _intent: AppIntent) -> None:
        if self._state.acquisition_running:
            raise RuntimeError("Pare a aquisição antes de desconectar.")
        self._acquisition.stop()
        self._begin("Desconectando com parada segura…")

        def work() -> None:
            self.controller.disconnect()
            self._visual_readings.clear()
            self._update(
                connected=False,
                connection_status="Desconectado",
                detected_model="—",
                resource_name="",
                identity="",
                serial_number="—",
                firmware="—",
                scpi_version="—",
                controller_state=ControllerState.DISCONNECTED.value,
                measurement_configured=False,
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
        resistance_mode = str(payload.get("resistance_vsource_mode", "AUTO"))
        self._begin("Aplicando configuração de medição…")

        def work() -> None:
            self.controller.configure_measurement(
                function,
                range_value,
                auto_range,
                nplc,
                digits,
                resistance_vsource_mode=resistance_mode,
            )
            self.controller.set_format_elements()
            model = self.controller.profile.model if self.controller.profile else "6517A"
            capability = capabilities_for_model(model).measurement(function)
            self._update(
                measurement_function=function,
                measurement_configured=True,
                reading_unit=capability.unit,
                controller_state=self.controller.state.value,
            )
            self._finish("Configuração confirmada pelo instrumento")
            self._log(LogLevel.SUCCESS, "Medição configurada: {0}, NPLC {1:g}".format(function, nplc))

        self._submit("Configuração de medição", work)

    def _one_shot(self, _intent: AppIntent) -> None:
        self._begin("Obtendo leitura única…")

        def work() -> None:
            reading = self.controller.one_shot_read()
            self._update_from_reading(1, reading, force_publish=True)
            self._update(controller_state=self.controller.state.value)
            self._finish("Leitura concluída")

        self._submit("Leitura única", work)

    def _start_acquisition(self, intent: AppIntent) -> None:
        if self._state.acquisition_running:
            raise RuntimeError("Já existe uma aquisição em andamento.")
        if self.controller.hv_enabled or self._state.hv_active:
            raise RuntimeError(
                "Aquisição bloqueada enquanto a fonte de alta tensão está ativa. "
                "Coloque a fonte em standby antes de adquirir."
            )
        payload = intent.payload
        mode = str(payload.get("mode", "LIVE")).upper()
        interval = self._number(payload.get("interval", 0.1), "Intervalo")
        if "duration" in payload:
            duration = self._number(payload.get("duration"), "Tempo de leitura")
            if duration <= 0:
                raise ValueError("O tempo de leitura deve ser positivo.")
            if not 0.1 <= interval <= 1.0:
                raise ValueError("O intervalo deve estar entre 0,1 e 1,0 segundo.")
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
        metadata = AcquisitionMetadata(
            model=model,
            serial=self._state.serial_number,
            firmware=self._state.firmware,
            unit=self._state.reading_unit,
        )
        request = AcquisitionRequest(mode, path, points, interval, timeout)
        self._visual_readings.clear()
        self._update(
            acquisition_running=True,
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
            try:
                count = self._acquisition.run(
                    request,
                    metadata,
                    self._on_acquisition_reading,
                    self._on_acquisition_progress,
                )
            except AcquisitionCancelled:
                self._update(
                    acquisition_running=False,
                    controller_state=self.controller.state.value,
                    readings=tuple(self._visual_readings),
                    status_message="Aquisição cancelada",
                )
                self._log(LogLevel.WARNING, "Aquisição cancelada pelo operador.")
                return
            except BaseException as error:
                self._update(
                    acquisition_running=False,
                    controller_state=self.controller.state.value,
                    readings=tuple(self._visual_readings),
                    status_message="Falha na aquisição",
                    error_banner="Aquisição: {0}".format(error),
                )
                self._log(LogLevel.ERROR, "Aquisição: {0}".format(error))
                return
            self._update(
                acquisition_running=False,
                acquisition_count=count,
                controller_state=self.controller.state.value,
                readings=tuple(self._visual_readings),
                status_message="Aquisição concluída",
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
            "Fechado OU cabo ausente — indeterminado"
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
