from pathlib import Path
import tempfile
import unittest

from codex_quota_guardian.config import SwitchResult
from codex_quota_guardian.guardian import Guardian
from codex_quota_guardian.models import GuardianMode, GuardianState
from codex_quota_guardian.quota import WeeklyQuota
from codex_quota_guardian.state import StateStore


class FakeConfigManager:
    def __init__(self):
        self.switch_calls = 0
        self.restore_calls = 0

    def switch_to_deepseek(self, **kwargs):
        self.switch_calls += 1
        return SwitchResult("/tmp/backup.toml", "managed", "original")

    def restore(self, backup_path, expected_managed_hash, original_existed=True):
        self.restore_calls += 1
        return "original"


class FakeNotifier:
    def __init__(self, switch=True, restart=True, background=False):
        self.switch = switch
        self.restart = restart
        self.background = background
        self.switch_prompts = 0
        self.restart_prompts = 0
        self.notifications = 0

    def ask_switch(self):
        self.switch_prompts += 1
        return self.switch

    def ask_restart(self):
        self.restart_prompts += 1
        return self.restart

    def ask_restart_after_switch(self):
        self.restart_prompts += 1
        return self.restart

    def notify(self, message):
        self.notifications += 1


class FakeApp:
    def __init__(self, running=False):
        self.running = running
        self.restarts = 0

    def is_running(self):
        return self.running

    def restart(self):
        self.restarts += 1


class FakeKeychain:
    def __init__(self, exists=True):
        self.available = exists

    def exists(self):
        return self.available


