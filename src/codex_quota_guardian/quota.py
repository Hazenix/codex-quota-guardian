from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional


WEEKLY_WINDOW_MINUTES = 7 * 24 * 60


@dataclass(frozen=True)
class WeeklyQuota:
    known: bool
    exhausted: bool = False
    used_percent: Optional[float] = None
    resets_at: Optional[int] = None
    reason: Optional[str] = None


def parse_weekly_quota(response: Dict[str, Any]) -> WeeklyQuota:
    """Return only an explicitly identified seven-day Codex quota window."""
    if not isinstance(response, dict):
        return WeeklyQuota(False, reason="response_not_object")

    by_limit_id = response.get("rateLimitsByLimitId")
    if not isinstance(by_limit_id, dict):
        return WeeklyQuota(False, reason="rate_limits_by_id_missing")

    codex_limits = by_limit_id.get("codex")
    if not isinstance(codex_limits, dict):
        return WeeklyQuota(False, reason="codex_limit_missing")

    weekly = next(
        (
            window
            for window in _candidate_windows(codex_limits)
            if window.get("windowDurationMins") == WEEKLY_WINDOW_MINUTES
        ),
        None,
    )
    if weekly is None:
        return WeeklyQuota(False, reason="weekly_window_missing")

    used_percent = weekly.get("usedPercent")
    resets_at = weekly.get("resetsAt")
    if isinstance(used_percent, bool) or not isinstance(used_percent, (int, float)):
        return WeeklyQuota(False, reason="weekly_used_percent_invalid")
    if isinstance(resets_at, bool) or not isinstance(resets_at, int):
        return WeeklyQuota(False, reason="weekly_reset_invalid")

    normalized_percent = float(used_percent)
    return WeeklyQuota(
        known=True,
        exhausted=normalized_percent >= 100.0,
        used_percent=normalized_percent,
        resets_at=resets_at,
    )


def _candidate_windows(codex_limits: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for key in ("primary", "secondary"):
        value = codex_limits.get(key)
        if isinstance(value, dict):
            yield value
