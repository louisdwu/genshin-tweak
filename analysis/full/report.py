#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""report.py —— 由 ctx 生成 report.md、dashboard.html 与派生 CSV 表。"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
import os
import re

import numpy as np

import gta
from gta import fmt, fmt_wh, hms

FIGS = [
    ('fig01_overview', '全景：24 小时 52 分钟', '原始 3 s 采样、缺口、变化点'),
    ('fig02_dist', '分布', '四变量的直方图与分位数'),
    ('fig03_gmm', '功耗档位（GMM）', '4 个 σ=3~5 W 的窄峰 + 1 个宽尾'),
    ('fig04_surface', '功耗面', '频率 × 利用率 → 平均功耗'),
    ('fig05_pclk', '功耗 ~ 频率', '联合分布与分温度带拟合'),
    ('fig06_states', '工况状态', 'k-means 五状态与能量占比'),
    ('fig07_trans', '状态转移与驻留', '一阶转移矩阵、游程'),
    ('fig08_acf', '自相关', '时间结构与有效样本量'),
    ('fig09_cp', '变化点', '二分割检测出的 15 个换挡点'),
    ('fig10_energy', '每小时电量', '按状态堆叠的 Wh'),
    ('fig11_sampling', '采样质量', '间隔分布与相邻样本跳变'),
    ('fig12_detect', '可检测性', '相邻窗口差与 95% 判据'),
    ('fig13_fitdiag', '回归诊断', '残差结构与模型对比'),
    ('fig14_budget', '电量账单', '测到的与没测到的'),
]


def md_table(headers, rows, aligns=None):
    out = ['| ' + ' | '.join(headers) + ' |',
           '|' + '|'.join(['---'] * len(headers)) + '|']
    for r in rows:
        out.append('| ' + ' | '.join(str(c) for c in r) + ' |')
    return '\n'.join(out)


def _r(x, nd=1, unit=''):
    return fmt(x, nd) + unit


# ============================================================ 表
def write_tables(ctx, outdir):
    os.makedirs(outdir, exist_ok=True)
    s, km = ctx['s'], ctx['km']
    # 分钟
    with open(os.path.join(outdir, 'minute.csv'), 'w', encoding='utf-8') as f:
        f.write('time,n,cov_s,p_avg,p_max,clk_avg,clk_max,util_avg,util_max,temp,cpu,wh\n')
        for r in ctx['minutes']:
            f.write('%s,%d,%.0f,%.2f,%.1f,%.0f,%.0f,%.1f,%.1f,%.1f,%.1f,%.4f\n' % (
                r['t'].strftime('%Y-%m-%d %H:%M:00'), r['n'], r['cov'], r['p'], r['p_max'],
                r['clk'], r['clk_max'], r['util'], r['util_max'], r['temp'], r['cpu'], r['wh']))
    # 小时
    with open(os.path.join(outdir, 'hour.csv'), 'w', encoding='utf-8') as f:
        head = ['time'] + ['wh_' + st['name'].split(' ')[0] for st in km['stats']] + ['wh_total']
        f.write(','.join(head) + '\n')
        for h, v in ctx['hour_energy']:
            f.write(h.strftime('%Y-%m-%d %H:00') + ',' +
                    ','.join('%.3f' % x for x in v) + ',%.3f\n' % v.sum())
    # 状态
    with open(os.path.join(outdir, 'state.csv'), 'w', encoding='utf-8') as f:
        f.write('state,name,n,hours,wh,share,p_mean,p_sd,clk_mean,util_mean,cpu_mean,temp_mean\n')
        for st in km['stats']:
            f.write('%d,%s,%d,%.3f,%.1f,%.4f,%.2f,%.2f,%.0f,%.1f,%.1f,%.1f\n' % (
                st['label'], st['name'], st['n'], st['hours'], st['wh'], st['share'],
                st['p'], st['p_sd'], st['clk'], st['util'], st['cpu'], st['temp']))
    # 变化点
    with open(os.path.join(outdir, 'changepoints.csv'), 'w', encoding='utf-8') as f:
        f.write('time,before_w,after_w,delta_w\n')
        for c in ctx['cps']:
            f.write('%s,%.1f,%.1f,%.1f\n' % (c['t'].strftime('%Y-%m-%d %H:%M:%S'),
                                             c['before'], c['after'], c['delta']))
    # 汇总统计
    with open(os.path.join(outdir, 'summary.csv'), 'w', encoding='utf-8') as f:
        keys = ['n', 'mean', 'sd', 'cv', 'se', 'min', 'p1', 'p5', 'q1', 'median', 'q3',
                'p95', 'p99', 'max', 'iqr', 'mad', 'skew', 'kurt', 'outliers']
        f.write('variable,dataset,' + ','.join(keys) + '\n')
        for k, _ in ctx['keys']:
            for tag, d in (('full', ctx['desc'][k]), ('hour1', ctx['desc1'][k])):
                f.write(f'{k},{tag},' + ','.join('%.4f' % d[x] if isinstance(d[x], float) else str(d[x])
                                                 for x in keys) + '\n')
    # 关系
    with open(os.path.join(outdir, 'windows.csv'), 'w', encoding='utf-8') as f:
        f.write('minutes,state,mean_a,mean_b,delta\n')
        for m in (5, 10, 20):
            for st, a, b, d in ctx['win'][m]['pairs']:
                f.write('%d,%d,%.2f,%.2f,%.3f\n' % (m, st, a, b, d))
    # 缺口
    with open(os.path.join(outdir, 'gaps.csv'), 'w', encoding='utf-8') as f:
        f.write('start,end,duration_s,duration_h\n')
        for g in ctx['gaps']:
            f.write('%s,%s,%.0f,%.3f\n' % (g['start'].strftime('%Y-%m-%d %H:%M:%S'),
                                           g['end'].strftime('%Y-%m-%d %H:%M:%S'), g['dur'], g['dur'] / 3600))
    # 事件
    with open(os.path.join(outdir, 'events.csv'), 'w', encoding='utf-8') as f:
        f.write('time,mode,detail,pre10min_w,post10min_w\n')
        for e in ctx['ev_stats']:
            f.write('%s,%s,"%s",%s,%s\n' % (e['ev']['time'].strftime('%Y-%m-%d %H:%M:%S'),
                                            e['ev']['mode'], e['ev']['detail'].replace('"', "'"),
                                            '' if not math.isfinite(e['pre']) else '%.1f' % e['pre'],
                                            '' if not math.isfinite(e['post']) else '%.1f' % e['post']))
    return ['minute.csv', 'hour.csv', 'state.csv', 'changepoints.csv', 'summary.csv',
            'windows.csv', 'gaps.csv', 'events.csv']


