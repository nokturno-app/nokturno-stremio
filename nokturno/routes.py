"""Endpointy protokolu Stremia nad jádrem Nokturna.

    GET /                                úvodní stránka a rozcestník repozitářů
    GET /configure                       formulář, který vyrobí adresu s účty
    GET /c/<nastavení>/manifest.json     co doplněk umí
    GET /c/<nastavení>/stream/:t/:id.json   streamy k titulu
    GET /c/<nastavení>/play/:payload     302 na skutečný soubor
    GET /c/<nastavení>/check             ověření účtů pro formulář (WebShare + VIP)
    GET /c/<klíč>/instalace              stránka pro mobil (QR): přidat do Stremia, Nuvia, zkopírovat
    GET /cztor/pin                       nový klíč a PIN pro párování CZtoru (viz cztor.py)
    GET /cztor/poll?k=<klíč>&t=<token>   čeká na potvrzení PINu na cztor.com/activate
    GET /health                          pro kontejner
    POST /profil                         uložení nastavení pod klíč profilu (viz profily.py)
    POST /profily, /profil/jmeno, /profil/smazat   seznam, pojmenování a smazání profilů (formulář)
    POST /povolit                        soukromá instance: připíše otisk nastavení (viz soukroma.py)
    POST /aplikace                       statistiky a hlášení o pádech z formuláře
    GET|POST /aktualizace                verze a kontrola aktualizace hned (jen pod zavaděčem)

Stremio nemá soubor nastavení — účty se nosí zakódované v cestě adresy, takže
každý, kdo si doplněk přidá, má vlastní. Server si nic nepamatuje a hledá vždy
pod účtem toho, kdo se ptá. Od 9.6.0 formulář nastavení ukládá do aplikace (profily.py)
a adresa nese jen klíč profilu; staré adresy s nastavením fungují dál.

Adresy bez `/c/<nastavení>/` fungují dál a berou nastavení z prostředí. Drží to
při životě instance nasazené dřív, než tahle vrstva vznikla. **Jen ze soukromé
sítě:** požadavek z internetu (Tailscale Funnel, viz `server.je_verejny`) bez
vlastního nastavení v adrese dostane 403 a formulář se mu nepředvyplní — jinak by
hledal a stahoval pod účty majitele instance a formulář by mu je ukázal.

Odkazy WebShare a HellSpy platí jen chvíli a nesou podpis, takže se nedávají
rovnou do odpovědi. Stremio dostane adresu na `/play/`, která soubor rozklíčuje
až ve chvíli, kdy se na ni přehrávač skutečně obrátí — a protože nese tentýž
prefix, rozklíčuje ho pod správným účtem.

Výjimkou jsou vlastní úložiště (`dav:`) a FastShare (`fs:`): ty chtějí u každého
požadavku autentizační hlavičku, kterou přehrávač nemá. `/play/` je proto nepřesměruje,
ale soubor přepošle sám (`Odpoved.proxy`, `server.Handler._proxy`).
"""
import html as html_lib
import json
import ipaddress
import logging
import os
import pathlib
import re
import threading
import time
import urllib.parse
import urllib.request


def verze_tuple(v):
    """„9.0.10“ → (9, 0, 10); nečíselné části jako 0."""
    return tuple(int(c) if c.isdigit() else 0 for c in str(v).split("."))

from .core.engine import NokturnoError, is_sosac_id, split_episode_id
from .core.lib.webshare_api import WebshareApi, WebshareError
from .core.lib.sledujteto_api import SledujtetoApi
from .core.lib.fastshare_api import FastshareApi
from .core.lib.prehrajto_api import PrehrajtoApi
from .core.lib.storage_api import SLOTS, StorageApi
from .core.lib.cztor_api import CztorError
from .core.lib import mycat
from . import config, cztor, mapping, profily, sit, soukroma
from .enginy import PrilisMnohoNovych
from .katalogy import KONCERTY
from .identita import Identita
from . import tls
from .kliky import Kliky

_LOGGER = logging.getLogger(__name__)

# Umělé zdržení hledání streamů v sekundách (NOKTURNO_STREAM_DELAY v .env), výchozí 0.
STREAM_DELAY = float(os.environ.get("NOKTURNO_STREAM_DELAY") or 0)

VERZE = "10.0.0b6"
TYPY = ("movie", "series")
CHECK_LIMIT = (10, 5 * 60)   # ověření účtů z jedné adresy za 5 minut — jinak je /check relay pro hádání hesel
# streamy z jedné IP klienta (IPv6 po /64, viz `klic_klienta`). Reálná data 2026-09-19: medián
# 2 titulů za den, 99. percentil 37, nejvíc 61; bot procházející katalog jich dělal stovky za
# vteřiny a nafoukl cache na 400 000 souborů. 60 za 10 min člověk nepřekročí, bot ano hned.
# Dřív se počítalo per otisk nastavení — jenže nastavení bez účtů (jen HellSpy a volby) sdílí
# spousta lidí, takže jeden bot vyčerpal limit, respektive blokaci, všem ostatním.
# Od 6.4.9 bez skutečných streamů pro adresy bez identity a bez účtů, takže se limity počítají
# jen na identitu (`id:`) nebo otisk s účty (`fp:`); adresa (IP) je jen strop nad nimi.
STREAM_LIMIT = (120, 10 * 60)
ID_LIMIT = (10, 3600)   # vydaných identit z jedné adresy za hodinu (formulář /configure)
# Audit 2026-09-19: `/play/` neměl limit vůbec (sto tisíc rozklíčování z jedné adresy = HellSpy
# 429 a jádro ho pak vypne všem na 10 minut), `/catalog` taky ne (každý `skip` = nový dotaz na
# TMDB/Sosáč). A s identitou v adrese se limity počítaly **místo** IP, takže N identit z jedné
# adresy = N × limit — proto je nad tím strop na IP (IPv6 po /64), který identita neobejde;
# je volnější než limit na uživatele, aby CGNAT s pár lidmi za jednou adresou nevadil.
PLAY_LIMIT = (120, 10 * 60)
KATALOG_LIMIT = (600, 10 * 60)
IP_STROP = (300, 10 * 60)
# párování CZtoru: každý PIN založí zařízení u CZtoru a soubor na disku, proto přísně;
# čekání na potvrzení se ptá po ~5 s a PIN platí asi 10 minut
CZ_PIN_LIMIT = (10, 3600)
CZ_POLL_LIMIT = (300, 10 * 60)
MAX_SKIP = 500


def klic_klienta(adresa):
    """Klíč do limitu: IPv4 celá, IPv6 jen prefix /64 — ten má jedna domácnost či stroj
    celý, takže by si bot jinak mohl adresu uvnitř něj měnit s každým požadavkem."""
    try:
        ip = ipaddress.ip_address((adresa or "").strip())
    except ValueError:
        return adresa or ""
    if ip.version == 6 and ip.ipv4_mapped:
        return str(ip.ipv4_mapped)
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


class Okno:
    """Počítadlo v klouzavém okně per klíč (adresa klienta).

    Při přetečení `max_keys` se vyhazují jen prošlá okna; když je i pak plno, nový klíč
    se odmítne — dřív se slovník mazal celý, takže 5001 různých /64 z jednoho IPv6 /48
    vynulovalo limity všem (audit 2026-09-19)."""

    def __init__(self, limit, window_s, max_keys=5000):
        self.limit, self.window_s, self.max_keys = limit, window_s, max_keys
        self._data = {}
        self._zamek = threading.Lock()

    def _uklid(self, now):
        hranice = now - self.window_s
        self._data = {k: v for k, v in self._data.items() if v[1] > hranice}

    def povolit(self, key):
        now = time.time()
        with self._zamek:
            if key not in self._data and len(self._data) >= self.max_keys:
                self._uklid(now)
                if len(self._data) >= self.max_keys:
                    return False
            count, start = self._data.get(key, (0, now))
            if now - start > self.window_s:
                count, start = 0, now
            if count >= self.limit:
                return False
            self._data[key] = (count + 1, start)
            return True


