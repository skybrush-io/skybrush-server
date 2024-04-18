from flockwave.server.model.preflight import PreflightCheckResult


def test_preflight_check_result():
    item = PreflightCheckResult.OFF
    assert not item.failed
    assert not item.failed_conclusively
    assert item.passed
    assert item.passed_without_warnings

    item = PreflightCheckResult.PASS
    assert not item.failed
    assert not item.failed_conclusively
    assert item.passed
    assert item.passed_without_warnings

    item = PreflightCheckResult.WARNING
    assert not item.failed
    assert not item.failed_conclusively
    assert item.passed
    assert not item.passed_without_warnings

    item = PreflightCheckResult.RUNNING
    assert not item.failed
    assert not item.failed_conclusively
    assert not item.passed
    assert not item.passed_without_warnings

    item = PreflightCheckResult.SOFT_FAILURE
    assert item.failed
    assert not item.failed_conclusively
    assert not item.passed
    assert not item.passed_without_warnings

    item = PreflightCheckResult.FAILURE
    assert item.failed
    assert item.failed_conclusively
    assert not item.passed
    assert not item.passed_without_warnings

    item = PreflightCheckResult.ERROR
    assert item.failed
    assert item.failed_conclusively
    assert not item.passed
    assert not item.passed_without_warnings
