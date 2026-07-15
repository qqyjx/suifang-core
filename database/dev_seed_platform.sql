-- ⚠️ DEV-ONLY 种子数据 — 切勿在生产环境 (h6dp_suifang) 运行本文件！
-- 仅供本地/开发库 (例如 h6dp_suifang_dev) 联调随访平台 1.0 M1 用。
--
-- 做的事:
--   1. 建 wearable_device / wearable_device_data / zhenmaiyi 3 张"最小 dev 结构"表
--      (IF NOT EXISTS; 字段推断自 scripts/health_server.py 的实际读写用法, 生产库这几张表
--      已经存在, 本文件不影响生产 —— 该文件只应指向 dev 库)。
--   2. 建 platform_patient / platform_alarm 表 (与 database/platform.sql /
--      health_server.py ensure_platform_tables() 的 DDL 保持一致)。这两张表本应由
--      服务启动时 ensure_platform_tables() 幂等创建, 但本文件设计为在启动服务*之前*先
--      执行 (verify 流程: 先 apply schema/seed, 再 python3 health_server.py), 所以这里
--      也建一遍, 确保下面的 INSERT 不会因为表不存在而失败; 服务后续启动时 CREATE TABLE
--      IF NOT EXISTS 是 no-op。
--   3. 插入 2 个测试患者 + 1 台 iwown 设备(绑定患者A) + 3 天 iwown 健康数据 +
--      1 条未关闭报警(患者A) + 1 行 wearable_device_data 大 JSON(患者B, S101-only)。
--   4. (M2 新增) 插入 4 条 iwown_data data_type='alarm' 原始行(SOS/心率越限/未佩戴/低电量),
--      供 POST /api/platform/alarm/ingest 联调; 用 (device_id,data_type,recorded_at) 做
--      NOT EXISTS 判重, 与其余表不同, 这几行可安全重复执行本文件。
--
-- 幂等性: platform_patient / iwown_device 用 ON DUPLICATE KEY UPDATE 重复执行安全;
-- 第 6 节 iwown_data alarm 行用 NOT EXISTS 判重, 也可安全重复执行; 其余
-- iwown_data(健康数据行) / wearable_device_data / 第 5 节直插的 platform_alarm 行
-- 没有业务唯一键, 重复执行本文件会重复插入这几处明细行 —— 需要重置请换新 dev 库或先手工清空。

-- ============ 1. 最小 dev 结构: wearable_device / wearable_device_data / zhenmaiyi ============

