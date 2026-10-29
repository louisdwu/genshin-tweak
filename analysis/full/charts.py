#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""charts.py —— 纯手写 SVG 绘图工具 + 本项目所有图表。

不依赖 matplotlib：只用字符串拼 SVG，再用 chromium 无头截成 PNG（见 shot.sh）。
好处是同一份 SVG 可以直接内嵌进单文件 HTML 看板。
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np

FONT = "Noto Sans CJK SC, PingFang SC, Microsoft YaHei, sans-serif"

# 状态配色（S0..S4）
STATE_COLORS = ['#9aa5b1', '#5fa8d3', '#4c9f70', '#e8871e', '#c0392b']
STATE_NAMES = ['S0 待机', 'S1 低载', 'S2 中载', 'S3 顶格空转', 'S4 满载工']


def esc(s: str) -> str:
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


# ============================================================ 基础画布
class Svg:
    def __init__(self, w, h, title='', sub='', bg='#ffffff'):
        self.w, self.h = w, h
        self.el = [f'<rect width="{w}" height="{h}" fill="{bg}"/>']
        if title:
            self.text(26, 36, title, 21, '#1f2430', '600')
        if sub:
            self.text(26, 60, sub, 12.8, '#6b7280')

    # ---- 元素 ----
    def text(self, x, y, s, size=12, fill='#4a5160', weight='400', anchor='start',
             rotate=None, family=None):
        tr = f' transform="rotate({rotate} {x:.1f} {y:.1f})"' if rotate is not None else ''
        self.el.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family or FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{tr}>{esc(s)}</text>')

    def line(self, x1, y1, x2, y2, stroke='#e3e7ee', w=1.0, dash=None, opacity=1.0):
        d = f' stroke-dasharray="{dash}"' if dash else ''
        self.el.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                       f'stroke="{stroke}" stroke-width="{w}" opacity="{opacity}"{d}/>')

    def rect(self, x, y, w, h, fill='none', stroke=None, sw=1.0, dash=None, opacity=1.0):
        st = f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ''
        d = f' stroke-dasharray="{dash}"' if dash else ''
        self.el.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                       f'fill="{fill}" opacity="{opacity}"{st}{d}/>')

    def path(self, pts, stroke='#c0392b', w=1.6, fill='none', opacity=1.0, close=False):
        if len(pts) == 0:
            return
        d = 'M' + ' L'.join(f'{x:.1f} {y:.1f}' for x, y in pts) + (' Z' if close else '')
        self.el.append(f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{w}" '
                       f'stroke-linejoin="round" stroke-linecap="round" opacity="{opacity}"/>')

    def area(self, pts_top, baseline_y, fill='#c0392b', opacity=0.15):
        if len(pts_top) == 0:
            return
        d = 'M' + ' L'.join(f'{x:.1f} {y:.1f}' for x, y in pts_top)
        d += f' L{pts_top[-1][0]:.1f} {baseline_y:.1f} L{pts_top[0][0]:.1f} {baseline_y:.1f} Z'
        self.el.append(f'<path d="{d}" fill="{fill}" opacity="{opacity}" stroke="none"/>')

    def circle(self, x, y, r, fill='#c0392b', stroke=None, opacity=1.0):
        st = f' stroke="{stroke}" stroke-width="1"' if stroke else ''
        self.el.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{fill}" '
                       f'opacity="{opacity}"{st}/>')

    def save(self, path):
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}">')
        open(path, 'w', encoding='utf-8').write(head + '\n' + '\n'.join(self.el) + '\n</svg>')
        return path

    def body(self):
        """内嵌 HTML 时只取内容（不含 svg 标签）"""
        return '\n'.join(self.el)


