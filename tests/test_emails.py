from markdownify_crawler.core import extract_emails


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
