-- Общий Pandoc-фильтр: превращает fenced divs в наши окружения
-- и подгоняет пути картинок под формат вывода.
--
-- Окружения:
--   ::: {.definition title="..."}   — определение
--   ::: {.theorem title="..."}      — теорема
--   ::: {.law title="..."}          — закон (например, первое начало ТД)
--   ::: {.formula-box caption="..."} — формула в рамке с подписью
--   ::: note                         — заметка (левая черта, мелкий шрифт)
--   ::: problem                      — задача (автонумерация);
--       атрибуты: title="..." — название, comment="..." — подпись у названия
--       (например, какие навыки развивает); comment можно писать и как skills=
--   ::: solution                     — решение (на сайте сворачивается)
--
-- Картинки: `![подпись](figures/имя)` без расширения — это TikZ-исходник
-- figures/имя.tex; фильтр подставляет .svg для HTML и .pdf для LaTeX.

local function is_html() return FORMAT:match("html") ~= nil end
local function is_latex() return FORMAT:match("latex") ~= nil end

-- Разбирает markdown-строку атрибута (там может быть математика) в inline-элементы.
local function parse_inlines(s)
  local doc = pandoc.read(s, "markdown")
  if #doc.blocks > 0 and doc.blocks[1].content then
    return doc.blocks[1].content
  end
  return pandoc.Inlines{pandoc.Str(s)}
end

local function latex_env(el, env, opt)
  local blocks = pandoc.List{}
  local open = "\\begin{" .. env .. "}"
  if opt then open = open .. "[{" .. opt .. "}]" end
  blocks:insert(pandoc.RawBlock("latex", open))
  blocks:extend(el.content)
  blocks:insert(pandoc.RawBlock("latex", "\\end{" .. env .. "}"))
  return blocks
end

-- Окружения-«коробки» с бейджем и необязательным названием:
-- класс → (LaTeX-окружение, текст бейджа)
local BADGE_ENVS = {
  definition = {latex = "defbox", badge = "Определение"},
  theorem    = {latex = "thmbox", badge = "Теорема"},
  law        = {latex = "lawbox", badge = "Закон"},
}

local function badge_env(el, spec)
  local title = el.attributes["title"]
  if is_latex() then
    return latex_env(el, spec.latex, title)
  elseif is_html() then
    local label = pandoc.Inlines{pandoc.Span(pandoc.Str(spec.badge), {class = "env-badge"})}
    if title then
      label:insert(pandoc.Space())
      label:insert(pandoc.Span(parse_inlines(title), {class = "env-name"}))
    end
    local blocks = pandoc.List{pandoc.Plain(label)}
    blocks:extend(el.content)
    return pandoc.Div(blocks, el.attr)
  end
end

function Div(el)
  local c = el.classes

  for cls, spec in pairs(BADGE_ENVS) do
    if c:includes(cls) then
      return badge_env(el, spec)
    end
  end

  if c:includes("formula-box") then
    local caption = el.attributes["caption"]
    if is_latex() then
      local blocks = pandoc.List{pandoc.RawBlock("latex", "\\begin{formulabox}")}
      blocks:extend(el.content)
      if caption then
        blocks:insert(pandoc.RawBlock("latex", "\\tcblower"))
        blocks:insert(pandoc.Plain(parse_inlines(caption)))
      end
      blocks:insert(pandoc.RawBlock("latex", "\\end{formulabox}"))
      return blocks
    elseif is_html() then
      local blocks = pandoc.List(el.content)
      if caption then
        blocks:insert(pandoc.Div(pandoc.Plain(parse_inlines(caption)), {class = "formula-caption"}))
      end
      return pandoc.Div(blocks, el.attr)
    end

  elseif c:includes("note") then
    if is_latex() then
      return latex_env(el, "noteenv")
    end
    -- HTML: остаётся <div class="note">, стилизуется CSS

  elseif c:includes("problem") then
    local title = el.attributes["title"]
    local comment = el.attributes["comment"] or el.attributes["skills"]
    if is_latex() then
      local opt
      if title or comment then
        opt = title or ""
        if comment then
          opt = opt .. "\\hfill{\\normalfont\\small\\itshape " .. comment .. "}"
        end
      end
      return latex_env(el, "problembox", opt)
    elseif is_html() then
      -- Шапка задачи: номер (CSS-счётчик) + название + комментарий
      local head = pandoc.Inlines{pandoc.Span(pandoc.Inlines{}, {class = "problem-num"})}
      if title then
        head:insert(pandoc.Space())
        head:insert(pandoc.Span(parse_inlines(title), {class = "problem-title"}))
      end
      if comment then
        head:insert(pandoc.Space())
        head:insert(pandoc.Span(parse_inlines(comment), {class = "problem-comment"}))
      end
      local blocks = pandoc.List{pandoc.Div({pandoc.Plain(head)}, {class = "problem-head"})}
      blocks:extend(el.content)
      return pandoc.Div(blocks, el.attr)
    end

  elseif c:includes("solution") then
    if is_latex() then
      return latex_env(el, "solutionenv")
    elseif is_html() then
      return {
        pandoc.RawBlock("html", "<details class=\"solution\"><summary>Решение</summary>"),
        pandoc.Div(el.content, {class = "solution-body"}),
        pandoc.RawBlock("html", "</details>"),
      }
    end
  end
end

