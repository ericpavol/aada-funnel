"""Funnel metrics computed over a filtered population.

Mirrors analysis_engine.build_matrix exactly, but sources its data from the DB
instead of an xlsx row list, and adds the extras the brief asked for: the
overall (channel-independent) funnel, first-touch / last-touch attribution, and
top-N UTM field breakdowns per stage.

Every channel figure is ANY-TOUCH: a person is counted in every channel they
touched, so rows OVERLAP and MUST NOT be summed to a stage total. Sub-sources
overlap under their parent for the same reason.
"""
from collections import defaultdict

from . import taxonomy


def _pct(num, den):
    return (num / den) if den else 0.0


def load_population(conn, program, where_sql, params):
    """-> (applicants, stage_flags) for the filtered set.

    applicants: list of sqlite3.Row
    stage_flags: list of {stage_key: bool} aligned by index
    """
    sql = (
        "SELECT id, global_id, term, region, country, city, age, emphasis, decision,"
        " started_date, submitted_date, completed_date,"
        " st_started, st_submitted, st_aud_req, st_aud_comp, st_admitted,"
        " st_accepted, st_enrolled"
        " FROM applicants WHERE " + where_sql
    )
    rows = conn.execute(sql, params).fetchall()
    flags = [{k: bool(r["st_" + k]) for k in program.stage_keys} for r in rows]
    return rows, flags


def load_pings(conn, applicant_ids):
    """-> {applicant_id: [ping rows in chronological order]}."""
    out = defaultdict(list)
    if not applicant_ids:
        return out
    ids = list(applicant_ids)
    for chunk_start in range(0, len(ids), 900):
        chunk = ids[chunk_start:chunk_start + 900]
        marks = ",".join("?" * len(chunk))
        sql = (
            "SELECT applicant_id, ts, seq, source, medium, campaign, content,"
            " channel, sub_source FROM pings WHERE applicant_id IN (%s)"
            " ORDER BY applicant_id, CASE WHEN ts='' THEN 1 ELSE 0 END, ts, seq" % marks
        )
        for p in conn.execute(sql, chunk):
            out[p["applicant_id"]].append(p)
    return out


def overall_funnel(program, flags):
    """Channel-independent funnel: the top-level overview.

    Reports each stage's count, its share of Started, and the step-over-step
    conversion from the previous stage.
    """
    total = len(flags)
    counts = {k: sum(1 for f in flags if f[k]) for k in program.stage_keys}
    started = counts.get("started", total) or 0
    steps = []
    prev = None
    for k in program.stage_keys:
        n = counts[k]
        steps.append({
            "key": k,
            "label": program.stage_labels[k],
            "n": n,
            "pct_of_started": _pct(n, started),
            "pct_of_prev": 1.0 if prev is None else _pct(n, prev),
            "drop_from_prev": 0 if prev is None else prev - n,
            "prev_label": None if prev is None else program.stage_labels[
                program.stage_keys[program.stage_keys.index(k) - 1]],
        })
        prev = n
    return {"population": total, "counts": counts, "steps": steps}


def build_matrix(program, applicants, flags, pings_by_app, touch="any"):
    """Channel x stage matrix.

    touch: 'any'   -> every channel the person touched (canonical, overlapping)
           'first' -> only the person's first ping's channel (acquisition)
           'last'  -> only the person's last ping's channel
    With 'first'/'last' each person lands in exactly ONE parent channel, so
    those rows DO sum to the stage total.
    """
    stage_keys = program.stage_keys
    members = defaultdict(set)

    for i, a in enumerate(applicants):
        plist = pings_by_app.get(a["id"], [])
        keys = set()
        if touch == "any":
            for p in plist:
                keys.add((p["channel"], None))
                if p["sub_source"]:
                    keys.add((p["channel"], p["sub_source"]))
        elif plist:
            p = plist[0] if touch == "first" else plist[-1]
            keys.add((p["channel"], None))
            if p["sub_source"]:
                keys.add((p["channel"], p["sub_source"]))
        if not keys:
            keys.add((taxonomy.NO_UTM, None))
        for k in keys:
            members[k].add(i)

    totals = {k: sum(1 for f in flags if f[k]) for k in stage_keys}

    matrix = {}
    for key, idxset in members.items():
        n = len(idxset)
        counts = {k: sum(1 for i in idxset if flags[i][k]) for k in stage_keys}
        matrix[key] = {
            "key": key,
            "channel": key[0],
            "sub_source": key[1],
            "is_parent": key[1] is None,
            "n": n,
            "counts": counts,
            "within_pct": {k: _pct(counts[k], n) for k in stage_keys},
            "penetration_pct": {k: _pct(counts[k], totals[k]) for k in stage_keys},
        }

    order = taxonomy.ordered_rows(matrix.keys())
    return {
        "totals": totals,
        "population": len(applicants),
        "touch": touch,
        "rows": [matrix[k] for k in order],
        "by_key": matrix,
        "undeclared_keys": taxonomy.unknown_taxonomy_keys(matrix.keys()),
    }


UTM_FIELDS = {
    "campaign": "UTM Campaign",
    "content": "UTM Content",
    "source": "UTM Source",
    "medium": "UTM Medium",
}


def top_utm_breakdown(program, applicants, flags, pings_by_app, field,
                      limit=15):
    """Top values of one UTM field at each funnel stage (any-touch).

    Answers "what were the top UTM contents among admits?" -- distinct from the
    channel tables, which roll these up into the taxonomy.
    """
    if field not in UTM_FIELDS:
        raise KeyError(field)
    stage_keys = program.stage_keys
    members = defaultdict(set)
    for i, a in enumerate(applicants):
        for p in pings_by_app.get(a["id"], []):
            val = (p[field] or "").strip()
            members[val if val else "(not set)"].add(i)

    totals = {k: sum(1 for f in flags if f[k]) for k in stage_keys}
    rows = []
    for val, idxset in members.items():
        counts = {k: sum(1 for i in idxset if flags[i][k]) for k in stage_keys}
        rows.append({
            "value": val,
            "n": len(idxset),
            "counts": counts,
            "within_pct": {k: _pct(counts[k], len(idxset)) for k in stage_keys},
            "penetration_pct": {k: _pct(counts[k], totals[k]) for k in stage_keys},
        })

    # Rank by the headline stage, not literally the last one. Enrolled is the
    # last stage but is barely populated yet, and sorting a UTM table by a
    # 16-person column would shuffle it on noise.
    final = program.channel_stage
    rows.sort(key=lambda r: (-r["counts"][final], -r["n"], r["value"]))
    return {
        "field": field, "label": UTM_FIELDS[field], "totals": totals,
        "rows": rows[:limit], "truncated": max(0, len(rows) - limit),
        "distinct": len(rows),
    }


