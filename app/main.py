"""FastAPI app: upload Slate exports, report the funnel by channel.

Server-rendered (Jinja2) with Chart.js vendored locally -- no build step and no
external network calls, so applicant data never leaves the machine.
"""
import os
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth, db, filters, ingest, metrics, programs, spend, taxonomy

# Refuses to import (and therefore to boot) on a half-configured credential pair.
# See app/auth.py -- this is what makes a typo'd env var a startup failure
# instead of a silent "app is open to anyone" hole once it's hosted.
auth.check_config()

HERE = os.path.dirname(os.path.abspath(__file__))
# The login gate applies here, once, to every future @app.get/@app.post route --
# not per-route. It deliberately does NOT reach the app.mount() below: mounted
# ASGI apps sit outside FastAPI's dependency injection, which is what keeps
# static assets (no applicant data) reachable without a login while every
# templated route and the upload endpoint require one.
app = FastAPI(title="AADA Funnel", dependencies=[Depends(auth.require_login)])
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))

DB_PATH = os.environ.get("AADA_DB", db.DEFAULT_DB)


def get_conn():
    return db.connect(DB_PATH)


# ---- template helpers ------------------------------------------------------
def _pct(v, digits=1):
    if v is None:
        return "--"
    return ("%." + str(digits) + "f%%") % (v * 100)


def _num(v):
    return "{:,}".format(v) if isinstance(v, int) else v


def _qs(flt, **over):
    """Current filter state as a query string, with overrides applied."""
    from urllib.parse import urlencode
    d = flt.query_dict()
    for k, v in over.items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    return urlencode(d, doseq=True)


def _current_params(request):
    """Current query string as {key: [values]}, blanks dropped."""
    q = request.query_params
    getall = getattr(q, "getlist", None) or (lambda k: q.getall(k))
    out = {}
    for key in q.keys():
        vals = [v for v in getall(key) if v != ""]
        if vals:
            out[key] = vals
    return out


def _apply_over(d, over):
    for k, v in over.items():
        if v is None:
            d.pop(k, None)
        elif isinstance(v, (list, tuple, set)):
            vals = [str(x) for x in v]
            if vals:
                d[k] = vals
            else:
                d.pop(k, None)
        else:
            d[k] = [str(v)]
    return d


def _qs_with(request, **over):
    """Full current query string with overrides applied.

    Used by controls that stay on the same page (the timeline's toggles), so
    they preserve the filter bar, the program, and each other rather than
    resetting everything they don't know about.
    """
    from urllib.parse import urlencode
    return urlencode(_apply_over(_current_params(request), over), doseq=True)


def _qs_set(request, key, value):
    """_qs_with for a key held in a variable — a macro reused by two controls
    cannot name its param at the call site. `None` drops the key entirely, so
    selecting a control's default leaves a clean URL.
    """
    return _qs_with(request, **{key: value})


def _qs_toggle(request, key, value):
    """Query string with `value` added to / removed from the multi-param `key`.

    Lets the timeline's category and year pickers be plain links — no JS.
    """
    from urllib.parse import urlencode
    d = _current_params(request)
    value = str(value)
    vals = list(d.get(key, []))
    if value in vals:
        vals.remove(value)
    else:
        vals.append(value)
    if vals:
        d[key] = vals
    else:
        d.pop(key, None)
    return urlencode(d, doseq=True)


# Explicit "nothing selected", distinct from "param absent" which means
# everything. Without it, clearing every option and defaulting to all would be
# the same URL, so "Clear all" could not exist.
PICK_NONE = "__clear__"


def _picked(request, key, values, all_values):
    """Which of `all_values` are currently selected."""
    cur = _current_params(request).get(key)
    if cur is None:
        return list(all_values)          # absent == everything
    if cur == [PICK_NONE]:
        return []
    return [v for v in all_values if str(v) in cur]


def _is_picked(request, key, value, all_values):
    return str(value) in [str(v) for v in _picked(request, key, None, all_values)]


def _qs_pick(request, key, value, all_values, current=None):
    """Toggle one option, starting from what is ACTUALLY selected.

    `current` matters: the series pickers default to parents-only, not to every
    entity, so deriving the baseline from "param absent == all" would make the
    first checkbox click switch on every sub-source at once — the picker and the
    chart would disagree about what the default is.

    The result is always written out explicitly rather than collapsing a
    full selection back to an absent param, because for these pickers "absent"
    means the default, not "everything".
    """
    from urllib.parse import urlencode
    d = _current_params(request)
    all_values = [str(v) for v in all_values]
    value = str(value)
    if current is None:
        current = _picked(request, key, None, all_values)
    chosen = [str(v) for v in current]
    if value in chosen:
        chosen.remove(value)
    else:
        chosen.append(value)

    if not chosen:
        d[key] = [PICK_NONE]
    else:
        d[key] = [v for v in all_values if v in chosen]   # keep canonical order
    return urlencode(d, doseq=True)


