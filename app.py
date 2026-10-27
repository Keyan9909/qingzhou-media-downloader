"""轻舟下载器 Windows desktop interface."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any, Callable

from media_core import (
    APP_NAME,
    APP_VERSION,
    DATA_DIR,
    PLATFORMS,
    STATE_DIR,
    detect_platform,
    extract_url,
    find_firefox_executable,
    list_history,
    load_config,
    platform_payload,
    progress_payload,
    resolve_output_dir,
    save_config,
)


CODE_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
CLI_FILE = CODE_DIR / ("media-cli.exe" if getattr(sys, "frozen", False) else "media_cli.py")
COLORS = {
    "bg": "#F4F7FB",
    "surface": "#FFFFFF",
    "sidebar": "#172033",
    "sidebar_hover": "#24314A",
    "primary": "#4F6EF7",
    "primary_dark": "#3E59D7",
    "success": "#16A075",
    "warning": "#D97706",
    "danger": "#D64545",
    "text": "#172033",
    "muted": "#68738A",
    "border": "#DCE3EF",
    "soft": "#EEF2FF",
}
PLATFORM_COLORS = {
    "youtube": "#FF0033",
    "douyin": "#111827",
    "xiaohongshu": "#FF2442",
    "bilibili": "#00A1D6",
    "xiaoetong": "#FF7A45",
    "unknown": "#7C879B",
}
PLATFORM_MARKS = {
    "youtube": "YT",
    "douyin": "抖",
    "xiaohongshu": "红",
    "bilibili": "B",
    "xiaoetong": "鹅",
    "unknown": "?",
}
QUALITY_LABELS = {
    "best": "自动最高画质",
    "2160": "4K · 2160p",
    "1440": "2K · 1440p",
    "1080": "高清 · 1080p（推荐）",
    "720": "流畅 · 720p",
    "480": "省流 · 480p",
    "360": "最低 · 360p",
}
COOKIE_LABELS = {
    "none": "不读取系统浏览器",
    "firefox": "Firefox 登录状态（YouTube 必选）",
    "edge": "Edge 登录状态",
    "chrome": "Chrome 登录状态",
    "app": "内置浏览器登录（抖音/小红书/小鹅通）",
}
_INSTANCE_MUTEX = None


def enable_windows_dpi() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def acquire_single_instance() -> bool:
    global _INSTANCE_MUTEX
    try:
        handle = ctypes.windll.kernel32.CreateMutexW(
            None, False, "Local\\QingzhouMediaDownloader_1"
        )
        if handle and ctypes.windll.kernel32.GetLastError() == 183:
            ctypes.windll.kernel32.CloseHandle(handle)
            return False
        _INSTANCE_MUTEX = handle
    except (AttributeError, OSError):
        return True
    return True


class DownloaderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} · 全平台内容保存工具")
        self.geometry("1220x800")
        self.minsize(1050, 700)
        self.configure(bg=COLORS["bg"])
        self.option_add("*Font", ("Microsoft YaHei UI", 10))
        for icon_path in (
            CODE_DIR / "app-icon.ico",
            CODE_DIR / "assets" / "app-icon.ico",
        ):
            if icon_path.is_file():
                try:
                    self.iconbitmap(default=str(icon_path))
                except tk.TclError:
                    pass
                break

        self.config_data = load_config()
        self.current_page = "download"
        self.pages: dict[str, tk.Frame] = {}
        self.nav_buttons: dict[str, tk.Button] = {}
        self.busy = False
        self.cancel_requested = False
        self.active_download_process: subprocess.Popen[str] | None = None
        self.download_process_lock = threading.Lock()
        self.progress_after: str | None = None
        self.progress_indeterminate_running = False
        self.download_started_at = 0.0
        self.detect_after: str | None = None
        self.manual_platform: str | None = None

        self.url_var = tk.StringVar()
        self.platform_var = tk.StringVar(value="粘贴链接后自动识别平台")
        self.platform_hint_var = tk.StringVar(value="支持分享文本，软件会自动提取其中的链接")
        self.quality_var = tk.StringVar(
            value=QUALITY_LABELS.get(
                str(self.config_data.get("quality") or "1080"),
                QUALITY_LABELS["1080"],
            )
        )
        self.cookie_var = tk.StringVar(
            value=COOKIE_LABELS.get(
                str(self.config_data.get("browser_cookies") or "none"),
                COOKIE_LABELS["none"],
            )
        )
        self.rights_var = tk.BooleanVar(value=False)
        self.show_browser_var = tk.BooleanVar(
            value=bool(self.config_data.get("show_browser_when_needed", True))
        )
        self.output_preview_var = tk.StringVar()
        self.status_var = tk.StringVar(value="准备就绪")
        self.progress_stage_var = tk.StringVar(value="尚未开始下载")
        self.progress_percent_var = tk.StringVar(value="—")
        self.progress_detail_var = tk.StringVar(value="开始下载后将在这里显示速度和剩余时间")
        self.default_dir_var = tk.StringVar(
            value=str(self.config_data.get("default_output_dir") or "")
        )
        self.platform_dir_vars = {
            platform: tk.StringVar(
                value=str(
                    (self.config_data.get("platform_output_dirs") or {}).get(platform)
                    or ""
                )
            )
            for platform in PLATFORMS
        }
        self.accepted_terms_var = tk.BooleanVar(
            value=bool(self.config_data.get("accepted_terms", False))
        )

        self._configure_styles()
        self._build_shell()
        self._build_download_page()
        self._build_history_page()
        self._build_browser_page()
        self._build_storage_page()
        self._build_guide_page()
        self._build_about_page()
        self.show_page("download")
        self.after_idle(lambda: self.state("zoomed"))
        self.url_var.trace_add("write", self._schedule_detection)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(300, self._first_run_notice)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "App.TEntry",
            fieldbackground=COLORS["surface"],
            foreground=COLORS["text"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["border"],
            darkcolor=COLORS["border"],
            padding=10,
        )
        style.configure(
            "App.TCombobox",
            fieldbackground=COLORS["surface"],
            background=COLORS["surface"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["muted"],
            bordercolor=COLORS["border"],
            padding=8,
        )
        style.map("App.TCombobox", fieldbackground=[("readonly", COLORS["surface"])])
        style.configure(
            "History.Treeview",
            background=COLORS["surface"],
            fieldbackground=COLORS["surface"],
            foreground=COLORS["text"],
            rowheight=34,
            borderwidth=0,
        )
        style.configure(
            "History.Treeview.Heading",
            background="#EEF2F8",
            foreground=COLORS["muted"],
            relief="flat",
            padding=8,
        )
        style.map("History.Treeview", background=[("selected", "#DDE5FF")])
        style.configure(
            "App.Horizontal.TProgressbar",
            troughcolor="#E6EAF2",
            background=COLORS["primary"],
            borderwidth=0,
            thickness=12,
        )

    def _build_shell(self) -> None:
        sidebar = tk.Frame(self, bg=COLORS["sidebar"], width=246)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        brand = tk.Frame(sidebar, bg=COLORS["sidebar"], height=96)
        brand.pack(fill="x")
        brand.pack_propagate(False)
        logo = tk.Label(
            brand,
            text="舟",
            bg=COLORS["primary"],
            fg="white",
            font=("Microsoft YaHei UI", 18, "bold"),
            width=2,
            height=1,
        )
        logo.pack(side="left", padx=(16, 8), pady=24)
        title_box = tk.Frame(brand, bg=COLORS["sidebar"])
        title_box.pack(side="left", pady=21)
        tk.Label(
            title_box,
            text=APP_NAME,
            bg=COLORS["sidebar"],
            fg="white",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text=f"全平台下载 · v{APP_VERSION}",
            bg=COLORS["sidebar"],
            fg="#93A0BA",
            font=("Microsoft YaHei UI", 7),
        ).pack(anchor="w", pady=(2, 0))

        nav_items = (
            ("download", "⌄", "下载中心"),
            ("history", "◷", "任务记录"),
            ("browser", "◎", "平台登录"),
            ("storage", "▣", "保存设置"),
            ("guide", "?", "使用帮助"),
            ("about", "i", "关于与合规"),
        )
        for key, icon, label in nav_items:
            button = tk.Button(
                sidebar,
                text=f"  {icon}     {label}",
                anchor="w",
                command=lambda page=key: self.show_page(page),
                bg=COLORS["sidebar"],
                fg="#B7C1D6",
                activebackground=COLORS["sidebar_hover"],
                activeforeground="white",
                relief="flat",
                bd=0,
                padx=20,
                pady=13,
                font=("Microsoft YaHei UI", 10),
                cursor="hand2",
            )
            button.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[key] = button

        tk.Label(
            sidebar,
            text="仅保存你有权使用的内容\n不绕过付费、DRM 或访问控制",
            bg=COLORS["sidebar"],
            fg="#7F8BA3",
            justify="left",
            font=("Microsoft YaHei UI", 8),
            wraplength=200,
        ).pack(side="bottom", anchor="w", padx=16, pady=20)

        right = tk.Frame(self, bg=COLORS["bg"])
        right.pack(side="left", fill="both", expand=True)
        self.content = tk.Frame(right, bg=COLORS["bg"])
        self.content.pack(fill="both", expand=True)

        status_bar = tk.Frame(right, bg=COLORS["surface"], height=34)
        status_bar.pack(side="bottom", fill="x")
        status_bar.pack_propagate(False)
        self.status_dot = tk.Label(
            status_bar,
            text="●",
            bg=COLORS["surface"],
            fg=COLORS["success"],
            font=("Microsoft YaHei UI", 8),
        )
        self.status_dot.pack(side="left", padx=(18, 7))
        tk.Label(
            status_bar,
            textvariable=self.status_var,
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left")
        tk.Label(
            status_bar,
            text=f"数据保存在：{DATA_DIR}",
            bg=COLORS["surface"],
            fg="#98A2B5",
            font=("Microsoft YaHei UI", 8),
        ).pack(side="right", padx=18)
        self.content.pack_forget()
        status_bar.pack_forget()
        status_bar.pack(side="bottom", fill="x")
        self.content.pack(side="top", fill="both", expand=True)

    def _new_page(self, key: str, title: str, subtitle: str) -> tuple[tk.Frame, tk.Frame]:
        page = tk.Frame(self.content, bg=COLORS["bg"])
        self.pages[key] = page
        header = tk.Frame(page, bg=COLORS["bg"])
        header.pack(fill="x", padx=28, pady=(18, 12))
        tk.Label(
            header,
            text=title,
            bg=COLORS["bg"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 20, "bold"),
        ).pack(anchor="w")
        tk.Label(
            header,
            text=subtitle,
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(5, 0))
        body = tk.Frame(page, bg=COLORS["bg"])
        body.pack(fill="both", expand=True, padx=28, pady=(0, 20))
        return page, body

    def _card(self, parent: tk.Widget, **pack: Any) -> tk.Frame:
        card = tk.Frame(
            parent,
            bg=COLORS["surface"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        card.pack(**pack)
        return card

    def _button(
        self,
        parent: tk.Widget,
        text: str,
        command: Callable[[], None],
        *,
        kind: str = "primary",
        padx: int = 18,
        pady: int = 10,
    ) -> tk.Button:
        backgrounds = {
            "primary": COLORS["primary"],
            "dark": COLORS["sidebar"],
            "soft": COLORS["soft"],
            "danger": "#FFF0F0",
        }
        foregrounds = {
            "primary": "white",
            "dark": "white",
            "soft": COLORS["primary_dark"],
            "danger": COLORS["danger"],
        }
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=backgrounds[kind],
            fg=foregrounds[kind],
            activebackground=backgrounds[kind],
            activeforeground=foregrounds[kind],
            relief="flat",
            bd=0,
            padx=padx,
            pady=pady,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold"),
        )

    def _build_download_page(self) -> None:
        _, body = self._new_page(
            "download",
            "下载中心",
            "复制作品链接，粘贴，点击下载。平台和保存位置由软件自动选择。",
        )
        platform_row = tk.Frame(body, bg=COLORS["bg"])
        platform_row.pack(fill="x", pady=(0, 8))
        for platform in PLATFORMS:
            metadata = platform_payload(platform)
            chip = tk.Frame(
                platform_row,
                bg=COLORS["surface"],
                highlightbackground=COLORS["border"],
                highlightthickness=1,
                cursor="hand2",
            )
            chip.pack(side="left", expand=True, fill="x", padx=(0, 8))
            mark_label = tk.Label(
                chip,
                text=PLATFORM_MARKS[platform],
                bg=PLATFORM_COLORS[platform],
                fg="white",
                width=3,
                pady=7,
                font=("Microsoft YaHei UI", 9, "bold"),
                cursor="hand2",
            )
            mark_label.pack(side="left")
            name_label = tk.Label(
                chip,
                text=metadata["name"],
                bg=COLORS["surface"],
                fg=COLORS["text"],
                padx=10,
                font=("Microsoft YaHei UI", 9, "bold"),
                cursor="hand2",
            )
            name_label.pack(side="left")
            for widget in (chip, mark_label, name_label):
                widget.bind(
                    "<Button-1>",
                    lambda _event, value=platform: self.select_platform(value),
                )

        card = self._card(body, fill="x")
        form = tk.Frame(card, bg=COLORS["surface"])
        form.pack(fill="x", padx=28, pady=8)
        tk.Label(
            form,
            text="作品链接或分享文本",
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor="w")
        input_row = tk.Frame(form, bg=COLORS["surface"])
        input_row.pack(fill="x", pady=(10, 8))
        self.url_entry = ttk.Entry(
            input_row,
            textvariable=self.url_var,
            style="App.TEntry",
            font=("Microsoft YaHei UI", 11),
        )
        self.url_entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.url_entry.bind("<Return>", lambda _event: self.start_download())
        self.download_button = self._button(
            input_row, "开始下载", self.start_download, padx=28, pady=9
        )
        self.download_button.pack(side="left", padx=(12, 0))

        platform_info = tk.Frame(form, bg="#F7F9FC")
        platform_info.pack(fill="x", pady=(2, 10))
        self.platform_mark = tk.Label(
            platform_info,
            text="?",
            bg=PLATFORM_COLORS["unknown"],
            fg="white",
            width=3,
            pady=3,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.platform_mark.pack(side="left")
        info_text = tk.Frame(platform_info, bg="#F7F9FC")
        info_text.pack(side="left", padx=12, pady=2)
        tk.Label(
            info_text,
            textvariable=self.platform_var,
            bg="#F7F9FC",
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(anchor="w")
        tk.Label(
            info_text,
            textvariable=self.platform_hint_var,
            bg="#F7F9FC",
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w")

        settings_row = tk.Frame(form, bg=COLORS["surface"])
        settings_row.pack(fill="x")
        left = tk.Frame(settings_row, bg=COLORS["surface"])
        left.pack(side="left", fill="x", expand=True, padx=(0, 10))
        tk.Label(
            left,
            text="视频画质",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w")
        ttk.Combobox(
            left,
            textvariable=self.quality_var,
            values=list(QUALITY_LABELS.values()),
            state="readonly",
            style="App.TCombobox",
        ).pack(fill="x", pady=(2, 0))
        right = tk.Frame(settings_row, bg=COLORS["surface"])
        right.pack(side="left", fill="x", expand=True, padx=(10, 0))
        tk.Label(
            right,
            text="登录状态",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w")
        ttk.Combobox(
            right,
            textvariable=self.cookie_var,
            values=list(COOKIE_LABELS.values()),
            state="readonly",
            style="App.TCombobox",
        ).pack(fill="x", pady=(2, 0))

        output_row = tk.Frame(form, bg=COLORS["surface"])
        output_row.pack(fill="x", pady=(6, 0))
        tk.Label(
            output_row,
            text="保存到",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            width=7,
            anchor="w",
        ).pack(side="left")
        tk.Label(
            output_row,
            textvariable=self.output_preview_var,
            bg=COLORS["surface"],
            fg=COLORS["text"],
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        self._button(
            output_row,
            "修改目录",
            lambda: self.show_page("storage"),
            kind="soft",
            padx=12,
            pady=4,
        ).pack(side="right")

        agreement = tk.Frame(form, bg=COLORS["surface"])
        agreement.pack(fill="x", pady=(6, 0))
        tk.Checkbutton(
            agreement,
            text="我确认对该内容拥有下载、备份或其他合法使用权限",
            variable=self.rights_var,
            bg=COLORS["surface"],
            fg=COLORS["text"],
            activebackground=COLORS["surface"],
            selectcolor=COLORS["surface"],
        ).pack(side="left")
        tk.Label(
            agreement,
            text="下载受平台规则、地区、登录、付费和 DRM 限制",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        ).pack(side="right")

        progress_header = tk.Frame(form, bg=COLORS["surface"])
        progress_header.pack(fill="x", pady=(6, 3))
        tk.Label(
            progress_header,
            textvariable=self.progress_stage_var,
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 9, "bold"),
            anchor="w",
        ).pack(side="left")
        tk.Label(
            progress_header,
            textvariable=self.progress_percent_var,
            bg=COLORS["surface"],
            fg=COLORS["primary"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side="right")
        tk.Label(
            progress_header,
            textvariable=self.progress_detail_var,
            bg=COLORS["surface"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
            anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=(18, 12))
        self.progress = ttk.Progressbar(
            form,
            mode="determinate",
            maximum=100,
            value=0,
            style="App.Horizontal.TProgressbar",
        )
        self.progress.pack(fill="x", ipady=3)
        self.output_preview_var.set(str(resolve_output_dir("unknown")))

    def _build_history_page(self) -> None:
        _, body = self._new_page(
            "history",
            "任务记录",
            "查看最近下载结果。清除记录不会删除已经保存的文件。",
        )
        toolbar = tk.Frame(body, bg=COLORS["bg"])
        toolbar.pack(fill="x", pady=(0, 10))
        self._button(toolbar, "刷新", self.refresh_history, kind="soft").pack(side="left")
        self._button(toolbar, "打开选中文件", self.open_selected_history, kind="soft").pack(
            side="left", padx=8
        )
        self._button(toolbar, "清空记录", self.clear_history_ui, kind="danger").pack(
            side="right"
        )
        card = self._card(body, fill="both", expand=True)
        self.history_tree = ttk.Treeview(
            card,
            columns=("time", "platform", "title", "status", "output"),
            show="headings",
            style="History.Treeview",
        )
        headings = {
            "time": ("时间", 130),
            "platform": ("平台", 90),
            "title": ("标题", 250),
            "status": ("状态", 90),
            "output": ("保存位置", 360),
        }
        for key, (label, width) in headings.items():
            self.history_tree.heading(key, text=label)
            self.history_tree.column(key, width=width, minwidth=70)
        scrollbar = ttk.Scrollbar(card, orient="vertical", command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=scrollbar.set)
        self.history_tree.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=12)
        scrollbar.pack(side="right", fill="y", padx=(0, 12), pady=12)
        self.history_tree.bind("<Double-1>", lambda _event: self.open_selected_history())

    def _build_browser_page(self) -> None:
        _, body = self._new_page(
            "browser",
            "平台登录",
            "YouTube 使用 Firefox；抖音、小红书和小鹅通使用内置浏览器。",
        )
        notice = self._card(body, fill="x")
        tk.Label(
            notice,
            text="YouTube：先用 Firefox 登录并完全退出；其他平台的内置登录状态只保存在本机。",
            bg="#FFF8E8",
            fg="#855B13",
            padx=18,
            pady=14,
            anchor="w",
        ).pack(fill="x")
        grid = tk.Frame(body, bg=COLORS["bg"])
        grid.pack(fill="both", expand=True, pady=(16, 0))
        for index, platform in enumerate(PLATFORMS):
            metadata = platform_payload(platform)
            card = tk.Frame(
                grid,
                bg=COLORS["surface"],
                highlightbackground=COLORS["border"],
                highlightthickness=1,
            )
            row, column = divmod(index, 2)
            card.grid(row=row, column=column, sticky="nsew", padx=(0, 12), pady=(0, 12))
            grid.columnconfigure(column, weight=1)
            grid.rowconfigure(row, weight=1)
            tk.Label(
                card,
                text=PLATFORM_MARKS[platform],
                bg=PLATFORM_COLORS[platform],
                fg="white",
                width=4,
                pady=10,
                font=("Microsoft YaHei UI", 12, "bold"),
            ).pack(side="left", padx=18, pady=18)
            text_box = tk.Frame(card, bg=COLORS["surface"])
            text_box.pack(side="left", fill="both", expand=True, pady=18)
            tk.Label(
                text_box,
                text=metadata["name"],
                bg=COLORS["surface"],
                fg=COLORS["text"],
                font=("Microsoft YaHei UI", 11, "bold"),
            ).pack(anchor="w")
            supports = "、".join(metadata["supports"])
            tk.Label(
                text_box,
                text=f"支持：{supports}",
                bg=COLORS["surface"],
                fg=COLORS["muted"],
                font=("Microsoft YaHei UI", 8),
            ).pack(anchor="w", pady=(4, 8))
            login_button_text = {
                "youtube": "用 Firefox 登录 YouTube",
                "xiaoetong": "登录并查看已购课程",
            }.get(platform, "打开登录页")
            self._button(
                text_box,
                login_button_text,
                lambda value=platform: self.open_platform_browser(value),
                kind="soft",
                padx=12,
                pady=7,
            ).pack(anchor="w")
        footer = tk.Frame(body, bg=COLORS["bg"])
        footer.pack(fill="x")
        self._button(
            footer, "关闭内置浏览器", self.close_browser, kind="danger"
        ).pack(side="right")
        self._button(
            footer, "检测浏览器状态", self.check_status, kind="soft"
        ).pack(side="right", padx=8)

    def _build_storage_page(self) -> None:
        _, body = self._new_page(
            "storage",
            "保存设置",
            "设置一个默认目录，也可以让不同平台分别保存到自己的文件夹。",
        )
        card = self._card(body, fill="both", expand=True)
        inner = tk.Frame(card, bg=COLORS["surface"])
        inner.pack(fill="both", expand=True, padx=26, pady=22)

        tk.Label(
            inner,
            text="默认保存目录",
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Entry(
            inner, textvariable=self.default_dir_var, style="App.TEntry"
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 18))
        self._button(
            inner,
            "选择文件夹",
            lambda: self.choose_directory(self.default_dir_var),
            kind="soft",
            padx=12,
            pady=8,
        ).grid(row=1, column=2, padx=(10, 0), pady=(8, 18))
        tk.Label(
            inner,
            text="平台独立目录（留空就使用上面的默认目录）",
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 10))

        for index, platform in enumerate(PLATFORMS, start=3):
            tk.Label(
                inner,
                text=platform_payload(platform)["name"],
                bg=COLORS["surface"],
                fg=COLORS["text"],
                width=10,
                anchor="w",
            ).grid(row=index, column=0, sticky="w", pady=5)
            ttk.Entry(
                inner,
                textvariable=self.platform_dir_vars[platform],
                style="App.TEntry",
            ).grid(row=index, column=1, sticky="ew", pady=5)
            self._button(
                inner,
                "浏览",
                lambda variable=self.platform_dir_vars[platform]: self.choose_directory(
                    variable
                ),
                kind="soft",
                padx=12,
                pady=7,
            ).grid(row=index, column=2, padx=(10, 0), pady=5)

        options_row = tk.Frame(inner, bg=COLORS["surface"])
        options_row.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(18, 4))
        tk.Checkbutton(
            options_row,
            text="需要登录或验证时显示内置浏览器",
            variable=self.show_browser_var,
            bg=COLORS["surface"],
            activebackground=COLORS["surface"],
            selectcolor=COLORS["surface"],
        ).pack(side="left")
        self._button(
            options_row, "保存设置", self.save_settings, padx=24
        ).pack(side="right")
        inner.columnconfigure(1, weight=1)

    def _build_guide_page(self) -> None:
        _, body = self._new_page(
            "guide",
            "使用帮助",
            "从第一次打开到常见问题处理，按步骤操作即可。",
        )
        card = self._card(body, fill="both", expand=True)
        guide = ScrolledText(
            card,
            wrap="word",
            relief="flat",
            bd=0,
            bg=COLORS["surface"],
            fg=COLORS["text"],
            padx=24,
            pady=20,
            font=("Microsoft YaHei UI", 10),
            spacing1=4,
            spacing3=8,
        )
        guide.pack(fill="both", expand=True, padx=2, pady=2)
        guide.tag_configure(
            "h1", font=("Microsoft YaHei UI", 16, "bold"), foreground=COLORS["text"]
        )
        guide.tag_configure(
            "h2", font=("Microsoft YaHei UI", 11, "bold"), foreground=COLORS["primary_dark"]
        )
        guide.tag_configure("muted", foreground=COLORS["muted"])
        guide_path = CODE_DIR / "详细使用教程.md"
        if guide_path.is_file():
            try:
                for line in guide_path.read_text(encoding="utf-8").splitlines(keepends=True):
                    if line.startswith("# "):
                        guide.insert("end", line[2:], "h1")
                    elif line.startswith("## "):
                        guide.insert("end", line[3:], "h2")
                    elif line.startswith("### "):
                        guide.insert("end", line[4:], "h2")
                    else:
                        guide.insert("end", line)
                guide.configure(state="disabled")
                return
            except OSError:
                pass
        sections = [
            (
                "h1",
                "轻舟下载器 · 完整使用方法\n",
            ),
            (
                "muted",
                "适用于 YouTube、抖音、小红书视频与图文、哔哩哔哩和你已合法购买的小鹅通课程。\n\n",
            ),
            (
                "h2",
                "一、第一次使用\n",
            ),
            (
                "",
                "1. 打开左侧“保存设置”。\n"
                "2. 选择一个默认保存目录，例如 D:\\下载内容。\n"
                "3. 如果希望分类保存，为 YouTube、抖音、小红书、B站或小鹅通分别选择文件夹；留空的平台会使用默认目录。\n"
                "4. 点击“保存设置”。\n"
                "5. 进入“关于与合规”，阅读并勾选使用条款。\n\n",
            ),
            (
                "h2",
                "二、普通视频下载（YouTube / B站）\n",
            ),
            (
                "",
                "1. 在平台中打开具体的视频页面，不要复制首页或搜索页。\n"
                "2. 复制地址栏链接或使用平台的“分享→复制链接”。\n"
                "3. 回到“下载中心”，可直接粘贴整段分享文案。\n"
                "4. 确认平台识别正确，选择画质。\n"
                "5. 若视频要求登录，在“登录状态”选择你已经登录的 Firefox（推荐）、Edge 或 Chrome。\n"
                "6. 勾选权利确认，点击“开始下载”。音视频会自动合并为 MP4。\n\n",
            ),
            (
                "h2",
                "三、抖音视频下载\n",
            ),
            (
                "",
                "1. 复制单条作品分享链接。\n"
                "2. 粘贴后开始下载。软件会打开内置浏览器锁定当前作品。\n"
                "3. 若出现登录或验证码，在弹出的内置浏览器中手动完成，然后保持作品页打开。\n"
                "4. 下载完成前不要关闭内置浏览器。只支持单条作品，不支持把推荐流当作一条作品下载。\n\n",
            ),
            (
                "h2",
                "四、小红书视频与完整图文\n",
            ),
            (
                "",
                "1. 打开具体笔记，复制分享链接。\n"
                "2. 粘贴并下载。软件会自动判断是视频还是图文。\n"
                "3. 视频保存为 MP4；图文会创建独立文件夹，保存全部可获取图片，并同时写入标题和作品信息文件。\n"
                "4. 若只能看到登录页，先去“平台登录”打开小红书并完成登录，再重试。\n\n",
            ),
            (
                "h2",
                "五、小鹅通已购课程\n",
            ),
            (
                "",
                "1. 必须使用你自己的合法账号，并确保课程许可个人离线观看。\n"
                "2. 先去“平台登录→小鹅通→登录并查看已购课程”，使用购买课程时相同的手机号、微信或机构账号登录。\n"
                "3. 在小鹅通“我的课程”中确认能找到并打开该课程；从已购课程入口进入播放页最可靠。\n"
                "4. 复制正在播放课程时地址栏里的完整链接，回到下载中心粘贴并开始下载。\n"
                "5. 必要时在内置浏览器播放几秒，让课程流地址出现。\n"
                "6. 若显示“尚未购买或领取”，说明当前内置浏览器身份或链接对应的课程 ID 与订单不一致；请切换为购买时的登录方式，或联系课程商家核对订单。\n"
                "7. 付费访问、DRM 加密、机构限制或平台禁止下载的课程不会被绕过。\n\n",
            ),
            (
                "h2",
                "六、遇到失败怎么办\n",
            ),
            (
                "",
                "• 提示链接无效：确认复制的是单条作品页，并重新复制完整链接。\n"
                "• 提示需要登录：先在“平台登录”完成登录；YouTube/B站也可选择系统浏览器登录状态。\n"
                "• 提示没有指定画质：改为“自动最高画质”或更低画质。\n"
                "• 下载停住：点击红色“取消下载”按钮；取消完成后再检查登录状态或链接。\n"
                "• 平台页面改版：运行 media-cli.cmd doctor，把返回的 JSON 发给技术支持；不要发送 Cookie、密码或验证码。\n"
                "• 已下载文件在哪里：查看“下载中心”的保存位置，或在“任务记录”双击记录。\n\n",
            ),
            (
                "h2",
                "七、给 AI / 自动化使用\n",
            ),
            (
                "",
                "软件目录内提供 media-cli.cmd。所有命令只在标准输出返回 JSON。\n"
                "常用命令：\n"
                "  media-cli.cmd doctor\n"
                "  media-cli.cmd detect \"分享文本或链接\"\n"
                "  media-cli.cmd config-get\n"
                "  media-cli.cmd auto-download \"链接\" --confirm-rights\n"
                "  media-cli.cmd status\n"
                "  media-cli.cmd history --limit 20\n"
                "更完整示例见软件目录中的《CLI使用说明.md》。\n",
            ),
        ]
        for tag, text in sections:
            guide.insert("end", text, tag)
        guide.configure(state="disabled")

    def _build_about_page(self) -> None:
        _, body = self._new_page(
            "about",
            "关于与合规",
            "使用软件前请阅读。软件提供本地工具，不授予任何第三方内容权利。",
        )
        card = self._card(body, fill="both", expand=True)
        inner = tk.Frame(card, bg=COLORS["surface"])
        inner.pack(fill="both", expand=True, padx=28, pady=24)
        tk.Label(
            inner,
            text=f"{APP_NAME}  {APP_VERSION}",
            bg=COLORS["surface"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 17, "bold"),
        ).pack(anchor="w")
        tk.Label(
            inner,
            text="本地运行 · 用户数据隔离 · JSON CLI 可自动化",
            bg=COLORS["surface"],
            fg=COLORS["muted"],
        ).pack(anchor="w", pady=(4, 18))
        disclaimer = (
            "重要声明\n\n"
            "1. 本软件仅是一种本地内容保存工具，不提供、出售或转让任何视频、图片、课程、音乐、商标或账号的版权及使用许可。\n\n"
            "2. 用户必须自行确认其下载、备份、研究、剪辑、转载、传播或商业使用行为已取得权利人授权，并符合所在地法律、平台服务条款、课程购买协议及组织内部规定。\n\n"
            "3. 用户自行输入链接、登录账号、选择内容和发起下载。因用户未经授权下载、侵犯版权或隐私、泄露商业秘密、违反平台规则、二次传播或其他不当用途产生的责任，由用户自行承担；软件作者与销售方不参与用户内容选择和后续用途。\n\n"
            "4. 本软件不会承诺所有链接永久可用，不会绕过付费墙、数字版权管理（DRM）、地区限制、账号权限、验证码或其他访问控制。平台改版、网络环境、登录状态、内容删除、版权限制都可能导致下载失败。\n\n"
            "5. 请勿向技术支持发送密码、Cookie、验证码、私钥或其他敏感信息。诊断时只提供 doctor 命令输出和错误提示。\n\n"
            "点击同意仅表示你理解上述边界；它不能替代具体法律意见，也不能把原本无权进行的行为变为合法。"
        )
        text = ScrolledText(
            inner,
            wrap="word",
            height=18,
            relief="flat",
            bg="#F7F9FC",
            fg=COLORS["text"],
            padx=18,
            pady=16,
            font=("Microsoft YaHei UI", 9),
            spacing3=4,
        )
        text.pack(fill="both", expand=True)
        text.insert("1.0", disclaimer)
        text.configure(state="disabled")
        agreement = tk.Frame(inner, bg=COLORS["surface"])
        agreement.pack(fill="x", pady=(16, 0))
        tk.Checkbutton(
            agreement,
            text="我已阅读并理解以上使用边界，承诺只处理自己有权使用的内容",
            variable=self.accepted_terms_var,
            bg=COLORS["surface"],
            activebackground=COLORS["surface"],
            selectcolor=COLORS["surface"],
        ).pack(side="left")
        self._button(agreement, "保存确认", self.save_terms, padx=20).pack(side="right")

    def show_page(self, key: str) -> None:
        for page in self.pages.values():
            page.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        self.current_page = key
        for name, button in self.nav_buttons.items():
            selected = name == key
            button.configure(
                bg=COLORS["sidebar_hover"] if selected else COLORS["sidebar"],
                fg="white" if selected else "#B7C1D6",
            )
        if key == "history":
            self.refresh_history()
        if key == "download":
            self._update_output_preview()

    def _schedule_detection(self, *_args: Any) -> None:
        self.manual_platform = None
        if self.detect_after:
            self.after_cancel(self.detect_after)
        self.detect_after = self.after(250, self._detect_url)

    def select_platform(self, platform: str) -> None:
        self.manual_platform = platform
        payload = platform_payload(platform)
        self.platform_mark.configure(
            text=PLATFORM_MARKS.get(platform, "?"),
            bg=PLATFORM_COLORS.get(platform, PLATFORM_COLORS["unknown"]),
        )
        self.platform_var.set(f"已手动指定：{payload['name']}")
        if payload["engine"] == "browser":
            self.cookie_var.set(COOKIE_LABELS["app"])
            self.platform_hint_var.set(
                "自动识别不对时可手动指定；下载时会打开内置浏览器"
            )
        elif platform == "youtube":
            self.cookie_var.set(COOKIE_LABELS["firefox"])
            self.platform_hint_var.set(
                "先用 Firefox 登录 YouTube 并完全退出 Firefox，再开始下载"
            )
        else:
            configured = str(self.config_data.get("browser_cookies") or "none")
            self.cookie_var.set(COOKIE_LABELS.get(configured, COOKIE_LABELS["none"]))
            self.platform_hint_var.set("已手动指定平台；修改链接后会恢复自动识别")
        self._update_output_preview(platform)

    def _detect_url(self) -> None:
        platform = detect_platform(self.url_var.get())
        payload = platform_payload(platform)
        self.platform_mark.configure(
            text=PLATFORM_MARKS.get(platform, "?"),
            bg=PLATFORM_COLORS.get(platform, PLATFORM_COLORS["unknown"]),
        )
        if payload["supported"]:
            supports = "、".join(payload["supports"])
            self.platform_var.set(f"已识别：{payload['name']}")
            if payload["engine"] == "browser":
                self.cookie_var.set(COOKIE_LABELS["app"])
                self.platform_hint_var.set(
                    f"支持 {supports} · 下载时会打开内置浏览器供你登录或验证"
                )
            elif platform == "youtube":
                self.cookie_var.set(COOKIE_LABELS["firefox"])
                self.platform_hint_var.set(
                    "YouTube 标准流程：Firefox 登录并确认视频能播放 → 完全退出 Firefox → 开始下载"
                )
            else:
                if self.cookie_var.get() == COOKIE_LABELS["app"]:
                    configured = str(self.config_data.get("browser_cookies") or "none")
                    self.cookie_var.set(COOKIE_LABELS.get(configured, COOKIE_LABELS["none"]))
                self.platform_hint_var.set(
                    f"支持 {supports} · 将自动使用{payload['engine']}下载引擎"
                )
        elif self.url_var.get().strip():
            self.platform_var.set("暂未识别该链接")
            self.platform_hint_var.set("请粘贴支持平台的单条作品或课程播放链接")
        else:
            self.platform_var.set("粘贴链接后自动识别平台")
            self.platform_hint_var.set("支持分享文本，软件会自动提取其中的链接")
        self._update_output_preview(platform)

    def _update_output_preview(self, platform: str | None = None) -> None:
        platform = platform or detect_platform(self.url_var.get())
        try:
            path = resolve_output_dir(platform)
        except OSError:
            path = Path(str(self.config_data.get("default_output_dir") or CODE_DIR))
        self.output_preview_var.set(str(path))

    def start_download(self) -> None:
        if self.busy:
            self.cancel_download()
            return
        if not self.accepted_terms_var.get():
            messagebox.showwarning(
                "请先阅读使用声明",
                "请先进入“关于与合规”，阅读并保存确认。",
            )
            self.show_page("about")
            return
        value = self.url_var.get().strip()
        if not value:
            messagebox.showwarning("缺少链接", "请先粘贴完整的作品或课程播放链接。")
            return
        candidate = extract_url(value)
        if not candidate.casefold().startswith(("http://", "https://")):
            messagebox.showerror(
                "这不是完整网址",
                "请粘贴浏览器地址栏里的完整网址。\n\n"
                "链接必须以 https:// 或 http:// 开头；"
                "不能只粘贴 video/v_... 这一段。",
            )
            return
        platform = self.manual_platform or detect_platform(value)
        if platform == "unknown":
            messagebox.showerror(
                "链接无法识别",
                "请粘贴 YouTube、抖音、小红书、B站或小鹅通的单条作品链接。\n\n"
                "小鹅通请复制正在播放课程时浏览器地址栏里的完整网址，"
                "或点击页面上方的“小鹅通”手动指定平台。",
            )
            return
        if not self.rights_var.get():
            messagebox.showwarning(
                "请勾选下载确认",
                "如果这是你有权离线保存的课程，请勾选页面下方的\n"
                "“我确认对该内容拥有下载、备份或其他合法使用权限”，"
                "然后再点“开始下载”。",
            )
            return
        quality = next(
            (key for key, label in QUALITY_LABELS.items() if label == self.quality_var.get()),
            "1080",
        )
        browser_cookies = next(
            (key for key, label in COOKIE_LABELS.items() if label == self.cookie_var.get()),
            "none",
        )
        if browser_cookies == "app":
            browser_cookies = "none"
        args = [
            "auto-download",
            value,
            "--platform",
            platform,
            "--quality",
            quality,
            "--confirm-rights",
        ]
        if browser_cookies != "none":
            args.extend(["--browser-cookies", browser_cookies])
        if platform == "xiaoetong":
            args.extend(["--media-timeout", "180", "--download-timeout", "3600"])
        args.append("--show-browser" if self.show_browser_var.get() else "--no-show-browser")
        self.cancel_requested = False
        self.download_started_at = time.time()
        for progress_file in (
            STATE_DIR / "direct_download.json",
            STATE_DIR / "browser_download.json",
        ):
            try:
                progress_file.unlink(missing_ok=True)
            except OSError:
                pass
        self._set_busy(True, f"正在准备 {platform_payload(platform)['name']} 下载…")
        self._run_cli(args, self._download_finished, download_task=True)

    def _download_finished(self, result: dict[str, Any], return_code: int) -> None:
        if self.cancel_requested:
            self.cancel_requested = False
            self._set_busy(False, "下载已取消")
            self._show_terminal_progress("下载已取消", "—", "未完成的临时任务已停止", 0)
            return
        self._set_busy(False, "准备就绪")
        if return_code == 0 and result.get("ok"):
            output = str(result.get("output") or "")
            kind = str(result.get("kind") or "video")
            self.status_var.set(f"下载完成：{output}")
            self._show_terminal_progress(
                "下载完成", "100%", "文件已保存到设定目录", 100
            )
            messagebox.showinfo(
                "下载完成",
                f"{'图文' if kind == 'images' else '视频'}已保存：\n{output}",
            )
            self.rights_var.set(False)
            self.refresh_history()
            return
        error = result.get("error") or {}
        message = error.get("message") if isinstance(error, dict) else str(error)
        if not message:
            message = result.get("_stderr") or "下载失败，请运行诊断并查看错误。"
        self.status_var.set(f"下载失败：{message}")
        self._show_terminal_progress("下载失败", "—", str(message), 0)
        messagebox.showerror("下载失败", str(message))

    def _set_busy(self, busy: bool, status: str) -> None:
        self.busy = busy
        self.status_var.set(status)
        self.status_dot.configure(fg=COLORS["warning"] if busy else COLORS["success"])
        self.download_button.configure(
            state="normal",
            text="取消下载" if busy else "开始下载",
            bg=COLORS["danger"] if busy else COLORS["primary"],
            activebackground=COLORS["danger"] if busy else COLORS["primary"],
        )
        if busy:
            self.progress_stage_var.set(status)
            self.progress_percent_var.set("等待数据")
            self.progress_detail_var.set("正在解析链接并获取媒体信息…")
            self.progress.configure(mode="indeterminate", value=0)
            self.progress.start(12)
            self.progress_indeterminate_running = True
            self._schedule_progress_poll()
        else:
            self.progress.stop()
            self.progress_indeterminate_running = False
            if self.progress_after is not None:
                try:
                    self.after_cancel(self.progress_after)
                except tk.TclError:
                    pass
                self.progress_after = None

    def _show_terminal_progress(
        self, stage: str, percent: str, detail: str, value: float
    ) -> None:
        self.progress.stop()
        self.progress_indeterminate_running = False
        self.progress.configure(mode="determinate", value=value)
        self.progress_stage_var.set(stage)
        self.progress_percent_var.set(percent)
        self.progress_detail_var.set(detail)

    def _schedule_progress_poll(self) -> None:
        if self.progress_after is not None:
            try:
                self.after_cancel(self.progress_after)
            except tk.TclError:
                pass
        self.progress_after = self.after(350, self._poll_download_progress)

    @staticmethod
    def _read_progress_file(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def _poll_download_progress(self) -> None:
        self.progress_after = None
        if not self.busy:
            return
        candidates = [
            self._read_progress_file(STATE_DIR / "direct_download.json"),
            self._read_progress_file(STATE_DIR / "browser_download.json"),
        ]
        fresh = [
            item
            for item in candidates
            if float(item.get("timestamp") or 0) >= self.download_started_at - 1
        ]
        if fresh:
            latest = max(fresh, key=lambda item: float(item.get("timestamp") or 0))
            display = progress_payload(latest)
            self.progress_stage_var.set(str(display["stage"]))
            self.progress_detail_var.set(str(display["detail"]))
            percent = display.get("percent")
            if display.get("determinate") and percent is not None:
                if self.progress_indeterminate_running:
                    self.progress.stop()
                    self.progress_indeterminate_running = False
                self.progress.configure(mode="determinate", value=float(percent))
                self.progress_percent_var.set(f"{float(percent):.1f}%")
            else:
                if not self.progress_indeterminate_running:
                    self.progress.configure(mode="indeterminate", value=0)
                    self.progress.start(12)
                    self.progress_indeterminate_running = True
                self.progress_percent_var.set("计算中")
        self._schedule_progress_poll()

    def _run_cli(
        self,
        arguments: list[str],
        callback: Callable[[dict[str, Any], int], None],
        *,
        download_task: bool = False,
    ) -> None:
        def worker() -> None:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process: subprocess.Popen[str] | None = None
            try:
                command = (
                    [str(CLI_FILE), *arguments]
                    if getattr(sys, "frozen", False)
                    else [sys.executable, str(CLI_FILE), *arguments]
                )
                process = subprocess.Popen(
                    command,
                    cwd=str(CODE_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=flags,
                )
                if download_task:
                    with self.download_process_lock:
                        self.active_download_process = process
                    if self.cancel_requested:
                        self._terminate_process(process)
                stdout, stderr = process.communicate(timeout=3700)
                try:
                    payload = json.loads(stdout)
                except json.JSONDecodeError:
                    payload = {
                        "ok": False,
                        "_stdout": stdout,
                        "_stderr": stderr,
                        "error": {
                            "code": "INVALID_CLI_RESPONSE",
                            "message": "下载引擎返回了无法解析的结果。",
                        },
                    }
                if stderr.strip():
                    payload["_stderr"] = stderr.strip()
                self.after(0, lambda: callback(payload, process.returncode))
            except subprocess.TimeoutExpired:
                if process is not None:
                    self._terminate_process(process)
                self.after(
                    0,
                    lambda: callback(
                        {
                            "ok": False,
                            "error": {
                                "code": "GUI_TIMEOUT",
                                "message": "任务运行时间过长，已停止等待。请查看任务记录和输出目录。",
                            },
                        },
                        124,
                    ),
                )
            except OSError as exc:
                self.after(
                    0,
                    lambda: callback(
                        {
                            "ok": False,
                            "error": {"code": "CLI_START_FAILED", "message": str(exc)},
                        },
                        1,
                    ),
                )
            finally:
                if download_task:
                    with self.download_process_lock:
                        if self.active_download_process is process:
                            self.active_download_process = None

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    timeout=8,
                    check=False,
                )
            else:
                process.terminate()
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass

    def cancel_download(self) -> None:
        if not self.busy or self.cancel_requested:
            return
        self.cancel_requested = True
        self.status_var.set("正在取消下载…")
        self.download_button.configure(state="disabled", text="正在取消…")

        def cancel_worker() -> None:
            with self.download_process_lock:
                process = self.active_download_process
            if process is not None:
                self._terminate_process(process)
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            command = (
                [str(CLI_FILE), "cancel-download"]
                if getattr(sys, "frozen", False)
                else [sys.executable, str(CLI_FILE), "cancel-download"]
            )
            try:
                subprocess.run(
                    command,
                    cwd=str(CODE_DIR),
                    capture_output=True,
                    creationflags=flags,
                    timeout=12,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass

        threading.Thread(target=cancel_worker, daemon=True).start()

    def refresh_history(self) -> None:
        if not hasattr(self, "history_tree"):
            return
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        names = {platform: platform_payload(platform)["name"] for platform in PLATFORMS}
        for row in list_history(200):
            moment = datetime.fromtimestamp(float(row.get("timestamp") or 0)).strftime(
                "%Y-%m-%d %H:%M"
            )
            status = "完成" if row.get("status") == "completed" else "失败"
            self.history_tree.insert(
                "",
                "end",
                iid=str(row.get("id") or time.time_ns()),
                values=(
                    moment,
                    names.get(str(row.get("platform")), "未知"),
                    row.get("title") or "未命名作品",
                    status,
                    row.get("output") or row.get("error") or "",
                ),
            )

    def open_selected_history(self) -> None:
        selected = self.history_tree.selection()
        if not selected:
            messagebox.showinfo("请选择记录", "请先选择一条下载记录。")
            return
        values = self.history_tree.item(selected[0], "values")
        raw = str(values[4] if len(values) > 4 else "")
        path = Path(raw)
        target = path if path.is_dir() else path.parent
        if not target.exists():
            messagebox.showwarning("文件不存在", f"找不到保存位置：\n{raw}")
            return
        os.startfile(str(target))

    def clear_history_ui(self) -> None:
        if not messagebox.askyesno(
            "清空记录",
            "只清除软件内的任务记录，不会删除已下载文件。确定继续吗？",
        ):
            return
        self._run_cli(["history-clear", "--yes"], self._history_cleared)

    def _history_cleared(self, result: dict[str, Any], return_code: int) -> None:
        if return_code == 0 and result.get("ok"):
            self.refresh_history()
            self.status_var.set(f"已清空 {result.get('cleared', 0)} 条记录")
        else:
            messagebox.showerror("清空失败", str(result))

    def choose_directory(self, variable: tk.StringVar) -> None:
        initial = variable.get().strip() or self.default_dir_var.get().strip()
        selected = filedialog.askdirectory(
            title="选择保存文件夹",
            initialdir=initial if Path(initial).is_dir() else str(Path.home()),
        )
        if selected:
            variable.set(selected)

    def save_settings(self) -> None:
        default_dir = self.default_dir_var.get().strip()
        if not default_dir:
            messagebox.showwarning("需要默认目录", "请选择一个默认保存目录。")
            return
        try:
            Path(default_dir).expanduser().resolve().mkdir(parents=True, exist_ok=True)
            updates = {
                "default_output_dir": str(Path(default_dir).expanduser().resolve()),
                "platform_output_dirs": {
                    platform: (
                        str(Path(variable.get()).expanduser().resolve())
                        if variable.get().strip()
                        else ""
                    )
                    for platform, variable in self.platform_dir_vars.items()
                },
                "quality": next(
                    (
                        key
                        for key, label in QUALITY_LABELS.items()
                        if label == self.quality_var.get()
                    ),
                    "1080",
                ),
                "browser_cookies": next(
                    (
                        key
                        for key, label in COOKIE_LABELS.items()
                        if label == self.cookie_var.get()
                    ),
                    "none",
                ),
                "show_browser_when_needed": self.show_browser_var.get(),
            }
            if updates["browser_cookies"] == "app":
                updates["browser_cookies"] = "none"
            for path in updates["platform_output_dirs"].values():
                if path:
                    Path(path).mkdir(parents=True, exist_ok=True)
            self.config_data = save_config(updates)
        except OSError as exc:
            messagebox.showerror("保存失败", f"无法创建或写入目录：\n{exc}")
            return
        self._update_output_preview()
        self.status_var.set("保存设置已更新")
        messagebox.showinfo("设置已保存", "以后下载会自动使用对应平台目录。")

    def save_terms(self) -> None:
        if not self.accepted_terms_var.get():
            messagebox.showwarning("尚未确认", "请先勾选阅读与使用确认。")
            return
        self.config_data = save_config({"accepted_terms": True})
        self.status_var.set("使用声明确认已保存")
        messagebox.showinfo("已保存", "你现在可以回到下载中心使用软件。")

    def open_platform_browser(self, platform: str) -> None:
        metadata = PLATFORMS[platform]
        url = str(metadata.get("home") or "")
        if platform == "youtube":
            firefox = find_firefox_executable()
            if firefox is None:
                if messagebox.askyesno(
                    "需要安装 Firefox",
                    "YouTube 下载使用 Firefox 登录状态。当前没有找到 Firefox。\n\n"
                    "是否打开 Firefox 官方下载页面？",
                ):
                    try:
                        os.startfile("https://www.mozilla.org/firefox/new/")
                    except OSError as exc:
                        messagebox.showerror("无法打开网页", str(exc))
                return
            try:
                subprocess.Popen([str(firefox), url])
            except OSError as exc:
                messagebox.showerror("Firefox 启动失败", str(exc))
                return
            self.cookie_var.set(COOKIE_LABELS["firefox"])
            self.status_var.set("已打开 Firefox，请登录 YouTube并确认视频能播放")
            messagebox.showinfo(
                "下一步",
                "1. 在 Firefox 登录 YouTube。\n"
                "2. 打开目标视频并确认可以播放。\n"
                "3. 完全关闭所有 Firefox 窗口。\n"
                "4. 回到下载中心粘贴链接并开始下载。",
            )
            return
        self.status_var.set(f"正在打开 {metadata['name']} 登录页…")
        self._run_cli(
            ["browser-open", url, "--show", "--timeout", "30"],
            lambda result, code: self._browser_opened(metadata["name"], result, code),
        )

    def _browser_opened(
        self, name: str, result: dict[str, Any], return_code: int
    ) -> None:
        if return_code == 0 and result.get("ok"):
            self.status_var.set(f"{name} 登录页已打开")
        else:
            error = result.get("error") or {}
            messagebox.showerror(
                "浏览器启动失败",
                str(error.get("message") if isinstance(error, dict) else error),
            )

    def close_browser(self) -> None:
        self._run_cli(["close-browser"], self._browser_closed)

    def _browser_closed(self, result: dict[str, Any], return_code: int) -> None:
        if return_code == 0 and result.get("ok"):
            self.status_var.set("内置浏览器已关闭")
        else:
            messagebox.showwarning("关闭失败", str(result))

    def check_status(self) -> None:
        self._run_cli(["status"], self._status_checked)

    def _status_checked(self, result: dict[str, Any], return_code: int) -> None:
        if return_code != 0:
            messagebox.showerror("检测失败", str(result))
            return
        browser = "运行中" if result.get("browser_running") else "未运行"
        page = result.get("page_state") or {}
        url = page.get("page_url") or "无"
        messagebox.showinfo("浏览器状态", f"内置浏览器：{browser}\n当前页面：{url}")

    def _first_run_notice(self) -> None:
        if not self.accepted_terms_var.get():
            self.show_page("about")
            messagebox.showinfo(
                "欢迎使用轻舟下载器",
                "第一次使用请先阅读“关于与合规”，保存确认后再设置下载目录。",
            )
        else:
            self.url_entry.focus_set()

    def _on_close(self) -> None:
        if self.busy and not messagebox.askyesno(
            "任务仍在进行",
            "关闭主窗口会取消当前下载。确定关闭吗？",
        ):
            return
        if self.busy:
            self.cancel_requested = True
            with self.download_process_lock:
                process = self.active_download_process
            if process is not None:
                self._terminate_process(process)
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            command = (
                [str(CLI_FILE), "cancel-download", "--timeout", "1"]
                if getattr(sys, "frozen", False)
                else [
                    sys.executable,
                    str(CLI_FILE),
                    "cancel-download",
                    "--timeout",
                    "1",
                ]
            )
            try:
                subprocess.Popen(
                    command,
                    cwd=str(CODE_DIR),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags,
                )
            except OSError:
                pass
        self.destroy()


def main() -> int:
    enable_windows_dpi()
    if "--self-test" in sys.argv:
        app = DownloaderApp()
        app.update_idletasks()
        app.update()
        result = {
            "ok": True,
            "title": app.title(),
            "width": app.winfo_width(),
            "height": app.winfo_height(),
            "pages": sorted(app.pages),
            "cli_exists": CLI_FILE.is_file(),
        }
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "gui-self-test.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        app.destroy()
        return 0
    if not acquire_single_instance():
        try:
            ctypes.windll.user32.MessageBoxW(
                None, f"{APP_NAME} 已经在运行。", APP_NAME, 0x40
            )
        except (AttributeError, OSError):
            pass
        return 2
    app = DownloaderApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
