#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试套件共用脚手架。

各套件 `from _harness import *` 即可拿到 hs(被测模块)、check()、以及起本地服务/
浏览器的工具。抽出来是因为原先每个文件都抄一遍这几十行, 改一处要改七遍。
"""
import os
import sys
import json
import socket
import functools
import threading
import subprocess
import http.server
import time
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, '..', '..'))
SERVER_PY = os.path.join(REPO, 'scripts', 'health_server.py')

# 本机是 TUN 全局代理, 不清掉的话连 127.0.0.1 都会被绕进代理
for _k in ('http_proxy', 'https_proxy', 'all_proxy',
           'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
    os.environ.pop(_k, None)
os.environ['no_proxy'] = os.environ['NO_PROXY'] = '127.0.0.1,localhost'

os.environ.setdefault('DB_HOST', '127.0.0.1')
os.environ.setdefault('DB_PORT', '3307')
os.environ.setdefault('DB_USER', 'root')
os.environ.setdefault('DB_NAME', 'h6dp_suifang_dev')
# 生成类接口必须在"没配大模型"的前提下也能跑 —— 那是生产的实际状态
os.environ.pop('SCALE_LLM_PROVIDER', None)
os.environ.pop('ANTHROPIC_API_KEY', None)
os.environ.pop('ANTHROPIC_AUTH_TOKEN', None)

if not os.environ.get('DB_PASSWORD'):
    print('请通过环境变量提供 DB_PASSWORD (不写进文件)')
    sys.exit(2)

_spec = importlib.util.spec_from_file_location('hs', SERVER_PY)
hs = importlib.util.module_from_spec(_spec)
sys.modules['hs'] = hs
_spec.loader.exec_module(hs)

FAIL = []


def check(name, cond, extra=''):
    print(('  ✅ ' if cond else '  ❌ ') + name + (('  ' + str(extra)[:180]) if extra else ''))
    if not cond:
        FAIL.append(name)


def section(title):
    print('\n=== ' + title + ' ===')


def sub(title):
    print('  -- ' + title + ' --')


def finish():
    print('\n' + '=' * 66)
    if FAIL:
        print('❌ %d 项失败:' % len(FAIL))
        for x in FAIL:
            print('   -', x)
        sys.exit(1)
    print('✅ 全部通过')
    sys.exit(0)


def db(*statements):
    """跑几条 SQL(建表/清场用)。单条报错不影响后面 —— 清场时表可能还不存在。

    每条可以写成 'SQL'、('SQL',) 或 ('SQL', params)。三种都接受是因为清场语句里
    带不带参数的都有, 强制统一成一种写法只会让调用点到处补空元组。
    """
    conn = hs.get_connection()
    cur = conn.cursor()
    for st in statements:
        if isinstance(st, (tuple, list)):
            sql = st[0]
            params = st[1] if len(st) > 1 else ()
        else:
            sql, params = st, ()
        try:
            cur.execute(sql, params)
        except Exception:
            pass
    cur.close()
    conn.close()


def ensure_all_tables():
    for fn in ('ensure_platform_tables', 'ensure_platform_scale_tables',
               'ensure_platform_qc_tables', 'ensure_platform_crf_tables',
               'ensure_platform_edu_tables', 'ensure_platform_vital_daily',
               'ensure_platform_doc_tables', 'ensure_platform_cohort_tables',
               'ensure_platform_screening_table'):
        if hasattr(hs, fn):
            getattr(hs, fn)()


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start_backend(extra_env=None, log_path=None):
    """起一个本地 health_server 实例, 返回 (proc, base_url)。

    打的是本地 dev 库, 不是生产 —— 端到端测试要先造出问题数据, 不该往生产塞测试患者。
    """
    port = free_port()
    env = dict(os.environ, PORT=str(port))
    if extra_env:
        env.update(extra_env)
    out = open(log_path, 'w') if log_path else subprocess.DEVNULL
    proc = subprocess.Popen([sys.executable, '-u', SERVER_PY], env=env,
                            stdout=out, stderr=subprocess.STDOUT)
    base = 'http://127.0.0.1:%d' % port
    import urllib.request
    for _ in range(50):
        try:
            urllib.request.urlopen(base + '/api/status', timeout=1).read()
            return proc, base
        except Exception:
            time.sleep(0.4)
    proc.kill()
    raise RuntimeError('本地后端起不来' + (', 见 ' + log_path if log_path else ''))


def start_static():
    """起一个静态服务托 prototype/ 目录, 返回 base_url。"""
    port = free_port()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=os.path.join(REPO, 'prototype'))
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return 'http://127.0.0.1:%d' % port


def new_page(pw, viewport=None):
    """开一个 chromium 页面并挂上错误收集。返回 (browser, page, errs, dialogs)。

    dialogs 是**唯一**的原生弹窗收集器: 每条消息记进列表并 dismiss。
    不要在各节里另行 page.on('dialog', ...) —— 多个处理器会抢同一个弹窗,
    谁先 dismiss 谁生效, 后面的拿到的是个已经处理过的对象, 表现是断言随机失败。
    要"确认"而不是"取消"的场景, 用 accept_next(page) 临时改行为。

    channel='chromium': 这台机器上缺 chrome-headless-shell。
    --no-proxy-server: TUN 全局代理会拦 localhost。
    "Failed to load resource" 是浏览器对 HTTP 非 2xx 的网络层日志, 不是 JS 报错 ——
    有些测试会**故意**打 4xx 验证报错文案, 那些不该算失败; 真正的 JS 异常走 pageerror。
    """
    browser = pw.chromium.launch(channel='chromium', args=['--no-proxy-server'])
    ctx = browser.new_context(viewport=viewport or {'width': 1500, 'height': 1000},
                              accept_downloads=True)
    page = ctx.new_page()
    errs = []
    page.on('pageerror', lambda e: errs.append('pageerror: ' + str(e)))
    page.on('console', lambda m: errs.append('console.error: ' + m.text)
            if m.type == 'error' and 'Failed to load resource' not in m.text else None)
    dialogs = []

    def _on_dialog(d):
        dialogs.append(d.message)
        try:
            if getattr(page, '_accept_dialogs', False):
                d.accept(getattr(page, '_dialog_text', '') or '')
            else:
                d.dismiss()
        except Exception:
            pass
    page.on('dialog', _on_dialog)
    page._accept_dialogs = False
    return browser, page, errs, dialogs


def accept_dialogs(page, on=True, text=''):
    """让接下来的原生弹窗走"确定"而不是"取消"。用完记得关回去。"""
    page._accept_dialogs = on
    page._dialog_text = text
