#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 genshin-tweak 的功耗 CSV 生成分析图（SVG，随后用 chromium 截成 PNG）。

用法: python3 report_figs.py && ./shot.sh fig1_hour fig2_day fig3_surface
输入: raw.csv（附件 1 小时切片）、../genshin_tweak_power.csv（完整历史）
"""
import csv, datetime as dt, collections, os

HERE = os.path.dirname(os.path.abspath(__file__))


def fl(x, d=-1.0):
    x = (x or '').strip()
    try:
        return float(x)
    except Exception:
        return d


def load(path):
    rows = list(csv.DictReader(open(path, encoding='utf-8-sig')))
    S = []
    for r in rows:
        S.append(dict(t=dt.datetime.strptime(r['time'], '%Y-%m-%d %H:%M:%S'),
                      p=fl(r['gpu_power_w']), clk=fl(r['gpu_clock_mhz']),
                      T=fl(r['gpu_temp_c']), util=fl(r['gpu_util_pct']),
                      cpu=fl(r['cpu_pct']), gap=int(fl(r['gap_before'], 0))))
    return S


# ---------------------------------------------------------------- 绘图小工具
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


PWR = [(0.0, (232, 240, 254)), (0.25, (140, 200, 235)), (0.5, (95, 190, 120)),
       (0.72, (245, 200, 70)), (1.0, (215, 60, 50))]

FONT = "Noto Sans CJK SC, PingFang SC, Microsoft YaHei, sans-serif"


def txt(x, y, t, size=13, fill="#4a5160", weight="400", anchor="start"):
    return ('<text x="%.1f" y="%.1f" font-family="%s" font-size="%s" font-weight="%s" '
            'fill="%s" text-anchor="%s">%s</text>' % (x, y, FONT, size, weight, fill, anchor, t))


def line(x1, y1, x2, y2, stroke="#e3e7ee", w=1.0, dash=None):
    d = ' stroke-dasharray="%s"' % dash if dash else ''
    return ('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="%.1f"%s/>'
            % (x1, y1, x2, y2, stroke, w, d))


def path(pts, stroke, w=1.6, opacity=1.0):
    if not pts:
        return ''
    d = 'M' + ' L'.join('%.1f %.1f' % p for p in pts)
    return ('<path d="%s" fill="none" stroke="%s" stroke-width="%.2f" stroke-linejoin="round" '
            'stroke-linecap="round" opacity="%.2f"/>' % (d, stroke, w, opacity))


def smooth(seq, n):
    """居中滑动平均，seq=[(t, v)]"""
    out, acc = [], collections.deque()
    for t, v in seq:
        acc.append(v)
        if len(acc) > n:
            acc.popleft()
        out.append((t, sum(acc) / len(acc)))
    return out


def svg_open(w, h, title="", sub=""):
    s = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">'
         % (w, h, w, h),
         '<rect width="%d" height="%d" fill="#ffffff"/>' % (w, h)]
    if title:
        s.append(txt(26, 36, title, 21, '#1f2430', '600'))
    if sub:
        s.append(txt(26, 60, sub, 12.8, '#6b7280'))
    return s


# ---------------------------------------------------------------- 图 1：1 小时细节
def fig_hour(S, out):
    W, H = 1240, 906
    L, R = 92, 1150
    ph, gap = 164, 76
    t0, t1 = S[0]['t'], S[-1]['t']
    span = (t1 - t0).total_seconds()
    X = lambda t: L + (t - t0).total_seconds() / span * (R - L)
    d = lambda h, m, sec=0: dt.datetime(2026, 10, 29, h, m, sec)
    TOP = 122
    BOTALL = TOP + 3 * ph + 2 * gap

    panels = [('GPU 板卡功耗 (W)', 0, 135, '#c0392b', 'p'),
              ('GPU 核心频率 clocks.sm (MHz)', 0, 1950, '#2c6fbb', 'clk'),
              ('GPU 3D 利用率 (橙) 与 CPU 占用 (灰) %', 0, 100, '#e08b1a', 'util')]

    s = svg_open(W, H, '附件这一个小时里发生了什么',
                 '10:57:09 → 11:57:07，3 s 采样，1150 点，全程无缺口（gap_before 全 0）')

    bands = [(d(10, 57, 9), d(10, 58, 18), '#fdecec', 'A  120 W / 1830 MHz / util 17%', 4),
             (d(10, 58, 18), d(11, 34), '#eef4fd', 'B  55.8 W / 1011 MHz / util 29%', 0),
             (d(11, 34), d(11, 57, 7), '#eaf6ee', 'C  50.4 W / 886 MHz / util 48%', 0)]
    for a, b, col, lab, dx in bands:
        xa, xb = X(a), X(b)
        s.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="%s"/>' % (xa, TOP, xb - xa, BOTALL - TOP, col))

    for i, (title, lo, hi, col, key) in enumerate(panels):
        top = TOP + i * (ph + gap)
        bot = top + ph
        s.append(txt(26, top - 26, title, 12.8, '#374151', '600'))
        s.append('<rect x="%d" y="%d" width="%d" height="%d" fill="none" stroke="#e5e8ee"/>' % (L, top, R - L, ph))
        for k in range(5):
            y = bot - ph * k / 4.0
            s.append(line(L, y, R, y, '#eef0f4'))
            s.append(txt(L - 8, y + 4, '%.0f' % (lo + (hi - lo) * k / 4.0), 11, '#9aa1ae', '400', 'end'))
        if key == 'p':
            yr = bot - ph * (45.0 / hi)
            s.append(line(L, yr, R, yr, '#7fb08a', 1.2, '6 5'))
            s.append(txt(L + 320, yr + 15, '低时钟基线 ≈ 45 W（900 MHz 附近，util 30–45%）', 11.5, '#2f7d46', '600'))
        raw = [(r['t'], r[key]) for r in S]
        if key == 'util':
            s.append(path([(X(t), bot - ph * max(0.0, min(1.0, v / 100.0))) for t, v in raw], '#b9bfc9', 1.0, .9))
        else:
            s.append(path([(X(t), bot - ph * max(0.0, min(1.0, v / hi))) for t, v in raw], col, 1.1, .45))
        sm = smooth(raw, 20)
        s.append(path([(X(t), bot - ph * max(0.0, min(1.0, v / hi))) for t, v in sm], col, 2.4, 1.0))

    for m in range(0, 61, 10):
        t = t0 + dt.timedelta(minutes=m)
        s.append(line(X(t), TOP, X(t), BOTALL, '#f2f4f7'))
        s.append(txt(X(t), BOTALL + 20, t.strftime('%H:%M'), 11.5, '#9aa1ae', '400', 'middle'))

    # 区段标签（放到底部做图例，避免压住曲线）
    lx = L
    for a, b, col, lab, dx in bands:
        s.append('<rect x="%d" y="%d" width="13" height="13" fill="%s" stroke="#d6dae2"/>'
                 % (lx, BOTALL + 40, col))
        s.append(txt(lx + 20, BOTALL + 51, lab, 12.8, '#374151', '600'))
        lx += 22 + len(lab) * 7.6

    xb = X(d(10, 58, 18))
    s.append(line(xb, TOP, xb, BOTALL, '#b0b7c3', 1, '4 4'))
    xb = X(d(11, 34))
    s.append(line(xb, TOP, xb, BOTALL, '#b0b7c3', 1, '4 4'))
    s.append(txt(L, BOTALL + 84, '10:58:18 起频率不再顶格：功耗 120 → 55 W（同一台机、同一个游戏）。', 12.5, '#b0392b', '600'))
    s.append(txt(L, BOTALL + 106, '11:34 起 util 30→48%、CPU 54→62%，但时钟 1011→886、功耗反而 −5.3 W。', 12.5, '#2f7d46', '600'))
    s.append(txt(26, H - 14, '数据源：genshin-tweak（nvidia-smi power.draw / clocks.sm / utilization.gpu，CPU 用 GetSystemTimes）；'
                             '深色为 20 点（≈60 s）滑动均值，浅色为原始 3 s 采样　|　' + '10:57–10:58 只有 76 秒，A 段只是上一个状态的回声', 11, '#a6adba'))
    s.append('</svg>')
    open(os.path.join(HERE, out), 'w', encoding='utf-8').write('\n'.join(s))


# ---------------------------------------------------------------- 图 2：两天全景
def fig_day(S, out):
    W, H = 1240, 640
    L, R, TOP, BOT = 86, 1150, 120, 470
    t0 = dt.datetime(2026, 10, 28, 11, 0)
    t1 = dt.datetime(2026, 10, 29, 12, 5)
    span = (t1 - t0).total_seconds()
    X = lambda t: L + (t - t0).total_seconds() / span * (R - L)
    PMAX, CMAX = 260.0, 2500.0
    Y = lambda v: BOT - (BOT - TOP) * max(0.0, min(1.0, v / PMAX))
    YC = lambda v: BOT - (BOT - TOP) * max(0.0, min(1.0, v / CMAX))

    b = collections.OrderedDict()
    for r in S:
        if not (t0 <= r['t'] <= t1):
            continue
        k = r['t'].replace(minute=r['t'].minute // 5 * 5, second=0)
        b.setdefault(k, []).append(r)
    mp = [(k, sum(x['p'] for x in b[k]) / len(b[k]), sum(x['clk'] for x in b[k]) / len(b[k])) for k in sorted(b)]

    s = svg_open(W, H, '两天全景：5 分钟均值',
                 '左轴 GPU 功耗 0–260 W（红线），右轴核心频率 0–2500 MHz（蓝线）；灰底＝休眠/关机无采样，红带＝「高频但有活也不多」的空转段')

    for a, z, lab in [(dt.datetime(2026, 10, 29, 0, 6), dt.datetime(2026, 10, 29, 7, 45), '休眠 7 小时 39 分'),
                      (dt.datetime(2026, 10, 28, 16, 16), dt.datetime(2026, 10, 28, 16, 58), ''),
                      (dt.datetime(2026, 10, 28, 18, 6), dt.datetime(2026, 10, 28, 18, 24), ''),
                      (dt.datetime(2026, 10, 28, 12, 14), dt.datetime(2026, 10, 28, 12, 42), ''),
                      (dt.datetime(2026, 10, 28, 11, 0), dt.datetime(2026, 10, 28, 11, 26), '')]:
        s.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="#eef0f4"/>' % (X(a), TOP, X(z) - X(a), BOT - TOP))
        if lab:
            s.append(txt((X(a) + X(z)) / 2, TOP + 24, lab, 11.5, '#9aa1ae', '400', 'middle'))

    for a, z, lab, anchor in [(dt.datetime(2026, 10, 28, 18, 30), dt.datetime(2026, 10, 28, 20, 0),
                               '1845 MHz / util 17% / 124 W', 'middle'),
                              (dt.datetime(2026, 10, 29, 9, 25), dt.datetime(2026, 10, 29, 10, 55),
                               '09:25–10:55　1830 MHz / util 16% / 119 W', 'middle'),
                              (dt.datetime(2026, 10, 28, 21, 30), dt.datetime(2026, 10, 28, 22, 15), '', 'middle')]:
        s.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="#d83c32" opacity="0.10"/>'
                 % (X(a), TOP, X(z) - X(a), BOT - TOP))
        if lab:
            s.append(txt((X(a) + X(z)) / 2, TOP + 24, lab, 11.5, '#b0392b', '600', anchor))

    for k in range(5):
        y = BOT - (BOT - TOP) * k / 4.0
        s.append(line(L, y, R, y, '#eaeef4'))
        s.append(txt(L - 10, y + 4, '%.0f' % (PMAX * k / 4.0), 11, '#9aa1ae', '400', 'end'))
    for k in range(6):
        yy = YC(CMAX * k / 5.0)
        s.append(line(R, yy, R + 5, yy, '#dfe3ea'))
        s.append(txt(R + 11, yy + 4, '%.0f' % (CMAX * k / 5.0), 11, '#9aa1ae'))
    s.append(txt(26, TOP - 12, 'W', 11.5, '#c0392b', '600'))
    s.append(txt(R + 10, TOP - 12, 'MHz', 11.5, '#2c6fbb', '600'))

    seg_p, seg_c, prev = [], [], None
    for k, p, c in mp:
        if prev is not None and (k - prev).total_seconds() > 900:
            s.append(path(seg_p, '#c0392b', 1.7))
            s.append(path(seg_c, '#2c6fbb', 1.2, .8))
            seg_p, seg_c = [], []
        seg_p.append((X(k), Y(p)))
        seg_c.append((X(k), YC(c)))
        prev = k
    s.append(path(seg_p, '#c0392b', 1.7))
    s.append(path(seg_c, '#2c6fbb', 1.2, .8))

    for dd in [dt.datetime(2026, 10, 28, 12), dt.datetime(2026, 10, 28, 18), dt.datetime(2026, 10, 29, 0),
               dt.datetime(2026, 10, 29, 6), dt.datetime(2026, 10, 29, 12)]:
        s.append(line(X(dd), TOP, X(dd), BOT, '#f2f4f7'))
        s.append(txt(X(dd), BOT + 18, dd.strftime('%m-%d %H:%M'), 11, '#9aa1ae', '400', 'middle'))

    ly = BOT + 54
    s.append(line(L, ly, L + 26, ly, '#c0392b', 2.4))
    s.append(txt(L + 34, ly + 4, 'GPU 功耗', 12, '#c0392b', '600'))
    s.append(line(L + 180, ly, L + 206, ly, '#2c6fbb', 2.0))
    s.append(txt(L + 214, ly + 4, 'GPU 核心频率', 12, '#2c6fbb', '600'))
    s.append(txt(L, ly + 30, '250 W 上限只在 10-29 07:45–08:00 被顶到过 14 分钟，其余时间连 135 W 都没到 ——'
                             '两天的瓶颈从来不是功率上限。', 12, '#6b7280'))
    s.append(txt(L, ly + 52, '最离谱的是 10-28 21:30–22:15：CPU 全程 7%、GPU 3D util 只有 4%，GPU 却锁在 1845 MHz 吃 113 W。'
                             '（同一晚 20:30–21:00 正常待机只有 30 W / 400 MHz）', 12, '#6b7280'))
    s.append(txt(26, H - 16, '数据源：full-snapshot.csv（17698 行，12:13 快照，有效采样覆盖 15.3 小时）', 11, '#a6adba'))
    s.append('</svg>')
    open(os.path.join(HERE, out), 'w', encoding='utf-8').write('\n'.join(s))


# ---------------------------------------------------------------- 图 3：功率面
def fig_surface(S, out):
    W, H = 1240, 800
    L, R, TOP, BOT = 130, 880, 120, 690
    cb0, cstep = 200, 100
    ustep = 5
    cols = list(range(cb0, 1900, cstep))
    rows = list(range(0, 100, ustep))
    cell = collections.defaultdict(list)
    for r in S:
        if r['clk'] < cb0:
            continue
        ci = min(len(cols) - 1, int((r['clk'] - cb0) // cstep))
        ri = min(len(rows) - 1, int(r['util'] // ustep))
        cell[(ci, ri)].append(r['p'])
    cw = (R - L) / len(cols)
    ch = (BOT - TOP) / len(rows)

    s = svg_open(W, H, 'GPU 功耗面：横轴频率、纵轴利用率、颜色＝该格平均功耗',
                 '每格是该频率×利用率区间的平均瓦数（n≥15 才标数字）；颜色只跟功耗走，所以“同色横条”＝这些工况一样费电')
    for ci in range(len(cols)):
        for ri in range(len(rows)):
            v = cell.get((ci, ri))
            x = L + ci * cw
            y = BOT - (ri + 1) * ch
            if not v:
                s.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="#f6f7f9"/>'
                         % (x, y, cw - 1, ch - 1))
                continue
            m = sum(v) / len(v)
            s.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>'
                     % (x, y, cw - 1, ch - 1, hexc(ramp(m, 20, 240, PWR))))
            if len(v) >= 15:
                s.append(txt(x + cw / 2, y + ch / 2 + 4, '%.0f' % m, 10.5,
                             '#232a36' if m < 165 else '#ffffff', '500', 'middle'))
    s.append('<rect x="%d" y="%d" width="%d" height="%d" fill="none" stroke="#dfe3ea"/>' % (L, TOP, R - L, BOT - TOP))
    for ci in range(len(cols) + 1):
        s.append(txt(L + ci * cw, BOT + 18, '%d' % (cb0 + ci * cstep), 10.5, '#9aa1ae', '400', 'middle'))
    for ri in range(len(rows) + 1):
        if (ri * ustep) % 20 == 0:
            s.append(txt(L - 10, BOT - ri * ch + 4, '%d' % (ri * ustep), 11, '#9aa1ae', '400', 'end'))
    s.append(txt((L + R) / 2, BOT + 44, 'GPU 核心频率 clocks.sm (MHz)', 13, '#374151', '600', 'middle'))
    ymid = (TOP + BOT) / 2
    s.append('<text x="46" y="%.1f" font-family="%s" font-size="13" font-weight="600" fill="#374151" '
             'transform="rotate(-90 46 %.1f)" text-anchor="middle">GPU 3D 利用率 utilization.gpu (%%)</text>'
             % (ymid, FONT, ymid))

    nx = 930
    notes = [('三列横着读', '#1f2430', '600', 13),
             ('900 MHz　util 20%→60%　　41 → 45 W', '#2f7d46', '400', 12.2),
             ('1300 MHz　util 0→65%　　　 68 → 72 W', '#2f7d46', '400', 12.2),
             ('1830 MHz　util 5→60%　　 114 → 205 W', '#b0392b', '400', 12.2),
             ('', '#6b7280', '400', 9),
             ('低频段 util 基本不要钱，只有顶格', '#4a5160', '400', 12.2),
             ('频率才“活越多越贵”。11:34 之后那', '#4a5160', '400', 12.2),
             ('段工况在 886 MHz，正好落在免费的那', '#4a5160', '400', 12.2),
             ('几条横带上 —— 这就是“加了活却没', '#4a5160', '400', 12.2),
             ('加功耗”的全部原因。', '#4a5160', '400', 12.2),
             ('', '#6b7280', '400', 9),
             ('最刺眼的是 1830 MHz 那一列：', '#b0392b', '600', 12.2),
             ('util 10% 也要 114–121 W，比 400 MHz', '#b0392b', '400', 12.2),
             ('待机的 26 W 多出近 90 W，而这 90 W', '#b0392b', '400', 12.2),
             ('几乎没换来任何活。09:25–10:55 就', '#b0392b', '400', 12.2),
             ('停在这一格：1.5 小时 ≈ 112 Wh。', '#b0392b', '400', 12.2),
             ('', '#6b7280', '400', 9),
             ('还能看到 util 越高功耗略低（45→41），', '#6b7280', '400', 11.8),
             ('因为有活干时驱动才肯降频降压。', '#6b7280', '400', 11.8),
             ('所以 utilization.gpu 不能当', '#6b7280', '400', 11.8),
             ('“工作量/能耗”的代理指标。', '#6b7280', '400', 11.8)]
    yy = TOP + 4
    for t, c, w, sz in notes:
        if t:
            s.append(txt(nx, yy, t, sz, c, w))
        yy += 25 if sz > 11.5 else 13
    s.append(txt(26, H - 16, '数据源：full-snapshot.csv 全量 17691 行，按 100 MHz × 5% 分格；空格＝该组合没出现过', 11, '#a6adba'))
    s.append('</svg>')
    open(os.path.join(HERE, out), 'w', encoding='utf-8').write('\n'.join(s))


def main():
    hour = load(os.path.join(HERE, 'raw.csv'))
    snap = os.path.join(HERE, 'full-snapshot.csv')
    src = snap if os.path.exists(snap) else os.path.join(HERE, '../genshin_tweak_power.csv')
    day = [r for r in load(src) if r['p'] >= 0]
    fig_hour(hour, 'fig1_hour.svg')
    fig_day(day, 'fig2_day.svg')
    fig_surface(day, 'fig3_surface.svg')
    print('figs written')


if __name__ == '__main__':
    main()
