import subprocess


DEFAULT_SERVICE = "com.hazehacker.codex-quota-guardian.deepseek"
DEFAULT_ACCOUNT = "deepseek-api"


class KeychainError(RuntimeError):
    pass


class Keychain:
    def __init__(
        self,
        service: str = DEFAULT_SERVICE,
        account: str = DEFAULT_ACCOUNT,
        runner=subprocess.run,
    ):
        self.service = service
        self.account = account
        self.runner = runner

    def store_interactively(self) -> None:
        # Passing -w without a value makes macOS security prompt securely instead
        # of exposing the secret in argv or shell history.
        command = [
            "/usr/bin/security",
            "add-generic-password",
            "-a",
            self.account,
            "-s",
            self.service,
            "-U",
            "-w",
        ]
        result = self.runner(command, check=False)
        if result.returncode != 0:
            raise KeychainError("Unable to store the DeepSeek API key in macOS Keychain")

    def exists(self) -> bool:
        result = self.runner(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                self.account,
                "-s",
                self.service,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        return result.returncode == 0

    def read(self) -> str:
        result = self.runner(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                self.account,
                "-s",
                self.service,
                "-w",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        secret = result.stdout.strip() if result.returncode == 0 else ""
        if not secret:
            raise KeychainError("DeepSeek API key is unavailable in macOS Keychain")
        return secret
