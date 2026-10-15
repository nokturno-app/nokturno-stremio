"""Profily nastavení: krátká adresa doplňku `/c/<klíč>/…` místo nastavení zakódovaného v adrese.

Dřív formulář zakódoval celé nastavení včetně hesel do adresy doplňku (`config.encode`):
každá změna znamenala novou adresu, doplněk se musel ve Stremiu odebrat a přidat znovu,
a Stremio si adresy doplňků ukládá do účtu v cloudu — i s hesly v base64.

Nově aplikace nastavení uloží u sebe (`profily.json` v datové složce, práva 0600) pod náhodný
klíč a do adresy jde jen klíč. Klíč je tajemství: kdo ho zná, používá účty profilu, stejně jako
dřív s celou adresou. Staré dlouhé adresy fungují dál (skutečné nastavení začíná vždy `eyJ`,
s klíčem se nikdy nepletou), pokud je správce nevypne.

Od 11.0.0 má profil vlastníka (`o`, jméno uživatele z `uzivatele.py`; chybí = správce) a klíč
dál dává jen právo přehrávat – upravit profil smí vlastník nebo správce (viz `routes.Router`).
Využití profilu (poslední aktivita a počet přehrání za den, ne co se hraje) drží `Vyuziti`.
"""
import json
import os
import re
import secrets
import threading
import time

from .core.lib import qr

PROFILY = "profily.json"
VYUZITI = "vyuziti.json"
KLIC_RE = re.compile(r"^p[A-Za-z0-9_-]{22}$")
MAX_PROFILU = 1000


def je_klic(text):
    return isinstance(text, str) and bool(KLIC_RE.match(text))


class JsonSoubor:
    """Slovník v JSON souboru v datové složce (0600), čtený znovu jen po změně souboru."""

    def __init__(self, data_dir, jmeno):
        self.data_dir = data_dir
        self.cesta = os.path.join(data_dir, jmeno)
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
                self._data = {}   # chybějící i poškozený soubor = prázdný
            self._mtime = mtime
        return self._data

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

    def smaz_soubor(self):
        with self._zamek:
            try:
                os.remove(self.cesta)
            except OSError:
                pass
            self._mtime, self._data = None, {}


class Profily(JsonSoubor):
    def __init__(self, data_dir):
        super().__init__(data_dir, PROFILY)

    def zaznam(self, klic):
        """Celý záznam profilu (`n` nastavení, `j` název, `o` vlastník, `t` změna); None = neexistuje."""
        if not je_klic(klic):
            return None
        with self._zamek:
            z = self._nacti().get(klic)
        return dict(z) if isinstance(z, dict) and isinstance(z.get("n"), str) else None

    def nacti(self, klic):
        """Uložený kousek (výstup `config.encode`); None = neexistuje / neplatný klíč."""
        z = self.zaznam(klic)
        return z["n"] if z else None

    def uloz(self, kousek, klic=None, prevod=False, vlastnik=""):
        """Uloží kousek, vrátí klíč. Bez (nebo s neplatným) klíče vznikne nový profil s vlastníkem
        `vlastnik` ("" = správce); strop `MAX_PROFILU` platí jen pro nové klíče (`ValueError`),
        přepis stávajícího projde vždy a vlastníka nemění."""
        with self._zamek:
            data = dict(self._nacti())
            if not je_klic(klic):
                # převod staré adresy otevřené podruhé = týž klíč, ne další profil (jen u téhož vlastníka);
                # jinak je každé uložení bez klíče nový profil, i se stejným nastavením
                stejny = prevod and next((k for k, z in data.items() if isinstance(z, dict) and z.get("n") == kousek
                                          and (z.get("o") or "") == vlastnik), None)
                if stejny:
                    return stejny
                if len(data) >= MAX_PROFILU:
                    raise ValueError("příliš mnoho profilů")
                klic = "p" + secrets.token_urlsafe(16)
                stary = {"o": vlastnik} if vlastnik else {}
            else:
                stary = data.get(klic) if isinstance(data.get(klic), dict) else {}
            data[klic] = {**stary, "n": kousek, "t": int(time.time())}
            self._zapis(data)
            return klic

    def seznam(self):
        """Uložené profily od posledně změněného: [{klic, jmeno, t, o}] – pro správu ve formuláři."""
        with self._zamek:
            data = self._nacti()
        out = [{"klic": k, "jmeno": z.get("j") or "", "t": z.get("t") or 0, "o": z.get("o") or ""}
               for k, z in data.items() if je_klic(k) and isinstance(z, dict)]
        return sorted(out, key=lambda p: -p["t"])

    def _zmen(self, klic, zmena):
        with self._zamek:
            data = dict(self._nacti())
            if not je_klic(klic) or not isinstance(data.get(klic), dict):
                return False
            data[klic] = {**data[klic], **zmena}
            self._zapis(data)
            return True

    def pojmenuj(self, klic, jmeno):
        """False = profil neexistuje. Jméno jen pro přehled, nejvýš 40 znaků."""
        return self._zmen(klic, {"j": " ".join(str(jmeno or "").split())[:40]})

    def predej(self, klic, vlastnik):
        """Nový vlastník ("" = správce). Klíč ani nastavení se nemění."""
        return self._zmen(klic, {"o": vlastnik})

    def smaz(self, klic):
        with self._zamek:
            data = dict(self._nacti())
            if data.pop(klic, None) is None:
                return False
            self._zapis(data)
            return True

    def smaz_vlastnika(self, vlastnik):
        """Smaže všechny profily uživatele, vrátí jejich klíče."""
        with self._zamek:
            data = dict(self._nacti())
            pryc = [k for k, z in data.items() if isinstance(z, dict) and vlastnik and z.get("o") == vlastnik]
            for k in pryc:
                del data[k]
            if pryc:
                self._zapis(data)
            return pryc