class Blokace:
    """Krátká automatická blokace adres, které opakovaně narážejí na limity streamů.

    Limit `STREAM_LIMIT` (na adresu, IPv6 po /64) bota jen zpomalí, ale nezastaví — dál mu
    odpovídá 429 a on tluče dál (v incidentu 2026-09-19 ~60 požadavků/s). Proto se počítají odmítnutí:
    `PRAH` za `okno_s` a adresa dostane na `doba_s` rovnou 403 bez další práce. Stav je jen v paměti (restart ho vynuluje) a týká se jen
    `/stream/` a `/play/` — jiné cesty se nikdy neblokují, adresu může sdílet víc lidí (CGNAT).
    Při přetečení `max_klicu` se vyhazují jen prošlé záznamy; blokace se nikdy nemažou naráz.
    """

    def __init__(self, prah=20, okno_s=10 * 60, doba_s=3600, max_klicu=5000, soubor=None, adresy_soubor=None):
        self.prah, self.okno_s, self.doba_s, self.max_klicu = prah, okno_s, doba_s, max_klicu
        self._odmitnuti = {}
        self._blok = {}
        self._kolikrat = {}
        self._zamek = threading.Lock()
        # identity (klíč `id:…`) odebrané natrvalo — druhá blokace téže identity; soubor přežije restart
        self.soubor = soubor
        self.adresy_soubor = adresy_soubor
        self._adresy, self._adresy_cteno = set(), -1e9
        self._z_dashboardu = frozenset()
        self.odebrane = set()
        if soubor:
            try:
                with open(soubor, encoding="utf-8") as f:
                    self.odebrane = {r.strip() for r in f if r.strip()}
            except OSError:
                pass

    def nastav_zakazane(self, ips):
        """Seznam z dashboardu (obrazovka Provoz); nahrazuje ten předchozí."""
        self._z_dashboardu = frozenset(klic_klienta(i) for i in ips if i)

    def adresa_zakazana(self, adresa):
        """Adresa (IPv4, IPv6 po /64) natvrdo zakázaná ručně v souboru `adresy_soubor` — jeden
        záznam na řádek, `#` komentář. Soubor se znovu čte nejvýš jednou za 30 s (změna platí bez restartu)."""
        if adresa and adresa in self._z_dashboardu:
            return True
        if not self.adresy_soubor:
            return False
        now = time.monotonic()
        if now - self._adresy_cteno > 30:
            self._adresy_cteno = now
            try:
                with open(self.adresy_soubor, encoding="utf-8") as f:
                    self._adresy = {klic_klienta(r.split("#")[0].strip()) for r in f if r.split("#")[0].strip()}
            except OSError:
                self._adresy = set()
        return bool(adresa) and adresa in self._adresy

    def odebrana(self, klic):
        return klic in self.odebrane

    def _odebrat(self, klic):
        self.odebrane.add(klic)
        if self.soubor:
            try:
                with open(self.soubor, "a", encoding="utf-8") as f:
                    f.write(klic + "\n")
            except OSError:
                _LOGGER.warning("odebrané identity se nepodařilo zapsat do %s", self.soubor)

    def blokovana(self, ip):
        if ip in self.odebrane:
            return True
        with self._zamek:
            do = self._blok.get(ip)
            if do is None:
                return False
            if time.time() >= do:
                del self._blok[ip]
                return False
            return True

    def prohresek(self, ip):
        """Zaznamená odmítnutí; vrátí True, když tím adresa právě dostala blokaci."""
        now = time.time()
        with self._zamek:
            if ip not in self._odmitnuti and len(self._odmitnuti) >= self.max_klicu:
                hranice = now - self.okno_s
                self._odmitnuti = {k: v for k, v in self._odmitnuti.items() if v[1] > hranice}
                if len(self._odmitnuti) >= self.max_klicu:
                    return False   # plno i po úklidu: nový klíč se nepočítá, staré blokace zůstávají
            pocet, start = self._odmitnuti.get(ip, (0, now))
            if now - start > self.okno_s:
                pocet, start = 0, now
            pocet += 1
            self._odmitnuti[ip] = (pocet, start)
            if pocet >= self.prah and ip not in self._blok:
                if len(self._blok) >= self.max_klicu:
                    self._blok = {k: do for k, do in self._blok.items() if do > now}
                    if len(self._blok) >= self.max_klicu:
                        return False
                self._blok[ip] = now + self.doba_s
                if ip.startswith("id:"):
                    self._kolikrat[ip] = self._kolikrat.get(ip, 0) + 1
                    if self._kolikrat[ip] >= 2:
                        self._odebrat(ip)   # identita podruhé v blokaci: natrvalo, ať si bot shání novou
                        _LOGGER.warning("identita odebrána natrvalo: %s", ip[3:11] + "…")
                return True
            return False


STATIKA = pathlib.Path(__file__).resolve().parent / "static"
JAZYKY = ("cs", "sk")   # stránky úvodu a formuláře; manifest a streamy zůstávají česky


def jazyk_z_hlavicky(accept_language):
    """Jazyk stránek podle `Accept-Language`: slovenština, jen když je první volbou prohlížeče.

    Bere se nejvyšší `q` (při shodě dřívější položka), `q=0` znamená „nechci".
    Cokoli jiného, prázdná nebo rozbitá hlavička → čeština.
    """
    kandidati = []
    for poradi, cast in enumerate((accept_language or "").split(",")):
        kusy = [k.strip() for k in cast.split(";")]
        tag = kusy[0].lower()
        if not tag:
            continue
        q = 1.0
        for parametr in kusy[1:]:
            if parametr.lower().startswith("q="):
                try:
                    q = float(parametr[2:])
                except ValueError:
                    q = 0.0
        if q > 0:
            kandidati.append((-q, poradi, tag))
    if not kandidati:
        return "cs"
    return "sk" if min(kandidati)[2].split("-")[0] == "sk" else "cs"


def klient_z_useragent(user_agent):
    """Která appka se ptá na streamy — jen pro statistiky (Dashboard, sloupec „Kodi / HA").

    Nuvio a Streamlet posílají vlastní jednoznačný `User-Agent` (`Nuvio/x.y.z-beta`,
    `Streamlet/x.y.z (…)`) — ověřeno z provozu 2026-09-17 (dočasné logování v `server.py`,
    121 požadavků, žádná kolize s ničím jiným). Oficiální Stremio appka UA občas neposílá
    vůbec, jindy jde jen UA prohlížeče (web verze na PC/smart TV) nebo generický klient
    (`okhttp`, komunitní agregátor `AIOStreams`) — ty všechny od sebe spolehlivě nejdou
    odlišit, spadají tedy pod „stremio"."""
    ua = user_agent or ""
    if ua.startswith("Nuvio/"):
        return "nuvio"
    if ua.startswith("Streamlet/"):
        return "streamlet"
    return "stremio"


class Odpoved:
    """Co server pošle klientovi."""

    def __init__(self, status=200, data=None, location=None, text=None, html=None, utok=None, proxy=None,
                 hlavicky=None):
        self.hlavicky = hlavicky or []   # [(jméno, hodnota)] navíc, třeba Set-Cookie správce
        self.proxy = proxy   # (adresa, hlavičky) — soubor stáhne a pošle dál server sám
        # `utok` = (důvod, otisk nastavení): odmítnutí, které se nepočítá do provozu, ale
        # do přehledu „kdo na nás útočí" (provoz.py). Klientovi se neposílá.
        self.utok = utok
        self.status = status
        self.data = data
        self.location = location
        self.text = text
        self.html = html

    @property
    def body(self):
        if self.data is not None:
            return mapping.json_bytes(self.data), "application/json; charset=utf-8"
        if self.html is not None:
            return self.html.encode("utf-8"), "text/html; charset=utf-8"
        return (self.text or "").encode("utf-8"), "text/plain; charset=utf-8"


def chyba(status, zprava):
    return Odpoved(status=status, text=zprava)


NEUSPECHU_DOST = 3   # kolik selhání jednoho zdroje v jedné odpovědi stačí, než to vzdáme
TMDB_PREFIX = "tmdb:"


def _imdb_z_tmdb(engine, ctype, tmdb_id):
    """`tmdb:<id>` → `tt…`, prázdno když se přeložit nedá.

    Klienti Stremia (ověřeno u Nuvia 2026-09-18) posílají u titulů z TMDB katalogů
    do `/stream/` id z TMDB. Zdroje ani jádro s ním neumí nic — celý doplněk stojí
    na IMDb id — takže se přeloží přes TMDB. Detail je v jádru cachovaný, takže je
    to jeden dotaz na titul, ne na požadavek.

    **Bez vlastního TMDB klíče** (nepovinné pole formuláře) to nejde: přeložit
    `tmdb:` id umí jen TMDB samo. Pak se vrátí prázdno jako dřív.
    """
    tmdb = getattr(engine, "tmdb", None)
    if tmdb is None:
        _LOGGER.info("tmdb:%s bez klíče TMDB nepřeložím", str(tmdb_id)[:20])
        return ""
    try:
        imdb = tmdb.imdb_id(ctype, tmdb_id)
    except Exception as err:  # noqa: BLE001 – výpadek TMDB = prázdno, ne chyba služby
        _LOGGER.warning("překlad tmdb:%s selhal: %s", str(tmdb_id)[:20], err)
        return ""
    if not imdb:
        _LOGGER.info("tmdb:%s nemá IMDb id", str(tmdb_id)[:20])
    return imdb or ""


