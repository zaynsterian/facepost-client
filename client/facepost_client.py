import os, sys, json, time, re
from pathlib import Path

import PySimpleGUI as sg

# ---------------------------
#   CONFIG: load/save helpers
# ---------------------------
APP_VERSION = "1.1.1"
APP_NAME = "Facepost"

def appdata_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming")))
        return base / APP_NAME
    elif sys.platform == "darwin":
        return Path.home() / "Library/Application Support" / APP_NAME
    else:
        return Path.home() / ".config" / APP_NAME

def config_path() -> Path:
    return appdata_dir() / "config.json"

DEFAULT_CONFIG = {
    "chrome_user_data_dir": "",
    "images_folder": "",
    "delay_sec": 120,
    "simulate": True,               # implicit SIM pentru siguranță la primul run
    "email": "",
    "license_key": "",
}

def load_config() -> dict:
    try:
        p = config_path()
        if p.exists():
            return {**DEFAULT_CONFIG, **json.loads(p.read_text(encoding="utf-8"))}
    except Exception:
        pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg: dict) -> None:
    try:
        ad = appdata_dir()
        ad.mkdir(parents=True, exist_ok=True)
        config_path().write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception as e:
        sg.popup_error(f"Nu pot salva setările:\n{e}")

# ---------------------------
#   VALIDĂRI & UTILITARE
# ---------------------------
def is_valid_fb_group_url(url: str) -> bool:
    return bool(re.match(r"^https?://(www\.)?facebook\.com/groups/[\w\.\-]+/?$", url.strip()))

def ensure_first_run_wizard(cfg: dict) -> dict:
    """Dacă lipsesc lucruri critice -> deschide wizardul Settings la pornire."""
    need = not cfg["chrome_user_data_dir"]
    if need:
        cfg = show_settings_dialog(cfg, first_run=True)
    return cfg

# ---------------------------
#   SETTINGS DIALOG
# ---------------------------
def show_settings_dialog(cfg: dict, first_run: bool=False) -> dict:
    sg.theme("SystemDefault")

    layout = [
        [sg.Text("Chrome user-data-dir", size=(20,1)),
         sg.Input(cfg["chrome_user_data_dir"], key="-UD-", expand_x=True),
         sg.FolderBrowse("Select")],
        [sg.Text("Images folder (implicit)", size=(20,1)),
         sg.Input(cfg["images_folder"], key="-IMGS-", expand_x=True),
         sg.FolderBrowse("Select")],
        [sg.Text("Delay (sec)", size=(20,1)),
         sg.Input(str(cfg["delay_sec"]), key="-DELAY-", size=(8,1))],
        [sg.Checkbox("Rulează în modul SIMULARE (fără postare reală)", key="-SIM-",
                     default=cfg["simulate"])],
        [sg.HorizontalSeparator()],
        [sg.Text("Email (licență)", size=(20,1)),
         sg.Input(cfg["email"], key="-EMAIL-", expand_x=True)],
        [sg.Text("License key", size=(20,1)),
         sg.Input(cfg["license_key"], key="-LIC-", expand_x=True, password_char="*")],
        [sg.HorizontalSeparator()],
        [sg.Button("Test config", key="-TEST-"), sg.Push(),
         sg.Button("Salvează", key="-SAVE-", button_color=("white", "#6C5CE7")),
         sg.Button("Anulează", key="-CANCEL-")]
    ]
    title = f"{APP_NAME} – Settings" if not first_run else f"{APP_NAME} – Configurare inițială"
    win = sg.Window(title, layout, modal=True, finalize=True)

    new_cfg = cfg.copy()
    while True:
        ev, vals = win.read()
        if ev in (sg.WIN_CLOSED, "-CANCEL-"):
            break

        if ev == "-TEST-":
            msg = []
            ud = vals["-UD-"].strip()
            imgs = vals["-IMGS-"].strip()
            dly = vals["-DELAY-"].strip()
            if not ud or not Path(ud).exists():
                msg.append("• Chrome user-data-dir NU este setat sau nu există.")
            if imgs and not Path(imgs).exists():
                msg.append("• Images folder indică un folder inexistent.")
            if not dly.isdigit() or int(dly) < 0:
                msg.append("• Delay trebuie să fie număr >= 0.")
            if msg:
                sg.popup_error("Probleme de configurare:\n\n" + "\n".join(msg))
            else:
                sg.popup_ok("Config OK ✅")

        if ev == "-SAVE-":
            try:
                ud = vals["-UD-"].strip()
                dly = int(vals["-DELAY-"].strip())
                new_cfg.update({
                    "chrome_user_data_dir": ud,
                    "images_folder": vals["-IMGS-"].strip(),
                    "delay_sec": max(0, dly),
                    "simulate": bool(vals["-SIM-"]),
                    "email": vals["-EMAIL-"].strip(),
                    "license_key": vals["-LIC-"].strip(),
                })
                if not new_cfg["chrome_user_data_dir"]:
                    sg.popup_error("Setează Chrome user-data-dir (profilul unde ești logat în Facebook).")
                    continue
                save_config(new_cfg)
                sg.popup_ok("Setări salvate.")
                break
            except Exception as e:
                sg.popup_error(f"Eroare la salvare: {e}")

    win.close()
    return new_cfg

