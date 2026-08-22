"""Comunicação inicial segura com Keithley 6517A/6517B via NI-VISA.

Esta aplicação enumera recursos VISA, identifica o modelo por ``*IDN?`` e
coloca o instrumento no estado seguro. Ela não inicia medições nem lê buffer.

Pré-requisitos:
    - NI-488.2 e NI-VISA instalados no Windows;
    - adaptador NI GPIB-USB-B conectado;
    - Python 3.8+ e a dependência pyvisa (python -m pip install -r requirements.txt).
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

try:
    from keithley_6517_driver import KeithleyController
except ImportError:
    from .keithley_6517_driver import KeithleyController

try:
    import pyvisa
    from pyvisa.errors import VisaIOError
except ImportError:
    pyvisa = None
    VisaIOError = Exception


PROJECT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_DIR / "var" / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"comunicacao_{datetime.now():%Y%m%d}.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

DEFAULT_RESOURCE = "GPIB0::27::INSTR"
QUERY_TIMEOUT_MS = 5000


class KeithleyCommunicationApp:
    """Tela para identificar o instrumento sem enviar comandos de configuração."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.controller = KeithleyController()

        self.resource_var = tk.StringVar(value=DEFAULT_RESOURCE)
        self.status_var = tk.StringVar(value="Desconectado")
        self.identity_var = tk.StringVar(value="Nenhum instrumento identificado.")
        self._build_ui()

        self.root.protocol("WM_DELETE_WINDOW", self.close_application)
        logging.info("Aplicação iniciada.")

    def _build_ui(self) -> None:
        self.root.title("Keithley 6517 - Comunicação Inicial")
        self.root.geometry("720x360")
        self.root.minsize(620, 320)

        container = ttk.Frame(self.root, padding=18)
        container.grid(sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        container.columnconfigure(1, weight=1)

        ttk.Label(
            container,
            text="Comunicação inicial segura",
            font=("Segoe UI", 14, "bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(
            container,
            text="A aplicação identifica por *IDN? e aplica uma parada segura; não inicia medições.",
            wraplength=650,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 16))

        ttk.Label(container, text="Recurso VISA:").grid(row=2, column=0, sticky="w")
        self.resource_combo = ttk.Combobox(
            container,
            textvariable=self.resource_var,
            width=42,
        )
        self.resource_combo.grid(row=2, column=1, sticky="ew", padx=(10, 10))
        self.search_button = ttk.Button(
            container,
            text="Buscar GPIB",
            command=self.search_resources,
        )
        self.search_button.grid(row=2, column=2, sticky="e")

        self.connect_button = ttk.Button(
            container,
            text="Conectar e identificar",
            command=self.connect_and_identify,
        )
        self.connect_button.grid(row=3, column=1, sticky="w", pady=(16, 16))
        self.disconnect_button = ttk.Button(
            container,
            text="Desconectar",
            command=self.disconnect,
            state="disabled",
        )
        self.disconnect_button.grid(row=3, column=2, sticky="e", pady=(16, 16))

        ttk.Separator(container).grid(row=4, column=0, columnspan=3, sticky="ew", pady=(0, 14))
        ttk.Label(container, text="Estado:", font=("Segoe UI", 10, "bold")).grid(
            row=5, column=0, sticky="nw"
        )
        ttk.Label(container, textvariable=self.status_var, wraplength=540).grid(
            row=5, column=1, columnspan=2, sticky="w"
        )
        ttk.Label(container, text="Identificação:", font=("Segoe UI", 10, "bold")).grid(
            row=6, column=0, sticky="nw", pady=(12, 0)
        )
        ttk.Label(container, textvariable=self.identity_var, wraplength=540).grid(
            row=6, column=1, columnspan=2, sticky="w", pady=(12, 0)
        )
        ttk.Label(
            container,
            text=f"Log: {LOG_FILE}",
            foreground="#555555",
            wraplength=650,
        ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(22, 0))

    def _ensure_pyvisa(self) -> bool:
        if pyvisa is not None:
            return True

        message = (
            "A biblioteca PyVISA não está instalada.\n\n"
            "No Prompt de Comando, execute:\n"
            "python -m pip install -r requirements.txt"
        )
        self.status_var.set("PyVISA não encontrado.")
        messagebox.showerror("Dependência ausente", message)
        logging.error("PyVISA não encontrado.")
        return False

    def search_resources(self) -> None:
        """Lista recursos GPIB detectados pela NI-VISA; não envia comandos ao instrumento."""
        if not self._ensure_pyvisa():
            return

        try:
            self.status_var.set("Buscando recursos VISA...")
            self.root.update_idletasks()
            resources = self.controller.list_resources()
            gpib_resources = [item for item in resources if item.upper().startswith("GPIB")]

            self.resource_combo["values"] = gpib_resources
            if gpib_resources:
                if self.resource_var.get() not in gpib_resources:
                    self.resource_var.set(gpib_resources[0])
                self.status_var.set(f"{len(gpib_resources)} recurso(s) GPIB encontrado(s).")
                logging.info("Recursos GPIB encontrados: %s", gpib_resources)
            else:
                self.status_var.set(
                    "Nenhum recurso GPIB listado automaticamente. Confirme a interface no NI MAX; "
                    "o endereço pode ser informado manualmente."
                )
                logging.warning("Nenhum recurso GPIB encontrado: %s", resources)
        except VisaIOError as error:
            self._show_visa_error("Não foi possível buscar os recursos VISA", error)

    def connect_and_identify(self) -> None:
        """Identifica o modelo e deixa o instrumento no estado Safe."""
        if not self._ensure_pyvisa():
            return

        resource_name = self.resource_var.get().strip()
        if not resource_name:
            messagebox.showwarning(
                "Recurso VISA",
                "Informe um recurso VISA, por exemplo GPIB0::27::INSTR.",
            )
            return

        self.disconnect(silent=True)
        self.status_var.set("Conectando, identificando e aplicando estado seguro...")
        self.identity_var.set("Aguardando resposta do instrumento.")
        self.root.update_idletasks()

        try:
            identity = self.controller.connect(
                resource_name, timeout_ms=QUERY_TIMEOUT_MS
            )
            self.identity_var.set(identity)
            model = self.controller.profile.model if self.controller.profile else "?"
            self.status_var.set(
                f"Keithley {model} conectado, identificado e em estado seguro."
            )
            logging.info("Instrumento identificado em %s: %s", resource_name, identity)
            self.connect_button.config(state="disabled")
            self.disconnect_button.config(state="normal")
        except VisaIOError as error:
            self.identity_var.set("Nenhuma identificação recebida.")
            self._show_visa_error(
                "Falha de comunicação. Verifique cabo, alimentação, endereço GPIB e NI MAX",
                error,
            )
        except Exception as error:
            self.identity_var.set("Nenhuma identificação recebida.")
            self.status_var.set(f"Erro: {error}")
            logging.exception("Erro durante identificação/estado seguro")
            messagebox.showerror("Erro", f"Falha de conexão:\n{error}")

    def _show_visa_error(self, context: str, error: Exception) -> None:
        self.status_var.set(context)
        logging.error("%s: %s", context, error)
        messagebox.showerror("Erro NI-VISA", f"{context}.\n\nDetalhe NI-VISA:\n{error}")
        self.disconnect(silent=True)

    def disconnect(self, silent: bool = False) -> None:
        """Executa parada segura e fecha a sessão pelo worker VISA."""
        self.controller.disconnect(silent=True)
        self.connect_button.config(state="normal")
        self.disconnect_button.config(state="disabled")
        if not silent:
            self.status_var.set("Desconectado.")

    def close_application(self) -> None:
        self.controller.shutdown()
        logging.info("Aplicação encerrada.")
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    ttk.Style().theme_use("vista" if "vista" in ttk.Style().theme_names() else "clam")
    KeithleyCommunicationApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
