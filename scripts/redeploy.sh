#!/bin/bash
# 重新部署 health_server.py + suifang.service 到公司服务器 192.168.4.104
# 改动一致，避免每次部署都要校对 unit 文件
#
# 用法（PowerShell / git bash / 任何能 SSH 192.168.4.104 的环境）：
#   bash scripts/redeploy.sh
#
# 认证：服务器已配 ~/.ssh/id_ed25519 免密（详见 docs/服务器运维.md），
# 如果在新机器上没配过 key，传密码作为第一参数：
#   bash scripts/redeploy.sh "8ik,(OL>"

set -e

SERVER="192.168.4.104"
USER="root"
PASSWORD="${1:-}"
SUIFANG_DIR="/opt/suifang"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCAL_PY="$SCRIPT_DIR/health_server.py"
LOCAL_UNIT="$SCRIPT_DIR/suifang.service"

if [ ! -f "$LOCAL_PY" ]; then
    echo "ERROR: 找不到 $LOCAL_PY"
    exit 1
fi
if [ ! -f "$LOCAL_UNIT" ]; then
    echo "ERROR: 找不到 $LOCAL_UNIT"
    exit 1
fi

echo "=== 智能随访服务部署 ==="
echo "目标:    $USER@$SERVER:$SUIFANG_DIR/"
echo "Python:  $LOCAL_PY"
echo "Service: $LOCAL_UNIT"
echo

# 选择 SSH/SCP 方式：优先用 key（默认），有密码就用 sshpass
if [ -n "$PASSWORD" ]; then
    if ! command -v sshpass &>/dev/null; then
        echo "提示: 系统没装 sshpass，但你传了密码——会提示交互输入"
        echo "Ubuntu/Debian: apt install sshpass"
        SSH_CMD="ssh -o StrictHostKeyChecking=no"
        SCP_CMD="scp -o StrictHostKeyChecking=no"
    else
        SSH_CMD="sshpass -p '$PASSWORD' ssh -o StrictHostKeyChecking=no"
        SCP_CMD="sshpass -p '$PASSWORD' scp -o StrictHostKeyChecking=no"
    fi
else
    # 默认走 key（id_ed25519 已配好）
    SSH_CMD="ssh -o StrictHostKeyChecking=no"
    SCP_CMD="scp -o StrictHostKeyChecking=no"
fi

# Step 1: 备份服务器上的现版本
# 备份文件名的时间戳用服务器时钟(远端 date), 不用本机的 —— 服务器在 CST, 操作的人可能在
# 别的时区, 名字和文件 mtime 对不上会让回滚时选错版本。
echo "[1/5] 备份现有 health_server.py（如果有）"
eval "$SSH_CMD $USER@$SERVER \"mkdir -p $SUIFANG_DIR && [ -f $SUIFANG_DIR/health_server.py ] && cp $SUIFANG_DIR/health_server.py $SUIFANG_DIR/health_server.py.bak.\\\$(date +%Y%m%d_%H%M%S) && echo '已备份' || echo '(无旧文件，跳过备份)'\""

# Step 2: 上传 Python 主程序
echo
echo "[2/5] 上传 health_server.py"
eval "$SCP_CMD '$LOCAL_PY' $USER@$SERVER:$SUIFANG_DIR/health_server.py"

# Step 3: 上传 systemd unit（与仓库内 scripts/suifang.service 一致）
echo
echo "[3/5] 上传 suifang.service"
eval "$SCP_CMD '$LOCAL_UNIT' $USER@$SERVER:/etc/systemd/system/suifang.service"

# Step 4: reload + restart
#
# 这一步 2026-08-02 之前是坏的, 坏得很隐蔽 —— 原本第一句是:
#     pkill -9 -f health_server.py; sleep 2; systemctl daemon-reload && ... && restart
# `pkill -f` 匹配的是**整条命令行**, 而执行这串命令的远端 sh 自己的命令行里就含
# "health_server.py" 这几个字 —— 于是 pkill 把自己杀了, 后面的 daemon-reload / enable /
# restart 一句都没执行, 脚本也就走不到 [5/5]。服务能爬起来纯粹是靠 unit 里的
# Restart=on-failure, 而且只有在文件已经传完(Step 2)时才恰好捡到新代码。
# 更危险的是 daemon-reload 从来没跑过 —— Step 3 传上去的新 unit 文件一直是不生效的。
# journal 实证: "main process exited, code=killed, status=9/KILL" 紧跟
#              "holdoff time over, scheduling restart", 中间没有任何 systemctl 动作。
#
# 现在的写法:
#   - daemon-reload 放最前面, 保证 Step 3 传的 unit 一定生效
#   - 用 systemctl stop 正常停服务(systemd 知道自己的 PID), 不再靠 pkill 当主手段
#   - 保留一个 [h]ealth_server 形式的 pkill 兜脱缰进程: 正则 [h]ealth 匹配字面
#     "health", 而本行自身的命令行里是带方括号的 "[h]ealth", 匹配不上, 不会再自杀
#   - sed -n '1,15p' 取代 head -15: head 提前关管道会给 systemctl 发 SIGPIPE
#   - 结尾 exit 0: 这步的返回值不该让外层 set -e 掐掉 [5/5] 的验证输出
echo
echo "[4/5] 重启服务"
eval "$SSH_CMD $USER@$SERVER \"systemctl daemon-reload; systemctl enable suifang >/dev/null 2>&1; systemctl stop suifang; sleep 1; pkill -9 -f '[h]ealth_server[.]py' >/dev/null 2>&1; sleep 1; systemctl start suifang; sleep 3; systemctl status suifang --no-pager | sed -n '1,15p'; echo '--- is-active:' \\\$(systemctl is-active suifang); exit 0\""

# Step 5: 验证
echo
echo "[5/5] 服务状态"
# 纯验证输出, 任何一句失败都不该让 set -e 把脚本掐在这里(那样反而看不到失败现场), 故 exit 0。
# unit 加了 python3 -u 之后 server.log 才会实时落盘, 这里的 tail 也才看得到本次启动横幅。
eval "$SSH_CMD $USER@$SERVER \"tail -25 $SUIFANG_DIR/server.log; echo '---'; curl -s http://localhost:3000/api/status; echo; exit 0\""

echo
echo "=== 部署完成。从外网验证 ==="
echo "curl -s https://dc.ncrc.org.cn/api2/api/status            # 期望 mysql:connected, total_devices:>=12"
echo "curl -s https://dc.ncrc.org.cn/api2/                      # 期望 endpoints 含 device/register"
echo "curl -X POST https://dc.ncrc.org.cn/api2/api/device/register \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"deviceSign\":\"S101_FA:BA:94:8A:70:75\",\"type\":1}'  # 期望 deviceId:4"
