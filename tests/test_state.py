import json
import os
from pathlib import Path
import tempfile
import unittest

from codex_quota_guardian.models import GuardianMode, GuardianState
from codex_quota_guardian.state import StateStore, should_prompt_for_exhaustion


class StateStoreTest(unittest.TestCase):
    def test_missing_file_returns_default_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.json")

            self.assertEqual(GuardianMode.GPT, store.load().mode)

    def test_save_is_private_and_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = GuardianState(
                mode=GuardianMode.DEEPSEEK_ACTIVE,
                weekly_reset_at=123,
            )

            StateStore(path).save(state)

            self.assertEqual(state, StateStore(path).load())
            self.assertEqual(0o600, os.stat(path).st_mode & 0o777)
            self.assertEqual(1, json.loads(path.read_text())["schema_version"])

    def test_corrupt_state_raises_instead_of_assuming_gpt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("not-json", encoding="utf-8")

            with self.assertRaises(ValueError):
                StateStore(path).load()


class PromptDeduplicationTest(unittest.TestCase):
    def test_declined_reset_window_is_not_prompted_again(self):
        state = GuardianState(
            mode=GuardianMode.DEEPSEEK_PROMPTED_DECLINED,
            prompted_reset_at=500,
        )

        self.assertFalse(should_prompt_for_exhaustion(state, 500))
        self.assertTrue(should_prompt_for_exhaustion(state, 600))


if __name__ == "__main__":
    unittest.main()
