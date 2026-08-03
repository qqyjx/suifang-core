#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M15 宣教材料库 (方案 §2.3)。

这块的测试重点和别处不同。CRF/量表生成得不好, 医护当场就发现不合用;
宣教材料是**直接推给患者**的, 患者没有能力判断对错而且多半会照做。
所以断言集中在两条闸门上:
  1. AI 产出永远进不了 published, 除非有人署名审核
  2. 剂量数字 / "可自行停药" / "不必就医" 这三类必须被标出来, 且发布时必须被确认
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import hs, check, section, sub, finish, ensure_all_tables, db

PFX='T15'
ensure_all_tables()
def clean():
    db(("DELETE FROM platform_edu_log WHERE material_id IN (SELECT id FROM platform_edu_material WHERE code LIKE %s)",(PFX+'%',)),
       ("DELETE FROM platform_edu_material WHERE code LIKE %s",(PFX+'%',)))
clean()
scan=lambda t:{x['rule'] for x in hs.scan_edu_content(t)}

section('1. 内容体检: 哪些话不该群发给患者')
sub('block 级: 患者照做会出事')
check('具体剂量被标出', 'dosage' in scan('每次服用二甲双胍 500mg，每日两次。'))
check('剂量说明解释了为什么不能写', '剂量因人而异' in [f['why'] for f in hs.scan_edu_content('服 500mg')][0])
check('中文剂量单位也认', 'dosage' in scan('每日 3 克。'))
check('片/粒 也算剂量', 'dosage' in scan('早晚各 2 片。'))
check('"可自行停药"被标出', 'med_change' in scan('血压平稳后可自行停药。'))
check('"自行减量"被标出', 'med_change' in scan('感觉好转可以自行减量。'))
check('"加倍服用"被标出', 'med_change' in scan('漏服后下次加倍服用。'))
check('"不必就医"被标出', 'no_care' in scan('轻微头晕不必就医。'))
check('"不需要复查"被标出', 'no_care' in scan('指标正常就不需要复查了。'))
check('这三类都是 block 级',
      all(f['level']=='block' for f in hs.scan_edu_content('服 500mg 后可自行停药，不必就医')))
sub('warn 级: 未必错但要人看一眼')
check('绝对化承诺被标出', 'absolute' in scan('坚持锻炼一定能根治高血压。'))
check('"无副作用"被标出', 'absolute' in scan('本药无副作用。'))
check('诊断性断言被标出', 'diagnosis' in scan('您患有2型糖尿病。'))
check('急症词被标出并要求给出求助方式',
      'emergency' in scan('出现胸痛时…') and
      '求助方式' in [f['why'] for f in hs.scan_edu_content('出现胸痛时…') if f['rule']=='emergency'][0])
check('这两类是 warn 级', all(f['level']=='warn' for f in hs.scan_edu_content('一定能根治，您确诊为高血压')))
sub('正常内容不该误报')
GOOD=('高血压是一种需要长期管理的慢性病。\n日常请规律作息、清淡饮食、坚持监测血压并记录。\n'
      '用药请严格遵医嘱，不要自行调整；如有不适请及时联系随访医生。\n'
      '如出现头晕、视物模糊等情况，请尽快就医。')
check('一份规范的宣教稿零命中', hs.scan_edu_content(GOOD)==[], [f['rule'] for f in hs.scan_edu_content(GOOD)])
check('"遵医嘱"不误报', hs.scan_edu_content('用药请遵医嘱。')==[])
check('"多喝水"不误报', hs.scan_edu_content('建议每天多喝水，适量运动。')==[])
check('"不要自行调整"不误报(这是规则自己推荐的正确写法)', 'med_change' not in scan('不要自行调整用药。'))
for neg in ['切勿自行停药','请勿自行减量','禁止自行换药','避免自行加量','不可自行调整']:
    check('「%s」不误报'%neg, 'med_change' not in scan(neg+'。'))
check('去掉否定词就必须命中', 'med_change' in scan('可自行停药。'))
check('否定不影响剂量规则(前面加"不要"也还是剂量数字)', 'dosage' in scan('不要超过 500mg。'))
check('"不必就医"自己带否定词, 不能被否定排除误伤', 'no_care' in scan('不必就医。'))
sub('命中太多时不刷屏')
many='服 1mg。'*30
check('同一类最多给 5 条', sum(1 for f in hs.scan_edu_content(many) if f['rule']=='dosage')<=5)
check('每条都带原文片段供定位', all(f.get('excerpt') for f in hs.scan_edu_content('服用 500mg')))

section('2. 生成: 产出恒为草稿')
d,r=hs.generate_edu_draft({'disease':'2型糖尿病','stage':'确诊初期','topic':'medication'})
check('生成成功', d and r)
check('status 恒为 draft', d['status']=='draft')
check('标了 needs_review', r['needs_review'] is True)
check('说明里点明必须人工审核后才能被计划调用', any('必须经人工审核' in n['detail'] for n in r['notes']))
check('说明里解释了为什么(患者会照做)', any('多半会照做' in n['detail'] for n in r['notes']))
check('骨架里没有编造的医学结论(全是待填写)', d['body'].count('【待填写】')>=3)
check('一定有"什么情况下必须联系医生"这一节', '什么情况下必须联系医生' in d['body'])
check('那一节还提示要留联系方式', '留下联系方式' in d['body'])
check('带免责声明', '不能替代医生的诊疗意见' in d['body'])
check('生成的骨架自己零命中体检', r['content_findings']==[], r['content_findings'])
for t in hs.EDU_TOPICS:
    check('主题 %s 的骨架都含"何时找医生"'%hs.EDU_TOPIC_LABELS[t],
          '什么情况下必须联系医生' in hs.generate_edu_draft({'disease':'x','topic':t})[0]['body'])
