# -*- coding: utf-8 -*-
"""
iwown 4G 手表上行数据解析
==========================

职责: 把设备 POST 的二进制 body 解成结构化 dict。
忠于官方 Flask 示例 (reference/sample-python) 的解帧 + protobuf 逻辑,
但返回 dict 而非 print, 并用 MessageToDict 做无损解码。

帧格式 (小端):
    body = device_id(15 字节 ASCII) + 多个帧拼接
    每帧 = prefix(0x44 0x54) + length(uint16) + crc(uint16) + opt(uint16) + payload[length]

opt:
    0x80 历史健康数据 (分钟级)  -> HisNotification
    0x0A 实时数据 (步数/距离/卡路里/GNSS) -> OM0Report
    0x12 设备报警               -> Alarm_infokConfirm

设计要点:
- protobuf 运行时缺失或单帧解码失败时, 不抛异常: 该帧 decoded=None,
  调用方仍可凭 raw_hex 落库, 保证不丢数据 (可后续重处理)。
- decoded_json 用 MessageToDict 全量保留; key_metrics 仅抽常用体征做索引列。
- 数值换算与官方示例一致: distance/calorie ÷10; 压力=100-fatigue;
  体温 esti_arm/evi_body 高低 16 位分别 ÷100。
"""
import struct
import datetime

# protobuf 运行时 + 编译产物 (protoc 5.27.2 生成, 需 protobuf>=4.25)
# 缺失时降级: 只保 raw, 不解码 (服务仍能收数据 + 回 0x00)
try:
    from google.protobuf.json_format import MessageToDict
    from theproto import his_data_pb2, om0_command_pb2, Alarm_info_pb2
    from theproto import his_health_data_pb2  # noqa: F401 (enum 引用)
    PROTOBUF_OK = True
    PROTOBUF_ERR = None
except Exception as e:  # ImportError 或 _pb2 与运行时不兼容
    PROTOBUF_OK = False
    PROTOBUF_ERR = repr(e)

PREFIX = b'\x44\x54'  # "DT"
OPT_HEALTH = 0x80
OPT_REALTIME = 0x0A
OPT_ALARM = 0x12
DEVICE_ID_LEN = 15
FRAME_HEADER_LEN = 8  # prefix2 + length2 + crc2 + opt2


def crc16_modbus(data):
    """CRC-16/MODBUS (poly=0xA001 反射, init=0xFFFF, 无 final xor)。
    iwown 帧 crc 字段对 payload 校验。注意: 官方示例只读不校验, 真机算法待回归确认;
    本服务仅用于"对账记日志", 不据此拒收 (见 server/README 待确认项)。"""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF


def _epoch_to_str(seconds):
    if not seconds:
        return None
    try:
        # 与示例一致用本地时区格式化; 同时另存 epoch 供入库
        return datetime.datetime.fromtimestamp(seconds).strftime('%Y-%m-%d %H:%M:%S')
    except (OSError, OverflowError, ValueError):
        return None


def split_frames(body):
    """切出 device_id + 各帧。返回 (device_id, [frame_dict...], err)。

    frame_dict = {opt, crc, raw_hex, pb_bytes}
    err 非 None 表示帧结构非法 (对应官方返回码 0x02 长度不足 / 0x03 帧头错)。
    """
    if body is None or len(body) < DEVICE_ID_LEN + FRAME_HEADER_LEN:
        return None, [], 0x02  # 长度不足

    try:
        device_id = bytes(body[:DEVICE_ID_LEN]).decode('ascii', errors='replace').strip('\x00').strip()
    except Exception:
        device_id = bytes(body[:DEVICE_ID_LEN]).hex()

    frames = []
    pos = DEVICE_ID_LEN
    n = len(body)
    while pos < n:
        if pos + FRAME_HEADER_LEN > n:
            return device_id, frames, 0x02
        if body[pos] != 0x44 or body[pos + 1] != 0x54:
            return device_id, frames, 0x03  # 帧头错
        length = body[pos + 2] | (body[pos + 3] << 8)        # uint16 小端
        crc = body[pos + 4] | (body[pos + 5] << 8)
        opt = struct.unpack('<H', body[pos + 6:pos + 8])[0]
        end = pos + FRAME_HEADER_LEN + length
        if end > n:
            return device_id, frames, 0x02
        pb_bytes = bytes(body[pos + FRAME_HEADER_LEN:end])
        frames.append({
            'opt': opt,
            'crc': crc,
            'crc_ok': crc16_modbus(pb_bytes) == crc,  # 仅对账, 不拒收
            'pb_bytes': pb_bytes,
            'raw_hex': bytes(body[pos:end]).hex(),
        })
        pos = end
    return device_id, frames, None


# ---------------- 各 opt 解码 ----------------