# ---------------------------------------------------------------------------
# Tag timeline — when channel touches ("tags") actually happen.
#
# Effective date: the tag's OWN timestamp, which 98.5% of Full-Time and 99.6% of
# Summer tags carry, falling back to the applicant's app start date for the rest.
# Every fallback case has a start date, so no tag is ever undated. `approx`
# counts the fallbacks so the UI can say how much of a line is approximated.
#
# Fiscal year runs 1 Sept -> 31 Aug. Sept is month 1 of the year, so a date in
# Jan-Aug belongs to the fiscal year that STARTED the previous calendar year.
# Ending 31 Aug rather than 31 Jul is deliberate: an 11-month window would strand
# every August tag between two fiscal years.
# ---------------------------------------------------------------------------
FISCAL_START_MONTH = 9

TL_BUCKETS = {"month": "Monthly", "week": "Weekly", "day": "Daily"}
TL_MEASURES = {"tags": "Tag events", "people": "Distinct people"}
TL_GROUPS = {"channel": "Category (channel)", "sub": "Sub-category (sub-source)"}

# Bucket index expressions. All are positions WITHIN the fiscal year, never
# absolute dates — that is what lets several years overlay on one axis.
_TL_BUCKET_SQL = {
    "month": "(CAST(strftime('%m', d) AS INTEGER) - 9 + 12) % 12",
    "week": "CAST((julianday(d) - julianday(printf('%04d-09-01', fyear))) / 7 AS INTEGER)",
    "day": "CAST(julianday(d) - julianday(printf('%04d-09-01', fyear)) AS INTEGER)",
}
_TL_BUCKET_COUNT = {"month": 12, "week": 53, "day": 366}


def fiscal_year_of(date_str):
    """'YYYY-MM-DD…' -> the fiscal year it belongs to (the Sept start year)."""
    if not date_str or len(date_str) < 7:
        return None
    year, month = int(date_str[:4]), int(date_str[5:7])
    return year if month >= FISCAL_START_MONTH else year - 1


def fiscal_year_label(fy):
    return "FY %d/%d" % (fy, (fy + 1) % 100)


def _tl_labels(bucket):
    """Axis labels for positions inside a fiscal year, year-agnostic.

    Built off a reference non-leap year so the labels are identical whichever
    fiscal years are overlaid.
    """
    import datetime as dt
    if bucket == "month":
        start = dt.date(2001, 9, 1)
        out = []
        for i in range(12):
            m = (start.month - 1 + i) % 12 + 1
            out.append(dt.date(2001, m, 1).strftime("%b"))
        return out
    start = dt.date(2001, 9, 1)
    step = 7 if bucket == "week" else 1
    return [(start + dt.timedelta(days=i * step)).strftime("%-d %b")
            for i in range(_TL_BUCKET_COUNT[bucket])]


def started_apps_series(conn, program, where_sql, params, bucket="week", years=None):
    """Applications STARTED per bucket — the reference band on the timeline.

    Dated on `started_date` (the application's own start), not on any tag, and
    bucketed identically to the tag lines so the two are directly comparable.

    Plotted on the SAME y-axis as the tags, in real counts. Deliberately not a
    second y-axis: two independent scales on one chart can make any two series
    look correlated, which is exactly the judgement this band exists to inform.
    Tags outnumber starts roughly 6:1, so the band sits low — that ratio is
    itself information, and the UI states it.
    """
    if bucket not in TL_BUCKETS:
        bucket = "week"
    # The blank-date guard belongs INSIDE the CTE: by the outer SELECT the
    # column is aliased to `d`, so `started_date` no longer resolves there.
    where = ["1 = 1"]
    args = list(params)
    if years:
        where.append("fyear IN (%s)" % ",".join("?" * len(years)))
        args.extend(int(y) for y in years)

    rows = conn.execute(
        "WITH ev AS ("
        "  SELECT substr(started_date,1,10) AS d FROM applicants"
        "   WHERE (" + where_sql + ") AND started_date <> ''"
        "), fy AS ("
        "  SELECT d, CAST(strftime('%Y', d) AS INTEGER)"
        "         - (CASE WHEN CAST(strftime('%m', d) AS INTEGER) >= 9 THEN 0 ELSE 1 END)"
        "         AS fyear FROM ev WHERE d <> ''"
        ")"
        " SELECT fyear, " + _TL_BUCKET_SQL[bucket] + " AS bkt, COUNT(*) AS n"
        "   FROM fy WHERE " + " AND ".join(where) +
        " GROUP BY fyear, bkt", args).fetchall()

    n_buckets = _TL_BUCKET_COUNT[bucket]
    cells = defaultdict(dict)
    for r in rows:
        if r["bkt"] is None or not (0 <= r["bkt"] < n_buckets):
            continue
        cells[r["fyear"]][r["bkt"]] = r["n"]

    newest = max(cells) if cells else None
    series = []
    for fyear in sorted(cells, reverse=True):
        data = cells[fyear]
        series.append({
            "fy": fyear,
            "fy_label": fiscal_year_label(fyear),
            "current": fyear == newest,
            "total": sum(data.values()),
            "peak": max(data.values()) if data else 0,
            "data": [data.get(i, 0) for i in range(n_buckets)],
        })
    return {"series": series, "total": sum(s["total"] for s in series),
            "peak": max((s["peak"] for s in series), default=0)}


def series_tree(entries):
    """Parents with their sub-sources nested, in canonical taxonomy order.

    `entries` is {entity_name: count}. Both pickers render this shape, so the
    timeline (counted in tags) and the presence chart (counted in people) get an
    identical control.
    """
    parents, subs = {}, defaultdict(list)
    for name, n in entries.items():
        parent, sub = taxonomy.split_sub_name(name)
        if sub is None:
            parents[name] = n
        else:
            subs[parent].append({"name": name, "label": sub, "n": n})

    ordered = taxonomy.canonical_order_index(list(parents))
    out = []
    for parent in ordered:
        kids = sorted(subs.get(parent, []), key=lambda k: -k["n"])
        kids = [k for k in taxonomy.canonical_order_index([k["name"] for k in kids])]
        kid_rows = []
        for kname in kids:
            row = next(k for k in subs[parent] if k["name"] == kname)
            kid_rows.append(row)
        out.append({"name": parent, "n": parents[parent], "subs": kid_rows})
    return out


