# 功耗数据全面统计分析（analysis/full）

一键把 `genshin_tweak_power.csv`（+ `genshin_tweak_log.csv`）变成一套完整统计：
14 张图、一份 Markdown 报告、一份单文件 HTML 看板（含 PDF）、8 张派生 CSV 表。

## 用法

```bash
cd analysis/full
python3 build.py            # 全量：快照 → 统计 → 图 → 报告 → 看板 → 表
python3 build.py --no-png   # 只出 SVG / 报告，不调 chromium 截图
```

产物都在 `out/`：

| 路径 | 内容 |
|---|---|
| `out/report.md` | 主报告（16 节） |
| `out/dashboard.html` | 单文件看板，SVG 全内嵌，离线可开 |
| `out/report.pdf` | 看板打印版 |
| `out/charts/fig01…fig14.{svg,png}` | 图 |
| `out/tables/*.csv` | minute / hour / state / changepoints / summary / windows / gaps / events |
| `out/data/snapshot-*.csv` | 本次分析用的冻结快照 |

## 代码结构

| 文件 | 职责 |
|---|---|
| `gta.py` | 数据层（解析/缺口/能量积分）+ 统计原语（描述统计、自相关、GMM-EM、k-means、二分割变化点、OLS、轮廓系数、标签去抖） |
| `pipeline.py` | 把全部统计量算进一个 `ctx`，供图和报告共用 |
| `charts.py` | 手写 SVG 工具（Canvas/Panel/热力图/分段折线），不依赖 matplotlib |
| `figures.py` | 14 张图 |
| `report.py` | Markdown 报告 + HTML 看板 + 派生 CSV |
| `build.py` | 一键重建 |
| `shot.sh` | SVG → PNG（chromium 无头，2 倍图） |

设计原则：
1. **所有数字只算一次**（`pipeline`），图和文字都从 `ctx` 取，避免图文不一致。
2. 缺口一律不跨、不插值；能量按「后一样本功率 × 其前面时间片（≤15 s）」积分。
3. 假设检验用实测噪声底（相邻窗口差），不用理论 σ/√n；并给出自相关修正后的有效样本量。
