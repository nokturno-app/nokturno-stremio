"""Zavaděč doplňku Nokturno pro Stremio: spouští doplněk a sám ho aktualizuje.

Jeden soubor pro všechny balíčky: addon pro Home Assistant, samostatný program pro
Windows, macOS a Linux (PyInstaller) i APK (viz android/, tam se volá `spust_v_procesu`).

Balíček nese vestavěnou kopii doplňku. Při startu a pak každých `KONTROLA_S` se zavaděč
podívá na `update_url` (JSON s verzí, adresou zipu a SHA-256). Je-li verze novější,
stáhne zip, ověří otisk, rozbalí ho vedle a doplněk restartuje. Když nová verze do
`START_S` neodpoví na /health, vrátí se k předchozí a vadnou už nezkouší.

    {"version": "9.0.1", "url": "https://…/nokturno-9.0.1.zip", "sha256": "…"}

Zip má na nejvyšší úrovni složku `nokturno/`. Vyrábí ho `baleni/balik.sh`.

Spuštění:  nokturno [--host 0.0.0.0] [--port 7140] [--https-port 7141] [--bez-https] [--data SLOŽKA]
           nokturno --povolit <adresa doplňku>     (soukromá instance, viz nokturno/soukroma.py)
           nokturno --install | --uninstall        (Windows a macOS: služba)

`host` (v nokturno.json i --host) je adresa poslechu. Za reverzní proxy (VPS s doménou)
127.0.0.1, ať port doplňku není vidět z internetu. `soukroma` zapne soukromou instanci:
doplněk obslouží jen nastavení povolená v `<data>/cache/povolena.txt`. `public_url` (i --public-url)
je veřejná adresa doplňku, kterou ukáže /configure – za proxy, kde adresa požadavku není ta veřejná.
"""
import argparse
import contextlib
import hashlib
import io
import json
import logging
import os
import plistlib
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import zipfile

LOG = logging.getLogger("zavadec")

# Kam se ptát na novou verzi, když ji nastavení (`update_url`) neurčí. Veřejné repo jen s vydáními.
VYCHOZI_UPDATE_URL = "https://raw.githubusercontent.com/nokturno-app/nokturno-stremio-app/main/update.json"
KONTROLA_S = 6 * 3600
START_S = 60
ZNACKA_AKTUALIZACE = "aktualizovat"   # stejné jméno v nokturno/routes.py
HA_VOLBY = "/data/options.json"
VYCHOZI = {"host": "0.0.0.0", "soukroma": False, "public_url": "", "port": 7140, "https_port": 7141, "enable_https": True, "tmdb_key": "",
           "stats": True, "crash_reports": True, "update_url": ""}
SLUZBA = "Nokturno"
SLUZBA_POPIS = "Nokturno pro Stremio"
MAC_LABEL = "cz.nokturno.stremio"
MAC_PLIST = "/Library/LaunchDaemons/cz.nokturno.stremio.plist"
MAC_SLOZKA = "/Library/Application Support/Nokturno"
PRENOS_IGNORUJ = ("verze", "zavadec.json", "nokturno.log")   # co se při přenosu dat nekopíruje


def verze_tuple(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


def zmrazeny():
    return getattr(sys, "frozen", False)


def vestaveny_kod():
    """Složka, ve které leží vestavěný balík `nokturno/` a `version.txt`."""
    if os.environ.get("NOKTURNO_VESTAVENY"):
        return os.environ["NOKTURNO_VESTAVENY"]
    if zmrazeny():
        return os.path.join(sys._MEIPASS, "app")   # noqa: SLF001 – PyInstaller
    if os.path.isdir("/app/nokturno"):
        return "/app"                               # addon pro HA
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # z repa


def datova_slozka():
    if os.environ.get("NOKTURNO_ZAVADEC_DATA"):
        return os.environ["NOKTURNO_ZAVADEC_DATA"]
    if os.path.isfile(HA_VOLBY):
        return "/data"
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Nokturno")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Nokturno")
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "nokturno")


