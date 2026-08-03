#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M20 访视超窗管理 + 消息推送 (方案 §4.5)。

两组最要紧的断言:
  1. **只有已发布的宣教材料能推给患者。** M15 那套"AI 产出一律草稿, 必须署名审核
     发布后才能被随访计划调用"的闸门, 落点就在这个接口。不校验它就纯粹是装饰。
  2. **推送状态永远不会是"已发送"。** 通道没接, 记录入队了不等于患者收到了。
     显示"已推送 200 条"会让人以为患者收到了, 那比不做这个功能更糟。
"""
import os, sys, json, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import hs, check, section, sub, finish, ensure_all_tables, db

PFX='T20'
ensure_all_tables(); hs.ensure_platform_push_tables()
def clean():
    db(("DELETE FROM platform_push WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_visit_followup WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_visit WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_flow_instance WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_flow WHERE code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_enrollment WHERE cohort_code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_edu_log WHERE material_id IN (SELECT id FROM platform_edu_material WHERE code LIKE %s)",(PFX+'%',)),
       ("DELETE FROM platform_edu_material WHERE code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_patient WHERE patient_no LIKE %s",(PFX+'%',)))
clean()
today = datetime.date.today()
for i in range(1,6):
    db(("INSERT INTO platform_patient (patient_no,name) VALUES (%s,%s)",('%s%03d'%(PFX,i),'跟进%d'%i)))
# 一条流程, 患者锚点分别拉开, 造出不同超窗档
FLOW={'levels':['阶段','访视'],'anchor':'enroll',
      'nodes':[{'id':'s1','name':'随访','children':[
          {'id':'v1','name':'第1次','offset_days':7,'window':[-2,3],
           'items':[{'type':'edu','ref':PFX+'E'},{'type':'interview'}]},
          {'id':'v2','name':'第2次','offset_days':30,'window':[-5,7],'items':[]}]}],
      'offschedule':[{'id':'ae','name':'不良事件','items':[]}]}
hs.upsert_flow({'code':PFX+'F','name':'推送测试流程','definition':FLOW,'status':'active'})
for i,back in [(1,60),(2,20),(3,12),(4,3),(5,0)]:
    hs.flow_instantiate({'flow_code':PFX+'F','patient_no':'%s%03d'%(PFX,i),
                         'anchor_date':(today-datetime.timedelta(days=back)).strftime('%Y-%m-%d')})
print('  测试数据: 5 患者 / 1 流程(每人 2 次访视, 锚点错开)')

section('1. 超窗统计 (§4.5(4))')
s,err = hs.visit_overdue_summary()
check('统计跑得出', s and s['ok'], err)
check('给出超窗访视数', s['overdue_visits']>0, s['overdue_visits'])
check('给出超窗患者数(去重)', s['overdue_patients']>0, s['overdue_patients'])
check('患者数不多于访视数', s['overdue_patients']<=s['overdue_visits'])
check('给出未来到期数', 'upcoming_visits' in s)
print('     超窗分档:', s['overdue_bands'])
check('超窗按天数分档', len(s['overdue_bands'])>=2, s['overdue_bands'])
check('说清了为什么要分档', '还救得回来' in s['note'] or '混在一起' in s['note'], s['note'][:60])
check('分档名可读', all(b['band'] in ('1-7天','8-30天','30天以上') for b in s['overdue_bands']))

section('2. 批量跟进 (§4.5(4))')
v,_ = hs.query_visits(flow_code=PFX+'F', overdue_only=True)
ids = [x['id'] for x in v['visits']]
check('取到超窗访视', len(ids)>=3, len(ids))
sub('逐条给结果, 不只说"成功了"')
r,err = hs.visit_batch_followup({'visit_ids':ids[:2]+[999999,'abc'],'action':'call',
                                 'result':'已电话联系','operator':'护士甲'})
check('批量跟进跑通', r and r['ok'], err)
check('总数/成功/失败都给了', r['total']==4 and r['succeeded']==2 and r['failed']==2, r)
check('逐条列出结果', len(r['results'])==4)
check('不存在的那条指明了原因', any(not x['ok'] and '不存在' in x['error'] for x in r['results']))
check('非整数那条也指明了', any(not x['ok'] and '整数' in x['error'] for x in r['results']))
check('成功的带上了门诊号', all(x.get('patient_no') for x in r['results'] if x['ok']))
f,_ = hs.query_followups(batch_id=r['batch_id'])
check('跟进记录落库', f['count']==2, f['count'])
check('记录带中文动作名', f['followups'][0]['action_label']=='电话联系')

sub('改约: 窗口跟着平移, 保持原本宽窄')
one = [x for x in v['visits'] if x['name']=='第1次'][0]
old_span = (datetime.datetime.strptime(one['window_end'],'%Y-%m-%d').date()
            - datetime.datetime.strptime(one['window_start'],'%Y-%m-%d').date()).days
newd = (today+datetime.timedelta(days=10)).strftime('%Y-%m-%d')
r,err = hs.visit_batch_followup({'visit_ids':[one['id']],'action':'reschedule','new_date':newd})
check('改约成功', r and r['succeeded']==1, err)
v2,_ = hs.query_visits(patient_no=one['patient_no'])
moved = [x for x in v2['visits'] if x['id']==one['id']][0]
check('计划日挪到了新日期', moved['planned_date']==newd, moved['planned_date'])
new_span = (datetime.datetime.strptime(moved['window_end'],'%Y-%m-%d').date()
            - datetime.datetime.strptime(moved['window_start'],'%Y-%m-%d').date()).days
check('窗口宽窄没变(不是塞成当天)', new_span==old_span, (old_span,new_span))
check('状态回到未到期', moved['status']=='pending', moved['status'])
check('改约不给 new_date 被拒',
      hs.visit_batch_followup({'visit_ids':[one['id']],'action':'reschedule'})[0] is None)

sub('失访是重大判定, 不能批量一点了事')
r,err = hs.visit_batch_followup({'visit_ids':ids[2:3],'action':'lost'})
check('标记失访不写依据被拒', r is None and err, err)
check('报错说清为什么', '移出分析人群' in (err or ''), err)
r,err = hs.visit_batch_followup({'visit_ids':ids[2:3],'action':'lost',
                                 'result':'连续 3 次电话无人接听, 短信未回',
                                 'operator':'护士甲'})
check('写了依据才执行', r and r['succeeded']==1, err)
check('说明了失访会连带终止流程', '流程一并终止' in (r.get('note') or ''), r.get('note'))
lost_pno = r['results'][0]['patient_no']
v3,_ = hs.query_visits(patient_no=lost_pno)
check('该患者剩余访视全部取消',
      all(x['status']=='cancelled' for x in v3['visits'] if x['kind']=='scheduled'),
      [x['status'] for x in v3['visits']])
check('豁免不写原因被拒', hs.visit_batch_followup({'visit_ids':ids[3:4],'action':'waive'})[0] is None)
check('非法动作被拒', hs.visit_batch_followup({'visit_ids':ids[:1],'action':'bogus'})[0] is None)
check('空数组被拒', hs.visit_batch_followup({'visit_ids':[],'action':'call'})[0] is None)
check('超过 500 条被拒', hs.visit_batch_followup({'visit_ids':list(range(600)),'action':'call'})[0] is None)

section('3. 只有已发布的宣教材料能推 (M15 闸门的落点)')
hs.upsert_edu_material({'code':PFX+'E','title':'演示患教','body':'规律作息，遵医嘱用药。',
                        'topic':'disease','owner':'医生甲'})
r,err = hs.push_create({'content_type':'edu','target_kind':'patient','patient_no':PFX+'001',
                        'ref_code':PFX+'E','dry_run':False})
check('草稿状态的材料**不能推**', r is None and err, err)
check('报错说清了当前状态', '草稿' in (err or ''), err)
check('报错解释了为什么严', '当医嘱照做' in (err or ''), err)
check('并点名了未审核内容的风险', '可自行停药' in (err or ''), err)
q,_ = hs.query_edu_materials(material_id=None)
mid = [m['id'] for m in hs.query_edu_materials()[0]['materials'] if m['code']==PFX+'E'][0]
hs.edu_transition({'id':mid,'action':'publish','operator':'主任乙'})
r,err = hs.push_create({'content_type':'edu','target_kind':'patient','patient_no':PFX+'001',
                        'ref_code':PFX+'E','dry_run':False,'operator':'护士甲'})
check('发布之后才能推', r and r['queued']==1, err)
check('带上了材料版本', r['ref_version']=='1', r.get('ref_version'))
check('不存在的材料被拒', hs.push_create({'content_type':'edu','target_kind':'patient',
                                          'patient_no':PFX+'001','ref_code':'NOPE'})[0] is None)
check('推患教不给 ref_code 被拒',
      hs.push_create({'content_type':'edu','target_kind':'patient','patient_no':PFX+'001'})[0] is None)

section('4. 推送状态永远不是"已发送"')
p,_ = hs.query_pushes(patient_no=PFX+'001')
check('推送记录查得到', p['count']>=1)
check('状态是待发送而不是已发送', all(x['status']=='queued' for x in p['pushes']),
      [x['status'] for x in p['pushes']])
check('中文标签也是"待发送"', p['pushes'][0]['status_label']=='待发送')
check('返回里明确警告通道没接', '没有真的发出去' in p['channel_warning'], p['channel_warning'][:50])
check('警告解释了后果', '比不做这个功能更糟' in hs.PUSH_NOT_WIRED or
      '没有真的发出去' in hs.PUSH_NOT_WIRED)
check('建推送时也带这条警告', r['channel_warning'] and '没有真的发出去' in r['channel_warning'])

section('5. 群发: dry_run 默认开')
db(("INSERT INTO platform_enrollment (cohort_code,patient_no,group_code,status) VALUES (%s,%s,'G1','enrolled')",
    (PFX+'H', PFX+'001')),
   ("INSERT INTO platform_enrollment (cohort_code,patient_no,group_code,status) VALUES (%s,%s,'G1','enrolled')",
    (PFX+'H', PFX+'002')),
   ("INSERT INTO platform_enrollment (cohort_code,patient_no,group_code,status) VALUES (%s,%s,'G2','enrolled')",
    (PFX+'H', PFX+'003')))
r,err = hs.push_create({'content_type':'notice','target_kind':'group','cohort_code':PFX+'H',
                        'group_code':'G1','title':'复诊提醒','body':'请按时复诊'})
check('不给 dry_run 时默认试算', r and r['dry_run'] is True, err)
check('试算告诉你会发给几个人', r['would_send']==2, r['would_send'])
check('试算给出对象样例', len(r['sample'])>0)
check('试算提示怎么真正执行', 'dry_run=false' in r['hint'])
check('试算时也带通道警告', '没有真的发出去' in r['channel_warning'])
p0,_ = hs.query_pushes(content_type='notice')
r,err = hs.push_create({'content_type':'notice','target_kind':'group','cohort_code':PFX+'H',
                        'group_code':'G1','title':'复诊提醒','body':'请按时复诊','dry_run':False})
check('执行后入队 2 条', r and r['queued']==2, err)
r,_ = hs.push_create({'content_type':'notice','target_kind':'cohort','cohort_code':PFX+'H',
                      'title':'x','body':'y'})
check('按方案推 = 3 人', r['would_send']==3, r['would_send'])
r,_ = hs.push_create({'content_type':'notice','target_kind':'all','title':'x','body':'y'})
check('全量推能算出总人数', r['would_send']>=5, r['would_send'])
check('分组推不给 group_code 被拒',
      hs.push_create({'content_type':'notice','target_kind':'group','cohort_code':PFX+'H','title':'x'})[0] is None)
check('未知渠道被拒', hs.push_create({'channel':'telepathy','content_type':'notice',
                                      'target_kind':'patient','patient_no':PFX+'001','title':'x'})[0] is None)
check('标题正文都不给被拒',
      hs.push_create({'content_type':'notice','target_kind':'patient','patient_no':PFX+'001'})[0] is None)

section('6. 从访视自动推患教 (§4.5(2) 自动推送)')
v,_ = hs.query_visits(patient_no=PFX+'004')
withedu = [x for x in v['visits'] if any(i.get('type')=='edu' for i in (x['items'] or []))]
check('访视上配了患教内容', len(withedu)==1, len(withedu))
r,err = hs.push_from_visit({'visit_id':withedu[0]['id'],'operator':'护士甲'})
check('自动推送入队', r and r['queued']==1, err or r)
check('没有被拦下的', not r['blocked'], r['blocked'])
p,_ = hs.query_pushes(patient_no=PFX+'004')
check('推送标了 mode=auto', p['pushes'][0]['mode_label']=='自动', p['pushes'][0]['mode_label'])
check('记了是哪次访视触发的', p['pushes'][0]['visit_id']==withedu[0]['id'])
noedu = [x for x in v['visits'] if not any(i.get('type')=='edu' for i in (x['items'] or []))]
r,_ = hs.push_from_visit({'visit_id':noedu[0]['id']})
check('没配患教的访视给出可读说明', r['queued']==0 and '没有配患教内容' in r['note'], r.get('note'))
sub('材料被撤回发布后, 自动推送必须挡住')
hs.edu_transition({'id':mid,'action':'archive','operator':'主任乙'})
r,err = hs.push_from_visit({'visit_id':withedu[0]['id']})
check('归档后的材料推不出去', r and r['blocked'], (r or {}).get('blocked'))
check('并说明多半是没审核发布', '审核发布' in (r.get('note') or ''), r.get('note'))

section('7. 清理')
clean()
print('  ✅ 测试数据已清')
finish()
