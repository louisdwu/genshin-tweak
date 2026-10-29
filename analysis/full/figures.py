#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""figures.py —— 14 张图。全部基于 charts.Svg/Panel 手写 SVG。"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np

import gta
from charts import (Svg, Panel, ramp, hexc, legend_row, PWR_STOPS, DIV_STOPS,
                    STATE_COLORS, FONT, plot_series)

W = 1240
L, R = 96, 1152


def _time_axis(p: Panel, t0: dt.datetime, span: float, step_h=3, fmt='%m-%d %H:%M'):
    t = t0.replace(minute=0, second=0, microsecond=0)
    while (t - t0).total_seconds() < 0:
        t += dt.timedelta(hours=step_h)
    while (t - t0).total_seconds() <= span:
        x = (t - t0).total_seconds()
        p.s.text(p.X(x), p.y1 + 18, t.strftime(fmt), 11, '#9aa1ae', '400', 'middle')
        p.s.line(p.X(x), p.y0, p.X(x), p.y1, '#f4f6f9')
        t += dt.timedelta(hours=step_h)


# ---------------------------------------------------------------- 1. 全景
def fig_overview(ctx, path):
    s = ctx['s']
    span = s.wall_span
    t0 = s.t0
    H = 1080
    svg = Svg(W, H, '全景：24 小时 52 分钟的原始曲线',
              f'{t0:%Y-%m-%d %H:%M} → {s.datetime(len(s)-1):%m-%d %H:%M}　'
              f'有效采样 {ctx["covered_h"]:.2f} h（占墙钟 {ctx["duty"]*100:.0f}%），共 {len(s)} 点 / 3 s')
    ph, gap, top = 150, 72, 122
    for i, (key, title, hi, color, steps) in enumerate([
            ('p', 'GPU 板卡功耗 (W)', 260, '#c0392b', 4),
            ('clk', '核心频率 clocks.sm (MHz)', 2000, '#2c6fbb', 4),
            ('util', 'GPU 3D 利用率 (%)', 70, '#e08b1a', 4),
            ('temp', 'GPU 温度 (℃)', 75, '#4c9f70', 4)]):
        y0 = top + i * (ph + gap)
        p = Panel(svg, L, y0, R, y0 + ph, (0, span), (0, hi), title=title, yticks=steps)
        if i == 0:
            _time_axis(p, t0, span)
        # 休眠灰带
        for a, b in [(x[0], x[1]) for x in _sleep_spans(ctx)]:
            p.vspan((a - t0).total_seconds(), (b - t0).total_seconds(), '#eef0f4')
        # 25 h / 3 s 的点远超像素数，按 3 点抽样（≈9 s）以缩小 SVG
        v = np.clip(getattr(s, key), 0, hi)[::3]
        plot_series(p, s.t[::3], v, color, 1.1, fill=True, opacity=0.13, gap=45)
        if i == 0:
            for c in ctx['cps'][:8]:
                p.vline((c['t'] - t0).total_seconds(), '#8b93a1', 0.9, '3 4')
    # 图例
    ly = top + 4 * (ph + gap) - 26
    legend_row(svg, L, ly, [('#eef0f4', '休眠/关机（无采样）'), ('#8b93a1', '检测到的变化点（top 8）')])
    svg.text(26, H - 16, '曲线由 3 s 采样按 9 s 抽样绘制（像素级精度不受影响）；缺口处断开、不连线；灰底＝无采样。', 11, '#a6adba')
    return svg.save(path)


def _sleep_spans(ctx):
    return [(g['start'], g['end'], '') for g in ctx['gaps'] if g['dur'] > 300]


# ---------------------------------------------------------------- 2. 分布
def fig_dist(ctx, path):
    s = ctx['s']
    svg = Svg(W, 900, '四个核心变量的分布',
              '直方图按 Scott 规则分箱；竖线为中位数（实线）与均值（虚线），标注 p5 / p95')
    boxes = [(0, 0), (1, 0), (0, 1), (1, 1)]
    pw, phh = 500, 290
    for i, (key, title) in enumerate(ctx['keys']):
        if i >= 4:
            break
        cx, cy = boxes[i]
        x0 = L + cx * (pw + 90)
        y0 = 120 + cy * (phh + 130)
        v = getattr(s, key)
        v = v[np.isfinite(v)]
        h, edges = np.histogram(v, bins='scott')
        p = Panel(svg, x0, y0, x0 + pw, y0 + phh, (float(edges[0]), float(edges[-1])),
                  (0, float(h.max()) * 1.12), title=title, yticks=3)
        for j in range(len(h)):
            xa, xb = p.X(edges[j]), p.X(edges[j + 1])
            p.s.rect(xa, p.Y(h[j]), max(xb - xa - 0.6, 0.6), p.Y(0) - p.Y(h[j]),
                     '#5fa8d3', opacity=0.75)
        d = ctx['desc'][key]
        p.s.line(p.X(d['median']), p.y0, p.X(d['median']), p.y1, '#c0392b', 1.6)
        p.s.line(p.X(d['mean']), p.y0, p.X(d['mean']), p.y1, '#c0392b', 1.2, dash='4 4')
        p.s.text(p.X(d['median']), p.y0 - 8, f"中位 {d['median']:.1f}", 11, '#c0392b', '600', 'middle')
        for q, lab in ((5, 'p5'), (95, 'p95')):
            p.s.line(p.X(d[lab]), p.y1 - 14, p.X(d[lab]), p.y1, '#9aa1ae', 1.0, dash='2 3')
            p.s.text(p.X(d[lab]), p.y1 - 20, f"{d[lab]:.0f}", 10, '#9aa1ae', '400', 'middle')
        p.s.text(x0, y0 + phh + 20, f"mean {d['mean']:.1f}　sd {d['sd']:.1f}　"
                                    f"skew {d['skew']:.2f}　kurt {d['kurt']:.1f}　n={d['n']}", 11.5, '#6b7280')
    # CPU 单独放第五格
    c = ctx['desc']['cpu']
    svg.text(26, 860, f"CPU 占用率：中位 {c['median']:.1f}，p95 {c['p95']:.0f}，均值 {c['mean']:.1f}，"
                       f"sd {c['sd']:.1f} —— 双峰（BGI 空转 vs 游戏/转码），不能只看均值。", 11.5, '#6b7280')
    return svg.save(path)


