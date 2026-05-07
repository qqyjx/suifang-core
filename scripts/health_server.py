#!/usr/bin/env python3
"""
智能随访 - 可穿戴设备数据接收服务（Python 版，部署到 CentOS 7 公司服务器）

为什么不用 Node.js：CentOS 7 默认 GLIBC 太旧（2.17），mysql2 等 npm 包跑不起。
所以服务器端使用 Python + pymysql 标准库，避免依赖问题。

数据结构（一台设备一行 + 大 JSON 汇总）：
- wearable_device_data 表中每台设备只占 1 行
- data 列是大 JSON，按 10 类数据分组，每类是历史测量数组
- 新数据进来时 UPSERT：SELECT 现有行 → 解析 JSON → push 新测量 → UPDATE/INSERT

API 端点：
- GET  /api/status                       服务状态 + MySQL 连接
- GET  /api/data                         查询所有设备的大 JSON (可选 ?patientNo= 过滤大 JSON 内的记录)
- POST /api/health-data                  写入一条体征记录（自动 UPSERT 到大 JSON 数组）;
                                         患者标识传 patientNo, 写入每条记录的 '门诊号' 字段
- POST /api/device/register              按 mac (优先) 或 device_sign UPSERT 到 wearable_device，返回 deviceId
- POST /api/device/merge                 合并 wearable_device_data 两行: {fromDeviceId, toDeviceId}
- GET  /api/device/by-sign?sign=...      按 sign 查 wearable_device（不创建）
- DELETE /api/device/:id                 删 wearable_device 一行 + 联动删该 deviceId 的所有数据

5.06-v9 决定: 不动 wearable_device_data schema (无 wx_openid / patient_no 列),
             患者标识统一写在大 JSON 每条记录的 '门诊号' 字段里, 切片仍按 deviceId 一台设备一行.
             v7/v8 残留的 wx.login / ble_event 端点和函数保留在文件中但不在启动时激活,
             如需启用请阅读 main 块中的注释.

部署：scp 本文件到 192.168.4.104:/opt/suifang/health_server.py，systemd 启动
"""
import os
import json
import re
import datetime
import traceback
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import pymysql

# ============ 配置 ============
PORT = 3000
DB_CONFIG = {
    'host': '192.168.4.174',
    'port': 3306,
    'user': 'developer',
    'password': 'DePer!$12967',
    'database': 'h6dp_suifang',
    'charset': 'utf8mb4',
    'connect_timeout': 5,
    'autocommit': True,
}
DEFAULT_DEVICE_ID = 1

# ============ 微信小程序登录配置 (5.06-v7) ============
# WX_APPSECRET 必须从 mp.weixin.qq.com → 开发管理 → 开发设置 取 (敏感, 不入仓);
# 通过 systemd Environment= 或环境变量注入.
WX_APPID = os.environ.get('WX_APPID') or 'wxbc5453a4c53dbee8'
WX_APPSECRET = os.environ.get('WX_APPSECRET') or ''

# 数据类型 → 中文键名（10 类，未含 daily）
TYPE_TO_CHINESE = {
    'heartRate':       '心率',
    'bloodOxygen':     '血氧',
    'bloodPressure':   '血压',
    'temperature':     '体温',
    'bloodGlucose':    '血糖',
    'bloodLiquid':     '血液成分',
    'bodyComposition': '身体成分',
    'ecg':             '心电',
    'step':            '步数',
    'sleep':           '睡眠',
    'daily':           '日综合',
}

# ============ 数据库连接 ============
def get_connection():
    return pymysql.connect(**DB_CONFIG)

def test_db():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM wearable_device_data')
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return True, count
    except Exception as e:
        return False, str(e)

