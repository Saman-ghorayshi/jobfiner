"""mock test: full SMS verification round-trip against a fake jobinja page.

injects a fake SMS into the ADB layer, loads mock_jobinja.html, runs the same
detect-popup → get_code → apply_sms_code → click submit → check success flow
that apply.py uses.

run:  python test_sms_mock.py
"""
import os, sys, time
from unittest.mock import patch

os.chdir(os.path.join(os.path.dirname(__file__)))

# inject fake SMS into _adb_latest_sms BEFORE importing sms
import sms as sms_mod
_FAKE_SMS = ("+9810000004347", "«جابینجا» کد تایید: 042069")
sms_mod._adb_latest_sms = lambda: _FAKE_SMS
# also mock get_code so it never touches real adb — just returns our fake code
sms_mod.get_code_adb = lambda timeout=10, sender_filter=None: (
    (_extract := sms_mod._extract_code)(_FAKE_SMS[1])
)

from sms import get_code, _extract_code, apply_sms_code
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from browser import make_driver

MOCK_HTML = os.path.abspath(os.path.join(os.path.dirname(__file__), "mock_jobinja.html"))

def test_round_trip():
    # 1. get_code returns the injected fake code
    code = get_code(timeout=5, sender_filter="jobinja")
    assert code and _extract_code(_FAKE_SMS[1]) == code, f"get_code failed: {code}"
    print(f"  [1] get_code returned: {code}")

    # 2. load mock page
    driver = make_driver()
    driver.get(f"file:///{MOCK_HTML}")
    time.sleep(1)

    # 3. detect verification page (same xpath apply.py uses)
    try:
        driver.find_element(By.XPATH,
            '//*[contains(text(),"اعتبارسنجی") or contains(text(),"کد تایید") or contains(text(),"کد را وارد")]')
        print("  [2] verification popup detected on mock page")
    except NoSuchElementException:
        driver.quit()
        assert False, "mock page doesn't have verification text — broken mock"

    # 4. fill code via apply_sms_code
    filled = apply_sms_code(driver, code)
    if not filled:
        driver.quit()
        assert False, "apply_sms_code couldn't find the input on the mock page"
    print(f"  [3] apply_sms_code filled code into input")

    # 5. verify the input has the code
    inp = driver.find_element(By.CSS_SELECTOR, "input[name='phone_verify_code']")
    assert inp.get_attribute("value") == code, f"input value='{inp.get_attribute('value')}' != {code}"
    print(f"  [4] input value matches code: {inp.get_attribute('value')}")

    # 6. click submit (same xpath apply.py uses)
    btn = driver.find_element(By.XPATH,
        '//button[contains(text(),"تایید") or contains(text(),"ارسال") or contains(text(),"ثبت")]')
    btn.click()
    time.sleep(1)

    # 7. check success message (same xpath apply.py uses)
    try:
        driver.find_element(By.XPATH,
            '//*[contains(text(),"با موفقیت") or contains(text(),"ارسال شد")]')
        print("  [5] success message found after submit")
        print("\n MOCK TEST PASSED — full round-trip works")
    except NoSuchElementException:
        driver.quit()
        assert False, "success message not found after clicking submit"

    driver.quit()

if __name__ == "__main__":
    test_round_trip()