# ---------------------------------------------------------------- 3. GMM 档位
def fig_gmm(ctx, path):
    s = ctx['s']
    svg = Svg(W, 700, '功耗其实是几个「台阶」，不是连续谱',
              '一维高斯混合（EM）分解：BIC 选 k=%d —— 4 个 σ=3~5 W 的窄峰 + 1 个宽尾（真的有活时）'
              % ctx['gmm']['k'])
    xlo, xhi, binw = 15, 260, 2.5
    v = s.p
    h, edges = np.histogram(v, bins=np.arange(xlo, xhi + binw, binw))
    xs = np.linspace(xlo, xhi, 600)
    tot = np.zeros_like(xs)
    for w, m, sd in ctx['gmm']['comps']:
        tot += w * np.exp(-0.5 * ((xs - m) / sd) ** 2) / (sd * math.sqrt(2 * math.pi))
    scale = len(v) * binw
    ymax = max(h.max(), tot.max() * scale) * 1.10
    p = Panel(svg, L, 130, 920, 560, (xlo, xhi), (0, ymax), yticks=4, yfmt='{:.0f}')
    for j in range(len(h)):
        p.s.rect(p.X(edges[j]), p.Y(h[j]), max(p.X(edges[j + 1]) - p.X(edges[j]) - 0.6, 0.6),
                 p.Y(0) - p.Y(h[j]), '#c8d0da')
    p.line(xs, tot * scale, '#374151', 2.2)
    # 各分量：竖线 + 标签
    for idx, (w, m, sd) in enumerate(ctx['gmm']['comps']):
        col = STATE_COLORS[min(idx + 1, 4)]
        p.s.line(p.X(m), p.Y(ymax * 0.98), p.X(m), p.Y(0), col, 1.4, dash='5 4')
        lab = f'{m:.0f} W · {w*100:.0f}% · σ{sd:.0f}'
        p.s.text(p.X(m) + 4, p.Y(ymax * 0.98) + 14 + idx * 13, lab, 10.5, col, '600')
    p.s.text((L + 920) / 2, 588, 'GPU 板卡功耗 (W)', 12.5, '#374151', '600', 'middle')
    svg.text(30, 150, '样本数', 12, '#374151', '600', rotate=-90)
    # BIC
    p2 = Panel(svg, 980, 130, 1150, 420, (2, 7),
               (min(b for _, b in ctx['gmm']['curve']), max(b for _, b in ctx['gmm']['curve'])),
               title='BIC', yticks=3, yfmt='{:.0f}')
    xs2 = [k for k, _ in ctx['gmm']['curve']]
    ys2 = [b for _, b in ctx['gmm']['curve']]
    p2.line(xs2, ys2, '#2c6fbb', 1.8)
    for k, b in ctx['gmm']['curve']:
        p2.s.circle(p2.X(k), p2.Y(b), 3, '#2c6fbb')
        p2.s.text(p2.X(k), p2.y1 + 16, str(k), 10.5, '#9aa1ae', '400', 'middle')
    kb = ctx['gmm']['k']
    p2.s.circle(p2.X(kb), p2.Y(dict(ctx['gmm']['curve'])[kb]), 5, '#c0392b')
    p2.s.text(p2.X(kb), p2.y0 + 14, '最优 k', 10.5, '#c0392b', '600', 'middle')
    p2.s.text(980, p2.y1 + 34, 'k', 11, '#9aa1ae', '400', 'middle')
    svg.text(L, 622, 'σ 只有 3~5 W 意味着：只要落进某个档位，瞬时功耗几乎是确定的（26 / 42 / 69 / 120 W）。'
                     '第 5 个宽峰（σ=49 W）才是「真的在跑东西」时的连续负载区。', 11.5, '#6b7280')
    svg.text(L, 646, '4 个窄峰的质量合计约 91% —— 大部分时间这台机器只是在这几个台阶之间跳。',
             11.5, '#6b7280')
    svg.text(26, 678, '注：柱＝2.5 W 分箱的样本计数；黑线＝5 分量混合模型的拟合计数；虚线＝各分量均值位置。',
             11, '#a6adba')
    return svg.save(path)