def nacti_volby(data):
    """Volby: v HA z options.json addonu, jinde z nokturno.json v datové složce
    (při prvním startu se založí s výchozími hodnotami, ať je co upravit)."""
    cesta = os.environ.get("NOKTURNO_VOLBY") or (HA_VOLBY if os.path.isfile(HA_VOLBY)
                                                  else os.path.join(data, "nokturno.json"))
    try:
        with open(cesta, encoding="utf-8") as f:
            return {**VYCHOZI, **json.load(f)}
    except (OSError, ValueError):
        if cesta != HA_VOLBY:
            try:
                os.makedirs(os.path.dirname(cesta), exist_ok=True)
                with open(cesta, "w", encoding="utf-8") as f:
                    json.dump(VYCHOZI, f, indent=2)
            except OSError:
                pass
        return dict(VYCHOZI)


def mistni_ip():
    """IPv4 v domácí síti (nic se neposílá, jen se zjistí rozhraní k výchozí trase)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def druh_behu(env=None):
    """Jak aplikace běží, jen kód do statistik (NOKTURNO_BEH). APK si ho nastaví samo."""
    env = os.environ if env is None else env
    if env.get("SUPERVISOR_TOKEN"):
        return "ha"
    if sys.platform.startswith("linux") and env.get("INVOCATION_ID"):
        return "systemd"
    if os.path.exists("/.dockerenv"):
        return "docker"
    if zmrazeny():
        return {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    return "python"


def prostredi(volby, data, beh=None):
    env = dict(os.environ)
    env.update({
        "NOKTURNO_BEH": beh or druh_behu(),
        "NOKTURNO_HOST": str(volby.get("host") or "0.0.0.0"),
        "NOKTURNO_SOUKROMA": "1" if volby.get("soukroma") else "0",
        "NOKTURNO_PUBLIC_URL": str(volby.get("public_url") or "").strip(),
        "NOKTURNO_PORT": str(volby.get("port") or 7140),
        "NOKTURNO_DATA": os.path.join(data, "cache"),
        "NOKTURNO_HTTPS_PORT": str(volby.get("https_port") or 7141) if volby.get("enable_https", True) else "",
        "NOKTURNO_TMDB_KEY": (volby.get("tmdb_key") or "").strip(),
        "NOKTURNO_STATS": "1" if volby.get("stats", True) else "0",
        "NOKTURNO_CRASH_REPORTS": "1" if volby.get("crash_reports", True) else "0",
        "NOKTURNO_TRAFFIC": "0",
        "PYTHONUNBUFFERED": "1",
    })
    # Tlačítko aktualizace na /configure jen tam, kde běží smyčka zavaděče (APK ji nemá).
    env["NOKTURNO_UPDATE_URL"] = "" if env["NOKTURNO_BEH"] == "android" else \
        (str(volby.get("update_url") or "").strip() or VYCHOZI_UPDATE_URL)
    return env


def znacka_aktualizace(data):
    """Soubor, kterým formulář (POST /aktualizace) požádá zavaděč o kontrolu hned."""
    return os.path.join(data, "cache", ZNACKA_AKTUALIZACE)


class Verze:
    """Stažené verze v `<data>/verze/<verze>/nokturno/` a stav v `<data>/zavadec.json`."""

    def __init__(self, data, update_url):
        self.url = (update_url or VYCHOZI_UPDATE_URL).strip()
        self.slozka = os.path.join(data, "verze")
        self.stav_soubor = os.path.join(data, "zavadec.json")
        os.makedirs(self.slozka, exist_ok=True)
        try:
            with open(self.stav_soubor, encoding="utf-8") as f:
                self.stav = json.load(f)
        except (OSError, ValueError):
            self.stav = {"aktualni": None, "vadne": []}

    def uloz(self):
        tmp = self.stav_soubor + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.stav, f)
        os.replace(tmp, self.stav_soubor)

    @staticmethod
    def vestavena():
        if os.environ.get("NOKTURNO_VESTAVENA_VERZE"):
            return os.environ["NOKTURNO_VESTAVENA_VERZE"]
        try:
            with open(os.path.join(vestaveny_kod(), "version.txt"), encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return "0"

    def aktualni(self):
        """(verze, složka s balíkem `nokturno/`) — stažená, je-li novější než vestavěná."""
        v = self.stav.get("aktualni")
        cesta = os.path.join(self.slozka, v) if v else ""
        if v and os.path.isfile(os.path.join(cesta, "nokturno", "server.py")) \
                and verze_tuple(v) > verze_tuple(self.vestavena()):
            return v, cesta
        return self.vestavena(), vestaveny_kod()

    def stahni_novou(self):
        """Stáhne a rozbalí novější verzi. True = je co spustit."""
        if not self.url:
            return False
        req = urllib.request.Request(self.url, headers={"User-Agent": "Nokturno zavadec"})
        with urllib.request.urlopen(req, timeout=30) as r:
            info = json.load(r)
        nova = str(info["version"])
        if verze_tuple(nova) <= verze_tuple(self.aktualni()[0]) or nova in self.stav.get("vadne", []):
            return False
        req = urllib.request.Request(info["url"], headers={"User-Agent": "Nokturno zavadec"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        if hashlib.sha256(data).hexdigest() != str(info["sha256"]).lower():
            raise ValueError(f"otisk balíku {nova} nesedí")
        cil = os.path.join(self.slozka, nova)
        tmp = cil + ".tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(tmp)   # extractall zahodí absolutní cesty i „..“
        if not os.path.isfile(os.path.join(tmp, "nokturno", "server.py")):
            shutil.rmtree(tmp, ignore_errors=True)
            raise ValueError(f"balík {nova} nemá nokturno/server.py")
        shutil.rmtree(cil, ignore_errors=True)
        os.replace(tmp, cil)
        self.stav["predchozi"] = self.stav.get("aktualni")
        self.stav["aktualni"] = nova
        self.uloz()
        LOG.info("stažena verze %s", nova)
        return True

    def oznac_vadnou(self, verze):
        LOG.error("verze %s nenaběhla, vracím předchozí", verze)
        self.stav.setdefault("vadne", []).append(verze)
        self.stav["aktualni"] = self.stav.get("predchozi")
        self.uloz()

    def uklid(self):
        """Na disku jen aktuální a předchozí verze."""
        drzet = {self.stav.get("aktualni"), self.stav.get("predchozi")}
        for jmeno in os.listdir(self.slozka):
            if jmeno not in drzet:
                shutil.rmtree(os.path.join(self.slozka, jmeno), ignore_errors=True)


class Zavadec:
    """Doplněk jako podproces; aktualizace a restart na pozadí."""

    def __init__(self, volby, data):
        self.volby = volby
        self.data = data
        self.port = int(volby.get("port") or 7140)
        listen = str(volby.get("host") or "0.0.0.0")
        self.health_host = "127.0.0.1" if listen in ("0.0.0.0", "::") else listen
        self.verze = Verze(data, volby.get("update_url"))
        self.proces = None
        self.konec = threading.Event()
        self.zamek = threading.Lock()

    def spust(self):
        verze, slozka = self.verze.aktualni()
        LOG.info("spouštím doplněk %s z %s", verze, slozka)
        prikaz = [sys.executable] + ([] if zmrazeny() else [os.path.abspath(__file__)]) + ["--sluzba", slozka]
        self.proces = subprocess.Popen(prikaz, env=prostredi(self.volby, self.data))
        return verze

    def zastav(self):
        p, self.proces = self.proces, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(20)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()

    def zdravy(self):
        konec = time.monotonic() + START_S
        while time.monotonic() < konec:
            if self.proces is None or self.proces.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(f"http://{self.health_host}:{self.port}/health", timeout=3) as r:
                    if r.status == 200:
                        return True
            except OSError:
                pass
            time.sleep(2)
        return False

    def restart(self):
        """Spustí aktuální verzi; když nenaběhne a je stažená, vrátí předchozí."""
        with self.zamek:
            self.zastav()
            verze = self.spust()
            if self.zdravy():
                self.verze.uklid()
                return True
            if self.verze.stav.get("aktualni") == verze:
                self.verze.oznac_vadnou(verze)
                self.zastav()
                self.spust()
            return False

    def vypis_adresy(self):
        ip = mistni_ip()
        LOG.info("nastavení doplňku: http://%s:%d/configure", ip, self.port)
        if self.volby.get("enable_https", True) and ip != "127.0.0.1":
            LOG.info("Stremio v síti: https://%s.my.local-ip.co:%s", ip.replace(".", "-"),
                     self.volby.get("https_port") or 7141)

    def bez(self):
        try:
            self.verze.stahni_novou()
        except Exception as err:  # noqa: BLE001 – bez sítě jede stávající verze
            LOG.warning("kontrola aktualizace: %s", err)
        self.restart()
        self.vypis_adresy()
        dalsi = time.monotonic() + KONTROLA_S
        while not self.konec.wait(5):
            if self.proces is not None and self.proces.poll() is not None:
                LOG.warning("doplněk skončil (%s), spouštím znovu", self.proces.returncode)
                self.restart()
            znacka = znacka_aktualizace(self.data)
            if os.path.exists(znacka):
                LOG.info("kontrola aktualizace z formuláře")
                with contextlib.suppress(OSError):
                    os.remove(znacka)
                dalsi = 0
            if time.monotonic() >= dalsi:
                dalsi = time.monotonic() + KONTROLA_S
                try:
                    if self.verze.stahni_novou():
                        self.restart()
                except Exception as err:  # noqa: BLE001
                    LOG.warning("kontrola aktualizace: %s", err)
        self.zastav()


def sluzba(slozka):
    """Podproces: doplněk ze zadané složky (vestavěná nebo stažená verze)."""
    sys.path.insert(0, slozka)
    from nokturno import server   # noqa: PLC0415 – záměrně až po úpravě sys.path
    return server.main([])


def spust_v_procesu(data, volby=None):
    """Pro APK: bez podprocesu. Aktualizace jen při startu služby; doplněk běží v tomto vlákně."""
    volby = {**VYCHOZI, **(volby or nacti_volby(data))}
    verze = Verze(data, volby.get("update_url"))
    try:
        verze.stahni_novou()
    except Exception as err:  # noqa: BLE001
        LOG.warning("kontrola aktualizace: %s", err)
    cislo, slozka = verze.aktualni()
    os.environ.update(prostredi(volby, data, beh="android"))
    LOG.info("spouštím doplněk %s", cislo)
    return sluzba(slozka)


def povolit(text, slozka):
    """`nokturno --povolit <adresa>`: připíše otisk do povolena.txt; běžící doplněk ho vezme hned."""
    sys.path.insert(0, Verze(os.path.dirname(slozka), "").aktualni()[1])
    from nokturno import soukroma   # noqa: PLC0415 – z vestavěné nebo stažené verze
    otisk = soukroma.otisk_z_textu(text)
    if not otisk:
        print("Adrese nerozumím – vlož celou adresu doplňku (…/c/…/manifest.json).", file=sys.stderr)
        return 2
    soukroma.Povolena(slozka).pridej(otisk)
    print(f"Povoleno: {otisk}")
    return 0


def tray(z):
    """Windows: bez okna konzole, ikona v oznamovací oblasti (Otevřít nastavení / Ukončit).
    False = ikona nejde (chybí knihovna, není plocha) → běží se jako dřív."""
    try:
        import pystray  # noqa: PLC0415
        from PIL import Image, ImageDraw  # noqa: PLC0415
    except ImportError:
        return False
    obr = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    kresli = ImageDraw.Draw(obr)
    kresli.ellipse((4, 4, 60, 60), fill=(92, 68, 150, 255))
    kresli.ellipse((22, 4, 66, 56), fill=(0, 0, 0, 0))   # srpek měsíce
    url = f"http://127.0.0.1:{z.port}/configure"

    def konec(ikona, _polozka):
        z.konec.set()
        ikona.stop()

    ikona = pystray.Icon("nokturno", obr, "Nokturno", pystray.Menu(
        pystray.MenuItem("Otevřít nastavení", lambda *_: webbrowser.open(url), default=True),
        pystray.MenuItem("Ukončit", konec)))
    vlakno = threading.Thread(target=z.bez, daemon=True)
    vlakno.start()
    ikona.run()   # blokuje do „Ukončit“
    z.konec.set()
    vlakno.join(30)
    return True


def slozka_sluzby():
    return os.path.join(os.environ.get("PROGRAMDATA") or r"C:\ProgramData", "Nokturno")


def oznam(text, chyba=False):
    """Windows exe nemá konzoli, výsledek ukáže okno; jinde jde na výstup."""
    if sys.platform == "win32":
        import ctypes  # noqa: PLC0415
        ctypes.windll.user32.MessageBoxW(None, text, SLUZBA_POPIS, 0x10 if chyba else 0x40)
    else:
        print(text, file=sys.stderr if chyba else sys.stdout)


def bezi_na_portu(port):
    """True, když na portu už odpovídá /health (služba nebo jiná instance)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
            return r.status == 200
    except OSError:
        return False


