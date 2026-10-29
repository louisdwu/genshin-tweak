#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pipeline.py —— 把所有统计量算进一个 ctx 对象，供 figures/report 共用。"""
from __future__ import annotations

import datetime as dt
import math
import os

import numpy as np

import gta
from gta import Series


class Ctx(dict):
    __getattr__ = dict.get


def _blocks(series: Series, min_len=80):
    """把序列切成无缺口的连续块，返回下标数组列表。"""
    out, cur = [], [0]
    for i in range(1, len(series.t)):
        if series.t[i] - series.t[i - 1] > gta.GAP_SEC:
            out.append(np.array(cur, int))
            cur = []
        cur.append(i)
    if cur:
        out.append(np.array(cur, int))
    return [b for b in out if len(b) >= min_len]


def _role(p, clk, util):
    if p < 38:
        return '待机'
    if clk > 1500:
        if util < 8:
            return '满频空转'
        if util < 32:
            return '满频低载'
    if util >= 45:
        return '中频满用'
    if util >= 32:
        return '有载'
    if p < 60:
        return '低载'
    return '中载'


def build(repo_root: str, art_dir: str, verbose=True) -> Ctx:
    ctx = Ctx()
    ctx['t_build'] = dt.datetime.now()
    ctx['repo_root'] = repo_root
    ctx['art_dir'] = art_dir

    # ---------- 1. 数据 ----------
    live = os.path.join(repo_root, 'genshin_tweak_power.csv')
    snap = gta.snapshot(live, os.path.join(art_dir, 'data'),
                        ctx['t_build'].strftime('%Y%m%d-%H%M%S'))
    full = gta.load_power(snap)
    one = gta.load_power(os.path.join(repo_root, 'analysis', 'raw.csv'))
    ev = gta.load_events(os.path.join(repo_root, 'genshin_tweak_log.csv'))
    ctx.update(s=full, s1=one, ev=ev, snap=snap, live=live)

    if verbose:
        print(f'[data] rows={full.n_raw} used={len(full)} dropped={full.n_drop} '
              f'span={(full.wall_span/3600):.2f}h covered={full.covered_seconds()/3600:.2f}h')

    # ---------- 2. 描述统计 ----------
    keys = [('p', 'GPU 板卡功耗 W'), ('clk', '核心频率 MHz'), ('util', 'GPU 3D 利用率 %'),
            ('temp', 'GPU 温度 ℃'), ('cpu', 'CPU 占用 %')]
    ctx['keys'] = keys
    ctx['desc'] = {k: gta.describe(getattr(full, k), nonneg=True) for k, _ in keys}
    ctx['desc1'] = {k: gta.describe(getattr(one, k), nonneg=True) for k, _ in keys}
    ctx['gaps'] = full.gaps()
    ctx['covered_h'] = full.covered_seconds() / 3600.0
    ctx['wall_h'] = full.wall_span / 3600.0
    ctx['duty'] = ctx['covered_h'] / ctx['wall_h']
    ctx['jitter'] = gta.sampling_jitter(full)

    # ---------- 3. 能量 ----------
    e_all, h_all = full.energy_wh()
    ctx['energy_total_wh'] = e_all
    ctx['energy_hours'] = h_all
    day_e = []
    for d in (full.t0.date(), full.datetime(len(full) - 1).date()):
        m = np.array([full.datetime(i).date() == d for i in range(len(full))])
        if m.any():
            e, h = full.energy_wh(m)
            day_e.append((d, e, h))
    ctx['day_energy'] = day_e

    # ---------- 4. 一维 GMM（功耗档位）----------
    gmm_curve, best = [], None
    for k in range(2, 7):
        comps, ll, bic = gta.gmm1d(full.p, k, restarts=4, seed=17)
        gmm_curve.append((k, bic))
        if best is None or bic < best[2]:
            best = (k, comps, bic)
    comps = sorted(best[1], key=lambda c: c[1])
    ctx['gmm'] = dict(k=best[0], comps=comps, bic=best[2], curve=gmm_curve)
    if verbose:
        print('[gmm] k=%d  ' % best[0] +
              '  '.join(f'{w*100:.0f}%@{mu:.0f}W(σ{sd:.0f})' for w, mu, sd in comps))

    # ---------- 5. k-means 状态（轮廓系数选 k）----------
    # 工况空间：频率 / 3D 利用率 / CPU 占用（刻意不放功耗——避免「用功耗解释功耗」）
    X = np.column_stack([np.log(np.clip(full.clk, 1, None)), full.util / 100.0,
                         np.clip(full.cpu, 0, 100) / 100.0])
    mu_, sd_ = X.mean(0), X.std(0)
    sd_[sd_ == 0] = 1
    Z = (X - mu_) / sd_
    # 先扫一遍轮廓系数（图表用），再按「一维 GMM 档位数」定格 k
    km_curve, runs = [], {}
    for kk in range(2, 8):
        lab, C, sse = gta.kmeans(Z, kk, restarts=4)
        sil = gta.silhouette(Z, lab, kk, sample=1800)
        km_curve.append((kk, sse, sil))
        runs[kk] = (lab, C, sse, sil)
    k = ctx['gmm']['k']
    if k not in runs:
        k = max(km_curve, key=lambda t: t[2])[0]
    lab, C, sse, sil = runs[k]
    ctx['km_sil_best'] = max(km_curve, key=lambda t: t[2])
    lab_raw = lab.copy()
    lab = gta.smooth_labels(lab, min_run=3, passes=2)
    ctx['flicker'] = gta.flicker_rate(lab_raw, min_run=5)
    ctx['km_curve'] = km_curve
    stats = []
    for j in range(k):
        m = lab == j
        stats.append(dict(p=float(full.p[m].mean()), clk=float(full.clk[m].mean()),
                          util=float(full.util[m].mean()), cpu=float(full.cpu[m].mean()),
                          temp=float(full.temp[m].mean()), n=int(m.sum()), old=j))
    stats.sort(key=lambda s: s['p'])
    remap = {s['old']: new for new, s in enumerate(stats)}
    labels = np.array([remap[v] for v in lab])
    names, used = [], {}
    for i, s in enumerate(stats):
        base = _role(s['p'], s['clk'], s['util'])
        used[base] = used.get(base, 0) + 1
        nm = f'S{i} {base}' if used[base] == 1 else f"S{i} {base}{used[base]}"
        names.append(nm)
        s['label'], s['name'] = i, nm
        m = labels == i
        e, h = full.energy_wh(m)
        s['wh'], s['hours'] = e, h
        s['share'] = e / e_all if e_all else 0.0
        s['p_sd'] = float(np.std(full.p[m], ddof=1))
    ctx['km'] = dict(k=k, labels=labels, centers=C, sse=sse, sil=sil, curve=km_curve,
                     stats=stats, names=names)
    if verbose:
        print(f'[km] k={k} silhouette={sil:.3f}')
        for s in stats:
            print(f"  {s['name']:14s} n={s['n']:5d} {s['hours']:5.2f}h {s['wh']:6.0f}Wh "
                  f"({s['share']*100:4.1f}%) p={s['p']:5.1f}±{s['p_sd']:4.1f} clk={s['clk']:4.0f} "
                  f"util={s['util']:4.1f} cpu={s['cpu']:4.1f}")

    # ---------- 6. 自相关 ----------
    blocks20 = _blocks(full, 200)
    acf_p = np.mean([gta.autocorr(full.p[b], 100) for b in blocks20], 0)
    acf_u = np.mean([gta.autocorr(full.util[b], 100) for b in blocks20], 0)
    acf_c = np.mean([gta.autocorr(np.clip(full.cpu[b], 0, 100), 100) for b in blocks20], 0)
    ctx['acf'] = dict(p=acf_p, util=acf_u, cpu=acf_c, n_blocks=len(blocks20))

    # ---------- 7. 变化点 ----------
    cps = []
    for b in _blocks(full, 1200):
        idx = gta.changepoints_l2(full.p[b], min_seg=40, penalty_k=3.0, max_cp=12)
        for i in idx:
            gi = int(b[i])
            before = float(np.mean(full.p[max(0, gi - 40):gi]))
            after = float(np.mean(full.p[gi:gi + 40]))
            cps.append(dict(i=gi, t=full.datetime(gi), before=before, after=after,
                            delta=after - before))
    cps.sort(key=lambda c: -abs(c['delta']))
    ctx['cps'] = cps
    if verbose and cps:
        print(f'[cp] {len(cps)} 个变化点, top3: ' +
              ', '.join(f"{c['t']:%m-%d %H:%M} {c['before']:.0f}→{c['after']:.0f}W" for c in cps[:3]))

    # ---------- 8. 回归 ----------
    p, clk, util, cpu, temp = full.p, full.clk, full.util, full.cpu, full.temp
    n = len(p)
    one_ = np.ones(n)
    fits = dict(
        clk=gta.ols(np.column_stack([one_, clk]), p),
        clk_util=gta.ols(np.column_stack([one_, clk, util]), p),
        clk_util_int=gta.ols(np.column_stack([one_, clk, util, clk * util]), p),
        clk_util_temp=gta.ols(np.column_stack([one_, clk, util, temp]), p),
    )
    band_fits = []
    for lo, hi in [(0, 50), (50, 53), (53, 56), (56, 60), (60, 101)]:
        m = (temp >= lo) & (temp < hi)
        if m.sum() > 300:
            f = gta.ols(np.column_stack([np.ones(int(m.sum())), clk[m], util[m]]), p[m])
            band_fits.append(dict(lo=lo, hi=hi, n=int(m.sum()), fit=f,
                                  p_mean=float(p[m].mean()), clk_mean=float(clk[m].mean())))
    ctx['fits'], ctx['band_fits'] = fits, band_fits
    bi = fits['clk_util_int']['beta']
    ctx['marginal'] = [dict(util=u, w_per_100mhz=(bi[1] + bi[3] * u) * 100.0)
                       for u in (0, 10, 20, 40, 60)]
    if verbose:
        f = fits['clk_util']
        print(f"[fit] P = {f['beta'][0]:.1f} + {f['beta'][1]:.5f}·clk + {f['beta'][2]:.3f}·util  "
              f"R²={f['r2']:.3f}  RMSE={f['rmse']:.1f}W")

    # ---------- 9. 温度滞后相关 ----------
    acc = None
    lags = []
    bl = _blocks(full, 300)
    for b in bl:
        lags, c = gta.lag_corr(full.p[b], full.temp[b], 40)
        acc = np.array(c) if acc is None else acc + np.array(c)
    nb = max(len(bl), 1)
    ctx['lagcorr'] = dict(lags=lags, corr=(acc / nb).tolist() if acc is not None else [])

    # ---------- 10. 最小可检测差异（实测相邻窗口差）----------
    blk_id = np.zeros(len(full), int)
    for bid, b in enumerate(_blocks(full, 80)):
        blk_id[b] = bid + 1
    resid = full.p - np.array([stats[v]['p'] for v in labels])
    ctx['acf']['resid'] = np.mean([gta.autocorr(resid[b], 100) for b in blocks20], 0)
    win = {}
    for mins in (5, 10, 20):
        pairs = _adjacent_window_pairs(full.p, labels, blk_id, mins)
        d = np.array([abs(x[3]) for x in pairs]) if pairs else np.array([])
        by_state = {}
        for st in stats:
            dd = np.array([abs(x[3]) for x in pairs if x[0] == st['label']]) if pairs else np.array([])
            if len(dd) >= 3:
                by_state[st['label']] = dict(n=int(len(dd)), sd=float(np.std(dd, ddof=1)),
                                             med=float(np.median(dd)),
                                             p95=float(np.percentile(dd, 95)),
                                             mde=1.959963985 * math.sqrt(2.0) * float(np.std(dd, ddof=1)))
        win[mins] = dict(pairs=pairs, deltas=d,
                         sd=float(np.std(d, ddof=1)) if len(d) > 1 else float('nan'),
                         med=float(np.median(d)) if len(d) else float('nan'),
                         p95=float(np.percentile(d, 95)) if len(d) else float('nan'),
                         n=len(d), by_state=by_state,
                         mde=1.959963985 * math.sqrt(2.0) * float(np.std(d, ddof=1)) if len(d) > 1 else float('nan'))
    ctx['win'] = win
    ctx['neff_ratio'] = float(np.mean([gta.effective_n(full.p[b]) / len(b) for b in blocks20]))
    if verbose:
        w = win[10]
        print(f"[mde] 相邻 10min 窗口差: n={w['n']} 中位 {w['med']:.1f}W p95 {w['p95']:.1f}W "
              f"sd {w['sd']:.1f}W → 判据 ~{w['mde']:.1f}W")

    # ---------- 11. 转移矩阵 / 游程 ----------
    valid = np.ones(len(full), bool)
    valid[1:] = np.diff(full.t) <= gta.GAP_SEC
    Cm, Pm = gta.transition_matrix(labels, k, valid)
    rl = gta.run_lengths(labels, valid)
    dwell = {}
    for j in range(k):
        v = rl.get(j, [])
        dwell[j] = dict(n=len(v),
                        med=float(np.median(v)) * gta.NOMINAL_DT if v else float('nan'),
                        mean=float(np.mean(v)) * gta.NOMINAL_DT if v else float('nan'),
                        max=float(np.max(v)) * gta.NOMINAL_DT if v else float('nan'))
    ctx['trans'] = dict(C=Cm, P=Pm, dwell=dwell, valid=valid)

    # ---------- 12. 每小时能量 ----------
    hours = {}
    for i in range(1, len(full)):
        step = min(full.t[i] - full.t[i - 1], gta.GAP_SEC)
        if step <= 0:
            continue
        hb = full.datetime(i).replace(minute=0, second=0, microsecond=0)
        hours.setdefault(hb, np.zeros(k))[labels[i]] += full.p[i] * step / 3600.0
    ctx['hour_energy'] = sorted(hours.items())

    # ---------- 13. 3 s 级抖动 ----------
    dps = []
    for b in _blocks(full, 20):
        d = np.abs(np.diff(full.p[b]))
        if len(d):
            dps.append(d)
    if dps:
        d = np.concatenate(dps)
        ctx['dp'] = dict(n=int(len(d)), median=float(np.median(d)),
                         p90=float(np.percentile(d, 90)), p99=float(np.percentile(d, 99)),
                         big=int(np.sum(d > 20)), big_frac=float(np.mean(d > 20)))
    else:
        ctx['dp'] = dict(n=0, median=float('nan'), p90=float('nan'), p99=float('nan'),
                         big=0, big_frac=float('nan'))

    # ---------- 14. 事件对齐 ----------
    modes = [e for e in ev if e['mode'] in ('game', 'work', 'video', 'custom')]
    inside = [e for e in modes if full.t0 <= e['time'] <= full.datetime(len(full) - 1)]
    ctx['modes_all'], ctx['modes_inside'] = modes, inside
    ev_stats = []
    for e in ev:
        if e['mode'] not in ('game', 'work', 'video', 'custom', 'sleep', 'wake'):
            continue
        pre = full.slice_mask(e['time'] - dt.timedelta(minutes=10), e['time'] - dt.timedelta(seconds=30))
        post = full.slice_mask(e['time'] + dt.timedelta(seconds=30), e['time'] + dt.timedelta(minutes=10))
        if pre.sum() > 20 or post.sum() > 20:
            ev_stats.append(dict(ev=e,
                                 pre=float(full.p[pre].mean()) if pre.sum() > 20 else float('nan'),
                                 post=float(full.p[post].mean()) if post.sum() > 20 else float('nan'),
                                 npre=int(pre.sum()), npost=int(post.sum())))
    ctx['ev_stats'] = ev_stats

    # ---------- 15. 聚合表 ----------
    ctx['minutes'] = _aggregate(full, 60)
    ctx['qtr'] = _aggregate(full, 900)
    return ctx


