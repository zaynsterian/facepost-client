Facepost Client – Instalare & Utilizare
=======================================

1) Cerințe
   - Windows 10/11
   - Chrome instalat și autentificat pe Facebook (într-un profil).
   - Python 3.10+ (doar pentru build; pe PC-ul clientului nu e necesar).

2) Build (doar pentru tine, ca publisher)
   - Copiază acest folder pe mașina ta.
   - (opțional) fă o copie a config.example.json ca "config.json"
   - Rulează: build_win.cmd
   - Găsești executabilul în dist/Facepost.exe

3) Distribuție
   - Pe PC-ul clientului creează: C:\Facepost\
   - Copiază: Facepost.exe + (opțional) config.json în C:\Facepost\
   - Creează un folder pentru imagini: C:\Facepost\post_images\

4) Primul launch (activare)
   - Rulează Facepost.exe
   - Introdu Server base (de ex. https://facepost.onrender.com)
   - Introdu emailul clientului (trebuie să existe licență pe server)
   - Apasă "Activate" -> dacă status=ok, intri în aplicație

5) Setări în aplicație
   - Chrome user-data-dir: alege profilul Chrome în care e logat FB,
     ex: C:\Users\Nume\AppData\Local\Google\Chrome\User Data
     (recomandat: profil dedicat, ex. C:\Facepost\ChromeProfile)
   - Group URLs: unul pe linie
   - Post Text: textul postării
   - Folder poze: C:\Facepost\post_images\
   - Delay: 120 sec (recomandat)
   - Scheduler: Enable + ora (HH:MM) pentru postare zilnică
   - Auto-update: ON (recomandat), setat version_endpoint

6) Run
   - "Preview" pentru a vedea textul și imaginile
   - "Run" pentru a posta în toate grupurile cu delay între postări

7) Log
   - Log-ul se salvează în facepost_log.txt în același folder.

8) Switch account
   - Meniu Account -> Switch account
   - La următoarea deschidere, cere din nou activarea (email).

9) Troubleshooting
   - Verifică să fie logat pe FB în profilul Chrome selectat.
   - Dacă nu găsește buton "Post/Publică", UI-ul FB s-a schimbat:
     contactează suport pentru un mic update de selectori.
   - Dacă auto-update nu descarcă, verifică endpointul /client-version
