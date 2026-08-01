#!/usr/bin/env python3
"""Сборщик гибкого учебника.

content/ (markdown-блоки) → site/ (статический сайт) и site/pdf/ (LaTeX-PDF).

Использование:
    python build.py            # только сайт
    python build.py --pdf      # сайт + все PDF (статьи/подтемы/разделы/учебник)
    python build.py --clean    # снести site/ и .cache/ перед сборкой
"""

import argparse
import hashlib
import html
import json
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
    ("methods", "02-methods.md", "Методы решения задач"),
    ("derivations", "03-derivations.md", "Выводы формул"),
]


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


# ---------------------------------------------------------------- макросы

def parse_katex_macros():
    """\newcommand из macros.tex → словарь для KaTeX (число аргументов KaTeX выводит сам)."""
    text = MACROS.read_text(encoding="utf-8")
    macros = {}
    for m in re.finditer(r"\\(?:re)?newcommand\{(\\[A-Za-z]+)\}(?:\[\d+\])?", text):
        name = m.group(1)
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
        macros[name] = text[i + 1 : j]
    return macros


# ---------------------------------------------------------------- дерево контента

def load_node_meta(dirpath, fallback_title):
    metafile = dirpath / "_meta.yml"
    meta = read_yaml(metafile) if metafile.exists() else {}
    meta.setdefault("title", fallback_title)
    meta.setdefault("order", 999)
    meta.setdefault("description", "")
    return meta


def load_tree():
    """content/ → [{slug, meta, subtopics: [{slug, meta, articles: [...]}]}]"""
    sections = []
    for sec_dir in sorted(CONTENT.iterdir()):
        if not sec_dir.is_dir() or sec_dir.name.startswith(("_", ".")):
            continue
        sec = {"slug": sec_dir.name, "meta": load_node_meta(sec_dir, sec_dir.name), "subtopics": []}
        for sub_dir in sorted(sec_dir.iterdir()):
            if not sub_dir.is_dir() or sub_dir.name.startswith(("_", ".")):
                continue
            sub = {"slug": sub_dir.name, "meta": load_node_meta(sub_dir, sub_dir.name), "articles": []}
            for art_dir in sorted(sub_dir.iterdir()):
                if not art_dir.is_dir() or not (art_dir / "meta.yml").exists():
                    continue
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
                sub["articles"].append(
                    {"slug": art_dir.name, "dir": art_dir, "meta": meta, "blocks": blocks}
                )
            sub["articles"].sort(key=lambda a: (a["meta"]["order"], a["meta"]["title"]))
            if sub["articles"]:
                sec["subtopics"].append(sub)
        sec["subtopics"].sort(key=lambda s: (s["meta"]["order"], s["meta"]["title"]))
        if sec["subtopics"]:
            sections.append(sec)
    sections.sort(key=lambda s: (s["meta"]["order"], s["meta"]["title"]))
    return sections


# ---------------------------------------------------------------- картинки

def convert_pdf_to_svg(pdf_path, svg_path):
    try:
        run(["dvisvgm", "--pdf", "--optimize", "-o", str(svg_path), str(pdf_path)])
        return
    except (RuntimeError, FileNotFoundError):
        pass
    run(["pdftocairo", "-svg", str(pdf_path), str(svg_path)])


def compile_figure(tex_path):
    """TikZ-исходник → (pdf, svg) в кэше. Ключ кэша — хэш исходника и макросов."""
    src = tex_path.read_bytes() + MACROS.read_bytes()
    key = hashlib.sha256(src).hexdigest()[:16]
    outdir = CACHE / "figures" / key
    pdf, svg = outdir / (tex_path.stem + ".pdf"), outdir / (tex_path.stem + ".svg")
    if pdf.exists() and svg.exists():
        return pdf, svg
    outdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        shutil.copy(tex_path, tmp / tex_path.name)
        import os
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
    latex_dir = CACHE / "figbuild" / article["dir"].relative_to(CONTENT)
    latex_dir.mkdir(parents=True, exist_ok=True)
    site_files = []
    if not figsrc.is_dir():
        return site_files, latex_dir
    for f in sorted(figsrc.iterdir()):
        if f.name.startswith("."):
            continue
        if f.suffix == ".tex":
            pdf, svg = compile_figure(f)
            shutil.copy(pdf, latex_dir / pdf.name)
            site_files.append(svg)
        else:
            shutil.copy(f, latex_dir / f.name)
            site_files.append(f)
    return site_files, latex_dir


# ---------------------------------------------------------------- pandoc

def pandoc_html(md_path):
    res = run([
        "pandoc", str(md_path), "--from", "markdown", "--to", "html5",
        "--mathjax", "--shift-heading-level-by=1", "--lua-filter", str(FILTER),
    ])
    return res.stdout


