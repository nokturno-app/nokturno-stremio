"""Anonymní statistiky doplňku pro Stremio — stejný sběrný bod jako Kodi a HA.

Jedna instalace = jedno nastavení doplňku (otisk adresy), ne jeden server: kdo si
doplněk přidal s vlastní adresou, má vlastní složku jádra (`Enginy`) a v ní
vlastní `stats.json` s náhodným id. Z adresy se nic neposílá — ani účty, ani
otisk. Jde jen verze, které zdroje má to nastavení zapnuté a u kterých titulů se
otevřely streamy (`core/lib/stats.py`), nejvýš jednou za 6 hodin.

Vypnout jde proměnnou prostředí `NOKTURNO_STATS=0`. I pak se ale při otevření
streamů nejvýš jednou za 6 hodin pošle ping — jen náhodné id, produkt a verze,
aby bylo vidět, že nastavení žije. Tituly ani zdroje ne.

Hlášení i ping navíc nesou pole `server` (od 9.0.6): náhodné id tohoto spuštění
aplikace (`<data>/instance.json`), druh běhu (`NOKTURNO_BEH` ze zavaděče), OS,
architekturu, příznak soukromé instance a počet nastavení použitých za 24 h.
Žádná adresa, doména, cesta ani jméno — jen id, kódy a čísla.
"""
import json
import logging
import os
import platform
import re
import threading
import time
import uuid
from collections import OrderedDict

from .core.engine import split_episode_id
from .core.lib.stats import COLLECT_URL, Stats

_LOGGER = logging.getLogger(__name__)
VYPNUTO = ("0", "false", "ne", "no", "off")
ZAPNUTO = ("1", "true", "ano", "yes", "on")
ID_RE = re.compile(r"^[0-9a-f]{32}$")
DEN = 86400


def id_serveru(data_dir):
    """Náhodné id instance v `<data>/instance.json`; vznikne jednou, restart ho nemění."""
    cesta = os.path.join(data_dir, "instance.json")
    try:
        with open(cesta, encoding="utf-8") as f:
            ident = json.load(f).get("id")
        if isinstance(ident, str) and ID_RE.match(ident):
            return ident
    except (OSError, ValueError, AttributeError):
        pass
    ident = uuid.uuid4().hex
    try:
        os.makedirs(data_dir, exist_ok=True)
        tmp = cesta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"id": ident}, f)
        os.replace(tmp, cesta)
    except OSError as err:
        _LOGGER.debug("instance.json: %s", err)
    return ident


def druh_behu(env=None):
    """Kód z `NOKTURNO_BEH` (nastaví zavaděč); bez zavaděče Docker nebo zdrojáky."""
    env = os.environ if env is None else env
    return env.get("NOKTURNO_BEH") or ("docker" if os.path.exists("/.dockerenv") else "python")


def system_os():
    s = platform.system().lower()
    return {"darwin": "macos"}.get(s, s)[:16] or "unknown"