# ============================================================ Markdown
def render_md(ctx):
    s, km, desc = ctx['s'], ctx['km'], ctx['desc']
    k = km['k']
    st = km['stats']
    f = ctx['fits']['clk_util']
    w5, w10 = ctx['win'][5], ctx['win'][10]
    top_hours = sorted(ctx['hour_energy'], key=lambda t: -t[1].sum())[:8]
    tot_wh = ctx['energy_total_wh']
    big = [x for x in st if x['clk'] > 1500]
    big_wh = sum(x['wh'] for x in big)
    big_h = sum(x['hours'] for x in big)
    peak_day = max(ctx['day_energy'], key=lambda x: x[1])
    L = []
    A = L.append

    A('# genshin-tweak 功耗数据 · 全面统计分析')
    A('')
    A(f'> 生成时间：{ctx["t_build"]:%Y-%m-%d %H:%M}　数据快照：`{os.path.basename(ctx["snap"])}`'
      f'（{s.n_raw} 行，用 {len(s)} 行）　原始文件：`genshin_tweak_power.csv`（实时增长）')
    A(f'> 样本：{s.t0:%Y-%m-%d %H:%M:%S} → {s.datetime(len(s)-1):%Y-%m-%d %H:%M:%S}，'
      f'墙钟 {ctx["wall_h"]:.2f} h，有效覆盖 {ctx["covered_h"]:.2f} h（{ctx["duty"]*100:.1f}%）')
    A('')

    # -------- 摘要
    A('## 0. 摘要')
    A('')
    A(f'- **总电量 {tot_wh:.0f} Wh**（GPU 板卡口径，{ctx["energy_hours"]:.2f} h 有效采样），'
      f'平均 **{tot_wh/ctx["energy_hours"]:.1f} W**。最重的一天是 {peak_day[0]}：{peak_day[1]:.0f} Wh / {peak_day[2]:.2f} h。')
    A(f'- **功耗是「台阶」不是连续谱**：一维 GMM 给出 5 个分量，其中 4 个的 σ 只有 3–5 W'
      f'（{"/".join("%.0f"%c[1] for c in ctx["gmm"]["comps"][:4])} W）。'
      f'只要落进某个档位，瞬时功耗几乎是确定的。')
    mg = ctx['marginal']
    A(f'- **功耗由频率主导，但「每 100 MHz 值多少钱」取决于负载**：空载时 {mg[0]["w_per_100mhz"]:.1f} W/100 MHz，'
      f'util 20% 时 {mg[2]["w_per_100mhz"]:.1f} W，util 60% 时 {mg[4]["w_per_100mhz"]:.1f} W'
      f'（含交互项的模型 R²={ctx["fits"]["clk_util_int"]["r2"]:.3f}）。'
      f'而 600–1400 MHz 这一段，util 从 20% 涨到 60% 总功耗只差几瓦 —— 静态项在那一档是主导。')
    A(f'- **一半电量花在「满频」上**：S3+S4 两个状态（频率 >1500 MHz）合计 {big_h:.2f} h / **{big_wh:.0f} Wh（{big_wh/tot_wh*100:.0f}%）**，'
      f'其中 S3「满频空转」{st[3]["hours"]:.2f} h / {st[3]["wh"]:.0f} Wh，util 只有 {st[3]["util"]:.1f}%。')
    A(f'- **数据的证据量远比样本数少**：lag-1 自相关 0.79，有效独立样本比例仅 **{ctx["neff_ratio"]*100:.1f}%**。'
      f'相邻 5 分钟窗口的功耗差中位 {w5["med"]:.2f} W、p95 {w5["p95"]:.2f} W → '
      f'**5 W 级差异刚好在检测极限上**（判据 {w5["mde"]:.1f} W）。')
    A(f'- **测的只是 GPU 板卡**：CPU 只有占用率没有瓦数，encoder/decoder 利用率、显存频率、power.limit 都没采。'
      f'整机功耗这块是空白（见 §12）。')
    A('')

    # -------- 1
    A('## 1. 数据与方法')
    A('')
    A('| 项目 | 值 |')
    A('|---|---|')
    A(f'| 原始行数 / 有效行数 | {s.n_raw} / {len(s)}（丢弃 {s.n_drop} 行：`power.draw` 为 `[N/A]` 或解析失败） |')
    A(f'| 采样周期 | 3 s（`PowerHistory.SampleIntervalMs=3000`），实测中位 {np.median(ctx["jitter"]):.2f} s |')
    A(f'| 断点阈值 | 15 s（与 C# `GapThresholdMs` 一致），缺口处不连线、不跨缺口算能量 |')
    A(f'| 能量积分 | 按「后一样本功率 × 其前面时间片（≤15 s）」积分，与设备记录方式一致 |')
    A(f'| 数据快照 | `{os.path.basename(ctx["snap"])}`（原始文件仍在增长，用快照保证可复现） |')
    A('')
    A('全部统计由 `gta.py`（数据 + 统计原语）、`pipeline.py`（pipeline）、`figures.py`（图）三部分组成，'
      '只依赖 numpy；图是手写 SVG，再由 chromium 截成 PNG，同一份 SVG 也内嵌进 `dashboard.html`。')
    A('')

    # -------- 2
    A('## 2. 数据完整性')
    A('')
    A(f'墙钟 {ctx["wall_h"]:.2f} h 里只有 **{ctx["covered_h"]:.2f} h** 有采样（{ctx["duty"]*100:.1f}%），'
      f'其余是休眠/关机。共 {len(ctx["gaps"])} 个缺口：')
    A('')
    rows = []
    for g in sorted(ctx['gaps'], key=lambda x: -x['dur']):
        rows.append([g['start'].strftime('%m-%d %H:%M:%S'), g['end'].strftime('%H:%M:%S'),
                     hms(g['dur']), f"{g['dur']/3600:.2f} h"])
    A(md_table(['断开于', '恢复于', '时长', '小时'], rows))
    A('')
    A(f'采样间隔：中位 {np.median(ctx["jitter"]):.2f} s、p95 {np.percentile(ctx["jitter"],95):.2f} s、'
      f'最大 {ctx["jitter"].max():.2f} s（>4 s 的占 {np.mean(ctx["jitter"]>4)*100:.1f}%）。')
    A('')
    A(f'**相邻 3 s 样本的功耗跳变**：中位 {ctx["dp"]["median"]:.1f} W，p90 {ctx["dp"]["p90"]:.1f} W，'
      f'p99 {ctx["dp"]["p99"]:.1f} W，**有 {ctx["dp"]["big_frac"]*100:.1f}% 的相邻样本差 >20 W**。'
      '这就是单点读数的噪声底——拿一个点去比另一个点是没有意义的。')
    A('')

    # -------- 3
    A('## 3. 描述统计')
    A('')
    A('全量（n=%d）：' % len(s))
    A('')
    rows = []
    for key, name in ctx['keys']:
        d = desc[key]
        rows.append([name, f"{d['mean']:.1f}", f"{d['sd']:.1f}", f"{d['cv']*100:.0f}%",
                     f"{d['median']:.1f}", f"{d['p5']:.1f}", f"{d['p95']:.1f}",
                     f"{d['min']:.0f}–{d['max']:.0f}", f"{d['skew']:.2f}", f"{d['kurt']:.1f}"])
    A(md_table(['变量', '均值', 'sd', 'CV', '中位', 'p5', 'p95', '范围', '偏度', '峰度'], rows))
    A('')
    A('同一台机器，附件那 1 小时切片（n=%d）与全量的差异：' % len(ctx['s1']))
    A('')
    rows = []
    for key, name in ctx['keys']:
        a, b = ctx['desc1'][key], desc[key]
        rows.append([name, f"{a['mean']:.1f} / {b['mean']:.1f}", f"{a['sd']:.1f} / {b['sd']:.1f}",
                     f"{a['median']:.1f} / {b['median']:.1f}", f"{a['p95']:.1f} / {b['p95']:.1f}"])
    A(md_table(['变量', '均值（1h / 全量）', 'sd', '中位', 'p95'], rows))
    A('')
    A('注意 CPU 占用率：均值 %.1f%% 但中位 %.1f%%、sd %.1f —— 双峰（BGI 后台空转 vs 游戏/转码），'
      '用均值描述它会同时失真于两端。' % (desc['cpu']['mean'], desc['cpu']['median'], desc['cpu']['sd']))
    A('')

    # -------- 4
    A('## 4. 功耗的「档位」')
    A('')
    A('![功耗档位](charts/fig03_gmm.png)')
    A('')
    A('一维高斯混合（EM，BIC 选 k=%d）：' % ctx['gmm']['k'])
    A('')
    rows = [[f"{i+1}", f"{c[1]:.0f} W", f"{c[0]*100:.0f}%", f"{c[2]:.1f} W", f"{c[2]/c[1]*100:.0f}%"]
            for i, c in enumerate(ctx['gmm']['comps'])]
    A(md_table(['分量', '均值', '权重', 'σ', 'σ/μ'], rows))
    A('')
    A('前 4 个分量的 σ 只有 3–5 W（相对离散度 3–12%）：**功耗几乎是阶跃的**，对应电源管理在几个离散 V/f 点之间切换。'
      '第 5 个分量（σ=49 W）是「真的在跑东西」时的连续负载区。')
    A('')
    A('![分布](charts/fig02_dist.png)')
    A('')

    # -------- 5
    A('## 5. 功耗面与功率–频率律')
    A('')
    A('![功耗面](charts/fig04_surface.png)')
    A('')
    A(md_table(['模型', 'R²', '调整 R²', 'RMSE (W)', 'AIC'], [
        ['P ~ clk'] + [f"{ctx['fits']['clk']['r2']:.3f}", f"{ctx['fits']['clk']['adj_r2']:.3f}",
                       f"{ctx['fits']['clk']['rmse']:.1f}", f"{ctx['fits']['clk']['aic']:.0f}"],
        ['P ~ clk + util'] + [f"{ctx['fits']['clk_util']['r2']:.3f}", f"{ctx['fits']['clk_util']['adj_r2']:.3f}",
                              f"{ctx['fits']['clk_util']['rmse']:.1f}", f"{ctx['fits']['clk_util']['aic']:.0f}"],
        ['P ~ clk + util + clk·util'] + [f"{ctx['fits']['clk_util_int']['r2']:.3f}",
                                         f"{ctx['fits']['clk_util_int']['adj_r2']:.3f}",
                                         f"{ctx['fits']['clk_util_int']['rmse']:.1f}",
                                         f"{ctx['fits']['clk_util_int']['aic']:.0f}"],
        ['P ~ clk + util + T'] + [f"{ctx['fits']['clk_util_temp']['r2']:.3f}",
                                  f"{ctx['fits']['clk_util_temp']['adj_r2']:.3f}",
                                  f"{ctx['fits']['clk_util_temp']['rmse']:.1f}",
                                  f"{ctx['fits']['clk_util_temp']['aic']:.0f}"],
    ]))
    A('')
    A('**频率的边际成本（含交互项模型，每 100 MHz）**：')
    A('')
    A(md_table(['利用率', '每 100 MHz 的功耗增量'],
               [[f'{m["util"]}%', f'{m["w_per_100mhz"]:.1f} W'] for m in ctx['marginal']]))
    A('')
    A('这才是正确的读法：空载时抬频只付「静态/漏电」的钱（≈4.4 W/100 MHz），有活时还要付动态功耗（≈13 W/100 MHz）。'
      '所以「把空闲时的频率压下来」这件事的收益，比「在满载时省功耗」更干净。')
    A('')
    A('分温度带的斜率（W/GHz）：')
    A('')
    A(md_table(['温度带', 'n', '斜率', '平均功耗', '平均频率'],
               [[f"{b['lo']:.0f}–{b['hi']:.0f}℃", b['n'], f"{b['fit']['beta'][1]*1000:.1f}",
                 f"{b['p_mean']:.0f} W", f"{b['clk_mean']:.0f} MHz"] for b in ctx['band_fits']]))
    A('')
    A('除最热的 60 ℃+ 那一档（它被 250 W 的满载样本主导、已经饱和）之外，斜率随温度升高单调变大。'
      '但**不能全算成漏电**：高功耗本身会把温度推高（反向因果）。'
      '滞后相关显示温度与功耗的最佳对齐在 lag=0，也就是说在这个采样尺度上分不清先后 —— 想分辨需要更长的时间基线。')
    A('')
    A('![功耗-频率](charts/fig05_pclk.png)')
    A('')

    # -------- 6
    A('## 6. 工况状态（k-means，k=%d）' % k)
    A('')
    A('在**工况空间**（log 频率、3D 利用率、CPU 占用——刻意不放功耗，避免用功耗解释功耗）里聚类。'
      f'轮廓系数：k=2 最高（{ctx["km_sil_best"][2]:.3f}），k={k} 为 {km["sil"]:.3f}；'
      '取 k=%d 是因为它与一维 GMM 独立给出的档位数一致，可解释性远高于二分。' % k)
    A('')
    rows = []
    for x in st:
        rows.append([x['name'], x['n'], f"{x['hours']:.2f} h", f"{x['wh']:.0f} Wh",
                     f"{x['share']*100:.1f}%", f"{x['p']:.0f}±{x['p_sd']:.0f}",
                     f"{x['clk']:.0f}", f"{x['util']:.1f}%", f"{x['cpu']:.1f}%", f"{x['temp']:.1f}"])
    A(md_table(['状态', 'n', '时长', '电量', '电量占比', '功耗 W', '频率 MHz', 'util', 'CPU', '温度'], rows))
    A('')
    A(f'状态抖动率（未平滑时短于 5 点的游程占比）：**{ctx["flicker"]*100:.1f}%** —— '
      '驱动会在相邻 V/f 档之间高频跳变，所以统计前做了 3 点去抖（原始序列的中位驻留只有 3–6 s）。')
    A('')
    A('![状态](charts/fig06_states.png)')
    A('')

    # -------- 7
    A('## 7. 状态动力学')
    A('')
    A('![转移](charts/fig07_trans.png)')
    A('')
    Pm, Cm = ctx['trans']['P'], ctx['trans']['C']
    rows = []
    for i in range(k):
        rows.append([km['names'][i]] + [f"{Pm[i,j]*100:.0f}%" for j in range(k)])
    A(md_table(['从 \\ 到'] + [f'S{j}' for j in range(k)], rows))
    A('')
    A(md_table(['状态', '段数', '中位驻留', '平均驻留', '最长'],
               [[km['names'][i], ctx['trans']['dwell'][i]['n'],
                 hms(ctx['trans']['dwell'][i]['med']), hms(ctx['trans']['dwell'][i]['mean']),
                 hms(ctx['trans']['dwell'][i]['max'])] for i in range(k)]))
    A('')
    off = int(Cm.sum() - np.trace(Cm))
    A(f'样本级的一步转移共 {int(Cm.sum()):,} 次，其中**真正换了状态的只有 {off} 次**'
      f'（约每 {ctx["covered_h"]*3600/max(off,1)/60:.1f} 分钟一次）—— 这段时间的工况极其稳定。'
      '非对角项几乎全部落在 S3⇄S4（满频空转 ⇄ 满频低载），也就是「GPU 一直顶在 1.8 GHz，只是偶尔来点活」。')
    A('')

    # -------- 8
    A('## 8. 时间结构：自相关与有效样本量')
    A('')
    A('![自相关](charts/fig08_acf.png)')
    A('')
    A(md_table(['滞后', '功耗 ACF', 'util ACF', 'CPU ACF'], [
        [f'{lag*3} s', f"{ctx['acf']['p'][lag]:.3f}", f"{ctx['acf']['util'][lag]:.3f}",
         f"{ctx['acf']['cpu'][lag]:.3f}"] for lag in (1, 10, 20, 60, 100)]))
    A('')
    A(f'lag-1 相关 0.79，5 分钟时仍有 0.44，**有效独立样本比例只有 {ctx["neff_ratio"]*100:.1f}%**。'
      f'{len(s)} 个样本折算下来只相当于约 {len(s)*ctx["neff_ratio"]:.0f} 个独立观测。'
      '做假设检验时必须用这个数，否则 p 值会小得离谱。')
    A('')

    # -------- 9
    A('## 9. 变化点')
    A('')
    A('![变化点](charts/fig09_cp.png)')
    A('')
    rows = []
    for i, c in enumerate(ctx['cps'][:10]):
        rows.append([i + 1, c['t'].strftime('%m-%d %H:%M'), f"{c['before']:.0f} W",
                     f"{c['after']:.0f} W", f"{c['delta']:+.0f} W"])
    A(md_table(['#', '时间', '前 2 分钟', '后 2 分钟', 'Δ'], rows))
    A('')
    A(f"共 {len(ctx['cps'])} 个显著变化点（二分割，L2 代价 + BIC 型惩罚，最小段 2 分钟）。"
      '注意 09:25–10:55 那 90 分钟里**一个变化点都没有** —— 一直停在同一个状态，'
      '安静、稳定、持续烧着 ~119 W。')
    A('')

    # -------- 10
    A('## 10. 能量核算')
    A('')
    A('![每小时电量](charts/fig10_energy.png)')
    A('')
    A(md_table(['日期', '覆盖时长', 'GPU 电量', '平均'],
               [[d, f"{h:.2f} h", f"{e:.0f} Wh", f"{e/h:.1f} W"] for d, e, h in ctx['day_energy']]))
    A('')
    A('电量最重的 8 个小时：')
    A('')
    rows = []
    for h, v in top_hours:
        rows.append([h.strftime('%m-%d %H:00'), f"{v.sum():.0f}"] + [f"{x:.0f}" for x in v])
    A(md_table(['小时', 'Wh 合计'] + [f'{x["name"].split(" ")[0]} Wh' for x in st], rows))
    A('')
    A(f'两天合计 **{tot_wh:.0f} Wh**；若只算 S3+S4（满频）则是 **{big_wh:.0f} Wh（{big_wh/tot_wh*100:.0f}%）**，'
      f'只花在 {big_h:.2f} h 上。'
      f'反过来说：如果把满频段（实测均值 121 W）整体压到 1350 MHz 一档（实测 ≈70 W），'
      f'在这 {big_h:.2f} h 上大约能省 **{(big_wh - big_h*70):.0f} Wh**。'
      '注意这是「等速」外推——真降频后同样的活要跑更久，实际收益会打折扣，量级参考即可。')
    A('')

    # -------- 11
    A('## 11. 温度')
    A('')
    d = desc['temp']
    A(f'温度：中位 {d["median"]:.0f} ℃、p95 {d["p95"]:.0f} ℃、最高 {d["max"]:.0f} ℃，sd {d["sd"]:.1f}；'
      f'与功耗的相关性 {np.corrcoef(s.p, s.temp)[0,1]:.3f}，与频率 {np.corrcoef(s.clk, s.temp)[0,1]:.3f}。')
    A('')
    A('按状态看稳态温度：' + '、'.join(f"{x['name'].split(' ')[0]} {x['temp']:.0f}℃" for x in st) + '。')
    A('')
    A('滞后相关（功耗 vs 温度，±2 分钟窗口）的峰值在 lag=0：这个采样尺度上两者同步，分不出因果方向。'
      '真要分离漏电，得在恒功率下改变风扇/环境温度，或者用更长的停机-升温曲线。')
    A('')

    # -------- 12
    A('## 12. 你能分辨多大的差异？（功效分析）')
    A('')
    A('![可检测性](charts/fig12_detect.png)')
    A('')
    A(md_table(['窗口', '对数', '|Δ| 中位', '|Δ| p95', '95% 判据'],
               [[f'{m} min', ctx['win'][m]['n'], f"{ctx['win'][m]['med']:.2f} W",
                 f"{ctx['win'][m]['p95']:.2f} W", f"{ctx['win'][m]['mde']:.2f} W"]
                for m in (5, 10, 20) if ctx['win'][m]['n'] >= 4]))
    A('')
    A('做法：在同一连续段、同一状态内，把时间切成相邻的等长窗口，统计「两窗均值之差」。'
      '这是对你自己的数据做的**实测**噪声底，比任何理论假设都靠谱。')
    A('')
    A(f'结论：5 分钟窗口的判据 ≈ {w5["mde"]:.1f} W，10 分钟 ≈ {w10["mde"]:.1f} W。'
      '上一轮测到的 **−5.3 W** 正好骑在这条线上——它显著（SE 0.8 W），但离「能放心下结论」只差一点。'
      '要判断 2–3 W 级别的差异，得拉到 20–30 分钟，或者换多指标（CPU+GPU+整机）联合判断。')
    A('')

    # -------- 13
    A('## 13. 采样质量')
    A('')
    A('![采样](charts/fig11_sampling.png)')
    A('')
    A(f'间隔中位 {np.median(ctx["jitter"]):.2f} s、抖动（sd）{ctx["jitter"].std():.3f} s。'
      '采样由 3 s 的 Timer + 每次 spawn 一个 `nvidia-smi` 进程实现，'
      '进程启动的耗时被算进了间隔，所以会出现 3.0 / 4.0 s 两簇。')
    A('')

    # -------- 14
    A('## 14. 缺失的统计（重要）')
    A('')
    A('![账单](charts/fig14_budget.png)')
    A('')
    A('| 缺口 | 影响 | 补法 |')
    A('|---|---|---|')
    A('| CPU 瓦数 | 只有占用率；而且工具自己会用 `powercfg` 限频，跨预设比 CPU% 不成立 | PDH `\\Power Meter(*)\\Power` / LibreHardwareMonitor |')
    A('| `utilization.encoder/decoder` | 无法判断转码是否走 GPU | 加进 nvidia-smi query |')
    A('| `clocks.mem` | 600–1000 MHz 那段 45 W 的「地板」无法解释 | 同上 |')
    A('| `power.limit` / `pstate` / throttle reason | 分不清「被限」还是「没活」 | 同上 |')
    A('| 整机功率 | GPU 板卡 ≠ 墙插 | 插座功率计 / 电池放电率 |')
    A('| 进程归因 | 不知道谁在吃 GPU | PDH `\\GPU Engine(*)\\Utilization Percentage` |')
    A('| `power.draw` 是瞬时值 | 与 3 s 平均不等价 | 用 `power.draw.average`（1 s 窗）或 NVML |')
    A('')

    # -------- 15
    A('## 15. 事件对齐')
    A('')
    A(f'日志里共 {len(ctx["modes_all"])} 条模式事件，但**落在数据窗口内、且两侧都有数据的只有 '
      f'{len(ctx["modes_inside"])} 条**（都在 10-28 11:26 工具启动那一刻）。'
      '也就是说这 25 小时里**没有发生过一次预设切换** —— 工具是「设完就不管」的状态，'
      '所有观察都是 game 预设（250 W 上限、`-rgc`）下的行为。')
    A('')
    if ctx['ev_stats']:
        rows = []
        for e in ctx['ev_stats']:
            rows.append([e['ev']['time'].strftime('%m-%d %H:%M:%S'), e['ev']['mode'],
                         '—' if not math.isfinite(e['pre']) else f"{e['pre']:.0f} W",
                         '—' if not math.isfinite(e['post']) else f"{e['post']:.0f} W"])
        A(md_table(['时间', '事件', '前 10 分钟均值', '后 10 分钟均值'], rows[:14]))
        A('')
        A('（`sleep` 的「后」为空、`wake` 的「前」为空，是缺口本身造成的，正常。）')
    A('')

    # -------- 16
    A('## 16. 结论与行动清单')
    A('')
    A(f'1. **最贵的是「满频低载」**：S3+S4 占了 {big_wh/tot_wh*100:.0f}% 的电量，其中真正在跑活的只有极少数时间。'
      '把 `-lgc` 上限收到 1350 MHz 是唯一高杠杆的动作，`-pl` 几乎没用（250 W 只在 07:45–08:00 摸到过）。')
    A('2. **不要用 `utilization.gpu` 当功耗代理**：1830 MHz 时 util 4% 也要 110 W；1300 MHz 时 util 从 0 到 65% 只差 4 W。')
    A('3. **采样机制要升级**：3 s + 每次 spawn nvidia-smi 既慢又有观测者效应；换 NVML 可以在 1 Hz 甚至 4 Hz 无成本采样。')
    A(f'4. **样本量 ≠ 证据量**：有效独立样本只有 {ctx["neff_ratio"]*100:.0f}%，任何 5 W 级的结论都需要 ≥10 分钟窗口 + 配对设计。')
    A('5. **补齐四列就能回答上一轮的问题**（CPU 瓦数 / encoder / clocks.mem / power.limit），成本一行 query。')
    A('')
    A('---')
    A('')
    A('### 附：产物清单')
    A('')
    A('| 文件 | 内容 |')
    A('|---|---|')
    A('| `out/report.md` | 本文 |')
    A('| `out/dashboard.html` | 单文件看板（内嵌全部 SVG + 表格），离线可开 |')
    A('| `out/charts/fig01…fig14.png/.svg` | 14 张图 |')
    A('| `out/tables/*.csv` | minute / hour / state / changepoints / summary / windows / gaps / events |')
    A('| `gta.py` | 数据层 + 统计原语（无副作用，可复用） |')
    A('| `pipeline.py` | 统计管线 |')
    A('| `figures.py` / `charts.py` | 图 |')
    A('| `build.py` | 一键重建 |')
    return '\n'.join(L)


