"""Professional CustomTkinter UI with a VS Code-inspired left navigation.

This is the only Python module that contains visual widgets.  It emits semantic
``AppIntent`` objects and renders immutable ``ViewState`` snapshots; it never
imports PyVISA, the instrument driver, threading, queue or storage services.
"""

from __future__ import annotations

import math
import os
import tkinter as tk
import ctypes
import webbrowser
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Dict, List, Optional, Tuple

import customtkinter as ctk
from PIL import Image

try:
    from .keithley_6517_contracts import AppIntent, IntentKind, PageId, ViewState
except ImportError:  # pragma: no cover - direct src execution compatibility
    from keithley_6517_contracts import AppIntent, IntentKind, PageId, ViewState


COLORS = {
    "window": ("#E8EDF2", "#181818"),
    "workspace": ("#F2F5F8", "#1E1E1E"),
    "sidebar": ("#E5EAF0", "#252526"),
    "surface": ("#FFFFFF", "#252526"),
    "surface_alt": ("#E9EEF3", "#202020"),
    "hover": ("#D7DFE8", "#2A2D2E"),
    "selection": ("#CFE6F7", "#37373D"),
    "border": ("#B7C1CC", "#3C3C3C"),
    "text": ("#17212B", "#CCCCCC"),
    "muted": ("#4D5C6A", "#A6A6A6"),
    "accent": ("#0067B8", "#007ACC"),
    "accent_hover": ("#005596", "#1688D4"),
    "danger": ("#B42318", "#F14C4C"),
    "danger_hover": ("#8F1D14", "#C93C3C"),
    "danger_bg": ("#FCECEA", "#3B1518"),
    "warning": ("#754B00", "#CCA700"),
    "success": ("#087A55", "#4EC9B0"),
}


PAGE_TITLES = {
    PageId.DASHBOARD: "Painel",
    PageId.CONNECTION: "Conexão",
    PageId.MEASUREMENT: "Medição",
    PageId.ACQUISITION: "Aquisição",
    PageId.HIGH_VOLTAGE: "Alta tensão",
    PageId.SCPI: "Console SCPI",
    PageId.LOGS: "Registros",
    PageId.SETTINGS: "Configurações",
}


NAV_ITEMS = (
    (PageId.DASHBOARD, "home", "Painel"),
    (PageId.CONNECTION, "usb-symbol", "Conexão"),
    (PageId.MEASUREMENT, "pulse", "Medição"),
    (PageId.ACQUISITION, "graph-line", "Aquisição"),
    (PageId.HIGH_VOLTAGE, "symbol-event", "Alta tensão"),
    (PageId.SCPI, "terminal-compact", "Console SCPI"),
    (PageId.LOGS, "history", "Registros"),
)

FOOTER_NAV_ITEMS = (
    (PageId.SETTINGS, "gear-compact", "Configurações"),
)

ICON_ROOT = Path(__file__).resolve().parents[1] / "assets" / "icons" / "codicons" / "png"
BRANDING_ROOT = Path(__file__).resolve().parents[1] / "assets" / "branding"
APP_ICON_PATH = BRANDING_ROOT / "keithley_6517_spectrum_icon.png"
APP_ICON_ICO_PATH = BRANDING_ROOT / "keithley_6517_spectrum_icon.ico"
WINDOWS_APP_ID = "RADInstruments.Keithley6517.ControlStudio"
RAD_WEBSITE = "https://radinstruments.com.br/"


FUNCTIONS = {
    "Corrente DC": "CURRent:DC",
    "Tensão DC": "VOLTage:DC",
    "Resistência": "RESistance",
    "Carga": "CHARge",
}

FUNCTION_FILENAME_PARTS = {
    "Corrente DC": "corrente_dc",
    "Tensão DC": "tensao_dc",
    "Resistência": "resistencia",
    "Carga": "carga",
}


class _Tooltip:
    """Small delayed tooltip for icon-only activity-bar actions."""

    def __init__(self, widget: Any, text: str, delay_ms: int = 450) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id: Optional[str] = None
        self._window: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def _schedule(self, _event: Any = None) -> None:
        self.hide()
        self._after_id = self.widget.after(self.delay_ms, self.show)

    def show(self) -> None:
        self._after_id = None
        if not self.widget.winfo_exists() or self._window is not None:
            return
        window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.wm_attributes("-topmost", True)
        label = tk.Label(
            window,
            text=self.text,
            justify="left",
            background="#252526",
            foreground="#F0F0F0",
            relief="solid",
            borderwidth=1,
            padx=9,
            pady=5,
            font=("Segoe UI", 9),
        )
        label.pack()
        window.update_idletasks()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 8
        y = self.widget.winfo_rooty() + (self.widget.winfo_height() - window.winfo_height()) // 2
        window.wm_geometry(f"+{x}+{max(0, y)}")
        self._window = window

    def hide(self, _event: Any = None) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None


