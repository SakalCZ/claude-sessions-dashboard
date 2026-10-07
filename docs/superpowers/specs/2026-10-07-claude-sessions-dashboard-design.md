# Claude Sessions Dashboard — design

Datum: 2026-10-07
Stav: schváleno v brainstormingu, čeká na review specu

## 1. Účel

Uživatel používá Claude Code paralelně v mnoha adresářích pod `~/Documents/Development`
(např. `acme/shop`, `acme/shop_2`, `acme/shop_local/shop`, worktrees, `budget`,
`client/website`) a ztrácí přehled, které issue řeší ve které session.
Opakovaně se kvůli tomu ptá samotného Clauda („ve kterém adresáři jsem pracoval na PROJ-…“).

Dashboard v prohlížeči ukáže **jeden řádek = jedna session** s adresářem, issue, větví,
poslední aktivitou a živým stavem, a umožní session jedním klikem obnovit (`claude --resume`).

### Kritéria úspěchu

- Napíšu „563“ do hledání a během sekundy vidím, ve kterém adresáři a které session se PROJ-563 řešil.
- Z řádku zkopíruji nebo rovnou spustím správný resume příkaz (správné `cwd` + `sessionId`).
- Vidím, které sessions právě běží (busy / idle).
- Ke každé session si můžu nastavit stav a krátkou poznámku. Hotové a archivované se ve výchozím stavu skryjí.

### Mimo rozsah (YAGNI)

- Přepnutí na již otevřený iTerm2 tab běžící session.
- Čtení obsahu odpovědí asistenta, statistiky tokenů a nákladů.
- Přístup odjinud než z localhostu, autentizace, více uživatelů.
- Napojení na Jira API (stav issue apod.). Jira klíče jsou jen odkazy.

## 2. Zdroje dat (ověřeno na reálných datech 2026-10-07)

| Zdroj | Obsah |
|---|---|
| `~/.claude/projects/<enc-cwd>/<sessionId>.jsonl` | Transcript session, jeden JSON záznam na řádek. Aktuálně 46 souborů, celkem ~205 MB, největší ~17 MB. |
| `~/.claude/projects/<enc-cwd>/<sessionId>/` | Podsložka se subagenty apod. **Ignorujeme.** |
| `~/.claude/sessions/<pid>.json` | Běžící instance: `pid`, `sessionId`, `cwd`, `status` (`busy`/`idle`), `procStart`, `name`, `updatedAt`. |

`<enc-cwd>` = `cwd` s každým znakem mimo `[A-Za-z0-9]` nahrazeným `-`
(`/Users/me/…/acme/shop_2` → `-Users-me-…-acme-shop-2`). Kódování je ztrátové, proto se
skutečná cesta bere z obsahu záznamů, ne z názvu složky.

Relevantní typy záznamů v JSONL:

- `user` / `assistant`: mají `uuid`, `parentUuid`, `timestamp`, `cwd`, `gitBranch`, `isSidechain`, `isMeta` a `message.content` (string nebo list bloků).
- `custom-title` (`customTitle`) a `agent-name` (`agentName`): titulek session.
- `last-prompt` (`lastPrompt`).
- Ostatní typy (`attachment`, `mode`, `bridge-session`, `artifact-*`, …) parser ignoruje.

## 3. Architektura

```
agent-orgestrator/
├── server.py                 # HTTP server (stdlib http.server, ThreadingHTTPServer), routing, API
├── sessions.py               # parsování ~/.claude → seznam řádků; čistá logika bez I/O na HTTP
├── live.py                   # detekce běžících instancí z ~/.claude/sessions
├── notes.py                  # čtení a atomický zápis poznámek
├── iterm.py                  # otevření nového iTerm2 tabu přes osascript
├── static/
│   ├── index.html            # markup + CSS
│   ├── filter.js             # čisté funkce: filtrování, seskupení, relativní čas (testované v Node)
│   └── app.js                # vykreslení a akce (jen DOM API, žádné innerHTML)
├── launchd/
│   ├── install.sh            # nasazení + registrace LaunchAgentu
│   └── uninstall.sh
├── tests/
│   ├── helpers.py            # generuje falešný ~/.claude (JSONL + pid soubory) do temp adresáře
│   ├── js/test_filter.js     # testy filter.js (node + assert, bez závislostí)
│   └── test_*.py             # unittest (stdlib)
└── docs/superpowers/specs/   # tento dokument
```

