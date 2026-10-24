from pathlib import Path
import tempfile
import unittest

from codex_quota_guardian.lock import AlreadyRunningError, ProcessLock


class ProcessLockTest(unittest.TestCase):
    def test_second_guardian_cannot_enter_same_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "guardian.lock"

            with ProcessLock(path):
                with self.assertRaises(AlreadyRunningError):
                    with ProcessLock(path):
                        pass


if __name__ == "__main__":
    unittest.main()
