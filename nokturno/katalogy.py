"""Volitelné katalogy pro Stremio — stejné seznamy jako v doplňku pro Kodi, bez žánrů a roků.

Každý katalog si uživatel zapne zvlášť ve formuláři (volba `katalogy`, klíče oddělené
čárkou). Výpis na účtech nezávisí, a proto je **jeden pro všechny adresy doplňku**:
vlastní `Store` ve složce `katalogy`, platnost 6 h. Server se tak Sosáče i TMDB ptá
nanejvýš jednou za 6 hodin na katalog a stránku, ať doplněk používá kolik lidí chce —
to byla obava, kvůli které byly katalogy dřív zamítnuté (dokumentace, fáze 3).

Položky nesou jen IMDb id (`tt…`). Detail titulu i díly seriálu si pak Stremio vezme
z Cinemety a streamy od Nokturna jako u každého jiného titulu, takže doplněk nemusí
dodávat metadata. Titul bez IMDb id se vynechá (u Sosáče výjimečně čerstvé přírůstky).

TMDB potřebuje API klíč. Ve formuláři ho záměrně nemáme, bere se klíč instance
z prostředí (`NOKTURNO_TMDB_KEY`); bez něj se katalogy TMDB nenabídnou.

Katalogy „Nově přidané s CZ/SK dabingem / titulky“ (filmy i seriály) od 9.0.0 nejsou (právní plán,
bod 11); klíč ze starší adresy doplňku se tiše ignoruje, `vybrane()` bere jen známé klíče.

Katalogy z dashboardu (obrazovka Katalogy — Vánoce, Film pro dnešní den…) se ve formuláři
nenabízejí a jsou v manifestu vždy, první v pořadí. Mění se bez vydání doplňku: platnost
i pořadí počítá server. Složku Stremio neumí, takže podkategorie jdou jako samostatné
katalogy s názvem „Vánoce: Komedie“.

Vlastní katalogy (od 8.5.0): uživatel si ve formuláři poskládá až dvacet katalogů z žánrů, témat, jazyka,
let a řazení (volba `vk`, viz `config.vlastni_katalogy`; s volbou `ov` jen tituly se streamem podle
požadavků, ověřuje je `overovani.py` na pozadí). Jdou v manifestu hned za katalogy
z dashboardu, tituly skládá dashboard (`DashApi.discover`, stránka po 20 titulech, cache 12 h
sdílená podle parametrů, ne podle uživatele). Stremio stránkuje po 100, proto se na jeden
dotaz stáhne tolik stránek dashboardu, kolik je potřeba.
"""
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from . import config

from .core.lib import concertcat
from .core.lib import mycat as mycat_lib
from .core.lib.sosac_direct import SosacDirect
from .core.lib.store import Store
from .core.lib.tmdb_api import TmdbApi
from .core.lib.trend_api import CATALOG_ID as TREND_CATALOG_ID, TrendApi

_LOGGER = logging.getLogger(__name__)

TTL = 6 * 3600
PREFIX = "nokturno."
STRANKA_SOSAC = 100   # Sosáč vydává dlouhé seznamy, TMDB stránkuje po 20 samo
TMDB_IMG = "https://image.tmdb.org/"
METAHUB_POSTER = "https://images.metahub.space/poster/medium/{}/img"

# klíč, typ, zdroj, id katalogu ve zdroji, název česky, název slovensky
SEZNAM = (
    ("trend.nejsledovanejsi.filmy", "movie", "trend", TREND_CATALOG_ID,
     "Nejsledovanější filmy tento týden", "Najsledovanejšie filmy tento týždeň"),
    ("trend.nejsledovanejsi.serialy", "series", "trend", TREND_CATALOG_ID,
     "Nejsledovanější seriály tento týden", "Najsledovanejšie seriály tento týždeň"),
    ("sosac.popularni.filmy", "movie", "sosac", "moviesmostpopular",
     "Nejpopulárnější filmy", "Najpopulárnejšie filmy"),
    ("sosac.nove.filmy", "movie", "sosac", "moviesrecentlyadded",
     "Nově přidané filmy", "Nedávno pridané filmy"),
    ("sosac.popularni.serialy", "series", "sosac", "tvshowsmostpopular",
     "Nejpopulárnější seriály", "Najpopulárnejšie seriály"),
    ("tmdb.trendy.filmy", "movie", "tmdb", "trending", "Trendy filmy tento týden", "Trendy filmy tento týždeň"),
    ("tmdb.popularni.filmy", "movie", "tmdb", "popular", "Populární filmy", "Populárne filmy"),
    ("tmdb.nejlepsi.filmy", "movie", "tmdb", "top_rated", "Nejlépe hodnocené filmy", "Najlepšie hodnotené filmy"),
    ("tmdb.trendy.serialy", "series", "tmdb", "trending", "Trendy seriály tento týden", "Trendy seriály tento týždeň"),
    ("tmdb.popularni.serialy", "series", "tmdb", "popular", "Populární seriály", "Populárne seriály"),
    ("tmdb.nejlepsi.serialy", "series", "tmdb", "top_rated", "Nejlépe hodnocené seriály", "Najlepšie hodnotené seriály"),
)

