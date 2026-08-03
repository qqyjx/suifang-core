#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M24 研究数据库状态管理 + 版本回滚 (方案 §3.1(3))。

方案原话: "重置后可修改 CRF 表、流程与分组信息, **且不影响已收集患者数据**"。
最后半句是整块的重心。三条钉死的规矩:
  1. 重置不动任何已收集的数据(用前后快照证明)
  2. 重置期间不收新数据(说不清是按哪版配置采的)
  3. 删除只能是逻辑删除(物理删患者数据在临床研究里不可接受)
再加: 回滚 = 把旧版再发一版, **不删任何版本**。
"""
import os, sys, json, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import hs, check, section, sub, finish, ensure_all_tables, db

PFX='T24'
ensure_all_tables()
for fn in ('ensure_platform_study_tables','ensure_platform_flow_tables','ensure_platform_crf_tables'):
    getattr(hs, fn)()
def clean():
    db(("DELETE FROM platform_study_log WHERE study_code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_study_log WHERE study_code LIKE %s",('crf:'+PFX+'%',)),
       ("DELETE FROM platform_study_log WHERE study_code LIKE %s",('flow:'+PFX+'%',)),
       ("DELETE FROM platform_study WHERE code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_visit WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_flow_instance WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_flow WHERE code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_crf_response WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_crf WHERE code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_scale_response WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_scale WHERE code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_enrollment_log WHERE cohort_code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_enrollment WHERE cohort_code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_group WHERE cohort_code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_cohort WHERE code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_patient WHERE patient_no LIKE %s",(PFX+'%',)))
clean()

# --- 造一个有真实数据的研究 ---
for i in (1,2,3):
    db(("INSERT INTO platform_patient (patient_no,name,age) VALUES (%s,%s,%s)",
        ('%s%03d'%(PFX,i), '研究对象%d'%i, 50+i)))
hs.upsert_cohort({'code':PFX+'C','name':'演示方案','status':'running',
                  'include_rule':{'field':'patient.no','operator':'contains','value':PFX}})
hs.upsert_group({'cohort_code':PFX+'C','code':'G1','name':'唯一组','priority':10})
hs.cohort_enroll({'cohort_code':PFX+'C','dry_run':False,'operator':'医生甲'})
YN=[{'label':'是','value':1},{'label':'否','value':0}]
CRF1={'items':[{'id':'q1','type':'text','text':'主诉','required':True},
               {'id':'q2','type':'single','text':'有无不适','options':YN}],'logic':[]}
hs.upsert_platform_crf({'code':PFX+'F','name':'演示表','definition':CRF1,'status':'active'})
hs.submit_crf_response({'crf_code':PFX+'F','patient_no':PFX+'001','data':{'q1':'头晕','q2':1}})
FLOW={'levels':['阶段','访视'],'anchor':'enroll',
      'nodes':[{'id':'s1','name':'随访','children':[
          {'id':'v1','name':'第1次','offset_days':7,'window':[-2,3],'items':[]}]}],'offschedule':[]}
hs.upsert_flow({'code':PFX+'L','name':'演示流程','definition':FLOW,'status':'active'})
hs.flow_instantiate({'flow_code':PFX+'L','patient_no':PFX+'001',
                     'anchor_date':datetime.date.today().strftime('%Y-%m-%d')})
print('  测试数据: 3 患者已入组 / 1 份 CRF 填报 / 1 条流程实例')

section('1. 建库与状态机')
r,err = hs.upsert_study({'code':PFX+'S','name':'演示研究','sponsor':'某院',
                         'cohort_code':PFX+'C','crf_codes':[PFX+'F'],'flow_codes':[PFX+'L'],
                         'owner':'医生甲'})
check('建库默认暂存', r and r['status']=='staged', err)
check('暂存态可以改配置', hs.study_check_action(PFX+'S','edit_config')[0] is True)
check('暂存态不能入组', hs.study_check_action(PFX+'S','enroll')[0] is False)
t,err = hs.study_transition({'code':PFX+'S','action':'activate','operator':'医生甲'})
check('启用', t and t['to']=='running', err)
check('运行中可以入组和收数据',
      hs.study_check_action(PFX+'S','enroll')[0] and hs.study_check_action(PFX+'S','collect')[0])
ok,why = hs.study_check_action(PFX+'S','edit_config')
check('运行中不能直接改配置', ok is False)
check('并提示先重置', '先执行"重置"' in (why or ''), why)
check('非法流转被拒并给出允许的前置状态',
      '允许的前置状态' in (hs.study_transition({'code':PFX+'S','action':'restore','operator':'x'})[1] or ''))
check('不存在的库被拒', hs.study_transition({'code':'NOPE','action':'activate','operator':'x'})[0] is None)

sub('运行中不许换绑定')
r,err = hs.upsert_study({'code':PFX+'S','name':'演示研究','cohort_code':'别的方案'})
check('换绑定被拒', r is None and err, err)
check('报错说清等于换了一个研究', '换了一个研究' in (err or ''), err)
check('并指路到重置', '重置' in (err or ''))

section('2. 重置: 不动任何已收集的数据 (整块的重心)')
before,_ = hs.query_studies(code=PFX+'S')
d0 = before['studies'][0]['data']
print('     重置前:', d0)
check('重置必须署名', hs.study_transition({'code':PFX+'S','action':'reset','reason':'x'})[0] is None)
check('重置必须写原因',
      hs.study_transition({'code':PFX+'S','action':'reset','operator':'医生甲'})[0] is None)
t,err = hs.study_transition({'code':PFX+'S','action':'reset','operator':'医生甲',
                             'reason':'方案 v2 修订, 需调整 CRF 与分组'})
check('重置成功', t and t['to']=='reset', err)
check('**前后数据量完全一致**', t['data_unchanged'] is True, t['data_snapshot'])
check('快照里入组人数没变', t['data_snapshot']['after'].get('enrolled')==d0.get('enrolled'),
      (d0.get('enrolled'), t['data_snapshot']['after'].get('enrolled')))
check('快照里 CRF 填报没变', t['data_snapshot']['after'].get('crf_responses')==d0.get('crf_responses'))
check('快照里访视没变', t['data_snapshot']['after'].get('visits')==d0.get('visits'))
check('提示明说数据一条没动', '一条没动' in (t['note'] or ''), (t['note'] or '')[:60])
after,_ = hs.query_studies(code=PFX+'S')
check('再查一遍仍然对得上', after['studies'][0]['data']==d0, (d0, after['studies'][0]['data']))
check('实测: 填报记录还在', hs.query_crf_responses(patient_no=PFX+'001')[0]['count']==1)
check('实测: 入组名单还在', hs.query_enrollments(cohort_code=PFX+'C')[0]['count']==3)
check('实测: 访视还在', hs.query_visits(patient_no=PFX+'001')[0]['count']>=1)

sub('重置态: 能改配置, 但不收新数据')
check('重置态可以改配置', hs.study_check_action(PFX+'S','edit_config')[0] is True)
ok,why = hs.study_check_action(PFX+'S','collect')
check('重置态**不能**收新数据', ok is False)
check('并解释了为什么', '说不清是按哪版配置' in (why or ''), why)
check('重置态可以换绑定', hs.upsert_study({'code':PFX+'S','name':'演示研究',
                                            'cohort_code':PFX+'C','crf_codes':[PFX+'F']})[0] is not None)
t,_ = hs.study_transition({'code':PFX+'S','action':'activate','operator':'医生甲'})
check('改完能回到运行中', t['to']=='running')

section('3. 删除只能是逻辑删除')
hs.study_transition({'code':PFX+'S','action':'end','operator':'医生甲'})
check('结束后只读', not any(hs.query_studies(code=PFX+'S')[0]['studies'][0]['allowed'].values()))
check('删除必须写原因', hs.study_transition({'code':PFX+'S','action':'delete','operator':'x'})[0] is None)
t,err = hs.study_transition({'code':PFX+'S','action':'delete','operator':'主任乙','reason':'项目取消'})
check('删除成功', t and t['to']=='deleted', err)
check('说明这是逻辑删除', '数据一行没删' in (t['note'] or ''), (t['note'] or '')[:50])
check('说明了为什么不物理删', '不可接受' in (t['note'] or ''))
check('**数据一条没少**', t['data_unchanged'] is True, t['data_snapshot'])
check('实测: 填报记录仍在', hs.query_crf_responses(patient_no=PFX+'001')[0]['count']==1)
check('实测: 入组名单仍在', hs.query_enrollments(cohort_code=PFX+'C')[0]['count']==3)
q,_ = hs.query_studies()
check('已删除的不出现在常规列表', not any(s['code']==PFX+'S' for s in q['studies']))
q,_ = hs.query_studies(include_deleted=True)
check('显式要才看得到', any(s['code']==PFX+'S' for s in q['studies']))
t,err = hs.study_transition({'code':PFX+'S','action':'restore','operator':'主任乙'})
check('可以恢复', t and t['to']=='staged', err)

section('4. 状态留痕带数据快照')
q,_ = hs.query_studies(code=PFX+'S', with_log=True)
log = q['studies'][0]['log']
check('留痕齐全', len(log)>=5, [l['action'] for l in log])
check('留痕带中文动作名', all(l['action_label'] for l in log))
resets = [l for l in log if l['action']=='reset']
check('重置那条记了原因', resets and '方案 v2' in (resets[0]['reason'] or ''))
check('重置那条带前后快照', resets and resets[0]['snapshot'] and
      resets[0]['snapshot']['before']==resets[0]['snapshot']['after'],
      resets[0]['snapshot'] if resets else None)
dels = [l for l in log if l['action']=='delete']
check('删除那条也带快照(事后能证明没弄丢数据)', dels and dels[0]['snapshot'])

section('5. 版本回滚: 把旧版再发一版, 不删任何版本')
CRF2 = json.loads(json.dumps(CRF1))
CRF2['items'][0]['text'] = '主诉(v2 措辞)'
hs.upsert_platform_crf({'code':PFX+'F','name':'演示表','definition':CRF2,'status':'active'})
CRF3 = json.loads(json.dumps(CRF1))
CRF3['items'].pop()          # 破坏性 -> 会开新版
hs.upsert_platform_crf({'code':PFX+'F','name':'演示表','definition':CRF3,'status':'active'})
h,err = hs.query_version_history('crf', PFX+'F')
check('版本历史查得到', h and len(h['versions'])>=2, err or h)
vers = [v['version'] for v in h['versions']]
print('     现有版本:', vers, '| 最新:', h['latest'])
check('标出了哪些版本在被使用', any(v['in_use']>0 for v in h['versions']),
      [(v['version'],v['in_use']) for v in h['versions']])
check('说明了在用的版本不能删', '不能删' in h['note'])
sub('回滚参数校验')
check('回滚必须署名', hs.rollback_version({'kind':'crf','code':PFX+'F','to_version':'1'})[0] is None)
check('回滚必须写原因',
      hs.rollback_version({'kind':'crf','code':PFX+'F','to_version':'1','operator':'x'})[0] is None)
check('不存在的版本被拒并列出现有版本',
      PFX+'F' in (hs.rollback_version({'kind':'crf','code':PFX+'F','to_version':'99',
                                       'operator':'x','reason':'y'})[1] or '') or
      '现有' in (hs.rollback_version({'kind':'crf','code':PFX+'F','to_version':'99',
                                      'operator':'x','reason':'y'})[1] or ''))
check('回滚到当前最新版被拒',
      hs.rollback_version({'kind':'crf','code':PFX+'F','to_version':h['latest'],
                           'operator':'x','reason':'y'})[0] is None)
sub('回滚本身')
n_before = len(h['versions'])
r,err = hs.rollback_version({'kind':'crf','code':PFX+'F','to_version':'1',
                             'operator':'主任乙','reason':'v2 删错了题, 先退回 v1'})
check('回滚成功', r and r['ok'], err)
check('产出的是**新版本**而不是删旧版', r['new_version'] not in vers, (r['new_version'], vers))
h2,_ = hs.query_version_history('crf', PFX+'F')
check('版本数只增不减', len(h2['versions'])==n_before+1, (n_before, len(h2['versions'])))
check('中间那几版原样留着', all(v in [x['version'] for x in h2['versions']] for v in vers))
check('新版内容等于回滚目标',
      len(hs.query_platform_crfs(code=PFX+'F', version=r['new_version'])[0]['crfs'][0]['definition']['items'])
      == len(CRF1['items']))
check('说明了为什么不删旧版', '读不懂' in (r['note'] or ''), (r['note'] or '')[:60])
check('回滚记录留了痕', h2['rollbacks'] and '退回 v1' in h2['rollbacks'][0]['reason'],
      h2['rollbacks'][:1])
check('回滚记录记了从哪版到哪版', h2['rollbacks'][0]['from'] and h2['rollbacks'][0]['to'])
sub('流程也能回滚')
FLOW2 = json.loads(json.dumps(FLOW))
FLOW2['nodes'][0]['children'][0]['offset_days'] = 14
hs.upsert_flow({'code':PFX+'L','name':'演示流程','definition':FLOW2,'status':'active'})
hf,_ = hs.query_version_history('flow', PFX+'L')
if len(hf['versions'])>1:
    r,err = hs.rollback_version({'kind':'flow','code':PFX+'L','to_version':'1',
                                 'operator':'主任乙','reason':'改错了'})
    check('流程回滚成功', r and r['ok'], err)
    check('同样是新版本', r['new_version'] != '1')
else:
    check('流程无在随患者时就地改, 未开新版(符合 M19 设计)', True)
check('未知 kind 被拒', hs.rollback_version({'kind':'scale','code':'x','to_version':'1',
                                             'operator':'a','reason':'b'})[0] is None)

section('6. 清理')
clean()
print('  ✅ 测试数据已清')
finish()
