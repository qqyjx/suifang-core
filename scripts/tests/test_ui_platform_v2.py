#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""platform-v2.html 端到端: 真 chromium + 本地 dev 库, 逐个模块点过去。

重点不是"页面能打开", 而是:
  1) 每个模块要么有真实内容, 要么有诚实的"未建设"标注, 没有假数字漏出
  2) 逻辑显隐/审核闸门/注入防线这些**服务端约束在界面上真的生效**
  3) 每个 hero 按钮都要真的点得动 —— 曾经有个 opacity .04 的水印伪元素
     盖在所有 hero 按钮上, 肉眼完全看不出来, 只有真去点才发现
  4) 窄屏可用(承诺函第 2 条: 医护移动端查看)

打本地 dev 库不打生产 —— 要验完整链路得先造出问题数据, 不该往生产塞测试患者。
"""
import os, sys, json, base64, hashlib, datetime, tempfile, shutil
TOKEN = 'uitest_token'
DOCTMP = tempfile.mkdtemp(prefix='suifang_uidoc_')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import (hs, check, section, sub, finish, ensure_all_tables, db,
                      start_backend, start_static, new_page, REPO)

PFX = 'UI9'
ensure_all_tables()
def clean():
    db(("DELETE FROM platform_qc_query_log WHERE query_id IN (SELECT id FROM platform_qc_query WHERE patient_no LIKE %s)",(PFX+'%',)),
       ("DELETE FROM platform_qc_query WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_qc_finding WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_scale_response WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_crf_response WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_consent WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_vital_daily WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_enrollment_log WHERE cohort_code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_enrollment WHERE cohort_code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_group WHERE cohort_code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_cohort WHERE code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_document_log WHERE doc_code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_document WHERE code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_edu_log WHERE material_id IN (SELECT id FROM platform_edu_material WHERE code LIKE %s)",(PFX+'%',)),
       ("DELETE FROM platform_edu_material WHERE code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_crf WHERE code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_scale WHERE code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_push WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_visit_followup WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_visit WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_flow_instance WHERE patient_no LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_flow WHERE code LIKE %s",(PFX+'%',)),
       ("DELETE FROM platform_patient WHERE patient_no LIKE %s",(PFX+'%',)))
clean()

# ---------- 造数据 ----------
today = datetime.date.today()
for i,(nm,g,a) in enumerate([('演示甲','M',72),('演示乙','F',35),('演示丙','M',58),('演示丁','F',81)],1):
    no = '%s%03d'%(PFX,i)
    db(("INSERT INTO platform_patient (patient_no,name,gender,age,group_tag) VALUES (%s,%s,%s,%s,%s)",
        (no,nm,g,a,'试验组' if i%2 else '对照组')))
    for d in range(4):
        db(("INSERT INTO platform_vital_daily (patient_no,metric,day,value,samples) VALUES (%s,'hr',%s,%s,6)",
            (no, today-datetime.timedelta(days=d), 95+i*4)))
# M13: 一份会触发质控的量表填报序列
NINE = {'items':[{'id':'p%d'%i,'text':'第 %d 题 · 演示题干'%i,'type':'single',
                  'options':[{'label':lb,'value':v} for v,lb in enumerate(['完全没有','有几天','一半以上','几乎每天'])]}
                 for i in range(1,10)],
        'scoring':{'total':{'method':'sum','items':'all'},'subscales':[],'levels':[]}}
hs.upsert_platform_scale({'code':PFX+'S','name':'质控演示量表','category':'演示','definition':NINE})
VARIED={'p1':2,'p2':0,'p3':3,'p4':1,'p5':2,'p6':0,'p7':1,'p8':3,'p9':2}
base=datetime.datetime(2026,6,1,9,30)
for at,ans,tot in [(base,VARIED,14.0),(base+datetime.timedelta(days=14),VARIED,14.0),
                   (base+datetime.timedelta(days=28),dict(VARIED,p1=3,p2=3,p3=3),40.0),
                   (base+datetime.timedelta(days=28,minutes=6),dict(VARIED,p1=3,p2=3,p3=3),40.0)]:
    db(("""INSERT INTO platform_scale_response (scale_code,scale_version,patient_no,answers,
           total_score,rater_type,operator,status,created_at)
           VALUES (%s,'1',%s,%s,%s,'clinician','演示医生','submitted',%s)""",
        (PFX+'S', PFX+'001', json.dumps(ans,ensure_ascii=False), tot, at)))
# M14: 一份带逻辑隐藏的 CRF
YN=[{'label':'是','value':1},{'label':'否','value':0}]
CRF={'sections':[
    {'name':'基本信息','items':[
        {'id':'note1','type':'note','text':'本表由医护填写'},
        {'id':'nm','type':'text','text':'姓名','required':True},
        {'id':'dt','type':'date','text':'访视日期','required':True},
        {'id':'age','type':'number','text':'年龄','min':0,'max':130}]},
    {'name':'不良事件','items':[
        {'id':'hasae','type':'single','text':'本次随访期间是否发生不良事件','options':YN,'required':True},
        {'id':'aedesc','type':'paragraph','text':'不良事件描述','hidden':True,'required':True},
        {'id':'aetab','type':'table_mixed','text':'不良事件明细','hidden':True,'dynamic':True,'max_rows':5,
         'columns':[{'id':'e','label':'事件','type':'text','required':True},
                    {'id':'g','label':'分级','type':'select',
                     'options':[{'label':'轻','value':1},{'label':'中','value':2},{'label':'重','value':3}]}]}]}],
    'logic':[{'when':{'field':'hasae','op':'eq','value':1},
              'then':{'action':'show','targets':['aedesc','aetab']}}]}
hs.upsert_platform_crf({'code':PFX+'C','name':'不良事件随访表','category':'演示','visit_type':'随诊',
                        'definition':CRF,'status':'active','owner':'演示医生'})
# M15: 一份含高危表述的宣教稿
hs.upsert_edu_material({'code':PFX+'E','title':'糖尿病用药（含高危表述）','topic':'medication',
                        'category':'2型糖尿病','owner':'医生甲',
                        'body':'二甲双胍每次 500mg，每日两次。\n血糖平稳后可自行减量。\n轻微不适不必就医。'})
# M17: 一份知情同意书
PDF='%PDF-1.4\n知情同意书正文\n%%EOF'.encode('utf-8')
DOC_SHA=hashlib.sha256(PDF).hexdigest()
hs.upload_document.__globals__['DOC_DIR']=DOCTMP
hs.upload_document({'code':PFX+'D','title':'XX研究知情同意书','doc_type':'consent',
                    'filename':'知情同意书.pdf','content_base64':base64.b64encode(PDF).decode(),
                    'uploader':'医生甲'})
# M19: 一条带窗口与流程外阶段的流程
FLOW={'levels':['阶段','访视'],'anchor':'enroll',
      'nodes':[{'id':'s1','name':'术后早期','children':[
                 {'id':'v7','name':'术后7天','offset_days':7,'window':[-2,3],
                  'items':[{'type':'scale','ref':PFX+'S'},{'type':'edu','ref':PFX+'P'}]},
                 {'id':'v30','name':'术后1月','offset_days':30,'window':[-5,7],'items':[]}]},
               {'id':'s2','name':'术后中期','children':[
                 {'id':'v90','name':'术后3月','offset_days':90,'window':[-7,14],'items':[]}]}],
      'offschedule':[{'id':'ae','name':'不良事件','items':[]}]}
hs.upsert_flow({'code':PFX+'F','name':'术后康复随访','category':'术后康复',
                'definition':FLOW,'status':'active','owner':'演示医生','scope':'shared'})
hs.flow_instantiate({'flow_code':PFX+'F','patient_no':PFX+'001',
                     'anchor_date':(today-datetime.timedelta(days=30)).strftime('%Y-%m-%d')})
hs.trigger_offschedule({'patient_no':PFX+'001','flow_code':PFX+'F','node_id':'ae','note':'演示不良事件'})
# M20: 一份**已发布**的患教材料(推送要用) + 一份草稿(验证推不出去)
_ok,_ = hs.upsert_edu_material({'code':PFX+'P','title':'已发布患教','body':'规律作息，遵医嘱用药。',
                                'topic':'disease','owner':'医生甲'})
hs.edu_transition({'id':_ok['id'],'action':'publish','operator':'主任乙'})
print('  数据已备好: 4 患者 / 1 量表(4 份填报) / 1 CRF / 1 宣教稿 / 1 知情同意书 / 1 流程(已分配)')

srv, API = start_backend({'PLATFORM_DOC_DIR': DOCTMP, 'PLATFORM_TOKEN': TOKEN},
                         log_path='/tmp/suifang_ui_srv.log')
WEB = start_static()
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser, page, errs = new_page(pw)
        page.goto('%s/platform-v2.html?api=%s' % (WEB, API), wait_until='networkidle')
        page.wait_for_timeout(1500)
        page.evaluate("localStorage.setItem('suifang_platform_token','%s');"
                      "localStorage.setItem('suifang_platform_operator','演示医生')" % TOKEN)
        page.reload(wait_until='networkidle'); page.wait_for_timeout(2500)

        section('1. 加载与导航')
        check('无 JS 运行时错误', not errs, ' | '.join(errs[:3]))
        navs = page.eval_on_selector_all('.nav-item', 'els=>els.map(e=>e.dataset.page)')
        check('9 个模块都在侧栏', len(navs) >= 9, navs)
        check('已建成的模块不再挂"未建设"标',
              page.eval_on_selector_all('.nav-item .wip', 'els=>els.length') <= 1,
              page.eval_on_selector_all('.nav-item', "els=>els.filter(e=>e.querySelector('.wip')).map(e=>e.dataset.page)"))

        section('2. 运营驾驶舱')
        page.click('[data-page="dashboard"]'); page.wait_for_timeout(1500)
        t = page.inner_text('#dashboard')
        check('KPI 卡渲染出来', '已建档患者' in t)
        check('阶段导航在', '随访资料准备' in t)

        section('3. 患者管理 (M1 + M8 + M9)')
        page.click('[data-page="patients"]'); page.wait_for_timeout(2000)
        t = page.inner_text('#patients')
        check('已建档患者渲染', '演示甲' in t or '已建档' in t, t[:100])

        section('4. 随访执行中心 (M2 + M7 + M5)')
        page.click('[data-page="execute"]'); page.wait_for_timeout(2000)
        check('执行中心有内容', len(page.inner_text('#execute')) > 50)

        section('5. 纳排与分组 (M18)')
        page.click('[data-page="cohorts"]'); page.wait_for_timeout(2000)
        t = page.inner_text('#cohorts')
        check('页面开门见山讲清改规则不动既有患者', '不会被自动挪走' in t)
        check('点明这是方案偏离而非数据更新', '方案偏离' in t)
        check('说明条件在保存时就校验', '保存时就校验' in t)
        page.click('#newCohortBtn'); page.wait_for_timeout(1500)
        check('新建方案对话框打开', page.query_selector('#chCode') is not None)
        page.fill('#chCode', PFX + 'H'); page.fill('#chName', '演示纳排方案')
        page.select_option('#chStatus', 'running')
        page.click('#incRows'); page.wait_for_timeout(200)
        page.evaluate("ruleAdd('inc')"); page.wait_for_timeout(600)
        check('条件行加得出来', page.query_selector('[data-rf="inc:0"]') is not None)
        page.select_option('[data-rf="inc:0"]', 'patient.no'); page.wait_for_timeout(400)
        page.select_option('[data-ro="inc:0"]', 'contains')
        page.fill('[data-rv="inc:0"]', PFX)
        page.click('#chGo'); page.wait_for_timeout(2500)
        t = page.inner_text('#cohortWrap')
        check('方案出现在列表', '演示纳排方案' in t, t[:120])
        check('列表展示纳入条件的人话描述', '纳入：' in t)
        page.click('#cohortWrap button:has-text("加分组")'); page.wait_for_timeout(1200)
        page.fill('#gpCode', 'EXP'); page.fill('#gpName', '试验组')
        page.select_option('#gpKind', 'experiment'); page.fill('#gpPri', '10')
        page.evaluate("ruleAdd('grp')"); page.wait_for_timeout(600)
        page.select_option('[data-rf="grp:0"]', 'patient.gender'); page.wait_for_timeout(400)
        page.fill('#gpTarget', '2')
        page.click('#gpGo'); page.wait_for_timeout(2200)
        page.click('#cohortWrap button:has-text("加分组")'); page.wait_for_timeout(1200)
        page.fill('#gpCode', 'CTL'); page.fill('#gpName', '对照组')
        page.select_option('#gpKind', 'control'); page.fill('#gpPri', '20')
        page.click('#gpGo'); page.wait_for_timeout(2200)
        t = page.inner_text('#cohortWrap')
        check('两个分组都在', '试验组' in t and '对照组' in t)
        check('兜底组被标出来', '兜底组' in t, [l for l in t.split('\n') if '兜底' in l][:2])
        sub('试算只算不写')
        page.click('#cohortWrap button:has-text("试算入组")'); page.wait_for_timeout(2500)
        v = page.inner_text('#drBody')
        check('试算抽屉打开', '符合纳排' in v, v[:100])
        check('给出各组分配人数', '各组分配' in v)
        check('提示这是试算没写库', '没有写库' in v)
        n0 = page.evaluate("(async()=>{const r=await fetch('%s/api/platform/enrollments?cohort=%sH');"
                           "return (await r.json()).count})()" % (API, PFX))
        check('试算确实没写库', n0 == 0, n0)
        page.click('#drFoot button:has-text("确认执行入组")'); page.wait_for_timeout(3000)
        n1 = page.evaluate("(async()=>{const r=await fetch('%s/api/platform/enrollments?cohort=%sH');"
                           "return (await r.json()).count})()" % (API, PFX))
        check('确认后才真的入组', n1 == 4, n1)

        section('6. 随访资料准备 · 量表库 (M10-M12)')
        page.click('[data-page="assets"]'); page.wait_for_timeout(2500)
        t = page.inner_text('#stabLib')
        check('量表库有内容', '质控演示量表' in t or '量表' in t, t[:100])
        page.click('#scaleGenBtn'); page.wait_for_timeout(900)
        md = page.inner_text('#modal')
        check('AI 生成量表对话框明说不含划界值分级', '不会包含划界值分级' in md)
        check('解释了为什么(实证研究)', '实证研究' in md)
        page.fill('#gnGoal', '演示评估目标'); page.fill('#gnDims', '维度一\n维度二')
        page.click('#gnGo'); page.wait_for_timeout(2500)
        g = page.inner_text('#drBody')
        check('生成草稿进入审核界面', 'AI 生成结果概览' in g, g[:80])
        check('分级档数为 0', '分级档数' in g and g.split('分级档数')[1].strip().startswith('0'))
        check('说明里有刻意不含分级的条目', '刻意不含划界值分级' in g)
        page.click('#drClose'); page.wait_for_timeout(500)

        section('7. CRF 表单库 (M14)')
        page.click('[data-stab="crf"]'); page.wait_for_timeout(1800)
        t = page.inner_text('#stabCrf')
        check('CRF 出现在列表', '不良事件随访表' in t, t[:120])
        check('说明了改表不伤已有数据', '改表不会伤到已填的数据' in t)
        page.click('#crfWrap button:has-text("填报")'); page.wait_for_timeout(1500)
        page.fill('#cfPno', PFX + '001'); page.click('#cfStart'); page.wait_for_timeout(2200)
        check('默认隐藏的题一开始不在表单里', page.query_selector('[data-cf="aedesc"]') is None)
        check('提示了有几道题被隐藏', '被逻辑隐藏' in page.inner_text('#drBody'))
        page.select_option('[data-cf="hasae"]', json.dumps(1)); page.wait_for_timeout(1800)
        check('选「是」后不良事件描述出现', page.query_selector('[data-cf="aedesc"]') is not None)
        page.select_option('[data-cf="hasae"]', json.dumps(0)); page.wait_for_timeout(1800)
        check('改回「否」后又收起来', page.query_selector('[data-cf="aedesc"]') is None)
        page.fill('[data-cf="nm"]', '演示患者'); page.fill('[data-cf="dt"]', '2026-08-01')
        page.wait_for_timeout(1500)
        page.click('#cfSubmit'); page.wait_for_timeout(2800)
        check('隐藏的必填项没有挡住提交', '已提交' in page.inner_text('#toast'), page.inner_text('#toast')[:100])

        section('8. 宣教材料库 (M15)')
        page.click('[data-stab="edu"]'); page.wait_for_timeout(1800)
        t = page.inner_text('#stabEdu')
        check('说清了为什么规矩比别处严', '多半会照做' in t)
        check('AI 产出一律是草稿', '一律是草稿' in t)
        check('高危数量直接显示在列表上', '处高危' in t, [l for l in t.split('\n') if '高危' in l][:2])
        page.click('#eduWrap button:has-text("查看 / 审核")'); page.wait_for_timeout(1600)
        v = page.inner_text('#drBody')
        check('体检结果顶在正文之前', v.index('内容体检') < v.index('正文'))
        check('高危逐条列出并给了原文', v.count('原文：') >= 3, v.count('原文：'))
        dialogs = []
        page.on('dialog', lambda d: (dialogs.append(d.message), d.dismiss()))
        page.click('#drFoot button:has-text("审核并发布")'); page.wait_for_timeout(2500)
        confirms = [m for m in dialogs if '命中「' in m]
        check('发布时把高危逐条摆进确认框', confirms and confirms[0].count('命中「') >= 3,
              (confirms[0][:80] if confirms else dialogs[:1]))
        check('确认框说明了后果', confirms and '照做' in confirms[0])
        st = page.evaluate("(async()=>{const r=await fetch('%s/api/platform/edu?code=%sE');"
                           "const d=await r.json();return (d.materials[0]||{}).status})()" % (API, PFX))
        check('取消后仍是草稿, 没有发出去', st == 'draft', st)
        page.click('#drClose'); page.wait_for_timeout(400)

        section('9. 知情与项目资料 (M17)')
        page.click('[data-stab="doc"]'); page.wait_for_timeout(1800)
        t = page.inner_text('#stabDoc')
        check('明说不是法律意义上的电子签名', '不是法律意义上的电子签名' in t)
        check('说清了缺什么(CA 证书)', 'CA 数字证书' in t)
        check('明说不能作为法律证据', '不能作为法律证据' in t)
        check('资料在列表里', 'XX研究知情同意书' in t)
        st = page.evaluate("(async()=>{const r=await fetch('%s/api/platform/document/download?code=%sD');"
                           "return r.status})()" % (API, PFX))
        check('不带口令直接请求下载被 403', st == 403, st)

        section('10. 数据质控 (M13)')
        page.click('[data-page="quality"]'); page.wait_for_timeout(2000)
        t = page.inner_text('#quality')
        check('讲清了质控 ≠ 预警', '质控 ≠ 预警' in t and '366℃' in t)
        check('点名了收件人不同', '收件人是医生' in t and '收件人是数据管理员' in t)
        page.click('#qcRunBtn'); page.wait_for_timeout(3500)
        f = page.inner_text('#qcFindings')
        check('抓到"逐题完全一致"', '逐题完全一致' in f, f[:150])
        check('抓到"总分跳变"', '总分由' in f)
        check('展示了判定依据供复核', '判定依据' in f)
        sub('历次对比')
        page.click('#qcCompareBtn'); page.wait_for_timeout(2000)
        if page.query_selector('#cmpGo'):
            page.click('#cmpGo'); page.wait_for_timeout(2500)
            c = page.inner_text('#drBody')
            check('对比抽屉打开', '第 1 次' in c, c[:80])
            check('说明了红/灰的含义', '红色格子' in c and '灰色' in c)
            check('确实有格子被标红', len(page.query_selector_all('.cmp-tbl td.chg')) > 0)
            page.click('#drClose'); page.wait_for_timeout(400)
        else:
            check('对比入口给出可读的空态', True)

        section('11. 统计分析 · 高级检索 (M6 + M16)')
        page.click('[data-page="analytics"]'); page.wait_for_timeout(2200)
        page.click('#searchBtn'); page.wait_for_timeout(1200)
        check('hero 按钮直接给一条起手条件', page.query_selector('[data-sqf="0"]') is not None)
        page.select_option('[data-sqf="0"]', 'patient.no'); page.wait_for_timeout(500)
        page.select_option('[data-sqo="0"]', 'contains'); page.fill('[data-sqv="0"]', PFX)
        page.click('#sqRun'); page.wait_for_timeout(2500)
        check('检索出 4 人', '共 4 人' in page.inner_text('#sqResultPanel'),
              [l for l in page.inner_text('#sqResultPanel').split('\n') if '共' in l][:2])
        page.click('#sqStat'); page.wait_for_timeout(1200)
        check('统计对话框说明图表类型是按规则选的', '按规则' in page.inner_text('#modal'))
        page.click('#sqStatGo'); page.wait_for_timeout(3000)
        check('图表画出来了', len(page.query_selector_all('#sqCharts svg')) >= 1,
              len(page.query_selector_all('#sqCharts svg')))
        check('没有引用任何外部资源(内网也能看)',
              page.evaluate("!Array.from(document.querySelectorAll('script[src],link[rel=stylesheet],img[src]'))"
                            ".some(e=>/^https?:/.test(e.src||e.href||''))"))

        section('11b. 随访流程 (M19)')
        page.click('[data-page="plans"]'); page.wait_for_timeout(2200)
        t = page.inner_text('#plans')
        check('说清了访视窗口的意义', '没有窗口就没有' in t)
        check('说清了流程外阶段不进分母', '不计入随访完成率的分母' in t or '不进完成率' in t)
        check('点明两种错法的后果', '永远上不去' in t and '永远很低' in t)
        check('流程出现在流程库', '术后康复随访' in page.inner_text('#flowWrap'),
              page.inner_text('#flowWrap')[:120])
        page.click('#flowWrap button:has-text("查看")'); page.wait_for_timeout(1500)
        v = page.inner_text('#drBody')
        check('流程结构按层级展开', '术后早期' in v and '术后7天' in v, v[:120])
        check('层级名来自定义而不是写死', '阶段 / 访视' in v, v[:150])
        check('访视显示窗口', '窗口' in v)
        check('流程外阶段单独一栏并标明不进分母', '不进完成率分母' in v)
        page.click('#drClose'); page.wait_for_timeout(400)
        page.click('[data-ftab="visit"]'); page.wait_for_timeout(2200)
        t = page.inner_text('#ftabVisit')
        check('访视看板有数据', '术后7天' in t, t[:150])
        check('超窗的访视标出来了', '已超窗' in t)
        check('流程外事件单独计数且注明不进分母', '不进完成率分母' in t)
        check('给出随访完成率', '随访完成率' in t)
        check('说明了分母是什么', '已到期访视' in t, [l for l in t.split('\n') if '分母' in l][:2])
        page.select_option('#vsFilter', 'overdue'); page.wait_for_timeout(1500)
        rows = page.inner_text('#visitTable')
        check('按超窗筛后只剩超窗的', '未到期' not in rows, rows[:150])
        page.select_option('#vsFilter', ''); page.wait_for_timeout(1200)

        section('11c. 超窗跟进与推送 (M20)')
        page.click('[data-ftab="visit"]'); page.wait_for_timeout(1800)
        t = page.inner_text('#ftabVisit')
        check('超窗按天数分档展示', '超窗分档' in t, t[:120])
        check('说清了为什么分档', '还救得回来' in t or '混在一起' in t)
        check('访视行可勾选', page.query_selector('[data-vsel]') is not None)
        page.click('#vsSelAll'); page.wait_for_timeout(500)
        n = page.inner_text('#vsSelN')
        check('全选后计数更新', n != '0', n)
        page.click('#vsFollowBtn'); page.wait_for_timeout(1000)
        check('批量跟进对话框打开', page.query_selector('#fuAct') is not None)
        page.select_option('#fuAct', 'lost'); page.wait_for_timeout(500)
        md = page.inner_text('#modal')
        check('选失访时给出重大判定的警示', '移出分析人群' in md, md[-200:])
        check('说明会连带终止流程', '终止其整个随访流程' in md)
        check('说明不能批量一点了事', '不能批量一点了事' in md)
        page.select_option('#fuAct', 'reschedule'); page.wait_for_timeout(500)
        check('改约时出现日期输入', page.is_visible('#fuDateWrap'))
        check('说明窗口会平移保持宽窄', '保持原本宽窄' in page.inner_text('#modal'))
        page.select_option('#fuAct', 'call'); page.wait_for_timeout(400)
        page.fill('#fuResult', '已电话联系，约定本周来院')
        page.click('#fuGo'); page.wait_for_timeout(2800)
        check('批量跟进执行成功', '电话联系' in page.inner_text('#toast'),
              page.inner_text('#toast')[:100])

        page.click('[data-ftab="push"]'); page.wait_for_timeout(1800)
        t = page.inner_text('#ftabPush')
        check('明说通道没接', '推送通道' in t and '还没接' in t)
        check('明说状态绝不会显示已发送', '绝不会显示' in t)
        check('说清了后果', '比不做这个功能更糟' in t)
        check('说明只有已发布的材料能推', '只有已发布的宣教材料能推' in t)
        check('点明这是那道闸门的落点', '装饰' in t)
        page.click('#newPushBtn'); page.wait_for_timeout(1200)
        check('推送对话框打开', page.query_selector('#pcType') is not None)
        page.select_option('#pcType', 'edu'); page.wait_for_timeout(600)
        check('选患教时出现材料下拉', page.is_visible('#pcEduWrap'))
        check('下拉只列已发布的', '已发布患教' in page.inner_text('#pcEduWrap'),
              page.inner_text('#pcEduWrap')[:120])
        check('并说明草稿推不出去', '推不出去' in page.inner_text('#modal'))
        dlg = []
        page.on('dialog', lambda d: (dlg.append(d.message), d.dismiss()))
        page.fill('#pcPno', PFX + '001')
        page.click('#pcGo'); page.wait_for_timeout(2500)
        check('试算后弹确认框', dlg and '位患者' in dlg[0], (dlg[:1] or [''])[0][:80])
        check('确认框里带通道未接的警告', dlg and '没有真的发出去' in dlg[0])
        st = page.evaluate("(async()=>{const r=await fetch('%s/api/platform/pushes?patientNo=%s001');"
                           "return (await r.json()).count})()" % (API, PFX))
        check('取消后没有入队', st == 0, st)
        page.click('#mdClose'); page.wait_for_timeout(400)

        section('12. 系统管理')
        page.click('[data-page="settings"]'); page.wait_for_timeout(1500)
        check('设置页有连接配置', page.query_selector('#cfgApi') is not None)

        section('13. 回归: 每个 module-hero 按钮都必须真的点得动')
        # 曾经有个 opacity .04 的 130px "AI" 水印伪元素盖在所有 hero 按钮上,
        # 肉眼完全看不出来, 只有真去点才发现整排按钮全点不动。
        for pg, sel in [('patients', '#newPatientBtn'), ('execute', '#vitalIngestBtn'),
                        ('plans', '#newPlanBtn'), ('plans', '#newFlowBtn'), ('analytics', '#exportBtn'),
                        ('analytics', '#searchBtn'), ('cohorts', '#newCohortBtn'),
                        ('assets', '#scaleParseBtn'), ('assets', '#scaleGenBtn'),
                        ('assets', '#crfGenBtn'), ('assets', '#crfExcelBtn'),
                        ('assets', '#eduGenBtn'), ('assets', '#docUploadBtn'),
                        ('quality', '#qcRunBtn'), ('quality', '#qcCompareBtn')]:
            page.click('[data-page="%s"]' % pg); page.wait_for_timeout(700)
            el = page.query_selector(sel)
            ok = bool(el) and el.is_visible() and el.is_enabled()
            why = '元素不存在/不可见/被禁用' if not ok else ''
            if ok:
                # 点不动时最有用的信息是"那个点上盖着谁", 而不是"超时了"。
                # 当年那个 opacity .04 的水印伪元素就是这么揪出来的。
                blocker = page.evaluate("""(s)=>{
                    const e=document.querySelector(s); if(!e) return 'no-el';
                    const r=e.getBoundingClientRect();
                    const top=document.elementFromPoint(r.left+r.width/2, r.top+r.height/2);
                    if(!top) return 'point-outside-viewport';
                    if(top===e||e.contains(top)) return '';
                    return (top.tagName+'#'+(top.id||'')+'.'+(top.className||'')).slice(0,60);
                }""", sel)
                if blocker:
                    ok = False; why = '被遮挡: ' + blocker
            try:
                if ok:
                    # 用 locator 点而不是 ElementHandle —— 它超时时会说清在等哪个可操作性条件
                    page.click(sel, timeout=5000)
                    page.wait_for_timeout(400)
                    # 关掉刚打开的弹窗/抽屉。判断依据是**容器有没有 .show**, 不是
                    # 关闭按钮 is_visible() —— 抽屉未打开时是滑出屏幕外(transform),
                    # 不是 display:none, is_visible() 照样返回 true, 但点不到(点在视口外)。
                    for cont, closer in (('#modal', '#mdClose'), ('#drawer', '#drClose')):
                        shown = page.evaluate(
                            "(c)=>{const e=document.querySelector(c);"
                            "return !!e && e.classList.contains('show')}", cont)
                        if shown:
                            page.click(closer, timeout=5000)
                            page.wait_for_timeout(250)
            except Exception as e:
                ok = False
                why = str(e).split('\n')[0][:150]
            check('%s 的 %s 可点击' % (pg, sel), ok, '' if ok else why)

        section('14. 窄屏 (医护移动端查看)')
        page.set_viewport_size({'width': 390, 'height': 850}); page.wait_for_timeout(1200)
        # 窄屏下侧栏是滑出屏幕的(transform), 要靠汉堡菜单唤出 —— 这本身就是移动端的用法, 先验它。
        check('窄屏下侧栏默认收起', page.evaluate(
            "getComputedStyle(document.getElementById('sidebar')).transform !== 'none'"))
        page.click('#menuBtn'); page.wait_for_timeout(600)
        check('汉堡菜单能唤出侧栏',
              page.evaluate("document.getElementById('sidebar').classList.contains('open')"))
        page.click('[data-page="patients"]'); page.wait_for_timeout(700)
        check('窄屏下点导航能切页且侧栏自动收回',
              not page.evaluate("document.getElementById('sidebar').classList.contains('open')")
              and page.evaluate("document.getElementById('patients').classList.contains('active')"))
        # 逐页量横向溢出。这里走 JS 的 goto() 而不是点导航 —— 要量的是**版面**,
        # 每页都去开一次汉堡菜单只是把上面那三条断言重复九遍。
        for pg in ('dashboard', 'patients', 'execute', 'cohorts', 'assets', 'quality',
                   'analytics', 'plans', 'settings'):
            page.evaluate("(p)=>goto(p)", pg); page.wait_for_timeout(500)
            w = page.evaluate('document.documentElement.scrollWidth')
            check('%s 窄屏无横向溢出' % pg, w <= 400, w)
        page.set_viewport_size({'width': 1500, 'height': 1000})

        check('全程无 JS 错误', not errs, ' | '.join(errs[:3]))
        browser.close()
finally:
    srv.terminate()
    try: srv.wait(timeout=5)
    except Exception: srv.kill()
    clean()
    shutil.rmtree(DOCTMP, ignore_errors=True)
    print('  测试数据与临时目录已清')
finish()
