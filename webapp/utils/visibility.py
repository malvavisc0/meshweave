def resolve_page_visibility(is_authed: bool, public_param: str | None) -> bool:
    """Determine page-mode visibility (public vs private).

    Rules:
      - Signed-in: default private unless 'public' param provided (truthy -> public).
      - Signed-out: default public unless 'public' is explicitly provided and falsy.
    Returns:
      bool: True if public, False if private.
    """
    if is_authed:
        # Explicit param wins; otherwise default private
        return bool(public_param)
    # Anonymous: default public. If param is provided and falsy (empty), set private.
    return True if (public_param is None) else bool(public_param)


def resolve_site_visibility(is_authed: bool, public_param: str | None) -> str:
    """Determine site-mode visibility string ('public' or 'private').

    Rules:
      - Signed-in: default private unless 'public' provided (truthy -> public).
      - Signed-out: default public unless 'public' provided and falsy.
    Returns:
      str: 'public' or 'private'.
    """
    if is_authed:
        return "public" if bool(public_param) else "private"
    # Anonymous default public; explicit falsy -> private
    return "public" if (public_param is None or bool(public_param)) else "private"