# Nabídka ve formuláři (2026-09-15) sjednocená s menu Filmy/Seriály v Kodi — „Populární na
# TMDB“, „Nejsledovanější tento týden“, „Nejlépe hodnocené“. Ostatní řádky `SEZNAM` fungují
# jen tomu, kdo je má uložené v adrese; nový uživatel se k nim ve formuláři nedostane.
DOPORUCENE = {
    "trend.nejsledovanejsi.filmy", "trend.nejsledovanejsi.serialy",
    "tmdb.popularni.filmy", "tmdb.popularni.serialy",
    "tmdb.nejlepsi.filmy", "tmdb.nejlepsi.serialy",
}


def nahled(typ, meta):
    """Metadata ze zdroje → `metaPreview` Stremia. Bez IMDb id None."""
    imdb = str(meta.get("imdb_id") or meta.get("id") or "")
    if not imdb.startswith("tt"):
        return None
    out = {"id": imdb, "type": typ, "name": meta.get("name") or "", "posterShape": "poster"}
    # obrázky Sosáče (movies.sosac.tv) mimo jeho web přesměrují na stránku a jsou na šířku —
    # plakát proto z TMDB, jinak podle IMDb id z metahubu (ten používá i Cinemeta)
    poster = str(meta.get("poster") or "")
    out["poster"] = poster if poster.startswith(TMDB_IMG) else METAHUB_POSTER.format(imdb)
    if str(meta.get("background") or "").startswith(TMDB_IMG):
        out["background"] = meta["background"]
    if meta.get("description"):
        out["description"] = meta["description"]
    rok = str(meta.get("year") or meta.get("releaseInfo") or "")[:4]
    if rok:
        out["releaseInfo"] = rok
    if meta.get("genres"):
        out["genres"] = list(meta["genres"])[:5]
    try:
        if meta.get("imdbRating"):
            out["imdbRating"] = f"{float(meta['imdbRating']):.1f}"
    except (TypeError, ValueError):
        pass
    return out


# katalogy TMDB přes dashboard; nejlépe hodnocené jen s dost hlasy (jinak vyhrají obskurní tituly)
TMDB_DASH = {"popular": {"sort_by": "popularity.desc"},
             "top_rated": {"sort_by": "vote_average.desc", "vote_count_gte": {"movie": "2000", "series": "1000"}}}
DASH = "dash."   # klíč katalogu z dashboardu: `dash.<slug>`
VK = "vk."       # vlastní katalog: `vk.<pořadí>`
STRANKA_VK = 100
KONCERTY = "Koncerty"   # vlastní typ Stremia pro koncerty (volba profilu `koncerty_zanry`)
KPREFIX = "nktk:"       # id: `nktk:<interpret>` (meta), `nktk:<interpret>:<koncert>` (stream)
K_NOVE, K_ABECEDA = "koncerty.nove", "koncerty.abeceda"
ZANRY_KONCERTU = {"czech": "Česká scéna", "slovak": "Slovenská scéna", "czech rock": "Český rock",
                  "classic rock": "Classic rock", "hard rock": "Hard rock", "metal": "Metal", "rock": "Rock",
                  "pop": "Pop", "punk": "Punk", "hip-hop": "Hip-hop", "jazz": "Jazz", "electronic": "Elektronika",
                  "folk": "Folk", "classical": "Klasika", "reggae": "Reggae", "world": "World"}
ZANRY_KONCERTU_SK = {"czech": "Česká scéna", "slovak": "Slovenská scéna", "czech rock": "Český rock",
                     "electronic": "Elektronika", "classical": "Klasika"}
ZDROJE_KONCERTU = {"ws": "WebShare", "hs": "HellSpy", "fs": "FastShare"}
STRANKA_DISCOVER = 20
DISCOVER_STRAN = 10   # víc stránek dashboard nevydá (`DISCOVER_MAX_PAGE`)


