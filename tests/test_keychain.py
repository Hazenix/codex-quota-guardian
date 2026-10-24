import subprocess
import unittest

from codex_quota_guardian.keychain import Keychain, KeychainError


class KeychainTest(unittest.TestCase):
    def test_store_uses_security_prompt_instead_of_command_line_secret(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "", "")

        Keychain(runner=runner).store_interactively()

        command, kwargs = calls[0]
        self.assertEqual("/usr/bin/security", command[0])
        self.assertEqual("-w", command[-1])
        self.assertNotIn("sk-test-secret", " ".join(command))
        self.assertFalse(kwargs.get("capture_output", False))

    def test_read_returns_secret_but_redacts_command_error(self):
        def success(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, "sk-secret\n", "")

        self.assertEqual("sk-secret", Keychain(runner=success).read())

        def failure(command, **kwargs):
            return subprocess.CompletedProcess(command, 44, "", "sk-leaked-error")

        with self.assertRaises(KeychainError) as raised:
            Keychain(runner=failure).read()
        self.assertNotIn("sk-leaked-error", str(raised.exception))

    def test_exists_does_not_read_secret(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        self.assertTrue(Keychain(runner=runner).exists())
        self.assertNotIn("-w", calls[0])


if __name__ == "__main__":
    unittest.main()
