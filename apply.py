"""auto-apply: selenium login (manual, saves cookies) + apply to jobs.

flow:
  python apply.py --login jobinja      # opens browser, you log in, cookies saved
  python apply.py --apply              # applies to all pending jobs in db
  python apply.py --apply --dry-run    # show what would happen, don't click
  python apply.py --apply --min-score 7   # only apply to ai_score >= 7
  python apply.py --apply --picks      # only apply to dashboard picks (applied='pending')
  python apply.py --apply --site jobinja  # only apply to jobs from one site
"""
import json, os, sys, time, random
from utils import Job
from browser import (SITES, make_driver as _driver, save_cookies,
                     load_cookies, site_from_url as _site_from_url)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
PROFILE_PATH = os.path.join(os.path.dirname(__file__), "profile.md")


# --- anti-bot helpers ---

def human_type(driver, element, text):
    """type char by char with 80-200ms delay to avoid bot detection"""
    for char in text:
        element.send_keys(char)
        time.sleep(random.uniform(0.08, 0.2))


def human_click(driver, element):
    """move mouse to element, pause, then click. not instant"""
    from selenium.webdriver.common.action_chains import ActionChains
    actions = ActionChains(driver)
    actions.move_to_element(element).pause(random.uniform(0.5, 1.5)).click().perform()


def human_scroll(driver, direction="down", amount=None):
    """scroll in random chunks, pause at random points"""
    amt = amount or random.randint(200, 600)
    if direction == "up":
        amt = -amt
    driver.execute_script(f"window.scrollBy(0, {amt})")
    time.sleep(random.uniform(0.5, 2.0))


def human_pause(low=2.0, high=8.0):
    """random pause between actions to look human"""
    time.sleep(random.uniform(low, high))


def load_profile():
    """parse profile.md into a dict: {phone, cv_path, about, answers}"""
    if not os.path.exists(PROFILE_PATH):
        return {}
    with open(PROFILE_PATH, encoding="utf-8") as f:
        text = f.read()
    sections = {}
    current = None
    for line in text.splitlines():
        line = line.rstrip()
        if line.startswith("# "):
            current = line[2:].strip().lower()
            sections[current] = ""
        elif current:
            sections[current] += line + "\n"
    return {
        "phone": sections.get("phone", "").strip(),
        "cv_path": sections.get("cv path", "").strip(),
        "about": sections.get("about me", "").strip(),
        "answers": sections.get("answers to common questions", "").strip(),
    }


