"""Nastavení doplňku — účty zdrojů a předvolby streamů.

Jádro bere nastavení jako obyčejný slovník (`Engine(options, storage_dir)`),
takže tenhle modul jen sbírá hodnoty z prostředí a překládá je na klíče, které
engine čte. Názvy klíčů drží `core/lib/const.py`, ne tento soubor.

Nastavení může přijít dvěma cestami:

  z prostředí     `from_environ()` — jedna konfigurace pro celou službu
  z adresy        `decode()` — `/c/<konfigurace>/manifest.json`, vlastní pro
                  každého, kdo si doplněk přidá; tak to dělá Stremio

Adresa z konfigurace se vyrábí na `/configure` a nese účty **v otevřené podobě**,
jen zakódované do base64. Není to šifra a nemá být — takhle fungují všechny
doplňky Stremia. Adresu proto nikomu neposílat. Viz `pristupy.md` projektu.

**Luna se ve Stremiu nepoužívá** (od 0.2.5). Má vlastní doplněk do Stremia, takže
by se soubory z WebShare zdvojovaly, a její odkazy vedou na server v domácí síti —
přes veřejnou adresu by nešly přehrát. WebShare zůstává, protože Nokturno dává
podepsaný odkaz rovnou na WebShare, který jde přehrát odkudkoli. Klíče Luny se
z prostředí ani z adresy nepřebírají; starší adresy, které je nesou, fungují dál.
"""
import base64
import datetime
import json
import os
import re

from .core.lib import concertcat
from .core.lib.const import LANGS, SORT_ORDERS
from .core.lib.dash_api import DISCOVER_PARAMS
from .core.lib import mycat as mycat_lib

# proměnná prostředí → klíč nastavení, který čte engine
# TMDB se ve formuláři záměrně nenabízí: popisy a názvy si ve Stremiu řeší
# katalogový doplněk, ne my. Na dohledání souborů klíč vliv nemá — ověřeno na
# Pelíškách, které Cinemeta zná jako „Cosy Dens": český název dodá i veřejný
# katalog Sosáče, takže výsledek je s klíčem i bez něj stejný.
# Proto tu `NOKTURNO_TMDB_KEY` **není**: klíč instance rozdává `Enginy` každému
# jádru zvlášť (viz `enginy.Enginy.__init__`), protože bez něj nejde přeložit
# `tmdb:` id od klientů. Do adresy s nastavením ani do formuláře nepatří.
PROSTREDI = {
    "NOKTURNO_WS_USERNAME": "ws_username",
    "NOKTURNO_WS_PASSWORD": "ws_password",
    "NOKTURNO_STREAMUJ_USERNAME": "streamuj_username",
    "NOKTURNO_STREAMUJ_PASSWORD": "streamuj_password",
    "NOKTURNO_HS_ENABLED": "hs_enabled",
    "NOKTURNO_ST_EMAIL": "st_email",
    "NOKTURNO_ST_PASSWORD": "st_password",
    "NOKTURNO_FS_USERNAME": "fs_username",
    "NOKTURNO_FS_PASSWORD": "fs_password",
    # „sdilej" = účet ze Sdilej.cz, týž katalog jako FastShare (jádro lib/fastshare_api)
    "NOKTURNO_FS_PROVIDER": "fs_provider",
    # FastShare přímo ze zdroje (`behaviorHints.proxyHeaders`), ne přes `/play/` aplikace —
    # umí Nuvio a Stremio na PC, Stremio pro Android hlavičky nepošle (viz mapping.PRES_HLAVICKY)
    "NOKTURNO_FS_PRIMO": "fs_primo",
    "NOKTURNO_PT_EMAIL": "pt_email",
    "NOKTURNO_PT_PASSWORD": "pt_password",
    # vlastní klíč Last.fm pro katalogy koncertů (jen v profilu, nikdy v adrese ani v logu)
    "NOKTURNO_LASTFM_KEY": "lastfm_key",
    # zapnuté katalogy, klíče oddělené čárkou — viz nokturno/katalogy.py
    "NOKTURNO_KATALOGY": "katalogy",
    "NOKTURNO_PREF_LANG": "pref_lang",
    "NOKTURNO_PREF_SURROUND": "pref_surround",
    "NOKTURNO_HIDE_SD": "hide_sd",
    "NOKTURNO_HIDE_3D": "hide_3d",
    "NOKTURNO_HIDE_LOWQ": "hide_lowq",
    "NOKTURNO_MAX_BITRATE": "max_bitrate_mbps",
    "NOKTURNO_SORT": "sort_streams",
    # vlastní úložiště (WebDAV), až tři — viz core/lib/storage_api.py
    **{f"NOKTURNO_DAV{n}_{pole.upper()}": f"dav{n}_{pole}"
       for n in (1, 2, 3) for pole in ("url", "username", "password", "name")},
}
PRAVDA = ("1", "true", "yes", "ano", "on")
# identita uživatele (`identita.py`) — jde jen z adresy, nikdy z prostředí; tvar hlídá `identita.TVAR`
ID_KLIC = "id"
ID_RE = re.compile(r"^(?:[0-9a-f]{8}\.)?[0-9a-f]{16}\.[0-9a-f]{16}$")   # s časem vydání (6.1.3) i bez
# klíč k zapečetěným tokenům CZtoru (`cztor.py`) — jen z adresy, vyrábí ho párování ve formuláři
CZ_KLIC = "cz"
CZ_RE = re.compile(r"^[0-9a-f]{32}$")
# jazyk hlášek doplňku (streamy, 410) — jen z adresy; slovenský formulář ukládá „sk“, čeština se neukládá
JAZYK_KLIC = "jazyk"
# vlastní katalogy (`katalogy.py`) — jen z adresy, formulář je ukládá jako JSON řetězec
VK_KLIC = "vk"
VK_MAX = 20   # nastavení je v profilu, ne v adrese; ověřované katalogy se počítají do `mycat.MAX_VERIFIED` na zařízení
VK_KLICOVA_SLOVA = mycat_lib.KEYWORDS   # témata (pohádky, Vánoce…): TMDB je má jen jako klíčová slova
LASTFM_RE = re.compile(r"^[0-9a-f]{1,64}$")
VK_RAZENI = ("popularity.desc", "vote_average.desc", "primary_release_date.desc", mycat_lib.ALPHA)
# žánry koncertů (štítky Last.fm, `concertcat.TAGS`) oddělené čárkou; prázdné = koncerty vypnuté
KONCERTY_KLIC = "koncerty_zanry"
# volitelné katalogy TMDB z karty Katalogy (do 10.0.0b2) → vlastní katalogy v režimu Katalog z TMDB
STARE_TMDB = {"tmdb.popularni.filmy": ("movie", "popularity.desc", "Populární filmy", "Populárne filmy"),
              "tmdb.nejlepsi.filmy": ("movie", "vote_average.desc", "Nejlépe hodnocené filmy", "Najlepšie hodnotené filmy"),
              "tmdb.popularni.serialy": ("series", "popularity.desc", "Populární seriály", "Populárne seriály"),
              "tmdb.nejlepsi.serialy": ("series", "vote_average.desc", "Nejlépe hodnocené seriály",
                                        "Najlepšie hodnotené seriály")}