def prikazy_instalace(exe, data):
    """Příkazy pro instalaci služby Windows (čistá funkce, jen sestaví seznam)."""
    binpath = f'"{exe}" --windows-sluzba --data "{data}"'
    return [
        ["sc.exe", "create", SLUZBA, "binPath=", binpath, "start=", "delayed-auto", "DisplayName=", SLUZBA_POPIS],
        ["sc.exe", "description", SLUZBA, "Doplněk Nokturno pro Stremio a Nuvio. Nastavení: http://127.0.0.1:7140/configure"],
        ["sc.exe", "failure", SLUZBA, "reset=", "86400", "actions=", "restart/5000/restart/5000/restart/60000"],
        ["netsh", "advfirewall", "firewall", "add", "rule", f"name={SLUZBA_POPIS}", "dir=in", "action=allow",
         f"program={exe}", "profile=private,domain", "enable=yes"],
        ["sc.exe", "start", SLUZBA],
    ]


def prikazy_odinstalace():
    return [["sc.exe", "stop", SLUZBA], ["sc.exe", "delete", SLUZBA],
            ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={SLUZBA_POPIS}"]]


def je_spravce():
    try:
        import ctypes  # noqa: PLC0415
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except (AttributeError, OSError):
        return False


def povys(argv):
    """Spustí exe znovu se stejnými argumenty jako správce; výsledek tam ohlásí okno."""
    import ctypes  # noqa: PLC0415
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, subprocess.list2cmdline(argv), None, 1)
    return 0