def _primy(engine):
    """`vnitřní odkaz → (adresa, hlavičky)` pro zdroje z `mapping.PRES_HLAVICKY`.

    None znamená „tenhle soubor teď přehrát nejde" (nenastavený účet, vypršelé
    přihlášení, málo kreditu) — stream se pak vůbec nenabídne, protože bez
    hlaviček by stejně neodehrál.

    Selhání se počítají: FastShare se při nedostatku kreditu zkusí přihlásit
    znovu (kredit se mohl mezitím dobít) a to je síťový dotaz — u výpisu s
    desítkami souborů by se opakoval pro každý z nich.
    """
    neuspechy = {}

    def primy(vnitrni):
        klic = vnitrni.split(":", 1)[0]
        if neuspechy.get(klic, 0) >= NEUSPECHU_DOST:
            return None
        try:
            return engine.file_request(vnitrni)
        except NokturnoError as err:
            _LOGGER.info("přímý odkaz %s: %s", vnitrni[:40], err)
        except Exception as err:  # noqa: BLE001 – výpadek zdroje nesmí shodit výpis streamů
            _LOGGER.warning("přímý odkaz %s selhal: %s", vnitrni[:40], err)
        neuspechy[klic] = neuspechy.get(klic, 0) + 1
        return None

    return primy


