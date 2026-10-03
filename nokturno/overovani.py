"""Ověřené vlastní katalogy: katalog s volbou `ov` ukáže jen tituly, ke kterým se našel stream podle
požadavků (kvalita, zvuk, titulky, 5.1). Kandidáty (`/discover`) a výsledky drží index z jádra
(`catindex`) v `overeni/<klíč>.json`; plní ho jediné vlákno po jednom titulu za `interval`, takže
katalog se zaplňuje postupně a dokud aplikace běží.

Koncerty (volba profilu `koncerty_zanry` + klíč Last.fm) mají vlastní index `concertcat` ve složce
`overeni/k<klíč>/`; plní ho totéž vlákno v tomtéž kole.

Index je podle otisku nastavení (účty a volby) a definice katalogu, ne podle profilu: víc profilů
se stejnými účty a stejným katalogem sdílí jeden index. Do synchronizace Stremio nepatří.
"""
import hashlib
import json
import logging
import os
import shutil
import threading
import time

from . import config
from .core.lib import catindex, concertcat
from .core.lib.mycat import FIRST_BATCH, POOL_EVERY, pool_for
from .core.lib.store import Store

_LOGGER = logging.getLogger(__name__)

PRVNI_DAVKA = FIRST_BATCH   # katalog bez jediného ověřeného titulu dostane úvodní dávku najednou, ať není hned prázdný
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
        ucty = {k: v for k, v in (options or {}).items()
                if k not in (config.ID_KLIC, config.JAZYK_KLIC, config.VK_KLIC, config.KONCERTY_KLIC)}
        definice = {k: v for k, v in cat.items() if k not in ("n", "z")}
        syrove = json.dumps([config.fingerprint(ucty), definice], sort_keys=True, separators=(",", ":"))
        return "o" + hashlib.sha1(syrove.encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def klic_koncertu(options):
        """Klíč indexu koncertů: otisk účtů (bez katalogů) + žánry. None = koncerty vypnuté nebo bez klíče Last.fm."""
        options = options or {}
        zanry = options.get(config.KONCERTY_KLIC) or ""
        if not zanry or not options.get("lastfm_key"):
            return None
        ucty = {k: v for k, v in options.items()
                if k not in (config.ID_KLIC, config.JAZYK_KLIC, config.VK_KLIC, config.KONCERTY_KLIC)}
        syrove = json.dumps([config.fingerprint(ucty), zanry], sort_keys=True, separators=(",", ":"))
        return "k" + hashlib.sha1(syrove.encode("utf-8")).hexdigest()[:20]

    def _koncerty_store(self, options):
        klic = self.klic_koncertu(options)
        if klic is None:
            return None
        store = Store(os.path.join(self.store.dir, klic))
        tags = config.koncerty_zanry(options.get(config.KONCERTY_KLIC))
        if concertcat.config(store)["tags"] != tags:
            concertcat.configure(store, tags)
        return store

    def koncerty(self, options):
        """Index koncertů profilu (struktura `catindex`), `{}`, dokud se nic nenašlo nebo jsou vypnuté."""
        store = self._koncerty_store(options)
        return concertcat.load_index(store) if store is not None else {}

    def _index(self, options, cat):
        index = self.store.load(self.klic(options, cat), {})
        sig = catindex.signature(*config.vk_definice(cat))
        return index if isinstance(index, dict) and index.get("sig") == sig else {}

    def polozky(self, options, cat):
        """Metadata vyhovujících titulů v pořadí podle `z`; prázdné, dokud se nic neověřilo."""
        return catindex.visible(self._index(options, cat), sort=cat.get("z") or "found")

    def _cile(self):
        """Ověřované katalogy všech uložených profilů, bez duplicit (stejný klíč indexu)."""
        videne, out = set(), []
        for p in self.profily.seznam():
            kousek = self.profily.nacti(p["klic"])
            options = config.decode(kousek) if kousek else None
            for cat in config.vlastni_katalogy((options or {}).get(config.VK_KLIC)):
                klic = self.klic(options, cat)
                if cat.get("ov") and self.dash is not None and klic not in videne:
                    videne.add(klic)
                    out.append((options, cat, klic))
            klic = self.klic_koncertu(options)
            if klic is not None and klic not in videne:
                videne.add(klic)
                out.append((options, None, klic))
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
        if cat is None:
            return self._krok_koncerty(options)
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
        for mid in batch:
            try:
                engine = self.enginy.pro(options)
                with engine.background():
                    vysledek = engine.verify_title(cat["t"], mid, *definice)
            except Exception as err:  # noqa: BLE001 – výpadek zdroje = zkusit později
                _LOGGER.debug("ověření %s: %s", mid, err)
                vysledek = None
            catindex.record(index, mid, vysledek, int(time.time()))
        self.store.save(klic, index)
        return bool(batch)

    def _krok_koncerty(self, options):
        """Jedna dávka koncertů (`concertcat.refresh`); nic nalezeno = úvodní dávka."""
        store = self._koncerty_store(options)
        if store is None:
            return False
        prvni = not concertcat.recent(concertcat.load_index(store), 1)
        try:
            return bool(concertcat.refresh(self.enginy.pro(options), store, PRVNI_DAVKA if prvni else 1))
        except Exception as err:  # noqa: BLE001 – výpadek zdroje = zkusit později
            _LOGGER.debug("koncerty: %s", err)
            return False

    def _pool(self, options, cat):
        return pool_for(self.dash, cat["t"], config.vk_parametry(cat))

    def _uklid_souboru(self, now):
        try:
            for name in os.listdir(self.store.dir):
                cesta = os.path.join(self.store.dir, name)
                if name.endswith(".json") and now - os.path.getmtime(cesta) > STARE:
                    os.remove(cesta)
                elif name.startswith("k") and os.path.isdir(cesta):   # koncerty: podle indexu, ne podle složky
                    index = os.path.join(cesta, concertcat.INDEX + ".json")
                    if now - os.path.getmtime(index if os.path.exists(index) else cesta) > STARE:
                        shutil.rmtree(cesta, ignore_errors=True)
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