def _qs_pick_every(request, key, all_values):
    """Select every option explicitly — distinct from clearing the param, which
    means "the default" (parents only) for the series pickers."""
    from urllib.parse import urlencode
    d = _current_params(request)
    d[key] = [str(v) for v in all_values]
    return urlencode(d, doseq=True)


def _qs_only(request, key, value):
    """Select exactly this one option — the "only" affordance.

    Isolating a series used to mean Clear all, then hunt for it in a wall of
    chips. One click instead.
    """
    from urllib.parse import urlencode
    d = _current_params(request)
    d[key] = [str(value)]
    return urlencode(d, doseq=True)


def _qs_pick_all(request, key):
    from urllib.parse import urlencode
    d = _current_params(request)
    d.pop(key, None)
    return urlencode(d, doseq=True)


def _qs_pick_none(request, key):
    from urllib.parse import urlencode
    d = _current_params(request)
    d[key] = [PICK_NONE]
    return urlencode(d, doseq=True)


def _qs_drop(request, dimension, **keep):
    """Query string with one whole filter dimension removed.

    Powers the ✕ on each token as a real link, so clearing a filter works with
    JavaScript disabled — only the add/edit picker needs JS. Built from the live
    query string so unrelated state (timeline options, UTM field) survives.
    """
    from urllib.parse import urlencode
    d = _current_params(request)
    for param in filters.DIMENSION_PARAMS.get(dimension, [dimension]):
        d.pop(param, None)
    return urlencode(_apply_over(d, keep), doseq=True)


def _asset(path):
    """/static URL with an mtime cache-buster.

    Without this the browser happily serves a stale app.js/filters.js after an
    edit, which is a genuinely confusing way to lose an afternoon.
    """
    rel = path.lstrip("/")
    try:
        version = int(os.path.getmtime(os.path.join(HERE, "static", rel)))
    except OSError:
        version = 0
    return "/static/%s?v=%d" % (rel, version)


templates.env.filters["pct"] = _pct
templates.env.filters["num"] = _num
templates.env.globals["qs"] = _qs
templates.env.globals["qs_drop"] = _qs_drop
templates.env.globals["qs_with"] = _qs_with
templates.env.globals["qs_set"] = _qs_set
templates.env.globals["qs_toggle"] = _qs_toggle
templates.env.globals["qs_pick"] = _qs_pick
templates.env.globals["qs_only"] = _qs_only
templates.env.globals["qs_pick_all"] = _qs_pick_all
templates.env.globals["qs_pick_every"] = _qs_pick_every
templates.env.globals["qs_pick_none"] = _qs_pick_none
templates.env.globals["is_picked"] = _is_picked
templates.env.globals["asset"] = _asset
templates.env.globals["NO_UTM"] = taxonomy.NO_UTM
templates.env.globals["UNRESOLVED"] = taxonomy.UNRESOLVED

CAVEAT_ANY_TOUCH = (
    "Any-touch: a person is counted in every channel they touched, so these rows "
    "overlap and must not be added up to a stage total. Sub-sources overlap under "
    "their parent for the same reason."
)
CAVEAT_CAUSATION = (
    "Association is not causation. People deeper in the funnel accumulate more "
    "pings, so presence rises at later stages partly as an artifact of that. For "
    "acquisition questions prefer the first-touch view."
)


