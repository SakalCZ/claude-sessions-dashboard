/* Claude Sessions Dashboard – rendering and actions. DOM API only, never HTML strings: transcript text is untrusted. */
(function () {
  "use strict";
  const F = window.DashFilter;
  const STATUSES = [["", "no status"], ["active", "active"], ["waiting", "waiting"], ["done", "done"], ["archived", "archived"]];
  const WARN_LABELS = {
    "cwd-missing": "directory no longer exists",
    "cwd-mismatch": "cwd does not match the project folder",
    "no-cwd": "unknown directory",
    unreadable: "file cannot be read",
  };
  const STATE_ICONS = { permission: "🔐", question: "❓", failed: "⚠", waiting: "✅" };
  const STATE_LABELS = { working: "working", waiting: "waiting", permission: "needs permission",
    question: "has a question", failed: "failed", idle: "idle", ended: "ended" };
  const DOT_CLASS = { working: "working", waiting: "waiting", permission: "permission", question: "permission",
    failed: "failed", idle: "idle" };
  let systemOpen = false;
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
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (e) { /* private window etc. */ }
  }

  function toggle(list, value) {
    const i = list.indexOf(value);
    if (i === -1) list.push(value); else list.splice(i, 1);
  }

  function plural(n) {
    return n === 1 ? "prompt" : "prompts";
  }

  function trunc(text, n) {
    return text.length > n ? text.slice(0, n - 1) + "…" : text;
  }

  function fmt(ts) {
    return ts ? new Date(ts).toLocaleString("en-GB") : "—";
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
      return { ok: false, error: "Server is not responding." };
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
    toast(`Copied: ${text}`);
  }

  async function openSession(r) {
    if (r.live && !confirm(`This session is running right now (${r.live.status}, pid ${r.live.pid}).\nOpen a second instance anyway?`)) return;
    const res = await post(`/api/open/${encodeURIComponent(r.session_id)}`, {});
    if (res.ok) toast("Opened in iTerm2.");
    else toast(`Could not open: ${res.error}`, true);
  }

  async function saveNote(r, fields) {
    const res = await post(`/api/notes/${encodeURIComponent(r.session_id)}`, fields);
    if (!res.ok) {
      toast(`Save failed: ${res.error}`, true);
      return;
    }
    const saved = res.note && (res.note.status || res.note.note) ? res.note : null;
    // A refresh may have replaced data.rows with new objects while typing → update the current row by id, too.
    const current = data.rows.find((x) => x.session_id === r.session_id);
    for (const target of current && current !== r ? [r, current] : [r]) target.note = saved;
    toast("Saved.");
    render();
  }

  async function focusSession(r) {
    const res = await post(`/api/focus/${encodeURIComponent(r.session_id)}`, {});
    if (res.ok) toast("Switched to the session's iTerm2 tab.");
    else toast(`Could not switch: ${res.error} Use Copy instead.`, true);
  }

  async function markSeen(r) {
    const res = await post(`/api/seen/${encodeURIComponent(r.session_id)}`, {});
    if (!res.ok) {
      toast(`Could not mark as seen: ${res.error}`, true);
      return;
    }
    const current = data.rows.find((x) => x.session_id === r.session_id);
    for (const target of current && current !== r ? [r, current] : [r]) {
      target.attention = Object.assign({}, target.attention, { in_queue: false, group: null });
    }
    data.queue = (data.queue || []).filter((id) => id !== r.session_id);
    render();
  }

  function renderSystemDetails(s) {
    const apps = el("table", {}, el("tr", {}, el("th", {}, "App"), el("th", {}, "RAM"), el("th", {}, "CPU")),
      (s.top_apps || []).map((a) => el("tr", {}, el("td", {}, a.name), el("td", {}, F.formatMB(a.rss_mb)), el("td", {}, `${a.cpu}%`))));
    const projects = el("table", {},
      el("tr", {}, el("th", {}, "Docker project"), el("th", {}, "RAM"), el("th", {}, "CPU"), el("th", {}, "Containers")),
      ((s.docker && s.docker.projects) || []).map((p) => el("tr", { title: p.working_dir || "" },
        el("td", {}, p.project), el("td", {}, F.formatMB(p.rss_mb)), el("td", {}, `${p.cpu}%`), el("td", {}, p.containers))));
    return el("div", { class: "system-details", onclick: (e) => e.stopPropagation() }, apps, projects);
  }

  function renderSystem() {
    const s = data.system;
    const box = $("#system");
    if (!s || !s.sampled_at) {
      box.className = "system";
      box.replaceChildren("System: sampling…");
      $("#alerts").replaceChildren();
      return;
    }
    const m = s.memory || {};
    const c = s.cpu || {};
    const swap = m.swap_total_mb ? ` · swap ${F.formatMB(m.swap_used_mb)}/${F.formatMB(m.swap_total_mb)}` : "";
    const parts = [
      `RAM ${m.pressure || "?"}${m.free_pct !== null && m.free_pct !== undefined ? ` · ${m.free_pct}% free` : ""}${swap}`,
      c.load1 !== undefined ? `CPU ${c.load1}/${c.ncpu}` : "CPU ?",
      s.top_apps ? `Top: ${s.top_apps.slice(0, 3).map((a) => `${a.name} ${F.formatMB(a.rss_mb)}`).join(", ")}` : "Processes unavailable",
      s.docker && s.docker.running
        ? `Docker: ${s.docker.projects.slice(0, 3).map((p) => `${p.project} ${F.formatMB(p.rss_mb)}`).join(", ") || "no containers"}`
        : "Docker: not running",
    ];
    box.className = `system ${F.worstLevel(s.alerts)}`;
    const summary = el("div", { onclick: () => { systemOpen = !systemOpen; render(); } }, parts.join("  │  "));
    box.replaceChildren(...[summary, systemOpen ? renderSystemDetails(s) : null].filter(Boolean));
    $("#alerts").replaceChildren(...(s.alerts || []).map((a) =>
      el("div", { class: `alert ${a.level}` }, `${a.level === "critical" ? "⛔" : "⚠"} ${a.message}`)));
  }

  function renderQueueItem(r) {
    const a = r.attention;
    const mins = F.minutesSince(a.since);
    return el("div", { class: `queue-item ${a.group}` },
      el("span", { class: "qicon" }, STATE_ICONS[a.state] || "•"),
      el("div", { class: "body" },
        el("div", { class: "main-line" }, r.jira_key ? jiraLink(r.jira_key, "key") : null, el("span", { class: "topic" }, r.topic)),
        el("div", { class: "sub" }, `${r.display_dir} · ${STATE_LABELS[a.state] || a.state} · waiting ${F.formatWait(mins)}${a.source === "estimate" ? " (estimate)" : ""}`),
        a.note ? el("div", { class: "prompts" }, a.note) : null),
      el("div", { class: "actions" },
        el("button", { type: "button", disabled: !r.tty, title: r.tty ? `Focus the iTerm2 tab on ${r.tty}` : "Terminal unknown", onclick: () => focusSession(r) }, "⇥ Switch"),
        el("button", { type: "button", onclick: () => markSeen(r) }, "✓ Seen"),
        el("button", { type: "button", disabled: !r.resume_cmd, onclick: () => copyText(r.resume_cmd) }, "⧉ Copy")));
  }

  function renderQueue() {
    const q = F.splitQueue(data.rows, data.queue);
    const total = q.blocking.length + q.done.length;
    document.title = total ? `(${total}) Claude Sessions` : "Claude Sessions";
    const box = $("#queue");
    if (!loaded) { box.replaceChildren(); return; }
    if (!total) { box.replaceChildren(el("div", { class: "queue-empty" }, "Nobody is waiting for you")); return; }
    const parts = [el("h2", {}, `Waiting for you (${total})`)];
    if (q.blocking.length) parts.push(el("h3", {}, "Blocking"), ...q.blocking.map(renderQueueItem));
    if (q.done.length) parts.push(el("h3", {}, "Done, waiting"), ...q.done.map(renderQueueItem));
    box.replaceChildren(...parts);
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
    const select = el("select", { title: "My status" },
      STATUSES.map(([value, label]) => {
        const option = el("option", { value }, label);
        option.selected = value === status;
        return option;
      }));
    select.addEventListener("change", () => {
      select.blur();
      saveNote(r, { status: select.value || null });
    });
    const note = el("input", { class: "note", type: "text", placeholder: "note…", maxlength: 500, value: noteValue });
    note.addEventListener("keydown", (e) => {
      if (e.key === "Enter") note.blur();
      if (e.key === "Escape") { note.value = noteValue; note.blur(); }
    });
    note.addEventListener("blur", () => {
      const value = note.value.trim();
      if (value !== noteValue) saveNote(r, { note: value });
    });
    return el("div", { class: "actions" },
      el("button", { type: "button", title: r.resume_cmd || "Unknown directory", disabled: !r.resume_cmd, onclick: () => copyText(r.resume_cmd) }, "⧉ Copy"),
      r.live
        ? el("button", { type: "button", disabled: !r.tty, title: r.tty ? `Focus the iTerm2 tab on ${r.tty}` : "Terminal unknown", onclick: () => focusSession(r) }, "⇥ Switch")
        : el("button", { type: "button", title: "Open in a new iTerm2 tab", disabled: !r.resume_cmd, onclick: () => openSession(r) }, "▶ Open"),
      select, note);
  }

  function renderDetails(r) {
    const dt = (label) => el("dt", {}, label);
    const items = [
      dt("Session ID"), el("dd", {}, r.session_id),
      dt("Directory"), el("dd", {}, r.cwd || "—"),
      dt("Resume"), el("dd", {}, r.resume_cmd || "—"),
      dt("Branches"), el("dd", {}, r.branches.join(" → ") || "—"),
      dt("Jira"), el("dd", {}, r.jira_keys.length ? r.jira_keys.map((k, i) => [i ? ", " : null, jiraLink(k)]) : "—"),
    ];
    if (r.title) items.push(dt("Title"), el("dd", { class: r.title_suspect ? "suspect" : null }, r.title, r.title_suspect ? "  (possibly inherited)" : null));
    items.push(dt("Started"), el("dd", {}, fmt(r.first_ts)), dt("Last activity"), el("dd", {}, fmt(r.last_ts)));
    if (r.live) items.push(dt("Running"), el("dd", {}, `${r.live.status}, pid ${r.live.pid}`));
    if (r.warnings.length) items.push(dt("Warnings"), el("dd", { class: "warn" }, warnText(r)));
    if (r.older_copies.length) {
      items.push(dt("Older copies"), el("dd", {}, r.older_copies.map((c) => el("div", {},
        `${c.session_id}  (${fmt(c.last_ts)}, ${c.prompt_count} ${plural(c.prompt_count)})`,
        c.jira_keys.length ? [" · ", c.jira_keys.map((k, i) => [i ? ", " : null, jiraLink(k)])] : null,
        c.branches.length ? ` · ${c.branches.join(" → ")}` : null,
        c.resume_cmd ? [" ", el("button", { type: "button", title: c.resume_cmd, onclick: () => copyText(c.resume_cmd) }, "⧉ Copy")] : null))));
    }
    return el("div", { class: "details" },
      el("dl", {}, items),
      r.first_prompt ? [el("h3", {}, "First prompt"), el("p", { class: "prompt" }, r.first_prompt)] : null,
      r.recent_prompts.length ? [el("h3", {}, "Recent prompts (newest last)"), el("ol", {}, r.recent_prompts.map((p) => el("li", {}, p)))] : null);
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
      el("div", {
        class: `dot ${r.live ? (DOT_CLASS[r.attention && r.attention.state] || "idle") : ""}${r.live && r.attention && r.attention.source === "estimate" ? " estimate" : ""}`,
        title: r.live ? `${STATE_LABELS[r.attention && r.attention.state] || "running"}${r.attention && r.attention.source === "estimate" ? " (estimate)" : ""}, pid ${r.live.pid}` : "not running",
      }),
      el("div", { class: "body" },
        el("div", { class: "main-line" }, r.jira_key ? jiraLink(r.jira_key, "key") : null, el("span", { class: "topic", title: r.topic }, r.topic)),
        el("div", { class: "sub" }, sub, r.warnings.length ? el("span", { class: "warn", title: warnText(r) }, "  ⚠ " + warnText(r)) : null),
        lastPrompts ? el("div", { class: "prompts" }, lastPrompts) : null),
      el("div", { class: "dir", title: r.cwd || "" }, r.display_dir),
      el("div", { class: "when", title: fmt(r.last_ts) }, F.relTime(r.last_ts),
        r.resources ? el("div", { class: "res", title: r.resources.top.map((p) => `${p.name} ${F.formatMB(p.rss_mb)} ${p.cpu}%`).join("\n") },
          `${F.formatMB(r.resources.rss_mb)} · ${r.resources.cpu}%`,
          r.resources.stack ? el("span", { class: "badge" }, `stack ${r.resources.stack}`) : null) : null),
      renderActions(r));
    if (open) row.append(renderDetails(r));
    return row;
  }

  function renderChips() {
    // replaceChildren does not flatten arrays → always spread.
    $("#dirs").replaceChildren(el("span", { class: "lbl" }, "directories:"),
      ...F.dirCounts(data.rows).map(({ dir, count }) => el("span", {
        class: prefs.dirs.includes(dir) ? "chip on" : "chip",
        onclick: () => { toggle(prefs.dirs, dir); savePrefs(); render(); },
      }, dir, " ", el("span", { class: "n" }, count))));
    $("#statuses").replaceChildren(el("span", { class: "lbl" }, "show statuses:"),
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
    renderSystem();
    renderQueue();
    const visible = data.rows.filter((r) => F.matches(r, prefs));
    $("#count").textContent = `showing ${visible.length} of ${data.rows.length}`;
    const out = [];
    for (const group of F.groupRows(visible, prefs.group)) {
      out.push(el("section", {},
        group.label ? el("h2", {}, group.label, " ", el("span", { class: "n" }, group.rows.length)) : null,
        group.rows.map(renderRow)));
    }
    if (!visible.length) out.push(el("p", { class: "empty" }, loaded ? "Nothing matches the filters." : "Loading…"));
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
      banner.textContent = `Server is not responding (${e.message}). Showing the last loaded data.`;
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