# ============================================================ 面板（带坐标轴）
class Panel:
    def __init__(self, svg: Svg, x0, y0, x1, y1, xlim, ylim, title='', ylabel='',
                 xlabel='', yfmt='{:.0f}', xfmt='{:.0f}', yticks=4, xticks=0,
                 grid=True, border=True):
        self.s, self.x0, self.y0, self.x1, self.y1 = svg, x0, y0, x1, y1
        self.xlim, self.ylim = xlim, ylim
        if border:
            svg.rect(x0, y0, x1 - x0, y1 - y0, 'none', '#e5e8ee')
        if title:
            svg.text(x0 - 60 if False else x0, y0 - 26, title, 12.8, '#374151', '600')
        xlo, xhi = xlim
        ylo, yhi = ylim
        self.dx = (xhi - xlo) or 1.0
        self.dy = (yhi - ylo) or 1.0
        for k in range(yticks + 1):
            if yticks <= 0:
                break
            v = ylo + (yhi - ylo) * k / yticks
            y = self.Y(v)
            if grid:
                svg.line(x0, y, x1, y, '#eef0f4')
            svg.text(x0 - 8, y + 4, yfmt.format(v), 11, '#9aa1ae', '400', 'end')
        if ylabel:
            svg.text(26, (y0 + y1) / 2, ylabel, 12, '#374151', '600', rotate=-90)
        self.xticks = xticks
        self.xfmt = xfmt
        self.xlabel = xlabel

    def X(self, v):
        return self.x0 + (v - self.xlim[0]) / self.dx * (self.x1 - self.x0)

    def Y(self, v):
        return self.y1 - (v - self.ylim[0]) / self.dy * (self.y1 - self.y0)

    def line(self, xs, ys, color, w=1.5, opacity=1.0):
        pts = [(self.X(x), self.Y(y)) for x, y in zip(xs, ys)]
        self.s.path(pts, color, w, opacity=opacity)

    def area(self, xs, ys, color, base=None, opacity=0.15):
        base = self.ylim[0] if base is None else base
        self.s.area([(self.X(x), self.Y(y)) for x, y in zip(xs, ys)], self.Y(base), color, opacity)

    def vspan(self, a, b, fill, opacity=1.0):
        xa, xb = self.X(a), self.X(b)
        self.s.rect(xa, self.y0, max(xb - xa, 0.8), self.y1 - self.y0, fill, opacity=opacity)

    def hline(self, v, color='#7fb08a', w=1.2, dash='6 5'):
        self.s.line(self.x0, self.Y(v), self.x1, self.Y(v), color, w, dash)

    def vline(self, v, color='#b0b7c3', w=1.0, dash='4 4'):
        self.s.line(self.X(v), self.y0, self.X(v), self.y1, color, w, dash)

    def ticks_x(self, points, fmt, y=None, size=11, every=1):
        y = self.y1 + 18 if y is None else y
        for i, p in enumerate(points):
            if i % every:
                continue
            self.s.text(self.X(p), y, fmt(p), size, '#9aa1ae', '400', 'middle')

    def xaxis_label(self, label):
        self.s.text((self.x0 + self.x1) / 2, self.y1 + 42, label, 12.5, '#374151', '600', 'middle')


def ramp(v, lo, hi, stops):
    x = 0.0 if hi <= lo else max(0.0, min(1.0, (v - lo) / (hi - lo)))
    for i in range(len(stops) - 1):
        p0, c0 = stops[i]
        p1, c1 = stops[i + 1]
        if p0 <= x <= p1:
            f = 0.0 if p1 == p0 else (x - p0) / (p1 - p0)
            return tuple(int(round(c0[k] + (c1[k] - c0[k]) * f)) for k in range(3))
    return stops[-1][1]


def hexc(c):
    return '#%02x%02x%02x' % c


PWR_STOPS = [(0.0, (232, 240, 254)), (0.25, (140, 200, 235)), (0.5, (95, 190, 120)),
             (0.72, (245, 200, 70)), (1.0, (215, 60, 50))]

DIV_STOPS = [(0.0, (33, 102, 172)), (0.5, (247, 247, 247)), (1.0, (178, 24, 43))]


def legend_row(svg: Svg, x, y, items, size=12, gap=26, swatch=12):
    """items: [(color, label)]，横向排。"""
    cx = x
    for color, label in items:
        svg.rect(cx, y - 9, swatch, 9, color, '#d6dae2')
        svg.text(cx + swatch + 6, y, label, size, '#374151')
        cx += swatch + 12 + len(label) * (7.4 if any('\u4e00' <= ch <= '\u9fff' for ch in label) else 6.2) + gap
    return cx


def segments(t, v, gap=15.0):
    """按时间断点切段，返回 [(t_seg, v_seg)]，用于「缺口不连线」。"""
    t = np.asarray(t, float)
    v = np.asarray(v, float)
    out, cur_t, cur_v = [], [t[0]], [v[0]]
    for i in range(1, len(t)):
        if t[i] - t[i - 1] > gap:
            out.append((np.array(cur_t), np.array(cur_v)))
            cur_t, cur_v = [], []
        cur_t.append(t[i])
        cur_v.append(v[i])
    if cur_t:
        out.append((np.array(cur_t), np.array(cur_v)))
    return out


def plot_series(panel, t, v, color, w=1.2, fill=True, opacity=0.12, gap=15.0):
    """缺口感知的画线（可选填充）。"""
    for ts, vs in segments(t, v, gap):
        if fill:
            panel.area(ts, vs, color, base=panel.ylim[0], opacity=opacity)
        panel.line(ts, vs, color, w)


def time_labels(ax_t0: dt.datetime, span_sec: float, hours: int):
    """给一个相对秒轴生成 (秒位置, 标签) 列表。"""
    out = []
    t = ax_t0.replace(minute=0, second=0)
    while (t - ax_t0).total_seconds() < 0:
        t += dt.timedelta(hours=1)
    while (t - ax_t0).total_seconds() <= span_sec:
        out.append(((t - ax_t0).total_seconds(), t))
        t += dt.timedelta(hours=hours)
    return out
