/* Клиентская логика: тема, панель блоков, печать, поиск. */
(function () {
  "use strict";

  var ROOT = document.documentElement.getAttribute("data-root") || ".";

  /* ---------- Тема ----------
     Начальное применение — инлайн-скриптом в <head> (до отрисовки).
     Хранится "dark" | "light" — раньше светлая писалась пустой строкой
     и не переживала переход на другую страницу. */
  var THEME_KEY = "metod-theme";
  function applyTheme(t) {
    if (t === "dark") document.documentElement.setAttribute("data-theme", "dark");
    else document.documentElement.removeAttribute("data-theme");
  }

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("theme-toggle");
    if (btn) {
      btn.addEventListener("click", function () {
        var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
        applyTheme(next);
        localStorage.setItem(THEME_KEY, next);
      });
    }
    initBlockPanel();
    initSearch();
  });

  /* ---------- Панель блоков ---------- */
  var PREFS_KEY = "metod-blocks-v1";

  function loadPrefs() {
    try { return JSON.parse(localStorage.getItem(PREFS_KEY)) || {}; }
    catch (e) { return {}; }
  }
  function savePrefs(p) { localStorage.setItem(PREFS_KEY, JSON.stringify(p)); }

  function initBlockPanel() {
    var container = document.querySelector(".article-blocks");
    var panelSlot = document.getElementById("block-panel-slot");
    if (!container || !panelSlot) return;

    var sections = Array.prototype.slice.call(container.querySelectorAll(".block"));
    if (sections.length < 2) return;

    var prefs = loadPrefs();
    var hidden = prefs.hidden || [];
    var order = (prefs.order || []).slice();

    // Сохранённый порядок чистим от исчезнувших блоков, а блоки, которых
    // в настройках ещё нет (новые/переименованные), вставляем не в конец,
    // а на их каноническое место.
    var keys = sections.map(function (s) { return s.dataset.block; });
    order = order.filter(function (k) { return keys.indexOf(k) !== -1; });
    keys.forEach(function (k) {
      if (order.indexOf(k) !== -1) return;
      var ci = keys.indexOf(k);
      var insertAt = order.length;
      for (var j = 0; j < order.length; j++) {
        if (keys.indexOf(order[j]) > ci) { insertAt = j; break; }
      }
      order.splice(insertAt, 0, k);
    });

    function byKey(k) {
      return sections.filter(function (s) { return s.dataset.block === k; })[0];
    }

    function apply() {
      order.forEach(function (k) {
        var sec = byKey(k);
        if (sec) container.appendChild(sec);
        if (sec) sec.classList.toggle("hidden", hidden.indexOf(k) !== -1);
      });
      savePrefs({ order: order, hidden: hidden });
      renderPanel();
    }

    var panel = document.createElement("details");
    panel.className = "block-panel";
    panel.innerHTML =
      "<summary>⚙ Настроить блоки</summary>" +
      "<div class='block-panel-body'><div class='block-panel-rows'></div>" +
      "<div class='block-panel-hint'>Галочка — показывать блок, стрелки — порядок. " +
      "Настройка запоминается для всех статей.</div></div>";
    panelSlot.appendChild(panel);

    function renderPanel() {
      var rows = panel.querySelector(".block-panel-rows");
      rows.innerHTML = "";
      order.forEach(function (k, i) {
        var sec = byKey(k);
        if (!sec) return;
        var title = sec.querySelector("h2") ? sec.querySelector("h2").textContent : k;
        var row = document.createElement("div");
        row.className = "block-panel-row";

        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.id = "bp-" + k;
        cb.checked = hidden.indexOf(k) === -1;
        cb.addEventListener("change", function () {
          if (cb.checked) hidden = hidden.filter(function (x) { return x !== k; });
          else if (hidden.indexOf(k) === -1) hidden.push(k);
          apply();
        });

        var label = document.createElement("label");
        label.htmlFor = cb.id;
        label.textContent = title;

        var moves = document.createElement("div");
        moves.className = "move-btns";
        var up = document.createElement("button");
        up.textContent = "↑";
        up.title = "Выше";
        up.disabled = i === 0;
        up.addEventListener("click", function () {
          order.splice(i - 1, 0, order.splice(i, 1)[0]);
          apply();
        });
        var down = document.createElement("button");
        down.textContent = "↓";
        down.title = "Ниже";
        down.disabled = i === order.length - 1;
        down.addEventListener("click", function () {
          order.splice(i + 1, 0, order.splice(i, 1)[0]);
          apply();
        });
        moves.appendChild(up);
        moves.appendChild(down);

        row.appendChild(cb);
        row.appendChild(label);
        row.appendChild(moves);
        rows.appendChild(row);
      });
    }

    apply();
  }

  /* ---------- Печать: раскрыть решения ---------- */
  var reclose = [];
  window.addEventListener("beforeprint", function () {
    reclose = [];
    document.querySelectorAll("details.solution:not([open])").forEach(function (d) {
      d.setAttribute("open", "");
      reclose.push(d);
    });
  });
  window.addEventListener("afterprint", function () {
    reclose.forEach(function (d) { d.removeAttribute("open"); });
    reclose = [];
  });

  /* ---------- Живой предпросмотр (make watch) ---------- */
  if (location.hostname === "localhost" || location.hostname === "127.0.0.1") {
    var lastStamp = null;
    var poll = setInterval(function () {
      fetch("/__stamp", { cache: "no-store" })
        .then(function (r) {
          if (!r.ok) throw new Error();
          return r.text();
        })
        .then(function (s) {
          if (lastStamp && s !== lastStamp) location.reload();
          lastStamp = s;
        })
        .catch(function () { clearInterval(poll); });
    }, 500);
  }

  /* ---------- Поиск (Pagefind, если собран) ---------- */
  function initSearch() {
    var box = document.getElementById("search-box");
    if (!box) return;
    var css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = ROOT + "/pagefind/pagefind-ui.css";
    var js = document.createElement("script");
    js.src = ROOT + "/pagefind/pagefind-ui.js";
    js.onload = function () {
      /* global PagefindUI */
      new PagefindUI({
        element: "#search-box",
        showSubResults: true,
        translations: { placeholder: "Поиск…", zero_results: "Ничего не найдено" }
      });
    };
    js.onerror = function () { box.style.display = "none"; };
    document.head.appendChild(css);
    document.head.appendChild(js);
  }
})();
