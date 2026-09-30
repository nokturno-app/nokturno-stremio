"""HTTPS v domácí síti přes local-ip.co.

Stremio přijme doplněk přes http jen z 127.0.0.1. Když služba běží na jiném stroji
v síti (addon v Home Assistantu, NAS), potřebuje HTTPS s platným certifikátem.
Stejně jako Luna na to bereme local-ip.co: `192-168-1-10.my.local-ip.co` se přeloží
na 192.168.1.10 a certifikát `*.my.local-ip.co` i jeho klíč služba sama zveřejňuje
právě k tomuhle účelu. Certifikát platí půl roku, proto se stahuje při startu
a pak jednou denně; nový platí pro další spojení bez restartu.
"""
import ipaddress
import logging
import os
import ssl
import threading
import time
import urllib.request

_LOGGER = logging.getLogger("nokturno.tls")

ZDROJ = "https://local-ip.co/cert/"
DOMENA = "my.local-ip.co"
OBNOVA_S = 24 * 3600


def _stahni(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Nokturno"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def _mezilehly(cert_pem, slozka):
    """Mezilehlý certifikát vydavatele podle AIA (caIssuers) v certifikátu.
    `chain.pem` na local-ip.co je zastaralý (jiná CA), proto si ho dohledáme sami."""
    docasny = os.path.join(slozka, "leaf.tmp")
    with open(docasny, "wb") as f:
        f.write(cert_pem)
    try:
        # ponytail: soukromé API CPythonu, jiný parser X.509 ve stdlib není
        adresy = ssl._ssl._test_decode_cert(docasny).get("caIssuers") or ()
    finally:
        os.remove(docasny)
    for url in adresy:
        data = _stahni(url)
        if b"BEGIN CERTIFICATE" not in data:
            data = ssl.DER_cert_to_PEM_cert(data).encode("ascii")
        return data
    return b""


def stahni(slozka):
    """Stáhne certifikát a klíč do `slozka` a doplní řetěz. Při výpadku zůstanou staré soubory."""
    os.makedirs(slozka, exist_ok=True)
    cert = _stahni(ZDROJ + "server.pem")
    klic = _stahni(ZDROJ + "server.key")
    try:
        retez = _mezilehly(cert, slozka)
    except Exception as err:  # noqa: BLE001 – bez řetězu to aspoň na desktopu projde
        _LOGGER.warning("řetěz certifikátu se nedohledal: %s", err)
        retez = b""
    for jmeno, obsah in (("cert.pem", cert.rstrip() + b"\n" + retez), ("key.pem", klic)):
        docasny = os.path.join(slozka, jmeno + ".tmp")
        with open(docasny, "wb") as f:
            f.write(obsah)
        os.chmod(docasny, 0o600)
        os.replace(docasny, os.path.join(slozka, jmeno))


def nacti(ctx, slozka):
    ctx.load_cert_chain(os.path.join(slozka, "cert.pem"), os.path.join(slozka, "key.pem"))


def kontext(slozka):
    """SSL kontext s certifikátem local-ip.co; None, když ho nejde získat ani z dřívějška."""
    try:
        stahni(slozka)
    except Exception as err:  # noqa: BLE001 – bez sítě při startu vezmeme uložený
        _LOGGER.warning("certifikát local-ip.co se nestáhl: %s", err)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        nacti(ctx, slozka)
    except (OSError, ssl.SSLError) as err:
        _LOGGER.error("HTTPS vypnuté, certifikát chybí: %s", err)
        return None
    threading.Thread(target=_obnova, args=(ctx, slozka), daemon=True).start()
    return ctx


def _obnova(ctx, slozka):
    while True:
        time.sleep(OBNOVA_S)
        try:
            stahni(slozka)
            nacti(ctx, slozka)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("obnova certifikátu local-ip.co selhala: %s", err)


def https_zaklad(zaklad, port):
    """`http://192.168.1.10:7140` → `https://192-168-1-10.my.local-ip.co:7141`.
    Jen pro soukromou IPv4 v síti a pro loopback (`127-0-0-1.my.local-ip.co` se přeloží na 127.0.0.1);
    jinak None (adresa zůstane, jak je). Loopback nutně potřebuje HTTPS: Stremio i Nuvio v Androidu
    z `stremio://`/`nuvio://` vyrobí https a na HTTP portu pak hlásí „Unable to parse TLS packet header"."""
    if not port or not zaklad.startswith("http://"):
        return None
    host = zaklad[len("http://"):].rsplit(":", 1)[0]
    try:
        ip = ipaddress.IPv4Address(host)
    except ValueError:
        return None
    if not (ip.is_private or ip.is_loopback):
        return None
    return f"https://{host.replace('.', '-')}.{DOMENA}:{port}"


if __name__ == "__main__":
    assert https_zaklad("http://192.168.1.10:7140", 7141) == "https://192-168-1-10.my.local-ip.co:7141"
    assert https_zaklad("http://127.0.0.1:7140", 7141) == "https://127-0-0-1.my.local-ip.co:7141"
    assert https_zaklad("http://nokturno.stream", 7141) is None
    assert https_zaklad("https://192.168.1.10:7141", 7141) is None
    assert https_zaklad("http://192.168.1.10:7140", 0) is None
    print("ok")
