// ============================================================================
// CoreAudio.cs — per-application volume control (WASAPI / Core Audio)
//
// Merged from the "Volume Controller" project (volume_controller.py):
//   - control the volume of the CURRENT FOREGROUND application only
//   - toggle its mute state
//   - group multi-process apps (Chrome, Electron, ...) by process name
//
// Pure COM interop against %WINDIR%\System32\Windows.Media.Devices style
// interfaces: no NuGet package, no Python, no external DLL. csc.exe can build
// it straight from source.
//
// C# 5 ONLY (csc.exe 4.0.30319) — no $"", no ?., no nameof, no expression
// bodied members.
// ============================================================================

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;

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

        // ---- COM interop -------------------------------------------------
        [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
        private class MMDeviceEnumeratorComObject
        {
        }

        [ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"),
         InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IMMDeviceEnumerator
        {
            [PreserveSig] int EnumAudioEndpoints(int dataFlow, int stateMask, out IntPtr devices);
            [PreserveSig] int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice endpoint);
            [PreserveSig] int GetDevice([MarshalAs(UnmanagedType.LPWStr)] string id, out IMMDevice device);
            [PreserveSig] int RegisterEndpointNotificationCallback(IntPtr client);
            [PreserveSig] int UnregisterEndpointNotificationCallback(IntPtr client);
        }

        [ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"),
         InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IMMDevice
        {
            [PreserveSig] int Activate(ref Guid iid, uint dwClsCtx, IntPtr pActivationParams,
                [MarshalAs(UnmanagedType.IUnknown)] out object ppInterface);
            [PreserveSig] int OpenPropertyStore(uint stgmAccess, out IntPtr properties);
            [PreserveSig] int GetId(out IntPtr id);
            [PreserveSig] int GetState(out uint state);
        }

        [ComImport, Guid("BFA971F1-4D5E-40BB-935E-967039BFBEE4"),
         InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IAudioSessionManager
        {
            [PreserveSig] int GetAudioSessionControl(ref Guid audioSessionGuid, uint streamFlags,
                out IAudioSessionControl sessionControl);
            [PreserveSig] int GetSimpleAudioVolume(ref Guid audioSessionGuid, uint streamFlags,
                out ISimpleAudioVolume audioVolume);
        }

        [ComImport, Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"),
         InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IAudioSessionManager2 : IAudioSessionManager
        {
            [PreserveSig] int GetSessionEnumerator(out IAudioSessionEnumerator sessionEnum);
            [PreserveSig] int RegisterSessionNotification(IntPtr sessionNotification);
            [PreserveSig] int UnregisterSessionNotification(IntPtr sessionNotification);
            [PreserveSig] int RegisterDuckNotification([MarshalAs(UnmanagedType.LPWStr)] string sessionId,
                IntPtr duckNotification);
            [PreserveSig] int UnregisterDuckNotification(IntPtr duckNotification);
        }

        [ComImport, Guid("E2F5BB11-0570-40CA-ACDD-3AA01277DEE8"),
         InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IAudioSessionEnumerator
        {
            [PreserveSig] int GetCount(out int sessionCount);
            [PreserveSig] int GetSession(int sessionIndex, out IAudioSessionControl session);
        }

        [ComImport, Guid("F4B1A599-7266-4319-A8CA-E70ACB11E8CD"),
         InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IAudioSessionControl
        {
            // vtable order matters — never call these, but they must be listed
            [PreserveSig] int GetState(out int state);
            [PreserveSig] int GetDisplayName(out IntPtr name);
            [PreserveSig] int SetDisplayName([MarshalAs(UnmanagedType.LPWStr)] string name, ref Guid eventContext);
            [PreserveSig] int GetIconPath(out IntPtr path);
            [PreserveSig] int SetIconPath([MarshalAs(UnmanagedType.LPWStr)] string path, ref Guid eventContext);
            [PreserveSig] int GetGroupingParam(out Guid groupingParam);
            [PreserveSig] int SetGroupingParam(ref Guid groupingParam, ref Guid eventContext);
            [PreserveSig] int RegisterAudioSessionNotification(IntPtr newNotifications);
            [PreserveSig] int UnregisterAudioSessionNotification(IntPtr newNotifications);
        }

        [ComImport, Guid("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D"),
         InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IAudioSessionControl2 : IAudioSessionControl
        {
            [PreserveSig] int GetSessionIdentifier(out IntPtr id);
            [PreserveSig] int GetSessionInstanceIdentifier(out IntPtr id);
            [PreserveSig] int GetProcessId(out uint processId);
            [PreserveSig] int IsSystemSoundsSession();
            [PreserveSig] int SetDuckingPreference([MarshalAs(UnmanagedType.Bool)] bool optOut);
        }

        [ComImport, Guid("87CE5498-68D6-44E5-9215-6DA47EF883D8"),
         InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface ISimpleAudioVolume
        {
            [PreserveSig] int SetMasterVolume(float level, ref Guid eventContext);
            [PreserveSig] int GetMasterVolume(out float level);
            [PreserveSig] int SetMute([MarshalAs(UnmanagedType.Bool)] bool mute, ref Guid eventContext);
            [PreserveSig] int GetMute([MarshalAs(UnmanagedType.Bool)] out bool mute);
        }

        // One audio session owned by a process (RCW for the session control).
        private struct Session
        {
            public IAudioSessionControl Control;     // released through this
            public IAudioSessionControl2 Control2;   // QueryInterface result
            public ISimpleAudioVolume Volume;        // QueryInterface result
            public uint Pid;
            public string ProcessName;
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
                    return st;
                }

                float volume = 0f;
                bool muted = false;
                Guid context = Guid.Empty;
                string error = null;
                st.SessionCount = targets.Count;

                if (action == AudioAction.ToggleMute)
                {
                    // "mute everything unless everything is already muted".
                    // Inverting each session separately (the Python original) left
                    // multi-process apps half muted, which sounds like "mute does
                    // not work" — e.g. Chrome keeps one renderer audible.
                    bool anyReadable = false;
                    bool anyUnmuted = false;
                    for (int i = 0; i < targets.Count; i++)
                    {
                        bool current;
                        if (targets[i].Volume.GetMute(out current) != 0) continue;
                        anyReadable = true;
                        if (!current) anyUnmuted = true;
                    }
                    bool desired = !anyReadable || anyUnmuted;

                    for (int i = 0; i < targets.Count; i++)
                    {
                        int hr = targets[i].Volume.SetMute(desired, ref context);
                        if (hr != 0) error = "SetMute 0x" + hr.ToString("X8");
                    }

                    // read back so the HUD shows the real state
                    muted = desired;
                    bool readAny = false;
                    bool allMuted = true;
                    for (int i = 0; i < targets.Count; i++)
                    {
                        bool current;
                        if (targets[i].Volume.GetMute(out current) != 0) continue;
                        readAny = true;
                        if (!current) allMuted = false;
                        float v;
                        if (targets[i].Volume.GetMasterVolume(out v) == 0 && v > volume) volume = v;
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
                        if (targets[i].Volume.GetMasterVolume(out v) != 0) continue;
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
                            int hr = targets[i].Volume.SetMasterVolume(next, ref context);
                            if (hr != 0) { error = "SetMasterVolume 0x" + hr.ToString("X8"); continue; }

                            // same as the Python original: raising volume un-mutes
                            if (delta > 0f)
                            {
                                bool isMuted;
                                if (targets[i].Volume.GetMute(out isMuted) == 0 && isMuted)
                                    targets[i].Volume.SetMute(false, ref context);
                            }
                        }
                    }

                    volume = next;
                    muted = true;
                    bool readAny = false;
                    for (int i = 0; i < targets.Count; i++)
                    {
                        bool m;
                        if (targets[i].Volume.GetMute(out m) != 0) continue;
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
                for (int i = 0; i < sessions.Count; i++) Release(sessions[i].Control);
                if (mustUninitialize) CoUninitialize();
            }
            return st;
        }

        private static bool EnterCom(out bool mustUninitialize)
        {
            mustUninitialize = false;
            int hr = CoInitializeEx(IntPtr.Zero, COINIT_APARTMENTTHREADED);
            if (hr == RPC_E_CHANGED_MODE) return true;   // already in an MTA: usable as-is
            if (hr < 0) return false;
            mustUninitialize = true;                     // S_OK / S_FALSE: balanced below
            return true;
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

        // Default render endpoint -> session manager -> every session.
        // Session controls that end up in `output` are owned by the caller.
        private static void EnumerateSessions(List<Session> output)
        {
            IMMDeviceEnumerator deviceEnumerator = null;
            IMMDevice device = null;
            IAudioSessionManager2 manager = null;
            IAudioSessionEnumerator enumerator = null;
            try
            {
                deviceEnumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();

                // eRender = 0, eConsole = 0
                if (deviceEnumerator.GetDefaultAudioEndpoint(0, 0, out device) != 0 || device == null)
                    return;

                Guid iid = typeof(IAudioSessionManager2).GUID;
                object activated;
                if (device.Activate(ref iid, CLSCTX_ALL, IntPtr.Zero, out activated) != 0 || activated == null)
                    return;
                manager = (IAudioSessionManager2)activated;

                if (manager.GetSessionEnumerator(out enumerator) != 0 || enumerator == null)
                    return;

                int count;
                if (enumerator.GetCount(out count) != 0) return;

                for (int i = 0; i < count; i++)
                {
                    IAudioSessionControl control;
                    if (enumerator.GetSession(i, out control) != 0 || control == null) continue;

                    // casting an RCW performs QueryInterface
                    IAudioSessionControl2 control2 = control as IAudioSessionControl2;
                    ISimpleAudioVolume volume = control as ISimpleAudioVolume;
                    if (control2 == null || volume == null)
                    {
                        Release(control);
                        continue;
                    }

                    uint pid;
                    if (control2.GetProcessId(out pid) != 0 || pid == 0)
                    {
                        Release(control);      // system sounds / stale session
                        continue;
                    }

                    Session session = new Session();
                    session.Control = control;
                    session.Control2 = control2;
                    session.Volume = volume;
                    session.Pid = pid;
                    session.ProcessName = ProcessNameOf(pid);
                    if (session.ProcessName.Length == 0)
                    {
                        Release(control);
                        continue;
                    }
                    output.Add(session);
                }
            }
            catch { }
            finally
            {
                Release(enumerator);
                Release(manager);
                Release(device);
                Release(deviceEnumerator);
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
    }
}
