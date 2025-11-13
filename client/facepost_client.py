Python 3.13.9 (tags/v3.13.9:8183fa5, Oct 14 2025, 14:09:13) [MSC v.1944 64 bit (AMD64)] on win32
Enter "help" below or click "Help" above for more information.
import os, re, json, time, hashlib, uuid, platform, sys, threading, shutil, subprocess, tempfile
from pathlib import Path
from datetime import datetime, date
import requests
from packaging import version as pver
import PySimpleGUI as sg

from PIL import Image
from io import BytesIO

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

APP_NAME = "Facepost"
VERSION  = "1.1.0"   # << actualizeaza la release

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
LOG_PATH    = HERE / "facepost_log.txt"

# -------------------- Utils --------------------
def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        LOG_PATH.write_text(LOG_PATH.read_text(encoding="utf-8") + line + "\n", encoding="utf-8")
    except FileNotFoundError:
        LOG_PATH.write_text(line + "\n", encoding="utf-8")

def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {
        "server_base": "https://facepost.onrender.com",
        "email": "",
        "fingerprint_override": "",
        "default_delay_sec": 120,
        "schedule_enabled": False,
        "schedule_time": "09:00",
        "auto_update": True,
        "version_endpoint": "https://facepost.onrender.com/client-version"
    }

def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

def windows_machine_guid():
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
        val, _ = winreg.QueryValueEx(k, "MachineGuid")
        return val
    except Exception:
        return ""

def get_fingerprint(cfg: dict) -> str:
    if cfg.get("fingerprint_override"):
        return cfg["fingerprint_override"]
    base = [platform.system(), platform.release(), platform.version(), platform.machine(), platform.node().lower()]
    if os.name == "nt":
        mg = windows_machine_guid()
        if mg: base.append(mg)
    uid_path = HERE / ".fp_uid"
    if uid_path.exists():
        base.append(uid_path.read_text().strip())
    else:
        new_id = str(uuid.uuid4())
        uid_path.write_text(new_id)
        base.append(new_id)
    return hashlib.sha256("|".join(base).encode("utf-8")).hexdigest()[:32]

def post_json(url, payload, headers=None, timeout=25):
    h = {"Content-Type": "application/json"}
    if headers: h.update(headers)
    r = requests.post(url, json=payload, headers=h, timeout=timeout)
    r.raise_for_status()
    return r.json()

def bind_and_check(server_base, email, fingerprint):
    post_json(f"{server_base}/bind",  {"email": email, "fingerprint": fingerprint})
    return post_json(f"{server_base}/check", {"email": email, "fingerprint": fingerprint})

