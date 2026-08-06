#!/usr/bin/env python3
"""Сборщик гибкого учебника.

content/ (markdown-блоки) → site/ (статический сайт) и site/pdf/ (LaTeX-PDF).

Иерархия произвольной глубины: раздел / тема / подтема / ... / статья.
Статья — папка с meta.yml; любая другая папка — группа (с _meta.yml).

Нумерация формул сквозная и иерархическая: <код раздела>.<индексы групп>.
<индекс статьи>.<номер формулы>, например А.1.2.3. Код раздела задаётся
полем `code` в _meta.yml (по умолчанию — первая буква названия), индексы
определяются полем `order`.

Использование:
    python build.py            # только сайт
    python build.py --pdf      # сайт + все PDF
    python build.py --clean    # снести site/ и .cache/ перед сборкой
"""

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
CONTENT = ROOT / "content"
SITE = ROOT / "site"
CACHE = ROOT / ".cache"
THEME = ROOT / "theme"
TEMPLATES = ROOT / "templates"
FILTER = ROOT / "filters" / "environments.lua"
PREAMBLE = ROOT / "latex" / "preamble.tex"
MACROS = ROOT / "macros" / "macros.tex"
CONFIG = ROOT / "config.yml"

SITE_URL = ""  # заполняется из config.yml в main()

# Канонический состав статьи: (ключ, имя файла, заголовок блока)
BLOCK_DEFS = [
    ("theory", "01-theory.md", "Обсуждение и формулы"),
    ("derivations", "02-derivations.md", "Выводы формул"),
    ("methods", "03-methods.md", "Методы решения задач"),
]


class ToolMissing(RuntimeError):
    """Не установлена внешняя программа."""


def log(msg):
    print(f"[build] {msg}")


def warn(msg):
    print(f"[build] ⚠ {msg}", file=sys.stderr)


def run(cmd, **kw):
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, **kw)
    except FileNotFoundError:
        raise ToolMissing(cmd[0]) from None
    if res.returncode != 0:
        raise RuntimeError(
            f"Команда {' '.join(map(str, cmd))} упала:\n{res.stdout[-2000:]}\n{res.stderr[-2000:]}"
        )
    return res


def read_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


PANDOC_HINT = (
    "  Ubuntu/WSL: wget https://github.com/jgm/pandoc/releases/download/3.6.4/pandoc-3.6.4-1-amd64.deb\n"
    "              sudo dpkg -i pandoc-3.6.4-1-amd64.deb\n"
    "  macOS:      brew install pandoc"
)
TEX_HINT = (
    "  Ubuntu/WSL: sudo apt install texlive-xetex texlive-latex-extra texlive-fonts-recommended \\\n"
    "                texlive-lang-cyrillic texlive-lang-other texlive-pictures fonts-paratype \\\n"
    "                dvisvgm ghostscript poppler-utils\n"
    "  macOS:      MacTeX (https://tug.org/mactex/)"
)


def preflight(need_pdf):
    """Понятные сообщения вместо трейсбеков, если чего-то нет в системе."""
    problems = []
    if not shutil.which("pandoc"):
        problems.append(f"Не найден pandoc (конвертер Markdown). Как поставить:\n{PANDOC_HINT}")
    else:
        first = run(["pandoc", "--version"]).stdout.splitlines()[0]
        m = re.search(r"(\d+)\.(\d+)", first)
        if m and (int(m.group(1)), int(m.group(2))) < (3, 0):
            problems.append(
                f"Найден слишком старый {first!r} — нужен pandoc ≥ 3.0\n"
                f"(apt-версия в Ubuntu 22.04 — это 2.x). Как поставить свежий:\n{PANDOC_HINT}"
            )
    if need_pdf and not shutil.which("xelatex"):
        problems.append(f"Для сборки PDF нужен XeLaTeX. Как поставить:\n{TEX_HINT}")
    if problems:
        for p in problems:
            warn(p)
        sys.exit(1)
    if not need_pdf and not shutil.which("xelatex"):
        warn("xelatex не найден — сайт соберётся, но TikZ-картинки будут пропущены.\n"
             f"Чтобы собирались картинки и PDF:\n{TEX_HINT}")


# ---------------------------------------------------------------- макросы

def parse_math_macros():
    """\newcommand из macros.tex → словарь макросов MathJax.

    Формат MathJax: {"имя": "тело"} или {"имя": ["тело", число_аргументов]}.
    """
    text = MACROS.read_text(encoding="utf-8")
    macros = {}
    for m in re.finditer(r"\\(?:re)?newcommand\{\\([A-Za-z]+)\}(?:\[(\d+)\])?", text):
        name, nargs = m.group(1), m.group(2)
        i = m.end()
        while i < len(text) and text[i] in " \t":
            i += 1
        if i >= len(text) or text[i] != "{":
            continue
        depth, j = 0, i
        while j < len(text):
            if text[j] == "{" and (j == 0 or text[j - 1] != "\\"):
                depth += 1
            elif text[j] == "}" and text[j - 1] != "\\":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        body = text[i + 1 : j]
        macros[name] = [body, int(nargs)] if nargs else body
    return macros


