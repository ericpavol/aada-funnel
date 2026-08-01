/* AADA Funnel — token-bar filter picker.
 *
 * The bar itself is server-rendered: every active filter is a token and every ✕
 * is a real link, so reading and clearing filters needs no JavaScript. This file
 * only adds the "＋ Add filter" picker.
 *
 * Filter state lives entirely in the query string. Applying a change means
 * navigating, which is what makes shareable links and the back button work for
 * free — so selections accumulate inside an open picker and are applied when it
 * closes. That's one page load per picker session instead of one per checkbox,
 * and there is no bar-wide "Apply" button to forget.
 */
(function () {
  "use strict";

  var bar = document.getElementById("filterbar");
  if (!bar) return;
  var pop = document.getElementById("fbPop");
  if (!pop) return;

  var spec;
  try {
    spec = JSON.parse(bar.dataset.spec);
  } catch (e) {
    return;
  }
  var DIMS = spec.dimensions || [];
  var byKey = {};
  DIMS.forEach(function (d) { byKey[d.key] = d; });

  var open = null;   // {dim, selected:[], date:{field,from,to}, changed:bool}

  /* ------------------------------------------------------------ utilities */
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function num(n) {
    return n == null ? "" : Number(n).toLocaleString();
  }

  function navigate(mutate) {
    var p = new URLSearchParams(location.search);
    mutate(p);
    location.search = p.toString();
  }

  function place(anchor) {
    var br = bar.getBoundingClientRect();
    var ar = anchor.getBoundingClientRect();
    var w = pop.offsetWidth || 286;
    var left = Math.max(0, Math.min(ar.left - br.left, br.width - w));
    pop.style.left = left + "px";
    pop.style.top = (ar.bottom - br.top + 8) + "px";
  }

  var CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="3.4" stroke-linecap="round"><path d="M20 6 9 17l-5-5"/></svg>';
  var MAG = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2.2" stroke-linecap="round">' +
    '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.6-3.6"/></svg>';

  /* -------------------------------------------------------- dimension list */
  function renderDimensions(query) {
    var q = (query || "").toLowerCase();
    var rows = DIMS.filter(function (d) {
      return d.label.toLowerCase().indexOf(q) !== -1;
    }).map(function (d) {
      var n = d.type === "date" ? "" :
        '<span class="fb-kbd">' + (d.values ? d.values.length : "") + "</span>";
      var on = (d.selected && (d.type === "date"
        ? d.selected.field : d.selected.length)) ? " is-on" : "";
      return '<button type="button" class="fb-row fb-dim' + on + '" data-dim="' +
        esc(d.key) + '"><span class="lbl">' + esc(d.label) + "</span>" + n + "</button>";
    }).join("");

    pop.innerHTML =
      '<div class="fb-head">' + MAG +
      '<input type="text" id="fbQ" placeholder="Search filters…" autocomplete="off"' +
      ' value="' + esc(query || "") + '" aria-label="Search filters"></div>' +
      '<div class="fb-list">' + (rows ||
        '<div class="fb-none">No filter matches that.</div>') + "</div>";
  }

  /* ------------------------------------------------------------ value list */
  function renderValues(dim, query) {
    var q = (query || "").toLowerCase();
    var sel = open.selected;

    if (dim.type === "date") {
      var d = open.date;
      var opts = ['<option value="">— pick a date field —</option>'].concat(
        dim.fields.map(function (f) {
          return '<option value="' + esc(f.v) + '"' +
            (f.v === d.field ? " selected" : "") + ">" + esc(f.label) + "</option>";
        })).join("");
      pop.innerHTML =
        '<div class="fb-head fb-head-plain"><b>' + esc(dim.label) + "</b></div>" +
        '<div class="fb-body">' +
        '<label class="fb-lab">Date field</label>' +
        '<select id="fbField" aria-label="Date field">' + opts + "</select>" +
        '<div class="fb-dates">' +
        '<div><label class="fb-lab">From</label>' +
        '<input type="date" id="fbFrom" value="' + esc(d.from) + '" aria-label="From date"></div>' +
        '<div><label class="fb-lab">To</label>' +
        '<input type="date" id="fbTo" value="' + esc(d.to) + '" aria-label="To date"></div>' +
        "</div>" +
        (dim.span && dim.span[0]
          ? '<div class="fb-span">data spans ' + esc(dim.span[0]) + " → " +
            esc(dim.span[1]) + "</div>" : "") +
        "</div>" +
        '<div class="fb-foot"><span class="fb-note">applied when you close</span>' +
        '<button type="button" class="fb-link" data-clear="1">Clear</button></div>';
      return;
    }

    var matches = dim.values.filter(function (v) {
      return String(v.label).toLowerCase().indexOf(q) !== -1;
    });
    var rows = matches.slice(0, 300).map(function (v) {
      var on = sel.indexOf(v.v) !== -1;
      return '<div class="fb-line' + (on ? " is-sel" : "") + '">' +
        '<button type="button" class="fb-row fb-val' + (on ? " is-sel" : "") +
        '" data-v="' + esc(v.v) + '" aria-pressed="' + on + '">' +
        '<span class="fb-box">' + CHECK + "</span>" +
        '<span class="lbl">' + esc(v.label) + "</span>" +
        (v.n == null ? "" : '<span class="n">' + num(v.n) + "</span>") + "</button>" +
        // "only" isolates this value: selecting one thing used to mean clearing
        // the rest by hand.
        '<button type="button" class="fb-only" data-only="' + esc(v.v) +
        '" title="Filter to only ' + esc(v.label) + '">only</button>' +
        "</div>";
    }).join("");

    var more = matches.length > 300
      ? '<div class="fb-none">' + num(matches.length - 300) + " more — keep typing.</div>" : "";

    pop.innerHTML =
      '<div class="fb-head">' + MAG +
      '<input type="text" id="fbQ" placeholder="Search ' +
      esc(dim.label.toLowerCase()) + '…" autocomplete="off" value="' +
      esc(query || "") + '" aria-label="Search ' + esc(dim.label) + '"></div>' +
      '<div class="fb-list">' + (rows ||
        '<div class="fb-none">Nothing matches “' + esc(query) + '”.</div>') + more + "</div>" +
      '<div class="fb-foot"><span class="fb-note">' +
      (dim.type === "single"
        ? "pick one"
        : sel.length + " selected · applied when you close") + "</span>" +
      '<span class="fb-footacts">' +
      (dim.type === "single" ? "" :
        '<button type="button" class="fb-link" data-all="1">Select all</button>') +
      '<button type="button" class="fb-link" data-clear="1">Clear</button></span></div>';
  }

  /* ------------------------------------------------------------- open/close */
  function show(dimKey, anchor) {
    var dim = dimKey ? byKey[dimKey] : null;
    if (dim) {
      open = {
        dim: dim,
        selected: (dim.selected && dim.selected.slice) ? dim.selected.slice() : [],
        date: dim.type === "date"
          ? { field: dim.selected.field || "", from: dim.selected.from || "",
              to: dim.selected.to || "" }
          : null,
        changed: false
      };
      renderValues(dim, "");
    } else {
      open = { dim: null, selected: [], date: null, changed: false };
      renderDimensions("");
    }
    pop.hidden = false;
    place(anchor);
    var q = pop.querySelector("#fbQ") || pop.querySelector("select,input");
    if (q) q.focus();
  }

  function apply() {
    var st = open;
    if (!st || !st.dim || !st.changed) return false;
    var dim = st.dim;
    if (dim.type === "date") {
      navigate(function (p) {
        p.delete("date_field"); p.delete("date_from"); p.delete("date_to");
        if (st.date.field) {
          p.set("date_field", st.date.field);
          if (st.date.from) p.set("date_from", st.date.from);
          if (st.date.to) p.set("date_to", st.date.to);
        }
      });
    } else if (dim.type === "single") {
      navigate(function (p) {
        p.delete(dim.param);
        if (st.selected.length) p.set(dim.param, st.selected[0]);
      });
    } else {
      navigate(function (p) {
        p.delete(dim.param);
        st.selected.forEach(function (v) { p.append(dim.param, v); });
      });
    }
    return true;
  }

  function close(withApply) {
    if (withApply && apply()) return;   // navigating away; leave it open
    pop.hidden = true;
    pop.innerHTML = "";
    open = null;
  }

  /* ----------------------------------------------------------------- events */
  bar.addEventListener("click", function (ev) {
    // The outside-click handler below runs later on the same event. By then a
    // re-render may have replaced pop.innerHTML, detaching ev.target — so
    // bar.contains() would wrongly report "outside" and close the picker.
    // Flag the event here instead of relying on the node still being attached.
    ev._fbInside = true;
    var trigger = ev.target.closest("[data-fb-open]");
    if (trigger) {
      ev.preventDefault();
      var key = trigger.getAttribute("data-fb-open");
      var already = open && ((open.dim && open.dim.key) || "") === key && !pop.hidden;
      close(true);
      if (!already) show(key, trigger);
      return;
    }
    if (!pop.contains(ev.target)) return;

    var dimBtn = ev.target.closest(".fb-dim");
    if (dimBtn) {
      var anchor = bar.querySelector('[data-fb-open=""]');
      show(dimBtn.getAttribute("data-dim"), anchor || bar);
      return;
    }

    var valBtn = ev.target.closest(".fb-val");
    if (valBtn && open && open.dim) {
      var v = valBtn.getAttribute("data-v");
      if (open.dim.type === "single") {
        open.selected = open.selected[0] === v ? [] : [v];
        open.changed = true;
        close(true);
        return;
      }
      var i = open.selected.indexOf(v);
      if (i === -1) open.selected.push(v); else open.selected.splice(i, 1);
      open.changed = true;
      renderValues(open.dim, (pop.querySelector("#fbQ") || {}).value || "");
      var q = pop.querySelector("#fbQ");
      if (q) { q.focus(); }
      return;
    }

    var onlyBtn = ev.target.closest("[data-only]");
    if (onlyBtn && open && open.dim) {
      open.selected = [onlyBtn.getAttribute("data-only")];
      open.changed = true;
      close(true);
      return;
    }

    if (ev.target.closest("[data-all]") && open && open.dim) {
      // Every value ticked. Same result set as no filter at all, but it shows
      // as an explicit selection you can then subtract from.
      open.selected = (open.dim.values || []).map(function (v) { return v.v; });
      open.changed = true;
      renderValues(open.dim, (pop.querySelector("#fbQ") || {}).value || "");
      var q2 = pop.querySelector("#fbQ");
      if (q2) q2.focus();
      return;
    }

    if (ev.target.closest("[data-clear]") && open && open.dim) {
      open.selected = [];
      if (open.date) open.date = { field: "", from: "", to: "" };
      open.changed = true;
      close(true);
    }
  });

  pop.addEventListener("input", function (ev) {
    if (!open) return;
    if (ev.target.id === "fbQ") {
      var v = ev.target.value;
      if (open.dim) renderValues(open.dim, v); else renderDimensions(v);
      var q = pop.querySelector("#fbQ");
      if (q) { q.focus(); q.setSelectionRange(v.length, v.length); }
      return;
    }
    if (!open.date) return;
    if (ev.target.id === "fbField") { open.date.field = ev.target.value; open.changed = true; }
    if (ev.target.id === "fbFrom") { open.date.from = ev.target.value; open.changed = true; }
    if (ev.target.id === "fbTo") { open.date.to = ev.target.value; open.changed = true; }
  });

  document.addEventListener("click", function (ev) {
    if (pop.hidden) return;
    if (ev._fbInside || bar.contains(ev.target)) return;
    close(true);
  });

  document.addEventListener("keydown", function (ev) {
    if (pop.hidden) return;
    if (ev.key === "Escape") { ev.preventDefault(); close(true); }
    if (ev.key === "Enter" && open && open.dim && open.dim.type !== "date") {
      var first = pop.querySelector(".fb-val");
      if (first) { ev.preventDefault(); first.click(); }
    }
  });

  window.addEventListener("resize", function () {
    if (pop.hidden) return;
    var a = bar.querySelector('[data-fb-open=""]');
    if (a) place(a);
  });
})();
