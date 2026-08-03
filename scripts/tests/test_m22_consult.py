#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M22 大模型健康咨询 + 高风险分诊 (方案 §4.5(1) / §4.4(2))。

这是整个平台风险最高的一块 —— 输出直接给患者看, 而患者会照做。
断言集中在四条不可让步的规则上:
  1. 急症/自伤**根本不调模型**, 直接返回固定话术并落预警
  2. 患者身份不发给第三方
  3. 模型回答再过一遍内容体检, 冒出剂量数字就拦
  4. 全程留痕
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import hs, check, section, sub, finish, ensure_all_tables, db

PFX='T22'
ensure_all_tables(); hs.ensure_platform_consult_tables()
def clean():
    db(("DELETE FROM platform_consult WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_alarm WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_patient WHERE patient_no LIKE %s",(PFX+'%',)))
clean()
TRIAGE_LABELS = {k: v[1] for k, v in hs.TRIAGE_LEVELS.items()}
db(("INSERT INTO platform_patient (patient_no,name) VALUES (%s,'咨询测试患者')",(PFX+'001',)))

section('1. 分诊: 确定性, 在调模型之前')
# 这一组是全项目最该防回归的断言。原先规则写死固定短语, 中间插一个字就漏 ——
# "胸口很痛"(中文里说胸痛最自然的说法)当时判成了普通咨询。漏一句的后果是
# 有人在家里等着。所以这里列的是**真实说法**, 不是规则的镜像。
TRIAGE_CASES = [
    ('emergency', [
        '我现在胸口很痛', '胸口有点疼', '心口压得难受', '胸闷得厉害', '胸痛', '心绞痛',
        '喘不上来气', '呼吸有点困难', '上不来气', '喘不过气',
        '刚才晕倒了', '人有点意识不清', '昏过去了',
        '一直在吐血', '鼻血止不住', '牙龈出血止不住', '伤口流血停不下来', '便血',
        '剧烈头痛，从没这么痛过', '头痛得像要裂开', '头疼得受不了',
        '半边身子麻', '左边胳膊不能动', '一侧腿没力气',
        '说话有点说不清', '嘴角歪了', '突然看不见了',
        '高烧一直不退', '体温 40.2 度', '血压 210']),
    ('self_harm', [
        '我不想活了', '有时候想结束生命', '想伤害自己', '活着没意思', '活不下去了',
        '想一了百了', '有自残的念头']),
    ('deterioration', [
        '最近咳嗽越来越严重', '一直不见好', '比以前差很多', '症状加重了', '反复发作']),
    ('med_change', [
        '血压正常了能不能把药停了', '这个药能不能减量', '我该吃几片', '可以停药吗',
        '药能不能少吃点', '这药还要吃吗', '能不能不吃药了', '剂量可以自己调吗',
        '想把剂量加到多少合适', '停了这个药行不行', '药量减半可以吗', '还需要继续吃吗']),
    ('diagnosis', [
        '我这是不是糖尿病', '帮我看看是什么病', '我是不是得了高血压', '我这是什么',
        '我得了什么病']),
    ('normal', [
        '高血压平时饮食要注意什么', '这个病是怎么回事', '晚上睡不好有什么办法',
        '这个药一般是治什么的', '药有哪些常见副作用', '运动要注意什么',
        '心情不好怎么调节', '家里人要注意什么',
        # 轻症不该被误判成急症 —— 误报太多会让人学会忽略预警
        '流鼻血了', '有点头痛', '头有点疼', '偶尔牙龈出血']),
]
for want, questions in TRIAGE_CASES:
    sub('期望判为 %s' % TRIAGE_LABELS.get(want, want))
    for q in questions:
        got = hs.triage_message(q)['level']
        check('「%s」-> %s' % (q, TRIAGE_LABELS.get(got, got)), got == want, got)

sub('前两级必须中断常规回复 (方案 §4.4(2))')
for q in ['我现在胸口很痛', '我不想活了']:
    check('「%s」中断' % q, hs.triage_message(q)['interrupt'] is True)
