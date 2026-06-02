# -*- coding: utf-8 -*-
"""
iwown 4G 智能手表数据接收服务
=============================

随访项目第 2 款手表 (iwown 4G)。设备自带蜂窝网络, 直接 HTTP POST
二进制 protobuf 到本服务 (无小程序桥)。本服务解帧 + 解 protobuf + 入库六元 MySQL。

与 S101 的 health_server.py 并列, 独立进程/端口:
  - S101:  health_server.py  :3000  收 JSON   -> dc.ncrc.org.cn/api2/
  - iwown: iwown_server.py    :8099  收二进制 -> dc.ncrc.org.cn/iwown/  (六元加 nginx location)

设备上行端点 (手表烧录地址只填前缀 https://dc.ncrc.org.cn/iwown, 不带 /pb/upload;
手表自动拼下列端点。多写 /pb/upload 会变 /iwown/pb/upload/pb/upload → 404):
  POST /pb/upload         二进制  必需  opt 0x80 健康 / 0x0A 实时
  POST /alarm/upload      二进制  必需  opt 0x12 报警
  POST /call_log/upload   JSON    必需  SOS+通话记录
  POST /deviceinfo/upload JSON    可选  设备信息
  POST /status/notify     JSON    可选  在线/离线
  GET  /health/sleep      ->JSON  可选  设备拉取睡眠结果
本服务自有:
  GET  /api/status              健康检查
  GET  /api/iwown/list          列最近数据 (给看板)

数据不丢三重保障:
  1. 收到字节先 append 到当日 fallback 文件 (raw hex), 任何后续失败都不丢原始数据
  2. protobuf 不可用/解码失败 -> 仍按 raw_hex 入库 (decoded_json=null), 可后续重处理
  3. DB 失败 -> 已在 fallback 文件, 仍回 0x00 ack 设备避免重传风暴 (错误进日志)

环境变量 (复用 S101 同一套六元 MySQL):
  IWOWN_PORT (默认 8099)
  SUIFANG_DB_HOST/PORT/USER/PASSWORD/NAME
  IWOWN_FALLBACK_DIR (默认 /opt/iwown/fallback)
"""
import os
import sys
import json
import datetime
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

try:
    import pymysql
    PYMYSQL_OK = True
except Exception as e:
    PYMYSQL_OK = False
    print('[警告] pymysql 不可用, 入库会失败 (仅 fallback 落盘):', e)

import iwown_parser

# ============ 配置 ============
PORT = int(os.environ.get('IWOWN_PORT', '8099'))
DB_CONFIG = {
    'host': os.environ.get('SUIFANG_DB_HOST', '192.168.4.174'),
    'port': int(os.environ.get('SUIFANG_DB_PORT', '3306')),
    'user': os.environ.get('SUIFANG_DB_USER', 'developer'),
    # 密码不硬编码进仓: 由 /opt/iwown/iwown.env (EnvironmentFile) 注入 SUIFANG_DB_PASSWORD
    # 与 S101 六元库同一套凭据 (developer)。本地调试可临时 export。
    'password': os.environ.get('SUIFANG_DB_PASSWORD', ''),
    'database': os.environ.get('SUIFANG_DB_NAME', 'h6dp_suifang'),
    'charset': 'utf8mb4',
}
FALLBACK_DIR = os.environ.get('IWOWN_FALLBACK_DIR', '/opt/iwown/fallback')

# 抽取的体征列 (key_metrics -> 表列)
METRIC_COLS = ['hr_avg', 'hr_min', 'hr_max', 'spo2_avg', 'spo2_min', 'spo2_max',
               'sbp', 'dbp', 'temperature', 'pressure', 'step', 'distance',
               'calorie', 'battery', 'rssi']

# 事件类: 即使无体征也保留 (报警/通话/设备信息/在线状态)
EVENT_TYPES = ('alarm', 'calllog', 'deviceinfo', 'status')


def _is_meaningful(dtype, metrics):
    """是否值得入库: 事件类恒保留; 其余(health/realtime/index/unknown)仅当确有体征。
    手表每分钟生成一条记录, 未测量的那分钟只有时间戳没有体征 -> 视为空帧不入库。"""
    return dtype in EVENT_TYPES or bool(metrics)


def get_connection():
    return pymysql.connect(**DB_CONFIG)