def _ctx(request, conn, program, flt, **extra):
    facets = filters.facet_values(conn, program)
    # Unfiltered population, so the bar can say "1,204 of 8,436".
    program_total = conn.execute(
        "SELECT COUNT(*) FROM applicants WHERE program=?", (program.key,)
    ).fetchone()[0]
    filtered_total = conn.execute(
        "SELECT COUNT(*) FROM applicants WHERE " + flt.where, flt.params
    ).fetchone()[0]
    fy_years = [r["fy"] for r in conn.execute(
        "SELECT DISTINCT CASE WHEN CAST(substr(started_date,6,2) AS INTEGER) >= 9"
        "                THEN CAST(substr(started_date,1,4) AS INTEGER)"
        "                ELSE CAST(substr(started_date,1,4) AS INTEGER) - 1 END fy"
        " FROM applicants WHERE program=? AND started_date <> ''"
        " ORDER BY fy DESC", (program.key,))]
    ctx = {
        "request": request,
        "program": program,
        "fy_years": fy_years,
        "fy_active": (filters.fiscal_year_of(flt.date_from)
                      if flt.date_field == filters.DEFAULT_DATE_FIELD
                      and flt.date_from and flt.date_to
                      and (flt.date_from, flt.date_to)
                      == filters.fiscal_range(filters.fiscal_year_of(flt.date_from))
                      else None),
        "fy_all_time": not flt.date_field,
        "programs": programs.PROGRAMS,
        "filters": flt,
        "facets": facets,
        "filter_spec": filters.describe(program, facets, flt),
        "program_total": program_total,
        "filtered_total": filtered_total,
        "caveat_any_touch": CAVEAT_ANY_TOUCH,
        "caveat_causation": CAVEAT_CAUSATION,
        "unknown_count": conn.execute(
            "SELECT COUNT(*) FROM unknown_utms WHERE acknowledged=0"
        ).fetchone()[0],
        "has_data": program_total > 0,
    }
    ctx.update(extra)
    return ctx


def _resolve(request):
    key = request.query_params.get("program", "ft")
    if key not in programs.PROGRAMS:
        key = "ft"
    program = programs.get(key)
    conn = get_conn()
    try:
        # The default range is data-driven, so it needs the database.
        fy = filters.default_fiscal_year(conn, program)
    finally:
        conn.close()
    return program, filters.from_query(program, request.query_params,
                                       default_fy=fy)


