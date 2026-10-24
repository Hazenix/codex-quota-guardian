import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest

from codex_quota_guardian.installer import InstallLayout, Installer


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class InstallerTest(unittest.TestCase):
    def test_dry_run_does_not_write_home(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            installer = Installer(
                InstallLayout.from_home(home),
                source_root=PROJECT_ROOT,
                codex_path="/Applications/ChatGPT.app/codex",
            )

            planned = installer.install(dry_run=True, activate_launch_agent=False)

            self.assertFalse((home / ".local").exists())
            self.assertIn(str(home / ".local/bin/codex-auto"), planned)

    def test_install_creates_private_runtime_wrappers_and_valid_plist(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            layout = InstallLayout.from_home(home)
            installer = Installer(
                layout,
                source_root=PROJECT_ROOT,
                codex_path="/usr/bin/true",
            )

            installer.install(dry_run=False, activate_launch_agent=False)

            self.assertTrue((layout.runtime_dir / "codex_quota_guardian/cli.py").exists())
            self.assertTrue(layout.models_path.exists())
            self.assertEqual(0o700, os.stat(layout.app_support_dir).st_mode & 0o777)
            self.assertEqual(0o600, os.stat(layout.install_manifest).st_mode & 0o777)

            wrapper = layout.codex_auto_path.read_text(encoding="utf-8")
            self.assertIn("/usr/bin/true", wrapper)
            self.assertIn('"$@"', wrapper)
            self.assertIn(str(layout.guardian_path), wrapper)

            with layout.launch_agent_path.open("rb") as handle:
                plist = plistlib.load(handle)
            self.assertEqual(300, plist["StartInterval"])
            self.assertEqual(
                [str(layout.guardian_path), "check", "--background"],
                plist["ProgramArguments"],
            )

            manifest = json.loads(layout.install_manifest.read_text(encoding="utf-8"))
            self.assertEqual("deepseek-flash", manifest["deepseek_model"])

            result = subprocess.run(
                [str(layout.guardian_path), "status"],
                capture_output=True,
                text=True,
                check=False,
                env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
                timeout=10,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("状态：gpt", result.stdout)

    def test_uninstall_removes_runtime_but_keeps_state_and_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            layout = InstallLayout.from_home(home)
            installer = Installer(
                layout,
                source_root=PROJECT_ROOT,
                codex_path="/usr/bin/true",
            )
            installer.install(dry_run=False, activate_launch_agent=False)
            layout.state_path.write_text("{}", encoding="utf-8")
            layout.backup_dir.mkdir()
            (layout.backup_dir / "keep.toml").write_text("backup", encoding="utf-8")

            installer.uninstall(deactivate_launch_agent=False, purge=False)

            self.assertFalse(layout.guardian_path.exists())
            self.assertFalse(layout.runtime_dir.exists())
            self.assertFalse(layout.launch_agent_path.exists())
            self.assertTrue(layout.state_path.exists())
            self.assertTrue((layout.backup_dir / "keep.toml").exists())


if __name__ == "__main__":
    unittest.main()
