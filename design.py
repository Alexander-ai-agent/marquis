"""The designer: a dedicated pass for real visual design on the canvas.

The butler's conversational reply stays short; when the founder asks for
something that must be *designed* (a logo, mark, icon, poster, card, a
visual for a page) the butler ends its reply with a <<DESIGN brief>> line.
The frontend then calls /canvas/design with that brief while the butler is
already speaking, and this module asks Claude, prompted as a senior
identity designer, for a finished vector composition.

The output vocabulary is far richer than the `drawing` block (which stays
for diagrams and flows): Bezier paths, polygons, ellipses, fills from a
fixed palette (including gradients), stroke weights, rotation, and set
typography, in two or three distinct concepts. Everything is validated
here; the frontend only ever renders whitelisted shapes and paints.
"""
import re
from typing import Optional, Tuple

from butler import _extract_json, _num, get_client
from config import Config

PAINTS = (
    "none", "ink", "ink-soft", "gold", "gold-light", "amber", "bronze", "oxblood", "ground",
    "gold-gradient", "ink-gradient", "dusk-gradient",
)
BACKGROUNDS = ("ground", "ink", "gold", "oxblood")
SHAPE_KINDS = ("path", "rect", "circle", "ellipse", "polygon", "line", "text")
MAX_VARIANTS = 3
MAX_SHAPES_PER_VARIANT = 48
_PATH_D = re.compile(r"^[Mm][MmLlHhVvCcSsQqTtAaZz0-9eE\s,.\-]{0,1600}$")
_BOARD_MIN, _BOARD_MAX = -20.0, 120.0

DESIGN_SYSTEM_PROMPT = (
    "You are the designer in a small, exacting studio: a senior identity and graphic designer with the "
    "restraint of Pentagram and the craft of a type foundry. You receive a brief and return a finished vector "
    "composition, not a sketch.\n\n"
    "How you work:\n"
    "- Build on a 100x100 board with a construction grid; keep the work within 8-92 unless it is meant to bleed.\n"
    "- Start from an idea, then geometry: circles, golden-ratio proportions, shared centres, deliberate negative "
    "space, optical (not mathematical) centring. Every element earns its place.\n"
    "- Use real curves. Paths with C, Q and A commands for anything organic or letter-like; polygons for crisp "
    "facets. Never settle for a box and two lines.\n"
    "- Consistent stroke weights; at most three paints per concept, chosen from the palette.\n"
    "- Wordmarks use set type: serif (Cormorant Garamond) or mono (JetBrains Mono), with considered weight and "
    "letter-spacing. Keep text short.\n"
    "- For a logo, mark or icon, give THREE genuinely different concepts (for example a monogram, an abstract "
    "symbol, and an emblem or lockup). For a poster, card or page visual, give one or two.\n"
    "- It must hold up small: a mark should still read at 16 pixels.\n\n"
    "Palette (paints): none, ink (warm white), ink-soft (dim warm white), gold, gold-light (champagne), amber, "
    "bronze, oxblood, ground (near-black), gold-gradient, ink-gradient, dusk-gradient (gold into oxblood). "
    "Backgrounds: ground, ink, gold, oxblood.\n\n"
    "Respond with JSON only, exactly:\n"
    '{"said": one sentence in the butler\'s formal voice naming the idea behind the work,\n'
    ' "design": {"title": str, "variants": [{"name": str, "note": str (one line: the idea), '
    '"background": one of the backgrounds, "shapes": [shape, ...]}]}}\n'
    "Shapes (all coordinates on the 100x100 board; every shape may also take "
    '"fill": paint, "stroke": paint, "sw": 0-6 stroke width, "opacity": 0-1, "rotate": degrees about ["ox","oy"] '
    "which default to the board centre):\n"
    '- {"kind":"path","d":"M ... Z"}  (absolute or relative SVG path commands only)\n'
    '- {"kind":"rect","x","y","w","h","rx"}\n'
    '- {"kind":"circle","cx","cy","r"}\n'
    '- {"kind":"ellipse","cx","cy","rx","ry"}\n'
    '- {"kind":"polygon","points":[[x,y],...]}  (3-40 points)\n'
    '- {"kind":"line","x1","y1","x2","y2"}\n'
    '- {"kind":"text","x","y","text","size":2-30,"weight":300|400|500|600,"family":"serif"|"mono",'
    '"anchor":"start"|"middle"|"end","ls":letter-spacing -2..10}\n'
    f"At most {MAX_SHAPES_PER_VARIANT} shapes per concept. Shapes are painted in order: background first."
)


def _coord(v) -> bool:
    return _num(v) and _BOARD_MIN <= v <= _BOARD_MAX


def _paint(v, default: str) -> str:
    return v if v in PAINTS else default