def timeline_tree(facets):
    """Series tree for the timeline, counted in tag volume."""
    entries = {v: n for v, n in facets["channels"]}
    for c, v, n in facets["subs"]:
        entries[taxonomy.sub_name(c, v)] = n
    return series_tree(entries)


def matrix_tree(matrix):
    """Series tree for the presence chart, counted in people touched."""
    entries = {}
    for row in matrix["rows"]:
        name = (row["channel"] if row["is_parent"]
                else taxonomy.sub_name(row["channel"], row["sub_source"]))
        entries[name] = row["n"]
    return series_tree(entries)


def tree_names(tree):
    """Flat list of every entity in a tree, parents before their own subs."""
    out = []
    for p in tree:
        out.append(p["name"])
        out.extend(k["name"] for k in p["subs"])
    return out


def slice_selection(names_by_rank, selected, limit=8):
    """The one rule both halves obey: take the first `limit` of what is ticked,
    in canonical rank order.

    Python uses it for the first paint; app.js mirrors exactly this when you tick
    a box, so the surviving series and their colours never depend on which side
    did the picking.
    """
    limit = max(1, min(limit, 8))
    chosen = [n for n in names_by_rank if n in set(selected)]
    return chosen[:limit], chosen[limit:]


def timeline_facets(conn, program):
    """Categories, sub-categories and fiscal years available for the timeline."""
    channels = conn.execute(
        "SELECT p.channel AS v, COUNT(*) AS n FROM pings p"
        " JOIN applicants a ON a.id = p.applicant_id"
        " WHERE a.program=? AND p.channel<>'' GROUP BY p.channel"
        " ORDER BY n DESC", (program.key,)).fetchall()
    subs = conn.execute(
        "SELECT p.channel AS c, p.sub_source AS v, COUNT(*) AS n FROM pings p"
        " JOIN applicants a ON a.id = p.applicant_id"
        " WHERE a.program=? AND p.sub_source<>'' GROUP BY p.channel, p.sub_source"
        " ORDER BY n DESC", (program.key,)).fetchall()
    years = conn.execute(
        "SELECT DISTINCT CAST(strftime('%Y', d) AS INTEGER)"
        "   - (CASE WHEN CAST(strftime('%m', d) AS INTEGER) >= 9 THEN 0 ELSE 1 END) AS fy"
        " FROM (SELECT substr(COALESCE(NULLIF(p.ts,''), a.started_date),1,10) AS d"
        "         FROM pings p JOIN applicants a ON a.id = p.applicant_id"
        "        WHERE a.program=?"
        "          AND COALESCE(NULLIF(p.ts,''), a.started_date) <> '')"
        " WHERE fy IS NOT NULL ORDER BY fy DESC", (program.key,)).fetchall()
    return {
        "channels": [(r["v"], r["n"]) for r in channels],
        "subs": [(r["c"], r["v"], r["n"]) for r in subs],
        "years": [r["fy"] for r in years],
    }


