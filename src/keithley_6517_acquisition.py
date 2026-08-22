"""Background acquisition workflows independent from the graphical UI."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional, Tuple

try:
    from .keithley_6517_driver import (
        AcquisitionCancelled,
        ControllerState,
        KeithleyController,
        MeasurementReading,
    )
    from .keithley_6517_storage import CsvAcquisitionWriter
except ImportError:  # pragma: no cover - direct src execution compatibility
    from keithley_6517_driver import (
        AcquisitionCancelled,
        ControllerState,
        KeithleyController,
        MeasurementReading,
    )
    from keithley_6517_storage import CsvAcquisitionWriter


@dataclass(frozen=True)
class AcquisitionRequest:
    mode: str
    output_path: Path
    points: int = 100
    timer_interval_s: float = 0.1
    timeout_s: float = 60.0


@dataclass(frozen=True)
class AcquisitionMetadata:
    model: str
    serial: str
    firmware: str
    unit: str


class AcquisitionRunner:
    """Execute LIVE or BUFFER acquisition in an application-owned worker."""

    def __init__(self, controller: KeithleyController) -> None:
        self._controller = controller
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(
        self,
        request: AcquisitionRequest,
        metadata: AcquisitionMetadata,
        on_reading: Callable[[int, MeasurementReading], None],
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> int:
        self._stop_event.clear()
        mode = request.mode.strip().upper()
        if mode not in ("LIVE", "BUFFER"):
            raise ValueError("Modo de aquisição deve ser LIVE ou BUFFER.")
        if request.points < 1:
            raise ValueError("A aquisição deve solicitar pelo menos uma amostra.")
        if request.timer_interval_s < 0:
            raise ValueError("O intervalo da aquisição não pode ser negativo.")

        count = 0
        timestamp_origin: Optional[float] = None
        try:
            with CsvAcquisitionWriter(request.output_path) as writer:
                if mode == "LIVE":
                    self._controller.start_live()
                    next_read_at = time.monotonic()
                    while count < request.points and not self._stop_event.is_set():
                        wait_s = next_read_at - time.monotonic()
                        if wait_s > 0 and self._stop_event.wait(wait_s):
                            break
                        reading = self._controller.read_live()
                        count += 1
                        reading, timestamp_origin = self._relative_reading(
                            reading, timestamp_origin
                        )
                        self._write_and_publish(
                            writer, count, reading, metadata, on_reading
                        )
                        if on_progress is not None:
                            on_progress(count, request.points)
                        next_read_at = max(
                            next_read_at + request.timer_interval_s,
                            time.monotonic(),
                        )
                else:
                    self._controller.prepare_buffer(
                        request.points,
                        source="TIMer",
                        timer_interval=request.timer_interval_s,
                    )
                    self._controller.start_buffer()
                    self._controller.wait_buffer_complete(
                        request.timeout_s,
                        stop_event=self._stop_event,
                    )
                    for reading in self._controller.read_buffer_readings():
                        if self._stop_event.is_set():
                            raise AcquisitionCancelled("Aquisição cancelada pelo operador.")
                        count += 1
                        reading, timestamp_origin = self._relative_reading(
                            reading, timestamp_origin
                        )
                        self._write_and_publish(
                            writer, count, reading, metadata, on_reading
                        )
                        if on_progress is not None:
                            on_progress(count, request.points)
            if self._stop_event.is_set():
                raise AcquisitionCancelled("Aquisição cancelada pelo operador.")
            return count
        finally:
            if self._controller.connected and self._controller.state in (
                ControllerState.ARMED,
                ControllerState.ACQUIRING,
                ControllerState.ERROR,
            ):
                self._controller.abort()

    @staticmethod
    def _relative_reading(
        reading: MeasurementReading,
        timestamp_origin: Optional[float],
    ) -> Tuple[MeasurementReading, float]:
        """Normalize instrument time so every acquisition starts at zero."""

        origin = reading.timestamp if timestamp_origin is None else timestamp_origin
        relative_timestamp = max(0.0, reading.timestamp - origin)
        return replace(reading, timestamp=relative_timestamp), origin

    @staticmethod
    def _write_and_publish(
        writer: CsvAcquisitionWriter,
        index: int,
        reading: MeasurementReading,
        metadata: AcquisitionMetadata,
        callback: Callable[[int, MeasurementReading], None],
    ) -> None:
        writer.write(
            sample=index,
            instrument_time_s=reading.timestamp,
            value=reading.value,
            raw_value=reading.raw_value,
            unit=metadata.unit,
            status=reading.status.value,
            model=metadata.model,
            serial=metadata.serial,
            firmware=metadata.firmware,
        )
        callback(index, reading)


__all__ = [
    "AcquisitionMetadata",
    "AcquisitionRequest",
    "AcquisitionRunner",
]
