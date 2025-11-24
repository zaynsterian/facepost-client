import os
import sys
import json
import time
import uuid
import threading
import hashlib
from pathlib import Path
from datetime import datetime, timedelta, time as dtime, timezone
import platform

import requests
import tkinter as tk
from tkinter import messagebox, filedialog, ttk

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ================== CONFIG GLOBALA ==================

APP_NAME = "Facepost"
API_URL = "https://facepost.onrender.com"   # serverul tău Render
CONFIG_FILE = Path.home() / ".facepost_config.json"
CHROMEDRIVER_NAME = "chromedriver.exe"     # în același folder cu EXE-ul

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
    "post_text": "",
    "groups_text": "",
    "images": [],
    "delay_seconds": 120,
    "simulate": False,
}


def stable_fingerprint() -> str:
    """
    Fingerprint stabil pentru device (hash din câteva info de sistem).
    """
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

    if not cfg.get("device_id"):
        cfg["device_id"] = stable_fingerprint()

    if not cfg.get("chrome_profile_dir"):
        base = Path.home() / ".facepost_chrome_profile"
        base.mkdir(parents=True, exist_ok=True)
        cfg["chrome_profile_dir"] = str(base)

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


# ================== API LICENTE ==================

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


# ================== SELENIUM: CHROME DRIVER ==================

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


def wait_for_facebook_home(driver, timeout=60):
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )


def open_group_and_post(driver, group_url: str, text: str, images, simulate: bool = False):
    """
    Deschide un link de grup și postează textul + pozele.
    (Aici rămâne logica ta existentă de automatizare; placeholder.)
    """
    print(
        f"[DEBUG] Ar posta în {group_url} cu text de {len(text)} caractere și {len(images)} imagini. simulate={simulate}"
    )
    time.sleep(1)


def run_posting(groups: list[str], text: str, images, delay: int, simulate: bool = False):
    """
    Rulează efectiv postarea în toate grupurile, cu delay între ele.
    """
    driver = None
    try:
        driver = create_driver()
        wait_for_facebook_home(driver)

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

def parse_time_str(s: str) -> dtime | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        hh, mm = s.split(":")
        return dtime(hour=int(hh), minute=int(mm))
    except Exception:
        return None


def next_run_time_for(config: dict, which: str) -> datetime | None:
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


