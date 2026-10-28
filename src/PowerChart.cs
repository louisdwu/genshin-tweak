// ============================================================================
// PowerChart.cs — dark-theme power/history chart, gap-aware
//
// Draws GPU power (left axis, W) and CPU usage (right axis, %) from
// PowerHistory. The line is split wherever PowerHistory says the samples are
// not continuous (sleep / hibernate / app closed / sampler hiccup), so a hole
// in the data stays a hole — no fabricated straight line across it.
// ============================================================================

using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.Text;
using System.Windows.Forms;

namespace GenshinTweak
{
    internal sealed class PowerChart : Control
    {
        private static readonly Color BgColor     = Color.FromArgb(22, 22, 27);
        private static readonly Color PlotColor   = Color.FromArgb(28, 28, 34);
        private static readonly Color GridColor   = Color.FromArgb(47, 47, 57);
        private static readonly Color AxisColor   = Color.FromArgb(110, 110, 126);
        private static readonly Color TextColor   = Color.FromArgb(166, 168, 182);
        private static readonly Color GpuColor    = Color.FromArgb(166, 227, 161);
        private static readonly Color CpuColor    = Color.FromArgb(250, 179, 135);
        private static readonly Color GapColor    = Color.FromArgb(40, 40, 49);
        private static readonly Color GapTextColor= Color.FromArgb(96, 96, 112);
        private static readonly Color HoverColor  = Color.FromArgb(150, 170, 170, 190);
        private static readonly Color BoxColor    = Color.FromArgb(18, 18, 23);

        private TimeSpan _range = TimeSpan.FromHours(1);
        private int _hoverX = -1;
        private int _hoverY = -1;

        private readonly Font _axisFont;
        private readonly Font _smallFont;

        private List<PowerSample> _cache;
        private DateTime _cacheStamp;
        private long _cacheVersion;
        private TimeSpan _cacheRange;

        public PowerChart()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint |
                     ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            BackColor = BgColor;
            _axisFont = new Font("Consolas", 8.5F);
            _smallFont = new Font("Microsoft YaHei UI", 8.25F);
        }

        public TimeSpan Range
        {
            get { return _range; }
            set
            {
                if (value <= TimeSpan.Zero || value == _range) return;
                _range = value;
                _cache = null;
                Invalidate();
            }
        }

        public List<PowerSample> GetVisibleSamples(out DateTime from, out DateTime to)
        {
            to = DateTime.Now;
            from = to - _range;
            return PowerHistory.CopyRange(from, to);
        }

        // ---- painting --------------------------------------------------------
        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TextRenderingHint = TextRenderingHint.ClearTypeGridFit;

            using (SolidBrush bg = new SolidBrush(BgColor))
                g.FillRectangle(bg, ClientRectangle);

            if (Width < 160 || Height < 70) return;

            int padLeft = 46;
            int padRight = 46;
            int padTop = 22;
            int padBottom = 22;
            Rectangle plot = new Rectangle(padLeft, padTop,
                Math.Max(20, Width - padLeft - padRight),
                Math.Max(20, Height - padTop - padBottom));

            using (SolidBrush pb = new SolidBrush(PlotColor))
                g.FillRectangle(pb, plot);

            DateTime to = DateTime.Now;
            DateTime from = to - _range;
            List<PowerSample> list = VisibleSamples(from, to);

            double gpuMax = 0;
            for (int i = 0; i < list.Count; i++)
                if (list[i].GpuWatt > gpuMax) gpuMax = list[i].GpuWatt;
            if (gpuMax <= 0) gpuMax = 100;
            // multiple of 20 -> the 4 grid labels stay whole watts
            gpuMax = Math.Max(100, Math.Ceiling(gpuMax / 20.0) * 20);

            bool hasGpu = false;
            for (int i = 0; i < list.Count; i++)
                if (list[i].HasGpu) { hasGpu = true; break; }

