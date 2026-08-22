"""Smoke test controlado para um Keithley 6517A/6517B real, sem habilitar HV.

O script apenas enumera VISA, identifica o instrumento, aplica a receita de
parada segura, drena a fila de erros e desconecta. Nenhuma aquisição é iniciada.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional


PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from keithley_6517_driver import ControllerState, KeithleyController


def _select_resource(resources: List[str], requested: Optional[str]) -> Optional[str]:
    if requested:
        return requested
    gpib = [resource for resource in resources if resource.upper().startswith("GPIB")]
    return gpib[0] if gpib else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--resource",
        help="Recurso VISA explícito, por exemplo GPIB0::27::INSTR.",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Somente lista recursos; não abre o instrumento.",
    )
    args = parser.parse_args()

    controller = KeithleyController()
    try:
        resources = list(controller.list_resources())
        print("Recursos VISA:")
        for resource in resources:
            print("  " + resource)
        if args.list_only:
            return 0

        resource = _select_resource(resources, args.resource)
        if resource is None:
            print("Nenhum recurso GPIB disponível para o smoke test.", file=sys.stderr)
            return 2

        print("Abrindo:", resource)
        identity = controller.connect(resource)
        profile = controller.profile
        print("*IDN?:", identity)
        print("Perfil:", profile.model if profile else "não detectado")
        print("Estado após conexão defensiva:", controller.state.value)
        if controller.state != ControllerState.SAFE:
            print("Instrumento não chegou ao estado Safe.", file=sys.stderr)
            return 3

        errors = controller.check_errors()
        print("Fila de erros:", "vazia" if not errors else " | ".join(errors))
        controller.safe_shutdown()
        print("Parada segura confirmada. HV não foi habilitada.")
        return 0 if not errors else 3
    except Exception as error:
        print("Smoke test não concluído:", error, file=sys.stderr)
        return 3
    finally:
        controller.shutdown()


if __name__ == "__main__":
    sys.exit(main())