# ---------------------------------------------------------------- 4. 功耗面
def fig_surface(ctx, path):
    s = ctx['s']
    svg = Svg(W, 800, '功耗面：频率 × 利用率 → 平均功耗',
              '每格为平均瓦数（n≥15 才标数字）；颜色只跟功耗走，"同色横条"代表这些工况一样费电')
    Lc, Rc, T, B = 130, 880, 120, 690
    cb0, cstep, ustep = 200, 100, 5
    cols = list(range(cb0, 1900, cstep))
    rows = list(range(0, 100, ustep))
    cell = {}
    for ci in range(len(cols)):
        m = (s.clk >= cols[ci]) & (s.clk < cols[ci] + cstep)
        if not m.any():
            continue
        for ri in range(len(rows)):
            mm = m & (s.util >= rows[ri]) & (s.util < rows[ri] + ustep)
            if mm.any():
                cell[(ci, ri)] = (float(s.p[mm].mean()), int(mm.sum()))
    cw = (Rc - Lc) / len(cols)
    ch = (B - T) / len(rows)
    for ci in range(len(cols)):
        for ri in range(len(rows)):
            x, y = Lc + ci * cw, B - (ri + 1) * ch
            got = cell.get((ci, ri))
            if not got:
                svg.rect(x, y, cw - 1, ch - 1, '#f6f7f9')
                continue
            m, n = got
            svg.rect(x, y, cw - 1, ch - 1, hexc(ramp(m, 20, 240, PWR_STOPS)))
            if n >= 15:
                svg.text(x + cw / 2, y + ch / 2 + 4, '%.0f' % m, 10.5,
                         '#232a36' if m < 165 else '#ffffff', '500', 'middle')
    svg.rect(Lc, T, Rc - Lc, B - T, 'none', '#dfe3ea')
    for ci in range(len(cols) + 1):
        svg.text(Lc + ci * cw, B + 18, str(cb0 + ci * cstep), 10.5, '#9aa1ae', '400', 'middle')
    for ri in range(len(rows) + 1):
        if (ri * ustep) % 20 == 0:
            svg.text(Lc - 10, B - ri * ch + 4, str(ri * ustep), 11, '#9aa1ae', '400', 'end')
    svg.text((Lc + Rc) / 2, B + 44, 'GPU 核心频率 clocks.sm (MHz)', 13, '#374151', '600', 'middle')
    ymid = (T + B) / 2
    svg.text(46, ymid, 'GPU 3D 利用率 utilization.gpu (%)', 13, '#374151', '600',
             rotate=-90)
    notes = [
        ('三个数', '#1f2430', 13, '600'),
        ('1830 MHz + util 10% ≈ 114 W', '#b0392b', 12.2, '400'),
        ('1830 MHz + util 60% ≈ 205 W', '#b0392b', 12.2, '400'),
        ('1300 MHz + util 0%　≈ 68 W', '#2f7d46', 12.2, '400'),
        ('1300 MHz + util 65% ≈ 72 W', '#2f7d46', 12.2, '400'),
        ('', '#6b7280', 9, '400'),
        ('低中频段 util 基本不要钱，', '#4a5160', 12.2, '400'),
        ('只有顶格频率才「活越多越贵」。', '#4a5160', 12.2, '400'),
        ('', '#6b7280', 9, '400'),
        ('1830 MHz 那一列，连 util 5% 都', '#b0392b', 12.2, '400'),
        ('要 114 W —— 比 400 MHz 待机', '#b0392b', 12.2, '400'),
        ('（26 W）多 88 W，什么活也没干。', '#b0392b', 12.2, '400'),
        ('', '#6b7280', 9, '400'),
        ('util 越高功耗略低（45→41），', '#6b7280', 11.8, '400'),
        ('因为有活干时驱动才肯降频降压。', '#6b7280', 11.8, '400'),
        ('utilization.gpu 不能当', '#6b7280', 11.8, '400'),
        ('「工作量/能耗」的代理指标。', '#6b7280', 11.8, '400'),
    ]
    yy = T + 4
    for t, c, sz, w in notes:
        if t:
            svg.text(930, yy, t, sz, c, w)
        yy += 25 if sz > 11.5 else 12
    svg.text(26, 770, '数据源：全量 %d 个样本，按 100 MHz × 5%% 分格；空格＝该组合没出现过' % len(s), 11, '#a6adba')
    return svg.save(path)


