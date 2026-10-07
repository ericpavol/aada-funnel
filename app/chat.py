"""Ask-the-data chat: Claude answers questions by calling this app's own
metrics through tools.

Privacy boundary (PROJECT.md ground rule 1, as amended 2026-10-07): only
AGGREGATES ever leave the app -- counts, percentages, costs. Every tool below
returns totals computed by the same functions the pages use; none returns an
applicant row, a Global ID, a city, a postcode or any free text from Slate.
`test_chat_tools_never_return_applicant_level_fields` pins that.

Conversation shape: within one question the message list is append-only (the
model's own response content, thinking blocks included, is appended verbatim
before each tool_result turn). Earlier questions come back as plain text
question/answer pairs with no thinking blocks -- a consistent prefix for every
request in the turn, so no reasoning block is ever replayed against a history
it was not produced from.
"""
import datetime as _dt
import json
import os

from . import filters, metrics, programs

MODEL = "claude-opus-5-5"
MAX_TOOL_ROUNDS = 8
MAX_HISTORY_TURNS = 10

SYSTEM = """You answer questions about the American Academy of Dramatic Arts (AADA) \
applicant funnel for the marketing team, using only numbers you get from the tools.

How the data works -- get these right:
- Two programs, never combined: "ft" (Full-Time: AOS 2-year and BFA 3-/4-year) and \
"summer". Funnel stages, Full-Time: started > submitted > aud_req (audition requested) \
> aud_comp (audition complete) > admitted > enrolled. Summer: started > submitted > accepted.
- Fiscal year runs 1 Sept to 31 Aug and is named by its start year (2026 = FY 2026/27). \
Date filters are on application start date.
- "Data through" is the last day in the uploaded Slate export. Never describe days after it.
- Channel attribution: "any" touch means everyone a channel touched -- these OVERLAP, \
never add them up. "first" and "last" credit each person to one channel and do add up. \
Last touch is not "what closed them": retargeting (Google PMax especially) keeps tagging \
people after they apply.
- Year over year compares the same point in the year. Only stages with a date can be \
compared (started, submitted, and enrolled via term start); audition and admitted stages \
come back not comparable -- say so, never invent a last-year figure for them.
- Cost per stage = paid spend / people reaching the stage (first touch by default). Check \
the spend coverage the tool returns: if a platform's spend ends before the Slate data does, \
its cost per stage is understated -- say so.
- Changes in conversion rates are percentage points, not percent.
- "No UTM (untracked)" is real people with no tracking, not a channel.

How to answer:
- Call tools for every number. If a question is ambiguous (which program, which year), \
assume the view the user is on, which is given with each question, and say what you used.
- Lead with the direct answer in a sentence or two, then the supporting numbers. Keep it \
short; use a small table only when comparing several things. Plain language.
- Say which program, fiscal year and filters the numbers are for, and the data-through date.
- If the tools cannot answer it, say what is missing rather than guessing."""


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

_FILTER_PROPS = {
    "program": {"type": "string", "enum": ["ft", "summer"],
                "description": "ft = Full-Time, summer = Summer. Defaults to the page's program."},
    "fiscal_year": {"type": "string",
                    "description": "Start year of the fiscal year, e.g. \"2026\" for FY 2026/27, "
                                   "or \"all\" for all time. Defaults to the page's date range."},
    "date_from": {"type": "string", "description": "YYYY-MM-DD application start date, inclusive. Overrides fiscal_year."},
    "date_to": {"type": "string", "description": "YYYY-MM-DD application start date, inclusive."},
    "terms": {"type": "array", "items": {"type": "string"}, "description": "Intake term labels, exactly as list_options returns them."},
    "degrees": {"type": "array", "items": {"type": "string", "enum": ["AOS", "BFA"]}},
    "bfa_pathways": {"type": "array", "items": {"type": "string"}, "description": "e.g. \"3-year\", \"4-year\"."},
    "emphases": {"type": "array", "items": {"type": "string"}},
    "regions": {"type": "array", "items": {"type": "string"}},
    "countries": {"type": "array", "items": {"type": "string"}},
    "age_bands": {"type": "array", "items": {"type": "string",
                  "enum": ["under18", "18_20", "21_24", "25_29", "30plus", "unknown"]}},
    "channels_touched": {"type": "array", "items": {"type": "string"},
                         "description": "Only people who touched one of these channels (any touch)."},
}


