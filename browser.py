"""shared browser/selenium helpers — used by apply.py and recon.py."""
import json, os, sys, time

COOKIES_DIR = os.path.join(os.path.dirname(__file__), "cookies")

SITES = {
    "jobinja":    {"login_url": "https://jobinja.ir/join/user",
                   "apply_xpath": '//button[contains(text(),"ارسال رزومه")]'},
    "jobvision":  {"login_url": "https://account.jobvision.ir/Candidate",
                   "apply_xpath": '//button[contains(text(),"پر کردن فرم") or contains(text(),"ارسال رزومه")]'},
    "karboom":    {"login_url": "https://karboom.io",
                   "apply_xpath": '//button[contains(text(),"ارسال رزومه")]'},
    "irantalent": {"login_url": "https://www.irantalent.com/auth/login",
                   "apply_xpath": '//button[contains(text(),"ارسال رزومه")] | //a[contains(text(),"ارسال رزومه")]'},
    "daneshkar":  {"login_url": "https://daneshkar.net/login",
                   "apply_xpath": '//button[contains(text(),"ارسال رزومه")]'},
}


def _chrome_paths():
    """find chrome.exe and chromedriver on this windows machine."""
    import shutil
    chrome = None
    for p in [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]:
        if os.path.exists(p):
            chrome = p
            break
    here = os.path.dirname(__file__)
    for p in [os.path.join(here, "chromedriver-win64", "chromedriver.exe"),
              os.path.join(here, "chromedriver-win64", "chromedriver")]:
        if os.path.exists(p):
            return chrome, p
    cd = shutil.which("chromedriver")
    return chrome, cd


def make_driver():
    """create a visible chrome webdriver (not headless — manual login needs it)."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    opts = Options()
    chrome_exe, chromedriver_exe = _chrome_paths()
    if chrome_exe:
        opts.binary_location = chrome_exe
    svc = Service(executable_path=chromedriver_exe) if chromedriver_exe else None
    if svc:
        return webdriver.Chrome(service=svc, options=opts)
    return webdriver.Chrome(options=opts)


def cookies_path(site):
    return os.path.join(COOKIES_DIR, f"{site}.json")


def save_cookies(driver, site):
    """save cookies + localStorage for a site. visits all related domains because
    auth may live on a subdomain or in localStorage (jobvision uses OpenID Connect —
    the token is in localStorage, not cookies). localStorage is per-origin, so we
    save it keyed by domain and restore each domain's keys on the correct origin."""
    os.makedirs(COOKIES_DIR, exist_ok=True)
    path = cookies_path(site)
    login_url = SITES[site]["login_url"]
    from urllib.parse import urlparse
    domain = urlparse(login_url).netloc
    root = domain.replace("www.", "").replace("account.", "")
    domains_to_check = [f"https://{root}", f"https://{domain}"]
    all_cookies = {}
    storage_by_domain = {}  # ponytail: per-domain localStorage — origins are isolated
    for url in domains_to_check:
        try:
            driver.get(url)
            time.sleep(0.5)
            for c in driver.get_cookies():
                key = (c["name"], c.get("domain", ""))
                all_cookies[key] = c
            ls = driver.execute_script("""
                let items = {};
                for (let i = 0; i < localStorage.length; i++) {
                    let k = localStorage.key(i);
                    items[k] = localStorage.getItem(k);
                }
                return JSON.stringify(items);
            """) or "{}"
            parsed = json.loads(ls)
            if parsed:
                origin = urlparse(driver.current_url).netloc
                storage_by_domain[origin] = parsed
        except Exception:
            pass
    cookies = list(all_cookies.values())
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"cookies": cookies, "localStorage": storage_by_domain}, f,
                  ensure_ascii=False, indent=2)
    total_ls = sum(len(v) for v in storage_by_domain.values())
    print(f"saved {len(cookies)} cookies + {total_ls} localStorage items ({len(storage_by_domain)} domains) to {path}")


def load_cookies(driver, site, domain=None):
    """load cookies + localStorage for a site. visits each domain to add cookies,
    then injects localStorage on the correct origin for each domain."""
    path = cookies_path(site)
    if not os.path.exists(path):
        print(f"no cookies for {site}, run --login {site} first")
        return False
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # ponytail: old format = plain list, mid format = dict with flat localStorage,
    # new format = dict with per-domain localStorage
    if isinstance(data, list):
        cookies = data
        storage_by_domain = {}
    else:
        cookies = data.get("cookies", [])
        ls = data.get("localStorage", {})
        if ls and all(isinstance(v, dict) for v in ls.values()):
            # new per-domain format: {origin: {key: val}}
            storage_by_domain = ls
        else:
            # old flat format: {key: val} — inject on the login_url's full origin
            storage_by_domain = {}
            from urllib.parse import urlparse
            root = urlparse(SITES[site]["login_url"]).netloc.replace("account.", "")
            if ls:
                storage_by_domain[root] = ls

    # ponytail: don't strip www. from cookie domains — a cookie on .www.irantalent.com
    # must be set on www.irantalent.com, not irantalent.com. just lstrip the dot.
    by_domain = {}
    for c in cookies:
        d = c.get("domain", "").lstrip(".")
        by_domain.setdefault(d, []).append(c)
    loaded = 0
    for cd, cks in by_domain.items():
        try:
            driver.get(f"https://{cd}/")
            time.sleep(0.3)
            for c in cks:
                c.pop("sameSite", None)
                try:
                    driver.add_cookie(c)
                    loaded += 1
                except Exception:
                    pass
        except Exception:
            pass

    # inject localStorage on each origin (localStorage is per-origin, not shared)
    for origin, items in storage_by_domain.items():
        if not items:
            continue
        try:
            driver.get(f"https://{origin}/")
            time.sleep(0.3)
            items_js = "; ".join(
                f'localStorage.setItem({json.dumps(k)}, {json.dumps(v)})'
                for k, v in items.items()
            )
            driver.execute_script(items_js)
        except Exception:
            pass

    total_ls = sum(len(v) for v in storage_by_domain.values())
    print(f"loaded {loaded}/{len(cookies)} cookies + {total_ls} localStorage items ({len(storage_by_domain)} domains) for {site}")
    return loaded > 0 or bool(storage_by_domain)


def site_from_url(url):
    """match a url to a site name by domain substring."""
    from urllib.parse import urlparse
    netloc = urlparse(url).netloc.lower()
    for name in SITES:
        if name in netloc:
            return name
    return None