# ---------------------------------------------------------------- 5. 功率-频率
def fig_pclk(ctx, path):
    s = ctx['s']
    svg = Svg(W, 700, '功耗 ~ 频率：一条曲线，温度只让它上下平移',
              '左：P–f 散点（按 5 W × 50 MHz 分格着色）；右：分温度带的回归线')
    Lc, Rc, T, B = 120, 700, 120, 520
    xs = np.arange(200, 1900, 50)
    ys = np.arange(20, 260, 5)
    cell = {}
    for i, x in enumerate(xs):
        m = (s.clk >= x) & (s.clk < x + 50)
        if not m.any():
            continue
        for j, y in enumerate(ys):
            mm = m & (s.p >= y) & (s.p < y + 5)
            if mm.any():
                cell[(i, j)] = int(mm.sum())
    vmax = max(cell.values()) if cell else 1
    cw = (Rc - Lc) / len(xs)
    ch = (B - T) / len(ys)
    p = Panel(svg, Lc, T, Rc, B, (180, 1900), (20, 260), title='P–f 联合分布', yticks=4)
    for (i, j), n in cell.items():
        x, y = Lc + i * cw, B - (j + 1) * ch
        c = ramp(math.log1p(n), 0, math.log1p(vmax), [(0, (245, 247, 250)), (0.4, (140, 190, 225)),
                                                      (0.75, (60, 130, 190)), (1, (20, 70, 130))])
        svg.rect(x, y, cw + 0.4, ch + 0.4, hexc(c))
    f = ctx['fits']['clk_util']
    fx = np.array([200, 1900])
    svg.text(Lc, B + 22, '核心频率 (MHz)　（格子越深＝该组合样本越多）', 12, '#374151', '500')
    # 右：分温度带回归
    p2 = Panel(svg, 760, T, 1140, B, (180, 1900), (20, 260), title='分温度带拟合', yticks=4)
    p2.s.text(760, B + 22, '核心频率 (MHz)', 12, '#374151', '500')
    cols = ['#9aa5b1', '#5fa8d3', '#4c9f70', '#e8871e', '#c0392b']
    for i, bf in enumerate(ctx['band_fits']):
        bb = bf['fit']['beta']
        yy = bb[0] + bb[1] * fx + bb[2] * float(np.mean(s.util[(s.temp >= bf['lo']) & (s.temp < bf['hi'])]))
        p2.line(fx, yy, cols[min(i, 4)], 1.8)
        p2.s.text(1140, T + 24 + i * 17, f"{bf['lo']:.0f}–{bf['hi']:.0f}℃  {bb[1]*1000:.1f} W/GHz",
                  10.5, cols[min(i, 4)], '500', 'end')
    svg.text(Lc, 580, f"全样本 P = {f['beta'][0]:.1f} + {f['beta'][1]:.5f}·clk + {f['beta'][2]:.3f}·util　"
                      f"R²={f['r2']:.3f}　RMSE={f['rmse']:.1f} W　n={f['n']}", 12.5, '#374151', '600')
    svg.text(Lc, 604, f"斜率 ≈ {f['beta'][1]*100:.1f} W / 100 MHz。右图各温度带的截距随温度抬升，"
                      f"但这不能全算漏电 —— 高功耗本身也会把温度推高（反向因果），见 fig08 的滞后相关。",
             11.5, '#6b7280')
    svg.text(26, 660, '数据源：全量样本；左图按 5 W × 50 MHz 分格计数，右图按温度带分别拟合（每条线取该带内的平均利用率）。',
             11, '#a6adba')
    return svg.save(path)


# ---------------------------------------------------------------- 6. 状态带
def fig_states(ctx, path):
    s, km = ctx['s'], ctx['km']
    span = s.wall_span
    H = 640
    svg = Svg(W, H, '五个「工况状态」铺满了两天',
              'k-means（工况空间：频率/3D 利用率/CPU 占用，k=%d，轮廓系数 %.2f）；下条形图为各状态的能量与时长'
              % (km['k'], km['sil']))
    # 上：微小方块状态带（每分钟取众数）
    T, B = 120, 210
    step = 60.0
    n = int(span / step) + 1
    p = Panel(svg, L, T, R, B, (0, span), (0, 1), yticks=0, border=False)
    lab = km['labels']
    tt = s.t
    for k in range(n):
        a, b = k * step, (k + 1) * step
        m = (tt >= a) & (tt < b)
        if not m.any():
            continue
        vals, cnts = np.unique(lab[m], return_counts=True)
        p.s.rect(p.X(a), T, max(p.X(b) - p.X(a), 1.0), B - T,
                 STATE_COLORS[int(vals[np.argmax(cnts)])])
    _time_axis(p, s.t0, span)
    legend_row(svg, L, B + 34, [(STATE_COLORS[i], km['names'][i]) for i in range(km['k'])], size=11.5)

    # 下左：能量占比条形
    y0 = 300
    svg.text(L, y0 - 14, '各状态的能量占比（条）与时长（括号）', 12.8, '#374151', '600')
    share = [st['share'] for st in km['stats']]
    bx = L + 150
    for i, st in enumerate(km['stats']):
        yy = y0 + i * 44
        wbar = 520 * st['share']
        svg.rect(bx, yy, 520, 26, '#f4f6f9')
        svg.rect(bx, yy, wbar, 26, STATE_COLORS[i])
        svg.text(L, yy + 18, st['name'], 12, '#1f2430', '600')
        svg.text(bx + wbar + 8, yy + 18, f"{st['wh']:.0f} Wh　{st['share']*100:.1f}%　({st['hours']:.2f} h)",
                 11.5, '#374151')
    # 下右：状态特征
    x2 = 860
    svg.text(x2, y0 - 14, '状态画像', 12.8, '#374151', '600')
    hdr = ['状态', 'W', 'MHz', 'util%', 'cpu%']
    for j, h in enumerate(hdr):
        svg.text(x2 + j * 78, y0 + 16, h, 11, '#9aa1ae', '600')
    for i, st in enumerate(km['stats']):
        yy = y0 + 40 + i * 40
        svg.rect(x2 - 12, yy - 14, 10, 10, STATE_COLORS[i])
        vals = [st['name'].split(' ')[0], f"{st['p']:.0f}±{st['p_sd']:.0f}", f"{st['clk']:.0f}",
                f"{st['util']:.0f}", f"{st['cpu']:.0f}"]
        for j, v in enumerate(vals):
            svg.text(x2 + j * 78, yy, v, 11, '#374151', '500' if j == 0 else '400')
    svg.text(26, H - 44, f"状态抖动率（未平滑时短于 5 点的游程占比）：{ctx['flicker']*100:.1f}% —— "
                         f"说明驱动会在相邻 V/f 档之间高频跳变，这条带已经做过 3 点去抖。", 11.5, '#6b7280')
    svg.text(26, H - 20, '数据源：全量样本；带上每一格＝1 分钟内的多数状态。', 11, '#a6adba')
    return svg.save(path)


