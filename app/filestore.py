"""Original upload files, kept so they can be downloaded again later.

Every file that ingests successfully is written next to the database (on Render
that is the persistent disk at /var/data) and listed in `stored_files`. Files
are named by their SHA-256, so the same file uploaded twice is stored once and
no user-supplied name ever reaches the filesystem. Eric asked for this on
2026-10-16; before it, uploads were parsed and thrown away.

The files hold real applicant records. They are served only through the app,
behind the same login as every other page, and are never sent anywhere else.
"""
import datetime as _dt
import os
import re
import shutil

_SHA = re.compile(r"^[0-9a-f]{64}$")


def folder(db_path):
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "uploads")


def _path(db_path, sha):
    if not _SHA.match(sha or ""):
        raise ValueError("bad file id")
    return os.path.join(folder(db_path), sha)


def save(conn, db_path, sha, filename, kind, payload):
    """Write the file (once per distinct content) and record it."""
    os.makedirs(folder(db_path), exist_ok=True)
    path = _path(db_path, sha)
    if not os.path.exists(path):
        tmp = path + ".part"
        with open(tmp, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    conn.execute(
        "INSERT OR IGNORE INTO stored_files (sha256, filename, kind, size, saved_at)"
        " VALUES (?,?,?,?,?)",
        (sha, filename, kind, len(payload),
         _dt.datetime.now().isoformat(timespec="seconds")))
    conn.commit()


def listing(conn, db_path):
    rows = conn.execute("SELECT * FROM stored_files ORDER BY id DESC").fetchall()
    total = sum(r["size"] for r in rows)
    try:
        du = shutil.disk_usage(os.path.dirname(os.path.abspath(db_path)))
        free = du.free
    except OSError:
        free = None
    return {"files": rows, "total": total, "free": free}


def get(conn, db_path, file_id):
    """-> (row, path) or (None, None)."""
    row = conn.execute("SELECT * FROM stored_files WHERE id=?", (file_id,)).fetchone()
    if not row:
        return None, None
    path = _path(db_path, row["sha256"])
    return (row, path) if os.path.exists(path) else (None, None)


def delete(conn, db_path, file_id):
    """Remove the saved copy. The data it loaded stays in the database."""
    row = conn.execute("SELECT * FROM stored_files WHERE id=?", (file_id,)).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM stored_files WHERE id=?", (file_id,))
    conn.commit()
    try:
        os.remove(_path(db_path, row["sha256"]))
    except FileNotFoundError:
        pass
    return True
