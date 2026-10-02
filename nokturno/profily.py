"""Profily nastavení: krátká adresa doplňku `/c/<klíč>/…` místo nastavení zakódovaného v adrese.

Dřív formulář zakódoval celé nastavení včetně hesel do adresy doplňku (`config.encode`):
každá změna znamenala novou adresu, doplněk se musel ve Stremiu odebrat a přidat znovu,
a Stremio si adresy doplňků ukládá do účtu v cloudu — i s hesly v base64.

Nově aplikace nastavení uloží u sebe (`profily.json` v datové složce, práva 0600) pod náhodný
klíč a do adresy jde jen klíč. Klíč je tajemství: kdo ho zná, používá účty profilu, stejně jako
dřív s celou adresou. Staré dlouhé adresy fungují dál (skutečné nastavení začíná vždy `eyJ`,
s klíčem se nikdy nepletou).
"""
import json
import os
import re
import secrets
import threading
import time

from .core.lib import qr

PROFILY = "profily.json"
KLIC_RE = re.compile(r"^p[A-Za-z0-9_-]{22}$")
MAX_PROFILU = 1000


def je_klic(text):
    return isinstance(text, str) and bool(KLIC_RE.match(text))


class Profily:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.cesta = os.path.join(data_dir, PROFILY)
        self._zamek = threading.Lock()
        self._mtime = None
        self._data = {}

    def _nacti(self):
        try:
            mtime = os.stat(self.cesta).st_mtime_ns
        except OSError:
            mtime = None
        if mtime != self._mtime:
            try:
                with open(self.cesta, encoding="utf-8") as f:
                    data = json.load(f)
                self._data = data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                self._data = {}   # chybějící i poškozený soubor = prázdné profily
            self._mtime = mtime
        return self._data

    def nacti(self, klic):
        """Uložený kousek (výstup `config.encode`); None = neexistuje / neplatný klíč."""
        if not je_klic(klic):
            return None
        with self._zamek:
            zaznam = self._nacti().get(klic)
        n = zaznam.get("n") if isinstance(zaznam, dict) else None
        return n if isinstance(n, str) else None

    def uloz(self, kousek, klic=None, prevod=False):
        """Uloží kousek, vrátí klíč. Bez (nebo s neplatným) klíče vznikne nový; strop `MAX_PROFILU`
        platí jen pro nové klíče (`ValueError`), přepis stávajícího projde vždy."""
        with self._zamek:
            data = dict(self._nacti())
            if not je_klic(klic):
                # převod staré adresy otevřené podruhé = týž klíč, ne další profil; jinak je každé
                # uložení bez klíče nový profil, i se stejným nastavením (víc profilů na tytéž účty)
                stejny = prevod and next((k for k, z in data.items() if isinstance(z, dict) and z.get("n") == kousek), None)
                if stejny:
                    return stejny
                if len(data) >= MAX_PROFILU:
                    raise ValueError("příliš mnoho profilů")
                klic = "p" + secrets.token_urlsafe(16)
            stary = data.get(klic) if isinstance(data.get(klic), dict) else {}
            data[klic] = {**stary, "n": kousek, "t": int(time.time())}
            self._zapis(data)
            return klic

    def _zapis(self, data):
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.cesta + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f)
        try:
            os.chmod(self.cesta + ".tmp", 0o600)
        except OSError:
            pass   # Windows / FAT
        os.replace(self.cesta + ".tmp", self.cesta)
        self._mtime = None   # příští čtení načte čerstvě

    def seznam(self):
        """Uložené profily od posledně změněného: [{klic, jmeno, t}] – pro správu ve formuláři."""
        with self._zamek:
            data = self._nacti()
        out = [{"klic": k, "jmeno": z.get("j") or "", "t": z.get("t") or 0}
               for k, z in data.items() if je_klic(k) and isinstance(z, dict)]
        return sorted(out, key=lambda p: -p["t"])

    def pojmenuj(self, klic, jmeno):
        """False = profil neexistuje. Jméno jen pro přehled, nejvýš 40 znaků."""
        with self._zamek:
            data = dict(self._nacti())
            if not je_klic(klic) or not isinstance(data.get(klic), dict):
                return False
            data[klic] = {**data[klic], "j": " ".join(str(jmeno or "").split())[:40]}
            self._zapis(data)
            return True

    def smaz(self, klic):
        with self._zamek:
            data = dict(self._nacti())
            if data.pop(klic, None) is None:
                return False
            self._zapis(data)
            return True


def qr_svg(text):
    """QR kód jako SVG s okrajem 4 moduly. Do SVG jdou jen čísla, nic z požadavku."""
    m = qr.encode(text)
    n = len(m) + 8
    tahy = "".join(f"M{x + 4} {y + 4}h1v1h-1z" for y, radek in enumerate(m) for x, tmavy in enumerate(radek) if tmavy)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges" '
            f'role="img" aria-label="QR kód"><rect width="100%" height="100%" fill="#fff"/>'
            f'<path fill="#000" d="{tahy}"/></svg>')
