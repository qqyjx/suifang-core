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
--   3. 插入 2 个测试患者 + 1 台 iwown 设备(绑定患者A) + 5 天 iwown 健康数据
--      (07-12..07-16, 佩戴小时数 3h/8h/16h/0h/5h, 供 M4 佩戴依从性联调用) +
--      1 条未关闭报警(患者A) + 1 行 wearable_device_data 大 JSON(患者B, S101-only)。
--   4. (M2 新增) 插入 4 条 iwown_data data_type='alarm' 原始行(SOS/心率越限/未佩戴/低电量),
--      供 POST /api/platform/alarm/ingest 联调; 用 (device_id,data_type,recorded_at) 做
--      NOT EXISTS 判重, 与其余表不同, 这几行可安全重复执行本文件。
--
-- 幂等性: platform_patient / iwown_device 用 ON DUPLICATE KEY UPDATE 重复执行安全;
-- 第 4 节 iwown_data health 行先 DELETE 再整段重插, 也可安全重复执行(见第 4 节注释);
-- 第 6 节 iwown_data alarm 行用 NOT EXISTS 判重, 也可安全重复执行; 其余
-- wearable_device_data / 第 5 节直插的 platform_alarm 行没有业务唯一键, 重复执行本文件
-- 会重复插入这几处明细行 —— 需要重置请换新 dev 库或先手工清空。

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

-- ============ 4. iwown 设备 + 5 天健康数据 (绑定患者 A, M4 佩戴依从性联调用) ============
-- M4 佩戴依从性 (docs/随访平台1.0设计方案.html §3.4): wear_hours = 当天 COUNT(DISTINCT HOUR(recorded_at))
-- 的 data_type='health' 帧数, 下面按 07-12..07-16 (真实"最近 5 天", 与本机 MySQL CURDATE() 对齐) 铺出
-- 3h/8h/16h/0h/5h 五种覆盖小时数, 制造佩戴率有起伏的曲线, 且 07-15 刻意留 0 小时(全天未佩戴),
-- 与下面第 5/6 节 07-15 的 not_worn 报警(09:30 直插 + 10:15 由 alarm 帧 ingest 出的一条)相互印证。
-- 幂等做法: 先删除该设备已有 health 行再整段重插 (dev-only 测试数据, 删了重插比追加更不容易腐化;
-- data_type='alarm' 的报警原始帧不受影响, 走第 6 节自己的 NOT EXISTS 判重)。

INSERT INTO iwown_device (device_id, patient_no, note) VALUES
    ('DEVWATCH00000001', 'DEV0001', 'dev seed 测试设备')
ON DUPLICATE KEY UPDATE patient_no = VALUES(patient_no), note = VALUES(note);

DELETE FROM iwown_data WHERE device_id = 'DEVWATCH00000001' AND data_type = 'health';

INSERT INTO iwown_data
    (device_id, data_type, opt, recorded_at, hr_avg, hr_min, hr_max,
     spo2_avg, spo2_min, spo2_max, sbp, dbp, temperature, pressure, step,
     battery, rssi, uploaded_at)
