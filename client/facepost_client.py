# client/facepost_client.py
import os
import sys
import json
import time
import threading
from datetime import datetime, timedelta
import requests
import PySimpleGUI as sg

# =========================
# CONFIG SERVER ENDPOINTS
# =========================
SERVER_BASE    = "https://facepost.onrender.com"   # <- pune URL-ul tău Render
CHECK_URL      = f"{SERVER_BASE}/check"
BIND_URL       = f"{SERVER_BASE}/bind"
ISSUE_URL      = f"{SERVER_BASE}/issue"
SUSPEND_URL    = f"{SERVER_BASE}/suspend"
RENEW_URL      = f"{SERVER_BASE}/renew"
CLIENT_VER_URL = f"{SERVER_BASE}/client-version"
CLIENT_DL_URL  = f"{SERVER_BASE}/client-download"

APP_VERSION = "1.1.0"   # versiunea EXE curentă

CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "email": "",
    "license_key": "",
    "groups": "",
    "post_text": "",
    "images_folder": "",
    "daily_time": "10:00",        # HH:MM (24h)
    "device_fingerprint": "",     # se completează la primul bind
}

# =========================
# UTILS
# =========================
def load_config():
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG.copy())
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

def boxed(msg):
    return f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"

def show_error(msg):
    sg.popup_error(f"Facepost • Eroare\n\n{msg}")

def show_info(msg):
    sg.popup_ok(f"Facepost\n\n{msg}")

def get_exe_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

# =========================
# LICENSING
# =========================
def device_fingerprint():
    """O amprentă simplă a device-ului (poți îmbunătăți cu MAC, CPU id etc.)."""
    base = os.getenv("COMPUTERNAME", "WIN") + "_" + os.getenv("USERNAME", "USER")
    return f"FP-{abs(hash(base))}"

def server_check(email, fingerprint):
    try:
        payload = {"email": email, "fingerprint": fingerprint}
        r = requests.post(CHECK_URL, json=payload, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"status": "error", "error": str(e)}

def server_bind(email, fingerprint):
    try:
        payload = {"email": email, "fingerprint": fingerprint}
        r = requests.post(BIND_URL, json=payload, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"status": "error", "error": str(e)}

