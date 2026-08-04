#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""填报数据的修订链: 历史 · 改前改后 · 回退 (§4.3 修改留痕剩下的那半)。

守的核心是一条: **任何操作都不能让"这条数据曾经被改过"从记录里消失。**
回退最容易想到的实现是把旧记录从 superseded 改回 submitted —— 那正好是
把修改痕迹抹掉, 而留痕要留的就是它。

    DB_PASSWORD=xxx python3 scripts/tests/test_revision.py
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import *          # noqa: F401,F403
from _harness import hs, check, section, sub, finish, db, need_db

P = 'TREV'
CRF_DEF = {'items': [
    {'id': 'has_ae', 'text': '本次随访期间是否发生不良事件', 'type': 'single',
     'options': [{'label': '是', 'value': 1}, {'label': '否', 'value': 0}]},
    {'id': 'ae_desc', 'text': '不良事件描述', 'type': 'paragraph'},
    {'id': 'weight', 'text': '体重(kg)', 'type': 'number', 'min': 20, 'max': 200},
    {'id': 'symptoms', 'text': '症状', 'type': 'multi',
     'options': [{'label': '头晕', 'value': 'a'}, {'label': '恶心', 'value': 'b'}]},
]}

section('REV-1 逐题差异: 三种变化都要报出来')
item_map = hs._rev_item_map('crf', CRF_DEF)
d = hs.diff_response_data('crf', {'has_ae': 0, 'weight': 70},
                          {'has_ae': 1, 'weight': 70, 'ae_desc': '头痛'}, item_map)
by = {x['field']: x for x in d}
check('改了值的报 changed', by.get('has_ae', {}).get('change') == 'changed')
check('**用题干显示, 不是字段 id** —— 稽查看 "has_ae: 0 -> 1" 是没有意义的',
      by['has_ae']['label'] == '本次随访期间是否发生不良事件', by['has_ae']['label'])
check('选项题显示标签而不是裸值',
      by['has_ae']['before'].startswith('否') and by['has_ae']['after'].startswith('是'),
      (by['has_ae']['before'], by['has_ae']['after']))
check('新答的报 added', by.get('ae_desc', {}).get('change') == 'added')
check('没变的不进差异', 'weight' not in by, list(by))

d2 = hs.diff_response_data('crf', {'has_ae': 1, 'ae_desc': '头痛'}, {'has_ae': 1}, item_map)
by2 = {x['field']: x for x in d2}
check('**答案被删掉要报 removed** —— 这一种最容易漏, 而"把答案删了"恰恰最该看',
      by2.get('ae_desc', {}).get('change') == 'removed', d2)
d3 = hs.diff_response_data('crf', {'symptoms': ['a']}, {'symptoms': ['a', 'b']}, item_map)
check('多选题按标签展开', d3 and '头晕' in d3[0]['after'] and '恶心' in d3[0]['after'],
      d3 and d3[0]['after'])
check('原始值也一并留着(供程序用)', d3 and d3[0]['after_raw'] == ['a', 'b'])
d4 = hs.diff_response_data('crf', {'x': 1}, {'x': 2}, {})
check('定义取不到时差异照出, 只是显示字段 id —— 不能因为查不到定义就不给差异',
      d4 and d4[0]['label'] == 'x', d4)

