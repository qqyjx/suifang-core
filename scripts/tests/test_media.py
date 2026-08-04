#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§2.1(3) CRF 音视频指导文件。

这一块的风险全在**播放**上, 不在上传上: M17 的资料下载走 attachment, 浏览器不渲染,
里面装什么都无所谓; 而指导片要 inline 播给填表的人看, 浏览器会解析这个字节流。
所以断言重心是"扩展名说了不算, 文件头说了才算"。

    HARNESS_NO_DB=1 python3 scripts/tests/test_media.py   # 只跑不碰库的部分
"""
import os
import sys
import base64
import tempfile
import shutil

DOCTMP = tempfile.mkdtemp(prefix='suifang_mediatest_')
os.environ['PLATFORM_DOC_DIR'] = DOCTMP

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import *          # noqa: F401,F403
from _harness import hs, check, section, sub, finish, db, need_db

P = 'TMED'
b64 = lambda raw: base64.b64encode(raw).decode()

# 各容器最小可辨识样本。只要文件头对 —— 这一层验的就是文件头。
MP4 = b'\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41' + b'\x00' * 64
WEBM = b'\x1a\x45\xdf\xa3' + b'\x00' * 64
MP3 = b'ID3\x03\x00\x00\x00\x00\x00\x00' + b'\x00' * 64
OGG = b'OggS\x00\x02' + b'\x00' * 64
WAV = b'RIFF\x24\x00\x00\x00WAVEfmt ' + b'\x00' * 64

section('MED-1 文件头说了算, 扩展名说了不算')
for raw, ext, label in ((MP4, 'mp4', 'MP4'), (WEBM, 'webm', 'WebM'), (MP3, 'mp3', 'MP3'),
                        (OGG, 'ogg', 'Ogg'), (WAV, 'wav', 'WAV')):
    mime, err = hs._sniff_media(raw, ext)
    check('%s 认得出来 -> %s' % (label, mime), err is None and mime, err)

sub('假装成音视频的东西一律拒')
EVIL = b'<html><script>alert(document.cookie)</script></html>'
for raw, ext, why in (
        (EVIL, 'mp4', '.mp4 里装 HTML'),
        (EVIL, 'mp3', '.mp3 里装 HTML'),
        (b'%PDF-1.4\n', 'mp4', 'PDF 改名成 .mp4'),
        (b'\x89PNG\r\n\x1a\n', 'webm', 'PNG 改名成 .webm'),
        (b'', 'mp4', '空文件')):
    mime, err = hs._sniff_media(raw, ext)
    check('%s 被拒 —— 它要 inline 发回浏览器, 只看扩展名就是让浏览器自己猜内容' % why,
          mime is None and err, mime)
mime, err = hs._sniff_media(MP4, 'exe')
check('扩展名不在音视频白名单里直接拒', mime is None and err)
mime, err = hs._sniff_media(MP4, 'webm')
check('容器和扩展名对不上也拒(MP4 数据配 .webm 名)', mime is None and err)

section('MED-2 Range: 不支持的话进度条拖不动, 有的浏览器干脆不给播')
check('bytes=0-99 -> (0,99)', hs._parse_range('bytes=0-99', 1000) == (0, 99))
check('开区间 bytes=500- 一直取到末尾', hs._parse_range('bytes=500-', 1000) == (500, 999))
check('bytes=-200 表示最后 200 字节', hs._parse_range('bytes=-200', 1000) == (800, 999))
check('结束位置超过文件长度时截到末尾', hs._parse_range('bytes=0-99999', 1000) == (0, 999))
check('起始位置越界返回 None(应答 200 整段, 而不是发一段空的)',
      hs._parse_range('bytes=5000-', 1000) is None)
check('没有 Range 头时返回 None', hs._parse_range(None, 1000) is None)
check('乱写的 Range 不认', hs._parse_range('bytes=abc', 1000) is None
      and hs._parse_range('items=0-1', 1000) is None)

if need_db('音视频入库 / 播放 / 挂到 CRF'):
    section('MED-3 上传与取用')
    hs.ensure_platform_doc_tables()
    hs.ensure_platform_crf_tables()
    db(("DELETE FROM platform_document WHERE code LIKE %s", (P + '%',)),
       ("DELETE FROM platform_crf WHERE code LIKE %s", (P + '%',)))

    r, err = hs.upload_media({'code': P + 'V1', 'title': '血压测量操作示范',
                              'filename': '示范.mp4', 'content_base64': b64(MP4),
                              'uploader': 'tester'})
    check('合法 MP4 传得上去', err is None and r and r['version'] == '1', err)
    check('返回里直接给出播放地址', r and 'play_url' in r, (r or {}).get('play_url'))
    check('落在资料库里, 类型是 media', r and r.get('doc_type', 'media') == 'media')

    r2, err = hs.upload_media({'code': P + 'BAD', 'title': '伪装的',
                               'filename': '坏东西.mp4', 'content_base64': b64(EVIL)})
    check('文件头不对的传不上去 —— 挡在入库前, 而不是等播放时才发现',
          r2 is None and err and '文件头' in err, err)

    raw, meta, err = hs.fetch_media_bytes(P + 'V1', '1')
    check('取回来的字节和传上去的一致', err is None and raw == MP4, err)
    check('取回时给出用于播放的 mime', meta and meta.get('mime') == 'video/mp4', meta)

    sub('取用时再验一次头 —— 上传时对不代表现在还对')
    docres, _ = hs.upload_document({'code': P + 'PDF', 'title': '一份 PDF', 'doc_type': 'other',
                                    'filename': 'x.pdf', 'content_base64': b64(b'%PDF-1.4\ntest')})
    raw3, meta3, err3 = hs.fetch_media_bytes(P + 'PDF', '1')
    check('资料库里的非音视频不能当音视频播', raw3 is None and err3, err3)

    section('MED-4 挂到 CRF 上')
    DEF = {'items': [{'id': 'bp', 'text': '收缩压', 'type': 'number'}]}
    res, err = hs.upsert_platform_crf({
        'code': P + 'CRF', 'name': '带指导片的表', 'definition': DEF,
        'media': [{'code': P + 'V1', 'version': '1', 'title': '测量示范'}]})
    check('带 media 的 CRF 存得下', err is None and res, err)

    got, err = hs.query_platform_crfs(code=P + 'CRF', with_definition=True)
    m = (got['crfs'][0].get('media') or []) if got and got.get('crfs') else []
    check('存下来的 media 带着播放地址与 mime', m and m[0].get('play_url') and m[0].get('mime'),
          m)
    check('标题按传进来的走', m and m[0]['title'] == '测量示范', m)

    sub('引用查不实就不让存')
    res, err = hs.upsert_platform_crf({
        'code': P + 'CRF2', 'name': 'x', 'definition': DEF,
        'media': [{'code': 'NOSUCHMEDIA', 'version': '1'}]})
    check('引用一份不存在的资料 -> 拒 —— 存下来的话填表页上会有个点了没反应的播放器',
          res is None and err and '不存在' in err, err)
    res, err = hs.upsert_platform_crf({
        'code': P + 'CRF3', 'name': 'x', 'definition': DEF,
        'media': [{'code': P + 'PDF', 'version': '1'}]})
    check('引用一份 PDF 当音视频 -> 拒', res is None and err, err)
    res, err = hs.upsert_platform_crf({
        'code': P + 'CRF4', 'name': 'x', 'definition': DEF, 'media': 'not-a-list'})
    check('media 不是数组 -> 拒, 并给出正确写法', res is None and err and 'code' in err, err)
    res, err = hs.upsert_platform_crf({
        'code': P + 'CRF5', 'name': 'x', 'definition': DEF,
        'media': [{'code': P + 'V1'} for _ in range(hs.CRF_MEDIA_MAX + 1)]})
    check('挂太多 -> 拒(挂一堆片子, 填表的人一个也不会看)',
          res is None and err and '最多' in err, err)

    sub('清理')
    db(("DELETE FROM platform_document WHERE code LIKE %s", (P + '%',)),
       ("DELETE FROM platform_crf WHERE code LIKE %s", (P + '%',)))
    print('  ✅ 测试数据已清')

shutil.rmtree(DOCTMP, ignore_errors=True)
finish()
