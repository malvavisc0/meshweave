"""Launch-blocker regression tests: deployment promises (workwave 4).

Parses the real prod compose file so the config keeps the promises the
privacy policy makes, the proxy fix's env wiring, the CSP and
secret-key fail-closed switches, and the deployment additions the
report missed (log rotation).
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import sys
import tempfile
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(), "ww4.db"))

COMPOSE = Path(__file__).resolve().parent.parent / "docker-compose.prod.yaml"
ROOT = COMPOSE.parent

_ROUTER_NAMES = (
    "all_public",
    "analysis",
    "api",
    "auth",
    "home",
    "jobs",
    "legal",
    "products",
    "profile",
    "prospects",
    "prospects_page",
    "scores",
    "scoring",
    "submissions",
)


class _Stub:
    def __init__(self, *a: object, **k: object) -> None:
        pass


def _ensure_stub(name: str, **attrs: object) -> types.ModuleType:
    """Return sys.modules[name], creating a stub with attrs when absent."""
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    for key, value in attrs.items():
        if not hasattr(mod, key):
            setattr(mod, key, value)
    return mod


def _install_starlette_pieces() -> None:
    """Fill the starlette pieces app.py imports when real starlette is absent."""
    try:
        import starlette.exceptions  # noqa: F401
        import starlette.middleware.gzip  # noqa: F401

        return
    except Exception:
        pass
    st = _ensure_stub("starlette")
    status_mod = _ensure_stub("starlette.status")
    for name, code in (
        ("HTTP_401_UNAUTHORIZED", 401),
        ("HTTP_403_FORBIDDEN", 403),
        ("HTTP_404_NOT_FOUND", 404),
        ("HTTP_422_UNPROCESSABLE_ENTITY", 422),
        ("HTTP_500_INTERNAL_SERVER_ERROR", 500),
    ):
        if not hasattr(status_mod, name):
            setattr(status_mod, name, code)
    st.status = status_mod
    exc = _ensure_stub(
        "starlette.exceptions", HTTPException=type("HTTPException", (Exception,), {})
    )
    st.exceptions = exc
    mw = _ensure_stub("starlette.middleware")
    st.middleware = mw
    gz = _ensure_stub(
        "starlette.middleware.gzip", GZipMiddleware=type("GZipMiddleware", (), {})
    )
    mw.gzip = gz


def _install_app_stubs() -> None:
    """Minimal runtime stubs so webapp/app.py imports without the full stack."""
    from fastapi_stub import install_fastapi_stub, install_prometheus_stub

    install_prometheus_stub()
    install_fastapi_stub()
    _ensure_stub("fastapi", FastAPI=_Stub)
    _ensure_stub(
        "fastapi.exceptions",
        RequestValidationError=type("RequestValidationError", (Exception,), {}),
    )
    _ensure_stub("fastapi.responses", JSONResponse=_Stub, PlainTextResponse=_Stub)
    _install_starlette_pieces()
    _ensure_stub(
        "webapp.infra",
        templates=types.SimpleNamespace(TemplateResponse=lambda *a, **k: None),
        mount_static=lambda *a, **k: None,
    )
    pkg = _ensure_stub("webapp.routers")
    for name in _ROUTER_NAMES:
        if not hasattr(pkg, name):
            loaded = sys.modules.get(f"webapp.routers.{name}")
            setattr(
                pkg, name, loaded if loaded is not None else types.SimpleNamespace()
            )


_APP_MOD = None


def _webapp_app():
    """Load webapp/app.py once under the runtime stubs."""
    global _APP_MOD
    if _APP_MOD is None:
        _install_app_stubs()
        path = ROOT / "webapp" / "app.py"
        spec = importlib.util.spec_from_file_location("webapp.app", path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules["webapp.app"] = mod
        spec.loader.exec_module(mod)
        _APP_MOD = mod
    return _APP_MOD


class _RequestURL:
    """URL whose str() leaks the proxy-internal origin, like request.url."""

    def __init__(self, path: str) -> None:
        self.path = path

    def __str__(self) -> str:
        return f"http://10.0.0.5:8080{self.path}"


def _fake_request(
    base_url: str | None, path: str = "/missing"
) -> types.SimpleNamespace:
    state = types.SimpleNamespace()
    if base_url:
        state.SITE_BASE_URL_OVERRIDE = base_url
    return types.SimpleNamespace(
        app=types.SimpleNamespace(state=state),
        headers={"host": "evil.example"},
        url=_RequestURL(path),
    )


def _csp_response_headers(middleware_cls, **init_kwargs: object) -> dict[str, str]:
    """Run a small ASGI request through the middleware and capture headers."""
    started: list[dict] = []

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        started.append(message)

    async def run() -> None:
        mw = middleware_cls(inner, **init_kwargs)
        await mw(
            {"type": "http", "method": "GET", "path": "/", "headers": []},
            receive,
            send,
        )

    asyncio.run(run())
    start = next(m for m in started if m.get("type") == "http.response.start")
    return {k.decode().lower(): v.decode() for k, v in start.get("headers", [])}


def _webapp_env_block() -> str:
    """Extract the webapp service's block from the compose file."""
    text = COMPOSE.read_text()
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.rstrip() == "  webapp:":
            start = i
            break
    assert start is not None, "webapp service not found"
    block: list[str] = []
    for line in lines[start + 1 :]:
        # A line at two-space indent starts the next top-level key/service
        if line.strip() and not line.startswith("    "):
            break
        block.append(line)
    return "\n".join(block)


