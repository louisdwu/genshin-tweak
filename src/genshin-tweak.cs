// ============================================================================
// genshin-tweak.cs — Genshin Impact Tweak Toolkit (single-file native exe)
//
// Features:
//   1. Power presets + custom limits   (powercfg for CPU, nvidia-smi for GPU)
//   2. Game window pause               (WM_SYSCOMMAND SC_MOVE modal-loop trick)
//   3. Global hotkey Alt+`             (toggle pause anytime)
//   4. System tray                     (presets / show / exit with HW restore)
//   5. CSV event log                   (genshin_tweak_log.csv next to exe)
//   6. Foreground-app volume + mute    (WASAPI, hotkeys Pause / Ctrl+Alt+Up/Down)
//      — merged from the "Volume Controller" project, now native C#
//
// Build (on Windows, zero install):
//   build.bat   ->  uses %WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe
//
// C# 5 ONLY — csc.exe from .NET 4.030319 does not support C# 6+ syntax.
// ============================================================================

using System;
using System.Collections.Generic;
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
            lbl1.Font = new Font("Microsoft YaHei UI", 15F, FontStyle.Bold);
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
                lbl2.Font = new Font("Microsoft YaHei UI", 11F);
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
                _swLabel.Font = new Font("Consolas", 14F, FontStyle.Bold);
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
            // ~28% down from the top: stays clear of where the mouse cursor
            // usually rests near the top-center of the screen
            f.Location = new Point(wa.Left + (wa.Width - f.Width) / 2,
                wa.Top + (int)(wa.Height * 0.28));

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
                StringBuilder outBuf = new StringBuilder();
                using (Process p = Process.Start(psi))
                {
                    // async reads avoid pipe-buffer deadlock. NOTE: nvidia-smi
                    // prints many errors on STDOUT, so both streams go into
                    // one buffer for phrase matching.
                    p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs ev)
                    {
                        if (ev.Data != null) outBuf.AppendLine(ev.Data);
                    };
                    p.OutputDataReceived += delegate(object s, DataReceivedEventArgs ev)
                    {
                        if (ev.Data != null) outBuf.AppendLine(ev.Data);
                    };
                    p.BeginErrorReadLine();
                    p.BeginOutputReadLine();
                    p.WaitForExit(10000);
                    if (!p.HasExited) { try { p.Kill(); } catch { } }
                    // parameterless WaitForExit drains the async events
                    // into outBuf (the timed overload does NOT do that)
                    p.WaitForExit();
                    stderr = outBuf.ToString();
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

        public static void QueryPowerRange(out int minW, out int maxW)
        {
            minW = -1;
            maxW = -1;
            string outp;
            if (RunCapture("nvidia-smi", "-q -d POWER", out outp) != 0) return;
            minW = ParsePowerLimit(outp, "Min Power Limit");
            maxW = ParsePowerLimit(outp, "Max Power Limit");
        }

        // parses lines like "    Min Power Limit            : 100.00 W"
        private static int ParsePowerLimit(string text, string tag)
        {
            foreach (string lineRaw in text.Split('\n'))
            {
                string line = lineRaw.Trim();
                if (line.IndexOf(tag, StringComparison.OrdinalIgnoreCase) < 0) continue;
                int c = line.IndexOf(':');
                if (c < 0) continue;
                string num = "";
                foreach (char ch in line.Substring(c + 1))
                {
                    if (char.IsDigit(ch) || ch == '.') num += ch;
                    else if (num.Length > 0) break;
                }
                double v;
                if (double.TryParse(num, System.Globalization.NumberStyles.Float,
                    System.Globalization.CultureInfo.InvariantCulture, out v))
                    return (int)Math.Round(v);
            }
            return -1;
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
            string errLow = err.ToLower();
            if (rcPl != 0 && (errLow.Contains("out of range")
                           || errLow.Contains("not in a valid range")))
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
        private static readonly object CsvLock = new object();

        public static void LogEvent(string mode, string detail)
        {
            try
            {
                lock (CsvLock)
                {
                    if (!File.Exists(CsvPath))
                    {
                        File.AppendAllText(CsvPath, "time,mode,detail\r\n", new UTF8Encoding(false));
                    }
                    string line = string.Format("{0:yyyy-MM-dd HH:mm:ss},{1},{2}\r\n",
                        DateTime.Now, mode, detail.Replace(",", "，"));
                    File.AppendAllText(CsvPath, line, new UTF8Encoding(false));
                }
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
        private Icon _appIcon;
        private Dictionary<string, Icon> _modeIcons;
        private string _modeName = "默认";
        private string _modeKey = "default";
        private bool _iconFallback;
        private string _savedMode = "";
        private int _savedGpu, _savedWatt, _savedCpu;
        private bool _syncing;
        private FormWindowState _preHideState = FormWindowState.Maximized;
        private bool _uiReady;

        private Button _btnVideo, _btnWork, _btnGame, _btnCustom;
        private ToolStripMenuItem _miVideo, _miWork, _miGame, _miCustom, _miDefault;

        // ---- foreground volume (merged Volume Controller) ----
        private CheckBox _volEnabled;
        private NumericUpDown _volStep;
        private TextBox _volMuteKey, _volUpKey, _volDownKey;
        private Label _volStatus;
        private Timer _volTimer;
        private Timer _volCaptureTimeout;
        private ToolTip _volTip;
        private volatile int _volStepPct = 2;
        private bool _volSyncing;
        private bool _savedVolEnabled = true;
        private int _savedVolStep = 2;
        private string _savedVolMute, _savedVolUp, _savedVolDown;

        private const string DefaultVolumeMuteKey = "Pause";
        private const string DefaultVolumeUpKey = "Ctrl+Alt+Up";
        private const string DefaultVolumeDownKey = "Ctrl+Alt+Down";
        private static readonly Color CaptureColor = Color.FromArgb(255, 244, 190);

        private static readonly Color ModeGreen  = Color.FromArgb(166, 227, 161);
        private static readonly Color ModeBlue   = Color.FromArgb(137, 180, 250);
        private static readonly Color ModeOrange = Color.FromArgb(250, 179, 135);
        private static readonly Color ModeWhite  = Color.FromArgb(245, 246, 255);

        public MainForm()
        {
            // ---- window ----
            Text = "Genshin Tweak — 原神工具箱";
            Font = new Font("Microsoft YaHei UI", 9.75F);
            FormBorderStyle = FormBorderStyle.Sizable;
            MaximizeBox = true;
            StartPosition = FormStartPosition.CenterScreen;
            WindowState = FormWindowState.Maximized;   // open maximized by default
            _appIcon = LoadAppIcon();
            Icon = _appIcon;

            // layout is AutoSize-based (fonts drive sizes), so it adapts to any
            // DPI on its own; only the initial/min window size needs scaling
            using (Graphics g = CreateGraphics())
            {
                float f = g.DpiX / 96f;
                ClientSize = new Size((int)(640 * f), (int)(800 * f));
                MinimumSize = new Size((int)(560 * f), (int)(680 * f));
            }

            LoadWindowStatePrefs();
            _uiReady = true;

            BuildUi();
            LoadModeIcons();
            BuildTray();
            if (_iconFallback)
                AppendLog("[WARN] 嵌入图标加载失败, 使用系统默认图标");

            PowerControl.Logger = AppendLog;

            if (!PowerControl.NvidiaAvailable())
            {
                AppendLog("[WARN] 未检测到 nvidia-smi，GPU 限频不可用（CPU 部分不受影响）");
            }
            else
            {
                int minW, maxW;
                PowerControl.QueryPowerRange(out minW, out maxW);
                if (minW > 0 || maxW > 0)
                {
                    _syncing = true;
                    if (minW > 0) _gpuWatt.Minimum = Math.Max(1, minW);
                    if (maxW > 0) _gpuWatt.Maximum = Math.Max(_gpuWatt.Minimum, maxW);
                    if (_gpuWatt.Value > _gpuWatt.Maximum) _gpuWatt.Value = _gpuWatt.Maximum;
                    if (_gpuWatt.Value < _gpuWatt.Minimum) _gpuWatt.Value = _gpuWatt.Minimum;
                    _syncing = false;
                    AppendLog("[OK] GPU 功耗墙范围: " + _gpuWatt.Minimum + "W - " + _gpuWatt.Maximum + "W (来自驱动)");
                }
            }

            // ---- global hotkey Alt+` ----
            if (!Win32.RegisterHotKey(Handle, HOTKEY_ID, Win32.MOD_ALT, Win32.VK_OEM_3))
                AppendLog("[WARN] Alt+` 热键注册失败（可能被占用），可改用托盘菜单切换时停");

            // ---- CPU usage poller ----
            _cpuTimer = new Timer();
            _cpuTimer.Interval = 2000;
            _cpuTimer.Tick += CpuTimerTick;
            _cpuTimer.Start();

            // ---- foreground-app volume hotkeys (merged Volume Controller) ----
            VolumeHud.Attach(this);
            VolumeKeys.Attach(this);
            VolumeKeys.Step = VolumeStep;
            VolumeKeys.Mute = VolumeMute;
            if (!VolumeKeys.Install())
                AppendLog("[WARN] 全局键盘钩子安装失败，音量热键与热键修改不可用（其余功能不受影响）");
            ApplyVolumeSettings(false);
            UpdateVolumeLabel(CoreAudio.QueryForeground());

            _volTimer = new Timer();
            _volTimer.Interval = 2000;
            _volTimer.Tick += VolTimerTick;
            _volTimer.Start();

            RestoreProfilePrefs();
            PowerControl.LogEvent("start", "app launched");
            AppendLog("[OK] 启动完成。先选预设或拖动滑条，再进游戏按 Alt+` 时停；"
                + "前台应用音量用 Pause 静音、Ctrl+Alt+↑/↓ 调节。");
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
            _root.RowCount = 5;
            _root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F));
            _root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            _root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            _root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            _root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            _root.RowStyles.Add(new RowStyle(SizeType.Percent, 100F));
            Controls.Add(_root);

            BuildPresetGroup();
            BuildCustomGroup();
            BuildPauseGroup();
            BuildVolumeGroup();
            BuildLogGroup();
        }

        private void BuildPresetGroup()
        {
            GroupBox g = NewGroup("一键模式预设 — 当前选择高亮");
            FlowLayoutPanel flow = new FlowLayoutPanel();
            flow.Dock = DockStyle.Fill;
            flow.AutoSize = true;
            g.Controls.Add(flow);

            _btnVideo  = NewButton("🎬 极致省电看视频", delegate { ApplyVideoPreset(); });
            _btnWork   = NewButton("💻 多网页/IDE 办公", delegate { ApplyWorkPreset(); });
            _btnGame   = NewButton("🔥 火力全开玩游戏", delegate { ApplyGamePreset(); });
            _btnCustom = NewButton("⚪ 自定义", delegate { MarkCustom(); });
            _btnVideo.MinimumSize  = new Size(150, 34);
            _btnWork.MinimumSize   = new Size(150, 34);
            _btnGame.MinimumSize   = new Size(150, 34);
            _btnCustom.MinimumSize = new Size(110, 34);
            flow.Controls.Add(_btnVideo);
            flow.Controls.Add(_btnWork);
            flow.Controls.Add(_btnGame);
            flow.Controls.Add(_btnCustom);

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

        // ====================================================================
        // foreground application volume (merged from Volume Controller)
        // ====================================================================
        private void BuildVolumeGroup()
        {
            GroupBox g = NewGroup("前台应用音量 — 独立控制焦点窗口（点热键框可重录）");

            TableLayoutPanel t = new TableLayoutPanel();
            t.Dock = DockStyle.Fill;
            t.AutoSize = true;
            t.ColumnCount = 1;
            t.RowCount = 2;
            t.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F));
            for (int i = 0; i < 2; i++) t.RowStyles.Add(new RowStyle(SizeType.AutoSize));

            _volStatus = new Label();
            _volStatus.Text = "前台: --";
            _volStatus.ForeColor = Color.Gray;
            _volStatus.AutoSize = true;
            _volStatus.Margin = new Padding(2, 2, 2, 4);
            t.Controls.Add(_volStatus, 0, 0);

            FlowLayoutPanel flow = new FlowLayoutPanel();
            flow.Dock = DockStyle.Fill;
            flow.AutoSize = true;
            flow.WrapContents = true;

            _volEnabled = new CheckBox();
            _volEnabled.Text = "启用全局热键";
            _volEnabled.AutoSize = true;
            _volEnabled.Checked = _savedVolEnabled;
            _volEnabled.Margin = new Padding(2, 8, 14, 2);
            flow.Controls.Add(_volEnabled);

            Label stepCap = new Label();
            stepCap.Text = "步进 %";
            stepCap.AutoSize = true;
            stepCap.Margin = new Padding(0, 11, 4, 2);
            flow.Controls.Add(stepCap);

            _volStep = new NumericUpDown();
            _volStep.Minimum = 1;
            _volStep.Maximum = 50;
            _volStep.Value = Math.Max(1, Math.Min(50, _savedVolStep));
            _volStep.Width = 52;
            _volStep.Margin = new Padding(0, 7, 14, 2);
            flow.Controls.Add(_volStep);

            _volMuteKey = NewHotkeyBox(_savedVolMute == null ? DefaultVolumeMuteKey : _savedVolMute);
            _volUpKey = NewHotkeyBox(_savedVolUp == null ? DefaultVolumeUpKey : _savedVolUp);
            _volDownKey = NewHotkeyBox(_savedVolDown == null ? DefaultVolumeDownKey : _savedVolDown);

            AddHotkeyField(flow, "静音", _volMuteKey);
            AddHotkeyField(flow, "音量-", _volDownKey);
            AddHotkeyField(flow, "音量+", _volUpKey);

            Button btnDown = NewButton("🔉 音量-", delegate { VolumeStep(-1); });
            btnDown.MinimumSize = new Size(96, 34);
            Button btnMute = NewButton("🔇 静音", delegate { VolumeMute(); });
            btnMute.MinimumSize = new Size(96, 34);
            Button btnUp = NewButton("🔊 音量+", delegate { VolumeStep(1); });
            btnUp.MinimumSize = new Size(96, 34);
            Button btnReset = NewButton("默认热键", delegate { ResetVolumeHotkeys(); });
            btnReset.MinimumSize = new Size(96, 34);
            flow.Controls.Add(btnDown);
            flow.Controls.Add(btnMute);
            flow.Controls.Add(btnUp);
            flow.Controls.Add(btnReset);
            t.Controls.Add(flow, 0, 1);

            // the details live in a tooltip so the log below keeps its space
            _volTip = new ToolTip();
            _volTip.AutoPopDelay = 15000;
            _volTip.InitialDelay = 400;
            _volTip.ReshowDelay = 100;
            string tip = "热键只作用于当前前台应用；同名多进程（Chrome / Electron 等）会自动同步。"
                + "热键会被拦截，不再传给前台程序。点输入框后直接按新组合键即可修改（Esc 取消）。";
            _volTip.SetToolTip(_volEnabled, tip);
            _volTip.SetToolTip(_volStep, tip);
            _volTip.SetToolTip(_volMuteKey, tip);
            _volTip.SetToolTip(_volUpKey, tip);
            _volTip.SetToolTip(_volDownKey, tip);

            g.Controls.Add(t);
            _root.Controls.Add(g, 0, 3);

            // wire the events only after the initial values are in place
            _volEnabled.CheckedChanged += delegate { if (!_volSyncing) ApplyVolumeSettings(true); };
            _volStep.ValueChanged += delegate { if (!_volSyncing) ApplyVolumeSettings(true); };
            _volMuteKey.Enter += BeginHotkeyCapture;
            _volMuteKey.Leave += EndHotkeyCapture;
            _volUpKey.Enter += BeginHotkeyCapture;
            _volUpKey.Leave += EndHotkeyCapture;
            _volDownKey.Enter += BeginHotkeyCapture;
            _volDownKey.Leave += EndHotkeyCapture;
            // clicking another window must not leave the hook in capture mode
            Deactivate += delegate { CancelHotkeyCapture(); };
        }

        private void CancelHotkeyCapture()
        {
            VolumeKeys.CancelCapture();
            if (_volCaptureTimeout != null) _volCaptureTimeout.Stop();
            if (_volMuteKey != null) _volMuteKey.BackColor = SystemColors.Window;
            if (_volUpKey != null) _volUpKey.BackColor = SystemColors.Window;
            if (_volDownKey != null) _volDownKey.BackColor = SystemColors.Window;
        }

        private TextBox NewHotkeyBox(string text)
        {
            TextBox box = new TextBox();
            box.Text = text;
            box.ReadOnly = true;
            box.ShortcutsEnabled = false;
            box.TextAlign = HorizontalAlignment.Center;
            box.Width = 118;
            box.Margin = new Padding(0, 7, 14, 2);
            box.Cursor = Cursors.Hand;
            return box;
        }

        private void AddHotkeyField(FlowLayoutPanel parent, string caption, TextBox box)
        {
            Label cap = new Label();
            cap.Text = caption;
            cap.AutoSize = true;
            cap.Margin = new Padding(0, 11, 4, 2);
            parent.Controls.Add(cap);
            parent.Controls.Add(box);
        }

        // The low-level hook captures the combo, so Pause / PrintScreen work
        // here even though WinForms never raises KeyDown for them.
        private void BeginHotkeyCapture(object sender, EventArgs e)
        {
            TextBox box = sender as TextBox;
            if (box == null) return;
            box.Tag = box.Text;
            box.BackColor = CaptureColor;
            VolumeKeys.BeginCapture(delegate(Hotkey picked) { CommitHotkey(box, picked); });

            // safety net: never leave the global hook swallowing keys forever
            if (_volCaptureTimeout == null)
            {
                _volCaptureTimeout = new Timer();
                _volCaptureTimeout.Interval = 10000;
                _volCaptureTimeout.Tick += delegate
                {
                    _volCaptureTimeout.Stop();
                    CancelHotkeyCapture();
                    AppendLog("[--] 热键捕获超时，未修改");
                };
            }
            _volCaptureTimeout.Stop();
            _volCaptureTimeout.Start();
            AppendLog("[..] 请按下新的热键组合（Esc 取消）");
        }

        private void EndHotkeyCapture(object sender, EventArgs e)
        {
            TextBox box = sender as TextBox;
            if (box == null) return;
            box.BackColor = SystemColors.Window;
            VolumeKeys.CancelCapture();
            if (_volCaptureTimeout != null) _volCaptureTimeout.Stop();
        }

        private void CommitHotkey(TextBox box, Hotkey picked)
        {
            CancelHotkeyCapture();
            if (picked.IsEmpty) return;

            if (IsPauseHotkey(picked) || IsDuplicateHotkey(box, picked))
            {
                string previous = box.Tag as string;
                if (previous != null) box.Text = previous;
                AppendLog("[WARN] 组合 " + picked.ToString() + " 已被占用（时停 Alt+` 或其它音量热键），未修改");
                return;
            }

            box.Text = picked.ToString();
            ApplyVolumeSettings(true);
            AppendLog("[OK] 热键已更新: " + picked.ToString());
        }

        private static bool IsPauseHotkey(Hotkey h)
        {
            return h.Vk == Win32.VK_OEM_3 && h.Alt && !h.Ctrl && !h.Shift && !h.Win;
        }

        private bool IsDuplicateHotkey(TextBox box, Hotkey h)
        {
            TextBox[] all = new TextBox[] { _volMuteKey, _volUpKey, _volDownKey };
            foreach (TextBox other in all)
            {
                if (other == null || other == box) continue;
                Hotkey o = Hotkey.Parse(other.Text);
                if (o.IsEmpty) continue;
                if (o.Vk == h.Vk && o.Ctrl == h.Ctrl && o.Alt == h.Alt &&
                    o.Shift == h.Shift && o.Win == h.Win) return true;
            }
            return false;
        }

        private void ResetVolumeHotkeys()
        {
            _volSyncing = true;
            _volMuteKey.Text = DefaultVolumeMuteKey;
            _volUpKey.Text = DefaultVolumeUpKey;
            _volDownKey.Text = DefaultVolumeDownKey;
            _volEnabled.Checked = true;
            _volStep.Value = 2;
            _volSyncing = false;
            ApplyVolumeSettings(true);
            AppendLog("[OK] 音量热键已恢复默认: 静音 Pause · 音量 Ctrl+Alt+↑ / Ctrl+Alt+↓");
        }

        private void ApplyVolumeSettings(bool save)
        {
            if (_volMuteKey == null || _volStep == null || _volEnabled == null) return;

            Hotkey mute = Hotkey.Parse(_volMuteKey.Text);
            Hotkey up = Hotkey.Parse(_volUpKey.Text);
            Hotkey down = Hotkey.Parse(_volDownKey.Text);

            VolumeKeys.SetHotkeys(mute, up, down);
            VolumeKeys.Enabled = _volEnabled.Checked;
            _volStepPct = (int)_volStep.Value;

            if (!save) return;

            AppendLog(string.Format("{0} 音量热键: 静音 [{1}] · 音量- [{2}] · 音量+ [{3}] · 步进 {4}%",
                _volEnabled.Checked ? "[OK]" : "[--]",
                KeyText(mute), KeyText(down), KeyText(up), _volStepPct));
            SaveWindowState();
        }

        private static string KeyText(Hotkey h)
        {
            return h.IsEmpty ? "未设置" : h.ToString();
        }

        // called on the hotkey worker thread AND on the UI thread
        private void VolumeStep(int dir)
        {
            int pct = _volStepPct;
            if (pct < 1) pct = 1;
            AudioStatus st = CoreAudio.AdjustForeground(dir * (pct / 100f));
            if (!st.Ok)
                PowerControl.Logger("[ERR] 音量调节失败: " + (st.Error == null ? "未知错误" : st.Error));
            UpdateVolumeLabel(st);
            VolumeHud.Show(st);
        }

        private void VolumeMute()
        {
            AudioStatus st = CoreAudio.ToggleForegroundMute();
            if (!st.Ok)
            {
                PowerControl.Logger("[ERR] 静音切换失败: " + (st.Error == null ? "未知错误" : st.Error));
            }
            else if (st.HasAudio)
            {
                PowerControl.LogEvent("volume", st.AppName + (st.Muted ? " muted" : " unmuted"));
            }
            UpdateVolumeLabel(st);
            VolumeHud.Show(st);
        }

        private void UpdateVolumeLabel(AudioStatus st)
        {
            if (_volStatus == null || _volStatus.IsDisposed) return;
            if (InvokeRequired)
            {
                try { BeginInvoke(new Action<AudioStatus>(UpdateVolumeLabel), st); } catch { }
                return;
            }

            if (!st.Ok)
            {
                _volStatus.Text = "前台: 音量控制不可用";
                _volStatus.ForeColor = WarnColor();
                return;
            }

            string app = st.AppName.Length > 0 ? st.AppName : "(无前台窗口)";
            if (!st.HasAudio)
            {
                _volStatus.Text = "前台: " + app + " · 无音频会话";
                _volStatus.ForeColor = Color.Gray;
            }
            else if (st.Muted)
            {
                _volStatus.Text = "前台: " + app + " · 已静音";
                _volStatus.ForeColor = WarnColor();
            }
            else
            {
                int pct = (int)Math.Round(st.Volume * 100f);
                _volStatus.Text = "前台: " + app + " · " + pct + "%";
                _volStatus.ForeColor = OkColor();
            }
        }

        // poll on a worker thread: a hung audio service must never freeze the UI
        private void VolTimerTick(object sender, EventArgs e)
        {
            System.Threading.ThreadPool.QueueUserWorkItem(delegate(object state)
            {
                UpdateVolumeLabel(CoreAudio.QueryForeground());
            });
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
            // The percent row only gets its final height after the form has been
            // laid out; a layout pass can resize the group without re-docking the
            // child, which left the log box one line high. Force the child layout.
            g.Resize += delegate { g.PerformLayout(); };
            _root.Controls.Add(g, 0, 4);
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
            tb.ValueChanged += delegate
            {
                val.Text = tb.Value.ToString();
                if (!_syncing) MarkCustom();
            };
            return tb;
        }

        private void BuildTray()
        {
            _tray = new NotifyIcon();
            _tray.Icon = _appIcon;
            _tray.Text = "Genshin Tweak";
            _tray.Visible = true;
            _tray.DoubleClick += delegate { ShowWindow(); };

            ContextMenuStrip menu = new ContextMenuStrip();
            menu.Items.Add("时停切换 (Alt+`)", null, delegate { PauseController.Toggle(); });
            menu.Items.Add(new ToolStripSeparator());

            _miVideo  = new ToolStripMenuItem("🎬 极致省电看视频");
            _miVideo.Click += delegate { ApplyVideoPreset(); };
            _miWork   = new ToolStripMenuItem("💻 多网页/IDE 办公");
            _miWork.Click += delegate { ApplyWorkPreset(); };
            _miGame   = new ToolStripMenuItem("🔥 火力全开玩游戏");
            _miGame.Click += delegate { ApplyGamePreset(); };
            _miCustom = new ToolStripMenuItem("⚪ 自定义 (打开主窗口微调)");
            _miCustom.Click += delegate { MarkCustom(); ShowWindow(); };
            _miDefault= new ToolStripMenuItem("↺ 默认 (全解锁)");
            _miDefault.Click += delegate { ResetDefaults(); };
            menu.Items.AddRange(new ToolStripItem[]
            {
                _miVideo, _miWork, _miGame, _miCustom, _miDefault
            });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("静音前台应用", null, delegate { VolumeMute(); });
            menu.Items.Add("前台音量 +", null, delegate { VolumeStep(1); });
            menu.Items.Add("前台音量 -", null, delegate { VolumeStep(-1); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("显示主窗口", null, delegate { ShowWindow(); });
            menu.Items.Add("退出并恢复硬件", null, delegate { RealExit(); });
            _tray.ContextMenuStrip = menu;
            UpdateTrayChecks(_modeKey);
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
            SetTrayMode("video", "🎬 视频");
        }

        private void ApplyWorkPreset()
        {
            SyncSliders(600, 120, 65);
            bool gpu = PowerControl.ApplyGpuLimit(600, 120);
            bool cpu = PowerControl.ApplyCpuLimit(65);
            AppendLog((gpu && cpu ? "[OK]" : "[ERR]") +
                " 💻 办公编程: GPU 600MHz / 120W, CPU 65%");
            PowerControl.LogEvent("work", "gpu=600MHz,120W cpu=65%");
            SetTrayMode("work", "💻 办公");
        }

        private void ApplyGamePreset()
        {
            SyncSliders(2100, 250, 100);
            bool gpu = PowerControl.ResetGpu();     // unlock beats locking to 1650
            bool cpu = PowerControl.ApplyCpuLimit(100);
            AppendLog((gpu && cpu ? "[OK]" : "[ERR]") +
                " 🔥 游戏模式: GPU 解锁 / 250W, CPU 100%");
            PowerControl.LogEvent("game", "gpu=unlock,250W cpu=100%");
            SetTrayMode("game", "🔥 游戏");
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
            SetTrayMode("custom", "⚪ 自定义");
        }

        private void ResetDefaults()
        {
            SyncSliders(2100, 250, 100);
            bool gpu = PowerControl.ResetGpu();
            bool cpu = PowerControl.ResetCpu();
            AppendLog((gpu && cpu ? "[OK]" : "[ERR]") + " 已恢复默认: GPU 解锁, CPU 100%");
            PowerControl.LogEvent("reset", "all unlocked");
            SetTrayMode("default", "默认");
        }

        private void SyncSliders(int clk, int watt, int cpu)
        {
            _syncing = true;
            _gpuClock.Value = Clamp(_gpuClock, clk);
            _gpuWatt.Value = Clamp(_gpuWatt, watt);
            _cpuMax.Value = Clamp(_cpuMax, cpu);
            _syncing = false;
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
                _tray.Text = string.Format("Genshin Tweak | {0} | ❄ 已时停", _modeName);
            }
            else
            {
                _pauseStatus.Text = "▶ 游戏未运行 / 未时停";
                _pauseStatus.ForeColor = OkColor();
                _pauseBtn.Text = "切换时停 (Alt+`)";
                _tray.Text = string.Format("Genshin Tweak | {0} | ▶ 运行中", _modeName);
            }
        }

        private static Color WarnColor() { return Color.FromArgb(243, 139, 168); }
        private static Color OkColor() { return Color.FromArgb(166, 227, 161); }

        // ====================================================================
        // helpers
        // ====================================================================
        // may be called from the volume hotkey worker thread -> marshal to UI
        private void AppendLog(string msg)
        {
            if (_logBox == null || _logBox.IsDisposed) return;
            if (InvokeRequired)
            {
                try { BeginInvoke(new Action<string>(AppendLog), msg); } catch { }
                return;
            }
            string line = string.Format("[{0:HH:mm:ss}] {1}\r\n", DateTime.Now, msg);
            if (_logBox.TextLength > 32000) _logBox.Clear();
            _logBox.AppendText(line);
        }

        private void CpuTimerTick(object sender, EventArgs e)
        {
            double cpu = PowerControl.GetCpuUsagePercent();
            if (cpu >= 0) _cpuLabel.Text = string.Format("CPU: {0:0}%", cpu);
        }

        // icon priority: managed resource (csc /resource) -> exe win32 icon -> system
        private Icon LoadAppIcon()
        {
            try
            {
                System.Reflection.Assembly asm = System.Reflection.Assembly.GetExecutingAssembly();
                using (System.IO.Stream s = asm.GetManifestResourceStream("app.ico"))
                {
                    if (s != null) return new Icon(s);
                }
            }
            catch { }
            _iconFallback = true;
            try { return System.Drawing.Icon.ExtractAssociatedIcon(Application.ExecutablePath); }
            catch { }
            return SystemIcons.Application;
        }

        private void LoadModeIcons()
        {
            _modeIcons = new Dictionary<string, Icon>();
            string[] keys = { "default", "video", "work", "game", "custom" };
            foreach (string key in keys)
            {
                try
                {
                    System.Reflection.Assembly asm = System.Reflection.Assembly.GetExecutingAssembly();
                    using (System.IO.Stream s = asm.GetManifestResourceStream("tray_" + key + ".ico"))
                    {
                        if (s != null) _modeIcons[key] = new Icon(s);
                    }
                }
                catch { }
            }
        }

        private void SetTrayMode(string key, string label)
        {
            _modeKey = key;
            _modeName = label;
            Icon mi;
            if (_modeIcons != null && _modeIcons.TryGetValue(key, out mi) && mi != null)
                _tray.Icon = mi;
            else
                _tray.Icon = (_appIcon != null) ? _appIcon : SystemIcons.Application;
            HighlightPreset(key);
            UpdateTrayChecks(key);
            UpdatePauseUi();
            SaveWindowState();
        }

        private void HighlightPreset(string key)
        {
            SetBtnMode(_btnVideo,  key == "video",  ModeGreen);
            SetBtnMode(_btnWork,   key == "work",   ModeBlue);
            SetBtnMode(_btnGame,   key == "game",   ModeOrange);
            SetBtnMode(_btnCustom, key == "custom", ModeWhite);
        }

        private static void SetBtnMode(Button b, bool on, Color c)
        {
            if (b == null) return;
            b.BackColor = on ? c : SystemColors.Control;
            b.ForeColor = on ? Color.FromArgb(24, 24, 32) : SystemColors.ControlText;
        }

        private void UpdateTrayChecks(string key)
        {
            if (_miVideo   != null) _miVideo.Checked   = (key == "video");
            if (_miWork    != null) _miWork.Checked    = (key == "work");
            if (_miGame    != null) _miGame.Checked    = (key == "game");
            if (_miCustom  != null) _miCustom.Checked  = (key == "custom");
            if (_miDefault != null) _miDefault.Checked = (key == "default");
        }

        // user tweaked sliders after picking a mode -> becomes "custom"
        private void MarkCustom()
        {
            if (_modeKey == "custom") return;
            SetTrayMode("custom", "⚪ 自定义");
            PowerControl.LogEvent("custom-state", "manual tweak");
        }

        // ---- window state persistence (default: maximized) ----
        private static string SettingsPath()
        {
            return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "GenshinTweak", "ui.ini");
        }

        private void LoadWindowStatePrefs()
        {
            try
            {
                string path = SettingsPath();
                if (!File.Exists(path)) return;          // no file -> default maximized
                string state = "max";
                int x = 0, y = 0, w = 0, h = 0;
                foreach (string ln in File.ReadAllLines(path))
                {
                    int i = ln.IndexOf('=');
                    if (i <= 0) continue;
                    string k = ln.Substring(0, i).Trim();
                    string v = ln.Substring(i + 1).Trim();
                    if (k == "state") state = v;
                    else if (k == "x") int.TryParse(v, out x);
                    else if (k == "y") int.TryParse(v, out y);
                    else if (k == "w") int.TryParse(v, out w);
                    else if (k == "h") int.TryParse(v, out h);
                    else if (k == "mode") _savedMode = v;
                    else if (k == "gpu") int.TryParse(v, out _savedGpu);
                    else if (k == "watt") int.TryParse(v, out _savedWatt);
                    else if (k == "cpu") int.TryParse(v, out _savedCpu);
                    else if (k == "vol") _savedVolEnabled = (v != "0");
                    else if (k == "volstep") int.TryParse(v, out _savedVolStep);
                    else if (k == "volmute") _savedVolMute = v;
                    else if (k == "volup") _savedVolUp = v;
                    else if (k == "voldown") _savedVolDown = v;
                }
                if (state == "normal" && w > 200 && h > 200)
                {
                    // never restore a window too small for the stacked panels
                    // (an old ui.ini would otherwise squeeze the log to one line)
                    if (w < MinimumSize.Width) w = MinimumSize.Width;
                    if (h < MinimumSize.Height) h = MinimumSize.Height;
                    Rectangle vs = SystemInformation.VirtualScreen;
                    if (x < vs.Left - 40 || x > vs.Right - 80) x = vs.Left + 40;
                    if (y < vs.Top) y = vs.Top;
                    StartPosition = FormStartPosition.Manual;
                    Bounds = new Rectangle(x, y, w, h);
                    WindowState = FormWindowState.Normal;
                }
            }
            catch { }
        }

        private void SaveWindowState()
        {
            if (!_uiReady) return;   // ctor resizes must not overwrite the file
            try
            {
                if (WindowState == FormWindowState.Minimized) return;
                string path = SettingsPath();
                string dir = Path.GetDirectoryName(path);
                if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
                string profile = "";
                if (_gpuClock != null && _gpuWatt != null && _cpuMax != null)
                {
                    profile = string.Format("mode={0}\r\ngpu={1}\r\nwatt={2}\r\ncpu={3}\r\n",
                        _modeKey, _gpuClock.Value, _gpuWatt.Value, _cpuMax.Value);
                }
                if (_volEnabled != null && _volStep != null &&
                    _volMuteKey != null && _volUpKey != null && _volDownKey != null)
                {
                    profile += string.Format(
                        "vol={0}\r\nvolstep={1}\r\nvolmute={2}\r\nvolup={3}\r\nvoldown={4}\r\n",
                        _volEnabled.Checked ? "1" : "0", _volStep.Value,
                        _volMuteKey.Text, _volUpKey.Text, _volDownKey.Text);
                }
                File.WriteAllText(path, string.Format(
                    "state={0}\r\nx={1}\r\ny={2}\r\nw={3}\r\nh={4}\r\n{5}",
                    (WindowState == FormWindowState.Maximized) ? "max" : "normal",
                    Location.X, Location.Y, Width, Height, profile));
            }
            catch { }
        }

        protected override void OnResize(EventArgs e)
        {
            base.OnResize(e);
            SaveWindowState();
        }

        protected override void OnResizeEnd(EventArgs e)
        {
            base.OnResizeEnd(e);
            SaveWindowState();
        }

        // re-apply the last-used mode + custom slider values from ui.ini
        private void RestoreProfilePrefs()
        {
            if (_savedMode == "") return;
            if (_savedGpu > 0 && _savedWatt > 0 && _savedCpu > 0)
            {
                _syncing = true;
                _gpuClock.Value = Clamp(_gpuClock, _savedGpu);
                _gpuWatt.Value = Clamp(_gpuWatt, _savedWatt);
                _cpuMax.Value = Clamp(_cpuMax, _savedCpu);
                _syncing = false;
            }
            if (_savedMode == "video") { ApplyVideoPreset(); }
            else if (_savedMode == "work") { ApplyWorkPreset(); }
            else if (_savedMode == "game") { ApplyGamePreset(); }
            else if (_savedMode == "custom")
            {
                if (_savedGpu > 0 && _savedWatt > 0 && _savedCpu > 0)
                {
                    bool gpu = PowerControl.ApplyGpuLimit(_gpuClock.Value, _gpuWatt.Value);
                    bool cpu = PowerControl.ApplyCpuLimit(_cpuMax.Value);
                    AppendLog((gpu && cpu ? "[OK]" : "[ERR]") + " 恢复上次自定义: GPU "
                        + _gpuClock.Value + "MHz / " + _gpuWatt.Value + "W, CPU " + _cpuMax.Value + "%");
                }
                SetTrayMode("custom", "⚪ 自定义");
            }
            else if (_savedMode == "default") { SetTrayMode("default", "默认"); }
        }

        private void ShowWindow()
        {
            if (!Visible) Show();
            WindowState = _preHideState;   // restore state from before hiding
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
            try
            {
                VolumeKeys.Uninstall();
                VolumeHud.Close();
                if (_volTimer != null) _volTimer.Stop();
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

        protected override void OnShown(EventArgs e)
        {
            base.OnShown(e);
            // The log lives in the percent row: its final height is only known
            // after the window is shown, so re-dock the box explicitly.
            ReDockLogBox();
        }

        private void ReDockLogBox()
        {
            if (_logBox != null && _logBox.Parent != null) _logBox.Parent.PerformLayout();
        }

        protected override void OnLoad(EventArgs e)
        {
            PauseController.StateChanged += UpdatePauseUi;
            base.OnLoad(e);
        }

        // X button hides to tray (real exit only from tray menu)
        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            SaveWindowState();
            CancelHotkeyCapture();
            if (e.CloseReason == CloseReason.UserClosing && !_reallyExiting)
            {
                e.Cancel = true;
                _preHideState = (WindowState == FormWindowState.Minimized)
                    ? FormWindowState.Normal : WindowState;
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
                if (_appIcon != null && !ReferenceEquals(_appIcon, SystemIcons.Application))
                {
                    try { _appIcon.Dispose(); } catch { }
                }
                if (_modeIcons != null)
                {
                    foreach (KeyValuePair<string, Icon> kv in _modeIcons)
                    {
                        if (kv.Value != null) { try { kv.Value.Dispose(); } catch { } }
                    }
                }
                try { VolumeKeys.Uninstall(); } catch { }
                try { VolumeHud.Close(); } catch { }
                if (_volTimer != null) { try { _volTimer.Dispose(); } catch { } }
                if (_volCaptureTimeout != null) { try { _volCaptureTimeout.Dispose(); } catch { } }
                if (_volTip != null) { try { _volTip.Dispose(); } catch { } }
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
