"""GitHub publishing must not include other local/staged user changes."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from macro_studio.deck_github_sync import DeckGitHubSyncError, publish_bundled_deck


@unittest.skipUnless(shutil.which("git"), "Git is required")
class DeckGitHubSyncTests(unittest.TestCase):
    def test_publish_only_bundled_file_preserves_other_changes(self) -> None:
        def git(root: Path, *args: str) -> str:
            return subprocess.run(
                ["git", "-C", str(root), *args], check=True, capture_output=True,
                text=True, encoding="utf-8", errors="replace",
            ).stdout.strip()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote = root / "remote.git"
            local = root / "work"
            remote.mkdir()
            local.mkdir()
            git(remote, "init", "--bare")
            git(local, "init", "-b", "main")
            git(local, "config", "user.name", "Deck Test")
            git(local, "config", "user.email", "deck-test@example.invalid")
            git(local, "remote", "add", "origin", str(remote))
            bundle = local / "deck_presets" / "macrorelay_bundled_deck.json"
            bundle.parent.mkdir()
            bundle.write_text('{"version":1}', encoding="utf-8")
            unrelated = local / "unrelated.txt"
            unrelated.write_text("original", encoding="utf-8")
            git(local, "add", ".")
            git(local, "commit", "-m", "Initial")
            git(local, "push", "origin", "main")
            original_head = git(local, "rev-parse", "HEAD")

            other = root / "other-work"
            git(root, "clone", "-b", "main", str(remote), str(other))
            git(other, "config", "user.name", "Other Test")
            git(other, "config", "user.email", "other-test@example.invalid")
            (other / "remote-only.txt").write_text("keep remote change", encoding="utf-8")
            git(other, "add", "remote-only.txt")
            git(other, "commit", "-m", "Another PC change")
            git(other, "push", "origin", "main")

            bundle.write_text('{"version":2}', encoding="utf-8")
            unrelated.write_text("staged user work", encoding="utf-8")
            git(local, "add", "unrelated.txt")
            commit = publish_bundled_deck(local, expected_remote=str(remote))

            self.assertEqual(original_head, git(local, "rev-parse", "HEAD"))
            self.assertEqual("unrelated.txt", git(local, "diff", "--cached", "--name-only"))
            self.assertEqual('{"version":2}', git(remote, "show", "main:deck_presets/macrorelay_bundled_deck.json"))
            self.assertEqual("original", git(remote, "show", "main:unrelated.txt"))
            self.assertEqual("keep remote change", git(remote, "show", "main:remote-only.txt"))
            self.assertEqual(commit, git(remote, "rev-parse", "main"))
            self.assertIsNone(publish_bundled_deck(local, expected_remote=str(remote)))
            with self.assertRaises(DeckGitHubSyncError):
                publish_bundled_deck(local, expected_remote="https://github.com/wrong/repo.git")


if __name__ == "__main__":
    unittest.main()