# ---------------------------------------------------------------- дерево контента

def find_meta_file(dirpath):
    """meta.yml — единое имя для статей и групп; _meta.yml — старое имя групп."""
    for name in ("meta.yml", "_meta.yml"):
        if (dirpath / name).exists():
            return dirpath / name
    return None


def load_node_meta(dirpath, fallback_title):
    metafile = find_meta_file(dirpath)
    meta = read_yaml(metafile) if metafile else {}
    if metafile is not None and metafile.name == "_meta.yml":
        warn(f"{metafile}: переименуйте в meta.yml — теперь у статей и групп "
             f"один файл метаданных (роль папки определяется наличием блоков)")
    meta.setdefault("title", fallback_title)
    if "order" in meta:
        warn(f"{dirpath}: поле order больше не используется — порядок задаётся "
             f"числовым префиксом имени папки («2raspredelenia»), уберите поле")
    meta["order"] = default_order(dirpath.name)
    meta.setdefault("description", "")
    return meta


ORDER_PREFIX_RE = re.compile(r"^(\d+)")


def default_order(dirname):
    """Порядок по умолчанию: числовой префикс имени папки («2raspredelenia» → 2)."""
    m = ORDER_PREFIX_RE.match(dirname)
    return int(m.group(1)) if m else 999


def url_slug(dirname):
    """Имя папки → сегмент URL: числовой префикс-порядок (и разделитель за ним:
    «-», «_», «.» или пробел) отбрасывается — «1Id_gas» и «1-Id_gas» → «Id_gas».
    Локально папки сортируются как надо, а в адресах сайта цифры не светятся.
    То же правило продублировано в filters/environments.lua для @/-ссылок."""
    stripped = re.sub(r"^\d+[-_. ]?", "", dirname)
    return stripped or dirname


def load_article(art_dir):
    meta = read_yaml(art_dir / "meta.yml")
    for field in ("title", "description"):
        if field not in meta:
            warn(f"{art_dir}: в meta.yml нет поля «{field}»")
            meta.setdefault(field, art_dir.name)
    if "order" in meta:
        warn(f"{art_dir}: поле order больше не используется — порядок задаётся "
             f"числовым префиксом имени папки («2raspredelenia»), уберите поле")
    meta["order"] = default_order(art_dir.name)
    blocks = []
    for key, fname, btitle in BLOCK_DEFS:
        if (art_dir / fname).exists():
            blocks.append((key, fname, btitle))
        else:
            warn(f"{art_dir}: нет блока {fname} — пропускаю")
    known = {fname for _, fname, _ in BLOCK_DEFS}
    for f in sorted(art_dir.glob("*.md")):
        if f.name not in known:
            warn(f"{art_dir}: файл {f.name} не совпадает ни с одним именем блока "
                 f"({', '.join(sorted(known))}) — он не попадёт в сборку")
    return {"slug": art_dir.name, "dir": art_dir, "meta": meta, "blocks": blocks}


def is_article_dir(dirpath):
    """Статья — папка с meta.yml и хотя бы одним блоком (01-theory.md и т.п.);
    папка с meta.yml без блоков — группа (глава), даже пустая."""
    return (dirpath / "meta.yml").exists() and \
        any((dirpath / fname).exists() for _, fname, _ in BLOCK_DEFS)


def load_group(dirpath):
    """Папка-группа: подгруппы + статьи (рекурсивно, произвольная глубина)."""
    node = {
        "slug": dirpath.name,
        "dir": dirpath,
        "meta": load_node_meta(dirpath, dirpath.name),
        "groups": [],
        "articles": [],
    }
    for child in sorted(dirpath.iterdir()):
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue
        if is_article_dir(child):
            node["articles"].append(load_article(child))
        else:
            sub = load_group(child)
            # Пустая папка без метаданных (например figures/) — не группа;
            # заготовка главы с meta.yml показывается и пустой
            if sub["groups"] or sub["articles"] or find_meta_file(child):
                node["groups"].append(sub)
    node["groups"].sort(key=lambda g: (g["meta"]["order"], g["meta"]["title"]))
    node["articles"].sort(key=lambda a: (a["meta"]["order"], a["meta"]["title"]))
    # Одинаковый порядок у соседей — фактически алфавитный, а нумерация
    # формул может «поплыть» при переименовании; лучше задать явно.
    for kind in ("groups", "articles"):
        if len(node[kind]) < 2:
            continue
        prev = None
        for child in node[kind]:
            o = child["meta"]["order"]
            if prev is not None and o == prev["meta"]["order"]:
                warn(f'{dirpath.relative_to(CONTENT) if dirpath != CONTENT else "content"}: '
                     f'у папок «{prev["slug"]}» и «{child["slug"]}» одинаковый '
                     f"порядок — порядок между ними алфавитный, добавьте папкам "
                     f"числовые префиксы («1foo», «2bar»)")
            prev = child
    return node