def _tool(name, description, extra=None, required=None):
    props = dict(_FILTER_PROPS)
    props.update(extra or {})
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props,
                             "required": required or []}}


_STAGE = {"type": "string",
          "description": "Funnel stage key: started, submitted, aud_req, aud_comp, admitted, "
                         "enrolled (Full-Time) or started, submitted, accepted (Summer)."}
_ATTR = {"type": "string", "enum": ["first", "last", "any"],
         "description": "Channel credit: first or last touch (add up) or any touch (overlaps)."}

TOOLS = [
    {"name": "list_options",
     "description": "What can be filtered: fiscal years with data, the data-through date, "
                    "intake terms, degrees, BFA pathways, emphases, top regions and "
                    "countries, channels. Call this before filtering by a label you have "
                    "not seen.",
     "input_schema": {"type": "object", "properties": {"program": _FILTER_PROPS["program"]},
                      "required": []}},
    _tool("funnel",
          "Overall funnel for the filtered applicants: count and share of started at each "
          "stage, plus AOS/BFA split for Full-Time."),
    _tool("channels",
          "Per marketing channel: people credited, how many reached each stage, and "
          "conversion within the channel. Parent channels by default.",
          {"attribution": _ATTR,
           "include_sub_sources": {"type": "boolean",
                                   "description": "Also return sub-sources such as Google (Paid) > PMax."}}),
    _tool("year_over_year",
          "This fiscal year against the same point last year: stage counts and conversion, "
          "whether each stage is comparable, cumulative pace, and per-channel reach for a "
          "stage. Needs a fiscal year, not all time.",
          {"stage": _STAGE}),
    _tool("cost",
          "Paid media cost per stage by channel: spend, people credited, people reaching the "
          "stage, cost per stage, plus where each platform's spend data ends.",
          {"stage": _STAGE, "attribution": _ATTR}),
]


def _r(x, n=4):
    return round(x, n) if isinstance(x, float) else x


def _resolve_filters(conn, args, page):
    """Tool arguments (falling back to the page the user is on) -> Filters."""
    program = programs.get(args.get("program") or page.get("program") or "ft")
    fy = args.get("fiscal_year")
    lo, hi = args.get("date_from"), args.get("date_to")
    if not (lo or hi):
        if fy is None:
            fy = page.get("fiscal_year")
        if fy is None:
            fy = filters.default_fiscal_year(conn, program)
        if str(fy).lower() != "all" and str(fy).strip():
            lo, hi = filters.fiscal_range(int(str(fy)[:4]))
    flt = filters.Filters(
        program,
        terms=args.get("terms"), regions=args.get("regions"),
        countries=args.get("countries"), emphases=args.get("emphases"),
        degrees=args.get("degrees"), pathways=args.get("bfa_pathways"),
        age_bands=args.get("age_bands"), channels=args.get("channels_touched"),
        date_field=filters.DEFAULT_DATE_FIELD if (lo or hi) else None,
        date_from=lo, date_to=hi,
    )
    return program, flt


def _scope(conn, program, flt):
    from .main import _data_through
    return {"program": program.label,
            "date_range": [flt.date_from or None, flt.date_to or None] if flt.date_field else "all time",
            "filters": flt.summary() or "none",
            "data_through": _data_through(conn, program, flt),
            "applicants_in_scope": conn.execute(
                "SELECT COUNT(*) FROM applicants WHERE " + flt.where, flt.params).fetchone()[0]}


def _population(conn, program, flt):
    apps, flags = metrics.load_population(conn, program, flt.where, flt.params)
    return apps, flags


