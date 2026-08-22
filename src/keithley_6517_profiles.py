"""Documented capability matrix for Keithley 6517A and 6517B.

Values are intentionally data-driven so the UI never branches on model names.
The driver remains authoritative for instrument control; this module supplies
operator-facing limits and traceable metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class MeasurementCapability:
    scpi_name: str
    label: str
    unit: str
    maximum_range: float


@dataclass(frozen=True)
class ModelCapabilities:
    model: str
    scpi_version: str
    max_buffer_points_with_timestamp: int
    max_rs232_baud: int
    measurement_functions: Tuple[MeasurementCapability, ...]
    source_ranges_v: Tuple[float, ...] = (100.0, 1000.0)
    source_current_limits_a: Tuple[float, ...] = (10.0e-3, 1.0e-3)
    format_elements: Tuple[str, ...] = ("READING", "TSTAMP", "STATUS")
    trace_elements: Tuple[str, ...] = ("TSTAMP",)

    def measurement(self, scpi_name: str) -> MeasurementCapability:
        normalized = scpi_name.strip().upper()
        for capability in self.measurement_functions:
            if capability.scpi_name.upper() == normalized:
                return capability
        raise ValueError("Função não suportada pelo perfil {0}: {1}".format(self.model, scpi_name))

    def source_current_limit_a(self, source_range_v: float) -> float:
        return (
            self.source_current_limits_a[0]
            if abs(float(source_range_v)) <= self.source_ranges_v[0]
            else self.source_current_limits_a[1]
        )


MEASUREMENT_FUNCTIONS = (
    MeasurementCapability("CURRent:DC", "Corrente DC", "A", 21.0e-3),
    MeasurementCapability("VOLTage:DC", "Tensão DC", "V", 210.0),
    MeasurementCapability("RESistance", "Resistência", "Ω", 100.0e18),
    MeasurementCapability("CHARge", "Carga", "C", 2.1e-6),
)


PROFILE_6517A_CAPABILITIES = ModelCapabilities(
    model="6517A",
    scpi_version="1991.0",
    max_buffer_points_with_timestamp=10470,
    max_rs232_baud=19200,
    measurement_functions=MEASUREMENT_FUNCTIONS,
)


PROFILE_6517B_CAPABILITIES = ModelCapabilities(
    model="6517B",
    scpi_version="1996.0",
    max_buffer_points_with_timestamp=50000,
    max_rs232_baud=115200,
    measurement_functions=MEASUREMENT_FUNCTIONS,
)


MODEL_CAPABILITIES: Dict[str, ModelCapabilities] = {
    "6517A": PROFILE_6517A_CAPABILITIES,
    "6517B": PROFILE_6517B_CAPABILITIES,
}


def capabilities_for_model(model: str) -> ModelCapabilities:
    normalized = (model or "").strip().upper().replace("MODEL", "").strip()
    if normalized not in MODEL_CAPABILITIES:
        raise ValueError("Modelo suportado deve ser 6517A ou 6517B.")
    return MODEL_CAPABILITIES[normalized]


__all__ = [
    "MeasurementCapability",
    "ModelCapabilities",
    "MODEL_CAPABILITIES",
    "PROFILE_6517A_CAPABILITIES",
    "PROFILE_6517B_CAPABILITIES",
    "capabilities_for_model",
]
