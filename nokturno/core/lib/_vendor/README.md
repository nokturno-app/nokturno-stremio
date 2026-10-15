# Přibalené knihovny

Jen pro vlastní úložiště přes SMB (`lib/smbfs.py`, `PysmbBackend`) v Home Assistantu
a v aplikaci pro Stremio. Jádro jinak stojí jen na standardní knihovně a integrace HA
ani aplikace nemají žádné `requirements` – proto jsou knihovny tady.

| Knihovna | Verze | Licence | Co je vynecháno |
|----------|-------|---------|-----------------|
| pysmb (`smb/`, `nmb/`) | 1.2.15 | zlib/libpng (`LICENSE.pysmb`) | `SMBHandler.py`, `SMBProtocol.py`, `NetBIOSProtocol.py` (urllib a Twisted) |
| pyasn1 (`pyasn1/`) | 0.6.4 | BSD-2-Clause (`LICENSE.pyasn1`) | nic |
| `tqdm.py` | – | vlastní náhrada | ukazatel průběhu pysmb nepotřebujeme |

Složka se přidává na **konec** `sys.path` (`smbfs._vendor_path`), takže knihovna
nainstalovaná v systému má přednost. Do doplňku pro Kodi se nekopíruje
(`tools/sync_core.py`): Kodi má SMB samo (`kodi_smb.py`).
Aktualizace: `pip download --no-deps pysmb pyasn1`, rozbalit, vynechat totéž, `python3 -m unittest`.
