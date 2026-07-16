# 智能随访系统 — 可穿戴设备体征数据采集平台

医疗级智能穿戴设备数据采集平台。**当前生产版本 5.06-v10**，已部署到生产环境，数据写入六元空间 MySQL 数据库。

本仓已整合 **3 款设备**，三者共用同一台服务器 (`192.168.4.104`) + 同一个反代 (`dc.ncrc.org.cn`) + 同一套六元 MySQL，但**接入方式与服务端各不相同**：

| 设备 | 联网方式 | 数据桥 | 服务端 | 端口 / 反代 location | 数据库表 | 看板 |
|---|---|---|---|---|---|---|
| **S101** (Veepoo) | BLE 蓝牙 | 微信小程序 | `scripts/health_server.py` (收 JSON) | `:3000` → `/api2/` | `wearable_device*` | [S101 看板](https://qqyjx.github.io/suifang/prototype/data-dashboard.html) |
| **诊脉仪** | 导出 .zip | 浏览器本地解析 | **复用** `scripts/health_server.py` (加 `/api/zhenmaiyi/*` 端点) | `:3000` → `/api2/` | `zhenmaiyi` | [诊脉仪看板](https://qqyjx.github.io/suifang/prototype/pulse-dashboard.html) |
| **iwown 4G** | 4G 蜂窝直连 | 无桥，设备直传 | **独立** `iwown/server/iwown_server.py` (收二进制 protobuf) | `:8099` → `/iwown/` | `iwown_device` / `iwown_data` | [iwown 看板](https://qqyjx.github.io/suifang/prototype/iwown-dashboard.html) |

> **为什么诊脉仪没有独立 server**：它是浏览器解析 + 偶发上传 (拖 zip→解析→点入库)，在现有 Python 服务上挂两个 JSON 端点即可，无需单独进程。
> **为什么 iwown 要独立 server**：它是 4G 手表 24h 主动推**二进制 protobuf 流**，处理逻辑 (解帧 + CRC + protobuf 反序列化) 与收 JSON 完全不同，单独进程隔离更稳。详见 [iwown/](iwown/)。

```
                          ┌─ S101 手表 ──BLE──► 微信小程序 ─┐
                          │                                 │ HTTPS POST (JSON)
                          │  诊脉仪 ──导出.zip──► 浏览器解析 ─┤
   六元 MySQL ◄───────────┤                                 ▼
   (h6dp_suifang)         │              dc.ncrc.org.cn/api2 ──► 192.168.4.104:3000 (health_server.py)
                          │
                          └─ iwown 4G 手表 ──4G HTTP POST(二进制 protobuf)──►
                                          dc.ncrc.org.cn/iwown ──► 192.168.4.104:8099 (iwown_server.py)
```

---

## 🎬 在线演示与看板（GitHub Pages）

| 链接 | 用途 | 说明 |
|---|---|---|
| 📊 [**S101 数据看板**](https://qqyjx.github.io/suifang/prototype/data-dashboard.html) | 给医院看数据 (S101 手表) | **实时**拉 `/api/data`，按门诊号聚合显示，30s 自动刷新 + 手动刷新按钮 |
| 🩺 [**诊脉仪数据看板**](https://qqyjx.github.io/suifang/prototype/pulse-dashboard.html) | 给医院看数据 (诊脉仪) | 上传诊脉仪导出的 .zip，**浏览器本地解析**体质+脉诊+答题+脉象图+报告 PDF；预览确认后可一键**入库到六元** MySQL (zhenmaiyi 表) |
| 📶 [**iwown 4G 手表看板**](https://qqyjx.github.io/suifang/prototype/iwown-dashboard.html) | 给医院看数据 (iwown 4G 手表) | 拉 `/iwown/api/iwown/list`，4G 手表直传二进制 protobuf 入库六元 (iwown_data 表)，详见 [iwown/](iwown/) |
| 🩹 [**随访平台 1.0**](https://qqyjx.github.io/suifang/prototype/platform.html) | 以患者为中心的随访工作台 | 患者建档/三链路设备绑定/跨设备体征趋势；走 `/api/platform/*`（需服务端部署 v11+），设计见 [设计方案](https://qqyjx.github.io/suifang/docs/%E9%9A%8F%E8%AE%BF%E5%B9%B3%E5%8F%B01.0%E8%AE%BE%E8%AE%A1%E6%96%B9%E6%A1%88.html) |
| 🩺 [极简患者界面](https://qqyjx.github.io/suifang/prototype/patient-demo.html) | 患者本人界面演示 | 仅"连接/断开"一个按钮，三态切换 |
| 📋 [医护管理界面](https://qqyjx.github.io/suifang/prototype/index.html) | 4-Tab 架构原型 | 首页 / 测量 / 数据 / 我的 |

> 浏览器/手机直接打开即可。数据看板需要能访问 `dc.ncrc.org.cn`（公网就行，不需要 VPN）。

---

## 📚 在线文档

**统一入口**：[📚 智能随访 · 文档中心](https://qqyjx.github.io/suifang/docs/) — 左侧分类 sidebar + 右侧 iframe，URL hash 记忆当前文档，移动端友好。

涵盖 14 份文档，分三大类：

- **使用与流程**：随访平台使用说明 / 医院交付使用指南 / 新设备接入手册 / FAQ 常见问题清单
- **测试与运维**：真机测试 checklist / 功能测试-手表数据上传 / 服务器运维
- **开发与设计**：**随访平台 1.0 设计方案** / 智能穿戴设备调研与对接 / SDK 数据采集全景 / Veepoo SDK 使用文档 / 业务逻辑设计 v10 / UI 原型 v10 / Pencil+Claude Code 工作流

> 直接打开 [`docs/`](https://qqyjx.github.io/suifang/docs/) 也可以 (GitHub Pages 默认入口)。原 11 份独立 .html 文件未删, 老链接仍可用。

---

## 数据链路

```
手表（Veepoo S101 / R04）
    ↓ BLE 蓝牙（私有协议，Veepoo SDK 解析）
微信小程序（v10 极简患者界面，仅连/断按钮）
    ↓ HTTPS POST
dc.ncrc.org.cn/api2（六元外网入口）
    ↓ 反向代理
192.168.4.104（公司服务器，Python health_server.py）
    ↓ pymysql
192.168.4.174:3306（六元 MySQL，h6dp_suifang 库）
    └─ wearable_device（设备名册）
    └─ wearable_device_data（一台设备一行 + 大 JSON 汇总）
```

**为什么必须用手机**：手表只支持 BLE 蓝牙，没有 WiFi/4G/以太网；BLE 协议被 Veepoo 加密，必须用厂商 SDK；Veepoo SDK 仅 3 个版本（微信小程序、Android、iOS），**没有 PC 桌面 SDK**，所以只能用手机当 BLE 桥。

---

## 当前版本（5.06-v10）

| 维度 | 状态 |
|---|---|
| 客户端 | 极简患者界面：仅"连接智能手表 / 断开连接"一个大按钮 + 设备状态卡 |
| 隐藏管理员入口 | 连续点 5 次顶部 logo（3 秒内）→ 显示门诊号输入 + 25+ 子页面 + 调试 |
| 患者识别 | 门诊号字段，写入大 JSON 每条记录的 `门诊号` 字段，**不动数据库 schema** |
| 服务端 | Python `health_server.py`，CentOS 7 + pymysql 标准库，无外部依赖 |
| 数据库 | 一台设备一行 + 大 JSON 汇总，按 `deviceId` 切片 |
| 上线渠道 | 微信小程序体验版（≤ 100 体验成员），二维码常驻 |

---

## REST API（当前生产可用）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/status` | 服务状态 + MySQL 连接 |
| GET | `/api/data` | 查询所有设备数据（可选 `?patientNo=xxx` 过滤） |
| POST | `/api/health-data` | 写入一条体征记录（含门诊号字段） |
| POST | `/api/device/register` | 按 mac (优先) 或 device_sign UPSERT 设备名册 |
| POST | `/api/device/merge` | 合并 wearable_device_data 两行（运维用） |
| GET | `/api/device/by-sign?sign=...` | 按 sign 查 wearable_device |
| DELETE | `/api/device/:id` | 删除设备 + 联动删数据（运维用） |

**外网验证**：
```bash
curl https://dc.ncrc.org.cn/api2/api/status
curl 'https://dc.ncrc.org.cn/api2/api/data?patientNo=0012865682'
```

---

## 生产环境配置

| 配置项 | 值 |
|---|---|
| 外网入口 | `https://dc.ncrc.org.cn/api2` |
| 反向代理 | `192.168.4.104`（公司服务器，CentOS 7） |
| MySQL | `192.168.4.174:3306/h6dp_suifang`（六元数据库） |
| 数据表 | `wearable_device`（设备）/ `wearable_device_data`（数据） |
| 微信 AppID | `wxbc5453a4c53dbee8` |

---

## 开发与发布

### 开发
1. 微信开发者工具打开 `mini_program_code/demo/WeiXinSDKTSDemo`
2. AppID `wxbc5453a4c53dbee8`，勾"不校验合法域名" + "增强编译"，基础库 ≥ 3.8.9
3. 改 `services/env.ts` 的 `BUILD_TAG` 跟版本号

### 发布体验版（≤ 100 体验成员，当前阶段）
1. 微信开发者工具 → **上传**（版本号填 `v10`）
2. mp.weixin.qq.com → 管理 → 版本管理 → 选刚上传的为「**体验版**」
3. 客户重扫旧二维码即生效（PNG 不变）

### 服务端部署
```bash
# 在能 SSH 192.168.4.104 的环境跑
bash scripts/redeploy.sh
```

更详细流程见 [服务器运维文档](https://qqyjx.github.io/suifang/docs/%E6%9C%8D%E5%8A%A1%E5%99%A8%E8%BF%90%E7%BB%B4.html)。

---

## 项目结构

```
WeChat_Mini_Program_Ble_SDK/
├── README.md                       本文件
├── mini_program_code/demo/WeiXinSDKTSDemo/       小程序源码
│   └── miniprogram/
│       ├── pages/index/             首页（v10 极简患者界面 + 高级模式）
│       ├── pages/...                25+ 子页面（高级模式入口）
│       ├── services/
│       │   ├── env.ts               BUILD_TAG=5.06-v10
│       │   ├── bleHub.ts            BLE 全局事件中心
│       │   └── dataStorage.ts       数据上传 + pending 队列
│       └── miniprogram_dist/        Veepoo SDK（厂商私有，378KB）
├── scripts/
│   ├── health_server.py             生产服务端（Python）
│   ├── md2html.py                   md→html 转换脚本
│   └── redeploy.sh                  服务端部署
├── docs/                            HTML 文档（GitHub Pages 渲染）
├── prototype/                       UI 原型（GitHub Pages 渲染）
└── database/                        本地开发用 schema（生产不走这）
```

---

## 文档说明

所有 `docs/` 目录下文件均为 HTML，由 `scripts/md2html.py` 从 Markdown 源生成（脚本保留，源 .md 已不入仓库）。需要修改文档时：

1. 用任何编辑器打开 `docs/*.html` 直接改（HTML 源可读）
2. 或重新写 .md 后跑 `python3 scripts/md2html.py` 重新生成
3. commit + push，GitHub Pages 1-2 分钟自动更新

---

## License

Apache 2.0