def assign_codes(sections):
    """Коды нумерации: раздел — буква, глубже — позиционные индексы.
    rel — путь страницы на сайте: имена папок без числовых префиксов."""
    def check_slugs(node):
        seen = {}
        for child in node["groups"] + node["articles"]:
            s = url_slug(child["slug"])
            if s in seen:
                warn(f'{node["dir"]}: папки «{seen[s]}» и «{child["slug"]}» дают '
                     f"одинаковый URL «{s}» — переименуйте одну из них")
            seen[s] = child["slug"]

    def walk(node, codes, rel):
        node["codes"] = codes
        node["rel"] = rel
        check_slugs(node)
        for i, g in enumerate(node["groups"], 1):
            walk(g, codes + [str(i)], f'{rel}/{url_slug(g["slug"])}')
        for i, a in enumerate(node["articles"], 1):
            a["codes"] = codes + [str(i)]
            a["rel"] = f'{rel}/{url_slug(a["slug"])}'

    for sec in sections:
        code = str(sec["meta"].get("code") or sec["meta"]["title"][0]).upper()
        walk(sec, [code], url_slug(sec["slug"]))


def load_tree():
    root = load_group(CONTENT)
    sections = root["groups"]
    if root["articles"]:
        warn("статьи в корне content/ игнорируются — положите их в раздел")
    assign_codes(sections)
    return sections


def iter_articles(node):
    yield from node["articles"]
    for g in node["groups"]:
        yield from iter_articles(g)


def article_count(node):
    return len(list(iter_articles(node)))


# ---------------------------------------------------------------- нумерация формул

_EQCACHE_PATH = CACHE / "eqmaths.json"
_eqcache = None


def save_eqcache():
    if _eqcache is not None:
        CACHE.mkdir(parents=True, exist_ok=True)
        _EQCACHE_PATH.write_text(json.dumps(_eqcache, ensure_ascii=False), encoding="utf-8")


