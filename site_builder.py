"""The builder: turns a brief into a working, self-contained web page.

When the founder asks for a website, landing page or app screen, the butler
hands over a <<BUILD brief>> instead of writing code in the chat. The
frontend calls /canvas/site while the butler speaks; this module asks
Claude, prompted as a senior front-end designer, for one complete HTML
document (inline CSS and JS). The canvas shows it live in a sandboxed
iframe (scripts allowed, no same-origin access), and the founder can
download it.
"""
import re
from typing import Optional, Tuple

from butler import get_client
from config import Config

MAX_HTML_BYTES = 400_000
_FENCE = re.compile(r"^```(?:html)?\s*|\s*```\s*$", re.IGNORECASE)
_SAID = re.compile(r"<!--\s*SAID:\s*(.*?)\s*-->", re.DOTALL)
_TITLE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)

SITE_SYSTEM_PROMPT = (
    "You are a senior front-end designer and engineer. You receive a brief and return ONE complete, "
    "production-quality HTML document that works on its own: all CSS in a <style> tag, any JavaScript in a "
    "<script> tag, no build step, no frameworks. External resources: Google Fonts only. No external images: "
    "use CSS gradients, inline SVG illustrations and typography instead, so nothing ever loads broken.\n\n"
    "Craft: a clear concept and art direction drawn from the brand in the brief; a real typographic scale; "
    "generous spacing; responsive down to 375px; tasteful motion (CSS transitions, IntersectionObserver "
    "reveals, respecting prefers-reduced-motion); accessible contrast and semantic landmarks. Write real, "
    "specific copy for the brand, never lorem ipsum. Links and forms stay on the page (use # anchors; forms "
    "show a thank-you state in JS, they never submit anywhere).\n\n"
    "Keep it focused: a landing page is usually a hero, three to five sections, and a footer. Stay under about "
    "500 lines.\n\n"
    "Output format, exactly: first line an HTML comment <!-- SAID: one short spoken sentence from Alfred, the "
    "user's butler (natural and composed, may call them \"sir\", never \"certainly\" or \"of course\"), saying "
    "what was built -->, then the document starting with <!DOCTYPE html>. Nothing else: no "
    "markdown fences, no commentary."
)


def _text(response) -> str:
    return "".join(getattr(b, "text", None) or "" for b in response.content if getattr(b, "type", "text") == "text")


def clean_html(raw: str) -> Optional[str]:
    """The HTML document from a model reply, or None if it isn't one."""
    if not isinstance(raw, str):
        return None
    html = _FENCE.sub("", raw.strip()).strip()
    start = html.lower().find("<!doctype html")
    if start == -1:
        start = html.lower().find("<html")
    if start == -1 or "</html>" not in html.lower():
        return None
    html = html[start:html.lower().rfind("</html>") + len("</html>")]
    if len(html.encode("utf-8")) > MAX_HTML_BYTES:
        return None
    return html


def build_site(brief: str) -> Tuple[str, Optional[dict]]:
    """Build a page for `brief`; returns (sentence, site-block-or-None)."""
    response = get_client().messages.create(
        model=Config.SITE_MODEL,
        max_tokens=12000,
        system=SITE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"BRIEF: {brief}"}],
    )
    raw = _text(response)
    said_match = _SAID.search(raw)
    said = " ".join(said_match.group(1).split())[:300] if said_match else ""
    html = clean_html(raw)
    if not html:
        return said, None
    title_match = _TITLE.search(html)
    title = " ".join(title_match.group(1).split())[:80] if title_match else "The page"
    return said, {"type": "site", "title": title, "html": html}
