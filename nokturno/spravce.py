"""Správce aplikace: kdo smí do sekce Aplikace a vidí všechny profily.

Správce se nepozná podle sítě (aplikace na VPS nemá jak poznat, že požadavek přišel
z internetu), ale podle hesla správce. To je v `aplikace.json` jako hash (`spravce`,
viz `soukroma.hash_hesla`). Po přihlášení dostane prohlížeč cookie s tokenem odvozeným
z hashe – po změně hesla tím přestanou platit všechna dřívější přihlášení.

Dokud heslo správce není, `/configure` ukáže jen jeho nastavení. Kdo stránku otevře
první, stane se správcem – na veřejné adrese ji proto majitel má otevřít hned po instalaci.

Zapomenuté heslo: z `aplikace.json` v datové složce smazat položku `spravce` a aplikaci
restartovat.
"""
import hashlib
import hmac
import logging
import threading
import time

from . import soukroma

_LOGGER = logging.getLogger(__name__)

COOKIE = "nokturno_spravce"
MAX_CHYB = 20            # špatných hesel za OKNO_CHYB – pak se odmítá všechno
OKNO_CHYB = 10 * 60


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

    def nastav(self, heslo):
        """První nastavení. Vrátí None = hotovo, jinak text chyby."""
        with self._zamek:
            if self.hash:
                return "Správce už je nastavený, přihlas se."
            if self._omezeno():
                return "Příliš mnoho pokusů, zkus to za 10 minut."
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
        soukroma.uloz_aplikaci(self.data_dir, {"spravce": self.hash})
