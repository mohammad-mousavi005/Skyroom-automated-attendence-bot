"""
SkyRoom Chat Monitor Bot
=========================
وارد اسکای‌روم می‌شود، پیام‌های چت را مانیتور می‌کند.
هر ۵۰ پیام جدید را به OpenAI می‌فرستد.
اگر همه آن‌ها فقط اسم و فامیل فارسی باشند، اسم کاربر را در چت می‌فرستد.

نیازمندی‌ها:
    pip install selenium requests
"""

import os
import sys
import time
import logging
import urllib3
from typing import List, Tuple, Set
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import requests

# غیرفعال کردن هشدارهای SSL برای API کاستوم
urllib3.disable_warnings()


# =============================================================================
# ====== کانفیگ (اینجا تغییر بده) ============================================
# =============================================================================

# آدرس اتاق اسکای‌روم
SKYROOM_URL = "https://www.skyroom.online/ch/virtualtums/medicinetums"

# اسم مهمان برای ورود
GUEST_NAME = "مهمان"

# اسم و فامیل شما که در صورت تأیید هوش مصنوعی ارسال می‌شود
MY_FULL_NAME = "اسم فامیل تو"

# کلید API (OpenAI-compatible)
OPENAI_API_KEY = ""

# آدرس بیس API کاستوم (OpenAI-compatible endpoint)
OPENAI_BASE_URL = "http://127.0.0.1:5001/v1"

# تعداد پیام در هر بچ برای بررسی
BATCH_SIZE = 50

# فاصله زمانی بررسی پیام‌های جدید (ثانیه)
CHECK_INTERVAL = 5

# مدل OpenAI برای تحلیل
OPENAI_MODEL = "deepseek-v4-pro-nothinking"

# حداکثر زمان انتظار برای لود عناصر (ثانیه)
WAIT_TIMEOUT = 20

# =============================================================================
# ====== تنظیمات لاگ =========================================================
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# =============================================================================
# ====== توابع اصلی ==========================================================
# =============================================================================

def create_driver() -> webdriver.Chrome:
    """ایجاد و کانفیگ درایور کروم."""
    options = Options()
    options.add_argument("--start-maximized")
    # options.add_argument("--headless")  # اگر نمی‌خواهی پنجره مرورگر دیده شود، کامنت رو بردار
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # استفاده از chromedriver.exe محلی (در کنار اسکریپت)
    driver_path = os.path.join(os.path.dirname(__file__), "chromedriver.exe")
    if not os.path.exists(driver_path):
        raise FileNotFoundError(
            f"❌ chromedriver.exe پیدا نشد!\n"
            f"لطفاً chromedriver.exe را از https://googlechromelabs.github.io/chrome-for-testing/ دانلود کرده\n"
            f"و در مسیر زیر قرار دهید:\n{os.path.abspath(driver_path)}"
        )
    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=options)
    return driver


def dismiss_alerts(driver: webdriver.Chrome):
    """بستن alertهای احتمالی."""
    try:
        alert = driver.switch_to.alert
        alert.accept()
        logger.info("Alert بسته شد.")
    except Exception:
        pass