class GuardianTest(unittest.TestCase):
    def make_guardian(
        self,
        directory,
        quota,
        notifier=None,
        app=None,
        config=None,
        keychain=None,
    ):
        return Guardian(
            quota_reader=lambda: quota,
            state_store=StateStore(Path(directory) / "state.json"),
            config_manager=config or FakeConfigManager(),
            keychain=keychain or FakeKeychain(),
            notifier=notifier or FakeNotifier(),
            app=app or FakeApp(),
            model_catalog_path=Path(directory) / "models.json",
            auth_helper_path=Path(directory) / "keychain-helper",
            config_validator=lambda path: None,
        )

    def test_decline_is_remembered_for_same_reset_window(self):
        with tempfile.TemporaryDirectory() as directory:
            notifier = FakeNotifier(switch=False)
            config = FakeConfigManager()
            guardian = self.make_guardian(
                directory,
                WeeklyQuota(True, True, 100, 500),
                notifier=notifier,
                config=config,
            )

            first = guardian.check()
            second = guardian.check()

            self.assertEqual("switch_declined", first.action)
            self.assertEqual("already_declined", second.action)
            self.assertEqual(1, notifier.switch_prompts)
            self.assertEqual(0, config.switch_calls)

    def test_accept_switches_and_restarts_running_app(self):
        with tempfile.TemporaryDirectory() as directory:
            app = FakeApp(running=True)
            config = FakeConfigManager()
            guardian = self.make_guardian(
                directory,
                WeeklyQuota(True, True, 100, 500),
                app=app,
                config=config,
            )

            result = guardian.check()

            self.assertEqual("switched_to_deepseek", result.action)
            self.assertEqual(1, config.switch_calls)
            self.assertEqual(1, app.restarts)
            self.assertEqual(
                GuardianMode.DEEPSEEK_ACTIVE,
                StateStore(Path(directory) / "state.json").load().mode,
            )

    def test_refresh_restores_config_but_restart_can_be_deferred(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.json")
            store.save(
                GuardianState(
                    mode=GuardianMode.DEEPSEEK_ACTIVE,
                    weekly_reset_at=500,
                    managed_config_hash="managed",
                    original_config_hash="original",
                    backup_path="/tmp/backup.toml",
                )
            )
            notifier = FakeNotifier(restart=False)
            app = FakeApp(running=True)
            config = FakeConfigManager()
            guardian = self.make_guardian(
                directory,
                WeeklyQuota(True, False, 1, 600),
                notifier=notifier,
                app=app,
                config=config,
            )

            result = guardian.check()

            self.assertEqual("gpt_restored_restart_pending", result.action)
            self.assertEqual(1, config.restore_calls)
            self.assertEqual(0, app.restarts)
            self.assertEqual(1, notifier.restart_prompts)
            self.assertEqual(
                GuardianMode.GPT_RESTORED_RESTART_PENDING,
                store.load().mode,
            )

    def test_pending_restart_finishes_when_app_is_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.json")
            store.save(GuardianState(mode=GuardianMode.GPT_RESTORED_RESTART_PENDING))
            guardian = self.make_guardian(
                directory,
                WeeklyQuota(True, False, 1, 600),
                app=FakeApp(running=False),
            )

            result = guardian.check()

            self.assertEqual("gpt_restored", result.action)
            self.assertEqual(GuardianMode.GPT, store.load().mode)

    def test_unknown_quota_never_switches_or_restores(self):
        with tempfile.TemporaryDirectory() as directory:
            config = FakeConfigManager()
            guardian = self.make_guardian(
                directory,
                WeeklyQuota(False, reason="missing"),
                config=config,
            )

            result = guardian.check()

            self.assertEqual("quota_unknown", result.action)
            self.assertEqual(0, config.switch_calls)
            self.assertEqual(0, config.restore_calls)

    def test_background_gui_failure_notifies_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            notifier = FakeNotifier(switch=None, background=True)
            guardian = self.make_guardian(
                directory,
                WeeklyQuota(True, True, 100, 500),
                notifier=notifier,
            )

            first = guardian.check()
            second = guardian.check()

            self.assertEqual("switch_decision_pending", first.action)
            self.assertEqual("already_notified", second.action)
            self.assertEqual(1, notifier.switch_prompts)
            self.assertEqual(1, notifier.notifications)

    def test_background_missing_key_notifies_without_switch_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            notifier = FakeNotifier(background=True)
            guardian = self.make_guardian(
                directory,
                WeeklyQuota(True, True, 100, 500),
                notifier=notifier,
                keychain=FakeKeychain(exists=False),
            )

            first = guardian.check()
            second = guardian.check()

            self.assertEqual("key_missing", first.action)
            self.assertEqual("already_notified", second.action)
            self.assertEqual(0, notifier.switch_prompts)
            self.assertEqual(1, notifier.notifications)

    def test_manual_switch_does_not_restart_without_separate_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            notifier = FakeNotifier(restart=False)
            app = FakeApp(running=True)
            config = FakeConfigManager()
            guardian = self.make_guardian(
                directory,
                WeeklyQuota(False, reason="manual"),
                notifier=notifier,
                app=app,
                config=config,
            )

            result = guardian.force_switch()

            self.assertEqual("switched_to_deepseek_restart_pending", result.action)
            self.assertEqual(1, config.switch_calls)
            self.assertEqual(0, app.restarts)

    def test_manual_restore_uses_safe_restart_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory) / "state.json")
            store.save(
                GuardianState(
                    mode=GuardianMode.DEEPSEEK_ACTIVE,
                    managed_config_hash="managed",
                    backup_path="/tmp/backup.toml",
                )
            )
            notifier = FakeNotifier(restart=True)
            app = FakeApp(running=True)
            config = FakeConfigManager()
            guardian = self.make_guardian(
                directory,
                WeeklyQuota(False, reason="manual"),
                notifier=notifier,
                app=app,
                config=config,
            )

            result = guardian.force_restore()

            self.assertEqual("gpt_restored", result.action)
            self.assertEqual(1, config.restore_calls)
            self.assertEqual(1, app.restarts)
            self.assertEqual(GuardianMode.GPT, store.load().mode)


if __name__ == "__main__":
    unittest.main()
