import argparse
from functools import partial
import json
from pathlib import Path
import shutil
import sys

from .app_server import AppServerClient, AppServerError
from .config import ConfigManager, ConfigValidationError, validate_codex_config
from .guardian import Guardian, GuardianError
from .installer import InstallLayout, Installer
from .keychain import Keychain, KeychainError
from .lock import AlreadyRunningError, ProcessLock
from .models import GuardianMode
from .notifier import ChatGPTApp, MacNotifier, TerminalNotifier
from .quota import parse_weekly_quota
from .state import StateStore


DEFAULT_CODEX_PATH = (
    "/Applications/ChatGPT.app/Contents/Resources/codex-cli/"
    "CodexCLI.app/Contents/MacOS/codex"
)


def main(argv=None, home=None, stdout=None, stderr=None) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    home = Path(home) if home is not None else Path.home()
    parser = _parser()
    args = parser.parse_args(argv)
    layout = InstallLayout.from_home(home)

    try:
        if args.command == "install":
            codex_path = args.codex_path or _discover_codex()
            key_configured = Keychain().exists() if not args.dry_run else False
            source_root = Path(__file__).resolve().parents[2]
            installer = Installer(
                layout,
                source_root=source_root,
                codex_path=codex_path,
                deepseek_model=args.model,
            )
            paths = installer.install(
                dry_run=args.dry_run,
                activate_launch_agent=not args.no_activate,
            )
            heading = "计划写入" if args.dry_run else "已安装"
            print(f"{heading}：", file=stdout)
            for path in paths:
                print(f"- {path}", file=stdout)
            if not args.dry_run and not key_configured:
                print(
                    "注意：尚未配置 DeepSeek API Key，请运行 "
                    f"{layout.guardian_path} setup-key。",
                    file=stdout,
                )
            return 0

        if args.command == "setup-key":
            Keychain().store_interactively()
            print("DeepSeek API Key 已保存到 macOS Keychain。", file=stdout)
            return 0

        if args.command == "keychain-token":
            print(Keychain().read(), file=stdout)
            return 0

        if args.command == "status":
            if not layout.install_manifest.exists():
                print("Codex Quota Guardian 尚未安装。", file=stdout)
                return 1
            state = StateStore(layout.state_path).load()
            manifest = _load_manifest(layout)
            print(f"状态：{state.mode.value}", file=stdout)
            print(f"DeepSeek 模型：{manifest['deepseek_model']}", file=stdout)
            try:
                quota = _quota_reader(manifest["codex_path"])()
                if quota.known:
                    print(f"周额度已使用：{quota.used_percent:g}%", file=stdout)
                    print(f"周额度重置时间戳：{quota.resets_at}", file=stdout)
                else:
                    print(f"周额度：不可判定（{quota.reason}）", file=stdout)
            except AppServerError:
                print("周额度：读取失败", file=stdout)
            return 0

        if args.command == "uninstall":
            if not layout.install_manifest.exists():
                print("Codex Quota Guardian 尚未安装。", file=stdout)
                return 0
            manifest = _load_manifest(layout)
            with ProcessLock(layout.app_support_dir / "guardian.lock"):
                state = StateStore(layout.state_path).load()
                if state.mode in {
                    GuardianMode.DEEPSEEK_ACTIVE,
                    GuardianMode.GPT_RESTORED_RESTART_PENDING,
                }:
                    guardian = _build_guardian(layout, background=False)
                    guardian.force_restore()
                installer = Installer(
                    layout,
                    source_root=Path(__file__).resolve().parents[2],
                    codex_path=manifest["codex_path"],
                    deepseek_model=manifest["deepseek_model"],
                )
                installer.uninstall(purge=args.purge)
            print("Codex Quota Guardian 已卸载；GPT 配置已安全恢复。", file=stdout)
            if not args.purge:
                print("状态与配置备份已保留。", file=stdout)
            return 0

        guardian = _build_guardian(layout, background=getattr(args, "background", False))
        if args.command == "check":
            with ProcessLock(layout.app_support_dir / "guardian.lock"):
                result = guardian.check()
            quiet_actions = {
                "gpt_active",
                "deepseek_active",
                "already_declined",
                "already_notified",
                "restart_pending",
            }
            if not args.background or result.action not in quiet_actions:
                print(result.message, file=stdout)
            return 0
        if args.command == "restart-app":
            app = ChatGPTApp()
            if app.is_running():
                app.restart()
                print("ChatGPT App 已重启。", file=stdout)
            else:
                print("ChatGPT App 当前未运行。", file=stdout)
            return 0
        if args.command == "switch-deepseek":
            with ProcessLock(layout.app_support_dir / "guardian.lock"):
                result = guardian.force_switch()
            print(result.message, file=stdout)
            return 0
        if args.command == "restore-gpt":
            with ProcessLock(layout.app_support_dir / "guardian.lock"):
                result = guardian.force_restore()
            print(result.message, file=stdout)
            return 0
    except AlreadyRunningError as error:
        if not getattr(args, "background", False):
            print(str(error), file=stdout)
        return 0
    except (AppServerError, ConfigValidationError, GuardianError, KeychainError, RuntimeError, ValueError) as error:
        print(str(error), file=stderr)
        return 2

    parser.error("Unknown command")
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-quota-guardian")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install", help="安装工具和 LaunchAgent")
    install.add_argument("--dry-run", action="store_true")
    install.add_argument("--no-activate", action="store_true")
    install.add_argument("--codex-path")
    install.add_argument(
        "--model",
        choices=["deepseek-flash", "deepseek-v4-pro"],
        default="deepseek-flash",
    )

    subparsers.add_parser("setup-key", help="安全写入 DeepSeek API Key")
    subparsers.add_parser("keychain-token", help=argparse.SUPPRESS)
    subparsers.add_parser("status", help="显示 provider 和周额度状态")
    check = subparsers.add_parser("check", help="立即检查周额度")
    check.add_argument("--background", action="store_true")
    subparsers.add_parser("switch-deepseek", help="手动切换 DeepSeek")
    subparsers.add_parser("restore-gpt", help="手动恢复 GPT")
    subparsers.add_parser("restart-app", help="安全重启 ChatGPT App")
    uninstall = subparsers.add_parser("uninstall", help="安全恢复 GPT 后卸载")
    uninstall.add_argument("--purge", action="store_true", help="同时删除状态和备份")
    return parser