# ---------------------------
#   MAIN UI
# ---------------------------
def main():
    cfg = load_config()
    cfg = ensure_first_run_wizard(cfg)  # deschide wizard dacă e prima dată / lipsesc setări de bază

    sg.theme("SystemDefault")
    menu = [["Ajutor", ["Open AppData", "Despre"]]]

    layout = [
        [sg.Menu(menu)],
        [sg.Text("Group URLs (unul pe linie)")],
        [sg.Multiline("", key="-GROUPS-", size=(80,8), expand_x=True, expand_y=True)],
        [sg.Text("Images folder"), sg.Input(cfg["images_folder"], key="-IMGS-", expand_x=True),
         sg.FolderBrowse("Browse")],
        [sg.Text("Post text")],
        [sg.Multiline("", key="-TEXT-", size=(80,8), expand_x=True, expand_y=True)],
        [sg.Text("Delay (sec)"), sg.Input(str(cfg["delay_sec"]), key="-DELAY-", size=(8,1)),
         sg.Push(),
         sg.Button("Preview", key="-PREVIEW-"),
         sg.Button("Save", key="-SAVE-"),
         sg.Button("Run", key="-RUN-", button_color=("white", "#00B894")),
         sg.Button("⚙️ Settings", key="-SET-")]
    ]

    win = sg.Window(APP_NAME, layout, finalize=True, resizable=True)

    while True:
        ev, vals = win.read()
        if ev == sg.WIN_CLOSED:
            break

        if ev == "Open AppData":
            os.startfile(str(appdata_dir())) if sys.platform.startswith("win") else os.system(f'open "{appdata_dir()}"')
        if ev == "Despre":
            sg.popup_ok(f"{APP_NAME}\nConfig salvat în:\n{config_path()}")

        if ev == "-SET-":
            cfg = show_settings_dialog(cfg)
            # reflectăm câteva setări în UI
            win["-IMGS-"].update(cfg.get("images_folder", ""))
            win["-DELAY-"].update(str(cfg.get("delay_sec", 120)))

        if ev == "-SAVE-":
            # salvăm doar elemente din fereastra principală (grupuri/text/delay/folder)
            try:
                dly = int(vals["-DELAY-"])
            except:
                dly = cfg["delay_sec"]
            cfg.update({
                "images_folder": vals["-IMGS-"].strip(),
                "delay_sec": max(0, dly),
            })
            save_config(cfg)
            sg.popup_ok("Date salvate în config.")

        if ev == "-PREVIEW-":
            groups = [g.strip() for g in vals["-GROUPS-"].splitlines() if g.strip()]
            bad = [g for g in groups if not is_valid_fb_group_url(g)]
            if bad:
                sg.popup_error("Următoarele URL-uri nu par a fi linkuri valide de grup Facebook:\n\n" + "\n".join(bad))
                continue
            text = vals["-TEXT-"].strip() or "(fără text)"
            sg.popup_ok(f"PREVIEW:\n\nGrupuri: {len(groups)}\nDelay: {cfg['delay_sec']}s\nSimulare: {cfg['simulate']}\n\nText:\n{text}")

        if ev == "-RUN-":
            # 1) validări ușoare
            groups = [g.strip() for g in vals["-GROUPS-"].splitlines() if g.strip()]
            if not groups:
                sg.popup_error("Adaugă cel puțin un URL de grup.")
                continue
            bad = [g for g in groups if not is_valid_fb_group_url(g)]
            if bad:
                sg.popup_error("Următoarele URL-uri nu par a fi linkuri valide de grup Facebook:\n\n" + "\n".join(bad))
                continue
            # 2) salvăm ultimele alegeri în config
            try:
                dly = int(vals["-DELAY-"])
            except:
                dly = cfg["delay_sec"]
            cfg.update({
                "images_folder": vals["-IMGS-"].strip(),
                "delay_sec": max(0, dly),
            })
            save_config(cfg)

            # 3) rulăm jobul – aici pui logica reală de Selenium;
            #    pentru demo, doar simulăm / apelăm modul live în funcție de cfg["simulate"]
            if cfg["simulate"]:
                # simulare pură
                for idx, g in enumerate(groups, start=1):
                    sg.one_line_progress_meter("Simulare postare", idx, len(groups),
                                               f"Postez (simulat) în {g}", orientation="h")
                    time.sleep(0.2)
                sg.popup_ok("Postare simulată terminată.")
            else:
                # aici chemi funcția ta de postare reală (Selenium):
                # run_posting(groups, vals["-TEXT-"], cfg["images_folder"], cfg)
                sg.popup_ok("(LIVE) Am pornit job-ul de postare. Vezi logul/Chrome.")

    win.close()

if __name__ == "__main__":
    main()