def prenes_data(stara, nova):
    """Při první instalaci převezme nastavení a data z dosavadní aplikace."""
    if not os.path.isdir(nova) and os.path.isdir(stara):
        shutil.copytree(stara, nova, ignore=shutil.ignore_patterns(*PRENOS_IGNORUJ))
    else:
        os.makedirs(nova, exist_ok=True)


def zkopiruj_exe(exe):
    """Služba pak nezávisí na tom, kde leží stažený soubor. Přeinstalace z už nainstalovaného exe nic nekopíruje."""
    if os.path.abspath(sys.executable) != os.path.abspath(exe):
        shutil.copy2(sys.executable, exe)


def _sluzba_bezi():
    r = subprocess.run(["sc.exe", "query", SLUZBA], capture_output=True, text=True)
    return r.returncode == 0 and "RUNNING" in r.stdout


def nainstaluj_windows(argv):
    if not je_spravce():
        return povys(argv)
    data = slozka_sluzby()
    exe = os.path.join(data, "nokturno.exe")
    if subprocess.run(["sc.exe", "query", SLUZBA], capture_output=True).returncode == 0:
        for p in prikazy_odinstalace():   # opakovaná instalace = aktualizace exe
            subprocess.run(p, capture_output=True)
        for _ in range(30):
            if not _sluzba_bezi():
                break
            time.sleep(1)
    if bezi_na_portu(7140):
        oznam("Nokturno už běží (ikona měsíce vpravo dole). Ukonči ho (pravé tlačítko → Ukončit) a spusť instalaci znovu.", True)
        return 1
    prenes_data(datova_slozka(), data)
    zkopiruj_exe(exe)
    for p in prikazy_instalace(exe, data):
        r = subprocess.run(p, capture_output=True, text=True)
        if r.returncode != 0:
            oznam(f"Instalace služby selhala: {' '.join(p[:2])}\n{r.stdout}{r.stderr}", True)
            return 1
    oznam("Nokturno běží jako služba Windows a spustí se samo po zapnutí počítače, i bez přihlášení.\n\n"
          "Nastavení: http://127.0.0.1:7140/configure\nOdinstalace: nokturno.exe --uninstall")
    return 0


