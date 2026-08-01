"""Display taxonomy: canonical parent -> sub-source ordering for tables/charts.

The classification RULES live in analysis_engine.py (vendored, canonical). This
file only decides what order rows appear in and which parents own which subs.
Any channel/sub the engine emits that is missing here still renders -- it gets
appended at the end -- so a new rule can never silently drop a row.
"""

# Parent channels in report order, each with its sub-sources in report order.
TAXONOMY = [
    ("Google (Paid)", ["PMax", "Search", "Other paid"]),
    ("Google (Organic)", []),
    ("Meta Paid Social (IG/FB)", [
        "Instagram", "Facebook", "Other Meta", "Untagged (broken tag)",
    ]),
    ("Organic/Other Search", [
        "Bing", "Yahoo", "DuckDuckGo", "Ecosia", "Yandex", "Other search",
    ]),
    ("Email (Marketing)", [
        "Email platform (email-hero)", "Gmail", "Other email",
    ]),
    ("Email/CRM (Slate)", []),
    ("Direct Mail/Print", ["DOT (letter)", "Print (postcard)"]),
    ("AI Referral (ChatGPT etc.)", ["ChatGPT", "Other AI"]),
    ("Partner/Referral", ["TeenLife", "Campwing"]),
    ("TikTok", []),
    ("Spotify (Paid Audio)", []),
    ("Unresolved/Other", []),
    # Always last, and never dropped -- see CLAUDE.md golden rules.
    ("No UTM (untracked)", []),
]

NO_UTM = "No UTM (untracked)"
UNRESOLVED = "Unresolved/Other"

_PARENT_ORDER = {name: i for i, (name, _) in enumerate(TAXONOMY)}


def ordered_rows(present_keys):
    """Order (channel, sub) keys for display.

    `present_keys` is any iterable of (channel, sub_or_None). Returns the keys
    that are present, sorted parent-then-sub per TAXONOMY, with anything
    unrecognised appended alphabetically at the end (so new engine output is
    visible rather than lost).
    """
    present = set(present_keys)
    out = []
    for parent, subs in TAXONOMY:
        if (parent, None) in present:
            out.append((parent, None))
        for sub in subs:
            if (parent, sub) in present:
                out.append((parent, sub))
    leftover = sorted(
        present.difference(out),
        key=lambda k: (_PARENT_ORDER.get(k[0], 999), k[0], k[1] or ""),
    )
    return out + leftover


SUB_SEP = " › "          # "Google (Paid) › PMax"


def sub_name(channel, sub):
    """Display name that keeps a sub-source attached to its parent.

    "PMax" alone is ambiguous in a flat list; "Google (Paid) › PMax" is not.
    """
    return "%s%s%s" % (channel, SUB_SEP, sub) if sub else channel


def split_sub_name(name):
    """"Google (Paid) › PMax" -> ("Google (Paid)", "PMax")."""
    if SUB_SEP in name:
        parent, sub = name.split(SUB_SEP, 1)
        return parent, sub
    return name, None


def canonical_order_index(names):
    """Rank display names (parents and/or "parent › sub") in taxonomy order.

    Used to assign chart colours, so a series keeps its colour regardless of
    which other series happen to be on screen.
    """
    order = {}
    i = 0
    for parent, subs in TAXONOMY:
        order[parent] = i
        i += 1
        for sub in subs:
            order[sub_name(parent, sub)] = i
            i += 1
    return sorted(names, key=lambda n: (order.get(n, 10 ** 6), n))


def unknown_taxonomy_keys(present_keys):
    """Keys the engine produced that TAXONOMY does not describe (drift check)."""
    known = set()
    for parent, subs in TAXONOMY:
        known.add((parent, None))
        for sub in subs:
            known.add((parent, sub))
    return sorted(set(present_keys) - known)