# ---- routes ----------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def overview(request: Request):
    """Overall funnel first, then the channel breakdowns."""
    conn = get_conn()
    try:
        program, flt = _resolve(request)
        # Query accessors, defined once — several blocks below read multi-value
        # params (pen_pick, tl_channel, tl_sub, tl_year).
        q = request.query_params
        getall = getattr(q, "getlist", None) or (lambda k: q.getall(k))

        # Which funnel stage the two channel charts are measured against.
        # "started" is not offered: every applicant has started, so within-channel
        # conversion to it is 100% for every row and the quality chart goes flat
        # — the same reason the funnel table omits its Started columns.
        # Each channel card picks its own target stage, independently. NB the
        # params are `cvs`/`mvs`, not `stage`: `stage` is already taken by the
        # applicant filter's "Reached stage". Reusing it silently narrowed the
        # population to the people who had reached that stage, which made every
        # channel convert at 100%.
        # "started" is not offered: every applicant has started, so within-channel
        # conversion to it is 100% for every row and the quality chart goes flat
        # — the same reason the funnel table omits its Started columns.
        stage_opts = [k for k in program.stage_keys if k != "started"]

        def _stage(param):
            v = q.get(param)
            return v if v in stage_opts else program.channel_stage

        cmp_stage, mk_stage = _stage("cvs"), _stage("mvs")

        apps, flags = metrics.load_population(conn, program, flt.where, flt.params)
        pings = metrics.load_pings(conn, [a["id"] for a in apps])

        overall = metrics.overall_funnel(program, flags)
        any_touch = metrics.build_matrix(program, apps, flags, pings, "any")
        first_touch = metrics.build_matrix(program, apps, flags, pings, "first")
        last_touch = metrics.build_matrix(program, apps, flags, pings, "last")
        comparison = metrics.channel_comparison(program, any_touch, stage=cmp_stage)
        makeup = metrics.channel_makeup(program, any_touch, stage=mk_stage)

        # --- chart payloads (plain JSON-safe structures) ---
        # Each parent carries its sub-sources so the bar charts can expand in
        # place on click. Each chart applies its OWN inclusion rule to the subs,
        # matching how it treats parents, and reports how many it withheld.
        subs = metrics.subs_by_parent(any_touch)

        def _cmp_subs(channel, stage):
            rows, hidden = [], 0
            for s in subs.get(channel, []):
                if s["n"] < comparison["min_n"]:
                    hidden += 1          # same volume gate as the parent rows
                    continue
                rows.append({"channel": s["sub_source"],
                             "rate": s["within_pct"][stage],
                             "n": s["n"], "final_n": s["counts"][stage]})
            rows.sort(key=lambda r: -r["rate"])
            return rows, hidden

        def _mk_subs(channel, stage):
            rows, hidden = [], 0
            for s in subs.get(channel, []):
                if not s["counts"][stage]:
                    hidden += 1          # same "reached the stage" rule as parents
                    continue
                rows.append({"channel": s["sub_source"],
                             "share": s["penetration_pct"][stage],
                             "n": s["n"], "final_n": s["counts"][stage],
                             "is_no_utm": False})
            rows.sort(key=lambda r: -r["share"])
            return rows, hidden

        def _cmp_rows(cmp_result, stage):
            return [{"channel": r["channel"], "rate": r["rate"], "n": r["n"],
                     "final_n": r["final_n"],
                     "subs": _cmp_subs(r["channel"], stage)[0],
                     "hidden_subs": _cmp_subs(r["channel"], stage)[1]}
                    for r in cmp_result["rows"]]

        def _mk_rows(mk_result, stage):
            return [{"channel": r["channel"], "share": r["share"],
                     "final_n": r["final_n"], "n": r["n"],
                     "is_no_utm": r["is_no_utm"],
                     "subs": _mk_subs(r["channel"], stage)[0],
                     "hidden_subs": _mk_subs(r["channel"], stage)[1]}
                    for r in mk_result["rows"]]

        chart_comparison = _cmp_rows(comparison, cmp_stage)

        # Cost card, sharing a row with First-touch mix. It uses the SAME stage
        # as the quality card so the two read together.
        # Cost card. Unlike the two channel cards it offers EVERY stage
        # including Started — cost per started app is the headline number here,
        # not a degenerate 100% row.
        cost_stage = _stage_of(q, "cst", program.stage_keys, "started")
        cost_attr = _stage_of(q, "ca", ("first", "last", "any"), "first")
        cost_reach = _paid_reach(conn, program, apps, flags, pings)
        cost = _cost_view(conn, program, flt, any_touch, first_touch, cost_stage,
                          attribution=cost_attr, reach=cost_reach,
                          last_matrix=last_touch)
        cost_payload = _cost_payload(conn, program, flt, any_touch, first_touch,
                                     program.stage_keys, cost["months"],
                                     reach=cost_reach, last_matrix=last_touch)

        # Every stage ships with the page so the two "measured against" controls
        # redraw in the browser instead of costing a reload — the same trade the
        # series pickers make. All four stages of both charts are a few KB
        # against a ~180 KB page, and it all comes off one matrix already in
        # memory, so there is no extra query.
        stage_payload = {}
        for k in stage_opts:
            c = metrics.channel_comparison(program, any_touch, stage=k)
            m_ = metrics.channel_makeup(program, any_touch, stage=k)
            stage_payload[k] = {
                "label": program.stage_labels[k],
                "cmp": {"rows": _cmp_rows(c, k), "baseline": c["baseline_rate"]},
                "mk": {"rows": _mk_rows(m_, k), "total": m_["final_total"],
                       "sum_share": m_["sum_share"]},
            }

        first_parents = sorted(
            (r for r in first_touch["rows"] if r["is_parent"]),
            key=lambda r: -r["n"],
        )
        top_ft, rest = first_parents[:9], first_parents[9:]
        chart_first_touch = [{"channel": r["channel"], "n": r["n"]} for r in top_ft]
        if rest:
            chart_first_touch.append(
                {"channel": "Other (%d channels)" % len(rest),
                 "n": sum(r["n"] for r in rest)})

        # Stage-presence chart: parents by default, sub-sources opt-in.
        pen_tree = metrics.matrix_tree(any_touch)
        pen_all = metrics.tree_names(pen_tree)
        pen_default = [p["name"] for p in pen_tree
                       if p["name"] != taxonomy.NO_UTM][:8]
        pen_raw = getall("pen_pick")
        if pen_raw == [PICK_NONE]:
            pen_selected = []
        elif pen_raw:
            pen_selected = [v for v in pen_all if v in pen_raw]
        else:
            pen_selected = pen_default
        pen_full = metrics.penetration_series(
            program, any_touch, selected=pen_all, cap=False)
        pen_kept, pen_dropped = metrics.slice_selection(
            [sv["name"] for sv in sorted(pen_full["series"],
                                         key=lambda sv: sv["rank"])],
            pen_selected)
        chart_penetration = metrics.penetration_series(
            program, any_touch, selected=pen_kept)
        chart_penetration["dropped"] = pen_dropped
        pen_payload = {
            "stages": pen_full["stages"], "totals": pen_full["totals"],
            "entities": pen_full["series"], "selected": pen_selected, "limit": 8,
        }

        # Ties a table row's colour dot to the same channel's series colour in
        # the chart. Keyed on the entity itself, never on rank, so a selection
        # change cannot repaint the survivors.
        series_index = {sv["name"]: sv["colour_index"]
                        for sv in chart_penetration["series"]}

        # --- tag timeline (tl_* params so they can't collide with the filter bar) ---
        tl_facets = metrics.timeline_facets(conn, program)
        tl_tree = metrics.timeline_tree(tl_facets)
        tl_all = metrics.tree_names(tl_tree)
        all_years = tl_facets["years"]

        # Default: the parent channels only. Sub-sources are opt-in, so the
        # chart opens on the eight top-level lines rather than twenty.
        tl_default = [p["name"] for p in tl_tree][:8]
        tl_raw = getall("tl_pick")
        if tl_raw == [PICK_NONE]:
            tl_selected = []
        elif tl_raw:
            tl_selected = [v for v in tl_all if v in tl_raw]
        else:
            tl_selected = tl_default
        sel_years = _picked(request, "tl_year", None, all_years)

        # Uncapped: every entity ships to the browser so ticking a box redraws
        # instantly instead of costing a ~0.45s page load. All 31 entities are
        # 1.3 KB monthly / 18.4 KB daily against a 182 KB page, so the payload
        # was never the constraint I assumed it was.
        tl = metrics.tag_timeline(
            conn, program, flt.where, flt.params,
            picked=None, cap=False,
            years=sel_years,
            select_none=(not sel_years and all_years),
            bucket=q.get("tl_bucket", "week"),
            measure=q.get("tl_measure", "tags"),
        )
        # Group the flat (entity x fiscal year) series into one record per
        # entity, ordered by the canonical rank the client slices on.
        tl_ent = {}
        for sv in tl["series"]:
            e = tl_ent.setdefault(sv["group"], {
                "name": sv["group"], "rank": sv["rank"],
                "torder": sv["torder"], "lines": []})
            e["lines"].append({"fy": sv["fy"], "fy_label": sv["fy_label"],
                               "current": sv["current"], "data": sv["data"],
                               "total": sv["total"]})
        # Displayed in taxonomy order (matching the picker tree); sliced by
        # volume rank, which is what decides who survives the eight slots.
        tl_entities = sorted(tl_ent.values(), key=lambda e: e["torder"])
        tl_kept, tl_dropped = metrics.slice_selection(
            [e["name"] for e in sorted(tl_entities, key=lambda e: e["rank"])],
            tl_selected)
        # The payload is uncapped; what the FIRST paint draws is the same slice
        # the browser will take on the next tick. Colour index is the position
        # within the kept set, which is how metrics.py assigns it too — so the
        # server-rendered chart and every client redraw agree on hues.
        keep_at = {e["name"]: i for i, e in enumerate(
            sorted((e for e in tl_entities if e["name"] in set(tl_kept)),
                   key=lambda e: e["torder"]))}
        tl_view = dict(tl, dropped=tl_dropped, series=[
            dict(sv, colour_index=keep_at[sv["group"]] % 8)
            for sv in tl["series"] if sv["group"] in keep_at])
        tl_payload = {
            "labels": tl["labels"], "bucket": tl["bucket"],
            "measure": tl["measure"], "years": tl["years"],
            "newest": tl["newest"], "entities": tl_entities,
            "selected": tl_selected, "limit": 8,
        }
        tl = tl_view
        # Reference band: applications started, same buckets, same axis. Off by
        # default so the chart stays about tags unless it is asked for.
        tl_apps = q.get("tl_apps") == "1"
        tl_started = metrics.started_apps_series(
            conn, program, flt.where, flt.params,
            bucket=tl["bucket"],
            years=sel_years,
        ) if tl_apps else None

        return templates.TemplateResponse("overview.html", _ctx(
            request, conn, program, flt,
            overall=overall, any_touch=any_touch, first_touch=first_touch,
            comparison=comparison, makeup=makeup,
            cmp_stage=cmp_stage, mk_stage=mk_stage, stage_opts=stage_opts,
            chart_comparison=chart_comparison,
            chart_makeup=_mk_rows(makeup, mk_stage),
            stage_payload=stage_payload,
            chart_first_touch=chart_first_touch, cost=cost,
            cost_stage=cost_stage, cost_stage_opts=program.stage_keys,
            cost_attr=cost_attr, attributions=ATTRIBUTIONS,
            cost_payload=cost_payload,
            chart_penetration=chart_penetration,
            series_index=series_index,
            pen_tree=pen_tree, pen_selected=pen_selected, pen_all=pen_all,
            pen_payload=pen_payload,
            timeline=tl, tl_facets=tl_facets, tl_payload=tl_payload,
            tl_apps=tl_apps, tl_started=tl_started,
            tl_tree=tl_tree, tl_all=tl_all, tl_selected=tl_selected,
            tl_all_years=all_years,
            tl_buckets=metrics.TL_BUCKETS, tl_measures=metrics.TL_MEASURES,
        ))
    finally:
        conn.close()