- **Jazyk:** Python 3 (na stroji `/usr/local/bin/python3`, 3.14), pouze standardní knihovna, bez závislostí.
- **Server:** `ThreadingHTTPServer`, bind **výhradně `127.0.0.1`**, výchozí port **7333**. Lze přepsat
  `--port` nebo env `CLAUDE_DASHBOARD_PORT`.
- **Cesty jdou konfigurovat** (kvůli testům): `--claude-dir` (výchozí `~/.claude`) a `--data-dir` (výchozí
  `~/Library/Application Support/claude-dashboard`).

### Nasazení přes launchd (změna oproti sekci 1 brainstormingu)

macOS (TCC) chrání `~/Documents`, a proces spuštěný z launchd tam může dostat `Operation not permitted`.
Proto:

- `install.sh` zkopíruje aplikaci (`*.py`, `static/`) do `~/Library/Application Support/claude-dashboard/app/`
  a LaunchAgent spouští tuto kopii.
- Poznámky se ukládají do `~/Library/Application Support/claude-dashboard/notes.json`, tedy mimo repo a mimo `~/Documents`.
- Po změně kódu se `install.sh` spustí znovu: zkopíruje soubory a restartuje agenta.
- Při vývoji lze server pustit přímo z repa (`python3 server.py --port 7334`).

LaunchAgent:

- Label `local.claude-sessions-dashboard`, plist v `~/Library/LaunchAgents/`.
- `RunAtLoad=true`, `KeepAlive=true`.
- Log do `~/Library/Logs/claude-dashboard.log`.
- Registrace přes `launchctl bootstrap gui/$UID`, odinstalace přes `launchctl bootout`.
- `install.sh` po startu ověří, že `curl -s http://127.0.0.1:7333/api/health` odpovídá.

## 4. Parsování session (sessions.py)

Vstup: jeden `.jsonl` soubor. Výstup: záznam `Session`. Parsuje se řádek po řádku. Řádek, který není validní
JSON (např. právě rozepsaný), se přeskočí.

### 4.1 Odvozená pole

| Pole | Pravidlo |
|---|---|
| `session_id` | Název souboru bez `.jsonl`. |
| `project_dir` | Název nadřazené složky v `projects/`. |
| `cwd` | První `cwd` ze záznamů, které splňuje `encode(cwd) == project_dir`. Když shodu nesplňuje žádné, nastaví se `warnings += ["cwd-mismatch"]` a použije se první nalezené `cwd`. Bez jakéhokoli `cwd`: `warnings += ["no-cwd"]` a `cwd = None` (resume nejde). |
| `repo` / `worktree` | Pokud `cwd` obsahuje `/.claude/worktrees/<X>`: `repo` = část před `/.claude/worktrees`, `worktree` = `<X>`. Jinak `repo = cwd`, `worktree = None`. |
| `display_dir` | `repo` relativně k `~/Documents/Development` (např. `acme/shop_2`), mimo tento kořen celá cesta s `~`. |
| `prompts` | Skutečné prompty uživatele (pravidla v 4.2), chronologicky. |
| `prompt_count` | `len(prompts)` |
| `first_ts` / `last_ts` | Min a max `timestamp` přes záznamy `user`/`assistant`, porovnávané hodnotou, ne pořadím v souboru. Fallback pro `last_ts`: mtime souboru. |
| `branches` | Unikátní `gitBranch` seřazené podle **posledního** výskytu: při každém výskytu se hodnota přesune na konec, takže poslední prvek je aktuální větev. `branch` = poslední prvek různý od `HEAD`, jinak `HEAD`. |
| `title` | Poslední `custom-title.customTitle`, jinak poslední `agent-name.agentName`, jinak `None`. |
| `last_prompt` | `last-prompt.lastPrompt` (poslední). |
| `root_uuid` | `uuid` prvního záznamu `user`/`assistant` s `isSidechain != true`. Slouží k detekci forků. |
| `jira_keys` | Viz 4.3. |
| `jira_key` | Primární klíč, viz 4.3. |
| `topic` | Viz 4.4. |
| `is_stub` | `prompt_count == 0` |