-- Финальный проход: пути картинок + нумерация формул.
--
-- Метаданные от сборщика:
--   eqprefix — иерархический префикс статьи, например "А.1.2.1"
--   eqstart  — сколько выключных формул было в предыдущих блоках статьи
--   eqlabels — карта "label=номер|label=номер" для \label / \eqref
function Pandoc(doc)
  local function meta_str(key)
    return doc.meta[key] and pandoc.utils.stringify(doc.meta[key]) or nil
  end

  local figdir = meta_str("figdir")
  local prefix = meta_str("eqprefix")
  local siteurl = meta_str("siteurl")
  local start = tonumber(meta_str("eqstart") or "0") or 0
  local labels = {}
  for pair in (meta_str("eqlabels") or ""):gmatch("[^|]+") do
    local k, v = pair:match("^(.-)=(.*)$")
    if k then labels[k] = v end
  end

  -- Значение метки: «номер» (своя статья) или «номер@путь» (чужая статья).
  local function eq_target(v)
    local tag, rel = v:match("^(.-)@(.+)$")
    if tag then return tag, rel end
    return v, nil
  end

  -- Ссылка на формулу — кликабельная: ведёт на якорь формулы
  -- (в своей статье — просто #, в чужой — на её страницу).
  local function eq_link(v, text)
    local tag, rel = eq_target(v)
    if is_latex() then
      if rel then
        return "\\href{" .. (siteurl or "") .. "/" .. rel .. "/index.html#eq-" .. tag .. "}{" .. text .. "}"
      end
      return "\\hyperlink{eq-" .. tag .. "}{" .. text .. "}"
    end
    if rel then
      return "\\href{{{root}}/" .. rel .. "/index.html#eq-" .. tag .. "}{" .. text .. "}"
    end
    return "\\href{#eq-" .. tag .. "}{" .. text .. "}"
  end

  local function subst_refs(tex)
    tex = tex:gsub("\\eqref%{([^}]*)%}", function(l)
      local v = labels[l]
      if not v then return "(\\text{??})" end
      return eq_link(v, "(\\text{" .. eq_target(v) .. "})")
    end)
    tex = tex:gsub("\\ref%{([^}]*)%}", function(l)
      local v = labels[l]
      if not v then return "\\text{??}" end
      return eq_link(v, "\\text{" .. eq_target(v) .. "}")
    end)
    if not is_latex() then
      -- Расширение physics в MathJax не понимает \dd^3 (в LaTeX это работает);
      -- переписываем в эквивалентное \dd[3]
      tex = tex:gsub("\\dd%s*%^%s*{([^}]*)}", "\\dd[%1]")
      tex = tex:gsub("\\dd%s*%^%s*([^%s{])", "\\dd[%1]")
    end
    return tex
  end

  local eqn = 0
  doc.blocks = doc.blocks:walk{
    Math = function(m)
      if m.mathtype == "DisplayMath" then
        eqn = eqn + 1
        local tex = subst_refs(m.text:gsub("%s*\\label%{[^}]*%}", ""))
        if prefix then
          local tag = prefix .. "." .. tostring(start + eqn)
          if is_latex() then
            return pandoc.RawInline(
              "latex",
              "\\hypertarget{eq-" .. tag .. "}{}\\begin{equation*}"
                .. tex .. "\\tag{" .. tag .. "}\\end{equation*}")
          end
          m.text = tex .. "\\tag{" .. tag .. "}"
          -- Якорь сырым HTML: --id-prefix не должен его переименовывать,
          -- иначе ссылки из других блоков статьи промахнутся
          return {
            pandoc.RawInline("html", '<span id="eq-' .. tag .. '">'),
            m,
            pandoc.RawInline("html", "</span>"),
          }
        end
        m.text = tex
        return m
      end
      m.text = subst_refs(m.text)
      return m
    end,
    Link = function(a)
      -- Межстатейные ссылки: [текст](@/путь/к/статье) или (@/путь#якорь).
      -- Путь пишется по папкам content/; числовые префиксы-порядки в URL
      -- не попадают (то же правило, что url_slug в build.py).
      local rest = a.target:match("^@/(.*)$")
      if rest then
        local path, frag = rest:match("^([^#]*)#?(.*)$")
        path = path:gsub("/$", "")
        path = path:gsub("[^/]+", function(seg)
          local s = seg:gsub("^%d+[-_%. ]?", "")
          return s ~= "" and s or seg
        end)
        local suffix = "/index.html" .. (frag ~= "" and ("#" .. frag) or "")
        if is_latex() then
          a.target = (siteurl or "") .. "/" .. path .. suffix
        else
          a.target = "{{root}}/" .. path .. suffix
        end
        return a
      end
    end,
    RawInline = function(r)
      -- \eqref{...} прямо в тексте (вне математики)
      if r.format == "tex" or r.format == "latex" then
        local l = r.text:match("^\\eqref%{([^}]*)%}$")
        if l and labels[l] then
          local tag, rel = eq_target(labels[l])
          if is_latex() then
            if rel then
              return pandoc.RawInline("latex",
                "\\href{" .. (siteurl or "") .. "/" .. rel .. "/index.html#eq-" .. tag .. "}{(" .. tag .. ")}")
            end
            return pandoc.RawInline("latex", "\\hyperlink{eq-" .. tag .. "}{(" .. tag .. ")}")
          end
          local href = rel and ("{{root}}/" .. rel .. "/index.html#eq-" .. tag) or ("#eq-" .. tag)
          return pandoc.Link("(" .. tag .. ")", href)
        end
      end
    end,
    Image = function(img)
      local src = img.src
      -- TikZ-исходник можно указать и с расширением .tex — приведём к без него
      src = src:gsub("%.tex$", "")
      local has_ext = src:match("%.%w+$") ~= nil
      if not has_ext then
        src = src .. (is_latex() and ".pdf" or ".svg")
      end
      if is_latex() and figdir and not src:match("^/") then
        src = figdir .. "/" .. src:gsub("^figures/", "")
      end
      img.src = src
      return img
    end,
  }
  return doc
end
