# 随访平台测试套件

## 为什么在仓库里

原本这些测试写在会话临时目录, 被系统清理误删过一次 —— 里面攒的是十几个真 bug
换来的断言, 丢一次就得从头再踩一遍。现在全部落在仓库里。

## 目录

| 文件 | 覆盖 | 需要 |
|---|---|---|
| `test_platform_safety.py` | M12-M17 安全不变量(跨模块) | MySQL |
| `test_m13_qc.py` | M13 数据质控 | MySQL |
| `test_m14_crf.py` | M14 智能 CRF 表单 | MySQL + openpyxl |
| `test_m15_edu.py` | M15 宣教材料库 | MySQL |
| `test_m16_search.py` | M16 高级检索与统计 | MySQL |
| `test_m17_doc.py` | M17 知情同意与项目资料 | MySQL |
| `test_m18_cohort.py` | M18 纳排规则与分组 | MySQL |
| `test_ui_platform_v2.py` | platform-v2.html 全模块端到端 | MySQL + playwright + chromium |
| `run_all.py` | 依次跑上面全部 | 同上 |

## 跑法

```bash
# 全部(后端 + 浏览器)
DB_PASSWORD=xxx python3 scripts/tests/run_all.py

# 只跑后端(不需要 playwright)
DB_PASSWORD=xxx python3 scripts/tests/run_all.py --no-ui

# 单个模块
DB_PASSWORD=xxx python3 scripts/tests/test_m18_cohort.py
```

## 约定

- **数据库口令走环境变量**, 不写进文件。默认连本机 dev 库
  (`127.0.0.1:3307` / `h6dp_suifang_dev`), 不碰生产。
- 每个套件用**自己的门诊号前缀**造数据(T13/T14/T15/T16/T17/T18/UI…),
  跑完自清。互不干扰, 可以并行跑。
- 浏览器测试起一个**本地后端实例**(`PORT` 环境变量)打本地 dev 库,
  不指向生产 —— 要验完整链路得先造出问题数据, 不该往生产塞测试患者。
- 本机 TUN 全局代理会拦 localhost, 所以测试里统一 `--no-proxy-server`
  并清掉 `*_proxy` 环境变量。chromium 用 `channel='chromium'`
  (`chrome-headless-shell` 在这台机器上缺)。

## 断言写法

断言名要写**它守的是什么**, 不是"函数返回了什么"。
坏了的时候, 断言名应当能直接告诉人为什么这条重要:

```python
check('隐藏的题不参与必填校验 —— 否则表单永远交不上去, 且报错指向看不见的题', ...)
check('一路同档两次一样不误报 —— 筛查量表上这是常态, 报了就天天误报', ...)
```

## 生产环境依赖

平台有几个功能是"库在就启用, 库不在就明确拒绝"的。当前生产(192.168.4.104)状态:

| 依赖 | 用途 | 状态 |
|---|---|---|
| `segno` | 筛查自助填报链接的二维码 | ✅ 1.6.6 已装 |
| `pyzipper` | 导出文件 AES-256 加密 | ✅ 0.4.0 已装 |
| `openpyxl` | Excel → CRF 建表 | ❌ 未装(接口会给出装法) |
| `pdf-inspector` | PDF → 量表解析 | ✅ 已装 |
| anthropic SDK | 量表/CRF 生成走大模型 | ❌ 未装(回落本地模板) |
| `DEEPSEEK_API_KEY` | 健康咨询 | ✅ 已配(在 /opt/suifang/wx.env) |

**这几个都不会静默降级。** 库不在时接口返回明确说明与安装命令, 尤其是加密导出 ——
勾了加密而库不在时**拒绝产出文件**, 绝不给一份明文冒充加密件。

装法(生产):
```bash
ssh root@192.168.4.104 'pip3 install segno pyzipper openpyxl'
systemctl restart suifang     # 重启后接口才会认到新库
```
