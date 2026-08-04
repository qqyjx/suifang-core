#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定时配置快照 —— §CRF「实时备份」的另一半。

守两件事:

1. **别让"备份"两个字造成误解。** 快照只含配置定义, 不含患者数据。
   等到需要恢复的那天才发现"原来患者数据不在里面", 就太晚了 ——
   所以每一个返回里都得带这句话。

2. **恢复不覆盖、不删。** 同 M24 回滚的理由: 已经按某一版填过的数据还钉在那一版上,
   把它改掉那些数据就读不懂了。

    DB_PASSWORD=xxx python3 scripts/tests/test_snapshot.py
"""
import os
import sys
import gzip
import json
import tempfile
import shutil

SNAPTMP = tempfile.mkdtemp(prefix='suifang_snaptest_')
os.environ['PLATFORM_SNAPSHOT_DIR'] = SNAPTMP
os.environ['PLATFORM_SNAPSHOT_KEEP'] = '3'

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import *          # noqa: F401,F403
from _harness import hs, check, section, sub, finish, db, need_db

P = 'TSNAP'
DEF_V1 = {'items': [{'id': 'a', 'text': '第一版的题', 'type': 'text'}]}
DEF_V2 = {'items': [{'id': 'a', 'text': '第一版的题', 'type': 'text'},
                    {'id': 'b', 'text': '第二版加的题', 'type': 'number'}]}

section('SNAP-1 文件名与读取的边界')
d, err = hs.read_snapshot('../../etc/passwd')
check('路径穿越读不到东西', d is None and err, err)
d, err = hs.read_snapshot('随便一个名字.json.gz')
check('不是 snapshot_ 开头的不认', d is None and err and '不合法' in err, err)
d, err = hs.read_snapshot('snapshot_20990101_000000.json.gz')
check('不存在的快照给明确错误', d is None and err and '不存在' in err, err)

if need_db('快照的生成 / 差异 / 恢复'):
    hs.ensure_platform_crf_tables()
    db(("DELETE FROM platform_crf WHERE code LIKE %s", (P + '%',)),
       ("DELETE FROM platform_crf_response WHERE crf_code LIKE %s", (P + '%',)))
    hs.upsert_platform_crf({'code': P + 'A', 'name': '快照测试表', 'definition': DEF_V1,
                            'owner': 'tester'})

    section('SNAP-2 做一份快照')
    info, err = hs.take_snapshot(reason='test', operator='tester')
    check('快照做得出来', err is None and info and info['file'], err)
    check('落到磁盘上了', info and os.path.isfile(os.path.join(SNAPTMP, info['file'])))
    check('是压缩过的', info and info['file'].endswith('.json.gz'))
    check('给出内容哈希', info and len(info.get('sha256') or '') == 64)
    check('**返回里明说不含患者数据** —— "备份"太容易被理解成患者数据也备份了',
          info and '不含任何患者数据' in info['note'], (info or {}).get('note'))
    check('如实报出各表各备了多少行', info and isinstance(info['counts'], dict)
          and info['counts'].get('platform_crf', 0) >= 1, (info or {}).get('counts'))
    check('目录里没有留下半截的 .part 文件 —— 断电时要么完整要么没有, '
          '不能是个坏文件等着将来被发现',
          not any(f.endswith('.part') for f in os.listdir(SNAPTMP)), os.listdir(SNAPTMP))

    raw = gzip.decompress(open(os.path.join(SNAPTMP, info['file']), 'rb').read())
    snap = json.loads(raw.decode('utf-8'))
    check('快照里确实有刚建的那张表',
          any(r['code'] == P + 'A' for r in snap['data']['platform_crf']))
    tables = set(snap['data'])
    check('只备份配置类的表, 没有把填报/患者表带进去',
          not any(t in tables for t in ('platform_crf_response', 'platform_patient',
                                        'platform_scale_response', 'platform_alarm')),
          sorted(tables))

    section('SNAP-3 快照 vs 现状的差异')
    hs.upsert_platform_crf({'code': P + 'A', 'name': '快照测试表', 'definition': DEF_V2,
                            'owner': 'tester', 'version': '2'})
    hs.upsert_platform_crf({'code': P + 'NEW', 'name': '快照之后才建的',
                            'definition': DEF_V1, 'owner': 'tester'})
    diff, err = hs.snapshot_diff(info['file'], 'platform_crf')
    check('差异算得出来', err is None and diff, err)
    check('快照之后新建的表被列进 only_now',
          any(P + 'NEW' in x for x in diff['only_now']), diff['only_now'][:5])
    check('快照之后新增的版本也在 only_now',
          any(P + 'A v2' in x for x in diff['only_now']), diff['only_now'][:5])
    check('没动过的那一版算 same, 不算 differs',
          not any(P + 'A v1' in x for x in diff['content_differs']),
          diff['content_differs'][:5])
    check('说清了 content_differs 意味着什么(同版本内容变了本不该发生)',
          '不该发生' in diff['note'])
    d2, err = hs.snapshot_diff(info['file'], 'platform_patient')
    check('不在备份范围内的表不给查', d2 is None and err, err)

    section('SNAP-4 恢复: 产出新版本, 不覆盖也不删')
    r, err = hs.restore_from_snapshot({'file': info['file'], 'code': P + 'A'})
    check('不署名不给恢复', r is None and '署名' in (err or ''), err)
    r, err = hs.restore_from_snapshot({'file': info['file'], 'code': P + 'A',
                                       'operator': 'tester'})
    check('不写原因不给恢复 —— 这会让线上换成另一份配置', r is None and '原因' in (err or ''), err)
    r, err = hs.restore_from_snapshot({'file': info['file'], 'code': 'NOSUCH',
                                       'operator': 'tester', 'reason': 'x'})
    check('快照里没有的 code 给明确错误', r is None and err, err)

    r, err = hs.restore_from_snapshot({'file': info['file'], 'code': P + 'A',
                                       'operator': 'tester', 'reason': '第二版改错了'})
    check('恢复成功', err is None and r, err)
    check('**产出的是新版本**, 不是就地改回去', r and r['new_version'] not in ('1', '2'),
          (r or {}).get('new_version'))
    # all_versions=True: 默认那条路刻意只给最新一版(列表页要的是"有哪些表"),
    # 这里要查的恰恰是"旧版还在不在"
    got, _ = hs.query_platform_crfs(code=P + 'A', with_definition=True, all_versions=True)
    vers = sorted(c['version'] for c in got['crfs'])
    check('原来的 v1 v2 一个没少 —— 按 v2 填过的数据还钉在 v2 上',
          '1' in vers and '2' in vers and len(vers) >= 3, vers)
    latest, _ = hs.query_platform_crfs(code=P + 'A')
    check('默认那条路仍然只给一版 —— updated_at 只到秒, 同秒写进去的几版'
          '以前会一起命中 MAX, 列表页把同一张表列成好几行',
          len(latest['crfs']) == 1 and latest['crfs'][0]['version'] == r['new_version'],
          [c['version'] for c in latest['crfs']])
    newest = [c for c in got['crfs'] if c['version'] == r['new_version']][0]
    check('新版本的内容确实是快照里那一版(只有 1 道题)',
          len(newest['definition']['items']) == 1, newest['definition'])
    check('返回里说清了"产出新版本"这件事', '新版本' in r['note'])

    section('SNAP-5 同一秒做两份不会互相覆盖')
    # 内容不同才算两份 —— 所以每轮改一下配置
    made = []
    for i in range(5):
        hs.upsert_platform_crf({'code': P + 'P%d' % i, 'name': 'prune%d' % i,
                                'definition': DEF_V1, 'owner': 'tester'})
        info_i, e_i = hs.take_snapshot(reason='prune-test-%d' % i)
        if info_i:
            made.append(info_i['file'])
    check('连着做 5 份, 文件名各不相同 —— 只精确到秒的话会同名互相覆盖, 而且是静默的',
          len(set(made)) == len(made) and len(made) == 5, made)

    files_after = [f for f in os.listdir(SNAPTMP) if f.startswith('snapshot_')]
    check('超出保留份数的旧快照被清掉(本次设 KEEP=3)',
          len(files_after) == 3, '现在 %d 份: %s' % (len(files_after), sorted(files_after)))
    check('留下的是最新的那几份', sorted(files_after) == sorted(made[-3:]),
          sorted(files_after))
    lst, err = hs.list_snapshots()
    check('列表读得出来且按时间倒序', err is None and lst['count'] == len(files_after),
          (lst or {}).get('count'))
    check('列表里也带着"不含患者数据"这句话', '不含任何患者数据' in lst['note'])

    sub('清理')
    db(("DELETE FROM platform_crf WHERE code LIKE %s", (P + '%',)))
    print('  ✅ 测试数据已清')

shutil.rmtree(SNAPTMP, ignore_errors=True)
finish()
