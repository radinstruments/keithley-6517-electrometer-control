"""Model-aware SCPI catalog and safe-console preflight.

The catalog is deliberately conservative.  The free console accepts one SCPI
program unit per transaction and rejects unknown or compound messages instead
of guessing how many responses the instrument will place in its output queue.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence, Tuple


class ScpiRisk(Enum):
    NONE = "NONE"
    STATE_CHANGE = "STATE_CHANGE"
    RESET = "RESET"
    HV_CONFIG = "HV_CONFIG"
    HV_ENABLE = "HV_ENABLE"
    HV_DISABLE = "HV_DISABLE"


@dataclass(frozen=True)
class ScpiCommandSpec:
    key: str
    header_pattern: str
    query: bool
    supported_models: Tuple[str, ...]
    risk: ScpiRisk
    summary: str
    manual_reference: str

    def matches(self, header: str, is_query: bool) -> bool:
        return self.query == is_query and bool(
            re.fullmatch(self.header_pattern, header, re.IGNORECASE)
        )


@dataclass(frozen=True)
class ScpiPreflightResult:
    source_text: str
    normalized_command: str
    valid: bool
    is_query: bool
    risk: ScpiRisk
    summary: str
    manual_reference: str
    confirmation_required: bool
    digest: str
    error: str = ""


@dataclass(frozen=True)
class ScpiAuthorizationToken:
    digest: str
    model: str
    session_id: int
    expires_at: float

    def valid_for(self, preview: ScpiPreflightResult, model: str, session_id: int) -> bool:
        return (
            preview.valid
            and self.digest == preview.digest
            and self.model == model
            and self.session_id == session_id
            and time.monotonic() <= self.expires_at
        )


ALL_MODELS = ("6517A", "6517B")


SCPI_CATALOG: Tuple[ScpiCommandSpec, ...] = (
    ScpiCommandSpec("idn", r"\*IDN", True, ALL_MODELS, ScpiRisk.NONE,
                    "Identifica fabricante, modelo, número de série e firmware.",
                    "6517A §3.11.4; 6517B Rev. F §12"),
    ScpiCommandSpec("options", r"\*OPT", True, ALL_MODELS, ScpiRisk.NONE,
                    "Consulta opções instaladas.", "IEEE-488.2 common commands"),
    ScpiCommandSpec("self_test", r"\*TST", True, ALL_MODELS, ScpiRisk.STATE_CHANGE,
                    "Executa o autoteste interno.", "IEEE-488.2 common commands"),
    ScpiCommandSpec("clear", r"\*CLS", False, ALL_MODELS, ScpiRisk.STATE_CHANGE,
                    "Limpa registradores de status e a fila de erros.",
                    "6517A §3.8; 6517B Rev. F §13"),
    ScpiCommandSpec("reset", r"\*RST", False, ALL_MODELS, ScpiRisk.RESET,
                    "Restaura defaults SCPI e exige reaplicação do estado seguro.",
                    "IEEE-488.2 common commands"),
    ScpiCommandSpec("opc", r"\*OPC", True, ALL_MODELS, ScpiRisk.NONE,
                    "Aguarda conclusão; proibido depois de INIT:CONT ON.",
                    "IEEE-488.2 common commands"),
    ScpiCommandSpec("status_byte", r"\*STB", True, ALL_MODELS, ScpiRisk.NONE,
                    "Consulta o status byte IEEE-488.2.",
                    "6517A §3.8; 6517B Rev. F §13"),
    ScpiCommandSpec("system_version", r":?SYST(?:EM)?:VERS(?:ION)?", True,
                    ALL_MODELS, ScpiRisk.NONE, "Consulta a versão SCPI reportada.",
                    "6517A §3.22.3; 6517B SYSTEM"),
    ScpiCommandSpec("system_error", r":?SYST(?:EM)?:ERR(?:OR)?", True,
                    ALL_MODELS, ScpiRisk.NONE, "Remove a mensagem mais antiga da fila de erros.",
                    "6517A/6517B SYSTEM error queue"),
    ScpiCommandSpec("interlock", r":?SYST(?:EM)?:INT(?:ERLOCK)?", True,
                    ALL_MODELS, ScpiRisk.NONE,
                    "Retorno 1 significa fixture fechada OU cabo ausente; não prova segurança.",
                    "6517A §3.22.16; 6517B SYSTEM"),
    ScpiCommandSpec("fresh", r":?SENS(?:E)?:DATA:FRES(?:H)?", True,
                    ALL_MODELS, ScpiRisk.NONE, "Espera e devolve uma leitura nova.",
                    "6517A/6517B SENSe:DATA:FRESh?"),
    ScpiCommandSpec("read", r":?READ", True, ALL_MODELS, ScpiRisk.STATE_CHANGE,
                    "Executa ABORt, INITiate e FETCh de uma leitura.",
                    "6517A/6517B trigger model"),
    ScpiCommandSpec("fetch", r":?FETC(?:H)?", True, ALL_MODELS, ScpiRisk.NONE,
                    "Devolve a leitura mais recente sem iniciar nova medição.",
                    "6517A/6517B trigger model"),
    ScpiCommandSpec("trace_actual", r":?(?:TRAC(?:E)?|DATA):POIN(?:TS)?:ACT(?:UAL)?",
                    True, ALL_MODELS, ScpiRisk.NONE, "Consulta pontos atualmente no buffer.",
                    "6517A §3.23; 6517B Rev. F TRACE"),
    ScpiCommandSpec("trace_data", r":?(?:TRAC(?:E)?|DATA):DATA", True,
                    ALL_MODELS, ScpiRisk.NONE, "Transfere o buffer no esquema FORMAT confirmado.",
                    "6517A §3.23; 6517B Rev. F TRACE"),
    ScpiCommandSpec("abort", r":?ABOR(?:T)?", False, ALL_MODELS,
                    ScpiRisk.STATE_CHANGE, "Interrompe o modelo de trigger.",
                    "6517A/6517B trigger model"),
    ScpiCommandSpec("output_query", r":?OUTP(?:UT)?1?(?::STAT(?:E)?)?", True,
                    ALL_MODELS, ScpiRisk.NONE, "Consulta o estado da fonte.",
                    "6517A §3.20; 6517B SOURCE/OUTPUT"),
    ScpiCommandSpec("output_write", r":?OUTP(?:UT)?1?(?::STAT(?:E)?)?", False,
                    ALL_MODELS, ScpiRisk.HV_ENABLE,
                    "Pode colocar a fonte de até ±1000 V em operate.",
                    "6517A §3.20; 6517B SOURCE/OUTPUT"),
    ScpiCommandSpec("source_voltage_query", r":?SOUR(?:CE)?:VOLT(?:AGE)?(?::(?:LEV(?:EL)?|RANG(?:E)?|LIM(?:IT)?)(?::STAT(?:E)?)?)?",
                    True, ALL_MODELS, ScpiRisk.NONE, "Consulta configuração da fonte de tensão.",
                    "6517A §3.20; 6517B Rev. F SOURCE"),
    ScpiCommandSpec("source_voltage_write", r":?SOUR(?:CE)?:VOLT(?:AGE)?(?::(?:LEV(?:EL)?|RANG(?:E)?|LIM(?:IT)?)(?::STAT(?:E)?)?)?",
                    False, ALL_MODELS, ScpiRisk.HV_CONFIG,
                    "Programa nível, faixa ou limite da fonte de tensão.",
                    "6517A §3.20; 6517B Rev. F SOURCE"),
    ScpiCommandSpec("source_compliance", r":?SOUR(?:CE)?:CURR(?:ENT)?:LIM(?:IT)?:STAT(?:E)?",
                    True, ALL_MODELS, ScpiRisk.NONE,
                    "Consulta o estado de compliance; não mede corrente.",
                    "6517A §3.20; 6517B Rev. F SOURCE"),
    ScpiCommandSpec("manual_vsource", r":?SENS(?:E)?:RES(?:ISTANCE)?:MAN(?:UAL)?:VSOU(?:RCE)?:OPER(?:ATE)?",
                    False, ALL_MODELS, ScpiRisk.HV_ENABLE,
                    "Pode operar a fonte manual de resistência.",
                    "6517A/6517B SENSe:RESistance:MANual"),
)


def normalize_scpi(command: str) -> str:
    lines = [line.strip() for line in (command or "").splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("Informe exatamente uma unidade SCPI por transação.")
    normalized = re.sub(r"\s+", " ", lines[0]).strip()
    if ";" in normalized:
        raise ValueError("Mensagens SCPI compostas por ';' são bloqueadas no console seguro.")
    return normalized


def _header_and_query(command: str) -> Tuple[str, bool]:
    header = command.partition(" ")[0]
    is_query = header.endswith("?")
    if "?" in header[:-1] or command.count("?") != (1 if is_query else 0):
        raise ValueError("Marcador de query inválido ou fora do cabeçalho SCPI.")
    return header[:-1] if is_query else header, is_query


def preflight_scpi(command: str, model: str, session_id: int = 0) -> ScpiPreflightResult:
    source = command or ""
    normalized_model = (model or "").strip().upper()
    try:
        normalized = normalize_scpi(source)
        header, is_query = _header_and_query(normalized)
    except ValueError as error:
        return ScpiPreflightResult(
            source, "", False, False, ScpiRisk.NONE, "Comando rejeitado.", "",
            False, "", str(error)
        )

    specification: Optional[ScpiCommandSpec] = None
    for candidate in SCPI_CATALOG:
        if candidate.matches(header, is_query):
            specification = candidate
            break
    if specification is None:
        return ScpiPreflightResult(
            source, normalized, False, is_query, ScpiRisk.NONE,
            "Comando fora do catálogo validado.", "", False, "",
            "Adicione o comando ao catálogo com modelo, parâmetros, resposta e fonte manual."
        )
    if normalized_model not in specification.supported_models:
        return ScpiPreflightResult(
            source, normalized, False, is_query, specification.risk,
            specification.summary, specification.manual_reference, False, "",
            "Comando não documentado para o modelo detectado {0}.".format(normalized_model)
        )

    upper = normalized.upper()
    risk = specification.risk
    if specification.key == "output_write":
        parameter = upper.partition(" ")[2].strip()
        if parameter in ("OFF", "0", "+0"):
            risk = ScpiRisk.HV_DISABLE
        elif parameter not in ("ON", "1", "+1"):
            return ScpiPreflightResult(
                source, normalized, False, is_query, risk, specification.summary,
                specification.manual_reference, False, "",
                "OUTPut aceita somente ON/1 ou OFF/0 no console seguro."
            )

    digest_source = "{0}|{1}|{2}".format(normalized_model, int(session_id), normalized)
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
    confirmation = risk in (
        ScpiRisk.RESET,
        ScpiRisk.HV_CONFIG,
        ScpiRisk.HV_ENABLE,
    )
    return ScpiPreflightResult(
        source, normalized, True, is_query, risk, specification.summary,
        specification.manual_reference, confirmation, digest, ""
    )


def issue_authorization(
    preview: ScpiPreflightResult,
    model: str,
    session_id: int,
    ttl_seconds: float = 20.0,
) -> ScpiAuthorizationToken:
    if not preview.valid or not preview.confirmation_required:
        raise ValueError("A pré-análise não requer uma autorização perigosa.")
    return ScpiAuthorizationToken(
        preview.digest,
        model.strip().upper(),
        int(session_id),
        time.monotonic() + max(1.0, min(float(ttl_seconds), 60.0)),
    )


__all__ = [
    "SCPI_CATALOG",
    "ScpiAuthorizationToken",
    "ScpiCommandSpec",
    "ScpiPreflightResult",
    "ScpiRisk",
    "issue_authorization",
    "normalize_scpi",
    "preflight_scpi",
]
