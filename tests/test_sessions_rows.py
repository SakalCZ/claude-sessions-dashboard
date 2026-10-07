import shlex
import tempfile
import unittest
from pathlib import Path

import sessions
from tests.helpers import FakeClaude, custom_title, last_prompt, sid, user

ROOT = Path("/w")
SHOP = "/w/acme/shop"
SHOP2 = "/w/acme/shop_2"


class BuildRowsTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeClaude()

    def tearDown(self):
        self.fake.cleanup()

    def session(self, n, records, cwd=SHOP):
        return sessions.parse_session(self.fake.write_session(cwd, sid(n), records))

    def rows(self, items, is_missing=lambda c: False):
        rows, hosts = sessions.build_rows(items, dev_root=ROOT, is_missing=is_missing)
        return {r["session_id"]: r for r in rows}, rows, hosts

    def test_row_fields(self):
        prompts = [user(f"prompt {i} https://acme.atlassian.net/browse/PROJ-563" if i == 0 else f"prompt {i}",
                        ts=f"2026-10-01T10:00:0{i}Z", uuid=f"u{i}", cwd=SHOP, branch="me/bugfix/PROJ-563-dup") for i in range(7)]
        s = self.session(1, prompts + [custom_title("PROJ-563 Contoso: GA orders lower", sid(1))])
        by_id, _, hosts = self.rows([s])
        r = by_id[sid(1)]
        self.assertEqual(r["display_dir"], "acme/shop")
        self.assertEqual(r["repo"], SHOP)
        self.assertIsNone(r["worktree"])
        self.assertEqual(r["branch"], "me/bugfix/PROJ-563-dup")
        self.assertEqual(r["jira_key"], "PROJ-563")
        self.assertEqual(r["topic"], "Contoso: GA orders lower")
        self.assertFalse(r["title_suspect"])
        self.assertEqual(r["prompt_count"], 7)
        self.assertFalse(r["is_stub"])
        self.assertEqual(r["recent_prompts"], [f"prompt {i}" for i in range(2, 7)])
        self.assertTrue(r["first_prompt"].startswith("prompt 0"))
        self.assertIn("prompt 3", r["search_text"])
        self.assertEqual(shlex.split(r["resume_cmd"]), ["cd", SHOP, "&&", "claude", "--resume", sid(1)])
        self.assertEqual(r["older_copies"], [])
        self.assertEqual(r["warnings"], [])
        self.assertEqual(hosts, {"PROJ": "acme.atlassian.net"})

    def test_worktree_row(self):
        cwd = "/w/budget/.claude/worktrees/me-fix"
        r = self.rows([self.session(1, [user("x", ts="2026-01-01T00:00:00Z", uuid="u1", cwd=cwd)], cwd=cwd)])[0][sid(1)]
        self.assertEqual(r["display_dir"], "budget")
        self.assertEqual(r["worktree"], "me-fix")
        self.assertEqual(r["cwd"], cwd)

    def test_fork_is_collapsed_under_newest(self):
        older = self.session(1, [user("a", ts="2026-08-25T11:39:50Z", uuid="root", cwd=SHOP),
                                 user("b", ts="2026-08-26T13:23:00Z", uuid="u2", cwd=SHOP)])
        newer = self.session(2, [user("a", ts="2026-08-25T11:39:50Z", uuid="root", cwd=SHOP),
                                 user("c", ts="2026-09-15T11:29:00Z", uuid="u3", cwd=SHOP)])
        by_id, rows, _ = self.rows([older, newer])
        self.assertEqual(list(by_id), [sid(2)])
        self.assertEqual(by_id[sid(2)]["older_copies"], [{"session_id": sid(1), "last_ts": "2026-08-26T13:23:00Z", "prompt_count": 2}])

    def test_rows_sorted_by_last_activity(self):
        a = self.session(1, [user("a", ts="2026-01-01T00:00:00Z", uuid="u1", cwd=SHOP)])
        b = self.session(2, [user("b", ts="2026-02-01T00:00:00Z", uuid="u2", cwd=SHOP)])
        _, rows, _ = self.rows([a, b])
        self.assertEqual([r["session_id"] for r in rows], [sid(2), sid(1)])

    def test_title_with_foreign_key_is_suspect(self):
        s = self.session(1, [custom_title("PROJ-494 Route partner traffic", sid(1)),
                             user("začni na https://acme.atlassian.net/browse/PROJ-563", ts="2026-01-01T00:00:00Z", uuid="u1", cwd=SHOP)])
        r = self.rows([s])[0][sid(1)]
        self.assertTrue(r["title_suspect"])
        self.assertEqual(r["jira_key"], "PROJ-563")
        self.assertEqual(r["topic"], "začni na https://acme.atlassian.net/browse/PROJ-563")

    def test_same_title_different_keys_in_same_project_is_suspect(self):
        a = self.session(1, [custom_title("country variables refactoring", sid(1)),
                             user("a", ts="2026-01-01T00:00:00Z", uuid="u1", cwd=SHOP2, branch="me/bug/PROJ-505-x")], cwd=SHOP2)
        b = self.session(2, [custom_title("Country variables refactoring ", sid(2)),
                             user("b", ts="2026-01-02T00:00:00Z", uuid="u2", cwd=SHOP2, branch="me/feature/PROJ-557-y")], cwd=SHOP2)
        by_id = self.rows([a, b])[0]
        self.assertTrue(by_id[sid(1)]["title_suspect"])
        self.assertTrue(by_id[sid(2)]["title_suspect"])
        self.assertEqual(by_id[sid(1)]["topic"], "a")

    def test_same_title_same_key_or_other_project_is_not_suspect(self):
        a = self.session(1, [custom_title("PROJ-560 Prefer visible tree", sid(1)),
                             user("a", ts="2026-01-01T00:00:00Z", uuid="u1", cwd=SHOP, branch="me/bugfix/PROJ-560-x")])
        b = self.session(2, [custom_title("PROJ-560 Prefer visible tree", sid(2)),
                             user("b", ts="2026-01-02T00:00:00Z", uuid="u2", cwd=SHOP, branch="me/bugfix/PROJ-560-x")])
        c = self.session(3, [custom_title("PROJ-560 Prefer visible tree", sid(3)),
                             user("c", ts="2026-01-03T00:00:00Z", uuid="u3", cwd=SHOP2, branch="me/feature/PROJ-999-z")], cwd=SHOP2)
        by_id = self.rows([a, b, c])[0]
        self.assertFalse(by_id[sid(1)]["title_suspect"])
        self.assertFalse(by_id[sid(2)]["title_suspect"])
        self.assertTrue(by_id[sid(3)]["title_suspect"])  # (a): klíč z titulku není v jeho větvích

    def test_topic_fallbacks(self):
        url_title = self.session(1, [custom_title("https://acme.atlassian.net/browse/PROJ-369 Remove legacy column", sid(1)),
                                     user("x https://acme.atlassian.net/browse/PROJ-369", ts="2026-01-01T00:00:00Z", uuid="u1", cwd=SHOP)])
        no_title = self.session(2, [user("Koukám na\n\ntento účet", ts="2026-01-01T00:00:00Z", uuid="u2", cwd=SHOP)])
        only_last = self.session(3, [last_prompt("poslední věc", sid(3))])
        nothing = self.session(4, [custom_title("PROJ-1", sid(4))])
        by_id = self.rows([url_title, no_title, only_last, nothing])[0]
        self.assertEqual(by_id[sid(1)]["topic"], "Remove legacy column")
        self.assertEqual(by_id[sid(2)]["topic"], "Koukám na tento účet")
        self.assertEqual(by_id[sid(3)]["topic"], "poslední věc")
        self.assertEqual(by_id[sid(4)]["topic"], "(bez popisu)")
        self.assertTrue(by_id[sid(3)]["is_stub"])
        self.assertIsNone(by_id[sid(3)]["resume_cmd"])

    def test_cwd_missing_warning(self):
        s = self.session(1, [user("x", ts="2026-01-01T00:00:00Z", uuid="u1", cwd=SHOP)])
        r = self.rows([s], is_missing=lambda c: c == SHOP)[0][sid(1)]
        self.assertEqual(r["warnings"], ["cwd-missing"])

    def test_cwd_missing_real_check(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(sessions.cwd_missing(d))
            self.assertTrue(sessions.cwd_missing(d + "/neexistuje"))


if __name__ == "__main__":
    unittest.main()
