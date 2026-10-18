"""
BetterGI 原神挂机锁频省电控制工具 (极限放宽下限版 + 实时功耗图表记录 + 4K/2K 高清大字号自适应 + 退出恢复 + 细致日志导出与历史复看)
适用: Windows 10 / Intel i5-10400 / NVIDIA RTX 2080 Ti
"""

import os
import sys
import subprocess
import ctypes
from ctypes import wintypes
import csv
import atexit
import tkinter as tk
from tkinter import ttk, font, messagebox, filedialog
import threading
import time
from datetime import datetime
from collections import deque
import psutil
import json

# 系统托盘与图标支持 (可选; 缺失时自动尝试安装, 仍失败则仅禁用托盘, 不影响档位显示)
try:
    from pystray import Icon as _TrayIcon, Menu as _TrayMenu, MenuItem as _TrayMenuItem
    from PIL import Image as _PILImage, ImageDraw as _PILDraw, ImageFont as _PILFont
    _TRAY_AVAILABLE = True
except Exception:
    _TRAY_AVAILABLE = False

# Matplotlib 图表嵌入支持
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.dates as mdates

# 设置中文字体与清晰度
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'sans-serif']
plt.rcParams['axes.unicode_minus'] = False

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def run_as_admin():
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, " ".join(f'"{arg}"' for arg in sys.argv), None, 1
    )
    sys.exit()

class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD)]

def get_system_times():
    idle = FILETIME()
    kernel = FILETIME()
    user = FILETIME()
    success = ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    if not success:
        return 0, 0, 0
    to_int = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime
    return to_int(idle), to_int(kernel), to_int(user)

