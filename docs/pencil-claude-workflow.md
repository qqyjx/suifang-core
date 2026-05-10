# Pencil + Claude Code 协作工作流

> **Pencil**: VSCode 插件, AI-native 设计工具 (类 Figma 但内嵌 IDE), 通过 MCP 跟 Claude Code/Codex/Gemini/OpenCode/Kiro/Claude Desktop/Copilot 集成.
> **Claude Code**: 终端 CLI, 通过 MCP "看到" Pencil 当前画布内容, 能读 / 给建议 / 转代码.

---

## 工作流总览

```
┌────────────────────┐         ┌──────────────────────┐
│  VSCode + Pencil   │  MCP    │  WSL 终端 + Claude   │
│  (画 UI)           │ ◄────► │  Code (生成代码/建议) │
└────────────────────┘         └──────────────────────┘
        │                                │
        │         设计 ←→ 代码           │
        ▼                                ▼
   prototype/.pen 文件              小程序 wxml/less/ts
        │                                │
        └────────► commit + push ◄──────┘
                       │
                       ▼
                 GitHub (双端同步)
```

---

## 第一次配置 (一次性)

### 1. 确认 Pencil 已装并启用 MCP
你截图里显示 Pencil Settings → MCP Integrations 已勾选 7 个 CLI:
- ✅ Claude Code CLI
- ✅ Codex CLI
- ✅ Gemini CLI
- ✅ OpenCode CLI
- ✅ Kiro CLI
- ✅ Claude Desktop
- ✅ Copilot IDE

注脚 "On Pencil startup, MCP is automatically setup" — 重启 Pencil 自动注册到 Claude Code.

### 2. 验证 Claude Code 能看到 Pencil
在 WSL 终端打开 Claude Code:
```bash
claude
```
进入交互后输 `/mcp` 列出当前可用的 MCP server. 应该看到 `pencil` (或类似名字) 在列表里.

如果没看到, 检查 `~/.claude/settings.json`:
```bash
cat ~/.claude/settings.json | grep -A 5 mcpServers
```
Pencil 启动时应该自动写入这块. 没有就手动看 Pencil → Settings → MCP → Copy MCP config (你截图里底部那个按钮), 粘到 `~/.claude/settings.json` 的 `mcpServers` 里.

---

## 标准协作模式

### 模式 1: "看一下我画的, 给我建议"

```
[Pencil] 画了一个粗略首页 (几个 Rectangle + Text 占位)
   │
   ▼
[终端] claude
   > 我在 Pencil 当前画布画了智能随访首页, 看一下给我建议
   ↓
[Claude Code] (通过 MCP 读 Pencil 当前 page 的 layers)
   "看到了 5 个 layer: 顶部 patient-bar, 中部 conn-bar, 4 个 quick-btn 网格.
    建议:
    1. quick-btn 高度增加到 96rpx 增强可点击性
    2. patient-bar 加渐变背景而不是纯色
    3. 缺少今日采集统计区..."
```

### 模式 2: "把这个 Frame 转成微信小程序代码"

```
[Pencil] 画完一个 Frame (例如 Tab 3 数据视图)
   │
   ▼
[Pencil] 选中这个 Frame
   │
   ▼
[终端] > 把当前选中的 Frame 转成微信小程序的 wxml + less
   ↓
[Claude Code] (通过 MCP 读选中 Frame 的几何 + 样式 + 子图层)
   生成 pages/data/index.wxml + index.less
   写到本地文件
```

### 模式 3: "对比设计稿和实现, 找出差异"

```
[Claude Code]
   > 看一下我现在 pages/index/ 的实现, 跟 Pencil 当前 Slide "首页 v10" 的设计对比,
     列出哪里不一致
   ↓
[Claude Code] (读 wxml/less + Pencil Slide layers)
   "差异:
    1. 设计稿 quick-btn 4 个, 实现里只有 2 个
    2. 实现里缺少 '今日采集' 统计卡
    3. 顶部高度: 设计稿 188px, 实现 ~120px..."
```

### 模式 4: "我用文字描述, 你帮我画到 Pencil"

```
[终端]
   > 在 Pencil 当前画布加一个 Frame 名为 "Tab 3 数据视图":
     - 顶部 horizontal scroll 4 个 chip (患者门诊号)
     - 中间 3 个数字统计卡 (血压条数, 心率条数, 异常)
     - 下面 2 个柱状图占位
     - 最下方导出按钮
   ↓
[Claude Code] (调用 Pencil MCP 的 create_frame / add_layer 等工具)
   "已在 Pencil 创建 Frame 'Tab 3 数据视图', 含 12 个 layer"
   ↓
[Pencil 自动刷新画布] 显示新 Frame
```

⚠️ **模式 4 是否能用取决于 Pencil MCP 暴露的写接口**. 如果只读不写, 模式 4 不行, 你得自己画.
进入 Claude Code 后输 `/mcp` 看 Pencil 暴露的 tool 列表, 有 `create_*` / `add_*` 之类的就支持写.

---

## 智能随访 v10 落地建议步骤