class Statistiky:
    def __init__(self, verze, zapnuto=True, url=COLLECT_URL, data_dir=None):
        self.verze = verze
        self.data_dir = data_dir
        self._id = None
        self._pouzito = {}            # složka jádra -> čas posledního použití (počet za 24 h)
        self.zapnuto = zapnuto
        self.url = url
        self._zamek = threading.Lock()
        self._stats = OrderedDict()   # jádra vypadávají z LRU, tohle by jinak rostlo navždy
        self.limit = 50

    @classmethod
    def z_prostredi(cls, verze, environ=None, data_dir=None):
        env = os.environ if environ is None else environ
        return cls(verze, zapnuto=str(env.get("NOKTURNO_STATS", "1")).strip().lower() not in VYPNUTO,
                   data_dir=data_dir)

    def server(self):
        """Pole `server` do hlášení i pingu; bez datové složky nic (testy, nic kam uložit id)."""
        if not self.data_dir:
            return {}
        if self._id is None:
            self._id = id_serveru(self.data_dir)
        return {"server": {
            "id": self._id, "run": druh_behu(), "os": system_os(), "arch": platform.machine()[:20],
            "private": str(os.environ.get("NOKTURNO_SOUKROMA", "")).strip().lower() in ZAPNUTO,
            "configs": len(self._pouzito),
        }}

    def zaznamenej(self, engine, ctype, item_id, aplikace="stremio"):
        """Po zobrazení streamů — na pozadí, odpověď Stremiu kvůli tomu nečeká.

        `aplikace` je appka podle User-Agentu (`routes.klient_z_useragent`) —
        „nuvio" / „streamlet" / „stremio"."""
        cil = self.zpracuj if self.zapnuto else self.ping
        args = (engine, aplikace) if cil == self.ping else (engine, ctype, item_id, aplikace)
        threading.Thread(target=cil, args=args, daemon=True, name="nokturno-statistiky").start()

    def _pro(self, engine):
        slozka = engine.store.dir
        with self._zamek:
            stats = self._stats.get(slozka)
            if stats is None:
                stats = self._stats[slozka] = Stats(slozka)
                while len(self._stats) > self.limit:
                    self._stats.popitem(last=False)
            else:
                self._stats.move_to_end(slozka)
            ted = time.time()
            self._pouzito[slozka] = ted
            for k in [k for k, t in self._pouzito.items() if t < ted - DEN]:
                del self._pouzito[k]
        return stats

    def ping(self, engine, aplikace="stremio"):
        """Vypnuté statistiky: jen „nastavení žije" (id, produkt, verze). Nikdy nevyhodí výjimku."""
        try:
            stats = self._pro(engine)
            with self._zamek:
                if not stats.due():
                    return
                ok, why = stats.send(self.url, version=self.verze, agent="Stremio nokturno",
                                     product="stremio", client=aplikace, ping=True,
                                     extra=self.server())
            if not ok:
                _LOGGER.info("ping neodeslán: %s", why)
        except Exception as err:  # noqa: BLE001 – statistiky nesmí nic shodit
            _LOGGER.debug("ping: %s", err)

    def zpracuj(self, engine, ctype, item_id, aplikace="stremio"):
        """Synchronní část (vlákno výš, testy přímo). Nikdy nevyhodí výjimku."""
        try:
            stats = self._pro(engine)
            title, year, kind = self._titul(engine, ctype, item_id)
            with self._zamek:
                stats.note_play(item_id, title, year, kind)
                if not stats.due():
                    return
                zdroje = [k for k, v in engine.sources().items() if v]
                ok, why = stats.send(self.url, version=self.verze, platform="Stremio", lang="cs",
                                     agent="Stremio nokturno", sources=zdroje, product="stremio",
                                     client=aplikace, extra=self.server())
            if not ok:
                _LOGGER.info("statistiky neodeslány: %s", why)
        except Exception as err:  # noqa: BLE001 – statistiky nesmí nic shodit
            _LOGGER.debug("statistiky: %s", err)

    @staticmethod
    def _titul(engine, ctype, item_id):
        """Titul pro statistiky — vždy název seriálu/filmu, nikdy epizody: server
        slučuje statistiky podle normalizovaného názvu (`db.canonical_key`), takže
        skutečný název konkrétní epizody by rozštěpil sledovanost jednoho seriálu
        na tolik „titulů", kolik různých epizod se sledovalo."""
        _base, season, _episode = split_episode_id(item_id)
        kind = "series" if season is not None or ctype == "series" else "movie"
        try:
            meta, _video = engine.meta(ctype, item_id)
        except Exception:  # noqa: BLE001 – název je jen pro čitelnost přehledu
            return "", None, kind
        title = meta.get("_title") or meta.get("name") or ""
        year = str(meta.get("year") or meta.get("releaseInfo") or "")[:4]
        return title, (int(year) if year.isdigit() else None), kind