            using (Pen gridPen = new Pen(GridColor))
            using (Pen axisPen = new Pen(AxisColor))
            using (SolidBrush textBrush = new SolidBrush(TextColor))
            {
                // horizontal grid + left (W) / right (%) labels
                for (int i = 0; i <= 4; i++)
                {
                    int y = plot.Bottom - (int)(plot.Height * i / 4.0);
                    g.DrawLine(i == 0 ? axisPen : gridPen, plot.Left, y, plot.Right, y);

                    double w = gpuMax * i / 4.0;
                    string lw = w.ToString("0");
                    SizeF sw = g.MeasureString(lw, _axisFont);
                    g.DrawString(lw, _axisFont, textBrush,
                        plot.Left - sw.Width - 6, y - sw.Height / 2);

                    string lp = (25 * i).ToString("0") + "%";
                    SizeF sp = g.MeasureString(lp, _axisFont);
                    g.DrawString(lp, _axisFont, textBrush,
                        plot.Right + 6, y - sp.Height / 2);
                }

                // vertical grid + time labels
                int divs = 5;
                for (int i = 0; i <= divs; i++)
                {
                    int x = plot.Left + (int)(plot.Width * i / (double)divs);
                    g.DrawLine(i == 0 ? axisPen : gridPen, x, plot.Top, x, plot.Bottom);

                    DateTime t = from + TimeSpan.FromTicks(_range.Ticks * i / divs);
                    string lbl = (_range.TotalHours <= 6)
                        ? t.ToString("HH:mm") : t.ToString("MM-dd HH:mm");
                    SizeF sz = g.MeasureString(lbl, _axisFont);
                    float lx = x - sz.Width / 2;
                    if (i == 0) lx = x;
                    else if (i == divs) lx = x - sz.Width;
                    g.DrawString(lbl, _axisFont, textBrush, lx, plot.Bottom + 3);
                }
                g.DrawRectangle(axisPen, plot);

                // holes: band only, never a line across them
                using (SolidBrush gapBrush = new SolidBrush(GapColor))
                using (SolidBrush gapTextBrush = new SolidBrush(GapTextColor))
                {
                    PowerSample prev = null;
                    for (int i = 0; i < list.Count; i++)
                    {
                        PowerSample s = list[i];
                        if (prev != null && !PowerHistory.IsContinuous(prev, s))
                        {
                            int x1 = XOf(prev.Time, plot, from);
                            int x2 = XOf(s.Time, plot, from);
                            if (x2 - x1 >= 2)
                            {
                                g.FillRectangle(gapBrush, x1, plot.Top + 1, x2 - x1, plot.Height - 1);
                                if (x2 - x1 >= 55)
                                {
                                    TimeSpan hole = s.Time - prev.Time;
                                    string gt = hole.TotalHours >= 1
                                        ? string.Format("无数据 {0:0.#} 小时", hole.TotalHours)
                                        : string.Format("无数据 {0:0} 分钟", hole.TotalMinutes);
                                    SizeF gs = g.MeasureString(gt, _smallFont);
                                    if (gs.Width + 10 < x2 - x1)
                                        g.DrawString(gt, _smallFont, gapTextBrush,
                                            x1 + (x2 - x1 - gs.Width) / 2,
                                            plot.Top + (plot.Height - gs.Height) / 2);
                                }
                            }
                        }
                        prev = s;
                    }
                }

                using (Pen gpuPen = new Pen(GpuColor, 1.8F))
                using (Pen cpuPen = new Pen(Color.FromArgb(205, CpuColor), 1.3F))
                {
                    if (hasGpu)
                        DrawSeries(g, list, plot, from, gpuMax, gpuPen,
                            delegate(PowerSample s) { return s.GpuWatt; });
                    DrawSeries(g, list, plot, from, 100.0, cpuPen,
                        delegate(PowerSample s) { return s.CpuPct; });
                }

                if (list.Count == 0)
                {
                    string msg = "暂无采样数据 — 休眠 / 程序未运行期间不记录，也不会连线";
                    SizeF ms = g.MeasureString(msg, _smallFont);
                    g.DrawString(msg, _smallFont, textBrush,
                        plot.Left + (plot.Width - ms.Width) / 2,
                        plot.Top + (plot.Height - ms.Height) / 2);
                }

                DrawLegend(g, plot, textBrush, hasGpu);
                if (_hoverX >= plot.Left && _hoverX <= plot.Right &&
                    _hoverY >= plot.Top && _hoverY <= plot.Bottom)
                {
                    DrawHover(g, plot, from, list, gpuMax, textBrush);
                }
            }
        }

        private List<PowerSample> VisibleSamples(DateTime from, DateTime to)
        {
            DateTime now = DateTime.Now;
            long v = PowerHistory.Version;
            if (_cache != null && v == _cacheVersion && _cacheRange == _range &&
                (now - _cacheStamp).TotalSeconds < 1.5)
                return _cache;

            _cache = PowerHistory.CopyRange(from, to);
            _cacheStamp = now;
            _cacheVersion = v;
            _cacheRange = _range;
            return _cache;
        }

        private delegate double ValueOf(PowerSample s);

        private void DrawSeries(Graphics g, List<PowerSample> list, Rectangle plot,
            DateTime from, double maxValue, Pen pen, ValueOf value)
        {
            // 24 h of 3 s samples is 28 800 points: draw at most ~2 per pixel,
            // but inspect every sample so holes still break the line
            int step = Math.Max(1, list.Count / Math.Max(120, plot.Width * 2));

            PointF? prevPt = null;
            PowerSample prevSample = null;
            bool broken = true;
            for (int i = 0; i < list.Count; i++)
            {
                PowerSample s = list[i];
                if (prevSample != null && !PowerHistory.IsContinuous(prevSample, s)) broken = true;
                prevSample = s;

                double v = value(s);
                if (v < 0)
                {
                    broken = true;       // missing value = no line through it
                    continue;
                }
                if ((i % step) != 0 && i != list.Count - 1) continue;

                PointF pt = new PointF(XOf(s.Time, plot, from), YOf(v, maxValue, plot));
                if (prevPt.HasValue && !broken)
                    g.DrawLine(pen, prevPt.Value, pt);
                prevPt = pt;
                broken = false;
            }
        }

        private int XOf(DateTime t, Rectangle plot, DateTime from)
        {
            double ratio = (t - from).TotalSeconds / Math.Max(1.0, _range.TotalSeconds);
            int x = plot.Left + (int)Math.Round(plot.Width * ratio);
            if (x < plot.Left) x = plot.Left;
            if (x > plot.Right) x = plot.Right;
            return x;
        }

        private static int YOf(double v, double maxValue, Rectangle plot)
        {
            double ratio = maxValue <= 0 ? 0 : v / maxValue;
            if (ratio < 0) ratio = 0;
            if (ratio > 1) ratio = 1;
            int y = plot.Bottom - (int)Math.Round(plot.Height * ratio);
            if (y < plot.Top) y = plot.Top;
            if (y > plot.Bottom) y = plot.Bottom;
            return y;
        }

        private void DrawLegend(Graphics g, Rectangle plot, SolidBrush textBrush, bool hasGpu)
        {
            int x = plot.Left + 4;
            int y = 4;
            x = DrawLegendItem(g, x, y, GpuColor,
                hasGpu ? "GPU 功耗 (W)" : "GPU 功耗 (无数据)", textBrush);
            DrawLegendItem(g, x + 10, y, CpuColor, "CPU 占用 (%)", textBrush);

            string note = "灰色 = 无数据（休眠 / 未记录）";
            SizeF ns = g.MeasureString(note, _smallFont);
            g.DrawString(note, _smallFont, textBrush, plot.Right - ns.Width - 2, 3);
        }

        private int DrawLegendItem(Graphics g, int x, int y, Color color, string text, SolidBrush textBrush)
        {
            using (Pen p = new Pen(color, 2F))
                g.DrawLine(p, x, y + 8, x + 16, y + 8);
            g.DrawString(text, _smallFont, textBrush, x + 20, y);
            return x + 20 + (int)g.MeasureString(text, _smallFont).Width;
        }

        private void DrawHover(Graphics g, Rectangle plot, DateTime from,
            List<PowerSample> list, double gpuMax, SolidBrush textBrush)
        {
            if (list.Count == 0) return;
            double span = _range.TotalSeconds;
            DateTime t = from.AddSeconds(span * (_hoverX - plot.Left) / Math.Max(1, plot.Width));
            int idx = NearestIndex(list, t);
            if (idx < 0) return;
            PowerSample s = list[idx];
            bool near = Math.Abs((s.Time - t).TotalSeconds) * 1000.0 <= PowerHistory.GapThresholdMs;

            string[] lines;
            if (near)
            {
                string ts = s.Time.ToString("HH:mm:ss");
                if (_range.TotalHours > 6) ts = s.Time.ToString("MM-dd HH:mm:ss");
                lines = new string[] { ts, GpuLine(s), CpuLine(s) };
            }
            else
            {
                lines = new string[] { "此处无数据", "休眠 / 待机 / 未记录 — 不连线" };
            }

            SizeF[] sizes = new SizeF[lines.Length];
            float bw = 0, bh = 0;
            for (int i = 0; i < lines.Length; i++)
            {
                sizes[i] = g.MeasureString(lines[i], _smallFont);
                bw = Math.Max(bw, sizes[i].Width);
                bh += sizes[i].Height;
            }
            bw += 16;
            bh += 12;

            int bx = (_hoverX > plot.Left + plot.Width / 2) ? plot.Left + 8 : plot.Right - (int)bw - 8;
            int by = plot.Top + 8;
            Rectangle box = new Rectangle(bx, by, (int)bw, (int)bh);
            using (SolidBrush bb = new SolidBrush(BoxColor))
                g.FillRectangle(bb, box);
            using (Pen bp = new Pen(AxisColor))
                g.DrawRectangle(bp, box);

            float ly = by + 6;
            for (int i = 0; i < lines.Length; i++)
            {
                g.DrawString(lines[i], _smallFont, textBrush, bx + 8, ly);
                ly += sizes[i].Height;
            }

            using (Pen hp = new Pen(HoverColor, 1F))
            {
                hp.DashStyle = DashStyle.Dash;
                g.DrawLine(hp, _hoverX, plot.Top, _hoverX, plot.Bottom);
            }
            if (near)
            {
                int px = XOf(s.Time, plot, from);
                if (s.HasGpu)
                {
                    using (SolidBrush dot = new SolidBrush(GpuColor))
                        g.FillEllipse(dot, px - 2.5F, YOf(s.GpuWatt, gpuMax, plot) - 2.5F, 5, 5);
                }
            }
        }

        private static string GpuLine(PowerSample s)
        {
            if (!s.HasGpu) return "GPU 无数据";
            StringBuilder sb = new StringBuilder();
            sb.AppendFormat("GPU {0:0.0} W", s.GpuWatt);
            if (s.GpuClockMhz >= 0) sb.AppendFormat(" · {0} MHz", s.GpuClockMhz);
            if (s.GpuTempC >= 0) sb.AppendFormat(" · {0}°C", s.GpuTempC);
            if (s.GpuUtilPct >= 0) sb.AppendFormat(" · {0:0}%", s.GpuUtilPct);
            return sb.ToString();
        }

        private static string CpuLine(PowerSample s)
        {
            return s.CpuPct >= 0 ? string.Format("CPU {0:0}%", s.CpuPct) : "CPU 无数据";
        }

        private static int NearestIndex(List<PowerSample> list, DateTime t)
        {
            if (list.Count == 0) return -1;
            int lo = 0, hi = list.Count - 1;
            while (lo < hi)
            {
                int mid = (lo + hi) / 2;
                if (list[mid].Time < t) lo = mid + 1;
                else hi = mid;
            }
            if (lo > 0 &&
                Math.Abs((list[lo - 1].Time - t).TotalSeconds) <
                Math.Abs((list[lo].Time - t).TotalSeconds))
                return lo - 1;
            return lo;
        }

        // ---- mouse -----------------------------------------------------------
        protected override void OnMouseMove(MouseEventArgs e)
        {
            base.OnMouseMove(e);
            if (e.X != _hoverX || e.Y != _hoverY)
            {
                _hoverX = e.X;
                _hoverY = e.Y;
                Invalidate();
            }
        }

        protected override void OnMouseLeave(EventArgs e)
        {
            base.OnMouseLeave(e);
            _hoverX = -1;
            _hoverY = -1;
            Invalidate();
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                try { _axisFont.Dispose(); } catch { }
                try { _smallFont.Dispose(); } catch { }
            }
            base.Dispose(disposing);
        }
    }
}
