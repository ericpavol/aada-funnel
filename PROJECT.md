# Project state — read this first

A standing summary for picking this app back up cold, especially in a fresh
session with no memory of how it got here. `README.md` covers *how the app
works*; this file covers **why it is the way it is**, what is currently true, and
what is still open — the parts that don't survive in the code.

**Keep it current.** When a decision gets made or reversed, edit this file in the
same change. A stale entry here is worse than a missing one, because it will be
trusted.

_Last updated: 2026-08-03_

---

## What this is

An applicant-funnel analytics app for AADA (American Academy of Dramatic Arts),
built from Slate "Ping Data" exports plus Google/Meta media spend. It answers:
where applicants drop out of the funnel, which marketing channels bring them in,
which channels convert, and what each outcome costs.

Two programs are analysed **separately and never merged** — Full-Time (2 Year)
and Summer. That is a golden rule from the handoff's `CLAUDE.md`, not a
preference.

## Ground rules that must not be broken

1. **`sample_data/` holds REAL applicant records.** Never commit it, never send
   it to a third-party service, never let it into the public repo. This is the
   constraint that shaped the entire hosting design below.
2. **`app/analysis_engine.py` is a byte-identical vendored copy** of the
   handoff's reference engine. Import it; never edit it. A SHA-256 test fails on
   drift — re-copy the file instead of patching it.
3. **Any-touch channel rows overlap and must never be summed.** A person counts
   in every channel they touched. First- and last-touch rows *do* partition
   people and may be summed. Conflating the two is the single most likely way to
   produce a confidently wrong number here — it has already happened once (see
   below).

## Current numbers (sanity check after any change)

Full-Time funnel, all time: **8,436** started → 1,240 submitted → 875 audition
requested → 460 audition complete → **350 admitted** → **16 enrolled**.
Summer: 4,483 → 954 → 387.

FY 2025/26 paid media: **$288,357** (Google $140,222 · Meta $148,135).
Blended first-touch cost per started app ≈ **$51**.

**68 tests** pass against the real sample files. If a change moves any canonical
number above, that is a regression until proven otherwise.

---

## Decisions and why

### Hosting: Render, paid Starter tier
Live at <https://aada-funnel.onrender.com>. Free tiers don't offer persistent
disks, and the SQLite database is a file that must survive redeploys. Fly.io was
costed as slightly cheaper and is still a fine fallback (the app has zero
platform-specific code — moving hosts is a file copy plus env vars), but Eric
already had a Render plan, which settled it.

### Database stays SQLite
A Postgres migration would unlock free hosting, and was explicitly **rejected**:
it touches `db.py`, `metrics.py`, `ingest.py`, `spend.py`, and every test's
`:memory:` fixture. Not worth a few dollars a month. Revisit only if concurrency
or hosting constraints actually force it.

### Auth: HTTP Basic Auth via env vars, not Supabase
Supabase was considered and dropped — a hosted user-management system is real
infrastructure for what is actually "let two known people in." Tradeoffs
accepted knowingly: one shared credential, no self-service reset (change
`AADA_PASS` in Render), no audit trail. Revisit if the audience grows past a
handful of people or per-person accountability starts to matter.

### Code public, data private
The GitHub repo is public; the data never enters it. The hosted instance starts
empty and needs its own one-time upload via `/uploads`.

### `dev` → `main` branch workflow, deploy gated separately
Work happens on `dev`; merging to `main` used to BE the deploy (Render
auto-deployed on push). Turned off after a real slip: staying on `main` after a
merge and committing the next change directly to it — reaching `main` and going
live were the same instant, so there was no gap to catch it.

Now `render.yaml` sets `autoDeploy: false`. Reaching `main` no longer deploys by
itself — a deploy happens only when Render's Sync Hook URL is called
explicitly, a separate deliberate step after the merge. The hook URL is a
capability, not unlike an API key: never commit it, never paste it anywhere
public. Rollback lives in Render's dashboard, not git.

---

## Analytical decisions worth not re-litigating

- **Fiscal year is 1 Sept → 31 Aug**, and a date filter on app start date is
  applied **by default** to the newest year with data. Without it the app
  silently mixed intake years. "All time" is one click away, so it's a starting
  point rather than a hidden filter.
- **Three attribution lenses, as a user-facing toggle**: first touch (what
  found them), last touch (what closed them), any touch (everything they
  touched). First and last each put a person in exactly one channel and
  therefore **sum**; any touch overlaps and its blended figure comes from a
  **set union**, never a column sum. The disagreement between lenses is the
  point — on current data Meta costs $11.4k per admit on first touch but
  $24.7k on last, i.e. it starts conversations far better than it finishes
  them, while Google is steady across both.
