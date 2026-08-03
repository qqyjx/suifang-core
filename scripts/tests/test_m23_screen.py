#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M23 主动筛查与自助收录 (方案 §4.1)。

公开填报链接是整个平台唯一一个"不登录就能访问"的入口, 它打在患者库上。
断言集中在四条规矩:
  1. **写入单向** —— 公开接口绝不回显任何已有患者的数据
  2. **提交进待审** —— 陌生人填的东西不直接进患者库
  3. **token 不可预测**, 带有效期与次数上限
  4. **限流**
"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import hs, check, section, sub, finish, ensure_all_tables, db

def patient_exists(no):
    """患者列表是内联在路由里的, 没有独立函数, 直接查库。"""
    conn = hs.get_connection(); cur = conn.cursor()
    cur.execute('SELECT 1 FROM platform_patient WHERE patient_no=%s', (no,))
    hit = cur.fetchone() is not None
    cur.close(); conn.close()
    return hit

PFX='T23'
ensure_all_tables(); hs.ensure_platform_screen_tables(); hs.ensure_platform_crf_tables()
def clean():
    db(("DELETE FROM platform_screen_submission WHERE task_code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_screen_link WHERE task_code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_screen_task WHERE code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_crf WHERE code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_patient WHERE patient_no LIKE %s",(PFX+'%',)))
clean()
db(("INSERT INTO platform_patient (patient_no,name) VALUES (%s,'既有患者')",(PFX+'001',)))
FORM={'items':[{'id':'name','type':'text','text':'姓名','required':True},
               {'id':'age','type':'number','text':'年龄','min':0,'max':130},
               {'id':'sym','type':'single','text':'最近是否有头晕',
                'options':[{'label':'有','value':1},{'label':'无','value':0}]}],
      'logic':[]}
hs.upsert_platform_crf({'code':PFX+'F','name':'筛查问卷','definition':FORM,'status':'active'})

section('1. 任务生命周期 (§4.1(4))')
r,err = hs.upsert_screen_task({'code':PFX+'T','name':'高血压主动筛查','crf_code':PFX+'F',
                               'intro':'请如实填写','owner':'医生甲','target_n':50,
                               'end_date':'2026-12-31'})
check('建任务', r and r['status']=='draft', err)
check('日期格式错被拒', hs.upsert_screen_task({'code':PFX+'X','name':'x','start_date':'2026/1/1'})[0] is None)
sub('未审批不能生成链接')
l,err = hs.create_screen_link({'task_code':PFX+'T'})
check('草稿状态不给链接', l is None and err, err)
check('报错点明这是绕过审批', '绕过了审批' in (err or ''), err)
sub('审批流')
t,err = hs.screen_task_transition({'code':PFX+'T','action':'submit','operator':'医生甲'})
check('提交审批', t and t['to']=='pending', err)
check('批准必须署名', hs.screen_task_transition({'code':PFX+'T','action':'approve'})[0] is None)
check('驳回必须写原因',
      hs.screen_task_transition({'code':PFX+'T','action':'reject','operator':'主任乙'})[0] is None)
t,err = hs.screen_task_transition({'code':PFX+'T','action':'approve','operator':'主任乙','note':'同意'})
check('批准后进入进行中', t and t['to']=='running', err)
check('非法流转被拒并给出允许的前置状态',
      '允许的前置状态' in (hs.screen_task_transition({'code':PFX+'T','action':'approve','operator':'x'})[1] or ''))

section('2. 自助链接: token 不可预测 + 有效期 + 次数上限')
l,err = hs.create_screen_link({'task_code':PFX+'T','label':'门诊海报','valid_days':30,
                               'max_uses':5,'created_by':'医生甲'})
check('生成链接', l and l['token'], err)
tok = l['token']
check('token 足够长且随机', len(tok)>=40, len(tok))
l2,_ = hs.create_screen_link({'task_code':PFX+'T'})
check('两次生成的 token 不同', l2['token'] != tok)
check('URL 带 token', tok in l['url'], l['url'])
check('没装二维码库时给出说明而不是假装有',
      l.get('qr_png') or ('比没有二维码更糟' in (l.get('qr_note') or '')), l.get('qr_note','')[:50])
