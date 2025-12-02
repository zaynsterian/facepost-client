import os
import sys
import json
import time
import threading
import hashlib
from pathlib import Path
from datetime import datetime, timedelta, time as dtime, timezone
import platform
import tempfile
import shutil
import webbrowser
import subprocess

import requests
import tkinter as tk
from tkinter import messagebox, filedialog

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.keys import Keys

# ================== CONFIG GLOBALĂ ==================

APP_NAME = "Facepost"
API_URL = "https://facepost.onrender.com"
CONFIG_FILE = Path.home() / ".facepost_config.json"
CHROMEDRIVER_NAME = "chromedriver.exe"  # în același folder cu EXE-ul
LOGIN_DRIVER: webdriver.Chrome | None = None
CLIENT_VERSION = "2.3.0"

UTC = timezone.utc

DEFAULT_CONFIG = {
    "email": "",
    "device_id": "",
    "server_url": API_URL,
    "chrome_profile_dir": "",
    "schedule_enabled_morning": False,
    "schedule_time_morning": "08:00",
    "schedule_enabled_evening": False,
    "schedule_time_evening": "20:00",
    "interval_enabled": False,
    "interval_minutes": 60,
    "post_text": "",
    "groups_text": "",
    "images": [],
    "delay_seconds": 120,
    "simulate": False,
}


# ================== CONFIG HELPERI ==================

def stable_fingerprint() -> str:
    """Fingerprint stabil pentru device (hash din info de sistem)."""
    raw = f"{platform.node()}|{platform.system()}|{platform.machine()}|{platform.version()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    else:
        data = {}

    cfg = DEFAULT_CONFIG.copy()
    cfg.update(data)

    # device_id stabil
    if not cfg.get("device_id"):
        cfg["device_id"] = stable_fingerprint()

    # director profil Chrome
    if not cfg.get("chrome_profile_dir"):
        base = Path.home() / ".facepost_chrome_profile"
        base.mkdir(parents=True, exist_ok=True)
        cfg["chrome_profile_dir"] = str(base)

    # migrare setări vechi de schedule (dacă există)
    if cfg.get("schedule_enabled") and not (
        cfg.get("schedule_enabled_morning") or cfg.get("schedule_enabled_evening")
    ):
        cfg["schedule_enabled_morning"] = True
        cfg["schedule_time_morning"] = cfg.get("schedule_time", "08:00")

    return cfg


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Eroare la salvare config:", e)


CONFIG = load_config()
# forțăm mereu același server URL (nu se poate modifica din UI)
CONFIG["server_url"] = API_URL


# ================== API LICENȚE & LOGS ==================

def api_post(path: str, payload: dict) -> dict:
    url = f"{CONFIG.get('server_url', API_URL).rstrip('/')}{path}"
    try:
        r = requests.post(url, json=payload, timeout=15)
        try:
            data = r.json()
        except Exception:
            data = {"error": f"HTTP {r.status_code}"}
        data["_http"] = r.status_code
        return data
    except Exception as e:
        return {"error": str(e), "_http": 0}


def bind_license(email: str, fingerprint: str) -> dict:
    """Leagă device-ul de licență: POST /bind"""
    return api_post("/bind", {"email": email, "fingerprint": fingerprint})


def check_license(email: str, fingerprint: str) -> dict:
    """Verifică licența pentru device: POST /check"""
    return api_post("/check", {"email": email, "fingerprint": fingerprint})


def log_run(groups, text: str, images):
    """
    Trimite către server un log simplu pentru fiecare RUN:
    - email, fingerprint, group_urls, post_text, images_count
    """
    email = (CONFIG.get("email") or "").strip().lower()
    fingerprint = CONFIG.get("device_id") or ""
    if not email:
        return {"error": "no email in config"}

    group_urls = "\n".join([g.strip() for g in (groups or []) if g.strip()])

    payload = {
        "email": email,
        "fingerprint": fingerprint,
        "group_urls": group_urls,
        "post_text": text or "",
        "images_count": len(images or []),
    }
    return api_post("/log_run", payload)


# ================== SELENIUM / CHROMEDRIVER ==================

def get_chromedriver_path() -> str:
    """
    Caută chromedriver.exe:
    - în același folder cu executabilul (Facepost.exe)
    - apoi în current working directory
    """
    exe_dir = Path(getattr(sys, "_MEIPASS", Path.cwd()))
    candidate = exe_dir / CHROMEDRIVER_NAME
    if candidate.exists():
        return str(candidate)
    candidate = Path.cwd() / CHROMEDRIVER_NAME
    return str(candidate)


def create_driver() -> webdriver.Chrome:
    """Pornește Chrome cu profilul dedicat Facepost."""
    chrome_opts = webdriver.ChromeOptions()
    profile_dir = CONFIG.get("chrome_profile_dir")
    if profile_dir:
        chrome_opts.add_argument(f"--user-data-dir={profile_dir}")
    chrome_opts.add_argument("--disable-notifications")
    chrome_opts.add_argument("--disable-infobars")
    chrome_opts.add_argument("--start-maximized")

    service = Service(get_chromedriver_path())
    driver = webdriver.Chrome(service=service, options=chrome_opts)
    return driver


# ================== CONFIGURARE LOGIN FACEBOOK ==================

def configure_facebook_login(
    parent: tk.Tk | None = None,
    mode: str = "login",
):
    """
    Ghidează userul să configureze sau să schimbe login-ul în Facebook.

    mode:
      - "login"  -> prima conectare / conectare normală
      - "switch" -> schimbarea profilului de Facebook (delogare + relogare)
    """
    global LOGIN_DRIVER

    if mode == "switch":
        instr = (
            "Vom schimba profilul de Facebook folosit de Facepost.\n\n"
            "1. După ce apeși OK, se va deschide o fereastră de Chrome cu profilul Facepost.\n"
            "2. În fereastra de Facebook, deloghează-te din contul curent dacă este cazul.\n"
            "3. Conectează-te în noul cont de Facebook pe care vrei să îl folosești.\n"
            "4. După ce ești logat în noul cont, pur și simplu închide fereastra de Chrome.\n\n"
            "Facepost va folosi de acum acest cont până când decizi să îl schimbi din nou."
        )
    else:
        instr = (
            "Vom configura logarea în Facebook.\n\n"
            "1. După ce apeși OK, se va deschide o fereastră de Chrome cu profilul Facepost.\n"
            "2. Conectează-te în contul tău de Facebook (introdu emailul și parola și finalizează login-ul).\n"
            "3. După ce ești logat, pur și simplu închide fereastra de Chrome.\n\n"
            "Nu trebuie să mai faci nimic în Facepost pentru acest pas.\n"
            "La următoarele rulări, vei fi conectat automat în acest cont."
        )

    # 1) Instrucțiuni ÎNAINTE de deschiderea Chrome
    messagebox.showinfo(
        APP_NAME,
        instr,
        parent=parent,
    )

    # Dacă aveam deja un driver de login deschis, încercăm să îl închidem curat
    if LOGIN_DRIVER is not None:
        try:
            LOGIN_DRIVER.quit()
        except Exception:
            pass
        LOGIN_DRIVER = None

    # 2) Pornim Chrome cu profilul Facepost
    try:
        driver = create_driver()
    except WebDriverException as e:
        messagebox.showerror(
            APP_NAME,
            f"Nu pot porni Chrome.\n"
            f"Verifică dacă {CHROMEDRIVER_NAME} este lângă Facepost.exe.\n\n{e}",
            parent=parent,
        )
        return

    # ținem driverul într-o variabilă globală ca să NU fie închis de GC
    LOGIN_DRIVER = driver

    # 3) Deschidem Facebook pentru login/schimbare profil
    try:
        driver.get("https://www.facebook.com/")
    except Exception as e:
        messagebox.showerror(
            APP_NAME,
            f"A apărut o eroare la deschiderea Facebook.\n\n{e}",
            parent=parent,
        )
        # dacă a murit ceva grav, închidem driverul și resetăm globalul
        try:
            driver.quit()
        except Exception:
            pass
        LOGIN_DRIVER = None
        return

    # NU mai afișăm alt mesaj aici; userul lucrează liniștit în Chrome,
    # se loghează (sau schimbă profilul) și când termină închide fereastra de Chrome.

