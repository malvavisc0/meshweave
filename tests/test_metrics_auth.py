"""Tests for the /metrics endpoint access policy (webapp.utils.metrics_auth)."""

from webapp.utils.metrics_auth import (
    ALLOWED,
    NOT_CONFIGURED,
    UNAUTHORIZED,
    check_metrics_access,
)


def test_no_token_configured_allows_open_access() -> None:
    assert check_metrics_access({}, environ={}) == ALLOWED


def test_missing_token_with_require_flag_fails_closed() -> None:
    env = {"WEBAPP_REQUIRE_METRICS_AUTH": "true"}
    assert check_metrics_access({}, environ=env) == NOT_CONFIGURED


def test_require_flag_truthy_variants_fail_closed() -> None:
    for value in ("1", "true", "yes", "on", "TRUE"):
        env = {"WEBAPP_REQUIRE_METRICS_AUTH": value}
        assert check_metrics_access({}, environ=env) == NOT_CONFIGURED


def test_require_flag_false_without_token_allows_access() -> None:
    env = {"WEBAPP_REQUIRE_METRICS_AUTH": "false"}
    assert check_metrics_access({}, environ=env) == ALLOWED


def test_blank_token_is_treated_as_unconfigured() -> None:
    env = {"METRICS_AUTH_TOKEN": "   ", "WEBAPP_REQUIRE_METRICS_AUTH": "true"}
    assert check_metrics_access({}, environ=env) == NOT_CONFIGURED


def test_configured_token_rejects_missing_credentials() -> None:
    env = {"METRICS_AUTH_TOKEN": "secret-token"}
    assert check_metrics_access({}, environ=env) == UNAUTHORIZED


def test_configured_token_rejects_wrong_token() -> None:
    env = {"METRICS_AUTH_TOKEN": "secret-token"}
    headers = {"authorization": "Bearer wrong"}
    assert check_metrics_access(headers, environ=env) == UNAUTHORIZED


def test_configured_token_rejects_wrong_x_metrics_token() -> None:
    env = {"METRICS_AUTH_TOKEN": "secret-token"}
    headers = {"x-metrics-token": "wrong"}
    assert check_metrics_access(headers, environ=env) == UNAUTHORIZED


def test_configured_token_accepts_bearer_header() -> None:
    env = {"METRICS_AUTH_TOKEN": "secret-token"}
    headers = {"authorization": "Bearer secret-token"}
    assert check_metrics_access(headers, environ=env) == ALLOWED


def test_configured_token_accepts_case_insensitive_bearer_scheme() -> None:
    env = {"METRICS_AUTH_TOKEN": "secret-token"}
    headers = {"authorization": "bearer secret-token"}
    assert check_metrics_access(headers, environ=env) == ALLOWED


def test_configured_token_accepts_case_insensitive_header_names() -> None:
    env = {"METRICS_AUTH_TOKEN": "secret-token"}
    headers = {"Authorization": "Bearer secret-token"}
    assert check_metrics_access(headers, environ=env) == ALLOWED


def test_configured_token_accepts_x_metrics_token_header() -> None:
    env = {"METRICS_AUTH_TOKEN": "secret-token"}
    headers = {"X-Metrics-Token": "secret-token"}
    assert check_metrics_access(headers, environ=env) == ALLOWED


def test_bearer_header_takes_precedence_over_x_metrics_token() -> None:
    env = {"METRICS_AUTH_TOKEN": "secret-token"}
    headers = {
        "authorization": "Bearer secret-token",
        "x-metrics-token": "wrong",
    }
    assert check_metrics_access(headers, environ=env) == ALLOWED
