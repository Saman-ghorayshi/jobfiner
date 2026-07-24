import httpx
from bs4 import BeautifulSoup
from utils import Job


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "Accept-Language": "fa,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class BaseScraper:
    source = "base"

    def __init__(self, proxy=None):
        self.proxy = proxy

    def fetch(self, url):
        kwargs = dict(headers=HEADERS, timeout=15, follow_redirects=True)
        if self.proxy:
            kwargs["proxies"] = {"all://": self.proxy}
        with httpx.Client(**kwargs) as c:
            r = c.get(url)
            r.raise_for_status()
            return r.text

    def parse(self, html):
        raise NotImplementedError

    def scrape_all_pages(self, config, max_pages=1000):
        """loop pages until a page returns 0 jobs. subclasses must implement _page_url(page, config)."""
        all_jobs = []
        for page in range(1, max_pages + 1):
            url = self._page_url(page, config)
            try:
                html = self.fetch(url)
            except Exception as e:
                print(f"    {self.source} page {page}: fetch failed {e}, stopping")
                break
            jobs = self.parse(html)
            if not jobs:
                break
            all_jobs.extend(jobs)
            if page % 50 == 0:
                print(f"    {self.source}: page {page}, {len(all_jobs)} jobs so far")
        return all_jobs

    def _page_url(self, page, config):
        raise NotImplementedError


class JobinjaScraper(BaseScraper):
    source = "jobinja"

    def search_url(self, query="", city="", category="", page=1):
        url = "https://jobinja.ir/jobs"
        params = {}
        if query:
            params["q"] = query
        if city:
            params["preferred_city"] = city
        if category:
            params["preferred_category"] = category
        if page > 1:
            params["page"] = page
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return url

    def _page_url(self, page, config):
        # ponytail: just search first tag, ai will filter the rest
        query = config.get("tags", [""])[0] if config.get("tags") else ""
        return self.search_url(query=query, page=page)

    def parse(self, html):
        soup = BeautifulSoup(html, "lxml")
        jobs = []
        cards = soup.select("div.o-listView__itemWrap")
        for card in cards:
            try:
                title_link = card.select_one("a.c-jobListView__titleLink")
                if not title_link:
                    continue
                title = title_link.get_text(strip=True)
                url = title_link.get("href", "")

                metas = card.select("li.c-jobListView__metaItem span")
                company = metas[0].get_text(strip=True) if len(metas) > 0 else ""
                city = metas[1].get_text(strip=True) if len(metas) > 1 else ""

                salary = ""
                if len(metas) > 2:
                    salary = metas[2].get_text(strip=True)

                posted = ""
                date_el = card.select_one("span.c-jobListView__passedDays")
                if date_el:
                    posted = date_el.get_text(strip=True)

                jobs.append(Job(
                    title=title, company=company, city=city, url=url,
                    salary=salary, posted=posted, source=self.source,
                ))
            except Exception:
                continue
        return jobs