def _compose_env() -> dict[str, str]:
    """Parse the webapp service's environment entries into a dict."""
    env: dict[str, str] = {}
    in_env = False
    for line in _webapp_env_block().splitlines():
        stripped = line.strip()
        if stripped.startswith("environment:"):
            in_env = True
            continue
        if in_env:
            if (
                stripped
                and not line.startswith("        ")
                and not line.startswith("      ")
            ):
                break
            m = re.match(r"([A-Z0-9_]+):\s*(.*)$", stripped)
            if m:
                env[m.group(1)] = m.group(2).strip().strip('"')
    return env


def test_compose_file_exists():
    assert COMPOSE.exists()


class TestProdComposePromises:
    def test_trust_proxy_set(self):
        #: the proxy-header fix only works behind the reverse proxy
        # when the variable is wired.
        assert _compose_env().get("WEBAPP_TRUST_PROXY") == "true"

    def test_cookie_secure_and_ip_masking(self):
        #: Secure on every cookie, IP masking on, salt present.
        env = _compose_env()
        assert env.get("WEBAPP_COOKIE_SECURE") == "true"
        assert env.get("WEBAPP_MASK_IP") == "true"
        assert "IP_HASH_SALT" in env

    def test_csp_enabled(self):
        #: CSP was dead config — the switch must be on in prod.
        assert _compose_env().get("WEBAPP_ENABLE_CSP") == "true"

    def test_env_marks_production(self):
        #: gates the dev-secret fallback.
        assert _compose_env().get("WEBAPP_ENV") == "production"

    def test_anon_time_budget_capped(self):
        # /: a free crawl may not hold an hour of shared CDP time.
        env = _compose_env()
        cap = int(env.get("ANON_SITE_TIME_BUDGET_MS_CAP", "0"))
        assert 0 < cap < 3_600_000

    def test_dead_debug_var_removed(self):
        assert "DEBUG" not in _compose_env()

    def test_body_cap_configured(self):
        assert int(_compose_env().get("WEBAPP_BODY_MAX_BYTES", "0")) > 0

    def test_per_key_and_concurrency_limits_configured(self):
        env = _compose_env()
        assert int(env.get("WEBAPP_API_RATE_LIMIT_MAX", "0")) > 0
        assert int(env.get("WEBAPP_MAX_CONCURRENT_CRAWLS", "0")) > 0

    def test_retention_configured(self):
        env = _compose_env()
        assert int(env.get("WEBAPP_SUBMISSION_RETENTION_DAYS", "0")) > 0
        assert int(env.get("WEBAPP_CACHE_RETENTION_DAYS", "0")) > 0

    def test_llm_timeout_configured(self):
        #
        assert int(_compose_env().get("LLM_TEST_TIMEOUT_SECONDS", "0")) > 0

    def test_log_rotation_on_all_services(self):
        text = COMPOSE.read_text()
        # A compose "service" is a top-level mapping with an image key.
        names = re.findall(r"^  (\w+):\n(?=.*image:)", text, re.MULTILINE)
        assert names, "no services found in compose file"
        for svc in names:
            start = None
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if line.rstrip() == f"  {svc}:":
                    start = i
                    break
            assert start is not None, f"service block {svc} not found"
            block: list[str] = []
            for line in lines[start + 1 :]:
                if line.strip() and not line.startswith("    "):
                    break
                block.append(line)
            assert any("max-size" in ln for ln in block), f"{svc} lacks log rotation"