def display_maths(md_path):
    """Исходники выключных формул файла в порядке появления.
    Кэшируется по mtime+size — это самая дорогая часть сборки
    (вызов pandoc на каждый md), а меняются файлы редко."""
    global _eqcache
    if _eqcache is None:
        try:
            _eqcache = json.loads(_EQCACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _eqcache = {}
    st = md_path.stat()
    ent = _eqcache.get(str(md_path))
    if ent and ent["mtime"] == st.st_mtime and ent["size"] == st.st_size:
        return ent["maths"]

    res = run(["pandoc", str(md_path), "--from", "markdown", "--to", "json"])
    doc = json.loads(res.stdout)
    out = []

    def walk(x):
        if isinstance(x, dict):
            c = x.get("c")
            if x.get("t") == "Math" and isinstance(c, list) and c and c[0].get("t") == "DisplayMath":
                out.append(c[1])
            else:
                for v in x.values():
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(doc.get("blocks", []))
    _eqcache[str(md_path)] = {"mtime": st.st_mtime, "size": st.st_size, "maths": out}
    return out


def analyze_equations(article):
    """Сквозная нумерация формул статьи + карта \\label → номер."""
    prefix = ".".join(article["codes"])
    counter = 0
    starts, labels = {}, {}
    for key, fname, _ in article["blocks"]:
        starts[key] = counter
        for tex in display_maths(article["dir"] / fname):
            counter += 1
            for m in re.finditer(r"\\label\{([^}]*)\}", tex):
                labels[m.group(1)] = f"{prefix}.{counter}"
    article["eqprefix"] = prefix
    article["eqstarts"] = starts
    article["eqlabels"] = labels


def link_foreign_labels(articles):
    """Метки формул из чужих статей: \\eqref{x} работает и между статьями.

    В eqlabels статьи добавляются чужие метки со значением «номер@путь-статьи»;
    Lua-фильтр по «@» понимает, что ссылка ведёт на другую страницу.
    Своя метка всегда в приоритете; неоднозначные (в двух статьях) не линкуются.
    """
    owners = {}
    for a in articles:
        for lbl, tag in a["eqlabels"].items():
            owners.setdefault(lbl, []).append((a["rel"], tag))
    for lbl, variants in owners.items():
        if len(variants) > 1:
            warn(f"метка формулы «{lbl}» определена сразу в статьях "
                 f'{", ".join(rel for rel, _ in variants)} — межстатейные ссылки '
                 f"на неё не работают, переименуйте метки")
    for a in articles:
        for lbl, variants in owners.items():
            if lbl not in a["eqlabels"] and len(variants) == 1:
                rel, tag = variants[0]
                a["eqlabels"][lbl] = f"{tag}@{rel}"


def eq_args(article, block_key):
    enc = "|".join(f"{k}={v}" for k, v in article["eqlabels"].items())
    return [
        "-M", f'eqprefix={article["eqprefix"]}',
        "-M", f'eqstart={article["eqstarts"][block_key]}',
        "-M", f"eqlabels={enc}",
    ]


# ---------------------------------------------------------------- картинки

def convert_pdf_to_svg(pdf_path, svg_path):
    if shutil.which("dvisvgm"):
        try:
            run(["dvisvgm", "--pdf", "--optimize", "-o", str(svg_path), str(pdf_path)])
            return
        except RuntimeError:
            pass
    if not shutil.which("pdftocairo"):
        raise ToolMissing("dvisvgm или pdftocairo (пакеты dvisvgm/ghostscript или poppler-utils)")
    run(["pdftocairo", "-svg", str(pdf_path), str(svg_path)])


def compile_figure(tex_path):
    """TikZ-исходник → (pdf, svg) в кэше. Ключ кэша — хэш исходника и макросов."""
    src = tex_path.read_bytes() + MACROS.read_bytes()
    key = hashlib.sha256(src).hexdigest()[:16]
    outdir = CACHE / "figures" / key
    pdf, svg = outdir / (tex_path.stem + ".pdf"), outdir / (tex_path.stem + ".svg")
    if pdf.exists() and svg.exists():
        return pdf, svg
    if not shutil.which("xelatex"):
        raise ToolMissing("xelatex")
    outdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        shutil.copy(tex_path, tmp / tex_path.name)
        env = dict(os.environ, TEXINPUTS=f".:{MACROS.parent}:")
        res = subprocess.run(
            ["xelatex", "-interaction=nonstopmode", "-halt-on-error", tex_path.name],
            cwd=tmp, capture_output=True, text=True, env=env,
        )
        built = tmp / (tex_path.stem + ".pdf")
        if res.returncode != 0 or not built.exists():
            # Неполный дистрибутив TeX (например BasicTeX) — не смерть сборки:
            # картинка пропускается с подсказкой, что доустановить
            m = re.search(r"File `([^']+)' not found", res.stdout)
            if m:
                raise ToolMissing(
                    f"LaTeX-пакет с файлом «{m.group(1)}» — доустановите TeX: "
                    f"см. раздел «Локальная сборка» в README "
                    f"(для BasicTeX: sudo tlmgr install standalone preview pgf "
                    f"collection-langcyrillic cm-super dvisvgm)")
            raise RuntimeError(f"Не собралась картинка {tex_path}:\n{res.stdout[-2000:]}")
        shutil.copy(built, pdf)
    convert_pdf_to_svg(pdf, svg)
    return pdf, svg


def prepare_figures(article):
    """Собирает фигуры статьи. Возвращает (files_for_site, dir_for_latex)."""
    figsrc = article["dir"] / "figures"
    latex_dir = CACHE / "figbuild" / article["rel"]
    latex_dir.mkdir(parents=True, exist_ok=True)
    site_files = []
    if not figsrc.is_dir():
        return site_files, latex_dir
    for f in sorted(figsrc.iterdir()):
        if f.name.startswith("."):
            continue
        if f.suffix == ".tex":
            try:
                pdf, svg = compile_figure(f)
            except ToolMissing as e:
                warn(f"{f.relative_to(CONTENT)}: картинка пропущена — не установлен {e}")
                continue
            shutil.copy(pdf, latex_dir / pdf.name)
            site_files.append(svg)
        else:
            shutil.copy(f, latex_dir / f.name)
            site_files.append(f)
    return site_files, latex_dir


# ---------------------------------------------------------------- pandoc

def pandoc_html(md_path, extra):
    res = run([
        "pandoc", str(md_path), "--from", "markdown", "--to", "html5",
        "--mathjax", "--shift-heading-level-by=1", "--lua-filter", str(FILTER),
        *extra,
    ])
    return res.stdout


def pandoc_latex(md_path, figdir, extra):
    res = run([
        "pandoc", str(md_path), "--from", "markdown", "--to", "latex",
        "--lua-filter", str(FILTER), "-M", f"figdir={figdir}",
        "-M", f"siteurl={SITE_URL}",
        *extra,
    ])
    return res.stdout


IMG_RE = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)")
XLINK_RE = re.compile(r"\]\(\s*@/([^)#\s]+)")