def guest_login(driver: webdriver.Chrome):
    """
    ورود به عنوان مهمان در اسکای‌روم.
    مرحله ۱: کلیک روی دکمه "مهمان"
    مرحله ۲: انتظار برای باز شدن فرم و پر کردن نام
    مرحله ۳: کلیک روی دکمه ورود
    """
    logger.info("در حال ورود مهمان...")

    # ==================== مرحله ۱: کلیک روی دکمه مهمان ====================
    guest_btn = None
    guest_btn_selectors = [
        (By.XPATH, "//button[contains(text(),'مهمان')]"),
        (By.XPATH, "//a[contains(text(),'مهمان')]"),
        (By.XPATH, "//*[contains(@class,'guest')]/button"),
        (By.XPATH, "//*[contains(@class,'guest')]/a"),
        (By.ID, "guestBtn"),
        (By.ID, "guestLogin"),
        (By.CLASS_NAME, "guest-btn"),
        (By.CSS_SELECTOR, "button[class*='guest']"),
        (By.CSS_SELECTOR, "a[class*='guest']"),
    ]

    for by, selector in guest_btn_selectors:
        try:
            guest_btn = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((by, selector))
            )
            if guest_btn.is_displayed():
                logger.info(f"دکمه مهمان با {by}='{selector}' پیدا شد.")
                break
        except (TimeoutException, NoSuchElementException):
            continue

    if guest_btn is None:
        # تلاش عمومی: اولین button یا a که متن "مهمان" دارد
        for elem in driver.find_elements(By.XPATH, "//button | //a"):
            try:
                if "مهمان" in elem.text and elem.is_displayed():
                    guest_btn = elem
                    logger.info("دکمه مهمان با جستجوی عمومی پیدا شد.")
                    break
            except Exception:
                continue

    if guest_btn is None:
        raise RuntimeError("❌ دکمه مهمان پیدا نشد. Selectorها رو تنظیم کن.")

    guest_btn.click()
    logger.info("✅ روی دکمه مهمان کلیک شد.")

    # ==================== مرحله ۲: انتظار برای باز شدن فرم ====================
    time.sleep(2)  # صبر برای انیمیشن

    # ==================== پیدا کردن فیلد نام ====================
    name_input = None
    name_selectors = [
        (By.ID, "guestName"),
        (By.ID, "nickname"),
        (By.NAME, "guestname"),
        (By.NAME, "nickname"),
        (By.CSS_SELECTOR, "input[placeholder*='نام']"),
        (By.CSS_SELECTOR, "input[placeholder*='اسم']"),
        (By.CSS_SELECTOR, "input[type='text']"),
    ]

    for by, selector in name_selectors:
        try:
            name_input = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((by, selector))
            )
            if name_input.is_displayed():
                logger.info(f"فیلد نام با {by}='{selector}' پیدا شد.")
                break
        except TimeoutException:
            continue

    if name_input is None:
        inputs = driver.find_elements(By.TAG_NAME, "input")
        for inp in inputs:
            if inp.get_attribute("type") in ("text", None, "") and inp.is_displayed():
                name_input = inp
                logger.info("فیلد نام با جستجوی عمومی پیدا شد.")
                break

    if name_input is None:
        raise RuntimeError("❌ فیلد ورود نام پیدا نشد. Selectorها رو تنظیم کن.")

    name_input.clear()
    name_input.send_keys(GUEST_NAME)
    logger.info(f"نام '{GUEST_NAME}' وارد شد.")

    # ==================== مرحله ۳: دکمه ورود نهایی ====================
    submit_btn = None
    submit_selectors = [
        (By.XPATH, "//button[contains(text(),'ورود')]"),
        (By.XPATH, "//button[contains(text(),'تأیید')]"),
        (By.XPATH, "//button[contains(text(),'ادامه')]"),
        (By.XPATH, "//button[contains(text(),'بعدی')]"),
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.ID, "guestLoginBtn"),
        (By.ID, "joinBtn"),
        (By.CLASS_NAME, "guest-submit"),
    ]

    for by, selector in submit_selectors:
        try:
            submit_btn = driver.find_element(by, selector)
            if submit_btn.is_displayed():
                logger.info(f"دکمه ورود با {by}='{selector}' پیدا شد.")
                break
        except NoSuchElementException:
            continue

    if submit_btn is None:
        buttons = driver.find_elements(By.TAG_NAME, "button")
        for btn in buttons:
            if btn.is_displayed() and ("ورود" in btn.text or "تأیید" in btn.text or "ادامه" in btn.text):
                submit_btn = btn
                logger.info("دکمه ورود با جستجوی عمومی پیدا شد.")
                break

    if submit_btn is None:
        raise RuntimeError("❌ دکمه ورود پیدا نشد.")

    submit_btn.click()
    logger.info("✅ روی دکمه ورود کلیک شد.")


