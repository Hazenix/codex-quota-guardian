from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import GuardianMode, GuardianState
from .quota import WeeklyQuota
from .state import should_prompt_for_exhaustion


class GuardianError(RuntimeError):
    pass


@dataclass(frozen=True)
class GuardianResult:
    action: str
    message: str
    quota: WeeklyQuota


class Guardian:
    def __init__(
        self,
        quota_reader: Callable[[], WeeklyQuota],
        state_store,
        config_manager,
        keychain,
        notifier,
        app,
        model_catalog_path: Path,
        auth_helper_path: Path,
        config_validator: Callable[[Path], None],
        deepseek_model: str = "deepseek-flash",
    ):
        self.quota_reader = quota_reader
        self.state_store = state_store
        self.config_manager = config_manager
        self.keychain = keychain
        self.notifier = notifier
        self.app = app
        self.model_catalog_path = Path(model_catalog_path)
        self.auth_helper_path = Path(auth_helper_path)
        self.config_validator = config_validator
        self.deepseek_model = deepseek_model

    def check(self) -> GuardianResult:
        state = self.state_store.load()
        quota = self.quota_reader()

        if state.mode == GuardianMode.GPT_RESTORED_RESTART_PENDING:
            if not self.app.is_running():
                self.state_store.save(GuardianState())
                return GuardianResult("gpt_restored", "GPT 配置已生效", quota)
            if not quota.known:
                return GuardianResult(
                    "quota_unknown",
                    "暂时无法确认周额度；不会更改 provider",
                    quota,
                )
            return GuardianResult(
                "restart_pending",
                "GPT 配置已恢复，等待 ChatGPT App 安全重启",
                quota,
            )

        if not quota.known:
            return GuardianResult(
                "quota_unknown",
                "暂时无法确认周额度；不会更改 provider",
                quota,
            )

        if state.mode == GuardianMode.DEEPSEEK_ACTIVE:
            if quota.exhausted:
                return GuardianResult(
                    "deepseek_active",
                    "GPT 周额度仍未恢复，继续使用 DeepSeek",
                    quota,
                )
            return self._restore_after_refresh(state, quota)

        if not quota.exhausted:
            if state.mode == GuardianMode.DEEPSEEK_PROMPTED_DECLINED:
                self.state_store.save(GuardianState(weekly_reset_at=quota.resets_at))
            return GuardianResult("gpt_active", "GPT 周额度可用", quota)

        if not should_prompt_for_exhaustion(state, quota.resets_at):
            return GuardianResult(
                "already_declined",
                "本额度窗口已选择暂不切换",
                quota,
            )

        if (
            getattr(self.notifier, "background", False)
            and state.notification_sent_for_reset == quota.resets_at
        ):
            return GuardianResult(
                "already_notified",
                "已通知等待切换决定",
                quota,
            )

        if not self.keychain.exists():
            if not getattr(self.notifier, "background", False):
                raise GuardianError(
                    "DeepSeek API Key 尚未存入 macOS Keychain；请先运行 setup-key"
                )
            self.state_store.save(
                GuardianState(
                    weekly_reset_at=quota.resets_at,
                    notification_sent_for_reset=quota.resets_at,
                )
            )
            self.notifier.notify(
                "Codex 周额度已用完；请先运行 codex-quota-guardian setup-key。"
            )
            return GuardianResult(
                "key_missing",
                "周额度已用完，但尚未配置 DeepSeek API Key",
                quota,
            )

        decision = self.notifier.ask_switch()
        if decision is None:
            self.state_store.save(
                GuardianState(
                    weekly_reset_at=quota.resets_at,
                    notification_sent_for_reset=quota.resets_at,
                )
            )
            self.notifier.notify(
                "Codex 周额度已用完；运行 codex-quota-guardian check 决定是否切换。"
            )
            return GuardianResult(
                "switch_decision_pending",
                "无法显示切换对话框，已发送一次通知",
                quota,
            )

        if not decision:
            self.state_store.save(
                GuardianState(
                    mode=GuardianMode.DEEPSEEK_PROMPTED_DECLINED,
                    weekly_reset_at=quota.resets_at,
                    prompted_reset_at=quota.resets_at,
                )
            )
            return GuardianResult("switch_declined", "已保留 GPT 配置", quota)

        return self._switch_to_deepseek(quota, restart_mode="authorized")

    def force_switch(self) -> GuardianResult:
        quota = WeeklyQuota(False, reason="manual")
        state = self.state_store.load()
        if state.mode == GuardianMode.DEEPSEEK_ACTIVE:
            return GuardianResult("deepseek_active", "已经在使用 DeepSeek", quota)
        return self._switch_to_deepseek(quota, restart_mode="ask")

    def force_restore(self) -> GuardianResult:
        quota = WeeklyQuota(False, reason="manual")
        state = self.state_store.load()
        if state.mode == GuardianMode.GPT_RESTORED_RESTART_PENDING:
            if self.app.is_running() and self.notifier.ask_restart():
                self.app.restart()
                self.state_store.save(GuardianState())
                return GuardianResult("gpt_restored", "GPT 配置已恢复并重启 App", quota)
            return GuardianResult(
                "gpt_restored_restart_pending",
                "GPT 配置已恢复；ChatGPT App 将在下次启动时生效",
                quota,
            )
        if state.mode != GuardianMode.DEEPSEEK_ACTIVE:
            return GuardianResult("gpt_active", "已经在使用 GPT 配置", quota)
        return self._restore_after_refresh(state, quota)

    def _switch_to_deepseek(
        self,
        quota: WeeklyQuota,
        restart_mode: str,
    ) -> GuardianResult:

        if not self.keychain.exists():
            raise GuardianError(
                "DeepSeek API Key 尚未存入 macOS Keychain；请先运行 setup-key"
            )

        switch = self.config_manager.switch_to_deepseek(
            model=self.deepseek_model,
            model_catalog_path=self.model_catalog_path,
            auth_helper_path=self.auth_helper_path,
            validate=self.config_validator,
        )
        self.state_store.save(
            GuardianState(
                mode=GuardianMode.DEEPSEEK_ACTIVE,
                weekly_reset_at=quota.resets_at,
                managed_config_hash=switch.managed_hash,
                original_config_hash=switch.original_hash,
                original_config_existed=switch.original_existed,
                backup_path=switch.backup_path,
                prompted_reset_at=quota.resets_at,
            )
        )
        if self.app.is_running():
            if restart_mode == "authorized":
                self.app.restart()
            elif self.notifier.ask_restart_after_switch():
                self.app.restart()
            else:
                return GuardianResult(
                    "switched_to_deepseek_restart_pending",
                    f"已切换到 {self.deepseek_model}；App 将在下次启动时生效",
                    quota,
                )
        return GuardianResult(
            "switched_to_deepseek",
            f"已切换到 {self.deepseek_model}",
            quota,
        )

    def _restore_after_refresh(
        self,
        state: GuardianState,
        quota: WeeklyQuota,
    ) -> GuardianResult:
        if not state.backup_path or not state.managed_config_hash:
            raise GuardianError("DeepSeek 状态缺少恢复信息；不会覆盖当前配置")

        self.config_manager.restore(
            backup_path=Path(state.backup_path),
            expected_managed_hash=state.managed_config_hash,
            original_existed=(
                state.original_config_existed
                if state.original_config_existed is not None
                else True
            ),
        )
        if not self.app.is_running():
            self.state_store.save(GuardianState(weekly_reset_at=quota.resets_at))
            return GuardianResult("gpt_restored", "GPT 配置已恢复", quota)

        pending = GuardianState(
            mode=GuardianMode.GPT_RESTORED_RESTART_PENDING,
            weekly_reset_at=quota.resets_at,
            original_config_hash=state.original_config_hash,
            original_config_existed=state.original_config_existed,
            backup_path=state.backup_path,
        )
        self.state_store.save(pending)
        if not self.notifier.ask_restart():
            return GuardianResult(
                "gpt_restored_restart_pending",
                "GPT 配置已恢复；ChatGPT App 将在下次启动时生效",
                quota,
            )

        self.app.restart()
        self.state_store.save(GuardianState(weekly_reset_at=quota.resets_at))
        return GuardianResult("gpt_restored", "GPT 配置已恢复并重启 App", quota)
