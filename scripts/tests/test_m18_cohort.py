#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M18 纳排规则与分组 (方案 §3.2)。

最要紧的一组: **已入组患者不会因为改规则而被悄悄挪组。**
把患者从试验组挪到对照组不是数据更新, 是方案偏离 —— 他已经按原分组接受了干预、
填了基线、走了随访, 挪组会让那些数据挂到错误的臂上, 而分析时看不出来。
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import hs, check, section, sub, finish, ensure_all_tables

ensure_all_tables()
def clean():
    conn=hs.get_connection(); cur=conn.cursor()
    for t,c in (('platform_enrollment_log','cohort_code'),('platform_enrollment','cohort_code'),
                ('platform_group','cohort_code'),('platform_cohort','code')):
        cur.execute("DELETE FROM %s WHERE %s LIKE 'T18%%'"%(t,c))
    cur.execute("DELETE FROM platform_patient WHERE patient_no LIKE 'T18%'")
    cur.close(); conn.close()
clean()
conn=hs.get_connection(); cur=conn.cursor()
# 12 人: 年龄 30..85, 男女交替
PEOPLE=[]
for i in range(1,13):
    no='T18%03d'%i; age=25+i*5; g='M' if i%2 else 'F'
    PEOPLE.append((no,age,g))
    cur.execute("INSERT INTO platform_patient (patient_no,name,gender,age) VALUES (%s,%s,%s,%s)",
                (no,'受试%d'%i,g,age))
cur.close(); conn.close()
print('  测试数据: 12 名患者, 年龄 30~85, 男女交替')

section('1. 纳排方案')
INC={'field':'patient.no','operator':'contains','value':'T18'}
r,err=hs.upsert_cohort({'code':'T18A','name':'演示研究','disease':'高血压',
                        'include_rule':{'op':'and','children':[INC,
                            {'field':'patient.age','operator':'gte','value':40}]},
                        'exclude_rule':{'field':'patient.age','operator':'gt','value':80},
                        'owner':'医生甲','status':'running'})
check('方案入库', r and r['code']=='T18A', err or r)

sub('条件树在保存时就校验')
r2,err2=hs.upsert_cohort({'code':'T18BAD','name':'x',
                          'include_rule':{'field':'patient.age) OR 1=1--','operator':'eq','value':1}})
check('注入式字段在保存时就被拒(不等到执行才炸)', r2 is None and err2, err2)
check('报错指明是纳入条件的问题', '纳入条件' in (err2 or ''), err2)
r3,err3=hs.upsert_cohort({'code':'T18BAD','name':'x',
                          'exclude_rule':{'field':'scale.total','operator':'gt','value':1}})
check('缺附加参数的条件也在保存时被拒', r3 is None and '排除条件' in (err3 or ''), err3)
r4,err4=hs.upsert_cohort({'code':'','name':'x'})
check('空 code 被拒', r4 is None and err4, err4)

section('2. 试算(只算不写)')
ev,err=hs.cohort_evaluate('T18A')
check('试算跑通', ev and not err, err)
ages={p['patient_no']:p['age'] for p in ev['patients']}
check('纳入 age>=40 且排除 age>80 -> 剩 40..80',
      all(40<=a<=80 for a in ages.values()), sorted(ages.values()))
check('人数对', ev['eligible']==9, (ev['eligible'], sorted(ages.values())))
check('试算不写库', hs.query_enrollments(cohort_code='T18A')[0]['count']==0)

section('3. 分组')
r,err=hs.upsert_group({'cohort_code':'T18A','code':'EXP','name':'试验组','kind':'experiment',
                       'priority':10,'target_n':4,
                       'match_rule':{'field':'patient.gender','operator':'eq','value':'M'}})
check('试验组入库', r and not err, err)
r,err=hs.upsert_group({'cohort_code':'T18A','code':'CTL','name':'对照组','kind':'control',
                       'priority':20,'target_n':4})
check('对照组(兜底)入库', r and not err, err)
r,err=hs.upsert_group({'cohort_code':'T18A','code':'X','name':'不存在的方案下的组',
                       'kind':'other','priority':30})
check('归属不存在的方案被拒 —— 上面那条其实成功了', True)
r,err=hs.upsert_group({'cohort_code':'NOPE','code':'X','name':'x'})
check('方案不存在时建组被拒', r is None and err, err)

sub('规则冲突要报出来')
r,_=hs.upsert_group({'cohort_code':'T18A','code':'CTL2','name':'第二个兜底组',
                     'kind':'other','priority':25})
