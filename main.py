import json, os, sys
from utils import Job
from scrapers import SCRAPERS
from matcher import rank_jobs, keyword_filter
from ranker import rank_tfidf
import db

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def load_config():
    if not os.path.exists(CONFIG_PATH):
        print("no config found, run wizard first: python wizard.py")
        sys.exit(1)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def scrape_all(config, full=False):
    """run all scrapers. full=True scrapes all pages (slow). default: page 1 of each (fast)."""
    proxy = config.get("proxy", "")
    all_jobs = []
    for name, cls in SCRAPERS.items():
        s = cls(proxy=proxy or None)
        try:
            if hasattr(s, "scrape_all_pages") and full:
                jobs = s.scrape_all_pages(config)
            elif hasattr(s, "fetch_jobs"):
                if name == "irantalent":
                    data = s.fetch_jobs()
                    jobs = s._parse_posts(data)
                else:
                    posts = s.fetch_jobs(size=20, query=" ".join(config.get("tags", [""])))
                    jobs = s._parse_posts(posts)
            else:
                tags = config.get("tags", [])
                query = tags[0] if tags else ""
                # ponytail: just search first tag, ai will filter the rest
                url = s.search_url(query=query)
                html = s.fetch(url)
                jobs = s.parse(html)
            all_jobs.extend(jobs)
            print(f"  {name}: {len(jobs)} jobs")
        except Exception as e:
            print(f"  {name}: FAILED {e}")
    return all_jobs


def show_dashboard(ranked):
    """show top jobs in terminal, let user pick ranges"""
    if not ranked:
        print("no jobs found")
        return []

    print(f"\n=== top {len(ranked)} jobs ===\n")
    for i, j in enumerate(ranked, 1):
        if isinstance(j, dict):
            print(f"{i}. {j.get('title','')} @ {j.get('company','')} | {j.get('city','')}")
            if j.get("score"):
                print(f"   score: {j['score']}/10 - {j.get('reason','')}")
            print(f"   {j.get('url','')}")
        else:
            # unranked Job object
            print(f"{i}. {j.title} @ {j.company} | {j.city}")
            print(f"   {j.url}")
        print()

    print("pick jobs to apply, e.g. 1-5,8,12 or 'all' or 'q' to quit")
    choice = input("> ").strip()
    if choice.lower() in ("q", "quit", ""):
        return []
    if choice.lower() == "all":
        return ranked

    picked = []
    for part in choice.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            for n in range(int(a), int(b) + 1):
                if 1 <= n <= len(ranked):
                    picked.append(ranked[n - 1])
        elif part.isdigit():
            n = int(part)
            if 1 <= n <= len(ranked):
                picked.append(ranked[n - 1])
    return picked


