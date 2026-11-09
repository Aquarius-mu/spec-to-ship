# 远程执行助手: python rsh.py <host> "<cmd>" [port] [user] [password]
# ⚠️ 本工具等价于远端 shell，仅限内网授权主机，仅供本人运维自用。
# 端口/用户名/口令一律从参数或环境变量取，脚本内不含任何团队默认值:
#   RSH_PORT（默认 22）/ RSH_USER（必填）/ RSH_PASSWORD（不填则走免密）。
import os
import sys
import paramiko

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

if len(sys.argv) < 3:
    print('用法: python rsh.py <host> "<cmd>" [port] [user] [password]')
    print("或设环境变量 RSH_PORT / RSH_USER / RSH_PASSWORD")
    sys.exit(1)

host = sys.argv[1]
cmd = sys.argv[2]
port = int(sys.argv[3]) if len(sys.argv) > 3 else int(os.environ.get("RSH_PORT") or 22)
user = sys.argv[4] if len(sys.argv) > 4 else os.environ.get("RSH_USER")
if not user:
    print("缺少用户名: 传第 4 个参数，或设环境变量 RSH_USER")
    sys.exit(1)
pwd = sys.argv[5] if len(sys.argv) > 5 else os.environ.get("RSH_PASSWORD")

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    cli.connect(host, port=port, username=user, password=pwd, timeout=10)
except Exception as e:
    print("[CONNECT-FAIL] %s: %s" % (host, e))
    sys.exit(2)

_, out, err = cli.exec_command(cmd, timeout=300)
o = out.read().decode("utf-8", "ignore")
e = err.read().decode("utf-8", "ignore")
if o:
    print(o, end="")
if e:
    print("[STDERR] " + e, end="")
cli.close()