d2,_=hs.generate_edu_draft({'disease':'高血压','topic':'diet','format':'video_script'})
check('视频脚本给镜头分节', '镜头 1' in d2['body'])
check('视频脚本也提醒别写剂量', '不要写具体剂量' in d2['body'])
check('图文科普给配图建议', '配图建议' in hs.generate_edu_draft({'disease':'高血压','topic':'diet','format':'illustrated'})[0]['body'])

section('3. 入库与状态机')
r,err=hs.upsert_edu_material({'code':PFX+'A','title':'高血压用药指导','body':GOOD,
                              'topic':'medication','category':'高血压','owner':'医生甲'})
check('入库成功', r and r['status']=='draft', err)
mid=r['id']
check('零高危发现', r['blocking_findings']==0, r['content_findings'])
sub('不许直接置 published')
r2,err2=hs.upsert_edu_material({'code':PFX+'B','title':'x','body':'y','status':'published'})
check('接口层面就堵死了"生成即发布"', r2 is None and err2 and 'publish' in err2, err2)
check('并告诉你该走哪条路', err2 and 'transition' in err2)
sub('正常流转')
check('提交审核', hs.edu_transition({'id':mid,'action':'submit','operator':'医生甲'})[0]['to']=='reviewing')
check('可以退回', hs.edu_transition({'id':mid,'action':'reject','operator':'主任乙','note':'太笼统'})[0]['to']=='draft')
t,err=hs.edu_transition({'id':mid,'action':'publish'})
check('发布必须署名', t is None and 'operator' in (err or ''), err)
check('署名后可发布', hs.edu_transition({'id':mid,'action':'publish','operator':'主任乙','note':'内容无误'})[0]['to']=='published')
t,err=hs.edu_transition({'id':mid,'action':'submit','operator':'x'})
check('已发布的不能再提交审核', t is None and err, err)
check('拒绝时给出允许的前置状态', '允许的前置状态' in (err or ''))
check('可归档', hs.edu_transition({'id':mid,'action':'archive','operator':'主任乙'})[0]['to']=='archived')
check('不存在的材料被拒', hs.edu_transition({'id':999999,'action':'publish','operator':'x'})[0] is None)
check('非法 action 被拒', hs.edu_transition({'id':mid,'action':'bogus'})[0] is None)

section('4. 高危内容必须被看见才能发布 (最关键)')
RISKY='二甲双胍每次 500mg，每日两次。\n血糖平稳后可自行减量。\n轻微不适不必就医。'
r,err=hs.upsert_edu_material({'code':PFX+'C','title':'糖尿病用药','body':RISKY,
                              'topic':'medication','owner':'医生甲'})
check('入库时就标出了高危', r and r['blocking_findings']>=3, (r or {}).get('blocking_findings'))
rid=r['id']
check('三类都识别到', {'dosage','med_change','no_care'} <= {f['rule'] for f in r['content_findings']})
t,err=hs.edu_transition({'id':rid,'action':'publish','operator':'主任乙'})
check('直接发布被挡住', t and t.get('published') is False)
check('挡住时把高危条目原样返回', t and len(t['blocking_findings'])>=3)
check('提示说清了为什么', t and '患者' in t['hint'] and '照做' in t['hint'], (t or {}).get('hint'))
t2,err=hs.edu_transition({'id':rid,'action':'publish','operator':'主任乙','ack_findings':True,
                          'note':'已逐条确认: 剂量为每片含量说明'})
check('逐条确认后可以放行', t2 and t2.get('to')=='published', err)
check('记下了确认了几条', t2 and t2['acked_findings']>=3)
sub('改已发布的材料要开新版')
r3,err=hs.upsert_edu_material({'code':PFX+'C','version':'1','title':'糖尿病用药(修订)','body':GOOD,
                               'topic':'medication','owner':'医生甲'})
check('已发布的改内容 -> 开新版', r3 and r3['version']=='2', (r3 or {}).get('version'))
q,_=hs.query_edu_materials()
v1=[m for m in q['materials'] if m['code']==PFX+'C' and m['version']=='1']
check('旧版仍在架(患者手里的版本对得上)', v1 and v1[0]['status']=='published')

section('5. 检索与留痕 (§2.3(2))')
q,err=hs.query_edu_materials(topic='medication')
check('按主题筛', q and all(m['topic']=='medication' for m in q['materials']), err)
check('按状态筛', all(m['status']=='published' for m in hs.query_edu_materials(status='published')[0]['materials']))
check('按关键词搜标题', hs.query_edu_materials(keyword='糖尿病')[0]['count']>=1)
check('按病种筛', all(m['category']=='高血压' for m in hs.query_edu_materials(category='高血压')[0]['materials']))
q,_=hs.query_edu_materials(material_id=rid)
m=q['materials'][0]
check('详情带正文', 'body' in m)
check('详情带流转留痕', m.get('log') and len(m['log'])>=2, len(m.get('log') or []))
check('记下了审核人', m['reviewed_by']=='主任乙')
check('记下了审核意见', '逐条确认' in (m['review_note'] or ''))
check('列表带中文标签', m['topic_label']=='用药指导' and m['status_label']=='已发布')

section('6. 清理')
clean()
print('  ✅ 测试数据已清')
finish()
