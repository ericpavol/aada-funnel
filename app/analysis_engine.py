"""
AADA Funnel Analysis — CANONICAL REFERENCE LOGIC
=================================================
This module is the source of truth for how Slate "Ping Data" exports are parsed
and turned into the channel-by-stage funnel. If you build the app in TypeScript,
PORT THIS FILE and write tests that reproduce these exact numbers against the
files in ../sample_data/. Do not re-derive the logic from the spreadsheets — the
subtle parts below are easy to get wrong.

Validated outputs against sample_data/Ping Data - 2 Year ... .xlsx (8,436 rows):
  Stage totals:  Started 8436 | Submitted 1240 | Aud Requested 875 |
                 Aud Complete 460 | Admitted 350
  Google (Paid) n=3520, admits=229  (PMax n=3279/226, Search n=350/16, overlap 13)
  Google (Organic) n=1634, admits=264
  Meta (IG/FB) n=2757, admits=28  (Instagram n=1811/21, Facebook n=943/6,
     Untagged-broken-tag n=20/1  <- recovered {{site_source_name}} macro pings)
  Organic/Other Search n=205, admits=45 (Bing 31, Yahoo 9, ...)
  Spotify (Paid Audio) n=1  |  Partner/Referral n=2  |  Unresolved/Other n=3
Use these as regression tests. Channels are ANY-TOUCH so they overlap and do not
sum to the stage total; sub-sources likewise do not sum to their parent.
"""
import re
from collections import defaultdict

# Each UTM cell packs the person's FULL history as "TIMESTAMP-value" items,
# comma-separated, e.g.:  "2025-10-08 01:16:34-google, 2025-10-08 01:16:47-google"
# Timestamps ALIGN across the Source/Medium/Campaign/Content columns, so we can
# reconstruct ordered multi-touch paths by merging the four columns on timestamp.
_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})-(.*)$")


def parse_utm_cell(cell):
    """'ts-value, ts-value' -> list of (timestamp|None, value). Order preserved."""
    if cell is None:
        return []
    out = []
    for part in str(cell).split(","):
        part = part.strip()
        if not part:
            continue
        m = _TS.match(part)
        out.append((m.group(1), m.group(2).strip()) if m else (None, part))
    return out


def build_path(src, med, camp, cont):
    """Merge the 4 UTM columns into ONE ordered list of ping dicts {s,m,c,t,ts}.
    Pings are sorted chronologically (the raw cells are NOT pre-sorted)."""
    ev = defaultdict(dict)
    for field, cell in (("s", src), ("m", med), ("c", camp), ("t", cont)):
        for i, (ts, val) in enumerate(parse_utm_cell(cell)):
            key = ts if ts else f"__{field}{i}"
            ev[key][field] = val
            if ts:
                ev[key]["ts"] = ts
    pings = list(ev.values())
    pings.sort(key=lambda e: (0, e["ts"]) if "ts" in e else (1, ""))
    return pings


def classify(s, m, c):
    """One ping -> (channel, sub_source). Rules are ORDER-SENSITIVE.

    Key gotchas baked in here (do NOT change without data):
      * Campaign 'started_app' is Meta (source ig/fb, medium paid) RETARGETING,
        NOT Google. We classify on SOURCE first, never campaign name.
      * source google + medium 'search' (no campaign) = ORGANIC brand search.
        source google + medium cpc/paid OR campaign PMax*/Search* = PAID.
      * Slate logs the referring domain as the 'source' when a visit has no UTM,
        which is why Bing/Yahoo/ChatGPT show up as sources (they are organic).
    """
    s = (s or "").strip().lower()
    m = (m or "").strip().lower()
    c = (c or "").strip().lower()
    if not s and not m:
        return (None, None)
    if "tiktok" in s:
        return ("TikTok", None)
    if "google" in s:
        paid = m in ("cpc", "paid", "ppc") or "pmax" in c or c.startswith("search")
        if paid:
            if "pmax" in c:
                return ("Google (Paid)", "PMax")
            if c.startswith("search"):
                return ("Google (Paid)", "Search")
            return ("Google (Paid)", "Other paid")   # observed count: 0
        return ("Google (Organic)", None)
    if s in ("ig", "instagram"):
        return ("Meta Paid Social (IG/FB)", "Instagram")
    if s in ("fb", "facebook"):
        return ("Meta Paid Social (IG/FB)", "Facebook")
    if m in ("social", "reel") or "reel" in m:
        return ("Meta Paid Social (IG/FB)", "Other Meta")
    if s == "bing":            return ("Organic/Other Search", "Bing")
    if s in ("yahoo!", "yahoo"): return ("Organic/Other Search", "Yahoo")
    if s == "duckduckgo":      return ("Organic/Other Search", "DuckDuckGo")
    if s == "ecosia":          return ("Organic/Other Search", "Ecosia")
    if s == "yandex":          return ("Organic/Other Search", "Yandex")
    if s in ("mail.ru", "seznam mail", "th"): return ("Organic/Other Search", "Other search")
    if "chatgpt" in s:         return ("AI Referral (ChatGPT etc.)", "ChatGPT")
    if "claude" in s or "perplexity" in s: return ("AI Referral (ChatGPT etc.)", "Other AI")
    if s == "slate":           return ("Email/CRM (Slate)", None)
    if s == "email-hero":      return ("Email (Marketing)", "Email platform (email-hero)")
    if s == "gmail":           return ("Email (Marketing)", "Gmail")
    if s in ("email", "outlook.com", "yahoo! mail", "mail") or "email" in m:
        return ("Email (Marketing)", "Other email")
    if s == "dot" or "letter" in m: return ("Direct Mail/Print", "DOT (letter)")
    if s == "print" or m == "postcard": return ("Direct Mail/Print", "Print (postcard)")
    if s == "teenlife":        return ("Partner/Referral", "TeenLife")
    if s == "spotify":         return ("Spotify (Paid Audio)", None)   # paid audio ad, its own channel
    if s == "campwing":        return ("Partner/Referral", "Campwing")
    # '{{site_source_name}}' = broken Meta URL macro (medium=paid, real Meta campaign)
    # -> recover as Meta, sub-source "Untagged (broken tag)".
    if "site_source_name" in s:
        return ("Meta Paid Social (IG/FB)", "Untagged (broken tag)")
    return ("Unresolved/Other", None)


