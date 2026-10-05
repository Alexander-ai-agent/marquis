"""Web research for the butler: Tavily finds the pages, Scrapling reads them.

Tavily's search API returns ranked results with a short extract. For the
top few we fetch the page ourselves and let Scrapling's parser pull the
readable text, falling back to Tavily's extract when a page can't be read.

Fetching is deliberately plain: `requests`, no redirects, a short timeout,
a size cap, and an SSRF guard that refuses anything resolving to a
private, loopback, link-local or reserved address. Sites that block
automated readers simply contribute their Tavily extract; we never try to
get around bot protection.
"""
import ipaddress
import socket
from urllib.parse import urlparse

import requests
from scrapling import Selector

from config import Config

TAVILY_SEARCH = "https://api.tavily.com/search"
SEARCH_TIMEOUT_S = 12
FETCH_TIMEOUT_S = 6
MAX_PAGE_BYTES = 1_500_000
MAX_SOURCE_CHARS = 5000
PAGES_TO_READ = 3
USER_AGENT = "MarquisResearch/1.0 (+https://marquis-production.up.railway.app)"
_NOISE_TAGS = ("script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg")


def search_web(query: str, max_results: int = 5) -> list:
    """Tavily search -> [{title, url, content}]."""
    res = requests.post(
        TAVILY_SEARCH,
        json={"query": query, "max_results": max_results, "search_depth": "basic"},
        headers={"Authorization": f"Bearer {Config.TAVILY_API_KEY}"},
        timeout=SEARCH_TIMEOUT_S,
    )
    res.raise_for_status()
    out = []
    for r in res.json().get("results", []):
        url = r.get("url") or ""
        if not url.startswith(("http://", "https://")):
            continue
        out.append({
            "title": (r.get("title") or url)[:200],
            "url": url,
            "content": (r.get("content") or "")[:MAX_SOURCE_CHARS],
        })
    return out


def is_public_url(url: str) -> bool:
    """True only for http(s) URLs whose host resolves solely to public addresses."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except (socket.gaierror, UnicodeError):
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            return False
    return bool(infos)


def read_page(url: str) -> str:
    """Fetch one public page and return its readable text ('' if unreadable)."""
    if not is_public_url(url):
        return ""
    with requests.get(url, timeout=FETCH_TIMEOUT_S, allow_redirects=False, stream=True,
                      headers={"User-Agent": USER_AGENT, "Accept": "text/html"}) as res:
        if res.status_code != 200 or "html" not in res.headers.get("Content-Type", ""):
            return ""
        body = b""
        for chunk in res.iter_content(64 * 1024):
            body += chunk
            if len(body) > MAX_PAGE_BYTES:
                break
    text = Selector(body[:MAX_PAGE_BYTES], url=url).get_all_text(ignore_tags=_NOISE_TAGS)
    lines = (ln.strip() for ln in str(text).splitlines())
    return "\n".join(ln for ln in lines if len(ln) > 2)[:MAX_SOURCE_CHARS]


def research(query: str) -> list:
    """Search, then read the top pages. Returns [{title, url, text}]."""
    sources = []
    for i, hit in enumerate(search_web(query)):
        text = ""
        if i < PAGES_TO_READ:
            try:
                text = read_page(hit["url"])
            except requests.RequestException as e:
                print(f"[web] read failed for {hit['url']}: {e}")
        text = text if len(text) > len(hit["content"]) else hit["content"]
        if text:
            sources.append({"title": hit["title"], "url": hit["url"], "text": text})
    return sources
