# =============================================================
# Genshin Impact Network Toggle
# Hotkey: Alt+` (tilde) -> Toggle block/allow
# =============================================================

# -- 自检权限：非管理员时自动以管理员身份重启自身 ----------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent())
              .IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

$ErrorActionPreference = "Stop"
$LogFile = "$PSScriptRoot\error.log"

function Write-Log {
    param([string]$Msg)
    $line = "[$(Get-Date -Format 'HH:mm:ss')] $Msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

try {
    Write-Log "Script starting..."

    Add-Type -AssemblyName System.Windows.Forms
    Write-Log "Loaded System.Windows.Forms"
    Add-Type -AssemblyName System.Drawing
    Write-Log "Loaded System.Drawing"

    # -- Firewall rule name -------------------------------------
    $RuleName       = "GenshinImpact_NetBlock_AGY"
    $GenshinExePath = "C:\Games\Genshin Impact Game\YuanShen.exe"

    # -- Compile GenshinHostForm (hotkey host) --------------------
    Write-Log "Compiling GenshinHostForm C# class..."
    try {
        Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Windows.Forms;

public class GenshinHostForm : Form {
    [DllImport("user32.dll")]
    public static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);
    [DllImport("user32.dll")]
    public static extern bool UnregisterHotKey(IntPtr hWnd, int id);

    public const int  WM_HOTKEY = 0x0312;
    public const uint MOD_ALT   = 0x0001;
    public const uint VK_OEM_3  = 0x00C0;

    public event Action<int> HotkeyFired;

    public bool BindHotkey(int id, uint mod, uint vk) {
        return RegisterHotKey(this.Handle, id, mod, vk);
    }
    public void ReleaseHotkey(int id) {
        UnregisterHotKey(this.Handle, id);
    }

    protected override void WndProc(ref Message m) {
        if (m.Msg == WM_HOTKEY) {
            if (HotkeyFired != null) HotkeyFired(m.WParam.ToInt32());
        }
        base.WndProc(ref m);
    }
}
"@ -ReferencedAssemblies "System.Windows.Forms"
        Write-Log "GenshinHostForm compiled OK"
    } catch {
        if ($_.Exception.Message -notmatch 'already exists') { throw }
        Write-Log "GenshinHostForm: type already loaded, skipping."
    }

    # -- Compile GenshinToast (screen OSD) -------------------------
    Write-Log "Compiling GenshinToast C# class..."
    try {
        Add-Type @"
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

public class GenshinToast {
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
        if (_toast != null && !_toast.IsDisposed) { try { _toast.Close(); _toast.Dispose(); } catch {} }
        _toast = null;

        Color bgColor     = Color.FromArgb(28, 24, 37);
        Color borderColor = isWarning ? Color.FromArgb(243, 139, 168) : Color.FromArgb(166, 227, 161);
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

        win.Controls.Add(panel);

        int labelW = Math.Max(lbl1.PreferredWidth, lbl2 != null ? lbl2.PreferredWidth : 0);
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
"@ -ReferencedAssemblies "System.Windows.Forms", "System.Drawing"
        Write-Log "GenshinToast compiled OK"
    } catch {
        if ($_.Exception.Message -notmatch 'already exists') { throw }
        Write-Log "GenshinToast: type already loaded, skipping."
    }

    # Pre-warm the STA pump thread so first Show() fires instantly
    Write-Log "Initializing GenshinToast pump thread..."
    [GenshinToast]::Init()
    Write-Log "GenshinToast pump ready"

    # -- Firewall helpers ---------------------------------------
    function Get-IsBlocked {
        return ($null -ne (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue))
    }

    function Show-Toast {
        param([string]$Title, [string]$Subtitle = "", [bool]$IsWarning = $false, [int]$Duration = 3500)
        $statusText = if ($script:IsBlocked) { "[BLOCKED]" } else { "[ONLINE]" }
        $script:Tray.Text = "Genshin Net Toggle | $statusText"
        [GenshinToast]::Show($Title, $Subtitle, $IsWarning, $Duration)
        Write-Log "$Title $Subtitle"
    }

    function Set-GenshinNetwork {
        param([bool]$Block)
        if (-not (Test-Path $GenshinExePath)) {
            Show-Toast -Title "Path not found" -Subtitle "YuanShen.exe missing" -IsWarning $true -Duration 4500
            Write-Log "ERROR: Path not found: $GenshinExePath"
            return
        }
        # Show toast immediately on hotkey press, before slow firewall operations
        if ($Block) {
            $script:IsBlocked = $true
            Show-Toast -Title "BLOCKED" -Subtitle "Alt+~ to restore" -IsWarning $true
        } else {
            $script:IsBlocked = $false
            Show-Toast -Title "ONLINE" -Subtitle "Alt+~ to block" -IsWarning $false
        }
        Remove-NetFirewallRule -DisplayName $RuleName        -ErrorAction SilentlyContinue
        Remove-NetFirewallRule -DisplayName "${RuleName}_In" -ErrorAction SilentlyContinue
        if ($Block) {
            New-NetFirewallRule -DisplayName $RuleName `
                -Direction Outbound -Program $GenshinExePath `
                -Action Block -Profile Any -Enabled True | Out-Null
            New-NetFirewallRule -DisplayName "${RuleName}_In" `
                -Direction Inbound -Program $GenshinExePath `
                -Action Block -Profile Any -Enabled True | Out-Null
        }
    }

    # -- Tray icon ----------------------------------------------
    Write-Log "Creating tray icon..."
    $script:IsBlocked = Get-IsBlocked
    $script:Tray      = New-Object System.Windows.Forms.NotifyIcon
    $script:Tray.Icon    = [System.Drawing.SystemIcons]::Shield
    $script:Tray.Text    = "Genshin Net Toggle | Initializing..."
    $script:Tray.Visible = $true
    Write-Log "Tray icon created"

    $menu       = New-Object System.Windows.Forms.ContextMenuStrip
    $miBlock    = New-Object System.Windows.Forms.ToolStripMenuItem "Block Network"
    $miAllow    = New-Object System.Windows.Forms.ToolStripMenuItem "Allow Network"
    $miToggle   = New-Object System.Windows.Forms.ToolStripMenuItem "Toggle  (Alt+~)"
    $miSep      = New-Object System.Windows.Forms.ToolStripSeparator
    $miExit     = New-Object System.Windows.Forms.ToolStripMenuItem "Exit"

    $miBlock.Add_Click({  Set-GenshinNetwork -Block $true  })
    $miAllow.Add_Click({  Set-GenshinNetwork -Block $false })
    $miToggle.Add_Click({ Set-GenshinNetwork -Block (-not (Get-IsBlocked)) })
    $miExit.Add_Click({
        Write-Log "Exit requested via menu"
        $script:Tray.Visible = $false
        $script:HostForm.ReleaseHotkey(1)
        Remove-NetFirewallRule -DisplayName $RuleName        -ErrorAction SilentlyContinue
        Remove-NetFirewallRule -DisplayName "${RuleName}_In" -ErrorAction SilentlyContinue
        [System.Windows.Forms.Application]::Exit()
    })

    $menu.Items.AddRange(@($miBlock, $miAllow, $miToggle, $miSep, $miExit))
    $script:Tray.ContextMenuStrip = $menu

    # -- Host form ----------------------------------------------
    Write-Log "Creating host form..."
    $script:HostForm = New-Object GenshinHostForm
    $script:HostForm.Text          = "GenshinNetToggle"
    $script:HostForm.ShowInTaskbar = $false
    $script:HostForm.WindowState   = [System.Windows.Forms.FormWindowState]::Minimized
    $script:HostForm.Width         = 1
    $script:HostForm.Height        = 1
    $script:HostForm.Opacity       = 0

    # Register hotkey after form handle is created (Shown event)
    $script:HostForm.Add_Shown({
        # Immediately hide - we just needed Shown to ensure Handle exists
        $script:HostForm.Hide()
        Write-Log "Registering Alt+~ hotkey..."
        $ok = $script:HostForm.BindHotkey(1, [GenshinHostForm]::MOD_ALT, [GenshinHostForm]::VK_OEM_3)
        if ($ok) {
            Write-Log "Hotkey Alt+~ registered OK"
            $statusText = if ($script:IsBlocked) { "[BLOCKED]" } else { "[ONLINE]" }
            $script:Tray.Text = "Genshin Net Toggle | $statusText"
            Show-Toast -Title "Net Toggle" -Subtitle "Alt+~ to toggle" -IsWarning $false -Duration 3000
        } else {
            Write-Log "WARNING: Alt+~ hotkey registration failed (key may be in use by another app)"
            Show-Toast -Title "Hotkey failed" -Subtitle "Alt+~ unavailable" -IsWarning $true -Duration 4500
        }

        # -- Sleep/wake watchdog (same approach as volume_controller.py) -----------
        # Every 5s, check if the elapsed time since last tick jumped > 10s.
        # A large jump means the system was suspended; re-register the hotkey.
        $script:WatchdogLast = [DateTime]::UtcNow
        $script:Watchdog = New-Object System.Windows.Forms.Timer
        $script:Watchdog.Interval = 5000
        $script:Watchdog.Add_Tick({
            $now     = [DateTime]::UtcNow
            $elapsed = ($now - $script:WatchdogLast).TotalSeconds
            $script:WatchdogLast = $now
            if ($elapsed -gt 10) {
                Write-Log "System wake detected (gap: $([Math]::Round($elapsed,1))s). Re-registering hotkey..."
                try { $script:HostForm.ReleaseHotkey(1) } catch {}
                Start-Sleep -Milliseconds 800   # brief delay for Win32 hook stack to settle
                $reOk = $script:HostForm.BindHotkey(1, [GenshinHostForm]::MOD_ALT, [GenshinHostForm]::VK_OEM_3)
                if ($reOk) {
                    Write-Log "Hotkey re-registered OK after wake"
                    Show-Toast -Title "Net Toggle" -Subtitle "Resumed after sleep" -IsWarning $false -Duration 3000
                } else {
                    Write-Log "WARNING: Hotkey re-registration failed after wake"
                    Show-Toast -Title "Hotkey lost" -Subtitle "Re-launch to restore" -IsWarning $true -Duration 4500
                }
            }
        })
        $script:Watchdog.Start()
        Write-Log "Sleep/wake watchdog started (interval: 5s, threshold: 10s)"
        # -------------------------------------------------------------------------
    })

    # Wire hotkey event -> toggle
    $script:HostForm.add_HotkeyFired({
        param($id)
        Write-Log "Hotkey fired: id=$id"
        if ($id -eq 1) {
            Set-GenshinNetwork -Block (-not (Get-IsBlocked))
        }
    })

    Write-Log "Entering Application::Run() message loop..."
    [System.Windows.Forms.Application]::Run($script:HostForm)
    Write-Log "Application::Run() returned - exiting"

} catch {
    $errMsg = $_.Exception.ToString()
    Write-Log "FATAL ERROR: $errMsg"
    [System.Windows.Forms.MessageBox]::Show($errMsg, "Genshin Net Toggle - Fatal Error", `
        [System.Windows.Forms.MessageBoxButtons]::OK, `
        [System.Windows.Forms.MessageBoxIcon]::Error) | Out-Null
} finally {
    if ($script:Tray)     { $script:Tray.Visible = $false }
    if ($script:HostForm) {
        try { $script:HostForm.ReleaseHotkey(1) } catch {}
    }
    Remove-NetFirewallRule -DisplayName $RuleName        -ErrorAction SilentlyContinue
    Remove-NetFirewallRule -DisplayName "${RuleName}_In" -ErrorAction SilentlyContinue
    Write-Log "Cleanup complete."
}