# ---------------------------------------------------------------- 7. 转移 / 驻留
def fig_trans(ctx, path):
    km = ctx['km']
    k = km['k']
    svg = Svg(W, 640, '状态转移与驻留',
              '左：一阶转移概率（行＝从，列＝到，格内小字＝样本数）；右：各状态连续驻留时长（去抖后）')
    Lc, T, cell = 190, 150, 76
    svg.text(Lc + cell * k / 2, T - 26, '到 →', 11.5, '#9aa1ae', '400', 'middle')
    for i in range(k):
        svg.text(Lc - 10, T + i * cell + cell / 2 + 4, km['names'][i], 11, '#374151', '500', 'end')
        svg.text(Lc + i * cell + cell / 2, T - 8, f'S{i}', 11, '#9aa1ae', '400', 'middle')
    Pm, Cm = ctx['trans']['P'], ctx['trans']['C']
    for i in range(k):
        for j in range(k):
            x, y = Lc + j * cell, T + i * cell
            v = float(Pm[i, j])
            c = ramp(v, 0, 1, [(0, (248, 249, 251)), (0.5, (150, 190, 225)), (1, (32, 90, 150))])
            svg.rect(x, y, cell - 2, cell - 2, hexc(c))
            if v >= 0.005:
                svg.text(x + cell / 2 - 1, y + cell / 2 + 2, f'{v*100:.0f}%', 12,
                         '#ffffff' if v > 0.55 else '#1f2430', '600', 'middle')
                svg.text(x + cell / 2 - 1, y + cell - 12, f"{int(Cm[i, j])}", 9,
                         '#ffffff' if v > 0.55 else '#7b8391', '400', 'middle')
    off = int(Cm.sum() - np.trace(Cm))
    svg.text(Lc, T + k * cell + 24, f"一步转移 {int(Cm.sum()):,} 次，其中真正换状态 {off} 次", 11, '#6b7280')

    # 右：驻留
    x2, y2 = 720, 150
    svg.text(x2, y2 - 26, '连续驻留时长', 12.8, '#374151', '600')
    dw = ctx['trans']['dwell']
    mx = max(v['med'] for v in dw.values()) or 1
    for i in range(k):
        d = dw[i]
        yy = y2 + 14 + i * 74
        svg.text(x2, yy + 12, km['names'][i], 11.5, '#374151', '500')
        wbar = 190 * (d['med'] / mx)
        svg.rect(x2 + 120, yy, 190, 16, '#f4f6f9')
        svg.rect(x2 + 120, yy, max(wbar, 1.5), 16, STATE_COLORS[i])
        svg.text(x2 + 120, yy + 34, f"中位 {gta.hms(d['med'])}　最长 {gta.hms(d['max'])}　{d['n']} 段",
                 10.5, '#6b7280')
    svg.text(26, 600, '读法：对角线接近 100% 说明这段时间工况极其稳定（切换很少）；真正频繁的切换是 S3⇄S4'
                      '（满频空转 ⇄ 满频低载），也就是「GPU 一直顶在 1.8 GHz」。', 11.5, '#6b7280')
    return svg.save(path)


# ---------------------------------------------------------------- 8. 自相关
def fig_acf(ctx, path):
    a = ctx['acf']
    svg = Svg(W, 620, '时间结构：自相关',
              '左：功耗/利用率/CPU 的自相关（3 s 步长，0–5 分钟）；右：去掉状态均值后的残差自相关')
    lags = np.arange(len(a['p'])) * 3 / 60.0
    p = Panel(svg, L, 130, 700, 520, (0, 5), (-0.2, 1.0),
              title='自相关 ACF（分钟）', yticks=4, yfmt='{:.1f}')
    for key, col, lab in (('p', '#c0392b', '功耗'), ('util', '#e08b1a', 'util'),
                          ('cpu', '#6b7280', 'CPU')):
        p.line(lags, a[key], col, 1.6)
    p.hline(1.96 / math.sqrt(200), '#b0b7c3', 1.0, '3 3')
    p.hline(-1.96 / math.sqrt(200), '#b0b7c3', 1.0, '3 3')
    p.s.text((L + 700) / 2, p.y1 + 42, '滞后（分钟）', 12.5, '#374151', '600', 'middle')
    legend_row(svg, L, 560, [('#c0392b', '功耗'), ('#e08b1a', 'util'), ('#6b7280', 'CPU')])
    p2 = Panel(svg, 760, 130, 1150, 520, (0, 5), (-0.2, 1.0), title='残差 ACF（去状态均值）', yticks=4,
               yfmt='{:.1f}')
    p2.line(lags, a['resid'], '#2c6fbb', 1.6)
    p2.hline(0, '#b0b7c3', 1.0, '3 3')
    p2.s.text((760 + 1150) / 2, p2.y1 + 42, '滞后（分钟）', 12.5, '#374151', '600', 'middle')
    svg.text(26, 590, 'lag=1 仍有 %.2f 的相关 —— 相邻 3 s 样本远不独立（有效独立样本比例仅 %.1f%%），'
                      '所以「样本数」不能当「证据量」。' % (a['p'][1], ctx['neff_ratio'] * 100),
             11.5, '#6b7280')
    return svg.save(path)


