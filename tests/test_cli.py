import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_quota_guardian.cli import main
from codex_quota_guardian.installer import InstallLayout
from codex_quota_guardian.keychain import KeychainError


class CliTest(unittest.TestCase):
    def test_keychain_token_prints_only_token(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            with patch("codex_quota_guardian.cli.Keychain.read", return_value="sk-example"):
                code = main(
                    ["keychain-token"],
                    home=Path(directory),
                    stdout=output,
                )

        self.assertEqual(0, code)
        self.assertEqual("sk-example\n", output.getvalue())

    def test_keychain_error_is_concise_and_nonzero(self):
        error = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "codex_quota_guardian.cli.Keychain.read",
                side_effect=KeychainError("unavailable"),
            ):
                code = main(
                    ["keychain-token"],
                    home=Path(directory),
                    stderr=error,
                )

        self.assertEqual(2, code)
        self.assertEqual("unavailable\n", error.getvalue())

    def test_status_before_install_is_read_only(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)

            code = main(["status"], home=home, stdout=output)

            self.assertEqual(1, code)
            self.assertIn("尚未安装", output.getvalue())
            self.assertFalse(InstallLayout.from_home(home).app_support_dir.exists())


if __name__ == "__main__":
    unittest.main()