def wait_for_facebook_home(driver: webdriver.Chrome, timeout: int = 60):
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )


# ================== LOGICA DE POSTARE ==================

def sanitize_for_chromedriver(text: str) -> str:
    """
    ChromeDriver nu suportă caractere în afara BMP (ex: unele emoji).
    Filtrăm caracterele cu codepoint > 0xFFFF ca să nu arunce eroare.
    """
    if not text:
        return text
    safe_chars = []
    for ch in text:
        if ord(ch) <= 0xFFFF:
            safe_chars.append(ch)
        else:
            # caracterele problematice le aruncăm; poți pune în loc "?" dacă vrei
            continue
    return "".join(safe_chars)

def set_text_via_js(driver, element, text: str):
    """
    Setează textul într-un contenteditable folosind JavaScript,
    astfel încât:
      - să accepte orice emoji / caractere Unicode (nu folosim send_keys)
      - să păstreze line-break-urile (paragrafele) ca în textul original
      - să folosească mecanismul nativ de input (execCommand),
        ca editorul Facebook să vadă textul ca și cum ar fi tastat/paste-uit.
    """
    js = r"""
var container = arguments[0];
var text = arguments[1];
if (!container) return;

// dăm focus pe container ca să activeze editorul
container.focus();

// adevăratul element de input este, de obicei, document.activeElement
var target = document.activeElement || container;

if (typeof document.execCommand === 'function') {
  try {
    target.focus();
    // selectăm și ștergem tot ce era înainte
    document.execCommand('selectAll', false, null);
    document.execCommand('delete', false, null);
  } catch (e) {}

  // aici NU mai împărțim textul noi – îl trimitem întreg, cu \n în el
  // editorul știe singur cum să transforme newline-urile în <br>/paragrafe
  try {
    target.focus();
    document.execCommand('insertText', false, text);
  } catch (e) {
    // fallback brut dacă insertText e blocat
    target.textContent = text;
  }
} else {
  // fallback foarte brut dacă execCommand nu există deloc
  target.textContent = text;
}

// notificăm React / Facebook că s-a schimbat conținutul
var ev = new Event("input", {bubbles: true});
target.dispatchEvent(ev);
"""
    driver.execute_script(js, element, text)

