"""sms verification: receive jobinja's SMS code automatically.

three backends, tried in order:
  1. ADB (USB cable, android phone, offline) — read sms inbox directly
  2. Telegram bot (phone forwards sms to telegram, script reads it) — remote
  3. Webhook (phone posts sms to local http server) — same wifi, no internet

config in config.json under "sms":
  {"sms": {"method": "adb", "telegram": {"api_id": 1, "api_hash": "x", "bot_token": "x"},
           "webhook_port": 5000, "sender_filter": "jobinja"}}

usage from apply.py:
  from sms import get_code
  code = get_code(timeout=120)  # tries adb -> telegram -> webhook
  if code: fill it into the verification field
"""
import json, os, re, time, subprocess, threading

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def _load_sms_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f).get("sms", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _extract_code(text):
    """pull a 4-6 digit code from sms text. jobinja sends something like
    'کد تایید شما: 123456' or 'verification code: 123456'."""
    m = re.search(r"\b(\d{4,6})\b", text or "")
    return m.group(1) if m else None


# --- backend 1: ADB (USB) ---

def _adb_latest_sms():
    """read latest inbox sms via adb. returns (sender, body) or None."""
    # ponytail: older android doesn't support --limit, just query sorted desc and take first row
    cmd = ("adb shell content query --uri content://sms/inbox "
           "--projection address,body,date --sort 'date DESC'")
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, timeout=10,
                           encoding="utf-8", errors="replace")
        out = r.stdout.strip()
        if not out:
            return None
        first_line = out.split("\n")[0]  # sorted DESC so first = newest
        # ponytail: body can contain commas, so match up to 'date=' not next comma
        addr = re.search(r"address=(.*?),", first_line)
        body = re.search(r"body=(.*?),\s*date=", first_line)
        if not body:
            # last resort: body= to end of line
            body = re.search(r"body=(.*)$", first_line)
        if addr and body:
            return addr.group(1), body.group(1)
    except Exception as e:
        print(f"  adb error: {e}")
    return None


def get_code_adb(timeout=120, sender_filter=None):
    """poll adb inbox for a new sms with a code. returns code string or None."""
    print(f"  [adb] waiting for sms (USB)... timeout={timeout}s")
    old = _adb_latest_sms()
    old_body = old[1] if old else ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        sms = _adb_latest_sms()
        if sms and sms[1] != old_body:
            sender, body = sms
            if sender_filter and sender_filter.lower() not in sender.lower():
                continue
            code = _extract_code(body)
            if code:
                print(f"  [adb] got code: {code}")
                return code
        time.sleep(2)
    print("  [adb] timed out")
    return None


# --- backend 2: Telegram bot ---

def get_code_telegram(timeout=120, sender_filter=None):
    """listen on a telegram bot for forwarded sms. returns code or None."""
    cfg = _load_sms_config().get("telegram", {})
    api_id = cfg.get("api_id")
    api_hash = cfg.get("api_hash")
    bot_token = cfg.get("bot_token")
    if not all([api_id, api_hash, bot_token]):
        print("  [telegram] no config, skipping")
        return None
    try:
        from telethon import TelegramClient, events
    except ImportError:
        print("  [telegram] telethon not installed, skipping")
        return None

    print(f"  [telegram] listening for forwarded sms... timeout={timeout}s")
    result = {"code": None}
    done = threading.Event()

    client = TelegramClient(os.path.join(os.path.dirname(__file__), "sms_bot_session"),
                            api_id, api_hash)
    client.start(bot_token=bot_token)

    @client.on(events.NewMessage)
    async def handler(event):
        code = _extract_code(event.raw_text)
        if code:
            result["code"] = code
            done.set()

    import asyncio
    loop = asyncio.get_event_loop()
    # run for timeout seconds or until we get a code
    try:
        loop.run_until_complete(asyncio.wait_for(
            client.run_until_disconnected() if not done.is_set() else asyncio.sleep(0),
            timeout=timeout
        ))
    except asyncio.TimeoutError:
        pass
    client.disconnect()
    if result["code"]:
        print(f"  [telegram] got code: {result['code']}")
    else:
        print("  [telegram] timed out")
    return result["code"]


