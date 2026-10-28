// ============================================================================
// PowerHistory.cs — power draw sampling, rolling history, CSV persistence
//
// A background thread samples GPU (nvidia-smi) + CPU usage every few seconds,
// keeps a rolling 24 h window in memory for the chart, and appends every
// sample to genshin_tweak_power.csv next to the exe.
//
// SLEEP / HIBERNATE: while the machine is asleep this process is suspended, so
// the timeline simply has a hole. PowerSample.GapBefore marks the first sample
// after such a hole and the chart MUST NOT connect across it — a gap stays a
// gap (no data, no line).
// ============================================================================

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading;
using Microsoft.Win32;

namespace GenshinTweak
{
    internal sealed class PowerSample
    {
        public DateTime Time;
        public double GpuWatt = -1;       // -1 = not available
        public int GpuClockMhz = -1;
        public int GpuTempC = -1;
        public double GpuUtilPct = -1;
        public double CpuPct = -1;
        public bool GapBefore;             // true = do not connect to the previous sample

        public bool HasGpu { get { return GpuWatt >= 0; } }
    }

    internal static class PowerHistory
    {
        public const int SampleIntervalMs = 3000;

        // A hole longer than this is a break. Sleep/hibernate is usually much
        // longer; a slow nvidia-smi call can stretch one interval but not this.
        public const int GapThresholdMs = 15000;

        public static readonly TimeSpan MemoryWindow = TimeSpan.FromHours(24);
        public static readonly TimeSpan DiskWindow = TimeSpan.FromDays(7);

        public static readonly string CsvPath =
            Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "genshin_tweak_power.csv");

        private const string CsvHeader =
            "time,gpu_power_w,gpu_clock_mhz,gpu_temp_c,gpu_util_pct,cpu_pct,gap_before";

        private static readonly object Lock = new object();
        private static readonly List<PowerSample> _samples = new List<PowerSample>();

        private static Thread _thread;
        private static volatile bool _stop = true;
        private static volatile bool _forceBreak;
        private static DateTime _resumeAt = DateTime.MinValue;
        private static string _gapReason = "";
        private static long _version;

        private static long _lastCpuIdle = -1;
        private static long _lastCpuTotal = -1;
        private static int _gpuFailStreak;
        private static DateTime _gpuNextTry = DateTime.MinValue;
        private static DateTime _lastGpuLog = DateTime.MinValue;
        private static bool _firstSample = true;

        public static event Action Updated = delegate { };   // NOT on the UI thread

        public static long Version { get { return Interlocked.Read(ref _version); } }

        public static bool Recording
        {
            get { Thread t = _thread; return t != null && t.IsAlive; }
        }

        // ---- lifecycle -------------------------------------------------------
        public static void Start()
        {
            if (Recording) return;
            LoadFromDisk();

            _stop = false;
            _firstSample = true;
            try
            {
                SystemEvents.PowerModeChanged += OnPowerModeChanged;
            }
            catch (Exception ex)
            {
                PowerControl.Logger("[WARN] 无法监听休眠事件（仍按时间空洞判断）: " + ex.Message);
            }

            _thread = new Thread(Loop);
            _thread.IsBackground = true;
            _thread.Name = "power-sampler";
            _thread.Start();
        }

        public static void Stop()
        {
            _stop = true;
            try { SystemEvents.PowerModeChanged -= OnPowerModeChanged; } catch { }
            Thread t = _thread;
            _thread = null;
            if (t != null && t.IsAlive)
            {
                try { t.Join(1500); } catch { }
            }
        }

        // ---- sleep / hibernate ----------------------------------------------
        private static void OnPowerModeChanged(object sender, PowerModeChangedEventArgs e)
        {
            try
            {
                if (e.Mode == PowerModes.Suspend)
                {
                    _gapReason = "休眠/待机";
                    PowerControl.Logger("[..] 系统进入休眠/待机，功耗曲线在此断开");
                    PowerControl.LogEvent("sleep", "system suspend");
                }
                else if (e.Mode == PowerModes.Resume)
                {
                    _resumeAt = DateTime.Now;
                    _forceBreak = true;
                    _gapReason = "休眠/待机";
                    PowerControl.Logger("[..] 系统已唤醒，休眠期间没有采样，曲线不连线");
                    PowerControl.LogEvent("wake", "system resume");
                }
            }
            catch { /* never throw out of a system event */ }
        }

