#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build.py —— 一键重建：数据快照 → 统计 → 图 → 报告 → 看板 → 派生表。

用法：
    cd analysis/full
    python3 build.py            # 全量
    python3 build.py --no-png   # 只出 SVG（不调 chromium）
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

import figures
import pipeline
import report

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
OUT = os.path.join(HERE, 'out')
CHARTS = os.path.join(OUT, 'charts')
TABLES = os.path.join(OUT, 'tables')

FIG_ORDER = [
    ('fig01_overview', figures.fig_overview),
    ('fig02_dist', figures.fig_dist),
    ('fig03_gmm', figures.fig_gmm),
    ('fig04_surface', figures.fig_surface),
    ('fig05_pclk', figures.fig_pclk),
    ('fig06_states', figures.fig_states),
    ('fig07_trans', figures.fig_trans),
    ('fig08_acf', figures.fig_acf),
    ('fig09_cp', figures.fig_cp),
    ('fig10_energy', figures.fig_energy),
    ('fig11_sampling', figures.fig_sampling),
    ('fig12_detect', figures.fig_detect),
    ('fig13_fitdiag', figures.fig_fitdiag),
    ('fig14_budget', figures.fig_budget),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-png', action='store_true')
    args = ap.parse_args()

    os.makedirs(CHARTS, exist_ok=True)
    os.makedirs(TABLES, exist_ok=True)
    # 清掉旧快照，只留最新一份
    snapdir = os.path.join(OUT, 'data')
    for fn in os.listdir(snapdir) if os.path.isdir(snapdir) else []:
        os.remove(os.path.join(snapdir, fn))

    print('== 统计 ==')
    ctx = pipeline.build(REPO, OUT)

    print('== 图 ==')
    for name, fn in FIG_ORDER:
        fn(ctx, os.path.join(CHARTS, name + '.svg'))
        print('  ', name)

    if not args.no_png:
        print('== 截图 ==')
        subprocess.run([os.path.join(HERE, 'shot.sh')] + [n for n, _ in FIG_ORDER], check=False)

    print('== 表 ==')
    files = report.write_tables(ctx, TABLES)
    print('  ', ', '.join(files))

    print('== 报告 ==')
    md = report.render_md(ctx)
    open(os.path.join(OUT, 'report.md'), 'w', encoding='utf-8').write(md)
    print(f'   report.md  {len(md)/1024:.0f} KB')

    html = report.render_html(ctx, CHARTS)
    open(os.path.join(OUT, 'dashboard.html'), 'w', encoding='utf-8').write(html)
    print(f'   dashboard.html {len(html)/1024/1024:.2f} MB')

    # 可选：把看板打成 PDF（需要 chromium；失败不影响其它产物）
    if not args.no_png:
        pdf = os.path.join(OUT, 'report.pdf')
        try:
            subprocess.run(['chromium', '--headless=new', '--no-sandbox', '--disable-gpu',
                            '--user-data-dir=' + os.path.join(OUT, '.chrome'),
                            '--no-pdf-header-footer', '--print-to-pdf=' + pdf,
                            'file://' + os.path.join(OUT, 'dashboard.html')],
                           check=False, timeout=180,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(pdf):
                print(f'   report.pdf  {os.path.getsize(pdf)/1024/1024:.2f} MB')
        except Exception as e:
            print('   (PDF 生成跳过:', e, ')')

    print('== done ==')


if __name__ == '__main__':
    main()