# --- backend 3: local webhook (same wifi) ---

def get_code_webhook(timeout=120, sender_filter=None):
    """start a tiny http server, wait for phone to POST sms. returns code or None."""
    from http.server import HTTPServer, BaseHTTPRequestHandler
    port = _load_sms_config().get("webhook_port", 5000)
    result = {"code": None}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            try:
                data = json.loads(body)
                msg = data.get("body", "") or data.get("message", "")
            except json.JSONDecodeError:
                msg = body
            code = _extract_code(msg)
            if code:
                result["code"] = code
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *args):
            pass  # shut up, keep terminal clean

    print(f"  [webhook] listening on :{port}... timeout={timeout}s")
    print(f"  [webhook] set your phone SMS forwarder to POST to http://YOUR_LAPTOP_IP:{port}/sms")
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.timeout = 1  # check loop every 1s
    deadline = time.time() + timeout
    while time.time() < deadline:
        server.handle_request()
        if result["code"]:
            break
    server.server_close()
    if result["code"]:
        print(f"  [webhook] got code: {result['code']}")
    else:
        print("  [webhook] timed out")
    return result["code"]


# --- unified interface ---

def get_code(timeout=120, sender_filter="jobinja"):
    """try all backends in order until one returns a code. returns code string or None."""
    cfg = _load_sms_config()
    method = cfg.get("method", "auto")  # auto = try all in order

    backends = {
        "adb": lambda: get_code_adb(timeout, sender_filter),
        "telegram": lambda: get_code_telegram(timeout, sender_filter),
        "webhook": lambda: get_code_webhook(timeout, sender_filter),
    }

    if method != "auto" and method in backends:
        return backends[method]()

    # auto: try each in order, first code wins
    for name, fn in backends.items():
        code = fn()
        if code:
            return code
    return None


def apply_sms_code(driver, code):
    """find the verification input on the page and type the code. returns True on success."""
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import NoSuchElementException
    # ponytail: jobinja popup input selectors — try specific patterns first, then broad
    selectors = [
        "input[name*='code']", "input[name*='verify']", "input[name*='otp']",
        "input[placeholder*='کد']", "input[placeholder*='تایید']",
        "input[name*='token']", "input[type='tel'][name*='code']",
        # broader: any visible text/tel input inside a popup-like container
        "input[type='tel']", "input[type='text'][maxlength='4']",
        "input[type='text'][maxlength='5']", "input[type='text'][maxlength='6']",
        "input[type='number']",
    ]
    for selector in selectors:
        try:
            inp = driver.find_element(By.CSS_SELECTOR, selector)
            if not inp.is_displayed():
                continue
            inp.clear()
            for ch in code:
                inp.send_keys(ch)
                time.sleep(0.1)
            return True
        except NoSuchElementException:
            continue
    return False


if __name__ == "__main__":
    # self-check: _extract_code works on jobinja-style sms
    assert _extract_code("کد تایید شما: 123456") == "123456"
    assert _extract_code("verification code: 54321") == "54321"
    assert _extract_code("your code is 9876") == "9876"
    assert _extract_code("no code here") is None
    print("extract_code ok")

    # adb: quick check if device is connected (5s timeout, non-blocking)
    try:
        r = subprocess.run("adb devices", shell=True, capture_output=True, text=True, timeout=5)
        lines = [l.strip() for l in r.stdout.strip().split("\n") if l.strip() and "List" not in l]
        print(f"adb devices: {lines if lines else 'none connected'}")
    except Exception:
        print("adb not reachable (install platform-tools or connect phone)")

    print("sms module ok — run get_code(timeout=120) in apply flow")
