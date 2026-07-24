"""TF-IDF ranker: local, free ranking that learns from jobs the LLM already scored.

cold start: if <20 labeled jobs in db, falls back to keyword_filter.
otherwise: build tf-idf from labeled jobs, compute profile vector
(weighted mean of job vectors by ai_score), rank new jobs by cosine similarity.
"""
import json
import os
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

import db
from matcher import keyword_filter

# ponytail: normalize persian+english skill variants to canonical tokens.
# without this, "C++" / "cpp" / "سی‌پلاس‌پلاس" are 3 different words to TF-IDF.
# mapped tokens get injected INTO the text blob before vectorizing.
SYNONYMS = {
    "cpp": ["cpp", "c++", "سی\u200cپلاس\u200cپلاس", "سی پلاس پلاس", "سي پلاس پلاس"],
    "python": ["python", "پایتون", "پايتون"],
    "backend": ["backend", "back-end", "back end", "بک\u200cاند", "بک اند", "سرور", "server-side", "سرورسایت"],
    "frontend": ["frontend", "فرانت\u200cاند", "فرانت اند", "front-end"],
    "devops": ["devops", "دواپس", "kubernetes", "docker", "k8s", "ci/cd"],
    "golang": ["golang", "گولنگ"],
    "javascript": ["javascript", "جاوااسکریپت", "جاوا اسکریپت"],
    "react": ["reactjs", "react.js", "ری\u200cاکت", "ری اکت"],
    "node": ["nodejs", "node.js", "نود"],
    "java": ["java", "جاوا"],
    "csharp": ["c#", "csharp", ".net", "سی\u200cشارپ", "سی شارپ"],
    "php": ["php", "پی\u200cاچ\u200cپی"],
    "wordpress": ["wordpress", "وردپرس", "ووردپرس"],
    "flutter": ["flutter", "فلاتر"],
    "angular": ["angular", "انگولار"],
    "vue": ["vuejs", "vue.js", "vue"],
    "fullstack": ["fullstack", "full-stack", "فول\u200cاستک", "فول استک"],
    "software": ["software", "نرم\u200cافزار", "نرم افزار", "برنامه\u200cنویس", "برنامه نویس", "developer", "توسعه\u200cدهنده", "توسعه دهنده"],
    "remote": ["remote work", "دورکار", "دورکاری", "remote"],
    "tehran": ["tehran", "تهران"],
}

# build reverse lookup with word boundaries
# ponytail: substring match would make "go" hit "google", "js" hit "json", etc.
# use regex word boundary per variant.
import re
_VARIANT_MAP = {}  # canonical -> compiled regex
for canonical, variants in SYNONYMS.items():
    patterns = []
    for v in variants:
        # use lookbehind/lookahead for non-word chars or string edges
        # for persian, \b doesn't work, so use explicit boundaries
        patterns.append(re.escape(v.lower()))
    _VARIANT_MAP[canonical] = re.compile(r"(?:^|[\s,/&|()؛،])(" + "|".join(patterns) + r")(?:$|[\s,/&|()؛،.])", re.IGNORECASE)


def _normalize_text(text):
    """inject canonical tokens for any synonym found in text (word-boundary match)."""
    if not text:
        return ""
    found = set()
    for canonical, rx in _VARIANT_MAP.items():
        if rx.search(" " + text.lower() + " "):  # pad so boundaries match at edges
            found.add(canonical)
    # also inject auto-discovered high-signal tags if they appear in the text
    if _EXTRA_TAGS:
        low = text.lower()
        for tag in _EXTRA_TAGS:
            if tag in low:
                found.add(tag)
    extra = " ".join(sorted(found))
    return f"{text} {extra}".strip() if extra else text


# ponytail: learned tags persisted to disk so they survive between runs.
# TF-IDF already builds vocab from text; SYNONYMS normalizes persian/english
# variants. _discover_extra_tags mines labeled data for new high-signal tokens,
# saves them to learned_tags.json, and reloads + extends on every run.
_EXTRA_TAGS = None
_LEARNED_PATH = os.path.join(os.path.dirname(__file__), "learned_tags.json")


