"""Tests for applying the MESSAGE_RATE_LIMITS configuration key."""

from logging import getLogger
from types import SimpleNamespace

from pytest import fixture

from flockwave.server.app import SkybrushServer
from flockwave.server.message_hub import (
    BatchMessageRateLimiter,
    ConnectionStatusMessageRateLimiter,
    RateLimiters,
    UAVMessageRateLimiter,
)

from .rate_limiter_helpers import create_message, noop_dispatcher


@fixture
def server():
    """Stub with only the attributes that ``_process_configuration()`` uses.

    Building a real ``SkybrushServer`` would create every component of the
    server, while the method under test needs only these three.
    """
    rate_limiters = RateLimiters(dispatcher=noop_dispatcher)
    rate_limiters.register(
        "CONN-INF", ConnectionStatusMessageRateLimiter(factory=create_message)
    )
    rate_limiters.register("SYS-MSG", BatchMessageRateLimiter(factory=create_message))
    rate_limiters.register("UAV-INF", UAVMessageRateLimiter(factory=create_message))

    return SimpleNamespace(
        command_execution_manager=SimpleNamespace(timeout=None),
        log=getLogger("test_app_rate_limits"),
        rate_limiters=rate_limiters,
    )


def _process(server, config):
    SkybrushServer._process_configuration(server, config, use_env=False)  # type: ignore[arg-type]


def _delay_of(server, name):
    return server.rate_limiters._rate_limiters[name].delay


def test_applies_valid_rate_limits(server):
    _process(server, {"MESSAGE_RATE_LIMITS": {"UAV-INF": 0.05, "SYS-MSG": 1}})

    assert _delay_of(server, "UAV-INF") == 0.05
    assert _delay_of(server, "SYS-MSG") == 1


def test_keeps_defaults_without_rate_limits(server):
    _process(server, {})

    assert _delay_of(server, "UAV-INF") == 0.2
    assert _delay_of(server, "SYS-MSG") == 0.2


def test_ignores_invalid_delay_with_warning(server, caplog):
    _process(server, {"MESSAGE_RATE_LIMITS": {"UAV-INF": -1, "SYS-MSG": 0.5}})

    assert _delay_of(server, "UAV-INF") == 0.2
    assert _delay_of(server, "SYS-MSG") == 0.5
    assert "Ignoring rate limit for 'UAV-INF'" in caplog.text


def test_ignores_types_without_configurable_delay_with_warning(server, caplog):
    _process(server, {"MESSAGE_RATE_LIMITS": {"CONN-INF": 0.1, "NO-SUCH-TYPE": 0.1}})

    assert "Ignoring rate limit for 'CONN-INF'" in caplog.text
    assert "Ignoring rate limit for 'NO-SUCH-TYPE'" in caplog.text


def test_ignores_non_dict_rate_limits_with_warning(server, caplog):
    _process(server, {"MESSAGE_RATE_LIMITS": [0.05]})

    assert _delay_of(server, "UAV-INF") == 0.2
    assert "Ignoring MESSAGE_RATE_LIMITS" in caplog.text