# -------------------- Auto Update --------------------
def check_update(cfg, ui_log):
    if not cfg.get("auto_update", True):
        return
    endpoint = cfg.get("version_endpoint")
    if not endpoint: return
    try:
        resp = requests.get(endpoint, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        latest = data.get("latest")
        dl_url = data.get("download_url")
        if latest and dl_url and pver.parse(latest) > pver.parse(VERSION):
            ui_log(f"⬆️ Versiune nouă: {latest}. Descarc update...")
            tmp = tempfile.gettempdir()
            new_exe = Path(tmp) / f"Facepost_{latest}.exe"
            with requests.get(dl_url, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(new_exe, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk: f.write(chunk)
            ui_log("✅ Descărcat. Înlocuiesc aplicația și repornesc...")

            cur_exe = Path(sys.argv[0]).resolve()
            bat = Path(tmp) / "facepost_update.bat"
            bat.write_text(f"""@echo off
ping 127.0.0.1 -n 2 >nul
copy /y "{new_exe}" "{cur_exe}"
start "" "{cur_exe}"
del "{new_exe}"
del "%~f0"
""", encoding="utf-8")
            subprocess.Popen(['cmd', '/c', str(bat)], creationflags=subprocess.CREATE_NO_WINDOW)
            sys.exit(0)
    except Exception as e:
        ui_log(f"⚠️ Update check failed: {e}")

# -------------------- Selenium --------------------
def build_driver(user_data_dir: str):
    chrome_opts = Options()
    chrome_opts.add_argument("--start-maximized")
    chrome_opts.add_argument(f"--user-data-dir={user_data_dir}")
    chrome_opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_opts.add_experimental_option('useAutomationExtension', False)
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_opts)
    driver.set_page_load_timeout(60)
    return driver

def post_in_group(driver, group_url, post_text, images_folder, timeout=35):
    wait = WebDriverWait(driver, timeout)
    driver.get(group_url)
    time.sleep(3)

    # deschide composer – fallback pe diverse label-uri
    try:
        composer = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//div[@role='article']//div[@role='button' and @aria-label] | //div[@role='button'][.//span[contains(.,'Post') or contains(.,'Scrie') or contains(.,'Creează')]]")
            )
        )
        driver.execute_script("arguments[0].click();", composer)
        time.sleep(2)
    except TimeoutException:
        pass

    editor = wait.until(EC.presence_of_element_located((By.XPATH, "//div[@contenteditable='true' and @role='textbox']")))
    editor.click(); editor.send_keys(post_text); time.sleep(0.6)

    # attach imagini
    if images_folder and Path(images_folder).exists():
        files = [str(p) for p in sorted(Path(images_folder).glob("*.*")) if p.suffix.lower() in [".jpg",".jpeg",".png",".webp"]]
        if files:
            try:
                file_input = driver.find_element(By.XPATH, "//input[@type='file' and @accept]")
            except NoSuchElementException:
                file_input = driver.find_element(By.XPATH, "//input[@type='file']")
            file_input.send_keys("\n".join(files))
            time.sleep(2)

    # Post/Publică
    labels = ["Post", "Publică", "Publish", "Enviar", "Invia"]
    for label in labels:
        try:
            btn = driver.find_element(By.XPATH, f"//div[@role='button' and .//span[contains(.,'{label}')]]")
            driver.execute_script("arguments[0].click();", btn)
            break
        except NoSuchElementException:
            continue
    else:
        raise RuntimeError("Nu am găsit butonul Post/Publică.")

    time.sleep(3)

# -------------------- Preview --------------------
def make_thumbnail(img_path: Path, max_w=360, max_h=240):
    try:
        im = Image.open(img_path).convert("RGB")
        im.thumbnail((max_w, max_h))
        bio = BytesIO()
        im.save(bio, format="PNG")
        return bio.getvalue()
    except Exception:
        return None

def show_preview(text, images_folder):
    thumbs = []
    if images_folder and Path(images_folder).exists():
        for p in sorted(Path(images_folder).glob("*.*")):
            if p.suffix.lower() in [".jpg",".jpeg",".png",".webp"]:
                data = make_thumbnail(p)
                if data: thumbs.append((p.name, data))

    layout = [
        [sg.Text("Preview post", font=("Segoe UI", 14, "bold"))],
        [sg.Frame("Text", [[sg.Multiline(text, size=(80,10), disabled=True)]], expand_x=True, expand_y=False)],
        [sg.Text("Imagini:")],
        [sg.Column([[sg.Text(n)], [sg.Image(d)] ] for n,d in thumbs, scrollable=True, size=(600,320))] if thumbs else [sg.Text("Fără imagini")],
        [sg.Button("Close")]
    ]
    sg.Window("Preview", layout, modal=True).read(close=True)

# -------------------- Scheduler --------------------
class DailyScheduler:
    def __init__(self, cfg, run_callable, ui_log):
        self.cfg = cfg
        self.run_callable = run_callable
        self.ui_log = ui_log
        self._stop = threading.Event()
        self._last_run_day = None
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                if self.cfg.get("schedule_enabled"):
                    hhmm = self.cfg.get("schedule_time","09:00")
                    now = datetime.now()
                    if now.strftime("%H:%M") == hhmm and self._last_run_day != date.today():
                        self.ui_log(f"🕘 Scheduler: rulez jobul zilnic ({hhmm})")
                        self._last_run_day = date.today()
                        threading.Thread(target=self.run_callable, daemon=True).start()
            except Exception as e:
                self.ui_log(f"⚠️ Scheduler error: {e}")
            time.sleep(30)