        // ---- sampler ---------------------------------------------------------
        private static void Loop()
        {
            while (!_stop)
            {
                DateTime started = DateTime.Now;

                // right after wake-up the driver needs a moment; do not write a
                // half-empty row, just wait for the first clean sample
                if (_resumeAt != DateTime.MinValue &&
                    (started - _resumeAt).TotalMilliseconds < 1500)
                {
                    SleepInterruptible(250);
                    continue;
                }

                PowerSample s = new PowerSample();
                s.Time = started;

                bool gap = _forceBreak;
                DateTime prev = LastSampleTime();
                if (prev != DateTime.MinValue)
                {
                    double dt = (started - prev).TotalMilliseconds;
                    if (dt > GapThresholdMs || dt < 0) gap = true;
                }
                s.GapBefore = gap;
                if (gap)
                {
                    // never average CPU usage across a hole
                    _lastCpuIdle = -1;
                    _lastCpuTotal = -1;
                }

                s.CpuPct = ReadCpuPercent();

                double w = -1, util = -1;
                int clk = -1, tmp = -1;
                if (DateTime.Now >= _gpuNextTry)
                {
                    if (PowerControl.SampleGpu(out w, out clk, out tmp, out util))
                    {
                        _gpuFailStreak = 0;
                    }
                    else
                    {
                        _gpuFailStreak++;
                        w = -1; util = -1; clk = -1; tmp = -1;
                        if (_gpuFailStreak == 3 ||
                            (_gpuFailStreak > 3 && (DateTime.Now - _lastGpuLog).TotalMinutes >= 60))
                        {
                            _lastGpuLog = DateTime.Now;
                            PowerControl.Logger("[WARN] 读取 GPU 功耗失败，曲线仅记录 CPU（稍后自动重试）");
                        }
                        // do not spawn nvidia-smi in vain every few seconds,
                        // but keep the blind spot short after a transient error
                        _gpuNextTry = DateTime.Now.AddSeconds(20);
                    }
                }
                s.GpuWatt = w;
                s.GpuClockMhz = clk;
                s.GpuTempC = tmp;
                s.GpuUtilPct = util;

                bool first = _firstSample;
                AppendSample(s);
                // the very first sample of a session is always "after a hole"
                // when history was loaded — no need to announce that one
                if (gap && prev != DateTime.MinValue && !first)
                    LogHole(prev, started);
                _firstSample = false;

                _forceBreak = false;
                _gapReason = "";
                _resumeAt = DateTime.MinValue;

                int spent = (int)(DateTime.Now - started).TotalMilliseconds;
                int rest = SampleIntervalMs - spent;
                SleepInterruptible(rest > 50 ? rest : 50);
            }
        }

        private static void SleepInterruptible(int ms)
        {
            int left = ms;
            while (left > 0 && !_stop)
            {
                int chunk = Math.Min(200, left);
                Thread.Sleep(chunk);
                left -= chunk;
            }
        }

        private static void LogHole(DateTime prev, DateTime cur)
        {
            TimeSpan hole = cur - prev;
            string dur;
            if (hole.TotalHours >= 1)
                dur = string.Format("{0}:{1:00}:{2:00}", (int)hole.TotalHours, hole.Minutes, hole.Seconds);
            else if (hole.TotalMinutes >= 1)
                dur = string.Format("{0} 分 {1:00} 秒", (int)hole.TotalMinutes, hole.Seconds);
            else
                dur = string.Format("{0} 秒", (int)hole.TotalSeconds);

            string why = string.IsNullOrEmpty(_gapReason) ? "无数据" : _gapReason;
            PowerControl.Logger(string.Format("[..] 功耗曲线断开 {0}（{1}，{2:HH:mm:ss} → {3:HH:mm:ss}），不连线",
                dur, why, prev, cur));
            PowerControl.LogEvent("power-gap", dur + " " + why);
        }

