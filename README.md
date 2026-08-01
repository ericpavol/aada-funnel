# AADA Funnel App

Ingests Slate "Ping Data" exports, computes the applicant funnel by marketing
channel, and reports it with charts and filters. Built against the handoff
package in `../aada-funnel-app-handoff/`.

**Stack:** Python 3.9 · FastAPI · SQLite · Jinja2 · Chart.js (vendored).
No Node, no build step, no external network calls at runtime — applicant data
never leaves the machine.

## Interface

Light SaaS dashboard: off-white plane, soft rounded cards, a serif display voice
(Instrument Serif) against IBM Plex Sans for every figure, oversized light-weight
hero numbers, pill controls, hairline tables. Both typefaces are **self-hosted**
in `app/static/fonts/` (OFL, latin subset, 112 KB total) — no font CDN, because
this app must make no external requests while applicant data is on screen.

- **Dark mode** — toggle in the top bar, persisted to `localStorage`. The dark
  series colours are separately *selected* steps validated against the dark
  surface, not an automatic inversion of the light ones.
- **Seven accent themes** — **lime (default)**, indigo, teal, amber, rose,
  violet, forest. The accent owns UI chrome only: active states, buttons, focus
  rings, funnel rails, and the single-series comparison bars.

### Filter bar

A **token bar**: one line at rest, one removable token per active dimension
("Region · England, CA"), and a **＋ Add filter** picker — dimension list, then a
searchable checkbox list with applicant counts. Typing beats hunting through 80
regions in a 6-row scroll box, which is what it replaced.

Two properties worth preserving if you touch it:

- **The bar is server-rendered and each ✕ is a real link.** Reading and clearing
  filters works with JavaScript off; only the add/edit picker needs JS. There is a
  `<noscript>` note saying so.
- **Selections accumulate inside an open picker and apply when it closes.** Filter
  state lives entirely in the query string, so applying means navigating — which is
  what makes shareable links and the back button work for free. Applying per
  checkbox would mean one page load per click; applying on close is one per picker
  session, and there's no bar-wide "Apply" button to forget.

Static asset URLs carry an mtime cache-buster (`asset()` in `main.py`). Without it
the browser will happily keep running a stale `filters.js` after an edit.

### Why each accent carries three tokens

A vivid accent cannot do every job, and lime is the reason:

| Token | Job | Lime |
|---|---|---|
| `--accent` | the **fill** — buttons, active pills, rails, bars | `#c7f000` |
| `--accent-fg` | ink **on** that fill | `#0b0b0b` (14.9:1) |
| `--accent-strong` | **text, hairlines, focus rings** | `#3f6212` light / `#bef264` dark |

`#c7f000` carries dark ink at 14.9:1, but it only reaches **1.3:1** against the
light surface — so it can never be a hairline, a focus ring, or a text colour.
That is not a flaw in the colour; it is what bright chartreuse *is*, and it is
why the reference designs only ever use lime as a filled chip with dark text.
Rather than mute the green, the accent splits into a fill plus a
contrast-bearing companion. Consequences worth knowing:

- Accent-filled bars and pills get a 1px `--accent-strong` hairline so their
  shape stays readable against a pale surface.
- The funnel rail is a gradient from a deeper step into the lime, which both
  looks deliberate and gives the leading edge real contrast.
- The dashed baseline reference line and the brand dot use `--accent-strong`,
  never the fill.

Every accent is measured: fill/ink ≥ 4.5:1, and `--accent-strong` ≥ 4.8:1
against the surface, in both modes.
- **The chart series palette is fixed and never themed.** Colour follows the
  entity, not the user's preference, so a channel keeps its colour across
  filters, pages, and themes. The dot beside each parent row in the tables is
  that channel's series colour, keyed on the channel name rather than its rank —
  so a filter that drops a channel cannot repaint the survivors.

