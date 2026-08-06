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


BLOCK_FILES = ("01-theory.md", "02-derivations.md", "03-methods.md")


def article_dir_of(path):
    """Папка статьи, которой принадлежит файл (или None)."""
    p = Path(path).resolve()
    content = (ROOT / "content").resolve()
    if content not in p.parents:
        return None
    for d in p.parents:
        if d == content:
            break
        if (d / "meta.yml").exists() and any((d / b).exists() for b in BLOCK_FILES):
            return d
    return None


def single_article(changed):
    """Если все изменения — внутри одной статьи (и это не meta.yml,
    который влияет на меню всех страниц), можно пересобрать только её."""
    if not changed or any(Path(f).name == "meta.yml" for f in changed):
        return None
    dirs = {article_dir_of(f) for f in changed}
    if len(dirs) == 1 and None not in dirs:
        return dirs.pop()
    return None


def rebuild(changed=None):
    global stamp
    t0 = time.time()
    only = single_article(changed)
    cmd = [sys.executable, str(ROOT / "build.py")]
    if only:
        cmd += ["--only", str(only)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[watch] ✗ сборка упала:\n{res.stdout[-1500:]}{res.stderr[-1500:]}", flush=True)
        return
    stamp = str(time.time())
    what = f"статья {only.name}" if only else "всё"
    print(f"[watch] ✓ {what} за {time.time() - t0:.1f} c", flush=True)


def watch_loop():
    state = snapshot()
    rebuild()
    while True:
        time.sleep(0.4)
        new = snapshot()
        if new != state:
            # даём редактору дописать файлы
            time.sleep(0.2)
            new = snapshot()
            changed = {k for k in set(new) | set(state)
                       if state.get(k) != new.get(k)}
            state = new
            rebuild(changed)


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
