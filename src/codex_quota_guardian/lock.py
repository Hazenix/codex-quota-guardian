import fcntl
import os
from pathlib import Path


class AlreadyRunningError(Exception):
    pass


class ProcessLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.descriptor = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.chmod(self.path, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(descriptor)
            raise AlreadyRunningError("另一额度检查正在运行") from error
        self.descriptor = descriptor
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.descriptor is not None:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            os.close(self.descriptor)
            self.descriptor = None