class TestPackaging:
    def test_webapp_shipped_in_package(self):
        """: the installed meshweave CLI needs webapp.db importable."""
        pyproject = (Path(__file__).parent.parent / "pyproject.toml").read_text()
        m = re.search(
            r"\[tool\.setuptools\.packages\.find\]\s*\ninclude\s*=\s*\[(.*?)\]",
            pyproject,
            re.DOTALL,
        )
        assert m, "packages.find include list not found"
        assert '"webapp*"' in m.group(1)

    def test_alembic_available_from_root(self):
        """: `uv run alembic` must work from a root-only install."""
        pyproject = (Path(__file__).parent.parent / "pyproject.toml").read_text()
        m = re.search(
            r"\[dependency-groups\]\s*\ndev\s*=\s*\[(.*?)\]",
            pyproject,
            re.DOTALL,
        )
        assert m, "root dev dependency group not found"
        assert '"alembic"' in m.group(1)


class TestSingleInitialMigration:
    """was resolved by deleting the database: the fresh schema starts
    with zero historical crawls, so no purge migration exists.'s
    series-latest uniqueness lives in the model and the one migration."""

    def test_single_migration_file(self):
        versions = Path(__file__).parent.parent / "alembic" / "versions"
        files = [f for f in versions.glob("*.py") if f.name != "__init__.py"]
        assert len(files) == 1, f"expected one migration, got {files}"

    def test_migration_declares_series_latest_index(self):
        versions = Path(__file__).parent.parent / "alembic" / "versions"
        text = next(versions.glob("*.py")).read_text()
        assert "uq_crawls_series_latest" in text

    def test_model_declares_series_latest_index(self):
        from webapp.models import Crawl

        args = Crawl.__table_args__
        names = [getattr(a, "name", None) for a in args]
        assert "uq_crawls_series_latest" in names


class TestBackupTooling:
    def test_backup_script_present_and_executable(self):
        script = Path(__file__).parent.parent / "scripts" / "backup-postgres.sh"
        assert script.exists()
        assert os.access(script, os.X_OK)

    def test_restore_docs_exist(self):
        assert (Path(__file__).parent.parent / "docs" / "backup-restore.md").exists()

    def test_ci_runs_all_five_gates(self):
        ci = (
            Path(__file__).parent.parent / ".github" / "workflows" / "ci.yml"
        ).read_text()
        for gate in ("ruff check", "ruff format --check", "mypy", "radon", "pytest"):
            assert gate in ci, f"CI lacks the {gate} gate"


class TestAnonLimiterFailClosed:
    """: the anonymous throttle no longer fails open."""

    def test_rate_limiter_raises_on_db_error(self, monkeypatch):
        from fastapi_stub import install_fastapi_stub, install_prometheus_stub

        install_prometheus_stub()
        install_fastapi_stub()
        from fastapi_stub import load_api_v1_module

        load_api_v1_module()  # loads routers.submissions with the stubs
        submissions_mod = sys.modules["webapp.routers.submissions"]

        class _Boom:
            def __enter__(self):
                raise RuntimeError("db down")

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(submissions_mod, "get_session", lambda: _Boom())
        Request = sys.modules["fastapi"].Request

        req = Request(headers={})
        req.cookies = {}
        with pytest.raises(RuntimeError):
            submissions_mod._enforce_rate_limit(req, datetime.now(UTC))


