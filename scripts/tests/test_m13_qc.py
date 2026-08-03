#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M13 数据质控 (方案 §4.3)。

重点验三件事:
  1. 质控 ≠ 预警 —— 39℃ 是临床异常(归 M7), 366℃ 是录入错(归质控), 两者不能互串
  2. 历次对比抓得到"逐题完全一致" —— 这类数据每一项都合法, 只有比历史才看得出
  3. 质疑单每一步流转都留痕, 且非法流转被拒
"""
import os, sys, json, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import hs, check, section, sub, finish, ensure_all_tables, db

PFX = 'T13'
ensure_all_tables()
def clean():
    db(("DELETE FROM platform_qc_query_log WHERE query_id IN (SELECT id FROM platform_qc_query WHERE patient_no LIKE %s)", (PFX+'%',)),
       ("DELETE FROM platform_qc_query WHERE patient_no LIKE %s", (PFX+'%',)),
       ("DELETE FROM platform_qc_finding WHERE patient_no LIKE %s", (PFX+'%',)),
       ("DELETE FROM platform_scale_response WHERE patient_no LIKE %s", (PFX+'%',)),
       ("DELETE FROM platform_scale WHERE code LIKE %s", (PFX+'%',)),
       ("DELETE FROM platform_patient WHERE patient_no LIKE %s", (PFX+'%',)))
clean()

section('1. 表内规则: 必填 / 范围 / 格式')
DEFN = {'items': [
    {'id':'q1','text':'过去两周情绪低落','type':'single','required':True,
     'options':[{'label':l,'value':v} for v,l in enumerate(['没有','偶尔','经常','总是'])]},
    {'id':'q2','text':'每晚睡眠小时数','type':'number','required':True,'min':0,'max':24},
    {'id':'q3','text':'联系电话','type':'text','format':'phone'},
    {'id':'q4','text':'身份证号','type':'text','format':'id_card'}],
    'scoring':{'total':{'method':'sum','items':['q1']},'subscales':[],'levels':[]}}
f = hs.qc_check_scale_answers(DEFN, {'q2':7}, PFX+'001', 1)
check('必填未答被抓到', any(x['rule_code']=='required_missing' and x['item_id']=='q1' for x in f), f)
check('必填是强校验(阻断)', all(x['severity']=='block' for x in f if x['rule_code']=='required_missing'))
f = hs.qc_check_scale_answers(DEFN, {'q1':1,'q2':30}, PFX+'001', 1)
check('超范围被抓到', any(x['rule_code']=='out_of_range' for x in f), [x['detail'] for x in f])
check('合法手机号放行',
      not any(x['rule_code']=='format_invalid'
              for x in hs.qc_check_scale_answers(DEFN, {'q1':1,'q2':7,'q3':'13800138000'}, PFX+'001', 1)))
check('10 位手机号被拦',
      any(x['rule_code']=='format_invalid'
          for x in hs.qc_check_scale_answers(DEFN, {'q1':1,'q2':7,'q3':'1380013800'}, PFX+'001', 1)))

sub('身份证走校验位, 不只是正则')
W=[7,9,10,5,8,4,2,1,6,3,7,9,10,5,8,4,2]
mk=lambda b: b+'10X98765432'[sum(int(b[i])*W[i] for i in range(17))%11]
good=mk('11010119900307721')
check('长度不对被拦', hs._qc_check_id_card('11010119900307') is not None)
check('合法号放行 ('+good+')', hs._qc_check_id_card(good) is None, hs._qc_check_id_card(good))
swapped=good[:14]+good[15]+good[14]+good[16:]
bad=hs._qc_check_id_card(swapped)
check('相邻两位打颠倒被校验位抓到 —— 正则抓不到这个', bad and '校验位' in bad, bad)
check('月份 13 被拦', hs._qc_check_id_card(mk('11010119901307721')) is not None)

section('2. 质控 ≠ 预警 (这块最容易做错)')
check('体温 39.0 不是质控问题(临床预警, 归 M7)', hs.qc_check_vital('temp',39.0)==[])
r=hs.qc_check_vital('temp',366.0)
check('体温 366.0 是质控问题(漏小数点)', len(r)==1 and r[0]['rule_code']=='impossible_value', r[0]['detail'] if r else '')
check('说明里点明了不当临床异常处理', r and '不当临床异常' in r[0]['detail'])
check('心率 45 不是质控问题(心动过缓归预警)', hs.qc_check_vital('hr',45)==[])
check('心率 400 是质控问题', len(hs.qc_check_vital('hr',400))==1)
check('血氧 101% 是质控问题(定义上不可能)', len(hs.qc_check_vital('spo2',101))==1)
check('血氧 88% 不是质控问题(低氧归预警)', hs.qc_check_vital('spo2',88)==[])
sub('跨字段: 单看都合法, 放一起才露馅')
check('sbp=120 dbp=80 正常', hs.qc_check_bp_pair(120,80)==[])
check('sbp=80 dbp=120 高低压填反被抓到', len(hs.qc_check_bp_pair(80,120))==1)
check('两个值单看都在可能范围内(所以单字段规则抓不到)',
      hs.qc_check_vital('sbp',80)==[] and hs.qc_check_vital('dbp',120)==[])
check('sbp==dbp 也算矛盾', len(hs.qc_check_bp_pair(100,100))==1)

section('3. 历次对比')
NINE={'items':[{'id':'p%d'%i,'text':'t%d'%i,'type':'single',
                'options':[{'label':str(v),'value':v} for v in range(4)]} for i in range(1,10)]}
VARIED={'p1':2,'p2':0,'p3':3,'p4':1,'p5':2,'p6':0,'p7':1,'p8':3,'p9':2}
FLAT={'p%d'%i:0 for i in range(1,10)}
prev={'id':9,'answers':dict(VARIED),'total_score':2.0,'created_at':'2026-07-01 10:00'}
f=hs.qc_compare_with_previous(NINE, dict(VARIED), 2.0, prev, PFX+'001', 10)
check('逐题完全一致被抓到', any(x['rule_code']=='identical_to_previous' for x in f), [x['detail'] for x in f])
n=[x for x in f if x['rule_code']=='identical_to_previous'][0]
check('是弱校验(提示, 不阻断)', n['severity']=='warn')
check('说明里点出"沿用上次结果"的可能', '沿用上次结果' in n['detail'])
check('依据里带了上次记录 id', n['basis'].get('prev_id')==9, n['basis'])
one=dict(VARIED); one['p4']=3
check('改了一题就不再报"完全一致"',
      not any(x['rule_code']=='identical_to_previous'
              for x in hs.qc_compare_with_previous(NINE, one, 4.0, prev, PFX+'001', 10)))
flat_prev={'id':8,'answers':dict(FLAT),'total_score':0.0,'created_at':'2026-07-01 10:00'}
check('一路同档(9 题全 0)两次一样**不**误报 —— 筛查量表上这是常态',
      not any(x['rule_code']=='identical_to_previous'
              for x in hs.qc_compare_with_previous(NINE, dict(FLAT), 0.0, flat_prev, PFX+'001', 10)))
check('总分 2→20 报跳变',
      any(x['rule_code']=='delta_jump'
          for x in hs.qc_compare_with_previous(NINE, {'p1':3}, 20.0, prev, PFX+'001', 10)))
check('2→4 不报(比例够但绝对差只有 2 分)',
      not any(x['rule_code']=='delta_jump'
              for x in hs.qc_compare_with_previous(NINE, {'p1':3}, 4.0, prev, PFX+'001', 10)))
check('没有上一份时不报任何对比问题',
      hs.qc_compare_with_previous(NINE, dict(VARIED), 2.0, None, PFX+'001', 10)==[])
SHORT={'items':[{'id':'a','text':'x','type':'number'},{'id':'b','text':'y','type':'number'}]}
check('题目太少(2 题)不报"完全一致" —— 那没有信息量',
      hs.qc_compare_with_previous(SHORT, {'a':1,'b':2}, 3.0,
                                  {'id':1,'answers':{'a':1,'b':2},'total_score':3.0}, 'P', 1)==[])

section('4. 端到端: 入库 -> 跑质控 -> 落发现')
E2E={'items':NINE['items']+[{'id':'q2','text':'每晚睡眠小时数','type':'number','required':True,'min':0,'max':24}],
     'scoring':{'total':{'method':'sum','items':[i['id'] for i in NINE['items']]},'subscales':[],'levels':[]}}
ok,err=hs.upsert_platform_scale({'code':PFX+'-4','name':'质控测试量表','definition':E2E})
check('测试量表入库', ok and not err, err)
db(("INSERT INTO platform_patient (patient_no,name) VALUES (%s,'质控测试患者')", (PFX+'001',)))
A1=dict(VARIED, q2=6); A3=dict(VARIED, p1=3, q2=6)
base=datetime.datetime(2026,7,1,10,0,0)
for at,ans,tot in [(base,A1,14.0), (base+datetime.timedelta(days=14),A1,14.0),
                   (base+datetime.timedelta(days=28),A3,40.0),
                   (base+datetime.timedelta(days=28,minutes=5),A3,40.0)]:
    db(("""INSERT INTO platform_scale_response
           (scale_code,scale_version,patient_no,answers,total_score,status,created_at)
           VALUES (%s,'1',%s,%s,%s,'submitted',%s)""",
        (PFX+'-4', PFX+'001', json.dumps(ans,ensure_ascii=False), tot, at)))
res,err=hs.platform_qc_run(patient_no=PFX+'001')
check('质控跑通', res and not err, err)
check('扫了 4 份填报', res['scanned_responses']==4, res['scanned_responses'])
check('抓到"逐题一致"', res['by_rule'].get('identical_to_previous',0)>=1, res['by_rule'])
check('抓到"总分跳变"', res['by_rule'].get('delta_jump',0)>=1, res['by_rule'])
check('抓到"重复提交"', res['by_rule'].get('duplicate_submission',0)>=1, res['by_rule'])
sub('重跑幂等: 同一个问题不该堆成两条')
res2,_=hs.platform_qc_run(patient_no=PFX+'001')
check('重跑没有新增', res2['new']==0, (res2['new'],res2['already_known']))
check('重跑全部命中已存在', res2['already_known']==res2['findings'])
lst,err=hs.query_qc_findings(patient_no=PFX+'001')
check('发现列表取得到', lst and lst['count']>0, err)
check('列表带规则中文名', all(x.get('rule_label') for x in lst['findings']))
check('列表带判定依据供复核', any(x.get('basis') for x in lst['findings']))
check('block 排在 warn 前面',
      [x['severity'] for x in lst['findings']] ==
      sorted([x['severity'] for x in lst['findings']], key=lambda s: 0 if s=='block' else 1))

section('5. 质疑单生命周期 + 留痕')
fid=lst['findings'][0]['id']
r,err=hs.platform_qc_query_raise({'finding_id':fid,'target_kind':'scale_response','target_id':1,
                                  'question':'请核对本次是否实际重新评估',
                                  'raised_by':'张质控','raiser_role':'site_qc'})
check('提质疑成功', r and r['status']=='open', err)
qid=r['query_id']
d,_=hs.query_qc_findings(patient_no=PFX+'001', status='queried')
check('被质疑的发现改成 queried', any(x['id']==fid for x in d['findings']))
r,err=hs.platform_qc_query_raise({'target_kind':'scale_response','target_id':1,'question':''})
check('空问题被拒', r is None and err and 'question' in err, err)
r,err=hs.platform_qc_query_raise({'target_kind':'bogus','target_id':1,'question':'x'})
check('非法 target_kind 被拒', r is None and err, err)
sub('状态机')
r,err=hs.platform_qc_query_transition({'query_id':qid,'action':'close','operator':'李管理'})
check('open 可以直接关闭', r and r['to']=='closed', err)
r,err=hs.platform_qc_query_transition({'query_id':qid,'action':'answer','remark':'x'})
check('已关闭的不能再回复', r is None and err and 'closed' in err, err)
check('拒绝时告知允许的前置状态', err and '允许的前置状态' in err, err)
r,err=hs.platform_qc_query_transition({'query_id':qid,'action':'reopen','operator':'王稽查','operator_role':'auditor'})
check('稽查员可以推翻已关闭的结论(reopen)', r and r['to']=='reopened', err)
r,err=hs.platform_qc_query_transition({'query_id':qid,'action':'answer','remark':'已核对原始记录, 本次为实际重测',
                                       'operator':'赵录入','operator_role':'entry'})
check('reopen 后可以回复', r and r['to']=='answered', err)
r,err=hs.platform_qc_query_transition({'query_id':qid,'action':'answer','remark':''})
check('空回复被拒(回复内容就是核查结论)', r is None and err, err)
r,err=hs.platform_qc_query_transition({'query_id':qid,'action':'close','operator':'王稽查','remark':'结论接受'})
check('回复后可关闭', r and r['to']=='closed', err)
check('不存在的质疑单被拒', hs.platform_qc_query_transition({'query_id':999999,'action':'close'})[0] is None)
check('非法 action 被拒', hs.platform_qc_query_transition({'query_id':qid,'action':'bogus'})[0] is None)
q,err=hs.query_qc_queries(query_id=qid, with_log=True)
row=q['queries'][0]; log=row['log']
check('留痕 5 步齐全', len(log)==5, [e['action'] for e in log])
check('留痕顺序正确', [e['action'] for e in log]==['raise','close','reopen','answer','close'])
check('每步都记了从哪到哪', all(e['to'] for e in log))
check('reopen 那步记了是稽查员做的',
      any(e['action']=='reopen' and e['operator_role']=='auditor' for e in log))
check('被 reopen 清掉的旧关闭记录仍在留痕里(不可抹除)',
      sum(1 for e in log if e['action']=='close')==2)
check('角色有中文名', row['raiser_role_label']=='单位质控员', row['raiser_role_label'])
check('关联的发现已置为 resolved',
      hs.query_qc_findings(patient_no=PFX+'001', status='resolved')[0]['count']>=1)

section('6. 历次对比明细 (前端标红 + 导出的数据源)')
c,err=hs.platform_qc_compare(PFX+'001', PFX+'-4')
check('对比取得到', c and not err, err)
check('4 轮都在', len(c['rounds'])==4, len(c['rounds']))
check('逐题矩阵覆盖全部题目', len(c['items'])==10, len(c['items']))
q1=[m for m in c['items'] if m['item_id']=='p1'][0]
check('首轮不标变化', q1['changed'][0] is False)
check('第 2 轮 p1 未变', q1['changed'][1] is False, q1['changed'])
check('第 3 轮 p1 变了 -> 标红', q1['changed'][2] is True, q1['changed'])
q2=[m for m in c['items'] if m['item_id']=='q2'][0]
check('q2 全程未变被标 all_same', q2['all_same'] is True)
check('给出选项文字而不只是数字', q1['labels'][0]=='2', q1['labels'][:2])
check('每轮算了总分变化', c['rounds'][2]['delta']==26.0, [r['delta'] for r in c['rounds']])
check('每轮算了变化题数', c['rounds'][1]['changed_items']==0, [r['changed_items'] for r in c['rounds']])
check('缺参数返回明确报错', hs.platform_qc_compare(PFX+'001', None)[0] is None)
c3,_=hs.platform_qc_compare(PFX+'_NOBODY', PFX+'-4')
check('无记录时给空态而不是报错', c3 and c3['rounds']==[] and c3.get('note'))

section('7. 清理')
clean()
print('  ✅ 测试数据已清')
finish()
