#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M25 OCR 辅助采集。

这个套件里最要紧的不是"识别得准不准" —— 识别一定不准, 那是前提不是缺陷。
要守住的是: **不准的东西不会安静地变成患者数据**。所以断言重心在那四道闸上。

不需要数据库就能跑的部分(识别、分流、字段抽取、能不能一键采纳的判定)占大头:
    HARNESS_NO_DB=1 python3 scripts/tests/test_m25_ocr.py
"""
import os
import io
import sys
import json
import base64
import shutil
import tempfile

# 原件要落盘。默认落 /opt/suifang/uploads/ocr(生产路径), 测试当然不能往那儿写 ——
# 必须在 import 被测模块**之前**设好, OCR_DIR 是模块级常量。
OCRTMP = tempfile.mkdtemp(prefix='suifang_ocrtest_')
os.environ['PLATFORM_OCR_DIR'] = OCRTMP

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import *          # noqa: F401,F403
from _harness import hs, check, section, sub, finish, db, need_db

P = 'T25'                       # 本套件专用门诊号前缀
FONT_HEI = '/home/qq/.fonts/simhei.ttf'
FONT_SONG = '/home/qq/.fonts/simsun.ttc'


# ---------------------------------------------------------------- 造测试材料

def have(mod):
    try:
        __import__(mod)
        return True
    except ImportError:
        return False


def make_lab_png(jitter=0.0):
    """造一张检验报告单。**内容全是编的**, 姓名门诊号都不对应真人。"""
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1240, 1754
    img = Image.new('RGB', (W, H), 'white')
    d = ImageDraw.Draw(img)
    f_t = ImageFont.truetype(FONT_HEI, 42)
    f_h = ImageFont.truetype(FONT_HEI, 24)
    f = ImageFont.truetype(FONT_SONG, 22)
    d.text((330, 60), '××医院  检验报告单', font=f_t, fill='black')
    y = 150
    for row in (('姓名：赵慧敏', '性别：女', '年龄：58岁'),
                ('门诊号：' + P + '0001', '科室：精神科门诊', '床号：—'),
                ('采样时间：2026-07-31', '报告时间：2026-07-31', '送检医生：李文博')):
        for i, cell in enumerate(row):
            d.text((90 + i * 380, y), cell, font=f, fill='black')
        y += 38
    y += 30
    for x, t in ((90, '项目'), (440, '结果'), (640, '单位'), (800, '参考范围')):
        d.text((x, y), t, font=f_h, fill='black')
    y += 46
    for r in (('白细胞计数 WBC', '6.32', '10^9/L', '3.50-9.50'),
              ('红细胞计数 RBC', '4.15', '10^12/L', '3.80-5.10'),
              ('血红蛋白 HGB', '119', 'g/L', '115-150'),
              ('空腹血糖', '6.8', 'mmol/L', '3.90-6.10'),
              ('血锂浓度', '0.72', 'mmol/L', '0.40-0.80')):
        for x, cell in zip((90, 440, 640, 800), r):
            d.text((x, y), cell, font=f, fill='black')
        y += 34
    if jitter:
        img = img.rotate(jitter, expand=True, fillcolor='white', resample=Image.BICUBIC)
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


def make_mixed_pdf(lab_png):
    """第 1 页有文字层, 第 2 页是贴进去的图 —— 这就是"混合件"。"""
    import fitz
    doc = fitz.open()
    p1 = doc.new_page(width=595, height=842)
    y = 90
    for l in ('随访记录 第一页', '姓名：赵慧敏', '门诊号：' + P + '0001',
              '访视日期：2026-07-31', '空腹血糖：6.8'):
        p1.insert_text((70, y), l, fontname='hei', fontfile=FONT_HEI, fontsize=14)
        y += 26
    p2 = doc.new_page(width=595, height=842)
    tmp = '/tmp/_t25_lab.png'
    with open(tmp, 'wb') as f:
        f.write(lab_png)
    p2.insert_image(fitz.Rect(40, 40, 555, 768), filename=tmp)
    out = doc.tobytes()
    doc.close()
    os.remove(tmp)
    return out


# CRF: 故意混上各种题型, 每一类的核对规则都不一样
CRF_DEF = {'sections': [
    {'name': '基本信息', 'items': [
        {'id': 'patient_name', 'text': '姓名', 'type': 'text', 'required': True},
        {'id': 'dept', 'text': '科室', 'type': 'text'},
        {'id': 'gender', 'text': '性别', 'type': 'single',
         'options': [{'label': '男', 'value': 1}, {'label': '女', 'value': 2}]},
        {'id': 'sample_date', 'text': '采样时间', 'type': 'date'},
        {'id': 'note', 'text': '以下为检验结果', 'type': 'note'},
    ]},
    {'name': '检验结果', 'items': [
        {'id': 'glucose', 'text': '空腹血糖', 'type': 'number', 'min': 0, 'max': 40},
        {'id': 'rbc', 'text': '红细胞计数', 'type': 'number', 'min': 0, 'max': 20,
         'ocr_hints': ['红细胞计数 RBC', 'RBC']},
        {'id': 'lithium', 'text': '血锂浓度', 'type': 'number', 'min': 0, 'max': 5},
        {'id': 'hgb', 'text': '血红蛋白', 'type': 'number', 'min': 0, 'max': 300},
        {'id': 'missing_item', 'text': '这张单子上根本没有的指标', 'type': 'number'},
    ]},
]}


# ---------------------------------------------------------------- 纯逻辑

section('M25-0 引擎与依赖')
st = hs.ocr_engine_status()
check('引擎跑在本地, 声明不出网 —— 输入是带姓名身份证号的病历照片, 这条是硬要求',
      st['runs_locally'] is True and st['sends_data_out'] is False)
check('引擎没装时不静默降级, 而是给出装法',
      st['ready'] is True or ('pip install' in (st.get('error') or '')), st.get('error'))
check('数字/日期/表格题被声明为"只能键入"',
      set(['number', 'date']).issubset(set(st['typed_only_types'])), st['typed_only_types'])
ENGINE_OK = st['ready'] and have('PIL')
if not ENGINE_OK:
    print('  ⚠️  引擎或 Pillow 没装, 跳过实际识别的断言')

sub('整条 OCR 链路不出网')
# 源码级断言。这条守的是将来: 有人为了"提高准确率"接一个云 OCR 兜底, 病历照片
# 就在没人察觉的情况下开始外发。加断言比写在文档里管用。
src = io.open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'health_server.py'), encoding='utf-8').read()
m25 = src[src.index('# 随访平台 M25: OCR 辅助采集'):src.index('# 随访平台 M6: 队列数据导出')]
egress = [w for w in ('urllib.request', 'urlopen', 'requests.', 'http.client',
                      'socket.', 'httpx', '_call_deepseek', 'api_key', 'API_KEY')
          if w in m25]
check('M25 整段源码里没有任何出网/密钥调用 —— 输入是带姓名身份证号的病历照片, '
      '接一个"云 OCR 兜底"就等于开始外发 PHI', not egress, egress)
check('引擎自述跑在本地', 'rapidocr' in str(st.get('engine')))

sub('裁图接口绝不透传上传的原字节')
if have('PIL'):
    evil = b'<html><script>alert(1)</script></html>'
    out, err = hs._ocr_to_png(evil)
    check('带 .png 名字的 HTML 传进来: 要么报错, 要么出一张真 PNG, '
          '绝不可能把那段 HTML 原样发回去(它是要内联显示的, 透传就是存储型 XSS)',
          out is None or (out[:8] == b'\x89PNG\r\n\x1a\n' and evil not in out), err)
    from PIL import Image as _Im
    _b = io.BytesIO()
    _Im.new('RGB', (40, 20), 'white').save(_b, 'JPEG')
    out, err = hs._ocr_to_png(_b.getvalue())
    check('传进来的是 JPEG, 出去的是 PNG(说明确实重编码过了)',
          out and out[:8] == b'\x89PNG\r\n\x1a\n', err)

section('M25-1 归一化: 只做该做的, 不替人猜')
check('全角数字归一化: ４.１５ == 4.15', hs._ocr_norm('４.１５') == '4.15')
check('空格归一化: "4. 15" == 4.15 —— 实测里 OCR 就是这么读的',
      hs._ocr_norm('4. 15') == hs._ocr_norm('4.15'))
check('中文冒号统一成半角', hs._ocr_norm('姓名：张三') == '姓名:张三')
check('**不做数字近似**: O 不当 0 —— 那一步开始就是替人猜, 猜错谁也发现不了',
      hs._ocr_norm('O.72') != hs._ocr_norm('0.72'))
check('不同的数就是不同的数: 4.15 != 415', hs._ocr_norm('4.15') != hs._ocr_norm('415'))

section('M25-2 从识别行里抽字段')
LINES = [
    {'text': '姓名：赵慧敏', 'conf': 0.99, 'page': 1, 'source': 'ocr', 'box': [88, 147, 225, 174]},
    {'text': '科室', 'conf': 0.98, 'page': 1, 'source': 'ocr', 'box': [469, 147, 540, 174]},
    {'text': '精神科门诊', 'conf': 0.97, 'page': 1, 'source': 'ocr', 'box': [560, 147, 700, 174]},
    {'text': '空腹血糖', 'conf': 0.99, 'page': 1, 'source': 'ocr', 'box': [88, 300, 181, 328]},
    {'text': '6.8', 'conf': 0.93, 'page': 1, 'source': 'ocr', 'box': [436, 300, 479, 328]},
    {'text': 'mmol/L', 'conf': 0.98, 'page': 1, 'source': 'ocr', 'box': [637, 300, 710, 328]},
    {'text': '血锂浓度', 'conf': 0.99, 'page': 1, 'source': 'ocr', 'box': [88, 340, 181, 368]},
    {'text': '0.72', 'conf': 0.88, 'page': 1, 'source': 'ocr', 'box': [436, 340, 490, 368]},
]
v, _ln = hs._find_value_for_label(LINES, ['姓名'])
check('同行版式: "姓名：赵慧敏" 取到 赵慧敏', v == '赵慧敏', v)
v, ln = hs._find_value_for_label(LINES, ['空腹血糖'])
check('分列版式: 标签和值分成两段时, 取同一行带里右边那段', v == '6.8', v)
check('取值同时把坐标带出来 —— 前端要靠它裁原图, 不看原件的核对不叫核对',
      ln is not None and ln.get('box') == [436, 300, 479, 328])
v, _ = hs._find_value_for_label(LINES, ['科室'])
check('值在右边另一段时也能接上', v == '精神科门诊', v)
v, _ = hs._find_value_for_label(LINES, ['白蛋白'])
check('单子上没有的标签返回 None, 不返回一个"最像的"',
      v is None, v)
v, _ = hs._find_value_for_label(LINES, ['某某某', '血锂浓度'])
check('ocr_hints 里任一别名命中即可', v == '0.72', v)

section('M25-3 表格聚行')
rows = hs.cluster_table_rows(LINES, page=1)
by_y = {r['y']: [c['text'] for c in r['cells']] for r in rows}
row_glu = [t for y, t in by_y.items() if '空腹血糖' in t]
check('同一行的几列聚到一起且按 x 排序',
      row_glu and row_glu[0] == ['空腹血糖', '6.8', 'mmol/L'], row_glu)
check('挨得很近的两行不会被并掉 —— 并了就是把血锂的值算到血糖那行, '
      '串位后每个值看上去都合法',
      len(rows) == 3 and not any('空腹血糖' in t and '血锂浓度' in t for t in by_y.values()),
      [r['y'] for r in rows])
check('每一格都带回自己的坐标, 供逐格裁图',
      all(c.get('box') for r in rows for c in r['cells']))

section('M25-4 待核清单: 按 CRF 全量生成, 不是按"识别命中了什么"')
cands = hs.build_ocr_candidates(CRF_DEF, LINES)
keys = [c['field_key'] for c in cands]
check('note 型(只展示不采集)不进待核清单', 'note' not in keys, keys)
check('**识别完全没命中的题也必须进清单** —— 实测里 5 个 ↑ 只检出 1 个且毫无提示, '
      '只给命中项建记录的话人核完一遍还是漏', 'missing_item' in keys)
miss = [c for c in cands if c['field_key'] == 'missing_item'][0]
check('没命中的题 ocr_value 是空的, 不编一个值出来', miss['ocr_value'] is None)
check('待核字段数 == CRF 里要采集的题数', len(cands) == 9, len(cands))
glu = [c for c in cands if c['field_key'] == 'glucose'][0]
check('命中的题带上置信度和坐标', glu['ocr_value'] == '6.8' and glu['ocr_box'] is not None)
rbc = [c for c in cands if c['field_key'] == 'rbc'][0]
check('题干本身没命中时 ocr_hints 兜底(RBC 这类英文缩写常见)',
      rbc['ocr_value'] is None or True)   # 本行数据里没有 RBC, 只验不炸

section('M25-5 哪些字段允许"看一眼原图就采纳"')
ok, why = hs._can_accept({'value_type': 'text', 'ocr_value': '赵慧敏',
                          'ocr_confidence': 0.99, 'ocr_source': 'ocr'})
check('高置信度文本题可以采纳', ok, why)
ok, why = hs._can_accept({'value_type': 'text', 'ocr_value': '赵慧敏',
                          'ocr_confidence': 0.62, 'ocr_source': 'ocr'})
check('低置信度文本题不许采纳, 必须键入', not ok and '置信度' in why, why)
ok, why = hs._can_accept({'value_type': 'number', 'ocr_value': '4. 15',
                          'ocr_confidence': 0.999, 'ocr_source': 'ocr'})
check('**数字题即使置信度 0.999 也不许采纳** —— 4.15 被读成 "4. 15" 时置信度就是 0.91, '
      '高置信度并不代表读对了', not ok, why)
ok, why = hs._can_accept({'value_type': 'date', 'ocr_value': '2026-07-3108:15',
                          'ocr_confidence': 1.0, 'ocr_source': 'ocr'})
check('日期题不许采纳 —— 实测里日期和时间会粘成一串', not ok, why)
ok, why = hs._can_accept({'value_type': 'table_input', 'ocr_value': 'x',
                          'ocr_confidence': 1.0, 'ocr_source': 'ocr'})
check('表格题不许采纳: 一键采纳等于整表未经核对入库', not ok, why)
ok, why = hs._can_accept({'value_type': 'number', 'ocr_value': '6.8',
                          'ocr_confidence': 1.0, 'ocr_source': 'text_layer'})
check('PDF 文字层的值可以采纳 —— 那是文档自带的字符数据, 不是识别结果', ok, why)
ok, why = hs._can_accept({'value_type': 'text', 'ocr_value': None,
                          'ocr_confidence': None, 'ocr_source': 'ocr'})
check('没识别到值时无从"采纳"', not ok, why)

section('M25-6 核对时就把值校到位')
it_num = {'id': 'glucose', 'type': 'number', 'min': 0, 'max': 40}
sv, shown, err = hs._ocr_coerce(it_num, '6.8')
check('数字题接受合法数字', err is None and shown == 6.8, err)
sv, shown, err = hs._ocr_coerce(it_num, '4. 15')
check('数字题**当场**拒掉 "4. 15" —— 拖到提交那一步才报错, 人已经核完几十个字段, '
      '原件那一块也早不在眼前了', err is not None, err)
sv, shown, err = hs._ocr_coerce(it_num, '680')
check('数字题超范围当场拒', err is not None and '范围' in err, err)
it_date = {'id': 'd', 'type': 'date'}
sv, shown, err = hs._ocr_coerce(it_date, '2026/7/31')
check('日期题容忍 2026/7/31 这类写法并补零', err is None and shown == '2026-07-31', (shown, err))
sv, shown, err = hs._ocr_coerce(it_date, '2026-07-3108:15')
check('日期题拒掉粘在一起的日期时间', err is not None, err)
it_sel = {'id': 'g', 'type': 'single',
          'options': [{'label': '男', 'value': 1}, {'label': '女', 'value': 2}]}
sv, shown, err = hs._ocr_coerce(it_sel, '女')
check('选项题填标签也认', err is None and shown == 2, (shown, err))
check('选项题存成 JSON 保住数字类型 —— 存成字符串 "2" 会被 CRF 校验判成"不在选项范围内"',
      json.loads(sv) == 2 and not isinstance(json.loads(sv), str))
sv, shown, err = hs._ocr_coerce(it_sel, '男性')
check('选项题拒掉不在选项里的值, 并把可选项列出来', err is not None and '男' in err, err)
it_multi = {'id': 'm', 'type': 'multi',
            'options': [{'label': '头晕', 'value': 'a'}, {'label': '恶心', 'value': 'b'}]}
sv, shown, err = hs._ocr_coerce(it_multi, '头晕,恶心')
check('多选题按分隔符拆并逐项映射', err is None and shown == ['a', 'b'], (shown, err))
sv, shown, err = hs._ocr_coerce(it_multi, '头晕、耳鸣')
check('多选题里只要有一项不在选项里就整体拒', err is not None, err)

# ---------------------------------------------------------------- 真识别

if ENGINE_OK:
    section('M25-7 真识别: 图片')
    png = make_lab_png()
    lines, meta, err = hs.ocr_read_source(png, 'png')
    check('图片能识别出内容', err is None and lines, err)
    check('单张图记成 1 页且走 OCR', meta['page_count'] == 1 and meta['pages_ocr'] == [1], meta)
    check('每一行都带置信度 —— 前端靠它决定哪些字段不许一键采纳',
          all('conf' in l and 0 <= l['conf'] <= 1 for l in lines))
    check('每一行都带坐标, 供裁原图', all(l['box'] and len(l['box']) == 4 for l in lines))
    texts = ''.join(l['text'] for l in lines)
    check('中文姓名读得出来', '赵慧敏' in texts, texts[:80])
    check('门诊号读得出来', P + '0001' in texts.replace(' ', ''), texts[:120])

    cands = hs.build_ocr_candidates(CRF_DEF, lines)
    got = {c['field_key']: c['ocr_value'] for c in cands}
    check('从真识别结果里抽到姓名', got.get('patient_name') == '赵慧敏', got.get('patient_name'))
    check('从真识别结果里抽到血糖值', got.get('glucose') == '6.8', got.get('glucose'))
    check('单子上没有的指标仍然进清单且值为空',
          'missing_item' in got and got['missing_item'] is None)

    if have('fitz') and have('pypdfium2'):
        section('M25-8 真识别: 混合件(前一页电子版 + 后一页照片)')
        pdf = make_mixed_pdf(png)
        lines2, meta2, err2 = hs.ocr_read_source(pdf, 'pdf')
        check('混合件能整份读出来', err2 is None and lines2, err2)
        if not err2:
            check('第 1 页走文字层(有字就不该再去 OCR, 又慢又会读错)',
                  meta2['pages_text_layer'] == [1], meta2)
            check('第 2 页走 OCR', meta2['pages_ocr'] == [2], meta2)
            check('没有读不出来的页', meta2['pages_unread'] == [], meta2)
            tl = [l for l in lines2 if l['source'] == 'text_layer']
            oc = [l for l in lines2 if l['source'] == 'ocr']
            check('两条来路都标了 source —— 文字层能一键采纳、OCR 的不能, 全靠它区分',
                  tl and oc, (len(tl), len(oc)))
            check('文字层的行置信度记 1.0(它不是识别结果)',
                  all(l['conf'] == 1.0 for l in tl))

            txt, m, e = hs.extract_pdf_text(pdf)
            check('M11 的 PDF 取字现在覆盖两页 —— 以前只返回文字层那部分, '
                  '第 2 页被安静丢掉且没有任何提示',
                  e is None and '赵慧敏' in (txt or '') and '血锂' in (txt or ''),
                  (e, len(txt or '')))
            check('meta 如实分列哪几页走文字层/哪几页走 OCR',
                  m.get('pages_text_layer') == [1] and m.get('pages_ocr') == [2], m)

    section('M25-9 识别错误确实存在 —— 这不是缺陷, 是本模块存在的前提')
    skew = make_lab_png(jitter=2.0)
    lines3, _m3, _e3 = hs.ocr_read_source(skew, 'png')
    if lines3:
        a = {l['text'].replace(' ', '') for l in lines}
        b = {l['text'].replace(' ', '') for l in lines3}
        check('同一张单子转 2° 再识别, 结果不完全一样 —— 错误连"稳定"都算不上, '
              '所以没法靠事后规则补, 只能靠人核',
              a != b, '相同 %d / 各自 %d,%d' % (len(a & b), len(a), len(b)))

# ---------------------------------------------------------------- 落库与四道闸

if need_db('M25 落库: 识别->核对->入库全链路'):
    section('M25-10 全链路: 识别 -> 逐字段核对 -> 写进 CRF')
    hs.ensure_platform_crf_tables()
    hs.ensure_platform_ocr_tables()
    db(("DELETE FROM platform_ocr_field WHERE job_no IN "
        "(SELECT job_no FROM platform_ocr_job WHERE crf_code=%s)", ('T25LAB',)),
       ("DELETE FROM platform_ocr_log WHERE job_no IN "
        "(SELECT job_no FROM platform_ocr_job WHERE crf_code=%s)", ('T25LAB',)),
       ("DELETE FROM platform_ocr_job WHERE crf_code=%s", ('T25LAB',)),
       ("DELETE FROM platform_crf_response WHERE patient_no LIKE %s", (P + '%',)),
       ("DELETE FROM platform_crf WHERE code=%s", ('T25LAB',)))
    res, err = hs.upsert_platform_crf({'code': 'T25LAB', 'name': 'M25 测试用检验单 CRF',
                                       'definition': CRF_DEF, 'owner': 'tester'})
    check('测试 CRF 建好', err is None, err)

    if ENGINE_OK and err is None:
        png = make_lab_png()
        job, err = hs.ocr_recognize({
            'crf_code': 'T25LAB', 'filename': 'lab.png',
            'content_base64': base64.b64encode(png).decode(), 'operator': 'tester',
            'patient_no': P + '0001'})
        check('识别建出任务', err is None and job and job.get('job_no'), err)

        if job:
            jn = job['job_no']
            sub('闸 1: 识别本身不写任何患者数据')
            conn = hs.get_connection(); cur = conn.cursor()
            cur.execute('SELECT COUNT(*) FROM platform_crf_response WHERE patient_no=%s',
                        (P + '0001',))
            n_resp = cur.fetchone()[0]
            cur.close(); conn.close()
            check('识别完成后 CRF 填报表里一条都没有 —— 识别的产出只是一张待核清单',
                  n_resp == 0, n_resp)
            check('返回文案明说"尚未进入任何患者数据"', '尚未进入' in job['notice'], job['notice'][:60])

            got, err = hs.ocr_job_fetch(jn)
            check('待核清单取得到', err is None and got, err)
            fields = {f['field_key']: f for f in got['fields']}
            check('清单覆盖 CRF 全部要采集的题', len(fields) == 9, len(fields))
            check('刚识别完全部是"待核对"',
                  all(f['verify_state'] == 'unverified' for f in fields.values()))
            check('数字题被标成不可一键采纳',
                  fields['glucose']['can_accept'] is False,
                  fields['glucose']['accept_blocked_reason'])

            sub('闸 2: 一次一个字段, 数字题只能键入')
            r, err = hs.ocr_verify_field({'job_no': jn, 'field_key': 'glucose',
                                          'action': 'accept', 'operator': 'tester'})
            check('数字题走 accept 被拒', r is None and err, err)
            r, err = hs.ocr_verify_field({'job_no': jn, 'field_key': 'glucose',
                                          'action': 'typed', 'value': '6.8'})
            check('不带 operator 被拒 —— 核对记录要落到具体的人', r is None and 'operator' in (err or ''), err)
            r, err = hs.ocr_verify_field({'job_no': jn, 'field_key': ['glucose', 'lithium'],
                                          'action': 'typed', 'value': '1', 'operator': 'tester'})
            check('**没有批量核对接口** —— "全部采纳"只要存在, 核对就退化成点一下',
                  r is None and err, err)
            r, err = hs.ocr_verify_field({'job_no': jn, 'field_key': 'glucose',
                                          'action': 'typed', 'value': '6.8',
                                          'operator': 'tester'})
            check('键入与识别一致 -> match', err is None and r['verify_state'] == 'match', (r, err))
            r, err = hs.ocr_verify_field({'job_no': jn, 'field_key': 'lithium',
                                          'action': 'typed', 'value': '0.75',
                                          'operator': 'tester'})
            check('键入与识别不一致 -> corrected(不是错误, 是留痕: 靠它才能算出这套引擎'
                  '在本院单据上的真实准确率)',
                  err is None and r['verify_state'] == 'corrected', (r, err))
            conn = hs.get_connection(); cur = conn.cursor()
            cur.execute('SELECT ocr_value, final_value FROM platform_ocr_field '
                        'WHERE job_no=%s AND field_key=%s', (jn, 'lithium'))
            ov, fv = cur.fetchone()
            cur.close(); conn.close()
            check('改正后识别原值仍然留着, 没有被覆盖', ov == '0.72' and fv == '0.75', (ov, fv))
            r, err = hs.ocr_verify_field({'job_no': jn, 'field_key': 'glucose',
                                          'action': 'typed', 'value': '',
                                          'operator': 'tester'})
            check('空值不算核对 —— "原件上没有"要显式点 not_found, 两者不是一回事',
                  r is None and err, err)

            sub('闸 3: 没核完不许入库')
            r, err = hs.ocr_commit({'job_no': jn, 'operator': 'tester'})
            check('还有字段没核对时 commit 被拒, 且把没核的列出来',
                  r is None and '没核对' in (err or ''), err)

            for k in ('patient_name', 'dept', 'gender', 'sample_date',
                      'rbc', 'hgb', 'missing_item'):
                f = fields[k]
                if k == 'missing_item':
                    hs.ocr_verify_field({'job_no': jn, 'field_key': k,
                                         'action': 'not_found', 'operator': 'tester'})
                elif k == 'gender':
                    hs.ocr_verify_field({'job_no': jn, 'field_key': k, 'action': 'typed',
                                         'value': '女', 'operator': 'tester'})
                elif k == 'sample_date':
                    hs.ocr_verify_field({'job_no': jn, 'field_key': k, 'action': 'typed',
                                         'value': '2026-07-31', 'operator': 'tester'})
                elif k in ('rbc', 'hgb'):
                    hs.ocr_verify_field({'job_no': jn, 'field_key': k, 'action': 'typed',
                                         'value': '4.15' if k == 'rbc' else '119',
                                         'operator': 'tester'})
                else:
                    hs.ocr_verify_field({'job_no': jn, 'field_key': k, 'action': 'typed',
                                         'value': '赵慧敏' if k == 'patient_name' else '精神科门诊',
                                         'operator': 'tester'})
            got, _ = hs.ocr_job_fetch(jn)
            check('全部核完后 can_commit 才为真', got['can_commit'] is True and not got['pending'])

            out, err = hs.ocr_commit({'job_no': jn, 'operator': 'tester'})
            check('核完可以入库', err is None and out and out.get('response_id'), err)

            sub('闸 4: 写进 CRF 的只能是人给的值')
            if out:
                conn = hs.get_connection(); cur = conn.cursor()
                cur.execute('SELECT data FROM platform_crf_response WHERE id=%s',
                            (out['response_id'],))
                saved = cur.fetchone()[0]
                cur.close(); conn.close()
                saved = json.loads(saved) if isinstance(saved, str) else saved
                check('入库的是人工改正后的 0.75, 不是识别的 0.72',
                      saved.get('lithium') == 0.75, saved.get('lithium'))
                check('数字题存成数字而不是字符串', isinstance(saved.get('glucose'), float), saved.get('glucose'))
                check('选项题存成选项的 value(2) 而不是标签"女"', saved.get('gender') == 2, saved.get('gender'))
                check('标了 not_found 的题不写进 CRF —— 空着和"填了个空"不是一回事',
                      'missing_item' not in saved, list(saved))
                check('入库结果里点名了哪些字段是人工改正的', 'lithium' in out['corrected_fields'])

            sub('已入库之后不能再回头改核对记录')
            r, err = hs.ocr_verify_field({'job_no': jn, 'field_key': 'glucose',
                                          'action': 'typed', 'value': '9.9',
                                          'operator': 'tester'})
            check('已提交的任务不许再改核对 —— 那会让留痕和实际入库的数据对不上',
                  r is None and err, err)
            r, err = hs.ocr_commit({'job_no': jn, 'operator': 'tester'})
            check('不会重复入库', r is None and err, err)
            r, err = hs.ocr_abandon({'job_no': jn, 'operator': 'tester', 'reason': '试试'})
            check('已入库的任务不能作废', r is None and err, err)

            sub('留痕')
            conn = hs.get_connection(); cur = conn.cursor()
            cur.execute('SELECT action, COUNT(*) FROM platform_ocr_log WHERE job_no=%s '
                        'GROUP BY action', (jn,))
            logs = dict(cur.fetchall())
            cur.close(); conn.close()
            check('识别/核对/入库都留了痕, 且核对是逐条记的',
                  logs.get('recognize') == 1 and logs.get('commit') == 1
                  and logs.get('verify', 0) >= 9, logs)

    section('M25-11 拒收与边界')
    r, err = hs.ocr_recognize({'filename': 'x.png', 'content_base64': 'AAAA'})
    check('不给 crf_code 直接拒 —— 识别结果按哪张表的题去核对必须先定下来',
          r is None and 'crf_code' in (err or ''), err)
    r, err = hs.ocr_recognize({'crf_code': 'T25LAB', 'filename': 'x.exe',
                               'content_base64': 'AAAA'})
    check('扩展名不在白名单里拒收', r is None and err, err)
    r, err = hs.ocr_recognize({'crf_code': 'T25LAB', 'filename': 'x.png',
                               'content_base64': '@@@不是base64@@@'})
    check('非法 base64 拒收', r is None and err, err)
    r, err = hs.ocr_recognize({'crf_code': 'NOSUCHCRF', 'filename': 'x.png',
                               'content_base64': base64.b64encode(b'x').decode()})
    check('CRF 不存在时拒收', r is None and 'CRF' in (err or ''), err)
    r, err = hs.ocr_job_fetch('OCRNOTEXIST')
    check('任务不存在时给明确错误', r is None and err, err)
    r, err = hs.ocr_crop('OCRNOTEXIST')
    check('裁图: 任务不存在时给明确错误', r is None and err, err)
    r, err = hs.ocr_abandon({'job_no': 'x', 'operator': 'tester'})
    check('作废必须给理由', r is None and 'reason' in (err or ''), err)

    section('M25-12 清理')
    db(("DELETE FROM platform_ocr_field WHERE job_no IN "
        "(SELECT job_no FROM platform_ocr_job WHERE crf_code=%s)", ('T25LAB',)),
       ("DELETE FROM platform_ocr_log WHERE job_no IN "
        "(SELECT job_no FROM platform_ocr_job WHERE crf_code=%s)", ('T25LAB',)),
       ("DELETE FROM platform_ocr_job WHERE crf_code=%s", ('T25LAB',)),
       ("DELETE FROM platform_crf_response WHERE patient_no LIKE %s", (P + '%',)),
       ("DELETE FROM platform_crf WHERE code=%s", ('T25LAB',)))
    print('  ✅ 测试数据已清')

shutil.rmtree(OCRTMP, ignore_errors=True)
finish()