@app.get("/utm", response_class=HTMLResponse)
def utm_detail(request: Request):
    """Top raw UTM values per stage -- campaign, content, source, medium."""
    conn = get_conn()
    try:
        program, flt = _resolve(request)
        field = request.query_params.get("field", "content")
        if field not in metrics.UTM_FIELDS:
            field = "content"
        try:
            limit = max(5, min(100, int(request.query_params.get("limit", 20))))
        except ValueError:
            limit = 20

        apps, flags = metrics.load_population(conn, program, flt.where, flt.params)
        pings = metrics.load_pings(conn, [a["id"] for a in apps])
        breakdown = metrics.top_utm_breakdown(program, apps, flags, pings, field, limit)
        return templates.TemplateResponse("utm.html", _ctx(
            request, conn, program, flt,
            breakdown=breakdown, field=field, limit=limit,
            utm_fields=metrics.UTM_FIELDS,
        ))
    finally:
        conn.close()


@app.get("/referrers", response_class=HTMLResponse)
def referrers(request: Request):
    """Top Slate native ping referrers — the third export in DATA_SCHEMA.md.

    Separate from the channel tables on purpose: the grain is page views, not
    UTM touches, so these numbers must never blend into the validated funnel.
    """
    conn = get_conn()
    try:
        program, flt = _resolve(request)
        group = request.query_params.get("group", "domain")
        scope = request.query_params.get("scope", "external")
        try:
            limit = max(10, min(200, int(request.query_params.get("limit", 40))))
        except ValueError:
            limit = 40

        has_refs = conn.execute("SELECT COUNT(*) FROM ref_pings").fetchone()[0] > 0
        summary = referrer_rows = terms = None
        if has_refs:
            summary = metrics.referrer_summary(conn, program, flt.where, flt.params)
            referrer_rows = metrics.top_referrers(
                conn, program, flt.where, flt.params, group, scope, limit)
            terms = metrics.top_ref_terms(conn)

        return templates.TemplateResponse("referrers.html", _ctx(
            request, conn, program, flt,
            has_refs=has_refs, summary=summary, breakdown=referrer_rows,
            terms=terms, group=group, scope=scope, limit=limit,
            ref_groups=metrics.REF_GROUPS, ref_scopes=metrics.REF_SCOPES,
        ))
    finally:
        conn.close()


