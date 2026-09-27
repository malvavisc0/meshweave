"""Tests for the v1 API (webapp.routers.api_v1) and the durable crawl queue.

Covers the plan's Track-D invariants: per-URL bulk admission (cooldown,
quota, invalid, batch cap), batch-atomic quota accounting, Bearer-only
auth, owner scoping (404 not 403 for other users' resources), the
unbranded report export, and diff honesty (predicted per fix, observed
per lens).
"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(), "apiv1.db"))

from fastapi_stub import (
    install_fastapi_stub,
    install_prometheus_stub,
    load_api_v1_module,
)

install_prometheus_stub()
install_fastapi_stub()

from webapp.models import ApiKey, Base, Crawl, ScoreSnapshot, User  # noqa: E402

api_v1 = load_api_v1_module()

HTTPException = sys.modules["fastapi"].HTTPException
Request = sys.modules["fastapi"].Request

from meshweave.scoring.composite import SCORING_VERSION  # noqa: E402


@pytest.fixture
def sessions():
    """In-memory DB with get_session patched into every module under test.

    The API modules call ``get_session()`` from ``webapp.db``; the fixture
    redirects those references to the in-memory engine so the endpoint code
    and the test share one database.
    """
    from contextlib import contextmanager

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
    def get_session():
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
    import webapp.services.crawl_queue as queue_mod
    import webapp.services.funnel as funnel_mod
    import webapp.utils.diff as diff_mod
    import webapp.utils.quotas as quotas_mod

    api_mod = sys.modules["webapp.routers.api"]

    from webapp.services import api_usage as usage_mod

    patched = [
        db_mod,
        quotas_mod,
        queue_mod,
        funnel_mod,
        diff_mod,
        api_mod,
        usage_mod,
        api_v1,
    ]
    originals = {m: m.get_session for m in patched}
    for m in patched:
        m.get_session = get_session
    # Deterministic DNS for the SSRF guard: names resolve to a public IP.
    import webapp.utils.url as url_mod

    _orig_getaddrinfo = url_mod.socket.getaddrinfo

    def _fake_getaddrinfo(host, *a, **k):
        if host and host.endswith(".internal"):
            raise OSError("no dns")
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    url_mod.socket.getaddrinfo = _fake_getaddrinfo
    yield engine, factory
    url_mod.socket.getaddrinfo = _orig_getaddrinfo
    for m, orig in originals.items():
        m.get_session = orig


def _user_with_key(factory, email: str) -> tuple[str, str]:
    """Create a user + API key; return (user_id, raw_token)."""
    import hashlib

    uid = str(uuid.uuid4())
    token = "mw_test_" + uuid.uuid4().hex
    with factory() as s:
        s.add(
            User(id=uid, email=email, provider="google", provider_id=str(uuid.uuid4()))
        )
        s.add(
            ApiKey(
                id=str(uuid.uuid4()),
                user_id=uid,
                name="test",
                key_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                key_prefix=token[:16],
            )
        )
        s.commit()
    return uid, token


def _bearer_request(token: str | None, body: dict | None = None):
    import json as _json

    headers = {"authorization": f"Bearer {token}"} if token else {}
    raw = _json.dumps(body or {}).encode()
    return Request(headers=headers, body=raw)


def _crawl(
    factory,
    *,
    user_id,
    domain="x.com",
    status="succeeded",
    path="/",
    query="",
    params=None,
    minutes_ago=120,
    scoring_version=SCORING_VERSION,
):
    ts = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    cid = str(uuid.uuid4())
    with factory() as s:
        s.add(
            Crawl(
                id=cid,
                url=f"https://{domain}{path}",
                domain=domain,
                path=path,
                query=query,
                canonical_url=f"https://{domain}{path}",
                visibility="private",
                status=status,
                user_id=user_id,
                crawl_params=params,
                payload_json={"ok": True} if status == "succeeded" else None,
                scoring_version=scoring_version,
                created_at=ts,
                updated_at=ts,
            )
        )
        s.commit()
    return cid


class TestAuth:
    @pytest.mark.asyncio
    async def test_missing_key_rejected(self, sessions):
        with pytest.raises(HTTPException) as exc:
            await api_v1.list_analyses(_bearer_request(None))
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_bad_key_rejected(self, sessions):
        _user_with_key(sessions[1], "a@b.c")
        with pytest.raises(HTTPException) as exc:
            await api_v1.list_analyses(_bearer_request("mw_wrong"))
        assert exc.value.status_code == 401


class TestBulkSubmit:
    @pytest.mark.asyncio
    async def test_batch_cap(self, sessions):
        _, token = _user_with_key(sessions[1], "a@b.c")
        body = {"urls": [f"https://d{i}.com" for i in range(26)], "scope": "page"}
        with pytest.raises(HTTPException) as exc:
            await api_v1.create_analyses(_bearer_request(token, body))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_per_url_outcomes(self, sessions):
        factory = sessions[1]
        uid, token = _user_with_key(factory, "a@b.c")
        # Recent crawl on recent.com → cooldown for the same target
        _crawl(factory, user_id=uid, domain="recent.com", minutes_ago=5)
        body = {
            "urls": ["https://fresh.com", "not-a-url", "https://recent.com"],
            "scope": "page",
        }
        resp = await api_v1.create_analyses(_bearer_request(token, body))
        assert resp.status_code == 201
        by_url = {i["url"]: i for i in resp.content["items"]}
        assert by_url["https://fresh.com"]["status"] == "queued"
        assert by_url["https://fresh.com"]["crawl_id"]
        assert by_url["not-a-url"]["status"] == "invalid"
        assert by_url["https://recent.com"]["status"] == "cooldown"
        # The queued crawl is owned, private, and queue-managed
        with factory() as s:
            row = s.get(Crawl, by_url["https://fresh.com"]["crawl_id"])
        assert row.user_id == uid
        assert row.visibility == "private"
        assert row.queue_status == "pending"

    @pytest.mark.asyncio
    async def test_batch_atomic_quota(self, sessions, monkeypatch):
        """A batch can't sneak past the concurrent limit one row at a time."""
        factory = sessions[1]
        uid, token = _user_with_key(factory, "a@b.c")
        monkeypatch.setenv("AUTH_USER_CONCURRENT_JOBS", "2")
        body = {
            "urls": [f"https://d{i}.com" for i in range(5)],
            "scope": "page",
        }
        resp = await api_v1.create_analyses(_bearer_request(token, body))
        statuses = [i["status"] for i in resp.content["items"]]
        assert statuses.count("queued") == 2
        assert statuses.count("quota") == 3

    @pytest.mark.asyncio
    async def test_bulk_retires_previous_latest_row(self, sessions, monkeypatch):
        """A second bulk run past cooldown must leave exactly one is_latest row."""
        factory = sessions[1]
        uid, token = _user_with_key(factory, "a@b.c")
        monkeypatch.setenv("REFRESH_MIN_AGE_MINUTES", "0")
        body = {"urls": ["https://twice.com"], "scope": "page"}
        r1 = await api_v1.create_analyses(_bearer_request(token, body))
        cid1 = r1.content["items"][0]["crawl_id"]
        r2 = await api_v1.create_analyses(_bearer_request(token, body))
        cid2 = r2.content["items"][0]["crawl_id"]
        assert cid1 != cid2
        with factory() as s:
            latest = (
                s.query(Crawl)
                .filter(
                    Crawl.user_id == uid,
                    Crawl.domain == "twice.com",
                    Crawl.is_latest.is_(True),
                )
                .all()
            )
            assert [r.id for r in latest] == [cid2]
            assert s.get(Crawl, cid1).is_latest is False
        # A third submission must still find exactly one row (no 500).
        r3 = await api_v1.create_analyses(_bearer_request(token, body))
        assert r3.status_code == 201

    @pytest.mark.asyncio
    async def test_bulk_retirement_cancels_pending_queue_job(
        self, sessions, monkeypatch
    ):
        """The retired row's queue job is dropped, not crawled behind our back."""
        factory = sessions[1]
        uid, token = _user_with_key(factory, "a@b.c")
        monkeypatch.setenv("REFRESH_MIN_AGE_MINUTES", "0")
        body = {"urls": ["https://queued.com"], "scope": "page"}
        r1 = await api_v1.create_analyses(_bearer_request(token, body))
        cid1 = r1.content["items"][0]["crawl_id"]
        r2 = await api_v1.create_analyses(_bearer_request(token, body))
        assert r2.status_code == 201
        with factory() as s:
            assert s.get(Crawl, cid1).queue_status is None

    @pytest.mark.asyncio
    async def test_internal_target_rejected(self, sessions):
        """SSRF guard: internal and metadata targets are invalid."""
        _, token = _user_with_key(sessions[1], "a@b.c")
        body = {
            "urls": [
                "http://localhost/",
                "http://127.0.0.1/",
                "http://169.254.169.254/",
                "http://10.0.0.5/",
                "http://192.168.1.1/",
            ],
            "scope": "page",
        }
        resp = await api_v1.create_analyses(_bearer_request(token, body))
        statuses = [i["status"] for i in resp.content["items"]]
        assert statuses == ["invalid"] * 5

    @pytest.mark.asyncio
    async def test_non_dict_body_rejected(self, sessions):
        """A JSON list/string body is a 400, not a 500."""
        _, token = _user_with_key(sessions[1], "a@b.c")
        for raw in [b'["https://a.com"]', b'"hello"', b"42"]:
            req = Request(
                headers={"authorization": f"Bearer {token}"},
                body=raw,
            )
            with pytest.raises(HTTPException) as exc:
                await api_v1.create_analyses(req)
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_site_scope_stores_real_params(self, sessions):
        """Site-scope rows carry truthy crawl_params and summarize as site."""
        factory = sessions[1]
        _, token = _user_with_key(factory, "a@b.c")
        body = {"urls": ["https://siteish.com"], "scope": "site"}
        resp = await api_v1.create_analyses(_bearer_request(token, body))
        cid = resp.content["items"][0]["crawl_id"]
        with factory() as s:
            row = s.get(Crawl, cid)
        assert row.crawl_params is not None
        assert row.crawl_params.get("max_pages")
        assert api_v1._crawl_summary(row)["scope"] == "site"

    @pytest.mark.asyncio
    async def test_usage_recorded_per_key(self, sessions):
        """Calls and admitted/rejected URLs land in the daily usage row."""
        factory = sessions[1]
        _, token = _user_with_key(factory, "a@b.c")
        body = {"urls": ["https://ok.com", "not-a-url"], "scope": "page"}
        await api_v1.create_analyses(_bearer_request(token, body))
        await api_v1.list_analyses(_bearer_request(token, None))
        from webapp.models import ApiKey, ApiKeyUsageDaily

        with factory() as s:
            key_id = s.query(ApiKey).first().id
            row = (
                s.query(ApiKeyUsageDaily)
                .filter(ApiKeyUsageDaily.api_key_id == key_id)
                .one()
            )
        assert row.calls == 2
        assert row.urls_admitted == 1
        assert row.urls_rejected == 1