class Vyuziti:
    """Kdy se profil naposledy ozval a kolikrát se z něj za den přehrávalo – pro přehled správce.

    Jen čísla, žádné tituly. V paměti, na disk (`vyuziti.json`) jde `uloz()` z údržby na pozadí;
    při pádu se ztratí nejvýš poslední interval. Přehrání = `/play/` s jiným souborem než posledně,
    nebo s týmž po 3 hodinách (přetáčení přes aplikaci posílá na `/play/` desítky dotazů).
    """
    DNU = 31
    ZNOVU_S = 3 * 3600

    def __init__(self, data_dir):
        self.cesta = os.path.join(data_dir, VYUZITI) if data_dir else None
        self._zamek = threading.Lock()
        self._posledni = {}   # klíč → (soubor, čas) posledního započítaného přehrání
        self._zmeneno = False
        try:
            with open(self.cesta, encoding="utf-8") as f:
                data = json.load(f)
            self._data = data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            self._data = {}

    def aktivita(self, klic, prehrani=None):
        ted = time.time()
        with self._zamek:
            z = self._data.setdefault(klic, {"a": 0, "d": {}})
            if ted - z.get("a", 0) >= 60 or prehrani:
                z["a"] = int(ted)
                self._zmeneno = True
            if prehrani:
                soubor, kdy = self._posledni.get(klic, ("", 0))
                if soubor != prehrani or ted - kdy > self.ZNOVU_S:
                    den = time.strftime("%Y-%m-%d", time.localtime(ted))
                    z.setdefault("d", {})[den] = z.get("d", {}).get(den, 0) + 1
                    self._posledni[klic] = (prehrani, ted)

    def souhrn(self, klice):
        """(poslední aktivita, přehrání za 7 dní, za 30 dní) za skupinu profilů."""
        hranice = {n: time.strftime("%Y-%m-%d", time.localtime(time.time() - (n - 1) * 86400)) for n in (7, 30)}
        posledni, p7, p30 = 0, 0, 0
        with self._zamek:
            for k in klice:
                z = self._data.get(k) or {}
                posledni = max(posledni, int(z.get("a") or 0))
                for den, n in (z.get("d") or {}).items():
                    p30 += n if den >= hranice[30] else 0
                    p7 += n if den >= hranice[7] else 0
        return posledni, p7, p30

    def zapomen(self, klice=None):
        with self._zamek:
            if klice is None:
                self._data, self._posledni = {}, {}
            for k in klice or ():
                self._data.pop(k, None)
            self._zmeneno = True

    def uloz(self):
        if not self.cesta:
            return
        with self._zamek:
            if not self._zmeneno:
                return
            hranice = time.strftime("%Y-%m-%d", time.localtime(time.time() - self.DNU * 86400))
            for z in self._data.values():
                z["d"] = {den: n for den, n in (z.get("d") or {}).items() if den >= hranice}
            data = json.dumps(self._data)
            self._zmeneno = False
        os.makedirs(os.path.dirname(self.cesta), exist_ok=True)
        with open(self.cesta + ".tmp", "w", encoding="utf-8") as f:
            f.write(data)
        try:
            os.chmod(self.cesta + ".tmp", 0o600)
        except OSError:
            pass
        os.replace(self.cesta + ".tmp", self.cesta)


def qr_svg(text):
    """QR kód jako SVG s okrajem 4 moduly. Do SVG jdou jen čísla, nic z požadavku."""
    m = qr.encode(text)
    n = len(m) + 8
    tahy = "".join(f"M{x + 4} {y + 4}h1v1h-1z" for y, radek in enumerate(m) for x, tmavy in enumerate(radek) if tmavy)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges" '
            f'role="img" aria-label="QR kód"><rect width="100%" height="100%" fill="#fff"/>'
            f'<path fill="#000" d="{tahy}"/></svg>')
