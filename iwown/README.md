# iwown 4G 智能手表 — 接入工作区

随访项目第 **2 款手表**（iwown 4G 手表）的接入工作区，与 S101（Veepoo BLE 手表）**完全分开**。
本 README 仅本地导航。

> 文档源：https://api8.iwown.com/iot_platform/restful.html （Sphinx 静态站，正文/proto/示例已全量下载）
> 建立：2026-05-31

## 🔑 与 S101 的根本区别（架构相反）

| 维度 | S101 (Veepoo) | iwown 4G（本工作区） |
|------|---------------|----------------------|
| 联网 | BLE 蓝牙（无 4G） | **自带 4G 蜂窝** |
| 数据桥 | 微信小程序 | **无桥，手表直连** |
| 协议 | Veepoo 私有 BLE | **二进制 protobuf + 自定义帧（HTTP）** |
| 客户端开发 | 重（57 页小程序） | **零**（用厂商固件） |
| 服务端 | Python 收 JSON `:3000` | **Python 收二进制 `:8099`**（本工作区已建） |
| 设备指向我们 | 小程序配 URL | **域名烧录**（Android 调试 APK 蓝牙改址） |

一句话：**S101 端上桥接，iwown 云端直收**。iwown 不写 App，但要部署一个解二进制 protobuf 的服务。

## 数据链路

```
iwown 手表 ──4G POST 二进制──→ dc.ncrc.org.cn/iwown/ ──反代──→ 192.168.4.104:8099 ──→ 六元 MySQL
                              (六元 nginx 加 location)   (iwown_server.py)     (iwown_data 表)
```
与 S101 共用 192.168.4.104 + dc.ncrc.org.cn 反代 + 六元 MySQL，iwown 独立进程/端口 + 新 location + 新表。

## 目录

```
iwown/
├── README.md                          ← 本文件
├── docs/
│   └── iwown-integration-plan.html    ← 集成方案（协议全解 + 建设状态）
├── server/                            ← ✅ 接收服务（已建，自测通过）
│   ├── iwown_server.py / iwown_parser.py / theproto/
│   ├── schema.sql / requirements.txt / iwown.service
│   ├── nginx-iwown.conf               ← 给六元同事的反代片段
│   ├── deploy-iwown.ps1               ← PowerShell 部署脚本
│   └── README.md                      ← 服务说明 + 部署 + 待确认清单
└── reference/                         ← iwown 官方原始资料
    ├── raw/        11 个 .rst 文档源
    ├── proto/      27 个 .proto 定义
    ├── sample-python/  官方 Flask 示例（移植来源）
    └── downloads/  proto.zip + 4gdata-python.zip 存档
```

## 已确认协议（依官方示例 + proto 核对，非推测）

- 帧：`device_id(15B ASCII)` + 多帧 `prefix(0x4454)+length(u16小端)+crc(u16)+opt(u16)+protobuf`
- opt：0x80 健康(分钟级) / 0x0A 实时(步数距离卡路里GNSS) / 0x12 报警
- 6 端点：pb/alarm 二进制，call_log/deviceinfo/status JSON，health/sleep 设备拉取；成功回 0x00
- 换算：distance/calorie ÷10；压力=100−fatigue；体温位运算 ÷100

## 当前状态

| 环节 | 状态 |
|------|------|
| 文档/proto/示例下载 | ✅ |
| 接收服务（解帧+解 protobuf+入库+落盘兜底） | ✅ 已建，框架自测通过 |
| 解帧逻辑自测 | ✅（device_id/多帧/0x02/0x03/降级 全绿） |
| 部署到 192.168.4.104 | ⏳ 待 git 落位 + PowerShell 部署 |
| 六元 nginx 反代 | ⏳ 待同事加 `/iwown/` location（片段已备 `server/nginx-iwown.conf`） |
| 域名烧录真机 | ⏳ 待反代+部署后，用 APK 把手表指向我们 |
| CRC 校验 / 字段语义 | ⚠️ 待真机样本确认（详见 server/README.md） |

## 下一步

1. **git 落位**（并入 qqyjx/suifang 仓）→ 2. **PowerShell 部署** → 3. **六元加反代** → 4. **域名烧录真机** → 5. **抽 raw 核字段 + 绑患者 + 看板**