class TestListAndFetch:
    @pytest.mark.asyncio
    async def test_list_is_owner_scoped(self, sessions):
        factory = sessions[1]
        uid, token = _user_with_key(factory, "a@b.c")
        other_uid, _ = _user_with_key(factory, "c@d.e")
        _crawl(factory, user_id=uid, domain="mine.com")
        _crawl(factory, user_id=other_uid, domain="theirs.com")
        resp = await api_v1.list_analyses(_bearer_request(token))
        domains = {i["domain"] for i in resp["items"]}
        assert domains == {"mine.com"}

    @pytest.mark.asyncio
    async def test_fetch_404_for_other_users_crawl(self, sessions):
        factory = sessions[1]
        _, token = _user_with_key(factory, "a@b.c")
        other_uid, _ = _user_with_key(factory, "c@d.e")
        cid = _crawl(factory, user_id=other_uid, domain="theirs.com")
        with pytest.raises(HTTPException) as exc:
            await api_v1.get_analysis(_bearer_request(token), cid)
        assert exc.value.status_code == 404


class TestReportExport:
    @pytest.mark.asyncio
    async def test_report_has_no_meshweave_branding(self, sessions, monkeypatch):
        factory = sessions[1]
        uid, token = _user_with_key(factory, "a@b.c")
        cid = _crawl(factory, user_id=uid)
        resp = await api_v1.get_report_markdown(_bearer_request(token), cid)
        body = resp.content
        assert "MeshWeave" not in body
        assert "meshweave" not in body
        assert "hello@meshweaveai.com" not in body
        # Unbranded header: domain-titled, no dangling brand separator
        assert body.startswith("# Website Audit for AI Agents — x.com")
        # No dangling "contact ." footer
        assert "contact ." not in body
        # Artifact filename names the deliverable
        assert (
            resp.headers["Content-Disposition"]
            == 'attachment; filename="ai-agent-audit-x.com.md"'
        )

    @pytest.mark.asyncio
    async def test_diff_md_filename_names_the_deliverable(self, sessions):
        factory = sessions[1]
        uid, token = _user_with_key(factory, "a@b.c")
        cid = _crawl(factory, user_id=uid)
        resp = await api_v1.get_diff_markdown(_bearer_request(token), cid)
        assert (
            resp.headers["Content-Disposition"]
            == 'attachment; filename="ai-agent-audit-diff-x.com.md"'
        )


