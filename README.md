# 智能随访系统 — 可穿戴设备体征数据采集平台

基于 Veepoo BLE SDK 的医疗级智能穿戴设备数据采集平台。**系统已部署生产环境**，数据通过 `https://dc.ncrc.org.cn/api2` 写入六元空间 MySQL 数据库。

当前已集成 **2 款具备二类医疗器械注册证资质**的 Veepoo 可穿戴设备，支持 **10 类体征数据**的自动采集，采集端尽可能多存，客户按需拉取。

## 🎬 在线演示（GitHub Pages）

| 演示 | 链接 |
|---|---|
| 极简患者界面（仅连接/断开） | https://qqyjx.github.io/suifang/prototype/patient-demo.html |
| 4-Tab 完整原型（医护管理界面） | https://qqyjx.github.io/suifang/prototype/index.html |

> 浏览器或手机直接打开即可。三态切换：未连接 / 连接中 / 已连接。键盘 `Space` 切状态、`R` 重置。

## 📚 在线文档（GitHub Pages 自动渲染）

所有 `docs/*.md` 已生成同款样式 HTML，浏览器/手机直接打开即可阅读，移动端响应式 + 中文优化。

| 文档 | 受众 | 在线链接 |
|---|---|---|
| 医院交付使用指南 | 患者本人 | https://qqyjx.github.io/suifang/docs/%E5%8C%BB%E9%99%A2%E4%BA%A4%E4%BB%98%E4%BD%BF%E7%94%A8%E6%8C%87%E5%8D%97.html |
| 新设备接入手册 | 医院 PM / 护士 | https://qqyjx.github.io/suifang/docs/%E6%96%B0%E8%AE%BE%E5%A4%87%E6%8E%A5%E5%85%A5%E6%89%8B%E5%86%8C.html |
| 真机测试 checklist | 工程师 | https://qqyjx.github.io/suifang/docs/%E7%9C%9F%E6%9C%BA%E6%B5%8B%E8%AF%95checklist.html |
| 服务器运维 | 运维 | https://qqyjx.github.io/suifang/docs/%E6%9C%8D%E5%8A%A1%E5%99%A8%E8%BF%90%E7%BB%B4.html |
| 功能测试-手表数据上传 | 第三方测试 | https://qqyjx.github.io/suifang/docs/%E5%8A%9F%E8%83%BD%E6%B5%8B%E8%AF%95-%E6%89%8B%E8%A1%A8%E6%95%B0%E6%8D%AE%E4%B8%8A%E4%BC%A0.html |
| FAQ-常见问题清单 | 全员 | https://qqyjx.github.io/suifang/docs/FAQ-%E5%B8%B8%E8%A7%81%E9%97%AE%E9%A2%98%E6%B8%85%E5%8D%95.html |
| SDK 数据采集全景 | 工程师 | https://qqyjx.github.io/suifang/docs/SDK%E6%95%B0%E6%8D%AE%E9%87%87%E9%9B%86%E5%85%A8%E6%99%AF.html |
| Veepoo SDK 使用文档（5600+ 行） | 工程师 | https://qqyjx.github.io/suifang/docs/VeepooWeiXinSDK%E4%BD%BF%E7%94%A8%E6%96%87%E6%A1%A3.html |
| 业务逻辑设计 v10 | 产品 + 工程师 | https://qqyjx.github.io/suifang/docs/business-logic-v10.html |
| UI 原型 v10 设计稿 | 产品 + 设计 | https://qqyjx.github.io/suifang/docs/ui-prototype-v10.html |
| Pencil + Claude Code 工作流 | 设计 + 工程师 | https://qqyjx.github.io/suifang/docs/pencil-claude-workflow.html |

## 为什么必须用手机/小程序，电脑直接收不了手表数据？

```
手表（VP-W680）
  ↓
  支持的通信方式只有：BLE 蓝牙
  没有 WiFi、没有 4G、没有以太网
  ↓
  BLE 是「短距离 + 加密私有协议」
  ↓
  必须有这两样东西才能接收：
  ① 蓝牙硬件（电脑/手机/树莓派都行）
  ② Veepoo 的协议解码器（厂商私有 SDK）
```

