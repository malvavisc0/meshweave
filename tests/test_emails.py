from markdownify_crawler.core import _is_valid_email, extract_emails


def test_extract_emails_ignores_script_and_style_and_deobfuscates():
    html = """
    <html>
      <head>
        <style>
          /* someone@example.com should not be detected from CSS */
          .cls { background:url('x'); }
        </style>
        <script>
          // dev@example.com should not be detected from script text
          var e = "dev@example.com";
        </script>
      </head>
      <body>
        <a href="mailto:contact@example.com">Email us</a>
        <p>Reach us at hello [at] example [dot] com for info.</p>
      </body>
    </html>
    """

    unique, sources = extract_emails(html, deobfuscate=True)

    # Expected emails:
    # - contact@example.com (from mailto)
    # - hello@example.com (from obfuscated visible text)
    # Not expected:
    # - dev@example.com (inside script)
    # - someone@example.com (inside style)
    assert "contact@example.com" in unique
    assert "hello@example.com" in unique
    assert "dev@example.com" not in unique
    assert "someone@example.com" not in unique

    # Ensure sources list only contains the expected addresses
    src_emails = {s["email"] for s in sources}
    assert "contact@example.com" in src_emails
    assert "hello@example.com" in src_emails
    assert "dev@example.com" not in src_emails
    assert "someone@example.com" not in src_emails


def test_extract_emails_filters_false_positives():
    """Test that false positive emails from deobfuscation are filtered out."""
    html = """
    <html>
      <body>
        <p>1. Introduction to our services at evaluatemyidea.ai</p>
        <p>Contact us at support@example.com</p>
        <p>Visit card at signup.put for more info.</p>
        <p>Tools at all.they need.</p>
      </body>
    </html>
    """

    unique, sources = extract_emails(html, deobfuscate=True)

    # Valid emails should be included
    assert "support@example.com" in unique

    # False positives should be filtered out
    assert "1.introduction@evaluatemyidea.ai" not in unique
    assert "card@signup.put" not in unique
    assert "tools@all.they" not in unique

    # Check that sources reflect the filtering
    src_emails = {s["email"] for s in sources}
    assert "support@example.com" in src_emails
    # Sources may include found_as even if filtered from unique


def test_is_valid_email_valid_cases():
    """Test that valid emails pass validation."""
    valid_emails = [
        "test@example.com",
        "user.name+tag@domain.co.uk",
        "contact@evaluatemyidea.ai",
        "support@company.org",
        "user123@test.net",
    ]
    for email in valid_emails:
        assert _is_valid_email(email), f"Email {email} should be valid"


def test_is_valid_email_invalid_cases():
    """Test that invalid emails fail validation."""
    invalid_emails = [
        "",  # empty
        "user@",  # no domain
        "@domain.com",  # no local
        "user@@domain.com",  # double @
        "user@domain",  # no TLD
        "user@domain.1",  # numeric TLD
        "user..name@domain.com",  # consecutive dots in local
        "user@domain..com",  # consecutive dots in domain
        "user@domain.fake",  # unknown TLD
        "a@domain.com",  # local too short
        "user@domain.c",  # TLD too short
    ]
    for email in invalid_emails:
        assert not _is_valid_email(email), f"Email {email} should be invalid"


def test_is_valid_email_false_positives():
    """Test that common false positive patterns are rejected."""
    false_positives = [
        "card@signup.put",  # unknown TLD
        "tools@all.they",  # unknown TLD
        "market@all.the",  # unknown TLD
        "data@scale.industry",  # unknown TLD
        "everywhere@once.turns",  # unknown TLD
        "merch@shows.the",  # unknown TLD
    ]
    for email in false_positives:
        assert not _is_valid_email(email), f"False positive {email} should be invalid"


def test_extract_emails_edge_cases():
    """Test email extraction with complex and edge case scenarios."""
    # Mixed obfuscation styles
    html1 = """
    <html>
      <body>
        <p>Contact: hello [at] example [dot] com</p>
        <p>Support: support at example.org</p>
        <p>Email: info@example.net</p>
        <script>var temp = "spam@fake.com";</script>
      </body>
    </html>
    """
    unique1, _ = extract_emails(html1, deobfuscate=True)
    assert "hello@example.com" in unique1
    assert "support@example.org" in unique1
    assert "info@example.net" in unique1
    assert "spam@fake.com" not in unique1  # in script

    # Partial obfuscation
    html2 = """
    <html>
      <body>
        <p>Reach us at contact[at]example.com</p>
        <p>Or visit support at example.org</p>
        <p>Plain: admin@example.net</p>
      </body>
    </html>
    """
    unique2, _ = extract_emails(html2, deobfuscate=True)
    assert "contact@example.com" in unique2
    assert "support@example.org" in unique2
    assert "admin@example.net" in unique2

    # Context-dependent (in different HTML elements)
    html3 = """
    <html>
      <body>
        <h1>Welcome</h1>
        <p>For questions: ask at support.example.com</p>
        <footer>Contact: footer at example.org</footer>
        <div class="sidebar">Email: sidebar@example.net</div>
      </body>
    </html>
    """
    unique3, _ = extract_emails(html3, deobfuscate=True)
    assert "ask@support.example.com" in unique3  # valid email
    assert "footer@example.org" in unique3
    assert "sidebar@example.net" in unique3

    # No deobfuscation
    unique4, _ = extract_emails(html1, deobfuscate=False)
    assert "hello@example.com" not in unique4  # not deobfuscated
    assert "info@example.net" in unique4  # plain email
    assert "spam@fake.com" not in unique4