def _stage_of(q, param, allowed, default):
    v = q.get(param)
    return v if v in allowed else default


ATTRIBUTIONS = [
    {"key": "first", "label": "First touch",
     "note": "credited to the channel that FOUND them — rows partition people, "
             "so they add up"},
    {"key": "last", "label": "Last touch",
     "note": "credited to the channel they touched LAST — what closed them. "
             "Also one channel per person, so these add up too"},
    {"key": "any", "label": "Any touch",
     "note": "credited to EVERY paid channel they touched — rows overlap, so "
             "the total is a de-duplicated count, not a sum"},
]


def _paid_reach(conn, program, apps, flags, pings):
    """Distinct people reached by any channel that has spend — the only honest
    denominator for a blended ANY-touch cost."""
    paid = [r["channel"] for r in conn.execute(
        "SELECT DISTINCT channel FROM spend WHERE program=?", (program.key,))]
    return metrics.paid_reach(program, apps, flags, pings, paid) if paid else None


def _cost_payload(conn, program, flt, any_matrix, first_matrix, stage_keys,
                  months, reach=None, last_matrix=None):
    """Every stage x both attributions, so the chips AND the first/any toggle
    redraw in the browser. A few KB against a ~230 KB page, and no extra
    queries — the matrices are already in memory."""
    per = metrics.cost_stage_payload(
        conn, program, any_matrix, first_matrix, stage_keys, months=months,
        reach=reach, last_matrix=last_matrix)

    def pack(v):
        return {
            "rows": [{"name": r["name"], "cost": r["cost"], "n": r["stage_n"],
                      "per": r["cost_per_stage"], "start_n": r["first_n"],
                      "start_per": r["cost_per_start"],
                      "subs": [{"name": s["name"], "cost": s["cost"],
                                "n": s["stage_n"], "per": s["cost_per_stage"],
                                "start_n": s["first_n"],
                                "start_per": s["cost_per_start"]}
                               for s in r["subs"]]}
                     for r in v["rows"]],
            "total_cost": v["total_cost"], "total_n": v["stage_total"],
            "per": v["blended_per_stage"], "label": v["stage_label"],
            "start_total": v["total_first"], "start_per": v["blended_per_start"],
            "rows_sum": v["rows_sum"],
        }

    return {
        "stages": [{"key": k, "label": program.stage_labels[k]} for k in stage_keys],
        "attributions": ATTRIBUTIONS,
        "by_attr": {a: {k: pack(v) for k, v in per[a].items()} for a in per},
    }


