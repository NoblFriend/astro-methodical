#!/usr/bin/env python3
"""Живой предпросмотр: `make watch`.

Поднимает сервер на http://localhost:8765 (порт — через PORT=...), следит за исходниками
(content/, theme/, macros/, filters/, latex/, templates/, config.yml, build.py)
и при любом изменении пересобирает сайт. Открытая в браузере страница
перезагружается сама — поменял букву, увидел результат.
"""

import functools
import http.server
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"
WATCHED = [
    ROOT / "content", ROOT / "theme", ROOT / "macros", ROOT / "filters",
    ROOT / "latex", ROOT / "templates", ROOT / "config.yml", ROOT / "build.py",
]
PORT = int(os.environ.get("PORT", "8765"))

stamp = str(time.time())


def snapshot():
    state = {}
    for w in WATCHED:
        if w.is_file():
            state[str(w)] = w.stat().st_mtime
        elif w.is_dir():
            for f in w.rglob("*"):
                if f.is_file() and not f.name.startswith("."):
                    state[str(f)] = f.stat().st_mtime
    return state


def rebuild():
    global stamp
    t0 = time.time()
    res = subprocess.run([sys.executable, str(ROOT / "build.py")],
                         capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[watch] ✗ сборка упала:\n{res.stdout[-1500:]}{res.stderr[-1500:]}", flush=True)
        return
    stamp = str(time.time())
    print(f"[watch] ✓ пересобрано за {time.time() - t0:.1f} c", flush=True)


def watch_loop():
    state = snapshot()
    rebuild()
    while True:
        time.sleep(0.4)
        new = snapshot()
        if new != state:
            state = new
            # даём редактору дописать файлы
            time.sleep(0.2)
            state = snapshot()
            rebuild()


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/__stamp":
            body = stamp.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *args):
        pass


def main():
    SITE.mkdir(exist_ok=True)
    threading.Thread(target=watch_loop, daemon=True).start()
    handler = functools.partial(Handler, directory=str(SITE))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    print(f"[watch] предпросмотр: http://localhost:{PORT} (Ctrl+C — выход)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