class TestDiff:
    @pytest.mark.asyncio
    async def test_diff_declares_granularity(self, sessions):
        factory = sessions[1]
        uid, token = _user_with_key(factory, "a@b.c")
        cid = _crawl(factory, user_id=uid)
        resp = await api_v1.get_diff(_bearer_request(token), cid)
        assert resp["delta_granularity"] == {
            "predicted": "per_fix",
            "observed": "per_lens",
        }

    @pytest.mark.asyncio
    async def test_diff_md_empty_state(self, sessions):
        factory = sessions[1]
        uid, token = _user_with_key(factory, "a@b.c")
        cid = _crawl(factory, user_id=uid)
        resp = await api_v1.get_diff_markdown(_bearer_request(token), cid)
        assert "No previous revision" in resp.content

    @pytest.mark.asyncio
    async def test_cross_version_diff_refused_with_409(self, sessions):
        """Cross-version comparison is a hard refusal, never an annotation."""
        factory = sessions[1]
        uid, token = _user_with_key(factory, "a@b.c")
        old_id = _crawl(
            factory,
            user_id=uid,
            domain="series.com",
            minutes_ago=180,
            scoring_version="1.2",
        )
        new_id = _crawl(
            factory,
            user_id=uid,
            domain="series.com",
            minutes_ago=5,
            scoring_version=SCORING_VERSION,
        )
        with pytest.raises(HTTPException) as exc:
            await api_v1.get_diff(_bearer_request(token), new_id, vs=old_id)
        assert exc.value.status_code == 409
        assert "scoring version" in str(exc.value.detail)
        with pytest.raises(HTTPException) as exc_md:
            await api_v1.get_diff_markdown(_bearer_request(token), new_id, vs=old_id)
        assert exc_md.value.status_code == 409

    @pytest.mark.asyncio
    async def test_cross_version_default_compare_also_refused(self, sessions):
        """The default previous-revision compare refuses too."""
        factory = sessions[1]
        uid, token = _user_with_key(factory, "a@b.c")
        _crawl(
            factory,
            user_id=uid,
            domain="series.com",
            minutes_ago=180,
            scoring_version="1.2",
        )
        new_id = _crawl(
            factory,
            user_id=uid,
            domain="series.com",
            minutes_ago=5,
            scoring_version=SCORING_VERSION,
        )
        with pytest.raises(HTTPException) as exc:
            await api_v1.get_diff(_bearer_request(token), new_id)
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_same_version_diff_carries_no_notes(self, sessions):
        """A comparable pair diffs without any annotation fields."""
        factory = sessions[1]
        uid, token = _user_with_key(factory, "a@b.c")
        old_id = _crawl(factory, user_id=uid, domain="series.com", minutes_ago=180)
        new_id = _crawl(factory, user_id=uid, domain="series.com", minutes_ago=5)
        resp = await api_v1.get_diff(_bearer_request(token), new_id, vs=old_id)
        assert resp["vs_crawl_id"] == old_id
        assert "comparison_notes" not in resp
        assert "notes" not in resp