def collect_rels(tree):
    """Все валидные URL-пути (rel) групп и статей."""
    rels = set()

    def walk(node):
        rels.add(node["rel"])
        for a in node["articles"]:
            rels.add(a["rel"])
        for g in node["groups"]:
            walk(g)

    for sec in tree:
        walk(sec)
    return rels


def normalize_xlink(path):
    """Путь из @/-ссылки → URL-путь: с каждого сегмента срезается
    числовой префикс-порядок (можно писать и по папкам, и по URL)."""
    return "/".join(url_slug(seg) for seg in path.split("/") if seg)


def validate_refs(article, valid_rels):
    """Понятные предупреждения про несуществующие картинки и межстатейные ссылки."""
    for _, fname, _ in article["blocks"]:
        text = (article["dir"] / fname).read_text(encoding="utf-8")
        for m in IMG_RE.finditer(text):
            src = m.group(1)
            if src.startswith(("http://", "https://")):
                continue
            if "\\" in src:
                warn(f'{article["rel"]}/{fname}: картинка «{src}» — обратные слэши '
                     f"(Windows-путь) не работают, замените на /")
                continue
            if not src.startswith("figures/"):
                warn(f'{article["rel"]}/{fname}: картинка «{src}» лежит вне figures/ — '
                     f"на сайт и в PDF попадают только файлы из папки figures/ статьи")
                continue
            base = re.sub(r"\.tex$", "", src)
            if "." in Path(base).name:
                expected = article["dir"] / src
            else:
                expected = article["dir"] / (base + ".tex")
            if not expected.exists():
                warn(f'{article["rel"]}/{fname}: картинка «{src}» не найдена — '
                     f"ожидаю файл {expected}")
        for m in XLINK_RE.finditer(text):
            path = m.group(1).rstrip("/")
            norm = normalize_xlink(path)
            if norm not in valid_rels:
                warn(f'{article["rel"]}/{fname}: ссылка @/{path} никуда не ведёт — '
                     f"нет такой статьи или группы")
            elif norm != path:
                warn(f'{article["rel"]}/{fname}: в ссылке @/{path} числовые '
                     f"префиксы папок — в @/-ссылках пишется адрес сайта: @/{norm}")
        for m in re.finditer(r"\\eqref\{([^}]*)\}", text):
            if m.group(1) not in article["eqlabels"]:
                warn(f'{article["rel"]}/{fname}: \\eqref{{{m.group(1)}}} — метка '
                     f"не найдена ни в одной статье, ссылка отобразится как (??)")


# ---------------------------------------------------------------- HTML-страницы

def render_template(tpl, mapping):
    for k, v in mapping.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    return tpl


def esc(s):
    return html.escape(str(s), quote=True)


def sidebar_html(tree, current):
    """current — путь страницы относительно site/ (например 'a/b/c/index.html').

    Дерево сворачиваемое: раскрыты верхний уровень и цепочка до текущей
    страницы (это решается здесь, при сборке — без мигания на клиенте);
    остальным управляет пользователь, app.js запоминает его выбор."""
    def link(href, text):
        cur = ' class="current"' if href == current else ""
        return f'<a href="{{{{root}}}}/{href}"{cur}>{esc(text)}</a>'

    def contains_current(node):
        if f'{node["rel"]}/index.html' == current:
            return True
        return any(contains_current(g) for g in node["groups"]) or \
            any(f'{a["rel"]}/index.html' == current for a in node["articles"])

    def group_items(node, top=False):
        classes = "nav-group" + (" nav-top" if top else "")
        if top or contains_current(node):
            classes += " open"
        title_link = link(f'{node["rel"]}/index.html', node["meta"]["title"])
        if not node["groups"] and not node["articles"]:
            # Пустая глава-заготовка: раскрывать нечего — точка вместо шеврона
            return [f'<li class="{classes}">',
                    f'<div class="nav-row"><span class="nav-dot"></span>{title_link}</div>',
                    "</li>"]
        out = [
            f'<li class="{classes}" data-nav="{esc(node["rel"])}">',
            '<div class="nav-row">'
            '<button class="nav-toggle" type="button" aria-label="Свернуть или развернуть"></button>'
            + title_link
            + "</div>",
            '<ul class="nav-children">',
        ]
        for g in node["groups"]:
            out.extend(group_items(g))
        for a in node["articles"]:
            out.append('<li class="nav-leaf">'
                       + link(f'{a["rel"]}/index.html', a["meta"]["title"]) + "</li>")
        out.append("</ul></li>")
        return out

    out = ['<ul class="nav-tree">']
    for sec in tree:
        out.extend(group_items(sec, top=True))
    out.append("</ul>")
    return "\n".join(out)


