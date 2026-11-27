"""Tests for the v1 report and diff exports (webapp.routers.api_v1_reports).

Moved from tests/test_api_v1.py when the export handlers were split out of
the over-cap v1 module.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(), "apiv1.db"))

from api_fixtures import bearer_request, crawl_row, user_with_key  # noqa: E402
from fastapi_stub import load_api_v1_module  # noqa: E402

load_api_v1_module()
api_v1_reports = sys.modules["webapp.routers.api_v1_reports"]

HTTPException = sys.modules["fastapi"].HTTPException

from meshweave.scoring.composite import SCORING_VERSION  # noqa: E402


class TestReportExport:
    @pytest.mark.asyncio
    async def test_report_has_no_meshweave_branding(self, sessions):
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid, with_snapshot=True)
        resp = await api_v1_reports.get_report_markdown(bearer_request(token), cid)
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
    async def test_report_gates_on_readiness_like_browser_export(self, sessions):
        """W7: no snapshot / pending AAX → 409, not a thin client-ready report."""
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid)  # succeeded, but no snapshot/AAX
        with pytest.raises(HTTPException) as exc:
            await api_v1_reports.get_report_markdown(bearer_request(token), cid)
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_diff_md_filename_names_the_deliverable(self, sessions):
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid)
        resp = await api_v1_reports.get_diff_markdown(bearer_request(token), cid)
        assert (
            resp.headers["Content-Disposition"]
            == 'attachment; filename="ai-agent-audit-diff-x.com.md"'
        )


class TestDiff:
    @pytest.mark.asyncio
    async def test_diff_declares_granularity(self, sessions):
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid)
        resp = await api_v1_reports.get_diff(bearer_request(token), cid)
        assert resp["delta_granularity"] == {
            "predicted": "per_fix",
            "observed": "per_lens",
        }

    @pytest.mark.asyncio
    async def test_diff_md_empty_state(self, sessions):
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        cid = crawl_row(factory, user_id=uid)
        resp = await api_v1_reports.get_diff_markdown(bearer_request(token), cid)
        assert "No previous revision" in resp.content

    @pytest.mark.asyncio
    async def test_cross_version_diff_refused_with_409(self, sessions):
        """Cross-version comparison is a hard refusal, never an annotation."""
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        old_id = crawl_row(
            factory,
            user_id=uid,
            domain="series.com",
            minutes_ago=180,
            scoring_version="1.2",
            is_latest=False,
        )
        new_id = crawl_row(
            factory,
            user_id=uid,
            domain="series.com",
            minutes_ago=5,
            scoring_version=SCORING_VERSION,
        )
        with pytest.raises(HTTPException) as exc:
            await api_v1_reports.get_diff(bearer_request(token), new_id, vs=old_id)
        assert exc.value.status_code == 409
        assert "scoring version" in str(exc.value.detail)
        with pytest.raises(HTTPException) as exc_md:
            await api_v1_reports.get_diff_markdown(
                bearer_request(token), new_id, vs=old_id
            )
        assert exc_md.value.status_code == 409

    @pytest.mark.asyncio
    async def test_cross_version_default_compare_also_refused(self, sessions):
        """The default previous-revision compare refuses too."""
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        crawl_row(
            factory,
            user_id=uid,
            domain="series.com",
            minutes_ago=180,
            scoring_version="1.2",
            is_latest=False,
        )
        new_id = crawl_row(
            factory,
            user_id=uid,
            domain="series.com",
            minutes_ago=5,
            scoring_version=SCORING_VERSION,
        )
        with pytest.raises(HTTPException) as exc:
            await api_v1_reports.get_diff(bearer_request(token), new_id)
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_same_version_diff_carries_no_notes(self, sessions):
        """A comparable pair diffs without any annotation fields."""
        factory = sessions[1]
        uid, token = user_with_key(factory, "a@b.c")
        old_id = crawl_row(
            factory,
            user_id=uid,
            domain="series.com",
            minutes_ago=180,
            is_latest=False,
        )
        new_id = crawl_row(factory, user_id=uid, domain="series.com", minutes_ago=5)
        resp = await api_v1_reports.get_diff(bearer_request(token), new_id, vs=old_id)
        assert resp["vs_crawl_id"] == old_id
