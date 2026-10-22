// ============================================================================
// VolumeKeys.cs — global hotkeys for the foreground application volume
//
// Merged from the "Volume Controller" project (volume_controller.py):
//   - Pause       -> toggle mute of the focused application   (default)
//   - Ctrl+Alt+Up -> volume up, hold to repeat
//   - Ctrl+Alt+Down -> volume down, hold to repeat
//   - a low-level keyboard hook captures the keys even inside
//     elevated games (UIPI would block a non-elevated listener)
//
// The hook is installed once (WH_KEYBOARD_LL) and always alive while the app
// runs; `Enabled` only gates the actions. Hotkey capture (the small text
// boxes in the main window) rides on the same hook, so Pause / PrintScreen
// can be captured even though WinForms' KeyDown never sees them.
//
// C# 5 ONLY (csc.exe 4.0.30319).
// ============================================================================

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace GenshinTweak
{
    // A normalized hotkey: modifiers + exactly one trigger key (Win32 VK).
    internal struct Hotkey
    {
        public bool Ctrl;
        public bool Alt;
        public bool Shift;
        public bool Win;
        public int Vk;

        public bool IsEmpty
        {
            get { return Vk <= 0; }
        }

        public static Hotkey None()
        {
            Hotkey empty = new Hotkey();
            empty.Vk = 0;
            return empty;
        }

        public static Hotkey Make(bool ctrl, bool alt, bool shift, bool win, int vk)
        {
            Hotkey h = new Hotkey();
            h.Ctrl = ctrl;
            h.Alt = alt;
            h.Shift = shift;
            h.Win = win;
            h.Vk = vk;
            return h;
        }

        // From a WinForms KeyEventArgs.KeyData (settings UI fallback).
        public static Hotkey FromKeyData(Keys keyData)
        {
            Hotkey h = new Hotkey();
            Keys key = keyData & Keys.KeyCode;
            if (key == Keys.None) return h;
            if (key == Keys.ControlKey || key == Keys.ShiftKey || key == Keys.Menu ||
                key == Keys.LWin || key == Keys.RWin) return h;
            h.Ctrl = (keyData & Keys.Control) == Keys.Control;
            h.Alt = (keyData & Keys.Alt) == Keys.Alt;
            h.Shift = (keyData & Keys.Shift) == Keys.Shift;
            h.Vk = (int)key;
            return h;
        }

        public static string KeyName(int vk)
        {
            if (vk <= 0) return "";
            string name = ((Keys)vk).ToString();
            if (name.Length == 0 || name == "None") return "VK" + vk.ToString("X2");
            return name;
        }

        // "Pause", "Ctrl+Alt+Up", "Ctrl+Shift+F10", "" (disabled)
        public static Hotkey Parse(string text)
        {
            Hotkey h = new Hotkey();
            if (text == null) return h;
            string[] parts = text.Split('+');
            for (int i = 0; i < parts.Length; i++)
            {
                string part = parts[i].Trim();
                if (part.Length == 0) continue;
                string low = part.ToLowerInvariant();
                if (low == "ctrl" || low == "control") { h.Ctrl = true; continue; }
                if (low == "alt") { h.Alt = true; continue; }
                if (low == "shift") { h.Shift = true; continue; }
                if (low == "win" || low == "windows") { h.Win = true; continue; }
                int vk = VkFromName(part);
                if (vk > 0) h.Vk = vk;
            }
            return h;
        }

        private static int VkFromName(string name)
        {
            string low = name.ToLowerInvariant();
            if (low == "pause" || low == "break") return (int)Keys.Pause;
            if (low == "printscreen" || low == "print screen" || low == "prtsc") return (int)Keys.PrintScreen;
            if (low == "esc" || low == "escape") return (int)Keys.Escape;
            if (low == "del" || low == "delete") return (int)Keys.Delete;
            if (low == "ins" || low == "insert") return (int)Keys.Insert;
            if (low == "pgup" || low == "pageup") return (int)Keys.PageUp;
            if (low == "pgdn" || low == "pagedown") return (int)Keys.PageDown;
            if (low == "space") return (int)Keys.Space;
            try
            {
                object parsed = Enum.Parse(typeof(Keys), name, true);
                int vk = (int)parsed;
                if (vk > 0 && vk != (int)Keys.Modifiers) return vk;
            }
            catch { }
            return 0;
        }

        public override string ToString()
        {
            if (IsEmpty) return "";
            StringBuilder sb = new StringBuilder();
            if (Ctrl) sb.Append("Ctrl+");
            if (Alt) sb.Append("Alt+");
            if (Shift) sb.Append("Shift+");
            if (Win) sb.Append("Win+");
            sb.Append(KeyName(Vk));
            return sb.ToString();
        }
    }

    internal static class VolumeKeys
    {
        // Wired up by MainForm; both run on a worker thread.
        public static Action<int> Step;   // +1 / -1
        public static Action Mute;

        // ---- Win32 keyboard hook ----------------------------------------
        private const int WH_KEYBOARD_LL = 13;
        private const int WM_KEYDOWN = 0x0100;
        private const int WM_KEYUP = 0x0101;
        private const int WM_SYSKEYDOWN = 0x0104;
        private const int WM_SYSKEYUP = 0x0105;

        private const int VK_TAB = 0x09;
        private const int VK_SHIFT = 0x10;
        private const int VK_CONTROL = 0x11;
        private const int VK_MENU = 0x12;
        private const int VK_ESCAPE = 0x1B;
        private const int VK_LWIN = 0x5B;
        private const int VK_RWIN = 0x5C;
        private const int VK_LSHIFT = 0xA0;
        private const int VK_RSHIFT = 0xA1;
        private const int VK_LCONTROL = 0xA2;
        private const int VK_RCONTROL = 0xA3;
        private const int VK_LMENU = 0xA4;
        private const int VK_RMENU = 0xA5;

        private const int REPEAT_DELAY_MS = 250;     // hold this long -> start repeating
        private const int REPEAT_INTERVAL_MS = 50;   // 20 steps / second

        [StructLayout(LayoutKind.Sequential)]
        private struct KBDLLHOOKSTRUCT
        {
            public int vkCode;
            public int scanCode;
            public int flags;
            public int time;
            public IntPtr dwExtraInfo;
        }

        private delegate IntPtr LowLevelKeyboardProc(int nCode, IntPtr wParam, IntPtr lParam);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern IntPtr SetWindowsHookEx(int idHook, LowLevelKeyboardProc lpfn, IntPtr hMod, uint dwThreadId);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool UnhookWindowsHookEx(IntPtr hhk);

        [DllImport("user32.dll")]
        private static extern IntPtr CallNextHookEx(IntPtr hhk, int nCode, IntPtr wParam, IntPtr lParam);

        [DllImport("kernel32.dll", CharSet = CharSet.Auto)]
        private static extern IntPtr GetModuleHandle(string lpModuleName);

        // ---- state -------------------------------------------------------
        private static IntPtr _hook = IntPtr.Zero;
        private static LowLevelKeyboardProc _hookProc;

        private static readonly HashSet<int> _pressed = new HashSet<int>();
        private static readonly HashSet<int> _swallowed = new HashSet<int>();

        private static Hotkey _muteKey = Hotkey.None();
        private static Hotkey _upKey = Hotkey.None();
        private static Hotkey _downKey = Hotkey.None();

        private static bool _enabled = true;
        private static Control _ui;
        private static Action<Hotkey> _capture;

        private static ManualResetEvent _repeatStop;
        private static int _repeatDir;
        private static readonly object _repeatSync = new object();

        public static bool Enabled
        {
            get { return _enabled; }
            set { _enabled = value; }
        }

        public static bool IsCapturing
        {
            get { return _capture != null; }
        }

        public static void Attach(Control ui)
        {
            _ui = ui;
        }

        public static void SetHotkeys(Hotkey mute, Hotkey up, Hotkey down)
        {
            _muteKey = mute;
            _upKey = up;
            _downKey = down;
        }

        // ---- lifecycle ---------------------------------------------------
        public static bool Install()
        {
            if (_hook != IntPtr.Zero) return true;
            _pressed.Clear();
            _swallowed.Clear();
            _hookProc = new LowLevelKeyboardProc(HookCallback);
            try
            {
                using (Process current = Process.GetCurrentProcess())
                using (ProcessModule module = current.MainModule)
                {
                    _hook = SetWindowsHookEx(WH_KEYBOARD_LL, _hookProc,
                        GetModuleHandle(module.ModuleName), 0);
                }
            }
            catch
            {
                _hook = IntPtr.Zero;
            }
            if (_hook == IntPtr.Zero) _hookProc = null;
            return _hook != IntPtr.Zero;
        }

        public static void Uninstall()
        {
            StopRepeat();
            _capture = null;
            if (_hook != IntPtr.Zero)
            {
                try { UnhookWindowsHookEx(_hook); } catch { }
                _hook = IntPtr.Zero;
            }
            _hookProc = null;
            _pressed.Clear();
            _swallowed.Clear();
        }

        // ---- capture mode (settings UI) ----------------------------------
        public static void BeginCapture(Action<Hotkey> onCaptured)
        {
            StopRepeat();
            _pressed.Clear();
            _swallowed.Clear();
            _capture = onCaptured;
        }

        public static void CancelCapture()
        {
            _capture = null;
            _pressed.Clear();
            _swallowed.Clear();
        }

        // ---- hook body ----------------------------------------------------
        private static IntPtr HookCallback(int nCode, IntPtr wParam, IntPtr lParam)
        {
            if (nCode < 0) return CallNextHookEx(_hook, nCode, wParam, lParam);

            bool swallow = false;
            try
            {
                int message = wParam.ToInt32();
                bool isDown = (message == WM_KEYDOWN || message == WM_SYSKEYDOWN);
                bool isUp = (message == WM_KEYUP || message == WM_SYSKEYUP);
                if (isDown || isUp)
                {
                    KBDLLHOOKSTRUCT info = (KBDLLHOOKSTRUCT)Marshal.PtrToStructure(lParam, typeof(KBDLLHOOKSTRUCT));
                    swallow = HandleKey(info.vkCode, isDown);
                }
            }
            catch { }

            return swallow ? (IntPtr)1 : CallNextHookEx(_hook, nCode, wParam, lParam);
        }

        private static bool HandleKey(int vk, bool down)
        {
            if (_capture != null) return HandleCapture(vk, down);

            if (!down)
            {
                bool wasSwallowed = _swallowed.Remove(vk);
                _pressed.Remove(vk);
                UpdateRepeat();
                return wasSwallowed;
            }

            bool fresh = !_pressed.Contains(vk);
            _pressed.Add(vk);
            if (!_enabled) return false;

            bool hitMute = IsTrigger(_muteKey, vk) && ComboDown(_muteKey);
            bool hitUp = IsTrigger(_upKey, vk) && ComboDown(_upKey);
            bool hitDown = IsTrigger(_downKey, vk) && ComboDown(_downKey);

            if (!hitMute && !hitUp && !hitDown) return false;

            _swallowed.Add(vk);
            if (!fresh) return true;                 // auto-repeat: keep swallowing only

            if (hitMute) PostMute();
            else if (hitUp) StartRepeat(1);
            else if (hitDown) StartRepeat(-1);
            return true;
        }

        private static bool HandleCapture(int vk, bool down)
        {
            if (!down)
            {
                _pressed.Remove(vk);
                // pass the matching key-up through for keys we never swallowed
                return !(vk == VK_TAB || vk == VK_LWIN || vk == VK_RWIN || vk == VK_ESCAPE);
            }
            if (vk == VK_TAB) return false;          // let focus leave the box
            if (vk == VK_LWIN || vk == VK_RWIN) return false;   // let Win/Start escape
            if (vk == VK_ESCAPE)
            {
                CancelCapture();
                return false;
            }

            _pressed.Add(vk);
            Hotkey picked = HotkeyFromPressed(vk);
            if (picked.IsEmpty) return true;         // modifier only: keep waiting

            Action<Hotkey> callback = _capture;
            _capture = null;
            _pressed.Clear();
            _swallowed.Clear();
            if (callback != null) PostToUi(callback, picked);
            return true;
        }

        private static void PostToUi(Action<Hotkey> callback, Hotkey picked)
        {
            Control ui = _ui;
            if (ui == null)
            {
                try { callback(picked); } catch { }
                return;
            }
            try
            {
                ui.BeginInvoke(new Action(delegate
                {
                    try { callback(picked); } catch { }
                }));
            }
            catch { }
        }

        private static Hotkey HotkeyFromPressed(int vk)
        {
            if (vk == VK_CONTROL || vk == VK_LCONTROL || vk == VK_RCONTROL) return Hotkey.None();
            if (vk == VK_SHIFT || vk == VK_LSHIFT || vk == VK_RSHIFT) return Hotkey.None();
            if (vk == VK_MENU || vk == VK_LMENU || vk == VK_RMENU) return Hotkey.None();
            if (vk == VK_LWIN || vk == VK_RWIN) return Hotkey.None();

            Hotkey h = new Hotkey();
            h.Vk = vk;
            h.Ctrl = AnyPressed(VK_CONTROL, VK_LCONTROL, VK_RCONTROL);
            h.Alt = AnyPressed(VK_MENU, VK_LMENU, VK_RMENU);
            h.Shift = AnyPressed(VK_SHIFT, VK_LSHIFT, VK_RSHIFT);
            h.Win = AnyPressed(VK_LWIN, VK_RWIN);
            return h;
        }

        // ---- matching helpers ---------------------------------------------
        private static bool IsTrigger(Hotkey key, int vk)
        {
            return !key.IsEmpty && key.Vk == vk;
        }

        private static bool ComboDown(Hotkey key)
        {
            if (key.IsEmpty) return false;
            if (!_pressed.Contains(key.Vk)) return false;

            bool ctrlDown = AnyPressed(VK_CONTROL, VK_LCONTROL, VK_RCONTROL);
            bool altDown = AnyPressed(VK_MENU, VK_LMENU, VK_RMENU);
            bool shiftDown = AnyPressed(VK_SHIFT, VK_LSHIFT, VK_RSHIFT);
            bool winDown = AnyPressed(VK_LWIN, VK_RWIN);

            if (key.Ctrl != ctrlDown) return false;   // exact modifier match
            if (key.Alt != altDown) return false;
            if (key.Shift != shiftDown) return false;
            if (key.Win != winDown) return false;
            return true;
        }

        private static bool AnyPressed(int a)
        {
            return _pressed.Contains(a);
        }

        private static bool AnyPressed(int a, int b)
        {
            return _pressed.Contains(a) || _pressed.Contains(b);
        }

        private static bool AnyPressed(int a, int b, int c)
        {
            return _pressed.Contains(a) || _pressed.Contains(b) || _pressed.Contains(c);
        }

        // ---- actions ------------------------------------------------------
        private static void PostMute()
        {
            Action handler = Mute;
            if (handler == null) return;
            ThreadPool.QueueUserWorkItem(delegate(object state)
            {
                try { handler(); } catch { }
            });
        }

        private static void StartRepeat(int dir)
        {
            StopRepeat();
            Action<int> handler = Step;
            if (handler == null) return;

            ManualResetEvent stop = new ManualResetEvent(false);
            lock (_repeatSync)
            {
                _repeatStop = stop;
                _repeatDir = dir;
            }
            Thread worker = new Thread(delegate()
            {
                try
                {
                    handler(dir);
                    if (stop.WaitOne(REPEAT_DELAY_MS)) return;
                    while (true)
                    {
                        handler(dir);
                        if (stop.WaitOne(REPEAT_INTERVAL_MS)) break;
                    }
                }
                catch { }
            });
            worker.IsBackground = true;
            worker.Start();
        }

        private static void StopRepeat()
        {
            ManualResetEvent stop;
            lock (_repeatSync)
            {
                stop = _repeatStop;
                _repeatStop = null;
                _repeatDir = 0;
            }
            if (stop != null)
            {
                try { stop.Set(); } catch { }
            }
        }

        // key released: stop, or switch direction when the other combo is held
        private static void UpdateRepeat()
        {
            int dir;
            lock (_repeatSync) { dir = _repeatDir; }
            if (dir == 0) return;

            bool upDown = ComboDown(_upKey);
            bool downDown = ComboDown(_downKey);
            if (dir > 0 && !upDown)
            {
                if (downDown) StartRepeat(-1); else StopRepeat();
            }
            else if (dir < 0 && !downDown)
            {
                if (upDown) StartRepeat(1); else StopRepeat();
            }
        }
    }
}
