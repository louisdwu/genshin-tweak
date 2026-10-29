#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gta = genshin-tweak analytics —— 数据分析库（只依赖 numpy）。

职责：
  1. 数据层：快照、解析、清洗、缺口、事件日志
  2. 统计层：描述统计、分位数、直方图、自相关、GMM(EM)、k-means、
     二分割变化点、OLS/WLS、交叉相关、转移矩阵、能量积分
  3. 指标层：能量核算、状态建模、效应量/功效、异常检测

所有函数无副作用，输入输出只用 numpy / 内建类型，方便复用到以后的 CSV。
"""
from __future__ import annotations

import csv
import datetime as dt
import math
import os
import shutil

import numpy as np

GAP_SEC = 15.0            # 与 C# GapThresholdMs 对齐：两次采样间隔 > 15 s 视为断点
NOMINAL_DT = 3.0          # 采样周期


# ============================================================ 1. 数据层
def snapshot(src: str, dst_dir: str, tag: str | None = None) -> str:
    """把实时增长的 CSV 拷成冻结快照，返回快照路径。"""
    os.makedirs(dst_dir, exist_ok=True)
    if tag is None:
        tag = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    dst = os.path.join(dst_dir, 'snapshot-%s.csv' % tag)
    shutil.copyfile(src, dst)
    return dst


def _f(x, default=-1.0):
    x = (x or '').strip()
    try:
        return float(x)
    except Exception:
        return default


class Series:
    """一条时间序列数据集。所有量都是 numpy 数组，t 为相对首样本的秒数。"""

    __slots__ = ('t0', 't', 'p', 'clk', 'temp', 'util', 'cpu', 'gap_before',
                 'path', 'n_raw', 'n_drop')

    def __init__(self, t0, t, p, clk, temp, util, cpu, gap_before, path,
                 n_raw=0, n_drop=0):
        self.t0, self.t, self.path = t0, t, path
        self.p, self.clk, self.temp = p, clk, temp
        self.util, self.cpu, self.gap_before = util, cpu, gap_before
        self.n_raw, self.n_drop = n_raw, n_drop

    # ---- 基础 ----
    def __len__(self):
        return len(self.t)

    @property
    def wall_span(self) -> float:
        """墙钟跨度（秒），含缺口。"""
        return float(self.t[-1] - self.t[0]) if len(self.t) else 0.0

    @property
    def dt(self) -> np.ndarray:
        d = np.diff(self.t)
        return np.concatenate(([NOMINAL_DT], d))

    def timedelta_at(self, i: int) -> float:
        return float(self.t[i] - self.t[i - 1]) if i else NOMINAL_DT

    def datetime(self, i):
        return self.t0 + dt.timedelta(seconds=float(self.t[i]))

    # ---- 缺口 ----
    def gaps(self, min_sec: float = GAP_SEC) -> list[dict]:
        out = []
        d = np.diff(self.t)
        for i in np.nonzero(d > min_sec)[0]:
            out.append(dict(i=int(i + 1),
                            start=self.datetime(i), end=self.datetime(i + 1),
                            dur=float(d[i])))
        return out

    def covered_seconds(self, max_dt: float = GAP_SEC) -> float:
        d = self.dt
        return float(np.sum(np.minimum(d, max_dt)))

    # ---- 能量 ----
    def energy_wh(self, mask=None, max_dt: float = GAP_SEC) -> tuple[float, float]:
        """返回 (Wh, 计入的小时数)。用法拉第梯形不是必须的——功率是阶跃量，
        这里按「后一个样本的功率 × 它前面的时间片」积分，与设备记录方式一致。"""
        if mask is None:
            mask = np.ones(len(self.t), bool)
        idx = np.nonzero(mask)[0]
        if len(idx) == 0:
            return 0.0, 0.0
        d = self.dt
        e = 0.0
        h = 0.0
        for i in idx:
            if i == 0:
                continue
            step = min(d[i], max_dt)
            e += self.p[i] * step / 3600.0
            h += step / 3600.0
        return e, h

    def mean_over_covered(self, mask=None, key='p') -> float:
        e, h = self.energy_wh(mask)
        if key == 'p':
            return e / h if h else float('nan')
        v = getattr(self, key)
        if mask is not None:
            v = v[mask]
        return float(np.mean(v)) if len(v) else float('nan')

    def slice_mask(self, a: dt.datetime, b: dt.datetime) -> np.ndarray:
        ta = (a - self.t0).total_seconds()
        tb = (b - self.t0).total_seconds()
        return (self.t >= ta) & (self.t <= tb)


def load_power(path: str) -> Series:
    """读 power csv。丢掉解不出的行（-1），记录丢弃数。"""
    rows = list(csv.DictReader(open(path, encoding='utf-8-sig')))
    t0 = None
    T, P, C, TP, U, CP, GB = [], [], [], [], [], [], []
    n_drop = 0
    for r in rows:
        try:
            tt = dt.datetime.strptime(r['time'], '%Y-%m-%d %H:%M:%S')
        except Exception:
            n_drop += 1
            continue
        p = _f(r['gpu_power_w'])
        if p < 0:
            n_drop += 1
            continue
        if t0 is None:
            t0 = tt
        T.append((tt - t0).total_seconds())
        P.append(p)
        C.append(_f(r['gpu_clock_mhz']))
        TP.append(_f(r['gpu_temp_c']))
        U.append(_f(r['gpu_util_pct']))
        CP.append(_f(r['cpu_pct']))
        GB.append(int(_f(r.get('gap_before', 0), 0)))
    return Series(t0, np.array(T, float), np.array(P, float), np.array(C, float),
                  np.array(TP, float), np.array(U, float), np.array(CP, float),
                  np.array(GB, int), path, len(rows), n_drop)


def load_events(path: str) -> list[dict]:
    rows = list(csv.DictReader(open(path, encoding='utf-8-sig')))
    out = []
    for r in rows:
        try:
            t = dt.datetime.strptime(r['time'], '%Y-%m-%d %H:%M:%S')
        except Exception:
            continue
        out.append(dict(time=t, mode=r['mode'], detail=r['detail']))
    out.sort(key=lambda e: e['time'])
    return out


# ============================================================ 2. 描述统计
def describe(x: np.ndarray, nonneg: bool = False) -> dict:
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if nonneg:
        x = x[x >= 0]
    n = len(x)
    if n == 0:
        return {}
    mean = float(np.mean(x))
    sd = float(np.std(x, ddof=1)) if n > 1 else 0.0
    q = np.percentile(x, [0, 1, 5, 25, 50, 75, 95, 99, 100])
    med = float(q[4])
    mad = float(np.median(np.abs(x - med)))
    iqr = float(q[5] - q[3])
    out = dict(n=n, mean=mean, sd=sd, cv=sd / mean if mean else float('inf'),
               min=float(q[0]), p1=float(q[1]), p5=float(q[2]), q1=float(q[3]),
               median=med, q3=float(q[5]), p95=float(q[6]), p99=float(q[7]),
               max=float(q[8]), iqr=iqr, mad=mad,
               se=sd / math.sqrt(n) if n else float('nan'))
    # 偏度/峰度（总体口径，避免小样本偏差讨论）
    if n > 2 and sd > 0:
        z = (x - mean) / sd
        out['skew'] = float(np.mean(z ** 3))
        out['kurt'] = float(np.mean(z ** 4) - 3.0)
    else:
        out['skew'] = out['kurt'] = float('nan')
    if n > 3 and iqr > 0:
        out['outliers'] = int(np.sum((x < q[3] - 1.5 * iqr) | (x > q[5] + 1.5 * iqr)))
    else:
        out['outliers'] = 0
    out['spike_hi'] = int(np.sum(x > med + 3 * mad)) if mad > 0 else 0
    return out


def histogram(x: np.ndarray, bins: int | np.ndarray = 30):
    h, edges = np.histogram(x, bins=bins)
    return h, edges


def quantiles(x: np.ndarray, qs=(0, 1, 5, 10, 25, 50, 75, 90, 95, 99, 100)):
    return {q: float(v) for q, v in zip(qs, np.percentile(x, qs))}


def autocorr(x: np.ndarray, max_lag: int) -> np.ndarray:
    x = np.asarray(x, float)
    x = x - np.mean(x)
    denom = float(np.dot(x, x))
    if denom == 0:
        return np.zeros(max_lag + 1)
    out = np.empty(max_lag + 1)
    for k in range(max_lag + 1):
        out[k] = float(np.dot(x[:len(x) - k], x[k:])) / denom if k else 1.0
    return out


def blockwise(series: Series, fn, block_gap=GAP_SEC):
    """把连续段切块后分别喂给 fn，返回结果列表（用于避开缺口求自相关）。"""
    blocks, cur = [], [0]
    d = np.diff(series.t)
    for i in range(1, len(series.t)):
        if d[i - 1] > block_gap:
            blocks.append(np.array(cur, int))
            cur = []
        cur.append(i)
    if cur:
        blocks.append(np.array(cur, int))
    return [fn(b) for b in blocks if len(b) > 40]


def lag_corr(x: np.ndarray, y: np.ndarray, max_lag: int):
    """滞后相关：corr(x[t], y[t+k])，k 可负。返回 (lags, corrs)。"""
    lags = list(range(-max_lag, max_lag + 1))
    out = []
    for k in lags:
        if k >= 0:
            a, b = x[:len(x) - k] if k else x, y[k:]
        else:
            a, b = x[-k:], y[:len(y) + k]
        if len(a) < 10 or np.std(a) == 0 or np.std(b) == 0:
            out.append(0.0)
            continue
        out.append(float(np.corrcoef(a, b)[0, 1]))
    return lags, out


# ============================================================ 3. 模型
def gmm1d(x: np.ndarray, k: int, iters: int = 400, restarts: int = 6, seed: int = 7):
    """一维高斯混合 EM。返回 (params, loglik, bic)。params=[(w,mu,sigma), ...]"""
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    rng = np.random.default_rng(seed)
    best = None
    lo, hi = float(np.min(x)), float(np.max(x))
    for _ in range(restarts):
        mu = np.sort(rng.uniform(lo, hi, k))
        sig = np.full(k, max((hi - lo) / (4 * k), 1e-6))
        w = np.full(k, 1.0 / k)
        ll_prev = -np.inf
        ll = -np.inf
        for _ in range(iters):
            # E
            pdf = np.zeros((len(x), k))
            for j in range(k):
                s = max(sig[j], 1e-9)
                pdf[:, j] = w[j] * np.exp(-0.5 * ((x - mu[j]) / s) ** 2) / (s * math.sqrt(2 * math.pi))
            tot = pdf.sum(1, keepdims=True)
            tot[tot == 0] = 1e-300
            r = pdf / tot
            # M
            nk = r.sum(0) + 1e-12
            w = nk / len(x)
            mu = (r * x[:, None]).sum(0) / nk
            sig = np.sqrt(((r * (x[:, None] - mu) ** 2).sum(0) / nk)) + 1e-9
            ll = float(np.sum(np.log(tot)))
            if abs(ll - ll_prev) < 1e-6 * max(1.0, abs(ll)):
                break
            ll_prev = ll
        p = 3 * k + (k - 1)              # 参数个数
        bic = -2 * ll + p * math.log(len(x))
        if best is None or bic < best[2]:
            best = ([(float(w[j]), float(mu[j]), float(sig[j])) for j in range(k)], ll, bic)
    return best


def kmeans(X: np.ndarray, k: int, iters: int = 200, restarts: int = 5, seed: int = 11):
    """k-means++ / Lloyd。返回 (labels, centers, SSE)。"""
    X = np.asarray(X, float)
    n = len(X)
    rng = np.random.default_rng(seed)
    best = None
    for _ in range(restarts):
        # k-means++
        centers = [X[rng.integers(n)]]
        for _ in range(k - 1):
            d2 = np.min(((X[:, None, :] - np.array(centers)[None, :, :]) ** 2).sum(-1), axis=1)
            prob = d2 / d2.sum() if d2.sum() > 0 else np.full(n, 1.0 / n)
            centers.append(X[rng.choice(n, p=prob)])
        C = np.array(centers)
        lab = np.zeros(n, int)
        for _ in range(iters):
            D = ((X[:, None, :] - C[None, :, :]) ** 2).sum(-1)
            new = np.argmin(D, axis=1)
            if np.array_equal(new, lab):
                break
            lab = new
            for j in range(k):
                m = lab == j
                if m.any():
                    C[j] = X[m].mean(0)
        D = ((X[:, None, :] - C[None, :, :]) ** 2).sum(-1)
        sse = float(D[np.arange(n), lab].sum())
        if best is None or sse < best[2]:
            best = (lab.copy(), C.copy(), sse)
    return best


def bic_kmeans(sse: float, n: int, k: int, d: int) -> float:
    """k-means 的 BIC 近似（球面高斯等协方差）。"""
    s2 = max(sse / (n * d), 1e-12)
    ll = -0.5 * n * d * (math.log(2 * math.pi * s2) + 1)
    return -2 * ll + (k * d) * math.log(n)


def silhouette(X: np.ndarray, labels: np.ndarray, k: int, sample: int = 2000,
               seed: int = 3) -> float:
    """轮廓系数（子采样估计，O(n^2) 在 ~2000 点上够快）。"""
    X = np.asarray(X, float)
    rng = np.random.default_rng(seed)
    n = len(X)
    if n > sample:
        idx = rng.choice(n, sample, replace=False)
        X, labels = X[idx], labels[idx]
    n = len(X)
    D = np.sqrt(np.maximum(((X[:, None, :] - X[None, :, :]) ** 2).sum(-1), 0))
    np.fill_diagonal(D, 0.0)
    s = np.zeros(n)
    uniq = np.unique(labels)
    for j in uniq:
        mj = labels == j
        cnt = int(mj.sum())
        if cnt < 2:
            continue
        a = D[np.ix_(mj, mj)].sum(1) / (cnt - 1)
        b = np.full(cnt, np.inf)
        for o in uniq:
            if o == j:
                continue
            mo = labels == o
            if mo.sum() == 0:
                continue
            b = np.minimum(b, D[np.ix_(mj, mo)].mean(1))
        with np.errstate(invalid='ignore', divide='ignore'):
            s[mj] = (b - a) / np.maximum(a, b)
    return float(np.nanmean(s[np.isfinite(s)])) if np.isfinite(s).any() else 0.0


def smooth_labels(labels: np.ndarray, min_run: int = 5, passes: int = 3) -> np.ndarray:
    """状态序列去抖：把短于 min_run 的游程并入较长的一侧邻居。"""
    out = np.asarray(labels).copy()
    n = len(out)
    for _ in range(passes):
        i = 0
        changed = False
        while i < n:
            j = i
            while j + 1 < n and out[j + 1] == out[i]:
                j += 1
            ln = j - i + 1
            if ln < min_run and i > 0 and j < n - 1:
                left, right = out[i - 1], out[j + 1]
                # 找两侧游程长度
                li = i - 1
                while li > 0 and out[li - 1] == left:
                    li -= 1
                rj = j + 1
                while rj < n - 1 and out[rj + 1] == right:
                    rj += 1
                out[i:j + 1] = left if (i - li) >= (rj - j) else right
                changed = True
            i = j + 1
        if not changed:
            break
    return out


def flicker_rate(labels: np.ndarray, min_run: int = 5) -> float:
    """样本中落在「短游程」里的比例 —— 状态抖动程度。"""
    n = len(labels)
    short = 0
    i = 0
    while i < n:
        j = i
        while j + 1 < n and labels[j + 1] == labels[i]:
            j += 1
        if j - i + 1 < min_run:
            short += j - i + 1
        i = j + 1
    return short / n if n else 0.0


def mde_window(sd: float, acf: np.ndarray, minutes: float,
               alpha: float = 0.05, power: float = 0.8) -> dict:
    """给定窗口时长，两组独立窗口均值差异的最小可检测量。

    用测得的自相关做 Bartlett 型方差膨胀：
        Var(mean) = σ²/n · (1 + 2Σ(1-k/n)ρ_k)
    返回 {n, infl, neff, mde}。
    """
    n = max(int(minutes * 60 / NOMINAL_DT), 3)
    infl = 1.0
    for kk in range(1, min(len(acf), n)):
        infl += 2.0 * (1 - kk / n) * acf[kk]
    infl = max(infl, 1.0)
    neff = n / infl
    za = 1.959963985 if alpha == 0.05 else 1.644853627
    zb = 0.8416212336 if power == 0.8 else 1.2815515655
    return dict(n=n, infl=infl, neff=neff, mde=(za + zb) * sd * math.sqrt(2.0 / max(neff, 1.0)))


def ols(X: np.ndarray, y: np.ndarray, w: np.ndarray | None = None) -> dict:
    """最小二乘（可加权）。X 需含常数列。返回 beta/se/t/r2/aic/n。"""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    n, p = X.shape
    if w is None:
        w = np.ones(n)
    W = np.diag(w) if n < 4000 else None
    if W is not None:
        XtWX = X.T @ W @ X
        XtWy = X.T @ W @ y
    else:
        XtWX = (X * w[:, None]).T @ X
        XtWy = (X * w[:, None]).T @ y
    try:
        inv = np.linalg.pinv(XtWX)
    except Exception:
        return {}
    beta = inv @ XtWy
    resid = y - X @ beta
    sw = float(np.sum(w))
    dof = max(n - p, 1)
    s2 = float(np.sum(w * resid ** 2) / sw * n / dof)
    cov = inv * s2
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    ss_tot = float(np.sum(w * (y - np.average(y, weights=w)) ** 2))
    ss_res = float(np.sum(w * resid ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot else float('nan')
    adj = 1 - (1 - r2) * (n - 1) / dof if ss_tot else float('nan')
    ll = -0.5 * n * (math.log(2 * math.pi * s2) + 1)
    aic = -2 * ll + 2 * p
    return dict(beta=beta, se=se, t=beta / np.where(se > 0, se, np.nan),
                r2=r2, adj_r2=adj, aic=aic, n=n, dof=dof, rmse=math.sqrt(s2),
                resid=resid)


def changepoints_l2(y: np.ndarray, min_seg: int, penalty_k: float = 3.0,
                    max_cp: int = 40) -> list[int]:
    """二分割（binary segmentation）变化点检测，L2 代价 + BIC 型惩罚。

    返回分割点下标（升序）。惩罚项：Δcost > penalty_k * σ² * ln(n)。
    """
    y = np.asarray(y, float)
    n = len(y)
    if n < 2 * min_seg:
        return []
    cs = np.concatenate(([0.0], np.cumsum(y)))
    cs2 = np.concatenate(([0.0], np.cumsum(y ** 2)))

    def cost(a, b):  # [a,b)
        m = b - a
        if m <= 0:
            return 0.0
        s = cs[b] - cs[a]
        s2 = cs2[b] - cs2[a]
        return s2 - s * s / m

    sig2 = float(np.var(y)) or 1.0
    pen = penalty_k * sig2 * math.log(n)
    out = []

    def split(a, b, depth=0):
        if b - a < 2 * min_seg or len(out) >= max_cp or depth > 12:
            return
        base = cost(a, b)
        best, bi = 0.0, -1
        for s in range(a + min_seg, b - min_seg + 1):
            d = base - cost(a, s) - cost(s, b)
            if d > best:
                best, bi = d, s
        if bi > 0 and best > pen:
            out.append(bi)
            split(a, bi, depth + 1)
            split(bi, b, depth + 1)

    split(0, n)
    return sorted(out)


def transition_matrix(labels: np.ndarray, k: int, valid: np.ndarray | None = None):
    """一阶马尔可夫转移矩阵（按样本计），返回 count 与 row-normalized 概率。"""
    labels = np.asarray(labels, int)
    C = np.zeros((k, k))
    for i in range(1, len(labels)):
        if valid is not None and not valid[i]:
            continue
        C[labels[i - 1], labels[i]] += 1
    row = C.sum(1, keepdims=True)
    row[row == 0] = 1
    return C, C / row


def run_lengths(labels: np.ndarray, valid: np.ndarray | None = None) -> dict:
    """每个状态的连续游程长度列表（单位：样本数）。"""
    labels = np.asarray(labels, int)
    out = {}
    cur, start = labels[0], 0
    for i in range(1, len(labels)):
        if valid is not None and not valid[i]:
            if i - 1 - start > 0:
                out.setdefault(cur, []).append(i - start)
            cur, start = labels[i], i
            continue
        if labels[i] != cur:
            out.setdefault(cur, []).append(i - start)
            cur, start = labels[i], i
    out.setdefault(cur, []).append(len(labels) - start)
    return out


# ============================================================ 4. 采样 / 效应量
def sampling_jitter(series: Series, max_dt=GAP_SEC):
    d = np.diff(series.t)
    d = d[d <= max_dt]
    return d


def effective_n(x: np.ndarray, max_lag: int = 60) -> float:
    """自相关修正后的有效样本量：n_eff = n / (1 + 2Σρ_k)。"""
    ac = autocorr(x, min(max_lag, len(x) // 4))
    s = float(np.sum(ac[1:]))
    infl = max(1.0 + 2 * s, 1.0)
    return len(x) / infl


def mde(x: np.ndarray, minutes: float, alpha=0.05, power=0.8) -> float:
    """给定窗口时长，两组均值差异的最小可检测量（Welch 近似，单尾向）"""
    sd = float(np.std(x, ddof=1))
    n = max(int(minutes * 60 / NOMINAL_DT), 3)
    n_eff = effective_n(x) / len(x) * n          # 按同样的自相关膨胀折算
    n_eff = max(n_eff, 2.0)
    za = 1.959963985 if alpha == 0.05 else 1.644853627
    zb = 0.8416212336 if power == 0.8 else 1.2815515655
    return (za + zb) * sd * math.sqrt(2 / n_eff)


# ============================================================ 5. 小工具
def fmt(v, nd=1):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return '—'
    if isinstance(v, float):
        return ('%.' + str(nd) + 'f') % v
    return str(v)


def fmt_wh(v):
    return ('%.0f' % v) + ' Wh'


def hms(sec: float) -> str:
    sec = int(round(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h:
        return '%dh%02dm' % (h, m)
    if m:
        return '%dm%02ds' % (m, s)
    return '%ds' % s


def resample_minutes(series: Series, minutes: int, mask=None):
    """按墙钟分钟桶聚合，返回 (桶起点秒, 均值 p, 均值 clk, 均值 util, n)。"""
    step = minutes * 60.0
    if mask is None:
        mask = np.ones(len(series.t), bool)
    t = series.t[mask]
    if len(t) == 0:
        return np.array([]), np.array([]), np.array([]), np.array([]), np.array([])
    key = np.floor((t - t[0]) / step).astype(int)
    keys = np.unique(key)
    mp, mc, mu, nn = [], [], [], []
    for k in keys:
        m = key == k
        mp.append(float(np.mean(series.p[mask][m])))
        mc.append(float(np.mean(series.clk[mask][m])))
        mu.append(float(np.mean(series.util[mask][m])))
        nn.append(int(m.sum()))
    return t[0] + keys * step, np.array(mp), np.array(mc), np.array(mu), np.array(nn)


if __name__ == '__main__':
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(here, '..', '..', 'genshin_tweak_power.csv')
    s = load_power(src)
    print('rows', s.n_raw, 'used', len(s), 'dropped', s.n_drop)
    print('span', s.t0, '->', s.datetime(len(s) - 1), 'wall %.2f h' % (s.wall_span / 3600))
    print('covered %.2f h' % (s.covered_seconds() / 3600))
    print('gaps', len(s.gaps()))
    for k in ('p', 'clk', 'util', 'temp', 'cpu'):
        d = describe(getattr(s, k))
        print(k, 'mean %.1f sd %.1f med %.1f p95 %.1f max %.1f' %
              (d['mean'], d['sd'], d['median'], d['p95'], d['max']))
    e, h = s.energy_wh()
    print('energy %.0f Wh over %.2f h' % (e, h))
    ev = load_events(os.path.join(here, '..', '..', 'genshin_tweak_log.csv'))
    print('events', len(ev), ev[0]['time'], ev[-1]['time'])
