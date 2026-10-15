"""Uživatelské účty (režim 3): správce zakládá uživatele, ti si spravují jen své profily.

`uzivatele.json` v datové složce (0600): `{jméno: {h: hash hesla, b: zablokovaný, t: založen,
p: poslední přihlášení}}`. Cookie `nokturno_ucet` = `<jméno>.<HMAC z hashe>` – nové heslo nebo
smazání účtu přihlášení zruší samo, blokace se kontroluje u každého požadavku (a zastaví
i cesty doplňku profilů uživatele, viz `routes.Router.route`).

Doplněk ve Stremiu a Nuviu se přihlásit neumí, takže cesty doplňku dál stojí jen na klíči profilu.
"""
import hashlib
import hmac
import time

from . import soukroma
from .profily import JsonSoubor
from .spravce import Odmitnuto, _cookie, jmeno, over_heslo, over_jmeno, set_cookie

UZIVATELE = "uzivatele.json"
COOKIE = "nokturno_ucet"
MAX_UZIVATELU = 100
_ATRAPA = soukroma.hash_hesla("atrapa")


class Uzivatele(JsonSoubor):
    def __init__(self, data_dir, limit):
        super().__init__(data_dir, UZIVATELE)
        self.limit = limit   # spravce.Limit – společný se správcem

    def _ucet(self, j):
        with self._zamek:
            u = self._nacti().get(jmeno(j))
        return u if isinstance(u, dict) and u.get("h") else None

    def existuje(self, j):
        return self._ucet(j) is not None

    def blokovany(self, j):
        u = self._ucet(j)
        return bool(u and u.get("b"))

    def _uprav(self, j, zmena):
        with self._zamek:
            data = dict(self._nacti())
            if not isinstance(data.get(j), dict):
                raise Odmitnuto(404, "Uživatel neexistuje.")
            data[j] = {**data[j], **zmena}
            self._zapis(data)

    def pridej(self, j, heslo):
        j = over_jmeno(j)
        over_heslo(heslo)
        with self._zamek:
            data = dict(self._nacti())
            if j in data:
                raise Odmitnuto(409, "Uživatel s tímhle jménem už je.")
            if len(data) >= MAX_UZIVATELU:
                raise Odmitnuto(507, "Uživatelů je moc.")
            data[j] = {"h": soukroma.hash_hesla(heslo), "b": False, "t": int(time.time()), "p": 0}
            self._zapis(data)
        return j

    def zmen_heslo(self, j, heslo):
        over_heslo(heslo)
        self._uprav(jmeno(j), {"h": soukroma.hash_hesla(heslo)})

    def blokuj(self, j, ano=True):
        self._uprav(jmeno(j), {"b": bool(ano)})

    def smaz(self, j):
        with self._zamek:
            data = dict(self._nacti())
            if data.pop(jmeno(j), None) is None:
                raise Odmitnuto(404, "Uživatel neexistuje.")
            self._zapis(data)

    def seznam(self):
        with self._zamek:
            data = self._nacti()
        return sorted(({"jmeno": j, "blokovany": bool(u.get("b")), "t": u.get("t") or 0, "p": u.get("p") or 0}
                       for j, u in data.items() if isinstance(u, dict)), key=lambda u: u["jmeno"])

    def prihlas(self, j, heslo):
        """True = jméno i heslo sedí a účet není zablokovaný."""
        with self.limit.zamek:
            self.limit.hlidej()
            u = self._ucet(j)
            # i neexistující jméno spočítá hash, ať z doby odpovědi nejde poznat, kdo tu účet má
            sedi = soukroma.heslo_odpovida(str(heslo or ""), u["h"] if u else _ATRAPA)
            if u and not u.get("b") and sedi:
                ok = True
            else:
                self.limit.chyba()
                ok = False
        if ok:
            self._uprav(jmeno(j), {"p": int(time.time())})
        return ok

    @staticmethod
    def _token(j, h):
        return hmac.new(h.encode("utf-8"), b"nokturno-ucet:" + j.encode("utf-8"), hashlib.sha256).hexdigest()

    def prihlaseny(self, headers):
        """Jméno přihlášeného nezablokovaného uživatele, jinak ""."""
        j, _, token = _cookie(headers, COOKIE).rpartition(".")   # jméno smí mít tečku, token ne
        u = self._ucet(j) if j and token else None
        if not u or u.get("b"):
            return ""
        return j if hmac.compare_digest(token, self._token(j, u["h"])) else ""

    def cookie(self, j, https, odhlasit=False):
        u = None if odhlasit else self._ucet(j)
        return set_cookie(COOKIE, f"{jmeno(j)}.{self._token(jmeno(j), u['h'])}" if u else "", https)