class SiteWriter:
    def __init__(self, tree, config):
        self.tree = tree
        self.config = config
        self.base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        self.math_macros = json.dumps(parse_math_macros(), ensure_ascii=False)
        maintainers = ", ".join(config.get("maintainers", []))
        self.footer = (
            f"{esc(config['title'])} · {esc(maintainers)} · "
            '<a href="https://github.com/NoblFriend/astro-methodical">исходники и контрибьюты</a>'
        )

    def write_page(self, rel_path, title, content, crumbs):
        out = SITE / rel_path
        out.parent.mkdir(parents=True, exist_ok=True)
        depth = len(Path(rel_path).parts) - 1
        root = "." if depth == 0 else "/".join([".."] * depth)
        page = render_template(self.base, {
            "title": f"{esc(title)} · {esc(self.config['title'])}",
            "site_title": esc(self.config["title"]),
            "site_tagline": esc(self.config.get("tagline", "")),
            "math_macros": self.math_macros,
            "sidebar": sidebar_html(self.tree, rel_path),
            "breadcrumbs": crumbs,
            "content": content,
            "footer": self.footer,
        })
        page = page.replace("{{root}}", root)
        out.write_text(page, encoding="utf-8")

    def crumbs(self, parts):
        items = []
        for text, href in parts:
            if href:
                items.append(f'<a href="{{{{root}}}}/{href}">{esc(text)}</a>')
            else:
                items.append(esc(text))
        return " / ".join(items)


def authors_line(authors):
    if not authors:
        return ""
    if isinstance(authors, str):
        authors = [authors]
    return ", ".join(esc(a) for a in authors)


def article_content_html(art):
    meta = art["meta"]
    pdf_href = f'{{{{root}}}}/pdf/{art["rel"]}.pdf'
    parts = [
        "<article>",
        '<header class="article-header">',
        f'<h1>{esc(meta["title"])}</h1>',
        f'<p class="article-description">{esc(meta["description"])}</p>',
    ]
    al = authors_line(meta.get("authors"))
    if al:
        parts.append(f'<p class="article-authors">Авторы: {al}</p>')
    parts.append(
        '<div class="article-actions">'
        f'<a href="{pdf_href}">⬇ PDF статьи</a>'
        '<button onclick="window.print()">🖨 Печать текущего вида</button>'
        "</div>"
    )
    parts.append("</header>")
    parts.append('<div id="block-panel-slot"></div>')
    parts.append('<div class="article-blocks">')

    block_authors = art["meta"].get("blocks", {}) or {}
    for key, fname, btitle in art["blocks"]:
        # id-prefix разводит якоря сносок и заголовков разных блоков
        fragment = pandoc_html(art["dir"] / fname,
                               eq_args(art, key) + ["--id-prefix", f"{key}-"])
        bmeta = block_authors.get(key, {}) or {}
        btitle_final = bmeta.get("title", btitle)
        ba = authors_line(bmeta.get("authors"))
        ba_html = f'<span class="block-authors">{ba}</span>' if ba else ""
        parts.append(
            f'<section class="block" data-block="{key}">'
            f'<div class="block-head"><h2>{esc(btitle_final)}</h2>{ba_html}</div>'
            f'<div class="block-body">{fragment}</div>'
            "</section>"
        )
    parts.append("</div></article>")
    return "\n".join(parts)


def card(href, title, description, meta_line=""):
    meta_html = f'<div class="card-meta">{meta_line}</div>' if meta_line else ""
    return (
        f'<li class="card"><h3><a href="{{{{root}}}}/{href}">{esc(title)}</a></h3>'
        f"<p>{esc(description)}</p>{meta_html}</li>"
    )


def write_group_pages(writer, node, crumb_chain):
    """Индексная страница группы + рекурсивно всё под ней."""
    crumbs_here = crumb_chain + [(node["meta"]["title"], None)]
    cards = []
    for g in node["groups"]:
        cards.append(card(f'{g["rel"]}/index.html', g["meta"]["title"],
                          g["meta"].get("description", ""),
                          f"статей: {article_count(g)}"))
    for a in node["articles"]:
        al = authors_line(a["meta"].get("authors"))
        cards.append(card(f'{a["rel"]}/index.html', a["meta"]["title"],
                          a["meta"]["description"], f"Авторы: {al}" if al else ""))
    if cards:
        actions = (f'<div class="article-actions">'
                   f'<a href="{{{{root}}}}/pdf/{node["rel"]}.pdf">⬇ PDF целиком</a></div>')
        body = f'<ul class="card-list">{"".join(cards)}</ul>'
    else:
        actions = ""
        body = '<p class="empty-group-note">Статей пока нет — глава в разработке.</p>'
    content = (
        f'<h1>{esc(node["meta"]["title"])}</h1>'
        f'<p class="article-description">{esc(node["meta"].get("description", ""))}</p>'
        f"{actions}{body}"
    )
    writer.write_page(f'{node["rel"]}/index.html', node["meta"]["title"], content,
                      writer.crumbs(crumbs_here))

    child_chain = crumb_chain + [(node["meta"]["title"], f'{node["rel"]}/index.html')]
    for g in node["groups"]:
        write_group_pages(writer, g, child_chain)
    for art in node["articles"]:
        write_article_page(writer, art, child_chain)