def _load_learned():
    try:
        with open(_LEARNED_PATH, encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_learned(tags):
    with open(_LEARNED_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(tags), f, ensure_ascii=False, indent=2)


def _discover_extra_tags(labeled_rows):
    """find tokens that appear mostly in high-scored jobs but aren't in SYNONYMS.
    loads previously learned tags, discovers new ones from fresh labeled data,
    saves the union back to disk. O(n) over labeled jobs."""
    global _EXTRA_TAGS
    if _EXTRA_TAGS is not None:
        return _EXTRA_TAGS

    learned = _load_learned()

    if not labeled_rows or len(labeled_rows) < 20:
        _EXTRA_TAGS = learned
        return _EXTRA_TAGS

    high, low = [], []
    for r in labeled_rows:
        score = r["ai_score"] or 5
        text = _job_text(r)
        if score >= 7:
            high.append(text.lower())
        elif score <= 4:
            low.append(text.lower())

    if not high:
        _EXTRA_TAGS = learned
        return learned

    # ponytail: simple word frequency ratio. if a word appears in high-scored
    # jobs much more than low-scored, it's a signal. ceiling: this is a heuristic,
    # not a statistical test. upgrade to chi2/MI if we get >500 labeled jobs.
    from collections import Counter
    high_counts = Counter()
    for t in high:
        high_counts.update(w for w in t.split() if len(w) > 2)
    low_counts = Counter()
    for t in low:
        low_counts.update(w for w in t.split() if len(w) > 2)

    known = set(SYNONYMS.keys())
    new_tags = set()
    for word, hc in high_counts.items():
        if word in known:
            continue
        lc = low_counts.get(word, 0)
        if hc >= 2 and hc >= lc * 3:
            new_tags.add(word)

    all_tags = learned | new_tags
    if new_tags:
        _save_learned(all_tags)
        print(f"  [tfidf] learned {len(new_tags)} new tag(s): {', '.join(sorted(new_tags))}")

    _EXTRA_TAGS = all_tags
    return all_tags


def _job_text(row):
    """blob of text for a db job row: title + company + city + tags, normalized."""
    tags = row["tags"] or "[]"
    try:
        tags = " ".join(json.loads(tags)) if tags else ""
    except (json.JSONDecodeError, TypeError):
        tags = ""
    raw = " ".join([row["title"] or "", row["company"] or "", row["city"] or "", tags]).strip()
    return _normalize_text(raw)


def _job_text_obj(job):
    """text blob for a Job object, normalized."""
    raw = " ".join([job.title, job.company, job.city, " ".join(job.tags)]).strip()
    return _normalize_text(raw)


def rank_tfidf(jobs, config, db_path=None):
    """rank jobs by cosine similarity to the user's scored profile.
    returns list of dicts (dashboard-ready), same shape as keyword_filter.

    db_path: override db location (for testing)."""
    conn = db.connect(db_path) if db_path else db.connect()
    labeled = conn.execute("SELECT * FROM jobs WHERE ai_score IS NOT NULL").fetchall()
    conn.close()

    if len(labeled) < 20:
        print(f"not enough labeled data for tfidf ({len(labeled)}/20), falling back to keyword")
        return keyword_filter(jobs, config)

    # build corpus from labeled jobs
    _discover_extra_tags(labeled)
    labeled_texts = [_job_text(r) for r in labeled]
    vec = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=1,
                          sublinear_tf=True)
    labeled_matrix = vec.fit_transform(labeled_texts)

    # profile vector: weighted mean of labeled job vectors (weight by ai_score)
    scores = np.array([r["ai_score"] or 5 for r in labeled], dtype=float)
    weights = scores / scores.sum()
    profile_vector = (labeled_matrix.T @ weights).reshape(1, -1)

    # vectorize new jobs with same vocabulary
    new_texts = [_job_text_obj(j) for j in jobs]
    new_matrix = vec.transform(new_texts)

    # cosine similarity: each new job vs profile vector
    sims = cosine_similarity(new_matrix, profile_vector).flatten()

    # normalize to 1-10 score
    if sims.max() == sims.min():
        scores_out = np.full(len(sims), 5)
    else:
        scores_out = 1 + 9 * (sims - sims.min()) / (sims.max() - sims.min())

    ranked = sorted(zip(jobs, sims, scores_out), key=lambda x: -x[1])[:50]
    out = []
    for job, sim, score in ranked:
        out.append({
            "title": job.title, "company": job.company, "city": job.city,
            "url": job.url, "score": round(score), "reason": f"tfidf sim={sim:.3f}",
        })
    return out