class Keithley6517UI(ctk.CTk):
    """Single CTk root that renders the complete operator interface."""

    POLL_MS = 50

    @staticmethod
    def _work_area() -> Tuple[int, int, int, int]:
        """Return the Windows desktop work area, excluding the taskbar."""

        rect = wintypes.RECT()
        try:
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                return rect.left, rect.top, rect.right, rect.bottom
        except (AttributeError, OSError):  # pragma: no cover - non-Windows fallback
            pass
        return 0, 0, 0, 0

    def __init__(self, coordinator: Any) -> None:
        initial_state: ViewState = coordinator.state
        ctk.set_appearance_mode(initial_state.theme)
        ctk.set_default_color_theme("blue")
        self._set_windows_app_id()
        super().__init__(fg_color=COLORS["window"])
        self._set_app_icon()
        self.coordinator = coordinator
        self.current_state = initial_state
        self.title("Keithley 6517 Control Studio")
        work_left, work_top, work_right, work_bottom = self._work_area()
        if work_right <= work_left or work_bottom <= work_top:
            work_right = self.winfo_screenwidth()
            work_bottom = self.winfo_screenheight()
        available_width = work_right - work_left
        available_height = work_bottom - work_top
        window_width = max(900, min(1440, available_width - 48))
        window_height = max(560, min(900, available_height - 88))
        left = work_left + max(0, (available_width - window_width) // 2)
        top = work_top + max(0, (available_height - window_height) // 2)
        self.geometry(f"{window_width}x{window_height}+{left}+{top}")
        self.minsize(min(1024, window_width), min(720, window_height))
        self.protocol("WM_DELETE_WINDOW", self._request_close)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self._pages: Dict[PageId, ctk.CTkFrame] = {}
        self._nav_buttons: Dict[PageId, ctk.CTkButton] = {}
        self._nav_rows: Dict[PageId, ctk.CTkFrame] = {}
        self._visible_page: Optional[PageId] = None
        self._nav_icons = self._load_nav_icons()
        self._about_icon = self._load_icon("info")
        self._branding_images = self._load_branding_images()
        self._tooltips: List[_Tooltip] = []
        self._about_window: Optional[ctk.CTkToplevel] = None
        self._closing_requested = False
        self._preview_after_id: Optional[str] = None
        self._poll_after_id: Optional[str] = None
        self._last_table_index = 0
        self._last_log_revision = -1
        self._last_output: Tuple[str, ...] = ()
        self._chart_points: List[Tuple[float, float, float, float, int]] = []
        self._chart_hover_index: Optional[int] = None

        self._build_sidebar()
        self._build_header()
        self._build_workspace()
        self._build_status_bar()
        self._show_page(initial_state.active_page, dispatch=False)
        self._apply_theme(initial_state.theme)
        self._render(initial_state)
        self._poll_after_id = self.after(self.POLL_MS, self._poll_application)

    @staticmethod
    def _set_windows_app_id() -> None:
        """Prevent Windows from grouping the UI under Python's default icon."""

        if os.name != "nt":
            return
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
        except (AttributeError, OSError):
            pass

    def _set_app_icon(self) -> None:
        """Use the RAD/Keithley artwork as the native application icon."""

        self._app_icon = None
        if not APP_ICON_PATH.is_file():
            return
        try:
            self._app_icon = tk.PhotoImage(file=str(APP_ICON_PATH))
            self.iconphoto(True, self._app_icon)
        except tk.TclError:
            # Keep the UI usable if a packaged build is missing the optional asset.
            self._app_icon = None
        if os.name == "nt" and APP_ICON_ICO_PATH.is_file():
            try:
                self.iconbitmap(default=str(APP_ICON_ICO_PATH))
            except tk.TclError:
                pass

    def run(self) -> None:
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after_idle(self.attributes, "-topmost", False)
        self.focus_force()
        self.mainloop()

    def destroy(self) -> None:
        self._cancel_own_timers()
        super().destroy()

    # ------------------------------------------------------------------ shell
    def _load_nav_icons(self) -> Dict[PageId, Tuple[ctk.CTkImage, ctk.CTkImage]]:
        icons: Dict[PageId, Tuple[ctk.CTkImage, ctk.CTkImage]] = {}
        for page, icon_name, _label in NAV_ITEMS + FOOTER_NAV_ITEMS:
            icons[page] = self._load_icon(icon_name)
        return icons

    @staticmethod
    def _load_icon(icon_name: str) -> Tuple[ctk.CTkImage, ctk.CTkImage]:
        def load(variant: str) -> Image.Image:
            path = ICON_ROOT / variant / f"{icon_name}.png"
            with Image.open(path) as source:
                return source.convert("RGBA").copy()

        normal = ctk.CTkImage(
            light_image=load("light"),
            dark_image=load("dark"),
            size=(22, 22),
        )
        active = ctk.CTkImage(
            light_image=load("light_active"),
            dark_image=load("dark_active"),
            size=(22, 22),
        )
        return normal, active

    @staticmethod
    def _load_branding_images() -> Dict[str, ctk.CTkImage]:
        path = BRANDING_ROOT / "radinstruments_250x37.png"
        with Image.open(path) as source:
            light_wordmark = source.convert("RGBA").copy()

        qr_path = BRANDING_ROOT / "radinstruments_qr.png"
        with Image.open(qr_path) as source:
            qr_code = source.convert("RGB").copy()

        # Keep the original RAD artwork in both themes.  Recoloring dark
        # pixels also recolored the wordmark's black outline, making the
        # letters look blown out on the dark About dialog.
        dark_wordmark = light_wordmark.copy()
        brand_mark = light_wordmark.crop((0, 0, min(39, light_wordmark.width), light_wordmark.height))
        return {
            "mark": ctk.CTkImage(
                light_image=brand_mark,
                dark_image=brand_mark,
                size=(34, 32),
            ),
            "wordmark": ctk.CTkImage(
                light_image=light_wordmark,
                dark_image=dark_wordmark,
                size=(225, 33),
            ),
            "qr": ctk.CTkImage(
                light_image=qr_code,
                dark_image=qr_code,
                size=(104, 104),
            ),
        }

    def _build_sidebar(self) -> None:
        self.sidebar = ctk.CTkFrame(
            self,
            width=56,
            corner_radius=0,
            fg_color=COLORS["sidebar"],
            border_width=1,
            border_color=COLORS["border"],
        )
        self.sidebar.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_columnconfigure(0, minsize=54)

        brand = ctk.CTkFrame(self.sidebar, fg_color="transparent", width=54, height=58)
        brand.grid(row=0, column=0, sticky="ew", pady=(2, 8))
        brand.grid_propagate(False)
        self.brand_mark = ctk.CTkLabel(
            brand,
            text="",
            image=self._branding_images["mark"],
            width=36,
            height=36,
            fg_color="transparent",
        )
        self.brand_mark.place(relx=0.5, rely=0.5, anchor="center")
        self._tooltips.append(_Tooltip(self.brand_mark, "RADinstruments"))

        self.nav_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent", width=54)
        self.nav_frame.grid(row=1, column=0, sticky="new")
        self.nav_frame.grid_columnconfigure(0, minsize=54)
        for row, item_data in enumerate(NAV_ITEMS):
            self._build_nav_item(self.nav_frame, row, *item_data)

        self.sidebar.grid_rowconfigure(2, weight=1)
        footer = ctk.CTkFrame(self.sidebar, fg_color="transparent", width=54)
        footer.grid(row=3, column=0, sticky="sew", pady=(8, 10))
        footer.grid_columnconfigure(0, minsize=54)
        for row, item_data in enumerate(FOOTER_NAV_ITEMS):
            self._build_nav_item(footer, row, *item_data)
        self._build_about_item(footer, len(FOOTER_NAV_ITEMS))

    def _build_about_item(self, parent: ctk.CTkFrame, row: int) -> None:
        item = ctk.CTkFrame(
            parent,
            width=54,
            height=48,
            corner_radius=0,
            fg_color="transparent",
        )
        item.grid(row=row, column=0, sticky="ew")
        item.grid_propagate(False)
        button = ctk.CTkButton(
            item,
            text="",
            image=self._about_icon[0],
            width=48,
            height=44,
            corner_radius=3,
            fg_color="transparent",
            hover_color=COLORS["hover"],
            command=self._open_about,
        )
        button.place(x=4, rely=0.5, anchor="w")
        self._tooltips.append(_Tooltip(button, "Sobre a RADinstruments"))

    def _open_about(self) -> None:
        if self._about_window is not None and self._about_window.winfo_exists():
            self._about_window.deiconify()
            self._about_window.lift()
            self._about_window.focus_force()
            return

        dialog = ctk.CTkToplevel(self, fg_color=COLORS["workspace"])
        self._about_window = dialog
        if self._app_icon is not None:
            dialog.iconphoto(False, self._app_icon)
        if os.name == "nt" and APP_ICON_ICO_PATH.is_file():
            try:
                dialog.iconbitmap(str(APP_ICON_ICO_PATH))
            except tk.TclError:
                pass
        dialog.title("Sobre — Keithley 6517 Control Studio")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.protocol("WM_DELETE_WINDOW", self._close_about)
        dialog.grid_columnconfigure(0, weight=1)

        card = ctk.CTkFrame(
            dialog,
            width=460,
            corner_radius=10,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["border"],
        )
        card.grid(row=0, column=0, sticky="nsew", padx=20, pady=20)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card,
            text="",
            image=self._branding_images["wordmark"],
        ).grid(row=0, column=0, padx=36, pady=(28, 18))
        ctk.CTkLabel(
            card,
            text="Keithley 6517 Control Studio",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=1, column=0, padx=28)
        ctk.CTkLabel(
            card,
            text="Interface de controle para os eletrômetros Keithley 6517A e 6517B",
            width=360,
            wraplength=360,
            justify="center",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=12),
        ).grid(row=2, column=0, padx=28, pady=(6, 20))

        site_button = ctk.CTkButton(
            card,
            text="Visitar radinstruments.com.br  ↗",
            width=260,
            height=38,
            corner_radius=5,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._open_rad_website,
        )
        site_button.grid(row=3, column=0, padx=28, pady=(0, 18))
        self._tooltips.append(_Tooltip(site_button, RAD_WEBSITE))

        ctk.CTkLabel(
            card,
            text="© 2026 RADinstruments Ltda.",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=11),
        ).grid(row=4, column=0, padx=28, pady=(0, 8))
        ctk.CTkButton(
            card,
            text="Fechar",
            width=88,
            height=30,
            fg_color="transparent",
            border_width=1,
            border_color=COLORS["border"],
            text_color=COLORS["text"],
            hover_color=COLORS["hover"],
            command=self._close_about,
        ).grid(row=5, column=0, padx=28, pady=(0, 24))

        dialog.update_idletasks()
        width = dialog.winfo_reqwidth()
        height = dialog.winfo_reqheight()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - width) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - height) // 2)
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        dialog.grab_set()
        dialog.focus_force()

    def _close_about(self) -> None:
        dialog = self._about_window
        self._about_window = None
        if dialog is not None and dialog.winfo_exists():
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            dialog.destroy()

    def _open_rad_website(self) -> None:
        try:
            opened = webbrowser.open_new_tab(RAD_WEBSITE)
        except webbrowser.Error:
            opened = False
        if not opened:
            messagebox.showerror(
                "Site da RADinstruments",
                f"Não foi possível abrir o navegador.\n\nAcesse: {RAD_WEBSITE}",
                parent=self._about_window or self,
            )

    def _build_nav_item(
        self,
        parent: ctk.CTkFrame,
        row: int,
        page: PageId,
        _icon_name: str,
        label: str,
    ) -> None:
        item = ctk.CTkFrame(
            parent,
            width=54,
            height=48,
            corner_radius=0,
            fg_color="transparent",
        )
        item.grid(row=row, column=0, sticky="ew")
        item.grid_propagate(False)
        indicator = ctk.CTkFrame(
            item,
            width=3,
            height=28,
            corner_radius=0,
            fg_color="transparent",
        )
        indicator.place(x=0, rely=0.5, anchor="w")
        button = ctk.CTkButton(
            item,
            text="",
            image=self._nav_icons[page][0],
            width=48,
            height=44,
            corner_radius=3,
            fg_color="transparent",
            hover_color=COLORS["hover"],
            text_color=COLORS["text"],
            command=lambda selected=page: self._show_page(selected),
        )
        button.place(x=4, rely=0.5, anchor="w")
        self._nav_rows[page] = indicator
        self._nav_buttons[page] = button
        self._tooltips.append(_Tooltip(button, label))

    def _build_header(self) -> None:
        self.header = ctk.CTkFrame(
            self,
            height=56,
            corner_radius=0,
            fg_color=COLORS["workspace"],
            border_width=0,
        )
        self.header.grid(row=0, column=1, sticky="ew")
        self.header.grid_propagate(False)
        self.header.grid_columnconfigure(1, weight=1)
        self.page_title = ctk.CTkLabel(
            self.header,
            text="Painel",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLORS["text"],
        )
        self.page_title.grid(row=0, column=0, padx=(24, 20), pady=14)

        identity = ctk.CTkFrame(self.header, fg_color="transparent")
        identity.grid(row=0, column=1, sticky="e", padx=10)
        self.header_model = ctk.CTkLabel(
            identity, text="Esperado AUTO  ·  Detectado —", text_color=COLORS["muted"]
        )
        self.header_model.grid(row=0, column=0, padx=12)
        self.connection_pill = ctk.CTkLabel(
            identity,
            text="● Desconectado",
            height=28,
            corner_radius=14,
            fg_color=COLORS["surface_alt"],
            text_color=COLORS["muted"],
            padx=12,
        )
        self.connection_pill.grid(row=0, column=1, padx=4)

        self.emergency_header = ctk.CTkButton(
            self.header,
            text="DESLIGAR HV AGORA",
            width=174,
            height=38,
            corner_radius=4,
            fg_color=COLORS["danger"],
            hover_color=COLORS["danger_hover"],
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._disable_hv,
        )

    def _build_workspace(self) -> None:
        self.workspace = ctk.CTkFrame(
            self, corner_radius=0, fg_color=COLORS["workspace"]
        )
        self.workspace.grid(row=1, column=1, sticky="nsew")
        self.workspace.grid_rowconfigure(0, weight=1)
        self.workspace.grid_columnconfigure(0, weight=1)

        self._build_dashboard_page()
        self._build_connection_page()
        self._build_measurement_page()
        self._build_acquisition_page()
        self._build_hv_page()
        self._build_scpi_page()
        self._build_logs_page()
        self._build_settings_page()

        # Every page is fully constructed up front, but overlapping native Tk
        # descendants must never remain mapped together.  Mapping a single
        # shell prevents controls from the incoming page leaking over the old
        # one while Windows resolves child-window z-order.
        for frame in self._pages.values():
            page_shell = getattr(frame, "_parent_frame", frame)
            page_shell.grid_remove()

    def _build_status_bar(self) -> None:
        self.status_bar = ctk.CTkFrame(
            self, height=28, corner_radius=0, fg_color=COLORS["accent"]
        )
        self.status_bar.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.status_bar.grid_propagate(False)
        self.status_bar.grid_columnconfigure(1, weight=1)
        self.status_left = ctk.CTkLabel(
            self.status_bar,
            text="VISA: desconectado",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=11),
        )
        self.status_left.grid(row=0, column=0, padx=12)
        self.status_message = ctk.CTkLabel(
            self.status_bar,
            text="Pronto",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=11),
        )
        self.status_message.grid(row=0, column=1, sticky="w", padx=12)
        self.status_right = ctk.CTkLabel(
            self.status_bar,
            text="HV: standby  ·  Erros: 0",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=11),
        )
        self.status_right.grid(row=0, column=2, padx=12)

    # --------------------------------------------------------------- components
    def _page(self, page_id: PageId) -> ctk.CTkScrollableFrame:
        frame = ctk.CTkScrollableFrame(
            self.workspace,
            corner_radius=0,
            fg_color=COLORS["workspace"],
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["muted"],
        )
        frame.grid(row=0, column=0, sticky="nsew")
        frame.grid_columnconfigure(0, weight=1)
        self._pages[page_id] = frame
        return frame

    def _section_header(self, parent: Any, title: str, subtitle: str, row: int = 0) -> None:
        # The persistent top header already identifies the active page.  Keep
        # only a small breathing space here instead of repeating title/subtitle.
        del title, subtitle
        spacer = ctk.CTkFrame(parent, height=8, fg_color="transparent")
        spacer.grid(row=row, column=0, sticky="ew")
        spacer.grid_propagate(False)

    def _card(self, parent: Any, row: int, column: int = 0, colspan: int = 1) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            parent,
            corner_radius=6,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["border"],
        )
        card.grid(
            row=row,
            column=column,
            columnspan=colspan,
            sticky="nsew",
            padx=24,
            pady=8,
        )
        return card

    @staticmethod
    def _label(parent: Any, text: str, row: int, column: int = 0, **kwargs: Any) -> ctk.CTkLabel:
        label = ctk.CTkLabel(parent, text=text, anchor="w", **kwargs)
        label.grid(row=row, column=column, sticky="w", padx=16, pady=(10, 2))
        return label

    # ------------------------------------------------------------------- pages
    def _build_dashboard_page(self) -> None:
        page = self._page(PageId.DASHBOARD)
        self._section_header(
            page,
            "Visão geral",
            "Estado operacional, identidade do instrumento e leitura mais recente.",
        )
        grid = ctk.CTkFrame(page, fg_color="transparent")
        grid.grid(row=1, column=0, sticky="ew", padx=18)
        grid.grid_rowconfigure(0, minsize=104)
        for column in range(3):
            grid.grid_columnconfigure(column, weight=1, uniform="dashboard-summary")
        cards: List[ctk.CTkFrame] = []
        for column in range(3):
            card = ctk.CTkFrame(
                grid,
                fg_color=COLORS["surface"],
                border_width=1,
                border_color=COLORS["border"],
                corner_radius=6,
                height=104,
            )
            card.grid(row=0, column=column, sticky="nsew", padx=6, pady=8)
            card.grid_propagate(False)
            card.grid_columnconfigure(0, weight=1)
            cards.append(card)
        self.dashboard_connection = self._label(cards[0], "Desconectado", 1, font=ctk.CTkFont(size=20, weight="bold"), text_color=COLORS["text"])
        self._label(cards[0], "CONEXÃO", 0, text_color=COLORS["muted"], font=ctk.CTkFont(size=11, weight="bold"))
        self.dashboard_model = self._label(cards[1], "—", 1, font=ctk.CTkFont(size=20, weight="bold"), text_color=COLORS["text"])
        self._label(cards[1], "MODELO DETECTADO", 0, text_color=COLORS["muted"], font=ctk.CTkFont(size=11, weight="bold"))
        self.dashboard_hv = self._label(cards[2], "Standby", 1, font=ctk.CTkFont(size=20, weight="bold"), text_color=COLORS["success"])
        self._label(cards[2], "FONTE DE TENSÃO", 0, text_color=COLORS["muted"], font=ctk.CTkFont(size=11, weight="bold"))

        reading_card = self._card(page, 2)
        reading_card.grid_columnconfigure(0, weight=1)
        self._label(reading_card, "ÚLTIMA LEITURA", 0, text_color=COLORS["muted"], font=ctk.CTkFont(size=11, weight="bold"))
        self.dashboard_reading = ctk.CTkLabel(
            reading_card,
            text="—",
            anchor="w",
            font=ctk.CTkFont(family="Cascadia Mono", size=34, weight="bold"),
            text_color=COLORS["accent"],
        )
        self.dashboard_reading.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 4))
        self.dashboard_reading_meta = ctk.CTkLabel(
            reading_card, text="Nenhuma leitura", anchor="w", text_color=COLORS["muted"]
        )
        self.dashboard_reading_meta.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 16))

        safety = self._card(page, 3)
        safety.grid_columnconfigure(0, weight=1)
        self._label(safety, "RESUMO DE SEGURANÇA", 0, text_color=COLORS["muted"], font=ctk.CTkFont(size=11, weight="bold"))
        self.dashboard_safety = ctk.CTkLabel(
            safety,
            text="Fonte em standby. O estado do interlock ainda não foi consultado.",
            anchor="w",
            justify="left",
            wraplength=900,
            text_color=COLORS["text"],
        )
        self.dashboard_safety.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 16))

        brand_grid = ctk.CTkFrame(page, fg_color="transparent")
        brand_grid.grid(row=4, column=0, sticky="ew", padx=18)
        brand_grid.grid_columnconfigure(0, weight=1, uniform="dashboard-branding")
        brand_grid.grid_columnconfigure(1, weight=1, uniform="dashboard-branding")

        brand_card = ctk.CTkFrame(
            brand_grid,
            height=164,
            corner_radius=6,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["border"],
        )
        brand_card.grid(row=0, column=0, sticky="nsew", padx=(6, 4), pady=8)
        brand_card.grid_propagate(False)
        brand_card.grid_columnconfigure(1, weight=1)
        brand_card.grid_rowconfigure(0, weight=1)
        ctk.CTkFrame(
            brand_card,
            width=3,
            height=92,
            corner_radius=0,
            fg_color=COLORS["accent"],
        ).grid(row=0, column=0, sticky="w", padx=(16, 16))
        brand_content = ctk.CTkFrame(brand_card, fg_color="transparent")
        brand_content.grid(row=0, column=1, sticky="ew", padx=(0, 18))
        ctk.CTkLabel(
            brand_content,
            text="",
            image=self._branding_images["wordmark"],
            anchor="w",
        ).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            brand_content,
            text="Tecnologia nacional para medição e monitoramento de radiações ionizantes.",
            anchor="w",
            justify="left",
            wraplength=520,
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", fill="x", pady=(0, 4))
        ctk.CTkLabel(
            brand_content,
            text="Conheça produtos, serviços e suporte da RADinstruments.",
            anchor="w",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w")

        qr_card = ctk.CTkFrame(
            brand_grid,
            height=164,
            corner_radius=6,
            fg_color=COLORS["surface"],
            border_width=1,
            border_color=COLORS["border"],
        )
        qr_card.grid(row=0, column=1, sticky="nsew", padx=(4, 6), pady=8)
        qr_card.grid_propagate(False)
        qr_card.grid_columnconfigure(0, weight=1, uniform="qr-content")
        qr_card.grid_columnconfigure(1, weight=1, uniform="qr-content")
        qr_card.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(
            qr_card,
            text="SITE DA RAD",
            anchor="w",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=11, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 2))
        ctk.CTkLabel(
            qr_card,
            text="",
            image=self._branding_images["qr"],
        ).grid(row=1, column=0, rowspan=2, sticky="nsew", padx=(16, 12), pady=(0, 12))
        ctk.CTkLabel(
            qr_card,
            text="Aponte a câmera do celular\npara acessar o site.",
            anchor="w",
            justify="left",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=11),
        ).grid(row=1, column=1, sticky="sw", padx=(0, 12), pady=(0, 4))
        ctk.CTkButton(
            qr_card,
            text="Abrir site  ↗",
            width=112,
            height=28,
            corner_radius=4,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._open_rad_website,
        ).grid(row=2, column=1, sticky="nw", padx=(0, 12), pady=(0, 12))

    def _build_connection_page(self) -> None:
        page = self._page(PageId.CONNECTION)
        self._section_header(
            page,
            "Conexão VISA",
            "Selecione o modelo esperado e confirme a identidade real por *IDN?. Divergências são bloqueadas.",
        )
        card = self._card(page, 1)
        card.grid_columnconfigure(1, weight=1)
        self._label(card, "Modelo esperado", 0, text_color=COLORS["muted"])
        self.expected_model = ctk.CTkOptionMenu(
            card,
            values=["Automático", "Keithley 6517A", "Keithley 6517B"],
            command=self._expected_model_changed,
            width=230,
        )
        self.expected_model.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 14))
        self._label(card, "Recurso VISA/GPIB", 0, 1, text_color=COLORS["muted"])
        self.resource_combo = ctk.CTkComboBox(
            card, values=["GPIB0::26::INSTR"], width=360
        )
        self.resource_combo.set("GPIB0::26::INSTR")
        self.resource_combo.grid(row=1, column=1, sticky="ew", padx=16, pady=(0, 14))
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(2, 16))
        self.discover_button = ctk.CTkButton(actions, text="Buscar recursos", command=self._discover)
        self.discover_button.grid(row=0, column=0, padx=(0, 8))
        self.connect_button = ctk.CTkButton(actions, text="Conectar", command=self._connect)
        self.connect_button.grid(row=0, column=1, padx=8)
        self.disconnect_button = ctk.CTkButton(
            actions,
            text="Desconectar com segurança",
            fg_color="transparent",
            border_width=1,
            border_color=COLORS["border"],
            text_color=COLORS["text"],
            hover_color=COLORS["hover"],
            command=self._disconnect,
        )
        self.disconnect_button.grid(row=0, column=2, padx=8)

        identity = self._card(page, 2)
        identity.grid_columnconfigure(1, weight=1)
        self.identity_labels: Dict[str, ctk.CTkLabel] = {}
        for row, (key, title) in enumerate((
            ("idn", "Identificação"),
            ("serial", "Número de série"),
            ("firmware", "Firmware"),
            ("scpi", "Versão SCPI"),
            ("state", "Estado do controlador"),
        )):
            ctk.CTkLabel(identity, text=title, text_color=COLORS["muted"], anchor="w").grid(row=row, column=0, sticky="w", padx=16, pady=9)
            label = ctk.CTkLabel(identity, text="—", text_color=COLORS["text"], anchor="w", font=ctk.CTkFont(family="Cascadia Mono", size=12))
            label.grid(row=row, column=1, sticky="ew", padx=16, pady=9)
            self.identity_labels[key] = label

    def _build_measurement_page(self) -> None:
        page = self._page(PageId.MEASUREMENT)
        self._section_header(
            page,
            "Configuração de medição",
            "Toda receita define função, faixa, NPLC, dígitos e formato; resistência também fixa AUTO ou MAN.",
        )
        card = self._card(page, 1)
        for column in range(3):
            card.grid_columnconfigure(column, weight=1)
        self._label(card, "Função", 0, 0, text_color=COLORS["muted"])
        self.function_option = ctk.CTkOptionMenu(card, values=list(FUNCTIONS), command=self._function_changed)
        self.function_option.set("Corrente DC")
        self.function_option.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 12))
        self._label(card, "NPLC (0,01 a 10)", 0, 1, text_color=COLORS["muted"])
        self.nplc_entry = ctk.CTkEntry(card, placeholder_text="1.0")
        self.nplc_entry.insert(0, "1.0")
        self.nplc_entry.grid(row=1, column=1, sticky="ew", padx=16, pady=(0, 12))
        self._label(card, "Dígitos (4 a 7)", 0, 2, text_color=COLORS["muted"])
        self.digits_option = ctk.CTkOptionMenu(card, values=["4", "5", "6", "7"])
        self.digits_option.set("6")
        self.digits_option.grid(row=1, column=2, sticky="ew", padx=16, pady=(0, 12))

        self.auto_range_var = tk.BooleanVar(value=True)
        self.auto_range_switch = ctk.CTkSwitch(
            card, text="Autorange", variable=self.auto_range_var, command=self._range_mode_changed
        )
        self.auto_range_switch.grid(row=2, column=0, sticky="w", padx=16, pady=12)
        self._label(card, "Faixa manual", 2, 1, text_color=COLORS["muted"])
        self.range_entry = ctk.CTkEntry(card, placeholder_text="Valor em unidade SI")
        self.range_entry.grid(row=3, column=1, sticky="ew", padx=16, pady=(0, 12))
        self._label(card, "Fonte para resistência", 2, 2, text_color=COLORS["muted"])
        self.resistance_mode = ctk.CTkOptionMenu(card, values=["AUTO", "MAN"])
        self.resistance_mode.set("AUTO")
        self.resistance_mode.grid(row=3, column=2, sticky="ew", padx=16, pady=(0, 12))
        self.resistance_notice = ctk.CTkLabel(
            card,
            text="AUTO seleciona a fonte automática. MAN exige análise adicional da fonte de resistência.",
            anchor="w",
            text_color=COLORS["warning"],
        )
        self.resistance_notice.grid(row=4, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 12))
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", padx=16, pady=(4, 16))
        self.apply_measurement_button = ctk.CTkButton(actions, text="Aplicar e confirmar", command=self._configure_measurement)
        self.apply_measurement_button.grid(row=0, column=0, padx=(0, 8))
        self.one_shot_button = ctk.CTkButton(
            actions,
            text="Leitura única",
            fg_color="transparent",
            border_width=1,
            border_color=COLORS["border"],
            text_color=COLORS["text"],
            hover_color=COLORS["hover"],
            command=lambda: self._emit(IntentKind.ONE_SHOT),
        )
        self.one_shot_button.grid(row=0, column=1, padx=8)
        self._range_mode_changed()
        self._function_changed("Corrente DC")

    def _build_acquisition_page(self) -> None:
        page = self._page(PageId.ACQUISITION)
        self._section_header(
            page,
            "Aquisição",
            "LIVE usa DATA:FRESh?; BUFFER usa o armazenamento interno com limite vindo do perfil detectado.",
        )
        controls = self._card(page, 1)
        for column in range(4):
            controls.grid_columnconfigure(column, weight=1)
        labels = ("Modo", "Tempo de leitura (s)", "Intervalo (s)")
        for column, label in enumerate(labels):
            self._label(controls, label, 0, column, text_color=COLORS["muted"])
        self._acquisition_name_automatic = True
        self.acquisition_mode = ctk.CTkOptionMenu(
            controls,
            values=["LIVE", "BUFFER"],
            command=self._acquisition_mode_changed,
        )
        self.acquisition_mode.set("LIVE")
        self.acquisition_mode.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 12))
        self.duration_entry = ctk.CTkEntry(controls)
        self.duration_entry.insert(0, "10")
        self.duration_entry.grid(row=1, column=1, sticky="ew", padx=16, pady=(0, 12))
        self.interval_option = ctk.CTkOptionMenu(
            controls,
            values=["{0:.1f}".format(value / 10.0) for value in range(1, 11)],
        )
        self.interval_option.set("0.1")
        self.interval_option.grid(row=1, column=2, sticky="ew", padx=16, pady=(0, 12))
        self._label(controls, "Nome do arquivo CSV", 2, 0, text_color=COLORS["muted"])
        self.file_entry = ctk.CTkEntry(
            controls,
            placeholder_text="Nome automático conforme o modo; você pode editar",
        )
        self.file_entry.grid(row=3, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 12))
        self.file_entry.bind("<KeyRelease>", self._acquisition_filename_edited, add="+")
        self._set_default_acquisition_filename("LIVE")
        ctk.CTkButton(
            controls,
            text="Abrir pasta",
            width=110,
            command=self._open_data_folder,
        ).grid(row=3, column=3, sticky="e", padx=16, pady=(0, 12))
        actions = ctk.CTkFrame(controls, fg_color="transparent")
        actions.grid(row=4, column=0, columnspan=4, sticky="ew", padx=16, pady=(4, 16))
        self.start_acquisition_button = ctk.CTkButton(actions, text="Iniciar aquisição", command=self._start_acquisition)
        self.start_acquisition_button.grid(row=0, column=0, padx=(0, 8))
        self.stop_acquisition_button = ctk.CTkButton(
            actions,
            text="Parar",
            fg_color=COLORS["danger"],
            hover_color=COLORS["danger_hover"],
            command=lambda: self._emit(IntentKind.STOP_ACQUISITION),
        )
        self.stop_acquisition_button.grid(row=0, column=1, padx=8)
        self.acquisition_progress = ctk.CTkProgressBar(actions, width=260)
        self.acquisition_progress.set(0)
        self.acquisition_progress.grid(row=0, column=2, padx=16)
        self.acquisition_counter = ctk.CTkLabel(actions, text="0 / 0", text_color=COLORS["muted"])
        self.acquisition_counter.grid(row=0, column=3)

        data_card = self._card(page, 2)
        data_card.grid_columnconfigure(0, weight=1)
        data_card.grid_columnconfigure(1, weight=1)
        self.chart = tk.Canvas(data_card, height=280, highlightthickness=0)
        self.chart.grid(row=0, column=0, sticky="nsew", padx=(16, 8), pady=16)
        self.chart.bind("<Motion>", self._chart_motion, add="+")
        self.chart.bind("<Leave>", self._chart_leave, add="+")
        self.reading_tree = ttk.Treeview(
            data_card,
            columns=("sample", "time", "value", "unit"),
            show="headings",
            height=12,
        )
        for key, title, width in (
            ("sample", "#", 55),
            ("time", "Tempo (s)", 100),
            ("value", "Valor", 145),
            ("unit", "Un.", 55),
        ):
            self.reading_tree.heading(key, text=title)
            self.reading_tree.column(key, width=width, anchor="e" if key in ("time", "value") else "center")
        self.reading_tree.grid(row=0, column=1, sticky="nsew", padx=(8, 16), pady=16)

    def _build_hv_page(self) -> None:
        page = self._page(PageId.HIGH_VOLTAGE)
        self._section_header(
            page,
            "Fonte de alta tensão",
            "A fonte pode aplicar até ±1000 V. A consulta do interlock é ambígua e não substitui verificação física.",
        )
        config = self._card(page, 1)
        for column in range(3):
            config.grid_columnconfigure(column, weight=1)
        self._label(config, "Tensão desejada (V)", 0, 0, text_color=COLORS["muted"])
        self.hv_voltage_entry = ctk.CTkEntry(config)
        self.hv_voltage_entry.insert(0, "0")
        self.hv_voltage_entry.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 12))
        self.hv_voltage_entry.bind("<KeyRelease>", self._update_hv_preview)
        self._label(config, "Limite absoluto (V)", 0, 1, text_color=COLORS["muted"])
        self.hv_limit_entry = ctk.CTkEntry(config)
        self.hv_limit_entry.insert(0, "100")
        self.hv_limit_entry.grid(row=1, column=1, sticky="ew", padx=16, pady=(0, 12))
        self._label(config, "Faixa / corrente nominal", 0, 2, text_color=COLORS["muted"])
        self.hv_preview = ctk.CTkLabel(config, text="100 V / 10 mA", anchor="w", text_color=COLORS["accent"], font=ctk.CTkFont(size=15, weight="bold"))
        self.hv_preview.grid(row=1, column=2, sticky="ew", padx=16, pady=(0, 12))
        self.configure_hv_button = ctk.CTkButton(config, text="Aplicar em standby", command=self._configure_hv)
        self.configure_hv_button.grid(row=2, column=0, padx=16, pady=(4, 16), sticky="w")
        ctk.CTkButton(config, text="Atualizar estado", fg_color="transparent", border_width=1, border_color=COLORS["border"], text_color=COLORS["text"], hover_color=COLORS["hover"], command=lambda: self._emit(IntentKind.REFRESH_HV)).grid(row=2, column=1, padx=16, pady=(4, 16), sticky="w")

        checks = self._card(page, 2)
        checks.grid_columnconfigure(0, weight=1)
        self._label(checks, "CONFIRMAÇÃO FÍSICA — NÃO É PERSISTIDA", 0, text_color=COLORS["warning"], font=ctk.CTkFont(size=11, weight="bold"))
        self.hv_check_vars = [tk.BooleanVar(value=False) for _index in range(3)]
        texts = (
            "O circuito, a carga, cabos e limites foram revisados.",
            "Confirmei fisicamente cabo de interlock, fixture fechada e tampa.",
            "A área está controlada e tenho autorização para energizar.",
        )
        for row, (variable, text) in enumerate(zip(self.hv_check_vars, texts), start=1):
            ctk.CTkCheckBox(checks, text=text, variable=variable).grid(row=row, column=0, sticky="w", padx=16, pady=7)
        hv_actions = ctk.CTkFrame(checks, fg_color="transparent")
        hv_actions.grid(row=4, column=0, sticky="ew", padx=16, pady=16)
        self.enable_hv_button = ctk.CTkButton(
            hv_actions,
            text="HABILITAR ALTA TENSÃO",
            height=48,
            fg_color=COLORS["danger"],
            hover_color=COLORS["danger_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._enable_hv,
        )
        self.enable_hv_button.grid(row=0, column=0, padx=(0, 12))
        self.disable_hv_button = ctk.CTkButton(
            hv_actions,
            text="DESLIGAR HV AGORA",
            height=48,
            fg_color="#7A0000",
            hover_color="#A00000",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._disable_hv,
        )
        self.disable_hv_button.grid(row=0, column=1)

        status = self._card(page, 3)
        status.grid_columnconfigure(1, weight=1)
        self.hv_status_labels: Dict[str, ctk.CTkLabel] = {}
        for row, (key, title) in enumerate((
            ("state", "Saída"),
            ("voltage", "Nível programado"),
            ("range", "Faixa e corrente nominal"),
            ("limit", "Limite de tensão"),
            ("interlock", "Interlock"),
            ("compliance", "Compliance"),
        )):
            ctk.CTkLabel(status, text=title, text_color=COLORS["muted"], anchor="w").grid(row=row, column=0, sticky="w", padx=16, pady=8)
            label = ctk.CTkLabel(status, text="—", text_color=COLORS["text"], anchor="w")
            label.grid(row=row, column=1, sticky="ew", padx=16, pady=8)
            self.hv_status_labels[key] = label

    def _build_scpi_page(self) -> None:
        page = self._page(PageId.SCPI)
        self._section_header(
            page,
            "Console SCPI seguro",
            "Aceita uma unidade por transação, valida o modelo e mantém a fila de respostas sincronizada.",
        )
        editor = self._card(page, 1)
        editor.grid_columnconfigure(0, weight=1)
        self.scpi_input = ctk.CTkTextbox(editor, height=100, font=ctk.CTkFont(family="Cascadia Mono", size=13))
        self.scpi_input.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        self.scpi_input.insert("1.0", "*IDN?")
        self.scpi_input.bind("<KeyRelease>", self._scpi_edited)
        editor_actions = ctk.CTkFrame(editor, fg_color="transparent")
        editor_actions.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 16))
        ctk.CTkButton(editor_actions, text="Pré-analisar", command=self._preview_scpi).grid(row=0, column=0, padx=(0, 8))
        self.execute_scpi_button = ctk.CTkButton(editor_actions, text="Executar validado", command=self._execute_scpi)
        self.execute_scpi_button.grid(row=0, column=1, padx=8)
        ctk.CTkButton(editor_actions, text="Limpar saída", fg_color="transparent", border_width=1, border_color=COLORS["border"], text_color=COLORS["text"], hover_color=COLORS["hover"], command=lambda: self._emit(IntentKind.CLEAR_SCPI_OUTPUT)).grid(row=0, column=2, padx=8)

        preview = self._card(page, 2)
        preview.grid_columnconfigure(0, weight=1)
        self.scpi_preview_title = ctk.CTkLabel(preview, text="Aguardando pré-análise", anchor="w", font=ctk.CTkFont(size=14, weight="bold"), text_color=COLORS["muted"])
        self.scpi_preview_title.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        self.scpi_preview_detail = ctk.CTkLabel(preview, text="", anchor="w", justify="left", wraplength=900, text_color=COLORS["text"])
        self.scpi_preview_detail.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        self.scpi_confirm_var = tk.BooleanVar(value=False)
        self.scpi_confirm = ctk.CTkCheckBox(preview, text="Confirmo exatamente o comando pré-analisado", variable=self.scpi_confirm_var)
        self.scpi_confirm.grid(row=2, column=0, sticky="w", padx=16, pady=6)
        self.scpi_physical_var = tk.BooleanVar(value=False)
        self.scpi_physical = ctk.CTkCheckBox(preview, text="Para HV: confirmei fisicamente cabo, fixture, tampa e circuito", variable=self.scpi_physical_var)
        self.scpi_physical.grid(row=3, column=0, sticky="w", padx=16, pady=(6, 14))

        output = self._card(page, 3)
        output.grid_columnconfigure(0, weight=1)
        self.scpi_output = ctk.CTkTextbox(output, height=260, font=ctk.CTkFont(family="Cascadia Mono", size=12), state="disabled")
        self.scpi_output.grid(row=0, column=0, sticky="nsew", padx=16, pady=16)

    def _build_logs_page(self) -> None:
        page = self._page(PageId.LOGS)
        self._section_header(page, "Registros", "Eventos da sessão atual, com operações, alertas e erros.")
        card = self._card(page, 1)
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(card, text="Limpar visualização", width=150, command=lambda: self._emit(IntentKind.CLEAR_LOGS)).grid(row=0, column=0, sticky="e", padx=16, pady=(14, 4))
        self.log_output = ctk.CTkTextbox(card, height=540, font=ctk.CTkFont(family="Cascadia Mono", size=12), state="disabled")
        self.log_output.grid(row=1, column=0, sticky="nsew", padx=16, pady=(4, 16))

    def _build_settings_page(self) -> None:
        page = self._page(PageId.SETTINGS)
        self._section_header(page, "Configurações", "Aparência e comportamento visual da estação de controle.")
        card = self._card(page, 1)
        card.grid_columnconfigure(1, weight=1)
        self._label(card, "Tema", 0, 0, text_color=COLORS["muted"])
        self.theme_option = ctk.CTkSegmentedButton(card, values=["Claro", "Escuro"], command=self._theme_changed)
        self.theme_option.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 16))
        self._label(card, "Escala e DPI", 0, 1, text_color=COLORS["muted"])
        ctk.CTkLabel(card, text="Escala automática do CustomTkinter/Windows", anchor="w", text_color=COLORS["text"]).grid(row=1, column=1, sticky="ew", padx=16, pady=(0, 16))
        architecture = self._card(page, 2)
        architecture.grid_columnconfigure(0, weight=1)
        self._label(architecture, "ARQUITETURA ATIVA", 0, text_color=COLORS["muted"], font=ctk.CTkFont(size=11, weight="bold"))
        ctk.CTkLabel(
            architecture,
            text="UI → intents semânticas → coordenador assíncrono → controller → VisaWorker único\nWorkers → ViewState imutável → root.after() → UI",
            justify="left",
            anchor="w",
            text_color=COLORS["text"],
            font=ctk.CTkFont(family="Cascadia Mono", size=12),
        ).grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 16))

    # -------------------------------------------------------------- user events
    def _emit(self, kind: IntentKind, **payload: Any) -> None:
        self.coordinator.dispatch(AppIntent(kind, payload))

    def _show_page(self, page: PageId, dispatch: bool = True) -> None:
        if page != self._visible_page:
            self.page_title.configure(text=PAGE_TITLES[page])
            for item_page, button in self._nav_buttons.items():
                active = item_page == page
                button.configure(
                    image=self._nav_icons[item_page][1 if active else 0],
                    fg_color=COLORS["selection"] if active else "transparent",
                )
                self._nav_rows[item_page].configure(
                    fg_color=COLORS["accent"] if active else "transparent"
                )

            # CTkScrollableFrame.grid() manages an outer parent frame.
            # Keep exactly one native page tree mapped at a time: sibling
            # controls from overlapping pages otherwise paint independently.
            if self._visible_page is not None:
                previous_frame = self._pages[self._visible_page]
                previous_shell = getattr(
                    previous_frame, "_parent_frame", previous_frame
                )
                previous_shell.grid_remove()
            frame = self._pages[page]
            page_shell = getattr(frame, "_parent_frame", frame)
            page_shell.grid()
            page_shell.tkraise()
            self._visible_page = page
        if dispatch:
            self._emit(IntentKind.NAVIGATE, page=page)

    def _expected_model_changed(self, value: str) -> None:
        model = {"Automático": "AUTO", "Keithley 6517A": "6517A", "Keithley 6517B": "6517B"}[value]
        self._emit(IntentKind.SET_EXPECTED_MODEL, model=model)

    def _discover(self) -> None:
        self._emit(IntentKind.DISCOVER_RESOURCES)

    def _connect(self) -> None:
        self._emit(IntentKind.CONNECT, resource=self.resource_combo.get())

    def _disconnect(self) -> None:
        self._emit(IntentKind.DISCONNECT)

    def _configure_measurement(self) -> None:
        self._emit(
            IntentKind.CONFIGURE_MEASUREMENT,
            function=FUNCTIONS[self.function_option.get()],
            auto_range=self.auto_range_var.get(),
            range_value=self.range_entry.get(),
            nplc=self.nplc_entry.get(),
            digits=self.digits_option.get(),
            resistance_vsource_mode=self.resistance_mode.get(),
        )

    def _function_changed(self, value: str) -> None:
        self._measurement_function_name = value
        is_resistance = value == "Resistência"
        self.resistance_mode.configure(state="normal" if is_resistance else "disabled")
        self.resistance_notice.configure(text_color=COLORS["warning"] if is_resistance else COLORS["muted"])
        if hasattr(self, "file_entry"):
            self._set_default_acquisition_filename(self.acquisition_mode.get())

    def _range_mode_changed(self) -> None:
        self.range_entry.configure(state="disabled" if self.auto_range_var.get() else "normal")

    def _automatic_acquisition_filename(self, mode: str) -> str:
        function_name = getattr(self, "_measurement_function_name", "Corrente DC")
        function_part = FUNCTION_FILENAME_PARTS.get(function_name, "medicao")
        return "{0}_{1}.csv".format(
            "{0}_{1}".format(
                str(mode).strip().lower() or "acquisition",
                function_part,
            ),
            datetime.now().strftime("%Y%m%d_%H%M%S"),
        )

    def _set_default_acquisition_filename(self, mode: str) -> None:
        if not self._acquisition_name_automatic:
            return
        self.file_entry.delete(0, "end")
        self.file_entry.insert(0, self._automatic_acquisition_filename(mode))

    def _acquisition_filename_edited(self, _event: Any = None) -> None:
        self._acquisition_name_automatic = False

    def _acquisition_mode_changed(self, mode: str) -> None:
        self._set_default_acquisition_filename(mode)

    def _open_data_folder(self) -> None:
        """Open the application's default folder containing acquisition CSVs."""

        folder = Path(self.coordinator.paths.data).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder))
        except AttributeError:  # pragma: no cover - non-Windows fallback
            import subprocess

            subprocess.Popen(["explorer", str(folder)])

    def _start_acquisition(self) -> None:
        self._last_table_index = 0
        for item in self.reading_tree.get_children():
            self.reading_tree.delete(item)
        mode = self.acquisition_mode.get()
        if self._acquisition_name_automatic:
            self._set_default_acquisition_filename(mode)
        self._emit(
            IntentKind.START_ACQUISITION,
            mode=mode,
            duration=self.duration_entry.get(),
            interval=self.interval_option.get(),
            path=self.file_entry.get(),
            automatic_name=self._acquisition_name_automatic,
        )

    def _update_hv_preview(self, _event: Any = None) -> None:
        try:
            value = abs(float(self.hv_voltage_entry.get().replace(",", ".")))
            self.hv_preview.configure(text="100 V / 10 mA" if value <= 100 else "1000 V / 1 mA")
        except ValueError:
            self.hv_preview.configure(text="Valor inválido")

    def _configure_hv(self) -> None:
        self._emit(IntentKind.CONFIGURE_HV, voltage=self.hv_voltage_entry.get(), limit=self.hv_limit_entry.get())

    def _enable_hv(self) -> None:
        physical = all(variable.get() for variable in self.hv_check_vars)
        if not physical:
            messagebox.showwarning("Alta tensão", "Conclua as três confirmações físicas antes de energizar.", parent=self)
            return
        confirmed = messagebox.askyesno(
            "Confirmar alta tensão",
            "A saída pode aplicar tensão letal. Confirma a energização exatamente com os valores mostrados?",
            icon="warning",
            default=messagebox.NO,
            parent=self,
        )
        if confirmed:
            self._emit(IntentKind.ENABLE_HV, physical_confirmed=True)

    def _disable_hv(self) -> None:
        self._emit(IntentKind.DISABLE_HV)

    def _scpi_text(self) -> str:
        return self.scpi_input.get("1.0", "end").strip()

    def _scpi_edited(self, _event: Any = None) -> None:
        self.scpi_confirm_var.set(False)
        self.scpi_physical_var.set(False)
        if self._preview_after_id is not None:
            self.after_cancel(self._preview_after_id)
        self._preview_after_id = self.after(350, self._preview_scpi)

    def _preview_scpi(self) -> None:
        self._preview_after_id = None
        self._emit(IntentKind.PREVIEW_SCPI, command=self._scpi_text())

    def _execute_scpi(self) -> None:
        preview = self.current_state.scpi_preview
        if preview.confirmation_required and not self.scpi_confirm_var.get():
            messagebox.showwarning("Console SCPI", "Confirme exatamente o comando pré-analisado.", parent=self)
            return
        self._emit(
            IntentKind.EXECUTE_SCPI,
            command=self._scpi_text(),
            confirmed=self.scpi_confirm_var.get(),
            physical_confirmed=self.scpi_physical_var.get(),
        )

    def _theme_changed(self, value: str) -> None:
        theme = "Light" if value == "Claro" else "Dark"
        self._apply_theme(theme)
        self._emit(IntentKind.SET_THEME, theme=theme)

    def _request_close(self) -> None:
        if self._closing_requested:
            return
        self._closing_requested = True
        self._emit(IntentKind.SHUTDOWN)

    def _cancel_own_timers(self) -> None:
        for attribute in ("_preview_after_id", "_poll_after_id"):
            timer_id = getattr(self, attribute, None)
            if timer_id is not None:
                try:
                    self.after_cancel(timer_id)
                except tk.TclError:
                    pass
                setattr(self, attribute, None)

    # --------------------------------------------------------------- rendering
    def _poll_application(self) -> None:
        self._poll_after_id = None
        snapshots = self.coordinator.drain_states()
        if snapshots:
            self._render(snapshots[-1])
        if self.current_state.closing and not self.current_state.busy:
            self._cancel_own_timers()
            self.destroy()
            return
        self._poll_after_id = self.after(self.POLL_MS, self._poll_application)

    def _render(self, state: ViewState) -> None:
        previous = self.current_state
        self.current_state = state
        if previous.acquisition_running and not state.acquisition_running and self._acquisition_name_automatic:
            self._set_default_acquisition_filename(self.acquisition_mode.get())
        if state.active_page != self._visible_page:
            self._show_page(state.active_page, dispatch=False)
        if state.theme != previous.theme:
            self._apply_theme(state.theme)

        self.header_model.configure(text="Esperado {0}  ·  Detectado {1}".format(state.expected_model, state.detected_model))
        self.connection_pill.configure(
            text="● " + state.connection_status,
            text_color=COLORS["success"] if state.connected else COLORS["muted"],
        )
        if state.hv_active:
            self.emergency_header.grid(row=0, column=2, padx=(8, 20), pady=8)
        else:
            self.emergency_header.grid_remove()
        self.status_bar.configure(fg_color=COLORS["danger"] if state.hv_active else (COLORS["warning"] if state.controller_state == "Error" else COLORS["accent"]))
        self.status_left.configure(text="VISA: {0}".format(state.resource_name or "desconectado"))
        self.status_message.configure(text=state.busy_message or state.status_message)
        error_count = sum(1 for entry in state.logs if entry.level.value == "ERROR")
        self.status_right.configure(text="HV: {0}  ·  Erros: {1}".format(state.hv_state, error_count))

        self.dashboard_connection.configure(text=state.connection_status)
        self.dashboard_model.configure(text=state.detected_model)
        self.dashboard_hv.configure(text=state.hv_state, text_color=COLORS["danger"] if state.hv_active else COLORS["success"])
        if state.reading_value is None:
            reading_text = "—"
        elif math.isfinite(state.reading_value):
            reading_text = "{0:.8E} {1}".format(state.reading_value, state.reading_unit)
        else:
            reading_text = "Inválida"
        self.dashboard_reading.configure(text=reading_text)
        self.dashboard_reading_meta.configure(text="Estado: {0}  ·  t = {1:.6g} s".format(state.reading_status, state.reading_timestamp))
        self.dashboard_safety.configure(text="HV: {0}. Interlock: {1}. Compliance: {2}.".format(state.hv_state, state.interlock_state, "ATIVA" if state.compliance else "não detectada"))

        self.expected_model.configure(state="disabled" if state.connected else "normal")
        expected_display = {"AUTO": "Automático", "6517A": "Keithley 6517A", "6517B": "Keithley 6517B"}.get(state.expected_model, "Automático")
        if self.expected_model.get() != expected_display:
            self.expected_model.set(expected_display)
        if state.available_resources:
            self.resource_combo.configure(values=list(state.available_resources))
            if not self.resource_combo.get() or self.resource_combo.get() in ("GPIB0::27::INSTR", "GPIB0::26::INSTR"):
                self.resource_combo.set(state.available_resources[0])
        self.identity_labels["idn"].configure(text=state.identity or "—")
        self.identity_labels["serial"].configure(text=state.serial_number)
        self.identity_labels["firmware"].configure(text=state.firmware)
        self.identity_labels["scpi"].configure(text=state.scpi_version)
        self.identity_labels["state"].configure(text=state.controller_state)

        can_connect = not state.connected and not state.busy and not state.closing
        self.connect_button.configure(state="normal" if can_connect else "disabled")
        self.discover_button.configure(state="normal" if can_connect else "disabled")
        self.disconnect_button.configure(
            state="normal"
            if state.connected and not state.closing and not state.acquisition_running
            else "disabled"
        )
        can_configure = state.connected and not state.busy and not state.acquisition_running and not state.hv_active
        self.apply_measurement_button.configure(state="normal" if can_configure else "disabled")
        self.one_shot_button.configure(state="normal" if state.measurement_configured and not state.busy and not state.acquisition_running else "disabled")

        self.start_acquisition_button.configure(state="normal" if state.connected and state.measurement_configured and not state.acquisition_running and not state.busy else "disabled")
        self.stop_acquisition_button.configure(state="normal" if state.acquisition_running else "disabled")
        target = max(1, state.acquisition_target)
        self.acquisition_progress.set(min(1.0, state.acquisition_count / target))
        self.acquisition_counter.configure(text="{0} / {1}".format(state.acquisition_count, state.acquisition_target))
        self._render_readings(state)

        self.hv_status_labels["state"].configure(text=state.hv_state, text_color=COLORS["danger"] if state.hv_active else COLORS["success"])
        self.hv_status_labels["voltage"].configure(text="{0:g} V".format(state.hv_voltage))
        self.hv_status_labels["range"].configure(text="{0:g} V / {1:g} mA nominal".format(state.hv_range, state.hv_current_limit_ma))
        self.hv_status_labels["limit"].configure(text="{0:g} V".format(state.hv_voltage_limit))
        self.hv_status_labels["interlock"].configure(text=state.interlock_state, text_color=COLORS["warning"] if "indeterminado" in state.interlock_state.lower() else COLORS["text"])
        self.hv_status_labels["compliance"].configure(text="DETECTADA" if state.compliance else "Não detectada", text_color=COLORS["danger"] if state.compliance else COLORS["text"])
        self.configure_hv_button.configure(state="normal" if state.connected and not state.busy and not state.acquisition_running and not state.hv_active else "disabled")
        self.enable_hv_button.configure(state="normal" if state.connected and state.hv_configured and not state.hv_active and not state.busy else "disabled")
        self.disable_hv_button.configure(state="normal" if state.connected else "disabled")

        preview = state.scpi_preview
        self.scpi_preview_title.configure(
            text=("VALIDADO" if preview.valid else "BLOQUEADO") + "  ·  Risco " + preview.risk,
            text_color=COLORS["success"] if preview.valid and preview.risk == "NONE" else (COLORS["warning"] if preview.valid else COLORS["danger"]),
        )
        self.scpi_preview_detail.configure(text="{0}\n{1}".format(preview.summary, preview.manual_reference).strip())
        self.scpi_confirm.configure(state="normal" if preview.confirmation_required else "disabled")
        self.scpi_physical.configure(state="normal" if preview.risk == "HV_ENABLE" else "disabled")
        self.execute_scpi_button.configure(state="normal" if state.connected and preview.valid and not state.busy and not state.acquisition_running else "disabled")
        self._render_scpi_output(state.scpi_output)
        self._render_logs(state)
        self.theme_option.set("Claro" if state.theme == "Light" else "Escuro")

        if state.error_banner and state.error_banner != previous.error_banner:
            messagebox.showerror("Keithley 6517", state.error_banner, parent=self)

    def _render_readings(self, state: ViewState) -> None:
        inserted_reading = False
        if state.acquisition_count < self._last_table_index:
            self._last_table_index = 0
            for item in self.reading_tree.get_children():
                self.reading_tree.delete(item)
        for reading in state.readings:
            if reading.index <= self._last_table_index:
                continue
            self.reading_tree.insert(
                "",
                "end",
                values=(
                    reading.index,
                    "{0:.6g}".format(reading.timestamp),
                    "{0:.8E}".format(reading.value),
                    reading.unit,
                ),
            )
            self._last_table_index = reading.index
            inserted_reading = True
        children = self.reading_tree.get_children()
        if len(children) > 2000:
            for item in children[: len(children) - 2000]:
                self.reading_tree.delete(item)
            children = self.reading_tree.get_children()
        if children and (inserted_reading or state.acquisition_running):
            self.reading_tree.see(children[-1])
        self._draw_chart(state)

    def _draw_chart(self, state: ViewState) -> None:
        self.chart.delete("all")
        self._chart_points = []
        width = max(200, self.chart.winfo_width())
        height = max(160, self.chart.winfo_height())
        background = "#FFFFFF" if state.theme == "Light" else "#202020"
        grid = "#CED6DE" if state.theme == "Light" else "#3C3C3C"
        foreground = "#0067B8" if state.theme == "Light" else "#4EC9B0"
        axis = "#66717D" if state.theme == "Light" else "#A6A6A6"
        self.chart.configure(bg=background)
        plot_left = 48
        plot_right = width - 12
        plot_top = 18
        plot_bottom = height - 38
        readings = [
            reading
            for reading in state.readings
            if math.isfinite(reading.value) and math.isfinite(reading.timestamp)
        ]
        values = [(reading.timestamp, reading.value) for reading in readings]
        if len(values) < 2:
            self._chart_hover_index = None
            self.chart.create_text(width / 2, height / 2, text="Aguardando dados", fill="#808080")
            return
        minimum = min(value for _index, value in values)
        maximum = max(value for _index, value in values)
        if math.isclose(minimum, maximum):
            minimum -= abs(minimum) * 0.05 or 1.0
            maximum += abs(maximum) * 0.05 or 1.0
        first, last = values[0][0], values[-1][0]
        span_x = max(1, last - first)
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = plot_top + fraction * (plot_bottom - plot_top)
            axis_value = maximum - fraction * (maximum - minimum)
            self.chart.create_line(plot_left, y, plot_right, y, fill=grid)
            self.chart.create_text(
                plot_left - 6,
                y,
                text="{0:.5g}".format(axis_value),
                fill=axis,
                anchor="e",
                font=("Segoe UI", 8),
            )
        self.chart.create_line(plot_left, plot_top, plot_left, plot_bottom, fill=axis)
        self.chart.create_line(plot_left, plot_bottom, plot_right, plot_bottom, fill=axis)
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            x = plot_left + fraction * (plot_right - plot_left)
            axis_time = first + fraction * (last - first)
            self.chart.create_line(x, plot_bottom, x, plot_bottom + 4, fill=axis)
            self.chart.create_text(
                x,
                plot_bottom + 7,
                text="{0:.5g}".format(axis_time),
                fill=axis,
                anchor="n",
                font=("Segoe UI", 8),
            )
        self.chart.create_text(
            plot_left,
            3,
            text="Valor ({0})".format(state.reading_unit),
            fill=axis,
            anchor="nw",
            font=("Segoe UI", 8, "bold"),
        )
        self.chart.create_text(
            plot_right,
            height - 3,
            text="Tempo (s)",
            fill=axis,
            anchor="se",
            font=("Segoe UI", 8, "bold"),
        )
        points: List[float] = []
        for reading in readings:
            x = plot_left + (reading.timestamp - first) / span_x * (plot_right - plot_left)
            value = reading.value
            y = plot_top + (maximum - value) / (maximum - minimum) * (plot_bottom - plot_top)
            points.extend((x, y))
            self._chart_points.append((x, y, reading.timestamp, value, reading.index))
        self.chart.create_line(*points, fill=foreground, width=2, smooth=False)
        if self._chart_hover_index is not None:
            hovered = next(
                (point for point in self._chart_points if point[4] == self._chart_hover_index),
                None,
            )
            if hovered is not None:
                self._render_chart_hover(hovered)

    def _chart_motion(self, event: Any) -> None:
        if not self._chart_points:
            return
        hovered = min(self._chart_points, key=lambda point: abs(point[0] - event.x))
        self._chart_hover_index = hovered[4]
        self._render_chart_hover(hovered)

    def _chart_leave(self, _event: Any = None) -> None:
        self._chart_hover_index = None
        self.chart.delete("chart_hover")

    def _render_chart_hover(self, point: Tuple[float, float, float, float, int]) -> None:
        x, y, timestamp, value, index = point
        width = max(200, self.chart.winfo_width())
        height = max(160, self.chart.winfo_height())
        self.chart.delete("chart_hover")
        color = "#F2C94C" if self.current_state.theme == "Dark" else "#B26A00"
        self.chart.create_line(
            x, 18, x, height - 38,
            fill=color,
            dash=(4, 3),
            tags="chart_hover",
        )
        self.chart.create_line(
            48, y, width - 12, y,
            fill=color,
            dash=(4, 3),
            tags="chart_hover",
        )
        text = "t = {0:.6g} s\nvalor = {1:.8E} {2}\namostra = {3}".format(
            timestamp,
            value,
            self.current_state.reading_unit,
            index,
        )
        label_width = 150
        label_height = 58
        label_x = x + 12
        label_y = y - label_height - 10
        if label_x + label_width > width - 4:
            label_x = x - label_width - 12
        if label_y < 4:
            label_y = y + 10
        self.chart.create_rectangle(
            label_x,
            label_y,
            label_x + label_width,
            label_y + label_height,
            fill="#252526" if self.current_state.theme == "Dark" else "#FFFFFF",
            outline=color,
            tags="chart_hover",
        )
        self.chart.create_text(
            label_x + 7,
            label_y + 7,
            text=text,
            anchor="nw",
            justify="left",
            fill="#F0F0F0" if self.current_state.theme == "Dark" else "#17212B",
            font=("Segoe UI", 9),
            tags="chart_hover",
        )

    def _render_scpi_output(self, lines: Tuple[str, ...]) -> None:
        if lines == self._last_output:
            return
        self._last_output = lines
        self.scpi_output.configure(state="normal")
        self.scpi_output.delete("1.0", "end")
        self.scpi_output.insert("1.0", "\n".join(lines))
        self.scpi_output.see("end")
        self.scpi_output.configure(state="disabled")

    def _render_logs(self, state: ViewState) -> None:
        if self._last_log_revision == state.revision and len(state.logs) == 0:
            return
        text = "\n".join(
            "{0} | {1:<7} | {2}".format(
                datetime.fromtimestamp(entry.timestamp).strftime("%H:%M:%S"),
                entry.level.value,
                entry.message,
            )
            for entry in state.logs
        )
        self.log_output.configure(state="normal")
        self.log_output.delete("1.0", "end")
        self.log_output.insert("1.0", text)
        self.log_output.see("end")
        self.log_output.configure(state="disabled")
        self._last_log_revision = state.revision

    def _apply_theme(self, theme: str) -> None:
        ctk.set_appearance_mode(theme)
        dark = theme == "Dark"
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        background = "#252526" if dark else "#E9EEF3"
        field = "#1E1E1E" if dark else "#FFFFFF"
        foreground = "#CCCCCC" if dark else "#17212B"
        border = "#3C3C3C" if dark else "#B7C1CC"
        selection = "#007ACC" if dark else "#0067B8"
        style.configure(
            "Treeview",
            background=field,
            fieldbackground=field,
            foreground=foreground,
            bordercolor=border,
            rowheight=28,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Treeview.Heading",
            background=background,
            foreground=foreground,
            relief="flat",
            font=("Segoe UI", 10, "bold"),
        )
        style.map("Treeview", background=[("selected", selection)], foreground=[("selected", "#FFFFFF")])
        self.chart.configure(bg="#202020" if dark else "#FFFFFF")


__all__ = ["Keithley6517UI"]
