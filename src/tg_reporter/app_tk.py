from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import load_config
from .excel_report import write_excel_report
from .history import archive_result
from .images import generate_report_images
from .paths import app_root
from .processor import ProcessResult, process_reports


ROOT = app_root()


class ReportApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("团购运营报表工具")
        self.geometry("1120x780")
        self.minsize(920, 640)
        self.resizable(True, True)
        self.configure(bg="#F6F7FB")

        self.config_path = tk.StringVar(value=str(ROOT / "配置表" / "报表工具配置模板.xlsx"))
        self.template_path = tk.StringVar(value=str(ROOT / "配置表" / "输出报表模板.xlsx"))
        self.output_dir = tk.StringVar(value=str(ROOT / "outputs"))
        self.meituan_path = tk.StringVar(value="")
        self.douyin_path = tk.StringVar(value="")
        self.brand = tk.StringVar(value="团购运营")
        self.summary_text = tk.StringVar(value="等待导入报表")

        self.last_result: ProcessResult | None = None
        self.ranking_vars: list[tuple[object, tk.BooleanVar]] = []
        self.ranking_widgets: list[ttk.Checkbutton] = []
        self.path_labels: list[ttk.Label] = []
        self.cards: dict[str, ttk.Frame] = {}
        self.content_frame: ttk.Frame | None = None
        self.ranking_frame: ttk.Frame | None = None
        self.scroll_canvas: tk.Canvas | None = None

        self._configure_styles()
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        shell = ttk.Frame(self, padding=18, style="App.TFrame")
        shell.grid(row=0, column=0, sticky=tk.NSEW)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(1, weight=1)

        self._build_header(shell)
        self._build_scroll_area(shell)
        self._build_cards()
        self.bind("<Configure>", self._on_resize)
        self._log("请选择美团或抖音报表；配置表、输出模板和输出目录使用固定目录结构。")

    def _build_header(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent, padding=(26, 22), style="Header.TFrame")
        header.grid(row=0, column=0, sticky=tk.EW, pady=(0, 16))
        header.columnconfigure(0, weight=1)
        header.columnconfigure(1, weight=0)

        title_box = ttk.Frame(header, style="Header.TFrame")
        title_box.grid(row=0, column=0, sticky=tk.EW)
        ttk.Label(title_box, text="团购运营报表控制台", style="HeaderTitle.TLabel").pack(anchor=tk.W)
        ttk.Label(
            title_box,
            text="导入平台报表，生成业务汇总、综合简报与排行榜图片",
            style="HeaderSub.TLabel",
        ).pack(anchor=tk.W, pady=(8, 0))

        state_box = ttk.Frame(header, padding=(18, 12), style="HeaderMetric.TFrame")
        state_box.grid(row=0, column=1, sticky=tk.E, padx=(18, 0))
        ttk.Label(state_box, text="当前状态", style="HeaderMetricLabel.TLabel").pack(anchor=tk.W)
        ttk.Label(state_box, textvariable=self.summary_text, style="HeaderMetricValue.TLabel").pack(anchor=tk.W, pady=(4, 0))

    def _build_scroll_area(self, parent: ttk.Frame) -> None:
        holder = ttk.Frame(parent, style="App.TFrame")
        holder.grid(row=1, column=0, sticky=tk.NSEW)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)

        canvas = tk.Canvas(holder, bg="#F6F7FB", highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(holder, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky=tk.NSEW)
        scrollbar.grid(row=0, column=1, sticky=tk.NS)
        self.scroll_canvas = canvas

        content = ttk.Frame(canvas, style="App.TFrame")
        window_id = canvas.create_window((0, 0), window=content, anchor=tk.NW)
        content.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window_id, width=event.width))
        canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        canvas.bind_all("<Button-4>", self._on_mousewheel)
        canvas.bind_all("<Button-5>", self._on_mousewheel)
        self.content_frame = content

    def _build_cards(self) -> None:
        if self.content_frame is None:
            return
        imports = self._card(self.content_frame, "平台报表", "选择一个或两个平台数据源")
        actions = self._card(self.content_frame, "生成操作", "先生成 Excel，再按需生成报告图")
        rankings = self._card(self.content_frame, "报告图榜单", "排行榜手动勾选，综合简报按配置自动生成")
        log = self._card(self.content_frame, "运行日志", "处理过程和输出路径会显示在这里")
        self.cards = {"imports": imports, "actions": actions, "rankings": rankings, "log": log}

        self._build_import_card(imports.body)
        self._build_action_card(actions.body)
        self._build_ranking_card(rankings.body)
        self._build_log_card(log.body)
        self._place_cards()

    def _build_import_card(self, body: ttk.Frame) -> None:
        body.columnconfigure(1, weight=1)
        ttk.Button(body, text="选择美团报表", command=self._pick_meituan, style="Secondary.TButton").grid(row=0, column=0, sticky=tk.W, pady=(0, 12), padx=(0, 12))
        self._path_value(body, self.meituan_path, 0)
        ttk.Button(body, text="选择抖音报表", command=self._pick_douyin, style="Secondary.TButton").grid(row=1, column=0, sticky=tk.W, pady=(0, 2), padx=(0, 12))
        self._path_value(body, self.douyin_path, 1)

    def _build_action_card(self, body: ttk.Frame) -> None:
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="报告图品牌名", style="Muted.TLabel").grid(row=0, column=0, sticky=tk.W, padx=(0, 12))
        ttk.Entry(body, textvariable=self.brand, style="Modern.TEntry").grid(row=0, column=1, sticky=tk.EW, padx=(0, 18))
        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.grid(row=0, column=2, sticky=tk.E)
        ttk.Button(buttons, text="生成 Excel 报表", command=self._generate_excel, style="Accent.TButton").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text="生成报告图", command=self._generate_images, style="Accent.TButton").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(buttons, text="刷新榜单", command=self._load_ranking_options, style="Secondary.TButton").pack(side=tk.LEFT)

    def _build_ranking_card(self, body: ttk.Frame) -> None:
        toolbar = ttk.Frame(body, style="Card.TFrame")
        toolbar.pack(fill=tk.X)
        ttk.Label(toolbar, text="勾选要导出的排行榜。综合简报使用配置表里的启用项。", style="Muted.TLabel").pack(side=tk.LEFT)
        ttk.Button(toolbar, text="全选", command=lambda: self._set_all_rankings(True), style="Tiny.TButton").pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(toolbar, text="清空", command=lambda: self._set_all_rankings(False), style="Tiny.TButton").pack(side=tk.RIGHT)
        self.ranking_frame = ttk.Frame(body, style="Card.TFrame")
        self.ranking_frame.pack(fill=tk.X, pady=(12, 0))
        self._load_ranking_options()

    def _build_log_card(self, body: ttk.Frame) -> None:
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.status = tk.Text(
            body,
            height=9,
            wrap=tk.WORD,
            bg="#0F172A",
            fg="#E5E7EB",
            insertbackground="#E5E7EB",
            selectbackground="#334155",
            relief=tk.FLAT,
            borderwidth=0,
            font=("Menlo", 12),
            padx=16,
            pady=14,
        )
        self.status.grid(row=0, column=0, sticky=tk.NSEW)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        font_body = self._font_family("PingFang SC", "Microsoft YaHei UI", "Segoe UI")
        font_mono = self._font_family("Menlo", "Consolas", "Courier New")
        self.option_add("*Font", (font_body, 12))

        style.configure("App.TFrame", background="#F6F7FB")
        style.configure("Header.TFrame", background="#FFFFFF")
        style.configure("HeaderMetric.TFrame", background="#EFF6FF")
        style.configure("Card.TFrame", background="#FFFFFF")
        style.configure("HeaderTitle.TLabel", background="#FFFFFF", foreground="#111827", font=(font_body, 25, "bold"))
        style.configure("HeaderSub.TLabel", background="#FFFFFF", foreground="#6B7280", font=(font_body, 13))
        style.configure("HeaderMetricLabel.TLabel", background="#EFF6FF", foreground="#64748B", font=(font_body, 11))
        style.configure("HeaderMetricValue.TLabel", background="#EFF6FF", foreground="#2563EB", font=(font_body, 13, "bold"))
        style.configure("CardTitle.TLabel", background="#FFFFFF", foreground="#111827", font=(font_body, 15, "bold"))
        style.configure("CardSub.TLabel", background="#FFFFFF", foreground="#6B7280", font=(font_body, 11))
        style.configure("Muted.TLabel", background="#FFFFFF", foreground="#6B7280", font=(font_body, 12))
        style.configure("Path.TLabel", background="#F8FAFC", foreground="#1F2937", font=(font_mono, 11), padding=(10, 8))
        style.configure("Modern.TEntry", fieldbackground="#F8FAFC", bordercolor="#D1D5DB", lightcolor="#D1D5DB", darkcolor="#D1D5DB", padding=(10, 8))
        style.configure("Accent.TButton", background="#2563EB", foreground="#FFFFFF", borderwidth=0, focusthickness=0, padding=(15, 9), font=(font_body, 12, "bold"))
        style.map("Accent.TButton", background=[("active", "#1D4ED8"), ("pressed", "#1E40AF")])
        style.configure("Secondary.TButton", background="#F3F4F6", foreground="#111827", borderwidth=0, focusthickness=0, padding=(13, 8), font=(font_body, 12))
        style.map("Secondary.TButton", background=[("active", "#E5E7EB"), ("pressed", "#D1D5DB")])
        style.configure("Tiny.TButton", background="#F3F4F6", foreground="#374151", borderwidth=0, focusthickness=0, padding=(10, 5), font=(font_body, 11))
        style.map("Tiny.TButton", background=[("active", "#E5E7EB")])
        style.configure("TCheckbutton", background="#FFFFFF", foreground="#374151", font=(font_body, 11), padding=(2, 4))

    def _card(self, parent: ttk.Frame, title: str, subtitle: str) -> ttk.Frame:
        outer = ttk.Frame(parent, padding=18, style="Card.TFrame")
        head = ttk.Frame(outer, style="Card.TFrame")
        head.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(head, text=title, style="CardTitle.TLabel").pack(anchor=tk.W)
        ttk.Label(head, text=subtitle, style="CardSub.TLabel").pack(anchor=tk.W, pady=(4, 0))
        body = ttk.Frame(outer, style="Card.TFrame")
        body.pack(fill=tk.BOTH, expand=True)
        outer.body = body  # type: ignore[attr-defined]
        return outer

    def _path_value(self, parent: ttk.Frame, var: tk.StringVar, row: int) -> None:
        label = ttk.Label(parent, textvariable=var, style="Path.TLabel", anchor=tk.W)
        label.grid(row=row, column=1, sticky=tk.EW, pady=6)
        self.path_labels.append(label)

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
            self.cards["imports"].grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 8), pady=(0, 14))
            self.cards["actions"].grid(row=0, column=1, sticky=tk.NSEW, padx=(8, 0), pady=(0, 14))
            self.cards["rankings"].grid(row=1, column=0, columnspan=2, sticky=tk.NSEW, pady=(0, 14))
            self.cards["log"].grid(row=2, column=0, columnspan=2, sticky=tk.NSEW, pady=(0, 4))
        else:
            for idx, name in enumerate(["imports", "actions", "rankings", "log"]):
                self.cards[name].grid(row=idx, column=0, sticky=tk.NSEW, pady=(0, 14))
            self.content_frame.columnconfigure(0, weight=1)
        self._layout_ranking_widgets()
        self._refresh_wraplengths()

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
            delta = -1 * int(getattr(event, "delta", 0) / 120)
        if delta:
            self.scroll_canvas.yview_scroll(delta, "units")

    def _refresh_wraplengths(self) -> None:
        width = max(self.winfo_width() - 260, 360)
        for label in self.path_labels:
            label.configure(wraplength=width)

    def _font_family(self, *names: str) -> str:
        available = set(self.tk.call("font", "families"))
        for name in names:
            if name in available:
                return name
        return "TkDefaultFont"

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
                self._log("请至少勾选一个榜单，或在综合简报配置中启用一项。")
                messagebox.showwarning("未选择图片", "请至少勾选一个榜单，或在综合简报配置中启用一项。")
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
        config_path = self.config_path.get().strip()
        if not config_path:
            raise ValueError("请先选择配置表。")
        config = load_config(config_path)
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
        if self.ranking_frame is None:
            return
        for child in self.ranking_frame.winfo_children():
            child.destroy()
        self.ranking_vars.clear()
        self.ranking_widgets.clear()
        try:
            config = load_config(self.config_path.get())
        except Exception as exc:
            ttk.Label(self.ranking_frame, text=f"榜单配置读取失败：{exc}", style="Muted.TLabel").pack(anchor=tk.W)
            return
        if not config.rankings:
            ttk.Label(self.ranking_frame, text="暂无排行榜配置", style="Muted.TLabel").pack(anchor=tk.W)
            return
        for ranking in config.rankings:
            var = tk.BooleanVar(value=ranking.enabled)
            label = ranking.name
            if ranking.filter_field and ranking.filter_value:
                label += f"（{ranking.filter_field}={ranking.filter_value}）"
            label += f" | {ranking.scope} / {ranking.dimension} / {ranking.metric}"
            check = ttk.Checkbutton(self.ranking_frame, text=label, variable=var)
            self.ranking_vars.append((ranking, var))
            self.ranking_widgets.append(check)
        self._layout_ranking_widgets()

    def _layout_ranking_widgets(self) -> None:
        if self.ranking_frame is None or not self.ranking_widgets:
            return
        width = max(self.ranking_frame.winfo_width(), self.winfo_width() - 80)
        columns = 1 if width < 720 else 2 if width < 1180 else 3
        for idx, check in enumerate(self.ranking_widgets):
            check.grid_forget()
            check.grid(row=idx // columns, column=idx % columns, sticky=tk.W, padx=(0, 18), pady=4)
        for col in range(columns):
            self.ranking_frame.columnconfigure(col, weight=1)

    def _set_all_rankings(self, value: bool) -> None:
        for _, var in self.ranking_vars:
            var.set(value)

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