if __name__ == "__main__":
    # self-check: fake db with labeled jobs, verify ranking works
    from utils import Job
    test_db = os.path.join(os.path.dirname(__file__), "_test_ranker.db")
    if os.path.exists(test_db):
        os.remove(test_db)
    # ponytail: redirect learned_tags.json to temp so self-test doesn't pollute real file
    # when run as __main__, module globals are __main__'s, not ranker's. patch both.
    import sys
    _test_learned = os.path.join(os.path.dirname(__file__), "_test_learned_tags.json")
    sys.modules["__main__"]._LEARNED_PATH = _test_learned
    if "ranker" in sys.modules:
        sys.modules["ranker"]._LEARNED_PATH = _test_learned
    _EXTRA_TAGS = None  # reset cache so it reloads from temp path
    c = db.connect(test_db)

    # 20 labeled jobs: 10 cpp (high score), 10 sales (low score)
    for i in range(10):
        j = Job(f"cpp developer {i}", "techco", "تهران",
                f"https://jobinja.ir/cpp/{i}", tags=["c++", "backend"], source="jobinja")
        db.upsert(c, j)
        db.set_ai_rank(c, j.hash(), 8 + i % 3, "cpp match")
    for i in range(10):
        j = Job(f"sales manager {i}", "retail", "یزد",
                f"https://jobinja.ir/sales/{i}", tags=["فروش"], source="jobinja")
        db.upsert(c, j)
        db.set_ai_rank(c, j.hash(), 3, "not a match")
    c.commit()
    c.close()

    new_jobs = [
        Job("senior cpp engineer", "snapp", "تهران", "https://jobinja.ir/new/1", tags=["c++", "systems"]),
        Job("python backend dev", "digikala", "تهران", "https://jobinja.ir/new/2", tags=["python"]),
        Job("retail sales rep", "shop", "یزد", "https://jobinja.ir/new/3", tags=["فروش"]),
    ]
    result = rank_tfidf(new_jobs, {}, db_path=test_db)
    assert len(result) == 3, f"expected 3, got {len(result)}"
    assert result[0]["title"] == "senior cpp engineer", f"expected cpp first, got {result[0]['title']}"
    assert result[0]["score"] >= result[-1]["score"], "top score should be >= bottom"
    print(f"ok: top={result[0]['title']} score={result[0]['score']}, bottom={result[-1]['title']} score={result[-1]['score']}")

    # cold start: <20 labeled -> fallback to keyword
    os.remove(test_db)
    c = db.connect(test_db)
    j = Job("only one", "co", "تهران", "https://jobinja.ir/x", source="jobinja")
    db.upsert(c, j)
    db.set_ai_rank(c, j.hash(), 10, "test")
    c.commit()
    c.close()
    result2 = rank_tfidf(new_jobs, {"tags": ["python"], "cities": [], "job_types": []}, db_path=test_db)
    print(f"cold start: fell back to keyword, {len(result2)} matches")
    os.remove(test_db)
    _tp = os.path.join(os.path.dirname(__file__),("_test_learned_tags.json"))
    if os.path.exists(_tp):
        os.remove(_tp)
    print("tfidf ranker ok")