def tool_list_options(conn, args, page):
    program = programs.get(args.get("program") or page.get("program") or "ft")
    f = filters.facet_values(conn, program)
    from .main import _data_through
    years = [r[0] for r in conn.execute(
        "SELECT DISTINCT CASE WHEN CAST(substr(started_date,6,2) AS INTEGER) >= 9"
        " THEN CAST(substr(started_date,1,4) AS INTEGER)"
        " ELSE CAST(substr(started_date,1,4) AS INTEGER) - 1 END"
        " FROM applicants WHERE program=? AND started_date <> '' ORDER BY 1 DESC",
        (program.key,))]
    def vals(pairs, top=None):
        out = [v for v, _n in pairs if v != filters.NONE_TOKEN]
        return out[:top] if top else out
    return {"program": program.label, "fiscal_years_with_data": years,
            "data_through": _data_through(conn, program, filters.Filters(program)),
            "stages": program.stage_keys,
            "comparable_year_over_year_stages": list(program.stage_dates),
            "terms": vals(f["terms"]), "degrees": vals(f["degrees"]),
            "bfa_pathways": vals(f["pathways"]), "emphases": vals(f["emphases"]),
            "regions_top": vals(f["regions"], 25), "countries_top": vals(f["countries"], 15),
            "channels": vals(f["channels"])}


def tool_funnel(conn, args, page):
    program, flt = _resolve_filters(conn, args, page)
    apps, flags = _population(conn, program, flt)
    o = metrics.overall_funnel(program, flags)
    out = {"scope": _scope(conn, program, flt),
           "stages": [{"stage": s["key"], "label": s["label"], "people": s["n"],
                       "share_of_started": _r(s["pct_of_started"]),
                       "share_of_previous_stage": _r(s["pct_of_prev"])} for s in o["steps"]]}
    if program.has_degree:
        out["by_degree"] = [{"degree": g["name"], "people_by_stage": g["counts"]}
                            for g in metrics.funnel_by(program, apps, flags, "degree",
                                                       order=[programs.AOS, programs.BFA])]
        out["note"] = "Rows with no degree selected yet are not in by_degree."
    return out


def tool_channels(conn, args, page):
    program, flt = _resolve_filters(conn, args, page)
    attr = args.get("attribution") or "any"
    if attr not in ("first", "last", "any"):
        attr = "any"
    apps, flags = _population(conn, program, flt)
    pings = metrics.load_pings(conn, [a["id"] for a in apps])
    m = metrics.build_matrix(program, apps, flags, pings, attr)
    subs = bool(args.get("include_sub_sources"))
    rows = []
    for r in m["rows"]:
        if not r["is_parent"] and not subs:
            continue
        rows.append({"channel": r["channel"] + ("" if r["is_parent"] else " > " + r["sub_source"]),
                     "people": r["n"], "reached": r["counts"],
                     "conversion_within_channel": {k: _r(v) for k, v in r["within_pct"].items()},
                     "share_of_stage": {k: _r(v) for k, v in r["penetration_pct"].items()}})
    return {"scope": _scope(conn, program, flt), "attribution": attr,
            "rows_overlap_and_must_not_be_summed": attr == "any",
            "totals_by_stage": m["totals"], "channels": rows}


