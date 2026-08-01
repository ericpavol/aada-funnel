"""Filter bar -> SQL WHERE clause over `applicants`.

Every table and chart recomputes against the filtered population, so all
filtering funnels through build_where().
"""
# Sentinel for "this field is blank". A fifth to a half of rows have no term,
# region, country or programme, and before this they were unreachable: the facet
# lists only offered non-empty values, so filtering ANY of those dimensions
# silently dropped them with no way to see or select them.
NONE_TOKEN = "__none__"

NONE_LABELS = {
    "term": "(no term)", "region": "(no region)", "country": "(no country)",
    "emphasis": "(no programme)", "channel": "(no UTM \u2014 untracked)",
}

AGE_BANDS = [
    ("under18", "Under 18", None, 17),
    ("18_20", "18-20", 18, 20),
    ("21_24", "21-24", 21, 24),
    ("25_29", "25-29", 25, 29),
    ("30plus", "30+", 30, None),
    ("unknown", "Age unknown", None, None),
]
_BANDS = {b[0]: b for b in AGE_BANDS}


def _disp(key, values):
    """Swap the sentinel for its human label so `__none__` never reaches the UI."""
    label = NONE_LABELS.get(key, "(none)")
    return [label if v == NONE_TOKEN else v for v in values]


# Fiscal year runs 1 Sept -> 31 Aug, matching how AADA plans an intake year.
FISCAL_START_MONTH = 9
ALL_TIME = "all"
DEFAULT_DATE_FIELD = "started_date"


def fiscal_range(fy):
    """-> ('YYYY-09-01', 'YYYY+1-08-31') for the fiscal year starting in `fy`."""
    return ("%d-09-01" % fy, "%d-08-31" % (fy + 1))


def fiscal_year_of(date_str):
    if not date_str or len(date_str) < 7:
        return None
    y, m = int(date_str[:4]), int(date_str[5:7])
    return y if m >= FISCAL_START_MONTH else y - 1


def default_fiscal_year(conn, program):
    """The newest fiscal year that actually has applications.

    Deliberately data-driven rather than "today's" fiscal year: the app is used
    against exports that lag, and a default range with nothing in it would open
    on an empty page.
    """
    row = conn.execute(
        "SELECT MAX(started_date) d FROM applicants WHERE program=? AND started_date <> ''",
        (program.key,)).fetchone()
    return fiscal_year_of(row["d"] if row else None)


