from meshweave.extraction.emails import extract_emails, is_valid_email


def test_extract_emails_ignores_script_and_style_and_deobfuscates():
    html = """
    <html>
      <head>
        <style>.cls { background:url('x'); }</style>
        <script>var e = "dev@example.com";</script>
      </head>
      <body>
        <a href="mailto:contact@example.com">Email us</a>
        <p>Reach us at hello [at] example [dot] com</p>
      </body>
    </html>
    """
    unique, sources = extract_emails(html, deobfuscate=True)

    assert "contact@example.com" in unique
    assert "hello@example.com" in unique
    assert "dev@example.com" not in unique

    src_emails = {s["email"] for s in sources}
    assert "contact@example.com" in src_emails
    assert "hello@example.com" in src_emails


def test_extract_emails_filters_false_positives():
    html = """
    <html><body>
      <p>Contact us at support@example.com</p>
    </body></html>
    """
    unique, _ = extract_emails(html, deobfuscate=True)
    assert "support@example.com" in unique


def test_is_valid_email_valid_cases():
    valid = [
        "test@example.com",
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
      <p>Contact: hello [at] example [dot] com</p>
      <p>Email: info@example.net</p>
      <script>var temp = "spam@fake.com";</script>
    </body></html>
    """
    unique, _ = extract_emails(html, deobfuscate=True)
    assert "hello@example.com" in unique
    assert "info@example.net" in unique
    assert "spam@fake.com" not in unique

    # No deobfuscation
    unique2, _ = extract_emails(html, deobfuscate=False)
    assert "hello@example.com" not in unique2
    assert "info@example.net" in unique2
