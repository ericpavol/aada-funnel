# Project state — read this first

A standing summary for picking this app back up cold, especially in a fresh
session with no memory of how it got here. `README.md` covers *how the app
works*; this file covers **why it is the way it is**, what is currently true, and
what is still open — the parts that don't survive in the code.

**Keep it current.** When a decision gets made or reversed, edit this file in the
same change. A stale entry here is worse than a missing one, because it will be
trusted.

_Last updated: 2026-09-23_

---

## What this is

An applicant-funnel analytics app for AADA (American Academy of Dramatic Arts),
built from Slate "Ping Data" exports plus Google/Meta media spend. It answers:
where applicants drop out of the funnel, which marketing channels bring them in,
which channels convert, and what each outcome costs.

Two programs are analysed **separately and never merged** — Full-Time and
Summer. That is a golden rule from the handoff's `CLAUDE.md`, not a preference.

Full-Time is no longer only the 2-year AOS: as of the 2026-09 export it also
carries the BFA (3- and 4-year). Both arrive in the *same* Slate export and
share one funnel, so they are one program here, separated by filters rather
than by the golden rule. The nav label dropped "(2 Year)" accordingly.

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

**78 tests** pass against the real sample files. If a change moves any canonical
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

### `dev` → `main` branch workflow, auto-deploy on `main`
Work happens on `dev`. It reaches `main` only via an explicit, asked-for merge
(`git merge dev --ff-only`), and `render.yaml` sets `autoDeploy: true`, so
pushing `main` deploys.

**This flag was flipped off once and flipped back — don't flip it again without
reading why.** It was turned off after a real slip: staying on `main` after a
merge and committing the next change directly to it, with no gap to catch it.
But "deploy by hand instead" did not survive contact with reality:

- The **Blueprint Sync Hook** (`api.render.com/sync/...`) only re-reads this
  `render.yaml` and applies config changes. It returns `201` and deploys
  nothing. Using it as a deploy trigger meant a deploy silently never happened.