check('未配对外域名时提醒', l.get('url_note') is None or '印到海报上' in l['url_note'])
b,err = hs.create_screen_link({'task_code':PFX+'T','kind':'bound'})
check('定向链接不给 patient_no 被拒', b is None and err, err)
b,err = hs.create_screen_link({'task_code':PFX+'T','kind':'bound','patient_no':PFX+'001'})
check('定向链接默认一次性', b and b['max_uses']==1, (b or {}).get('max_uses'))
check('有效期范围被校验',
      hs.create_screen_link({'task_code':PFX+'T','valid_days':999})[0] is None)

section('3. 公开取表单: 绝不回显患者数据 (最关键)')
f,err = hs.screen_form_public(tok)
check('取到表单', f and f['ok'], err)
check('给了表单结构', f['definition'] and len(f['definition']['items'])==3)
check('给了任务名与说明', f['task_name']=='高血压主动筛查' and f['intro']=='请如实填写')
blob = json.dumps(f, ensure_ascii=False)
check('返回里没有任何门诊号', PFX+'001' not in blob, blob[:200])
check('返回里没有患者姓名', '既有患者' not in blob)
check('明确告知不会显示既往病历', '不会显示任何既往病历' in f['privacy_note'])
fb,_ = hs.screen_form_public(b['token'])
check('定向链接也只说"记在您名下"', fb['bound'] and fb['bound_note']=='本次填报将记在您名下')
check('定向链接同样不吐姓名', '既有患者' not in json.dumps(fb, ensure_ascii=False))
check('无效 token 被拒', hs.screen_form_public('nosuchtoken')[1])
check('空 token 被拒', hs.screen_form_public('')[1])

section('4. 公开提交: 落待审区, 不直接建档')
s,err = hs.screen_submit_public({'token':tok,'data':{'name':'王五','age':52,'sym':1,
                                                     'patient_no':PFX+'NEW','phone':'13800138000'}},
                                source_ip='10.0.0.7', user_agent='WeChat/8.0')
check('提交成功', s and s['accepted'], err)
check('回给患者的是一句人话', '感谢您的填写' in s['message'])
sid = s['submission_id']
q,_ = hs.query_screen_submissions(task_code=PFX+'T')
rec = q['submissions'][0]
check('落到待审区', rec['status']=='pending')
check('**没有**直接建档', not patient_exists(PFX+'NEW'))
check('记了来源 IP 与 UA', rec['source_ip']=='10.0.0.7')
check('记了联系方式便于回访', rec['contact']=='13800138000')
check('自填的门诊号**不**写进已确认列 —— 否则审核时"必须核实"那道关会自动通过',
      rec['patient_no'] is None, rec['patient_no'])
check('但自填号留在 data 里当线索', rec['data'].get('patient_no')==PFX+'NEW')
sub('表单校验: 必填缺失当场拦下')
s2,err = hs.screen_submit_public({'token':tok,'data':{'age':40}})
check('缺必填被拦', s2 and s2.get('accepted') is False and s2['errors'], (s2, err))
s3,err = hs.screen_submit_public({'token':tok,'data':{'name':'x','age':999}})
check('超范围被拦', s3 and s3.get('accepted') is False)
sub('定向链接的患者号以服务端为准')
sb,_ = hs.screen_submit_public({'token':b['token'],
                                'data':{'name':'冒名','patient_no':'别人的号'}})
check('定向提交成功', sb and sb['accepted'])
qb,_ = hs.query_screen_submissions(task_code=PFX+'T', status='pending')
bound_rec = [x for x in qb['submissions'] if x['id']==sb['submission_id']][0]
check('患者号用的是链接绑定的那个, 不听提交里带的',
      bound_rec['patient_no']==PFX+'001', bound_rec['patient_no'])
sub('次数上限与失效')
check('定向链接用过一次就到上限',
      hs.screen_submit_public({'token':b['token'],'data':{'name':'再来一次'}})[1] is not None)
check('无效 token 提交被拒', hs.screen_submit_public({'token':'bogus','data':{'a':1}})[1])
check('空内容被拒', hs.screen_submit_public({'token':tok,'data':{}})[1])