# ---- Funnel stage flags. Column indices are 0-based per DATA_SCHEMA.md ----
# FULL-TIME file columns: 3=Submitted date, 5=Audition Requested (Y/N),
#   7=Audition Complete (Y/N), 8=Admitted (Y/N). Everyone has a Started date.
def ft_stages(row):
    return {
        "started":   True,
        "submitted": row[3] is not None or row[5] == "Y",
        "aud_req":   row[5] == "Y",
        "aud_comp":  row[7] == "Y",
        "admitted":  row[8] == "Y",
    }

# SUMMER has no audition stage: 4=Application Status, 5=Most Recent Decision.
def summer_stages(row):
    status = row[4] or ""
    return {
        "started":   True,
        "submitted": status in ("Decided", "Awaiting Decision"),
        "accepted":  (row[5] or "") == "Accept",
    }


def person_channels(row, utm_idx):
    """Return the set of (channel, sub) a person touched (ANY-TOUCH, deduped).
    utm_idx = (source_col, medium_col, campaign_col, content_col)."""
    si, mi, ci, ti = utm_idx
    keys = set()
    for p in build_path(row[si], row[mi], row[ci], row[ti]):
        ch, sub = classify(p.get("s"), p.get("m"), p.get("c"))
        if ch is None:
            continue
        keys.add((ch, None))          # parent (combined) total
        if sub:
            keys.add((ch, sub))       # sub-source
    return keys


# ---------------------------------------------------------------------------
# METRICS — two lenses, both ANY-TOUCH (a person is counted in every channel
# they touched, so channel rows OVERLAP and do NOT sum to the stage total).
#   1) within-channel %  = stage_count / that channel's own Started count  (QUALITY)
#   2) stage penetration = stage_count / the whole stage's total           (PRESENCE)
# Association is not causation: people deeper in the funnel accumulate more
# pings, so penetration rises at later stages partly as an artifact. First-touch
# (first ping's channel) is the cleaner acquisition signal. Surface both.
# ---------------------------------------------------------------------------
def build_matrix(rows, utm_idx, stage_fn, stage_keys):
    members = defaultdict(set)        # (channel, sub) -> set of person indices
    for i, row in enumerate(rows):
        chans = person_channels(row, utm_idx)
        if not chans:
            members[("No UTM (untracked)", None)].add(i)
        for k in chans:
            members[k].add(i)
    flags = [stage_fn(r) for r in rows]
    totals = {k: sum(1 for f in flags if f[k]) for k in stage_keys}

    def counts(idxset):
        return {k: sum(1 for i in idxset if flags[i][k]) for k in stage_keys}

    matrix = {}
    for key, idxset in members.items():
        c = counts(idxset)
        matrix[key] = {
            "n": len(idxset),
            "counts": c,
            "within_pct": {k: (c[k] / len(idxset) if idxset else 0) for k in stage_keys},
            "penetration_pct": {k: (c[k] / totals[k] if totals[k] else 0) for k in stage_keys},
        }
    return {"totals": totals, "matrix": matrix}


if __name__ == "__main__":
    # quick self-test against the sample full-time file
    import openpyxl, os
    p = os.path.join(os.path.dirname(__file__), "..", "sample_data",
                     "Ping Data - 2 Year 20260706-112027.xlsx")
    rows = list(openpyxl.load_workbook(p)["Export"].iter_rows(values_only=True))[1:]
    res = build_matrix(rows, (15, 16, 17, 18), ft_stages,
                       ["started", "submitted", "aud_req", "aud_comp", "admitted"])
    print("Stage totals:", res["totals"])
    for k in [("Google (Paid)", None), ("Google (Paid)", "PMax"),
              ("Google (Paid)", "Search"), ("Google (Organic)", None),
              ("Meta Paid Social (IG/FB)", None),
              ("Meta Paid Social (IG/FB)", "Untagged (broken tag)"),
              ("Spotify (Paid Audio)", None)]:
        m = res["matrix"][k]
        print(f"{str(k):40s} n={m['n']:5d} admitted={m['counts']['admitted']}")
