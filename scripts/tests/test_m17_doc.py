#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M17 知情同意与项目资料 (方案 §2.4)。

三组重点:
  1. 上传的文件名一个字都不该落到磁盘上 —— 路径穿越/超长名/同名覆盖一次性都没了
  2. 签署钉的是**内容哈希**而不是文件 id —— 否则换了 PDF, 签名就"覆盖"了不同内容
  3. 接口和界面都必须说清: 这不是《电子签名法》意义上的可靠电子签名
"""
import os, sys, base64, hashlib, tempfile, shutil
TMP = tempfile.mkdtemp(prefix='suifang_m17_')
os.environ['PLATFORM_DOC_DIR'] = TMP
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import hs, check, section, sub, finish, ensure_all_tables, db

PFX='T17'
ensure_all_tables()
def clean():
    db(("DELETE FROM platform_document_log WHERE doc_code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_document_log WHERE doc_code LIKE 'DOC%'",),
       ("DELETE FROM platform_consent WHERE doc_code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_document WHERE code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_document WHERE code LIKE 'DOC%'",),
       ("DELETE FROM platform_patient WHERE patient_no LIKE %s",(PFX+'%',)))
clean()
db(("INSERT INTO platform_patient (patient_no,name) VALUES (%s,'知情测试患者')",(PFX+'001',)))
PDF1='%PDF-1.4\n知情同意书 v1 正文\n%%EOF'.encode('utf-8')
PDF2='%PDF-1.4\n知情同意书 v2 正文(改过)\n%%EOF'.encode('utf-8')
b64=lambda raw: base64.b64encode(raw).decode()

section('1. 上传与文件名安全')
r,err=hs.upload_document({'code':PFX+'C','title':'研究知情同意书','doc_type':'consent',
                          'filename':'知情同意书.pdf','content_base64':b64(PDF1),'uploader':'医生甲'})
check('上传成功', r and r['version']=='1', err)
check('哈希是内容的 sha256', r['sha256']==hashlib.sha256(PDF1).hexdigest())
check('磁盘上的名字由服务端生成, 不含原文件名',
      '知情同意书' not in r['stored_name'] and r['stored_name'].startswith(PFX+'C_1_'), r['stored_name'])
check('文件确实写到磁盘了', os.path.isfile(os.path.join(TMP, r['stored_name'])))
sub('恶意文件名')
for bad_name,why in [('../../../etc/passwd.pdf','路径穿越'),('..\\..\\windows\\x.pdf','反斜杠穿越'),
                     ('a'*400+'.pdf','超长文件名'),('x\x00.pdf','空字节')]:
    rr,ee=hs.upload_document({'title':'t','doc_type':'other','filename':bad_name,'content_base64':b64(b'x'*10)})
    check('%s 的文件名不影响磁盘落点'%why,
          rr and '/' not in rr['stored_name'] and '\\' not in rr['stored_name'] and len(rr['stored_name'])<80,
          (rr or {}).get('stored_name') or ee)
check('目录里没有多出奇怪的文件', all('..' not in f and '/' not in f for f in os.listdir(TMP)))
check('没写到目录外', not os.path.exists('/etc/passwd.pdf'))
sub('扩展名白名单')
for ext,should in [('pdf',True),('docx',True),('png',True),('html',False),('svg',False),
                   ('js',False),('exe',False),('',False)]:
    rr,ee=hs.upload_document({'title':'t','doc_type':'other',
                              'filename':('f.'+ext) if ext else 'noext','content_base64':b64(b'x'*10)})
    check('.%s %s'%(ext or '(无扩展名)','接受' if should else '拒绝'), bool(rr)==should, ee if not rr else rr['ext'])
_,e_html=hs.upload_document({'title':'t','doc_type':'other','filename':'x.html','content_base64':b64(b'x')})
check('拒绝时说清了为什么排除 html/svg', 'html/svg' in (e_html or ''))
rr,ee=hs.upload_document({'title':'t','doc_type':'other','filename':'big.pdf','content_base64':b64(b'x'*(31*1024*1024))})
check('超过 30MB 被拒', rr is None and 'MB' in (ee or ''), ee)
check('空文件被拒', hs.upload_document({'title':'t','doc_type':'other','filename':'e.pdf','content_base64':''})[0] is None)
check('未知资料类型被拒', hs.upload_document({'title':'t','doc_type':'bogus','filename':'e.pdf','content_base64':b64(b'x')})[0] is None)

section('2. 版本管理')
r2,err=hs.upload_document({'code':PFX+'C','title':'研究知情同意书(修订)','doc_type':'consent',
                           'filename':'知情同意书v2.pdf','content_base64':b64(PDF2),'uploader':'医生甲'})
check('同 code 再传自动成 v2', r2 and r2['version']=='2', err)
q,_=hs.query_documents(code=PFX+'C')
byv={d['version']:d for d in q['documents']}
check('两版并存', set(byv)=={'1','2'})
check('旧版自动置 superseded', byv['1']['status']=='superseded')
check('新版是 active', byv['2']['status']=='active')
check('旧版文件没被删(伦理批件要留档)', os.path.isfile(os.path.join(TMP, r['stored_name'])))
check('两版哈希不同', byv['1']['sha256']!=byv['2']['sha256'])
rr,ee=hs.upload_document({'code':PFX+'C','version':'2','title':'x','doc_type':'consent',
                          'filename':'a.pdf','content_base64':b64(b'y')})
check('显式指定已存在的版本被拒', rr is None and '已存在' in (ee or ''), ee)

section('3. 下载与完整性')
raw,meta,err=hs.fetch_document_bytes(PFX+'C','1')
check('按版本取回 v1 原始字节', raw==PDF1, err)
check('不给版本时取最新一版', hs.fetch_document_bytes(PFX+'C')[0]==PDF2)
check('返回的元信息带原始文件名供下载显示', meta['orig_name']=='知情同意书.pdf')
check('不存在的资料给明确报错', '不存在' in (hs.fetch_document_bytes('NOPE')[2] or ''))
sub('磁盘文件被换掉必须发现')
victim=os.path.join(TMP, r['stored_name'])
open(victim,'wb').write('%PDF-1.4\n有人把文件换掉了\n%%EOF'.encode('utf-8'))
raw3,_,e_tamper=hs.fetch_document_bytes(PFX+'C','1')
check('哈希对不上时拒绝下发', raw3 is None and e_tamper, e_tamper)
check('报错说清了是被替换或损坏', '替换' in (e_tamper or ''))
open(victim,'wb').write(PDF1)
check('复原后又能下发', hs.fetch_document_bytes(PFX+'C','1')[0]==PDF1)

section('4. 签署')
SIG='data:image/png;base64,'+base64.b64encode(b'\x89PNG fake').decode()
c,err=hs.sign_consent({'doc_code':PFX+'C','patient_no':PFX+'001','signer_name':'张三',
                       'signer_role':'patient','signature_png':SIG},
                      source_ip='10.0.0.9', user_agent='TestUA/1.0')
check('签署成功', c and c.get('signed'), err)
check('签的是当前 active 版本 v2', c['doc_version']=='2')
check('钉住了内容哈希', c['doc_sha256']==hashlib.sha256(PDF2).hexdigest())
check('返回里就带免责声明', '不是《电子签名法》' in c['disclaimer'])
check('免责声明说清了缺什么(CA)', 'CA' in c['disclaimer'])
sub('参数校验')
for body,why in [({'doc_code':PFX+'C','patient_no':PFX+'001'},'缺签署人'),
                 ({'doc_code':PFX+'C','signer_name':'x'},'缺门诊号'),
                 ({'patient_no':PFX+'001','signer_name':'x'},'缺文件编码')]:
    check('%s 被拒'%why, hs.sign_consent(body)[0] is None)
check('未知签署人身份被拒', hs.sign_consent({'doc_code':PFX+'C','patient_no':PFX+'001','signer_name':'x','signer_role':'boss'})[0] is None)
check('签名图必须是 png data URI(挡掉塞 svg)',
      hs.sign_consent({'doc_code':PFX+'C','patient_no':PFX+'001','signer_name':'x','signature_png':'<svg onload=alert(1)>'})[0] is None)
sub('只有知情同意书能签')
hs.upload_document({'code':PFX+'P','title':'研究方案','doc_type':'protocol','filename':'p.pdf','content_base64':b64(b'protocol')})
cc,ee=hs.sign_consent({'doc_code':PFX+'P','patient_no':PFX+'001','signer_name':'张三'})
check('给研究方案签名被拒', cc is None and ee, ee)
check('并解释了为什么', '不构成任何东西' in (ee or ''))
sub('重复签署')
cc,_=hs.sign_consent({'doc_code':PFX+'C','patient_no':PFX+'001','signer_name':'张三'})
check('同人同版同身份再签被拦', cc and cc.get('signed') is False)
check('拦住时指出已有哪条记录', cc and cc.get('existing_id'))
check('确认后可重签', hs.sign_consent({'doc_code':PFX+'C','patient_no':PFX+'001','signer_name':'张三','allow_resign':True})[0].get('signed'))
check('换个身份(监护人)可以另签一份',
      hs.sign_consent({'doc_code':PFX+'C','patient_no':PFX+'001','signer_name':'张父','signer_role':'guardian'})[0].get('signed'))

section('5. 查询与完整性告警')
q,err=hs.query_consents(patient_no=PFX+'001')
check('签署记录查得到', q and q['count']>=3, err)
rec=q['consents'][0]
check('带签署人身份中文名', rec['signer_role_label'] in hs.CONSENT_SIGNER_ROLES.values())
check('记了来源 IP', any(x['source_ip']=='10.0.0.9' for x in q['consents']))
check('列表默认不带签名图(几十KB base64)', 'signature_png' not in rec)
check('要了才给签名图', 'signature_png' in hs.query_consents(patient_no=PFX+'001', with_signature=True)[0]['consents'][0])
check('查询结果也带免责声明', '不是《电子签名法》' in q['disclaimer'])
check('当前哈希与签署时一致时不告警', all(x['hash_matches'] and 'integrity_warning' not in x for x in q['consents']))
sub('签完之后文件被换掉: 必须标出来')
db(("UPDATE platform_document SET sha256=%s WHERE code=%s AND version='2'",('0'*64, PFX+'C')))
q3,_=hs.query_consents(patient_no=PFX+'001')
bad=[x for x in q3['consents'] if x['doc_version']=='2']
check('哈希对不上的记录被标出', bad and not bad[0]['hash_matches'])
check('告警说清这份签名已不能证明什么', bad and '不能证明' in bad[0]['integrity_warning'])
db(("UPDATE platform_document SET sha256=%s WHERE code=%s AND version='2'",(hashlib.sha256(PDF2).hexdigest(), PFX+'C')))

section('6. 撤回')
cid=q['consents'][0]['id']
check('撤回必须写原因', 'reason' in (hs.revoke_consent({'id':cid,'operator':'主任乙'})[1] or ''))
check('写了原因可撤回', hs.revoke_consent({'id':cid,'operator':'主任乙','reason':'受试者退出研究'})[0]['status']=='revoked')
check('不能重复撤回', hs.revoke_consent({'id':cid,'operator':'x','reason':'y'})[0] is None)
q4,_=hs.query_consents(patient_no=PFX+'001', status='revoked')
check('撤回记录仍在库里(不删)', q4['count']>=1)
check('记了撤回人和原因', q4['consents'][0]['revoke_reason']=='受试者退出研究')

section('7. 资料列表与留痕')
q,_=hs.query_documents(doc_type='consent')
check('按类型筛', all(d['doc_type']=='consent' for d in q['documents']))
check('带中文类型名', q['documents'][0]['doc_type_label']=='知情同意书')
check('列表给出已签署人数', any(d['signed_count']>0 for d in q['documents']))
check('列表不含文件内容', 'content' not in q['documents'][0] and 'stored_name' not in q['documents'][0])
check('给出了白名单与大小上限供前端提示', q['allowed_ext'] and q['max_mb']==30)
q,_=hs.query_documents(code=PFX+'C', with_log=True)
log=q['documents'][0].get('log') or []
check('上传/新版/签署/撤回都留了痕',
      {'upload','new_version','sign','revoke'} <= {e['action'] for e in log}, sorted({e['action'] for e in log}))
check('签署留痕里写明了内容哈希', any('哈希' in (e['detail'] or '') for e in log))

section('8. 清理')
clean()
shutil.rmtree(TMP, ignore_errors=True)
print('  ✅ 测试数据与临时目录已清')
finish()
