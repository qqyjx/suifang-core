#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""随访平台安全不变量回归 (M12-M17)。

这个文件在仓库里而不是临时目录里, 是因为吃过一次亏: 各模块的完整测试套件原本
写在会话临时目录, 被清掉之后一条都不剩。里面攒下的是十几个真 bug 换来的断言,
丢一次就得从头再踩一遍。

这里只收**安全不变量** —— 那些一旦回归就会产生"看着正常的错误结果"的规则。
功能性断言(某个列表能不能翻页之类)不在这儿, 坏了一眼就看得出来。

跑法:
    DB_HOST=127.0.0.1 DB_PORT=3307 DB_USER=root DB_PASSWORD=xxx \
    DB_NAME=h6dp_suifang_dev python3 scripts/tests/test_platform_safety.py

数据库口令走环境变量, 不写在文件里。默认连的是本机 dev 库, 不碰生产。
"""
import os
import sys
import json
import base64
import hashlib
import datetime
import tempfile
import shutil
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, '..', 'health_server.py')

DOC_TMP = tempfile.mkdtemp(prefix='suifang_doctest_')
os.environ.setdefault('DB_HOST', '127.0.0.1')
os.environ.setdefault('DB_PORT', '3307')
os.environ.setdefault('DB_USER', 'root')
os.environ.setdefault('DB_NAME', 'h6dp_suifang_dev')
os.environ['PLATFORM_DOC_DIR'] = DOC_TMP
# 生成类接口必须在"没配大模型"的前提下也能跑 —— 那是生产的实际状态
os.environ.pop('SCALE_LLM_PROVIDER', None)
os.environ.pop('ANTHROPIC_API_KEY', None)

if not os.environ.get('DB_PASSWORD'):
    print('请通过环境变量提供 DB_PASSWORD (不写进文件)')
    sys.exit(2)

spec = importlib.util.spec_from_file_location('hs', SERVER)
hs = importlib.util.module_from_spec(spec)
sys.modules['hs'] = hs
spec.loader.exec_module(hs)

FAIL = []


def check(name, cond, extra=''):
    print(('  ✅ ' if cond else '  ❌ ') + name + (('  ' + str(extra)[:170]) if extra else ''))
    if not cond:
        FAIL.append(name)


def section(t):
    print('\n=== ' + t + ' ===')


PFX = 'SAFET'          # 本套测试造的数据一律用这个前缀, 便于清场


def cleanup():
    conn = hs.get_connection()
    cur = conn.cursor()
    for sql in (
        "DELETE FROM platform_qc_query_log WHERE query_id IN "
        "  (SELECT id FROM platform_qc_query WHERE patient_no LIKE %s)",
        "DELETE FROM platform_qc_query WHERE patient_no LIKE %s",
        "DELETE FROM platform_qc_finding WHERE patient_no LIKE %s",
        "DELETE FROM platform_scale_response WHERE patient_no LIKE %s",
        "DELETE FROM platform_crf_response WHERE patient_no LIKE %s",
        "DELETE FROM platform_consent WHERE patient_no LIKE %s",
        "DELETE FROM platform_vital_daily WHERE patient_no LIKE %s",
        "DELETE FROM platform_patient WHERE patient_no LIKE %s",
    ):
        try:
            cur.execute(sql, (PFX + '%',))
        except Exception:
            pass
    for sql in ("DELETE FROM platform_document_log WHERE doc_code LIKE %s",
                "DELETE FROM platform_document WHERE code LIKE %s",
                "DELETE FROM platform_edu_log WHERE material_id IN "
                "  (SELECT id FROM platform_edu_material WHERE code LIKE %s)",
                "DELETE FROM platform_edu_material WHERE code LIKE %s",
                "DELETE FROM platform_scale WHERE code LIKE %s",
                "DELETE FROM platform_crf WHERE code LIKE %s"):
        try:
            cur.execute(sql, (PFX + '%',))
        except Exception:
            pass
    cur.close()
    conn.close()


for fn in ('ensure_platform_tables', 'ensure_platform_scale_tables', 'ensure_platform_qc_tables',
           'ensure_platform_crf_tables', 'ensure_platform_edu_tables',
           'ensure_platform_vital_daily', 'ensure_platform_doc_tables'):
    getattr(hs, fn)()
cleanup()

conn = hs.get_connection(); cur = conn.cursor()
cur.execute("INSERT INTO platform_patient (patient_no,name,gender,age) VALUES (%s,'安全测试患者','M',60)",
            (PFX + '001',))
cur.close(); conn.close()


# ------------------------------------------------------------------ M12
section('M12 · AI 生成的量表绝不能带划界值分级')
# 编出来的分级阈值会让每一份评估报告都给出看似正常的错误结论 —— 比不给结论坏得多。
draft, report = hs.generate_scale_draft({'goal': '演示', 'dimensions': ['A', 'B'],
                                         'items_per_dimension': 3})
check('schema 里根本没有 levels 的位置',
      'levels' not in json.dumps(hs.SCALE_GEN_SCHEMA))
check('产出的 levels 为空', draft['definition']['scoring']['levels'] == [])
check('report 里 level_count = 0', report['level_count'] == 0)
check('说明里讲清了为什么不给分级',
      any('实证研究' in n['detail'] for n in report['notes']))
check('带免责声明', '未经信效度验证' in draft['definition'].get('disclaimer', ''))
evil = {'definition': {'items': [], 'scoring': {'levels': [
    {'min': 0, 'max': 9, 'label': '正常'}, {'min': 10, 'max': 27, 'label': '重度'}]}}}
notes = []
check('后端硬塞进来的分级会被兜底删掉',
      hs._sanitize_generated(evil, notes)['definition']['scoring']['levels'] == [])
check('删除动作有记账', any(n['step'] == 'levels_stripped' for n in notes))


# ------------------------------------------------------------------ M13
section('M13 · 质控 ≠ 预警, 两条线不能互串')
# 把录入笔误当预警推给医生, 医生几次之后就不看预警了;
# 把真的高热丢进数据待办, 就没人给患者打电话。
check('体温 39℃ 不是质控问题(临床异常, 归 M7 预警)', hs.qc_check_vital('temp', 39.0) == [])
check('体温 366℃ 是质控问题(漏小数点)', len(hs.qc_check_vital('temp', 366.0)) == 1)
check('心率 45 不是质控问题', hs.qc_check_vital('hr', 45) == [])
check('心率 400 是质控问题', len(hs.qc_check_vital('hr', 400)) == 1)
check('血氧 88% 不是质控问题', hs.qc_check_vital('spo2', 88) == [])
check('血氧 101% 是质控问题(定义上不可能)', len(hs.qc_check_vital('spo2', 101)) == 1)
check('80/120 高低压填反被跨字段规则抓到', len(hs.qc_check_bp_pair(80, 120)) == 1)
check('但两个值单看都合法 —— 单字段规则抓不到',
      hs.qc_check_vital('sbp', 80) == [] and hs.qc_check_vital('dbp', 120) == [])

print('  -- 身份证走校验位, 不只是正则 --')
W = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
mk = lambda b: b + '10X98765432'[sum(int(b[i]) * W[i] for i in range(17)) % 11]
good = mk('11010119900307721')
check('合法号放行', hs._qc_check_id_card(good) is None, hs._qc_check_id_card(good))
swapped = good[:14] + good[15] + good[14] + good[16:]
check('相邻两位打颠倒被抓到 —— 正则抓不到这个',
      '校验位' in (hs._qc_check_id_card(swapped) or ''))

print('  -- 逐题完全一致: 只有比历史才看得出来 --')
NINE = {'items': [{'id': 'p%d' % i, 'text': 't', 'type': 'single',
                   'options': [{'label': str(v), 'value': v} for v in range(4)]}
                  for i in range(1, 10)]}
VARIED = {'p1': 2, 'p2': 0, 'p3': 3, 'p4': 1, 'p5': 2, 'p6': 0, 'p7': 1, 'p8': 3, 'p9': 2}
FLAT = {'p%d' % i: 0 for i in range(1, 10)}
prev = {'id': 1, 'answers': dict(VARIED), 'total_score': 14.0, 'created_at': '2026-07-01 10:00'}
f = hs.qc_compare_with_previous(NINE, dict(VARIED), 14.0, prev, PFX + '001', 1)
check('有起伏的答案一字不差重现 -> 报出来',
      any(x['rule_code'] == 'identical_to_previous' for x in f))
flat_prev = {'id': 2, 'answers': dict(FLAT), 'total_score': 0.0, 'created_at': '2026-07-01 10:00'}
f2 = hs.qc_compare_with_previous(NINE, dict(FLAT), 0.0, flat_prev, PFX + '001', 2)
check('一路同档两次一样 -> 不报(筛查量表上这是常态, 报了就天天误报)',
      not any(x['rule_code'] == 'identical_to_previous' for x in f2))


# ------------------------------------------------------------------ M14
section('M14 · 逻辑隐藏的题不能挡住提交, 其旧答案也不能驱动后续逻辑')
YN = [{'label': '是', 'value': 1}, {'label': '否', 'value': 0}]
D = {'items': [{'id': 'a', 'type': 'single', 'text': '有无', 'options': YN, 'required': True},
               {'id': 'b', 'type': 'text', 'text': '详情', 'required': True}],
     'logic': [{'when': {'field': 'a', 'op': 'eq', 'value': 0},
                'then': {'action': 'hide', 'targets': ['b']}}]}
errs, _ = hs.validate_crf_data(D, {'a': 0})
check('b 被隐藏后不再报必填 —— 否则表单永远交不上去, 且报错指向看不见的题', errs == [], errs)
errs, _ = hs.validate_crf_data(D, {'a': 1})
check('b 可见时仍然报必填', any(x['field'] == 'b' for x in errs))

D3 = {'items': [{'id': 'q1', 'type': 'single', 'text': 'A', 'options': YN},
                {'id': 'q2', 'type': 'single', 'text': 'B', 'options': YN},
                {'id': 'q3', 'type': 'text', 'text': 'C', 'hidden': True}],
      'logic': [{'when': {'field': 'q1', 'op': 'eq', 'value': 0},
                 'then': {'action': 'hide', 'targets': ['q2']}},
                {'when': {'field': 'q2', 'op': 'eq', 'value': 1},
                 'then': {'action': 'show', 'targets': ['q3']}}]}
st = hs.eval_crf_logic(D3, {'q1': 0, 'q2': 1, 'q3': '残留内容'})
check('驱动它的题被隐藏后, q3 也收起来(不产生幽灵数据)', st['visible']['q3'] is False)
check('残留答案被标为 stale 而不是静默丢弃', 'q3' in st['stale'])
loop = {'items': [{'id': 'x', 'type': 'text', 'text': 'x'}, {'id': 'y', 'type': 'text', 'text': 'y'}],
        'logic': [{'when': {'field': 'x', 'op': 'empty'}, 'then': {'action': 'hide', 'targets': ['y']}},
                  {'when': {'field': 'y', 'op': 'empty'}, 'then': {'action': 'hide', 'targets': ['x']}}]}
check('互相隐藏的规则不死循环', hs.eval_crf_logic(loop, {})['passes'] <= 4)

print('  -- 改表不能伤到已有数据 --')
BASE = {'items': [{'id': 'n', 'type': 'number', 'text': '年龄', 'min': 0, 'max': 130},
                  {'id': 's', 'type': 'single', 'text': '性别',
                   'options': [{'label': '男', 'value': 1}, {'label': '女', 'value': 2}]}],
        'logic': []}
for name, mut, kind in [
        ('删题', lambda d: d['items'].pop(0), 'item_removed'),
        ('改题型', lambda d: d['items'][0].__setitem__('type', 'text'), 'type_changed'),
        ('删选项', lambda d: d['items'][1]['options'].pop(), 'option_removed'),
        ('收紧 max', lambda d: d['items'][0].__setitem__('max', 60), 'max_tightened')]:
    d = json.loads(json.dumps(BASE)); mut(d)
    ch = hs.classify_crf_change(BASE, d)
    check('{} -> 破坏性, 已有填报时必须开新版'.format(name),
          ch['verdict'] == 'new_version' and any(x['kind'] == kind for x in ch['breaking']),
          [x['kind'] for x in ch['breaking']])
d = json.loads(json.dumps(BASE))
d['items'][0]['text'] = '患者年龄'
d['items'].append({'id': 'z', 'type': 'text', 'text': '新增'})
d['items'][0]['max'] = 150
check('改措辞/加题/放宽范围 -> 安全, 就地改(不把版本号变成噪音)',
      hs.classify_crf_change(BASE, d)['verdict'] == 'in_place')
d = json.loads(json.dumps(BASE)); d['items'].reverse()
check('调整顺序 -> 安全(数据按 id 存)',
      hs.classify_crf_change(BASE, d)['verdict'] == 'in_place')

print('  -- lint: show 作用在默认可见的题上等于没写 --')
trap = {'items': [{'id': 'a', 'type': 'single', 'text': 'A', 'options': YN},
                  {'id': 'b', 'type': 'text', 'text': 'B'}],
        'logic': [{'when': {'field': 'a', 'op': 'eq', 'value': 1},
                   'then': {'action': 'show', 'targets': ['b']}}]}
check('结构本身合法(不该拦住保存)', hs.validate_crf_definition(trap) == [])
check('但 lint 指出这条规则没有效果',
      any(x['kind'] == 'show_without_default_hidden' for x in hs.lint_crf_definition(trap)))
check('实测确实一直显示 —— lint 说的是对的',
      hs.eval_crf_logic(trap, {'a': 0})['visible']['b'] is True)


# ------------------------------------------------------------------ M15
section('M15 · 未经署名审核的宣教稿不能推给患者')
d, r = hs.generate_edu_draft({'disease': '演示病种', 'topic': 'medication'})
check('AI 产出恒为草稿', d['status'] == 'draft')
check('每个主题的骨架都强制含"什么情况下必须联系医生"',
      all('什么情况下必须联系医生' in hs.generate_edu_draft({'disease': 'x', 'topic': t})[0]['body']
          for t in hs.EDU_TOPICS))
res, err = hs.upsert_edu_material({'code': PFX + 'EDU', 'title': 't', 'body': 'b',
                                   'status': 'published'})
check('接口层面堵死"生成即发布"', res is None and 'transition' in (err or ''), err)

print('  -- 内容体检: 患者照做会出事的三类 --')
scan = lambda t: {x['rule'] for x in hs.scan_edu_content(t)}
check('具体剂量被标出', 'dosage' in scan('每次服用二甲双胍 500mg。'))
check('"可自行停药"被标出', 'med_change' in scan('血压平稳后可自行停药。'))
check('"不必就医"被标出', 'no_care' in scan('轻微头晕不必就医。'))
check('规则自己推荐的正确写法不误报 —— 否则人学会忽略这条规则',
      'med_change' not in scan('不要自行调整用药。'))
for neg in ('切勿自行停药', '请勿自行减量', '禁止自行换药'):
    check('「{}」不误报'.format(neg), 'med_change' not in scan(neg + '。'))
check('去掉否定词就必须命中', 'med_change' in scan('可自行停药。'))
check('"不必就医"本身带否定词, 不能被否定排除误伤', 'no_care' in scan('不必就医。'))
GOOD_EDU = ('高血压需要长期管理。用药请严格遵医嘱，不要自行调整；'
            '如有不适请及时联系随访医生。')
check('一份规范的宣教稿零命中', hs.scan_edu_content(GOOD_EDU) == [],
      [x['rule'] for x in hs.scan_edu_content(GOOD_EDU)])

RISKY = '二甲双胍每次 500mg。血糖平稳后可自行减量。轻微不适不必就医。'
res, err = hs.upsert_edu_material({'code': PFX + 'EDU', 'title': '高危稿', 'body': RISKY,
                                   'topic': 'medication', 'owner': '医生甲'})
check('入库时就标出高危', res and res['blocking_findings'] >= 3, err or res)
mid = res['id']
t, err = hs.edu_transition({'id': mid, 'action': 'publish'})
check('发布必须署名', t is None and 'operator' in (err or ''), err)
t, err = hs.edu_transition({'id': mid, 'action': 'publish', 'operator': '主任乙'})
check('有高危时直接发布被挡住', t and t.get('published') is False)
check('挡住时把高危条目原样返回给审核者看', t and len(t['blocking_findings']) >= 3)
t2, err = hs.edu_transition({'id': mid, 'action': 'publish', 'operator': '主任乙',
                             'ack_findings': True, 'note': '已逐条确认'})
check('逐条确认后才放行', t2 and t2.get('to') == 'published', err or t2)
check('记下了确认了几条', t2 and t2['acked_findings'] >= 3)


# ------------------------------------------------------------------ M16
section('M16 · 检索是个让人自拼查询的不鉴权接口, 注入防线是唯一的防护')
def rejects(node):
    try:
        hs.build_search_sql(node)
        return False
    except ValueError:
        return True

check('未知字段被拒', rejects({'field': 'p.x; DROP TABLE platform_patient--',
                              'operator': 'eq', 'value': 1}))
check('伪装成合法字段的注入被拒', rejects({'field': 'patient.age) OR 1=1--',
                                          'operator': 'eq', 'value': 1}))
check('非法运算符被拒', rejects({'field': 'patient.age', 'operator': '; DELETE--', 'value': 1}))
check('数字字段收到 SQL 串被拒', rejects({'field': 'patient.age', 'operator': 'gt',
                                         'value': '1 OR 1=1'}))
try:
    err_msg = ''
    hs.build_search_sql({'field': 'zzz', 'operator': 'eq', 'value': 1})
except ValueError as e:
    err_msg = str(e)
check('拒绝时不列出可用字段(免得帮人枚举)', 'patient.' not in err_msg, err_msg)

sql, params = hs.build_search_sql({'field': 'patient.name', 'operator': 'contains',
                                   'value': "' OR '1'='1"})
check('用户输入进的是参数不是 SQL 文本', "OR '1'='1" not in sql and "OR '1'='1" in params[0])
check('SQL 片段里只有占位符', sql.count('%s') == 1 and "'" not in sql, sql)
sql, params = hs.build_search_sql({'field': 'scale.item', 'operator': 'gte', 'value': 2,
                                   'params': {'scale_code': "X' OR 1=1--", 'item_id': "q1'--"}})
check('附加参数(量表编码)也走参数', "X' OR 1=1--" in params and "X' OR 1=1--" not in sql)
check('JSON 路径里的题目 id 同样走参数', "q1'--" in params and "q1'--" not in sql)
check('缺附加参数时报错而不是静默查全库',
      rejects({'field': 'scale.total', 'operator': 'gt', 'value': 1}))
deep = {'field': 'patient.age', 'operator': 'gt', 'value': 1}
for _ in range(8):
    deep = {'op': 'and', 'children': [deep]}
check('嵌套过深被拒', rejects(deep))
check('条件过多被拒', rejects({'op': 'or', 'children': [
    {'field': 'patient.age', 'operator': 'eq', 'value': i} for i in range(60)]}))
res, err = hs.platform_search({'conditions': {'field': 'patient.name', 'operator': 'contains',
                                              'value': "' OR '1'='1"}})
check('端到端: 注入串查不出任何人(说明被当成了字面量)', res and res['total'] == 0,
      (res or {}).get('total'))


# ------------------------------------------------------------------ M17
section('M17 · 上传文件名不落盘 + 签署钉内容哈希')
PDF = '%PDF-1.4\n知情同意书\n%%EOF'.encode('utf-8')
b64 = lambda raw: base64.b64encode(raw).decode()
r, err = hs.upload_document({'code': PFX + 'DOC', 'title': '知情同意书', 'doc_type': 'consent',
                             'filename': '知情同意书.pdf', 'content_base64': b64(PDF),
                             'uploader': '医生甲'})
check('上传成功', r and r['version'] == '1', err or r)
check('哈希是内容的 sha256', r['sha256'] == hashlib.sha256(PDF).hexdigest())
check('磁盘名由服务端生成, 不含原文件名', '知情同意书' not in r['stored_name'], r['stored_name'])
for bad_name, why in [('../../../etc/passwd.pdf', '路径穿越'),
                      ('..\\..\\windows\\x.pdf', '反斜杠穿越'),
                      ('a' * 400 + '.pdf', '超长名'),
                      ('x\x00.pdf', '空字节')]:
    rr, _ = hs.upload_document({'code': PFX + 'X', 'title': 't', 'doc_type': 'other',
                                'filename': bad_name, 'content_base64': b64(b'x' * 10)})
    check('{} 不影响磁盘落点'.format(why),
          rr and '/' not in rr['stored_name'] and '\\' not in rr['stored_name']
          and len(rr['stored_name']) < 80, (rr or {}).get('stored_name'))
check('目录里没有多出奇怪的东西',
      all('..' not in f and '/' not in f for f in os.listdir(DOC_TMP)))
for ext, should in [('pdf', True), ('docx', True), ('html', False), ('svg', False),
                    ('js', False), ('exe', False)]:
    rr, _ = hs.upload_document({'title': 't', 'doc_type': 'other', 'filename': 'f.' + ext,
                                'content_base64': b64(b'x' * 10)})
    check('.{} {}'.format(ext, '接受' if should else '拒绝(可能被当作可执行内容渲染)'),
          bool(rr) == should)

c, err = hs.sign_consent({'doc_code': PFX + 'DOC', 'patient_no': PFX + '001',
                          'signer_name': '张三'}, source_ip='10.0.0.9')
check('签署成功', c and c.get('signed'), err or c)
check('钉住的是内容哈希而不是文件 id', c['doc_sha256'] == hashlib.sha256(PDF).hexdigest())
check('返回里就带免责声明(这不是可靠电子签名)', '不是《电子签名法》' in c['disclaimer'])
cc, err = hs.sign_consent({'doc_code': PFX + 'DOC', 'patient_no': PFX + '001',
                           'signer_name': '张三'})
check('同人同版同身份重复签署要显式确认', cc and cc.get('signed') is False)

conn = hs.get_connection(); cur = conn.cursor()
cur.execute("UPDATE platform_document SET sha256=%s WHERE code=%s AND version='1'",
            ('0' * 64, PFX + 'DOC'))
cur.close(); conn.close()
q, _ = hs.query_consents(patient_no=PFX + '001')
tampered = [x for x in q['consents'] if not x['hash_matches']]
check('签署后文件被换过 -> 记录被标出', bool(tampered))
check('并说清这份签名已不能证明什么',
      tampered and '不能证明' in tampered[0]['integrity_warning'])
conn = hs.get_connection(); cur = conn.cursor()
cur.execute("UPDATE platform_document SET sha256=%s WHERE code=%s AND version='1'",
            (hashlib.sha256(PDF).hexdigest(), PFX + 'DOC'))
cur.close(); conn.close()
raw, meta, err = hs.fetch_document_bytes(PFX + 'DOC', '1')
check('取回的是原始字节', raw == PDF)
victim = os.path.join(DOC_TMP, r['stored_name'])
with open(victim, 'wb') as fh:
    fh.write(b'tampered')
raw2, _, e2 = hs.fetch_document_bytes(PFX + 'DOC', '1')
check('磁盘文件被换掉时拒绝下发', raw2 is None and e2)
check('报错说清是被替换或损坏', '替换' in (e2 or ''))


# ------------------------------------------------------------------
section('清理')
cleanup()
shutil.rmtree(DOC_TMP, ignore_errors=True)
print('  ✅ 测试数据与临时目录已清')

print('\n' + '=' * 66)
if FAIL:
    print('❌ %d 项安全不变量失败:' % len(FAIL))
    for x in FAIL:
        print('   -', x)
    sys.exit(1)
print('✅ 全部通过')
