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
  自动摄入: 环境变量 PLATFORM_INGEST_INTERVAL_MIN (默认 '10', '0' 关闭) 控制启动时是否拉起一个
  daemon 线程, 每 N 分钟调用一次与 POST /api/platform/alarm/ingest 相同的核心函数
  platform_alarm_ingest(), 不必再手动点"拉取新报警"按钮; 端点本身仍保留、仍走 token 门禁。

随访平台 1.0 M4 (佩戴依从性, 见 docs/随访平台1.0设计方案.html §3.4/④⑤):
- GET  /api/platform/compliance?patientNo=&days=  单患者每日佩戴率(佩戴小时数/24) + 当日未佩戴报警数标注,
                                          纯查询视图, 不新增表; 未绑定 iwown 的患者返回空 daily。
  /api/platform/patients 响应同时新增 wear_rate_7d (近 7 天平均佩戴率, 未绑定为 null), 供列表卡片显示。

随访平台 1.1 M5 (随访计划引擎): 新增 1 张表 platform_plan (随访计划); "任务"永远是从
active=1 的计划 + next_due 现算, 从不落地存储:
- GET  /api/platform/plans?patientNo=&active=      计划列表 (关联患者姓名)
- POST /api/platform/plan                          建/改计划 {id?, patient_no, name,
                                          frequency_days|null(一次性), next_due 'YYYY-MM-DD',
                                          active?, note?}; 传 {id, active:0} 即停用
- GET  /api/platform/tasks?horizon_days=7          今日待办 (active=1 且 next_due<=today+horizon,
                                          overdue_days=max(0, today-next_due), 按 next_due 升序天然
                                          就是 overdue 在前)
- POST /api/platform/task/complete                 完成任务 {plan_id, method:'call'|'visit'|'note',
                                          result_text, operator}; 单事务: 写 1 行
                                          platform_followup_log(plan_id 关联) + 循环计划推进
                                          next_due=完成当日+frequency_days / 一次性计划 active=0。
  /api/platform/patients 响应同时新增 task_due_count (今日到期+逾期的随访任务数), 纯附加字段,
  供列表卡片任务角标直接用, 不必再单独请求 /api/platform/tasks。

5.06-v9 决定: 不动 wearable_device_data schema (无 wx_openid / patient_no 列),
             患者标识统一写在大 JSON 每条记录的 '门诊号' 字段里, 切片仍按 deviceId 一台设备一行.
             v7/v8 残留的 wx.login / ble_event 端点和函数保留在文件中但不在启动时激活,
             如需启用请阅读 main 块中的注释.

部署：scp 本文件到 192.168.4.104:/opt/suifang/health_server.py，systemd 启动
"""
import os
import io
import csv
import json
import re
import gzip
import zipfile
import time
import datetime
import threading
import traceback
import urllib.request
import urllib.parse
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import pymysql

# ============ 配置 ============
# 生产不设 PORT 环境变量, 行为与硬编码 3000 时完全一致。可覆盖是为了能在本机
# 另起一个实例跑端到端测试 —— 否则测试要么占用 3000, 要么只能打生产。
PORT = int(os.environ.get('PORT') or 3000)
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
    """随访平台 1.0 M1 + 1.1 M5: 创建 platform_* 4 张表 (idempotent).

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
                alarm_type VARCHAR(24) DEFAULT NULL COMMENT 'fall/sos/hr/spo2/bp/temp/sedentary/not_worn/low_battery + M7 的 *_trend/pulse_report',
                severity ENUM('crit','warn','info') DEFAULT NULL,
                lat DECIMAL(10,6) DEFAULT NULL,
                lng DECIMAL(10,6) DEFAULT NULL,
                payload_json JSON DEFAULT NULL,
                source_data_id BIGINT DEFAULT NULL COMMENT '→iwown_data.id, 解析重跑幂等去重',
                source_chain VARCHAR(16) NOT NULL DEFAULT 'iwown' COMMENT 'M7: iwown/s101/zhenmaiyi',
                dedup_key VARCHAR(191) DEFAULT NULL COMMENT 'M7: 非 iwown 链的幂等键, 见 ensure_platform_alarm_m7_columns',
                status ENUM('new','acked','followed','closed') DEFAULT 'new',
                occurred_at DATETIME DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uk_source_data_id (source_data_id),
                UNIQUE KEY uk_dedup_key (dedup_key),
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
                INDEX idx_patient_no (patient_no),
                INDEX idx_plan (plan_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台回访/处理记录 (M2 起写入, M1 只建表)'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_plan (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                patient_no VARCHAR(64) NOT NULL,
                name VARCHAR(128) NOT NULL COMMENT '如 术后1月电话随访',
                frequency_days INT DEFAULT NULL COMMENT 'NULL=一次性, 否则每 N 天重复',
                next_due DATE NOT NULL,
                active TINYINT(1) DEFAULT 1,
                note VARCHAR(255) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_patient_no (patient_no),
                INDEX idx_next_due (next_due)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 1.1 随访计划 (任务由 active+next_due 现算, 不单独存储)'
        """)
        print('[启动] platform_patient / platform_alarm / platform_followup_log / platform_plan 表已就绪')
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


def ensure_followup_log_plan_index():
    """M5 性能修复: 确保 platform_followup_log 表有 idx_plan (plan_id) 索引 (idempotent).

    生产库该表在 M2 就已建好 (无此索引), CREATE TABLE IF NOT EXISTS 对已存在的表不会补索引,
    所以需要单独做一次 ALTER-if-missing 迁移, 与 ensure_mac_column() 同一惯例。
    query_platform_tasks() 的 last_done 从相关子查询改成了 LEFT JOIN 派生表 GROUP BY plan_id,
    没有这个索引会退化成全表扫描。
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'platform_followup_log' AND INDEX_NAME = 'idx_plan'",
            (DB_CONFIG['database'],)
        )
        if cur.fetchone()[0] == 0:
            print('[启动] platform_followup_log.idx_plan 不存在, 添加中...')
            cur.execute('ALTER TABLE platform_followup_log ADD INDEX idx_plan (plan_id)')
            print('[启动] platform_followup_log.idx_plan 索引添加完成')
        else:
            print('[启动] platform_followup_log.idx_plan 索引已存在, 跳过 ALTER')
        cur.close()
    except Exception as e:
        print('[启动] ensure_followup_log_plan_index 失败:', e)
    finally:
        conn.close()


def ensure_platform_alarm_m7_columns():
    """M7: 给 platform_alarm 补 source_chain / dedup_key 两列 + uk_dedup_key 唯一索引 (idempotent).

    生产库该表在 M2 就建好了, CREATE TABLE IF NOT EXISTS 不会给已存在的表补列,
    所以走和 ensure_mac_column()/ensure_followup_log_plan_index() 同一惯例的 ALTER-if-missing。

    为什么不复用现成的 uk_source_data_id 做新链的幂等:
      那一列的语义是 iwown_data.id —— 一个整数外键。而 S101 的体征存在
      wearable_device_data 的"每设备一行大 JSON"里, 单条采样点根本没有行 id 可引用。
      所以新链改用自造的字符串幂等键 dedup_key:
        S101 阈值越限   s101:th:{门诊号}:{metric}:{采集时间}
        S101 趋势异常   s101:tr:{门诊号}:{metric}:{日期}
        脉诊仪新报告    zmy:{case_id}
      存量 iwown 行 dedup_key 留 NULL —— MySQL 的 UNIQUE 允许多个 NULL, 两套幂等键
      各走各的索引, 互不干扰, 也不需要回填历史数据。
      191 字符 × 4 字节 = 764B, 在 InnoDB 单列索引 3072B 上限内。
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        for col, ddl in (
            ('source_chain',
             "ALTER TABLE platform_alarm ADD COLUMN source_chain VARCHAR(16) NOT NULL DEFAULT 'iwown' "
             "COMMENT 'M7: iwown/s101/zhenmaiyi'"),
            ('dedup_key',
             "ALTER TABLE platform_alarm ADD COLUMN dedup_key VARCHAR(191) DEFAULT NULL "
             "COMMENT 'M7: 非 iwown 链的幂等键'"),
        ):
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'platform_alarm' AND COLUMN_NAME = %s",
                (DB_CONFIG['database'], col)
            )
            if cur.fetchone()[0] == 0:
                print('[启动] platform_alarm.{} 不存在, 添加中...'.format(col))
                cur.execute(ddl)
                print('[启动] platform_alarm.{} 列添加完成'.format(col))

        cur.execute(
            "SELECT COUNT(*) FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'platform_alarm' AND INDEX_NAME = 'uk_dedup_key'",
            (DB_CONFIG['database'],)
        )
        if cur.fetchone()[0] == 0:
            print('[启动] platform_alarm.uk_dedup_key 不存在, 添加中...')
            cur.execute('ALTER TABLE platform_alarm ADD UNIQUE KEY uk_dedup_key (dedup_key)')
            print('[启动] platform_alarm.uk_dedup_key 索引添加完成')
        else:
            print('[启动] platform_alarm M7 列/索引已就绪, 跳过 ALTER')
        cur.close()
    except Exception as e:
        print('[启动] ensure_platform_alarm_m7_columns 失败:', e)
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
                   a.source_chain,
                   (SELECT COUNT(*) FROM platform_followup_log f WHERE f.alarm_id = a.id) AS followup_count
            FROM platform_alarm a
            LEFT JOIN platform_patient p ON p.patient_no = a.patient_no
            WHERE {}
            ORDER BY a.occurred_at DESC, a.id DESC
            LIMIT %s
        """.format(' AND '.join(where)), params)
        cols = ['id', 'patient_no', 'patient_name', 'device_id', 'alarm_type', 'severity',
                'lat', 'lng', 'payload_json', 'status', 'occurred_at', 'created_at',
                'source_chain', 'followup_count']
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


# ============ 随访平台 1.2 M7 (体征阈值预警 + 趋势异常检测) ============
# 对应北大六院项目 ▲1.8.3 承诺函里第 3、4 条待第三方检测的指标:
#   3. 趋势分析: 自动分析心率、睡眠、血压等指标的变化趋势, 识别异常波动
#   4. 异常预警: 当监测指标超出正常阈值、出现异常波动时, 系统自动触发预警
#
# 为什么不能复用 M2 的 platform_alarm_ingest:
#   那一套只吃 iwown 4G 手环**设备侧**推上来的 0x12 报警帧 (classify_iwown_alarm) ——
#   判定发生在手表固件里, 服务端只做解码归类。而承诺函点名的两款二类械是
#   「智能手环(小程序扫码绑定 + 门诊号)」= S101/R04 和「脉诊仪」, 这两条链传上来的是
#   **原始体征**, 设备侧不产报警帧, 服务端在 M7 之前一条判定规则都没有 —— 预警能力
#   恰好长在唯一不属于承诺范围的那条链上。M7 把服务端判定补齐。

VITAL_THRESHOLDS = {
    # 成人通用预警线。阈值全部集中在这一张表里, 改这里即改全局判定,
    # 不要把数字散进判定函数 —— 验收时临床方要逐条核对的就是这张表。
    #
    # 待临床校准: 北大六院是精神专科, 抗精神病药(氯氮平/喹硫平等)的窦性心动过速与
    # 体位性低血压是已知常见不良反应, 用通用成人线会在这类在管患者上持续误报。
    # 上线前应由临床方按科室实际把 hr / sbp / dbp 三项重新定线。
    # min_samples = 当天至少要有几个采样点才让这一天参与趋势判定。心率/血氧这类高频量给 3,
    # 防止清早只测了 1 次就拿这一个点当"今日均值"去和 7 天基线比 —— 自动摄入线程每 10 分钟
    # 跑一次, 不设这道门槛的话每天上午都会刷一批假的趋势异常。睡眠是一晚一条, 只能给 1。
    'hr':    {'type': 'hr',    'label': '心率',       'unit': 'bpm',    'min_samples': 3,
              'crit_low': 40,   'warn_low': 50,   'warn_high': 120,  'crit_high': 150},
    'spo2':  {'type': 'spo2',  'label': '血氧饱和度', 'unit': '%',      'min_samples': 3,
              'crit_low': 85,   'warn_low': 90,   'warn_high': None, 'crit_high': None},
    'sbp':   {'type': 'bp',    'label': '收缩压',     'unit': 'mmHg',   'min_samples': 2,
              'crit_low': 80,   'warn_low': 90,   'warn_high': 160,  'crit_high': 180},
    'dbp':   {'type': 'bp',    'label': '舒张压',     'unit': 'mmHg',   'min_samples': 2,
              'crit_low': 50,   'warn_low': 60,   'warn_high': 100,  'crit_high': 110},
    'temp':  {'type': 'temp',  'label': '体温',       'unit': '℃',     'min_samples': 2,
              'crit_low': 35.0, 'warn_low': 36.0, 'warn_high': 37.5, 'crit_high': 39.0},
    # 睡眠: 承诺函第 3 条把"睡眠"和心率、血压并列写进了要做趋势分析的指标, 必须有。
    # 对精神专科它还不只是陪跑指标 —— 入睡困难/早醒/嗜睡是抑郁与躁狂发作的核心症状,
    # 个体基线偏离(平时睡 7 小时的人连着两晚睡 3 小时)比绝对阈值更有临床意义。
    'sleep': {'type': 'sleep', 'label': '睡眠时长',   'unit': '分钟',   'min_samples': 1,
              'crit_low': 180,  'warn_low': 300,  'warn_high': 660,  'crit_high': 840},
}

# S101 大 JSON 的类型键 -> [(记录内取值字段, 内部 metric 名)]。
# metric 名与 _s101_patient_vitals 的日聚合分桶键保持一致, 两处要改必须同时改。
# '睡眠' 的值是"深睡+浅睡"两个字段相加的派生量, 不是直取, 所以这里映射为空列表,
# 由 _s101_scan_for_alarms 单独算 (见那里的注释)。
S101_METRIC_FIELDS = {
    '心率': [('心率值', 'hr')],
    '血氧': [('血氧饱和度', 'spo2')],
    '血压': [('高压', 'sbp'), ('低压', 'dbp')],
    '体温': [('体温', 'temp')],
    '睡眠': [],
}

TREND_BASELINE_DAYS = 7       # 基线窗口: 判定日往前 7 个自然日
TREND_MIN_BASELINE_DAYS = 4   # 基线里至少要有 4 天有数据, 不足则本日不判(样本不够宁可漏报)
TREND_SIGMA_WARN = 2.0        # 偏离 ≥2σ -> info
TREND_SIGMA_HIGH = 3.0        # 偏离 ≥3σ -> warn
TREND_MIN_DELTA = {           # 且绝对偏移要同时过这条线, 见 detect_trend_anomalies 的双门槛说明
    'hr': 8.0, 'spo2': 3.0, 'sbp': 12.0, 'dbp': 8.0, 'temp': 0.4, 'sleep': 90.0,
}
# σ 低于这个值一律当"基线完全平坦"处理。基线各天数值相同时, sum((x-mu)**2) 得到的不是精确 0
# 而是 1e-15 量级的浮点残差, 直接拿去做除数会让报警文案里出现 "(4222124650659840σ)" 这种
# 数字 —— 验收演示时这一条足以毁掉整页的可信度。
TREND_SIGMA_EPS = 1e-6


def _fmt_num(v):
    """40.0 -> '40', 37.5 -> '37.5'。报警文案里不出现无意义的 .0 小数尾巴。"""
    f = float(v)
    return str(int(f)) if f == int(f) else str(round(f, 1))


def _s101_ts_to_local(ts):
    """S101 采集时间 (ISO-8601 UTC, 形如 2026-07-26T10:00:00.000Z) -> 北京时间 datetime。

    upsert_device_data 落库时写的是 datetime.utcnow() 的 ISO Z 串 (见 record['采集时间']),
    而 platform_alarm.occurred_at 这一列上已有的 iwown 行来自 iwown_data.recorded_at ——
    那是"帧内测量时间", 国内部署的 4G 手表报的是设备本地时间即北京时间。同一列混两种时区
    会让报警工作台的时间线错 8 小时, 所以这里统一折成北京时间再入库。
    原始 UTC 串完整保留在 payload_json.sample_ts_utc 里, 需要时可回溯。

    没有 Z 后缀的老记录按"已是本地时间"处理, 不再加 8 小时。
    """
    if not ts:
        return None
    s = str(ts).strip()
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})', s)
    if not m:
        return None
    try:
        dt = datetime.datetime(*[int(x) for x in m.groups()])
    except ValueError:
        return None
    return dt + datetime.timedelta(hours=8) if s.endswith('Z') else dt


def classify_vital_threshold(metric, value):
    """单个采样点的越限判定。返回 (alarm_type, severity, detail) 或 None(在正常区间内)。

    crit 判在 warn 前面: 一个 185mmHg 的收缩压同时越过 warn_high(160) 和 crit_high(180),
    只出 crit 一条, 不出两条。
    """
    rule = VITAL_THRESHOLDS.get(metric)
    if rule is None or value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None

    hit = None
    if rule['crit_high'] is not None and v >= rule['crit_high']:
        hit = ('crit', 'high', rule['crit_high'])
    elif rule['crit_low'] is not None and v <= rule['crit_low']:
        hit = ('crit', 'low', rule['crit_low'])
    elif rule['warn_high'] is not None and v >= rule['warn_high']:
        hit = ('warn', 'high', rule['warn_high'])
    elif rule['warn_low'] is not None and v <= rule['warn_low']:
        hit = ('warn', 'low', rule['warn_low'])
    if hit is None:
        return None

    severity, direction, bound = hit
    text = '{} {}{} {}{} {}{}'.format(
        rule['label'], _fmt_num(v), rule['unit'],
        '高于' if direction == 'high' else '低于',
        '危急阈值' if severity == 'crit' else '预警阈值',
        _fmt_num(bound), rule['unit'])
    return rule['type'], severity, {
        'rule': 'threshold',
        'metric': metric,
        'label': rule['label'],
        'value': v,
        'unit': rule['unit'],
        'direction': direction,
        'bound': bound,
        'text': text,
    }


def detect_trend_anomalies(series, metric, judge_from_date):
    """个体基线偏离法识别"异常波动"。返回 [(date_str, severity, detail)]。

    series: {date_str: 当日均值}
    judge_from_date: 只对 >= 这个日期的天出结论, 更早的天只作基线用。

    为什么不用"连续 N 天超阈值"那类规则: 那本质还是阈值判定, 抓不到"这个人平时 58bpm,
    这两天变 88bpm"—— 88 不越任何绝对阈值, 但相对他自己的基线是显著异常。承诺函写的
    "识别异常波动"指的正是这种个体内偏离; 绝对阈值那条线已经由 classify_vital_threshold 管了,
    两者互补, 不互相替代。

    σ 倍数和最小绝对幅度这两道门槛缺一不可: 只看 σ, 基线极稳的患者(σ→0)会被 1bpm 的
    正常抖动刷屏; 只看绝对幅度, 基线本来就飘的患者会天天报。
    """
    out = []
    if not series:
        return out
    rule = VITAL_THRESHOLDS.get(metric)
    if rule is None:
        return out
    min_delta = TREND_MIN_DELTA.get(metric, 0.0)
    all_dates = sorted(series)

    for date_str in all_dates:
        if date_str < judge_from_date:
            continue
        try:
            day = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            continue
        lo = (day - datetime.timedelta(days=TREND_BASELINE_DAYS)).strftime('%Y-%m-%d')
        base = [series[d] for d in all_dates if lo <= d < date_str]
        if len(base) < TREND_MIN_BASELINE_DAYS:
            continue

        mu = sum(base) / len(base)
        sigma = (sum((x - mu) ** 2 for x in base) / len(base)) ** 0.5
        v = series[date_str]
        delta = v - mu
        if abs(delta) < min_delta:
            continue
        flat = sigma < TREND_SIGMA_EPS
        if not flat and abs(delta) < TREND_SIGMA_WARN * sigma:
            continue
        # 基线完全平坦时 σ 倍数没有意义(除数是浮点残差), 判定只由 min_delta 那道门槛决定;
        # 一个稳定在 58bpm 的人突然 88bpm, 按 warn 报是对的。
        severity = 'warn' if flat or abs(delta) >= TREND_SIGMA_HIGH * sigma else 'info'

        text = '{}日均 {}{} 较前 {} 天基线 {}{} {} {}{} ({})'.format(
            rule['label'], _fmt_num(v), rule['unit'], len(base),
            _fmt_num(mu), rule['unit'],
            '上升' if delta > 0 else '下降', _fmt_num(abs(delta)), rule['unit'],
            '基线无波动' if flat else _fmt_num(abs(delta) / sigma) + 'σ')
        out.append((date_str, severity, {
            'rule': 'trend',
            'metric': metric,
            'label': rule['label'],
            'value': round(v, 1),
            'unit': rule['unit'],
            'baseline_mean': round(mu, 1),
            'baseline_sigma': round(sigma, 2),
            'baseline_days': len(base),
            'delta': round(delta, 1),
            'text': text,
        }))
    return out


def _s101_scan_for_alarms(scan_from_date):
    """单趟扫 wearable_device_data, 一次产出阈值判定和趋势判定两份原料。

    返回 (breaches, daily):
      breaches: [{patient_no, device_id, metric, value, ts_utc, occurred_at, alarm_type,
                  severity, detail}]  —— 越限的原始采样点
      daily:    {patient_no: {metric: {date: {'sum','n','device_id'}}}} —— 日均值序列原料

    为什么合成一趟扫: _s101_patient_vitals 是"每调用一次全表扫一遍"的写法, 后台 ingest 要
    对全部患者跑判定, 沿用它就会把整张 wearable_device_data 扫 N 遍 (M6 的 _export_vitals
    踩过同一个坑, 见那里的注释)。这里一趟扫完按 (患者, 指标, 日期) 分桶。

    scan_from_date 'YYYY-MM-DD': 早于此日期的采样点直接丢。调用方要把趋势基线需要的历史
    天数一并算进去(见 platform_vital_alarm_ingest)。
    """
    breaches = []
    daily = {}
    conn = get_connection()
    try:
        cur = conn.cursor()
        # createTime 是 upsert_device_data 每次写入时用 datetime.now() 重置的(见那里的 now_str),
        # 所以一行只要在 scan_from 之后没被写过, 它的大 JSON 里就不可能有 scan_from 之后的记录 ——
        # 直接在 SQL 层跳掉, 不用把那几 MB 的 LONGTEXT 拉回来做无用的 json.loads。
        # 这个循环每 PLATFORM_INGEST_INTERVAL_MIN 分钟跑一次, 久不上传的设备不该次次陪跑。
        # createTime 为 NULL 的历史行无从判断, 一律扫。
        cur.execute('SELECT deviceId, data FROM wearable_device_data '
                    'WHERE createTime IS NULL OR createTime >= %s', (scan_from_date,))
        for dev_id, data_raw in cur.fetchall():
            try:
                big_json = json.loads(data_raw) if data_raw else {}
            except json.JSONDecodeError:
                big_json = {}
            if not isinstance(big_json, dict):
                continue
            for type_key, arr in big_json.items():
                if type_key not in S101_METRIC_FIELDS or not isinstance(arr, list):
                    continue
                fields = S101_METRIC_FIELDS[type_key]
                for rec in arr:
                    if not isinstance(rec, dict):
                        continue
                    p_no = rec.get('门诊号')
                    if not p_no:
                        continue
                    ts = rec.get('采集时间') or rec.get('recordedAt') or rec.get('uploadedAt') or ''
                    date_str = str(ts)[:10]
                    if not date_str or date_str < scan_from_date:
                        continue
                    occurred_at = _s101_ts_to_local(ts)
                    if type_key == '睡眠':
                        # 睡眠是派生量: 一晚一条记录, 总时长 = 深睡 + 浅睡 (to_chinese_record 就是
                        # 按这两个字段落的库)。两个字段都缺才跳过, 缺一个按 0 计。
                        deep, light = rec.get('深睡_分钟'), rec.get('浅睡_分钟')
                        pairs = ([('sleep', (deep or 0) + (light or 0))]
                                 if (deep is not None or light is not None) else [])
                    else:
                        pairs = [(m, rec[f]) for f, m in fields if rec.get(f) is not None]
                    for metric, raw in pairs:
                        try:
                            v = float(raw)
                        except (TypeError, ValueError):
                            continue
                        bucket = daily.setdefault(p_no, {}).setdefault(metric, {}).setdefault(
                            date_str, {'sum': 0.0, 'n': 0, 'device_id': dev_id})
                        bucket['sum'] += v
                        bucket['n'] += 1
                        hit = classify_vital_threshold(metric, v)
                        if hit:
                            alarm_type, severity, detail = hit
                            detail['sample_ts_utc'] = ts
                            breaches.append({
                                'patient_no': p_no, 'device_id': dev_id, 'metric': metric,
                                'value': v, 'ts_utc': ts, 'occurred_at': occurred_at,
                                'alarm_type': alarm_type, 'severity': severity, 'detail': detail,
                            })
        cur.close()
        return breaches, daily
    finally:
        conn.close()


def _zhenmaiyi_report_events(cur, since_date):
    """脉诊仪: 每份新四诊报告落一条 info 级事件。返回待插入行的列表。

    为什么这条链不做阈值预警: 脉诊仪采的是一次性四诊评估(体质 9 得分 + 脉诊 42 参数 + 答题
    记录), 不是连续生理量, "超出正常阈值"在它身上没有现成的临床判定标准 —— 中医体质辨识的
    分级得由临床方给, 我们不能自己编一套塞进验收件。所以这条链只做"新报告到达 + 结论透出":
    工作台上能看到"X 患者出了新的四诊报告, 体质结论 Y, 主脉象 Z", 由医生判读。
    若六院验收要求脉诊仪也出预警, 需要他们提供判定规则再补。

    患者归属走 platform_patient.zhenmaiyi_case_id 映射; 没建映射的报告 patient_no 留空,
    工作台上仍看得见(与 iwown 未绑定设备的报警同样处理)。
    """
    cur.execute("""
        SELECT z.case_id, z.patient_name, z.detect_time, z.conclusion, z.pulse_label, p.patient_no
        FROM zhenmaiyi z
        LEFT JOIN platform_patient p ON p.zhenmaiyi_case_id = z.case_id
        WHERE z.detect_time IS NOT NULL AND DATE(z.detect_time) >= %s
        ORDER BY z.detect_time
    """, (since_date,))
    rows = []
    for case_id, pname, detect_time, conclusion, pulse_label, patient_no in cur.fetchall():
        parts = []
        if conclusion:
            parts.append('体质结论 ' + conclusion)
        if pulse_label:
            parts.append('主脉象 ' + pulse_label)
        detail = {
            'rule': 'report',
            'case_id': case_id,
            'patient_name': pname,
            'conclusion': conclusion,
            'pulse_label': pulse_label,
            'text': '脉诊仪新报告' + (': ' + ' / '.join(parts) if parts else ''),
        }
        rows.append({
            'patient_no': patient_no, 'device_id': None, 'alarm_type': 'pulse_report',
            'severity': 'info', 'occurred_at': detect_time, 'detail': detail,
            'source_chain': 'zhenmaiyi', 'dedup_key': 'zmy:{}'.format(case_id),
        })
    return rows


def platform_vital_alarm_ingest(days=7):
    """M7 主入口: S101 体征阈值判定 + 趋势检测 + 脉诊仪新报告事件, 幂等写 platform_alarm。

    days: 判定窗口, 只对最近 days 天的数据出结论(默认 7)。趋势基线还要再往前多取
          TREND_BASELINE_DAYS 天, 所以实际扫描窗口 = days + TREND_BASELINE_DAYS 天。
    幂等: 每条新链报警都带 dedup_key, 唯一索引兜底, 重跑 inserted=0。
    """
    today = datetime.date.today()
    judge_from = (today - datetime.timedelta(days=days)).strftime('%Y-%m-%d')
    scan_from = (today - datetime.timedelta(days=days + TREND_BASELINE_DAYS)).strftime('%Y-%m-%d')

    try:
        breaches, daily = _s101_scan_for_alarms(scan_from)
    except Exception as e:
        traceback.print_exc()
        return None, 'S101 扫描失败: {}'.format(e)

    # 顺带把日聚合落进派生表 —— M7 本来就算出来了, 不存下来 §4.6(2) 的
    # "按指标阈值检索受试者"就无从查起(原始体征在大 JSON 里, 进不了 WHERE)
    daily_rows = _persist_vital_daily(daily)

    pending = []
    # --- 1) 阈值越限: 一个采样点一条 ---
    for b in breaches:
        if str(b['ts_utc'])[:10] < judge_from:
            continue      # 基线区间的点只用来算 μ/σ, 不出报警
        occurred = b['occurred_at']
        pending.append({
            'patient_no': b['patient_no'], 'device_id': b['device_id'],
            'alarm_type': b['alarm_type'], 'severity': b['severity'],
            'occurred_at': occurred, 'detail': b['detail'], 'source_chain': 's101',
            'dedup_key': 's101:th:{}:{}:{}'.format(b['patient_no'], b['metric'], b['ts_utc']),
        })

    # --- 2) 趋势异常: 一个 (患者, 指标, 日) 一条 ---
    for p_no, metrics in daily.items():
        for metric, by_date in metrics.items():
            # 采样点不够的日子整天剔出序列 —— 既不当被判定日(避免拿清早唯一一次测量冒充"今日均值"),
            # 也不当基线样本(避免一个孤点把基线 μ/σ 带歪)。阈值判定不受这道门槛影响:
            # 单次 185mmHg 该报就得报, 不能因为"当天只测了一次"就压下去。
            need = VITAL_THRESHOLDS[metric].get('min_samples', 3)
            series = {d: agg['sum'] / agg['n'] for d, agg in by_date.items() if agg['n'] >= need}
            for date_str, severity, detail in detect_trend_anomalies(series, metric, judge_from):
                pending.append({
                    'patient_no': p_no, 'device_id': by_date[date_str].get('device_id'),
                    'alarm_type': '{}_trend'.format(VITAL_THRESHOLDS[metric]['type']),
                    'severity': severity,
                    # 趋势是整日汇总判定, 没有"发生时刻"这回事, 统一记在当日 23:59:59,
                    # 保证工作台按 occurred_at 倒序时它排在当天所有采样点之后
                    'occurred_at': datetime.datetime.strptime(
                        date_str + ' 23:59:59', '%Y-%m-%d %H:%M:%S'),
                    'detail': detail, 'source_chain': 's101',
                    'dedup_key': 's101:tr:{}:{}:{}'.format(p_no, metric, date_str),
                })

    conn = get_connection()
    try:
        cur = conn.cursor()
        # --- 3) 脉诊仪新报告 ---
        try:
            pending.extend(_zhenmaiyi_report_events(cur, judge_from))
        except Exception as e:
            # 脉诊仪表可能还没建(独立上传链路), 不能让它拖垮 S101 那两条主线
            print('[M7] 脉诊仪事件跳过:', e)

        counters = {'s101_threshold': 0, 's101_trend': 0, 'zhenmaiyi': 0}
        for row in pending:
            cur.execute("""
                INSERT IGNORE INTO platform_alarm
                  (patient_no, device_id, alarm_type, severity, payload_json,
                   source_chain, dedup_key, status, occurred_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'new', %s)
            """, (row['patient_no'], row['device_id'], row['alarm_type'], row['severity'],
                  json.dumps(row['detail'], ensure_ascii=False),
                  row['source_chain'], row['dedup_key'], row['occurred_at']))
            if cur.rowcount:
                if row['source_chain'] == 'zhenmaiyi':
                    counters['zhenmaiyi'] += 1
                elif row['detail'].get('rule') == 'trend':
                    counters['s101_trend'] += 1
                else:
                    counters['s101_threshold'] += 1
        cur.close()
        return {
            'ok': True,
            'window_days': days,
            'judge_from': judge_from,
            'candidates': len(pending),
            'inserted': sum(counters.values()),
            'detail': counters,
            'vital_daily_rows': daily_rows,
        }, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


# ============ 随访平台 1.2 M8 (待建档门诊号发现) ============
# 只有"步数"和"日综合"这两类不算生命体征 —— 日综合还经常是 dailyRecords:[] 的空跑
# (to_chinese_record 会给它标 is_empty)。判断一个门诊号"有没有真体征"就是看它有没有
# 落在 VITAL_THRESHOLDS 能判定的那五类里, 与 M7 判定范围保持同一个口径。
S101_VITAL_TYPE_KEYS = set(S101_METRIC_FIELDS.keys())


def platform_discover_patients(min_records=0, min_vitals=0, since=None,
                               require_vitals=False, include='pending'):
    """列出在设备数据里出现过、但 platform_patient 里还没有档案的门诊号。

    纳排条件 (设计方案 §4.1(2)"智能批量入组与筛查"的最小可用形态, 复杂条件组合是后面的事):
      min_records    总记录数下限
      min_vitals     **体征**记录数下限 —— 通常这个才是你要的那个。总记录数被步数主导:
                     生产实测 0010090645 有 117 条记录, 其中 96 条是步数, 真正能被 M7
                     判定的血压只有 8 条。按总数筛会把"传了一堆步数但只测过 1 次血压"的
                     人当成优质候选。
      since          'YYYY-MM-DD', 最近上传时间不早于此日
      require_vitals True = 只要有真实体征(心率/血氧/血压/体温/睡眠)的, 滤掉只有步数和
                     空日综合的号 —— 生产实测 39 个候选里只有 6 个有真体征
      include        pending=未筛查(默认) / excluded=已排除 / all=全部

    为什么需要这个端点: 患者标识是 5.06-v9 定的"写在大 JSON 每条记录的 '门诊号' 字段里",
    小程序端患者自己输门诊号就能上传数据 —— 也就是说**数据先到, 档案后建**。
    /api/platform/patients 只读 platform_patient, 所以在有人手工建档之前, 平台上一个
    患者都看不到, 而设备数据里其实已经躺了几十个门诊号 (2026-08-02 生产实测: 设备数据
    39 个门诊号, platform_patient 0 行 —— 这就是平台一直看着是空的原因)。这个端点把
    这段落差显式暴露出来, 让医护一键建档, 而不是要求他们凭空知道有哪些号。

    返回每个候选 {patient_no, s101_count, s101_latest, s101_types, has_vitals, vital_count,
    excluded, exclusion, iwown_devices, zhenmaiyi_cases}, 按最近上传时间倒序
    (最近有数据的排前面, 建档优先级天然就高)。
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT patient_no FROM platform_patient')
        known = {r[0] for r in cur.fetchall()}
        # 已排除名单: 医护看过并决定不入组的号。不记住这个决定的话, 每次打开候选列表
        # 那几十个号又全冒出来, 筛查就永远做不完。
        excluded = {}
        try:
            cur.execute('SELECT patient_no, reason, operator, created_at FROM platform_screening')
            for p_no, reason, operator, created in cur.fetchall():
                excluded[p_no] = {
                    'reason': reason, 'operator': operator,
                    'created_at': created.strftime('%Y-%m-%d %H:%M:%S') if created else None,
                }
        except Exception as e:
            print('[M9] platform_screening 读取跳过:', e)

        found = {}

        def entry(p_no):
            return found.setdefault(p_no, {
                'patient_no': p_no, 's101_count': 0, 's101_latest': None,
                's101_types': {}, 'iwown_devices': [], 'zhenmaiyi_cases': [],
            })

        # --- S101/R04: 门诊号藏在每设备一行的大 JSON 里, 只能整扫 ---
        cur.execute('SELECT data FROM wearable_device_data')
        for (data_raw,) in cur.fetchall():
            try:
                big_json = json.loads(data_raw) if data_raw else {}
            except json.JSONDecodeError:
                continue
            if not isinstance(big_json, dict):
                continue
            for type_key, arr in big_json.items():
                if not isinstance(arr, list):
                    continue
                for rec in arr:
                    if not isinstance(rec, dict):
                        continue
                    p_no = rec.get('门诊号')
                    if not p_no or p_no in known:
                        continue
                    e = entry(p_no)
                    e['s101_count'] += 1
                    e['s101_types'][type_key] = e['s101_types'].get(type_key, 0) + 1
                    ts = rec.get('采集时间') or rec.get('recordedAt') or rec.get('uploadedAt')
                    if ts and (e['s101_latest'] is None or ts > e['s101_latest']):
                        e['s101_latest'] = ts

        # --- iwown: 名册表里直接有 patient_no 列 ---
        try:
            cur.execute('SELECT device_id, patient_no FROM iwown_device WHERE patient_no IS NOT NULL')
            for device_id, p_no in cur.fetchall():
                if p_no and p_no not in known:
                    entry(p_no)['iwown_devices'].append(device_id)
        except Exception as e:
            print('[M8] iwown_device 跳过:', e)

        cur.close()

        # --- 纳排过滤 ---
        rows = []
        for e in found.values():
            e['has_vitals'] = any(k in S101_VITAL_TYPE_KEYS for k in e['s101_types'])
            e['vital_count'] = sum(v for k, v in e['s101_types'].items() if k in S101_VITAL_TYPE_KEYS)
            is_excluded = e['patient_no'] in excluded
            e['excluded'] = is_excluded
            e['exclusion'] = excluded.get(e['patient_no'])
            if include == 'pending' and is_excluded:
                continue
            if include == 'excluded' and not is_excluded:
                continue
            if e['s101_count'] < min_records:
                continue
            if e['vital_count'] < min_vitals:
                continue
            if since and (e['s101_latest'] or '')[:10] < since:
                continue
            if require_vitals and not e['has_vitals']:
                continue
            rows.append(e)

        rows.sort(key=lambda x: (x['s101_latest'] or ''), reverse=True)
        return {'ok': True, 'count': len(rows), 'known_count': len(known),
                'excluded_count': len(excluded), 'total_found': len(found),
                'filters': {'min_records': min_records, 'min_vitals': min_vitals,
                            'since': since, 'require_vitals': require_vitals, 'include': include},
                'candidates': rows}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def ensure_platform_screening_table():
    """M9: 筛查排除名单 (idempotent)。

    只存"排除"这一个决定, 不存"已入组" —— 已入组就是 platform_patient 里有这一行,
    再存一份等于两个事实来源, 早晚不一致。三态是算出来的:
      在 platform_patient 里          -> 已入组
      在 platform_screening 里        -> 筛查不通过
      两边都没有但设备数据里有         -> 未筛查
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_screening (
                patient_no VARCHAR(64) PRIMARY KEY COMMENT '门诊号',
                reason VARCHAR(255) DEFAULT NULL COMMENT '排除原因',
                operator VARCHAR(64) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M9 筛查排除名单'
        """)
        print('[启动] platform_screening 表已就绪')
        cur.close()
    except Exception as e:
        print('[启动] ensure_platform_screening_table 失败:', e)
    finally:
        conn.close()


def platform_batch_enroll(body):
    """M9: 批量建档 {patients:[{patient_no, name?, gender?, age?, group_tag?, note?}, ...]}。

    逐条复用 upsert_platform_patient, 不另写一套 UPSERT —— 字段校验(gender 枚举、age 整数)
    只在那一处维护。单条失败不影响其余条, 逐条回报结果, 因为批量导入最常见的场景就是
    Excel 里混了一两行脏数据, 不该让整批回滚。
    """
    items = body.get('patients')
    if not isinstance(items, list) or not items:
        return None, 'patients 必须是非空数组'
    if len(items) > 500:
        return None, '单次最多 500 条, 请分批'
    results, ok_n, fail_n = [], 0, 0
    for it in items:
        if not isinstance(it, dict):
            results.append({'patient_no': None, 'ok': False, 'error': '每项必须是对象'})
            fail_n += 1
            continue
        r, err = upsert_platform_patient(it)
        if err:
            results.append({'patient_no': it.get('patient_no'), 'ok': False, 'error': err})
            fail_n += 1
        else:
            results.append({'patient_no': r['patient_no'], 'ok': True, 'action': r['action']})
            ok_n += 1
    return {'ok': True, 'succeeded': ok_n, 'failed': fail_n, 'results': results}, None


def platform_screening_mark(body):
    """M9: 标记/撤销筛查排除 {patient_no, action:'exclude'|'restore', reason?, operator?}。"""
    patient_no = str(body.get('patient_no') or '').strip()
    if not patient_no:
        return None, 'patient_no 必填'
    action = body.get('action') or 'exclude'
    if action not in ('exclude', 'restore'):
        return None, "action 必须是 'exclude' 或 'restore'"
    conn = get_connection()
    try:
        cur = conn.cursor()
        if action == 'restore':
            cur.execute('DELETE FROM platform_screening WHERE patient_no = %s', (patient_no,))
        else:
            cur.execute("""
                INSERT INTO platform_screening (patient_no, reason, operator)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE reason = VALUES(reason), operator = VALUES(operator)
            """, (patient_no, body.get('reason') or None, body.get('operator') or None))
        cur.close()
        return {'patient_no': patient_no, 'action': action}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


# ============ 随访平台 1.3 M10 (量表引擎: 存储 / 填报 / 评分) ============
# 对应设计方案 §2.2(1) 内置标准化量表底座, 以及北大六院标书 ★1.6.2 里点名的:
#   CRF 设置 / 题型配置 / 评分规则配置 / 字段映射 / 阶段管理 / 预览管理
#   + 医护填报 / 患者自评 / 错误修订
#
# 本轮做的是**引擎**, 不是**内容**。★1.6.2 要的 200+ 个精神科量表是内容, 题目与评分规则
# 得临床方提供(且相当一部分量表有版权与授权要求, 不能自己编)。引擎先立住, 量表用
# POST /api/platform/scale 一份一份导入即可 —— 导入一份就是一次 INSERT。
#
# 明确没做: §2.2(2) AI 量表生成、§2.2(3) 从文档/图片 OCR 结构化入库。
#
# 为什么 definition 用一整块 JSON 而不拆成 item/option 两张表:
#   量表是"整体发布 + 整体版本化"的单位, 题目不会被单独查询或跨量表检索; 拆表要 3~4 张
#   还得维护顺序字段, 收益极小。代价是没法按题目做跨量表检索(比如"哪些量表问了自杀意念"),
#   真需要时再加一张倒排表, 不必现在就付这个复杂度。

SCALE_ITEM_TYPES = ('single', 'multi', 'number', 'text', 'scale')


def _scale_opt_values(item):
    """取一道题所有选项的分值列表(用于反向计分)。"""
    return [o.get('value') for o in (item.get('options') or []) if isinstance(o.get('value'), (int, float))]


def validate_scale_definition(d):
    """量表定义结构校验。返回 errors 列表(空 = 合法)。

    导入时先过这一关, 免得一份结构就不对的量表进了库, 等到患者填到一半才炸。
    """
    errs = []
    if not isinstance(d, dict):
        return ['definition 必须是对象']
    items = d.get('items')
    if not isinstance(items, list) or not items:
        return ['definition.items 必须是非空数组']

    seen = set()
    for i, it in enumerate(items):
        where = 'items[{}]'.format(i)
        if not isinstance(it, dict):
            errs.append(where + ' 必须是对象'); continue
        iid = it.get('id')
        if not iid:
            errs.append(where + ' 缺 id')
        elif iid in seen:
            errs.append(where + ' id 重复: ' + str(iid))
        else:
            seen.add(iid)
        if not it.get('text'):
            errs.append(where + ' 缺题干 text')
        t = it.get('type')
        if t not in SCALE_ITEM_TYPES:
            errs.append('{} type 必须是 {} 之一, 得到 {!r}'.format(where, '/'.join(SCALE_ITEM_TYPES), t))
        if t in ('single', 'multi'):
            opts = it.get('options')
            if not isinstance(opts, list) or not opts:
                errs.append(where + ' 选择题必须有 options')
            else:
                for j, o in enumerate(opts):
                    if not isinstance(o, dict) or 'label' not in o or 'value' not in o:
                        errs.append('{}.options[{}] 必须含 label 和 value'.format(where, j))
            if it.get('reverse') and len(_scale_opt_values(it)) < 2:
                errs.append(where + ' 标了 reverse 但选项分值不足 2 个, 无法反向计分')
        if t in ('number', 'scale'):
            lo, hi = it.get('min'), it.get('max')
            if lo is not None and hi is not None and lo > hi:
                errs.append(where + ' min 大于 max')

    sc = d.get('scoring') or {}
    for k, sub in enumerate(sc.get('subscales') or []):
        if not isinstance(sub, dict) or not sub.get('name') or not isinstance(sub.get('items'), list):
            errs.append('scoring.subscales[{}] 必须含 name 和 items 数组'.format(k)); continue
        for iid in sub['items']:
            if iid not in seen:
                errs.append('scoring.subscales[{}] 引用了不存在的题目 {!r}'.format(k, iid))
    levels = sc.get('levels') or []
    for k, lv in enumerate(levels):
        if not isinstance(lv, dict) or 'min' not in lv or 'max' not in lv or not lv.get('label'):
            errs.append('scoring.levels[{}] 必须含 min/max/label'.format(k))
        elif lv['min'] > lv['max']:
            errs.append('scoring.levels[{}] min 大于 max'.format(k))
    # 分级区间重叠会导致同一个总分落进两档, 判定结果取决于数组顺序 —— 这是隐蔽的错源, 直接拦
    ok_levels = [lv for lv in levels if isinstance(lv, dict) and isinstance(lv.get('min'), (int, float))
                 and isinstance(lv.get('max'), (int, float))]
    for a in range(len(ok_levels)):
        for b in range(a + 1, len(ok_levels)):
            x, y = ok_levels[a], ok_levels[b]
            if x['min'] <= y['max'] and y['min'] <= x['max']:
                errs.append('scoring.levels 区间重叠: [{},{}] 与 [{},{}]'.format(
                    x['min'], x['max'], y['min'], y['max']))

    for k, r in enumerate(d.get('consistency') or []):
        if not isinstance(r, dict) or r.get('type') not in ('require_if', 'exclusive'):
            errs.append('consistency[{}] type 必须是 require_if 或 exclusive'.format(k))
    return errs


def score_scale(definition, answers):
    """按量表定义对一份作答评分 + 校验。返回 (result, errors)。

    评分口径:
      single  取所选选项的 value
      multi   取所选各选项 value 之和
      number  取数值本身
      scale   取数值本身(视觉模拟/滑块)
      text    不计分

      reverse=true 的题按 (选项最大分 + 选项最小分) - 原始分 反向 —— 这是量表学里标准的
      反向计分公式, 不是简单的 max-x。很多自评量表(如 Zung SDS)有近半题目是反向题,
      算错方向会让抑郁分变成健康分, 所以单列出来并有专门的单元测试。

    校验(方案 §2.2(3) 的"必填校验/取值范围校验/逻辑一致性校验"):
      - required 缺答
      - number/scale 越 min/max
      - 选择题答案不在选项里
      - consistency: require_if(条件必填) / exclusive(互斥)
    未做: 从文档/图片 OCR 结构化入库(那是 AI 侧的事)。
    """
    errors = []
    items = definition.get('items') or []
    by_id = {it.get('id'): it for it in items if isinstance(it, dict)}
    item_scores = {}
    answered = 0

    for it in items:
        iid = it.get('id')
        t = it.get('type')
        raw = answers.get(iid)
        missing = raw is None or raw == '' or (isinstance(raw, list) and not raw)
        if missing:
            if it.get('required'):
                errors.append({'item': iid, 'error': '必填项未作答'})
            continue
        answered += 1

        if t == 'single':
            vals = {o.get('value') for o in (it.get('options') or [])}
            if raw not in vals:
                errors.append({'item': iid, 'error': '答案不在选项内: {!r}'.format(raw)})
                continue
            score = raw
        elif t == 'multi':
            if not isinstance(raw, list):
                errors.append({'item': iid, 'error': '多选题答案必须是数组'}); continue
            vals = {o.get('value') for o in (it.get('options') or [])}
            bad = [x for x in raw if x not in vals]
            if bad:
                errors.append({'item': iid, 'error': '答案不在选项内: {!r}'.format(bad)}); continue
            score = sum(x for x in raw if isinstance(x, (int, float)))
        elif t in ('number', 'scale'):
            try:
                score = float(raw)
            except (TypeError, ValueError):
                errors.append({'item': iid, 'error': '必须是数字'}); continue
            lo, hi = it.get('min'), it.get('max')
            if lo is not None and score < lo:
                errors.append({'item': iid, 'error': '低于下限 {}'.format(lo)}); continue
            if hi is not None and score > hi:
                errors.append({'item': iid, 'error': '高于上限 {}'.format(hi)}); continue
        else:                      # text 不计分
            continue

        if it.get('reverse'):
            ov = _scale_opt_values(it)
            if ov:
                score = (max(ov) + min(ov)) - score
            else:
                lo, hi = it.get('min'), it.get('max')
                if lo is not None and hi is not None:
                    score = (hi + lo) - score
        item_scores[iid] = score

    # --- 逻辑一致性 ---
    for rule in (definition.get('consistency') or []):
        if rule.get('type') == 'require_if':
            w = rule.get('when') or {}
            trig = answers.get(w.get('item'))
            hit = trig in (w.get('in') or []) if isinstance(w.get('in'), list) else trig == w.get('equals')
            if hit:
                for need in (rule.get('then_required') or []):
                    v = answers.get(need)
                    if v is None or v == '' or (isinstance(v, list) and not v):
                        errors.append({'item': need, 'error': '当 {} 选了特定项时此题必填'.format(w.get('item'))})
        elif rule.get('type') == 'exclusive':
            picked = [i for i in (rule.get('items') or [])
                      if answers.get(i) not in (None, '', []) ]
            if len(picked) > 1:
                errors.append({'item': picked[0], 'error': '互斥题只能选其一, 现有 {}'.format('、'.join(picked))})

    sc = definition.get('scoring') or {}
    total_cfg = sc.get('total') or {'method': 'sum', 'items': 'all'}
    pick = total_cfg.get('items')
    ids = list(item_scores) if pick in (None, 'all') else [i for i in pick if i in item_scores]
    vals = [item_scores[i] for i in ids]
    if total_cfg.get('method') == 'mean':
        total = round(sum(vals) / len(vals), 2) if vals else None
    else:
        total = sum(vals) if vals else 0

    subscores = {}
    for sub in (sc.get('subscales') or []):
        sv = [item_scores[i] for i in sub.get('items', []) if i in item_scores]
        subscores[sub['name']] = round(sum(sv) / len(sv), 2) if sub.get('method') == 'mean' else sum(sv)

    level = None
    if total is not None:
        for lv in (sc.get('levels') or []):
            try:
                if lv['min'] <= total <= lv['max']:
                    level = {'label': lv.get('label'), 'advice': lv.get('advice'),
                             'min': lv['min'], 'max': lv['max']}
                    break
            except (KeyError, TypeError):
                continue

    return {
        'item_scores': item_scores, 'total': total, 'subscores': subscores,
        'level': level, 'answered': answered, 'total_items': len(items),
        'unanswered': [it.get('id') for it in items if it.get('id') not in item_scores
                       and it.get('type') != 'text'],
    }, errors


# ---------------------------------------------------------------------------
# M11: 把一份量表文档(PDF/纯文本)结构化成量表草稿 —— 设计方案 §2.2(3)
#
# 定位必须先说清楚: 这是**草稿生成器, 不是自动导入器**。产出一律先进人工审核界面, 由临床
# 方逐题确认后才入库。理由不是技术保守 —— 量表的题干措辞、选项分值、划界值直接决定评估
# 结论, 解析器把"0-4分 无焦虑"错认成"0-4题"这种事一旦静默入库, 后面每一份填报都是错的,
# 而且错得看不出来。所以每个识别结果都带 confidence 和 source_line, 让人能对着原文核。
#
# 分工: PDF 取文字交给 pdf-inspector(纯 Rust, 本地跑, 不联网不要 key, 对中文 PDF 实测
# 3.2 万汉字零乱码), 这里只负责"文字 -> 结构"。扫描件/图片它读不了(它只能告诉你这是扫描
# 件), 那部分要真 OCR 引擎, 尚未接入。
# ---------------------------------------------------------------------------

# 题号: "1." "1、" "1)" "1．" "(1)" "第1题"
_RE_ITEM = re.compile(r'^\s*(?:第)?\s*[(（]?(\d{1,3})[)）]?\s*[.、．)）]\s*(.+?)\s*$')
# 选项标记: "0=" "0．" "(0)" "0分:" —— 只认标记本身, 标签由 _parse_option_line 按标记
# 位置切出来 (真实标签里常带数字, 用字符集匹配会截断)
_RE_OPT_MARK = re.compile(r'[(（]?(\d{1,2})[)）]?\s*(?:分)?\s*[=＝:：.．、]')
# 无分值的勾选项: "□ 完全不会" "○完全不会" "( ) 完全不会" "[ ]完全不会"
_RE_OPT_BOX = re.compile(r'(?:□|☐|○|●|◯|\[\s*\]|[(（]\s*[)）])\s*([^\s□☐○●◯\[\](（)）]{1,20})')
# 分级有两种书写顺序, 都得认, 否则标签会解析成"分）"这种碎片:
#   区间在前: "0-4 分 无焦虑"   "5~9分：轻度焦虑"   "总分 10-14 为中度"
_RE_LEVEL = re.compile(
    r'(\d{1,3})\s*[-~—–至]\s*(\d{1,3})\s*(?:分)?\s*(?:分?为|:|：|,|，|\s)\s*'
    r'([^\s0-9;；,，。.（()）]{2,16})')
# 单位词/量词不是分级名。不拦的话 "正常（0-4 分）" 会被正向模式切出 "分）" 这个碎片,
# 而且因为它"匹配上了", 标签在前的反向模式根本轮不到试。
_LEVEL_LABEL_STOP = {'分', '分数', '总分', '得分', '级', '档', '分档', '区间', '以上', '以下'}


def _looks_like_level_line(s):
    """两种书写顺序一起判 —— 只查正向模式会漏掉 "1、正常（1-4 分）" 这种标签在前的写法,
    那正是最容易被误当成题目的形态。"""
    return bool(_RE_LEVEL.search(s) or _RE_LEVEL_REV.search(s))
#   标签在前: "正常（1-4 分）"  "轻度 5-9 分"  "重度抑郁(20~27)"
_RE_LEVEL_REV = re.compile(
    r'([^\s0-9;；,，。.、：:（()）]{2,16})\s*[（(]?\s*(\d{1,3})\s*[-~—–至]\s*(\d{1,3})\s*(?:分)?\s*[）)]?')


def _clean_line(s):
    return re.sub(r'\s+', ' ', (s or '').replace('　', ' ')).strip()


def _parse_option_line(s):
    """从一行里解析出 [{label, value}] 选项序列。

    按**标记位置切分**, 而不是用字符集去匹配标签 —— 真实量表的选项标签里常带数字
    ("每周少于1次" "每周1-2次" "3次或以上")。字符集排掉数字会把标签截成 "每周少于";
    不排数字又会把下一个选项的值吃进来。按 "数字+分隔符" 的位置切, 两个问题都没有。

    切出来不像标签(空的、或长得离谱)就整行放弃 —— 给不出选项好过给一组错的。
    """
    marks = list(_RE_OPT_MARK.finditer(s))
    if len(marks) < 2:
        return []
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(s)
        label = s[m.end():end].strip(' \t　，,、;；。．.')
        if not label or len(label) > 24:
            return []
        out.append({'label': label, 'value': int(m.group(1))})
    return out


def parse_scale_text(text, code=None, name=None):
    """把一份量表文档的文字解析成量表定义草稿。返回 (draft, report)。

    report 里带每一步的依据和置信度, 前端据此提示人重点核对哪几处。
    识别不出的东西一律留空, 绝不猜 —— 让人补, 好过给一个看着像对的错值。
    """
    lines = [_clean_line(l) for l in (text or '').replace('\r', '').split('\n')]
    lines = [l for l in lines if l]
    notes = []
    option_line_idx = set()    # 被当作选项行消费掉的行, 不参与分级解析
    extra_level_text = []      # 从题干行尾部切下来的分级片段

    # ---- 标题 ----
    title = name
    if not title:
        for l in lines[:12]:
            if _RE_ITEM.match(l):
                break
            t = re.sub(r'^#+\s*', '', l)
            # 排掉选项行: "0=无 1=有" 这种既短又没冒号, 不设防就会被当成量表名
            if _parse_option_line(t) or _RE_OPT_BOX.findall(t) or '=' in t or '＝' in t:
                continue
            if 3 <= len(t) <= 40 and not re.search(r'[:：]', t):
                title = t
                notes.append({'step': 'title', 'confidence': 'medium',
                              'detail': '取首个短行作为量表名', 'source_line': l})
                break

    # ---- 指导语 ----
    instruction = ''
    for l in lines[:20]:
        if _RE_ITEM.match(l):
            break
        if re.search(r'(说明|指导语|指导说明|请根据|在过去|下列|以下问题)', l) and len(l) >= 8:
            # PDF 常把"说明：…"和"选项：0=…"挤成一行, 只取选项标记之前那段当指导语
            cut = _RE_OPT_MARK.search(l)
            seg = l[:cut.start()] if cut else l
            seg = re.sub(r'(选项|评分)\s*[:：]\s*$', '', seg).strip()
            instruction = re.sub(r'^(说明|指导语|指导说明)\s*[:：]?\s*', '', seg)
            notes.append({'step': 'instruction', 'confidence': 'medium',
                          'detail': '命中指导语关键词', 'source_line': l})
            break

    # ---- 全局共享选项集 ----
    shared_opts = []
    for idx, l in enumerate(lines[:25]):
        if _RE_ITEM.match(l):
            break
        found = _parse_option_line(l)
        if len(found) >= 2:
            shared_opts = found
            option_line_idx.add(idx)
            notes.append({'step': 'shared_options', 'confidence': 'high',
                          'detail': '解析到 {} 个共享选项: {}'.format(
                              len(found), ' / '.join(o['label'] for o in found)),
                          'source_line': l})
            break

    # ---- 题目 ----
    items = []
    item_line_idx = set()
    last_num = 0
    for idx, l in enumerate(lines):
        m = _RE_ITEM.match(l)
        if not m:
            continue
        num, body = int(m.group(1)), m.group(2)
        # 题号必须递增且不跳太远, 否则多半撞上了 "87." 这种页脚数字, 或是评分标准里
        # "1、正常（1-4分） 2、轻度（5-9分）" 这类自带序号的分级说明
        if not (last_num < num <= last_num + 3):
            if _looks_like_level_line(l):
                notes.append({'step': 'item_skip', 'confidence': 'high',
                              'detail': '该行带序号但同时像分级说明(题号也不连续), 未当作题目',
                              'source_line': l})
            continue

        # PDF 抽文字常把相邻行挤成一行, 典型是最后一题和评分标准粘在一起:
        #   "9. 疼痛不适 评分标准：0-5 分 睡眠质量好；…"
        # 整行丢掉会平白少一道题, 所以在分级说明起点处切开, 尾巴留给分级解析。
        if _looks_like_level_line(body):
            mk = _RE_LEVEL.search(body) or _RE_LEVEL_REV.search(body)
            head = body[:mk.start()].strip(' ，,、；;：:') if mk else ''
            head = re.sub(r'(评分标准|评分|分级|判定)\s*$', '', head).strip(' ，,、；;：:')
            if len(head) >= 2:
                extra_level_text.append(body[mk.start():])
                notes.append({'step': 'item_split', 'confidence': 'medium',
                              'detail': '第 {} 题与分级说明被挤在同一行, 已切开; 请核对题干是否完整'.format(num),
                              'source_line': l})
                body = head
            else:
                notes.append({'step': 'item_skip', 'confidence': 'high',
                              'detail': '该行同时像分级说明, 未当作题目', 'source_line': l})
                continue

        # 题干自带选项 ("1. 头痛程度 0=无 1=轻度")
        own = _parse_option_line(body)
        if len(own) >= 2:
            cut = _RE_OPT_MARK.search(body)
            body = body[:cut.start()].strip(' ，,、:：') or body
            opts = own
        else:
            opts = []
            for off, nxt in enumerate(lines[idx + 1: idx + 3], start=idx + 1):
                if _RE_ITEM.match(nxt):
                    break
                f = _parse_option_line(nxt)
                if len(f) >= 2:
                    opts = f
                    option_line_idx.add(off)
                    break
                b = _RE_OPT_BOX.findall(nxt)
                if len(b) >= 2:
                    # 勾选框没有分值, 按出现顺序给 0,1,2... 并标出来让人确认
                    opts = [{'label': lab, 'value': i} for i, lab in enumerate(b)]
                    option_line_idx.add(off)
                    notes.append({'step': 'option_value_guess', 'confidence': 'low',
                                  'detail': '第 {} 题的选项没有分值, 按顺序暂定 0..{}, 需人工确认'.format(
                                      num, len(b) - 1),
                                  'source_line': nxt})
                    break
        if not opts:
            opts = list(shared_opts)
        last_num = num
        item_line_idx.add(idx)
        items.append({
            'id': 'q{}'.format(num), 'text': body, 'type': 'single' if opts else 'text',
            'required': True, 'options': opts or None,
        })

    # ---- 分级 (划界值) ----
    levels = []

    def _add_level(lo, hi, label):
        lo, hi = int(lo), int(hi)
        label = label.strip(' 、，,：:（()）').lstrip('分')
        if (lo > hi or not label or label in _LEVEL_LABEL_STOP
                or any(x['min'] == lo and x['max'] == hi for x in levels)):
            return False
        levels.append({'min': lo, 'max': hi, 'label': label})
        return True

    # 排除**真正被接受为题目**的行, 以及被当作选项消费掉的行。后者尤其重要:
    # "2=每周1-2次" 这种选项标签里的 1-2 会被分级正则当成一个区间, 凭空造出一档假分级。
    for idx, l in enumerate(lines):
        if idx in item_line_idx or idx in option_line_idx:
            continue
        got = False
        for lo, hi, label in _RE_LEVEL.findall(l):
            got = _add_level(lo, hi, label) or got
        if not got:
            for label, lo, hi in _RE_LEVEL_REV.findall(l):
                _add_level(lo, hi, label)
    for frag in extra_level_text:
        got = False
        for lo, hi, label in _RE_LEVEL.findall(frag):
            got = _add_level(lo, hi, label) or got
        if not got:
            for label, lo, hi in _RE_LEVEL_REV.findall(frag):
                _add_level(lo, hi, label)

    levels.sort(key=lambda x: x['min'])
    if levels:
        notes.append({'step': 'levels', 'confidence': 'medium',
                      'detail': '解析到 {} 档分级, 区间与名称务必对照原文核'.format(len(levels))})

    max_total = sum(max([o['value'] for o in (it['options'] or [{'value': 0}])])
                    for it in items) if items else 0
    if levels and items:
        top = max(x['max'] for x in levels)
        if not max_total:
            notes.append({'step': 'levels_check', 'confidence': 'low',
                          'detail': '解析到 {} 档分级, 但没有一道题识别出带分值的选项 —— '
                                    '无从核对分级区间是否合理, 请人工确认'.format(len(levels))})
        elif top > max_total:
            notes.append({'step': 'levels_check', 'confidence': 'low',
                          'detail': '分级上限 {} 超过按选项算出的理论最高分 {} —— '
                                    '很可能把别的数字当成了分级'.format(top, max_total)})

    m_code = re.search(r'[A-Z][A-Z0-9\-]{2,15}', title or '')
    draft = {
        'code': code or (m_code.group(0) if m_code else ''),
        'name': title or '',
        'category': '',
        'rater': 'both',
        'source': 'import',
        'definition': {
            'instruction': instruction,
            'items': [{k: v for k, v in it.items() if v is not None} for it in items],
            'scoring': {'total': {'method': 'sum', 'items': 'all'},
                        'subscales': [], 'levels': levels},
            'consistency': [],
        },
    }
    report = {
        'item_count': len(items),
        'items_without_options': sum(1 for it in items if not it['options']),
        'level_count': len(levels),
        'max_total_by_options': max_total,
        'notes': notes,
        'validation': validate_scale_definition(draft['definition']),
        'needs_review': True,
    }
    return draft, report



def extract_pdf_text(pdf_bytes):
    """PDF -> 文字。返回 (text, meta, error)。

    走 M25 的 ocr_read_source: 有文字层的页用 pdf-inspector 直接取字(快且精确),
    没有文字层的页渲成图交给本地 OCR 引擎。两个库分工不同 ——
    pdf-inspector 是**给 OCR 做分流的**(它算出 pages_needing_ocr), 本身不认图。

    这里原先有个坑: 混合件(前几页电子版 + 后面附一张化验单照片)只返回文字层那部分,
    读不到的页没有任何提示, 看起来像整份都读完了 —— 一份量表少了后半截, 前端和使用者
    都不会察觉。现在 meta 里如实分列哪几页走了文字层、哪几页走了 OCR、哪几页没读成,
    有没读成的页时 meta['warning'] 会写清楚。
    """
    lines, meta, err = ocr_read_source(pdf_bytes, 'pdf')
    meta = meta or {}
    if err:
        return None, meta, err
    text = '\n'.join(l['text'] for l in lines)
    meta['chars'] = len(text)
    meta['line_count'] = len(lines)
    if meta.get('pages_unread'):
        meta['warning'] = ('第 {} 页没能读出来, 下面的内容**不是全文**。原因: {}'.format(
            '、'.join(str(p) for p in meta['pages_unread']),
            '; '.join(sorted(set((meta.get('unread_reasons') or {}).values()))) or '未知'))
    if not text.strip():
        return None, meta, 'PDF 里没有提取到文字 (分类: {})'.format(meta.get('pdf_type'))
    return text, meta, None


# ---------------------------------------------------------------------------
# M12: AI 量表生成 —— 设计方案 §2.2(2)
#
# 两条铁律, 都体现在代码里而不是文档里:
#
# 1) 生成出来的量表**永远不带划界值分级**。分级(0-4 无 / 5-9 轻度 / ...)是把总分翻译成
#    临床结论的那一步, 它来自特定人群上的信效度研究, 不是能从题目推导出来的东西。
#    让模型编一组阈值出来, 产出的每一份评估报告都会给出错误的临床结论, 而且看起来完全正常。
#    所以 _sanitize_generated 会把模型吐出的 levels 一律删掉并记账 —— 宁可让人工去补,
#    也不给一个像模像样的假阈值。生成的量表只有原始总分, 没有"重度抑郁"这种判定。
#
# 2) 默认后端是模板, 不联网、不需要 key。医院内网服务器把内容发给外部 LLM 是数据治理
#    决策, 不该由这段代码替人做主。要启用 Claude 后端必须显式配置
#    SCALE_LLM_PROVIDER=claude + ANTHROPIC_API_KEY, 且服务器上要装 anthropic SDK。
#    注意即便启用, 送出去的也只是**评估规格**(评估目标/人群/维度), 绝不含任何患者数据。

SCALE_RESPONSE_SETS = {
    'freq4': ('四级频率', [{'label': '完全不会', 'value': 0}, {'label': '好几天', 'value': 1},
                          {'label': '一半以上时间', 'value': 2}, {'label': '几乎天天', 'value': 3}]),
    'likert5': ('五级李克特', [{'label': '完全不符合', 'value': 0}, {'label': '比较不符合', 'value': 1},
                              {'label': '不确定', 'value': 2}, {'label': '比较符合', 'value': 3},
                              {'label': '完全符合', 'value': 4}]),
    'severity4': ('四级严重度', [{'label': '无', 'value': 0}, {'label': '轻度', 'value': 1},
                                {'label': '中度', 'value': 2}, {'label': '重度', 'value': 3}]),
    'yesno': ('二分是否', [{'label': '否', 'value': 0}, {'label': '是', 'value': 1}]),
}

# 生成结果的结构约束。JSON Schema 里 additionalProperties 必须显式 false, 且不放
# minLength/maximum 这类数值约束 —— 结构化输出不支持它们, 写了会被静默丢掉。
# 注意这份 schema **没有 levels 字段**: 不给模型留下产出划界值的位置, 比事后删更干净。
SCALE_GEN_SCHEMA = {
    'type': 'object',
    'properties': {
        'name': {'type': 'string'},
        'instruction': {'type': 'string'},
        'items': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'id': {'type': 'string'},
                    'text': {'type': 'string'},
                    'dimension': {'type': 'string'},
                    'reverse': {'type': 'boolean'},
                },
                'required': ['id', 'text', 'dimension', 'reverse'],
                'additionalProperties': False,
            },
        },
    },
    'required': ['name', 'instruction', 'items'],
    'additionalProperties': False,
}

SCALE_GEN_DISCLAIMER = (
    'AI 生成草稿, 未经信效度验证。只有原始总分, 没有划界值分级 —— 分级阈值必须来自'
    '目标人群上的实证研究, 不能由模型推导。临床使用前须由专业人员逐题审核并补充常模。'
)


def _sanitize_generated(draft, notes):
    """把生成结果拉回安全边界。返回处理后的 draft。

    删 levels 是这里最重要的一件事, 理由见本节顶部注释。即使 schema 里没给位置,
    也仍然做一次兜底删除 —— 换后端、换模型、有人手改 schema, 任何一种情况下这道闸门都还在。
    """
    d = draft.setdefault('definition', {})
    sc = d.setdefault('scoring', {})
    if sc.get('levels'):
        notes.append({'step': 'levels_stripped', 'confidence': 'high',
                      'detail': '已删除模型产出的 {} 档划界值 —— 分级阈值来自实证研究, '
                                '不能由模型推导, 必须由临床方补充'.format(len(sc['levels']))})
    sc['levels'] = []
    sc.setdefault('total', {'method': 'sum', 'items': 'all'})
    sc.setdefault('subscales', [])
    d.setdefault('consistency', [])
    d['disclaimer'] = SCALE_GEN_DISCLAIMER
    draft['source'] = 'ai'
    draft.setdefault('rater', 'both')
    return draft


def _build_scale_from_items(spec, name, instruction, raw_items, notes):
    """把 (题干, 维度, 是否反向) 三元组组装成量表定义, 挂上选项与分量表。

    选项由 spec.response_scale 统一决定而不是让模型自己发挥 —— 一份量表内选项不一致会让
    总分失去意义, 而这恰好是模型很容易出错的地方。
    """
    key = spec.get('response_scale') or 'freq4'
    label, options = SCALE_RESPONSE_SETS.get(key, SCALE_RESPONSE_SETS['freq4'])
    items, dims = [], {}
    for i, it in enumerate(raw_items, 1):
        iid = 'q{}'.format(i)
        dim = (it.get('dimension') or '').strip() or '总体'
        items.append({
            'id': iid, 'text': (it.get('text') or '').strip(), 'type': 'single',
            'required': True, 'reverse': bool(it.get('reverse')),
            'dimension': dim, 'options': list(options),
        })
        dims.setdefault(dim, []).append(iid)
    subscales = [{'name': k, 'items': v} for k, v in dims.items()] if len(dims) > 1 else []
    notes.append({'step': 'response_scale', 'confidence': 'high',
                  'detail': '全部题目统一使用「{}」选项 —— 同一份量表内选项不一致会让总分失去意义'
                            .format(label)})
    draft = {
        'code': (spec.get('code') or '').strip(),
        'name': name,
        'category': (spec.get('category') or '').strip(),
        'source': 'ai',
        'definition': {
            'instruction': instruction,
            'items': items,
            'scoring': {'total': {'method': 'sum', 'items': 'all'},
                        'subscales': subscales, 'levels': []},
            'consistency': [],
        },
    }
    return _sanitize_generated(draft, notes)


def _generate_via_template(spec, notes):
    """模板后端: 不联网、不需要 key、结果完全可预期。

    产出的是**骨架**: 维度、题号、选项、分量表分组都排好, 题干留成待填占位。
    这不是降级方案 —— 一份需要临床方逐题审核的量表, 题干本来就该由他们写;
    骨架把结构性的活干完, 剩下的是他们无法外包的那部分。
    """
    dims = [d.strip() for d in (spec.get('dimensions') or []) if str(d).strip()]
    if not dims:
        dims = ['总体']
    per = spec.get('items_per_dimension')
    try:
        per = max(1, min(int(per or 3), 20))
    except (TypeError, ValueError):
        per = 3
    raw = []
    for dim in dims:
        for k in range(1, per + 1):
            raw.append({'text': '【待填写】{} · 第 {} 题'.format(dim, k), 'dimension': dim,
                        'reverse': False})
    notes.append({'step': 'backend', 'confidence': 'high',
                  'detail': '模板后端: 生成 {} 个维度 × {} 题的骨架, 题干需人工填写'
                            .format(len(dims), per)})
    goal = (spec.get('goal') or '').strip()
    name = (spec.get('name') or '').strip() or (goal[:30] if goal else '未命名量表')
    instruction = (spec.get('instruction') or '').strip() or (
        '请根据{}的实际情况作答。'.format(spec.get('population') or '您最近一段时间'))
    return _build_scale_from_items(spec, name, instruction, raw, notes)


def _generate_via_claude(spec, notes):
    """Claude 后端。返回 (draft, error)。

    默认不启用。启用需要三件事同时具备:
      1. 服务器装了 anthropic SDK
      2. 环境变量 SCALE_LLM_PROVIDER=claude
      3. ANTHROPIC_API_KEY (或 ANTHROPIC_AUTH_TOKEN) 已配置
    任何一件缺失都回退到模板后端并说明原因, 不静默失败也不硬报错。

    送出去的只有评估规格(目标/人群/维度), 不含任何患者数据 —— 生成量表这件事本身
    不需要接触患者信息, 所以这条边界是天然的, 代码里也不给传患者字段的口子。
    """
    try:
        import anthropic
    except ImportError:
        return None, ('服务器未安装 anthropic SDK。启用 Claude 后端需要: '
                      '/root/miniconda3/bin/pip install anthropic')
    if not (os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('ANTHROPIC_AUTH_TOKEN')):
        return None, '未配置 ANTHROPIC_API_KEY, Claude 后端不可用'

    dims = [d.strip() for d in (spec.get('dimensions') or []) if str(d).strip()]
    per = spec.get('items_per_dimension') or 3
    prompt = (
        '你在为临床随访平台起草一份评估量表的**题目草稿**, 产出会交给临床专业人员逐题审核后才使用。\n\n'
        '评估目标: {goal}\n目标人群: {pop}\n评估维度: {dims}\n每个维度题目数: {per}\n\n'
        '要求:\n'
        '- 每道题只问一件事, 用被评估者能直接判断的具体表现, 不要用专业术语\n'
        '- dimension 字段必须取自上面给定的维度列表\n'
        '- reverse 表示该题是否反向计分(表述方向与其余题目相反)\n'
        '- 不要给出任何评分阈值、分级或临床判定 —— 那部分由临床方依据实证研究补充\n'
    ).format(goal=spec.get('goal') or '(未说明)', pop=spec.get('population') or '(未说明)',
             dims=' / '.join(dims) or '(未指定)', per=per)

    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model='claude-opus-5',
            max_tokens=16000,
            output_config={'format': {'type': 'json_schema', 'schema': SCALE_GEN_SCHEMA}},
            messages=[{'role': 'user', 'content': prompt}],
        )
        # 安全分类器可能拒答, 此时是 HTTP 200 + stop_reason='refusal', content 为空。
        # 不先查这个就直接读 content[0] 会抛 IndexError。
        if getattr(resp, 'stop_reason', None) == 'refusal':
            return None, '模型拒绝了该生成请求 (stop_reason=refusal)'
        text = next((b.text for b in resp.content if b.type == 'text'), None)
        if not text:
            return None, '模型未返回文本内容'
        data = json.loads(text)
    except Exception as e:
        traceback.print_exc()
        return None, 'Claude 生成失败: {}'.format(e)

    raw = data.get('items') or []
    if not raw:
        return None, '模型返回的题目为空'
    notes.append({'step': 'backend', 'confidence': 'medium',
                  'detail': 'Claude 后端 (claude-opus-5) 生成 {} 道题, 题干与维度归属均需人工审核'
                            .format(len(raw))})
    return _build_scale_from_items(spec, (data.get('name') or '').strip() or '未命名量表',
                                   (data.get('instruction') or '').strip(), raw, notes), None


def generate_scale_draft(spec):
    """AI 量表生成入口。返回 (draft, report)。

    产出与 M11 的文档解析走**同一个审核界面**: 都是草稿, 都要人逐条确认后才入库。
    """
    notes = []
    backend = (spec.get('backend') or os.environ.get('SCALE_LLM_PROVIDER') or 'template').lower()
    draft = None
    if backend == 'claude':
        draft, err = _generate_via_claude(spec, notes)
        if err:
            notes.append({'step': 'backend_fallback', 'confidence': 'high',
                          'detail': 'Claude 后端不可用, 已回退到模板后端: {}'.format(err)})
    if draft is None:
        draft = _generate_via_template(spec, notes)

    notes.append({'step': 'no_levels', 'confidence': 'high',
                  'detail': '生成的量表刻意不含划界值分级。总分能算, 但"轻度/中度/重度"这类结论'
                            '必须由临床方依据目标人群的实证研究补充 —— 编出来的阈值会让每一份'
                            '评估报告都给出看似正常的错误结论'})
    d = draft['definition']
    report = {
        'item_count': len(d['items']),
        'items_without_options': sum(1 for i in d['items'] if not i.get('options')),
        'level_count': 0,
        'max_total_by_options': sum(max(o['value'] for o in i['options']) for i in d['items']
                                    if i.get('options')),
        'backend': backend,
        'notes': notes,
        'validation': validate_scale_definition(d),
        'needs_review': True,
    }
    return draft, report


def ensure_platform_scale_tables():
    """M10: 量表定义表 + 填报记录表 (idempotent)。"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_scale (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                code VARCHAR(64) NOT NULL COMMENT '量表编码, 如 PHQ-9',
                name VARCHAR(128) NOT NULL,
                category VARCHAR(64) DEFAULT NULL COMMENT '抑郁/焦虑/睡眠/认知/服药依从性...',
                version VARCHAR(32) DEFAULT '1',
                rater ENUM('clinician','self','both') DEFAULT 'both' COMMENT '他评/自评/皆可',
                source VARCHAR(32) DEFAULT 'import' COMMENT 'builtin/import/ai',
                stages JSON DEFAULT NULL COMMENT '阶段管理: 适用的随访阶段名数组',
                definition JSON NOT NULL COMMENT '题目+选项+评分规则+分级+字段映射',
                active TINYINT(1) DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uk_code_version (code, version),
                INDEX idx_category (category),
                INDEX idx_active (active)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M10 量表定义'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_scale_response (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                scale_code VARCHAR(64) NOT NULL,
                scale_version VARCHAR(32) DEFAULT '1',
                patient_no VARCHAR(64) NOT NULL,
                plan_id BIGINT DEFAULT NULL COMMENT '关联随访计划 = 阶段管理落点',
                answers JSON NOT NULL,
                total_score DECIMAL(10,2) DEFAULT NULL,
                subscores JSON DEFAULT NULL,
                level_label VARCHAR(64) DEFAULT NULL,
                level_advice VARCHAR(500) DEFAULT NULL,
                rater_type ENUM('clinician','self') DEFAULT 'clinician',
                operator VARCHAR(64) DEFAULT NULL,
                status ENUM('submitted','superseded') DEFAULT 'submitted',
                revision_of BIGINT DEFAULT NULL COMMENT '错误修订: 指向被本条取代的上一版',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_patient (patient_no, created_at),
                INDEX idx_scale (scale_code),
                INDEX idx_status (status),
                INDEX idx_plan (plan_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M10 量表填报记录 (修订走新增+标记旧版, 不原地改, 留痕)'
        """)
        print('[启动] platform_scale / platform_scale_response 表已就绪')
        cur.close()
    except Exception as e:
        print('[启动] ensure_platform_scale_tables 失败:', e)
    finally:
        conn.close()


def upsert_platform_scale(body):
    """建/改量表 (= 量表导入)。同 (code, version) 覆盖, 换 version 则并存新旧两版。"""
    code = str(body.get('code') or '').strip()
    name = str(body.get('name') or '').strip()
    if not code or not name:
        return None, 'code 和 name 必填'
    version = str(body.get('version') or '1').strip()
    rater = body.get('rater') or 'both'
    if rater not in ('clinician', 'self', 'both'):
        return None, "rater 必须是 clinician/self/both"
    definition = body.get('definition')
    if isinstance(definition, str):
        try:
            definition = json.loads(definition)
        except ValueError:
            return None, 'definition 不是合法 JSON'
    errs = validate_scale_definition(definition)
    if errs:
        return None, '量表定义有 {} 处问题: {}'.format(len(errs), '; '.join(errs[:6]))

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO platform_scale (code, name, category, version, rater, source, stages, definition, active)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              name=VALUES(name), category=VALUES(category), rater=VALUES(rater),
              source=VALUES(source), stages=VALUES(stages), definition=VALUES(definition),
              active=VALUES(active)
        """, (code, name, body.get('category') or None, version, rater,
              body.get('source') or 'import',
              json.dumps(body.get('stages') or [], ensure_ascii=False),
              json.dumps(definition, ensure_ascii=False),
              0 if body.get('active') in (0, False, '0') else 1))
        cur.close()
        return {'code': code, 'version': version, 'items': len(definition.get('items') or [])}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def query_platform_scales(code=None, category=None, active_only=True, with_definition=False):
    """量表列表 / 单份定义。列表默认不带 definition —— 一份量表几十道题, 列表页不需要。"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ['1=1'], []
        if code:
            where.append('code = %s'); params.append(code)
        if category:
            where.append('category = %s'); params.append(category)
        if active_only:
            where.append('active = 1')
        # 题目数走 SQL 侧的 JSON_LENGTH 取, 不为了数个数把整份 definition(几十道题) 拉回来 ——
        # 列表页只需要这一个数字。带 definition 时仍然算, 保证两条路径的字段一致。
        cols = ('id, code, name, category, version, rater, source, stages, active, '
                "created_at, updated_at, JSON_LENGTH(definition, '$.items') AS item_count")
        if with_definition or code:
            cols += ', definition'
        cur.execute('SELECT {} FROM platform_scale WHERE {} ORDER BY category, code'.format(
            cols, ' AND '.join(where)), params)
        names = [d[0] for d in cur.description]
        out = []
        for row in cur.fetchall():
            r = dict(zip(names, row))
            for k in ('created_at', 'updated_at'):
                if r.get(k) is not None and hasattr(r[k], 'strftime'):
                    r[k] = r[k].strftime('%Y-%m-%d %H:%M:%S')
            for k in ('stages', 'definition'):
                if isinstance(r.get(k), str):
                    try:
                        r[k] = json.loads(r[k])
                    except ValueError:
                        pass
            out.append(r)
        cur.close()
        return {'ok': True, 'count': len(out), 'scales': out}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def submit_scale_response(body):
    """提交一份填报: 取定义 -> 评分 + 校验 -> 落库。

    {scale_code, scale_version?, patient_no, answers{}, rater_type?, operator?, plan_id?,
     revision_of?, allow_errors?}

    revision_of 给了 = 错误修订: 新插一条, 把被修订的那条标成 superseded, 两条都留在库里。
    不原地 UPDATE —— 标书 ★1.6.2 要"错误修订", 方案 §4.3(3) 要"修改留痕/历史版本/可回退",
    原地改就把这两条都毁了。

    默认校验不过就拒收(errors 原样返回给前端逐题标红)。allow_errors=true 时仍落库,
    用于"先存草稿、回头补"的场景, 但 status 仍是 submitted, 由前端自己决定要不要提示。
    """
    code = str(body.get('scale_code') or '').strip()
    patient_no = str(body.get('patient_no') or '').strip()
    answers = body.get('answers')
    if not code or not patient_no:
        return None, 'scale_code 和 patient_no 必填'
    if not isinstance(answers, dict):
        return None, 'answers 必须是对象 {题目id: 答案}'
    rater_type = body.get('rater_type') or 'clinician'
    if rater_type not in ('clinician', 'self'):
        return None, "rater_type 必须是 clinician 或 self"

    conn = get_connection()
    try:
        cur = conn.cursor()
        version = body.get('scale_version')
        if version:
            cur.execute('SELECT version, definition FROM platform_scale WHERE code=%s AND version=%s',
                        (code, str(version)))
        else:
            cur.execute('SELECT version, definition FROM platform_scale WHERE code=%s AND active=1 '
                        'ORDER BY updated_at DESC LIMIT 1', (code,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, '量表不存在或已停用: {}'.format(code)
        version, definition = row[0], row[1]
        if isinstance(definition, str):
            definition = json.loads(definition)

        result, errors = score_scale(definition, answers)
        if errors and not body.get('allow_errors'):
            cur.close()
            return {'ok': False, 'scored': False, 'errors': errors, 'result': result}, None

        revision_of = body.get('revision_of')
        if revision_of is not None:
            try:
                revision_of = int(revision_of)
            except (TypeError, ValueError):
                cur.close()
                return None, 'revision_of 必须是整数'
            cur.execute('SELECT id FROM platform_scale_response WHERE id=%s', (revision_of,))
            if not cur.fetchone():
                cur.close()
                return None, '被修订的记录不存在: {}'.format(revision_of)

        lv = result.get('level') or {}
        cur.execute("""
            INSERT INTO platform_scale_response
              (scale_code, scale_version, patient_no, plan_id, answers, total_score, subscores,
               level_label, level_advice, rater_type, operator, status, revision_of)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'submitted',%s)
        """, (code, version, patient_no, body.get('plan_id'),
              json.dumps(answers, ensure_ascii=False), result.get('total'),
              json.dumps(result.get('subscores') or {}, ensure_ascii=False),
              lv.get('label'), lv.get('advice'), rater_type, body.get('operator') or None,
              revision_of))
        new_id = cur.lastrowid
        if revision_of is not None:
            cur.execute("UPDATE platform_scale_response SET status='superseded' WHERE id=%s", (revision_of,))
        cur.close()
        return {'ok': True, 'scored': True, 'id': new_id, 'scale_code': code,
                'scale_version': version, 'result': result, 'errors': errors}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def query_scale_responses(patient_no=None, code=None, include_superseded=False, limit=100):
    """填报记录列表。默认只给现行版(superseded 的历史版要显式要)。"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ['1=1'], []
        if patient_no:
            where.append('r.patient_no = %s'); params.append(patient_no)
        if code:
            where.append('r.scale_code = %s'); params.append(code)
        if not include_superseded:
            where.append("r.status = 'submitted'")
        params.append(int(limit))
        cur.execute("""
            SELECT r.id, r.scale_code, r.scale_version, s.name, r.patient_no, p.name,
                   r.total_score, r.subscores, r.level_label, r.level_advice,
                   r.rater_type, r.operator, r.status, r.revision_of, r.plan_id, r.created_at
            FROM platform_scale_response r
            LEFT JOIN platform_scale s ON s.code = r.scale_code AND s.version = r.scale_version
            LEFT JOIN platform_patient p ON p.patient_no = r.patient_no
            WHERE {}
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT %s
        """.format(' AND '.join(where)), params)
        cols = ['id', 'scale_code', 'scale_version', 'scale_name', 'patient_no', 'patient_name',
                'total_score', 'subscores', 'level_label', 'level_advice', 'rater_type',
                'operator', 'status', 'revision_of', 'plan_id', 'created_at']
        out = []
        for row in cur.fetchall():
            r = dict(zip(cols, row))
            if r.get('created_at') is not None and hasattr(r['created_at'], 'strftime'):
                r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            if r.get('total_score') is not None:
                r['total_score'] = float(r['total_score'])
            if isinstance(r.get('subscores'), str):
                try:
                    r['subscores'] = json.loads(r['subscores'])
                except ValueError:
                    pass
            out.append(r)
        cur.close()
        return {'ok': True, 'count': len(out), 'responses': out}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


# ============ 随访平台 1.1 M13 (全流程数据质控, 方案 §4.3) ============
#
# 这一块和 M7 的体征预警长得像, 但**必须分开**, 混起来两边都废:
#
#   M7 预警  = "这个数说明患者需要关注"  -> 收件人是临床, 动作是打电话/叫回院
#   M13 质控 = "这个数大概率是错的"      -> 收件人是数据管理, 动作是找录入员核对
#
# 把录入笔误(体温 366.0)当预警推给医生, 医生几次之后就不看预警了; 把真的高热当质控
# 发现丢进数据待办, 就没人给患者打电话。所以两者分表、分状态机、分收件人, 只在
# "同一条记录同时命中两边" 时各出各的。
#
# 生理上不可能的值。注意这**不是**临床异常线(那是 VITAL_THRESHOLDS):
#   体温 39.0 是临床危急但物理上完全可能 -> 走 M7 预警, 不是质控发现
#   体温 366.0 只能是漏了小数点          -> 走质控发现, 不该惊动医生
QC_IMPOSSIBLE = {
    'hr':    {'label': '心率',       'unit': 'bpm',  'lo': 20,   'hi': 250},
    'spo2':  {'label': '血氧饱和度', 'unit': '%',    'lo': 50,   'hi': 100},
    'sbp':   {'label': '收缩压',     'unit': 'mmHg', 'lo': 50,   'hi': 300},
    'dbp':   {'label': '舒张压',     'unit': 'mmHg', 'lo': 20,   'hi': 200},
    'temp':  {'label': '体温',       'unit': '℃',   'lo': 30.0, 'hi': 45.0},
    'sleep': {'label': '睡眠时长',   'unit': '分钟', 'lo': 0,    'hi': 1440},
}

# 历次对比的判定参数 (方案 §4.3(1) "历次随访数据自动对比, 差异数据自动标红")
QC_DELTA_RATIO = 0.5      # 总分相对上次变化超过这个比例即提示
QC_DELTA_MIN = 5.0        # 且绝对变化不少于这么多分 —— 只用比例的话 2 分变 4 分也会报
QC_DUP_MINUTES = 30       # 同患者同量表这么多分钟内重复提交, 视为可疑

QC_RULES = {
    'required_missing':     ('block', '必填项未作答'),
    'out_of_range':         ('block', '数值超出题目允许范围'),
    'format_invalid':       ('block', '格式不合法'),
    'impossible_value':     ('block', '生理上不可能的数值, 疑为录入错误'),
    'cross_field_conflict': ('block', '字段间互相矛盾'),
    'delta_jump':           ('warn',  '与上次填报差异过大'),
    'identical_to_previous': ('warn', '与上次填报逐题完全一致'),
    'duplicate_submission': ('warn',  '短时间内重复提交'),
}

QC_ROLES = ('site_qc', 'db_qc', 'auditor', 'entry')
QC_ROLE_LABELS = {'site_qc': '单位质控员', 'db_qc': '数据库级质控员',
                  'auditor': '第三方稽查员', 'entry': '录入员'}


def _qc_check_id_card(s):
    """身份证号校验。走 ISO 7064 MOD 11-2 校验位, 不只是正则。

    只用正则(18 位数字 + 末位 X)拦不住最常见的错误 —— 相邻两位打颠倒。
    校验位算得出来才说明这串数字是**发放过的**格式, 这正是录入核对要的东西。
    """
    s = (s or '').strip().upper()
    if len(s) != 18 or not s[:17].isdigit() or s[17] not in '0123456789X':
        return '身份证号应为 18 位(末位可为 X)'
    w = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    chk = '10X98765432'[sum(int(s[i]) * w[i] for i in range(17)) % 11]
    if chk != s[17]:
        return '身份证号校验位不符(应为 {}), 常见于相邻数字打颠倒'.format(chk)
    m = int(s[10:12])
    d = int(s[12:14])
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return '身份证号中的出生日期不合法'
    return None


def _qc_check_phone(s):
    """手机号校验(中国大陆 11 位)。"""
    s = re.sub(r'[\s\-]', '', (s or '').strip())
    if not re.match(r'^1[3-9]\d{9}$', s):
        return '手机号应为 1 开头的 11 位数字'
    return None


QC_FORMAT_CHECKS = {'id_card': _qc_check_id_card, 'phone': _qc_check_phone}


def _qc_finding(rule, patient_no, target_kind, target_id, detail, basis=None, item_id=None):
    """造一条质控发现。severity 由 QC_RULES 统一决定, 调用点不各自拍脑袋定档。"""
    sev, rule_label = QC_RULES.get(rule, ('warn', rule))
    return {'rule_code': rule, 'rule_label': rule_label, 'severity': sev,
            'patient_no': patient_no, 'target_kind': target_kind, 'target_id': target_id,
            'item_id': item_id, 'detail': detail, 'basis': basis or {}}


def qc_check_scale_answers(definition, answers, patient_no=None, target_id=None):
    """自动质控 · 单份填报的**表内**规则 (方案 §4.3(1) 必填/范围/格式)。

    和 score_scale 的校验有重叠但用途不同: score_scale 是"能不能算分",
    这里是"这份数据能不能进库存档"。前者拦不住的东西这里要拦 —— 典型是
    定义里挂了 format 的题(身份证/手机号), 评分根本不关心它们的内容。
    """
    out = []
    items = (definition or {}).get('items') or []
    for it in items:
        iid = it.get('id')
        v = answers.get(iid)
        blank = v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list) and not v)
        if it.get('required') and blank:
            out.append(_qc_finding('required_missing', patient_no, 'scale_response', target_id,
                                   '第 {} 题「{}」为必填但未作答'.format(iid, str(it.get('text'))[:30]),
                                   {'item_id': iid}, iid))
            continue
        if blank:
            continue
        if it.get('type') in ('number', 'scale') and isinstance(v, (int, float)):
            lo, hi = it.get('min'), it.get('max')
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                out.append(_qc_finding('out_of_range', patient_no, 'scale_response', target_id,
                                       '第 {} 题填了 {}, 允许范围 {}~{}'.format(
                                           iid, _fmt_num(v), _fmt_num(lo), _fmt_num(hi)),
                                       {'item_id': iid, 'value': v, 'min': lo, 'max': hi}, iid))
        fmt = it.get('format')
        if fmt in QC_FORMAT_CHECKS and isinstance(v, str):
            msg = QC_FORMAT_CHECKS[fmt](v)
            if msg:
                out.append(_qc_finding('format_invalid', patient_no, 'scale_response', target_id,
                                       '第 {} 题: {}'.format(iid, msg),
                                       {'item_id': iid, 'format': fmt}, iid))
    return out


def qc_compare_with_previous(definition, answers, total, prev, patient_no=None, target_id=None):
    """自动质控 · 历次对比 (方案 §4.3(1) "历次随访数据自动对比, 差异数据自动标红")。

    prev = 上一份现行填报的 dict(含 answers/total_score/created_at), 没有则返回空。

    两条规则里, **逐题完全一致** 才是这块的重点。分数跳变临床上常有真实原因
    (换药、急性发作), 报出来多半是虚惊; 而两次随访隔了几周、几十道题一字不差,
    现实中基本只有一个解释 —— 这次没真做, 是照着上次抄的/复制的。
    这种数据不会触发任何其他校验(它每一项都合法), 只有和历史比才看得出来,
    也正是临床研究稽查最在意的一类问题。
    """
    out = []
    if not prev:
        return out
    p_ans = prev.get('answers') or {}
    p_total = prev.get('total_score')
    when = prev.get('created_at') or ''

    ids = [it.get('id') for it in ((definition or {}).get('items') or [])]
    # 只比两次都答了的题 —— 一边空一边有值不算"一致", 也不算"不一致", 没有可比性
    comparable = [i for i in ids if answers.get(i) is not None and p_ans.get(i) is not None]
    diffs = [i for i in comparable if answers.get(i) != p_ans.get(i)]
    # 答案本身有几种取值。全选同一个档(比如筛查量表上一路"没有")复现是常事,
    # 拿它报"疑似照抄"会天天误报; 而一组**有起伏**的答案隔几周一字不差地重现,
    # 现实中基本只有照抄一个解释。所以这道闸门卡的是"花样", 不是"题数"。
    variety = len(set(json.dumps(answers.get(i), ensure_ascii=False, sort_keys=True)
                      for i in comparable))
    if len(comparable) >= 4 and variety >= 2 and not diffs:
        out.append(_qc_finding('identical_to_previous', patient_no, 'scale_response', target_id,
                               '本次 {} 道题的答案与 {} 那次逐题完全一致(且答案有 {} 种取值, 非一路同档) '
                               '—— 请确认本次是实际重新评估, 而非沿用上次结果'.format(
                                   len(comparable), when, variety),
                               {'prev_id': prev.get('id'), 'prev_at': when,
                                'compared_items': len(comparable), 'diff_items': 0,
                                'answer_variety': variety}))

    if isinstance(total, (int, float)) and isinstance(p_total, (int, float)):
        delta = total - p_total
        base = abs(p_total) or 1.0
        if abs(delta) >= QC_DELTA_MIN and abs(delta) / base >= QC_DELTA_RATIO:
            out.append(_qc_finding('delta_jump', patient_no, 'scale_response', target_id,
                                   '总分由 {} 变为 {} ({}{}), 较上次({})变化 {:.0%}, 请核对是否录入有误'.format(
                                       _fmt_num(p_total), _fmt_num(total),
                                       '+' if delta > 0 else '', _fmt_num(delta), when,
                                       abs(delta) / base),
                                   {'prev_id': prev.get('id'), 'prev_at': when,
                                    'prev_total': p_total, 'total': total, 'delta': delta,
                                    'diff_items': diffs[:20]}))
    return out


def qc_check_vital(metric, value, patient_no=None, target_id=None):
    """自动质控 · 体征数值 (生理不可能值)。返回 findings 列表。"""
    cfg = QC_IMPOSSIBLE.get(metric)
    if not cfg or not isinstance(value, (int, float)):
        return []
    if cfg['lo'] <= value <= cfg['hi']:
        return []
    return [_qc_finding('impossible_value', patient_no, 'vital', target_id,
                        '{} = {}{}, 超出生理可能范围 {}~{}{} —— 按录入/传输错误处理, 不当临床异常'.format(
                            cfg['label'], _fmt_num(value), cfg['unit'],
                            _fmt_num(cfg['lo']), _fmt_num(cfg['hi']), cfg['unit']),
                        {'metric': metric, 'value': value, 'lo': cfg['lo'], 'hi': cfg['hi']})]


def qc_check_bp_pair(sbp, dbp, patient_no=None, target_id=None):
    """自动质控 · 血压跨字段逻辑: 收缩压必须高于舒张压。

    单看 sbp=80 / dbp=120 两个数都在各自可能范围内, 只有放在一起才看得出是
    高低压填反了 —— 这类"字段间矛盾"是 EDC 逻辑核查的经典项, 单字段规则抓不到。
    """
    if not isinstance(sbp, (int, float)) or not isinstance(dbp, (int, float)):
        return []
    if sbp > dbp:
        return []
    return [_qc_finding('cross_field_conflict', patient_no, 'vital', target_id,
                        '收缩压 {} 不高于舒张压 {}, 疑为高低压填反'.format(_fmt_num(sbp), _fmt_num(dbp)),
                        {'sbp': sbp, 'dbp': dbp})]


def ensure_platform_qc_tables():
    """M13: 质控发现表 + 质疑单表 + 质疑流转日志表 (idempotent)。"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_qc_finding (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                patient_no VARCHAR(64) DEFAULT NULL,
                target_kind VARCHAR(24) NOT NULL COMMENT 'scale_response/vital/patient',
                target_id BIGINT DEFAULT NULL,
                item_id VARCHAR(64) DEFAULT NULL COMMENT '定位到具体题目, 前端据此标红',
                rule_code VARCHAR(32) NOT NULL,
                severity ENUM('block','warn') NOT NULL COMMENT '强校验阻断/弱校验提示, 方案 §2(2)',
                detail VARCHAR(500) DEFAULT NULL,
                basis JSON DEFAULT NULL COMMENT '判定依据: 本次值/上次值/阈值, 供人复核',
                status ENUM('open','queried','resolved','dismissed') DEFAULT 'open',
                dedup_key VARCHAR(191) DEFAULT NULL COMMENT '规则+目标+题目, 重跑不重复堆积',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uk_qc_dedup (dedup_key),
                INDEX idx_patient (patient_no),
                INDEX idx_status (status),
                INDEX idx_target (target_kind, target_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M13 自动质控发现'
        """)
        # 质疑单与"发现"分开: 发现是机器判的, 质疑是人提的。人可以对没有任何自动发现的
        # 数据提质疑(finding_id 为空), 也可以看着一条发现认为没问题而直接 dismiss 不提质疑。
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_qc_query (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                finding_id BIGINT DEFAULT NULL COMMENT '由哪条自动发现引发; 人工直接发起则为空',
                patient_no VARCHAR(64) DEFAULT NULL,
                target_kind VARCHAR(24) NOT NULL,
                target_id BIGINT DEFAULT NULL,
                item_id VARCHAR(64) DEFAULT NULL,
                question VARCHAR(1000) NOT NULL,
                raised_by VARCHAR(64) DEFAULT NULL,
                raiser_role ENUM('site_qc','db_qc','auditor') DEFAULT 'site_qc',
                status ENUM('open','answered','closed','reopened') DEFAULT 'open',
                answer VARCHAR(1000) DEFAULT NULL,
                answered_by VARCHAR(64) DEFAULT NULL,
                answered_at DATETIME DEFAULT NULL,
                closed_by VARCHAR(64) DEFAULT NULL,
                closed_at DATETIME DEFAULT NULL,
                close_note VARCHAR(500) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_patient (patient_no),
                INDEX idx_status (status),
                INDEX idx_finding (finding_id),
                INDEX idx_target (target_kind, target_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M13 质疑单 (GCP query)'
        """)
        # 只增不改的流转日志。质疑单本身的 status 是"当前状态"的快照, 方便查询;
        # 谁在什么时候把它从哪一步推到哪一步, 只认这张表 —— 稽查看的是这张。
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_qc_query_log (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                query_id BIGINT NOT NULL,
                action ENUM('raise','answer','close','reopen') NOT NULL,
                from_status VARCHAR(16) DEFAULT NULL,
                to_status VARCHAR(16) DEFAULT NULL,
                operator VARCHAR(64) DEFAULT NULL,
                operator_role VARCHAR(16) DEFAULT NULL,
                remark VARCHAR(1000) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_query (query_id, id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M13 质疑流转留痕 (只增不改)'
        """)
        print('[启动] platform_qc_finding / platform_qc_query / platform_qc_query_log 表已就绪')
        cur.close()
    except Exception as e:
        print('[启动] ensure_platform_qc_tables 失败:', e)
    finally:
        conn.close()


def _qc_persist(cur, findings):
    """把发现写库, 按 dedup_key 幂等。返回 (新增数, 命中已存在数)。

    dedup_key = 规则 + 目标 + 题目。重跑质控不该把同一个问题堆成十几条 ——
    数据管理员看到的待办列表要能反映"还剩多少个问题", 不是"跑了多少次"。
    已存在的只刷新 detail/basis(阈值可能被临床方调过), 不动 status ——
    人已经处理成 resolved 的, 不因为重跑又变回 open。
    """
    new_n = hit_n = 0
    for f in findings:
        key = '{}|{}|{}|{}'.format(f['rule_code'], f['target_kind'],
                                   f.get('target_id') or '-', f.get('item_id') or '-')
        cur.execute("""
            INSERT INTO platform_qc_finding
              (patient_no, target_kind, target_id, item_id, rule_code, severity, detail, basis, dedup_key)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE detail=VALUES(detail), basis=VALUES(basis)
        """, (f.get('patient_no'), f['target_kind'], f.get('target_id'), f.get('item_id'),
              f['rule_code'], f['severity'], f.get('detail'),
              json.dumps(f.get('basis') or {}, ensure_ascii=False), key))
        # rowcount: 1 = 新插, 2 = 命中重复走了 UPDATE
        if cur.rowcount == 1:
            new_n += 1
        else:
            hit_n += 1
    return new_n, hit_n


def platform_qc_run(patient_no=None, scale_code=None, limit=500):
    """跑一遍自动质控 (方案 §4.3(1))。当前覆盖量表填报, 逐份做表内校验 + 历次对比。

    只扫现行版(status='submitted'), 被修订掉的历史版不再挑毛病 —— 那些问题正是
    修订要解决的, 重复报出来只会让待办永远清不空。
    """
    ensure_platform_qc_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ["r.status='submitted'"], []
        if patient_no:
            where.append('r.patient_no=%s'); params.append(patient_no)
        if scale_code:
            where.append('r.scale_code=%s'); params.append(scale_code)
        params.append(int(limit))
        cur.execute("""
            SELECT r.id, r.patient_no, r.scale_code, r.scale_version, r.answers,
                   r.total_score, r.created_at, s.definition
            FROM platform_scale_response r
            LEFT JOIN platform_scale s ON s.code=r.scale_code AND s.version=r.scale_version
            WHERE {}
            ORDER BY r.patient_no, r.scale_code, r.created_at
            LIMIT %s
        """.format(' AND '.join(where)), params)
        rows = cur.fetchall()

        findings = []
        prev_by_key = {}     # (patient_no, scale_code) -> 上一份, 用于历次对比
        last_at = {}         # 同上, 用于重复提交判定
        scanned = 0
        for rid, pno, code, ver, ans, total, created, defn in rows:
            if isinstance(ans, str):
                try:
                    ans = json.loads(ans)
                except ValueError:
                    ans = {}
            if isinstance(defn, str):
                try:
                    defn = json.loads(defn)
                except ValueError:
                    defn = None
            if not defn:
                # 量表定义找不到 = 填报引用了已删除的版本, 这本身就是个数据问题
                findings.append(_qc_finding('cross_field_conflict', pno, 'scale_response', rid,
                                            '填报引用的量表 {} v{} 在库中不存在, 无法校验也无法重算分'.format(code, ver),
                                            {'scale_code': code, 'scale_version': ver}))
                continue
            scanned += 1
            total_f = float(total) if total is not None else None
            findings += qc_check_scale_answers(defn, ans, pno, rid)

            key = (pno, code)
            findings += qc_compare_with_previous(defn, ans, total_f, prev_by_key.get(key), pno, rid)
            if key in last_at and created and last_at[key]:
                gap = (created - last_at[key]).total_seconds() / 60.0
                if 0 <= gap <= QC_DUP_MINUTES:
                    findings.append(_qc_finding('duplicate_submission', pno, 'scale_response', rid,
                                                '距上一份 {} 填报仅 {:.0f} 分钟, 请确认是否重复提交'.format(code, gap),
                                                {'gap_minutes': round(gap, 1)}))
            prev_by_key[key] = {'id': rid, 'answers': ans, 'total_score': total_f,
                                'created_at': created.strftime('%Y-%m-%d %H:%M') if created else ''}
            last_at[key] = created

        new_n, hit_n = _qc_persist(cur, findings)
        cur.close()
        by_rule = {}
        for f in findings:
            by_rule[f['rule_code']] = by_rule.get(f['rule_code'], 0) + 1
        return {'ok': True, 'scanned_responses': scanned, 'findings': len(findings),
                'new': new_n, 'already_known': hit_n, 'by_rule': by_rule,
                'blocking': sum(1 for f in findings if f['severity'] == 'block')}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def query_qc_findings(status=None, patient_no=None, severity=None, limit=100):
    """质控发现列表。默认只给未处理的 —— 已处理的要显式要。"""
    ensure_platform_qc_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ['1=1'], []
        if status:
            where.append('f.status=%s'); params.append(status)
        else:
            where.append("f.status IN ('open','queried')")
        if patient_no:
            where.append('f.patient_no=%s'); params.append(patient_no)
        if severity:
            where.append('f.severity=%s'); params.append(severity)
        params.append(int(limit))
        cur.execute("""
            SELECT f.id, f.patient_no, p.name, f.target_kind, f.target_id, f.item_id,
                   f.rule_code, f.severity, f.detail, f.basis, f.status, f.created_at,
                   (SELECT COUNT(*) FROM platform_qc_query q WHERE q.finding_id=f.id) AS query_count
            FROM platform_qc_finding f
            LEFT JOIN platform_patient p ON p.patient_no=f.patient_no
            WHERE {}
            ORDER BY FIELD(f.severity,'block','warn'), f.created_at DESC
            LIMIT %s
        """.format(' AND '.join(where)), params)
        cols = ['id', 'patient_no', 'patient_name', 'target_kind', 'target_id', 'item_id',
                'rule_code', 'severity', 'detail', 'basis', 'status', 'created_at', 'query_count']
        out = []
        for row in cur.fetchall():
            r = dict(zip(cols, row))
            r['rule_label'] = QC_RULES.get(r['rule_code'], ('', r['rule_code']))[1]
            if r.get('created_at') is not None and hasattr(r['created_at'], 'strftime'):
                r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            if isinstance(r.get('basis'), str):
                try:
                    r['basis'] = json.loads(r['basis'])
                except ValueError:
                    pass
            out.append(r)
        cur.close()
        return {'ok': True, 'count': len(out), 'findings': out,
                'rules': {k: {'severity': v[0], 'label': v[1]} for k, v in QC_RULES.items()}}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


# 质疑单状态机。写成表而不是散在 if 里, 是因为验收时要拿它对 GCP 流程,
# 一张表比翻代码好核。closed 之后仍允许 reopen —— 稽查员复核时推翻质控员的结论,
# 是这套流程存在的意义之一, 不能把它做成终态。
QC_QUERY_TRANSITIONS = {
    'answer': {'from': ('open', 'reopened'), 'to': 'answered'},
    'close':  {'from': ('open', 'answered', 'reopened'), 'to': 'closed'},
    'reopen': {'from': ('answered', 'closed'), 'to': 'reopened'},
}


def platform_qc_query_raise(body):
    """提质疑 (方案 §4.3(2))。{target_kind, target_id, question, patient_no?, finding_id?,
    item_id?, raised_by?, raiser_role?}

    注意: raiser_role 现在只是**记录**谁以什么身份提的, 没有鉴权 —— 平台还没有账号
    体系(方案 §4.1 权限模块未建), 任何人调这个接口都能写任意 role。要满足 GCP 的
    权责分离, 必须等鉴权做完再把这个字段接到登录身份上。在那之前, 这里产出的留痕
    对内可用(知道是谁做的), 对外不能当合规证据。
    """
    kind = str(body.get('target_kind') or '').strip()
    question = str(body.get('question') or '').strip()
    if kind not in ('scale_response', 'vital', 'patient'):
        return None, "target_kind 必须是 scale_response/vital/patient"
    if not question:
        return None, 'question 必填 —— 质疑必须说清楚疑点是什么, 否则录入员无从核查'
    role = body.get('raiser_role') or 'site_qc'
    if role not in ('site_qc', 'db_qc', 'auditor'):
        return None, 'raiser_role 必须是 site_qc/db_qc/auditor'
    finding_id = body.get('finding_id')

    ensure_platform_qc_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        patient_no = body.get('patient_no')
        if finding_id is not None:
            cur.execute('SELECT patient_no, item_id FROM platform_qc_finding WHERE id=%s', (finding_id,))
            row = cur.fetchone()
            if not row:
                cur.close()
                return None, '质控发现不存在: {}'.format(finding_id)
            patient_no = patient_no or row[0]
        cur.execute("""
            INSERT INTO platform_qc_query
              (finding_id, patient_no, target_kind, target_id, item_id, question, raised_by, raiser_role, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'open')
        """, (finding_id, patient_no, kind, body.get('target_id'), body.get('item_id'),
              question[:1000], body.get('raised_by') or None, role))
        qid = cur.lastrowid
        cur.execute("""
            INSERT INTO platform_qc_query_log (query_id, action, from_status, to_status, operator, operator_role, remark)
            VALUES (%s,'raise',NULL,'open',%s,%s,%s)
        """, (qid, body.get('raised_by') or None, role, question[:1000]))
        if finding_id is not None:
            cur.execute("UPDATE platform_qc_finding SET status='queried' WHERE id=%s AND status='open'",
                        (finding_id,))
        cur.close()
        return {'ok': True, 'query_id': qid, 'status': 'open'}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def platform_qc_query_transition(body):
    """推进质疑单 {query_id, action: answer|close|reopen, operator?, operator_role?, remark?}。

    每一步都往 log 表追一行, 质疑单自己只保留当前状态。非法流转直接拒绝并
    把允许的前置状态告诉调用方 —— 让人知道为什么不行, 比只说"失败"有用。
    """
    try:
        qid = int(body.get('query_id'))
    except (TypeError, ValueError):
        return None, 'query_id 必填且为整数'
    action = str(body.get('action') or '').strip()
    tr = QC_QUERY_TRANSITIONS.get(action)
    if not tr:
        return None, 'action 必须是 {}'.format('/'.join(QC_QUERY_TRANSITIONS))
    remark = str(body.get('remark') or '').strip()
    if action == 'answer' and not remark:
        return None, 'answer 必须带 remark —— 回复内容就是核查结论, 空回复等于没查'

    ensure_platform_qc_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT status, finding_id FROM platform_qc_query WHERE id=%s', (qid,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, '质疑单不存在: {}'.format(qid)
        cur_status, finding_id = row
        if cur_status not in tr['from']:
            cur.close()
            return None, '当前状态 {} 不能执行 {} (允许的前置状态: {})'.format(
                cur_status, action, '/'.join(tr['from']))
        new_status = tr['to']
        op = body.get('operator') or None
        role = body.get('operator_role') or None

        if action == 'answer':
            cur.execute("""UPDATE platform_qc_query SET status=%s, answer=%s, answered_by=%s,
                           answered_at=NOW() WHERE id=%s""", (new_status, remark[:1000], op, qid))
        elif action == 'close':
            cur.execute("""UPDATE platform_qc_query SET status=%s, closed_by=%s, closed_at=NOW(),
                           close_note=%s WHERE id=%s""", (new_status, op, remark[:500] or None, qid))
            if finding_id is not None:
                cur.execute("UPDATE platform_qc_finding SET status='resolved' WHERE id=%s", (finding_id,))
        else:   # reopen: 清掉上一轮的回复, 但 log 里那一行永远在
            cur.execute("""UPDATE platform_qc_query SET status=%s, closed_by=NULL, closed_at=NULL,
                           close_note=NULL WHERE id=%s""", (new_status, qid))
            if finding_id is not None:
                cur.execute("UPDATE platform_qc_finding SET status='queried' WHERE id=%s", (finding_id,))

        cur.execute("""
            INSERT INTO platform_qc_query_log (query_id, action, from_status, to_status, operator, operator_role, remark)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (qid, action, cur_status, new_status, op, role, remark[:1000] or None))
        cur.close()
        return {'ok': True, 'query_id': qid, 'from': cur_status, 'to': new_status}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def query_qc_queries(status=None, patient_no=None, with_log=False, query_id=None, limit=100):
    """质疑单列表 / 单张详情(带完整流转留痕)。"""
    ensure_platform_qc_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ['1=1'], []
        if query_id:
            where.append('q.id=%s'); params.append(int(query_id))
        if status:
            where.append('q.status=%s'); params.append(status)
        if patient_no:
            where.append('q.patient_no=%s'); params.append(patient_no)
        params.append(int(limit))
        cur.execute("""
            SELECT q.id, q.finding_id, q.patient_no, p.name, q.target_kind, q.target_id, q.item_id,
                   q.question, q.raised_by, q.raiser_role, q.status, q.answer, q.answered_by,
                   q.answered_at, q.closed_by, q.closed_at, q.close_note, q.created_at,
                   f.rule_code, f.severity, f.detail
            FROM platform_qc_query q
            LEFT JOIN platform_patient p ON p.patient_no=q.patient_no
            LEFT JOIN platform_qc_finding f ON f.id=q.finding_id
            WHERE {}
            ORDER BY FIELD(q.status,'open','reopened','answered','closed'), q.created_at DESC
            LIMIT %s
        """.format(' AND '.join(where)), params)
        cols = ['id', 'finding_id', 'patient_no', 'patient_name', 'target_kind', 'target_id',
                'item_id', 'question', 'raised_by', 'raiser_role', 'status', 'answer',
                'answered_by', 'answered_at', 'closed_by', 'closed_at', 'close_note',
                'created_at', 'rule_code', 'severity', 'finding_detail']
        out = []
        for row in cur.fetchall():
            r = dict(zip(cols, row))
            for k in ('answered_at', 'closed_at', 'created_at'):
                if r.get(k) is not None and hasattr(r[k], 'strftime'):
                    r[k] = r[k].strftime('%Y-%m-%d %H:%M:%S')
            r['raiser_role_label'] = QC_ROLE_LABELS.get(r.get('raiser_role'), r.get('raiser_role'))
            out.append(r)
        if (with_log or query_id) and out:
            ids = [r['id'] for r in out]
            cur.execute("""
                SELECT query_id, action, from_status, to_status, operator, operator_role, remark, created_at
                FROM platform_qc_query_log WHERE query_id IN ({}) ORDER BY query_id, id
            """.format(','.join(['%s'] * len(ids))), ids)
            logs = {}
            for qid, act, fr, to, op, role, remark, at in cur.fetchall():
                logs.setdefault(qid, []).append({
                    'action': act, 'from': fr, 'to': to, 'operator': op,
                    'operator_role': role, 'operator_role_label': QC_ROLE_LABELS.get(role, role),
                    'remark': remark,
                    'at': at.strftime('%Y-%m-%d %H:%M:%S') if hasattr(at, 'strftime') else at})
            for r in out:
                r['log'] = logs.get(r['id'], [])
        cur.close()
        return {'ok': True, 'count': len(out), 'queries': out}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def platform_qc_compare(patient_no, scale_code):
    """历次对比明细 (方案 §4.3(1) "差异数据自动标红, 支持对比结果导出")。

    返回逐题的历次答案矩阵 + 每次相对上一次的变化标记, 前端据此标红, 也是导出的数据源。
    """
    if not patient_no or not scale_code:
        return None, 'patient_no 和 scale_code 必填'
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT r.id, r.answers, r.total_score, r.level_label, r.created_at, r.operator, s.definition
            FROM platform_scale_response r
            LEFT JOIN platform_scale s ON s.code=r.scale_code AND s.version=r.scale_version
            WHERE r.patient_no=%s AND r.scale_code=%s AND r.status='submitted'
            ORDER BY r.created_at, r.id
        """, (patient_no, scale_code))
        rows = cur.fetchall()
        cur.close()
        if not rows:
            return {'ok': True, 'patient_no': patient_no, 'scale_code': scale_code,
                    'rounds': [], 'items': [], 'note': '该患者暂无此量表的填报记录'}, None

        defn = rows[-1][6]
        if isinstance(defn, str):
            try:
                defn = json.loads(defn)
            except ValueError:
                defn = {}
        items = (defn or {}).get('items') or []

        rounds, answers_seq = [], []
        for rid, ans, total, level, created, op, _ in rows:
            if isinstance(ans, str):
                try:
                    ans = json.loads(ans)
                except ValueError:
                    ans = {}
            answers_seq.append(ans)
            rounds.append({'id': rid, 'total': float(total) if total is not None else None,
                           'level': level, 'operator': op,
                           'at': created.strftime('%Y-%m-%d %H:%M') if created else ''})
        for i, r in enumerate(rounds):
            if i == 0 or r['total'] is None or rounds[i - 1]['total'] is None:
                r['delta'] = None
            else:
                r['delta'] = round(r['total'] - rounds[i - 1]['total'], 2)

        matrix = []
        for it in items:
            iid = it.get('id')
            vals = [a.get(iid) for a in answers_seq]
            labels = {}
            for o in (it.get('options') or []):
                labels[o.get('value')] = o.get('label')
            matrix.append({
                'item_id': iid, 'text': it.get('text'),
                'values': vals,
                'labels': [labels.get(v, v) for v in vals],
                # changed[i] = 第 i 次相对第 i-1 次是否变了; 首次恒 False, 前端据此标红
                'changed': [False] + [vals[i] != vals[i - 1] for i in range(1, len(vals))],
                'all_same': len(set(json.dumps(v, ensure_ascii=False, sort_keys=True)
                                    for v in vals)) == 1 and len(vals) > 1,
            })
        changed_counts = [sum(1 for m in matrix if m['changed'][i]) for i in range(len(rounds))]
        for i, r in enumerate(rounds):
            r['changed_items'] = changed_counts[i]
        return {'ok': True, 'patient_no': patient_no, 'scale_code': scale_code,
                'scale_name': (defn or {}).get('name') or scale_code,
                'rounds': rounds, 'items': matrix,
                'total_items': len(matrix)}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


# ============ 随访平台 1.1 M14 (智能 CRF 表单, 方案 §2.1) ============
#
# CRF = Case Report Form, 临床研究的病例报告表。和量表(M10)的区别不是长相而是用途:
# 量表要算分、要划界值, 一份量表的题目动不得(动了信效度就不成立); CRF 只采集,
# 题目本来就该随项目调整。所以两者共用校验代码, 但**不共用表** —— 把"不能改的"
# 和"就是要改的"塞进同一张表, 迟早有人为了改 CRF 而放宽了量表的约束。
#
# 题型按方案 §2.1(1) 原文逐个列出。表格类那 4 种在引擎里其实是同一套机制
# (一张表格 = 若干列 × 若干行, 每列有自己的类型), 差别只在"列允许是什么类型";
# 这里仍然保留 4 个独立的类型名, 因为验收要逐条对方案。
CRF_BASIC_TYPES = {
    'note':      '提示语',      # 只展示不采集, 没有答案
    'text':      '文本填空',
    'paragraph': '段落填空',
    'number':    '数字填空',
    'date':      '日期填空',
    'single':    '单选',
    'multi':     '多选',
    'select':    '下拉选',
}
# 表格题型 -> 该表格的列允许用哪些类型
CRF_TABLE_TYPES = {
    'table_input':    ('输入框表格',   ('text', 'number', 'date')),
    'table_select':   ('选择表格',     ('single', 'multi')),
    'table_dropdown': ('下拉框表格',   ('select',)),
    'table_mixed':    ('列表混搭表格', ('text', 'paragraph', 'number', 'date', 'single', 'multi', 'select')),
}
CRF_ITEM_TYPES = tuple(CRF_BASIC_TYPES) + tuple(CRF_TABLE_TYPES)
CRF_OPTION_TYPES = ('single', 'multi', 'select')     # 必须带 options 的类型
CRF_NO_ANSWER_TYPES = ('note',)                      # 不采集答案, 不参与必填/校验

CRF_LOGIC_ACTIONS = {
    'show':      '显示',
    'hide':      '隐藏',
    'enable':    '启用',
    'disable':   '禁用',
    'require':   '置为必填',
    'optional':  '置为选填',
    'set_value': '自动设值',
    'check':     '逻辑校验',      # 条件成立即报错(强/弱由 severity 定)
    'exclusive': '互斥',          # targets 里最多只能填一个
}
CRF_OPS = ('eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'in', 'contains', 'empty', 'filled')
CRF_SCOPES = ('private', 'shared')


def _crf_items(definition):
    """铺平 items(CRF 允许分节, 节里再放题)。返回 [(section_name, item)]。"""
    out = []
    for sec in (definition or {}).get('sections') or []:
        for it in sec.get('items') or []:
            out.append((sec.get('name') or '', it))
    for it in (definition or {}).get('items') or []:
        out.append(('', it))
    return out


def validate_crf_definition(d):
    """CRF 定义结构校验。返回 errors 列表(空 = 合法)。"""
    errs = []
    if not isinstance(d, dict):
        return ['definition 必须是对象']
    pairs = _crf_items(d)
    if not pairs:
        return ['definition 至少要有一道题 (items 或 sections[].items)']

    seen = set()
    for sec, it in pairs:
        where = '题 {}'.format(it.get('id') or '(缺 id)')
        if not isinstance(it, dict):
            errs.append('items 里有非对象元素'); continue
        iid = it.get('id')
        if not iid:
            errs.append(where + ' 缺 id')
        elif iid in seen:
            errs.append('题 id 重复: ' + str(iid))
        else:
            seen.add(iid)
        t = it.get('type')
        if t not in CRF_ITEM_TYPES:
            errs.append('{} 的 type 必须是 {} 之一, 得到 {!r}'.format(
                where, '/'.join(CRF_ITEM_TYPES), t))
            continue
        if t != 'note' and not it.get('text'):
            errs.append(where + ' 缺题干 text')
        if t in CRF_OPTION_TYPES:
            opts = it.get('options')
            if not isinstance(opts, list) or not opts:
                errs.append(where + ' 选择类题型必须有 options')
            else:
                vals = set()
                for j, o in enumerate(opts):
                    if not isinstance(o, dict) or 'label' not in o or 'value' not in o:
                        errs.append('{} 的 options[{}] 必须含 label 和 value'.format(where, j))
                    elif o['value'] in vals:
                        errs.append('{} 的 options 里 value 重复: {!r}'.format(where, o['value']))
                    else:
                        vals.add(o['value'])
        if t == 'number':
            lo, hi = it.get('min'), it.get('max')
            if lo is not None and hi is not None and lo > hi:
                errs.append('{} 的 min({}) 大于 max({})'.format(where, lo, hi))
        if it.get('format') and it['format'] not in QC_FORMAT_CHECKS:
            errs.append('{} 的 format 必须是 {} 之一'.format(where, '/'.join(QC_FORMAT_CHECKS)))
        if t in CRF_TABLE_TYPES:
            errs += _validate_crf_table(it, where)

    # ---- 逻辑规则 ----
    for i, rule in enumerate((d.get('logic') or [])):
        w = 'logic[{}]'.format(i)
        if not isinstance(rule, dict):
            errs.append(w + ' 必须是对象'); continue
        act = (rule.get('then') or {}).get('action') if rule.get('then') else rule.get('action')
        if act not in CRF_LOGIC_ACTIONS:
            errs.append('{} 的 action 必须是 {} 之一, 得到 {!r}'.format(
                w, '/'.join(CRF_LOGIC_ACTIONS), act))
            continue
        tgts = (rule.get('then') or rule).get('targets') or []
        if not isinstance(tgts, list) or not tgts:
            errs.append(w + ' 缺 targets')
        else:
            for t in tgts:
                if t not in seen:
                    errs.append('{} 指向不存在的题 {!r}'.format(w, t))
        if act == 'exclusive':
            if len(tgts) < 2:
                errs.append(w + ' 互斥至少要指定 2 道题')
            continue
        cond = rule.get('when')
        if cond is None:
            errs.append(w + ' 缺 when 条件')
        else:
            errs += _validate_crf_cond(cond, seen, w)
        if act == 'set_value' and 'value' not in (rule.get('then') or {}):
            errs.append(w + ' 自动设值必须给 then.value')
    return errs


def lint_crf_definition(d):
    """结构合法之外的**用法**问题。返回 advisories 列表, 不阻断保存。

    和 validate 分开是因为这些都不是错误, 是"多半不是你想要的":

    最要紧的一条是 show-在-默认可见的题上。作者写下
        {when: 用药=是, then: show 用药清单}
    通常心里想的是"不满足条件就别显示", 但 show 只会把它设为可见, 而这道题**本来就可见** ——
    规则等于没写, 表单上永远显示用药清单。要真做成条件显示, 那道题必须先声明 hidden:true。
    这类错配不会报任何错, 只会安静地把不该采集的字段一直摆在那儿。
    """
    out = []
    pairs = _crf_items(d or {})
    by_id = {it.get('id'): it for _, it in pairs}
    rules = (d or {}).get('logic') or []

    shown, hidden_by_rule = set(), set()
    for rule in rules:
        then = rule.get('then') or rule
        act = then.get('action') or rule.get('action')
        for t in (then.get('targets') or rule.get('targets') or []):
            if act == 'show':
                shown.add(t)
            elif act == 'hide':
                hidden_by_rule.add(t)
    for t in sorted(shown):
        it = by_id.get(t)
        if it is None:
            continue
        if it.get('hidden') is not True and t not in hidden_by_rule:
            out.append({
                'kind': 'show_without_default_hidden', 'field': t,
                'detail': '题「{}」有 show 规则, 但它默认就是可见的, 也没有任何 hide 规则 —— '
                          '这条 show 等于没写, 该题会一直显示。要做成条件显示, '
                          '请给它加 "hidden": true, 由 show 规则来揭开'.format(
                              str(it.get('text') or t)[:24])})
    # 有 show 也有 hide 的题, 两条规则的条件应当互补, 否则会留下"两边都不成立"的空档,
    # 那时该题落回默认状态, 而作者多半没想过默认状态是什么。
    for t in sorted(shown & hidden_by_rule):
        it = by_id.get(t) or {}
        if it.get('hidden') is not True:
            out.append({
                'kind': 'show_hide_default_visible', 'field': t,
                'detail': '题「{}」同时有 show 和 hide 规则, 但默认可见 —— '
                          '当两条规则的条件都不成立时(比如驱动它的题还没答、或已被隐藏), '
                          '它会落回"显示"。若本意是"没明确要求就不显示", 请加 "hidden": true'.format(
                              str(it.get('text') or t)[:24])})
    for _, it in pairs:
        if it.get('required') and it.get('hidden') is True and it.get('id') not in shown:
            out.append({
                'kind': 'required_but_never_shown', 'field': it.get('id'),
                'detail': '题「{}」既是必填又默认隐藏, 且没有任何 show 规则能揭开它 —— '
                          '它永远不会出现, 那个 required 也就永远不起作用'.format(
                              str(it.get('text') or it.get('id'))[:24])})
    return out


def _validate_crf_table(it, where):
    """表格题的列/行结构校验。"""
    errs = []
    label, allowed = CRF_TABLE_TYPES[it['type']]
    cols = it.get('columns')
    if not isinstance(cols, list) or not cols:
        return [where + ' 表格题必须有 columns']
    cids = set()
    for j, c in enumerate(cols):
        w = '{} 的第 {} 列'.format(where, j + 1)
        if not isinstance(c, dict):
            errs.append(w + ' 必须是对象'); continue
        if not c.get('id'):
            errs.append(w + ' 缺 id')
        elif c['id'] in cids:
            errs.append(w + ' id 重复: ' + str(c['id']))
        else:
            cids.add(c['id'])
        ct = c.get('type')
        if ct not in allowed:
            errs.append('{} 的 type 是 {!r}, 但「{}」只允许 {}'.format(
                w, ct, label, '/'.join(allowed)))
        elif ct in CRF_OPTION_TYPES and not (isinstance(c.get('options'), list) and c['options']):
            errs.append(w + ' 选择类列必须有 options')
    rows = it.get('rows')
    if rows is not None and not isinstance(rows, list):
        errs.append(where + ' rows 必须是数组(固定行的行名), 动态加行请置为 null 并设 dynamic:true')
    if not rows and not it.get('dynamic'):
        errs.append(where + ' 表格既没有固定 rows, 也没标 dynamic:true —— 那这张表格永远是空的')
    return errs


def _validate_crf_cond(cond, ids, where):
    """条件表达式校验(支持 all/any/not 嵌套)。"""
    errs = []
    if not isinstance(cond, dict):
        return [where + ' 的条件必须是对象']
    for key in ('all', 'any'):
        if key in cond:
            if not isinstance(cond[key], list) or not cond[key]:
                errs.append('{} 的 {} 必须是非空数组'.format(where, key))
            else:
                for sub in cond[key]:
                    errs += _validate_crf_cond(sub, ids, where)
            return errs
    if 'not' in cond:
        return _validate_crf_cond(cond['not'], ids, where)
    f = cond.get('field')
    if not f:
        errs.append(where + ' 的条件缺 field')
    elif f not in ids:
        errs.append('{} 的条件引用了不存在的题 {!r}'.format(where, f))
    op = cond.get('op')
    if op not in CRF_OPS:
        errs.append('{} 的条件 op 必须是 {} 之一, 得到 {!r}'.format(where, '/'.join(CRF_OPS), op))
    elif op not in ('empty', 'filled') and 'value' not in cond:
        errs.append('{} 的条件缺 value'.format(where))
    return errs


def _crf_blank(v):
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, (list, dict)) and not v)


def _eval_crf_cond(cond, data, visible):
    """求值一个条件。

    **被隐藏的题, 其答案不参与任何条件求值** —— 这是这块最容易错的地方:
    患者答了 Q2=是 让 Q3 出现, 然后改了 Q1 使 Q2 被隐藏, 如果 Q2 的旧答案还算数,
    Q3 就会一直挂在那儿。临床数据里这叫幽灵数据, 导出后没人看得出哪些该作废。
    """
    if 'all' in cond:
        return all(_eval_crf_cond(c, data, visible) for c in cond['all'])
    if 'any' in cond:
        return any(_eval_crf_cond(c, data, visible) for c in cond['any'])
    if 'not' in cond:
        return not _eval_crf_cond(cond['not'], data, visible)
    f, op = cond.get('field'), cond.get('op')
    v = data.get(f) if visible.get(f, True) else None
    tgt = cond.get('value')
    if op == 'empty':
        return _crf_blank(v)
    if op == 'filled':
        return not _crf_blank(v)
    if _crf_blank(v):
        return False        # 没答的题, 除 empty 外一律不满足
    try:
        if op == 'eq':
            return v == tgt
        if op == 'ne':
            return v != tgt
        if op == 'in':
            return v in (tgt if isinstance(tgt, list) else [tgt])
        if op == 'contains':
            return tgt in v if isinstance(v, (list, str)) else False
        fv, ft = float(v), float(tgt)
        return {'gt': fv > ft, 'gte': fv >= ft, 'lt': fv < ft, 'lte': fv <= ft}[op]
    except (TypeError, ValueError, KeyError):
        return False


def eval_crf_logic(definition, data):
    """跑一遍逻辑规则 (方案 §2.1(2))。返回 state:

      {visible:{id:bool}, enabled:{id:bool}, required:{id:bool},
       auto:{id:value}, violations:[...], stale:[被隐藏但仍有答案的题], passes:n}

    规则会级联(Q1 显示 Q2, Q2 的值再显示 Q3), 所以要迭代到不动点。上限是题数+2 轮:
    真实的级联深度不会超过题数, 超了说明规则互相打架(A 显示 B、B 隐藏 A),
    这时不静默收敛到某一轮的结果, 而是报出来让人去改规则 —— 静默收敛的后果是
    同一份数据在不同浏览器/不同填写顺序下呈现不同的表单。
    """
    data = data or {}
    pairs = _crf_items(definition)
    ids = [it.get('id') for _, it in pairs]
    by_id = {it.get('id'): it for _, it in pairs}
    rules = (definition or {}).get('logic') or []

    def base_state():
        vis, en, req = {}, {}, {}
        for i in ids:
            it = by_id[i]
            vis[i] = it.get('hidden') is not True
            en[i] = it.get('disabled') is not True
            req[i] = bool(it.get('required')) and it.get('type') not in CRF_NO_ANSWER_TYPES
        return vis, en, req

    visible, enabled, required = base_state()
    auto, violations = {}, []
    passes, stable = 0, False
    limit = len(ids) + 2
    while passes < limit:
        passes += 1
        nv, ne, nr = base_state()
        na, nviol = {}, []
        for rule in rules:
            then = rule.get('then') or rule
            act = then.get('action') or rule.get('action')
            tgts = then.get('targets') or rule.get('targets') or []
            if act == 'exclusive':
                filled = [t for t in tgts if visible.get(t, True) and not _crf_blank(data.get(t))]
                if len(filled) > 1:
                    nviol.append({
                        'rule': 'exclusive', 'fields': filled,
                        'severity': rule.get('severity') or 'block',
                        'message': rule.get('message') or '「{}」互斥, 不能同时填写'.format(
                            '」「'.join(str((by_id.get(t) or {}).get('text') or t)[:20] for t in filled))})
                continue
            if not _eval_crf_cond(rule.get('when') or {}, data, visible):
                continue
            for t in tgts:
                if act == 'show':
                    nv[t] = True
                elif act == 'hide':
                    nv[t] = False
                elif act == 'enable':
                    ne[t] = True
                elif act == 'disable':
                    ne[t] = False
                elif act == 'require':
                    nr[t] = True
                elif act == 'optional':
                    nr[t] = False
                elif act == 'set_value':
                    na[t] = then.get('value')
            if act == 'check':
                nviol.append({
                    'rule': 'check', 'fields': list(tgts),
                    'severity': rule.get('severity') or 'warn',
                    'message': rule.get('message') or '逻辑校验未通过'})
        if (nv, ne, nr) == (visible, enabled, required):
            visible, enabled, required, auto, violations = nv, ne, nr, na, nviol
            stable = True
            break
        visible, enabled, required, auto, violations = nv, ne, nr, na, nviol
    if not stable:
        violations.append({
            'rule': 'logic_cycle', 'fields': [], 'severity': 'block',
            'message': '逻辑规则在 {} 轮内没有收敛, 多半是两条规则互相打架'
                       '(比如 A 显示 B、B 又隐藏 A)。请检查规则表 —— 不改的话, '
                       '同一份数据在不同填写顺序下会呈现不同的表单'.format(limit)})
    # 被隐藏却仍带着答案的题 = 幽灵数据。不在这里直接删(那是静默改数据),
    # 交给 submit 决定, 但一定要报出来。
    stale = [i for i in ids if not visible.get(i, True) and not _crf_blank(data.get(i))]
    return {'visible': visible, 'enabled': enabled, 'required': required,
            'auto': auto, 'violations': violations, 'stale': stale, 'passes': passes}


def validate_crf_data(definition, data, state=None):
    """校验一份 CRF 填报。返回 (errors, warnings)。

    强校验(block)进 errors 会挡住提交, 弱校验(warn)进 warnings 只提示 —— 方案 §2.1(2)
    明写要这两档。这里复用 M13 的 QC_FORMAT_CHECKS, 不另起一套格式规则:
    身份证/手机号的判定标准在整个平台里只该有一份。
    """
    data = data or {}
    state = state or eval_crf_logic(definition, data)
    errors, warnings = [], []
    for _, it in _crf_items(definition):
        iid, t = it.get('id'), it.get('type')
        if t in CRF_NO_ANSWER_TYPES:
            continue
        # 隐藏的题一律不校验。不设这条, 一个被逻辑隐藏的必填项会让表单永远提交不了,
        # 而且报错指向的题在界面上根本看不见 —— 使用者完全无从下手。
        if not state['visible'].get(iid, True):
            continue
        v = data.get(iid)
        if _crf_blank(v):
            if state['required'].get(iid):
                errors.append({'field': iid, 'error': '必填项未作答'})
            continue
        if t == 'number':
            try:
                fv = float(v)
            except (TypeError, ValueError):
                errors.append({'field': iid, 'error': '必须是数字'}); continue
            lo, hi = it.get('min'), it.get('max')
            if (lo is not None and fv < lo) or (hi is not None and fv > hi):
                errors.append({'field': iid, 'error': '超出允许范围 {}~{}'.format(
                    _fmt_num(lo) if lo is not None else '-', _fmt_num(hi) if hi is not None else '-')})
        elif t == 'date':
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', str(v)):
                errors.append({'field': iid, 'error': "日期格式应为 YYYY-MM-DD"})
        elif t in ('single', 'select'):
            vals = [o.get('value') for o in (it.get('options') or [])]
            if v not in vals:
                errors.append({'field': iid, 'error': '答案不在选项范围内'})
        elif t == 'multi':
            if not isinstance(v, list):
                errors.append({'field': iid, 'error': '多选题答案必须是数组'})
            else:
                vals = [o.get('value') for o in (it.get('options') or [])]
                bad = [x for x in v if x not in vals]
                if bad:
                    errors.append({'field': iid, 'error': '含不在选项范围内的答案: {}'.format(bad[:3])})
        elif t in CRF_TABLE_TYPES:
            errors += _validate_crf_table_data(it, v)
        if it.get('format') and isinstance(v, str):
            msg = QC_FORMAT_CHECKS[it['format']](v)
            if msg:
                errors.append({'field': iid, 'error': msg})
        if it.get('maxlength') and isinstance(v, str) and len(v) > int(it['maxlength']):
            errors.append({'field': iid, 'error': '超过 {} 字'.format(it['maxlength'])})

    for vi in state['violations']:
        (errors if vi.get('severity') == 'block' else warnings).append(
            {'field': (vi.get('fields') or [None])[0], 'error': vi['message'], 'rule': vi['rule']})
    for iid in state['stale']:
        warnings.append({'field': iid, 'rule': 'stale_hidden',
                         'error': '该题已被逻辑隐藏但仍留有答案 —— 提交时会连同隐藏原因一起归档, '
                                  '不会计入本次数据'})
    return errors, warnings


def _validate_crf_table_data(it, v):
    """表格题答案校验。答案形如 [{列id: 值}, ...], 一个元素一行。"""
    iid = it.get('id')
    if not isinstance(v, list):
        return [{'field': iid, 'error': '表格题答案必须是数组(每个元素一行)'}]
    cols = {c['id']: c for c in (it.get('columns') or []) if c.get('id')}
    rows = it.get('rows')
    if rows and len(v) != len(rows):
        return [{'field': iid, 'error': '固定行表格应有 {} 行, 收到 {} 行'.format(len(rows), len(v))}]
    if it.get('dynamic') and it.get('max_rows') and len(v) > int(it['max_rows']):
        return [{'field': iid, 'error': '最多 {} 行'.format(it['max_rows'])}]
    errs = []
    for ri, row in enumerate(v):
        if not isinstance(row, dict):
            errs.append({'field': iid, 'error': '第 {} 行必须是对象'.format(ri + 1)}); continue
        for cid, cv in row.items():
            c = cols.get(cid)
            if c is None:
                errs.append({'field': iid, 'error': '第 {} 行有未定义的列 {!r}'.format(ri + 1, cid)})
                continue
            if _crf_blank(cv):
                if c.get('required'):
                    errs.append({'field': iid, 'error': '第 {} 行「{}」必填'.format(
                        ri + 1, c.get('label') or cid)})
                continue
            ct = c.get('type')
            if ct == 'number':
                try:
                    fv = float(cv)
                except (TypeError, ValueError):
                    errs.append({'field': iid, 'error': '第 {} 行「{}」必须是数字'.format(
                        ri + 1, c.get('label') or cid)}); continue
                lo, hi = c.get('min'), c.get('max')
                if (lo is not None and fv < lo) or (hi is not None and fv > hi):
                    errs.append({'field': iid, 'error': '第 {} 行「{}」超出范围 {}~{}'.format(
                        ri + 1, c.get('label') or cid,
                        _fmt_num(lo) if lo is not None else '-',
                        _fmt_num(hi) if hi is not None else '-')})
            elif ct == 'date' and not re.match(r'^\d{4}-\d{2}-\d{2}$', str(cv)):
                errs.append({'field': iid, 'error': '第 {} 行「{}」日期格式应为 YYYY-MM-DD'.format(
                    ri + 1, c.get('label') or cid)})
            elif ct in ('single', 'select'):
                vals = [o.get('value') for o in (c.get('options') or [])]
                if cv not in vals:
                    errs.append({'field': iid, 'error': '第 {} 行「{}」答案不在选项范围内'.format(
                        ri + 1, c.get('label') or cid)})
            elif ct == 'multi' and not isinstance(cv, list):
                errs.append({'field': iid, 'error': '第 {} 行「{}」多选答案必须是数组'.format(
                    ri + 1, c.get('label') or cid)})
    return errs


# ---- 版本管理: 方案 §2.1(3) "支持项目开展中增改变量、调整顺序, 且修改不影响已有数据" ----
#
# "不影响已有数据" 的实现不是"不让改", 而是**分清哪些改动会让旧数据变得读不懂**:
#
#   删掉一道题     -> 旧记录里那道题的答案成了没有归属的孤儿值        -> 破坏性
#   改题型         -> 旧答案的数据形态对不上新定义(单选值 vs 数组)    -> 破坏性
#   删/改选项分值  -> 旧记录指向一个不再存在的选项, 导出时无法翻译     -> 破坏性
#   收紧取值范围   -> 旧记录里合法的值现在成了非法值                  -> 破坏性
#   新增题         -> 旧记录只是缺这一项, 是正常的缺失, 不是损坏      -> 安全
#   改题干措辞     -> 答案含义不变                                    -> 安全
#   调整顺序       -> 数据按 id 存, 与顺序无关                        -> 安全
#   放宽取值范围   -> 旧值仍然合法                                    -> 安全
#
# 破坏性改动一律**开新版本**, 旧填报仍钉在旧版本上, 两边都完整可读。
# 安全改动就地改, 免得改个错别字也涨一个版本、把版本号变成噪音。
def classify_crf_change(old_def, new_def):
    """比对两版 CRF 定义。返回 {breaking:[...], safe:[...], verdict:'in_place'|'new_version'}"""
    breaking, safe = [], []
    old_items = {it.get('id'): it for _, it in _crf_items(old_def or {})}
    new_items = {it.get('id'): it for _, it in _crf_items(new_def or {})}

    for iid, oit in old_items.items():
        nit = new_items.get(iid)
        if nit is None:
            breaking.append({'field': iid, 'kind': 'item_removed',
                             'detail': '删除了题「{}」—— 已填报记录里这道题的答案会成为无归属的孤儿值'.format(
                                 str(oit.get('text') or iid)[:30])})
            continue
        if oit.get('type') != nit.get('type'):
            breaking.append({'field': iid, 'kind': 'type_changed',
                             'detail': '题「{}」的类型由 {} 改为 {} —— 旧答案的数据形态对不上新定义'.format(
                                 str(oit.get('text') or iid)[:20], oit.get('type'), nit.get('type'))})
            continue
        if oit.get('type') in CRF_OPTION_TYPES:
            ov = set(json.dumps(o.get('value'), sort_keys=True) for o in (oit.get('options') or []))
            nv = set(json.dumps(o.get('value'), sort_keys=True) for o in (nit.get('options') or []))
            gone = ov - nv
            if gone:
                breaking.append({'field': iid, 'kind': 'option_removed',
                                 'detail': '题「{}」删掉了 {} 个选项值 —— 已选过这些选项的记录将指向不存在的选项'.format(
                                     str(oit.get('text') or iid)[:20], len(gone))})
            elif nv - ov:
                safe.append({'field': iid, 'kind': 'option_added',
                             'detail': '题「{}」新增了 {} 个选项'.format(
                                 str(oit.get('text') or iid)[:20], len(nv - ov))})
        if oit.get('type') == 'number':
            for key, tighter in (('min', lambda o, n: n > o), ('max', lambda o, n: n < o)):
                ov, nv = oit.get(key), nit.get(key)
                if nv is None:
                    if ov is not None:
                        safe.append({'field': iid, 'kind': key + '_relaxed',
                                     'detail': '题「{}」去掉了 {} 限制'.format(str(oit.get('text') or iid)[:20], key)})
                elif ov is None or tighter(ov, nv):
                    breaking.append({'field': iid, 'kind': key + '_tightened',
                                     'detail': '题「{}」把 {} 收紧为 {} —— 已填报中原本合法的值可能变成非法'.format(
                                         str(oit.get('text') or iid)[:20], key, _fmt_num(nv))})
                elif ov != nv:
                    safe.append({'field': iid, 'kind': key + '_relaxed',
                                 'detail': '题「{}」把 {} 放宽为 {}'.format(
                                     str(oit.get('text') or iid)[:20], key, _fmt_num(nv))})
        if oit.get('type') in CRF_TABLE_TYPES:
            oc = {c.get('id') for c in (oit.get('columns') or [])}
            nc = {c.get('id') for c in (nit.get('columns') or [])}
            if oc - nc:
                breaking.append({'field': iid, 'kind': 'column_removed',
                                 'detail': '表格题「{}」删掉了 {} 列 —— 旧记录里这些列的值会失去列定义'.format(
                                     str(oit.get('text') or iid)[:20], len(oc - nc))})
            elif nc - oc:
                safe.append({'field': iid, 'kind': 'column_added',
                             'detail': '表格题「{}」新增了 {} 列'.format(
                                 str(oit.get('text') or iid)[:20], len(nc - oc))})
        if oit.get('text') != nit.get('text'):
            safe.append({'field': iid, 'kind': 'text_changed',
                         'detail': '改了题「{}」的措辞(答案含义不变)'.format(iid)})

    for iid, nit in new_items.items():
        if iid not in old_items:
            safe.append({'field': iid, 'kind': 'item_added',
                         'detail': '新增题「{}」{} —— 已有记录只是缺这一项, 属正常缺失'.format(
                             str(nit.get('text') or iid)[:30],
                             '(必填, 已有记录会显示为不完整)' if nit.get('required') else '')})

    old_order = [i for i in old_items if i in new_items]
    new_order = [i for i in new_items if i in old_items]
    if old_order != new_order:
        safe.append({'field': None, 'kind': 'reordered',
                     'detail': '调整了题目顺序(数据按 id 存, 与顺序无关)'})
    if json.dumps((old_def or {}).get('logic') or [], sort_keys=True, ensure_ascii=False) != \
       json.dumps((new_def or {}).get('logic') or [], sort_keys=True, ensure_ascii=False):
        safe.append({'field': None, 'kind': 'logic_changed',
                     'detail': '改了逻辑规则 —— 只影响今后的填写过程, 不改变已存数据的含义'})

    return {'breaking': breaking, 'safe': safe,
            'verdict': 'new_version' if breaking else 'in_place'}


def _bump_version(v):
    """'1' -> '2'; 'v1.2' -> 'v1.3'; 认不出数字就在后面挂 -2。"""
    m = re.search(r'(\d+)(?!.*\d)', str(v or '1'))
    if not m:
        return str(v) + '-2'
    return str(v)[:m.start()] + str(int(m.group(1)) + 1) + str(v)[m.end():]


def ensure_platform_vital_daily():
    """M16: 体征日聚合表 (idempotent)。

    这张表不是新数据源 —— M7 摄入时本来就在内存里算出了
    {患者: {指标: {日期: 均值}}}, 只是算完就扔了。落到表里有两个用处:
      · §4.6(2) 的"按指标阈值检索受试者"才有得查。体征原始数据在
        wearable_device_data 的大 JSON 里, 没法直接进 WHERE 子句。
      · 患者详情页的体征曲线不用每次全表扫。
    因此它是**派生表**: 删了不丢数据, 下次 ingest 会重建。
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_vital_daily (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                patient_no VARCHAR(64) NOT NULL,
                metric VARCHAR(16) NOT NULL COMMENT 'hr/spo2/sbp/dbp/temp/sleep',
                day DATE NOT NULL,
                value DECIMAL(10,2) NOT NULL COMMENT '当日均值',
                samples INT NOT NULL DEFAULT 0 COMMENT '当日采样点数, 少于阈值的日子判定时会被剔除',
                device_id VARCHAR(32) DEFAULT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uk_vital_daily (patient_no, metric, day),
                INDEX idx_metric_day (metric, day)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
              COMMENT='随访平台 M16 体征日聚合(派生表, 由 M7 摄入重建)'
        """)
        print('[启动] platform_vital_daily 表已就绪')
        cur.close()
    except Exception as e:
        print('[启动] ensure_platform_vital_daily 失败:', e)
    finally:
        conn.close()


def _persist_vital_daily(daily):
    """把 M7 算出的日聚合写进派生表。返回写入行数。

    整段包在 try 里: 这是顺带产出的派生数据, 写失败不该让报警摄入整个失败 ——
    报警是有人要看的, 聚合表下一轮还会重建。
    """
    if not daily:
        return 0
    rows = []
    for p_no, metrics in daily.items():
        for metric, by_date in metrics.items():
            for d, agg in by_date.items():
                if not agg.get('n'):
                    continue
                rows.append((p_no, metric, d, round(agg['sum'] / agg['n'], 2),
                             agg['n'], agg.get('device_id')))
    if not rows:
        return 0
    try:
        ensure_platform_vital_daily()
        conn = get_connection()
        try:
            cur = conn.cursor()
            for i in range(0, len(rows), 500):
                cur.executemany("""
                    INSERT INTO platform_vital_daily (patient_no, metric, day, value, samples, device_id)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE value=VALUES(value), samples=VALUES(samples),
                                            device_id=VALUES(device_id)
                """, rows[i:i + 500])
            cur.close()
            return len(rows)
        finally:
            conn.close()
    except Exception as e:
        print('[M16] 体征日聚合写入失败(不影响报警摄入):', e)
        return 0


def ensure_platform_crf_tables():
    """M14: CRF 定义表 + 填报表 (idempotent)。"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_crf (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                code VARCHAR(64) NOT NULL,
                name VARCHAR(128) NOT NULL,
                category VARCHAR(64) DEFAULT NULL COMMENT '病种/科室',
                visit_type VARCHAR(32) DEFAULT NULL COMMENT '初诊/随诊/结局...',
                version VARCHAR(32) NOT NULL DEFAULT '1',
                scope ENUM('private','shared') DEFAULT 'private' COMMENT '§2.1(3) 私有/院内共享',
                owner VARCHAR(64) DEFAULT NULL COMMENT '创建者; 私有 CRF 仅其可改',
                source VARCHAR(32) DEFAULT 'manual' COMMENT 'manual/ai/excel/copy',
                copied_from VARCHAR(191) DEFAULT NULL COMMENT '拷贝自 code@version',
                definition JSON NOT NULL COMMENT '分节 + 题目 + 逻辑规则',
                item_count INT DEFAULT NULL COMMENT '写入时算好的题数(含分节内的题)。
                    不在查询时用 JSON_LENGTH 算: 那只数得到顶层 $.items, 分节的题一律漏掉,
                    列表页会显示 "--"。这个数在写入时是已知的, 存下来最省事也最准。',
                media JSON DEFAULT NULL COMMENT '§2.1(3) 音视频指导文件 [{name,url,type}]',
                status ENUM('draft','active','archived') DEFAULT 'draft',
                active TINYINT(1) DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uk_crf_code_version (code, version),
                INDEX idx_scope (scope),
                INDEX idx_category (category),
                INDEX idx_status (status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M14 CRF 定义 (破坏性改动开新版, 旧填报钉旧版)'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_crf_response (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                crf_code VARCHAR(64) NOT NULL,
                crf_version VARCHAR(32) NOT NULL COMMENT '钉住填报时的版本, 后来改 CRF 不影响本条',
                patient_no VARCHAR(64) NOT NULL,
                plan_id BIGINT DEFAULT NULL,
                visit_name VARCHAR(64) DEFAULT NULL COMMENT '访视节点名',
                data JSON NOT NULL,
                hidden_data JSON DEFAULT NULL COMMENT '提交时被逻辑隐藏的题的残留答案, 归档不计入',
                operator VARCHAR(64) DEFAULT NULL,
                status ENUM('submitted','superseded') DEFAULT 'submitted',
                revision_of BIGINT DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_patient (patient_no, created_at),
                INDEX idx_crf (crf_code, crf_version),
                INDEX idx_status (status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M14 CRF 填报 (修订走新增+旧版标 superseded)'
        """)
        # 老库补 item_count 列 (本列 2026-08-02 才加, 此前建的表没有)
        cur.execute("SHOW COLUMNS FROM platform_crf LIKE 'item_count'")
        if not cur.fetchone():
            cur.execute("ALTER TABLE platform_crf ADD COLUMN item_count INT DEFAULT NULL "
                        "COMMENT '写入时算好的题数(含分节内的题)'")
            print('[启动] platform_crf 补列 item_count')
        print('[启动] platform_crf / platform_crf_response 表已就绪')
        cur.close()
    except Exception as e:
        print('[启动] ensure_platform_crf_tables 失败:', e)
    finally:
        conn.close()


def upsert_platform_crf(body):
    """建/改 CRF。破坏性改动自动开新版 (方案 §2.1(3))。

    {code, name, definition, category?, visit_type?, scope?, owner?, version?,
     status?, media?, source?, force_version?}

    改一份**已有填报**的 CRF 时:
      - 只有安全改动 -> 就地改, 版本号不变
      - 有破坏性改动 -> 自动开新版, 旧版原样留着, 旧填报继续钉在旧版上
    没有填报的 CRF 怎么改都就地改 —— 没有数据要保护, 涨版本号只是噪音。
    """
    code = str(body.get('code') or '').strip()
    name = str(body.get('name') or '').strip()
    if not code or not name:
        return None, 'code 和 name 必填'
    scope = body.get('scope') or 'private'
    if scope not in CRF_SCOPES:
        return None, 'scope 必须是 private 或 shared'
    definition = body.get('definition')
    if isinstance(definition, str):
        try:
            definition = json.loads(definition)
        except ValueError:
            return None, 'definition 不是合法 JSON'
    errs = validate_crf_definition(definition)
    if errs:
        return None, 'CRF 定义有 {} 处问题: {}'.format(len(errs), '; '.join(errs[:6]))

    ensure_platform_crf_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        version = str(body.get('version') or '').strip()
        if version:
            cur.execute('SELECT definition, owner FROM platform_crf WHERE code=%s AND version=%s',
                        (code, version))
        else:
            cur.execute('SELECT definition, owner, version FROM platform_crf WHERE code=%s '
                        'ORDER BY updated_at DESC LIMIT 1', (code,))
        row = cur.fetchone()

        change, note = None, None
        if row:
            old_def = row[0]
            if isinstance(old_def, str):
                old_def = json.loads(old_def)
            if not version:
                version = row[2]
            cur.execute("SELECT COUNT(*) FROM platform_crf_response WHERE crf_code=%s AND crf_version=%s",
                        (code, version))
            used = cur.fetchone()[0]
            change = classify_crf_change(old_def, definition)
            if change['breaking'] and used:
                new_version = str(body.get('force_version') or _bump_version(version))
                note = ('检测到 {} 处破坏性改动, 而该版本已有 {} 份填报 —— 已自动开新版 {} (原 {} 保持不变, '
                        '旧填报仍钉在旧版上, 两边都完整可读)').format(
                            len(change['breaking']), used, new_version, version)
                version = new_version
            elif change['breaking']:
                note = '有 {} 处破坏性改动, 但该版本还没有任何填报, 就地修改'.format(len(change['breaking']))
        else:
            version = version or '1'

        cur.execute("""
            INSERT INTO platform_crf (code, name, category, visit_type, version, scope, owner,
                                      source, copied_from, definition, item_count, media, status, active)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              name=VALUES(name), category=VALUES(category), visit_type=VALUES(visit_type),
              scope=VALUES(scope), owner=VALUES(owner), source=VALUES(source),
              definition=VALUES(definition), item_count=VALUES(item_count),
              media=VALUES(media), status=VALUES(status), active=VALUES(active)
        """, (code, name, body.get('category') or None, body.get('visit_type') or None,
              version, scope, body.get('owner') or None, body.get('source') or 'manual',
              body.get('copied_from') or None,
              json.dumps(definition, ensure_ascii=False),
              len(_crf_items(definition)),
              json.dumps(body.get('media') or [], ensure_ascii=False),
              body.get('status') or 'draft',
              0 if body.get('active') in (0, False, '0') else 1))
        cur.close()
        return {'code': code, 'version': version,
                'items': len(_crf_items(definition)),
                'change': change, 'note': note,
                'advisories': lint_crf_definition(definition)}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def query_platform_crfs(code=None, version=None, scope=None, category=None,
                        owner=None, all_versions=False, with_definition=False, limit=200):
    """CRF 列表 / 单份定义。默认每个 code 只给最新一版。"""
    ensure_platform_crf_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ['1=1'], []
        if code:
            where.append('c.code=%s'); params.append(code)
        if version:
            where.append('c.version=%s'); params.append(version)
        if scope:
            where.append('c.scope=%s'); params.append(scope)
        if category:
            where.append('c.category=%s'); params.append(category)
        if owner:
            where.append('c.owner=%s'); params.append(owner)
        if not (all_versions or version):
            # 每个 code 只留最新一版 —— 列表页给人看的是"有哪些表", 不是"有哪些版本"
            where.append("c.updated_at = (SELECT MAX(x.updated_at) FROM platform_crf x WHERE x.code=c.code)")
        cols = ("c.id, c.code, c.name, c.category, c.visit_type, c.version, c.scope, c.owner, "
                "c.source, c.copied_from, c.media, c.status, c.active, c.created_at, c.updated_at, "
                "c.item_count, "
                "(SELECT COUNT(*) FROM platform_crf_response r WHERE r.crf_code=c.code AND r.crf_version=c.version) AS response_count, "
                "(SELECT COUNT(*) FROM platform_crf x WHERE x.code=c.code) AS version_count")
        if with_definition or code:
            cols += ', c.definition'
        params.append(int(limit))
        cur.execute('SELECT {} FROM platform_crf c WHERE {} ORDER BY c.category, c.code, c.updated_at DESC '
                    'LIMIT %s'.format(cols, ' AND '.join(where)), params)
        names = [d[0] for d in cur.description]
        out = []
        for row in cur.fetchall():
            r = dict(zip(names, row))
            for k in ('created_at', 'updated_at'):
                if r.get(k) is not None and hasattr(r[k], 'strftime'):
                    r[k] = r[k].strftime('%Y-%m-%d %H:%M:%S')
            for k in ('media', 'definition'):
                if isinstance(r.get(k), str):
                    try:
                        r[k] = json.loads(r[k])
                    except ValueError:
                        pass
            # item_count 是写入时存下的。老库里可能还是 NULL(补列之前建的行),
            # 这时若手头有 definition 就现算一个, 免得列表页显示 "--"。
            if r.get('item_count') is None and r.get('definition'):
                r['item_count'] = len(_crf_items(r['definition']))
            out.append(r)
        cur.close()
        return {'ok': True, 'count': len(out), 'crfs': out,
                'item_types': dict(CRF_BASIC_TYPES,
                                   **{k: v[0] for k, v in CRF_TABLE_TYPES.items()})}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def copy_platform_crf(body):
    """拷贝一份 CRF (方案 §2.1(3) "支持 CRF 拷贝")。

    拷贝出来的是**新 code 的第 1 版**, 不是原表的新版本 —— 拷贝的意图是"以此为底
    另做一张表", 如果做成新版本, 改动就会牵连原表已有的填报。
    """
    src = str(body.get('code') or '').strip()
    new_code = str(body.get('new_code') or '').strip()
    if not src or not new_code:
        return None, 'code(源) 和 new_code(新) 必填'
    if src == new_code:
        return None, 'new_code 不能与源相同 —— 拷贝要生成一张独立的表'
    ensure_platform_crf_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        if body.get('version'):
            cur.execute('SELECT name, category, visit_type, version, definition, media '
                        'FROM platform_crf WHERE code=%s AND version=%s', (src, str(body['version'])))
        else:
            cur.execute('SELECT name, category, visit_type, version, definition, media '
                        'FROM platform_crf WHERE code=%s ORDER BY updated_at DESC LIMIT 1', (src,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, '源 CRF 不存在: {}'.format(src)
        cur.execute('SELECT 1 FROM platform_crf WHERE code=%s LIMIT 1', (new_code,))
        if cur.fetchone():
            cur.close()
            return None, 'new_code 已存在: {}'.format(new_code)
        name, category, visit_type, ver, defn, media = row
        cur.close()
        return upsert_platform_crf({
            'code': new_code, 'name': body.get('new_name') or (name + ' (副本)'),
            'category': category, 'visit_type': visit_type, 'version': '1',
            'scope': body.get('scope') or 'private', 'owner': body.get('owner'),
            'source': 'copy', 'copied_from': '{}@{}'.format(src, ver),
            'definition': json.loads(defn) if isinstance(defn, str) else defn,
            'media': json.loads(media) if isinstance(media, str) else media,
            'status': 'draft'})
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def submit_crf_response(body):
    """提交一份 CRF 填报。

    {crf_code, crf_version?, patient_no, data{}, visit_name?, plan_id?, operator?,
     revision_of?, allow_warnings?}

    强校验不过直接拒收。被逻辑隐藏却仍带答案的题, 其值挪进 hidden_data 单独归档 ——
    既不静默丢弃(那是偷偷改数据), 也不混进正式数据(那是把作废的答案当成有效填报)。
    """
    code = str(body.get('crf_code') or body.get('code') or '').strip()
    patient_no = str(body.get('patient_no') or '').strip()
    data = body.get('data')
    if not code or not patient_no:
        return None, 'crf_code 和 patient_no 必填'
    if not isinstance(data, dict):
        return None, 'data 必须是对象 {题目id: 答案}'

    ensure_platform_crf_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        version = body.get('crf_version') or body.get('version')
        if version:
            cur.execute('SELECT version, definition FROM platform_crf WHERE code=%s AND version=%s',
                        (code, str(version)))
        else:
            cur.execute('SELECT version, definition FROM platform_crf WHERE code=%s AND active=1 '
                        'ORDER BY updated_at DESC LIMIT 1', (code,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, 'CRF 不存在或已停用: {}'.format(code)
        version, definition = row[0], row[1]
        if isinstance(definition, str):
            definition = json.loads(definition)

        state = eval_crf_logic(definition, data)
        errors, warnings = validate_crf_data(definition, data, state)
        if errors:
            cur.close()
            return {'ok': False, 'accepted': False, 'errors': errors,
                    'warnings': warnings, 'state': state}, None
        if warnings and not body.get('allow_warnings'):
            cur.close()
            return {'ok': False, 'accepted': False, 'errors': [],
                    'warnings': warnings, 'state': state,
                    'hint': '有 {} 条弱校验提示。确认无误后带 allow_warnings=true 再提交'.format(len(warnings))}, None

        # 自动设值的题, 以规则算出来的值为准 —— 否则客户端传什么就存什么, 自动设值形同虚设
        clean = {k: v for k, v in data.items() if state['visible'].get(k, True)}
        clean.update(state['auto'])
        hidden = {k: data[k] for k in state['stale']}

        revision_of = body.get('revision_of')
        if revision_of is not None:
            try:
                revision_of = int(revision_of)
            except (TypeError, ValueError):
                cur.close()
                return None, 'revision_of 必须是整数'
            cur.execute('SELECT id FROM platform_crf_response WHERE id=%s', (revision_of,))
            if not cur.fetchone():
                cur.close()
                return None, '被修订的记录不存在: {}'.format(revision_of)

        cur.execute("""
            INSERT INTO platform_crf_response
              (crf_code, crf_version, patient_no, plan_id, visit_name, data, hidden_data,
               operator, status, revision_of)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'submitted',%s)
        """, (code, version, patient_no, body.get('plan_id'), body.get('visit_name') or None,
              json.dumps(clean, ensure_ascii=False),
              json.dumps(hidden, ensure_ascii=False) if hidden else None,
              body.get('operator') or None, revision_of))
        new_id = cur.lastrowid
        if revision_of is not None:
            cur.execute("UPDATE platform_crf_response SET status='superseded' WHERE id=%s", (revision_of,))
        cur.close()
        return {'ok': True, 'accepted': True, 'id': new_id, 'crf_code': code,
                'crf_version': version, 'saved_fields': len(clean),
                'archived_hidden': len(hidden), 'warnings': warnings}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


# ---- §2.1(1) AI 辅助 CRF 生成 ----
#
# 和 M12 生成量表的关键差别: CRF **可以**给出完整可用的草稿, 量表不行。
# 量表的划界值是实证结果, 编不出来; 而 CRF 只是采集表, 「记录用药名称」这道题
# 没有对错之分 —— 编出来的题目最多是不合用, 不会产生看似正常的错误结论。
# 所以这里给的是真题干而不是占位符, 但仍然是**草稿**, 仍然要人过一遍。
CRF_BASE_SECTIONS = [
    ('基本信息', [
        ('patient_no', '门诊号', 'text', {'required': True}),
        ('visit_date', '访视日期', 'date', {'required': True}),
        ('age', '年龄', 'number', {'min': 0, 'max': 130}),
        ('gender', '性别', 'single', {'options': [('男', 1), ('女', 2)]}),
        ('phone', '联系电话', 'text', {'format': 'phone'}),
    ]),
    ('本次访视', [
        ('visit_type', '访视类型', 'select',
         {'options': [('初诊', 1), ('常规随诊', 2), ('计划外随访', 3)], 'required': True}),
        ('chief_complaint', '主诉', 'paragraph', {}),
    ]),
    ('用药情况', [
        ('on_med', '目前是否在用药', 'single',
         {'options': [('是', 1), ('否', 0)], 'required': True}),
        ('med_table', '用药清单', 'table_mixed', {
            'dynamic': True, 'max_rows': 20,
            'columns': [('drug', '药品名称', 'text', None), ('dose', '剂量', 'text', None),
                        ('freq', '频次', 'select', [('每日一次', 1), ('每日两次', 2), ('每日三次', 3), ('按需', 9)]),
                        ('adherence', '依从性', 'single', [('规律服用', 2), ('偶有漏服', 1), ('经常漏服', 0)])]}),
    ]),
    ('不良事件', [
        ('has_ae', '本次随访期间是否发生不良事件', 'single',
         {'options': [('是', 1), ('否', 0)], 'required': True}),
        ('ae_desc', '不良事件描述', 'paragraph', {}),
        ('ae_severity', '严重程度', 'select',
         {'options': [('轻度', 1), ('中度', 2), ('重度', 3), ('严重不良事件', 4)]}),
    ]),
]


def _mk_crf_item(iid, text, itype, extra):
    it = {'id': iid, 'text': text, 'type': itype}
    extra = extra or {}
    if extra.get('required'):
        it['required'] = True
    for k in ('min', 'max', 'format', 'maxlength', 'dynamic', 'max_rows'):
        if extra.get(k) is not None:
            it[k] = extra[k]
    if extra.get('options'):
        it['options'] = [{'label': l, 'value': v} for l, v in extra['options']]
    if extra.get('columns'):
        it['columns'] = [dict({'id': cid, 'label': cl, 'type': ct},
                              **({'options': [{'label': l, 'value': v} for l, v in copts]} if copts else {}))
                         for cid, cl, ct, copts in extra['columns']]
    return it


def generate_crf_draft(spec):
    """§2.1(1): 按病种/访视/采集需求生成 CRF 草稿。返回 (draft, report)。

    后端可插拔, 与 M12 同一惯例: 默认本地模板, 配了 SCALE_LLM_PROVIDER=claude 且装了
    SDK 才走大模型, 否则带原因回落模板而不是报错。
    """
    notes = []
    disease = str(spec.get('disease') or '').strip()
    visit = str(spec.get('visit_type') or '随诊').strip()
    fields = [str(x).strip() for x in (spec.get('fields') or []) if str(x).strip()]
    backend = (spec.get('backend') or os.environ.get('SCALE_LLM_PROVIDER') or 'template').lower()

    draft, err = (None, None)
    if backend == 'claude':
        draft, err = _generate_via_claude({
            'goal': 'CRF: {} {}'.format(disease, visit), 'dimensions': fields}, notes)
        if err:
            notes.append({'step': 'backend_fallback', 'confidence': 'high',
                          'detail': '大模型后端不可用({}), 已回落本地模板'.format(err)})
            draft = None

    sections = []
    for sec_name, items in CRF_BASE_SECTIONS:
        sections.append({'name': sec_name,
                         'items': [_mk_crf_item(*it) for it in items]})
    notes.append({'step': 'base_template', 'confidence': 'high',
                  'detail': '生成 {} 个基础章节({}), 覆盖 CRF 的通用骨架'.format(
                      len(sections), '、'.join(s['name'] for s in sections))})

    # 用户点名的采集字段单独成节。类型靠字段名里的线索猜, 猜不出一律给文本 ——
    # 猜错成日期/数字会让人填不进去, 给文本至少填得进去, 事后改类型也是安全改动。
    if fields:
        extra = []
        for i, f in enumerate(fields):
            if re.search(r'(日期|时间|date)', f):
                t, ex = 'date', {}
            elif re.search(r'(次数|数量|年龄|身高|体重|剂量|评分|值|计数|水平|浓度)', f):
                t, ex = 'number', {}
            elif re.search(r'(是否|有无)', f):
                t, ex = 'single', {'options': [('是', 1), ('否', 0)]}
            elif re.search(r'(描述|说明|备注|小结|情况)', f):
                t, ex = 'paragraph', {}
            else:
                t, ex = 'text', {}
            extra.append(_mk_crf_item('f{}'.format(i + 1), f, t, ex))
        sections.append({'name': '{}专项采集'.format(disease or '项目'), 'items': extra})
        notes.append({'step': 'custom_fields', 'confidence': 'low',
                      'detail': '按需求生成 {} 个专项字段。题型是按字段名里的关键词猜的'
                                '(含"日期"→日期题、含"是否"→是否题…), 猜不准的一律给了文本题 —— '
                                '请逐个核对; 事后改类型属破坏性改动, 有填报后会开新版'.format(len(extra))})

    # 逻辑规则: 只生成能从题目语义确定推出来的那几条, 不臆造
    logic = [
        {'when': {'field': 'on_med', 'op': 'eq', 'value': 0},
         'then': {'action': 'hide', 'targets': ['med_table']}},
        {'when': {'field': 'has_ae', 'op': 'eq', 'value': 0},
         'then': {'action': 'hide', 'targets': ['ae_desc', 'ae_severity']}},
        {'when': {'field': 'has_ae', 'op': 'eq', 'value': 1},
         'then': {'action': 'require', 'targets': ['ae_desc', 'ae_severity']}},
    ]
    notes.append({'step': 'logic', 'confidence': 'medium',
                  'detail': '生成 {} 条逻辑规则: 没在用药就隐藏用药清单; 没有不良事件就隐藏'
                            '描述与严重程度, 有则置为必填。被隐藏的题不参与必填校验, '
                            '也不会把残留答案计入正式数据'.format(len(logic))})

    definition = {'title': '{}{} CRF'.format(disease or '通用', visit),
                  'sections': sections, 'logic': logic}
    if draft and isinstance(draft.get('definition'), dict):
        notes.append({'step': 'llm_merge', 'confidence': 'low',
                      'detail': '大模型产出已作为参考并入草稿, 题干与题型仍须逐条核对'})

    # 不用内置 hash(): Python 的字符串 hash 每个进程都重新加盐, 同样的病种+访视
    # 在服务重启前后会算出不同的 code, 而这是个默认值, 使用者不会想到它会变。
    # md5 只是拿来当稳定摘要, 不涉及任何安全用途。
    import hashlib
    code = re.sub(r'[^A-Za-z0-9]', '', (spec.get('code') or '')) or \
        'CRF{}'.format(hashlib.md5((disease + '|' + visit).encode('utf-8')).hexdigest()[:6].upper())
    out = {'code': code, 'name': definition['title'], 'category': disease or None,
           'visit_type': visit, 'scope': 'private', 'source': 'ai',
           'definition': definition}
    report = {'backend': backend, 'item_count': len(_crf_items(definition)),
              'section_count': len(sections), 'logic_count': len(logic),
              'notes': notes, 'validation': validate_crf_definition(definition),
              'advisories': lint_crf_definition(definition), 'needs_review': True}
    return out, report


# ---- §2.1(4) Excel 辅助建表 ----
_EXCEL_TYPE_HINTS = [
    (r'(单选|radio)', 'single'), (r'(多选|checkbox)', 'multi'),
    (r'(下拉|select|dropdown)', 'select'), (r'(日期|date)', 'date'),
    (r'(数值|数字|number|int|float)', 'number'),
    (r'(段落|长文本|textarea|paragraph)', 'paragraph'),
    (r'(文本|填空|text|string)', 'text'), (r'(提示|说明|note)', 'note'),
]


def parse_excel_to_crf(xlsx_bytes, code=None, name=None):
    """§2.1(4): Excel 模板 -> CRF 草稿。返回 (draft, report, error)。

    认两种排布, 自动判断:
      A. **配置式** —— 表头含"题型/类型"等列, 一行一道题, 列里写明题干/类型/选项/必填
      B. **数据式** —— 就是一张普通表格, 首行是字段名。这时题型只能靠**下面几行的实际值**
         去推: 整列都是 YYYY-MM-DD 就当日期题, 整列都是数字就当数字题, 取值种类很少
         且重复出现就当单选题。推不出来一律给文本题。

    B 类推断一定会有错, 所以每一列都在 report 里写明"凭什么这么判", 让人对着核。
    """
    try:
        import openpyxl
    except ImportError:
        return None, None, ('服务器未安装 openpyxl, 无法解析 Excel。'
                            '装法: pip install openpyxl —— 在那之前请改用「AI 生成」或手工建表')
    try:
        import io
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True, read_only=True)
    except Exception as e:
        return None, None, 'Excel 打不开: {}'.format(e)

    ws = wb[wb.sheetnames[0]]
    rows = []
    for r in ws.iter_rows(max_row=200, values_only=True):
        if r and any(c is not None and str(c).strip() for c in r):
            rows.append([('' if c is None else str(c).strip()) for c in r])
    wb.close()
    if not rows:
        return None, None, 'Excel 第一个工作表是空的'

    header = rows[0]
    notes = []
    hidx = {}
    for i, h in enumerate(header):
        for key, pats in (('text', r'(题干|问题|字段名?|标题|name|label)'),
                          ('type', r'(题型|类型|type)'),
                          ('options', r'(选项|可选值|options)'),
                          ('required', r'(必填|required)'),
                          ('id', r'^(id|编码|变量名|code)$'),
                          ('section', r'(章节|分节|section|模块)')):
            if key not in hidx and re.search(pats, h, re.I):
                hidx[key] = i
    config_mode = 'text' in hidx and 'type' in hidx

    items, sections = [], {}
    if config_mode:
        notes.append({'step': 'layout', 'confidence': 'high',
                      'detail': '识别为配置式表格(表头含题干与题型列), 一行一道题'})
        for ri, row in enumerate(rows[1:], start=2):
            txt = row[hidx['text']] if hidx['text'] < len(row) else ''
            if not txt:
                continue
            raw_t = row[hidx['type']] if hidx['type'] < len(row) else ''
            t = 'text'
            for pat, tt in _EXCEL_TYPE_HINTS:
                if re.search(pat, raw_t, re.I):
                    t = tt; break
            iid = (row[hidx['id']] if 'id' in hidx and hidx['id'] < len(row) else '') or 'e{}'.format(ri)
            iid = re.sub(r'[^0-9A-Za-z_]', '_', str(iid)) or 'e{}'.format(ri)
            it = {'id': iid, 'text': txt, 'type': t}
            if 'required' in hidx and hidx['required'] < len(row) and \
                    re.search(r'(是|必填|Y|yes|true|1)', row[hidx['required']], re.I):
                it['required'] = True
            if t in CRF_OPTION_TYPES:
                raw_o = row[hidx['options']] if 'options' in hidx and hidx['options'] < len(row) else ''
                opts = [o.strip() for o in re.split(r'[;；,，/|]', raw_o) if o.strip()]
                if not opts:
                    notes.append({'step': 'options_missing', 'confidence': 'high',
                                  'detail': '第 {} 行「{}」是选择题但没给选项, 已降级为文本题'.format(ri, txt[:20])})
                    it['type'] = 'text'
                else:
                    it['options'] = [{'label': o, 'value': i} for i, o in enumerate(opts)]
            sec = row[hidx['section']] if 'section' in hidx and hidx['section'] < len(row) else ''
            sections.setdefault(sec or '', []).append(it)
            items.append(it)
    else:
        notes.append({'step': 'layout', 'confidence': 'medium',
                      'detail': '未找到题型列, 按数据式表格处理: 首行当字段名, '
                                '题型由下面 {} 行的实际取值推断'.format(len(rows) - 1)})
        body = rows[1:]
        for ci, hname in enumerate(header):
            if not hname:
                continue
            col = [r[ci] for r in body if ci < len(r) and r[ci] != '']
            it = {'id': 'c{}'.format(ci + 1), 'text': hname, 'type': 'text'}
            why = '整列无有效取值, 默认文本题'
            if col:
                if all(re.match(r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}', c) for c in col):
                    it['type'] = 'date'; why = '{} 个取值全都是日期格式'.format(len(col))
                elif all(re.match(r'^-?\d+(\.\d+)?$', c) for c in col):
                    it['type'] = 'number'; why = '{} 个取值全都是数字'.format(len(col))
                    nums = [float(c) for c in col]
                    it['min'], it['max'] = min(nums), max(nums)
                    why += ', 按实测范围暂定 min/max 为 {}~{} —— 这只是样本范围, 不是业务约束, 请核对'.format(
                        _fmt_num(it['min']), _fmt_num(it['max']))
                else:
                    uniq = sorted(set(col))
                    if 2 <= len(uniq) <= 8 and len(col) >= len(uniq) * 2:
                        it['type'] = 'single'
                        it['options'] = [{'label': u, 'value': i} for i, u in enumerate(uniq)]
                        why = '只有 {} 种取值且重复出现({} 行), 判为单选题'.format(len(uniq), len(col))
                    else:
                        # 用**中位数**长度而不是最大值: 一列三四个字的科室代码里混进一条
                        # 三十字的备注, 按最大值就把整列判成段落题了。中位数反映的是
                        # "这一列平常长什么样"。25 字是单行输入框大致装得下的中文上限。
                        lens = sorted(len(c) for c in col)
                        med = lens[len(lens) // 2]
                        if med >= 25:
                            it['type'] = 'paragraph'
                            why = '取值长度中位数 {} 字(最长 {} 字), 单行输入框装不下, 判为段落题'.format(
                                med, lens[-1])
                        else:
                            why = '取值零散、长度中位数仅 {} 字, 判为文本题'.format(med)
            notes.append({'step': 'column_type', 'confidence': 'low',
                          'detail': '列「{}」判为{} —— {}'.format(
                              hname[:20], CRF_BASIC_TYPES.get(it['type'], it['type']), why)})
            items.append(it)
        sections[''] = items

    definition = ({'sections': [{'name': k, 'items': v} for k, v in sections.items() if k],
                   'items': sections.get('', []), 'logic': []}
                  if any(sections.keys()) else {'items': items, 'logic': []})
    definition = {k: v for k, v in definition.items() if v or k == 'logic'}
    if 'items' not in definition and 'sections' not in definition:
        definition['items'] = items

    draft = {'code': code or re.sub(r'[^0-9A-Za-z]', '', ws.title)[:20] or 'CRFXLSX',
             'name': name or ws.title or 'Excel 导入的 CRF',
             'scope': 'private', 'source': 'excel', 'definition': definition}
    report = {'sheet': ws.title, 'rows_read': len(rows), 'mode': 'config' if config_mode else 'data',
              'item_count': len(_crf_items(definition)), 'notes': notes,
              'validation': validate_crf_definition(definition),
              'advisories': lint_crf_definition(definition), 'needs_review': True}
    return draft, report, None


def query_crf_responses(patient_no=None, code=None, include_superseded=False, limit=100):
    """CRF 填报记录列表。"""
    ensure_platform_crf_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ['1=1'], []
        if patient_no:
            where.append('r.patient_no=%s'); params.append(patient_no)
        if code:
            where.append('r.crf_code=%s'); params.append(code)
        if not include_superseded:
            where.append("r.status='submitted'")
        params.append(int(limit))
        cur.execute("""
            SELECT r.id, r.crf_code, r.crf_version, c.name, r.patient_no, p.name,
                   r.visit_name, r.data, r.hidden_data, r.operator, r.status,
                   r.revision_of, r.plan_id, r.created_at
            FROM platform_crf_response r
            LEFT JOIN platform_crf c ON c.code=r.crf_code AND c.version=r.crf_version
            LEFT JOIN platform_patient p ON p.patient_no=r.patient_no
            WHERE {}
            ORDER BY r.created_at DESC, r.id DESC LIMIT %s
        """.format(' AND '.join(where)), params)
        cols = ['id', 'crf_code', 'crf_version', 'crf_name', 'patient_no', 'patient_name',
                'visit_name', 'data', 'hidden_data', 'operator', 'status', 'revision_of',
                'plan_id', 'created_at']
        out = []
        for row in cur.fetchall():
            r = dict(zip(cols, row))
            if r.get('created_at') is not None and hasattr(r['created_at'], 'strftime'):
                r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            for k in ('data', 'hidden_data'):
                if isinstance(r.get(k), str):
                    try:
                        r[k] = json.loads(r[k])
                    except ValueError:
                        pass
            r['field_count'] = len(r['data']) if isinstance(r.get('data'), dict) else 0
            out.append(r)
        cur.close()
        return {'ok': True, 'count': len(out), 'responses': out}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


# ============ 随访平台 1.1 M15 (宣教材料库, 方案 §2.3) ============
#
# 这一块和前面几块有个本质区别, 必须先说清楚:
#
#   CRF / 量表 / 质控 的产出是给**医护**看的 —— 生成得不好, 医护当场就发现不合用。
#   宣教材料的产出是直接推给**患者**的 —— 患者没有能力判断内容对不对, 而且他们
#   多半会照做。一句"血压平稳后可自行减量"送到几百个随访患者手机上, 后果不是
#   "内容质量差", 是有人真的把药停了。
#
# 所以这里的规矩比别处严:
#   1. AI 产出一律是 draft, **必须**经人工审核置为 published 才能被随访计划调用;
#      没有"生成即发布"这条路, 接口层面就没有。
#   2. 生成时和提交审核时都跑一遍内容体检, 把剂量数字、用药指令、绝对化承诺、
#      "不必就医"这类高危表述标出来, 让审核的人知道该重点看哪几句。
#   3. 体检只标不删 —— 删了审核的人就看不到模型写了什么, 反而更危险。

EDU_TOPICS = ('disease', 'medication', 'rehab', 'diet', 'psych', 'other')
EDU_TOPIC_LABELS = {'disease': '疾病知识', 'medication': '用药指导', 'rehab': '康复训练',
                    'diet': '饮食调理', 'psych': '心理调节', 'other': '其他'}
EDU_FORMATS = ('article', 'illustrated', 'video_script')
EDU_FORMAT_LABELS = {'article': '健康宣教文章', 'illustrated': '图文科普', 'video_script': '视频脚本'}
EDU_STATUSES = ('draft', 'reviewing', 'published', 'archived')
EDU_STATUS_LABELS = {'draft': '草稿', 'reviewing': '待审核', 'published': '已发布', 'archived': '已归档'}

# 内容体检规则。level:
#   block = 不该出现在群发给患者的材料里, 审核必须逐句确认
#   warn  = 未必错, 但要人看一眼
#
# 这些不是"敏感词过滤"。判断依据是: 这句话被一个不具备医学判断力的患者照做之后,
# 最坏会发生什么。剂量数字被照抄、"可自行停药"被当真, 都是能出人命的; 而
# "多喝水"再啰嗦也不会。
# 否定前缀。"不要自行调整用药" 是这条规则自己推荐的**正确**写法, 却和
# "可自行调整用药" 命中同一个模式。不排掉的话, 规则会在它推荐的改法上报警 ——
# 使用者试一次就学会了忽略这条规则, 那它对真正危险的那句也就不起作用了。
_EDU_NEG = r'(不要|不可|不能|不得|不应|不宜|切勿|请勿|禁止|避免|严禁|勿|别|无需|不需|禁)\s*$'

# 每条规则: (代码, 级别, 命中模式, 说明, 前置否定则跳过?)
# 只有"动作类"的规则需要看否定 —— 剂量数字前面加个"不要"也还是剂量数字;
# 而 no_care 那条本身就以否定词开头("不必就医"), 再排否定会把它自己排没。
EDU_CONTENT_RULES = [
    ('dosage', 'block', r'\d+\s*(mg|毫克|μg|微克|g\b|克|ml|毫升|IU|国际单位|片|粒|袋|支)\b',
     '出现了具体剂量。群发材料里的剂量会被患者当成自己的用法 —— 剂量因人而异, '
     '应改为"遵医嘱"或"按处方剂量", 具体数字放在一对一医嘱里', False),
    ('med_change', 'block', r'(自行(停药|减量|加量|调整|换药)|可以?停药|停用|加大剂量|减半服用|加倍服用)',
     '出现了调整用药的指示。患者据此擅自改药是随访中最常见的严重不良事件来源, '
     '这类表述必须改成"如有不适请联系随访医生, 不要自行调整"', True),
    ('no_care', 'block', r'(不必就医|无需就诊|不用去医院|不需要复查|可以不用管|观察即可)',
     '出现了劝阻就医的表述。它会让本该及时就诊的患者在家里拖延', False),
    ('absolute', 'warn', r'(一定能|保证|百分之百|百分百|完全根治|彻底治愈|绝对(安全|有效)|无副作用|没有副作用)',
     '出现了绝对化承诺。医学结论几乎没有绝对, 这类话既不真实, 也会在预期落空时'
     '摧毁患者对整个随访的信任', False),
    ('diagnosis', 'warn', r'(您(患有|得了|确诊)|你(患有|得了|确诊)|诊断为|确诊为)',
     '出现了诊断性断言。宣教材料是群发的, 不该对具体某个人下诊断', True),
    ('emergency', 'warn', r'(胸痛|呼吸困难|意识不清|昏迷|大出血|抽搐|自杀|轻生)',
     '提到了急症/危机情形。这类内容本身常常是必要的, 但必须同时给出明确的求助方式'
     '(急救电话、随访医生联系方式), 只描述症状不给出路等于没写', False),
]


def scan_edu_content(text, title=''):
    """宣教内容体检。返回 findings 列表, 每条带原文片段供审核者定位。

    只标不删 —— 删掉的话审核的人根本看不到模型写了什么, 比留着更危险。
    """
    body = '{}\n{}'.format(title or '', text or '')
    out = []
    for code, level, pat, why, skip_negated in EDU_CONTENT_RULES:
        for m in re.finditer(pat, body):
            if skip_negated and re.search(_EDU_NEG, body[max(0, m.start() - 6):m.start()]):
                continue
            lo = max(0, m.start() - 28)
            hi = min(len(body), m.end() + 28)
            out.append({'rule': code, 'level': level, 'matched': m.group(0),
                        'excerpt': ('…' if lo else '') + body[lo:hi].replace('\n', ' ') + ('…' if hi < len(body) else ''),
                        'why': why})
            if sum(1 for x in out if x['rule'] == code) >= 5:
                break       # 同一类命中太多就不刷屏了, 审核者看几条就明白了
    return out


EDU_DISCLAIMER = ('本材料为健康科普, 不能替代医生的诊疗意见。用药与治疗方案请遵医嘱; '
                  '若出现不适或病情变化, 请及时联系随访医生或就近就医。')

# 模板骨架。刻意只给**结构和提问**, 不给医学结论 —— 一份宣教稿真正有价值的部分
# (这个病该注意什么、这个阶段最容易出什么问题) 必须由临床方写, 模板负责保证
# 它不会漏掉"什么时候该找医生"这一节。
EDU_TEMPLATE_SECTIONS = {
    'disease': ['这个病是怎么回事', '为什么要长期随访', '日常需要留意哪些变化', '什么情况下必须联系医生'],
    'medication': ['为什么要按时用药', '漏服了怎么办', '常见的不舒服有哪些', '什么情况下必须联系医生'],
    'rehab': ['这个阶段的康复目标', '每天可以做什么', '做到什么程度就该停', '什么情况下必须联系医生'],
    'diet': ['这个阶段的饮食原则', '推荐多吃什么', '需要控制什么', '什么情况下必须联系医生'],
    'psych': ['这个阶段常见的情绪反应', '可以自己做的调节', '家人可以怎么帮忙', '什么情况下必须联系医生'],
    'other': ['背景', '要点', '注意事项', '什么情况下必须联系医生'],
}


def generate_edu_draft(spec):
    """§2.3(1): 生成宣教材料草稿。返回 (draft, report)。

    产出恒为 status='draft'。这不是默认值, 是硬约束 —— 见本节开头的说明。
    """
    notes = []
    disease = str(spec.get('disease') or '').strip()
    stage = str(spec.get('stage') or '').strip()
    topic = spec.get('topic') or 'disease'
    if topic not in EDU_TOPICS:
        topic = 'other'
    fmt = spec.get('format') or 'article'
    if fmt not in EDU_FORMATS:
        fmt = 'article'
    backend = (spec.get('backend') or os.environ.get('SCALE_LLM_PROVIDER') or 'template').lower()

    body_from_llm = None
    if backend == 'claude':
        draft, err = _generate_via_claude({
            'goal': '患者宣教材料: {} {} {}'.format(disease, stage, EDU_TOPIC_LABELS[topic]),
            'dimensions': EDU_TEMPLATE_SECTIONS[topic]}, notes)
        if err:
            notes.append({'step': 'backend_fallback', 'confidence': 'high',
                          'detail': '大模型后端不可用({}), 已回落本地模板'.format(err)})
        else:
            body_from_llm = draft

    title = '{}{}{}'.format(disease or '通用', ('·' + stage) if stage else '',
                            EDU_TOPIC_LABELS[topic])
    secs = EDU_TEMPLATE_SECTIONS[topic]
    lines = []
    if fmt == 'video_script':
        lines.append('【视频脚本 · 建议时长 2-3 分钟】')
        for i, s in enumerate(secs, 1):
            lines.append('\n镜头 {}｜{}'.format(i, s))
            lines.append('  画面：【待填写】')
            lines.append('  旁白：【待填写 —— 由临床方撰写, 不要写具体剂量】')
    else:
        for s in secs:
            lines.append('\n## {}'.format(s))
            lines.append('【待填写】' + ('（这一节请务必写清楚: 出现哪些情况要立刻联系随访医生或就医, '
                                        '并留下联系方式）' if s.startswith('什么情况下') else ''))
    if fmt == 'illustrated':
        lines.append('\n## 配图建议')
        lines.append('【待填写 —— 每节配一张图, 图上不要出现具体剂量】')
    lines.append('\n---\n' + EDU_DISCLAIMER)
    body = '\n'.join(lines).strip()

    notes.append({'step': 'template', 'confidence': 'high',
                  'detail': '按「{}」生成 {} 节骨架。模板只给结构和提问, 不给医学结论 —— '
                            '这个病该注意什么、这个阶段最容易出什么问题, 必须由临床方写。'
                            '模板负责保证不漏掉"什么时候该找医生"这一节'.format(
                                EDU_FORMAT_LABELS[fmt], len(secs))})
    notes.append({'step': 'must_review', 'confidence': 'high',
                  'detail': '产出为草稿, 必须经人工审核发布后才能被随访计划调用。'
                            '宣教材料是直接推给患者的, 患者没有能力判断内容对不对, 而且多半会照做'})
    if body_from_llm:
        notes.append({'step': 'llm_note', 'confidence': 'low',
                      'detail': '大模型产出已作为参考, 仍须逐句核对后替换占位文字'})

    findings = scan_edu_content(body, title)
    draft = {'title': title, 'category': disease or None, 'stage': stage or None,
             'topic': topic, 'format': fmt, 'body': body, 'source': 'ai',
             'status': 'draft', 'tags': [x for x in [disease, stage, EDU_TOPIC_LABELS[topic]] if x]}
    report = {'backend': backend, 'section_count': len(secs), 'char_count': len(body),
              'notes': notes, 'content_findings': findings,
              'blocking_findings': sum(1 for f in findings if f['level'] == 'block'),
              'needs_review': True}
    return draft, report


def ensure_platform_edu_tables():
    """M15: 宣教材料表 + 审核留痕表 (idempotent)。"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_edu_material (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                code VARCHAR(64) NOT NULL,
                version VARCHAR(32) NOT NULL DEFAULT '1',
                title VARCHAR(200) NOT NULL,
                category VARCHAR(64) DEFAULT NULL COMMENT '病种',
                stage VARCHAR(64) DEFAULT NULL COMMENT '病程阶段',
                topic VARCHAR(24) DEFAULT 'disease' COMMENT '疾病知识/用药指导/康复训练/饮食调理/心理调节',
                format VARCHAR(24) DEFAULT 'article' COMMENT '文章/图文科普/视频脚本',
                tags JSON DEFAULT NULL COMMENT '按随访场景/病种/科室快速调用用的标签',
                body MEDIUMTEXT NOT NULL,
                scope ENUM('private','shared') DEFAULT 'private',
                owner VARCHAR(64) DEFAULT NULL,
                source VARCHAR(24) DEFAULT 'manual' COMMENT 'manual/ai',
                status ENUM('draft','reviewing','published','archived') DEFAULT 'draft'
                    COMMENT 'AI 产出恒为 draft; 只有 published 才允许被随访计划调用',
                content_findings JSON DEFAULT NULL COMMENT '内容体检结果, 供审核者定位',
                reviewed_by VARCHAR(64) DEFAULT NULL,
                reviewed_at DATETIME DEFAULT NULL,
                review_note VARCHAR(500) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uk_edu_code_version (code, version),
                INDEX idx_status (status),
                INDEX idx_category (category),
                INDEX idx_topic (topic)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M15 宣教材料库'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_edu_log (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                material_id BIGINT NOT NULL,
                action ENUM('create','submit','publish','reject','archive','edit') NOT NULL,
                from_status VARCHAR(16) DEFAULT NULL,
                to_status VARCHAR(16) DEFAULT NULL,
                operator VARCHAR(64) DEFAULT NULL,
                note VARCHAR(1000) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_material (material_id, id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M15 宣教材料流转留痕 (只增不改)'
        """)
        print('[启动] platform_edu_material / platform_edu_log 表已就绪')
        cur.close()
    except Exception as e:
        print('[启动] ensure_platform_edu_tables 失败:', e)
    finally:
        conn.close()


def upsert_edu_material(body):
    """建/改宣教材料。

    **status 不能由调用方直接设成 published** —— 发布必须走 /edu/transition 的
    审核动作, 那条路才会记留痕、记审核人。允许直接置 published 的话, 前端一个字段
    就能把未经审核的 AI 稿推给患者, 前面所有约束都白设。
    """
    title = str(body.get('title') or '').strip()
    text = body.get('body')
    if not title or not str(text or '').strip():
        return None, 'title 和 body 必填'
    topic = body.get('topic') or 'disease'
    if topic not in EDU_TOPICS:
        return None, 'topic 必须是 {} 之一'.format('/'.join(EDU_TOPICS))
    fmt = body.get('format') or 'article'
    if fmt not in EDU_FORMATS:
        return None, 'format 必须是 {} 之一'.format('/'.join(EDU_FORMATS))
    scope = body.get('scope') or 'private'
    if scope not in CRF_SCOPES:
        return None, 'scope 必须是 private 或 shared'
    status = body.get('status') or 'draft'
    if status not in ('draft', 'reviewing'):
        return None, ('status 只能设为 draft 或 reviewing。发布要走 '
                      '/api/platform/edu/transition 的 publish 动作 —— 那条路会记下'
                      '是谁在什么时候审的, 直接置 published 就没有这份留痕了')

    import hashlib
    code = str(body.get('code') or '').strip() or 'EDU' + hashlib.md5(
        title.encode('utf-8')).hexdigest()[:6].upper()
    version = str(body.get('version') or '1').strip()
    findings = scan_edu_content(str(text), title)

    ensure_platform_edu_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT id, status FROM platform_edu_material WHERE code=%s AND version=%s',
                    (code, version))
        row = cur.fetchone()
        if row and row[1] == 'published':
            # 已发布的材料改内容 = 患者手里的版本和库里的对不上。开新版, 旧版继续在架。
            version = _bump_version(version)
            row = None
        cur.execute("""
            INSERT INTO platform_edu_material
              (code, version, title, category, stage, topic, format, tags, body,
               scope, owner, source, status, content_findings)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              title=VALUES(title), category=VALUES(category), stage=VALUES(stage),
              topic=VALUES(topic), format=VALUES(format), tags=VALUES(tags),
              body=VALUES(body), scope=VALUES(scope), owner=VALUES(owner),
              status=VALUES(status), content_findings=VALUES(content_findings)
        """, (code, version, title, body.get('category') or None, body.get('stage') or None,
              topic, fmt, json.dumps(body.get('tags') or [], ensure_ascii=False), str(text),
              scope, body.get('owner') or None, body.get('source') or 'manual', status,
              json.dumps(findings, ensure_ascii=False)))
        mid = cur.lastrowid or (row[0] if row else None)
        if mid is None:
            cur.execute('SELECT id FROM platform_edu_material WHERE code=%s AND version=%s',
                        (code, version))
            mid = (cur.fetchone() or [None])[0]
        cur.execute("""INSERT INTO platform_edu_log (material_id, action, from_status, to_status, operator, note)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (mid, 'edit' if row else 'create', row[1] if row else None, status,
                     body.get('owner') or None, body.get('note') or None))
        cur.close()
        return {'id': mid, 'code': code, 'version': version, 'status': status,
                'content_findings': findings,
                'blocking_findings': sum(1 for f in findings if f['level'] == 'block')}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


EDU_TRANSITIONS = {
    'submit':  {'from': ('draft',), 'to': 'reviewing'},
    'publish': {'from': ('draft', 'reviewing'), 'to': 'published'},
    'reject':  {'from': ('reviewing',), 'to': 'draft'},
    'archive': {'from': ('published', 'reviewing', 'draft'), 'to': 'archived'},
}


def edu_transition(body):
    """推进宣教材料状态 {id, action, operator?, note?, ack_findings?}。

    publish 时若内容体检有 block 级发现, 必须显式 ack_findings=true 才放行 ——
    不是拦死, 是逼审核的人**看见**它。临床方完全可能有正当理由保留某个剂量数字
    (比如那是"每片含量"而不是"你该吃多少"), 但那必须是他知情之后的决定。
    """
    try:
        mid = int(body.get('id'))
    except (TypeError, ValueError):
        return None, 'id 必填且为整数'
    action = str(body.get('action') or '').strip()
    tr = EDU_TRANSITIONS.get(action)
    if not tr:
        return None, 'action 必须是 {}'.format('/'.join(EDU_TRANSITIONS))

    ensure_platform_edu_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT status, content_findings, title FROM platform_edu_material WHERE id=%s', (mid,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, '材料不存在: {}'.format(mid)
        cur_status, findings, title = row
        if isinstance(findings, str):
            try:
                findings = json.loads(findings)
            except ValueError:
                findings = []
        findings = findings or []
        if cur_status not in tr['from']:
            cur.close()
            return None, '当前状态 {} 不能执行 {} (允许的前置状态: {})'.format(
                EDU_STATUS_LABELS.get(cur_status, cur_status), action,
                '/'.join(EDU_STATUS_LABELS.get(x, x) for x in tr['from']))

        # 参数缺失先报 —— 那是调用方的问题; 内容体检是给审核者的反馈, 排在后面
        if action == 'publish' and not body.get('operator'):
            cur.close()
            return None, '发布必须署名 operator —— 这份材料要推给患者, 得有人对它负责'
        blocking = [f for f in findings if f.get('level') == 'block']
        if action == 'publish' and blocking and not body.get('ack_findings'):
            cur.close()
            return {'ok': False, 'published': False, 'blocking_findings': blocking,
                    'hint': ('这份材料有 {} 处高危表述(剂量数字/用药调整指示/劝阻就医)。'
                             '宣教材料是直接推给患者的, 他们多半会照做。请逐条确认后带 '
                             'ack_findings=true 再发布, 或先改稿'.format(len(blocking)))}, None
        new_status = tr['to']
        if action == 'publish':
            cur.execute("""UPDATE platform_edu_material SET status=%s, reviewed_by=%s,
                           reviewed_at=NOW(), review_note=%s WHERE id=%s""",
                        (new_status, body.get('operator'), (body.get('note') or '')[:500] or None, mid))
        else:
            cur.execute('UPDATE platform_edu_material SET status=%s WHERE id=%s', (new_status, mid))
        cur.execute("""INSERT INTO platform_edu_log (material_id, action, from_status, to_status, operator, note)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (mid, action, cur_status, new_status, body.get('operator') or None,
                     (body.get('note') or '')[:1000] or None))
        cur.close()
        return {'ok': True, 'id': mid, 'from': cur_status, 'to': new_status,
                'acked_findings': len(blocking) if action == 'publish' else 0}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def query_edu_materials(status=None, category=None, topic=None, scope=None,
                        keyword=None, with_body=False, material_id=None, limit=200):
    """宣教材料列表 (§2.3(2) 按随访场景/病种/科室快速调用)。"""
    ensure_platform_edu_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ['1=1'], []
        if material_id:
            where.append('m.id=%s'); params.append(int(material_id))
        if status:
            where.append('m.status=%s'); params.append(status)
        if category:
            where.append('m.category=%s'); params.append(category)
        if topic:
            where.append('m.topic=%s'); params.append(topic)
        if scope:
            where.append('m.scope=%s'); params.append(scope)
        if keyword:
            where.append('(m.title LIKE %s OR JSON_SEARCH(m.tags, "one", %s) IS NOT NULL)')
            params += ['%{}%'.format(keyword), '%{}%'.format(keyword)]
        cols = ('m.id, m.code, m.version, m.title, m.category, m.stage, m.topic, m.format, '
                'm.tags, m.scope, m.owner, m.source, m.status, m.content_findings, '
                'm.reviewed_by, m.reviewed_at, m.review_note, m.created_at, m.updated_at, '
                'CHAR_LENGTH(m.body) AS char_count')
        if with_body or material_id:
            cols += ', m.body'
        params.append(int(limit))
        cur.execute('SELECT {} FROM platform_edu_material m WHERE {} '
                    'ORDER BY FIELD(m.status,"reviewing","draft","published","archived"), '
                    'm.updated_at DESC LIMIT %s'.format(cols, ' AND '.join(where)), params)
        names = [d[0] for d in cur.description]
        out = []
        for row in cur.fetchall():
            r = dict(zip(names, row))
            for k in ('reviewed_at', 'created_at', 'updated_at'):
                if r.get(k) is not None and hasattr(r[k], 'strftime'):
                    r[k] = r[k].strftime('%Y-%m-%d %H:%M:%S')
            for k in ('tags', 'content_findings'):
                if isinstance(r.get(k), str):
                    try:
                        r[k] = json.loads(r[k])
                    except ValueError:
                        pass
            r['topic_label'] = EDU_TOPIC_LABELS.get(r.get('topic'), r.get('topic'))
            r['format_label'] = EDU_FORMAT_LABELS.get(r.get('format'), r.get('format'))
            r['status_label'] = EDU_STATUS_LABELS.get(r.get('status'), r.get('status'))
            r['blocking_findings'] = sum(1 for f in (r.get('content_findings') or [])
                                         if f.get('level') == 'block')
            out.append(r)
        if material_id and out:
            cur.execute("""SELECT action, from_status, to_status, operator, note, created_at
                           FROM platform_edu_log WHERE material_id=%s ORDER BY id""", (int(material_id),))
            out[0]['log'] = [{'action': a, 'from': f, 'to': t, 'operator': o, 'note': n,
                              'at': c.strftime('%Y-%m-%d %H:%M:%S') if hasattr(c, 'strftime') else c}
                             for a, f, t, o, n, c in cur.fetchall()]
        cur.close()
        return {'ok': True, 'count': len(out), 'materials': out,
                'topics': EDU_TOPIC_LABELS, 'formats': EDU_FORMAT_LABELS,
                'statuses': EDU_STATUS_LABELS}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


# ============ 随访平台 1.1 M16 (高级检索与统计, 方案 §4.6(2)) ============
#
# "柔性增删检索条件" = 让使用者自己拼查询。这件事只有一种安全的做法:
#
#   **字段来自白名单, 值一律走参数化占位符, 用户输入永远不进 SQL 文本。**
#
# 反过来做(把字段名或值拼进 SQL)在功能上更省事、更"灵活", 但那等于把一个
# 患者数据库的任意读权限交给任何能调这个接口的人。这里的 SEARCH_FIELDS 是
# 唯一允许出现在 SQL 里的字段来源, 不在表里的名字一律拒绝, 不做模糊匹配、
# 不做"看起来像列名就放行"。
#
# 每个字段声明:
#   label   给人看的名字
#   type    num / str / enum / date  -> 决定允许哪些运算符
#   sql     直接可用的 SQL 片段(**常量, 不含任何用户输入**), 或 None 表示要走子查询
#   sub     需要子查询时的构造函数 (op, value, extra) -> (sql_fragment, params)
#   needs   该字段还需要哪些附加参数(如量表编码), 缺了就报错而不是静默忽略
SEARCH_OPS = {
    'num':  ('eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between'),
    'str':  ('eq', 'ne', 'contains', 'in', 'empty', 'filled'),
    'enum': ('eq', 'ne', 'in'),
    'date': ('eq', 'gt', 'gte', 'lt', 'lte', 'between'),
}
SEARCH_OP_SQL = {'eq': '=', 'ne': '<>', 'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}
SEARCH_MAX_NODES = 40      # 一次检索最多多少个条件节点
SEARCH_MAX_DEPTH = 5       # 条件树最深几层


def _sub_scale(kind):
    """量表相关字段的子查询构造。kind: total / level / item"""
    def build(op, value, extra):
        code = str(extra.get('scale_code') or '').strip()
        params = [code]
        if kind == 'item':
            item_id = str(extra.get('item_id') or '').strip()
            # JSON_EXTRACT 的路径必须是常量, 不能拼用户输入 —— 用 JSON_UNQUOTE(JSON_EXTRACT(x, ?))
            # 的形式让 item_id 走参数
            val_expr = "JSON_UNQUOTE(JSON_EXTRACT(r.answers, CONCAT('$.', %s)))"
            params.append(item_id)
        elif kind == 'total':
            val_expr = 'r.total_score'
        else:
            val_expr = 'r.level_label'
        cmp_sql, cmp_params = _cmp_sql(val_expr if kind != 'item' else 'CAST({} AS DECIMAL(10,2))'.format(val_expr),
                                       'num' if kind in ('total', 'item') else 'str', op, value)
        return ("""EXISTS (SELECT 1 FROM platform_scale_response r
                           WHERE r.patient_no = p.patient_no AND r.status='submitted'
                             AND r.scale_code = %s AND {})""".format(cmp_sql),
                params + cmp_params)
    return build


def _sub_vital(metric):
    """体征字段: 近 N 天该指标的日均值。N 由 extra.days 给, 默认 30。"""
    def build(op, value, extra):
        try:
            days = min(max(int(extra.get('days') or 30), 1), 365)
        except (TypeError, ValueError):
            days = 30
        cmp_sql, cmp_params = _cmp_sql('AVG(a.value)', 'num', op, value)
        return ("""p.patient_no IN (
                     SELECT a.patient_no FROM platform_vital_daily a
                     WHERE a.metric = %s AND a.day >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
                     GROUP BY a.patient_no HAVING {})""".format(cmp_sql),
                [metric, days] + cmp_params)
    return build


def _sub_alarm(status_set):
    def build(op, value, extra):
        cmp_sql, cmp_params = _cmp_sql('COUNT(*)', 'num', op, value)
        ph = ','.join(['%s'] * len(status_set))
        return ("""p.patient_no IN (
                     SELECT al.patient_no FROM platform_alarm al
                     WHERE al.status IN ({}) GROUP BY al.patient_no HAVING {})""".format(ph, cmp_sql),
                list(status_set) + cmp_params)
    return build


def _sub_crf_field():
    def build(op, value, extra):
        code = str(extra.get('crf_code') or '').strip()
        field = str(extra.get('field') or '').strip()
        cmp_sql, cmp_params = _cmp_sql(
            "JSON_UNQUOTE(JSON_EXTRACT(cr.data, CONCAT('$.', %s)))", 'str', op, value)
        return ("""EXISTS (SELECT 1 FROM platform_crf_response cr
                           WHERE cr.patient_no = p.patient_no AND cr.status='submitted'
                             AND cr.crf_code = %s AND {})""".format(cmp_sql),
                [code, field] + cmp_params)
    return build


def _sub_plan_overdue():
    def build(op, value, extra):
        cmp_sql, cmp_params = _cmp_sql('DATEDIFF(CURDATE(), pl.next_due)', 'num', op, value)
        return ("""EXISTS (SELECT 1 FROM platform_plan pl
                           WHERE pl.patient_no = p.patient_no AND pl.active=1
                             AND pl.next_due IS NOT NULL AND {})""".format(cmp_sql), cmp_params)
    return build


SEARCH_FIELDS = {
    'patient.group':   {'label': '分组/队列', 'type': 'str',  'sql': 'p.group_tag', 'group': '患者属性'},
    'patient.gender':  {'label': '性别', 'type': 'enum', 'sql': 'p.gender', 'group': '患者属性',
                        'options': [{'label': '男', 'value': 'M'}, {'label': '女', 'value': 'F'}]},
    'patient.age':     {'label': '年龄', 'type': 'num',  'sql': 'p.age', 'group': '患者属性'},
    'patient.name':    {'label': '姓名', 'type': 'str',  'sql': 'p.name', 'group': '患者属性'},
    'patient.no':      {'label': '门诊号', 'type': 'str', 'sql': 'p.patient_no', 'group': '患者属性'},
    'patient.note':    {'label': '备注', 'type': 'str',  'sql': 'p.note', 'group': '患者属性'},
    'patient.created': {'label': '建档日期', 'type': 'date', 'sql': 'DATE(p.created_at)', 'group': '患者属性'},
    'scale.total':     {'label': '量表总分', 'type': 'num', 'sub': _sub_scale('total'),
                        'needs': ['scale_code'], 'group': '量表'},
    'scale.level':     {'label': '量表分级', 'type': 'str', 'sub': _sub_scale('level'),
                        'needs': ['scale_code'], 'group': '量表'},
    'scale.item':      {'label': '量表单题答案', 'type': 'num', 'sub': _sub_scale('item'),
                        'needs': ['scale_code', 'item_id'], 'group': '量表'},
    'vital.hr':        {'label': '心率日均值', 'type': 'num', 'sub': _sub_vital('hr'), 'group': '体征'},
    'vital.spo2':      {'label': '血氧日均值', 'type': 'num', 'sub': _sub_vital('spo2'), 'group': '体征'},
    'vital.sbp':       {'label': '收缩压日均值', 'type': 'num', 'sub': _sub_vital('sbp'), 'group': '体征'},
    'vital.dbp':       {'label': '舒张压日均值', 'type': 'num', 'sub': _sub_vital('dbp'), 'group': '体征'},
    'vital.temp':      {'label': '体温日均值', 'type': 'num', 'sub': _sub_vital('temp'), 'group': '体征'},
    'vital.sleep':     {'label': '睡眠时长日均值', 'type': 'num', 'sub': _sub_vital('sleep'), 'group': '体征'},
    'alarm.open':      {'label': '未处理预警数', 'type': 'num',
                        'sub': _sub_alarm(('new', 'acked')), 'group': '预警'},
    'alarm.total':     {'label': '累计预警数', 'type': 'num',
                        'sub': _sub_alarm(('new', 'acked', 'followed', 'closed')), 'group': '预警'},
    'crf.field':       {'label': 'CRF 字段值', 'type': 'str', 'sub': _sub_crf_field(),
                        'needs': ['crf_code', 'field'], 'group': 'CRF'},
    'plan.overdue':    {'label': '随访超窗天数', 'type': 'num', 'sub': _sub_plan_overdue(),
                        'group': '随访计划'},
}


def _cmp_sql(expr, ftype, op, value):
    """把 (表达式, 运算符, 值) 变成 SQL 片段 + 参数。expr 必须是常量片段。"""
    if op == 'empty':
        return "({} IS NULL OR {} = '')".format(expr, expr), []
    if op == 'filled':
        return "({} IS NOT NULL AND {} <> '')".format(expr, expr), []
    if op == 'contains':
        return '{} LIKE %s'.format(expr), ['%{}%'.format(value)]
    if op == 'in':
        vals = value if isinstance(value, list) else [value]
        vals = vals[:50] or ['']
        return '{} IN ({})'.format(expr, ','.join(['%s'] * len(vals))), list(vals)
    if op == 'between':
        lo, hi = (value + [None, None])[:2] if isinstance(value, list) else (value, value)
        return '{} BETWEEN %s AND %s'.format(expr), [lo, hi]
    return '{} {} %s'.format(expr, SEARCH_OP_SQL[op]), [value]


def build_search_sql(node, depth=0, counter=None):
    """条件树 -> (SQL 片段, 参数列表)。任何不合法之处直接抛 ValueError, 不做兜底放行。

    counter 用来限制节点总数 —— 没有上限的话, 一个几千节点的条件树能把数据库拖死,
    而这个接口是不鉴权的。
    """
    if counter is None:
        counter = [0]
    counter[0] += 1
    if counter[0] > SEARCH_MAX_NODES:
        raise ValueError('检索条件超过 {} 个, 请精简'.format(SEARCH_MAX_NODES))
    if depth > SEARCH_MAX_DEPTH:
        raise ValueError('检索条件嵌套超过 {} 层'.format(SEARCH_MAX_DEPTH))
    if not isinstance(node, dict):
        raise ValueError('条件必须是对象')

    if node.get('op') in ('and', 'or'):
        kids = node.get('children') or []
        if not isinstance(kids, list) or not kids:
            raise ValueError('{} 组合至少要有一个子条件'.format(node['op']))
        parts, params = [], []
        for k in kids:
            sql, ps = build_search_sql(k, depth + 1, counter)
            parts.append(sql); params += ps
        return '(' + (' AND ' if node['op'] == 'and' else ' OR ').join(parts) + ')', params

    field = node.get('field')
    spec = SEARCH_FIELDS.get(field)
    if spec is None:
        # 这里不给"你是不是想找 xxx"之类的提示 —— 那等于帮人枚举字段。
        raise ValueError('未知的检索字段: {!r}'.format(field)[:120])
    op = node.get('operator') or node.get('op')
    allowed = SEARCH_OPS[spec['type']]
    if op not in allowed:
        raise ValueError('字段「{}」({}) 只支持 {} 这些运算符, 收到 {!r}'.format(
            spec['label'], spec['type'], '/'.join(allowed), op))
    value = node.get('value')
    if op not in ('empty', 'filled') and value is None:
        raise ValueError('字段「{}」的条件缺 value'.format(spec['label']))
    if spec['type'] == 'num' and op != 'between':
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValueError('字段「{}」需要数字, 收到 {!r}'.format(spec['label'], value)[:120])
    extra = node.get('params') or {}
    for need in (spec.get('needs') or []):
        if not str(extra.get(need) or '').strip():
            raise ValueError('字段「{}」还需要参数 {} —— 不给的话不知道查哪一份'.format(
                spec['label'], need))
    if spec.get('sub'):
        return spec['sub'](op, value, extra)
    return _cmp_sql(spec['sql'], spec['type'], op, value)


def platform_search(body):
    """§4.6(2) 受试者高级检索。{conditions:{...}, limit?, offset?}"""
    conds = body.get('conditions')
    try:
        limit = min(max(int(body.get('limit') or 200), 1), 1000)
        offset = max(int(body.get('offset') or 0), 0)
    except (TypeError, ValueError):
        return None, 'limit/offset 必须是整数'
    where, params = '1=1', []
    if conds:
        try:
            where, params = build_search_sql(conds)
        except ValueError as e:
            return None, str(e)

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM platform_patient p WHERE {}'.format(where), params)
        total = cur.fetchone()[0]
        cur.execute("""
            SELECT p.patient_no, p.name, p.gender, p.age, p.group_tag, p.note, p.created_at,
                   (SELECT COUNT(*) FROM platform_alarm a WHERE a.patient_no=p.patient_no
                      AND a.status IN ('new','acked')) AS open_alarms,
                   (SELECT COUNT(*) FROM platform_scale_response r WHERE r.patient_no=p.patient_no
                      AND r.status='submitted') AS scale_n,
                   (SELECT COUNT(*) FROM platform_crf_response cr WHERE cr.patient_no=p.patient_no
                      AND cr.status='submitted') AS crf_n
            FROM platform_patient p WHERE {} ORDER BY p.patient_no LIMIT %s OFFSET %s
        """.format(where), params + [limit, offset])
        cols = ['patient_no', 'name', 'gender', 'age', 'group_tag', 'note', 'created_at',
                'open_alarms', 'scale_n', 'crf_n']
        out = []
        for row in cur.fetchall():
            r = dict(zip(cols, row))
            if r.get('created_at') is not None and hasattr(r['created_at'], 'strftime'):
                r['created_at'] = r['created_at'].strftime('%Y-%m-%d')
            out.append(r)
        cur.close()
        return {'ok': True, 'total': total, 'count': len(out), 'patients': out,
                'limit': limit, 'offset': offset}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


# 统计维度。和检索字段分开: 检索是"筛出哪些人", 统计是"这批人在某个维度上怎么分布"。
# 图表类型由**规则**决定, 不是模型选的 —— 分类字段给饼图/柱状图, 数值字段分桶给直方图,
# 时间给折线。方案里写的"AI 自动生成图表", 我们做到的是这一层, 别把它说成别的。
STAT_DIMS = {
    'gender':      {'label': '性别分布', 'kind': 'cat', 'sql': "COALESCE(p.gender,'未填')",
                    'chart': 'pie', 'map': {'M': '男', 'F': '女'}},
    'group_tag':   {'label': '分组分布', 'kind': 'cat', 'sql': "COALESCE(p.group_tag,'未分组')", 'chart': 'pie'},
    'age_band':    {'label': '年龄段分布', 'kind': 'cat', 'chart': 'bar',
                    'sql': ("CASE WHEN p.age IS NULL THEN '未填' WHEN p.age<18 THEN '<18' "
                            "WHEN p.age<40 THEN '18-39' WHEN p.age<60 THEN '40-59' "
                            "WHEN p.age<75 THEN '60-74' ELSE '75+' END")},
    'alarm_band':  {'label': '未处理预警数分布', 'kind': 'cat', 'chart': 'bar',
                    'sql': ("CASE WHEN (SELECT COUNT(*) FROM platform_alarm a WHERE a.patient_no=p.patient_no "
                            "AND a.status IN ('new','acked'))=0 THEN '0' "
                            "WHEN (SELECT COUNT(*) FROM platform_alarm a WHERE a.patient_no=p.patient_no "
                            "AND a.status IN ('new','acked'))<=2 THEN '1-2' ELSE '3+' END")},
    'enroll_month': {'label': '按月建档趋势', 'kind': 'time', 'chart': 'line',
                     'sql': "DATE_FORMAT(p.created_at,'%%Y-%%m')"},
    'scale_level': {'label': '量表分级占比', 'kind': 'cat', 'chart': 'pie', 'needs': ['scale_code'],
                    'sql': ("COALESCE((SELECT r.level_label FROM platform_scale_response r "
                            "WHERE r.patient_no=p.patient_no AND r.status='submitted' AND r.scale_code=%s "
                            "ORDER BY r.created_at DESC LIMIT 1),'未评估')"),
                    'sql_params': ['scale_code']},
    'followup':    {'label': '随访完成情况', 'kind': 'cat', 'chart': 'bar',
                    'sql': ("CASE WHEN NOT EXISTS (SELECT 1 FROM platform_plan pl "
                            "WHERE pl.patient_no=p.patient_no AND pl.active=1) THEN '无在随计划' "
                            "WHEN EXISTS (SELECT 1 FROM platform_plan pl WHERE pl.patient_no=p.patient_no "
                            "AND pl.active=1 AND pl.next_due < CURDATE()) THEN '已超窗' "
                            "ELSE '按期' END")},
}


def platform_stats(body):
    """§4.6(2) 对检索结果做单维/多维统计。{conditions?, dims:[...], params?}"""
    dims = body.get('dims') or ['gender']
    if not isinstance(dims, list) or not dims:
        return None, 'dims 必须是非空数组'
    if len(dims) > 6:
        return None, 'dims 最多 6 个'
    for d in dims:
        if d not in STAT_DIMS:
            return None, '未知的统计维度: {!r}'.format(d)[:120]

    where, params = '1=1', []
    if body.get('conditions'):
        try:
            where, params = build_search_sql(body['conditions'])
        except ValueError as e:
            return None, str(e)
    extra = body.get('params') or {}

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM platform_patient p WHERE {}'.format(where), params)
        total = cur.fetchone()[0]
        charts = []
        for d in dims:
            spec = STAT_DIMS[d]
            pre = []
            for need in (spec.get('sql_params') or []):
                v = str(extra.get(need) or '').strip()
                if not v:
                    cur.close()
                    return None, '统计维度「{}」还需要参数 {}'.format(spec['label'], need)
                pre.append(v)
            cur.execute('SELECT {} AS k, COUNT(*) AS n FROM platform_patient p WHERE {} '
                        'GROUP BY k ORDER BY {}'.format(
                            spec['sql'], where, 'k' if spec['kind'] == 'time' else 'n DESC'),
                        pre + params)
            rows = [{'label': spec.get('map', {}).get(k, k) if k is not None else '未填',
                     'value': n} for k, n in cur.fetchall()]
            charts.append({'dim': d, 'label': spec['label'], 'chart': spec['chart'],
                           'kind': spec['kind'], 'data': rows,
                           'total': sum(r['value'] for r in rows)})
        cur.close()
        return {'ok': True, 'matched_patients': total, 'charts': charts}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def search_field_catalog():
    """把可用的检索字段与统计维度交给前端 —— 前端不该自己硬编码一份, 那会和后端漂移。"""
    fields = []
    for k, v in SEARCH_FIELDS.items():
        fields.append({'field': k, 'label': v['label'], 'type': v['type'],
                       'group': v.get('group', '其他'), 'ops': list(SEARCH_OPS[v['type']]),
                       'needs': v.get('needs') or [], 'options': v.get('options')})
    dims = [{'dim': k, 'label': v['label'], 'chart': v['chart'],
             'needs': v.get('sql_params') or []} for k, v in STAT_DIMS.items()]
    return {'ok': True, 'fields': fields, 'dims': dims,
            'ops': {k: list(v) for k, v in SEARCH_OPS.items()},
            'limits': {'max_nodes': SEARCH_MAX_NODES, 'max_depth': SEARCH_MAX_DEPTH}}


# ============ 随访平台 1.1 M17 (知情同意与项目资料, 方案 §2.4) ============
#
# 两条必须先说清楚的边界, 否则这块做出来会给人错误的安全感:
#
# 1) **这不是《电子签名法》意义上的"可靠电子签名"。**
#    法律上的可靠电子签名要求: 制作数据由签名人专有控制、签署后对签名和文件的
#    任何改动都能被发现 —— 实务上靠 CA 数字证书实现。这里做的是**签署留痕**:
#    谁、什么时候、从哪个 IP/设备、签的是哪一版(内容哈希)、手写签名图。
#    它对内部流程管理和事后追溯是够用的, 但**不能拿去当法律证据**。
#    要有法律效力必须接第三方 CA。这一条在接口注释、前端界面、对照表三处都写明。
#
# 2) 签署记录必须钉住**内容哈希**, 不能只记文件 id。
#    只记 id 的话, 有人替换了那份 PDF, 已有的签名就"覆盖"了不同的内容 ——
#    而且看不出来。存 sha256 之后, 换了文件一比对就知道签的不是这一版。
#    这是这块唯一真正有价值的完整性属性, 也是最便宜的。
#
# 3) 签好的知情同意书含姓名、身份证号、手写签名, 比平台现在暴露的门诊号严重得多。
#    所以**文件下载要带写接口口令**, 尽管它是个读操作。这个不对称是刻意的。

DOC_TYPES = {
    'consent':   '知情同意书',
    'protocol':  '研究方案',
    'ethics':    '伦理批件',
    'sop':       'SOP 文件',
    'guideline': '诊疗规范',
    'other':     '其他资料',
}
# 扩展名白名单。明确排掉 html/htm/svg/js —— 这些从服务器发回去时浏览器可能当成
# 可执行内容渲染, 一份上传的 svg 里塞段脚本就是存储型 XSS。
DOC_ALLOWED_EXT = ('pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
                   'jpg', 'jpeg', 'png', 'txt', 'md', 'csv', 'zip')
DOC_MAX_BYTES = 30 * 1024 * 1024
DOC_DIR = os.environ.get('PLATFORM_DOC_DIR') or '/opt/suifang/uploads'

CONSENT_SIGNER_ROLES = {'patient': '受试者本人', 'guardian': '监护人/法定代理人',
                        'witness': '见证人', 'investigator': '研究者'}
CONSENT_DISCLAIMER = ('本签署记录为流程留痕(签署人、时间、来源、文件内容哈希与手写签名图), '
                      '不是《电子签名法》意义上的可靠电子签名。可靠电子签名需第三方 CA 数字证书, '
                      '本平台尚未接入 —— 本记录可用于内部追溯, 不能作为法律证据。')


def _doc_safe_ext(filename):
    """从原始文件名里取扩展名。**只取扩展名, 原始文件名一个字都不落到磁盘上** ——
    用户可以在文件名里塞 ../../etc/passwd, 也可以塞超长名/控制字符。"""
    ext = str(filename or '').rsplit('.', 1)[-1].lower() if '.' in str(filename or '') else ''
    ext = re.sub(r'[^a-z0-9]', '', ext)[:8]
    return ext if ext in DOC_ALLOWED_EXT else None


def ensure_platform_doc_tables():
    """M17: 项目资料表 + 签署记录表 + 留痕表 (idempotent)。"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_document (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                code VARCHAR(64) NOT NULL,
                version VARCHAR(32) NOT NULL DEFAULT '1',
                title VARCHAR(200) NOT NULL,
                doc_type VARCHAR(24) NOT NULL DEFAULT 'other',
                category VARCHAR(64) DEFAULT NULL COMMENT '项目/病种',
                orig_name VARCHAR(255) DEFAULT NULL COMMENT '上传时的原始文件名, 仅供显示',
                stored_name VARCHAR(128) NOT NULL COMMENT '磁盘上的名字, 由服务端生成',
                ext VARCHAR(8) NOT NULL,
                size_bytes BIGINT NOT NULL,
                sha256 CHAR(64) NOT NULL COMMENT '内容哈希: 签署记录钉的就是它',
                uploader VARCHAR(64) DEFAULT NULL,
                scope ENUM('private','shared') DEFAULT 'private',
                status ENUM('active','superseded','archived') DEFAULT 'active',
                note VARCHAR(500) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uk_doc_code_version (code, version),
                INDEX idx_type (doc_type),
                INDEX idx_status (status),
                INDEX idx_sha (sha256)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M17 项目资料 (含知情同意书模板)'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_consent (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                doc_code VARCHAR(64) NOT NULL,
                doc_version VARCHAR(32) NOT NULL,
                doc_sha256 CHAR(64) NOT NULL COMMENT '签署当时那份文件的内容哈希 —— 只记 doc_id 的话
                    有人换了 PDF, 这个签名就"覆盖"了不同的内容, 而且看不出来',
                patient_no VARCHAR(64) NOT NULL,
                signer_name VARCHAR(64) NOT NULL,
                signer_role VARCHAR(16) NOT NULL DEFAULT 'patient',
                signature_png MEDIUMTEXT DEFAULT NULL COMMENT '手写签名图 data URI',
                signed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                source_ip VARCHAR(64) DEFAULT NULL,
                user_agent VARCHAR(300) DEFAULT NULL,
                status ENUM('signed','revoked') DEFAULT 'signed',
                revoked_by VARCHAR(64) DEFAULT NULL,
                revoked_at DATETIME DEFAULT NULL,
                revoke_reason VARCHAR(500) DEFAULT NULL,
                note VARCHAR(500) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_patient (patient_no),
                INDEX idx_doc (doc_code, doc_version),
                INDEX idx_status (status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
              COMMENT='随访平台 M17 知情同意签署留痕 (非可靠电子签名, 见 CONSENT_DISCLAIMER)'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_document_log (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                doc_code VARCHAR(64) DEFAULT NULL,
                doc_version VARCHAR(32) DEFAULT NULL,
                consent_id BIGINT DEFAULT NULL,
                action VARCHAR(24) NOT NULL COMMENT 'upload/new_version/download/sign/revoke/archive',
                operator VARCHAR(64) DEFAULT NULL,
                detail VARCHAR(500) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_doc (doc_code, doc_version),
                INDEX idx_consent (consent_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M17 资料与签署留痕 (只增不改)'
        """)
        print('[启动] platform_document / platform_consent / platform_document_log 表已就绪')
        cur.close()
    except Exception as e:
        print('[启动] ensure_platform_doc_tables 失败:', e)
    finally:
        conn.close()


def upload_document(body):
    """上传一份项目资料。{title, doc_type, filename, content_base64, code?, version?,
       category?, uploader?, scope?, note?}

    同 code 再传一份 = 新版本, 旧版自动置 superseded 但**文件不删** ——
    伦理批件这类东西, "当时用的是哪一版"本身就是要留档的信息。
    """
    title = str(body.get('title') or '').strip()
    doc_type = body.get('doc_type') or 'other'
    if not title:
        return None, 'title 必填'
    if doc_type not in DOC_TYPES:
        return None, 'doc_type 必须是 {} 之一'.format('/'.join(DOC_TYPES))
    ext = _doc_safe_ext(body.get('filename'))
    if not ext:
        return None, '只接受这些格式: {}。html/svg/js 等被刻意排除 —— 它们从服务器发回时可能被当作可执行内容渲染'.format(
            '/'.join(DOC_ALLOWED_EXT))
    b64 = body.get('content_base64') or ''
    try:
        import base64 as _b64
        raw = _b64.b64decode(b64)
    except Exception:
        return None, 'content_base64 不是合法 base64'
    if not raw:
        return None, '文件是空的'
    if len(raw) > DOC_MAX_BYTES:
        return None, '文件超过 {}MB'.format(DOC_MAX_BYTES // 1024 // 1024)

    import hashlib
    sha = hashlib.sha256(raw).hexdigest()
    code = re.sub(r'[^0-9A-Za-z_\-]', '', str(body.get('code') or ''))[:64] or \
        'DOC' + sha[:8].upper()

    ensure_platform_doc_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        version = str(body.get('version') or '').strip()
        if not version:
            cur.execute('SELECT version FROM platform_document WHERE code=%s '
                        'ORDER BY id DESC LIMIT 1', (code,))
            row = cur.fetchone()
            version = _bump_version(row[0]) if row else '1'
        cur.execute('SELECT id FROM platform_document WHERE code=%s AND version=%s', (code, version))
        if cur.fetchone():
            cur.close()
            return None, '{} 的版本 {} 已存在。不指定 version 时会自动递增'.format(code, version)

        # 磁盘上的名字完全由服务端生成, 与用户提供的文件名无关 —— 路径穿越、
        # 超长名、控制字符、同名覆盖这几类问题一次性都没有了。
        stored = '{}_{}_{}.{}'.format(code, version, sha[:12], ext)
        try:
            if not os.path.isdir(DOC_DIR):
                os.makedirs(DOC_DIR)
            with open(os.path.join(DOC_DIR, stored), 'wb') as f:
                f.write(raw)
        except OSError as e:
            cur.close()
            return None, '文件写入失败({}): {}'.format(DOC_DIR, e)

        cur.execute("""
            INSERT INTO platform_document
              (code, version, title, doc_type, category, orig_name, stored_name, ext,
               size_bytes, sha256, uploader, scope, status, note)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s)
        """, (code, version, title, doc_type, body.get('category') or None,
              str(body.get('filename') or '')[:255], stored, ext, len(raw), sha,
              body.get('uploader') or None, body.get('scope') or 'private',
              body.get('note') or None))
        cur.execute("UPDATE platform_document SET status='superseded' "
                    "WHERE code=%s AND version<>%s AND status='active'", (code, version))
        cur.execute("""INSERT INTO platform_document_log (doc_code, doc_version, action, operator, detail)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (code, version, 'new_version' if version != '1' else 'upload',
                     body.get('uploader') or None,
                     '{} · {} 字节 · sha256 {}'.format(title, len(raw), sha[:16])))
        cur.close()
        return {'code': code, 'version': version, 'sha256': sha, 'size_bytes': len(raw),
                'ext': ext, 'stored_name': stored}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def query_documents(doc_type=None, code=None, category=None, status=None,
                    with_log=False, limit=200):
    """项目资料列表。不返回文件内容, 只返回元信息 —— 内容走单独的下载接口(带门禁)。"""
    ensure_platform_doc_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ['1=1'], []
        if code:
            where.append('d.code=%s'); params.append(code)
        if doc_type:
            where.append('d.doc_type=%s'); params.append(doc_type)
        if category:
            where.append('d.category=%s'); params.append(category)
        if status:
            where.append('d.status=%s'); params.append(status)
        params.append(int(limit))
        cur.execute("""
            SELECT d.id, d.code, d.version, d.title, d.doc_type, d.category, d.orig_name,
                   d.ext, d.size_bytes, d.sha256, d.uploader, d.scope, d.status, d.note,
                   d.created_at,
                   (SELECT COUNT(*) FROM platform_consent c
                     WHERE c.doc_code=d.code AND c.doc_version=d.version AND c.status='signed') AS signed_count
            FROM platform_document d WHERE {}
            ORDER BY d.doc_type, d.code, d.id DESC LIMIT %s
        """.format(' AND '.join(where)), params)
        cols = ['id', 'code', 'version', 'title', 'doc_type', 'category', 'orig_name', 'ext',
                'size_bytes', 'sha256', 'uploader', 'scope', 'status', 'note', 'created_at',
                'signed_count']
        out = []
        for row in cur.fetchall():
            r = dict(zip(cols, row))
            if r.get('created_at') is not None and hasattr(r['created_at'], 'strftime'):
                r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            r['doc_type_label'] = DOC_TYPES.get(r['doc_type'], r['doc_type'])
            r['sha256_short'] = (r.get('sha256') or '')[:16]
            out.append(r)
        if with_log and code:
            cur.execute("""SELECT action, operator, detail, created_at FROM platform_document_log
                           WHERE doc_code=%s ORDER BY id""", (code,))
            logs = [{'action': a, 'operator': o, 'detail': d,
                     'at': c.strftime('%Y-%m-%d %H:%M:%S') if hasattr(c, 'strftime') else c}
                    for a, o, d, c in cur.fetchall()]
            for r in out:
                r['log'] = logs
        cur.close()
        return {'ok': True, 'count': len(out), 'documents': out,
                'doc_types': DOC_TYPES, 'allowed_ext': list(DOC_ALLOWED_EXT),
                'max_mb': DOC_MAX_BYTES // 1024 // 1024}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def fetch_document_bytes(code, version=None):
    """取一份资料的字节。返回 (bytes, meta, error)。

    读文件前重算一次 sha256 和库里比对 —— 磁盘上的文件被换掉/损坏时要立刻发现,
    而不是把一份不知道是什么的东西发出去。
    """
    if not code:
        return None, None, 'code 必填'
    ensure_platform_doc_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        if version:
            cur.execute('SELECT stored_name, orig_name, ext, sha256, title, size_bytes '
                        'FROM platform_document WHERE code=%s AND version=%s', (code, str(version)))
        else:
            cur.execute('SELECT stored_name, orig_name, ext, sha256, title, size_bytes '
                        'FROM platform_document WHERE code=%s ORDER BY id DESC LIMIT 1', (code,))
        row = cur.fetchone()
        cur.close()
        if not row:
            return None, None, '资料不存在: {}'.format(code)
        stored, orig, ext, sha, title, size = row
        # basename 兜底: stored_name 是服务端生成的, 正常不含分隔符; 万一库被改过也不越出目录
        path = os.path.join(DOC_DIR, os.path.basename(stored))
        if not os.path.isfile(path):
            return None, None, '文件在磁盘上找不到({}) —— 库里有记录但文件丢了'.format(stored)
        with open(path, 'rb') as f:
            raw = f.read()
        import hashlib
        actual = hashlib.sha256(raw).hexdigest()
        if actual != sha:
            return None, None, ('文件内容哈希与入库时不一致(库 {} / 实际 {}) —— '
                                '文件可能被替换或损坏, 已拒绝下发'.format(sha[:16], actual[:16]))
        return raw, {'orig_name': orig, 'ext': ext, 'title': title,
                     'sha256': sha, 'size_bytes': size}, None
    except Exception as e:
        traceback.print_exc()
        return None, None, str(e)
    finally:
        conn.close()


def sign_consent(body, source_ip=None, user_agent=None):
    """记录一次知情同意签署。{doc_code, patient_no, signer_name, signer_role?,
       doc_version?, signature_png?, note?}

    再说一次: 这是**签署留痕**, 不是可靠电子签名。见 CONSENT_DISCLAIMER。
    """
    code = str(body.get('doc_code') or '').strip()
    patient_no = str(body.get('patient_no') or '').strip()
    signer = str(body.get('signer_name') or '').strip()
    if not code or not patient_no or not signer:
        return None, 'doc_code / patient_no / signer_name 都必填'
    role = body.get('signer_role') or 'patient'
    if role not in CONSENT_SIGNER_ROLES:
        return None, 'signer_role 必须是 {} 之一'.format('/'.join(CONSENT_SIGNER_ROLES))
    png = body.get('signature_png')
    if png and (not isinstance(png, str) or not png.startswith('data:image/png;base64,')):
        return None, 'signature_png 必须是 data:image/png;base64, 开头的 data URI'
    if png and len(png) > 2 * 1024 * 1024:
        return None, '签名图过大(超过 2MB)'

    ensure_platform_doc_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        if body.get('doc_version'):
            cur.execute('SELECT version, sha256, doc_type, title FROM platform_document '
                        'WHERE code=%s AND version=%s', (code, str(body['doc_version'])))
        else:
            cur.execute("SELECT version, sha256, doc_type, title FROM platform_document "
                        "WHERE code=%s AND status='active' ORDER BY id DESC LIMIT 1", (code,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, '知情同意书不存在或已归档: {}'.format(code)
        version, sha, doc_type, title = row
        if doc_type != 'consent':
            cur.close()
            return None, ('{} 的类型是「{}」, 不是知情同意书 —— 签署只对知情同意书有意义, '
                          '给研究方案盖个签名不构成任何东西'.format(code, DOC_TYPES.get(doc_type, doc_type)))
        cur.execute("SELECT id FROM platform_consent WHERE doc_code=%s AND doc_version=%s "
                    "AND patient_no=%s AND signer_role=%s AND status='signed'",
                    (code, version, patient_no, role))
        dup = cur.fetchone()
        if dup and not body.get('allow_resign'):
            cur.close()
            return {'ok': False, 'signed': False, 'existing_id': dup[0],
                    'hint': '该受试者已以「{}」身份签署过本版本(记录 #{})。'
                            '确需重签请带 allow_resign=true —— 重签会新增一条记录, '
                            '旧记录不删除'.format(CONSENT_SIGNER_ROLES[role], dup[0])}, None

        cur.execute("""
            INSERT INTO platform_consent
              (doc_code, doc_version, doc_sha256, patient_no, signer_name, signer_role,
               signature_png, source_ip, user_agent, status, note)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'signed',%s)
        """, (code, version, sha, patient_no, signer[:64], role, png,
              (source_ip or '')[:64] or None, (user_agent or '')[:300] or None,
              body.get('note') or None))
        cid = cur.lastrowid
        cur.execute("""INSERT INTO platform_document_log
                       (doc_code, doc_version, consent_id, action, operator, detail)
                       VALUES (%s,%s,%s,'sign',%s,%s)""",
                    (code, version, cid, signer[:64],
                     '{} 以「{}」身份签署《{}》v{} (内容哈希 {})'.format(
                         patient_no, CONSENT_SIGNER_ROLES[role], title, version, sha[:16])))
        cur.close()
        return {'ok': True, 'signed': True, 'id': cid, 'doc_code': code,
                'doc_version': version, 'doc_sha256': sha,
                'disclaimer': CONSENT_DISCLAIMER}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def revoke_consent(body):
    """撤回一份签署 {id, operator, reason}。不删记录, 只标 revoked。"""
    try:
        cid = int(body.get('id'))
    except (TypeError, ValueError):
        return None, 'id 必填且为整数'
    reason = str(body.get('reason') or '').strip()
    if not reason:
        return None, 'reason 必填 —— 撤回知情同意是件大事, 必须写清楚为什么'
    ensure_platform_doc_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT status, doc_code, doc_version, patient_no FROM platform_consent WHERE id=%s', (cid,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, '签署记录不存在: {}'.format(cid)
        if row[0] == 'revoked':
            cur.close()
            return None, '该记录已经是撤回状态'
        cur.execute("""UPDATE platform_consent SET status='revoked', revoked_by=%s,
                       revoked_at=NOW(), revoke_reason=%s WHERE id=%s""",
                    (body.get('operator') or None, reason[:500], cid))
        cur.execute("""INSERT INTO platform_document_log
                       (doc_code, doc_version, consent_id, action, operator, detail)
                       VALUES (%s,%s,%s,'revoke',%s,%s)""",
                    (row[1], row[2], cid, body.get('operator') or None,
                     '撤回 {} 的签署: {}'.format(row[3], reason[:200])))
        cur.close()
        return {'ok': True, 'id': cid, 'status': 'revoked'}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def query_consents(patient_no=None, doc_code=None, status=None,
                   with_signature=False, limit=200):
    """签署记录查询。默认不带签名图 —— 那是几十 KB 的 base64, 列表页不需要。"""
    ensure_platform_doc_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ['1=1'], []
        if patient_no:
            where.append('c.patient_no=%s'); params.append(patient_no)
        if doc_code:
            where.append('c.doc_code=%s'); params.append(doc_code)
        if status:
            where.append('c.status=%s'); params.append(status)
        params.append(int(limit))
        cols = ('c.id, c.doc_code, c.doc_version, c.doc_sha256, c.patient_no, p.name, '
                'c.signer_name, c.signer_role, c.signed_at, c.source_ip, c.status, '
                'c.revoked_by, c.revoked_at, c.revoke_reason, c.note, d.title, '
                "(c.doc_sha256 = COALESCE(d.sha256,'')) AS hash_matches")
        if with_signature:
            cols += ', c.signature_png'
        cur.execute("""
            SELECT {} FROM platform_consent c
            LEFT JOIN platform_patient p ON p.patient_no=c.patient_no
            LEFT JOIN platform_document d ON d.code=c.doc_code AND d.version=c.doc_version
            WHERE {} ORDER BY c.signed_at DESC LIMIT %s
        """.format(cols, ' AND '.join(where)), params)
        names = [x[0] for x in cur.description]
        out = []
        for row in cur.fetchall():
            r = dict(zip(names, row))
            for k in ('signed_at', 'revoked_at'):
                if r.get(k) is not None and hasattr(r[k], 'strftime'):
                    r[k] = r[k].strftime('%Y-%m-%d %H:%M:%S')
            r['signer_role_label'] = CONSENT_SIGNER_ROLES.get(r.get('signer_role'), r.get('signer_role'))
            r['doc_sha256_short'] = (r.get('doc_sha256') or '')[:16]
            # 库里那份文件现在的哈希和签署时对不上 = 文件被换过。这是这块最该报出来的事。
            r['hash_matches'] = bool(r.get('hash_matches'))
            if not r['hash_matches']:
                r['integrity_warning'] = ('签署时的文件哈希与该版本当前的哈希不一致 —— '
                                          '文件在签署之后被替换过, 这份签名已不能证明签的是现在这一版')
            out.append(r)
        cur.close()
        return {'ok': True, 'count': len(out), 'consents': out,
                'roles': CONSENT_SIGNER_ROLES, 'disclaimer': CONSENT_DISCLAIMER}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


# ============ 随访平台 1.1 M18 (纳排规则与分组配置, 方案 §3.2) ============
#
# 纳排条件直接复用 M16 的条件树(build_search_sql) —— 不另起一套。好处不只是省事:
# M16 那套的字段白名单和参数化是唯一的注入防线, 再写一套等于再开一个口子。
#
# 这块的支点是方案 §3.2(2) 那句 "分组条件变更时, 可自主配置是否保留既有患者"。
# 它背后是一件比听起来严重得多的事:
#
#   **把一个已入组患者从试验组挪到对照组, 不是数据更新, 是方案偏离。**
#
# 患者已经按原分组接受了干预、填了基线、走了几次随访。悄悄改掉他的组别, 那些数据
# 就挂到了错误的臂上, 而分析时没有任何迹象能看出来 —— 这是能让整个研究作废的事。
# 所以这里的默认行为是**保留**: 规则改了只影响此后新入组的人, 既有患者纹丝不动;
# 要动他们必须显式 regroup_existing=true, 并且每一次移动都单独记一条留痕带原因。
# 所有会移动人的操作默认 dry_run, 先告诉你会动几个人再说。

GROUP_KINDS = {'control': '对照组', 'experiment': '试验组',
               'routine': '常规管理组', 'other': '其他分组'}
ENROLL_STATUSES = {'enrolled': '已入组', 'screened_out': '筛查排除', 'withdrawn': '已退出'}


def ensure_platform_cohort_tables():
    """M18: 纳排方案 + 分组 + 入组归属 + 留痕 (idempotent)。"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_cohort (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                code VARCHAR(64) NOT NULL UNIQUE,
                name VARCHAR(128) NOT NULL,
                disease VARCHAR(64) DEFAULT NULL COMMENT '病种/科研项目',
                include_rule JSON DEFAULT NULL COMMENT '纳入条件(M16 条件树)',
                exclude_rule JSON DEFAULT NULL COMMENT '排除条件(M16 条件树)',
                owner VARCHAR(64) DEFAULT NULL,
                status ENUM('draft','running','closed') DEFAULT 'draft',
                note VARCHAR(500) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_status (status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M18 纳排方案'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_group (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                cohort_code VARCHAR(64) NOT NULL,
                code VARCHAR(64) NOT NULL,
                name VARCHAR(128) NOT NULL,
                kind VARCHAR(16) DEFAULT 'other',
                match_rule JSON DEFAULT NULL COMMENT '分组条件; 为空 = 兜底组(前面都不匹配的落这里)',
                priority INT NOT NULL DEFAULT 100 COMMENT '数字小的先判; 一个患者只落第一个命中的组',
                plan_template VARCHAR(64) DEFAULT NULL COMMENT '入组后随访流程(留给 §3.3)',
                screen_note VARCHAR(500) DEFAULT NULL COMMENT '入组前筛查阶段说明',
                push_policy JSON DEFAULT NULL COMMENT '面向分组的消息推送策略',
                target_n INT DEFAULT NULL COMMENT '计划样本量',
                active TINYINT(1) DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uk_group (cohort_code, code),
                INDEX idx_cohort (cohort_code, priority)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M18 分组'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_enrollment (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                cohort_code VARCHAR(64) NOT NULL,
                patient_no VARCHAR(64) NOT NULL,
                group_code VARCHAR(64) DEFAULT NULL,
                status ENUM('enrolled','screened_out','withdrawn') DEFAULT 'enrolled',
                assigned_by ENUM('auto','manual') DEFAULT 'auto',
                assign_reason VARCHAR(300) DEFAULT NULL,
                enrolled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uk_enroll (cohort_code, patient_no),
                INDEX idx_group (cohort_code, group_code),
                INDEX idx_status (status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M18 入组归属 (一个方案里一个患者只有一条)'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_enrollment_log (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                cohort_code VARCHAR(64) NOT NULL,
                patient_no VARCHAR(64) DEFAULT NULL,
                action VARCHAR(24) NOT NULL COMMENT 'enroll/regroup/screen_out/withdraw/rule_change',
                from_group VARCHAR(64) DEFAULT NULL,
                to_group VARCHAR(64) DEFAULT NULL,
                operator VARCHAR(64) DEFAULT NULL,
                reason VARCHAR(500) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_cohort (cohort_code, id),
                INDEX idx_patient (patient_no)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
              COMMENT='随访平台 M18 入组与改组留痕 (只增不改; 改组=方案偏离, 必须查得到)'
        """)
        print('[启动] platform_cohort / platform_group / platform_enrollment 表已就绪')
        cur.close()
    except Exception as e:
        print('[启动] ensure_platform_cohort_tables 失败:', e)
    finally:
        conn.close()


def _rule_eq(a, b):
    """比两份规则是否相同。

    MySQL 的 JSON 列取回来可能是 str 也可能已经是 dict(看驱动版本), 直接拿
    json.dumps 比会永远判成"改过了" —— 那样每次保存都报一次"规则已变更",
    真正改了的时候反而没人信。这里先归一化再比。
    """
    def norm(x):
        if isinstance(x, str):
            try:
                x = json.loads(x) if x.strip() else None
            except ValueError:
                pass
        return json.dumps(x, sort_keys=True, ensure_ascii=False) if x else ''
    return norm(a) == norm(b)


def _validate_rule(rule, label):
    """条件树在**保存时**就校验, 不等到跑的时候才炸。

    存一份引用了不存在字段的规则进去, 表面上一切正常, 直到某天有人点"执行入组"
    才报错 —— 那时候方案可能已经挂在墙上了。
    """
    if rule in (None, {}, []):
        return None
    try:
        build_search_sql(rule)
    except ValueError as e:
        return '{}有问题: {}'.format(label, e)
    return None


def upsert_cohort(body):
    """建/改纳排方案。{code, name, disease?, include_rule?, exclude_rule?, owner?, status?}"""
    code = re.sub(r'[^0-9A-Za-z_\-]', '', str(body.get('code') or ''))[:64]
    name = str(body.get('name') or '').strip()
    if not code or not name:
        return None, 'code 和 name 必填 (code 只接受字母数字下划线连字符)'
    status = body.get('status') or 'draft'
    if status not in ('draft', 'running', 'closed'):
        return None, 'status 必须是 draft/running/closed'
    for rule, label in ((body.get('include_rule'), '纳入条件'), (body.get('exclude_rule'), '排除条件')):
        err = _validate_rule(rule, label)
        if err:
            return None, err

    ensure_platform_cohort_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT include_rule, exclude_rule FROM platform_cohort WHERE code=%s', (code,))
        old = cur.fetchone()
        cur.execute("""
            INSERT INTO platform_cohort (code, name, disease, include_rule, exclude_rule, owner, status, note)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE name=VALUES(name), disease=VALUES(disease),
              include_rule=VALUES(include_rule), exclude_rule=VALUES(exclude_rule),
              owner=VALUES(owner), status=VALUES(status), note=VALUES(note)
        """, (code, name, body.get('disease') or None,
              json.dumps(body.get('include_rule') or None, ensure_ascii=False)
              if body.get('include_rule') else None,
              json.dumps(body.get('exclude_rule') or None, ensure_ascii=False)
              if body.get('exclude_rule') else None,
              body.get('owner') or None, status, body.get('note') or None))
        changed = bool(old) and (not _rule_eq(old[0], body.get('include_rule'))
                                 or not _rule_eq(old[1], body.get('exclude_rule')))
        if changed:
            cur.execute("""INSERT INTO platform_enrollment_log (cohort_code, action, operator, reason)
                           VALUES (%s,'rule_change',%s,%s)""",
                        (code, body.get('owner') or None, '纳排条件被修改'))
        cur.execute("SELECT COUNT(*) FROM platform_enrollment WHERE cohort_code=%s AND status='enrolled'",
                    (code,))
        enrolled = cur.fetchone()[0]
        cur.close()
        out = {'code': code, 'name': name, 'status': status, 'enrolled_count': enrolled}
        if changed and enrolled:
            out['warning'] = ('纳排条件已改, 而本方案已有 {} 人在组。**既有患者不受影响** —— '
                              '新条件只作用于此后的入组评估。要重新评估既有患者, '
                              '请显式执行入组并带 regroup_existing=true'.format(enrolled))
        return out, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def upsert_group(body):
    """建/改分组。{cohort_code, code, name, kind?, match_rule?, priority?, ...}

    match_rule 为空 = 兜底组: 前面所有分组都不匹配的人落到这里。
    一个方案里应当只有一个兜底组, 多了就说明规则没想清楚 —— 这里会报出来。
    """
    cohort = str(body.get('cohort_code') or '').strip()
    code = re.sub(r'[^0-9A-Za-z_\-]', '', str(body.get('code') or ''))[:64]
    name = str(body.get('name') or '').strip()
    if not cohort or not code or not name:
        return None, 'cohort_code / code / name 必填'
    kind = body.get('kind') or 'other'
    if kind not in GROUP_KINDS:
        return None, 'kind 必须是 {} 之一'.format('/'.join(GROUP_KINDS))
    err = _validate_rule(body.get('match_rule'), '分组条件')
    if err:
        return None, err
    try:
        priority = int(body.get('priority') if body.get('priority') is not None else 100)
    except (TypeError, ValueError):
        return None, 'priority 必须是整数'

    ensure_platform_cohort_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT 1 FROM platform_cohort WHERE code=%s', (cohort,))
        if not cur.fetchone():
            cur.close()
            return None, '纳排方案不存在: {}'.format(cohort)
        cur.execute('SELECT match_rule FROM platform_group WHERE cohort_code=%s AND code=%s',
                    (cohort, code))
        old = cur.fetchone()
        cur.execute("""
            INSERT INTO platform_group (cohort_code, code, name, kind, match_rule, priority,
                                        plan_template, screen_note, push_policy, target_n, active)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE name=VALUES(name), kind=VALUES(kind),
              match_rule=VALUES(match_rule), priority=VALUES(priority),
              plan_template=VALUES(plan_template), screen_note=VALUES(screen_note),
              push_policy=VALUES(push_policy), target_n=VALUES(target_n), active=VALUES(active)
        """, (cohort, code, name, kind,
              json.dumps(body.get('match_rule'), ensure_ascii=False) if body.get('match_rule') else None,
              priority, body.get('plan_template') or None, body.get('screen_note') or None,
              json.dumps(body.get('push_policy') or None, ensure_ascii=False)
              if body.get('push_policy') else None,
              body.get('target_n'), 0 if body.get('active') in (0, False, '0') else 1))

        warnings = []
        cur.execute("SELECT code, name FROM platform_group WHERE cohort_code=%s "
                    "AND match_rule IS NULL AND active=1", (cohort,))
        fallbacks = cur.fetchall()
        if len(fallbacks) > 1:
            warnings.append('本方案有 {} 个兜底组({}) —— 兜底组不设条件, 谁在前面谁把人全收走, '
                            '后面的永远分不到人。应当只保留一个'.format(
                                len(fallbacks), '、'.join(x[1] for x in fallbacks)))
        cur.execute("SELECT priority, COUNT(*) FROM platform_group WHERE cohort_code=%s AND active=1 "
                    "GROUP BY priority HAVING COUNT(*)>1", (cohort,))
        dup = cur.fetchall()
        if dup:
            warnings.append('有 {} 组分组的 priority 相同 —— 同优先级时谁先命中取决于数据库返回顺序, '
                            '也就是说同一个患者可能这次分到 A 组、下次分到 B 组。请把优先级改成互不相同'.format(len(dup)))
        rule_changed = bool(old) and not _rule_eq(old[0], body.get('match_rule'))
        if rule_changed:
            # 当场的警告只有正在操作的人看得见。留痕是给三个月后查"这些人为什么换组"的人看的。
            cur.execute("""INSERT INTO platform_enrollment_log
                           (cohort_code, action, from_group, to_group, operator, reason)
                           VALUES (%s,'rule_change',%s,%s,%s,%s)""",
                        (cohort, code, code, body.get('owner') or body.get('operator') or None,
                         '分组「{}」的匹配条件被修改'.format(name)))
            cur.execute("SELECT COUNT(*) FROM platform_enrollment WHERE cohort_code=%s "
                        "AND group_code=%s AND status='enrolled'", (cohort, code))
            n = cur.fetchone()[0]
            if n:
                warnings.append('分组条件已改, 本组现有 {} 人。**他们不会被自动挪走** —— '
                                '把已入组患者从一个臂挪到另一个臂是方案偏离, 不是数据更新。'
                                '确需重新分组请执行入组时带 regroup_existing=true'.format(n))
        cur.close()
        return {'cohort_code': cohort, 'code': code, 'name': name, 'priority': priority,
                'warnings': warnings}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def _cohort_eligible_sql(inc, exc):
    """纳入 AND NOT 排除。返回 (where, params)。"""
    parts, params = [], []
    if inc:
        s, p = build_search_sql(inc); parts.append(s); params += p
    if exc:
        s, p = build_search_sql(exc); parts.append('NOT (' + s + ')'); params += p
    return (' AND '.join(parts) if parts else '1=1'), params


def cohort_evaluate(cohort_code, limit=500):
    """算一遍谁符合纳排条件, 以及他们各自会落到哪个分组。**只算不写。**"""
    ensure_platform_cohort_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT name, include_rule, exclude_rule FROM platform_cohort WHERE code=%s',
                    (cohort_code,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, '纳排方案不存在: {}'.format(cohort_code)
        name, inc, exc = row
        if isinstance(inc, str):
            inc = json.loads(inc) if inc else None
        if isinstance(exc, str):
            exc = json.loads(exc) if exc else None
        try:
            where, params = _cohort_eligible_sql(inc, exc)
        except ValueError as e:
            cur.close()
            return None, '纳排条件有问题: {}'.format(e)

        cur.execute('SELECT p.patient_no, p.name, p.gender, p.age, p.group_tag '
                    'FROM platform_patient p WHERE {} ORDER BY p.patient_no LIMIT %s'.format(where),
                    params + [int(limit)])
        eligible = [{'patient_no': a, 'name': b, 'gender': c, 'age': d, 'group_tag': e}
                    for a, b, c, d, e in cur.fetchall()]

        cur.execute('SELECT code, name, kind, match_rule, priority FROM platform_group '
                    'WHERE cohort_code=%s AND active=1 ORDER BY priority, code', (cohort_code,))
        groups = []
        for g_code, g_name, kind, rule, pri in cur.fetchall():
            if isinstance(rule, str):
                rule = json.loads(rule) if rule else None
            groups.append({'code': g_code, 'name': g_name, 'kind': kind,
                           'rule': rule, 'priority': pri})

        # 逐个分组算命中集合, 按优先级"先到先得" —— 一个患者只落第一个命中的组。
        # 不这么做的话一个人会同时出现在多个臂里, 而这在临床研究里没有任何意义。
        assigned, by_group = {}, {}
        for g in groups:
            if g['rule']:
                try:
                    gw, gp = build_search_sql(g['rule'])
                except ValueError as e:
                    cur.close()
                    return None, '分组「{}」的条件有问题: {}'.format(g['name'], e)
                cur.execute('SELECT p.patient_no FROM platform_patient p WHERE ({}) AND ({})'.format(
                    where, gw), params + gp)
                hits = [r[0] for r in cur.fetchall()]
            else:
                hits = [p['patient_no'] for p in eligible]      # 兜底组
            fresh = [h for h in hits if h not in assigned]
            for h in fresh:
                assigned[h] = g['code']
            by_group[g['code']] = {'code': g['code'], 'name': g['name'], 'kind': g['kind'],
                                   'kind_label': GROUP_KINDS.get(g['kind'], g['kind']),
                                   'priority': g['priority'], 'is_fallback': not g['rule'],
                                   'matched': len(hits), 'assigned': len(fresh)}

        for p in eligible:
            p['group_code'] = assigned.get(p['patient_no'])
        unassigned = [p['patient_no'] for p in eligible if not p.get('group_code')]

        cur.execute("SELECT patient_no, group_code FROM platform_enrollment "
                    "WHERE cohort_code=%s AND status='enrolled'", (cohort_code,))
        existing = dict(cur.fetchall())
        cur.close()

        would_move = [{'patient_no': k, 'from': v, 'to': assigned.get(k)}
                      for k, v in existing.items()
                      if assigned.get(k) and assigned[k] != v]
        return {'ok': True, 'cohort_code': cohort_code, 'cohort_name': name,
                'eligible': len(eligible), 'patients': eligible,
                'groups': [by_group[g['code']] for g in groups],
                'unassigned': len(unassigned),
                'already_enrolled': len(existing),
                'would_move': would_move,
                'move_warning': ('按当前规则重新评估, 会有 {} 名**已入组**患者被挪到别的组。'
                                 '把患者从一个臂挪到另一个臂是方案偏离 —— 他们已经按原分组接受了干预、'
                                 '填了基线、走了随访, 挪组会让那些数据挂到错误的臂上, 而分析时看不出来。'
                                 '默认不会动他们'.format(len(would_move))) if would_move else None}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def cohort_enroll(body):
    """执行入组。{cohort_code, dry_run?, regroup_existing?, operator?, reason?}

    dry_run 默认 **true** —— 这个操作会改变几十上百人的组别归属, 先看清楚再说。
    regroup_existing 默认 false —— 已入组患者不动, 见本节开头。
    """
    code = str(body.get('cohort_code') or '').strip()
    if not code:
        return None, 'cohort_code 必填'
    dry = body.get('dry_run')
    dry = True if dry is None else bool(dry)
    regroup = bool(body.get('regroup_existing'))
    if regroup and not dry and not str(body.get('reason') or '').strip():
        return None, ('regroup_existing=true 时必须写 reason —— 把已入组患者挪组是方案偏离, '
                      '得说清楚为什么, 这条会进留痕')

    ev, err = cohort_evaluate(code, limit=5000)
    if err:
        return None, err

    if dry:
        return {'ok': True, 'dry_run': True, 'cohort_code': code,
                'eligible': ev['eligible'], 'groups': ev['groups'],
                'already_enrolled': ev['already_enrolled'],
                'would_newly_enroll': ev['eligible'] - ev['already_enrolled'],
                'would_move': ev['would_move'], 'move_warning': ev['move_warning'],
                'unassigned': ev['unassigned'],
                'hint': '这是试算, 没有写库。确认无误后带 dry_run=false 执行'}, None

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT patient_no, group_code FROM platform_enrollment "
                    "WHERE cohort_code=%s AND status='enrolled'", (code,))
        existing = dict(cur.fetchall())
        new_n = moved_n = kept_n = 0
        for p in ev['patients']:
            no, g = p['patient_no'], p.get('group_code')
            if no not in existing:
                cur.execute("""INSERT INTO platform_enrollment
                               (cohort_code, patient_no, group_code, status, assigned_by, assign_reason)
                               VALUES (%s,%s,%s,'enrolled','auto',%s)
                               ON DUPLICATE KEY UPDATE group_code=VALUES(group_code),
                                 status='enrolled', assigned_by='auto'""",
                            (code, no, g, '按纳排规则自动入组'))
                cur.execute("""INSERT INTO platform_enrollment_log
                               (cohort_code, patient_no, action, from_group, to_group, operator, reason)
                               VALUES (%s,%s,'enroll',NULL,%s,%s,%s)""",
                            (code, no, g, body.get('operator') or None, '按纳排规则自动入组'))
                new_n += 1
            elif existing[no] != g:
                if not regroup:
                    kept_n += 1
                    continue
                cur.execute("UPDATE platform_enrollment SET group_code=%s, assign_reason=%s "
                            "WHERE cohort_code=%s AND patient_no=%s",
                            (g, (body.get('reason') or '')[:300], code, no))
                cur.execute("""INSERT INTO platform_enrollment_log
                               (cohort_code, patient_no, action, from_group, to_group, operator, reason)
                               VALUES (%s,%s,'regroup',%s,%s,%s,%s)""",
                            (code, no, existing[no], g, body.get('operator') or None,
                             (body.get('reason') or '')[:500]))
                moved_n += 1
        cur.close()
        return {'ok': True, 'dry_run': False, 'cohort_code': code,
                'newly_enrolled': new_n, 'regrouped': moved_n,
                'kept_unchanged': kept_n, 'groups': ev['groups'],
                'note': ('有 {} 名已入组患者按新规则本应换组, 但因为 regroup_existing 没打开而保持原样 —— '
                         '这是默认且安全的行为'.format(kept_n)) if kept_n else None}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def enrollment_transition(body):
    """手工调整单个患者 {cohort_code, patient_no, action: regroup|screen_out|withdraw|reenroll,
       group_code?, operator?, reason?}"""
    code = str(body.get('cohort_code') or '').strip()
    no = str(body.get('patient_no') or '').strip()
    action = str(body.get('action') or '').strip()
    reason = str(body.get('reason') or '').strip()
    if not code or not no:
        return None, 'cohort_code 和 patient_no 必填'
    if action not in ('regroup', 'screen_out', 'withdraw', 'reenroll'):
        return None, 'action 必须是 regroup/screen_out/withdraw/reenroll'
    if not reason:
        return None, 'reason 必填 —— 手工调整归属必须说明依据, 这条会进留痕'
    if action == 'regroup' and not body.get('group_code'):
        return None, 'regroup 必须给 group_code'

    ensure_platform_cohort_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT group_code, status FROM platform_enrollment '
                    'WHERE cohort_code=%s AND patient_no=%s', (code, no))
        row = cur.fetchone()
        if not row and action != 'reenroll':
            cur.close()
            return None, '该患者不在本方案的入组名单里'
        cur_group = row[0] if row else None
        new_status = {'regroup': 'enrolled', 'screen_out': 'screened_out',
                      'withdraw': 'withdrawn', 'reenroll': 'enrolled'}[action]
        to_group = body.get('group_code') if action in ('regroup', 'reenroll') else cur_group
        if row:
            cur.execute("UPDATE platform_enrollment SET group_code=%s, status=%s, "
                        "assigned_by='manual', assign_reason=%s WHERE cohort_code=%s AND patient_no=%s",
                        (to_group, new_status, reason[:300], code, no))
        else:
            cur.execute("""INSERT INTO platform_enrollment
                           (cohort_code, patient_no, group_code, status, assigned_by, assign_reason)
                           VALUES (%s,%s,%s,%s,'manual',%s)""",
                        (code, no, to_group, new_status, reason[:300]))
        cur.execute("""INSERT INTO platform_enrollment_log
                       (cohort_code, patient_no, action, from_group, to_group, operator, reason)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (code, no, action, cur_group, to_group,
                     body.get('operator') or None, reason[:500]))
        cur.close()
        return {'ok': True, 'cohort_code': code, 'patient_no': no,
                'from_group': cur_group, 'to_group': to_group, 'status': new_status}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def query_cohorts(code=None, with_groups=True, with_log=False, limit=100):
    """纳排方案列表 / 单个详情(含分组与入组统计)。"""
    ensure_platform_cohort_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ['1=1'], []
        if code:
            where.append('c.code=%s'); params.append(code)
        params.append(int(limit))
        cur.execute("""
            SELECT c.id, c.code, c.name, c.disease, c.include_rule, c.exclude_rule,
                   c.owner, c.status, c.note, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM platform_enrollment e
                     WHERE e.cohort_code=c.code AND e.status='enrolled') AS enrolled,
                   (SELECT COUNT(*) FROM platform_group g WHERE g.cohort_code=c.code AND g.active=1) AS group_n
            FROM platform_cohort c WHERE {} ORDER BY c.updated_at DESC LIMIT %s
        """.format(' AND '.join(where)), params)
        names = [d[0] for d in cur.description]
        out = []
        for row in cur.fetchall():
            r = dict(zip(names, row))
            for k in ('created_at', 'updated_at'):
                if r.get(k) is not None and hasattr(r[k], 'strftime'):
                    r[k] = r[k].strftime('%Y-%m-%d %H:%M:%S')
            for k in ('include_rule', 'exclude_rule'):
                if isinstance(r.get(k), str):
                    try:
                        r[k] = json.loads(r[k])
                    except ValueError:
                        pass
            out.append(r)
        if with_groups and out:
            codes = [r['code'] for r in out]
            cur.execute("""
                SELECT g.cohort_code, g.code, g.name, g.kind, g.match_rule, g.priority,
                       g.plan_template, g.screen_note, g.push_policy, g.target_n, g.active,
                       (SELECT COUNT(*) FROM platform_enrollment e
                         WHERE e.cohort_code=g.cohort_code AND e.group_code=g.code
                           AND e.status='enrolled') AS n
                FROM platform_group g WHERE g.cohort_code IN ({})
                ORDER BY g.cohort_code, g.priority, g.code
            """.format(','.join(['%s'] * len(codes))), codes)
            gmap = {}
            for (ck, gc, gn, kind, rule, pri, tpl, sn, push, tgt, act, n) in cur.fetchall():
                if isinstance(rule, str):
                    try:
                        rule = json.loads(rule) if rule else None
                    except ValueError:
                        pass
                if isinstance(push, str):
                    try:
                        push = json.loads(push) if push else None
                    except ValueError:
                        pass
                gmap.setdefault(ck, []).append({
                    'code': gc, 'name': gn, 'kind': kind,
                    'kind_label': GROUP_KINDS.get(kind, kind), 'match_rule': rule,
                    'is_fallback': rule is None, 'priority': pri, 'plan_template': tpl,
                    'screen_note': sn, 'push_policy': push, 'target_n': tgt,
                    'active': act, 'enrolled': n,
                    'progress': (round(n * 100.0 / tgt, 1) if tgt else None)})
            for r in out:
                r['groups'] = gmap.get(r['code'], [])
        if with_log and code and out:
            cur.execute("""SELECT patient_no, action, from_group, to_group, operator, reason, created_at
                           FROM platform_enrollment_log WHERE cohort_code=%s
                           ORDER BY id DESC LIMIT 200""", (code,))
            out[0]['log'] = [{'patient_no': a, 'action': b, 'from': c, 'to': d, 'operator': e,
                              'reason': f,
                              'at': g.strftime('%Y-%m-%d %H:%M:%S') if hasattr(g, 'strftime') else g}
                             for a, b, c, d, e, f, g in cur.fetchall()]
        cur.close()
        return {'ok': True, 'count': len(out), 'cohorts': out, 'kinds': GROUP_KINDS,
                'statuses': ENROLL_STATUSES}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def query_enrollments(cohort_code=None, group_code=None, status=None, limit=500):
    """入组名单。"""
    ensure_platform_cohort_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ['1=1'], []
        if cohort_code:
            where.append('e.cohort_code=%s'); params.append(cohort_code)
        if group_code:
            where.append('e.group_code=%s'); params.append(group_code)
        if status:
            where.append('e.status=%s'); params.append(status)
        params.append(int(limit))
        cur.execute("""
            SELECT e.id, e.cohort_code, e.patient_no, p.name, p.gender, p.age,
                   e.group_code, g.name, g.kind, e.status, e.assigned_by,
                   e.assign_reason, e.enrolled_at
            FROM platform_enrollment e
            LEFT JOIN platform_patient p ON p.patient_no=e.patient_no
            LEFT JOIN platform_group g ON g.cohort_code=e.cohort_code AND g.code=e.group_code
            WHERE {} ORDER BY e.cohort_code, e.group_code, e.patient_no LIMIT %s
        """.format(' AND '.join(where)), params)
        cols = ['id', 'cohort_code', 'patient_no', 'patient_name', 'gender', 'age',
                'group_code', 'group_name', 'group_kind', 'status', 'assigned_by',
                'assign_reason', 'enrolled_at']
        out = []
        for row in cur.fetchall():
            r = dict(zip(cols, row))
            if r.get('enrolled_at') is not None and hasattr(r['enrolled_at'], 'strftime'):
                r['enrolled_at'] = r['enrolled_at'].strftime('%Y-%m-%d %H:%M:%S')
            r['status_label'] = ENROLL_STATUSES.get(r['status'], r['status'])
            r['group_kind_label'] = GROUP_KINDS.get(r.get('group_kind'), r.get('group_kind'))
            out.append(r)
        cur.close()
        return {'ok': True, 'count': len(out), 'enrollments': out}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


# ============ 随访平台 1.1 M19 (个性化随访流程, 方案 §3.3) ============
#
# M5 只有"固定频次/一次性"两种, §3.3 要的是完整的流程编排。三个新概念:
#
# 1) **访视窗口**。"术后 7 天访视"实际是 "第 5~10 天之间做完"。没有窗口就没有
#    "超窗"这个概念, 而方案 §4.5(4) 的访视超窗管理整个建立在它上面。M5 的
#    next_due 是个光秃秃的日期, 早一天晚一天都无从判断。
#
# 2) **流程外阶段**(不良事件、并发症)。它们**不在时间轴上** —— 由事件触发,
#    可以发生零次也可以发生五次。必须和计划内访视分开存, 因为:
#
#    **随访完成率的分母不能包含流程外阶段, 也不能包含尚未到期的访视。**
#
#    把从未发生的不良事件访视算进分母, 完成率永远上不去; 把三个月后才到期的
#    访视算进分母, 早期的完成率永远很低。两种错法都让这个数失去意义 ——
#    而管理层恰恰只看这个数。
#
# 3) **多级层级且层级名可自定义**。方案要"三级以上", 且"各级流程名称可自定义" ——
#    肿瘤科叫"周期/访视/项目", 术后康复叫"阶段/复查/检查项"。所以层级名是数据不是代码。

FLOW_ITEM_TYPES = {'scale': '量表', 'crf': 'CRF 表单', 'lab': '检验复查',
                   'interview': '问诊', 'edu': '宣教推送', 'other': '其他'}
FLOW_ANCHORS = {'enroll': '按入组时间', 'fixed_date': '固定日期'}
FLOW_TRIGGERS = {'schedule': '按计划到期', 'event': '事件触发'}
FLOW_END_REASONS = {'completed': '正常完成', 'event': '事件触发终止',
                    'manual': '手动终止', 'withdrawn': '受试者退出'}
VISIT_STATUSES = {'pending': '未到期', 'due': '窗口内待完成', 'done': '已完成',
                  'overdue': '已超窗', 'skipped': '已跳过', 'cancelled': '已取消'}
FLOW_MAX_DEPTH = 5
FLOW_MAX_NODES = 300


def _flow_walk(nodes, depth=0, path=None):
    """深度优先遍历节点树, 产出 (node, depth, path)。"""
    for n in nodes or []:
        if not isinstance(n, dict):
            continue
        p = (path or []) + [n.get('name') or n.get('id') or '?']
        yield n, depth, p
        for x in _flow_walk(n.get('children'), depth + 1, p):
            yield x


def validate_flow_definition(d):
    """流程定义校验。返回 errors 列表。"""
    errs = []
    if not isinstance(d, dict):
        return ['definition 必须是对象']
    levels = d.get('levels')
    if not isinstance(levels, list) or len(levels) < 2:
        errs.append('levels 必须是至少 2 个层级名的数组(方案要求三级以上, 这里最低放到 2 级)')
    elif len(levels) > FLOW_MAX_DEPTH:
        errs.append('层级最多 {} 级'.format(FLOW_MAX_DEPTH))
    nodes = d.get('nodes')
    if not isinstance(nodes, list) or not nodes:
        return errs + ['nodes 必须是非空数组']

    seen, n_count, max_depth = set(), 0, 0
    for n, depth, path in _flow_walk(nodes):
        n_count += 1
        max_depth = max(max_depth, depth)
        where = '节点「{}」'.format(' / '.join(path))
        nid = n.get('id')
        if not nid:
            errs.append(where + ' 缺 id')
        elif nid in seen:
            errs.append('节点 id 重复: ' + str(nid))
        else:
            seen.add(nid)
        if not n.get('name'):
            errs.append(where + ' 缺 name')
        leaf = not (n.get('children') or [])
        if leaf:
            # 叶子节点才是真正的"访视" —— 它得有时间落点
            off = n.get('offset_days')
            date = n.get('date')
            if off is None and not date:
                errs.append(where + ' 是叶子节点(即一次访视), 必须给 offset_days(相对锚点的天数) '
                                    '或 date(固定日期)')
            if off is not None:
                try:
                    int(off)
                except (TypeError, ValueError):
                    errs.append(where + ' 的 offset_days 必须是整数')
            if date and not re.match(r'^\d{4}-\d{2}-\d{2}$', str(date)):
                errs.append(where + " 的 date 必须是 'YYYY-MM-DD'")
            w = n.get('window')
            if w is not None:
                if not (isinstance(w, list) and len(w) == 2):
                    errs.append(where + ' 的 window 必须是 [提前几天, 延后几天]')
                else:
                    try:
                        lo, hi = int(w[0]), int(w[1])
                        if lo > 0:
                            errs.append(where + ' 的 window 下限应当是 0 或负数(提前几天)')
                        if hi < 0:
                            errs.append(where + ' 的 window 上限应当是 0 或正数(延后几天)')
                    except (TypeError, ValueError):
                        errs.append(where + ' 的 window 必须是两个整数')
            for i, it in enumerate(n.get('items') or []):
                if not isinstance(it, dict) or it.get('type') not in FLOW_ITEM_TYPES:
                    errs.append('{} 的第 {} 个随访内容 type 必须是 {} 之一'.format(
                        where, i + 1, '/'.join(FLOW_ITEM_TYPES)))
                elif it['type'] in ('scale', 'crf') and not it.get('ref'):
                    errs.append('{} 的第 {} 个内容是{}, 必须给 ref(量表编码/表单编码)'.format(
                        where, i + 1, FLOW_ITEM_TYPES[it['type']]))
    if n_count > FLOW_MAX_NODES:
        errs.append('节点总数 {} 超过上限 {}'.format(n_count, FLOW_MAX_NODES))
    if levels and max_depth + 1 > len(levels):
        errs.append('节点树深度 {} 级, 但只给了 {} 个层级名({}) —— 每一级都要有名字, '
                    '否则界面上没法称呼它'.format(max_depth + 1, len(levels), '/'.join(levels)))

    anchor = d.get('anchor') or 'enroll'
    if anchor not in FLOW_ANCHORS:
        errs.append('anchor 必须是 {} 之一'.format('/'.join(FLOW_ANCHORS)))
    # 流程外阶段: 不在时间轴上, 所以**不该有** offset_days
    for i, o in enumerate(d.get('offschedule') or []):
        w = 'offschedule[{}]'.format(i)
        if not isinstance(o, dict) or not o.get('id') or not o.get('name'):
            errs.append(w + ' 需要 id 和 name')
            continue
        if o.get('offset_days') is not None or o.get('date'):
            errs.append(w + '「{}」是流程外阶段(由事件触发, 可能发生零次也可能发生多次), '
                            '不该有 offset_days/date —— 给了时间落点就说明它其实是计划内访视'.format(o['name']))
        for j, it in enumerate(o.get('items') or []):
            if not isinstance(it, dict) or it.get('type') not in FLOW_ITEM_TYPES:
                errs.append('{} 的第 {} 个内容 type 不合法'.format(w, j + 1))
    return errs


def lint_flow_definition(d):
    """用法建议, 不阻断保存。"""
    out = []
    leaves = [(n, p) for n, _, p in _flow_walk((d or {}).get('nodes')) if not (n.get('children') or [])]
    if not leaves:
        out.append({'kind': 'no_leaf', 'detail': '整棵树没有叶子节点 —— 没有叶子就没有访视, '
                                                 '这个流程实例化之后不会产生任何任务'})
    nowin = [p for n, p in leaves if n.get('window') is None]
    if nowin:
        out.append({'kind': 'no_window',
                    'detail': '有 {} 个访视没设窗口(如「{}」)。没有窗口就没有"超窗"可言 —— '
                              '晚一天和晚三个月在系统看来一样, 访视超窗管理会失效。'
                              '建议按方案给每个访视一个可接受区间'.format(
                                  len(nowin), ' / '.join(nowin[0]))})
    offs = sorted(int(n['offset_days']) for n, _ in leaves if n.get('offset_days') is not None)
    dup = {x for x in offs if offs.count(x) > 1}
    if dup:
        out.append({'kind': 'same_day_visits',
                    'detail': '有多个访视落在同一天(第 {} 天) —— 患者同一天要跑两次? '
                              '若确实如此可以忽略, 更多时候是 offset 填错了'.format(
                                  '、'.join(str(x) for x in sorted(dup)))})
    if not (d or {}).get('offschedule'):
        out.append({'kind': 'no_offschedule',
                    'detail': '没有配置流程外阶段(不良事件/并发症)。计划内访视覆盖不了突发情况, '
                              '真出了不良事件就只能记在别处 —— 建议至少配一个'})
    return out


def ensure_platform_flow_tables():
    """M19: 流程模板 + 患者流程实例 + 访视 (idempotent)。"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_flow (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                code VARCHAR(64) NOT NULL,
                version VARCHAR(32) NOT NULL DEFAULT '1',
                name VARCHAR(128) NOT NULL,
                category VARCHAR(64) DEFAULT NULL COMMENT '慢病管理/术后康复/肿瘤随访/临床研究',
                scope ENUM('private','shared') DEFAULT 'private' COMMENT '§3.3(3) 公开流程共享/私有自建',
                owner VARCHAR(64) DEFAULT NULL,
                source VARCHAR(24) DEFAULT 'manual' COMMENT 'manual/ai/copy',
                copied_from VARCHAR(191) DEFAULT NULL,
                definition JSON NOT NULL COMMENT '层级名 + 节点树 + 锚点 + 触发 + 流程外阶段',
                node_count INT DEFAULT NULL,
                visit_count INT DEFAULT NULL COMMENT '叶子节点数 = 一轮完整随访有几次访视',
                status ENUM('draft','active','archived') DEFAULT 'draft',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uk_flow (code, version),
                INDEX idx_scope (scope), INDEX idx_status (status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M19 随访流程模板(流程库)'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_flow_instance (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                flow_code VARCHAR(64) NOT NULL,
                flow_version VARCHAR(32) NOT NULL COMMENT '钉住实例化时的版本 —— 流程改版不影响在随的人',
                patient_no VARCHAR(64) NOT NULL,
                cohort_code VARCHAR(64) DEFAULT NULL,
                group_code VARCHAR(64) DEFAULT NULL,
                anchor_date DATE NOT NULL COMMENT '锚点日: 入组日或指定日, 所有 offset 从这里算',
                status ENUM('running','ended') DEFAULT 'running',
                end_reason VARCHAR(24) DEFAULT NULL,
                end_note VARCHAR(500) DEFAULT NULL,
                ended_at DATETIME DEFAULT NULL,
                operator VARCHAR(64) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uk_instance (flow_code, patient_no),
                INDEX idx_patient (patient_no), INDEX idx_status (status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M19 患者流程实例'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_visit (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                instance_id BIGINT NOT NULL,
                patient_no VARCHAR(64) NOT NULL,
                node_id VARCHAR(64) NOT NULL,
                node_path VARCHAR(300) DEFAULT NULL COMMENT '多级路径, 如 术后早期/术后7天',
                name VARCHAR(128) NOT NULL,
                kind ENUM('scheduled','offschedule') DEFAULT 'scheduled'
                    COMMENT 'offschedule=流程外阶段(不良事件等); **完成率分母不含它**',
                planned_date DATE DEFAULT NULL,
                window_start DATE DEFAULT NULL,
                window_end DATE DEFAULT NULL,
                items JSON DEFAULT NULL COMMENT '本次访视要做的量表/CRF/检验/问诊',
                status ENUM('pending','due','done','overdue','skipped','cancelled') DEFAULT 'pending',
                done_at DATETIME DEFAULT NULL,
                operator VARCHAR(64) DEFAULT NULL,
                note VARCHAR(500) DEFAULT NULL,
                seq INT DEFAULT 0 COMMENT '流程外阶段可重复发生, 用它区分第几次',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uk_visit (instance_id, node_id, seq),
                INDEX idx_patient (patient_no, planned_date),
                INDEX idx_status (status), INDEX idx_window (window_end)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M19 访视'
        """)
        print('[启动] platform_flow / platform_flow_instance / platform_visit 表已就绪')
        cur.close()
    except Exception as e:
        print('[启动] ensure_platform_flow_tables 失败:', e)
    finally:
        conn.close()


def upsert_flow(body):
    """建/改流程模板。已有在随实例时改动 -> 开新版 (同 M14 的道理)。"""
    code = re.sub(r'[^0-9A-Za-z_\-]', '', str(body.get('code') or ''))[:64]
    name = str(body.get('name') or '').strip()
    if not code or not name:
        return None, 'code 和 name 必填'
    d = body.get('definition')
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except ValueError:
            return None, 'definition 不是合法 JSON'
    errs = validate_flow_definition(d)
    if errs:
        return None, '流程定义有 {} 处问题: {}'.format(len(errs), '; '.join(errs[:6]))
    scope = body.get('scope') or 'private'
    if scope not in CRF_SCOPES:
        return None, 'scope 必须是 private 或 shared'

    leaves = [n for n, _, _ in _flow_walk(d.get('nodes')) if not (n.get('children') or [])]
    n_count = sum(1 for _ in _flow_walk(d.get('nodes')))

    ensure_platform_flow_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        version = str(body.get('version') or '').strip()
        note = None
        if not version:
            cur.execute('SELECT version FROM platform_flow WHERE code=%s ORDER BY id DESC LIMIT 1', (code,))
            row = cur.fetchone()
            if row:
                cur.execute("SELECT COUNT(*) FROM platform_flow_instance WHERE flow_code=%s "
                            "AND flow_version=%s AND status='running'", (code, row[0]))
                running = cur.fetchone()[0]
                if running:
                    version = _bump_version(row[0])
                    note = ('该版本有 {} 名患者正在随访, 已自动开新版 {} —— '
                            '在随患者的访视表已经按旧版排好, 改流程不该把他们的日程重排'.format(
                                running, version))
                else:
                    version = row[0]
            else:
                version = '1'
        cur.execute("""
            INSERT INTO platform_flow (code, version, name, category, scope, owner, source,
                                       copied_from, definition, node_count, visit_count, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE name=VALUES(name), category=VALUES(category),
              scope=VALUES(scope), owner=VALUES(owner), definition=VALUES(definition),
              node_count=VALUES(node_count), visit_count=VALUES(visit_count), status=VALUES(status)
        """, (code, version, name, body.get('category') or None, scope,
              body.get('owner') or None, body.get('source') or 'manual',
              body.get('copied_from') or None, json.dumps(d, ensure_ascii=False),
              n_count, len(leaves), body.get('status') or 'draft'))
        cur.close()
        return {'code': code, 'version': version, 'nodes': n_count, 'visits': len(leaves),
                'offschedule': len(d.get('offschedule') or []),
                'levels': d.get('levels'), 'note': note,
                'advisories': lint_flow_definition(d)}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def _visit_status(planned, win_start, win_end, today):
    """按今天和窗口算访视状态。没设窗口时退化为"当天即窗口"。"""
    ws = win_start or planned
    we = win_end or planned
    if today < ws:
        return 'pending'
    if today <= we:
        return 'due'
    return 'overdue'


def flow_instantiate(body):
    """把流程实例化到患者身上, 生成访视表。

    {flow_code, patient_no, anchor_date?, flow_version?, cohort_code?, group_code?, operator?}

    幂等: 同 (flow_code, patient_no) 只有一个实例。已存在则返回现状, **不重排** ——
    重排会把患者已经完成的访视记录冲掉。
    """
    code = str(body.get('flow_code') or '').strip()
    no = str(body.get('patient_no') or '').strip()
    if not code or not no:
        return None, 'flow_code 和 patient_no 必填'
    anchor = str(body.get('anchor_date') or '').strip() or datetime.date.today().strftime('%Y-%m-%d')
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', anchor):
        return None, "anchor_date 必须是 'YYYY-MM-DD'"

    ensure_platform_flow_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        if body.get('flow_version'):
            cur.execute('SELECT version, definition FROM platform_flow WHERE code=%s AND version=%s',
                        (code, str(body['flow_version'])))
        else:
            cur.execute("SELECT version, definition FROM platform_flow WHERE code=%s "
                        "AND status='active' ORDER BY id DESC LIMIT 1", (code,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, '流程不存在或未启用: {}'.format(code)
        version, d = row
        if isinstance(d, str):
            d = json.loads(d)

        cur.execute('SELECT id, status FROM platform_flow_instance WHERE flow_code=%s AND patient_no=%s',
                    (code, no))
        exist = cur.fetchone()
        if exist:
            cur.execute('SELECT COUNT(*) FROM platform_visit WHERE instance_id=%s', (exist[0],))
            n_exist = cur.fetchone()[0]
            cur.close()
            return {'ok': True, 'created': False, 'instance_id': exist[0],
                    'visits': n_exist,
                    'hint': '该患者已在此流程中(实例 #{}, 状态 {}), 未做任何改动 —— '
                            '重新实例化会把已完成的访视记录冲掉'.format(exist[0], exist[1])}, None

        cur.execute("""INSERT INTO platform_flow_instance
                       (flow_code, flow_version, patient_no, cohort_code, group_code,
                        anchor_date, status, operator)
                       VALUES (%s,%s,%s,%s,%s,%s,'running',%s)""",
                    (code, version, no, body.get('cohort_code') or None,
                     body.get('group_code') or None, anchor, body.get('operator') or None))
        iid = cur.lastrowid
        a = datetime.datetime.strptime(anchor, '%Y-%m-%d').date()
        today = datetime.date.today()
        rows = []
        for n, depth, path in _flow_walk(d.get('nodes')):
            if n.get('children'):
                continue                      # 非叶子是分组层级, 不产生访视
            if n.get('date'):
                planned = datetime.datetime.strptime(str(n['date']), '%Y-%m-%d').date()
            else:
                planned = a + datetime.timedelta(days=int(n.get('offset_days') or 0))
            w = n.get('window') or [0, 0]
            ws = planned + datetime.timedelta(days=int(w[0]))
            we = planned + datetime.timedelta(days=int(w[1]))
            rows.append((iid, no, n['id'], ' / '.join(path)[:300], n.get('name'), 'scheduled',
                         planned, ws, we, json.dumps(n.get('items') or [], ensure_ascii=False),
                         _visit_status(planned, ws, we, today), 0))
        if rows:
            cur.executemany("""INSERT INTO platform_visit
                (instance_id, patient_no, node_id, node_path, name, kind, planned_date,
                 window_start, window_end, items, status, seq)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
        cur.close()
        return {'ok': True, 'created': True, 'instance_id': iid, 'flow_version': version,
                'anchor_date': anchor, 'visits': len(rows),
                'offschedule_available': len(d.get('offschedule') or []),
                'note': '流程外阶段(不良事件等)不预先生成 —— 它们由事件触发, '
                        '可能发生零次也可能多次, 预生成会让完成率的分母失真'}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def flow_refresh_status(patient_no=None):
    """按今天重算访视状态(pending -> due -> overdue)。已完成/跳过/取消的不动。"""
    ensure_platform_flow_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ["v.status IN ('pending','due','overdue')"], []
        if patient_no:
            where.append('v.patient_no=%s'); params.append(patient_no)
        # 已终止的实例不再推进状态 —— 人都退出了还在那儿累积"超窗"没有意义
        where.append("EXISTS (SELECT 1 FROM platform_flow_instance i "
                     "WHERE i.id=v.instance_id AND i.status='running')")
        cur.execute("""UPDATE platform_visit v SET v.status = CASE
                         WHEN CURDATE() < COALESCE(v.window_start, v.planned_date) THEN 'pending'
                         WHEN CURDATE() <= COALESCE(v.window_end, v.planned_date) THEN 'due'
                         ELSE 'overdue' END
                       WHERE {}""".format(' AND '.join(where)), params)
        n = cur.rowcount
        cur.close()
        return {'ok': True, 'refreshed': n}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def visit_transition(body):
    """推进一次访视 {visit_id, action: done|skip|cancel|reopen, operator?, note?}"""
    try:
        vid = int(body.get('visit_id'))
    except (TypeError, ValueError):
        return None, 'visit_id 必填且为整数'
    action = str(body.get('action') or '').strip()
    if action not in ('done', 'skip', 'cancel', 'reopen'):
        return None, 'action 必须是 done/skip/cancel/reopen'
    note = str(body.get('note') or '').strip()
    if action in ('skip', 'cancel') and not note:
        return None, '{} 必须写明原因 —— 一次没做的访视是数据缺失, 得说清楚为什么'.format(
            '跳过' if action == 'skip' else '取消')

    ensure_platform_flow_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT status, planned_date, window_start, window_end FROM platform_visit WHERE id=%s', (vid,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, '访视不存在: {}'.format(vid)
        cur_status, planned, ws, we = row
        today = datetime.date.today()
        if action == 'done':
            new = 'done'
            in_window = (ws or planned) <= today <= (we or planned)
        elif action == 'reopen':
            new = _visit_status(planned, ws, we, today)
            in_window = None
        else:
            new = 'skipped' if action == 'skip' else 'cancelled'
            in_window = None
        cur.execute("""UPDATE platform_visit SET status=%s,
                       done_at=%s, operator=%s, note=%s WHERE id=%s""",
                    (new, datetime.datetime.now() if action == 'done' else None,
                     body.get('operator') or None, note[:500] or None, vid))
        cur.close()
        out = {'ok': True, 'visit_id': vid, 'from': cur_status, 'to': new}
        if action == 'done' and in_window is False:
            out['warning'] = ('这次访视是在窗口({} ~ {})之外完成的, 已如实记为完成但请注意: '
                              '超窗完成在临床研究里通常要记方案偏离'.format(ws or planned, we or planned))
        return out, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def trigger_offschedule(body):
    """事件触发一次流程外阶段(不良事件/并发症)。

    {patient_no, flow_code, node_id, date?, operator?, note?}
    同一个流程外阶段可以发生多次, 用 seq 区分。
    """
    no = str(body.get('patient_no') or '').strip()
    code = str(body.get('flow_code') or '').strip()
    nid = str(body.get('node_id') or '').strip()
    if not no or not code or not nid:
        return None, 'patient_no / flow_code / node_id 必填'
    when = str(body.get('date') or '').strip() or datetime.date.today().strftime('%Y-%m-%d')
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', when):
        return None, "date 必须是 'YYYY-MM-DD'"

    ensure_platform_flow_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, flow_version, status FROM platform_flow_instance "
                    "WHERE flow_code=%s AND patient_no=%s", (code, no))
        inst = cur.fetchone()
        if not inst:
            cur.close()
            return None, '该患者不在此流程中'
        if inst[2] != 'running':
            cur.close()
            return None, '该患者的流程已终止, 不能再触发流程外阶段'
        cur.execute('SELECT definition FROM platform_flow WHERE code=%s AND version=%s', (code, inst[1]))
        d = cur.fetchone()[0]
        if isinstance(d, str):
            d = json.loads(d)
        node = next((o for o in (d.get('offschedule') or []) if o.get('id') == nid), None)
        if node is None:
            cur.close()
            return None, '流程外阶段不存在: {} (本流程有: {})'.format(
                nid, '、'.join(o.get('id', '?') for o in (d.get('offschedule') or [])) or '无')
        cur.execute("SELECT COALESCE(MAX(seq),0)+1 FROM platform_visit WHERE instance_id=%s AND node_id=%s",
                    (inst[0], nid))
        seq = cur.fetchone()[0]
        wd = datetime.datetime.strptime(when, '%Y-%m-%d').date()
        cur.execute("""INSERT INTO platform_visit
            (instance_id, patient_no, node_id, node_path, name, kind, planned_date,
             window_start, window_end, items, status, seq, operator, note)
            VALUES (%s,%s,%s,%s,%s,'offschedule',%s,%s,%s,%s,'due',%s,%s,%s)""",
                    (inst[0], no, nid, '流程外 / ' + str(node.get('name'))[:200], node.get('name'),
                     wd, wd, wd, json.dumps(node.get('items') or [], ensure_ascii=False),
                     seq, body.get('operator') or None, (body.get('note') or '')[:500] or None))
        vid = cur.lastrowid
        cur.close()
        return {'ok': True, 'visit_id': vid, 'node_id': nid, 'name': node.get('name'),
                'seq': seq, 'date': when,
                'note': '流程外阶段不计入随访完成率的分母 —— 它是突发事件不是计划任务, '
                        '算进去会让完成率永远上不去'}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def flow_end(body):
    """终止一个患者的流程 {patient_no, flow_code, reason, note?, operator?}

    终止后未完成的计划内访视一律置 cancelled(而不是删掉) —— 删了就看不出
    "这个人本来还有 5 次随访没做完"。
    """
    no = str(body.get('patient_no') or '').strip()
    code = str(body.get('flow_code') or '').strip()
    reason = body.get('reason') or 'manual'
    if not no or not code:
        return None, 'patient_no 和 flow_code 必填'
    if reason not in FLOW_END_REASONS:
        return None, 'reason 必须是 {} 之一'.format('/'.join(FLOW_END_REASONS))
    note = str(body.get('note') or '').strip()
    if reason in ('manual', 'event') and not note:
        return None, '{} 必须写明原因'.format(FLOW_END_REASONS[reason])

    ensure_platform_flow_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, status FROM platform_flow_instance WHERE flow_code=%s AND patient_no=%s",
                    (code, no))
        inst = cur.fetchone()
        if not inst:
            cur.close()
            return None, '该患者不在此流程中'
        if inst[1] == 'ended':
            cur.close()
            return None, '该流程已经终止'
        cur.execute("""UPDATE platform_flow_instance SET status='ended', end_reason=%s,
                       end_note=%s, ended_at=NOW() WHERE id=%s""", (reason, note[:500] or None, inst[0]))
        cur.execute("""UPDATE platform_visit SET status='cancelled',
                       note=CONCAT(COALESCE(note,''), ' [流程终止: ', %s, ']')
                       WHERE instance_id=%s AND status IN ('pending','due','overdue')""",
                    (FLOW_END_REASONS[reason], inst[0]))
        n = cur.rowcount
        cur.close()
        return {'ok': True, 'instance_id': inst[0], 'reason': reason,
                'reason_label': FLOW_END_REASONS[reason], 'cancelled_visits': n,
                'note': '未完成的 {} 次访视已置为取消而不是删除 —— 删了就看不出'
                        '这个人本来还有几次随访没做完'.format(n)}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def query_visits(patient_no=None, flow_code=None, status=None, due_within=None,
                 overdue_only=False, limit=500):
    """访视列表。due_within=N 取未来 N 天内到期的。"""
    ensure_platform_flow_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ['1=1'], []
        if patient_no:
            where.append('v.patient_no=%s'); params.append(patient_no)
        if flow_code:
            where.append('i.flow_code=%s'); params.append(flow_code)
        if status:
            where.append('v.status=%s'); params.append(status)
        if overdue_only:
            where.append("v.status='overdue'")
        if due_within is not None:
            where.append('v.window_end >= CURDATE() AND v.window_start <= DATE_ADD(CURDATE(), INTERVAL %s DAY)')
            params.append(int(due_within))
        params.append(int(limit))
        cur.execute("""
            SELECT v.id, v.patient_no, p.name, i.flow_code, v.node_id, v.node_path, v.name,
                   v.kind, v.planned_date, v.window_start, v.window_end, v.items,
                   v.status, v.done_at, v.operator, v.note, v.seq,
                   DATEDIFF(CURDATE(), v.window_end) AS days_overdue
            FROM platform_visit v
            JOIN platform_flow_instance i ON i.id=v.instance_id
            LEFT JOIN platform_patient p ON p.patient_no=v.patient_no
            WHERE {} ORDER BY v.planned_date, v.patient_no LIMIT %s
        """.format(' AND '.join(where)), params)
        cols = ['id', 'patient_no', 'patient_name', 'flow_code', 'node_id', 'node_path', 'name',
                'kind', 'planned_date', 'window_start', 'window_end', 'items', 'status',
                'done_at', 'operator', 'note', 'seq', 'days_overdue']
        out = []
        for row in cur.fetchall():
            r = dict(zip(cols, row))
            for k in ('planned_date', 'window_start', 'window_end'):
                if r.get(k) is not None and hasattr(r[k], 'strftime'):
                    r[k] = r[k].strftime('%Y-%m-%d')
            if r.get('done_at') is not None and hasattr(r['done_at'], 'strftime'):
                r['done_at'] = r['done_at'].strftime('%Y-%m-%d %H:%M:%S')
            if isinstance(r.get('items'), str):
                try:
                    r['items'] = json.loads(r['items'])
                except ValueError:
                    pass
            r['status_label'] = VISIT_STATUSES.get(r['status'], r['status'])
            r['is_offschedule'] = r['kind'] == 'offschedule'
            if r['status'] != 'overdue':
                r['days_overdue'] = None
            out.append(r)
        cur.close()
        return {'ok': True, 'count': len(out), 'visits': out, 'statuses': VISIT_STATUSES}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def flow_completion(flow_code=None, cohort_code=None):
    """随访完成率。

    **分母的定义是这块最容易做错的地方**, 两种错法都让这个数失去意义:
      · 把流程外阶段(不良事件)算进分母 -> 从未发生的事件被当成"未完成", 完成率永远上不去
      · 把尚未到期的访视算进分母 -> 三个月后才做的访视现在就算"没做", 早期完成率永远很低
    所以分母 = **已到窗口期的计划内访视**(due/overdue/done/skipped), 不含 pending,
    不含 offschedule, 不含因流程终止而取消的。
    """
    ensure_platform_flow_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ["v.kind='scheduled'", "v.status IN ('due','overdue','done','skipped')"], []
        if flow_code:
            where.append('i.flow_code=%s'); params.append(flow_code)
        if cohort_code:
            where.append('i.cohort_code=%s'); params.append(cohort_code)
        cur.execute("""
            SELECT i.flow_code, COUNT(*) AS denom,
                   SUM(v.status='done') AS done,
                   SUM(v.status='overdue') AS overdue,
                   SUM(v.status='skipped') AS skipped,
                   SUM(v.status='due') AS due
            FROM platform_visit v JOIN platform_flow_instance i ON i.id=v.instance_id
            WHERE {} GROUP BY i.flow_code
        """.format(' AND '.join(where)), params)
        rows = []
        for fc, denom, done, overdue, skipped, due in cur.fetchall():
            # MySQL 的 SUM() 回来是 Decimal, 直接和 float 相乘会 TypeError。
            # 先统一转 int, 别在算式里混着两种数值类型。
            denom, done = int(denom or 0), int(done or 0)
            rows.append({'flow_code': fc, 'denominator': denom, 'done': done,
                         'overdue': int(overdue or 0), 'skipped': int(skipped or 0),
                         'due': int(due or 0),
                         'completion_rate': round(done * 100.0 / denom, 1) if denom else None})
        # 单独给出被排除在分母外的那些, 免得有人以为它们丢了
        cur.execute("""SELECT SUM(v.kind='offschedule'), SUM(v.kind='scheduled' AND v.status='pending'),
                              SUM(v.status='cancelled')
                       FROM platform_visit v JOIN platform_flow_instance i ON i.id=v.instance_id
                       {}""".format('WHERE i.flow_code=%s' if flow_code else ''),
                    ([flow_code] if flow_code else []))
        off, pend, canc = cur.fetchone()
        cur.close()
        return {'ok': True, 'by_flow': rows,
                'excluded': {'offschedule': int(off or 0), 'not_yet_due': int(pend or 0),
                             'cancelled': int(canc or 0)},
                'denominator_note': ('分母 = 已到窗口期的计划内访视。不含流程外阶段({} 次)、'
                                     '尚未到期的访视({} 次)、因流程终止而取消的({} 次) —— '
                                     '把前两类算进分母会让完成率永远上不去'.format(
                                         int(off or 0), int(pend or 0), int(canc or 0)))}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def query_flows(code=None, scope=None, category=None, all_versions=False,
                with_definition=False, limit=100):
    """流程库列表 (§3.3(3))。"""
    ensure_platform_flow_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ['1=1'], []
        if code:
            where.append('f.code=%s'); params.append(code)
        if scope:
            where.append('f.scope=%s'); params.append(scope)
        if category:
            where.append('f.category=%s'); params.append(category)
        if not (all_versions or code):
            where.append('f.updated_at = (SELECT MAX(x.updated_at) FROM platform_flow x WHERE x.code=f.code)')
        cols = ('f.id, f.code, f.version, f.name, f.category, f.scope, f.owner, f.source, '
                'f.copied_from, f.node_count, f.visit_count, f.status, f.created_at, f.updated_at, '
                '(SELECT COUNT(*) FROM platform_flow_instance i WHERE i.flow_code=f.code '
                "  AND i.status='running') AS running_patients")
        if with_definition or code:
            cols += ', f.definition'
        params.append(int(limit))
        cur.execute('SELECT {} FROM platform_flow f WHERE {} ORDER BY f.category, f.code, '
                    'f.updated_at DESC LIMIT %s'.format(cols, ' AND '.join(where)), params)
        names = [d[0] for d in cur.description]
        out = []
        for row in cur.fetchall():
            r = dict(zip(names, row))
            for k in ('created_at', 'updated_at'):
                if r.get(k) is not None and hasattr(r[k], 'strftime'):
                    r[k] = r[k].strftime('%Y-%m-%d %H:%M:%S')
            if isinstance(r.get('definition'), str):
                try:
                    r['definition'] = json.loads(r['definition'])
                except ValueError:
                    pass
            out.append(r)
        cur.close()
        return {'ok': True, 'count': len(out), 'flows': out,
                'item_types': FLOW_ITEM_TYPES, 'anchors': FLOW_ANCHORS,
                'end_reasons': FLOW_END_REASONS, 'visit_statuses': VISIT_STATUSES}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def copy_flow(body):
    """复制流程 (§3.3(3) 已配置流程可整体复用、复制修改)。同 M14: 新 code 的第 1 版。"""
    src = str(body.get('code') or '').strip()
    new = re.sub(r'[^0-9A-Za-z_\-]', '', str(body.get('new_code') or ''))[:64]
    if not src or not new:
        return None, 'code(源) 和 new_code(新) 必填'
    if src == new:
        return None, 'new_code 不能与源相同 —— 复制要生成一条独立的流程'
    ensure_platform_flow_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT name, category, version, definition FROM platform_flow WHERE code=%s '
                    'ORDER BY id DESC LIMIT 1', (src,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, '源流程不存在: {}'.format(src)
        cur.execute('SELECT 1 FROM platform_flow WHERE code=%s LIMIT 1', (new,))
        if cur.fetchone():
            cur.close()
            return None, 'new_code 已存在: {}'.format(new)
        name, cat, ver, d = row
        cur.close()
        return upsert_flow({'code': new, 'name': body.get('new_name') or (name + ' (副本)'),
                            'category': cat, 'version': '1', 'scope': body.get('scope') or 'private',
                            'owner': body.get('owner'), 'source': 'copy',
                            'copied_from': '{}@{}'.format(src, ver),
                            'definition': json.loads(d) if isinstance(d, str) else d,
                            'status': 'draft'})
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


# ============ 随访平台 1.1 M20 (访视超窗管理 + 消息推送, 方案 §4.5) ============
#
# 两件事必须先说死:
#
# 1) **推送通道没有接。** 短信/微信网关都没配。所以这里做的是**推送记录与队列**:
#    决定了要发什么、发给谁、什么时候发。状态一律是 queued, **绝不会显示 sent** ——
#    有人看到"已推送 200 条"就会以为患者收到了, 那比不做这个功能更糟。
#    真接了通道再让 sender 把 queued 改成 sent/failed。
#
# 2) **只有已发布的宣教材料能推。** M15 那套"AI 产出一律草稿, 必须署名审核发布后
#    才能被随访计划调用"的闸门, 落点就在这里。推送接口不校验 status='published',
#    那道闸门就纯粹是装饰 —— 前面写的所有约束都白设。

PUSH_CHANNELS = {'sms': '短信', 'wechat': '微信', 'inapp': '站内'}
PUSH_CONTENT_TYPES = {'edu': '患教内容', 'reminder': '用药/复诊提醒',
                      'task': '随访任务', 'notice': '通知'}
PUSH_TARGETS = {'patient': '单个患者', 'group': '分组', 'cohort': '整个方案', 'all': '全量患者'}
PUSH_STATUSES = {'queued': '待发送', 'sent': '已发送', 'failed': '发送失败', 'cancelled': '已取消'}
PUSH_MAX_TARGETS = 2000
PUSH_NOT_WIRED = ('推送通道(短信/微信)尚未接入。以上记录已入队但**没有真的发出去** —— '
                  '状态是"待发送"不是"已发送"。接通道后由发送器把它们置为已发送/失败。')

FOLLOWUP_ACTIONS = {'call': '电话联系', 'reschedule': '改约', 'visited': '已到院',
                    'lost': '标记失访', 'waive': '本次豁免', 'note': '仅记录'}


def ensure_platform_push_tables():
    """M20: 访视跟进记录 + 推送队列 (idempotent)。"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_visit_followup (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                visit_id BIGINT DEFAULT NULL,
                patient_no VARCHAR(64) NOT NULL,
                action VARCHAR(16) NOT NULL,
                result VARCHAR(500) DEFAULT NULL,
                new_date DATE DEFAULT NULL COMMENT '改约后的新计划日',
                operator VARCHAR(64) DEFAULT NULL,
                batch_id VARCHAR(40) DEFAULT NULL COMMENT '同一次批量操作的标记, 便于回溯"那次群跟进"',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_visit (visit_id), INDEX idx_patient (patient_no),
                INDEX idx_batch (batch_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M20 访视跟进记录 (只增不改)'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_push (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                batch_id VARCHAR(40) DEFAULT NULL,
                channel VARCHAR(16) NOT NULL DEFAULT 'sms',
                content_type VARCHAR(16) NOT NULL DEFAULT 'notice',
                target_kind VARCHAR(16) NOT NULL DEFAULT 'patient',
                patient_no VARCHAR(64) NOT NULL,
                ref_code VARCHAR(64) DEFAULT NULL COMMENT '患教内容的材料编码',
                ref_version VARCHAR(32) DEFAULT NULL,
                title VARCHAR(200) DEFAULT NULL,
                body MEDIUMTEXT,
                mode ENUM('auto','manual') DEFAULT 'manual',
                visit_id BIGINT DEFAULT NULL COMMENT '由哪次访视触发(自动推送)',
                scheduled_at DATETIME DEFAULT NULL,
                status ENUM('queued','sent','failed','cancelled') DEFAULT 'queued'
                    COMMENT '通道未接时恒为 queued —— 绝不能让人以为患者已经收到了',
                sent_at DATETIME DEFAULT NULL,
                error VARCHAR(300) DEFAULT NULL,
                operator VARCHAR(64) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_patient (patient_no), INDEX idx_status (status),
                INDEX idx_batch (batch_id), INDEX idx_visit (visit_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M20 推送队列(通道未接, 只入队不发送)'
        """)
        print('[启动] platform_visit_followup / platform_push 表已就绪')
        cur.close()
    except Exception as e:
        print('[启动] ensure_platform_push_tables 失败:', e)
    finally:
        conn.close()


def _batch_id():
    """批次号。不用随机数 —— 同一秒内的两次批量操作各自有 id 就够了, 用时间戳+序号。"""
    return datetime.datetime.now().strftime('B%Y%m%d%H%M%S%f')[:22]


def visit_overdue_summary(days_ahead=7):
    """§4.5(4) 实时统计: 当日访视人数、超窗人数、未来 N 天到期。"""
    ensure_platform_flow_tables()
    ensure_platform_push_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
              SUM(v.kind='scheduled' AND v.status='overdue') AS overdue,
              COUNT(DISTINCT CASE WHEN v.kind='scheduled' AND v.status='overdue'
                                  THEN v.patient_no END) AS overdue_patients,
              SUM(v.kind='scheduled' AND v.status='due') AS due,
              SUM(v.kind='scheduled' AND v.status='due'
                  AND CURDATE() BETWEEN v.window_start AND v.window_end
                  AND v.planned_date = CURDATE()) AS today,
              COUNT(DISTINCT CASE WHEN v.kind='scheduled' AND v.planned_date=CURDATE()
                                  THEN v.patient_no END) AS today_patients,
              SUM(v.kind='scheduled' AND v.status='pending'
                  AND v.window_start <= DATE_ADD(CURDATE(), INTERVAL %s DAY)) AS upcoming
            FROM platform_visit v
            JOIN platform_flow_instance i ON i.id=v.instance_id AND i.status='running'
        """, (int(days_ahead),))
        row = cur.fetchone() or (0,) * 6
        n = [int(x or 0) for x in row]
        # 超窗分档: 超 1-7 天还能补, 超 30 天以上多半是失访了, 两者的跟进方式不一样
        cur.execute("""
            SELECT CASE WHEN DATEDIFF(CURDATE(), v.window_end) <= 7 THEN '1-7天'
                        WHEN DATEDIFF(CURDATE(), v.window_end) <= 30 THEN '8-30天'
                        ELSE '30天以上' END AS band, COUNT(*)
            FROM platform_visit v JOIN platform_flow_instance i ON i.id=v.instance_id
            WHERE i.status='running' AND v.kind='scheduled' AND v.status='overdue'
            GROUP BY band ORDER BY FIELD(band,'1-7天','8-30天','30天以上')
        """)
        bands = [{'band': b, 'count': int(c)} for b, c in cur.fetchall()]
        cur.execute("""SELECT COUNT(*) FROM platform_visit_followup f
                       WHERE DATE(f.created_at)=CURDATE()""")
        followed_today = int(cur.fetchone()[0] or 0)
        cur.close()
        return {'ok': True,
                'overdue_visits': n[0], 'overdue_patients': n[1],
                'due_visits': n[2], 'today_visits': n[3], 'today_patients': n[4],
                'upcoming_visits': n[5], 'days_ahead': int(days_ahead),
                'overdue_bands': bands, 'followed_up_today': followed_today,
                'note': ('超窗按天数分档: 1-7 天还能补, 30 天以上多半已经失访 —— '
                         '两者的跟进方式不一样, 混在一起看会把还救得回来的人淹掉')}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def visit_batch_followup(body):
    """§4.5(4) 批量跟进 {visit_ids:[...], action, result?, new_date?, operator?}

    **逐条给结果**, 不只说"成功了" —— 20 条里坏了 3 条, 得知道是哪 3 条。
    标记失访要写原因: 失访会把患者移出分析人群, 是个重大判定。
    """
    ids = body.get('visit_ids')
    if not isinstance(ids, list) or not ids:
        return None, 'visit_ids 必须是非空数组'
    if len(ids) > 500:
        return None, '一次最多跟进 500 条'
    action = str(body.get('action') or '').strip()
    if action not in FOLLOWUP_ACTIONS:
        return None, 'action 必须是 {} 之一'.format('/'.join(FOLLOWUP_ACTIONS))
    result = str(body.get('result') or '').strip()
    if action == 'lost' and not result:
        return None, ('标记失访必须写明依据 —— 失访会把患者移出分析人群, '
                      '是个改变研究结论的判定, 不能批量一点了事')
    if action == 'waive' and not result:
        return None, '本次豁免必须写明原因'
    new_date = str(body.get('new_date') or '').strip()
    if action == 'reschedule':
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', new_date):
            return None, "改约必须给 new_date ('YYYY-MM-DD')"

    ensure_platform_push_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        bid = _batch_id()
        out, ok_n = [], 0
        for vid in ids[:500]:
            try:
                v = int(vid)
            except (TypeError, ValueError):
                out.append({'visit_id': vid, 'ok': False, 'error': 'visit_id 不是整数'})
                continue
            cur.execute('SELECT patient_no, status, kind FROM platform_visit WHERE id=%s', (v,))
            row = cur.fetchone()
            if not row:
                out.append({'visit_id': v, 'ok': False, 'error': '访视不存在'})
                continue
            pno, st, kind = row
            if st in ('done', 'cancelled'):
                out.append({'visit_id': v, 'ok': False, 'patient_no': pno,
                            'error': '该访视已{}，不再跟进'.format(VISIT_STATUSES.get(st, st))})
                continue
            cur.execute("""INSERT INTO platform_visit_followup
                           (visit_id, patient_no, action, result, new_date, operator, batch_id)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (v, pno, action, result[:500] or None,
                         new_date or None, body.get('operator') or None, bid))
            if action == 'reschedule':
                # 改约 = 挪日期。窗口跟着平移, 保持原本的宽窄 —— 直接把窗口设成当天
                # 会让"术后 3 月(±14 天)"变成"必须当天完成"。
                cur.execute("""UPDATE platform_visit SET
                    window_start = DATE_ADD(%s, INTERVAL DATEDIFF(window_start, planned_date) DAY),
                    window_end   = DATE_ADD(%s, INTERVAL DATEDIFF(window_end, planned_date) DAY),
                    planned_date = %s,
                    status = CASE WHEN CURDATE() < DATE_ADD(%s, INTERVAL DATEDIFF(window_start, planned_date) DAY)
                                  THEN 'pending' ELSE 'due' END,
                    note = CONCAT(COALESCE(note,''),' [改约至 ',%s,']')
                    WHERE id=%s""", (new_date, new_date, new_date, new_date, new_date, v))
            elif action == 'visited':
                cur.execute("UPDATE platform_visit SET status='done', done_at=NOW(), operator=%s WHERE id=%s",
                            (body.get('operator') or None, v))
            elif action == 'waive':
                cur.execute("UPDATE platform_visit SET status='skipped', note=%s WHERE id=%s",
                            ('本次豁免: ' + result[:400], v))
            elif action == 'lost':
                # 失访不改这一次访视的状态, 而是终止整个流程 —— 人都联系不上了,
                # 后面几次访视继续在那儿"待完成"没有意义, 还会把超窗数越堆越高
                cur.execute("""SELECT i.flow_code FROM platform_visit v
                               JOIN platform_flow_instance i ON i.id=v.instance_id WHERE v.id=%s""", (v,))
                fc = cur.fetchone()
                if fc:
                    cur.execute("""UPDATE platform_flow_instance SET status='ended',
                                   end_reason='withdrawn', end_note=%s, ended_at=NOW()
                                   WHERE flow_code=%s AND patient_no=%s AND status='running'""",
                                ('失访: ' + result[:400], fc[0], pno))
                    cur.execute("""UPDATE platform_visit v JOIN platform_flow_instance i ON i.id=v.instance_id
                                   SET v.status='cancelled', v.note=CONCAT(COALESCE(v.note,''),' [失访]')
                                   WHERE i.flow_code=%s AND v.patient_no=%s
                                     AND v.status IN ('pending','due','overdue')""", (fc[0], pno))
            out.append({'visit_id': v, 'ok': True, 'patient_no': pno, 'action': action})
            ok_n += 1
        cur.close()
        res = {'ok': True, 'batch_id': bid, 'action': action,
               'action_label': FOLLOWUP_ACTIONS[action],
               'total': len(ids), 'succeeded': ok_n, 'failed': len(ids) - ok_n,
               'results': out}
        if action == 'lost' and ok_n:
            res['note'] = ('已对 {} 名患者标记失访: 其流程一并终止, 剩余访视置为取消 —— '
                           '人都联系不上了, 后面的访视继续挂着"待完成"只会把超窗数越堆越高, '
                           '而且会让随访完成率失真'.format(ok_n))
        return res, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def _resolve_push_targets(cur, body):
    """把推送目标解析成门诊号列表。返回 (list, error)。"""
    kind = body.get('target_kind') or 'patient'
    if kind not in PUSH_TARGETS:
        return None, 'target_kind 必须是 {} 之一'.format('/'.join(PUSH_TARGETS))
    if kind == 'patient':
        no = str(body.get('patient_no') or '').strip()
        if not no:
            return None, '单患者推送必须给 patient_no'
        return [no], None
    if kind == 'group':
        c, g = body.get('cohort_code'), body.get('group_code')
        if not c or not g:
            return None, '分组推送必须给 cohort_code 和 group_code'
        cur.execute("SELECT patient_no FROM platform_enrollment WHERE cohort_code=%s "
                    "AND group_code=%s AND status='enrolled'", (c, g))
    elif kind == 'cohort':
        if not body.get('cohort_code'):
            return None, '方案推送必须给 cohort_code'
        cur.execute("SELECT patient_no FROM platform_enrollment WHERE cohort_code=%s "
                    "AND status='enrolled'", (body['cohort_code'],))
    else:
        cur.execute('SELECT patient_no FROM platform_patient')
    return [r[0] for r in cur.fetchall()], None


def push_create(body):
    """建推送。{channel, content_type, target_kind, ..., title?, body?, ref_code?,
       dry_run?, operator?, mode?, scheduled_at?}

    dry_run 默认 **true** —— 全量群发点错一次是收不回来的, 先告诉你会发给几个人。
    content_type='edu' 时**强制校验材料已发布**: M15 那道审核闸门的落点就在这儿。
    """
    ch = body.get('channel') or 'sms'
    if ch not in PUSH_CHANNELS:
        return None, 'channel 必须是 {} 之一'.format('/'.join(PUSH_CHANNELS))
    ct = body.get('content_type') or 'notice'
    if ct not in PUSH_CONTENT_TYPES:
        return None, 'content_type 必须是 {} 之一'.format('/'.join(PUSH_CONTENT_TYPES))
    dry = body.get('dry_run')
    dry = True if dry is None else bool(dry)

    ensure_platform_push_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        targets, err = _resolve_push_targets(cur, body)
        if err:
            cur.close()
            return None, err
        targets = [t for t in targets if t]
        if not targets:
            cur.close()
            return None, '没有解析到任何推送对象'
        if len(targets) > PUSH_MAX_TARGETS:
            cur.close()
            return None, '一次最多推送 {} 人, 当前 {} 人 —— 请收窄范围分批发'.format(
                PUSH_MAX_TARGETS, len(targets))

        title = str(body.get('title') or '').strip()
        content = str(body.get('body') or '')
        ref_code = ref_ver = None
        if ct == 'edu':
            ref_code = str(body.get('ref_code') or '').strip()
            if not ref_code:
                cur.close()
                return None, '推送患教内容必须给 ref_code(宣教材料编码)'
            cur.execute("SELECT version, title, body, status FROM platform_edu_material "
                        "WHERE code=%s ORDER BY id DESC LIMIT 1", (ref_code,))
            m = cur.fetchone()
            if not m:
                cur.close()
                return None, '宣教材料不存在: {}'.format(ref_code)
            if m[3] != 'published':
                cur.close()
                # 这是 M15 那道闸门真正起作用的地方。不校验的话前面所有约束都白设。
                return None, ('宣教材料《{}》当前是「{}」, 不能推送给患者。'
                              '只有经人工署名审核发布的材料才能推 —— 这份材料会被患者当医嘱照做, '
                              '未审核的内容里可能有剂量数字或"可自行停药"这类表述'.format(
                                  m[1], EDU_STATUS_LABELS.get(m[3], m[3])))
            ref_ver, title, content = m[0], (title or m[1]), (content or m[2])
        elif not title and not content:
            cur.close()
            return None, 'title 或 body 至少给一个'

        if dry:
            cur.close()
            return {'ok': True, 'dry_run': True, 'channel': ch,
                    'channel_label': PUSH_CHANNELS[ch],
                    'content_type': ct, 'target_kind': body.get('target_kind') or 'patient',
                    'would_send': len(targets), 'sample': targets[:10],
                    'title': title, 'ref_code': ref_code, 'ref_version': ref_ver,
                    'hint': '这是试算, 没有入队。确认后带 dry_run=false 执行',
                    'channel_warning': PUSH_NOT_WIRED}, None

        bid = _batch_id()
        rows = [(bid, ch, ct, body.get('target_kind') or 'patient', t, ref_code, ref_ver,
                 title[:200] or None, content, body.get('mode') or 'manual',
                 body.get('visit_id'), body.get('scheduled_at') or None,
                 body.get('operator') or None) for t in targets]
        cur.executemany("""INSERT INTO platform_push
            (batch_id, channel, content_type, target_kind, patient_no, ref_code, ref_version,
             title, body, mode, visit_id, scheduled_at, status, operator)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued',%s)""", rows)
        cur.close()
        return {'ok': True, 'dry_run': False, 'batch_id': bid, 'queued': len(rows),
                'channel': ch, 'channel_label': PUSH_CHANNELS[ch],
                'ref_code': ref_code, 'ref_version': ref_ver,
                'status': 'queued', 'status_label': PUSH_STATUSES['queued'],
                'channel_warning': PUSH_NOT_WIRED}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def push_from_visit(body):
    """§4.5(2) 自动推送: 把一次访视上配的患教内容入队。

    {visit_id, channel?, operator?, dry_run?}
    只挑访视 items 里 type='edu' 的; 依然要过"必须已发布"这一关。
    """
    try:
        vid = int(body.get('visit_id'))
    except (TypeError, ValueError):
        return None, 'visit_id 必填且为整数'
    ensure_platform_push_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT patient_no, items, name FROM platform_visit WHERE id=%s', (vid,))
        row = cur.fetchone()
        cur.close()
        if not row:
            return None, '访视不存在: {}'.format(vid)
        pno, items, vname = row
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except ValueError:
                items = []
        edus = [i for i in (items or []) if i.get('type') == 'edu' and i.get('ref')]
        if not edus:
            return {'ok': True, 'queued': 0,
                    'note': '这次访视「{}」没有配患教内容(items 里没有 type=edu 的项)'.format(vname)}, None
        out, errs = [], []
        for e in edus:
            r, err = push_create({'channel': body.get('channel') or 'sms', 'content_type': 'edu',
                                  'target_kind': 'patient', 'patient_no': pno,
                                  'ref_code': e['ref'], 'mode': 'auto', 'visit_id': vid,
                                  'dry_run': bool(body.get('dry_run')),
                                  'operator': body.get('operator')})
            if err:
                errs.append({'ref': e['ref'], 'error': err})
            else:
                out.append(r)
        return {'ok': True, 'visit_id': vid, 'patient_no': pno,
                'queued': sum(x.get('queued', 0) for x in out),
                'batches': [x.get('batch_id') for x in out if x.get('batch_id')],
                'blocked': errs,
                'channel_warning': PUSH_NOT_WIRED,
                'note': ('有 {} 份材料没能推出去(多半是还没审核发布) —— 未审核的内容里'
                         '可能有剂量数字或"可自行停药"这类表述'.format(len(errs))) if errs else None}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def query_pushes(patient_no=None, status=None, batch_id=None, content_type=None, limit=300):
    """推送队列。"""
    ensure_platform_push_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ['1=1'], []
        if patient_no:
            where.append('p.patient_no=%s'); params.append(patient_no)
        if status:
            where.append('p.status=%s'); params.append(status)
        if batch_id:
            where.append('p.batch_id=%s'); params.append(batch_id)
        if content_type:
            where.append('p.content_type=%s'); params.append(content_type)
        params.append(int(limit))
        cur.execute("""
            SELECT p.id, p.batch_id, p.channel, p.content_type, p.target_kind, p.patient_no,
                   pt.name, p.ref_code, p.ref_version, p.title, p.mode, p.visit_id,
                   p.status, p.scheduled_at, p.sent_at, p.error, p.operator, p.created_at
            FROM platform_push p
            LEFT JOIN platform_patient pt ON pt.patient_no=p.patient_no
            WHERE {} ORDER BY p.id DESC LIMIT %s
        """.format(' AND '.join(where)), params)
        cols = ['id', 'batch_id', 'channel', 'content_type', 'target_kind', 'patient_no',
                'patient_name', 'ref_code', 'ref_version', 'title', 'mode', 'visit_id',
                'status', 'scheduled_at', 'sent_at', 'error', 'operator', 'created_at']
        out = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            for k in ('scheduled_at', 'sent_at', 'created_at'):
                if d.get(k) is not None and hasattr(d[k], 'strftime'):
                    d[k] = d[k].strftime('%Y-%m-%d %H:%M:%S')
            d['channel_label'] = PUSH_CHANNELS.get(d['channel'], d['channel'])
            d['content_type_label'] = PUSH_CONTENT_TYPES.get(d['content_type'], d['content_type'])
            d['status_label'] = PUSH_STATUSES.get(d['status'], d['status'])
            d['mode_label'] = '自动' if d['mode'] == 'auto' else '手动'
            out.append(d)
        cur.execute("SELECT status, COUNT(*) FROM platform_push GROUP BY status")
        by_status = {a: int(b) for a, b in cur.fetchall()}
        cur.close()
        return {'ok': True, 'count': len(out), 'pushes': out, 'by_status': by_status,
                'channels': PUSH_CHANNELS, 'content_types': PUSH_CONTENT_TYPES,
                'statuses': PUSH_STATUSES, 'channel_warning': PUSH_NOT_WIRED}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def query_followups(patient_no=None, visit_id=None, batch_id=None, limit=300):
    """访视跟进记录。"""
    ensure_platform_push_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ['1=1'], []
        if patient_no:
            where.append('f.patient_no=%s'); params.append(patient_no)
        if visit_id:
            where.append('f.visit_id=%s'); params.append(int(visit_id))
        if batch_id:
            where.append('f.batch_id=%s'); params.append(batch_id)
        params.append(int(limit))
        cur.execute("""
            SELECT f.id, f.visit_id, f.patient_no, p.name, v.name, f.action, f.result,
                   f.new_date, f.operator, f.batch_id, f.created_at
            FROM platform_visit_followup f
            LEFT JOIN platform_patient p ON p.patient_no=f.patient_no
            LEFT JOIN platform_visit v ON v.id=f.visit_id
            WHERE {} ORDER BY f.id DESC LIMIT %s
        """.format(' AND '.join(where)), params)
        cols = ['id', 'visit_id', 'patient_no', 'patient_name', 'visit_name', 'action',
                'result', 'new_date', 'operator', 'batch_id', 'created_at']
        out = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            for k in ('new_date', 'created_at'):
                if d.get(k) is not None and hasattr(d[k], 'strftime'):
                    d[k] = d[k].strftime('%Y-%m-%d' if k == 'new_date' else '%Y-%m-%d %H:%M:%S')
            d['action_label'] = FOLLOWUP_ACTIONS.get(d['action'], d['action'])
            out.append(d)
        cur.close()
        return {'ok': True, 'count': len(out), 'followups': out,
                'actions': FOLLOWUP_ACTIONS}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


# ============ 随访平台 1.1 M21 (导出增强, 方案 §4.6(1)) ============
#
# 三件事必须先说死:
#
# 1) **"CDISC 标准格式"这里做的是 SDTM 风格, 不是 CDISC 合规。**
#    真正的 CDISC 提交需要 define.xml、受控术语(CT)、申办方特定映射和一整套核查。
#    这里产出的是按 SDTM 域(DM/VS/QS/AE)组织、用标准列名(STUDYID/USUBJID/DOMAIN...)
#    的 CSV —— 它能让统计方少做很多整理, 但**不能直接拿去递交**。
#    说成"支持 CDISC"而实际不合规, 在审计时是要出事的。
#
# 2) **加密导出要么真加密, 要么不给文件。**
#    Python 标准库的 zipfile 只能**读**加密 zip, 不能写; 而它能读的那种 ZipCrypto
#    本身就是可以秒破的, 拿来保护患者数据等于没保护。真加密需要 pyzipper(AES-256),
#    当前环境没装。所以勾了"加密"而库不在时, 这里**拒绝产出文件**而不是悄悄给一份
#    明文的 —— 用户勾了加密拿到文件, 一定会以为它是受保护的。
#
# 3) **每一次导出都是一次患者数据出境。** 导出记录(谁、什么时候、导了哪些人的
#    哪些数据、多少行、下载过几次)本身就是这块最要紧的产出, 比导出功能本身更重要。

EXPORT_PICK_MODES = {
    'horizontal': '横向挑选(单分组 · 多 CRF 变量)',
    'vertical':   '纵向挑选(多分组 · 单变量)',
    'history':    '历史挑选(单变量 · 历次随访变化)',
}
EXPORT_JOB_STATUSES = {'queued': '排队中', 'running': '导出中', 'done': '已完成',
                       'failed': '失败', 'expired': '已过期'}
EXPORT_ASYNC_THRESHOLD = 500        # 超过这么多患者就建议走异步
EXPORT_KEEP_HOURS = 48              # 导出文件保留多久
EXPORT_DIR = os.environ.get('PLATFORM_EXPORT_DIR') or '/opt/suifang/exports'

# SDTM 域。列名用 SDTM 的标准写法, 但不声称合规 —— 见本节开头。
SDTM_DOMAINS = {
    'DM': ('人口学', ['STUDYID', 'DOMAIN', 'USUBJID', 'SUBJID', 'SEX', 'AGE', 'AGEU',
                      'ARM', 'ARMCD', 'RFSTDTC']),
    'VS': ('生命体征', ['STUDYID', 'DOMAIN', 'USUBJID', 'VSSEQ', 'VSTESTCD', 'VSTEST',
                        'VSORRES', 'VSORRESU', 'VSDTC']),
    'QS': ('问卷', ['STUDYID', 'DOMAIN', 'USUBJID', 'QSSEQ', 'QSCAT', 'QSTESTCD',
                    'QSTEST', 'QSORRES', 'QSSTRESN', 'QSDTC']),
    'SV': ('访视', ['STUDYID', 'DOMAIN', 'USUBJID', 'VISITNUM', 'VISIT', 'SVSTDTC',
                    'SVUPDES']),
}
SDTM_VS_MAP = {'hr': ('HR', 'Heart Rate', 'beats/min'), 'sbp': ('SYSBP', 'Systolic Blood Pressure', 'mmHg'),
               'dbp': ('DIABP', 'Diastolic Blood Pressure', 'mmHg'), 'temp': ('TEMP', 'Temperature', 'C'),
               'spo2': ('SPO2', 'Oxygen Saturation', '%'), 'sleep': ('SLEEP', 'Sleep Duration', 'min')}

SDTM_DISCLAIMER = ('本导出为 **SDTM 风格**, 不是 CDISC 合规提交件。真正的 CDISC 递交还需要 '
                   'define.xml、受控术语(CT)、申办方特定映射与一整套核查 —— 这里产出的是按 SDTM '
                   '域组织、用标准列名的 CSV, 能让统计方少做很多整理, 但不能直接递交。')
ENCRYPT_UNAVAILABLE = ('导出加密需要 pyzipper(AES-256), 当前服务器没装。'
                       '**已拒绝产出文件** —— 勾了加密却拿到一份明文文件, 比不提供这个选项危险得多。'
                       '装法: pip install pyzipper。注意标准库 zipfile 只能读加密 zip 不能写, '
                       '而它能读的那种 ZipCrypto 本身就是可以秒破的, 保护不了患者数据。')


def _has_pyzipper():
    try:
        import pyzipper       # noqa: F401
        return True
    except ImportError:
        return False


def ensure_platform_export_tables():
    """M21: 导出任务 + 下载留痕 (idempotent)。"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_export_job (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                job_no VARCHAR(40) NOT NULL UNIQUE,
                kind VARCHAR(32) NOT NULL COMMENT 'full/pick/sdtm',
                params JSON DEFAULT NULL COMMENT '导出条件原样留档 —— 事后要能说清"那次导的是谁"',
                status ENUM('queued','running','done','failed','expired') DEFAULT 'queued',
                patient_count INT DEFAULT NULL,
                row_count INT DEFAULT NULL,
                file_name VARCHAR(200) DEFAULT NULL,
                stored_name VARCHAR(160) DEFAULT NULL,
                size_bytes BIGINT DEFAULT NULL,
                sha256 CHAR(64) DEFAULT NULL,
                encrypted TINYINT(1) DEFAULT 0,
                error VARCHAR(500) DEFAULT NULL,
                requested_by VARCHAR(64) DEFAULT NULL,
                download_count INT DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                finished_at DATETIME DEFAULT NULL,
                expires_at DATETIME DEFAULT NULL,
                INDEX idx_status (status), INDEX idx_created (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
              COMMENT='随访平台 M21 导出任务(每一次导出都是一次患者数据出境, 记录本身比功能更重要)'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_export_download (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                job_no VARCHAR(40) NOT NULL,
                operator VARCHAR(64) DEFAULT NULL,
                source_ip VARCHAR(64) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_job (job_no)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M21 导出文件下载留痕'
        """)
        print('[启动] platform_export_job / platform_export_download 表已就绪')
        cur.close()
    except Exception as e:
        print('[启动] ensure_platform_export_tables 失败:', e)
    finally:
        conn.close()


def _csv_bytes(header, rows):
    """拼一份 UTF-8 BOM 的 CSV。Excel 不认没有 BOM 的 UTF-8, 中文会乱码。"""
    q = lambda v: '"' + ('' if v is None else str(v)).replace('"', '""') + '"'
    out = [','.join(q(h) for h in header)]
    out += [','.join(q(c) for c in r) for r in rows]
    # 末尾留一个换行: RFC 4180 允许, Excel 也是这么写的。少了它有些工具会把
    # 最后一行当成"未结束的记录"。读的一方按行拆时要记得末尾会多一个空元素。
    return ('﻿' + '\r\n'.join(out) + '\r\n').encode('utf-8')


def export_variable_pick(spec):
    """§4.6(1) 变量挑选导出。三种模式对应三种透视形状。

    横向: 一个分组的患者 × 多个变量  -> 一行一患者, 一列一变量(最常见的分析用表)
    纵向: 多个分组 × 同一个变量      -> 一行一患者, 带分组列(用来比较组间差异)
    历史: 一个变量 × 历次随访        -> 一行一患者, 一列一次随访(看变化趋势)

    变量走 M16 那套白名单的量表/CRF 引用形式, 不接受任意 SQL。
    """
    mode = spec.get('mode') or 'horizontal'
    if mode not in EXPORT_PICK_MODES:
        return None, None, 'mode 必须是 {} 之一'.format('/'.join(EXPORT_PICK_MODES))
    variables = spec.get('variables') or []
    if not isinstance(variables, list) or not variables:
        return None, None, 'variables 必须是非空数组'
    if len(variables) > 200:
        return None, None, 'variables 最多 200 个'
    for v in variables:
        if not isinstance(v, dict) or v.get('source') not in ('scale', 'crf', 'patient'):
            return None, None, "每个变量要有 source(scale/crf/patient)"
        if v['source'] in ('scale', 'crf') and not (v.get('code') and v.get('field')):
            return None, None, '量表/CRF 变量必须给 code 和 field'
    if mode == 'history' and len(variables) != 1:
        return None, None, '历史挑选是"单变量历次变化", variables 只能给 1 个'

    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ['1=1'], []
        if spec.get('conditions'):
            try:
                where_sql, params = build_search_sql(spec['conditions'])
                where = [where_sql]
            except ValueError as e:
                cur.close()
                return None, None, '筛选条件有问题: {}'.format(e)
        cur.execute('SELECT p.patient_no, p.name, p.gender, p.age, p.group_tag '
                    'FROM platform_patient p WHERE {} ORDER BY p.patient_no LIMIT 5000'.format(
                        ' AND '.join(where)), params)
        pats = [{'no': a, 'name': b, 'gender': c, 'age': d, 'group': e} for a, b, c, d, e in cur.fetchall()]
        if not pats:
            cur.close()
            return None, None, '筛选条件没有命中任何患者'
        nos = [p['no'] for p in pats]
        ph = ','.join(['%s'] * len(nos))

        def var_label(v):
            return v.get('label') or '{}.{}'.format(v.get('code') or v['source'], v.get('field') or '')

        # 取值: (patient_no, var_key, value, when) 四元组, 三种模式共用同一份原料
        vals = {}
        for v in variables:
            key = var_label(v)
            if v['source'] == 'patient':
                f = {'name': 'name', 'gender': 'gender', 'age': 'age', 'group': 'group_tag'}.get(v.get('field'))
                if not f:
                    cur.close()
                    return None, None, '患者字段只支持 name/gender/age/group'
                cur.execute('SELECT patient_no, {} FROM platform_patient WHERE patient_no IN ({})'.format(f, ph), nos)
                for no, val in cur.fetchall():
                    vals.setdefault(no, {}).setdefault(key, []).append((None, val))
            elif v['source'] == 'scale':
                if v['field'] == '__total__':
                    cur.execute("""SELECT patient_no, total_score, created_at FROM platform_scale_response
                                   WHERE scale_code=%s AND status='submitted' AND patient_no IN ({})
                                   ORDER BY created_at""".format(ph), [v['code']] + nos)
                else:
                    cur.execute("""SELECT patient_no,
                                     JSON_UNQUOTE(JSON_EXTRACT(answers, CONCAT('$.', %s))), created_at
                                   FROM platform_scale_response
                                   WHERE scale_code=%s AND status='submitted' AND patient_no IN ({})
                                   ORDER BY created_at""".format(ph), [v['field'], v['code']] + nos)
                for no, val, when in cur.fetchall():
                    vals.setdefault(no, {}).setdefault(key, []).append(
                        (when.strftime('%Y-%m-%d') if hasattr(when, 'strftime') else when, val))
            else:
                cur.execute("""SELECT patient_no,
                                 JSON_UNQUOTE(JSON_EXTRACT(data, CONCAT('$.', %s))), created_at
                               FROM platform_crf_response
                               WHERE crf_code=%s AND status='submitted' AND patient_no IN ({})
                               ORDER BY created_at""".format(ph), [v['field'], v['code']] + nos)
                for no, val, when in cur.fetchall():
                    vals.setdefault(no, {}).setdefault(key, []).append(
                        (when.strftime('%Y-%m-%d') if hasattr(when, 'strftime') else when, val))
        cur.close()

        keys = [var_label(v) for v in variables]
        if mode == 'history':
            k = keys[0]
            maxn = max([len(vals.get(p['no'], {}).get(k, [])) for p in pats] + [1])
            header = ['门诊号', '姓名', '分组'] + ['第{}次'.format(i + 1) for i in range(maxn)] \
                + ['第{}次日期'.format(i + 1) for i in range(maxn)]
            rows = []
            for p in pats:
                seq = vals.get(p['no'], {}).get(k, [])
                rows.append([p['no'], p['name'], p['group']]
                            + [(seq[i][1] if i < len(seq) else '') for i in range(maxn)]
                            + [(seq[i][0] if i < len(seq) else '') for i in range(maxn)])
            note = '历史挑选: 变量「{}」的历次取值。每位患者的次数不同, 列数按最多的那位对齐, 空白 = 该次没有记录'.format(k)
        else:
            header = ['门诊号', '姓名', '性别', '年龄', '分组'] + keys
            rows = []
            for p in pats:
                r = [p['no'], p['name'], {'M': '男', 'F': '女'}.get(p['gender'], ''), p['age'], p['group']]
                for k in keys:
                    seq = vals.get(p['no'], {}).get(k, [])
                    # 取最近一次 —— 横向/纵向都是"一行一患者", 多次填报要压成一个值。
                    # 取最近一次而不是首次, 因为分析通常关心当前状态; 要看变化请用历史模式。
                    r.append(seq[-1][1] if seq else '')
                rows.append(r)
            note = ('{}: 一行一患者。同一变量有多次记录时取**最近一次** —— '
                    '要看历次变化请用"历史挑选"模式'.format(EXPORT_PICK_MODES[mode]))
        return _csv_bytes(header, rows), {'mode': mode, 'patients': len(pats), 'rows': len(rows),
                                          'variables': keys, 'note': note}, None
    except Exception as e:
        traceback.print_exc()
        return None, None, str(e)
    finally:
        conn.close()


def export_sdtm_like(spec):
    """§4.6(1) SDTM 风格导出。返回 (files{name: bytes}, meta, err)。**不是 CDISC 合规件。**"""
    study = re.sub(r'[^0-9A-Za-z_\-]', '', str(spec.get('study_id') or 'STUDY001'))[:20] or 'STUDY001'
    domains = spec.get('domains') or ['DM', 'VS', 'QS', 'SV']
    bad = [d for d in domains if d not in SDTM_DOMAINS]
    if bad:
        return None, None, '未知的 SDTM 域: {} (支持 {})'.format(
            '、'.join(bad), '/'.join(SDTM_DOMAINS))
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = '1=1', []
        if spec.get('conditions'):
            try:
                where, params = build_search_sql(spec['conditions'])
            except ValueError as e:
                cur.close()
                return None, None, '筛选条件有问题: {}'.format(e)
        cur.execute('SELECT p.patient_no FROM platform_patient p WHERE {} LIMIT 5000'.format(where), params)
        nos = [r[0] for r in cur.fetchall()]
        if not nos:
            cur.close()
            return None, None, '筛选条件没有命中任何患者'
        ph = ','.join(['%s'] * len(nos))
        files, counts = {}, {}

        if 'DM' in domains:
            cur.execute("""SELECT p.patient_no, p.gender, p.age, p.group_tag, DATE(p.created_at),
                                  e.group_code
                           FROM platform_patient p
                           LEFT JOIN platform_enrollment e ON e.patient_no=p.patient_no
                             AND e.status='enrolled'
                           WHERE p.patient_no IN ({})""".format(ph), nos)
            rows = [[study, 'DM', '{}-{}'.format(study, a), a,
                     {'M': 'M', 'F': 'F'}.get(b, 'U'), c if c is not None else '', 'YEARS',
                     f or d or '', f or '', e.strftime('%Y-%m-%d') if e else '']
                    for a, b, c, d, e, f in cur.fetchall()]
            files['dm.csv'] = _csv_bytes(SDTM_DOMAINS['DM'][1], rows); counts['DM'] = len(rows)

        if 'VS' in domains:
            cur.execute("""SELECT patient_no, metric, value, day FROM platform_vital_daily
                           WHERE patient_no IN ({}) ORDER BY patient_no, day""".format(ph), nos)
            rows, seq = [], {}
            for no, metric, val, day in cur.fetchall():
                tc, tn, unit = SDTM_VS_MAP.get(metric, (metric.upper(), metric, ''))
                seq[no] = seq.get(no, 0) + 1
                rows.append([study, 'VS', '{}-{}'.format(study, no), seq[no], tc, tn,
                             float(val), unit, day.strftime('%Y-%m-%d') if day else ''])
            files['vs.csv'] = _csv_bytes(SDTM_DOMAINS['VS'][1], rows); counts['VS'] = len(rows)

        if 'QS' in domains:
            cur.execute("""SELECT r.patient_no, r.scale_code, r.answers, r.total_score, r.created_at
                           FROM platform_scale_response r
                           WHERE r.status='submitted' AND r.patient_no IN ({})
                           ORDER BY r.patient_no, r.created_at""".format(ph), nos)
            rows, seq = [], {}
            for no, code, ans, total, when in cur.fetchall():
                if isinstance(ans, str):
                    try:
                        ans = json.loads(ans)
                    except ValueError:
                        ans = {}
                dt = when.strftime('%Y-%m-%d') if hasattr(when, 'strftime') else ''
                for iid, val in sorted((ans or {}).items()):
                    seq[no] = seq.get(no, 0) + 1
                    rows.append([study, 'QS', '{}-{}'.format(study, no), seq[no], code,
                                 '{}{}'.format(code[:4].upper(), iid), iid, val,
                                 val if isinstance(val, (int, float)) else '', dt])
                if total is not None:
                    seq[no] = seq.get(no, 0) + 1
                    rows.append([study, 'QS', '{}-{}'.format(study, no), seq[no], code,
                                 '{}TOT'.format(code[:4].upper()), 'Total Score',
                                 float(total), float(total), dt])
            files['qs.csv'] = _csv_bytes(SDTM_DOMAINS['QS'][1], rows); counts['QS'] = len(rows)

        if 'SV' in domains:
            cur.execute("""SELECT v.patient_no, v.name, v.planned_date, v.status, v.kind
                           FROM platform_visit v WHERE v.patient_no IN ({})
                           ORDER BY v.patient_no, v.planned_date""".format(ph), nos)
            rows, seq = [], {}
            for no, name, day, st, kind in cur.fetchall():
                seq[no] = seq.get(no, 0) + 1
                rows.append([study, 'SV', '{}-{}'.format(study, no), seq[no], name,
                             day.strftime('%Y-%m-%d') if day else '',
                             '{}{}'.format(VISIT_STATUSES.get(st, st),
                                           ' (流程外)' if kind == 'offschedule' else '')])
            files['sv.csv'] = _csv_bytes(SDTM_DOMAINS['SV'][1], rows); counts['SV'] = len(rows)

        cur.close()
        files['README.txt'] = (SDTM_DISCLAIMER + '\n\n生成时间: ' +
                               datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S') +
                               '\n研究编号: ' + study +
                               '\n包含域: ' + '、'.join('{}({} 行)'.format(
                                   d, counts.get(d, 0)) for d in domains) +
                               '\n患者数: ' + str(len(nos)) + '\n').encode('utf-8')
        return files, {'study_id': study, 'patients': len(nos), 'domains': counts,
                       'rows': sum(counts.values()), 'disclaimer': SDTM_DISCLAIMER}, None
    except Exception as e:
        traceback.print_exc()
        return None, None, str(e)
    finally:
        conn.close()


def _pack_zip(files, password=None):
    """打包。password 给了就必须真加密, 加密不了就报错 —— 绝不产出明文冒充加密件。"""
    import io as _io
    buf = _io.BytesIO()
    if password:
        if not _has_pyzipper():
            return None, ENCRYPT_UNAVAILABLE
        import pyzipper
        with pyzipper.AESZipFile(buf, 'w', compression=pyzipper.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES) as z:
            z.setpassword(password.encode('utf-8'))
            for name, data in files.items():
                z.writestr(name, data)
    else:
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            for name, data in files.items():
                z.writestr(name, data)
    return buf.getvalue(), None


_EXPORT_LOCK = threading.Lock()


def export_job_create(body):
    """建一个导出任务。{kind: full|pick|sdtm, params{}, encrypt?, password?, requested_by?}

    大样本自动走后台 —— 同步导出会把请求占住几十秒, 期间整个服务只能排队
    (这是个单线程 HTTP server)。
    """
    kind = body.get('kind') or 'pick'
    if kind not in ('full', 'pick', 'sdtm'):
        return None, 'kind 必须是 full/pick/sdtm'
    encrypt = bool(body.get('encrypt'))
    password = str(body.get('password') or '')
    if encrypt:
        if not _has_pyzipper():
            return None, ENCRYPT_UNAVAILABLE
        if len(password) < 8:
            return None, '加密导出的口令至少 8 位'

    ensure_platform_export_tables()
    job_no = datetime.datetime.now().strftime('EX%Y%m%d%H%M%S%f')[:20]
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""INSERT INTO platform_export_job
                       (job_no, kind, params, status, encrypted, requested_by, expires_at)
                       VALUES (%s,%s,%s,'queued',%s,%s,DATE_ADD(NOW(), INTERVAL %s HOUR))""",
                    (job_no, kind, json.dumps(body.get('params') or {}, ensure_ascii=False),
                     1 if encrypt else 0, body.get('requested_by') or None, EXPORT_KEEP_HOURS))
        cur.close()
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()

    t = threading.Thread(target=_export_job_run, args=(job_no, kind, body.get('params') or {},
                                                       password if encrypt else None), daemon=True)
    t.start()
    return {'ok': True, 'job_no': job_no, 'kind': kind, 'status': 'queued',
            'encrypted': encrypt,
            'note': ('已转后台导出。样本量大时同步导出会把请求占住几十秒, 期间整个服务只能排队。'
                     '完成后到导出记录里下载, 文件保留 {} 小时'.format(EXPORT_KEEP_HOURS))}, None


def _export_job_run(job_no, kind, params, password):
    """后台跑导出。任何异常都要写回 job, 不能让任务永远卡在 running。"""
    def finish(**kw):
        conn = get_connection()
        try:
            cur = conn.cursor()
            sets = ', '.join('{}=%s'.format(k) for k in kw)
            cur.execute('UPDATE platform_export_job SET {}, finished_at=NOW() WHERE job_no=%s'.format(sets),
                        list(kw.values()) + [job_no])
            cur.close()
        except Exception:
            traceback.print_exc()
        finally:
            conn.close()

    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE platform_export_job SET status='running' WHERE job_no=%s", (job_no,))
        cur.close(); conn.close()

        if kind == 'pick':
            data, meta, err = export_variable_pick(params)
            if err:
                return finish(status='failed', error=err[:500])
            files = {'变量挑选导出.csv': data,
                     'README.txt': (meta['note'] + '\n\n生成时间: ' +
                                    datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')).encode('utf-8')}
            pc, rc = meta['patients'], meta['rows']
        elif kind == 'sdtm':
            files, meta, err = export_sdtm_like(params)
            if err:
                return finish(status='failed', error=err[:500])
            pc, rc = meta['patients'], meta['rows']
        else:
            body, fname, mime, err = platform_export('all', params.get('patient_no'),
                                                     int(params.get('days') or 90))
            if err:
                return finish(status='failed', error=err[:500])
            files = {fname: body}
            pc = rc = None

        blob, err = _pack_zip(files, password)
        if err:
            return finish(status='failed', error=err[:500])
        import hashlib
        sha = hashlib.sha256(blob).hexdigest()
        stored = '{}.zip'.format(job_no)
        try:
            if not os.path.isdir(EXPORT_DIR):
                os.makedirs(EXPORT_DIR)
            with open(os.path.join(EXPORT_DIR, stored), 'wb') as f:
                f.write(blob)
        except OSError as e:
            return finish(status='failed', error='写文件失败: {}'.format(e)[:500])
        finish(status='done', patient_count=pc, row_count=rc,
               file_name='随访导出_{}.zip'.format(job_no), stored_name=stored,
               size_bytes=len(blob), sha256=sha)
    except Exception as e:
        traceback.print_exc()
        finish(status='failed', error=str(e)[:500])


def export_job_query(job_no=None, status=None, limit=100):
    """导出记录。**这是这块最要紧的产出** —— 每一次导出都是一次患者数据出境。"""
    ensure_platform_export_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        # 过期的自动标出来, 免得有人点了下载才发现文件没了
        cur.execute("UPDATE platform_export_job SET status='expired' "
                    "WHERE status='done' AND expires_at < NOW()")
        where, params = ['1=1'], []
        if job_no:
            where.append('job_no=%s'); params.append(job_no)
        if status:
            where.append('status=%s'); params.append(status)
        params.append(int(limit))
        cur.execute("""SELECT job_no, kind, params, status, patient_count, row_count, file_name,
                              size_bytes, sha256, encrypted, error, requested_by, download_count,
                              created_at, finished_at, expires_at
                       FROM platform_export_job WHERE {} ORDER BY id DESC LIMIT %s""".format(
                           ' AND '.join(where)), params)
        cols = ['job_no', 'kind', 'params', 'status', 'patient_count', 'row_count', 'file_name',
                'size_bytes', 'sha256', 'encrypted', 'error', 'requested_by', 'download_count',
                'created_at', 'finished_at', 'expires_at']
        out = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            for k in ('created_at', 'finished_at', 'expires_at'):
                if d.get(k) is not None and hasattr(d[k], 'strftime'):
                    d[k] = d[k].strftime('%Y-%m-%d %H:%M:%S')
            if isinstance(d.get('params'), str):
                try:
                    d['params'] = json.loads(d['params'])
                except ValueError:
                    pass
            d['status_label'] = EXPORT_JOB_STATUSES.get(d['status'], d['status'])
            d['encrypted'] = bool(d['encrypted'])
            d['sha256_short'] = (d.get('sha256') or '')[:16]
            out.append(d)
        if job_no and out:
            cur.execute("""SELECT operator, source_ip, created_at FROM platform_export_download
                           WHERE job_no=%s ORDER BY id DESC LIMIT 50""", (job_no,))
            out[0]['downloads'] = [{'operator': a, 'source_ip': b,
                                    'at': c.strftime('%Y-%m-%d %H:%M:%S') if hasattr(c, 'strftime') else c}
                                   for a, b, c in cur.fetchall()]
        cur.close()
        return {'ok': True, 'count': len(out), 'jobs': out,
                'statuses': EXPORT_JOB_STATUSES, 'keep_hours': EXPORT_KEEP_HOURS,
                'encryption_available': _has_pyzipper(),
                'encryption_note': None if _has_pyzipper() else ENCRYPT_UNAVAILABLE,
                'pick_modes': EXPORT_PICK_MODES, 'sdtm_disclaimer': SDTM_DISCLAIMER}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def export_job_fetch(job_no, operator=None, source_ip=None):
    """取导出文件, 并记一条下载留痕。返回 (bytes, meta, err)。"""
    if not job_no:
        return None, None, 'job_no 必填'
    ensure_platform_export_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT status, stored_name, file_name, sha256, expires_at '
                    'FROM platform_export_job WHERE job_no=%s', (job_no,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, None, '导出任务不存在: {}'.format(job_no)
        st, stored, fname, sha, exp = row
        if st != 'done':
            cur.close()
            return None, None, '任务当前状态是「{}」, 还没有可下载的文件'.format(
                EXPORT_JOB_STATUSES.get(st, st))
        if exp and exp < datetime.datetime.now():
            cur.execute("UPDATE platform_export_job SET status='expired' WHERE job_no=%s", (job_no,))
            cur.close()
            return None, None, '导出文件已过期(保留 {} 小时), 请重新导出'.format(EXPORT_KEEP_HOURS)
        path = os.path.join(EXPORT_DIR, os.path.basename(stored))
        if not os.path.isfile(path):
            cur.close()
            return None, None, '文件在磁盘上找不到, 可能已被清理'
        with open(path, 'rb') as f:
            blob = f.read()
        import hashlib
        if hashlib.sha256(blob).hexdigest() != sha:
            cur.close()
            return None, None, '文件内容哈希与导出时不一致 —— 文件可能被替换或损坏, 已拒绝下发'
        cur.execute("""INSERT INTO platform_export_download (job_no, operator, source_ip)
                       VALUES (%s,%s,%s)""", (job_no, operator, (source_ip or '')[:64] or None))
        cur.execute('UPDATE platform_export_job SET download_count=download_count+1 WHERE job_no=%s',
                    (job_no,))
        cur.close()
        return blob, {'orig_name': fname, 'sha256': sha, 'ext': 'zip', 'title': fname}, None
    except Exception as e:
        traceback.print_exc()
        return None, None, str(e)
    finally:
        conn.close()


# ============ 随访平台 1.1 M22 (大模型健康咨询 + 高风险分诊, 方案 §4.5(1) / §4.4(2)) ============
#
# 这是整个平台风险最高的一块 —— 输出直接给患者看, 而患者会照做。
# 方案自己写了两条硬要求, 正好是这块的骨架:
#   §4.5(1) "不提供诊断与处方, 所有对话全程留痕可审核"
#   §4.4(2) "高风险场景下**立即中断常规智能回复**, 触发预警提示、人工干预与转接"
#
# 由此定下四条不可让步的规则:
#
# 1) **急症分诊前置且确定性, 不经过模型。**
#    把"我胸口压着痛、左手发麻"发给模型然后指望它说对, 是把一条命押在采样器上 ——
#    同一个问题问两次可能得到不同答案。所以分诊是代码里的正则, 在调模型**之前**跑;
#    命中急症/自伤就直接返回固定话术并落预警, **根本不调模型**。
#
# 2) **不把患者身份发给第三方。** DeepSeek 是外部服务。发出去之前把门诊号、身份证、
#    手机号、姓名从问题里剔掉 —— 咨询内容本身对模型有用, 患者是谁对它没用。
#
# 3) **模型的回答要再过一遍内容体检。** 复用 M15 那套(剂量数字/可自行停药/不必就医)。
#    模型答得再流畅, 只要冒出一个剂量数字就得拦下 —— 那正是我们最怕它说的东西。
#
# 4) **全程留痕。** 问了什么、分诊判成什么、有没有调模型、答了什么、拦没拦、多久。
#    这既是合规要求, 也是唯一能事后发现"模型开始乱说了"的途径。

# 分诊等级。数字越小越紧急, 前两级中断常规回复。
TRIAGE_LEVELS = {
    'emergency':   (0, '急症', True),
    'self_harm':   (0, '自伤/自杀风险', True),
    'deterioration': (1, '病情恶化', False),
    'med_change':  (2, '用药调整请求', False),
    'diagnosis':   (2, '求诊断', False),
    'normal':      (9, '一般咨询', False),
}

# 分诊规则。判断依据是"患者照着模型的回答做, 最坏会发生什么"。
# 这些正则宁可多报不可漏报 —— 误判成急症的代价是让人多跑一趟医院,
# 漏判的代价是有人在家里等着。
TRIAGE_RULES = [
    ('self_harm', r'(自杀|轻生|不想活|活不下去|不想活了|结束(自己的)?生命|了结自己|'
                  r'伤害自己|自残|割腕|跳楼|一了百了|活着没(有)?意思|活着没(有)?意义|想死)'),

    # 急症。**部位和症状之间允许插字** —— 原先写死"胸口痛", 结果"胸口很痛""胸口有点疼"
    # 全漏了, 而那恰恰是中文里说胸痛最自然的说法。漏一句的后果是有人在家里等着。
    ('emergency',
     r'('
     r'(胸口|胸部|胸前|心口|心前区|前胸)[^。！？，,]{0,4}(痛|疼|闷|压|憋|难受|不适)'
     r'|胸痛|胸闷|心绞痛'
     r'|(呼吸|喘气|气)[^。！？，,]{0,3}(困难|费力|不上来|不过来)|喘不(上|过)(来|气)|上不来气'
     r'|(意识|神志)[^。！？，,]{0,3}(不清|模糊|丧失)|昏迷|晕倒|昏过去|不省人事'
     r'|抽搐|惊厥|抽风'
     r'|(出|流)血[^。！？，,]{0,4}(止不住|不止|停不下)|(鼻|牙龈|伤口)?血[^。！？，,]{0,3}止不住'
     r'|吐血|咯血|呕血|便血|大出血'
     r'|(剧烈|突然|从没这么|特别厉害的?)[^。！？，,]{0,3}(头痛|头疼)'
     r'|(头痛|头疼)[^。！？，,]{0,4}(剧烈|欲裂|要裂|裂开|从没这么|受不了|厉害得)'
     r'|(半边|一侧|左边|右边|左半|右半|一边)[^。！？，,]{0,5}(麻|无力|没(有)?力气|不能动|动不了|抬不起|瘫)'
     r'|偏瘫|嘴(角)?歪|口角歪斜|说话[^。！？，,]{0,3}(不清|说不出|含糊)|突然(失明|失语|看不见)'
     r'|高(热|烧)[^。！？，,]{0,3}不退|体温[^。！？]{0,4}4[01]'
     r'|血压[^。！？]{0,4}(2[0-9][0-9]|1[89][0-9])'
     r')'),

    ('deterioration', r'(越来越(严重|重|厉害|差|不好)|(持续|一直)[^。！？，,]{0,3}(加重|不缓解|不见好|没好)|'
                      r'比(以前|之前|上次)[^。！？，,]{0,4}(差|重|严重)|反复发作|加重了|恶化)'),

    # 用药调整。同样按结构匹配 —— 写死"能不能停药"会漏掉"能不能把药停了""这药还要吃吗"。
    # 宁可多报: 误报的代价只是让人去问医生, 那本来就是对的。
    ('med_change', r'((停|减|加|换|不吃|少吃|多吃|不用吃|别吃)[^。！？]{0,6}药|'
                   r'药[^。！？]{0,6}(停|减|加|换|不吃|少吃|多吃|停了|减半|加倍)|'
                   r'(剂量|用量)[^。！？]{0,6}(改|调|加|减|变)|'
                   r'还(要|需要|得)(不要)?(继续)?吃|'
                   r'吃(多少|几片|几粒|几次|多久)|加到多少|减到多少)'),

    # 求诊断。"我这是不是糖尿病"里没有"什么病"三个字, 原来的写法抓不到。
    ('diagnosis', r'((我|这)[^。！？，,]{0,4}是不是[^。！？，,]{0,8}(病|症|癌|炎|梗|瘤|综合征)|'
                  r'我这是(什么|啥)|(帮|给)我(看看|诊断|判断)(一下)?(是什么|什么病|是不是)?|'
                  r'我(得|患)了(什么|啥)|确诊(了)?(吗|没)|我是不是(有|得了|患了))'),
]


# 急症/自伤的固定话术。**不经过模型** —— 这几句必须每次一模一样。
# 求助方式只写 120 和本院随访医生这两个确定有效的; 心理援助热线号码由院方在
# CRISIS_HOTLINE 里配 —— 硬编码一个可能已经停用的号码, 会让正在危机中的人打到空号。
CRISIS_HOTLINE = os.environ.get('PLATFORM_CRISIS_HOTLINE') or ''
TRIAGE_REPLIES = {
    'emergency': (
        '您描述的情况可能需要**立即就医**，我不能替代急诊判断。\n\n'
        '请现在就做这两件事：\n'
        '1. 拨打 **120**，或让身边的人立刻送您去最近医院的急诊；\n'
        '2. 如果身边有人，请告诉他们您现在的不舒服。\n\n'
        '我已经把这条消息标记出来并通知了随访团队。在见到医生之前，请不要自行用药。'),
    'self_harm': (
        '我看到您现在很难受。这样的念头出现的时候，人是真的很痛苦，您愿意说出来，这本身很不容易。\n\n'
        '请您现在联系一个能马上到您身边的人 —— 家人、朋友，或者拨打 **120**。\n'
        '{hotline}'
        '如果此刻有立即伤害自己的想法，请立刻拨打 120 或到最近医院的急诊。\n\n'
        '我已经通知了您的随访团队，会有人尽快联系您。您不需要一个人扛着。'),
    'med_change': (
        '用药怎么调整，我不能给建议 —— 剂量因人而异，还要看您最近的检查结果和其他在用的药，'
        '这些只有您的医生手上有。\n\n'
        '请**不要自行停药或改剂量**：有些药突然停用会让病情反弹，有些会有停药反应。\n\n'
        '请联系您的随访医生。如果是因为有不舒服才想停药，把那个不舒服描述给我，我可以先帮您了解一般情况。'),
    'diagnosis': (
        '我不能做诊断 —— 诊断要结合体格检查、化验和影像，还要医生当面看，这些我都做不到，'
        '猜一个反而会耽误您。\n\n'
        '如果您想了解某个症状一般和哪些情况有关、什么时候该去看医生，我可以说明；'
        '但"您是不是得了某个病"这个问题，请交给您的医生。'),
}

CONSULT_DISCLAIMER = ('本回答由 AI 生成，仅供健康科普参考，**不构成诊断、处方或治疗建议**。'
                      '用药与治疗方案请遵医嘱；若症状加重或出现新的不适，请及时联系随访医生或就近就医。')

CONSULT_SYSTEM_PROMPT = """你是一家医院随访平台上的健康科普助手，回答对象是正在随访中的患者本人。

绝对不能做的事：
1. 不做诊断。不要说"你这是XX病""考虑是XX"。
2. 不给处方、不给具体剂量、不建议增减停换任何药物。不要写出任何"数字+mg/片/粒"的用法。
3. 不要说"不用去医院""observe即可""不必就医"这类会让人延误就诊的话。
4. 不要做绝对承诺（"一定能好""保证没事""无副作用"）。

要做的事：
- 用通俗的中文解释症状一般与什么有关、日常可以注意什么。
- 每次回答结尾，明确写出"出现哪些情况需要联系随访医生或就医"。
- 不确定的就说不确定，并建议问医生。宁可少说，不要编。
- 简洁，控制在 400 字以内，不要用夸张语气。

你面对的是真实患者，他们会照着你的话做。"""

CONSULT_MAX_CHARS = 800
CONSULT_TIMEOUT = 25


def _scrub_identifiers(text):
    """把患者身份从要发给第三方的文本里剔掉。返回 (清洗后文本, 命中的类型列表)。

    咨询内容本身对模型有用, "患者是谁"对它没有任何用处 —— 发出去只是白白扩大暴露面。
    """
    hits = []
    out = text or ''
    subs = [
        ('身份证号', r'\b[1-9]\d{5}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b', '[身份证已隐去]'),
        ('手机号', r'\b1[3-9]\d{9}\b', '[手机号已隐去]'),
        ('门诊号', r'\b[A-Z]{0,3}\d{6,12}\b', '[编号已隐去]'),
        ('邮箱', r'\b[\w.+-]+@[\w-]+\.[\w.]+\b', '[邮箱已隐去]'),
    ]
    for label, pat, repl in subs:
        new = re.sub(pat, repl, out)
        if new != out:
            hits.append(label)
            out = new
    return out, hits


def triage_message(text):
    """确定性分诊。**在调模型之前跑**, 命中前两级就中断。

    返回 {level, label, interrupt, matched, reply}。
    宁可多报不可漏报: 误判成急症的代价是让人多跑一趟医院, 漏判的代价是有人在家里等着。
    """
    t = str(text or '')
    for level, pat in TRIAGE_RULES:
        m = re.search(pat, t)
        if m:
            rank, label, interrupt = TRIAGE_LEVELS[level]
            reply = TRIAGE_REPLIES.get(level)
            if level == 'self_harm':
                hot = ('如果愿意，也可以拨打心理援助热线 **{}**。\n'.format(CRISIS_HOTLINE)
                       if CRISIS_HOTLINE else '')
                reply = reply.format(hotline=hot)
            return {'level': level, 'rank': rank, 'label': label, 'interrupt': interrupt,
                    'matched': m.group(0), 'reply': reply}
    return {'level': 'normal', 'rank': 9, 'label': '一般咨询', 'interrupt': False,
            'matched': None, 'reply': None}


def _call_deepseek(question, history=None):
    """调 DeepSeek。返回 (answer, meta, error)。没配 key 时优雅降级, 不抛异常。"""
    key = os.environ.get('DEEPSEEK_API_KEY') or ''
    if not key:
        return None, None, ('未配置 DEEPSEEK_API_KEY, 健康咨询暂不可用。'
                            '密钥应写进 /opt/suifang/wx.env(600 权限), 不要写进代码或仓库')
    base = os.environ.get('DEEPSEEK_BASE_URL') or 'https://api.deepseek.com'
    model = os.environ.get('DEEPSEEK_MODEL') or 'deepseek-chat'
    msgs = [{'role': 'system', 'content': CONSULT_SYSTEM_PROMPT}]
    for h in (history or [])[-6:]:
        if h.get('role') in ('user', 'assistant') and h.get('content'):
            msgs.append({'role': h['role'], 'content': str(h['content'])[:1500]})
    msgs.append({'role': 'user', 'content': question})
    payload = json.dumps({'model': model, 'messages': msgs,
                          'temperature': 0.3, 'max_tokens': 900,
                          'stream': False}).encode('utf-8')
    req = urllib.request.Request(base.rstrip('/') + '/chat/completions', data=payload,
                                 headers={'Content-Type': 'application/json',
                                          'Authorization': 'Bearer ' + key})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=CONSULT_TIMEOUT) as r:
            data = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = ''
        try:
            body = e.read().decode('utf-8', 'replace')[:200]
        except Exception:
            pass
        return None, None, 'DeepSeek 返回 HTTP {}: {}'.format(e.code, body)
    except Exception as e:
        return None, None, 'DeepSeek 调用失败: {}'.format(e)
    try:
        answer = data['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError):
        return None, None, 'DeepSeek 返回格式异常: {}'.format(str(data)[:200])
    usage = data.get('usage') or {}
    return answer, {'model': data.get('model') or model,
                    'prompt_tokens': usage.get('prompt_tokens'),
                    'completion_tokens': usage.get('completion_tokens'),
                    'elapsed_ms': int((time.time() - t0) * 1000)}, None


def ensure_platform_consult_tables():
    """M22: 咨询留痕 (idempotent)。"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_consult (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(40) DEFAULT NULL,
                patient_no VARCHAR(64) DEFAULT NULL,
                question MEDIUMTEXT NOT NULL COMMENT '患者原话(库里留原文, 发给第三方的是清洗过的)',
                sent_text MEDIUMTEXT DEFAULT NULL COMMENT '实际发给模型的文本(已剔除身份信息)',
                scrubbed JSON DEFAULT NULL COMMENT '剔掉了哪几类身份信息',
                triage_level VARCHAR(24) DEFAULT 'normal',
                triage_matched VARCHAR(120) DEFAULT NULL,
                interrupted TINYINT(1) DEFAULT 0 COMMENT '是否中断了常规回复(急症/自伤)',
                answer MEDIUMTEXT DEFAULT NULL,
                answer_source VARCHAR(16) DEFAULT NULL COMMENT 'triage(固定话术)/model/blocked',
                blocked_findings JSON DEFAULT NULL COMMENT '模型回答被内容体检拦下的条目',
                model VARCHAR(64) DEFAULT NULL,
                prompt_tokens INT DEFAULT NULL,
                completion_tokens INT DEFAULT NULL,
                elapsed_ms INT DEFAULT NULL,
                error VARCHAR(500) DEFAULT NULL,
                reviewed_by VARCHAR(64) DEFAULT NULL,
                review_note VARCHAR(500) DEFAULT NULL,
                reviewed_at DATETIME DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_patient (patient_no, created_at),
                INDEX idx_triage (triage_level),
                INDEX idx_session (session_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
              COMMENT='随访平台 M22 健康咨询留痕 (方案 §4.5(1) 要求全程可审核)'
        """)
        print('[启动] platform_consult 表已就绪')
        cur.close()
    except Exception as e:
        print('[启动] ensure_platform_consult_tables 失败:', e)
    finally:
        conn.close()


def _consult_alarm(patient_no, level, question):
    """高风险落一条预警, 让随访团队看得到。幂等靠 dedup_key。"""
    if not patient_no:
        return
    try:
        import hashlib
        key = 'consult:{}:{}:{}'.format(level, patient_no,
                                        hashlib.md5(question.encode('utf-8')).hexdigest()[:12])
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""INSERT INTO platform_alarm
                       (patient_no, alarm_type, severity, payload_json, source_chain, dedup_key,
                        status, occurred_at)
                       VALUES (%s,%s,'crit',%s,'consult',%s,'new',NOW())
                       ON DUPLICATE KEY UPDATE id=id""",
                    (patient_no, 'consult_' + level,
                     json.dumps({'rule': 'triage', 'level': level,
                                 'excerpt': question[:120]}, ensure_ascii=False), key))
        cur.close(); conn.close()
    except Exception:
        traceback.print_exc()


def consult_ask(body):
    """患者提问 -> 分诊 -> (必要时)调模型 -> 内容体检 -> 落痕。

    {question, patient_no?, session_id?, history?[]}
    """
    q = str(body.get('question') or '').strip()
    if not q:
        return None, '问题不能为空'
    if len(q) > CONSULT_MAX_CHARS:
        return None, '问题过长(超过 {} 字), 请拆成几次问'.format(CONSULT_MAX_CHARS)
    patient_no = str(body.get('patient_no') or '').strip() or None
    session_id = str(body.get('session_id') or '').strip() or \
        datetime.datetime.now().strftime('C%Y%m%d%H%M%S%f')[:22]

    ensure_platform_consult_tables()
    tri = triage_message(q)
    sent, scrubbed = _scrub_identifiers(q)

    answer = source = model = err = None
    ptok = ctok = ms = None
    blocked = []

    if tri['interrupt']:
        # §4.4(2): 高风险立即中断常规智能回复。**不调模型。**
        answer, source = tri['reply'], 'triage'
        _consult_alarm(patient_no, tri['level'], q)
    elif tri['level'] in ('med_change', 'diagnosis'):
        # 这两类不是急症, 但也不该让模型去回答 —— 它一旦顺着答, 就是在给处方/下诊断
        answer, source = TRIAGE_REPLIES[tri['level']], 'triage'
    else:
        raw, meta, err = _call_deepseek(sent, body.get('history'))
        if err:
            answer, source = None, None
        else:
            findings = scan_edu_content(raw)
            blocked = [f for f in findings if f['level'] == 'block']
            if blocked:
                # 模型答得再流畅, 冒出剂量数字或"可自行停药"就得拦 —— 那正是最怕它说的
                answer = ('这个问题涉及用药剂量或治疗调整，我不能回答 —— '
                          '这类建议必须由了解您完整病情的医生给出。请联系您的随访医生。\n\n'
                          '如果您想了解的是"这个药一般是干什么的""有哪些常见不适"，可以换个说法再问我。')
                source = 'blocked'
            else:
                answer, source = raw, 'model'
            model = (meta or {}).get('model')
            ptok, ctok, ms = ((meta or {}).get('prompt_tokens'),
                              (meta or {}).get('completion_tokens'),
                              (meta or {}).get('elapsed_ms'))
        if tri['level'] == 'deterioration' and answer:
            # 病情恶化不中断, 但把就医提示顶到最前面, 并落预警
            answer = ('您提到症状在加重 —— 这种情况建议尽快联系随访医生或到院复诊，'
                      '不要只靠自行观察。下面是一些一般性说明，供您参考：\n\n' + answer)
            _consult_alarm(patient_no, tri['level'], q)

    if answer:
        answer = answer.rstrip() + '\n\n---\n' + CONSULT_DISCLAIMER

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""INSERT INTO platform_consult
            (session_id, patient_no, question, sent_text, scrubbed, triage_level, triage_matched,
             interrupted, answer, answer_source, blocked_findings, model,
             prompt_tokens, completion_tokens, elapsed_ms, error)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (session_id, patient_no, q, sent if source == 'model' or blocked else None,
                     json.dumps(scrubbed, ensure_ascii=False) if scrubbed else None,
                     tri['level'], tri['matched'], 1 if tri['interrupt'] else 0,
                     answer, source, json.dumps(blocked, ensure_ascii=False) if blocked else None,
                     model, ptok, ctok, ms, (err or '')[:500] or None))
        cid = cur.lastrowid
        cur.close()
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()

    if err:
        return {'ok': False, 'id': cid, 'session_id': session_id,
                'triage': {'level': tri['level'], 'label': tri['label']},
                'error': err,
                'fallback': ('健康咨询暂时不可用。如果是紧急情况请拨打 120；'
                             '其他问题请联系您的随访医生。')}, None
    return {'ok': True, 'id': cid, 'session_id': session_id,
            'answer': answer,
            'answer_source': source,
            'triage': {'level': tri['level'], 'label': tri['label'],
                       'interrupted': tri['interrupt'], 'matched': tri['matched']},
            'scrubbed': scrubbed,
            'blocked_findings': blocked,
            'model': model, 'elapsed_ms': ms,
            'note': ('高风险内容已中断常规回复并通知随访团队' if tri['interrupt']
                     else ('模型回答含高危表述, 已拦下并改为转人工' if source == 'blocked'
                           else None))}, None


def query_consults(patient_no=None, level=None, session_id=None, unreviewed=False, limit=200):
    """咨询留痕。方案 §4.5(1) 要求"全程留痕可审核" —— 这是它的落点。"""
    ensure_platform_consult_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ['1=1'], []
        if patient_no:
            where.append('patient_no=%s'); params.append(patient_no)
        if level:
            where.append('triage_level=%s'); params.append(level)
        if session_id:
            where.append('session_id=%s'); params.append(session_id)
        if unreviewed:
            where.append('reviewed_at IS NULL')
        params.append(int(limit))
        cur.execute("""SELECT id, session_id, patient_no, question, sent_text, scrubbed,
                              triage_level, triage_matched, interrupted, answer, answer_source,
                              blocked_findings, model, prompt_tokens, completion_tokens,
                              elapsed_ms, error, reviewed_by, review_note, reviewed_at, created_at
                       FROM platform_consult WHERE {} ORDER BY id DESC LIMIT %s""".format(
                           ' AND '.join(where)), params)
        cols = ['id', 'session_id', 'patient_no', 'question', 'sent_text', 'scrubbed',
                'triage_level', 'triage_matched', 'interrupted', 'answer', 'answer_source',
                'blocked_findings', 'model', 'prompt_tokens', 'completion_tokens',
                'elapsed_ms', 'error', 'reviewed_by', 'review_note', 'reviewed_at', 'created_at']
        out = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            for k in ('reviewed_at', 'created_at'):
                if d.get(k) is not None and hasattr(d[k], 'strftime'):
                    d[k] = d[k].strftime('%Y-%m-%d %H:%M:%S')
            for k in ('scrubbed', 'blocked_findings'):
                if isinstance(d.get(k), str):
                    try:
                        d[k] = json.loads(d[k])
                    except ValueError:
                        pass
            d['interrupted'] = bool(d['interrupted'])
            d['triage_label'] = TRIAGE_LEVELS.get(d['triage_level'], (9, d['triage_level'], False))[1]
            d['source_label'] = {'triage': '固定话术(未调模型)', 'model': '模型回答',
                                 'blocked': '模型回答被拦'}.get(d['answer_source'], d['answer_source'])
            out.append(d)
        cur.execute("SELECT triage_level, COUNT(*) FROM platform_consult GROUP BY triage_level")
        by_level = {a: int(b) for a, b in cur.fetchall()}
        cur.execute("SELECT COUNT(*) FROM platform_consult WHERE interrupted=1")
        n_int = int(cur.fetchone()[0] or 0)
        cur.close()
        return {'ok': True, 'count': len(out), 'consults': out, 'by_level': by_level,
                'interrupted_total': n_int,
                'levels': {k: v[1] for k, v in TRIAGE_LEVELS.items()},
                'llm_configured': bool(os.environ.get('DEEPSEEK_API_KEY')),
                'crisis_hotline': CRISIS_HOTLINE or None,
                'hotline_note': None if CRISIS_HOTLINE else
                    ('未配置心理援助热线(PLATFORM_CRISIS_HOTLINE)。自伤风险的回复目前只给 120 '
                     '和本院随访医生 —— 这两个确定有效。刻意不硬编码一个可能已停用的号码: '
                     '正在危机中的人打到空号, 比不给号码更糟')}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def consult_review(body):
    """医护复核一条咨询 {id, operator, note}。"""
    try:
        cid = int(body.get('id'))
    except (TypeError, ValueError):
        return None, 'id 必填且为整数'
    op = str(body.get('operator') or '').strip()
    if not op:
        return None, '复核必须署名'
    ensure_platform_consult_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT id FROM platform_consult WHERE id=%s', (cid,))
        if not cur.fetchone():
            cur.close()
            return None, '咨询记录不存在: {}'.format(cid)
        cur.execute("""UPDATE platform_consult SET reviewed_by=%s, review_note=%s,
                       reviewed_at=NOW() WHERE id=%s""",
                    (op, str(body.get('note') or '')[:500] or None, cid))
        cur.close()
        return {'ok': True, 'id': cid, 'reviewed_by': op}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


# ============ 随访平台 1.1 M23 (主动筛查与自助收录, 方案 §4.1) ============
#
# §4.1(2) 的"联动纳排规则批量入组"已由 M18 覆盖, §4.1(1) 的 Excel 导入由 M9 覆盖。
# 这一版补的是**自助填报入口**和**筛查任务全周期**。
#
# 公开填报链接是整个平台唯一一个"不登录就能访问"的入口, 它打在患者库上。
# 四条规矩:
#
# 1) **写入单向。** 提交能创建一条待审记录, 但**绝不回显任何已有患者的数据**。
#    如果自助页会按门诊号回显"您的既往信息", 那任何人猜一个门诊号就能读别人的病历 ——
#    这是这块最容易犯也最致命的错。所以公开接口只吐表单结构, 从不吐患者数据。
#
# 2) **提交进待审, 不直接建档。** 陌生人填的东西直接进 platform_patient, 等于把
#    患者名册的写权限交给任何拿到链接的人。必须有人看过才采纳。
#
# 3) **token 用 secrets 生成**, 不用时间戳/自增/md5(可预测的都不算)。带有效期和
#    次数上限 —— 印在海报上的链接会一直被扫, 没有上限就等于永久开放。
#
# 4) **限流。** 同一个 token 短时间内狂提交, 多半不是患者在填表。

SCREEN_TASK_STATUSES = {'draft': '草稿', 'pending': '待审批', 'running': '进行中',
                        'paused': '已暂停', 'ended': '已结束', 'rejected': '已驳回'}
SCREEN_TASK_TRANSITIONS = {
    'submit':  {'from': ('draft', 'rejected'), 'to': 'pending'},
    'approve': {'from': ('pending',), 'to': 'running'},
    'reject':  {'from': ('pending',), 'to': 'rejected'},
    'pause':   {'from': ('running',), 'to': 'paused'},
    'resume':  {'from': ('paused',), 'to': 'running'},
    'end':     {'from': ('running', 'paused', 'pending'), 'to': 'ended'},
}
SUBMISSION_STATUSES = {'pending': '待审核', 'accepted': '已采纳', 'rejected': '已驳回'}
SCREEN_LINK_KINDS = {'open': '通用链接(谁扫都能填)', 'bound': '定向链接(绑定一位患者)'}
SCREEN_RATE_WINDOW = 60          # 限流窗口(秒)
SCREEN_RATE_MAX = 10             # 同一 token 每窗口最多提交几次
SCREEN_PUBLIC_BASE = os.environ.get('PLATFORM_PUBLIC_BASE') or ''

QR_UNAVAILABLE = ('服务器没装二维码库, 只给出链接。装法: pip install segno(纯 Python, 无依赖)。'
                  '刻意不手写一个无法验证能否被扫出来的编码器 —— 一张扫不出来的二维码'
                  '印在诊室海报上, 比没有二维码更糟。链接本身已完全可用, 可以先用任意工具生成二维码。')


def ensure_platform_screen_tables():
    """M23: 筛查任务 + 自助链接 + 提交 (idempotent)。"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_screen_task (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                code VARCHAR(64) NOT NULL UNIQUE,
                name VARCHAR(128) NOT NULL,
                cohort_code VARCHAR(64) DEFAULT NULL COMMENT '采纳后按哪个纳排方案评估入组',
                crf_code VARCHAR(64) DEFAULT NULL COMMENT '自助填报用哪份表单/问卷',
                intro VARCHAR(1000) DEFAULT NULL COMMENT '给患者看的说明',
                status ENUM('draft','pending','running','paused','ended','rejected') DEFAULT 'draft',
                owner VARCHAR(64) DEFAULT NULL,
                approver VARCHAR(64) DEFAULT NULL,
                approve_note VARCHAR(500) DEFAULT NULL,
                approved_at DATETIME DEFAULT NULL,
                start_date DATE DEFAULT NULL,
                end_date DATE DEFAULT NULL COMMENT '超过这天还在 running 就算超期',
                target_n INT DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_status (status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M23 筛查任务'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_screen_link (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                token VARCHAR(64) NOT NULL UNIQUE COMMENT 'secrets 生成, 不可预测',
                task_code VARCHAR(64) NOT NULL,
                kind ENUM('open','bound') DEFAULT 'open',
                patient_no VARCHAR(64) DEFAULT NULL COMMENT '定向链接绑定的患者',
                label VARCHAR(128) DEFAULT NULL COMMENT '这条链接投放在哪(门诊海报/短信/公众号)',
                max_uses INT DEFAULT NULL,
                used INT DEFAULT 0,
                expires_at DATETIME DEFAULT NULL,
                active TINYINT(1) DEFAULT 1,
                created_by VARCHAR(64) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_task (task_code), INDEX idx_active (active)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M23 自助填报链接(公开入口)'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_screen_submission (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                task_code VARCHAR(64) NOT NULL,
                token VARCHAR(64) DEFAULT NULL,
                patient_no VARCHAR(64) DEFAULT NULL COMMENT '定向链接带来的; 通用链接由患者自填, 未经核实',
                contact VARCHAR(64) DEFAULT NULL,
                data JSON NOT NULL,
                status ENUM('pending','accepted','rejected') DEFAULT 'pending'
                    COMMENT '陌生人提交的数据不直接进患者库, 必须有人看过才采纳',
                review_note VARCHAR(500) DEFAULT NULL,
                reviewed_by VARCHAR(64) DEFAULT NULL,
                reviewed_at DATETIME DEFAULT NULL,
                source_ip VARCHAR(64) DEFAULT NULL,
                user_agent VARCHAR(300) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_task (task_code, status), INDEX idx_token (token),
                INDEX idx_created (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M23 自助填报提交(待审区)'
        """)
        print('[启动] platform_screen_task / _link / _submission 表已就绪')
        cur.close()
    except Exception as e:
        print('[启动] ensure_platform_screen_tables 失败:', e)
    finally:
        conn.close()


def upsert_screen_task(body):
    """建/改筛查任务。running 之后不许改问卷 —— 改了会让前后收上来的数据对不齐。"""
    code = re.sub(r'[^0-9A-Za-z_\-]', '', str(body.get('code') or ''))[:64]
    name = str(body.get('name') or '').strip()
    if not code or not name:
        return None, 'code 和 name 必填'
    for k in ('start_date', 'end_date'):
        v = str(body.get(k) or '').strip()
        if v and not re.match(r'^\d{4}-\d{2}-\d{2}$', v):
            return None, "{} 必须是 'YYYY-MM-DD'".format(k)

    ensure_platform_screen_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT status, crf_code FROM platform_screen_task WHERE code=%s', (code,))
        old = cur.fetchone()
        if old and old[0] in ('running', 'paused') and body.get('crf_code') \
                and body['crf_code'] != old[1]:
            cur.execute("SELECT COUNT(*) FROM platform_screen_submission WHERE task_code=%s", (code,))
            n = cur.fetchone()[0]
            if n:
                cur.close()
                return None, ('任务已在进行中且收到 {} 份提交, 不能换问卷 —— '
                              '换了会让前后收上来的数据对不齐, 而这批数据是要拿来判断入组的。'
                              '请结束本任务后另建一个'.format(n))
        cur.execute("""
            INSERT INTO platform_screen_task (code, name, cohort_code, crf_code, intro,
                                              owner, start_date, end_date, target_n)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE name=VALUES(name), cohort_code=VALUES(cohort_code),
              crf_code=VALUES(crf_code), intro=VALUES(intro), owner=VALUES(owner),
              start_date=VALUES(start_date), end_date=VALUES(end_date), target_n=VALUES(target_n)
        """, (code, name, body.get('cohort_code') or None, body.get('crf_code') or None,
              (body.get('intro') or '')[:1000] or None, body.get('owner') or None,
              body.get('start_date') or None, body.get('end_date') or None, body.get('target_n')))
        cur.close()
        return {'code': code, 'name': name, 'status': (old[0] if old else 'draft')}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def screen_task_transition(body):
    """任务状态流转 {code, action, operator, note?}。审批要署名。"""
    code = str(body.get('code') or '').strip()
    action = str(body.get('action') or '').strip()
    tr = SCREEN_TASK_TRANSITIONS.get(action)
    if not code or not tr:
        return None, 'code 必填, action 必须是 {}'.format('/'.join(SCREEN_TASK_TRANSITIONS))
    op = str(body.get('operator') or '').strip()
    if action in ('approve', 'reject') and not op:
        return None, '审批必须署名 —— 这个任务批下去就会生成公开填报链接, 得有人负责'
    note = str(body.get('note') or '').strip()
    if action == 'reject' and not note:
        return None, '驳回必须写明原因'

    ensure_platform_screen_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT status FROM platform_screen_task WHERE code=%s', (code,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, '筛查任务不存在: {}'.format(code)
        cur_st = row[0]
        if cur_st not in tr['from']:
            cur.close()
            return None, '当前状态「{}」不能执行 {} (允许的前置状态: {})'.format(
                SCREEN_TASK_STATUSES.get(cur_st, cur_st), action,
                '/'.join(SCREEN_TASK_STATUSES.get(x, x) for x in tr['from']))
        new = tr['to']
        if action in ('approve', 'reject'):
            cur.execute("""UPDATE platform_screen_task SET status=%s, approver=%s,
                           approve_note=%s, approved_at=NOW() WHERE code=%s""",
                        (new, op, note[:500] or None, code))
        else:
            cur.execute('UPDATE platform_screen_task SET status=%s WHERE code=%s', (new, code))
        # 结束/暂停时把链接一并停掉 —— 任务停了链接还能填, 等于任务没停
        if new in ('ended', 'paused', 'rejected'):
            cur.execute('UPDATE platform_screen_link SET active=0 WHERE task_code=%s', (code,))
            n = cur.rowcount
        else:
            n = 0
        cur.close()
        out = {'ok': True, 'code': code, 'from': cur_st, 'to': new,
               'status_label': SCREEN_TASK_STATUSES[new]}
        if n:
            out['links_deactivated'] = n
            out['note'] = ('已同时停用 {} 条填报链接 —— 任务停了链接还能填, '
                           '等于任务没停'.format(n))
        return out, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def create_screen_link(body):
    """生成一条自助填报链接。token 用 secrets, 不可预测。"""
    task = str(body.get('task_code') or '').strip()
    kind = body.get('kind') or 'open'
    if not task:
        return None, 'task_code 必填'
    if kind not in SCREEN_LINK_KINDS:
        return None, 'kind 必须是 open 或 bound'
    if kind == 'bound' and not str(body.get('patient_no') or '').strip():
        return None, '定向链接必须给 patient_no'
    days = body.get('valid_days')
    try:
        days = int(days) if days is not None else 30
    except (TypeError, ValueError):
        return None, 'valid_days 必须是整数'
    if not (1 <= days <= 365):
        return None, 'valid_days 需在 1~365 之间'
    max_uses = body.get('max_uses')
    if max_uses is not None:
        try:
            max_uses = int(max_uses)
        except (TypeError, ValueError):
            return None, 'max_uses 必须是整数'
        if max_uses < 1:
            return None, 'max_uses 至少为 1'
    elif kind == 'bound':
        max_uses = 1        # 定向链接默认一次性 —— 它代表某一位患者

    ensure_platform_screen_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT status FROM platform_screen_task WHERE code=%s', (task,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, '筛查任务不存在: {}'.format(task)
        if row[0] != 'running':
            cur.close()
            return None, ('任务当前是「{}」, 只有进行中的任务才能生成填报链接 —— '
                          '未经审批就把入口发出去, 等于绕过了审批'.format(
                              SCREEN_TASK_STATUSES.get(row[0], row[0])))
        import secrets
        token = secrets.token_urlsafe(32)[:43]
        cur.execute("""INSERT INTO platform_screen_link
                       (token, task_code, kind, patient_no, label, max_uses, expires_at, created_by)
                       VALUES (%s,%s,%s,%s,%s,%s,DATE_ADD(NOW(), INTERVAL %s DAY),%s)""",
                    (token, task, kind, body.get('patient_no') or None,
                     (body.get('label') or '')[:128] or None, max_uses, days,
                     body.get('created_by') or None))
        cur.close()
        url = ((SCREEN_PUBLIC_BASE.rstrip('/') + '/screen.html?t=' + token)
               if SCREEN_PUBLIC_BASE else ('/screen.html?t=' + token))
        out = {'ok': True, 'token': token, 'url': url, 'kind': kind,
               'kind_label': SCREEN_LINK_KINDS[kind], 'valid_days': days,
               'max_uses': max_uses}
        try:
            import segno
            import io as _io
            buf = _io.BytesIO()
            segno.make(url, error='m').save(buf, kind='png', scale=6)
            import base64 as _b64
            out['qr_png'] = 'data:image/png;base64,' + _b64.b64encode(buf.getvalue()).decode()
        except ImportError:
            out['qr_note'] = QR_UNAVAILABLE
        if not SCREEN_PUBLIC_BASE:
            out['url_note'] = ('未配置 PLATFORM_PUBLIC_BASE, 只给出相对路径。'
                               '印到海报上之前请配上对外可访问的域名')
        return out, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def screen_form_public(token):
    """公开接口: 按 token 返回表单结构。

    **只吐表单结构, 绝不吐任何患者数据。** 这是这块最要紧的一条 —— 若按门诊号回显
    "您的既往信息", 任何人猜一个门诊号就能读别人的病历。定向链接也只回一句
    "本次填报将记在您名下", 不回姓名不回既往记录。
    """
    if not token:
        return None, '缺少 token'
    ensure_platform_screen_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""SELECT l.task_code, l.kind, l.max_uses, l.used, l.expires_at, l.active,
                              t.name, t.intro, t.crf_code, t.status
                       FROM platform_screen_link l
                       JOIN platform_screen_task t ON t.code=l.task_code
                       WHERE l.token=%s""", (token,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, '链接无效'
        task, kind, mx, used, exp, active, name, intro, crf, tstatus = row
        if not active or tstatus != 'running':
            cur.close()
            return None, '本次筛查已结束或暂停, 链接不再可用'
        if exp and exp < datetime.datetime.now():
            cur.close()
            return None, '链接已过期'
        if mx is not None and used >= mx:
            cur.close()
            return None, '链接已达使用次数上限'
        definition = None
        if crf:
            cur.execute("SELECT definition FROM platform_crf WHERE code=%s AND active=1 "
                        "ORDER BY id DESC LIMIT 1", (crf,))
            d = cur.fetchone()
            if d:
                definition = json.loads(d[0]) if isinstance(d[0], str) else d[0]
        cur.close()
        return {'ok': True, 'task_name': name, 'intro': intro,
                'crf_code': crf, 'definition': definition,
                'bound': kind == 'bound',
                'bound_note': '本次填报将记在您名下' if kind == 'bound' else None,
                'privacy_note': ('本页只用于提交信息, 不会显示任何既往病历。'
                                 '提交后由医护人员核对, 核对通过才会进入随访')}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def screen_submit_public(body, source_ip=None, user_agent=None):
    """公开接口: 提交自助填报。落到**待审区**, 不直接建档。"""
    token = str(body.get('token') or '').strip()
    data = body.get('data')
    if not token:
        return None, '缺少 token'
    if not isinstance(data, dict) or not data:
        return None, '没有收到填写内容'
    if len(json.dumps(data, ensure_ascii=False)) > 40000:
        return None, '内容过大'

    ensure_platform_screen_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""SELECT l.id, l.task_code, l.kind, l.patient_no, l.max_uses, l.used,
                              l.expires_at, l.active, t.status, t.crf_code
                       FROM platform_screen_link l
                       JOIN platform_screen_task t ON t.code=l.task_code
                       WHERE l.token=%s""", (token,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, '链接无效'
        lid, task, kind, bound_no, mx, used, exp, active, tstatus, crf = row
        if not active or tstatus != 'running':
            cur.close()
            return None, '本次筛查已结束或暂停'
        if exp and exp < datetime.datetime.now():
            cur.close()
            return None, '链接已过期'
        if mx is not None and used >= mx:
            cur.close()
            return None, '链接已达使用次数上限'
        # 限流: 同一 token 短时间狂提交, 多半不是患者在填表
        cur.execute("""SELECT COUNT(*) FROM platform_screen_submission
                       WHERE token=%s AND created_at > DATE_SUB(NOW(), INTERVAL %s SECOND)""",
                    (token, SCREEN_RATE_WINDOW))
        if cur.fetchone()[0] >= SCREEN_RATE_MAX:
            cur.close()
            return None, '提交过于频繁, 请稍后再试'

        # 有表单定义就校验一遍, 免得收上来一堆填不全的
        if crf:
            cur.execute("SELECT definition FROM platform_crf WHERE code=%s AND active=1 "
                        "ORDER BY id DESC LIMIT 1", (crf,))
            d = cur.fetchone()
            if d:
                defn = json.loads(d[0]) if isinstance(d[0], str) else d[0]
                errs, _w = validate_crf_data(defn, data)
                if errs:
                    cur.close()
                    return {'ok': False, 'accepted': False, 'errors': errs}, None

        # patient_no 这一列的含义是"**已确认**的身份", 只有定向链接才填得起 ——
        # 它的患者号是服务端记录的, 不听提交里带的(否则任何人都能拿一条定向链接
        # 往别人名下塞数据)。
        # 通用链接上患者自填的号码没经过任何核实, 只留在 data 里当线索;
        # 写进这一列会让审核时"必须核实门诊号"那道关自动通过 —— 正是它要防的事。
        pno = bound_no if kind == 'bound' else None
        contact = str(data.get('phone') or data.get('contact') or '')[:64] or None
        cur.execute("""INSERT INTO platform_screen_submission
                       (task_code, token, patient_no, contact, data, status, source_ip, user_agent)
                       VALUES (%s,%s,%s,%s,%s,'pending',%s,%s)""",
                    (task, token, pno, contact, json.dumps(data, ensure_ascii=False),
                     (source_ip or '')[:64] or None, (user_agent or '')[:300] or None))
        sid = cur.lastrowid
        cur.execute('UPDATE platform_screen_link SET used=used+1 WHERE id=%s', (lid,))
        cur.close()
        return {'ok': True, 'accepted': True, 'submission_id': sid,
                'message': '已收到，感谢您的填写。医护人员核对后会与您联系。'}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def screen_submission_review(body):
    """审核一份自助提交 {id, action: accept|reject, operator, note?, patient_no?}

    accept 时才建档 —— 陌生人填的东西不直接进患者库。
    """
    try:
        sid = int(body.get('id'))
    except (TypeError, ValueError):
        return None, 'id 必填且为整数'
    action = str(body.get('action') or '').strip()
    if action not in ('accept', 'reject'):
        return None, 'action 必须是 accept 或 reject'
    op = str(body.get('operator') or '').strip()
    if not op:
        return None, '审核必须署名'
    note = str(body.get('note') or '').strip()
    if action == 'reject' and not note:
        return None, '驳回必须写明原因'

    ensure_platform_screen_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT status, task_code, patient_no, data FROM platform_screen_submission WHERE id=%s',
                    (sid,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, '提交记录不存在: {}'.format(sid)
        if row[0] != 'pending':
            cur.close()
            return None, '该提交已{}，不能重复审核'.format(SUBMISSION_STATUSES.get(row[0], row[0]))
        task, pno, data = row[1], row[2], row[3]
        if isinstance(data, str):
            data = json.loads(data)
        new_status = 'accepted' if action == 'accept' else 'rejected'
        created = None
        if action == 'accept':
            pno = str(body.get('patient_no') or pno or '').strip()
            if not pno:
                cur.close()
                claimed = str((data or {}).get('patient_no') or '').strip()
                return None, ('采纳时必须确定门诊号 —— 通用链接上患者自填的号码没经过核实, '
                              '请核对后填入正确的门诊号{}'.format(
                                  '。患者自填的是「{}」, 可作参考'.format(claimed) if claimed else ''))
            cur.execute('SELECT patient_no FROM platform_patient WHERE patient_no=%s', (pno,))
            if not cur.fetchone():
                cur.execute("""INSERT INTO platform_patient (patient_no, name, gender, age, note)
                               VALUES (%s,%s,%s,%s,%s)""",
                            (pno, str(data.get('name') or '')[:64] or None,
                             data.get('gender') if data.get('gender') in ('M', 'F') else None,
                             data.get('age') if isinstance(data.get('age'), int) else None,
                             '自助筛查采纳({})'.format(task)))
                created = True
            else:
                created = False
        cur.execute("""UPDATE platform_screen_submission SET status=%s, patient_no=%s,
                       reviewed_by=%s, review_note=%s, reviewed_at=NOW() WHERE id=%s""",
                    (new_status, pno, op, note[:500] or None, sid))
        cur.close()
        return {'ok': True, 'id': sid, 'status': new_status,
                'status_label': SUBMISSION_STATUSES[new_status],
                'patient_no': pno if action == 'accept' else None,
                'patient_created': created,
                'note': ('已建档 {}。是否入组由纳排规则决定 —— 到「纳排与分组」里试算'.format(pno)
                         if created else None)}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def query_screen_tasks(code=None, status=None, with_links=True, limit=100):
    """筛查任务列表, 含超期与待审批预警 (§4.1(4))。"""
    ensure_platform_screen_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ['1=1'], []
        if code:
            where.append('t.code=%s'); params.append(code)
        if status:
            where.append('t.status=%s'); params.append(status)
        params.append(int(limit))
        cur.execute("""
            SELECT t.code, t.name, t.cohort_code, t.crf_code, t.intro, t.status, t.owner,
                   t.approver, t.approve_note, t.approved_at, t.start_date, t.end_date,
                   t.target_n, t.created_at,
                   (SELECT COUNT(*) FROM platform_screen_submission s
                     WHERE s.task_code=t.code) AS total_sub,
                   (SELECT COUNT(*) FROM platform_screen_submission s
                     WHERE s.task_code=t.code AND s.status='pending') AS pending_sub,
                   (SELECT COUNT(*) FROM platform_screen_submission s
                     WHERE s.task_code=t.code AND s.status='accepted') AS accepted_sub,
                   (SELECT COUNT(*) FROM platform_screen_link l
                     WHERE l.task_code=t.code AND l.active=1) AS active_links,
                   DATEDIFF(CURDATE(), t.end_date) AS overdue_days,
                   DATEDIFF(NOW(), t.updated_at) AS idle_days
            FROM platform_screen_task t WHERE {} ORDER BY
              FIELD(t.status,'pending','running','paused','draft','rejected','ended'),
              t.updated_at DESC LIMIT %s
        """.format(' AND '.join(where)), params)
        cols = ['code', 'name', 'cohort_code', 'crf_code', 'intro', 'status', 'owner',
                'approver', 'approve_note', 'approved_at', 'start_date', 'end_date',
                'target_n', 'created_at', 'total_sub', 'pending_sub', 'accepted_sub',
                'active_links', 'overdue_days', 'idle_days']
        out = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            for k in ('approved_at', 'created_at'):
                if d.get(k) is not None and hasattr(d[k], 'strftime'):
                    d[k] = d[k].strftime('%Y-%m-%d %H:%M:%S')
            for k in ('start_date', 'end_date'):
                if d.get(k) is not None and hasattr(d[k], 'strftime'):
                    d[k] = d[k].strftime('%Y-%m-%d')
            d['status_label'] = SCREEN_TASK_STATUSES.get(d['status'], d['status'])
            d['progress'] = (round(d['accepted_sub'] * 100.0 / d['target_n'], 1)
                             if d.get('target_n') else None)
            # §4.1(4): 对超期、待审批任务预警
            alerts = []
            if d['status'] == 'running' and (d.get('overdue_days') or 0) > 0:
                alerts.append({'kind': 'overdue', 'level': 'warn',
                               'detail': '已超过计划结束日 {} 天, 但链接仍然开着 —— '
                                         '印出去的二维码不会自己失效'.format(d['overdue_days'])})
            if d['status'] == 'pending' and (d.get('idle_days') or 0) >= 3:
                alerts.append({'kind': 'awaiting_approval', 'level': 'warn',
                               'detail': '待审批已 {} 天'.format(d['idle_days'])})
            if d['pending_sub'] >= 20:
                alerts.append({'kind': 'backlog', 'level': 'warn',
                               'detail': '有 {} 份提交等着核对 —— 患者已经填了, 这边压着'
                                         '会让人觉得没人管'.format(d['pending_sub'])})
            d['alerts'] = alerts
            out.append(d)
        if with_links and code and out:
            cur.execute("""SELECT token, kind, patient_no, label, max_uses, used,
                                  expires_at, active, created_by, created_at
                           FROM platform_screen_link WHERE task_code=%s ORDER BY id DESC""", (code,))
            out[0]['links'] = [{
                'token_short': a[:8] + '…', 'kind': b, 'kind_label': SCREEN_LINK_KINDS.get(b, b),
                'patient_no': c, 'label': d2, 'max_uses': e, 'used': f,
                'expires_at': g.strftime('%Y-%m-%d %H:%M') if hasattr(g, 'strftime') else g,
                'active': bool(h), 'created_by': i,
                'created_at': j.strftime('%Y-%m-%d %H:%M') if hasattr(j, 'strftime') else j}
                for a, b, c, d2, e, f, g, h, i, j in cur.fetchall()]
        cur.close()
        return {'ok': True, 'count': len(out), 'tasks': out,
                'statuses': SCREEN_TASK_STATUSES, 'link_kinds': SCREEN_LINK_KINDS,
                'public_base': SCREEN_PUBLIC_BASE or None,
                'qr_available': _has_segno()}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def _has_segno():
    try:
        import segno       # noqa: F401
        return True
    except ImportError:
        return False


def query_screen_submissions(task_code=None, status=None, limit=200):
    """自助提交列表(待审区)。"""
    ensure_platform_screen_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ['1=1'], []
        if task_code:
            where.append('s.task_code=%s'); params.append(task_code)
        if status:
            where.append('s.status=%s'); params.append(status)
        params.append(int(limit))
        cur.execute("""SELECT s.id, s.task_code, t.name, s.patient_no, s.contact, s.data,
                              s.status, s.review_note, s.reviewed_by, s.reviewed_at,
                              s.source_ip, s.created_at
                       FROM platform_screen_submission s
                       LEFT JOIN platform_screen_task t ON t.code=s.task_code
                       WHERE {} ORDER BY s.id DESC LIMIT %s""".format(' AND '.join(where)), params)
        cols = ['id', 'task_code', 'task_name', 'patient_no', 'contact', 'data', 'status',
                'review_note', 'reviewed_by', 'reviewed_at', 'source_ip', 'created_at']
        out = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            for k in ('reviewed_at', 'created_at'):
                if d.get(k) is not None and hasattr(d[k], 'strftime'):
                    d[k] = d[k].strftime('%Y-%m-%d %H:%M:%S')
            if isinstance(d.get('data'), str):
                try:
                    d['data'] = json.loads(d['data'])
                except ValueError:
                    pass
            d['status_label'] = SUBMISSION_STATUSES.get(d['status'], d['status'])
            d['field_count'] = len(d['data']) if isinstance(d.get('data'), dict) else 0
            out.append(d)
        cur.close()
        return {'ok': True, 'count': len(out), 'submissions': out,
                'statuses': SUBMISSION_STATUSES}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


# ============ 随访平台 1.1 M24 (研究数据库状态管理 + 版本回滚, 方案 §3.1(3)) ============
#
# 方案原话: "支持暂存、重置、删除、结束四种状态管理, 重置后可修改 CRF 表、流程与
# 分组信息, **且不影响已收集患者数据**"。最后半句是整块的重心, 也是和 M14/M18/M19
# 一脉相承的地方。
#
# 三条钉死的规矩:
#
# 1) **重置不动任何已收集的数据。** 重置的含义只是"解开配置锁, 允许改 CRF/流程/分组",
#    不是"清空重来"。已填的表、已排的访视、已入组的人一条不动。改完配置回到运行中,
#    旧数据仍钉在它当时的版本上(M14/M19 的版本机制保证了这一点)。
#
# 2) **重置期间不收新数据。** 这条是我加的: 配置正在改的时候收上来的数据, 说不清
#    是按旧配置采的还是按新配置采的 —— 而三个月后没人能凭记忆分辨。所以重置态下
#    入组和填报一律挡住, 改完再放开。
#
# 3) **删除只能是逻辑删除。** 物理删患者数据在临床研究里不可接受(数据要留档备查,
#    受试者也有权要求知道自己的数据在哪)。所以代码里根本不提供物理删除的路径 ——
#    "删除"只是把状态置为 deleted 并从常规列表里隐去, 数据一行不少, 而且可以恢复。
#
# 状态决定**能做什么**, 不是决定**谁能做** —— 后者是鉴权, 还没建。

STUDY_STATUSES = {
    'staged':  '暂存(配置中)',
    'running': '运行中',
    'reset':   '重置态(可改配置, 暂停收数据)',
    'ended':   '已结束(只读)',
    'deleted': '已删除(逻辑删除, 数据保留)',
}
STUDY_TRANSITIONS = {
    'activate': {'from': ('staged', 'reset'), 'to': 'running', 'label': '启用'},
    'reset':    {'from': ('running',), 'to': 'reset', 'label': '重置'},
    'end':      {'from': ('running', 'reset', 'staged'), 'to': 'ended', 'label': '结束'},
    'delete':   {'from': ('staged', 'reset', 'ended'), 'to': 'deleted', 'label': '删除'},
    'restore':  {'from': ('deleted',), 'to': 'staged', 'label': '恢复'},
}
# 各状态允许的业务动作。挡住的不是"权限", 是"这个动作在这个状态下没有意义或会毁数据"。
STUDY_ALLOWED = {
    'staged':  {'edit_config': True,  'enroll': False, 'collect': False},
    'running': {'edit_config': False, 'enroll': True,  'collect': True},
    'reset':   {'edit_config': True,  'enroll': False, 'collect': False},
    'ended':   {'edit_config': False, 'enroll': False, 'collect': False},
    'deleted': {'edit_config': False, 'enroll': False, 'collect': False},
}
STUDY_ACTION_LABELS = {'edit_config': '修改 CRF/流程/分组', 'enroll': '入组患者',
                       'collect': '收集数据(填报/访视)'}


def ensure_platform_study_tables():
    """M24: 研究数据库 + 状态留痕 (idempotent)。"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_study (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                code VARCHAR(64) NOT NULL UNIQUE,
                name VARCHAR(128) NOT NULL,
                sponsor VARCHAR(128) DEFAULT NULL,
                cohort_code VARCHAR(64) DEFAULT NULL COMMENT '绑定的纳排方案',
                flow_codes JSON DEFAULT NULL COMMENT '绑定的随访流程',
                crf_codes JSON DEFAULT NULL COMMENT '绑定的 CRF',
                status ENUM('staged','running','reset','ended','deleted') DEFAULT 'staged',
                owner VARCHAR(64) DEFAULT NULL,
                note VARCHAR(500) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_status (status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
              COMMENT='随访平台 M24 研究数据库(删除只置状态, 数据一行不删)'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_study_log (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                study_code VARCHAR(64) NOT NULL,
                action VARCHAR(24) NOT NULL,
                from_status VARCHAR(16) DEFAULT NULL,
                to_status VARCHAR(16) DEFAULT NULL,
                operator VARCHAR(64) DEFAULT NULL,
                reason VARCHAR(500) DEFAULT NULL,
                snapshot JSON DEFAULT NULL COMMENT '变更当时的数据量快照, 事后能证明"重置没动数据"',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_study (study_code, id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M24 数据库状态留痕(只增不改)'
        """)
        print('[启动] platform_study / platform_study_log 表已就绪')
        cur.close()
    except Exception as e:
        print('[启动] ensure_platform_study_tables 失败:', e)
    finally:
        conn.close()


def _study_data_snapshot(cur, code, cohort, flows):
    """点一遍这个研究底下有多少数据。用来证明"重置前后一条没少"。"""
    snap = {}
    try:
        if cohort:
            cur.execute("SELECT COUNT(*) FROM platform_enrollment WHERE cohort_code=%s "
                        "AND status='enrolled'", (cohort,))
            snap['enrolled'] = int(cur.fetchone()[0] or 0)
            cur.execute("SELECT COUNT(*) FROM platform_scale_response r "
                        "JOIN platform_enrollment e ON e.patient_no=r.patient_no "
                        "WHERE e.cohort_code=%s", (cohort,))
            snap['scale_responses'] = int(cur.fetchone()[0] or 0)
            cur.execute("SELECT COUNT(*) FROM platform_crf_response r "
                        "JOIN platform_enrollment e ON e.patient_no=r.patient_no "
                        "WHERE e.cohort_code=%s", (cohort,))
            snap['crf_responses'] = int(cur.fetchone()[0] or 0)
        if flows:
            ph = ','.join(['%s'] * len(flows))
            cur.execute("SELECT COUNT(*) FROM platform_visit v JOIN platform_flow_instance i "
                        "ON i.id=v.instance_id WHERE i.flow_code IN ({})".format(ph), flows)
            snap['visits'] = int(cur.fetchone()[0] or 0)
    except Exception:
        traceback.print_exc()
    return snap


def upsert_study(body):
    """建/改研究数据库。运行中不许改绑定 —— 换掉绑的 CRF/流程等于换了一个研究。"""
    code = re.sub(r'[^0-9A-Za-z_\-]', '', str(body.get('code') or ''))[:64]
    name = str(body.get('name') or '').strip()
    if not code or not name:
        return None, 'code 和 name 必填'
    for k in ('flow_codes', 'crf_codes'):
        v = body.get(k)
        if v is not None and not isinstance(v, list):
            return None, '{} 必须是数组'.format(k)

    ensure_platform_study_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT status, cohort_code, flow_codes, crf_codes FROM platform_study WHERE code=%s',
                    (code,))
        old = cur.fetchone()
        if old and old[0] not in ('staged', 'reset'):
            binding_changed = any([
                body.get('cohort_code') is not None and body['cohort_code'] != old[1],
                body.get('flow_codes') is not None
                and json.dumps(sorted(body['flow_codes'])) != json.dumps(sorted(
                    json.loads(old[2]) if isinstance(old[2], str) else (old[2] or []))),
                body.get('crf_codes') is not None
                and json.dumps(sorted(body['crf_codes'])) != json.dumps(sorted(
                    json.loads(old[3]) if isinstance(old[3], str) else (old[3] or []))),
            ])
            if binding_changed:
                cur.close()
                return None, ('数据库当前是「{}」, 不能改绑定的纳排方案/流程/CRF —— '
                              '换掉这些等于换了一个研究, 而已收上来的数据是按原配置采的。'
                              '要改请先执行"重置"(重置不会动任何已有数据)'.format(
                                  STUDY_STATUSES.get(old[0], old[0])))
        cur.execute("""
            INSERT INTO platform_study (code, name, sponsor, cohort_code, flow_codes, crf_codes,
                                        owner, note)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE name=VALUES(name), sponsor=VALUES(sponsor),
              cohort_code=VALUES(cohort_code), flow_codes=VALUES(flow_codes),
              crf_codes=VALUES(crf_codes), owner=VALUES(owner), note=VALUES(note)
        """, (code, name, body.get('sponsor') or None, body.get('cohort_code') or None,
              json.dumps(body.get('flow_codes') or [], ensure_ascii=False),
              json.dumps(body.get('crf_codes') or [], ensure_ascii=False),
              body.get('owner') or None, (body.get('note') or '')[:500] or None))
        if not old:
            cur.execute("""INSERT INTO platform_study_log (study_code, action, to_status, operator)
                           VALUES (%s,'create','staged',%s)""", (code, body.get('owner') or None))
        cur.close()
        return {'code': code, 'name': name, 'status': (old[0] if old else 'staged'),
                'status_label': STUDY_STATUSES.get(old[0] if old else 'staged')}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def study_transition(body):
    """状态流转 {code, action, operator, reason?}。

    每一步都存一份数据量快照 —— 重置/删除之后有人问"是不是把数据弄没了",
    快照能直接对上。
    """
    code = str(body.get('code') or '').strip()
    action = str(body.get('action') or '').strip()
    tr = STUDY_TRANSITIONS.get(action)
    if not code or not tr:
        return None, 'code 必填, action 必须是 {}'.format('/'.join(STUDY_TRANSITIONS))
    op = str(body.get('operator') or '').strip()
    reason = str(body.get('reason') or '').strip()
    if action in ('reset', 'delete', 'end') and not op:
        return None, '{}必须署名'.format(tr['label'])
    if action in ('reset', 'delete') and not reason:
        return None, ('{}必须写明原因 —— 这一步会改变整个研究的可操作状态, '
                      '三个月后要能说清是谁为什么做的'.format(tr['label']))

    ensure_platform_study_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT status, cohort_code, flow_codes FROM platform_study WHERE code=%s', (code,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, '研究数据库不存在: {}'.format(code)
        cur_st, cohort, flows = row
        if isinstance(flows, str):
            flows = json.loads(flows) if flows else []
        if cur_st not in tr['from']:
            cur.close()
            return None, '当前状态「{}」不能执行{} (允许的前置状态: {})'.format(
                STUDY_STATUSES.get(cur_st, cur_st), tr['label'],
                '、'.join(STUDY_STATUSES.get(x, x) for x in tr['from']))

        before = _study_data_snapshot(cur, code, cohort, flows or [])
        cur.execute('UPDATE platform_study SET status=%s WHERE code=%s', (tr['to'], code))
        after = _study_data_snapshot(cur, code, cohort, flows or [])
        cur.execute("""INSERT INTO platform_study_log
                       (study_code, action, from_status, to_status, operator, reason, snapshot)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (code, action, cur_st, tr['to'], op or None, reason[:500] or None,
                     json.dumps({'before': before, 'after': after}, ensure_ascii=False)))
        cur.close()

        out = {'ok': True, 'code': code, 'from': cur_st, 'to': tr['to'],
               'status_label': STUDY_STATUSES[tr['to']],
               'allowed': {k: v for k, v in STUDY_ALLOWED[tr['to']].items()},
               'data_snapshot': {'before': before, 'after': after},
               'data_unchanged': before == after}
        if action == 'reset':
            out['note'] = ('已进入重置态: 可以改 CRF/流程/分组了, **已收集的数据一条没动**'
                           '(快照: 入组 {} 人 / 量表 {} 份 / CRF {} 份 / 访视 {} 次)。'
                           '重置期间**暂停收新数据** —— 配置正在改的时候收上来的东西, '
                           '说不清是按旧配置还是新配置采的。改完执行"启用"回到运行中'.format(
                               before.get('enrolled', 0), before.get('scale_responses', 0),
                               before.get('crf_responses', 0), before.get('visits', 0)))
        elif action == 'delete':
            out['note'] = ('已标记删除。**这是逻辑删除, 数据一行没删** —— 物理删患者数据在'
                           '临床研究里不可接受(数据要留档备查, 受试者也有权知道自己的数据在哪), '
                           '所以代码里根本没有物理删除的路径。需要时可以"恢复"')
        elif action == 'activate':
            out['note'] = '已启用: 可以入组和收数据了。此后改 CRF/流程的绑定需要先重置'
        return out, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def study_check_action(code, action):
    """问一句"这个研究现在能不能做某件事"。返回 (allowed, reason)。

    给别处调用(入组/填报前先问一声), 也给前端拿来把按钮灰掉。
    """
    if action not in STUDY_ACTION_LABELS:
        return False, 'action 必须是 {}'.format('/'.join(STUDY_ACTION_LABELS))
    ensure_platform_study_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT status FROM platform_study WHERE code=%s', (code,))
        row = cur.fetchone()
        cur.close()
        if not row:
            return False, '研究数据库不存在: {}'.format(code)
        st = row[0]
        ok = STUDY_ALLOWED.get(st, {}).get(action, False)
        if ok:
            return True, None
        hint = ''
        if st == 'reset' and action in ('enroll', 'collect'):
            hint = ' —— 重置期间收上来的数据说不清是按哪版配置采的, 改完配置执行"启用"即可恢复'
        elif st == 'running' and action == 'edit_config':
            hint = ' —— 要改配置请先执行"重置"(不会动任何已有数据)'
        elif st == 'ended':
            hint = ' —— 已结束的研究只读'
        return False, '研究「{}」当前是「{}」, 不能{}{}'.format(
            code, STUDY_STATUSES.get(st, st), STUDY_ACTION_LABELS[action], hint)
    except Exception as e:
        traceback.print_exc()
        return False, str(e)
    finally:
        conn.close()


def query_studies(code=None, status=None, include_deleted=False, with_log=False, limit=100):
    """研究数据库列表。默认不含已删除的 —— 它们还在库里, 只是不该出现在日常视野。"""
    ensure_platform_study_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        where, params = ['1=1'], []
        if code:
            where.append('s.code=%s'); params.append(code)
        if status:
            where.append('s.status=%s'); params.append(status)
        elif not include_deleted:
            where.append("s.status <> 'deleted'")
        params.append(int(limit))
        cur.execute("""SELECT s.code, s.name, s.sponsor, s.cohort_code, s.flow_codes, s.crf_codes,
                              s.status, s.owner, s.note, s.created_at, s.updated_at
                       FROM platform_study s WHERE {} ORDER BY
                       FIELD(s.status,'running','reset','staged','ended','deleted'),
                       s.updated_at DESC LIMIT %s""".format(' AND '.join(where)), params)
        cols = ['code', 'name', 'sponsor', 'cohort_code', 'flow_codes', 'crf_codes',
                'status', 'owner', 'note', 'created_at', 'updated_at']
        out = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            for k in ('created_at', 'updated_at'):
                if d.get(k) is not None and hasattr(d[k], 'strftime'):
                    d[k] = d[k].strftime('%Y-%m-%d %H:%M:%S')
            for k in ('flow_codes', 'crf_codes'):
                if isinstance(d.get(k), str):
                    try:
                        d[k] = json.loads(d[k])
                    except ValueError:
                        d[k] = []
            d['status_label'] = STUDY_STATUSES.get(d['status'], d['status'])
            d['allowed'] = STUDY_ALLOWED.get(d['status'], {})
            d['data'] = _study_data_snapshot(cur, d['code'], d['cohort_code'], d['flow_codes'] or [])
            out.append(d)
        if with_log and code and out:
            cur.execute("""SELECT action, from_status, to_status, operator, reason, snapshot, created_at
                           FROM platform_study_log WHERE study_code=%s ORDER BY id DESC LIMIT 100""",
                        (code,))
            logs = []
            for a, f, t, o, rs, sn, c in cur.fetchall():
                if isinstance(sn, str):
                    try:
                        sn = json.loads(sn)
                    except ValueError:
                        sn = None
                logs.append({'action': a, 'action_label': (STUDY_TRANSITIONS.get(a) or {}).get('label', a),
                             'from': f, 'to': t, 'operator': o, 'reason': rs, 'snapshot': sn,
                             'at': c.strftime('%Y-%m-%d %H:%M:%S') if hasattr(c, 'strftime') else c})
            out[0]['log'] = logs
        cur.close()
        return {'ok': True, 'count': len(out), 'studies': out,
                'statuses': STUDY_STATUSES, 'actions': STUDY_ACTION_LABELS,
                'transitions': {k: {'from': list(v['from']), 'to': v['to'], 'label': v['label']}
                                for k, v in STUDY_TRANSITIONS.items()}}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


# ---- 版本回滚 (CRF / 流程通用) ----
#
# **回滚 = 把旧版内容再发一版, 不是删掉新版。**
# 理由和 M13 质疑单的 reopen 一样: 已经按新版填报的数据钉在新版上, 删了新版
# 那些数据就读不懂了 —— 而且"曾经发过这一版"这件事本身也该留在记录里。
# 所以回滚产出的是 vN+1, 内容等于 vX, 中间那几版原样留着。
def rollback_version(body):
    """回滚 CRF 或流程到某个旧版本 {kind: crf|flow, code, to_version, operator, reason}"""
    kind = body.get('kind')
    if kind not in ('crf', 'flow'):
        return None, 'kind 必须是 crf 或 flow'
    code = str(body.get('code') or '').strip()
    to_ver = str(body.get('to_version') or '').strip()
    op = str(body.get('operator') or '').strip()
    reason = str(body.get('reason') or '').strip()
    if not code or not to_ver:
        return None, 'code 和 to_version 必填'
    if not op:
        return None, '回滚必须署名'
    if not reason:
        return None, '回滚必须写明原因 —— 这会让线上换成另一版内容, 得说清为什么'

    table = 'platform_crf' if kind == 'crf' else 'platform_flow'
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT version, definition, name FROM {} WHERE code=%s AND version=%s'.format(table),
                    (code, to_ver))
        src = cur.fetchone()
        if not src:
            cur.execute('SELECT version FROM {} WHERE code=%s ORDER BY id'.format(table), (code,))
            have = [r[0] for r in cur.fetchall()]
            cur.close()
            return None, '{} {} 没有版本 {} (现有: {})'.format(
                kind.upper(), code, to_ver, '、'.join(have) or '无')
        cur.execute('SELECT version FROM {} WHERE code=%s ORDER BY id DESC LIMIT 1'.format(table), (code,))
        latest = cur.fetchone()[0]
        if latest == to_ver:
            cur.close()
            return None, '当前最新版就是 {}, 无需回滚'.format(to_ver)
        defn = src[1]
        if isinstance(defn, str):
            defn = json.loads(defn)
        name = src[2]
        new_ver = _bump_version(latest)
        cur.close()
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()

    payload = {'code': code, 'name': name, 'version': new_ver, 'definition': defn,
               'owner': op, 'status': 'active',
               'note': '回滚自 v{}: {}'.format(to_ver, reason)}
    if kind == 'crf':
        res, err = upsert_platform_crf(payload)
    else:
        res, err = upsert_flow(payload)
    if err:
        return None, '回滚失败: {}'.format(err)

    # 留痕借用 study_log 表(它就是干这个的), study_code 记成 kind:code
    try:
        ensure_platform_study_tables()
        conn = get_connection(); cur = conn.cursor()
        cur.execute("""INSERT INTO platform_study_log
                       (study_code, action, from_status, to_status, operator, reason)
                       VALUES (%s,'rollback',%s,%s,%s,%s)""",
                    ('{}:{}'.format(kind, code), 'v' + latest, 'v' + new_ver, op,
                     '回滚到 v{} —— {}'.format(to_ver, reason)[:500]))
        cur.close(); conn.close()
    except Exception:
        traceback.print_exc()

    return {'ok': True, 'kind': kind, 'code': code, 'rolled_back_to': to_ver,
            'new_version': new_ver, 'previous_latest': latest,
            'note': ('回滚产出的是**新版本 v{}**(内容等于 v{}), 中间那几版原样留着 —— '
                     '已经按 v{} 填报的数据还钉在 v{} 上, 删掉它们那些数据就读不懂了。'
                     '"曾经发过这一版"本身也该留在记录里'.format(
                         new_ver, to_ver, latest, latest))}, None


def query_version_history(kind, code):
    """看某个 CRF/流程的版本历史与回滚记录。"""
    if kind not in ('crf', 'flow'):
        return None, 'kind 必须是 crf 或 flow'
    if not code:
        return None, 'code 必填'
    table = 'platform_crf' if kind == 'crf' else 'platform_flow'
    resp_table = 'platform_crf_response' if kind == 'crf' else None
    conn = get_connection()
    try:
        cur = conn.cursor()
        if kind == 'crf':
            cur.execute("""SELECT c.version, c.name, c.status, c.created_at, c.updated_at,
                                  (SELECT COUNT(*) FROM platform_crf_response r
                                    WHERE r.crf_code=c.code AND r.crf_version=c.version) AS used
                           FROM platform_crf c WHERE c.code=%s ORDER BY c.id""", (code,))
        else:
            cur.execute("""SELECT f.version, f.name, f.status, f.created_at, f.updated_at,
                                  (SELECT COUNT(*) FROM platform_flow_instance i
                                    WHERE i.flow_code=f.code AND i.flow_version=f.version) AS used
                           FROM platform_flow f WHERE f.code=%s ORDER BY f.id""", (code,))
        versions = [{'version': a, 'name': b, 'status': c,
                     'created_at': d.strftime('%Y-%m-%d %H:%M') if hasattr(d, 'strftime') else d,
                     'updated_at': e.strftime('%Y-%m-%d %H:%M') if hasattr(e, 'strftime') else e,
                     'in_use': int(f or 0)} for a, b, c, d, e, f in cur.fetchall()]
        ensure_platform_study_tables()
        cur.execute("""SELECT from_status, to_status, operator, reason, created_at
                       FROM platform_study_log WHERE study_code=%s AND action='rollback'
                       ORDER BY id DESC""", ('{}:{}'.format(kind, code),))
        rollbacks = [{'from': a, 'to': b, 'operator': c, 'reason': d,
                      'at': e.strftime('%Y-%m-%d %H:%M:%S') if hasattr(e, 'strftime') else e}
                     for a, b, c, d, e in cur.fetchall()]
        cur.close()
        if not versions:
            return None, '{} 不存在: {}'.format(kind.upper(), code)
        return {'ok': True, 'kind': kind, 'code': code, 'versions': versions,
                'latest': versions[-1]['version'], 'rollbacks': rollbacks,
                'note': ('带"使用中"计数的版本不能删 —— 那些数据钉在它上面。'
                         '回滚也不删任何版本, 而是把旧内容再发一版')}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


# ============ 随访平台 1.1 M5 (随访计划引擎) ============
def upsert_platform_plan(body):
    """建/改随访计划 (design: 只有 1 张新表 platform_plan, "任务"从不落地存储).

    {id?, patient_no, name, frequency_days|null, next_due 'YYYY-MM-DD', active?, note?}
    id 传了 = 部分字段更新(未传的字段沿用现有值, 供 {id, active:0} 这种纯停用调用);
    id 不传 = 新建, 此时 patient_no/name/next_due 必填。
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        existing = None
        plan_id = body.get('id')
        if plan_id is not None:
            try:
                plan_id = int(plan_id)
            except (TypeError, ValueError):
                cur.close()
                return None, 'id 必须是整数'
            cur.execute(
                'SELECT id, patient_no, name, frequency_days, next_due, active, note '
                'FROM platform_plan WHERE id = %s', (plan_id,)
            )
            row = cur.fetchone()
            if not row:
                cur.close()
                return None, '随访计划不存在: {}'.format(plan_id)
            existing = dict(zip(
                ['id', 'patient_no', 'name', 'frequency_days', 'next_due', 'active', 'note'], row))

        patient_no = str(body.get('patient_no') or (existing['patient_no'] if existing else '')).strip()
        if not patient_no:
            cur.close()
            return None, 'patient_no 必填'

        name = body.get('name')
        if name is None:
            name = existing['name'] if existing else None
        name = (name or '').strip() if isinstance(name, str) else name
        if not name:
            cur.close()
            return None, 'name 必填'

        if 'frequency_days' in body:
            freq_raw = body.get('frequency_days')
            if freq_raw in (None, ''):
                frequency_days = None
            else:
                try:
                    frequency_days = int(freq_raw)
                except (TypeError, ValueError):
                    cur.close()
                    return None, 'frequency_days 必须是正整数或 null'
                if frequency_days <= 0:
                    cur.close()
                    return None, 'frequency_days 必须 > 0 或为 null(一次性计划)'
        else:
            frequency_days = existing['frequency_days'] if existing else None

        if 'next_due' in body and body.get('next_due'):
            try:
                next_due = datetime.datetime.strptime(str(body['next_due']), '%Y-%m-%d').date()
            except ValueError:
                cur.close()
                return None, "next_due 格式必须是 'YYYY-MM-DD'"
        elif existing:
            next_due = existing['next_due']
        else:
            cur.close()
            return None, 'next_due 必填'

        if 'active' in body:
            active = 1 if body.get('active') else 0
        else:
            active = existing['active'] if existing else 1

        note = body.get('note') if 'note' in body else (existing['note'] if existing else None)

        cur.execute('SELECT patient_no FROM platform_patient WHERE patient_no = %s', (patient_no,))
        if not cur.fetchone():
            cur.close()
            return None, '患者不存在, 请先建档: {}'.format(patient_no)

        if existing:
            cur.execute("""
                UPDATE platform_plan SET
                  patient_no = %s, name = %s, frequency_days = %s, next_due = %s,
                  active = %s, note = %s
                WHERE id = %s
            """, (patient_no, name, frequency_days, next_due, active, note, existing['id']))
            action = 'update'
            plan_id = existing['id']
        else:
            cur.execute("""
                INSERT INTO platform_plan (patient_no, name, frequency_days, next_due, active, note)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (patient_no, name, frequency_days, next_due, active, note))
            action = 'insert'
            plan_id = cur.lastrowid
        cur.close()
        return {'id': plan_id, 'action': action}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def query_platform_plans(patient_no=None, active=None):
    """计划列表 (关联 platform_patient 姓名). active: None=不过滤, 0/1=精确过滤。"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        where = ['1=1']
        params = []
        if patient_no:
            where.append('pl.patient_no = %s')
            params.append(patient_no)
        if active is not None:
            where.append('pl.active = %s')
            params.append(active)
        cur.execute("""
            SELECT pl.id, pl.patient_no, p.name, pl.name, pl.frequency_days, pl.next_due,
                   pl.active, pl.note, pl.created_at, pl.updated_at
            FROM platform_plan pl
            LEFT JOIN platform_patient p ON p.patient_no = pl.patient_no
            WHERE {}
            ORDER BY pl.next_due ASC, pl.id ASC
        """.format(' AND '.join(where)), params)
        cols = ['id', 'patient_no', 'patient_name', 'name', 'frequency_days', 'next_due',
                'active', 'note', 'created_at', 'updated_at']
        plans = []
        for r in cur.fetchall():
            row = dict(zip(cols, r))
            if row.get('next_due') is not None and hasattr(row['next_due'], 'strftime'):
                row['next_due'] = row['next_due'].strftime('%Y-%m-%d')
            for k in ('created_at', 'updated_at'):
                if row.get(k) is not None and hasattr(row[k], 'strftime'):
                    row[k] = row[k].strftime('%Y-%m-%d %H:%M:%S')
            row['active'] = bool(row['active'])
            plans.append(row)
        cur.close()
        return {'ok': True, 'count': len(plans), 'plans': plans}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def query_platform_tasks(horizon_days=7):
    """今日待办任务 (从 platform_plan 现算, 不落地存储): active=1 且
    next_due <= today+horizon_days 都算一条任务; overdue_days = max(0, today - next_due)。
    按 next_due 升序天然就是"逾期在前、今日到期次之、未来最后", 不需要额外排序键。
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT CURDATE()')
        today = cur.fetchone()[0]
        cur.execute("""
            SELECT pl.id, pl.patient_no, p.name, pl.name, pl.next_due, pl.frequency_days, pl.note,
                   fl.last_done
            FROM platform_plan pl
            LEFT JOIN platform_patient p ON p.patient_no = pl.patient_no
            LEFT JOIN (
                SELECT plan_id, MAX(created_at) AS last_done
                FROM platform_followup_log
                WHERE plan_id IS NOT NULL
                GROUP BY plan_id
            ) fl ON fl.plan_id = pl.id
            WHERE pl.active = 1 AND pl.next_due <= %s
            ORDER BY pl.next_due ASC, pl.id ASC
        """, (today + datetime.timedelta(days=horizon_days),))
        tasks = []
        for (plan_id, patient_no, patient_name, plan_name, next_due,
             frequency_days, note, last_done) in cur.fetchall():
            overdue_days = max(0, (today - next_due).days)
            tasks.append({
                'plan_id': plan_id, 'patient_no': patient_no, 'patient_name': patient_name,
                'plan_name': plan_name, 'next_due': next_due.strftime('%Y-%m-%d'),
                'overdue_days': overdue_days, 'frequency_days': frequency_days, 'note': note,
                'last_done': last_done.strftime('%Y-%m-%d %H:%M:%S') if last_done else None,
            })
        cur.close()
        return {'ok': True, 'today': today.strftime('%Y-%m-%d'), 'count': len(tasks), 'tasks': tasks}, None
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()


def platform_task_complete(body):
    """完成随访任务: {plan_id, method:'call'|'visit'|'note', result_text, operator}.
    单事务: 写 1 行 platform_followup_log(action=method, plan_id 关联) + 推进计划——
    循环计划(frequency_days 非空) next_due = 完成当日(非旧到期日) + frequency_days;
    一次性计划(frequency_days 为空) active 置 0。任一步失败整体回滚。
    """
    try:
        plan_id = int(body.get('plan_id'))
    except (TypeError, ValueError):
        return None, 'plan_id 必须是整数'
    method = body.get('method')
    if method not in ('call', 'visit', 'note'):
        return None, "method 必须是 'call'/'visit'/'note' 之一"
    result_text = body.get('result_text') or None
    operator = body.get('operator') or None

    conn = get_connection()
    try:
        conn.autocommit(False)
        cur = conn.cursor()
        cur.execute(
            'SELECT patient_no, frequency_days, active FROM platform_plan WHERE id = %s FOR UPDATE',
            (plan_id,)
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            cur.close()
            return None, '随访计划不存在: {}'.format(plan_id)
        patient_no, frequency_days, active = row
        if not active:
            conn.rollback()
            cur.close()
            return None, '随访计划已停用, 无需再完成: {}'.format(plan_id)

        cur.execute("""
            INSERT INTO platform_followup_log (patient_no, plan_id, action, result_text, operator)
            VALUES (%s, %s, %s, %s, %s)
        """, (patient_no, plan_id, method, result_text, operator))

        if frequency_days:
            cur.execute('SELECT CURDATE()')
            today = cur.fetchone()[0]
            next_due = today + datetime.timedelta(days=int(frequency_days))
            cur.execute('UPDATE platform_plan SET next_due = %s WHERE id = %s', (next_due, plan_id))
            result = {'plan_id': plan_id, 'next_due': next_due.strftime('%Y-%m-%d'), 'active': True}
        else:
            cur.execute('UPDATE platform_plan SET active = 0 WHERE id = %s', (plan_id,))
            result = {'plan_id': plan_id, 'next_due': None, 'active': False}

        conn.commit()
        cur.close()
        return result, None
    except Exception as e:
        conn.rollback()
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.autocommit(True)
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


def _iwown_compliance_daily(device_id, days=14):
    """随访平台 M4 佩戴依从性 (design doc §3.4): 单台 iwown 设备的每日佩戴率。

    佩戴率指标定义(供 GET /api/platform/compliance 与 /api/platform/patients.wear_rate_7d 共用):
      wear_hours      = 当天(按 recorded_at 的日历日) COUNT(DISTINCT HOUR(recorded_at)),
                         统计 data_type='health' 的帧覆盖了 0-23 点中的几个不同小时
                         (同一小时内多帧只算 1 次, 不要求逐分钟连续, 是"覆盖时长"的近似值)。
      wear_rate       = wear_hours / 24, 四舍五入保留 2 位小数 (1.0 = 全天 24 个小时段都有数据)。
      not_worn_alarms = 同一日历日该设备 platform_alarm.alarm_type='not_worn' 的条数, 仅作标注
                         (annotation), 不参与 wear_rate 计算, 用来在页面上跟"佩戴率骤降"的天数
                         交叉核对。
    每台设备用 1 条 GROUP BY DATE(recorded_at) 查询取整个窗口(不逐天循环查询), 另用 1 条小聚合
    查询取 not_worn 报警按天计数, 两边按日期字符串在 Python 侧合并。
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT DATE(recorded_at) AS d, COUNT(DISTINCT HOUR(recorded_at)) AS wear_hours
            FROM iwown_data
            WHERE device_id = %s AND data_type = 'health' AND recorded_at IS NOT NULL
              AND recorded_at >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            GROUP BY DATE(recorded_at)
            ORDER BY d
        """, (device_id, days))
        wear_map = {}
        for d, wear_hours in cur.fetchall():
            date_str = d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)
            wear_map[date_str] = int(wear_hours)

        cur.execute("""
            SELECT DATE(occurred_at) AS d, COUNT(*) AS n
            FROM platform_alarm
            WHERE device_id = %s AND alarm_type = 'not_worn' AND occurred_at IS NOT NULL
              AND occurred_at >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            GROUP BY DATE(occurred_at)
        """, (device_id, days))
        alarm_map = {}
        for d, n in cur.fetchall():
            date_str = d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)
            alarm_map[date_str] = int(n)
        cur.close()

        daily = []
        for date_str in sorted(set(wear_map) | set(alarm_map)):
            wear_hours = wear_map.get(date_str, 0)
            daily.append({
                'date': date_str,
                'wear_hours': wear_hours,
                'wear_rate': round(wear_hours / 24.0, 2),
                'not_worn_alarms': alarm_map.get(date_str, 0),
            })
        return daily
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 随访平台 M25: OCR 辅助采集 —— 病历/检验单拍照填 CRF
#
# 在这之前平台里"OCR"是缺的。M11 用 pdf-inspector 读 PDF, 而 pdf-inspector 按它自己
# 的说明是**给 OCR 做分流的**(它算出 pages_needing_ocr, 你再拿去调真 OCR), 本身不认图。
# 所以扫描件和拍照件一直读不出来。
#
# 引擎选 RapidOCR(onnxruntime): 模型打包在 wheel 里, 纯 CPU, 不联网、不要 key。
# 这是刻意的 —— 输入是病历和检验单的照片, 上面有姓名、身份证号、门诊号,
# 送云 OCR 等于把一整份 PHI 交给第三方。本模块没有任何出网代码路径。
#
# ---- 为什么识别结果一个字都不自动入库 ----
#
# 拿一张 150dpi 的检验单实测(13 行结果, 印刷体, 不倾斜 —— 比真实拍照件干净得多):
#
#   * 5 个 ↑ 异常标记只检出 1 个, 而且那一个是全页置信度最低的(0.519)。
#     漏掉的 4 个**没有任何提示**: 结果看起来是完整的, 只是不再异常了。
#   * "4.15" 读成 "4. 15"、"5.42" 读成 "5. 42"(中间多个空格), 置信度 0.91,
#     按数字解析会变成 4 或者 415。
#   * "床号：—" 读成 "床号：一" —— 破折号成了汉字一, "没有床号"变成"1 床"。
#   * "2026-07-31 08:15" 粘成 "2026-07-3108:15"。
#   * 同一张图旋转 2.5° 再识别, 上面那两个空格错误消失了。错误连"稳定"都算不上,
#     没法靠事后规则补。
#
# 共同点是**看起来完全正常**。一个错的血锂浓度不会报错, 它就是个数字。
# 所以本模块的定位是: OCR 负责定位和预读, 人负责转录。
#
# 落到代码上是四道闸:
#
# 1) ocr_recognize 只往 platform_ocr_* 写, 一个字都不进 platform_crf_response。
# 2) ocr_verify_field 一次只核一个字段, 且必须带 operator。数字/日期/表格题
#    **不提供"采纳 OCR 值"这个动作**, 只能人工键入 —— 上面那个 "4. 15" 就是理由。
#    置信度低于 OCR_ACCEPT_MIN_CONF 的文本题同样只能键入。
# 3) ocr_commit 要求该 job 下每一个待核字段都已处置, 写进 CRF 的只能是
#    final_value(人给的); ocr_value 永远不会成为答案。
# 4) 待核字段按 **CRF 定义**全量生成, 不是按"OCR 命中了什么"生成。
#    只给命中项建待核记录的话, 被静默漏掉的字段压根不会出现在核对清单上,
#    人认真核完一遍还是漏, 且漏得毫无痕迹。宁可让人对着空值点"未找到"。
#
# 顺带补上 M11 那边的一个坑: 混合件(前几页电子版 + 后面附一张化验单照片)以前
# 只返回文字层那部分, 读不到的页没有任何提示 —— 看起来像是整份都读完了。
# 现在 ocr_read_source 会如实报出哪几页走了文字层、哪几页走了 OCR、哪几页没读成。
# ---------------------------------------------------------------------------

OCR_DIR = os.environ.get('PLATFORM_OCR_DIR') or os.path.join(DOC_DIR, 'ocr')
OCR_ALLOWED_EXT = ('jpg', 'jpeg', 'png', 'bmp', 'tif', 'tiff', 'webp', 'pdf')
OCR_MAX_BYTES = 20 * 1024 * 1024
OCR_MAX_PAGES = int(os.environ.get('PLATFORM_OCR_MAX_PAGES') or 20)
# PDF 页渲成图再识别的放大倍数。PDF 默认 72dpi, ×3 约等于 216dpi ——
# 低于 200dpi 时小字号的数字识别率掉得很快, 而这里错一个数字就是错一个化验值。
OCR_PDF_RENDER_SCALE = float(os.environ.get('PLATFORM_OCR_RENDER_SCALE') or 3.0)
# 文本题允许"看一眼原图就采纳"的置信度下限。低于它必须人工键入。
OCR_ACCEPT_MIN_CONF = 0.90

OCR_JOB_STATUSES = ('recognized', 'verifying', 'committed', 'failed', 'abandoned')
OCR_VERIFY_STATES = {
    'unverified': '待核对',
    'match':      '已核对(与识别一致)',
    'corrected':  '已核对(人工改正)',
    'not_found':  '原件上没有/看不清',
    'na':         '本次不适用',
}
OCR_TERMINAL_STATES = ('match', 'corrected', 'not_found', 'na')
# 这些题型永远不给"采纳"按钮, 只能键入。数字和日期是错了看不出来的重灾区,
# 表格题一次涉及几十个格子, 一键采纳等于整表未经核对入库。
OCR_TYPED_ONLY = ('number', 'date') + tuple(CRF_TABLE_TYPES)

_OCR_ENGINE = [None]


def _ocr_engine():
    """惰性拿 OCR 引擎。返回 (engine, error)。

    引擎实例化要加载几个 onnx 模型(约 0.2s), 所以进程内只建一次。
    未安装时返回明确错误, **不返回一个"识别出 0 行"的空结果** ——
    静默降级在这里的后果是: 页面显示"未识别到内容", 使用者以为是照片拍糊了。
    """
    if _OCR_ENGINE[0] is not None:
        return _OCR_ENGINE[0], None
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return None, ('未安装 OCR 引擎(rapidocr-onnxruntime)。在服务器上执行: '
                      '/root/miniconda3/bin/pip install rapidocr-onnxruntime')
    try:
        _OCR_ENGINE[0] = RapidOCR()
    except Exception as e:
        traceback.print_exc()
        return None, 'OCR 引擎初始化失败: {}'.format(e)
    return _OCR_ENGINE[0], None


def ocr_engine_status():
    """引擎与依赖的可用性。前端据此决定是否显示"拍照识别"入口。"""
    info = {'engine': 'rapidocr-onnxruntime', 'runs_locally': True,
            'sends_data_out': False, 'ready': False,
            'accept_min_conf': OCR_ACCEPT_MIN_CONF,
            'typed_only_types': list(OCR_TYPED_ONLY),
            'max_mb': OCR_MAX_BYTES // 1024 // 1024, 'max_pages': OCR_MAX_PAGES}
    try:
        import rapidocr_onnxruntime          # noqa: F401
        info['ready'] = True
    except ImportError:
        info['error'] = ('未安装 OCR 引擎。装法: '
                         '/root/miniconda3/bin/pip install rapidocr-onnxruntime')
    for mod, key, why in (('onnxruntime', 'onnxruntime_version', None),
                          ('PIL', 'pillow', None)):
        try:
            m = __import__(mod)
            info[key] = getattr(m, '__version__', 'ok')
        except ImportError:
            info[key] = None
    try:
        import pypdfium2                     # noqa: F401
        info['pdf_render'] = True
    except ImportError:
        info['pdf_render'] = False
        info['pdf_note'] = ('未安装 pypdfium2, 扫描版 PDF 无法渲成图送识别(图片文件不受影响)。'
                            '装法: /root/miniconda3/bin/pip install pypdfium2')
    try:
        import pdf_inspector                 # noqa: F401
        info['pdf_route'] = True
    except ImportError:
        info['pdf_route'] = False
        info['pdf_route_note'] = ('未安装 pdf-inspector, PDF 的每一页都会走 OCR —— '
                                  '有文字层的页本可以直接取字, 又快又不会有识别错误')
    return info


def _ocr_safe_ext(filename):
    """只取扩展名, 原始文件名一个字都不落到磁盘上(同 M17 的理由)。"""
    name = str(filename or '')
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    ext = re.sub(r'[^a-z0-9]', '', ext)[:8]
    return ext if ext in OCR_ALLOWED_EXT else None


def _ocr_lines(image_bytes, page=1, source='ocr'):
    """一张图 -> ([{text, conf, box, page, source}], error)。

    box 是 [x0,y0,x1,y1] 外接矩形(整数, 图片像素坐标), 前端靠它裁出原图片段
    摆在待核字段旁边 —— 不看原件的"核对"不叫核对。
    """
    engine, err = _ocr_engine()
    if err:
        return None, err
    try:
        res, _elapse = engine(image_bytes)
    except Exception as e:
        traceback.print_exc()
        return None, 'OCR 识别失败: {}'.format(e)
    out = []
    for row in (res or []):
        box, text, conf = row[0], row[1], row[2]
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        out.append({'text': str(text), 'conf': round(float(conf), 4),
                    'page': page, 'source': source,
                    'box': [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))]})
    out.sort(key=lambda l: (l['box'][1], l['box'][0]))
    return out, None


def _render_pdf_page(raw, index0, scale=None):
    """渲染 PDF 的第 index0 页(0 基) 成 PNG bytes。返回 (png, error)。"""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None, ('未安装 pypdfium2, 无法把 PDF 页渲成图。'
                      '装法: /root/miniconda3/bin/pip install pypdfium2')
    try:
        doc = pdfium.PdfDocument(raw)
        try:
            bmp = doc[index0].render(scale=scale or OCR_PDF_RENDER_SCALE)
            buf = io.BytesIO()
            bmp.to_pil().save(buf, 'PNG')
            return buf.getvalue(), None
        finally:
            doc.close()
    except Exception as e:
        traceback.print_exc()
        return None, 'PDF 第 {} 页渲染失败: {}'.format(index0 + 1, e)


def _pdf_page_plan(raw):
    """用 pdf-inspector 给 PDF 分流。返回 (plan, meta)。

    plan 形如 [{'page':1, 'need_ocr':False, 'markdown':'...'}, ...] (page 1 基)。

    这里有个必须留神的地方: pdf-inspector 顶层的 pages_needing_ocr 是 **1 基**,
    而 PageMarkdown.page 是 **0 基**。两个字段挨着放, 混用就会错开一页 ——
    表现是"某一页明明是照片却被当成有文字层", 那一页就整页读不出来还不报错。
    """
    meta = {'pdf_route': 'pdf-inspector'}
    try:
        import pdf_inspector
    except ImportError:
        meta['pdf_route'] = 'none'
        meta['note'] = '未安装 pdf-inspector, 全部页面走 OCR'
        return None, meta
    try:
        pm = pdf_inspector.extract_pages_markdown_bytes(raw)
        cls = pdf_inspector.process_pdf_bytes(raw)
        meta['pdf_type'] = getattr(cls, 'pdf_type', None)
        meta['has_encoding_issues'] = bool(getattr(cls, 'has_encoding_issues', False))
        meta['pages_needing_ocr'] = list(getattr(cls, 'pages_needing_ocr', None) or [])
        reasons = {}
        for r in (getattr(cls, 'ocr_reasons_by_page', None) or []):
            reasons[int(getattr(r, 'page', 0))] = list(getattr(r, 'reasons', None) or [])
        meta['ocr_reasons'] = reasons
        plan = []
        for p in (getattr(pm, 'pages', None) or []):
            md = getattr(p, 'markdown', None) or ''
            plan.append({'page': int(getattr(p, 'page', 0)) + 1,      # 0 基 -> 1 基
                         'need_ocr': bool(getattr(p, 'needs_ocr', False)) or not md.strip(),
                         'markdown': md})
        # 文字层坏掉时(CID/ToUnicode 有问题)取出来的是乱码, 比没有更糟 —— 一律改走 OCR
        if meta['has_encoding_issues']:
            for it in plan:
                it['need_ocr'] = True
            meta['note'] = 'PDF 字体编码有问题, 文字层不可信, 全部页面改走 OCR'
        return (plan or None), meta
    except Exception as e:
        traceback.print_exc()
        meta['pdf_route'] = 'failed'
        meta['note'] = 'pdf-inspector 分流失败, 全部页面走 OCR: {}'.format(e)
        return None, meta


def ocr_read_source(raw, ext):
    """源文件 -> (lines, meta, error)。

    PDF 走分流: 有文字层的页直接取字(快, 且没有识别错误), 没有的页才渲图送 OCR。
    meta 里如实分列 pages_text_layer / pages_ocr / pages_unread ——
    读不到的页必须说出来, 否则一份"前 3 页电子版 + 第 4 页照片"的材料
    会安安静静只返回前 3 页, 看起来像整份都读完了。
    """
    meta = {'ext': ext, 'pages_text_layer': [], 'pages_ocr': [], 'pages_unread': [],
            'capped': False}
    t0 = time.time()

    if ext != 'pdf':
        lines, err = _ocr_lines(raw, page=1, source='ocr')
        if err:
            return None, meta, err
        meta['page_count'] = 1
        meta['pages_ocr'] = [1]
        meta['engine'] = 'rapidocr'
        meta['ocr_ms'] = int((time.time() - t0) * 1000)
        return lines, meta, None

    plan, pmeta = _pdf_page_plan(raw)
    meta.update(pmeta)
    if plan is None:
        # 分流不可用: 只能整份走 OCR, 页数从渲染器问
        try:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(raw)
            n = len(doc)
            doc.close()
        except ImportError:
            return None, meta, ('这是 PDF, 但既没有 pdf-inspector 也没有 pypdfium2, '
                                '无法处理。装法: /root/miniconda3/bin/pip install '
                                'pdf-inspector pypdfium2')
        except Exception as e:
            return None, meta, 'PDF 打不开: {}'.format(e)
        plan = [{'page': i + 1, 'need_ocr': True, 'markdown': ''} for i in range(n)]

    meta['page_count'] = len(plan)
    if len(plan) > OCR_MAX_PAGES:
        meta['capped'] = True
        meta['cap_note'] = '共 {} 页, 只处理了前 {} 页(PLATFORM_OCR_MAX_PAGES)'.format(
            len(plan), OCR_MAX_PAGES)
        meta['pages_unread'] += [p['page'] for p in plan[OCR_MAX_PAGES:]]
        plan = plan[:OCR_MAX_PAGES]

    lines = []
    for item in plan:
        pno = item['page']
        if not item['need_ocr']:
            for ln in _lines_from_markdown(item['markdown'], pno):
                lines.append(ln)
            meta['pages_text_layer'].append(pno)
            continue
        png, err = _render_pdf_page(raw, pno - 1)
        if err:
            meta['pages_unread'].append(pno)
            meta.setdefault('unread_reasons', {})[str(pno)] = err
            continue
        got, err = _ocr_lines(png, page=pno, source='ocr')
        if err:
            # 引擎不可用是整体性问题, 不是这一页的问题 —— 直接把错误抛回去
            if not meta['pages_ocr'] and not meta['pages_text_layer']:
                return None, meta, err
            meta['pages_unread'].append(pno)
            meta.setdefault('unread_reasons', {})[str(pno)] = err
            continue
        lines += got
        meta['pages_ocr'].append(pno)

    meta['engine'] = 'rapidocr+text_layer' if meta['pages_text_layer'] and meta['pages_ocr'] \
        else ('text_layer' if meta['pages_text_layer'] else 'rapidocr')
    meta['ocr_ms'] = int((time.time() - t0) * 1000)
    if not lines and meta['pages_unread']:
        return None, meta, '第 {} 页读不出来, 全文没有可用内容'.format(
            '、'.join(str(p) for p in meta['pages_unread']))
    return lines, meta, None


def _lines_from_markdown(md, page):
    """文字层的 markdown -> 行。没有坐标(PDF 文字层不给外接框), box 记 None。

    source 记成 text_layer 而不是 ocr: 这不是识别结果, 是文档自带的字符数据,
    准确性和 OCR 不是一回事, 后面允不允许"一键采纳"就靠这个区分。
    """
    out = []
    for raw_line in str(md or '').split('\n'):
        t = re.sub(r'^#+\s*', '', raw_line).strip()
        if not t:
            continue
        out.append({'text': t, 'conf': 1.0, 'page': page,
                    'source': 'text_layer', 'box': None})
    return out


# ---- 行 -> 待核字段 ----

def _ocr_norm(s):
    """比对用的归一化: 去空白、全角转半角、统一分隔符。

    只做这些。**不做数字近似**: "4. 15" 归一化成 "4.15" 已经够宽了,
    再往下(比如把 O 当 0)就等于替人猜, 猜错的那次谁也发现不了。
    """
    s = str(s if s is not None else '')
    out = []
    for ch in s:
        o = ord(ch)
        if o == 0x3000:
            continue
        if 0xFF01 <= o <= 0xFF5E:
            ch = chr(o - 0xFEE0)
        out.append(ch)
    s = ''.join(out)
    s = re.sub(r'\s+', '', s)
    return s.replace('：', ':').replace('，', ',')


_OCR_SEP = re.compile(r'^[\s:：=＝\-—－·。、]+')


def _find_value_for_label(lines, labels):
    """在 OCR 行里找 labels 中任一标签对应的值。返回 (value, line) 或 (None, None)。

    两种版式都要认:
      同行  "姓名：赵慧敏"          -> 取冒号后面
      分列  "空腹血糖"  |  "6.8"    -> 取同一行带里 x 更大的下一段
    """
    norm_labels = [(_ocr_norm(x), x) for x in labels if str(x or '').strip()]
    for i, ln in enumerate(lines):
        nt = _ocr_norm(ln['text'])
        for nl, _orig in norm_labels:
            if not nl or nl not in nt:
                continue
            rest = nt.split(nl, 1)[1]
            rest = _OCR_SEP.sub('', rest)
            if rest:
                return rest, ln
            side = _same_row_right(lines, i)
            if side is not None:
                return _ocr_norm(side['text']), side
            return None, ln
    return None, None


def _same_row_right(lines, idx):
    """找与 lines[idx] 同一行带、且在其右侧最近的一段文字。没有坐标时返回 None。"""
    cur = lines[idx]
    if not cur.get('box'):
        return None
    _x0, y0, x1, y1 = cur['box']
    h = max(1, y1 - y0)
    best = None
    for j, ln in enumerate(lines):
        if j == idx or not ln.get('box') or ln['page'] != cur['page']:
            continue
        bx0, by0, _bx1, by1 = ln['box']
        overlap = min(y1, by1) - max(y0, by0)
        if overlap < h * 0.5 or bx0 <= x1:
            continue
        if best is None or bx0 < best['box'][0]:
            best = ln
    return best


def cluster_table_rows(lines, page=None):
    """把 OCR 行按 y 带聚成表格行。返回 [{'y':..,'cells':[{text,conf,box}]}]。

    只给人看的**线索**, 不作为答案 —— 聚错一行的后果是几个化验值串位,
    而串位后的值每一个看上去都是合法的。所以表格题在核对时必须整表键入。
    """
    ls = [l for l in lines if l.get('box') and (page is None or l['page'] == page)]
    ls.sort(key=lambda l: (l['box'][1], l['box'][0]))
    rows = []
    for ln in ls:
        _x0, y0, _x1, y1 = ln['box']
        mid = (y0 + y1) / 2.0
        h = max(1, y1 - y0)
        placed = False
        for r in rows:
            if abs(r['y'] - mid) <= h * 0.6:
                r['cells'].append(ln)
                r['y'] = (r['y'] * (len(r['cells']) - 1) + mid) / len(r['cells'])
                placed = True
                break
        if not placed:
            rows.append({'y': mid, 'cells': [ln]})
    for r in rows:
        r['cells'].sort(key=lambda l: l['box'][0])
        r['y'] = int(r['y'])
    rows.sort(key=lambda r: r['y'])
    return [{'y': r['y'],
             'cells': [{'text': c['text'], 'conf': c['conf'], 'box': c['box']}
                       for c in r['cells']]} for r in rows]


def build_ocr_candidates(definition, lines):
    """按 CRF 定义生成待核字段。**每一道要采集的题都生成一条**, 不管 OCR 有没有命中。

    理由见本节顶部第 4 条: 实测里 5 个 ↑ 只检出 1 个, 漏掉的没有任何提示。
    只给命中项建记录, 核对清单本身就是残缺的, 人再认真也补不回来。
    """
    out = []
    for sec, it in _crf_items(definition):
        t = it.get('type')
        if t in CRF_NO_ANSWER_TYPES:
            continue
        labels = [it.get('text') or '']
        labels += [str(x) for x in (it.get('ocr_hints') or [])]
        value, ln = (None, None)
        if t not in CRF_TABLE_TYPES:
            value, ln = _find_value_for_label(lines, labels)
        else:
            # 表格题不猜值, 只把标签所在页记下来, 让前端把整块原图摆出来
            _v, ln = _find_value_for_label(lines, labels)
            value = None
        out.append({
            'field_key': it.get('id'),
            'field_label': (sec + ' / ' if sec else '') + (it.get('text') or it.get('id') or ''),
            'value_type': t,
            'required': bool(it.get('required')),
            'ocr_value': value,
            'ocr_confidence': (ln or {}).get('conf') if value is not None else None,
            'ocr_source': (ln or {}).get('source') or 'ocr',
            'ocr_page': (ln or {}).get('page'),
            'ocr_box': (ln or {}).get('box'),
        })
    return out


def _can_accept(field):
    """这个字段允不允许"看一眼原图就采纳"。返回 (bool, 理由)。

    文字层不是识别结果, 是文档自带的字符数据, 所以可以采纳;
    OCR 出来的数字/日期/表格一律不行 —— 错了看不出来的正是这几类。
    """
    t = field.get('value_type')
    if field.get('ocr_value') in (None, ''):
        return False, '识别没取到值, 只能人工键入或标记为"原件上没有"'
    if field.get('ocr_source') == 'text_layer':
        return True, ''
    if t in OCR_TYPED_ONLY:
        return False, ('{} 题不提供"采纳识别值", 必须人工键入 —— '
                       '识别把 4.15 读成 "4. 15" 这类错误不会报错, 只会安静地写进数据'
                       .format(_ocr_type_label(t)))
    conf = field.get('ocr_confidence')
    if conf is None or float(conf) < OCR_ACCEPT_MIN_CONF:
        return False, '识别置信度 {} 低于 {}, 必须人工键入'.format(
            conf, OCR_ACCEPT_MIN_CONF)
    return True, ''


def _ocr_type_label(t):
    if t in CRF_BASIC_TYPES:
        return CRF_BASIC_TYPES[t]
    if t in CRF_TABLE_TYPES:
        return CRF_TABLE_TYPES[t][0]
    return str(t)


# 这些题型的最终值按 JSON 存: 选项的 value 可能是数字, 存成字符串再交给
# validate_crf_data 会被判成"不在选项范围内" —— 类型必须原样保住。
OCR_JSON_VALUE_TYPES = ('single', 'select', 'multi') + tuple(CRF_TABLE_TYPES)


def _ocr_find_item(definition, field_key):
    for _sec, it in _crf_items(definition or {}):
        if it.get('id') == field_key:
            return it
    return None


def _ocr_option_value(it, raw):
    """把人给的一个选项(填标签或填值都行)映射成该选项的 value。返回 (value, error)。"""
    opts = it.get('options') or []
    r = _ocr_norm(raw)
    for o in opts:
        if _ocr_norm(o.get('label')) == r or _ocr_norm(o.get('value')) == r:
            return o.get('value'), None
    return None, '"{}" 不在选项里。可选: {}'.format(
        raw, '/'.join(str(o.get('label')) for o in opts[:8]) or '(该题没有配选项)')


def _ocr_coerce(it, raw):
    """核对时就把人给的值校到位。返回 (存库用的字符串, 显示用的值, error)。

    刻意放在核对这一步而不是提交那一步: 提交时才报"这不是数字", 人已经核完
    几十个字段了, 还得回头找是哪个; 而且那时原件那一块早就不在眼前了。
    """
    t = it.get('type')
    if t == 'number':
        try:
            fv = float(str(raw).strip())
        except (TypeError, ValueError):
            return None, None, '"{}" 不是数字'.format(raw)
        lo, hi = it.get('min'), it.get('max')
        if (lo is not None and fv < lo) or (hi is not None and fv > hi):
            return None, None, '{} 超出该题允许范围 {}~{}'.format(
                raw, lo if lo is not None else '-', hi if hi is not None else '-')
        return str(raw).strip(), fv, None
    if t == 'date':
        s = re.sub(r'[./年月]', '-', str(raw).strip()).rstrip('-日')
        m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', s)
        if not m:
            return None, None, '"{}" 不是日期。格式 YYYY-MM-DD'.format(raw)
        s = '%04d-%02d-%02d' % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return s, s, None
    if t in ('single', 'select'):
        v, err = _ocr_option_value(it, raw)
        if err:
            return None, None, err
        return json.dumps(v, ensure_ascii=False), v, None
    if t == 'multi':
        parts = [x for x in re.split(r'[,，、;；]', str(raw)) if x.strip()]
        vals = []
        for p in parts:
            v, err = _ocr_option_value(it, p)
            if err:
                return None, None, err
            vals.append(v)
        if not vals:
            return None, None, '多选题至少要选一项'
        return json.dumps(vals, ensure_ascii=False), vals, None
    return str(raw).strip(), str(raw).strip(), None


# ---- 落库 ----

def ensure_platform_ocr_tables():
    """M25: OCR 任务表 + 待核字段表 + 留痕表 (idempotent)。"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_ocr_job (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                job_no VARCHAR(32) NOT NULL,
                crf_code VARCHAR(64) NOT NULL,
                crf_version VARCHAR(32) NOT NULL COMMENT '识别时钉的版本 —— CRF 改版后
                    旧任务仍按旧版核对, 否则待核清单会和当初看到的原件对不上',
                patient_no VARCHAR(64) DEFAULT NULL,
                visit_name VARCHAR(100) DEFAULT NULL,
                plan_id BIGINT DEFAULT NULL,
                orig_name VARCHAR(255) DEFAULT NULL COMMENT '仅供显示',
                stored_name VARCHAR(160) NOT NULL COMMENT '磁盘上的名字, 服务端生成',
                ext VARCHAR(8) NOT NULL,
                size_bytes BIGINT NOT NULL,
                sha256 CHAR(64) NOT NULL,
                page_count INT DEFAULT NULL,
                engine VARCHAR(40) DEFAULT NULL,
                ocr_ms INT DEFAULT NULL,
                line_count INT DEFAULT 0,
                mean_conf DECIMAL(6,4) DEFAULT NULL,
                lines_json MEDIUMTEXT DEFAULT NULL COMMENT '每行 text/conf/box/page/source',
                meta_json TEXT DEFAULT NULL COMMENT '分流结果: 哪几页走文字层/OCR/没读成',
                status ENUM('recognized','verifying','committed','failed','abandoned')
                    DEFAULT 'recognized',
                response_id BIGINT DEFAULT NULL COMMENT '核完后落到哪条 CRF 填报',
                uploader VARCHAR(64) DEFAULT NULL,
                note VARCHAR(500) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uk_ocr_job (job_no),
                INDEX idx_patient (patient_no),
                INDEX idx_status (status),
                INDEX idx_crf (crf_code, crf_version)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M25 OCR 识别任务'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_ocr_field (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                job_no VARCHAR(32) NOT NULL,
                field_key VARCHAR(64) NOT NULL,
                field_label VARCHAR(200) DEFAULT NULL,
                value_type VARCHAR(24) NOT NULL,
                is_required TINYINT(1) DEFAULT 0,
                ocr_value VARCHAR(1000) DEFAULT NULL COMMENT '识别原值。只读, 任何时候都不修改
                    —— 留着才能事后算这套引擎在本院单据上的真实准确率',
                ocr_confidence DECIMAL(6,4) DEFAULT NULL,
                ocr_source VARCHAR(16) DEFAULT 'ocr' COMMENT 'ocr / text_layer',
                ocr_page INT DEFAULT NULL,
                ocr_box VARCHAR(64) DEFAULT NULL COMMENT 'x0,y0,x1,y1 供裁原图',
                final_value MEDIUMTEXT DEFAULT NULL COMMENT '人工确认后的值。写进 CRF 的只能是它',
                verify_state ENUM('unverified','match','corrected','not_found','na')
                    DEFAULT 'unverified',
                verified_by VARCHAR(64) DEFAULT NULL,
                verified_at DATETIME DEFAULT NULL,
                note VARCHAR(500) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uk_ocr_field (job_no, field_key),
                INDEX idx_state (job_no, verify_state)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M25 待人工核对的候选字段'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS platform_ocr_log (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                job_no VARCHAR(32) NOT NULL,
                field_key VARCHAR(64) DEFAULT NULL,
                action VARCHAR(24) NOT NULL COMMENT 'recognize/verify/commit/abandon',
                operator VARCHAR(64) DEFAULT NULL,
                detail VARCHAR(1000) DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_job (job_no)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 M25 识别与核对留痕(只增不改)'
        """)
        print('[启动] platform_ocr_job / platform_ocr_field / platform_ocr_log 表已就绪')
        cur.close()
    except Exception as e:
        print('[启动] ensure_platform_ocr_tables 失败:', e)
    finally:
        conn.close()


def _ocr_log(job_no, action, operator=None, detail=None, field_key=None):
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute('INSERT INTO platform_ocr_log (job_no, field_key, action, operator, detail) '
                    'VALUES (%s,%s,%s,%s,%s)',
                    (job_no, field_key, action, operator, (detail or '')[:1000] or None))
        cur.close()
        conn.close()
    except Exception:
        traceback.print_exc()


def _ocr_crf_definition(code, version=None):
    """取一份 CRF 定义。version 给了就取那一版 —— 识别任务钉的是识别当时那一版。"""
    ensure_platform_crf_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        if version:
            cur.execute('SELECT version, definition FROM platform_crf WHERE code=%s AND version=%s',
                        (str(code), str(version)))
        else:
            cur.execute('SELECT version, definition FROM platform_crf WHERE code=%s AND active=1 '
                        'ORDER BY updated_at DESC LIMIT 1', (str(code),))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if not row:
        return None, 'CRF 不存在或已停用: {}{}'.format(code, ' v' + str(version) if version else '')
    d = row[1]
    if isinstance(d, str):
        d = json.loads(d)
    return d, None


def ocr_recognize(body):
    """识别一份材料, 产出**待人工核对**的候选字段。

    {crf_code, crf_version?, filename, content_base64, patient_no?, visit_name?,
     plan_id?, operator?, note?}

    这个函数不写任何患者数据 —— 它的全部产出是一张待核清单。
    """
    code = str(body.get('crf_code') or body.get('code') or '').strip()
    if not code:
        return None, 'crf_code 必填 —— 识别结果要按哪张 CRF 的题目去核对, 必须先定下来'
    ext = _ocr_safe_ext(body.get('filename'))
    if not ext:
        return None, '只接受这些格式: {}'.format('/'.join(OCR_ALLOWED_EXT))
    try:
        import base64 as _b64
        raw = _b64.b64decode(body.get('content_base64') or '')
    except Exception:
        return None, 'content_base64 不是合法 base64'
    if not raw:
        return None, '文件是空的'
    if len(raw) > OCR_MAX_BYTES:
        return None, '文件超过 {}MB'.format(OCR_MAX_BYTES // 1024 // 1024)

    version = body.get('crf_version') or body.get('version')
    definition, err = _ocr_crf_definition(code, version)
    if err:
        return None, err
    if not version:
        ensure_platform_crf_tables()
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute('SELECT version FROM platform_crf WHERE code=%s AND active=1 '
                        'ORDER BY updated_at DESC LIMIT 1', (code,))
            version = cur.fetchone()[0]
            cur.close()
        finally:
            conn.close()

    lines, meta, err = ocr_read_source(raw, ext)
    if err:
        return None, err

    import hashlib
    import secrets as _secrets
    sha = hashlib.sha256(raw).hexdigest()
    job_no = 'OCR' + _secrets.token_hex(5).upper()
    stored = '{}_{}.{}'.format(job_no, sha[:12], ext)
    try:
        if not os.path.isdir(OCR_DIR):
            # exist_ok: 两个请求同时上传时, 一个刚建完另一个的 makedirs 就会炸,
            # 报出来是"原件写入失败", 看着像磁盘问题, 实际只是撞了一下
            os.makedirs(OCR_DIR, exist_ok=True)
        with open(os.path.join(OCR_DIR, stored), 'wb') as f:
            f.write(raw)
    except Exception as e:
        return None, '原件写入失败({}): {}'.format(OCR_DIR, e)

    cands = build_ocr_candidates(definition, lines)
    confs = [l['conf'] for l in lines if l.get('source') == 'ocr']
    mean_conf = round(sum(confs) / len(confs), 4) if confs else None

    ensure_platform_ocr_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO platform_ocr_job
              (job_no, crf_code, crf_version, patient_no, visit_name, plan_id,
               orig_name, stored_name, ext, size_bytes, sha256, page_count, engine,
               ocr_ms, line_count, mean_conf, lines_json, meta_json, status, uploader, note)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'recognized',%s,%s)
        """, (job_no, code, str(version), body.get('patient_no') or None,
              body.get('visit_name') or None, body.get('plan_id'),
              str(body.get('filename') or '')[:255] or None, stored, ext, len(raw), sha,
              meta.get('page_count'), meta.get('engine'), meta.get('ocr_ms'),
              len(lines), mean_conf,
              json.dumps(lines, ensure_ascii=False),
              json.dumps(meta, ensure_ascii=False),
              body.get('operator') or None, (body.get('note') or None)))
        for c in cands:
            cur.execute("""
                INSERT INTO platform_ocr_field
                  (job_no, field_key, field_label, value_type, is_required, ocr_value,
                   ocr_confidence, ocr_source, ocr_page, ocr_box)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (job_no, c['field_key'], c['field_label'][:200], c['value_type'],
                  1 if c['required'] else 0,
                  (c['ocr_value'] or None) if c['ocr_value'] is None else str(c['ocr_value'])[:1000],
                  c['ocr_confidence'], c['ocr_source'], c['ocr_page'],
                  ','.join(str(x) for x in c['ocr_box']) if c['ocr_box'] else None))
        cur.close()
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()

    hit = sum(1 for c in cands if c['ocr_value'] not in (None, ''))
    _ocr_log(job_no, 'recognize', body.get('operator'),
             '{} 页 / {} 行 / 命中 {}·共 {} 个待核字段'.format(
                 meta.get('page_count'), len(lines), hit, len(cands)))
    return {'ok': True, 'job_no': job_no, 'crf_code': code, 'crf_version': str(version),
            'page_count': meta.get('page_count'), 'line_count': len(lines),
            'mean_conf': mean_conf, 'fields_total': len(cands), 'fields_prefilled': hit,
            'meta': meta, 'table_rows_hint': cluster_table_rows(lines),
            'notice': ('识别结果**尚未进入任何患者数据**。下面 {} 个字段要逐个人工核对, '
                       '其中 {} 个识别到了候选值、{} 个没识别到(仍需处置)。'
                       '数字/日期/表格题不提供"采纳识别值", 只能对着原件键入。'
                       .format(len(cands), hit, len(cands) - hit))}, None


def ocr_job_fetch(job_no, with_lines=False):
    """取一个识别任务: 任务本身 + 全部待核字段 (+ 可选的原始识别行)。"""
    job_no = str(job_no or '').strip()
    if not job_no:
        return None, 'job_no 必填'
    ensure_platform_ocr_tables()
    conn = get_connection()
    try:
        cur = conn.cursor(pymysql.cursors.DictCursor)
        cur.execute('SELECT * FROM platform_ocr_job WHERE job_no=%s', (job_no,))
        job = cur.fetchone()
        if not job:
            cur.close()
            return None, '识别任务不存在: {}'.format(job_no)
        lines = job.pop('lines_json', None)
        meta = job.pop('meta_json', None)
        job['meta'] = json.loads(meta) if isinstance(meta, str) else (meta or {})
        for k in ('created_at', 'updated_at'):
            if job.get(k) is not None:
                job[k] = job[k].strftime('%Y-%m-%d %H:%M:%S')
        if job.get('mean_conf') is not None:
            job['mean_conf'] = float(job['mean_conf'])
        cur.execute('SELECT * FROM platform_ocr_field WHERE job_no=%s ORDER BY id', (job_no,))
        fields = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    for f in fields:
        if f.get('ocr_confidence') is not None:
            f['ocr_confidence'] = float(f['ocr_confidence'])
        if f.get('verified_at') is not None:
            f['verified_at'] = f['verified_at'].strftime('%Y-%m-%d %H:%M:%S')
        f.pop('created_at', None)
        f['box'] = [int(x) for x in f['ocr_box'].split(',')] if f.get('ocr_box') else None
        ok, why = _can_accept(f)
        f['can_accept'] = ok
        f['accept_blocked_reason'] = why or None
        f['state_label'] = OCR_VERIFY_STATES.get(f['verify_state'], f['verify_state'])
    pending = [f['field_key'] for f in fields if f['verify_state'] == 'unverified']
    out = {'ok': True, 'job': job, 'fields': fields,
           'pending': pending, 'pending_count': len(pending),
           'verified_count': len(fields) - len(pending),
           'can_commit': not pending and job['status'] not in ('committed', 'abandoned')}
    if with_lines:
        out['lines'] = json.loads(lines) if isinstance(lines, str) else (lines or [])
    return out, None


def ocr_verify_field(body):
    """人工核对**一个**字段。{job_no, field_key, action, value?/rows?, operator, note?}

    action: typed(键入) / accept(采纳识别值) / not_found(原件没有) / na(不适用)

    刻意不做批量接口。"全部采纳"这个动作只要存在, 核对就会退化成点一下 ——
    而这套东西的全部意义就在于每个值都被人看过一眼原件。
    """
    job_no = str(body.get('job_no') or '').strip()
    field_key = body.get('field_key')
    operator = str(body.get('operator') or '').strip()
    action = str(body.get('action') or '').strip()
    if not job_no or not field_key:
        return None, 'job_no 和 field_key 必填'
    if isinstance(field_key, (list, tuple)):
        return None, '一次只能核一个字段 —— 没有批量核对接口, 理由见 M25 注释'
    if not operator:
        return None, 'operator 必填 —— 核对记录要落到具体的人, 这是 GCP 的最低要求'
    if action not in ('typed', 'accept', 'not_found', 'na'):
        return None, "action 必须是 typed / accept / not_found / na 之一"

    ensure_platform_ocr_tables()
    conn = get_connection()
    try:
        cur = conn.cursor(pymysql.cursors.DictCursor)
        cur.execute('SELECT status, crf_code, crf_version FROM platform_ocr_job '
                    'WHERE job_no=%s', (job_no,))
        job = cur.fetchone()
        if not job:
            cur.close()
            return None, '识别任务不存在: {}'.format(job_no)
        if job['status'] == 'committed':
            cur.close()
            return None, '这个任务已经提交进 CRF 了, 要改数据请走 CRF 的修订(revision_of), ' \
                         '不要回头改核对记录 —— 那会让留痕和实际入库的数据对不上'
        if job['status'] == 'abandoned':
            cur.close()
            return None, '这个任务已作废'
        cur.execute('SELECT * FROM platform_ocr_field WHERE job_no=%s AND field_key=%s',
                    (job_no, str(field_key)))
        fld = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if not fld:
        return None, '字段不存在: {}'.format(field_key)
    if fld.get('ocr_confidence') is not None:
        fld['ocr_confidence'] = float(fld['ocr_confidence'])

    definition, err = _ocr_crf_definition(job['crf_code'], job['crf_version'])
    if err:
        return None, err
    item = _ocr_find_item(definition, str(field_key)) or {'type': fld['value_type'],
                                                          'id': str(field_key)}

    state, final = None, None
    if action == 'na':
        state, final = 'na', None
    elif action == 'not_found':
        state, final = 'not_found', None
    elif action == 'accept':
        ok, why = _can_accept(fld)
        if not ok:
            return None, why
        final, _shown, err = _ocr_coerce(item, fld['ocr_value'])
        if err:
            return None, '识别值不能直接采纳: {} —— 请人工键入'.format(err)
        state = 'match'
    else:                       # typed
        if fld['value_type'] in CRF_TABLE_TYPES:
            rows = body.get('rows')
            if not isinstance(rows, list):
                return None, '表格题要给 rows(数组, 一个元素一行), 且必须是人工逐格键入的'
            errs = _validate_crf_table_data(item, rows) if item.get('columns') else []
            if errs:
                return None, '表格内容不合法: {}'.format(errs[0].get('error'))
            final = json.dumps(rows, ensure_ascii=False)
            # 表格题没有识别原值可比(见 build_ocr_candidates), 一律记成人工填入
            state = 'corrected'
        else:
            if 'value' not in body:
                return None, 'typed 必须给 value'
            v = body.get('value')
            if v is None or str(v).strip() == '':
                return None, 'value 是空的。原件上确实没有请用 action=not_found, ' \
                             '本次不适用请用 action=na —— 空值和"没有"不是一回事'
            final, _shown, err = _ocr_coerce(item, v)
            if err:
                return None, err
            state = 'match' if (fld['ocr_value'] is not None and
                                _ocr_norm(v) == _ocr_norm(fld['ocr_value'])) else 'corrected'

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""UPDATE platform_ocr_field
                       SET final_value=%s, verify_state=%s, verified_by=%s, verified_at=NOW(),
                           note=%s
                       WHERE job_no=%s AND field_key=%s""",
                    (final, state, operator, (body.get('note') or None),
                     job_no, str(field_key)))
        cur.execute("UPDATE platform_ocr_job SET status='verifying' "
                    "WHERE job_no=%s AND status='recognized'", (job_no,))
        cur.close()
    except Exception as e:
        traceback.print_exc()
        return None, str(e)
    finally:
        conn.close()

    _ocr_log(job_no, 'verify', operator, '{} -> {} (识别值: {})'.format(
        action, state, fld['ocr_value']), field_key=str(field_key))
    res, err = ocr_job_fetch(job_no)
    if err:
        return None, err
    return {'ok': True, 'field_key': str(field_key), 'verify_state': state,
            'state_label': OCR_VERIFY_STATES[state],
            'matched_ocr': state == 'match',
            'pending_count': res['pending_count'], 'can_commit': res['can_commit']}, None


def ocr_commit(body):
    """把核完的字段写进 CRF。{job_no, patient_no?, operator, visit_name?, allow_warnings?}

    三条硬性前提, 缺一不可:
      - 每一个待核字段都已处置(没有 unverified)
      - 写进去的只能是 final_value —— ocr_value 在这个函数里根本不参与取值
      - 走 submit_crf_response, 也就是照样过 M14 的逻辑与强弱校验
    """
    job_no = str(body.get('job_no') or '').strip()
    operator = str(body.get('operator') or '').strip()
    if not job_no:
        return None, 'job_no 必填'
    if not operator:
        return None, 'operator 必填'

    res, err = ocr_job_fetch(job_no)
    if err:
        return None, err
    job, fields = res['job'], res['fields']
    if job['status'] == 'committed':
        return None, '已经提交过了 (CRF 填报 id={})'.format(job.get('response_id'))
    if job['status'] == 'abandoned':
        return None, '这个任务已作废'
    if res['pending']:
        return None, ('还有 {} 个字段没核对: {}{}。识别结果不能整体入库 —— '
                      '没核过的字段里既可能是识别错的, 也可能是识别整个漏掉的'
                      .format(len(res['pending']), '、'.join(res['pending'][:8]),
                              ' 等' if len(res['pending']) > 8 else ''))
    patient_no = str(body.get('patient_no') or job.get('patient_no') or '').strip()
    if not patient_no:
        return None, 'patient_no 必填(识别时没填, 提交时要补上)'

    data, unwritten = {}, []
    for f in fields:
        if f['verify_state'] not in ('match', 'corrected'):
            unwritten.append({'field': f['field_key'], 'state': f['verify_state']})
            continue
        v, t = f['final_value'], f['value_type']
        if t in OCR_JSON_VALUE_TYPES:
            # 选项题和表格题在核对那一步就已经存成 JSON 了(选项 value 可能是数字,
            # 存成字符串会被 validate_crf_data 判成"不在选项范围内")
            try:
                data[f['field_key']] = json.loads(v)
            except Exception:
                return None, '{} 的答案不是合法 JSON —— 这条核对记录坏了, 请重核该字段'.format(
                    f['field_key'])
        elif t == 'number':
            try:
                fv = float(v)
                data[f['field_key']] = int(fv) if fv == int(fv) else fv
            except (TypeError, ValueError):
                return None, '{} 核对后的值 "{}" 不是数字'.format(f['field_key'], v)
        else:
            data[f['field_key']] = v

    payload = {'crf_code': job['crf_code'], 'crf_version': job['crf_version'],
               'patient_no': patient_no, 'data': data,
               'visit_name': body.get('visit_name') or job.get('visit_name'),
               'plan_id': body.get('plan_id') or job.get('plan_id'),
               'operator': operator, 'allow_warnings': body.get('allow_warnings')}
    out, err = submit_crf_response(payload)
    if err:
        return None, err
    if not out.get('accepted'):
        out['job_no'] = job_no
        out['hint'] = (out.get('hint') or '') + ' (CRF 校验没过, 本次未入库; 识别任务仍是待提交状态)'
        return out, None

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE platform_ocr_job SET status='committed', response_id=%s, "
                    "patient_no=%s WHERE job_no=%s", (out['id'], patient_no, job_no))
        cur.close()
    finally:
        conn.close()
    corrected = [f['field_key'] for f in fields if f['verify_state'] == 'corrected']
    _ocr_log(job_no, 'commit', operator,
             'CRF 填报 id={} | 写入 {} 项 | 人工改正 {} 项 | 未写入 {} 项'.format(
                 out['id'], len(data), len(corrected), len(unwritten)))
    return {'ok': True, 'job_no': job_no, 'response_id': out['id'],
            'crf_code': job['crf_code'], 'crf_version': job['crf_version'],
            'patient_no': patient_no, 'written_fields': len(data),
            'corrected_fields': corrected, 'unwritten': unwritten,
            'warnings': out.get('warnings') or [],
            'note': '写进 CRF 的全部是人工核对后的值; 识别原值留在 platform_ocr_field 里可追溯'}, None


def ocr_abandon(body):
    """作废一个识别任务(照片拍糊了/传错人了)。原件和留痕都留着, 不物理删。"""
    job_no = str(body.get('job_no') or '').strip()
    operator = str(body.get('operator') or '').strip()
    reason = str(body.get('reason') or '').strip()
    if not job_no or not operator:
        return None, 'job_no 和 operator 必填'
    if not reason:
        return None, 'reason 必填 —— 作废要说明理由'
    ensure_platform_ocr_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT status FROM platform_ocr_job WHERE job_no=%s", (job_no,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, '识别任务不存在: {}'.format(job_no)
        if row[0] == 'committed':
            cur.close()
            return None, '已提交进 CRF 的任务不能作废 —— 要改数据走 CRF 修订'
        cur.execute("UPDATE platform_ocr_job SET status='abandoned' WHERE job_no=%s", (job_no,))
        cur.close()
    finally:
        conn.close()
    _ocr_log(job_no, 'abandon', operator, reason)
    return {'ok': True, 'job_no': job_no, 'status': 'abandoned',
            'note': '原件与识别记录都留着, 没有物理删除'}, None


def ocr_job_list(patient_no=None, crf_code=None, status=None, limit=50):
    """识别任务列表。"""
    ensure_platform_ocr_tables()
    where, params = [], []
    if patient_no:
        where.append('j.patient_no=%s'); params.append(str(patient_no))
    if crf_code:
        where.append('j.crf_code=%s'); params.append(str(crf_code))
    if status:
        if status not in OCR_JOB_STATUSES:
            return None, 'status 必须是 {} 之一'.format('/'.join(OCR_JOB_STATUSES))
        where.append('j.status=%s'); params.append(status)
    try:
        limit = max(1, min(int(limit or 50), 500))
    except (TypeError, ValueError):
        limit = 50
    sql = ("SELECT j.job_no, j.crf_code, j.crf_version, j.patient_no, j.visit_name, "
           "j.orig_name, j.ext, j.page_count, j.line_count, j.mean_conf, j.status, "
           "j.response_id, j.uploader, j.created_at, "
           "(SELECT COUNT(*) FROM platform_ocr_field f WHERE f.job_no=j.job_no) AS fields_total, "
           "(SELECT COUNT(*) FROM platform_ocr_field f WHERE f.job_no=j.job_no "
           " AND f.verify_state='unverified') AS fields_pending "
           "FROM platform_ocr_job j")
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY j.id DESC LIMIT %d' % limit
    conn = get_connection()
    try:
        cur = conn.cursor(pymysql.cursors.DictCursor)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    for r in rows:
        if r.get('created_at') is not None:
            r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        if r.get('mean_conf') is not None:
            r['mean_conf'] = float(r['mean_conf'])
        r['fields_total'] = int(r['fields_total'] or 0)
        r['fields_pending'] = int(r['fields_pending'] or 0)
    return {'ok': True, 'count': len(rows), 'jobs': rows}, None


OCR_CROP_MAX_SIDE = 1600


def ocr_crop(job_no, field_key=None, page=None, box=None, pad=12):
    """裁一块原件图片出来。返回 (png_bytes, error)。

    核对界面必须把原图那一块摆在输入框旁边。让人凭记忆核对等于没核对,
    而回原件里逐行找位置又慢到没人愿意做 —— 这个接口就是为了消掉这个摩擦。

    **一律经 PIL 重新编码成 PNG, 绝不把上传的原字节直接回给浏览器。**
    这个响应是要 <img> 内联显示的(不像 M17 的资料下载是 attachment), 而
    "扩展名是 .png 的文件"和"真的是 PNG"是两回事 —— 直接透传就等于让人上传
    任意字节再由我们的域内联发出去。重编码之后回的必然是我们自己生成的 PNG。
    """
    job_no = str(job_no or '').strip()
    if not job_no:
        return None, 'job_no 必填'
    ensure_platform_ocr_tables()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT stored_name, ext FROM platform_ocr_job WHERE job_no=%s', (job_no,))
        row = cur.fetchone()
        if not row:
            cur.close()
            return None, '识别任务不存在: {}'.format(job_no)
        stored, ext = row
        if field_key and box is None:
            cur.execute('SELECT ocr_page, ocr_box FROM platform_ocr_field '
                        'WHERE job_no=%s AND field_key=%s', (job_no, str(field_key)))
            f = cur.fetchone()
            if not f:
                cur.close()
                return None, '字段不存在: {}'.format(field_key)
            page = f[0] or 1
            box = [int(x) for x in f[1].split(',')] if f[1] else None
        cur.close()
    finally:
        conn.close()

    path = os.path.join(OCR_DIR, os.path.basename(stored))
    if not os.path.isfile(path):
        return None, '原件文件不在了: {}'.format(stored)
    with open(path, 'rb') as fp:
        raw = fp.read()

    if ext == 'pdf':
        png, err = _render_pdf_page(raw, int(page or 1) - 1)
        if err:
            return None, err
        raw = png
    return _ocr_to_png(raw, box, pad)


def _ocr_to_png(raw, box=None, pad=12):
    """任意上传字节 -> **我们自己生成的** PNG。返回 (png, error)。

    单独抽出来是为了让"绝不透传原字节"这条能被直接测到: 传进一段带 .png 名字的
    HTML 进来, 出去的要么是报错、要么是一张真 PNG, 不可能是那段 HTML 原样。
    """
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        return None, '未安装 Pillow, 无法出图'
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except UnidentifiedImageError:
        # 上传的东西根本不是图片。这是常规的输入拒绝, 不是服务端故障, 别打整条栈
        return None, '这个文件不是图片(扩展名说是, 内容不是), 无法出图'
    except Exception as e:
        traceback.print_exc()
        return None, '图片打不开: {}'.format(e)
    try:
        if box is not None:
            x0, y0, x1, y1 = [int(v) for v in box]
            pad = max(0, min(int(pad or 0), 200))
            img = img.crop((max(0, x0 - pad), max(0, y0 - pad),
                            min(img.width, x1 + pad), min(img.height, y1 + pad)))
        else:
            # 没有坐标(文字层字段, 或前端要整页预览)就给整页, 但缩到能看清即可 ——
            # 原图可能是 4000px 的手机照片, 原样发出去核对界面要等好几秒
            long_side = max(img.width, img.height)
            if long_side > OCR_CROP_MAX_SIDE:
                k = float(OCR_CROP_MAX_SIDE) / long_side
                img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))))
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        buf = io.BytesIO()
        img.save(buf, 'PNG')
        return buf.getvalue(), None
    except Exception as e:
        traceback.print_exc()
        return None, '出图失败: {}'.format(e)


# ---------------------------------------------------------------------------
# 随访平台 M6: 队列数据导出
#
# 平台此前 12 个端点全是"看", 没有一个是"拿" —— 研究者无法把队列数据取走做统计,
# 而平台的立项理由正是"支撑临床随访研究的数据管理"。M6 补这个口子。
#
# 只读、不动任何表结构。走 X-Platform-Token 门禁: 与既有只读端点不同, 导出是整队列
# PHI 的批量拉取, 一个请求就能把全部患者档案+体征+报警+随访记录带走, 因此按写接口
# 的标准鉴权 (既有 GET 端点的无鉴权现状是另一件事, 见 claude-progress.txt 记录)。
# ---------------------------------------------------------------------------
EXPORT_KINDS = ('patients', 'vitals', 'alarms', 'followups', 'plans')

EXPORT_KIND_LABELS = {
    'patients': '患者档案',
    'vitals': '体征日聚合',
    'alarms': '报警事件',
    'followups': '随访记录',
    'plans': '随访计划',
}


def _export_fmt(v):
    """CSV 单元格取值: datetime/date 统一成字符串, None 留空, 其余原样交给 csv 模块。"""
    if v is None:
        return ''
    if hasattr(v, 'strftime'):
        return v.strftime('%Y-%m-%d %H:%M:%S') if hasattr(v, 'hour') else v.strftime('%Y-%m-%d')
    return v


def _csv_bytes(header, rows):
    """UTF-8-BOM CSV。

    BOM 是给 Excel 的 —— 没有它, 中文列名在 Excel 里打开是乱码 (LibreOffice / pandas /
    R 都不受影响, 会把 BOM 当空白跳过)。行结束符固定 \\r\\n, 与 Excel 的期望一致。
    """
    buf = io.StringIO(newline='')
    w = csv.writer(buf, lineterminator='\r\n')
    w.writerow(header)
    for r in rows:
        w.writerow([_export_fmt(v) for v in r])
    return b'\xef\xbb\xbf' + buf.getvalue().encode('utf-8')


def _export_patients(cur, patient_no):
    where, params = ('WHERE p.patient_no = %s', [patient_no]) if patient_no else ('', [])
    cur.execute(
        'SELECT p.patient_no, p.name, p.gender, p.age, p.group_tag, p.zhenmaiyi_case_id, '
        'p.note, p.created_at, p.updated_at, '
        '(SELECT d.device_id FROM iwown_device d WHERE d.patient_no = p.patient_no '
        ' ORDER BY d.last_seen DESC LIMIT 1) AS iwown_device_id '
        'FROM platform_patient p ' + where + ' ORDER BY p.patient_no', params)
    header = ['门诊号', '姓名', '性别(M男/F女)', '年龄', '队列分组', '诊脉仪case_id',
              '备注', '建档时间', '更新时间', 'iwown设备号']
    return header, [list(r) for r in cur.fetchall()]


def _export_alarms(cur, patient_no):
    where, params = ('WHERE a.patient_no = %s', [patient_no]) if patient_no else ('', [])
    cur.execute(
        'SELECT a.id, a.patient_no, p.name, a.device_id, a.alarm_type, a.severity, a.status, '
        'a.occurred_at, a.created_at, a.lat, a.lng, '
        '(SELECT COUNT(*) FROM platform_followup_log f WHERE f.alarm_id = a.id) AS followup_count '
        'FROM platform_alarm a LEFT JOIN platform_patient p ON p.patient_no = a.patient_no '
        + where + ' ORDER BY a.occurred_at DESC, a.id DESC', params)
    header = ['报警ID', '门诊号', '姓名', '设备号', '报警类型', '严重度', '状态',
              '发生时间', '入库时间', '纬度', '经度', '处理次数']
    return header, [list(r) for r in cur.fetchall()]


def _export_followups(cur, patient_no):
    where, params = ('WHERE f.patient_no = %s', [patient_no]) if patient_no else ('', [])
    cur.execute(
        'SELECT f.id, f.patient_no, p.name, f.action, f.result_text, f.operator, '
        'f.alarm_id, f.plan_id, pl.name, f.created_at '
        'FROM platform_followup_log f '
        'LEFT JOIN platform_patient p ON p.patient_no = f.patient_no '
        'LEFT JOIN platform_plan pl ON pl.id = f.plan_id '
        + where + ' ORDER BY f.created_at DESC, f.id DESC', params)
    header = ['记录ID', '门诊号', '姓名', '动作', '结果文本', '操作人',
              '关联报警ID', '关联计划ID', '计划名', '记录时间']
    return header, [list(r) for r in cur.fetchall()]


def _export_plans(cur, patient_no):
    where, params = ('WHERE pl.patient_no = %s', [patient_no]) if patient_no else ('', [])
    cur.execute(
        'SELECT pl.id, pl.patient_no, p.name, pl.name, pl.frequency_days, pl.next_due, '
        'pl.active, pl.note, pl.created_at, pl.updated_at '
        'FROM platform_plan pl LEFT JOIN platform_patient p ON p.patient_no = pl.patient_no '
        + where + ' ORDER BY pl.active DESC, pl.next_due', params)
    header = ['计划ID', '门诊号', '姓名', '计划名', '周期天数(空=一次性)', '下次到期',
              '启用中(1是/0否)', '备注', '创建时间', '更新时间']
    return header, [list(r) for r in cur.fetchall()]


_VITAL_FIELDS = ('hr_avg', 'hr_min', 'hr_max', 'spo2_avg', 'spo2_min', 'spo2_max',
                 'sbp', 'dbp', 'temperature', 'step')


def _export_vitals(cur, patient_no, days):
    """跨链路体征日聚合摊平成长表: 一行 = 一个患者 × 一天 × 一条链路。

    性能取舍: S101 日聚合 (_s101_patient_vitals) 每调一次就全表扫一遍
    wearable_device_data 的大 JSON, 所以这里先用 _s101_scan_by_patient() 扫一次拿到
    "哪些门诊号有 S101 数据", 只对命中的患者调日聚合 —— 生产上多数患者没有 S101 链路,
    这一步把 N 次全表扫降到 1 + (有 S101 数据的患者数) 次。队列规模上到几十人以后
    仍需把 S101 聚合改成一次扫描分桶, 那是比本次导出更大的改动, 不在 M6 范围内。
    """
    where, params = ('WHERE p.patient_no = %s', [patient_no]) if patient_no else ('', [])
    cur.execute(
        'SELECT p.patient_no, p.name, '
        '(SELECT d.device_id FROM iwown_device d WHERE d.patient_no = p.patient_no '
        ' ORDER BY d.last_seen DESC LIMIT 1) AS iwown_device_id '
        'FROM platform_patient p ' + where + ' ORDER BY p.patient_no', params)
    patients = cur.fetchall()

    s101_present = _s101_scan_by_patient()

    rows = []
    for (p_no, name, dev) in patients:
        if dev:
            for d in (_iwown_daily_vitals(dev, days=days).get('daily') or []):
                rows.append([p_no, name, 'iwown', d.get('date')] +
                            [d.get(f) for f in _VITAL_FIELDS])
        if p_no in s101_present:
            for d in (_s101_patient_vitals(p_no, days=days).get('daily') or []):
                rows.append([p_no, name, 'S101/R04', d.get('date')] +
                            [d.get(f) for f in _VITAL_FIELDS])
    rows.sort(key=lambda r: (str(r[0]), str(r[3]), str(r[2])))
    header = ['门诊号', '姓名', '数据链路', '日期', '心率均值', '心率最低', '心率最高',
              '血氧均值', '血氧最低', '血氧最高', '收缩压', '舒张压', '体温', '步数']
    return header, rows


def platform_export(kind, patient_no=None, days=90):
    """导出一种(或全部)数据集。

    返回 (body_bytes, filename, mimetype, err)。kind='all' 打成一个 zip, 内含 5 个 CSV ——
    研究者要的通常是"把整个队列拿走", 分 5 次点按钮不合理。
    """
    if kind != 'all' and kind not in EXPORT_KINDS:
        return None, None, None, 'kind 必须是 {} 或 all'.format('/'.join(EXPORT_KINDS))
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    conn = get_connection()
    try:
        cur = conn.cursor()

        def build(k):
            if k == 'patients':
                return _export_patients(cur, patient_no)
            if k == 'alarms':
                return _export_alarms(cur, patient_no)
            if k == 'followups':
                return _export_followups(cur, patient_no)
            if k == 'plans':
                return _export_plans(cur, patient_no)
            return _export_vitals(cur, patient_no, days)

        scope = ('-' + re.sub(r'[^0-9A-Za-z_-]', '', str(patient_no))) if patient_no else ''
        if kind == 'all':
            zbuf = io.BytesIO()
            with zipfile.ZipFile(zbuf, 'w', zipfile.ZIP_DEFLATED) as zf:
                for k in EXPORT_KINDS:
                    header, rows = build(k)
                    zf.writestr('{}-{}.csv'.format(k, EXPORT_KIND_LABELS[k]),
                                _csv_bytes(header, rows))
            cur.close()
            return (zbuf.getvalue(),
                    '随访平台队列导出{}-{}.zip'.format(scope, stamp),
                    'application/zip', None)

        header, rows = build(kind)
        cur.close()
        return (_csv_bytes(header, rows),
                '随访平台-{}{}-{}.csv'.format(EXPORT_KIND_LABELS[kind], scope, stamp),
                'text/csv; charset=utf-8', None)
    except Exception as e:
        traceback.print_exc()
        return None, None, None, str(e)
    finally:
        conn.close()


def platform_auto_ingest_loop(interval_min):
    """随访平台 M4: 报警自动摄入后台线程 (design doc §3.4 提到的自动化 ingest, 替代人工点
    "拉取新报警"按钮)。每 interval_min 分钟跑两件事 —— 都与各自的 POST 端点复用同一份
    核心函数, 端点仍保留、仍走 token 门禁, 这个线程只是定时帮你点一次:
      1) platform_alarm_ingest()      iwown 4G 设备侧报警帧的解码归类 (M2)
      2) platform_vital_alarm_ingest() S101 体征阈值/趋势 + 脉诊仪新报告 (M7)
    只有 inserted>0 或抛异常时才打印一行日志(避免刷屏); 两件事各自 catch, 一件出错不影响
    另一件; 任何异常都在本轮内吞掉继续下一轮, 绝不能让线程挂掉导致自动摄入从此停摆。
    """
    while True:
        time.sleep(max(1, interval_min) * 60)
        try:
            result, err = platform_alarm_ingest()
            if err:
                print('[自动摄入] platform_alarm_ingest 出错:', err)
            elif result and result.get('inserted', 0) > 0:
                print('[自动摄入] 扫描 {} 条, 新增 {} 条报警事件'.format(
                    result.get('scanned', 0), result.get('inserted', 0)))
        except Exception as e:
            # catch-all: 任何异常都不能让这个 daemon 线程退出
            print('[自动摄入] 线程内异常(已捕获, 继续下一轮):', e)
        try:
            # 判定窗口固定 1 天: 这个循环每 interval_min 分钟就跑一次, 只需要覆盖当天的新数据,
            # 幂等键保证同一采样点重复判定不会重复入库。补历史要用 POST 端点手动传大 days。
            result, err = platform_vital_alarm_ingest(days=1)
            if err:
                print('[自动摄入] platform_vital_alarm_ingest 出错:', err)
            elif result and result.get('inserted', 0) > 0:
                print('[自动摄入] M7 体征判定新增 {} 条 (阈值 {} / 趋势 {} / 脉诊 {})'.format(
                    result['inserted'], result['detail']['s101_threshold'],
                    result['detail']['s101_trend'], result['detail']['zhenmaiyi']))
        except Exception as e:
            print('[自动摄入] M7 线程内异常(已捕获, 继续下一轮):', e)


# ============ HTTP Handler ============
class HealthDataHandler(BaseHTTPRequestHandler):

    def _send_file(self, raw, meta):
        """下发一份上传的资料。

        三个必须的响应头:
          Content-Disposition: attachment  —— 强制下载而不是在浏览器里渲染。上传的
              文档里可能有主动内容, 在我们自己的域下渲染就是存储型 XSS。
          X-Content-Type-Options: nosniff  —— 关掉浏览器的类型嗅探, 否则 Content-Type
              写成 octet-stream 也可能被"猜"成 html 后渲染。
          Content-Type: application/octet-stream —— 一律当字节流, 不按扩展名给真实类型。
        文件名走 RFC 5987 的 filename*, 中文名才不会乱码。
        """
        name = meta.get('orig_name') or '{}.{}'.format(meta.get('title') or 'document', meta.get('ext') or 'bin')
        quoted = urllib.parse.quote(str(name), safe='')
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quoted)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('X-Content-SHA256', meta.get('sha256') or '')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Platform-Token')
        self.send_header('Access-Control-Expose-Headers', 'Content-Disposition, X-Content-SHA256')
        self.end_headers()
        self.wfile.write(raw)

    def _send_image(self, png):
        """内联下发一张 PNG (M25 核对界面要把原件那一块摆在输入框旁边)。

        和 _send_file 的 attachment 相反, 这个是要在页面里显示的, 所以两件事必须成立:
        字节是**我们自己用 PIL 重编码出来的** PNG (见 ocr_crop), 且带 nosniff ——
        两条合起来才能保证浏览器不会把它当成别的东西渲染。
        """
        self.send_response(200)
        self.send_header('Content-Type', 'image/png')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'private, no-store')
        self.send_header('Content-Length', str(len(png)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Platform-Token')
        self.end_headers()
        self.wfile.write(png)

    def _send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        # X-Platform-Token: 随访平台写接口门禁头 (check_platform_token), 不加进这里
        # 跨域(如 GitHub Pages prototype -> dc.ncrc.org.cn)会在预检阶段被浏览器拦截,
        # POST 请求根本发不出去 —— M5 联调(本机 8800 + 3000 跨端口)才暴露出这个此前一直存在的缺口。
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Platform-Token')
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

    def _send_download(self, body, filename, mimetype):
        """M6 导出: 附件下载响应。

        文件名是中文, 必须走 RFC 5987 的 filename*=UTF-8''<percent-encoded> —— 裸中文放
        filename= 会被浏览器按 latin-1 解成乱码。同时保留一个 ASCII 版 filename= 作为
        老客户端兜底。Content-Disposition 要进 Access-Control-Expose-Headers, 否则跨域
        (GitHub Pages -> dc.ncrc.org.cn) 的前端 JS 读不到文件名。
        zip 已经是压缩流, 不再叠 gzip; CSV 走和 _send_json 同一档的 gzip 阈值。
        """
        ascii_name = re.sub(r'[^0-9A-Za-z._-]', '_', filename) or 'export'
        self.send_response(200)
        self.send_header('Content-Type', mimetype)
        self.send_header('Content-Disposition',
                         "attachment; filename=\"{}\"; filename*=UTF-8''{}".format(
                             ascii_name, urllib.parse.quote(filename, safe='')))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Platform-Token')
        self.send_header('Access-Control-Expose-Headers', 'Content-Disposition')
        self.send_header('Vary', 'Accept-Encoding')
        accept_enc = (self.headers.get('Accept-Encoding') or '').lower()
        if mimetype != 'application/zip' and 'gzip' in accept_enc and len(body) > 1024:
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
            # M4 新增: wear_rate_7d (近 7 天平均佩戴率), 纯附加字段, 不改动已有字段
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

                # M5 新增: task_due_count (今日到期+逾期的随访任务数), 一条聚合查询覆盖所有患者
                # (GROUP BY patient_no, 不逐患者单独查询), 与上面 alarm_open_map 同一惯例;
                # 供列表页任务角标直接用, 不必再为角标单独发一次 /api/platform/tasks 请求。
                cur.execute(
                    "SELECT patient_no, COUNT(*) FROM platform_plan "
                    "WHERE active = 1 AND next_due <= CURDATE() AND patient_no IS NOT NULL GROUP BY patient_no"
                )
                task_due_map = {p_no: n for p_no, n in cur.fetchall()}

                # M4: 近 7 天平均佩戴率, 两条聚合查询覆盖所有已绑定设备(GROUP BY device_id,
                # 不逐患者单独查询), 与 _iwown_compliance_daily() 用同一套口径合并:
                # 1) 每设备每日 wear_hours; 2) 每设备每日 not_worn 报警数(用来把"当天 0 帧但
                # 有未佩戴报警"的日子也算进分母, 否则这天会因为 iwown_data 没有行而在
                # GROUP BY 里直接消失, 跟详情页 compliance summary 的均值口径对不上)。
                cur.execute("""
                    SELECT device_id, DATE(recorded_at) AS d, COUNT(DISTINCT HOUR(recorded_at)) AS day_hours
                    FROM iwown_data
                    WHERE data_type = 'health' AND recorded_at IS NOT NULL
                      AND recorded_at >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
                    GROUP BY device_id, DATE(recorded_at)
                """)
                device_day_hours = {}
                for dev, d, day_hours in cur.fetchall():
                    date_str = d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)
                    device_day_hours.setdefault(dev, {})[date_str] = int(day_hours)

                cur.execute("""
                    SELECT device_id, DATE(occurred_at) AS d
                    FROM platform_alarm
                    WHERE alarm_type = 'not_worn' AND device_id IS NOT NULL AND occurred_at IS NOT NULL
                      AND occurred_at >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
                    GROUP BY device_id, DATE(occurred_at)
                """)
                for dev, d in cur.fetchall():
                    date_str = d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)
                    device_day_hours.setdefault(dev, {}).setdefault(date_str, 0)
                cur.close()
                conn.close()

                wear_rate_map = {}
                for dev, day_hours_map in device_day_hours.items():
                    rates = [h / 24.0 for h in day_hours_map.values()]
                    wear_rate_map[dev] = round(sum(rates) / len(rates), 2) if rates else None

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
                        'wear_rate_7d': wear_rate_map.get(iw['device_id']) if iw else None,
                        'task_due_count': task_due_map.get(p_no, 0),
                    })
                self._send_json(200, {'ok': True, 'patients': patients})
            except Exception as e:
                traceback.print_exc()
                self._send_json(500, {'ok': False, 'error': str(e)})

        elif pathname == '/api/platform/scales':
            result, err = query_platform_scales(
                code=(query.get('code') or [None])[0],
                category=(query.get('category') or [None])[0],
                active_only=(query.get('all') or ['0'])[0] not in ('1', 'true'),
                with_definition=(query.get('withDefinition') or ['0'])[0] in ('1', 'true'))
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/scale/responses':
            try:
                limit = min(int((query.get('limit') or ['100'])[0]), 500)
            except (TypeError, ValueError):
                self._send_json(400, {'ok': False, 'error': 'limit 必须是整数'}); return
            result, err = query_scale_responses(
                patient_no=(query.get('patientNo') or [None])[0],
                code=(query.get('code') or [None])[0],
                include_superseded=(query.get('includeSuperseded') or ['0'])[0] in ('1', 'true'),
                limit=limit)
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/studies':
            st = (query.get('status') or [None])[0]
            if st and st not in STUDY_STATUSES:
                self._send_json(400, {'ok': False,
                                      'error': 'status 必须是 {} 之一'.format('/'.join(STUDY_STATUSES))}); return
            result, err = query_studies(
                code=(query.get('code') or [None])[0], status=st,
                include_deleted=(query.get('includeDeleted') or ['0'])[0] in ('1', 'true'),
                with_log=(query.get('withLog') or ['0'])[0] in ('1', 'true'),
                limit=min(int((query.get('limit') or ['100'])[0] or 100), 300))
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/study/can':
            ok, reason = study_check_action((query.get('code') or [None])[0],
                                            (query.get('action') or [None])[0])
            self._send_json(200, {'ok': True, 'allowed': ok, 'reason': reason})

        elif pathname == '/api/platform/version/history':
            result, err = query_version_history((query.get('kind') or [None])[0],
                                                (query.get('code') or [None])[0])
            self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/ocr/status':
            # 唯一不带门禁的 OCR 端点: 只报引擎装没装, 不碰任何任务或患者数据
            self._send_json(200, {'ok': True, 'status': ocr_engine_status()})

        elif pathname == '/api/platform/ocr/jobs':
            if not check_platform_token(self):
                return
            result, err = ocr_job_list(
                patient_no=(query.get('patientNo') or [None])[0],
                crf_code=(query.get('crfCode') or [None])[0],
                status=(query.get('status') or [None])[0],
                limit=(query.get('limit') or ['50'])[0])
            self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/ocr/job':
            # 待核清单里带着识别出的姓名/门诊号, 按写接口标准鉴权(同 M17 资料下载的不对称)
            if not check_platform_token(self):
                return
            result, err = ocr_job_fetch((query.get('job') or [None])[0],
                                        with_lines=(query.get('withLines') or ['0'])[0] in ('1', 'true'))
            self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/ocr/crop':
            if not check_platform_token(self):
                return
            box = (query.get('box') or [None])[0]
            try:
                box = [int(x) for x in box.split(',')] if box else None
                if box is not None and len(box) != 4:
                    raise ValueError
            except (TypeError, ValueError):
                self._send_json(400, {'ok': False, 'error': 'box 形如 x0,y0,x1,y1'}); return
            png, err = ocr_crop((query.get('job') or [None])[0],
                                field_key=(query.get('field') or [None])[0],
                                page=(query.get('page') or [None])[0], box=box,
                                pad=(query.get('pad') or ['12'])[0])
            if err:
                self._send_json(400, {'ok': False, 'error': err}); return
            self._send_image(png)

        elif pathname == '/api/platform/screen/tasks':
            st = (query.get('status') or [None])[0]
            if st and st not in SCREEN_TASK_STATUSES:
                self._send_json(400, {'ok': False,
                                      'error': 'status 必须是 {} 之一'.format('/'.join(SCREEN_TASK_STATUSES))}); return
            result, err = query_screen_tasks((query.get('code') or [None])[0], st,
                                             limit=min(int((query.get('limit') or ['100'])[0] or 100), 300))
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/screen/submissions':
            st = (query.get('status') or [None])[0]
            if st and st not in SUBMISSION_STATUSES:
                self._send_json(400, {'ok': False,
                                      'error': 'status 必须是 {} 之一'.format('/'.join(SUBMISSION_STATUSES))}); return
            result, err = query_screen_submissions((query.get('task') or [None])[0], st,
                                                   limit=min(int((query.get('limit') or ['200'])[0] or 200), 500))
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/screen/form':
            # **公开接口**(患者扫码后打开)。只吐表单结构, 从不吐任何患者数据 ——
            # 若按门诊号回显"您的既往信息", 任何人猜一个号就能读别人的病历。
            result, err = screen_form_public((query.get('t') or [None])[0])
            self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/consults':
            lv = (query.get('level') or [None])[0]
            if lv and lv not in TRIAGE_LEVELS:
                self._send_json(400, {'ok': False,
                                      'error': 'level 必须是 {} 之一'.format('/'.join(TRIAGE_LEVELS))}); return
            result, err = query_consults(
                patient_no=(query.get('patientNo') or [None])[0], level=lv,
                session_id=(query.get('session') or [None])[0],
                unreviewed=(query.get('unreviewed') or ['0'])[0] in ('1', 'true'),
                limit=min(int((query.get('limit') or ['200'])[0] or 200), 500))
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/exports':
            st = (query.get('status') or [None])[0]
            if st and st not in EXPORT_JOB_STATUSES:
                self._send_json(400, {'ok': False,
                                      'error': 'status 必须是 {} 之一'.format('/'.join(EXPORT_JOB_STATUSES))}); return
            result, err = export_job_query((query.get('job') or [None])[0], st,
                                           min(int((query.get('limit') or ['100'])[0] or 100), 300))
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/export/download':
            # 同 M17 的下载: 读操作却要写接口口令 —— 导出件里是整队患者的数据
            if not check_platform_token(self):
                return
            blob, meta, err = export_job_fetch(
                (query.get('job') or [None])[0], (query.get('operator') or [None])[0],
                self.client_address[0] if self.client_address else None)
            if err:
                self._send_json(404 if '不存在' in err else 400, {'ok': False, 'error': err}); return
            self._send_file(blob, meta)

        elif pathname == '/api/platform/visit/summary':
            try:
                da = int((query.get('daysAhead') or ['7'])[0] or 7)
            except (TypeError, ValueError):
                self._send_json(400, {'ok': False, 'error': 'daysAhead 必须是整数'}); return
            result, err = visit_overdue_summary(da)
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/pushes':
            st = (query.get('status') or [None])[0]
            if st and st not in PUSH_STATUSES:
                self._send_json(400, {'ok': False,
                                      'error': 'status 必须是 {} 之一'.format('/'.join(PUSH_STATUSES))}); return
            result, err = query_pushes(
                patient_no=(query.get('patientNo') or [None])[0], status=st,
                batch_id=(query.get('batch') or [None])[0],
                content_type=(query.get('type') or [None])[0],
                limit=min(int((query.get('limit') or ['300'])[0] or 300), 1000))
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/followups':
            result, err = query_followups(
                patient_no=(query.get('patientNo') or [None])[0],
                visit_id=(query.get('visitId') or [None])[0],
                batch_id=(query.get('batch') or [None])[0],
                limit=min(int((query.get('limit') or ['300'])[0] or 300), 1000))
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/flows':
            result, err = query_flows(
                code=(query.get('code') or [None])[0],
                scope=(query.get('scope') or [None])[0],
                category=(query.get('category') or [None])[0],
                all_versions=(query.get('allVersions') or ['0'])[0] in ('1', 'true'),
                with_definition=(query.get('withDefinition') or ['0'])[0] in ('1', 'true'),
                limit=min(int((query.get('limit') or ['100'])[0] or 100), 200))
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/visits':
            st = (query.get('status') or [None])[0]
            if st and st not in VISIT_STATUSES:
                self._send_json(400, {'ok': False,
                                      'error': 'status 必须是 {} 之一'.format('/'.join(VISIT_STATUSES))}); return
            dw = (query.get('dueWithin') or [None])[0]
            result, err = query_visits(
                patient_no=(query.get('patientNo') or [None])[0],
                flow_code=(query.get('flow') or [None])[0], status=st,
                due_within=int(dw) if dw is not None else None,
                overdue_only=(query.get('overdue') or ['0'])[0] in ('1', 'true'),
                limit=min(int((query.get('limit') or ['500'])[0] or 500), 2000))
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/flow/completion':
            result, err = flow_completion((query.get('flow') or [None])[0],
                                          (query.get('cohort') or [None])[0])
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/cohorts':
            result, err = query_cohorts(
                code=(query.get('code') or [None])[0],
                with_groups=(query.get('withGroups') or ['1'])[0] in ('1', 'true'),
                with_log=(query.get('withLog') or ['0'])[0] in ('1', 'true'),
                limit=min(int((query.get('limit') or ['100'])[0] or 100), 200))
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/enrollments':
            st = (query.get('status') or [None])[0]
            if st and st not in ENROLL_STATUSES:
                self._send_json(400, {'ok': False,
                                      'error': 'status 必须是 {} 之一'.format('/'.join(ENROLL_STATUSES))}); return
            result, err = query_enrollments(
                cohort_code=(query.get('cohort') or [None])[0],
                group_code=(query.get('group') or [None])[0], status=st,
                limit=min(int((query.get('limit') or ['500'])[0] or 500), 2000))
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/cohort/evaluate':
            result, err = cohort_evaluate((query.get('code') or [None])[0],
                                          limit=min(int((query.get('limit') or ['500'])[0] or 500), 2000))
            self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/documents':
            dt = (query.get('type') or [None])[0]
            if dt and dt not in DOC_TYPES:
                self._send_json(400, {'ok': False,
                                      'error': 'type 必须是 {} 之一'.format('/'.join(DOC_TYPES))}); return
            result, err = query_documents(
                doc_type=dt, code=(query.get('code') or [None])[0],
                category=(query.get('category') or [None])[0],
                status=(query.get('status') or [None])[0],
                with_log=(query.get('withLog') or ['0'])[0] in ('1', 'true'),
                limit=min(int((query.get('limit') or ['200'])[0] or 200), 500))
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/document/download':
            # 这是个读操作, 却要写接口口令 —— 刻意的。签好的知情同意书含姓名、身份证号、
            # 手写签名, 比平台其余不鉴权接口暴露的门诊号严重得多, 不该谁都能下载。
            if not check_platform_token(self):
                return
            raw, meta, err = fetch_document_bytes(
                (query.get('code') or [None])[0], (query.get('version') or [None])[0])
            if err:
                self._send_json(404 if '不存在' in err or '找不到' in err else 400,
                                {'ok': False, 'error': err}); return
            self._send_file(raw, meta)

        elif pathname == '/api/platform/consents':
            result, err = query_consents(
                patient_no=(query.get('patientNo') or [None])[0],
                doc_code=(query.get('code') or [None])[0],
                status=(query.get('status') or [None])[0],
                with_signature=(query.get('withSignature') or ['0'])[0] in ('1', 'true'),
                limit=min(int((query.get('limit') or ['200'])[0] or 200), 500))
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/search/fields':
            self._send_json(200, search_field_catalog())

        elif pathname == '/api/platform/edu':
            try:
                limit = min(int((query.get('limit') or ['200'])[0]), 500)
            except (TypeError, ValueError):
                self._send_json(400, {'ok': False, 'error': 'limit 必须是整数'}); return
            st = (query.get('status') or [None])[0]
            if st and st not in EDU_STATUSES:
                self._send_json(400, {'ok': False,
                                      'error': 'status 必须是 {} 之一'.format('/'.join(EDU_STATUSES))}); return
            tp = (query.get('topic') or [None])[0]
            if tp and tp not in EDU_TOPICS:
                self._send_json(400, {'ok': False,
                                      'error': 'topic 必须是 {} 之一'.format('/'.join(EDU_TOPICS))}); return
            result, err = query_edu_materials(
                status=st, category=(query.get('category') or [None])[0], topic=tp,
                scope=(query.get('scope') or [None])[0],
                keyword=(query.get('q') or [None])[0],
                with_body=(query.get('withBody') or ['0'])[0] in ('1', 'true'),
                material_id=(query.get('id') or [None])[0], limit=limit)
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/crfs':
            try:
                limit = min(int((query.get('limit') or ['200'])[0]), 500)
            except (TypeError, ValueError):
                self._send_json(400, {'ok': False, 'error': 'limit 必须是整数'}); return
            scope = (query.get('scope') or [None])[0]
            if scope and scope not in CRF_SCOPES:
                self._send_json(400, {'ok': False, 'error': 'scope 必须是 private 或 shared'}); return
            result, err = query_platform_crfs(
                code=(query.get('code') or [None])[0],
                version=(query.get('version') or [None])[0],
                scope=scope, category=(query.get('category') or [None])[0],
                owner=(query.get('owner') or [None])[0],
                all_versions=(query.get('allVersions') or ['0'])[0] in ('1', 'true'),
                with_definition=(query.get('withDefinition') or ['0'])[0] in ('1', 'true'),
                limit=limit)
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/crf/responses':
            try:
                limit = min(int((query.get('limit') or ['100'])[0]), 500)
            except (TypeError, ValueError):
                self._send_json(400, {'ok': False, 'error': 'limit 必须是整数'}); return
            result, err = query_crf_responses(
                patient_no=(query.get('patientNo') or [None])[0],
                code=(query.get('code') or [None])[0],
                include_superseded=(query.get('includeSuperseded') or ['0'])[0] in ('1', 'true'),
                limit=limit)
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/qc/findings':
            try:
                limit = min(int((query.get('limit') or ['100'])[0]), 500)
            except (TypeError, ValueError):
                self._send_json(400, {'ok': False, 'error': 'limit 必须是整数'}); return
            status = (query.get('status') or [None])[0]
            if status and status not in ('open', 'queried', 'resolved', 'dismissed', 'all'):
                self._send_json(400, {'ok': False,
                                      'error': 'status 必须是 open/queried/resolved/dismissed/all'}); return
            severity = (query.get('severity') or [None])[0]
            if severity and severity not in ('block', 'warn'):
                self._send_json(400, {'ok': False, 'error': 'severity 必须是 block 或 warn'}); return
            result, err = query_qc_findings(
                status=None if status in (None, 'all') else status,
                patient_no=(query.get('patientNo') or [None])[0],
                severity=severity, limit=limit)
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/qc/queries':
            try:
                limit = min(int((query.get('limit') or ['100'])[0]), 500)
            except (TypeError, ValueError):
                self._send_json(400, {'ok': False, 'error': 'limit 必须是整数'}); return
            status = (query.get('status') or [None])[0]
            if status and status not in ('open', 'answered', 'closed', 'reopened'):
                self._send_json(400, {'ok': False,
                                      'error': 'status 必须是 open/answered/closed/reopened'}); return
            result, err = query_qc_queries(
                status=status, patient_no=(query.get('patientNo') or [None])[0],
                with_log=(query.get('withLog') or ['0'])[0] in ('1', 'true'),
                query_id=(query.get('id') or [None])[0], limit=limit)
            self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/qc/compare':
            result, err = platform_qc_compare(
                (query.get('patientNo') or [None])[0], (query.get('code') or [None])[0])
            self._send_json((400 if err and '必填' in err else 500) if err else 200,
                            {'ok': False, 'error': err} if err else result)

        elif pathname == '/api/platform/discover':
            try:
                min_records = int((query.get('minRecords') or ['0'])[0] or 0)
            except (TypeError, ValueError):
                self._send_json(400, {'ok': False, 'error': 'minRecords 必须是整数'})
                return
            since = (query.get('since') or [None])[0] or None
            if since and not re.match(r'^\d{4}-\d{2}-\d{2}$', since):
                self._send_json(400, {'ok': False, 'error': "since 必须是 'YYYY-MM-DD'"})
                return
            try:
                min_vitals = int((query.get('minVitals') or ['0'])[0] or 0)
            except (TypeError, ValueError):
                self._send_json(400, {'ok': False, 'error': 'minVitals 必须是整数'})
                return
            require_vitals = (query.get('requireVitals') or ['0'])[0] in ('1', 'true', 'yes')
            include = (query.get('include') or ['pending'])[0]
            if include not in ('pending', 'excluded', 'all'):
                self._send_json(400, {'ok': False, 'error': "include 必须是 pending/excluded/all"})
                return
            result, err = platform_discover_patients(
                min_records=min_records, min_vitals=min_vitals, since=since,
                require_vitals=require_vitals, include=include)
            if err:
                self._send_json(500, {'ok': False, 'error': err})
            else:
                self._send_json(200, result)

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

        elif pathname == '/api/platform/compliance':
            # 随访平台 M4: 单患者佩戴依从性明细 (design doc §3.4). 指标定义见
            # _iwown_compliance_daily() 顶部注释。未绑定 iwown 的患者(如仅 S101 的 S101 链路
            # 患者) 返回 daily=[] + summary 全 null, 这是设计范围内的行为, 不是 bug
            # (design doc: S101 链路没有连续在线流, M4 只覆盖 iwown 佩戴场景)。
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
                    'SELECT device_id FROM iwown_device WHERE patient_no = %s '
                    'ORDER BY last_seen DESC LIMIT 1', (patient_no,)
                )
                row = cur.fetchone()
                cur.close()
                conn.close()

                if not row:
                    self._send_json(200, {
                        'ok': True, 'patient_no': patient_no, 'days': days, 'daily': [],
                        'summary': {'avg_wear_rate': None, 'days_with_data': 0},
                    })
                    return

                daily = _iwown_compliance_daily(row[0], days=days)
                rates = [d['wear_rate'] for d in daily]
                avg_rate = round(sum(rates) / len(rates), 2) if rates else None
                self._send_json(200, {
                    'ok': True, 'patient_no': patient_no, 'days': days, 'daily': daily,
                    'summary': {'avg_wear_rate': avg_rate, 'days_with_data': len(daily)},
                })
            except Exception as e:
                traceback.print_exc()
                self._send_json(500, {'ok': False, 'error': str(e)})

        elif pathname == '/api/platform/plans':
            # 随访平台 M5: 随访计划列表 (?patientNo=&active=0|1, 都缺省 = 全部计划)
            patient_no = (query.get('patientNo') or [None])[0]
            active_raw = (query.get('active') or [None])[0]
            active = None
            if active_raw in ('0', '1'):
                active = int(active_raw)
            result, err = query_platform_plans(patient_no=patient_no, active=active)
            if err:
                self._send_json(500, {'ok': False, 'error': err})
            else:
                self._send_json(200, result)

        elif pathname == '/api/platform/tasks':
            # 随访平台 M5: 今日待办任务, 从 platform_plan 现算 (见 query_platform_tasks 注释)
            try:
                horizon_days = max(1, min(90, int((query.get('horizon_days') or ['7'])[0])))
            except (ValueError, TypeError):
                horizon_days = 7
            result, err = query_platform_tasks(horizon_days=horizon_days)
            if err:
                self._send_json(500, {'ok': False, 'error': err})
            else:
                self._send_json(200, result)

        elif pathname == '/api/platform/export':
            # 随访平台 M6: 队列数据导出 (CSV / 全量 zip)。整队列 PHI 批量拉取, 走写接口同款
            # token 门禁。GET 里需要鉴权的现在有这么几个, 判据都是"这一个响应里带出多少
            # 患者身份信息", 而不是它是读还是写: 本接口、export/download、
            # document/download(签好的知情同意书)、ocr/job 与 ocr/crop(病历原件照片)。
            if not check_platform_token(self):
                return
            kind = (query.get('kind') or ['all'])[0]
            patient_no = (query.get('patientNo') or [None])[0]
            try:
                days = max(1, min(3650, int((query.get('days') or ['90'])[0])))
            except (ValueError, TypeError):
                days = 90
            body, filename, mimetype, err = platform_export(kind, patient_no=patient_no, days=days)
            if err:
                self._send_json(400 if 'kind' in err else 500, {'ok': False, 'error': err})
            else:
                self._send_download(body, filename, mimetype)

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
                    'POST /api/platform/patients/batch': '随访平台 M9: 批量建档 {patients:[{patient_no,...}]}, 单条失败不影响其余',
                    'POST /api/platform/screening': "随访平台 M9: 筛查排除/撤销 {patient_no, action:'exclude'|'restore', reason, operator}",
                    'GET  /api/platform/scales': '随访平台 M10: 量表列表/单份定义 (?code=&category=&withDefinition=1)',
                    'POST /api/platform/scale': '随访平台 M10: 导入/改版量表 (定义结构校验不过则拒收)',
                    'POST /api/platform/scale/score': '随访平台 M10: 试评分 (只算不落库, 供填报页实时出分与预览)',
                    'POST /api/platform/scale/response': '随访平台 M10: 提交填报 (评分+校验+落库; revision_of 走修订留痕)',
                    'GET  /api/platform/scale/responses': '随访平台 M10: 填报记录 (?patientNo=&code=&includeSuperseded=1)',
                    'POST /api/platform/scale/parse': '随访平台 M11: 文档/PDF -> 量表草稿 ({text} 或 {pdf_base64}; 扫描件需先 OCR)',
                    'POST /api/platform/scale/generate': '随访平台 M12: AI 量表生成 (可插拔后端, 默认模板; 产出刻意不含划界值分级)',
                    'GET  /api/platform/studies': '随访平台 M24: 研究数据库(暂存/运行/重置/结束/已删除)',
                    'POST /api/platform/study': '随访平台 M24: 建/改研究数据库',
                    'POST /api/platform/study/transition': '随访平台 M24: 启用/重置/结束/删除/恢复(删除是逻辑删除)',
                    'GET  /api/platform/study/can': '随访平台 M24: 问这个状态下能不能做某动作 (?code=&action=enroll)',
                    'POST /api/platform/version/rollback': '随访平台 M24: 回滚 CRF/流程(产出新版本, 不删旧版)',
                    'GET  /api/platform/version/history': '随访平台 M24: 版本历史与回滚记录 (?kind=crf&code=)',
                    'GET  /api/platform/ocr/status': '随访平台 M25: OCR 引擎可用性(本地引擎, 不出网)',
                    'POST /api/platform/ocr/recognize': '随访平台 M25: 病历/检验单拍照 -> 待人工核对的候选字段(不入库)',
                    'GET  /api/platform/ocr/job': '随访平台 M25: 取识别任务与待核清单 (?job=)',
                    'GET  /api/platform/ocr/jobs': '随访平台 M25: 识别任务列表',
                    'GET  /api/platform/ocr/crop': '随访平台 M25: 裁一块原件图供核对 (?job=&field=)',
                    'POST /api/platform/ocr/verify': '随访平台 M25: 人工核对一个字段(一次一个, 数字/日期只能键入)',
                    'POST /api/platform/ocr/commit': '随访平台 M25: 核完后写进 CRF(全部核完才允许)',
                    'POST /api/platform/ocr/abandon': '随访平台 M25: 作废识别任务(不物理删)',
                    'GET  /api/platform/screen/tasks': '随访平台 M23: 筛查任务(含超期/待审批预警)',
                    'POST /api/platform/screen/task': '随访平台 M23: 建/改筛查任务',
                    'POST /api/platform/screen/task/transition': '随访平台 M23: 提交审批/批准/驳回/启停/结束',
                    'POST /api/platform/screen/link': '随访平台 M23: 生成自助填报链接(token 不可预测, 带有效期与次数上限)',
                    'GET  /api/platform/screen/form': '随访平台 M23 **公开**: 按 token 取表单结构(绝不返回患者数据)',
                    'POST /api/platform/screen/submit': '随访平台 M23 **公开**: 提交自助填报(落待审区, 不直接建档)',
                    'GET  /api/platform/screen/submissions': '随访平台 M23: 待审提交列表',
                    'POST /api/platform/screen/submission/review': '随访平台 M23: 审核采纳/驳回',
                    'POST /api/platform/consult': '随访平台 M22: 健康咨询(急症分诊前置, 不调模型直接中断; 不给诊断处方)',
                    'POST /api/platform/consult/triage': '随访平台 M22: 只跑高风险分诊({text}), 供语音随访等复用',
                    'GET  /api/platform/consults': '随访平台 M22: 咨询留痕(全程可审核, ?level=&unreviewed=1)',
                    'POST /api/platform/consult/review': '随访平台 M22: 医护复核一条咨询',
                    'POST /api/platform/export/job': '随访平台 M21: 建导出任务 (kind=full|pick|sdtm; 大样本走后台)',
                    'POST /api/platform/export/preview': '随访平台 M21: 变量挑选预览(不落任务)',
                    'GET  /api/platform/exports': '随访平台 M21: 导出记录(每次导出=一次患者数据出境)',
                    'GET  /api/platform/export/download': '随访平台 M21: 下载导出件 (?job=) —— 需 X-Platform-Token',
                    'GET  /api/platform/visit/summary': '随访平台 M20: 当日/超窗统计 (?daysAhead=7)',
                    'POST /api/platform/visit/followup': '随访平台 M20: 批量跟进超窗访视(逐条给结果)',
                    'GET  /api/platform/followups': '随访平台 M20: 跟进记录',
                    'POST /api/platform/push': '随访平台 M20: 建推送(单/分组/方案/全量; dry_run 默认 true; 通道未接只入队)',
                    'POST /api/platform/push/from-visit': '随访平台 M20: 把访视上配的患教内容入队',
                    'GET  /api/platform/pushes': '随访平台 M20: 推送队列 (?status=&patientNo=)',
                    'GET  /api/platform/flows': '随访平台 M19: 流程库 (?code=&scope=shared&allVersions=1)',
                    'POST /api/platform/flow': '随访平台 M19: 建/改流程模板(多级节点树+访视窗口+流程外阶段)',
                    'POST /api/platform/flow/copy': '随访平台 M19: 复制流程 ({code,new_code})',
                    'POST /api/platform/flow/instantiate': '随访平台 M19: 把流程实例化到患者, 生成访视表',
                    'POST /api/platform/flow/refresh': '随访平台 M19: 按今天重算访视状态(pending/due/overdue)',
                    'POST /api/platform/flow/end': '随访平台 M19: 终止患者流程 ({patient_no,flow_code,reason})',
                    'GET  /api/platform/visits': '随访平台 M19: 访视列表 (?patientNo=&status=&overdue=1&dueWithin=7)',
                    'POST /api/platform/visit/transition': '随访平台 M19: 完成/跳过/取消访视',
                    'POST /api/platform/visit/offschedule': '随访平台 M19: 事件触发流程外阶段(不良事件等)',
                    'GET  /api/platform/flow/completion': '随访平台 M19: 随访完成率(分母不含流程外阶段与未到期访视)',
                    'GET  /api/platform/cohorts': '随访平台 M18: 纳排方案与分组 (?code=&withLog=1)',
                    'POST /api/platform/cohort': '随访平台 M18: 建/改纳排方案 ({code,name,include_rule,exclude_rule})',
                    'POST /api/platform/group': '随访平台 M18: 建/改分组 ({cohort_code,code,name,kind,match_rule,priority})',
                    'GET  /api/platform/cohort/evaluate': '随访平台 M18: 试算谁符合纳排、各落哪个组 (只算不写)',
                    'POST /api/platform/cohort/enroll': '随访平台 M18: 执行入组 (dry_run 默认 true; 既有患者默认不改组)',
                    'GET  /api/platform/enrollments': '随访平台 M18: 入组名单 (?cohort=&group=&status=)',
                    'POST /api/platform/enrollment/transition': '随访平台 M18: 手工改组/筛除/退出 ({patient_no,action,reason})',
                    'GET  /api/platform/documents': '随访平台 M17: 项目资料列表 (?type=consent|protocol|ethics|sop|guideline)',
                    'POST /api/platform/document': '随访平台 M17: 上传资料 ({title, doc_type, filename, content_base64}); 同 code 再传=新版本',
                    'GET  /api/platform/document/download': '随访平台 M17: 下载资料 (?code=&version=) —— 需 X-Platform-Token, 见代码注释',
                    'POST /api/platform/consent': '随访平台 M17: 记录知情同意签署(签署留痕, 非可靠电子签名)',
                    'GET  /api/platform/consents': '随访平台 M17: 签署记录 (?patientNo=&code=); 会标出文件被换过的记录',
                    'POST /api/platform/consent/revoke': '随访平台 M17: 撤回签署 ({id, reason})',
                    'GET  /api/platform/search/fields': '随访平台 M16: 可用检索字段与统计维度(前端据此建条件, 不自己硬编码)',
                    'POST /api/platform/search': '随访平台 M16: 受试者高级检索 ({conditions:{op:and,children:[...]}})',
                    'POST /api/platform/stats': '随访平台 M16: 对检索结果做分布统计 ({conditions?, dims:[...]})',
                    'GET  /api/platform/edu': '随访平台 M15: 宣教材料库 (?status=&category=&topic=&q=)',
                    'POST /api/platform/edu/material': '随访平台 M15: 建/改宣教材料 (status 只能 draft/reviewing)',
                    'POST /api/platform/edu/transition': '随访平台 M15: 提交/发布/退回/归档 ({id, action, operator})',
                    'POST /api/platform/edu/generate': '随访平台 M15: 生成宣教草稿 (产出恒为 draft, 必须人工审核发布)',
                    'POST /api/platform/edu/scan': '随访平台 M15: 内容体检 ({title?, body}) —— 标出剂量/用药调整/劝阻就医等高危表述',
                    'GET  /api/platform/crfs': '随访平台 M14: CRF 列表 (?code=&scope=private|shared&allVersions=1)',
                    'POST /api/platform/crf': '随访平台 M14: 建/改 CRF (破坏性改动且已有填报时自动开新版)',
                    'POST /api/platform/crf/copy': '随访平台 M14: 拷贝 CRF ({code, new_code})',
                    'POST /api/platform/crf/preview': '随访平台 M14: 实时预览 —— 跑逻辑+校验不落库',
                    'POST /api/platform/crf/diff': '随访平台 M14: 比对两版定义, 标出破坏性/安全改动',
                    'POST /api/platform/crf/generate': '随访平台 M14: AI 生成 CRF 草稿 ({disease, visit_type, fields[]})',
                    'POST /api/platform/crf/parse-excel': '随访平台 M14: Excel -> CRF 草稿 ({xlsx_base64})',
                    'POST /api/platform/crf/response': '随访平台 M14: 提交 CRF 填报',
                    'GET  /api/platform/crf/responses': '随访平台 M14: CRF 填报记录',
                    'POST /api/platform/qc/run': '随访平台 M13: 跑自动质控 ({patient_no?, scale_code?})',
                    'GET  /api/platform/qc/findings': '随访平台 M13: 质控发现 (?status=&severity=block|warn&patientNo=)',
                    'GET  /api/platform/qc/compare': '随访平台 M13: 历次对比明细 (?patientNo=&code=), 逐题标出变化',
                    'POST /api/platform/qc/query': '随访平台 M13: 提质疑 ({target_kind, target_id, question, finding_id?})',
                    'GET  /api/platform/qc/queries': '随访平台 M13: 质疑单 (?status=&withLog=1&id=)',
                    'POST /api/platform/qc/query/transition': '随访平台 M13: 推进质疑单 ({query_id, action: answer|close|reopen, remark})',
                    'GET  /api/platform/discover': '随访平台 M8/M9: 未建档门诊号 + 纳排筛选 (?minRecords=&minVitals=&since=&requireVitals=1&include=pending|excluded|all; 只读, 无需 token)',
                    'GET  /api/platform/patients': '随访平台 M1/M4/M5: 患者列表 + 绑定态 + 最近上传时间 + 未关闭报警数 + wear_rate_7d + task_due_count',
                    'POST /api/platform/patient': '随访平台 M1: UPSERT platform_patient (建档/改档)',
                    'POST /api/platform/bind': "随访平台 M1: 绑定/解绑 {patient_no, chain:'iwown'|'zhenmaiyi', key, unbind}",
                    'GET  /api/platform/patient/vitals?patientNo=&days=': '随访平台 M1: 单患者跨链路体征日聚合',
                    'GET  /api/platform/alarms?status=&patientNo=&limit=': '随访平台 M2: 报警工作台列表 (status 支持 open meta 值)',
                    'POST /api/platform/alarm/ingest': '随访平台 M2: 扫 iwown_data alarm 行 -> platform_alarm (幂等; 也被自动摄入线程定时调用)',
                    'POST /api/platform/alarm/vital-ingest': '随访平台 M7: S101 体征阈值/趋势判定 + 脉诊仪新报告 -> platform_alarm (幂等; {days:1..365}, 自动摄入线程按 days=1 定时调用)',
                    'POST /api/platform/alarm/transition': "随访平台 M2: 报警状态流转 {alarm_id, action:'ack'|'call'|'visit'|'note'|'close', result_text, operator}",
                    'GET  /api/platform/compliance?patientNo=&days=': '随访平台 M4: 单患者每日佩戴率 + 未佩戴报警标注',
                    'GET  /api/platform/plans?patientNo=&active=': '随访平台 M5: 随访计划列表',
                    'POST /api/platform/plan': "随访平台 M5: 建/改随访计划 {id?, patient_no, name, frequency_days|null, next_due, active?, note?}",
                    'GET  /api/platform/tasks?horizon_days=7': '随访平台 M5: 今日待办任务 (从计划现算, overdue 在前)',
                    'POST /api/platform/task/complete': "随访平台 M5: 完成任务 {plan_id, method:'call'|'visit'|'note', result_text, operator}",
                    'GET  /api/platform/export?kind=&patientNo=&days=': '随访平台 M6: 队列数据导出 (kind=patients/vitals/alarms/followups/plans 出 CSV, kind=all 出 zip; 需 X-Platform-Token)',
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

            elif pathname == '/api/platform/patients/batch':
                if not check_platform_token(self):
                    return
                result, err = platform_batch_enroll(body)
                if err:
                    self._send_json(400, {'ok': False, 'error': err})
                else:
                    self._send_json(200, result)

            elif pathname == '/api/platform/scale':
                if not check_platform_token(self):
                    return
                result, err = upsert_platform_scale(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/scale/score':
                # 试评分: 只算不落库, 供填报页实时出分与预览管理用, 因此不走写接口门禁
                definition = body.get('definition')
                if not definition and body.get('scale_code'):
                    q, e = query_platform_scales(code=str(body['scale_code']))
                    if e or not q['scales']:
                        self._send_json(404, {'ok': False, 'error': e or '量表不存在'}); return
                    definition = q['scales'][0]['definition']
                if not isinstance(definition, dict):
                    self._send_json(400, {'ok': False, 'error': '需要 definition 或 scale_code'}); return
                result, errors = score_scale(definition, body.get('answers') or {})
                self._send_json(200, {'ok': True, 'result': result, 'errors': errors})

            elif pathname == '/api/platform/study':
                result, err = upsert_study(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else
                                dict(result, ok=True))

            elif pathname == '/api/platform/study/transition':
                result, err = study_transition(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/version/rollback':
                result, err = rollback_version(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/ocr/recognize':
                if not check_platform_token(self):
                    return
                result, err = ocr_recognize(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/ocr/verify':
                if not check_platform_token(self):
                    return
                result, err = ocr_verify_field(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/ocr/commit':
                if not check_platform_token(self):
                    return
                result, err = ocr_commit(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/ocr/abandon':
                if not check_platform_token(self):
                    return
                result, err = ocr_abandon(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/screen/task':
                result, err = upsert_screen_task(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else
                                dict(result, ok=True))

            elif pathname == '/api/platform/screen/task/transition':
                result, err = screen_task_transition(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/screen/link':
                result, err = create_screen_link(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/screen/submit':
                # **公开接口**(患者提交)。落待审区, 不直接建档 —— 陌生人填的东西
                # 直接进患者库, 等于把名册写权限交给任何拿到链接的人。
                result, err = screen_submit_public(
                    body, self.client_address[0] if self.client_address else None,
                    self.headers.get('User-Agent'))
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/screen/submission/review':
                result, err = screen_submission_review(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/consult':
                # 患者提问入口。刻意不走写接口门禁 —— 这个接口将来要开给患者端小程序,
                # 而患者手里不该有平台口令。风险由分诊+内容体检+留痕三道控制, 不靠口令。
                result, err = consult_ask(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/consult/triage':
                # 只跑分诊不调模型 —— 给别处(语音随访/问诊)复用同一套急症判定
                if not body.get('text'):
                    self._send_json(400, {'ok': False, 'error': '需要 text'}); return
                self._send_json(200, dict(triage_message(str(body['text'])), ok=True))

            elif pathname == '/api/platform/consult/review':
                result, err = consult_review(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/export/job':
                result, err = export_job_create(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/export/preview':
                # 变量挑选的即时预览(不落任务, 不写文件), 供确认变量选对了没
                data, meta, err = export_variable_pick(body)
                if err:
                    self._send_json(400, {'ok': False, 'error': err}); return
                self._send_json(200, dict(meta, ok=True,
                                          preview=data.decode('utf-8-sig').split('\r\n')[:8]))

            elif pathname == '/api/platform/visit/followup':
                result, err = visit_batch_followup(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/push':
                result, err = push_create(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/push/from-visit':
                result, err = push_from_visit(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/flow':
                result, err = upsert_flow(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else
                                dict(result, ok=True))

            elif pathname == '/api/platform/flow/copy':
                result, err = copy_flow(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else
                                dict(result, ok=True))

            elif pathname == '/api/platform/flow/instantiate':
                result, err = flow_instantiate(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/flow/refresh':
                result, err = flow_refresh_status(body.get('patient_no'))
                self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/flow/end':
                result, err = flow_end(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/visit/transition':
                result, err = visit_transition(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/visit/offschedule':
                result, err = trigger_offschedule(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/cohort':
                result, err = upsert_cohort(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else
                                dict(result, ok=True))

            elif pathname == '/api/platform/group':
                result, err = upsert_group(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else
                                dict(result, ok=True))

            elif pathname == '/api/platform/cohort/enroll':
                result, err = cohort_enroll(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/enrollment/transition':
                result, err = enrollment_transition(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/document':
                result, err = upload_document(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else
                                dict(result, ok=True))

            elif pathname == '/api/platform/consent':
                result, err = sign_consent(
                    body, source_ip=self.client_address[0] if self.client_address else None,
                    user_agent=self.headers.get('User-Agent'))
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/consent/revoke':
                result, err = revoke_consent(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/search':
                # 只读检索, 但用 POST: 条件树放不进查询串, 而且门诊号不该出现在
                # 访问日志和浏览器历史里
                result, err = platform_search(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/stats':
                result, err = platform_stats(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/edu/material':
                result, err = upsert_edu_material(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else
                                dict(result, ok=True))

            elif pathname == '/api/platform/edu/transition':
                result, err = edu_transition(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/edu/generate':
                spec = body if isinstance(body, dict) else {}
                if not (spec.get('disease') or spec.get('topic')):
                    self._send_json(400, {'ok': False, 'error': '至少要给 disease(病种) 或 topic(主题)'}); return
                draft, report = generate_edu_draft(spec)
                self._send_json(200, {'ok': True, 'draft': draft, 'report': report})

            elif pathname == '/api/platform/edu/scan':
                if not body.get('body'):
                    self._send_json(400, {'ok': False, 'error': '需要 body(正文)'}); return
                f = scan_edu_content(str(body['body']), body.get('title') or '')
                self._send_json(200, {'ok': True, 'findings': f,
                                      'blocking': sum(1 for x in f if x['level'] == 'block'),
                                      'rules': [{'code': c, 'level': l, 'why': w}
                                                for c, l, _, w, _n in EDU_CONTENT_RULES]})

            elif pathname == '/api/platform/crf':
                result, err = upsert_platform_crf(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else
                                dict(result, ok=True))

            elif pathname == '/api/platform/crf/copy':
                result, err = copy_platform_crf(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else
                                dict(result, ok=True))

            elif pathname == '/api/platform/crf/response':
                result, err = submit_crf_response(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/crf/preview':
                # 实时预览 (§2.1(3)): 跑逻辑 + 校验但**不落库**, 供填写过程中逐步反馈。
                # 不走写接口门禁, 与 /scale/score 同理。
                definition = body.get('definition')
                if not isinstance(definition, dict):
                    code = body.get('crf_code') or body.get('code')
                    if not code:
                        self._send_json(400, {'ok': False, 'error': '需要 definition 或 crf_code'}); return
                    q, e = query_platform_crfs(code=code, version=body.get('crf_version'))
                    if e or not q['crfs']:
                        self._send_json(404, {'ok': False, 'error': e or 'CRF 不存在'}); return
                    definition = q['crfs'][0]['definition']
                errs = validate_crf_definition(definition)
                if errs:
                    self._send_json(400, {'ok': False, 'error': 'CRF 定义有问题', 'validation': errs}); return
                state = eval_crf_logic(definition, body.get('data') or {})
                errors, warnings = validate_crf_data(definition, body.get('data') or {}, state)
                self._send_json(200, {'ok': True, 'state': state, 'errors': errors,
                                      'warnings': warnings,
                                      'advisories': lint_crf_definition(definition)})

            elif pathname == '/api/platform/crf/diff':
                # §2.1(3) 改表前先看会不会伤到已有数据
                a, bdef = body.get('old'), body.get('new')
                if not isinstance(a, dict) or not isinstance(bdef, dict):
                    self._send_json(400, {'ok': False, 'error': '需要 old 和 new 两份 definition'}); return
                self._send_json(200, dict(classify_crf_change(a, bdef), ok=True))

            elif pathname == '/api/platform/crf/generate':
                spec = body if isinstance(body, dict) else {}
                if not (spec.get('disease') or spec.get('fields')):
                    self._send_json(400, {'ok': False,
                                          'error': '至少要给 disease(病种) 或 fields(采集字段)'}); return
                if spec.get('fields') is not None and not isinstance(spec['fields'], list):
                    self._send_json(400, {'ok': False, 'error': 'fields 必须是数组'}); return
                if isinstance(spec.get('fields'), list) and len(spec['fields']) > 80:
                    self._send_json(400, {'ok': False, 'error': 'fields 最多 80 个'}); return
                draft, report = generate_crf_draft(spec)
                self._send_json(200, {'ok': True, 'draft': draft, 'report': report})

            elif pathname == '/api/platform/crf/parse-excel':
                if not body.get('xlsx_base64'):
                    self._send_json(400, {'ok': False, 'error': '需要 xlsx_base64'}); return
                try:
                    import base64 as _b64
                    xb = _b64.b64decode(body['xlsx_base64'])
                except Exception:
                    self._send_json(400, {'ok': False, 'error': 'xlsx_base64 不是合法 base64'}); return
                if len(xb) > 20 * 1024 * 1024:
                    self._send_json(400, {'ok': False, 'error': 'Excel 超过 20MB'}); return
                draft, report, err = parse_excel_to_crf(xb, body.get('code'), body.get('name'))
                if err:
                    self._send_json(400, {'ok': False, 'error': err}); return
                self._send_json(200, {'ok': True, 'draft': draft, 'report': report})

            elif pathname == '/api/platform/qc/run':
                result, err = platform_qc_run(
                    patient_no=body.get('patient_no') or body.get('patientNo'),
                    scale_code=body.get('scale_code') or body.get('code'),
                    limit=min(int(body.get('limit') or 500), 2000))
                self._send_json(500 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/qc/query':
                result, err = platform_qc_query_raise(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/qc/query/transition':
                result, err = platform_qc_query_transition(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/scale/generate':
                # 只生成不落库, 产出交人工审核 —— 与 /scale/parse 同样不走写接口门禁
                spec = body if isinstance(body, dict) else {}
                if not (spec.get('goal') or spec.get('dimensions')):
                    self._send_json(400, {'ok': False,
                                          'error': '至少要给 goal(评估目标) 或 dimensions(评估维度)'})
                    return
                dims = spec.get('dimensions')
                if dims is not None and not isinstance(dims, list):
                    self._send_json(400, {'ok': False, 'error': 'dimensions 必须是数组'}); return
                if isinstance(dims, list) and len(dims) > 20:
                    self._send_json(400, {'ok': False, 'error': 'dimensions 最多 20 个'}); return
                draft, report = generate_scale_draft(spec)
                self._send_json(200, {'ok': True, 'draft': draft, 'report': report})

            elif pathname == '/api/platform/scale/parse':
                # 只解析不落库, 产出交人工审核 —— 因此不走写接口门禁
                text = body.get('text')
                meta = None
                if not text and body.get('pdf_base64'):
                    try:
                        import base64 as _b64
                        pdf_bytes = _b64.b64decode(body['pdf_base64'])
                    except Exception:
                        self._send_json(400, {'ok': False, 'error': 'pdf_base64 不是合法 base64'}); return
                    if len(pdf_bytes) > 20 * 1024 * 1024:
                        self._send_json(400, {'ok': False, 'error': 'PDF 超过 20MB'}); return
                    text, meta, err = extract_pdf_text(pdf_bytes)
                    if err:
                        self._send_json(400, {'ok': False, 'error': err, 'meta': meta}); return
                if not text or not str(text).strip():
                    self._send_json(400, {'ok': False, 'error': '需要 text 或 pdf_base64'}); return
                draft, report = parse_scale_text(str(text), body.get('code'), body.get('name'))
                self._send_json(200, {'ok': True, 'draft': draft, 'report': report,
                                      'pdf': meta, 'source_text': str(text)[:20000],
                                      # 有页读不出来时把话说到最外层。埋在 meta 里
                                      # 前端很可能不看, 而"少了半份量表"是看不出来的
                                      'warning': (meta or {}).get('warning')})

            elif pathname == '/api/platform/scale/response':
                if not check_platform_token(self):
                    return
                result, err = submit_scale_response(body)
                self._send_json(400 if err else 200, {'ok': False, 'error': err} if err else result)

            elif pathname == '/api/platform/screening':
                if not check_platform_token(self):
                    return
                result, err = platform_screening_mark(body)
                if err:
                    self._send_json(400, {'ok': False, 'error': err})
                else:
                    self._send_json(200, result)

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

            elif pathname == '/api/platform/alarm/vital-ingest':
                if not check_platform_token(self):
                    return
                # days: 判定窗口。自动摄入线程走 days=1 只判当天; 这个端点给的是补历史/演示的
                # 手动入口(如首次上线时 days=30 把近一个月的越限与趋势异常一次性判出来)。
                # 上限 365: 再大就该走离线批处理, 不该占着一个 HTTP 请求全表扫。
                try:
                    days = int(body.get('days') or 7)
                except (TypeError, ValueError):
                    self._send_json(400, {'ok': False, 'error': 'days 必须是整数'})
                    return
                if not 1 <= days <= 365:
                    self._send_json(400, {'ok': False, 'error': 'days 必须在 1..365 之间'})
                    return
                result, err = platform_vital_alarm_ingest(days=days)
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

            elif pathname == '/api/platform/plan':
                if not check_platform_token(self):
                    return
                result, err = upsert_platform_plan(body)
                if err:
                    self._send_json(400, {'ok': False, 'error': err})
                else:
                    self._send_json(200, {'ok': True, **result})

            elif pathname == '/api/platform/task/complete':
                if not check_platform_token(self):
                    return
                result, err = platform_task_complete(body)
                if err:
                    self._send_json(400, {'ok': False, 'error': err})
                else:
                    self._send_json(200, {'ok': True, **result})

            else:
                self._send_json(404, {'error': 'Not found. Available: POST /api/health-data, POST /api/device/register, POST /api/device/merge, POST /api/zhenmaiyi/upload, POST /api/platform/patient, POST /api/platform/bind, POST /api/platform/alarm/ingest, POST /api/platform/alarm/transition, POST /api/platform/plan, POST /api/platform/task/complete'})

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
        # 随访平台 1.0 M1 + 1.1 M5: 启动时确保 platform_patient/alarm/followup_log/plan 4 张表存在 (idempotent)
        ensure_platform_tables()
        # M5 性能修复: 确保 platform_followup_log.idx_plan 索引存在 (idempotent, 老库需要 ALTER 补齐)
        ensure_followup_log_plan_index()
        # M7: 确保 platform_alarm 有 source_chain/dedup_key 两列 (idempotent, 老库需要 ALTER 补齐)
        ensure_platform_alarm_m7_columns()
        # M9: 筛查排除名单表 (idempotent)
        ensure_platform_screening_table()
        # M10: 量表定义 + 填报记录 (idempotent)
        ensure_platform_scale_tables()
        # M13: 质控发现 + 质疑单 + 流转留痕 (idempotent)
        ensure_platform_qc_tables()
        # M14: CRF 定义 + 填报 (idempotent)
        ensure_platform_crf_tables()
        # M15: 宣教材料 + 流转留痕 (idempotent)
        ensure_platform_edu_tables()
        # M16: 体征日聚合派生表 (idempotent)
        ensure_platform_vital_daily()
        # M17: 项目资料 + 知情签署 + 留痕 (idempotent)
        ensure_platform_doc_tables()
        # M18: 纳排方案 + 分组 + 入组归属 (idempotent)
        ensure_platform_cohort_tables()
        # M19: 随访流程 + 实例 + 访视 (idempotent)
        ensure_platform_flow_tables()
        # M20: 访视跟进 + 推送队列 (idempotent)
        ensure_platform_push_tables()
        # M21: 导出任务 + 下载留痕 (idempotent)
        ensure_platform_export_tables()
        # M22: 健康咨询留痕 (idempotent)
        ensure_platform_consult_tables()
        # M23: 筛查任务 + 自助链接 + 提交 (idempotent)
        ensure_platform_screen_tables()
        # M24: 研究数据库状态 + 版本留痕 (idempotent)
        ensure_platform_study_tables()
        # M25: OCR 识别任务 + 待核字段 + 留痕 (idempotent)
        ensure_platform_ocr_tables()
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

    # 随访平台 1.0 M4: 报警自动摄入线程. PLATFORM_INGEST_INTERVAL_MIN 未设时默认 10 分钟一次,
    # 设为 '0' 关闭(退回纯手动点"拉取新报警"按钮); 解析失败(非数字)也按默认 10 处理。
    try:
        _ingest_interval_min = int(os.environ.get('PLATFORM_INGEST_INTERVAL_MIN') or '10')
    except (TypeError, ValueError):
        _ingest_interval_min = 10
    if _ingest_interval_min > 0:
        threading.Thread(target=platform_auto_ingest_loop, args=(_ingest_interval_min,), daemon=True).start()
        print('[启动] 报警自动摄入线程已启动, 每 {} 分钟调用一次 platform_alarm_ingest() '
              '(PLATFORM_INGEST_INTERVAL_MIN 调整间隔 / 设为 0 关闭)'.format(_ingest_interval_min))
    else:
        print('[启动] 报警自动摄入线程已禁用 (PLATFORM_INGEST_INTERVAL_MIN=0), 只能手动 POST /api/platform/alarm/ingest')

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
    print('[端点] GET  /api/platform/discover              随访平台 M8/M9: 待建档门诊号 + 纳排筛选')
    print('[端点] GET  /api/platform/scales                 随访平台 M10: 量表库')
    print('[端点] POST /api/platform/scale                  随访平台 M10: 导入量表')
    print('[端点] POST /api/platform/scale/score            随访平台 M10: 试评分(不落库)')
    print('[端点] POST /api/platform/scale/response         随访平台 M10: 提交填报')
    print('[端点] GET  /api/platform/scale/responses        随访平台 M10: 填报记录')
    print('[端点] POST /api/platform/scale/parse            随访平台 M11: 文档解析成量表草稿')
    print('[端点] POST /api/platform/scale/generate         随访平台 M12: AI 量表生成')
    print('[端点] GET  /api/platform/studies                  随访平台 M24: 研究数据库状态')
    print('[端点] POST /api/platform/study/transition         随访平台 M24: 启用/重置/结束/删除')
    print('[端点] POST /api/platform/version/rollback         随访平台 M24: 版本回滚(不删旧版)')
    print('[端点] GET  /api/platform/screen/tasks             随访平台 M23: 筛查任务')
    print('[端点] POST /api/platform/screen/link              随访平台 M23: 生成自助填报链接')
    print('[端点] GET  /api/platform/screen/form              随访平台 M23 公开: 取表单(不返回患者数据)')
    print('[端点] POST /api/platform/screen/submit            随访平台 M23 公开: 提交(落待审区)')
    print('[端点] POST /api/platform/consult                  随访平台 M22: 健康咨询(分诊前置)')
    print('[端点] POST /api/platform/consult/triage           随访平台 M22: 高风险分诊')
    print('[端点] GET  /api/platform/consults                 随访平台 M22: 咨询留痕')
    print('[端点] POST /api/platform/export/job               随访平台 M21: 建导出任务')
    print('[端点] GET  /api/platform/exports                  随访平台 M21: 导出记录')
    print('[端点] GET  /api/platform/export/download          随访平台 M21: 下载导出件(需口令)')
    print('[端点] GET  /api/platform/visit/summary            随访平台 M20: 当日/超窗统计')
    print('[端点] POST /api/platform/visit/followup           随访平台 M20: 批量跟进')
    print('[端点] POST /api/platform/push                     随访平台 M20: 建推送(通道未接, 只入队)')
    print('[端点] GET  /api/platform/pushes                   随访平台 M20: 推送队列')
    print('[端点] GET  /api/platform/flows                    随访平台 M19: 流程库')
    print('[端点] POST /api/platform/flow                     随访平台 M19: 建/改流程模板')
    print('[端点] POST /api/platform/flow/instantiate         随访平台 M19: 实例化到患者')
    print('[端点] GET  /api/platform/visits                   随访平台 M19: 访视列表')
    print('[端点] POST /api/platform/visit/transition         随访平台 M19: 完成/跳过访视')
    print('[端点] POST /api/platform/visit/offschedule        随访平台 M19: 事件触发流程外阶段')
    print('[端点] GET  /api/platform/flow/completion          随访平台 M19: 随访完成率')
    print('[端点] GET  /api/platform/cohorts                  随访平台 M18: 纳排方案与分组')
    print('[端点] POST /api/platform/cohort                   随访平台 M18: 建/改纳排方案')
    print('[端点] POST /api/platform/group                    随访平台 M18: 建/改分组')
    print('[端点] GET  /api/platform/cohort/evaluate          随访平台 M18: 入组试算(只算不写)')
    print('[端点] POST /api/platform/cohort/enroll            随访平台 M18: 执行入组')
    print('[端点] GET  /api/platform/enrollments              随访平台 M18: 入组名单')
    print('[端点] POST /api/platform/enrollment/transition    随访平台 M18: 手工改组/筛除/退出')
    print('[端点] GET  /api/platform/documents                随访平台 M17: 项目资料列表')
    print('[端点] POST /api/platform/document                 随访平台 M17: 上传资料(同 code 再传=新版本)')
    print('[端点] GET  /api/platform/document/download        随访平台 M17: 下载资料(需口令)')
    print('[端点] POST /api/platform/consent                  随访平台 M17: 知情同意签署留痕')
    print('[端点] GET  /api/platform/consents                 随访平台 M17: 签署记录')
    print('[端点] POST /api/platform/consent/revoke           随访平台 M17: 撤回签署')
    print('[端点] GET  /api/platform/search/fields           随访平台 M16: 可用检索字段')
    print('[端点] POST /api/platform/search                  随访平台 M16: 受试者高级检索')
    print('[端点] POST /api/platform/stats                   随访平台 M16: 分布统计')
    print('[端点] GET  /api/platform/edu                     随访平台 M15: 宣教材料库')
    print('[端点] POST /api/platform/edu/material            随访平台 M15: 建/改宣教材料')
    print('[端点] POST /api/platform/edu/transition          随访平台 M15: 提交/发布/退回/归档')
    print('[端点] POST /api/platform/edu/generate            随访平台 M15: 生成宣教草稿')
    print('[端点] POST /api/platform/edu/scan                随访平台 M15: 内容体检')
    print('[端点] GET  /api/platform/ocr/status              随访平台 M25: OCR 引擎可用性')
    print('[端点] POST /api/platform/ocr/recognize           随访平台 M25: 拍照识别 -> 待核清单(不入库)')
    print('[端点] GET  /api/platform/ocr/job                  随访平台 M25: 待核清单(需口令)')
    print('[端点] GET  /api/platform/ocr/crop                 随访平台 M25: 原件裁图(需口令)')
    print('[端点] POST /api/platform/ocr/verify               随访平台 M25: 逐字段人工核对')
    print('[端点] POST /api/platform/ocr/commit               随访平台 M25: 核完写进 CRF')
    print('[端点] GET  /api/platform/crfs                    随访平台 M14: CRF 列表')
    print('[端点] POST /api/platform/crf                     随访平台 M14: 建/改 CRF (自动版本管理)')
    print('[端点] POST /api/platform/crf/copy                随访平台 M14: 拷贝 CRF')
    print('[端点] POST /api/platform/crf/preview             随访平台 M14: 实时预览(逻辑+校验, 不落库)')
    print('[端点] POST /api/platform/crf/diff                随访平台 M14: 改动影响比对')
    print('[端点] POST /api/platform/crf/generate            随访平台 M14: AI 生成 CRF 草稿')
    print('[端点] POST /api/platform/crf/parse-excel         随访平台 M14: Excel -> CRF 草稿')
    print('[端点] POST /api/platform/crf/response            随访平台 M14: 提交 CRF 填报')
    print('[端点] GET  /api/platform/crf/responses           随访平台 M14: CRF 填报记录')
    print('[端点] POST /api/platform/qc/run                  随访平台 M13: 跑自动质控')
    print('[端点] GET  /api/platform/qc/findings             随访平台 M13: 质控发现列表')
    print('[端点] GET  /api/platform/qc/compare              随访平台 M13: 历次对比明细')
    print('[端点] POST /api/platform/qc/query                随访平台 M13: 提质疑')
    print('[端点] GET  /api/platform/qc/queries              随访平台 M13: 质疑单列表')
    print('[端点] POST /api/platform/qc/query/transition     随访平台 M13: 推进质疑单')
    print('[端点] POST /api/platform/patients/batch        随访平台 M9: 批量建档')
    print('[端点] POST /api/platform/screening             随访平台 M9: 筛查排除/撤销')
    print('[端点] POST /api/platform/patient                随访平台 M1: UPSERT 患者建档')
    print('[端点] POST /api/platform/bind                   随访平台 M1: 绑定/解绑 iwown|zhenmaiyi')
    print('[端点] GET  /api/platform/patient/vitals         随访平台 M1: 单患者跨链路体征日聚合')
    print('[端点] POST /api/platform/alarm/ingest            随访平台 M2: iwown 报警行 -> platform_alarm (幂等)')
    print('[端点] POST /api/platform/alarm/vital-ingest      随访平台 M7: S101 阈值/趋势 + 脉诊仪报告 -> platform_alarm (幂等)')
    print('[端点] GET  /api/platform/alarms                  随访平台 M2: 报警工作台列表')
    print('[端点] POST /api/platform/alarm/transition        随访平台 M2: 报警状态流转 (state machine)')
    print('[端点] GET  /api/platform/compliance              随访平台 M4: 单患者每日佩戴率')
    print('[端点] GET  /api/platform/plans                   随访平台 M5: 随访计划列表')
    print('[端点] POST /api/platform/plan                    随访平台 M5: 建/改随访计划')
    print('[端点] GET  /api/platform/tasks                   随访平台 M5: 今日待办任务 (从计划现算)')
    print('[端点] POST /api/platform/task/complete           随访平台 M5: 完成任务 (推进/停用计划)')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n[停止] 服务已关闭')
        server.server_close()
