import hashlib
from pathlib import Path
import tempfile
import unittest

from codex_quota_guardian.config import (
    ConfigConflictError,
    ConfigManager,
    ConfigValidationError,
    build_deepseek_config,
    validate_codex_config,
)


FIXTURES = Path(__file__).parent / "fixtures"


class ConfigRenderingTest(unittest.TestCase):
    def test_preserves_unrelated_sections_and_updates_existing_desktop(self):
        original = (FIXTURES / "config_full.toml").read_text(encoding="utf-8")

        rendered = build_deepseek_config(
            original,
            model="deepseek-flash",
            model_catalog_path=Path("/tmp/models.json"),
            auth_helper_path=Path("/tmp/keychain-helper"),
        )

        self.assertIn('model = "deepseek-flash"', rendered)
        self.assertIn('model_provider = "deepseek"', rendered)
        self.assertIn('[model_providers.deepseek]', rendered)
        self.assertIn('[model_providers.deepseek.auth]', rendered)
        self.assertIn('command = "/tmp/keychain-helper"', rendered)
        self.assertIn('[mcp_servers.example]', rendered)
        self.assertIn('[projects."/tmp/example"]', rendered)
        self.assertIn('analytics = false', rendered)
        self.assertEqual(1, rendered.count("[desktop]"))
        self.assertIn('enabled-reasoning-efforts = ["low", "medium", "high", "xhigh", "ultra", "max"]', rendered)

    def test_refuses_unmanaged_existing_deepseek_provider(self):
        original = '[model_providers.deepseek]\nbase_url = "https://example.invalid"\n'

        with self.assertRaises(ConfigConflictError):
            build_deepseek_config(
                original,
                model="deepseek-flash",
                model_catalog_path=Path("/tmp/models.json"),
                auth_helper_path=Path("/tmp/keychain-helper"),
            )

    def test_unrelated_managed_marker_does_not_authorize_provider_overwrite(self):
        original = (
            "# managed-by: codex-quota-guardian\n"
            "setting = true\n\n"
            "[model_providers.deepseek]\n"
            'base_url = "https://example.invalid"\n'
        )

        with self.assertRaises(ConfigConflictError):
            build_deepseek_config(
                original,
                model="deepseek-flash",
                model_catalog_path=Path("/tmp/models.json"),
                auth_helper_path=Path("/tmp/keychain-helper"),
            )

    def test_preserves_chatgpt_login_so_weekly_quota_remains_readable(self):
        original = 'forced_login_method = "chatgpt"\nmodel = "gpt-5.6-sol"\n'

        rendered = build_deepseek_config(
            original,
            model="deepseek-flash",
            model_catalog_path=Path("/tmp/models.json"),
            auth_helper_path=Path("/tmp/keychain-helper"),
        )

        self.assertIn('forced_login_method = "chatgpt"', rendered)
        self.assertNotIn('forced_login_method = "api"', rendered)
        self.assertNotIn("preferred_auth_method", rendered)

    def test_does_not_treat_multiline_string_content_as_toml_structure(self):
        original = '''instructions = """Keep this example verbatim:
[desktop]
model = "example-only"
"""
model = "gpt-5.6-sol"
'''

        rendered = build_deepseek_config(
            original,
            model="deepseek-flash",
            model_catalog_path=Path("/tmp/models.json"),
            auth_helper_path=Path("/tmp/keychain-helper"),
        )

        self.assertIn('[desktop]\nmodel = "example-only"', rendered)
        self.assertEqual(1, rendered.count('model = "example-only"'))
        self.assertIn('model = "deepseek-flash"', rendered)


class ConfigManagerTest(unittest.TestCase):
    def test_switch_creates_backup_and_restore_is_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            backup_dir = root / "backups"
            original = (FIXTURES / "config_minimal.toml").read_bytes()
            config_path.write_bytes(original)
            manager = ConfigManager(config_path, backup_dir)

            switch = manager.switch_to_deepseek(
                model="deepseek-flash",
                model_catalog_path=root / "models.json",
                auth_helper_path=root / "keychain-helper",
                validate=lambda path: None,
            )

            self.assertTrue(Path(switch.backup_path).exists())
            self.assertEqual(hashlib.sha256(config_path.read_bytes()).hexdigest(), switch.managed_hash)

            manager.restore(
                backup_path=Path(switch.backup_path),
                expected_managed_hash=switch.managed_hash,
            )

            self.assertEqual(original, config_path.read_bytes())

    def test_restore_refuses_to_overwrite_user_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text('model = "gpt"\n', encoding="utf-8")
            manager = ConfigManager(config_path, root / "backups")
            switch = manager.switch_to_deepseek(
                model="deepseek-flash",
                model_catalog_path=root / "models.json",
                auth_helper_path=root / "keychain-helper",
                validate=lambda path: None,
            )
            config_path.write_text(config_path.read_text() + "# user change\n", encoding="utf-8")

            with self.assertRaises(ConfigConflictError) as raised:
                manager.restore(Path(switch.backup_path), switch.managed_hash)

            self.assertTrue(raised.exception.proposed_restore_path.exists())
            self.assertEqual(
                'model = "gpt"\n',
                raised.exception.proposed_restore_path.read_text(encoding="utf-8"),
            )

    def test_restore_removes_config_if_it_did_not_exist_before_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            manager = ConfigManager(config_path, root / "backups")

            switch = manager.switch_to_deepseek(
                model="deepseek-flash",
                model_catalog_path=root / "models.json",
                auth_helper_path=root / "keychain-helper",
                validate=lambda path: None,
            )
            self.assertFalse(switch.original_existed)
            self.assertTrue(config_path.exists())

            manager.restore(
                Path(switch.backup_path),
                switch.managed_hash,
                original_existed=False,
            )

            self.assertFalse(config_path.exists())


class ConfigValidationTest(unittest.TestCase):
    def test_validation_uses_isolated_codex_home_and_strict_config(self):
        calls = []

        class Result:
            returncode = 0
            stdout = '{"id":1,"result":{}}\n'
            stderr = ""

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return Result()

        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate.toml"
            candidate.write_text('model = "deepseek-flash"\n', encoding="utf-8")
            validate_codex_config(candidate, "/opt/codex", runner=runner)

        command, kwargs = calls[0]
        self.assertEqual("/opt/codex", command[0])
        self.assertIn("--strict-config", command)
        self.assertIn("initialize", kwargs["input"])
        self.assertNotEqual(str(Path.home() / ".codex"), kwargs["env"]["CODEX_HOME"])

    def test_validation_error_does_not_echo_subprocess_output(self):
        class Result:
            returncode = 1
            stdout = "sk-secret"
            stderr = "bad config with sk-secret"

        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate.toml"
            candidate.write_text('model = "deepseek-flash"\n', encoding="utf-8")
            with self.assertRaises(ConfigValidationError) as raised:
                validate_codex_config(
                    candidate,
                    "/opt/codex",
                    runner=lambda *args, **kwargs: Result(),
                )

        self.assertNotIn("sk-secret", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