- The **Service Deploy Hook** (per-service, that service's **Settings** tab) is
  the one that actually deploys — but obtaining it means going into the Render
  dashboard, and Eric's requirement is explicitly that deploys cost him zero
  dashboard time. So the manual-trigger route had no path that met the brief.

**The gate was never the flag.** It is that nothing reaches `main` unless Eric
asks for it. The actual failure mode — committing to `main` because that's
where HEAD happened to be sitting — is guarded by **switching back to `dev`
immediately after every merge**, and by checking `git branch --show-current`
before any commit. Both are cheap; neither depends on this flag.

**Flipping the flag needs a sync AND a push.** Render's *live* config is what
governs, not what `render.yaml` says on `main`. Turning auto-deploy back on
took three steps: push the change, call the Blueprint Sync Hook to apply it
(the one thing that hook genuinely does), then push a further commit — the
push that carried the change could not deploy itself, because at that instant
Render still had the old setting. Confirmed empirically both ways: after the
sync returned `201`, `/static/app.css` served the old build for 7 more minutes;
the next commit deployed in ~90s.

Render has since renamed this field to `autoDeployTrigger`. `autoDeploy` is
still honoured, but check the current
[blueprint spec](https://render.com/docs/blueprint-spec) before editing it.

**Verifying a deploy without the dashboard:** everything under `/static` is
outside the login gate, so `curl`-ing an asset and grepping for a string only
the new build contains proves *which build is serving* — the app's own routes
all answer `401` and can't distinguish builds. A route's existence is
checkable the same way: a real path answers `401`, a nonexistent one `404`.

If a hook URL is ever used again: it is a capability, like an API key. Never
commit it, never paste it anywhere public. Rollback lives in Render's
dashboard, not git.

---

### AOS vs BFA: a filter dimension, not a third program
AADA added a BFA alongside the AOS. Slate reports it in the **same Full-Time
export**, through the same funnel columns, so it is not a separate `Program` —
splitting it out would have meant duplicating every stage, layout and test for
a population that shares one pipeline.

Slate sends **no degree column**. It prefixes the emphasis string instead:
`"BFA - Acting for Film, Television & Theatre"` is the BFA, and the unprefixed
`"Acting for Film, Television & Theatre"` is AOS only. Confirmed with Eric
(2026-09-23) that these are **different degrees, not one emphasis under two
labels** — so `emphasis` is stored exactly as Slate sends it and `degree` is
derived *alongside* it (`programs.degree_of`) rather than parsed out of it.
Selecting Degree = BFA together with the unprefixed emphasis therefore returns
nothing, and that is correct: no such applicant exists.

Three filter dimensions result, and they are independent:
`emphasis` (what they study, raw), `degree` (AOS / BFA), `bfa_pathway`
(3-year / 4-year). Degree and pathway are **Full-Time only** — Summer's
emphasis column carries an unrelated vocabulary ("Focused Intensives-Musical
Theatre"), which is what `Program.has_degree` gates. Both dimensions are hidden
when the data has none, the same way Country already was, and neither offers a
"(none)" bucket: blank is ~97% of rows for pathway and a filter option holding
almost everything is noise.

`degree` backfills from the stored `emphasis` on connect (`db._backfill_degree`),
so existing rows got it without a re-upload.

**The 2026-09 layout APPENDED its column** (`Application BFA Pathway` at 21),
which means the older 2026-08 layout's anchors still match a new file. Layout
order is the only thing stopping the pathway being silently dropped —
`FT_2026_09` must stay first in `Program.layouts`. There is a test pinning this.

### Facet counts respect the other active filters
Every picker's counts are computed against whatever else is currently
filtered — pick FY 2026/27 and the Degree picker says how many AOS and BFA
people are *in that year*. They used to be whole-program totals, which made a
filtered page quietly contradict itself.

Each dimension is counted with its **own** selection removed
(`Filters.without`). That is not an optimisation, it is the thing that keeps a
multi-select usable: count a dimension against itself and every value the user
has not ticked reads 0, so a selection can never be widened again.

### One programme, one spelling
`programs.canonical_emphasis` collapses `Acting for Theatre, Film, and
Television` onto `Acting for Film, Television & Theatre` — same programme, word
order changed, previously two options in every filter and two rows in every
chart. `db._migrate_emphasis` rewrites stored rows on connect. Unlike term,
emphasis is not part of the dedup key, so this is a plain UPDATE.

Deliberately an **explicit alias map**, not a fuzzy word-set match: a
fingerprint that ignores word order would merge two programmes that genuinely
differ only by order. Add a line when Slate invents another spelling; never
widen it into a heuristic. The degree prefix is split off and re-attached, so
one entry covers the AOS and BFA spellings both.

### Overall funnel splits by degree
Under the headline funnel each stage also shows AOS and BFA counts
(`metrics.funnel_by`). Rows with a blank degree are **skipped, not pooled**, so
the two groups do not necessarily sum to the headline — deliberate, because a
third bar made of "we don't know" reads as a third programme. Each stage's rail is stacked into three
segments — AOS, BFA, and a grey "no degree recorded" — all measured against the
same base (everyone who started), so the segments together are exactly as long
as the unstacked bar and still match the total printed beside them.

The grey segment is not optional: ~21% of Full-Time rows have a blank emphasis,
so AOS + BFA alone would leave every bar visibly short of its own total. It is
inert grey on purpose — missing data, not a third programme.

BFA is the accent colour mixed toward ink rather than a fixed second hue. The
accent is user-switchable across seven colours and any fixed partner collides
with one of them; light-vs-dark of one hue survives all seven, and it is the
convention the bar charts already use for a sub-source. The colour dot on each
chip below the rail doubles as the key, so there is no separate legend.

### Tip popovers are positioned in JS, not guessed in CSS
A `.tip-pop` is 430px wide and absolutely positioned, and an absolutely
positioned box counts toward scrollable overflow **even while hidden** — which
put a horizontal scrollbar and a band of dead space on the right of every page.

Clipping does not fix it: an `overflow` value on `html` *or* `body` propagates
to the viewport and leaves the element itself `visible`, so neither clips its
own children. Both were tried. The popovers have to actually fit, so `app.js`
(`wireTipFlip`) measures each one on load, on resize and on hover, and flips it
leftward when it would run off. The old CSS-only guess
(`.grid2 section:last-child`) caught one of the four places a tip sits near the
right edge.

### "Started" is offered on both channel cards
`Which channels convert to X` and `Which channels make up X` withheld Started;
both now offer it, at Eric's request (2026-09-23), knowing the trade:

* On **make up** it is the genuinely useful case — of everyone who started,
  the share each channel touched (Google Paid 42%, Meta 31% on current data).
* On **convert** it is degenerate. `ft_stages` hard-codes `"started": True`, so
  every applicant row has reached it and every channel converts at exactly
  100.0%. The flat chart is expected output, not a bug. Don't "fix" it, and
  don't re-remove the option without asking.

The `/cost` page's own stage picker still excludes Started and was left alone —
out of scope for that request, but it is inconsistent with the overview cost
card, which offers every stage.

### Ad performance: embedded Looker Studio report, not API-connected
`/ads` shows Meta/Google ad performance via an `<iframe>` embed of a Looker
Studio report Eric already built — **not** a Google/Meta Ads API integration.
This app never touches ad-platform credentials or makes the request itself;
the browser loads the report directly from `lookerstudio.google.com`, so it is
the one page whose data does not come from this app's own database and the one
place client-side network calls to an external host happen at all.

Deliberately **not** wired to the app's date/program filter bar: Looker's own
native controls (campaign multi-select, date range) already work inside the
iframe with zero custom code, and testing showed the embed does **not**
support auto-resize (`postMessage` fires once on load with an empty height
field), so each report page pins a fixed iframe height (`AD_REPORT_PAGES` in
`main.py`) rather than trying to size to content.

Nav label landed on "Ad Performance" over "Meta & Google" or "Reports" — names
the funnel stage it's showing, matching how every other nav entry names a
view rather than a data source. Multiple report pages (more are coming) share
one nav entry with an internal tab bar (`?page=<key>`, same URL-driven pattern
as the Cost page's attribution/stage tabs) rather than one top-level nav entry
per page, so the segmented nav doesn't grow unbounded as pages are added —
adding a page is one entry in `AD_REPORT_PAGES`, not a nav/route change.

## Analytical decisions worth not re-litigating

- **Fiscal year is 1 Sept → 31 Aug**, and a date filter on app start date is
  applied **by default** to the newest year with data. Without it the app
  silently mixed intake years. "All time" is one click away, so it's a starting
  point rather than a hidden filter.
- **Three attribution lenses, as a user-facing toggle**: first touch (what
  found them), last touch (what touched them last — *not* reliably "what closed
  them", see the caveat below), any touch (everything they
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
- **Last touch is NOT "what closed them."** Retargeting keeps serving ads after
  someone converts unless the ad account excludes converters, and most don't.
  On this data **32% of submitted applicants get tagged again afterwards**
  (median 44 days later), and of admits whose last touch was paid, **91% of
  those touches landed after they had already submitted**. It is overwhelmingly
  Google PMax (6,260 of 6,559 post-submission paid pings); Meta barely does it
  (10.9% vs PMax's 52.8%), which is itself a finding — PMax is spending half its
  impressions on people who already applied. Surfaced as "Touches after they'd
  already applied" on UTM detail, and called out in the Last-touch toggle note.
  **Read paid and organic differently there:** on an organic channel a
  post-submission touch is the applicant choosing to come back (Google Organic
  is 58%, higher than Paid) — normal, not waste.
- **Paid channels only.** Organic, email, and direct mail carry real cost no ad
  platform exports. They show a dash, never `$0`.
- **Date filter honours whole months** for spend. A range inside one month counts
  that month entire; splitting it would be inventing daily figures.
- **August spend is missing** from the current files (Sept–Jul only) while the
  fiscal year runs through August.

## Mistakes already made once — don't repeat

- **`[hidden]` can be silently defeated by a class's own `display` rule.**
  `.uploading` and `.filelist` (both from the multi-upload commit) declared
  `display` unconditionally; a browser's `[hidden]{display:none}` default has
  the same specificity as a single class, and author CSS loads after the
  browser's own stylesheet — so the class won every time, and the "Importing…"
  spinner showed from first paint regardless of the attribute. Fixed with one
  global `[hidden]{display:none!important}` rule near the top of `app.css`
  rather than patching the two classes, since the same mistake is easy to make
  again in a future class.
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

- **Spend from TikTok, ChatGPT (Paid) and Microsoft Ads** is coming (Eric,
  2026-09-23; files not supplied yet). Channel-side: `TikTok` and
  `AI Referral (ChatGPT etc.)` already exist in `taxonomy.py`, but both sit
  past `PALETTE_SLOTS` (8) and draw grey, and neither is currently treated as
  paid. There is **no Microsoft/Bing paid channel at all** — `Bing` exists only
  as a sub-source of `Organic/Other Search`. Each platform also needs its own
  parser in `spend.py`. Adding paid channels means revisiting the 8-slot
  palette, so decide colours and paid/organic classification together.
- Summer media spend (Eric will supply separately; the uploader already accepts a
  program per file).
- Cohorting spend to each person's first-touch month, which would fix the
  date-blending caveat for deeper stages. Real work; only worth it if
  cost-per-admit/enrolled becomes a decision-grade number.
- No xlsx/pdf export of the current view.
- Direct-mail cost has no source file — would need manual entry.