# -------------------- Onboarding / Login --------------------
def activate_flow(cfg, ui_log=lambda s: None):
    sg.theme("DarkBlue3")
    layout = [
        [sg.Text("Activate Facepost", font=("Segoe UI", 16, "bold"))],
        [sg.Text("Server base", size=(12,1)), sg.Input(cfg.get("server_base","https://facepost.onrender.com"), key="-S-BASE-", expand_x=True)],
        [sg.Text("Email", size=(12,1)), sg.Input(cfg.get("email",""), key="-S-EMAIL-", expand_x=True)],
        [sg.Text("Activation code", size=(12,1)), sg.Input("", key="-S-CODE-", password_char="•", expand_x=True)],
        [sg.Push(), sg.Button("Activate", bind_return_key=True), sg.Button("Exit")]
    ]
    win = sg.Window("Facepost – Activate", layout, modal=True)

    email = None
    while True:
        ev, vals = win.read()
        if ev in (sg.WINDOW_CLOSED, "Exit"):
            win.close()
            return None, None
        if ev == "Activate":
            server = vals["-S-BASE-"].strip()
            email  = vals["-S-EMAIL-"].strip().lower()

            if not server or not email:
                sg.popup_error("Completează Server și Email.")
                continue

            try:
                finger = get_fingerprint(cfg)
                post_json(f"{server}/bind", {"email": email, "fingerprint": finger})
                chk = post_json(f"{server}/check", {"email": email, "fingerprint": finger})
            except requests.HTTPError as e:
                sg.popup_error(f"Eroare server:\n{e.response.text}")
                continue
            except Exception as e:
                sg.popup_error(f"Eroare rețea/licență:\n{e}")
                continue

            if chk.get("status") != "ok":
                sg.popup_error(f"Licență invalidă sau inactivă (status: {chk.get('status')}).")
                continue

            cfg["server_base"] = server
            cfg["email"] = email
            save_config(cfg)
            sg.popup_ok("Activare reușită. Bine ai venit!")
            win.close()
            return cfg, email

def switch_account():
    cfg = load_config()
    cfg["email"] = ""
    save_config(cfg)
    sg.popup_ok("Contul a fost deconectat.\nLa următoarea pornire vei vedea ecranul de activare.")

