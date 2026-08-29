def resolve_page_visibility(is_authed: bool, public_param: str | None) -> bool:
    """Determine page-mode visibility (public vs private).

    Rules:
      - Signed-in: default private unless 'public' param provided (truthy -> public).
      - Signed-out: always public. Anonymous callers can no longer resolve to
        private; anonymous-private submissions are rejected at submit time.
    Returns:
      bool: True if public, False if private.
    """
    if is_authed:
        # Explicit param wins; otherwise default private
        return bool(public_param)
    # Anonymous: never private.
    return True


def resolve_site_visibility(is_authed: bool, public_param: str | None) -> str:
    """Determine site-mode visibility string ('public' or 'private').

    Rules:
      - Signed-in: default private unless 'public' provided (truthy -> public).
      - Signed-out: always public. Anonymous callers can no longer resolve to
        private; anonymous-private submissions are rejected at submit time.
    Returns:
      str: 'public' or 'private'.
    """
    if is_authed:
        return "public" if bool(public_param) else "private"
    # Anonymous: never private.
    return "public"
