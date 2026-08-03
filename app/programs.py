"""Per-program configuration: column layout, funnel stages, labels.

Column indices are 0-based and come from DATA_SCHEMA.md. Full-Time and Summer
are analysed SEPARATELY and never merged (CLAUDE.md golden rule).
"""
import re

from .analysis_engine import ft_stages, summer_stages


def _s(v):
    return "" if v is None else str(v).strip()


# A flag column counts as "set" when it holds anything that is not an explicit
# negative. That way one test covers both shapes Slate might send: a Y/N flag,
# and a date column ("Enrolled Date") whose mere presence is the signal.
_NEGATIVE = {"", "n", "no", "0", "false", "none", "null"}


def _flag(v):
    return _s(v).lower() not in _NEGATIVE


# Header text that means "this person enrolled". Checked as a normalised
# substring so "Enrolled", "Enrolled Date", and "Matriculated" all match, while
# "Most Recent Decision" does not.
ENROLLED_HINTS = ("enrol", "matriculat")


def enrolled_index(headers):
    """Index of a dedicated enrolled column, or None if the export has none.

    Until Slate sends one, `enrolled` is read off Most Recent Decision. Once a
    real column is appended, this finds it and nothing else has to change —
    which is why the stage is being added now rather than waiting for the pull.
    """
    for i, h in enumerate(headers):
        low = _s(h).lower()
        if any(hint in low for hint in ENROLLED_HINTS):
            return i
    return None


def _ft_enrolled(row, idx):
    """Enrolled for Full-Time.

    Dedicated column when the export has one; otherwise Most Recent Decision
    (col 9) == "Enrolled", which is where Slate records it today. In the July
    2026 export that is 16 people, all Winter 2026 — every one of them also
    flagged Admitted, so the stage nests inside the one above it.
    """
    if idx is not None and idx < len(row):
        return _flag(row[idx])
    return _s(row[9]).lower() == "enrolled"


# AADA is renaming the January intake from "Winter" to "Spring". Slate's
# 2026-08-03 export already uses the new label for 2027 ("January 2027 (Spring)")
# while still carrying the old one for 2026 ("Winter 2026 (January 2026)"), so
# both spellings are live in the same file. Left alone they would split one
# intake into two filter options and two dedup keys.
#
# Canonical form is the NEW naming, since that is where AADA is heading.
_WINTER_RE = re.compile(r"^winter\s+(\d{4})\s*\(january\s+(\d{4})\)$", re.I)
_SPRING_RE = re.compile(r"^january\s+(\d{4})\s*\(spring\)$", re.I)


def canonical_term(term):
    """Normalise a term label so one intake has exactly one name.

    "Winter 2027 (January 2027)" and "January 2027 (Spring)" are the same
    intake under two labels; both become "January 2027 (Spring)". Anything
    that matches neither pattern is returned untouched -- this only collapses
    the rename, it does not try to tidy terms generally.
    """
    t = _s(term)
    if not t:
        return t
    m = _WINTER_RE.match(t)
    if m:
        # The January year is authoritative: "Winter 2026 (January 2026)".
        return "January %s (Spring)" % m.group(2)
    if _SPRING_RE.match(t):
        return "January %s (Spring)" % _SPRING_RE.match(t).group(1)
    return t


class Layout:
    """One known column arrangement of a Slate export.

    Slate's layout changes over time -- on 2026-08-03 a "Referral Info" column
    appeared at index 19, pushing Program Emphasis to 20. Rather than bumping
    indices and breaking every older file (including the ones the tests pin),
    each known arrangement is kept as its own Layout and the right one is picked
    per upload by matching its anchors.

    Newest first in Program.layouts, so a file matching several (columns only
    appended) resolves to the most recent.
    """

    def __init__(self, name, cols, utm_idx, anchors):
        self.name = name
        self.cols = cols                # logical name -> column index
        self.utm_idx = utm_idx          # (source, medium, campaign, content)
        self.anchors = anchors          # index -> expected header substring

    @property
    def min_cols(self):
        return max(max(self.cols.values()), max(self.utm_idx)) + 1

    def matches(self, headers):
        """-> (ok, [(idx, expected, found), ...]) for the anchors that failed."""
        bad = []
        for idx, expect in sorted(self.anchors.items()):
            found = "" if idx >= len(headers) else _s(headers[idx]).lower()
            if expect not in found:
                bad.append((idx, expect,
                            _s(headers[idx]) if idx < len(headers) else "(missing)"))
        return (not bad), bad


class Program:
    def __init__(self, key, label, layouts, stage_keys, stage_labels, stage_fn,
                 date_fields, extra_stages=None, channel_stage=None):
        self.key = key
        self.label = label
        self.layouts = layouts          # newest arrangement first
        self.stage_keys = stage_keys    # ordered funnel stages
        self.stage_labels = stage_labels
        self.stage_fn = stage_fn        # raw row -> {stage: bool}
        self.date_fields = date_fields  # filterable date columns: name -> label
        # Stages derived HERE rather than in analysis_engine.py, which is a
        # byte-identical vendored copy of the handoff reference and must not be
        # edited — a test pins its SHA-256. Each entry is stage key -> fn(row, idx).
        self.extra_stages = extra_stages or {}
        # What the two channel cards measure by default. Not simply the last
        # stage: Enrolled is real but barely populated yet, and defaulting the
        # headline charts to a 16-person stage would show near-empty bars.
        self.channel_stage = channel_stage or stage_keys[-1]

    def stages(self, row, enrolled_idx=None):
        """Canonical stage flags for a raw row, plus any locally derived ones."""
        st = dict(self.stage_fn(row))
        for key, fn in self.extra_stages.items():
            st[key] = fn(row, enrolled_idx)
        return st

    # The newest layout is the default for callers that aren't parsing a file
    # (seed scripts, tests, anything reading a column index off the program).
    @property
    def cols(self):
        return self.layouts[0].cols

    @property
    def utm_idx(self):
        return self.layouts[0].utm_idx

    @property
    def min_cols(self):
        """Lowest column count any known layout can be parsed from."""
        return min(l.min_cols for l in self.layouts)

    def layout_for(self, headers):
        """Pick the layout this file is in -> (layout, None), or (None, report).

        `report` describes the failure against the NEWEST layout, since that is
        the one a genuinely-new Slate arrangement would be closest to.
        """
        for layout in self.layouts:
            ok, _bad = layout.matches(headers)
            if ok:
                return layout, None
        _ok, bad = self.layouts[0].matches(headers)
        return None, bad