# ---------------------------------------------------------------- 9. 变化点
def fig_cp(ctx, path):
    s = ctx['s']
    span = s.wall_span
    svg = Svg(W, 700, '变化点检测：这两天到底经历了多少次「换挡」',
              '1 分钟均值 + 二分割（L2 代价，BIC 型惩罚，最小段 2 分钟）：共 %d 个显著变化点' % len(ctx['cps']))
    p = Panel(svg, L, 120, R, 520, (0, span), (0, 270), title='功耗（1 分钟均值）', yticks=4)
    m = ctx['minutes']
    xs = np.array([(r['t'] - s.t0).total_seconds() for r in m])
    ys = np.array([r['p'] for r in m])
    plot_series(p, xs, ys, '#c0392b', 1.4, fill=True, opacity=0.12, gap=240)
    for a, b in [(x[0], x[1]) for x in _sleep_spans(ctx)]:
        p.vspan((a - s.t0).total_seconds(), (b - s.t0).total_seconds(), '#eef0f4')
    for c in ctx['cps'][:10]:
        x = (c['t'] - s.t0).total_seconds()
        p.vline(x, '#2c6fbb', 1.0, '3 3')
        p.s.circle(p.X(x), p.Y(min(max(c['after'], 0), 270)), 3, '#2c6fbb')
    _time_axis(p, s.t0, span)
    p.xaxis_label('时间')
    # 前十变化点表
    y0 = 580
    svg.text(L, y0 - 12, '幅度最大的 10 个变化点', 12.8, '#374151', '600')
    for i, c in enumerate(ctx['cps'][:10]):
        col = y0 + i * 20
        svg.text(L, col + 10, f"{i+1:2d}.  {c['t']:%m-%d %H:%M}", 11, '#374151')
        svg.text(L + 190, col + 10, f"{c['before']:.0f} → {c['after']:.0f} W", 11, '#374151')
        svg.text(L + 340, col + 10, f"Δ {c['delta']:+.0f} W", 11,
                 '#c0392b' if c['delta'] > 0 else '#2f7d46', '600')
    svg.text(560, y0 - 12, '读法', 12.8, '#374151', '600')
    for i, t in enumerate([
            '· 全部 15 个变化点里，最大的几次都在 10-28 傍晚到夜间；',
            '· 白天那段（09:25–10:55）根本没有变化点 —— 它一直是同一个状态，',
            '　 这才是「空转」最危险的地方：稳定、安静、还一直在烧 119 W；',
            '· 变化点不等于「换游戏/换程序」，也可能只是加载、切场景。']):
        svg.text(560, y0 + 10 + i * 20, t, 11.2, '#6b7280')
    svg.text(26, 680, '数据源：全量样本先聚合成 1 分钟均值，再按连续块分别做二分割；灰底为休眠缺口。', 11, '#a6adba')
    return svg.save(path)


# ---------------------------------------------------------------- 10. 每小时能量
def fig_energy(ctx, path):
    km = ctx['km']
    k = km['k']
    he = ctx['hour_energy']
    svg = Svg(W, 720, '每小时电量：账单按状态拆开',
              '柱＝该小时 GPU 电量（Wh），按占据的状态堆叠；折线＝该小时的平均功耗')
    hs = [h for h, _ in he]
    if not hs:
        return svg.save(path)
    t0, t1 = hs[0], hs[-1]
    span = (t1 - t0).total_seconds() + 3600
    p = Panel(svg, L, 130, R, 500, (0, span), (0, max(sum(v) for _, v in he) * 1.15),
              title='Wh / 小时', yticks=4)
    bw = (R - L) / (span / 3600.0) * 0.72
    for h, v in he:
        x = p.X((h - t0).total_seconds())
        base = 0.0
        for j in range(k):
            if v[j] <= 0:
                continue
            y0 = p.Y(base)
            y1 = p.Y(base + v[j])
            svg.rect(x, y1, bw, y0 - y1, STATE_COLORS[j])
            base += v[j]
    _time_axis(p, t0, span, step_h=3)
    p.xaxis_label('时间')
    legend_row(svg, L, 578, [(STATE_COLORS[i], km['names'][i]) for i in range(k)], size=11.5)
    # 汇总
    tot = sum(sum(v) for _, v in he)
    svg.text(L, 614, f'窗口内合计 {tot:.0f} Wh（GPU 板卡）；覆盖 {ctx["energy_hours"]:.2f} h，'
                     f'平均 {tot/ctx["energy_hours"]:.1f} W。', 12.5, '#374151', '600')
    svg.text(L, 638, '注意：空白的小时是休眠（无采样），不是 0 W —— 所以「日均功耗」必须按覆盖时长算。',
             11.5, '#6b7280')
    svg.text(26, 686, '数据源：全量样本，按墙钟小时分桶、按状态累加 Wh。', 11, '#a6adba')
    return svg.save(path)


# ---------------------------------------------------------------- 11. 采样质量
def fig_sampling(ctx, path):
    s = ctx['s']
    svg = Svg(W, 620, '采样质量：你手上的数据有多「诚实」',
              '左：采样间隔分布；右：相邻 3 s 样本的功耗跳变分布')
    j = ctx['jitter']
    p = Panel(svg, L, 130, 620, 480, (0, 6), (0, 1), title='采样间隔 (s)', yticks=4, yfmt='{:.0%}')
    h, edges = np.histogram(j, bins=np.arange(2, 6.1, 0.1))
    dens = h / (len(j) * 0.1)
    for i in range(len(h)):
        p.s.rect(p.X(edges[i]), p.Y(dens[i]), max(p.X(edges[i + 1]) - p.X(edges[i]) - 0.5, 0.5),
                 p.Y(0) - p.Y(dens[i]), '#5fa8d3', opacity=0.8)
    p.xaxis_label('间隔（秒）')
    svg.text(L, 510, f'n={len(j)}　中位 {np.median(j):.2f} s　均值 {j.mean():.2f} s　'
                     f'p95 {np.percentile(j,95):.2f} s', 11.5, '#6b7280')
    d = ctx['dp']['n']
    p2 = Panel(svg, 700, 130, 1150, 480, (0, 60), (0, 1), title='相邻样本 |ΔP| (W)', yticks=4, yfmt='{:.0%}')
    svg.text(26, 590, f"相邻样本功耗差：中位 {ctx['dp']['median']:.1f} W，p90 {ctx['dp']['p90']:.1f} W，"
                      f"p99 {ctx['dp']['p99']:.1f} W；有 {ctx['dp']['big_frac']*100:.1f}% 的相邻样本差超过 20 W "
                      f"—— 这就是单点读数的噪声底。", 11.5, '#6b7280')
    return svg.save(path)


