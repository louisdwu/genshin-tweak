// ============================================================================
// genshin-tweak.cs — Genshin Impact Tweak Toolkit (single-file native exe)
//
// Features:
//   1. Power presets + custom limits   (powercfg for CPU, nvidia-smi for GPU)
//   2. Game window pause               (WM_SYSCOMMAND SC_MOVE modal-loop trick)
//   3. Global hotkey Alt+`             (toggle pause anytime)
//   4. System tray                     (presets / show / exit with HW restore)
//   5. CSV event log                   (genshin_tweak_log.csv next to exe)
//
// Build (on Windows, zero install):
//   build.bat   ->  uses %WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe
//
// C# 5 ONLY — csc.exe from .NET 4.030319 does not support C# 6+ syntax.
// ============================================================================

using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;

namespace GenshinTweak
{
    // ========================================================================
    // Win32 P/Invoke + helpers (ported from genshin_window_pause.ps1)
    // ========================================================================
    internal static class Win32
    {
        public const uint WM_NCLBUTTONDOWN = 0x00A1;
        public const uint WM_CANCELMODE    = 0x001F;
        public const uint WM_KEYDOWN       = 0x0100;
        public const uint WM_SYSCOMMAND    = 0x0112;
        public const uint SC_MOVE          = 0xF010;
        public const int  HTCAPTION        = 2;
        public const int  GWL_STYLE        = -16;
        public const uint WS_CAPTION       = 0x00C00000;
        public const byte VK_MENU          = 0x12;
        public const byte VK_RETURN        = 0x0D;
        public const byte VK_ESCAPE        = 0x1B;
        public const uint KEYEVENTF_KEYUP  = 0x0002;
        public const int  WM_HOTKEY        = 0x0312;
        public const uint MOD_ALT          = 0x0001;
        public const uint VK_OEM_3         = 0x00C0;   // ~ key

        [DllImport("user32.dll", SetLastError = true)]
        public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);

        [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);

        [DllImport("user32.dll")]
        public static extern bool IsWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

        [DllImport("user32.dll")]
        public static extern bool SetForegroundWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        public static extern uint GetWindowLong(IntPtr hWnd, int nIndex);