def tool_year_over_year(conn, args, page):
    program, flt = _resolve_filters(conn, args, page)
    if not flt.date_from:
        return {"error": "Year over year needs a fiscal year, not all time."}
    y = metrics.yoy_funnel(conn, program, flt, _dt.date.today().isoformat())
    if not y:
        return {"error": "No data in that period."}
    stage = args.get("stage") or program.stage_keys[0]
    if stage not in program.stage_dates:
        stage_note = "%s has no date, so pace/channels use started instead." % stage
        stage = program.stage_keys[0]
    else:
        stage_note = None
    pace = metrics.yoy_pace(conn, program, flt, y["current"], y["prior"], y["field"], stage=stage)
    paid = [r[0] for r in conn.execute(
        "SELECT DISTINCT channel FROM spend WHERE program=?", (program.key,))]
    chans = metrics.yoy_channels(conn, program, flt, y["current"], y["prior"], y["field"],
                                 stage=stage, paid=paid)
    base_c = y["rows"][0]["current"] or 0
    base_p = y["rows"][0]["prior"] or 0
    stages = []
    for r in y["rows"]:
        row = {"stage": r["key"], "comparable": r["comparable"], "this_year": r["current"]}
        if r["comparable"]:
            row.update({"last_year_same_point": r["prior"],
                        "change_in_people": _r(r["delta"]) if r["delta"] is not None else None,
                        "conversion_this_year": _r(r["current"] / base_c) if base_c else None,
                        "conversion_last_year": _r(r["prior"] / base_p) if base_p else None})
        else:
            row["last_year_same_point"] = "unknown: Slate sends no date for this stage"
        stages.append(row)
    return {"scope": _scope(conn, program, flt),
            "this_year_window": y["current"], "last_year_window": y["prior"],
            "prior_year_data_is_thin": y["prior_thin"], "stages": stages,
            "pace_stage": stage, "pace_note": stage_note,
            "pace_final": {"this_year": pace["current_final"], "last_year": pace["prior_final"]},
            "channels_reaching_stage": [
                {"channel": c["channel"], "this_year": c["current"], "last_year": c["prior"],
                 "change": _r(c["delta"]) if c["delta"] is not None else None, "paid": c["paid"]}
                for c in chans],
            "channels_note": "Any touch: channels overlap; the honest total is the stage row."}


def tool_cost(conn, args, page):
    from .main import _cost_view, _paid_reach
    program, flt = _resolve_filters(conn, args, page)
    stage = args.get("stage") or program.stage_keys[0]
    if stage not in program.stage_keys:
        stage = program.stage_keys[0]
    attr = args.get("attribution") or "first"
    if attr not in ("first", "last", "any"):
        attr = "first"
    apps, flags = _population(conn, program, flt)
    pings = metrics.load_pings(conn, [a["id"] for a in apps])
    anym = metrics.build_matrix(program, apps, flags, pings, "any")
    first = metrics.build_matrix(program, apps, flags, pings, "first")
    last = metrics.build_matrix(program, apps, flags, pings, "last")
    reach = _paid_reach(conn, program, apps, flags, pings)
    c = _cost_view(conn, program, flt, anym, first, stage, attribution=attr,
                   reach=reach, last_matrix=last)
    return {"scope": _scope(conn, program, flt), "stage": stage, "attribution": attr,
            "has_spend_in_period": c["has_spend"],
            "spend_months_used": c["months"],
            "spend_coverage": c["coverage"],
            "total_paid_spend": _r(c["total_cost"], 2),
            "blended_cost_per_stage": _r(c.get("blended_per_stage"), 2),
            "people_reaching_stage": c.get("stage_total"),
            "channels": [{"channel": r["name"], "spend": _r(r["cost"], 2),
                          "people_reaching_stage": r["stage_n"],
                          "cost_per_stage": _r(r["cost_per_stage"], 2),
                          "cost_per_click": _r(r["cpc"], 2)} for r in c["rows"]]}


EXECUTORS = {"list_options": tool_list_options, "funnel": tool_funnel,
             "channels": tool_channels, "year_over_year": tool_year_over_year,
             "cost": tool_cost}

TOOL_LABELS = {"list_options": "filter options", "funnel": "funnel",
               "channels": "channels", "year_over_year": "year over year",
               "cost": "media cost"}


def run_tool(conn, name, args, page):
    """-> (json text, is_error). Never raises: a failure becomes a tool error
    the model can read and recover from."""
    fn = EXECUTORS.get(name)
    if fn is None:
        return json.dumps({"error": "Unknown tool %r" % name}), True
    if not isinstance(args, dict):
        return json.dumps({"error": "Tool input must be an object."}), True
    try:
        out = fn(conn, args, page)
        return json.dumps(out, default=str), "error" in out
    except (KeyError, ValueError, TypeError) as exc:
        return json.dumps({"error": "Could not run %s: %s" % (name, exc)}), True


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

