/* AADA Funnel — theming + charts.
 *
 * Theme state lives on <html> as data-theme / data-accent and is persisted to
 * localStorage. It is stamped by an inline script in <head> (before paint) so
 * there is no flash of the wrong theme; this file only handles the controls and
 * rebuilds the charts when the palette changes.
 *
 * Chart colours are read from CSS custom properties, so the series palette and
 * the accent stay defined in exactly one place (app.css).
 */
(function () {
  "use strict";

  var LS_THEME = "aada.theme", LS_ACCENT = "aada.accent";
  var root = document.documentElement;

  /* ------------------------------------------------------------------ theme */
  function currentTheme() {
    return root.getAttribute("data-theme") ||
      (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  }

  function setTheme(t) {
    root.setAttribute("data-theme", t);
    try { localStorage.setItem(LS_THEME, t); } catch (e) {}
    syncThemeButton();
    rebuildCharts();
  }

  function syncSwatches() {
    var a = root.getAttribute("data-accent") || "lime";
    document.querySelectorAll(".swatch").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.dataset.accent === a));
    });
  }

  function setAccent(a) {
    root.setAttribute("data-accent", a);
    try { localStorage.setItem(LS_ACCENT, a); } catch (e) {}
    syncSwatches();
    rebuildCharts();
  }

  var SUN = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M18.7 5.3l-1.4 1.4M6.7 17.3l-1.4 1.4"/></svg>';
  var MOON = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a6.8 6.8 0 0 0 10.5 10.5Z"/></svg>';

  function syncThemeButton() {
    var btn = document.getElementById("themeToggle");
    if (!btn) return;
    var dark = currentTheme() === "dark";
    btn.innerHTML = dark ? SUN : MOON;
    btn.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
    btn.setAttribute("title", dark ? "Light mode" : "Dark mode");
  }

  /* ------------------------------------------------------------------ charts */
  var registry = [];   // {canvasId, build} — rebuilt on theme change
  var instances = {};

  function css(name) {
    return getComputedStyle(root).getPropertyValue(name).trim();
  }

  function palette() {
    var s = [];
    for (var i = 1; i <= 8; i++) s.push(css("--s" + i));
    return s;
  }

  function tokens() {
    return {
      series: palette(),
      accent: css("--accent"),
      accentStrong: css("--accent-strong"),
      surface: css("--surface"),
      ink: css("--ink"),
      ink2: css("--ink-2"),
      ink3: css("--ink-3"),
      grid: css("--grid"),
      axis: css("--axis")
    };
  }

  /* "<0.1%" rather than "0.0%" for a small-but-real share: rounding a genuine
   * 3-person slice down to a flat zero reads as a bug. True zero stays "0.0%". */
  var pctFmt = function (v) {
    if (v > 0 && v < 0.001) return "<0.1%";
    return (v * 100).toFixed(1) + "%";
  };
  var numFmt = function (n) { return Number(n).toLocaleString(); };

  function applyDefaults(t) {
    if (!window.Chart) return;
    Chart.defaults.font.family = "'Plex', -apple-system, sans-serif";
    Chart.defaults.font.size = 11;
    Chart.defaults.color = t.ink2;
    Chart.defaults.borderColor = t.grid;
    // Set the properties; do NOT replace the object. Assigning a bare
    // {duration, easing} over Chart.defaults.animation drops the rest of the
    // animation spec that Chart.js ships, which breaks the animator driving
    // tooltip opacity — the tooltip then activates on hover but stays stuck
    // near 0 opacity, i.e. hover silently does nothing on every chart.
    Chart.defaults.animation.duration = 620;
    Chart.defaults.animation.easing = "easeOutQuart";
    Chart.defaults.plugins.tooltip.backgroundColor = t.ink;
    Chart.defaults.plugins.tooltip.titleColor = t.surface;
    Chart.defaults.plugins.tooltip.bodyColor = t.surface;
    Chart.defaults.plugins.tooltip.padding = 10;
    Chart.defaults.plugins.tooltip.cornerRadius = 8;
    Chart.defaults.plugins.tooltip.displayColors = false;
    Chart.defaults.plugins.tooltip.titleFont = { weight: "600", size: 12 };
    Chart.defaults.plugins.legend.labels.boxWidth = 9;
    Chart.defaults.plugins.legend.labels.boxHeight = 9;
    Chart.defaults.plugins.legend.labels.padding = 11;
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.plugins.legend.labels.pointStyle = "rectRounded";
  }

  /* Draws the value at the end of each horizontal bar. This is the "relief"
   * the palette validator asks for: three light-mode series sit below 3:1 on
   * the surface, so values are always legible as text, never colour-only. */
  var endLabels = {
    id: "endLabels",
    afterDatasetsDraw: function (chart, args, opts) {
      var ctx = chart.ctx, meta = chart.getDatasetMeta(0);
      ctx.save();
      ctx.font = "500 11px 'Plex', sans-serif";
      ctx.fillStyle = opts.color;
      ctx.textBaseline = "middle";
      var skip = opts.skip || [];
      var fmt = opts.fmt || pctFmt;
      meta.data.forEach(function (bar, i) {
        if (skip[i]) return;
        var v = chart.data.datasets[0].data[i];
        ctx.textAlign = "left";
        ctx.fillText(fmt(v), bar.x + 8, bar.y);
      });
      ctx.restore();
    }
  };

  /* Value labels above grouped bars.
   *
   * Written as whole percents ("42" not "41.7%") because a grouped chart with 8
   * series only leaves ~25px per bar — the full string collides, two characters
   * don't. The axis title already says these are shares of the stage. Bars under
   * `min` are skipped: at that size the label would overlap its neighbour and the
   * bar is visually flat anyway, so the precise figure lives in the tooltip. */
  var barValueLabels = {
    id: "barValueLabels",
    afterDatasetsDraw: function (chart, args, opts) {
      var ctx = chart.ctx;
      var min = opts.min == null ? 0.01 : opts.min;
      ctx.save();
      ctx.font = "600 8.5px 'Plex', sans-serif";
      ctx.fillStyle = opts.color;
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      chart.data.datasets.forEach(function (ds, di) {
        if (chart.getDatasetMeta(di).hidden) return;
        chart.getDatasetMeta(di).data.forEach(function (bar, i) {
          var v = ds.data[i];
          if (v == null || v < min) return;
          ctx.fillText(Math.round(v * 100) + "%", bar.x, bar.y - 3);
        });
      });
      ctx.restore();
    }
  };

  /* Percentages drawn ON the doughnut arcs, so the split is readable without
   * hovering. Text wears the ink token with a surface-coloured halo rather than
   * a fixed white: that reads against every slice colour in both themes, where
   * plain white would vanish on the yellow and aqua slices in light mode.
   * Slices below `min` are skipped — the arc is too thin to hold the text, and
   * the legend carries their figure instead. */
  var arcLabels = {
    id: "arcLabels",
    afterDatasetsDraw: function (chart, args, opts) {
      var data = chart.data.datasets[0].data;
      var total = data.reduce(function (a, b) { return a + b; }, 0);
      if (!total) return;
      var ctx = chart.ctx;
      ctx.save();
      ctx.font = "600 11px 'Plex', sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.lineWidth = 3;
      ctx.lineJoin = "round";
      ctx.strokeStyle = opts.halo;
      ctx.fillStyle = opts.color;
      chart.getDatasetMeta(0).data.forEach(function (arc, i) {
        var share = data[i] / total;
        if (share < (opts.min == null ? .05 : opts.min)) return;
        var mid = (arc.startAngle + arc.endAngle) / 2;
        var r = (arc.innerRadius + arc.outerRadius) / 2;
        var x = arc.x + Math.cos(mid) * r;
        var y = arc.y + Math.sin(mid) * r;
        var txt = (share * 100).toFixed(share < .1 ? 1 : 0) + "%";
        ctx.strokeText(txt, x, y);
        ctx.fillText(txt, x, y);
      });
      ctx.restore();
    }
  };

  /* Puts the population in the doughnut hole — otherwise it is dead space and
   * the reader has no denominator for the percentages. */
  var donutCentre = {
    id: "donutCentre",
    afterDatasetsDraw: function (chart, args, opts) {
      var meta = chart.getDatasetMeta(0);
      if (!meta.data.length) return;
      var arc = meta.data[0];
      var ctx = chart.ctx;
      ctx.save();
      ctx.textAlign = "center";
      ctx.fillStyle = opts.color;
      ctx.font = "300 26px 'Plex', sans-serif";
      ctx.textBaseline = "alphabetic";
      ctx.fillText(opts.total, arc.x, arc.y + 3);
      ctx.font = "400 10px 'Plex', sans-serif";
      ctx.fillStyle = opts.subColor;
      ctx.textBaseline = "top";
      ctx.fillText(opts.caption, arc.x, arc.y + 9);
      ctx.restore();
    }
  };

  /* Vertical crosshair at the hovered point. On a chart with a dozen overlapping
   * lines, the tooltip alone doesn't tell you WHICH x you're reading. */
  var crosshair = {
    id: "crosshair",
    afterDatasetsDraw: function (chart, args, opts) {
      var active = chart.tooltip && chart.tooltip.getActiveElements
        ? chart.tooltip.getActiveElements() : [];
      if (!active.length) return;
      var x = active[0].element.x;
      var area = chart.chartArea, ctx = chart.ctx;
      ctx.save();
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = opts.color;
      ctx.lineWidth = 1;
      ctx.globalAlpha = .6;
      ctx.beginPath();
      ctx.moveTo(x, area.top);
      ctx.lineTo(x, area.bottom);
      ctx.stroke();
      ctx.restore();
    }
  };

  /* Labelled dashed reference line for the overall baseline. */
  function baselinePlugin(value, label) {
    return {
      id: "baseline",
      afterDatasetsDraw: function (chart) {
        var x = chart.scales.x.getPixelForValue(value);
        var area = chart.chartArea, ctx = chart.ctx;
        var t = tokens();
        ctx.save();
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = t.accentStrong;
        ctx.lineWidth = 1.25;
        ctx.globalAlpha = .85;
        ctx.beginPath();
        ctx.moveTo(x, area.top);
        ctx.lineTo(x, area.bottom);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
        ctx.font = "600 9.5px 'Plex', sans-serif";
        ctx.fillStyle = t.ink3;
        ctx.textAlign = "center";
        ctx.fillText(label, x, area.top - 5);
        ctx.restore();
      }
    };
  }

  var firstPaintDone = false;

  /* Re-registering the same canvas REPLACES its recipe rather than appending a
     second one — the instant pickers call the chart functions again on every
     tick, and a growing registry would rebuild each chart N times on the next
     theme switch. */
  function register(canvasId, build) {
    var entry = { id: canvasId, build: build };
    var at = registry.findIndex(function (e) { return e.id === canvasId; });
    if (at === -1) registry.push(entry); else registry[at] = entry;
    if (firstPaintDone) buildOne(entry, tokens());
  }

  function destroy(canvasId) {
    var at = registry.findIndex(function (e) { return e.id === canvasId; });
    if (at !== -1) registry.splice(at, 1);
    if (instances[canvasId]) { instances[canvasId].destroy(); delete instances[canvasId]; }
  }

  function buildOne(entry, t) {
    var el = document.getElementById(entry.id);
    if (!el) return;
    if (instances[entry.id]) { instances[entry.id].destroy(); }
    instances[entry.id] = entry.build(el, t);
  }

  function rebuildCharts() {
    if (!window.Chart) return;
    var t = tokens();
    applyDefaults(t);
    registry.forEach(function (entry) { buildOne(entry, t); });
    firstPaintDone = true;
  }

  /* ---- expandable horizontal bar chart -------------------------------
   * Shared by "Which channels convert" and "Which channels make up X".
   * Clicking a category (bar or its label) expands its sub-sources in place;
   * clicking again collapses. Expansion state is per-chart and remembered in
   * localStorage, so it survives the reloads that applying a filter causes.
   *
   * The canvas grows with the row count — a fixed height would squeeze the bars
   * to slivers once a couple of categories are open.
   */
  var ROW_PX = 34, CHART_MIN_PX = 298;
  var barCharts = {};   // canvasId -> {open:Set, flat:[], rows, opts}

  function loadOpen(canvasId) {
    var open = new Set();
    try {
      var saved = JSON.parse(localStorage.getItem("aada.expand." + canvasId) || "[]");
      if (Array.isArray(saved)) saved.forEach(function (k) { open.add(k); });
    } catch (e) {}
    return open;
  }

  function saveOpen(canvasId, open) {
    try {
      localStorage.setItem("aada.expand." + canvasId,
        JSON.stringify([].concat(Array.from(open))));
    } catch (e) {}
  }

  /** rows + expansion state -> flat display rows */
  function flattenRows(rows, open, opts) {
    var out = [];
    rows.forEach(function (r) {
      var kids = r.subs || [];
      var expandable = kids.length > 0;
      var expanded = expandable && open.has(r.channel);
      out.push({
        key: r.channel, label: r.channel, value: opts.value(r), row: r,
        isSub: false, expandable: expandable, expanded: expanded
      });
      if (!expanded) return;
      kids.forEach(function (sub) {
        out.push({
          key: r.channel + " › " + sub.channel, label: sub.channel,
          value: opts.value(sub), row: sub, isSub: true,
          expandable: false, expanded: false, parent: r
        });
      });
      if (r.hidden_subs) {
        out.push({
          key: r.channel + " __hidden",
          label: r.hidden_subs + " more below the threshold",
          value: 0, row: null, isSub: true, muted: true,
          expandable: false, expanded: false, parent: r
        });
      }
    });
    return out;
  }

  /** Which row a canvas-relative y falls on — works over the bar, the gap, AND
   * the axis-label gutter. Chart.js's own onClick only fires inside the plot
   * area, so clicking a category NAME (the obvious target) would be dead. */
  function rowFromY(chart, offsetY) {
    var scale = chart.scales.y;
    if (!scale) return null;
    var v = scale.getValueForPixel(offsetY);
    if (v == null || isNaN(v)) return null;
    v = Math.round(v);
    return (v >= 0 && v < chart.data.labels.length) ? v : null;
  }

  /** One listener per canvas, kept across rebuilds (the canvas element outlives
   * chart.destroy(), so re-adding on every rebuild would stack duplicates). */
  function wireExpandClicks(canvas, canvasId) {
    if (canvas.dataset.expandWired) return;
    canvas.dataset.expandWired = "1";

    function rowAt(ev) {
      var chart = instances[canvasId];
      var st = barCharts[canvasId];
      if (!chart || !st || !st.flat) return null;
      var idx = rowFromY(chart, ev.offsetY);
      return idx == null ? null : st.flat[idx];
    }

    canvas.addEventListener("click", function (ev) {
      var f = rowAt(ev);
      if (!f || !f.expandable) return;
      var st = barCharts[canvasId];
      if (st.open.has(f.key)) st.open.delete(f.key); else st.open.add(f.key);
      saveOpen(canvasId, st.open);
      rebuildCharts();
    });

    canvas.addEventListener("mousemove", function (ev) {
      var f = rowAt(ev);
      canvas.style.cursor = (f && f.expandable) ? "pointer" : "default";
    });

    canvas.addEventListener("mouseleave", function () {
      canvas.style.cursor = "default";
    });
  }

  function expandableBars(canvasId, rows, opts) {
    var state = barCharts[canvasId] || (barCharts[canvasId] = { open: loadOpen(canvasId) });
    state.rows = rows;
    state.opts = opts;

    register(canvasId, function (el, t) {
      var flat = flattenRows(rows, state.open, opts);
      state.flat = flat;

      // Grow the container so bars keep a usable thickness when expanded.
      var box = el.parentNode;
      if (box) {
        box.style.height = Math.max(CHART_MIN_PX, flat.length * ROW_PX + 56) + "px";
      }
      wireExpandClicks(el, canvasId);

      // Sub-source fill: blended toward the neutral ink, NOT toward the surface.
      // Mixing toward the surface only lightens the accent, leaving the same
      // vivid hue and reading as "another parent". Mixing toward grey drops the
      // chroma, so a child bar is obviously subordinate while staying in the
      // accent's colour family — and it keeps mid-tone luminance, so it stays
      // visible on the light surface AND the dark one.
      var subFill = mixToward(t.accent, t.ink3, .5);

      return new Chart(el, {
        type: "bar",
        data: {
          labels: flat.map(function (f) {
            // A caret marks what can be opened; an arrow marks a child row.
            if (f.isSub) return "   ↳ " + f.label;
            return (f.expandable ? (f.expanded ? "▾ " : "▸ ") : "  ") + f.label;
          }),
          datasets: [{
            label: opts.seriesLabel,
            data: flat.map(function (f) { return f.value; }),
            backgroundColor: flat.map(function (f) {
              return f.muted ? t.grid : (f.isSub ? subFill : t.accent);
            }),
            borderColor: flat.map(function (f) {
              return f.muted ? t.grid : t.accentStrong;
            }),
            borderWidth: 1,
            borderRadius: 4,
            borderSkipped: "start",
            barPercentage: .74,
            categoryPercentage: .86
          }]
        },
        options: {
          indexAxis: "y",
          maintainAspectRatio: false,
          layout: { padding: { right: 52, top: opts.baseline == null ? 6 : 14 } },
          plugins: {
            legend: { display: false },
            endLabels: {
              color: t.ink2,
              fmt: opts.money ? money : pctFmt,
              skip: flat.map(function (f) { return !!f.muted; })
            },
            tooltip: {
              filter: function (c) { return !flat[c.dataIndex].muted; },
              callbacks: {
                title: function (c) {
                  var f = flat[c[0].dataIndex];
                  return (f.isSub ? f.parent.channel + " › " : "") + f.label;
                },
                label: function (c) {
                  var f = flat[c.dataIndex];
                  var lines = opts.tooltip(f.row, f);
                  if (f.expandable) {
                    lines = lines.concat([
                      (f.expanded ? "click to collapse" : "click to expand") + " " +
                      f.row.subs.length + " sub-source" +
                      (f.row.subs.length === 1 ? "" : "s")
                    ]);
                  }
                  return lines;
                }
              }
            }
          },
          scales: {
            x: {
              // Wrapped, not passed by reference: Chart.js calls a tick
              // callback with (value, index, ticks), so money() read the tick
              // INDEX as its decimal-places argument — $10.0, $20.00, $30.000.
              ticks: {
                callback: opts.money ? function (v) { return money(v); } : pctFmt,
                color: t.ink3
              },
              grid: { color: t.grid, drawTicks: false },
              border: { display: false }
            },
            y: {
              grid: { display: false },
              border: { display: false },
              ticks: {
                color: function (ctx) {
                  var f = flat[ctx.index];
                  return (f && (f.isSub || f.muted)) ? t.ink3 : t.ink2;
                },
                font: function (ctx) {
                  var f = flat[ctx.index];
                  return {
                    size: f && f.isSub ? 10.5 : 11.5,
                    weight: (f && !f.isSub && f.expandable) ? "500" : "400"
                  };
                }
              }
            }
          }
        },
        plugins: opts.baseline == null
          ? [endLabels]
          : [endLabels, baselinePlugin(opts.baseline, opts.baselineLabel)]
      });
    });
  }

  /** Blend two hex colours — used for the lighter sub-source fill. */
  function mixToward(hex, towardHex, amount) {
    function parse(h) {
      var m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(String(h).trim());
      return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : null;
    }
    var a = parse(hex), b = parse(towardHex);
    if (!a || !b) return hex;
    return "rgb(" + a.map(function (v, i) {
      return Math.round(v + (b[i] - v) * amount);
    }).join(",") + ")";
  }

  /* ---- chart builders ------------------------------------------------- */

  /** Channel QUALITY — within-channel conversion, with a labelled baseline.
   * Rank is carried by sort order and position against the baseline, never by
   * hue, so one accent colour is correct here. Categories expand to sub-sources
   * on click. */
  function comparisonChart(rows, baseline, finalLabel) {
    expandableBars("cmpChart", rows, {
      seriesLabel: "conversion",
      value: function (r) { return r.rate; },
      baseline: baseline,
      baselineLabel: "baseline " + (baseline * 100).toFixed(1) + "%",
      tooltip: function (r) {
        return [
          (r.rate * 100).toFixed(1) + "% reached " + finalLabel,
          numFmt(r.final_n) + " of " + numFmt(r.n) + " people touched",
          "overall baseline " + (baseline * 100).toFixed(1) + "%"
        ];
      }
    });
  }

  /** Channel PRESENCE — share of the final stage, the mirror of the quality
   * chart. No baseline line: a share-of-stage baseline is 100% by definition, so
   * a reference line would say nothing. Categories expand on click. */
  function makeupChart(rows, finalTotal, finalLabel) {
    expandableBars("mkChart", rows, {
      seriesLabel: "share of " + finalLabel,
      value: function (r) { return r.share; },
      baseline: null,
      tooltip: function (r) {
        var out = [
          (r.share * 100).toFixed(1) + "% of " + finalLabel,
          numFmt(r.final_n) + " of " + numFmt(finalTotal) + " people",
          "out of " + numFmt(r.n) + " who touched this"
        ];
        if (r.is_no_utm) out.push("no UTM at all — untracked");
        return out;
      }
    });
  }

  /** Composition — first touch assigns each person exactly one channel, so this
   * is the one chart here whose shares genuinely total 100%. Percentages appear
   * in three places on purpose: the legend (exact, every slice), on the arcs
   * (at-a-glance, big slices only), and the denominator in the centre. Hover was
   * the only way to read them before, which made the chart decorative. */
  function mixChart(rows) {
    register("ftChart", function (el, t) {
      var total = rows.reduce(function (s, r) { return s + r.n; }, 0);
      var pct = function (n) { return total ? n / total : 0; };

      return new Chart(el, {
        type: "doughnut",
        data: {
          labels: rows.map(function (r) { return r.channel; }),
          datasets: [{
            data: rows.map(function (r) { return r.n; }),
            backgroundColor: rows.map(function (_, i) { return t.series[i % 8]; }),
            borderColor: t.surface,
            borderWidth: 2,
            hoverOffset: 12,
            hoverBorderColor: t.ink,
            hoverBorderWidth: 2
          }]
        },
        options: {
          maintainAspectRatio: false,
          cutout: "58%",
          layout: { padding: 4 },
          // intersect:false is the whole point here. Chart.js defaults a
          // doughnut to intersect:true, so the pointer must land exactly inside
          // the arc — impossible for a 0.3% sliver. Nearest-without-intersect
          // means sweeping the ring reaches every slice, however thin.
          interaction: { mode: "nearest", intersect: false },
          plugins: {
            arcLabels: { color: t.ink, halo: t.surface, min: .05 },
            donutCentre: {
              total: numFmt(total), caption: "people",
              color: t.ink, subColor: t.ink3
            },
            legend: {
              position: "right",
              labels: {
                color: t.ink2,
                boxWidth: 9,
                boxHeight: 9,
                padding: 7,
                generateLabels: function (chart) {
                  return rows.map(function (r, i) {
                    return {
                      // The figure lives in the legend too, so even a 0.1%
                      // sliver is readable.
                      text: r.channel + "  " + pctFmt(pct(r.n)),
                      fillStyle: t.series[i % 8],
                      strokeStyle: t.surface,
                      lineWidth: 1,
                      hidden: !chart.getDataVisibility(i),
                      index: i
                    };
                  });
                }
              },
              onClick: function (e, item, legend) {
                legend.chart.toggleDataVisibility(item.index);
                legend.chart.update();
              }
            },
            tooltip: {
              callbacks: {
                title: function (c) { return c[0].label; },
                label: function (c) {
                  return [
                    pctFmt(pct(c.parsed)) + " of first touches",
                    numFmt(c.parsed) + " of " + numFmt(total) + " people"
                  ];
                }
              }
            }
          }
        },
        plugins: [arcLabels, donutCentre]
      });
    });
  }

  /** Grouped magnitude across ordered stages, one series per channel. */
  function penetrationChart(payload) {
    if (!payload || !payload.series || !payload.series.length) return;
    register("penChart", function (el, t) {
      return new Chart(el, {
        type: "bar",
        data: {
          labels: payload.stages,
          datasets: payload.series.map(function (s) {
            return {
              label: s.name,
              data: s.values,
              counts: s.counts,
              // Colour follows the entity's canonical slot, not its position in
              // the current selection, so picking a sub-source cannot repaint
              // the parents already on screen.
              backgroundColor: t.series[s.colour_index % 8],
              borderRadius: 3,
              borderSkipped: "start",
              barPercentage: .9,
              categoryPercentage: .78
            };
          })
        },
        options: {
          maintainAspectRatio: false,
          layout: { padding: { top: 14 } },
          plugins: {
            legend: { position: "bottom", labels: { color: t.ink2 } },
            barValueLabels: { color: t.ink2, min: 0.02 },
            tooltip: {
              callbacks: {
                title: function (c) { return c[0].label; },
                label: function (c) {
                  var counts = c.dataset.counts || [];
                  var total = (payload.totals || [])[c.dataIndex];
                  var head = c.dataset.label + " — " + pctFmt(c.parsed.y) + " of stage";
                  if (counts[c.dataIndex] == null || total == null) return head;
                  return [head, numFmt(counts[c.dataIndex]) + " of " +
                          numFmt(total) + " people at this stage"];
                }
              }
            }
          },
          scales: {
            x: { grid: { display: false }, border: { display: false },
                 ticks: { color: t.ink2 } },
            y: {
              ticks: { callback: pctFmt, color: t.ink3 },
              grid: { color: t.grid, drawTicks: false },
              border: { display: false },
              title: { display: true, text: "share of stage touched (%)", color: t.ink3,
                       font: { size: 10.5 } }
            }
          }
        },
        plugins: [barValueLabels]
      });
    });
  }

  /* ------------------------------------------------------ sticky offset */
  /* The top bar wraps to two or three rows on narrower viewports, so a fixed
   * scroll-margin can't clear it. Publish the measured height as a custom
   * property and let CSS offset anchor jumps by it. */
  function wireStickyOffset() {
    var bar = document.querySelector(".topbar");
    if (!bar) return;

    function apply() {
      root.style.setProperty(
        "--sticky-h", Math.round(bar.getBoundingClientRect().height) + "px");
    }
    apply();
    if (window.ResizeObserver) {
      new ResizeObserver(apply).observe(bar);
    } else {
      window.addEventListener("resize", apply);
    }

    // The browser already jumped to the hash before the header existed, using
    // the wrong margin — redo the jump now that the offset is right.
    if (location.hash) {
      var target = null;
      try { target = document.querySelector(location.hash); } catch (e) {}
      if (target) {
        requestAnimationFrame(function () { target.scrollIntoView(); });
      }
    }
  }

  /* ---------------------------------------------------- scroll position */
  /* Filter state lives in the URL, so every control is a real navigation — which
   * normally dumps you back at the top of the page. Deep in the timeline
   * controls or a long table, that is the single most annoying thing the app
   * does. So remember the scroll offset per path and restore it.
   *
   * Keyed by pathname, so moving between tabs doesn't restore a foreign offset.
   * Skipped for back/forward (the browser already restores those correctly) and
   * when the URL carries a #hash (the anchor should win).
   */
  function wireScrollMemory() {
    var key = "aada.scroll." + location.pathname;

    function save() {
      try { sessionStorage.setItem(key, String(Math.round(window.scrollY))); } catch (e) {}
    }
    window.addEventListener("pagehide", save);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") save();
    });
    // Anchor for clicks too: pagehide can be unreliable on some navigations.
    document.addEventListener("click", function (ev) {
      var a = ev.target.closest && ev.target.closest("a[href]");
      if (a && !a.target && a.origin === location.origin) save();
    }, true);
    document.addEventListener("submit", save, true);

    if (location.hash) return;
    var navType = "navigate";
    try {
      var entry = performance.getEntriesByType("navigation")[0];
      if (entry && entry.type) navType = entry.type;
    } catch (e) {}
    if (navType === "back_forward") return;

    var saved = null;
    try { saved = sessionStorage.getItem(key); } catch (e) {}
    var y = parseInt(saved, 10);
    if (!y || y < 0) return;

    // Charts and webfonts change the page height after first paint, so settle
    // onto the offset over a couple of frames rather than guessing once.
    var tries = 0;
    (function settle() {
      window.scrollTo(0, y);
      if (++tries < 4 && Math.abs(window.scrollY - y) > 2) {
        setTimeout(settle, 90);
      }
    })();
  }

  /* ------------------------------------------------- collapsible sections */
  /* <details> does the collapsing on its own; this only remembers the choice so
   * a section you closed stays closed across pages and reloads. */
  function wireFolds() {
    // Links and tips inside a <summary> must not toggle the section: a tip
    // would close the thing you are reading about, and a tree row's checkbox
    // would collapse the parent you just ticked.
    document.addEventListener("click", function (ev) {
      if (!ev.target.closest) return;
      // Picker links must still reach the picker root, which is an ANCESTOR of
      // the summary they sit in — so suppress the <details> toggle but do NOT
      // swallow the event. Stopping propagation here is what made a ticked
      // parent category navigate instead of redrawing in place.
      var pick = ev.target.closest("[data-pick], [data-act]");
      var pkr = pick && pick.closest("[data-picker]");
      if (pkr && PICKERS[pkr.getAttribute("data-picker")]) {
        ev.preventDefault();
        return;
      }
      if (ev.target.closest("summary .tip, summary a")) {
        // Let a real link navigate; just stop it toggling the <details>.
        if (!ev.target.closest("a[href]")) ev.preventDefault();
        ev.stopPropagation();
      }
    }, true);

    // Any <details> carrying data-fold, not just .fold — the series picker
    // uses .stree-fold, and matching on the class meant its open state was
    // never saved, so it collapsed on every selection.
    document.querySelectorAll("details[data-fold]").forEach(function (d) {
      var key = "aada.fold." + d.dataset.fold;
      try {
        var saved = localStorage.getItem(key);
        if (saved === "0") d.open = false;
        else if (saved === "1") d.open = true;
      } catch (e) {}
      d.addEventListener("toggle", function () {
        try { localStorage.setItem(key, d.open ? "1" : "0"); } catch (e) {}
      });
    });
  }

  /** Tag timeline — one line per category x fiscal year.
   *
   * Two encodings at once: COLOUR carries the category (so a channel keeps the
   * same colour it has in every table and chart), and LINE STYLE carries the
   * year — newest solid and full-strength, earlier years dashed, thinner and
   * dimmed. That is what "previous years greyed out" means here without giving
   * up the category identity.
   *
   * The x axis is a position inside the fiscal year (1 Sep → 31 Aug), not an
   * absolute date, which is what lets several years sit on one axis.
   */
  function timelineChart(payload, started) {
    register("tlChart", function (el, t) {
      var bucketWord = { month: "month", week: "week", day: "day" }[payload.bucket];
      var unit = payload.measure === "people" ? "people" : "tags";

      var datasets = payload.series.map(function (s) {
        var colour = t.series[s.colour_index % 8];
        return {
          label: s.label,
          data: s.data,
          borderColor: colour,
          backgroundColor: colour,
          borderWidth: s.current ? 2 : 1.25,
          borderDash: s.current ? [] : [5, 4],
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBorderWidth: 2,
          pointHoverBorderColor: t.surface,
          pointHitRadius: 14,
          tension: .28,
          // Past years recede without losing their category colour.
          opacity: s.current ? 1 : .45,
          spanGaps: false,
          _fy: s.fy_label,
          _group: s.group,
          _current: s.current
        };
      });

      // Chart.js has no dataset-level opacity, so dim the past years by
      // applying alpha to their stroke directly.
      datasets.forEach(function (d) {
        if (d.opacity === 1) return;
        d.borderColor = dim(d.borderColor, d.opacity);
        d.backgroundColor = d.borderColor;
      });

      // Reference band: applications started. Grey, filled, on its own
      // right-hand axis, and drawn BEHIND every category line — it is context
      // for the tag lines, never a competitor for attention. Its axis is
      // independent, which is what makes it legible (tags outrun starts ~6:1)
      // and also why only its SHAPE is comparable, not its height.
      var bands = [];
      if (started && started.series && started.series.length) {
        started.series.forEach(function (s) {
          bands.push({
            label: "Started apps · " + s.fy_label,
            data: s.data,
            yAxisID: "y1",
            borderColor: dim(t.ink3, s.current ? .85 : .4),
            backgroundColor: dim(t.ink3, s.current ? .13 : .06),
            borderWidth: s.current ? 1.5 : 1,
            borderDash: s.current ? [] : [5, 4],
            fill: "origin",
            pointRadius: 0,
            pointHoverRadius: 4,
            pointHitRadius: 10,
            tension: .28,
            order: 99,              // higher order = drawn first = behind
            _fy: s.fy_label,
            _group: "Applications started",
            _current: s.current,
            _isBand: true
          });
        });
        datasets.forEach(function (d) { d.order = 1; });
        datasets = bands.concat(datasets);
      }

      return new Chart(el, {
        type: "line",
        data: { labels: payload.labels, datasets: datasets },
        options: {
          maintainAspectRatio: false,
          // axis "xy", not "x": with a dozen overlapping lines, nearest-on-x
          // repeatedly returned a different series than the one under the
          // cursor. 2D nearest gives you the line you are actually pointing at.
          interaction: { mode: "nearest", axis: "xy", intersect: false },
          plugins: {
            crosshair: { color: t.axis },
            legend: {
              position: "bottom",
              labels: {
                color: t.ink2, boxWidth: 10, boxHeight: 2, padding: 9,
                usePointStyle: false,
                generateLabels: function (chart) {
                  return chart.data.datasets.map(function (d, i) {
                    return {
                      text: d.label + (d._current ? "" : "  (past)"),
                      fillStyle: d.borderColor,
                      strokeStyle: d.borderColor,
                      lineWidth: 2,
                      lineDash: d.borderDash,
                      fontColor: d._current ? t.ink2 : t.ink3,
                      hidden: !chart.isDatasetVisible(i),
                      datasetIndex: i
                    };
                  });
                }
              }
            },
            tooltip: {
              callbacks: {
                title: function (c) {
                  var d = c[0].dataset;
                  // Lead with the category — it is the thing you were pointing at.
                  return d._group;
                },
                label: function (c) {
                  var d = c.dataset;
                  if (d._isBand) {
                    return [
                      numFmt(c.parsed.y) + " applications started",
                      c.label + " · " + d._fy + (d._current ? " (current)" : " (past year)"),
                      "right axis — scaled separately from tags"
                    ];
                  }
                  var lines = [
                    numFmt(c.parsed.y) + " " + unit,
                    c.label + " · " + d._fy + (d._current ? " (current)" : " (past year)")
                  ];
                  // Share of everything in this bucket, across the drawn lines.
                  var stack = 0;
                  c.chart.data.datasets.forEach(function (ds, i) {
                    if (!ds._isBand && ds._fy === d._fy && c.chart.isDatasetVisible(i)) {
                      stack += ds.data[c.dataIndex] || 0;
                    }
                  });
                  if (stack > 0) {
                    lines.push(pctFmt(c.parsed.y / stack) + " of that " +
                      bucketWord + "'s tags");
                  }
                  return lines;
                }
              }
            }
          },
          scales: {
            x: {
              grid: { display: false },
              border: { color: t.axis },
              ticks: {
                color: t.ink3, maxRotation: 0, autoSkip: true,
                maxTicksLimit: payload.bucket === "month" ? 12 : 14
              },
              title: {
                display: true, color: t.ink3, font: { size: 10.5 },
                text: "fiscal year · 1 Sep → 31 Aug"
              }
            },
            y: {
              beginAtZero: true,
              grid: { color: t.grid, drawTicks: false },
              border: { display: false },
              ticks: { color: t.ink3, precision: 0 },
              title: {
                display: true, color: t.ink3, font: { size: 10.5 },
                text: unit + " per " + bucketWord
              }
            },
            // Second scale, deliberately styled to look like a different scale:
            // grey to match the band, no gridlines of its own (they would imply
            // the left axis shares them), and an explicit title.
            y1: {
              display: !!(started && started.series && started.series.length),
              position: "right",
              beginAtZero: true,
              grid: { drawOnChartArea: false },
              border: { display: false },
              ticks: { color: t.ink3, precision: 0 },
              title: {
                display: true, color: t.ink3, font: { size: 10.5 },
                text: "applications started per " + bucketWord + "  (right axis)"
              }
            }
          }
        },
        plugins: [crosshair]
      });
    });
  }

  /** Blend a hex colour toward transparency for the dimmed past-year lines. */
  function dim(hex, alpha) {
    var m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(String(hex).trim());
    if (!m) return hex;
    return "rgba(" + parseInt(m[1], 16) + "," + parseInt(m[2], 16) + "," +
      parseInt(m[3], 16) + "," + alpha + ")";
  }

  /** Monthly media spend, stacked by channel. Stacked is right here and wrong
   *  for the funnel charts above: spend IS additive — two channels' dollars are
   *  different dollars — whereas any-touch people are not. */
  function spendChart(payload) {
    register("spendChart", function (el, t) {
      return new Chart(el, {
        type: "bar",
        data: {
          labels: payload.labels,
          datasets: payload.series.map(function (s) {
            return {
              label: s.name,
              data: s.data,
              backgroundColor: t.series[s.colour_index % 8],
              borderRadius: 3,
              borderSkipped: "bottom",
              barPercentage: .78,
              categoryPercentage: .82
            };
          })
        },
        options: {
          maintainAspectRatio: false,
          layout: { padding: { top: 12 } },
          plugins: {
            legend: { position: "bottom", labels: { color: t.ink2 } },
            tooltip: {
              callbacks: {
                label: function (c) {
                  return c.dataset.label + ": " + money(c.parsed.y);
                },
                footer: function (items) {
                  var sum = items.reduce(function (a, i) { return a + i.parsed.y; }, 0);
                  return "total " + money(sum);
                }
              }
            }
          },
          scales: {
            x: { stacked: true, grid: { display: false }, ticks: { color: t.ink3 } },
            y: {
              stacked: true, beginAtZero: true,
              grid: { color: t.grid }, border: { display: false },
              ticks: { color: t.ink3, callback: function (v) { return money(v); } },
              title: { display: true, text: "spend per month", color: t.ink3 }
            }
          }
        }
      });
    });
  }

  function money(v, dp) {
    if (v == null) return "—";
    return "$" + v.toLocaleString(undefined, {
      minimumFractionDigits: dp || 0, maximumFractionDigits: dp || 0 });
  }

  /* ---------------------------------------------- instant series pickers
   * Every entity ships with the page, so ticking a box re-slices and redraws
   * locally instead of costing a ~0.45s navigation. The URL is kept in sync
   * with replaceState, so links stay shareable and a refresh restores the view.
   *
   * The slice rule is deliberately trivial here — take the first `limit` of the
   * ticked set in the server's canonical `rank` order. Everything that decides
   * WHICH colour a series gets stays in Python, under the test that stops two
   * channels sharing a hue; this side only slices.
   */
  var PICKERS = {};

  function registerPicker(key, payload, redraw) {
    var root = document.querySelector('[data-picker="' + key + '"]');
    if (!root) return;
    var p = PICKERS[key] = {
      key: key, root: root, payload: payload, redraw: redraw,
      selected: (payload.selected || []).slice()
    };
    root.addEventListener("click", function (ev) {
      var act = ev.target.closest("[data-act]");
      var row = ev.target.closest("[data-pick]");
      if (!act && !row) return;
      ev.preventDefault();
      ev.stopPropagation();
      var names = payload.entities.map(function (e) { return e.name; });
      if (act) {
        var a = act.getAttribute("data-act");
        if (a === "every") p.selected = names.slice();
        else if (a === "none") p.selected = [];
        else if (a === "default") p.selected = (payload.defaults || []).slice();
      } else if (ev.target.closest("[data-only]")) {
        p.selected = [row.getAttribute("data-pick")];
      } else {
        var n = row.getAttribute("data-pick");
        var i = p.selected.indexOf(n);
        if (i === -1) p.selected.push(n); else p.selected.splice(i, 1);
      }
      applyPicker(p);
    });
  }

  /** Mirrors metrics.slice_selection, then metrics' colour assignment.
   *  Which eight survive is decided by `rank` (volume — drop the smallest);
   *  the colour each survivor gets is decided by `torder` (taxonomy) across
   *  the kept set. Both orderings come from the server; this only sorts. */
  function slicePicked(payload, selected) {
    var chosen = payload.entities.filter(function (e) {
      return selected.indexOf(e.name) !== -1;
    }).sort(function (a, b) { return a.rank - b.rank; });
    var lim = payload.limit || 8;
    return {
      kept: chosen.slice(0, lim).sort(function (a, b) { return a.torder - b.torder; }),
      dropped: chosen.slice(lim).map(function (e) { return e.name; })
    };
  }

  function applyPicker(p) {
    var sliced = slicePicked(p.payload, p.selected);
    p.redraw(sliced.kept, p.selected, sliced.dropped);
    paintPicker(p, sliced.dropped);
    syncPickerUrl(p);
  }

  function paintPicker(p, dropped) {
    p.root.querySelectorAll("[data-row]").forEach(function (row) {
      var on = p.selected.indexOf(row.getAttribute("data-row")) !== -1;
      row.classList.toggle("on", on);
    });
    var sum = p.root.querySelector(".stree-sumtxt");
    if (sum) {
      // Preview names in rank order, not click order, so the summary line does
      // not reshuffle as you tick boxes.
      var inRank = p.payload.entities
        .filter(function (e) { return p.selected.indexOf(e.name) !== -1; })
        .map(function (e) { return e.name; });
      var n = inRank.length, total = p.payload.entities.length;
      sum.innerHTML = n === 0
        ? "<b>none shown</b> — pick something"
        : "<b>" + n + "</b> of " + total + " shown · " +
          inRank.slice(0, 2).join(", ") + (n > 2 ? " +" + (n - 2) : "");
    }
    var warn = p.root.querySelector("[data-dropped]");
    if (warn) {
      warn.hidden = !dropped.length;
      warn.textContent = dropped.length
        ? dropped.length + " selected but not drawn (" + dropped.join(", ") +
          "). The palette has eight slots and a repeated colour would read as " +
          "the same series."
        : "";
    }
  }

  function syncPickerUrl(p) {
    if (!window.history || !history.replaceState) return;
    var u = new URLSearchParams(location.search);
    u.delete(p.key);
    if (!p.selected.length) {
      u.set(p.key, "__clear__");
    } else {
      p.payload.entities.forEach(function (e) {
        if (p.selected.indexOf(e.name) !== -1) u.append(p.key, e.name);
      });
    }
    history.replaceState(null, "", location.pathname + "?" + u.toString() + location.hash);
  }

  /* --------------------------------------------- rebuilding the two charts
   * These mirror the Python that produced the first paint. Colour index is the
   * position within the KEPT set, exactly as metrics.py assigns it, so a series
   * keeps its hue for a given selection no matter which side did the drawing.
   */
  function buildTimeline(payload, kept) {
    var series = [];
    kept.forEach(function (e, i) {
      e.lines.forEach(function (l) {
        series.push({
          group: e.name, fy: l.fy, fy_label: l.fy_label, current: l.current,
          data: l.data, total: l.total, colour_index: i % 8,
          label: e.name + " · " + l.fy_label
        });
      });
    });
    return {
      labels: payload.labels, series: series, bucket: payload.bucket,
      measure: payload.measure, years: payload.years, newest: payload.newest
    };
  }

  function buildPenetration(payload, kept) {
    return {
      stages: payload.stages, totals: payload.totals,
      series: kept.map(function (e, i) {
        return {
          name: e.name, values: e.values, counts: e.counts,
          colour_index: i % 8
        };
      })
    };
  }

  function wirePickers(tlPayload, tlStarted, penPayload) {
    if (tlPayload) {
      tlPayload.defaults = (tlPayload.entities || [])
        .filter(function (e) { return e.name.indexOf(" › ") === -1; })
        .map(function (e) { return e.name; });
      registerPicker("tl_pick", tlPayload, function (kept) {
        destroy("tlChart");
        var empty = document.querySelector('[data-empty="tl_pick"]');
        if (empty) empty.hidden = kept.length > 0;
        if (kept.length) timelineChart(buildTimeline(tlPayload, kept), tlStarted);
      });
    }
    if (penPayload) {
      penPayload.defaults = (penPayload.entities || [])
        .filter(function (e) { return e.name.indexOf(" › ") === -1; })
        .map(function (e) { return e.name; });
      registerPicker("pen_pick", penPayload, function (kept) {
        destroy("penChart");
        var empty = document.querySelector('[data-empty="pen_pick"]');
        if (empty) empty.hidden = kept.length > 0;
        if (kept.length) penetrationChart(buildPenetration(penPayload, kept));
      });
    }
  }

  /* ------------------------------------------ instant "measured against"
   * Every stage of both channel charts ships with the page, so switching the
   * target stage redraws locally rather than reloading. Each control drives
   * ONLY its own card — that is the whole point of there being two of them —
   * so the payload is looked up per card and nothing is shared but the data.
   *
   * expandableBars() keys its expansion state by canvas id, so re-calling the
   * chart wrapper with a different stage's rows keeps whichever categories the
   * user had opened.
   */
  var STAGE_CARDS = {
    cvs: {
      canvas: "cmpChart",
      draw: function (st) { comparisonChart(st.cmp.rows, st.cmp.baseline, st.label); }
    },
    mvs: {
      canvas: "mkChart",
      draw: function (st) { makeupChart(st.mk.rows, st.mk.total, st.label); }
    }
  };

  function wireStagePicks(payload, current, defaultStage) {
    if (!payload) return;
    current = Object.assign({}, current);
    document.querySelectorAll("[data-stagepick]").forEach(function (bar) {
      var param = bar.getAttribute("data-stagepick");
      var card = STAGE_CARDS[param];
      if (!card) return;
      bar.addEventListener("click", function (ev) {
        var a = ev.target.closest("a[data-stage]");
        if (!a) return;
        var key = a.getAttribute("data-stage");
        var st = payload[key];
        if (!st) return;                 // unknown stage: let the link navigate
        ev.preventDefault();
        if (key === current[param]) return;
        current[param] = key;
        card.draw(st);
        bar.querySelectorAll("a[data-stage]").forEach(function (n) {
          n.classList.toggle("on", n.getAttribute("data-stage") === key);
        });
        paintStageText(param, st);
        syncStageUrl(param, key, defaultStage);
      });
    });
  }

  function paintStageText(param, st) {
    document.querySelectorAll('[data-stagetext="' + param + '"]').forEach(function (n) {
      n.textContent = st.label;
    });
    document.querySelectorAll('[data-stagetotal="' + param + '"]').forEach(function (n) {
      n.textContent = numFmt(st.mk.total);
    });
    document.querySelectorAll('[data-stagesum="' + param + '"]').forEach(function (n) {
      n.textContent = (st.mk.sum_share * 100).toFixed(1) + "%";
    });
  }

  function syncStageUrl(param, key, defaultStage) {
    if (!window.history || !history.replaceState) return;
    var u = new URLSearchParams(location.search);
    // Selecting the default drops the param, matching the server-built hrefs
    // so a copied URL is the same one a no-JS click would have produced.
    //
    // The default is passed in, NOT inferred from the payload's key order:
    // Jinja's |tojson sorts dict keys, so "the last key" is alphabetical
    // ("submitted"), not the funnel's last stage. Reading it that way inverted
    // the whole rule — every non-default choice dropped its param and the
    // default wrote one.
    if (key === defaultStage) u.delete(param); else u.set(param, key);
    var qs = u.toString();
    history.replaceState(null, "", location.pathname + (qs ? "?" + qs : "") + location.hash);
  }

  /* ------------------------------------------------------- cost, client-side
   * Every stage's cost table ships with the page, so the "Cost per" chips
   * redraw locally. Same trade the series pickers make: a few KB of payload
   * against a ~0.45s reload that also loses your scroll position.
   */
  var COST = { payload: null, stage: null, attr: "first" };

  /** Overview card: cost per stage by channel, expanding to sub-sources. */
  function costChart(payload, stage, attr) {
    COST.payload = payload;
    COST.stage = stage;
    COST.attr = attr || "first";
    drawCost();
    wireCostChips();
    wireAttrChips(drawCost);
  }

  function costStage() {
    var byAttr = COST.payload && COST.payload.by_attr;
    return byAttr && byAttr[COST.attr] ? byAttr[COST.attr][COST.stage] : null;
  }

  /** The first/any-touch toggle. Shared by the Overview card and the Cost tab,
   *  which is why it takes the redraw as an argument rather than assuming a
   *  chart. Which lens you are in decides whether the rows may be added up, so
   *  the note beside it is part of the control, not decoration. */
  function wireAttrChips(redraw) {
    document.querySelectorAll("[data-attrpick]").forEach(function (bar) {
      bar.addEventListener("click", function (ev) {
        var a = ev.target.closest("a[data-attr]");
        if (!a || !COST.payload) return;
        var key = a.getAttribute("data-attr");
        if (!COST.payload.by_attr[key]) return;
        ev.preventDefault();
        if (key === COST.attr) return;
        COST.attr = key;
        redraw();
        bar.querySelectorAll("a[data-attr]").forEach(function (n) {
          n.classList.toggle("on", n.getAttribute("data-attr") === key);
        });
        var note = bar.querySelector("[data-attrnote]");
        var spec = (COST.payload.attributions || []).filter(function (x) {
          return x.key === key;
        })[0];
        if (note && spec) note.textContent = spec.note;
        syncStageUrl("ca", key, "first");
      });
    });
  }

  function drawCost() {
    var st = costStage();
    if (!st) return;
    expandableBars("costChart", st.rows.map(function (r) {
      return {
        channel: r.name, rate: r.per || 0, n: r.n, cost: r.cost,
        subs: r.subs.map(function (s) {
          return { channel: s.name, rate: s.per || 0, n: s.n, cost: s.cost };
        }),
        hidden_subs: 0
      };
    }), {
      seriesLabel: "cost per " + st.label.toLowerCase(),
      value: function (r) { return r.rate; },
      baseline: st.per || null,
      baselineLabel: "blended " + money(st.per),
      money: true,
      tooltip: function (r) {
        return [
          money(r.rate) + " per " + st.label.toLowerCase(),
          money(r.cost) + " spent",
          numFmt(r.n) + " reached " + st.label +
            (st.rows_sum ? " (first touch)" : " (any touch)")
        ];
      }
    });
    paintCostText(st);
  }

  function paintCostText(st) {
    document.querySelectorAll("[data-coststagelow]").forEach(function (n) {
      n.textContent = st.label.toLowerCase();
    });
    document.querySelectorAll("[data-coststage]").forEach(function (n) {
      n.textContent = st.label + (st.rows_sum ? " (1st touch)" : " (any touch)");
    });
    var per = document.querySelector("[data-costper]");
    if (per) per.textContent = st.per ? money(st.per) : "—";
    var cnt = document.querySelector("[data-costn]");
    if (cnt) cnt.innerHTML = numFmt(st.total_n) + " <small>from paid</small>";
    var reach = document.querySelector("[data-costreach]");
    if (reach) reach.textContent = st.rows_sum ? "Reached it" : "Reached it (de-duped)";
  }

  function wireCostChips() {
    document.querySelectorAll("[data-costcard]").forEach(function (bar) {
      bar.addEventListener("click", function (ev) {
        var a = ev.target.closest("a[data-stage]");
        if (!a || !COST.payload) return;
        var key = a.getAttribute("data-stage");
        // Guard against the CURRENT lens, not the old flat payload: leaving
        // this on by_stage made every chip fall through to a page navigation.
        if (!COST.payload.by_attr[COST.attr][key]) return;
        ev.preventDefault();
        if (key === COST.stage) return;
        COST.stage = key;
        drawCost();
        bar.querySelectorAll("a[data-stage]").forEach(function (n) {
          n.classList.toggle("on", n.getAttribute("data-stage") === key);
        });
        syncStageUrl("cst", key, "started");
      });
    });
  }

  /** Cost tab: the same swap, redrawing a table rather than a chart. */
  function wireCostTable(payload, stage, defaultStage, attr) {
    var table = document.querySelector("[data-costtable]");
    if (!table || !payload) return;
    COST.payload = payload;
    COST.stage = stage;
    COST.attr = attr || "first";
    wireAttrChips(function () { redrawCostTable(table, costStage()); });
    document.querySelectorAll("[data-costpick]").forEach(function (bar) {
      bar.addEventListener("click", function (ev) {
        var a = ev.target.closest("a[data-stage]");
        if (!a) return;
        var key = a.getAttribute("data-stage");
        if (!payload.by_attr[COST.attr][key]) return;
        ev.preventDefault();
        if (key === COST.stage) return;
        COST.stage = key;
        redrawCostTable(table, costStage());
        bar.querySelectorAll("a[data-stage]").forEach(function (n) {
          n.classList.toggle("on", n.getAttribute("data-stage") === key);
        });
        syncStageUrl("vs", key, defaultStage);
      });
    });
  }

  function redrawCostTable(table, st) {
    // A sub-source name is only unique within its parent ("Search" could sit
    // under more than one channel), so the lookup key is the pair.
    function key(parent, sub) { return parent + "::" + sub; }
    var byName = {};
    st.rows.forEach(function (r) {
      byName[r.name] = r;
      r.subs.forEach(function (s) { byName[key(r.name, s.name)] = s; });
    });
    // Only the two stage columns move. Spend and the first-touch start columns
    // are the same whichever stage you are asking about.
    var parent = null;
    table.querySelectorAll("tbody tr").forEach(function (tr) {
      var name = tr.querySelector("td.chan").textContent.trim();
      var rec;
      if (tr.classList.contains("sub")) {
        rec = byName[key(parent, name)];
      } else {
        parent = name;
        rec = byName[name];
      }
      if (!rec) return;
      var tds = tr.children;
      tds[3].textContent = numFmt(rec.start_n);
      tds[4].innerHTML = rec.start_per ? money(rec.start_per, 2)
                                       : '<span class="dash">-</span>';
      tds[7].textContent = numFmt(rec.n);
      tds[8].innerHTML = rec.per ? money(rec.per, 2)
                                 : '<span class="dash">—</span>';
    });
    var set = function (sel, v) {
      var n = table.querySelector(sel);
      if (n) n.textContent = v;
    };
    set("[data-foot-start]", numFmt(st.start_total));
    set("[data-foot-startper]", st.start_per ? money(st.start_per, 2) : "—");
    set("[data-foot-n]", numFmt(st.total_n));
    set("[data-foot-per]", st.per ? money(st.per, 2) : "—");
    var lens = st.rows_sum ? " (1st touch)" : " (any touch)";
    var sc = table.querySelector("[data-startcol]");
    if (sc) sc.textContent = "Started" + lens;
    var ov = document.querySelector("[data-overlapnote]");
    if (ov) ov.hidden = !!st.rows_sum;
    paintCostText(st);
    var warn = document.querySelector("[data-costwarn]");
    if (warn) warn.hidden = COST.stage !== "enrolled";
  }

  /** Spend trend: pick channels and sub-sources, redraw without reloading. */
  function wireSpendPicker(payload) {
    if (!payload) return;
    registerPicker("sp_pick", payload, function (kept) {
      destroy("spendChart");
      var empty = document.querySelector('[data-empty="sp_pick"]');
      if (empty) empty.hidden = kept.length > 0;
      if (!kept.length) return;
      spendChart({
        labels: payload.labels,
        series: kept.map(function (e, i) {
          return { name: e.name, data: e.data, colour_index: i % 8,
                   is_sub: e.is_sub };
        })
      });
    });
  }

  /* ------------------------------------------------------------------ wire up */
  function init() {
    syncThemeButton();
    syncSwatches();   // reflect the accent stamped by the inline <head> script
    wireStickyOffset();
    wireFolds();
    wireScrollMemory();

    var toggle = document.getElementById("themeToggle");
    if (toggle) {
      toggle.addEventListener("click", function () {
        setTheme(currentTheme() === "dark" ? "light" : "dark");
      });
    }
    document.querySelectorAll(".swatch").forEach(function (b) {
      b.addEventListener("click", function () { setAccent(b.dataset.accent); });
    });

    rebuildCharts();
  }

  window.AADA = {
    spendChart: spendChart,
    costChart: costChart,
    wireCostTable: wireCostTable,
    wireSpendPicker: wireSpendPicker,
    wirePickers: wirePickers,
    wireStagePicks: wireStagePicks,
    comparisonChart: comparisonChart,
    makeupChart: makeupChart,
    mixChart: mixChart,
    penetrationChart: penetrationChart,
    timelineChart: timelineChart,
    init: init
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