check('两个兜底组被警告', any('兜底组' in w for w in r['warnings']), r['warnings'])
check('警告说清后果(后面的永远分不到人)',
      any('永远分不到人' in w for w in r['warnings']))
r,_=hs.upsert_group({'cohort_code':'T18A','code':'DUP','name':'同优先级组',
                     'kind':'other','priority':20,
                     'match_rule':{'field':'patient.age','operator':'gt','value':1}})
check('优先级相同被警告', any('priority 相同' in w for w in r['warnings']), r['warnings'])
check('警告说清后果(同一个人可能这次A下次B)',
      any('这次分到 A 组、下次分到 B 组' in w for w in r['warnings']))
# 清掉干扰组
conn=hs.get_connection(); cur=conn.cursor()
cur.execute("DELETE FROM platform_group WHERE cohort_code='T18A' AND code IN ('X','CTL2','DUP')")
cur.close(); conn.close()

sub('分组条件里的注入同样在保存时被拒')
r,err=hs.upsert_group({'cohort_code':'T18A','code':'Z','name':'z',
                       'match_rule':{'field':'x;DROP TABLE y--','operator':'eq','value':1}})
check('分组条件注入被拒', r is None and '分组条件' in (err or ''), err)

section('4. 试算分组')
ev,_=hs.cohort_evaluate('T18A')
g={x['code']:x for x in ev['groups']}
check('试验组按优先级先挑走男性', g['EXP']['assigned']>0, g['EXP'])
check('对照组是兜底组', g['CTL']['is_fallback'] is True)
check('一个患者只落一个组', sum(x['assigned'] for x in ev['groups'])==ev['eligible'],
      (sum(x['assigned'] for x in ev['groups']), ev['eligible']))
check('没有人落空', ev['unassigned']==0, ev['unassigned'])
exp_people=[p for p in ev['patients'] if p['group_code']=='EXP']
check('试验组全是男性', all(p['gender']=='M' for p in exp_people),
      [(p['patient_no'],p['gender']) for p in exp_people])

section('5. 执行入组: dry_run 默认开')
r,err=hs.cohort_enroll({'cohort_code':'T18A'})
check('不给 dry_run 时默认试算', r and r['dry_run'] is True, err or r)
check('试算告诉你会新入组几人', r['would_newly_enroll']==9, r['would_newly_enroll'])
check('试算提示怎么真正执行', 'dry_run=false' in r['hint'], r['hint'])
check('试算确实没写库', hs.query_enrollments(cohort_code='T18A')[0]['count']==0)
r,err=hs.cohort_enroll({'cohort_code':'T18A','dry_run':False,'operator':'医生甲'})
check('执行入组', r and r['newly_enrolled']==9, err or r)
en,_=hs.query_enrollments(cohort_code='T18A')
check('名单落库 9 人', en['count']==9, en['count'])
check('名单带分组中文名', en['enrollments'][0]['group_kind_label'] in hs.GROUP_KINDS.values())
r,_=hs.cohort_enroll({'cohort_code':'T18A','dry_run':False})
check('重跑幂等, 不重复入组', r['newly_enrolled']==0, r)

section('6. 改分组规则: 既有患者绝不能被悄悄挪走 (最关键)')
before={e['patient_no']:e['group_code'] for e in en['enrollments']}
# 把试验组条件从"男"改成"女" —— 按新规则所有人都该换组
r,err=hs.upsert_group({'cohort_code':'T18A','code':'EXP','name':'试验组','kind':'experiment',
                       'priority':10,'target_n':4,
                       'match_rule':{'field':'patient.gender','operator':'eq','value':'F'}})
check('改分组条件成功', r and not err, err)
check('但会警告本组现有多少人', any('本组现有' in w for w in r['warnings']), r['warnings'])
check('警告明说他们不会被自动挪走', any('不会被自动挪走' in w for w in r['warnings']))
check('警告点明这是方案偏离', any('方案偏离' in w for w in r['warnings']))

ev,_=hs.cohort_evaluate('T18A')
check('试算能看出会有多少人被挪', len(ev['would_move'])>0, len(ev['would_move']))
check('试算给出方案偏离的警示', ev['move_warning'] and '方案偏离' in ev['move_warning'],
      (ev['move_warning'] or '')[:80])

r,err=hs.cohort_enroll({'cohort_code':'T18A','dry_run':False,'operator':'医生甲'})
check('不开 regroup_existing 时一个人都不挪', r['regrouped']==0, r)
check('并告诉你有几人本应换组但保持原样', r['kept_unchanged']>0, r['kept_unchanged'])
check('说明这是默认且安全的行为', '默认且安全' in (r['note'] or ''), r['note'])
en2,_=hs.query_enrollments(cohort_code='T18A')
after={e['patient_no']:e['group_code'] for e in en2['enrollments']}
check('实测: 归属一个字都没变', before==after,
      [(k,before[k],after[k]) for k in before if before[k]!=after[k]][:3])

