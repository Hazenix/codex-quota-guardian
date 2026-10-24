import subprocess
import unittest

from codex_quota_guardian.notifier import MacNotifier


class MacNotifierTest(unittest.TestCase):
    def test_switch_dialog_mentions_restart_and_parses_confirmation(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "button returned:切换并重启\n", "")

        notifier = MacNotifier(runner=runner)

        self.assertTrue(notifier.ask_switch())
        self.assertIn("切换将重启 ChatGPT App", calls[0][-1])

    def test_cancel_is_a_decline_not_an_exception(self):
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, "", "User canceled")

        self.assertFalse(MacNotifier(runner=runner).ask_restart())

    def test_manual_switch_restart_has_accurate_message(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "button returned:稍后\n", "")

        self.assertFalse(MacNotifier(runner=runner).ask_restart_after_switch())
        self.assertIn("DeepSeek 配置已写入", calls[0][-1])

    def test_unavailable_gui_returns_pending_decision(self):
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 2, "", "No user interaction allowed")

        self.assertIsNone(MacNotifier(runner=runner).ask_switch())


if __name__ == "__main__":
    unittest.main()