# ============================================================ HTML
CSS = """
body{margin:0;background:#f7f8fa;color:#1f2430;
 font-family:"Noto Sans CJK SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif;
 line-height:1.65;font-size:15px}
.wrap{max-width:1320px;margin:0 auto;padding:28px 22px 90px}
h1{font-size:30px;margin:8px 0 4px;letter-spacing:-.5px}
h2{font-size:21px;margin:44px 0 10px;padding-top:14px;border-top:1px solid #e5e8ee}
h3{font-size:16px;margin:24px 0 8px;color:#2b3240}
.sub{color:#6b7280;font-size:13.5px}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin:18px 0 6px}
.card{background:#fff;border:1px solid #e5e8ee;border-radius:10px;padding:12px 16px;min-width:150px;flex:1}
.card b{display:block;font-size:24px;line-height:1.2}
.card span{color:#6b7280;font-size:12.5px}
table{border-collapse:collapse;width:100%;margin:10px 0 4px;background:#fff;font-size:13.5px}
th,td{border-bottom:1px solid #eceff4;padding:7px 10px;text-align:right}
th{background:#f2f5f9;color:#374151;font-weight:600;text-align:right}
td:first-child,th:first-child{text-align:left}
tr:hover td{background:#fafbfd}
.fig{background:#fff;border:1px solid #e5e8ee;border-radius:10px;padding:8px;margin:14px 0}
.fig svg{width:100%;height:auto;display:block}
.note{color:#6b7280;font-size:13px;margin:6px 0 0}
nav{background:#fff;border:1px solid #e5e8ee;border-radius:10px;padding:12px 16px;margin:18px 0;font-size:13.5px}
nav a{color:#2c6fbb;text-decoration:none;margin-right:14px;white-space:nowrap;display:inline-block}
code{background:#eef1f6;padding:1px 5px;border-radius:4px;font-size:13px}
.bad{color:#b0392b;font-weight:600}
.good{color:#2f7d46;font-weight:600}
ul{padding-left:22px}
"""


