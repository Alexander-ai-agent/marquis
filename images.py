"""Image search for the canvas `images` block (Unsplash).

Unsplash's API guidelines require hotlinking their image URLs (not
re-hosting) and crediting the photographer, so each result carries the
author name and profile link for the canvas to show.

Web information (pages, articles, pricing) is a separate concern: the
founder is choosing a scraper. When one is chosen, it plugs in beside this
module and feeds the conversation endpoint; nothing here depends on it.
"""
import requests

from config import Config

UNSPLASH_SEARCH = "https://api.unsplash.com/search/photos"
TIMEOUT_S = 8


def search_images(query: str, count: int = 12) -> list:
    res = requests.get(
        UNSPLASH_SEARCH,
        params={"query": query, "per_page": count, "orientation": "portrait", "content_filter": "high"},
        headers={"Authorization": f"Client-ID {Config.UNSPLASH_ACCESS_KEY}", "Accept-Version": "v1"},
        timeout=TIMEOUT_S,
    )
    res.raise_for_status()
    out = []
    for p in res.json().get("results", []):
        urls, user = p.get("urls") or {}, p.get("user") or {}
        if not urls.get("small"):
            continue
        out.append({
            "id": p.get("id"),
            "src": urls["small"],
            "alt": (p.get("alt_description") or "")[:120],
            "author": user.get("name") or "",
            "author_url": (user.get("links") or {}).get("html") or "",
        })
    return out