The categorical palette passes all six data-viz checks in both modes (lightness
band, chroma floor, adjacent-pair CVD separation ΔE 9.1 light / 8.4 dark,
normal-vision floor 19.6 / 19.3, contrast). Three light-mode hues sit below 3:1
on the surface, which obliges "relief": every chart carries a legend or direct
value labels, and the full numbers are always available in the tables below. To
re-check after any palette change:

```bash
python3 <dataviz-skill>/scripts/validate_palette.py \
  "#2a78d6,#eb6834,#1baf7a,#eda100,#e87ba4,#008300,#4a3aa7,#e34948" \
  --mode light --surface "#fcfcfb"
```

Theme state is stamped onto `<html>` by an inline script in `<head>` before first
paint, so there is no flash of the wrong palette — and that is also what lets the
dark-mode token block win at equal CSS specificity. `app/static/app.css` holds the
tokens; `app/static/app.js` holds the theme controls and the chart factory, which
reads its colours back out of the CSS custom properties so the palette lives in
exactly one place.

## Run it

```bash
cd "AADA Slate Funnel/funnel-app"
./.venv/bin/python seed.py          # load the two sample exports (idempotent)
./.venv/bin/python serve.py         # http://127.0.0.1:8123
```

Tests:

```bash
./.venv/bin/python -m pytest
```

First-time setup, if the venv is missing:

```bash
python3 -m venv .venv && ./.venv/bin/pip install fastapi "uvicorn[standard]" openpyxl jinja2 python-multipart pytest httpx
```

## How the analysis logic is handled

`app/analysis_engine.py` is a **byte-identical copy** of
`../aada-funnel-app-handoff/reference/analysis_engine.py`. It is the canonical
source of parsing and classification, and it is imported, never re-implemented —
`app/programs.py` uses its `ft_stages`/`summer_stages`, and `app/ingest.py` uses
its `build_path`/`classify`. `test_vendored_engine_matches_handoff_original`
fails if the copy drifts, so re-copy the file rather than editing it here.

`app/metrics.py` mirrors `build_matrix` but reads from SQLite instead of an xlsx
row list, and `test_db_matrix_matches_engine_matrix_row_for_row` asserts the two
agree on every channel row against the real sample data.

## What the tests pin

34 tests, all against the real files in `sample_data/`:

- the documented stage totals (8436 / 1240 / 875 / 460 / 350) and every channel
  figure in the engine's docstring;
- Summer totals (4483 / 954 / 387);
- the DB round-trip reproducing the canonical matrix row for row;
- re-upload idempotency (0 new rows, 0 new pings, identical matrix);
- Full-Time and Summer staying separate;
- any-touch rows overlapping (not summable) vs first-touch rows summing exactly
  to the stage total;
- the No-UTM row surviving at the documented ~13%;
- unclassified UTMs being recorded for review, their counts **not** inflating on
  re-upload, and a "reviewed" mark surviving a re-ingest;
- fiscal-year boundaries (August lands *inside* a year), timeline bucketing being
  exact against raw SQL, and the timeline never reusing a colour for two
  categories;
- the makeup chart keeping the No-UTM row and overlapping past 100%;
- a mid-export column insertion being **rejected** while an appended column
  imports with a warning;
- the referrer ping log de-duplicating, rejecting the wrong export type, and
  leaving every funnel figure untouched.

## Screens

| Route | What |
|---|---|
| `/` | Overall conversion funnel first, then channel-comparison charts, then the two channel tables (both collapsible; the open/closed choice is remembered) |
| `/utm` | Top raw UTM Campaign / Content / Source / Medium values per stage |
| `/referrers` | Top Slate native ping referrers — the third export. See below. |
| `/uploads` | Upload form (all three export types), unrecognised-UTM review queue, upload history |

Both tables mirror the hand-built workbook: parent channels with indented
sub-rows, `#` columns white, `%` columns grey, gold baseline row.

## Ongoing uploads and de-duplication

Uploads accumulate. Nothing is replaced, so you can keep dropping in new exports.