### 4.2 Co je „skutečný prompt“

Záznam `type == "user"`, žádný z příznaků `isMeta`, `isSidechain`, `isCompactSummary`, `isVisibleInTranscriptOnly`, a zároveň:

- `message.content` je string, nebo list, který obsahuje alespoň jeden blok `type == "text"` a žádný blok `type == "tool_result"`. U listu se texty bloků spojí.
- Text (po `strip()`) nezačíná systémovým tagem `<nazev…` (regex `^<([a-z][a-z0-9_-]*)[\s>]`).
  Výjimkou je `<pasted_content`, protože to je skutečný vložený obsah od uživatele. Tím se přeskočí
  `<command-name>`, `<local-command-*>`, `<task-notification>` (v datech 428×), `<artifact-content-…>`,
  `<system-reminder>` atd.
- Text nezačíná `Caveat:` ani `[Request interrupted`. Prompty jako `[Image #3] …` nebo vložený výstup
  terminálu `[me@db1 ~]$ …` jsou skutečné a zůstávají.
- Text není prázdný.

Pro zobrazení se blok `<pasted_content …>X</pasted_content>` nahradí `[vloženo: <prvních 60 znaků X>]`.
Bílé znaky se kolabují až při zobrazení. Uloží se nejvýš 2000 znaků z každého promptu. Jira URL se hledají
v původním textu, včetně vloženého obsahu.

### 4.3 Jira klíče

Regex klíče: `[A-Z][A-Z0-9]{1,9}-\d+`. Klíče se berou **jen ze spolehlivých zdrojů**:

1. **Větve:** klíč nalezený v každém prvku `branches`.
2. **URL v promptech:** `https?://([a-z0-9-]+)\.atlassian\.net/browse/(KEY)`. Zároveň se zaznamená mapování prefix → host (např. `PROJ` → `acme.atlassian.net`, `SD` → `example.atlassian.net`).
3. **Titulek:** klíč v `title`. Povolena je i mezera místo pomlčky (`PROJ 548` → `PROJ-548`).

Volný text promptů se **neprohledává**, protože by přinesl šum typu `P1-1` nebo `PSR-4`.

- `jira_keys`: všechny nalezené klíče, unikátní, v pořadí výskytu.
- `jira_key` (primární):
  1. klíč z poslední větve v `branches`, která klíč obsahuje (session mohla po práci na `me/feature/PROJ-512-…` přepnout zpět na `master`),
  2. jinak klíč z poslední URL v promptech,
  3. jinak klíč z titulku,
  4. jinak `None`.

Mapa `jira_hosts` (prefix → host) se skládá přes všechny sessions. Klient z ní tvoří odkaz
`https://<host>/browse/<KEY>`. Prefix bez známého hostu se zobrazí jako text bez odkazu.

### 4.4 Titulek, podezřelý titulek, téma

Titulek se v datech přenáší mezi sessions. Např. „country variables refactoring“ je na 6 sessions v
`shop_2` s různými větvemi. Titulek je proto **podezřelý** (`title_suspect = true`), když platí
aspoň jedno:

- (a) titulek obsahuje Jira klíč, který není mezi klíči session z větví nebo URL, a ta session nějaké takové klíče má;
- (b) stejný titulek (po normalizaci: lowercase, strip) má jiná session (jiný řetězec, viz 4.5) se stejným
  `project_dir` a odlišným `jira_key`, přičemž oba `jira_key` nejsou `None`.

`topic` (krátký popis do řádku):

1. `title`, pokud není podezřelý. Odstraní se z něj úvodní Jira URL nebo klíč, protože klíč se zobrazuje zvlášť.
2. Jinak začátek prvního skutečného promptu (jeden řádek, max. 140 znaků).
3. Jinak `last_prompt`.
4. Jinak `"(bez popisu)"`.