class JobvisionScraper(BaseScraper):
    """jobvision is a spa, but it has a clean json api"""
    source = "jobvision"
    api_url = "https://candidateapi.jobvision.ir/api/v1/JobPost/List"

    def search_url(self, query="", city="", category=""):
        # returns the api body, not a url — override fetch instead
        return self.api_url

    def fetch_jobs(self, page=1, size=30, query="", city="", category=""):
        body = {"pageSize": size, "requestedPage": page, "sortBy": 1, "searchId": None}
        if query:
            body["query"] = query
        if city:
            body["provinceId"] = city
        if category:
            body["jobCategoryId"] = category

        kwargs = dict(headers={**HEADERS, "Content-Type": "application/json",
                               "Origin": "https://jobvision.ir", "Referer": "https://jobvision.ir/jobs"},
                      timeout=15)
        if self.proxy:
            kwargs["proxies"] = {"all://": self.proxy}
        with httpx.Client(**kwargs) as c:
            r = c.post(self.api_url, json=body)
            r.raise_for_status()
            # ponytail: jobvision serves the marketing SPA HTML with 200 when the
            # api is down (maintenance) — detect it so we say "api down" instead
            # of a misleading json-error message. ceiling: no retry/backoff, a
            # 5xx or html page just bails; add backoff if jobvision rate-limits.
            ct = r.headers.get("content-type", "")
            if "json" not in ct or not r.text.lstrip().startswith(("{", "[")):
                raise RuntimeError(
                    f"jobvision api returned {ct!r} (not json) — site is likely "
                    f"down for maintenance; got {len(r.text)} bytes of "
                    f"{'html' if r.text.lstrip().startswith('<') else 'non-json'}"
                )
            return r.json()["data"]["jobPosts"]

    def scrape_all_pages(self, config, max_pages=2000):
        all_jobs = []
        query = " ".join(config.get("tags", [])) if config.get("tags") else ""
        for page in range(1, max_pages + 1):
            try:
                posts = self.fetch_jobs(page=page, size=30, query=query)
            except Exception as e:
                print(f"    jobvision page {page}: failed {e}, stopping")
                break
            if not posts:
                break
            all_jobs.extend(self._parse_posts(posts))
            if page % 50 == 0:
                print(f"    jobvision: page {page}, {len(all_jobs)} jobs so far")
        return all_jobs

    def parse(self, html_or_json):
        # if called with the raw html page, skip — this scraper uses fetch_jobs
        # but we provide parse() for the base interface, parsing api json
        import json as _json
        if isinstance(html_or_json, str):
            data = _json.loads(html_or_json)
        else:
            data = html_or_json
        posts = data.get("data", {}).get("jobPosts", []) if "data" in data else data
        return self._parse_posts(posts)

    def _parse_posts(self, posts):
        jobs = []
        for p in posts:
            salary = p.get("salary", {}) or {}
            salary_text = salary.get("titleFa", "") if isinstance(salary, dict) else ""

            loc = p.get("location", {}) or {}
            city = ""
            if isinstance(loc, dict):
                prov = loc.get("province", {}) or {}
                city_obj = loc.get("city", {}) or {}
                if isinstance(city_obj, dict) and city_obj.get("titleFa"):
                    city = city_obj["titleFa"]
                elif isinstance(prov, dict) and prov.get("titleFa"):
                    city = prov["titleFa"]

            work = p.get("workType", {}) or {}
            job_type = work.get("titleFa", "") if isinstance(work, dict) else ""

            cats = p.get("jobCategories", []) or []
            tags = [c.get("titleFa", "") for c in cats if isinstance(c, dict) and c.get("titleFa")]

            posted = ""
            act = p.get("activationTime", {}) or {}
            if isinstance(act, dict):
                posted = act.get("beautifyFa", "")

            company = p.get("company", {}) or {}
            company_name = company.get("nameFa", "") if isinstance(company, dict) else ""

            jobs.append(Job(
                title=p.get("title", ""),
                company=company_name,
                city=city,
                url=f"https://jobvision.ir/job/{p['id']}",
                salary=salary_text,
                tags=tags,
                posted=posted,
                source=self.source,
            ))
        return jobs


class KarboomScraper(BaseScraper):
    source = "karboom"

    def search_url(self, query="", city="", category="", page=1):
        url = "https://karboom.io/jobs"
        params = {}
        if category:
            url = f"https://karboom.io/jobs/{category}"
        if query:
            params["search"] = query
        if page > 1:
            params["page"] = page
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return url

    def _page_url(self, page, config):
        query = config.get("tags", [""])[0] if config.get("tags") else ""
        return self.search_url(query=query, page=page)

    def parse(self, html):
        soup = BeautifulSoup(html, "lxml")
        jobs = []
        for card in soup.select("div.js-job-item"):
            h3 = card.find("h3")
            a = h3.find("a") if h3 else None
            if not a:
                continue
            title = a.get_text(strip=True)
            url = a.get("href", "")
            if not url.startswith("http"):
                url = "https://karboom.io" + url

            # company is the logo img alt
            img = card.select_one("img.company-logo")
            company = img.get("alt", "") if img else ""

            # city is in the card text after company name
            text = card.get_text(" ", strip=True)
            city = ""
            for w in ["تهران", "البرز", "مشهد", "اصفهان", "شیراز", "کرج", "یزد"]:
                if w in text:
                    city = w
                    break

            # tags: badges in the card (پیگیری قطعی, گارانتی کاربوم, etc.)
            # skip company name, dashes, city
            tags = []
            for span in card.select("span"):
                t = span.get_text(strip=True)
                if t and len(t) < 20 and t not in (title, company, city, "-", "تهران"):
                    tags.append(t)

            jobs.append(Job(
                title=title, company=company, city=city, url=url,
                tags=tags, source=self.source,
            ))
        return jobs


