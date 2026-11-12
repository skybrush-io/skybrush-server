"""Tests for the validation of configured message rate limit delays."""

from math import inf, nan

from flockwave.server.app import _is_valid_rate_limit_delay


class TestRateLimitDelayValidation:
    def test_accepts_valid_delays(self):
        assert _is_valid_rate_limit_delay(0) is True
        assert _is_valid_rate_limit_delay(0.0) is True
        assert _is_valid_rate_limit_delay(0.05) is True
        assert _is_valid_rate_limit_delay(1) is True
        assert _is_valid_rate_limit_delay(2.5) is True

    def test_rejects_negative_delays(self):
        assert _is_valid_rate_limit_delay(-0.1) is False
        assert _is_valid_rate_limit_delay(-1) is False

    def test_rejects_non_finite_delays(self):
        assert _is_valid_rate_limit_delay(nan) is False
        assert _is_valid_rate_limit_delay(inf) is False
        assert _is_valid_rate_limit_delay(-inf) is False

    def test_rejects_non_numeric_delays(self):
        assert _is_valid_rate_limit_delay("0.05") is False
        assert _is_valid_rate_limit_delay(None) is False
        assert _is_valid_rate_limit_delay([0.05]) is False
        assert _is_valid_rate_limit_delay({"delay": 0.05}) is False

    def test_rejects_booleans(self):
        assert _is_valid_rate_limit_delay(True) is False
        assert _is_valid_rate_limit_delay(False) is False
