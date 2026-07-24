"""recon: open browser, user logs in, then we inspect the apply form.

flow:
  python recon.py jobinja     # opens jobinja login, you log in, it explores a job page
  python recon.py jobvision
  python recon.py karboom
  python recon.py irantalent
  python recon.py daneshkar

what it does:
1. opens chrome to the login page
2. waits for you to log in (press enter when done)
3. saves cookies
4. fetches a real job url from the site (via scraper)
5. navigates to that job page
6. clicks "ارسال رزومه" (or tries to)
7. dumps ALL form fields, textareas, file inputs, buttons it finds
8. takes a screenshot
"""
import json, os, sys, time
from urllib.parse import urlparse

from browser import SITES, make_driver, save_cookies, load_cookies

# job list pages for finding a sample job to inspect
JOB_LIST = {
    "jobinja":    "https://jobinja.ir/jobs?preferred_category=27",
    "jobvision":  "https://jobvision.ir/jobs",
    "karboom":    "https://karboom.io/jobs",
    "irantalent": "https://www.irantalent.com/jobs",
    "daneshkar":  "https://daneshkar.net/jobs",
}


def dump_page_forms(d, site):
    """dump everything that looks like a form field on the current page"""
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import NoSuchElementException

    print("\n=== PAGE FORM RECON ===")
    print(f"url: {d.current_url}")
    print(f"title: {d.title}")

    # textareas
    textareas = d.find_elements(By.TAG_NAME, "textarea")
    for i, ta in enumerate(textareas):
        print(f"  TEXTAREA {i}:")
        print(f"    id={ta.get_attribute('id')!r} name={ta.get_attribute('name')!r}")
        print(f"    placeholder={ta.get_attribute('placeholder')!r}")
        print(f"    label={ta.get_attribute('aria-label')!r}")
        print(f"    visible={ta.is_displayed()}")

    # text/email/tel inputs
    inputs = d.find_elements(By.CSS_SELECTOR, "input[type='text'], input[type='email'], input[type='tel'], input[type='number'], input:not([type])")
    for i, inp in enumerate(inputs):
        if not inp.is_displayed():
            continue
        print(f"  INPUT {i}:")
        print(f"    id={inp.get_attribute('id')!r} name={inp.get_attribute('name')!r}")
        print(f"    type={inp.get_attribute('type')!r} placeholder={inp.get_attribute('placeholder')!r}")
        print(f"    label={inp.get_attribute('aria-label')!r}")
        iid = inp.get_attribute("id")
        if iid:
            try:
                lbl = d.find_element(By.CSS_SELECTOR, f"label[for='{iid}']")
                print(f"    <label>={lbl.text!r}")
            except NoSuchElementException:
                pass

    # file uploads
    files = d.find_elements(By.CSS_SELECTOR, "input[type='file']")
    for i, f in enumerate(files):
        print(f"  FILE INPUT {i}:")
        print(f"    id={f.get_attribute('id')!r} name={f.get_attribute('name')!r}")
        print(f"    accept={f.get_attribute('accept')!r} multiple={f.get_attribute('multiple')!r}")

    # selects
    selects = d.find_elements(By.TAG_NAME, "select")
    for i, s in enumerate(selects):
        if not s.is_displayed():
            continue
        opts = [o.text for o in s.find_elements(By.TAG_NAME, "option")]
        print(f"  SELECT {i}: id={s.get_attribute('id')!r} name={s.get_attribute('name')!r}")
        print(f"    options: {opts[:10]}")

    # buttons — re-find inside loop to avoid StaleElementReferenceException on SPAs
    buttons = d.find_elements(By.CSS_SELECTOR, "button, input[type='submit'], a.btn, a.button")
    for i, b in enumerate(buttons):
        try:
            if not b.is_displayed():
                continue
            txt = b.text or b.get_attribute("value") or b.get_attribute("aria-label") or ""
            if not txt:
                continue
            print(f"  BUTTON {i}: {txt!r}  tag={b.tag_name} id={b.get_attribute('id')!r}")
        except Exception:
            continue  # ponytail: SPA re-rendered, element is stale, skip it

    # screenshot
    path = os.path.join(os.path.dirname(__file__), "cookies", f"recon_{site}.png")
    d.save_screenshot(path)
    print(f"\n  screenshot: {path}")
    print("=== END RECON ===\n")