class IranTalentScraper(BaseScraper):
    """irantalent is an angular spa.
    real endpoint is POST api/v1/employer/position/search-by-slug — despite
    the /employer/ path it's fully public (CORS *) and returns paginated
    positions (server hardcaps per_page at 30, page param works).
    ponytail: query/category filters on this endpoint are undocumented; we
    scrape the unfiltered feed and let the AI matcher filter — adding a
    body["query"] field later is one line if a config value needs it."""
    source = "irantalent"
    api_url = "https://api.irantalent.com/api/v1/employer/position/search-by-slug"

    def search_url(self, query="", city="", category=""):
        return self.api_url

    def scrape_all_pages(self, config, max_pages=1000):
        all_jobs = []
        last_page = None
        for page in range(1, max_pages + 1):
            try:
                data = self.fetch_jobs(page=page)
            except Exception as e:
                print(f"    irantalent page {page}: failed {e}, stopping")
                break
            posts = data.get("data", []) or []
            if not posts:
                break
            if last_page is None:
                last_page = data.get("last_page")
                if last_page:
                    last_page = int(last_page)
                    print(f"    irantalent: server reports {data.get('total')} jobs across {last_page} pages")
            all_jobs.extend(self._parse_posts(posts))
            if page % 5 == 0:
                print(f"    irantalent: page {page}, {len(all_jobs)} jobs so far")
            if last_page and page >= last_page:
                break
        return all_jobs

    def fetch_jobs(self, page=1):
        # ponytail: per_page is ignored server-side (hardcaps at 30); only
        # page is honoured. empty body = unfiltered, newest-first feed.
        kwargs = dict(headers={**HEADERS, "Content-Type": "application/json",
                               "Accept": "application/json",
                               "Referer": "https://www.irantalent.com/"},
                      timeout=15, follow_redirects=True)
        if self.proxy:
            kwargs["proxies"] = {"all://": self.proxy}
        with httpx.Client(**kwargs) as c:
            r = c.post(self.api_url, json={"page": page})
            r.raise_for_status()
            return r.json()["data"]

    def parse(self, html_or_json):
        import json as _json
        if isinstance(html_or_json, str):
            data = _json.loads(html_or_json)
        else:
            data = html_or_json
        # search-by-slug wraps posts under data.data; latest-positions gave a bare list
        if isinstance(data, dict) and "data" in data and "current_page" in data["data"]:
            return self._parse_posts(data["data"]["data"])
        return self._parse_posts(data)

    def _parse_posts(self, posts):
        jobs = []
        for p in posts:
            title = p.get("title_farsi", "") or p.get("title", "")
            company_obj = p.get("brand_data", {}) or p.get("employer", {}) or {}
            company = company_obj.get("name_fa", "") or company_obj.get("name_farsi", "")
            city = p.get("location_text_farsi", "") or p.get("location_text", "")
            if not city:
                loc = p.get("location", {}) or {}
                if isinstance(loc, dict):
                    city = loc.get("title_farsi", "") or loc.get("title", "")

            salary = ""
            if p.get("is_show_salary") and p.get("salary_from"):
                salary = f"{p['salary_from']} - {p.get('salary_to', '')}"
            elif p.get("salary_from"):
                salary = f"{p['salary_from']}"

            job_type = ""
            etype = p.get("employment_type", {}) or {}
            if isinstance(etype, dict):
                job_type = etype.get("title_farsi", "") or etype.get("title", "")

            slug = p.get("slug", "")
            jid = p.get("id", "")
            url = f"https://www.irantalent.com/job/{slug}/{jid}"

            jobs.append(Job(
                title=title, company=company, city=city, url=url,
                salary=salary, tags=[job_type] if job_type else [],
                posted=p.get("lived_at", "")[:10], source=self.source,
            ))
        return jobs


