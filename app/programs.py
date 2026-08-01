"""Per-program configuration: column layout, funnel stages, labels.

Column indices are 0-based and come from DATA_SCHEMA.md. Full-Time and Summer
are analysed SEPARATELY and never merged (CLAUDE.md golden rule).
"""
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


class Program:
    def __init__(self, key, label, utm_idx, stage_keys, stage_labels, stage_fn,
                 cols, date_fields, extra_stages=None, channel_stage=None):
        self.key = key
        self.label = label
        self.utm_idx = utm_idx          # (source, medium, campaign, content)
        self.stage_keys = stage_keys    # ordered funnel stages
        self.stage_labels = stage_labels
        self.stage_fn = stage_fn        # raw row -> {stage: bool}
        self.cols = cols                # logical name -> column index
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

    @property
    def min_cols(self):
        """Lowest column count a file must have to be parseable."""
        return max(max(self.cols.values()), max(self.utm_idx)) + 1


FT = Program(
    key="ft",
    label="Full-Time (2 Year)",
    utm_idx=(15, 16, 17, 18),
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
    cols={
        "global_id": 0, "term": 1,
        "started_date": 2, "submitted_date": 3, "completed_date": 4,
        "aud_requested": 5, "aud_pending": 6, "aud_complete": 7, "admitted": 8,
        "decision": 9,
        "country": 10, "region": 11, "city": 12, "postal": 13, "age": 14,
        "emphasis": 19,
    },
    date_fields={
        "started_date": "App Start Date",
        "submitted_date": "App Submitted Date",
        "completed_date": "App Completed Date",
    },
)

SUMMER = Program(
    key="summer",
    label="Summer",
    utm_idx=(10, 12, 13, 14),
    stage_keys=["started", "submitted", "accepted"],
    stage_labels={
        "started": "Started",
        "submitted": "Submitted",
        "accepted": "Accepted",
    },
    stage_fn=summer_stages,
    cols={
        "global_id": 0, "term": 1, "started_date": 2, "age": 3,
        "app_status": 4, "decision": 5,
        "country": 6, "region": 7, "city": 8, "postal": 9,
        "emphasis": 11,
    },
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

# Anchor columns checked BY POSITION on every upload.
#
# Everything is parsed by column index, so a column INSERTED IN THE MIDDLE of a
# Slate export shifts every field after it and silently corrupts the numbers —
# dates read as decisions, UTMs read as postcodes, no error anywhere. A schema
# change is expected (a BFA program is coming, likely as a new column), so this
# turns that failure mode into a loud, specific error at upload time.
#
# Matching is a normalised substring, so the "Audtion" typos, minor renames, and
# columns APPENDED at the end all still pass — only a positional shift fails.
HEADER_ANCHORS = {
    "ft": {
        0: "global id", 1: "term", 2: "started", 8: "admitted",
        15: "utm source", 16: "utm medium", 17: "utm campaign", 18: "utm content",
        19: "emphasis",
    },
    "summer": {
        0: "global id", 1: "term", 2: "app date", 4: "status",
        10: "utm source", 11: "programs", 12: "utm medium", 13: "utm campaign",
        14: "utm content",
    },
}


def get(program_key):
    if program_key not in PROGRAMS:
        raise KeyError("unknown program %r" % (program_key,))
    return PROGRAMS[program_key]