# ponytail: job detail URL patterns per site.
# karboom: /jobs/XXXXXX/slug  (6-char random code before the slug)
# irantalent: /job/slug/123456 (numeric ID at the end)
# daneshkar: /company/XXXX/1/0/slug (company-based job pages)
def _is_job_detail(site, href):
    if site == "karboom":
        parts = href.replace("https://karboom.io/jobs/", "").split("/")
        return len(parts) == 2 and len(parts[0]) == 6
    if site == "irantalent":
        return "/job/" in href and href.rstrip("/").split("/")[-1].isdigit()
    if site == "daneshkar":
        return "/company/" in href and href.count("/") >= 5
    if site == "jobinja":
        return "/jobs/" in href and href.count("/") > 4
    if site == "jobvision":
        return "/jobs/" in href and "category" not in href
    return False


def find_first_job_url(d, site):
    """try to find a job detail link on the job list page"""
    from selenium.webdriver.common.by import By
    d.get(JOB_LIST[site])
    time.sleep(3)
    links = d.find_elements(By.TAG_NAME, "a")
    for a in links:
        href = a.get_attribute("href") or ""
        if _is_job_detail(site, href):
            return href
    return None


def recon(site):
    if site not in SITES:
        print(f"unknown site: {site}. options: {', '.join(SITES)}")
        sys.exit(1)

    d = make_driver()

    # step 1: login — try saved cookies first, skip manual login if they work
    from browser import load_cookies as _load_cookies, cookies_path as _cookies_path
    from urllib.parse import urlparse
    domain = urlparse(SITES[site]["login_url"]).netloc
    site_root = f"https://{domain.replace('account.', '')}" if "account." in domain else f"https://{domain}"
    has_cookies = os.path.exists(_cookies_path(site))
    if has_cookies:
        d.get(site_root)
        _load_cookies(d, site)
        # ponytail: reload AFTER injecting localStorage so Angular reads it fresh.
        # SPA needs ~5s to boot + OIDC silent refresh before login state settles.
        d.get(site_root)
        time.sleep(5)
        try:
            from selenium.webdriver.common.by import By
            # check: redirected to a login page → NOT logged in.
            # jobvision: account.jobvision.ir/Candidate (fallback to /login)
            # irantalent: www.irantalent.com/auth/login
            # daneshkar: daneshkar.net/login?redirect=
            current = d.current_url
            if "/auth/login" in current.lower() or "/login" in current.lower():
                print(f"  redirected to login ({current[:80]})")
                has_cookies = False
            elif "account." in current and "Candidate" in current:
                print(f"  redirected to login ({current[:60]})")
                has_cookies = False
            else:
                login_btns = d.find_elements(By.XPATH,
                    '//button[contains(text(),"ورود") or contains(text(),"ثبت نام") or contains(text(),"login")]')
                err = d.find_elements(By.XPATH, '//*[contains(text(),"متاسفانه خطایی")]')
                if err:
                    print(f"  jobvision error on page (stale token?)")
                    has_cookies = False
                elif not login_btns:
                    print(f"\n>>> cookies work — already logged in to {site}")
                else:
                    has_cookies = False
        except Exception:
            has_cookies = False

    if not has_cookies:
        d.get(SITES[site]["login_url"])
        print(f"\n>>> log in to {site} in the browser")
        print(">>> press Enter HERE when logged in.")
        input()
        save_cookies(d, site)

    # step 2: find a job
    print(f"\n>>> looking for a job on {site}...")
    job_url = find_first_job_url(d, site)
    if not job_url:
        print("could not find a job url automatically.")
        print("paste a job detail url manually:")
        job_url = input("> ").strip()
        if not job_url:
            d.quit()
            return

    print(f"  found job: {job_url}")
    d.get(job_url)
    time.sleep(3)

    # step 3: click apply
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import NoSuchElementException, ElementClickInterceptedException

    apply_xpaths = [
        '//button[contains(text(),"ارسال رزومه")]',
        '//a[contains(text(),"ارسال رزومه")]',
        '//input[contains(@value,"ارسال رزومه")]',
        '//button[contains(text(),"ارسال")]',
        '//a[contains(text(),"ارسال")]',
        # ponytail: jobvision uses "پر کردن فرم استخدام" instead of "ارسال رزومه"
        '//button[contains(text(),"پر کردن فرم")]',
        '//a[contains(text(),"پر کردن فرم")]',
    ]
    clicked = False
    for xpath in apply_xpaths:
        try:
            btn = d.find_element(By.XPATH, xpath)
            if not btn.is_displayed():
                continue
            print(f"\n>>> found apply button: {btn.text!r}")
            print(">>> clicking it...")
            try:
                btn.click()
            except ElementClickInterceptedException:
                d.execute_script("arguments[0].click();", btn)
            clicked = True
            break
        except NoSuchElementException:
            continue

    if not clicked:
        print(">>> no apply button found. dumping page forms anyway.")

    time.sleep(3)

    # step 3.5: multi-step forms — jobvision shows an intro modal first,
    # then a "تکمیل فرم استخدام" button that opens the actual form.
    # click through up to 3 layers looking for the real form fields.
    proceed_xpaths = [
        '//button[contains(text(),"تکمیل فرم")]',
        '//button[contains(text(),"ادامه")]',
        '//button[contains(text(),"بعدی")]',
        '//a[contains(text(),"تکمی")]',
        '//button[contains(text(),"شروع")]',
    ]
    for layer in range(3):
        found_proceed = False
        for xpath in proceed_xpaths:
            try:
                btn = d.find_element(By.XPATH, xpath)
                if not btn.is_displayed():
                    continue
                print(f"\n>>> step {layer+1}: found proceed button: {btn.text!r}")
                print(">>> clicking to see next step...")
                try:
                    btn.click()
                except ElementClickInterceptedException:
                    d.execute_script("arguments[0].click();", btn)
                found_proceed = True
                time.sleep(3)
                break
            except NoSuchElementException:
                continue
        if not found_proceed:
            break

    # step 4: dump everything
    print(f"\n>>> dumping forms at current step (url: {d.current_url[:80]})")
    dump_page_forms(d, site)

    # also dump visible text (first 1000 chars) for context
    print("=== VISIBLE TEXT (first 1000 chars) ===")
    print(d.find_element(By.TAG_NAME, "body").text[:1000])
    print("=== END ===\n")

    # step 5: check CV/profile requirements
    print("=== CV/PROFILE REQUIREMENTS ===")
    cv_urls = {
        "irantalent": "https://www.irantalent.com/cv",
        "karboom":    "https://karboom.io/my/resumes",
        "jobvision":  "https://jobvision.ir/Candidate/Resumes",
        "jobinja":    None,  # jobinja uses inline form on job page
        "daneshkar":  None,  # need to discover
    }
    cv_url = cv_urls.get(site)
    if cv_url:
        d.get(cv_url)
        time.sleep(4)
        print(f"  profile page: {d.current_url[:80]}")
        body_text = d.find_element(By.TAG_NAME, "body").text[:600]
        print(f"  visible text: {body_text[:400]}")
        # check for file upload / CV builder / PDF
        files = d.find_elements(By.CSS_SELECTOR, "input[type='file']")
        print(f"  file inputs: {len(files)}")
        for f in files:
            print(f"    accept={f.get_attribute('accept')!r} name={f.get_attribute('name')!r}")
        # check for "complete your CV" type messages
        for kw in ["تکمیل", "complete", "CV", "رزومه", "ساخت", "build"]:
            try:
                el = d.find_element(By.XPATH, f'//*[contains(text(),"{kw}")]')
                if el.is_displayed():
                    print(f'  found text: "{el.text[:60]}"')
                    break
            except NoSuchElementException:
                continue
    else:
        print("  no separate CV page known for this site (uses inline form)")

    print("=== END CV CHECK ===\n")

    print(">>> done. browser stays open. press Enter to close.")
    input()
    d.quit()


if __name__ == "__main__":
    site = sys.argv[1] if len(sys.argv) > 1 else ""
    if not site:
        print(__doc__)
        print(f"sites: {', '.join(SITES)}")
        sys.exit(1)
    recon(site)