class DaneshkarScraper(BaseScraper):
    """daneshkar is next.js with mui, full html in initial response.
    ponytail: ?page=N doesn't actually change content (infinite scroll via RSC).
    we get ~19 jobs per request. accept it — not worth selenium for this."""
    source = "daneshkar"

    def search_url(self, query="", city="", category="", page=1):
        if category:
            return f"https://daneshkar.net/jobs/category/{category}"
        return "https://daneshkar.net/jobs"

    def scrape_all_pages(self, config, max_pages=1000):
        # ponytail: pagination doesn't work on this site (client-side RSC), one page only
        url = self.search_url()
        html = self.fetch(url)
        return self.parse(html)

    def parse(self, html):
        soup = BeautifulSoup(html, "lxml")
        jobs = []
        # each card has an a[href*="/company/"] link — walk up to the full card
        for a in soup.select('a[href*="/company/"]'):
            # walk up until we find the card with date text ("روز پیش" or "جزئیات")
            card = a
            for _ in range(8):
                card = card.parent
                if card is None:
                    break
                text = card.get_text(" ", strip=True)
                if "جزئیات" in text or "روز" in text:
                    break
            if not card or "جزئیات" not in card.get_text(" ", strip=True):
                continue

            text = card.get_text(" ", strip=True)

            company = a.get_text(strip=True)

            # title = p that isn't company and isn't the date
            title = ""
            for p in card.find_all("p"):
                pt = p.get_text(strip=True)
                if pt and pt != company and "روز" not in pt and not pt.startswith("|") and len(pt) > 5:
                    title = pt
                    break

            # tags: links to /jobs/category/
            tags = []
            for link in card.find_all("a", href=True):
                href = link.get("href", "")
                if "/jobs/category/" in href:
                    tags.append(link.get_text(strip=True))

            # city + date: text has "city | N روز پیش جزئیات بیشتر"
            city = ""
            posted = ""
            import re
            date_m = re.search(r"(\d+|[۰-۹])\s*روز\s*پیش", text)
            if date_m:
                posted = date_m.group(0)
            for w in ["تهران", "اصفهان", "البرز", "مشهد", "شیراز", "کرج", "یزد", "هرمزگان",
                       "گیلان", "مازندران", "آذربایجان", "خوزستان", "کرمان"]:
                # city appears after the tags and before the date
                date_pos = text.find(posted) if posted else len(text)
                if w in text[:date_pos] and w not in company:
                    city = w
                    break

            url = a.get("href", "")
            if url and not url.startswith("http"):
                url = "https://daneshkar.net" + url

            if title and url:
                jobs.append(Job(
                    title=title, company=company, city=city, url=url,
                    salary="", tags=tags, posted=posted, source=self.source,
                ))
        return jobs


SCRAPERS = {
    "jobinja": JobinjaScraper,
    "jobvision": JobvisionScraper,
    "karboom": KarboomScraper,
    "irantalent": IranTalentScraper,
    "daneshkar": DaneshkarScraper,
}


if __name__ == "__main__":
    # quick smoke test, one site at a time
    import sys
    site = sys.argv[1] if len(sys.argv) > 1 else "jobinja"
    if site == "all":
        for name, cls in SCRAPERS.items():
            s = cls()
            try:
                if hasattr(s, "fetch_jobs"):
                    if name == "irantalent":
                        data = s.fetch_jobs()
                        jobs = s._parse_posts(data)
                    else:
                        posts = s.fetch_jobs(size=10)
                        jobs = s._parse_posts(posts)
                else:
                    url = s.search_url(query="برنامه")
                    html = s.fetch(url)
                    jobs = s.parse(html)
                print(f"{name}: {len(jobs)} jobs")
                if jobs:
                    print(f"  first: {jobs[0].title} @ {jobs[0].company}")
            except Exception as e:
                print(f"{name}: FAILED {e}")
    else:
        cls = SCRAPERS[site]
        s = cls()
        if hasattr(s, "fetch_jobs"):
            if site == "irantalent":
                data = s.fetch_jobs()
                jobs = s._parse_posts(data)
            else:
                posts = s.fetch_jobs(size=10, query="برنامه")
                jobs = s._parse_posts(posts)
            print(f"{site}: {len(jobs)} jobs")
            for j in jobs[:3]:
                print(f"  {j.title} | {j.company} | {j.city} | {j.url}")
        else:
            url = s.search_url(query="برنامه")
            print(f"fetching {url} ...")
            html = s.fetch(url)
            jobs = s.parse(html)
            print(f"{site}: {len(jobs)} jobs")
            for j in jobs[:3]:
                print(f"  {j.title} | {j.company} | {j.city} | {j.url}")
