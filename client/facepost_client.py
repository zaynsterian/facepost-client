import os
import sys
import json
import time
import threading
import hashlib
from pathlib import Path
from datetime import datetime, timedelta, time as dtime, timezone
import platform

import requests
import tkinter as tk
from tkinter import messagebox, filedialog

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException

# ================== CONFIG GLOBALĂ ==================

APP_NAME = "Facepost"
API_URL = "https://facepost.onrender.com"
CONFIG_FILE = Path.home() / ".facepost_config.json"
CHROMEDRIVER_NAME = "chromedriver.exe"  # în același folder cu EXE-ul

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

def configure_facebook_login(parent: tk.Tk | None = None):
    """
    Deschide Chrome cu profilul Facepost și lasă userul să se logheze manual pe Facebook.
    Se folosește o singură dată, apoi rămâne logat în profil.
    """
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

    driver.get("https://www.facebook.com/")
    messagebox.showinfo(
        APP_NAME,
        "S-a deschis un Chrome cu profilul Facepost.\n"
        "Loghează-te în Facebook, apoi închide fereastra.\n\n"
        "După aceea, Facepost va folosi acest profil pentru postări.",
        parent=parent,
    )


def wait_for_facebook_home(driver: webdriver.Chrome, timeout: int = 60):
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )


# ================== LOGICA DE POSTARE ==================

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
                # mai întâi, textbox în interiorul GroupInlineComposer, exclus comentarii
                "//div[@data-pagelet='GroupInlineComposer']"
                "//div[@role='textbox' and "
                "not(contains(@aria-label,'comentariu')) and "
                "not(contains(@aria-label,'comment'))]",

                # fallback: primul textbox fără 'comentariu/comment' în aria-label
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
                textbox.send_keys(text)
            print("[DEBUG] Am introdus textul în postare.")
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


def run_posting(groups, text: str, images, delay: int, simulate: bool = False):
    """
    Rulează efectiv postarea în toate grupurile, cu delay între ele.
    """
    driver = None
    try:
        driver = create_driver()
        wait_for_facebook_home(driver, timeout=60)

        for idx, group in enumerate(groups, start=1):
            group = group.strip()
            if not group:
                continue
            print(f"[RUN] ({idx}/{len(groups)}) {group}")
            open_group_and_post(driver, group, text, images, simulate=simulate)
            if idx < len(groups):
                time.sleep(delay)
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

                # 1) schedule clasic dimineață/seară
                next_fixed = compute_next_schedule_run(cfg)
                if next_fixed and now >= next_fixed and not self.app.is_running:
                    print("[SCHEDULER] Rulez rundă programată (dimineață/seară).")
                    self.app.run_now(simulate=False, from_scheduler=True)
                    time.sleep(60)
                    continue

                # 2) schedule repetitiv (din X în X minute)
                if cfg.get("interval_enabled"):
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

        self._build_ui()
        self._load_initial_texts()
        self._start_scheduler_if_needed()

    # ---------- UI building ----------

    def _build_ui(self):
        root = self.root
        root.geometry("900x720")

        main_frame = tk.Frame(root)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # Config licență (sus) + buton Facebook login
        config_frame = tk.LabelFrame(main_frame, text="Config licență")
        config_frame.pack(fill="x", pady=5)

        tk.Label(config_frame, text="Email licență:").grid(row=0, column=0, sticky="w")
        tk.Entry(config_frame, textvariable=self.email_var, width=40).grid(
            row=0, column=1, sticky="w"
        )

        tk.Button(
            config_frame, text="Salvează config", command=self.save_config_clicked
        ).grid(row=0, column=2, padx=5)

        tk.Button(
            config_frame,
            text="Configurează login Facebook",
            command=lambda: configure_facebook_login(self.root),
        ).grid(row=0, column=3, padx=5)

        self.license_status_var = tk.StringVar(value="Status licență necunoscut.")
        tk.Button(
            config_frame, text="Check licență", command=self.check_license_clicked
        ).grid(row=1, column=0, pady=5)
        tk.Button(
            config_frame, text="Bind licență", command=self.bind_license_clicked
        ).grid(row=1, column=1, pady=5)
        tk.Label(config_frame, textvariable=self.license_status_var, fg="blue").grid(
            row=1, column=2, columnspan=2, sticky="w"
        )

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

        tk.Button(images_frame, text="Adaugă imagini", command=self.add_images_clicked).pack(
            side="left"
        )
        self.images_listbox = tk.Listbox(images_frame, height=4)
        self.images_listbox.pack(side="left", fill="x", expand=True, padx=5)
        tk.Button(
            images_frame, text="Șterge selectat", command=self.remove_selected_image
        ).pack(side="left")

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
            text="Start",
            command=self.toggle_interval,
            width=8,
        )
        self.interval_button.grid(row=0, column=3, padx=8, sticky="w")

        tk.Label(
            interval_frame,
            text="(rulări repetate până apeși Stop sau închizi aplicația)",
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
            bottom_frame, text="Rulează acum", command=self.run_now_clicked
        )
        self.run_btn.pack(side="right")

        self._update_interval_button_text()

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
        if (
            CONFIG.get("schedule_enabled_morning")
            or CONFIG.get("schedule_enabled_evening")
            or CONFIG.get("interval_enabled")
        ):
            self.scheduler_thread = SchedulerThread(self)
            self.scheduler_thread.start()

    def _update_interval_button_text(self):
        if self.interval_enabled_var.get():
            self.interval_button.config(text="Stop")
        else:
            self.interval_button.config(text="Start")

    def toggle_interval(self):
        current = self.interval_enabled_var.get()
        self.interval_enabled_var.set(not current)
        self._update_interval_button_text()
        self.schedule_changed()

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
        self._update_interval_button_text()
        self._start_scheduler_if_needed()

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
        if resp.get("error"):
            self.license_status_var.set(f"Eroare: {resp['error']}")
            return

        status = resp.get("status", "unknown")
        exp = resp.get("expires_at")
        is_trial = resp.get("is_trial")
        extra = resp.get("note")

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
        try:
            delay = int(self.delay_var.get() or "120")
        except ValueError:
            delay = 120

        if simulate is None:
            simulate = bool(self.simulate_var.get())

        t = threading.Thread(
            target=self._run_thread,
            args=(groups, text, list(self.images), delay, simulate),
            daemon=True,
        )
        t.start()

    def run_now_clicked(self):
        self.run_now(simulate=None, from_scheduler=False)

    def _run_thread(self, groups, text, images, delay, simulate):
        self.is_running = True
        try:
            self.run_btn.config(state="disabled")
        except Exception:
            pass
        self.status_var.set("Rulez postările...")
        try:
            # log către server (best-effort)
            try:
                resp = log_run(groups, text, images)
                print("[LOG_RUN]", resp)
            except Exception as e:
                print("[WARN] Nu pot trimite log_run:", e)

            # rulare efectivă
            run_posting(groups, text, images, delay, simulate=simulate)
            if simulate:
                self.status_var.set("Gata (simulare).")
            else:
                self.status_var.set("Gata – postările ar trebui să fie publicate.")
        finally:
            self.is_running = False
            try:
                self.run_btn.config(state="normal")
            except Exception:
                pass


# ================== MAIN ==================

def main():
    root = tk.Tk()
    app = FacepostApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()


