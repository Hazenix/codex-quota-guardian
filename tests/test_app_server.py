import io
import json
import unittest

from codex_quota_guardian.app_server import AppServerClient, AppServerError


class FakeStdin(io.StringIO):
    def close(self):
        pass


class FakeProcess:
    def __init__(self, stdout_text):
        self.stdin = FakeStdin()
        self.stdout = io.StringIO(stdout_text)
        self.stderr = io.StringIO("")
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True


class AppServerClientTest(unittest.TestCase):
    def test_reads_rate_limits_without_starting_a_thread_or_turn(self):
        process = FakeProcess(
            "\n".join(
                [
                    json.dumps({"id": 1, "result": {}}),
                    json.dumps({"method": "account/rateLimits/updated", "params": {}}),
                    json.dumps(
                        {
                            "id": 2,
                            "result": {
                                "rateLimitsByLimitId": {
                                    "codex": {
                                        "primary": {
                                            "windowDurationMins": 10080,
                                            "usedPercent": 9,
                                            "resetsAt": 123,
                                        }
                                    }
                                }
                            },
                        }
                    ),
                    "",
                ]
            )
        )
        commands = []

        def popen(command, **kwargs):
            commands.append(command)
            return process

        result = AppServerClient("/opt/codex", popen_factory=popen).read_rate_limits()

        requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
        methods = [request.get("method") for request in requests]
        self.assertEqual(9, result["rateLimitsByLimitId"]["codex"]["primary"]["usedPercent"])
        self.assertEqual([["/opt/codex", "app-server", "--stdio"]], commands)
        self.assertIn("initialize", methods)
        self.assertIn("account/rateLimits/read", methods)
        self.assertNotIn("thread/start", methods)
        self.assertNotIn("turn/start", methods)
        self.assertTrue(process.terminated)

    def test_rpc_error_is_redacted(self):
        process = FakeProcess(
            json.dumps({"id": 1, "error": {"message": "secret-value"}}) + "\n"
        )

        with self.assertRaisesRegex(AppServerError, "initialize failed") as raised:
            AppServerClient("/opt/codex", popen_factory=lambda *args, **kwargs: process).read_rate_limits()

        self.assertNotIn("secret-value", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