CREATE TABLE IF NOT EXISTS wearable_device (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    device_sign VARCHAR(255) DEFAULT NULL COMMENT 'name_<MAC> 复合标识',
    mac         VARCHAR(32)  DEFAULT NULL,
    type        INT DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='S101/R04 设备名册 (最小 dev 结构, 推断自 health_server.py device_register 用法)';

CREATE TABLE IF NOT EXISTS wearable_device_data (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    deviceId   INT NOT NULL,
    data       JSON DEFAULT NULL COMMENT '大 JSON 汇总 (中文键 心率/血氧/血压/... 各是记录数组), 见 upsert_device_data',
    createTime DATETIME DEFAULT NULL,
    INDEX idx_deviceId (deviceId)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='S101/R04 体征数据 (一台设备一行, 最小 dev 结构)';

CREATE TABLE IF NOT EXISTS zhenmaiyi (
    id                       INT AUTO_INCREMENT PRIMARY KEY,
    case_id                  VARCHAR(64)  NOT NULL UNIQUE COMMENT '病例ID',
    patient_name             VARCHAR(50)  DEFAULT NULL COMMENT '患者姓名',
    patient_gender           VARCHAR(8)   DEFAULT NULL COMMENT '患者性别',
    patient_age              INT          DEFAULT NULL COMMENT '患者年龄',
    detect_time              DATETIME     DEFAULT NULL COMMENT '诊脉仪检测时间',
    conclusion               VARCHAR(100) DEFAULT NULL COMMENT '体质结论',
    pulse_label              VARCHAR(32)  DEFAULT NULL COMMENT '主脉象',
    full_data                JSON COMMENT '体质9得分 + 脉诊42参数 + 答题记录',
    pdf_base64               LONGTEXT COMMENT '四诊报告 sizhen_.pdf base64',
    constitution_xlsx_base64 LONGTEXT COMMENT '顶层 患者体质记录导出*.xlsx base64',
    pulse_xlsx_base64        LONGTEXT COMMENT '顶层 患者脉诊导出*.xlsx base64',
    source_zip_name          VARCHAR(200) DEFAULT NULL COMMENT '上传时的源 zip 文件名',
    uploaded_at              DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_detect_time (detect_time),
    INDEX idx_patient_name (patient_name),
    INDEX idx_conclusion (conclusion),
    INDEX idx_uploaded_at (uploaded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='诊脉仪数据 (与 database/zhenmaiyi.sql / ensure_zhenmaiyi_table 一致)';

-- ============ 2. platform_patient / platform_alarm (与 database/platform.sql 一致, 见文件头说明) ============

CREATE TABLE IF NOT EXISTS platform_patient (
    patient_no        VARCHAR(64) PRIMARY KEY COMMENT '门诊号, 患者主键',
    name              VARCHAR(64)  DEFAULT NULL,
    gender            ENUM('M','F') DEFAULT NULL,
    age               INT DEFAULT NULL,
    group_tag         VARCHAR(64)  DEFAULT NULL COMMENT '队列/分组',
    zhenmaiyi_case_id VARCHAR(64)  DEFAULT NULL COMMENT '诊脉仪 case_id 映射',
    note              VARCHAR(255) DEFAULT NULL,
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台患者主索引';

CREATE TABLE IF NOT EXISTS platform_alarm (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    patient_no      VARCHAR(64) DEFAULT NULL,
    device_id       VARCHAR(32) DEFAULT NULL,
    alarm_type      VARCHAR(24) DEFAULT NULL COMMENT 'fall/sos/hr/spo2/bp/temp/sedentary/not_worn/low_battery',
    severity        ENUM('crit','warn','info') DEFAULT NULL,
    lat             DECIMAL(10,6) DEFAULT NULL,
    lng             DECIMAL(10,6) DEFAULT NULL,
    payload_json    JSON DEFAULT NULL,
    source_data_id  BIGINT DEFAULT NULL COMMENT '→iwown_data.id, 幂等去重',
    status          ENUM('new','acked','followed','closed') DEFAULT 'new',
    occurred_at     DATETIME DEFAULT NULL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_source_data_id (source_data_id),
    INDEX idx_patient_no (patient_no),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台报警事件 (M2 起写入, M1 只建表)';

-- ============ 3. 测试患者 A / B ============
-- A: DEV0001, 绑定 1 台真机形态的 iwown 设备 (M1 验收标准: "绑定 1 台真机 iwown 设备到测试患者, 详情页出真趋势")
-- B: DEV0002, 仅 S101 数据(大 JSON 里门诊号=DEV0002), 不绑 iwown, 演示"S101-only"患者

INSERT INTO platform_patient (patient_no, name, gender, age, group_tag, note) VALUES
    ('DEV0001', '测试患者A', 'F', 68, '随访平台联调', 'M1 验收用: iwown 真机设备绑定 + 日趋势'),
    ('DEV0002', '测试患者B', 'M', 55, '随访平台联调', 'M1 验收用: 仅 S101 数据, 无 iwown 绑定')
ON DUPLICATE KEY UPDATE name = VALUES(name), gender = VALUES(gender), age = VALUES(age),
    group_tag = VALUES(group_tag), note = VALUES(note);

-- ============ 4. iwown 设备 + 3 天健康数据 (绑定患者 A) ============

INSERT INTO iwown_device (device_id, patient_no, note) VALUES
    ('DEVWATCH00000001', 'DEV0001', 'dev seed 测试设备')
ON DUPLICATE KEY UPDATE patient_no = VALUES(patient_no), note = VALUES(note);

INSERT INTO iwown_data
    (device_id, data_type, opt, recorded_at, hr_avg, hr_min, hr_max,
     spo2_avg, spo2_min, spo2_max, sbp, dbp, temperature, pressure, step,
     battery, rssi, uploaded_at)
VALUES
    ('DEVWATCH00000001', 'health', 128, '2026-07-13 08:00:00', 68, 60, 78,
     97, 95, 99, 120, 76, 36.4, 45, 1500,
     88, -62, '2026-07-13 08:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-13 20:00:00', 74, 65, 90,
     96, 94, 98, 124, 79, 36.6, 52, 7600,
     81, -65, '2026-07-13 20:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-14 08:00:00', 66, 58, 76,
     98, 96, 99, 117, 74, 36.3, 40, 1300,
     76, -60, '2026-07-14 08:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-14 20:00:00', 72, 63, 85,
     97, 95, 98, 121, 77, 36.5, 48, 7100,
     68, -64, '2026-07-14 20:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-15 08:00:00', 69, 61, 80,
     97, 95, 99, 119, 75, 36.4, 42, 1600,
     60, -58, '2026-07-15 08:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-15 20:00:00', 76, 66, 92,
     96, 93, 98, 126, 80, 36.7, 55, 8200,
     52, -63, '2026-07-15 20:00:30');

-- ============ 5. 1 条未关闭报警 (患者 A, 直接插 platform_alarm, source_data_id=NULL) ============
-- 与下面第 6 节"由 iwown_data alarm 行 ingest 出来的报警"(source_data_id 非空)共存,
-- 用于验证 M2 ingest 幂等去重逻辑(LEFT JOIN 判重只看 source_data_id, 这条 NULL 不受影响)。

INSERT INTO platform_alarm (patient_no, device_id, alarm_type, severity, status, occurred_at) VALUES
    ('DEV0001', 'DEVWATCH00000001', 'not_worn', 'warn', 'new', '2026-07-15 09:30:00');

-- ============ 6. M2: iwown_data data_type='alarm' 原始行 (供 /api/platform/alarm/ingest 用) ============
-- decoded_json 形状与 iwown_parser.py _decode_alarm() 的 MessageToDict(preserving_proto_field_name=True)
-- 输出一致 (字段名对应 iwown/reference/proto/Alarm_info.proto 的 Alarm_infokConfirm/HealthAlarmV3/AlarminfoV3)。
-- 幂等: 用 INSERT...SELECT...WHERE NOT EXISTS 按 (device_id,data_type,recorded_at) 去重, 可重复执行本文件。

-- 6a. SOS (含 GNSS 定位)
INSERT INTO iwown_data (device_id, data_type, opt, recorded_at, decoded_json, uploaded_at)
SELECT 'DEVWATCH00000001', 'alarm', 18, '2026-07-15 05:30:00',
    '{"alarm": {"gnssinfo": [{"time_stamp": {"date_time": {"seconds": 1784263800}}, "longitude": 116.397128, "latitude": 39.916527, "gps_type": "GPS_WGS84"}], "SOS_Notification_time": {"date_time": {"seconds": 1784263800}}}}',
    '2026-07-15 05:30:05'
WHERE NOT EXISTS (
    SELECT 1 FROM iwown_data WHERE device_id = 'DEVWATCH00000001' AND data_type = 'alarm' AND recorded_at = '2026-07-15 05:30:00'
);

-- 6b. 心率越限 (hr threshold)
INSERT INTO iwown_data (device_id, data_type, opt, recorded_at, decoded_json, uploaded_at)
SELECT 'DEVWATCH00000001', 'alarm', 18, '2026-07-15 07:10:00',
    '{"alarm": {"alarm_hr": [{"time_stamp": {"date_time": {"seconds": 1784269800}}, "hr": 138}]}}',
    '2026-07-15 07:10:05'
WHERE NOT EXISTS (
    SELECT 1 FROM iwown_data WHERE device_id = 'DEVWATCH00000001' AND data_type = 'alarm' AND recorded_at = '2026-07-15 07:10:00'
);

-- 6c. 未佩戴 (not worn)
INSERT INTO iwown_data (device_id, data_type, opt, recorded_at, decoded_json, uploaded_at)
SELECT 'DEVWATCH00000001', 'alarm', 18, '2026-07-15 10:15:00',
    '{"Alarminfo": {"time_stamp": {"date_time": {"seconds": 1784283300}}, "wearstate": true}}',
    '2026-07-15 10:15:05'
WHERE NOT EXISTS (
    SELECT 1 FROM iwown_data WHERE device_id = 'DEVWATCH00000001' AND data_type = 'alarm' AND recorded_at = '2026-07-15 10:15:00'
);

-- 6d. 低电量 (low battery)
INSERT INTO iwown_data (device_id, data_type, opt, recorded_at, decoded_json, uploaded_at)
SELECT 'DEVWATCH00000001', 'alarm', 18, '2026-07-15 13:45:00',
    '{"Alarminfo": {"time_stamp": {"date_time": {"seconds": 1784296500}}, "lowpowerPercentage": 8}}',
    '2026-07-15 13:45:05'
WHERE NOT EXISTS (
    SELECT 1 FROM iwown_data WHERE device_id = 'DEVWATCH00000001' AND data_type = 'alarm' AND recorded_at = '2026-07-15 13:45:00'
);

-- ============ 7. wearable_device_data 大 JSON 行 (患者 B, 门诊号=DEV0002, 形状同生产) ============
-- 结构与 health_server.py to_chinese_record()/upsert_device_data() 写入的形状一致:
-- 每个中文键(心率/血氧/血压/体温/步数)是记录数组, 每条记录带 采集时间/上传时间/门诊号。
-- deviceId=9001 是本文件专用的 dev 测试设备号, 不与真实生产 deviceId 冲突。

INSERT INTO wearable_device_data (deviceId, data, createTime) VALUES
    (9001, '{"心率": [{"心率值": 72, "心率状态": 0, "采集时间": "2026-07-13T08:15:00.000Z", "上传时间": "2026-07-13T08:15:00.000Z", "门诊号": "DEV0002"}, {"心率值": 75, "心率状态": 0, "采集时间": "2026-07-13T20:30:00.000Z", "上传时间": "2026-07-13T20:30:00.000Z", "门诊号": "DEV0002"}, {"心率值": 70, "心率状态": 0, "采集时间": "2026-07-14T08:20:00.000Z", "上传时间": "2026-07-14T08:20:00.000Z", "门诊号": "DEV0002"}, {"心率值": 74, "心率状态": 0, "采集时间": "2026-07-14T20:35:00.000Z", "上传时间": "2026-07-14T20:35:00.000Z", "门诊号": "DEV0002"}, {"心率值": 73, "心率状态": 0, "采集时间": "2026-07-15T08:10:00.000Z", "上传时间": "2026-07-15T08:10:00.000Z", "门诊号": "DEV0002"}, {"心率值": 77, "心率状态": 0, "采集时间": "2026-07-15T20:25:00.000Z", "上传时间": "2026-07-15T20:25:00.000Z", "门诊号": "DEV0002"}], "血氧": [{"血氧饱和度": 97, "心率": 72, "采集时间": "2026-07-13T08:15:00.000Z", "上传时间": "2026-07-13T08:15:00.000Z", "门诊号": "DEV0002"}, {"血氧饱和度": 98, "心率": 75, "采集时间": "2026-07-13T20:30:00.000Z", "上传时间": "2026-07-13T20:30:00.000Z", "门诊号": "DEV0002"}, {"血氧饱和度": 96, "心率": 70, "采集时间": "2026-07-14T08:20:00.000Z", "上传时间": "2026-07-14T08:20:00.000Z", "门诊号": "DEV0002"}, {"血氧饱和度": 97, "心率": 74, "采集时间": "2026-07-14T20:35:00.000Z", "上传时间": "2026-07-14T20:35:00.000Z", "门诊号": "DEV0002"}, {"血氧饱和度": 97, "心率": 73, "采集时间": "2026-07-15T08:10:00.000Z", "上传时间": "2026-07-15T08:10:00.000Z", "门诊号": "DEV0002"}, {"血氧饱和度": 99, "心率": 77, "采集时间": "2026-07-15T20:25:00.000Z", "上传时间": "2026-07-15T20:25:00.000Z", "门诊号": "DEV0002"}], "血压": [{"高压": 118, "低压": 75, "脉搏": 72, "风险等级": "正常", "采集时间": "2026-07-13T08:15:00.000Z", "上传时间": "2026-07-13T08:15:00.000Z", "门诊号": "DEV0002"}, {"高压": 122, "低压": 78, "脉搏": 75, "风险等级": "偏高", "采集时间": "2026-07-13T20:30:00.000Z", "上传时间": "2026-07-13T20:30:00.000Z", "门诊号": "DEV0002"}, {"高压": 115, "低压": 74, "脉搏": 70, "风险等级": "正常", "采集时间": "2026-07-14T08:20:00.000Z", "上传时间": "2026-07-14T08:20:00.000Z", "门诊号": "DEV0002"}, {"高压": 124, "低压": 79, "脉搏": 74, "风险等级": "偏高", "采集时间": "2026-07-14T20:35:00.000Z", "上传时间": "2026-07-14T20:35:00.000Z", "门诊号": "DEV0002"}, {"高压": 119, "低压": 73, "脉搏": 73, "风险等级": "正常", "采集时间": "2026-07-15T08:10:00.000Z", "上传时间": "2026-07-15T08:10:00.000Z", "门诊号": "DEV0002"}, {"高压": 128, "低压": 81, "脉搏": 77, "风险等级": "高血压1级", "采集时间": "2026-07-15T20:25:00.000Z", "上传时间": "2026-07-15T20:25:00.000Z", "门诊号": "DEV0002"}], "体温": [{"体温": 36.5, "皮肤温度": 35.3, "采集时间": "2026-07-13T08:15:00.000Z", "上传时间": "2026-07-13T08:15:00.000Z", "门诊号": "DEV0002"}, {"体温": 36.6, "皮肤温度": 35.4, "采集时间": "2026-07-13T20:30:00.000Z", "上传时间": "2026-07-13T20:30:00.000Z", "门诊号": "DEV0002"}, {"体温": 36.4, "皮肤温度": 35.2, "采集时间": "2026-07-14T08:20:00.000Z", "上传时间": "2026-07-14T08:20:00.000Z", "门诊号": "DEV0002"}, {"体温": 36.7, "皮肤温度": 35.5, "采集时间": "2026-07-14T20:35:00.000Z", "上传时间": "2026-07-14T20:35:00.000Z", "门诊号": "DEV0002"}, {"体温": 36.5, "皮肤温度": 35.3, "采集时间": "2026-07-15T08:10:00.000Z", "上传时间": "2026-07-15T08:10:00.000Z", "门诊号": "DEV0002"}, {"体温": 36.8, "皮肤温度": 35.6, "采集时间": "2026-07-15T20:25:00.000Z", "上传时间": "2026-07-15T20:25:00.000Z", "门诊号": "DEV0002"}], "步数": [{"步数": 3200, "卡路里": 128.0, "距离_米": 2240.0, "采集时间": "2026-07-13T08:15:00.000Z", "上传时间": "2026-07-13T08:15:00.000Z", "门诊号": "DEV0002"}, {"步数": 8700, "卡路里": 348.0, "距离_米": 6090.0, "采集时间": "2026-07-13T20:30:00.000Z", "上传时间": "2026-07-13T20:30:00.000Z", "门诊号": "DEV0002"}, {"步数": 2900, "卡路里": 116.0, "距离_米": 2030.0, "采集时间": "2026-07-14T08:20:00.000Z", "上传时间": "2026-07-14T08:20:00.000Z", "门诊号": "DEV0002"}, {"步数": 8100, "卡路里": 324.0, "距离_米": 5670.0, "采集时间": "2026-07-14T20:35:00.000Z", "上传时间": "2026-07-14T20:35:00.000Z", "门诊号": "DEV0002"}, {"步数": 3400, "卡路里": 136.0, "距离_米": 2380.0, "采集时间": "2026-07-15T08:10:00.000Z", "上传时间": "2026-07-15T08:10:00.000Z", "门诊号": "DEV0002"}, {"步数": 9200, "卡路里": 368.0, "距离_米": 6440.0, "采集时间": "2026-07-15T20:25:00.000Z", "上传时间": "2026-07-15T20:25:00.000Z", "门诊号": "DEV0002"}]}',
     '2026-07-15 20:25:30');