Podezřelý titulek se v UI zobrazí sekundárně a šedě s označením „titulek (možná zděděný)“.

### 4.5 Forky a kopie

Soubory se stejným `root_uuid` (bez `None`) tvoří řetězec. Ověřeno: `b0372375` je kopie začátku
`cd6e5aad`, se stejným prvním záznamem. V řetězci je hlavní řádek soubor s nejnovějším `last_ts`,
ostatní jdou do `older_copies` (id, `last_ts`, `prompt_count`).

Poznámky se dědí: když hlavní řádek nemá vlastní poznámku a některá starší kopie ano, zobrazí se poznámka
kopie a při uložení se zapíše pod id hlavního řádku.

### 4.6 Cache

Modulová cache `{path: (mtime_ns, size, Session)}`. Při každém `GET /api/sessions` se projde
`projects/*/*.jsonl` přes `os.scandir` a znovu se parsují jen soubory se změněným `(mtime_ns, size)`.
Smazané soubory se z cache vyhodí. Odvozené vazby mezi sessions (4.4 b, 4.5, `jira_hosts`) se
přepočítávají vždy, protože jsou levné.

## 5. Živý stav (live.py)

Pro každý `~/.claude/sessions/*.json`:

1. Načte se JSON. Nevalidní soubor se přeskočí.
2. Zkontroluje se, že proces žije, a to porovnáním `procStart` s výstupem
   `LC_ALL=C TZ=UTC ps -o lstart= -p <pid>` (po `strip()`). Shoda znamená, že běží. Tím se odfiltrují
   mrtvé pid soubory i znovupoužité pid. Ověřeno: formát se shoduje pouze s `LC_ALL=C TZ=UTC`, protože
   bez nich `ps` vypisuje česky v lokálním čase.
3. Výsledek je mapa `sessionId → {status, pid, name, updated_at}`.

`ps` se volá jednou pro všechny pid (`ps -o pid=,lstart= -p 1,2,3`), ne zvlášť pro každý.

## 6. Poznámky (notes.py)

Soubor `<data-dir>/notes.json`:

```json
{
  "version": 1,
  "notes": {
    "<sessionId>": {"status": "active", "note": "čeká na review od JH", "updated_at": "2026-10-07T12:00:00Z"}
  }
}
```

- `status`: jedno z `active` (aktivní), `waiting` (čeká), `done` (hotovo), `archived` (archiv). Chybějící záznam znamená „bez stavu“.
- `note`: nejvýš 500 znaků, jeden řádek (`\n` se nahradí mezerou).
- Zápis je atomický (`tempfile` ve stejném adresáři, `fsync`, `os.replace`) a pod `threading.Lock`.
- Chybějící nebo nevalidní soubor znamená prázdné poznámky. Nevalidní soubor se před přepsáním přejmenuje na `notes.json.corrupt-<ts>`.
- Poznámky k sessions, které už neexistují (Claude Code staré transcripty maže, `cleanupPeriodDays`), v souboru zůstávají a jen se nezobrazují.

## 7. HTTP API (server.py)

| Metoda | Cesta | Popis |
|---|---|---|
| GET | `/`, `/index.html`, `/app.js`, `/filter.js` | statické soubory ze `static/` (pevný whitelist) |
| GET | `/api/health` | `{"ok": true}` |
| GET | `/api/sessions` | `{generated_at, jira_hosts, rows: [Row…]}` |
| POST | `/api/notes/<sessionId>` | Tělo `{"status"?: str\|null, "note"?: str}` → uložený záznam. `status: null` stav smaže. |
| POST | `/api/open/<sessionId>` | Otevře resume v iTerm2 → 200 `{"ok": true}`. Selhání osascriptu → 502 `{"ok": false, "error": "…"}`. Neznámý nebo neexistující adresář (`no-cwd`, `cwd-missing`) → 409 bez spuštění. |

`Row` (JSON):

