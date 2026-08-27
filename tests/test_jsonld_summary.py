"""Tests for the JSON-LD summarizer used by the AAX meta test.

The summarizer must surface the structured data an LLM consumer would
actually read — FAQPage questions, feature lists, offers, logos, dates —
not just @type/name/description, or rich sites report as "empty" types.
"""

import json

from meshweave.ai.prompts import summarize_jsonld


def test_faq_page_questions_are_visible():
    jsonld = [
        {
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": "What does MeshWeave do?",
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": "MeshWeave audits websites for AI visibility.",
                    },
                },
                {"@type": "Question", "name": "How much does it cost?"},
            ],
        }
    ]
    out = json.loads(summarize_jsonld(jsonld))[0]
    assert out["mainEntity_count"] == 2
    qa = out["mainEntity_qa"]
    assert qa[0]["question"] == "What does MeshWeave do?"
    assert "audits websites" in qa[0]["answer_excerpt"]
    # Questions without answers are still listed, without the key.
    assert qa[1] == {"question": "How much does it cost?"}


def test_contact_point_nested_visible():
    jsonld = [
        {
            "@type": "Organization",
            "name": "MeshWeave",
            "contactPoint": [
                {
                    "@type": "ContactPoint",
                    "contactType": "sales",
                    "email": "sales@x.com",
                },
                {
                    "@type": "ContactPoint",
                    "contactType": "customer support",
                    "email": "hello@x.com",
                },
            ],
        }
    ]
    out = json.loads(summarize_jsonld(jsonld))[0]
    assert out["contactPoint"][0] == {"contactType": "sales", "email": "sales@x.com"}
    assert out["contactPoint"][1]["contactType"] == "customer support"


def test_software_application_details_visible():
    jsonld = [
        {
            "@type": "SoftwareApplication",
            "name": "MeshWeave",
            "applicationCategory": "DataExtraction",
            "featureList": ["AI visibility risk analysis", "Citation diagnostics"],
            "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
            "dateModified": "2026-08-27",
            "author": {"@type": "Organization", "name": "MeshWeave"},
        }
    ]
    out = json.loads(summarize_jsonld(jsonld))[0]
    assert out["featureList"] == ["AI visibility risk analysis", "Citation diagnostics"]
    assert out["offers"]["price"] == "0"
    assert out["dateModified"] == "2026-08-27"
    assert out["author"] == "MeshWeave"


def test_organization_sameas_and_logo_visible():
    jsonld = [
        {
            "@type": "Organization",
            "name": "MeshWeave",
            "logo": "https://x/static/brain.png",
            "sameAs": ["https://github.com/x", "https://x/linkedin"],
        }
    ]
    out = json.loads(summarize_jsonld(jsonld))[0]
    assert out["logo"] == "https://x/static/brain.png"
    assert out["sameAs_count"] == 2
    assert len(out["sameAs_sample"]) == 2


def test_empty_and_malformed_inputs():
    assert summarize_jsonld([]) == "None"
    assert summarize_jsonld([None, "string", {}]) == "None"
