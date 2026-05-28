-- 诊脉仪数据表 (h6dp_suifang.zhenmaiyi)
-- 创建于 2026-05-28, 与现有 wearable_device / wearable_device_data 并列
--
-- 结构: 每条记录 = 一位患者的一次诊脉, 含结构化数据 + 原始附件 base64
-- 附件: 一个 PDF (四诊报告) + 两个顶层 Excel (体质 + 脉诊导出)
-- 顶层 Excel 是同一 zip 内所有患者共享的, 故每条都冗余存一份 (单批 ~6KB 二进制,
-- 可接受). 这样查询时不需要 join, 单条记录自包含, 调用方拉一条就能还原全貌.

CREATE TABLE IF NOT EXISTS zhenmaiyi (
    id                       INT AUTO_INCREMENT PRIMARY KEY,
    case_id                  VARCHAR(64)  NOT NULL UNIQUE COMMENT '病例ID (诊脉仪导出 xlsx 第一列)',
    patient_name             VARCHAR(50)              COMMENT '患者姓名',
    patient_gender           VARCHAR(8)               COMMENT '患者性别',
    patient_age              INT                      COMMENT '患者年龄',
    detect_time              DATETIME                 COMMENT '诊脉仪检测时间',
    conclusion               VARCHAR(100)             COMMENT '体质结论 (例: 阳虚质兼痰湿质)',
    pulse_label              VARCHAR(32)              COMMENT '主脉象 (例: 弦细)',

    -- 结构化数据: 9 类体质得分 + 全部脉诊参数 + 答题记录 etc
    full_data                JSON                     COMMENT '体质9得分 + 脉诊42参数 + 体质问卷答题',

    -- 原始附件 (base64 编码)
    pdf_base64               LONGTEXT                 COMMENT '四诊报告 sizhen_.pdf base64',
    constitution_xlsx_base64 LONGTEXT                 COMMENT '顶层 患者体质记录导出*.xlsx base64',
    pulse_xlsx_base64        LONGTEXT                 COMMENT '顶层 患者脉诊导出*.xlsx base64',

    -- 元信息
    source_zip_name          VARCHAR(200)             COMMENT '上传时的源 zip 文件名',
    uploaded_at              DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT 'server 收到上传的时刻',

    INDEX idx_detect_time (detect_time),
    INDEX idx_patient_name (patient_name),
    INDEX idx_conclusion (conclusion),
    INDEX idx_uploaded_at (uploaded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='诊脉仪体质 + 脉诊数据 (h6dp_suifang) - 一行一例, 浏览器端解析 zip 后上传';
