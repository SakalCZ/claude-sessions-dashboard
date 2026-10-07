import json
import os
import unittest
from pathlib import Path

import sessions
from tests.helpers import FakeClaude, agent_name, assistant, custom_title, last_prompt, sid, tool_result, user

CWD = "/w/acme/shop"


class ParseSessionTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeClaude()

    def tearDown(self):
        self.fake.cleanup()

    def parse(self, records, cwd=CWD, project_dir=None, n=1):
        return sessions.parse_session(self.fake.write_session(cwd, sid(n), records, project_dir))

    def test_full_session_fields(self):
        s = self.parse([
            agent_name("Agent title", sid(1)),
            user("Analyze https://acme.atlassian.net/browse/PROJ-369?x=1", ts="2026-09-18T10:00:00.000Z", uuid="u1", cwd=CWD),
            assistant(ts="2026-09-18T10:00:05.000Z", uuid="a1", cwd=CWD, branch="me/feature/PROJ-604-drop"),
            tool_result(ts="2026-09-18T10:00:06.000Z", uuid="t1", cwd=CWD, branch="me/feature/PROJ-604-drop"),
            user("done, switch back", ts="2026-09-18T09:59:00.000Z", uuid="u2", cwd=CWD, branch="master"),
            user("meta", ts="2026-09-18T10:00:07.000Z", uuid="m1", cwd=CWD, isMeta=True),
            custom_title("PROJ-369 Remove legacy column", sid(1)),
            last_prompt("done, switch back", sid(1)),
        ])
        self.assertEqual(s.session_id, sid(1))
        self.assertEqual(s.cwd, CWD)
        self.assertEqual(s.project_dir, sessions.encode_cwd(CWD))
        self.assertEqual(s.title, "PROJ-369 Remove legacy column")
        self.assertEqual(s.last_prompt, "done, switch back")
        self.assertEqual(s.prompts, ["Analyze https://acme.atlassian.net/browse/PROJ-369?x=1", "done, switch back"])
        self.assertEqual(s.first_ts, "2026-09-18T09:59:00.000Z")
        self.assertEqual(s.last_ts, "2026-09-18T10:00:07.000Z")
        self.assertEqual(s.root_uuid, "u1")
        self.assertEqual(s.branches, ["me/feature/PROJ-604-drop", "master"])
        self.assertEqual(s.branch, "master")
        self.assertEqual(s.jira_key, "PROJ-604")
        self.assertEqual(s.jira_keys, ["PROJ-604", "PROJ-369"])
        self.assertEqual(s.hosts, {"PROJ": "acme.atlassian.net"})
        self.assertEqual(s.warnings, [])

    def test_agent_name_is_title_fallback(self):
        s = self.parse([agent_name("DBO", sid(1)), user("x", ts="2026-01-01T00:00:00Z", uuid="u1", cwd=CWD)])
        self.assertEqual(s.title, "DBO")

    def test_branch_order_follows_last_occurrence(self):
        s = self.parse([
            user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD, branch="A"),
            user("b", ts="2026-01-01T00:00:02Z", uuid="u2", cwd=CWD, branch="B"),
            user("c", ts="2026-01-01T00:00:03Z", uuid="u3", cwd=CWD, branch="A"),
        ])
        self.assertEqual(s.branches, ["B", "A"])
        self.assertEqual(s.branch, "A")

    def test_head_branch(self):
        only_head = self.parse([user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD, branch="HEAD")], n=1)
        self.assertEqual(only_head.branch, "HEAD")
        detached_then_real = self.parse([
            user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD, branch="x"),
            user("b", ts="2026-01-01T00:00:02Z", uuid="u2", cwd=CWD, branch="HEAD"),
        ], n=2)
        self.assertEqual(detached_then_real.branch, "x")

    def test_jira_key_fallbacks(self):
        from_url = self.parse([
            user("see https://acme.atlassian.net/browse/PROJ-1 and https://example.atlassian.net/browse/OPS-30", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD),
        ], n=1)
        self.assertEqual(from_url.jira_key, "OPS-30")
        self.assertEqual(from_url.hosts, {"PROJ": "acme.atlassian.net", "OPS": "example.atlassian.net"})
        from_title = self.parse([custom_title("PROJ 548 Optimize", sid(2)), user("x", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD)], n=2)
        self.assertEqual(from_title.jira_key, "PROJ-548")
        none = self.parse([user("x", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD)], n=3)
        self.assertIsNone(none.jira_key)
        self.assertEqual(none.jira_keys, [])

    def test_free_text_keys_are_not_jira(self):
        s = self.parse([user("see P1-1, PSR-4 and ARCH-1874", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD)])
        self.assertEqual(s.jira_keys, [])

    def test_url_inside_pasted_content_counts(self):
        s = self.parse([user('start\n<pasted_content id="b">\nhttps://acme.atlassian.net/browse/PROJ-563\n</pasted_content>',
                             ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD)])
        self.assertEqual(s.jira_key, "PROJ-563")
        self.assertEqual(s.prompts, ["start\n[pasted: https://acme.atlassian.net/browse/PROJ-563]"])

    def test_cwd_matching_project_dir_wins(self):
        s = self.parse([
            user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd="/w/other"),
            user("b", ts="2026-01-01T00:00:02Z", uuid="u2", cwd=CWD),
        ], project_dir=sessions.encode_cwd(CWD))
        self.assertEqual(s.cwd, CWD)
        self.assertEqual(s.warnings, [])

    def test_cwd_mismatch_warning(self):
        s = self.parse([user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd="/w/other")], project_dir="-w-somewhere")
        self.assertEqual(s.cwd, "/w/other")
        self.assertEqual(s.warnings, ["cwd-mismatch"])

    def test_stub_without_records(self):
        s = self.parse([custom_title("x", sid(1))])
        self.assertIsNone(s.cwd)
        self.assertEqual(s.warnings, ["no-cwd"])
        self.assertEqual(s.prompts, [])
        self.assertIsNotNone(s.last_ts)  # fallback z mtime
        self.assertTrue(s.last_ts.endswith("Z"))

    def test_truncated_last_line_is_skipped(self):
        s = self.parse([
            "not json at all",
            user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD),
            '{"type": "user", "message": {"content": "useknut',
        ])
        self.assertEqual(s.prompts, ["a"])

    def test_unreadable_file(self):
        s = sessions.parse_session(Path(self.fake.root, "projects", "x", f"{sid(9)}.jsonl"))
        self.assertEqual(s.warnings, ["unreadable"])
        self.assertEqual(s.session_id, sid(9))


class SessionCacheTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeClaude()
        self.cache = sessions.SessionCache(self.fake.root)

    def tearDown(self):
        self.fake.cleanup()

    def test_cache_skips_unchanged_files(self):
        self.fake.write_session(CWD, sid(1), [user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD)])
        self.assertEqual(len(self.cache.load()), 1)
        self.assertEqual(len(self.cache.load()), 1)
        self.assertEqual(self.cache.parse_count, 1)

    def test_cache_reparses_changed_file(self):
        path = self.fake.write_session(CWD, sid(1), [user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD)])
        self.cache.load()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write('{"type": "user", "message": {"content": "b"}, "timestamp": "2026-01-01T00:00:02Z", "uuid": "u2", "cwd": "%s"}\n' % CWD)
        [s] = self.cache.load()
        self.assertEqual(s.prompts, ["a", "b"])
        self.assertEqual(self.cache.parse_count, 2)

    def test_cache_parses_only_appended_lines(self):
        path = self.fake.write_session(CWD, sid(1), [user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD),
                                                     user("b", ts="2026-01-01T00:00:02Z", uuid="u2", cwd=CWD)])
        self.cache.load()
        self.assertEqual(self.cache.lines_parsed, 2)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(user("c", ts="2026-01-01T00:00:03Z", uuid="u3", cwd=CWD, branch="me/feature/PROJ-7-x")) + "\n")
        [s] = self.cache.load()
        self.assertEqual(s.prompts, ["a", "b", "c"])
        self.assertEqual(s.root_uuid, "u1")
        self.assertEqual(s.last_ts, "2026-01-01T00:00:03Z")
        self.assertEqual(s.jira_key, "PROJ-7")
        self.assertEqual(self.cache.lines_parsed, 3)

    def test_cache_waits_for_partial_line_to_complete(self):
        path = self.fake.write_session(CWD, sid(1), [user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD)])
        line = json.dumps(user("b", ts="2026-01-01T00:00:02Z", uuid="u2", cwd=CWD))
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line[:20])
        [s] = self.cache.load()
        self.assertEqual(s.prompts, ["a"])
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line[20:] + "\n")
        [s] = self.cache.load()
        self.assertEqual(s.prompts, ["a", "b"])

    def test_cache_reparses_rewritten_shorter_file(self):
        path = self.fake.write_session(CWD, sid(1), [user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD),
                                                     user("b", ts="2026-01-01T00:00:02Z", uuid="u2", cwd=CWD)])
        self.cache.load()
        path.write_text(json.dumps(user("z", ts="2026-01-02T00:00:00Z", uuid="z1", cwd=CWD)) + "\n", encoding="utf-8")
        [s] = self.cache.load()
        self.assertEqual(s.prompts, ["z"])
        self.assertEqual(s.root_uuid, "z1")

    def test_cache_drops_deleted_files_and_ignores_subdirs(self):
        path = self.fake.write_session(CWD, sid(1), [user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD)])
        (path.parent / sid(1)).mkdir()  # subfolder with subagents
        (path.parent / sid(1) / "agent.jsonl").write_text("{}\n")
        self.assertEqual([s.session_id for s in self.cache.load()], [sid(1)])
        os.remove(path)
        self.assertEqual(self.cache.load(), [])

    def test_missing_projects_dir(self):
        self.assertEqual(sessions.SessionCache(Path(self.fake.root, "nope")).load(), [])


if __name__ == "__main__":
    unittest.main()
