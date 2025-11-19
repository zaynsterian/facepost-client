import os
import json
import uuid
import requests
import tkinter as tk
from tkinter import messagebox, filedialog
from pathlib import Path
import webbrowser
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

# ================== CONFIG BAZĂ ==================

API_URL = "https://facepost.onrender.com"  # URL-ul serverului de licențe
APP_NAME = "Facepost"
APP_VERSION = "1.0.0"  # versiunea clientului (o vei incrementa când updatezi EXE-ul)

# folder intern Facepost (pt config + device_id + profil Chrome)
def get_app_dir() -> Path:
    base = os.getenv("APPDATA")  # pe Windows
    if not base:
        base = str(Path.home())
    app_dir = Path(base) / "Facepost"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


# ================== DEVICE FINGERPRINT ==================

def get_device_fingerprint() -> str:
    """
    Generăm și persistăm un ID de device local (se salvează în AppData\Facepost\device_id.txt)
    astfel încât același PC să fie recunoscut de server.
    """
    app_dir = get_app_dir()
    f = app_dir / "device_id.txt"
    if f.exists():
        return f.read_text(encoding="utf-8").strip()

    # dacă nu există, creăm unul nou
    device_id = uuid.uuid4().hex
    f.write_text(device_id, encoding="utf-8")
    return device_id


# ================== PROFIL CHROME & DRIVER ==================

def get_chrome_profile_dir() -> str:
    """
    Returnează folderul în care Facepost își ține profilul de Chrome (cookie-uri, sesiuni).
    Aici rămâne login-ul în Facebook.
    """
    app_dir = get_app_dir()
    chrome_profile = app_dir / "chrome_profile"
    chrome_profile.mkdir(parents=True, exist_ok=True)
    return str(chrome_profile)


def create_driver(headless: bool = False) -> webdriver.Chrome:
    """
    Creează un Chrome WebDriver care folosește profilul dedicat Facepost.
    Dacă headless=True, pornește în mod „invizibil”.
    """
    options = Options()

    # profilul dedicat Facepost (rămâne logat în FB aici)
    profile_dir = get_chrome_profile_dir()
    options.add_argument(f"--user-data-dir={profile_dir}")

    if headless:
        options.add_argument("--headless=new")

    options.add_argument("--disable-notifications")

    # Dacă folosești un chromedriver.exe local, poți seta aici:
    # from selenium.webdriver.chrome.service import Service
    # service = Service("chromedriver.exe")
    # return webdriver.Chrome(service=service, options=options)

    driver = webdriver.Chrome(options=options)
    return driver


# ================== CONFIG LOCAL (EMAIL, SCHEDULER) ==================

CONFIG_FILE = get_app_dir() / "config.json"


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.load(open(CONFIG_FILE, "r", encoding="utf-8"))
        except:
            return {}
    return {}


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ================== API LICENȚE (CHECK / BIND) ==================

