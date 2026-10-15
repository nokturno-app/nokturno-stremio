"""Správce aplikace: kdo smí do sekce Aplikace, k uživatelům a ke všem profilům.

Správce se pozná podle hesla, ne podle sítě (aplikace na VPS nemá jak poznat, že požadavek přišel
z internetu). V `aplikace.json` je hash hesla (`spravce`, viz `soukroma.hash_hesla`), jméno
(`spravce_jmeno`), e-mail (`spravce_email`, ukazuje se na přihlášení jako kontakt) a hash
obnovovacího kódu (`obnova`). Po přihlášení dostane prohlížeč cookie s tokenem odvozeným z hashe –
po změně hesla tím přestanou platit všechna dřívější přihlášení.

Dokud správce není, `/configure` ukáže jen jeho založení. Z domácí sítě a z téhož počítače bez
proxy jde rovnou, odjinud (proxy hlavičky, veřejná IP – `bez_kodu`) chce i kód, který aplikace
vypíše při startu do logu. Pozor: nginx s holým `proxy_pass` hlavičky nepřidává a vypadá jako
127.0.0.1 – na VPS se proto `/configure` otevírá dřív, než se aplikace pustí ven.

Zapomenuté heslo: obnovovací kód ukázaný jednou při založení (`obnov`), nebo z `aplikace.json`
v datové složce smazat položku `spravce` a aplikaci restartovat. Uživatelé a profily zůstanou.
"""
import hashlib
import hmac
import ipaddress
import logging
import re
import secrets
import threading
import time

from . import soukroma

_LOGGER = logging.getLogger(__name__)

COOKIE = "nokturno_spravce"
# hlavičky, které přidává proxy – s nimi adresa klienta neříká, odkud požadavek přišel
HLAVICKY_PROXY = ("X-Forwarded-For", "X-Real-IP", "Forwarded", "Cf-Connecting-IP", "X-Forwarded-Host",
                  "Tailscale-Funnel-Request")
ABECEDA = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MAX_CHYB = 20            # špatných hesel a kódů za OKNO_CHYB – pak se odmítá všechno
OKNO_CHYB = 10 * 60
JMENO_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


class Odmitnuto(Exception):
    """Odmítnutá akce: `status` pro HTTP odpověď, text pro log a klienta."""

    def __init__(self, status, text):
        super().__init__(text)
        self.status = status