def compute_next_schedule_run(config: dict) -> datetime | None:
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
    """

    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self._stop_flag = threading.Event()

    def stop(self):
        self._stop_flag.set()

    def run(self):
        while not self._stop_flag.is_set():
            try:
                cfg = CONFIG
                next_run = compute_next_schedule_run(cfg)
                if not next_run:
                    time.sleep(5)
                    continue

                now = datetime.now()
                if now >= next_run:
                    print("[SCHEDULER] E timpul pentru run programat.")
                    self.app.run_now(simulate=False, from_scheduler=True)
                    time.sleep(60)
                else:
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
        self.images = set()
        self.scheduler_thread = None

        self.email_var = tk.StringVar(value=CONFIG.get("email", ""))
        self.server_url_var = tk.StringVar(value=CONFIG.get("server_url", API_URL))
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

        self._build_ui()
        self._load_initial_texts()
        self._start_scheduler_if_needed()

    def _build_ui(self):
        root = self.root
        root.geometry("900x700")

        main_frame = tk.Frame(root)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        config_frame = tk.LabelFrame(main_frame, text="Config licență & server")
        config_frame.pack(fill="x", pady=5)

        tk.Label(config_frame, text="Email licență:").grid(row=0, column=0, sticky="w")
        tk.Entry(config_frame, textvariable=self.email_var, width=40).grid(
            row=0, column=1, sticky="w"
        )

        tk.Label(config_frame, text="Server URL:").grid(row=0, column=2, sticky="w")
        tk.Entry(config_frame, textvariable=self.server_url_var, width=40).grid(
            row=0, column=3, sticky="w"
        )

        tk.Button(
            config_frame, text="Salvează config", command=self.save_config_clicked
        ).grid(row=0, column=4, padx=5)

        self.license_status_var = tk.StringVar(value="Status licență necunoscut.")
        tk.Button(
            config_frame, text="Check licență", command=self.check_license_clicked
        ).grid(row=1, column=0, pady=5)
        tk.Button(
            config_frame, text="Bind licență", command=self.bind_license_clicked
        ).grid(row=1, column=1, pady=5)
        tk.Label(config_frame, textvariable=self.license_status_var, fg="blue").grid(
            row=1, column=2, columnspan=3, sticky="w"
        )

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

        schedule_frame = tk.LabelFrame(main_frame, text="Programare automată")
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

    def _load_initial_texts(self):
        self.post_text.delete("1.0", "end")
        self.post_text.insert("1.0", CONFIG.get("post_text", ""))

        self.group_text.delete("1.0", "end")
        self.group_text.insert("1.0", CONFIG.get("groups_text", ""))

        self.images = set(CONFIG.get("images", []))
        self.images_listbox.delete(0, "end")
        for img in self.images:
            self.images_listbox.insert("end", img)

    def _start_scheduler_if_needed(self):
        if self.scheduler_thread is not None:
            return
        if CONFIG.get("schedule_enabled_morning") or CONFIG.get(
            "schedule_enabled_evening"
        ):
            self.scheduler_thread = SchedulerThread(self)
            self.scheduler_thread.start()

    def save_config_clicked(self):
        CONFIG["email"] = self.email_var.get().strip()
        CONFIG["server_url"] = self.server_url_var.get().strip()
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

        save_config(CONFIG)
        messagebox.showinfo(APP_NAME, "Config salvată.", parent=self.root)

    def check_license_clicked(self):
        email = self.email_var.get().strip().lower()
        if not email:
            messagebox.showerror(
                APP_NAME, "Te rog introdu emailul licenței.", parent=self.root
            )
            return
        CONFIG["email"] = email
        CONFIG["server_url"] = self.server_url_var.get().strip()
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
        CONFIG["server_url"] = self.server_url_var.get().strip()
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

    def add_images_clicked(self):
        paths = filedialog.askopenfilenames(
            title="Alege imagini",
            filetypes=[
                (
                    "Imagini",
                    "*.png;*.jpg;*.jpeg;*.gif;*.bmp;*.webp",
                ),
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

    def schedule_changed(self):
        CONFIG["schedule_enabled_morning"] = bool(
            self.schedule_enabled_morning_var.get()
        )
        CONFIG["schedule_time_morning"] = self.schedule_time_morning_var.get().strip()
        CONFIG["schedule_enabled_evening"] = bool(
            self.schedule_enabled_evening_var.get()
        )
        CONFIG["schedule_time_evening"] = self.schedule_time_evening_var.get().strip()
        save_config(CONFIG)
        if not self.scheduler_thread and (
            CONFIG.get("schedule_enabled_morning")
            or CONFIG.get("schedule_enabled_evening")
        ):
            self._start_scheduler_if_needed()

    def run_now(self, simulate: bool | None = None, from_scheduler: bool = False):
        if self.is_running:
            messagebox.showwarning(
                APP_NAME,
                "Deja rulează o sesiune de postare.",
                parent=self.root,
            )
            return

        email = self.email_var.get().strip().lower()
        if not email:
            messagebox.showerror(
                APP_NAME, "Te rog introdu emailul licenței.", parent=self.root
            )
            return

        CONFIG["email"] = email
        CONFIG["server_url"] = self.server_url_var.get().strip()
        save_config(CONFIG)

        resp = check_license(email, CONFIG.get("device_id"))
        if resp.get("error"):
            messagebox.showerror(
                APP_NAME,
                f"Eroare la check licență: {resp['error']}",
                parent=self.root,
            )
            return
        if resp.get("status") not in ("ok",):
            messagebox.showerror(
                APP_NAME,
                f"Licența nu este activă sau este expirată ({resp.get('status')}).",
                parent=self.root,
            )
            return

        groups_raw = self.group_text.get("1.0", "end").strip()
        groups = [g for g in groups_raw.splitlines() if g.strip()]
        if not groups:
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
        self.run_btn.config(state="disabled")
        self.status_var.set("Rulez postările...")
        try:
            # trimitem log către server (best-effort; nu blocăm rularea dacă apare o eroare)
            try:
                resp = log_run(groups, text, images)
                print("[LOG_RUN]", resp)
            except Exception as e:
                print("[WARN] Nu pot trimite log_run:", e)

            # apoi rulăm efectiv postările
            run_posting(groups, text, images, delay, simulate=simulate)
            if simulate:
                self.status_var.set("Gata (simulare).")
            else:
                self.status_var.set(
                    "Gata – postările ar trebui să fie publicate."
                )
        finally:
            self.is_running = False
            self.run_btn.config(state="normal")


# ================== MAIN ==================

def main():
    root = tk.Tk()
    app = FacepostApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