def _style(s: dict, fill_default: str, stroke_default: str) -> dict:
    out = {"fill": _paint(s.get("fill"), fill_default), "stroke": _paint(s.get("stroke"), stroke_default)}
    sw = s.get("sw")
    out["sw"] = float(sw) if _num(sw) and 0 <= sw <= 6 else (0.6 if out["stroke"] != "none" else 0.0)
    op = s.get("opacity")
    out["opacity"] = float(op) if _num(op) and 0 <= op <= 1 else 1.0
    rot = s.get("rotate")
    if _num(rot) and -360 <= rot <= 360 and rot:
        out["rotate"] = float(rot)
        out["ox"] = float(s["ox"]) if _coord(s.get("ox")) else 50.0
        out["oy"] = float(s["oy"]) if _coord(s.get("oy")) else 50.0
    return out


def _shape(s) -> Optional[dict]:
    """One validated shape, or None if it is malformed."""
    if not isinstance(s, dict) or s.get("kind") not in SHAPE_KINDS:
        return None
    k = s["kind"]
    if k == "path":
        d = s.get("d")
        if not isinstance(d, str) or not _PATH_D.match(d.strip()):
            return None
        return {"kind": k, "d": d.strip(), **_style(s, "none", "ink")}
    if k == "rect":
        if not all(_coord(s.get(f)) for f in ("x", "y", "w", "h")) or s["w"] <= 0 or s["h"] <= 0:
            return None
        rx = s.get("rx")
        return {"kind": k, **{f: float(s[f]) for f in ("x", "y", "w", "h")},
                "rx": float(rx) if _num(rx) and 0 <= rx <= 50 else 0.0, **_style(s, "none", "ink")}
    if k == "circle":
        if not all(_coord(s.get(f)) for f in ("cx", "cy", "r")) or s["r"] <= 0:
            return None
        return {"kind": k, **{f: float(s[f]) for f in ("cx", "cy", "r")}, **_style(s, "none", "ink")}
    if k == "ellipse":
        if not all(_coord(s.get(f)) for f in ("cx", "cy", "rx", "ry")) or s["rx"] <= 0 or s["ry"] <= 0:
            return None
        return {"kind": k, **{f: float(s[f]) for f in ("cx", "cy", "rx", "ry")}, **_style(s, "none", "ink")}
    if k == "polygon":
        pts = s.get("points")
        if not isinstance(pts, list) or not 3 <= len(pts) <= 40:
            return None
        if not all(isinstance(p, list) and len(p) == 2 and _coord(p[0]) and _coord(p[1]) for p in pts):
            return None
        return {"kind": k, "points": [[float(p[0]), float(p[1])] for p in pts], **_style(s, "none", "ink")}
    if k == "line":
        if not all(_coord(s.get(f)) for f in ("x1", "y1", "x2", "y2")):
            return None
        return {"kind": k, **{f: float(s[f]) for f in ("x1", "y1", "x2", "y2")}, **_style(s, "none", "ink")}
    # text
    text = s.get("text")
    if not isinstance(text, str) or not text.strip() or not _coord(s.get("x")) or not _coord(s.get("y")):
        return None
    size, weight, ls = s.get("size"), s.get("weight"), s.get("ls")
    return {
        "kind": k, "x": float(s["x"]), "y": float(s["y"]), "text": text.strip()[:40],
        "size": float(size) if _num(size) and 2 <= size <= 30 else 6.0,
        "weight": weight if weight in (300, 400, 500, 600) else 400,
        "family": s.get("family") if s.get("family") in ("serif", "mono") else "serif",
        "anchor": s.get("anchor") if s.get("anchor") in ("start", "middle", "end") else "middle",
        "ls": float(ls) if _num(ls) and -2 <= ls <= 10 else 0.0,
        **_style(s, "ink", "none"),
    }


def validate_design(payload) -> Optional[dict]:
    """A clean `design` canvas block, or None. Malformed shapes are dropped;
    a concept left with no shapes is dropped; no concepts means None."""
    if not isinstance(payload, dict):
        return None
    variants = payload.get("variants")
    if not isinstance(variants, list) or not variants:
        return None
    clean = []
    for v in variants[:MAX_VARIANTS]:
        if not isinstance(v, dict) or not isinstance(v.get("shapes"), list):
            continue
        shapes = [sh for sh in (_shape(s) for s in v["shapes"][:MAX_SHAPES_PER_VARIANT]) if sh]
        if not shapes:
            continue
        clean.append({
            "name": str(v.get("name") or f"Concept {len(clean) + 1}")[:40],
            "note": str(v.get("note") or "")[:120],
            "background": v.get("background") if v.get("background") in BACKGROUNDS else "ground",
            "shapes": shapes,
        })
    if not clean:
        return None
    return {"type": "design", "title": str(payload.get("title") or "Design")[:60], "variants": clean}


def design_canvas(brief: str) -> Tuple[str, Optional[dict]]:
    """Have the designer realise `brief`; returns (sentence, design-or-None)."""
    response = get_client().messages.create(
        model=Config.CLAUDE_MODEL,
        max_tokens=7000,
        system=DESIGN_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"BRIEF: {brief}"}],
    )
    data = _extract_json(response.content[0].text)
    if not isinstance(data, dict):
        raise ValueError("design reply not an object")
    said = str(data.get("said") or "").strip()[:300]
    return said, validate_design(data.get("design"))
