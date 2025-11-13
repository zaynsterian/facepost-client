# facepost_client.py  (Tkinter UI)
import os, sys, json, time, logging, traceback
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import requests

APP_NAME = "Facepost"
API_BASE = "https://facepost.onrender.com"   # <- schimbă dacă ai alt URL

# --- log setup ---
LOG_DIR = os.path.join(os.getenv('LOCALAPPDATA', os.getcwd()), APP_NAME, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'app.log')
logging.basicConfig(filename=LOG_FILE, level=logging.DEBUG,
                    format="%(asctime)s [%(levelname)s] %(message)s")

def excepthook(exc_type, exc, tb):
    logging.exception("Uncaught", exc_info=(exc_type, exc, tb))
    try:
        messagebox.showerror(APP_NAME, f"{exc_type.__name__}: {exc}\n\nLog: {LOG_FILE}")
    finally:
        sys.exit(1)

sys.excepthook = excepthook

# --- config helpers ---
CFG_DIR = os.path.join(os.getenv('APPDATA', os.getcwd()), APP_NAME)
CFG_FILE = os.path.join(CFG_DIR, "config.json")
os.makedirs(CFG_DIR, exist_ok=True)

def load_cfg():
    if os.path.exists(CFG_FILE):
        with open(CFG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "email": "",
        "license_key": "",
        "group_urls": [],
        "images_folder": "",
        "post_text": "",
        "delay_sec": 120
    }