class Filters:
    """Parsed filter state. `.where` / `.params` plug straight into a query."""

    def __init__(self, program, terms=None, regions=None, countries=None,
                 emphases=None, age_bands=None, date_field=None,
                 date_from=None, date_to=None, channels=None, stage=None):
        self.program = program
        self.terms = [t for t in (terms or []) if t]
        self.regions = [r for r in (regions or []) if r]
        self.countries = [c for c in (countries or []) if c]
        self.emphases = [e for e in (emphases or []) if e]
        self.age_bands = [a for a in (age_bands or []) if a in _BANDS]
        self.date_field = date_field if date_field in program.date_fields else None
        self.date_from = (date_from or "").strip()
        self.date_to = (date_to or "").strip()
        self.channels = [c for c in (channels or []) if c]
        self.stage = stage if stage in program.stage_keys else None
        self.where, self.params = self._build()

    def _build(self):
        clauses = ["program = ?"]
        params = [self.program.key]

        def inlist(col, vals):
            """`col IN (...)`, plus `col = ''` when "(no value)" is selected."""
            real = [v for v in vals if v != NONE_TOKEN]
            parts = []
            if real:
                parts.append("%s IN (%s)" % (col, ",".join("?" * len(real))))
                params.extend(real)
            if len(real) != len(vals):
                parts.append("%s = ''" % col)
            clauses.append("(" + " OR ".join(parts) + ")")

        if self.terms:
            inlist("term", self.terms)
        if self.regions:
            inlist("region", self.regions)
        if self.countries:
            inlist("country", self.countries)
        if self.emphases:
            inlist("emphasis", self.emphases)

        if self.age_bands:
            parts = []
            for key in self.age_bands:
                _, _, lo, hi = _BANDS[key]
                if key == "unknown":
                    parts.append("age IS NULL")
                elif lo is None:
                    parts.append("(age IS NOT NULL AND age <= ?)")
                    params.append(hi)
                elif hi is None:
                    parts.append("(age IS NOT NULL AND age >= ?)")
                    params.append(lo)
                else:
                    parts.append("(age BETWEEN ? AND ?)")
                    params.extend([lo, hi])
            clauses.append("(" + " OR ".join(parts) + ")")

        if self.date_field and (self.date_from or self.date_to):
            col = self.date_field
            clauses.append("%s <> ''" % col)
            if self.date_from:
                clauses.append("%s >= ?" % col)
                params.append(self.date_from)
            if self.date_to:
                clauses.append("%s <= ?" % col)
                params.append(self.date_to)

        if self.stage:
            clauses.append("st_%s = 1" % self.stage)

        if self.channels:
            # "touched at least one of these channels" (any-touch semantics).
            # "(no UTM)" is not a stored channel — those applicants have no ping
            # rows at all — so it becomes a NOT EXISTS instead of an IN.
            real = [c for c in self.channels if c != NONE_TOKEN]
            parts = []
            if real:
                parts.append(
                    "id IN (SELECT applicant_id FROM pings WHERE channel IN (%s))"
                    % ",".join("?" * len(real)))
                params.extend(real)
            if len(real) != len(self.channels):
                parts.append(
                    "NOT EXISTS (SELECT 1 FROM pings WHERE applicant_id = applicants.id)")
            clauses.append("(" + " OR ".join(parts) + ")")

        return " AND ".join(clauses), params

    @property
    def active(self):
        return bool(self.terms or self.regions or self.countries or self.emphases
                    or self.age_bands or self.channels or self.stage
                    or (self.date_field and (self.date_from or self.date_to)))

    def summary(self):
        bits = []
        if self.terms:
            bits.append("Term: " + ", ".join(_disp("term", self.terms)))
        if self.regions:
            bits.append("Region: " + ", ".join(_disp("region", self.regions)))
        if self.countries:
            bits.append("Country: " + ", ".join(_disp("country", self.countries)))
        if self.emphases:
            bits.append("Program: " + ", ".join(_disp("emphasis", self.emphases)))
        if self.age_bands:
            bits.append("Age: " + ", ".join(_BANDS[a][1] for a in self.age_bands))
        if self.date_field and (self.date_from or self.date_to):
            bits.append("%s: %s to %s" % (
                self.program.date_fields[self.date_field],
                self.date_from or "any", self.date_to or "any"))
        if self.channels:
            bits.append("Touched: " + ", ".join(_disp("channel", self.channels)))
        if self.stage:
            bits.append("Reached: " + self.program.stage_labels[self.stage])
        return " · ".join(bits)

    def tokens(self):
        """One token per ACTIVE dimension for the filter bar.

        Grouped by dimension rather than one token per value, so "Region ·
        CA, NY, TX" is a single removable unit. `key` is what the remove link
        and the picker use to identify the dimension.
        """
        out = []

        def add(key, label, values):
            # "display" rather than "values": in Jinja, `token.values` resolves to
            # dict.values (the method) before the item, which silently breaks.
            if values:
                out.append({"key": key, "label": label, "display": list(values)})

        add("term", "Term", _disp("term", self.terms))
        add("region", "Region", _disp("region", self.regions))
        add("country", "Country", _disp("country", self.countries))
        add("emphasis", "Program", _disp("emphasis", self.emphases))
        add("channel", "Channel", _disp("channel", self.channels))
        add("age", "Age", [_BANDS[a][1] for a in self.age_bands])
        if self.date_field and (self.date_from or self.date_to):
            out.append({
                "key": "date",
                "label": self.program.date_fields[self.date_field],
                "display": ["%s → %s" % (self.date_from or "any", self.date_to or "any")],
            })
        if self.stage:
            add("stage", "Reached", [self.program.stage_labels[self.stage]])
        return out

    def query_dict(self):
        """Round-trip the filter state back into query params (for links)."""
        d = {"program": self.program.key}
        for name, vals in (("term", self.terms), ("region", self.regions),
                           ("country", self.countries), ("emphasis", self.emphases),
                           ("age", self.age_bands), ("channel", self.channels)):
            if vals:
                d[name] = list(vals)
        if self.stage:
            d["stage"] = self.stage
        if self.date_field:
            d["date_field"] = self.date_field
        if self.date_from:
            d["date_from"] = self.date_from
        if self.date_to:
            d["date_to"] = self.date_to
        return d


def facet_values(conn, program):
    """Distinct filter options actually present in the data, for the filter bar."""
    def col(name, alpha=False):
        """Distinct values for one column, plus a "(no value)" bucket.

        `alpha` sorts A-Z instead of by volume. Term uses it: there are only a
        handful of terms and they are a known list, so a stable alphabetical
        order is easier to scan than one that reshuffles as intake changes.
        High-cardinality columns (80 regions) stay volume-ranked, because there
        the useful ones are the big ones and the picker has a search box.
        """
        rows = conn.execute(
            "SELECT %s AS v, COUNT(*) AS n FROM applicants WHERE program=? AND %s<>''"
            " GROUP BY %s" % (name, name, name),
            (program.key,),
        ).fetchall()
        out = [(r["v"], r["n"]) for r in rows]
        if alpha:
            out.sort(key=lambda kv: str(kv[0]).lower())
        else:
            out.sort(key=lambda kv: (-kv[1], str(kv[0])))
        blank = conn.execute(
            "SELECT COUNT(*) FROM applicants WHERE program=? AND %s=''" % name,
            (program.key,)).fetchone()[0]
        if blank:
            # Always last: it is a catch-all, not a value, so it should not sit
            # in the middle of an A-Z run or above real terms in a ranked one.
            out.append((NONE_TOKEN, blank))
        return out

    channels = conn.execute(
        "SELECT p.channel AS v, COUNT(DISTINCT p.applicant_id) AS n"
        " FROM pings p JOIN applicants a ON a.id = p.applicant_id"
        " WHERE a.program=? AND p.channel<>''"
        " GROUP BY p.channel ORDER BY n DESC", (program.key,),
    ).fetchall()

    channel_list = [(r["v"], r["n"]) for r in channels]
    untracked = conn.execute(
        "SELECT COUNT(*) FROM applicants a WHERE a.program=?"
        " AND NOT EXISTS (SELECT 1 FROM pings p WHERE p.applicant_id = a.id)",
        (program.key,)).fetchone()[0]
    if untracked:
        channel_list.append((NONE_TOKEN, untracked))

    span = conn.execute(
        "SELECT MIN(started_date) AS lo, MAX(started_date) AS hi FROM applicants"
        " WHERE program=? AND started_date<>''", (program.key,),
    ).fetchone()

    return {
        "terms": col("term", alpha=True),
        "regions": col("region"),
        "countries": col("country"),
        "emphases": col("emphasis"),
        "channels": channel_list,
        "age_bands": AGE_BANDS,
        "date_fields": program.date_fields,
        "date_span": (span["lo"] or "", span["hi"] or "") if span else ("", ""),
    }


