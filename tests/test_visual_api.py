"""Tests for the visual CTA assessment API and its persistence.

Covers ``webapp.routers.api_v1_visual`` (POST/GET) and
``webapp.services.visual_audit`` (store-once-per-crawl, claim lifecycle,
export context, panel states). The owner form runner is covered by
``tests/test_owner_visual_runner.py``. The capture itself is
monkeypatched at the service seam — no browser runs in tests.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(), "apiv1.db"))

from api_fixtures import (  # noqa: E402
    bearer_request,
    crawl_row,
    fake_audit,
    user_with_key,
)
from fastapi_stub import load_api_v1_module  # noqa: E402

load_api_v1_module()
api_v1_visual = sys.modules["webapp.routers.api_v1_visual"]

HTTPException = sys.modules["fastapi"].HTTPException

from playwright.async_api import Error as PlaywrightError  # noqa: E402

import webapp.services.visual_audit as visual_audit_mod  # noqa: E402
from webapp.services.visual_audit import (  # noqa: E402
    get_stored,
    panel_context,
    payload_from,
    stored_cta_text,
    visual_export_context,
)

SHIM_PNG = b"\x89PNG\r\n\x1a\nfake"


class TestVisualCapture:
    @pytest.mark.asyncio
    async def test_visual_uses_stored_cta_and_owner_scope(self, sessions, monkeypatch):
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid, domain="voxlynow.com", with_snapshot=True)
        with factory() as s:
            from webapp.models import ScoreSnapshot

            snap = s.query(ScoreSnapshot).filter(ScoreSnapshot.crawl_id == cid).one()
            snap.ai_analysis_json = {
                "aax": {
                    "homepage_comprehension": {"call_to_action": "Start a campaign"}
                }
            }
            s.commit()

        seen = {}

        async def fake_run(url, **kwargs):
            seen["url"] = url
            seen["cta"] = kwargs.get("cta_text")
            return fake_audit()

        monkeypatch.setattr(visual_audit_mod, "run_visual_audit", fake_run)
        resp = await api_v1_visual.create_visual(bearer_request(token), cid)

        assert seen == {
            "url": "https://voxlynow.com/",
            "cta": "Start a campaign",
        }
        assert resp.content["id"] == cid
        assert resp.content["assessment"]["cta_text"] == "Start a campaign"

    @pytest.mark.asyncio
    async def test_visual_404_for_other_users_crawl(self, sessions):
        factory = sessions[1]
        _, token = user_with_key(factory, "a@b.c")
        other_uid, _ = user_with_key(factory, "c@d.e")
        cid = crawl_row(factory, user_id=other_uid, domain="voxlynow.com")
        with pytest.raises(HTTPException) as exc:
            await api_v1_visual.create_visual(bearer_request(token), cid)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_visual_409_for_unfinished_crawl(self, sessions):
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid, domain="voxlynow.com", status="running")
        with pytest.raises(HTTPException) as exc:
            await api_v1_visual.create_visual(bearer_request(token), cid)
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_visual_400_for_internal_target_url(self, sessions):
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid, domain="169.254.169.254")
        with pytest.raises(HTTPException) as exc:
            await api_v1_visual.create_visual(bearer_request(token), cid)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_visual_returns_503_when_capture_is_unavailable(
        self, sessions, monkeypatch
    ):
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid, domain="voxlynow.com")

        async def failing(url, **kwargs):
            raise RuntimeError("MESHWEAVE_VISUAL_CDP_ENDPOINT is not set")

        monkeypatch.setattr(visual_audit_mod, "run_visual_audit", failing)
        with pytest.raises(HTTPException) as exc:
            await api_v1_visual.create_visual(bearer_request(token), cid)
        assert exc.value.status_code == 503
        assert "not available" in exc.value.detail

    @pytest.mark.asyncio
    async def test_visual_returns_502_when_page_fails_to_load(
        self, sessions, monkeypatch
    ):
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid, domain="voxlynow.com")

        async def failing(url, **kwargs):
            raise PlaywrightError("net::ERR_NAME_NOT_RESOLVED")

        monkeypatch.setattr(visual_audit_mod, "run_visual_audit", failing)
        with pytest.raises(HTTPException) as exc:
            await api_v1_visual.create_visual(bearer_request(token), cid)
        assert exc.value.status_code == 502

    @pytest.mark.asyncio
    async def test_visual_images_are_base64(self, sessions, monkeypatch):
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid, domain="voxlynow.com")

        async def fake_run(url, **kwargs):
            return fake_audit(attention=True)

        monkeypatch.setattr(visual_audit_mod, "run_visual_audit", fake_run)
        monkeypatch.setattr(
            visual_audit_mod, "render_overlay", lambda audit: b"overlay"
        )
        resp = await api_v1_visual.create_visual(
            bearer_request(token), cid, images=True
        )
        assert resp.content["screenshot_png"] == "iVBORw0KGgpmYWtl"
        assert resp.content["overlay_png"] == "b3ZlcmxheQ=="

    @pytest.mark.asyncio
    async def test_visual_images_omit_overlay_without_attention(
        self, sessions, monkeypatch
    ):
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid, domain="voxlynow.com")

        async def fake_run(url, **kwargs):
            return fake_audit(attention=False)

        monkeypatch.setattr(visual_audit_mod, "run_visual_audit", fake_run)
        resp = await api_v1_visual.create_visual(
            bearer_request(token), cid, images=True
        )
        assert resp.content["screenshot_png"] == "iVBORw0KGgpmYWtl"
        assert "overlay_png" not in resp.content


class TestVisualPersistence:
    @pytest.mark.asyncio
    async def test_capture_is_stored_and_get_returns_it(self, sessions, monkeypatch):
        """POST stores one audit; GET serves the same numbers back."""
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid, domain="voxlynow.com")

        async def fake_run(url, **kwargs):
            return fake_audit(contrast=2.1)

        monkeypatch.setattr(visual_audit_mod, "run_visual_audit", fake_run)
        post = await api_v1_visual.create_visual(bearer_request(token), cid)
        get = await api_v1_visual.get_visual(bearer_request(token), cid)

        assert post.content["assessment"] == get.content["assessment"]
        assert get.content["assessment"]["contrast_ratio"] == 2.1
        assert get.content["attention_model"] is False

    @pytest.mark.asyncio
    async def test_rerun_replaces_the_stored_audit(self, sessions, monkeypatch):
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid, domain="voxlynow.com")

        async def first(url, **kwargs):
            return fake_audit(contrast=2.1)

        async def second(url, **kwargs):
            return fake_audit(contrast=6.0)

        monkeypatch.setattr(visual_audit_mod, "run_visual_audit", first)
        await api_v1_visual.create_visual(bearer_request(token), cid)
        monkeypatch.setattr(visual_audit_mod, "run_visual_audit", second)
        await api_v1_visual.create_visual(bearer_request(token), cid)

        with factory() as s:
            from webapp.models_visual import VisualAudit

            rows = s.query(VisualAudit).filter(VisualAudit.crawl_id == cid).all()
        assert len(rows) == 1
        get = await api_v1_visual.get_visual(bearer_request(token), cid)
        assert get.content["assessment"]["contrast_ratio"] == 6.0

    @pytest.mark.asyncio
    async def test_get_without_capture_404s_on_succeeded(self, sessions):
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid, domain="voxlynow.com")
        with pytest.raises(HTTPException) as exc:
            await api_v1_visual.get_visual(bearer_request(token), cid)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_without_capture_202s_while_analysis_runs(self, sessions):
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid, domain="voxlynow.com", status="running")
        resp = await api_v1_visual.get_visual(bearer_request(token), cid)
        assert resp.status_code == 202

    @pytest.mark.asyncio
    async def test_stored_audit_feeds_the_export_section(self, sessions, monkeypatch):
        """The stored audit renders as the report.md CTA visual check."""
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid, domain="voxlynow.com")

        async def fake_run(url, **kwargs):
            return fake_audit(attention=True)

        monkeypatch.setattr(visual_audit_mod, "run_visual_audit", fake_run)
        monkeypatch.setattr(visual_audit_mod, "render_overlay", lambda audit: b"ov")
        await api_v1_visual.create_visual(bearer_request(token), cid)

        with factory() as s:
            stored = get_stored(s, cid)
            export = visual_export_context(stored)
        assert export is not None
        assert export["cta_text"] == "Start a campaign"
        assert export["attention_share"] == pytest.approx(0.42)

        from webapp.utils.export import render_export_markdown

        body = render_export_markdown(
            {
                "site_name": "",
                "domain": "voxlynow.com",
                "canonical_url": "https://voxlynow.com/",
                "scope": "page",
                "report_date": "2026-11-27",
                "scores": {},
                "interpretation": {},
                "answerability": [],
                "visual": export,
                "recommendations": [],
                "consultation_email": "",
            }
        )
        assert "CTA Visual Check" in body
        assert "Start a campaign" in body
        assert "42.0%" in body

    @pytest.mark.asyncio
    async def test_export_section_absent_without_capture(self, sessions):
        factory = sessions[1]
        uid, _ = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid, domain="voxlynow.com")
        with factory() as s:
            assert visual_export_context(get_stored(s, cid)) is None

    @pytest.mark.asyncio
    async def test_panel_context_tracks_capture_state(self, sessions, monkeypatch):
        """Panel: never run → can run; captured → cooldown then re-run."""
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid, domain="voxlynow.com")

        async def fake_run(url, **kwargs):
            return fake_audit(attention=False)

        monkeypatch.setattr(visual_audit_mod, "run_visual_audit", fake_run)
        with factory() as s:
            from webapp.models import Crawl

            row = s.get(Crawl, cid)
            assert row is not None
            before = panel_context(s, row)
            assert before["stored"] is False
            assert before["can_run"] is True

        await api_v1_visual.create_visual(bearer_request(token), cid)

        with factory() as s:
            from webapp.models import Crawl

            row = s.get(Crawl, cid)
            assert row is not None
            after = panel_context(s, row)
            assert after["stored"] is True
            assert after["payload"]["assessment"]["cta_text"] == "Start a campaign"
            assert after["can_run"] is False  # fresh capture → cooldown
            assert after["run_eta"]
            # Force the capture outside the cooldown window.
            from webapp.models_visual import VisualAudit

            stored = s.query(VisualAudit).filter(VisualAudit.crawl_id == cid).one()
            stored.updated_at = datetime.now(UTC) - timedelta(hours=2)
            s.commit()
            row = s.get(Crawl, cid)
            assert row is not None
            later = panel_context(s, row)
            assert later["can_run"] is True


def test_stored_cta_text_reads_homepage_comprehension():
    snapshot = SimpleNamespace(
        ai_analysis_json={
            "aax": {"homepage_comprehension": {"call_to_action": "Start a campaign"}}
        }
    )
    row = SimpleNamespace(score_snapshot=snapshot)
    assert stored_cta_text(row) == "Start a campaign"


def test_stored_cta_text_none_without_analysis():
    assert stored_cta_text(SimpleNamespace(score_snapshot=None)) is None
    empty = SimpleNamespace(ai_analysis_json=None)
    assert stored_cta_text(SimpleNamespace(score_snapshot=empty)) is None


def test_payload_from_stored_row_shape():
    stored = SimpleNamespace(
        crawl_id="cid-1",
        url="https://voxlynow.com/",
        viewport={"width": 1366, "height": 768},
        element_count=7,
        screenshot_png=SHIM_PNG,
        attention_model=False,
        assessment_json={"cta_text": "Go"},
        capture_version="1",
    )
    payload = payload_from(stored)
    assert payload == {
        "id": "cid-1",
        "url": "https://voxlynow.com/",
        "viewport": {"width": 1366, "height": 768},
        "element_count": 7,
        "screenshot_bytes": len(SHIM_PNG),
        "attention_model": False,
        "assessment": {"cta_text": "Go"},
        "capture_version": "1",
    }


@pytest.mark.asyncio
async def test_owner_claim_refuses_rerun_inside_cooldown(sessions, monkeypatch):
    """A replayed submit inside the cooldown is refused, not re-captured."""
    import webapp.db as db_mod

    factory = sessions[1]
    uid, _token = user_with_key(factory, "a@b.c")
    cid = crawl_row(factory, user_id=uid, domain="voxlynow.com")

    monkeypatch.setattr(visual_audit_mod, "get_session", db_mod.get_session)

    assert visual_audit_mod.claim_run(cid) is True
    assert visual_audit_mod.claim_run(cid) is False
    from webapp.services.visual_audit import store_audit

    with db_mod.get_session() as s:
        from webapp.models import Crawl

        row = s.get(Crawl, cid)
        store_audit(s, row, fake_audit())
    assert visual_audit_mod.claim_run(cid) is False
    with factory() as s:
        assert get_stored(s, cid) is not None


@pytest.mark.asyncio
async def test_failed_capture_releases_the_claim(sessions, monkeypatch):
    """A failed capture frees the slot so the owner can retry immediately."""
    factory = sessions[1]
    uid, _token = user_with_key(factory, "a@b.c")
    import webapp.db as db_mod

    cid = crawl_row(factory, user_id=uid, domain="voxlynow.com")
    monkeypatch.setattr(visual_audit_mod, "get_session", db_mod.get_session)

    assert visual_audit_mod.claim_run(cid) is True
    with factory() as s:
        assert get_stored(s, cid) is None
    visual_audit_mod.release_run(cid)
    assert visual_audit_mod.claim_run(cid) is True


@pytest.mark.asyncio
async def test_failed_rerun_keeps_the_previous_audit(sessions, monkeypatch):
    """A failed re-run must not destroy the last successful capture.

    Regression: the claim used to overwrite the stored screenshot with a
    marker and the release deleted the row, so a timed-out re-run wiped
    the previous audit the panel promises to keep showing.
    """
    factory = sessions[1]
    uid, token = user_with_key(factory, "a@b.c")
    cid = crawl_row(factory, user_id=uid, domain="voxlynow.com")

    async def first(url, **kwargs):
        return fake_audit(contrast=2.1)

    monkeypatch.setattr(visual_audit_mod, "run_visual_audit", first)
    await api_v1_visual.create_visual(bearer_request(token), cid)

    with factory() as s:
        from webapp.models_visual import VisualAudit

        stored = s.query(VisualAudit).filter(VisualAudit.crawl_id == cid).one()
        stored.updated_at = datetime.now(UTC) - timedelta(hours=2)
        s.commit()

    import webapp.db as db_mod

    monkeypatch.setattr(visual_audit_mod, "get_session", db_mod.get_session)
    assert visual_audit_mod.claim_run(cid) is True

    # The previous audit stays readable while the re-run is in flight.
    with factory() as s:
        in_flight = get_stored(s, cid)
        assert in_flight is not None

    # The capture fails; the claim is released and the audit survives.
    visual_audit_mod.release_run(cid)
    with factory() as s:
        survived = get_stored(s, cid)
        assert survived is not None
        assert survived.assessment_json["contrast_ratio"] == 2.1
    # The owner can retry immediately.
    assert visual_audit_mod.claim_run(cid) is True


@pytest.mark.asyncio
async def test_crashed_claim_recovers_after_the_cooldown_window(sessions, monkeypatch):
    """A claim left by a crashed capture is reclaimable once it ages out."""
    factory = sessions[1]
    uid, _token = user_with_key(factory, "a@b.c")
    import webapp.db as db_mod

    cid = crawl_row(factory, user_id=uid, domain="voxlynow.com")
    monkeypatch.setattr(visual_audit_mod, "get_session", db_mod.get_session)

    assert visual_audit_mod.claim_run(cid) is True
    assert visual_audit_mod.claim_run(cid) is False  # live claim refuses

    # Simulate a crashed capture: the claim is never released and ages out.
    with factory() as s:
        from webapp.models_visual import VisualAudit

        marker = s.query(VisualAudit).filter(VisualAudit.crawl_id == cid).one()
        marker.claimed_at = datetime.now(UTC) - timedelta(hours=2)
        marker.updated_at = datetime.now(UTC) - timedelta(hours=2)
        s.commit()
    assert visual_audit_mod.claim_run(cid) is True