def save_cfg(cfg):
    with open(CFG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

# --- API helpers ---
def api_post(path, payload):
    url = f"{API_BASE}{path}"
    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()

def check_license(email, fingerprint="FP-DEVICE-1"):
    return api_post("/check", {"email": email, "fingerprint": fingerprint})

def bind_license(email, fingerprint="FP-DEVICE-1"):
    return api_post("/bind", {"email": email, "fingerprint": fingerprint})

# --- UI: Login Window ---
class LoginWindow(tk.Toplevel):
    def __init__(self, master, cfg):
        super().__init__(master)
        self.title(f"{APP_NAME} - Login")
        self.resizable(False, False)
        self.cfg = cfg
        pad = 10

        self.columnconfigure(1, weight=1)

        ttk.Label(self, text="Email").grid(row=0, column=0, padx=pad, pady=(pad,5), sticky="w")
        self.email_var = tk.StringVar(value=cfg.get("email",""))
        ttk.Entry(self, textvariable=self.email_var, width=40).grid(row=0, column=1, padx=pad, pady=(pad,5), sticky="ew")

        ttk.Label(self, text="Licență").grid(row=1, column=0, padx=pad, pady=5, sticky="w")
        self.lic_var = tk.StringVar(value=cfg.get("license_key",""))
        ttk.Entry(self, textvariable=self.lic_var, width=40, show="*").grid(row=1, column=1, padx=pad, pady=5, sticky="ew")

        self.msg = ttk.Label(self, text="", foreground="#a00")
        self.msg.grid(row=2, column=0, columnspan=2, padx=pad, pady=5, sticky="w")

        btn = ttk.Button(self, text="Continuă", command=self.do_login)
        btn.grid(row=3, column=0, columnspan=2, padx=pad, pady=(5,pad))

        self.bind("<Return>", lambda e: self.do_login())

        # focus
        self.after(100, lambda: self.email_var.set(self.email_var.get()) )

    def do_login(self):
        email = self.email_var.get().strip()
        lic = self.lic_var.get().strip()
        if not email or not lic:
            self.msg.config(text="Completează email și licență.")
            return
        try:
            # opțional: poți valida că licența e non-empty; check-ul real îl face serverul
            # înregistrăm device-ul (first bind e idempotent la serverul tău)
            bind_license(email, "FP-DEVICE-1")
            resp = check_license(email, "FP-DEVICE-1")
            if resp.get("status") == "ok":
                self.cfg["email"] = email
                self.cfg["license_key"] = lic
                save_cfg(self.cfg)
                self.destroy()
            else:
                self.msg.config(text=f"Licență invalidă / expirat.")
        except requests.HTTPError as e:
            logging.exception("HTTP error on login")
            self.msg.config(text=f"Server: {e.response.status_code}")
        except Exception as e:
            logging.exception("Login error")
            self.msg.config(text=str(e))

# --- UI: Main Window ---
class MainApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("820x640")
        self.minsize(780, 560)
        self.cfg = load_cfg()

        # dacă nu avem email/licență → login
        if not self.cfg.get("email") or not self.cfg.get("license_key"):
            self.wait_visibility()
            LoginWindow(self, self.cfg).wait_window()

        # dacă tot nu avem (user a închis loginul), închidem
        if not self.cfg.get("email"):
            self.after(50, self.destroy)
            return

        self.create_widgets()

    def create_widgets(self):
        pad = 10

        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, padx=pad, pady=pad)

        # Group URLs
        ttk.Label(frm, text="Group URLs (unul pe linie)").grid(row=0, column=0, sticky="w")
        self.urls_txt = tk.Text(frm, height=8)
        self.urls_txt.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(0,pad))
        frm.rowconfigure(1, weight=1)
        frm.columnconfigure(0, weight=1)

        if self.cfg.get("group_urls"):
            self.urls_txt.insert("1.0", "\n".join(self.cfg["group_urls"]))

        # Images folder
        ttk.Label(frm, text="Images folder").grid(row=2, column=0, sticky="w")
        self.images_var = tk.StringVar(value=self.cfg.get("images_folder",""))
        ttk.Entry(frm, textvariable=self.images_var).grid(row=2, column=1, sticky="ew")
        ttk.Button(frm, text="Browse", command=self.pick_folder).grid(row=2, column=2, padx=(5,0), sticky="w")
        frm.columnconfigure(1, weight=1)

        # Post text
        ttk.Label(frm, text="Post text").grid(row=3, column=0, sticky="w", pady=(pad,0))
        self.text_txt = tk.Text(frm, height=10)
        self.text_txt.grid(row=4, column=0, columnspan=3, sticky="nsew")
        frm.rowconfigure(4, weight=1)

        if self.cfg.get("post_text"):
            self.text_txt.insert("1.0", self.cfg["post_text"])

        # Delay
        ttk.Label(frm, text="Delay (sec)").grid(row=5, column=0, sticky="w", pady=(pad,0))
        self.delay_var = tk.IntVar(value=int(self.cfg.get("delay_sec",120)))
        ttk.Spinbox(frm, from_=30, to=3600, textvariable=self.delay_var, width=8).grid(row=5, column=1, sticky="w")

        # Buttons
        btns = ttk.Frame(frm)
        btns.grid(row=6, column=0, columnspan=3, pady=(pad,0), sticky="e")
        ttk.Button(btns, text="Preview", command=self.preview).pack(side="left", padx=5)
        ttk.Button(btns, text="Save", command=self.save_local).pack(side="left", padx=5)
        ttk.Button(btns, text="Run", command=self.run_script).pack(side="left", padx=5)

        self.status = ttk.Label(frm, text="")
        self.status.grid(row=7, column=0, columnspan=3, sticky="w", pady=(pad,0))

    def pick_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.images_var.set(path)

    def preview(self):
        urls = self._get_urls()
        imgs = self._get_images()
        text = self.text_txt.get("1.0", "end").strip()
        messagebox.showinfo(APP_NAME, f"{len(urls)} grupuri\n{len(imgs)} imagini\n\n{text[:200]}...")

    def save_local(self):
        self.cfg["group_urls"] = self._get_urls()
        self.cfg["images_folder"] = self.images_var.get().strip()
        self.cfg["post_text"] = self.text_txt.get("1.0","end").strip()
        self.cfg["delay_sec"] = int(self.delay_var.get())
        save_cfg(self.cfg)
        self.status.config(text="Config salvat.")
        logging.info("Config saved")

    def run_script(self):
        # aici vei apela funcțiile tale de automatisare (Selenium/Facebook poster)
        # momentan doar validăm licența înainte de execuție
        try:
            resp = check_license(self.cfg["email"], "FP-DEVICE-1")
            if resp.get("status") != "ok":
                messagebox.showerror(APP_NAME, "Licență invalidă sau expirată.")
                return
        except Exception as e:
            logging.exception("check failed")
            messagebox.showerror(APP_NAME, f"Eroare verificare licență:\n{e}")
            return

        urls = self._get_urls()
        imgs = self._get_images()
        text = self.text_txt.get("1.0","end").strip()
        delay = int(self.delay_var.get())

        if not urls:
            messagebox.showwarning(APP_NAME, "Nu ai introdus niciun grup.")
            return
        if not imgs:
            messagebox.showwarning(APP_NAME, "Nu există imagini în folder.")
            return

        # TODO: integrează aici rutina ta de postare (selenium)
        # Deocamdată simulăm:
        self.status.config(text="Rulează… (simulat)")
        self.update_idletasks()
        try:
            for i, g in enumerate(urls, 1):
                logging.info(f"[SIM] Post to {g} cu {len(imgs)} imagini (delay {delay}s)")
                self.status.config(text=f"[{i}/{len(urls)}] Post la: {g}")
                self.update()
                time.sleep(1)  # scurt în loc de delay real
            self.status.config(text="Gata (simulat)")
            messagebox.showinfo(APP_NAME, "Postare simulată terminată.")
        except Exception as e:
            logging.exception("run error")
            messagebox.showerror(APP_NAME, f"Eroare la rulare:\n{e}")

    def _get_urls(self):
        raw = self.urls_txt.get("1.0","end").strip()
        return [u.strip() for u in raw.splitlines() if u.strip()]

    def _get_images(self):
        folder = self.images_var.get().strip()
        if not folder or not os.path.isdir(folder):
            return []
        exts = {".jpg",".jpeg",".png",".webp",".gif"}
        files = [os.path.join(folder,f) for f in os.listdir(folder) if os.path.splitext(f)[1].lower() in exts]
        return files

if __name__ == "__main__":
    app = MainApp()
    if app:
        app.mainloop()
