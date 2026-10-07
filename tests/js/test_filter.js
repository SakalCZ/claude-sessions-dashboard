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
  "stubs hidden without search unless enabled"() {
    assert.strictEqual(F.matches(row({ is_stub: true }), prefs()), false);
    assert.strictEqual(F.matches(row({ is_stub: true }), prefs({ showStubs: true })), true);
  },
  "search requires all terms, case insensitive, covers prompts, note, cwd"() {
    const r = row({ topic: "Contoso GA", search_text: "pusť testy", note: { status: "", note: "Čeká na JH" } });
    assert.strictEqual(F.matches(r, prefs({ q: "contoso TESTY" })), true);
    assert.strictEqual(F.matches(r, prefs({ q: "čeká" })), true);
    assert.strictEqual(F.matches(r, prefs({ q: "shop" })), true);
    assert.strictEqual(F.matches(r, prefs({ q: "contoso nic" })), false);
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
  "relTime"() {
    const now = Date.parse("2026-10-07T12:00:00Z");
    assert.strictEqual(F.relTime("2026-10-07T11:59:30Z", now), "právě teď");
    assert.strictEqual(F.relTime("2026-10-07T11:55:00Z", now), "před 5 min");
    assert.strictEqual(F.relTime("2026-10-07T09:00:00Z", now), "před 3 h");
    assert.strictEqual(F.relTime("2026-10-06T10:00:00Z", now), "včera");
    assert.strictEqual(F.relTime("2026-10-02T12:00:00Z", now), "před 5 dny");
    assert.strictEqual(F.relTime(null, now), "");
    assert.strictEqual(F.relTime("nonsense", now), "");
  },
  "dirCounts ignores stubs and sorts by name"() {
    const rows = [row({ display_dir: "budget" }), row({ display_dir: "acme/shop" }), row({ display_dir: "acme/shop" }), row({ display_dir: "x", is_stub: true })];
    assert.deepStrictEqual(F.dirCounts(rows), [{ dir: "acme/shop", count: 2 }, { dir: "budget", count: 1 }]);
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
