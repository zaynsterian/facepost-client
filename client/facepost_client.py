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
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

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
    "chrome_profile_dir": "",    # se generează automat
    "group_urls": "",
    "post_text": "",
    "image_files": [],
    "delay_seconds": 120,

    # vechi (single scheduler) – le lăsăm pt compatibilitate, dar nu le mai folosim
    "schedule_enabled": False,
    "schedule_time": "09:00",

    # nou: două runde pe zi
    "schedule_enabled_morning": False,
    "schedule_time_morning": "08:00",   # HH:MM
    "schedule_enabled_evening": False,
    "schedule_time_evening": "19:00",
}


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
        hw = f"{platform.node()}-{uuid.uuid4()}"
        cfg["device_id"] = hashlib.sha256(hw.encode("utf-8")).hexdigest()[:32]

    # profil Chrome dedicat
    if not cfg.get("chrome_profile_dir"):
        base = Path.home() / ".facepost_chrome_profile"
        base.mkdir(parents=True, exist_ok=True)
        cfg["chrome_profile_dir"] = str(base)

    # mic fallback: dacă userul avea doar schedule_enabled vechi,
    # îl mapăm pe dimineață ca să nu piardă setarea
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
        print("[WARN] nu pot salva config:", e)


CONFIG = load_config()


# ================== API LICENȚE (CHECK / BIND) ==================

def api_post(path: str, payload: dict) -> dict:
    url = f"{CONFIG.get('server_url', API_URL).rstrip('/')}{path}"
    try:
        r = requests.post(url, json=payload, timeout=15)
        try:
            data = r.json()
        except Exception:
            data = {"error": f"HTTP {r.status_code}"}
        return data
    except Exception as e:
        return {"error": str(e)}


def bind_license(email: str, fingerprint: str) -> dict:
    """Leagă device-ul de licență: POST /bind"""
    return api_post("/bind", {"email": email, "fingerprint": fingerprint})


def check_license(email: str, fingerprint: str) -> dict:
    """Verifică licența pentru device: POST /check"""
    return api_post("/check", {"email": email, "fingerprint": fingerprint})


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
            f"Nu pot porni Chrome.\nVerifică dacă {CHROMEDRIVER_NAME} este lângă Facepost.exe.\n\n{e}",
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


# ================== LOGICĂ POSTARE CU SELENIUM ==================