# ---------------------------------------------------------------- 12. 可检测性
def fig_detect(ctx, path):
    w5 = ctx['win'][5]
    w10 = ctx['win'][10]
    svg = Svg(W, 640, '你能分辨多大的差异？—— 用你自己的数据算',
              '相邻两个窗口的功耗差（同状态、同连续段），以及 95% 判据线')
    p = Panel(svg, L, 130, 760, 470, (0, 12), (0, max(6, w10['n'] / 2 + 2)),
              title='|Δ| 分布（5 分钟窗口）', yticks=4, yfmt='{:.0f}')
    h, edges = np.histogram(np.abs(w5['deltas']), bins=np.arange(0, 12.5, 0.5))
    for i in range(len(h)):
        p.s.rect(p.X(edges[i]), p.Y(h[i]), max(p.X(edges[i + 1]) - p.X(edges[i]) - 1, 1),
                 p.Y(0) - p.Y(h[i]), '#5fa8d3', opacity=0.85)
    for v, lab, col in ((w5['med'], f"中位 {w5['med']:.1f} W", '#2f7d46'),
                        (w5['mde'], f"95% 判据 {w5['mde']:.1f} W", '#c0392b'),
                        (5.3, '上次的转码效应 5.3 W', '#8b5cf6')):
        p.s.line(p.X(v), p.y0, p.X(v), p.y1, col, 1.6, dash='5 4')
        p.s.text(p.X(v), p.y0 - 8, lab, 11, col, '600', 'middle')
    p.xaxis_label('|Δ 功耗| (W)')
    # 右：判据随窗口长度
    p2 = Panel(svg, 820, 130, 1150, 470, (2, 25), (0, 14), title='判据 vs 窗口长度', yticks=4)
    mins = [5, 10, 20]
    vals = [ctx['win'][m]['mde'] for m in mins if ctx['win'][m]['n'] > 0 and np.isfinite(ctx['win'][m]['mde'])]
    p2.line([m for m in mins if ctx['win'][m]['n'] > 0 and np.isfinite(ctx['win'][m]['mde'])], vals, '#c0392b', 1.8)
    for m, v in zip([m for m in mins if ctx['win'][m]['n'] > 0 and np.isfinite(ctx['win'][m]['mde'])], vals):
        p2.s.circle(p2.X(m), p2.Y(v), 3.5, '#c0392b')
        p2.s.text(p2.X(m), p2.Y(v) - 8, f'{v:.1f} W', 10.5, '#c0392b', '600', 'middle')
        p2.s.text(p2.X(m), p2.y1 + 16, f'{m} min', 10.5, '#9aa1ae', '400', 'middle')
    p2.s.line(p2.x0, p2.Y(5.3), p2.x1, p2.Y(5.3), '#8b5cf6', 1.4, dash='5 4')
    p2.s.text(p2.x0 + 6, p2.Y(5.3) - 6, '5.3 W', 10.5, '#8b5cf6', '600')
    svg.text(L, 540, f"5 分钟窗口：{w5['n']} 对，|Δ| 中位 {w5['med']:.2f} W、p95 {w5['p95']:.2f} W；"
                     f"10 分钟窗口：{w10['n']} 对，|Δ| 中位 {w10['med']:.2f} W、p95 {w10['p95']:.2f} W。",
             12.2, '#374151', '600')
    svg.text(L, 566, '结论：5 W 级的效应正好落在 5–10 分钟窗口的检测极限上 —— 想判断 2–3 W 的差别，'
                     '要么拉到 20–30 分钟，要么换「CPU+GPU+整机」的多指标。', 11.5, '#6b7280')
    svg.text(26, 610, '数据源：同状态、同连续段的两个相邻窗口构成一对；判据 =1.96·√2·sd(|Δ|)（双侧 95%）。', 11, '#a6adba')
    return svg.save(path)