class TestAnalysisContract:
    def _snapshot_json(self) -> dict:
        return {
            "aeo": {
                "composite": 62.0,
                "rating": "Partially extractable",
                "factors": {
                    "answerability": {
                        "score": 50.0,
                        "weight": 0.35,
                        "auto_measurable": True,
                        "raw": {
                            "answer_support": 0.5,
                            "evidence_coverage": 0.4,
                            "question_count": 2,
                            "questions": [
                                {
                                    "question_id": "offer",
                                    "question": "What does this company offer?",
                                    "answer": "A widget kit",
                                    "verdict": "supported",
                                    "source_pages": ["https://example.com/"],
                                    "missing_facts": [],
                                }
                            ],
                        },
                    },
                    "schema": {
                        "score": 40.0,
                        "weight": 0.3,
                        "auto_measurable": True,
                        "raw": {},
                    },
                },
            },
            "geo": {
                "composite": 55.0,
                "rating": "Fragmented",
                "factors": {
                    "crawl_access": {
                        "score": 70.0,
                        "weight": 0.3,
                        "auto_measurable": True,
                        "raw": {},
                    }
                },
            },
            "aax": {
                "composite": 80.0,
                "rating": "Fluent",
                "factors": {
                    "contactability": {
                        "score": 100.0,
                        "weight": 0.25,
                        "auto_measurable": True,
                        "raw": {},
                    }
                },
            },
            "recommendations": [
                {
                    "factor": "answerability",
                    "pillar": "aeo",
                    "priority": "high",
                    "title": "Fix the audience answer",
                    "detail": "Name the audience",
                    "expected_points": 2.5,
                }
            ],
        }

    def _scored_crawl(self, factory, user_id: str) -> str:
        cid = _crawl(factory, user_id=user_id, domain="contract.com", minutes_ago=30)
        with factory() as s:
            row = s.get(Crawl, cid)
            row.aeo_score = 62.0
            row.geo_score = 55.0
            row.aeo_rating = "Partially extractable"
            row.geo_rating = "Fragmented"
            s.add(
                ScoreSnapshot(
                    crawl_id=cid,
                    user_id=user_id,
                    domain="contract.com",
                    aeo_score=62.0,
                    geo_score=55.0,
                    aeo_rating="Partially extractable",
                    geo_rating="Fragmented",
                    score_json=self._snapshot_json(),
                    scoring_version=SCORING_VERSION,
                )
            )
            s.commit()
        return cid

    @pytest.mark.asyncio
    async def test_contract_carries_site_side_model_only(self, sessions):
        import json as _json

        factory = sessions[1]
        uid, token = _user_with_key(factory, "a@b.c")
        cid = self._scored_crawl(factory, uid)
        resp = await api_v1.get_analysis(_bearer_request(token), cid)
        body = resp.content
        assert body["scoring_version"] == SCORING_VERSION
        # Factors with weights and lens ratings
        assert body["scores"]["aeo"]["factors"]["answerability"] == {
            "score": 50.0,
            "weight": 0.35,
        }
        assert body["scores"]["aeo"]["rating"] == "Partially extractable"
        assert body["scores"]["geo"]["factors"]["crawl_access"]["weight"] == 0.3
        assert body["scores"]["aax"]["score"] == 80.0
        # Interpretation text
        assert body["interpretation"]["profile_label"]
        assert body["interpretation"]["headline"]
        # Answerability evidence
        questions = body["answerability"]["questions"]
        assert questions[0]["question_id"] == "offer"
        assert questions[0]["answer"] == "A widget kit"
        assert questions[0]["source_pages"] == ["https://example.com/"]
        assert questions[0]["verdict"] == "supported"
        # No simulation or manual-input fields, no crawl payload internals
        blob = _json.dumps(body)
        for banned in (
            "citation_sim",
            "citation_rate",
            "mention_rate",
            "capture_rate",
            "query_match",
            "voice_rate",
            "score_basis",
            "has_manual_input",
            "manual_input",
            "faqs_optimal",
        ):
            assert banned not in blob
        assert "markdown" not in body
        assert "emails" not in body