def open_group_and_post(driver: webdriver.Chrome,
                        group_url: str,
                        text: str,
                        images,
                        simulate: bool = False) -> None:
    """
    Deschide un link de grup și postează textul + imaginile.

    1. Navighează în grup
    2. Găsește composer-ul folosind:
       - data-pagelet="GroupInlineComposer" + role="button"
       - texte RO/EN: "Scrie ceva", "Scrie acum", "Scrie o postare", "Create post", etc.
    3. Găsește textbox-ul din composer (nu din comentarii)
    4. Scrie textul, atașează imagini, apasă Postează.
    """

    def try_click_xpaths(xpaths, log_prefix="composer"):
        """Încearcă pe rând mai multe XPATH-uri până reușește un click."""
        for xp in xpaths:
            try:
                el = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, xp))
                )
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", el
                )
                el.click()
                print(f"[DEBUG] {log_prefix} click cu XPATH: {xp}")
                return True
            except Exception:
                continue
        return False

    try:
        print(f"[DEBUG] Navighez la {group_url}")
        driver.get(group_url)

        # așteptăm încărcarea paginii grupului
        wait_for_facebook_home(driver, timeout=60)
        time.sleep(3)  # mic delay pentru componentele dinamice

        if simulate:
            print("[DEBUG] Simulare activă – nu postez efectiv.")
            return

        # --- 1. Caută butonul de composer în interiorul GroupInlineComposer ---

        group_inline_xpaths = [
            # cu text explicit în span
            "//div[@data-pagelet='GroupInlineComposer']"
            "//div[@role='button'][.//span[contains(text(),'Scrie ceva')]]",

            "//div[@data-pagelet='GroupInlineComposer']"
            "//div[@role='button'][.//span[contains(text(),'Scrie acum')]]",

            "//div[@data-pagelet='GroupInlineComposer']"
            "//div[@role='button'][.//span[contains(text(),'Scrie o postare')]]",

            "//div[@data-pagelet='GroupInlineComposer']"
            "//div[@role='button'][.//span[contains(text(),'Creează o postare')]]",

            # engleză
            "//div[@data-pagelet='GroupInlineComposer']"
            "//div[@role='button'][.//span[contains(text(),'Create post')]]",

            "//div[@data-pagelet='GroupInlineComposer']"
            "//div[@role='button'][.//span[contains(text(),\"What's on your mind\")]]",

            # fallback generic: primul button din GroupInlineComposer care are un span
            "(//div[@data-pagelet='GroupInlineComposer']//div[@role='button'][.//span])[1]",
        ]

        clicked = try_click_xpaths(group_inline_xpaths, log_prefix="GroupInlineComposer")

        # --- 2. Dacă nu găsim în GroupInlineComposer, folosim pattern-urile generice RO/EN ---

        if not clicked:
            generic_composer_xpaths = [
                # română
                "//div[@role='button'][.//span[contains(text(),'Scrie ceva')]]",
                "//div[@role='button'][.//span[contains(text(),'Scrie acum')]]",
                "//div[@role='button'][.//span[contains(text(),'Scrie o postare')]]",
                "//div[@role='button'][.//span[contains(text(),'Creează o postare')]]",

                # engleză
                "//div[@role='button'][.//span[contains(text(),'Create post')]]",
                "//div[@role='button'][.//span[contains(text(),\"What's on your mind\")]]",
                "//div[@role='button'][.//span[contains(text(),'Write something')]]",

                # aria-label (în cazul în care textul e ascuns în aria-label)
                "//div[@role='button' and @aria-label and "
                " (contains(@aria-label,'postare') or contains(@aria-label,'Post'))]",
            ]
            clicked = try_click_xpaths(generic_composer_xpaths, log_prefix="composer")

        # --- 3. Fallback: click direct în primul textbox dacă nu găsim niciun buton ---

        if not clicked:
            try:
                textbox_fallback = WebDriverWait(driver, 15).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "(//div[@role='textbox'])[1]")
                    )
                )
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});",
                    textbox_fallback,
                )
                textbox_fallback.click()
                print("[DEBUG] Am dat click direct în primul textbox (fallback).")
            except Exception as e:
                print(
                    "[WARN] Nu am putut găsi nici butonul de creare postare, "
                    "nici textbox-ul:", e
                )
                return

        # --- 4. Găsește textbox-ul de postare (NU cel de comentarii) și scrie textul ---

        try:
            textbox = None

            textbox_xpaths = [
                # 1) Preferăm textbox-ul din dialogul de postare (overlay)
                "//div[@role='dialog']//div[@role='textbox' and "
                "not(contains(@aria-label,'comentariu')) and "
                "not(contains(@aria-label,'comment'))]",

                # 2) Apoi textbox în interiorul GroupInlineComposer (inline)
                "//div[@data-pagelet='GroupInlineComposer']"
                "//div[@role='textbox' and "
                "not(contains(@aria-label,'comentariu')) and "
                "not(contains(@aria-label,'comment'))]",

                # 3) Fallback: primul textbox fără 'comentariu/comment' în aria-label
                "(//div[@role='textbox' and "
                "not(contains(@aria-label,'comentariu')) and "
                "not(contains(@aria-label,'comment'))])[1]",
            ]

            for xp in textbox_xpaths:
                try:
                    tb = WebDriverWait(driver, 20).until(
                        EC.presence_of_element_located((By.XPATH, xp))
                    )
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", tb
                    )
                    WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((By.XPATH, xp))
                    )
                    try:
                        tb.click()
                    except Exception:
                        # fallback JS click dacă Selenium clasic e interceptat
                        driver.execute_script("arguments[0].click();", tb)
                    textbox = tb
                    print(f"[DEBUG] Am găsit textbox-ul de postare cu XPATH: {xp}")
                    break
                except Exception:
                    continue

            if textbox is None:
                print(
                    "[WARN] Nu am găsit textbox-ul de postare (probabil a rămas doar cel de comentarii)."
                )
                return

            if text:
                # încercăm varianta "user real": CTRL+A, DELETE, CTRL+V (din clipboard)
                try:
                    # ne asigurăm că textbox-ul are focus
                    try:
                        textbox.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", textbox)

                    time.sleep(0.2)  # mică pauză să prindă focusul

                    textbox.send_keys(Keys.CONTROL, "a")
                    textbox.send_keys(Keys.DELETE)
                    textbox.send_keys(Keys.CONTROL, "v")
                    print("[DEBUG] Am introdus textul în postare prin clipboard (CTRL+V).")
                except Exception as e:
                    print(
                        "[WARN] Paste prin clipboard eșuat, încerc inserare prin JS:", e
                    )
                    # fallback: varianta JS, în caz că CTRL+V e blocat din vreun motiv
                    set_text_via_js(driver, textbox, text)
            else:
                print("[DEBUG] Textul de postare este gol – nu introduc nimic.")
        except Exception as e:
            print("[WARN] Nu pot scrie textul postării:", e)

        # --- 5. Încarcă imaginile (dacă există) ---

        for img_path in images or []:
            abs_path = os.path.abspath(img_path)
            try:
                # input <input type="file" accept="image/...">
                file_inputs = driver.find_elements(
                    By.XPATH,
                    "//input[@type='file' and contains(@accept, 'image')]",
                )
                file_input = file_inputs[0] if file_inputs else None

                if file_input is None:
                    # încercăm să apăsăm pe Foto/Photo ca să apară input-ul
                    try:
                        photo_btn = driver.find_element(
                            By.XPATH,
                            "//div[@role='button'][.//span[contains(text(),'Foto')] "
                            " or .//span[contains(text(),'Photo')]]"
                        )
                        photo_btn.click()
                        time.sleep(1)
                        file_inputs = driver.find_elements(
                            By.XPATH,
                            "//input[@type='file' and contains(@accept, 'image')]",
                        )
                        file_input = file_inputs[0] if file_inputs else None
                    except Exception:
                        file_input = None

                if file_input is None:
                    print("[WARN] Nu am găsit input-ul de fișier pentru imagini.")
                    break

                file_input.send_keys(abs_path)
                print(f"[DEBUG] Am atașat imaginea: {abs_path}")
                time.sleep(1.5)
            except Exception as e:
                print("[WARN] Nu pot atașa imaginea:", abs_path, e)
                break

        # --- 6. Apasă butonul de „Postare” ---

        try:
            post_btn = WebDriverWait(driver, 30).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//div[@aria-label='Postează' or "
                    "      @aria-label='Post' or "
                    "      @aria-label='Trimite' or "
                    "      @aria-label='Publică']"
                ))
            )
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", post_btn
            )
            post_btn.click()
            print("[DEBUG] Am apăsat butonul de postare.")
            time.sleep(3)
        except Exception as e:
            print("[WARN] Nu am găsit butonul de Postare:", e)

    except Exception as e:
        print("[ERROR] Eroare în open_group_and_post pentru", group_url, ":", e)


def run_posting(
    groups, text: str, images, delay: int, simulate: bool = False, stop_event=None
):
    """
    Rulează efectiv postarea în toate grupurile, cu delay între ele.
    Poate fi întreruptă prin stop_event (Event) – se oprește între grupuri
    și nu mai pornește noi postări după ce stop_event este setat.
    """
    driver = None
    try:
        driver = create_driver()
        wait_for_facebook_home(driver, timeout=60)

        for idx, group in enumerate(groups, start=1):
            if stop_event is not None and stop_event.is_set():
                print("[RUN] Stop requested – opresc înainte de următorul grup.")
                break

            group = group.strip()
            if not group:
                continue
            print(f"[RUN] ({idx}/{len(groups)}) {group}")
            open_group_and_post(driver, group, text, images, simulate=simulate)

            if idx < len(groups):
                # așteptăm delay-ul, dar ieșim mai rapid dacă se cere stop
                total = int(delay)
                for _ in range(total):
                    if stop_event is not None and stop_event.is_set():
                        print("[RUN] Stop requested în timpul delay-ului.")
                        break
                    time.sleep(1)
                if stop_event is not None and stop_event.is_set():
                    break
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


# ================== SCHEDULER ==================

def parse_time_str(s: str):
    s = (s or "").strip()
    if not s:
        return None
    try:
        hh, mm = s.split(":")
        return dtime(hour=int(hh), minute=int(mm))
    except Exception:
        return None


def next_run_time_for(config: dict, which: str):
    enabled = config.get(f"schedule_enabled_{which}", False)
    if not enabled:
        return None
    t = parse_time_str(config.get(f"schedule_time_{which}"))
    if not t:
        return None

    now = datetime.now()
    candidate = datetime.combine(now.date(), t)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate

def should_run_daily_slot(config: dict, which: str, now: datetime) -> bool:
    """
    Decide dacă trebuie să rulăm acum slotul "which" (morning / evening).
    Condiții:
      - slotul e activat (schedule_enabled_morning/evening = True)
      - ora configurată a trecut pentru ziua de azi
      - nu am mai rulat deja azi pentru acest slot
    """
    enabled = config.get(f"schedule_enabled_{which}", False)
    if not enabled:
        return False

    t = parse_time_str(config.get(f"schedule_time_{which}"))
    if not t:
        return False

    # ora programată pentru astăzi
    today = now.date()
    scheduled_dt = datetime.combine(today, t)

    # dacă încă nu am ajuns la ora programată, nu rulăm
    if now < scheduled_dt:
        return False

    # verificăm dacă am mai rulat deja azi acest slot
    last_key = f"last_run_{which}"
    last_val = config.get(last_key)
    last_date = None
    if last_val:
        try:
            last_date = datetime.strptime(last_val, "%Y-%m-%d").date()
        except Exception:
            last_date = None

    # dacă am rulat deja azi, nu mai rulăm încă o dată
    if last_date == today:
        return False

    # marcăm că am rulat azi pentru acest slot
    config[last_key] = today.strftime("%Y-%m-%d")
    save_config(config)
    return True

