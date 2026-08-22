"""Software de controle do eletrômetro Keithley 6517A/6517B via NI-VISA.

Estende a comunicação inicial (keithley_6517_comunicacao.py) com:
  - aba Conexão: identificação, reset/cls, leitura de erros;
  - aba Aquisição: configuração de função/faixa/NPLC e captura LIVE por
    :SENSe:DATA:FRESh? ou finita por buffer (:TRACe), salvando CSV com
    valor, tempo e classificação da leitura;
  - aba Alta tensão: programação em standby, limite obrigatório, interlock,
    checklist de segurança, monitoramento e desligamento prioritário;
  - aba Painel SCPI: envio de comandos/consultas livres, com checagem
    automática de :SYSTem:ERRor? e resposta formatada.

Pré-requisitos:
  - NI-488.2 e NI-VISA instalados no Windows;
  - Python 3.8+ e pyvisa (python -m pip install -r requirements.txt);

AVISO DE SEGURANÇA:
  Este software envia comandos que alteram o estado do instrumento (função,
  faixa, integração, trigger, buffer). Os modelos medem tensão até cerca de
  210 V e possuem uma fonte interna separada de até ±1000 V.
  Confirme as configurações antes de iniciar a aquisição e nunca habilite a
  fonte de tensão (:OUTPut1 ON) sem revisar o circuito de cargas.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from tkinter import messagebox, ttk
from typing import Any, List, Optional, Tuple

try:
    from keithley_6517_driver import (
        AcquisitionCancelled,
        ControllerState,
        KeithleyController as DeterministicKeithleyController,
        MeasurementReading,
        ReadingStatus,
        VoltageSourceStatus,
        analyze_scpi_safety,
        is_dangerous_command as driver_is_dangerous_command,
    )
except ImportError:  # permite importar como pacote ``src`` nos testes
    from .keithley_6517_driver import (
        AcquisitionCancelled,
        ControllerState,
        KeithleyController as DeterministicKeithleyController,
        MeasurementReading,
        ReadingStatus,
        VoltageSourceStatus,
        analyze_scpi_safety,
        is_dangerous_command as driver_is_dangerous_command,
    )

try:
    import pyvisa
    from pyvisa.errors import VisaIOError
except ImportError:
    pyvisa = None
    VisaIOError = Exception

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
except ImportError:
    matplotlib = None
    Figure = None
    FigureCanvasTkAgg = None


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR = PROJECT_DIR / "var" / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"controle_{datetime.now():%Y%m%d}.log"

_log_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
_log_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[_log_handler])

DEFAULT_RESOURCE = "GPIB0::27::INSTR"
DEFAULT_TIMEOUT_MS = 5000

FUNCTIONS: List[Tuple[str, str, str]] = [
    ("VOLTage:DC", "Tensão (V)", "VOLTage"),
    ("CURRent:DC", "Corrente (A)", "CURRent"),
    ("RESistance", "Resistência (Ω)", "RESistance"),
    ("CHARge", "Carga (C)", "CHARge"),
]

def is_dangerous_command(command: str) -> bool:
    """Detecta ativações/configurações HV, inclusive mensagens com ``;``."""
    return driver_is_dangerous_command(command)


def short_function(scpi_name: str) -> str:
    """Retorna o caminho curto (:SENS:<X>) a partir do nome SCPI retornado."""
    upper = (scpi_name or "").upper().strip().strip("'\"")
    for key in ("VOLTAGE:DC", "VOLTAGE", "VOLT:DC", "VOLT"):
        if upper.startswith(key) or upper == key:
            return "VOLTage"
    for key in ("CURRENT:DC", "CURRENT", "CURR:DC", "CURR"):
        if upper.startswith(key) or upper == key:
            return "CURRent"
    for key in ("RESISTANCE", "RES"):
        if upper.startswith(key) or upper == key:
            return "RESistance"
    for key in ("CHARGE", "CHAR"):
        if upper.startswith(key) or upper == key:
            return "CHARge"
    return "VOLTage"


# A GUI permanece inalterada visualmente e usa exclusivamente a nova camada.
KeithleyController = DeterministicKeithleyController


class KeithleyControlApp:
    """Janela principal de conexão, aquisição, alta tensão e console SCPI."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.controller = KeithleyController()
        self._acq_thread: Optional[threading.Thread] = None
        self._acq_stop = threading.Event()
        self._acq_queue: "Queue[Tuple[str, Any]]" = Queue()
        self._scpi_running = False
        self._hv_operation_running = False
        self._hv_queue: "Queue[Tuple[str, Any]]" = Queue()
        self._hv_last_status: Optional[VoltageSourceStatus] = None
        self._applied_acq_signature: Optional[Tuple[Any, ...]] = None

        self._log_widgets: List[tk.Text] = []
        self.resource_var = tk.StringVar(value=DEFAULT_RESOURCE)
        self.status_var = tk.StringVar(value="Desconectado.")
        self.identity_var = tk.StringVar(value="Nenhum instrumento identificado.")
        self.error_log_lines: List[str] = []

        self._build_layout()
        self._restore_state()
        self.root.protocol("WM_DELETE_WINDOW", self.close_application)
        logging.info("Aplicação de controle iniciada.")
        self._poll_acq_queue()
        self._poll_hv_queue()
        self.root.after(2000, self._auto_refresh_hv_status)

    def _build_layout(self) -> None:
        self.root.title("Keithley 6517 - Controle")
        self.root.geometry("1400x920")
        self.root.minsize(1180, 780)

        top = ttk.Frame(self.root, padding=10)
        top.grid(sticky="ew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        ttk.Label(
            top,
            text="Software de controle do eletrômetro Keithley 6517A/B",
            font=("Segoe UI", 13, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            top,
            text="Identificação:",
            font=("Segoe UI", 9, "bold"),
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(top, textvariable=self.identity_var, wraplength=900).grid(
            row=2, column=0, sticky="w"
        )

        notebook = ttk.Notebook(self.root)
        notebook.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        self._build_connection_tab(notebook)
        self._build_acquisition_tab(notebook)
        self._build_high_voltage_tab(notebook)
        self._build_scpi_tab(notebook)

        bottom = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        bottom.grid(row=2, column=0, sticky="ew")
        ttk.Label(bottom, text="Estado:", font=("Segoe UI", 9, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(bottom, textvariable=self.status_var, wraplength=900).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(
            bottom,
            text=f"Log: {LOG_FILE}",
            foreground="#555555",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _build_connection_tab(self, parent: tk.Widget) -> None:
        tab = ttk.Frame(parent, padding=14)
        parent.add(tab, text="Conexão")

        tab.columnconfigure(1, weight=1)
        ttk.Label(tab, text="Recurso VISA:").grid(row=0, column=0, sticky="w", pady=4)
        self.resource_combo = ttk.Combobox(
            tab, textvariable=self.resource_var, width=44
        )
        self.resource_combo.grid(row=0, column=1, sticky="ew", padx=(10, 10))
        self.search_button = ttk.Button(
            tab, text="Buscar GPIB", command=self.search_resources
        )
        self.search_button.grid(row=0, column=2, sticky="e")

        btns = ttk.Frame(tab)
        btns.grid(row=1, column=0, columnspan=3, sticky="w", pady=(12, 12))
        self.connect_button = ttk.Button(
            btns, text="Conectar", command=self.connect
        )
        self.connect_button.grid(row=0, column=0, padx=2)
        self.disconnect_button = ttk.Button(
            btns, text="Desconectar", command=lambda: self.disconnect_prompt(), state="disabled"
        )
        self.disconnect_button.grid(row=0, column=1, padx=2)
        ttk.Button(btns, text="*IDN?", command=self.query_idn).grid(row=0, column=2, padx=2)
        ttk.Button(btns, text="*OPT? (options)", command=self.query_options).grid(
            row=0, column=3, padx=2
        )

        safe = ttk.LabelFrame(tab, text="Comandos seguros", padding=10)
        safe.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Button(
            safe, text="*CLS (limpa status/erros)", command=self.safe_clear_status
        ).grid(row=0, column=0, padx=2, pady=4)
        ttk.Button(
            safe, text="*TST? (autoteste)", command=self.safe_self_test
        ).grid(row=0, column=1, padx=2, pady=4)
        ttk.Button(
            safe, text=":SYSTem:ERRor? (drena fila)", command=self.safe_check_errors
        ).grid(row=0, column=2, padx=2, pady=4)

        reset_frame = ttk.LabelFrame(
            tab, text="Recuperação", padding=10
        )
        reset_frame.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(
            reset_frame,
            text="*RST restaura defaults de fábrica do 6517A. Use apenas com o "
            "instrumento em estado seguro (fonte V desligada, entrada em zero-check).",
            wraplength=820,
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Button(
            reset_frame, text="Confirmar e enviar *RST", command=self.safe_reset
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        self.connection_log = tk.Text(tab, height=10, wrap="word", state="disabled")
        self.connection_log.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        tab.rowconfigure(4, weight=1)
        self._log_widgets.append(self.connection_log)

    def _build_acquisition_tab(self, parent: tk.Widget) -> None:
        tab = ttk.Frame(parent, padding=14)
        parent.add(tab, text="Aquisição")
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(8, weight=1)

        self.acq_function_var = tk.StringVar(value=FUNCTIONS[0][0])
        self.acq_auto_range_var = tk.BooleanVar(value=True)
        self.acq_range_var = tk.StringVar(value="2.000000E-3")
        self.acq_nplc_var = tk.DoubleVar(value=1.0)
        self.acq_digits_var = tk.IntVar(value=6)
        self.acq_mode_var = tk.StringVar(value="continuo")
        self.acq_mode_var.trace_add("write", self._on_mode_change)
        self.acq_points_var = tk.IntVar(value=200)
        self.acq_interval_var = tk.DoubleVar(value=0.55)
        self.acq_file_var = tk.StringVar(
            value=str(DATA_DIR / "aquisicao_{:%Y%m%d_%H%M%S}.csv".format(datetime.now()))
        )
        self.acq_running = False

        ttk.Label(tab, text="Função de medição:").grid(row=0, column=0, sticky="w", pady=4)
        func_box = ttk.Combobox(
            tab,
            textvariable=self.acq_function_var,
            values=[f"{scpi}  ({label})" for scpi, label, _short in FUNCTIONS],
            state="readonly",
            width=42,
        )
        func_box.grid(row=0, column=1, sticky="w", padx=(10, 0))
        func_box.bind("<<ComboboxSelected>>", self._on_function_change)
        self._func_box = func_box

        ttk.Label(tab, text="Faixa (range upper):").grid(row=1, column=0, sticky="w", pady=4)
        range_frame = ttk.Frame(tab)
        range_frame.grid(row=1, column=1, sticky="w", padx=(10, 0))
        ttk.Checkbutton(
            range_frame, text="AUTO", variable=self.acq_auto_range_var
        ).grid(row=0, column=0, padx=(0, 10))
        ttk.Entry(range_frame, textvariable=self.acq_range_var, width=22).grid(row=0, column=1)
        ttk.Button(
            range_frame, text="Ler faixa atual (:SENS:<f>:RANG?)", command=self.query_current_range
        ).grid(row=0, column=2, padx=(10, 0))

        ttk.Label(tab, text="NPLCycles (0.01 a 10):").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(tab, textvariable=self.acq_nplc_var, width=12).grid(row=2, column=1, sticky="w", padx=(10, 0))
        ttk.Label(tab, text="Dígitos (4 a 7):").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(tab, textvariable=self.acq_digits_var, width=12).grid(row=3, column=1, sticky="w", padx=(10, 0))

        ttk.Label(tab, text="Modo:").grid(row=4, column=0, sticky="w", pady=4)
        mode_box = ttk.Combobox(
            tab, textvariable=self.acq_mode_var, state="readonly",
            values=["continuo", "buffer"],
            width=14,
        )
        mode_box.grid(row=4, column=1, sticky="w", padx=(10, 0))
        self.mode_dependent = ttk.Frame(tab)
        self.mode_dependent.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(self.mode_dependent, text="Nº de pontos (buffer):").grid(row=0, column=0, sticky="w", padx=2)
        ttk.Entry(self.mode_dependent, textvariable=self.acq_points_var, width=10).grid(row=0, column=1, padx=2)
        ttk.Label(self.mode_dependent, text="Intervalo(timer)s:").grid(row=0, column=2, sticky="w", padx=(20, 0))
        ttk.Entry(self.mode_dependent, textvariable=self.acq_interval_var, width=10).grid(row=0, column=3, padx=2)

        ttk.Label(tab, text="Arquivo CSV:").grid(row=6, column=0, sticky="w", pady=4)
        file_row = ttk.Frame(tab)
        file_row.grid(row=6, column=1, columnspan=2, sticky="ew", padx=(10, 0))
        self.file_entry = ttk.Entry(file_row, textvariable=self.acq_file_var)
        self.file_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(file_row, text="Procurar...", command=self.choose_file).grid(row=0, column=1)
        file_row.columnconfigure(0, weight=1)

        actions = ttk.Frame(tab)
        actions.grid(row=7, column=0, columnspan=3, sticky="w", pady=(10, 6))
        self.acq_start_button = ttk.Button(actions, text="Iniciar aquisição", command=self.start_acquisition)
        self.acq_start_button.grid(row=0, column=0, padx=2)
        self.acq_stop_button = ttk.Button(
            actions, text="Parar", command=self.request_stop_acquisition, state="disabled"
        )
        self.acq_stop_button.grid(row=0, column=1, padx=2)
        self.acq_abort_button = ttk.Button(
            actions, text=":ABORt (instrumento)", command=self.abort_instrument
        )
        self.acq_abort_button.grid(row=0, column=2, padx=2)
        ttk.Button(actions, text="Aplicar config (sem ler)", command=self.apply_configuration_only).grid(
            row=0, column=3, padx=2
        )

        results_frame = ttk.LabelFrame(tab, text="Leituras (valor, tempo s) e gráfico", padding=6)
        results_frame.grid(row=8, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        results_frame.columnconfigure(0, weight=1, uniform="results")
        results_frame.columnconfigure(1, weight=4, uniform="results")
        results_frame.rowconfigure(0, weight=1)

        # Coluna esquerda: tabela de leituras (compacta)
        table_pane = ttk.Frame(results_frame)
        table_pane.grid(row=0, column=0, sticky="nsew")
        table_pane.columnconfigure(0, weight=1)
        table_pane.rowconfigure(0, weight=1)
        cols = ("idx", "valor", "tempo", "status")
        self.results_tree = ttk.Treeview(
            table_pane, columns=cols, show="headings", height=22
        )
        for col, text, width in zip(
            cols,
            ("#", "Valor", "Tempo (s)", "Status"),
            (50, 220, 120, 120),
        ):
            self.results_tree.heading(col, text=text)
            self.results_tree.column(col, width=width, anchor="w")
        self.results_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table_pane, orient="vertical", command=self.results_tree.yview)
        self.results_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")

        # Coluna direita: gráfico em tempo real (matplotlib)
        chart_pane = ttk.Frame(results_frame)
        chart_pane.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        chart_pane.columnconfigure(0, weight=1)
        chart_pane.rowconfigure(0, weight=1)
        self._build_realtime_chart(chart_pane)

        self.acq_status_var = tk.StringVar(value="Pronto.")
        ttk.Label(tab, textvariable=self.acq_status_var, wraplength=820).grid(
            row=9, column=0, columnspan=3, sticky="w", pady=(6, 0)
        )

    def _build_high_voltage_tab(self, parent: tk.Widget) -> None:
        tab = ttk.Frame(parent, padding=14)
        parent.add(tab, text="Alta tensão")
        tab.columnconfigure(0, weight=1, uniform="hv")
        tab.columnconfigure(1, weight=1, uniform="hv")
        tab.rowconfigure(4, weight=1)

        ttk.Label(
            tab,
            text=(
                "PERIGO — a fonte interna pode aplicar até ±1000 V. Use somente "
                "fixture intertravado, conexões protegidas e procedimento aprovado. "
                "O limite nominal é 10 mA na faixa 100 V e 1 mA na faixa 1000 V."
            ),
            foreground="#9b0000",
            font=("Segoe UI", 10, "bold"),
            wraplength=1120,
        ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))

        config = ttk.LabelFrame(tab, text="Programação da fonte (saída em standby)", padding=12)
        config.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        config.columnconfigure(1, weight=1)
        self.hv_voltage_var = tk.StringVar(value="0")
        self.hv_limit_var = tk.StringVar(value="100")
        self.hv_range_preview_var = tk.StringVar(value="Faixa automática: 100 V")
        ttk.Label(config, text="Tensão desejada (V):").grid(row=0, column=0, sticky="w", pady=5)
        self.hv_voltage_entry = ttk.Entry(config, textvariable=self.hv_voltage_var, width=20)
        self.hv_voltage_entry.grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Label(config, text="-1000 a +1000 V").grid(row=0, column=2, sticky="w", padx=(10, 0))
        ttk.Label(config, text="Limite absoluto (V):").grid(row=1, column=0, sticky="w", pady=5)
        self.hv_limit_entry = ttk.Entry(config, textvariable=self.hv_limit_var, width=20)
        self.hv_limit_entry.grid(row=1, column=1, sticky="w", padx=(10, 0))
        ttk.Label(config, text="deve ser ≥ |tensão|").grid(row=1, column=2, sticky="w", padx=(10, 0))
        ttk.Label(config, textvariable=self.hv_range_preview_var).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(5, 8)
        )
        self.hv_apply_button = ttk.Button(
            config,
            text="Aplicar com saída desligada",
            command=self.apply_hv_configuration,
            state="disabled",
        )
        self.hv_apply_button.grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Label(
            config,
            text="A aplicação força :OUTPut1 OFF antes de alterar faixa, limite e nível.",
            foreground="#555555",
            wraplength=500,
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))

        status = ttk.LabelFrame(tab, text="Estado lido do instrumento", padding=12)
        status.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        status.columnconfigure(1, weight=1)
        self.hv_indicator = tk.Label(
            status,
            text="DESCONECTADO",
            bg="#5f6368",
            fg="white",
            font=("Segoe UI", 12, "bold"),
            padx=12,
            pady=8,
        )
        self.hv_indicator.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.hv_readback_var = tk.StringVar(value="—")
        self.hv_range_readback_var = tk.StringVar(value="—")
        self.hv_limit_readback_var = tk.StringVar(value="—")
        self.hv_interlock_var = tk.StringVar(value="—")
        self.hv_compliance_var = tk.StringVar(value="—")
        for row, (label, variable) in enumerate(
            (
                ("Nível programado:", self.hv_readback_var),
                ("Faixa selecionada:", self.hv_range_readback_var),
                ("Limite ativo:", self.hv_limit_readback_var),
                ("Interlock:", self.hv_interlock_var),
                ("Estado de compliance:", self.hv_compliance_var),
            ),
            start=1,
        ):
            ttk.Label(status, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Label(status, textvariable=variable, font=("Segoe UI", 9, "bold")).grid(
                row=row, column=1, sticky="w", padx=(10, 0), pady=3
            )
        self.hv_refresh_button = ttk.Button(
            status,
            text="Atualizar leituras",
            command=self.refresh_hv_status,
            state="disabled",
        )
        self.hv_refresh_button.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(
            status,
            text=(
                "* No 6517, resposta 1 também pode significar que nenhum cabo de "
                "interlock foi detectado. Verifique fisicamente o fixture."
            ),
            foreground="#7c2d12",
            wraplength=500,
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))

        checklist = ttk.LabelFrame(tab, text="Confirmações obrigatórias para ativar", padding=10)
        checklist.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 8))
        self.hv_circuit_checked_var = tk.BooleanVar(value=False)
        self.hv_fixture_checked_var = tk.BooleanVar(value=False)
        self.hv_area_checked_var = tk.BooleanVar(value=False)
        checks = (
            (self.hv_circuit_checked_var, "Circuito, polaridade, cabos e aterramento foram revisados."),
            (self.hv_fixture_checked_var, "Fixture fechado; não há partes energizadas acessíveis."),
            (self.hv_area_checked_var, "Área controlada e pessoas avisadas; estou autorizado a operar."),
        )
        for row, (variable, text) in enumerate(checks):
            ttk.Checkbutton(
                checklist,
                text=text,
                variable=variable,
                command=self._update_hv_controls,
            ).grid(row=row, column=0, sticky="w", pady=2)

        actions = ttk.Frame(tab)
        actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 8))
        self.hv_enable_button = tk.Button(
            actions,
            text="ATIVAR ALTA TENSÃO",
            command=self.enable_high_voltage,
            state="disabled",
            bg="#b45309",
            activebackground="#92400e",
            fg="white",
            activeforeground="white",
            font=("Segoe UI", 10, "bold"),
            padx=18,
            pady=8,
            relief="raised",
        )
        self.hv_enable_button.grid(row=0, column=0, padx=(0, 12))
        self.hv_disable_button = tk.Button(
            actions,
            text="DESLIGAR AGORA",
            command=self.disable_high_voltage,
            state="disabled",
            bg="#991b1b",
            activebackground="#7f1d1d",
            fg="white",
            activeforeground="white",
            font=("Segoe UI", 10, "bold"),
            padx=22,
            pady=8,
            relief="raised",
        )
        self.hv_disable_button.grid(row=0, column=1)
        ttk.Label(
            actions,
            text="O desligamento permanece disponível durante a aquisição.",
            foreground="#555555",
        ).grid(row=0, column=2, sticky="w", padx=(14, 0))

        self.hv_log = tk.Text(tab, height=9, wrap="word", state="disabled")
        self.hv_log.grid(row=4, column=0, columnspan=2, sticky="nsew")
        self._log_widgets.append(self.hv_log)
        self.hv_voltage_var.trace_add("write", self._update_hv_preview)
        self.hv_limit_var.trace_add("write", self._update_hv_preview)

    def _build_scpi_tab(self, parent: tk.Widget) -> None:
        tab = ttk.Frame(parent, padding=14)
        parent.add(tab, text="Painel SCPI")
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(4, weight=1)

        ttk.Label(tab, text="Comando/consulta (um por linha; linhas começam com # são ignoradas):").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(
            tab,
            text="AVISO: este painel altera o estado do instrumento. Comandos que ativam a "
            "fonte de tensão V (ex.: :OUTPut1 ON) geram até 1000V na saída — você será "
            "pedido para confirmar antes do envio.",
            foreground="#8a0000",
            wraplength=900,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 4))
        self.scpi_input = tk.Text(tab, height=6, wrap="word")
        self.scpi_input.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 6))
        self.scpi_input.insert("1.0", "*IDN?\n:SYSTem:ERRor?")

        scpi_actions = ttk.Frame(tab)
        scpi_actions.grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.scpi_send_button = ttk.Button(
            scpi_actions, text="Enviar", command=self.send_scpi
        )
        self.scpi_send_button.grid(row=0, column=0, padx=2)
        self.scpi_check_errors_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            scpi_actions,
            text="Checar :SYSTem:ERRor? após cada comando",
            variable=self.scpi_check_errors_var,
        ).grid(row=0, column=1, padx=10)
        ttk.Button(scpi_actions, text="Limpar saída", command=self.clear_scpi_output).grid(
            row=0, column=2, padx=2
        )

        quick = ttk.Frame(tab)
        quick.grid(row=3, column=2, sticky="e")
        for i, (label, cmd, query) in enumerate(
            [
                ("*IDN?", "*IDN?", True),
                ("*OPT?", "*OPT?", True),
                ("*RST", "*RST", False),
                ("*CLS", "*CLS", False),
                ("*OPC?", "*OPC?", True),
                ("*ESR?", "*ESR?", True),
                ("*STB?", "*STB?", True),
                (":SYST:ERR?", ":SYSTem:ERRor?", True),
                (":SENS:DATA:LAT?", ":SENSe:DATA:LATest?", True),
                (":SENS:FUNC?", ":SENSe:FUNCtion?", True),
                (":TRAC:FREE?", ":TRACe:FREE?", True),
                (":TRAC:DATA?", ":TRACe:DATA?", True),
                (":ABORt", ":ABORt", False),
            ]
        ):
            ttk.Button(
                quick,
                text=label,
                command=lambda c=cmd, q=query: self.send_quick(c, q),
            ).grid(row=i // 4, column=i % 4, padx=2, pady=2, sticky="ew")

        self.scpi_output = tk.Text(tab, height=18, wrap="word", state="disabled")
        self.scpi_output.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        self._log_widgets.append(self.scpi_output)

    def _parse_hv_values(self) -> Tuple[float, float]:
        voltage = self._parse_float(self.hv_voltage_var.get().strip(), "tensão HV")
        voltage_limit = self._parse_float(
            self.hv_limit_var.get().strip(), "limite de tensão"
        )
        if not -1000.0 <= voltage <= 1000.0:
            raise ValueError("Tensão da fonte deve estar entre -1000 e +1000 V.")
        if not 0.0 <= voltage_limit <= 1000.0:
            raise ValueError("Limite de tensão deve estar entre 0 e 1000 V.")
        if abs(voltage) > voltage_limit:
            raise ValueError(
                "O limite deve ser maior ou igual ao módulo da tensão desejada."
            )
        return voltage, voltage_limit

    def _update_hv_preview(self, *_args: Any) -> None:
        try:
            voltage, voltage_limit = self._parse_hv_values()
            selected_range = 100 if abs(voltage) <= 100 else 1000
            effective_limit = min(float(selected_range), voltage_limit)
            self.hv_range_preview_var.set(
                f"Faixa automática: {selected_range} V · limite efetivo: {effective_limit:g} V"
            )
        except (TypeError, ValueError):
            self.hv_range_preview_var.set("Revise a tensão e o limite informados.")

    def _hv_operation_allowed(self) -> bool:
        if not self._require_connected():
            return False
        if self._hv_operation_running:
            return False
        if self.acq_running or self.controller.state in (
            ControllerState.ARMED,
            ControllerState.ACQUIRING,
        ):
            messagebox.showwarning(
                "Alta tensão",
                "Durante a aquisição somente o botão DESLIGAR AGORA fica disponível.",
            )
            return False
        if self._scpi_running:
            messagebox.showwarning(
                "Alta tensão", "Aguarde o lote do console SCPI terminar."
            )
            return False
        return True

    def apply_hv_configuration(self) -> None:
        if not self._hv_operation_allowed():
            return
        try:
            voltage, voltage_limit = self._parse_hv_values()
        except Exception as error:
            self._show_error("Configuração de alta tensão inválida", error)
            return
        self._start_hv_operation("configure", voltage, voltage_limit)

    def enable_high_voltage(self) -> None:
        if not self._hv_operation_allowed():
            return
        if not all(
            variable.get()
            for variable in (
                self.hv_circuit_checked_var,
                self.hv_fixture_checked_var,
                self.hv_area_checked_var,
            )
        ):
            messagebox.showwarning(
                "Alta tensão", "Marque as três confirmações de segurança."
            )
            return
        try:
            voltage, voltage_limit = self._parse_hv_values()
        except Exception as error:
            self._show_error("Configuração de alta tensão inválida", error)
            return
        if not messagebox.askyesno(
            "Confirmar ALTA TENSÃO",
            f"O Keithley será programado para {voltage:+g} V, com limite absoluto "
            f"de {voltage_limit:g} V, e a saída será colocada em OPERATE.\n\n"
            "O controlador consultará o interlock imediatamente antes de ativar, "
            "mas essa consulta não substitui a inspeção física. A fonte possui "
            "limite nominal de 10 mA na faixa 100 V ou 1 mA na faixa 1000 V.\n\n"
            "Ativar alta tensão agora?",
            icon="warning",
        ):
            return
        self._start_hv_operation("enable", voltage, voltage_limit)

    def disable_high_voltage(self) -> None:
        if not self.controller.connected or self._hv_operation_running:
            return
        self._start_hv_operation("disable")

    def refresh_hv_status(self, silent: bool = False) -> None:
        if not self.controller.connected:
            if not silent:
                self._require_connected()
            return
        if self._hv_operation_running or self.acq_running or self._scpi_running:
            return
        self._start_hv_operation("refresh", silent=silent)

    def _start_hv_operation(
        self,
        action: str,
        voltage: Optional[float] = None,
        voltage_limit: Optional[float] = None,
        silent: bool = False,
    ) -> None:
        self._hv_operation_running = True
        self._update_hv_controls()
        descriptions = {
            "configure": "Aplicando configuração com a saída em standby...",
            "enable": "Validando interlock e ativando alta tensão...",
            "disable": "Desligando a fonte de alta tensão...",
            "refresh": "Lendo o estado da fonte...",
        }
        self.status_var.set(descriptions[action])
        threading.Thread(
            target=self._hv_worker,
            args=(action, voltage, voltage_limit, silent),
            daemon=True,
        ).start()

    def _hv_worker(
        self,
        action: str,
        voltage: Optional[float],
        voltage_limit: Optional[float],
        silent: bool,
    ) -> None:
        try:
            if action == "configure":
                status = self.controller.configure_voltage_source(
                    float(voltage), float(voltage_limit)
                )
            elif action == "enable":
                self.controller.configure_voltage_source(
                    float(voltage), float(voltage_limit)
                )
                status = self.controller.enable_voltage_source(
                    physical_interlock_confirmed=True
                )
            elif action == "disable":
                status = self.controller.disable_voltage_source()
            else:
                status = self.controller.get_voltage_source_status()
            self._hv_queue.put(("success", (action, status, silent)))
        except Exception as error:
            self._hv_queue.put(("error", (action, error, silent)))

    def _poll_hv_queue(self) -> None:
        try:
            while True:
                kind, payload = self._hv_queue.get_nowait()
                action, value, silent = payload
                self._hv_operation_running = False
                if kind == "success":
                    status = value
                    self._apply_hv_status(status)
                    interlock_trip = (
                        status.output_enabled and not status.interlock_ok
                    )
                    messages = {
                        "configure": "Configuração aplicada; saída confirmada em standby.",
                        "enable": "ALTA TENSÃO ATIVA.",
                        "disable": "Fonte desligada e nível programado zerado.",
                        "refresh": "Estado da fonte atualizado.",
                    }
                    self.status_var.set(messages[action])
                    if action == "enable":
                        for variable in (
                            self.hv_circuit_checked_var,
                            self.hv_fixture_checked_var,
                            self.hv_area_checked_var,
                        ):
                            variable.set(False)
                    if not silent:
                        self._append_hv_log(
                            f"[{datetime.now():%H:%M:%S}] {messages[action]}\n"
                        )
                    if interlock_trip:
                        self._append_hv_log(
                            f"[{datetime.now():%H:%M:%S}] Interlock aberto com "
                            "saída ativa; desligamento automático solicitado.\n"
                        )
                        self._start_hv_operation("disable")
                else:
                    error = value
                    logging.error("Operação HV %s falhou: %s", action, error)
                    self.status_var.set(f"Falha na operação de alta tensão: {error}")
                    self._append_hv_log(
                        f"[{datetime.now():%H:%M:%S}] ERRO: {error}\n"
                    )
                    if not silent:
                        messagebox.showerror(
                            "Alta tensão", f"A operação falhou.\n\nDetalhe:\n{error}"
                        )
                self._update_hv_controls()
        except Empty:
            pass
        self.root.after(100, self._poll_hv_queue)

    def _auto_refresh_hv_status(self) -> None:
        if (
            self.controller.connected
            and self.controller.hv_enabled
            and not self._hv_operation_running
            and not self.acq_running
            and not self._scpi_running
        ):
            self.refresh_hv_status(silent=True)
        self.root.after(2000, self._auto_refresh_hv_status)

    def _apply_hv_status(self, status: VoltageSourceStatus) -> None:
        self._hv_last_status = status
        self.hv_readback_var.set(f"{status.voltage:+.6g} V")
        self.hv_range_readback_var.set(f"{status.range_value:g} V")
        limit_state = "habilitado" if status.limit_enabled else "DESABILITADO"
        self.hv_limit_readback_var.set(
            f"{status.voltage_limit:g} V ({limit_state})"
        )
        self.hv_interlock_var.set(
            "fechado OU cabo ausente*" if status.interlock_ok else "ABERTO / bloqueado"
        )
        self.hv_compliance_var.set(
            "ATINGIDA" if status.compliance else "normal"
        )
        if status.output_enabled and not status.interlock_ok:
            self.hv_indicator.config(
                text="INTERLOCK ABERTO — DESLIGUE AGORA", bg="#7f1d1d", fg="white"
            )
        elif status.output_enabled and status.compliance:
            self.hv_indicator.config(
                text="HV ATIVA — LIMITADA POR CORRENTE", bg="#c2410c", fg="white"
            )
        elif status.output_enabled:
            self.hv_indicator.config(
                text="ALTA TENSÃO ATIVA", bg="#b91c1c", fg="white"
            )
        else:
            self.hv_indicator.config(
                text="STANDBY — SAÍDA DESLIGADA", bg="#166534", fg="white"
            )

    def _reset_hv_display(self) -> None:
        self._hv_last_status = None
        self.hv_indicator.config(text="DESCONECTADO", bg="#5f6368", fg="white")
        for variable in (
            self.hv_readback_var,
            self.hv_range_readback_var,
            self.hv_limit_readback_var,
            self.hv_interlock_var,
            self.hv_compliance_var,
        ):
            variable.set("—")

    def _update_hv_controls(self) -> None:
        connected = self.controller.connected
        idle_state = self.controller.state in (
            ControllerState.SAFE,
            ControllerState.CONFIGURED,
        )
        free = (
            connected
            and idle_state
            and not self._hv_operation_running
            and not self.acq_running
            and not self._scpi_running
        )
        confirmed = all(
            variable.get()
            for variable in (
                self.hv_circuit_checked_var,
                self.hv_fixture_checked_var,
                self.hv_area_checked_var,
            )
        )
        entry_state = "normal" if free else "disabled"
        self.hv_voltage_entry.configure(state=entry_state)
        self.hv_limit_entry.configure(state=entry_state)
        self.hv_apply_button.configure(state="normal" if free else "disabled")
        self.hv_enable_button.configure(
            state="normal" if free and confirmed else "disabled"
        )
        can_refresh = (
            connected
            and not self._hv_operation_running
            and not self.acq_running
            and not self._scpi_running
        )
        self.hv_refresh_button.configure(
            state="normal" if can_refresh else "disabled"
        )
        self.hv_disable_button.configure(
            state="normal"
            if connected and not self._hv_operation_running
            else "disabled"
        )

    def _append_hv_log(self, text: str) -> None:
        self._append_log(self.hv_log, text)

    def _on_function_change(self, _evt: tk.Event) -> None:
        raw = self.acq_function_var.get()
        for scpi, _label, _short in FUNCTIONS:
            if raw.startswith(scpi):
                self.acq_function_var.set(scpi)
                break

    def _on_mode_change(self, *_args: Any) -> None:
        mode = self.acq_mode_var.get()
        state = "normal" if mode == "buffer" else "disabled"
        for child in self.mode_dependent.winfo_children():
            if isinstance(child, ttk.Entry):
                child.configure(state=state)

    def choose_file(self) -> None:
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Todos", "*.*")],
            initialdir=str(DATA_DIR),
            initialfile=Path(self.acq_file_var.get()).name,
        )
        if path:
            self.acq_file_var.set(path)

    def _set_connected_state(self, connected: bool) -> None:
        self.connect_button.config(state="normal" if not connected else "disabled")
        self.disconnect_button.config(state="normal" if connected else "disabled")
        if not connected:
            self._applied_acq_signature = None
            self._reset_hv_display()
        self._update_hv_controls()

    def _append_log(self, widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.insert("end", text)
        widget.see("end")
        widget.configure(state="disabled")

    def _append_connection_log(self, text: str) -> None:
        self._append_log(self.connection_log, text)

    def _append_scpi_log(self, text: str) -> None:
        self._append_log(self.scpi_output, text)

    def search_resources(self) -> None:
        if pyvisa is None:
            messagebox.showerror("PyVISA", "PyVISA não está instalado.")
            return
        try:
            self.status_var.set("Buscando recursos VISA...")
            self.root.update_idletasks()
            resources = self.controller.list_resources()
            gpib = [r for r in resources if r.upper().startswith("GPIB")]
            self.resource_combo["values"] = gpib
            if gpib:
                if self.resource_var.get() not in gpib:
                    self.resource_var.set(gpib[0])
                self.status_var.set(f"{len(gpib)} recurso(s) GPIB encontrado(s).")
            else:
                self.status_var.set(
                    "Nenhum recurso GPIB. Confirme a interface no NI MAX e informe o endereço manualmente."
                )
        except Exception as error:
            self._show_error("Falha ao listar recursos", error)

    def connect(self) -> None:
        if pyvisa is None:
            messagebox.showerror("PyVISA", "PyVISA não está instalado.")
            return
        resource = self.resource_var.get().strip()
        if not resource:
            messagebox.showwarning("Recurso VISA", "Informe um recurso VISA válido.")
            return
        self.status_var.set("Conectando...")
        self.root.update_idletasks()
        try:
            identity = self.controller.connect(resource, timeout_ms=DEFAULT_TIMEOUT_MS)
            self.identity_var.set(identity)
            self.status_var.set("Conectado e identificado com sucesso.")
            self._append_connection_log(f"[{datetime.now():%H:%M:%S}] {resource}\n{identity}\n")
            logging.info("Conectado: %s", identity)
            self._set_connected_state(True)
            errors = self.controller.check_errors()
            if errors:
                self._append_connection_log("Erros iniciais: " + " | ".join(errors) + "\n")
            self.refresh_hv_status(silent=True)
        except Exception as error:
            self.controller.disconnect(silent=True)
            self._show_error("Falha de conexão. Verifique cabo, alimentação, endereço GPIB e NI MAX", error)

    def disconnect_prompt(self) -> None:
        if self.acq_running:
            messagebox.showwarning("Desconectar", "Pare a aquisição antes de desconectar.")
            return
        if self._hv_operation_running:
            messagebox.showwarning(
                "Desconectar", "Aguarde a operação de alta tensão terminar."
            )
            return
        self.controller.disconnect()
        self.identity_var.set("Nenhum instrumento identificado.")
        self.status_var.set("Desconectado.")
        self._set_connected_state(False)

    def query_idn(self) -> None:
        if not self._require_connected():
            return
        try:
            response = self.controller.identify()
            self.identity_var.set(response)
            self._append_connection_log(f"*IDN? -> {response}\n")
        except Exception as error:
            self._show_error("*IDN? falhou", error)

    def query_options(self) -> None:
        if not self._require_connected():
            return
        try:
            response = self.controller.options()
            self._append_connection_log(f"*OPT? -> {response}\n")
        except Exception as error:
            self._show_error("*OPT? falhou", error)

    def safe_clear_status(self) -> None:
        if not self._require_connected():
            return
        try:
            self.controller.clear_status()
            self._append_connection_log("*CLS enviado.\n")
        except Exception as error:
            self._show_error("*CLS falhou", error)

    def safe_self_test(self) -> None:
        if not self._require_connected():
            return
        try:
            result = self.controller.query("*TST?")
            self._append_connection_log(f"*TST? -> {result}\n")
        except Exception as error:
            self._show_error("*TST? falhou", error)

    def safe_check_errors(self) -> None:
        if not self._require_connected():
            return
        try:
            errors = self.controller.check_errors()
            self._append_connection_log(
                ("Sem erros na fila.\n" if not errors else "Erros:\n" + "\n".join(errors) + "\n")
            )
        except Exception as error:
            self._show_error(":SYSTem:ERRor? falhou", error)

    def safe_reset(self) -> None:
        if not self._require_connected():
            return
        if not messagebox.askyesno(
            "Confirmar *RST",
            "*RST restaura defaults de fábrica do 6517A (função, faixa, "
            "trigger, buffer e fonte V standby). Confirme apenas com o "
            "instrumento em estado seguro.",
        ):
            return
        try:
            self.controller.reset()
            self._applied_acq_signature = None
            self._append_connection_log("*RST enviado; fila de erros drenada.\n")
            self.refresh_hv_status(silent=True)
        except Exception as error:
            self._show_error("*RST falhou", error)

    def query_current_range(self) -> None:
        if not self._require_connected():
            return
        short = short_function(self.acq_function_var.get().split(" ")[0])
        try:
            value = self.controller.query(f":SENSe:{short}:RANGe:UPPer?")
            self.acq_range_var.set(value)
            self.acq_auto_range_var.set(False)
            self._append_connection_log(f":SENSe:{short}:RANGe:UPPer? -> {value}\n")
        except Exception as error:
            self._show_error("Falha lendo a faixa atual", error)

    def apply_configuration_only(self) -> None:
        if not self._require_connected():
            return
        if self.acq_running:
            messagebox.showwarning("Aquisição", "Pare a aquisição em andamento antes de reconfigurar.")
            return
        if self.controller.hv_enabled:
            messagebox.showwarning(
                "Aquisição",
                "Desligue a alta tensão antes de alterar a configuração de medição.",
            )
            return
        try:
            self._apply_measurement_config()
            self.acq_status_var.set("Configuração aplicada. Pronto para iniciar aquisição.")
        except Exception as error:
            self._show_error("Falha aplicando configuração", error)

    def _parse_float(self, value: str, field: str) -> float:
        try:
            return float(value)
        except ValueError:
            raise ValueError(f"Valor inválido para {field}: '{value}'")

    def _current_measurement_signature(self) -> Tuple[Any, ...]:
        function = self.acq_function_var.get().split(" ")[0]
        auto_range = bool(self.acq_auto_range_var.get())
        range_value = None if auto_range else self._parse_float(
            self.acq_range_var.get(), "faixa"
        )
        return (
            function,
            auto_range,
            range_value,
            self._parse_float(self.acq_nplc_var.get(), "NPLCycles"),
            int(self.acq_digits_var.get()),
        )

    def _apply_measurement_config(self) -> None:
        signature = self._current_measurement_signature()
        function, auto_range, range_value, nplc, digits = signature
        self.controller.configure_measurement(
            function=function,
            range_value=range_value,
            auto_range=auto_range,
            nplc=nplc,
            digits=digits,
        )
        self.controller.set_format_elements("READing,TSTamp")
        self._applied_acq_signature = signature

    def start_acquisition(self) -> None:
        if not self._require_connected():
            return
        if self.acq_running:
            messagebox.showwarning("Aquisição", "Já existe aquisição em andamento.")
            return
        if self._scpi_running:
            messagebox.showwarning(
                "Aquisição", "Aguarde o lote do console SCPI terminar."
            )
            return
        if self._hv_operation_running:
            messagebox.showwarning(
                "Aquisição", "Aguarde a operação de alta tensão terminar."
            )
            return
        try:
            measurement_signature = self._current_measurement_signature()
        except Exception as error:
            self._show_error("Configuração de aquisição inválida", error)
            return
        hv_active = self.controller.hv_enabled
        if hv_active and self._applied_acq_signature != measurement_signature:
            messagebox.showwarning(
                "Aquisição com alta tensão",
                "A configuração de medição exibida ainda não foi aplicada.\n\n"
                "Desligue a alta tensão, clique em 'Aplicar config (sem ler)' e "
                "só então volte a ativar a fonte. Isso evita reconfigurar o "
                "eletrômetro com a saída energizada.",
            )
            return
        path = Path(self.acq_file_var.get().strip())
        if path.parent and not path.parent.exists():
            messagebox.showwarning("CSV", "A pasta do arquivo CSV não existe.")
            return
        mode = self.acq_mode_var.get()
        function = self.acq_function_var.get().split(" ")[0]
        confirm_text = (
            f"Iniciar aquisição no modo '{mode}' para o arquivo:\n{path}\n\n"
            "Esta operação configura função, faixa, integração e (no modo "
            "buffer) trigger/buffer do 6517A/6517B. Confirma?"
        )
        if hv_active:
            voltage_text = (
                f"{self._hv_last_status.voltage:+g} V"
                if self._hv_last_status is not None
                else "nível lido anteriormente"
            )
            confirm_text += (
                f"\n\nALTA TENSÃO ATIVA ({voltage_text}): a aquisição utilizará "
                "a configuração já aplicada e, ao terminar ou falhar, executará "
                "a parada segura, desligando a fonte."
            )
        if short_function(function) == "CHARge":
            confirm_text += (
                "\n\nCARGA: deixe o circuito ainda desconectado da entrada; "
                "mantenha somente o cabo de teste preparado com a entrada aberta. "
                "O programa compensará o Zero Check Hop antes de pedir a conexão."
            )
        if not messagebox.askyesno("Confirmar aquisição", confirm_text):
            return
        fh: Any = None
        try:
            # Reserva e valida o arquivo antes de alterar qualquer estado do
            # instrumento. O modo exclusivo também evita sobrescrever ensaios.
            fh = open(path, "x", newline="", encoding="ascii")
            fh.write("valor,tempo,status\n")
            fh.flush()
            os.fsync(fh.fileno())
            if not hv_active:
                self._apply_measurement_config()
            if short_function(function) == "CHARge":
                if not messagebox.askokcancel(
                    "Carga — conectar circuito",
                    "Zero Check Hop compensado por REL.\n\n"
                    "Agora conecte o circuito à entrada do Keithley conforme o "
                    "procedimento físico do ensaio. Clique OK somente quando a "
                    "conexão estiver pronta; Cancelar executará a parada segura.",
                    icon="warning",
                ):
                    self.controller.safe_shutdown()
                    self._applied_acq_signature = None
                    fh.close()
                    self.acq_status_var.set(
                        "Aquisição de carga cancelada; instrumento em estado seguro."
                    )
                    self.refresh_hv_status(silent=True)
                    return
            for child in self.results_tree.get_children():
                self.results_tree.delete(child)
            self.clear_chart()
            if self.chart_unit_var is not None:
                self.chart_unit_var.set(
                    self._unit_for_function(self.acq_function_var.get().split(" ")[0])
                )
                self._refresh_chart_axes()
            self._acq_stop.clear()
            if mode == "buffer":
                self._prepare_buffer()
            else:
                self._prepare_continuous()
        except Exception as error:
            if fh is not None:
                fh.close()
            try:
                if self.controller.connected:
                    self.controller.safe_shutdown()
                    self._applied_acq_signature = None
            except Exception:
                logging.exception("Parada segura falhou após erro de preparação.")
            self._show_error("Falha preparando aquisição", error)
            self.refresh_hv_status(silent=True)
            return

        self.acq_running = True
        self.acq_start_button.config(state="disabled")
        self.acq_stop_button.config(state="normal")
        self.scpi_send_button.config(state="disabled")
        self._update_hv_controls()
        self.acq_status_var.set("Aquisição em andamento...")
        self._acq_thread = threading.Thread(
            target=self._acquisition_worker,
            args=(mode, fh),
            daemon=True,
        )
        self._acq_thread.start()

    def _prepare_continuous(self) -> None:
        # LIVE é iniciado uma única vez; o laço consulta somente DATA:FRESH?.
        self.controller.start_live()

    def _prepare_buffer(self) -> None:
        points = int(self.acq_points_var.get())
        if points < 1:
            raise ValueError("Número de pontos deve ser no mínimo 1.")
        interval = self._parse_float(self.acq_interval_var.get(), "intervalo")
        self.controller.prepare_buffer(
            points=points,
            source="TIMer",
            timer_interval=interval,
            delay=None,
        )

    def _acquisition_worker(self, mode: str, fh: Any) -> None:
        acquisition_error: Optional[Exception] = None
        try:
            if mode == "buffer":
                self._run_buffer_acquisition(fh)
            else:
                self._run_continuous_acquisition(fh)
        except AcquisitionCancelled:
            logging.info("Aquisição cancelada pelo usuário.")
        except Exception as error:
            acquisition_error = error
            logging.exception("Aquisição falhou.")
            try:
                _write_csv_row(fh, "nan", "0", ReadingStatus.ERROR.value)
                fh.flush()
            except Exception:
                logging.exception("Não foi possível registrar ERROR no CSV.")
        finally:
            try:
                if self.controller.connected:
                    self.controller.safe_shutdown()
            except Exception as error:
                logging.exception("Parada segura pós-aquisição falhou.")
                if acquisition_error is None:
                    acquisition_error = error
            try:
                fh.close()
            except Exception:
                logging.exception("Falha fechando CSV de aquisição.")
        if acquisition_error is None:
            self._acq_queue.put(("done", None))
        else:
            self._acq_queue.put(("error", str(acquisition_error)))

    def _run_continuous_acquisition(self, fh: Any) -> None:
        idx = 0
        while not self._acq_stop.is_set():
            try:
                reading = self.controller.read_live()
            except VisaIOError as error:
                raise RuntimeError(f":SENSe:DATA:FRESh? falhou: {error}") from error
            _write_csv_row(
                fh, reading.raw_value, reading.raw_timestamp, reading.status.value
            )
            fh.flush()
            idx += 1
            self._acq_queue.put(("point", (idx, reading)))

    def _run_buffer_acquisition(self, fh: Any) -> None:
        points = int(self.acq_points_var.get())
        interval = float(self.acq_interval_var.get())
        nplc = float(self.acq_nplc_var.get())
        conversion_s = nplc / 50.0 + 0.05
        estimated_s = max(points * max(interval, conversion_s) + 10.0, 10.0)
        self.controller.start_buffer()
        self.controller.wait_buffer_complete(
            timeout_s=estimated_s,
            stop_event=self._acq_stop,
            poll_interval_s=min(max(interval / 4.0, 0.05), 0.5),
        )
        readings = self.controller.read_buffer_readings()
        idx = 0
        for reading in readings:
            _write_csv_row(
                fh, reading.raw_value, reading.raw_timestamp, reading.status.value
            )
            fh.flush()
            idx += 1
            self._acq_queue.put(("point", (idx, reading)))

    def request_stop_acquisition(self) -> None:
        if not self.acq_running:
            return
        self._acq_stop.set()
        threading.Thread(target=self._abort_for_stop, daemon=True).start()
        self.acq_status_var.set("Parando...")

    def _abort_for_stop(self) -> None:
        try:
            if self.controller.connected:
                self.controller.abort()
        except Exception:
            logging.exception("Abort solicitado mas falhou.")

    def abort_instrument(self) -> None:
        if not self._require_connected():
            return
        try:
            self.controller.abort()
            self.acq_status_var.set(":ABORt enviado ao instrumento.")
        except Exception as error:
            self._show_error("Falha enviando :ABORt", error)

    def _poll_acq_queue(self) -> None:
        try:
            while True:
                kind, payload = self._acq_queue.get_nowait()
                if kind == "done":
                    self.acq_running = False
                    self._applied_acq_signature = None
                    self.acq_start_button.config(state="normal")
                    self.acq_stop_button.config(state="disabled")
                    self.scpi_send_button.config(state="normal")
                    self.acq_status_var.set("Aquisição concluída e CSV salvo.")
                    self._update_hv_controls()
                    self.refresh_hv_status(silent=True)
                elif kind == "error":
                    self.acq_running = False
                    self._applied_acq_signature = None
                    self.acq_start_button.config(state="normal")
                    self.acq_stop_button.config(state="disabled")
                    self.scpi_send_button.config(state="normal")
                    self.acq_status_var.set(f"Erro de aquisição: {payload}")
                    self._update_hv_controls()
                    self.refresh_hv_status(silent=True)
                    messagebox.showerror("Aquisição", f"Erro: {payload}")
                elif kind == "point":
                    idx, reading = payload
                    self.results_tree.insert(
                        "", "end", iid=str(idx),
                        values=(
                            idx,
                            f"{reading.value:.6E}",
                            f"{reading.timestamp:.6f}",
                            reading.status.value,
                        ),
                    )
                    self.results_tree.see(str(idx))
                    if reading.status == ReadingStatus.OK:
                        self._append_chart_point(reading.value, reading.timestamp)
        except Empty:
            pass
        self.root.after(100, self._poll_acq_queue)

    def send_scpi(self) -> None:
        if not self._require_connected():
            return
        if self.acq_running or self.controller.state in (
            ControllerState.ARMED,
            ControllerState.ACQUIRING,
        ):
            messagebox.showwarning(
                "Console SCPI",
                "O console permanece bloqueado durante aquisições.",
            )
            return
        if self._scpi_running:
            return
        if self._hv_operation_running:
            messagebox.showwarning(
                "Console SCPI", "Aguarde a operação de alta tensão terminar."
            )
            return
        commands = [
            line.strip() for line in self.scpi_input.get("1.0", "end").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if not commands:
            return
        dangerous = [cmd for cmd in commands if is_dangerous_command(cmd)]
        allow_hv = False
        if dangerous:
            descriptions = [
                f"{finding.command}: {finding.description}"
                for cmd in dangerous
                for finding in analyze_scpi_safety(cmd)
            ]
            list_txt = "\n".join(descriptions)
            ok = messagebox.askyesno(
                "Comando perigoso — confirmar",
                "O lote abaixo pode configurar ou ATIVAR a fonte de tensão do "
                "Keithley 6517A/6517B (até 1000 V). O controlador também validará "
                "o interlock antes de qualquer ativação.\n\n"
                f"{list_txt}\n\n"
                "Confirme apenas com o circuito de carga devidamente conectado e "
                "revisado. Prosseguir?",
                icon="warning",
            )
            if not ok:
                self._safe_scpi_log("Envio cancelado pelo usuário (comando perigoso).\n")
                return
            allow_hv = True
        self._scpi_running = True
        self.scpi_send_button.config(state="disabled")
        self._update_hv_controls()
        threading.Thread(
            target=self._send_scpi_worker,
            args=(commands, bool(self.scpi_check_errors_var.get()), allow_hv),
            daemon=True,
        ).start()

    def _send_scpi_worker(
        self, commands: List[str], check_errors: bool, allow_hv: bool
    ) -> None:
        try:
            results = self.controller.execute_scpi_batch(
                commands,
                check_errors=check_errors,
                allow_hv=allow_hv,
            )
            for cmd, response, errors in results:
                if response is not None:
                    self._safe_scpi_log(f"Q> {cmd}\n< {response}\n")
                else:
                    self._safe_scpi_log(f"W> {cmd}  (OK)\n")
                if errors:
                    self._safe_scpi_log("  Erros: " + " | ".join(errors) + "\n")
        except Exception as error:
            self._safe_scpi_log(f"ERRO no lote SCPI: {error}\n")
        finally:
            self.root.after(0, self._finish_scpi_worker)

    def _finish_scpi_worker(self) -> None:
        self._scpi_running = False
        self.scpi_send_button.config(
            state="disabled" if self.acq_running else "normal"
        )
        self._update_hv_controls()
        if not self.acq_running:
            self.refresh_hv_status(silent=True)

    def send_quick(self, cmd: str, is_query: bool) -> None:
        if not self._require_connected():
            return
        self.scpi_input.delete("1.0", "end")
        self.scpi_input.insert("1.0", cmd)
        self.send_scpi()

    def clear_scpi_output(self) -> None:
        self.scpi_output.configure(state="normal")
        self.scpi_output.delete("1.0", "end")
        self.scpi_output.configure(state="disabled")

    def _safe_scpi_log(self, text: str) -> None:
        self.root.after(0, lambda: self._append_scpi_log(text))

    def _require_connected(self) -> bool:
        if not self.controller.connected:
            messagebox.showwarning("Comunicação", "Conecte ao instrumento primeiro.")
            return False
        return True

    def _show_error(self, context: str, error: Exception) -> None:
        self.status_var.set(context)
        logging.error("%s: %s", context, error)
        messagebox.showerror("Erro", f"{context}.\n\nDetalhe:\n{error}")

    # ---- Gráfico em tempo real (matplotlib) ----

    def _build_realtime_chart(self, parent: tk.Widget) -> None:
        if matplotlib is None or Figure is None or FigureCanvasTkAgg is None:
            ttk.Label(
                parent,
                text="matplotlib não está instalado.\n"
                "Execute: python -m pip install --user matplotlib numpy",
                foreground="#8a0000",
                wraplength=400,
            ).grid(row=0, column=0, sticky="nsew")
            self.chart_canvas = None
            self.chart_ax = None
            self.chart_line = None
            self.chart_times: List[float] = []
            self.chart_values: List[float] = []
            self.chart_unit_var = None
            return

        self.chart_unit_var = tk.StringVar(value="V")
        self.chart_times: List[float] = []
        self.chart_values: List[float] = []

        fig = Figure(figsize=(9, 6), dpi=100, facecolor="#fafafa")
        self.chart_fig = fig
        ax = fig.add_subplot(111)
        fig.tight_layout(pad=1.2)
        ax.set_xlabel("Tempo (s)")
        ax.set_ylabel("Valor")
        ax.set_title("Aquisição em tempo real", fontsize=10)
        ax.grid(True, linestyle=":", alpha=0.5)
        (self.chart_line,) = ax.plot([], [], marker="o", markersize=3, linewidth=1.2)
        self.chart_ax = ax
        ax.tick_params(labelsize=8)

        canvas = FigureCanvasTkAgg(fig, master=parent)
        self.chart_canvas = canvas
        canvas.draw()
        canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        controls = ttk.Frame(parent)
        controls.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        ttk.Button(controls, text="Limpar", command=self.clear_chart).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Label(controls, text="Unidade eixo Y:").grid(row=0, column=1, padx=(0, 4))
        ttk.Entry(controls, textvariable=self.chart_unit_var, width=10).grid(
            row=0, column=2
        )
        ttk.Button(controls, text="Atualizar eixo", command=self._refresh_chart_axes).grid(
            row=0, column=3, padx=(6, 0)
        )
        self.chart_autoscale_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            controls, text="Auto-escala Y", variable=self.chart_autoscale_var
        ).grid(row=0, column=4, padx=(12, 0))

    def _unit_for_function(self, function: str) -> str:
        short = short_function(function)
        return {
            "VOLTage": "V",
            "CURRent": "A",
            "RESistance": "Ω",
            "CHARge": "C",
        }.get(short, "V")

    def clear_chart(self) -> None:
        self.chart_times = []
        self.chart_values = []
        if self.chart_canvas is None:
            return
        self.chart_line.set_data([], [])
        self.chart_ax.relim()
        self.chart_ax.autoscale_view()
        self.chart_canvas.draw_idle()

    def _refresh_chart_axes(self) -> None:
        if self.chart_canvas is None or self.chart_ax is None:
            return
        self.chart_ax.set_ylabel(self.chart_unit_var.get() or "Valor")
        self.chart_canvas.draw_idle()

    def _append_chart_point(self, value: float, ts: float) -> None:
        if self.chart_canvas is None or self.chart_ax is None:
            return
        if value != value:  # NaN
            value = 0.0
        self.chart_times.append(ts)
        self.chart_values.append(value)
        self.chart_line.set_data(self.chart_times, self.chart_values)
        if self.chart_autoscale_var.get():
            self.chart_ax.relim()
            self.chart_ax.autoscale_view()
        else:
            self.chart_ax.relim()
        self.chart_canvas.draw_idle()

    def _restore_state(self) -> None:
        self._on_mode_change()
        self.acq_function_var.set(FUNCTIONS[0][0])
        self._update_hv_preview()
        self._update_hv_controls()

    def close_application(self) -> None:
        if self.acq_running:
            if not messagebox.askyesno(
                "Sair", "Existe aquisição em andamento. Parar e sair mesmo assim?"
            ):
                return
            self._acq_stop.set()
            try:
                self.controller.abort()
            except Exception:
                pass
            if self._acq_thread is not None:
                self._acq_thread.join(timeout=3)
        self.controller.shutdown()
        logging.info("Aplicação encerrada.")
        self.root.destroy()


def _write_csv_row(
    fh: Any, raw_value: str, raw_ts: str, status: str = ReadingStatus.OK.value
) -> None:
    """Grava uma linha do CSV preservando o formato bruto enviado pelo
    instrumento e acrescentando a classificação determinística da amostra."""
    fh.write(f"{raw_value.strip()},{raw_ts.strip()},{status.strip()}\n")


def main() -> int:
    if pyvisa is None:
        print(
            "PyVISA nao instalado. Execute: python -m pip install -r requirements.txt",
            file=sys.stderr,
        )
    root = tk.Tk()
    ttk.Style().theme_use("vista" if "vista" in ttk.Style().theme_names() else "clam")
    KeithleyControlApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
