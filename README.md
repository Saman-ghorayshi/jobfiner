# jobfiner

scrapes 5 persian job sites, stores them in a sqlite db, ranks top matches
(AI / TF-IDF / keyword), you pick ranges in a terminal dashboard, it auto-applies
with selenium using your saved logins + anti-bot human behavior + SMS verification.

## sites

- jobinja.ir — paginates ~700 pages (~14k jobs with --full)
- jobvision.ir (json api) — paginates all ~41k jobs with --full
- karboom.io — paginates ~17 pages (~320 jobs)
- irantalent.com (json api) — public api returns latest 7 only, no pagination
- daneshkar.net — ~19 jobs per page, ?page=N doesn't work (client-side RSC scroll)

## demo

run `python main.py` and you get a terminal dashboard like this:

```
jobfiner — 47 new jobs, 312 total in db
  1  [9.2] Senior Python Developer @ Snapp         (jobinja)    تهران
  2  [8.7] Backend Engineer @ Digikala              (jobvision)  تهران
  3  [7.5] C++ Developer @ Asriran                  (jobinja)    تهران
  4  [6.1] Python Intern @ IranTalent               (irantalent) تهران
  ...

pick ranges (e.g. 1-5,8,12) or 'q' to quit:
```

AI scores each job 0-10 based on your tags (python, cpp, etc).
TF-IDF mode learns from your past scores — no API cost after enough data.

## setup

```
pip install httpx[socks] beautifulsoup4 lxml selenium scikit-learn numpy
python wizard.py    # answer questions about your preferences
python main.py      # scrape, rank, pick
```

## usage

```
python main.py              # fast: page 1 of each site, ai rank, dashboard
python main.py --full       # scrape ALL pages (slow, ~15 min first run)
python main.py --refresh    # scrape page 1, update db, report counts, no dashboard
python main.py --no-ai      # keyword filter instead of ai ranking
python main.py --tfidf      # local tf-idf ranking (free, learns from past scores)
python main.py --loop        # keep running every interval_minutes, timestamp + summary each run
python main.py --full --no-ai  # all pages, keyword filter
python main.py --rerank        # backfill ai scores on existing db jobs (no scrape)
```

### ranking options

| flag       | what it does                                              | cost  |
|------------|----------------------------------------------------------|-------|
| (default)  | AI ranking via your configured provider (25 in, top 10) | api   |
| --no-ai    | keyword filter using your tags                            | free  |
| --tfidf    | tf-idf cosine similarity, learns from previously scored jobs | free |

tf-idf falls back to keyword filter when <20 labeled jobs in db (cold start).
as you run AI ranking and it scores jobs, learned_tags.json grows with
auto-discovered high-signal persian/english tokens. no AI needed for ranking
after enough data — just use --tfidf.

## auto-apply

### 1. login to each site (once per site)

```
python apply.py --login jobinja    # opens chrome, you log in, cookies + localStorage saved
python apply.py --login jobvision  # jobvision uses OIDC — saves localStorage tokens
python apply.py --login karboom    # repeat for each site
```

cookies + localStorage are saved to `cookies/<site>.json` (gitignored).
jobvision's auth is in localStorage (OIDC access token), not just cookies —
browser.py saves/restores per-origin localStorage on the correct domain.

### 2. run recon to inspect the apply form

```
python recon.py jobinja      # opens browser, logs in, navigates to a job,
python recon.py jobvision    # clicks apply, dumps ALL form fields + screenshot
```

recon tries saved cookies first, skips login if they work. for multi-step
apply flows (jobvision has a modal → proceed button → form), it clicks through
up to 3 layers and dumps fields at each step.

### 3. apply

```
python apply.py --apply            # applies to all unapplied + pending jobs in db
python apply.py --apply --dry-run  # show what would happen, don't click
python apply.py --apply --min-score 7    # only apply to ai_score >= 7
python apply.py --apply --picks    # only apply to dashboard picks (applied='pending')
python apply.py --apply --site jobinja   # only apply to jobs from one site
```

### 4. profile.md

edit `profile.md` (gitignored) with your phone, cv path, about me, and
answers to common apply form questions. the apply script reads this to fill forms.

### 5. SMS verification (jobinja)

jobinja sends an SMS code on first apply. to handle this automatically,
configure SMS in config.json:

```json
{"sms": {"method": "adb",
         "telegram": {"api_id": 1, "api_hash": "x", "bot_token": "x"},
         "webhook_port": 5000,
         "sender_filter": "jobinja"}}
```

three backends, tried in order:
1. ADB (USB cable, android phone, offline) — reads SMS inbox directly
2. telegram bot (phone forwards SMS to telegram, script reads it)
3. webhook (phone posts SMS to local HTTP server, same wifi)

or pre-verify your phone on jobinja in browser first (option B from the plan),
then apply.py won't trigger SMS.

## how it works

1. wizard asks: tags, cities, job types, ai provider, proxy, cv path, interval
2. main.py scrapes sites, upserts into `jobs.db` (sqlite, stdlib)
3. new jobs (not seen before) are sent to ai/tfidf/keyword, ranked
4. dashboard shows them numbered, you type ranges like `1-5,8,12`
5. picked jobs are marked `applied='pending'` in db
6. `--full` scrapes all pages and marks disappeared jobs as expired
7. `--loop` repeats: timestamp header, scrape, rank, summary, sleep
8. `apply.py --apply` opens each picked job, fills form (human typing, delays), submits
9. if SMS verification appears, sms.py reads the code and fills it

## db

`jobs.db` (sqlite, stdlib `sqlite3`). one table:

- `hash` (sha256 of url, 12 chars) — primary key
- `first_seen` / `last_seen` — when we first/last scraped it
- `status` — active / expired
- `applied` — null / pending / applied / failed
- `ai_score` / `ai_reason` — from ranking

## ai providers

all use openai-compatible endpoints, no sdk needed:

- **gemini** (free tier): key at https://aistudio.google.com/apikey
- **openai**: any openai-compatible endpoint
- **anthropic**: claude models
- **openrouter** (free models, many providers): key at https://openrouter.ai/keys

## config

saved to `config.json` by the wizard:

```json
{
  "tags": ["python", "cpp"],
  "cities": ["تهران"],
  "job_types": ["تمام وقت"],
  "ai": {"provider": "openrouter", "api_key": "...", "model": "openai/gpt-oss-20b:free"},
  "proxy": "socks5://127.0.0.1:10808",
  "interval_minutes": 30,
  "auto_apply": false,
  "cv_path": "cv.pdf",
  "sms": {"method": "adb", "sender_filter": "jobinja"}
}
```

## files

| file         | what                                              |
|--------------|---------------------------------------------------|
| main.py      | scrape → db → rank → dashboard → (loop)            |
| scrapers.py  | 5 site scrapers with pagination                   |
| matcher.py   | AI ranking (openrouter/openai/gemini/anthropic) + keyword_filter |
| ranker.py    | TF-IDF ranking, learns from scored jobs, learned_tags.json |
| apply.py     | selenium apply: login, fill form, submit, anti-bot |
| recon.py     | inspect apply forms — dump fields, screenshot, multi-step |
| browser.py   | make_driver, save/load cookies + per-origin localStorage |
| sms.py       | receive jobinja SMS code: adb / telegram / webhook |
| db.py        | sqlite jobs table, upsert, find_new, counts       |
| wizard.py    | config.json setup wizard                          |
| utils.py     | Job dataclass                                     |
| profile.md   | phone, cv path, about me, common answers (gitignored) |
| config.json  | preferences, ai keys, proxy (gitignored)          |
