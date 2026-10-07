/* Čisté funkce dashboardu – sdílené prohlížečem (window.DashFilter) a Node testy (module.exports). */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.DashFilter = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  const NO_ISSUE = "bez issue";

  function haystack(r) {
    return [
      r.session_id, (r.jira_keys || []).join(" "), r.topic, r.title || "", (r.branches || []).join(" "),
      r.display_dir, r.worktree || "", r.cwd || "", r.search_text || "", (r.note && r.note.note) || "",
      // Starší kopie (forky) – jejich větve a klíče musí jít najít, i když řádek ukazuje novější kopii.
      ...(r.older_copies || []).map((c) => [c.session_id, (c.jira_keys || []).join(" "), (c.branches || []).join(" ")].join(" ")),
    ].join("\n").toLowerCase();
  }

  function matches(r, f) {
    if (f.runningOnly && !r.live) return false;
    if (f.dirs && f.dirs.length && !f.dirs.includes(r.display_dir)) return false;
    const terms = (f.q || "").trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (terms.length) {
      // Hledání záměrně ignoruje skrývání podle stavu a stubů: „563“ musí najít i hotovou session.
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
    if (s < 60) return "právě teď";
    if (s < 3600) return `před ${Math.floor(s / 60)} min`;
    if (s < 86400) return `před ${Math.floor(s / 3600)} h`;
    const d = Math.floor(s / 86400);
    if (d === 1) return "včera";
    if (d < 30) return `před ${d} dny`;
    return new Date(t).toLocaleDateString("cs-CZ");
  }

  function dirCounts(rows) {
    const counts = new Map();
    for (const r of rows) {
      if (r.is_stub) continue;
      counts.set(r.display_dir, (counts.get(r.display_dir) || 0) + 1);
    }
    return Array.from(counts, ([dir, count]) => ({ dir, count })).sort((a, b) => a.dir.localeCompare(b.dir));
  }

  return { NO_ISSUE, matches, groupRows, relTime, dirCounts };
});
