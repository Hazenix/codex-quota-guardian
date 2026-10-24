import unittest

from codex_quota_guardian.quota import parse_weekly_quota


def response_with_windows(primary, secondary):
    return {
        "rateLimitsByLimitId": {
            "codex": {
                "primary": primary,
                "secondary": secondary,
            }
        }
    }


class WeeklyQuotaParserTest(unittest.TestCase):
    def test_finds_weekly_window_by_duration_not_position(self):
        response = response_with_windows(
            {"windowDurationMins": 300, "usedPercent": 100, "resetsAt": 10},
            {"windowDurationMins": 10080, "usedPercent": 42, "resetsAt": 20},
        )

        quota = parse_weekly_quota(response)

        self.assertTrue(quota.known)
        self.assertFalse(quota.exhausted)
        self.assertEqual(42.0, quota.used_percent)
        self.assertEqual(20, quota.resets_at)

    def test_weekly_window_at_one_hundred_percent_is_exhausted(self):
        response = response_with_windows(
            {"windowDurationMins": 10080, "usedPercent": 100, "resetsAt": 20},
            {"windowDurationMins": 300, "usedPercent": 1, "resetsAt": 10},
        )

        self.assertTrue(parse_weekly_quota(response).exhausted)

    def test_five_hour_exhaustion_does_not_trigger_weekly_switch(self):
        response = response_with_windows(
            {"windowDurationMins": 300, "usedPercent": 100, "resetsAt": 10},
            {"windowDurationMins": 10080, "usedPercent": 99, "resetsAt": 20},
        )

        self.assertFalse(parse_weekly_quota(response).exhausted)

    def test_missing_weekly_window_is_unknown(self):
        response = response_with_windows(
            {"windowDurationMins": 300, "usedPercent": 100, "resetsAt": 10},
            None,
        )

        quota = parse_weekly_quota(response)

        self.assertFalse(quota.known)
        self.assertFalse(quota.exhausted)

    def test_legacy_single_bucket_is_not_guessed(self):
        quota = parse_weekly_quota({"rateLimits": {"usedPercent": 100}})

        self.assertFalse(quota.known)


if __name__ == "__main__":
    unittest.main()
