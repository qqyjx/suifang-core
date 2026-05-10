# 智能随访系统 — 可穿戴设备体征数据采集平台

基于 Veepoo BLE SDK 的医疗级智能穿戴设备数据采集平台。**当前生产版本 5.06-v10**，已部署到生产环境，数据通过 `https://dc.ncrc.org.cn/api2` 写入六元空间 MySQL 数据库。

---

## 🎬 在线演示（GitHub Pages）

| 演示 | 在线链接 | 说明 |
|---|---|---|
| 🩺 极简患者界面 | [patient-demo.html](https://qqyjx.github.io/suifang/prototype/patient-demo.html) | 患者本人用，仅"连接 / 断开"一个按钮，三态切换（未连接 / 连接中 / 已连接） |
| 📋 医护管理界面 | [index.html](https://qqyjx.github.io/suifang/prototype/index.html) | 4-Tab 架构：首页 / 测量 / 数据 / 我的（设计稿） |

> 浏览器/手机直接打开。`Space` 切状态、`R` 重置。

---

## 📚 在线文档

所有文档均为 GitHub Pages 自动渲染 HTML，移动端友好，中文优化。

| 文档 | 受众 | 链接 |
|---|---|---|
| 医院交付使用指南 | 患者本人 | [打开](https://qqyjx.github.io/suifang/docs/%E5%8C%BB%E9%99%A2%E4%BA%A4%E4%BB%98%E4%BD%BF%E7%94%A8%E6%8C%87%E5%8D%97.html) |
| 新设备接入手册 | 医院 PM / 护士 | [打开](https://qqyjx.github.io/suifang/docs/%E6%96%B0%E8%AE%BE%E5%A4%87%E6%8E%A5%E5%85%A5%E6%89%8B%E5%86%8C.html) |
| 真机测试 checklist | 工程师 | [打开](https://qqyjx.github.io/suifang/docs/%E7%9C%9F%E6%9C%BA%E6%B5%8B%E8%AF%95checklist.html) |
| 功能测试-手表数据上传 | 第三方测试 | [打开](https://qqyjx.github.io/suifang/docs/%E5%8A%9F%E8%83%BD%E6%B5%8B%E8%AF%95-%E6%89%8B%E8%A1%A8%E6%95%B0%E6%8D%AE%E4%B8%8A%E4%BC%A0.html) |
| 服务器运维 | 运维 | [打开](https://qqyjx.github.io/suifang/docs/%E6%9C%8D%E5%8A%A1%E5%99%A8%E8%BF%90%E7%BB%B4.html) |
| FAQ-常见问题清单 | 全员 | [打开](https://qqyjx.github.io/suifang/docs/FAQ-%E5%B8%B8%E8%A7%81%E9%97%AE%E9%A2%98%E6%B8%85%E5%8D%95.html) |
| SDK 数据采集全景 | 工程师 | [打开](https://qqyjx.github.io/suifang/docs/SDK%E6%95%B0%E6%8D%AE%E9%87%87%E9%9B%86%E5%85%A8%E6%99%AF.html) |
| Veepoo SDK 使用文档（5600+ 行） | 工程师 | [打开](https://qqyjx.github.io/suifang/docs/VeepooWeiXinSDK%E4%BD%BF%E7%94%A8%E6%96%87%E6%A1%A3.html) |
| 业务逻辑设计 v10 | 产品 + 工程师 | [打开](https://qqyjx.github.io/suifang/docs/business-logic-v10.html) |
| UI 原型 v10 设计稿 | 产品 + 设计 | [打开](https://qqyjx.github.io/suifang/docs/ui-prototype-v10.html) |
| Pencil + Claude Code 工作流 | 设计 + 工程师 | [打开](https://qqyjx.github.io/suifang/docs/pencil-claude-workflow.html) |

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
1. 微信开发者工具打开 `code/demo/WeiXinSDKTSDemo`
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
├── code/demo/WeiXinSDKTSDemo/       小程序源码
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