# -------------------- UI & Orchestrare --------------------
def main():
    cfg = load_config()
    if not cfg.get("email"):
        cfg, email = activate_flow(cfg)
        if not email:
            return

    fingerprint = get_fingerprint(cfg)
    sg.theme("DarkBlue3")

    menu_def = [["Account", ["Switch account", "---", "Exit"]]]

    layout = [
        [sg.Menu(menu_def)],
        [sg.Text(f"{APP_NAME} v{VERSION}", font=("Segoe UI", 14, "bold")), sg.Push(),
         sg.Button("Preview"), sg.Button("Save Config"), sg.Button("Run"), sg.Button("Exit")],
        [sg.Text("Server base:", size=(16,1)), sg.Input(cfg.get("server_base",""), key="-SERVER-", expand_x=True, disabled=True)],
        [sg.Text("Email licență:", size=(16,1)), sg.Input(cfg.get("email",""), key="-EMAIL-", expand_x=True, disabled=True)],
        [sg.Text("Chrome user-data-dir:", size=(16,1)), sg.Input(key="-PROFILE-", expand_x=True), sg.FolderBrowse("Select")],
        [sg.Text("Group URLs (unul pe linie):")],
        [sg.Multiline(size=(90,8), key="-GROUPS-")],
        [sg.Text("Post Text:")],
        [sg.Multiline(size=(90,8), key="-TEXT-")],
        [sg.Text("Folder poze:", size=(16,1)), sg.Input(key="-IMGFOLDER-", expand_x=True), sg.FolderBrowse("Select")],
        [sg.Text("Delay (sec):", size=(16,1)), sg.Input(str(cfg.get("default_delay_sec",120)), key="-DELAY-", size=(8,1)),
         sg.Push(),
         sg.Checkbox("Enable scheduler", key="-SCH-EN-", default=cfg.get("schedule_enabled", False)),
         sg.Text("Ora (HH:MM):"), sg.Input(cfg.get("schedule_time","09:00"), key="-SCH-T-", size=(8,1))],
        [sg.Checkbox("Auto-update", key="-AUTUP-", default=cfg.get("auto_update", True)),
         sg.Text("Version endpoint:"), sg.Input(cfg.get("version_endpoint",""), key="-VER-ENDP-", expand_x=True)],
        [sg.Text("Status / Log:")],
        [sg.Multiline(size=(90,12), key="-LOG-", autoscroll=True, disabled=True)]
    ]
    win = sg.Window(APP_NAME, layout, finalize=True)

    def ui_log(s):
        log(s)
        win["-LOG-"].update(value=(win["-LOG-"].get() + s + "\n"))

    threading.Thread(target=lambda: check_update(cfg, ui_log), daemon=True).start()

    def do_run():
        server = cfg.get("server_base","").strip()
        email  = cfg.get("email","").strip().lower()
        profile= values["-PROFILE-"].strip()
        groups_raw = values["-GROUPS-"]
        post_text  = values["-TEXT-"]
        img_folder = values["-IMGFOLDER-"].strip()
        try:
            delay = int(values["-DELAY-"])
        except:
            delay = 120

        if not (server and email and profile and groups_raw and post_text):
            ui_log("❗ Completează profil Chrome, grupuri și text.")
            return

        groups = [g.strip() for g in groups_raw.splitlines() if g.strip()]

        ui_log(f"🔐 Verific licență pentru {email} ...")
        try:
            check = bind_and_check(server, email, fingerprint)
        except requests.HTTPError as e:
            ui_log(f"❌ Eroare la server: {e.response.text}")
            return
        except Exception as e:
            ui_log(f"❌ Eroare rețea/licență: {e}")
            return

        if check.get("status") != "ok":
            ui_log(f"❌ Licență nevalidă (status: {check.get('status')}).")
            return

        ui_log("🚀 Pornez Chrome (să fie deja logat Facebook în profilul ales)...")
        try:
            driver = build_driver(profile)
        except WebDriverException as e:
            ui_log(f"❌ Chrome/Driver error: {e}")
            return

        success = 0
        for i, url in enumerate(groups, 1):
            try:
                ui_log(f"[{i}/{len(groups)}] Post în {url} ...")
                post_in_group(driver, url, post_text, img_folder)
                success += 1
            except Exception as e:
                ui_log(f"⚠️ Eșec la {url}: {e}")
            if i < len(groups):
                ui_log(f"⏳ Pauză {delay}s ...")
                time.sleep(delay)
        try: driver.quit()
        except: pass
        ui_log(f"🏁 Finalizat. Reușite: {success}/{len(groups)}")
... 
...     scheduler = DailyScheduler(cfg, run_callable=do_run, ui_log=ui_log)
...     scheduler.start()
... 
...     while True:
...         ev, values = win.read(timeout=200)
...         if ev in (sg.WINDOW_CLOSED, "Exit"):
...             break
... 
...         if ev == "Switch account":
...             switch_account()
...             break
... 
...         if ev == "Save Config":
...             cfg["default_delay_sec"] = int(values["-DELAY-"]) if values["-DELAY-"].isdigit() else 120
...             cfg["schedule_enabled"]  = bool(values["-SCH-EN-"])
...             cfg["schedule_time"]     = values["-SCH-T-"].strip() or "09:00"
...             cfg["auto_update"]       = bool(values["-AUTUP-"])
...             cfg["version_endpoint"]  = values["-VER-ENDP-"].strip() or cfg.get("version_endpoint","")
...             save_config(cfg)
...             ui_log("💾 Config salvat.")
... 
...         if ev == "Preview":
...             show_preview(values["-TEXT-"], values["-IMGFOLDER-"])
... 
...         if ev == "Run":
...             threading.Thread(target=do_run, daemon=True).start()
... 
...     scheduler.stop()
...     win.close()
... 
... if __name__ == "__main__":
...     try:
...         main()
...     except Exception as e:
...         log(f"FATAL: {e}")
...         raise