# ---------------------------------------------------------------- 13. 回归诊断
def fig_fitdiag(ctx, path):
    s = ctx['s']
    f = ctx['fits']['clk_util']
    svg = Svg(W, 620, '回归诊断：线性模型解释了多少，漏了什么',
              '左：拟合值 vs 残差；中：残差分布；右：各模型 AIC / R² 对比')
    fitted = s.p - f['resid']
    p = Panel(svg, L, 130, 520, 470, (0, 260), (-80, 80), title='残差 vs 拟合值', yticks=4)
    xs = np.arange(0, 260, 10)
    for x in xs:
        m = (fitted >= x) & (fitted < x + 10)
        if not m.any():
            continue
        r = f['resid'][m]
        p.s.line(p.X(x + 5), p.Y(np.percentile(r, 10)), p.X(x + 5), p.Y(np.percentile(r, 90)),
                 '#c8d0da', 3)
        p.s.circle(p.X(x + 5), p.Y(np.median(r)), 1.8, '#c0392b')
    p.hline(0, '#8b93a1', 1.2, '4 4')
    p.s.text((L + 520) / 2, 494, '拟合功耗 (W)', 12.5, '#374151', '600', 'middle')
    h, edges = np.histogram(f['resid'], bins=np.arange(-80, 80, 4))
    p2 = Panel(svg, 580, 130, 860, 470, (-80, 80), (0, float(h.max()) * 1.1), title='残差分布',
               yticks=4, yfmt='{:.0f}')
    for i in range(len(h)):
        p2.s.rect(p2.X(edges[i]), p2.Y(h[i]), max(p2.X(edges[i + 1]) - p2.X(edges[i]) - 0.5, 0.5),
                  p2.Y(0) - p2.Y(h[i]), '#5fa8d3', opacity=0.85)
    p2.s.line(p2.X(0), p2.y0, p2.X(0), p2.y1, '#8b93a1', 1.2, dash='4 4')
    p2.s.text((580 + 860) / 2, 494, '残差 (W)', 12.5, '#374151', '600', 'middle')
    # 模型对比
    x0, y0 = 900, 150
    svg.text(x0, y0 - 12, '模型对比', 12.8, '#374151', '600')
    rows = [('P ~ clk', ctx['fits']['clk']), ('P ~ clk + util', ctx['fits']['clk_util']),
            ('P ~ clk + util + clk·util', ctx['fits']['clk_util_int']),
            ('P ~ clk + util + T', ctx['fits']['clk_util_temp'])]
    svg.text(x0 + 190, y0 + 10, 'R² / RMSE', 10.5, '#9aa1ae', '600')
    for i, (nm, mm) in enumerate(rows):
        yy = y0 + 34 + i * 34
        svg.text(x0, yy, nm, 11, '#374151')
        svg.text(x0 + 190, yy, f"{mm['r2']:.3f} / {mm['rmse']:.1f}", 11, '#374151')
    svg.text(26, 540, '残差的最大结构：高拟合段（150–250 W）被系统性低估 —— 说明「高负载」不是线性的，'
                      '用一次项永远拟合不好；这也是为什么线性模型只能解释 85%。', 11.5, '#6b7280')
    svg.text(26, 566, '提示：R² 高不代表因果 —— clk 和 util 本身就被同一个电源管理策略同时决定。', 11.5, '#6b7280')
    svg.text(26, 600, '数据源：全量样本；残差按拟合值分箱后取 p10–p90 带与中位线。', 11, '#a6adba')
    return svg.save(path)


# ---------------------------------------------------------------- 14. 账单
def fig_budget(ctx, path):
    km = ctx['km']
    svg = Svg(W, 560, '电量账单：你测到的，和你没测到的',
              '左：测得的部分（GPU 板卡）按状态拆分；右：整机功耗里还缺的那几块')
    # 左：测得
    tot = sum(st['wh'] for st in km['stats'])
    x0, y0, wmax = L, 180, 520
    y = y0
    for i, st in enumerate(km['stats']):
        w = wmax * st['wh'] / tot
        svg.rect(x0, y, max(w, 2), 42, STATE_COLORS[i])
        txt = f"{st['name']}　{st['wh']:.0f} Wh"
        if w > 150:
            svg.text(x0 + 6, y + 27, txt, 11.5, '#ffffff' if i in (1, 3, 4) else '#1f2430', '600')
        else:
            svg.text(x0 + w + 8, y + 27, txt, 11.5, '#374151', '600')
        y += 48
    svg.text(x0, y0 - 16, f'已测：GPU 板卡合计 {tot:.0f} Wh / {ctx["energy_hours"]:.2f} h', 12.8, '#374151', '600')
    # 右：未测
    x2 = 700
    svg.text(x2, y0 - 16, '未测：缺失的四块（只能给区间）', 12.8, '#374151', '600')
    items = [('CPU 封装', '15–45 W', '只有占用率，没有瓦数'),
             ('内存 + 主板 + 硬盘', '8–18 W', '完全没采'),
             ('显示器 / 风扇 / 其它', '15–45 W', '完全没采'),
             ('电源转换损耗', '≈ 10%', '80Plus 金牌量级')]
    for i, (nm, rng, why) in enumerate(items):
        yy = y0 + i * 52
        svg.rect(x2, yy, 300, 34, '#f6f7f9', '#d6dae2', 1, dash='4 3')
        svg.text(x2 + 8, yy + 22, nm, 11.5, '#374151', '600')
        svg.text(x2 + 310, yy + 22, rng, 11.5, '#b0392b', '600')
        svg.text(x2 + 400, yy + 22, why, 10.8, '#9aa1ae')
    svg.text(x2, y0 + 4 * 52 + 8, '合计：整机 ≈ GPU × 2.2 ~ 3.0 倍（粗估，未标定）', 12.2, '#b0392b', '600')
    svg.text(26, 500, '这不是说测量错了 —— 而是「GPU 板卡功耗」这个口径回答不了「整机耗了多少电」。'
                      '要做整机账，唯一的办法是在插座上加一个功率计，或者读电池放电功率。', 11.5, '#6b7280')
    svg.text(26, 524, '右列区间只用于提醒数量级，不是本机实测值。', 11, '#a6adba')
    return svg.save(path)