def pandoc_latex(md_path, figdir):
    res = run([
        "pandoc", str(md_path), "--from", "markdown", "--to", "latex",
        "--lua-filter", str(FILTER), "-M", f"figdir={figdir}",
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

    out = ["<ul>"]
    for sec in tree:
        out.append('<li class="nav-section">')
        out.append(link(f'{sec["slug"]}/index.html', sec["meta"]["title"]))
        out.append("<ul>")
        for sub in sec["subtopics"]:
            out.append('<li class="nav-subtopic">')
            out.append(link(f'{sec["slug"]}/{sub["slug"]}/index.html', sub["meta"]["title"]))
            out.append("<ul>")
            for art in sub["articles"]:
                out.append("<li>")
                out.append(link(f'{sec["slug"]}/{sub["slug"]}/{art["slug"]}/index.html',
                                art["meta"]["title"]))
                out.append("</li>")
            out.append("</ul></li>")
        out.append("</ul></li>")
    out.append("</ul>")
    return "\n".join(out)


class SiteWriter:
    def __init__(self, tree, config):
        self.tree = tree
        self.config = config
        self.base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        self.katex_macros = json.dumps(parse_katex_macros(), ensure_ascii=False)
        maintainers = ", ".join(config.get("maintainers", []))
        self.footer = (
            f"{esc(config['title'])} · {esc(maintainers)} · "
            '<a href="https://github.com">исходники и контрибьюты — в git-репозитории</a>'
        )

    def write_page(self, rel_path, title, content, crumbs):
        """rel_path — путь относительно site/, например 'a/b/index.html'."""
        out = SITE / rel_path
        out.parent.mkdir(parents=True, exist_ok=True)
        depth = len(Path(rel_path).parts) - 1
        root = "." if depth == 0 else "/".join([".."] * depth)
        page = render_template(self.base, {
            "title": f"{esc(title)} · {esc(self.config['title'])}",
            "site_title": esc(self.config["title"]),
            "site_tagline": esc(self.config.get("tagline", "")),
            "katex_macros": self.katex_macros,
            "sidebar": sidebar_html(self.tree, rel_path),
            "breadcrumbs": crumbs,
            "content": content,
            "footer": self.footer,
        })
        page = page.replace("{{root}}", root)
        out.write_text(page, encoding="utf-8")

    def crumbs(self, parts):
        """parts — [(текст, rel_href | None)], последний обычно без ссылки."""
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


def article_content_html(sec, sub, art):
    meta = art["meta"]
    pdf_href = f'{{{{root}}}}/pdf/{sec["slug"]}/{sub["slug"]}/{art["slug"]}.pdf'
    parts = [
        '<article>',
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

    block_authors = meta.get("blocks", {}) or {}
    for key, fname, btitle in art["blocks"]:
        fragment = pandoc_html(art["dir"] / fname)
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


def build_site(tree, config, writer):
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "assets").mkdir(parents=True)
    for f in THEME.iterdir():
        shutil.copy(f, SITE / "assets" / f.name)

    # Главная
    cards = []
    for sec in tree:
        n = sum(len(sub["articles"]) for sub in sec["subtopics"])
        cards.append(card(f'{sec["slug"]}/index.html', sec["meta"]["title"],
                          sec["meta"].get("description", ""),
                          f"подтем: {len(sec['subtopics'])} · статей: {n}"))
    home = (
        f'<h1>{esc(config["title"])}</h1>'
        f'<p class="article-description">{esc(config.get("tagline", ""))}</p>'
        '<div class="article-actions"><a href="{{root}}/pdf/uchebnik.pdf">⬇ Весь учебник в PDF</a></div>'
        f'<ul class="card-list">{"".join(cards)}</ul>'
    )
    writer.write_page("index.html", "Главная", home, writer.crumbs([("Главная", None)]))

    for sec in tree:
        sec_href = f'{sec["slug"]}/index.html'
        cards = []
        for sub in sec["subtopics"]:
            cards.append(card(f'{sec["slug"]}/{sub["slug"]}/index.html', sub["meta"]["title"],
                              sub["meta"].get("description", ""),
                              f"статей: {len(sub['articles'])}"))
        content = (
            f'<h1>{esc(sec["meta"]["title"])}</h1>'
            f'<p class="article-description">{esc(sec["meta"].get("description", ""))}</p>'
            f'<div class="article-actions"><a href="{{{{root}}}}/pdf/{sec["slug"]}.pdf">⬇ PDF раздела</a></div>'
            f'<ul class="card-list">{"".join(cards)}</ul>'
        )
        writer.write_page(sec_href, sec["meta"]["title"], content,
                          writer.crumbs([("Главная", "index.html"),
                                         (sec["meta"]["title"], None)]))

        for sub in sec["subtopics"]:
            sub_href = f'{sec["slug"]}/{sub["slug"]}/index.html'
            cards = []
            for art in sub["articles"]:
                meta_line = ""
                al = authors_line(art["meta"].get("authors"))
                if al:
                    meta_line = f"Авторы: {al}"
                cards.append(card(f'{sec["slug"]}/{sub["slug"]}/{art["slug"]}/index.html',
                                  art["meta"]["title"], art["meta"]["description"], meta_line))
            content = (
                f'<h1>{esc(sub["meta"]["title"])}</h1>'
                f'<p class="article-description">{esc(sub["meta"].get("description", ""))}</p>'
                f'<div class="article-actions">'
                f'<a href="{{{{root}}}}/pdf/{sec["slug"]}/{sub["slug"]}.pdf">⬇ PDF подтемы</a></div>'
                f'<ul class="card-list">{"".join(cards)}</ul>'
            )
            writer.write_page(sub_href, sub["meta"]["title"], content,
                              writer.crumbs([("Главная", "index.html"),
                                             (sec["meta"]["title"], sec_href),
                                             (sub["meta"]["title"], None)]))

            for art in sub["articles"]:
                log(f'статья: {sec["slug"]}/{sub["slug"]}/{art["slug"]}')
                site_figs, _ = prepare_figures(art)
                art_rel = f'{sec["slug"]}/{sub["slug"]}/{art["slug"]}'
                if site_figs:
                    figout = SITE / art_rel / "figures"
                    figout.mkdir(parents=True, exist_ok=True)
                    for f in site_figs:
                        shutil.copy(f, figout / f.name)
                writer.write_page(
                    f"{art_rel}/index.html", art["meta"]["title"],
                    article_content_html(sec, sub, art),
                    writer.crumbs([("Главная", "index.html"),
                                   (sec["meta"]["title"], sec_href),
                                   (sub["meta"]["title"], sub_href),
                                   (art["meta"]["title"], None)]),
                )


# ---------------------------------------------------------------- PDF

LATEX_SPECIALS = {
    "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
    "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    "\\": r"\textbackslash{}",
}


def lesc(s):
    return "".join(LATEX_SPECIALS.get(ch, ch) for ch in str(s))


def latex_fragments(art):
    """Блоки статьи → список (заголовок блока, latex-фрагмент)."""
    _, figdir = prepare_figures(art)
    frags = []
    block_meta = art["meta"].get("blocks", {}) or {}
    for key, fname, btitle in art["blocks"]:
        bmeta = block_meta.get(key, {}) or {}
        frags.append((bmeta.get("title", btitle), pandoc_latex(art["dir"] / fname, figdir)))
    return frags


def article_body_latex(art, heading="\\section"):
    meta = art["meta"]
    out = ["\\resetproblems"]
    al = meta.get("authors")
    if al:
        if isinstance(al, str):
            al = [al]
        out.append(f'\\noindent{{\\small\\itshape Авторы: {lesc(", ".join(al))}}}\\par\\medskip')
    desc = meta.get("description")
    if desc:
        out.append(f"\\noindent{{\\itshape {lesc(desc)}}}\\par\\medskip")
    for btitle, frag in latex_fragments(art):
        out.append(f"{heading}{{{lesc(btitle)}}}")
        out.append(frag)
    return "\n".join(out)


def compile_pdf(tex_source, out_pdf):
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    import os
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


def build_pdfs(tree, config):
    pdf_root = SITE / "pdf"
    site_title = config["title"]

    for sec in tree:
        sec_parts = []
        for sub in sec["subtopics"]:
            sub_chapters = []
            for art in sub["articles"]:
                body = article_body_latex(art, heading="\\section")
                # PDF отдельной статьи
                compile_pdf(
                    doc_wrap("article", art["meta"]["title"],
                             f'{sec["meta"]["title"]} · {sub["meta"]["title"]}', body),
                    pdf_root / sec["slug"] / sub["slug"] / f'{art["slug"]}.pdf',
                )
                sub_chapters.append(f'\\chapter{{{lesc(art["meta"]["title"])}}}\n{body}')
            sub_body = "\n".join(sub_chapters)
            # PDF подтемы
            compile_pdf(
                doc_wrap("report", sub["meta"]["title"], sec["meta"]["title"], sub_body, toc=True),
                pdf_root / sec["slug"] / f'{sub["slug"]}.pdf',
            )
            sec_parts.append((sub["meta"]["title"], sub_body))
        # PDF раздела
        sec_body = "\n".join(
            f"\\part{{{lesc(t)}}}\n{b}" for t, b in sec_parts
        )
        compile_pdf(
            doc_wrap("report", sec["meta"]["title"], site_title, sec_body, toc=True),
            pdf_root / f'{sec["slug"]}.pdf',
        )

    # Весь учебник
    book = []
    for sec in tree:
        for sub in sec["subtopics"]:
            book.append(f'\\part{{{lesc(sec["meta"]["title"])} · {lesc(sub["meta"]["title"])}}}')
            for art in sub["articles"]:
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