# =========================
# AUTO-UPDATE
# =========================
def get_latest_version():
    try:
        r = requests.get(CLIENT_VER_URL, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def download_new_exe(download_url, target_path):
    try:
        with requests.get(download_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(target_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)
        return True
    except Exception:
        return False

def try_update_if_needed(window=None):
    """Verifică versiunea și, dacă e mai nouă, descarcă și înlocuiește EXE-ul."""
    data = get_latest_version()
    if not data or "version" not in data:
        return False

    latest = str(data.get("version", "")).strip()
    if latest and latest != APP_VERSION:
        # cerem link-ul de download
        try:
            r = requests.get(CLIENT_DL_URL, timeout=10)
            r.raise_for_status()
            url = r.json().get("url")
        except Exception:
            url = None

        if url:
            exe_dir = get_exe_dir()
            new_path = os.path.join(exe_dir, "Facepost_new.exe")
            ok = download_new_exe(url, new_path)
            if ok:
                # pe Windows nu poți suprascrie exe-ul curent. Lansăm un updater
                # minimalist care închide aplicația și înlocuiește fișierul.
                updater_path = os.path.join(exe_dir, "updater.bat")
                current_exe = sys.executable if getattr(sys, "frozen", False) else None

                with open(updater_path, "w", encoding="utf-8") as f:
                    f.write(f"""@echo off
timeout /t 1 >nul
taskkill /f /pid {os.getpid()} >nul 2>&1
timeout /t 1 >nul
copy /y "{new_path}" "{current_exe}" >nul
del /f "{new_path}"
start "" "{current_exe}"
del "%~f0"
""")
                # anunțăm userul și rulăm updaterul
                if window:
                    window.write_event_value("-LOG-", boxed(f"Update disponibil: {latest}. Se aplică acum..."))
                os.startfile(updater_path)
                return True
    return False

# =========================
# POSTING DUMMY (Preview)
# =========================
def list_images(folder):
    if not folder or not os.path.isdir(folder):
        return []
    exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    files = [os.path.join(folder, f) for f in os.listdir(folder)]
    return [f for f in files if os.path.splitext(f)[-1].lower() in exts]

def perform_post(groups_csv, text, images_folder, log_cb):
    """
    Aici integrezi funcția ta reală de postare în grupuri.
    Deocamdată doar loghează ce ar face.
    """
    groups = [g.strip() for g in groups_csv.splitlines() if g.strip()]
    imgs = list_images(images_folder)

    if not groups:
        log_cb(boxed("Niciun grup definit."))
        return

    log_cb(boxed(f"Încep postarea în {len(groups)} grupuri..."))
    log_cb(boxed(f"Text: {text[:60]}{'...' if len(text)>60 else ''}"))
    log_cb(boxed(f"Imagini găsite: {len(imgs)}"))

    # >>> aici chemi selenium-ul tău de postare, cu delay-urile tale <<<
    for i, g in enumerate(groups, start=1):
        time.sleep(1)  # simulez un delay
        log_cb(boxed(f"[{i}/{len(groups)}] Postare în: {g} ... OK"))

    log_cb(boxed("Postare finalizată."))

# =========================
# SCHEDULER
# =========================
def seconds_until(target_hhmm):
    now = datetime.now()
    hour, minute = map(int, target_hhmm.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return int((target - now).total_seconds())

def start_daily_job(cfg, window):
    def runner():
        while True:
            wait_s = seconds_until(cfg["daily_time"])
            window.write_event_value("-LOG-", boxed(f"Următoarea postare la {cfg['daily_time']} (în ~{wait_s//60} min)"))
            time.sleep(wait_s)
            perform_post(cfg["groups"], cfg["post_text"], cfg["images_folder"], lambda m: window.write_event_value("-LOG-", m))
    th = threading.Thread(target=runner, daemon=True)
    th.start()
    return th

# =========================
# UI – LOGIN & MAIN
# =========================
def login_window():
    layout = [
        [sg.Text("Email"), sg.Input(key="-EMAIL-", size=(40,1))],
        [sg.Text("Licență"), sg.Input(key="-LIC-", size=(40,1), password_char="*")],
        [sg.Button("Login", key="-LOGIN-", bind_return_key=True)]
    ]
    win = sg.Window("Facepost • Login", layout, modal=True)
    return win

def main_window(cfg):
    sg.theme("DarkBlue3")
    left = [
        [sg.Text("Group URLs (unul pe linie)")],
        [sg.Multiline(cfg.get("groups",""), key="-GROUPS-", size=(50,10))],
        [sg.Text("Post text")],
        [sg.Multiline(cfg.get("post_text",""), key="-TEXT-", size=(50,8))],
        [sg.Text("Imagini"), sg.Input(cfg.get("images_folder",""), key="-IMGF-", size=(36,1)), sg.FolderBrowse("Select")],
        [sg.Text("Ora zilnică (HH:MM)"), sg.Input(cfg.get("daily_time","10:00"), key="-TIME-", size=(8,1))],
        [sg.Button("Preview", key="-PREVIEW-"), sg.Button("Run acum", key="-RUN-"), sg.Button("Salvează", key="-SAVE-")],
    ]
    right = [
        [sg.Text("Status / Log")],
        [sg.Multiline("", key="-LOG-", size=(70,22), autoscroll=True, disabled=True)],
        [sg.Button("Verifică update", key="-UPDATE-"), sg.Push(), sg.Button("Exit")],
    ]
    col = [[sg.Column(left), sg.VSeperator(), sg.Column(right)]]
    win = sg.Window("Facepost", col, finalize=True, resizable=True)
    return win

def app():
    cfg = load_config()
    # login dacă nu avem licență sau email
    if not cfg.get("email") or not cfg.get("license_key"):
        w = login_window()
        while True:
            e, v = w.read()
            if e in (sg.WINDOW_CLOSED,):
                w.close()
                return
            if e == "-LOGIN-":
                email = v["-EMAIL-"].strip().lower()
                lic = v["-LIC-"].strip()
                if not email or not lic:
                    show_error("Completează email + licență.")
                    continue
                # facem bind/check
                cfg["email"] = email
                cfg["license_key"] = lic
                if not cfg.get("device_fingerprint"):
                    cfg["device_fingerprint"] = device_fingerprint()

                # În mod normal aici ai verifica licența cu /check (și/sau bind).
                # Exemplu minimal:
                resp = server_check(email, cfg["device_fingerprint"])
                if resp.get("status") == "ok":
                    save_config(cfg)
                    show_info("Autentificare OK.")
                    w.close()
                    break
                else:
                    show_error(f"Eroare licență: {resp}")
                    # nu ieșim din login
        # continuăm în main window

    # Fereastra principală
    win = main_window(cfg)

    # Pornește schedulerul în background
    sched_thread = start_daily_job(cfg, win)

    # Mesaj inițial
    win["-LOG-"].update(boxed("Aplicația a pornit. Verific update-uri...") + "\n", append=True)
    try_update_if_needed(win)

    while True:
        e, v = win.read()
        if e in (sg.WINDOW_CLOSED, "Exit"):
            break

        if e == "-SAVE-":
            cfg["groups"] = v["-GROUPS-"]
            cfg["post_text"] = v["-TEXT-"]
            cfg["images_folder"] = v["-IMGF-"]
            cfg["daily_time"] = v["-TIME-"]
            save_config(cfg)
            win["-LOG-"].update(boxed("Config salvat.") + "\n", append=True)

        elif e == "-PREVIEW-":
            imgs = list_images(v["-IMGF-"])
            win["-LOG-"].update(boxed(f"PREVIEW: {len(imgs)} imagini, primele 3: {imgs[:3]}") + "\n", append=True)
            win["-LOG-"].update(boxed(f"Text: {v['-TEXT-'][:100]}{'...' if len(v['-TEXT-'])>100 else ''}") + "\n", append=True)
            gr = [g.strip() for g in v["-GROUPS-"].splitlines() if g.strip()]
            win["-LOG-"].update(boxed(f"Vor fi postate {len(gr)} grupuri.") + "\n", append=True)

        elif e == "-RUN-":
            tmp_cfg = {
                "groups": v["-GROUPS-"],
                "post_text": v["-TEXT-"],
                "images_folder": v["-IMGF-"],
            }
            perform_post(tmp_cfg["groups"], tmp_cfg["post_text"], tmp_cfg["images_folder"], lambda m: win["-LOG-"].update(m + "\n", append=True))

        elif e == "-UPDATE-":
            updated = try_update_if_needed(win)
            if not updated:
                win["-LOG-"].update(boxed("Nu există update nou sau descărcarea a eșuat." ) + "\n", append=True)

        elif e == "-LOG-":
            # evenimente trimise din threaduri
            win["-LOG-"].update(v["-LOG-"] + "\n", append=True)

    win.close()

if __name__ == "__main__":
    app()