def _discover_codex() -> str:
    found = shutil.which("codex")
    if found:
        return str(Path(found).resolve())
    if Path(DEFAULT_CODEX_PATH).is_file():
        return DEFAULT_CODEX_PATH
    raise RuntimeError("未找到 Codex CLI；请使用 --codex-path 指定绝对路径")


def _load_manifest(layout: InstallLayout):
    try:
        data = json.loads(layout.install_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("安装清单损坏，请重新安装") from error
    if data.get("schema_version") != 1:
        raise RuntimeError("不支持的安装清单版本")
    return data


def _quota_reader(codex_path: str):
    return lambda: parse_weekly_quota(AppServerClient(codex_path).read_rate_limits())


def _build_guardian(layout: InstallLayout, background: bool) -> Guardian:
    if not layout.install_manifest.exists():
        raise RuntimeError("Codex Quota Guardian 尚未安装")
    manifest = _load_manifest(layout)
    codex_path = manifest["codex_path"]
    notifier = MacNotifier() if background else TerminalNotifier()
    return Guardian(
        quota_reader=_quota_reader(codex_path),
        state_store=StateStore(layout.state_path),
        config_manager=ConfigManager(layout.home / ".codex/config.toml", layout.backup_dir),
        keychain=Keychain(),
        notifier=notifier,
        app=ChatGPTApp(),
        model_catalog_path=layout.models_path,
        auth_helper_path=layout.token_helper_path,
        config_validator=partial(validate_codex_config, codex_path=codex_path),
        deepseek_model=manifest["deepseek_model"],
    )