- **One channel, one colour, everywhere.** `taxonomy.channel_slot` is the
  single source of truth, shared by every chart via `AADA_CHANNEL_COLOURS`.
  Charts used to assign hues by position within whatever was on screen, so
  ticking one extra series repainted the rest — Meta was green in one section
  and orange in the donut. There are 13 channels and 8 validated hues: the 8
  slots are reserved for the 8 that carry volume, and the tail draws in neutral
  grey rather than wrapping (which would make Spotify wear Meta's green).
  A sub-source deliberately shares its parent's slot, drawn desaturated and
  dotted — that is the one intended kind of colour sharing.
- **Spend maps to channels with no mapping table.** Google's `Campaign type` and
  Meta's `Platform` land directly on the app's existing sub-sources. Google's
  `utm_campaign` values are hand-made codes that do *not* match its campaign
  names — a campaign-level join would need a hand-maintained mapping, which is
  exactly what rolling up by campaign type avoids.
- **Spend uploads replace, they don't accumulate.** Each upload wipes and
  rewrites the months it covers, because platforms revise figures after the
  fact. Months not in the file are untouched.
- **Winter and Spring are the same intake.** AADA is renaming the January
  intake, and Slate's Aug 2026 export carries both spellings at once
  ("Winter 2026 (January 2026)" and "January 2027 (Spring)").
  `programs.canonical_term` collapses them to the new naming at ingest, and
  `db._migrate_terms` rewrites rows stored under the old label — term is part
  of the applicant dedup key, so two spellings would mean two filter options
  and duplicate people.
- **Enrolled is derived here, not by the vendored engine** — read from Most
  Recent Decision until Slate ships a dedicated column, which the code will pick
  up automatically when it appears.

## Known caveats, surfaced in the UI

- **Enrolled is thin**: 16 people, all one term. Slate only flips the flag at
  matriculation, so cost-per-enrolled is a maturing cohort, not a rate.
- **Cost blends three different dates** and doesn't reconcile them: platform
  spend month, app start date (the filter), and ping timestamps (channel
  credit, unbounded). On current data they line up closely — median gap between
  first ping and app start is 0 days — but that's a property of this data, not a
  guarantee.
- **Paid channels only.** Organic, email, and direct mail carry real cost no ad
  platform exports. They show a dash, never `$0`.
- **Date filter honours whole months** for spend. A range inside one month counts
  that month entire; splitting it would be inventing daily figures.
- **August spend is missing** from the current files (Sept–Jul only) while the
  fiscal year runs through August.

## Mistakes already made once — don't repeat

- **Summing any-touch rows for a blended cost.** Produced a cost-per-admit 44%
  too cheap ($1,122 vs the correct $1,988) and looked entirely plausible. Fixed
  with a set union; there is a test asserting the union is genuinely smaller than
  the sum on real data, so it can't quietly become a no-op.
- **Inferring a default from `|tojson` key order.** Jinja sorts dict keys, so
  "the last key" was alphabetical, not the funnel's last stage — it inverted
  every URL. Defaults are now passed explicitly.
- **A `<p>` nested inside a `<p>`** silently hoisted a hover popover into the
  page as visible body text. `tip()` emits `<p>`; don't wrap it in one.
- **Two servers bound to port 8123**, with a stale one answering and producing
  phantom 500s. Kill by port, not by process name.

## Growth-only invariant

Every export should be a **superset** of the last one — more people, never
fewer. `ingest._check_term_shrinkage` enforces this at term granularity: before
a file is stored, it compares the file's per-term row counts against what the
database already holds, and warns (does not block — uploads never delete)
whenever a term comes back smaller. This exists because the 2026-08-03 Slate
pull silently dropped 1,326 of 1,334 Winter 2026 rows. Checked and ruled out
the Winter->Spring rename as the cause: those 1,335 Global IDs are absent from
the new file under *any* term name, and the rename visibly applied to 2027
(which grew 14 -> 44), not 2026. So it is a narrower report
filter on Slate's side, not data loss, but nothing would have surfaced it
without this check.

## Still open

- Summer media spend (Eric will supply separately; the uploader already accepts a
  program per file).
- Cohorting spend to each person's first-touch month, which would fix the
  date-blending caveat for deeper stages. Real work; only worth it if
  cost-per-admit/enrolled becomes a decision-grade number.
- No xlsx/pdf export of the current view.
- Direct-mail cost has no source file — would need manual entry.
