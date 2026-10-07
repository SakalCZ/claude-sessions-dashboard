# Claude Sessions Dashboard

Lokální přehled všech Claude Code sessions z `~/.claude`: adresář, Jira issue / téma, větev, poslední prompty,
živý stav (🟢 busy / 🟡 idle), vlastní stav a poznámka. Session jde obnovit přes kopírování příkazu
`cd … && claude --resume …` nebo přímo v novém iTerm2 tabu.

**Adresa:** http://127.0.0.1:7333/ (jen localhost)

## Instalace / aktualizace

```bash
./launchd/install.sh
```

- Zkopíruje aplikaci do `~/Library/Application Support/claude-dashboard/app/` a zaregistruje LaunchAgent
  `local.claude-sessions-dashboard`. Ten startuje při přihlášení a po pádu se restartuje.
- Po změně kódu spusť `install.sh` znovu.
- Jiný port: `CLAUDE_DASHBOARD_PORT=7400 ./launchd/install.sh`.

## Odinstalace

```bash
./launchd/uninstall.sh
```

Poznámky zůstanou v `~/Library/Application Support/claude-dashboard/notes.json`.

## Vývoj

```bash
python3 server.py --port 7334 --data-dir "$TMPDIR/cd-dev"   # běží přímo z repa
python3 -m unittest discover -s tests -t . -v               # všechny testy (vč. node testu filter.js)
```

- Log LaunchAgentu: `~/Library/Logs/claude-dashboard.log`.
- „Otevřít v iTerm2“: při prvním použití se macOS zeptá na povolení ovládání iTerm2. Když ho odmítneš,
  povol ho v Nastavení systému → Soukromí a zabezpečení → Automatizace.
- Claude Code staré transcripty maže (nastavení `cleanupPeriodDays`). Takové sessions z dashboardu zmizí
  a nejdou obnovit.

Design: `docs/superpowers/specs/2026-10-07-claude-sessions-dashboard-design.md`