def ensure_ble_event_table():
    """5.06-v8: 创建 ble_event 表 (idempotent), 收客户端蓝牙连接质量埋点.

    用途:
        - 聚合 connect_success / connect_failed / handshake_failed 等事件
        - 反向定位线上失败热点 (notify 失败率 / 哪个微信 openid 高失败)
        - 长期数据驱动后续优化 (而不是盲改)

    字段 (字段名贴近 health_server 风格):
        id              主键
        wx_openid       关联用户 (可空, 客户端 wxOpenid 未就绪时)
        device_id       关联设备 (可空, 连接尚未 register 时)
        mac             手表 MAC (可空)
        event_type      事件类型: connect_success / connect_failed / handshake_failed /
                        reconnect_success / reconnect_failed / heartbeat_timeout /
                        adapter_off / adapter_on / connection_lost
        success         布尔, 1/0
        duration_ms     连接耗时 (从用户点击到事件结束)
        notify_enabled  forceEnableNotify 实际 enable 数
        notify_total    forceEnableNotify 总数
        password_calls  密钥核准实际成功调用次数
        error_msg       失败时具体原因 (可空)
        platform        ios / android
        build_tag       客户端 ENV.BUILD_TAG (5.06-v8)
        created_at      服务端写入时刻
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ble_event (
                id INT AUTO_INCREMENT PRIMARY KEY,
                wx_openid VARCHAR(64) DEFAULT NULL,
                device_id INT DEFAULT NULL,
                mac VARCHAR(32) DEFAULT NULL,
                event_type VARCHAR(32) NOT NULL,
                success TINYINT(1) DEFAULT 0,
                duration_ms INT DEFAULT NULL,
                notify_enabled INT DEFAULT NULL,
                notify_total INT DEFAULT NULL,
                password_calls INT DEFAULT NULL,
                error_msg VARCHAR(255) DEFAULT NULL,
                platform VARCHAR(16) DEFAULT NULL,
                build_tag VARCHAR(32) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_openid_time (wx_openid, created_at),
                INDEX idx_event_time (event_type, created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        print('[启动] ble_event 表已就绪')
        cur.close()
    except Exception as e:
        print('[启动] ensure_ble_event_table 失败:', e)
    finally:
        conn.close()

def insert_ble_event(payload):
    """写入一条蓝牙连接质量埋点. 字段全可空, 仅 event_type 必填."""
    event_type = payload.get('eventType')
    if not event_type:
        return None, '缺少 eventType'
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO ble_event
              (wx_openid, device_id, mac, event_type, success, duration_ms,
               notify_enabled, notify_total, password_calls, error_msg, platform, build_tag)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            payload.get('wxOpenid') or None,
            payload.get('deviceId'),
            payload.get('mac') or None,
            event_type,
            1 if payload.get('success') else 0,
            payload.get('durationMs'),
            payload.get('notifyEnabled'),
            payload.get('notifyTotal'),
            payload.get('passwordCalls'),
            (payload.get('errorMsg') or None) and str(payload.get('errorMsg'))[:255],
            payload.get('platform') or None,
            payload.get('buildTag') or None,
        ))
        cur.close()
        return {'eventId': cur.lastrowid}, None
    except Exception as e:
        return None, 'insert_ble_event 失败: {}'.format(e)
    finally:
        conn.close()

def query_ble_event_stats(days=7):
    """5.06-v8: 聚合最近 N 天的事件类型计数 + 成功率.
    返回: { totalEvents, byType: [{eventType, count, successCount, successRate}] }
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT event_type, COUNT(*) AS total, SUM(success) AS succ
            FROM ble_event
            WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
            GROUP BY event_type
            ORDER BY total DESC
        """, (days,))
        by_type = []
        total_all = 0
        for row in cur.fetchall():
            evt, total, succ = row[0], row[1] or 0, int(row[2] or 0)
            total_all += total
            by_type.append({
                'eventType': evt,
                'count': total,
                'successCount': succ,
                'successRate': round(succ / total, 4) if total > 0 else 0,
            })
        cur.close()
        return {'days': days, 'totalEvents': total_all, 'byType': by_type}, None
    except Exception as e:
        return None, str(e)
    finally:
        conn.close()

def ensure_openid_column():
    """5.06-v7: 确保 wearable_device_data 表有 wx_openid 列 (idempotent).

    多患者轮流用同一台手表的需求 (入组研究): 按 (deviceId, wx_openid) 二维 key 切片,
    每个 (设备, 微信用户) 一行大 JSON. 历史行 wx_openid=NULL 视作"未分组"保留.

    注意不加 UNIQUE 约束: 历史 deviceId=4/6/7 三行都是 wx_openid=NULL, 加 UNIQUE 会冲突.
    只用普通复合 INDEX 加速查询, 唯一性靠 upsert_device_data 的 SELECT-UPSERT 流程保证.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'wearable_device_data' "
            "AND COLUMN_NAME = 'wx_openid'",
            (DB_CONFIG['database'],)
        )
        if cur.fetchone()[0] == 0:
            print('[启动] wearable_device_data.wx_openid 不存在, 添加中...')
            cur.execute('ALTER TABLE wearable_device_data ADD COLUMN wx_openid VARCHAR(64) DEFAULT NULL')
            try:
                cur.execute('ALTER TABLE wearable_device_data ADD INDEX idx_dev_openid (deviceId, wx_openid)')
            except Exception as e:
                print('[启动] idx_dev_openid 索引添加失败 (可忽略):', e)
            print('[启动] wearable_device_data.wx_openid 列添加完成')
        else:
            print('[启动] wearable_device_data.wx_openid 列已存在, 跳过 ALTER')
        cur.close()
    except Exception as e:
        print('[启动] ensure_openid_column 失败:', e)
    finally:
        conn.close()