# Full-Time, as exported from 2026-08-03 on: "Referral Info" inserted at 19.
FT_2026_08 = Layout(
    name="2026-08 (with Referral Info)",
    utm_idx=(15, 16, 17, 18),
    cols={
        "global_id": 0, "term": 1,
        "started_date": 2, "submitted_date": 3, "completed_date": 4,
        "aud_requested": 5, "aud_pending": 6, "aud_complete": 7, "admitted": 8,
        "decision": 9,
        "country": 10, "region": 11, "city": 12, "postal": 13, "age": 14,
        "referral_info": 19, "emphasis": 20,
    },
    anchors={
        0: "global id", 1: "term", 2: "started", 8: "admitted",
        15: "utm source", 16: "utm medium", 17: "utm campaign", 18: "utm content",
        19: "referral", 20: "emphasis",
    },
)

# The layout every file before 2026-08-03 uses, including the handoff samples
# the tests pin. Kept so older exports still ingest.
FT_2026_07 = Layout(
    name="2026-07 (pre Referral Info)",
    utm_idx=(15, 16, 17, 18),
    cols={
        "global_id": 0, "term": 1,
        "started_date": 2, "submitted_date": 3, "completed_date": 4,
        "aud_requested": 5, "aud_pending": 6, "aud_complete": 7, "admitted": 8,
        "decision": 9,
        "country": 10, "region": 11, "city": 12, "postal": 13, "age": 14,
        "emphasis": 19,
    },
    anchors={
        0: "global id", 1: "term", 2: "started", 8: "admitted",
        15: "utm source", 16: "utm medium", 17: "utm campaign", 18: "utm content",
        19: "emphasis",
    },
)

FT = Program(
    key="ft",
    label="Full-Time (2 Year)",
    layouts=[FT_2026_08, FT_2026_07],
    stage_keys=["started", "submitted", "aud_req", "aud_comp", "admitted",
                "enrolled"],
    stage_labels={
        "started": "Started",
        "submitted": "Submitted",
        "aud_req": "Audition Requested",
        "aud_comp": "Audition Complete",
        "admitted": "Admitted",
        "enrolled": "Enrolled",
    },
    stage_fn=ft_stages,
    extra_stages={"enrolled": _ft_enrolled},
    channel_stage="admitted",
    date_fields={
        "started_date": "App Start Date",
        "submitted_date": "App Submitted Date",
        "completed_date": "App Completed Date",
    },
)

SUMMER_2026_07 = Layout(
    name="2026-07",
    utm_idx=(10, 12, 13, 14),
    cols={
        "global_id": 0, "term": 1, "started_date": 2, "age": 3,
        "app_status": 4, "decision": 5,
        "country": 6, "region": 7, "city": 8, "postal": 9,
        "emphasis": 11,
    },
    anchors={
        0: "global id", 1: "term", 2: "app date", 4: "status",
        10: "utm source", 11: "programs", 12: "utm medium", 13: "utm campaign",
        14: "utm content",
    },
)

SUMMER = Program(
    key="summer",
    label="Summer",
    layouts=[SUMMER_2026_07],
    stage_keys=["started", "submitted", "accepted"],
    stage_labels={
        "started": "Started",
        "submitted": "Submitted",
        "accepted": "Accepted",
    },
    stage_fn=summer_stages,
    date_fields={
        "started_date": "App Date",
    },
)

PROGRAMS = {"ft": FT, "summer": SUMMER}

# Expected header text per program, used for a loose sanity check on upload.
# The real FT export misspells "Audition" as "Audtion" in three headers, so we
# match on a normalised substring rather than exact equality.
EXPECTED_HEADER_HINTS = {
    "ft": ["global id", "app term", "utm source", "utm medium", "utm campaign"],
    "summer": ["global id", "term", "utm source", "utm medium", "utm campaign"],
}

# Anchor columns now live on each Layout above; this dict is kept only so the
# newest arrangement is greppable from one place.
#
# Anchor columns are checked BY POSITION on every upload.
#
# Everything is parsed by column index, so a column INSERTED IN THE MIDDLE of a
# Slate export shifts every field after it and silently corrupts the numbers —
# dates read as decisions, UTMs read as postcodes, no error anywhere. A schema
# change is expected (a BFA program is coming, likely as a new column), so this
# turns that failure mode into a loud, specific error at upload time.
#
# Matching is a normalised substring, so the "Audtion" typos, minor renames, and
# columns APPENDED at the end all still pass — only a positional shift fails.
HEADER_ANCHORS = {k: p.layouts[0].anchors for k, p in
                  (("ft", FT), ("summer", SUMMER))}


def get(program_key):
    if program_key not in PROGRAMS:
        raise KeyError("unknown program %r" % (program_key,))
    return PROGRAMS[program_key]
