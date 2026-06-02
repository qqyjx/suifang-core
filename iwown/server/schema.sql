-- iwown 4G 手表数据表 (h6dp_suifang)
-- 与 S101 的 wearable_device* / zhenmaiyi 表并列; 服务启动会自动建 (ensure_tables), 此文件供手工/参考
-- 设计: 一行一帧, data_type 区分; 常用体征抽列做索引 + decoded_json 全量无损 + raw_hex 兜底

CREATE TABLE IF NOT EXISTS iwown_device (
    device_id  VARCHAR(32) PRIMARY KEY COMMENT '15字节设备ID(ASCII)',
    patient_no VARCHAR(64) DEFAULT NULL COMMENT '门诊号/患者标识(后台绑定, 类比 S101 门诊号)',
    note       VARCHAR(255) DEFAULT NULL,
    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='iwown 设备名册';

CREATE TABLE IF NOT EXISTS iwown_data (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    device_id   VARCHAR(32) NOT NULL,
    data_type   VARCHAR(16) NOT NULL COMMENT 'health/realtime/alarm/index/calllog/deviceinfo/status/unknown',
    opt         SMALLINT UNSIGNED DEFAULT NULL COMMENT '0x80健康/0x0A实时/0x12报警',
    recorded_at DATETIME DEFAULT NULL COMMENT '帧内测量时间',
    hr_avg SMALLINT DEFAULT NULL, hr_min SMALLINT DEFAULT NULL, hr_max SMALLINT DEFAULT NULL,
    spo2_avg SMALLINT DEFAULT NULL, spo2_min SMALLINT DEFAULT NULL, spo2_max SMALLINT DEFAULT NULL,
    sbp SMALLINT DEFAULT NULL, dbp SMALLINT DEFAULT NULL,
    temperature DECIMAL(5,2) DEFAULT NULL, pressure SMALLINT DEFAULT NULL,
    step INT DEFAULT NULL, distance DECIMAL(10,1) DEFAULT NULL, calorie DECIMAL(10,1) DEFAULT NULL,
    battery SMALLINT DEFAULT NULL, rssi SMALLINT DEFAULT NULL,
    decoded_json JSON DEFAULT NULL COMMENT 'MessageToDict 全量解码(无损)',
    raw_hex     MEDIUMTEXT COMMENT '原始帧hex(解码失败也保住, 可重处理)',
    uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_dev_time (device_id, recorded_at),
    INDEX idx_type (data_type),
    INDEX idx_uploaded (uploaded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='iwown 4G 手表上行数据';
