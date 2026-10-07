/* Claude Sessions Dashboard – vykreslení a akce. Pouze DOM API, žádné vkládání HTML řetězců: text z transcriptů je nedůvěryhodný. */
(function () {
  "use strict";
  const F = window.DashFilter;
  const STATUSES = [["", "bez stavu"], ["active", "aktivní"], ["waiting", "čeká"], ["done", "hotovo"], ["archived", "archiv"]];
  const WARN_LABELS = {
    "cwd-missing": "adresář už neexistuje",
    "cwd-mismatch": "cwd neodpovídá složce projektu",
    "no-cwd": "neznámý adresář",
    unreadable: "soubor nelze přečíst",
  };
  const PREFS_KEY = "claude-dashboard:prefs:v1";
  const REFRESH_MS = 10000;

  const prefs = loadPrefs();
  const expanded = new Set();
  let data = { rows: [], jira_hosts: {} };
  let loaded = false;
  let pendingRender = false;
  let inFlight = false;

  const $ = (sel) => document.querySelector(sel);

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "value") node.value = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : String(v));
    }
    for (const c of children.flat(2)) {
      if (c === null || c === undefined || c === false || c === "") continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  function loadPrefs() {
    const defaults = { q: "", dirs: [], hide: ["done", "archived"], runningOnly: false, showStubs: false, group: "dir" };
    try {
      const saved = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
      return Object.assign(defaults, saved && typeof saved === "object" ? saved : {});
    } catch (e) {
      return defaults;
    }
  }

  function savePrefs() {
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (e) { /* privátní okno apod. */ }
  }

  function toggle(list, value) {
    const i = list.indexOf(value);
    if (i === -1) list.push(value); else list.splice(i, 1);
  }

  function plural(n) {
    if (n === 1) return "prompt";
    if (n >= 2 && n <= 4) return "prompty";
    return "promptů";
  }

  function trunc(text, n) {
    return text.length > n ? text.slice(0, n - 1) + "…" : text;
  }

  function fmt(ts) {
    return ts ? new Date(ts).toLocaleString("cs-CZ") : "—";
  }

  function toast(message, isError) {
    const node = el("div", { class: isError ? "toast err" : "toast" }, message);
    $("#toasts").append(node);
    setTimeout(() => node.remove(), isError ? 8000 : 2500);
  }

  async function post(url, body) {
    try {
      const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) return { ok: false, error: json.error || `HTTP ${res.status}` };
      return json;
    } catch (e) {
      return { ok: false, error: "Server neodpovídá." };
    }
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      const ta = el("textarea", {}, text);
      document.body.append(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    toast(`Zkopírováno: ${text}`);
  }

  async function openSession(r) {
    if (r.live && !confirm(`Session právě běží (${r.live.status}, pid ${r.live.pid}).\nOpravdu otevřít druhou instanci?`)) return;
    const res = await post(`/api/open/${encodeURIComponent(r.session_id)}`, {});
    if (res.ok) toast("Otevřeno v iTerm2.");
    else toast(`Nepodařilo se otevřít: ${res.error}`, true);
  }

  async function saveNote(r, fields) {
    const res = await post(`/api/notes/${encodeURIComponent(r.session_id)}`, fields);
    if (!res.ok) {
      toast(`Uložení selhalo: ${res.error}`, true);
      return;
    }
    const saved = res.note && (res.note.status || res.note.note) ? res.note : null;
    // Během psaní mohl proběhnout refresh a nahradit data.rows novými objekty → aktualizuj i aktuální řádek podle id.
    const current = data.rows.find((x) => x.session_id === r.session_id);
    for (const target of current && current !== r ? [r, current] : [r]) target.note = saved;
    toast("Uloženo.");
    render();
  }

  function jiraLink(key, cls) {
    const host = data.jira_hosts[key.split("-")[0]];
    if (!host) return el("span", { class: cls || null }, key);
    return el("a", { class: cls || null, href: `https://${host}/browse/${key}`, target: "_blank", rel: "noopener" }, key);
  }

  function warnText(r) {
    return r.warnings.map((w) => WARN_LABELS[w] || w).join(", ");
  }

  function renderActions(r) {
    const status = (r.note && r.note.status) || "";
    const noteValue = (r.note && r.note.note) || "";
    const select = el("select", { title: "Můj stav" },
      STATUSES.map(([value, label]) => {
        const option = el("option", { value }, label);
        option.selected = value === status;
        return option;
      }));
    select.addEventListener("change", () => {
      select.blur();
      saveNote(r, { status: select.value || null });
    });
    const note = el("input", { class: "note", type: "text", placeholder: "poznámka…", maxlength: 500, value: noteValue });
    note.addEventListener("keydown", (e) => {
      if (e.key === "Enter") note.blur();
      if (e.key === "Escape") { note.value = noteValue; note.blur(); }
    });
    note.addEventListener("blur", () => {
      const value = note.value.trim();
      if (value !== noteValue) saveNote(r, { note: value });
    });
    return el("div", { class: "actions" },
      el("button", { type: "button", title: r.resume_cmd || "Neznámý adresář", disabled: !r.resume_cmd, onclick: () => copyText(r.resume_cmd) }, "⧉ Kopírovat"),
      el("button", { type: "button", title: "Otevřít v novém iTerm2 tabu", disabled: !r.resume_cmd, onclick: () => openSession(r) }, "▶ Otevřít"),
      select, note);
  }

  function renderDetails(r) {
    const dt = (label) => el("dt", {}, label);
    const items = [
      dt("Session ID"), el("dd", {}, r.session_id),
      dt("Adresář"), el("dd", {}, r.cwd || "—"),
      dt("Resume"), el("dd", {}, r.resume_cmd || "—"),
      dt("Větve"), el("dd", {}, r.branches.join(" → ") || "—"),
      dt("Jira"), el("dd", {}, r.jira_keys.length ? r.jira_keys.map((k, i) => [i ? ", " : null, jiraLink(k)]) : "—"),
    ];
    if (r.title) items.push(dt("Titulek"), el("dd", { class: r.title_suspect ? "suspect" : null }, r.title, r.title_suspect ? "  (možná zděděný)" : null));
    items.push(dt("Začátek"), el("dd", {}, fmt(r.first_ts)), dt("Poslední aktivita"), el("dd", {}, fmt(r.last_ts)));
    if (r.live) items.push(dt("Běží"), el("dd", {}, `${r.live.status}, pid ${r.live.pid}`));
    if (r.warnings.length) items.push(dt("Varování"), el("dd", { class: "warn" }, warnText(r)));
    if (r.older_copies.length) {
      items.push(dt("Starší kopie"), el("dd", {}, r.older_copies.map((c) => `${c.session_id}  (${fmt(c.last_ts)}, ${c.prompt_count} ${plural(c.prompt_count)})`).join("\n")));
    }
    return el("div", { class: "details" },
      el("dl", {}, items),
      r.first_prompt ? [el("h3", {}, "První prompt"), el("p", { class: "prompt" }, r.first_prompt)] : null,
      r.recent_prompts.length ? [el("h3", {}, "Poslední prompty (nejnovější dole)"), el("ol", {}, r.recent_prompts.map((p) => el("li", {}, p)))] : null);
  }

  function renderRow(r) {
    const open = expanded.has(r.session_id);
    const row = el("div", { class: `row${r.is_stub ? " stub" : ""}${open ? " open" : ""}` });
    row.addEventListener("click", (e) => {
      if (e.target.closest(".actions, a, .details")) return;
      if (open) expanded.delete(r.session_id); else expanded.add(r.session_id);
      render();
    });
    const sub = [r.branch || "—", r.worktree ? `worktree ${r.worktree}` : null, `${r.prompt_count} ${plural(r.prompt_count)}`]
      .filter(Boolean).join(" · ");
    const lastPrompts = r.recent_prompts.slice(-2).reverse().map((p) => `» ${trunc(p, 120)}`).join("    ");
    row.append(
      el("div", { class: `dot ${r.live ? r.live.status : ""}`, title: r.live ? `běží (${r.live.status}), pid ${r.live.pid}` : "neběží" }),
      el("div", { class: "body" },
        el("div", { class: "main-line" }, r.jira_key ? jiraLink(r.jira_key, "key") : null, el("span", { class: "topic", title: r.topic }, r.topic)),
        el("div", { class: "sub" }, sub, r.warnings.length ? el("span", { class: "warn", title: warnText(r) }, "  ⚠ " + warnText(r)) : null),
        lastPrompts ? el("div", { class: "prompts" }, lastPrompts) : null),
      el("div", { class: "dir", title: r.cwd || "" }, r.display_dir),
      el("div", { class: "when", title: fmt(r.last_ts) }, F.relTime(r.last_ts)),
      renderActions(r));
    if (open) row.append(renderDetails(r));
    return row;
  }

  function renderChips() {
    // replaceChildren nerozbaluje pole → vždy spread.
    $("#dirs").replaceChildren(el("span", { class: "lbl" }, "adresáře:"),
      ...F.dirCounts(data.rows).map(({ dir, count }) => el("span", {
        class: prefs.dirs.includes(dir) ? "chip on" : "chip",
        onclick: () => { toggle(prefs.dirs, dir); savePrefs(); render(); },
      }, dir, " ", el("span", { class: "n" }, count))));
    $("#statuses").replaceChildren(el("span", { class: "lbl" }, "zobrazit stavy:"),
      ...STATUSES.map(([value, label]) => el("span", {
        class: prefs.hide.includes(value) ? "chip" : "chip on",
        onclick: () => { toggle(prefs.hide, value); savePrefs(); render(); },
      }, label)));
  }

  function isEditing() {
    const a = document.activeElement;
    return !!(a && a.closest && a.closest("#list") && a.matches("input, select"));
  }

  function render() {
    if (isEditing()) { pendingRender = true; return; }
    pendingRender = false;
    renderChips();
    const visible = data.rows.filter((r) => F.matches(r, prefs));
    $("#count").textContent = `zobrazeno ${visible.length} z ${data.rows.length}`;
    const out = [];
    for (const group of F.groupRows(visible, prefs.group)) {
      out.push(el("section", {},
        group.label ? el("h2", {}, group.label, " ", el("span", { class: "n" }, group.rows.length)) : null,
        group.rows.map(renderRow)));
    }
    if (!visible.length) out.push(el("p", { class: "empty" }, loaded ? "Nic neodpovídá filtrům." : "Načítám…"));
    $("#list").replaceChildren(...out);
  }

  async function refresh() {
    if (inFlight || document.hidden) return;
    inFlight = true;
    try {
      const res = await fetch("/api/sessions", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
      loaded = true;
      const known = new Set(data.rows.map((r) => r.display_dir));
      const kept = prefs.dirs.filter((d) => known.has(d));
      if (kept.length !== prefs.dirs.length) { prefs.dirs = kept; savePrefs(); }
      $("#banner").hidden = true;
      render();
    } catch (e) {
      const banner = $("#banner");
      banner.textContent = `Server neodpovídá (${e.message}). Zobrazuji poslední načtená data.`;
      banner.hidden = false;
    } finally {
      inFlight = false;
    }
  }

  function init() {
    const q = $("#q");
    q.value = prefs.q;
    q.addEventListener("input", () => { prefs.q = q.value; savePrefs(); render(); });
    const running = $("#running");
    running.checked = prefs.runningOnly;
    running.addEventListener("change", () => { prefs.runningOnly = running.checked; savePrefs(); render(); });
    const stubs = $("#stubs");
    stubs.checked = prefs.showStubs;
    stubs.addEventListener("change", () => { prefs.showStubs = stubs.checked; savePrefs(); render(); });
    const group = $("#group");
    group.value = prefs.group;
    group.addEventListener("change", () => { prefs.group = group.value; savePrefs(); render(); });
    document.addEventListener("keydown", (e) => {
      const typing = e.target.matches && e.target.matches("input, select, textarea");
      if (e.key === "/" && !typing) { e.preventDefault(); q.focus(); q.select(); }
    });
    document.addEventListener("focusout", () => setTimeout(() => { if (pendingRender) render(); }, 0));
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
    refresh();
    setInterval(refresh, REFRESH_MS);
  }

  init();
})();
