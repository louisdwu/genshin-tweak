// ============================================================================
// CoreAudio.cs — per-application volume control (WASAPI / Core Audio)
//
// Merged from the "Volume Controller" project (volume_controller.py):
//   - control the volume of the CURRENT FOREGROUND application only
//   - toggle its mute state
//   - group multi-process apps (Chrome, Electron, ...) by process name
//
// IMPORTANT: the session graph is driven through raw vtable calls instead of
// ComImport interfaces. A derived ComImport interface (IAudioSessionManager2 :
// IAudioSessionManager) did not resolve to the native vtable slots on a real
// Windows box — GetSessionEnumerator landed on the neighbouring method and the
// audio service answered E_POINTER (0x80004003), so no session was ever found.
// Indexing the vtable ourselves (slot 3 = GetAudioSessionControl, 5 =
// GetSessionEnumerator, ...) removes the CLR interface-mapping question.
//
// Pure interop: no NuGet package, no Python, no external DLL. csc.exe can build
// it straight from source.
//
// C# 5 ONLY (csc.exe 4.0.30319) — no $"", no ?., no nameof, no expression
// bodied members.
// ============================================================================

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace GenshinTweak
{
    // Result of a volume / mute operation, also used by the HUD.
    internal struct AudioStatus
    {
        public bool Ok;            // operation itself succeeded
        public bool HasAudio;      // an audio session for the app was found
        public string AppName;     // exe name of the foreground application
        public float Volume;       // 0.0 .. 1.0
        public bool Muted;
        public int SessionCount;   // audio sessions controlled by this operation
        public bool UsedPreviousApp; // our own window was focused -> last other app
        public string AvailableHint; // when nothing matched: sessions that do exist
        public string Error;       // null when Ok
    }

    internal enum AudioAction
    {
        Query = 0,
        Adjust = 1,
        ToggleMute = 2
    }

    internal static class CoreAudio
    {
        // ---- Win32 -------------------------------------------------------
        [DllImport("user32.dll")]
        private static extern IntPtr GetForegroundWindow();

        [DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

        [DllImport("ole32.dll")]
        private static extern int CoInitializeEx(IntPtr pvReserved, uint dwCoInit);

        [DllImport("ole32.dll")]
        private static extern void CoUninitialize();

        private const uint COINIT_APARTMENTTHREADED = 0x2;
        private const int RPC_E_CHANGED_MODE = unchecked((int)0x80010106);
        private const uint CLSCTX_ALL = 23;
        private const int DEVICE_STATE_ACTIVE = 0x1;

        // ---- device enumerator (plain RCW; these calls are vtable-stable) --
        [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
        private class MMDeviceEnumeratorComObject
        {
        }

        [ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"),
         InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IMMDeviceEnumerator
        {
            [PreserveSig] int EnumAudioEndpoints(int dataFlow, int stateMask, out IMMDeviceCollection devices);
            [PreserveSig] int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice endpoint);
            [PreserveSig] int GetDevice([MarshalAs(UnmanagedType.LPWStr)] string id, out IMMDevice device);
            [PreserveSig] int RegisterEndpointNotificationCallback(IntPtr client);
            [PreserveSig] int UnregisterEndpointNotificationCallback(IntPtr client);
        }

        [ComImport, Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"),
         InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IMMDeviceCollection
        {
            [PreserveSig] int GetCount(out int count);
            [PreserveSig] int Item(int index, out IMMDevice device);
        }

        [ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"),
         InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IMMDevice
        {
            // raw IUnknown** out; the returned pointer's vtable belongs to
            // IAudioSessionManager2 (the IID we ask for), so we index it by hand
            [PreserveSig] int Activate(ref Guid iid, uint dwClsCtx, IntPtr pActivationParams, out IntPtr ppInterface);
            [PreserveSig] int OpenPropertyStore(uint stgmAccess, out IntPtr properties);
            [PreserveSig] int GetId(out IntPtr id);
            [PreserveSig] int GetState(out uint state);
        }

        // ---- raw Core Audio vtables ---------------------------------------
        private static readonly Guid IID_IAudioSessionManager2 = new Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F");
        private static readonly Guid IID_IAudioSessionControl2 = new Guid("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D");
        private static readonly Guid IID_ISimpleAudioVolume = new Guid("87CE5498-68D6-44E5-9215-6DA47EF883D8");

        // IUnknown occupies slots 0..2 in every COM vtable
        private const int SLOT_GET_SESSION_ENUMERATOR = 5;   // IAudioSessionManager2 (3,4 = v1 methods)
        private const int SLOT_GET_COUNT = 3;                // IAudioSessionEnumerator
        private const int SLOT_GET_SESSION = 4;              // IAudioSessionEnumerator
        private const int SLOT_SET_MASTER_VOLUME = 3;        // ISimpleAudioVolume
        private const int SLOT_GET_MASTER_VOLUME = 4;
        private const int SLOT_SET_MUTE = 5;
        private const int SLOT_GET_MUTE = 6;
        private const int SLOT_GET_PROCESS_ID = 14;          // IAudioSessionControl2 (3..11 = control v1)

        [UnmanagedFunctionPointer(CallingConvention.Winapi)]
        private delegate int FnGetSessionEnumerator(IntPtr self, out IntPtr sessionEnum);

        [UnmanagedFunctionPointer(CallingConvention.Winapi)]
        private delegate int FnGetCount(IntPtr self, out int count);

        [UnmanagedFunctionPointer(CallingConvention.Winapi)]
        private delegate int FnGetSession(IntPtr self, int index, out IntPtr control);

        [UnmanagedFunctionPointer(CallingConvention.Winapi)]
        private delegate int FnGetProcessId(IntPtr self, out uint processId);

        [UnmanagedFunctionPointer(CallingConvention.Winapi)]
        private delegate int FnSetMasterVolume(IntPtr self, float level, ref Guid eventContext);

        [UnmanagedFunctionPointer(CallingConvention.Winapi)]
        private delegate int FnGetMasterVolume(IntPtr self, out float level);

        [UnmanagedFunctionPointer(CallingConvention.Winapi)]
        private delegate int FnSetMute(IntPtr self, [MarshalAs(UnmanagedType.Bool)] bool mute, ref Guid eventContext);

        [UnmanagedFunctionPointer(CallingConvention.Winapi)]
        private delegate int FnGetMute(IntPtr self, [MarshalAs(UnmanagedType.Bool)] out bool mute);

        private static IntPtr VtableSlot(IntPtr unknown, int slot)
        {
            IntPtr vtable = Marshal.ReadIntPtr(unknown);
            return Marshal.ReadIntPtr(vtable, slot * IntPtr.Size);
        }

        private static T Fn<T>(IntPtr unknown, int slot) where T : class
        {
            return Marshal.GetDelegateForFunctionPointer(VtableSlot(unknown, slot), typeof(T)) as T;
        }

        // ---- session facade -------------------------------------------------
        private struct Session
        {
            public IntPtr Control;   // IAudioSessionControl2*
            public IntPtr Volume;    // ISimpleAudioVolume*
            public uint Pid;
            public string ProcessName;
        }

        private static int SessionGetMute(IntPtr volume, out bool muted)
        {
            return Fn<FnGetMute>(volume, SLOT_GET_MUTE)(volume, out muted);
        }

        private static int SessionSetMute(IntPtr volume, bool mute)
        {
            Guid context = Guid.Empty;
            return Fn<FnSetMute>(volume, SLOT_SET_MUTE)(volume, mute, ref context);
        }

        private static int SessionGetVolume(IntPtr volume, out float level)
        {
            return Fn<FnGetMasterVolume>(volume, SLOT_GET_MASTER_VOLUME)(volume, out level);
        }

        private static int SessionSetVolume(IntPtr volume, float level)
        {
            Guid context = Guid.Empty;
            return Fn<FnSetMasterVolume>(volume, SLOT_SET_MASTER_VOLUME)(volume, level, ref context);
        }

        // ====================================================================
        // public API
        // ====================================================================
        public static AudioStatus AdjustForeground(float delta)
        {
            return Run(AudioAction.Adjust, delta);
        }

        public static AudioStatus ToggleForegroundMute()
        {
            return Run(AudioAction.ToggleMute, 0f);
        }

        public static AudioStatus QueryForeground()
        {
            return Run(AudioAction.Query, 0f);
        }

        // One-shot diagnostic used at startup: enumerate everything and describe it.
        public static string ProbeAudio()
        {
            bool mustUninitialize;
            if (!EnterCom(out mustUninitialize)) return "COM 初始化失败";

            List<Session> sessions = new List<Session>();
            try
            {
                EnumerateSessions(sessions);
                return DescribeSessions(sessions);
            }
            finally
            {
                ReleaseSessions(sessions);
                if (mustUninitialize) CoUninitialize();
            }
        }

        // Diagnostics of the last enumeration (devices, raw sessions, failure)
        public static string LastEnumSummary
        {
            get
            {
                string s = "输出设备 " + _lastDeviceCount + " 个 · 音频会话 "
                    + _lastRawSessionCount + " 个（含系统声音）";
                if (_lastEnumError != null) s += " · " + _lastEnumError;
                return s;
            }
        }

        // ====================================================================
        // internals
        // ====================================================================
        private static AudioStatus Run(AudioAction action, float delta)
        {
            AudioStatus st = new AudioStatus();
            st.Ok = false;
            st.HasAudio = false;
            st.AppName = "";
            st.Volume = 0f;
            st.Muted = false;
            st.SessionCount = 0;

            bool mustUninitialize;
            if (!EnterCom(out mustUninitialize))
            {
                st.Error = "COM 初始化失败";
                return st;
            }

            List<Session> sessions = new List<Session>();
            try
            {
                uint pid;
                st.AppName = ForegroundApp(out pid, out st.UsedPreviousApp);
                if (pid == 0 || st.AppName.Length == 0)
                {
                    // no usable foreground window: nothing to control, not an error
                    st.Ok = true;
                    st.HasAudio = false;
                    return st;
                }

                EnumerateSessions(sessions);
                List<Session> targets = MatchSessions(sessions, pid, st.AppName);
                if (targets.Count == 0)
                {
                    st.Ok = true;               // not an error: app is just silent
                    st.HasAudio = false;
                    st.AvailableHint = DescribeSessions(sessions);
                    return st;
                }

                float volume = 0f;
                bool muted = false;
                string error = null;
                st.SessionCount = targets.Count;

                if (action == AudioAction.ToggleMute)
                {
                    // "mute everything unless everything is already muted".
                    // Inverting each session separately left multi-process apps
                    // half muted, which sounds like "mute does not work".
                    bool anyReadable = false;
                    bool anyUnmuted = false;
                    for (int i = 0; i < targets.Count; i++)
                    {
                        bool current;
                        if (SessionGetMute(targets[i].Volume, out current) != 0) continue;
                        anyReadable = true;
                        if (!current) anyUnmuted = true;
                    }
                    bool desired = !anyReadable || anyUnmuted;

                    for (int i = 0; i < targets.Count; i++)
                    {
                        int hr = SessionSetMute(targets[i].Volume, desired);
                        if (hr != 0) error = "SetMute 0x" + hr.ToString("X8");
                    }

                    // read back so the HUD shows the real state
                    muted = desired;
                    bool readAny = false;
                    bool allMuted = true;
                    for (int i = 0; i < targets.Count; i++)
                    {
                        bool current;
                        if (SessionGetMute(targets[i].Volume, out current) != 0) continue;
                        readAny = true;
                        if (!current) allMuted = false;
                        float v;
                        if (SessionGetVolume(targets[i].Volume, out v) == 0 && v > volume) volume = v;
                    }
                    if (readAny) muted = allMuted;
                }
                else
                {
                    // adjust / query: the loudest session is the reference, so all
                    // processes of a multi-process app end up in sync
                    float current = -1f;
                    for (int i = 0; i < targets.Count; i++)
                    {
                        float v;
                        if (SessionGetVolume(targets[i].Volume, out v) != 0) continue;
                        if (v > current) current = v;
                    }
                    if (current < 0f) current = 0f;

                    float next = current;
                    if (action == AudioAction.Adjust)
                    {
                        next = current + delta;
                        if (next < 0f) next = 0f;
                        if (next > 1f) next = 1f;

                        for (int i = 0; i < targets.Count; i++)
                        {
                            int hr = SessionSetVolume(targets[i].Volume, next);
                            if (hr != 0) { error = "SetMasterVolume 0x" + hr.ToString("X8"); continue; }

                            // same as the Python original: raising volume un-mutes
                            if (delta > 0f)
                            {
                                bool isMuted;
                                if (SessionGetMute(targets[i].Volume, out isMuted) == 0 && isMuted)
                                    SessionSetMute(targets[i].Volume, false);
                            }
                        }
                    }

                    volume = next;
                    muted = true;
                    bool readAny = false;
                    for (int i = 0; i < targets.Count; i++)
                    {
                        bool m;
                        if (SessionGetMute(targets[i].Volume, out m) != 0) continue;
                        readAny = true;
                        if (!m) muted = false;
                    }
                    if (!readAny) muted = false;
                }

                st.Volume = volume;
                st.Muted = muted;
                st.HasAudio = true;
                st.Ok = (error == null);
                st.Error = error;
            }
            catch (Exception ex)
            {
                st.Error = ex.Message;
                st.Ok = false;
            }
            finally
            {
                ReleaseSessions(sessions);
                if (mustUninitialize) CoUninitialize();
            }
            return st;
        }

        private static void ReleaseSessions(List<Session> sessions)
        {
            for (int i = 0; i < sessions.Count; i++)
            {
                try
                {
                    if (sessions[i].Control != IntPtr.Zero) Marshal.Release(sessions[i].Control);
                    if (sessions[i].Volume != IntPtr.Zero) Marshal.Release(sessions[i].Volume);
                }
                catch { }
            }
        }

        private static bool EnterCom(out bool mustUninitialize)
        {
            mustUninitialize = false;
            try
            {
                int hr = CoInitializeEx(IntPtr.Zero, COINIT_APARTMENTTHREADED);
                if (hr == RPC_E_CHANGED_MODE) return true;   // already in an MTA: usable as-is
                if (hr < 0) return false;
                mustUninitialize = true;                     // S_OK / S_FALSE: balanced below
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static void Release(object com)
        {
            if (com == null) return;
            try
            {
                if (Marshal.IsComObject(com)) Marshal.ReleaseComObject(com);
            }
            catch { }
        }

        // Last foreground app that was not this tool. When the user clicks the
        // tool window and presses the hotkey, that app is still what they mean.
        private static readonly object LastAppLock = new object();
        private static uint _lastAppPid;
        private static string _lastAppName = "";
        private static DateTime _lastAppAt = DateTime.MinValue;
        private static int _selfPid = -1;

        private static int SelfPid
        {
            get
            {
                if (_selfPid < 0)
                {
                    try
                    {
                        using (Process self = Process.GetCurrentProcess()) _selfPid = self.Id;
                    }
                    catch { _selfPid = 0; }
                }
                return _selfPid;
            }
        }

        private static string ForegroundApp(out uint pid, out bool usedPrevious)
        {
            usedPrevious = false;
            pid = 0;
            IntPtr hwnd = GetForegroundWindow();
            if (hwnd == IntPtr.Zero) return "";
            GetWindowThreadProcessId(hwnd, out pid);
            string name = ProcessNameOf(pid);
            if (pid == 0) return name;

            if (pid != (uint)SelfPid)
            {
                lock (LastAppLock)
                {
                    _lastAppPid = pid;
                    _lastAppName = name;
                    _lastAppAt = DateTime.Now;
                }
                return name;
            }

            lock (LastAppLock)
            {
                // 10 minutes is enough for "I just left that window"
                if (_lastAppPid != 0 && (DateTime.Now - _lastAppAt).TotalMinutes <= 10.0)
                {
                    pid = _lastAppPid;
                    usedPrevious = true;
                    return _lastAppName;
                }
            }
            return name;
        }

        private static string ProcessNameOf(uint pid)
        {
            if (pid == 0) return "";
            try
            {
                using (Process p = Process.GetProcessById((int)pid))
                {
                    string name = p.ProcessName;
                    if (name.Length == 0) return "";
                    if (!name.EndsWith(".exe", StringComparison.OrdinalIgnoreCase)) name += ".exe";
                    return name;
                }
            }
            catch { return ""; }
        }

        // ---- enumeration ----------------------------------------------------
        private static int _lastDeviceCount;
        private static int _lastRawSessionCount;
        private static string _lastEnumError;

        // Every active render endpoint -> session manager -> every session.
        // Games often pick a non-default output device (headset vs HDMI), and
        // WASAPI keeps sessions per endpoint.
        private static void EnumerateSessions(List<Session> output)
        {
            _lastDeviceCount = 0;
            _lastRawSessionCount = 0;
            _lastEnumError = null;

            IMMDeviceEnumerator deviceEnumerator = null;
            IMMDeviceCollection devices = null;
            try
            {
                deviceEnumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();

                // eRender = 0, DEVICE_STATE_ACTIVE = 0x1
                int hr = deviceEnumerator.EnumAudioEndpoints(0, DEVICE_STATE_ACTIVE, out devices);
                if (hr != 0 || devices == null)
                {
                    _lastEnumError = "EnumAudioEndpoints 失败 0x" + hr.ToString("X8");
                    IMMDevice fallback = null;
                    if (deviceEnumerator.GetDefaultAudioEndpoint(0, 0, out fallback) == 0 && fallback != null)
                    {
                        _lastDeviceCount = 1;
                        EnumerateDeviceSessions(fallback, output);
                        Release(fallback);
                    }
                    return;
                }

                int deviceCount;
                hr = devices.GetCount(out deviceCount);
                if (hr != 0)
                {
                    _lastEnumError = "设备计数失败 0x" + hr.ToString("X8");
                    return;
                }
                _lastDeviceCount = deviceCount;

                for (int i = 0; i < deviceCount; i++)
                {
                    IMMDevice device;
                    if (devices.Item(i, out device) != 0 || device == null) continue;
                    EnumerateDeviceSessions(device, output);
                    Release(device);
                }
            }
            catch (Exception ex)
            {
                _lastEnumError = "异常 " + ex.GetType().Name + ": " + ex.Message;
            }
            finally
            {
                Release(devices);
                Release(deviceEnumerator);
            }
        }

        private static void EnumerateDeviceSessions(IMMDevice device, List<Session> output)
        {
            IntPtr manager = IntPtr.Zero;
            IntPtr enumerator = IntPtr.Zero;
            try
            {
                Guid iid = IID_IAudioSessionManager2;
                int hr = device.Activate(ref iid, CLSCTX_ALL, IntPtr.Zero, out manager);
                if (hr != 0 || manager == IntPtr.Zero)
                {
                    if (_lastEnumError == null) _lastEnumError = "Activate 失败 0x" + hr.ToString("X8");
                    return;
                }

                hr = Fn<FnGetSessionEnumerator>(manager, SLOT_GET_SESSION_ENUMERATOR)(manager, out enumerator);
                if (hr != 0 || enumerator == IntPtr.Zero)
                {
                    if (_lastEnumError == null) _lastEnumError = "GetSessionEnumerator 失败 0x" + hr.ToString("X8");
                    return;
                }

                int count;
                hr = Fn<FnGetCount>(enumerator, SLOT_GET_COUNT)(enumerator, out count);
                if (hr != 0)
                {
                    if (_lastEnumError == null) _lastEnumError = "会话计数失败 0x" + hr.ToString("X8");
                    return;
                }

                for (int i = 0; i < count; i++)
                {
                    IntPtr control;
                    if (Fn<FnGetSession>(enumerator, SLOT_GET_SESSION)(enumerator, i, out control) != 0
                        || control == IntPtr.Zero) continue;
                    _lastRawSessionCount++;      // includes system sounds

                    IntPtr control2;
                    Guid iidControl2 = IID_IAudioSessionControl2;
                    if (Marshal.QueryInterface(control, ref iidControl2, out control2) != 0
                        || control2 == IntPtr.Zero)
                    {
                        Marshal.Release(control);
                        continue;
                    }

                    uint pid;
                    if (Fn<FnGetProcessId>(control2, SLOT_GET_PROCESS_ID)(control2, out pid) != 0 || pid == 0)
                    {
                        Marshal.Release(control2);      // system sounds / stale session
                        Marshal.Release(control);
                        continue;
                    }

                    IntPtr volume;
                    Guid iidVolume = IID_ISimpleAudioVolume;
                    if (Marshal.QueryInterface(control, ref iidVolume, out volume) != 0
                        || volume == IntPtr.Zero)
                    {
                        Marshal.Release(control2);
                        Marshal.Release(control);
                        continue;
                    }
                    Marshal.Release(control);            // control2 + volume hold their own refs

                    Session session = new Session();
                    session.Control = control2;
                    session.Volume = volume;
                    session.Pid = pid;
                    // an empty name is fine: matching by PID still works, and
                    // protected (anti-cheat) processes may refuse the lookup
                    session.ProcessName = ProcessNameOf(pid);
                    output.Add(session);
                }
            }
            catch (Exception ex)
            {
                if (_lastEnumError == null) _lastEnumError = "异常 " + ex.GetType().Name + ": " + ex.Message;
            }
            finally
            {
                if (enumerator != IntPtr.Zero) { try { Marshal.Release(enumerator); } catch { } }
                if (manager != IntPtr.Zero) { try { Marshal.Release(manager); } catch { } }
            }
        }

        // 1. exact PID match
        // 2. fall back to process-name match (Chrome / Electron: the focused
        //    browser process owns no audio, the renderer children do)
        private static List<Session> MatchSessions(List<Session> all, uint pid, string appName)
        {
            List<Session> hit = new List<Session>();
            for (int i = 0; i < all.Count; i++)
            {
                if (all[i].Pid == pid) hit.Add(all[i]);
            }
            if (hit.Count == 0 && appName.Length > 0)
            {
                for (int i = 0; i < all.Count; i++)
                {
                    if (string.Equals(all[i].ProcessName, appName, StringComparison.OrdinalIgnoreCase))
                        hit.Add(all[i]);
                }
            }
            return hit;
        }

        // "chrome.exe、Spotify.exe、pid 1234…" — shown in the log when nothing
        // matched, so a device mismatch / exclusive-mode stream is obvious
        private static string DescribeSessions(List<Session> sessions)
        {
            string head = LastEnumSummary;
            if (sessions.Count == 0) return head + "（没有任何应用音频会话）";

            StringBuilder sb = new StringBuilder();
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            int shown = 0;
            for (int i = 0; i < sessions.Count; i++)
            {
                string label = sessions[i].ProcessName.Length > 0
                    ? sessions[i].ProcessName
                    : "pid " + sessions[i].Pid;
                if (!seen.Add(label)) continue;
                if (shown > 0) sb.Append("、");
                sb.Append(label);
                shown++;
                if (shown >= 6) { sb.Append("…"); break; }
            }
            return head + "：" + (sb.Length == 0 ? "（无法读取会话信息）" : sb.ToString());
        }
    }
}