def get_messages(driver: webdriver.Chrome) -> List[str]:
    """
    استخراج تمام پیام‌های فعلی چت از DOM.
    برمی‌گرداند لیستی از متن پیام‌ها.
    """
    messages = []

    # Selectorهای احتمالی برای پیام‌ها
    msg_selectors = [
        ".chat-message",
        ".message",
        ".msg",
        "[class*='message']",
        "[class*='chat-message']",
        ".chat-item",
        ".chat-msg",
        ".chat-message-text",
        "div[class*='msg'] div[class*='text']",
        "//div[contains(@class,'message')]//span",
        "//div[contains(@class,'msg')]",
    ]

    elements = []
    for selector in msg_selectors:
        try:
            if selector.startswith("//"):
                elements = driver.find_elements(By.XPATH, selector)
            else:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
            if elements:
                logger.info(f"پیام‌ها با selector '{selector}' پیدا شدند: {len(elements)} عدد")
                break
        except Exception:
            continue

    if not elements:
        logger.warning("⚠️ هیچ پیامی در DOM پیدا نشد. شاید هنوز لود نشده یا selector اشتباه است.")
        return []

    for el in elements:
        text = el.text.strip()
        if text:
            messages.append(text)

    return messages


def send_chat_message(driver: webdriver.Chrome, text: str):
    """ارسال یک پیام در چت اسکای‌روم."""
    logger.info(f"در حال ارسال پیام: {text}")

    # ===== پیدا کردن فیلد ورودی چت =====
    chat_input = None
    input_selectors = [
        (By.ID, "chatInput"),
        (By.ID, "txtMsg"),
        (By.CLASS_NAME, "chat-input"),
        (By.CSS_SELECTOR, "textarea[placeholder*='پیام']"),
        (By.CSS_SELECTOR, "input[placeholder*='پیام']"),
        (By.CSS_SELECTOR, "[contenteditable='true']"),
    ]

    for by, selector in input_selectors:
        try:
            chat_input = driver.find_element(by, selector)
            if chat_input.is_displayed():
                logger.info(f"فیلد چت با {by}='{selector}' پیدا شد.")
                break
        except NoSuchElementException:
            continue

    if chat_input is None:
        raise RuntimeError("❌ فیلد ورودی چت پیدا نشد.")

    chat_input.clear()
    chat_input.send_keys(text)

    # ===== پیدا کردن دکمه ارسال =====
    send_btn = None
    send_selectors = [
        (By.ID, "sendBtn"),
        (By.ID, "btnSend"),
        (By.CLASS_NAME, "send-btn"),
        (By.CSS_SELECTOR, "button[aria-label='ارسال']"),
        (By.XPATH, "//button[contains(@class,'send')]"),
        (By.XPATH, "//button[contains(text(),'ارسال')]"),
        (By.CSS_SELECTOR, ".chat-send button"),
    ]

    for by, selector in send_selectors:
        try:
            send_btn = driver.find_element(by, selector)
            if send_btn.is_displayed():
                logger.info(f"دکمه ارسال با {by}='{selector}' پیدا شد.")
                break
        except NoSuchElementException:
            continue

    if send_btn is None:
        raise RuntimeError("❌ دکمه ارسال پیام پیدا نشد.")

    send_btn.click()
    logger.info(f"✅ پیام ارسال شد.")


