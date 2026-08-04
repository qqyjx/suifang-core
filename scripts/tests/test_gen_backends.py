#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成类接口的后端层 (M12 量表 / M14 CRF / M15 宣教 共用)。

这个套件守两件事:

1. **"有 key" 不等于 "自动拿去用"。** 平台为健康咨询配了 DeepSeek key,
   技术上完全可以复用它生成量表/CRF/宣教稿。但把内容发给外部大模型是数据治理决策,
   为咨询开了口子不等于为建表也开了 —— 必须显式选后端。

2. **模型产出要么真的用上, 要么明说没用上。** 这里原先有个很难看出来的坏:
   CRF 和宣教的"Claude 后端"会真的发一次请求, 然后把结果扔掉, 却加一条
   "大模型产出已作为参考并入草稿" 的说明。花了钱、告诉人用上了、实际一个字没进去。

大模型调用一律打桩, 不烧真 key(末尾那节除外, 要显式给 RUN_LIVE_LLM=1 才跑)。
不需要数据库:
    HARNESS_NO_DB=1 python3 scripts/tests/test_gen_backends.py
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import *          # noqa: F401,F403
from _harness import hs, check, section, sub, finish

for k in ('SCALE_LLM_PROVIDER', 'DEEPSEEK_API_KEY', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN'):
    os.environ.pop(k, None)


class Stub(object):
    """把 _deepseek_json 换成固定返回值, 用完还原。记录被调了几次。"""

    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.calls = payload, error, []

    def __enter__(self):
        self._orig = hs._deepseek_json

        def fake(system, user, schema, what, max_tokens=8000):
            self.calls.append({'system': system, 'user': user, 'what': what})
            if self.error:
                return None, None, self.error
            return self.payload, {'backend': 'deepseek', 'model': 'stub'}, None
        hs._deepseek_json = fake
        os.environ['DEEPSEEK_API_KEY'] = 'sk-stub-not-a-real-key-0000000000'
        return self

    def __exit__(self, *a):
        hs._deepseek_json = self._orig
        os.environ.pop('DEEPSEEK_API_KEY', None)


# ---------------------------------------------------------------- 后端选择

section('G1 后端选择: 有 key 不等于自动启用')
st = hs.gen_backend_status()
check('没配任何东西时默认走本地模板', st['default'] == 'template' and
      hs.resolve_gen_backend({}) == 'template')
check('本地模板被标成不出网', [b for b in st['backends'] if b['id'] == 'template'][0]
      ['sends_data_out'] is False)
check('两个大模型后端都被标成出网 —— 界面上要让人看见这件事',
      all(b['sends_data_out'] for b in st['backends'] if b['id'] in ('claude', 'deepseek')))
check('没装/没配时如实说明缺什么, 不是笼统的"不可用"',
      all(b['note'] for b in st['backends'] if not b['ready']),
      [b['note'] for b in st['backends'] if not b['ready']])

os.environ['DEEPSEEK_API_KEY'] = 'sk-stub-not-a-real-key-0000000000'
st = hs.gen_backend_status()
ds = [b for b in st['backends'] if b['id'] == 'deepseek'][0]
check('配了 key 之后 deepseek 报 ready', ds['ready'] is True)
check('**但默认后端仍然是模板** —— 咨询用的 key 不等于建表也能用, '
      '把内容发给外部大模型是数据治理决策, 不由代码替人决定',
      hs.resolve_gen_backend({}) == 'template')
check('说明里点明了"默认不启用"这件事, 不让人以为配了就生效',
      '默认不启用' in ds['note'], ds['note'][:60])
check('显式传 backend 才用', hs.resolve_gen_backend({'backend': 'deepseek'}) == 'deepseek')
os.environ['SCALE_LLM_PROVIDER'] = 'deepseek'
check('或者显式设环境变量', hs.resolve_gen_backend({}) == 'deepseek')
os.environ.pop('SCALE_LLM_PROVIDER')
check('乱传后端名不认, 回落模板', hs.resolve_gen_backend({'backend': '../etc'}) == 'template')
os.environ.pop('DEEPSEEK_API_KEY')

section('G2 结构校验: DeepSeek 那条路服务端不强制 schema, 必须自己验')
S = hs.CRF_GEN_SCHEMA
check('缺顶层字段能查出来', hs._json_shape_errors({'title': 'x'}, S))
check('空数组算错 —— 不然表现是"生成出一份空表单"而不是报错',
      hs._json_shape_errors({'title': 'x', 'sections': []}, S))
check('嵌套里缺字段也能查出来, 且路径指得准',
      '$.sections[0].items[0] 缺字段 type' in
      hs._json_shape_errors({'title': 'x', 'sections': [
          {'name': 'a', 'items': [{'id': 'i', 'text': 't'}]}]}, S))
check('类型不对能查出来', hs._json_shape_errors({'title': 123, 'sections': [
    {'name': 'a', 'items': [{'id': 'i', 'text': 't', 'type': 'text'}]}]}, S))
check('合法结构不报错', not hs._json_shape_errors({'title': 'x', 'sections': [
    {'name': 'a', 'items': [{'id': 'i', 'text': 't', 'type': 'text'}]}]}, S))

# ---------------------------------------------------------------- CRF

section('G3 CRF: 模型产出必须真的进草稿')
GOOD_CRF = {'title': '哮喘随诊 CRF', 'sections': [
    {'name': '症状控制', 'items': [
        {'id': 'night_wake', 'text': '过去 4 周夜间因喘憋醒来的次数', 'type': 'number'},
        {'id': 'rescue_use', 'text': '过去 4 周使用急救吸入剂的频次', 'type': 'select',
         'options': [{'label': '未使用', 'value': 0}, {'label': '每周<2次', 'value': 1},
                     {'label': '每周≥2次', 'value': 2}]},
        {'id': 'act_score', 'text': '哮喘控制测试(ACT)得分', 'type': 'number'},
    ]},
    {'name': '触发因素', 'items': [
        {'id': 'triggers', 'text': '本次发作的可能诱因', 'type': 'multi',
         'options': [{'label': '呼吸道感染', 'value': 'inf'}, {'label': '冷空气', 'value': 'cold'},
                     {'label': '运动', 'value': 'exercise'}]},
    ]},
]}

with Stub(GOOD_CRF) as stub:
    draft, report = hs.generate_crf_draft(
        {'disease': '哮喘', 'visit_type': '随诊', 'backend': 'deepseek'})
ids = [it['id'] for _s, it in hs._crf_items(draft['definition'])]
check('模型出的题**确实在**最终 definition 里 —— 这是原先坏掉的那件事',
      'night_wake' in ids and 'act_score' in ids and 'triggers' in ids, ids)
check('通用骨架仍在(基本信息那几题没被顶掉)', 'patient_no' in ids and 'visit_date' in ids)
check('选择题的 options 原样带过来',
      [it for _s, it in hs._crf_items(draft['definition'])
       if it['id'] == 'rescue_use'][0]['options'][2]['value'] == 2)
check('生成的定义整体合法', not hs.validate_crf_definition(draft['definition']),
      hs.validate_crf_definition(draft['definition']))
steps = [n['step'] for n in report['notes']]
check('说明里标的是"已并入草稿", 而且这次是真的', 'llm_items' in steps, steps)
check('报告里的题数把模型那几道算进去了',
      report['item_count'] == len(ids) and report['item_count'] > 8, report['item_count'])

sub('不合规的题当场丢掉, 不塞给使用者去改')
BAD_CRF = {'title': 'x', 'sections': [{'name': '乱来', 'items': [
    {'id': 'ok_one', 'text': '正常的一题', 'type': 'text'},
    {'id': 'bad_type', 'text': '题型不在白名单', 'type': 'signature'},
    {'id': 'no_opts', 'text': '单选却没给选项', 'type': 'single'},
    {'id': 'one_opt', 'text': '单选只给一个选项', 'type': 'single',
     'options': [{'label': '是', 'value': 1}]},
    {'id': 'ok_one', 'text': 'id 撞了', 'type': 'text'},
    {'id': 'patient_no', 'text': '和骨架撞 id', 'type': 'text'},
]}]}
with Stub(BAD_CRF):
    draft2, report2 = hs.generate_crf_draft(
        {'disease': '测试', 'visit_type': '随诊', 'backend': 'deepseek'})
ids2 = [it['id'] for _s, it in hs._crf_items(draft2['definition'])]
check('题型不在白名单的被丢', 'bad_type' not in ids2)
check('单选没给够选项的被丢 —— 留着的话表单渲染出来是个点不了的空选项组',
      'no_opts' not in ids2 and 'one_opt' not in ids2)
check('id 重复的只留一份', ids2.count('ok_one') == 1)
check('和通用骨架撞 id 的被丢 —— 同一个 id 两份答案会互相覆盖且界面看不出来',
      ids2.count('patient_no') == 1)
check('正常那题留下了', 'ok_one' in ids2)
steps2 = [n['step'] for n in report2['notes']]
check('丢了什么如实记账, 不静默', 'llm_items_dropped' in steps2 or 'llm_id_clash' in steps2, steps2)

sub('后端不可用时带原因回落, 不报错也不静默')
with Stub(error='DeepSeek 返回 HTTP 401: invalid key'):
    draft3, report3 = hs.generate_crf_draft({'disease': '测试', 'backend': 'deepseek'})
check('回落到模板仍产出可用草稿', draft3 and not hs.validate_crf_definition(draft3['definition']))
fb = [n for n in report3['notes'] if n['step'] == 'backend_fallback']
check('回落原因写在报告里(含原始错误)', fb and '401' in fb[0]['detail'], fb)
check('**没有**声称大模型产出已并入',
      not any(n['step'] in ('llm_items',) for n in report3['notes']))

# ---------------------------------------------------------------- 宣教

section('G4 宣教: 正文要么真写了, 要么明说是占位')
GOOD_EDU = {'title': '哮喘用药指导', 'sections': [
    {'heading': '为什么要按时用药', 'body': '控制类药物需要长期规律使用才能让气道炎症稳定下来。'
                                            '感觉症状好转也不代表可以停, 请遵医嘱。'},
    {'heading': '漏服了怎么办', 'body': '想起来时若距离下次用药还早, 可按医生交代的方式补上; '
                                        '若已接近下次时间, 不要叠加。具体怎么补请联系随访医生。'},
    {'heading': '常见的不舒服有哪些', 'body': '部分人会有口干、声音嘶哑或心慌。多数较轻, '
                                              '若持续或加重请告知医生。'},
    {'heading': '什么情况下必须联系医生', 'body': '出现以下情况请立即联系随访医生或就近就医: '
                                                  '喘憋在休息状态下仍不缓解、说话成句困难、'
                                                  '口唇发紫、急救吸入剂用后无改善。'},
]}
with Stub(GOOD_EDU):
    edu, edu_rep = hs.generate_edu_draft(
        {'disease': '哮喘', 'topic': 'medication', 'backend': 'deepseek'})
check('正文是模型写的, 不再是一片【待填写】',
      '【待填写】' not in edu['body'] and '长期规律使用' in edu['body'])
check('四节都在且小标题没被改', all(('## ' + s) in edu['body']
      for s in hs.EDU_TEMPLATE_SECTIONS['medication']))
check('免责声明仍然缀在末尾', hs.EDU_DISCLAIMER in edu['body'])
check('产出恒为草稿 —— 宣教稿是直接推给患者的, 必须过审核',
      edu['status'] == 'draft' and edu_rep['needs_review'] is True)
steps = [n['step'] for n in edu_rep['notes']]
check('说明里点明正文是模型写的(不是模板占位)', 'llm_written' in steps, steps)
check('仍然提醒必须人工审核', 'must_review' in steps)

sub('内容体检是生成之后跑的, 模型写的一样要过')
DANGEROUS_EDU = {'title': 'x', 'sections': [
    {'heading': '为什么要按时用药', 'body': '每天吃两片, 每片 5mg, 症状好了可以自行停药。'},
    {'heading': '漏服了怎么办', 'body': '下次吃双倍剂量补回来。'},
    {'heading': '常见的不舒服有哪些', 'body': '一般没什么问题。'},
    {'heading': '什么情况下必须联系医生', 'body': '出现严重不适时联系医生。'},
]}
with Stub(DANGEROUS_EDU):
    edu2, rep2 = hs.generate_edu_draft(
        {'disease': '测试', 'topic': 'medication', 'backend': 'deepseek'})
check('模型写的具体剂量被内容体检抓住', rep2['blocking_findings'] > 0,
      [f.get('rule') for f in rep2['content_findings']])
check('"可自行停药"被抓住',
      any('停药' in json.dumps(f, ensure_ascii=False) for f in rep2['content_findings']))
check('并且在 notes 里明说这稿不能就这么提交审核',
      any(n['step'] == 'llm_blocked_findings' for n in rep2['notes']),
      [n['step'] for n in rep2['notes']])

sub('模型漏写的节留占位, 不静默跳过')
PARTIAL_EDU = {'title': 'x', 'sections': [
    {'heading': '为什么要按时用药', 'body': '正常内容。'}]}
with Stub(PARTIAL_EDU):
    edu3, rep3 = hs.generate_edu_draft(
        {'disease': '测试', 'topic': 'medication', 'backend': 'deepseek'})
check('漏写的节留【待填写】而不是消失', edu3['body'].count('【待填写】') == 3, edu3['body'][:100])
check('"什么情况下必须联系医生"这一节任何情况下都不能少 —— 它是这份材料唯一的安全出口',
      '什么情况下必须联系医生' in edu3['body'])
check('漏了哪几节如实记账',
      any(n['step'] == 'llm_sections_missing' for n in rep3['notes']),
      [n['step'] for n in rep3['notes']])

sub('视频脚本不走大模型')
with Stub(GOOD_EDU) as stub4:
    edu4, rep4 = hs.generate_edu_draft(
        {'disease': '测试', 'topic': 'medication', 'format': 'video_script',
         'backend': 'deepseek'})
check('视频脚本压根没发请求出去', not stub4.calls)
check('并说明了为什么跳过',
      any(n['step'] == 'backend_skipped' for n in rep4['notes']))

# ---------------------------------------------------------------- 量表

section('G5 量表: 换后端不能绕开"不出划界值"那道闸')
GEN_SCALE = {'name': '测试量表', 'instruction': '请按最近两周的情况作答', 'items': [
    {'id': 'q1', 'text': '感到紧张不安', 'dimension': '焦虑', 'reverse': False},
    {'id': 'q2', 'text': '难以放松', 'dimension': '焦虑', 'reverse': False},
    {'id': 'q3', 'text': '能安然入睡', 'dimension': '睡眠', 'reverse': True},
]}
with Stub(GEN_SCALE):
    sc, sc_rep = hs.generate_scale_draft(
        {'goal': '焦虑筛查', 'dimensions': ['焦虑', '睡眠'], 'backend': 'deepseek'})
check('模型的题进了草稿', len(sc['definition']['items']) == 3,
      len(sc['definition']['items']))
check('**没有划界值分级** —— 编出来的阈值会让每份报告都给出看着正常的错误结论',
      sc['definition']['scoring']['levels'] == [])
check('reverse 标记保住了',
      [i for i in sc['definition']['items'] if i['id'] == 'q3'][0].get('reverse') is True)
check('带着"未经信效度验证"的声明', '未经信效度验证' in sc['definition']['disclaimer'])

sub('模型硬塞划界值也要被剥掉')
WITH_LEVELS = dict(GEN_SCALE)
with Stub(WITH_LEVELS) as s5:
    sc2, rep5 = hs.generate_scale_draft({'goal': 'x', 'backend': 'deepseek'})
check('送给模型的 schema 里根本没有 levels 的位置 —— 比事后删更干净',
      'levels' not in json.dumps(hs.SCALE_GEN_SCHEMA))
check('即便如此仍然兜底清一遍', sc2['definition']['scoring']['levels'] == [])

# ---------------------------------------------------------------- 真调用

section('G6 真实调用 (要显式 RUN_LIVE_LLM=1 且有 key 才跑)')
if os.environ.get('RUN_LIVE_LLM') == '1' and os.environ.get('REAL_DEEPSEEK_KEY'):
    os.environ['DEEPSEEK_API_KEY'] = os.environ['REAL_DEEPSEEK_KEY']
    draft, report = hs.generate_crf_draft(
        {'disease': '2型糖尿病', 'visit_type': '3个月随访', 'backend': 'deepseek'})
    ids = [it['id'] for _s, it in hs._crf_items(draft['definition'])]
    check('真调 DeepSeek 能生成 CRF', len(ids) > 8, len(ids))
    check('产出结构合法', not hs.validate_crf_definition(draft['definition']),
          hs.validate_crf_definition(draft['definition']))
    print('   生成的题:', [it['text'] for _s, it in hs._crf_items(draft['definition'])][-6:])
    os.environ.pop('DEEPSEEK_API_KEY')
else:
    print('  ⏭  跳过真实调用 (要跑: RUN_LIVE_LLM=1 REAL_DEEPSEEK_KEY=xxx ...)')

finish()