def odinstaluj_windows(argv):
    if not je_spravce():
        return povys(argv)
    for p in prikazy_odinstalace():
        r = subprocess.run(p, capture_output=True, text=True)
        if r.returncode != 0:
            oznam(f"{' '.join(p[:2])}: {r.stdout}{r.stderr}".strip(), True)
    with contextlib.suppress(OSError):
        os.remove(os.path.join(slozka_sluzby(), "nokturno.exe"))   # za běhu nemusí jít hned
    oznam("Služba Nokturno je odinstalovaná. Nastavení a data zůstala v " + slozka_sluzby())
    return 0


def plist_macos(exe, data):
    """LaunchDaemon (čistá funkce): služba běží od startu Macu, při pádu se restartuje."""
    return plistlib.dumps({"Label": MAC_LABEL, "ProgramArguments": [exe, "--data", data],
                           "RunAtLoad": True, "KeepAlive": True,
                           "StandardOutPath": data + "/nokturno.log", "StandardErrorPath": data + "/nokturno.log"})


def nainstaluj_macos(_argv):
    if os.geteuid() != 0:
        oznam("Spusť instalaci přes sudo: sudo ./" + os.path.basename(sys.executable) + " --install", True)
        return 1
    if os.path.exists(MAC_PLIST):   # opakovaná instalace = aktualizace
        subprocess.run(["launchctl", "bootout", "system/" + MAC_LABEL], capture_output=True)
    if bezi_na_portu(7140):
        oznam("Nokturno už běží – ukonči ho (Ctrl+C v Terminálu) a spusť instalaci znovu.", True)
        return 1
    stara = os.path.expanduser("~" + os.environ.get("SUDO_USER", "") + "/Library/Application Support/Nokturno")
    prenes_data(stara, MAC_SLOZKA)
    exe = MAC_SLOZKA + "/nokturno"
    zkopiruj_exe(exe)
    os.chmod(exe, 0o755)
    with open(MAC_PLIST, "wb") as f:
        f.write(plist_macos(exe, MAC_SLOZKA))
    os.chmod(MAC_PLIST, 0o644)
    r = subprocess.run(["launchctl", "bootstrap", "system", MAC_PLIST], capture_output=True, text=True)
    if r.returncode != 0:
        oznam(f"Instalace služby selhala: launchctl bootstrap\n{r.stdout}{r.stderr}", True)
        return 1
    oznam("Nokturno běží jako služba macOS a spustí se samo po zapnutí Macu, i bez přihlášení. "
          "Okno Terminálu můžeš zavřít.\nNastavení: http://127.0.0.1:7140/configure\n"
          'Odinstalace: sudo "/Library/Application Support/Nokturno/nokturno" --uninstall')
    return 0


