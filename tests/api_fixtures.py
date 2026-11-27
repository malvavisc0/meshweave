"""Shared row and audit builders for the webapp API tests.

The ``sessions`` fixture lives in ``tests/conftest.py`` so pytest
discovers it without imports; the builders are imported per module.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from meshweave.scoring.composite import SCORING_VERSION
from webapp.models import ApiKey, Crawl, ScoreSnapshot, User

SHIM_PNG = b"\x89PNG\r\n\x1a\nfake"


def fake_audit(
    *,
    cta: str | None = "Start a campaign",
    matched_by: str = "text",
    attention: bool = False,
    screenshot: bytes = SHIM_PNG,
    contrast: float | None = 4.8,
) -> SimpleNamespace:
    """A minimal stand-in for meshweave.visual_audit.VisualAudit."""
    assessment = SimpleNamespace(
        to_dict=lambda: {
            "cta_text": cta,
            "matched_by": matched_by,
            "contrast_ratio": contrast,
            "contrast_threshold": 4.5,
            "surface_contrast": 4.1,
            "target_px": 36,
            "competing_actions": 1,
            "competing_names": ["Learn more"],
            "attention_share": 0.42 if attention else None,
            "attention_rank": 1 if attention else None,
            "findings": [
                {
                    "code": "text_contrast",
                    "severity": "high",
                    "detail": "CTA label contrast is 2.1:1 (needs 4.5:1).",
                    "fix": "Darken the label.",
                }
            ],
        }
    )
    snapshot = SimpleNamespace(
        final_url="https://voxlynow.com/",
        viewport_width=1366,
        viewport_height=768,
        elements=(),
        screenshot_png=screenshot,
    )
    return SimpleNamespace(
        snapshot=snapshot,
        assessment=assessment,
        attention=SimpleNamespace() if attention else None,
    )


def user_with_key(factory, email: str) -> tuple[str, str]:
    """Create a user + API key; return (user_id, raw_token)."""
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


def bearer_request(token: str | None, body: dict | None = None):
    """Build a stub Request carrying an optional Bearer key and JSON body."""
    import json as _json

    from fastapi_stub import install_fastapi_stub

    install_fastapi_stub()
    from fastapi import Request

    headers = {"authorization": f"Bearer {token}"} if token else {}
    raw = _json.dumps(body or {}).encode()
    return Request(headers=headers, body=raw)


def crawl_row(
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
    with_snapshot=False,
    is_latest=True,
):
    """Insert one crawl row (optionally with a populated snapshot)."""
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
                is_latest=is_latest,
            )
        )
        if with_snapshot:
            # A populated snapshot so export/branding assertions actually
            # scan rendered sections.
            s.add(
                ScoreSnapshot(
                    crawl_id=cid,
                    user_id=user_id,
                    domain=domain,
                    aeo_score=62.0,
                    geo_score=55.0,
                    aeo_rating="Partially extractable",
                    geo_rating="Fragmented",
                    score_json={
                        "aeo": {
                            "composite": 62.0,
                            "rating": "Partially extractable",
                            "factors": {
                                "schema": {"score": 40.0, "weight": 0.2, "raw": {}},
                            },
                        },
                        "geo": {
                            "composite": 55.0,
                            "rating": "Fragmented",
                            "factors": {
                                "crawl_access": {
                                    "score": 70.0,
                                    "weight": 0.3,
                                    "raw": {},
                                },
                            },
                        },
                        "recommendations": [
                            {
                                "factor": "schema",
                                "pillar": "aeo",
                                "priority": "high",
                                "title": "Add structured data to more pages",
                                "detail": "Key pages lack schema markup.",
                                "expected_points": 4.0,
                            }
                        ],
                    },
                    scoring_version=scoring_version,
                )
            )
            s.flush()
            row = s.get(Crawl, cid)
            assert row is not None
            row.aeo_score = 62.0
            row.geo_score = 55.0
            row.aeo_rating = "Partially extractable"
            row.geo_rating = "Fragmented"
            row.aax_status = "completed"
        s.commit()
    return cid
