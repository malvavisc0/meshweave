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