class Router:
    """Obsluha požadavků. Jádro si bere podle nastavení v adrese."""

    def __init__(self, enginy, verze=VERZE, predvyplnit=False, statistiky=None, katalogy=None,
                 blokovane=None, identita=None, blokace=None):
        self.enginy = enginy
        self.identita = identita or Identita("")
        self.https_port = 0   # vlastní instance v síti: port HTTPS (server.main)
        self.public_url = ""  # veřejná adresa doplňku za proxy (NOKTURNO_PUBLIC_URL), přebije adresu z požadavku
        self.zprava = None   # volatelná → [(id, text, odkaz)] zpráv z dashboardu (nastaví server)
        self.zobrazeni = None   # volatelná (id, klíč uživatele) → započítá zobrazení zprávy
        self.klik = None     # volatelná (id) → započítá proklik zprávy (viz `/z/<id>`)
        self.kliky = Kliky()   # komu se zpráva už neukazuje, protože na ni klikl
        self.klik_okno = Okno(60, 10 * 60)   # prokliků zpráv z jedné adresy za 10 min
        self.id_okno = Okno(*ID_LIMIT)
        self.cz_pin_okno = Okno(*CZ_PIN_LIMIT)
        self.cz_poll_okno = Okno(*CZ_POLL_LIMIT)
        self.cz_klient = lambda klic: cztor.klient(self.enginy.data_dir, klic)   # testy podstrčí falešný
        self.katalogy = katalogy   # nokturno.katalogy.Katalogy, None = katalogy se nenabízejí
        self.verze = verze
        # nabídnout ve formuláři účty z prostředí? Na sdílené instanci NE — ukázalo
        # by je komukoli, kdo formulář otevře. Na vlastní ušetří opisování hashů.
        self.predvyplnit = predvyplnit
        self.statistiky = statistiky   # nokturno.statistiky.Statistiky, None = vypnuto
        self.ws_api = WebshareApi   # testy podstrčí falešné, aby nešly na síť
        self.st_api = SledujtetoApi
        self.fs_api = FastshareApi
        self.pt_api = PrehrajtoApi
        self.dav_api = StorageApi
        self.check_okno = Okno(*CHECK_LIMIT)
        self.stream_okno = Okno(*STREAM_LIMIT)
        self.play_okno = Okno(*PLAY_LIMIT)
        self.katalog_okno = Okno(*KATALOG_LIMIT)
        self.ip_okno = Okno(*IP_STROP)   # strop na adresu nad limity na uživatele (identitu)
        self.blokace = blokace or Blokace()
        # ruční blokace zneužívající adresy (otisk `config.fingerprint()`, ne účty
        # samotné) — `NOKTURNO_BLOCKED_FINGERPRINTS` v `.env`, viz `server.py`.
        # Incident 2026-09-19: jedna adresa systematicky procházela celý katalog
        # (stream požadavek na tisíce id po řadě) a nafoukla cache adresáře na
        # tolik souborů, že LXC 124 došly inody i sousednímu dashboardu.
        self.blokovane = frozenset(blokovane or ())
        self.povolena = None   # soukroma.Povolena = soukromá instance (server.vytvor_server)
        data_dir = getattr(enginy, "data_dir", None)
        self.profily = profily.Profily(data_dir) if data_dir else None
        self.pady = None       # pady.Pady, kvůli přepínači na /configure

    # --- soukromá instance a volby aplikace --------------------------------
    STRANKY = ("", "/", "/configure", "/configure/", "/check")
    # sdílená instance (`"sdilena": true`): z internetu i formulář a uložení profilu, pro kamarády;
    # včetně výběru, přejmenování a mazání profilů; povolování a aktualizace dál jen mimo proxy
    sdilena = False
    profily_vypnute = False   # NOKTURNO_PROFILY=0: starý režim, adresa nese celé nastavení
    heslo = ""                # NOKTURNO_HESLO: heslo k formuláři a profilům (viz server.potrebuje_heslo)
    spravce = None            # spravce.Spravce (vytvor_server); None = jako do 9.11: správce = požadavek mimo proxy
    SPRAVA = ("/povolit", "/aktualizace", "/aplikace")   # POST jen pro přihlášeného správce
    FORMULAR = ("/configure", "/configure/", "/check")
    PROFILY_CESTY = ("/profil", "/profily", "/profil/jmeno", "/profil/smazat")

    def _formular(self, cesta):
        if cesta in ("/configure", "/configure/") or cesta.startswith("/cztor/"):
            return True
        kousek, zbytek = self._rozdel(cesta)
        return bool(kousek) and zbytek in self.FORMULAR
    CESTY_DOPLNKU = ("/manifest.json", "/stream/", "/catalog/", "/meta/", "/play/")

    def _nastaveni(self, kousek):
        """Kousek adresy → nastavení. Klíč profilu se nejdřív přeloží na uložený kousek. None = nečitelné."""
        if profily.je_klic(kousek):
            ulozeny = self.profily.nacti(kousek) if self.profily else None
            return config.decode(ulozeny) if ulozeny else None
        return config.decode(kousek)

    def _zaklad_doplnku(self, zaklad):
        return self.public_url or tls.https_zaklad(zaklad, self.https_port) or zaklad

    def sprava(self, headers, z_proxy):
        """Smí požadavek do sekce Aplikace a k seznamu všech profilů?"""
        if self.spravce is None:
            return not z_proxy
        return self.spravce.prihlaseny(headers)

    def _soukroma(self, cesta, z_proxy):
        """Na soukromé instanci: cesty doplňku jen s povoleným otiskem, ostatní jen mimo proxy.
        None = smí dál, jinak 403 bez vysvětlení."""
        kousek, zbytek = self._rozdel(cesta)
        doplnek = zbytek not in self.STRANKY if kousek else zbytek.startswith(self.CESTY_DOPLNKU)
        if doplnek:
            options = self._nastaveni(kousek) if kousek else self.enginy.vychozi_options
            if options is not None and self.povolena.obsahuje(soukroma.otisk(options)):
                return None
        elif not z_proxy or (self.sdilena and (self._formular(cesta) or cesta == "/aktualizace")):
            return None
        return Odpoved(status=403, text="", utok=("soukromá instance", None))

    ZNACKA_AKTUALIZACE = "aktualizovat"   # zavadec.ZNACKA_AKTUALIZACE
    UPDATE_URL = os.environ.get("NOKTURNO_UPDATE_URL", "").strip()   # jen pod zavaděčem

    def aktualizace(self, znovu=False):
        """Nainstalovaná a nejnovější vydaná verze (update.json, v paměti 10 min)."""
        ted = time.time()
        uloz = getattr(self, "_nejnovejsi", None)
        if znovu or not uloz or ted - uloz[0] > 600:
            try:
                req = urllib.request.Request(self.UPDATE_URL, headers={"User-Agent": "Nokturno"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    uloz = (ted, str(json.load(r)["version"]))
            except (OSError, ValueError, KeyError) as err:
                _LOGGER.info("kontrola verze: %s", err)
                uloz = (ted, "")
            self._nejnovejsi = uloz
        nejnovejsi = uloz[1]
        return {"verze": self.verze, "nejnovejsi": nejnovejsi,
                "novejsi": bool(nejnovejsi) and verze_tuple(nejnovejsi) > verze_tuple(self.verze)}

    def aplikace(self):
        return {"soukroma": self.povolena is not None, "aktualizace": bool(self.UPDATE_URL),
                "stats": bool(self.statistiky and self.statistiky.zapnuto),
                "crash_reports": bool(self.pady and self.pady.zapnuto),
                "rezim_profily": not self.profily_vypnute, "ma_heslo": bool(self.heslo)}

    def nastav_aplikaci(self, volby):
        if "stats" in volby and self.statistiky is not None:
            self.statistiky.zapnuto = bool(volby["stats"])
        if "crash_reports" in volby and self.pady is not None:
            self.pady.zapnuto = bool(volby["crash_reports"])
        if "profily" in volby:
            self.profily_vypnute = not volby["profily"]
        if "heslo" in volby:
            self.heslo = str(volby["heslo"] or "")

    def post(self, cesta, telo, headers, zaklad=""):
        """POST jen z formuláře mimo proxy. Vlastní hlavička `X-Nokturno` vynutí u cizího webu
        preflight, který neprojde (CORS tu není), takže jiná stránka v prohlížeči majitele
        nastavení nezmění."""
        cesta = cesta.partition("?")[0]
        if self.profily_vypnute and cesta in self.PROFILY_CESTY:
            return chyba(404, "Profily jsou vypnuté.")
        if not headers.get("X-Nokturno"):
            return Odpoved(status=403, text="")
        z_proxy = soukroma.z_proxy(headers)
        sprava = self.sprava(headers, z_proxy)
        if cesta.startswith("/spravce/"):
            return self._spravce_post(cesta, telo, zaklad)
        if cesta in self.SPRAVA:
            if not sprava:
                return Odpoved(status=403, text="")
        elif z_proxy and not (self.sdilena and cesta in self.PROFILY_CESTY):
            return Odpoved(status=403, text="")
        if cesta in self.PROFILY_CESTY and self.spravce is not None and not self.spravce.nastaveno:
            return chyba(403, "Nejdřív nastav heslo správce.")
        if cesta == "/povolit":
            if self.povolena is None:
                return chyba(404, "Instance není soukromá.")
            otisk = soukroma.otisk_z_textu(telo)
            if not otisk:
                return chyba(400, "Nečitelné nastavení.")
            self.povolena.pridej(otisk)
            _LOGGER.info("povolené nastavení %s", otisk)
            return Odpoved(data={"ok": True, "otisk": otisk})
        if cesta == "/profil":
            if self.profily is None:
                return chyba(404, "Profily tu nejsou.")
            try:
                data = json.loads(telo or "{}")
                kousek = str(data["nastaveni"])
            except (ValueError, TypeError, KeyError):
                return chyba(400, "Nečitelné nastavení.")
            options = config.decode(kousek)
            if options is None:
                return chyba(400, "Nečitelné nastavení.")
            try:
                klic = self.profily.uloz(config.encode(options), data.get("klic"), prevod=bool(data.get("prevod")))
            except ValueError:
                return chyba(507, "Profilů je moc, starý smaž v datové složce aplikace.")
            if self.povolena is not None:
                self.povolena.pridej(soukroma.otisk(options))   # majitel ukládá mimo proxy = rovnou povoleno
            if data.get("jmeno") is not None:
                self.profily.pojmenuj(klic, data["jmeno"])
            instalace = self._zaklad_doplnku(zaklad) + "/c/" + klic + "/instalace"
            return Odpoved(data={"klic": klic, "instalace": instalace, "qr": profily.qr_svg(instalace)})
        if cesta in ("/profily", "/profil/jmeno", "/profil/smazat"):
            # správa profilů – jen majitel (mimo proxy, s hlavičkou), seznam nese klíče = účty
            if self.profily is None:
                return chyba(404, "Profily tu nejsou.")
            try:
                data = json.loads(telo or "{}")
                klic = str(data.get("klic") or "") if isinstance(data, dict) else ""
            except ValueError:
                return chyba(400, "Nečitelný požadavek.")
            if cesta == "/profil/jmeno" and not self.profily.pojmenuj(klic, data.get("jmeno")):
                return chyba(404, "Profil neexistuje.")
            if cesta == "/profil/smazat" and not self.profily.smaz(klic):
                return chyba(404, "Profil neexistuje.")
            seznam = self.profily.seznam()
            if not sprava:
                # kamarád vidí jen profily, jejichž klíč zná (pamatuje si je jeho prohlížeč)
                znam = {str(k) for k in (data.get("klice") or []) if isinstance(k, str)} | {klic}
                seznam = [p for p in seznam if p["klic"] in znam]
            return Odpoved(data={"profily": seznam})
        if cesta == "/aktualizace":
            if not self.UPDATE_URL:
                return chyba(404, "Aktualizace řídí zavaděč, tady neběží.")
            with open(os.path.join(self.enginy.data_dir, self.ZNACKA_AKTUALIZACE), "w", encoding="utf-8"):
                pass
            return Odpoved(data={"ok": True})
        if cesta == "/aplikace":
            try:
                data = json.loads(telo or "{}")
                zmeny = {k: bool(data[k]) for k in ("stats", "crash_reports", "profily") if k in data}
                if "heslo" in data:
                    # prázdné = bez hesla; jinak jen hash, holé heslo se nikam neukládá
                    heslo = str(data["heslo"] or "")
                    if len(heslo) > 200:
                        return chyba(400, "Heslo je moc dlouhé.")
                    zmeny["heslo"] = soukroma.hash_hesla(heslo) if heslo else ""
            except (ValueError, TypeError, AttributeError):
                return chyba(400, "Nečitelné volby.")
            nove_heslo = str(data.get("spravce_heslo") or "") if isinstance(data, dict) else ""
            hlavicky = []
            if nove_heslo and self.spravce is not None:
                vadne = self.spravce.zmen(nove_heslo)
                if vadne:
                    return chyba(400, vadne)
                hlavicky.append(("Set-Cookie", self.spravce.cookie(zaklad.startswith("https"))))   # nový token
            soukroma.uloz_aplikaci(self.enginy.data_dir, zmeny)
            self.nastav_aplikaci(zmeny)
            return Odpoved(data=self.aplikace(), hlavicky=hlavicky)
        return chyba(404, "Nic tu není.")

    def _spravce_post(self, cesta, telo, zaklad):
        """`/spravce/nastavit` (první heslo), `/prihlasit`, `/odhlasit`."""
        if self.spravce is None:
            return chyba(404, "Nic tu není.")
        https = zaklad.startswith("https")
        if cesta == "/spravce/odhlasit":
            return Odpoved(data={"ok": True}, hlavicky=[("Set-Cookie", self.spravce.cookie(https, odhlasit=True))])
        try:
            data = json.loads(telo or "{}")
            heslo = str(data.get("heslo") or "")[:200]
        except (ValueError, TypeError, AttributeError):
            return chyba(400, "Nečitelný požadavek.")
        if cesta == "/spravce/nastavit":
            vadne = self.spravce.nastav(heslo)
            if vadne:
                return chyba(403 if "pokus" in vadne else 400, vadne)
        elif cesta == "/spravce/prihlasit":
            if not self.spravce.prihlas(heslo):
                return chyba(403, "Heslo nesedí.")
        else:
            return chyba(404, "Nic tu není.")
        return Odpoved(data={"ok": True}, hlavicky=[("Set-Cookie", self.spravce.cookie(https))])

    # --- adresy -----------------------------------------------------------
    @staticmethod
    def _rozdel(cesta):
        """`/c/<nastavení>/zbytek` → (nastavení, `/zbytek`); jinak (None, cesta)."""
        casti = [c for c in cesta.split("/") if c]
        if len(casti) >= 2 and casti[0] == "c":
            return casti[1], "/" + "/".join(casti[2:])
        return None, cesta

    def ma_identitu(self, cesta):
        """Čím je adresa jedinečná — jen pro přehled útočníků, bez ověření podpisu:
        1 = token identity (`id`), 2 = vlastní účty (jedinečný otisk), 0 = nic (sdílené)."""
        kousek, _ = self._rozdel(urllib.parse.unquote((cesta or "").split("?", 1)[0]))
        options = self._nastaveni(kousek) if kousek else None
        if not options:
            return 0
        return 1 if options.get(config.ID_KLIC) else 2 if config.ma_ucty(options) else 0

    def _odkaz(self, zaklad, kousek):
        """Stavitel adres na `/play/`, se stejným nastavením jako příchozí požadavek.

        Základ musí být absolutní: Stremio přehrává na jiném zařízení, než na
        kterém běží tahle služba. Prefix musí sedět, jinak by se soubor
        rozklíčoval cizím účtem, nebo vůbec.
        """
        predpona = f"{zaklad}/c/{kousek}" if kousek else zaklad

        def odkaz(vnitrni_url):
            return f"{predpona}/play/{mapping.zakoduj(vnitrni_url)}"
        return odkaz

    # --- endpointy --------------------------------------------------------
    def manifest(self, options, nastaveno, nova_adresa=None, jazyk="cs"):
        """Jen z nastavení — jádro se kvůli manifestu nezakládá (viz `sources_from_options`)."""
        zdroje = config.sources_from_options(options)
        katalogy = self.katalogy.manifest(options, jazyk) if self.katalogy else []
        data = mapping.manifest(self.verze, zdroje, nastaveno=bool(zdroje), katalogy=katalogy, nova_adresa=nova_adresa,
                                jazyk=jazyk, koncerty=bool(self.katalogy and self.katalogy.ma_koncerty(options)))
        data["behaviorHints"]["configurable"] = True
        # bez vlastního nastavení ať Stremio rovnou nabídne formulář
        data["behaviorHints"]["configurationRequired"] = not (nastaveno or zdroje)
        return Odpoved(data=data)

    def health(self):
        return Odpoved(data={"ok": True, "verze": self.verze, "jader": len(self.enginy)})

    @staticmethod
    def _stranka(jmeno, jazyk):
        """`configure` + `sk` → `configure.sk.html`; když jazyková verze chybí, česká."""
        if jazyk and jazyk != "cs":
            try:
                return (STATIKA / f"{jmeno}.{jazyk}.html").read_text(encoding="utf-8")
            except OSError:
                pass
        return (STATIKA / f"{jmeno}.html").read_text(encoding="utf-8")

    def configure(self, kousek, zaklad, verejny=False, jazyk="cs", klient="", z_proxy=False, sprava=None):
        """Formulář, který vyrobí adresu s účty. Předvyplní se z adresy, na které stojí."""
        try:
            html = self._stranka("configure", jazyk)
        except OSError:
            return chyba(500, "Formulář nastavení chybí.")
        soucasne = self._nastaveni(kousek) if kousek else None
        if soucasne is None and self.predvyplnit and not verejny:
            soucasne = self.enginy.vychozi_options
        # hodnoty z adresy jsou od kohokoli — do <script> jen escapované (viz json_do_scriptu)
        html = html.replace("__NASTAVENI__", mapping.json_do_scriptu(soucasne or {}))
        nabidka = self.katalogy.formular(jazyk) if self.katalogy else []
        html = html.replace("__KATALOGY__", mapping.json_do_scriptu(nabidka))
        html = html.replace("__SABLONY__", mapping.json_do_scriptu(mycat.TEMPLATES))
        # adresa doplňku: v síti HTTPS přes local-ip.co (Stremio jinak http z LAN nevezme)
        doplnek = self._zaklad_doplnku(zaklad)
        html = html.replace("__ZAKLAD_DOPLNKU__", html_lib.escape(doplnek, quote=True))
        html = html.replace("__ZAKLAD__", html_lib.escape(zaklad, quote=True))
        html = html.replace("__PROFIL__", kousek if profily.je_klic(kousek or "") and soucasne is not None else "")
        html = html.replace("__VERZE__", self.verze)
        html = html.replace("__ID__", self._identita_pro_formular(soucasne, klient))
        html = html.replace("__APLIKACE__", mapping.json_do_scriptu({**self.aplikace(), "sprava": (not z_proxy) if sprava is None else sprava,
                                                                "spravce_nastaven": self.spravce is None or self.spravce.nastaveno,
                                                                "profily": (not z_proxy or self.sdilena) and not self.profily_vypnute, "stary": self.profily_vypnute}))
        return Odpoved(html=html)

    def instalace(self, kousek, zaklad, jazyk):
        """Stránka z QR kódu: tlačítka pro přidání doplňku do Stremia, Nuvia a zkopírování adresy."""
        if self._nastaveni(kousek) is None:
            return chyba(404, "Tohle nastavení už v aplikaci není. Ulož ho znovu na /configure")
        try:
            html = self._stranka("instalace", jazyk)
        except OSError:
            return chyba(500, "Stránka chybí.")
        adresa = self._zaklad_doplnku(zaklad) + "/c/" + kousek + "/manifest.json"
        html = html.replace("__ADRESA__", html_lib.escape(adresa, quote=True))
        html = html.replace("__ZAKLAD__", html_lib.escape(zaklad, quote=True))
        return Odpoved(html=html)

    def _identita_pro_formular(self, soucasne, klient):
        """Token do adresy: stávající platný (v druhé půlce platnosti tiše vyměněný za čerstvý —
        držitel platného už práci prokázal); bez něj si stránka novou vyžádá až za důkaz práce."""
        t = (soucasne or {}).get(config.ID_KLIC)
        if self.identita.zapnuta and t and self.identita.platna(t) and not self.blokace.odebrana("id:" + t):
            return self.identita.vydat() if self.identita.k_obnove(t) else t
        return ""

    def vydat_identitu(self, dotaz, klient):
        """Nová identita za spočítanou výzvu (`identita.over_dukaz`), nejvýš `ID_LIMIT` na adresu."""
        if not self.identita.zapnuta:
            return chyba(404, "Identity se nevydávají.")
        vyzva = (dotaz.get("vyzva") or [""])[0]
        reseni = (dotaz.get("reseni") or [""])[0]
        if not self.identita.over_dukaz(vyzva, reseni, klic_klienta(klient)):
            odp = chyba(403, "Výzva nesedí nebo vypršela.")
            odp.utok = ("špatný důkaz", None)
            return odp
        # limit až za správný důkaz — špatný stojí jen toho, kdo ho poslal
        adresa = klic_klienta(klient)
        if adresa and not self.id_okno.povolit(adresa):
            _LOGGER.info("identita: limit vydávání pro %s", adresa)
            return chyba(429, "Příliš mnoho identit z jedné adresy za hodinu.")
        return Odpoved(data={"id": self.identita.vydat()})

    def _klic_limitu(self, options, klient):
        """Na koho se počítají limity, blokace a nová jádra: identita z adresy, jinak adresa."""
        if options and options.get(config.ID_KLIC):
            return "id:" + options[config.ID_KLIC]
        if options and config.ma_ucty(options):
            return "fp:" + config.fingerprint(options)   # účty v adrese = jedinečný otisk uživatele
        return klic_klienta(klient)

    def _omezit(self, okno, options, klient, co, jazyk="cs"):
        """Limit `okno` na klíč (identita, jinak adresa) + strop `IP_STROP` na adresu, který
        identita neobejde; blokovaná adresa/identita dostane 403. None = smí dál."""
        fp = config.fingerprint(options)
        adresa = self._klic_limitu(options, klient)
        ip = klic_klienta(klient)
        for klic in {adresa, ip} - {""}:
            if self.blokace.blokovana(klic):
                odp = chyba(403, mapping.blokovano(jazyk))
                odp.utok = ("auto-blok", fp)
                return odp
        vlastni_ok = okno.povolit(adresa or fp)
        ip_ok = not (ip and ip != adresa) or self.ip_okno.povolit(ip)
        if not vlastni_ok or not ip_ok:
            # prohřešek dostane jen ten klíč, který limit opravdu překročil — jinak by jeden
            # uživatel s vlastním otiskem přivedl na blokaci celou domácnost za stejnou IP
            # blokace jen identity/otisku; adresa se nikdy neblokuje (CGNAT, domácnost), dostane jen 429
            if adresa.startswith(("id:", "fp:")) and not vlastni_ok:
                self.blokace.prohresek(adresa)
            odp = chyba(429, mapping.prilis_mnoho(co, jazyk))
            odp.utok = ("limit", fp)
            return odp
        return None

    def check(self, options, verejny=False):
        """Ověření účtů pro tlačítko ve formuláři.

        WebShare se opravdu přihlásí a řekne, kolik zbývá VIP — bez VIP je rychlost
        omezená a film se nedá plynule přehrát, což je nejčastější „nic nehraje".
        Streamuj přihlašovací endpoint nemá, špatné heslo se pozná až při přehrání,
        takže se jen ohlásí, co je vyplněné. Jádro se kvůli tomu nezakládá — jen
        jedno přihlášení, žádná cache.

        Z internetu (`verejny`) se úložiště prochází hlídaným openerem a chyba se
        nehlásí doslova: „connection refused" vs. „timed out" z adres v naší síti
        by z tlačítka udělalo skener portů.
        """
        out = {"webshare": None, "streamuj": None, "sledujteto": None, "fastshare": None,
               "hellspy": bool(options.get("hs_enabled"))}
        user = (options.get("ws_username") or "").strip()
        if user:
            try:
                api = self.ws_api(user, options.get("ws_password") or "")
                if not api.login():
                    raise WebshareError("přihlášení selhalo")
                stav = api.account_status()
                out["webshare"] = {"ok": True, "vip": bool(stav.get("vip")),
                                   "days": int(stav.get("days") or 0), "until": stav.get("until") or ""}
            except Exception as err:  # noqa: BLE001 – cokoli, co přihlášení zastaví, je pro uživatele totéž
                _LOGGER.info("ověření WebShare %s: %s", user[:3] + "…", err)
                out["webshare"] = {"ok": False, "chyba": str(err) or "přihlášení selhalo"}
        if (options.get("streamuj_username") or "").strip():
            out["streamuj"] = {"heslo": bool((options.get("streamuj_password") or "").strip())}
        email = (options.get("st_email") or "").strip()
        if email:
            # Sledujteto: přihlášení a jestli má účet Premium — bez něj přehrání neprojde
            try:
                user = self.st_api(email, options.get("st_password") or "").me()
                out["sledujteto"] = {"ok": True, "premium": bool(user.get("is_premium"))}
            except Exception as err:  # noqa: BLE001 – pro uživatele je každé selhání totéž
                _LOGGER.info("ověření Sledujteto %s: %s", email[:3] + "…", err)
                out["sledujteto"] = {"ok": False, "chyba": str(err) or "přihlášení selhalo"}
        fs_user = (options.get("fs_username") or "").strip()
        if fs_user:
            # FastShare: přihlášení a kolik zbývá — přehrání se odečítá z kreditu, pokud účet nemá neomezený tarif
            try:
                ucet = self.fs_api(fs_user, options.get("fs_password") or "",
                                   provider=options.get("fs_provider") or "fastshare").login()
                out["fastshare"] = {"ok": True, "neomezene": bool(ucet.get("unlimited")),
                                    "kredit_mb": int(ucet.get("credit_mb") or 0)}
            except Exception as err:  # noqa: BLE001 – pro uživatele je každé selhání totéž
                _LOGGER.info("ověření FastShare %s: %s", fs_user[:3] + "…", err)
                out["fastshare"] = {"ok": False, "chyba": str(err) or "přihlášení selhalo"}
        pt_email = (options.get("pt_email") or "").strip()
        if pt_email:
            # Přehraj.to: přihlášení a jestli je Premium (jinak jen překódované soubory)
            try:
                ucet = self.pt_api(pt_email, options.get("pt_password") or "").me()
                out["prehrajto"] = {"ok": True, "premium": bool(ucet.get("premium")),
                                    "days": int(ucet.get("days") or 0)}
            except Exception as err:  # noqa: BLE001 – pro uživatele je každé selhání totéž
                _LOGGER.info("ověření Přehraj.to %s: %s", pt_email[:3] + "…", err)
                out["prehrajto"] = {"ok": False, "chyba": str(err) or "přihlášení selhalo"}
        klic = options.get(config.CZ_KLIC)
        if klic:
            try:
                api = self.cz_klient(klic)
                out["cztor"] = ({"ok": True, **api.profile()} if api.paired()
                                else {"ok": False, "chyba": "Zařízení není spárované – spáruj znovu."})
            except CztorError as err:
                out["cztor"] = {"ok": False, "chyba": str(err) or "ověření selhalo"}
        out["uloziste"] = []
        for n in range(1, SLOTS + 1):
            url = (options.get(f"dav{n}_url") or "").strip()
            if not url:
                continue
            # jen kořen složky, celý strom se prochází až při hledání
            try:
                navic = {"opener": sit.OPENER} if verejny else {}
                api = self.dav_api(url, options.get(f"dav{n}_username") or "", options.get(f"dav{n}_password") or "",
                                   options.get(f"dav{n}_name") or "", slot=n, **navic)
                out["uloziste"].append({"slot": n, "ok": True, "polozek": api.check()})
            except Exception as err:  # noqa: BLE001 – pro uživatele je každé selhání totéž
                _LOGGER.info("ověření úložiště %d: %s", n, err)
                out["uloziste"].append({"slot": n, "ok": False,
                                        "chyba": "nedostupné" if verejny else (str(err) or "nedostupné")})
        return Odpoved(data=out)

    def cztor_pin(self, klient):
        """Nový klíč do adresy doplňku a PIN, který uživatel potvrdí na cztor.com/activate."""
        if not self.cz_pin_okno.povolit(klic_klienta(klient) or "?"):
            return chyba(429, "Příliš mnoho párování z jedné adresy za hodinu, zkus to později.")
        klic = cztor.novy_klic()
        try:
            pin = self.cz_klient(klic).start_pin()
        except CztorError as err:
            _LOGGER.info("CZtor PIN: %s", err)
            return chyba(502, f"CZtor teď PIN nevydal ({err}).")
        return Odpoved(data={"klic": klic, "pin": pin["pin"], "url": pin["url"], "token": pin["poll_token"],
                             "interval": pin["interval"], "platnost": int(pin["expires"] - time.time())})

    def cztor_poll(self, q, klient):
        """{"stav": "ceka" | "ok" | "chyba"}; u „ok" i účet a předplatné."""
        if not self.cz_poll_okno.povolit(klic_klienta(klient) or "?"):
            return chyba(429, "Příliš mnoho dotazů za sebou, zkus to za pár minut.")
        klic = (q.get("k") or [""])[0]
        token = (q.get("t") or [""])[0]
        if not config.CZ_RE.match(klic) or not token or len(token) > 512:
            return chyba(400, "Neplatný dotaz.")
        api = self.cz_klient(klic)
        if not api.store.existuje():
            # jen klíč z `/cztor/pin` — jinak by každý vymyšlený klíč založil soubor
            return chyba(404, "Párování nenalezeno, začni znovu.")
        try:
            if not api.poll_pin(token):
                return Odpoved(data={"stav": "ceka"})
        except CztorError as err:
            return Odpoved(data={"stav": "chyba", "zprava": str(err)})
        return Odpoved(data={"stav": "ok", "ucet": api.account()})

    def proklik(self, kus, zaklad, klient):
        """Klik na řádek se zprávou (`externalUrl` = `/z/<id>/<značka>`): započítá se,
        uživateli se zpráva přestane ukazovat a odpověď přesměruje tam, kam zpráva mířila.

        Limit na adresu platí **jen na počítání**, ať čísla nejde nafouknout; skrytí i
        přesměrování se dělají vždy — o cíl uživatel přijít nesmí a skrytí je jen zápis
        jedné dvojice navíc. Značka je jednosměrná (viz `kliky.py`), klíč uživatele
        z ní server neodvodí a ani ho k ničemu nepotřebuje."""
        kus, _, znacka = kus.partition("/")
        if not kus.isdigit() or len(kus) > 12 or not re.fullmatch(r"[0-9a-f]{0,16}", znacka):
            return chyba(404, "Neznámá zpráva")
        id_zpravy = int(kus)
        self.kliky.oznac(id_zpravy, znacka)
        cil = zaklad + "/"
        for zid, _text, odkaz in (self.zprava() if callable(self.zprava) else []):
            if zid == id_zpravy and odkaz:
                cil = odkaz if odkaz.startswith("https://") else zaklad + (odkaz if odkaz.startswith("/") else "/")
                break
        if callable(self.klik) and self.klik_okno.povolit(klic_klienta(klient) or "?"):
            self.klik(id_zpravy)
        return Odpoved(status=302, location=cil)

    def uvod(self, zaklad, jazyk="cs"):
        """Úvodní stránka a rozcestník celé rodiny Nokturna — nic o nastavení instance neprozradí."""
        try:
            html = self._stranka("index", jazyk)
        except OSError:
            return Odpoved(text=f"Nokturno pro Stremio {self.verze}\nNastavení: {zaklad}/configure\n")
        return Odpoved(html=html.replace("__ZAKLAD__", html_lib.escape(zaklad, quote=True))
                       .replace("__VERZE__", self.verze))

    TERMS_URL = "https://nokturno.stream/terms"

    def terms(self, zaklad, jazyk="cs"):
        """Podmínky použití mají jedno místo, nokturno.stream/terms – odsud jen přesměrování."""
        return Odpoved(status=302, location=self.TERMS_URL)

    def streams(self, engine, ctype, item_id, zaklad, kousek, aplikace="stremio", jazyk="cs"):
        if ctype not in TYPY:
            return chyba(404, f"Neznámý typ obsahu: {ctype}")
        base_id, season, episode = split_episode_id(item_id)
        if base_id.startswith(TMDB_PREFIX):
            tmdb_id = base_id[len(TMDB_PREFIX):]
            if not tmdb_id.isdigit() or getattr(engine, "tmdb", None) is None:
                return Odpoved(data={"streams": []})
            # bez IMDb id (nové české a slovenské seriály) zůstává `tmdb:` – jádro vezme
            # metadata z TMDB a hledá jen podle názvu (WebShare, HellSpy, FastShare…)
            base_id = _imdb_z_tmdb(engine, ctype, tmdb_id) or base_id
            item_id = base_id if season is None else f"{base_id}:{season}:{episode}"
        if not (base_id.startswith(("tt", TMDB_PREFIX)) or is_sosac_id(base_id)):
            # titul z cizího katalogu, jehož id neumíme přeložit na název — hledat
            # fulltextem není podle čeho. Zapíšeme si, co chodí: kdyby se nějaký
            # tvar opakoval, vyplatí se ho podpořit.
            _LOGGER.info("neznámý tvar id, vracím prázdno: %s", item_id[:60])
            return Odpoved(data={"streams": []})
        if ctype == "series" and season is None:
            return chyba(400, "Seriál potřebuje id ve tvaru tt…:série:díl")

        if STREAM_DELAY:
            time.sleep(STREAM_DELAY)
        try:
            # jen přísná shoda, stejně jako v Kodi a HA: volnější fulltext si má
            # uživatel vyžádat sám (tlačítko v Kodi), ve Stremiu na to není místo
            popisy = engine.streams(ctype, item_id)
        except NokturnoError as err:
            # chybějící zdroj není chyba služby; Stremio má ukázat prázdno a jít dál
            _LOGGER.info("streamy %s %s: %s", ctype, item_id, err)
            return Odpoved(data={"streams": []})
        except Exception as err:  # noqa: BLE001 – výpadek zdroje nesmí shodit službu
            _LOGGER.warning("streamy %s %s selhaly: %s", ctype, item_id, err)
            return Odpoved(data={"streams": []})

        _LOGGER.info("streamy %s %s: %d", ctype, item_id, len(popisy))
        if self.statistiky is not None:
            self.statistiky.zaznamenej(engine, ctype, item_id, aplikace)
        return Odpoved(data=mapping.streams_response(popisy, self._odkaz(zaklad, kousek),
                                                      primy=_primy(engine), jazyk=jazyk))

    def katalog(self, casti, options=None, jazyk="cs"):
        """`/catalog/<typ>/<id>.json` nebo `/catalog/<typ>/<id>/skip=<n>.json` → `{"metas": [...]}`."""
        if self.katalogy is None or len(casti) not in (3, 4) or not casti[-1].endswith(".json"):
            return chyba(404, "Takový katalog tu není.")
        typ = casti[1]
        if len(casti) == 3:
            katalog_id, extra = casti[2][:-len(".json")], ""
        else:
            katalog_id, extra = casti[2], casti[3][:-len(".json")]
        dotaz = urllib.parse.parse_qs(extra)
        try:
            skip = max(0, int((dotaz.get("skip") or ["0"])[0]))
        except ValueError:
            skip = 0
        if skip > MAX_SKIP:
            # každá hodnota skip je vlastní cache klíč a dotaz na zdroj; hlouběji Stremio nikdo neroluje
            return Odpoved(data={"metas": []})
        metas = self.katalogy.polozky(typ, katalog_id, skip, options, zanr=(dotaz.get("genre") or [""])[0][:40],
                                      jazyk=jazyk)
        if metas is None:
            return chyba(404, "Takový katalog tu není.")
        return Odpoved(data={"metas": metas})

    def koncert_meta(self, casti, options):
        """`/meta/Koncerty/<id>.json` → `{"meta": …}`; jiný typ nebo neznámé id = 404."""
        if (self.katalogy is None or len(casti) != 3 or casti[1] != KONCERTY or not casti[2].endswith(".json")):
            return chyba(404, "Takové meta tu není.")
        meta = self.katalogy.koncert_meta(options, casti[2][:-len(".json")])
        return Odpoved(data={"meta": meta}) if meta else chyba(404, "Takové meta tu není.")

    def koncert_streamy(self, engine, options, item_id, zaklad, kousek, jazyk="cs"):
        popisy = self.katalogy.koncert_soubory(options, item_id) if self.katalogy else []
        odp = mapping.streams_response(popisy, self._odkaz(zaklad, kousek), primy=_primy(engine), jazyk=jazyk)
        for s in odp["streams"]:
            s["name"] = "Koncerty"
        return Odpoved(data=odp)

    def play(self, engine, payload, klic="vychozi"):
        vnitrni = mapping.dekoduj(payload)
        if not vnitrni:
            return chyba(400, "Neplatný odkaz.")
        if vnitrni.startswith(mapping.PRES_HLAVICKY):
            # Vlastní úložiště a FastShare chtějí u každého požadavku hlavičku (heslo,
            # cookie z přihlášení), kterou přehrávač nemá — soubor přepošle server.
            try:
                return Odpoved(proxy=engine.file_request(vnitrni))
            except NokturnoError as err:
                _LOGGER.info("přímý odkaz %s: %s", vnitrni[:40], err)
                return chyba(502, str(err))
        try:
            skutecna = engine.resolve(vnitrni)
        except NokturnoError as err:
            # nejčastěji „soubor je dočasně nedostupný“ od WebShare
            _LOGGER.info("rozklíčování %s: %s", vnitrni[:40], err)
            return chyba(502, str(err))
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("rozklíčování %s selhalo: %s", vnitrni[:40], err)
            return chyba(502, "Zdroj teď odkaz nevydal.")
        if not skutecna:
            return chyba(502, "Zdroj vrátil prázdný odkaz.")
        return Odpoved(status=302, location=skutecna, text="")

    # --- rozcestník -------------------------------------------------------
    def route(self, cesta, zaklad, verejny=False, jazyk=None, klient="", aplikace="stremio", z_proxy=False,
              sprava=None, hlavicky=None):
        """Cesta požadavku na odpověď. `zaklad` je absolutní adresa služby,
        `verejny` říká, že přišel z internetu (viz docstring modulu), `jazyk`
        je jazyk stránek z `Accept-Language` (viz `jazyk_z_hlavicky`), `klient`
        adresa klienta pro limit na `/check`, `aplikace` appka podle User-Agentu
        (viz `klient_z_useragent`) pro statistiky u `/stream/`.
        Parametr `?lang=cs|sk` v adrese má přednost, bez obojího čeština."""
        if self.blokace.adresa_zakazana(klic_klienta(klient)):
            odp = chyba(403, "Tato adresa je zakázaná.")
            odp.utok = ("zakázaná adresa", None)
            return odp
        cesta, _, dotaz = cesta.partition("?")
        lang = (urllib.parse.parse_qs(dotaz).get("lang") or [""])[0].strip().lower().split("-")[0]
        if lang in JAZYKY:
            jazyk = lang
        elif jazyk not in JAZYKY:
            jazyk = "cs"
        cesta = urllib.parse.unquote(cesta)
        if self.povolena is not None and cesta != "/health":
            odp = self._soukroma(cesta, z_proxy)
            if odp is not None:
                return odp
        if cesta == "/health":
            return self.health()
        if cesta == "/aktualizace":
            if not (self.sprava(hlavicky or {}, z_proxy) if sprava is None else sprava) or not self.UPDATE_URL:
                return chyba(404, "Nic tu není.")
            return Odpoved(data=self.aktualizace("znovu=1" in dotaz))
        if cesta == "/terms":
            return self.terms(zaklad, jazyk)
        if cesta.startswith("/z/"):
            return self.proklik(cesta[len("/z/"):], zaklad, klient)
        if cesta == "/identita/vyzva":
            return Odpoved(data={"vyzva": self.identita.vyzva(klic_klienta(klient)), "bity": self.identita.bity})
        if cesta == "/identita":
            return self.vydat_identitu(urllib.parse.parse_qs(dotaz), klient)
        if cesta == "/cztor/pin":
            return self.cztor_pin(klient)
        if cesta == "/cztor/poll":
            return self.cztor_poll(urllib.parse.parse_qs(dotaz), klient)

        kousek, zbytek = self._rozdel(cesta)
        options = self._nastaveni(kousek) if kousek else None
        if kousek and options is None:
            if profily.je_klic(kousek):
                return chyba(404, "Tohle nastavení už v aplikaci není. Ulož ho znovu na /configure")
            return chyba(404, "Adresa nese nečitelné nastavení. Vyrob si novou na /configure")
        if options and options.get(config.ID_KLIC):
            platna = self.identita.platna(options[config.ID_KLIC])
            odebrana = self.blokace.odebrana("id:" + options[config.ID_KLIC])
            if not self.identita.zapnuta or (zbytek == "/configure" and (not platna or odebrana)):
                # bez tajemství nejde ověřit; na formuláři se neplatná/odebraná jen zahodí a vydá se nová
                options.pop(config.ID_KLIC)
            elif not platna:
                odp = chyba(403, "Adresa nese neplatnou identitu. Vyrob si novou na /configure")
                odp.utok = ("neplatné id", config.fingerprint(options))
                return odp
            elif odebrana:
                odp = chyba(403, "Tato identita byla kvůli opakovanému zneužití odebrána. Vyrob si novou na /configure")
                odp.utok = ("odebráno", config.fingerprint(options))
                return odp
        if kousek and self.blokovane and config.fingerprint(options) in self.blokovane:
            odp = chyba(403, "Tato adresa doplňku je zablokovaná.")
            odp.utok = ("blokováno", config.fingerprint(options))
            return odp
        if verejny and options:
            options = config.bez_lokalnich_uloziste(options)

        if zbytek in ("", "/", "/configure", "/configure/"):
            if zbytek in ("/configure", "/configure/"):
                if sprava is None:
                    sprava = self.sprava(hlavicky or {}, z_proxy)
                return self.configure(kousek, zaklad, verejny, jazyk, klient, z_proxy, sprava=sprava)
            return self.uvod(zaklad, jazyk)

        if kousek and zbytek == "/instalace":
            return self.instalace(kousek, zaklad, jazyk)

        if verejny and not kousek:
            if zbytek == "/manifest.json":
                # Stremio z toho pozná, že si má doplněk nejdřív nastavit
                data = mapping.manifest(self.verze, (), nastaveno=False)
                data["behaviorHints"].update(configurable=True, configurationRequired=True)
                return Odpoved(data=data)
            return chyba(403, f"Doplněk bez vlastního nastavení jde použít jen z domácí sítě. "
                              f"Vyrob si adresu na {zaklad}/configure")

        if zbytek == "/check":
            if not self.check_okno.povolit(klic_klienta(klient) or "?"):
                odp = chyba(429, "Příliš mnoho ověření za sebou, zkus to za pár minut.")
                odp.utok = ("limit", config.fingerprint(options) if kousek else None)
                return odp
            return self.check(options if kousek else self.enginy.vychozi_options, verejny=verejny)
        # adresa z doby před identitou (do 6.1.0) a bez účtů (jen HellSpy): funguje dál, ale limity
        # sdílí celá IP — uživatele postrčíme na novou (popis doplňku, první položka streamů)
        stara = bool(kousek) and self.identita.zapnuta and not options.get(config.ID_KLIC) and not config.ma_ucty(options)
        nova = f"{zaklad}/c/{kousek}/configure"   # formulář s jeho nastavením, vydá i identitu
        # jazyk hlášek: uložený ze slovenského formuláře, jinak podle Accept-Language klienta
        jazyk = "sk" if (options or {}).get(config.JAZYK_KLIC) == "sk" or jazyk == "sk" else "cs"
        if zbytek == "/manifest.json":
            return self.manifest(options if kousek else self.enginy.vychozi_options, nastaveno=bool(kousek),
                                 nova_adresa=nova if stara else None, jazyk=jazyk)

        casti = [c for c in zbytek.split("/") if c]
        if casti and casti[0] in ("catalog", "meta"):
            # katalog na účtech nezávisí — jádro se nezakládá, cache je jedna pro všechny
            if not self.katalog_okno.povolit(klic_klienta(klient) or "?"):
                odp = chyba(429, "Příliš mnoho požadavků na katalog za sebou, zkus to za pár minut.")
                odp.utok = ("limit", config.fingerprint(options) if kousek else None)
                return odp
            if casti[0] == "meta":
                return self.koncert_meta(casti, options)
            return self.katalog(casti, options, jazyk)

        if stara and casti and casti[0] in ("stream", "play"):
            # od 6.4.9 bez skutečných streamů: sdílené nastavení bez účtů a bez identity musí
            # přejít na novou adresu (limity by se počítaly na sdílenou IP)
            # odpověď je levná a bot na staré adrese na ni tluče desítky za vteřinu — nepatří do
            # provozu ani chybovosti, jen do přehledu útočníků (`utok`)
            if casti[0] == "play":
                odp = chyba(410, mapping.zastarala_adresa(jazyk))
            else:
                odp = Odpoved(data={"streams": [mapping.upozorneni_nova_adresa(nova, jazyk)]})
            odp.utok = ("stará adresa", config.fingerprint(options))
            return odp
        if kousek and casti and casti[0] == "stream":
            odp = self._omezit(self.stream_okno, options, klient, "streamy", jazyk)
            if odp is not None:
                # Stremio při 403/429 nic neukáže — uživatel dostane důvod jako jediný „stream"
                blok = Odpoved(data={"streams": [mapping.upozorneni_blokace(odp.text or "", zaklad + "/")]})
                blok.utok = odp.utok
                return blok
        # přetáčení přes proxy jsou desítky Range dotazů — limit jen z internetu
        if kousek and casti and casti[0] == "play" and (verejny or len(casti) != 2 or not
                                                         (mapping.dekoduj(casti[1]) or "").startswith(mapping.PRES_HLAVICKY)):
            odp = self._omezit(self.play_okno, options, klient, "přehrání")
            if odp is not None:
                return odp
        try:
            engine = self.enginy.pro(options, verejny=verejny, klient=self._klic_limitu(options, klient) if kousek else "")
        except PrilisMnohoNovych:
            adresa = self._klic_limitu(options, klient)
            if adresa.startswith(("id:", "fp:")):
                self.blokace.prohresek(adresa)
            odp = chyba(429, "Příliš mnoho nových nastavení z jedné adresy za hodinu, zkus to později.")
            odp.utok = ("limit", config.fingerprint(options))
            return odp

        if casti and casti[0] == "play" and len(casti) == 2:
            return self.play(engine, casti[1], klic=config.fingerprint(options) if kousek else "vychozi")
        if casti and casti[0] == "stream" and len(casti) == 3 and casti[1] == KONCERTY and casti[2].endswith(".json"):
            return self.koncert_streamy(engine, options, casti[2][:-len(".json")], zaklad, kousek, jazyk)
        if casti and casti[0] == "stream" and len(casti) == 3 and casti[2].endswith(".json"):
            odp = self.streams(engine, casti[1], casti[2][:-len(".json")], zaklad, kousek, aplikace, jazyk)
            if kousek and isinstance(odp.data, dict) and odp.data.get("streams"):
                povysit = getattr(self.enginy, "povysit", None)
                if povysit is not None:
                    povysit(options, verejny)   # první skutečný stream = jádro se ověřilo
            zpravy = self.zprava() if callable(self.zprava) else []
            if zpravy and isinstance(odp.data, dict) and isinstance(odp.data.get("streams"), list):
                # od nejnovější, každá jako vlastní řádek — víc aktivních zpráv se nesmí
                # slít do jedné položky (delší text klienti ořezávají)
                uzivatel = self._klic_limitu(options, klient) if kousek else ""
                znacka = self.kliky.znacka(uzivatel)
                for id_zpravy, oznameni, odkaz in reversed(zpravy):
                    if id_zpravy and self.kliky.videl(id_zpravy, uzivatel):
                        continue   # na tuhle zprávu už klikl, podruhé ji nedostane
                    # se známým id vede řádek přes `/z/<id>`, ať jde spočítat proklik;
                    # značka za ním říká, komu se pak zpráva má přestat ukazovat
                    if id_zpravy:
                        cil = zaklad + "/z/" + str(id_zpravy) + ("/" + znacka if znacka else "")
                    else:
                        cil = odkaz if odkaz.startswith("https://") else zaklad + (odkaz if odkaz.startswith("/") else "/")
                    odp.data["streams"].insert(0, mapping.zprava_z_dashboardu(oznameni, cil))
                    if id_zpravy and callable(self.zobrazeni):
                        self.zobrazeni(id_zpravy, uzivatel)
                    if id_zpravy:
                        self.kliky.zobrazeno(id_zpravy, uzivatel)
            return odp
        return chyba(404, "Nic tu není. Doplněk se nastavuje na /configure")