def enabled():
    """Whether the chat exists at all on this server.

    Off on the hosted site by default (Eric, 2026-10-07: chat stays on dev, not
    live) -- Render sets RENDER=true on every service. On anywhere else, i.e.
    local dev. AADA_CHAT=1 switches it on regardless; AADA_CHAT=0 off. Kept as
    a switch rather than a branch so dev -> main merges stay safe.
    """
    flag = os.environ.get("AADA_CHAT")
    if flag in ("0", "1"):
        return flag == "1"
    return not os.environ.get("RENDER")


def configured():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def page_context(conn, page):
    program = programs.get(page.get("program") or "ft")
    fy = page.get("fiscal_year")
    if fy is None:
        fy = filters.default_fiscal_year(conn, program)
    view = "FY %s/%02d" % (fy, (int(fy) + 1) % 100) if str(fy).isdigit() else "all time"
    return ("[Context: today is %s. The user is viewing %s, %s%s.]"
            % (_dt.date.today().isoformat(), program.label, view,
               ("; page filters: " + page["summary"]) if page.get("summary") else ""))


def answer(conn, question, history, page, client=None):
    """One question -> {"answer", "tools", "user_sent"}.

    `history` is a list of {"role": "user"|"assistant", "text": ...} from
    earlier questions; `user_sent` is the exact user text sent this turn, which
    the browser stores and replays verbatim next time so the prefix never moves.
    """
    import anthropic
    client = client or anthropic.Anthropic()

    messages = []
    for h in (history or [])[-2 * MAX_HISTORY_TURNS:]:
        role = h.get("role")
        text = (h.get("text") or "").strip()
        if role in ("user", "assistant") and text:
            messages.append({"role": role, "content": text})
    # Must alternate and start with the user; drop anything that would not.
    cleaned = []
    for m in messages:
        if (not cleaned and m["role"] != "user") or (cleaned and cleaned[-1]["role"] == m["role"]):
            continue
        cleaned.append(m)
    if cleaned and cleaned[-1]["role"] != "assistant":
        cleaned.pop()

    user_sent = page_context(conn, page) + "\n\n" + question.strip()
    messages = cleaned + [{"role": "user", "content": user_sent}]
    used = []

    for _round in range(MAX_TOOL_ROUNDS + 1):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            tools=TOOLS,
            messages=messages,
            output_config={"effort": "medium"},
            cache_control={"type": "ephemeral"},
            # Server-side fallback on a policy decline: the API re-runs the same
            # request on a suitable model inside this call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            return {"answer": "I can't answer that one. Try rephrasing the question.",
                    "tools": used, "user_sent": user_sent}
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if response.stop_reason == "tool_use" and _round < MAX_TOOL_ROUNDS:
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for b in response.content:
                if b.type != "tool_use":
                    continue
                out, err = run_tool(conn, b.name, b.input, page)
                label = TOOL_LABELS.get(b.name, b.name)
                if label not in used:
                    used.append(label)
                block = {"type": "tool_result", "tool_use_id": b.id, "content": out}
                if err:
                    block["is_error"] = True
                results.append(block)
            messages.append({"role": "user", "content": results})
            continue
        if response.stop_reason == "pause_turn" and _round < MAX_TOOL_ROUNDS:
            messages.append({"role": "assistant", "content": response.content})
            continue
        if response.stop_reason == "max_tokens":
            text = (text + "\n\n(The answer was cut off - try a narrower question.)").strip()
        return {"answer": text or "I couldn't put an answer together. Try rephrasing.",
                "tools": used, "user_sent": user_sent}
    return {"answer": "That needed more lookups than I allow per question. Try a narrower question.",
            "tools": used, "user_sent": user_sent}