- **Applicant identity:** `(program, global_id, term, started_date)` plus a
  `dedup_seq` safety net. Not `global_id` alone — the real exports repeat Global
  IDs (29 in the Full-Time sample, 15 in Summer), usually one row with a term and
  a duplicate with a blank term. Collapsing on `global_id` would report 8,403
  instead of the documented 8,436 and silently drop 33 records.
- **Ping identity:** `(applicant_id, ts, seq, source, medium, campaign, content)`,
  so the same ping arriving in two exports is stored once.
- **Stage flags are monotonic** — an upsert can only advance a stage 0 → 1. Funnel
  stages are cumulative, so ingestion order doesn't matter and re-uploading an
  older export can't walk someone back out of "Admitted".

## The tag timeline ("When tags land")

A "tag" is one UTM touch. Category = channel, sub-category = sub-source, so the
lines reconcile with every other number in the app.

**Counting — the two "Count" options are not interchangeable:**

| Option | What one unit is | Reads as |
|---|---|---|
| **Tag events** | every touch — the same person clicking an ad 5× in October is **5** | activity / volume |
| **Distinct people** | each person once **per bucket** — that person is **1** in October | reach |

The trap in the second one: a person active in three months is distinct in each,
so the plotted points **do not add up to a headcount**. On the current data the
monthly buckets sum to 8,196 while only 7,303 real people exist. The chart is
per-bucket by definition, but the headline figure is its own
`COUNT(DISTINCT applicant_id)` over the whole range so it never reports an
inflated number, and the note under the controls spells out the difference.
`test_timeline_people_headline_is_a_real_headcount` pins it.

**Dating a tag:** its own timestamp where it has one — **98.5%** of Full-Time and
**99.6%** of Summer tags do, because the UTM cells pack a timestamp against each
value. The remainder fall back to the applicant's **app start date**, and every
fallback case has one, so no tag is ever undated. The count of approximated tags
is shown under the chart rather than hidden.

**Fiscal year runs 1 Sept → 31 Aug.** The original ask was Sept 1 – Jul 31, but
that is an 11-month window and would strand every August tag between two years
(479 of them in the current data). Ending 31 Aug means every tag lands in exactly
one year. `test_fiscal_year_boundaries` pins this.

**Two encodings, because two dimensions overlay at once:**

- **Colour = category**, so a channel keeps the colour it has in the tables and
  the penetration chart.
- **Line style = year.** The newest fiscal year is solid and full-strength;
  earlier years are dashed, thinner and dimmed to 45% alpha — that's the
  "previous years greyed out" requirement, done without giving up the category
  colour.

**Hard-capped at 8 categories**, matching the palette's 8 slots. A 9th line would
have to reuse a hue, and a repeated colour reads as the same category — so extra
categories are listed as *not drawn* and can be selected explicitly instead.
`test_timeline_never_reuses_a_colour_for_two_categories` enforces it.

Buckets (monthly / weekly / daily) are positions **within** the fiscal year, not
absolute dates — that's what lets several years share one axis. Counting toggles
between tag events and distinct people. All timeline state lives in `tl_*` query
params so it can't collide with the filter bar, and both survive each other.

## The two channel bar charts

A deliberate pair, same visual language, opposite questions:

| Chart | Question | Metric |
|---|---|---|
| **Which channels convert** | of the people who touched this channel, how many converted? | within-channel % — *quality* |
| **Which channels make up X** | of the people who got in, how many touched this channel? | stage penetration % — *presence* |

Three differences in the makeup chart, all intentional:

- **No baseline line.** A share-of-stage baseline is 100% by definition, so a
  reference line would say nothing.
- **No volume gate.** Penetration is a share of the stage, so a tiny channel
  can't inflate it; gating would only hide real rows.
- **"No UTM (untracked)" is kept in.** Untracked people do get admitted (22 of
  350, 6.3%), and `CLAUDE.md` is explicit that the row is never silently dropped.

The bars are any-touch and therefore **overlap** — they total ~179% for Admitted,
not 100%, because most admits touched several channels. The card states the actual
total so it can't be misread as a composition. For a true 100% split, that's what
the first-touch donut below is for.

