import sqlite3, json, os
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "jobs.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    hash        TEXT PRIMARY KEY,
    title       TEXT,
    company     TEXT,
    city        TEXT,
    url         TEXT,
    salary      TEXT,
    tags        TEXT,
    posted      TEXT,
    source      TEXT,
    first_seen  TEXT,
    last_seen   TEXT,
    status      TEXT DEFAULT 'active',
    applied     TEXT,
    ai_score    INTEGER,
    ai_reason   TEXT
);
CREATE INDEX IF NOT EXISTS idx_source   ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_status   ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_first    ON jobs(first_seen);
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert(conn, job):
    """insert a new job, or touch last_seen if it already exists. returns True if new."""
    h = job.hash()
    tags_json = json.dumps(job.tags, ensure_ascii=False) if job.tags else "[]"
    row = conn.execute("SELECT hash FROM jobs WHERE hash=?", (h,)).fetchone()
    if row:
        conn.execute(
            "UPDATE jobs SET last_seen=?, status='active' WHERE hash=?",
            (_now(), h))
        return False
    conn.execute(
        "INSERT INTO jobs (hash,title,company,city,url,salary,tags,posted,source,"
        "first_seen,last_seen,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (h, job.title, job.company, job.city, job.url,
         job.salary, tags_json, job.posted, job.source,
         now := _now(), now, "active"))
    return True


def upsert_many(conn, jobs):
    """returns (new_count, updated_count)"""
    new = updated = 0
    for j in jobs:
        if upsert(conn, j):
            new += 1
        else:
            updated += 1
    conn.commit()
    return new, updated


def set_ai_rank(conn, hash, score, reason):
    conn.execute("UPDATE jobs SET ai_score=?, ai_reason=? WHERE hash=?",
                 (score, reason, hash))
    conn.commit()


def get_pending_jobs(conn, min_score=None, site=None):
    """get jobs to apply to. filters: applied IS NULL/pending, optionally by ai_score and source."""
    sql = "SELECT * FROM jobs WHERE (applied IS NULL OR applied='pending') AND status='active'"
    args = []
    if min_score is not None:
        sql += " AND ai_score >= ?"
        args.append(min_score)
    if site:
        sql += " AND source=?"
        args.append(site)
    sql += " ORDER BY first_seen DESC"
    return conn.execute(sql, args).fetchall()


def mark_pending(conn, hash):
    conn.execute("UPDATE jobs SET applied='pending' WHERE hash=?", (hash,))
    conn.commit()


def mark_picked(conn, hashes):
    for h in hashes:
        conn.execute("UPDATE jobs SET applied='pending' WHERE hash=?", (h,))
    conn.commit()


def set_applied(conn, hash, status):
    conn.execute("UPDATE jobs SET applied=? WHERE hash=?", (status, hash))
    conn.commit()


def mark_expired_by_source(conn, source, live_hashes):
    """jobs with this source not in live_hashes -> status='expired'. returns count."""
    if not live_hashes:
        cur = conn.execute("UPDATE jobs SET status='expired' WHERE source=? AND status='active'", (source,))
        conn.commit()
        return cur.rowcount
    # ponytail: sqlite caps at 999 vars per query. find all active hashes for this
    # source, compute which ones to expire (not in live), then batch-update by hash.
    all_active = [r["hash"] for r in conn.execute(
        "SELECT hash FROM jobs WHERE source=? AND status='active'", (source,)).fetchall()]
    live_set = set(live_hashes)
    to_expire = [h for h in all_active if h not in live_set]
    total = 0
    for i in range(0, len(to_expire), 500):
        chunk = to_expire[i:i+500]
        placeholders = ",".join("?" * len(chunk))
        cur = conn.execute(
            f"UPDATE jobs SET status='expired' WHERE hash IN ({placeholders})", chunk)
        total += cur.rowcount
    conn.commit()
    return total


def find_new(conn, source=None):
    """return jobs with ai_score IS NULL and status='active', newest first."""
    if source:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE ai_score IS NULL AND status='active' "
            "AND source=? ORDER BY first_seen DESC", (source,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE ai_score IS NULL AND status='active' "
            "ORDER BY first_seen DESC").fetchall()
    return [_row_to_job(r) for r in rows]


def get_all_active(conn, source=None):
    if source:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status='active' AND source=? "
            "ORDER BY first_seen DESC", (source,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status='active' ORDER BY first_seen DESC"
        ).fetchall()
    return [_row_to_job(r) for r in rows]


def counts(conn):
    """return dict of status -> count"""
    rows = conn.execute(
        "SELECT status, COUNT(*) as c FROM jobs GROUP BY status").fetchall()
    return {r["status"]: r["c"] for r in rows}


def _row_to_job(r):
    from utils import Job
    tags = r["tags"]
    try:
        tags = json.loads(tags) if tags else []
    except (json.JSONDecodeError, TypeError):
        tags = []
    return Job(
        title=r["title"] or "", company=r["company"] or "", city=r["city"] or "",
        url=r["url"] or "", salary=r["salary"] or "", tags=tags,
        posted=r["posted"] or "", source=r["source"] or "")


if __name__ == "__main__":
    # self-check: insert fake jobs, verify round-trip
    from utils import Job
    test_db = os.path.join(os.path.dirname(__file__), "_test_jobs.db")
    if os.path.exists(test_db):
        os.remove(test_db)
    c = connect(test_db)

    j1 = Job("cpp dev", "snapp", "تهران", "https://jobinja.ir/jobs/1", source="jobinja")
    j2 = Job("python dev", "digikala", "تهران", "https://jobinja.ir/jobs/2", source="jobinja")
    new, updated = upsert_many(c, [j1, j2])
    assert new == 2 and updated == 0, f"expected 2 new, got {new}/{updated}"

    # re-upsert same jobs -> all touch
    new, updated = upsert_many(c, [j1, j2])
    assert new == 0 and updated == 2, f"expected 2 updated, got {new}/{updated}"

    # expire one
    expired = mark_expired_by_source(c, "jobinja", [j1.hash()])
    assert expired == 1, f"expected 1 expired, got {expired}"

    # counts
    counts_dict = counts(c)
    assert counts_dict.get("active", 0) == 1, f"expected 1 active, got {counts_dict}"
    assert counts_dict.get("expired", 0) == 1, f"expected 1 expired, got {counts_dict}"

    # find_new returns unranked jobs
    found = find_new(c)
    assert len(found) == 1 and found[0].title == "cpp dev"

    # ai rank + applied
    set_ai_rank(c, j1.hash(), 9, "great match")
    set_applied(c, j1.hash(), "applied")
    from utils import Job
    row = c.execute("SELECT ai_score, ai_reason, applied FROM jobs WHERE hash=?",
                     (j1.hash(),)).fetchone()
    assert row["ai_score"] == 9 and row["applied"] == "applied"

    c.close()
    os.remove(test_db)
    print("ok")
