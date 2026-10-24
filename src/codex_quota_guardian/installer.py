from dataclasses import dataclass
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import tempfile
from typing import List


LAUNCH_AGENT_LABEL = "com.hazehacker.codex-quota-guardian"


@dataclass(frozen=True)
class InstallLayout:
    home: Path
    app_support_dir: Path
    runtime_dir: Path
    models_path: Path
    state_path: Path
    backup_dir: Path
    install_manifest: Path
    bin_dir: Path
    guardian_path: Path
    token_helper_path: Path
    codex_auto_path: Path
    launch_agent_path: Path
    log_path: Path

    @classmethod
    def from_home(cls, home: Path) -> "InstallLayout":
        home = Path(home)
        support = home / "Library/Application Support/CodexQuotaGuardian"
        bin_dir = home / ".local/bin"
        return cls(
            home=home,
            app_support_dir=support,
            runtime_dir=support / "runtime",
            models_path=support / "models.json",
            state_path=support / "state.json",
            backup_dir=support / "backups",
            install_manifest=support / "install.json",
            bin_dir=bin_dir,
            guardian_path=bin_dir / "codex-quota-guardian",
            token_helper_path=bin_dir / "codex-deepseek-token",
            codex_auto_path=bin_dir / "codex-auto",
            launch_agent_path=(
                home / "Library/LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"
            ),
            log_path=home / "Library/Logs/codex-quota-guardian.log",
        )


class Installer:
    def __init__(
        self,
        layout: InstallLayout,
        source_root: Path,
        codex_path: str,
        deepseek_model: str = "deepseek-flash",
        runner=subprocess.run,
    ):
        if deepseek_model not in {"deepseek-flash", "deepseek-v4-pro"}:
            raise ValueError("Unsupported DeepSeek model")
        self.layout = layout
        self.source_root = Path(source_root)
        self.codex_path = str(Path(codex_path))
        self.deepseek_model = deepseek_model
        self.runner = runner

    def install(
        self,
        dry_run: bool = False,
        activate_launch_agent: bool = True,
    ) -> List[str]:
        planned = [
            str(self.layout.runtime_dir),
            str(self.layout.models_path),
            str(self.layout.install_manifest),
            str(self.layout.guardian_path),
            str(self.layout.token_helper_path),
            str(self.layout.codex_auto_path),
            str(self.layout.launch_agent_path),
        ]
        if dry_run:
            return planned

        source_package = self.source_root / "src/codex_quota_guardian"
        source_models = self.source_root / "resources/models.json"
        if not source_package.is_dir() or not source_models.is_file():
            raise RuntimeError("Installation resources are incomplete")
        if not Path(self.codex_path).is_file():
            raise RuntimeError(f"Codex CLI not found: {self.codex_path}")

        self.layout.app_support_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.layout.app_support_dir, 0o700)
        self.layout.runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copytree(
            source_package,
            self.layout.runtime_dir / "codex_quota_guardian",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        _write_atomic(self.layout.models_path, source_models.read_bytes(), 0o600)

        manifest = {
            "schema_version": 1,
            "codex_path": self.codex_path,
            "deepseek_model": self.deepseek_model,
        }
        _write_atomic(
            self.layout.install_manifest,
            (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
            0o600,
        )

        self.layout.bin_dir.mkdir(parents=True, exist_ok=True)
        runtime = shlex.quote(str(self.layout.runtime_dir))
        guardian = shlex.quote(str(self.layout.guardian_path))
        codex = shlex.quote(self.codex_path)
        _write_atomic(
            self.layout.guardian_path,
            (
                "#!/bin/sh\n"
                f"export PYTHONPATH={runtime}\n"
                'exec /usr/bin/python3 -m codex_quota_guardian "$@"\n'
            ).encode("utf-8"),
            0o755,
        )
        _write_atomic(
            self.layout.token_helper_path,
            (
                "#!/bin/sh\n"
                f"exec {guardian} keychain-token\n"
            ).encode("utf-8"),
            0o755,
        )
        _write_atomic(
            self.layout.codex_auto_path,
            (
                "#!/bin/sh\n"
                f"{guardian} check\n"
                f'exec {codex} "$@"\n'
            ).encode("utf-8"),
            0o755,
        )

        self.layout.launch_agent_path.parent.mkdir(parents=True, exist_ok=True)
        self.layout.log_path.parent.mkdir(parents=True, exist_ok=True)
        plist = {
            "Label": LAUNCH_AGENT_LABEL,
            "ProgramArguments": [
                str(self.layout.guardian_path),
                "check",
                "--background",
            ],
            "RunAtLoad": True,
            "StartInterval": 300,
            "StandardOutPath": str(self.layout.log_path),
            "StandardErrorPath": str(self.layout.log_path),
            "ProcessType": "Background",
        }
        _write_atomic(
            self.layout.launch_agent_path,
            plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=True),
            0o644,
        )

        if activate_launch_agent:
            domain = f"gui/{os.getuid()}"
            self.runner(
                ["/bin/launchctl", "bootout", domain, str(self.layout.launch_agent_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            result = self.runner(
                ["/bin/launchctl", "bootstrap", domain, str(self.layout.launch_agent_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            if result.returncode != 0:
                raise RuntimeError("LaunchAgent installation failed")
        return planned

    def uninstall(
        self,
        deactivate_launch_agent: bool = True,
        purge: bool = False,
    ) -> None:
        if deactivate_launch_agent and self.layout.launch_agent_path.exists():
            self.runner(
                [
                    "/bin/launchctl",
                    "bootout",
                    f"gui/{os.getuid()}",
                    str(self.layout.launch_agent_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )

        for path in (
            self.layout.guardian_path,
            self.layout.token_helper_path,
            self.layout.codex_auto_path,
            self.layout.launch_agent_path,
            self.layout.models_path,
            self.layout.install_manifest,
        ):
            if path.is_file() or path.is_symlink():
                path.unlink()
        if self.layout.runtime_dir.is_dir():
            shutil.rmtree(self.layout.runtime_dir)

        if purge:
            if self.layout.state_path.is_file():
                self.layout.state_path.unlink()
            if self.layout.backup_dir.is_dir():
                shutil.rmtree(self.layout.backup_dir)


def _write_atomic(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