def do_post_in_group(driver: webdriver.Chrome,
                     group_url: str,
                     text: str,
                     image_files: list[str],
                     simulate: bool = False) -> bool:
    """
    Postează într-un singur grup:
      1) merge la URL
      2) apasă „Scrie ceva...” / „What's on your mind...”
      3) scrie textul
      4) atașează imagini
      5) dacă nu e simulare → Postează
    """
    print("[DEBUG] Navighez la grup:", group_url)
    driver.get(group_url)
    wait = WebDriverWait(driver, 30)

    # 1) butonul de composer
    composer = None
    composer_xpaths = [
        # RO
        "//div[@role='button']//span[contains(text(),'Scrie ceva')]",
        "//div[@role='button']//span[contains(text(),'Scrie o postare')]",
        # EN
        "//div[@role='button']//span[contains(text(),\"What's on your mind\")]",
        "//div[@role='button' and contains(.,\"What's on your mind\")]",
    ]
    for xp in composer_xpaths:
        try:
            composer = wait.until(EC.element_to_be_clickable((By.XPATH, xp)))
            if composer:
                print("[DEBUG] Am găsit composer prin XPATH:", xp)
                composer.click()
                break
        except TimeoutException:
            continue

    if not composer:
        print("[WARN] Nu am găsit butonul de composer (Scrie ceva...).")
        return False

    # 2) editorul din dialog
    try:
        editor = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//div[@role='dialog']//div[@role='textbox']")
            )
        )
    except TimeoutException:
        print("[WARN] Nu am găsit editorul principal din dialog.")
        return False

    editor.click()
    time.sleep(1)
    if text.strip():
        editor.send_keys(text)
    else:
        print("[WARN] Nu ai text de postare – continui doar cu imagini.")

    # 3) upload imagini
    if image_files:
        joined = "\n".join(image_files)
        print("[DEBUG] Atașez imagini:")
        for p in image_files:
            print("  -", p)
        try:
            file_input = wait.until(
                EC.presence_of_element_located(
                    (
                        By.XPATH,
                        "//div[@role='dialog']//input[@type='file' and contains(@accept,'image')]",
                    )
                )
            )
            file_input.send_keys(joined)
            time.sleep(5)
        except TimeoutException:
            print("[WARN] Nu am găsit input-ul de fișiere pentru imagini – postez doar text.")

    # simulare?
    if simulate:
        print("[DEBUG] SIMULARE: nu apăs butonul Post/Publish.")
        return True

    # 5) buton Post
    try:
        post_btn = None
        post_xpaths = [
            "//div[@role='dialog']//div[@aria-label='Postează']",
            "//div[@role='dialog']//div[@aria-label='Post']",
            "//div[@role='dialog']//div[@aria-label='Publish']",
            "//div[@role='dialog']//span[text()='Postează']/ancestor::div[@role='button']",
            "//div[@role='dialog']//span[text()='Post']/ancestor::div[@role='button']",
        ]
        for xp in post_xpaths:
            try:
                post_btn = wait.until(EC.element_to_be_clickable((By.XPATH, xp)))
                if post_btn:
                    print("[DEBUG] Am găsit butonul Post prin XPATH:", xp)
                    break
            except TimeoutException:
                continue

        if not post_btn:
            print("[WARN] Nu am găsit butonul Post/Publish.")
            return False

        post_btn.click()
        print("[DEBUG] Am apăsat butonul Post.")
        time.sleep(5)
        return True
    except Exception as e:
        print("[ERROR] Eroare la apăsat butonul Post:", e)
        return False


def run_posting(groups: list[str],
                text: str,
                image_files: list[str],
                delay_seconds: int,
                simulate: bool = False) -> None:
    """Rulează secvența de postare pentru toate grupurile."""
    if not groups:
        print("[INFO] Niciun grup – nimic de făcut.")
        return

    try:
        driver = create_driver()
    except WebDriverException as e:
        messagebox.showerror(
            APP_NAME,
            f"Nu pot porni Chrome.\nVerifică chromedriver-ul.\n\n{e}",
        )
        return

    try:
        for idx, url in enumerate(groups, start=1):
            url = url.strip()
            if not url:
                continue
            print(f"[INFO] ({idx}/{len(groups)}) Postez în grup: {url}")
            ok = do_post_in_group(driver, url, text, image_files, simulate=simulate)
            if not ok:
                print("[WARN] Postarea în acest grup pare să fi eșuat.")
            if idx < len(groups) and delay_seconds > 0:
                print(f"[INFO] Aștept {delay_seconds} secunde înainte de următorul grup...")
                time.sleep(delay_seconds)
    finally:
        driver.quit()
        print("[INFO] Am închis browserul.")


# ================== UI TKINTER ==================

class FacepostApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("780x640")

        self.config = CONFIG

        # stare
        self.is_running = False
        self.images: list[str] = list(self.config.get("image_files", []))
        self.simulate_var = tk.BooleanVar(value=False)

        # programare: 2 runde
        self.schedule_morning_enabled = tk.BooleanVar(
            value=self.config.get("schedule_enabled_morning", False)
        )
        self.schedule_morning_time_var = tk.StringVar(
            value=self.config.get("schedule_time_morning", "08:00")
        )
        self.schedule_evening_enabled = tk.BooleanVar(
            value=self.config.get("schedule_enabled_evening", False)
        )
        self.schedule_evening_time_var = tk.StringVar(
            value=self.config.get("schedule_time_evening", "19:00")
        )

        self.next_run_morning: datetime | None = None
        self.next_run_evening: datetime | None = None

        self.build_ui()
        self.root.after(1000, self.schedule_tick)

        # la start, verificăm licența
        self.check_license_startup()

    # ---------- UI building ----------

    def build_ui(self):
        # Sus: email + buton configure FB
        top = ttk.Frame(self.root)
        top.pack(fill="x", pady=5, padx=10)

        ttk.Label(top, text="Email licență:").pack(side="left")
        self.email_var = tk.StringVar(value=self.config.get("email", ""))
        self.email_entry = ttk.Entry(top, textvariable=self.email_var, width=30)
        self.email_entry.pack(side="left", padx=5)

        ttk.Button(top, text="Salvează email", command=self.save_email).pack(side="left", padx=5)
        ttk.Button(top, text="Configurează login Facebook",
                   command=lambda: configure_facebook_login(self.root)).pack(side="right")

        # Group URLs
        grp_frame = ttk.LabelFrame(self.root, text="Group URLs (unul pe linie)")
        grp_frame.pack(fill="both", expand=True, padx=10, pady=(5, 5))

        self.group_text = tk.Text(grp_frame, height=6)
        self.group_text.pack(fill="both", expand=True)
        if self.config.get("group_urls"):
            self.group_text.insert("1.0", self.config["group_urls"])

        # Images
        img_frame = ttk.Frame(self.root)
        img_frame.pack(fill="x", padx=10, pady=(5, 0))

        ttk.Button(img_frame, text="Alege imagini", command=self.select_images).pack(side="left")
        self.images_label = ttk.Label(img_frame, text="")
        self.images_label.pack(side="left", padx=8)
        self.update_images_label()

        # Post text
        post_frame = ttk.LabelFrame(self.root, text="Post text")
        post_frame.pack(fill="both", expand=True, padx=10, pady=(5, 5))

        self.post_text = tk.Text(post_frame, height=8)
        self.post_text.pack(fill="both", expand=True)
        if self.config.get("post_text"):
            self.post_text.insert("1.0", self.config["post_text"])

        # Delay + simulate
        bottom1 = ttk.Frame(self.root)
        bottom1.pack(fill="x", padx=10, pady=(5, 0))

        ttk.Label(bottom1, text="Delay între grupuri (secunde):").pack(side="left")
        self.delay_var = tk.StringVar(value=str(self.config.get("delay_seconds", 120)))
        self.delay_entry = ttk.Entry(bottom1, textvariable=self.delay_var, width=6)
        self.delay_entry.pack(side="left", padx=5)

        ttk.Checkbutton(bottom1, text="Simulare (nu posta efectiv)",
                        variable=self.simulate_var).pack(side="left", padx=10)

        # Scheduler – două runde
        sched_frame = ttk.LabelFrame(self.root, text="Programare zilnică (max 2 runde)")
        sched_frame.pack(fill="x", padx=10, pady=(5, 0))

        row_m = ttk.Frame(sched_frame)
        row_m.pack(fill="x", pady=2)
        ttk.Checkbutton(row_m, text="Rundă dimineața",
                        variable=self.schedule_morning_enabled).pack(side="left")
        ttk.Label(row_m, text="la ora (HH:MM):").pack(side="left", padx=(10, 2))
        self.schedule_morning_entry = ttk.Entry(
            row_m, textvariable=self.schedule_morning_time_var, width=6
        )
        self.schedule_morning_entry.pack(side="left")

        row_e = ttk.Frame(sched_frame)
        row_e.pack(fill="x", pady=2)
        ttk.Checkbutton(row_e, text="Rundă seara",
                        variable=self.schedule_evening_enabled).pack(side="left")
        ttk.Label(row_e, text="la ora (HH:MM):").pack(side="left", padx=(10, 2))
        self.schedule_evening_entry = ttk.Entry(
            row_e, textvariable=self.schedule_evening_time_var, width=6
        )
        self.schedule_evening_entry.pack(side="left")

        self.next_run_label = ttk.Label(sched_frame, text="Programare oprită")
        self.next_run_label.pack(anchor="w", padx=4, pady=(4, 2))

        # Butoane jos
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", padx=10, pady=10)

        ttk.Button(btn_frame, text="Preview", command=self.preview).pack(side="left")
        ttk.Button(btn_frame, text="Salvează config", command=self.save_all).pack(side="left", padx=5)
        self.run_btn = ttk.Button(btn_frame, text="Run acum", command=self.run_now)
        self.run_btn.pack(side="right")

        self.status_var = tk.StringVar(value="Gata.")
        ttk.Label(self.root, textvariable=self.status_var).pack(anchor="w", padx=10, pady=(0, 5))

    # ---------- Helpers UI ----------

    def update_images_label(self):
        if self.images:
            self.images_label.config(text=f"{len(self.images)} imagini selectate")
        else:
            self.images_label.config(text="Nicio imagine selectată")

    def select_images(self):
        files = filedialog.askopenfilenames(
            title="Alege imaginile",
            filetypes=[
                ("Imagini", "*.png;*.jpg;*.jpeg;*.webp;*.gif"),
                ("Toate fișierele", "*.*"),
            ],
        )
        if files:
            self.images = list(files)
            self.update_images_label()

    def save_email(self):
        email = self.email_var.get().strip().lower()
        if not email:
            messagebox.showerror(APP_NAME, "Te rog introdu un email.", parent=self.root)
            return
        self.config["email"] = email
        save_config(self.config)
        messagebox.showinfo(APP_NAME, "Email salvat.", parent=self.root)

    def save_all(self):
        self.config["email"] = self.email_var.get().strip().lower()
        self.config["group_urls"] = self.group_text.get("1.0", "end").strip()
        self.config["post_text"] = self.post_text.get("1.0", "end").strip()
        try:
            self.config["delay_seconds"] = int(self.delay_var.get() or "120")
        except ValueError:
            self.config["delay_seconds"] = 120

        self.config["image_files"] = self.images

        # nou: salvăm cele două runde
        self.config["schedule_enabled_morning"] = bool(self.schedule_morning_enabled.get())
        self.config["schedule_time_morning"] = self.schedule_morning_time_var.get().strip() or "08:00"
        self.config["schedule_enabled_evening"] = bool(self.schedule_evening_enabled.get())
        self.config["schedule_time_evening"] = self.schedule_evening_time_var.get().strip() or "19:00"

        save_config(self.config)
        messagebox.showinfo(APP_NAME, "Config salvat.", parent=self.root)

    # ---------- Licență ----------

    def check_license_startup(self):
        email = (self.config.get("email") or "").strip().lower()
        if not email:
            self.status_var.set("Introduce emailul licenței și salvează.")
            return

        self.status_var.set("Verific licența...")
        self.root.update_idletasks()

        resp = check_license(email, self.config.get("device_id"))
        if resp.get("status") == "ok":
            self.status_var.set("Licență OK.")
            return
        else:
            # încercăm și bind + check
            b = bind_license(email, self.config.get("device_id"))
            if b.get("status") == "ok":
                c = check_license(email, self.config.get("device_id"))
                if c.get("status") == "ok":
                    self.status_var.set("Licență OK.")
                    return

        err = resp.get("error") or resp.get("status") or "Licență invalidă."
        self.status_var.set(f"Probleme licență: {err}")
        messagebox.showerror(APP_NAME, f"Probleme licență: {err}", parent=self.root)

    # ---------- Programare zilnică (2 runde) ----------

    def parse_time_str(self, val: str) -> dtime | None:
        val = (val or "").strip()
        try:
            h, m = map(int, val.split(":"))
            return dtime(hour=h, minute=m)
        except Exception:
            return None

    def compute_next_for(self, time_str: str, enabled: bool) -> datetime | None:
        if not enabled:
            return None
        t = self.parse_time_str(time_str)
        if not t:
            return None
        now = datetime.now(UTC)
        cand = datetime.combine(now.date(), t, tzinfo=UTC)
        if cand <= now:
            cand += timedelta(days=1)
        return cand

    def schedule_tick(self):
        now = datetime.now(UTC)

        # actualizăm next_run pentru fiecare rundă dacă e nevoie
        if self.schedule_morning_enabled.get():
            if not self.next_run_morning:
                self.next_run_morning = self.compute_next_for(
                    self.schedule_morning_time_var.get(),
                    True,
                )
            elif self.next_run_morning <= now:
                # a „expirat” -> după ce rulează, îl recalculăm
                self.next_run_morning = self.compute_next_for(
                    self.schedule_morning_time_var.get(),
                    True,
                )
        else:
            self.next_run_morning = None

        if self.schedule_evening_enabled.get():
            if not self.next_run_evening:
                self.next_run_evening = self.compute_next_for(
                    self.schedule_evening_time_var.get(),
                    True,
                )
            elif self.next_run_evening <= now:
                self.next_run_evening = self.compute_next_for(
                    self.schedule_evening_time_var.get(),
                    True,
                )
        else:
            self.next_run_evening = None

        # alegem cea mai apropiată execuție
        candidates = [dt for dt in [self.next_run_morning, self.next_run_evening] if dt]
        if candidates:
            next_dt = min(candidates)
            delta = next_dt - now
            if delta.total_seconds() <= 0 and not self.is_running:
                # lansăm run acum
                self.run_now()
                # după run_now, la următorul tick se vor recalcula
            else:
                mins = int(delta.total_seconds() // 60)
                secs = int(delta.total_seconds() % 60)
                hhmm = next_dt.strftime("%H:%M")
                self.next_run_label.config(
                    text=f"Următoarea postare la {hhmm} (~{mins}m {secs}s)"
                )
        else:
            self.next_run_label.config(text="Programare oprită")

        self.root.after(1000, self.schedule_tick)

    # ---------- Run ----------

    def preview(self):
        groups_raw = self.group_text.get("1.0", "end").strip()
        groups = [g for g in groups_raw.splitlines() if g.strip()]
        text = self.post_text.get("1.0", "end").strip()
        msg = (
            f"Grupuri ({len(groups)}):\n" +
            "\n".join(groups[:5]) +
            ("\n..." if len(groups) > 5 else "") +
            "\n\nText:\n" + text[:500] +
            ("\n..." if len(text) > 500 else "") +
            f"\n\nImagini: {len(self.images)}"
        )
        messagebox.showinfo(APP_NAME, msg, parent=self.root)

    def run_now(self):
        if self.is_running:
            return
        # salvăm ce avem
        self.save_all()

        # verificăm din nou licența
        email = (self.config.get("email") or "").strip().lower()
        if not email:
            messagebox.showerror(APP_NAME, "Te rog setează emailul licenței.", parent=self.root)
            return
        resp = check_license(email, self.config.get("device_id"))
        if resp.get("status") != "ok":
            err = resp.get("error") or resp.get("status") or "Licență invalidă."
            messagebox.showerror(APP_NAME, f"Nu pot porni: {err}", parent=self.root)
            return

        # colectăm datele
        groups_raw = self.group_text.get("1.0", "end").strip()
        groups = [g for g in groups_raw.splitlines() if g.strip()]
        if not groups:
            messagebox.showerror(APP_NAME, "Te rog introdu cel puțin un URL de grup.", parent=self.root)
            return
        text = self.post_text.get("1.0", "end").strip()
        try:
            delay = int(self.delay_var.get() or "120")
        except ValueError:
            delay = 120

        simulate = bool(self.simulate_var.get())

        # rulăm în thread separat
        t = threading.Thread(
            target=self._run_thread,
            args=(groups, text, list(self.images), delay, simulate),
            daemon=True,
        )
        t.start()

    def _run_thread(self, groups, text, images, delay, simulate):
        self.is_running = True
        self.run_btn.config(state="disabled")
        self.status_var.set("Rulez postările...")
        try:
            run_posting(groups, text, images, delay, simulate=simulate)
            if simulate:
                self.status_var.set("Gata (simulare).")
            else:
                self.status_var.set("Gata – postările ar trebui să fie publicate.")
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
