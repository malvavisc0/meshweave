"""Launch-blocker regression tests: user-data integrity (workwave 2).

-: the claim flow requires the browser's anonymous-id cookie to match
  — no cookie, no claim; wrong cookie, no claim.
-: retrying a non-latest revision retires the series' actual latest,
  leaving exactly one is_latest row.
-: a quota-rejected bulk re-submit leaves the previous revision and
  its queued job intact.
-: form site rows carry real crawl params (never {}).
-: the fix list sorts by expected points, not priority band.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(), "ww2.db"))

if "prometheus_client" not in sys.modules:
    _fake_prom = types.ModuleType("prometheus_client")

    class _FakeMetric:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def labels(self, *args: object, **kwargs: object) -> _FakeMetric:
            return self

        def inc(self, *args: object, **kwargs: object) -> None:
            pass

        def observe(self, *args: object, **kwargs: object) -> None:
            pass

        def set(self, *args: object, **kwargs: object) -> None:
            pass

    _fake_prom.Counter = _FakeMetric
    _fake_prom.Gauge = _FakeMetric
    _fake_prom.Histogram = _FakeMetric
    _fake_prom.CONTENT_TYPE_LATEST = "text/plain"
    _fake_prom.generate_latest = lambda: b""
    sys.modules["prometheus_client"] = _fake_prom

from fastapi_stub import (
    install_fastapi_stub,
    install_prometheus_stub,
    load_api_v1_module,
)

install_prometheus_stub()
install_fastapi_stub()

api_v1 = load_api_v1_module()  # also loads routers.api + routers.submissions
api_mod = sys.modules["webapp.routers.api"]
submissions = sys.modules["webapp.routers.submissions"]

HTTPException = sys.modules["fastapi"].HTTPException
Request = sys.modules["fastapi"].Request

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from meshweave.scoring.composite import SCORING_VERSION  # noqa: E402
from webapp.models import Base, Crawl, User  # noqa: E402
from webapp.utils.revisions import replace_succeeded_crawl  # noqa: E402


@pytest.fixture
def sessions():
    """In-memory DB shared with every patched module under test."""
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )

    @contextmanager
    def committing_factory():
        """Session scope with get_session semantics (commit on exit)."""
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    import webapp.db as db_mod
    import webapp.services.funnel as funnel_mod

    patched = [db_mod, funnel_mod, api_mod, api_v1, submissions]
    originals = {m: m.get_session for m in patched}
    for m in patched:
        m.get_session = committing_factory
    yield committing_factory
    for m, orig in originals.items():
        m.get_session = orig


def _user(factory, email="u@x.c") -> str:
    uid = str(uuid.uuid4())
    with factory() as s:
        s.add(
            User(id=uid, email=email, provider="google", provider_id=str(uuid.uuid4()))
        )
        s.commit()
    return uid


def _ownerless_public_crawl(
    factory,
    *,
    key="pubk1",
    anon_id="anon_123",
    hours_old=48,
    domain="claimme.com",
) -> str:
    ts = datetime.now(UTC) - timedelta(hours=hours_old)
    cid = str(uuid.uuid4())
    with factory() as s:
        s.add(
            Crawl(
                id=cid,
                url=f"https://{domain}/",
                domain=domain,
                path="/",
                query="",
                canonical_url=f"https://{domain}/",
                key=key,
                visibility="public",
                status="succeeded",
                user_id=None,
                anonymous_user_id=anon_id,
                payload_json={"ok": True},
                scoring_version=SCORING_VERSION,
                is_latest=True,
                created_at=ts,
                updated_at=ts,
            )
        )
        s.commit()
    return cid


def _cookie_request(cookies: dict, auth_user=None) -> Request:
    req = Request(headers={}, body=b"")
    req.cookies = cookies
    req.state.current_user = auth_user
    return req


class _FakeUser:
    def __init__(self, uid: str) -> None:
        self.id = uid


class TestClaimCookieProof:
    """: claiming requires proof this browser ran the analysis."""

    def _authed_request(self, sessions, monkeypatch, cookies, uid):
        monkeypatch.setenv("WEBAPP_CSRF_ENABLED", "false")
        fake = _FakeUser(uid)

        async def _fake_require_auth(request):
            return fake

        monkeypatch.setattr(api_mod, "require_auth", _fake_require_auth)
        return _cookie_request(cookies)

    @pytest.mark.asyncio
    async def test_claim_without_cookie_rejected(self, sessions, monkeypatch):
        uid = _user(sessions)
        _ownerless_public_crawl(sessions)
        req = self._authed_request(sessions, monkeypatch, {}, uid)
        resp = await api_mod.claim_public(req, "pubk1")
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_claim_with_wrong_cookie_rejected(self, sessions, monkeypatch):
        uid = _user(sessions)
        _ownerless_public_crawl(sessions, anon_id="anon_right")
        req = self._authed_request(
            sessions, monkeypatch, {"mw_anon_id": "anon_wrong"}, uid
        )
        resp = await api_mod.claim_public(req, "pubk1")
        assert resp.status_code == 409
        with sessions() as s:
            assert s.query(Crawl).filter(Crawl.user_id.isnot(None)).count() == 0

    @pytest.mark.asyncio
    async def test_claim_with_matching_cookie_claims(self, sessions, monkeypatch):
        monkeypatch.setenv("CLAIM_PUBLIC_MIN_AGE_HOURS", "24")
        uid = _user(sessions)
        _ownerless_public_crawl(sessions, anon_id="anon_right")
        req = self._authed_request(
            sessions, monkeypatch, {"mw_anon_id": "anon_right"}, uid
        )
        resp = await api_mod.claim_public(req, "pubk1")
        assert resp["ok"] is True
        with sessions() as s:
            row = s.query(Crawl).filter(Crawl.key == "pubk1").one()
            assert row.user_id == uid


def _succeeded(
    factory, user_id, *, domain="series.com", is_latest=True, created_at=None
):
    ts = created_at or datetime.now(UTC) - timedelta(days=2)
    cid = str(uuid.uuid4())
    with factory() as s:
        s.add(
            Crawl(
                id=cid,
                url=f"https://{domain}/",
                domain=domain,
                path="/",
                query="",
                canonical_url=f"https://{domain}/",
                visibility="private",
                status="succeeded",
                user_id=user_id,
                payload_json={"markdown": "x"},
                scoring_version=SCORING_VERSION,
                is_latest=is_latest,
                created_at=ts,
                updated_at=ts,
            )
        )
        s.commit()
    return cid


class TestNonLatestRetry:
    """: retrying an old revision keeps exactly one latest row."""

    def test_retry_of_old_revision_retires_actual_latest(self, sessions):
        uid = _user(sessions)
        old = _succeeded(sessions, uid, is_latest=False)
        current = _succeeded(
            sessions,
            uid,
            is_latest=True,
            created_at=datetime.now(UTC) - timedelta(hours=1),
        )

        now = datetime.now(UTC)
        with sessions() as s:
            new_id = replace_succeeded_crawl(s, old, now)
        assert new_id is not None

        with sessions() as s:
            latest = (
                s.query(Crawl)
                .filter(Crawl.domain == "series.com", Crawl.is_latest.is_(True))
                .all()
            )
            assert [r.id for r in latest] == [new_id]
            assert s.get(Crawl, current).is_latest is False


class TestFormSiteLimits:
    """: form site rows carry real crawl params."""

    def test_parse_site_limits_fills_defaults_for_anonymous(self):

        submissions = sys.modules["webapp.routers.submissions"]
        lim = submissions._parse_site_limits(None, None, None, is_authenticated=False)
        assert lim != {}
        assert set(lim) == {"max_pages", "max_depth", "time_budget_ms"}
        assert lim["max_pages"] > 0

    def test_parse_site_limits_fills_auth_defaults(self, monkeypatch):
        submissions = sys.modules["webapp.routers.submissions"]
        monkeypatch.setenv("AUTH_SITE_MAX_PAGES_DEFAULT", "25")
        lim = submissions._parse_site_limits(None, None, None, is_authenticated=True)
        assert lim["max_pages"] == 25


class TestSiteSubmitLogsSubmissionRow:
    """: site submits land in the limiter's data source.

    The anonymous rate limiter counts Submission rows; a site submit
    that writes none is invisible to the only anonymous throttle.
    """

    def test_site_submit_writes_submission_row(self, sessions, monkeypatch):
        from webapp.models import Submission

        monkeypatch.setenv("WEBAPP_LOG_REQUESTS", "true")
        cid = str(uuid.uuid4())
        req = _cookie_request({})
        submissions._maybe_log_site_submission(req, cid, "siteform.com", "public")
        with sessions() as s:
            row = s.query(Submission).filter(Submission.crawl_id == cid).one_or_none()
        assert row is not None
        assert row.domain == "siteform.com"
        assert row.visibility == "public"

    def test_site_submit_respects_logging_disabled(self, sessions, monkeypatch):
        from webapp.models import Submission

        monkeypatch.setenv("WEBAPP_LOG_REQUESTS", "false")
        cid = str(uuid.uuid4())
        req = _cookie_request({})
        submissions._maybe_log_site_submission(req, cid, "siteform.com", "private")
        with sessions() as s:
            row = s.query(Submission).filter(Submission.crawl_id == cid).one_or_none()
        assert row is None


class TestFixListOrder:
    """: expected points drive the sort; band stays display-only."""

    def test_high_points_low_band_outranks_low_points_high_band(self):
        from webapp.utils.scoring import _sorted_recommendations

        ss = {
            "recommendations": [
                {
                    "factor": "a",
                    "pillar": "aeo",
                    "priority": "high",
                    "title": "tiny high-band fix",
                    "expected_points": 3.0,
                },
                {
                    "factor": "b",
                    "pillar": "geo",
                    "priority": "low",
                    "title": "big low-band fix",
                    "expected_points": 12.0,
                },
            ]
        }
        ordered = _sorted_recommendations(ss)
        assert [r["title"] for r in ordered] == [
            "big low-band fix",
            "tiny high-band fix",
        ]

    def test_real_zero_scores_last_not_first(self):
        from webapp.utils.scoring import _sorted_recommendations

        ss = {
            "recommendations": [
                {
                    "factor": "a",
                    "pillar": "aeo",
                    "priority": "medium",
                    "title": "zero",
                    "expected_points": 0.0,
                },
                {
                    "factor": "b",
                    "pillar": "geo",
                    "priority": "medium",
                    "title": "some",
                    "expected_points": 1.5,
                },
            ]
        }
        ordered = _sorted_recommendations(ss)
        assert [r["title"] for r in ordered] == ["some", "zero"]


def _succeeded_in_scope(factory, user_id, *, crawl_params, is_latest=True):
    ts = datetime.now(UTC) - timedelta(days=2)
    cid = str(uuid.uuid4())
    with factory() as s:
        s.add(
            Crawl(
                id=cid,
                url="https://scoped.com/",
                domain="scoped.com",
                path="/",
                query="",
                canonical_url="https://scoped.com/",
                visibility="private",
                status="succeeded",
                user_id=user_id,
                crawl_params=crawl_params,
                payload_json={"markdown": "x"},
                scoring_version=SCORING_VERSION,
                is_latest=is_latest,
                created_at=ts,
                updated_at=ts,
            )
        )
        s.commit()
    return cid


class TestSeriesScopeIsolation:
    """: page and site runs of the same URL are two series.

    A page re-run of the domain root must never retire the site-scope
    latest (and vice versa) — the series key includes scope.
    """

    def test_private_page_lookup_ignores_site_row(self, sessions):
        uid = _user(sessions)
        site_id = _succeeded_in_scope(
            sessions, uid, crawl_params={"max_pages": 5}, is_latest=True
        )
        with sessions() as s:
            found = submissions._find_latest_crawl(
                s, "private", "scoped.com", "/", "", uid, scope_site=False
            )
        assert found is None or found.id != site_id

    def test_private_page_lookup_ignores_page_row_for_site(self, sessions):
        uid = _user(sessions)
        page_id = _succeeded_in_scope(sessions, uid, crawl_params=None, is_latest=True)
        with sessions() as s:
            found = submissions._find_latest_crawl(
                s, "private", "scoped.com", "/", "", uid, scope_site=True
            )
        assert found is None or found.id != page_id

    def test_bulk_admit_page_scope_ignores_site_latest(self, sessions):
        uid = _user(sessions)
        site_id = _succeeded_in_scope(
            sessions, uid, crawl_params={"max_pages": 5}, is_latest=True
        )
        with sessions() as s:
            found = api_v1._find_latest_crawl(
                s, "private", "scoped.com", "/", "", uid, scope_site=False
            )
        assert found is None or found.id != site_id

    def test_bulk_admit_site_scope_ignores_page_latest(self, sessions):
        uid = _user(sessions)
        page_id = _succeeded_in_scope(sessions, uid, crawl_params=None, is_latest=True)
        with sessions() as s:
            found = api_v1._find_latest_crawl(
                s, "private", "scoped.com", "/", "", uid, scope_site=True
            )
        assert found is None or found.id != page_id


class TestVisibilityToggleRetiresTargetSeries:
    """: toggling visibility keeps the one-latest invariant.

    Moving a private row into the public series (or back) must retire
    that series' current latest first; otherwise the toggle mints a
    double-latest series.
    """

    def test_make_public_retires_public_series_latest(self, sessions):
        from webapp.utils.revisions import retire_series_latest

        uid = _user(sessions)
        ts = datetime.now(UTC) - timedelta(hours=1)
        pub_id = str(uuid.uuid4())
        priv_id = str(uuid.uuid4())
        with sessions() as s:
            s.add(
                Crawl(
                    id=pub_id,
                    url="https://toggle.com/",
                    domain="toggle.com",
                    path="/",
                    query="",
                    canonical_url="https://toggle.com/",
                    key="tog1",
                    visibility="public",
                    status="succeeded",
                    user_id=None,
                    scoring_version=SCORING_VERSION,
                    is_latest=True,
                    created_at=ts,
                    updated_at=ts,
                )
            )
            s.add(
                Crawl(
                    id=priv_id,
                    url="https://toggle.com/",
                    domain="toggle.com",
                    path="/",
                    query="",
                    canonical_url="https://toggle.com/",
                    visibility="private",
                    status="succeeded",
                    user_id=uid,
                    scoring_version=SCORING_VERSION,
                    is_latest=True,
                    created_at=ts,
                    updated_at=ts,
                )
            )
            s.commit()
            moving = s.get(Crawl, priv_id)
            retire_series_latest(s, moving, visibility="public")
            moving.visibility = "public"
            s.commit()

        with sessions() as s:
            latest_public = (
                s.query(Crawl)
                .filter(
                    Crawl.visibility == "public",
                    Crawl.domain == "toggle.com",
                    Crawl.is_latest.is_(True),
                )
                .all()
            )
            assert [r.id for r in latest_public] == [priv_id]
            assert s.get(Crawl, pub_id).is_latest is False

    def test_toggle_same_visibility_is_a_noop(self, sessions):
        """Retiring the row's own series must never retire the row itself."""
        from webapp.utils.revisions import retire_series_latest

        uid = _user(sessions)
        priv_id = _succeeded(sessions, uid)
        with sessions() as s:
            row = s.get(Crawl, priv_id)
            retire_series_latest(s, row)
            s.commit()
        with sessions() as s:
            assert s.get(Crawl, priv_id).is_latest is True


