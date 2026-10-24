import subprocess
import time


class MacNotifier:
    background = True

    def __init__(self, runner=subprocess.run):
        self.runner = runner

    def ask_switch(self) -> bool:
        script = (
            'display dialog "Codex 周额度已用完。切换将重启 ChatGPT App，'
            '是否切换到 DeepSeek 继续？" with title "Codex Quota Guardian" '
            'buttons {"暂不切换", "切换并重启"} default button "切换并重启" '
            'cancel button "暂不切换"'
        )
        return self._confirmed(script, "切换并重启")

    def ask_restart(self) -> bool:
        script = (
            'display dialog "GPT 额度已恢复，配置已切回；是否立即重启 ChatGPT App？" '
            'with title "Codex Quota Guardian" buttons {"稍后", "立即重启"} '
            'default button "立即重启" cancel button "稍后"'
        )
        return self._confirmed(script, "立即重启")

    def ask_restart_after_switch(self) -> bool:
        script = (
            'display dialog "DeepSeek 配置已写入，是否立即重启 ChatGPT App？" '
            'with title "Codex Quota Guardian" buttons {"稍后", "立即重启"} '
            'default button "立即重启" cancel button "稍后"'
        )
        return self._confirmed(script, "立即重启")

    def notify(self, message: str) -> None:
        safe_message = message.replace("\\", "\\\\").replace('"', '\\"')
        script = (
            f'display notification "{safe_message}" '
            'with title "Codex Quota Guardian"'
        )
        self.runner(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

    def _confirmed(self, script: str, expected_button: str) -> bool:
        try:
            result = self.runner(
                ["/usr/bin/osascript", "-e", script],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return None
        if result.returncode == 0:
            return f"button returned:{expected_button}" in result.stdout
        if result.returncode == 1:
            return False
        return None


class TerminalNotifier:
    background = False

    def ask_switch(self) -> bool:
        answer = input(
            "Codex 周额度已用完，是否切换到 DeepSeek？"
            "切换可能重启正在运行的 ChatGPT App。[y/N] "
        ).strip().lower()
        return answer in {"y", "yes"}

    def ask_restart(self) -> bool:
        answer = input("GPT 额度已恢复，是否立即重启 ChatGPT App？[y/N] ").strip().lower()
        return answer in {"y", "yes"}

    def ask_restart_after_switch(self) -> bool:
        answer = input("DeepSeek 配置已写入，是否立即重启 ChatGPT App？[y/N] ").strip().lower()
        return answer in {"y", "yes"}

    @staticmethod
    def notify(message: str) -> None:
        print(message)


class ChatGPTApp:
    def __init__(self, runner=subprocess.run):
        self.runner = runner

    def is_running(self) -> bool:
        result = self.runner(
            ["/usr/bin/pgrep", "-x", "ChatGPT"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        return result.returncode == 0

    def restart(self, wait_seconds: float = 20.0) -> None:
        self.runner(
            ["/usr/bin/osascript", "-e", 'tell application "ChatGPT" to quit'],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        deadline = time.monotonic() + wait_seconds
        while self.is_running() and time.monotonic() < deadline:
            time.sleep(0.25)
        if self.is_running():
            raise RuntimeError("ChatGPT App did not quit; refusing to force terminate it")
        result = self.runner(
            ["/usr/bin/open", "-a", "ChatGPT"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if result.returncode != 0:
            raise RuntimeError("Unable to reopen ChatGPT App")
