import json, os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

PERSIAN_CITIES = [
    "تهران", "البرز", "اصفهان", "مشهد", "شیراز", "کرج", "یزد", "هرمزگان",
    "گیلان", "مازندران", "آذربایجان", "خوزستان", "کرمان", "البرز",
]

AI_PROVIDERS = ["gemini", "openai", "anthropic", "openrouter"]


def ask(prompt, default=None):
    if default:
        prompt = f"{prompt} [{default}]: "
    else:
        prompt = f"{prompt}: "
    ans = input(prompt).strip()
    if not ans and default:
        return default
    return ans


def ask_multi(prompt, options):
    """show numbered list, user picks multiple by number or name"""
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    ans = ask(prompt + " (comma-separated numbers, e.g. 1,3)")
    picked = []
    for part in ans.split(","):
        part = part.strip()
        if not part:
            continue
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(options):
                picked.append(options[idx])
        else:
            picked.append(part.strip())
    return picked


def run_wizard():
    print("=== jobfiner wizard ===\n")

    config = {}

    # tags
    print("what tags do you want to match? (e.g. cpp, c++, python, برنامه‌نویس)")
    print("type your tags separated by commas:")
    tags_input = ask("tags")
    tags = [t.strip() for t in tags_input.split(",")]
    config["tags"] = tags

    # cities
    print("\nwhich cities?")
    cities = ask_multi("cities", PERSIAN_CITIES)
    config["cities"] = cities

    # job types
    print("\njob type?")
    job_types = ask_multi("job types", ["تمام وقت", "پاره وقت", "دورکاری", "کارآموزی"])
    config["job_types"] = job_types

    # ai provider
    print("\nai provider for ranking jobs:")
    print("  1. gemini (free tier)")
    print("  2. openai (or any openai-compatible)")
    print("  3. anthropic")
    print("  4. openrouter (free models, many providers)")
    provider = ask("provider number", "4")
    provider_map = {"1": "gemini", "2": "openai", "3": "anthropic", "4": "openrouter"}
    config["ai"] = {"provider": provider_map.get(provider, "openrouter")}

    api_key = ask("api key")
    config["ai"]["api_key"] = api_key

    if config["ai"]["provider"] == "gemini":
        config["ai"]["model"] = ask("model", "gemini-2.0-flash")
    elif config["ai"]["provider"] == "openai":
        config["ai"]["model"] = ask("model", "gpt-4o-mini")
        config["ai"]["base_url"] = ask("base url (enter for default)", "https://api.openai.com/v1")
    elif config["ai"]["provider"] == "anthropic":
        config["ai"]["model"] = ask("model", "claude-sonnet-4-20250514")
    elif config["ai"]["provider"] == "openrouter":
        config["ai"]["model"] = ask("model", "openai/gpt-oss-20b:free")

    # proxy
    print("\nproxy (for sites that might block you)")
    print("  default is your socks proxy, or leave empty for none")
    proxy = ask("proxy url", "socks5://127.0.0.1:10808")
    if proxy.lower() in ("none", "n", ""):
        proxy = ""
    config["proxy"] = proxy

    # cv path
    print("\ncv/pdf path (or enter for dummy)")
    cv_path = ask("cv path", "cv.pdf")
    config["cv_path"] = cv_path

    # check interval
    print("\nhow often to check for new jobs?")
    interval = ask("interval (minutes)", "30")
    config["interval_minutes"] = int(interval)

    # auto apply
    print("\nauto-apply to matched jobs? (needs site logins later)")
    auto = ask("auto-apply? y/n", "n")
    config["auto_apply"] = auto.lower() in ("y", "yes")

    # sms verification (for jobinja auto-apply)
    print("\nsms verification for jobinja's phone code?")
    print("  1. adb (android phone via usb, offline)")
    print("  2. telegram bot (phone forwards sms to telegram)")
    print("  3. webhook (phone posts to local http server)")
    print("  skip if you'll pre-verify your phone on jobinja manually")
    sms_method = ask("method (enter to skip)")
    sms_map = {"1": "adb", "2": "telegram", "3": "webhook"}
    if sms_method in sms_map:
        config["sms"] = {"method": sms_map[sms_method], "sender_filter": "jobinja"}
        if sms_method == "2":
            print("  telegram needs: api_id, api_hash, bot_token (from @BotFather)")
            config["sms"]["telegram"] = {
                "api_id": ask("telegram api_id"),
                "api_hash": ask("telegram api_hash"),
                "bot_token": ask("bot_token"),
            }
        elif sms_method == "3":
            config["sms"]["webhook_port"] = int(ask("webhook port", "5000"))
    else:
        config["sms"] = {"method": "manual", "sender_filter": "jobinja"}

    # save
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    print(f"\nconfig saved to {CONFIG_PATH}")
    print("next steps:")
    print("  1. edit profile.md — add your phone, cv path, about me, common answers")
    print("  2. python main.py — scrape, rank, pick jobs")
    print("  3. python apply.py --login <site> — log in to each site")
    print("  4. python apply.py --apply --picks — apply to dashboard picks")


if __name__ == "__main__":
    run_wizard()