def api_post(path: str, payload: dict) -> dict | None:
    url = f"{API_URL.rstrip('/')}{path}"
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code == 200:
            return r.json()
        else:
            try:
                return r.json()
            except:
                return {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def bind_license(email: str, fingerprint: str) -> dict:
    """Apel la /bind (leagă device-ul de licență)."""
    return api_post("/bind", {"email": email, "fingerprint": fingerprint}) or {}


def check_license(email: str, fingerprint: str) -> dict:
    """Apel la /check (verifică status licență)."""
    return api_post("/check", {"email": email, "fingerprint": fingerprint}) or {}


# ================== UPDATE CHECK ==================

def check_for_updates_dialog(root: tk.Tk):
    """
    Verifică /updates/client.json pe server.
    Format așteptat:
    {
      "version": "1.0.1",
      "url": "https://.../FacepostSetup.exe",
      "notes": "Ce s-a schimbat..."
    }
    """
    url = f"{API_URL.rstrip('/')}/updates/client.json"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            messagebox.showinfo("Update", "Nu s-a putut verifica versiunea de pe server.")
            return
        data = r.json()
    except Exception as e:
        messagebox.showinfo("Update", f"Nu s-a putut verifica update-ul:\n{e}")
        return

    server_ver = str(data.get("version", "")).strip()
    download_url = data.get("url") or data.get("download_url") or ""
    notes = data.get("notes", "")

    if not server_ver:
        messagebox.showinfo("Update", "Răspuns invalid de la server (fără versiune).")
        return

    if server_ver == APP_VERSION:
        messagebox.showinfo("Update", f"Ai deja ultima versiune ({APP_VERSION}).")
        return

    msg = f"A apărut o versiune nouă: {server_ver}\nVersiunea ta: {APP_VERSION}"
    if notes:
        msg += f"\n\nNoutăți:\n{notes}"

    if download_url:
        msg += "\n\nVrei să deschizi pagina de download?"
        if messagebox.askyesno("Update disponibil", msg):
            webbrowser.open(download_url)
    else:
        messagebox.showinfo("Update disponibil", msg)


# ================== TKINTER: CONFIG FACEBOOK LOGIN ==================

def configure_facebook_login(root: tk.Tk | None = None):
    """
    Deschide un Chrome cu profilul Facepost și lasă userul să se logheze în Facebook.
    După ce a terminat loginul, apasă pe butonul 'Gata, sunt logat'.
    """
    try:
        driver = create_driver(headless=False)
    except Exception as e:
        messagebox.showerror("Eroare Chrome", f"Nu pot porni Chrome:\n{e}")
        return

    try:
        driver.get("https://www.facebook.com/")
    except Exception as e:
        messagebox.showerror("Eroare Facebook", f"Nu pot accesa Facebook:\n{e}")
        driver.quit()
        return

    win = tk.Toplevel(root)
    win.title("Configurare Facebook")
    win.geometry("460x220")

    lbl = tk.Label(
        win,
        text=(
            "1. În fereastra de Chrome care s-a deschis,\n"
            "   loghează-te în contul tău de Facebook.\n\n"
            "2. După ce ai terminat și vezi că ești logat,\n"
            "   apasă pe butonul de mai jos.\n\n"
            "Atenție: după acest pas, Facepost va folosi această sesiune\n"
            "pentru a posta automat în grupurile selectate."
        ),
        justify="left"
    )
    lbl.pack(padx=20, pady=15)

    def on_done():
        try:
            driver.quit()
        except:
            pass
        win.destroy()
        messagebox.showinfo(
            "Gata!",
            "Login-ul în Facebook a fost configurat.\n"
            "De acum înainte, Facepost va posta folosind această sesiune."
        )

    btn = tk.Button(win, text="Gata, sunt logat", command=on_done)
    btn.pack(pady=10)

    def on_close():
        try:
            driver.quit()
        except:
            pass
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", on_close)


# ================== LOGICĂ POSTARE (SCHELET) ==================

def run_posting(post_text: str, groups: list[str], images: list[str] | None = None):
    """
    Aici folosești driver-ul cu profilul Facepost ca să postezi în grupuri.
    `post_text` = textul introdus de user
    `groups` = lista de URL-uri de grupuri Facebook
    `images` = lista de căi de fișiere imagini (dacă userul a selectat)
    """
    images = images or []

    try:
        driver = create_driver(headless=False)
    except Exception as e:
        messagebox.showerror("Eroare Chrome", f"Nu pot porni Chrome:\n{e}")
        return

    try:
        if not groups:
            messagebox.showwarning("Info", "Nu ai definit niciun URL de grup.")
            return

        print("[DEBUG] Text de postat:")
        print(post_text)
        print("[DEBUG] Imagini selectate:")
        for img in images:
            print("  -", img)

        for url in groups:
            driver.get(url)
            # TODO: Aici pui logica efectivă de postare:
            #   - găsești zona de „Creează postare”
            #   - inserezi `post_text`
            #   - atașezi imaginile din `images`
            #   - apeși Publish
            print(f"[DEBUG] (simulare) postez în grup: {url}")
            # time.sleep(X) etc.

        messagebox.showinfo("Succes", "Postările au fost procesate (vezi console log pentru debug).")
    finally:
        try:
            driver.quit()
        except:
            pass


# ================== UI PRINCIPAL TKINTER ==================

class FacepostApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(APP_NAME)
        root.geometry("720x650")

        self.config = load_config()
        self.device_id = get_device_fingerprint()

        # pentru scheduler
        self.last_run_date = None  # ca să nu ruleze de mai multe ori în aceeași zi
        self.image_paths: list[str] = []  # imagini selectate pentru postare

        # === Secțiune licență ===
        frame_lic = tk.LabelFrame(root, text=" Licență ", padx=10, pady=10)
        frame_lic.pack(fill="x", padx=10, pady=10)

        tk.Label(frame_lic, text="Email utilizator:").grid(row=0, column=0, sticky="w")
        self.email_var = tk.StringVar(value=self.config.get("email", ""))
        tk.Entry(frame_lic, textvariable=self.email_var, width=40).grid(row=0, column=1, sticky="w")

        self.lic_status_label = tk.Label(frame_lic, text="Status: necunoscut", fg="gray")
        self.lic_status_label.grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))

        btn_check = tk.Button(frame_lic, text="Verifică licența", command=self.on_check_license)
        btn_check.grid(row=0, column=2, padx=10)

        btn_update = tk.Button(frame_lic, text="Verifică update", command=lambda: check_for_updates_dialog(self.root))
        btn_update.grid(row=0, column=3, padx=5)

        # === Secțiune Facebook login ===
        frame_fb = tk.LabelFrame(root, text=" Facebook ", padx=10, pady=10)
        frame_fb.pack(fill="x", padx=10, pady=5)

        tk.Label(frame_fb, text="1. Configurează login-ul în Facebook:").grid(row=0, column=0, sticky="w")
        btn_fb = tk.Button(
            frame_fb,
            text="Configurează login Facebook",
            command=lambda: configure_facebook_login(self.root)
        )
        btn_fb.grid(row=0, column=1, padx=10, pady=5, sticky="w")

        # === Secțiune imagini ===
        frame_img = tk.LabelFrame(root, text=" Imagini pentru postare ", padx=10, pady=10)
        frame_img.pack(fill="x", padx=10, pady=5)

        btn_sel_img = tk.Button(frame_img, text="Alege imagini...", command=self.on_select_images)
        btn_sel_img.grid(row=0, column=0, sticky="w")

        self.images_label = tk.Label(frame_img, text="0 imagini selectate", fg="gray")
        self.images_label.grid(row=0, column=1, sticky="w", padx=10)

        # === Secțiune conținut postare ===
        frame_post = tk.LabelFrame(root, text=" Conținut postare ", padx=10, pady=10)
        frame_post.pack(fill="both", expand=True, padx=10, pady=10)

        tk.Label(frame_post, text="Text postare:").pack(anchor="w")
        self.post_text = tk.Text(frame_post, height=6)
        self.post_text.pack(fill="x", pady=5)

        tk.Label(frame_post, text="URL-uri grupuri (unul pe linie):").pack(anchor="w", pady=(10, 0))
        self.groups_text = tk.Text(frame_post, height=6)
        self.groups_text.pack(fill="both", expand=True, pady=5)

        # === Secțiune scheduler ===
        frame_sched = tk.LabelFrame(root, text=" Programare zilnică ", padx=10, pady=10)
        frame_sched.pack(fill="x", padx=10, pady=5)

        tk.Label(frame_sched, text="Ora zilnică (HH:MM):").grid(row=0, column=0, sticky="w")
        self.schedule_time_var = tk.StringVar(value=self.config.get("schedule_time", "09:00"))
        tk.Entry(frame_sched, textvariable=self.schedule_time_var, width=8).grid(row=0, column=1, sticky="w", padx=(5, 15))

        self.schedule_enabled_var = tk.BooleanVar(value=bool(self.config.get("schedule_enabled", False)))
        chk = tk.Checkbutton(frame_sched, text="Activează programarea zilnică", variable=self.schedule_enabled_var,
                             command=self.on_scheduler_changed)
        chk.grid(row=0, column=2, sticky="w")

        tk.Label(
            frame_sched,
            text="Programarea funcționează doar cât timp aplicația este deschisă.\n"
                 "La ora setată, Facepost va încerca să posteze cu textul, grupurile și imaginile de mai sus.",
            fg="gray", justify="left"
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # === Butoane jos ===
        frame_bottom = tk.Frame(root)
        frame_bottom.pack(fill="x", padx=10, pady=10)

        btn_preview = tk.Button(frame_bottom, text="Preview postare", command=self.on_preview_posting)
        btn_preview.pack(side="left")

        btn_run = tk.Button(frame_bottom, text="Pornește postarea acum", command=self.on_run_posting)
        btn_run.pack(side="right")

        # pornește loop-ul schedulerului
        root.after(5000, self.scheduler_tick)

    # ------- Helpers config -------

    def save_config_fields(self):
        self.config["email"] = self.email_var.get().strip().lower()
        self.config["schedule_time"] = self.schedule_time_var.get().strip()
        self.config["schedule_enabled"] = bool(self.schedule_enabled_var.get())
        save_config(self.config)

    # ------- Callbacks UI -------

    def on_select_images(self):
        files = filedialog.askopenfilenames(
            title="Selectează imagini",
            filetypes=[
                ("Imagini", "*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.gif"),
                ("Toate fișierele", "*.*"),
            ]
        )
        if not files:
            return
        self.image_paths = list(files)
        self.images_label.config(text=f"{len(self.image_paths)} imagini selectate", fg="black")

    def on_check_license(self):
        email = self.email_var.get().strip().lower()
        if not email:
            messagebox.showwarning("Atenție", "Te rog introdu emailul.")
            return

        # salvăm în config
        self.save_config_fields()

        # întâi bind (în caz că nu e legat), apoi check
        bind_res = bind_license(email, self.device_id)
        if bind_res.get("error"):
            self.lic_status_label.config(
                text=f"Status licență: eroare bind ({bind_res['error']})", fg="red"
            )
            return

        check_res = check_license(email, self.device_id)
        if check_res.get("error"):
            self.lic_status_label.config(
                text=f"Status licență: eroare check ({check_res['error']})", fg="red"
            )
            return

        status = check_res.get("status") or check_res.get("status_raw")
        expires = check_res.get("expires_at")

        if status == "ok":
            msg = f"Status licență: ACTIVĂ (expiră la {expires})" if expires else "Status licență: ACTIVĂ"
            self.lic_status_label.config(text=msg, fg="green")
        elif status == "expired":
            self.lic_status_label.config(
                text=f"Status licență: EXPIRATĂ (expiră la {expires})", fg="red"
            )
        elif status == "inactive":
            self.lic_status_label.config(text="Status licență: SUSPENDATĂ/INACTIVĂ", fg="red")
        elif status == "unbound":
            self.lic_status_label.config(text="Status licență: UNBOUND (nu e legat device-ul)", fg="orange")
        else:
            self.lic_status_label.config(
                text=f"Status licență: NECUNOSCUT ({status})", fg="orange"
            )

    def on_preview_posting(self):
        """Arată un mic preview (text + grupuri + imagini) într-o fereastră separată."""
        text = self.post_text.get("1.0", "end").strip()
        groups_raw = self.groups_text.get("1.0", "end").strip()

        if not text and not groups_raw and not self.image_paths:
            messagebox.showinfo("Preview", "Nu ai completat nimic pentru preview.")
            return

        win = tk.Toplevel(self.root)
        win.title("Preview postare")
        win.geometry("650x500")

        lbl1 = tk.Label(win, text="Text postare:", font=("Segoe UI", 10, "bold"))
        lbl1.pack(anchor="w", padx=10, pady=(10, 0))

        txt1 = tk.Text(win, height=8)
        txt1.pack(fill="x", padx=10, pady=5)
        txt1.insert("1.0", text)
        txt1.config(state="disabled")

        lbl2 = tk.Label(win, text="URL-uri grupuri:", font=("Segoe UI", 10, "bold"))
        lbl2.pack(anchor="w", padx=10, pady=(10, 0))

        txt2 = tk.Text(win, height=8)
        txt2.pack(fill="x", padx=10, pady=5)
        txt2.insert("1.0", groups_raw)
        txt2.config(state="disabled")

        lbl3 = tk.Label(win, text="Imagini selectate:", font=("Segoe UI", 10, "bold"))
        lbl3.pack(anchor="w", padx=10, pady=(10, 0))

        txt3 = tk.Text(win, height=6)
        txt3.pack(fill="both", expand=True, padx=10, pady=5)
        if self.image_paths:
            for p in self.image_paths:
                txt3.insert("end", p + "\n")
        else:
            txt3.insert("1.0", "(nici o imagine selectată)")
        txt3.config(state="disabled")

    def on_run_posting(self, from_scheduler: bool = False):
        email = self.email_var.get().strip().lower()
        if not email:
            if not from_scheduler:
                messagebox.showwarning("Atenție", "Te rog introdu emailul și verifică licența înainte.")
            return

        # verificare licență înainte de postare
        check_res = check_license(email, self.device_id)
        if check_res.get("status") not in ("ok",):
            if not from_scheduler:
                messagebox.showerror(
                    "Licență invalidă",
                    "Licența nu este activă. Te rog verifică licența înainte de postare."
                )
            return

        text = self.post_text.get("1.0", "end").strip()
        if not text:
            if not from_scheduler:
                messagebox.showwarning("Atenție", "Te rog introdu textul postării.")
            return

        groups_raw = self.groups_text.get("1.0", "end").strip()
        groups = [line.strip() for line in groups_raw.splitlines() if line.strip()]
        if not groups:
            if not from_scheduler:
                messagebox.showwarning("Atenție", "Te rog introdu cel puțin un URL de grup.")
            return

        # rulăm postarea (cu imagini)
        run_posting(text, groups, self.image_paths)

    def on_scheduler_changed(self):
        self.save_config_fields()

    # ------- Scheduler loop -------

    def scheduler_tick(self):
        """
        Se apelează periodic (la ~60s). Dacă programarea este activă și
        ora curentă = ora setată și nu am mai postat azi, lansează on_run_posting(from_scheduler=True).
        """
        try:
            if self.schedule_enabled_var.get():
                schedule_time = (self.schedule_time_var.get() or "").strip()
                try:
                    hh_str, mm_str = schedule_time.split(":")
                    hh = int(hh_str)
                    mm = int(mm_str)
                    now = datetime.now()
                    target_today = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                except Exception:
                    # oră invalidă, nu facem nimic
                    pass
                else:
                    today = datetime.now().date()
                    # dacă e după ora setată și nu am rulat încă azi
                    if now >= target_today and self.last_run_date != today:
                        self.last_run_date = today
                        self.on_run_posting(from_scheduler=True)
        finally:
            # replanificăm verificarea peste 60 de secunde
            self.root.after(60_000, self.scheduler_tick)


# ================== ENTRY POINT ==================

def main():
    root = tk.Tk()
    app = FacepostApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