def ensure_mac_column():
    """5.06-v6: 确保 wearable_device 表有 mac 列 (idempotent).

    为什么需要 mac 列: device_sign 是 'name_<MAC>' 复合, name 部分跨连接可能漂移
    ('(上次连接)' 后缀, 系统名修改等), 导致同一手表生成不同 sign 多行.
    单独的 mac 列 + 优先按 mac 查匹配, 保证一表一行.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'wearable_device' AND COLUMN_NAME = 'mac'",
            (DB_CONFIG['database'],)
        )
        if cur.fetchone()[0] == 0:
            print('[启动] wearable_device.mac 不存在, 添加中...')
            cur.execute('ALTER TABLE wearable_device ADD COLUMN mac VARCHAR(32) DEFAULT NULL')
            try:
                cur.execute('ALTER TABLE wearable_device ADD INDEX idx_mac (mac)')
            except Exception as e:
                print('[启动] mac 索引添加失败 (可忽略, 仅影响查询性能):', e)
            print('[启动] wearable_device.mac 列添加完成')
        else:
            print('[启动] wearable_device.mac 列已存在, 跳过 ALTER')
        cur.close()
    except Exception as e:
        print('[启动] ensure_mac_column 失败:', e)
    finally:
        conn.close()

# ============ 数据转换 ============
def classify_bp(systolic, diastolic):
    """血压风险分级（AHA 2017 标准）"""
    s, d = systolic or 0, diastolic or 0
    if s >= 180 or d >= 120: return '危急'
    if s >= 140 or d >= 90:  return '高血压2级'
    if s >= 130 or d >= 80:  return '高血压1级'
    if s >= 120 and d < 80:  return '偏高'
    return '正常'

def to_chinese_record(data_type, data, patient_no=None, recorded_at=None, uploaded_at=None):
    """单条测量 → 中文字段记录（含采集时间 + 上传时间 + 5.06-v9 门诊号）.

    recorded_at: 客户端 saveData 调用时刻 (用户实际测量时刻);
                 客户端 ISO 8601 字符串, 如 '2026-04-29T16:33:01.000Z'.
                 不传时回退到 server 收到 POST 的时刻.
    uploaded_at: server 收到 POST 的时刻 (UTC). 由 upsert_device_data 在调用
                 本函数前固定时刻, 多类型同一批用同一值.
    patient_no:  5.06-v9 新增. 患者门诊号 (客户端首页输入, 一台手表多患者轮流时
                 靠这个字段区分). 不传时不写本字段, 兼容老客户端 / 未输入场景.
                 不动表 schema, 仅在大 JSON 每条记录里加一个 '门诊号' 字段.
    """
    if data_type == 'heartRate':
        record = {'心率值': data.get('heartRate', 0), '心率状态': data.get('heartState', 0)}
    elif data_type == 'bloodOxygen':
        record = {'血氧饱和度': data.get('bloodOxygen', 0), '心率': data.get('heartRate', 0)}
    elif data_type == 'bloodPressure':
        record = {
            '高压': data.get('systolic', 0),
            '低压': data.get('diastolic', 0),
            '脉搏': data.get('heartRate', 0),
            '风险等级': classify_bp(data.get('systolic'), data.get('diastolic')),
        }
    elif data_type == 'temperature':
        record = {'体温': data.get('temperature', 0), '皮肤温度': data.get('skinTemperature', 0)}
    elif data_type == 'bloodGlucose':
        record = {'血糖值_mmol_L': data.get('bloodGlucose', 0), '餐态': data.get('mealState', '')}
    elif data_type == 'bloodLiquid':
        record = {
            '尿酸': data.get('uricAcid', 0),
            '胆固醇': data.get('cholesterol', 0),
            '甘油三酯': data.get('triacylglycerol', 0),
        }
    elif data_type == 'bodyComposition':
        record = {
            '体重': data.get('weight', 0),
            'BMI': data.get('bmi', 0),
            '体脂率': data.get('bodyFat', 0),
            '肌肉量': data.get('muscle', 0),
        }
    elif data_type == 'ecg':
        record = {
            '心率': data.get('heartRate', 0),
            '诊断': data.get('diseaseResult', ''),
            '波形采样点数': len(data.get('ecgWaveform', [])),
        }
    elif data_type == 'step':
        record = {
            '步数': data.get('step', 0),
            '卡路里': data.get('calorie', 0),
            '距离_米': data.get('distance', 0),
        }
    elif data_type == 'sleep':
        record = {
            '入睡时间': data.get('fallAsleepTime', ''),
            '醒来时间': data.get('wakeUpTime', ''),
            '深睡_分钟': data.get('deepSleepTime', 0),
            '浅睡_分钟': data.get('lightSleepTime', 0),
        }
    elif data_type == 'daily':
        record = dict(data)
    else:
        record = dict(data)
    # 采集时间: 优先用客户端 recordedAt (真实测量时刻); 缺省回退 server 收到时刻
    record['采集时间'] = recorded_at or datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
    # 上传时间: server 收到 POST 的时刻 (一定是 server 端时刻, 防客户端时钟错乱)
    record['上传时间'] = uploaded_at or datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
    # 5.06-v9: 门诊号 — 客户端首页输入的患者标识, 写入大 JSON 每条记录里.
    # 不动表 schema (仍按 deviceId 一行), 同台设备多患者数据靠每条 '门诊号' 字段区分.
    # 客户端没传 (老版本 / 没输入) 时不写, 兼容历史数据.
    if patient_no:
        record['门诊号'] = patient_no
    return record

# ============ UPSERT 大 JSON 逻辑 ============
def upsert_device_data(device_id, data_type, data, patient_no=None, recorded_at=None, uploaded_at=None):
    """一台设备一行：SELECT-merge-UPSERT.

    5.06-v9: 切片回到 v6 风格 — 仅按 deviceId 一维 (不动表 schema).
    多患者轮流用同一台手表的区分: 在大 JSON 每条记录里塞 '门诊号' 字段
    (调用方查询时自己按门诊号过滤数组). 一台手表所有患者数据共享同一行,
    医院流程上靠"换人时切换门诊号"保证语义清晰.
    """
    chinese_key = TYPE_TO_CHINESE.get(data_type)
    if not chinese_key:
        return None, '未知数据类型: {}'.format(data_type)

    new_record = to_chinese_record(data_type, data, patient_no=patient_no,
                                    recorded_at=recorded_at, uploaded_at=uploaded_at)
    conn = get_connection()
    try:
        cur = conn.cursor()
        # v9: 仅按 deviceId 切片 (与 v6 一致, 不依赖 wx_openid 列存在).
        cur.execute(
            'SELECT id, data FROM wearable_device_data WHERE deviceId = %s LIMIT 1',
            (device_id,)
        )
        row = cur.fetchone()

        big_json = {}
        if row and row[1]:
            try:
                big_json = json.loads(row[1])
                if not isinstance(big_json, dict):
                    big_json = {}
            except json.JSONDecodeError:
                big_json = {}

        if not isinstance(big_json.get(chinese_key), list):
            big_json[chinese_key] = []
        big_json[chinese_key].append(new_record)

        big_json_str = json.dumps(big_json, ensure_ascii=False)
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if row:
            cur.execute(
                'UPDATE wearable_device_data SET data = %s, createTime = %s WHERE id = %s',
                (big_json_str, now_str, row[0])
            )
            result = {
                'action': 'update',
                'rowId': row[0],
                'type': chinese_key,
                'totalTypes': len(big_json),
                'count': len(big_json[chinese_key]),
                'patientNo': patient_no,
            }
        else:
            # v9: INSERT 仅写 (deviceId, data, createTime), 不依赖 wx_openid 列
            cur.execute(
                'INSERT INTO wearable_device_data (deviceId, data, createTime) '
                'VALUES (%s, %s, %s)',
                (device_id, big_json_str, now_str)
            )
            result = {
                'action': 'insert',
                'rowId': cur.lastrowid,
                'type': chinese_key,
                'totalTypes': len(big_json),
                'count': 1,
                'patientNo': patient_no,
            }
        cur.close()
        return result, None
    finally:
        conn.close()

# ============ 微信 jscode2session (5.06-v7) ============
def wx_jscode2session(js_code):
    """code -> openid + session_key. AppSecret 走环境变量, 不入仓.

    返回 ({openid, session_key, unionid?}, None) 或 (None, error_msg).
    session_key 仅服务端使用 (后续可能要解密 phone_number 等), 不下发前端.
    """
    if not WX_APPSECRET:
        return None, 'WX_APPSECRET 未配置, 请在 systemd unit 加 Environment="WX_APPSECRET=xxx" 后重启服务'
    if not js_code:
        return None, '缺少 code'
    url = 'https://api.weixin.qq.com/sns/jscode2session?' + urllib.parse.urlencode({
        'appid': WX_APPID,
        'secret': WX_APPSECRET,
        'js_code': js_code,
        'grant_type': 'authorization_code',
    })
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
        if 'openid' in payload:
            return {
                'openid': payload['openid'],
                'session_key': payload.get('session_key', ''),
                'unionid': payload.get('unionid', ''),
            }, None
        return None, 'jscode2session 返回错: {}'.format(payload)
    except Exception as e:
        return None, 'jscode2session 调用失败: {}'.format(e)

# ============ 设备名册（wearable_device）======================
def device_register(device_sign, device_type=1, mac=None):
    """按 mac (优先) 或 device_sign UPSERT 到 wearable_device, 返回 (deviceId, action).

    5.06-v6 匹配优先级 (越靠前越权威):
        1. 有 mac -> 按 mac 查; 命中 -> 顺手把 sign 更新成最新 (sign 可能跨连接漂移, mac 不变)
        2. 无 mac 命中 -> 按 sign 查; 命中 -> 若客户端传了 mac 但表里这行还是 NULL 则补上
        3. 都没命中 -> INSERT 新行 (含 sign + mac)

    为什么 mac 匹配优于 sign: device_sign 是 'name_<MAC>' 复合, 同一手表 name 部分可能
    跨连接漂移 ('(上次连接)' 后缀, 系统名修改等), 导致同 mac 生成不同 sign 多行.
    现有数据中已发现 deviceId=4 与 deviceId=5 是同一张表 (用户报告).
    """
    if not device_sign:
        return None, '缺少 deviceSign'
    conn = get_connection()
    try:
        cur = conn.cursor()
        # 1. 优先按 mac 查
        if mac:
            cur.execute(
                'SELECT id, device_sign FROM wearable_device WHERE mac = %s LIMIT 1',
                (mac,)
            )
            row = cur.fetchone()
            if row:
                # 命中 mac, 顺手把 sign 更新成最新 (兼容 name 漂移)
                if row[1] != device_sign:
                    cur.execute(
                        'UPDATE wearable_device SET device_sign = %s WHERE id = %s',
                        (device_sign, row[0])
                    )
                    print('[设备注册] mac={} 命中已存在 id={}, sign 更新 {} -> {}'.format(
                        mac, row[0], row[1], device_sign))
                cur.close()
                return {'deviceId': row[0], 'action': 'matched-by-mac'}, None
        # 2. 按 sign 查
        cur.execute(
            'SELECT id, mac FROM wearable_device WHERE device_sign = %s LIMIT 1',
            (device_sign,)
        )
        row = cur.fetchone()
        if row:
            # 命中 sign, 若客户端传了 mac 但表里 mac 字段还空, 补上
            if mac and not row[1]:
                cur.execute(
                    'UPDATE wearable_device SET mac = %s WHERE id = %s',
                    (mac, row[0])
                )
                print('[设备注册] sign={} 命中已存在 id={}, 补充 mac={}'.format(
                    device_sign, row[0], mac))
            cur.close()
            return {'deviceId': row[0], 'action': 'matched-by-sign'}, None
        # 3. 都没命中 -> 新建
        cur.execute(
            'INSERT INTO wearable_device (device_sign, mac, type) VALUES (%s, %s, %s)',
            (device_sign, mac, device_type)
        )
        new_id = cur.lastrowid
        print('[设备注册] 新增 wearable_device: id={} sign={} mac={} type={}'.format(
            new_id, device_sign, mac, device_type))
        cur.close()
        return {'deviceId': new_id, 'action': 'created'}, None
    finally:
        conn.close()

def device_by_sign(sign):
    """按 sign 查 wearable_device，不创建"""
    if not sign:
        return None, '缺少 sign'
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            'SELECT id, device_sign, type FROM wearable_device WHERE device_sign = %s LIMIT 1',
            (sign,)
        )
        row = cur.fetchone()
        cur.close()
        if row:
            return {'id': row[0], 'device_sign': row[1], 'type': row[2]}, None
        return {'error': 'not found'}, None
    finally:
        conn.close()

def device_merge(from_id, to_id):
    """5.06-v6: 合并 wearable_device_data 两行的大 JSON: from_id -> to_id.

    每个中文键 (心率/血氧/血压/...) 的数组追加到 to_id, from_id 那行删除.
    wearable_device 表本身不动 (调用方再用 DELETE /api/device/<from_id> 清理).

    用途: iOS UUID 漂移生成的脏行合并. 例如生产数据 deviceId=5 与 deviceId=4 实际是同一手表,
    把 5 的所有数据合并到 4, 然后删掉 wearable_device.id=5 那行.
    """
    if not isinstance(from_id, int) or from_id <= 0:
        return None, 'invalid fromDeviceId'
    if not isinstance(to_id, int) or to_id <= 0:
        return None, 'invalid toDeviceId'
    if from_id == to_id:
        return None, 'fromDeviceId == toDeviceId'

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT id, data FROM wearable_device_data WHERE deviceId = %s LIMIT 1', (from_id,))
        from_row = cur.fetchone()
        if not from_row:
            cur.close()
            return None, 'fromDeviceId={} 在 wearable_device_data 中不存在'.format(from_id)
        cur.execute('SELECT id, data FROM wearable_device_data WHERE deviceId = %s LIMIT 1', (to_id,))
        to_row = cur.fetchone()

        try:
            from_json = json.loads(from_row[1]) if from_row[1] else {}
            if not isinstance(from_json, dict):
                from_json = {}
        except json.JSONDecodeError:
            from_json = {}
        if to_row:
            try:
                to_json = json.loads(to_row[1]) if to_row[1] else {}
                if not isinstance(to_json, dict):
                    to_json = {}
            except json.JSONDecodeError:
                to_json = {}
        else:
            to_json = {}

        merged_counts = {}
        for k, v in from_json.items():
            if not isinstance(v, list):
                continue
            if not isinstance(to_json.get(k), list):
                to_json[k] = []
            added = len(v)
            to_json[k].extend(v)
            merged_counts[k] = {'added': added, 'totalAfter': len(to_json[k])}

        merged_str = json.dumps(to_json, ensure_ascii=False)
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if to_row:
            cur.execute(
                'UPDATE wearable_device_data SET data = %s, createTime = %s WHERE id = %s',
                (merged_str, now_str, to_row[0])
            )
            to_row_id = to_row[0]
            to_action = 'updated'
        else:
            cur.execute(
                'INSERT INTO wearable_device_data (deviceId, data, createTime) VALUES (%s, %s, %s)',
                (to_id, merged_str, now_str)
            )
            to_row_id = cur.lastrowid
            to_action = 'inserted'

        cur.execute('DELETE FROM wearable_device_data WHERE id = %s', (from_row[0],))
        cur.close()
        print('[数据合并] from={} -> to={} | 类型 {} | 删 from 行 id={} | to 行 {} id={}'.format(
            from_id, to_id, list(merged_counts.keys()), from_row[0], to_action, to_row_id))
        return {
            'fromDeviceId': from_id,
            'toDeviceId': to_id,
            'mergedCounts': merged_counts,
            'fromRowDeleted': from_row[0],
            'toRowAction': to_action,
            'toRowId': to_row_id,
        }, None
    finally:
        conn.close()

def device_delete(device_id):
    """
    删 wearable_device 一行 + 联动删 wearable_device_data 中所有 deviceId 行.
    用于清理废弃设备 (例如 iOS UUID 飘逸生成的脏行).
    """
    if not isinstance(device_id, int) or device_id <= 0:
        return None, 'invalid id'
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('DELETE FROM wearable_device_data WHERE deviceId = %s', (device_id,))
        deleted_data = cur.rowcount
        cur.execute('DELETE FROM wearable_device WHERE id = %s', (device_id,))
        deleted_device = cur.rowcount
        cur.close()
        print('[设备删除] id={} | wearable_device 删 {} 行 | wearable_device_data 删 {} 行'.format(
            device_id, deleted_device, deleted_data))
        return {'deviceId': device_id, 'deletedDevice': deleted_device, 'deletedData': deleted_data}, None
    finally:
        conn.close()

# ============ HTTP Handler ============
class HealthDataHandler(BaseHTTPRequestHandler):

    def _send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send_json(200, {'ok': True})

    def do_GET(self):
        parsed = urlparse(self.path)
        pathname = parsed.path
        query = parse_qs(parsed.query)

        if pathname == '/api/status':
            ok, info = test_db()
            self._send_json(200, {
                'status': 'running',
                'mysql': 'connected' if ok else 'disconnected',
                'total_devices': info if ok else 0,
                'error': None if ok else info,
                'server_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            })

        elif pathname == '/api/data':
            # 5.06-v9: 数据库一台设备一行 (按 deviceId 切片), 不依赖 wx_openid 列.
            # 客户端 / 调用方按 patientNo 过滤数据时, 可选 ?patientNo=100234:
            # 服务端在 Python 端把每行 data 数组按 '门诊号' 字段过滤, 返回精简版.
            # 不传 patientNo 时返回原样大 JSON (与 v6 行为一致).
            patient_no_filter = (query.get('patientNo') or [None])[0]
            try:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute(
                    'SELECT id, deviceId, data, createTime '
                    'FROM wearable_device_data ORDER BY deviceId, createTime'
                )
                rows = []
                for r in cur.fetchall():
                    big_json = json.loads(r[2]) if r[2] else {}
                    if patient_no_filter and isinstance(big_json, dict):
                        # 按门诊号过滤每个数据类型的数组, 没匹配的类型从结果里剔除
                        filtered = {}
                        for k, v in big_json.items():
                            if isinstance(v, list):
                                hits = [rec for rec in v if rec.get('门诊号') == patient_no_filter]
                                if hits: filtered[k] = hits
                        big_json = filtered
                        # 该行没有目标患者的任何数据 → 跳过这行
                        if not big_json:
                            continue
                    type_counts = {}
                    if isinstance(big_json, dict):
                        for k, v in big_json.items():
                            if isinstance(v, list):
                                type_counts[k] = len(v)
                    rows.append({
                        'id': r[0],
                        'deviceId': r[1],
                        'data': big_json,
                        'typeCounts': type_counts,
                        'createTime': r[3].strftime('%Y-%m-%d %H:%M:%S') if r[3] else None,
                    })
                cur.close()
                conn.close()
                self._send_json(200, {'count': len(rows), 'records': rows,
                                       'filteredBy': {'patientNo': patient_no_filter} if patient_no_filter else None})
            except Exception as e:
                self._send_json(500, {'error': str(e)})

        elif pathname == '/api/ble-event/stats':
            try:
                days_str = (query.get('days') or ['7'])[0]
                days = max(1, min(90, int(days_str)))
            except (ValueError, TypeError):
                days = 7
            result, err = query_ble_event_stats(days)
            if err:
                self._send_json(500, {'error': err})
            else:
                self._send_json(200, result)

        elif pathname == '/api/device/by-sign':
            sign = (query.get('sign') or [None])[0]
            try:
                result, err = device_by_sign(sign)
                if err:
                    self._send_json(400, {'error': err})
                else:
                    self._send_json(200, result)
            except Exception as e:
                self._send_json(500, {'error': str(e)})

        else:
            self._send_json(200, {
                'service': '智能随访-可穿戴设备数据接收服务',
                'mode': '一台设备一个微信用户一行 + 大 JSON 汇总',
                'version': '5.06-v7',
                'endpoints': {
                    'GET  /api/status': '服务状态',
                    'GET  /api/data': '查询所有设备 (?wxOpenid= 过滤, =NULL 仅历史未分组数据)',
                    'POST /api/health-data': 'UPSERT 体征数据 (按 deviceId+wxOpenid 切片)',
                    'POST /api/device/register': '按 mac (优先) 或 device_sign UPSERT 到 wearable_device 并返回 deviceId',
                    'POST /api/device/merge': '合并 wearable_device_data 两行: {fromDeviceId, toDeviceId}',
                    'POST /api/wx/login': '微信小程序 code -> openid (走 jscode2session, 需 WX_APPSECRET)',
                    'POST /api/ble-event': '客户端蓝牙连接质量埋点 (5.06-v8)',
                    'GET  /api/ble-event/stats': '最近 N 天连接质量聚合 (?days=7)',
                    'GET  /api/device/by-sign?sign=...': '按 sign 查 wearable_device（不创建）',
                    'DELETE /api/device/:id': '删 wearable_device 一行 + 联动删该 deviceId 的所有数据',
                },
            })

    def do_POST(self):
        parsed = urlparse(self.path)
        pathname = parsed.path

        try:
            length = int(self.headers.get('Content-Length', 0))
            raw = self.rfile.read(length) if length > 0 else b'{}'
            body = json.loads(raw.decode('utf-8') or '{}')
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {'error': 'Invalid JSON'})
            return

        try:
            if pathname == '/api/health-data':
                device_id = body.get('deviceId', DEFAULT_DEVICE_ID)
                data_type = body.get('dataType')
                data = body.get('data')
                # 5.06-v9: 客户端首页输入的患者门诊号. 写入大 JSON 每条记录的 '门诊号' 字段;
                # 数据库一台设备一行不变. 老客户端 / 未输入门诊号时 = None, 该字段不写.
                patient_no = body.get('patientNo') or None
                # 客户端 4.29-v5+ 携带的双时间戳:
                #   recordedAt = saveData 调用时刻 (= 用户在表上测量时刻, 经 BleHub 收到回包时填)
                #   uploadedAt = postOnce 发送时刻 (客户端) — 服务端记录自己收到的时刻更可靠
                # 缺省 (老客户端) 时用 server 当前时刻当采集时间, 兼容旧版本.
                recorded_at = body.get('recordedAt')
                # 服务端权威 uploadedAt: 用 server 收到时刻, 不信客户端的 (防时钟漂移)
                uploaded_at = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
                if not data_type or data is None:
                    self._send_json(400, {
                        'error': 'Required fields: dataType, data',
                        'supportedTypes': list(TYPE_TO_CHINESE.keys()),
                    })
                    return
                result, err = upsert_device_data(device_id, data_type, data,
                                                  patient_no=patient_no,
                                                  recorded_at=recorded_at,
                                                  uploaded_at=uploaded_at)
                if err:
                    self._send_json(400, {'error': err, 'supportedTypes': list(TYPE_TO_CHINESE.keys())})
                    return
                print('[{}] {} 设备{} 门诊号={} {}({}条) 总{}类'.format(
                    datetime.datetime.now().strftime('%H:%M:%S'),
                    result['action'].upper(),
                    device_id,
                    patient_no or 'NULL',
                    result['type'],
                    result['count'],
                    result['totalTypes'],
                ))
                self._send_json(200, {'success': True, **result, 'deviceId': device_id})

            elif pathname == '/api/wx/login':
                code = body.get('code')
                result, err = wx_jscode2session(code)
                if err:
                    self._send_json(400, {'error': err})
                else:
                    # session_key 仅服务端保留, 不下发前端
                    self._send_json(200, {
                        'openid': result['openid'],
                        'unionid': result.get('unionid', ''),
                    })

            elif pathname == '/api/ble-event':
                result, err = insert_ble_event(body)
                if err:
                    self._send_json(400, {'error': err})
                else:
                    self._send_json(200, {'success': True, **result})

            elif pathname == '/api/device/register':
                device_sign = body.get('deviceSign')
                device_type = body.get('type', 1)
                mac = body.get('mac')
                result, err = device_register(device_sign, device_type, mac=mac)
                if err:
                    self._send_json(400, {'error': err})
                else:
                    self._send_json(200, result)

            elif pathname == '/api/device/merge':
                from_id_raw = body.get('fromDeviceId')
                to_id_raw = body.get('toDeviceId')
                try:
                    from_id = int(from_id_raw) if from_id_raw is not None else None
                    to_id = int(to_id_raw) if to_id_raw is not None else None
                except (TypeError, ValueError):
                    self._send_json(400, {'error': 'fromDeviceId/toDeviceId 必须为正整数'})
                    return
                result, err = device_merge(from_id, to_id)
                if err:
                    self._send_json(400, {'error': err})
                else:
                    self._send_json(200, {'success': True, **result})

            else:
                self._send_json(404, {'error': 'Not found. Available: POST /api/health-data, POST /api/device/register, POST /api/device/merge'})

        except Exception as e:
            traceback.print_exc()
            self._send_json(500, {'error': str(e)})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        pathname = parsed.path
        # /api/device/:id
        m = re.match(r'^/api/device/(\d+)$', pathname)
        if not m:
            self._send_json(404, {'error': 'Not found. Available: DELETE /api/device/:id'})
            return
        try:
            device_id = int(m.group(1))
            result, err = device_delete(device_id)
            if err:
                self._send_json(400, {'error': err})
            else:
                self._send_json(200, {'success': True, **result})
        except Exception as e:
            traceback.print_exc()
            self._send_json(500, {'error': str(e)})

    def log_message(self, format, *args):
        pass

# ============ 启动 ============
if __name__ == '__main__':
    ok, info = test_db()
    if ok:
        print('[启动] MySQL 连接成功 → {}, 当前 {} 行体征数据'.format(DB_CONFIG['host'], info))
        # 5.06-v6: 启动时确保 wearable_device.mac 列存在 (idempotent), 后续 register 走 mac 优先匹配
        ensure_mac_column()
        # 5.06-v9 决定: 不动 wearable_device_data schema, 不再自动建 wx_openid 列 / ble_event 表.
        # 患者标识改为写入大 JSON 每条记录的 '门诊号' 字段, 切片仍按 deviceId 一台设备一行.
        # ensure_openid_column / ensure_ble_event_table 函数保留在文件中以备未来需要,
        # 但启动时不调用. 想启用: 解开下面两行注释 + 重启服务.
        # ensure_openid_column()
        # ensure_ble_event_table()
    else:
        print('[警告] MySQL 连接失败 → {}: {}'.format(DB_CONFIG['host'], info))

    if WX_APPSECRET:
        print('[启动] WX_APPSECRET 已配置 (长度 {}), /api/wx/login 可用'.format(len(WX_APPSECRET)))
    else:
        print('[警告] WX_APPSECRET 未配置, /api/wx/login 会拒绝请求. systemd 加 Environment="WX_APPSECRET=xxx" 后重启')

    server = HTTPServer(('0.0.0.0', PORT), HealthDataHandler)
    print('[启动] 智能随访数据接收服务 v5.06-v9: http://0.0.0.0:{}'.format(PORT))
    print('[模式] 一台设备一行 + 大 JSON 汇总; 患者标识 = 大 JSON 每条记录的 "门诊号" 字段')
    print('[端点] POST /api/health-data       UPSERT 体征数据 (按 deviceId 切片, 透传 patientNo)')
    print('[端点] POST /api/device/register   设备名册 UPSERT (mac 优先)')
    print('[端点] POST /api/device/merge      合并 wearable_device_data 两行')
    print('[端点] GET  /api/device/by-sign    设备名册查询')
    print('[端点] GET  /api/status            服务状态')
    print('[端点] GET  /api/data              查询所有设备 (?patientNo= 过滤大 JSON 内的记录)')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n[停止] 服务已关闭')
        server.server_close()
