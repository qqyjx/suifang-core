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

    用 pdf-inspector (纯 Rust, 本地跑, 无网络无 key)。它明确**不做 OCR**: 扫描件只能被
    识别出"是扫描件", 读不出字。这里如实把分类回给前端, 而不是返回空文本让人以为解析失败。
    未安装时不让整个服务起不来 —— 只有用到这个端点才报错。
    """
    try:
        import pdf_inspector
    except ImportError:
        return None, None, ('未安装 pdf-inspector, 无法解析 PDF。'
                            '在服务器上执行: /root/miniconda3/bin/pip install pdf-inspector')
    try:
        r = pdf_inspector.process_pdf_bytes(pdf_bytes)
        kind = getattr(r, 'pdf_type', None)
        md = getattr(r, 'markdown', None) or ''
        meta = {'pdf_type': kind, 'chars': len(md)}
        if not md.strip():
            if kind in ('scanned', 'image_based'):
                return None, meta, ('这份 PDF 是{}, 里面没有可提取的文字层。'
                                    'pdf-inspector 不做 OCR, 需要先用 OCR 引擎转成文字再粘贴进来。'
                                    .format('扫描件' if kind == 'scanned' else '纯图片'))
            return None, meta, 'PDF 里没有提取到文字 (分类: {})'.format(kind)
        return md, meta, None
    except Exception as e:
        traceback.print_exc()
        return None, None, 'PDF 解析失败: {}'.format(e)


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
            # token 门禁 —— 这是平台上唯一一个需要鉴权的 GET。
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
                                      'pdf': meta, 'source_text': str(text)[:20000]})

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
    print('[端点] GET  /api/platform/search/fields           随访平台 M16: 可用检索字段')
    print('[端点] POST /api/platform/search                  随访平台 M16: 受试者高级检索')
    print('[端点] POST /api/platform/stats                   随访平台 M16: 分布统计')
    print('[端点] GET  /api/platform/edu                     随访平台 M15: 宣教材料库')
    print('[端点] POST /api/platform/edu/material            随访平台 M15: 建/改宣教材料')
    print('[端点] POST /api/platform/edu/transition          随访平台 M15: 提交/发布/退回/归档')
    print('[端点] POST /api/platform/edu/generate            随访平台 M15: 生成宣教草稿')
    print('[端点] POST /api/platform/edu/scan                随访平台 M15: 内容体检')
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
