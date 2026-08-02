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

# Канонический состав статьи: (ключ, имя файла, заголовок блока)
BLOCK_DEFS = [
    ("theory", "01-theory.md", "Обсуждение формул"),
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
    res = subprocess.run(cmd, capture_output=True, text=True, **kw)
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

def load_node_meta(dirpath, fallback_title):
    metafile = dirpath / "_meta.yml"
    meta = read_yaml(metafile) if metafile.exists() else {}
    meta.setdefault("title", fallback_title)
    meta.setdefault("order", 999)
    meta.setdefault("description", "")
    return meta


def load_article(art_dir):
    meta = read_yaml(art_dir / "meta.yml")
    for field in ("title", "description"):
        if field not in meta:
            warn(f"{art_dir}: в meta.yml нет поля «{field}»")
            meta.setdefault(field, art_dir.name)
    meta.setdefault("order", 999)
    blocks = []
    for key, fname, btitle in BLOCK_DEFS:
        if (art_dir / fname).exists():
            blocks.append((key, fname, btitle))
        else:
            warn(f"{art_dir}: нет блока {fname} — пропускаю")
    return {"slug": art_dir.name, "dir": art_dir, "meta": meta, "blocks": blocks}


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
        if (child / "meta.yml").exists():
            node["articles"].append(load_article(child))
        else:
            sub = load_group(child)
            if sub["groups"] or sub["articles"]:
                node["groups"].append(sub)
    node["groups"].sort(key=lambda g: (g["meta"]["order"], g["meta"]["title"]))
    node["articles"].sort(key=lambda a: (a["meta"]["order"], a["meta"]["title"]))
    return node


def assign_codes(sections):
    """Коды нумерации: раздел — буква, глубже — позиционные индексы."""
    def walk(node, codes, rel):
        node["codes"] = codes
        node["rel"] = rel  # путь относительно content/, POSIX-строка
        for i, g in enumerate(node["groups"], 1):
            walk(g, codes + [str(i)], f'{rel}/{g["slug"]}')
        for i, a in enumerate(node["articles"], 1):
            a["codes"] = codes + [str(i)]
            a["rel"] = f'{rel}/{a["slug"]}'

    for sec in sections:
        code = str(sec["meta"].get("code") or sec["meta"]["title"][0]).upper()
        walk(sec, [code], sec["slug"])


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

def display_maths(md_path):
    """Исходники выключных формул файла в порядке появления."""
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
        *extra,
    ])
    return res.stdout


# ---------------------------------------------------------------- HTML-страницы

def render_template(tpl, mapping):
    for k, v in mapping.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    return tpl


def esc(s):
    return html.escape(str(s), quote=True)


def sidebar_html(tree, current):
    """current — путь страницы относительно site/ (например 'a/b/c/index.html')."""
    def link(href, text, cls=""):
        cur = ' class="current"' if href == current else ""
        return f'<a href="{{{{root}}}}/{href}"{cur}>{esc(text)}</a>'

    def group_items(node, top=False):
        cls = "nav-section" if top else "nav-subtopic"
        out = [f'<li class="{cls}">', link(f'{node["rel"]}/index.html', node["meta"]["title"]), "<ul>"]
        for g in node["groups"]:
            out.extend(group_items(g))
        for a in node["articles"]:
            out.append("<li>" + link(f'{a["rel"]}/index.html', a["meta"]["title"]) + "</li>")
        out.append("</ul></li>")
        return out

    out = ["<ul>"]
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
    content = (
        f'<h1>{esc(node["meta"]["title"])}</h1>'
        f'<p class="article-description">{esc(node["meta"].get("description", ""))}</p>'
        f'<div class="article-actions"><a href="{{{{root}}}}/pdf/{node["rel"]}.pdf">⬇ PDF целиком</a></div>'
        f'<ul class="card-list">{"".join(cards)}</ul>'
    )
    writer.write_page(f'{node["rel"]}/index.html', node["meta"]["title"], content,
                      writer.crumbs(crumbs_here))

    child_chain = crumb_chain + [(node["meta"]["title"], f'{node["rel"]}/index.html')]
    for g in node["groups"]:
        write_group_pages(writer, g, child_chain)
    for art in node["articles"]:
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
            writer.crumbs(child_chain + [(art["meta"]["title"], None)]),
        )


def build_site(tree, config, writer):
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "assets").mkdir(parents=True)
    for f in THEME.iterdir():
        shutil.copy(f, SITE / "assets" / f.name)

    for art in [a for sec in tree for a in iter_articles(sec)]:
        analyze_equations(art)

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
        if not (tmp / "main.pdf").exists():
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
    args = ap.parse_args()

    if args.clean:
        for d in (SITE, CACHE):
            if d.exists():
                shutil.rmtree(d)

    preflight(need_pdf=args.pdf)
    config = read_yaml(CONFIG)
    tree = load_tree()
    if not tree:
        warn("в content/ не найдено ни одной статьи")
        sys.exit(1)

    writer = SiteWriter(tree, config)
    build_site(tree, config, writer)
    log(f"сайт готов: {SITE}")

    if args.pdf:
        build_pdfs(tree, config)
        log("все PDF готовы")


if __name__ == "__main__":
    main()