def analyze_with_openai(messages: List[str]) -> Tuple[bool, str]:
    """
    بررسی می‌کند که آیا تمام پیام‌ها فقط شامل اسم و فامیل فارسی هستند.
    برمی‌گرداند (True/False, توضیح).
    """
    if not messages:
        return False, "لیست پیام‌ها خالی است."

    # ساخت پرامپت
    numbered_msgs = "\n".join([f"{i+1}. {msg}" for i, msg in enumerate(messages)])
    prompt = f"""به پیام‌های زیر که از یک چت آنلاین جمع‌آوری شده‌اند نگاه کن.

فقط و فقط با یک عبارت YES یا NO پاسخ بده.

YES: اگر **تمام** {len(messages)} پیام صرفاً اسم و فامیل افراد به زبان فارسی باشند (و نه هیچ چیز دیگری — نه پیام تبلیغاتی، نه احوالپرسی، نه سؤال، نه پیام خداحافظی، نه عدد، نه اسم انگلیسی).
NO: اگر حتی یکی از پیام‌ها اسم و فامیل فارسی نباشد یا شامل متن اضافی باشد.

لیست پیام‌ها:
{numbered_msgs}

فقط YES یا NO:"""

    try:
        url = f"{OPENAI_BASE_URL}/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": OPENAI_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 5,
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=30, verify=False)
        resp.raise_for_status()
        data = resp.json()
        answer = data["choices"][0]["message"]["content"].strip().upper()
        logger.info(f"پاسخ OpenAI: {answer}")

        if answer == "YES":
            return True, "تمامی پیام‌ها اسم و فامیل فارسی هستند."
        else:
            return False, f"حداقل یک پیام فقط اسم و فامیل فارسی نیست. (پاسخ: {answer})"
    except Exception as e:
        logger.error(f"خطا در فراخوانی OpenAI: {e}")
        return False, f"خطا: {e}"


# =============================================================================
# ====== حلقه اصلی ===========================================================
# =============================================================================

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    logger.info("=" * 60)
    logger.info("🚀 SkyRoom Bot شروع به کار کرد.")
    logger.info(f"آدرس: {SKYROOM_URL}")
    logger.info(f"اسم مهمان: {GUEST_NAME}")
    logger.info(f"بچ‌سایز: {BATCH_SIZE} | اینتروال: {CHECK_INTERVAL}s")
    logger.info("=" * 60)

    driver = create_driver()
    seen_messages: Set[str] = set()
    new_batch: List[str] = []

    try:
        # 1. باز کردن سایت
        logger.info("باز کردن سایت...")
        driver.get(SKYROOM_URL)

        # 2. صبر برای لود صفحه لاگین
        time.sleep(5)
        dismiss_alerts(driver)

        # 3. لاگین مهمان
        guest_login(driver)

        # 4. صبر برای لود کامل اتاق و چت
        logger.info("منتظر لود اتاق و چت...")
        time.sleep(10)
        dismiss_alerts(driver)

        # 5. حلقه اصلی مانیتورینگ
        logger.info("🔍 شروع مانیتورینگ پیام‌ها...")
        while True:
            try:
                current_messages = get_messages(driver)
                if not current_messages:
                    logger.info("هنوز پیامی دریافت نشده. منتظر...")
                    time.sleep(CHECK_INTERVAL)
                    continue

                # تشخیص پیام‌های جدید
                for msg in current_messages:
                    if msg not in seen_messages:
                        seen_messages.add(msg)
                        new_batch.append(msg)
                        logger.info(f"📩 پیام جدید: {msg[:80]}{'...' if len(msg)>80 else ''}")

                logger.info(f"وضعیت بچ: {len(new_batch)}/{BATCH_SIZE}")

                # اگر بچ پر شد
                if len(new_batch) >= BATCH_SIZE:
                    batch = new_batch[:BATCH_SIZE]
                    new_batch = new_batch[BATCH_SIZE:]  # مازاد برای بچ بعدی

                    logger.info(f"📦 ارسال {len(batch)} پیام به OpenAI برای تحلیل...")
                    is_all_names, reason = analyze_with_openai(batch)

                    if is_all_names:
                        logger.info(f"✅ تأیید شد! {reason}")
                        logger.info(f"📤 ارسال اسم و فامیل: {MY_FULL_NAME}")
                        send_chat_message(driver, MY_FULL_NAME)
                    else:
                        logger.info(f"❌ رد شد. {reason}")

                # استراحت
                time.sleep(CHECK_INTERVAL)

            except Exception as e:
                logger.error(f"خطا در حلقه اصلی: {e}")
                # تلاش برای ادامه
                time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        logger.info("⏹️ توقف دستی توسط کاربر.")
    except Exception as e:
        logger.error(f"💥 خطای بحرانی: {e}")
    finally:
        logger.info("بستن مرورگر...")
        driver.quit()
        logger.info("👋 پایان.")


if __name__ == "__main__":
    main()