def bez_kodu(client_ip, headers):
    """Požadavek z domácí sítě nebo z téhož počítače bez proxy – tam kód není potřeba."""
    if any(headers.get(h) for h in HLAVICKY_PROXY):
        return False
    try:
        ip = ipaddress.ip_address(str(client_ip).split("%")[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_private


def jmeno(text):
    return str(text or "").strip().lower()


def over_jmeno(text):
    j = jmeno(text)
    if not JMENO_RE.match(j):
        raise Odmitnuto(400, "Jméno má 3 až 32 znaků: písmena bez diakritiky, číslice, tečka, pomlčka, podtržítko.")
    return j


def over_heslo(heslo):
    if len(heslo or "") < 6:
        raise Odmitnuto(400, "Heslo musí mít aspoň 6 znaků.")
    if len(heslo) > 200:
        raise Odmitnuto(400, "Heslo je moc dlouhé.")
    return heslo


def nahodny_kod(skupin):
    return "-".join("".join(secrets.choice(ABECEDA) for _ in range(4)) for _ in range(skupin))


def _normalizuj_kod(kod):
    return "".join(c for c in str(kod or "").upper() if c in ABECEDA)


class Limit:
    """Společný strop špatných hesel a kódů (správce i uživatelé)."""

    def __init__(self):
        self._chyby = []
        self.zamek = threading.Lock()

    def omezeno(self):
        ted = time.time()
        self._chyby = [t for t in self._chyby if ted - t < OKNO_CHYB]
        return len(self._chyby) >= MAX_CHYB

    def hlidej(self):
        if self.omezeno():
            raise Odmitnuto(429, "Příliš mnoho pokusů, zkus to za 10 minut.")

    def chyba(self):
        self._chyby.append(time.time())
        time.sleep(1)   # ponytail: zdržení na pokus, ne per-IP limit; stačí na hádání z prohlížeče


def _cookie(headers, nazev):
    for cast in str(headers.get("Cookie") or "").split(";"):
        k, _, hodnota = cast.strip().partition("=")
        if k == nazev:
            return hodnota
    return ""


def set_cookie(nazev, hodnota, https):
    """Hlavička Set-Cookie na rok (prázdná hodnota = odhlásit). `Lax`: „Konfigurovat“ ze Stremia na webu
    je navigace z cizího webu a se `Strict` by přihlášený vypadal odhlášeně. Proti cizím webům
    drží POST hlavička `X-Nokturno` a GET nic nemění."""
    vek = 365 * 86400 if hodnota else 0
    return f"{nazev}={hodnota}; Path=/; Max-Age={vek}; HttpOnly; SameSite=Lax" + ("; Secure" if https else "")


class Spravce:
    def __init__(self, data_dir, limit=None):
        self.data_dir = data_dir
        self.limit = limit or Limit()
        self._nacti()

    def _nacti(self):
        app = soukroma.nacti_aplikaci(self.data_dir)
        self.hash = str(app.get("spravce") or "")
        self.jmeno = str(app.get("spravce_jmeno") or "")
        self.email = str(app.get("spravce_email") or "")
        # kód pro založení správce odjinud než z domácí sítě (vypíše se při startu do logu)
        self.kod = "" if self.hash else nahodny_kod(2)

    def ohlas(self):
        if not self.hash:
            _LOGGER.warning("Aplikace zatím nemá správce. Otevři /configure a nastav ho. Kód pro nastavení "
                            "mimo domácí síť: %s", self.kod)

    @property
    def nastaveno(self):
        return bool(self.hash)

    @property
    def uplny(self):
        """Má jméno i e-mail (od 11.0.0 povinné, starší správce je doplní po přihlášení)."""
        return bool(self.jmeno and self.email)

    def token(self):
        return hmac.new(self.hash.encode("utf-8"), b"nokturno-spravce", hashlib.sha256).hexdigest()

    def prihlaseny(self, headers):
        token = _cookie(headers, COOKIE)
        return bool(self.hash and token) and hmac.compare_digest(token, self.token())

    def cookie(self, https, odhlasit=False):
        return set_cookie(COOKIE, "" if odhlasit else self.token(), https)

    def _over_udaje(self, jmeno_, email):
        j = over_jmeno(jmeno_)
        e = str(email or "").strip()
        if len(e) > 120 or not EMAIL_RE.match(e):
            raise Odmitnuto(400, "Vyplň platný e-mail.")
        return j, e

    def nastav(self, jmeno_, heslo, email, kod=None):
        """První nastavení. `kod` None = bez kódu (domácí síť). Vrátí obnovovací kód (ukáže se jednou)."""
        with self.limit.zamek:
            if self.hash:
                raise Odmitnuto(409, "Správce už je nastavený, přihlas se.")
            self.limit.hlidej()
            if kod is not None and not hmac.compare_digest(_normalizuj_kod(kod), _normalizuj_kod(self.kod)):
                self.limit.chyba()
                raise Odmitnuto(403, "Kód nesedí.")
            j, e = self._over_udaje(jmeno_, email)
            over_heslo(heslo)
            obnova = nahodny_kod(4)
            self._uloz({"spravce": soukroma.hash_hesla(heslo), "spravce_jmeno": j, "spravce_email": e,
                        "obnova": soukroma.hash_hesla(_normalizuj_kod(obnova))})
            _LOGGER.info("správce aplikace nastavený")
            return obnova

    def prihlas(self, heslo):
        with self.limit.zamek:
            if not self.hash:
                return False
            self.limit.hlidej()
            if soukroma.heslo_odpovida(str(heslo or ""), self.hash):
                return True
            self.limit.chyba()
            return False

    def zmen(self, heslo):
        over_heslo(heslo)
        with self.limit.zamek:
            self._uloz({"spravce": soukroma.hash_hesla(heslo)})

    def udaje(self, jmeno_, email):
        """Jméno a e-mail. Správce z doby před 11.0.0 je doplní a dostane obnovovací kód (vrátí ho, jinak None)."""
        j, e = self._over_udaje(jmeno_, email)
        with self.limit.zamek:
            zmeny = {"spravce_jmeno": j, "spravce_email": e}
            obnova = None
            if not soukroma.nacti_aplikaci(self.data_dir).get("obnova"):
                obnova = nahodny_kod(4)
                zmeny["obnova"] = soukroma.hash_hesla(_normalizuj_kod(obnova))
            self._uloz(zmeny)
            return obnova

    def obnov(self, kod, heslo):
        """Zapomenuté heslo: obnovovací kód → nové heslo a nový kód (starý přestane platit)."""
        over_heslo(heslo)
        with self.limit.zamek:
            self.limit.hlidej()
            ulozeny = str(soukroma.nacti_aplikaci(self.data_dir).get("obnova") or "")
            kod = _normalizuj_kod(kod)
            if not (self.hash and ulozeny and kod) or not soukroma.heslo_odpovida(kod, ulozeny):
                self.limit.chyba()
                raise Odmitnuto(403, "Kód nesedí.")
            obnova = nahodny_kod(4)
            self._uloz({"spravce": soukroma.hash_hesla(heslo), "obnova": soukroma.hash_hesla(_normalizuj_kod(obnova))})
            _LOGGER.info("heslo správce obnovené kódem")
            return obnova

    def zapomen(self):
        """Tovární nastavení: bez správce, nový kód pro založení."""
        with self.limit.zamek:
            soukroma.uloz_aplikaci(self.data_dir, {}, smazat=("spravce", "spravce_jmeno", "spravce_email", "obnova"))
            self._nacti()
        self.ohlas()

    def _uloz(self, zmeny):
        soukroma.uloz_aplikaci(self.data_dir, zmeny)
        self.hash = zmeny.get("spravce", self.hash)
        self.jmeno = zmeny.get("spravce_jmeno", self.jmeno)
        self.email = zmeny.get("spravce_email", self.email)
        self.kod = ""