```
session_id, cwd, repo, worktree, display_dir, branch, branches[], jira_key, jira_keys[],
topic, title, title_suspect, first_ts, last_ts, prompt_count, is_stub,
recent_prompts[]   (posledních 5, nejnovější poslední),
first_prompt, search_text (všechny prompty, každý max. 300 znaků, celkem max. 60 000),
older_copies[], warnings[],
live: {status, pid} | null,
note: {status, note, updated_at} | null,
resume_cmd         ("cd '<cwd>' && claude --resume <id>", quotováno přes shlex.quote)
```

Validace a bezpečnost:

- `sessionId` v cestě musí odpovídat `^[0-9a-f-]{36}$` a existovat v indexu, jinak 404.
- **Všechny** požadavky (i GET) vyžadují hlavičku `Host` `127.0.0.1:<port>` nebo `localhost:<port>`,
  jinak 403. Tím se brání DNS rebindingu, kdy by cizí web jinak mohl přečíst `/api/sessions` i s obsahem
  transcriptů.
- POST endpointy navíc vyžadují `Content-Type: application/json`. `Origin`, pokud je přítomen, musí být
  `http://127.0.0.1:<port>` nebo `http://localhost:<port>`. Jinak 403.
- Tělo POST má limit 4 KB.
- Statické soubory se servírují jen z whitelistu (`/` → `index.html`), žádný obecný file server.

## 8. Otevření v iTerm2 (iterm.py)

- Příkaz `cd <shlex.quote(cwd)> && claude --resume <session_id>` se skládá **na serveru** z indexu.
  Klient posílá jen `sessionId`.
- Spouští se přes `subprocess.run(["osascript", "-e", SCRIPT, cmd], timeout=10)`. Příkaz se předá jako
  `argv`, takže se nic neinterpoluje do zdrojáku AppleScriptu:

```applescript
on run argv
  set cmd to item 1 of argv
  tell application "iTerm2"
    activate
    if (count of windows) = 0 then
      create window with default profile
    else
      tell current window to create tab with default profile
    end if
    tell current session of current window to write text cmd
  end tell
end run
```

- Při prvním použití se macOS zeptá na oprávnění „python3 chce ovládat iTerm2“ (Automation). Když ho
  uživatel odmítne, osascript vrátí chybu -1743. UI ukáže hlášku s odkazem na Nastavení → Soukromí →
  Automatizace a nabídne zkopírování příkazu.
- Když session právě běží (`live != null`), klient se před otevřením zeptá přes `confirm()`, protože by
  vznikla druhá instance.

## 9. UI (static/index.html)

Tmavý, hustý, tabulkový layout v systémovém monospace/sans fontu. Pracovní nástroj, ne landing page.
Jen vanilla JS, žádné externí knihovny.

**Horní lišta:**

- Fulltext: case-insensitive, více slov = všechna musí sedět. Hledá v `jira_keys`, `topic`, `title`,
  `branches`, `display_dir`, `worktree`, `cwd`, promptech (`search_text`), poznámce a `session_id`.
  **Při neprázdném hledání se neuplatní skrývání podle stavu ani skrývání stubů**, takže „563“ najde
  i session označenou jako hotovou. Filtr adresářů a „jen běžící“ platí dál.
- Chipy adresářů (`display_dir`, multi-select, žádný vybraný = vše).
- Filtr stavu: ve výchozím stavu skryto `done` a `archived`.
- Přepínače: „jen běžící“, „zobrazit prázdné“ (stuby jsou ve výchozím stavu skryté).
- Seskupení: podle adresáře (výchozí), podle issue (`jira_key`, sessions bez klíče ve skupině „bez issue“), nebo bez seskupení.
- Počítadlo „zobrazeno X z Y“.

**Řádek** (v rámci skupiny řazeno podle `last_ts`, nejnovější nahoře):