def tag_timeline(conn, program, where_sql, params, picked=None, years=None,
                 bucket="week", measure="tags", max_series=8, select_none=False,
                 cap=True):
    """`cap=False` returns EVERY entity, uncapped, each carrying a canonical
    `rank`. That is the client payload: the browser slices the first N of what
    is ticked, by rank, so which-8-survive and their colours stay decided by the
    same Python ordering that the tests pin — the JS half is only a slice."""
    """Tag counts over the fiscal year, one line per category x fiscal year.

    Colour encodes the CATEGORY (so a channel keeps its colour everywhere) and
    line style encodes the YEAR — the newest fiscal year is solid, earlier ones
    dashed and dimmed. Two encodings rather than one because both dimensions
    overlay at once.
    """
    if bucket not in TL_BUCKETS:
        bucket = "week"
    if measure not in TL_MEASURES:
        measure = "tags"

    if select_none:
        # "cleared everything" is a real state and must not silently fall back
        # to showing all series.
        return {
            "labels": _tl_labels(bucket), "series": [], "bucket": bucket,
            "measure": measure, "years": [], "newest": None, "approx": 0,
            "dropped": [], "max_series": max_series, "grand_total": 0,
            "tags_total": 0, "people_unique": 0, "headline": 0,
            "headline_unit": "tags" if measure == "tags" else "people",
            "measure_label": TL_MEASURES.get(measure, measure), "empty": True,
        }

    where = ["1 = 1"]
    args = list(params)
    if picked:
        where.append("grp IN (%s)" % ",".join("?" * len(picked)))
        args.extend(picked)
    if years:
        where.append("fyear IN (%s)" % ",".join("?" * len(years)))
        args.extend(int(y) for y in years)

    # Parents and sub-sources are UNIONed rather than derived from one another.
    # A parent's tag count could be summed from its subs, but its DISTINCT
    # PEOPLE count cannot -- one person can appear under several sub-sources --
    # so each grain is aggregated from the rows directly.
    cte = (
        "WITH ev AS ("
        "  SELECT p.applicant_id AS applicant_id, p.channel AS channel,"
        "         p.sub_source AS sub_source,"
        "         substr(COALESCE(NULLIF(p.ts,''), a.started_date),1,10) AS d,"
        "         CASE WHEN p.ts = '' THEN 1 ELSE 0 END AS approx"
        "    FROM pings p"
        "    JOIN (SELECT id, started_date FROM applicants WHERE " + where_sql + ") a"
        "      ON a.id = p.applicant_id"
        "   WHERE COALESCE(NULLIF(p.ts,''), a.started_date) <> ''"
        "), fy AS ("
        "  SELECT *, CAST(strftime('%Y', d) AS INTEGER)"
        "         - (CASE WHEN CAST(strftime('%m', d) AS INTEGER) >= 9 THEN 0 ELSE 1 END)"
        "         AS fyear FROM ev"
        "), ent AS ("
        "  SELECT channel AS grp, fyear, d, applicant_id, approx FROM fy"
        "  UNION ALL"
        "  SELECT channel || '" + taxonomy.SUB_SEP + "' || sub_source AS grp,"
        "         fyear, d, applicant_id, approx FROM fy WHERE sub_source <> ''"
        ")"
    )
    rows = conn.execute(
        cte +
        " SELECT grp, fyear, " + _TL_BUCKET_SQL[bucket] + " AS bkt,"
        "        COUNT(*) AS tags, COUNT(DISTINCT applicant_id) AS people,"
        "        SUM(approx) AS approx"
        "   FROM ent WHERE " + " AND ".join(where) +
        " GROUP BY grp, fyear, bkt", args).fetchall()

    # Headline counts. `people_unique` is deliberately a separate query rather
    # than a sum of the per-bucket figures: a person active in three months is
    # distinct in each of them, so adding buckets over-counts humans (by ~1.3x
    # on this data). The chart is per-bucket, but the headline must not lie.
    #
    # It reads from `fy`, not `ent`: `ent` duplicates every ping that has a
    # sub-source (once under the parent, once under the sub), so counting there
    # would roughly double the headline.
    head_where = ["1 = 1"]
    head_args = list(params)
    if years:
        head_where.append("fyear IN (%s)" % ",".join("?" * len(years)))
        head_args.extend(int(y) for y in years)
    head = conn.execute(
        cte + " SELECT COUNT(*) AS tags, COUNT(DISTINCT applicant_id) AS people"
        "   FROM fy WHERE " + " AND ".join(head_where), head_args).fetchone()

    n_buckets = _TL_BUCKET_COUNT[bucket]
    value_key = "tags" if measure == "tags" else "people"

    totals = defaultdict(int)       # grp -> total, to rank series
    cells = defaultdict(dict)       # (grp, fyear) -> {bkt: value}
    approx_total = 0
    years_seen = set()
    for r in rows:
        if r["bkt"] is None or not (0 <= r["bkt"] < n_buckets):
            continue
        key = (r["grp"], r["fyear"])
        cells[key][r["bkt"]] = r[value_key]
        totals[r["grp"]] += r[value_key]
        approx_total += r["approx"] or 0
        years_seen.add(r["fyear"])

    ranked = [g for g, _ in sorted(totals.items(), key=lambda kv: -kv[1])]
    # Hard-capped at the palette's 8 slots. Colours must never cycle: a 9th line
    # sharing slot 1's hue would read as the same category, so extra categories
    # are reported as undrawn instead (the UI lists them and lets you select
    # them explicitly).
    if cap:
        max_series = max(1, min(max_series, 8))
        kept = ranked[:max_series]
        dropped = ranked[max_series:]
    else:
        kept = ranked
        dropped = []

    # Colour by category, ordered by the taxonomy so it matches the tables, and
    # assigned across the KEPT set only — indexing over `ranked` could hand a
    # kept category an index past 8 and wrap it onto a colour already in use.
    order = taxonomy.canonical_order_index(kept)
    # Canonical slot, NOT position within `kept`. Assigning by position meant
    # ticking one extra series repainted every other line -- Meta could be green
    # here and amber after one click. None = the grey tail (see taxonomy).
    colour_index = {g: taxonomy.channel_slot(g) for g in order}
    # Two orderings, because they answer two different questions and the client
    # has to reproduce both:
    #   rank   — by volume: WHICH eight survive when more than eight are ticked
    #            (drop the smallest, never "whoever sits lowest in the list").
    #   torder — canonical taxonomy: which COLOUR each survivor gets, assigned
    #            across the kept set so the hues match the tables below.
    rank = {g: i for i, g in enumerate(ranked)}
    torder = {g: i for i, g in enumerate(
        taxonomy.canonical_order_index(list(totals)))}
    if cap:
        assert all(i is None or i < 8 for i in colour_index.values()), \
            "palette slot overflow"

    newest = max(years_seen) if years_seen else None
    series = []
    for grp in kept:
        for fyear in sorted(y for (g, y) in cells if g == grp):
            data = cells[(grp, fyear)]
            series.append({
                "group": grp,
                "fy": fyear,
                "fy_label": fiscal_year_label(fyear),
                "label": "%s · %s" % (grp, fiscal_year_label(fyear)),
                "current": fyear == newest,
                "rank": rank.get(grp, 10 ** 6),
                "torder": torder.get(grp, 10 ** 6),
                "colour_index": colour_index.get(grp),
                "is_sub": taxonomy.SUB_SEP in grp,
                "total": sum(data.values()),
                "data": [data.get(i, 0) for i in range(n_buckets)],
            })
    series.sort(key=lambda s: (-s["fy"], -s["total"]))

    return {
        "labels": _tl_labels(bucket),
        "series": series,
        "bucket": bucket, "measure": measure,
        "years": sorted(years_seen, reverse=True),
        "newest": newest,
        "approx": approx_total,
        "dropped": dropped,
        "max_series": max_series,
        # Sum of the plotted values — correct for tag events, and for people it
        # is the sum of per-bucket distincts, which is NOT a headcount.
        "empty": False,
        "grand_total": sum(totals.values()),
        "tags_total": head["tags"] or 0,
        "people_unique": head["people"] or 0,
        # What the headline should say, given the chosen measure.
        "headline": (head["tags"] or 0) if measure == "tags" else (head["people"] or 0),
        "headline_unit": "tags" if measure == "tags" else "people",
        "measure_label": TL_MEASURES[measure],
    }


REF_SCOPES = {
    "external": "Excludes our own site",
    "untagged": "External and carrying no UTM at all",
    "all": "Everything, including apply.aada.edu itself",
}
REF_GROUPS = {"domain": "Referrer domain", "referrer": "Full referrer URL"}


def referrer_summary(conn, program, where_sql, params):
    """Headline figures for the native ping log."""
    # `untagged_external` rather than a bare untagged count: ~82% of all page
    # views have no UTM simply because they are internal navigation inside
    # apply.aada.edu, which is normal and not a tracking gap. The number that
    # actually means something is external arrivals carrying no UTM — the
    # traffic the Ping Data export cannot attribute at all.
    tot = conn.execute(
        "SELECT COUNT(*) AS pings, COUNT(DISTINCT global_id) AS people,"
        " SUM(CASE WHEN has_utm=0 THEN 1 ELSE 0 END) AS untagged,"
        " SUM(CASE WHEN internal=0 AND domain<>'' THEN 1 ELSE 0 END) AS external,"
        " SUM(CASE WHEN internal=0 AND domain<>'' AND has_utm=0 THEN 1 ELSE 0 END)"
        "   AS untagged_external,"
        " COUNT(DISTINCT CASE WHEN internal=0 AND domain<>'' AND has_utm=0"
        "        THEN global_id END) AS untagged_external_people,"
        " MIN(NULLIF(ts,'')) AS first_ts, MAX(ts) AS last_ts,"
        " SUM(CASE WHEN term<>'' THEN 1 ELSE 0 END) AS with_term"
        " FROM ref_pings").fetchone()
    matched = conn.execute(
        "SELECT COUNT(DISTINCT a.id) FROM (SELECT id, global_id FROM applicants"
        " WHERE " + where_sql + ") a"
        " WHERE a.global_id IN (SELECT global_id FROM ref_pings)", params
    ).fetchone()[0]
    return {
        "pings": tot["pings"] or 0, "people": tot["people"] or 0,
        "untagged": tot["untagged"] or 0, "external": tot["external"] or 0,
        "untagged_external": tot["untagged_external"] or 0,
        "untagged_external_people": tot["untagged_external_people"] or 0,
        "with_term": tot["with_term"] or 0,
        "first_ts": tot["first_ts"] or "", "last_ts": tot["last_ts"] or "",
        "matched": matched,
    }


