#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M21 导出增强 (方案 §4.6(1))。

三组重点:
  1. **勾了加密而库不在时, 必须拒绝产出文件** —— 给一份明文冒充加密件,
     比不提供这个选项危险得多
  2. **不声称 CDISC 合规** —— 产出的是 SDTM 风格, 说成合规在审计时要出事
  3. **导出记录本身比导出功能更重要** —— 每一次导出都是一次患者数据出境
"""
import os, sys, json, time, tempfile, shutil, datetime
TMP = tempfile.mkdtemp(prefix='suifang_export_')
os.environ['PLATFORM_EXPORT_DIR'] = TMP
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import hs, check, section, sub, finish, ensure_all_tables, db

PFX='T21'
ensure_all_tables(); hs.ensure_platform_export_tables()
def clean():
    db(("DELETE FROM platform_export_download WHERE job_no IN (SELECT job_no FROM platform_export_job WHERE requested_by=%s)",(PFX,)),
       ("DELETE FROM platform_export_job WHERE requested_by=%s",(PFX,)),
       ("DELETE FROM platform_scale_response WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_crf_response WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_vital_daily WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_scale WHERE code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_patient WHERE patient_no LIKE %s",(PFX+'%',)))
clean()
today = datetime.date.today()
for i,(nm,g,a,grp) in enumerate([('导出甲','M',60,'试验组'),('导出乙','F',45,'对照组'),
                                 ('导出丙','M',70,'试验组')],1):
    no='%s%03d'%(PFX,i)
    db(("INSERT INTO platform_patient (patient_no,name,gender,age,group_tag) VALUES (%s,%s,%s,%s,%s)",
        (no,nm,g,a,grp)))
    for d in range(3):
        db(("INSERT INTO platform_vital_daily (patient_no,metric,day,value,samples) VALUES (%s,'hr',%s,%s,6)",
            (no, today-datetime.timedelta(days=d), 70+i*5)))
DEFN={'items':[{'id':'q1','text':'情绪','type':'single','options':[{'label':str(v),'value':v} for v in range(4)]},
               {'id':'q2','text':'睡眠','type':'single','options':[{'label':str(v),'value':v} for v in range(4)]}],
      'scoring':{'total':{'method':'sum','items':'all'},'subscales':[],'levels':[]}}
hs.upsert_platform_scale({'code':PFX+'S','name':'导出测试量表','definition':DEFN})
# 甲填 3 次(用来验历史挑选), 乙丙各 1 次
for i,(no,times) in enumerate([(PFX+'001',3),(PFX+'002',1),(PFX+'003',1)]):
    for t in range(times):
        hs.submit_scale_response({'scale_code':PFX+'S','patient_no':no,
                                  'answers':{'q1':t%4,'q2':(t+1)%4}})
print('  测试数据: 3 患者 / 心率日聚合 / 量表填报(甲 3 次)')
ONLY={'field':'patient.no','operator':'contains','value':PFX}

section('1. 变量挑选: 三种模式三种形状 (§4.6(1))')
sub('横向: 一行一患者, 一列一变量')
data,meta,err = hs.export_variable_pick({'mode':'horizontal','conditions':ONLY,
    'variables':[{'source':'patient','field':'age','label':'年龄'},
                 {'source':'scale','code':PFX+'S','field':'__total__','label':'量表总分'},
                 {'source':'scale','code':PFX+'S','field':'q1','label':'第1题'}]})
check('横向导出跑通', data and not err, err)
txt = data.decode('utf-8-sig')
# CSV 末尾有换行(RFC 4180 / Excel 惯例), 拆出来最后会多一个空元素
lines = [l for l in txt.split('\r\n') if l]
check('一行表头 + 3 行患者', len(lines)==4, len(lines))
check('表头含挑的变量', '年龄' in lines[0] and '量表总分' in lines[0], lines[0])
check('一行一患者', all(('%s00'%PFX) in l for l in lines[1:]))
check('说明了多次记录取最近一次', '最近一次' in meta['note'], meta['note'])
sub('纵向: 带分组列, 用来比组间差异')
data,meta,err = hs.export_variable_pick({'mode':'vertical','conditions':ONLY,
    'variables':[{'source':'scale','code':PFX+'S','field':'__total__','label':'总分'}]})
check('纵向导出跑通', data and not err, err)
check('含分组列', '分组' in data.decode('utf-8-sig').split('\r\n')[0])
sub('历史: 一列一次随访, 看变化')
data,meta,err = hs.export_variable_pick({'mode':'history','conditions':ONLY,
    'variables':[{'source':'scale','code':PFX+'S','field':'__total__','label':'总分'}]})
check('历史导出跑通', data and not err, err)
h = data.decode('utf-8-sig').split('\r\n')[0]
check('列数按最多次数对齐(甲有 3 次)', '第3次' in h, h)
check('还给了每次的日期列', '第1次日期' in h)
check('说明了空白是什么意思', '该次没有记录' in meta['note'], meta['note'])
check('历史模式只接受 1 个变量',
      hs.export_variable_pick({'mode':'history','conditions':ONLY,
          'variables':[{'source':'patient','field':'age'},{'source':'patient','field':'name'}]})[2] is not None)
sub('参数校验')
check('未知模式被拒', hs.export_variable_pick({'mode':'bogus','variables':[{'source':'patient','field':'age'}]})[2])
check('空变量被拒', hs.export_variable_pick({'mode':'horizontal','variables':[]})[2])
check('未知来源被拒', hs.export_variable_pick({'mode':'horizontal','variables':[{'source':'sql'}]})[2])
check('量表变量不给 code 被拒',
      hs.export_variable_pick({'mode':'horizontal','variables':[{'source':'scale','field':'q1'}]})[2])
check('筛选条件里的注入被拒(复用 M16 白名单)',
      hs.export_variable_pick({'mode':'horizontal','conditions':{'field':'x) OR 1=1--','operator':'eq','value':1},
                               'variables':[{'source':'patient','field':'age'}]})[2])
check('命中 0 人时给明确提示',
      '没有命中' in (hs.export_variable_pick({'mode':'horizontal',
          'conditions':{'field':'patient.no','operator':'eq','value':'NOBODY'},
          'variables':[{'source':'patient','field':'age'}]})[2] or ''))

section('2. SDTM 风格导出: 不声称 CDISC 合规')
files,meta,err = hs.export_sdtm_like({'study_id':'DEMO01','conditions':ONLY})
check('SDTM 导出跑通', files and not err, err)
check('四个域都在', set(files) >= {'dm.csv','vs.csv','qs.csv','sv.csv'}, list(files))
check('附了 README', 'README.txt' in files)
readme = files['README.txt'].decode('utf-8')
check('README 明说不是 CDISC 合规件', '不是 CDISC 合规' in readme, readme[:60])
check('说清还差什么(define.xml/受控术语)', 'define.xml' in readme and '受控术语' in readme)
check('说清了它有什么用', '少做很多整理' in readme)
check('返回的 meta 里也带这句', '不是 CDISC 合规' in meta['disclaimer'])
dm = files['dm.csv'].decode('utf-8-sig').split('\r\n')
check('DM 用 SDTM 标准列名', 'USUBJID' in dm[0] and 'STUDYID' in dm[0] and 'DOMAIN' in dm[0], dm[0])
check('USUBJID 带研究编号前缀', 'DEMO01-'+PFX in dm[1], dm[1][:60])
vs = files['vs.csv'].decode('utf-8-sig').split('\r\n')
check('VS 把心率映射成 SDTM 测试码', 'HR' in vs[1] and 'Heart Rate' in vs[1], vs[1][:80])
qs = files['qs.csv'].decode('utf-8-sig')
check('QS 含逐题记录与总分', 'Total Score' in qs)
check('未知域被拒', hs.export_sdtm_like({'domains':['ZZ']})[2])
check('命中 0 人时给明确提示',
      '没有命中' in (hs.export_sdtm_like({'conditions':{'field':'patient.no','operator':'eq','value':'NOBODY'}})[2] or ''))

section('3. 加密: 要么真加密, 要么不给文件 (最关键)')
avail = hs._has_pyzipper()
print('     pyzipper 可用:', avail)
blob,err = hs._pack_zip({'a.txt': b'hello'}, password='secret123')
if avail:
    check('装了库时真的加密了', blob and not err, err)
    import io, zipfile as zf
    try:
        zf.ZipFile(io.BytesIO(blob)).read('a.txt')
        check('不给口令读不出来', False, '居然读出来了')
    except Exception:
        check('不给口令读不出来', True)
else:
    check('**没装库时拒绝产出文件**', blob is None and err, err)
    check('报错说清了为什么拒绝', '比不提供这个选项危险得多' in (err or ''), err)
    check('报错给了装法', 'pip install pyzipper' in (err or ''))
    check('并说明标准库那种 ZipCrypto 保护不了患者数据', '秒破' in (err or ''), err)
    r,e = hs.export_job_create({'kind':'pick','encrypt':True,'password':'12345678',
                                'params':{'mode':'horizontal','conditions':ONLY,
                                          'variables':[{'source':'patient','field':'age'}]}})
    check('建加密任务同样被拒(不会先建了再失败)', r is None and e, e)
blob,err = hs._pack_zip({'a.txt': b'hello'})
check('不加密时正常打包', blob and not err, err)

section('4. 异步导出任务')
r,err = hs.export_job_create({'kind':'pick','requested_by':PFX,
    'params':{'mode':'horizontal','conditions':ONLY,
              'variables':[{'source':'patient','field':'age','label':'年龄'},
                           {'source':'scale','code':PFX+'S','field':'__total__','label':'总分'}]}})
check('建任务成功', r and r['job_no'], err)
check('说明了为什么转后台', '整个服务只能排队' in r['note'], r['note'][:60])
job = r['job_no']
for _ in range(40):
    q,_e = hs.export_job_query(job_no=job)
    if q['jobs'][0]['status'] in ('done','failed'): break
    time.sleep(0.3)
j = q['jobs'][0]
check('任务跑完', j['status']=='done', (j['status'], j.get('error')))
check('记了患者数与行数', j['patient_count']==3 and j['row_count']==3, (j['patient_count'], j['row_count']))
check('记了文件大小与哈希', j['size_bytes']>0 and len(j['sha256'])==64)
check('记了导出条件(事后能说清导的是谁)', j['params'].get('mode')=='horizontal', j['params'])
check('记了申请人', j['requested_by']==PFX)
check('给了过期时间', j['expires_at'])
sub('下载与留痕')
blob,meta,err = hs.export_job_fetch(job, operator='医生甲', source_ip='10.0.0.5')
check('下载得到文件', blob and not err, err)
import io, zipfile as zf
z = zf.ZipFile(io.BytesIO(blob))
check('是个 zip 且内含 CSV', any(n.endswith('.csv') for n in z.namelist()), z.namelist())
check('附了说明文件', 'README.txt' in z.namelist())
q,_e = hs.export_job_query(job_no=job)
check('下载次数 +1', q['jobs'][0]['download_count']==1)
check('下载留痕记了谁和从哪', q['jobs'][0]['downloads'][0]['operator']=='医生甲'
      and q['jobs'][0]['downloads'][0]['source_ip']=='10.0.0.5', q['jobs'][0]['downloads'][:1])
sub('完整性与过期')
path = os.path.join(TMP, job + '.zip')
open(path,'wb').write(b'tampered')
b2,_m,e2 = hs.export_job_fetch(job)
check('文件被换掉时拒绝下发', b2 is None and e2, e2)
check('报错说清是被替换或损坏', '替换' in (e2 or ''))
open(path,'wb').write(blob)
db(("UPDATE platform_export_job SET expires_at=DATE_SUB(NOW(), INTERVAL 1 HOUR) WHERE job_no=%s",(job,)))
b3,_m,e3 = hs.export_job_fetch(job)
check('过期后拒绝下载并说明', b3 is None and '过期' in (e3 or ''), e3)
q,_e = hs.export_job_query()
check('过期任务在列表里被标出', any(x['job_no']==job and x['status']=='expired' for x in q['jobs']))
check('不存在的任务被拒', hs.export_job_fetch('NOPE')[2])

section('5. 失败的任务不会永远卡在 running')
r,_ = hs.export_job_create({'kind':'pick','requested_by':PFX,
                            'params':{'mode':'horizontal','variables':[{'source':'scale','field':'q1'}]}})
for _ in range(40):
    q,_e = hs.export_job_query(job_no=r['job_no'])
    if q['jobs'][0]['status'] in ('done','failed'): break
    time.sleep(0.3)
check('参数有问题的任务落到 failed 而不是卡住', q['jobs'][0]['status']=='failed', q['jobs'][0]['status'])
check('失败原因写回了任务', q['jobs'][0]['error'], q['jobs'][0]['error'])
check('未知 kind 被拒', hs.export_job_create({'kind':'bogus'})[0] is None)

section('6. 导出记录是这块最要紧的产出')
q,_e = hs.export_job_query()
check('记录里能看到每次导出的条件', all('params' in x for x in q['jobs']))
check('列表带中文状态', q['jobs'][0]['status_label'] in hs.EXPORT_JOB_STATUSES.values())
check('告诉前端加密可不可用', 'encryption_available' in q)
if not avail:
    check('不可用时给出原因', q['encryption_note'] and 'pyzipper' in q['encryption_note'])
check('给出保留时长', q['keep_hours']==48)
check('给出 SDTM 免责说明', '不是 CDISC 合规' in q['sdtm_disclaimer'])

section('7. 清理')
clean()
shutil.rmtree(TMP, ignore_errors=True)
print('  ✅ 测试数据与临时目录已清')
finish()
