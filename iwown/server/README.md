# iwown 4G 手表接收服务

iwown 4G 手表自带蜂窝网络，**直接 HTTP POST 二进制 protobuf** 到本服务（无小程序桥）。
本服务解帧 + 解 protobuf + 入库六元 MySQL，与 S101 的 `health_server.py` 并列、独立进程。

```
iwown 手表 ──4G POST 二进制──→ dc.ncrc.org.cn/iwown/ ──反代──→ 192.168.4.104:8099 ──→ 六元 MySQL
                              (六元 nginx 加 location)   (iwown_server.py)      (iwown_data 表)
```

## ✅ 生产部署状态（2026-05-31 已上线）

- **服务**：`192.168.4.104:8099`，systemd `iwown` 已 active + 开机自启，`/api/status` 返回 `protobuf:true` + `mysql:connected`
- **反代**：nginx 在**负载机 `192.168.4.136`**（非 .104！），配置 `/etc/nginx/conf.d/vhost_dc.ncrc.org.cn.conf`，`/api2/` 后加了 `location /iwown/` → `http://192.168.4.104:8099/`
- **外网入口**：`https://dc.ncrc.org.cn/iwown/api/status` 已验证通
- **DB 密码**：取自 S101 `scripts/health_server.py`，base64 写入 `/opt/iwown/iwown.env`（chmod 600，不入 git）

### ⚠️ protobuf 实现冲突坑（已解，必读）
本仓 `theproto/*_pb2.py` 是旧版 protoc 生成，与服务器上 protobuf 5.29.6 的默认 **C++ 实现**不兼容（启动报 descriptor 错、`protobuf:false`）。
解法：`iwown.service` 里加了 `Environment="PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python"` 强制纯 python 实现，仅作用本服务，不影响 S101。
**这行已在 git 里**，下次 `deploy-iwown.ps1` 会自动带上，无需手动加。

## 文件

| 文件 | 作用 |
|------|------|
| `iwown_server.py` | 主服务（stdlib http.server + pymysql），6 端点 + 建表 + 入库 + 落盘兜底 |
| `iwown_parser.py` | 解帧 + 按 opt 解 protobuf（移植官方 Flask 示例，用 MessageToDict 无损解码） |
| `theproto/` | protobuf 编译产物 `*_pb2.py`（protoc 5.27.2，从官方示例拷入） |
| `schema.sql` | 建表 SQL（服务启动会自动建，此文件备查） |
| `requirements.txt` | protobuf>=4.25 + PyMySQL |
| `iwown.service` | systemd 单元（端口 8099 + 六元 MySQL 环境变量） |
| `nginx-iwown.conf` | **给六元同事**的 nginx location 片段 |
| `deploy-iwown.ps1` | PowerShell 部署脚本（WSL 不通 .104，走 Windows ssh/scp + VPN） |

## 端点

| 端点 | body | 必需 | 处理 |
|------|------|------|------|
| `POST /pb/upload` | 二进制 | ✅ | opt 0x80 健康（分钟级）/ 0x0A 实时（步数距离卡路里 GNSS） |
| `POST /alarm/upload` | 二进制 | ✅ | opt 0x12 报警 |
| `POST /call_log/upload` | JSON | ✅ | SOS + 通话记录 |
| `POST /deviceinfo/upload` | JSON | 可选 | 设备信息 |
| `POST /status/notify` | JSON | 可选 | 在线/离线 |
| `GET /health/sleep` | →JSON | 可选 | 设备拉睡眠结果（**算法未接，暂返 10404**） |
| `GET /api/status` | →JSON | — | 健康检查（mysql/protobuf 状态） |
| `GET /api/iwown/list` | →JSON | — | 最近数据（给看板） |

二进制端点处理完返回**单字节 0x00**（成功）/ 0x02（长度不足）/ 0x03（帧头错），与官方一致。

## 不丢数据三重保障