def top_referrers(conn, program, where_sql, params, group="domain",
                  scope="external", limit=40):
    """Top Slate native ping referrers, with funnel outcomes per referrer.

    The applicants join is a SUBQUERY carrying the filter bar's WHERE clause.
    That is deliberate: `ref_pings` also has term/source/medium/campaign/content
    columns, so joining the filter clause directly would make those column names
    ambiguous and silently filter on the wrong table.

    Stage figures are ANY-TOUCH on page views, so referrers overlap and must not
    be summed. `matched` is the applicants (in the current program and filter)
    that this referrer touched; the stage percentages are against it.
    """
    if group not in REF_GROUPS:
        group = "domain"
    if scope not in REF_SCOPES:
        scope = "external"
    col = "rp.domain" if group == "domain" else "rp.referrer"

    if scope == "external":
        scope_sql = "rp.internal = 0 AND rp.domain <> ''"
    elif scope == "untagged":
        scope_sql = "rp.internal = 0 AND rp.domain <> '' AND rp.has_utm = 0"
    else:
        scope_sql = "1 = 1"

    stage_cols = ", ".join(
        "COUNT(DISTINCT CASE WHEN a.st_%s = 1 THEN a.id END) AS s_%s" % (k, k)
        for k in program.stage_keys)

    sql = (
        "SELECT %s AS label, COUNT(*) AS pings,"
        " COUNT(DISTINCT rp.global_id) AS people,"
        " SUM(CASE WHEN rp.has_utm = 0 THEN 1 ELSE 0 END) AS untagged_pings,"
        " MAX(rp.internal) AS internal,"
        " COUNT(DISTINCT a.id) AS matched, %s"
        " FROM ref_pings rp"
        " LEFT JOIN (SELECT id, global_id, %s FROM applicants WHERE %s) a"
        "        ON a.global_id = rp.global_id"
        " WHERE %s AND %s <> ''"
        " GROUP BY label"
        " ORDER BY s_%s DESC, matched DESC, people DESC, pings DESC"
        " LIMIT ?" % (
            col, stage_cols,
            ", ".join("st_" + k for k in program.stage_keys), where_sql,
            scope_sql, col, program.channel_stage)
    )
    rows = conn.execute(sql, list(params) + [limit]).fetchall()

    out = []
    for r in rows:
        matched = r["matched"] or 0
        counts = {k: r["s_" + k] or 0 for k in program.stage_keys}
        out.append({
            "label": r["label"], "pings": r["pings"], "people": r["people"],
            "untagged_pings": r["untagged_pings"] or 0,
            "internal": bool(r["internal"]), "matched": matched,
            "counts": counts,
            "within_pct": {k: _pct(counts[k], matched) for k in program.stage_keys},
        })

    distinct = conn.execute(
        "SELECT COUNT(*) FROM (SELECT %s FROM ref_pings rp WHERE %s AND %s <> ''"
        " GROUP BY %s)" % (col, scope_sql, col, col)).fetchone()[0]
    return {"rows": out, "group": group, "scope": scope, "limit": limit,
            "distinct": distinct, "truncated": max(0, distinct - len(out))}


def top_ref_terms(conn, limit=15):
    """UTM Term values — this export is the only place they exist."""
    rows = conn.execute(
        "SELECT term, COUNT(*) AS pings, COUNT(DISTINCT global_id) AS people"
        " FROM ref_pings WHERE term <> '' GROUP BY term"
        " ORDER BY pings DESC LIMIT ?", (limit,)).fetchall()
    return [{"term": r["term"], "pings": r["pings"], "people": r["people"]}
            for r in rows]


def subs_by_parent(matrix):
    """{channel: [sub rows]} from a matrix, preserving taxonomy order."""
    out = defaultdict(list)
    for row in matrix["rows"]:
        if not row["is_parent"]:
            out[row["channel"]].append(row)
    return out


def penetration_options(matrix):
    """Selectable entities for the stage-presence chart.

    Parents and sub-sources in one canonical list, each already carrying its
    parent in the display name, so a sub is never an orphaned "PMax".
    """
    parents, subs = [], []
    for row in matrix["rows"]:
        if row["is_parent"]:
            parents.append({"name": row["channel"], "n": row["n"], "is_sub": False})
        else:
            subs.append({"name": taxonomy.sub_name(row["channel"], row["sub_source"]),
                         "parent": row["channel"], "n": row["n"], "is_sub": True})
    parents.sort(key=lambda r: -r["n"])
    subs.sort(key=lambda r: -r["n"])
    return {"parents": parents, "subs": subs}


def penetration_series(program, matrix, selected=None, limit=8, cap=True):
    """Stage-presence series for the chosen channels and/or sub-sources.

    Defaults to the top parents by volume; sub-sources are opt-in, because
    switching them all on at once would blow past the palette and bury the
    parents they belong to. Capped at the palette's 8 slots for the usual
    reason: a ninth colour would have to repeat, and a repeated colour reads as
    the same series.
    """
    by_name = {}
    for row in matrix["rows"]:
        name = (row["channel"] if row["is_parent"]
                else taxonomy.sub_name(row["channel"], row["sub_source"]))
        by_name[name] = row

    if selected is None:
        chosen = [r["channel"] for r in matrix["rows"]
                  if r["is_parent"] and r["channel"] != taxonomy.NO_UTM]
        chosen.sort(key=lambda n: -by_name[n]["n"])
    else:
        chosen = [n for n in selected if n in by_name]

    limit = max(1, min(limit, 8))
    ranked = sorted(chosen, key=lambda n: -by_name[n]["n"])
    if cap:
        kept = ranked[:limit]
        dropped = ranked[limit:]
    else:
        kept = ranked
        dropped = []

    ordered = taxonomy.canonical_order_index(kept)
    vrank = {n: i for i, n in enumerate(ranked)}
    series = []
    for i, name in enumerate(ordered):
        row = by_name[name]
        series.append({
            "name": name,
            "is_sub": not row["is_parent"],
            # See tag_timeline: rank decides which survive, torder the colour.
            "rank": vrank[name],
            "torder": i,
            "colour_index": taxonomy.channel_slot(name),
            "n": row["n"],
            "values": [row["penetration_pct"][k] for k in program.stage_keys],
            "counts": [row["counts"][k] for k in program.stage_keys],
        })
    return {
        "stages": [program.stage_labels[k] for k in program.stage_keys],
        "totals": [matrix["totals"][k] for k in program.stage_keys],
        "series": series, "dropped": dropped, "limit": limit,
    }


