import json
import httpx

# all three providers support openai-compatible /chat/completions
ENDPOINTS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
    "anthropic": "https://api.anthropic.com/v1beta/openai/chat/completions",
}

ENDPOINTS["openrouter"] = "https://openrouter.ai/api/v1/chat/completions"

PROMPT = """You are a job matching assistant. The user wants these tags: {tags}
These cities: {cities}
These job types: {job_types}

Here are {n} jobs. Return the top 10 best matches as JSON array.
Each item: {{"rank": 1, "title": "", "company": "", "city": "", "url": "", "score": 9, "reason": ""}}
Sort by score descending. Only return the JSON array, no markdown, no explanation.

Jobs:
{jobs_json}"""


def rank_jobs(jobs, config):
    """send all jobs to ai in one call, get ranked top 20 back"""
    ai = config.get("ai", {})
    provider = ai.get("provider", "gemini")
    api_key = ai.get("api_key", "")
    model = ai.get("model", "gemini-2.0-flash")
    base_url = ai.get("base_url") or ENDPOINTS.get(provider, ENDPOINTS["gemini"])
    if not api_key:
        print("no api key in config, skipping ai ranking")
        return jobs[:20]

    tags = ", ".join(config.get("tags", []))
    cities = ", ".join(config.get("cities", []))
    job_types = ", ".join(config.get("job_types", []))

    # ponytail: free models have small output token limits, cap at 25 jobs
    jobs = jobs[:25]
    jobs_json = json.dumps([
        {"title": j.title, "company": j.company, "city": j.city,
         "url": j.url, "tags": j.tags, "salary": j.salary, "source": j.source}
        for j in jobs
    ], ensure_ascii=False)

    prompt = PROMPT.format(tags=tags, cities=cities, job_types=job_types,
                            n=len(jobs), jobs_json=jobs_json)

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False}

    try:
        r = httpx.post(base_url, json=body, headers=headers, timeout=60)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        # strip markdown fences if model wrapped them
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        # gpt-oss sometimes truncates — try to salvage a partial json array
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # find last } before end and close the array
            last = content.rfind("}")
            if last > 0:
                return json.loads(content[:last+1] + "]")
            raise
    except Exception as e:
        print(f"ai ranking failed: {e}")
        return jobs[:20]


def keyword_filter(jobs, config):
    """simple tag/city filter without ai. returns matching jobs as dicts (dashboard-ready)."""
    wanted_tags = [t.lower() for t in config.get("tags", [])]
    wanted_cities = [c.lower() for c in config.get("cities", [])]
    wanted_types = [t.lower() for t in config.get("job_types", [])]

    out = []
    for j in jobs:
        blob = (j.title + " " + j.company + " " + " ".join(j.tags)).lower()
        if wanted_tags and not any(t in blob for t in wanted_tags):
            continue
        if wanted_cities and j.city and not any(c in j.city.lower() for c in wanted_cities):
            continue
        if wanted_types and not any(t in blob for t in wanted_types):
            continue
        out.append({"title": j.title, "company": j.company, "city": j.city,
                     "url": j.url, "score": None, "reason": "keyword match"})
    return out


if __name__ == "__main__":
    # demo with fake jobs
    from utils import Job
    fake = [
        Job("cpp developer", "snapp", "تهران", "https://jobinja.ir/1", tags=["برنامه‌نویس", "تمام وقت"]),
        Job("python backend", "digikala", "تهران", "https://jobinja.ir/2", tags=["تمام وقت"]),
        Job("sales manager", "foo", "یزد", "https://jobinia.ir/3"),
    ]
    config = {"tags": ["cpp", "python"], "cities": ["تهران"], "job_types": ["تمام وقت"]}
    # without api key it just returns top 20 unranked
    result = rank_jobs(fake, config)
    assert len(result) <= 20
    print(f"ok, got {len(result)} jobs back from rank_jobs")

    # keyword filter
    kf = keyword_filter(fake, config)
    assert len(kf) == 2, f"expected 2 keyword matches, got {len(kf)}"
    print(f"ok, got {len(kf)} keyword matches")
