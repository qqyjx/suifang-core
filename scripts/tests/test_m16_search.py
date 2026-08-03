#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M16 高级检索与统计 (方案 §4.6(2))。

第一组断言全部关于**注入**。这是个让使用者自己拼查询的接口, 打的是患者库,
而且不鉴权。字段白名单和参数化是唯一的防线, 破一个口子就是全库可读。
"""
import os, sys, json, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import hs, check, section, sub, finish, ensure_all_tables, db

PFX='S16'
ensure_all_tables()
def clean():
    for t,c in (('platform_vital_daily','patient_no'),('platform_scale_response','patient_no'),
                ('platform_alarm','patient_no'),('platform_patient','patient_no')):
        db(("DELETE FROM %s WHERE %s LIKE %%s"%(t,c),(PFX+'%',)))
    db(("DELETE FROM platform_scale WHERE code=%s",(PFX+'Q',)))
clean()
today=datetime.date.today()
for no,nm,g,a,grp in [(PFX+'001','张一','M',72,'试验组'),(PFX+'002','李二','F',35,'对照组'),
                      (PFX+'003','王三','M',58,'试验组'),(PFX+'004','赵四','F',81,'对照组'),
                      (PFX+'005','钱五',None,None,None)]:
    db(("INSERT INTO platform_patient (patient_no,name,gender,age,group_tag) VALUES (%s,%s,%s,%s,%s)",(no,nm,g,a,grp)))
for no,hr in [(PFX+'001',95),(PFX+'002',62),(PFX+'003',110),(PFX+'004',58)]:
    for d in range(5):
        db(("INSERT INTO platform_vital_daily (patient_no,metric,day,value,samples) VALUES (%s,'hr',%s,%s,6)",
            (no, today-datetime.timedelta(days=d), hr)))
for no in (PFX+'001',PFX+'003'):
    db(("INSERT INTO platform_alarm (patient_no,alarm_type,severity,status,source_chain,dedup_key) "
        "VALUES (%s,'hr','warn','new','s101',%s)",(no,'test:'+no)))
DEFN={'items':[{'id':'q1','text':'情绪低落','type':'single','options':[{'label':str(v),'value':v} for v in range(4)]},
               {'id':'q2','text':'睡眠','type':'single','options':[{'label':str(v),'value':v} for v in range(4)]}],
      'scoring':{'total':{'method':'sum','items':'all'},'subscales':[],
                 'levels':[{'min':0,'max':2,'label':'正常'},{'min':3,'max':6,'label':'异常'}]}}
hs.upsert_platform_scale({'code':PFX+'Q','name':'检索测试量表','definition':DEFN})
for no,a1,a2 in [(PFX+'001',3,3),(PFX+'002',0,1),(PFX+'003',2,2)]:
    hs.submit_scale_response({'scale_code':PFX+'Q','patient_no':no,'answers':{'q1':a1,'q2':a2}})

section('1. 注入防线 (最要紧的一组)')
def bad(node):
    try: hs.build_search_sql(node); return None
    except ValueError as e: return str(e)
check('未知字段被拒', bad({'field':'p.patient_no; DROP TABLE platform_patient--','operator':'eq','value':1}))
check('伪装成合法字段的注入被拒', bad({'field':'patient.age) OR 1=1--','operator':'eq','value':1}))
check('拒绝时不提示有哪些字段(免得帮人枚举)', 'patient.' not in (bad({'field':'zzz','operator':'eq','value':1}) or ''))
check('非法运算符被拒', bad({'field':'patient.age','operator':'; DELETE FROM x --','value':1}))
check('数字字段收到 SQL 串被拒', bad({'field':'patient.age','operator':'gt','value':'1 OR 1=1'}))
sql,params=hs.build_search_sql({'field':'patient.name','operator':'contains','value':"' OR '1'='1"})
check('字符串里的引号进的是参数不是 SQL', "OR '1'='1" not in sql and "OR '1'='1" in params[0])
check('SQL 片段里只有占位符, 没有任何用户输入', sql.count('%s')==1 and "'" not in sql, sql)
r,_=hs.platform_search({'conditions':{'field':'patient.name','operator':'contains','value':"' OR '1'='1"}})
check('端到端: 注入串查不出任何人(说明它被当成了字面量)', r and r['total']==0)
check('而正常关键词查得出来', hs.platform_search({'conditions':{'field':'patient.name','operator':'contains','value':'张'}})[0]['total']==1)
sub('需要附加参数的字段, 参数本身也不能进 SQL')
sql,params=hs.build_search_sql({'field':'scale.item','operator':'gte','value':2,
                                'params':{'scale_code':"X' OR 1=1--",'item_id':"q1'--"}})
check('量表编码走参数', "X' OR 1=1--" in params and "X' OR 1=1--" not in sql)
check('题目 id 也走参数(JSON 路径用 CONCAT 拼占位符)', "q1'--" in params and "q1'--" not in sql)
check('缺附加参数时明确报错而不是静默查全库', bad({'field':'scale.total','operator':'gt','value':1}))
check('报错说清缺什么', '需要参数' in (bad({'field':'scale.total','operator':'gt','value':1}) or ''))
sub('规模上限')
deep={'op':'and','children':[{'field':'patient.age','operator':'gt','value':1}]}
for _ in range(8): deep={'op':'and','children':[deep]}
check('嵌套过深被拒', '嵌套' in (bad(deep) or ''), bad(deep))
check('条件过多被拒', '精简' in (bad({'op':'or','children':[{'field':'patient.age','operator':'eq','value':i} for i in range(60)]}) or ''))

section('2. 检索能查对')
ONLY={'field':'patient.no','operator':'contains','value':PFX}
def find(c):
    r,e=hs.platform_search({'conditions':{'op':'and','children':[ONLY,c]},'limit':100})
    assert r,e
    return sorted(p['patient_no'] for p in r['patients'])
check('年龄 > 60', find({'field':'patient.age','operator':'gt','value':60})==[PFX+'001',PFX+'004'])
check('性别 = 男', find({'field':'patient.gender','operator':'eq','value':'M'})==[PFX+'001',PFX+'003'])
check('分组 = 试验组', find({'field':'patient.group','operator':'eq','value':'试验组'})==[PFX+'001',PFX+'003'])
check('分组为空', find({'field':'patient.group','operator':'empty'})==[PFX+'005'])
check('AND 组合: 男 且 >60', find({'op':'and','children':[{'field':'patient.gender','operator':'eq','value':'M'},{'field':'patient.age','operator':'gt','value':60}]})==[PFX+'001'])
check('OR 组合: >75 或 <40', find({'op':'or','children':[{'field':'patient.age','operator':'gt','value':75},{'field':'patient.age','operator':'lt','value':40}]})==[PFX+'002',PFX+'004'])
check('嵌套: (男 且 试验组) 或 年龄>80',
      find({'op':'or','children':[{'op':'and','children':[{'field':'patient.gender','operator':'eq','value':'M'},{'field':'patient.group','operator':'eq','value':'试验组'}]},{'field':'patient.age','operator':'gt','value':80}]})==[PFX+'001',PFX+'003',PFX+'004'])
check('between', find({'field':'patient.age','operator':'between','value':[50,75]})==[PFX+'001',PFX+'003'])
check('in', find({'field':'patient.no','operator':'in','value':[PFX+'002',PFX+'004']})==[PFX+'002',PFX+'004'])
sub('指标阈值 (方案点名的能力)')
check('心率日均 > 90', find({'field':'vital.hr','operator':'gt','value':90})==[PFX+'001',PFX+'003'])
check('心率日均 < 65', find({'field':'vital.hr','operator':'lt','value':65})==[PFX+'002',PFX+'004'])
check('体征条件能和患者属性组合',
      find({'op':'and','children':[{'field':'vital.hr','operator':'gt','value':90},{'field':'patient.age','operator':'gt','value':60}]})==[PFX+'001'])
sub('量表')
check('量表总分 >= 5', find({'field':'scale.total','operator':'gte','value':5,'params':{'scale_code':PFX+'Q'}})==[PFX+'001'])
check('量表分级 = 异常', find({'field':'scale.level','operator':'eq','value':'异常','params':{'scale_code':PFX+'Q'}})==[PFX+'001',PFX+'003'])
check('量表单题答案 >= 3', find({'field':'scale.item','operator':'gte','value':3,'params':{'scale_code':PFX+'Q','item_id':'q1'}})==[PFX+'001'])
check('未评估的人不会被误纳', PFX+'005' not in find({'field':'scale.total','operator':'gte','value':0,'params':{'scale_code':PFX+'Q'}}))
sub('预警')
check('未处理预警 >= 1', find({'field':'alarm.open','operator':'gte','value':1})==[PFX+'001',PFX+'003'])
sub('无条件')
r,_=hs.platform_search({})
check('不给条件时返回全部', r['total']>=5)
check('限定前缀后正好 5 人', hs.platform_search({'conditions':ONLY})[0]['total']==5)
check('结果带汇总列(预警数/量表数/CRF数)', all('open_alarms' in p and 'scale_n' in p and 'crf_n' in p for p in r['patients']))
r,_=hs.platform_search({'limit':2})
check('分页生效', len(r['patients'])==2 and r['total']>=5)
check('limit 被夹住', hs.platform_search({'limit':99999})[0]['limit']<=1000)

section('3. 统计')
st,err=hs.platform_stats({'dims':['gender','age_band','group_tag'],'conditions':ONLY})
check('多维统计一次出', st and len(st['charts'])==3, err)
g=[c for c in st['charts'] if c['dim']=='gender'][0]
check('性别分布正确', sorted((d['label'],d['value']) for d in g['data'])==[('女',2),('未填',1),('男',2)], g['data'])
check('图表类型由规则给(分类->饼图)', g['chart']=='pie')
ab=[c for c in st['charts'] if c['dim']=='age_band'][0]
check('年龄段分桶', {d['label'] for d in ab['data']}=={'40-59','60-74','75+','18-39','未填'})
check('分桶用柱图', ab['chart']=='bar')
st2,_=hs.platform_stats({'dims':['gender'],'conditions':{'op':'and','children':[ONLY,{'field':'patient.age','operator':'gt','value':60}]}})
check('统计跟着检索条件走', st2['matched_patients']==2)
check('筛后的分布也对', sorted((d['label'],d['value']) for d in st2['charts'][0]['data'])==[('女',1),('男',1)])
st3,err=hs.platform_stats({'dims':['scale_level'],'params':{'scale_code':PFX+'Q'},'conditions':ONLY})
check('量表分级占比', st3 and {d['label'] for d in st3['charts'][0]['data']}=={'正常','异常','未评估'}, err)
st4,err=hs.platform_stats({'dims':['scale_level']})
check('缺参数的维度明确报错', st4 is None and 'scale_code' in (err or ''), err)
check('未知维度被拒', hs.platform_stats({'dims':['bogus']})[0] is None)
check('维度过多被拒', hs.platform_stats({'dims':['a']*9})[0] is None)
check('随访完成情况维度可用', hs.platform_stats({'dims':['followup'],'conditions':ONLY})[0]['charts'][0]['data'])

section('4. 字段目录')
cat=hs.search_field_catalog()
check('字段目录交给前端', cat['ok'] and len(cat['fields'])>=15, len(cat['fields']))
check('每个字段带允许的运算符', all(f['ops'] for f in cat['fields']))
check('需要附加参数的字段标出来了', any(f['needs'] for f in cat['fields'] if f['field'].startswith('scale.')))
check('字段分了组便于前端展示', len({f['group'] for f in cat['fields']})>=4)
check('统计维度也给了', len(cat['dims'])>=6)
check('给出了规模上限供前端提示', cat['limits']['max_nodes']>0)

section('5. 清理')
clean()
print('  ✅ 测试数据已清')
finish()