def channel_makeup(program, matrix, limit=10, stage=None):
    """Which channels MAKE UP the chosen stage — stage penetration, ranked.

    The sibling of channel_comparison(): that one asks "of the people who
    touched this channel, how many converted?" (quality); this one asks "of the
    people who got in, how many touched this channel?" (presence).

    Two deliberate differences from the quality chart:
      * No volume gate. Penetration is a share of the stage, so a tiny channel
        cannot inflate it — gating would only hide real rows.
      * "No UTM (untracked)" is INCLUDED. It is a genuine and material part of
        the makeup (untracked people do get admitted) and CLAUDE.md is explicit
        that the row is never silently dropped.
    Rows are any-touch and therefore overlap: they sum past 100%.

    `stage` selects which funnel step the question is asked about; it defaults
    to the last one.
    """
    final = stage or program.channel_stage
    total = matrix["totals"][final]
    rows = []
    for row in matrix["rows"]:
        if not row["is_parent"]:
            continue
        if not row["counts"][final]:
            continue
        rows.append({
            "channel": row["channel"],
            "n": row["n"],
            "final_n": row["counts"][final],
            "share": row["penetration_pct"][final],
            "is_no_utm": row["channel"] == taxonomy.NO_UTM,
        })
    rows.sort(key=lambda r: -r["share"])
    kept = rows[:limit]
    return {
        "rows": kept,
        "dropped": len(rows) - len(kept),
        "final_label": program.stage_labels[final],
        "final_total": total,
        "sum_share": sum(r["share"] for r in kept),
    }


def channel_comparison(program, matrix, min_n=25, stage=None):
    """Chart-ready comparison of parent channels on conversion to one stage.

    This is the "what's actually working" view: within-channel conversion to the
    chosen funnel stage (the last one by default), volume-gated so a 1-person
    channel can't top the chart.

    The baseline stays population-wide conversion to that same stage, so the
    dashed line moves with the selection instead of comparing against a
    different question than the bars.
    """
    final = stage or program.channel_stage
    started = "started"
    out = []
    for row in matrix["rows"]:
        if not row["is_parent"]:
            continue
        if row["channel"] == taxonomy.NO_UTM:
            continue
        if row["n"] < min_n:
            continue
        out.append({
            "channel": row["channel"],
            "n": row["n"],
            "final_n": row["counts"][final],
            "rate": row["within_pct"][final],
            "penetration": row["penetration_pct"][final],
        })
    out.sort(key=lambda r: -r["rate"])
    baseline = _pct(matrix["totals"][final], matrix["totals"][started]) \
        if matrix["totals"].get(started) else 0.0
    return {"rows": out, "baseline_rate": baseline, "min_n": min_n,
            "final_label": program.stage_labels[final]}


# --------------------------------------------------------------------------
# media spend
# --------------------------------------------------------------------------
def spend_months(conn, program, flt=None):
    """Which spend months are in scope, and whether the date filter narrowed them.

    Spend arrives monthly, so a filter of "15 Jan - 3 Feb" cannot be honoured
    exactly: splitting a month proportionally would be inventing daily spend.
    Any month the range touches is included WHOLE, and the caller says so on
    screen rather than quietly reporting a number nobody can reproduce.
    """
    all_months = [r["month"] for r in conn.execute(
        "SELECT DISTINCT month FROM spend WHERE program=? AND month <> ''"
        " ORDER BY month", (program.key,))]
    lo = (getattr(flt, "date_from", "") or "")[:7] if flt else ""
    hi = (getattr(flt, "date_to", "") or "")[:7] if flt else ""
    if not (lo or hi):
        return all_months, all_months, False
    kept = [m for m in all_months if (not lo or m >= lo) and (not hi or m <= hi)]
    return kept, all_months, len(kept) != len(all_months)


def paid_reach(program, applicants, flags, pings_by_app, channels):
    """How many DISTINCT people touched any of `channels` and reached each stage.

    This is the honest denominator for a blended any-touch cost. Adding up the
    per-channel any-touch counts would charge anyone who touched both Google and
    Meta twice; a set union counts them once. First touch needs no equivalent —
    it already partitions people, so its rows sum by construction.
    """
    want = set(channels)
    hit = set()
    for i, a in enumerate(applicants):
        for ping in pings_by_app.get(a["id"], []):
            if ping["channel"] in want:
                hit.add(i)
                break
    out = {k: sum(1 for i in hit if flags[i][k]) for k in program.stage_keys}
    out["_touched"] = len(hit)
    return out


