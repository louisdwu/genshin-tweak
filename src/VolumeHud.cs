// ============================================================================
// VolumeHud.cs — top-center OSD card for the foreground volume
//
// Merged from the "Volume Controller" project (volume_controller.py OSD):
// dark card with the app name, percentage and a level bar.
//
// The card is laid out from MEASURED text (title height + gap + bar height),
// never from fixed pixel anchors, so the bar can neither cover the text nor
// get clipped at any DPI / font size / app-name length.
//
// Non-activating, click-through, topmost, auto-hides after 1.2s.
//
// C# 5 ONLY (csc.exe 4.0.30319).
// ============================================================================

using System;
using System.Drawing;
using System.Windows.Forms;

namespace GenshinTweak
{
    internal sealed class VolumeHudForm : Form
    {
        private const int WS_EX_NOACTIVATE = 0x08000000;
        private const int WS_EX_TOOLWINDOW = 0x00000080;
        private const int WS_EX_TRANSPARENT = 0x00000020;

        private static readonly Color BgColor = Color.FromArgb(24, 20, 32);
        private static readonly Color TrackColor = Color.FromArgb(58, 53, 76);
        private static readonly Color OkColor = Color.FromArgb(166, 227, 161);
        private static readonly Color WarnColor = Color.FromArgb(243, 139, 168);

        private readonly float _scale = 1f;
        private readonly Font _titleFont;
        private readonly Font _valueFont;

        private string _headline = "";
        private string _valueText = "";
        private float _level;
        private bool _warn;

        private readonly int _padX;
        private readonly int _padY;
        private readonly int _gap;
        private readonly int _barH;
        private readonly int _barGap;

        private Size _titleSize;
        private Size _valueSize;

        public VolumeHudForm()
        {
            try
            {
                // desktop DC: reading DPI must not force this window's handle
                using (Graphics probe = Graphics.FromHwnd(IntPtr.Zero)) _scale = probe.DpiX / 96f;
            }
            catch { }
            if (_scale <= 0f) _scale = 1f;

            _titleFont = new Font("Microsoft YaHei UI", 12f * _scale, FontStyle.Bold);
            _valueFont = new Font("Consolas", 14f * _scale, FontStyle.Bold);

            _padX = (int)Math.Round(18 * _scale);
            _padY = (int)Math.Round(11 * _scale);
            _gap = (int)Math.Round(16 * _scale);
            _barH = Math.Max(3, (int)Math.Round(8 * _scale));
            _barGap = (int)Math.Round(10 * _scale);

            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.Manual;
            TopMost = true;
            ShowInTaskbar = false;
            BackColor = BgColor;
            Opacity = 0.94;
            DoubleBuffered = true;
            ClientSize = new Size((int)Math.Round(340 * _scale),
                _padY * 2 + _titleFont.Height + _barGap + _barH);
        }

        public float ScaleFactor
        {
            get { return _scale; }
        }

        // Content drives the size: measure both texts, then reserve room for the
        // bar underneath with a fixed gap.
        public void SetContent(string headline, string value, float level, bool warn)
        {
            _headline = headline == null ? "" : headline;
            _valueText = value == null ? "" : value;
            _level = level;
            _warn = warn;

            _titleSize = Measure(_headline, _titleFont);
            _valueSize = Measure(_valueText, _valueFont);

            int textH = Math.Max(_titleSize.Height, _valueSize.Height);
            int width = _padX * 2 + _titleSize.Width + _gap + _valueSize.Width;
            int minWidth = (int)Math.Round(340 * _scale);
            if (width < minWidth) width = minWidth;
            int height = _padY * 2 + textH + _barGap + _barH;

            Size wanted = new Size(width, height);
            if (ClientSize != wanted) ClientSize = wanted;
            Invalidate();
        }

        private static Size Measure(string text, Font font)
        {
            if (text == null || text.Length == 0) return new Size(0, font.Height);
            return TextRenderer.MeasureText(text, font, new Size(10000, 10000),
                TextFormatFlags.SingleLine | TextFormatFlags.NoPadding);
        }

        protected override bool ShowWithoutActivation
        {
            get { return true; }
        }

