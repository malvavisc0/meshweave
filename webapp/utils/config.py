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


def _get_secret_key() -> bytes:
    """Resolve the webapp secret key as bytes.

    Uses WEBAPP_SECRET_KEY or SECRET_KEY; falls back to a development default.

    Returns:
        bytes: Secret key bytes for HMAC operations.
    """
    key = os.getenv("WEBAPP_SECRET_KEY") or os.getenv("SECRET_KEY") or ""
    if not key:
        key = "dev-secret"
    return key.encode("utf-8")