class TestDefaultCSPPolicy:
    """: the built-in default CSP must not break Google Fonts."""

    def test_default_policy_allows_font_and_favicon_origins(self):
        policy = _webapp_app()._default_csp("")
        assert "https://fonts.googleapis.com" in policy
        assert "https://fonts.gstatic.com" in policy
        assert "https://www.google.com" in policy

    def test_middleware_emits_default_csp_header(self, monkeypatch):
        monkeypatch.setenv("WEBAPP_ENABLE_CSP", "true")
        monkeypatch.delenv("WEBAPP_CSP", raising=False)
        headers = _csp_response_headers(_webapp_app().CSPMiddleware)
        policy = headers.get("content-security-policy", "")
        assert policy, "CSP middleware emitted no header"
        assert "https://fonts.googleapis.com" in policy
        assert "https://fonts.gstatic.com" in policy
        assert "https://www.google.com" in policy

    def test_middleware_emits_nothing_when_disabled(self, monkeypatch):
        monkeypatch.delenv("WEBAPP_ENABLE_CSP", raising=False)
        headers = _csp_response_headers(_webapp_app().CSPMiddleware)
        assert "content-security-policy" not in headers

    def test_webapp_csp_override_wins(self, monkeypatch):
        monkeypatch.setenv("WEBAPP_ENABLE_CSP", "true")
        monkeypatch.setenv("WEBAPP_CSP", "default-src 'none'")
        headers = _csp_response_headers(_webapp_app().CSPMiddleware)
        assert headers.get("content-security-policy") == "default-src 'none'"


class TestDatabaseUrlFailClosed:
    """: an empty DATABASE_URL raises in production, SQLite in dev only."""

    def test_empty_url_raises_outside_dev(self, monkeypatch):
        from webapp import db as db_mod

        monkeypatch.setenv("WEBAPP_ENV", "production")
        with pytest.raises(RuntimeError):
            db_mod._fallback_sqlite_url("/tmp/never.db")

    def test_empty_url_falls_back_to_sqlite_in_dev(self, monkeypatch):
        from webapp import db as db_mod

        monkeypatch.setenv("WEBAPP_ENV", "development")
        assert db_mod._fallback_sqlite_url("/tmp/dev.db") == "sqlite:////tmp/dev.db"


class TestErrorPageCanonical:
    """: error pages must not leak the request/proxy host as canonical."""

    def test_error_context_uses_configured_base_url(self):
        request = _fake_request("https://meshweaveai.com")
        _title, _desc, abs_url = _webapp_app()._error_context(
            request, "Page Not Found", "The page you requested was not found."
        )
        assert abs_url == "https://meshweaveai.com/missing"
        assert "evil.example" not in abs_url
        assert "10.0.0.5" not in abs_url


class TestProdContainerNames:
    """: generic container names must not collide with other stacks."""

    def test_container_names_scoped_to_the_project(self):
        names = re.findall(r"container_name:\s*(\S+)", COMPOSE.read_text())
        assert names == [
            "meshweave-prod-postgres",
            "meshweave-prod-lightpanda",
            "meshweave-prod-webapp",
        ]

    def test_service_names_and_db_host_stay_stable(self):
        text = COMPOSE.read_text()
        for svc in ("postgres", "lightpanda", "webapp"):
            assert re.search(rf"^  {svc}:", text, re.M), svc
        assert "postgres:5432" in _compose_env().get("DATABASE_URL", "")


class TestEnvExampleCoversComposeVars:
    def test_every_compose_interpolation_is_documented(self):
        example = (ROOT / ".env.prod.example").read_text()
        names = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", COMPOSE.read_text()))
        documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", example, re.M))
        missing = sorted(names - documented)
        assert not missing, f".env.prod.example misses {missing}"
