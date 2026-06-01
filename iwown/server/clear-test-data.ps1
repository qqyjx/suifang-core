# ============================================================
# 清理 iwown 测试数据 (PowerShell 端, 连 VPN 后)
# ============================================================
# 删掉 iwown 在线测试工具 (search.iwown.com/connect4g) 发的样例数据,
# 它们 device_id 固定为 860132060611357 (心率80/血压120/80/步数10 假样板),
# 真机数据不是这个号, 所以按号删干净, 不误伤真机数据。
#
# 用法: 连 VPN -> cd 到本 server 目录 -> .\clear-test-data.ps1
# 可选: .\clear-test-data.ps1 -DeviceId '其它要删的号'

param([string]$DeviceId = "860132060611357")

$ErrorActionPreference = "Stop"
$SERVER = "root@192.168.4.104"
$PY = "/root/miniconda3/bin/python3"

$t = Test-NetConnection 192.168.4.104 -Port 22
if (-not $t.TcpTestSucceeded) { Write-Host "[X] 192.168.4.104:22 不通, 先连 VPN"; exit 1 }

# 远端用 python 读 /opt/iwown/iwown.env 取库密码, 删两表里该 device_id
$remotePy = @"
import pymysql
pw = ''
try:
    for line in open('/opt/iwown/iwown.env'):
        if line.startswith('SUIFANG_DB_PASSWORD='):
            pw = line.split('=',1)[1].strip()
except Exception as e:
    print('read env fail', e); raise SystemExit(1)
c = pymysql.connect(host='192.168.4.174', user='developer', password=pw, database='h6dp_suifang', charset='utf8mb4')
cur = c.cursor()
n1 = cur.execute(\"DELETE FROM iwown_data WHERE device_id=%s\", ('$DeviceId',))
n2 = cur.execute(\"DELETE FROM iwown_device WHERE device_id=%s\", ('$DeviceId',))
c.commit()
cur.execute('SELECT COUNT(*) FROM iwown_data'); left = cur.fetchone()[0]
print('deleted iwown_data=%d iwown_device=%d, remaining rows=%d' % (n1, n2, left))
"@

# base64 经 ssh 落盘到临时 py 再执行, 避开引号/特殊字符被 shell 吃掉
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remotePy))
Write-Host "[*] 删除 device_id = $DeviceId 的测试数据..."
ssh.exe -o StrictHostKeyChecking=no $SERVER "echo $b64 | base64 -d > /tmp/_clr.py && $PY /tmp/_clr.py && rm -f /tmp/_clr.py"
Write-Host "[*] 完成。验证: curl https://dc.ncrc.org.cn/iwown/api/iwown/list"