def apply_jobinja(driver, job, profile):
    """jobinja apply flow: fill phone, select resume type, submit.

    form fields (from real page inspection):
      - phone: input[name=telephone] #contactInp, placeholder='شماره موبایل‌تان را وارد کنید'
      - radio online: #apply_choice_jobinja_profile (default selected)
      - radio attached: #apply_choice_uploaded_cv
      - file upload: #cv-uploader (hidden, use send_keys)
      - checkbox: input[name=create_job_alert]
      - submit: input[type=submit].c-btn--primary
    """
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import NoSuchElementException

    # 1. phone
    try:
        phone = driver.find_element(By.CSS_SELECTOR, "input[name='telephone']")
    except NoSuchElementException:
        return False, "phone input not found"
    human_type(driver, phone, profile.get("phone", ""))
    human_pause(0.5, 1.5)

    # 2. resume type: use online by default. if cv_path is a real file, use attached + upload
    cv_path = profile.get("cv_path", "")
    cv_abs = os.path.join(os.path.dirname(__file__), cv_path) if cv_path else ""
    use_attached = os.path.exists(cv_abs) if cv_abs else False

    if use_attached:
        try:
            radio = driver.find_element(By.CSS_SELECTOR, "#apply_choice_uploaded_cv")
            human_click(driver, radio)
            human_pause(1, 2)
            file_input = driver.find_element(By.CSS_SELECTOR, "#cv-uploader")
            file_input.send_keys(os.path.abspath(cv_abs))
            human_pause(1, 2)
        except NoSuchElementException:
            pass  # fallback: leave online resume selected
    # else: online resume is default-selected, do nothing

    # 3. submit — it's input[type=submit], not a button with text
    try:
        submit = driver.find_element(By.CSS_SELECTOR, "input[type='submit'].c-btn--primary")
    except NoSuchElementException:
        return False, "submit button not found"

    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", submit)
    human_pause(0.5, 1.5)
    try:
        human_click(driver, submit)
    except Exception:
        driver.execute_script("arguments[0].click();", submit)
    human_pause(3, 6)

    # 4. check for success or SMS verification
    # ponytail: jobinja shows "اعتبارسنجی با موبایل" popup with a code input.
    # the popup has: title text, an input for the code, a submit button, and
    # a "کد را دریافت نکردید؟ ارسال مجدد" resend link.
    try:
        driver.find_element(By.XPATH, '//*[contains(text(),"با موفقیت") or contains(text(),"ارسال شد")]')
        return True, "applied successfully (success message found)"
    except NoSuchElementException:
        # check for SMS verification popup — jobinja says "اعتبارسنجی با موبایل"
        try:
            driver.find_element(By.XPATH,
                '//*[contains(text(),"اعتبارسنجی") or contains(text(),"کد تایید") or contains(text(),"کد را وارد")]')
            print("    SMS verification popup detected — waiting for code via ADB...")
            from sms import get_code, apply_sms_code
            code = get_code(timeout=120, sender_filter="jobinja")
            if code:
                if apply_sms_code(driver, code):
                    human_pause(2, 4)
                    # try to click the verify/submit button in the popup
                    try:
                        btn = driver.find_element(By.XPATH,
                            '//button[contains(text(),"تایید") or contains(text(),"ارسال") '
                            'or contains(text(),"confirm") or contains(text(),"ثبت")]')
                        human_click(driver, btn)
                        human_pause(3, 5)
                    except NoSuchElementException:
                        # maybe it's an input[type=submit] inside the popup
                        try:
                            btn = driver.find_element(By.CSS_SELECTOR,
                                "input[type='submit']")
                            human_click(driver, btn)
                            human_pause(3, 5)
                        except NoSuchElementException:
                            pass
                    # check for success after code
                    try:
                        driver.find_element(By.XPATH, '//*[contains(text(),"با موفقیت") or contains(text(),"ارسال شد")]')
                        return True, "applied + sms verified"
                    except NoSuchElementException:
                        return True, "sms code submitted"
                return False, "found sms code but couldn't fill verification field"
            return False, "sms verification required but no code received (timeout)"
        except NoSuchElementException:
            # maybe the verification input is visible without a text prompt we recognize
            try:
                inp = driver.find_element(By.CSS_SELECTOR,
                    "input[name*='code'], input[name*='verify'], input[placeholder*='کد'], input[placeholder*='تایید']")
                print("    SMS verification input found (no popup text) — waiting for code...")
                from sms import get_code, apply_sms_code
                code = get_code(timeout=120, sender_filter="jobinja")
                if code:
                    if apply_sms_code(driver, code):
                        human_pause(2, 4)
                        try:
                            btn = driver.find_element(By.XPATH,
                                '//button[contains(text(),"تایید") or contains(text(),"ارسال") or contains(text(),"confirm") or contains(text(),"ثبت")]')
                            human_click(driver, btn)
                            human_pause(3, 5)
                        except NoSuchElementException:
                            pass
                        return True, "sms code entered and submitted"
                    return False, "found sms code but couldn't fill verification field"
                return False, "sms verification required but no code received (timeout)"
            except NoSuchElementException:
                try:
                    err = driver.find_element(By.XPATH, '//*[contains(@class,"error") or contains(text(),"خطا") or contains(text(),"اصلاح")]')
                    return False, f"form error: {err.text[:100]}"
                except NoSuchElementException:
                    return True, "submitted (no error detected)"


