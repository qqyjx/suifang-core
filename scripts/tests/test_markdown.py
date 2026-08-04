#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宣教材料的排版与配图 (§2.3 文本编辑、内容布局调整)。

走 Markdown + 服务端转义渲染, 不存 HTML。这个套件守两条:

1. **库里存进去的东西不可能变成标签。** 渲染顺序是"先整体转义, 再生成我们认识的
   那几种记号"。任何一次改动如果把顺序倒过来, 或者引入"把原文当标记插进去"的写法,
   这里的注入用例就会红。

2. **图片只能指向本平台。** 外部图片地址会让每个打开这份材料的患者向对方发一次
   请求, 对方由此知道有人在什么时候看了它 —— 这是能反推患者行为的。

    HARNESS_NO_DB=1 python3 scripts/tests/test_markdown.py
"""
import os
import sys
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import *          # noqa: F401,F403
from _harness import hs, check, section, sub, finish

R = hs.render_markdown_safe

section('MD-1 注入: 存进去的东西不能变成标签')
# 判据不是"输出里有没有 onerror 这几个字" —— 转义之后原文照样含这几个字, 那是对的。
# 判据是: **输出里出现的标签, 只能是我们自己生成的那几种**, 且这些标签上不带
# 事件处理器、地址里不带伪协议。这条比逐个 payload 打黑名单强得多:
# 没见过的新 payload 只要能变出标签来, 这里就会红。
ALLOWED_TAGS = {'p', 'br', 'h2', 'h3', 'h4', 'h5', 'ul', 'li', 'strong', 'em',
                'code', 'hr', 'blockquote', 'a', 'img'}


def live_tags(html):
    return set(t.lower() for t in re.findall(r'<\s*/?\s*([a-zA-Z][a-zA-Z0-9]*)', html))


CASES = [
    ('<script>alert(1)</script>', '裸 script 标签'),
    ('<img src=x onerror=alert(1)>', 'img onerror'),
    ('<a href="javascript:alert(1)">点我</a>', 'javascript: 链接'),
    ('<svg/onload=alert(1)>', 'svg onload'),
    ('<iframe src="https://evil.example"></iframe>', 'iframe'),
    ('<style>body{display:none}</style>', 'style 标签'),
    ('"><script>alert(1)</script>', '先闭合属性再插标签'),
    ('<div onclick="x">文字</div>', '带事件处理器的 div'),
    ('<a href=" javascript:alert(1)">空格绕过</a>', 'href 前导空格'),
    ('![x](java\tscript:alert(1))', '伪协议里插制表符'),
    ('<<script>script>alert(1)<</script>/script>', '嵌套闭合绕过'),
    ('&lt;script&gt;alert(1)&lt;/script&gt;', '预先转义好的 script(不能被二次解码)'),
]
for payload, label in CASES:
    html, _n = R(payload)
    tags = live_tags(html)
    extra = tags - ALLOWED_TAGS
    has_handler = re.search(r'<[^>]*\son[a-z]+\s*=', html, re.I)
    has_proto = re.search(r'(href|src)\s*=\s*"[^"]*(javascript|data|vbscript)\s*:', html, re.I)
    check('%s 变不出标签' % label,
          not extra and not has_handler and not has_proto,
          '多出的标签=%s 事件=%s 伪协议=%s' % (sorted(extra), bool(has_handler), bool(has_proto)))

html, _ = R('<script>alert(1)</script>')
check('被转义之后原文还看得见(是转义不是删除 —— 删了写稿的人会以为自己没保存上)',
      '&lt;script&gt;' in html, html[:80])
html, _ = R('正常正文 **加粗** 和 [链接](/api/platform/media?code=A&version=1)')
check('正常内容只产出白名单里的标签',
      not (live_tags(html) - ALLOWED_TAGS), sorted(live_tags(html)))

section('MD-2 正常排版认得出来')
html, _ = R('# 一级标题\n\n正文一段。\n\n## 二级标题\n- 第一条\n- 第二条\n\n**加粗** 和 *斜体* 和 `代码`')
for frag, label in (('<h2>一级标题</h2>', '标题'), ('<p>正文一段。</p>', '段落'),
                    ('<ul>', '列表'), ('<li>第一条</li>', '列表项'),
                    ('<strong>加粗</strong>', '加粗'), ('<em>斜体</em>', '斜体'),
                    ('<code>代码</code>', '行内代码')):
    check('%s 渲染正确' % label, frag in html, html[:120])
html, _ = R('> 这是一段提示\n\n---\n\n后面')
check('引用块', '<blockquote>' in html)
check('分隔线', '<hr>' in html)
html, _ = R('这里有 *星号* 但 a*b*c 不该被当成斜体')
check('单词中间的星号不当斜体 —— 不然剂量写法 5*2 会被吃掉',
      html.count('<em>') == 1, html)

section('MD-3 图片与链接只能指向本平台')
ok_src = '/api/platform/media?code=IMG1&version=1'
# 属性里的 & 写成 &amp; 是对的 —— 那是 HTML 属性里 & 的正确写法, 浏览器取图时会解回 &。
# 断言要按渲染后的形态写, 不能按原始 URL 写。
ok_rendered = ok_src.replace('&', '&amp;')
html, notes = R('![血糖仪示意](%s)' % ok_src)
check('本平台图片正常渲染', '<img src="%s"' % ok_rendered in html, html[:120])
check('带 alt 文本', 'alt="血糖仪示意"' in html)
check('限宽, 不撑破版面', 'max-width:100%' in html)

for bad_src, label in (('https://evil.example/track.png', '外部 https 图片'),
                       ('http://1.2.3.4/x.gif', '外部 IP 图片'),
                       ('//evil.example/x.png', '协议相对地址'),
                       ('javascript:alert(1)', 'javascript: 伪协议'),
                       ('/etc/passwd', '本机其他路径'),
                       ('/api/platform/export?kind=all', '本平台但不是媒体接口')):
    html, notes = R('![x](%s)' % bad_src)
    dropped = any(n['kind'] == 'external_image_dropped' for n in notes)
    check('%s 被移除且记账 —— 外部地址会让每个看材料的患者向对方发一次请求, '
          '对方由此知道有人在什么时候看了它' % label,
          bad_src not in html and dropped, html[:90])

html, notes = R('[看这里](https://evil.example)')
check('外部链接降级成纯文字, 不留 a 标签',
      '<a ' not in html and '看这里' in html and 'evil.example' not in html, html[:90])
html, _ = R('[本平台链接](%s)' % ok_src)
check('本平台链接保留', '<a href="%s">' % ok_rendered in html, html[:120])

section('MD-4 预览接口')
info, err = hs.edu_preview('# 标题\n\n正文\n\n![图](https://evil.example/x.png)')
check('预览出得来', err is None and info and info['html'], err)
check('**把被丢掉的外部图片列出来** —— 悄悄丢掉的话, 写稿的人会以为是浏览器的问题',
      info['dropped_external'], info.get('dropped_external'))
check('说明里讲清了为什么丢', '发一次请求' in info['note'], info['note'][:80])
info, err = hs.edu_preview(None)
check('body 必填', info is None and err, err)
info, err = hs.edu_preview('')
check('空正文能渲染成空(不是报错 —— 新建材料时正文本来就是空的)',
      err is None and info['html'] == '', (err, (info or {}).get('html')))

sub('超长正文截断而不是崩掉')
info, err = hs.edu_preview('正文\n' * 40000)
check('超长截断并记账', err is None and
      any(n['kind'] == 'truncated' for n in info['notes']), (info or {}).get('notes'))

section('MD-5 图片走的是和音视频同一条已验过文件头的路')
check('png/jpg/gif/webp 都在媒体白名单里',
      all(e in hs.MEDIA_TYPES for e in ('png', 'jpg', 'jpeg', 'gif', 'webp')))
check('**不收 svg** —— 它是 XML, 里面能塞脚本, 和"图片"不是一类东西',
      'svg' not in hs.MEDIA_TYPES)
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 40
GIF = b'GIF89a' + b'\x00' * 40
JPG = b'\xff\xd8\xff\xe0' + b'\x00' * 40
for raw, ext, label in ((PNG, 'png', 'PNG'), (GIF, 'gif', 'GIF'), (JPG, 'jpg', 'JPEG')):
    mime, err = hs._sniff_media(raw, ext)
    check('%s 按文件头认得出来 -> %s' % (label, mime), err is None and mime, err)
mime, err = hs._sniff_media(b'<svg onload=alert(1)>', 'png')
check('SVG 内容改名成 .png 被拒(文件头对不上)', mime is None and err, mime)
mime, err = hs._sniff_media(b'<html>x</html>', 'gif')
check('HTML 改名成 .gif 被拒', mime is None and err, mime)

finish()
