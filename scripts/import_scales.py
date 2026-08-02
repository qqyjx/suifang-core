#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 database/scales/*.json 里的量表导入随访平台 (M10)。

用法:
    python3 scripts/import_scales.py                       # 导入全部
    python3 scripts/import_scales.py PHQ-9                 # 只导某几份
    API_BASE=http://192.168.4.104:3000 PLATFORM_TOKEN=xxx python3 scripts/import_scales.py

为什么单独一个脚本而不是服务端启动时自动 seed:
    量表是**内容**不是 schema。启动自动 seed 会让"生产上到底装了哪些量表"变成由代码版本
    决定, 临床方在界面上改过的定义会在下次重启时被悄悄覆盖回去 —— 这是最难查的一类事故。
    导入必须是一次显式的、有人为它负责的动作。

★1.6.2 要求 200+ 个精神科量表, 这里只放了 PHQ-9 / GAD-7 两份免授权的做引擎验证。
其余量表(HAMD/HAMA/YMRS/PSQI/Y-BOCS/MMAS-8/MoCA...)多数有版权与授权要求, 题目和评分
规则须由临床方提供并确认授权后, 按同一份 JSON 结构追加到 database/scales/ 即可。
"""
import os
import sys
import glob
import json
import urllib.request
import urllib.error

API_BASE = os.environ.get('API_BASE') or 'https://dc.ncrc.org.cn/api2'
TOKEN = os.environ.get('PLATFORM_TOKEN') or ''
HERE = os.path.dirname(os.path.abspath(__file__))
# 默认按仓库布局 (scripts/ 的兄弟目录 database/scales)。SCALE_DIR 可覆盖 —— 部署到服务器时
# 脚本和量表文件不再保持仓库里的相对位置, 硬算路径会指到 /opt/database/scales 这种地方。
SCALE_DIR = os.environ.get('SCALE_DIR') or os.path.join(os.path.dirname(HERE), 'database', 'scales')


def post(path, payload):
    req = urllib.request.Request(
        API_BASE + path,
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST')
    if TOKEN:
        req.add_header('X-Platform-Token', TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode('utf-8')), None
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'replace')
        try:
            return None, json.loads(body).get('error') or body
        except ValueError:
            return None, 'HTTP {}: {}'.format(e.code, body[:200])
    except Exception as e:
        return None, str(e)


def main(argv):
    want = set(argv[1:])
    files = sorted(glob.glob(os.path.join(SCALE_DIR, '*.json')))
    if not files:
        print('没找到量表文件:', SCALE_DIR)
        return 1
    print('目标: {}   口令: {}'.format(API_BASE, '已配置' if TOKEN else '未配置(服务端若开了门禁会 403)'))
    ok = fail = skip = 0
    for path in files:
        try:
            with open(path, encoding='utf-8') as fh:
                s = json.load(fh)
        except ValueError as e:
            print('  ✕ {}  JSON 解析失败: {}'.format(os.path.basename(path), e))
            fail += 1
            continue
        if want and s.get('code') not in want:
            skip += 1
            continue
        # license 只是给人看的说明字段, 不入库
        payload = {k: v for k, v in s.items() if k != 'license'}
        r, err = post('/api/platform/scale', payload)
        if err:
            print('  ✕ {:<10} {}'.format(s.get('code'), err))
            fail += 1
        else:
            print('  ✓ {:<10} v{}  {} 题  {}'.format(
                r['code'], r['version'], r['items'], s.get('name', '')))
            ok += 1
    print('\n导入完成: 成功 {} / 失败 {}{}'.format(ok, fail, ' / 跳过 {}'.format(skip) if skip else ''))
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