def login(site):
    """open browser to the site login page, wait for user to log in, save cookies"""
    if site not in SITES:
        print(f"unknown site: {site}. options: {', '.join(SITES)}")
        sys.exit(1)
    driver = _driver()
    driver.get(SITES[site]["login_url"])
    print(f"\n>>> log in to {site} in the browser window")
    print(">>> press Enter HERE when you're logged in and on the site homepage")
    input()
    save_cookies(driver, site)
    driver.quit()
    print("done. cookies saved.")


def apply_to_job(driver, job, profile=None):
    """navigate to job url, load cookies, then call per-site form handler."""
    site = _site_from_url(job.url)
    if not site:
        return False, f"unknown site for url: {job.url}"
    from urllib.parse import urlparse
    domain = urlparse(job.url).netloc
    root = f"https://{domain}"
    driver.get(root)
    load_cookies(driver, site)
    driver.get(job.url)
    human_pause(2, 4)

    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import (
        NoSuchElementException, ElementClickInterceptedException)

    # jobinja: the form is already on the page (sidebar), no initial button click needed.
    # just dispatch to per-site handler.
    if site == "jobinja" and profile:
        return apply_jobinja(driver, job, profile)

    # generic: click apply button then handle confirm
    xpath = SITES[site]["apply_xpath"]
    try:
        btn = driver.find_element(By.XPATH, xpath)
    except NoSuchElementException:
        return False, "no apply button found (maybe already applied or page layout changed)"

    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        human_scroll(driver)
        human_click(driver, btn)
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", btn)
    except Exception as e:
        return False, f"click failed: {e}"

    human_pause(2, 5)
    # generic: try to click confirm dialog
    try:
        confirm = driver.find_element(By.XPATH, '//button[contains(text(),"تایید") or contains(text(),"بله") or contains(text(),"ارسال")]')
        human_click(driver, confirm)
        human_pause(2, 4)
    except NoSuchElementException:
        pass
    return True, "clicked apply button"


def apply_all(dry_run=False, min_score=None, picks_only=False, site=None):
    """apply to jobs in the db. gating: dry-run, min_score, picks_only, site filter."""
    import db
    conn = db.connect()

    if picks_only:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE applied='pending' AND status='active' ORDER BY first_seen DESC"
        ).fetchall()
    else:
        rows = db.get_pending_jobs(conn, min_score=min_score, site=site)

    if not rows:
        print("no jobs to apply to (after filters)")
        conn.close()
        return

    print(f"{len(rows)} jobs to apply to" +
          (f" [dry-run]" if dry_run else "") +
          (f" [min-score={min_score}]" if min_score else "") +
          (f" [picks only]" if picks_only else "") +
          (f" [site={site}]" if site else ""))

    if dry_run:
        for r in rows:
            job = db._row_to_job(r)
            print(f"  would apply: {job.title} @ {job.company} ({job.source}) score={r['ai_score'] or '-'}")
        print(f"\ndry run: {len(rows)} jobs, no clicks made. re-run without --dry-run to actually apply.")
        conn.close()
        return

    profile = load_profile()
    driver = _driver()
    successes = failures = 0
    for r in rows:
        job = db._row_to_job(r)
        print(f"\n--- {job.title} @ {job.company} ({job.source}) ---")
        print(f"    {job.url}")
        ok, msg = apply_to_job(driver, job, profile=profile)
        status = "applied" if ok else "failed"
        db.set_applied(conn, r["hash"], status)
        print(f"    -> {status}: {msg}")
        if ok:
            successes += 1
        else:
            failures += 1
        time.sleep(random.uniform(3, 8))  # ponytail: don't hammer the site between applies
    driver.quit()
    conn.close()
    print(f"\ndone: {successes} applied, {failures} failed")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--login" in args:
        i = args.index("--login")
        login(args[i + 1] if i + 1 < len(args) else "")
    elif "--apply" in args:
        apply_all(
            dry_run="--dry-run" in args,
            min_score=int(args[args.index("--min-score") + 1]) if "--min-score" in args else None,
            picks_only="--picks" in args,
            site=args[args.index("--site") + 1] if "--site" in args else None,
        )
    else:
        print(__doc__)
        print("  flags: --dry-run  --min-score N  --picks  --site <name>")