# klíče, u kterých engine čeká pravdivostní hodnotu, ne řetězec
LOGICKE = ("hs_enabled", "pref_surround", "hide_sd", "hide_3d", "hide_lowq", "fs_primo")

VYCHOZI = {
    "sort_streams": "quality",   # ve Stremiu je vidět jen několik prvních řádků
    "pref_lang": "CZ",
    "hs_enabled": False,         # úložiště třetích stran jsou volitelná, zapíná je uživatel
    "hide_lowq": True,           # nahrávky z kina (CAM, TS…) se skrývají, pokud je k dispozici něco lepšího
}


def _cislo(hodnota):
    try:
        return float(str(hodnota).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def from_mapping(raw):
    """Slovník surových hodnot → nastavení pro `Engine`.

    Nezná prostředí ani URL, jen převádí typy a zahazuje nesmysly, takže ho umí
    použít i configure stránka z fáze 4.
    """
    options = dict(VYCHOZI)
    stare_koncerty = []   # beta 1 měla koncerty jako druh vlastního katalogu (`t == "koncert"`)
    for key, value in (raw or {}).items():
        if value is None or (key not in set(PROSTREDI.values()) and key not in (ID_KLIC, CZ_KLIC, JAZYK_KLIC, VK_KLIC, KONCERTY_KLIC)):
            continue
        if key == CZ_KLIC:
            if isinstance(value, str) and CZ_RE.match(value.strip()):
                options[key] = value.strip()
                options["cz_enabled"] = True   # přepínač jádra; bez spárování ho `Engine.cz` stejně vypne
            continue
        if key == "lastfm_key":
            if isinstance(value, str) and LASTFM_RE.match(value.strip().lower()):
                options[key] = value.strip().lower()
            continue
        if key == KONCERTY_KLIC:
            zanry = koncerty_zanry(value)
            if zanry:
                options[key] = ",".join(zanry)
            continue
        if key == VK_KLIC:
            stare_koncerty = _stare_koncerty(value)
            vk = vlastni_katalogy(value)
            if vk:
                options[key] = json.dumps(vk, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            continue
        if key == JAZYK_KLIC:
            if str(value).strip().lower() == "sk":
                options[key] = "sk"
            continue
        if key == ID_KLIC:
            if isinstance(value, str) and ID_RE.match(value.strip()):
                options[key] = value.strip()
            continue
        if key in LOGICKE:
            options[key] = str(value).strip().lower() in PRAVDA if isinstance(value, str) else bool(value)
        elif key == "max_bitrate_mbps":
            options[key] = _cislo(value)
        elif key == "katalogy":
            kusy = value if isinstance(value, (list, tuple)) else str(value).split(",")
            options[key] = ",".join(sorted({str(k).strip() for k in kusy if str(k).strip()}))
        else:
            options[key] = str(value).strip()

    _stare_tmdb(options)
    if stare_koncerty and KONCERTY_KLIC not in options:
        options[KONCERTY_KLIC] = ",".join(koncerty_zanry(stare_koncerty))
    # „nezáleží" nese formulář jako ANY: prázdnou hodnotu by z adresy zahodil a server
    # dosadil výchozí CZ (2026-09-14)
    if options.get("pref_lang") == "ANY":
        options["pref_lang"] = ""
    # nepovolená hodnota by v jádru propadla na výchozí, ale tiše — lepší ji srovnat tady
    if options.get("pref_lang") not in LANGS:
        options["pref_lang"] = ""
    # výchozí FastShare v nastavení nenechávat, ať se otisk nastavení nezmění
    if options.get("fs_provider") != "sdilej":
        options.pop("fs_provider", None)
    if not options.get("fs_primo"):
        options.pop("fs_primo", None)   # vypnuto = výchozí, otisk se nemění
    if options.get("sort_streams") not in SORT_ORDERS:
        options["sort_streams"] = VYCHOZI["sort_streams"]
    # Přehraj.to je ve Stremiu per-uživatel jako ostatní zdroje — účet z adresy/prostředí.
    # Jádro zapíná zdroj přepínačem `pt_enabled`; ten se ve Stremiu odvodí z vyplněného
    # účtu (bez účtu API nevydá token a HTML z jedné serverové IP by dostalo 429, takže
    # anonymní režim jako v Kodi tu nedává smysl — nutný účet, stejně jako u Sledujteto).
    if str(options.get("pt_email") or "").strip() and str(options.get("pt_password") or "").strip():
        options["pt_enabled"] = True   # jen když je účet; jinak klíč vůbec není (čistý otisk)
    return options


def _vk_jeden(c):
    """Jeden katalog z formuláře → čistý záznam, nebo None. Hodnoty jsou od kohokoli."""
    if not isinstance(c, dict):
        return None
    if c.get("t") == "koncert":   # koncerty mají vlastní sekci (`KONCERTY_KLIC`), převádí je `from_mapping`
        return None
    try:
        g = [int(x) for x in c.get("g") or []][:10]
    except (TypeError, ValueError):
        g = []
    out = {"n": " ".join(str(c.get("n") or "").split())[:40], "t": "series" if c.get("t") == "series" else "movie",
           "g": [x for x in g if 0 < x < 1000000], "k": [k for k in c.get("k") or [] if k in VK_KLICOVA_SLOVA][:mycat_lib.MAX_KEYWORDS],
           "j": "or" if c.get("j") == "or" else "and"}
    try:
        posl = int(c.get("posl") or 0)
    except (TypeError, ValueError):
        posl = 0
    if 1 <= posl <= 50:   # posledních X let: počítá se při každém dotazu, `od`/`do` se pak ignorují
        out["posl"] = posl
    for pole, param in (("l", "with_original_language"), ("od", "year_from"), ("do", "year_to")):
        hodnota = str(c.get(pole) or "").strip()
        if hodnota and DISCOVER_PARAMS[param].match(hodnota) and "posl" not in out:
            out[pole] = hodnota
    zeme = mycat_lib.countries_of({"countries": [str(x) for x in c.get("zeme") or [] if isinstance(x, str)]})
    if zeme:   # země původu; starý „původní jazyk“ `l` dál platí, formulář ho při úpravě převede na zemi
        out["zeme"] = zeme
    if c.get("s") in VK_RAZENI:
        out["s"] = c["s"]
    if c.get("ov") in (1, True, "1", "true"):   # ověřování dostupnosti streamů (`overovani.py`)
        out["ov"] = 1
        q = mycat_lib.norm_quality(c.get("q"))
        if q:
            out["q"] = f"{q:g}"
        for pole in ("a", "ti"):
            if c.get(pole) in mycat_lib.TRACKS and c.get(pole):
                out[pole] = c[pole]
        if c.get("ch") in (1, True, "1", "true"):
            out["ch"] = 1
        out["z"] = c.get("z") if c.get("z") in mycat_lib.SHOWS else "found"
    return out if out["n"] else None


def _seznam(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return value if isinstance(value, list) else []


def _stare_tmdb(options):
    """Populární a Nejlépe hodnocené z karty Katalogy se převedou na vlastní katalogy (karta zmizela)."""
    kusy = [k for k in str(options.get("katalogy") or "").split(",") if k]
    stare = [k for k in kusy if k in STARE_TMDB]
    if not stare:
        return
    vk = vlastni_katalogy(options.get(VK_KLIC))
    mame = {(c["t"], c.get("s"), tuple(c.get("g") or ()), c.get("ov")) for c in vk}
    for k in stare:
        typ, razeni, cs, sk = STARE_TMDB[k]
        if (typ, razeni, (), None) not in mame and len(vk) < VK_MAX:
            vk.append({"n": sk if options.get(JAZYK_KLIC) == "sk" else cs, "t": typ, "g": [], "k": [], "j": "and",
                       "s": razeni})
    zbytek = [k for k in kusy if k not in STARE_TMDB]
    if zbytek:
        options["katalogy"] = ",".join(zbytek)
    else:
        options.pop("katalogy", None)
    if vk:
        options[VK_KLIC] = json.dumps(vlastni_katalogy(vk), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _stare_koncerty(value):
    """Žánry koncertních katalogů bety 1 z hodnoty `vk`."""
    return [str(t) for c in _seznam(value) if isinstance(c, dict) and c.get("t") == "koncert" for t in c.get("g") or []]


def koncerty_zanry(value):
    """Hodnota `koncerty_zanry` (čárkami nebo seznam) → platné štítky v pořadí `concertcat.TAGS`."""
    kusy = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
    chtene = {str(k).strip() for k in kusy}
    return [t for t in concertcat.TAGS if t in chtene]


def vlastni_katalogy(value):
    """Hodnota `vk` (JSON řetězec nebo seznam) → nejvýš `VK_MAX` čistých katalogů."""
    return [c for c in (_vk_jeden(x) for x in _seznam(value)[:VK_MAX]) if c][:VK_MAX]


def vk_parametry(c):
    """Čistý katalog → parametry `DashApi.discover` (jako `mycat_params` v Kodi)."""
    year_from, year_to = c.get("od") or "", c.get("do") or ""
    if c.get("posl"):
        year_from, year_to = str(datetime.date.today().year - int(c["posl"]) + 1), ""
    params = {"with_genres": ("|" if c.get("j") == "or" else ",").join(str(x) for x in c.get("g") or []),
              "with_keywords": "|".join(VK_KLICOVA_SLOVA[k] for k in c.get("k") or []),
              "with_origin_country": "|".join(c.get("zeme") or []),
              "with_original_language": "" if c.get("zeme") else c.get("l") or "", "year_from": year_from,
              "year_to": year_to,
              "sort_by": "popularity.desc" if c.get("s") == mycat_lib.ALPHA else c.get("s") or ""}
    return {k: v for k, v in params.items() if v}


def vk_definice(c):
    """Požadavky na stream ověřovaného katalogu → argumenty `Engine.verify_title` (po typu a id)."""
    return (mycat_lib.norm_quality(c.get("q")), bool(c.get("ch")), c.get("a") or "", c.get("ti") or "")


def from_environ(environ=None):
    """Nastavení z proměnných prostředí `NOKTURNO_*`."""
    env = environ if environ is not None else os.environ
    return from_mapping({klic: env[promenna] for promenna, klic in PROSTREDI.items() if promenna in env})


# --- nastavení v adrese doplňku ------------------------------------------

def encode(options):
    """Nastavení do jednoho kousku adresy.

    Prázdné hodnoty se vynechají, klíče se řadí — stejné nastavení tak dá vždy
    stejnou adresu a uživateli se doplněk po přenastavení neduplikuje.
    """
    ulozit = {k: v for k, v in sorted((options or {}).items()) if v not in ("", None)}
    syrove = json.dumps(ulozit, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(syrove.encode("utf-8")).decode("ascii").rstrip("=")


def decode(kousek):
    """Zpátky na nastavení. Vrací None, když to nastavení není.

    Prochází přes `from_mapping()`, takže na neznámé klíče a nesmyslné hodnoty
    platí stejná pravidla jako u prostředí — z adresy je nelze podstrčit.
    """
    try:
        doplneni = "=" * (-len(kousek) % 4)
        data = json.loads(base64.urlsafe_b64decode(kousek + doplneni).decode("utf-8"))
    except Exception:  # noqa: BLE001 – cokoli nerozluštitelného prostě není nastavení
        return None
    return from_mapping(data) if isinstance(data, dict) else None


def bez_lokalnich_uloziste(options, resolve=None):
    """Nastavení bez úložišť, na která se z internetu nesmí (viz `sit.zakazana`).

    Požadavek z internetu nese adresu úložiště od kohokoli. Doplněk pak tu adresu
    prochází a soubory z ní přes sebe streamuje, takže bez téhle pojistky by šlo
    přes Funnel sahat na služby, které poslouchají jen na localhostu (dashboard,
    Apache na :8080), na metadata cloudu (169.254.x), do domácí sítě (CoreELEC,
    Home Assistant) i do tailnetu. Uživatel zvenku na naši LAN stejně nedosáhne,
    takže o nic nepřijde; domácí požadavek se tudy nevede vůbec.

    Tohle je rychlé odmítnutí podle DNS v nastavení. Skutečnou pojistkou je
    `sit.verejny_opener()`, který hlídá adresu až při navázání spojení — DNS
    může podruhé vrátit něco jiného a cizí server může přesměrovat.
    """
    import urllib.parse

    from . import sit

    resolve = resolve or sit.resolvuj
    out = dict(options or {})
    for n in (1, 2, 3):
        url = str(out.get(f"dav{n}_url") or "").strip()
        if not url:
            continue
        host = urllib.parse.urlsplit(url if "://" in url else "http://" + url).hostname or ""
        try:
            adresy = list(resolve(host))
        except OSError:
            adresy = []
        if not adresy or any(sit.zakazana(a) for a in adresy):
            for pole in ("url", "username", "password", "name"):
                out.pop(f"dav{n}_{pole}", None)
    return out


def fingerprint(options):
    """Krátký otisk nastavení — jméno složky s cache a klíč do cache enginů.

    Hesla se do něj nepromítají čitelně, takže může do logu i do jména složky.
    """
    import hashlib
    return hashlib.sha256(encode(options).encode("ascii")).hexdigest()[:16]


NAZVY_ZDROJU = {"luna": "Luna", "sosac": "Sosáč", "webshare": "WebShare",
                "hellspy": "HellSpy", "sledujteto": "Sledujteto", "fastshare": "FastShare",
                "prehrajto": "Přehraj.to", "cztor": "CZtor", "storage": "vlastní úložiště"}


def sources_summary(engine):
    """Které zdroje jsou nastavené — do logu při startu.

    WebShare se hlásí podle vyplněných údajů, ne podle přihlášení; to je síťové volání.
    """
    return [NAZVY_ZDROJU[k] for k, zapnuto in engine.sources().items() if zapnuto and k in NAZVY_ZDROJU]


def ma_ucty(options):
    """Má nastavení vlastní přihlašovací údaje nebo úložiště? Jejich otisk (`fingerprint`)
    je pak jedinečný pro uživatele, takže na něj jde počítat limity místo sdílené IP.
    Nastavení jen s HellSpy a volbami sdílí spousta lidí — to takové není."""
    o = options or {}
    return any(str(o.get(k) or "").strip() for k in (
        "ws_username", "streamuj_username", "st_email", "fs_username", "pt_email", CZ_KLIC,
        "dav1_url", "dav2_url", "dav3_url"))


def sources_from_options(options):
    """Totéž jen z nastavení, bez jádra — pro manifest. Manifest se dřív ptal jádra,
    a to znamenalo založit ho i se složkou na disku pro každou adresu, kterou kdo
    poslal (jeden GET na náhodný base64 = nová složka navždy; audit 2026-09-14)."""
    o = options or {}
    zapnuto = {
        "storage": any(str(o.get(f"dav{n}_url") or "").strip() for n in (1, 2, 3)),
        "sosac": bool(str(o.get("streamuj_username") or "").strip()),
        "webshare": bool(str(o.get("ws_username") or "").strip()),
        "hellspy": bool(o.get("hs_enabled")),
        "sledujteto": bool(str(o.get("st_email") or "").strip()),
        "fastshare": bool(str(o.get("fs_username") or "").strip()),
        "prehrajto": bool(str(o.get("pt_email") or "").strip()),
        "cztor": bool(o.get(CZ_KLIC)),
    }
    return [NAZVY_ZDROJU[k] for k, v in zapnuto.items() if v]
