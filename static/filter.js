/* Pure dashboard functions – shared by the browser (window.DashFilter) and the Node tests (module.exports). */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.DashFilter = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  const NO_ISSUE = "no issue";

  function haystack(r) {
    return [
      r.session_id, (r.jira_keys || []).join(" "), r.topic, r.title || "", (r.branches || []).join(" "),
      r.display_dir, r.worktree || "", r.cwd || "", r.search_text || "", (r.note && r.note.note) || "",
      // Older copies (forks) – their branches and keys must be searchable even though the row shows the newer copy.
      ...(r.older_copies || []).map((c) => [c.session_id, (c.jira_keys || []).join(" "), (c.branches || []).join(" ")].join(" ")),
    ].join("\n").toLowerCase();
  }

  function matches(r, f) {
    if (f.runningOnly && !r.live) return false;
    if (f.dirs && f.dirs.length && !f.dirs.includes(r.display_dir)) return false;
    const terms = (f.q || "").trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (terms.length) {
      // Search deliberately ignores status and stub hiding: "563" must find a session marked done, too.
      const hay = haystack(r);
      return terms.every((t) => hay.includes(t));
    }
    if (!f.showStubs && r.is_stub) return false;
    const status = (r.note && r.note.status) || "";
    return !(f.hide || []).includes(status);
  }

  const byLastDesc = (a, b) => (b.last_ts || "").localeCompare(a.last_ts || "");

  function groupRows(rows, mode) {
    if (mode === "none") return [{ key: "", label: "", rows: rows.slice().sort(byLastDesc) }];
    const map = new Map();
    for (const r of rows) {
      const key = mode === "issue" ? r.jira_key || NO_ISSUE : r.display_dir;
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(r);
    }
    const groups = Array.from(map, ([key, rs]) => ({ key, label: key, rows: rs.sort(byLastDesc) }));
    groups.sort((a, b) => {
      if (mode === "issue" && (a.key === NO_ISSUE) !== (b.key === NO_ISSUE)) return a.key === NO_ISSUE ? 1 : -1;
      return byLastDesc(a.rows[0], b.rows[0]);
    });
    return groups;
  }

  function relTime(ts, now) {
    if (!ts) return "";
    const t = Date.parse(ts);
    if (Number.isNaN(t)) return "";
    const s = Math.max(0, ((now === undefined ? Date.now() : now) - t) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    const d = Math.floor(s / 86400);
    if (d === 1) return "yesterday";
    if (d < 30) return `${d} days ago`;
    return new Date(t).toLocaleDateString("en-GB");
  }

  function dirCounts(rows) {
    const counts = new Map();
    for (const r of rows) {
      if (r.is_stub) continue;
      counts.set(r.display_dir, (counts.get(r.display_dir) || 0) + 1);
    }
    return Array.from(counts, ([dir, count]) => ({ dir, count })).sort((a, b) => a.dir.localeCompare(b.dir));
  }

  function formatMB(mb) {
    if (mb === null || mb === undefined) return "";
    return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
  }

  function minutesSince(ts, now) {
    const t = Date.parse(ts);
    if (Number.isNaN(t)) return null;
    return Math.max(0, Math.floor(((now === undefined ? Date.now() : now) - t) / 60000));
  }

  function formatWait(mins) {
    if (mins === null || mins === undefined) return "?";
    if (mins < 60) return `${mins} min`;
    if (mins < 1440) return `${Math.floor(mins / 60)} h`;
    const days = Math.floor(mins / 1440);
    return days === 1 ? "1 day" : `${days} days`;
  }

  function splitQueue(rows, queue) {
    const byId = new Map(rows.map((r) => [r.session_id, r]));
    const out = { blocking: [], done: [] };
    for (const id of queue || []) {
      const r = byId.get(id);
      if (r) (r.attention && r.attention.group === "blocking" ? out.blocking : out.done).push(r);
    }
    return out;
  }

  function worstLevel(alerts) {
    const list = alerts || [];
    if (list.some((a) => a.level === "critical")) return "critical";
    return list.length ? "warn" : "ok";
  }

  return { NO_ISSUE, matches, groupRows, relTime, dirCounts, formatMB, minutesSince, formatWait, splitQueue, worstLevel };
});
