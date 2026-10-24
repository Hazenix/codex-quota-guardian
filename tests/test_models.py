import unittest

from codex_quota_guardian.models import GuardianMode, GuardianState


class GuardianStateTest(unittest.TestCase):
    def test_default_state_is_gpt(self):
        state = GuardianState()

        self.assertEqual(GuardianMode.GPT, state.mode)
        self.assertEqual(1, state.schema_version)

    def test_round_trip_preserves_state(self):
        state = GuardianState(
            mode=GuardianMode.DEEPSEEK_ACTIVE,
            weekly_reset_at=1_800_000_000,
            managed_config_hash="abc123",
            original_config_hash="def456",
            original_config_existed=True,
            backup_path="/tmp/config.toml",
            prompted_reset_at=1_800_000_000,
        )

        restored = GuardianState.from_dict(state.to_dict())

        self.assertEqual(state, restored)

    def test_unknown_mode_fails_closed(self):
        with self.assertRaises(ValueError):
            GuardianState.from_dict({"schema_version": 1, "mode": "unknown"})


if __name__ == "__main__":
    unittest.main()
