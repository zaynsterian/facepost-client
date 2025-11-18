import os
import sys
import json
import time
import threading
from uuid import uuid4
from dataclasses import dataclass, asdict
from typing import List, Optional

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import requests

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException

# ------------------ Config generale ------------------

LICENSE_API_BASE = "https://facepost.onrender.com"  # serverul tău de licențe

# determinăm folderul unde stă EXE-ul / scriptul
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(BASE_DIR, "facepost_config.json")
CHROMEDRIVER_PATH = os.path.join(BASE_DIR, "chromedriver.exe")


@dataclass
class AppConfig:
    email: str = ""
    device_id: str = ""
    groups: List[str] = None
    images_folder: str = ""
    post_text: str = ""
    delay_seconds: int = 120
    chrome_profile_dir: str = ""
    simulate_only: bool = True  # implicit rulăm în mod „simulat”


def load_config() -> AppConfig:
    if not os.path.exists(CONFIG_PATH):
        cfg = AppConfig(groups=[])
        # generăm un device_id la prima rulare
        cfg.device_id = str(uuid4())
        save_config(cfg)
        return cfg

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # back-compat / valori lipsă
        data.setdefault("groups", [])
        data.setdefault("simulate_only", True)
        data.setdefault("device_id", str(uuid4()))
        return AppConfig(**data)
    except Exception:
        # dacă ceva e corupt, pornim cu config nou
        cfg = AppConfig(groups=[], device_id=str(uuid4()))
        save_config(cfg)
        return cfg


def save_config(cfg: AppConfig) -> None:
    data = asdict(cfg)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ------------------ License check ------------------


class LicenseError(Exception):
    pass


def check_license(email: str, device_id: str) -> dict:
    email = (email or "").strip().lower()
    if not email:
        raise LicenseError("Te rog introdu o adresă de email.")

    payload = {"email": email, "fingerprint": device_id}

    try:
        resp = requests.post(
            f"{LICENSE_API_BASE}/check",
            json=payload,
            timeout=15,
        )
    except requests.RequestException as e:
        raise LicenseError(f"Nu se poate contacta serverul de licențe: {e}")

    if resp.status_code != 200:
        raise LicenseError(f"Răspuns neașteptat de la server ({resp.status_code}).")

    try:
        data = resp.json()
    except Exception:
        raise LicenseError("Răspuns invalid de la server (nu e JSON).")

    status = data.get("status", "error")
    if status != "ok":
        msg = data.get("message") or f"Licență invalidă (status: {status})."
        raise LicenseError(msg)

    return data


# ------------------ Selenium posting logic ------------------


