#!/usr/bin/env python
"""Load the sample Slate exports into the local database.

    python seed.py            # ingest both sample files (idempotent)
    python seed.py --reset     # wipe stored data first

Idempotent by design: run it twice and the second run reports 0 new rows.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import db, ingest  # noqa: E402

HANDOFF = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "aada-funnel-app-handoff"))
SAMPLES = [
    ("ft", os.path.join(HANDOFF, "sample_data", "Ping Data - 2 Year 20260706-112027.xlsx")),
    ("summer", os.path.join(HANDOFF, "sample_data", "Ping Data - Summer 20260706-111924.xlsx")),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="clear stored data first")
    ap.add_argument("--db", default=os.environ.get("AADA_DB", db.DEFAULT_DB))
    args = ap.parse_args()

    conn = db.connect(args.db)
    if args.reset:
        db.reset(conn)
        print("cleared existing data")

    for program, path in SAMPLES:
        if not os.path.exists(path):
            print("MISSING %s" % path)
            continue
        with open(path, "rb") as fh:
            digest = ingest.sha256_of(fh.read())
        res = ingest.ingest(conn, path, program, os.path.basename(path), digest)
        print("\n%s  <-  %s" % (program.upper(), os.path.basename(path)))
        print("   rows=%d  new=%d  updated=%d  pings_new=%d  pings_dup=%d"
              % (res["row_count"], res["applicants_new"], res["applicants_updated"],
                 res["pings_new"], res["pings_duplicate"]))
        for w in res["warnings"]:
            print("   ! %s" % w)
        for u in res["new_unknown_utms"]:
            print("   ? unclassified: source=%r medium=%r campaign=%r (%d pings)"
                  % (u["source"], u["medium"], u["campaign"], u["pings"]))

    print("\ndatabase: %s" % args.db)
    conn.close()


if __name__ == "__main__":
    main()