def odinstaluj_macos(_argv):
    if os.geteuid() != 0:
        oznam('Spusť odinstalaci přes sudo: sudo "' + sys.executable + '" --uninstall', True)
        return 1
    r = subprocess.run(["launchctl", "bootout", "system/" + MAC_LABEL], capture_output=True, text=True)
    if r.returncode != 0:
        oznam(f"launchctl bootout: {r.stdout}{r.stderr}".strip(), True)
    for cesta in (MAC_PLIST, MAC_SLOZKA + "/nokturno"):
        with contextlib.suppress(OSError):
            os.remove(cesta)
    oznam("Služba Nokturno je odinstalovaná. Nastavení a data zůstala v " + MAC_SLOZKA)
    return 0


def _jen_zabalene():
    if zmrazeny() and sys.platform in ("win32", "darwin"):
        return False
    oznam("Instalace služby funguje jen v aplikaci pro Windows a macOS. Na Linuxu použij install.sh (viz README).", True)
    return True


def nainstaluj_sluzbu(argv):
    if _jen_zabalene():
        return 2
    return (nainstaluj_windows if sys.platform == "win32" else nainstaluj_macos)(argv)


def odinstaluj_sluzbu(argv):
    if _jen_zabalene():
        return 2
    return (odinstaluj_windows if sys.platform == "win32" else odinstaluj_macos)(argv)


