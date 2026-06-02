from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

from .config import load_config
from .excel_report import write_excel_report
from .history import archive_result
from .images import generate_report_images
from .paths import app_root
from .processor import ProcessResult, process_reports


ROOT = app_root()
ICON_DIR = ROOT / "assets" / "icons"
ICON_SIZE = 40
APP_ICON_SIZE = 128
FILE_PATH_WIDTH = 280
FILE_PATH_HEIGHT = 34


class ReportApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("团购运营报表工具")
        self.geometry("1120x780")
        self.minsize(900, 640)
        self.resizable(True, True)
        self.configure(bg="#F7F7F8")

        self.config_path = tk.StringVar(value=str(ROOT / "配置表" / "报表工具配置模板.xlsx"))
        self.template_path = tk.StringVar(value=str(ROOT / "配置表" / "输出报表模板.xlsx"))
        self.output_dir = tk.StringVar(value=str(ROOT / "outputs"))
        self.meituan_path = tk.StringVar(value="")
        self.douyin_path = tk.StringVar(value="")
        self.brand = tk.StringVar(value="团购运营")
        self.summary_text = tk.StringVar(value="等待导入")

        self.last_result: ProcessResult | None = None
        self.icons: dict[str, ImageTk.PhotoImage] = {}
        self.cards: dict[str, tk.Frame] = {}
        self.file_cards: dict[str, dict[str, object]] = {}
        self.ranking_vars: list[tuple[object, tk.BooleanVar]] = []
        self.ranking_tiles: list[dict[str, object]] = []
        self.content_frame: tk.Frame | None = None
        self.ranking_grid: tk.Frame | None = None
        self.action_button_frame: tk.Frame | None = None
        self.scroll_canvas: tk.Canvas | None = None
        self._mousewheel_bound_widgets: set[str] = set()

        self._load_icons()
        self._configure_styles()
        self._apply_app_icon()
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        shell = tk.Frame(self, bg="#F7F7F8")
        shell.grid(row=0, column=0, sticky=tk.NSEW)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(1, weight=1)

        self._build_header(shell)
        self._build_scroll_area(shell)
        self._build_cards()
        self.bind("<Configure>", self._on_resize)
        self._log("请选择平台报表。配置表、输出模板和输出目录使用固定目录结构。")

    def _build_header(self, parent: tk.Frame) -> None:
        header = tk.Frame(parent, bg="#F7F7F8")
        header.grid(row=0, column=0, sticky=tk.EW, padx=22, pady=(20, 12))
        header.columnconfigure(0, weight=1)

        title = tk.Label(header, text="团购运营报表工具", bg="#F7F7F8", fg="#101214", font=self._font(24, "bold"))
        title.grid(row=0, column=0, sticky=tk.W)
        subtitle = tk.Label(
            header,
            text="导入平台报表，生成 Excel 汇总、综合简报与排行榜图片",
            bg="#F7F7F8",
            fg="#6B7280",
            font=self._font(12),
        )
        subtitle.grid(row=1, column=0, sticky=tk.W, pady=(6, 0))

        status = tk.Label(
            header,
            textvariable=self.summary_text,
            bg="#ECEEF2",
            fg="#30343A",
            font=self._font(11, "bold"),
            padx=14,
            pady=8,
        )
        status.grid(row=0, column=1, rowspan=2, sticky=tk.E, padx=(18, 0))

    def _build_scroll_area(self, parent: tk.Frame) -> None:
        holder = tk.Frame(parent, bg="#F7F7F8")
        holder.grid(row=1, column=0, sticky=tk.NSEW)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)

        canvas = tk.Canvas(holder, bg="#F7F7F8", highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(holder, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky=tk.NSEW)
        scrollbar.grid(row=0, column=1, sticky=tk.NS)
        self.scroll_canvas = canvas

        content = tk.Frame(canvas, bg="#F7F7F8")
        window_id = canvas.create_window((0, 0), window=content, anchor=tk.NW)
        content.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window_id, width=event.width))
        self.content_frame = content
        self._bind_mousewheel_recursive(holder)
        self.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.bind_all("<Button-4>", self._on_mousewheel, add="+")
        self.bind_all("<Button-5>", self._on_mousewheel, add="+")

    def _build_cards(self) -> None:
        if self.content_frame is None:
            return
        imports = self._section(self.content_frame, "选择报表", "选择一个或两个平台数据源")
        actions = self._section(self.content_frame, "生成操作", "品牌名和输出操作分开排列，窗口缩放时不裁切")
        rankings = self._section(self.content_frame, "报告图榜单", "点击卡片选择要导出的排行榜，默认不选择")
        log = self._section(self.content_frame, "运行日志", "处理过程和输出路径")
        self.cards = {"imports": imports, "actions": actions, "rankings": rankings, "log": log}

        self._build_import_section(imports.body)  # type: ignore[attr-defined]
        self._build_action_section(actions.body)  # type: ignore[attr-defined]
        self._build_ranking_section(rankings.body)  # type: ignore[attr-defined]
        self._build_log_section(log.body)  # type: ignore[attr-defined]
        self._place_cards()

    def _build_import_section(self, body: tk.Frame) -> None:
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        self.file_cards["meituan"] = self._file_card(body, "美团报表", self.meituan_path, "meituan", self._pick_meituan, 0)
        self.file_cards["douyin"] = self._file_card(body, "抖音报表", self.douyin_path, "douyin", self._pick_douyin, 1)

    def _build_action_section(self, body: tk.Frame) -> None:
        body.columnconfigure(0, weight=1)
        tk.Label(body, text="报告图品牌名", bg="#FFFFFF", fg="#5F6673", font=self._font(11)).grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(body, textvariable=self.brand, style="Modern.TEntry").grid(row=1, column=0, sticky=tk.EW, pady=(8, 14))

        self.action_button_frame = tk.Frame(body, bg="#FFFFFF")
        self.action_button_frame.grid(row=2, column=0, sticky=tk.EW)
        self._action_button("生成 Excel 报表", "excel", self._generate_excel, primary=True)
        self._action_button("生成报告图", "image", self._generate_images, primary=True)
        self._action_button("刷新榜单", "refresh", self._load_ranking_options, primary=False)

    def _build_ranking_section(self, body: tk.Frame) -> None:
        toolbar = tk.Frame(body, bg="#FFFFFF")
        toolbar.pack(fill=tk.X)
        tk.Label(toolbar, text="默认全不选；选中后卡片会高亮。", bg="#FFFFFF", fg="#6B7280", font=self._font(11)).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="全选", command=lambda: self._set_all_rankings(True), style="Ghost.TButton").pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(toolbar, text="清空", command=lambda: self._set_all_rankings(False), style="Ghost.TButton").pack(side=tk.RIGHT)

        self.ranking_grid = tk.Frame(body, bg="#FFFFFF")
        self.ranking_grid.pack(fill=tk.X, pady=(14, 0))
        self._load_ranking_options()

    def _build_log_section(self, body: tk.Frame) -> None:
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.status = tk.Text(
            body,
            height=9,
            wrap=tk.WORD,
            bg="#101214",
            fg="#F2F4F7",
            insertbackground="#F2F4F7",
            selectbackground="#30343A",
            relief=tk.FLAT,
            borderwidth=0,
            font=self._font(11, family="mono"),
            padx=14,
            pady=12,
        )
        self.status.grid(row=0, column=0, sticky=tk.NSEW)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.font_body = self._font_family("PingFang SC", "Microsoft YaHei UI", "Segoe UI")
        self.font_mono = self._font_family("Menlo", "Consolas", "Courier New")
        self.option_add("*Font", (self.font_body, 12))
        style.configure("Modern.TEntry", fieldbackground="#FFFFFF", bordercolor="#DDE1E8", lightcolor="#DDE1E8", darkcolor="#DDE1E8", padding=(10, 9))
        style.configure("Primary.TButton", background="#101214", foreground="#FFFFFF", borderwidth=0, focusthickness=0, padding=(14, 9), font=(self.font_body, 11, "bold"))
        style.map("Primary.TButton", background=[("active", "#30343A"), ("pressed", "#000000")])
        style.configure("Secondary.TButton", background="#F1F3F6", foreground="#101214", borderwidth=0, focusthickness=0, padding=(14, 9), font=(self.font_body, 11))
        style.map("Secondary.TButton", background=[("active", "#E7EAF0"), ("pressed", "#DDE1E8")])
        style.configure("Ghost.TButton", background="#FFFFFF", foreground="#30343A", borderwidth=0, focusthickness=0, padding=(10, 6), font=(self.font_body, 11))
        style.map("Ghost.TButton", background=[("active", "#F1F3F6")])

    def _section(self, parent: tk.Frame, title: str, subtitle: str) -> tk.Frame:
        outer = tk.Frame(parent, bg="#FFFFFF", highlightthickness=1, highlightbackground="#E5E7EB", highlightcolor="#E5E7EB")
        head = tk.Frame(outer, bg="#FFFFFF")
        head.pack(fill=tk.X, padx=18, pady=(16, 10))
        tk.Label(head, text=title, bg="#FFFFFF", fg="#101214", font=self._font(15, "bold")).pack(anchor=tk.W)
        tk.Label(head, text=subtitle, bg="#FFFFFF", fg="#6B7280", font=self._font(11)).pack(anchor=tk.W, pady=(4, 0))
        body = tk.Frame(outer, bg="#FFFFFF")
        body.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 18))
        outer.body = body  # type: ignore[attr-defined]
        self._bind_mousewheel_recursive(outer)
        return outer

    def _file_card(self, parent: tk.Frame, title: str, var: tk.StringVar, icon_name: str, command, column: int) -> dict[str, object]:
        card = tk.Frame(parent, bg="#FAFBFC", highlightthickness=1, highlightbackground="#E5E7EB")
        card.grid(row=0, column=column, sticky=tk.NSEW, padx=(0, 8) if column == 0 else (8, 0))
        card.columnconfigure(0, weight=1)

        clear = tk.Label(card, text="×", bg="#EEF0F4", fg="#5F6673", font=self._font(12, "bold"), width=2)
        clear.place(relx=1, x=-12, y=10, anchor=tk.NE)
        clear.place_forget()

        icon = tk.Label(card, image=self._icon(icon_name), bg="#FAFBFC")
        icon.grid(row=0, column=0, pady=(18, 8))
        name = tk.Label(card, text=title, bg="#FAFBFC", fg="#101214", font=self._font(13, "bold"))
        name.grid(row=1, column=0)
        path_box = tk.Frame(card, width=FILE_PATH_WIDTH, height=FILE_PATH_HEIGHT, bg="#FAFBFC")
        path_box.grid(row=2, column=0, pady=(8, 16))
        path_box.grid_propagate(False)
        path = tk.Label(path_box, text="点击选择文件", bg="#FAFBFC", fg="#8A929E", font=self._font(10), justify=tk.CENTER, anchor=tk.CENTER)
        path.place(relx=0.5, rely=0.5, anchor=tk.CENTER, width=FILE_PATH_WIDTH, height=FILE_PATH_HEIGHT)

        clear.bind("<Button-1>", lambda event, variable=var, label=path, clear_button=clear: self._clear_file(event, variable, label, clear_button))
        for widget in (card, icon, name, path_box, path):
            widget.bind("<Button-1>", lambda _event: command())
        var.trace_add("write", lambda *_args, label=path, variable=var, clear_button=clear: self._update_file_label(label, variable, clear_button))
        self._bind_mousewheel_recursive(card)
        return {"frame": card, "path": path, "clear": clear, "var": var}

    def _action_button(self, text: str, icon_name: str, command, primary: bool) -> None:
        if self.action_button_frame is None:
            return
        style = "Primary.TButton" if primary else "Secondary.TButton"
        label = f"{self._icon_text(icon_name)}  {text}"
        button = ttk.Button(self.action_button_frame, text=label, command=command, style=style)
        button.pack(side=tk.LEFT, padx=(0, 10), pady=(0, 8))

    def _ranking_tile(self, ranking, var: tk.BooleanVar) -> dict[str, object]:
        frame = tk.Frame(self.ranking_grid, bg="#FAFBFC", highlightthickness=1, highlightbackground="#E5E7EB")  # type: ignore[arg-type]
        title = ranking.name
        meta = f"{ranking.scope} / {ranking.dimension} / {ranking.metric}"
        if ranking.filter_field and ranking.filter_value:
            meta += f" · {ranking.filter_field}={ranking.filter_value}"
        mark = tk.Label(frame, text="", width=2, bg="#FAFBFC", fg="#101214", font=self._font(12, "bold"))
        mark.grid(row=0, column=0, rowspan=2, sticky=tk.N, padx=(12, 8), pady=12)
        title_label = tk.Label(frame, text=title, bg="#FAFBFC", fg="#101214", font=self._font(12, "bold"), anchor=tk.W)
        title_label.grid(row=0, column=1, sticky=tk.EW, padx=(0, 12), pady=(12, 2))
        meta_label = tk.Label(frame, text=meta, bg="#FAFBFC", fg="#6B7280", font=self._font(10), anchor=tk.W, wraplength=320)
        meta_label.grid(row=1, column=1, sticky=tk.EW, padx=(0, 12), pady=(0, 12))
        frame.columnconfigure(1, weight=1)

        tile = {"frame": frame, "mark": mark, "title": title_label, "meta": meta_label, "var": var}
        for widget in (frame, mark, title_label, meta_label):
            widget.bind("<Button-1>", lambda _event, current=tile: self._toggle_ranking_tile(current))
        self._bind_mousewheel_recursive(frame)
        self._paint_ranking_tile(tile)
        return tile

    def _toggle_ranking_tile(self, tile: dict[str, object]) -> None:
        var = tile["var"]
        if isinstance(var, tk.BooleanVar):
            var.set(not var.get())
        self._paint_ranking_tile(tile)

    def _paint_ranking_tile(self, tile: dict[str, object]) -> None:
        var = tile["var"]
        selected = isinstance(var, tk.BooleanVar) and var.get()
        bg = "#EEF4FF" if selected else "#FAFBFC"
        border = "#7AA7FF" if selected else "#E5E7EB"
        fg = "#101214"
        muted = "#315FAE" if selected else "#6B7280"
        mark_text = "✓" if selected else ""
        for key in ("frame", "mark", "title", "meta"):
            widget = tile[key]
            if isinstance(widget, tk.Widget):
                widget.configure(bg=bg)
        frame = tile["frame"]
        if isinstance(frame, tk.Frame):
            frame.configure(highlightbackground=border, highlightcolor=border)
        mark = tile["mark"]
        if isinstance(mark, tk.Label):
            mark.configure(text=mark_text, fg="#2563EB")
        title = tile["title"]
        meta = tile["meta"]
        if isinstance(title, tk.Label):
            title.configure(fg=fg)
        if isinstance(meta, tk.Label):
            meta.configure(fg=muted)

    def _place_cards(self) -> None:
        if self.content_frame is None or not self.cards:
            return
        width = max(self.winfo_width(), 1)
        two_cols = width >= 1060
        for card in self.cards.values():
            card.grid_forget()
        for col in range(2):
            self.content_frame.columnconfigure(col, weight=1 if two_cols else 0)
        if two_cols:
            self.cards["imports"].grid(row=0, column=0, sticky=tk.NSEW, padx=(22, 8), pady=(0, 14))
            self.cards["actions"].grid(row=0, column=1, sticky=tk.NSEW, padx=(8, 22), pady=(0, 14))
            self.cards["rankings"].grid(row=1, column=0, columnspan=2, sticky=tk.NSEW, padx=22, pady=(0, 14))
            self.cards["log"].grid(row=2, column=0, columnspan=2, sticky=tk.NSEW, padx=22, pady=(0, 22))
        else:
            for idx, name in enumerate(["imports", "actions", "rankings", "log"]):
                self.cards[name].grid(row=idx, column=0, sticky=tk.NSEW, padx=22, pady=(0, 14))
            self.content_frame.columnconfigure(0, weight=1)
        self._layout_file_cards(two_cols)
        self._layout_action_buttons()
        self._layout_ranking_tiles()

    def _layout_file_cards(self, two_cols: bool) -> None:
        cards = [self.file_cards.get("meituan"), self.file_cards.get("douyin")]
        for card in cards:
            if not card:
                continue
            frame = card["frame"]
            if isinstance(frame, tk.Frame):
                frame.grid_forget()
        if not all(cards):
            return
        meituan = cards[0]["frame"]
        douyin = cards[1]["frame"]
        if not isinstance(meituan, tk.Frame) or not isinstance(douyin, tk.Frame):
            return
        parent = meituan.master
        if two_cols:
            parent.columnconfigure(0, weight=1)
            parent.columnconfigure(1, weight=1)
            meituan.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 8))
            douyin.grid(row=0, column=1, sticky=tk.NSEW, padx=(8, 0))
        else:
            parent.columnconfigure(0, weight=1)
            meituan.grid(row=0, column=0, sticky=tk.NSEW, pady=(0, 10))
            douyin.grid(row=1, column=0, sticky=tk.NSEW)

    def _layout_action_buttons(self) -> None:
        if self.action_button_frame is None:
            return
        width = self.action_button_frame.winfo_width() or self.winfo_width()
        vertical = width < 520
        for child in self.action_button_frame.winfo_children():
            child.pack_forget()
            if vertical:
                child.pack(fill=tk.X, pady=(0, 8))
            else:
                child.pack(side=tk.LEFT, padx=(0, 10), pady=(0, 8))

    def _layout_ranking_tiles(self) -> None:
        if self.ranking_grid is None or not self.ranking_tiles:
            return
        width = max(self.ranking_grid.winfo_width(), self.winfo_width() - 80)
        columns = 1 if width < 720 else 2 if width < 1180 else 3
        for idx, tile in enumerate(self.ranking_tiles):
            frame = tile.get("frame")
            if not isinstance(frame, tk.Frame):
                continue
            frame.grid_forget()
            frame.grid(row=idx // columns, column=idx % columns, sticky=tk.NSEW, padx=(0, 10), pady=(0, 10))
        for col in range(columns):
            self.ranking_grid.columnconfigure(col, weight=1)
        if self.content_frame is not None:
            self._bind_mousewheel_recursive(self.content_frame)

    def _on_resize(self, _event: tk.Event) -> None:
        self.after_idle(self._place_cards)

    def _on_mousewheel(self, event: tk.Event) -> None:
        if self.scroll_canvas is None:
            return
        if getattr(event, "num", None) == 4:
            delta = -3
        elif getattr(event, "num", None) == 5:
            delta = 3
        else:
            raw_delta = int(getattr(event, "delta", 0))
            if abs(raw_delta) >= 120:
                delta = -1 * int(raw_delta / 120)
            else:
                delta = -1 if raw_delta > 0 else 1 if raw_delta < 0 else 0
        if delta:
            self.scroll_canvas.yview_scroll(delta, "units")

    def _bind_mousewheel_recursive(self, widget: tk.Widget) -> None:
        widget_id = str(widget)
        if widget_id not in self._mousewheel_bound_widgets:
            widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
            widget.bind("<Button-4>", self._on_mousewheel, add="+")
            widget.bind("<Button-5>", self._on_mousewheel, add="+")
            self._mousewheel_bound_widgets.add(widget_id)
        for child in widget.winfo_children():
            self._bind_mousewheel_recursive(child)

    def _load_icons(self) -> None:
        for name in ["app", "file", "meituan", "douyin", "excel", "image", "refresh"]:
            size = APP_ICON_SIZE if name == "app" else ICON_SIZE
            self.icons[name] = self._load_icon(name, size)

    def _load_icon(self, name: str, size: int) -> ImageTk.PhotoImage:
        path = ICON_DIR / f"{name}.png"
        if path.exists():
            image = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
        else:
            image = self._fallback_icon(name, size)
        return ImageTk.PhotoImage(image)

    def _fallback_icon(self, name: str, size: int) -> Image.Image:
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        stroke = max(2, size // 18)
        color = "#101214"
        accent = "#2563EB"
        pad = size // 5
        if name == "app":
            draw.rounded_rectangle((pad, pad, size - pad, size - pad), radius=size // 5, fill="#101214")
            draw.rectangle((size // 3, size // 3, size * 2 // 3, size * 2 // 3), fill="#FFFFFF")
            draw.line((size // 3, size // 2, size * 2 // 3, size // 2), fill=accent, width=stroke)
        elif name in {"file", "meituan", "douyin", "excel"}:
            draw.rounded_rectangle((pad, pad, size - pad, size - pad), radius=size // 9, outline=color, width=stroke)
            draw.line((pad + stroke, size // 3, size - pad - stroke, size // 3), fill=accent if name == "meituan" else color, width=stroke)
            draw.line((pad + stroke, size // 2, size - pad - stroke, size // 2), fill=color, width=stroke)
            draw.line((pad + stroke, size * 2 // 3, size - pad - stroke, size * 2 // 3), fill=color, width=stroke)
        elif name == "image":
            draw.rounded_rectangle((pad, pad, size - pad, size - pad), radius=size // 8, outline=color, width=stroke)
            draw.ellipse((size // 2, size // 3, size // 2 + size // 8, size // 3 + size // 8), fill=accent)
            draw.line((pad + stroke, size * 2 // 3, size // 2, size // 2, size - pad - stroke, size * 2 // 3), fill=color, width=stroke)
        elif name == "refresh":
            draw.arc((pad, pad, size - pad, size - pad), 35, 320, fill=color, width=stroke)
            draw.polygon([(size - pad, size // 2), (size - pad * 2, size // 2), (size - pad, size // 2 + pad)], fill=accent)
        else:
            draw.ellipse((pad, pad, size - pad, size - pad), outline=color, width=stroke)
        return image

    def _apply_app_icon(self) -> None:
        icon = self.icons.get("app")
        if icon:
            self.iconphoto(True, icon)

    def _icon(self, name: str) -> ImageTk.PhotoImage:
        return self.icons.get(name) or self.icons["file"]

    def _icon_text(self, name: str) -> str:
        return {"excel": "□", "image": "◇", "refresh": "↻"}.get(name, "□")

    def _update_file_label(self, label: tk.Label, var: tk.StringVar, clear_button: tk.Label | None = None) -> None:
        value = var.get().strip()
        label.configure(text=self._short_path(value) if value else "点击选择文件", fg="#4B5563" if value else "#8A929E")
        if clear_button is not None:
            if value:
                clear_button.place(relx=1, x=-12, y=10, anchor=tk.NE)
            else:
                clear_button.place_forget()

    def _clear_file(self, event: tk.Event, var: tk.StringVar, label: tk.Label, clear_button: tk.Label) -> None:
        var.set("")
        self.last_result = None
        self.summary_text.set("等待导入")
        self._update_file_label(label, var, clear_button)
        self._log("已清除已选择的报表。")
        return "break"

    def _short_path(self, value: str) -> str:
        path = Path(value)
        try:
            text = str(path.relative_to(ROOT))
        except ValueError:
            text = f"{path.parent.name}/{path.name}" if path.name else value
        return self._ellipsize_middle(text, 34)

    def _ellipsize_middle(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        keep = max_chars - 1
        left = keep // 2
        right = keep - left
        return f"{text[:left]}…{text[-right:]}"

    def _font_family(self, *names: str) -> str:
        available = set(self.tk.call("font", "families"))
        for name in names:
            if name in available:
                return name
        return "TkDefaultFont"

    def _font(self, size: int, weight: str = "normal", family: str = "body") -> tuple[str, int, str]:
        if family == "mono":
            return (getattr(self, "font_mono", "TkFixedFont"), size, weight)
        return (getattr(self, "font_body", "TkDefaultFont"), size, weight)

    def _pick_file(self, title: str) -> str:
        return filedialog.askopenfilename(
            title=title,
            filetypes=[("表格文件", "*.xlsx *.xls *.xlsm *.csv"), ("所有文件", "*.*")],
        )

    def _pick_meituan(self) -> None:
        path = self._pick_file("选择美团报表")
        if path:
            self.meituan_path.set(path)
            self._log(f"已选择美团报表：{path}")

    def _pick_douyin(self) -> None:
        path = self._pick_file("选择抖音报表")
        if path:
            self.douyin_path.set(path)
            self._log(f"已选择抖音报表：{path}")

    def _generate_excel(self) -> None:
        try:
            result = self._process()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_dir = Path(self.output_dir.get())
            output_path = out_dir / f"报表输出_{timestamp}.xlsx"
            write_excel_report(result, output_path, self.template_path.get())
            db_path = archive_result(result, ROOT / "data" / "history.sqlite")
            self.last_result = result
            self.summary_text.set(f"{len(result.combined_detail)} 行已处理")
            self._log(f"Excel 已生成：{output_path}")
            self._log(f"历史数据已归档：{db_path}")
            messagebox.showinfo("完成", f"Excel 报表已生成：\n{output_path}")
        except Exception as exc:
            self._error(exc)

    def _generate_images(self) -> None:
        try:
            result = self.last_result or self._process()
            config = load_config(self.config_path.get())
            selected = [replace(ranking, enabled=True) for ranking, var in self.ranking_vars if var.get()]
            enabled_briefings = [briefing for briefing in config.briefings if briefing.enabled]
            if not selected and not enabled_briefings:
                self._log("请至少选择一个榜单，或在综合简报配置中启用一项。")
                messagebox.showwarning("未选择图片", "请至少选择一个榜单，或在综合简报配置中启用一项。")
                return
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_dir = Path(self.output_dir.get()) / "images" / timestamp
            paths = generate_report_images(result, selected, enabled_briefings, out_dir, self.brand.get())
            self.last_result = result
            if not paths:
                self._log("没有启用的排行榜或综合简报配置，未生成图片。")
                messagebox.showwarning("未生成", "没有启用的排行榜或综合简报配置。")
                return
            self.summary_text.set(f"已生成 {len(paths)} 张图片")
            self._log("报告图已生成：")
            for path in paths:
                self._log(f"  {path}")
            messagebox.showinfo("完成", f"已生成 {len(paths)} 张报告图：\n{out_dir}")
        except Exception as exc:
            self._error(exc)

    def _process(self) -> ProcessResult:
        config = load_config(self.config_path.get())
        report_paths = {
            "meituan": self.meituan_path.get().strip() or None,
            "douyin": self.douyin_path.get().strip() or None,
        }
        result = process_reports(config, report_paths)
        self._log(
            f"处理完成：{len(result.combined_detail)} 行，周期 {result.period_start or '未识别'} 至 {result.period_end or '未识别'}。"
        )
        return result

    def _load_ranking_options(self) -> None:
        if self.ranking_grid is None:
            return
        for child in self.ranking_grid.winfo_children():
            child.destroy()
        self.ranking_vars.clear()
        self.ranking_tiles.clear()
        try:
            config = load_config(self.config_path.get())
        except Exception as exc:
            tk.Label(self.ranking_grid, text=f"榜单配置读取失败：{exc}", bg="#FFFFFF", fg="#6B7280", font=self._font(11)).pack(anchor=tk.W)
            return
        if not config.rankings:
            tk.Label(self.ranking_grid, text="暂无排行榜配置", bg="#FFFFFF", fg="#6B7280", font=self._font(11)).pack(anchor=tk.W)
            return
        for ranking in config.rankings:
            var = tk.BooleanVar(value=False)
            tile = self._ranking_tile(ranking, var)
            self.ranking_vars.append((ranking, var))
            self.ranking_tiles.append(tile)
        self._layout_ranking_tiles()

    def _set_all_rankings(self, value: bool) -> None:
        for _, var in self.ranking_vars:
            var.set(value)
        for tile in self.ranking_tiles:
            self._paint_ranking_tile(tile)

    def _log(self, text: str) -> None:
        self.status.insert(tk.END, text + "\n")
        self.status.see(tk.END)

    def _error(self, exc: Exception) -> None:
        self.summary_text.set("处理失败")
        self._log("处理失败：" + str(exc))
        self._log(traceback.format_exc())
        messagebox.showerror("处理失败", str(exc))


def main() -> None:
    app = ReportApp()
    app.mainloop()