def _cost_view(conn, program, flt, any_matrix, first_matrix, stage,
               attribution="first", reach=None, last_matrix=None):
    """Everything both the Overview card and the Cost tab need."""
    months, all_months, narrowed = metrics.spend_months(conn, program, flt)
    cost = metrics.cost_by_channel(
        conn, program, any_matrix, first_matrix, stage, months=months,
        attribution=attribution, reach=reach, last_matrix=last_matrix)
    cost.update({
        "months": months, "all_months": all_months, "narrowed": narrowed,
        "has_spend": bool(cost["rows"]),
    })
    return cost


@app.get("/cost", response_class=HTMLResponse)
def cost(request: Request):
    conn = get_conn()
    try:
        program, flt = _resolve(request)
        q = request.query_params
        stage_opts = [k for k in program.stage_keys if k != "started"]
        stage = _stage_of(q, "vs", stage_opts, program.channel_stage)
        getall = getattr(q, "getlist", None) or (lambda k: q.getall(k))

        apps, flags = metrics.load_population(conn, program, flt.where, flt.params)
        pings = metrics.load_pings(conn, [a["id"] for a in apps])
        any_touch = metrics.build_matrix(program, apps, flags, pings, "any")
        first_touch = metrics.build_matrix(program, apps, flags, pings, "first")
        last_touch = metrics.build_matrix(program, apps, flags, pings, "last")
        cost_attr = _stage_of(q, "ca", ("first", "last", "any"), "first")
        cost_reach = _paid_reach(conn, program, apps, flags, pings)
        cost = _cost_view(conn, program, flt, any_touch, first_touch, stage,
                          attribution=cost_attr, reach=cost_reach,
                          last_matrix=last_touch)
        cost_payload = _cost_payload(conn, program, flt, any_touch, first_touch,
                                     stage_opts, cost["months"], reach=cost_reach,
                                     last_matrix=last_touch)

        sp_tree = metrics.spend_tree(conn, program)
        sp_all = metrics.tree_names(sp_tree)
        sp_default = [p["name"] for p in sp_tree]      # parents only
        sp_raw = getall("sp_pick")
        if sp_raw == [PICK_NONE]:
            sp_selected = []
        elif sp_raw:
            sp_selected = [v for v in sp_all if v in sp_raw]
        else:
            sp_selected = sp_default
        trend = metrics.spend_trend(conn, program, cost["months"],
                                    picked=sp_selected)
        trend_full = metrics.spend_trend(conn, program, cost["months"], cap=False)
        sp_payload = {
            "labels": trend_full["labels"],
            "defaults": sp_default,
            "entities": trend_full["series"],
            "selected": sp_selected, "limit": 8,
        }
        uploads = conn.execute(
            "SELECT * FROM spend_uploads WHERE program=? ORDER BY id DESC LIMIT 20",
            (program.key,)).fetchall()
        return templates.TemplateResponse("cost.html", _ctx(
            request, conn, program, flt,
            cost=cost, trend=trend, spend_uploads=uploads,
            stage=stage, stage_opts=stage_opts, cost_payload=cost_payload,
            cost_attr=cost_attr, attributions=ATTRIBUTIONS,
            sp_tree=sp_tree, sp_all=sp_all, sp_selected=sp_selected,
            sp_payload=sp_payload,
            coverage=spend.meta_ad_coverage(conn, program.key),
        ))
    finally:
        conn.close()


