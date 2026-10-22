// ============================================================================
// VolumeHud.cs — top-center OSD card for the foreground volume
//
// Merged from the "Volume Controller" project (volume_controller.py OSD):
// dark rounded-ish card with the app name, percentage and a level bar.
// Unlike the pause toast it is updated IN PLACE (repaint only), so holding
// the hotkey at 20 steps/second never flickers or re-creates the window.
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

        public string Headline = "";
        public string ValueText = "";
        public float Level;
        public bool Warn;

        public VolumeHudForm()
        {
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.Manual;
            TopMost = true;
            ShowInTaskbar = false;
            BackColor = BgColor;
            Opacity = 0.94;
            DoubleBuffered = true;
            ClientSize = new Size(460, 92);
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
            Color accent = Warn ? WarnColor : OkColor;

            using (SolidBrush bg = new SolidBrush(BgColor))
            using (SolidBrush accentBrush = new SolidBrush(accent))
            using (SolidBrush trackBrush = new SolidBrush(TrackColor))
            using (Pen border = new Pen(accent, 2f))
            using (Font titleFont = new Font("Microsoft YaHei UI", 12F, FontStyle.Bold))
            using (Font valueFont = new Font("Consolas", 15F, FontStyle.Bold))
            {
                g.FillRectangle(bg, ClientRectangle);
                g.DrawRectangle(border, 1, 1, Width - 3, Height - 3);

                Rectangle titleRect = new Rectangle(20, 12, Width - 200, 26);
                TextRenderer.DrawText(g, Headline, titleFont, titleRect, accent,
                    TextFormatFlags.Left | TextFormatFlags.VerticalCenter |
                    TextFormatFlags.NoPadding | TextFormatFlags.EndEllipsis);

                Rectangle valueRect = new Rectangle(Width - 170, 10, 150, 28);
                TextRenderer.DrawText(g, ValueText, valueFont, valueRect, accent,
                    TextFormatFlags.Right | TextFormatFlags.VerticalCenter |
                    TextFormatFlags.NoPadding | TextFormatFlags.EndEllipsis);

                int barX = 20;
                int barY = Height - 30;
                int barW = Width - 40;
                int barH = 10;
                g.FillRectangle(trackBrush, barX, barY, barW, barH);

                float level = Level;
                if (level < 0f) level = 0f;
                if (level > 1f) level = 1f;
                int fill = (int)Math.Round((double)barW * level);
                if (fill > 0) g.FillRectangle(accentBrush, barX, barY, fill, barH);
            }
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
            if (form == null || form.IsDisposed)
            {
                form = new VolumeHudForm();
                form.Headline = headline;
                form.ValueText = value;
                form.Level = level;
                form.Warn = warn;
                Rectangle area = Screen.PrimaryScreen.WorkingArea;
                form.Location = new Point(area.Left + (area.Width - form.Width) / 2, area.Top + 64);
                form.Show();
                _form = form;
            }
            else
            {
                form.Headline = headline;
                form.ValueText = value;
                form.Level = level;
                form.Warn = warn;
                form.Invalidate();
            }

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