def compute_next_schedule_run(config: dict):
    times = []
    for w in ("morning", "evening"):
        nt = next_run_time_for(config, w)
        if nt:
            times.append(nt)
    if not times:
        return None
    return min(times)


class SchedulerThread(threading.Thread):
    """
    Thread care verifică periodic dacă e momentul să ruleze postarea programată.
    Gestionează atât:
      - rundele fixe (dimineață/seară)
      - cât și rundele repetitive (din X în X minute)
    """

    def __init__(self, app: "FacepostApp"):
        super().__init__(daemon=True)
        self.app = app
        self._stop_flag = threading.Event()
        self.last_interval_run: datetime | None = None

    def stop(self):
        self._stop_flag.set()

    def run(self):
        while not self._stop_flag.is_set():
            try:
                now = datetime.now()
                cfg = CONFIG

                # 1) Programare zilnică dimineață/seară – doar dacă este activă
                if cfg.get("daily_schedule_active") and not self.app.is_running:
                    run_morning = should_run_daily_slot(cfg, "morning", now)
                    run_evening = should_run_daily_slot(cfg, "evening", now)

                    if run_morning or run_evening:
                        print("[SCHEDULER] Rulez rundă programată (dimineață/seară).")
                        self.app.run_now(simulate=False, from_scheduler=True)
                        # așteptăm puțin ca să nu dublăm runda în același interval
                        time.sleep(60)
                        continue

                # 2) Programare repetitivă (din X în X minute) – doar dacă este activă și configurată
                if cfg.get("interval_schedule_active") and cfg.get("interval_enabled"):
                    try:
                        minutes = int(cfg.get("interval_minutes") or 0)
                    except ValueError:
                        minutes = 0

                    # minim 5 minute ca protecție
                    if minutes < 5:
                        minutes = 5

                    should_run = False
                    if self.last_interval_run is None:
                        should_run = True
                    else:
                        delta_sec = (now - self.last_interval_run).total_seconds()
                        if delta_sec >= minutes * 60:
                            should_run = True

                    if should_run and not self.app.is_running:
                        print("[SCHEDULER] Rulez rundă repetitivă.")
                        self.app.run_now(simulate=False, from_scheduler=True)
                        self.last_interval_run = datetime.now()

                time.sleep(5)
            except Exception as e:
                print("[SCHEDULER ERROR]", e)
                time.sleep(10)


# ================== TKINTER UI ==================

class FacepostApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.is_running = False
        self.images = set(CONFIG.get("images", []))
        self.scheduler_thread = None
        self.stop_event = None  # pentru a opri rularea curentă
        # starea de update
        self.update_info = None        # dict cu info despre update (dacă există)
        self.update_pending = False    # dacă trebuie făcut update după runda curentă

        self.email_var = tk.StringVar(value=CONFIG.get("email", ""))
        self.delay_var = tk.StringVar(value=str(CONFIG.get("delay_seconds", 120)))
        self.simulate_var = tk.BooleanVar(value=CONFIG.get("simulate", False))

        self.schedule_enabled_morning_var = tk.BooleanVar(
            value=CONFIG.get("schedule_enabled_morning", False)
        )
        self.schedule_time_morning_var = tk.StringVar(
            value=CONFIG.get("schedule_time_morning", "08:00")
        )
        self.schedule_enabled_evening_var = tk.BooleanVar(
            value=CONFIG.get("schedule_enabled_evening", False)
        )
        self.schedule_time_evening_var = tk.StringVar(
            value=CONFIG.get("schedule_time_evening", "20:00")
        )

        self.interval_enabled_var = tk.BooleanVar(
            value=CONFIG.get("interval_enabled", False)
        )
        self.interval_minutes_var = tk.StringVar(
            value=str(CONFIG.get("interval_minutes", 60))
        )

        # starea de "programare activă" pentru cele două tipuri de scheduler
        self.daily_schedule_active_var = tk.BooleanVar(
            value=CONFIG.get("daily_schedule_active", False)
        )
        self.interval_schedule_active_var = tk.BooleanVar(
            value=CONFIG.get("interval_schedule_active", False)
        )

        self._build_ui()
        self._load_initial_texts()
        self._update_daily_button_text()
        self._update_interval_button_text()
        self._update_run_button_text()
        self._start_scheduler_if_needed()

        # pornim thread-ul care verifică periodic update-urile
        threading.Thread(
            target=self._update_watcher, daemon=True
        ).start()

    # ---------- UI building ----------

    def _build_ui(self):
        root = self.root
        root.geometry("900x720")

        # Container cu Canvas + Scrollbar pentru conținut scrollabil
        container = tk.Frame(root)
        container.pack(fill="both", expand=True, padx=10, pady=10)

        canvas = tk.Canvas(container)
        canvas.pack(side="left", fill="both", expand=True)

        scrollbar = tk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")

        canvas.configure(yscrollcommand=scrollbar.set)

        main_frame = tk.Frame(canvas)
        frame_id = canvas.create_window((0, 0), window=main_frame, anchor="nw")

        # scrollregion + lățimea frame-ului
        def _on_frame_config(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_config(event):
            # facem main_frame să aibă lățimea canvas-ului → nu mai apare banda gri
            canvas.itemconfig(frame_id, width=event.width)

        main_frame.bind("<Configure>", _on_frame_config)
        canvas.bind("<Configure>", _on_canvas_config)

        # scroll cu rotița mouse-ului (Windows)
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # Config licență (sus) + buton Facebook login
        config_frame = tk.LabelFrame(main_frame, text="Config licență")
        config_frame.pack(fill="x", pady=5)

        # stânga: email + check + bind + status
        left_cfg = tk.Frame(config_frame)
        left_cfg.pack(side="left", fill="x", expand=True)

        # dreapta: Salvează config + butoane Facebook, grupate compact
        right_cfg = tk.Frame(config_frame)
        right_cfg.pack(side="right", anchor="ne")

        # ---- LEFT (grid) ----
        tk.Label(left_cfg, text="Email licență:").grid(row=0, column=0, sticky="w")

        email_entry = tk.Entry(left_cfg, textvariable=self.email_var, width=40)
        email_entry.grid(row=0, column=1, sticky="we", padx=(0, 5))

        left_cfg.grid_columnconfigure(1, weight=1)

        tk.Button(
            left_cfg, text="Verifică licență", command=self.check_license_clicked
        ).grid(row=1, column=0, pady=5, sticky="w")

        tk.Button(
            left_cfg, text="Activează licență", command=self.bind_license_clicked
        ).grid(row=1, column=1, pady=5, sticky="w")

        self.license_status_var = tk.StringVar(value="Status licență necunoscut.")
        tk.Label(
            left_cfg,
            textvariable=self.license_status_var,
            fg="blue",
            anchor="w",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 3))

        # ---- RIGHT (vertical pack) ----
        tk.Button(
            right_cfg, text="Salvează config", command=self.save_config_clicked
        ).pack(fill="x", pady=(0, 2))

        tk.Button(
            right_cfg,
            text="Conectează-te la Facebook",
            command=lambda: configure_facebook_login(self.root, mode="login"),
        ).pack(fill="x", pady=(0, 2))

        tk.Button(
            right_cfg,
            text="Schimbă profilul de Facebook",
            command=lambda: configure_facebook_login(self.root, mode="switch"),
        ).pack(fill="x")
        
        # Conținut postare
        post_frame = tk.LabelFrame(main_frame, text="Conținut postare")
        post_frame.pack(fill="both", expand=True, pady=5)

        tk.Label(post_frame, text="Text postare:").pack(anchor="w")
        self.post_text = tk.Text(post_frame, height=8)
        self.post_text.pack(fill="x", pady=3)

        tk.Label(post_frame, text="Linkuri grupuri (unul pe linie):").pack(anchor="w")
        self.group_text = tk.Text(post_frame, height=8)
        self.group_text.pack(fill="x", pady=3)

        images_frame = tk.Frame(post_frame)
        images_frame.pack(fill="x", pady=5)

        tk.Button(
            images_frame, text="Adaugă imagini", command=self.add_images_clicked
        ).pack(side="left")

        self.images_listbox = tk.Listbox(images_frame, height=4)
        self.images_listbox.pack(side="left", fill="x", expand=True, padx=5)

        # Frame pentru butoanele de ștergere (vertical: sus "Șterge imaginea", jos "Șterge tot")
        images_buttons_frame = tk.Frame(images_frame)
        images_buttons_frame.pack(side="left", padx=5)

        tk.Button(
            images_buttons_frame,
            text="Șterge imaginea",
            command=self.remove_selected_image,
        ).pack(fill="x", pady=(0, 3))

        tk.Button(
            images_buttons_frame,
            text="Șterge tot",
            command=self.clear_all_images,
        ).pack(fill="x")

        delay_frame = tk.Frame(post_frame)
        delay_frame.pack(fill="x", pady=5)

        tk.Label(delay_frame, text="Delay între grupuri (secunde):").pack(side="left")
        tk.Entry(delay_frame, textvariable=self.delay_var, width=6).pack(
            side="left", padx=5
        )
        tk.Checkbutton(
            delay_frame,
            text="Simulare (nu posta efectiv)",
            variable=self.simulate_var,
        ).pack(side="left", padx=10)

        # Programare automată zilnică
        schedule_frame = tk.LabelFrame(main_frame, text="Programare automată zilnică")
        schedule_frame.pack(fill="x", pady=5)

        tk.Checkbutton(
            schedule_frame,
            text="Rulează dimineața la ora:",
            variable=self.schedule_enabled_morning_var,
            command=self.schedule_changed,
        ).grid(row=0, column=0, sticky="w")
        tk.Entry(
            schedule_frame, textvariable=self.schedule_time_morning_var, width=6
        ).grid(row=0, column=1, sticky="w")

        tk.Checkbutton(
            schedule_frame,
            text="Rulează seara la ora:",
            variable=self.schedule_enabled_evening_var,
            command=self.schedule_changed,
        ).grid(row=1, column=0, sticky="w")
        tk.Entry(
            schedule_frame, textvariable=self.schedule_time_evening_var, width=6
        ).grid(row=1, column=1, sticky="w")

        # buton start/stop pentru programarea zilnică
        self.daily_button = tk.Button(
            schedule_frame,
            text="Pornește programarea zilnică",  # textul va fi actualizat din _update_daily_button_text
            command=self.toggle_daily_schedule,
            width=25,
        )
        self.daily_button.grid(row=2, column=0, columnspan=4, pady=(5, 0), sticky="w")

        # Programare repetitivă
        interval_frame = tk.LabelFrame(main_frame, text="Programare repetitivă")
        interval_frame.pack(fill="x", pady=5)

        tk.Checkbutton(
            interval_frame,
            text="Rulează la fiecare:",
            variable=self.interval_enabled_var,
            command=self.schedule_changed,
        ).grid(row=0, column=0, sticky="w")

        tk.Entry(
            interval_frame,
            textvariable=self.interval_minutes_var,
            width=6,
        ).grid(row=0, column=1, sticky="w")

        tk.Label(interval_frame, text="minute").grid(row=0, column=2, sticky="w")

        self.interval_button = tk.Button(
            interval_frame,
            text="Pornește repetarea",  # textul va fi actualizat din _update_interval_button_text
            command=self.toggle_interval,
            width=18,
        )
        self.interval_button.grid(row=0, column=3, padx=8, sticky="w")

        tk.Label(
            interval_frame,
            text="(rulări repetate până apeși Oprește repetarea sau închizi aplicația)",
            fg="gray",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(3, 0))

        # Bottom bar
        bottom_frame = tk.Frame(main_frame)
        bottom_frame.pack(fill="x", pady=10)

        self.status_var = tk.StringVar(value="Gata de lucru.")
        tk.Label(bottom_frame, textvariable=self.status_var, fg="green").pack(
            side="left"
        )

        self.run_btn = tk.Button(
            bottom_frame, text="Postează acum", command=self.run_now_clicked
        )
        self.run_btn.pack(side="right")

        self._update_daily_button_text()
        self._update_interval_button_text()
        self._update_run_button_text()

        # ---------- helperi UI ----------

    def _load_initial_texts(self):
        self.post_text.delete("1.0", "end")
        self.post_text.insert("1.0", CONFIG.get("post_text", ""))

        self.group_text.delete("1.0", "end")
        self.group_text.insert("1.0", CONFIG.get("groups_text", ""))

        self.images_listbox.delete(0, "end")
        for img in self.images:
            self.images_listbox.insert("end", img)

    def _start_scheduler_if_needed(self):
        if self.scheduler_thread is not None:
            return
        if CONFIG.get("daily_schedule_active") or CONFIG.get(
            "interval_schedule_active"
        ):
            self.scheduler_thread = SchedulerThread(self)
            self.scheduler_thread.start()

    def _update_daily_button_text(self):
        if getattr(self, "daily_button", None) is None:
            return
        if self.daily_schedule_active_var.get():
            self.daily_button.config(text="Oprește programarea zilnică")
        else:
            self.daily_button.config(text="Pornește programarea zilnică")

    def _update_interval_button_text(self):
        if getattr(self, "interval_button", None) is None:
            return
        if self.interval_schedule_active_var.get():
            self.interval_button.config(text="Oprește repetarea")
        else:
            self.interval_button.config(text="Pornește repetarea")

    def _update_run_button_text(self):
        if self.is_running:
            self.run_btn.config(text="Oprește postările")
        else:
            self.run_btn.config(text="Postează acum")

    def _update_scheduler_state(self):
        # sincronizăm flag-urile în CONFIG și pornim/oprim thread-ul de scheduler
        CONFIG["daily_schedule_active"] = bool(self.daily_schedule_active_var.get())
        CONFIG["interval_schedule_active"] = bool(
            self.interval_schedule_active_var.get()
        )
        save_config(CONFIG)

        active_daily = CONFIG["daily_schedule_active"]
        active_interval = CONFIG["interval_schedule_active"]

        if active_daily or active_interval:
            self._start_scheduler_if_needed()
        else:
            # Oprim schedulerul dacă nu mai e nimic activ
            if self.scheduler_thread is not None:
                try:
                    self.scheduler_thread.stop()
                except Exception:
                    pass
                self.scheduler_thread = None

    def toggle_daily_schedule(self):
        """
        Pornește / oprește programarea zilnică.
        IMPORTANT: sincronizăm mereu UI -> CONFIG înainte de a actualiza scheduler-ul,
        ca să fie folosite orele și bifele pe care le vezi pe ecran.
        """
        current = self.daily_schedule_active_var.get()
        # toggling: dacă era activă, oprim; dacă era oprită, pornim
        self.daily_schedule_active_var.set(not current)

        # sincronizăm orele și bifele din UI în CONFIG (morning/evening + interval)
        self.schedule_changed()

        # actualizăm textul butonului și starea scheduler-ului
        self._update_daily_button_text()
        self._update_scheduler_state()

    def toggle_interval(self):
        """
        Pornește / oprește programarea repetitivă (din X în X minute).
        La pornire:
          - validăm intervalul
          - sincronizăm UI -> CONFIG (prin schedule_changed)
          - marcăm interval_schedule_active = True și pornim schedulerul
          - rulăm prima rundă imediat
        La oprire:
          - doar dezactivăm flag-ul și actualizăm schedulerul.
        """
        if self.interval_schedule_active_var.get():
            # oprim repetarea
            self.interval_schedule_active_var.set(False)

            # sincronizăm UI -> CONFIG (bife + minute)
            self.schedule_changed()

            self._update_interval_button_text()
            self._update_scheduler_state()
        else:
            # pornim repetarea – verificăm întâi că intervalul este valid
            try:
                interval_minutes = int(self.interval_minutes_var.get() or "0")
            except ValueError:
                interval_minutes = 0

            if not self.interval_enabled_var.get():
                messagebox.showerror(
                    APP_NAME,
                    "Bifează opțiunea 'Rulează la fiecare' înainte de a porni programarea repetitivă.",
                    parent=self.root,
                )
                return

            if interval_minutes <= 0:
                messagebox.showerror(
                    APP_NAME,
                    "Te rog introdu un număr valid de minute pentru programarea repetitivă.",
                    parent=self.root,
                )
                return

            # activăm programarea repetitivă în UI
            self.interval_schedule_active_var.set(True)

            # sincronizăm UI -> CONFIG (inclusiv interval_minutes nou)
            self.schedule_changed()

            # actualizăm butonul și starea scheduler-ului (va porni thread-ul dacă e nevoie)
            self._update_interval_button_text()
            self._update_scheduler_state()

            # prima rundă rulează imediat
            self.run_now(simulate=None, from_scheduler=False)
            # scheduler-ul va continua de la acest moment
            if self.scheduler_thread is not None:
                self.scheduler_thread.last_interval_run = datetime.now()

    # ---------- acțiuni config & schedule ----------

    def save_config_clicked(self):
        CONFIG["email"] = self.email_var.get().strip()
        CONFIG["post_text"] = self.post_text.get("1.0", "end").strip()
        CONFIG["groups_text"] = self.group_text.get("1.0", "end").strip()
        CONFIG["images"] = list(self.images)
        try:
            CONFIG["delay_seconds"] = int(self.delay_var.get() or "120")
        except ValueError:
            CONFIG["delay_seconds"] = 120
        CONFIG["simulate"] = bool(self.simulate_var.get())

        CONFIG["schedule_enabled_morning"] = bool(
            self.schedule_enabled_morning_var.get()
        )
        CONFIG["schedule_time_morning"] = self.schedule_time_morning_var.get().strip()
        CONFIG["schedule_enabled_evening"] = bool(
            self.schedule_enabled_evening_var.get()
        )
        CONFIG["schedule_time_evening"] = self.schedule_time_evening_var.get().strip()

        try:
            interval_minutes = int(self.interval_minutes_var.get() or "0")
        except ValueError:
            interval_minutes = 0
        CONFIG["interval_enabled"] = bool(self.interval_enabled_var.get())
        CONFIG["interval_minutes"] = interval_minutes

        save_config(CONFIG)
        messagebox.showinfo(APP_NAME, "Config salvată.", parent=self.root)

    def schedule_changed(self):
        """
        Sincronizează din UI în CONFIG:
          - bife + ore pentru dimineață / seară
          - bifa + minute pentru programarea repetitivă
        și salvează imediat în fișierul de config.
        """
        # dimineața
        CONFIG["schedule_enabled_morning"] = bool(
            self.schedule_enabled_morning_var.get()
        )
        CONFIG["schedule_time_morning"] = self.schedule_time_morning_var.get().strip()

        # seara
        CONFIG["schedule_enabled_evening"] = bool(
            self.schedule_enabled_evening_var.get()
        )
        CONFIG["schedule_time_evening"] = self.schedule_time_evening_var.get().strip()

        # interval repetitiv
        try:
            interval_minutes = int(self.interval_minutes_var.get() or "0")
        except ValueError:
            interval_minutes = 0

        CONFIG["interval_enabled"] = bool(self.interval_enabled_var.get())
        CONFIG["interval_minutes"] = interval_minutes

        save_config(CONFIG)

        # ---------- LOGICA DE UPDATE AUTOMAT ----------

    def _parse_version(self, v: str) -> tuple:
        """Transformă '1.2.3' într-un tuplu (1,2,3) pentru comparații sigure."""
        try:
            return tuple(int(x) for x in v.strip().split("."))
        except Exception:
            return (0, 0, 0)

    def _check_for_update_once(self) -> dict | None:
        """
        Face un singur request la backend și întoarce info despre update dacă există.
        return:
          None   -> nu există update sau eroare
          dict   -> {"version": ..., "notes": ..., "download_url": ...}
        """
        server_base = CONFIG.get("server_url", API_URL).rstrip("/")

        try:
            r = requests.get(f"{server_base}/client-version", timeout=10)
            data = r.json()
        except Exception as e:
            print("[UPDATE] Eroare la /client-version:", e)
            return None

        server_ver = str(data.get("version") or "").strip()
        if not server_ver:
            return None

        if self._parse_version(server_ver) <= self._parse_version(CLIENT_VERSION):
            # suntem la zi
            return None

        notes = data.get("notes", "")

        # luăm URL-ul de download
        try:
            r2 = requests.get(f"{server_base}/client-download", timeout=10)
            d2 = r2.json()
            download_url = d2.get("url")
        except Exception as e:
            print("[UPDATE] Eroare la /client-download:", e)
            return None

        if not download_url:
            return None

        print(f"[UPDATE] Disponibilă versiunea {server_ver}")
        return {
            "version": server_ver,
            "notes": notes,
            "download_url": download_url,
        }

    def _update_watcher(self):
        """
        Verifică o dată la 24h dacă există versiune nouă.
        Dacă găsește update și nu rulează nimic, declanșează direct self-update.
        Dacă rulează ceva, setează update_pending și va fi tratat la finalul task-ului.
        """
        while True:
            try:
                now = datetime.now()
                last_check_iso = CONFIG.get("last_update_check")
                if last_check_iso:
                    try:
                        last_check = datetime.fromisoformat(last_check_iso)
                    except Exception:
                        last_check = now - timedelta(days=2)
                else:
                    last_check = now - timedelta(days=2)

                # dacă au trecut deja 24h de la ultimul check, verificăm
                if (now - last_check) >= timedelta(hours=24):
                    info = self._check_for_update_once()
                    CONFIG["last_update_check"] = now.isoformat()
                    save_config(CONFIG)

                    if info is not None:
                        # avem versiune nouă
                        self.update_info = info
                        if not self.is_running:
                            # nu rulează nimic -> putem lansa direct updater-ul
                            self.root.after(0, self._trigger_auto_update)
                        else:
                            # există task în curs, îl facem după ce se termină
                            print("[UPDATE] Update găsit, îl fac după terminarea rundei.")
                            self.update_pending = True

                # nu vrem să spamăm API-ul – verificăm maxim o dată pe oră
                time.sleep(3600)
            except Exception as e:
                print("[UPDATE] Eroare în update_watcher:", e)
                time.sleep(3600)

    def _trigger_auto_update(self):
        """Cheamă start_self_update dacă avem info validă despre update."""
        if not self.update_info:
            return
        try:
            self._start_self_update()
        except Exception as e:
            print("[UPDATE] Eroare la pornirea self-update:", e)

    def _start_self_update(self):
        """
        Creează o copie temporară a exe-ului curent și o pornește în modul '--self-update'.
        Instanța curentă de Facepost se va închide, iar copia va înlocui exe-ul și va porni noua versiune.
        """
        info = self.update_info
        if info is None:
            return

        download_url = info["download_url"]
        target_ver = info["version"]

        # Dacă rulăm din surse (.py), nu încercăm self-update – doar deschidem linkul
        if not getattr(sys, "frozen", False):
            print("[UPDATE] Rulezi din surse (nu exe). Deschid linkul de download.")
            webbrowser.open(download_url)
            return

        exe_path = Path(sys.executable).resolve()

        # copie temporară care va rula ca updater
        tmp_dir = Path(tempfile.gettempdir())
        tmp_exe = tmp_dir / "facepost_self_updater.exe"

        try:
            shutil.copy2(exe_path, tmp_exe)
        except Exception as e:
            print("[UPDATE] Nu pot copia exe-ul curent în TEMP:", e)
            # fallback: măcar deschidem linkul
            webbrowser.open(download_url)
            return

        args = [
            str(tmp_exe),
            "--self-update",
            "--target",
            str(exe_path),
            "--url",
            download_url,
            "--version",
            target_ver,
        ]
        print("[UPDATE] Pornez self-updater-ul:", args)

        try:
            subprocess.Popen(args, close_fds=True)
        except Exception as e:
            print("[UPDATE] Eroare la lansarea self-updater-ului:", e)
            return

        # închidem UI-ul ca updater-ul să poată lucra liniștit
        self.root.after(200, self.root.destroy)

    # ---------- acțiuni licență ----------

    def check_license_clicked(self):
        email = self.email_var.get().strip().lower()
        if not email:
            messagebox.showerror(
                APP_NAME, "Te rog introdu emailul licenței.", parent=self.root
            )
            return

        CONFIG["email"] = email
        save_config(CONFIG)

        resp = check_license(email, CONFIG.get("device_id"))

        # Dacă avem o eroare HTTP / de API, tratăm în funcție de cod
        if resp.get("error"):
            http_code = resp.get("_http", 0)

            # Nicio licență pentru acest email
            if http_code == 404 and resp["error"] == "license not found":
                self.license_status_var.set(
                    "Nu există nicio licență pentru acest email. Verifică adresa de email sau contactează suportul."
                )
                return

            # Limită de dispozitive atinsă
            if http_code == 403 and resp["error"] == "device limit reached":
                self.license_status_var.set(
                    "Limita de dispozitive a fost atinsă pentru această licență. Te rugăm să eliberezi un dispozitiv existent sau să contactezi suportul."
                )
                return

            # Alte erori – afișăm generic
            self.license_status_var.set(f"Eroare: {resp['error']}")
            return

        status = resp.get("status", "unknown")
        exp = resp.get("expires_at")
        is_trial = resp.get("is_trial")
        extra = resp.get("note")

        # 1) Licență activă și device deja legat
        if status == "ok":
            msg = "Licență activă"
            if exp:
                msg += f" | expiră la: {exp}"
            if is_trial:
                msg += " | TRIAL"
            if extra:
                msg += f" | {extra}"
            self.license_status_var.set(msg)
            return

        # 2) Licență activă, device NELEGAT încă → ghidăm userul să apese Bind
        if status == "unbound":
            if exp:
                self.license_status_var.set(
                    f'Licență activă până la {exp}. Te rog apasă butonul "Bind licență" pentru a continua.'
                )
            else:
                self.license_status_var.set(
                    'Licență activă. Te rog apasă butonul "Bind licență" pentru a continua.'
                )
            return

        # 3) Licență expirată
        if status == "expired":
            self.license_status_var.set(
                "Licență expirată. Te rugăm să îți reînnoiești abonamentul pentru a continua."
            )
            return

        # 4) Licență suspendată / inactivă
        if status == "inactive":
            self.license_status_var.set(
                "Licența este suspendată. Te rugăm să contactezi suportul pentru detalii."
            )
            return

        # 5) Fallback – stilul vechi, în caz că apar alte statusuri
        msg = f"Status: {status}"
        if exp:
            msg += f" | expiră la: {exp}"
        if is_trial:
            msg += " | TRIAL"
        if extra:
            msg += f" | {extra}"
        self.license_status_var.set(msg)

    def bind_license_clicked(self):
        email = self.email_var.get().strip().lower()
        if not email:
            messagebox.showerror(
                APP_NAME, "Te rog introdu emailul licenței.", parent=self.root
            )
            return
        CONFIG["email"] = email
        save_config(CONFIG)

        resp = bind_license(email, CONFIG.get("device_id"))
        if resp.get("error"):
            messagebox.showerror(
                APP_NAME, f"Eroare la bind: {resp['error']}", parent=self.root
            )
            return

        if resp.get("_http") and resp["_http"] != 200:
            messagebox.showerror(APP_NAME, f"HTTP {resp['_http']}", parent=self.root)
            return

        self.license_status_var.set("Licență legată cu succes pe acest device.")

    # ---------- acțiuni imagini ----------

    def add_images_clicked(self):
        paths = filedialog.askopenfilenames(
            title="Alege imagini",
            filetypes=[
                ("Imagini", "*.png;*.jpg;*.jpeg;*.gif;*.bmp;*.webp"),
                ("Toate fișierele", "*.*"),
            ],
        )
        if not paths:
            return
        for p in paths:
            if p not in self.images:
                self.images.add(p)
                self.images_listbox.insert("end", p)

    def remove_selected_image(self):
        sel = list(self.images_listbox.curselection())
        if not sel:
            return
        for idx in reversed(sel):
            val = self.images_listbox.get(idx)
            self.images_listbox.delete(idx)
            if val in self.images:
                self.images.remove(val)

    def clear_all_images(self):
        """Șterge toate imaginile din listă și din setul intern."""
        if not self.images:
            return

        # Dacă vrei confirmare, păstrezi blocul următor;
        # dacă nu, poți șterge complet acest if.
        if not messagebox.askyesno(
            APP_NAME,
            "Ești sigur că vrei să ștergi toate imaginile din postare?",
            parent=self.root,
        ):
            return

        self.images.clear()
        self.images_listbox.delete(0, "end")
    
    # ---------- run logic ----------

    def run_now(self, simulate: bool | None = None, from_scheduler: bool = False):
        if self.is_running:
            # când e chemat din scheduler, doar ignorăm dacă rulează deja
            if not from_scheduler:
                messagebox.showwarning(
                    APP_NAME,
                    "Deja rulează o sesiune de postare.",
                    parent=self.root,
                )
            return

        email = self.email_var.get().strip().lower()
        if not email:
            if not from_scheduler:
                messagebox.showerror(
                    APP_NAME, "Te rog introdu emailul licenței.", parent=self.root
                )
            return

        CONFIG["email"] = email
        save_config(CONFIG)

        resp = check_license(email, CONFIG.get("device_id"))
        if resp.get("error"):
            if not from_scheduler:
                messagebox.showerror(
                    APP_NAME,
                    f"Eroare la check licență: {resp['error']}",
                    parent=self.root,
                )
            return
        if resp.get("status") not in ("ok",):
            if not from_scheduler:
                messagebox.showerror(
                    APP_NAME,
                    f"Licența nu este activă sau este expirată ({resp.get('status')}).",
                    parent=self.root,
                )
            return

        groups_raw = self.group_text.get("1.0", "end").strip()
        groups = [g for g in groups_raw.splitlines() if g.strip()]
        if not groups:
            if not from_scheduler:
                messagebox.showerror(
                    APP_NAME,
                    "Te rog introdu cel puțin un URL de grup.",
                    parent=self.root,
                )
            return

        text = self.post_text.get("1.0", "end").strip()

        # setăm textul postării în clipboard-ul sistemului
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()  # necesar ca să se propage către OS
            print(f"[DEBUG] Clipboard set cu textul postării (lungime {len(text)})")
        except Exception as e:
            print("[WARN] Nu pot seta clipboard-ul cu textul postării:", e)

        try:
            delay = int(self.delay_var.get() or "120")
        except ValueError:
            delay = 120

        if simulate is None:
            simulate = bool(self.simulate_var.get())

        # pregătim flag-ul de oprire pentru această rundă
        self.stop_event = threading.Event()

        t = threading.Thread(
            target=self._run_thread,
            args=(groups, text, list(self.images), delay, simulate, self.stop_event),
            daemon=True,
        )
        t.start()

    def run_now_clicked(self):
        # Butonul "Postează acum" funcționează ca Start/Stop pentru runda curentă
        if self.is_running:
            # cerem oprirea rundei curente
            if self.stop_event is not None:
                self.stop_event.set()
            return
        self.run_now(simulate=None, from_scheduler=False)

    def _run_thread(self, groups, text, images, delay, simulate, stop_event):
        self.is_running = True
        self._update_run_button_text()
        self.status_var.set("Rulez postările...")
        try:
            # log către server (best-effort)
            try:
                resp = log_run(groups, text, images)
                print("[LOG_RUN]", resp)
            except Exception as e:
                print("[WARN] Nu pot trimite log_run:", e)

            # rulare efectivă
            run_posting(
                groups,
                text,
                images,
                delay,
                simulate=simulate,
                stop_event=stop_event,
            )

            if stop_event is not None and stop_event.is_set():
                self.status_var.set(
                    "Postările au fost oprite la cererea utilizatorului."
                )
            else:
                if simulate:
                    self.status_var.set("Gata (simulare).")
                else:
                    self.status_var.set(
                        "Gata – postările ar trebui să fie publicate."
                    )
        finally:
            self.is_running = False
            self.stop_event = None
            self._update_run_button_text()

            # dacă există un update în așteptare, îl declanșăm acum
            if self.update_pending and self.update_info is not None:
                print("[UPDATE] Runda s-a terminat, lansez self-update.")
                self.update_pending = False
                # trebuie făcut în thread-ul principal Tk
                self.root.after(0, self._trigger_auto_update)