def write_article_page(writer, art, crumb_chain):
    log(f'статья: {art["rel"]}')
    site_figs, _ = prepare_figures(art)
    if site_figs:
        figout = SITE / art["rel"] / "figures"
        figout.mkdir(parents=True, exist_ok=True)
        for f in site_figs:
            shutil.copy(f, figout / f.name)
    writer.write_page(
        f'{art["rel"]}/index.html', art["meta"]["title"],
        article_content_html(art),
        writer.crumbs(crumb_chain + [(art["meta"]["title"], None)]),
    )


def find_article(tree, target_dir):
    """Статья по её папке + цепочка хлебных крошек до неё."""
    target = Path(target_dir).resolve()

    def walk(node, chain):
        chain = chain + [(node["meta"]["title"], f'{node["rel"]}/index.html')]
        for a in node["articles"]:
            if a["dir"].resolve() == target:
                return a, chain
        for g in node["groups"]:
            found = walk(g, chain)
            if found:
                return found
        return None

    for sec in tree:
        found = walk(sec, [("Главная", "index.html")])
        if found:
            return found
    return None


def build_one_article(tree, config, writer, target_dir):
    """Быстрый путь для предпросмотра: перестраивается только одна статья.

    Остальные страницы не трогаются (их ссылки на номера формул этой статьи
    могут устареть до следующей полной сборки — для превью это ок)."""
    found = find_article(tree, target_dir)
    if not found:
        return False
    art, chain = found
    all_articles = [a for sec in tree for a in iter_articles(sec)]
    for a in all_articles:
        analyze_equations(a)  # быстро: карта формул кэшируется по mtime
    link_foreign_labels(all_articles)
    validate_refs(art, collect_rels(tree))
    write_article_page(writer, art, chain)
    return True


def build_site(tree, config, writer):
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "assets").mkdir(parents=True)
    for f in THEME.iterdir():
        shutil.copy(f, SITE / "assets" / f.name)

    all_articles = [a for sec in tree for a in iter_articles(sec)]
    for art in all_articles:
        analyze_equations(art)
    link_foreign_labels(all_articles)
    valid_rels = collect_rels(tree)
    for art in all_articles:
        validate_refs(art, valid_rels)

    cards = []
    for sec in tree:
        cards.append(card(f'{sec["rel"]}/index.html', sec["meta"]["title"],
                          sec["meta"].get("description", ""),
                          f"статей: {article_count(sec)}"))
    home = (
        f'<h1>{esc(config["title"])}</h1>'
        f'<p class="article-description">{esc(config.get("tagline", ""))}</p>'
        '<div class="article-actions"><a href="{{root}}/pdf/uchebnik.pdf">⬇ Весь учебник в PDF</a></div>'
        f'<ul class="card-list">{"".join(cards)}</ul>'
    )
    writer.write_page("index.html", "Главная", home, writer.crumbs([("Главная", None)]))

    for sec in tree:
        write_group_pages(writer, sec, [("Главная", "index.html")])


# ---------------------------------------------------------------- PDF

LATEX_SPECIALS = {
    "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
    "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    "\\": r"\textbackslash{}",
}


def lesc(s):
    return "".join(LATEX_SPECIALS.get(ch, ch) for ch in str(s))


def article_body_latex(art, heading="\\section"):
    meta = art["meta"]
    _, figdir = prepare_figures(art)
    out = ["\\resetproblems"]
    al = meta.get("authors")
    if al:
        if isinstance(al, str):
            al = [al]
        out.append(f'\\noindent{{\\small\\itshape Авторы: {lesc(", ".join(al))}}}\\par\\medskip')
    desc = meta.get("description")
    if desc:
        out.append(f"\\noindent{{\\itshape {lesc(desc)}}}\\par\\medskip")
    block_meta = meta.get("blocks", {}) or {}
    for key, fname, btitle in art["blocks"]:
        bmeta = block_meta.get(key, {}) or {}
        out.append(f'{heading}{{{lesc(bmeta.get("title", btitle))}}}')
        out.append(pandoc_latex(art["dir"] / fname, figdir, eq_args(art, key)))
    return "\n".join(out)


