-- 随访平台 1.0 M1 + 1.1 M5 数据模型 (h6dp_suifang)
-- 归档参考: 真正的建表逻辑在 scripts/health_server.py ensure_platform_tables() (启动时幂等执行),
-- 本文件与其保持一致, 供人工查阅/离线建库, 不由代码读取。
--
-- 设计原则 (docs/随访平台1.0设计方案.html §①④): 不动现有 6 张生产表
-- (wearable_device* / zhenmaiyi / iwown_* / ble_event) 一列, 平台层只新增以下 4 张
-- platform_ 前缀表, 以门诊号 (patient_no) 为患者主键, 与 S101 现行体系同构。

CREATE TABLE IF NOT EXISTS platform_patient (
    patient_no        VARCHAR(64) PRIMARY KEY COMMENT '门诊号, 患者主键',
    name              VARCHAR(64)  DEFAULT NULL,
    gender            ENUM('M','F') DEFAULT NULL,
    age               INT DEFAULT NULL,
    group_tag         VARCHAR(64)  DEFAULT NULL COMMENT '队列/分组',
    zhenmaiyi_case_id VARCHAR(64)  DEFAULT NULL COMMENT '诊脉仪 case_id 映射 (zhenmaiyi.case_id)',
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
    lat             DECIMAL(10,6) DEFAULT NULL COMMENT 'SOS 定位纬度',
    lng             DECIMAL(10,6) DEFAULT NULL COMMENT 'SOS 定位经度',
    payload_json    JSON DEFAULT NULL COMMENT '原始报警帧解析出的结构化字段',
    source_data_id  BIGINT DEFAULT NULL COMMENT '→iwown_data.id, 幂等去重 (解析任务重跑不产生重复事件)',
    status          ENUM('new','acked','followed','closed') DEFAULT 'new',
    occurred_at     DATETIME DEFAULT NULL COMMENT '报警发生时刻(帧内时间)',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '事件入库时刻',
    UNIQUE KEY uk_source_data_id (source_data_id),
    INDEX idx_patient_no (patient_no),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台报警事件 (M2 起写入, M1 只建表)';

CREATE TABLE IF NOT EXISTS platform_followup_log (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    patient_no   VARCHAR(64) DEFAULT NULL,
    alarm_id     BIGINT DEFAULT NULL COMMENT '可空: 日常随访无报警',
    action       ENUM('ack','call','visit','note','close') DEFAULT NULL,
    result_text  TEXT,
    operator     VARCHAR(64) DEFAULT NULL,
    plan_id      BIGINT DEFAULT NULL COMMENT '1.1 随访计划引擎钩子 -> platform_plan.id, M5 起写入',
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_patient_no (patient_no),
    INDEX idx_plan (plan_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台回访/处理记录 (报警闭环 + 日常随访通用, M2 起写入, M1 只建表)';

-- 随访平台 1.1 M5 数据模型: 随访计划引擎. 设计原则不变——"任务"是 active=1 且
-- next_due 落在窗口内的计划现算结果, 从不单独落地存储; 完成任务只写 1 行
-- platform_followup_log(plan_id 关联) + 推进/停用本表这一条计划记录。
CREATE TABLE IF NOT EXISTS platform_plan (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    patient_no      VARCHAR(64) NOT NULL,
    name            VARCHAR(128) NOT NULL COMMENT '如 术后1月电话随访',
    frequency_days  INT DEFAULT NULL COMMENT 'NULL=一次性, 否则每 N 天重复',
    next_due        DATE NOT NULL,
    active          TINYINT(1) DEFAULT 1,
    note            VARCHAR(255) DEFAULT NULL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_patient_no (patient_no),
    INDEX idx_next_due (next_due)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访平台 1.1 随访计划 (任务由 active+next_due 现算, 不单独存储)';