def katalogy_dashboardu(polozky, predpona=""):
    """Strom menu z `DashApi.menu()` → ploché (slug, typ, název); složka se rozloží na potomky."""
    out = []
    for p in polozky:
        nazev = f"{predpona}{p['title']}"
        if p["children"]:
            out += katalogy_dashboardu(p["children"], f"{nazev}: ")
        else:
            out.append((p["slug"], p["kind"], nazev))
    return out


class Katalogy:
    def __init__(self, data_dir, tmdb_key="", ttl=TTL, dash=None):
        self.store = Store(os.path.join(data_dir, "katalogy"))
        self.ttl = ttl
        self.sosac = SosacDirect(cache=self.store, index_store=self.store.index())
        self.tmdb = TmdbApi(tmdb_key, cache=self.store) if str(tmdb_key or "").strip() else None
        self.trend = TrendApi(cache=self.store)
        self.dash = dash   # DashApi, None = katalogy z dashboardu se nenabízejí
        self.overovani = None   # `overovani.Overovani` (server ho připojí); bez něj se `ov` ignoruje

    def dostupne(self):
        # TMDB bez vlastního klíče: tituly skládá dashboard (`DashApi.discover`)
        return [radek for radek in SEZNAM
                if radek[2] != "tmdb" or self.tmdb is not None or (self.dash is not None and radek[3] in TMDB_DASH)]

    def formular(self, jazyk="cs"):
        """Nabídka pro formulář — jen doporučené katalogy (`DOPORUCENE`), které tahle
        instance umí. Starší katalogy z `SEZNAM` (mimo `DOPORUCENE`) se ve formuláři
        novým uživatelům nenabízejí, ale `dostupne()`/`vybrane()`/`manifest()` je
        pořád umí vyřešit — kdo je má uložené v adrese, nic mu nepřestane fungovat."""
        return [{"klic": klic, "typ": typ, "nazev": sk if jazyk == "sk" else cs}
                for klic, typ, _zdroj, _cid, cs, sk in self.dostupne() if klic in DOPORUCENE]

    def vybrane(self, options):
        chtene = {x.strip() for x in str((options or {}).get("katalogy") or "").split(",") if x.strip()}
        return [radek for radek in self.dostupne() if radek[0] in chtene]

    def z_dashboardu(self):
        if self.dash is None:
            return []
        try:
            return katalogy_dashboardu(self.dash.menu())
        except Exception as err:  # noqa: BLE001 – výpadek dashboardu nesmí shodit manifest
            _LOGGER.warning("katalogy z dashboardu: %s", err)
            return []

    def vlastni(self, options):
        return config.vlastni_katalogy((options or {}).get(config.VK_KLIC)) if self.dash is not None else []

    def ma_koncerty(self, options):
        return self.overovani is not None and self.overovani.klic_koncertu(options) is not None

    @staticmethod
    def _zanr_nazev(tag, jazyk="cs"):
        return (ZANRY_KONCERTU_SK.get(tag) if jazyk == "sk" else None) or ZANRY_KONCERTU.get(tag, tag)

    def _koncerty_manifest(self, options, jazyk):
        if not self.ma_koncerty(options):
            return []
        zanry = [self._zanr_nazev(t, jazyk) for t in config.koncerty_zanry(options.get(config.KONCERTY_KLIC))]
        nove = "Nové pridané" if jazyk == "sk" else "Nově přidané"
        abeceda = "Podľa abecedy" if jazyk == "sk" else "Podle abecedy"
        return [{"type": KONCERTY, "id": PREFIX + K_NOVE, "name": nove},
                {"type": KONCERTY, "id": PREFIX + K_ABECEDA, "name": abeceda,
                 "extra": [{"name": "genre", "isRequired": False, "options": zanry},
                           {"name": "search", "isRequired": False},
                           {"name": "skip", "isRequired": False}]}]

    @staticmethod
    def _koncert_nahled(a, popis=""):
        out = {"id": KPREFIX + a["id"][2:], "type": KONCERTY, "name": a["name"], "posterShape": "square",
               "description": popis or f"Koncertů: {len(concertcat.group(a['files'], a['name']))}"}
        img = concertcat.image(a["files"])
        return dict(out, poster=img) if img else out

    def _koncerty_polozky(self, options, klic, skip, zanr, jazyk="cs", hledej=""):
        if hledej and klic == K_ABECEDA:
            return [self._koncert_nahled(a) for a in self.overovani.hledej_koncerty(options, hledej)][skip:skip + STRANKA_VK]
        index = self.overovani.koncerty(options)
        if klic == K_NOVE:   # interpreti podle nejnovějšího koncertu, celé najednou
            if skip:
                return []
            videne, out = set(), []
            for c in concertcat.recent(index, 500):
                if c["artist_id"] not in videne:
                    videne.add(c["artist_id"])
                    nazev = f"{c['title']} ({c['year']})" if c["year"] else c["title"]
                    out.append(self._koncert_nahled({"id": c["artist_id"], "name": c["artist"], "files": c["files"]},
                                                    nazev))
            return out[:STRANKA_VK]
        tag = next((t for t in concertcat.TAGS if zanr and zanr in (t, self._zanr_nazev(t, jazyk),
                                                                     self._zanr_nazev(t))), None)
        if zanr and tag is None:
            return []
        rows = concertcat.by_tag(index, tag) if tag else sorted(
            (a for letter in concertcat.letters(index) for a in concertcat.by_letter(index, letter)),
            key=lambda a: a["name"].casefold())
        return [self._koncert_nahled(a) for a in rows][skip:skip + STRANKA_VK]

    def _koncert(self, options, item_id):
        """`nktk:<interpret>[:<koncert>]` → (interpret, koncerty z `concertcat.group`, číslo koncertu | None)."""
        casti = str(item_id).split(":")
        if casti[0] + ":" != KPREFIX or len(casti) not in (2, 3) or not self.ma_koncerty(options):
            return None
        index = self.overovani.koncerty(options)
        koncerty = concertcat.artist(index, "a:" + casti[1])
        if not koncerty:
            return None
        jmeno = (((index.get("items") or {}).get("a:" + casti[1]) or {}).get("meta") or {}).get("name") or casti[1]
        poradi = int(casti[2]) if len(casti) == 3 and casti[2].isdigit() else None
        return jmeno, koncerty, poradi

    def koncert_meta(self, options, item_id):
        """Meta interpreta: každý jeho koncert jako „video“. None = takové id tu není."""
        k = self._koncert(options, item_id)
        if k is None:
            return None
        jmeno, koncerty, _ = k
        videa = [dict({"id": f"{item_id}:{i}", "title": f"{g['title']} ({g['year']})" if g["year"] else g["title"],
                       "released": f"{g['year'] or 1970}-01-01T00:00:00.000Z"},
                      **({"thumbnail": concertcat.image(g["files"])} if concertcat.image(g["files"]) else {}))
                 for i, g in enumerate(koncerty)]
        out = {"id": item_id, "type": KONCERTY, "name": jmeno, "posterShape": "square", "videos": videa,
               "description": f"Koncertů: {len(koncerty)}"}
        img = next((v["thumbnail"] for v in videa if v.get("thumbnail")), "")
        return dict(out, poster=img, background=img) if img else out

    def koncert_soubory(self, options, item_id):
        """Soubory jednoho koncertu (id se 3 částmi) jako popisy streamů pro `mapping.streams_response`."""
        k = self._koncert(options, item_id)
        if k is None or k[2] is None or k[2] >= len(k[1]):
            return []
        return [{"url": f["ref"], "file": f["name"], "source": ZDROJE_KONCERTU.get(f["source"], ""),
                 "size_gb": round(f["size"] / 1000 ** 3, 2) if f.get("size") else 0,
                 "length_min": (f.get("duration") or 0) // 60} for f in k[1][k[2]]["files"]]

    def _vlastni_polozky(self, typ, cat, skip):
        """Náhledy `skip`–`skip+100` vlastního katalogu; stránky dashboardu souběžně."""
        if cat.get("s") == mycat_lib.ALPHA:   # nejoblíbenější podle filtrů, seřazené podle abecedy (stránky mají cache)
            pool = mycat_lib.pool_for(self.dash, typ, config.vk_parametry(cat), alpha=True) or []
            return [p for p in (nahled(typ, m) for m in pool) if p][skip:skip + STRANKA_VK]
        return self._discover_polozky(typ, config.vk_parametry(cat), skip)

    def _discover_polozky(self, typ, params, skip):
        prvni, stran = self.dash.discover(typ, params, page=1)
        if prvni is None:
            return []
        # +1 stránka jako rezerva na tituly bez IMDb id, které dashboard vynechá
        potreba = min(stran, DISCOVER_STRAN, (skip + STRANKA_VK) // STRANKA_DISCOVER + 2)
        with ThreadPoolExecutor(max_workers=5) as pool:
            dalsi = list(pool.map(lambda p: self.dash.discover(typ, params, page=p)[0] or [], range(2, potreba + 1)))
        videne, out = set(), []
        for meta in [m for stranka in [prvni] + dalsi for m in stranka]:
            nahl = nahled(typ, meta)
            if nahl and nahl["id"] not in videne:
                videne.add(nahl["id"])
                out.append(nahl)
        return out[skip:skip + STRANKA_VK]

    def manifest(self, options, jazyk="cs"):
        dashboard = [{"type": typ, "id": PREFIX + DASH + slug, "name": nazev}
                     for slug, typ, nazev in self.z_dashboardu()]
        vlastni = [{"type": c["t"], "id": f"{PREFIX}{VK}{i}", "name": c["n"],
                    "extra": [{"name": "skip", "isRequired": False}]}
                   for i, c in enumerate(self.vlastni(options))]
        return dashboard + vlastni + self._koncerty_manifest(options, jazyk) + [{"type": typ, "id": PREFIX + klic, "name": sk if jazyk == "sk" else cs,
                             "extra": [{"name": "skip", "isRequired": False}]}
                            for klic, typ, _zdroj, _cid, cs, sk in self.vybrane(options)]

    def polozky(self, typ, katalog_id, skip=0, options=None, zanr="", jazyk="cs", hledej=""):
        """Náhledy jedné stránky katalogu. None = takový katalog tahle instance nemá."""
        klic = katalog_id[len(PREFIX):] if str(katalog_id).startswith(PREFIX) else ""
        if klic in (K_NOVE, K_ABECEDA):
            if typ != KONCERTY or not self.ma_koncerty(options):
                return None
            return self._koncerty_polozky(options, klic, max(0, int(skip or 0)), zanr, jazyk, hledej)
        if klic.startswith(VK):
            vlastni = self.vlastni(options)
            poradi = klic[len(VK):]
            cat = vlastni[int(poradi)] if poradi.isdigit() and int(poradi) < len(vlastni) else None
            if cat is None or cat["t"] != typ:
                return None
            if cat.get("ov") and self.overovani is not None:
                # jen tituly, které ověřování označilo za vyhovující; plní se postupně na pozadí
                skip = max(0, int(skip or 0))
                metas = self.overovani.polozky(options, cat)
                return [p for p in (nahled(typ, m) for m in metas) if p][skip:skip + STRANKA_VK]
            try:
                return self._vlastni_polozky(typ, cat, max(0, int(skip or 0)))
            except Exception as err:  # noqa: BLE001 – výpadek dashboardu = prázdný katalog
                _LOGGER.warning("vlastní katalog %s: %s", klic, err)
                return []
        if klic.startswith(DASH) and self.dash is not None:
            # celý katalog najednou (server drží nejvýš 60 položek), cache má `DashApi`
            if skip:
                return []
            try:
                return [p for p in (nahled(typ, m) for m in self.dash.catalog(typ, klic[len(DASH):])) if p]
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("katalog %s: %s", klic, err)
                return []
        radek = next((r for r in self.dostupne() if r[0] == klic and r[1] == typ), None)
        if radek is None:
            return None
        try:
            skip = max(0, int(skip or 0))
        except (TypeError, ValueError):
            skip = 0
        _klic, typ, zdroj, cid = radek[:4]

        def load():
            if zdroj == "sosac":
                raw = self.sosac.catalog(typ, cid, skip=skip, page=STRANKA_SOSAC)
            elif zdroj == "trend":
                # vlastní žebříček dashboardu — nejvýš 50 položek, `TrendApi.catalog()`
                # sám vrátí prázdno pro skip > 0 (stránkování nemá co nabídnout)
                raw = self.trend.catalog(typ, cid, skip=skip)
            elif self.tmdb is None:
                params = {k: (v[typ] if isinstance(v, dict) else v) for k, v in TMDB_DASH[cid].items()}
                return self._discover_polozky(typ, params, skip)
            else:
                raw = self.tmdb.catalog(typ, cid, skip=skip)
            return [p for p in (nahled(typ, m) for m in raw or []) if p]
        try:
            # prázdný výsledek (výpadek zdroje) se nepamatuje — další dotaz zkusí znovu
            return self.store.cached_if(f"stremio:katalog:{klic}:{skip}", self.ttl, load) or []
        except Exception as err:  # noqa: BLE001 – výpadek zdroje = prázdný katalog, ne chyba služby
            _LOGGER.warning("katalog %s (skip %d): %s", klic, skip, err)
            return []