for q in ['能不能把药停了', '最近越来越严重', '饮食要注意什么']:
    check('「%s」不中断' % q, hs.triage_message(q)['interrupt'] is False)

sub('固定话术的内容要求')
check('急症话术里给了 120', '120' in hs.TRIAGE_REPLIES['emergency'])
check('急症话术明说不能替代急诊判断', '不能替代急诊' in hs.TRIAGE_REPLIES['emergency'])
check('急症话术提醒别自行用药', '不要自行用药' in hs.TRIAGE_REPLIES['emergency'])
sh = hs.triage_message('我不想活了')['reply']
check('自伤话术先接住情绪再给求助方式', '很难受' in sh and '120' in sh, sh[:40])
check('自伤话术不说教', '不需要一个人扛着' in sh)
check('自伤话术说明已通知随访团队', '通知了' in sh)
check('用药话术明说不能给建议且解释为什么', '剂量因人而异' in hs.TRIAGE_REPLIES['med_change'])
check('用药话术警告别自行停药', '不要自行停药' in hs.TRIAGE_REPLIES['med_change'])
check('求诊断话术解释了为什么不能诊断', '猜一个反而会耽误' in hs.TRIAGE_REPLIES['diagnosis'])

section('2. 患者身份不发给第三方')
q = '我叫张三，门诊号 P20260001，手机 13800138000，身份证 110101199003077213，最近血压偏高怎么办'
sent, hits = hs._scrub_identifiers(q)
check('身份证被剔掉', '110101199003077213' not in sent, sent)
check('手机号被剔掉', '13800138000' not in sent)
check('门诊号被剔掉', 'P20260001' not in sent)
check('记下了剔掉哪几类', set(hits) >= {'身份证号','手机号','门诊号'}, hits)
check('咨询内容本身保留', '血压偏高' in sent, sent)
s2,h2 = hs._scrub_identifiers('高血压要注意什么')
check('没有身份信息时原样不动', s2=='高血压要注意什么' and not h2)

section('3. 急症走完整链路: 不调模型 + 落预警 + 留痕')
r,err = hs.consult_ask({'question':'我现在胸口压着痛，左手也发麻','patient_no':PFX+'001'})
check('返回成功', r and r['ok'], err)
check('分诊判为急症', r['triage']['level']=='emergency')
check('标了中断', r['triage']['interrupted'] is True)
check('回答来自固定话术而不是模型', r['answer_source']=='triage', r['answer_source'])
check('回答里有 120', '120' in r['answer'])
check('回答带免责声明', '不构成诊断' in r['answer'])
check('提示已通知随访团队', '通知随访团队' in (r['note'] or ''), r.get('note'))
a,_ = hs.query_platform_alarms(patient_no=PFX+'001')
check('落了一条预警', a['count']>=1, a['count'])
check('预警是 crit 级', a['alarms'][0]['severity']=='crit', a['alarms'][0]['severity'])
check('预警类型标明来自咨询', a['alarms'][0]['alarm_type'].startswith('consult_'),
      a['alarms'][0]['alarm_type'])
c,_ = hs.query_consults(patient_no=PFX+'001')
rec = c['consults'][0]
check('留痕记了原问题', '胸口' in rec['question'])
check('急症时 sent_text 为空(压根没发出去)', rec['sent_text'] is None, rec['sent_text'])
check('留痕标了中断', rec['interrupted'] is True)
check('留痕带中文分诊名', rec['triage_label']=='急症')
check('留痕说明了回答来源', rec['source_label']=='固定话术(未调模型)', rec['source_label'])

sub('自伤同样不调模型')
r,_ = hs.consult_ask({'question':'我最近总想结束生命','patient_no':PFX+'001'})
check('自伤也走固定话术', r['answer_source']=='triage' and r['triage']['level']=='self_harm')
check('也落了预警', hs.query_platform_alarms(patient_no=PFX+'001')[0]['count']>=2)

