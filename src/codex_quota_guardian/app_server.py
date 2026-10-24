import json
from queue import Empty, Queue
import subprocess
import threading
from typing import Any, Dict


class AppServerError(RuntimeError):
    pass


_EOF = object()


class AppServerClient:
    """Minimal JSON-RPC client that only reads account rate limits."""

    def __init__(self, codex_path: str, popen_factory=subprocess.Popen, timeout: float = 10.0):
        self.codex_path = codex_path
        self.popen_factory = popen_factory
        self.timeout = timeout

    def read_rate_limits(self) -> Dict[str, Any]:
        process = self.popen_factory(
            [self.codex_path, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        if process.stdin is None or process.stdout is None:
            raise AppServerError("app-server stdio is unavailable")

        messages = Queue()
        reader = threading.Thread(
            target=_read_lines,
            args=(process.stdout, messages),
            daemon=True,
            name="codex-quota-guardian-app-server-reader",
        )
        reader.start()
        try:
            self._send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {
                            "name": "codex-quota-guardian",
                            "version": "0.1.0",
                        }
                    },
                },
            )
            self._wait_for_response(messages, 1, "initialize")
            self._send(
                process,
                {"jsonrpc": "2.0", "method": "initialized", "params": {}},
            )
            self._send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "account/rateLimits/read",
                    "params": {},
                },
            )
            result = self._wait_for_response(messages, 2, "account/rateLimits/read")
            if not isinstance(result, dict):
                raise AppServerError("account/rateLimits/read returned an invalid result")
            return result
        finally:
            try:
                process.terminate()
                process.wait(timeout=2)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    @staticmethod
    def _send(process, message: Dict[str, Any]) -> None:
        process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        process.stdin.flush()

    def _wait_for_response(self, messages: Queue, request_id: int, method: str):
        while True:
            try:
                message = messages.get(timeout=self.timeout)
            except Empty as error:
                raise AppServerError(f"{method} timed out") from error
            if message is _EOF:
                raise AppServerError(f"{method} failed because app-server closed")
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            if "error" in message:
                raise AppServerError(f"{method} failed")
            if "result" not in message:
                raise AppServerError(f"{method} returned no result")
            return message["result"]


def _read_lines(stream, messages: Queue) -> None:
    try:
        for raw_line in stream:
            line = raw_line.strip()
            if not line:
                continue
            try:
                messages.put(json.loads(line))
            except json.JSONDecodeError:
                messages.put({"invalid_json": True})
    finally:
        messages.put(_EOF)
