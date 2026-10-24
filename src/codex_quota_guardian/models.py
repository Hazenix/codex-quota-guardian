from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, Optional


class GuardianMode(str, Enum):
    GPT = "gpt"
    DEEPSEEK_PROMPTED_DECLINED = "deepseek_prompted_declined"
    DEEPSEEK_ACTIVE = "deepseek_active"
    GPT_RESTORED_RESTART_PENDING = "gpt_restored_restart_pending"


@dataclass(frozen=True)
class GuardianState:
    schema_version: int = 1
    mode: GuardianMode = GuardianMode.GPT
    weekly_reset_at: Optional[int] = None
    managed_config_hash: Optional[str] = None
    original_config_hash: Optional[str] = None
    original_config_existed: Optional[bool] = None
    backup_path: Optional[str] = None
    prompted_reset_at: Optional[int] = None
    notification_sent_for_reset: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["mode"] = self.mode.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GuardianState":
        if data.get("schema_version", 1) != 1:
            raise ValueError("Unsupported guardian state schema")
        try:
            mode = GuardianMode(data.get("mode", GuardianMode.GPT.value))
        except (TypeError, ValueError) as error:
            raise ValueError("Invalid guardian state mode") from error
        return cls(
            schema_version=1,
            mode=mode,
            weekly_reset_at=_optional_int(data.get("weekly_reset_at")),
            managed_config_hash=_optional_string(data.get("managed_config_hash")),
            original_config_hash=_optional_string(data.get("original_config_hash")),
            original_config_existed=_optional_bool(data.get("original_config_existed")),
            backup_path=_optional_string(data.get("backup_path")),
            prompted_reset_at=_optional_int(data.get("prompted_reset_at")),
            notification_sent_for_reset=_optional_int(
                data.get("notification_sent_for_reset")
            ),
        )


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Expected an integer or null")
    return value


def _optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Expected a string or null")
    return value


def _optional_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError("Expected a boolean or null")
    return value
