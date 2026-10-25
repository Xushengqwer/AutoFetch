import os
import sys
import json
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

# Add parent directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from core.collector import BattleCollector
from core.game_window import GameWindow

CONFIG_PATH = os.path.join(current_dir, "config.json")

def load_config():
    default_cfg = {
        "max_count": 10,
        "only_today": True,
        "save_dir": os.path.join(current_dir, "战报导出"),
        "server_url": "",
        "user_token": "",
        "auto_upload": False
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                default_cfg.update(json.load(f))
        except Exception:
            pass
    return default_cfg

def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

class SanguoCollectorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("三国志·战略版 战报自动收集工具 v1.0")
        self.geometry("780x640")
        self.minsize(700, 560)
        self.configure(bg="#1a1c23")

        self.cfg = load_config()
        self.collector = None
        self.collect_thread = None

        self._setup_style()
        self._build_ui()
        self._check_game_status()

    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        
        # Configure colors
        bg_dark = "#1a1c23"
        card_bg = "#252834"
        accent_gold = "#e5a93c"
        text_white = "#e0e2ec"

        style.configure("TFrame", background=bg_dark)
        style.configure("Card.TFrame", background=card_bg, relief="flat")
        style.configure("TLabel", background=card_bg, foreground=text_white, font=("Microsoft YaHei UI", 10))
        style.configure("Header.TLabel", background=bg_dark, foreground=accent_gold, font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("SubHeader.TLabel", background=bg_dark, foreground="#8f94a6", font=("Microsoft YaHei UI", 9))
        
        style.configure("TCheckbutton", background=card_bg, foreground=text_white, font=("Microsoft YaHei UI", 9))
        style.map("TCheckbutton", background=[("active", card_bg)])

    def _build_ui(self):
        # Header
        header_frame = ttk.Frame(self, style="TFrame", padding="20 15 20 10")
        header_frame.pack(fill="x")

        lbl_title = ttk.Label(header_frame, text="三国志·战略版 · 战报自动收集工具", style="Header.TLabel")
        lbl_title.pack(anchor="w")
        lbl_sub = ttk.Label(header_frame, text="自动化翻页 · 详情页全量截取 · 按日期自动归档 · 零封号安全辅助", style="SubHeader.TLabel")
        lbl_sub.pack(anchor="w", pady=(3, 0))

        # Main Container
        container = ttk.Frame(self, style="TFrame", padding="20 5 20 15")
        container.pack(fill="both", expand=True)

        # Card 1: 采集配置
        cfg_card = ttk.Frame(container, style="Card.TFrame", padding="15")
        cfg_card.pack(fill="x", pady=(0, 10))

        # Row 1: 数量与过滤
        row1 = ttk.Frame(cfg_card, style="Card.TFrame")
        row1.pack(fill="x", pady=4)

        ttk.Label(row1, text="采集数量：").pack(side="left")
        self.var_count = tk.StringVar(value=str(self.cfg.get("max_count", 50)))
        count_combo = ttk.Combobox(row1, textvariable=self.var_count, values=["10", "20", "50", "100", "200"], width=6, state="readonly")
        count_combo.pack(side="left", padx=(0, 20))

        self.var_only_today = tk.BooleanVar(value=self.cfg.get("only_today", True))
        chk_today = ttk.Checkbutton(row1, text="仅采集当天战报（遇到历史日期自动停止）", variable=self.var_only_today)
        chk_today.pack(side="left")

        # Row 2: 存储路径
        row2 = ttk.Frame(cfg_card, style="Card.TFrame")
        row2.pack(fill="x", pady=6)

        ttk.Label(row2, text="存储目录：").pack(side="left")
        self.var_save_dir = tk.StringVar(value=self.cfg.get("save_dir", os.path.join(current_dir, "战报导出")))
        ent_dir = tk.Entry(row2, textvariable=self.var_save_dir, bg="#1a1c23", fg="#e0e2ec", insertbackground="white", relief="flat", font=("Microsoft YaHei UI", 9))
        ent_dir.pack(side="left", fill="x", expand=True, padx=(5, 10), ipady=3)

        btn_browse = tk.Button(row2, text="选择...", bg="#373b4e", fg="#ffffff", activebackground="#454a61", activeforeground="#ffffff", relief="flat", font=("Microsoft YaHei UI", 8), command=self._browse_dir)
        btn_browse.pack(side="left", padx=(0, 5))

        btn_open = tk.Button(row2, text="📂 打开文件夹", bg="#373b4e", fg="#e5a93c", activebackground="#454a61", activeforeground="#e5a93c", relief="flat", font=("Microsoft YaHei UI", 8), command=self._open_dir)
        btn_open.pack(side="left")

        # Card 2: Action Buttons
        act_frame = ttk.Frame(container, style="TFrame")
        act_frame.pack(fill="x", pady=(5, 10))

        self.btn_start = tk.Button(
            act_frame, 
            text="▶ 一键开始自动采集战报", 
            bg="#27ae60", 
            fg="#ffffff", 
            activebackground="#2ecc71", 
            activeforeground="#ffffff",
            font=("Microsoft YaHei UI", 12, "bold"),
            relief="flat",
            cursor="hand2",
            padx=20,
            pady=8,
            command=self._start_collect
        )
        self.btn_start.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.btn_stop = tk.Button(
            act_frame, 
            text="⏹ 停止", 
            bg="#c0392b", 
            fg="#ffffff", 
            activebackground="#e74c3c", 
            activeforeground="#ffffff",
            font=("Microsoft YaHei UI", 10, "bold"),
            relief="flat",
            state="disabled",
            padx=15,
            pady=8,
            command=self._stop_collect
        )
        self.btn_stop.pack(side="left")

        # Card 3: 日志面板
        log_card = ttk.Frame(container, style="Card.TFrame", padding="10")
        log_card.pack(fill="both", expand=True)

        log_head = ttk.Frame(log_card, style="Card.TFrame")
        log_head.pack(fill="x", pady=(0, 5))
        ttk.Label(log_head, text="运行实时日志：", font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")

        btn_clear = tk.Button(log_head, text="清空日志", bg="#2b2b36", fg="#8f94a6", relief="flat", font=("Microsoft YaHei UI", 8), command=self._clear_log)
        btn_clear.pack(side="right")

        self.txt_log = ScrolledText(
            log_card, 
            bg="#181a20", 
            fg="#cbd5e1", 
            insertbackground="white", 
            relief="flat",
            font=("Consolas", 9),
            padx=8,
            pady=8
        )
        self.txt_log.pack(fill="both", expand=True)

        # Status Bar
        status_bar = ttk.Frame(self, style="TFrame", padding="10 5")
        status_bar.pack(fill="x", side="bottom")

        self.lbl_game_status = ttk.Label(status_bar, text="🎮 正在检测游戏运行状态...", style="SubHeader.TLabel")
        self.lbl_game_status.pack(side="left")

        self.lbl_run_status = ttk.Label(status_bar, text="就绪", style="SubHeader.TLabel")
        self.lbl_run_status.pack(side="right")

    def _browse_dir(self):
        d = filedialog.askdirectory(initialdir=self.var_save_dir.get())
        if d:
            self.var_save_dir.set(d)
            self._save_current_cfg()

    def _open_dir(self):
        d = self.var_save_dir.get()
        os.makedirs(d, exist_ok=True)
        os.startfile(d)

    def _clear_log(self):
        self.txt_log.delete("1.0", tk.END)

    def log(self, msg):
        def _append():
            self.txt_log.insert(tk.END, msg + "\n")
            self.txt_log.see(tk.END)
        self.after(0, _append)

    def set_status(self, text):
        self.after(0, lambda: self.lbl_run_status.config(text=text))

    def _check_game_status(self):
        gw = GameWindow()
        hwnd = gw.find_window()
        if hwnd:
            self.lbl_game_status.config(text="🎮 已检测到三战 PC 桌面版运行中", foreground="#2ecc71")
        else:
            self.lbl_game_status.config(text="⚠️ 未检测到三战 PC 桌面版，请先启动游戏", foreground="#e74c3c")
        self.after(3000, self._check_game_status)

    def _save_current_cfg(self):
        try:
            self.cfg["max_count"] = int(self.var_count.get())
        except ValueError:
            self.cfg["max_count"] = 10
        self.cfg["only_today"] = self.var_only_today.get()
        self.cfg["save_dir"] = self.var_save_dir.get()
        save_config(self.cfg)

    def _start_collect(self):
        self._save_current_cfg()
        os.makedirs(self.cfg["save_dir"], exist_ok=True)

        self.btn_start.config(state="disabled", bg="#7f8c8d")
        self.btn_stop.config(state="normal", bg="#c0392b")

        self.collector = BattleCollector(
            config=self.cfg,
            log_cb=self.log,
            status_cb=self.set_status
        )

        # Minimize tool window so it doesn't cover game buttons
        self.iconify()

        def run_thread():
            try:
                self.collector.run_collection(
                    max_count=self.cfg["max_count"],
                    save_root=self.cfg["save_dir"],
                    only_today=self.cfg["only_today"]
                )
            except Exception as e:
                self.log(f"❌ 发生异常: {e}")
            finally:
                self.after(0, self._on_finish)

        self.collect_thread = threading.Thread(target=run_thread, daemon=True)
        self.collect_thread.start()

    def _stop_collect(self):
        if self.collector:
            self.collector.stop()
            self.log("⏳ 正在请求停止，请稍候...")

    def _on_finish(self):
        self.btn_start.config(state="normal", bg="#27ae60")
        self.btn_stop.config(state="disabled", bg="#7f8c8d")
        self.set_status("就绪")
        # Restore tool window so user sees completed logs
        self.deiconify()
        self.lift()
        # Friendly completion notification: ONLY show if at least 1 report was actually saved!
        if self.collector and not self.collector.stop_requested and getattr(self.collector, "collected_count", 0) > 0:
            save_d = self.var_save_dir.get()
            cnt = self.collector.collected_count
            if messagebox.askyesno("采集完成", f"🎉 今日战报采集完成（共成功保存 {cnt} 份）！\n\n是否立即打开存放文件夹？\n路径：{save_d}"):
                os.startfile(save_d)

def check_and_elevate():
    """Ensure the tool runs as Administrator to match the game's elevation level."""
    try:
        import ctypes
        if not ctypes.windll.shell32.IsUserAnAdmin():
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, f'"{os.path.abspath(__file__)}"', None, 1
            )
            if ret > 32:
                sys.exit(0)
    except Exception:
        pass

if __name__ == "__main__":
    check_and_elevate()
    app = SanguoCollectorApp()
    app.mainloop()