        // ---- CPU (own baseline: the UI label polls with its own state) -------
        private static double ReadCpuPercent()
        {
            Win32.FILETIME idle, kernel, user;
            if (!Win32.GetSystemTimes(out idle, out kernel, out user)) return -1;
            long i = Win32.FtToLong(idle);
            long t = Win32.FtToLong(kernel) + Win32.FtToLong(user);
            long lastI = _lastCpuIdle;
            long lastT = _lastCpuTotal;
            _lastCpuIdle = i;
            _lastCpuTotal = t;
            if (lastT < 0 || lastI < 0) return -1;
            long dt = t - lastT;
            long di = i - lastI;
            if (dt <= 0) return -1;
            double v = 100.0 * (dt - di) / dt;
            return Math.Max(0.0, Math.Min(100.0, v));
        }

        // ---- storage ---------------------------------------------------------
        private static DateTime LastSampleTime()
        {
            lock (Lock)
            {
                return _samples.Count == 0 ? DateTime.MinValue : _samples[_samples.Count - 1].Time;
            }
        }

        private static void AppendSample(PowerSample s)
        {
            lock (Lock)
            {
                _samples.Add(s);
                DateTime cutoff = s.Time - MemoryWindow;
                int remove = 0;
                while (remove < _samples.Count && _samples[remove].Time < cutoff) remove++;
                if (remove > 0) _samples.RemoveRange(0, remove);
                Interlocked.Increment(ref _version);
            }
            Persist(s);
            try { Updated(); } catch { }
        }

        public static List<PowerSample> CopyRange(DateTime from, DateTime to)
        {
            List<PowerSample> result = new List<PowerSample>();
            lock (Lock)
            {
                for (int i = 0; i < _samples.Count; i++)
                {
                    PowerSample s = _samples[i];
                    if (s.Time < from || s.Time > to) continue;
                    result.Add(s);
                }
            }
            return result;
        }

        // true = the two samples are neighbours on the curve (safe to connect)
        public static bool IsContinuous(PowerSample a, PowerSample b)
        {
            if (a == null || b == null) return false;
            if (b.GapBefore) return false;
            double dt = (b.Time - a.Time).TotalMilliseconds;
            return dt >= 0 && dt <= GapThresholdMs;
        }

        // ---- disk ------------------------------------------------------------
        private static void LoadFromDisk()
        {
            try
            {
                TrimDiskIfHuge();
                if (!File.Exists(CsvPath)) return;

                DateTime cutoff = DateTime.Now - MemoryWindow;
                List<PowerSample> loaded = new List<PowerSample>();
                PowerSample prev = null;
                using (StreamReader sr = new StreamReader(CsvPath, Encoding.UTF8))
                {
                    string line;
                    while ((line = sr.ReadLine()) != null)
                    {
                        line = line.Trim();
                        if (line.Length == 0 || line.StartsWith("time", StringComparison.OrdinalIgnoreCase)) continue;
                        PowerSample s = ParseLine(line);
                        if (s == null || s.Time < cutoff) continue;
                        if (prev != null && (s.Time - prev.Time).TotalMilliseconds > GapThresholdMs)
                            s.GapBefore = true;
                        loaded.Add(s);
                        prev = s;
                    }
                }
                if (loaded.Count == 0) return;

                lock (Lock)
                {
                    _samples.InsertRange(0, loaded);
                    Interlocked.Increment(ref _version);
                }
                PowerControl.Logger(string.Format("[..] 功耗记录: 载入最近 {0} 条采样（{1}）",
                    loaded.Count, Path.GetFileName(CsvPath)));
            }
            catch (Exception ex)
            {
                PowerControl.Logger("[WARN] 读取功耗记录失败: " + ex.Message);
            }
        }