class PowerSaverApp:
    def __init__(self, root):
        self.root = root
        self.root.title("BetterGI 挂机锁频节能工具 - 功耗监控与曲线记录版")

        # 调优全局缩放倍率，彻底解决高分屏字小问题 (将 Tk 内部默认比例放大 1.85 倍)
        self.apply_ui_scaling()

        # 针对 1080P/2K/4K 屏幕优化初始尺寸 (高 780 保持图表与控制面板宽裕)
        self.root.geometry("900x780")
        self.root.minsize(760, 660)
        self.root.resizable(True, True)

        # 窗口状态持久化: 记住上次窗口大小与最大化状态
        self._win_config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "window_state.json")
        self._load_window_state()

        # 默认以最大化状态打开
        self.root.after(0, lambda: self.root.state("zoomed"))

        self.current_cpu_throttle = 100
        self.last_idle, self.last_kernel, self.last_user = get_system_times()
        self.current_gpu_watts = 0.0
        self.current_cpu_watts = 0.0
        self.current_gpu_util = 0.0
        self.current_gpu_temp = 0.0
        self.current_gpu_mem_used = 0.0
        self.current_gpu_mem_total = 11.0
        self.current_ram_used = 0.0
        self.current_ram_total = 32.0
        self.current_ram_pct = 0.0
        self.top3_processes_str = "统计中..."

        # 退出时是否恢复硬件全速
        self.restore_on_exit = tk.BooleanVar(value=True)

        # 当前档位状态: 'video' | 'work' | 'game' | 'custom' | 'none'
        self.current_mode = "none"
        self.current_mode_var = tk.StringVar(value="未设置档位")
        # 档位 -> (显示名, 主色, 托盘图标RGB, 图标字母)
        self.MODE_TABLE = {
            "video":  ("🎬 极致省电看视频", "#2e7d32", (46, 125, 50),  "E"),
            "work":   ("💻 多网页/IDE办公",  "#0277bd", (2, 119, 189),  "W"),
            "game":   ("🔥 火力全开玩游戏",  "#c62828", (198, 40, 40),  "G"),
            "custom": ("⚙ 自定义参数",      "#6a1b9a", (106, 27, 154), "C"),
            "none":   ("未设置档位",        "#555555", (96, 96, 96),   "·"),
        }
        self.tray_icon = None
        self.tray_enabled = False

        # 细致 CSV 数据记录配置
        self.log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        os.makedirs(self.log_dir, exist_ok=True)
        self.today_str = datetime.now().strftime("%Y-%m-%d")
        self.csv_file_path = os.path.join(self.log_dir, f"power_log_{self.today_str}.csv")
        self.init_csv_file()

        # 数据记录：最多保留 600 个点 (约 15~20 分钟实时曲线)
        self.max_data_points = 600
        self.history_lock = threading.Lock()
        self.time_history = deque(maxlen=self.max_data_points)
        self.gpu_history = deque(maxlen=self.max_data_points)
        self.cpu_history = deque(maxlen=self.max_data_points)
        self.total_history = deque(maxlen=self.max_data_points)

        # 切换设置事件标记: list of dict {'time': dt, 'label': str, 'desc': str}
        self.switch_events = deque(maxlen=100)

        # CPU 采样基准字典: {pid: (name, cpu_times)}
        self.prev_proc_cpu = {}

        self.style = ttk.Style()
        self.style.theme_use('vista')

        # 针对 ttk 样式做字号强化与舒适行距
        self.style.configure("TLabelframe.Label", font=("Microsoft YaHei", 10, "bold"), foreground="#1a237e")
        self.style.configure("TLabel", font=("Microsoft YaHei", 9))
        self.style.configure("TButton", font=("Microsoft YaHei", 9, "bold"))

        self.create_widgets()
        self.init_chart()

        self.is_monitoring = True
        self.monitor_thread = threading.Thread(target=self.update_monitor_loop, daemon=True)
        self.monitor_thread.start()

        # 注册退出时的恢复处理 (无论点击叉号还是异常退出)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        atexit.register(self.safe_exit_cleanup)

        # 启动定时刷新图表 (UI 主线程中更新以保稳定)
        self.root.after(1500, self.refresh_chart_loop)

        # 初始化系统托盘 (缺失依赖则自动尝试安装, 仍失败则静默禁用, 档位横幅不受影响)
        self.init_tray()

    def init_csv_file(self):
        if not os.path.exists(self.csv_file_path):
            try:
                with open(self.csv_file_path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        "时间", "总核心功耗(W)", "GPU功耗(W)", "CPU功耗(W)",
                        "CPU占用率(%)", "GPU占用率(%)", "GPU温度(°C)",
                        "内存占用(GB)", "内存总容量(GB)", "内存占用率(%)",
                        "显存占用(MB)", "显存总容量(MB)",
                        "最高能耗进程TOP1", "最高能耗进程TOP2", "最高能耗进程TOP3", "当前模式配置"
                    ])
            except Exception as e:
                print(f"初始化日志文件失败: {e}")

    def apply_ui_scaling(self):
        """强制提升 Tk 缩放比，彻底解决 4K/2K 显示器下控件与字体细小的问题"""
        try:
            curr_s = float(self.root.tk.call('tk', 'scaling'))
            target_s = max(curr_s * 1.85, 2.45)
            self.root.tk.call('tk', 'scaling', target_s)
        except Exception:
            pass

    def _load_window_state(self):
        """从配置文件恢复上次的窗口大小与最大化状态"""
        try:
            if os.path.exists(self._win_config_file):
                with open(self._win_config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("geometry"):
                    self.root.geometry(data["geometry"])
                if data.get("state") == "zoomed":
                    # 延迟到事件循环中设置最大化, 确保窗口已完成初始映射
                    self.root.after(150, lambda: self.root.state("zoomed"))
        except Exception:
            pass

    def _save_window_state(self):
        """保存当前窗口大小与最大化状态到配置文件"""
        try:
            state = self.root.winfo_state()
            # 窗口最小化到托盘时状态为 withdrawn, 恢复时按 normal 处理
            if state == "withdrawn":
                state = "normal"
            data = {
                "geometry": self.root.geometry(),
                "state": state
            }
            with open(self._win_config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def on_close(self):
        # 点击窗口叉号 -> 最小化到托盘 (若托盘可用); 否则直接退出
        if self.tray_enabled and self.tray_icon is not None:
            try:
                self.root.withdraw()
                self.log("🖥 已最小化到系统托盘; 右键托盘图标可切换档位/显示窗口/退出")
            except Exception:
                self._real_quit()
        else:
            self._real_quit()

    def _real_quit(self):
        """真正退出: 停止托盘、按需恢复硬件、销毁窗口"""
        self.is_monitoring = False
        self._save_window_state()
        if self.restore_on_exit.get():
            try:
                self.reset_gpu_limit()
                self.apply_cpu_limit(100)
            except Exception:
                pass
        try:
            if self.tray_icon is not None:
                self.tray_icon.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def safe_exit_cleanup(self):
        if hasattr(self, 'restore_on_exit') and self.restore_on_exit.get():
            try:
                subprocess.run(["nvidia-smi", "-rgc"], check=False, creationflags=0x08000000)
                subprocess.run(["nvidia-smi", "-pl", "250"], check=False, creationflags=0x08000000)
                subprocess.run("powercfg -setacvalueindex SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMAX 100", shell=True, check=False, creationflags=0x08000000)
                subprocess.run("powercfg -setactive SCHEME_CURRENT", shell=True, check=False, creationflags=0x08000000)
            except Exception:
                pass

    # ---------- 档位显示与系统托盘 ----------
    def set_current_mode(self, key):
        """统一更新顶部档位横幅与托盘图标/提示"""
        if key not in self.MODE_TABLE:
            key = "none"
        self.current_mode = key
        label, color, _rgb, _letter = self.MODE_TABLE[key]
        self.current_mode_var.set(label)
        try:
            self.mode_banner.config(bg=color)
        except Exception:
            pass
        if self.tray_icon is not None:
            try:
                self.tray_icon.icon = self.make_tray_icon(key)
                self.tray_icon.title = f"BetterGI省电工具 | 当前: {label}"
                self.tray_icon.update_menu()
            except Exception:
                pass

    def make_tray_icon(self, key):
        """根据档位生成托盘图标 (带颜色圆角底与字母标识)"""
        _label, _color, rgb, letter = self.MODE_TABLE.get(key, self.MODE_TABLE["none"])
        img = _PILImage.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = _PILDraw.Draw(img)
        try:
            d.rounded_rectangle([6, 6, 58, 58], radius=14, fill=rgb + (255,))
        except Exception:
            d.rectangle([6, 6, 58, 58], fill=rgb + (255,))
        font = None
        for fname in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "segoeui.ttf", "arial.ttf"):
            try:
                font = _PILFont.truetype(fname, 40)
                break
            except Exception:
                continue
        if font is None:
            font = _PILFont.load_default()
        try:
            bbox = d.textbbox((0, 0), letter, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            d.text((32 - tw / 2, 32 - th / 2 - bbox[1]), letter, fill="white", font=font)
        except Exception:
            d.text((22, 16), letter, fill="white", font=font)
        return img

    def init_tray(self):
        """创建系统托盘; 依赖缺失时自动尝试安装, 仍失败则禁用托盘 (档位横幅不受影响)"""
        global _TRAY_AVAILABLE
        if not _TRAY_AVAILABLE:
            # 后台安装托盘依赖, 不阻塞主窗口启动; 装好后回主线程创建托盘
            self.log("🛠 后台安装托盘依赖 pystray + Pillow 中, 窗口可正常使用...")
            threading.Thread(target=self._install_tray_deps, daemon=True).start()
            return
        self._build_tray()

    def _install_tray_deps(self):
        """后台线程: 安装 pystray/Pillow 并绑定全局名, 成功后回主线程建托盘"""
        global _TRAY_AVAILABLE, _TrayIcon, _TrayMenu, _TrayMenuItem, _PILImage, _PILDraw, _PILFont
        try:
            import subprocess as _sp, sys as _sys
            _sp.run([_sys.executable, "-m", "pip", "install", "pystray", "Pillow"],
                    check=False, creationflags=0x08000000)
            from pystray import Icon as _TrayIcon2, Menu as _TrayMenu2, MenuItem as _TrayMenuItem2
            from PIL import Image as _PILImage2, ImageDraw as _PILDraw2, ImageFont as _PILFont2
            _TrayIcon = _TrayIcon2
            _TrayMenu = _TrayMenu2
            _TrayMenuItem = _TrayMenuItem2
            _PILImage = _PILImage2
            _PILDraw = _PILDraw2
            _PILFont = _PILFont2
            _TRAY_AVAILABLE = True
            self.root.after(0, self._build_tray)
        except Exception as e:
            msg = f"⚠ 托盘依赖安装失败, 仅启用档位显示: {e}"
            self.root.after(0, lambda m=msg: self.log(m))

    def _build_tray(self):
        """创建系统托盘图标与菜单 (主线程调用)"""
        global _TRAY_AVAILABLE
        if not _TRAY_AVAILABLE:
            return
        try:
            IconCls = _TrayIcon
            MenuCls = _TrayMenu
            ItemCls = _TrayMenuItem

            def _show(icon=None, item=None):
                self.root.after(0, self._tray_restore)

            def _quit(icon=None, item=None):
                self.root.after(0, self._real_quit)

            def _preset(key):
                def _cb(icon=None, item=None):
                    self.root.after(0, lambda k=key: self._tray_apply_preset(k))
                return _cb

            menu = MenuCls(
                ItemCls("显示主窗口", _show, default=True),
                ItemCls("极致省电看视频", _preset("video")),
                ItemCls("多网页/IDE办公", _preset("work")),
                ItemCls("火力全开玩游戏", _preset("game")),
                ItemCls("退出 (恢复全速)", _quit),
            )
            self.tray_icon = IconCls("EcoGenshinTray", self.make_tray_icon(self.current_mode),
                                     "BetterGI省电工具", menu)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
            self.tray_enabled = True
            self.set_current_mode(self.current_mode)
            self.log("🖥 系统托盘已就绪: 叉号即最小化, 右键托盘图标可切档位/退出")
        except Exception as e:
            self.log(f"⚠ 系统托盘初始化失败: {e}")
            self.tray_enabled = False

    def _tray_restore(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def _tray_apply_preset(self, key):
        try:
            {"video": self.apply_video_preset,
             "work": self.apply_work_preset,
             "game": self.apply_game_preset}[key]()
        except Exception as e:
            self.log(f"托盘切换档位失败: {e}")

    def create_widgets(self):
        # 0. 顶部当前档位醒目横幅 (随档位变色, 一眼可知当前处于哪一档)
        self.mode_banner = tk.Label(
            self.root, textvariable=self.current_mode_var,
            bg=self.MODE_TABLE[self.current_mode][1], fg="white",
            font=("Microsoft YaHei", 18, "bold"), height=2, relief="flat"
        )
        self.mode_banner.pack(fill="x", padx=10, pady=(6, 2))
        self.mode_banner.bind("<Button-1>", lambda e: self.log("当前档位: " + self.current_mode_var.get()))

        # 1. 监控面板 (字号配合 scaling 呈现出大字效果)
        mon_frame = ttk.LabelFrame(self.root, text="实时状态与全维度监控 (CPU/GPU/内存/最高能耗进程)", padding=(10, 4))
        mon_frame.pack(fill="x", padx=10, pady=3)

        self.gpu_status_var = tk.StringVar(value="GPU: 读取中...")
        self.gpu_power_var = tk.StringVar(value="显卡功耗: 读取中...")
        ttk.Label(mon_frame, textvariable=self.gpu_status_var, font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", pady=1)
        ttk.Label(mon_frame, textvariable=self.gpu_power_var, font=("Microsoft YaHei", 10), foreground="#007acc").pack(anchor="w", pady=1)

        ttk.Separator(mon_frame, orient="horizontal").pack(fill="x", pady=2)

        self.cpu_status_var = tk.StringVar(value="CPU: 正在初始化...")
        self.cpu_power_var = tk.StringVar(value="CPU 实时功耗: 计算中...")
        ttk.Label(mon_frame, textvariable=self.cpu_status_var, font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", pady=1)
        ttk.Label(mon_frame, textvariable=self.cpu_power_var, font=("Microsoft YaHei", 10), foreground="#e65100").pack(anchor="w", pady=1)

        ttk.Separator(mon_frame, orient="horizontal").pack(fill="x", pady=2)

        self.sys_status_var = tk.StringVar(value="内存 & 显存: 读取中...")
        ttk.Label(mon_frame, textvariable=self.sys_status_var, font=("Microsoft YaHei", 9)).pack(anchor="w", pady=1)

        self.top_procs_var = tk.StringVar(value="当时能耗最高的3个进程: 统计中...")
        ttk.Label(mon_frame, textvariable=self.top_procs_var, font=("Microsoft YaHei", 9, "bold"), foreground="#6a1b9a").pack(anchor="w", pady=1)

        ttk.Separator(mon_frame, orient="horizontal").pack(fill="x", pady=2)

        self.total_power_var = tk.StringVar(value="核心总功耗 (CPU+GPU): 读取中...")
        ttk.Label(mon_frame, textvariable=self.total_power_var, font=("Microsoft YaHei", 11, "bold"), foreground="#2e7d32").pack(anchor="w", pady=1)

        # 2. 快捷模式
        preset_frame = ttk.LabelFrame(self.root, text="场景化一键模式预设 (点击即可即时切换)", padding=(10, 4))
        preset_frame.pack(fill="x", padx=10, pady=3)

        btn_box = ttk.Frame(preset_frame)
        btn_box.pack(fill="x", expand=True)

        video_btn = tk.Button(btn_box, text="🎬 极致省电看视频\n(全部拉到物理最低 | GPU 300MHz, CPU 800MHz)", 
                              bg="#e0f2f1", fg="#004d40", font=("Microsoft YaHei", 9, "bold"),
                              relief="groove", height=2, command=self.apply_video_preset)
        video_btn.pack(side="left", fill="x", expand=True, padx=3)

        work_btn = tk.Button(btn_box, text="💻 多网页/IDE编程办公\n(丝滑不卡顿 | GPU 600MHz, CPU ~2.0GHz)", 
                             bg="#e1f5fe", fg="#0277bd", font=("Microsoft YaHei", 9, "bold"),
                             relief="groove", height=2, command=self.apply_work_preset)
        work_btn.pack(side="left", fill="x", expand=True, padx=3)

        game_btn = tk.Button(btn_box, text="🔥 火力全开玩游戏\n(满血全解锁 | GPU 250W+, CPU 100%睿频)", 
                             bg="#ffebee", fg="#c62828", font=("Microsoft YaHei", 9, "bold"),
                              relief="groove", height=2, command=self.apply_game_preset)
        game_btn.pack(side="right", fill="x", expand=True, padx=3)

        # 3. 自定义滑块调节
        custom_frame = ttk.LabelFrame(self.root, text="自定义参数微调 (已开放物理极限下限)", padding=(10, 4))
        custom_frame.pack(fill="x", padx=10, pady=3)

        # 先定义标签对象，确保回调执行时属性已存在
        self.gpu_clock_label = ttk.Label(custom_frame, text="300 MHz (硬件物理最低)", font=("Microsoft YaHei", 9, "bold"))
        self.gpu_pl_label = ttk.Label(custom_frame, text="100 W (VBIOS功耗墙底线)", font=("Microsoft YaHei", 9, "bold"))
        self.cpu_label = ttk.Label(custom_frame, text="20% (~800MHz 硬件触底)", font=("Microsoft YaHei", 9, "bold"))

        # GPU 核心锁频: 下限放宽至 300 MHz
        ttk.Label(custom_frame, text="GPU 核心锁定频率:", font=("Microsoft YaHei", 9)).grid(row=0, column=0, sticky="w", pady=2)
        self.gpu_clock_scale = ttk.Scale(custom_frame, from_=300, to=1800, orient="horizontal", command=self._on_gpu_clock_change)
        self.gpu_clock_scale.set(300)
        self.gpu_clock_scale.grid(row=0, column=1, sticky="ew", padx=8)
        self.gpu_clock_label.grid(row=0, column=2, sticky="w")

        # GPU 功耗上限: 2080Ti VBIOS 固件物理范围 100W ~ 280W
        ttk.Label(custom_frame, text="GPU 功耗墙上限:", font=("Microsoft YaHei", 9)).grid(row=1, column=0, sticky="w", pady=2)
        self.gpu_pl_scale = ttk.Scale(custom_frame, from_=100, to=280, orient="horizontal", command=self._on_gpu_pl_change)
        self.gpu_pl_scale.set(100)
        self.gpu_pl_scale.grid(row=1, column=1, sticky="ew", padx=8)
        self.gpu_pl_label.grid(row=1, column=2, sticky="w")

        # CPU 状态上限: 下限放宽至 20% (硬件触底 800MHz)
        ttk.Label(custom_frame, text="CPU 最大状态上限:", font=("Microsoft YaHei", 9)).grid(row=2, column=0, sticky="w", pady=2)
        self.cpu_scale = ttk.Scale(custom_frame, from_=20, to=100, orient="horizontal", command=self._on_cpu_change)
        self.cpu_scale.set(20)
        self.cpu_scale.grid(row=2, column=1, sticky="ew", padx=8)
        self.cpu_label.grid(row=2, column=2, sticky="w")

        custom_frame.columnconfigure(1, weight=1)

        apply_custom_btn = ttk.Button(custom_frame, text="应用当前自定义参数", command=self.apply_custom)
        apply_custom_btn.grid(row=3, column=0, columnspan=3, pady=3)

        # 4. 实时功耗图表区域
        chart_frame = ttk.LabelFrame(self.root, text="功耗趋势图 (按时间动态记录与配置切换标注)", padding=6)
        chart_frame.pack(fill="both", expand=True, padx=10, pady=3)

        # 按钮控制栏
        chart_toolbar = ttk.Frame(chart_frame)
        chart_toolbar.pack(fill="x", side="top", pady=1)

        # 退出恢复勾选框
        ttk.Checkbutton(chart_toolbar, text="退出软件时自动恢复全速", variable=self.restore_on_exit).pack(side="left", padx=4)

        # 数据管理按钮
        ttk.Button(chart_toolbar, text="清空视图", command=self.clear_chart_history).pack(side="right", padx=2)
        ttk.Button(chart_toolbar, text="导出当前记录CSV", command=self.export_csv).pack(side="right", padx=2)
        ttk.Button(chart_toolbar, text="历史日志复看", command=self.open_history_viewer).pack(side="right", padx=2)

        self.canvas_container = ttk.Frame(chart_frame)
        self.canvas_container.pack(fill="both", expand=True)

        # 5. 底部日志
        self.log_var = tk.StringVar(value="就绪。已放宽下限至硬件物理极值，全维度细致记录中。")
        ttk.Label(self.root, textvariable=self.log_var, foreground="#555555", font=("Microsoft YaHei", 8)).pack(anchor="w", padx=12, pady=2)

    def init_chart(self):
        # 尺寸比例调优为 7.8 x 3.0，DPI 调整为 115 确保大屏下清晰细腻
        self.fig, self.ax = plt.subplots(figsize=(7.8, 3.0), dpi=115)
        self.fig.patch.set_facecolor('#fafafa')
        self.ax.set_facecolor('#ffffff')

        self.ax.grid(True, linestyle="--", alpha=0.5)
        self.ax.set_ylabel("功耗 (W)", fontsize=10, fontweight="bold")
        self.ax.set_xlabel("时间", fontsize=10)
        self.ax.set_title("实时核心功耗曲线与设置切换事件", fontsize=11, fontweight="bold")
        self.ax.tick_params(axis='both', labelsize=9)

        self.fig.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.canvas_container)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def record_switch_event(self, label, desc):
        """记录配置切换事件，用于在图表中标注"""
        now = datetime.now()
        with self.history_lock:
            self.switch_events.append({
                'time': now,
                'label': label,
                'desc': desc
            })

    def clear_chart_history(self):
        with self.history_lock:
            self.time_history.clear()
            self.gpu_history.clear()
            self.cpu_history.clear()
            self.total_history.clear()
            self.switch_events.clear()
        self.log("📊 实时功耗折线图已重置清空")

    def export_csv(self):
        """导出 CSV 日志副本到用户自定义位置"""
        try:
            target = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV 文件", "*.csv")],
                initialfile=f"power_log_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            )
            if target:
                import shutil
                shutil.copy2(self.csv_file_path, target)
                messagebox.showinfo("导出成功", f"日志已成功导出至:\n{target}")
        except Exception as e:
            messagebox.showerror("导出失败", f"导出过程出错: {e}")

    def open_history_viewer(self):
        """打开历史日志文件复看窗口或在文件管理器中浏览"""
        try:
            viewer_win = tk.Toplevel(self.root)
            viewer_win.title("历史能耗日志复看与分析")
            viewer_win.geometry("920x540")

            ctrl_bar = ttk.Frame(viewer_win, padding=6)
            ctrl_bar.pack(fill="x")

            def open_in_explorer():
                os.startfile(self.log_dir)

            ttk.Button(ctrl_bar, text="打开日志目录", command=open_in_explorer).pack(side="left", padx=4)
            ttk.Label(ctrl_bar, text=f"当前日志文件: {os.path.basename(self.csv_file_path)}", font=("Microsoft YaHei", 9)).pack(side="left", padx=10)

            # 表格查看器
            columns = ("时间", "总功耗", "GPU功耗", "CPU功耗", "CPU%", "GPU%", "温度", "内存%", "TOP进程")
            tree = ttk.Treeview(viewer_win, columns=columns, show="headings")
            for col in columns:
                tree.heading(col, text=col)
                tree.column(col, width=95, anchor="center")
            tree.column("时间", width=85)
            tree.column("TOP进程", width=240, anchor="w")

            scrollbar = ttk.Scrollbar(viewer_win, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side="right", fill="y")
            tree.pack(fill="both", expand=True, padx=6, pady=4)

            # 读取最新 200 条记录倒序展示
            if os.path.exists(self.csv_file_path):
                with open(self.csv_file_path, "r", encoding="utf-8-sig") as f:
                    rows = list(csv.reader(f))
                    if len(rows) > 1:
                        data_rows = rows[1:]
                        for r in reversed(data_rows[-200:]):
                            if len(r) >= 15:
                                top_proc_summary = f"{r[12]}" + (f", {r[13]}" if r[13] else "")
                                tree.insert("", "end", values=(r[0], f"{r[1]}W", f"{r[2]}W", f"{r[3]}W", f"{r[4]}%", f"{r[5]}%", f"{r[6]}°C", f"{r[9]}%", top_proc_summary))

        except Exception as e:
            messagebox.showerror("复看失败", f"无法打开日志: {e}")

    def refresh_chart_loop(self):
        if not self.is_monitoring:
            return

        with self.history_lock:
            times = list(self.time_history)
            gpu_powers = list(self.gpu_history)
            cpu_powers = list(self.cpu_history)
            total_powers = list(self.total_history)
            events = list(self.switch_events)

        if len(times) >= 2:
            self.ax.clear()
            self.ax.grid(True, linestyle="--", alpha=0.5)
            self.ax.set_ylabel("功耗 (W)", fontsize=10, fontweight="bold")
            self.ax.set_xlabel("时间", fontsize=10)
            self.ax.set_title("实时核心功耗曲线与设置切换事件", fontsize=11, fontweight="bold")
            self.ax.tick_params(axis='both', labelsize=9)

            # 绘制曲线
            self.ax.plot(times, total_powers, label="核心总功耗 (CPU+GPU)", color="#2e7d32", linewidth=2.0)
            self.ax.plot(times, gpu_powers, label="显卡功耗 (GPU)", color="#007acc", linewidth=1.4, alpha=0.9)
            self.ax.plot(times, cpu_powers, label="CPU 功耗", color="#e65100", linewidth=1.4, alpha=0.9)

            # 标注切换设置的时间点及其配置
            t_min = times[0]
            t_max = times[-1]
            max_y = max(total_powers) if total_powers else 100
            upper_limit = max(max_y * 1.30, 45)

            # 过滤在当前时间窗口内的事件
            valid_events = [ev for ev in events if t_min <= ev['time'] <= t_max]

            for idx, ev in enumerate(valid_events):
                ev_time = ev['time']
                self.ax.axvline(x=ev_time, color="#d32f2f", linestyle=":", linewidth=1.6, alpha=0.85)
                y_pos = upper_limit * (0.60 + (idx % 3) * 0.12)
                text_content = f"{ev['label']}\n{ev['desc']}"
                self.ax.text(
                    ev_time, y_pos, text_content,
                    fontsize=8.5, color="#b71c1c", fontweight="bold",
                    ha='center', va='bottom',
                    bbox=dict(boxstyle='round,pad=0.3', facecolor='#fffde7', edgecolor='#f57f17', alpha=0.95)
                )

            self.ax.set_ylim(bottom=0, top=upper_limit)
            self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
            self.fig.autofmt_xdate(rotation=20, ha='right')

            self.ax.legend(loc="upper left", fontsize=9, framealpha=0.85)
            self.fig.tight_layout()
            try:
                self.canvas.draw_idle()
            except Exception:
                pass

        # 每 2 秒刷新一次图表
        self.root.after(2000, self.refresh_chart_loop)

    def _on_gpu_clock_change(self, val):
        mhz = int(float(val))
        suffix = " (极限最低)" if mhz <= 300 else ""
        if hasattr(self, 'gpu_clock_label'):
            self.gpu_clock_label.config(text=f"{mhz} MHz{suffix}")

    def _on_gpu_pl_change(self, val):
        if hasattr(self, 'gpu_pl_label'):
            self.gpu_pl_label.config(text=f"{int(float(val))} W")

    def _on_cpu_change(self, val):
        percent = int(float(val))
        est_mhz = max(800, int(2900 * (percent / 100)))
        suffix = " (触底 800MHz)" if est_mhz <= 800 else ""
        if hasattr(self, 'cpu_label'):
            self.cpu_label.config(text=f"{percent}% (~{est_mhz}MHz{suffix})")

    def log(self, text):
        self.log_var.set(f"[{time.strftime('%H:%M:%S')}] {text}")

    def apply_gpu_limit(self, clock_mhz, power_w):
        try:
            min_c = 300
            max_c = max(300, clock_mhz)
            subprocess.run(["nvidia-smi", "-lgc", f"{min_c},{max_c}"], check=True, creationflags=0x08000000)
            
            res = subprocess.run(["nvidia-smi", "-pl", str(power_w)], capture_output=True, text=True, creationflags=0x08000000)
            if res.returncode != 0 and "out of range" in res.stderr.lower():
                subprocess.run(["nvidia-smi", "-pl", "100"], check=False, creationflags=0x08000000)
            return True
        except Exception as e:
            self.log(f"GPU设置失败: {e}")
            return False

    def reset_gpu_limit(self):
        try:
            subprocess.run(["nvidia-smi", "-rgc"], check=True, creationflags=0x08000000)
            subprocess.run(["nvidia-smi", "-pl", "250"], check=True, creationflags=0x08000000)
            return True
        except Exception as e:
            self.log(f"GPU恢复失败: {e}")
            return False

    def apply_cpu_limit(self, max_percent):
        try:
            self.current_cpu_throttle = max_percent
            cmd = f"powercfg -setacvalueindex SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMAX {max_percent}"
            subprocess.run(cmd, shell=True, check=True, creationflags=0x08000000)
            subprocess.run("powercfg -setactive SCHEME_CURRENT", shell=True, check=True, creationflags=0x08000000)
            return True
        except Exception as e:
            self.log(f"CPU设置失败: {e}")
            return False

    def apply_video_preset(self):
        """1. 极致省电看视频模式：全部拉到物理最低"""
        self.gpu_clock_scale.set(300)
        self.gpu_pl_scale.set(100)
        self.cpu_scale.set(20)
        self._on_gpu_clock_change(300)
        self._on_gpu_pl_change(100)
        self._on_cpu_change(20)
        
        self.apply_gpu_limit(300, 100)
        self.apply_cpu_limit(20)
        self.record_switch_event("[极致省电看视频]", "全部拉至硬件最低 | GPU 300MHz, 100W | CPU 20% (800MHz)")
        self.set_current_mode("video")
        self.log("🎬 已应用极致省电模式: GPU 300MHz(极限), 功耗100W, CPU 20%(800MHz触底)")

    def apply_work_preset(self):
        """2. 多网页/IDE编程办公模式：兼顾省电与绝对流畅"""
        self.gpu_clock_scale.set(600)
        self.gpu_pl_scale.set(120)
        self.cpu_scale.set(65)
        self._on_gpu_clock_change(600)
        self._on_gpu_pl_change(120)
        self._on_cpu_change(65)
        
        self.apply_gpu_limit(600, 120)
        self.apply_cpu_limit(65)
        self.record_switch_event("[多网页/IDE办公]", "流畅办公省电 | GPU 600MHz, 120W | CPU 65% (~2.0GHz)")
        self.set_current_mode("work")
        self.log("💻 已应用办公编程模式: GPU 600MHz, CPU ~2.0GHz (切网页/写代码丝滑不卡)")

    def apply_game_preset(self):
        """3. 火力全开玩游戏模式：彻底解锁，性能拉满"""
        self.gpu_clock_scale.set(1650)
        self.gpu_pl_scale.set(250)
        self.cpu_scale.set(100)
        self._on_gpu_clock_change(1650)
        self._on_gpu_pl_change(250)
        self._on_cpu_change(100)

        self.reset_gpu_limit()
        self.apply_cpu_limit(100)
        self.record_switch_event("[火力全开玩游戏]", "满血全开 | GPU 解锁/250W+ | CPU 100% 满血睿频")
        self.set_current_mode("game")
        self.log("🔥 已应用游戏火力全开模式: 完全解除锁频与功耗墙，CPU 100% 睿频满载")

    def apply_custom(self):
        clk = int(self.gpu_clock_scale.get())
        pl = int(self.gpu_pl_scale.get())
        cpu_p = int(self.cpu_scale.get())
        self.apply_gpu_limit(clk, pl)
        self.apply_cpu_limit(cpu_p)
        self.record_switch_event("[自定义配置]", f"GPU {clk}MHz, {pl}W | CPU {cpu_p}%")
        self.set_current_mode("custom")
        self.log(f"✅ 自定义配置已应用: GPU {clk}MHz, CPU {cpu_p}%")

    def get_cpu_load(self):
        idle, kernel, user = get_system_times()
        delta_idle = idle - self.last_idle
        delta_kernel = kernel - self.last_kernel
        delta_user = user - self.last_user
        self.last_idle, self.last_kernel, self.last_user = idle, kernel, user
        total = delta_kernel + delta_user
        if total <= 0:
            return 0.0
        load = (total - delta_idle) / total * 100.0
        return max(0.0, min(100.0, load))

    def get_top3_processes(self):
        """高精度计算当时占用 CPU / 能耗最高的前 3 个进程"""
        results = []
        curr_procs = {}
        try:
            for p in psutil.process_iter(["pid", "name"]):
                try:
                    curr_procs[p.pid] = (p.name(), p.cpu_times())
                except Exception:
                    pass

            for pid, (name, t1) in curr_procs.items():
                if pid in self.prev_proc_cpu:
                    _, t0 = self.prev_proc_cpu[pid]
                    if name in ("System Idle Process", "System"):
                        continue
                    delta = (t1.user - t0.user) + (t1.system - t0.system)
                    if delta > 0.005:
                        results.append((delta * 100.0, name))

            self.prev_proc_cpu = curr_procs
            results.sort(reverse=True)
            top3 = results[:3]
            if top3:
                top_strs = [f"{name} ({pct:.1f}%)" for pct, name in top3]
                return top3, ", ".join(top_strs)
        except Exception:
            pass
        return [], "暂无高占用进程"

    def update_monitor_loop(self):
        while self.is_monitoring:
            gpu_desc = ""
            gpu_pwr_desc = ""
            cpu_desc = ""
            cpu_pwr_desc = ""
            total_desc = ""
            sys_desc = ""
            top3_summary = ""
            top3_list = []

            # 1. 采集 GPU 状态
            try:
                cmd = ["nvidia-smi", "--query-gpu=clocks.gr,clocks.mem,power.draw,temperature.gpu,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"]
                out = subprocess.check_output(cmd, creationflags=0x08000000).decode('utf-8').strip()
                gr_clk, mem_clk, pwr, temp, util, vram_used, vram_total = [x.strip() for x in out.split(',')]
                self.current_gpu_watts = float(pwr)
                self.current_gpu_util = float(util)
                self.current_gpu_temp = float(temp)
                self.current_gpu_mem_used = float(vram_used)
                self.current_gpu_mem_total = float(vram_total)

                gpu_desc = f"GPU核心: {gr_clk} MHz | 显存: {mem_clk} MHz | 温度: {temp}°C | GPU占用: {util}%"
                gpu_pwr_desc = f"当前实时显卡功耗: {self.current_gpu_watts:.1f} W (原默认可达 250W+)"
            except:
                gpu_desc = "GPU: 未检测到 NVIDIA 显卡或驱动"

            # 2. 采集 CPU 状态与功耗
            cpu_load = 0.0
            try:
                cpu_load = self.get_cpu_load()
                if self.current_cpu_throttle < 100:
                    current_freq_mhz = max(800, int(2900 * (self.current_cpu_throttle / 100)))
                    freq_desc = f"{current_freq_mhz} MHz (锁频/省电)"
                    dynamic_max = 35.0 * (current_freq_mhz / 2900.0) ** 2
                else:
                    current_freq_mhz = int(2900 + 1100 * (cpu_load / 100.0))
                    freq_desc = f"{current_freq_mhz} MHz (睿频允许)"
                    dynamic_max = 65.0

                idle_watts = 4.5 if current_freq_mhz <= 800 else 6.5
                self.current_cpu_watts = idle_watts + (dynamic_max - idle_watts) * (cpu_load / 100.0)

                cpu_desc = f"CPU核心: {freq_desc} | 总体占用率: {cpu_load:.1f}%"
                cpu_pwr_desc = f"当前实时 CPU 功耗: {self.current_cpu_watts:.1f} W (原默认可达 65W+)"
            except Exception as e:
                cpu_desc = f"CPU 读取异常: {e}"

            # 3. 内存与显存占用
            try:
                vm = psutil.virtual_memory()
                self.current_ram_used = vm.used / (1024 ** 3)
                self.current_ram_total = vm.total / (1024 ** 3)
                self.current_ram_pct = vm.percent
                sys_desc = f"物理内存: {self.current_ram_used:.1f}G / {self.current_ram_total:.1f}G ({self.current_ram_pct}%) | 显存: {self.current_gpu_mem_used:.0f}MB / {self.current_gpu_mem_total:.0f}MB"
            except Exception:
                pass

            # 4. 获取能耗最高的 3 个进程
            top3_list, top3_summary = self.get_top3_processes()
            self.top3_processes_str = top3_summary

            # 5. 总功耗
            total_w = self.current_gpu_watts + self.current_cpu_watts
            saved_pct = max(0, int((1 - total_w / 315.0) * 100))
            total_desc = f"⚡ CPU + GPU 核心总功耗: {total_w:.1f} W (相比原满载省电 ~{saved_pct}%)"

            # 6. 存入内存历史队列供折线图绘制
            now = datetime.now()
            with self.history_lock:
                self.time_history.append(now)
                self.gpu_history.append(self.current_gpu_watts)
                self.cpu_history.append(self.current_cpu_watts)
                self.total_history.append(total_w)

            # 7. 写入细致的持久化 CSV 日志
            try:
                top1_str = f"{top3_list[0][1]} ({top3_list[0][0]:.1f}%)" if len(top3_list) > 0 else ""
                top2_str = f"{top3_list[1][1]} ({top3_list[1][0]:.1f}%)" if len(top3_list) > 1 else ""
                top3_str = f"{top3_list[2][1]} ({top3_list[2][0]:.1f}%)" if len(top3_list) > 2 else ""
                curr_config = f"GPU {self.gpu_clock_scale.get():.0f}MHz, {self.gpu_pl_scale.get():.0f}W | CPU {self.current_cpu_throttle}%"
                with open(self.csv_file_path, "a", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        now.strftime("%Y-%m-%d %H:%M:%S"),
                        f"{total_w:.1f}", f"{self.current_gpu_watts:.1f}", f"{self.current_cpu_watts:.1f}",
                        f"{cpu_load:.1f}", f"{self.current_gpu_util:.0f}", f"{self.current_gpu_temp:.0f}",
                        f"{self.current_ram_used:.2f}", f"{self.current_ram_total:.1f}", f"{self.current_ram_pct:.1f}",
                        f"{self.current_gpu_mem_used:.0f}", f"{self.current_gpu_mem_total:.0f}",
                        top1_str, top2_str, top3_str, curr_config
                    ])
            except Exception:
                pass

            # 安全更新 UI StringVar
            def safe_update_ui(g_desc=gpu_desc, g_pwr=gpu_pwr_desc, c_desc=cpu_desc, c_pwr=cpu_pwr_desc, t_desc=total_desc, s_desc=sys_desc, p_desc=top3_summary):
                try:
                    if g_desc:
                        self.gpu_status_var.set(g_desc)
                    if g_pwr:
                        self.gpu_power_var.set(g_pwr)
                    if c_desc:
                        self.cpu_status_var.set(c_desc)
                    if c_pwr:
                        self.cpu_power_var.set(c_pwr)
                    if s_desc:
                        self.sys_status_var.set(s_desc)
                    if p_desc:
                        self.top_procs_var.set(f"当时能耗最高的3个进程: {p_desc}")
                    if t_desc:
                        self.total_power_var.set(t_desc)
                except Exception:
                    pass

            try:
                self.root.after(0, safe_update_ui)
            except Exception:
                pass

            time.sleep(1.5)

if __name__ == "__main__":
    if not is_admin():
        run_as_admin()
    root = tk.Tk()
    app = PowerSaverApp(root)
    root.mainloop()