        protected override CreateParams CreateParams
        {
            get
            {
                CreateParams cp = base.CreateParams;
                cp.ExStyle |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT;
                return cp;
            }
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            Color accent = _warn ? WarnColor : OkColor;
            int textH = Math.Max(_titleSize.Height, _valueSize.Height);
            if (textH <= 0) textH = _titleFont.Height;

            using (SolidBrush bg = new SolidBrush(BgColor))
            using (SolidBrush accentBrush = new SolidBrush(accent))
            using (SolidBrush trackBrush = new SolidBrush(TrackColor))
            using (Pen border = new Pen(accent, Math.Max(1f, 2f * _scale)))
            {
                g.FillRectangle(bg, ClientRectangle);
                g.DrawRectangle(border, 1, 1, Width - 3, Height - 3);

                int titleWidth = Width - _padX * 2 - _valueSize.Width - _gap;
                if (titleWidth < 0) titleWidth = 0;
                Rectangle titleRect = new Rectangle(_padX, _padY, titleWidth, textH);
                TextRenderer.DrawText(g, _headline, _titleFont, titleRect, accent,
                    TextFormatFlags.Left | TextFormatFlags.VerticalCenter |
                    TextFormatFlags.SingleLine | TextFormatFlags.NoPadding |
                    TextFormatFlags.EndEllipsis);

                Rectangle valueRect = new Rectangle(Width - _padX - _valueSize.Width - 2, _padY,
                    _valueSize.Width + 2, textH);
                TextRenderer.DrawText(g, _valueText, _valueFont, valueRect, accent,
                    TextFormatFlags.Right | TextFormatFlags.VerticalCenter |
                    TextFormatFlags.SingleLine | TextFormatFlags.NoPadding);

                int barX = _padX;
                int barY = Height - _padY - _barH;
                int barW = Width - _padX * 2;
                g.FillRectangle(trackBrush, barX, barY, barW, _barH);

                float level = _level;
                if (level < 0f) level = 0f;
                if (level > 1f) level = 1f;
                int fill = (int)Math.Round((double)barW * level);
                if (fill > 0) g.FillRectangle(accentBrush, barX, barY, fill, _barH);
            }
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                try { if (_titleFont != null) _titleFont.Dispose(); } catch { }
                try { if (_valueFont != null) _valueFont.Dispose(); } catch { }
            }
            base.Dispose(disposing);
        }
    }

    internal static class VolumeHud
    {
        private const int DURATION_MS = 1200;

        private static VolumeHudForm _form;
        private static Timer _timer;
        private static Control _ui;

        public static void Attach(Control ui)
        {
            _ui = ui;
        }

        public static void Show(AudioStatus status)
        {
            Control ui = _ui;
            if (ui == null) return;
            if (ui.InvokeRequired)
            {
                try { ui.BeginInvoke(new Action<AudioStatus>(Show), status); } catch { }
                return;
            }

            string headline;
            string value;
            bool warn = false;
            float level = 0f;

            if (!status.Ok)
            {
                headline = "音量控制失败";
                value = status.Error == null ? "未知错误" : status.Error;
                warn = true;
            }
            else if (!status.HasAudio)
            {
                headline = "🔇 " + (status.AppName.Length > 0 ? status.AppName : "未知应用");
                value = "无音频会话";
                warn = true;
            }
            else if (status.Muted)
            {
                headline = "🔇 " + status.AppName;
                value = "静音";
                warn = true;
            }
            else
            {
                headline = "🔊 " + status.AppName;
                value = ((int)Math.Round(status.Volume * 100f)).ToString() + "%";
                level = status.Volume;
            }

            VolumeHudForm form = _form;
            bool created = false;
            if (form == null || form.IsDisposed)
            {
                form = new VolumeHudForm();
                _form = form;
                created = true;
            }

            form.SetContent(headline, value, level, warn);

            Rectangle area = Screen.PrimaryScreen.WorkingArea;
            form.Location = new Point(area.Left + (area.Width - form.Width) / 2,
                area.Top + (int)(64 * form.ScaleFactor));

            if (created) form.Show();
            else form.Invalidate();

            if (_timer == null)
            {
                _timer = new Timer();
                _timer.Interval = DURATION_MS;
                _timer.Tick += TimerTick;
            }
            else
            {
                _timer.Stop();
            }
            _timer.Start();
        }

        private static void TimerTick(object sender, EventArgs e)
        {
            Close();
        }

        public static void Close()
        {
            if (_timer != null)
            {
                try { _timer.Stop(); } catch { }
            }
            if (_form != null)
            {
                try { _form.Close(); _form.Dispose(); } catch { }
                _form = null;
            }
        }
    }
}