## The native referrer ping log (`/referrers`)

`DATA_SCHEMA.md`'s third export — one row per **page view**, with the referring
URL. Upload it from `/uploads` choosing **Referrer ping log**.

It lives in its own tables (`ref_pings`, `ref_uploads`), never folded into
`pings`. Two reasons, both worth preserving:

- **The grain is different** — page views, not UTM touches. Mixing them would
  corrupt the validated funnel figures. A test asserts that loading the ping log
  leaves every channel figure untouched.
- **It's keyed on the person, not the application.** The export carries no term,
  so rows join to `applicants` on `global_id` and the table is deliberately
  program-agnostic.

**What it's for:** a referrer domain is recorded even when there is no UTM at
all, so this is the only view that sees email opens through the Gmail app
(`com.google.android.gm`), Zoom sessions, AI assistants, and partner sites.
The `untagged` scope isolates exactly that traffic.

**Two honest caveats**, both surfaced in the UI:

- **UTM Term is here and nowhere else**, but 54 of the 56 distinct values in the
  July 2026 pull are opaque 18-digit **Meta ad-set IDs**, not keywords. Useful for
  joining to Ads reporting; not useful to read directly.
- **It barely dents the No-UTM bucket.** Of ~1,130 Full-Time applicants with no
  usable UTM, 728 appear in the ping log but only **66** have an external referrer
  to attribute — the rest only ever arrived via `apply.aada.edu` itself. Roughly
  6% recovery, not the fix the "13% untracked" figure might imply.

The headline stat is **external and untagged**, not raw untagged: ~82% of all page
views carry no UTM simply because they are internal navigation, which is normal
and not a tracking gap.

## Schema changes (BFA, and anything after it)

Every column is read **by index**, so a column inserted mid-export would shift
every field after it and silently corrupt the numbers — dates read as decisions,
UTMs as postcodes, no error anywhere. `programs.HEADER_ANCHORS` therefore checks
anchor headers **at their expected position** on every upload and refuses the file
if they've moved, naming the column that shifted. Two tests cover it.

Consequences for the coming **BFA program (3-year / 4-year)**:

- A column **appended at the end** imports fine and logs a warning that it was
  ignored — nothing breaks, and nothing silently uses it either.
- A column **inserted in the middle** is rejected with a clear message until the
  column map in `app/programs.py` is updated. That is the intended behaviour.
- If BFA arrives as a **new value in an existing column** (e.g. a program-type
  field), it becomes a filter dimension: add it to `filters.describe()` and
  `Filters`, which is a small change.
- If it arrives as a **separate export** with its own funnel stages, add a
  `Program` entry in `app/programs.py` — `PROGRAMS` is what drives the program
  switcher, the stage labels, and the column map, so a third program is
  configuration rather than new code. Full-Time and Summer stay separate, and a
  third would too.

Either way, send a sample export and it's a short change — I deliberately did not
guess at the column layout.

## Unrecognised UTMs

Any ping that matches no classification rule is recorded in `unknown_utms` and
surfaced with a badge on `/uploads`, so a new network (Snapchat, say) is reported
rather than silently absorbed into "Unresolved/Other". In the current sample data
that's 7 pings total: 6 with an empty source and `medium=cpc`, and 1
`Chrome / Extension:omnibox`.

## Known gaps

- **UTM Term is not in these exports.** The Full-Time and Summer files carry only
  Source / Medium / Campaign / Content. UTM Term appears only in the third Slate
  export (the native "referrer" ping log, see `DATA_SCHEMA.md`), which isn't
  ingested yet. `/utm` covers the four fields that do exist.
- **Period-over-period trends** (v3) aren't built. Every upload is recorded as a
  dated run in the `uploads` table, so the data needed for run-to-run deltas is
  being captured already.
- **No xlsx/pdf export** of the current view yet.
- The reference docstring comments `# observed count: 0` on
  `Google (Paid) / Other paid`, but the sample file has exactly 1 such ping. The
  rule is correct; the comment is stale. The test pins the real value, 1.