def windows_sluzba(data):
    """Běh pod správcem služeb Windows (pywin32); doplněk hlídá `Zavadec` jako jinde."""
    import servicemanager  # noqa: PLC0415
    import win32service  # noqa: PLC0415
    import win32serviceutil  # noqa: PLC0415

    class Sluzba(win32serviceutil.ServiceFramework):
        _svc_name_ = SLUZBA
        _svc_display_name_ = SLUZBA_POPIS

        def __init__(self, args):
            super().__init__(args)
            self.z = Zavadec(nacti_volby(data), data)

        def SvcDoRun(self):
            self.z.bez()

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING, waitHint=30000)
            self.z.konec.set()

    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(Sluzba)
    servicemanager.StartServiceCtrlDispatcher()
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--sluzba"]:
        return sluzba(argv[1])
    ap = argparse.ArgumentParser(prog="nokturno", description="Nokturno pro Stremio a Nuvio")
    ap.add_argument("--host", help="adresa poslechu (za reverzní proxy 127.0.0.1)")
    ap.add_argument("--public-url", help="veřejná adresa doplňku za proxy, např. https://nokturno.example.cz")
    ap.add_argument("--povolit", metavar="ADRESA", help="soukromá instance: povolit adresu doplňku (nebo otisk) a skončit")
    ap.add_argument("--port", type=int)
    ap.add_argument("--https-port", type=int)
    ap.add_argument("--bez-https", action="store_true")
    ap.add_argument("--data", help="datová složka (nastavení, cache, stažené verze)")
    ap.add_argument("--install", action="store_true",
                    help="Windows a macOS: nainstalovat jako službu (spustí se po startu počítače i bez přihlášení)")
    ap.add_argument("--uninstall", action="store_true",
                    help="Windows a macOS: odinstalovat službu, data nechá")
    ap.add_argument("--windows-sluzba", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.install:
        return nainstaluj_sluzbu(argv)
    if args.uninstall:
        return odinstaluj_sluzbu(argv)
    data = args.data or datova_slozka()
    os.makedirs(data, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        **({} if sys.stderr else {"filename": os.path.join(data, "nokturno.log"), "encoding": "utf-8"}))   # bez konzole (Windows tray)
    if args.windows_sluzba:
        return windows_sluzba(data)
    if args.povolit:
        return povolit(args.povolit, os.path.join(data, "cache"))
    volby = nacti_volby(data)
    if args.host:
        volby["host"] = args.host
    if args.public_url:
        volby["public_url"] = args.public_url
    if args.port:
        volby["port"] = args.port
    if args.https_port:
        volby["https_port"] = args.https_port
    if args.bez_https:
        volby["enable_https"] = False
    z = Zavadec(volby, data)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: z.konec.set())
    if sys.platform == "win32" and zmrazeny() and bezi_na_portu(z.port):
        webbrowser.open(f"http://127.0.0.1:{z.port}/configure")   # běží služba nebo jiná instance
        return 0
    if sys.platform == "win32" and zmrazeny() and tray(z):
        return 0
    try:
        z.bez()
    except KeyboardInterrupt:
        z.konec.set()
        z.zastav()
    return 0


if __name__ == "__main__":
    sys.exit(main())
