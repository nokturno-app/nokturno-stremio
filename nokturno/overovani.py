"""Ověřené vlastní katalogy: katalog s volbou `ov` ukáže jen tituly, ke kterým se našel stream podle
požadavků (kvalita, zvuk, titulky, 5.1). Kandidáty (`/discover`) a výsledky drží index z jádra
(`catindex`) v `overeni/<klíč>.json`; plní ho jediné vlákno po jednom titulu za `interval`, takže
katalog se zaplňuje postupně a dokud aplikace běží.

Index je podle otisku nastavení (účty a volby) a definice katalogu, ne podle profilu: víc profilů
se stejnými účty a stejným katalogem sdílí jeden index. Do synchronizace Stremio nepatří.
"""
import hashlib
import json
import logging
import os
import threading
import time

from . import config
from .core.lib import catindex, concertcat, concertfilter
from .core.lib.mycat import POOL_EVERY, pool_for
from .core.lib.store import Store

_LOGGER = logging.getLogger(__name__)

PRVNI_DAVKA = 30   # katalog bez jediného ověřeného titulu dostane úvodní dávku najednou, ať není hned prázdný
STARE = 14 * 86400   # soubor indexu, na který se 14 dní nesáhlo (katalog smazán), se uklidí


class Overovani:
    def __init__(self, data_dir, enginy, dash, profily, interval=60):
        self.store = Store(os.path.join(data_dir, "overeni"))
        self.enginy, self.dash, self.profily, self.interval = enginy, dash, profily, interval
        self._dalsi = 0
        self._uklid = 0

    @staticmethod
    def klic(options, cat):
        """Klíč indexu: účty a volby bez identity/jazyka/katalogů + definice katalogu bez názvu a řazení zobrazení."""
        ucty = {k: v for k, v in (options or {}).items() if k not in (config.ID_KLIC, config.JAZYK_KLIC, config.VK_KLIC)}
        definice = {k: v for k, v in cat.items() if k not in ("n", "z")}
        syrove = json.dumps([config.fingerprint(ucty), definice], sort_keys=True, separators=(",", ":"))
        return "o" + hashlib.sha1(syrove.encode("utf-8")).hexdigest()[:20]

    def _index(self, options, cat):
        index = self.store.load(self.klic(options, cat), {})
        sig = catindex.signature(*config.vk_definice(cat))
        return index if isinstance(index, dict) and index.get("sig") == sig else {}

    def polozky(self, options, cat):
        """Metadata vyhovujících titulů v pořadí podle `z`; prázdné, dokud se nic neověřilo."""
        index = self._index(options, cat)
        if cat.get("t") == "koncert":
            return concertcat.visible(index, cat.get("z") or "pool")
        return catindex.visible(index, sort=cat.get("z") or "found")

    def _cile(self):
        """Ověřované katalogy všech uložených profilů, bez duplicit (stejný klíč indexu)."""
        videne, out = set(), []
        for p in self.profily.seznam():
            kousek = self.profily.nacti(p["klic"])
            options = config.decode(kousek) if kousek else None
            for cat in config.vlastni_katalogy((options or {}).get(config.VK_KLIC)):
                klic = self.klic(options, cat)
                if cat.get("ov") and klic not in videne:
                    videne.add(klic)
                    out.append((options, cat, klic))
        return out

    def krok(self):
        """Jedno ověření jednoho titulu jednoho katalogu (round-robin); vrací True, když se něco zkusilo."""
        now = int(time.time())
        if now - self._uklid >= 86400:
            self._uklid = now
            self._uklid_souboru(now)
        cile = self._cile()
        if not cile:
            return False
        options, cat, klic = cile[self._dalsi % len(cile)]
        self._dalsi += 1
        definice = config.vk_definice(cat)
        sig = catindex.signature(*definice)
        index = self.store.reload(klic, {})
        if not isinstance(index, dict) or index.get("sig") != sig:
            index = {"sig": sig}
        if now - int(index.get("pool_ts") or 0) >= POOL_EVERY and now - int(index.get("pool_try") or 0) >= 1800:
            index["pool_try"] = now   # selhání (špatný klíč, výpadek) se nezkouší každou minutu
            kandidati = self._pool(options, cat)
            if kandidati is not None:
                catindex.merge_pool(index, kandidati, now)
                index["pool_ts"] = now
        prvni = catindex.counts(index)[0] == 0
        batch = catindex.next_batch(index, now, PRVNI_DAVKA if prvni else 1)
        if cat.get("t") == "koncert":
            jmena = [(e.get("meta") or {}).get("name") or "" for e in (index.get("items") or {}).values()]
        for mid in batch:
            try:
                engine = self.enginy.pro(options)
                with engine.background():
                    if cat.get("t") == "koncert":
                        nazev = ((index["items"].get(mid) or {}).get("meta") or {}).get("name") or ""
                        files = concertcat.search(engine, nazev, concertfilter.rivals(nazev, jmena)) if nazev else None
                        vysledek = None if files is None else bool(files)
                    else:
                        vysledek = engine.verify_title(cat["t"], mid, *definice)
            except Exception as err:  # noqa: BLE001 – výpadek zdroje = zkusit později
                _LOGGER.debug("ověření %s: %s", mid, err)
                vysledek, files = None, None
            catindex.record(index, mid, vysledek, int(time.time()))
            if cat.get("t") == "koncert":
                entry = index["items"][mid]
                if vysledek:
                    entry["files"] = files
                else:
                    entry.pop("files", None)
        self.store.save(klic, index)
        return bool(batch)

    def _pool(self, options, cat):
        """Kandidáti katalogu: filmy a seriály z dashboardu, interpreti z Last.fm (jen s klíčem v profilu)."""
        if cat.get("t") != "koncert":
            return pool_for(self.dash, cat["t"], config.vk_parametry(cat))
        klic = str((options or {}).get("lastfm_key") or "")
        if not klic:
            return None
        try:
            return concertcat.pool(klic, cat.get("g") or [])
        except concertcat.ConcertError as err:
            _LOGGER.debug("Last.fm: %s", err)
            return None

    def _uklid_souboru(self, now):
        try:
            for name in os.listdir(self.store.dir):
                cesta = os.path.join(self.store.dir, name)
                if name.endswith(".json") and now - os.path.getmtime(cesta) > STARE:
                    os.remove(cesta)
        except OSError:
            pass

    def start(self):
        def smycka():
            while True:
                time.sleep(self.interval)
                try:
                    self.krok()
                except Exception:  # noqa: BLE001 – vlákno nesmí spadnout
                    _LOGGER.warning("ověřování katalogů selhalo", exc_info=True)

        threading.Thread(target=smycka, daemon=True, name="nokturno-overovani").start()