def cost_by_channel(conn, program, any_matrix, first_matrix, stage,
                    months=None, include_undated=True, attribution="first",
                    reach=None, last_matrix=None):
    """Spend joined to funnel outcomes, per channel and sub-source.

    `attribution` picks which question the denominators answer:

      * "first" -- credited to the channel that FOUND them. Best for "what did
                   acquiring this person cost".
      * "last"  -- credited to the channel they touched LAST before converting.
                   Best for "what closed them". Like first touch it puts each
                   person in exactly one channel, so it sums the same way.
      * "any"   -- credited to EVERY paid channel they touched. Rows overlap by
                   design and must never be summed: the blended figure comes
                   from `reach`, a set union that counts a Google-and-Meta
                   person once.

    First and last are the two ends of the same journey and will disagree
    whenever a channel is better at starting conversations than finishing them
    (or vice versa) -- that disagreement is the point of having both.

    Either way `cost_per_assist` stays any-touch, so the two lenses are visible
    side by side without switching.

    Channels with spend but no funnel rows still appear, and vice versa: a
    channel burning money with nothing to show is the single most useful thing
    this view can surface, and dropping the row would hide it.
    """
    where = ["program = ?"]
    params = [program.key]
    if months is not None:
        keys = list(months)
        if include_undated:
            keys.append("")     # an export pulled without a month segment
        if not keys:
            where.append("0")
        else:
            where.append("month IN (%s)" % ",".join("?" * len(keys)))
            params.extend(keys)
    rows = conn.execute(
        "SELECT channel, sub_source, SUM(cost) cost, SUM(clicks) clicks,"
        "       SUM(impressions) impressions"
        " FROM spend WHERE %s GROUP BY channel, sub_source" % " AND ".join(where),
        params).fetchall()

    cost = {}
    for r in rows:
        cost[(r["channel"], r["sub_source"])] = {
            "cost": r["cost"] or 0.0, "clicks": r["clicks"] or 0,
            "impressions": r["impressions"] or 0}
    parent_cost = defaultdict(lambda: {"cost": 0.0, "clicks": 0, "impressions": 0})
    for (ch, _sub), v in cost.items():
        p = parent_cost[ch]
        p["cost"] += v["cost"]
        p["clicks"] += v["clicks"]
        p["impressions"] += v["impressions"]

    # First touch for BOTH the start and the deeper-stage denominators. The
    # any-touch counts are kept for the per-row "cost per assist" column only:
    # summing them across channels double-counts everyone who touched two paid
    # channels, which is exactly what a blended figure must not do.
    # "last" falls back to first-touch only if no last-touch matrix was built,
    # which would be a caller bug rather than a supported mode.
    credit = {"first": first_matrix,
              "any": any_matrix,
              "last": last_matrix or first_matrix}[attribution]
    first_by, first_stage = {}, {}
    for r in credit["rows"]:
        key = r["channel"] if r["is_parent"] else (r["channel"], r["sub_source"])
        first_by[key] = r["n"]
        first_stage[key] = r["counts"][stage]

    def _row(name, key, spent, touched, any_reached, first_n, stage_n, is_sub):
        c = spent["cost"]
        return {
            "name": name, "is_sub": is_sub,
            "cost": c, "clicks": spent["clicks"], "impressions": spent["impressions"],
            "first_n": first_n, "n": touched,
            "stage_n": stage_n,          # first touch -> sums
            "any_stage_n": any_reached,  # any touch  -> per row only
            "cost_per_start": (c / first_n) if (c and first_n) else None,
            "cost_per_assist": (c / touched) if (c and touched) else None,
            "cost_per_stage": (c / stage_n) if (c and stage_n) else None,
            "cpc": (c / spent["clicks"]) if spent["clicks"] else None,
        }

    out = []
    seen_parents = set()
    for r in any_matrix["rows"]:
        if r["is_parent"]:
            ch = r["channel"]
            spent = parent_cost.get(ch)
            if not spent:
                continue
            seen_parents.add(ch)
            kids = []
            for s in any_matrix["rows"]:
                if s["is_parent"] or s["channel"] != ch:
                    continue
                sp = cost.get((ch, s["sub_source"]))
                if not sp:
                    continue
                k = (ch, s["sub_source"])
                kids.append(_row(
                    s["sub_source"], k, sp, s["n"], s["counts"][stage],
                    first_by.get(k, 0), first_stage.get(k, 0), True))
            # Spend on a sub-source the funnel never recorded still has to show.
            for (c_ch, sub), sp in cost.items():
                if c_ch != ch or any(k["name"] == sub for k in kids):
                    continue
                kids.append(_row(sub, (ch, sub), sp, 0, 0, 0, 0, True))
            kids.sort(key=lambda k: -k["cost"])
            row = _row(ch, ch, spent, r["n"], r["counts"][stage],
                       first_by.get(ch, 0), first_stage.get(ch, 0), False)
            row["subs"] = kids
            out.append(row)

    for ch, spent in parent_cost.items():
        if ch in seen_parents:
            continue
        row = _row(ch, ch, spent, 0, 0, 0, 0, False)
        row["subs"] = [_row(sub, (c, sub), sp, 0, 0, 0, 0, True)
                       for (c, sub), sp in cost.items() if c == ch]
        out.append(row)

    out.sort(key=lambda r: -r["cost"])
    total_cost = sum(r["cost"] for r in out)
    if attribution != "any":
        total_first = sum(r["first_n"] for r in out)
        total_stage = sum(r["stage_n"] for r in out)
    else:
        # Union, not sum. Falls back to the sum only when no reach was supplied,
        # which would overstate — so callers on this path must pass one.
        total_first = (reach or {}).get("_touched", sum(r["first_n"] for r in out))
        total_stage = (reach or {}).get(stage, sum(r["stage_n"] for r in out))
    return {
        "rows": out,
        "total_cost": total_cost,
        "total_first": total_first,
        # Both blended figures are first-touch. Summing the any-touch columns
        # would double-count everyone who touched two paid channels — with
        # Google and Meta both running, that is a lot of people.
        "blended_per_start": (total_cost / total_first) if total_first else None,
        "stage_total": total_stage,
        "blended_per_stage": (total_cost / total_stage) if total_stage else None,
        "stage_label": program.stage_labels[stage],
        "attribution": attribution,
        "rows_sum": attribution != "any",
    }


def spend_tree(conn, program):
    """Parents with their sub-sources, for the spend picker. Same shape as
    timeline_tree so the one tree macro serves both."""
    rows = conn.execute(
        "SELECT channel, sub_source, SUM(cost) cost FROM spend"
        " WHERE program=? GROUP BY channel, sub_source", (program.key,)).fetchall()
    parents, subs = defaultdict(float), defaultdict(list)
    for r in rows:
        parents[r["channel"]] += r["cost"] or 0.0
        subs[r["channel"]].append(
            {"name": taxonomy.sub_name(r["channel"], r["sub_source"]),
             "label": r["sub_source"], "n": int(round(r["cost"] or 0))})
    out = []
    for ch in taxonomy.canonical_order_index(list(parents)):
        kids = sorted(subs.get(ch, []), key=lambda k: -k["n"])
        out.append({"name": ch, "n": int(round(parents[ch])), "subs": kids})
    return out


