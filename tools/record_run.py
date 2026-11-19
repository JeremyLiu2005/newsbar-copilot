"""Run a command and log every output line with its elapsed time (for the demo video).

Usage: python tools/record_run.py out.json -- <command ...>
"""

import json
import subprocess
import sys
import time

out, cmd = sys.argv[1], sys.argv[sys.argv.index("--") + 1:]
t0 = time.time()
lines = [[0.0, "$ " + " ".join(cmd)]]
p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
for line in p.stdout:
    line = line.rstrip("\n")
    if "ctranslate2" in line or not line.strip():
        continue
    lines.append([round(time.time() - t0, 2), line])
    print(f"{lines[-1][0]:7.1f}  {line}", flush=True)
p.wait()
lines.append([round(time.time() - t0, 2), f"[完成，耗時 {time.time() - t0:.0f} 秒，結束碼 {p.returncode}]"])
json.dump({"cmd": cmd, "lines": lines, "returncode": p.returncode}, open(out, "w"), ensure_ascii=False, indent=1)