# Which query params belong to each dimension. Removing a dimension drops all
# of them, which is why `date` is a list of three.
DIMENSION_PARAMS = {
    "term": ["term"], "region": ["region"], "country": ["country"],
    "emphasis": ["emphasis"], "channel": ["channel"], "age": ["age"],
    "stage": ["stage"], "date": ["date_field", "date_from", "date_to"],
}


def describe(program, facets, flt):
    """JSON-serialisable description of every dimension + current selection.

    Consumed by the token-bar picker (static/filters.js). Values carry their
    applicant counts so the picker can show "England · 484" and sort by volume.
    """
    def vals(pairs, key):
        label = NONE_LABELS.get(key, "(none)")
        return [{"v": v, "label": label if v == NONE_TOKEN else v, "n": n}
                for v, n in pairs]

    dims = [
        {"key": "term", "label": "Term", "param": "term", "type": "multi",
         "values": vals(facets["terms"], "term"), "selected": flt.terms},
        {"key": "region", "label": "Region", "param": "region", "type": "multi",
         "values": vals(facets["regions"], "region"), "selected": flt.regions},
        {"key": "emphasis", "label": "Program emphasis", "param": "emphasis",
         "type": "multi", "values": vals(facets["emphases"], "emphasis"), "selected": flt.emphases},
        {"key": "channel", "label": "Channel touched", "param": "channel",
         "type": "multi", "values": vals(facets["channels"], "channel"), "selected": flt.channels},
        {"key": "age", "label": "Age band", "param": "age", "type": "multi",
         "values": [{"v": k, "label": lbl, "n": None} for k, lbl, _, _ in AGE_BANDS],
         "selected": flt.age_bands},
        {"key": "date", "label": "Date range", "type": "date",
         "fields": [{"v": k, "label": lbl} for k, lbl in program.date_fields.items()],
         "span": list(facets["date_span"]),
         "selected": {"field": flt.date_field or "",
                      "from": flt.date_from, "to": flt.date_to}},
        {"key": "stage", "label": "Reached stage", "param": "stage", "type": "single",
         "values": [{"v": k, "label": program.stage_labels[k], "n": None}
                    for k in program.stage_keys],
         "selected": [flt.stage] if flt.stage else []},
    ]
    if facets["countries"]:
        dims.insert(2, {"key": "country", "label": "Country", "param": "country",
                        "type": "multi", "values": vals(facets["countries"], "country"),
                        "selected": flt.countries})
    return {"dimensions": dims, "program": program.key}


def from_query(program, params, default_fy=None):
    """Build Filters from a Starlette QueryParams-like multi-dict.

    A date range is applied BY DEFAULT — application start date across the
    current fiscal year — because almost every question here is asked about one
    intake year, and the unfiltered view silently mixes several. The default is
    visible in the bar and one click from "All time", so it is a starting point
    rather than a hidden filter:

        (no date params)   -> the default fiscal year
        dates=all          -> no date filter at all
        dates=2024         -> that fiscal year
        explicit from/to   -> exactly that, untouched
    """
    getall = getattr(params, "getlist", None) or (lambda k: params.getall(k))
    field = params.get("date_field")
    lo, hi = params.get("date_from"), params.get("date_to")
    dates = params.get("dates")

    if lo or hi:
        field = field or DEFAULT_DATE_FIELD
    elif dates == ALL_TIME:
        field = lo = hi = None
    else:
        fy = None
        if dates and dates.isdigit():
            fy = int(dates)
        elif default_fy is not None:
            fy = default_fy
        if fy is not None:
            field = field or DEFAULT_DATE_FIELD
            lo, hi = fiscal_range(fy)

    return Filters(
        program,
        terms=getall("term"), regions=getall("region"),
        countries=getall("country"), emphases=getall("emphasis"),
        age_bands=getall("age"), channels=getall("channel"),
        date_field=field, date_from=lo, date_to=hi,
        stage=params.get("stage"),
    )
