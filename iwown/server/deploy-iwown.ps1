# ============================================================
# iwown 接收服务部署脚本 (PowerShell 端, 需先连公司 VPN)
# ============================================================
# WSL 直连不通 192.168.4.104, 必须在 Windows PowerShell 用 ssh.exe/scp.exe 直连。
# 用法 (连 VPN 后, cd 到本 server 目录):
#     .\deploy-iwown.ps1 -DbPassword '<六元库密码>'
#   不带 -DbPassword 也能跑, 但若服务器 /opt/iwown/iwown.env 不存在, MySQL 会连不上。
# 前提: Test-NetConnection 192.168.4.104 -Port 22 显示 True
#
# 密码处理: 用 base64 经 ssh 落盘到 /opt/iwown/iwown.env (chmod 600), 不进 git,
#          避开 $ ! # 等特殊字符被远端 shell 解释。

param(
    [string]$DbPassword = ""
)

$ErrorActionPreference = "Stop"
$SERVER = "root@192.168.4.104"
$REMOTE = "/opt/iwown"
$HERE = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $HERE

Write-Host "[0/7] 探活内网..."
$t = Test-NetConnection 192.168.4.104 -Port 22
if (-not $t.TcpTestSucceeded) { Write-Host "[X] 192.168.4.104:22 不通, 先连 VPN"; exit 1 }

Write-Host "[1/7] 建目录 + 备份现版本..."
$TS = Get-Date -Format "yyyyMMdd_HHmmss"
ssh.exe -o StrictHostKeyChecking=no $SERVER "mkdir -p $REMOTE/theproto $REMOTE/fallback; [ -f $REMOTE/iwown_server.py ] && cp $REMOTE/iwown_server.py $REMOTE/iwown_server.py.bak.$TS || true"

Write-Host "[2/7] 上传服务代码..."
scp.exe -o StrictHostKeyChecking=no iwown_server.py iwown_parser.py requirements.txt "${SERVER}:${REMOTE}/"

Write-Host "[3/7] 上传 theproto (protobuf 编译产物)..."
scp.exe -o StrictHostKeyChecking=no -r theproto/* "${SERVER}:${REMOTE}/theproto/"

Write-Host "[4/7] 写数据库密码到 /opt/iwown/iwown.env (base64 落盘, chmod 600)..."
if ($DbPassword -ne "") {
    # 把 "SUIFANG_DB_PASSWORD=xxx\n" 整行 base64, 远端解码落盘, 避免特殊字符被 shell 吃掉
    $envLine = "SUIFANG_DB_PASSWORD=$DbPassword`n"
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($envLine))
    ssh.exe -o StrictHostKeyChecking=no $SERVER "echo $b64 | base64 -d > $REMOTE/iwown.env && chmod 600 $REMOTE/iwown.env && echo 'iwown.env 已写入 (chmod 600)'"
} else {
    Write-Host "    (未传 -DbPassword, 跳过; 若 $REMOTE/iwown.env 已存在则沿用, 否则 MySQL 连不上)"
    ssh.exe -o StrictHostKeyChecking=no $SERVER "[ -f $REMOTE/iwown.env ] && echo '沿用已存在的 iwown.env' || echo '⚠ iwown.env 不存在, 服务会连不上库'"
}

Write-Host "[5/7] 装依赖 (protobuf + pymysql)..."
ssh.exe -o StrictHostKeyChecking=no $SERVER "cd $REMOTE && (pip3 install -r requirements.txt 2>&1 | tail -3 || python3 -m pip install -r requirements.txt 2>&1 | tail -3)"

Write-Host "[6/7] 装 systemd 服务 + 启动..."
scp.exe -o StrictHostKeyChecking=no iwown.service "${SERVER}:/etc/systemd/system/iwown.service"
ssh.exe -o StrictHostKeyChecking=no $SERVER "systemctl daemon-reload && systemctl enable iwown && systemctl restart iwown && sleep 2 && systemctl is-active iwown"

Write-Host "[7/7] 探活 /api/status (内网直连 8099)..."
ssh.exe -o StrictHostKeyChecking=no $SERVER "curl -sS http://127.0.0.1:8099/api/status"
Write-Host ""
Write-Host "=== 完成。看上面 JSON 里 mysql 是否 connected。"
Write-Host "    反代加好后外网验证: curl https://dc.ncrc.org.cn/iwown/api/status ==="
