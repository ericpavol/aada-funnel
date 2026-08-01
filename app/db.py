"""SQLite storage. One row per applicant per program, one row per ping.

Dedup contract (this is the whole point of persisting):
  * applicants  UNIQUE(program, global_id, term, started_date, dedup_seq)
    -- re-uploading an overlapping export updates the existing record instead
    of creating a second one.
  * pings       UNIQUE(applicant_id, ts, seq, source, medium, campaign, content)
    -- the same ping arriving in two exports is stored once.
Empty strings are used instead of NULL in those key columns because SQLite
treats NULLs as distinct in a UNIQUE index, which would defeat the dedup.

Why the applicant key is composite rather than just global_id: the real Slate
exports repeat Global IDs (29 of them in the Full-Time sample, 15 in Summer),
usually as one row with a term plus one row with a blank term. The canonical
engine counts ROWS, so collapsing on global_id alone would report 8,403 instead
of the documented 8,436 and quietly discard 33 records. Adding term +
started_date makes the key unique across every row of both sample files while
staying stable between exports, since neither field changes once an application
exists. `dedup_seq` is a safety net: if a future export ever does collide on all
three, the second row gets seq=1 rather than being silently dropped.

Funnel stage flags are stored, not re-derived at query time, and they are
MONOTONIC: an upsert can only ever advance a stage from 0 -> 1. Funnel stages
are cumulative achievements, so this makes ingestion order-independent -- a
stale export uploaded after a fresh one cannot walk someone back out of
"Admitted".
"""
import os
import sqlite3

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "funnel.db")

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS uploads (
    id                 INTEGER PRIMARY KEY,
    program            TEXT NOT NULL CHECK (program IN ('ft','summer')),
    filename           TEXT NOT NULL,
    sha256             TEXT NOT NULL,
    uploaded_at        TEXT NOT NULL,
    row_count          INTEGER NOT NULL DEFAULT 0,
    applicants_new     INTEGER NOT NULL DEFAULT 0,
    applicants_updated INTEGER NOT NULL DEFAULT 0,
    pings_new          INTEGER NOT NULL DEFAULT 0,
    pings_duplicate    INTEGER NOT NULL DEFAULT 0,
    notes              TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS applicants (
    id              INTEGER PRIMARY KEY,
    program         TEXT NOT NULL,
    global_id       TEXT NOT NULL,
    term            TEXT NOT NULL DEFAULT '',
    country         TEXT NOT NULL DEFAULT '',
    region          TEXT NOT NULL DEFAULT '',
    city            TEXT NOT NULL DEFAULT '',
    postal          TEXT NOT NULL DEFAULT '',
    age             INTEGER,
    emphasis        TEXT NOT NULL DEFAULT '',
    decision        TEXT NOT NULL DEFAULT '',
    app_status      TEXT NOT NULL DEFAULT '',
    started_date    TEXT NOT NULL DEFAULT '',
    submitted_date  TEXT NOT NULL DEFAULT '',
    completed_date  TEXT NOT NULL DEFAULT '',
    st_started      INTEGER NOT NULL DEFAULT 0,
    st_submitted    INTEGER NOT NULL DEFAULT 0,
    st_aud_req      INTEGER NOT NULL DEFAULT 0,
    st_aud_comp     INTEGER NOT NULL DEFAULT 0,
    st_admitted     INTEGER NOT NULL DEFAULT 0,
    st_accepted     INTEGER NOT NULL DEFAULT 0,
    st_enrolled     INTEGER NOT NULL DEFAULT 0,
    dedup_seq       INTEGER NOT NULL DEFAULT 0,
    first_upload_id INTEGER REFERENCES uploads(id),
    last_upload_id  INTEGER REFERENCES uploads(id),
    UNIQUE (program, global_id, term, started_date, dedup_seq)
);
CREATE INDEX IF NOT EXISTS ix_app_gid ON applicants(program, global_id);
CREATE INDEX IF NOT EXISTS ix_app_program ON applicants(program);
CREATE INDEX IF NOT EXISTS ix_app_term    ON applicants(program, term);
CREATE INDEX IF NOT EXISTS ix_app_started ON applicants(program, started_date);

CREATE TABLE IF NOT EXISTS spend_uploads (
    id           INTEGER PRIMARY KEY,
    program      TEXT NOT NULL,
    platform     TEXT NOT NULL,
    filename     TEXT NOT NULL,
    sha256       TEXT NOT NULL DEFAULT '',
    period_start TEXT NOT NULL DEFAULT '',
    period_end   TEXT NOT NULL DEFAULT '',
    row_count    INTEGER NOT NULL DEFAULT 0,
    total_cost   REAL NOT NULL DEFAULT 0,
    uploaded_at  TEXT NOT NULL
);

-- Media spend, already resolved onto the app's own channel taxonomy at ingest.
--
-- Re-uploading REPLACES the months it covers rather than merging. Spend is a
-- restatement of a period, not an event log: platforms revise figures after the
-- fact (invalid-click credits, late conversions), so a monotonic merge like the
-- one applicants use would freeze the first number ever seen. `campaign` and
-- `ad_id` are kept raw so the Meta ad-id coverage check has something to join
-- on, and so campaign-level detail stays possible without a re-upload.
CREATE TABLE IF NOT EXISTS spend (
    id          INTEGER PRIMARY KEY,
    program     TEXT NOT NULL,
    platform    TEXT NOT NULL,
    month       TEXT NOT NULL DEFAULT '',   -- 'YYYY-MM'
    channel     TEXT NOT NULL DEFAULT '',
    sub_source  TEXT NOT NULL DEFAULT '',
    campaign    TEXT NOT NULL DEFAULT '',
    ad_id       TEXT NOT NULL DEFAULT '',
    cost        REAL NOT NULL DEFAULT 0,
    clicks      INTEGER NOT NULL DEFAULT 0,
    impressions INTEGER NOT NULL DEFAULT 0,
    upload_id   INTEGER REFERENCES spend_uploads(id),
    UNIQUE (program, platform, month, campaign, ad_id, sub_source)
);
CREATE INDEX IF NOT EXISTS ix_spend_scope ON spend(program, month);
CREATE INDEX IF NOT EXISTS ix_spend_chan  ON spend(program, channel, sub_source);

CREATE TABLE IF NOT EXISTS pings (
    id           INTEGER PRIMARY KEY,
    applicant_id INTEGER NOT NULL REFERENCES applicants(id) ON DELETE CASCADE,
    ts           TEXT NOT NULL DEFAULT '',
    seq          INTEGER NOT NULL DEFAULT 0,
    source       TEXT NOT NULL DEFAULT '',
    medium       TEXT NOT NULL DEFAULT '',
    campaign     TEXT NOT NULL DEFAULT '',
    content      TEXT NOT NULL DEFAULT '',
    channel      TEXT NOT NULL DEFAULT '',
    sub_source   TEXT NOT NULL DEFAULT '',
    UNIQUE (applicant_id, ts, seq, source, medium, campaign, content)
);
CREATE INDEX IF NOT EXISTS ix_ping_app     ON pings(applicant_id);
CREATE INDEX IF NOT EXISTS ix_ping_channel ON pings(channel, sub_source);

-- ---------------------------------------------------------------------------
-- Slate native "referrer" ping log (DATA_SCHEMA.md's third export). One row per
-- PAGE VIEW, keyed on the person rather than an application, because the export
-- carries no term -- so it joins to `applicants` on global_id and is deliberately
-- program-agnostic. Kept in its own tables rather than folded into `pings`: the
-- grain is different (page views, not UTM touches) and the numbers must never
-- silently mix into the validated funnel figures.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ref_uploads (
    id              INTEGER PRIMARY KEY,
    filename        TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    uploaded_at     TEXT NOT NULL,
    row_count       INTEGER NOT NULL DEFAULT 0,
    pings_new       INTEGER NOT NULL DEFAULT 0,
    pings_duplicate INTEGER NOT NULL DEFAULT 0,
    people          INTEGER NOT NULL DEFAULT 0,
    notes           TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ref_pings (
    id        INTEGER PRIMARY KEY,
    global_id TEXT NOT NULL,
    ts        TEXT NOT NULL DEFAULT '',
    duration  INTEGER,
    referrer  TEXT NOT NULL DEFAULT '',
    domain    TEXT NOT NULL DEFAULT '',
    internal  INTEGER NOT NULL DEFAULT 0,   -- referrer is our own site
    url       TEXT NOT NULL DEFAULT '',
    source    TEXT NOT NULL DEFAULT '',
    medium    TEXT NOT NULL DEFAULT '',
    campaign  TEXT NOT NULL DEFAULT '',
    content   TEXT NOT NULL DEFAULT '',
    term      TEXT NOT NULL DEFAULT '',
    has_utm   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (global_id, ts, referrer, url)
);
CREATE INDEX IF NOT EXISTS ix_ref_gid    ON ref_pings(global_id);
CREATE INDEX IF NOT EXISTS ix_ref_domain ON ref_pings(domain);
CREATE INDEX IF NOT EXISTS ix_ref_utm    ON ref_pings(has_utm);

-- Source/medium combos that fell through every classification rule, so a new
-- network (Snapchat, etc.) is reported instead of silently pooling into
-- "Unresolved/Other".
CREATE TABLE IF NOT EXISTS unknown_utms (
    id              INTEGER PRIMARY KEY,
    source          TEXT NOT NULL DEFAULT '',
    medium          TEXT NOT NULL DEFAULT '',
    campaign        TEXT NOT NULL DEFAULT '',
    ping_count      INTEGER NOT NULL DEFAULT 0,
    applicant_count INTEGER NOT NULL DEFAULT 0,
    first_upload_id INTEGER REFERENCES uploads(id),
    last_seen_at    TEXT NOT NULL DEFAULT '',
    acknowledged    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (source, medium, campaign)
);
"""


def connect(path=None):
    path = path or DEFAULT_DB
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# Stage columns added after the first release. CREATE TABLE IF NOT EXISTS does
# nothing to a table that already exists, so an existing funnel.db would keep
# the old shape and every insert would fail on the unknown column.
_ADDED_COLUMNS = [
    ("applicants", "st_enrolled", "INTEGER NOT NULL DEFAULT 0"),
]


def _migrate(conn):
    for table, col, decl in _ADDED_COLUMNS:
        have = {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}
        if col not in have:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))
    conn.commit()


def reset(conn):
    """Drop all ingested data (keeps the schema). Used by tests."""
    conn.executescript(
        "DELETE FROM pings; DELETE FROM applicants; "
        "DELETE FROM spend; DELETE FROM spend_uploads; "
        "DELETE FROM unknown_utms; DELETE FROM uploads; "
        "DELETE FROM ref_pings; DELETE FROM ref_uploads;"
    )
    conn.commit()