- Indikátor živého stavu: 🟢 busy / 🟡 idle / nic.
- `jira_key` jako odkaz (je-li host) a `topic`. Pod tím `branch` (+ worktree), počet promptů a 1–2 poslední prompty (zkrácené na ~120 znaků).
- `display_dir`, relativní čas („před 5 min“, tooltip s absolutním časem).
- Akce: **⧉ Kopírovat** (`navigator.clipboard`, fallback `execCommand('copy')`), **▶ Otevřít** (POST /api/open), dropdown stavu a pole poznámky (ukládá se při `blur` a `Enter`).
- Klik na řádek ho rozbalí: `session_id`, celé `cwd`, všechny větve a Jira klíče, posledních 5 promptů, podezřelý titulek (šedě), `older_copies`, `warnings`.

**Obnovování:** každých 10 s se volá `GET /api/sessions`. Re-render zachová rozbalené řádky a
nepřepíše pole poznámky, které má fokus. Filtry, seskupení a fulltext se ukládají do `localStorage`
(čtení i zápis v try/catch).

**Chyby:** když API neodpovídá, zobrazí se pruh „server neodpovídá“ a poslední data zůstanou
zobrazená. Neúspěšné uložení poznámky nebo otevření ukáže toast s chybou.

## 10. Ošetření chyb (souhrn)

| Situace | Chování |
|---|---|
| Nevalidní JSON řádek | přeskočit |
| Nečitelný soubor (OSError) | řádek s `warnings=["unreadable"]`, ostatní běží |
| `cwd` neodpovídá kódování složky | `warnings=["cwd-mismatch"]`, viz 4.1 |
| `ps` selže | všechny sessions bez živého stavu, server běží dál |
| Poškozený `notes.json` | záloha + prázdné poznámky |
| osascript selže nebo vyprší timeout | `{"ok": false, "error": …}`, toast v UI |
| Adresář session už neexistuje (smazaný worktree) | `warnings=["cwd-missing"]`, ⚠ v řádku, „Otevřít“ vrátí 409 s hláškou. Když `stat` selže jinak než `FileNotFoundError` (např. TCC), varování se nenastaví. |
| Port obsazený | server skončí s jasnou chybou v logu (launchd ho bude restartovat; řeší se změnou portu) |

## 11. Testování

`python3 -m unittest discover tests`, jen stdlib.

Fixtures generuje `tests/helpers.py` do temp adresáře (JSONL i pid soubory). Testy pokrývají:

- **sessions.py:**
  - skutečné prompty vs. meta, příkazy a tool_result,
  - list content s textem,
  - `cwd` a validace kódování,
  - worktree → repo,
  - větve a `HEAD`,
  - Jira klíče jen z větví, URL a titulku (šum `P1-1` v textu se ignoruje),
  - primární klíč, `jira_hosts`,
  - podezřelý titulek (a) i (b),
  - téma,
  - fork přes `root_uuid`,
  - stub,
  - poškozený řádek,
  - cache (beze změny mtime se soubor nečte znovu).
- **live.py:** shoda a neshoda `procStart` (výstup `ps` se mockuje), nevalidní pid soubor.
- **notes.py:** atomický zápis, validace stavu a délky, poškozený soubor → záloha.
- **server.py:** API přes skutečný server na náhodném portu s fixtures:
  - `/api/sessions` tvar,
  - POST notes OK,
  - 403 při špatném `Origin` / `Host` / content-type,
  - 404 pro neznámé id.
  - `iterm.py` se mockuje.
- **iterm.py:** sestavení `argv` a quotování `cwd` s mezerou a apostrofem (osascript se mockuje).
- **filter.js:** `node tests/js/test_filter.js` (spouští se i z unittest, pokud je `node` dostupný).
  Pokrývá hledání vs. skryté stavy, seskupení a relativní čas. Statický test hlídá, že `app.js`
  nepoužívá `innerHTML` a podobná API, takže text z transcriptů nejde vložit jako HTML.

**Ověření na reálných datech:**

- Server spuštěný proti skutečnému `~/.claude` vrátí sessions z shop, shop_2 a shop_local.
- PROJ-563 je u `acme/shop` s větví `me/bugfix/PROJ-563-duplicate-orders`.
- Běžící sessions mají živý stav.
- `b0372375` je sbalená pod `cd6e5aad`.
- Otevření v iTerm2 se otestuje ručně na jedné neběžící session.

## 12. Otevřené body

Žádné.
