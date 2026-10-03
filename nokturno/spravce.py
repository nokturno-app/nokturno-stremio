"""Správce aplikace: kdo smí do sekce Aplikace a vidí všechny profily.

Správce se nepozná podle sítě (aplikace na VPS nemá jak poznat, že požadavek přišel
z internetu), ale podle hesla správce. To je v `aplikace.json` jako hash (`spravce`,
viz `soukroma.hash_hesla`). Po přihlášení dostane prohlížeč cookie s tokenem odvozeným
z hashe – po změně hesla tím přestanou platit všechna dřívější přihlášení.

Dokud heslo správce není, `/configure` ukáže jen jeho nastavení. Kdo by stránku otevřel
dřív než majitel, mohl by si správce udělat sám. Proto mimo domácí síť chce nastavení
ještě jednorázový kód, který aplikace při startu vypíše do logu (konzole, `journalctl`).
Doma (adresa z místní sítě, bez hlaviček proxy) kód není potřeba – APK a aplikace na
počítači log nemají kde ukázat.

Zapomenuté heslo: z `aplikace.json` v datové složce smazat položku `spravce` a aplikaci
restartovat.
"""
import hashlib
import hmac
import ipaddress
import logging
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


def _cookie(headers):
    for cast in str(headers.get("Cookie") or "").split(";"):
        jmeno, _, hodnota = cast.strip().partition("=")
        if jmeno == COOKIE:
            return hodnota
    return ""


class Spravce:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.hash = str(soukroma.nacti_aplikaci(data_dir).get("spravce") or "")
        self.kod = "" if self.hash else "-".join(
            "".join(secrets.choice(ABECEDA) for _ in range(4)) for _ in range(2))
        self._chyby = []
        self._zamek = threading.Lock()

    @property
    def nastaveno(self):
        return bool(self.hash)

    def token(self):
        return hmac.new(self.hash.encode("utf-8"), b"nokturno-spravce", hashlib.sha256).hexdigest()

    def prihlaseny(self, headers):
        token = _cookie(headers)
        return bool(self.hash and token) and hmac.compare_digest(token, self.token())

    def cookie(self, https, odhlasit=False):
        """Hodnota hlavičky Set-Cookie (rok, jen pro tuhle stránku, ne pro cizí weby)."""
        hodnota, vek = ("", 0) if odhlasit else (self.token(), 365 * 86400)
        return (f"{COOKIE}={hodnota}; Path=/; Max-Age={vek}; HttpOnly; SameSite=Strict"
                + ("; Secure" if https else ""))

    def _omezeno(self):
        ted = time.time()
        self._chyby = [t for t in self._chyby if ted - t < OKNO_CHYB]
        return len(self._chyby) >= MAX_CHYB

    def _chyba(self):
        self._chyby.append(time.time())
        time.sleep(1)   # ponytail: zdržení na pokus, ne per-IP limit; stačí na hádání z prohlížeče

    def nastav(self, heslo, kod, doma):
        """První nastavení. Vrátí None = hotovo, jinak text chyby."""
        with self._zamek:
            if self.hash:
                return "Správce už je nastavený, přihlas se."
            if self._omezeno():
                return "Příliš mnoho pokusů, zkus to za 10 minut."
            if not doma and not hmac.compare_digest(str(kod or "").upper().replace("-", "").replace(" ", ""),
                                                    self.kod.replace("-", "")):
                self._chyba()
                return "Kód nesedí. Najdeš ho ve výpisu (logu) aplikace z posledního startu."
            if len(heslo or "") < 6:
                return "Heslo musí mít aspoň 6 znaků."
            self._uloz(heslo)
            _LOGGER.info("správce aplikace nastavený")
            return None

    def prihlas(self, heslo):
        with self._zamek:
            if not self.hash or self._omezeno():
                return False
            if soukroma.heslo_odpovida(str(heslo or ""), self.hash):
                return True
            self._chyba()
            return False

    def zmen(self, heslo):
        if len(heslo or "") < 6:
            return "Heslo musí mít aspoň 6 znaků."
        with self._zamek:
            self._uloz(heslo)
        return None

    def _uloz(self, heslo):
        self.hash = soukroma.hash_hesla(heslo)
        self.kod = ""
        soukroma.uloz_aplikaci(self.data_dir, {"spravce": self.hash})