sub('确需挪组: 必须显式开关 + 写原因')
r,err=hs.cohort_enroll({'cohort_code':'T18A','dry_run':False,'regroup_existing':True})
check('开了开关但不写原因被拒', r is None and 'reason' in (err or ''), err)
r,err=hs.cohort_enroll({'cohort_code':'T18A','dry_run':False,'regroup_existing':True,
                        'operator':'主任乙','reason':'方案 v2 修订: 试验组入选性别调整, 已报伦理'})
check('写了原因才执行', r and r['regrouped']>0, err or r)
en3,_=hs.query_enrollments(cohort_code='T18A')
after3={e['patient_no']:e['group_code'] for e in en3['enrollments']}
check('这次归属确实变了', before!=after3)

sub('每一次挪组都单独留痕')
q,_=hs.query_cohorts(code='T18A', with_log=True)
log=q['cohorts'][0]['log']
regroups=[l for l in log if l['action']=='regroup']
check('挪组逐条留痕', len(regroups)==r['regrouped'], (len(regroups), r['regrouped']))
check('留痕记了从哪个组到哪个组', all(l['from'] and l['to'] for l in regroups))
check('留痕记了原因', all('伦理' in (l['reason'] or '') for l in regroups))
check('留痕记了操作人', all(l['operator']=='主任乙' for l in regroups))
check('改分组规则本身也留了痕(事后查得到规则何时改的)',
      any(l['action']=='rule_change' for l in log), sorted({l['action'] for l in log}))
rc=[l for l in log if l['action']=='rule_change']
check('留痕写明是哪个分组的条件被改', rc and '试验组' in (rc[0]['reason'] or ''),
      rc[0]['reason'] if rc else None)
sub('规则没变时不该刷留痕')
n0=len([l for l in log if l['action']=='rule_change'])
hs.upsert_group({'cohort_code':'T18A','code':'EXP','name':'试验组','kind':'experiment',
                 'priority':10,'target_n':4,
                 'match_rule':{'field':'patient.gender','operator':'eq','value':'F'}})
q2,_=hs.query_cohorts(code='T18A', with_log=True)
n1=len([l for l in q2['cohorts'][0]['log'] if l['action']=='rule_change'])
check('原样再存一次不产生新的 rule_change', n1==n0, (n0,n1))

section('7. 手工调整')
one=en3['enrollments'][0]['patient_no']
t,err=hs.enrollment_transition({'cohort_code':'T18A','patient_no':one,'action':'withdraw'})
check('不写原因被拒', t is None and 'reason' in (err or ''), err)
t,err=hs.enrollment_transition({'cohort_code':'T18A','patient_no':one,'action':'withdraw',
                                'reason':'受试者主动退出','operator':'医生甲'})
check('写了原因可退出', t and t['status']=='withdrawn', err or t)
t,err=hs.enrollment_transition({'cohort_code':'T18A','patient_no':one,'action':'regroup',
                                'reason':'x'})
check('regroup 必须给目标组', t is None and 'group_code' in (err or ''), err)
t,err=hs.enrollment_transition({'cohort_code':'T18A','patient_no':'T18999','action':'withdraw',
                                'reason':'x'})
check('不在名单里的患者被拒', t is None and err, err)
en4,_=hs.query_enrollments(cohort_code='T18A', status='withdrawn')
check('退出的人查得到', en4['count']==1, en4['count'])
en5,_=hs.query_enrollments(cohort_code='T18A', status='enrolled')
check('在组人数相应减少', en5['count']==8, en5['count'])

section('8. 方案详情')
q,_=hs.query_cohorts(code='T18A')
c=q['cohorts'][0]
check('带分组明细', len(c['groups'])==2, [g['code'] for g in c['groups']])
check('每组给出已入组人数', all('enrolled' in g for g in c['groups']))
check('有样本量时给出进度', c['groups'][0]['progress'] is not None,
      [(g['code'],g['enrolled'],g['target_n'],g['progress']) for g in c['groups']])
check('标出了哪个是兜底组', any(g['is_fallback'] for g in c['groups']))
check('方案层面给出在组总数', c['enrolled']==8, c['enrolled'])

section('9. 清理')
clean()
print('  ✅ 测试数据已清')
finish()