        [DllImport("user32.dll")]
        public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);

        [DllImport("user32.dll")]
        public static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);

        [DllImport("user32.dll")]
        public static extern bool UnregisterHotKey(IntPtr hWnd, int id);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool GetSystemTimes(out FILETIME lpIdleTime, out FILETIME lpKernelTime, out FILETIME lpUserTime);

        public struct RECT
        {
            public int Left, Top, Right, Bottom;
        }

        public struct FILETIME
        {
            public uint LowPart;
            public int HighPart;
        }

        public static long FtToLong(FILETIME ft)
        {
            return ((long)ft.HighPart << 32) | (long)ft.LowPart;
        }

        // True when the window has a visible title bar:
        //   - windowed:  WS_CAPTION set and Top >= -10
        //   - borderless fullscreen: Top ~= -30  -> false
        //   - exclusive fullscreen: no WS_CAPTION -> false
        public static bool HasVisibleTitleBar(IntPtr hWnd)
        {
            uint style = GetWindowLong(hWnd, GWL_STYLE);
            if ((style & WS_CAPTION) != WS_CAPTION) return false;
            RECT wr;
            if (!GetWindowRect(hWnd, out wr)) return false;
            return wr.Top >= -10;
        }

        public static void SendAltEnter()
        {
            keybd_event(VK_MENU, 0, 0, UIntPtr.Zero);
            keybd_event(VK_RETURN, 0, 0, UIntPtr.Zero);
            keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
            keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
        }

        // WM_SYSCOMMAND + SC_MOVE: same as right-click title bar -> "Move".
        // DefWindowProc blocks the window thread inside the modal move loop
        // until ESC / Enter / mouse click — this is what freezes the game.
        public static bool PostSysMove(IntPtr hWnd, out int lastError)
        {
            lastError = 0;
            bool ok = PostMessage(hWnd, WM_SYSCOMMAND, (IntPtr)SC_MOVE, IntPtr.Zero);
            if (!ok) lastError = Marshal.GetLastWin32Error();
            return ok;
        }

        public static void SendEscapeToWindow(IntPtr hWnd)
        {
            PostMessage(hWnd, WM_KEYDOWN, (IntPtr)VK_ESCAPE, IntPtr.Zero);
        }

        public static bool PostCancelMode(IntPtr hWnd)
        {
            return PostMessage(hWnd, WM_CANCELMODE, IntPtr.Zero, IntPtr.Zero);
        }
    }

    // ========================================================================
    // Toast — non-activating, topmost OSD near the top of the screen
    // ========================================================================
    internal sealed class ToastForm : Form
    {
        private const int WS_EX_NOACTIVATE = 0x08000000;
        private const int WS_EX_TOOLWINDOW = 0x00000080;

        protected override bool ShowWithoutActivation
        {
            get { return true; }
        }

        protected override CreateParams CreateParams
        {
            get
            {
                CreateParams cp = base.CreateParams;
                cp.ExStyle |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW;
                return cp;
            }
        }
    }

    internal static class Toast
    {
        private static ToastForm _form;
        private static Timer _closeTimer;
        private static Timer _swTimer;
        private static Label _swLabel;
        private static DateTime _swStart;

        private static readonly Color BgColor      = Color.FromArgb(28, 24, 37);
        private static readonly Color WarnColor    = Color.FromArgb(243, 139, 168);
        private static readonly Color AccentColor  = Color.FromArgb(137, 220, 235);
        private static readonly Color SwColor      = Color.FromArgb(166, 227, 161);

        public static void Show(string line1, string line2, bool isWarning, int durationMs)
        {
            Close();

            Color accent = isWarning ? WarnColor : AccentColor;

            ToastForm f = new ToastForm();
            f.FormBorderStyle = FormBorderStyle.None;
            f.StartPosition = FormStartPosition.Manual;
            f.TopMost = true;
            f.ShowInTaskbar = false;
            f.BackColor = BgColor;
            f.Opacity = 0.94;
            f.Padding = new Padding(32, 14, 32, 14);

            Label lbl1 = new Label();
            lbl1.Text = line1;
            lbl1.Font = new Font("Microsoft YaHei UI", 12F, FontStyle.Bold);
            lbl1.ForeColor = accent;
            lbl1.BackColor = BgColor;
            lbl1.AutoSize = true;
            lbl1.Location = new Point(32, 14);
            f.Controls.Add(lbl1);

            int bottom = 14 + lbl1.PreferredHeight;

            Label lbl2 = null;
            if (!string.IsNullOrEmpty(line2))
            {
                lbl2 = new Label();
                lbl2.Text = line2;
                lbl2.Font = new Font("Microsoft YaHei UI", 9F);
                lbl2.ForeColor = Color.FromArgb(200, accent.R, accent.G, accent.B);
                lbl2.BackColor = BgColor;
                lbl2.AutoSize = true;
                lbl2.Location = new Point(32, bottom + 6);
                f.Controls.Add(lbl2);
                bottom = lbl2.Location.Y + lbl2.PreferredHeight;
            }

            if (durationMs == 0)
            {
                // persistent toast with live stopwatch (used while paused)
                _swStart = DateTime.Now;
                _swLabel = new Label();
                _swLabel.Text = "00:00";
                _swLabel.Font = new Font("Consolas", 11F, FontStyle.Bold);
                _swLabel.ForeColor = SwColor;
                _swLabel.BackColor = BgColor;
                _swLabel.AutoSize = true;
                _swLabel.Location = new Point(32 + lbl1.PreferredWidth + 24, 14);
                f.Controls.Add(_swLabel);
                bottom = Math.Max(bottom, _swLabel.Location.Y + _swLabel.PreferredHeight);

                _swTimer = new Timer();
                _swTimer.Interval = 1000;
                _swTimer.Tick += delegate
                {
                    TimeSpan ts = DateTime.Now - _swStart;
                    if (_swLabel != null && !_swLabel.IsDisposed)
                        _swLabel.Text = string.Format("{0:00}:{1:00}", ts.Minutes, ts.Seconds);
                };
                _swTimer.Start();
            }

            int w = Math.Max(lbl1.PreferredWidth, lbl2 != null ? lbl2.PreferredWidth : 0) + 64;
            if (_swLabel != null) w = Math.Max(w, _swLabel.Location.X - 32 + _swLabel.PreferredWidth + 32);
            f.Size = new Size(Math.Max(w, 240), bottom + 18);

            Rectangle wa = Screen.PrimaryScreen.WorkingArea;
            f.Location = new Point(wa.Left + (wa.Width - f.Width) / 2, wa.Top + 48);

            if (durationMs > 0)
            {
                _closeTimer = new Timer();
                _closeTimer.Interval = durationMs;
                _closeTimer.Tick += delegate { Close(); };
                _closeTimer.Start();
            }

            _form = f;
            f.Show();
        }

        public static void Close()
        {
            if (_closeTimer != null)
            {
                try { _closeTimer.Stop(); _closeTimer.Dispose(); } catch { }
                _closeTimer = null;
            }
            if (_swTimer != null)
            {
                try { _swTimer.Stop(); _swTimer.Dispose(); } catch { }
                _swTimer = null;
            }
            _swLabel = null;
            if (_form != null)
            {
                try { _form.Close(); _form.Dispose(); } catch { }
                _form = null;
            }
        }
    }

    // ========================================================================
    // PowerControl — powercfg (CPU) + nvidia-smi (GPU) + CSV event log
    // ========================================================================
    internal static class PowerControl
    {
        public static Action<string> Logger = delegate { };   // hooked up by MainForm

        private static readonly string CsvPath =
            Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "genshin_tweak_log.csv");

        private static long _lastIdle = -1;
        private static long _lastTotal = -1;

        // ---- process helpers ------------------------------------------------
        private static int Run(string exe, string args)
        {
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = exe;
                psi.Arguments = args;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                using (Process p = Process.Start(psi))
                {
                    p.WaitForExit(10000);
                    return p.HasExited ? p.ExitCode : -1;
                }
            }
            catch (Exception ex)
            {
                Logger("[ERR] " + exe + " " + args + " -> " + ex.Message);
                return -1;
            }
        }

        private static int RunCapture(string exe, string args, out string stderr)
        {
            stderr = "";
            try
            {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = exe;
                psi.Arguments = args;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                StringBuilder errBuf = new StringBuilder();
                using (Process p = Process.Start(psi))
                {
                    // async stderr read avoids pipe-buffer deadlock; stdout is small
                    p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs ev)
                    {
                        if (ev.Data != null) errBuf.AppendLine(ev.Data);
                    };
                    p.BeginErrorReadLine();
                    p.StandardOutput.ReadToEnd();
                    p.WaitForExit(10000);
                    stderr = errBuf.ToString();
                    return p.HasExited ? p.ExitCode : -1;
                }
            }
            catch (Exception ex)
            {
                Logger("[ERR] " + exe + " " + args + " -> " + ex.Message);
                return -1;
            }
        }

        public static bool NvidiaAvailable()
        {
            string err;
            return RunCapture("nvidia-smi", "", out err) == 0;
        }

        // ---- CPU ------------------------------------------------------------
        public static bool ApplyCpuLimit(int maxPercent)
        {
            int rc1 = Run("powercfg", "-setacvalueindex SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMAX " + maxPercent);
            int rc2 = Run("powercfg", "-setactive SCHEME_CURRENT");
            return rc1 == 0 && rc2 == 0;
        }

        public static bool ResetCpu()
        {
            return ApplyCpuLimit(100);
        }

        // ---- GPU ------------------------------------------------------------
        public static bool ApplyGpuLimit(int clockMhz, int powerWatt)
        {
            string err;
            int rc = RunCapture("nvidia-smi", "-lgc 300," + clockMhz, out err);
            if (rc != 0)
            {
                Logger("[ERR] nvidia-smi -lgc failed: " + err.Trim());
                return false;
            }
            int rcPl = RunCapture("nvidia-smi", "-pl " + powerWatt, out err);
            if (rcPl != 0 && err.ToLower().Contains("out of range"))
            {
                Logger("[WARN] power limit " + powerWatt + "W out of range, fallback to 100W");
                RunCapture("nvidia-smi", "-pl 100", out err);
            }
            else if (rcPl != 0)
            {
                Logger("[ERR] nvidia-smi -pl failed: " + err.Trim());
                return false;
            }
            return true;
        }

        public static bool ResetGpu()
        {
            string err;
            RunCapture("nvidia-smi", "-rgc", out err);
            RunCapture("nvidia-smi", "-pl 250", out err);
            return true;
        }

        public static void Cleanup()
        {
            // restore hardware to stock state (best effort)
            ResetGpu();
            ResetCpu();
        }

        // ---- CPU usage sampling ----------------------------------------------
        public static double GetCpuUsagePercent()
        {
            Win32.FILETIME idle, kernel, user;
            if (!Win32.GetSystemTimes(out idle, out kernel, out user)) return -1;
            long i = Win32.FtToLong(idle);
            long t = Win32.FtToLong(kernel) + Win32.FtToLong(user);
            if (_lastTotal < 0) { _lastIdle = i; _lastTotal = t; return -1; }
            long dTotal = t - _lastTotal;
            long dIdle = i - _lastIdle;
            _lastIdle = i;
            _lastTotal = t;
            if (dTotal <= 0) return -1;
            return Math.Max(0.0, Math.Min(100.0, 100.0 * (dTotal - dIdle) / dTotal));
        }

        // ---- CSV event log ----------------------------------------------------
        public static void LogEvent(string mode, string detail)
        {
            try
            {
                if (!File.Exists(CsvPath))
                {
                    File.AppendAllText(CsvPath, "time,mode,detail\r\n", new UTF8Encoding(false));
                }
                string line = string.Format("{0:yyyy-MM-dd HH:mm:ss},{1},{2}\r\n",
                    DateTime.Now, mode, detail.Replace(",", "，"));
                File.AppendAllText(CsvPath, line, new UTF8Encoding(false));
            }
            catch { /* logging must never crash the app */ }
        }
    }

    // ========================================================================
    // PauseController — game window freeze / resume (ported from the PS1)
    // ========================================================================
    internal static class PauseController
    {
        public static Action StateChanged = delegate { };   // hooked up by MainForm

        public static bool IsPaused;
        public static IntPtr Hwnd = IntPtr.Zero;
        public static bool WasFullscreen;
        public static DateTime PauseStart;

        private static readonly string[] WindowTitles = { "原神", "Genshin Impact" };
        private static readonly string[] ProcessNames = { "YuanShen", "GenshinImpact" };

        private static IntPtr FindGenshinHwnd()
        {
            // fast path: by window title
            foreach (string title in WindowTitles)
            {
                IntPtr h = Win32.FindWindow(null, title);
                if (h != IntPtr.Zero && Win32.IsWindow(h)) return h;
            }
            // fallback: by process name (exclusive fullscreen may have odd titles)
            foreach (string name in ProcessNames)
            {
                Process[] procs = Process.GetProcessesByName(name);
                if (procs.Length > 0 && procs[0].MainWindowHandle != IntPtr.Zero)
                {
                    IntPtr h = procs[0].MainWindowHandle;
                    foreach (Process p in procs) p.Dispose();
                    return h;
                }
                foreach (Process p in procs) p.Dispose();
            }
            return IntPtr.Zero;
        }

        private static void ShowToast(string line1, string line2, bool warning, int durationMs)
        {
            Toast.Show(line1, line2, warning, durationMs);
            PowerControl.Logger(line1 + (line2.Length > 0 ? " | " + line2 : ""));
        }

        public static void Pause()
        {
            IntPtr hwnd = FindGenshinHwnd();
            if (hwnd == IntPtr.Zero)
            {
                ShowToast("未找到原神窗口", "请确认游戏正在运行", true, 4500);
                return;
            }
            Hwnd = hwnd;

            bool hasTitleBar = Win32.HasVisibleTitleBar(hwnd);
            WasFullscreen = !hasTitleBar;

            if (WasFullscreen)
            {
                PowerControl.Logger("fullscreen detected, Alt+Enter -> windowed");
                Win32.SetForegroundWindow(hwnd);
                System.Threading.Thread.Sleep(200);
                Win32.SendAltEnter();

                int waited = 0;
                while (!Win32.HasVisibleTitleBar(hwnd) && waited < 3000)
                {
                    System.Threading.Thread.Sleep(100);
                    waited += 100;
                }
                if (!Win32.HasVisibleTitleBar(hwnd))
                {
                    ShowToast("切换窗口模式失败", "Alt+Enter 未生效，请手动切换", true, 4500);
                    return;
                }
                System.Threading.Thread.Sleep(300);

                // the game may recreate its window after the mode switch
                IntPtr newHwnd = FindGenshinHwnd();
                if (newHwnd != IntPtr.Zero) Hwnd = newHwnd;
            }

            int lastErr;
            bool ok = Win32.PostSysMove(Hwnd, out lastErr);
            if (ok)
            {
                IsPaused = true;
                PauseStart = DateTime.Now;
                ShowToast("游戏已时停 ❄", "Alt+` 恢复运行", true, 0);
                PowerControl.LogEvent("pause", "hwnd=" + Hwnd);
                StateChanged();
            }
            else
            {
                ShowToast("时停失败", "Win32 错误码: " + lastErr, true, 4500);
            }
        }

        public static void Resume()
        {
            IntPtr hwnd = Hwnd;
            if (hwnd == IntPtr.Zero || !Win32.IsWindow(hwnd)) hwnd = FindGenshinHwnd();
            if (hwnd == IntPtr.Zero)
            {
                ShowToast("未找到原神窗口", "游戏可能已关闭", true, 4500);
                IsPaused = false;
                StateChanged();
                return;
            }

            Win32.SendEscapeToWindow(hwnd);
            Win32.PostCancelMode(hwnd);

            if (WasFullscreen)
            {
                Win32.SetForegroundWindow(hwnd);
                System.Threading.Thread.Sleep(200);
                Win32.SendAltEnter();
            }

            IsPaused = false;
            string elapsed = "";
            TimeSpan ts = DateTime.Now - PauseStart;
            if (ts.TotalHours >= 1)
                elapsed = string.Format("暂停了 {0}:{1:00}:{2:00} · ", (int)ts.TotalHours, ts.Minutes, ts.Seconds);
            else if (ts.TotalSeconds >= 1)
                elapsed = string.Format("暂停了 {0:00}:{1:00} · ", ts.Minutes, ts.Seconds);

            ShowToast("游戏已恢复 ▶", elapsed + "Alt+` 再次时停", false, 3500);
            PowerControl.LogEvent("resume", "");
            StateChanged();
        }

        public static void Toggle()
        {
            if (IsPaused) Resume(); else Pause();
        }
    }

    // ========================================================================
    // MainForm
    // ========================================================================
    internal class MainForm : Form
    {
        private const int HOTKEY_ID = 1;

        private NotifyIcon _tray;
        private TrackBar _gpuClock;
        private TrackBar _gpuWatt;
        private TrackBar _cpuMax;
        private Label _gpuClockVal;
        private Label _gpuWattVal;
        private Label _cpuMaxVal;
        private Label _pauseStatus;
        private Button _pauseBtn;
        private TextBox _logBox;
        private Timer _cpuTimer;
        private Label _cpuLabel;
        private TableLayoutPanel _root;

        public MainForm()
        {
            // ---- window ----
            Text = "Genshin Tweak — 原神工具箱";
            Font = new Font("Microsoft YaHei UI", 9.75F);
            FormBorderStyle = FormBorderStyle.Sizable;
            MaximizeBox = true;
            StartPosition = FormStartPosition.CenterScreen;
            Icon = SystemIcons.Application;

            // layout is AutoSize-based (fonts drive sizes), so it adapts to any
            // DPI on its own; only the initial/min window size needs scaling
            using (Graphics g = CreateGraphics())
            {
                float f = g.DpiX / 96f;
                ClientSize = new Size((int)(620 * f), (int)(560 * f));
                MinimumSize = new Size((int)(560 * f), (int)(500 * f));
            }

            BuildUi();
            BuildTray();

            PowerControl.Logger = AppendLog;

            if (!PowerControl.NvidiaAvailable())
                AppendLog("[WARN] 未检测到 nvidia-smi，GPU 限频不可用（CPU 部分不受影响）");

            // ---- global hotkey Alt+` ----
            if (!Win32.RegisterHotKey(Handle, HOTKEY_ID, Win32.MOD_ALT, Win32.VK_OEM_3))
                AppendLog("[WARN] Alt+` 热键注册失败（可能被占用），可改用托盘菜单切换时停");

            // ---- CPU usage poller ----
            _cpuTimer = new Timer();
            _cpuTimer.Interval = 2000;
            _cpuTimer.Tick += CpuTimerTick;
            _cpuTimer.Start();

            PowerControl.LogEvent("start", "app launched");
            AppendLog("[OK] 启动完成。先选预设或拖动滑条，再进游戏按 Alt+` 时停。");
            UpdatePauseUi();
        }

        // ====================================================================
        // UI construction
        // ====================================================================
        private GroupBox NewGroup(string title)
        {
            GroupBox g = new GroupBox();
            g.Text = title;
            g.AutoSize = true;
            g.Dock = DockStyle.Fill;
            g.Padding = new Padding(10, 2, 10, 6);
            return g;
        }

        private Button NewButton(string text, EventHandler onClick)
        {
            Button b = new Button();
            b.Text = text;
            b.AutoSize = true;
            b.MinimumSize = new Size(140, 34);
            b.Padding = new Padding(6, 2, 6, 2);
            b.Click += onClick;
            return b;
        }

        private void BuildUi()
        {
            _root = new TableLayoutPanel();
            _root.Dock = DockStyle.Fill;
            _root.Padding = new Padding(6);
            _root.ColumnCount = 1;
            _root.RowCount = 4;
            _root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F));
            _root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            _root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            _root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            _root.RowStyles.Add(new RowStyle(SizeType.Percent, 100F));
            Controls.Add(_root);

            BuildPresetGroup();
            BuildCustomGroup();
            BuildPauseGroup();
            BuildLogGroup();
        }

        private void BuildPresetGroup()
        {
            GroupBox g = NewGroup("一键场景预设");
            FlowLayoutPanel flow = new FlowLayoutPanel();
            flow.Dock = DockStyle.Fill;
            flow.AutoSize = true;
            flow.WrapContents = false;
            g.Controls.Add(flow);

            string[] names = { "🎬 极致省电看视频", "💻 多网页/IDE 办公", "🔥 火力全开玩游戏" };
            EventHandler[] handlers =
            {
                delegate { ApplyVideoPreset(); },
                delegate { ApplyWorkPreset(); },
                delegate { ApplyGamePreset(); }
            };
            for (int i = 0; i < 3; i++)
            {
                Button b = NewButton(names[i], handlers[i]);
                b.MinimumSize = new Size(150, 34);
                flow.Controls.Add(b);
            }

            _cpuLabel = new Label();
            _cpuLabel.Text = "CPU: --%";
            _cpuLabel.ForeColor = Color.Gray;
            _cpuLabel.AutoSize = true;
            _cpuLabel.Margin = new Padding(14, 10, 4, 0);
            flow.Controls.Add(_cpuLabel);

            _root.Controls.Add(g, 0, 0);
        }

        private void BuildCustomGroup()
        {
            GroupBox g = NewGroup("自定义限频");
            TableLayoutPanel t = new TableLayoutPanel();
            t.Dock = DockStyle.Fill;
            t.AutoSize = true;
            t.ColumnCount = 3;
            t.RowCount = 4;
            t.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            t.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F));
            t.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            for (int r = 0; r < 4; r++)
                t.RowStyles.Add(new RowStyle(SizeType.AutoSize));

            _gpuClock = AddSlider(t, 0, "GPU 时钟 MHz", 300, 2100, 100, 300, out _gpuClockVal);
            _gpuWatt  = AddSlider(t, 1, "GPU 功耗 W",   50, 250, 10, 100, out _gpuWattVal);
            _cpuMax   = AddSlider(t, 2, "CPU 上限 %",   5, 100, 5, 100, out _cpuMaxVal);

            FlowLayoutPanel btns = new FlowLayoutPanel();
            btns.Dock = DockStyle.Fill;
            btns.AutoSize = true;
            btns.Margin = new Padding(0, 4, 0, 0);
            btns.Controls.Add(NewButton("应用自定义", delegate { ApplyCustom(); }));
            btns.Controls.Add(NewButton("恢复默认 (全解锁)", delegate { ResetDefaults(); }));
            t.Controls.Add(btns, 0, 3);
            t.SetColumnSpan(btns, 3);

            g.Controls.Add(t);
            _root.Controls.Add(g, 0, 1);
        }

        private void BuildPauseGroup()
        {
            GroupBox g = NewGroup("游戏时停 — 全局热键 Alt+`");
            TableLayoutPanel t = new TableLayoutPanel();
            t.Dock = DockStyle.Fill;
            t.AutoSize = true;
            t.ColumnCount = 2;
            t.RowCount = 2;
            t.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            t.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F));
            t.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            t.RowStyles.Add(new RowStyle(SizeType.AutoSize));

            _pauseStatus = new Label();
            _pauseStatus.Text = "▶ 游戏未运行 / 未时停";
            _pauseStatus.ForeColor = Color.FromArgb(166, 227, 161);
            _pauseStatus.AutoSize = true;
            _pauseStatus.Margin = new Padding(2, 2, 2, 6);
            t.Controls.Add(_pauseStatus, 0, 0);
            t.SetColumnSpan(_pauseStatus, 2);

            _pauseBtn = NewButton("切换时停 (Alt+`)", delegate { PauseController.Toggle(); });
            t.Controls.Add(_pauseBtn, 0, 1);

            Label hint = new Label();
            hint.Text = "原理: 让窗口进入系统移动模态循环，游戏主线程被阻塞即“时停”。ESC 恢复。";
            hint.ForeColor = Color.Gray;
            hint.AutoSize = true;
            hint.Margin = new Padding(14, 10, 2, 2);
            t.Controls.Add(hint, 1, 1);

            g.Controls.Add(t);
            _root.Controls.Add(g, 0, 2);
        }

        private void BuildLogGroup()
        {
            GroupBox g = NewGroup("运行日志");
            g.AutoSize = false;               // Fill row -> group takes leftover height
            _logBox = new TextBox();
            _logBox.Multiline = true;
            _logBox.ReadOnly = true;
            _logBox.ScrollBars = ScrollBars.Vertical;
            _logBox.Font = new Font("Consolas", 9F);
            _logBox.BackColor = Color.FromArgb(24, 24, 28);
            _logBox.ForeColor = Color.FromArgb(166, 227, 161);
            _logBox.Dock = DockStyle.Fill;
            g.Controls.Add(_logBox);
            _root.Controls.Add(g, 0, 3);
        }

        private TrackBar AddSlider(TableLayoutPanel parent, int row, string caption,
            int min, int max, int tick, int initial, out Label valueLabel)
        {
            Label cap = new Label();
            cap.Text = caption;
            cap.AutoSize = true;
            cap.Margin = new Padding(2, 10, 8, 2);
            parent.Controls.Add(cap, 0, row);

            TrackBar tb = new TrackBar();
            tb.Minimum = min;
            tb.Maximum = max;
            tb.TickStyle = TickStyle.None;
            tb.TickFrequency = tick;
            tb.SmallChange = tick;
            tb.LargeChange = tick * 2;
            tb.Value = initial;
            tb.Dock = DockStyle.Fill;
            tb.Margin = new Padding(2);
            parent.Controls.Add(tb, 1, row);

            valueLabel = new Label();
            valueLabel.Text = initial.ToString();
            valueLabel.AutoSize = true;
            valueLabel.Margin = new Padding(10, 10, 2, 2);
            parent.Controls.Add(valueLabel, 2, row);

            // CS1628: out params cannot be captured by anonymous methods
            Label val = valueLabel;
            tb.ValueChanged += delegate { val.Text = tb.Value.ToString(); };
            return tb;
        }

        private void BuildTray()
        {
            _tray = new NotifyIcon();
            _tray.Icon = SystemIcons.Application;
            _tray.Text = "Genshin Tweak | ▶ 运行中";
            _tray.Visible = true;
            _tray.DoubleClick += delegate { ShowWindow(); };

            ContextMenuStrip menu = new ContextMenuStrip();
            menu.Items.Add("时停切换 (Alt+`)", null, delegate { PauseController.Toggle(); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("🎬 极致省电看视频", null, delegate { ApplyVideoPreset(); });
            menu.Items.Add("💻 多网页/IDE 办公", null, delegate { ApplyWorkPreset(); });
            menu.Items.Add("🔥 火力全开玩游戏", null, delegate { ApplyGamePreset(); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("显示主窗口", null, delegate { ShowWindow(); });
            menu.Items.Add("退出并恢复硬件", null, delegate { RealExit(); });
            _tray.ContextMenuStrip = menu;
        }

        // ====================================================================
        // presets & custom
        // ====================================================================
        private void ApplyVideoPreset()
        {
            SyncSliders(300, 100, 20);
            bool gpu = PowerControl.ApplyGpuLimit(300, 100);
            bool cpu = PowerControl.ApplyCpuLimit(20);
            AppendLog((gpu && cpu ? "[OK]" : "[ERR]") +
                " 🎬 极致省电: GPU 300MHz / 100W, CPU 20%");
            PowerControl.LogEvent("video", "gpu=300MHz,100W cpu=20%");
        }

        private void ApplyWorkPreset()
        {
            SyncSliders(600, 120, 65);
            bool gpu = PowerControl.ApplyGpuLimit(600, 120);
            bool cpu = PowerControl.ApplyCpuLimit(65);
            AppendLog((gpu && cpu ? "[OK]" : "[ERR]") +
                " 💻 办公编程: GPU 600MHz / 120W, CPU 65%");
            PowerControl.LogEvent("work", "gpu=600MHz,120W cpu=65%");
        }

        private void ApplyGamePreset()
        {
            SyncSliders(2100, 250, 100);
            bool gpu = PowerControl.ResetGpu();     // unlock beats locking to 1650
            bool cpu = PowerControl.ApplyCpuLimit(100);
            AppendLog((gpu && cpu ? "[OK]" : "[ERR]") +
                " 🔥 游戏模式: GPU 解锁 / 250W, CPU 100%");
            PowerControl.LogEvent("game", "gpu=unlock,250W cpu=100%");
        }

        private void ApplyCustom()
        {
            int clk = _gpuClock.Value;
            int watt = _gpuWatt.Value;
            int cpuPct = _cpuMax.Value;
            bool gpu = PowerControl.ApplyGpuLimit(clk, watt);
            bool cpu = PowerControl.ApplyCpuLimit(cpuPct);
            AppendLog((gpu && cpu ? "[OK]" : "[ERR]") +
                string.Format(" 自定义: GPU {0}MHz / {1}W, CPU {2}%", clk, watt, cpuPct));
            PowerControl.LogEvent("custom",
                string.Format("gpu={0}MHz,{1}W cpu={2}%", clk, watt, cpuPct));
        }

        private void ResetDefaults()
        {
            SyncSliders(2100, 250, 100);
            bool gpu = PowerControl.ResetGpu();
            bool cpu = PowerControl.ResetCpu();
            AppendLog((gpu && cpu ? "[OK]" : "[ERR]") + " 已恢复默认: GPU 解锁, CPU 100%");
            PowerControl.LogEvent("reset", "all unlocked");
        }

        private void SyncSliders(int clk, int watt, int cpu)
        {
            _gpuClock.Value = Clamp(_gpuClock, clk);
            _gpuWatt.Value = Clamp(_gpuWatt, watt);
            _cpuMax.Value = Clamp(_cpuMax, cpu);
        }

        private static int Clamp(TrackBar tb, int v)
        {
            return Math.Max(tb.Minimum, Math.Min(tb.Maximum, v));
        }

        // ====================================================================
        // pause UI sync
        // ====================================================================
        private void UpdatePauseUi()
        {
            if (PauseController.IsPaused)
            {
                _pauseStatus.Text = "❄ 游戏已时停";
                _pauseStatus.ForeColor = WarnColor();
                _pauseBtn.Text = "恢复运行 (Alt+`)";
                _tray.Text = "Genshin Tweak | ❄ 已时停";
            }
            else
            {
                _pauseStatus.Text = "▶ 游戏未运行 / 未时停";
                _pauseStatus.ForeColor = OkColor();
                _pauseBtn.Text = "切换时停 (Alt+`)";
                _tray.Text = "Genshin Tweak | ▶ 运行中";
            }
        }

        private static Color WarnColor() { return Color.FromArgb(243, 139, 168); }
        private static Color OkColor() { return Color.FromArgb(166, 227, 161); }

        // ====================================================================
        // helpers
        // ====================================================================
        private void AppendLog(string msg)
        {
            string line = string.Format("[{0:HH:mm:ss}] {1}\r\n", DateTime.Now, msg);
            if (_logBox.TextLength > 32000) _logBox.Clear();
            _logBox.AppendText(line);
        }

        private void CpuTimerTick(object sender, EventArgs e)
        {
            double cpu = PowerControl.GetCpuUsagePercent();
            if (cpu >= 0) _cpuLabel.Text = string.Format("CPU: {0:0}%", cpu);
        }

        private void ShowWindow()
        {
            Show();
            WindowState = FormWindowState.Normal;
            Activate();
        }

        private void RealExit()
        {
            try
            {
                if (PauseController.IsPaused) PauseController.Resume();
                PowerControl.Cleanup();
                PowerControl.LogEvent("exit", "hardware restored");
            }
            catch { }
            Win32.UnregisterHotKey(Handle, HOTKEY_ID);
            _tray.Visible = false;
            Application.Exit();
        }

        // ====================================================================
        // overrides
        // ====================================================================
        protected override void WndProc(ref Message m)
        {
            if (m.Msg == Win32.WM_HOTKEY && m.WParam.ToInt32() == HOTKEY_ID)
            {
                PauseController.Toggle();
                return;
            }
            base.WndProc(ref m);
        }

        protected override void OnLoad(EventArgs e)
        {
            PauseController.StateChanged += UpdatePauseUi;
            base.OnLoad(e);
        }

        // X button hides to tray (real exit only from tray menu)
        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (e.CloseReason == CloseReason.UserClosing && !_reallyExiting)
            {
                e.Cancel = true;
                Hide();
                return;
            }
            base.OnFormClosing(e);
        }

        private bool _reallyExiting;

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                _reallyExiting = true;
                if (_tray != null) { try { _tray.Dispose(); } catch { } }
                Toast.Close();
            }
            base.Dispose(disposing);
        }
    }

    // ========================================================================
    // entry point
    // ========================================================================
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new MainForm());
        }
    }
}
