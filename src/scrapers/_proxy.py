"""Parse Apify proxy URL into camoufox/Playwright proxy settings."""
from urllib.parse import urlparse


def parse_proxy(proxy_url: str | None) -> dict | None:
    if not proxy_url:
        return None
    try:
        p = urlparse(proxy_url)
        opts: dict = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
        if p.username:
            opts["username"] = p.username
        if p.password:
            opts["password"] = p.password
        return opts
    except Exception:
        return {"server": proxy_url}
