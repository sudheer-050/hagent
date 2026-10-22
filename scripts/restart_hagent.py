"""Restart this checkout's local server from a normal Windows terminal."""
import argparse
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.request

import psutil

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--check", action="store_true", help="Inspect readiness without restarting")
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
port_file = root / "output/hagent-local-port.txt"
port = int(port_file.read_text().strip()) if port_file.exists() else 8000
if not 1024 <= port <= 65535:
    raise SystemExit("Invalid saved Hagent port")
database = Path(os.environ.get("HAGENT_DB_PATH", str(root / "hagent.db"))).resolve()
with sqlite3.connect("file:" + database.as_posix() + "?mode=ro", uri=True) as connection:
    active = connection.execute("SELECT count(*) FROM runs WHERE status = 'RUNNING'").fetchone()[0]
if active:
    raise SystemExit(f"{active} task(s) are running. Wait until they finish before restarting.")
servers = []
for process in psutil.process_iter(["cmdline", "cwd"]):
    command = process.info["cmdline"] or []
    directory = process.info["cwd"]
    if (command[1:4] == ["-m", "hagent", "serve"] and directory
            and Path(directory).resolve() == root and command[-2:] == ["--port", str(port)]):
        servers.append(process)
print("Hagent checkout:", root)
print("Port:", port, "Server process IDs:", [process.pid for process in servers])
if args.check:
    raise SystemExit(0)
for process in servers:
    try:
        process.terminate()
    except psutil.NoSuchProcess:
        pass
_, alive = psutil.wait_procs(servers, timeout=10)
if alive:
    raise SystemExit("A server did not stop. Close Hagent and rerun this command.")
log = open(root / "output/hagent-restart.log", "ab")
subprocess.Popen([str(root / ".venv/Scripts/python.exe"), "-m", "hagent", "serve", "--port", str(port)],
                 cwd=root, stdout=log, stderr=log,
                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
log.close()
for attempt in range(30):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
            if response.status == 200:
                print(f"Ready: http://127.0.0.1:{port}/connections")
                break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit("Hagent did not become ready. Inspect output/hagent-restart.log.")
