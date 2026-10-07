import shlex
import unittest
from pathlib import Path

import sessions


def rec_user(content, **extra):
    return {"type": "user", "message": {"role": "user", "content": content}, **extra}


class EncodeCwdTest(unittest.TestCase):
    def test_matches_claude_project_dir_naming(self):
        self.assertEqual(
            sessions.encode_cwd("/Users/me/Documents/Development/acme/shop_2"),
            "-Users-me-Documents-Development-acme-shop-2",
        )
        self.assertEqual(
            sessions.encode_cwd("/Users/me/Documents/Development/budget/.claude/worktrees/me-fix"),
            "-Users-me-Documents-Development-budget--claude-worktrees-me-fix",
        )


class PromptTextTest(unittest.TestCase):
    def test_plain_string_prompt(self):
        self.assertEqual(sessions.prompt_text(rec_user("  Analyze PROJ-563 \n")), "Analyze PROJ-563")

    def test_non_user_records_are_ignored(self):
        self.assertIsNone(sessions.prompt_text({"type": "assistant", "message": {"content": "x"}}))

    def test_flagged_records_are_ignored(self):
        for flag in ("isMeta", "isSidechain", "isCompactSummary", "isVisibleInTranscriptOnly"):
            with self.subTest(flag=flag):
                self.assertIsNone(sessions.prompt_text(rec_user("ahoj", **{flag: True})))

    def test_tool_results_are_ignored(self):
        content = [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]
        self.assertIsNone(sessions.prompt_text(rec_user(content)))

    def test_list_with_text_and_image_is_a_prompt(self):
        content = [{"type": "image", "source": {}}, {"type": "text", "text": "[Image #3] Still not working"}]
        self.assertEqual(sessions.prompt_text(rec_user(content)), "[Image #3] Still not working")

    def test_system_tags_are_ignored(self):
        for text in (
            "<command-name>/clear</command-name>",
            "<local-command-caveat>Caveat: The messages below…</local-command-caveat>",
            "<local-command-stdout>Set model</local-command-stdout>",
            "<task-notification>\n<task-id>x</task-id>",
            "<artifact-content-authored-by-claude>…",
            "<system-reminder>x</system-reminder>",
            "Caveat: something",
            "[Request interrupted by user]",
        ):
            with self.subTest(text=text):
                self.assertIsNone(sessions.prompt_text(rec_user(text)))

    def test_interrupted_text_block_is_ignored(self):
        self.assertIsNone(sessions.prompt_text(rec_user([{"type": "text", "text": "[Request interrupted by user for tool use]"}])))

    def test_real_prompts_starting_with_bracket_or_paste_are_kept(self):
        for text in ('<pasted_content id="b032">\nhttps://x</pasted_content>', "[me@db1 ~]$ mysql --defaults"):
            with self.subTest(text=text):
                self.assertEqual(sessions.prompt_text(rec_user(text)), text)

    def test_empty_prompt_is_ignored(self):
        self.assertIsNone(sessions.prompt_text(rec_user("   ")))
        self.assertIsNone(sessions.prompt_text(rec_user([])))


class CleanPromptTest(unittest.TestCase):
    def test_pasted_content_is_replaced_by_snippet(self):
        text = 'start on\n\n<pasted_content id="b0">\nhttps://acme.atlassian.net/browse/PROJ-563\n</pasted_content>'
        self.assertEqual(sessions.clean_prompt(text), "start on\n\n[pasted: https://acme.atlassian.net/browse/PROJ-563]")

    def test_closing_tag_with_id_attribute(self):
        # Real transcript format: the closing tag carries an id too.
        text = 'start on\n<pasted_content id="b032">\nhttps://x/PROJ-563\n</pasted_content id="b032">\na <pasted_content id="c1">B</pasted_content id="c1"> end'
        self.assertEqual(sessions.clean_prompt(text), "start on\n[pasted: https://x/PROJ-563]\na [pasted: B] end")

    def test_long_paste_is_truncated(self):
        text = "<pasted_content id='x'>" + "a" * 100 + "</pasted_content>"
        self.assertEqual(sessions.clean_prompt(text), "[pasted: " + "a" * 60 + "…]")

    def test_empty_paste(self):
        self.assertEqual(sessions.clean_prompt("x <pasted_content id='x'> </pasted_content>"), "x [pasted]")


class OneLineTest(unittest.TestCase):
    def test_collapses_whitespace_and_truncates(self):
        self.assertEqual(sessions.one_line("a\n\n  b\tc", 100), "a b c")
        self.assertEqual(sessions.one_line("abcdefghij", 5), "abcd…")


class TitleKeysTest(unittest.TestCase):
    def test_keys_with_dash_and_leading_space_variant(self):
        self.assertEqual(sessions.title_keys("PROJ 548 Optimize DB queries"), ["PROJ-548"])
        self.assertEqual(sessions.title_keys("https://acme.atlassian.net/browse/PROJ-369 Remove"), ["PROJ-369"])
        self.assertEqual(sessions.title_keys("OPS-218, OPS-207, OPS-238 development"), ["OPS-218", "OPS-207", "OPS-238"])
        self.assertEqual(sessions.title_keys("Partner Audit"), [])


class PathHelpersTest(unittest.TestCase):
    def test_split_worktree(self):
        self.assertEqual(sessions.split_worktree("/d/budget/.claude/worktrees/me-fix/sub"), ("/d/budget", "me-fix"))
        self.assertEqual(sessions.split_worktree("/d/budget"), ("/d/budget", None))

    def test_display_dir(self):
        root = Path("/d")
        self.assertEqual(sessions.display_dir("/d/acme/shop_2", root), "acme/shop_2")
        self.assertEqual(sessions.display_dir("/d", root), "d")
        self.assertEqual(sessions.display_dir("/elsewhere/x", root), "/elsewhere/x")
        home = str(Path.home())
        self.assertEqual(sessions.display_dir(home + "/proj", root), "~/proj")

    def test_resume_command_quotes_cwd(self):
        cmd = sessions.resume_command("/Users/x/it's dir", "abc-123")
        self.assertEqual(shlex.split(cmd), ["cd", "/Users/x/it's dir", "&&", "claude", "--resume", "abc-123"])


if __name__ == "__main__":
    unittest.main()
