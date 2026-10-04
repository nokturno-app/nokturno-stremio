# Nokturno pro Stremio

Doplněk, který k filmům a seriálům ve Stremiu (i v Nuviu a dalších klientech
s doplňky Stremia) přehraje soubory z tvého **vlastního úložiště** (WebDAV).
Jako volitelnou doplňkovou službu si zapneš i vyhledávače třetích stran
**WebShare**, **Sosáč**, **HellSpy**, **Sledujteto**, **FastShare / Sdilej.cz**,
**Přehraj.to** a **CZtor**. Nokturno samo žádný obsah nehostuje ani nešíří.

## Instalace (od 9.0.0 jen u tebe)

Veřejná instance na `nokturno.stream` končí 30. 9. 2026 (od 29. 9. už nevydává nová nastavení,
`/configure` vrací 410). Nokturno pro Stremio je od 9.0.0 **jen aplikace, která běží na počítači,
NASu nebo Android TV boxu uživatele**. Stáhne se z
[vydání](https://github.com/nokturno-app/nokturno-stremio-app/releases) (Windows, macOS Intel a ARM,
Linux amd64/arm64/arm/386, APK), po spuštění se otevře `http://<adresa toho stroje>:7140/configure`.
Aplikace se sama aktualizuje (`update.json` ve stejném repu, balík ověřuje SHA-256 a verzi,
která nenaběhne, vrátí). Uživatelský návod: README repa `nokturno-stremio-app` a článek nápovědy
[Nokturno pro Stremio – aplikace](https://nokturno-app.github.io/nokturno-napoveda/cs/stremio-aplikace).

Stremio na témže stroji bere adresu `http://127.0.0.1:7140/…`. Z jiného zařízení v síti
potřebuje HTTPS: aplikace k tomu otevře port 7141 s adresou `https://192-168-1-10.my.local-ip.co:7141`
(certifikát z local-ip.co, jako Luna). Vypnutí `--bez-https`.

## Rodina Nokturno

Nokturno má tři klienty, všechny stojí na společném jádru (soukromé repo `nokturno-app/nokturno-jadro`, `nokturno/core/` je jeho kopie):

| Klient | Co dělá | Navíc oproti Stremiu |
|---|---|---|
| [**Nokturno pro Kodi**](https://github.com/nokturno-app/plugin.video.nokturno) | plnohodnotný doplněk s menu, výběrem streamu, stahováním, Hlídanými, SyncWatch, Traktem a TV programem | Luna, titulky z OpenSubtitles, synchronizace mezi zařízeními |
| [**Nokturno pro Home Assistant**](https://github.com/nokturno-app/nokturno-ha) | HACS integrace, karta a služby; přehrává přes doplněk pro Kodi, hlídá nové díly sledovaných seriálů | Luna, stahování do HA |
| **Nokturno pro Stremio** (tento repozitář) | aplikace u uživatele, jen JSON pro Stremio, Nuvio a Streamlet | – |

Stremio je záměrně nejjednodušší: nemá Lunu (ta má vlastní oficiální doplněk). CZtor (od 8.4.0)
se páruje PINem ve formuláři: do adresy doplňku jde jen náhodný klíč a aplikace drží přihlášení zapečetěné
tímto klíčem ([nápověda](https://nokturno-app.github.io/nokturno-napoveda/cs/cztor)).
Kdo chce víc – stahování, titulky z OpenSubtitles, Trakt, TV program – použije Kodi.

## Co umí

- **Streamy ke všemu s IMDb id.** Doplněk se chytá na všem, co má identifikátor IMDb (i `tmdb:` id od klientů), a k tomu přihodí své streamy. Detail titulu a díly seriálů dodá Stremio z Cinemety.
- **Vlastní úložiště** (od 3.1.0) – až tři WebDAV složky s vlastními soubory ve formuláři (s ověřením). Soubory jsou mezi streamy první.
- **Sedm volitelných vyhledávačů třetích stran** – WebShare, Sosáč, HellSpy, Sledujteto, FastShare / Sdilej.cz, Přehraj.to a CZtor; žádný není povinný. FastShare jde i s účtem ze Sdilej.cz ([nápověda](https://nokturno-app.github.io/nokturno-napoveda/cs/sdilej-cz)). Přehraj.to (od 7.0.4) chce ve formuláři vlastní účet jako WebShare nebo Sledujteto: bez přihlášení API nevydá token a HTML z jedné serverové adresy dostane HTTP 429.
- **Volitelné katalogy** (od 5.1.0) – seznamy ze Sosáče a TMDB, žebříček „Nejsledovanější tento týden“; každý se zapíná zvlášť ve formuláři. Katalogy z dashboardu (sezónní a tematické, třeba Vánoce nebo Film pro dnešní den) jsou v doplňku vždy a první (od 8.4.0).
- **Přehrávání přes aplikaci** (od 9.6.1) – vlastní úložiště a FastShare chtějí přihlášení, které přehrávač Stremia nepošle (Stremio pro Android `proxyHeaders` ignoruje), proto soubor přeposílá `/play/` aplikace s `Range`. Úložiště stačí dosažitelné z aplikace. 
- **Zprávy z dashboardu** – položka „📢 Nokturno" jako první stream, jen na instanci s `NOKTURNO_TRAFFIC_TOKEN` (v aplikaci vypnuté, zavaděč nastavuje `NOKTURNO_TRAFFIC=0`); **jazyky** čeština, slovenština, angličtina i maďarština. Formulář je česky a slovensky, nastavení ze slovenského formuláře má slovensky i hlášky doplňku.

| | |
|---|---|
| Filmy | ano |
| Seriály | ano, včetně jednotlivých dílů |
| Titulky | ano, z WebShare a Sledujteto |
| Zvuk | jazyk, kanály a kodek – z hlavičky souboru, u Sledujteto přímo z API; u FastShare jen s neomezeným stahováním (na kredit by čtení hlaviček ubíralo kredit) |
| Katalogy | volitelně (od 5.1.0): Sosáč – nejpopulárnější filmy a seriály, nově přidané; TMDB – trendy, populární, nejlépe hodnocené (jen s klíčem `NOKTURNO_TMDB_KEY`, v aplikaci volba `tmdb_key` v `nokturno.json`). Jedna cache pro všechny, obnova po 6 h. Katalogy z dashboardu (sezónní a tematické) bez přepínače, vždy první |
| Popisy titulů | ne – detail k položkám katalogů i k ostatním titulům dodává Cinemeta |

Streamy se řadí podle kvality a preferovaného jazyka, protože ve Stremiu je vidět
jen několik prvních řádků. Kvalitu, velikost, bitrate, jazyky zvuku i titulky
odhaduje jádro a co neví ze zdroje, přiznaně označí vlnkou (`~Full HD`).

## Anonymní statistiky

Doplněk posílá anonymní statistiky na stejný sběrný bod jako Nokturno pro Kodi
a Home Assistant: náhodný identifikátor nastavení, verzi, které zdroje jsou
zapnuté a u kterých titulů se otevřely streamy – nejvýš jednou za 6 hodin.
Jedna „instalace" je jedno nastavení doplňku (vlastní adresa), ne celý server.
Účty ani adresa doplňku se neposílají. Vypnutí: `NOKTURNO_STATS=0` (v aplikaci volba `"stats": false`
v `nokturno.json`, zavaděč proměnnou prostředí přepíše podle ní). Od 9.0.4 jde statistiky i hlášení o pádech
vypnout přímo na `/configure` (sekce *Aplikace na tomhle zařízení*, i na Androidu); volba se uloží do
`<data>/cache/aplikace.json`, má přednost před `nokturno.json` a platí hned. Přes Cloudflare (`Cf-Connecting-IP`) ji změnit nejde. I po vypnutí se nejvýš jednou za 6 hodin pošle jen náhodný identifikátor a verze, aby bylo vidět, že nastavení žije – žádné tituly ani zdroje.

## Hlášení o pádech

Když při obsluze požadavku nastane neošetřená chyba v kódu (ne výpadek zdroje),
služba pošle na stejný server krátké hlášení: typ chyby, místo v kódu, verzi
a posledních pár řádků vlastního logu. Adresy, účty, IP a nastavení z adresy
doplňku se předem vymažou. Stejná chyba odejde nejvýš jednou za verzi. Id je
náhodné, jedno na server (`<data>/pady/id`). Vypnutí: `NOKTURNO_CRASH_REPORTS=0` (v aplikaci `"crash_reports": false`).

## Soukromá instance (adresa doplňku v internetu)

Adresa doplňku může být na veřejné doméně (Cloudflare Tunnel, reverzní proxy), aniž by ji mohl
použít kdokoli. V `nokturno.json`:

```json
{"host": "127.0.0.1", "soukroma": true}
```

- `host` je adresa poslechu (výchozí `0.0.0.0`, za proxy `127.0.0.1`), v příkazové řádce `--host`.
- `soukroma` (prostředí `NOKTURNO_SOUKROMA=1`): cesty doplňku (`/c/<nastavení>/manifest.json`, `stream`,
  `catalog`, `meta`, `play`, i bez `/c/`) obslouží jen nastavení, jejichž otisk je v `<data>/cache/povolena.txt`
  (jeden otisk na řádek, `#` komentář, ruční úprava platí bez restartu). Ostatní dostanou 403 bez těla.
  `/health` jde vždy. Otisk je `config.fingerprint` nastavení bez tokenu identity.
- Požadavek s hlavičkou `Cf-Connecting-IP` (přišel přes Cloudflare) smí jen na cesty doplňku, formulář,
  `/check`, `/cztor/*`, `/identita*`, `/z/`, `/terms` ani POST nedostane. Hlavní ochrana má být v proxy,
  tohle je druhá pojistka.
- `public_url` (prostředí `NOKTURNO_PUBLIC_URL`, v příkazové řádce `--public-url`): veřejná adresa doplňku,
  kterou ukáže `/configure` (např. `https://nokturno.example.cz`). Hodí se, když se formulář otevírá jinudy
  než přes veřejnou adresu (Tailscale, domácí síť). Bez ní se adresa bere z požadavku.
- `NOKTURNO_PROFILY=0`: starý režim bez profilů – adresa doplňku nese celé nastavení včetně hesel.
  Už uložené profily se dál čtou, nové nejdou zakládat, ukládat, přejmenovat ani mazat.
- `NOKTURNO_HESLO`: heslo k nastavení a profilům (HTTP Basic, jméno libovolné). Doplněk sám heslo nechce.
- Obojí jde od 9.9.2 přepnout i na `/configure` v sekci Aplikace (jen správce, mimo veřejnou adresu).
  Uloží se do `aplikace.json` v datové složce (heslo jen jako hash) a má přednost před prostředím.

Jak nastavení povolit:

1. Na `/configure` (otevřeném mimo veřejnou adresu, třeba v Tailscale) vyrob adresu a klikni na
   **Povolit na tomhle serveru** (`POST /povolit`, jen s hlavičkou `X-Nokturno`, ať ho nepošle cizí web).
2. `nokturno --povolit <adresa doplňku>` (bere i samotný otisk), s `--data`, když datová složka není výchozí.
3. Ručně připsat otisk do `povolena.txt`.

Změněné nastavení je nová adresa i nový otisk, povolit ho je potřeba znovu.

## Vlastní instance (pro vývojáře a pokročilé)

Běžný uživatel má aplikaci (viz Instalace). Tady je spuštění ze zdrojáků nebo v Dockeru.
Zavaděč a balení: `baleni/` (`zavadec.py`, `sestav.sh`, `balik.sh`, `android/`), CI `.github/workflows/baleni.yml`.

### Spuštění

```bash
cp .env.example .env      # vyplň účty
docker compose up -d
```

Pak otevřít `http://<adresa stroje>:7127/configure`, vyplnit účty a kliknout na
**Přidat do Stremia** nebo **Přidat do Nuvia**. Do Streamletu se adresa vkládá ručně
(*Zkopírovat adresu*). Na `/` je úvodní stránka s rozcestníkem všech repozitářů Nokturna.

Bez Dockeru to jde taky, závislosti žádné nejsou:

```bash
python3 -m nokturno.server --port 7127
```

### Účty jsou v adrese, ne na serveru

Stremio nemá soubor nastavení. Účty se nosí **zakódované v adrese doplňku**, takže
každý, kdo si ho přidá, má vlastní a hledá pod sebou. Server si z nastavení ukládá
jen zašifrované přihlášení k CZtoru; klíč k němu je v adrese.

```
http://<stroj>:7127/c/<nastavení>/manifest.json
```

Tu adresu vyrobí formulář na `/configure`. Uschovej si ji – bez ní se ke svému
nastavení nedostaneš a vyrobíš si prostě novou.

> Kódování **není šifra**. Kdo adresu má, stahuje z tvého WebShare. Nikomu ji
> neposílej.

Jde i nastavení z prostředí, pak má celá instance jednu konfiguraci a adresa je
bez prefixu. Takhle běžela verze 0.1.0 a funguje to dál.

Hodnoty jsou stejné jako v doplňku pro Kodi, takže se dají opsat z jeho
`settings.xml`.

| Proměnná | Co to je |
|---|---|
| `NOKTURNO_WS_USERNAME`, `NOKTURNO_WS_PASSWORD` | WebShare; místo hesla jde vložit i 40znakový salted hash |
| `NOKTURNO_STREAMUJ_USERNAME`, `NOKTURNO_STREAMUJ_PASSWORD` | Streamuj, kvůli Sosáči; místo hesla i hotový `md5(md5(heslo))` |
| ~~`NOKTURNO_LUNA_URL`, `NOKTURNO_LUNA_TOKEN`~~ | od 0.2.5 se nečtou – Luna má vlastní doplněk do Stremia |
| `NOKTURNO_ST_EMAIL`, `NOKTURNO_ST_PASSWORD` | Sledujteto – hledání chce účet, přehrávání Premium |
| `NOKTURNO_FS_USERNAME`, `NOKTURNO_FS_PASSWORD` | FastShare (od 5.1.0) – hledá se i bez účtu, přehrání jde z kreditu nebo neomezeného tarifu. Soubor přeposílá aplikace přes `/play/` s cookie z přihlášení |
| `NOKTURNO_FS_PROVIDER` | `sdilej` = účet výš je ze Sdilej.cz (týž katalog, jiné účty); prázdné = FastShare |
| `NOKTURNO_FS_PRIMO` | `1` = soubor z FastShare jde přímo ke klientovi (`proxyHeaders`), ne přes aplikaci; umí Nuvio a Stremio na počítači, Stremio pro Android ne |
| `NOKTURNO_PT_EMAIL`, `NOKTURNO_PT_PASSWORD` | Přehraj.to (od 7.0.4) – s Premium účtem přijde původní soubor, bez něj jen překódovaný. Na veřejné instanci se nenastavuje: účet je per-uživatel ve formuláři, jako u ostatních zdrojů |
| `NOKTURNO_TMDB_KEY` | klíč TMDB instance pro katalogy TMDB (od 5.1.0); bez něj se nabízejí jen katalogy Sosáče. Ve formuláři se nezadává |
| `NOKTURNO_HS_ENABLED` | `1` zapne HellSpy (veřejný, bez účtu); od 10.4.1 ve výchozím stavu vypnutý |
| `NOKTURNO_DAV1_URL` … `NOKTURNO_DAV3_NAME` | až tři vlastní úložiště (WebDAV): `_URL`, `_USERNAME`, `_PASSWORD`, `_NAME` |
| `NOKTURNO_PREF_LANG`, `NOKTURNO_PREF_SURROUND`, `NOKTURNO_SORT`, `NOKTURNO_HIDE_SD`, `NOKTURNO_MAX_BITRATE` | předvolby řazení a filtrování |
| `NOKTURNO_KATALOGY` | zapnuté katalogy, klíče oddělené čárkou (viz `nokturno/katalogy.py`) |
| `NOKTURNO_ID_SECRET` | tajemství pro podepsanou identitu v adrese (limity na uživatele); bez něj se identita nevydává (v aplikaci netřeba) |
| `NOKTURNO_VEREJNA` | `1` = služba vystavená do internetu za proxy: požadavky z `127.0.0.1` se berou jako cizí (bez účtů z prostředí, bez úložišť v síti). V aplikaci vypnuté |
| `NOKTURNO_HTTPS_PORT` | druhý posluchač s HTTPS přes local-ip.co pro Stremio z jiného zařízení v síti (zavaděč nastavuje 7141) |
| `NOKTURNO_STATS` | `0` vypne anonymní statistiky, viz níže |
| `NOKTURNO_CRASH_REPORTS` | `0` vypne hlášení o pádech služby, viz níže |
| `NOKTURNO_HOST`, `NOKTURNO_PORT`, `NOKTURNO_DATA` | na čem poslouchat (v Dockeru `0.0.0.0`, za reverzní proxy `127.0.0.1`), port a složka s cache |
| `NOKTURNO_CONFIGURE_PREFILL` | `1` předvyplní formulář účty z prostředí – jen na vlastní instanci, nikdy na veřejné |

Hlavní je vlastní úložiště (`NOKTURNO_DAV1_URL`…), úložiště třetích stran jsou volitelná a ve výchozím stavu vypnutá.

### Jak to funguje

```
Stremio ──▶ /stream/movie/tt0133093.json ──▶ Engine.streams() ──▶ úložiště, WebShare, Sosáč, HellSpy,
                                                                     Sledujteto, FastShare, Přehraj.to, CZtor
                        ▼
            streamy s odkazem na /play/<payload>
                        ▼
Přehrávač ─▶ /play/<payload> ──▶ Engine.resolve() ──▶ 302 na soubor
```

**Proč to obchází přes `/play/`.** Odkazy WebShare, HellSpy, Sledujteto, Přehraj.to a CZtor nesou podpis a platí
jen chvíli. Kdyby se vydaly rovnou v odpovědi, do chvíle, než si uživatel stream
vybere, by vyhasly. Endpoint `/play/` proto soubor rozklíčuje až ve chvíli, kdy se
na něj přehrávač skutečně obrátí. Přijímá jen odkazy se známým schématem, jinak by
z něj šlo udělat otevřené přesměrování.

#### Proč bez závislostí

Jádro je čistý Python bez vazby na hostitele a jeho volání jsou blokující. HTTP
vrstva proto stojí na `http.server` ze standardní knihovny; `ThreadingHTTPServer`
obslouží každý požadavek ve vlákně, takže dlouhé hledání na WebShare nezablokuje
ostatní dotazy. Nasazení je tím jen zkopírování zdrojáků, bez `pip install`.

#### Jádro se needituje tady

`nokturno/core/` je **vysypaná kopie** z repa `nokturno-core`. Oprava udělaná tady
se při příštím rozeslání přepíše. Patří do jádra:

```bash
cd ../nokturno-jadro
python3 tools/sync_core.py --check --diff stremio
python3 tools/sync_core.py stremio
```

### Testy

```bash
python3 -m unittest discover -s tests -v
```

Nesahají na síť a nepotřebují účty. Jádro má vlastní testy ve svém repu.

## Vydání

Vydává jen CI, nikdy účet člověka:

1. Číslo verze v `VERZE` (`nokturno/routes.py`) podle pravidel rodiny, commit do `main`.
2. Anotovaný tag `v<verze>` a push. Workflow `.github/workflows/baleni.yml` postaví 7 programů (PyInstaller:
   Linux amd64/arm64/arm/386 v Debianu přes QEMU, Windows, macOS Intel a ARM) a APK (Chaquopy, Gradle).
3. Job `vydani` zkontroluje, že tag sedí s `VERZE`, vyrobí balík pro zavaděč (`baleni/balik.sh`: zip se složkou
   `nokturno/` a `update.json` s SHA-256) a ve veřejném repu `nokturno-app/nokturno-stremio-app` založí release
   „Nokturno pro Stremio <verze> – opravy chyb“. Stabilní verze pak přepíše `update.json` v `main` toho repa,
   beta (`9.1.0b1`) jde jen jako pre-release a `update.json` nemění.
4. Do veřejného repa zapisuje **GitHub App `nokturno-release`** organizace (instalovaná jen na
   `nokturno-stremio-app`, Contents: read and write). V tomhle repu je k tomu proměnná `NOKTURNO_APP_ID`
   a tajemství `NOKTURNO_APP_KEY` (privátní klíč aplikace), volitelně `NOKTURNO_KEYSTORE_B64`
   a `NOKTURNO_KEYSTORE_HESLO` pro podpis APK (bez nich ladicí klíč).

Ruční spuštění workflow (`workflow_dispatch`) vyrobí jen artefakty běhu, nic nevydá.

## Pomoc

- **Dotazy, rady a novinky:** [Discord Nokturna](https://discord.gg/ChmMPmDDEj) (fórum #pomoc, novinky v #novinky).
- **Řešení častých potíží:** [nápověda Nokturna](https://nokturno-app.github.io/nokturno-napoveda/).
- **Chyba v kódu:** na Discordu (#pomoc). Adresu doplňku neposílej, jsou v ní tvoje účty.

## Právní upozornění

Nokturno je především přehrávač a správce tvého vlastního úložiště – obsah, který
si nahraješ a zpřístupníš (např. přes WebDAV), přehrává napřímo. Jako doplňkovou
službu si můžeš volitelně napojit i některé veřejně dostupné vyhledávače třetích
stran (WebShare, Sosáč, HellSpy, Sledujteto, FastShare, Přehraj.to, CZtor, Luna,
OpenSubtitles) – v tom případě je Nokturno jen technické rozhraní, samo žádný
obsah nehostuje, neukládá ani neposkytuje.

Nokturno smíš používat jen k obsahu, ke kterému máš zákonné oprávnění, licenci
nebo jiný právní titul. Vyhledávání, zpřístupňování nebo přehrávání autorsky
chráněného obsahu bez souhlasu nositelů práv je zakázáno.

Nokturno je poskytováno „tak, jak je“, bez záruky funkčnosti, dostupnosti ani
legálnosti zdrojů třetích stran. Za způsob použití odpovídáš výhradně ty.
Provozovatel si vyhrazuje právo kdykoli omezit nebo ukončit přístup.

Plný text a kontakty pro nahlášení nelegálního obsahu u jednotlivých zdrojů:
[nokturno.stream/terms](https://nokturno.stream/terms).

## Licence

Zdrojový kód je soukromý, aplikace se šíří jen přes vydání v `nokturno-app/nokturno-stremio-app`. Kopírování, úpravy a šíření bez
svolení autora nejsou dovolené – viz [LICENSE](LICENSE).
