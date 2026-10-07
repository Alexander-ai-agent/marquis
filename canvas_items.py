"""Canvas items: which kinds exist and what a stored spec may contain.

`kind` is validated here, in code, not by a database CHECK, so a new kind
is one entry in KINDS rather than a migration. Everything stored is the
backend's own validated output (butler.validate_visualization and friends)
plus the user's edits, re-validated on every change.
"""
import json
import re
from typing import Optional

import butler
import design as design_module

# Kinds a spec can have. `images` blocks are stored as `reel`.
VIZ_KINDS = tuple(butler.CANVAS_TYPES)                      # chart, sheet, bars, drawing, blueprint, ...
KINDS = tuple(k for k in VIZ_KINDS if k != "images") + ("reel", "design", "site", "sources")
EDITABLE_KINDS = ("sheet", "blueprint", "reel")             # the user changes these in place
MAX_SPEC_BYTES = 700_000
MAX_REEL_IMAGES = 24

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_HTTP = re.compile(r"^https?://[^\s<>\"']{1,500}$")
_HTTPS = re.compile(r"^https://[^\s<>\"']{1,500}$")
_STAGES = ("select", "sequenced", "music", "done")


def is_uuid(value) -> bool:
    return isinstance(value, str) and bool(_UUID.match(value))


def kind_for(block_type: str) -> str:
    """The stored kind for a canvas block type."""
    return "reel" if block_type == "images" else block_type


def _sources(raw) -> list:
    out = []
    for s in (raw if isinstance(raw, list) else [])[:10]:
        if isinstance(s, dict) and isinstance(s.get("n"), int) and _HTTP.match(str(s.get("url") or "")):
            out.append({"n": s["n"], "title": str(s.get("title") or "")[:200], "url": s["url"]})
    return out


def _reel(spec: dict) -> Optional[dict]:
    query = spec.get("query")
    if not isinstance(query, str) or not query.strip():
        return None
    images = []
    for im in (spec.get("images") or [])[:MAX_REEL_IMAGES]:
        if isinstance(im, dict) and _HTTPS.match(str(im.get("src") or "")):
            images.append({
                "id": str(im.get("id") or "")[:80], "src": im["src"],
                "alt": str(im.get("alt") or "")[:120], "author": str(im.get("author") or "")[:80],
                "author_url": im["author_url"] if _HTTPS.match(str(im.get("author_url") or "")) else "",
            })
    kept = [i for i in (spec.get("kept") or []) if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(images)]
    stage = spec.get("stage") if spec.get("stage") in _STAGES else "select"
    return {"type": "images", "title": str(spec.get("title") or "")[:80], "query": query.strip()[:60],
            "images": images, "kept": list(dict.fromkeys(kept)), "stage": stage,
            "music": str(spec.get("music") or "")[:100]}


def clean_spec(kind: str, spec) -> Optional[dict]:
    """The spec as it may be stored for `kind`, or None if it isn't valid."""
    if kind not in KINDS or not isinstance(spec, dict):
        return None
    sources = _sources(spec.get("sources"))
    if kind == "reel":
        clean = _reel(spec)
    elif kind == "sources":
        clean = {"type": "sources", "title": str(spec.get("title") or "What I read on your behalf")[:80]} if sources else None
    elif kind == "site":
        html = spec.get("html")
        clean = ({"type": "site", "title": str(spec.get("title") or "The page")[:80], "html": html}
                 if isinstance(html, str) and "</html>" in html.lower() else None)
    elif kind == "design":
        clean = design_module.validate_design(spec)
    else:
        clean = butler.validate_visualization({**spec, "type": kind})
    if clean is None:
        return None
    if sources:
        clean["sources"] = sources
    if len(json.dumps(clean, ensure_ascii=False).encode("utf-8")) > MAX_SPEC_BYTES:
        return None
    return clean


def public(row: dict, with_spec: bool = True) -> dict:
    """An item as the frontend sees it."""
    out = {k: row.get(k) for k in ("id", "exchange_id", "kind", "title", "x", "y", "w", "h", "dismissed", "created_at", "updated_at")}
    if with_spec:
        out["spec"] = row.get("spec")
    return out