class Poster:
    def __init__(self, cfg: AppConfig, log_callback=None):
        self.cfg = cfg
        self.log_callback = log_callback or (lambda msg: None)
        self.driver: Optional[webdriver.Chrome] = None

    def log(self, msg: str):
        print(msg)
        self.log_callback(msg)

    def _build_driver(self):
        if not os.path.exists(CHROMEDRIVER_PATH):
            raise RuntimeError(
                "chromedriver.exe nu a fost găsit lângă Facepost.exe.\n"
                "Asigură-te că installerul copiază și chromedriver în același folder."
            )

        options = Options()
        options.add_argument("--start-maximized")

        if self.cfg.chrome_profile_dir:
            options.add_argument(f"--user-data-dir={self.cfg.chrome_profile_dir}")

        service = Service(CHROMEDRIVER_PATH)
        try:
            driver = webdriver.Chrome(service=service, options=options)
        except WebDriverException as e:
            raise RuntimeError(f"Eroare la pornirea Chrome: {e}")

        self.driver = driver

    def _collect_images(self) -> List[str]:
        folder = (self.cfg.images_folder or "").strip()
        if not folder:
            return []

        if not os.path.isdir(folder):
            self.log(f"[WARN] Folderul de imagini nu există: {folder}")
            return []

        exts = (".jpg", ".jpeg", ".png", ".webp")
        files = [
            os.path.join(folder, fn)
            for fn in sorted(os.listdir(folder))
            if fn.lower().endswith(exts)
        ]
        return files

    def _post_to_group(self, url: str, text: str, images: List[str]):
        """
        Aici pui logica ta reală de postare în grup.
        Acum doar deschide pagina și așteaptă câteva secunde.
        """
        assert self.driver is not None
        self.log(f"[INFO] Deschid grupul: {url}")
        self.driver.get(url)
        time.sleep(5)

        # TODO: Înlocuiește cu logica ta:
        # 1. Click pe "Creează o postare"
        # 2. Introdu textul în editor
        # 3. Încarcă imagini (dacă există)
        # 4. Apasă pe "Publică"
        #
        # Ca exemplu, ceva de genul:
        # create_btn = self.driver.find_element(By.XPATH, "//div[@role='button' and contains(., 'Creează o postare')]")
        # create_btn.click()
        # time.sleep(2)
        # editor = self.driver.find_element(By.XPATH, "//div[@role='textbox']")
        # editor.send_keys(text)
        # etc.

        self.log(f"[INFO] (DEMO) Am deschis grupul și aștept. Nu am postat nimic încă.")
        time.sleep(3)

    def run(self):
        groups = [g.strip() for g in (self.cfg.groups or []) if g.strip()]
        if not groups:
            raise RuntimeError("Nu ai introdus niciun URL de grup.")

        text = (self.cfg.post_text or "").strip()
        if not text:
            raise RuntimeError("Te rog introdu textul pentru postare.")

        delay = int(self.cfg.delay_seconds or 0)
        if delay < 10:
            delay = 10

        images = self._collect_images()

        if self.cfg.simulate_only:
            self.log("=== MOD SIMULARE ACTIV ===")
            for idx, g in enumerate(groups, start=1):
                self.log(f"[SIM] ({idx}/{len(groups)}) Aș posta în: {g}")
                time.sleep(2)
                self.log(f"[SIM] Aș aștepta {delay} secunde înainte de următorul grup.")
                time.sleep(1)
            self.log("[SIM] Rulare simulată terminată.")
            return

        # Mod real – folosim Selenium
        self.log("=== PORNESC SELENIUM PENTRU POSTARE REALĂ ===")
        self._build_driver()

        try:
            for idx, g in enumerate(groups, start=1):
                self.log(f"[REAL] ({idx}/{len(groups)}) Postez în: {g}")
                self._post_to_group(g, text, images)
                if idx < len(groups):
                    self.log(f"[REAL] Aștept {delay} secunde înainte de următorul grup.")
                    time.sleep(delay)
            self.log("[REAL] Rulare completă.")
        finally:
            if self.driver:
                self.driver.quit()
                self.driver = None


# ------------------ Tkinter UI ------------------


class FacepostApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Facepost")
        self.root.geometry("800x600")

        self.config = load_config()

        self.login_frame: Optional[tk.Frame] = None
        self.main_frame: Optional[tk.Frame] = None

        self.log_text: Optional[tk.Text] = None

        self.show_login()

    # -------- helper log (thread-safe) --------
    def ui_log(self, msg: str):
        if not self.log_text:
            return
        def _append():
            self.log_text.insert(tk.END, msg + "\n")
            self.log_text.see(tk.END)
        self.root.after(0, _append)

    # -------- LOGIN --------
    def show_login(self):
        if self.main_frame:
            self.main_frame.destroy()
            self.main_frame = None

        self.login_frame = tk.Frame(self.root, padx=20, pady=20)
        self.login_frame.pack(fill=tk.BOTH, expand=True)

        lbl = tk.Label(self.login_frame, text="Autentificare Facepost", font=("Segoe UI", 16, "bold"))
        lbl.pack(pady=(0, 20))

        frm = tk.Frame(self.login_frame)
        frm.pack(pady=10)

        tk.Label(frm, text="Email:").grid(row=0, column=0, sticky="e", padx=5, pady=5)
        self.email_var = tk.StringVar(value=self.config.email)
        tk.Entry(frm, textvariable=self.email_var, width=40).grid(row=0, column=1, padx=5, pady=5)

        btn = ttk.Button(self.login_frame, text="Verifică licența", command=self.on_login)
        btn.pack(pady=10)

    def on_login(self):
        email = self.email_var.get().strip()
        try:
            data = check_license(email, self.config.device_id)
        except LicenseError as e:
            messagebox.showerror("Licență", str(e), parent=self.root)
            return

        # ok
        self.config.email = email
        save_config(self.config)
        messagebox.showinfo("Licență", "Licență validă. Bine ai venit în Facepost!", parent=self.root)
        self.show_main()

    # -------- MAIN UI --------
    def show_main(self):
        if self.login_frame:
            self.login_frame.destroy()
            self.login_frame = None

        self.main_frame = tk.Frame(self.root, padx=10, pady=10)
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        # panou sus: group URLs + imagini
        top = tk.Frame(self.main_frame)
        top.pack(fill=tk.BOTH, expand=True)

        # stânga: grupuri
        left = tk.Frame(top)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        tk.Label(left, text="Group URLs (unul pe linie):").pack(anchor="w")
        self.groups_text = tk.Text(left, height=8)
        self.groups_text.pack(fill=tk.BOTH, expand=True)

        if self.config.groups:
            self.groups_text.insert(tk.END, "\n".join(self.config.groups))

        # dreapta: folder imagini + delay + simulare
        right = tk.Frame(top)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(5, 0))

        tk.Label(right, text="Images folder:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.images_var = tk.StringVar(value=self.config.images_folder)
        tk.Entry(right, textvariable=self.images_var, width=35).grid(row=1, column=0, padx=5, pady=(0, 5))
        ttk.Button(right, text="Browse", command=self.on_browse_images).grid(
            row=1, column=1, padx=5, pady=(0, 5)
        )

        tk.Label(right, text="Delay (sec):").grid(row=2, column=0, sticky="w", padx=5, pady=(10, 0))
        self.delay_var = tk.StringVar(value=str(self.config.delay_seconds))
        tk.Entry(right, textvariable=self.delay_var, width=10).grid(row=3, column=0, padx=5, pady=5, sticky="w")

        self.sim_var = tk.BooleanVar(value=self.config.simulate_only)
        tk.Checkbutton(
            right,
            text="Simulare doar (nu postează)",
            variable=self.sim_var,
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=5, pady=(10, 0))

        ttk.Button(right, text="Setări", command=self.show_settings_dialog).grid(
            row=5, column=0, columnspan=2, padx=5, pady=(20, 0), sticky="we"
        )

        # text postare
        tk.Label(self.main_frame, text="Post text:").pack(anchor="w")
        self.post_text = tk.Text(self.main_frame, height=8)
        self.post_text.pack(fill=tk.BOTH, expand=False)
        if self.config.post_text:
            self.post_text.insert(tk.END, self.config.post_text)

        # butoane jos
        btns = tk.Frame(self.main_frame)
        btns.pack(fill=tk.X, pady=10)

        ttk.Button(btns, text="Preview", command=self.on_preview).pack(side=tk.LEFT, padx=5)
        ttk.Button(btns, text="Save", command=self.on_save).pack(side=tk.LEFT, padx=5)
        self.run_btn = ttk.Button(btns, text="Run", command=self.on_run)
        self.run_btn.pack(side=tk.LEFT, padx=5)

        ttk.Button(btns, text="Logout", command=self.on_logout).pack(side=tk.RIGHT, padx=5)

        # log text
        tk.Label(self.main_frame, text="Log:").pack(anchor="w")
        self.log_text = tk.Text(self.main_frame, height=10, state="normal")
        self.log_text.pack(fill=tk.BOTH, expand=True)

    # -------- handlers --------

    def on_browse_images(self):
        folder = filedialog.askdirectory(title="Alege folderul cu imagini", parent=self.root)
        if folder:
            self.images_var.set(folder)

    def on_preview(self):
        # doar arătăm un rezumat simplu
        groups = self._get_groups_from_ui()
        text = self.post_text.get("1.0", tk.END).strip()
        folder = self.images_var.get().strip()
        delay = self.delay_var.get().strip()

        msg = (
            f"Grupuri: {len(groups)}\n"
            f"Folder imagini: {folder or '-'}\n"
            f"Delay: {delay} sec\n"
            f"Simulare: {'DA' if self.sim_var.get() else 'NU'}\n\n"
            f"Text postare:\n{text[:500]}{'...' if len(text) > 500 else ''}"
        )
        messagebox.showinfo("Preview", msg, parent=self.root)

    def _get_groups_from_ui(self) -> List[str]:
        raw = self.groups_text.get("1.0", tk.END)
        groups = [g.strip() for g in raw.splitlines() if g.strip()]
        return groups

    def on_save(self):
        try:
            delay = int(self.delay_var.get().strip() or "0")
        except ValueError:
            messagebox.showerror("Eroare", "Delay trebuie să fie un număr.", parent=self.root)
            return

        self.config.groups = self._get_groups_from_ui()
        self.config.images_folder = self.images_var.get().strip()
        self.config.post_text = self.post_text.get("1.0", tk.END).strip()
        self.config.delay_seconds = delay
        self.config.simulate_only = self.sim_var.get()

        save_config(self.config)
        messagebox.showinfo("Salvat", "Configurația a fost salvată.", parent=self.root)

    def on_run(self):
        self.on_save()  # ne asigurăm că avem config update

        self.run_btn.config(state="disabled")
        self.ui_log("=== Pornesc rularea Facepost ===")

        def worker():
            try:
                poster = Poster(self.config, log_callback=self.ui_log)
                poster.run()
                self.ui_log("=== Rulare terminată ===")
                messagebox.showinfo("Facepost", "Rulare terminată.", parent=self.root)
            except Exception as e:
                self.ui_log(f"[ERROR] {e}")
                messagebox.showerror("Eroare", str(e), parent=self.root)
            finally:
                self.root.after(0, lambda: self.run_btn.config(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    def on_logout(self):
        if messagebox.askyesno("Logout", "Sigur vrei să te deloghezi?", parent=self.root):
            self.config.email = ""
            save_config(self.config)
            self.show_login()

    # -------- SETTINGS DIALOG --------
    def show_settings_dialog(self):
        win = tk.Toplevel(self.root)
        win.title("Setări Facepost")
        win.grab_set()
        win.resizable(False, False)

        frm = tk.Frame(win, padx=10, pady=10)
        frm.pack(fill=tk.BOTH, expand=True)

        tk.Label(frm, text="Chrome profile dir (opțional):").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        chrome_var = tk.StringVar(value=self.config.chrome_profile_dir)
        tk.Entry(frm, textvariable=chrome_var, width=40).grid(row=1, column=0, padx=5, pady=(0, 5))
        ttk.Button(frm, text="Browse", command=lambda: self._browse_profile(chrome_var, win)).grid(
            row=1, column=1, padx=5, pady=(0, 5)
        )

        def on_ok():
            self.config.chrome_profile_dir = chrome_var.get().strip()
            save_config(self.config)
            win.destroy()

        ttk.Button(frm, text="OK", command=on_ok).grid(row=2, column=0, columnspan=2, pady=10)

    def _browse_profile(self, var: tk.StringVar, parent):
        folder = filedialog.askdirectory(title="Alege folderul pentru profilul Chrome", parent=parent)
        if folder:
            var.set(folder)


# ------------------ entrypoint ------------------


def main():
    root = tk.Tk()
    app = FacepostApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