def spend_trend(conn, program, months=None, picked=None, cap=True, limit=8):
    """Monthly spend by channel AND sub-source.

    Every entity ships whether or not it is currently shown (cap=False), each
    carrying the same `rank` / `torder` pair the funnel charts use, so the
    browser can re-slice a selection without a round trip and land on exactly
    the colours Python would have chosen.
    """
    rows = conn.execute(
        "SELECT month, channel, sub_source, SUM(cost) cost FROM spend"
        " WHERE program=? AND month <> '' GROUP BY month, channel, sub_source"
        " ORDER BY month", (program.key,)).fetchall()
    keys = sorted({r["month"] for r in rows})
    if months is not None:
        keys = [m for m in keys if m in set(months)]

    by, totals = defaultdict(float), defaultdict(float)
    for r in rows:
        ch, sub = r["channel"], taxonomy.sub_name(r["channel"], r["sub_source"])
        c = r["cost"] or 0.0
        # A parent is the sum of its sub-sources here, unlike the any-touch
        # people charts where a parent cannot be summed from its children.
        by[(r["month"], ch)] += c
        by[(r["month"], sub)] += c
        totals[ch] += c
        totals[sub] += c

    names = list(totals)
    ranked = sorted(names, key=lambda n: -totals[n])
    rank = {n: i for i, n in enumerate(ranked)}
    torder = {n: i for i, n in enumerate(_taxonomy_order(names))}

    if picked is None:
        chosen = [n for n in ranked if taxonomy.SUB_SEP not in n]
    else:
        chosen = [n for n in ranked if n in set(picked)]
    kept, dropped = (chosen[:limit], chosen[limit:]) if cap else (ranked, [])
    kept = sorted(kept, key=lambda n: torder.get(n, 10 ** 6))

    return {
        "labels": keys,
        "series": [{"name": n, "colour_index": taxonomy.channel_slot(n),
                    "rank": rank.get(n, 10 ** 6), "torder": torder.get(n, 10 ** 6),
                    "is_sub": taxonomy.SUB_SEP in n,
                    "data": [by.get((m, n), 0.0) for m in keys],
                    "total": totals[n]}
                   for i, n in enumerate(kept)],
        "dropped": dropped,
        "limit": limit,
        "totals": [sum(by.get((m, n), 0.0) for n in names
                       if taxonomy.SUB_SEP not in n) for m in keys],
    }


def _taxonomy_order(names):
    """Canonical order over a mix of parents and 'Parent > Sub' names."""
    parents = taxonomy.canonical_order_index(
        [n for n in names if taxonomy.SUB_SEP not in n])
    out = []
    for p in parents:
        out.append(p)
        out.extend(sorted(n for n in names
                          if n.startswith(p + taxonomy.SUB_SEP)))
    out.extend(n for n in names if n not in out)
    return out


def cost_stage_payload(conn, program, any_matrix, first_matrix, stage_keys,
                       months=None, reach=None, last_matrix=None):
    """cost_by_channel for every stage AND every attribution, so the stage chips
    and the attribution toggle both redraw in the browser. The matrices are
    already in memory, so this costs one spend query, not stages x lenses."""
    return {a: {k: cost_by_channel(conn, program, any_matrix, first_matrix, k,
                                   months=months, attribution=a, reach=reach,
                                   last_matrix=last_matrix)
                for k in stage_keys}
            for a in ("first", "last", "any")}


def post_submission_touches(program, applicants, flags, pings_by_app):
    """How much of each channel's activity lands AFTER someone already applied.

    Retargeting keeps serving ads to people who have already converted unless
    the ad account explicitly excludes them, and most don't. That matters here
    because "last touch" is supposed to mean "what closed them" -- if a
    channel's last touch usually happens months after submission, it is
    measuring what followed them, not what persuaded them.

    Only applicants with a submitted_date can be judged (you cannot be "after"
    a date that does not exist), so the denominator is the submitted population,
    not everyone. The caller must say so on screen.
    """
    sub_date = {}
    for a in applicants:
        d = (a["submitted_date"] or "")[:10]
        if d:
            sub_date[a["id"]] = d
    if not sub_date:
        return {"rows": [], "submitted": 0, "any_after": 0, "pings_after": 0,
                "pings_total": 0}

    rows = defaultdict(lambda: {"before": 0, "after": 0, "people": set(),
                                "people_after": set(), "gaps": []})
    for aid, when in sub_date.items():
        for p in pings_by_app.get(aid, []):
            if not p["ts"]:
                continue
            key = (p["channel"], p["sub_source"] or "")
            slot = rows[key]
            slot["people"].add(aid)
            if p["ts"][:10] > when:
                slot["after"] += 1
                slot["people_after"].add(aid)
                slot["gaps"].append(_days_between(when, p["ts"][:10]))
            else:
                slot["before"] += 1

    # roll sub-sources up into their parent as well
    parents = defaultdict(lambda: {"before": 0, "after": 0, "people": set(),
                                   "people_after": set(), "gaps": []})
    for (ch, sub), v in rows.items():
        p = parents[ch]
        p["before"] += v["before"]
        p["after"] += v["after"]
        p["people"] |= v["people"]
        p["people_after"] |= v["people_after"]
        p["gaps"].extend(v["gaps"])

    def pack(name, v, is_sub):
        total = v["before"] + v["after"]
        gaps = sorted(v["gaps"])
        return {
            "channel": name, "is_sub": is_sub,
            "before": v["before"], "after": v["after"], "total": total,
            # `rate` is what the chart draws: the share of this channel's
            # touches that arrived after the person had already applied.
            "rate": (v["after"] / total) if total else 0.0,
            "people": len(v["people"]), "people_after": len(v["people_after"]),
            "median_days": gaps[len(gaps) // 2] if gaps else None,
        }

    out = []
    for ch in taxonomy.canonical_order_index(list(parents)):
        row = pack(ch, parents[ch], False)
        subs = [pack(sub, v, True) for (c, sub), v in rows.items()
                if c == ch and sub]
        subs.sort(key=lambda r: -r["after"])
        row["subs"] = subs
        row["hidden_subs"] = 0
        out.append(row)
    out.sort(key=lambda r: -r["after"])

    all_gaps = sorted(g for v in parents.values() for g in v["gaps"])
    people_after = set()
    for v in parents.values():
        people_after |= v["people_after"]
    return {
        "rows": out,
        "submitted": len(sub_date),
        "any_after": len(people_after),
        "pings_after": sum(r["after"] for r in out),
        "pings_total": sum(r["total"] for r in out),
        "median_days": all_gaps[len(all_gaps) // 2] if all_gaps else None,
    }


def _days_between(a, b):
    import datetime as _d
    try:
        da = _d.date(*map(int, a.split("-")))
        db_ = _d.date(*map(int, b.split("-")))
        return (db_ - da).days
    except (ValueError, TypeError):
        return 0
