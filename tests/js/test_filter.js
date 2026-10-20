"use strict";
const assert = require("assert");
const F = require("../../static/filter.js");

const base = {
  session_id: "s", jira_key: null, jira_keys: [], topic: "", title: null, branches: [], display_dir: "acme/shop",
  worktree: null, search_text: "", note: null, cwd: "/w/acme/shop", is_stub: false, live: null,
  last_ts: "2026-10-01T10:00:00Z",
};
const row = (o) => Object.assign({}, base, o);
const prefs = (o) => Object.assign({ q: "", dirs: [], hide: ["done", "archived"], runningOnly: false, showStubs: false }, o);

const tests = {
  "search finds model and effort"() {
    const r = row({ model_label: "Fable 5.1", effort: "xhigh" });
    assert.strictEqual(F.matches(r, prefs({ q: "fable" })), true);
    assert.strictEqual(F.matches(r, prefs({ q: "xhigh" })), true);
    assert.strictEqual(F.matches(row(), prefs({ q: "fable" })), false);
  },
  "formatCountdown"() {
    const now = Date.parse("2026-10-14T13:40:00Z");
    assert.strictEqual(F.formatCountdown("2026-10-14T14:49:00Z", now), "in 1 h 9 min");
    assert.strictEqual(F.formatCountdown("2026-10-19T04:59:59Z", now), "in 4 d 15 h");
    assert.strictEqual(F.formatCountdown("2026-10-14T13:43:30Z", now), "in 3 min");
    assert.strictEqual(F.formatCountdown("2026-10-14T13:40:20Z", now), "in <1 min");
    assert.strictEqual(F.formatCountdown("2026-10-14T13:00:00Z", now), "in <1 min");
    assert.strictEqual(F.formatCountdown("nonsense", now), "");
  },
  "formatReset"() {
    const now = new Date(2026, 9, 14, 15, 0).getTime();
    assert.strictEqual(F.formatReset(new Date(2026, 9, 14, 18, 50).toISOString(), now), "18:50");
    assert.strictEqual(F.formatReset(new Date(2026, 9, 19, 6, 59).toISOString(), now), "Mon 06:59");
    assert.strictEqual(F.formatReset(new Date(2026, 9, 15, 0, 10).toISOString(), new Date(2026, 9, 14, 23, 30).getTime()), "Thu 00:10");
    assert.strictEqual(F.formatReset(new Date(2026, 9, 14, 18, 49, 59, 700).toISOString(), now), "18:50");
    assert.strictEqual(F.formatReset(new Date(2026, 9, 14, 18, 50, 29).toISOString(), now), "18:50");
    assert.strictEqual(F.formatReset("nonsense", now), "");
  },
  "formatMoney"() {
    assert.strictEqual(F.formatMoney(0, "EUR"), "€0");
    assert.strictEqual(F.formatMoney(10, "EUR"), "€10");
    assert.strictEqual(F.formatMoney(12.5, "EUR"), "€12.5");
    assert.strictEqual(F.formatMoney(3.5, null), "3.50");
    assert.strictEqual(F.formatMoney(3.5, "EURO"), "3.50 EURO");
    assert.strictEqual(F.formatMoney(null, "EUR"), "?");
  },
  "hides done and archived by default"() {
    assert.strictEqual(F.matches(row({ note: { status: "done" } }), prefs()), false);
    assert.strictEqual(F.matches(row({ note: { status: "archived" } }), prefs()), false);
    assert.strictEqual(F.matches(row({ note: { status: "waiting" } }), prefs()), true);
    assert.strictEqual(F.matches(row(), prefs()), true);
  },
  "hiding 'no status' uses empty string"() {
    assert.strictEqual(F.matches(row(), prefs({ hide: [""] })), false);
  },
  "search finds done session by jira number"() {
    assert.strictEqual(F.matches(row({ jira_keys: ["PROJ-563"], note: { status: "done" } }), prefs({ q: "563" })), true);
  },
  "search finds stub"() {
    assert.strictEqual(F.matches(row({ is_stub: true, branches: ["me/bugfix/PROJ-584-x"] }), prefs({ q: "proj-584" })), true);
  },
  "search finds keys, branches and ids of older copies"() {
    const r = row({ jira_keys: ["PROJ-538"], older_copies: [{ session_id: "b0372375-x", jira_keys: ["PROJ-466"], branches: ["me/feature/PROJ-466-cleanup"] }] });
    assert.strictEqual(F.matches(r, prefs({ q: "proj-466" })), true);
    assert.strictEqual(F.matches(r, prefs({ q: "b0372375" })), true);
    assert.strictEqual(F.matches(r, prefs({ q: "466-cleanup" })), true);
    assert.strictEqual(F.matches(row(), prefs({ q: "proj-466" })), false);
  },
  "stubs hidden without search unless enabled"() {
    assert.strictEqual(F.matches(row({ is_stub: true }), prefs()), false);
    assert.strictEqual(F.matches(row({ is_stub: true }), prefs({ showStubs: true })), true);
  },
  "search requires all terms, case insensitive, covers prompts, note, cwd"() {
    const r = row({ topic: "Contoso GA", search_text: "run the tests", note: { status: "", note: "Waiting for JH" } });
    assert.strictEqual(F.matches(r, prefs({ q: "contoso TESTS" })), true);
    assert.strictEqual(F.matches(r, prefs({ q: "waiting" })), true);
    assert.strictEqual(F.matches(r, prefs({ q: "shop" })), true);
    assert.strictEqual(F.matches(r, prefs({ q: "contoso nothing" })), false);
  },
  "dir filter and running-only apply even when searching"() {
    const r = row({ jira_keys: ["PROJ-1"] });
    assert.strictEqual(F.matches(r, prefs({ q: "proj-1", dirs: ["budget"] })), false);
    assert.strictEqual(F.matches(r, prefs({ q: "proj-1", dirs: ["acme/shop"] })), true);
    assert.strictEqual(F.matches(r, prefs({ q: "proj-1", runningOnly: true })), false);
    assert.strictEqual(F.matches(row({ live: { status: "busy" } }), prefs({ runningOnly: true })), true);
  },
  "group by issue puts no-issue last and sorts by recency"() {
    const rows = [
      row({ session_id: "a", jira_key: null, last_ts: "2026-10-07T00:00:00Z" }),
      row({ session_id: "b", jira_key: "PROJ-1", last_ts: "2026-10-01T00:00:00Z" }),
      row({ session_id: "c", jira_key: "PROJ-2", last_ts: "2026-10-05T00:00:00Z" }),
      row({ session_id: "d", jira_key: "PROJ-1", last_ts: "2026-10-06T00:00:00Z" }),
    ];
    const groups = F.groupRows(rows, "issue");
    assert.deepStrictEqual(groups.map((g) => g.key), ["PROJ-1", "PROJ-2", F.NO_ISSUE]);
    assert.deepStrictEqual(groups[0].rows.map((r) => r.session_id), ["d", "b"]);
  },
  "group by dir sorts groups by most recent row"() {
    const rows = [
      row({ session_id: "a", display_dir: "budget", last_ts: "2026-10-01T00:00:00Z" }),
      row({ session_id: "b", display_dir: "acme/shop", last_ts: "2026-10-03T00:00:00Z" }),
    ];
    assert.deepStrictEqual(F.groupRows(rows, "dir").map((g) => g.key), ["acme/shop", "budget"]);
  },
  "group none keeps one unlabeled group sorted by recency"() {
    const rows = [row({ session_id: "a", last_ts: "2026-10-01T00:00:00Z" }), row({ session_id: "b", last_ts: "2026-10-02T00:00:00Z" })];
    const groups = F.groupRows(rows, "none");
    assert.strictEqual(groups.length, 1);
    assert.strictEqual(groups[0].label, "");
    assert.deepStrictEqual(groups[0].rows.map((r) => r.session_id), ["b", "a"]);
  },
  "staleAge"() {
    const now = Date.parse("2026-10-07T12:00:00Z");
    assert.strictEqual(F.staleAge("2026-10-07T09:00:00Z", now, 30), "3 h ago");
    assert.strictEqual(F.staleAge("2026-10-07T11:30:00Z", now, 30), "");
    assert.strictEqual(F.staleAge("2026-10-07T11:29:00Z", now, 30), "31 min ago");
    assert.strictEqual(F.staleAge("nonsense", now, 30), "");
  },
  "relTime"() {
    const now = Date.parse("2026-10-07T12:00:00Z");
    assert.strictEqual(F.relTime("2026-10-07T11:59:30Z", now), "just now");
    assert.strictEqual(F.relTime("2026-10-07T11:55:00Z", now), "5 min ago");
    assert.strictEqual(F.relTime("2026-10-07T09:00:00Z", now), "3 h ago");
    assert.strictEqual(F.relTime("2026-10-06T10:00:00Z", now), "yesterday");
    assert.strictEqual(F.relTime("2026-10-02T12:00:00Z", now), "5 days ago");
    assert.strictEqual(F.relTime(null, now), "");
    assert.strictEqual(F.relTime("nonsense", now), "");
  },
  "formatMB"() {
    assert.strictEqual(F.formatMB(312), "312 MB");
    assert.strictEqual(F.formatMB(4403), "4.3 GB");
    assert.strictEqual(F.formatMB(null), "");
  },
  "minutesSince"() {
    const now = Date.parse("2026-10-07T12:00:00Z");
    assert.strictEqual(F.minutesSince("2026-10-07T11:48:00Z", now), 12);
    assert.strictEqual(F.minutesSince("2026-10-07T12:30:00Z", now), 0);
    assert.strictEqual(F.minutesSince("nonsense", now), null);
  },
  "splitQueue keeps server order and splits groups"() {
    const rows = [
      row({ session_id: "a", attention: { group: "done" } }),
      row({ session_id: "b", attention: { group: "blocking" } }),
      row({ session_id: "c", attention: { group: "done" } }),
    ];
    const q = F.splitQueue(rows, ["b", "c", "a", "missing"]);
    assert.deepStrictEqual(q.blocking.map((r) => r.session_id), ["b"]);
    assert.deepStrictEqual(q.done.map((r) => r.session_id), ["c", "a"]);
    assert.deepStrictEqual(F.splitQueue(rows, undefined), { blocking: [], done: [] });
  },
  "formatWait"() {
    assert.strictEqual(F.formatWait(12), "12 min");
    assert.strictEqual(F.formatWait(125), "2 h");
    assert.strictEqual(F.formatWait(1440), "1 day");
    assert.strictEqual(F.formatWait(53190), "36 days");
    assert.strictEqual(F.formatWait(null), "?");
  },
  "worstLevel"() {
    assert.strictEqual(F.worstLevel([]), "ok");
    assert.strictEqual(F.worstLevel(undefined), "ok");
    assert.strictEqual(F.worstLevel([{ level: "warn" }]), "warn");
    assert.strictEqual(F.worstLevel([{ level: "warn" }, { level: "critical" }]), "critical");
  },
  "dirCounts ignores stubs and sorts by name"() {
    const rows = [row({ display_dir: "budget" }), row({ display_dir: "acme/shop" }), row({ display_dir: "acme/shop" }), row({ display_dir: "x", is_stub: true })];
    assert.deepStrictEqual(F.dirCounts(rows), [{ dir: "acme/shop", count: 2 }, { dir: "budget", count: 1 }]);
  },
  "filterDirs keeps directories containing every term, case insensitive"() {
    const counts = [{ dir: "Acme/Shop_9", count: 4 }, { dir: "acme/shop_5", count: 3 }, { dir: "acme/shop_7", count: 1 }, { dir: "budget", count: 1 }];
    assert.deepStrictEqual(F.filterDirs(counts, "acme", []),
      [{ dir: "Acme/Shop_9", count: 4 }, { dir: "acme/shop_5", count: 3 }, { dir: "acme/shop_7", count: 1 }]);
    assert.deepStrictEqual(F.filterDirs(counts, "ACME 7", []), [{ dir: "acme/shop_7", count: 1 }]);
    assert.deepStrictEqual(F.filterDirs(counts, "shop_9", []), [{ dir: "Acme/Shop_9", count: 4 }]);
    assert.deepStrictEqual(F.filterDirs(counts, "nothing", []), []);
  },
  "filterDirs keeps selected directories that do not match"() {
    const counts = [{ dir: "acme/shop_5", count: 3 }, { dir: "budget", count: 1 }, { dir: "zeta", count: 2 }];
    assert.deepStrictEqual(F.filterDirs(counts, "acme", ["budget"]), [{ dir: "acme/shop_5", count: 3 }, { dir: "budget", count: 1 }]);
  },
  "filterDirs without a query keeps every directory"() {
    const counts = [{ dir: "acme/shop_5", count: 3 }, { dir: "budget", count: 1 }];
    assert.deepStrictEqual(F.filterDirs(counts, "", []), counts);
    assert.deepStrictEqual(F.filterDirs(counts, "   ", []), counts);
    assert.deepStrictEqual(F.filterDirs(counts, undefined, undefined), counts);
  },
};

let failed = 0;
for (const [name, fn] of Object.entries(tests)) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (e) {
    failed += 1;
    console.error(`FAIL - ${name}\n${e.stack}`);
  }
}
if (failed) {
  console.error(`${failed} test(s) failed`);
  process.exit(1);
}
console.log("all filter.js tests passed");
