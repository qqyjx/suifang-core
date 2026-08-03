#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M19 个性化随访流程 (方案 §3.3)。

最要紧的一组是**随访完成率的分母**。两种错法都让这个数失去意义:
  · 把流程外阶段(不良事件)算进分母 -> 从未发生的事件被当成"未完成"
  · 把尚未到期的访视算进分母 -> 三个月后才做的访视现在就算"没做"
而管理层恰恰只看这个数。
"""
import os, sys, json, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import hs, check, section, sub, finish, ensure_all_tables, db

PFX='T19'
ensure_all_tables()
def clean():
    db(("DELETE FROM platform_visit WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_flow_instance WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_flow WHERE code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_patient WHERE patient_no LIKE %s",(PFX+'%',)))
clean()
for i in (1,2,3):
    db(("INSERT INTO platform_patient (patient_no,name) VALUES (%s,%s)",
        ('%s%03d'%(PFX,i), '流程测试%d'%i)))
today = datetime.date.today()

section('1. 流程定义校验')
FLOW = {
  'levels': ['阶段','访视','项目'],
  'anchor': 'enroll',
  'nodes': [
    {'id':'s1','name':'术后早期','children':[
      {'id':'v7','name':'术后7天','offset_days':7,'window':[-2,3],
       'items':[{'type':'scale','ref':'PHQ-9'},{'type':'lab','name':'血常规'}]},
      {'id':'v30','name':'术后1月','offset_days':30,'window':[-5,7],
       'items':[{'type':'crf','ref':'FU-1'},{'type':'interview'}]}]},
    {'id':'s2','name':'术后中期','children':[
      {'id':'v90','name':'术后3月','offset_days':90,'window':[-7,14],
       'items':[{'type':'scale','ref':'GAD-7'}]},
      {'id':'v180','name':'术后6月','offset_days':180,'window':[-10,20],'items':[]}]}],
  'offschedule': [
    {'id':'ae','name':'不良事件','items':[{'type':'crf','ref':'AE-1'}]},
    {'id':'comp','name':'并发症','items':[]}]}
check('定义合法', hs.validate_flow_definition(FLOW)==[], hs.validate_flow_definition(FLOW))
bad = hs.validate_flow_definition
check('层级名少于 2 个被拦', any('levels' in e for e in bad({'levels':['x'],'nodes':[{'id':'a','name':'a','offset_days':1}]})))
check('叶子节点没有时间落点被拦',
      any('offset_days' in e for e in bad({'levels':['a','b'],'nodes':[{'id':'x','name':'x'}]})))
check('窗口下限给正数被拦',
      any('下限' in e for e in bad({'levels':['a','b'],'nodes':[{'id':'x','name':'x','offset_days':1,'window':[2,3]}]})))
check('节点 id 重复被拦',
      any('重复' in e for e in bad({'levels':['a','b'],'nodes':[
          {'id':'x','name':'x','offset_days':1},{'id':'x','name':'y','offset_days':2}]})))
check('量表项没给 ref 被拦',
      any('ref' in e for e in bad({'levels':['a','b'],'nodes':[
          {'id':'x','name':'x','offset_days':1,'items':[{'type':'scale'}]}]})))
check('树比层级名深被拦(每一级都要有名字)',
      any('层级名' in e for e in bad({'levels':['一级'],'nodes':[
          {'id':'a','name':'a','children':[{'id':'b','name':'b','offset_days':1}]}]})))
sub('流程外阶段不该有时间落点')
e = bad(dict(FLOW, offschedule=[{'id':'ae','name':'不良事件','offset_days':10}]))
check('给了 offset 的流程外阶段被拦', any('不该有 offset_days' in x for x in e), e)
check('并解释了为什么', any('其实是计划内访视' in x for x in e))

sub('lint 建议')
adv = hs.lint_flow_definition({'levels':['a','b'],'nodes':[
    {'id':'x','name':'第一次','offset_days':7},{'id':'y','name':'第二次','offset_days':7}]})
kinds = {a['kind'] for a in adv}
check('没设窗口会提示', 'no_window' in kinds, kinds)
check('提示说清后果(超窗管理会失效)', any('超窗管理会失效' in a['detail'] for a in adv))
check('同一天多个访视会提示', 'same_day_visits' in kinds)
check('没配流程外阶段会提示', 'no_offschedule' in kinds)
check('完整的流程只剩零星建议', len(hs.lint_flow_definition(FLOW))==0, hs.lint_flow_definition(FLOW))

section('2. 流程库')
r,err = hs.upsert_flow({'code':PFX+'F','name':'术后康复随访','category':'术后康复',
                        'definition':FLOW,'status':'active','owner':'医生甲','scope':'shared'})
check('流程入库', r and r['version']=='1', err)
check('数对了叶子节点数(=一轮几次访视)', r['visits']==4, r['visits'])
check('数对了流程外阶段', r['offschedule']==2)
check('回带层级名', r['levels']==['阶段','访视','项目'])
q,_ = hs.query_flows(code=PFX+'F')
check('流程库查得到', q['count']==1)
check('标了共享', q['flows'][0]['scope']=='shared')
r,err = hs.copy_flow({'code':PFX+'F','new_code':PFX+'G','new_name':'复制的流程'})
check('复制成功', r and r['code']==PFX+'G', err)
check('复制出来是新流程的第 1 版', r['version']=='1')
check('不许复制成同名', hs.copy_flow({'code':PFX+'F','new_code':PFX+'F'})[0] is None)
check('目标已存在时拒绝', hs.copy_flow({'code':PFX+'F','new_code':PFX+'G'})[0] is None)

section('3. 实例化: 生成访视表')
# 锚点选 today-30, 让三种状态各出现一次:
#   术后7天  -> 计划 today-23, 窗口至 today-20  => 超窗
#   术后1月  -> 计划 today,    窗口 -5~+7       => 窗口内待办
#   术后3月  -> 计划 today+60                    => 未到期
anchor = (today - datetime.timedelta(days=30)).strftime('%Y-%m-%d')
r,err = hs.flow_instantiate({'flow_code':PFX+'F','patient_no':PFX+'001',
                             'anchor_date':anchor,'operator':'医生甲'})
check('实例化成功', r and r['created'], err)
check('生成 4 次访视(只有叶子节点产生访视)', r['visits']==4, r['visits'])
check('流程外阶段不预先生成', '不预先生成' in r['note'])
v,_ = hs.query_visits(patient_no=PFX+'001')
check('访视表查得到', v['count']==4)
byname = {x['name']:x for x in v['visits']}
check('多级路径记下来了', byname['术后7天']['node_path']=='术后早期 / 术后7天',
      byname['术后7天']['node_path'])
check('计划日 = 锚点 + offset',
      byname['术后7天']['planned_date'] ==
      (datetime.datetime.strptime(anchor,'%Y-%m-%d').date()+datetime.timedelta(days=7)).strftime('%Y-%m-%d'))
check('窗口按 [-2,+3] 算出来了',
      byname['术后7天']['window_start'] < byname['术后7天']['planned_date'] < byname['术后7天']['window_end'])
check('访视内容带过来了', len(byname['术后7天']['items'])==2)
sub('状态按今天和窗口算')
check('锚点 30 天前 -> 术后7天已超窗', byname['术后7天']['status']=='overdue', byname['术后7天']['status'])
check('超窗给出超了几天', byname['术后7天']['days_overdue'] is not None and byname['术后7天']['days_overdue']>0,
      byname['术后7天']['days_overdue'])
check('术后1月正落在窗口内', byname['术后1月']['status']=='due', byname['术后1月']['status'])
check('术后3月还没到期', byname['术后3月']['status']=='pending')
check('未超窗的不给超窗天数', byname['术后3月']['days_overdue'] is None)
sub('幂等')
r2,_ = hs.flow_instantiate({'flow_code':PFX+'F','patient_no':PFX+'001'})
check('重复实例化不重排', r2['created'] is False and r2['visits']==4, r2)
check('并说清为什么', '冲掉' in r2['hint'])

section('4. 访视流转')
vid = byname['术后1月']['id']
r,err = hs.visit_transition({'visit_id':vid,'action':'done','operator':'医生甲'})
check('窗口内完成', r and r['to']=='done', err)
check('窗口内完成不给方案偏离警告', 'warning' not in r)
over_id = byname['术后7天']['id']
r,err = hs.visit_transition({'visit_id':over_id,'action':'done','operator':'医生甲'})
check('超窗也能记为完成(如实记录)', r and r['to']=='done')
check('但给出方案偏离提示', 'warning' in r and '方案偏离' in r['warning'], r.get('warning'))
r,err = hs.visit_transition({'visit_id':byname['术后6月']['id'],'action':'skip'})
check('跳过必须写原因', r is None and '原因' in (err or ''), err)
r,err = hs.visit_transition({'visit_id':byname['术后6月']['id'],'action':'skip',
                             'note':'患者已达研究终点','operator':'医生甲'})
check('写了原因可跳过', r and r['to']=='skipped', err)
check('不存在的访视被拒', hs.visit_transition({'visit_id':999999,'action':'done'})[0] is None)
check('非法 action 被拒', hs.visit_transition({'visit_id':vid,'action':'bogus'})[0] is None)
r,_ = hs.visit_transition({'visit_id':vid,'action':'reopen'})
check('可以撤销完成, 状态按窗口重算', r['to'] in ('due','overdue','pending'), r['to'])
hs.visit_transition({'visit_id':vid,'action':'done','operator':'医生甲'})

section('5. 流程外阶段 (事件触发)')
r,err = hs.trigger_offschedule({'patient_no':PFX+'001','flow_code':PFX+'F','node_id':'ae',
                                'operator':'医生甲','note':'术后感染'})
check('触发不良事件', r and r['seq']==1, err)
check('提示它不计入完成率分母', '不计入随访完成率' in r['note'])
r2,_ = hs.trigger_offschedule({'patient_no':PFX+'001','flow_code':PFX+'F','node_id':'ae'})
check('同一个流程外阶段可以发生多次', r2['seq']==2, r2['seq'])
r,err = hs.trigger_offschedule({'patient_no':PFX+'001','flow_code':PFX+'F','node_id':'nope'})
check('不存在的流程外阶段被拒, 并列出有哪些', r is None and 'ae' in (err or ''), err)
check('不在流程里的患者被拒',
      hs.trigger_offschedule({'patient_no':PFX+'999','flow_code':PFX+'F','node_id':'ae'})[0] is None)
v,_ = hs.query_visits(patient_no=PFX+'001')
offs = [x for x in v['visits'] if x['is_offschedule']]
check('流程外访视也在访视表里', len(offs)==2, len(offs))
check('但标了 kind=offschedule', all(x['kind']=='offschedule' for x in offs))

section('6. 随访完成率: 分母是这块最容易做错的地方')
c,err = hs.flow_completion(flow_code=PFX+'F')
check('完成率算得出', c and c['by_flow'], err)
row = c['by_flow'][0]
print('     分母 %d = 已完成 %d + 超窗 %d + 跳过 %d + 窗口内待办 %d' %
      (row['denominator'], row['done'], row['overdue'], row['skipped'], row['due']))
check('分母 = 已到窗口期的计划内访视(3 次: 7天/1月/6月)', row['denominator']==3, row['denominator'])
check('完成 2 次 -> 66.7%%', row['completion_rate']==66.7, row['completion_rate'])
check('分母**不含**流程外阶段', c['excluded']['offschedule']==2, c['excluded'])
check('分母**不含**尚未到期的访视', c['excluded']['not_yet_due']==1, c['excluded'])
check('说明里讲清了为什么排除', '永远上不去' in c['denominator_note'])
sub('反证: 如果算进去会怎样')
naive = row['done'] * 100.0 / (row['denominator'] + c['excluded']['offschedule'] + c['excluded']['not_yet_due'])
check('把两类都算进分母, 完成率会从 66.7%% 掉到 %.1f%% —— 同样的工作量, 数字腰斩' % naive,
      naive < row['completion_rate'] * 0.7, (row['completion_rate'], round(naive,1)))

section('7. 终止流程')
hs.flow_instantiate({'flow_code':PFX+'F','patient_no':PFX+'002',
                     'anchor_date':today.strftime('%Y-%m-%d')})
r,err = hs.flow_end({'patient_no':PFX+'002','flow_code':PFX+'F','reason':'manual'})
check('手动终止必须写原因', r is None and '原因' in (err or ''), err)
r,err = hs.flow_end({'patient_no':PFX+'002','flow_code':PFX+'F','reason':'event',
                     'note':'发生研究终点事件','operator':'医生甲'})
check('事件触发终止', r and r['reason']=='event', err)
check('未完成的访视被置为取消而不是删除', r['cancelled_visits']>0, r['cancelled_visits'])
check('说清为什么不删', '看不出' in r['note'])
check('不能重复终止', hs.flow_end({'patient_no':PFX+'002','flow_code':PFX+'F','reason':'manual','note':'x'})[0] is None)
check('终止后不能再触发流程外阶段',
      hs.trigger_offschedule({'patient_no':PFX+'002','flow_code':PFX+'F','node_id':'ae'})[0] is None)
v,_ = hs.query_visits(patient_no=PFX+'002')
check('取消的访视仍查得到(看得出本来还有几次没做)',
      all(x['status']=='cancelled' for x in v['visits']), [x['status'] for x in v['visits']])
c2,_ = hs.flow_completion(flow_code=PFX+'F')
check('取消的访视不进分母', c2['by_flow'][0]['denominator']==3, c2['by_flow'][0]['denominator'])
check('但被单独统计出来', c2['excluded']['cancelled']>0, c2['excluded'])

section('8. 超窗查询与状态刷新')
r,_ = hs.flow_refresh_status(patient_no=PFX+'001')
check('刷新状态跑通', r['ok'])
v,_ = hs.query_visits(patient_no=PFX+'001', overdue_only=True)
check('超窗清单查得到', v['count']>=0)
v,_ = hs.query_visits(patient_no=PFX+'001', due_within=7)
check('未来 7 天到期的查得到', v['ok'])
v,_ = hs.query_visits(status='done')
check('按状态筛', all(x['status']=='done' for x in v['visits']))
check('访视带中文状态', v['visits'] and v['visits'][0]['status_label']=='已完成')

section('9. 流程改版不影响在随患者')
FLOW2 = json.loads(json.dumps(FLOW))
FLOW2['nodes'][0]['children'][0]['offset_days'] = 14      # 术后7天改成14天
r,err = hs.upsert_flow({'code':PFX+'F','name':'术后康复随访','category':'术后康复',
                        'definition':FLOW2,'status':'active'})
check('有在随患者时改流程 -> 自动开新版', r and r['version']=='2', (r or {}).get('version'))
check('说清了为什么', r and '不该把他们的日程重排' in (r.get('note') or ''), (r or {}).get('note'))
v,_ = hs.query_visits(patient_no=PFX+'001')
d7 = [x for x in v['visits'] if x['name']=='术后7天'][0]
check('在随患者的访视日期没被动过',
      d7['planned_date'] == (datetime.datetime.strptime(anchor,'%Y-%m-%d').date()
                             +datetime.timedelta(days=7)).strftime('%Y-%m-%d'),
      d7['planned_date'])

section('10. 清理')
clean()
print('  ✅ 测试数据已清')
finish()
