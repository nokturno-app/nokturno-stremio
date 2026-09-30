"""Soukromá instance a nastavení aplikace v datové složce.

Soukromá instance (`NOKTURNO_SOUKROMA=1`, v `nokturno.json` `"soukroma": true`) může mít
adresu doplňku v internetu, ale obslouží jen nastavení, která majitel povolil. Povolená
jsou v `povolena.txt` v datové složce, jeden otisk (`otisk()`) na řádek, `#` je komentář.
Soubor se čte znovu, když se změní, takže ruční úprava platí bez restartu.

Otisk se počítá bez tokenu identity (`id`): ten si formulář dotahuje na pozadí a adresa
by se jím po povolení změnila.

Požadavek, který přišel přes Cloudflare (hlavička `Cf-Connecting-IP`), je „z proxy":
na soukromé instanci smí jen na cesty doplňku, formulář ani `/povolit` nedostane.
Hlavní ochrana je v nginx, tohle je druhá pojistka.

`aplikace.json` drží volby aplikace, které jde změnit na `/configure` (statistiky
a hlášení o pádech) — na Androidu `nokturno.json` upravit nejde. Má přednost před
prostředím.
"""
import json
import os
import re
import threading
import urllib.parse

from . import config

POVOLENA = "povolena.txt"
APLIKACE = "aplikace.json"
OTISK_RE = re.compile(r"^[0-9a-f]{16}$")


def z_proxy(headers):
    """Požadavek z internetu přes Cloudflare. Výjimka: admin.* za Cloudflare Access s přihlášeným
    uživatelem (Access hlavičku doplní až po ověření; cesty k tomuhle hostu jinak nevedou)."""
    if not headers.get("Cf-Connecting-IP"):
        return False
    admin = str(headers.get("Host") or "").lower().startswith("admin.")
    return not (admin and headers.get("Cf-Access-Authenticated-User-Email"))


def otisk(options):
    return config.fingerprint({k: v for k, v in (options or {}).items() if k != config.ID_KLIC})


def otisk_z_textu(text):
    """Adresa doplňku, samotný kousek `/c/<nastavení>` nebo rovnou otisk → otisk; None = nečitelné."""
    text = urllib.parse.unquote((text or "").strip())
    if OTISK_RE.match(text):
        return text
    m = re.search(r"/c/([^/?#]+)", text)
    options = config.decode(m.group(1) if m else text)
    return otisk(options) if options else None


class Povolena:
    def __init__(self, data_dir):
        self.cesta = os.path.join(data_dir, POVOLENA)
        self._zamek = threading.Lock()
        self._mtime = None
        self._otisky = frozenset()

    def _nacti(self):
        try:
            mtime = os.stat(self.cesta).st_mtime_ns
        except OSError:
            mtime = None
        if mtime != self._mtime:
            try:
                with open(self.cesta, encoding="utf-8") as f:
                    radky = [r.split("#")[0].strip() for r in f]
            except OSError:
                radky = []
            self._otisky = frozenset(r for r in radky if r)
            self._mtime = mtime
        return self._otisky

    def obsahuje(self, o):
        with self._zamek:
            return o in self._nacti()

    def pridej(self, o):
        """True = nově připsán."""
        with self._zamek:
            if o in self._nacti():
                return False
            os.makedirs(os.path.dirname(self.cesta) or ".", exist_ok=True)
            with open(self.cesta, "a", encoding="utf-8") as f:
                f.write(o + "\n")
            return True


def nacti_aplikaci(data_dir):
    try:
        with open(os.path.join(data_dir, APLIKACE), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def uloz_aplikaci(data_dir, zmeny):
    data = {**nacti_aplikaci(data_dir), **zmeny}
    os.makedirs(data_dir, exist_ok=True)
    cesta = os.path.join(data_dir, APLIKACE)
    with open(cesta + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(cesta + ".tmp", cesta)
    return data