def compile_pdf(tex_source, out_pdf):
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TEXINPUTS=f".:{PREAMBLE.parent}:{MACROS.parent}:")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "main.tex").write_text(tex_source, encoding="utf-8")
        for _ in range(2):  # два прохода — оглавление и ссылки
            res = subprocess.run(
                ["xelatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
                cwd=tmp, capture_output=True, text=True, env=env,
            )
        # -halt-on-error оставляет обрезанный main.pdf — не выдавать его за готовый
        if res.returncode != 0 or not (tmp / "main.pdf").exists():
            raise RuntimeError(f"PDF не собрался ({out_pdf.name}):\n{res.stdout[-3000:]}")
        shutil.copy(tmp / "main.pdf", out_pdf)
    log(f"pdf: {out_pdf.relative_to(SITE)}")


def doc_wrap(cls, title, subtitle, body, toc=False):
    toc_tex = "\\tableofcontents\\clearpage" if toc else ""
    return f"""\\documentclass[11pt]{{{cls}}}
\\input{{preamble.tex}}
\\input{{macros.tex}}
\\begin{{document}}
\\begin{{center}}
{{\\LARGE\\bfseries\\sffamily {lesc(title)}}}\\\\[0.5em]
{{\\itshape {lesc(subtitle)}}}
\\end{{center}}
\\medskip
{toc_tex}
{body}
\\end{{document}}
"""


def leaf_groups(node, titles=None):
    """Группы, непосредственно содержащие статьи, с цепочкой заголовков от node."""
    titles = titles or []
    out = []
    if node["articles"]:
        out.append((titles, node))
    for g in node["groups"]:
        out.extend(leaf_groups(g, titles + [g["meta"]["title"]]))
    return out


def group_body_latex(node):
    leafs = leaf_groups(node)
    single = len(leafs) == 1 and leafs[0][1] is node
    out = []
    for titles, leaf in leafs:
        if not single:
            part_title = " · ".join(titles) if titles else leaf["meta"]["title"]
            out.append(f"\\part{{{lesc(part_title)}}}")
        for art in leaf["articles"]:
            out.append(f'\\chapter{{{lesc(art["meta"]["title"])}}}')
            out.append(article_body_latex(art, heading="\\section"))
    return "\n".join(out)


def build_pdfs(tree, config):
    pdf_root = SITE / "pdf"
    site_title = config["title"]

    def walk(node, parent_titles):
        if not article_count(node):
            return  # пустая глава-заготовка — PDF не из чего собирать
        # PDF самой группы
        subtitle = " · ".join(parent_titles) if parent_titles else site_title
        compile_pdf(
            doc_wrap("report", node["meta"]["title"], subtitle,
                     group_body_latex(node), toc=True),
            pdf_root / f'{node["rel"]}.pdf',
        )
        for art in node["articles"]:
            compile_pdf(
                doc_wrap("article", art["meta"]["title"],
                         " · ".join(parent_titles + [node["meta"]["title"]]),
                         article_body_latex(art, heading="\\section")),
                pdf_root / f'{art["rel"]}.pdf',
            )
        for g in node["groups"]:
            walk(g, parent_titles + [node["meta"]["title"]])

    for sec in tree:
        walk(sec, [])

    # Весь учебник: части = цепочки «Раздел · … · Подтема»
    book = []
    for sec in tree:
        for titles, leaf in leaf_groups(sec, [sec["meta"]["title"]]):
            book.append(f'\\part{{{lesc(" · ".join(titles))}}}')
            for art in leaf["articles"]:
                book.append(f'\\chapter{{{lesc(art["meta"]["title"])}}}')
                book.append(article_body_latex(art, heading="\\section"))
    compile_pdf(
        doc_wrap("report", site_title, config.get("tagline", ""), "\n".join(book), toc=True),
        pdf_root / "uchebnik.pdf",
    )


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", action="store_true", help="собрать и PDF тоже")
    ap.add_argument("--clean", action="store_true", help="очистить site/ и .cache/")
    ap.add_argument("--only", metavar="DIR",
                    help="пересобрать только одну статью (папка в content/); "
                         "используется живым предпросмотром")
    args = ap.parse_args()

    if args.clean:
        for d in (SITE, CACHE):
            if d.exists():
                shutil.rmtree(d)

    preflight(need_pdf=args.pdf)
    config = read_yaml(CONFIG)
    global SITE_URL
    SITE_URL = str(config.get("url", "")).rstrip("/")
    tree = load_tree()
    if not tree:
        warn("в content/ не найдено ни одной статьи")
        sys.exit(1)

    try:
        writer = SiteWriter(tree, config)
        if args.only and (SITE / "assets").is_dir():
            if build_one_article(tree, config, writer, args.only):
                save_eqcache()
                return
            warn(f"--only: статья {args.only} не найдена — собираю всё")
        build_site(tree, config, writer)
        save_eqcache()
        log(f"сайт готов: {SITE}")

        if args.pdf:
            build_pdfs(tree, config)
            log("все PDF готовы")
    except ToolMissing as e:
        warn(f"не установлена программа «{e}» — см. раздел «Локальная сборка» в README")
        sys.exit(1)


if __name__ == "__main__":
    main()
