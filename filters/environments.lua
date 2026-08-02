-- Общий Pandoc-фильтр: превращает fenced divs в наши окружения
-- и подгоняет пути картинок под формат вывода.
--
-- Окружения:
--   ::: {.definition title="..."}   — определение
--   ::: {.formula-box caption="..."} — формула в рамке с подписью
--   ::: note                         — заметка (левая черта, мелкий шрифт)
--   ::: problem                      — задача (автонумерация)
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

function Div(el)
  local c = el.classes

  if c:includes("definition") then
    local title = el.attributes["title"]
    if is_latex() then
      return latex_env(el, "defbox", title)
    elseif is_html() then
      local label = pandoc.Inlines{pandoc.Span(pandoc.Str("Определение"), {class = "env-badge"})}
      if title then
        label:insert(pandoc.Space())
        label:insert(pandoc.Span(parse_inlines(title), {class = "env-name"}))
      end
      local blocks = pandoc.List{pandoc.Plain(label)}
      blocks:extend(el.content)
      return pandoc.Div(blocks, el.attr)
    end

  elseif c:includes("formula-box") then
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
    if is_latex() then
      return latex_env(el, "problembox")
    end
    -- HTML: <div class="problem">, номер вешает CSS-счётчик

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
  local start = tonumber(meta_str("eqstart") or "0") or 0
  local labels = {}
  for pair in (meta_str("eqlabels") or ""):gmatch("[^|]+") do
    local k, v = pair:match("^(.-)=(.*)$")
    if k then labels[k] = v end
  end

  local function subst_refs(tex)
    tex = tex:gsub("\\eqref%{([^}]*)%}", function(l)
      return "(\\text{" .. (labels[l] or "??") .. "})"
    end)
    tex = tex:gsub("\\ref%{([^}]*)%}", function(l)
      return "\\text{" .. (labels[l] or "??") .. "}"
    end)
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
              "\\begin{equation*}" .. tex .. "\\tag{" .. tag .. "}\\end{equation*}")
          end
          m.text = tex .. "\\tag{" .. tag .. "}"
          return m
        end
        m.text = tex
        return m
      end
      m.text = subst_refs(m.text)
      return m
    end,
    RawInline = function(r)
      -- \eqref{...} прямо в тексте (вне математики)
      if r.format == "tex" or r.format == "latex" then
        local l = r.text:match("^\\eqref%{([^}]*)%}$")
        if l and labels[l] then
          if is_latex() then
            return pandoc.RawInline("latex", "(" .. labels[l] .. ")")
          end
          return pandoc.Str("(" .. labels[l] .. ")")
        end
      end
    end,
    Image = function(img)
      local src = img.src
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
