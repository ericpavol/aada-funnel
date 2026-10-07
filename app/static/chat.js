/* Ask-the-data chat panel. The conversation lives in this tab only
 * (sessionStorage): each question sends the earlier questions and answers as
 * plain text, plus the page's query string so the answer defaults to the
 * view the user is on. The server answers using the app's own totals. */
(function () {
  "use strict";
  var KEY = "aada-chat-v1";
  var fab = document.getElementById("chatFab");
  var panel = document.getElementById("chatPanel");
  if (!fab || !panel) return;
  var log = document.getElementById("chatLog");
  var form = document.getElementById("chatForm");
  var input = document.getElementById("chatInput");
  var send = document.getElementById("chatSend");
  var hello = log.querySelector(".chat-hello");
  var history = [];          // [{role, text}] exactly as sent / received
  var busy = false;

  function load() {
    try { history = JSON.parse(sessionStorage.getItem(KEY) || "[]") || []; }
    catch (e) { history = []; }
  }
  function save() {
    try { sessionStorage.setItem(KEY, JSON.stringify(history.slice(-40))); } catch (e) {}
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  /* A deliberately small markdown subset: tables, bullets, bold, line breaks.
   * Everything is escaped FIRST, so model output can never inject markup. */
  function md(src) {
    var lines = esc(src).split("\n"), out = [], i = 0;
    function inline(t) {
      return t.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
    }
    while (i < lines.length) {
      var l = lines[i];
      if (/^\s*\|.*\|\s*$/.test(l)) {
        var rows = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { rows.push(lines[i]); i++; }
        var cells = function (r) { return r.trim().replace(/^\||\|$/g, "").split("|").map(function (c) { return c.trim(); }); };
        var body = rows.filter(function (r) { return !/^\s*\|[\s:\-|]+\|\s*$/.test(r); });
        var html = "<table>";
        body.forEach(function (r, k) {
          var tag = k === 0 ? "th" : "td";
          html += "<tr>" + cells(r).map(function (c) { return "<" + tag + ">" + inline(c) + "</" + tag + ">"; }).join("") + "</tr>";
        });
        out.push(html + "</table>");
        continue;
      }
      if (/^\s*[-*•] /.test(l)) {
        var items = [];
        while (i < lines.length && /^\s*[-*•] /.test(lines[i])) {
          items.push("<li>" + inline(lines[i].replace(/^\s*[-*•] /, "")) + "</li>"); i++;
        }
        out.push("<ul>" + items.join("") + "</ul>");
        continue;
      }
      if (/^#{1,4} /.test(l)) { out.push("<p><b>" + inline(l.replace(/^#{1,4} /, "")) + "</b></p>"); i++; continue; }
      if (l.trim() === "") { i++; continue; }
      var para = [];
      while (i < lines.length && lines[i].trim() !== "" && !/^\s*[-*•] /.test(lines[i]) &&
             !/^\s*\|.*\|\s*$/.test(lines[i]) && !/^#{1,4} /.test(lines[i])) { para.push(inline(lines[i])); i++; }
      out.push("<p>" + para.join("<br>") + "</p>");
    }
    return out.join("");
  }

  function bubble(role, html, extraClass) {
    if (hello) { hello.hidden = true; }
    var d = document.createElement("div");
    d.className = "chat-msg " + role + (extraClass ? " " + extraClass : "");
    d.innerHTML = html;
    log.appendChild(d);
    log.scrollTop = log.scrollHeight;
    return d;
  }
  function stripContext(t) { return String(t).replace(/^\[Context:[^\]]*\]\s*/, ""); }

  function render() {
    log.querySelectorAll(".chat-msg").forEach(function (n) { n.remove(); });
    if (hello) hello.hidden = history.length > 0;
    history.forEach(function (h) {
      bubble(h.role, h.role === "user" ? "<p>" + esc(stripContext(h.text)) + "</p>" : md(h.text));
    });
  }

  function open(on) {
    panel.hidden = !on;
    fab.setAttribute("aria-expanded", String(on));
    fab.classList.toggle("on", on);
    if (on) { setTimeout(function () { input.focus(); }, 0); log.scrollTop = log.scrollHeight; }
  }

  function ask(q) {
    q = q.trim();
    if (!q || busy) return;
    busy = true; send.disabled = true; input.value = "";
    bubble("user", "<p>" + esc(q) + "</p>");
    var wait = bubble("assistant",
      '<p class="chat-wait"><span class="spinner"></span> Looking at the numbers…</p>', "pending");
    fetch("/chat", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, history: history, page: location.search })
    }).then(function (r) {
      return r.json().catch(function () { return { error: "Unexpected response (" + r.status + ")." }; });
    }).then(function (res) {
      wait.remove();
      if (res.error) { bubble("assistant", "<p>" + esc(res.error) + "</p>", "err"); return; }
      var used = (res.tools || []).length
        ? '<div class="chat-used">Looked at: ' + esc(res.tools.join(", ")) + "</div>" : "";
      bubble("assistant", md(res.answer) + used);
      history.push({ role: "user", text: res.user_sent || q });
      history.push({ role: "assistant", text: res.answer });
      save();
    }).catch(function () {
      wait.remove();
      bubble("assistant", "<p>Couldn't reach the server. Try again.</p>", "err");
    }).then(function () { busy = false; send.disabled = false; input.focus(); });
  }

  fab.addEventListener("click", function () { open(panel.hidden); });
  document.getElementById("chatClose").addEventListener("click", function () { open(false); });
  document.getElementById("chatNew").addEventListener("click", function () {
    history = []; save(); render();
  });
  form.addEventListener("submit", function (e) { e.preventDefault(); ask(input.value); });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(input.value); }
  });
  log.addEventListener("click", function (e) {
    var b = e.target.closest(".chat-eg");
    if (b) ask(b.textContent);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !panel.hidden) open(false);
  });

  load(); render();
})();