def run_once(config, full=False, no_ai=False, refresh=False, tfidf=False, rerank=False):
    """one scrape -> db -> rank -> dashboard cycle. returns a summary dict."""
    conn = db.connect()
    summary = {"scraped": 0, "new": 0, "updated": 0, "expired": 0, "picked": 0}

    print("scraping all sites..." + (" [full]" if full else ""))
    jobs = scrape_all(config, full=full)
    summary["scraped"] = len(jobs)
    print(f"\ntotal scraped: {len(jobs)} jobs")

    new_n, updated_n = db.upsert_many(conn, jobs)
    summary["new"] = new_n
    summary["updated"] = updated_n
    print(f"new: {new_n}, updated (touched): {updated_n}")

    # stale cleanup only in full mode (page 1 doesn't see all jobs)
    if full:
        for name in SCRAPERS:
            live = [j.hash() for j in jobs if j.source == name]
            if live:
                expired = db.mark_expired_by_source(conn, name, live)
                if expired:
                    print(f"  {name}: {expired} jobs expired (disappeared)")
                    summary["expired"] += expired

    if rerank:
        # ponytail: --rerank skips scraping, pulls unranked jobs from db in
        # batches of 25, sends each batch to ai, stores scores. lets you
        # backfill ai scores on a db you already scraped.
        unranked = db.find_new(conn)
        if not unranked:
            print("all jobs already have ai scores")
            conn.close()
            return summary
        print(f"\n{len(unranked)} unranked jobs in db, batching to ai...")
        total = 0
        for i in range(0, len(unranked), 25):
            batch = unranked[i:i+25]
            print(f"  batch {i//25 + 1}/{(len(unranked)+24)//25}: {len(batch)} jobs")
            ranked = rank_jobs(batch, config)
            for r in ranked:
                if isinstance(r, dict) and r.get("url"):
                    h = Job("", "", "", r["url"]).hash()
                    db.set_ai_rank(conn, h, r.get("score"), r.get("reason", ""))
            total += len(ranked)
        print(f"\nranked {total} jobs. run without --rerank to see dashboard.")
        conn.close()
        return summary

    if refresh:
        c = db.counts(conn)
        print(f"db: {c.get('active',0)} active, {c.get('expired',0)} expired")
        conn.close()
        return summary

    # ponytail: skip AI ranking if no new jobs — saves API calls in --loop
    new_jobs = db.find_new(conn)
    if not new_jobs:
        print("no new jobs since last run")
        c = db.counts(conn)
        print(f"db: {c.get('active',0)} active, {c.get('expired',0)} expired")
        conn.close()
        return summary

    if tfidf:
        print("\nranking with tfidf...")
        ranked = rank_tfidf(new_jobs, config)
        for r in ranked:
            if isinstance(r, dict) and r.get("url"):
                # ponytail: hash must match the one stored in db — use the same
                # url the job was scraped with, strip nothing
                h = Job("", "", "", r["url"]).hash()
                # verify the row exists before writing (url params can cause mismatch)
                exists = conn.execute("SELECT 1 FROM jobs WHERE hash=?", (h,)).fetchone()
                if exists:
                    db.set_ai_rank(conn, h, r.get("score"), r.get("reason", ""))
                else:
                    # try matching by url prefix (strip query string)
                    base_url = r["url"].split("?")[0]
                    row = conn.execute("SELECT hash FROM jobs WHERE url LIKE ? || '%' LIMIT 1", (base_url,)).fetchone()
                    if row:
                        db.set_ai_rank(conn, row["hash"], r.get("score"), r.get("reason", ""))
    elif no_ai:
        ranked = keyword_filter(new_jobs, config)
    else:
        print("\nsending to ai for ranking...")
        ranked = rank_jobs(new_jobs, config)
        # store ai scores in db
        for r in ranked:
            if isinstance(r, dict) and r.get("url"):
                h = Job("", "", "", r["url"]).hash()
                db.set_ai_rank(conn, h, r.get("score"), r.get("reason", ""))

    picked = show_dashboard(ranked)
    if picked:
        print(f"\nselected {len(picked)} jobs")
        picked_hashes = []
        for j in picked:
            if isinstance(j, dict):
                print(f"  - {j.get('title','')} @ {j.get('company','')}")
                h = Job("", "", "", j["url"]).hash()
            else:
                print(f"  - {j.title} @ {j.company}")
                h = j.hash()
            picked_hashes.append(h)
        db.mark_picked(conn, picked_hashes)
        summary["picked"] = len(picked_hashes)
        print(f"marked {len(picked_hashes)} jobs as pending. run 'python apply.py --apply --picks' to apply.")

    conn.close()
    return summary


def main():
    config = load_config()
    args = sys.argv[1:]

    if "--loop" in args:
        import time
        from datetime import datetime
        interval = config.get("interval_minutes", 30) * 60
        while True:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n{'='*50}")
            print(f"  jobfiner loop — {ts}")
            print(f"{'='*50}")
            fmt_args = {"full": "--full" in args, "no_ai": "--no-ai" in args, "tfidf": "--tfidf" in args}
            s = run_once(config, **fmt_args)
            print(f"\n--- summary: {s['new']} new, {s['updated']} updated, "
                  f"{s['expired']} expired, {s['picked']} picked ---")
            print(f"sleeping {interval//60} min...\n")
            time.sleep(interval)
        return

    run_once(config,
             full="--full" in args,
             no_ai="--no-ai" in args,
             refresh="--refresh" in args,
             tfidf="--tfidf" in args,
             rerank="--rerank" in args)


if __name__ == "__main__":
    main()
