"""Which browser origins may call the API with credentials (CORS).

An origin is `scheme://host[:port]`. The frontend's address is always allowed; more can be added
with CORS_EXTRA_ORIGINS (a comma-separated list), for example `http://localhost:3000` to try the
production API from a local frontend. A wildcard is refused: with credentials it would let any
website use a visitor's login.
"""

from urllib.parse import urlsplit


def origin_of(value: str) -> str:
    """`https://app.example.com/some/path` -> `https://app.example.com`."""
    value = value.strip()
    if "*" in value:
        raise ValueError("a wildcard origin is not allowed (the API uses credentials)")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"not an origin (needs http:// or https:// and a host): {value!r}")
    return f"{parts.scheme}://{parts.netloc}"


def allowed_origins(frontend_url: str, extra: str = "") -> list[str]:
    origins = [origin_of(frontend_url)]
    for part in extra.split(","):
        if part.strip():
            origin = origin_of(part)
            if origin not in origins:
                origins.append(origin)
    return origins
