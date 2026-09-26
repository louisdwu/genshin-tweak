# =============================================================
# Genshin Impact Window-Drag Pause
# 热键：Alt+~ 切换"时停/恢复"
# 原理：
#   冻结：发送 Alt+Enter 切窗口模式 → PostMessage WM_NCLBUTTONDOWN(HTCAPTION)
#         让 Windows 进入窗口移动模态循环，游戏渲染/逻辑线程被 DefWindowProc 卡住
#   恢复：PostMessage WM_CANCELMODE → 退出模态循环 → 可选 Alt+Enter 切回全屏
# 优势：
#   - 不需要管理员权限
#   - 不操作进程线程，更安全
#   - 利用 Windows 原生机制
# =============================================================

# -- 自检权限：原神因反作弊以较高权限运行，UIPI 会拦截低权限进程的 PostMessage --
# 非管理员时自动以管理员身份重启
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

$ErrorActionPreference = "Stop"
$LogFile = "$PSScriptRoot\window_pause.log"
Set-Content -Path $LogFile -Value "" -Encoding UTF8 -ErrorAction SilentlyContinue

function Write-Log {
    param([string]$Msg)
    $line = "[$(Get-Date -Format 'HH:mm:ss')] $Msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

try {
    Write-Log "Script starting..."

    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    # -- Win32 API helpers ------------------------------------------
    Write-Log "Compiling Win32Helper..."
    try {
        Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class Win32Helper {
    // 窗口消息
    public const uint WM_NCLBUTTONDOWN = 0x00A1;
    public const uint WM_CANCELMODE    = 0x001F;
    public const uint WM_KEYDOWN       = 0x0100;
    public const int  HTCAPTION        = 2;

    // 窗口样式
    public const int GWL_STYLE      = -16;
    public const int GWL_EXSTYLE    = -20;
    public const uint WS_CAPTION    = 0x00C00000;  // WS_BORDER | WS_DLGFRAME

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    public static extern uint GetWindowLong(IntPtr hWnd, int nIndex);

    [DllImport("user32.dll")]
    public static extern bool GetClientRect(IntPtr hWnd, out RECT lpRect);

    [DllImport("user32.dll")]
    public static extern bool IsWindow(IntPtr hWnd);

    // 模拟键盘（用于 Alt+Enter）
    [DllImport("user32.dll")]
    public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);

    public const byte VK_MENU   = 0x12; // Alt
    public const byte VK_RETURN = 0x0D; // Enter
    public const uint KEYEVENTF_KEYUP = 0x0002;

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT {
        public int Left, Top, Right, Bottom;
        public int Width  { get { return Right  - Left; } }
        public int Height { get { return Bottom - Top;  } }
    }

    // 判断标题栏是否**可见**：
    //   - 真正的窗口模式：有 WS_CAPTION 且 wr.Top >= 0
    //   - 无边框全屏：有 WS_CAPTION 但 wr.Top ≈ -30（标题栏藏在屏幕上方）→ 返回 false
    //   - 独占全屏：无 WS_CAPTION → 返回 false
    public static bool HasVisibleTitleBar(IntPtr hWnd) {
        uint style = GetWindowLong(hWnd, GWL_STYLE);
        if ((style & WS_CAPTION) != WS_CAPTION) return false;
        RECT wr;
        if (!GetWindowRect(hWnd, out wr)) return false;
        return wr.Top >= -10;  // 容差 10px，排除无边框全屏（Top 通常 ≈ -30）
    }

    // 向前台窗口发送 Alt+Enter（切换全屏/窗口）
    public static void SendAltEnter() {
        keybd_event(VK_MENU,   0, 0, UIntPtr.Zero);
        keybd_event(VK_RETURN, 0, 0, UIntPtr.Zero);
        keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
        keybd_event(VK_MENU,   0, KEYEVENTF_KEYUP, UIntPtr.Zero);
    }

    // WM_SYSCOMMAND + SC_MOVE：让窗口进入"键盘移动"模态循环
    // 等同于右键标题栏 → 选择"移动(M)"
    // DefWindowProc 会阻塞该窗口的线程，直到收到 ESC / Enter / 鼠标点击
    // 比 WM_NCLBUTTONDOWN 更可靠：不依赖物理鼠标按钮状态
    public const uint WM_SYSCOMMAND = 0x0112;
    public const uint SC_MOVE       = 0xF010;
    public const byte VK_ESCAPE     = 0x1B;

    public static bool PostSysMove(IntPtr hWnd, out int lastError) {
        lastError = 0;
        bool ok = PostMessage(hWnd, WM_SYSCOMMAND, (IntPtr)SC_MOVE, IntPtr.Zero);
        if (!ok) lastError = Marshal.GetLastWin32Error();
        return ok;
    }

    // 发送 ESC 键退出移动模式
    public static void SendEscapeToWindow(IntPtr hWnd) {
        PostMessage(hWnd, WM_KEYDOWN, (IntPtr)VK_ESCAPE, IntPtr.Zero);
    }

    // WM_CANCELMODE 退出移动/调整模态循环（备用）
    public static bool PostCancelMode(IntPtr hWnd) {
        return PostMessage(hWnd, WM_CANCELMODE, IntPtr.Zero, IntPtr.Zero);
    }
}
"@
        Write-Log "Win32Helper compiled OK"
    } catch {
        if ($_.Exception.Message -notmatch 'already exists') { throw }
        Write-Log "Win32Helper: type already loaded, skipping."
    }

    # -- Hotkey host form -------------------------------------------
    Write-Log "Compiling HotkeyForm..."
    try {
        Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Windows.Forms;

public class HotkeyForm : Form {
    [DllImport("user32.dll")]
    public static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);
    [DllImport("user32.dll")]
    public static extern bool UnregisterHotKey(IntPtr hWnd, int id);

    public const int  WM_HOTKEY = 0x0312;
    public const uint MOD_ALT   = 0x0001;
    public const uint VK_OEM_3  = 0x00C0;  // ~ 键

    public event Action<int> HotkeyFired;

    public bool BindHotkey(int id, uint mod, uint vk) {
        return RegisterHotKey(this.Handle, id, mod, vk);
    }
    public void ReleaseHotkey(int id) {
        UnregisterHotKey(this.Handle, id);
    }
    protected override void WndProc(ref Message m) {
        if (m.Msg == WM_HOTKEY && HotkeyFired != null)
            HotkeyFired(m.WParam.ToInt32());
        base.WndProc(ref m);
    }
}
"@ -ReferencedAssemblies "System.Windows.Forms"
        Write-Log "HotkeyForm compiled OK"
    } catch {
        if ($_.Exception.Message -notmatch 'already exists') { throw }
        Write-Log "HotkeyForm: type already loaded, skipping."
    }

    # -- Toast OSD --------------------------------------------------
    Write-Log "Compiling PauseToast..."
    try {
        Add-Type @"
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

public class PauseToast {
    [DllImport("user32.dll")]
    private static extern bool SetLayeredWindowAttributes(IntPtr hwnd, uint crKey, byte bAlpha, uint dwFlags);
    private const int  WS_EX_NOACTIVATE = 0x08000000;
    private const int  WS_EX_TOOLWINDOW = 0x00000080;
    private const int  WS_EX_LAYERED    = 0x00080000;
    private const uint LWA_ALPHA        = 0x00000002;

    private sealed class PumpForm : Form {
        protected override bool ShowWithoutActivation { get { return true; } }
        protected override CreateParams CreateParams {
            get {
                CreateParams cp = base.CreateParams;
                cp.ExStyle |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW;
                return cp;
            }
        }
    }

    private sealed class ToastForm : Form {
        protected override bool ShowWithoutActivation { get { return true; } }
        protected override CreateParams CreateParams {
            get {
                CreateParams cp = base.CreateParams;
                cp.ExStyle |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_LAYERED;
                return cp;
            }
        }
    }

    private static PumpForm  _pump  = null;
    private static ToastForm _toast = null;
    private static System.Windows.Forms.Timer _timer = null;
    private static Font _fBold = null;
    private static Font _fReg  = null;
    private static System.Windows.Forms.Timer _swTimer = null;
    private static DateTime _swStart;
    private static Label _lblSw = null;

    public static void Init() {
        var ready = new ManualResetEventSlim(false);
        var t = new Thread(() => {
            _pump = new PumpForm();
            _pump.FormBorderStyle = FormBorderStyle.None;
            _pump.ShowInTaskbar   = false;
            _pump.Opacity         = 0;
            _pump.Size            = new Size(1, 1);
            _pump.Location        = new System.Drawing.Point(-200, -200);
            _pump.Load += (s, e) => {
                _fBold = new Font("Segoe UI", 13f, FontStyle.Bold,    GraphicsUnit.Point);
                _fReg  = new Font("Segoe UI", 11f, FontStyle.Regular, GraphicsUnit.Point);
                ready.Set();
            };
            Application.Run(_pump);
        });
        t.IsBackground = true;
        t.SetApartmentState(ApartmentState.STA);
        t.Start();
        ready.Wait(4000);
    }

    public static void Show(string line1, string line2, bool isWarning, int durationMs) {
        if (_pump == null || _pump.IsDisposed) return;
        try { _pump.BeginInvoke(new Action(() => ShowOnPump(line1, line2, isWarning, durationMs))); }
        catch {}
    }

    private static void ShowOnPump(string line1, string line2, bool isWarning, int durationMs) {
        if (_timer != null) { _timer.Stop(); _timer.Dispose(); _timer = null; }
        if (_swTimer != null) { _swTimer.Stop(); _swTimer.Dispose(); _swTimer = null; }
        _lblSw = null;
        if (_toast != null && !_toast.IsDisposed) { try { _toast.Close(); _toast.Dispose(); } catch {} }
        _toast = null;

        Color bgColor     = Color.FromArgb(28, 24, 37);
        Color borderColor = isWarning ? Color.FromArgb(243, 139, 168) : Color.FromArgb(137, 220, 235);
        Color fgColor2    = Color.FromArgb(200, borderColor.R, borderColor.G, borderColor.B);

        ToastForm win = new ToastForm();
        win.FormBorderStyle = FormBorderStyle.None;
        win.StartPosition   = FormStartPosition.Manual;
        win.TopMost         = true;
        win.ShowInTaskbar   = false;
        win.BackColor       = bgColor;

        Panel panel = new Panel();
        panel.BackColor = bgColor;
        panel.Dock      = DockStyle.Fill;

        Label lbl1 = new Label();
        lbl1.Text      = line1;
        lbl1.Font      = _fBold;
        lbl1.ForeColor = borderColor;
        lbl1.BackColor = bgColor;
        lbl1.AutoSize  = true;
        lbl1.Location  = new System.Drawing.Point(32, 14);
        panel.Controls.Add(lbl1);

        Label lbl2 = null;
        if (!string.IsNullOrEmpty(line2)) {
            lbl2 = new Label();
            lbl2.Text      = line2;
            lbl2.Font      = _fReg;
            lbl2.ForeColor = fgColor2;
            lbl2.BackColor = bgColor;
            lbl2.AutoSize  = true;
            lbl2.Location  = new System.Drawing.Point(32, 14 + lbl1.PreferredHeight + 6);
            panel.Controls.Add(lbl2);
        }

        Label lblSw = null;
        if (durationMs == 0) {
            _swStart = DateTime.Now;
            lblSw = new Label();
            lblSw.Font = _fBold;
            lblSw.ForeColor = Color.FromArgb(166, 227, 161);
            lblSw.BackColor = bgColor;
            lblSw.AutoSize = true;
            lblSw.Text = "00:00";
            lblSw.Location = new System.Drawing.Point(32 + lbl1.PreferredWidth + 20, 14);
            panel.Controls.Add(lblSw);
            _lblSw = lblSw;
        }

        win.Controls.Add(panel);

        int swReservedW = 0;
        if (lblSw != null) {
            using (var g = Graphics.FromHwnd(IntPtr.Zero)) {
                swReservedW = 20 + (int)Math.Ceiling(g.MeasureString("00:00:00", _fBold).Width);
            }
        }
        int labelW = Math.Max(lbl1.PreferredWidth + swReservedW, lbl2 != null ? lbl2.PreferredWidth : 0);
        int labelH = lbl1.PreferredHeight + (lbl2 != null ? 6 + lbl2.PreferredHeight : 0);
        int w = labelW + 64 + 4;
        int h = labelH + 28 + 4;

        Screen scr = Screen.PrimaryScreen;
        win.Size     = new Size(w, h);
        win.Location = new System.Drawing.Point((scr.Bounds.Width - w) / 2, 70);

        win.Load  += (s, e) => SetLayeredWindowAttributes(win.Handle, 0, 238, LWA_ALPHA);
        win.Paint += (s, e) => {
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            using (Pen pen = new Pen(borderColor, 2f))
                e.Graphics.DrawRectangle(pen, 1, 1, w - 3, h - 3);
        };

        _toast = win;
        win.Show();

        if (durationMs == 0 && lblSw != null) {
            var swt = new System.Windows.Forms.Timer();
            swt.Interval = 1000;
            swt.Tick += (s, e) => {
                if (win.IsDisposed) { swt.Stop(); swt.Dispose(); return; }
                var el = DateTime.Now - _swStart;
                lblSw.Text = el.TotalHours >= 1
                    ? el.ToString(@"h\:mm\:ss")
                    : el.ToString(@"mm\:ss");
            };
            _swTimer = swt;
            swt.Start();
        }

        if (durationMs > 0) {
            _timer = new System.Windows.Forms.Timer();
            _timer.Interval = durationMs;
            _timer.Tick += (s, e) => {
                _timer.Stop(); _timer.Dispose(); _timer = null;
                if (!win.IsDisposed) { win.Close(); win.Dispose(); }
                _toast = null;
            };
            _timer.Start();
        }
    }
}
"@ -ReferencedAssemblies "System.Windows.Forms", "System.Drawing"
        Write-Log "PauseToast compiled OK"
    } catch {
        if ($_.Exception.Message -notmatch 'already exists') { throw }
        Write-Log "PauseToast: type already loaded, skipping."
    }

    # -- 初始化 Toast 泵线程 ----------------------------------------
    [PauseToast]::Init()
    Write-Log "PauseToast pump ready"

    # -- 状态 -------------------------------------------------------
    $script:IsPaused      = $false
    $script:GenshinHwnd   = [IntPtr]::Zero
    # 暂停前游戏是否为全屏，用于恢复时决定是否再发 Alt+Enter
    $script:WasFullscreen = $false

    # 原神进程名（国服 YuanShen，国际服 GenshinImpact）
    $GenshinProcessNames  = @("YuanShen", "GenshinImpact")
    # 原神主窗口标题（用于 FindWindow 快速路径）
    $GenshinWindowTitles  = @("原神", "Genshin Impact")

    # -- Helpers ----------------------------------------------------
    function Get-GenshinHwnd {
        # 快速路径：按窗口标题查找
        foreach ($title in $GenshinWindowTitles) {
            $hwnd = [Win32Helper]::FindWindow($null, $title)
            if ($hwnd -ne [IntPtr]::Zero -and [Win32Helper]::IsWindow($hwnd)) {
                Write-Log "FindWindow by title '$title' -> $hwnd"
                return $hwnd
            }
        }
        # 兜底路径：按进程名取 MainWindowHandle
        # 独占全屏下窗口标题可能为空或与预期不符
        foreach ($name in $GenshinProcessNames) {
            $proc = Get-Process -Name $name -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($proc -and $proc.MainWindowHandle -ne [IntPtr]::Zero) {
                Write-Log "Found via process '$name' (pid=$($proc.Id)) MainWindowHandle=$($proc.MainWindowHandle)"
                return $proc.MainWindowHandle
            }
        }
        return [IntPtr]::Zero
    }

    function Show-Toast {
        param([string]$Title, [string]$Subtitle = "", [bool]$IsWarning = $false, [int]$Duration = 3500)
        $statusText = if ($script:IsPaused) { "[PAUSED]" } else { "[RUNNING]" }
        $script:Tray.Text = "Genshin Pause | $statusText"
        [PauseToast]::Show($Title, $Subtitle, $IsWarning, $Duration)
        Write-Log "$Title | $Subtitle"
    }

    function Invoke-Pause {
        $hwnd = Get-GenshinHwnd
        if ($hwnd -eq [IntPtr]::Zero) {
            Show-Toast -Title "未找到原神窗口" -Subtitle "请确认游戏正在运行" -IsWarning $true -Duration 4500
            return
        }
        $script:GenshinHwnd = $hwnd

        # 检测是否有可见标题栏
        # HasVisibleTitleBar 同时排除：独占全屏（无 WS_CAPTION）和无边框全屏（Top ≈ -30）
        $hasTitleBar = [Win32Helper]::HasVisibleTitleBar($hwnd)
        $script:WasFullscreen = -not $hasTitleBar

        if ($script:WasFullscreen) {
            Write-Log "Game is fullscreen, sending Alt+Enter to switch to windowed..."
            [Win32Helper]::SetForegroundWindow($hwnd) | Out-Null
            Start-Sleep -Milliseconds 200
            [Win32Helper]::SendAltEnter()
            # 等待标题栏出现（最多 3 秒）
            $waited = 0
            while (-not [Win32Helper]::HasVisibleTitleBar($hwnd) -and $waited -lt 3000) {
                Start-Sleep -Milliseconds 100
                $waited += 100
            }
            if (-not [Win32Helper]::HasVisibleTitleBar($hwnd)) {
                Show-Toast -Title "切换窗口模式失败" -Subtitle "Alt+Enter 未生效，请手动切换" -IsWarning $true -Duration 4500
                Write-Log "ERROR: Failed to switch to windowed mode after ${waited}ms"
                return
            }
            Write-Log "Switched to windowed mode after ${waited}ms"
            Start-Sleep -Milliseconds 300  # 等窗口完全稳定

            # 切换后游戏可能重建了窗口，重新获取句柄
            $newHwnd = Get-GenshinHwnd
            if ($newHwnd -ne [IntPtr]::Zero) {
                Write-Log "Re-fetched hwnd after mode switch: $newHwnd (was $hwnd)"
                $hwnd = $newHwnd
                $script:GenshinHwnd = $hwnd
            }
        }

        # WM_SYSCOMMAND SC_MOVE → 进入窗口移动模态循环（等同右键标题栏→“移动”）
        $lastErr = 0
        $ok = [Win32Helper]::PostSysMove($hwnd, [ref]$lastErr)
        if ($ok) {
            $script:IsPaused = $true
            $script:PauseStartTime = Get-Date
            Show-Toast -Title "游戏已时停 ❄" -Subtitle "Alt+~ 恢复运行" -IsWarning $true -Duration 0
            Write-Log "PostSysMove(SC_MOVE) OK -> game paused (hwnd=$hwnd)"
        } else {
            Show-Toast -Title "时停失败" -Subtitle "Win32 错误码: $lastErr" -IsWarning $true -Duration 4500
            Write-Log "ERROR: PostSysMove failed, hwnd=$hwnd, LastError=$lastErr"
        }
    }

    function Invoke-Resume {
        $hwnd = $script:GenshinHwnd
        if ($hwnd -eq [IntPtr]::Zero -or -not [Win32Helper]::IsWindow($hwnd)) {
            $hwnd = Get-GenshinHwnd
        }
        if ($hwnd -eq [IntPtr]::Zero) {
            Show-Toast -Title "未找到原神窗口" -Subtitle "游戏可能已关闭" -IsWarning $true -Duration 4500
            $script:IsPaused = $false
            return
        }

        # 发送 ESC 退出移动模式 + WM_CANCELMODE 备用
        [Win32Helper]::SendEscapeToWindow($hwnd)
        [Win32Helper]::PostCancelMode($hwnd) | Out-Null
        Write-Log "Sent ESC + CancelMode to exit move mode"

        # 如果暂停前是全屏，切回全屏
        if ($script:WasFullscreen) {
            [Win32Helper]::SetForegroundWindow($hwnd) | Out-Null
            Start-Sleep -Milliseconds 200
            [Win32Helper]::SendAltEnter()
            Write-Log "Sent Alt+Enter to restore fullscreen"
        }

        $script:IsPaused = $false
        $elapsed = ""
        if ($script:PauseStartTime) {
            $ts = (Get-Date) - $script:PauseStartTime
            if ($ts.TotalHours -ge 1) {
                $elapsed = "暂停了 {0}:{1:D2}:{2:D2}" -f [int]$ts.TotalHours, $ts.Minutes, $ts.Seconds
            } else {
                $elapsed = "暂停了 {0:D2}:{1:D2}" -f $ts.Minutes, $ts.Seconds
            }
            $elapsed = "$elapsed · "
        }
        Show-Toast -Title "游戏已恢复 ▶" -Subtitle "${elapsed}Alt+~ 再次时停" -IsWarning $false -Duration 3500
    }

    function Toggle-Pause {
        if ($script:IsPaused) {
            Invoke-Resume
        } else {
            Invoke-Pause
        }
    }

    # -- 托盘图标 ---------------------------------------------------
    $script:Tray         = New-Object System.Windows.Forms.NotifyIcon
    $script:Tray.Icon    = [System.Drawing.SystemIcons]::Information
    $script:Tray.Text    = "Genshin Pause | [RUNNING]"
    $script:Tray.Visible = $true

    $menu      = New-Object System.Windows.Forms.ContextMenuStrip
    $miPause   = New-Object System.Windows.Forms.ToolStripMenuItem "时停 Pause"
    $miResume  = New-Object System.Windows.Forms.ToolStripMenuItem "恢复 Resume"
    $miToggle  = New-Object System.Windows.Forms.ToolStripMenuItem "切换 Toggle (Alt+~)"
    $miSep     = New-Object System.Windows.Forms.ToolStripSeparator
    $miExit    = New-Object System.Windows.Forms.ToolStripMenuItem "退出 Exit"

    $miPause.Add_Click({  Invoke-Pause  })
    $miResume.Add_Click({ Invoke-Resume })
    $miToggle.Add_Click({ Toggle-Pause  })
    $miExit.Add_Click({
        Write-Log "Exit via menu"
        if ($script:IsPaused) { Invoke-Resume }
        $script:Tray.Visible = $false
        $script:HostForm.ReleaseHotkey(1)
        [System.Windows.Forms.Application]::Exit()
    })

    $menu.Items.AddRange(@($miPause, $miResume, $miToggle, $miSep, $miExit))
    $script:Tray.ContextMenuStrip = $menu

    # -- Host form --------------------------------------------------
    $script:HostForm = New-Object HotkeyForm
    $script:HostForm.Text          = "GenshinWindowPause"
    $script:HostForm.ShowInTaskbar = $false
    $script:HostForm.WindowState   = [System.Windows.Forms.FormWindowState]::Minimized
    $script:HostForm.Width         = 1
    $script:HostForm.Height        = 1
    $script:HostForm.Opacity       = 0

    $script:HostForm.Add_Shown({
        $script:HostForm.Hide()
        $ok = $script:HostForm.BindHotkey(1, [HotkeyForm]::MOD_ALT, [HotkeyForm]::VK_OEM_3)
        if ($ok) {
            Write-Log "Hotkey Alt+~ registered OK"
            Show-Toast -Title "窗口拖拽时停" -Subtitle "Alt+~ 时停/恢复" -IsWarning $false -Duration 3000
        } else {
            Write-Log "WARNING: Alt+~ registration failed"
            Show-Toast -Title "热键注册失败" -Subtitle "Alt+~ 被其他程序占用" -IsWarning $true -Duration 4500
        }
    })

    $script:HostForm.add_HotkeyFired({
        param($id)
        Write-Log "HotkeyFired: id=$id, IsPaused=$($script:IsPaused)"
        if ($id -eq 1) { Toggle-Pause }
    })

    Write-Log "Entering Application::Run()..."
    [System.Windows.Forms.Application]::Run($script:HostForm)
    Write-Log "Application::Run() returned"

} catch {
    $errMsg = $_.Exception.ToString()
    Write-Log "FATAL: $errMsg"
    [System.Windows.Forms.MessageBox]::Show($errMsg, "Genshin Window Pause - Fatal Error",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
} finally {
    if ($script:Tray)     { $script:Tray.Visible = $false }
    if ($script:IsPaused) { Invoke-Resume }
    if ($script:HostForm) { try { $script:HostForm.ReleaseHotkey(1) } catch {} }
    Write-Log "Cleanup done."
}