# ================== MAIN ==================

def run_self_updater():
    """
    Rulat din copia temporară (facepost_self_updater.exe) cu flagul --self-update.
    Scop:
      - așteaptă închiderea exe-ului original
      - descarcă noua versiune
      - înlocuiește Facepost.exe
      - pornește noua versiune
    """
    print("[SELF-UPDATE] Pornit cu argv:", sys.argv)
    argv = sys.argv[1:]
    target = None
    url = None
    version = None

    i = 0    # mic parser simplu pentru --target / --url / --version
    while i < len(argv):
        arg = argv[i]
        if arg == "--self-update":
            i += 1
        elif arg == "--target" and i + 1 < len(argv):
            target = Path(argv[i + 1])
            i += 2
        elif arg == "--url" and i + 1 < len(argv):
            url = argv[i + 1]
            i += 2
        elif arg == "--version" and i + 1 < len(argv):
            version = argv[i + 1]
            i += 2
        else:
            i += 1

    if not target or not url:
        print("[SELF-UPDATE] Lipsesc parametrii target/url. Ies.")
        if url:
            webbrowser.open(url)
        return

    # 1) așteptăm ca exe-ul țintă să fie liber (să se fi închis Facepost-ul original)
    for _ in range(60):
        try:
            with open(target, "rb+"):
                break
        except OSError:
            time.sleep(1)
    else:
        print("[SELF-UPDATE] Nu pot obține acces la fișierul țintă. Renunț.")
        return

    # 2) descărcăm noua versiune într-un fișier temporar
    try:
        tmp_dir = Path(tempfile.gettempdir())
        download_path = tmp_dir / f"facepost_update_{int(time.time())}.exe"
        print(f"[SELF-UPDATE] Descarc noua versiune în {download_path}")
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(download_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
    except Exception as e:
        print("[SELF-UPDATE] Eroare la descărcare:", e)
        # fallback: deschidem linkul în browser
        try:
            webbrowser.open(url)
        except Exception:
            pass
        return

    # 3) backup la exe-ul vechi (opțional)
    try:
        backup_path = target.with_suffix(target.suffix + ".old")
        try:
            if backup_path.exists():
                backup_path.unlink()
        except Exception:
            pass

        try:
            shutil.move(str(target), str(backup_path))
            print(f"[SELF-UPDATE] Am mutat vechiul exe la {backup_path}")
        except Exception as e:
            print("[SELF-UPDATE] Nu pot muta exe-ul vechi:", e)
    except Exception as e:
        print("[SELF-UPDATE] Eroare la backup:", e)

    # 4) mutăm noul exe pe poziția țintă
    try:
        shutil.move(str(download_path), str(target))
        print("[SELF-UPDATE] Noul exe a fost copiat peste țintă.")
    except Exception as e:
        print("[SELF-UPDATE] Nu pot muta noul exe peste țintă:", e)
        return

    # 5) pornim Facepost nou
    try:
        print("[SELF-UPDATE] Pornez noul Facepost:", target)
        subprocess.Popen([str(target)], close_fds=True)
    except Exception as e:
        print("[SELF-UPDATE] Nu pot porni noul Facepost:", e)
        return

    # nu încercăm să ștergem self_updater-ul din TEMP (Windows nu te lasă să-ți ștergi propriul exe în execuție)
    print("[SELF-UPDATE] Gata, ies.")

def main():
    root = tk.Tk()
    app = FacepostApp(root)
    root.mainloop()


if __name__ == "__main__":
    # dacă a fost pornit cu --self-update, rulăm logica de updater și NU deschidem UI-ul
    if "--self-update" in sys.argv:
        run_self_updater()
    else:
        main()
























