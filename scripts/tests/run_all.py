#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""依次跑全部测试套件。

    DB_PASSWORD=xxx python3 scripts/tests/run_all.py
    DB_PASSWORD=xxx python3 scripts/tests/run_all.py --no-ui     # 跳过浏览器测试

每个套件在独立进程里跑 —— 一个套件崩了不影响后面的, 而且各自的建表/清场互不干扰。
"""
import os
import sys
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = ['test_platform_safety.py', 'test_m13_qc.py', 'test_m14_crf.py',
           'test_m15_edu.py', 'test_m16_search.py', 'test_m17_doc.py',
           'test_m18_cohort.py', 'test_m19_flow.py', 'test_m20_push.py']
UI = ['test_ui_platform_v2.py']

if not os.environ.get('DB_PASSWORD'):
    print('请通过环境变量提供 DB_PASSWORD')
    sys.exit(2)

suites = BACKEND + ([] if '--no-ui' in sys.argv else UI)
results, t0 = [], time.time()
for name in suites:
    path = os.path.join(HERE, name)
    if not os.path.isfile(path):
        print('⚠  跳过(文件不存在): ' + name)
        continue
    print('\n' + '━' * 70)
    print('▶ ' + name)
    print('━' * 70)
    t = time.time()
    proc = subprocess.run([sys.executable, path], capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    passed = out.count('  ✅ ')
    failed = [l for l in out.split('\n') if l.startswith('  ❌ ')]
    for l in failed:
        print(l)
    if proc.returncode != 0 and not failed:
        print(out[-1500:])
    print('  %s  %d 项通过, %d 项失败  (%.1fs)' %
          ('✅' if proc.returncode == 0 else '❌', passed, len(failed), time.time() - t))
    results.append((name, proc.returncode == 0, passed, len(failed)))

print('\n' + '═' * 70)
total_p = sum(r[2] for r in results)
total_f = sum(r[3] for r in results)
for name, ok, p, f in results:
    print('  %s %-30s %4d 通过  %d 失败' % ('✅' if ok else '❌', name, p, f))
print('─' * 70)
print('  合计 %d 项通过, %d 项失败, 用时 %.1fs' % (total_p, total_f, time.time() - t0))
sys.exit(0 if all(r[1] for r in results) else 1)