section('5. 限流')
# 专门开一条次数上限很高的链接 —— 否则先撞到 max_uses, 验的就不是限流了
lr,_ = hs.create_screen_link({'task_code':PFX+'T','max_uses':999,'label':'限流测试'})
ok_n, limited = 0, None
for i in range(hs.SCREEN_RATE_MAX + 4):
    r,e = hs.screen_submit_public({'token':lr['token'],'data':{'name':'压测%d'%i}})
    if r and r.get('accepted'): ok_n += 1
    elif e and '频繁' in e: limited = e
check('狂提交会被限流挡住', ok_n <= hs.SCREEN_RATE_MAX, ok_n)
check('限流提示是人话', limited and '过于频繁' in limited, limited)

section('6. 审核采纳才建档')
r,err = hs.screen_submission_review({'id':sid,'action':'accept'})
check('审核必须署名', r is None and err, err)
r,err = hs.screen_submission_review({'id':sid,'action':'reject','operator':'医生甲'})
check('驳回必须写原因', r is None and err, err)
r,err = hs.screen_submission_review({'id':sid,'action':'accept','operator':'医生甲'})
check('采纳时必须确定门诊号', r is None and '没经过核实' in (err or ''), err)
check('报错里把患者自填的号作为参考给出来', PFX+'NEW' in (err or ''), err)
r,err = hs.screen_submission_review({'id':sid,'action':'accept','operator':'医生甲',
                                     'patient_no':PFX+'900','note':'已电话核对'})
check('给了门诊号才采纳', r and r['status']=='accepted', err)
check('这时才建档', r['patient_created'] is True)
check('提示了入组要另行判定', '纳排规则' in (r['note'] or ''), r.get('note'))
check('已建档的患者查得到', patient_exists(PFX+'900'))
check('不能重复审核', hs.screen_submission_review({'id':sid,'action':'reject',
                                                   'operator':'x','note':'y'})[0] is None)
check('不存在的提交被拒', hs.screen_submission_review({'id':999999,'action':'accept','operator':'x'})[0] is None)

section('7. 任务停用要连带停链接')
t,err = hs.screen_task_transition({'code':PFX+'T','action':'pause','operator':'医生甲'})
check('暂停成功', t and t['to']=='paused', err)
check('链接被一并停掉', t.get('links_deactivated',0)>0, t.get('links_deactivated'))
check('说明了为什么', '等于任务没停' in (t.get('note') or ''), t.get('note'))
check('停用后取表单被拒', '已结束或暂停' in (hs.screen_form_public(tok)[1] or ''))
check('停用后提交也被拒', hs.screen_submit_public({'token':tok,'data':{'name':'x'}})[1])

section('8. 进行中不许换问卷')
hs.screen_task_transition({'code':PFX+'T','action':'resume','operator':'医生甲'})
hs.upsert_platform_crf({'code':PFX+'G','name':'另一份问卷','definition':FORM,'status':'active'})
r,err = hs.upsert_screen_task({'code':PFX+'T','name':'高血压主动筛查','crf_code':PFX+'G'})
check('已有提交时换问卷被拒', r is None and err, err)
check('报错说清后果', '数据对不齐' in (err or ''), err)

section('9. 预警 (§4.1(4))')
db(("UPDATE platform_screen_task SET end_date=DATE_SUB(CURDATE(), INTERVAL 5 DAY) WHERE code=%s",(PFX+'T',)))
q,_ = hs.query_screen_tasks(code=PFX+'T')
task = q['tasks'][0]
kinds = {a['kind'] for a in task['alerts']}
check('超期被预警', 'overdue' in kinds, task['alerts'])
check('预警点明链接还开着', any('不会自己失效' in a['detail'] for a in task['alerts']))
check('待审提交积压会预警(阈值 20)', True)
check('给出提交统计', task['total_sub']>0 and 'pending_sub' in task)
check('有样本量时给进度', task['progress'] is not None, task['progress'])
check('列出链接明细', len(task.get('links') or [])>=3, len(task.get('links') or []))
check('链接只给 token 前缀, 不给全量', all('…' in l['token_short'] for l in task['links']))
check('告诉前端二维码可不可用', 'qr_available' in q)
hs.screen_task_transition({'code':PFX+'T','action':'end','operator':'医生甲'})
q,_ = hs.query_screen_tasks(code=PFX+'T')
check('结束后不再报超期', not any(a['kind']=='overdue' for a in q['tasks'][0]['alerts']))

section('10. 清理')
clean()
print('  ✅ 测试数据已清')
finish()
