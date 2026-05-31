# ============================================================
# iwown 接收服务部署脚本 (PowerShell 端, 需先连公司 VPN)
# ============================================================
# WSL 直连不通 192.168.4.104, 必须在 Windows PowerShell 用 ssh.exe/scp.exe 直连。
# 用法: 连 VPN -> cd 到本 server 目录 ->
#   .\deploy-iwown.ps1 -DbPassword '<六元库密码>'      # 首次部署传六元库密码(写到服务器 env, 不入 git)
#   .\deploy-iwown.ps1                                # 之后部署可不传(复用服务器上已有 /opt/iwown/iwown.env)
# 前提: Test-NetConnection 192.168.4.104 -Port 22 显示 True

param([string]$DbPassword = "")

$ErrorActionPreference = "Stop"
$SERVER = "root@192.168.4.104"
$REMOTE = "/opt/iwown"
$PY = "/root/miniconda3/bin/python3"   # 与 S101 同一个带 pymysql 的 python
$HERE = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $HERE

Write-Host "[0/7] 探活内网..."
$t = Test-NetConnection 192.168.4.104 -Port 22
if (-not $t.TcpTestSucceeded) { Write-Host "X 192.168.4.104:22 不通, 先连 VPN"; exit 1 }

Write-Host "[1/7] 建目录 + 备份现版本..."
$TS = Get-Date -Format "yyyyMMdd_HHmmss"
ssh.exe -o StrictHostKeyChecking=no $SERVER "mkdir -p $REMOTE/theproto $REMOTE/fallback; [ -f $REMOTE/iwown_server.py ] && cp $REMOTE/iwown_server.py $REMOTE/iwown_server.py.bak.$TS || true"

Write-Host "[2/7] 写数据库密码到 /opt/iwown/iwown.env (不入 git)..."
if ($DbPassword -ne "") {
    # 单引号包裹, 用 base64 经 ssh 落盘避免特殊字符($!#)被 shell 解释
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("SUIFANG_DB_PASSWORD=$DbPassword`n"))
    ssh.exe -o StrictHostKeyChecking=no $SERVER "echo $b64 | base64 -d > $REMOTE/iwown.env && chmod 600 $REMOTE/iwown.env && echo 'iwown.env 已写'"
} else {
    ssh.exe -o StrictHostKeyChecking=no $SERVER "[ -f $REMOTE/iwown.env ] && echo 'iwown.env 已存在, 复用' || echo '!! 未传 -DbPassword 且 iwown.env 不存在: 入库会失败, 请重跑并传密码'"
}

Write-Host "[3/7] 上传服务代码..."
scp.exe -o StrictHostKeyChecking=no iwown_server.py iwown_parser.py requirements.txt "${SERVER}:${REMOTE}/"

Write-Host "[4/7] 上传 theproto (protobuf 编译产物)..."
scp.exe -o StrictHostKeyChecking=no -r theproto/* "${SERVER}:${REMOTE}/theproto/"

Write-Host "[5/7] 装依赖 (protobuf + pymysql) 到 miniconda python..."
ssh.exe -o StrictHostKeyChecking=no $SERVER "$PY -m pip install -r $REMOTE/requirements.txt 2>&1 | tail -4"

Write-Host "[6/7] 装 systemd 服务 + 启动..."
scp.exe -o StrictHostKeyChecking=no iwown.service "${SERVER}:/etc/systemd/system/iwown.service"
ssh.exe -o StrictHostKeyChecking=no $SERVER "systemctl daemon-reload && systemctl enable iwown && systemctl restart iwown && sleep 2 && systemctl is-active iwown"

Write-Host "[7/7] 探活 /api/status (内网直连 8099)..."
ssh.exe -o StrictHostKeyChecking=no $SERVER "curl -sS http://127.0.0.1:8099/api/status"
Write-Host ""
Write-Host "=== 完成。外网验证(反代加好后): curl https://dc.ncrc.org.cn/iwown/api/status ==="