        // keep the rolling file bounded: > 16 MB -> rewrite with the last 7 days
        private static void TrimDiskIfHuge()
        {
            try
            {
                FileInfo fi = new FileInfo(CsvPath);
                if (!fi.Exists || fi.Length < 16L * 1024 * 1024) return;

                DateTime cutoff = DateTime.Now - DiskWindow;
                List<string> keep = new List<string>();
                using (StreamReader sr = new StreamReader(CsvPath, Encoding.UTF8))
                {
                    string line;
                    while ((line = sr.ReadLine()) != null)
                    {
                        line = line.Trim();
                        if (line.Length == 0 || line.StartsWith("time", StringComparison.OrdinalIgnoreCase)) continue;
                        PowerSample s = ParseLine(line);
                        if (s != null && s.Time >= cutoff) keep.Add(line);
                    }
                }
                string tmp = CsvPath + ".tmp";
                using (StreamWriter sw = new StreamWriter(tmp, false, new UTF8Encoding(false)))
                {
                    sw.Write(CsvHeader + "\r\n");
                    foreach (string line in keep) sw.Write(line + "\r\n");
                }
                try { File.Delete(CsvPath); } catch { }
                File.Move(tmp, CsvPath);
                PowerControl.Logger("[..] 功耗记录已裁剪: 保留最近 7 天 " + keep.Count + " 条");
            }
            catch { /* trimming is best effort */ }
        }

        private static void Persist(PowerSample s)
        {
            try
            {
                StringBuilder sb = new StringBuilder();
                if (!File.Exists(CsvPath)) sb.Append(CsvHeader).Append("\r\n");
                sb.Append(FormatLine(s)).Append("\r\n");
                File.AppendAllText(CsvPath, sb.ToString(), new UTF8Encoding(false));
            }
            catch { /* recording must never crash the app */ }
        }

        private static string FormatLine(PowerSample s)
        {
            StringBuilder sb = new StringBuilder();
            sb.Append(s.Time.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture)).Append(',');
            AppendNum(sb, s.GpuWatt, "0.0");
            sb.Append(',');
            if (s.GpuClockMhz >= 0) sb.Append(s.GpuClockMhz.ToString(CultureInfo.InvariantCulture));
            sb.Append(',');
            if (s.GpuTempC >= 0) sb.Append(s.GpuTempC.ToString(CultureInfo.InvariantCulture));
            sb.Append(',');
            AppendNum(sb, s.GpuUtilPct, "0");
            sb.Append(',');
            AppendNum(sb, s.CpuPct, "0.0");
            sb.Append(',');
            sb.Append(s.GapBefore ? "1" : "0");
            return sb.ToString();
        }

        private static void AppendNum(StringBuilder sb, double v, string fmt)
        {
            if (v >= 0) sb.Append(v.ToString(fmt, CultureInfo.InvariantCulture));
        }

        private static PowerSample ParseLine(string line)
        {
            string[] f = line.Split(',');
            if (f.Length < 7) return null;
            DateTime t;
            if (!DateTime.TryParseExact(f[0].Trim(), "yyyy-MM-dd HH:mm:ss",
                CultureInfo.InvariantCulture, DateTimeStyles.None, out t)) return null;

            PowerSample s = new PowerSample();
            s.Time = t;
            s.GpuWatt = ParseNum(f[1]);
            double clk = ParseNum(f[2]);
            s.GpuClockMhz = clk >= 0 ? (int)Math.Round(clk) : -1;
            double tmp = ParseNum(f[3]);
            s.GpuTempC = tmp >= 0 ? (int)Math.Round(tmp) : -1;
            s.GpuUtilPct = ParseNum(f[4]);
            s.CpuPct = ParseNum(f[5]);
            s.GapBefore = f[6].Trim() == "1";
            return s;
        }

        private static double ParseNum(string s)
        {
            double v;
            if (double.TryParse(s.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out v)) return v;
            return -1;
        }
    }
}