def _svg_inline(path):
    try:
        t = open(path, encoding='utf-8').read()
    except FileNotFoundError:
        return f'<p class="bad">missing {path}</p>'
    t = re.sub(r'<svg ([^>]*?)width="\d+" height="\d+"',
               r'<svg \1', t, count=1)
    return t


def render_html(ctx, chart_dir):
    s, km, desc = ctx['s'], ctx['km'], ctx['desc']
    k = km['k']
    st = km['stats']
    f = ctx['fits']['clk_util']
    w5, w10 = ctx['win'][5], ctx['win'][10]
    tot_wh = ctx['energy_total_wh']
    big = [x for x in st if x['clk'] > 1500]
    big_wh = sum(x['wh'] for x in big)
    big_h = sum(x['hours'] for x in big)
    H = []
    A = H.append
    A(f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
      f'<meta name="viewport" content="width=device-width,initial-scale=1">'
      f'<title>genshin-tweak 功耗全面统计</title><style>{CSS}</style></head><body><div class="wrap">')
    A('<h1>genshin-tweak 功耗数据 · 全面统计看板</h1>')
    A(f'<div class="sub">{s.t0:%Y-%m-%d %H:%M} → {s.datetime(len(s)-1):%Y-%m-%d %H:%M}　'
      f'有效覆盖 {ctx["covered_h"]:.2f} h / 墙钟 {ctx["wall_h"]:.2f} h　'
      f'{len(s)} 个样本（3 s）　生成于 {ctx["t_build"]:%Y-%m-%d %H:%M}</div>')

    A('<div class="cards">')
    for big_v, lab in [
            (f'{tot_wh:.0f} Wh', 'GPU 板卡总电量'),
            (f'{tot_wh/ctx["energy_hours"]:.1f} W', '有效采样期均功耗'),
            (f'{big_wh/tot_wh*100:.0f}%', '满频状态(S3+S4)电量占比'),
            (f'{ctx["marginal"][0]["w_per_100mhz"]:.1f} W', '空载时每 100 MHz 的成本'),
            (f'{ctx["neff_ratio"]*100:.0f}%', '有效独立样本比例'),
            (f'{w5["mde"]:.1f} W', '5 分钟窗口可分辨差异')]:
        A(f'<div class="card"><b>{big_v}</b><span>{lab}</span></div>')
    A('</div>')

    A('<nav><b>跳转：</b>' +
      ''.join(f'<a href="#s{i}">{t}</a>' for i, t in enumerate([
          '数据完整性', '描述统计', '功耗档位', '功耗面', '功耗~频率', '工况状态', '动力学',
          '自相关', '变化点', '电量', '可检测性', '缺失统计'])) + '</nav>')

    def sec(i, title, body):
        A(f'<h2 id="s{i}">{title}</h2>')
        A(body)

    def table(headers, rows):
        out = ['<table><thead><tr>' + ''.join(f'<th>{h}</th>' for h in headers) + '</tr></thead><tbody>']
        for r in rows:
            out.append('<tr>' + ''.join(f'<td>{c}</td>' for c in r) + '</tr>')
        out.append('</tbody></table>')
        return ''.join(out)

    def fig(name, cap):
        return (f'<div class="fig">{_svg_inline(os.path.join(chart_dir, name + ".svg"))}</div>'
                f'<div class="note">{cap}</div>')

    # 全景
    A(fig('fig01_overview', '整段 24h52m 的原始曲线：功耗 / 频率 / 利用率 / 温度。灰底＝休眠缺口，虚线＝变化点。'))

    # 1 完整性
    rows = [[g['start'].strftime('%m-%d %H:%M:%S'), g['end'].strftime('%H:%M:%S'), hms(g['dur'])]
            for g in sorted(ctx['gaps'], key=lambda x: -x['dur'])]
    sec(1, '数据完整性', f'<p>共 {len(ctx["gaps"])} 个缺口，最大 {hms(max(g["dur"] for g in ctx["gaps"]))}。'
        f'采样间隔中位 {np.median(ctx["jitter"]):.2f} s，相邻样本功耗跳变中位 {ctx["dp"]["median"]:.1f} W'
        f'（{ctx["dp"]["big_frac"]*100:.1f}% 超过 20 W）—— 单点读数没有意义。</p>' +
        table(['断开', '恢复', '时长'], rows))

    # 2 描述统计
    rows = [[n, f"{desc[k2]['mean']:.1f}", f"{desc[k2]['sd']:.1f}", f"{desc[k2]['median']:.1f}",
             f"{desc[k2]['p5']:.1f}", f"{desc[k2]['p95']:.1f}", f"{desc[k2]['skew']:.2f}"]
            for k2, n in ctx['keys']]
    sec(2, '描述统计', table(['变量', '均值', 'sd', '中位', 'p5', 'p95', '偏度'], rows) +
        fig('fig02_dist', '四变量直方图：功耗是双峰/多峰的，均值没有代表性。'))

    # 3 档位
    rows = [[f'#{i+1}', f'{c[1]:.0f} W', f'{c[0]*100:.0f}%', f'{c[2]:.1f} W', f'{c[2]/c[1]*100:.0f}%']
            for i, c in enumerate(ctx['gmm']['comps'])]
    sec(3, '功耗档位（GMM）', f'<p>BIC 选 k={ctx["gmm"]["k"]}；前 4 个分量 σ 仅 3–5 W。</p>' +
        table(['分量', '均值', '权重', 'σ', 'σ/μ'], rows) + fig('fig03_gmm', ''))

    # 4 功耗面
    sec(4, '功耗面', fig('fig04_surface', '同样「没干什么活」，1830 MHz 比 400 MHz 多 88 W。'))

    # 5 功耗-频率
    rows = [[m, f"{ctx['fits'][key]['r2']:.3f}", f"{ctx['fits'][key]['rmse']:.1f}"]
            for m, key in [('P ~ clk', 'clk'), ('P ~ clk + util', 'clk_util'),
                           ('P ~ clk + util + clk·util', 'clk_util_int'),
                           ('P ~ clk + util + T', 'clk_util_temp')]]
    sec(5, '功耗 ~ 频率', table(['模型', 'R²', 'RMSE (W)'], rows) +
        fig('fig05_pclk', '左：P–f 联合分布；右：分温度带拟合。'))

    # 6 状态
    rows = [[x['name'], x['n'], f"{x['hours']:.2f} h", f"{x['wh']:.0f} Wh", f"{x['share']*100:.0f}%",
             f"{x['p']:.0f}±{x['p_sd']:.0f}", f"{x['clk']:.0f}", f"{x['util']:.0f}%", f"{x['cpu']:.0f}%"]
            for x in st]
    sec(6, '工况状态', f'<p>抖动率 {ctx["flicker"]*100:.1f}%（原始序列中位驻留只有 3–6 s）。</p>' +
        table(['状态', 'n', '时长', '电量', '占比', '功耗', '频率', 'util', 'CPU'], rows) +
        fig('fig06_states', ''))

    # 7 动力学
    Pm = ctx['trans']['P']
    rows = [[km['names'][i]] + [f'{Pm[i,j]*100:.0f}%' for j in range(k)] for i in range(k)]
    rows2 = [[km['names'][i], ctx['trans']['dwell'][i]['n'], hms(ctx['trans']['dwell'][i]['med']),
              hms(ctx['trans']['dwell'][i]['max'])] for i in range(k)]
    sec(7, '状态动力学', table(['从 \\ 到'] + [f'S{j}' for j in range(k)], rows) +
        table(['状态', '段数', '中位驻留', '最长'], rows2) + fig('fig07_trans', ''))

    # 8 自相关
    sec(8, '自相关 / 有效样本量',
        f'<p>lag-1 = {ctx["acf"]["p"][1]:.3f}，5 分钟仍有 {ctx["acf"]["p"][100]:.3f}；'
        f'有效独立样本比例 <b>{ctx["neff_ratio"]*100:.1f}%</b>（约 {len(s)*ctx["neff_ratio"]:.0f} 个独立观测）。</p>' +
        fig('fig08_acf', ''))

    # 9 变化点
    rows = [[i+1, c['t'].strftime('%m-%d %H:%M'), f"{c['before']:.0f}", f"{c['after']:.0f}",
             f"{c['delta']:+.0f}"] for i, c in enumerate(ctx['cps'][:12])]
    sec(9, '变化点', f'<p>共 {len(ctx["cps"])} 个。</p>' +
        table(['#', '时间', '前 2 min (W)', '后 2 min (W)', 'Δ (W)'], rows) + fig('fig09_cp', ''))

    # 10 电量
    rows = [[d, f"{h:.2f} h", f"{e:.0f} Wh", f"{e/h:.1f} W"] for d, e, h in ctx['day_energy']]
    sec(10, '电量', table(['日期', '覆盖', '电量', '平均'], rows) +
        f'<p>S3+S4（满频）合计 <b class="bad">{big_wh:.0f} Wh / {big_wh/tot_wh*100:.0f}%</b>，'
        f'只占 {big_h:.2f} h。</p>' + fig('fig10_energy', ''))

    # 11 可检测性
    rows = [[f'{m} min', ctx['win'][m]['n'], f"{ctx['win'][m]['med']:.2f} W",
             f"{ctx['win'][m]['p95']:.2f} W", f"{ctx['win'][m]['mde']:.2f} W"]
            for m in (5, 10, 20) if ctx['win'][m]['n'] > 0]
    sec(11, '可检测性', table(['窗口', '对数', '|Δ| 中位', '|Δ| p95', '95% 判据'], rows) +
        fig('fig12_detect', '5 W 级效应正好骑在判据线上。'))

    # 12 缺失
    sec(12, '缺失的统计', fig('fig14_budget', '') +
        '<p>另有采样质量图：</p>' + fig('fig11_sampling', '') +
        '<p class="note">回归诊断见 <code>charts/fig13_fitdiag.png</code>。</p>')

    A(f'<p class="note" style="margin-top:40px">本页为单文件看板，SVG 全部内嵌，可离线打开、可打印为 PDF。'
      f'数据快照：<code>{os.path.basename(ctx["snap"])}</code>。</p>')
    A('</div></body></html>')
    return ''.join(H)
