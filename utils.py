from dataclasses import dataclass, field, asdict
import json, hashlib


@dataclass
class Job:
    title: str
    company: str
    city: str
    url: str
    salary: str = ""
    tags: list = field(default_factory=list)
    posted: str = ""
    source: str = ""
    description: str = ""

    def hash(self):
        return hashlib.sha256(self.url.encode()).hexdigest()[:12]


def save_jobs(jobs, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(j) for j in jobs], f, ensure_ascii=False, indent=2)


def load_seen(path):
    try:
        with open(path, encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f)


if __name__ == "__main__":
    j = Job("cpp dev", "snapp", "tehran", "https://jobinja.ir/companies/snapp/jobs/1")
    assert j.hash() == hashlib.sha256(j.url.encode()).hexdigest()[:12]
    assert len(j.tags) == 0
    print("ok")