sub('用药调整/求诊断也不调模型')
r,_ = hs.consult_ask({'question':'血压正常了能不能把药停了','patient_no':PFX+'001'})
check('用药请求走固定话术(未调模型)', r.get('answer_source')=='triage', r.get('answer_source'))
check('分诊判对', r['triage']['level']=='med_change')
check('回答里明确不给建议', '不能给建议' in r['answer'])
check('这类不落预警(不是急症)',
      not any(x['alarm_type']=='consult_med_change'
              for x in hs.query_platform_alarms(patient_no=PFX+'001')[0]['alarms']))
r,_ = hs.consult_ask({'question':'我这是不是冠心病','patient_no':PFX+'001'})
check('求诊断走固定话术(未调模型)', r.get('answer_source')=='triage', r.get('answer_source'))
check('回答里明确不做诊断', '不能做诊断' in r['answer'])

section('4. 模型不可用时优雅降级')
had = os.environ.pop('DEEPSEEK_API_KEY', None)
r,err = hs.consult_ask({'question':'高血压饮食要注意什么','patient_no':PFX+'001'})
check('没 key 时不抛异常', r is not None and not err, err)
check('明确告知不可用', r.get('ok') is False and r.get('error'), r.get('error'))
check('报错指向 wx.env 而不是代码', 'wx.env' in (r.get('error') or ''), r.get('error'))
check('给了兜底话术', '120' in (r.get('fallback') or ''), r.get('fallback'))
check('失败也留了痕', hs.query_consults(patient_no=PFX+'001')[0]['consults'][0]['error'])
if had: os.environ['DEEPSEEK_API_KEY'] = had

section('5. 模型回答要再过一遍内容体检')
# 直接验体检那一层: 模型若答出剂量/停药, 必须被 block
for bad in ['建议每次服用二甲双胍 500mg', '血压稳定后可自行停药', '轻微不适不必就医']:
    f = hs.scan_edu_content(bad)
    check('「%s」会被拦'%bad[:14], any(x['level']=='block' for x in f), [x['rule'] for x in f])
good = '高血压需要长期管理，请遵医嘱用药，不要自行调整。如出现头晕请及时就医。'
check('规范回答不会被误拦', not any(x['level']=='block' for x in hs.scan_edu_content(good)))

section('6. 留痕与复核 (方案 §4.5(1) 要求全程可审核)')
c,_ = hs.query_consults(patient_no=PFX+'001')
check('留痕查得到', c['count']>=5, c['count'])
check('按分诊等级分类统计', c['by_level'].get('emergency',0)>=1, c['by_level'])
check('给出中断总数', c['interrupted_total']>=2, c['interrupted_total'])
check('告诉前端模型配没配', 'llm_configured' in c)
check('未配热线时给出说明', c['crisis_hotline'] or '打到空号' in (c['hotline_note'] or ''),
      c.get('hotline_note'))
c2,_ = hs.query_consults(level='emergency')
check('按等级筛', all(x['triage_level']=='emergency' for x in c2['consults']))
cid = c['consults'][0]['id']
check('复核必须署名', hs.consult_review({'id':cid})[0] is None)
r,err = hs.consult_review({'id':cid,'operator':'主任乙','note':'话术恰当, 已电话回访'})
check('署名后可复核', r and r['ok'], err)
c3,_ = hs.query_consults(patient_no=PFX+'001', unreviewed=True)
check('已复核的不再出现在待复核里', all(x['id']!=cid for x in c3['consults']))
check('不存在的记录被拒', hs.consult_review({'id':999999,'operator':'x'})[0] is None)

section('7. 参数校验')
check('空问题被拒', hs.consult_ask({'question':''})[0] is None)
check('超长问题被拒', hs.consult_ask({'question':'啊'*900})[0] is None)

section('8. 清理')
clean()
print('  ✅ 测试数据已清')
finish()