### 阶段 1: 业务对齐 (本阶段, 不动 UI)
- ✅ 已产出: [docs/business-logic-v10.md](business-logic-v10.md) (业务逻辑设计文档)
- ✅ 已产出: [docs/ui-prototype-v10.md](ui-prototype-v10.md) (UI 设计规格)
- ✅ 已产出: [prototype/index.html](../prototype/index.html) (可点击 HTML 原型)
- 待做: 跟客户/医生评审业务逻辑文档, 确认 9 个决策点的选择

### 阶段 2: 用 Pencil 把 4-tab 画细 (1-2 天)
- 在 Pencil 创建一个 .pen 文件 `suifang-v10.pen`
- 4 个 Slide: home / measure / data / settings
- 每个 Slide 按 [docs/ui-prototype-v10.md](ui-prototype-v10.md) 的 ASCII 线框画细化版
- Claude Code 边看边给意见 (模式 1/3)
- 用 Components 抽出复用的卡片 (patient-bar / conn-bar / measure-item / list-row)

### 阶段 3: 与课题组/医生对齐 (0.5 天)
- 把 Pencil 设计稿 export PNG 发给客户 / 医生评审
- 同时给客户看 prototype/index.html 可点击版
- 收集修改意见, 在 Pencil 改 → 再 export

### 阶段 4: Claude Code 转代码 (3-5 天)
- 模式 2: 让 Claude Code 把每个 Slide 转成 wxml + less
- 配套修改 app.json 加 tabBar
- 旧 25 page 不动, 仅做导航重组
- 阶段 1-6 按 [docs/ui-prototype-v10.md](ui-prototype-v10.md) 末尾 "实施方案" 走

### 阶段 5: 上线 + 测试 (0.5 天)
- BUILD_TAG → 5.06-v10
- PowerShell git pull D:\suifang_dev
- 微信开发者工具上传 5.6.10 → 选为体验版
- 客户重扫旧二维码

---

## 实用 Tip

### Tip 1: 用 Pencil Components 复用卡片样式
在 Pencil 顶部 Components tab 创建可复用组件:
- PatientBar (患者状态条)
- ConnBar (连接状态条)
- MeasureItem (测量列表项)
- ListRow (我的页列表项)

每个 Slide 拖组件用即可, 改组件全局生效. 跟 Figma Components 同概念.

### Tip 2: Slides 模式做演示
Pencil 的 "Slides" tab (你截图里有) 可以把多个 Frame 当幻灯片, 用左右键切换. 给客户演示 4-tab 流程时一键过.

### Tip 3: Library 共享设计令牌
Pencil 顶部 "Libraries" tab 能定义 design tokens (色值/字号/圆角). 一处改全局生效, 不用每个 Frame 改一遍.

### Tip 4: 不要让 Claude Code 直接覆盖 .pen 文件
Pencil 文件是二进制(或加密 JSON), Claude Code 直接写可能损坏. 让它通过 MCP 工具调用 (模式 4) 而不是直接 Write 工具改 .pen.

### Tip 5: 把 Pencil 设计稿 commit 到 git
.pen 文件入仓 → 设计版本可追溯. 但 .pen 体积可能大, 注意检查是否要进 .gitattributes 走 LFS.

```bash
echo "*.pen filter=lfs diff=lfs merge=lfs -text" >> .gitattributes
```

---

## 排错

| 现象 | 原因 | 解决 |
|---|---|---|
| Claude Code `/mcp` 不显示 pencil | MCP 配置没写入 | 重启 Pencil; 检查 `~/.claude/settings.json` |
| Pencil 启动后 Claude Code 还是看不到画布 | Claude Code 进程已开启时 MCP 没刷新 | 退出 Claude Code 重进 |
| Claude Code 说"Pencil MCP 工具不可用" | Pencil 没启动或没打开任何 .pen 文件 | 先在 Pencil 打开 .pen 文件再问 |
| 画布修改后 Claude Code 看到的是旧的 | MCP 缓存 / 没自动刷新 | 让 Claude Code 重新调用 MCP read 工具 |
| WSL 跑 claude, 但 Pencil 在 Windows 主机 | 跨平台 IPC 可能不通 | 检查 Pencil 是否监听 0.0.0.0 而非仅 localhost |

WSL ↔ Windows 主机 的 MCP 跨边界问题如果出现, 退路是: 在 Windows PowerShell 里用 Claude Code (D:\suifang_dev 工作区那边), 跟 Pencil 同进程边界, 必通.

---

## 现在你做的事 (按优先级)

1. **打开 [docs/business-logic-v10.md](business-logic-v10.md), 跟同事/医生评审 9 个决策点**
   决策完了再动手画 Pencil, 否则方向错画了白画.

2. **同时让同事**:
   - 在 Pencil 创建 `prototype/suifang-v10.pen`
   - 4 个 Slide 按 [docs/ui-prototype-v10.md](ui-prototype-v10.md) 的 ASCII 线框画
   - 业务逻辑评审完后, 把决策点 1-9 的答案反映到 Pencil 设计稿

3. **本机 (WSL) 同步**:
   - `git pull` 拿 docs/ + prototype/
   - 同事 Pencil 改了 push, 你 pull 看
   - Claude Code 任何时候都能跟你沟通画布内容

4. **不要急**: 业务对齐 > UI 细化 > 代码实施. 跳步必返工.
