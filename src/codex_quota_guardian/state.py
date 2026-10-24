import json
import os
from pathlib import Path
import tempfile

from .models import GuardianMode, GuardianState


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> GuardianState:
        if not self.path.exists():
            return GuardianState()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("State root must be an object")
            return GuardianState.from_dict(data)
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as error:
            raise ValueError("Guardian state is invalid; refusing an automatic switch") from error

    def save(self, state: GuardianState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            dir=str(self.path.parent),
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state.to_dict(), handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()


def should_prompt_for_exhaustion(state: GuardianState, reset_at: int) -> bool:
    return not (
        state.mode == GuardianMode.DEEPSEEK_PROMPTED_DECLINED
        and state.prompted_reset_at == reset_at
    )