def _aggregate(s: Series, step_sec: int):
    t = s.t
    key = np.floor(t / step_sec).astype(np.int64)
    rows = []
    for kk in np.unique(key):
        m = key == kk
        idx = np.nonzero(m)[0]
        if len(idx) == 0:
            continue
        d = s.dt
        e = cov = 0.0
        for i in idx:
            if i == 0:
                continue
            st = min(d[i], gta.GAP_SEC)
            e += s.p[i] * st / 3600.0
            cov += st
        rows.append(dict(t=s.t0 + dt.timedelta(seconds=int(kk * step_sec)),
                         n=len(idx), cov=cov, wh=e,
                         p=float(s.p[m].mean()), p_max=float(s.p[m].max()),
                         clk=float(s.clk[m].mean()), clk_max=float(s.clk[m].max()),
                         util=float(s.util[m].mean()), util_max=float(s.util[m].max()),
                         temp=float(s.temp[m].mean()), cpu=float(np.nanmean(s.cpu[m]))))
    return rows


def _window_means(p, mask, blk_id, minutes):
    """把某个状态的样本按「同块连续段」切成 minutes 长的窗口，返回各窗口均值。"""
    n = int(round(minutes * 60 / gta.NOMINAL_DT))
    if n < 2:
        return np.array([])
    idx = np.nonzero(mask)[0]
    out = []
    if len(idx) == 0:
        return np.array([])
    # 按块 + 连续性切段
    runs, cur = [], [idx[0]]
    for a, b in zip(idx[:-1], idx[1:]):
        if b == a + 1 and blk_id[b] == blk_id[a]:
            cur.append(b)
        else:
            runs.append(np.array(cur))
            cur = [b]
    runs.append(np.array(cur))
    for r in runs:
        for st in range(0, len(r) - n + 1, n):
            out.append(float(np.mean(p[r[st:st + n]])))
    return np.array(out)


def _adjacent_window_pairs(p, labels, blk_id, minutes):
    """同一连续块内、相邻两个窗口（同状态）的均值差，返回 [(state, m1, m2, delta)]。"""
    n = int(round(minutes * 60 / gta.NOMINAL_DT))
    if n < 2:
        return []
    out = []
    for bid in np.unique(blk_id):
        if bid == 0:
            continue
        idx = np.nonzero(blk_id == bid)[0]
        for st in range(0, len(idx) - 2 * n + 1, 2 * n):
            a, b = idx[st:st + n], idx[st + n:st + 2 * n]
            la, lb = labels[a], labels[b]
            if len(np.unique(la)) == 1 and len(np.unique(lb)) == 1 and la[0] == lb[0]:
                out.append((int(la[0]), float(np.mean(p[a])), float(np.mean(p[b])),
                            float(np.mean(p[b]) - np.mean(p[a]))))
    return out
