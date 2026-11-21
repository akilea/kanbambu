"""Run kanbambu and keep it up to date with GitHub.

    python tools/serve.py            (pythonw on Windows to run without a window)

- starts the server;
- every few minutes, `git pull` if GitHub has new commits, and `pip install` if
  requirements.txt changed;
- restarts the server if it ever stops.
Logs go to data/kanbambu.log.
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("KANBAMBU_DATA", os.path.join(ROOT, "data"))
os.makedirs(DATA, exist_ok=True)
LOG = open(os.path.join(DATA, "kanbambu.log"), "a", buffering=1, encoding="utf-8")
# On Windows, git and pip are console programs: without this, each check every few minutes
# flashes a terminal window that steals the focus.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


_last = None


def log(msg):
    global _last
    if msg == _last and "impossible" in msg:  # same failing pull every few minutes: say it once
        return
    _last = msg
    LOG.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [serve] {msg}\n")


def git(*args):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, creationflags=NO_WINDOW)
    return r.returncode, (r.stdout + r.stderr).strip()


def start_server(host, port):
    log(f"démarrage du serveur sur {host}:{port}")
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", host, "--port", str(port)],
        cwd=ROOT, stdout=LOG, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)


def update():
    """Pull new commits from GitHub. True if something was pulled."""
    code, out = git("fetch", "--quiet")
    if code:
        log(f"git fetch a échoué: {out}")
        return False
    _, local = git("rev-parse", "HEAD")
    code, remote = git("rev-parse", "@{u}")
    if code or local == remote:
        return False
    _, changed = git("diff", "--name-only", local, remote)
    code, out = git("pull", "--ff-only", "--quiet")
    if code:
        log(f"git pull impossible (modifications locales ?): {out}")
        return False
    log(f"mis à jour {local[:7]} -> {remote[:7]}")
    if "requirements.txt" in changed.split():
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"],
                           cwd=ROOT, capture_output=True, text=True, creationflags=NO_WINDOW)
        log("pip install: " + ("ok" if r.returncode == 0 else r.stderr.strip()[-500:]))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--interval", type=int, default=300, help="secondes entre deux vérifications GitHub")
    a = ap.parse_args()

    with open(os.path.join(DATA, "serve.pid"), "w") as f:   # for tools/kanbambu-stop.bat
        f.write(str(os.getpid()))
    update()
    server = start_server(a.host, a.port)
    try:
        while True:
            for _ in range(a.interval):
                time.sleep(1)
                if server.poll() is not None:
                    log(f"le serveur s'est arrêté (code {server.returncode}), redémarrage")
                    time.sleep(3)
                    server = start_server(a.host, a.port)
            if update() and server.poll() is None:
                server.terminate()  # restart on the new code
                server.wait(timeout=20)
                server = start_server(a.host, a.port)
    except KeyboardInterrupt:
        pass
    finally:
        server.terminate()
        log("arrêt")


if __name__ == "__main__":
    main()