def decode_frame(opt, pb_bytes):
    """单帧 protobuf 解码。返回 (data_type, recorded_at_str, decoded_dict, key_metrics)。
    解码失败/protobuf 不可用时 decoded_dict=None。"""
    if not PROTOBUF_OK:
        return _opt_name(opt), None, None, {}
    try:
        if opt == OPT_HEALTH:
            return _decode_health(pb_bytes)
        elif opt == OPT_REALTIME:
            return _decode_realtime(pb_bytes)
        elif opt == OPT_ALARM:
            return _decode_alarm(pb_bytes)
        else:
            return 'unknown', None, None, {}
    except Exception as e:
        # 解码失败: 交回 raw, 标注错误
        return _opt_name(opt), None, {'_decode_error': repr(e)}, {}


def _opt_name(opt):
    return {OPT_HEALTH: 'health', OPT_REALTIME: 'realtime', OPT_ALARM: 'alarm'}.get(opt, 'unknown')


def _decode_health(pb_bytes):
    """0x80 HisNotification -> 仅 HEALTH_DATA 类型抽体征; 其余(ecg/rri/spo2…)保 json。"""
    notify = his_data_pb2.HisNotification()
    notify.ParseFromString(pb_bytes)
    decoded = MessageToDict(notify, preserving_proto_field_name=True)
    field = notify.WhichOneof('data')
    metrics = {}
    rec = None
    if field == 'index_table':
        # 目录/同步索引帧 (start_seq/end_seq), 无体征 -> 单独归类, 不污染体征视图
        return 'index', None, decoded, {}
    if field == 'his_data' and notify.type == his_data_pb2.HisDataType.HEALTH_DATA \
            and notify.his_data.HasField('health'):
        h = notify.his_data.health
        rec = _epoch_to_str(h.time_stamp.date_time.seconds)
        if h.HasField('pedo_data'):
            metrics['step'] = h.pedo_data.step
            metrics['distance'] = round(h.pedo_data.distance * 0.1, 1)
            metrics['calorie'] = round(h.pedo_data.calorie * 0.1, 1)
        if h.HasField('hr_data'):
            metrics['hr_avg'] = h.hr_data.avg_bpm
            metrics['hr_min'] = h.hr_data.min_bpm
            metrics['hr_max'] = h.hr_data.max_bpm
        if h.HasField('bxoy_data'):
            metrics['spo2_avg'] = h.bxoy_data.agv_oxy
            metrics['spo2_min'] = h.bxoy_data.min_oxy
            metrics['spo2_max'] = h.bxoy_data.max_oxy
        if h.HasField('bp_data'):
            metrics['sbp'] = h.bp_data.sbp
            metrics['dbp'] = h.bp_data.dbp
        if h.HasField('hrv_data'):
            fatigue = int(h.hrv_data.fatigue)
            if fatigue > 0:
                metrics['pressure'] = 100 - fatigue
        if h.HasField('temperature_data'):
            t = h.temperature_data
            # esti_arm: 低16=腋温, 高16=估计体温; evi_body: 低16=壳温, 高16=环境温 (均÷100)
            metrics['temperature'] = round(float((t.esti_arm >> 16) & 0xFFFF) / 100.0, 2)
            metrics['temperature_axillary'] = round(float(t.esti_arm & 0xFFFF) / 100.0, 2)
    return 'health', rec, decoded, metrics


def _decode_realtime(pb_bytes):
    """0x0A OM0Report (om0_command_pb2) -> 实时步数/距离/卡路里/电量/信号/GNSS。
    注意: battery 是子消息(取 .level), health 字段名是 steps(非 step)。"""
    om0 = om0_command_pb2.OM0Report()
    om0.ParseFromString(pb_bytes)
    decoded = MessageToDict(om0, preserving_proto_field_name=True)
    rec = _epoch_to_str(om0.date_time.date_time.seconds) if om0.HasField('date_time') else None
    metrics = {}
    if om0.HasField('battery'):
        metrics['battery'] = om0.battery.level
    if om0.HasField('rssi'):
        rssi = om0.rssi
        if rssi > 0x7FFFFFFF:           # uint32 表负数, 与示例一致
            rssi = -((rssi ^ 0xFFFFFFFF) + 1)
        metrics['rssi'] = rssi
    if om0.HasField('health'):
        metrics['step'] = om0.health.steps
        metrics['distance'] = round(om0.health.distance * 0.1, 1)
        metrics['calorie'] = round(om0.health.calorie * 0.1, 1)
    return 'realtime', rec, decoded, metrics


def _decode_alarm(pb_bytes):
    """0x12 Alarm_infokConfirm -> 报警全量 json (类型多, 不抽列, 全保)。"""
    alarm = Alarm_info_pb2.Alarm_infokConfirm()
    alarm.ParseFromString(pb_bytes)
    decoded = MessageToDict(alarm, preserving_proto_field_name=True)
    rec = None
    try:
        if alarm.HasField('Alarminfo'):
            rec = _epoch_to_str(alarm.Alarminfo.time_stamp.date_time.seconds)
    except Exception:
        pass
    return 'alarm', rec, decoded, {}
