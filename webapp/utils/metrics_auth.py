"""Access control for the /metrics endpoint.

Kept dependency-free (no fastapi / prometheus_client imports) so the policy
is unit-testable without the webapp runtime stack installed.
"""

import hmac
import os

# Outcome constants returned by ``check_metrics_access``.
ALLOWED = "allowed"
UNAUTHORIZED = "unauthorized"  # token configured, credentials missing/wrong
NOT_CONFIGURED = "not_configured"  # auth required by policy but no token set


def check_metrics_access(
    headers: dict[str, str],
    *,
    environ: dict[str, str] | None = None,
) -> str:
    """Decide whether a /metrics request is allowed.

    Policy:
    - ``METRICS_AUTH_TOKEN`` set: require a matching bearer token, supplied
      either as ``Authorization: Bearer <token>`` (case-insensitive scheme)
      or as the ``X-Metrics-Token`` header. Comparison is constant-time.
    - Token unset but ``WEBAPP_REQUIRE_METRICS_AUTH`` truthy: fail closed
      (``NOT_CONFIGURED``) so production cannot silently expose metrics.
    - Otherwise: open access (local/dev default).

    Args:
        headers: Request headers. Keys are matched case-insensitively.
        environ: Environment to read config from; defaults to ``os.environ``.

    Returns:
        One of ``ALLOWED``, ``UNAUTHORIZED``, or ``NOT_CONFIGURED``.
    """
    env = os.environ if environ is None else environ
    configured_token = env.get("METRICS_AUTH_TOKEN", "").strip()
    if not configured_token:
        require = env.get("WEBAPP_REQUIRE_METRICS_AUTH", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        return NOT_CONFIGURED if require else ALLOWED

    lowered = {key.lower(): value for key, value in headers.items()}
    supplied_token = lowered.get("x-metrics-token", "")
    authorization = lowered.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        supplied_token = authorization[7:].strip()
    if not supplied_token or not hmac.compare_digest(supplied_token, configured_token):
        return UNAUTHORIZED
    return ALLOWED
