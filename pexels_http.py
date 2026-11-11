"""Shared Pexels request metadata; never store credentials in global headers."""

# Some Pexels/Cloudflare edges reject default Python HTTP-library User-Agents.
# Match the browser-compatible UA verified by the metadata-only smoke request,
# while retaining an explicit application identifier.
PEXELS_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Project-GAKU/1.0"
)


def pexels_headers(api_key: str) -> dict[str, str]:
    """Return fresh per-request headers; do not log the Authorization value."""
    return {"Authorization": api_key, "User-Agent": PEXELS_USER_AGENT, "Accept": "application/json"}