Veepoo 厂商提供的 SDK 只有 3 个：**微信小程序、Android、iOS**。**没有 PC 桌面 SDK**。

理论上可以「电脑插 USB 蓝牙适配器 + 自己逆向 Veepoo 加密协议」，但这难度堪比破解微信协议——不现实。

**结论**：手表 → 手机（小程序作为 BLE 桥）→ 后端服务，是当前唯一可行的链路。电脑/服务器在整个系统里只负责「接收手机上传的数据 + 写入数据库」，不参与 BLE 接收。

---

---

## 目录

- [生产环境架构](#生产环境架构)
- [支持的设备](#支持的设备)
- [数据采集能力（11 类体征数据）](#数据采集能力11-类体征数据)
- [REST API 文档](#rest-api-文档)
- [数据库设计](#数据库设计)
- [快速验证（生产环境）](#快速验证生产环境)
- [本地开发环境](#本地开发环境)
- [BLE 连接与数据采集](#ble-连接与数据采集)
- [小程序本地存储](#小程序本地存储)
- [数据导出](#数据导出)
- [故障排除](#故障排除)
- [项目结构](#项目结构)
- [SDK 版本历史](#sdk-版本历史)
- [注意事项](#注意事项)

---

## 生产环境架构

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────────┐
│   可穿戴设备      │────→│  微信小程序        │────→│  六元外网代理             │
│                 │ BLE │                  │HTTPS│  dc.ncrc.org.cn/api2    │
│ AMOLED 手表     │     │ Veepoo SDK(378KB)│POST │                         │
│ R04 智能戒指    │     │ 11 类数据采集     │     │                         │
└─────────────────┘     └──────────────────┘     └────────────┬────────────┘
                                                              │ 反向代理
                                                 ┌────────────▼────────────┐
                                                 │  公司服务器               │
                                                 │  192.168.4.104          │
                                                 │  Python HTTP 服务        │
                                                 └────────────┬────────────┘
                                                              │ SQL INSERT
                                                 ┌────────────▼────────────┐
                                                 │  六元 MySQL              │
                                                 │  192.168.4.174          │
                                                 │  数据库: h6dp_suifang    │
                                                 │  ├ wearable_device (设备)│
                                                 │  └ wearable_device_data  │
                                                 │    (体征数据, JSON 格式) │
                                                 └─────────────────────────┘
```

**数据流**：

```
AMOLED 手表（VP-W680）
    ↓ BLE 蓝牙（Veepoo 私有协议 + 加密）
手机微信小程序（Veepoo SDK 解析协议帧 → TypeScript 结构体）
    ↓ HTTPS POST
https://dc.ncrc.org.cn/api2/api/health-data（六元外网代理）
    ↓ 反向代理
公司服务器 192.168.4.104（Python HTTP :3000）
    ↓ SELECT-merge-UPSERT
六元 MySQL 192.168.4.174 / h6dp_suifang / wearable_device_data
```

**各层职责**：

| 层 | 做什么 |
|----|-------|
| **手表** | 传感器采集原始体征信号，通过 BLE 发送二进制数据帧 |
| **微信小程序** | Veepoo SDK（378KB JS）解码协议帧为结构化数据，先存手机本地 JSON 缓存（断网不丢），再自动 HTTPS POST 上传 |
| **六元外网代理** | `dc.ncrc.org.cn/api2`，nginx 反向代理，手机在任何网络都能访问 |
| **公司服务器** | Python HTTP 服务，接收 JSON，转为中文字段，UPSERT 写入 MySQL |
| **六元 MySQL** | `wearable_device_data` 表，每行一个大 JSON 汇总该设备的所有历史采集数据 |

---

## 支持的设备

| 设备 | 型号 | 佩戴方式 | 生产厂商 | 注册证编号 | 采集指标 | 连接方式 | 状态 |
|------|------|---------|---------|-----------|---------|---------|------|
| R04 蓝牙智能戒指 | VPR04-BLE | 指戴式 | 深圳维亿魄科技 | 粤械注准20232070845 | 心率、血氧、HRV、压力、睡眠 | BLE 4.2+ | ✅ 已集成 |
| AMOLED 智能手表 | VP-W680 | 腕戴式 | 深圳维亿魄科技 | 粤械注准20242211536 | 血压、体温、血糖、血液成分、身体成分、血氧 | BLE 5.0 | ✅ 已集成 |

两款设备共享同一个 BLE SDK（`vp_sdk`，378KB JS），该 SDK 是**设备无关的通用协议层**——连接任何 Veepoo BLE 设备后，通过功能汇总包自动识别该设备支持的数据类型，无需针对不同设备型号修改代码。

---

## 数据采集能力（10 类体征数据）

| 类型 | 存储字段 | 单位 | 采集频率 |
|------|---------|------|---------|
| 心率 | 心率值、心率状态 | bpm | 每 2h 持续监测 |
| 血压 | 高压、低压、脉搏、风险等级（自动分级） | mmHg | 早中下晚各一次 |
| 血氧 | 血氧饱和度、心率 | % | 每 4h |
| 体温 | 体温、皮肤温度 | °C | 早中晚 |
| 血糖 | 血糖值（mmol/L）、餐态（空腹/餐后） | mmol/L | 空腹 + 餐后 |
| 血液成分 | 尿酸、胆固醇、甘油三酯 | mg/dL | 每日 1 次 |
| 身体成分 | 体重、BMI、体脂率、肌肉量 | kg/% | 每日 1 次 |
| 心电 | 心率、诊断结果、波形采样点数 | bpm/mV | 按需 |
| 步数 | 步数、卡路里、距离 | 步/cal/m | 累计上报 |
| 睡眠 | 入睡/醒来时间、深睡/浅睡分钟数 | 分钟 | 每日晨起读取 |

血压数据写入时自动按 AHA 标准分级：正常 / 偏高 / 高血压1级 / 高血压2级 / 危急。

---

## REST API 文档

**生产基地址**：`https://dc.ncrc.org.cn/api2`

### 数据写入

#### POST /api/health-data

小程序端自动调用，写入一条体征记录到六元 MySQL。

**请求体**：

```json
{
  "dataType": "bloodPressure",
  "data": { "systolic": 128, "diastolic": 82, "heartRate": 72 },
  "deviceId": 1
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `dataType` | string | 是 | 数据类型（11 种之一） |
| `data` | object | 是 | 体征数据 |
| `deviceId` | int | 否 | 设备 ID（关联 wearable_device 表），默认 1 |

**示例**：

```bash
# 写入血压数据
curl -X POST https://dc.ncrc.org.cn/api2/api/health-data \
  -H "Content-Type: application/json" \
  -d '{"dataType":"bloodPressure","data":{"systolic":120,"diastolic":80,"heartRate":72}}'

# 写入心率数据
curl -X POST https://dc.ncrc.org.cn/api2/api/health-data \
  -H "Content-Type: application/json" \
  -d '{"dataType":"heartRate","data":{"heartRate":72,"heartState":0}}'

# 写入体温数据
curl -X POST https://dc.ncrc.org.cn/api2/api/health-data \
  -H "Content-Type: application/json" \
  -d '{"dataType":"temperature","data":{"temperature":36.5}}'
```

### 数据查询

#### GET /api/data

查询所有已写入的体征数据。

```bash
curl https://dc.ncrc.org.cn/api2/api/data
```

#### GET /api/status

查询服务器运行状态和六元 MySQL 连接状态。

```bash
curl https://dc.ncrc.org.cn/api2/api/status
```

### 设备名册（wearable_device）

#### POST /api/device/register

按 `device_sign` UPSERT，已存在则返回现有 deviceId，不存在则 INSERT 一行并回传新 id。
小程序在 BLE 密钥认证成功后自动调用，无需手工干预。

```bash
curl -X POST https://dc.ncrc.org.cn/api2/api/device/register \
  -H "Content-Type: application/json" \
  -d '{"deviceSign":"S101_FA:BA:94:8A:70:75","type":1}'
# {"deviceId":4,"action":"created"}（首次）或 {"deviceId":4,"action":"existing"}（重连）
```

#### GET /api/device/by-sign

只查不创建，运维核对用。

```bash
curl 'https://dc.ncrc.org.cn/api2/api/device/by-sign?sign=S101_FA:BA:94:8A:70:75'
# {"id":4,"device_sign":"S101_FA:BA:94:8A:70:75","type":1}
```

---

## 数据库设计

### 生产数据库（六元 MySQL）

六元空间部署的 MySQL 数据库，服务器地址 `192.168.4.174`，数据库名 `h6dp_suifang`。

**wearable_device（设备表）**

| 列名 | 类型 | 说明 |
|------|------|------|
| id | bigint | 主键，自增 |
| device_sign | varchar(64) | 设备标识（型号_MAC地址） |
| type | int | 设备类型（1=手表, 2=戒指） |

当前数据（3 台 AMOLED 手表）：

| id | device_sign | type | 说明 |
|----|-------------|------|------|
| 1 | VP-W680_B8:27:EB:6A:F3:11 | 1 | 手表 1 号（demo） |
| 2 | VP-W680_B8:27:EB:9C:D7:4E | 1 | 手表 2 号（demo） |
| 3 | VP-W680_B8:27:EB:3B:A8:72 | 1 | 手表 3 号（demo） |
| 4 | S101_FA:BA:94:8A:70:75 | 1 | 真机测试 1 号（S101） |

新增设备由小程序首次连上 BLE 后自动调 `POST /api/device/register` 注册——无需手工 SQL。
device_sign 取值：`${BLE 设备名}_${平台 ID}`（Android=MAC，iOS=系统给的 UUID）。

**wearable_device_data（数据表）**

| 列名 | 类型 | 说明 |
|------|------|------|
| id | bigint | 自增主键 |
| deviceId | bigint | 设备 ID（1/2/3 代表不同设备），关联 wearable_device.id |
| data | text | 大 JSON：按 10 类数据分组，每类是当天历史测量数组 |
| createTime | datetime | 数据采集日期 |

每行 = **一台设备一天**的全部采集数据。

当前数据（12 行：3 台设备 × 4 天）：

| deviceId | 日期 | 行数 |
|----------|------|------|
| 1 → 手表1号 | 04-06, 07, 08, 09 | 4 行 |
| 2 → 手表2号 | 04-06, 07, 08, 09 | 4 行 |
| 3 → 手表3号 | 04-06, 07, 08, 09 | 4 行 |

**data 列大 JSON 格式**（每行包含一天的全部采集数据，按 2h 间隔）：

```json
{
  "心率": [
    {"心率值": 62, "心率状态": "静息", "采集时间": "2026-04-06T06:00:00.000Z"},
    {"心率值": 65, "心率状态": "静息", "采集时间": "2026-04-06T08:00:00.000Z"},
    {"心率值": 78, "心率状态": "活动", "采集时间": "2026-04-06T10:00:00.000Z"},
    {"心率值": 72, "心率状态": "静息", "采集时间": "2026-04-06T12:00:00.000Z"},
    {"心率值": 85, "心率状态": "活动", "采集时间": "2026-04-06T14:00:00.000Z"},
    {"心率值": 70, "心率状态": "静息", "采集时间": "2026-04-06T16:00:00.000Z"},
    {"心率值": 68, "心率状态": "静息", "采集时间": "2026-04-06T18:00:00.000Z"},
    {"心率值": 64, "心率状态": "静息", "采集时间": "2026-04-06T20:00:00.000Z"}
  ],
  "血压": [
    {"高压": 132, "低压": 85, "脉搏": 65, "风险等级": "高血压1级", "采集时间": "2026-04-06T06:00:00.000Z"},
    {"高压": 128, "低压": 82, "脉搏": 72, "风险等级": "正常", "采集时间": "2026-04-06T10:00:00.000Z"},
    {"高压": 135, "低压": 88, "脉搏": 70, "风险等级": "高血压1级", "采集时间": "2026-04-06T14:00:00.000Z"},
    {"高压": 130, "低压": 84, "脉搏": 68, "风险等级": "高血压1级", "采集时间": "2026-04-06T18:00:00.000Z"}
  ],
  "血氧": [
    {"血氧饱和度": 97, "心率": 63, "采集时间": "2026-04-06T06:00:00.000Z"},
    {"血氧饱和度": 98, "心率": 72, "采集时间": "2026-04-06T10:00:00.000Z"},
    {"血氧饱和度": 97, "心率": 70, "采集时间": "2026-04-06T14:00:00.000Z"},
    {"血氧饱和度": 98, "心率": 66, "采集时间": "2026-04-06T18:00:00.000Z"}
  ],
  "体温": [
    {"体温": 36.3, "皮肤温度": 33.5, "采集时间": "2026-04-06T06:00:00.000Z"},
    {"体温": 36.6, "皮肤温度": 34.0, "采集时间": "2026-04-06T12:00:00.000Z"},
    {"体温": 36.5, "皮肤温度": 33.8, "采集时间": "2026-04-06T18:00:00.000Z"}
  ],
  "血糖": [
    {"血糖值_mmol_L": 5.4, "餐态": "空腹", "采集时间": "2026-04-06T06:00:00.000Z"},
    {"血糖值_mmol_L": 7.2, "餐态": "餐后2小时", "采集时间": "2026-04-06T10:00:00.000Z"},
    {"血糖值_mmol_L": 6.8, "餐态": "餐后2小时", "采集时间": "2026-04-06T14:00:00.000Z"}
  ],
  "血液成分": [
    {"尿酸": 345, "胆固醇": 4.8, "甘油三酯": 1.3, "采集时间": "2026-04-06T08:00:00.000Z"}
  ],
  "身体成分": [
    {"体重": 75.2, "BMI": 23.5, "体脂率": 19.8, "肌肉量": 53.6, "采集时间": "2026-04-06T08:00:00.000Z"}
  ],
  "心电": [
    {"心率": 66, "诊断": "窦性心律", "波形采样点数": 512, "采集时间": "2026-04-06T10:00:00.000Z"}
  ],
  "步数": [
    {"步数": 3520, "卡路里": 135, "距离_米": 2500, "采集时间": "2026-04-06T12:00:00.000Z"},
    {"步数": 6840, "卡路里": 260, "距离_米": 4800, "采集时间": "2026-04-06T16:00:00.000Z"},
    {"步数": 9215, "卡路里": 348, "距离_米": 6500, "采集时间": "2026-04-06T20:00:00.000Z"}
  ],
  "睡眠": [
    {"入睡时间": "23:20", "醒来时间": "06:10", "深睡_分钟": 105, "浅睡_分钟": 290, "采集时间": "2026-04-06T06:30:00.000Z"}
  ]
}
```

10 类数据 → 10 个键，每个键是该类型当天所有测量的数组（按 2h 间隔）。每条记录都有 `采集时间` 字段。

### 开发数据库（仅本地调试用）

本地 WSL 环境有一个 MySQL 开发数据库 `smart_followup_research`，9 张表，3,395 条演示数据。详见 `database/schema.sql`。

```bash
# 本地开发数据库初始化
echo "xyf" | sudo -S service mysql start
mysql -u root -phealth123 < database/init.sql
```

---

## 快速验证（生产环境）

生产环境已部署，无需本地搭建即可验证：

```bash
# 1. 检查服务器状态
curl https://dc.ncrc.org.cn/api2/api/status

# 2. 查看已有数据
curl https://dc.ncrc.org.cn/api2/api/data

# 3. 写入一条测试数据
curl -X POST https://dc.ncrc.org.cn/api2/api/health-data \
  -H "Content-Type: application/json" \
  -d '{"dataType":"heartRate","data":{"heartRate":75,"heartState":0}}'

# 4. 再次查询确认数据已写入
curl https://dc.ncrc.org.cn/api2/api/data
```

---

## 本地开发环境

本地开发环境用于代码修改和调试，不影响生产数据。

### 环境要求

| 工具 | 版本 | 用途 |
|------|------|------|
| 微信开发者工具 | 最新稳定版 | 小程序编译调试 |
| Node.js | ≥ 16.x | 本地数据同步服务（可选） |
| MySQL | 8.0+ | 本地开发数据库（可选） |
| 支持 BLE 的手机 | Android 5.0+ / iOS 10+ | 真机测试 |

### 环境搭建

```bash
# 1. 克隆项目
git clone https://github.com/qqyjx/suifang.git
cd WeChat_Mini_Program_Ble_SDK

# 2. 在微信开发者工具中导入项目
#    路径：code/demo/WeiXinSDKTSDemo
#    AppID：wxbc5453a4c53dbee8
#    勾选"不校验合法域名" + "增强编译"
#    基础库 ≥ 3.8.9
```

### WSL 开发工作流

项目运行在 WSL 中，配合 Windows 版微信开发者工具：

```
WSL VSCode 编辑代码 → 保存 → Windows 微信开发者工具自动热重载 → 查看效果/真机调试
```

微信开发者工具导入路径：
```
\\wsl$\Ubuntu\home\qq\WeChat_Mini_Program_Ble_SDK\code\demo\WeiXinSDKTSDemo
```

### 生产 vs 开发环境切换

`dataStorage.ts` 中的服务器地址控制数据流向：

```typescript
// 当前配置（生产环境）
const WSL_SERVER_URL = 'https://dc.ncrc.org.cn/api2';

// 切换为本地开发（需启动本地 Node.js 服务器）
// const WSL_SERVER_URL = 'http://127.0.0.1:3000';
```

本地 Node.js 开发服务器启动：
```bash
echo "xyf" | sudo -S service mysql start
cd scripts && npm install && node health-data-server.js
# 访问 http://localhost:3000/api/status 验证
```

---

## BLE 连接与数据采集

### 设备连接流程

```typescript
import { veepooBle, veepooFeature } from '../../libs/vp_sdk/index';

// 1. 扫描设备
veepooBle.veepooWeiXinSDKStartScanDeviceAndReceiveScanningDevice((device) => {
  console.log('发现设备:', device.name, device.mac);
});

// 2. 连接设备
veepooBle.veepooWeiXinSDKBleConnectionServicesCharacteristicsNotifyManager(
  { deviceId: 'XX:XX:XX:XX:XX:XX' },
  (result) => {
    if (result.success) {
      // 3. 密钥认证（必须在数据采集前完成）
      veepooFeature.veepooBlePasswordCheckManager();
    }
  }
);
```

### 数据采集与自动上传

采集的数据通过 `dataStorage` 服务自动保存到本地并上传到生产服务器。

```typescript
import { dataStorage } from '../../services/dataStorage';

// 保存心率（自动 POST 到 dc.ncrc.org.cn/api2）
await dataStorage.saveHeartRateData({ heartRate: 72, heartState: 0 });

// 保存血压
await dataStorage.saveBloodPressureData({ systolic: 120, diastolic: 80 });

// 保存血氧
await dataStorage.saveBloodOxygenData({ bloodOxygen: 98 });

// 保存体温
await dataStorage.saveTemperatureData({ temperature: 36.5 });

// 保存步数
await dataStorage.saveStepData({ step: 8500, calorie: 320, distance: 6200 });

// 保存睡眠
await dataStorage.saveSleepData({ deepSleepMinutes: 120, lightSleepMinutes: 180 });
```

完整 SDK API 文档（5,600+ 行）：`docs/VeepooWeiXinSDK使用文档.md`

---

## 小程序本地存储

小程序采集的数据先写入手机本地文件系统，再异步上传到服务器。断网时数据不丢失。

```
{wx.env.USER_DATA_PATH}/health_data/
├── 2026-03-24/
│   ├── heartRate.json
│   ├── bloodOxygen.json
│   ├── bloodPressure.json
│   ├── temperature.json
│   └── ...
└── 2026-03-25/
    └── ...
```

每个 JSON 文件包含当日该类型的所有采集记录。

### 同步队列（断网重传）

- 服务器 HTTP 同步失败时，整条 payload 进 `wx.storage` 的 `sync_pending_queue`（最多 500 条 LRU）
- 触发刷队列：① App.onShow（回到前台）② `wx.onNetworkStatusChange` 网络从离线恢复
- 实现位置：`services/dataStorage.ts:enqueuePending` / `flushPending` / `postOnce`

---

## 体验版 vs 正式版

代码层用 `services/env.ts` 单一开关区分；下次正式上线前还需补隐私协议、医疗免责、微信审核。

```ts
// services/env.ts
export const ENV = {
  IS_TEST_BUILD: true,             // 体验版唯一开关，正式版改 false
  BUILD_TAG: 'test-2026.04.27',
  API_BASE: 'https://dc.ncrc.org.cn/api2',
  SUPPORTED_DEVICE_PREFIXES: ['VP-', 'S101', 'VPR'],  // BLE 扫描白名单前缀
  MIN_BLE_RSSI: -75,               // 弱信号过滤门槛
};
```

| 影响范围 | 体验版 (`IS_TEST_BUILD=true`) | 正式版 (`false`) |
|---|---|---|
| 首页右上角橙色"测试版"角标 | 显示 | 隐藏 |
| vConsole 调试面板 | 自动开启 | 关闭 |
| 后端 API 地址 | 本字段统一 | 本字段统一 |

**正式版打包流程**（待药监/法务评估完成后再做，本次不在范围）：
1. `services/env.ts` 改 `IS_TEST_BUILD: false` + 更新 `BUILD_TAG`
2. 补充 `pages/protocol/index`（用户协议）+ `pages/privacy/index`（隐私政策）
3. `app.json` 加 `permission` 字段（蓝牙、定位授权说明）
4. 微信开发者工具 → 上传 → 微信公众平台 → 提交审核（1-7 天）

---

## 交付与运营

### 文档分工（看哪个文档要看清角色）

| 角色 | 看哪份文档 | 路径 |
|---|---|---|
| 患者本人 | 怎么扫码 / 连蓝牙 / 故障 | [`医院交付使用指南`](https://qqyjx.github.io/suifang/docs/%E5%8C%BB%E9%99%A2%E4%BA%A4%E4%BB%98%E4%BD%BF%E7%94%A8%E6%8C%87%E5%8D%97.html) |
| 医院 PM / 护士 / 项目运营 | 接入新患者 5 步 SOP / 加白名单 / Veepoo 兼容性 | [`新设备接入手册`](https://qqyjx.github.io/suifang/docs/%E6%96%B0%E8%AE%BE%E5%A4%87%E6%8E%A5%E5%85%A5%E6%89%8B%E5%86%8C.html) |
| 工程师 | 完整交付规划 / 微信发布 SOP / 真机验证 | `docs/4.25plan.md`(已废弃) + [`真机测试 checklist`](https://qqyjx.github.io/suifang/docs/%E7%9C%9F%E6%9C%BA%E6%B5%8B%E8%AF%95checklist.html) |

### 体验版交付物（4.27）

详见 `docs/4.25plan.md` 的交付清单与发布 SOP（Step 10），以及 `docs/医院交付使用指南.md`。

```
医院 PM 收到三件套：
1. 手表硬件      ← 由 Veepoo 厂家或医院自购
2. 体验版二维码 (PNG)  ← 我们从微信公众平台导出
3. 使用指南 (PDF)      ← docs/医院交付使用指南.md 导出
```

### 体验成员名单（≤ 100 人硬限制）

微信小程序体验版**所有体验成员（含开发者）总计不能超过 100**。100+ 患者必须走正式版审核。

加白名单 SOP（一次性手动操作）：
1. 登录 https://mp.weixin.qq.com（AppID 主体微信号）
2. 左侧菜单 → 管理 → 成员管理 → 体验成员 → 添加成员
3. 输入要使用者的微信号（**不是手机号、不是姓名**）
4. 对方微信会收到加入通知，扫体验版二维码即可使用

### 受试者编号 ↔ 设备绑定（不归我们）

当前 `services/dataStorage.ts:syncToServer` 把 `patientId` 硬编码为 1。受试者真实绑定关系由六元/医生端在 `patients` 表手工建立，本次小程序范围内不实现。详见 `docs/4.25plan.md` "交付窗口 + 已确认范围"。

---

## 数据导出

```typescript
import { dataExport } from '../../services/dataExport';

// 导出所有数据
const filePath = await dataExport.exportAllData();

// 导出指定日期范围
const filePath = await dataExport.exportDataByDateRange('2026-03-01', '2026-03-15');

// 微信分享
await dataExport.shareViaWeChat(filePath);

// 查看数据统计
const stats = dataExport.getDataStatistics();
```

---

## 故障排除

### BLE 连接问题

- **模拟器中扫描不到设备**：正常现象，BLE 必须使用真机测试
- **扫描不到设备**：确保设备已开机，手机已开启蓝牙和定位权限
- **连接后无法采集**：必须先完成密钥认证 `veepooFeature.veepooBlePasswordCheckManager()`

### 数据上传失败

- **网络错误**：检查手机网络是否正常，生产地址 `dc.ncrc.org.cn` 是否可达
- **服务器无响应**：`curl https://dc.ncrc.org.cn/api2/api/status` 检查服务状态
- **数据已缓存**：即使上传失败，数据仍保存在小程序本地存储中，不会丢失

### 小程序编译问题

- **域名校验错误**：开发者工具必须勾选"不校验合法域名"
- **编译报错**：确保基础库 ≥ 3.8.9，已启用 TypeScript 和 LESS 编译插件

---

## 项目结构

```
WeChat_Mini_Program_Ble_SDK/
├── README.md                          # 本文件
├── 智能随访说明.md                     # 完整系统需求说明
├── SDK功能清单.md                      # SDK 全部功能清单
├── 开发环境使用指南.md                  # WSL + 微信开发者工具配置
│
├── code/demo/WeiXinSDKTSDemo/         # 微信小程序主项目
│   └── miniprogram/
│       ├── pages/                     # 57 个功能页面
│       ├── services/
│       │   ├── dataStorage.ts         # 数据存储 + HTTP 上传（→ dc.ncrc.org.cn）
│       │   ├── dataExport.ts          # 数据导出（JSON + 微信分享）
│       │   └── httpSync.ts            # 遗留代码，不使用
│       └── types/
│           └── healthData.ts          # 11 种体征数据 TypeScript 接口定义
│
├── libs/
│   ├── vp_sdk/                        # Veepoo BLE SDK（378KB JS，设备无关协议层）
│   └── jieli_sdk/                     # 杰理 BLE SDK（OTA/表盘传输）
│
├── scripts/
│   ├── health-data-server.js          # 公司服务器上运行的数据服务（含六元 MySQL 对接）
│   └── package.json                   # Node.js 依赖
│
├── database/
│   ├── schema.sql                     # 本地开发数据库表结构
│   └── init.sql                       # 本地开发数据库（含演示数据）
│
└── docs/
    ├── SDK数据采集全景.md              # 数据流水线详细文档
    └── VeepooWeiXinSDK使用文档.md     # SDK 完整 API 文档（5,600+ 行）
```

---

## SDK 版本历史

| 版本 | 日期 | 更新内容 |
|------|------|---------|
| V1.1.16 | 2026/01/09 | 新增配置 4G 服务信息接口 |
| V1.1.15 | 2026/01/04 | 修复已知问题 |
| V1.1.14 | 2025/12/29 | 新增 ZT163 常灭屏功能接口 |
| V1.1.13 | 2025/12/16 | 新增 JH58 动态血压监测 |
| V1.1.12 | 2025/12/05 | 血糖风险等级解析优化 |
| V1.1.11 | 2025/11/19 | ECG 波形兼容性增强 |

---

## 运行环境兼容性

| 平台 | 支持状态 | 备注 |
|------|---------|------|
| Android | ✅ 完全支持 | Android 5.0+ |
| iOS | ✅ 完全支持 | iOS 10+ |
| HarmonyOS 4.0 | ✅ 支持 | 兼容 Android BLE API |
| HarmonyOS Next | ⚠️ 部分支持 | 部分功能需适配 |

---

## 注意事项

1. **蓝牙测试需真机**：模拟器不支持 BLE 通信
2. **密钥认证**：连接设备后必须完成密钥认证才能采集数据
3. **框架限制**：仅支持微信原生 + TypeScript，不兼容 uni-app/Taro
4. **数据合规**：采集的体征数据涉及个人健康信息，需遵守隐私保护法规
5. **OTA 与表盘**：固件升级和表盘传输仅支持杰理芯片设备

---

## 当前状态

| 维度 | 状态 |
|------|------|
| 生产数据服务 | ✅ 已部署（dc.ncrc.org.cn/api2 → 六元 MySQL） |
| 数据管道代码 | ✅ 全链路打通（BLE → 小程序 → HTTPS → MySQL） |
| demo 数据验证 | ✅ 17 条样例数据已写入生产库 |
| 真机 BLE 测试 | ❌ 待测试（需用手表采集真实数据） |
