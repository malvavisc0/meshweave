import os


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable.

    Accepts common truthy values: {"1", "true", "yes", "y", "on"} (case-insensitive).

    Args:
        name (str): Environment variable name.
        default (bool, optional): Default value if not set. Defaults to False.

    Returns:
        bool: Parsed boolean value.
    """
    v = os.getenv(name)
    if v is None:
        return default
    v = str(v).strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _is_dev_environment() -> bool:
    """True unless the deployment explicitly marks itself non-dev.

    Mirrors the existing ``WEBAPP_REQUIRE_METRICS_AUTH`` /
    ``WEBAPP_READINESS_REQUIRE_OAUTH`` switches: prod compose sets
    ``WEBAPP_ENV=production`` and the fail-closed checks key off it.
    """
    return os.getenv("WEBAPP_ENV", "development").strip().lower() in {
        "development",
        "dev",
        "",
    }


def get_telemetry_config() -> tuple[str, str, bool]:
    """Resolve telemetry script URL, site id, and whether it is enabled.

    Telemetry is disabled unless the operator opts in with
    ENABLE_TELEMETRY=true and configures RYBBIT_SCRIPT_URL and
    RYBBIT_SITE_ID for their own analytics instance. No defaults are
    embedded in the code.

    Returns:
        tuple[str, str, bool]: (script_url, site_id, enabled).
    """
    script_url = os.getenv("RYBBIT_SCRIPT_URL", "").strip()
    site_id = os.getenv("RYBBIT_SITE_ID", "").strip()
    enabled = _env_bool("ENABLE_TELEMETRY", False) and bool(script_url and site_id)
    return script_url, site_id, enabled


def _get_secret_key() -> bytes:
    """Resolve the webapp secret key as bytes.

    Uses WEBAPP_SECRET_KEY or SECRET_KEY. Outside a dev environment a
    missing key is a boot-stopping configuration error: falling
    back to a hard-coded development default in prod would let anyone
    who reads the source forge CSRF tokens.

    Returns:
        bytes: Secret key bytes for HMAC operations.

    Raises:
        RuntimeError: When the key is unset and WEBAPP_ENV marks this a
            non-development deployment.
    """
    key = os.getenv("WEBAPP_SECRET_KEY") or os.getenv("SECRET_KEY") or ""
    if not key:
        if not _is_dev_environment():
            raise RuntimeError(
                "WEBAPP_SECRET_KEY is required outside development "
                "(WEBAPP_ENV != development)"
            )
        key = "dev-secret"
    return key.encode("utf-8")