class TestQuotaRejectPreservesRevision:
    """: a quota-rejected bulk re-submit leaves the previous revision
    and its queued job intact."""

    def test_quota_rejected_resubmit_preserves_latest_and_queue_job(
        self, sessions, monkeypatch
    ):
        import webapp.utils.url as url_mod

        uid = _user(sessions)
        ts = datetime.now(UTC) - timedelta(days=1)
        prev_id = str(uuid.uuid4())
        with sessions() as s:
            s.add(
                Crawl(
                    id=prev_id,
                    url="https://quota.com/",
                    domain="quota.com",
                    path="/",
                    query="",
                    canonical_url="https://quota.com/",
                    visibility="private",
                    status="succeeded",
                    user_id=uid,
                    payload_json={"markdown": "x"},
                    scoring_version=SCORING_VERSION,
                    is_latest=True,
                    queue_status="pending",
                    created_at=ts,
                    updated_at=ts,
                )
            )
            s.commit()

        monkeypatch.setattr(
            url_mod.socket,
            "getaddrinfo",
            lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))],
        )
        monkeypatch.setattr(api_v1, "_count_user_concurrent_jobs", lambda u: 5)
        monkeypatch.setattr(api_v1, "_concurrent_jobs_limit", lambda: 5)
        monkeypatch.setattr(api_v1, "_count_user_daily_site_crawls", lambda u: 0)
        monkeypatch.setattr(api_v1, "_daily_site_limit", lambda: 10)
        budget = api_v1._BatchBudget(uid)

        now = datetime.now(UTC)
        with sessions() as s:
            entry = api_v1._admit_one(s, uid, "https://quota.com/", "page", budget, now)
        assert entry["status"] == "quota"
        assert entry["crawl_id"] is None

        with sessions() as s:
            rows = s.query(Crawl).filter(Crawl.domain == "quota.com").all()
            assert [r.id for r in rows] == [prev_id]
            row = rows[0]
            assert row.is_latest is True
            assert row.status == "succeeded"
            assert row.queue_status == "pending"