if need_db('修订链与回退'):
    hs.ensure_platform_crf_tables()
    db(("DELETE FROM platform_crf_response WHERE patient_no LIKE %s", (P + '%',)),
       ("DELETE FROM platform_crf WHERE code=%s", (P + 'CRF',)))
    hs.upsert_platform_crf({'code': P + 'CRF', 'name': '修订测试表',
                            'definition': CRF_DEF, 'owner': 'tester'})

    section('REV-2 修订链')
    r1, err = hs.submit_crf_response({'crf_code': P + 'CRF', 'patient_no': P + '001',
                                      'data': {'has_ae': 0, 'weight': 70},
                                      'operator': '护士甲'})
    check('第一次填报', err is None and r1 and r1.get('id'), err)
    r2, err = hs.submit_crf_response({'crf_code': P + 'CRF', 'patient_no': P + '001',
                                      'data': {'has_ae': 1, 'ae_desc': '头痛', 'weight': 70},
                                      'operator': '医生乙', 'revision_of': r1['id']})
    check('第一次修订', err is None and r2, err)
    r3, err = hs.submit_crf_response({'crf_code': P + 'CRF', 'patient_no': P + '001',
                                      'data': {'has_ae': 1, 'ae_desc': '头痛加重',
                                               'weight': 68},
                                      'operator': '医生乙', 'revision_of': r2['id']})
    check('第二次修订', err is None and r3, err)

    h, err = hs.response_history('crf', r1['id'])
    check('从链上任意一条都能取到整条链', err is None and h['revision_count'] == 3,
          (err, (h or {}).get('revision_count')))
    check('顺序是从旧到新', [x['id'] for x in h['revisions']] == [r1['id'], r2['id'], r3['id']])
    check('当前版是最后一条', h['current_id'] == r3['id'])
    check('每一版都记着是谁改的',
          [x['operator'] for x in h['revisions']] == ['护士甲', '医生乙', '医生乙'])
    h2, _ = hs.response_history('crf', r3['id'])
    check('从最新一条往回查也得到同一条链',
          [x['id'] for x in h2['revisions']] == [x['id'] for x in h['revisions']])

    sub('每一步的改前改后')
    step2 = h['revisions'][1]['changes']
    f = {x['field']: x for x in step2}
    check('第 1 次改动: 不良事件 否 -> 是',
          f['has_ae']['before'].startswith('否') and f['has_ae']['after'].startswith('是'), f)
    check('并且带上题干', f['has_ae']['label'] == '本次随访期间是否发生不良事件')
    check('新增的描述算 added', f['ae_desc']['change'] == 'added')
    step3 = {x['field']: x for x in h['revisions'][2]['changes']}
    check('第 2 次改动: 体重 70 -> 68', step3['weight']['before'] == '70'
          and step3['weight']['after'] == '68', step3.get('weight'))
    check('第一版没有"改前"(它就是最初那版)', h['revisions'][0]['changes'] == [])

    section('REV-3 回退: 产出新修订, 不复活旧记录')
    r, err = hs.revert_response({'kind': 'crf', 'id': r3['id'], 'to_id': r1['id']})
    check('不署名不给回退', r is None and '署名' in (err or ''), err)
    r, err = hs.revert_response({'kind': 'crf', 'id': r3['id'], 'to_id': r1['id'],
                                 'operator': 'tester'})
    check('不写原因不给回退 —— 这会改变这位患者的现行数据', r is None and '原因' in (err or ''), err)
    r, err = hs.revert_response({'kind': 'crf', 'id': r3['id'], 'to_id': 999999,
                                 'operator': 'tester', 'reason': 'x'})
    check('目标不在这条链上 -> 拒(不能拿别人的记录来回退)', r is None and err, err)
    r, err = hs.revert_response({'kind': 'crf', 'id': r3['id'], 'to_id': r3['id'],
                                 'operator': 'tester', 'reason': 'x'})
    check('目标就是当前版 -> 明确说不用回退', r is None and '不需要' in (err or ''), err)

    rv, err = hs.revert_response({'kind': 'crf', 'id': r3['id'], 'to_id': r1['id'],
                                  'operator': '数据管理员', 'reason': '第二次修订认错了患者'})
    check('回退成功', err is None and rv and rv.get('new_id'), err)
    check('**产出的是新的一条**, 不是把旧记录复活', rv['new_id'] not in
          (r1['id'], r2['id'], r3['id']), rv.get('new_id'))

    conn = hs.get_connection(); cur = conn.cursor()
    cur.execute('SELECT id, status FROM platform_crf_response WHERE patient_no=%s ORDER BY id',
                (P + '001',))
    rows = cur.fetchall(); cur.close(); conn.close()
    st = dict(rows)
    check('链上一条没少(3 条旧的 + 1 条新的)', len(rows) == 4, rows)
    check('**被回退掉的那一版仍然是 superseded, 没有被复活** —— '
          '复活等于让"曾经改过"这件事从记录里消失',
          st[r3['id']] == 'superseded', st)
    check('最初那版也仍然是 superseded(回退不等于把它变回现行)',
          st[r1['id']] == 'superseded', st)
    check('只有新产出的那条是现行版', st[rv['new_id']] == 'submitted')

    h3, _ = hs.response_history('crf', rv['new_id'])
    check('回退之后链变成 4 版', h3['revision_count'] == 4, h3['revision_count'])
    check('新版内容等于回退目标那一版',
          h3['revisions'][-1]['field_count'] == 2, h3['revisions'][-1])
    back = {x['field']: x for x in h3['revisions'][-1]['changes']}
    check('回退这一步本身也留下了逐题改前改后',
          back.get('has_ae', {}).get('after', '').startswith('否')
          and back.get('ae_desc', {}).get('change') == 'removed', back)
    check('返回里说清了"没有任何一版被删"', '一版没少' in rv['note'])

    sub('回退产生的数据同样要过校验')
    # 直接往库里塞一条**绕过校验**的历史记录, 再回退到它。库里出现这种行是有可能的:
    # 早期代码写进去的、迁移脚本导入的、或者当时那一版的规则比现在松。
    # 回退如果只是把旧值原样搬回来, 这条不合法的数据就悄悄变成了现行数据。
    ok1, _ = hs.submit_crf_response({'crf_code': P + 'CRF', 'patient_no': P + '002',
                                     'data': {'has_ae': 0, 'weight': 70}, 'operator': 't'})
    conn = hs.get_connection(); cur = conn.cursor()
    cur.execute("""INSERT INTO platform_crf_response
                   (crf_code, crf_version, patient_no, data, operator, status, revision_of)
                   VALUES (%s,'1',%s,%s,'t','submitted',%s)""",
                (P + 'CRF', P + '002',
                 json.dumps({'has_ae': 0, 'weight': 5000}, ensure_ascii=False), ok1['id']))
    raw_id = cur.lastrowid
    cur.execute("UPDATE platform_crf_response SET status='superseded' WHERE id=%s", (ok1['id'],))
    cur.close(); conn.close()
    ok2, _ = hs.submit_crf_response({'crf_code': P + 'CRF', 'patient_no': P + '002',
                                     'data': {'has_ae': 0, 'weight': 72},
                                     'operator': 't', 'revision_of': raw_id})
    rv2, err2 = hs.revert_response({'kind': 'crf', 'id': ok2['id'], 'to_id': raw_id,
                                    'operator': 't', 'reason': '试试回退到一条不合法的历史值'})
    check('回退到一条不合法的历史记录 -> 被拒, 而不是硬塞回去 —— '
          '回退走的是正常提交路径, 照样过校验; 直接 INSERT 的话这条 5000kg 就成了现行数据',
          rv2 is None and err2 and '校验' in err2, err2)
    rv3, err3 = hs.revert_response({'kind': 'crf', 'id': ok2['id'], 'to_id': ok1['id'],
                                    'operator': 't', 'reason': '回到合法的那一版'})
    check('回退到合法的历史版仍然正常', err3 is None and rv3, err3)

    section('REV-4 边界')
    r, err = hs.response_history('nosuch', 1)
    check('kind 只认 crf/scale', r is None and err, err)
    r, err = hs.response_history('crf', 99999999)
    check('记录不存在给明确错误', r is None and err, err)
    r, err = hs.response_history('crf', 'abc')
    check('id 不是整数给明确错误', r is None and err, err)

    sub('清理')
    db(("DELETE FROM platform_crf_response WHERE patient_no LIKE %s", (P + '%',)),
       ("DELETE FROM platform_crf WHERE code=%s", (P + 'CRF',)))
    print('  ✅ 测试数据已清')

finish()