def ensure_tables():
    """启动时幂等建表: iwown_device (设备->患者绑定) + iwown_data (上行数据)。"""
    if not PYMYSQL_OK:
        print('[启动] 跳过建表 (pymysql 不可用)')
        return
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS iwown_device (
                device_id  VARCHAR(32) PRIMARY KEY COMMENT '15字节设备ID(ASCII)',
                patient_no VARCHAR(64) DEFAULT NULL COMMENT '门诊号/患者标识(后台绑定)',
                note       VARCHAR(255) DEFAULT NULL,
                first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_seen  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='iwown 设备名册'
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS iwown_data (
                id          BIGINT AUTO_INCREMENT PRIMARY KEY,
                device_id   VARCHAR(32) NOT NULL,
                data_type   VARCHAR(16) NOT NULL COMMENT 'health/realtime/alarm/index/calllog/deviceinfo/status',
                opt         SMALLINT UNSIGNED DEFAULT NULL COMMENT '0x80/0x0A/0x12',
                recorded_at DATETIME DEFAULT NULL COMMENT '帧内测量时间',
                hr_avg SMALLINT DEFAULT NULL, hr_min SMALLINT DEFAULT NULL, hr_max SMALLINT DEFAULT NULL,
                spo2_avg SMALLINT DEFAULT NULL, spo2_min SMALLINT DEFAULT NULL, spo2_max SMALLINT DEFAULT NULL,
                sbp SMALLINT DEFAULT NULL, dbp SMALLINT DEFAULT NULL,
                temperature DECIMAL(5,2) DEFAULT NULL, pressure SMALLINT DEFAULT NULL,
                step INT DEFAULT NULL, distance DECIMAL(10,1) DEFAULT NULL, calorie DECIMAL(10,1) DEFAULT NULL,
                battery SMALLINT DEFAULT NULL, rssi SMALLINT DEFAULT NULL,
                decoded_json JSON DEFAULT NULL COMMENT 'MessageToDict 全量解码(无损)',
                raw_hex     MEDIUMTEXT COMMENT '原始帧hex(解码失败也保住)',
                uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_dev_time (device_id, recorded_at),
                INDEX idx_type (data_type),
                INDEX idx_uploaded (uploaded_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='iwown 4G 手表上行数据'
        """)
        conn.commit()
        cur.close()
        print('[启动] iwown_device / iwown_data 表已就绪')
    except Exception as e:
        print('[启动] 建表失败:', e)
    finally:
        conn.close()


def fallback_write(tag, device_id, payload_hex):
    """收到的原始字节先落盘, 任何后续失败都不丢数据。"""
    try:
        os.makedirs(FALLBACK_DIR, exist_ok=True)
        day = datetime.datetime.now().strftime('%Y-%m-%d')
        line = '{0}\t{1}\t{2}\t{3}\n'.format(
            datetime.datetime.now().isoformat(), tag, device_id or '-', payload_hex)
        with open(os.path.join(FALLBACK_DIR, 'iwown-%s.log' % day), 'a') as f:
            f.write(line)
    except Exception as e:
        print('[fallback] 写盘失败:', e)


def upsert_device(cur, device_id):
    cur.execute(
        "INSERT INTO iwown_device (device_id) VALUES (%s) "
        "ON DUPLICATE KEY UPDATE last_seen = CURRENT_TIMESTAMP", (device_id,))


def insert_rows(device_id, rows):
    """rows: list of (data_type, opt, recorded_at, decoded_dict, key_metrics, raw_hex)。
    返回 (inserted, err)。"""
    if not PYMYSQL_OK:
        return 0, 'pymysql 不可用'
    conn = get_connection()
    try:
        cur = conn.cursor()
        upsert_device(cur, device_id)
        cols = ['device_id', 'data_type', 'opt', 'recorded_at'] + METRIC_COLS + ['decoded_json', 'raw_hex']
        ph = ','.join(['%s'] * len(cols))
        sql = 'INSERT INTO iwown_data ({0}) VALUES ({1})'.format(','.join(cols), ph)
        n = 0
        for data_type, opt, rec, decoded, metrics, raw_hex in rows:
            vals = [device_id, data_type, opt, rec]
            for c in METRIC_COLS:
                vals.append(metrics.get(c) if metrics else None)
            vals.append(json.dumps(decoded, ensure_ascii=False) if decoded is not None else None)
            vals.append(raw_hex)
            cur.execute(sql, vals)
            n += 1
        conn.commit()
        cur.close()
        return n, None
    except Exception as e:
        traceback.print_exc()
        return 0, str(e)
    finally:
        conn.close()


def insert_json_row(device_id, data_type, obj):
    if not PYMYSQL_OK:
        return 0, 'pymysql 不可用'
    conn = get_connection()
    try:
        cur = conn.cursor()
        if device_id:
            upsert_device(cur, device_id)
        cur.execute(
            "INSERT INTO iwown_data (device_id, data_type, decoded_json) VALUES (%s,%s,%s)",
            (device_id or '', data_type, json.dumps(obj, ensure_ascii=False)))
        conn.commit()
        cur.close()
        return 1, None
    except Exception as e:
        traceback.print_exc()
        return 0, str(e)
    finally:
        conn.close()


class IwownHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):  # 静音默认访问日志
        pass

    def _read_body(self):
        n = int(self.headers.get('Content-Length', 0) or 0)
        return self.rfile.read(n) if n > 0 else b''

    def _send_byte(self, code):
        """二进制端点统一返回单字节 (0x00 成功 / 0x02 长度 / 0x03 帧头)。"""
        body = bytes([code])
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    # ---------- 二进制上行 ----------
    def _handle_binary(self, tag, accept_opts):
        body = self._read_body()
        device_id, frames, err = iwown_parser.split_frames(body)
        # 先整体落盘兜底
        fallback_write(tag, device_id, body.hex())
        if err is not None:
            print('[%s] device=%s 帧错误 code=0x%02x len=%d' % (tag, device_id, err, len(body)))
            return self._send_byte(err)
        rows = []
        crc_bad = sum(1 for fr in frames if not fr.get('crc_ok', True))
        if crc_bad:
            print('[%s] device=%s ⚠ CRC 不符 %d/%d 帧 (仍入库, 算法待真机确认)'
                  % (tag, device_id, crc_bad, len(frames)))
        skipped = 0
        for fr in frames:
            if accept_opts and fr['opt'] not in accept_opts:
                skipped += 1  # opt 不属于本端点; 原始已在 fallback, 不入库
                continue
            dtype, rec, decoded, metrics = iwown_parser.decode_frame(fr['opt'], fr['pb_bytes'])
            if not _is_meaningful(dtype, metrics):
                skipped += 1  # 无测量的空帧 / index 同步帧: 不入库 (原始已在 fallback)
                continue
            rows.append((dtype, fr['opt'], rec, decoded, metrics, fr['raw_hex']))
        inserted, derr = insert_rows(device_id, rows)
        if derr:
            print('[%s] device=%s 入库失败(已落盘): %s' % (tag, device_id, derr))
        else:
            print('[%s] device=%s 帧=%d 入库=%d 跳过空帧=%d' % (tag, device_id, len(frames), inserted, skipped))
        # 即便入库失败也回 0x00: 原始数据已在 fallback, 避免设备重传风暴
        return self._send_byte(0x00)

    # ---------- JSON 上行 ----------
    def _handle_json(self, data_type):
        body = self._read_body()
        try:
            obj = json.loads(body.decode('utf-8')) if body else {}
        except Exception:
            return self._send_json(200, {'ReturnCode': 10002})
        device_id = ''
        if isinstance(obj, dict):
            device_id = str(obj.get('deviceid') or obj.get('device_id') or obj.get('deviceId') or '')
        _, derr = insert_json_row(device_id, data_type, obj)
        if derr:
            fallback_write(data_type, device_id, body.hex())
            print('[%s] 入库失败(已落盘): %s' % (data_type, derr))
        return self._send_json(200, {'ReturnCode': 0})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path.endswith('/pb/upload'):
                return self._handle_binary('pb', {iwown_parser.OPT_HEALTH, iwown_parser.OPT_REALTIME})
            if path.endswith('/alarm/upload'):
                return self._handle_binary('alarm', {iwown_parser.OPT_ALARM})
            if path.endswith('/call_log/upload'):
                return self._handle_json('calllog')
            if path.endswith('/deviceinfo/upload'):
                return self._handle_json('deviceinfo')
            if path.endswith('/status/notify'):
                return self._handle_json('status')
            return self._send_json(404, {'error': 'unknown endpoint', 'path': path})
        except Exception as e:
            traceback.print_exc()
            # 二进制端点出错也尽量回 0x00 (数据已落盘); JSON 端点回错误码
            if '/upload' in path and ('pb' in path or 'alarm' in path):
                return self._send_byte(0x00)
            return self._send_json(500, {'error': str(e)})

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        q = parse_qs(parsed.query)
        if path == '/api/status':
            ok, info = self._db_ping()
            return self._send_json(200, {
                'status': 'running', 'service': 'iwown-4g-receiver',
                'port': PORT, 'protobuf': iwown_parser.PROTOBUF_OK,
                'protobuf_err': iwown_parser.PROTOBUF_ERR,
                'mysql': 'connected' if ok else 'error', 'mysql_info': info,
                'server_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            })
        if path == '/api/iwown/list':
            return self._iwown_list(q)
        if path.endswith('/health/sleep'):
            return self._health_sleep(q)
        return self._send_json(404, {'error': 'unknown endpoint', 'path': path})

    def _db_ping(self):
        if not PYMYSQL_OK:
            return False, 'pymysql 不可用'
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute('SELECT COUNT(*) FROM iwown_data')
            row = cur.fetchone()
            cur.close()
            conn.close()
            return True, {'iwown_data_rows': row[0] if row else 0}
        except Exception as e:
            return False, str(e)

    def _iwown_list(self, q):
        if not PYMYSQL_OK:
            return self._send_json(500, {'error': 'pymysql 不可用'})
        try:
            limit = max(1, min(500, int((q.get('limit') or ['100'])[0])))
        except (ValueError, TypeError):
            limit = 100
        try:
            offset = max(0, int((q.get('offset') or ['0'])[0]))
        except (ValueError, TypeError):
            offset = 0
        dtype = (q.get('type') or ['all'])[0]
        try:
            conn = get_connection()
            cur = conn.cursor()
            # 概况统计: 始终全量 (排除 index 同步帧), 不受分页/筛选影响
            cur.execute("SELECT data_type, COUNT(*) FROM iwown_data "
                        "WHERE data_type <> 'index' GROUP BY data_type")
            type_counts = {dt: n for dt, n in cur.fetchall()}
            total = sum(type_counts.values())
            cur.execute("SELECT COUNT(DISTINCT device_id) FROM iwown_data "
                        "WHERE data_type <> 'index'")
            device_count = cur.fetchone()[0]
            labels = self._device_labels(cur)   # IMEI -> 手表显示名
            # 当前页数据: 类型筛选 + 分页
            if dtype and dtype != 'all':
                where, tail, filtered_total = "data_type = %s", [dtype], type_counts.get(dtype, 0)
            else:
                where, tail, filtered_total = "data_type <> 'index'", [], total
            cur.execute(
                "SELECT device_id,data_type,opt,recorded_at,hr_avg,spo2_avg,sbp,dbp,"
                "temperature,pressure,step,battery,uploaded_at "
                "FROM iwown_data WHERE " + where + " "
                "ORDER BY id DESC LIMIT %s OFFSET %s", tail + [limit, offset])
            cols = [d[0] for d in cur.description]
            rows = []
            for r in cur.fetchall():
                d = dict(zip(cols, r))
                for k in ('recorded_at', 'uploaded_at'):
                    if d.get(k) is not None and hasattr(d[k], 'strftime'):
                        d[k] = d[k].strftime('%Y-%m-%d %H:%M:%S')
                if d.get('temperature') is not None:
                    d['temperature'] = float(d['temperature'])
                d['device_label'] = labels.get(d['device_id']) or d['device_id']
                rows.append(d)
            cur.close()
            conn.close()
            return self._send_json(200, {
                'count': len(rows), 'total': total, 'filtered_total': filtered_total,
                'offset': offset, 'limit': limit, 'type_counts': type_counts,
                'device_count': device_count, 'rows': rows})
        except Exception as e:
            return self._send_json(500, {'error': str(e)})

    def _device_labels(self, cur):
        """IMEI -> 手表显示名 (型号前缀-IMEI末4位)。前缀取该设备 deviceinfo.sn 前 6 位
        (如 'BP100C100260M26000090' -> 'BP100C'), 无 deviceinfo 时回退原 IMEI。"""
        labels = {}
        try:
            cur.execute("SELECT device_id, decoded_json FROM iwown_data "
                        "WHERE data_type='deviceinfo' AND decoded_json IS NOT NULL")
            for dev, dj in cur.fetchall():
                if not dev:
                    continue
                try:
                    info = json.loads(dj)
                except Exception:
                    info = {}
                sn = info.get('sn') or ''
                prefix = sn[:6] if sn else (info.get('model') or '')
                labels[dev] = ('%s-%s' % (prefix, dev[-4:])) if prefix else dev
        except Exception:
            pass
        return labels

    def _health_sleep(self, q):
        # 设备拉取睡眠结果。睡眠分期算法需 iwown 算法 API (calculation.html#id4), 暂未接入。
        device_id = (q.get('deviceid') or [''])[0]
        sleep_date = (q.get('sleep_date') or [''])[0]
        if not device_id or not sleep_date:
            return self._send_json(200, {'ReturnCode': 10002})
        # TODO: 接 iwown 睡眠算法 API 后返回真实结果; 当前返回 10404 表示暂无
        return self._send_json(200, {'ReturnCode': 10404})


def main():
    print('[启动] iwown 4G 接收服务 :%d' % PORT)
    print('[启动] protobuf 可用=%s%s' % (
        iwown_parser.PROTOBUF_OK,
        '' if iwown_parser.PROTOBUF_OK else ' (%s) — 仍可收数据+落盘, 解码降级' % iwown_parser.PROTOBUF_ERR))
    ensure_tables()
    httpd = ThreadingHTTPServer(('0.0.0.0', PORT), IwownHandler)
    print('[启动] 监听 http://0.0.0.0:%d (端点结尾 /pb/upload /alarm/upload ...)' % PORT)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n[退出] 收到中断')
        httpd.shutdown()


if __name__ == '__main__':
    main()
