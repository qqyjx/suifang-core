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
- GET  /api/data                         查询所有设备的大 JSON (可选 ?patientNo= 过滤;
                                         v10 新增 ?page=N&size=M 分页, deviceId DESC; 响应支持 gzip)
- GET  /api/patients/summary             v10 新增: 按门诊号服务端聚合摘要
                                         (count/types/devices/earliest/latest, 响应 ~50x 小于 /api/data)
- POST /api/health-data                  写入一条体征记录（自动 UPSERT 到大 JSON 数组）;
                                         患者标识传 patientNo, 写入每条记录的 '门诊号' 字段
- POST /api/device/register              按 mac (优先) 或 device_sign UPSERT 到 wearable_device，返回 deviceId
- POST /api/device/merge                 合并 wearable_device_data 两行: {fromDeviceId, toDeviceId}
- GET  /api/device/by-sign?sign=...      按 sign 查 wearable_device（不创建）
- DELETE /api/device/:id                 删 wearable_device 一行 + 联动删该 deviceId 的所有数据

随访平台 1.0 M1 (只读版本, 见 docs/随访平台1.0设计方案.html §④⑤):
- GET  /api/platform/patients             患者列表 + 三链路绑定态 + 最近上传时间 + 未关闭报警数
- POST /api/platform/patient              UPSERT platform_patient (建档/改档)
- POST /api/platform/bind                 绑定/解绑 iwown 设备 或 zhenmaiyi case_id
- GET  /api/platform/patient/vitals        单患者跨链路体征日聚合 (iwown 日聚合 + 复用 S101 门诊号解析 + 诊脉仪最新一条)
  写接口门禁: 环境变量 PLATFORM_TOKEN 设了时, POST /api/platform/* 必须带 header
  X-Platform-Token 且值匹配, 否则 403; 未设时开发模式放行 (启动时打印警告)。

随访平台 1.0 M2 (报警闭环, 见 docs/随访平台1.0设计方案.html §3.3/④⑤⑥):
- POST /api/platform/alarm/ingest          扫 iwown_data data_type='alarm' 未处理行 -> platform_alarm
                                          (幂等: source_data_id 唯一索引, 重跑 inserted=0)
- GET  /api/platform/alarms                报警工作台列表 (?status=new|acked|followed|closed|open&patientNo=&limit=)
- POST /api/platform/alarm/transition      报警状态流转 {alarm_id, action:'ack'|'call'|'visit'|'note'|'close',
                                          result_text, operator}; 状态机 new->acked->followed->closed,
                                          每次成功流转落 1 行 platform_followup_log。

5.06-v9 决定: 不动 wearable_device_data schema (无 wx_openid / patient_no 列),
             患者标识统一写在大 JSON 每条记录的 '门诊号' 字段里, 切片仍按 deviceId 一台设备一行.
             v7/v8 残留的 wx.login / ble_event 端点和函数保留在文件中但不在启动时激活,
             如需启用请阅读 main 块中的注释.

部署：scp 本文件到 192.168.4.104:/opt/suifang/health_server.py，systemd 启动
"""
import os
import json
import re
import gzip
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
    # 随访平台 1.0: DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME 环境变量覆盖硬编码默认值
    # (与下面 WX_APPID 同款写法), 生产环境不设这些变量时行为不变.
    'host': os.environ.get('DB_HOST') or '192.168.4.174',
    'port': int(os.environ.get('DB_PORT') or 3306),
    'user': os.environ.get('DB_USER') or 'developer',
    'password': os.environ.get('DB_PASSWORD') or 'DePer!$12967',
    'database': os.environ.get('DB_NAME') or 'h6dp_suifang',
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

# ============ 随访平台 1.0 写接口门禁 ============
# PLATFORM_TOKEN 设了时, POST /api/platform/* 必须带 header X-Platform-Token 且值匹配;
# 未设时 (本地/原型阶段) 放行, 启动时打印警告. 与 WX_APPSECRET 同一套"敏感值不入仓"原则.
PLATFORM_TOKEN = os.environ.get('PLATFORM_TOKEN') or ''

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

def ensure_zhenmaiyi_table():
    """v10 patch: 创建 zhenmaiyi 表 (idempotent), 收浏览器端解析的诊脉仪 zip 数据.

    每条记录 = 一位患者的一次诊脉, 含结构化数据 + 三个原始附件 base64
    (一个四诊报告 PDF + 两个顶层 Excel 汇总).
    schema 详见 database/zhenmaiyi.sql.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS zhenmaiyi (
                id INT AUTO_INCREMENT PRIMARY KEY,
                case_id VARCHAR(64) NOT NULL UNIQUE COMMENT '病例ID',
                patient_name VARCHAR(50) DEFAULT NULL COMMENT '患者姓名',
                patient_gender VARCHAR(8) DEFAULT NULL COMMENT '患者性别',
                patient_age INT DEFAULT NULL COMMENT '患者年龄',
                detect_time DATETIME DEFAULT NULL COMMENT '诊脉仪检测时间',
                conclusion VARCHAR(100) DEFAULT NULL COMMENT '体质结论',
                pulse_label VARCHAR(32) DEFAULT NULL COMMENT '主脉象',
                full_data JSON COMMENT '体质9得分 + 脉诊42参数 + 答题记录',
                pdf_base64 LONGTEXT COMMENT '四诊报告 sizhen_.pdf base64',
                constitution_xlsx_base64 LONGTEXT COMMENT '顶层 患者体质记录导出*.xlsx base64',
                pulse_xlsx_base64 LONGTEXT COMMENT '顶层 患者脉诊导出*.xlsx base64',
                source_zip_name VARCHAR(200) DEFAULT NULL COMMENT '上传时的源 zip 文件名',
                uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_detect_time (detect_time),
                INDEX idx_patient_name (patient_name),
                INDEX idx_conclusion (conclusion),
                INDEX idx_uploaded_at (uploaded_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        print('[启动] zhenmaiyi 表已就绪')
        cur.close()
    except Exception as e:
        print('[启动] ensure_zhenmaiyi_table 失败:', e)
    finally:
        conn.close()


def ensure_platform_tables():
    """随访平台 1.0 M1: 创建 platform_* 3 张表 (idempotent).

    设计原则 (docs/随访平台1.0设计方案.html §①④): 不动现有 6 张生产表
    (wearable_device* / zhenmaiyi / iwown_* / ble_event) 一列, 平台层只新增
    platform_ 前缀表. DDL 归档参考见 database/platform.sql, 本函数为权威来源
    (与 ensure_zhenmaiyi_table 同一惯例).
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_patient (
                patient_no VARCHAR(64) PRIMARY KEY COMMENT '门诊号, 患者主键',
                name VARCHAR(64) DEFAULT NULL,
                gender ENUM('M','F') DEFAULT NULL,
                age INT DEFAULT NULL,
                group_tag VARCHAR(64) DEFAULT NULL COMMENT '队列/分组',
                zhenmaiyi_case_id VARCHAR(64) DEFAULT NULL COMMENT '诊脉仪 case_id 映射',
                note VARCHAR(255) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台患者主索引'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_alarm (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                patient_no VARCHAR(64) DEFAULT NULL,
                device_id VARCHAR(32) DEFAULT NULL,
                alarm_type VARCHAR(24) DEFAULT NULL COMMENT 'fall/sos/hr/spo2/bp/temp/sedentary/not_worn/low_battery',
                severity ENUM('crit','warn','info') DEFAULT NULL,
                lat DECIMAL(10,6) DEFAULT NULL,
                lng DECIMAL(10,6) DEFAULT NULL,
                payload_json JSON DEFAULT NULL,
                source_data_id BIGINT DEFAULT NULL COMMENT '→iwown_data.id, 解析重跑幂等去重',
                status ENUM('new','acked','followed','closed') DEFAULT 'new',
                occurred_at DATETIME DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uk_source_data_id (source_data_id),
                INDEX idx_patient_no (patient_no),
                INDEX idx_status (status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台报警事件 (M2 起写入, M1 只建表)'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_followup_log (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                patient_no VARCHAR(64) DEFAULT NULL,
                alarm_id BIGINT DEFAULT NULL,
                action ENUM('ack','call','visit','note','close') DEFAULT NULL,
                result_text TEXT,
                operator VARCHAR(64) DEFAULT NULL,
                plan_id BIGINT DEFAULT NULL COMMENT '1.1 随访计划钩子, 暂空',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_patient_no (patient_no)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台回访/处理记录 (M2 起写入, M1 只建表)'
        """)
        print('[启动] platform_patient / platform_alarm / platform_followup_log 表已就绪')
        cur.close()
    except Exception as e:
        print('[启动] ensure_platform_tables 失败:', e)
    finally:
        conn.close()


def upsert_zhenmaiyi(patients, constitution_xlsx_b64, pulse_xlsx_b64, source_zip_name):
    """v10 patch: 批量 UPSERT zhenmaiyi 记录, 按 case_id 去重.

    patients: list of dict, 每个含:
      case_id (必填), patient_name, patient_gender, patient_age (int),
      detect_time (YYYY-MM-DD HH:MM:SS), conclusion, pulse_label,
      full_data (dict), pdf_base64 (可空)
    constitution_xlsx_b64 / pulse_xlsx_b64: 顶层两个汇总 xlsx 的 base64,
      所有患者共享, 每条记录都冗余存一份.
    返回: {inserted, updated, total, errors}
    """
    if not patients:
        return {'inserted': 0, 'updated': 0, 'total': 0, 'errors': []}

    inserted = updated = 0
    errors = []
    conn = get_connection()
    try:
        cur = conn.cursor()
        for p in patients:
            case_id = str(p.get('case_id') or '').strip()
            if not case_id:
                errors.append({'case_id': None, 'msg': '缺 case_id'})
                continue
            try:
                age = int(p.get('patient_age') or 0) or None
            except (ValueError, TypeError):
                age = None
            full_data = p.get('full_data') or {}
            full_data_json = json.dumps(full_data, ensure_ascii=False)
            # 先 SELECT 看是否存在 (兼容 mysql 5.7 没 ON DUPLICATE KEY JSON 写法分歧)
            cur.execute('SELECT id FROM zhenmaiyi WHERE case_id = %s', (case_id,))
            row = cur.fetchone()
            if row:
                cur.execute("""
                    UPDATE zhenmaiyi SET
                      patient_name = %s, patient_gender = %s, patient_age = %s,
                      detect_time = %s, conclusion = %s, pulse_label = %s,
                      full_data = %s,
                      pdf_base64 = %s,
                      constitution_xlsx_base64 = %s,
                      pulse_xlsx_base64 = %s,
                      source_zip_name = %s,
                      uploaded_at = CURRENT_TIMESTAMP
                    WHERE case_id = %s
                """, (
                    p.get('patient_name'), p.get('patient_gender'), age,
                    p.get('detect_time') or None, p.get('conclusion'),
                    p.get('pulse_label'), full_data_json,
                    p.get('pdf_base64'), constitution_xlsx_b64, pulse_xlsx_b64,
                    source_zip_name, case_id,
                ))
                updated += 1
            else:
                cur.execute("""
                    INSERT INTO zhenmaiyi (
                      case_id, patient_name, patient_gender, patient_age,
                      detect_time, conclusion, pulse_label, full_data,
                      pdf_base64, constitution_xlsx_base64, pulse_xlsx_base64,
                      source_zip_name
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    case_id, p.get('patient_name'), p.get('patient_gender'), age,
                    p.get('detect_time') or None, p.get('conclusion'),
                    p.get('pulse_label'), full_data_json,
                    p.get('pdf_base64'), constitution_xlsx_b64, pulse_xlsx_b64,
                    source_zip_name,
                ))
                inserted += 1
        conn.commit()
        cur.close()
    except Exception as e:
        traceback.print_exc()
        errors.append({'msg': str(e)})
    finally:
        conn.close()
    return {'inserted': inserted, 'updated': updated,
            'total': inserted + updated, 'errors': errors}


def query_zhenmaiyi_list():
    """v10 patch: 查全部 zhenmaiyi 记录 (不含 base64 大字段, 给看板列表用)."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT case_id, patient_name, patient_gender, patient_age,
                   detect_time, conclusion, pulse_label, full_data,
                   source_zip_name, uploaded_at,
                   CHAR_LENGTH(IFNULL(pdf_base64,'')) > 0 AS has_pdf
            FROM zhenmaiyi
            ORDER BY detect_time DESC, uploaded_at DESC
        """)
        cols = [d[0] for d in cur.description]
        rows = []
        for r in cur.fetchall():
            row = dict(zip(cols, r))
            # detect_time / uploaded_at datetime → str
            for k in ('detect_time', 'uploaded_at'):
                if row.get(k) is not None and hasattr(row[k], 'strftime'):
                    row[k] = row[k].strftime('%Y-%m-%d %H:%M:%S')
            # full_data 在某些 driver 下是 str
            if isinstance(row.get('full_data'), str):
                try: row['full_data'] = json.loads(row['full_data'])
                except Exception: pass
            row['has_pdf'] = bool(row.get('has_pdf'))
            rows.append(row)
        cur.close()
        return {'count': len(rows), 'patients': rows}
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

def _is_daily_empty(daily_records):
    """判定 SDK readDailyData 回传的 dailyRecords 是否全空.

    Veepoo SDK 经常回放出 dailyRecords=[] 或 dailyRecords=[{字段全空字符串}] 的空汇报
    (老固件没填 / 患者没穿够 / 当日聚合未触发). 看板/统计用这个判定排除空跑.
    """
    if not daily_records or not isinstance(daily_records, list):
        return True
    key_fields = ('date', 'step', 'sleepData', 'pulseReat', 'bloodPressure',
                  'bloodOxygen', 'bloodGlucose', 'HRVData', 'pressure',
                  'respirationRate', 'sleepStatus', 'bloodLiquid')
    empty_markers = (None, '', [], {}, 0, '0', '0.0')
    for r in daily_records:
        if not isinstance(r, dict):
            continue
        for k in key_fields:
            v = r.get(k)
            if v not in empty_markers:
                return False
        # 体温是嵌套 dict, 单独判 (全 0.0 也算空)
        bt = r.get('bodyTemperature')
        if isinstance(bt, dict):
            for v in bt.values():
                if v not in empty_markers:
                    return False
    return True


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
        # v10 patch: 标 dailyRecords 是否全空 (老固件 / 患者没穿够 / 触发条件未达).
        # 看板用 is_empty 算"有效日综合数", 避免 497 条空跑被当真实数据.
        record['is_empty'] = _is_daily_empty(data.get('dailyRecords'))
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

# ============ 随访平台 1.0 M1 (患者中心 + 患者视图, 只读版本) ============
def check_platform_token(handler):
    """写接口门禁: PLATFORM_TOKEN 设了时校验 header X-Platform-Token; 未设时放行(dev 模式).

    返回 True = 通过; False = 已直接发送 403 响应, 调用方应立即 return.
    """
    if not PLATFORM_TOKEN:
        return True
    token = handler.headers.get('X-Platform-Token')
    if token != PLATFORM_TOKEN:
        handler._send_json(403, {'ok': False, 'error': 'X-Platform-Token 校验失败或缺失'})
        return False
    return True


def upsert_platform_patient(body):
    """UPSERT platform_patient (建档/改档). patient_no 为业务主键(门诊号)."""
    patient_no = str(body.get('patient_no') or '').strip()
    if not patient_no:
        return None, 'patient_no 必填'
    name = body.get('name') or None
    gender = body.get('gender') or None
    if gender not in (None, 'M', 'F'):
        return None, "gender 必须是 'M' 或 'F'"
    age_raw = body.get('age')
    try:
        age = int(age_raw) if age_raw not in (None, '') else None
    except (ValueError, TypeError):
        return None, 'age 必须是整数'
    group_tag = body.get('group_tag') or None
    zhenmaiyi_case_id = body.get('zhenmaiyi_case_id') or None
    note = body.get('note') or None
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT patient_no FROM platform_patient WHERE patient_no = %s', (patient_no,))
        exists = cur.fetchone()
        if exists:
            cur.execute("""
                UPDATE platform_patient SET
                  name = %s, gender = %s, age = %s, group_tag = %s,
                  zhenmaiyi_case_id = %s, note = %s
                WHERE patient_no = %s
            """, (name, gender, age, group_tag, zhenmaiyi_case_id, note, patient_no))
            action = 'update'
        else:
            cur.execute("""
                INSERT INTO platform_patient
                  (patient_no, name, gender, age, group_tag, zhenmaiyi_case_id, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (patient_no, name, gender, age, group_tag, zhenmaiyi_case_id, note))
            action = 'insert'
        cur.close()
        return {'patient_no': patient_no, 'action': action}, None
    finally:
        conn.close()


def platform_bind(body):
    """绑定/解绑 {patient_no, chain: 'iwown'|'zhenmaiyi', key, unbind}.

    iwown:     UPDATE iwown_device SET patient_no (unbind → NULL); key = device_id,
               device_id 必须已在 iwown_device 名册中(设备先上报/或手工登记), 否则报错.
    zhenmaiyi: UPDATE platform_patient SET zhenmaiyi_case_id; key = case_id.
    """
    patient_no = str(body.get('patient_no') or '').strip()
    chain = body.get('chain')
    key = body.get('key')
    unbind = bool(body.get('unbind'))
    if not patient_no:
        return None, 'patient_no 必填'
    if chain not in ('iwown', 'zhenmaiyi'):
        return None, "chain 必须是 'iwown' 或 'zhenmaiyi'"
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT patient_no FROM platform_patient WHERE patient_no = %s', (patient_no,))
        if not cur.fetchone():
            cur.close()
            return None, '患者不存在, 请先 POST /api/platform/patient 建档: {}'.format(patient_no)

        if chain == 'iwown':
            if not key:
                cur.close()
                return None, 'iwown 绑定/解绑需要 key(device_id)'
            cur.execute('SELECT device_id FROM iwown_device WHERE device_id = %s', (key,))
            if not cur.fetchone():
                cur.close()
                return None, 'iwown_device 中不存在 device_id={} (设备需先上报或手工登记)'.format(key)
            new_patient_no = None if unbind else patient_no
            cur.execute('UPDATE iwown_device SET patient_no = %s WHERE device_id = %s',
                        (new_patient_no, key))
            cur.close()
            return {'patient_no': patient_no, 'chain': 'iwown', 'device_id': key, 'unbind': unbind}, None
        else:
            new_case_id = None if unbind else (str(key).strip() if key else None)
            cur.execute('UPDATE platform_patient SET zhenmaiyi_case_id = %s WHERE patient_no = %s',
                        (new_case_id, patient_no))
            cur.close()
            return {'patient_no': patient_no, 'chain': 'zhenmaiyi',
                    'zhenmaiyi_case_id': new_case_id, 'unbind': unbind}, None
    finally:
        conn.close()


# ============ 随访平台 1.0 M2 (报警闭环) ============
def classify_iwown_alarm(decoded):
    """从 iwown 0x12 报警帧解码 JSON (MessageToDict, preserving_proto_field_name=True) 分类出
    (alarm_type, severity, lat, lng)。字段名逐一对应
    iwown/reference/proto/Alarm_info.proto 的 Alarm_infokConfirm/HealthAlarmV3/AlarminfoV3
    (与 iwown/reference/sample-python 官方示例 alarm_parser.py 读同一套字段名)。

    一帧可能同时含多个子类型(repeated 字段都非空), 按下表优先级只取最高等级的一个做
    列表展示分类; payload_json 仍落全量 decoded_json, 需要时可回溯其余子类型。

    映射表 (design doc §3.3/④ 枚举: fall/sos/hr/spo2/bp/temp/sedentary/not_worn/low_battery + unknown):
      alarm.alarm_fall                          -> fall        crit  跌倒
      alarm.gnssinfo / alarm.SOS_Notification_time -> sos       crit  SOS(取 gnssinfo[0] 经纬度)
      alarm.alarm_hr                              -> hr         warn  心率越限
      alarm.alarm_spo2                            -> spo2       warn  血氧越限
      alarm.alarm_Bp                              -> bp         warn  血压越限
      alarm.alarm_Temperature                     -> temp       warn  体温越限
      alarm.alarm_Sedentary                       -> sedentary  info  久坐
      Alarminfo.wearstate                         -> not_worn   info  未佩戴
      Alarminfo.lowpowerPercentage / poweroffPercentage -> low_battery info  低电/关机前电量上报
                                                                        (proto 无独立"关机"档, 并入低电量)
      其余 (alarm_Thrombus/alarm_Blood_sugar/alarm_Blood_potassium/alarm_ecg/
            Alarminfo.sleepstate/intercept_number/解码失败/无法解析) -> unknown warn
            (proto 有此字段但 design doc §3.3/④ 的报警类型枚举未列出; 全量仍存 payload_json 可回溯)
    """
    if not isinstance(decoded, dict):
        return 'unknown', 'warn', None, None
    alarm = decoded.get('alarm') or {}
    info = decoded.get('Alarminfo') or {}

    if alarm.get('alarm_fall'):
        return 'fall', 'crit', None, None
    gnss = alarm.get('gnssinfo')
    if gnss or 'SOS_Notification_time' in alarm:
        lat = lng = None
        if gnss:
            first = gnss[0]
            lat = first.get('latitude')
            lng = first.get('longitude')
        return 'sos', 'crit', lat, lng
    if alarm.get('alarm_hr'):
        return 'hr', 'warn', None, None
    if alarm.get('alarm_spo2'):
        return 'spo2', 'warn', None, None
    if alarm.get('alarm_Bp'):
        return 'bp', 'warn', None, None
    if alarm.get('alarm_Temperature'):
        return 'temp', 'warn', None, None
    if alarm.get('alarm_Sedentary'):
        return 'sedentary', 'info', None, None
    if 'wearstate' in info:
        return 'not_worn', 'info', None, None
    if 'lowpowerPercentage' in info or 'poweroffPercentage' in info:
        return 'low_battery', 'info', None, None
    return 'unknown', 'warn', None, None


def platform_alarm_ingest():
    """扫 iwown_data 里 data_type='alarm' 且尚未写入 platform_alarm 的行(LEFT JOIN 判重),
    解析 decoded_json 分类 + 用 iwown_device 名册在 ingest 时刻做患者归属, 幂等写入
    (source_data_id 唯一索引, 并发/重跑用 INSERT IGNORE 兜底)。返回 {ok, scanned, inserted}。"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT d.id, d.device_id, d.recorded_at, d.decoded_json, dev.patient_no
            FROM iwown_data d
            LEFT JOIN platform_alarm pa ON pa.source_data_id = d.id
            LEFT JOIN iwown_device dev ON dev.device_id = d.device_id
            WHERE d.data_type = 'alarm' AND pa.id IS NULL
            ORDER BY d.id
        """)
        rows = cur.fetchall()
        scanned = len(rows)
        inserted = 0
        for source_id, device_id, recorded_at, decoded_raw, patient_no in rows:
            if isinstance(decoded_raw, str):
                try:
                    decoded = json.loads(decoded_raw)
                except (TypeError, ValueError):
                    decoded = None
            else:
                decoded = decoded_raw
            alarm_type, severity, lat, lng = classify_iwown_alarm(decoded)
            payload_json = json.dumps(decoded, ensure_ascii=False) if decoded is not None else None
            cur.execute("""
                INSERT IGNORE INTO platform_alarm
                  (patient_no, device_id, alarm_type, severity, lat, lng, payload_json,
                   source_data_id, status, occurred_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'new', %s)
            """, (patient_no, device_id, alarm_type, severity, lat, lng, payload_json,
                  source_id, recorded_at))
            if cur.rowcount:
                inserted += 1
        cur.close()
        return {'ok': True, 'scanned': scanned, 'inserted': inserted}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def query_platform_alarms(status=None, patient_no=None, limit=50):
    """报警工作台列表: 最新在前, 关联 platform_patient 姓名 + platform_followup_log 处理记录.
    status 支持 'open' 这个 meta 值 = status != 'closed'。
    每条报警除 followup_count 外, 还带 followups 数组(完整回访历史, 时间正序), 供工作台处理弹窗
    直接展示, 不必再为此单开一个 GET 端点(design doc §⑤ 只列了 ingest/transition/list 三个)。"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        where = ['1=1']
        params = []
        if status == 'open':
            where.append("a.status != 'closed'")
        elif status in ('new', 'acked', 'followed', 'closed'):
            where.append('a.status = %s')
            params.append(status)
        if patient_no:
            where.append('a.patient_no = %s')
            params.append(patient_no)
        params.append(limit)
        cur.execute("""
            SELECT a.id, a.patient_no, p.name, a.device_id, a.alarm_type, a.severity,
                   a.lat, a.lng, a.payload_json, a.status, a.occurred_at, a.created_at,
                   (SELECT COUNT(*) FROM platform_followup_log f WHERE f.alarm_id = a.id) AS followup_count
            FROM platform_alarm a
            LEFT JOIN platform_patient p ON p.patient_no = a.patient_no
            WHERE {}
            ORDER BY a.occurred_at DESC, a.id DESC
            LIMIT %s
        """.format(' AND '.join(where)), params)
        cols = ['id', 'patient_no', 'patient_name', 'device_id', 'alarm_type', 'severity',
                'lat', 'lng', 'payload_json', 'status', 'occurred_at', 'created_at', 'followup_count']
        alarms = []
        for r in cur.fetchall():
            row = dict(zip(cols, r))
            for k in ('occurred_at', 'created_at'):
                if row.get(k) is not None and hasattr(row[k], 'strftime'):
                    row[k] = row[k].strftime('%Y-%m-%d %H:%M:%S')
            if isinstance(row.get('payload_json'), str):
                try:
                    row['payload_json'] = json.loads(row['payload_json'])
                except (TypeError, ValueError):
                    pass
            for k in ('lat', 'lng'):
                if row.get(k) is not None:
                    row[k] = float(row[k])
            alarms.append(row)

        alarm_ids = [row['id'] for row in alarms]
        followups_map = {}
        if alarm_ids:
            placeholders = ','.join(['%s'] * len(alarm_ids))
            cur.execute(
                'SELECT alarm_id, action, result_text, operator, created_at '
                'FROM platform_followup_log WHERE alarm_id IN ({}) ORDER BY created_at ASC'.format(placeholders),
                alarm_ids
            )
            for aid, action, result_text, operator, created_at in cur.fetchall():
                followups_map.setdefault(aid, []).append({
                    'action': action, 'result_text': result_text, 'operator': operator,
                    'created_at': created_at.strftime('%Y-%m-%d %H:%M:%S') if created_at else None,
                })
        for row in alarms:
            row['followups'] = followups_map.get(row['id'], [])

        cur.close()
        return {'ok': True, 'count': len(alarms), 'alarms': alarms}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


# 报警闭环状态机 (design doc §3.3): new -> acked(ack) -> followed(call/visit/note) -> closed(close)。
# acked/followed 都可以再记一次回访(followed); 除 closed 外任意态都可直接 close。
ALARM_TRANSITIONS = {
    'ack':   {'from': ('new',), 'to': 'acked'},
    'call':  {'from': ('acked', 'followed'), 'to': 'followed'},
    'visit': {'from': ('acked', 'followed'), 'to': 'followed'},
    'note':  {'from': ('acked', 'followed'), 'to': 'followed'},
    'close': {'from': ('new', 'acked', 'followed'), 'to': 'closed'},
}


def platform_alarm_transition(body):
    """报警状态流转 {alarm_id, action, result_text, operator}. 非法流转(如 closed 再 ack)拒绝,
    不落任何记录。每次成功流转写 1 行 platform_followup_log, 并更新 platform_alarm.status。"""
    try:
        alarm_id = int(body.get('alarm_id'))
    except (TypeError, ValueError):
        return None, 'alarm_id 必须是整数'
    action = body.get('action')
    if action not in ALARM_TRANSITIONS:
        return None, 'action 必须是 ack/call/visit/note/close 之一'
    result_text = body.get('result_text') or None
    operator = body.get('operator') or None
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT patient_no, status FROM platform_alarm WHERE id = %s', (alarm_id,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, '报警不存在: {}'.format(alarm_id)
        patient_no, cur_status = row
        rule = ALARM_TRANSITIONS[action]
        if cur_status not in rule['from']:
            cur.close()
            return None, '非法流转: status={} action={} (仅允许 {} -> {})'.format(
                cur_status, action, '/'.join(rule['from']), rule['to'])
        new_status = rule['to']
        cur.execute('UPDATE platform_alarm SET status = %s WHERE id = %s', (new_status, alarm_id))
        cur.execute("""
            INSERT INTO platform_followup_log (patient_no, alarm_id, action, result_text, operator)
            VALUES (%s, %s, %s, %s, %s)
        """, (patient_no, alarm_id, action, result_text, operator))
        cur.close()
        return {'alarm_id': alarm_id, 'status': new_status}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def _s101_scan_by_patient():
    """扫 wearable_device_data 全表一遍, 按门诊号建索引 (患者列表绑定态 + 最近上传时间用).

    复用 /api/patients/summary 的扫描惯例: 逐行 json.loads 大 JSON, 按每条记录的
    '门诊号' 字段分桶. 返回 dict: patient_no -> {'latest': iso_str|None, 'count': int}.
    """
    result = {}
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT data FROM wearable_device_data')
        for (data_raw,) in cur.fetchall():
            try:
                big_json = json.loads(data_raw) if data_raw else {}
            except json.JSONDecodeError:
                big_json = {}
            if not isinstance(big_json, dict):
                continue
            for type_key, arr in big_json.items():
                if not isinstance(arr, list):
                    continue
                for rec in arr:
                    if not isinstance(rec, dict):
                        continue
                    p_no = rec.get('门诊号')
                    if not p_no:
                        continue
                    ts = rec.get('采集时间') or rec.get('recordedAt') or rec.get('uploadedAt')
                    entry = result.setdefault(p_no, {'latest': None, 'count': 0})
                    entry['count'] += 1
                    if ts and (entry['latest'] is None or ts > entry['latest']):
                        entry['latest'] = ts
        cur.close()
        return result
    finally:
        conn.close()


def _s101_patient_vitals(patient_no, days=14):
    """单患者 S101/R04 体征日聚合(心率/血氧/血压/体温/步数).

    复用 /api/data?patientNo= 的过滤惯例: 逐行大 JSON, Python 端按 '门诊号' 过滤每条记录,
    再按 采集时间 的日期分桶取 均值/极值(步数取当日最大值, 与手表累计计数器语义一致).
    """
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime('%Y-%m-%d')
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT deviceId, data FROM wearable_device_data')
        devices = set()
        count = 0
        earliest = latest = None
        buckets = {}

        def bucket(date_str):
            return buckets.setdefault(date_str, {
                'hr': [], 'spo2': [], 'sbp': [], 'dbp': [], 'temp': [], 'step': 0,
            })

        for dev_id, data_raw in cur.fetchall():
            try:
                big_json = json.loads(data_raw) if data_raw else {}
            except json.JSONDecodeError:
                big_json = {}
            if not isinstance(big_json, dict):
                continue
            for type_key, arr in big_json.items():
                if not isinstance(arr, list):
                    continue
                for rec in arr:
                    if not isinstance(rec, dict) or rec.get('门诊号') != patient_no:
                        continue
                    ts = rec.get('采集时间') or rec.get('recordedAt') or rec.get('uploadedAt') or ''
                    date_str = ts[:10] if ts else None
                    if not date_str or date_str < cutoff:
                        continue
                    devices.add(dev_id)
                    count += 1
                    if earliest is None or ts < earliest:
                        earliest = ts
                    if latest is None or ts > latest:
                        latest = ts
                    b = bucket(date_str)
                    if type_key == '心率' and rec.get('心率值') is not None:
                        b['hr'].append(rec['心率值'])
                    elif type_key == '血氧' and rec.get('血氧饱和度') is not None:
                        b['spo2'].append(rec['血氧饱和度'])
                    elif type_key == '血压':
                        if rec.get('高压') is not None:
                            b['sbp'].append(rec['高压'])
                        if rec.get('低压') is not None:
                            b['dbp'].append(rec['低压'])
                    elif type_key == '体温' and rec.get('体温') is not None:
                        b['temp'].append(rec['体温'])
                    elif type_key == '步数' and rec.get('步数') is not None:
                        b['step'] = max(b['step'], rec['步数'] or 0)
        cur.close()

        def avg(lst):
            return round(sum(lst) / len(lst), 1) if lst else None

        daily = []
        for date_str in sorted(buckets.keys()):
            b = buckets[date_str]
            daily.append({
                'date': date_str,
                'hr_avg': avg(b['hr']),
                'hr_min': min(b['hr']) if b['hr'] else None,
                'hr_max': max(b['hr']) if b['hr'] else None,
                'spo2_avg': avg(b['spo2']),
                'spo2_min': min(b['spo2']) if b['spo2'] else None,
                'spo2_max': max(b['spo2']) if b['spo2'] else None,
                'sbp': avg(b['sbp']),
                'dbp': avg(b['dbp']),
                'temperature': avg(b['temp']),
                'step': b['step'] or None,
            })
        return {
            'count': count,
            'devices': sorted(devices, reverse=True),
            'earliest': earliest,
            'latest': latest,
            'daily': daily,
        }
    finally:
        conn.close()


def _iwown_daily_vitals(device_id, days=14):
    """iwown 设备日聚合: GROUP BY DATE(recorded_at), 仅 data_type='health' 行,
    AVG/MIN/MAX 各体征列, 步数取当日 MAX(手表侧是累计计数器, 取当日最大值即当日步数).
    附带设备状态条: 最近一条带 battery/rssi 的帧 + iwown_device.last_seen.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT DATE(recorded_at) AS d,
                   ROUND(AVG(hr_avg),1), MIN(hr_min), MAX(hr_max),
                   ROUND(AVG(spo2_avg),1), MIN(spo2_min), MAX(spo2_max),
                   ROUND(AVG(sbp),1), ROUND(AVG(dbp),1),
                   ROUND(AVG(temperature),2), MAX(step)
            FROM iwown_data
            WHERE device_id = %s AND data_type = 'health' AND recorded_at IS NOT NULL
              AND recorded_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
            GROUP BY DATE(recorded_at)
            ORDER BY d
        """, (device_id, days))
        daily = []
        for row in cur.fetchall():
            (d, hr_avg, hr_min, hr_max, spo2_avg, spo2_min, spo2_max,
             sbp, dbp, temperature, step) = row
            daily.append({
                'date': d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d),
                'hr_avg': float(hr_avg) if hr_avg is not None else None,
                'hr_min': hr_min, 'hr_max': hr_max,
                'spo2_avg': float(spo2_avg) if spo2_avg is not None else None,
                'spo2_min': spo2_min, 'spo2_max': spo2_max,
                'sbp': float(sbp) if sbp is not None else None,
                'dbp': float(dbp) if dbp is not None else None,
                'temperature': float(temperature) if temperature is not None else None,
                'step': step,
            })
        cur.execute("""
            SELECT battery, rssi FROM iwown_data
            WHERE device_id = %s AND (battery IS NOT NULL OR rssi IS NOT NULL)
            ORDER BY id DESC LIMIT 1
        """, (device_id,))
        batt_row = cur.fetchone()
        battery, rssi = batt_row if batt_row else (None, None)
        cur.execute('SELECT last_seen FROM iwown_device WHERE device_id = %s', (device_id,))
        seen_row = cur.fetchone()
        last_seen = (seen_row[0].strftime('%Y-%m-%d %H:%M:%S')
                     if seen_row and seen_row[0] else None)
        cur.close()
        return {
            'daily': daily,
            'device': {'device_id': device_id, 'battery': battery, 'rssi': rssi,
                       'last_seen': last_seen},
        }
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
        # gzip 压缩: 客户端支持 (Accept-Encoding 含 gzip) + 响应 > 1KB 才压缩,
        # 小响应压缩反而变大 (gzip header 开销). 实测大 JSON 可压到 1/15 大小.
        self.send_header('Vary', 'Accept-Encoding')
        accept_enc = (self.headers.get('Accept-Encoding') or '').lower()
        if 'gzip' in accept_enc and len(body) > 1024:
            body = gzip.compress(body, compresslevel=6)
            self.send_header('Content-Encoding', 'gzip')
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
            # 5.06-v10: 数据库一台设备一行 (按 deviceId 切片), 不依赖 wx_openid 列.
            # 客户端 / 调用方按 patientNo 过滤数据时, 可选 ?patientNo=100234:
            # 服务端在 Python 端把每行 data 数组按 '门诊号' 字段过滤, 返回精简版.
            # 不传 patientNo 时返回原样大 JSON (与 v6 行为一致).
            # v10 新增 ?page=N&size=M 分页 (deviceId DESC 排, 大号在前):
            #   不传 size 或 size=0 = 不分页, 返回全部 (兼容旧客户端).
            #   传 size > 0 时返回 records[page-1*size : page*size], total 字段给出过滤后总数.
            patient_no_filter = (query.get('patientNo') or [None])[0]
            try:
                page = max(1, int((query.get('page') or ['1'])[0]))
            except (ValueError, TypeError):
                page = 1
            try:
                size = max(0, min(500, int((query.get('size') or ['0'])[0])))
            except (ValueError, TypeError):
                size = 0
            try:
                conn = get_connection()
                cur = conn.cursor()
                # deviceId DESC: 大号 (新设备) 排前面, 与 dashboard 默认排序一致
                cur.execute(
                    'SELECT id, deviceId, data, createTime '
                    'FROM wearable_device_data ORDER BY deviceId DESC, createTime'
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
                total = len(rows)
                if size > 0:
                    start = (page - 1) * size
                    rows = rows[start:start + size]
                resp = {
                    'count': len(rows),
                    'total': total,
                    'records': rows,
                    'filteredBy': {'patientNo': patient_no_filter} if patient_no_filter else None,
                }
                if size > 0:
                    resp['page'] = page
                    resp['size'] = size
                    resp['hasMore'] = page * size < total
                self._send_json(200, resp)
            except Exception as e:
                self._send_json(500, {'error': str(e)})

        elif pathname == '/api/patients/summary':
            # 5.06-v10: 服务端按门诊号聚合, 不返回大 JSON 全文, 只返回每个患者的
            #   { patientNo, count, types: {心率:N, 血氧:M,...}, devices: [id1,id2], earliest, latest }
            # 实测响应体比 /api/data 小 ~50x, 适合 dashboard 30s 轮询.
            # 客户端要看具体某条数据再用 /api/data?patientNo=xxx 拉详情.
            try:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute(
                    'SELECT id, deviceId, data, createTime '
                    'FROM wearable_device_data ORDER BY deviceId DESC'
                )
                # patient_no -> { count, types, devices(set), earliest, latest }
                summary = {}
                unbound_count = 0  # 没门诊号的记录数 (用 _NULL_ 占位)
                row_count = 0
                for r in cur.fetchall():
                    row_count += 1
                    big_json = json.loads(r[2]) if r[2] else {}
                    if not isinstance(big_json, dict):
                        continue
                    for type_key, arr in big_json.items():
                        if not isinstance(arr, list):
                            continue
                        for rec in arr:
                            if not isinstance(rec, dict):
                                continue
                            p_no = rec.get('门诊号') or '_NULL_'
                            entry = summary.setdefault(p_no, {
                                'count': 0,
                                'types': {},
                                'typesValid': {},  # v10 patch: 各 type 的"有效"条数, 目前只日综合计入
                                'devices': set(),
                                'earliest': None,
                                'latest': None,
                            })
                            entry['count'] += 1
                            entry['types'][type_key] = entry['types'].get(type_key, 0) + 1
                            entry['devices'].add(r[1])
                            # v10 patch: 日综合的"有效"判定 — 排除 dailyRecords 全空的空跑
                            if type_key == '日综合':
                                is_empty = rec.get('is_empty')
                                if is_empty is None:  # 老数据未标 → 现场判
                                    is_empty = _is_daily_empty(rec.get('dailyRecords'))
                                if not is_empty:
                                    entry['typesValid']['日综合'] = entry['typesValid'].get('日综合', 0) + 1
                            # 时间戳: upsert_device_data 把客户端 recordedAt 落到中文字段 '采集时间' (ISO),
                            # 兼容老/异常记录回退 recordedAt/uploadedAt 字段, 最后兜底 row createTime
                            ts = rec.get('采集时间') or rec.get('recordedAt') or rec.get('uploadedAt')
                            if not ts and r[3]:
                                ts = r[3].strftime('%Y-%m-%dT%H:%M:%S.000Z')
                            if ts:
                                if entry['earliest'] is None or ts < entry['earliest']:
                                    entry['earliest'] = ts
                                if entry['latest'] is None or ts > entry['latest']:
                                    entry['latest'] = ts
                            if p_no == '_NULL_':
                                unbound_count += 1
                cur.close()
                conn.close()
                patients = []
                for p_no, entry in summary.items():
                    patients.append({
                        'patientNo': None if p_no == '_NULL_' else p_no,
                        'count': entry['count'],
                        'types': entry['types'],
                        'typesValid': entry['typesValid'],  # v10 patch: 仅日综合, 空 dict 表示无有效
                        # 设备 ID 按 desc 排, 大号在前
                        'devices': sorted(entry['devices'], reverse=True),
                        'earliest': entry['earliest'],
                        'latest': entry['latest'],
                    })
                # 默认按 latest desc 排, 最近活跃的在前, NULL 放最后
                patients.sort(key=lambda x: (x['latest'] or '', x['patientNo'] or ''), reverse=True)
                self._send_json(200, {
                    'count': len(patients),
                    'rows': row_count,
                    'unboundRecords': unbound_count,
                    'patients': patients,
                })
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

        elif pathname == '/api/zhenmaiyi/list':
            # v10 patch: 诊脉仪记录列表 (不返回 base64 大字段, 看板列表用)
            try:
                result = query_zhenmaiyi_list()
                self._send_json(200, result)
            except Exception as e:
                traceback.print_exc()
                self._send_json(500, {'error': str(e)})

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

        elif pathname == '/api/platform/patients':
            # 随访平台 M1: 患者列表 + 三链路绑定态 + 最近上传时间 + 未关闭报警数
            try:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute(
                    'SELECT patient_no, name, gender, age, group_tag, zhenmaiyi_case_id, note '
                    'FROM platform_patient ORDER BY patient_no'
                )
                patient_rows = cur.fetchall()

                cur.execute('SELECT device_id, patient_no, last_seen FROM iwown_device WHERE patient_no IS NOT NULL')
                iwown_map = {}
                for dev_id, p_no, last_seen in cur.fetchall():
                    iwown_map[p_no] = {
                        'device_id': dev_id,
                        'last_seen': last_seen.strftime('%Y-%m-%d %H:%M:%S') if last_seen else None,
                    }

                cur.execute(
                    "SELECT device_id, MAX(recorded_at) FROM iwown_data "
                    "WHERE data_type = 'health' GROUP BY device_id"
                )
                iwown_last_health = {
                    dev: (ts.strftime('%Y-%m-%d %H:%M:%S') if ts else None)
                    for dev, ts in cur.fetchall()
                }

                cur.execute(
                    "SELECT patient_no, COUNT(*) FROM platform_alarm "
                    "WHERE status != 'closed' AND patient_no IS NOT NULL GROUP BY patient_no"
                )
                alarm_open_map = {p_no: n for p_no, n in cur.fetchall()}
                cur.close()
                conn.close()

                s101_map = _s101_scan_by_patient()

                patients = []
                for (p_no, name, gender, age, group_tag, zm_case, note) in patient_rows:
                    iw = iwown_map.get(p_no)
                    s101_entry = s101_map.get(p_no)
                    iwown_last = None
                    if iw:
                        iwown_last = iwown_last_health.get(iw['device_id']) or iw['last_seen']
                    patients.append({
                        'patient_no': p_no, 'name': name, 'gender': gender, 'age': age,
                        'group_tag': group_tag, 'note': note,
                        'bindings': {
                            'iwown': iw['device_id'] if iw else None,
                            's101': bool(s101_entry),
                            'zhenmaiyi': zm_case,
                        },
                        'last_upload': {'iwown': iwown_last, 's101': s101_entry['latest'] if s101_entry else None},
                        'alarm_open': alarm_open_map.get(p_no, 0),
                    })
                self._send_json(200, {'ok': True, 'patients': patients})
            except Exception as e:
                traceback.print_exc()
                self._send_json(500, {'ok': False, 'error': str(e)})

        elif pathname == '/api/platform/patient/vitals':
            # 随访平台 M1: 单患者跨链路体征日聚合 (iwown 日聚合 + S101 门诊号解析 + 诊脉仪最新一条)
            patient_no = (query.get('patientNo') or [None])[0]
            try:
                days = max(1, min(90, int((query.get('days') or ['14'])[0])))
            except (ValueError, TypeError):
                days = 14
            if not patient_no:
                self._send_json(400, {'ok': False, 'error': '缺少 patientNo'})
                return
            try:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute(
                    'SELECT patient_no, name, gender, age, group_tag, zhenmaiyi_case_id, note, '
                    'created_at, updated_at FROM platform_patient WHERE patient_no = %s',
                    (patient_no,)
                )
                row = cur.fetchone()
                if not row:
                    cur.close()
                    conn.close()
                    self._send_json(404, {'ok': False, 'error': '患者不存在: {}'.format(patient_no)})
                    return
                cols = ['patient_no', 'name', 'gender', 'age', 'group_tag',
                        'zhenmaiyi_case_id', 'note', 'created_at', 'updated_at']
                patient = dict(zip(cols, row))
                for k in ('created_at', 'updated_at'):
                    if patient.get(k) is not None and hasattr(patient[k], 'strftime'):
                        patient[k] = patient[k].strftime('%Y-%m-%d %H:%M:%S')

                cur.execute(
                    'SELECT device_id FROM iwown_device WHERE patient_no = %s '
                    'ORDER BY last_seen DESC LIMIT 1', (patient_no,)
                )
                iw_row = cur.fetchone()
                cur.close()
                conn.close()

                iwown_result = _iwown_daily_vitals(iw_row[0], days=days) if iw_row else \
                    {'daily': [], 'device': None}
                s101_result = _s101_patient_vitals(patient_no, days=days)

                zhenmaiyi_result = None
                if patient.get('zhenmaiyi_case_id'):
                    zconn = get_connection()
                    try:
                        zcur = zconn.cursor()
                        zcur.execute(
                            'SELECT case_id, patient_name, patient_gender, patient_age, detect_time, '
                            'conclusion, pulse_label, uploaded_at FROM zhenmaiyi WHERE case_id = %s '
                            'ORDER BY detect_time DESC, uploaded_at DESC LIMIT 1',
                            (patient['zhenmaiyi_case_id'],)
                        )
                        zrow = zcur.fetchone()
                        if zrow:
                            zcols = ['case_id', 'patient_name', 'patient_gender', 'patient_age',
                                     'detect_time', 'conclusion', 'pulse_label', 'uploaded_at']
                            zhenmaiyi_result = dict(zip(zcols, zrow))
                            for k in ('detect_time', 'uploaded_at'):
                                if zhenmaiyi_result.get(k) is not None and hasattr(zhenmaiyi_result[k], 'strftime'):
                                    zhenmaiyi_result[k] = zhenmaiyi_result[k].strftime('%Y-%m-%d %H:%M:%S')
                        zcur.close()
                    finally:
                        zconn.close()

                self._send_json(200, {
                    'ok': True, 'patient': patient,
                    'iwown': iwown_result, 's101': s101_result, 'zhenmaiyi': zhenmaiyi_result,
                })
            except Exception as e:
                traceback.print_exc()
                self._send_json(500, {'ok': False, 'error': str(e)})

        elif pathname == '/api/platform/alarms':
            # 随访平台 M2: 报警工作台列表. status 支持 new/acked/followed/closed 精确值 +
            # 'open' meta 值(= status != 'closed'); 缺省/其他值 = 全部。
            status = (query.get('status') or [None])[0]
            patient_no = (query.get('patientNo') or [None])[0]
            try:
                limit = max(1, min(500, int((query.get('limit') or ['50'])[0])))
            except (ValueError, TypeError):
                limit = 50
            result, err = query_platform_alarms(status=status, patient_no=patient_no, limit=limit)
            if err:
                self._send_json(500, {'ok': False, 'error': err})
            else:
                self._send_json(200, result)

        else:
            self._send_json(200, {
                'service': '智能随访-可穿戴设备数据接收服务',
                'mode': '一台设备一行 + 大 JSON 汇总; 患者标识 = 大 JSON 每条记录的 "门诊号" 字段',
                'version': '5.06-v10',
                'endpoints': {
                    'GET  /api/status': '服务状态',
                    'GET  /api/data': '查询所有设备 (可选 ?patientNo= 过滤; ?page=N&size=M 分页, deviceId DESC; 响应支持 gzip)',
                    'GET  /api/patients/summary': '按门诊号聚合摘要 (count/types/typesValid/devices/earliest/latest, ~50x 小于 /api/data; typesValid 仅日综合, 排除 dailyRecords 全空空跑)',
                    'POST /api/health-data': 'UPSERT 体征数据 (按 deviceId 切片, 透传 patientNo 写入大 JSON)',
                    'POST /api/device/register': '按 mac (优先) 或 device_sign UPSERT 到 wearable_device 并返回 deviceId',
                    'POST /api/device/merge': '合并 wearable_device_data 两行: {fromDeviceId, toDeviceId}',
                    'GET  /api/device/by-sign?sign=...': '按 sign 查 wearable_device（不创建）',
                    'DELETE /api/device/:id': '删 wearable_device 一行 + 联动删该 deviceId 的所有数据',
                    'POST /api/zhenmaiyi/upload': 'v10 patch: 浏览器解析诊脉仪 zip 后批量入库 (zhenmaiyi 表, UPSERT by case_id)',
                    'GET  /api/zhenmaiyi/list': 'v10 patch: 列全部诊脉仪记录 (不含 base64 附件)',
                    'GET  /api/platform/patients': '随访平台 M1: 患者列表 + 绑定态 + 最近上传时间 + 未关闭报警数',
                    'POST /api/platform/patient': '随访平台 M1: UPSERT platform_patient (建档/改档)',
                    'POST /api/platform/bind': "随访平台 M1: 绑定/解绑 {patient_no, chain:'iwown'|'zhenmaiyi', key, unbind}",
                    'GET  /api/platform/patient/vitals?patientNo=&days=': '随访平台 M1: 单患者跨链路体征日聚合',
                    'GET  /api/platform/alarms?status=&patientNo=&limit=': '随访平台 M2: 报警工作台列表 (status 支持 open meta 值)',
                    'POST /api/platform/alarm/ingest': '随访平台 M2: 扫 iwown_data alarm 行 -> platform_alarm (幂等)',
                    'POST /api/platform/alarm/transition': "随访平台 M2: 报警状态流转 {alarm_id, action:'ack'|'call'|'visit'|'note'|'close', result_text, operator}",
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

            elif pathname == '/api/zhenmaiyi/upload':
                # v10 patch: 浏览器端 pulse-dashboard.html 解析诊脉仪 .zip 后批量入库
                # body: { patients: [...], constitution_xlsx_b64, pulse_xlsx_b64, source_zip_name }
                patients = body.get('patients') or []
                if not isinstance(patients, list):
                    self._send_json(400, {'error': 'patients 必须是数组'})
                    return
                result = upsert_zhenmaiyi(
                    patients,
                    body.get('constitution_xlsx_b64') or '',
                    body.get('pulse_xlsx_b64') or '',
                    body.get('source_zip_name') or '',
                )
                self._send_json(200, {'success': True, **result})

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

            elif pathname == '/api/platform/patient':
                if not check_platform_token(self):
                    return
                result, err = upsert_platform_patient(body)
                if err:
                    self._send_json(400, {'ok': False, 'error': err})
                else:
                    self._send_json(200, {'ok': True, **result})

            elif pathname == '/api/platform/bind':
                if not check_platform_token(self):
                    return
                result, err = platform_bind(body)
                if err:
                    self._send_json(400, {'ok': False, 'error': err})
                else:
                    self._send_json(200, {'ok': True, **result})

            elif pathname == '/api/platform/alarm/ingest':
                if not check_platform_token(self):
                    return
                result, err = platform_alarm_ingest()
                if err:
                    self._send_json(500, {'ok': False, 'error': err})
                else:
                    self._send_json(200, result)

            elif pathname == '/api/platform/alarm/transition':
                if not check_platform_token(self):
                    return
                result, err = platform_alarm_transition(body)
                if err:
                    self._send_json(400, {'ok': False, 'error': err})
                else:
                    self._send_json(200, {'ok': True, **result})

            else:
                self._send_json(404, {'error': 'Not found. Available: POST /api/health-data, POST /api/device/register, POST /api/device/merge, POST /api/zhenmaiyi/upload, POST /api/platform/patient, POST /api/platform/bind, POST /api/platform/alarm/ingest, POST /api/platform/alarm/transition'})

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
        # v10 patch: 启动时确保 zhenmaiyi 表存在 (idempotent), 接收浏览器端解析诊脉仪 zip 上传
        ensure_zhenmaiyi_table()
        # 随访平台 1.0 M1: 启动时确保 platform_patient/alarm/followup_log 3 张表存在 (idempotent)
        ensure_platform_tables()
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

    if PLATFORM_TOKEN:
        print('[启动] PLATFORM_TOKEN 已配置 (长度 {}), POST /api/platform/* 写接口需带 X-Platform-Token'.format(len(PLATFORM_TOKEN)))
    else:
        print('[警告] PLATFORM_TOKEN 未配置, POST /api/platform/* 写接口不做鉴权 (开发模式). '
              '生产部署前请 systemd 加 Environment="PLATFORM_TOKEN=xxx" 后重启')

    server = HTTPServer(('0.0.0.0', PORT), HealthDataHandler)
    print('[启动] 智能随访数据接收服务 v5.06-v9: http://0.0.0.0:{}'.format(PORT))
    print('[模式] 一台设备一行 + 大 JSON 汇总; 患者标识 = 大 JSON 每条记录的 "门诊号" 字段')
    print('[端点] POST /api/health-data       UPSERT 体征数据 (按 deviceId 切片, 透传 patientNo)')
    print('[端点] POST /api/device/register   设备名册 UPSERT (mac 优先)')
    print('[端点] POST /api/device/merge      合并 wearable_device_data 两行')
    print('[端点] GET  /api/device/by-sign    设备名册查询')
    print('[端点] GET  /api/status            服务状态')
    print('[端点] GET  /api/data              查询所有设备 (?patientNo= 过滤大 JSON 内的记录)')
    print('[端点] GET  /api/platform/patients              随访平台 M1: 患者列表 + 绑定态')
    print('[端点] POST /api/platform/patient                随访平台 M1: UPSERT 患者建档')
    print('[端点] POST /api/platform/bind                   随访平台 M1: 绑定/解绑 iwown|zhenmaiyi')
    print('[端点] GET  /api/platform/patient/vitals         随访平台 M1: 单患者跨链路体征日聚合')
    print('[端点] POST /api/platform/alarm/ingest            随访平台 M2: iwown 报警行 -> platform_alarm (幂等)')
    print('[端点] GET  /api/platform/alarms                  随访平台 M2: 报警工作台列表')
    print('[端点] POST /api/platform/alarm/transition        随访平台 M2: 报警状态流转 (state machine)')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n[停止] 服务已关闭')
        server.server_close()