@app.get("/uploads", response_class=HTMLResponse)
def uploads(request: Request, ok: Optional[str] = None, err: Optional[str] = None):
    conn = get_conn()
    try:
        program, flt = _resolve(request)
        history = conn.execute(
            "SELECT * FROM uploads ORDER BY id DESC LIMIT 50"
        ).fetchall()
        ref_history = conn.execute(
            "SELECT * FROM ref_uploads ORDER BY id DESC LIMIT 25"
        ).fetchall()
        unknown = conn.execute(
            "SELECT * FROM unknown_utms ORDER BY acknowledged, ping_count DESC"
        ).fetchall()
        totals = {
            k: conn.execute("SELECT COUNT(*) FROM applicants WHERE program=?", (k,))
            .fetchone()[0] for k in programs.PROGRAMS
        }
        ref_pings = conn.execute("SELECT COUNT(*) FROM ref_pings").fetchone()[0]
        spend_history = conn.execute(
            "SELECT * FROM spend_uploads ORDER BY id DESC LIMIT 25").fetchall()
        coverage = spend.meta_ad_coverage(conn, program.key)
        return templates.TemplateResponse("uploads.html", _ctx(
            request, conn, program, flt,
            history=history, ref_history=ref_history, unknown=unknown,
            totals=totals, ref_pings=ref_pings, ok=ok, err=err,
            spend_history=spend_history, coverage=coverage,
        ))
    finally:
        conn.close()


SPEND_KINDS = {"spend_google": spend.GOOGLE, "spend_meta": spend.META}


@app.post("/uploads")
async def do_upload(request: Request, program: str = Form("auto"),
                    files: list[UploadFile] = File(...),
                    spend_program: str = Form("ft")):
    """Accepts several files at once.

    With program="auto" (the default) each file's type is detected from its
    HEADERS -- see ingest.sniff_kind -- so a batch of mixed Slate and spend
    exports can go up in one go. Choosing an explicit type forces it for every
    file in the batch, which is the escape hatch when a guess is wrong.
    """
    conn = get_conn()
    try:
        results, errors = [], []
        for upload in files:
            payload = await upload.read()
            name = upload.filename or "upload.xlsx"
            if not payload:
                errors.append("%s: empty file" % name)
                continue
            if program == "auto":
                kind, _conf, why = ingest.sniff_kind(name, payload)
                detected = " (detected: %s)" % why
            else:
                kind, detected = program, ""
            msg, err = _ingest_one(conn, kind, name, payload, spend_program)
            if err:
                errors.append("%s: %s" % (name, err))
            else:
                results.append(msg + detected)

        parts = []
        if results:
            parts.append(" · ".join(results))
        if errors:
            parts.append("FAILED — " + " · ".join(errors))
        joined = " || ".join(parts) or "Nothing uploaded"
        key = "err" if errors and not results else "ok"
        return RedirectResponse("/uploads?%s=%s" % (key, joined[:800]),
                                status_code=303)
    finally:
        conn.close()


def _ingest_one(conn, program, name, payload, spend_program):
    """Ingest one file of a known kind -> (success message, None) | (None, error).

    Returns rather than redirects so the caller can run a whole batch and report
    per-file outcomes together.
    """
    if (program not in SPEND_KINDS and program != "referrals"
            and program not in programs.PROGRAMS):
        return None, "unknown export type %r" % program

    import io
    digest = ingest.sha256_of(payload)
    try:
        if program in SPEND_KINDS:
            platform = SPEND_KINDS[program]
            prog_key = (spend_program if spend_program in programs.PROGRAMS
                        else "ft")
            rows, meta = spend.parse(io.BytesIO(payload), platform, name)
            result = spend.store(conn, prog_key, rows, meta, digest)
            msg = ("%s: $%s across %s rows, %s month(s) -> %s (%s replaced)"
                   % (name, format(result["total_cost"], ",.2f"),
                      result["row_count"], len(result["months"]),
                      programs.PROGRAMS[prog_key].label, result["replaced"]))
            if result["warnings"]:
                msg += " · " + " ".join(result["warnings"])
        elif program == "referrals":
            result = ingest.ingest_referrals(
                conn, io.BytesIO(payload), name, digest)
            msg = ("%s: %s page views -> %s new, %s already known, across %s people"
                   % (name, result["row_count"], result["pings_new"],
                      result["pings_duplicate"], result["people"]))
        else:
            result = ingest.ingest(conn, io.BytesIO(payload), program, name, digest)
            msg = ("%s: %s rows -> %s new, %s updated (%s new pings, %s already known)"
                   % (name, result["row_count"], result["applicants_new"],
                      result["applicants_updated"], result["pings_new"],
                      result["pings_duplicate"]))
    except (ingest.IngestError, spend.SpendError) as exc:
        return None, str(exc)[:400]
    return msg, None


@app.post("/unknown/{unknown_id}/ack")
def ack_unknown(unknown_id: int):
    conn = get_conn()
    try:
        conn.execute("UPDATE unknown_utms SET acknowledged=1 WHERE id=?", (unknown_id,))
        conn.commit()
        return RedirectResponse("/uploads", status_code=303)
    finally:
        conn.close()


@app.get("/health")
def health():
    return {"ok": True}