1. 收到字节**先 append 到当日 fallback 文件**（`/opt/iwown/fallback/iwown-YYYY-MM-DD.log`，raw hex），后续任何失败都不丢原始数据
2. protobuf 不可用 / 单帧解码失败 → 仍按 `raw_hex` 入库（`decoded_json=null`），可后续重处理
3. DB 失败 → 数据已在 fallback 文件，仍回 `0x00` ack 设备，**避免重传风暴**（错误进日志）

## 部署（六元服务器 192.168.4.104）

**前提**：本服务部署需 ① 六元同事加 nginx 反代（见 `nginx-iwown.conf`）② 域名烧录把手表指向我们。

```powershell
# Windows PowerShell, 先连公司 VPN
cd <iwown>\server
.\deploy-iwown.ps1
```
脚本做：建 `/opt/iwown` → scp 代码 + theproto → pip 装依赖 → 装 systemd 启动 → 探活 `:8099/api/status`。

验证：
```powershell
# 内网直连
ssh root@192.168.4.104 "curl -sS http://127.0.0.1:8099/api/status"
# 外网(反代加好后)
curl https://dc.ncrc.org.cn/iwown/api/status
```
`/api/status` 看 `mysql:connected` + `protobuf:true` 即正常。

## ✅ 已确认（依官方示例 + proto 核对）

- 帧格式：`device_id(15B ASCII)` + 多帧 `prefix(0x4454)+length(u16小端)+crc(u16)+opt(u16小端)+payload`
- opt：0x80 `HisNotification`、0x0A `OM0Report`、0x12 `Alarm_infokConfirm`
- 换算：distance/calorie ÷10；压力=100−fatigue；体温 esti_arm 高/低 16 位 ÷100
- 端点 body 类型（pb/alarm 二进制；call_log/deviceinfo/status JSON）
- 成功返回 0x00

## ⚠️ 待真机/向 iwown 确认（不杜撰）

1. **CRC 算法**：官方示例**提取 crc 字段但未校验**，算法（多项式/初值）文档未明给。本服务当前**不校验 CRC**（与示例一致），先收数据。需要校验时向 iwown 要算法。
2. **device_id → 患者绑定**：15 字节 device_id 是设备，类比 S101 的"门诊号"。需后台在 `iwown_device.patient_no` 绑定（绑定方式待定：扫码/管理界面/随设备登记）。
3. **字段语义/单位**：心率/血氧/血压/体温的具体单位与边界，需真机一条真实样本核对（当前抽列逻辑依示例，raw_hex 已兜底可重算）。
4. **JSON 端点结构**：call_log/deviceinfo/status 的 JSON 字段官方未给完整 schema，当前整包存 `decoded_json`，拿到真实样本后再抽列。
5. **睡眠算法**：`/health/sleep` 需调 iwown 睡眠算法 API（calculation.html#id4），当前暂返 10404。
6. **protobuf 运行时兼容**：`theproto/*_pb2.py` 为 protoc 5.27.2 生成；部署机 protobuf 若不兼容，用 `reference/proto/*.proto` 在机上重新 `protoc --python_out` 生成。

## 联调步骤（拿到真机后）

1. 六元加 nginx 反代 + 部署本服务
2. iwown Android 调试 APK 蓝牙烧录手表上传地址 → **只填前缀** `https://dc.ncrc.org.cn/iwown`（⚠️ **不带 `/pb/upload`**！手表内部自动拼 `/pb/upload` 等端点。多写 `/pb/upload` 会让手表实际发到 `/iwown/pb/upload/pb/upload` → 404，真机数据进不来。2026-06-01 厂商确认 + 实测踩坑。注意：`search.iwown.com/connect4g` 测试工具反而要填**完整** `.../iwown/pb/upload`，两者规则相反别混）
3. 手表上报 → 看 `/api/iwown/list` 出数据 + `fallback/` 有 raw
4. 抽一条 raw_hex 核对字段语义，修正抽列逻辑（raw 已存，可重算历史）
5. 绑定 device_id ↔ 患者；接睡眠算法；按需做看板
