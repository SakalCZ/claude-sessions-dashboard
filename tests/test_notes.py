import json
import tempfile
import unittest
from pathlib import Path

import notes


class NotesStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sub" / "notes.json"
        self.store = notes.NotesStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_means_no_notes(self):
        self.assertEqual(self.store.all(), {})

    def test_update_persists(self):
        entry = self.store.update("s1", status="waiting", note="čeká na CR")
        self.assertEqual(entry["status"], "waiting")
        self.assertEqual(entry["note"], "čeká na CR")
        self.assertTrue(entry["updated_at"].endswith("Z"))
        reloaded = notes.NotesStore(self.path).all()
        self.assertEqual(reloaded["s1"]["note"], "čeká na CR")
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["version"], 1)

    def test_partial_update_keeps_other_field(self):
        self.store.update("s1", status="active")
        self.store.update("s1", note="poznámka")
        self.assertEqual(self.store.all()["s1"]["status"], "active")
        self.store.update("s1", status="done")
        self.assertEqual(self.store.all()["s1"]["note"], "poznámka")

    def test_validation(self):
        with self.assertRaises(notes.NotesError):
            self.store.update("s1", status="bogus")
        with self.assertRaises(notes.NotesError):
            self.store.update("s1", note=123)
        self.assertFalse(self.path.exists())

    def test_note_is_single_line_and_truncated(self):
        entry = self.store.update("s1", note="a\nb  c\t" + "x" * 600)
        self.assertTrue(entry["note"].startswith("a b c x"))
        self.assertEqual(len(entry["note"]), notes.MAX_NOTE)

    def test_clearing_everything_removes_entry(self):
        self.store.update("s1", status="active", note="x")
        entry = self.store.update("s1", status=None, note="")
        self.assertIsNone(entry["status"])
        self.assertEqual(self.store.all(), {})

    def test_remove_entries(self):
        self.store.update("s1", status="done")
        self.store.update("s2", note="x")
        self.store.remove(["s1", "neni"])
        self.assertEqual(list(self.store.all()), ["s2"])

    def test_corrupt_file_is_backed_up(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.store.all(), {})
        backups = list(self.path.parent.glob("notes.json.corrupt-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "{broken")

    def test_no_temp_files_left(self):
        self.store.update("s1", status="active")
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["notes.json"])


if __name__ == "__main__":
    unittest.main()