VALUES
    -- 07-12: 3 个不同小时 (8/14/20) -> wear_hours=3, wear_rate=0.13
    ('DEVWATCH00000001', 'health', 128, '2026-07-12 08:00', 82, 77, 88, 97, 96, 97, 116, 81, 36.3, 55, 1624, 82, -68,
     '2026-07-12 08:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-12 14:00', 63, 58, 70, 96, 95, 98, 112, 78, 36.3, 55, 7073, 59, -69,
     '2026-07-12 14:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-12 20:00', 80, 73, 86, 96, 93, 97, 122, 74, 36.3, 48, 1874, 50, -67,
     '2026-07-12 20:00:30'),
    -- 07-13: 8 个不同小时 (每 3 小时一次) -> wear_hours=8, wear_rate=0.33
    ('DEVWATCH00000001', 'health', 128, '2026-07-13 00:00', 65, 58, 76, 99, 97, 99, 126, 78, 36.3, 50, 1491, 80, -64,
     '2026-07-13 00:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-13 03:00', 88, 78, 99, 99, 98, 100, 114, 70, 36.6, 47, 1507, 59, -58,
     '2026-07-13 03:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-13 06:00', 74, 67, 87, 97, 96, 98, 123, 73, 36.6, 40, 3003, 79, -62,
     '2026-07-13 06:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-13 09:00', 67, 59, 79, 97, 94, 99, 129, 73, 36.6, 39, 3952, 47, -65,
     '2026-07-13 09:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-13 12:00', 74, 67, 81, 96, 93, 98, 122, 73, 36.6, 50, 7717, 54, -63,
     '2026-07-13 12:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-13 15:00', 66, 60, 80, 99, 97, 100, 130, 76, 36.7, 50, 6130, 59, -59,
     '2026-07-13 15:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-13 18:00', 78, 70, 85, 95, 94, 95, 117, 82, 36.6, 40, 6504, 69, -69,
     '2026-07-13 18:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-13 21:00', 78, 71, 92, 95, 92, 97, 115, 80, 36.7, 46, 5773, 52, -64,
     '2026-07-13 21:00:30'),
    -- 07-14: 16 个连续小时 (06 点到 21 点) -> wear_hours=16, wear_rate=0.67
    ('DEVWATCH00000001', 'health', 128, '2026-07-14 06:00', 75, 69, 88, 95, 92, 97, 120, 78, 36.7, 54, 1943, 85, -64,
     '2026-07-14 06:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-14 07:00', 88, 78, 102, 99, 98, 99, 123, 82, 36.3, 54, 209, 83, -65,
     '2026-07-14 07:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-14 08:00', 77, 72, 84, 97, 95, 97, 113, 73, 36.7, 40, 1603, 91, -70,
     '2026-07-14 08:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-14 09:00', 88, 83, 102, 96, 95, 98, 127, 78, 36.3, 54, 7132, 58, -61,
     '2026-07-14 09:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-14 10:00', 84, 77, 96, 97, 95, 99, 126, 71, 36.3, 40, 5739, 46, -62,
     '2026-07-14 10:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-14 11:00', 80, 74, 86, 95, 92, 97, 113, 73, 36.2, 39, 5613, 49, -62,
     '2026-07-14 11:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-14 12:00', 70, 60, 83, 96, 93, 96, 130, 79, 36.5, 53, 6869, 57, -58,
     '2026-07-14 12:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-14 13:00', 65, 55, 77, 97, 95, 98, 126, 81, 36.2, 41, 1193, 70, -65,
     '2026-07-14 13:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-14 14:00', 87, 82, 96, 96, 95, 98, 126, 72, 36.5, 46, 7779, 60, -57,
     '2026-07-14 14:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-14 15:00', 76, 67, 83, 95, 92, 97, 112, 71, 36.8, 45, 2924, 71, -70,
     '2026-07-14 15:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-14 16:00', 77, 71, 89, 95, 94, 96, 112, 76, 36.4, 52, 4873, 72, -70,
     '2026-07-14 16:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-14 17:00', 66, 60, 76, 96, 95, 98, 129, 70, 36.6, 39, 1021, 82, -70,
     '2026-07-14 17:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-14 18:00', 78, 69, 86, 95, 92, 95, 117, 71, 36.6, 45, 6815, 52, -62,
     '2026-07-14 18:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-14 19:00', 80, 71, 86, 99, 98, 100, 130, 79, 36.5, 46, 3546, 87, -65,
     '2026-07-14 19:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-14 20:00', 69, 62, 81, 96, 93, 98, 121, 77, 36.4, 40, 352, 74, -58,
     '2026-07-14 20:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-14 21:00', 64, 55, 73, 99, 97, 99, 123, 71, 36.7, 49, 4869, 55, -69,
     '2026-07-14 21:00:30'),
    -- 07-15: 刻意 0 小时(全天未佩戴) -> wear_hours=0, 与第 5/6 节的 07-15 not_worn 报警互相印证
    -- 07-16 (今天): 5 个小时 (凌晨 0-4 点, 早于当前时刻) -> wear_hours=5, wear_rate=0.21
    ('DEVWATCH00000001', 'health', 128, '2026-07-16 00:00', 88, 79, 98, 99, 96, 100, 112, 80, 36.7, 47, 1897, 53, -63,
     '2026-07-16 00:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-16 01:00', 65, 60, 79, 96, 94, 97, 118, 81, 36.4, 46, 8480, 76, -63,
     '2026-07-16 01:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-16 02:00', 63, 58, 75, 97, 96, 97, 122, 82, 36.3, 46, 2847, 92, -69,
     '2026-07-16 02:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-16 03:00', 79, 69, 91, 99, 98, 99, 114, 81, 36.7, 55, 790, 68, -59,
     '2026-07-16 03:00:30'),
    ('DEVWATCH00000001', 'health', 128, '2026-07-16 04:00', 75, 69, 81, 97, 95, 97, 123, 73, 36.6, 41, 5994, 80, -68,
     '2026-07-16 04:00:30');

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
