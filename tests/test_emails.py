from meshweave.extraction.emails import extract_emails, is_valid_email


def test_extract_emails_ignores_script_and_style_and_deobfuscates():
    html = """
    <html>
      <head>
        <style>.cls { background:url('x'); }</style>
        <script>var e = "dev@acme.com";</script>
      </head>
      <body>
        <a href="mailto:contact@acme.com">Email us</a>
        <p>Reach us at hello [at] acme [dot] com</p>
      </body>
    </html>
    """
    unique, sources = extract_emails(html, deobfuscate=True)

    assert "contact@acme.com" in unique
    assert "hello@acme.com" in unique
    assert "dev@acme.com" not in unique

    src_emails = {s["email"] for s in sources}
    assert "contact@acme.com" in src_emails
    assert "hello@acme.com" in src_emails


def test_extract_emails_filters_false_positives():
    html = """
    <html><body>
      <p>Contact us at support@acme.com</p>
    </body></html>
    """
    unique, _ = extract_emails(html, deobfuscate=True)
    assert "support@acme.com" in unique


def test_is_valid_email_valid_cases():
    valid = [
        "test@acme.com",
        "user.name+tag@domain.co.uk",
        "contact@evaluatemyidea.ai",
        "support@company.org",
        "user123@test.net",
    ]
    for email in valid:
        assert is_valid_email(email), f"{email} should be valid"


def test_is_valid_email_invalid_cases():
    invalid = [
        "",
        "user@",
        "@domain.com",
        "user@domain",
        "user..name@domain.com",
        "user@domain..com",
        "a@domain.com",  # local too short
        "user@domain.c",  # TLD too short
    ]
    for email in invalid:
        assert not is_valid_email(email), f"{email} should be invalid"


def test_extract_emails_edge_cases():
    html = """
    <html><body>
      <p>Contact: hello [at] acme [dot] com</p>
      <p>Email: info@acme.net</p>
      <script>var temp = "spam@fake.com";</script>
    </body></html>
    """
    unique, _ = extract_emails(html, deobfuscate=True)
    assert "hello@acme.com" in unique
    assert "info@acme.net" in unique
    assert "spam@fake.com" not in unique

    # No deobfuscation
    unique2, _ = extract_emails(html, deobfuscate=False)
    assert "hello@acme.com" not in unique2
    assert "info@acme.net" in unique2


def test_extract_emails_does_not_absorb_sentence_after_mailto_link():
    """An inline mailto link followed by ``. We will`` must not fuse.

    ``soup.get_text(" ")`` inserts a separator between the anchor text
    and the following text node, producing ``hello@meshweaveai.com . We``
    in extracted text.  The deobfuscation dot-joiner must not collapse
    that into ``hello@meshweaveai.com.we``.
    """
    html = (
        "<html><body><p>by contacting us at "
        '<a href="mailto:hello@meshweaveai.com">hello@meshweaveai.com</a>. '
        "We will process deletion requests within 30 days.</p></body></html>"
    )
    unique, _ = extract_emails(html, deobfuscate=True)
    assert "hello@meshweaveai.com" in unique
    assert "hello@meshweaveai.com.we" not in unique


def test_deobfuscation_still_joins_partial_addresses():
    from meshweave.extraction.emails import _extract_text_emails

    text = "Reach us at john@example . com or jane [at] other [dot] org"
    emails, _ = _extract_text_emails(text, deobfuscate=True)
    assert "john@example.com" in emails
    assert "jane@other.org" in emails
