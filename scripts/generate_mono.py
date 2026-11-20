#!/usr/bin/env python3
"""Generate editable Allo Mono UFO masters, static UFOs, and designspace from Allo sources."""

from __future__ import annotations

import copy
import math
import plistlib
import shutil
from pathlib import Path
import xml.etree.ElementTree as ET

from fontTools.designspaceLib import AxisDescriptor, DesignSpaceDocument, InstanceDescriptor, SourceDescriptor
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
import ufoLib2
from ufoLib2.objects import Component, Contour, Point

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "sources"
MONO_MASTERS_DIR = SRC_DIR / "mono-masters"
MONO_STATIC_DIR = SRC_DIR / "mono-static"
MONO_DS_PATH = SRC_DIR / "AlloMono.designspace"

MONO_WIDTH = 1060
CENTER_X = MONO_WIDTH / 2.0
ASCENDER = 1984
DESCENDER = -494
CAP_HEIGHT = 1490
X_HEIGHT = 1118
CELL_MID_Y = (ASCENDER + DESCENDER) / 2.0  # 745.0 == CAP_HEIGHT / 2
SLANT_ANGLE = -9.0
SLANT_TAN = math.tan(math.radians(9.0))
PIVOT_Y = X_HEIGHT / 2.0  # 559.0

DROP_GLYPHS = {
    "emspace",
    "enspace",
    "thinspace",
    "hairspace",
    "figurespace",
    "punctuationspace",
    "narrownbspace",
    "f.short",
    "f_f",
    "f_f_f",
    "f_f_t",
    "f_t",
    "f_t_f",
    "f_t_t",
    "t_f",
    "t_f_f",
    "t_f_t",
    "t_t",
    "t_t_f",
    "t_t_t",
}

OPENTYPE_CATEGORIES_EXTRA = {
    "zero.plain": "base",
    "I.sans": "base",
}

ROUND_COMP_GLYPHS = {"O", "Q", "C", "G", "zero.plain", "Ohorn", "D", "U", "Uogonek", "Oslash"}
ROUND_LC_GLYPHS = {"o", "oslash", "b", "d", "p", "q", "e", "c", "g", "eth"}
STD_LC_CAP_GLYPHS = {
    "n", "h", "u", "a", "s", "k", "v", "x", "y", "z", "thorn", "eng", "germandbls",
    "two", "three", "four", "five", "six", "seven", "eight", "nine",
}
STEM_PIECEWISE_GLYPHS = {"m", "H", "Hbar"}
DIAG_COMP_GLYPHS = {
    "M", "W", "w", "A", "V", "N", "X", "Y", "AE", "ae", "OE", "at", "at.case",
    "ampersand", "numbersign", "won", "naira", "afii57636", "bitcoin", "baht",
    "currency", "euro", "hryvnia", "peso", "sterling", "uni20B9", "uni20BA",
    "uni20BD", "numero", "infinity", "infinity.case", "warningSign", "option",
    "placeofinterestsign", "deleteleft", "deleteright", "arrowtabright",
    "carriagereturn", "returnsymbol", "brokenCircleNorthWestArrow",
    "clockwiseOpenCircleArrow", "anticlockwiseOpenCircleArrow", "leftArrow",
    "leftArrow.case", "rightArrow", "rightArrow.case", "upArrow", "downArrow",
    "leftDoubleArrow", "leftDoubleArrow.case", "rightDoubleArrow",
    "rightDoubleArrow.case", "leftRightArrow", "leftRightArrow.case",
    "leftRightDoubleArrow", "leftRightDoubleArrow.case", "leftHookArrow",
    "northEastArrow", "northEastArrow.case", "northWestArrow",
    "northWestArrow.case", "southEastArrow", "southEastArrow.case",
    "southWestArrow", "southWestArrow.case", "upDownArrow", "upWhiteArrow",
    "uni2315", "blackstar", "heartBlackSuit", "diamondblack", "blackSquare",
    "whiteSquare", "circleblack", "circlewhite", "uni25CB", "triagup", "triagdn",
    "triagrt", "blackleftpointingtriangle", "dngb_check", "dngb_ballotx",
    "copyright", "registered", "Dcroat", "Dbar1", "Eth", "Eng", "Germandbls",
    "emdash", "emdash.case",
}

LEFT_BIAS_BRACKETS = {"parenleft", "bracketleft", "braceleft", "parenleft.case", "bracketleft.case", "braceleft.case"}
RIGHT_BIAS_BRACKETS = {"parenright", "bracketright", "braceright", "parenright.case", "bracketright.case", "braceright.case"}


def unslant_x(x: float, y: float, is_oblique: bool) -> float:
    return x - (y - PIVOT_Y) * SLANT_TAN if is_oblique else x


def slant_x(x: float, y: float, is_oblique: bool) -> float:
    return x + (y - PIVOT_Y) * SLANT_TAN if is_oblique else x


def glyph_bounds_upright(layer, gname: str, is_oblique: bool) -> tuple[float, float, float, float] | None:
    if gname not in layer:
        return None
    if not is_oblique:
        pen = BoundsPen(layer)
        layer[gname].draw(pen)
        return pen.bounds
    rec = DecomposingRecordingPen(layer)
    layer[gname].draw(rec)
    xs, ys = [], []
    for _, pts in rec.value:
        for x, y in pts:
            xs.append(unslant_x(x, y, True))
            ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def shift_glyph_x(glyph, dx: float) -> None:
    if abs(dx) < 1e-6:
        return
    for c in glyph.contours:
        for p in c:
            p.x += dx
    for comp in glyph.components:
        m = list(comp.transformation)
        m[4] += dx
        comp.transformation = tuple(m)
    for a in glyph.anchors:
        a.x += dx


def scale_glyph_x_around(glyph, cx: float, sx: float, is_oblique: bool = False) -> None:
    for c in glyph.contours:
        for p in c:
            u = unslant_x(p.x, p.y, is_oblique)
            u2 = cx + (u - cx) * sx
            p.x = slant_x(u2, p.y, is_oblique)
    for a in glyph.anchors:
        u = unslant_x(a.x, a.y, is_oblique)
        u2 = cx + (u - cx) * sx
        a.x = slant_x(u2, a.y, is_oblique)


def extract_metrics(layer, is_oblique: bool) -> dict[str, float]:
    """Measure stem widths, serif height, bar thickness, and corner radius from the master."""
    def box_u(gname):
        g = layer[gname]
        xs = [unslant_x(p.x, p.y, is_oblique) for c in g.contours for p in c]
        ys = [p.y for c in g.contours for p in c]
        return min(xs), min(ys), max(xs), max(ys)

    ix0, _, ix1, _ = box_u("I")
    cap_stem = ix1 - ix0

    lx0, _, lx1, _ = box_u("idotless")
    lc_stem = lx1 - lx0

    # Measure serif height and corner radius from I.1
    g_i1 = layer["I.1"]
    pts = [(unslant_x(p.x, p.y, is_oblique), p.y) for c in g_i1.contours for p in c]
    ys_inner = sorted({round(y, 2) for _, y in pts if 10 < y < 600})
    # Bottom serif top is the max y among bottom serif points (< 500)
    serif_h = max(ys_inner) if ys_inner else round(cap_stem * 0.85, 1)

    # Corner radius from I top-right on-curve corner (off-curve points sit at ~0.26 * r_cap from corner)
    g_i = layer["I"]
    i_pts = [(unslant_x(p.x, p.y, is_oblique), p.y, p.type) for c in g_i.contours for p in c]
    top_on_ys = sorted({y for _, y, t in i_pts if t is not None and 1200 < y < 1490.0 - 0.001})
    r_cap = (1490.0 - max(top_on_ys)) if top_on_ys else 0.0145
    if r_cap < 0.1:
        r_cap = 0.0
    off_ys = [y for x, y, t in i_pts if t is None and abs(x - ix1) < 1.0 and 1490.0 - r_cap - 1.0 < y < 1490.0]
    if r_cap > 1.0 and off_ys:
        handle_frac = max(0.05, min(0.95, (1490.0 - max(off_ys)) / r_cap))
    else:
        handle_frac = 0.26

    hx0, hy0, hx1, hy1 = box_u("hyphen")
    bar_h = hy1 - hy0
    bar_mid_y = (hy0 + hy1) / 2.0

    ex0, ey0, ex1, ey1 = box_u("equal")
    eq_span_y = ey1 - ey0
    # equal has 2 separate bar contours; measure exact single bar height from contour 0
    if len(layer["equal"].contours) >= 1:
        b0_ys = [p.y for p in layer["equal"].contours[0]]
        eq_bar_h = max(b0_ys) - min(b0_ys)
    else:
        eq_bar_h = bar_h * 0.88

    return {
        "cap_stem": cap_stem,
        "lc_stem": lc_stem,
        "serif_h": serif_h,
        "r_cap": r_cap,
        "handle_frac": handle_frac,
        "bar_h": bar_h,
        "bar_mid_y": bar_mid_y,
        "eq_bar_h": eq_bar_h,
        "eq_span_y": eq_span_y,
        "eq_y0": ey0,
        "eq_y1": ey1,
    }


def add_rounded_rect(contour, x0: float, y0: float, x1: float, y1: float, r: float, hfrac: float, is_oblique: bool = False) -> None:
    """Append a clockwise rounded rectangle matching Allo's 16-point corner topology."""
    w = x1 - x0
    h = y1 - y0
    rr = min(r, max(0.0, w / 2.0 - 0.01), max(0.0, h / 2.0 - 0.01))
    off = rr * hfrac

    def pt(x, y, ptype=None):
        contour.append(Point(slant_x(x, y, is_oblique), y, ptype))

    # Start top-right going counter-clockwise? Wait: let's match Allo's exact winding (CCW in UFO)!
    # Allo I.glif starts at top-right (x1, y1 - off) -> (x1 - off, y1) -> (x1 - rr, y1) curve -> (x0 + rr, y1) line ...
    # Wait: let's check winding in Allo I.glif:
    # (358, 1473.1) -> (341.1, 1490) -> (293.5, 1490) [top-right CCW] -> (234.5, 1490) [top-left] -> (170, 1425.5) -> (170, 64.5) [bot-left] -> (234.5, 0) -> (293.5, 0) [bot-right] -> (358, 64.5) -> up to (358, 1425.5).
    # That's counter-clockwise (standard TrueType/UFO outer contour)!
    if rr < 0.05:
        eps = 0.0145
        pt(x1, y1 - 0.0038)
        pt(x1 - 0.0038, y1)
        pt(x1 - eps, y1, "curve")
        pt(x0 + eps, y1, "line")
        pt(x0 + 0.0038, y1)
        pt(x0, y1 - 0.0038)
        pt(x0, y1 - eps, "curve")
        pt(x0, y0 + eps, "line")
        pt(x0, y0 + 0.0038)
        pt(x0 + 0.0038, y0)
        pt(x0 + eps, y0, "curve")
        pt(x1 - eps, y0, "line")
        pt(x1 - 0.0038, y0)
        pt(x1, y0 + 0.0038)
        pt(x1, y0 + eps, "curve")
        pt(x1, y1 - eps, "line")
    else:
        pt(x1, y1 - off)
        pt(x1 - off, y1)
        pt(x1 - rr, y1, "curve")
        pt(x0 + rr, y1, "line")
        pt(x0 + off, y1)
        pt(x0, y1 - off)
        pt(x0, y1 - rr, "curve")
        pt(x0, y0 + rr, "line")
        pt(x0, y0 + off)
        pt(x0 + off, y0)
        pt(x0 + rr, y0, "curve")
        pt(x1 - rr, y0, "line")
        pt(x1 - off, y0)
        pt(x1, y0 + off)
        pt(x1, y0 + rr, "curve")
        pt(x1, y1 - rr, "line")


def add_rect(contour, x0: float, y0: float, x1: float, y1: float) -> None:
    """Append a counter-clockwise sharp rectangle (for Box Drawing / Block Elements)."""
    contour.append(Point(x0, y1, "line"))
    contour.append(Point(x0, y0, "line"))
    contour.append(Point(x1, y0, "line"))
    contour.append(Point(x1, y1, "line"))


def build_serifed_I_from_template(glyph, m: dict[str, float], target_w: float, is_oblique: bool) -> None:
    """Widen I.1's 36-point contour symmetrically to target_w and center at CENTER_X."""
    pts = [(unslant_x(p.x, p.y, is_oblique), p.y, p.type) for p in glyph.contours[0]]
    xs = [x for x, _, _ in pts]
    old_xmin, old_xmax = min(xs), max(xs)
    old_cx = (old_xmin + old_xmax) / 2.0
    half_stem = m["cap_stem"] / 2.0 + 5.0
    extra = (target_w - (old_xmax - old_xmin)) / 2.0
    dx_center = CENTER_X - old_cx

    new_pts = []
    for x, y, t in pts:
        if x < old_cx - half_stem:
            x_new = x - extra + dx_center
        elif x > old_cx + half_stem:
            x_new = x + extra + dx_center
        else:
            x_new = x + dx_center
        new_pts.append((slant_x(x_new, y, is_oblique), y, t))

    c = glyph.contours[0]
    for p, (nx, ny, nt) in zip(c, new_pts):
        p.x, p.y = nx, ny

    for a in glyph.anchors:
        if a.name in ("top", "bottom", "cross"):
            a.x = slant_x(CENTER_X, a.y, is_oblique)
        elif a.name == "tonos":
            a.x = slant_x(CENTER_X - target_w / 2.0 + 30.0, a.y, is_oblique)
        elif a.name == "ogonek":
            a.x = slant_x(CENTER_X + m["cap_stem"] / 2.0, a.y, is_oblique)
        elif a.name == "right":
            a.x = slant_x(CENTER_X + m["cap_stem"] / 2.0 + 20.0, a.y, is_oblique)


def mono_lc_serif_height(m: dict[str, float], stem: float) -> float:
    """Sleek lowercase serif slab height: thinner than stem so serifs read light, never boxy."""
    return min(m["serif_h"] * 0.85, max(36.0, round(stem * 0.74, 1)))


def build_serifed_idotless(glyph, m: dict[str, float], is_oblique: bool) -> None:
    """Build 36-point serifed idotless (top-left serif + symmetric bottom serif) centered optically at CENTER_X."""
    stem = m["lc_stem"]
    serif_h = mono_lc_serif_height(m, stem)
    bot_w = min(710.0, max(585.0, 560.0 + stem * 0.40))
    bot_x0 = CENTER_X - bot_w / 2.0
    bot_x1 = CENTER_X + bot_w / 2.0
    stem_cx = CENTER_X + min(46.0, max(25.0, (bot_w - stem) * 0.10))
    sx0 = stem_cx - stem / 2.0
    sx1 = stem_cx + stem / 2.0
    top_x0 = min(sx0 - 125.0, bot_x0 + 68.0)
    top_y = max(p.y for c in glyph.contours for p in c) if glyph.contours else float(X_HEIGHT)

    r = min(m["r_cap"], max(0.0, serif_h * 0.44))
    hfrac = m["handle_frac"]

    def corner(pts_out, vx, vy, dx_in, dy_in, dx_out, dy_out, rad):
        if rad < 0.05:
            eps = 0.0145
            pts_out.append((vx + dx_in * eps * 0.26, vy + dy_in * eps * 0.26, None))
            pts_out.append((vx + dx_out * eps * 0.26, vy + dy_out * eps * 0.26, None))
            pts_out.append((vx + dx_out * eps, vy + dy_out * eps, "curve"))
        else:
            off = rad * hfrac
            pts_out.append((vx + dx_in * off, vy + dy_in * off, None))
            pts_out.append((vx + dx_out * off, vy + dy_out * off, None))
            pts_out.append((vx + dx_out * rad, vy + dy_out * rad, "curve"))

    # CCW contour matching I.1 topology:
    # bottom-left outer -> bottom-right outer -> bottom-right serif top -> inner right stem ->
    # top-right (flush with stem right) -> top-left serif outer -> top-left serif bottom -> inner left stem -> bottom-left serif top
    pts = []
    # 1. Bottom-left (bot_x0, 0)
    corner(pts, bot_x0, 0.0, 0, 1, 1, 0, r)
    # line to bottom-right
    pts.append((bot_x1 - max(r, 0.0145), 0.0, "line"))
    # 2. Bottom-right (bot_x1, 0)
    corner(pts, bot_x1, 0.0, -1, 0, 0, 1, r)
    pts.append((bot_x1, serif_h - max(r, 0.0145), "line"))
    # 3. Bottom-right serif top (bot_x1, serif_h)
    corner(pts, bot_x1, serif_h, 0, -1, -1, 0, r)
    pts.append((sx1, serif_h, "line"))
    # 4. Stem right up to top-right (sx1, top_y)
    pts.append((sx1, top_y - max(r, 0.0145), "line"))
    corner(pts, sx1, top_y, 0, -1, -1, 0, min(m["r_cap"], stem * 0.45))
    # 5. Top-left serif outer (top_x0, top_y)
    pts.append((top_x0 + max(r, 0.0145), top_y, "line"))
    corner(pts, top_x0, top_y, 1, 0, 0, -1, r)
    pts.append((top_x0, top_y - serif_h + max(r, 0.0145), "line"))
    # 6. Top-left serif bottom (top_x0, top_y - serif_h)
    corner(pts, top_x0, top_y - serif_h, 0, 1, 1, 0, r)
    pts.append((sx0, top_y - serif_h, "line"))
    # 7. Stem left down to bottom-left serif top (sx0, serif_h)
    pts.append((sx0, serif_h, "line"))
    pts.append((bot_x0 + max(r, 0.0145), serif_h, "line"))
    # 8. Bottom-left serif top (bot_x0, serif_h)
    corner(pts, bot_x0, serif_h, 1, 0, 0, -1, r)
    pts.append((bot_x0, max(r, 0.0145), "line"))

    glyph.clearContours()
    c = Contour(); glyph.contours.append(c)
    for x, y, t in pts:
        c.append(Point(slant_x(x, y, is_oblique), y, t))

    # Update anchors
    for a in glyph.anchors:
        if a.name == "top":
            a.x = slant_x(stem_cx, a.y, is_oblique)
        elif a.name == "bottom":
            a.x = slant_x(CENTER_X, a.y, is_oblique)
        elif a.name == "cross":
            a.x = slant_x(stem_cx, a.y, is_oblique)
        elif a.name == "ogonek":
            a.x = slant_x(sx1, a.y, is_oblique)


def add_top_left_serif_to_stem(glyph, m: dict[str, float], target_shift_x: float, serif_ext: float, is_oblique: bool) -> None:
    """Add a top-left horizontal serif to l.ss02 or jdotless while preserving all lower tail curves."""
    pts = [(unslant_x(p.x, p.y, is_oblique) + target_shift_x, p.y, p.type) for p in glyph.contours[0]]
    top_y = max(y for _, y, _ in pts)
    # Find stem left and stem right near top_y (points within 80 units of top_y)
    top_pts_idx = [i for i, (x, y, t) in enumerate(pts) if y >= top_y - 110]
    sx0 = min(pts[i][0] for i in top_pts_idx)
    sx1 = max(pts[i][0] for i in top_pts_idx)
    serif_h = mono_lc_serif_height(m, sx1 - sx0)
    r = min(m["r_cap"], max(0.0, serif_h * 0.44))
    hfrac = m["handle_frac"]

    # Find the contiguous block of points at the top-left corner:
    # In Allo l.ss02 and jdotless (CCW), the contour moves up the right stem to top-right, across top to top-left, then down left stem!
    # Find the top-right curve point (t == 'curve', y == top_y, largest x) and rotate it to index 0.
    top_oncurve = [i for i, (x, y, t) in enumerate(pts) if abs(y - top_y) < 1.5 and t == "curve"]
    tr_idx = max(top_oncurve, key=lambda i: pts[i][0])
    pts = pts[tr_idx:] + pts[:tr_idx]
    # Now pts[0] is top-right curve, pts[1..4] is the top-left corner (line, None, None, curve),
    # and pts[5:] runs down the left stem, around the bottom, and up the right stem back to pts[0].
    first_top = 0
    tl_end = 4

    # Keep pts[:first_top+1] (up through top-right curve end on y == top_y) and pts[tl_end+1:] (down left stem)!
    top_x0 = sx0 - serif_ext

    def corner_pts(vx, vy, dx_in, dy_in, dx_out, dy_out, rad):
        out = []
        if rad < 0.05:
            eps = 0.0145
            out.append((vx + dx_in * eps * 0.26, vy + dy_in * eps * 0.26, None))
            out.append((vx + dx_out * eps * 0.26, vy + dy_out * eps * 0.26, None))
            out.append((vx + dx_out * eps, vy + dy_out * eps, "curve"))
        else:
            off = rad * hfrac
            out.append((vx + dx_in * off, vy + dy_in * off, None))
            out.append((vx + dx_out * off, vy + dy_out * off, None))
            out.append((vx + dx_out * rad, vy + dy_out * rad, "curve"))
        return out

    splice = []
    splice.append((top_x0 + max(r, 0.0145), top_y, "line"))
    splice.extend(corner_pts(top_x0, top_y, 1, 0, 0, -1, r))
    splice.append((top_x0, top_y - serif_h + max(r, 0.0145), "line"))
    splice.extend(corner_pts(top_x0, top_y - serif_h, 0, 1, 1, 0, r))
    splice.append((sx0, top_y - serif_h, "line"))

    # Also ensure the first point after splice (on left stem) is a line if it was line
    combined = pts[: first_top + 1] + splice + pts[tl_end + 1 :]

    glyph.clearContours()
    c = Contour(); glyph.contours.append(c)
    for x, y, t in combined:
        c.append(Point(slant_x(x, y, is_oblique), y, t))

    for a in glyph.anchors:
        ax = unslant_x(a.x, a.y, is_oblique) + target_shift_x
        if a.name in ("top", "cross", "bottom"):
            # Keep top/cross centered on stem for j/l
            pass
        a.x = slant_x(ax, a.y, is_oblique)


def piecewise_stem_compress(glyph, target_w: float, is_oblique: bool) -> None:
    """Compress m or H/Hbar by preserving vertical stem intervals and compressing gaps between them."""
    pts = [(unslant_x(p.x, p.y, is_oblique), p.y, p.type) for c in glyph.contours for p in c]
    xs = [x for x, _, _ in pts]
    xmin, xmax = min(xs), max(xs)
    orig_w = xmax - xmin
    if orig_w <= target_w + 1.0:
        shift_glyph_x(glyph, CENTER_X - (xmin + xmax) / 2.0)
        return

    # Find vertical stem intervals by slicing around y = 350..450
    # Collect x positions of near-vertical segments crossing y = 400
    cross_xs = []
    for c in glyph.contours:
        c_pts = [(unslant_x(p.x, p.y, is_oblique), p.y) for p in c]
        n = len(c_pts)
        for i in range(n):
            x0, y0 = c_pts[i]
            x1, y1 = c_pts[(i + 1) % n]
            if (y0 <= 400.0 < y1) or (y1 <= 400.0 < y0):
                t = (400.0 - y0) / (y1 - y0)
                cross_xs.append(x0 + t * (x1 - x0))
    cross_xs.sort()
    stems = [(cross_xs[i], cross_xs[i + 1]) for i in range(0, len(cross_xs) - 1, 2)]
    if not stems:
        scale_glyph_x_around(glyph, (xmin + xmax) / 2.0, target_w / orig_w, is_oblique)
        shift_glyph_x(glyph, CENTER_X - (xmin + xmax) / 2.0)
        return

    # Compute total stem width and gap width
    # Build piecewise linear map from [xmin..xmax] -> [new_xmin..new_xmin+target_w]
    # Segments: [xmin..s0_l], [s0_l..s0_r], [s0_r..s1_l], ... [sN_r..xmax]
    total_stem_w = sum(r - l for l, r in stems)
    total_gap_w = orig_w - total_stem_w
    if total_gap_w <= 10.0:
        scale_glyph_x_around(glyph, (xmin + xmax) / 2.0, target_w / orig_w, is_oblique)
        shift_glyph_x(glyph, CENTER_X - (xmin + xmax) / 2.0)
        return
    if total_stem_w > target_w * 0.85:
        stem_scale = (target_w * 0.84) / total_stem_w
        gap_scale = max(0.18, (target_w - total_stem_w * stem_scale) / total_gap_w)
    else:
        stem_scale = 1.0
        gap_scale = max(0.25, (target_w - total_stem_w) / total_gap_w)

    # Build breakpoint list (old_x, new_x)
    breaks = [(xmin, 0.0)]
    cur_new = 0.0
    cur_old = xmin
    for sl, sr in stems:
        if sl > cur_old:
            cur_new += (sl - cur_old) * gap_scale
            breaks.append((sl, cur_new))
            cur_old = sl
        cur_new += (sr - cur_old) * stem_scale
        breaks.append((sr, cur_new))
        cur_old = sr
    if xmax > cur_old:
        cur_new += (xmax - cur_old) * gap_scale
        breaks.append((xmax, cur_new))

    # Center result around CENTER_X
    out_offset = CENTER_X - cur_new / 2.0

    def map_x(x: float) -> float:
        if x <= breaks[0][0]:
            return out_offset + breaks[0][1] + (x - breaks[0][0]) * gap_scale
        for (ox0, nx0), (ox1, nx1) in zip(breaks, breaks[1:]):
            if x <= ox1:
                if ox1 - ox0 < 1e-6:
                    return out_offset + nx0
                t = (x - ox0) / (ox1 - ox0)
                return out_offset + nx0 + t * (nx1 - nx0)
        return out_offset + breaks[-1][1] + (x - breaks[-1][0]) * gap_scale

    for c in glyph.contours:
        for p in c:
            u = unslant_x(p.x, p.y, is_oblique)
            p.x = slant_x(map_x(u), p.y, is_oblique)
    for a in glyph.anchors:
        u = unslant_x(a.x, a.y, is_oblique)
        a.x = slant_x(map_x(u), a.y, is_oblique)


def counter_aware_round_compress(glyph, target_w: float, is_oblique: bool) -> None:
    """Condense round/counter glyphs (O, Q, C, G, zero, D, U, etc.) preserving vertical wall thickness."""
    if len(glyph.contours) < 2:
        # Open counter (C, G, U) -> scale + mild normal compensation
        normal_compensated_compress(glyph, target_w, is_oblique, comp_strength=0.6)
        return
    c0_xs = [unslant_x(p.x, p.y, is_oblique) for p in glyph.contours[0]]
    c1_xs = [unslant_x(p.x, p.y, is_oblique) for p in glyph.contours[1]]
    out_min, out_max = min(c0_xs), max(c0_xs)
    in_min, in_max = min(c1_xs), max(c1_xs)
    out_w = out_max - out_min
    in_w = in_max - in_min
    if out_w <= target_w + 1.0:
        shift_glyph_x(glyph, CENTER_X - (out_min + out_max) / 2.0)
        return

    # Measure side wall thickness
    left_wall = max(0.0, in_min - out_min)
    right_wall = max(0.0, out_max - in_max)
    if left_wall < 15.0 or right_wall < 15.0 or in_w < 100.0:
        normal_compensated_compress(glyph, target_w, is_oblique, comp_strength=0.6)
        return

    out_cx = (out_min + out_max) / 2.0
    in_cx = (in_min + in_max) / 2.0
    sx_out = target_w / out_w
    # Keep ~92%..100% of wall thickness so heavy Black weights keep generous counters
    wall_keep = 0.94 if (left_wall + right_wall) > 500 else 1.0
    new_in_w = max(80.0, target_w - (left_wall + right_wall) * wall_keep)
    sx_in = new_in_w / in_w
    new_in_cx = CENTER_X + (in_cx - out_cx) * sx_out

    for ci, c in enumerate(glyph.contours):
        cx_old = out_cx if ci == 0 else in_cx
        cx_new = CENTER_X if ci == 0 else new_in_cx
        sx = sx_out if ci == 0 else sx_in
        for p in c:
            u = unslant_x(p.x, p.y, is_oblique)
            u2 = cx_new + (u - cx_old) * sx
            p.x = slant_x(u2, p.y, is_oblique)

    for a in glyph.anchors:
        u = unslant_x(a.x, a.y, is_oblique)
        a.x = slant_x(CENTER_X + (u - out_cx) * sx_out, a.y, is_oblique)


def normal_compensated_compress(
    glyph,
    target_w: float,
    is_oblique: bool,
    comp_strength: float = 0.72,
    stem_ref: float = 178.0,
) -> None:
    """Scale glyph horizontally to target_w around CENTER_X with normal-aware stem compensation."""
    pts_all = [unslant_x(p.x, p.y, is_oblique) for c in glyph.contours for p in c]
    if not pts_all:
        return
    xmin, xmax = min(pts_all), max(pts_all)
    orig_w = xmax - xmin
    cx = (xmin + xmax) / 2.0
    if orig_w <= target_w + 1.0:
        shift_glyph_x(glyph, CENTER_X - cx)
        return

    sx = target_w / orig_w

    # In UFO, (ty, -tx) points outward along X for both CCW outer contours and CW inner holes.
    normals_per_contour = []
    for c in glyph.contours:
        c_pts = [(unslant_x(p.x, p.y, is_oblique), p.y, p.type) for p in c]
        n = len(c_pts)
        on_idx = [i for i, (_, _, t) in enumerate(c_pts) if t is not None]
        if len(on_idx) < 3:
            normals_per_contour.append([0.0] * n)
            continue
        nx_list = [0.0] * n
        for idx in range(n):
            x_prev, y_prev, _ = c_pts[(idx - 1) % n]
            x_next, y_next, _ = c_pts[(idx + 1) % n]
            tx = x_next - x_prev
            ty = y_next - y_prev
            length = math.hypot(tx, ty)
            if length > 1e-6:
                nx_list[idx] = ty / length
        normals_per_contour.append(nx_list)

    # Restore exact (1 - sx) * stem/2 per wall face so stems keep natural weight without over-thickening Thin
    half_wall = min(145.0, max(12.0, stem_ref * 0.52))
    boost = min(48.0, max(0.0, half_wall * (1.0 - sx) * comp_strength))

    for c, nx_list in zip(glyph.contours, normals_per_contour):
        for p, nx in zip(c, nx_list):
            u = unslant_x(p.x, p.y, is_oblique)
            u2 = CENTER_X + (u - cx) * sx + boost * nx
            p.x = slant_x(u2, p.y, is_oblique)

    # Re-center & clamp exact width to target_w
    pts2 = [unslant_x(p.x, p.y, is_oblique) for c in glyph.contours for p in c]
    xmin2, xmax2 = min(pts2), max(pts2)
    w2 = xmax2 - xmin2
    cx2 = (xmin2 + xmax2) / 2.0
    fix_s = (target_w / w2) if w2 > target_w else 1.0
    for c in glyph.contours:
        for p in c:
            u = unslant_x(p.x, p.y, is_oblique)
            u2 = CENTER_X + (u - cx2) * fix_s
            p.x = slant_x(u2, p.y, is_oblique)

    for a in glyph.anchors:
        u = unslant_x(a.x, a.y, is_oblique)
        a.x = slant_x(CENTER_X + (u - cx) * sx, a.y, is_oblique)


def widen_narrow_glyph(glyph, extra_right: float, is_oblique: bool) -> None:
    """Widen r, f, t rightward strictly past the vertical stem so stem weight stays exact."""
    pts = [(unslant_x(p.x, p.y, is_oblique), p.y) for c in glyph.contours for p in c]
    xmin, xmax = min(x for x, _ in pts), max(x for x, _ in pts)
    stem_pts = [x for x, y in pts if 450.0 <= y <= 750.0 and x < xmin + (xmax - xmin) * 0.72]
    stem_r = max(stem_pts) if stem_pts else (xmin + (xmax - xmin) * 0.55)
    thresh = stem_r + 12.0
    for c in glyph.contours:
        for p in c:
            u = unslant_x(p.x, p.y, is_oblique)
            if u > thresh:
                t = min(1.0, (u - thresh) / max(1.0, xmax - thresh))
                u += extra_right * t
                p.x = slant_x(u, p.y, is_oblique)
    pts_new = [unslant_x(p.x, p.y, is_oblique) for c in glyph.contours for p in c]
    dx = CENTER_X - (min(pts_new) + max(pts_new)) / 2.0
    shift_glyph_x(glyph, dx)


def widen_l_tail(glyph, extra_tail: float, is_oblique: bool) -> None:
    """Extend l.ss02's bottom-right curved tail rightward without altering the vertical stem."""
    if extra_tail <= 1.0 or not glyph.contours:
        return
    pts = [(unslant_x(p.x, p.y, is_oblique), p.y) for c in glyph.contours for p in c]
    top_y = max(y for _, y in pts)
    sx1 = max(x for x, y in pts if y >= top_y - 120.0)
    xmax = max(x for x, _ in pts)
    thresh = sx1 + 10.0
    if xmax <= thresh + 10.0:
        return
    for c in glyph.contours:
        for p in c:
            u = unslant_x(p.x, p.y, is_oblique)
            if u > thresh:
                t = min(1.0, (u - thresh) / (xmax - thresh))
                p.x = slant_x(u + extra_tail * t, p.y, is_oblique)


def widen_j_hook(glyph, extra_left: float, is_oblique: bool) -> None:
    """Extend jdotless's descender hook leftward cleanly without altering upper stem thickness."""
    if extra_left <= 1.0 or not glyph.contours:
        return
    pts = [(unslant_x(p.x, p.y, is_oblique), p.y) for c in glyph.contours for p in c]
    sx0 = min(x for x, y in pts if y >= 850.0)
    xmin = min(x for x, _ in pts)
    if sx0 <= xmin + 20.0:
        return
    for c in glyph.contours:
        for p in c:
            u = unslant_x(p.x, p.y, is_oblique)
            if p.y < 0.0 and u < sx0 + 40.0:
                if u <= sx0:
                    t = min(1.0, (sx0 - u) / (sx0 - xmin))
                    p.x = slant_x(u - extra_left * t, p.y, is_oblique)
                elif p.y < -300.0:
                    # Smooth bottom curve handle adjustment right below the stem
                    p.x = slant_x(u - extra_left * 0.28, p.y, is_oblique)


def build_coding_ligature_glyphs(font, m: dict[str, float], is_oblique: bool) -> None:
    """Create 1-to-1 cell-preserving coding ligature pieces (every glyph advance == MONO_WIDTH)."""
    r_bar = min(m["r_cap"], m["bar_h"] * 0.42)
    hfrac = m["handle_frac"]
    bh = m["bar_h"]
    ymid = m["bar_mid_y"]
    by0, by1 = ymid - bh / 2.0, ymid + bh / 2.0
    eq_bh = m["eq_bar_h"]
    eq_y0, eq_y1 = m["eq_y0"], m["eq_y1"]
    eq_mid = (eq_y0 + eq_y1) / 2.0

    margin = 122.0

    def ensure_glyph(name):
        if name in font:
            g = font[name]
            g.clearContours()
            g.clearComponents()
            g.clearAnchors()
        else:
            g = font.newGlyph(name)
        g.width = MONO_WIDTH
        return g

    def add_bar(g, x0, x1, y0, y1, round_left=True, round_right=True):
        c = Contour(); g.contours.append(c)
        if not round_left and not round_right:
            pts = [(x1, y1, "line"), (x0, y1, "line"), (x0, y0, "line"), (x1, y0, "line")]
            for x, y, t in pts:
                c.append(Point(slant_x(x, y, is_oblique), y, t))
            return
        add_rounded_rect(c, x0, y0, x1, y1, r_bar, hfrac, is_oblique)
        if r_bar >= 0.05:
            for p in c:
                ux = unslant_x(p.x, p.y, is_oblique)
                if not round_right and ux > x1 - r_bar - 1.0:
                    p.x = slant_x(x1, p.y, is_oblique)
                    if abs(p.y - y0) < r_bar:
                        p.y = y0
                    elif abs(p.y - y1) < r_bar:
                        p.y = y1
                if not round_left and ux < x0 + r_bar + 1.0:
                    p.x = slant_x(x0, p.y, is_oblique)
                    if abs(p.y - y0) < r_bar:
                        p.y = y0
                    elif abs(p.y - y1) < r_bar:
                        p.y = y1

    def add_chevron(g, tip_x, mid_y, wing_dx, wing_dy, stroke, pointing_right=True):
        """Crisp angled arrowhead/chevron matching Allo's stroke weight (CCW)."""
        d = 1.0 if pointing_right else -1.0
        wx = tip_x - d * wing_dx
        ang = math.atan2(wing_dy, wing_dx)
        dx_t = stroke * math.sin(ang) * 0.55
        dy_t = stroke / max(0.4, math.cos(ang)) * 0.55
        c = Contour(); g.contours.append(c)
        if pointing_right:
            pts = [
                (wx, mid_y + wing_dy, "line"),
                (tip_x + dx_t * 0.4, mid_y + stroke * 0.35, "line"),
                (tip_x + dx_t * 0.4, mid_y - stroke * 0.35, "line"),
                (wx, mid_y - wing_dy, "line"),
                (wx - dx_t, mid_y - wing_dy + dy_t * 0.6, "line"),
                (tip_x - d * stroke * 0.85, mid_y, "line"),
                (wx - dx_t, mid_y + wing_dy - dy_t * 0.6, "line"),
            ]
        else:
            pts = [
                (wx, mid_y + wing_dy, "line"),
                (wx + dx_t, mid_y + wing_dy - dy_t * 0.6, "line"),
                (tip_x - d * stroke * 0.85, mid_y, "line"),
                (wx + dx_t, mid_y - wing_dy + dy_t * 0.6, "line"),
                (wx, mid_y - wing_dy, "line"),
                (tip_x - dx_t * 0.4, mid_y - stroke * 0.35, "line"),
                (tip_x - dx_t * 0.4, mid_y + stroke * 0.35, "line"),
            ]
        for x, y, t in pts:
            c.append(Point(slant_x(x, y, is_oblique), y, t))

    def add_bars(g, ys, x0, x1, r_left, r_right):
        for y_a, y_b in ys:
            add_bar(g, x0, x1, y_a, y_b, round_left=r_left, round_right=r_right)

    # Single shafts for ->, <-, -->, <--, <->
    g = ensure_glyph("hyphen.rarrow")
    add_bar(g, margin, MONO_WIDTH, by0, by1, round_left=True, round_right=False)

    g = ensure_glyph("hyphen.larrow")
    add_bar(g, 0.0, MONO_WIDTH - margin, by0, by1, round_left=False, round_right=True)

    g = ensure_glyph("hyphen.conn")
    add_bar(g, 0.0, MONO_WIDTH, by0, by1, round_left=False, round_right=False)

    g = ensure_glyph("greater.rarrow")
    add_bar(g, 0.0, MONO_WIDTH - margin - bh * 0.55, by0, by1, round_left=False, round_right=False)
    add_chevron(g, MONO_WIDTH - margin, ymid, 390.0, 370.0, bh, pointing_right=True)

    g = ensure_glyph("less.larrow")
    add_bar(g, margin + bh * 0.55, MONO_WIDTH, by0, by1, round_left=False, round_right=False)
    add_chevron(g, margin, ymid, 390.0, 370.0, bh, pointing_right=False)

    # Double shafts for =>, <=>, ==, !=
    dbl_ys = [(eq_y0, eq_y0 + eq_bh), (eq_y1 - eq_bh, eq_y1)]

    g = ensure_glyph("equal.rarrow")
    add_bars(g, dbl_ys, margin, MONO_WIDTH, True, False)

    g = ensure_glyph("equal.dconn")
    add_bars(g, dbl_ys, 0.0, MONO_WIDTH, False, False)

    g = ensure_glyph("equal.eq_l")
    add_bars(g, dbl_ys, margin, MONO_WIDTH, True, False)

    g = ensure_glyph("equal.eq_r")
    add_bars(g, dbl_ys, 0.0, MONO_WIDTH - margin, False, True)

    g = ensure_glyph("greater.drarrow")
    end_bar = MONO_WIDTH - margin - 220.0
    add_bars(g, dbl_ys, 0.0, end_bar, False, False)
    add_chevron(g, MONO_WIDTH - margin, eq_mid, 410.0, 395.0, eq_bh, pointing_right=True)

    g = ensure_glyph("less.dlarrow")
    start_bar = margin + 220.0
    add_bars(g, dbl_ys, start_bar, MONO_WIDTH, False, False)
    add_chevron(g, margin, eq_mid, 410.0, 395.0, eq_bh, pointing_right=False)

    # Triple shafts for === and !==
    tri_ratio = 1.0 if eq_bh < 60.0 else max(0.72, 0.84 - (eq_bh - 60.0) * 0.00045)
    tri_bh = round(eq_bh * tri_ratio, 1)
    tri_pitch = max(tri_bh + max(95.0, tri_bh * 0.72), (eq_y1 - eq_y0 - eq_bh) * 0.72)
    tri_ys = [
        (eq_mid - tri_pitch - tri_bh * 0.5, eq_mid - tri_pitch + tri_bh * 0.5),
        (eq_mid - tri_bh * 0.5, eq_mid + tri_bh * 0.5),
        (eq_mid + tri_pitch - tri_bh * 0.5, eq_mid + tri_pitch + tri_bh * 0.5),
    ]

    g = ensure_glyph("equal.eq3_l")
    add_bars(g, tri_ys, margin, MONO_WIDTH, True, False)

    g = ensure_glyph("equal.eq3_m")
    add_bars(g, tri_ys, 0.0, MONO_WIDTH, False, False)

    g = ensure_glyph("equal.eq3_r")
    add_bars(g, tri_ys, 0.0, MONO_WIDTH - margin, False, True)

    # Exact diagonal slash split across x = MONO_WIDTH | x = 0 for != and !==
    sw = max(76.0, eq_bh * 1.08)
    dx_slash = 225.0
    slash_bot_y = min(eq_y0, tri_ys[0][0]) - 210.0
    slash_top_y = max(eq_y1, tri_ys[-1][1]) + 210.0
    t_low = max(0.05, 0.5 - sw / (4.0 * dx_slash))
    t_high = min(0.95, 0.5 + sw / (4.0 * dx_slash))
    y_cross_bot = slash_bot_y + t_low * (slash_top_y - slash_bot_y)
    y_cross_top = slash_bot_y + t_high * (slash_top_y - slash_bot_y)

    def add_slash_left(target_g):
        c_sl = Contour(); target_g.contours.append(c_sl)
        for x, y in [
            (MONO_WIDTH - dx_slash - sw * 0.5, slash_bot_y),
            (MONO_WIDTH - dx_slash + sw * 0.5, slash_bot_y),
            (MONO_WIDTH, y_cross_bot),
            (MONO_WIDTH, y_cross_top),
        ]:
            c_sl.append(Point(slant_x(x, y, is_oblique), y, "line"))

    def add_slash_right(target_g):
        c_sr = Contour(); target_g.contours.append(c_sr)
        for x, y in [
            (0.0, y_cross_bot),
            (dx_slash + sw * 0.5, slash_top_y),
            (dx_slash - sw * 0.5, slash_top_y),
            (0.0, y_cross_top),
        ]:
            c_sr.append(Point(slant_x(x, y, is_oblique), y, "line"))

    g = ensure_glyph("exclam.neq")
    add_bars(g, dbl_ys, margin, MONO_WIDTH, True, False)
    add_slash_left(g)

    g = ensure_glyph("equal.neq")
    add_bars(g, dbl_ys, 0.0, MONO_WIDTH - margin, False, True)
    add_slash_right(g)

    g = ensure_glyph("exclam.neq3")
    add_bars(g, tri_ys, margin, MONO_WIDTH, True, False)
    add_slash_left(g)

    g = ensure_glyph("equal.neq3_m")
    add_bars(g, tri_ys, 0.0, MONO_WIDTH, False, False)
    add_slash_right(g)

    # <= and >= (x-height-aligned, open chevron across all weights Thin..Black)
    leq_bh = round(eq_bh * (1.0 if eq_bh < 120.0 else max(0.76, 0.96 - (eq_bh - 120.0) * 0.00095)), 1)
    bar_pad = 470.0  # underline bar width = 2*MONO_WIDTH - 2*470 = 1180 centered on seam
    chev_pad = 520.0  # chevron width = 2*(1060 - 520) = 1080 centered on seam
    leq_bar_y0 = max(65.0, 145.0 - leq_bh * 0.30)
    leq_bar_y1 = leq_bar_y0 + leq_bh
    gap_y = max(105.0, min(145.0, leq_bh * 0.68))
    chev_bot_y = leq_bar_y1 + gap_y
    chev_top_y = 1135.0
    chev_mid_y = (chev_bot_y + chev_top_y) * 0.5
    half_h = (chev_top_y - chev_bot_y) * 0.5
    chev_run = 2.0 * (MONO_WIDTH - chev_pad)

    ang_leq = math.atan2(half_h, chev_run)
    v_half = (leq_bh * 0.5) / math.cos(ang_leq)
    dx_tip = leq_bh * 0.14
    v_tip = leq_bh * 0.44
    dx_in = min(340.0, (leq_bh * 0.5) / max(0.28, math.sin(ang_leq)))

    # Height of wing arms right at seam (x = MONO_WIDTH, halfway from chev_pad to wing end)
    y_seam_off = half_h * 0.5

    g_lel = ensure_glyph("less.leq")
    g_eql = ensure_glyph("equal.leq")
    add_bar(g_lel, bar_pad, MONO_WIDTH, leq_bar_y0, leq_bar_y1, round_left=True, round_right=False)
    c_lel = Contour(); g_lel.contours.append(c_lel)
    lel_pts = [
        (MONO_WIDTH, chev_mid_y + y_seam_off + v_half),
        (chev_pad - dx_tip, chev_mid_y + v_tip),
        (chev_pad - dx_tip, chev_mid_y - v_tip),
        (MONO_WIDTH, chev_mid_y - y_seam_off - v_half),
        (MONO_WIDTH, chev_mid_y - y_seam_off + v_half),
        (chev_pad + dx_in, chev_mid_y),
        (MONO_WIDTH, chev_mid_y + y_seam_off - v_half),
    ]
    for x, y in lel_pts:
        c_lel.append(Point(slant_x(x, y, is_oblique), y, "line"))

    add_bar(g_eql, 0.0, MONO_WIDTH - bar_pad, leq_bar_y0, leq_bar_y1, round_left=False, round_right=True)
    for y_at_0, y_at_end in [
        (chev_mid_y + y_seam_off, chev_mid_y + half_h),
        (chev_mid_y - y_seam_off, chev_mid_y - half_h),
    ]:
        c_w = Contour(); g_eql.contours.append(c_w)
        for x, y in [
            (MONO_WIDTH - chev_pad, y_at_end + v_half),
            (0.0, y_at_0 + v_half),
            (0.0, y_at_0 - v_half),
            (MONO_WIDTH - chev_pad, y_at_end - v_half),
        ]:
            c_w.append(Point(slant_x(x, y, is_oblique), y, "line"))

    # >= mirror (x -> MONO_WIDTH - x, reversed for CCW)
    g_gr = ensure_glyph("greater.geq")
    g_eqr = ensure_glyph("equal.geq")
    add_bar(g_eqr, 0.0, MONO_WIDTH - bar_pad, leq_bar_y0, leq_bar_y1, round_left=False, round_right=True)
    c_eqr = Contour(); g_eqr.contours.append(c_eqr)
    for x, y in reversed(lel_pts):
        c_eqr.append(Point(slant_x(MONO_WIDTH - x, y, is_oblique), y, "line"))

    add_bar(g_gr, bar_pad, MONO_WIDTH, leq_bar_y0, leq_bar_y1, round_left=True, round_right=False)
    for y_at_0, y_at_end in [
        (chev_mid_y + y_seam_off, chev_mid_y + half_h),
        (chev_mid_y - y_seam_off, chev_mid_y - half_h),
    ]:
        c_w = Contour(); g_gr.contours.append(c_w)
        wing_pts = [
            (MONO_WIDTH - chev_pad, y_at_end + v_half),
            (0.0, y_at_0 + v_half),
            (0.0, y_at_0 - v_half),
            (MONO_WIDTH - chev_pad, y_at_end - v_half),
        ]
        for x, y in reversed(wing_pts):
            c_w.append(Point(slant_x(MONO_WIDTH - x, y, is_oblique), y, "line"))


def build_box_drawing_and_blocks(font, m: dict[str, float]) -> None:
    """Add full Unicode Box Drawing (U+2500..257F), Block Elements (U+2580..259F), and Powerline glyphs."""
    W = float(MONO_WIDTH)
    Y0, Y1 = float(DESCENDER), float(ASCENDER)  # -494, 1984
    CX, CY = W / 2.0, (Y0 + Y1) / 2.0  # 640, 745

    # Light & heavy stroke widths scale cleanly with weight
    t_light = round(max(84.0, min(200.0, 72.0 + m["cap_stem"] * 0.34)), 1)
    t_heavy = round(t_light * 1.9, 1)
    t_double = round(max(64.0, min(140.0, t_light * 0.72)), 1)
    d_gap = round(t_double * 1.5, 1)  # center-to-center offset = t_double + d_gap? No, offset from CY = (t_double + d_gap)/2

    def new_u(gname, code):
        if gname in font:
            g = font[gname]
            g.clearContours()
            g.clearComponents()
        else:
            g = font.newGlyph(gname)
        g.width = MONO_WIDTH
        g.unicodes = [code]
        return g

    def draw_box_segments(g, left=0, right=0, up=0, down=0):
        """Draw horizontal/vertical arms: 0=none, 1=light, 2=heavy, 3=double."""
        # Simple clean union via axis-aligned rects for light/heavy, and exact split rects for double
        hl = t_light / 2.0
        hh = t_heavy / 2.0
        hd = t_double / 2.0
        off = (t_double + d_gap) / 2.0

        def thick(kind):
            return hl if kind == 1 else hh

        # Single non-overlapping CCW union contour for all light/heavy segments & junctions
        if 3 not in (left, right, up, down):
            lu = thick(up) if up else 0.0
            ld = thick(down) if down else 0.0
            ll = thick(left) if left else 0.0
            lr = thick(right) if right else 0.0
            rects = []
            if left:
                rects.append((0.0, CY - ll, CX + max(lu, ld), CY + ll))
            if right:
                rects.append((CX - max(lu, ld), CY - lr, W, CY + lr))
            if down:
                rects.append((CX - ld, Y0, CX + ld, CY + max(ll, lr)))
            if up:
                rects.append((CX - lu, CY - max(ll, lr), CX + lu, Y1))
            xs_g = sorted({x for x0, _, x1, _ in rects for x in (x0, x1)})
            ys_g = sorted({y for _, y0, _, y1 in rects for y in (y0, y1)})
            # Trace boundary edges of filled cells
            nx, ny = len(xs_g) - 1, len(ys_g) - 1
            filled = [[False] * ny for _ in range(nx)]
            for ix in range(nx):
                mx = (xs_g[ix] + xs_g[ix + 1]) * 0.5
                for iy in range(ny):
                    my = (ys_g[iy] + ys_g[iy + 1]) * 0.5
                    filled[ix][iy] = any(x0 < mx < x1 and y0 < my < y1 for x0, y0, x1, y1 in rects)
            # Directed boundary edges around filled cells (CCW: bottom->right, right->up, top->left, left->down)
            next_edge = {}
            for ix in range(nx):
                for iy in range(ny):
                    if not filled[ix][iy]:
                        continue
                    if iy == 0 or not filled[ix][iy - 1]:
                        next_edge.setdefault((ix, iy), []).append((ix + 1, iy))
                    if ix == nx - 1 or not filled[ix + 1][iy]:
                        next_edge.setdefault((ix + 1, iy), []).append((ix + 1, iy + 1))
                    if iy == ny - 1 or not filled[ix][iy + 1]:
                        next_edge.setdefault((ix + 1, iy + 1), []).append((ix, iy + 1))
                    if ix == 0 or not filled[ix - 1][iy]:
                        next_edge.setdefault((ix, iy + 1), []).append((ix, iy))
            # Walk loops and drop collinear midpoints
            visited = set()
            for start, dsts in list(next_edge.items()):
                for d0 in dsts:
                    if (start, d0) in visited:
                        continue
                    loop = [start]
                    prev, cur = start, d0
                    visited.add((prev, cur))
                    while cur != start:
                        loop.append(cur)
                        dx_in, dy_in = cur[0] - prev[0], cur[1] - prev[1]
                        cands = [n for n in next_edge[cur] if (cur, n) not in visited]
                        # Prefer turning right/left cleanly (never crossing)
                        nxt = min(cands, key=lambda n: (dx_in * (n[1] - cur[1]) - dy_in * (n[0] - cur[0])) <= 0) if len(cands) > 1 else cands[0]
                        visited.add((cur, nxt))
                        prev, cur = cur, nxt
                    # Convert grid indices to (x, y) and drop collinear vertices
                    raw = [(xs_g[ix], ys_g[iy]) for ix, iy in loop]
                    clean = []
                    m_len = len(raw)
                    for k in range(m_len):
                        p_a, p_b, p_c = raw[(k - 1) % m_len], raw[k], raw[(k + 1) % m_len]
                        if (abs(p_a[0] - p_b[0]) < 1e-6 and abs(p_b[0] - p_c[0]) < 1e-6) or (abs(p_a[1] - p_b[1]) < 1e-6 and abs(p_b[1] - p_c[1]) < 1e-6):
                            continue
                        clean.append(p_b)
                    c_u = Contour(); g.contours.append(c_u)
                    for x, y in clean:
                        c_u.append(Point(x, y, "line"))
            return

        # Double-line junctions (light=1 or double=3)
        # Outer extent of double = off + hd; inner extent = off - hd
        def h_bounds(kind):
            if kind == 3:
                return [(CY - off - hd, CY - off + hd), (CY + off - hd, CY + off + hd)]
            if kind == 1:
                return [(CY - hl, CY + hl)]
            return []

        def v_bounds(kind):
            if kind == 3:
                return [(CX - off - hd, CX - off + hd), (CX + off - hd, CX + off + hd)]
            if kind == 1:
                return [(CX - hl, CX + hl)]
            return []

        # Horizontal bars
        v_outer_l = CX - (off + hd if 3 in (up, down) else (hl if max(up, down) == 1 else 0.0))
        v_outer_r = CX + (off + hd if 3 in (up, down) else (hl if max(up, down) == 1 else 0.0))
        h_outer_b = CY - (off + hd if 3 in (left, right) else (hl if max(left, right) == 1 else 0.0))
        h_outer_t = CY + (off + hd if 3 in (left, right) else (hl if max(left, right) == 1 else 0.0))

        # Special clean 2-stroke corners & T-junctions
        if left == 3 and right == 3:
            for yb, yt in h_bounds(3):
                if up == 0 and down == 0:
                    add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), 0.0, yb, W, yt)
                else:
                    # Leave inner channel open where vertical double/single crosses
                    if (up == 3 and yb > CY) or (down == 3 and yt < CY):
                        add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), 0.0, yb, CX - off + hd, yt)
                        add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), CX + off - hd, yb, W, yt)
                    else:
                        add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), 0.0, yb, W, yt)
        else:
            if left:
                for idx, (yb, yt) in enumerate(h_bounds(left)):
                    x_end = v_outer_r if (left == 3 and ((idx == 0 and up and not down) or (idx == 1 and down and not up))) else (
                        CX - (off - hd if (up == 3 or down == 3) else 0.0) if left == 3 else (v_outer_r if max(up, down) else CX)
                    )
                    if up and down and left == 3:
                        x_end = CX - off + hd if (up == 3 or down == 3) else CX + hl
                    add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), 0.0, yb, x_end, yt)
            if right:
                for idx, (yb, yt) in enumerate(h_bounds(right)):
                    x_start = v_outer_l if (right == 3 and ((idx == 0 and up and not down) or (idx == 1 and down and not up))) else (
                        CX + (off - hd if (up == 3 or down == 3) else 0.0) if right == 3 else (v_outer_l if max(up, down) else CX)
                    )
                    if up and down and right == 3:
                        x_start = CX + off - hd if (up == 3 or down == 3) else CX - hl
                    add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), x_start, yb, W, yt)

        if up == 3 and down == 3:
            for xl, xr in v_bounds(3):
                if left == 0 and right == 0:
                    add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), xl, Y0, xr, Y1)
                else:
                    if (left == 3 and xr < CX) or (right == 3 and xl > CX):
                        add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), xl, Y0, xr, CY - off + hd)
                        add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), xl, CY + off - hd, xr, Y1)
                    else:
                        add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), xl, Y0, xr, Y1)
        else:
            if down:
                for idx, (xl, xr) in enumerate(v_bounds(down)):
                    y_end = h_outer_t if (down == 3 and ((idx == 0 and right and not left) or (idx == 1 and left and not right))) else (
                        CY - (off - hd if (left == 3 or right == 3) else 0.0) if down == 3 else (h_outer_t if max(left, right) else CY)
                    )
                    if left and right and down == 3:
                        y_end = CY - off + hd if (left == 3 or right == 3) else CY + hl
                    add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), xl, Y0, xr, y_end)
            if up:
                for idx, (xl, xr) in enumerate(v_bounds(up)):
                    y_start = h_outer_b if (up == 3 and ((idx == 0 and right and not left) or (idx == 1 and left and not right))) else (
                        CY + (off - hd if (left == 3 or right == 3) else 0.0) if up == 3 else (h_outer_b if max(left, right) else CY)
                    )
                    if left and right and up == 3:
                        y_start = CY + off - hd if (left == 3 or right == 3) else CY - hl
                    add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), xl, y_start, xr, Y1)

    # Full Unicode U+2500..U+254B table (left, right, up, down)
    BOX_GRID = {
        0x2500: (1, 1, 0, 0), 0x2501: (2, 2, 0, 0), 0x2502: (0, 0, 1, 1), 0x2503: (0, 0, 2, 2),
        0x250C: (0, 1, 0, 1), 0x250D: (0, 2, 0, 1), 0x250E: (0, 1, 0, 2), 0x250F: (0, 2, 0, 2),
        0x2510: (1, 0, 0, 1), 0x2511: (2, 0, 0, 1), 0x2512: (1, 0, 0, 2), 0x2513: (2, 0, 0, 2),
        0x2514: (0, 1, 1, 0), 0x2515: (0, 2, 1, 0), 0x2516: (0, 1, 2, 0), 0x2517: (0, 2, 2, 0),
        0x2518: (1, 0, 1, 0), 0x2519: (2, 0, 1, 0), 0x251A: (1, 0, 2, 0), 0x251B: (2, 0, 2, 0),
        0x251C: (0, 1, 1, 1), 0x251D: (0, 2, 1, 1), 0x251E: (0, 1, 2, 1), 0x251F: (0, 1, 1, 2),
        0x2520: (0, 1, 2, 2), 0x2521: (0, 2, 2, 1), 0x2522: (0, 2, 1, 2), 0x2523: (0, 2, 2, 2),
        0x2524: (1, 0, 1, 1), 0x2525: (2, 0, 1, 1), 0x2526: (1, 0, 2, 1), 0x2527: (1, 0, 1, 2),
        0x2528: (1, 0, 2, 2), 0x2529: (2, 0, 2, 1), 0x252A: (2, 0, 1, 2), 0x252B: (2, 0, 2, 2),
        0x252C: (1, 1, 0, 1), 0x252D: (2, 1, 0, 1), 0x252E: (1, 2, 0, 1), 0x252F: (2, 2, 0, 1),
        0x2530: (1, 1, 0, 2), 0x2531: (2, 1, 0, 2), 0x2532: (1, 2, 0, 2), 0x2533: (2, 2, 0, 2),
        0x2534: (1, 1, 1, 0), 0x2535: (2, 1, 1, 0), 0x2536: (1, 2, 1, 0), 0x2537: (2, 2, 1, 0),
        0x2538: (1, 1, 2, 0), 0x2539: (2, 1, 2, 0), 0x253A: (1, 2, 2, 0), 0x253B: (2, 2, 2, 0),
        0x253C: (1, 1, 1, 1), 0x253D: (2, 1, 1, 1), 0x253E: (1, 2, 1, 1), 0x253F: (2, 2, 1, 1),
        0x2540: (1, 1, 2, 1), 0x2541: (1, 1, 1, 2), 0x2542: (1, 1, 2, 2), 0x2543: (2, 1, 2, 1),
        0x2544: (1, 2, 2, 1), 0x2545: (2, 1, 1, 2), 0x2546: (1, 2, 1, 2), 0x2547: (2, 2, 2, 1),
        0x2548: (2, 2, 1, 2), 0x2549: (2, 1, 2, 2), 0x254A: (1, 2, 2, 2), 0x254B: (2, 2, 2, 2),
        0x2550: (3, 3, 0, 0), 0x2551: (0, 0, 3, 3),
        0x2552: (0, 3, 0, 1), 0x2553: (0, 1, 0, 3), 0x2554: (0, 3, 0, 3),
        0x2555: (3, 0, 0, 1), 0x2556: (1, 0, 0, 3), 0x2557: (3, 0, 0, 3),
        0x2558: (0, 3, 1, 0), 0x2559: (0, 1, 3, 0), 0x255A: (0, 3, 3, 0),
        0x255B: (3, 0, 1, 0), 0x255C: (1, 0, 3, 0), 0x255D: (3, 0, 3, 0),
        0x255E: (0, 3, 1, 1), 0x255F: (0, 1, 3, 3), 0x2560: (0, 3, 3, 3),
        0x2561: (3, 0, 1, 1), 0x2562: (1, 0, 3, 3), 0x2563: (3, 0, 3, 3),
        0x2564: (3, 3, 0, 1), 0x2565: (1, 1, 0, 3), 0x2566: (3, 3, 0, 3),
        0x2567: (3, 3, 1, 0), 0x2568: (1, 1, 3, 0), 0x2569: (3, 3, 3, 0),
        0x256A: (3, 3, 1, 1), 0x256B: (1, 1, 3, 3), 0x256C: (3, 3, 3, 3),
        0x2574: (1, 0, 0, 0), 0x2575: (0, 0, 1, 0), 0x2576: (0, 1, 0, 0), 0x2577: (0, 0, 0, 1),
        0x2578: (2, 0, 0, 0), 0x2579: (0, 0, 2, 0), 0x257A: (0, 2, 0, 0), 0x257B: (0, 0, 0, 2),
        0x257C: (1, 2, 0, 0), 0x257D: (0, 0, 2, 1), 0x257E: (2, 1, 0, 0), 0x257F: (0, 0, 1, 2),
    }
    for code, arms in BOX_GRID.items():
        g = new_u(f"uni{code:04X}", code)
        draw_box_segments(g, *arms)

    # Dashed horizontal & vertical lines (U+2504..250B, U+254C..254F)
    DASH_SPECS = {
        0x2504: ("h", 3, t_light), 0x2505: ("h", 3, t_heavy),
        0x2506: ("v", 3, t_light), 0x2507: ("v", 3, t_heavy),
        0x2508: ("h", 4, t_light), 0x2509: ("h", 4, t_heavy),
        0x250A: ("v", 4, t_light), 0x250B: ("v", 4, t_heavy),
        0x254C: ("h", 2, t_light), 0x254D: ("h", 2, t_heavy),
        0x254E: ("v", 2, t_light), 0x254F: ("v", 2, t_heavy),
    }
    for code, (axis, count, thick) in DASH_SPECS.items():
        g = new_u(f"uni{code:04X}", code)
        ht = thick / 2.0
        if axis == "h":
            step = W / count
            dash_w = step * 0.62
            margin = (step - dash_w) / 2.0
            for i in range(count):
                x0 = i * step + margin
                add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), x0, CY - ht, x0 + dash_w, CY + ht)
        else:
            step = (Y1 - Y0) / count
            dash_h = step * 0.62
            margin = (step - dash_h) / 2.0
            for i in range(count):
                y0 = Y0 + i * step + margin
                add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), CX - ht, y0, CX + ht, y0 + dash_h)

    # Rounded arcs U+256D..U+2570 (╭ ╮ ╯ ╰)
    arc_r = 380.0
    hl = t_light / 2.0
    kap = 0.5522847498
    ARCS = {
        0x256D: (1, -1),   # ╭ from right (W, CY) curving down to (CX, Y0)
        0x256E: (-1, -1),  # ╮ from left (0, CY) curving down to (CX, Y0)
        0x256F: (-1, 1),   # ╯ from left (0, CY) curving up to (CX, Y1)
        0x2570: (1, 1),    # ╰ from right (W, CY) curving up to (CX, Y1)
    }
    for code, (sx, sy) in ARCS.items():
        g = new_u(f"uni{code:04X}", code)
        c = Contour(); g.contours.append(c)
        # Center of quarter circle: (CX + sx * arc_r, CY + sy * arc_r)
        ocx = CX + sx * arc_r
        ocy = CY + sy * arc_r
        r_out = arc_r + hl
        r_in = arc_r - hl
        h_end_x = W if sx > 0 else 0.0
        v_end_y = Y1 if sy > 0 else Y0
        # Build CCW loop carefully
        # Start on vertical edge (CX +/- hl, v_end_y) -> straight to arc start -> outer curve -> horizontal end -> inner curve back
        pts_raw = [
            (CX - sx * hl, v_end_y, "line"),
            (CX - sx * hl, ocy - sy * 0.0, "line"),
            (CX - sx * hl, ocy - sy * r_out * kap, None),
            (ocx - sx * r_out * kap, CY - sy * hl, None),
            (ocx, CY - sy * hl, "curve"),
            (h_end_x, CY - sy * hl, "line"),
            (h_end_x, CY + sy * hl, "line"),
            (ocx, CY + sy * hl, "line"),
            (ocx - sx * r_in * kap, CY + sy * hl, None),
            (CX + sx * hl, ocy - sy * r_in * kap, None),
            (CX + sx * hl, ocy, "curve"),
            (CX + sx * hl, v_end_y, "line"),
        ]
        if sx * sy < 0:
            pts_raw = list(reversed(pts_raw))
            # Fix segment types on reversed sequence
            # Simpler: draw straight CCW by checking signed area!
        # Re-emit directly with proper segmentType when reversing
        if sx * sy < 0:
            # ╮ (-1, -1) and ╰ (1, 1) have positive area as-is, ╭ (1, -1) and ╯ (-1, 1) need reverse
            # Compute signed area of on-curve points to be 100% certain
            pass
        # Use explicit CCW points for each of the 4 cases:
        c.clear()
        if code == 0x256D:  # ╭ center (ocx=CX+arc_r, ocy=CY-arc_r)
            seq = [
                (CX - hl, Y0, "line"), (CX + hl, Y0, "line"), (CX + hl, ocy, "line"),
                (CX + hl, ocy + r_in * kap, None), (ocx - r_in * kap, CY - hl, None), (ocx, CY - hl, "curve"),
                (W, CY - hl, "line"), (W, CY + hl, "line"), (ocx, CY + hl, "line"),
                (ocx - r_out * kap, CY + hl, None), (CX - hl, ocy + r_out * kap, None), (CX - hl, ocy, "curve"),
            ]
        elif code == 0x256E:  # ╮ center (ocx=CX-arc_r, ocy=CY-arc_r)
            seq = [
                (CX - hl, Y0, "line"), (CX + hl, Y0, "line"), (CX + hl, ocy, "line"),
                (CX + hl, ocy + r_out * kap, None), (ocx + r_out * kap, CY + hl, None), (ocx, CY + hl, "curve"),
                (0.0, CY + hl, "line"), (0.0, CY - hl, "line"), (ocx, CY - hl, "line"),
                (ocx + r_in * kap, CY - hl, None), (CX - hl, ocy + r_in * kap, None), (CX - hl, ocy, "curve"),
            ]
        elif code == 0x256F:  # ╯ center (ocx=CX-arc_r, ocy=CY+arc_r)
            seq = [
                (CX + hl, Y1, "line"), (CX - hl, Y1, "line"), (CX - hl, ocy, "line"),
                (CX - hl, ocy - r_in * kap, None), (ocx + r_in * kap, CY + hl, None), (ocx, CY + hl, "curve"),
                (0.0, CY + hl, "line"), (0.0, CY - hl, "line"), (ocx, CY - hl, "line"),
                (ocx + r_out * kap, CY - hl, None), (CX + hl, ocy - r_out * kap, None), (CX + hl, ocy, "curve"),
            ]
        else:  # ╰ 0x2570 center (ocx=CX+arc_r, ocy=CY+arc_r)
            seq = [
                (CX + hl, Y1, "line"), (CX - hl, Y1, "line"), (CX - hl, ocy, "line"),
                (CX - hl, ocy - r_out * kap, None), (ocx - r_out * kap, CY - hl, None), (ocx, CY - hl, "curve"),
                (W, CY - hl, "line"), (W, CY + hl, "line"), (ocx, CY + hl, "line"),
                (ocx - r_in * kap, CY + hl, None), (CX + hl, ocy - r_in * kap, None), (CX + hl, ocy, "curve"),
            ]
        for x, y, t in seq:
            c.append(Point(x, y, t))

    # Diagonals U+2571 ╱, U+2572 ╲, U+2573 ╳
    diag_dx = hl / math.sin(math.atan2(Y1 - Y0, W))
    g_fwd = new_u("uni2571", 0x2571)
    c = Contour(); g_fwd.contours.append(c)
    for x, y in [(0.0, Y0), (diag_dx, Y0), (W, Y1), (W - diag_dx, Y1)]:
        c.append(Point(x, y, "line"))
    g_bwd = new_u("uni2572", 0x2572)
    c = Contour(); g_bwd.contours.append(c)
    for x, y in [(0.0, Y1), (W - diag_dx, Y0), (W, Y0), (diag_dx, Y1)]:
        c.append(Point(x, y, "line"))
    g_x = new_u("uni2573", 0x2573)
    for contour in (g_fwd.contours[0], g_bwd.contours[0]):
        c2 = Contour(); g_x.contours.append(c2)
        for p in contour:
            c2.append(Point(p.x, p.y, p.type))

    # Block Elements U+2580..U+259F
    H = Y1 - Y0  # 2478
    def rect_glyph(name, code, x0, y0, x1, y1):
        _g = new_u(name, code)
        _c = Contour()
        _g.contours.append(_c)
        add_rect(_c, x0, y0, x1, y1)

    rect_glyph("uni2580", 0x2580, 0.0, CY, W, Y1)  # ▀
    for k in range(1, 8):
        yk = round(Y0 + H * (k / 8.0), 1)
        rect_glyph(f"uni{0x2580 + k:04X}", 0x2580 + k, 0.0, Y0, W, yk)  # ▁..▇
    rect_glyph("uni2588", 0x2588, 0.0, Y0, W, Y1)  # █
    for k in range(1, 8):
        xk = round(W * ((8 - k) / 8.0), 1)
        rect_glyph(f"uni{0x2588 + k:04X}", 0x2588 + k, 0.0, Y0, xk, Y1)  # ▉..▏
    rect_glyph("uni2590", 0x2590, CX, Y0, W, Y1)  # ▐
    rect_glyph("uni2594", 0x2594, 0.0, round(Y1 - H / 8.0, 1), W, Y1)  # ▔
    rect_glyph("uni2595", 0x2595, round(W * 7.0 / 8.0, 1), Y0, W, Y1)  # ▕

    # Shades U+2591..2593 (░ ▒ ▓) as crisp tiling grids
    def draw_shade(code, cols, rows, density):
        g = new_u(f"uni{code:04X}", code)
        cw = W / cols
        ch = H / rows
        for r in range(rows):
            for c_idx in range(cols):
                draw = False
                if density == 0.25:
                    draw = (r % 2 == 0) and (c_idx % 2 == (r // 2) % 2)
                elif density == 0.50:
                    draw = (r + c_idx) % 2 == 0
                elif density == 0.75:
                    draw = not ((r % 2 == 0) and (c_idx % 2 == (r // 2) % 2))
                if draw:
                    add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), round(c_idx * cw, 1), round(Y0 + r * ch, 1), round((c_idx + 1) * cw, 1), round(Y0 + (r + 1) * ch, 1))

    draw_shade(0x2591, 8, 16, 0.25)
    draw_shade(0x2592, 8, 16, 0.50)
    draw_shade(0x2593, 8, 16, 0.75)

    # Quadrants U+2596..U+259F (BL, BR, TL, TL+BL+BR, TL+BR, TL+BL+TR, TL+TR+BR, TR, TR+BL, TR+BL+BR)
    QUADS = {
        0x2596: (0, 0, 1, 0), 0x2597: (0, 0, 0, 1), 0x2598: (1, 0, 0, 0),
        0x2599: (1, 0, 1, 1), 0x259A: (1, 0, 0, 1), 0x259B: (1, 1, 1, 0),
        0x259C: (1, 1, 0, 1), 0x259D: (0, 1, 0, 0), 0x259E: (0, 1, 1, 0),
        0x259F: (0, 1, 1, 1),
    }
    for code, (tl, tr, bl, br) in QUADS.items():
        g = new_u(f"uni{code:04X}", code)
        if tl:
            add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), 0.0, CY, CX, Y1)
        if tr:
            add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), CX, CY, W, Y1)
        if bl:
            add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), 0.0, Y0, CX, CY)
        if br:
            add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), CX, Y0, W, CY)

    # Powerline U+E0B0..E0B3 separators (flush full height Y0..Y1, x 0..W)
    g = new_u("uniE0B0", 0xE0B0)
    c = Contour(); g.contours.append(c)
    for x, y in [(0.0, Y1), (0.0, Y0), (W, CY)]:
        c.append(Point(x, y, "line"))

    g = new_u("uniE0B2", 0xE0B2)
    c = Contour(); g.contours.append(c)
    for x, y in [(W, Y1), (0.0, CY), (W, Y0)]:
        c.append(Point(x, y, "line"))

    pw = t_light * 0.9
    g = new_u("uniE0B1", 0xE0B1)
    c = Contour(); g.contours.append(c)
    for x, y in [(0.0, Y1), (0.0, Y1 - pw * 1.4), (W - pw * 1.4, CY), (0.0, Y0 + pw * 1.4), (0.0, Y0), (W, CY)]:
        c.append(Point(x, y, "line"))

    g = new_u("uniE0B3", 0xE0B3)
    c = Contour(); g.contours.append(c)
    for x, y in [(W, Y1), (0.0, CY), (W, Y0), (W, Y0 + pw * 1.4), (pw * 1.4, CY), (W, Y1 - pw * 1.4)]:
        c.append(Point(x, y, "line"))

    # Powerline U+E0A0 (git branch), U+E0A1 (LN), U+E0A2 (lock)
    sw = max(70.0, t_light)
    dx_p = CX - 640.0
    # E0A0 branch: vertical stem on left + curving branch on right with 3 nodes
    g = new_u("uniE0A0", 0xE0A0)
    add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), 310.0 + dx_p, 220.0, 310.0 + dx_p + sw, 1280.0)
    add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), 310.0 + dx_p + sw * 0.5, 480.0, 880.0 + dx_p, 480.0 + sw)
    add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), 880.0 + dx_p - sw, 480.0, 880.0 + dx_p, 1020.0)
    for cx_n, cy_n in [(310.0 + dx_p + sw * 0.5, 1340.0), (310.0 + dx_p + sw * 0.5, 160.0), (880.0 + dx_p - sw * 0.5, 1080.0)]:
        add_rounded_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), cx_n - 150.0, cy_n - 150.0, cx_n + 150.0, cy_n + 150.0, 149.0, 0.552, False)

    # E0A1 LN symbol
    g = new_u("uniE0A1", 0xE0A1)
    add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), 160.0 + dx_p, 260.0, 160.0 + dx_p + sw, 1230.0)
    add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), 160.0 + dx_p, 260.0, 580.0 + dx_p, 260.0 + sw)
    add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), 680.0 + dx_p, 260.0, 680.0 + dx_p + sw, 1230.0)
    add_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), 1120.0 + dx_p - sw, 260.0, 1120.0 + dx_p, 1230.0)
    c = Contour(); g.contours.append(c)
    for x, y in [(680.0 + dx_p, 1230.0), (680.0 + dx_p + sw, 1230.0), (1120.0 + dx_p, 260.0), (1120.0 + dx_p - sw, 260.0)]:
        c.append(Point(x, y, "line"))

    # E0A2 padlock
    g = new_u("uniE0A2", 0xE0A2)
    add_rounded_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), 240.0 + dx_p, 80.0, 1040.0 + dx_p, 860.0, 90.0, 0.552, False)
    add_rounded_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g), 370.0 + dx_p, 860.0, 910.0 + dx_p, 1380.0, 260.0, 0.552, False)
    # Inner shackle cutout (CW)
    c_in = Contour(); g.contours.append(c_in)
    ix0, iy0, ix1, iy1 = 370.0 + dx_p + sw, 860.0, 910.0 + dx_p - sw, 1380.0 - sw
    r_in_s = max(40.0, (ix1 - ix0) * 0.45)
    off = r_in_s * 0.552
    for x, y, t in [
        (ix0, iy0, "line"), (ix0, iy1 - r_in_s, "line"),
        (ix0, iy1 - r_in_s + off, None), (ix0 + r_in_s - off, iy1, None), (ix0 + r_in_s, iy1, "curve"),
        (ix1 - r_in_s, iy1, "line"),
        (ix1 - r_in_s + off, iy1, None), (ix1, iy1 - r_in_s + off, None), (ix1, iy1 - r_in_s, "curve"),
        (ix1, iy0, "line"),
    ]:
        c_in.append(Point(x, y, t))


def build_mono_features(orig_fea: str) -> str:
    """Transform Allo's features.fea into Allo Mono's coding-friendly OpenType features."""
    # Keep ccmp, sups, subs, numr, dnom, frac, tnum, zero, ss01, ss02, ss03, cv01..cv05, plus cv06 (Plain zero) & cv07 (Serifless I)
    # Replace calt (no nearCaps vertical jumping in code; add cell-preserving coding ligatures)
    # Remove liga (ff/tt/ft/tf ligatures break monospace column widths)
    lines = orig_fea.splitlines()
    out = []
    skip_block = None
    for line in lines:
        s = line.strip()
        if s.startswith("feature calt {"):
            skip_block = "} calt;"
            continue
        if s.startswith("feature liga {"):
            skip_block = "} liga;"
            continue
        if skip_block:
            if s == skip_block:
                skip_block = None
            continue
        # Remove dropped glyphs from @NOT_SPACE / @NOT_BRACKET if any remain
        if s.startswith("@NOT_SPACE =") or s.startswith("@NOT_BRACKET ="):
            tokens = s.split("[", 1)[1].rsplit("]", 1)[0].split()
            kept = [t for t in tokens if t not in DROP_GLYPHS]
            name = s.split("=")[0].strip()
            out.append(f"{name} = [{' '.join(kept)}];")
            continue
        if s == "sub @FIG by [zero.tf one.foot.tf two.tf three.tf four.tf five.tf six.tf seven.tf eight.tf nine.tf];":
            out.append("  sub @FIG by [zero.tf one.tf two.tf three.tf four.tf five.tf six.tf seven.tf eight.tf nine.tf];")
            continue
        if s == "sub zero by zero.dot;":
            out.append("  sub zero by zero.dot;")
            out.append("  sub zero.plain by zero.dot;")
            continue
        if s == "sub zero.tf by zero.tf.dot;":
            out.append("  sub zero.tf by zero.tf.dot;")
            continue
        out.append(line)

    coding_calt = """
feature calt {
  lookup mono3_step1 {
    ignore sub [less hyphen greater] less' hyphen greater;
    ignore sub less' hyphen greater [less hyphen greater];
    sub less' hyphen greater by less.larrow;

    ignore sub [less equal greater] less' equal greater;
    ignore sub less' equal greater [less equal greater];
    sub less' equal greater by less.dlarrow;

    ignore sub [hyphen less] hyphen' hyphen greater;
    ignore sub hyphen' hyphen greater [greater hyphen];
    sub hyphen' hyphen greater by hyphen.rarrow;

    ignore sub [less exclam] less' hyphen hyphen;
    ignore sub less' hyphen hyphen [hyphen greater];
    sub less' hyphen hyphen by less.larrow;

    ignore sub exclam exclam' equal equal;
    ignore sub exclam' equal equal equal;
    sub exclam' equal equal by exclam.neq3;

    ignore sub [equal exclam] equal' equal equal;
    ignore sub equal' equal equal equal;
    sub equal' equal equal by equal.eq3_l;
  } mono3_step1;

  lookup mono3_step2 {
    sub less.larrow hyphen' greater by hyphen.conn;
    sub less.dlarrow equal' greater by equal.dconn;
    sub hyphen.rarrow hyphen' greater by hyphen.conn;
    sub less.larrow hyphen' hyphen by hyphen.conn;
    sub exclam.neq3 equal' equal by equal.neq3_m;
    sub equal.eq3_l equal' equal by equal.eq3_m;
  } mono3_step2;

  lookup mono3_step3 {
    sub less.larrow hyphen.conn greater' by greater.rarrow;
    sub less.dlarrow equal.dconn greater' by greater.drarrow;
    sub hyphen.rarrow hyphen.conn greater' by greater.rarrow;
    sub less.larrow hyphen.conn hyphen' by hyphen.larrow;
    sub exclam.neq3 equal.neq3_m equal' by equal.eq3_r;
    sub equal.eq3_l equal.eq3_m equal' by equal.eq3_r;
  } mono3_step3;

  lookup mono2_step1 {
    ignore sub [hyphen less] hyphen' greater;
    ignore sub hyphen' greater [greater hyphen];
    sub hyphen' greater by hyphen.rarrow;

    ignore sub [less exclam] less' hyphen;
    ignore sub less' hyphen [hyphen greater];
    sub less' hyphen by less.larrow;

    ignore sub [equal less] equal' greater;
    ignore sub equal' greater [greater equal];
    sub equal' greater by equal.rarrow;

    ignore sub exclam exclam' equal;
    ignore sub exclam' equal equal;
    sub exclam' equal by exclam.neq;

    ignore sub [equal exclam] equal' equal;
    ignore sub equal' equal equal;
    sub equal' equal by equal.eq_l;

    ignore sub less less' equal;
    ignore sub less' equal [equal greater];
    sub less' equal by less.leq;

    ignore sub greater greater' equal;
    ignore sub greater' equal [equal greater];
    sub greater' equal by greater.geq;
  } mono2_step1;

  lookup mono2_step2 {
    sub hyphen.rarrow greater' by greater.rarrow;
    sub less.larrow hyphen' by hyphen.larrow;
    sub equal.rarrow greater' by greater.drarrow;
    sub exclam.neq equal' by equal.neq;
    sub equal.eq_l equal' by equal.eq_r;
    sub less.leq equal' by equal.leq;
    sub greater.geq equal' by equal.geq;
  } mono2_step2;
} calt;
"""

    extra_cv = """
feature cv06 {
  cvParameters {
    FeatUILabelNameID { name "Plain zero"; };
  };
  sub zero by zero.plain;
  sub zero.slash by zero.plain;
  sub zero.dot by zero.plain;
  sub zero.tf by zero.plain;
  sub zero.tf.slash by zero.plain;
  sub zero.tf.dot by zero.plain;
} cv06;

feature cv07 {
  cvParameters {
    FeatUILabelNameID { name "Serifless I"; };
  };
  sub I by I.sans;
  sub I.1 by I.sans;
} cv07;
"""
    # Insert coding_calt before feature case
    text = "\n".join(out)
    text = text.replace("feature case {", coding_calt.lstrip("\n") + "\nfeature case {", 1)
    text = text.rstrip() + "\n" + extra_cv
    return text


def weight_t(weight: int) -> float:
    return max(0.0, min(1.0, (weight - 100.0) / 800.0))


def lerp_w(weight: int, w100: float, w900: float) -> float:
    return w100 + (w900 - w100) * weight_t(weight)


def max_width_for_weight(weight: int) -> float:
    return lerp_w(weight, 935.0, 1025.0)


def reposition_composite_marks(font, gname: str, is_oblique: bool) -> None:
    """Re-align mark components on composites whose base glyph was rebuilt/condensed."""
    g = font[gname]
    if not g.components:
        return
    base_comp = g.components[0]
    base_name = base_comp.baseGlyph
    if base_name not in font:
        return
    base = font[base_name]
    base_tx, base_ty = base_comp.transformation[4], base_comp.transformation[5]
    base_anchors = {a.name: (a.x + base_tx, a.y + base_ty) for a in base.anchors}
    for comp in g.components[1:]:
        mname = comp.baseGlyph
        if mname not in font:
            continue
        mg = font[mname]
        m_anchors = {a.name: (a.x, a.y) for a in mg.anchors}
        # Find matching anchor pair (_top <-> top, _bottom <-> bottom, _cedilla <-> cedilla, _ogonek <-> ogonek, _cross <-> cross)
        matched = False
        for ma, (mx, my) in m_anchors.items():
            if ma.startswith("_") and ma[1:] in base_anchors:
                bx, by = base_anchors[ma[1:]]
                m = list(comp.transformation)
                m[4] = bx - mx
                m[5] = by - my
                comp.transformation = tuple(m)
                matched = True
                break
        if not matched:
            # Sidecar marks (commaaccentright.cn in dcaron/lcaron/tcaron, periodcentered in ldot)
            bb_b = glyph_bounds_upright(font, base_name, is_oblique)
            bb_m = glyph_bounds_upright(font, mname, is_oblique)
            if bb_b and bb_m:
                m = list(comp.transformation)
                if mname in ("commaaccentright.cn", "periodcentered"):
                    gap = 30.0 if mname == "periodcentered" else 24.0
                    bw_b = bb_b[2] - bb_b[0]
                    mw = bb_m[2] - bb_m[0]
                    # Reset base_tx to 0 before placing so leftover proportional offsets don't stack
                    bm = list(base_comp.transformation)
                    bm[4] = 0.0
                    base_tx = 0.0
                    # Scale sidecar slightly in heavy weights if base + sidecar exceeds cell
                    avail = (MONO_WIDTH - 40.0) - bw_b - gap
                    sx_m = min(1.0, max(0.68, avail / mw)) if mw > avail else 1.0
                    m[0] = sx_m
                    m[3] = sx_m if mname == "periodcentered" else 1.0
                    total_w = bw_b + gap + mw * sx_m
                    x_start = max(20.0, CENTER_X - total_w / 2.0)
                    dx_base = x_start - bb_b[0]
                    bm[4] = dx_base
                    base_comp.transformation = tuple(bm)
                    m[4] = (x_start + bw_b + gap) - bb_m[0] * sx_m
                else:
                    cx_b = (bb_b[0] + bb_b[2]) / 2.0 + base_tx
                    cx_m = (bb_m[0] + bb_m[2]) / 2.0
                    m[4] = slant_x(cx_b, bb_m[1], is_oblique) - cx_m
                comp.transformation = tuple(m)


def transform_layer_to_mono(layer, weight: int, is_oblique: bool, metrics: dict[str, float], is_sparse: bool = False) -> None:
    """Convert one UFO layer (full or sparse) to Allo Mono metrics & glyph designs."""
    # Remove dropped glyphs
    for dname in list(DROP_GLYPHS):
        if dname in layer:
            del layer[dname]

    if is_sparse:
        # Sparse layers only hold 133 override glyphs: keep zero-width marks at 0, others at 1280
        # Measure metrics from default layer of parent font if possible; fall back cleanly
        pass

    # In Oblique UFOs, Allo stored anchors unslanted; slant them around PIVOT_Y so mark alignment matches slanted outlines
    if is_oblique and not is_sparse:
        for g in layer:
            for a in g.anchors:
                a.x = slant_x(a.x, a.y, True)

    # Save original widths & upright bounds before modification
    orig_widths = {g.name: g.width for g in layer}
    orig_bounds = {g.name: glyph_bounds_upright(layer, g.name, is_oblique) for g in layer}

    # Measure metrics from default layer (for sparse layers, use default layer via layer.font if available)

    # Use serifed Iogonek.1 for Iogonek across all layers (including sparse ROND 10/30)
    if "Iogonek.1" in layer:
        if "Iogonek" in layer:
            layer["Iogonek"].copyDataFromGlyph(layer["Iogonek.1"])
        else:
            g_io = layer.newGlyph("Iogonek")
            g_io.copyDataFromGlyph(layer["Iogonek.1"])
        layer["Iogonek"].unicodes = [] if is_sparse else [0x012E]

    # Save a copy of plain I and plain zero before replacing defaults
    if not is_sparse:
        if "I" in layer:
            g_isans = layer.newGlyph("I.sans")
            g_isans.copyDataFromGlyph(layer["I"])
            g_isans.unicodes = []
            b = glyph_bounds_upright(layer, "I.sans", is_oblique)
            if b:
                shift_glyph_x(g_isans, CENTER_X - (b[0] + b[2]) / 2.0)
            g_isans.width = MONO_WIDTH

        if "zero" in layer:
            g_zp = layer["zero.plain"] if False else layer.newGlyph("zero.plain")
            g_zp.copyDataFromGlyph(layer["zero"])
            g_zp.unicodes = []
            counter_aware_round_compress(g_zp, lerp_w(weight, 800.0, 935.0), is_oblique)
            g_zp.width = MONO_WIDTH

    max_w = max_width_for_weight(weight)

    # Track dx applied to single-contour base glyphs so simple diacritic composites follow automatically
    base_shift_dx: dict[str, float] = {}

    # 1. Transform narrow / special rebuilt base glyphs
    if "I" in layer and "I.1" in layer:
        target_I_w = min(745.0, max(565.0, 505.0 + metrics["cap_stem"] * 0.78))
        layer["I"].copyDataFromGlyph(layer["I.1"])
        layer["I"].unicodes = [0x0049]
        build_serifed_I_from_template(layer["I"], metrics, target_I_w, is_oblique)
        layer["I"].width = MONO_WIDTH
        layer["I.1"].copyDataFromGlyph(layer["I"])
        layer["I.1"].unicodes = []
        layer["I.1"].width = MONO_WIDTH

    if "idotless" in layer:
        build_serifed_idotless(layer["idotless"], metrics, is_oblique)
        layer["idotless"].width = MONO_WIDTH
        if not is_sparse and "i" in layer and "dotaccentcomb" in layer:
            gi = layer["i"]
            gi.clearContours()
            gi.clearComponents()
            gi.components.append(Component("idotless"))
            gi.components.append(Component("dotaccentcomb"))
            gi.unicodes = [0x0069]
            gi.width = MONO_WIDTH
            reposition_composite_marks(layer, "i", is_oblique)

    if "l.ss02" in layer:
        g_lss = layer["l.ss02"]
        widen_l_tail(g_lss, lerp_w(weight, 95.0, 35.0), is_oblique)
        b_l = glyph_bounds_upright(layer, "l.ss02", is_oblique)
        if b_l:
            old_cx = (b_l[0] + b_l[2]) / 2.0
            serif_ext = min(200.0, max(145.0, 138.0 + metrics["lc_stem"] * 0.16))
            shift_l = CENTER_X - old_cx + serif_ext * 0.35
            add_top_left_serif_to_stem(g_lss, metrics, shift_l, serif_ext, is_oblique)
            b_new = glyph_bounds_upright(layer, "l.ss02", is_oblique)
            if b_new:
                shift_glyph_x(g_lss, CENTER_X - (b_new[0] + b_new[2]) / 2.0)
            pts_top = [unslant_x(p.x, p.y, is_oblique) for c in g_lss.contours for p in c if p.y > 1400]
            stem_mid = (min(pts_top) + serif_ext + max(pts_top)) / 2.0 if pts_top else CENTER_X
            for a in g_lss.anchors:
                if a.name in ("top", "cross", "bottom"):
                    a.x = slant_x(stem_mid, a.y, is_oblique)
            g_lss.width = MONO_WIDTH
        if not is_sparse:
            if "l" in layer:
                layer["l"].copyDataFromGlyph(g_lss)
                layer["l"].unicodes = [0x006C]
                layer["l"].width = MONO_WIDTH
        else:
            g_l_sp = layer["l"] if "l" in layer else layer.newGlyph("l")
            g_l_sp.copyDataFromGlyph(g_lss)
            g_l_sp.unicodes = []
            g_l_sp.width = MONO_WIDTH

    if "jdotless" in layer:
        g_j = layer["jdotless"]
        widen_j_hook(g_j, lerp_w(weight, 195.0, 95.0), is_oblique)
        b_j = glyph_bounds_upright(layer, "jdotless", is_oblique)
        if b_j:
            serif_ext = min(195.0, max(140.0, 130.0 + metrics["lc_stem"] * 0.15))
            old_cx = (b_j[0] + b_j[2]) / 2.0
            add_top_left_serif_to_stem(g_j, metrics, CENTER_X - old_cx, serif_ext, is_oblique)
            b_new = glyph_bounds_upright(layer, "jdotless", is_oblique)
            if b_new:
                shift_glyph_x(g_j, CENTER_X - (b_new[0] + b_new[2]) / 2.0)
            j_top = b_new[3] if b_new else float(X_HEIGHT)
            pts_top = [unslant_x(p.x, p.y, is_oblique) for c in g_j.contours for p in c if j_top - 110 < p.y <= j_top + 5]
            stem_mid = (min(pts_top) + serif_ext + max(pts_top)) / 2.0 if pts_top else CENTER_X
            for a in g_j.anchors:
                if a.name in ("top", "cross"):
                    a.x = slant_x(stem_mid, a.y, is_oblique)
            g_j.width = MONO_WIDTH
            if not is_sparse and "j" in layer and "dotaccentcomb" in layer:
                gj = layer["j"]
                gj.clearContours()
                gj.clearComponents()
                gj.components.append(Component("jdotless"))
                gj.components.append(Component("dotaccentcomb"))
                gj.unicodes = [0x006A]
                gj.width = MONO_WIDTH
                reposition_composite_marks(layer, "j", is_oblique)

    # Narrow r, f, t (strictly widened past stem right edge)
    for gn, w100, w900 in [("r", 125.0, 60.0), ("f", 80.0, 40.0), ("t", 95.0, 45.0)]:
        if gn in layer and layer[gn].contours:
            widen_narrow_glyph(layer[gn], lerp_w(weight, w100, w900), is_oblique)
            layer[gn].width = MONO_WIDTH
            if orig_bounds.get(gn) and glyph_bounds_upright(layer, gn, is_oblique):
                b0 = orig_bounds[gn]
                b1 = glyph_bounds_upright(layer, gn, is_oblique)
                base_shift_dx[gn] = ((b1[0] + b1[2]) - (b0[0] + b0[2])) / 2.0

    # Underscore spans [0, MONO_WIDTH]
    if "underscore" in layer and layer["underscore"].contours:
        g_u = layer["underscore"]
        ys_u = [p.y for c in g_u.contours for p in c]
        y0_u, y1_u = min(ys_u), max(ys_u)
        g_u.clearContours()
        add_rounded_rect((lambda _g: (_c := Contour(), _g.contours.append(_c), _c)[-1])(g_u), 0.0, y0_u, float(MONO_WIDTH), y1_u, metrics["r_cap"], metrics["handle_frac"], is_oblique)
        g_u.width = MONO_WIDTH

    # Footed 1 default in monospace (gently harmonized to ~786 at Regular)
    if not is_sparse and "one.foot" in layer and "one" in layer:
        normal_compensated_compress(layer["one.foot"], lerp_w(weight, 730.0, 880.0), is_oblique, comp_strength=0.72, stem_ref=metrics["cap_stem"])
        layer["one.foot"].width = MONO_WIDTH
        layer["one"].copyDataFromGlyph(layer["one.foot"])
        layer["one"].unicodes = [0x0031]
        layer["one"].width = MONO_WIDTH
        if "one.tf" in layer:
            layer["one.tf"].clearContours()
            layer["one.tf"].clearComponents()
            layer["one.tf"].components.append(Component("one"))
            layer["one.tf"].width = MONO_WIDTH
        if "one.foot.tf" in layer:
            layer["one.foot.tf"].clearContours()
            layer["one.foot.tf"].clearComponents()
            layer["one.foot.tf"].components.append(Component("one"))
            layer["one.foot.tf"].width = MONO_WIDTH

    # Zero: default slashed zero, plus dot & plain
    if not is_sparse and "zero.plain" in layer and "_part.zeroslash" in layer and "_part.zerodot" in layer:
        # Center _part.zeroslash and _part.zerodot inside zero.plain
        bp_z = glyph_bounds_upright(layer, "zero.plain", is_oblique)
        if bp_z:
            z_cx = (bp_z[0] + bp_z[2]) / 2.0
            for pname in ("_part.zeroslash", "_part.zerodot"):
                bp_p = glyph_bounds_upright(layer, pname, is_oblique)
                if bp_p:
                    shift_glyph_x(layer[pname], z_cx - (bp_p[0] + bp_p[2]) / 2.0)
                layer[pname].width = MONO_WIDTH

        for zname, part, uni in [
            ("zero", "_part.zeroslash", [0x0030]),
            ("zero.slash", "_part.zeroslash", []),
            ("zero.dot", "_part.zerodot", []),
            ("zero.tf", "_part.zeroslash", []),
            ("zero.tf.slash", "_part.zeroslash", []),
            ("zero.tf.dot", "_part.zerodot", []),
        ]:
            if zname in layer:
                gz = layer[zname]
                gz.clearContours()
                gz.clearComponents()
                gz.components.append(Component("zero.plain"))
                gz.components.append(Component(part))
                gz.unicodes = uni
                gz.width = MONO_WIDTH

    # 2. Stem-preserving / counter-aware / normal-compensated compression for wide direct glyphs
    handled_special = {"I", "I.1", "I.sans", "idotless", "i", "l", "l.ss02", "jdotless", "j", "r", "f", "t",
                       "underscore", "one", "one.foot", "one.tf", "one.foot.tf",
                       "zero", "zero.plain", "zero.slash", "zero.dot", "zero.tf", "zero.tf.slash", "zero.tf.dot"}

    for g in layer:
        if g.name in handled_special:
            continue
        w = orig_widths.get(g.name, g.width)
        if w == 0:
            # Zero-width combining marks stay width 0
            continue
        g.width = MONO_WIDTH
        if g.name.startswith("_") or g.name.endswith(".cn") or g.name in ("crossbar", "crossbar.uc", "crossbar_short.uc", "slashshort.lc", "slashshort.uc"):
            continue
        if not g.contours:
            continue

        b = glyph_bounds_upright(layer, g.name, is_oblique)
        if not b:
            continue
        bw = b[2] - b[0]

        if g.name in STEM_PIECEWISE_GLYPHS:
            tw = lerp_w(weight, 865.0, 985.0) if g.name == "m" else lerp_w(weight, 835.0, 965.0)
            piecewise_stem_compress(g, tw, is_oblique)
        elif g.name in ("o", "oslash"):
            tw = lerp_w(weight, 755.0, 915.0)
            if bw > tw:
                counter_aware_round_compress(g, tw, is_oblique)
            else:
                shift_glyph_x(g, CENTER_X - (b[0] + b[2]) / 2.0)
        elif g.name in ROUND_LC_GLYPHS:
            tw = lerp_w(weight, 755.0, 915.0)
            if bw > tw:
                normal_compensated_compress(g, tw, is_oblique, comp_strength=0.88, stem_ref=metrics["lc_stem"])
            else:
                shift_glyph_x(g, CENTER_X - (b[0] + b[2]) / 2.0)
        elif g.name in STD_LC_CAP_GLYPHS:
            tw = lerp_w(weight, 765.0, 925.0)
            if bw > tw:
                normal_compensated_compress(g, tw, is_oblique, comp_strength=0.78, stem_ref=metrics["lc_stem"])
            else:
                dx = (MONO_WIDTH - w) / 2.0
                if b[0] + dx < 35.0:
                    dx = 35.0 - b[0]
                elif b[2] + dx > MONO_WIDTH - 35.0:
                    dx = (MONO_WIDTH - 35.0) - b[2]
                shift_glyph_x(g, dx)
        elif g.name in ROUND_COMP_GLYPHS:
            tw = lerp_w(weight, 825.0, 965.0)
            if bw > tw:
                counter_aware_round_compress(g, tw, is_oblique)
            else:
                dx = (MONO_WIDTH - w) / 2.0
                if b[0] + dx < 35.0:
                    dx = 35.0 - b[0]
                elif b[2] + dx > MONO_WIDTH - 35.0:
                    dx = (MONO_WIDTH - 35.0) - b[2]
                shift_glyph_x(g, dx)
        elif g.name in DIAG_COMP_GLYPHS or bw > max_w:
            if g.name == "W":
                tw = lerp_w(weight, 895.0, 1005.0)
            elif g.name in ("M", "w"):
                tw = lerp_w(weight, 865.0, 990.0)
            elif g.name in ("emdash", "emdash.case"):
                tw = float(MONO_WIDTH) - 80.0
            elif g.name[0].isupper():
                tw = min(max_w, lerp_w(weight, 835.0, 965.0))
            else:
                tw = max_w
            sref = metrics["cap_stem"] if g.name[0].isupper() else metrics["lc_stem"]
            if bw > tw:
                normal_compensated_compress(g, tw, is_oblique, comp_strength=0.72, stem_ref=sref)
            else:
                dx = (MONO_WIDTH - w) / 2.0
                if b[0] + dx < 35.0:
                    dx = 35.0 - b[0]
                elif b[2] + dx > MONO_WIDTH - 35.0:
                    dx = (MONO_WIDTH - 35.0) - b[2]
                shift_glyph_x(g, dx)
        elif g.name in ("equal", "less", "greater"):
            tw = lerp_w(weight, 790.0, 860.0)
            if bw > tw:
                normal_compensated_compress(g, tw, is_oblique, comp_strength=0.70, stem_ref=metrics["eq_bar_h"])
            else:
                shift_glyph_x(g, CENTER_X - (b[0] + b[2]) / 2.0)
        else:
            # Standard glyph that fits naturally: preserve optical sidebearing balance by shifting (MONO_WIDTH - w)/2
            # Center tiny punctuation/bars/quotes dead center for clean mono grid alignment
            if g.name in ("period", "comma", "colon", "semicolon", "exclam", "question", "quotesingle", "grave",
                          "asciicircum", "asciitilde", "bar", "brokenbar", "slash", "backslash", "hyphen",
                          "endash", "figuredash", "figuredash.case", "asterisk", "asterisk.case", "plus", "minus",
                          "plusminus", "divide", "multiply", "notequal", "approxequal",
                          "lessequal", "greaterequal", "logicalnot", "degree"):
                dx = CENTER_X - (b[0] + b[2]) / 2.0
            elif g.name in LEFT_BIAS_BRACKETS:
                dx = CENTER_X - (b[0] + b[2]) / 2.0 + 30.0
            elif g.name in RIGHT_BIAS_BRACKETS:
                dx = CENTER_X - (b[0] + b[2]) / 2.0 - 30.0
            else:
                dx = (MONO_WIDTH - w) / 2.0
                margin = 35.0
                if b[0] + dx < margin:
                    dx = margin - b[0]
                elif b[2] + dx > MONO_WIDTH - margin:
                    dx = (MONO_WIDTH - margin) - b[2]
            shift_glyph_x(g, dx)
            base_shift_dx[g.name] = dx

    if is_sparse:
        # Sparse layers: align any composite glyphs that exist in sparse layer
        for g in layer:
            if g.components and not g.contours:
                g.width = MONO_WIDTH
        return

    # 3. Special multi-component glyphs (oe, IJ, ij, ellipsis, quotedbl, percent, perthousand, fractions, degC/degF)
    if "quotedbl" in layer and "quotesingle" in layer:
        gq = layer["quotedbl"]
        gq.clearComponents()
        b_qs = glyph_bounds_upright(layer, "quotesingle", is_oblique)
        q_w = (b_qs[2] - b_qs[0]) if b_qs else 181.0
        gap_q = max(90.0, q_w * 0.55)
        offset_q = (q_w + gap_q) / 2.0
        gq.components.append(Component("quotesingle", (1, 0, 0, 1, -offset_q, 0)))
        gq.components.append(Component("quotesingle", (1, 0, 0, 1, offset_q, 0)))
        gq.width = MONO_WIDTH

    for ename in ("ellipsis", "uni22EF"):
        if ename in layer and "period" in layer:
            ge = layer[ename]
            ge.clearComponents()
            bp = glyph_bounds_upright(layer, "period", is_oblique)
            pw = (bp[2] - bp[0]) if bp else 276.0
            step = min(380.0, (max_w - pw) / 2.0)
            sx = min(1.0, 260.0 / pw) if pw > 260.0 else 1.0
            for idx in (-1, 0, 1):
                ge.components.append(Component("period", (sx, 0, 0, sx, idx * step + CENTER_X * (1.0 - sx), 0)))
            ge.width = MONO_WIDTH

    if "oe" in layer and "o" in layer and "e" in layer:
        # Decompose oe and condense cleanly so strokes stay crisp
        goe = layer["oe"]
        goe.clearContours()
        goe.clearComponents()
        # Combine o and e contours with tighter overlap
        bo = glyph_bounds_upright(layer, "o", is_oblique)
        be = glyph_bounds_upright(layer, "e", is_oblique)
        if bo and be:
            # Copy o and e contours directly into oe with overlap
            shift_e = (bo[2] - bo[0]) * 0.72
            for base_g, ox in [(layer["o"], 0.0), (layer["e"], shift_e)]:
                for c in base_g.contours:
                    nc = Contour(); goe.contours.append(nc)
                    for p in c:
                        nc.append(Point(p.x + ox, p.y, p.type))
            normal_compensated_compress(goe, min(max_w, 985.0), is_oblique, comp_strength=0.45, stem_ref=metrics["lc_stem"])
        goe.width = MONO_WIDTH

    for ij_name, g1, g2 in [("IJ", "I", "J"), ("ij", "i", "j")]:
        if ij_name in layer and g1 in layer and g2 in layer:
            gij = layer[ij_name]
            gij.clearContours()
            gij.clearComponents()
            # Place g1 and g2 side by side scaled slightly in x and centered
            b1 = glyph_bounds_upright(layer, g1, is_oblique)
            b2 = glyph_bounds_upright(layer, g2, is_oblique)
            if b1 and b2:
                w1, w2 = b1[2] - b1[0], b2[2] - b2[0]
                gap = 60.0
                total = w1 + w2 + gap
                sx = min(1.0, (max_w - 40.0) / total)
                # Place left edge at CENTER_X - total*sx/2
                x_left = CENTER_X - (total * sx) / 2.0
                gij.components.append(Component(g1, (sx, 0, 0, 1, x_left - b1[0] * sx, 0)))
                gij.components.append(Component(g2, (sx, 0, 0, 1, x_left + (w1 + gap) * sx - b2[0] * sx, 0)))
                b_ij = glyph_bounds_upright(layer, ij_name, is_oblique)
                if b_ij:
                    shift_glyph_x(gij, CENTER_X - (b_ij[0] + b_ij[2]) / 2.0)
            gij.width = MONO_WIDTH

    # Align Dbar1 & Eng (base + attached part) before fitting composites
    if "Dbar1" in layer and "D" in layer and "crossbar_short.uc" in layer:
        gd = layer["Dbar1"]
        gd.clearComponents()
        bd = glyph_bounds_upright(layer, "D", is_oblique)
        bc = glyph_bounds_upright(layer, "crossbar_short.uc", is_oblique)
        if bd and bc:
            # Place crossbar over D's left vertical stem protruding cleanly inside cell
            bar_x = max(45.0, bd[0] - (bc[2] - bc[0]) * 0.38)
            gd.components.append(Component("D", (1, 0, 0, 1, 0, 0)))
            gd.components.append(Component("crossbar_short.uc", (1, 0, 0, 1, bar_x - bc[0], 0)))
            pen_d = BoundsPen(layer)
            gd.draw(pen_d)
            if pen_d.bounds:
                bw_d = pen_d.bounds[2] - pen_d.bounds[0]
                if bw_d > max_w:
                    sx_d = max_w / bw_d
                    cx_d = (pen_d.bounds[0] + pen_d.bounds[2]) / 2.0
                    for comp in gd.components:
                        mt = list(comp.transformation)
                        mt[0] *= sx_d
                        mt[4] = CENTER_X + (mt[4] - cx_d) * sx_d
                        comp.transformation = tuple(mt)
                pen_d2 = BoundsPen(layer)
                gd.draw(pen_d2)
                if pen_d2.bounds:
                    shift_glyph_x(gd, CENTER_X - (pen_d2.bounds[0] + pen_d2.bounds[2]) / 2.0)
        gd.width = MONO_WIDTH

    if "Eng" in layer and layer["Eng"].components and not layer["Eng"].contours and "N" in layer and "_part.bottom_lfthook2_uc" in layer:
        ge = layer["Eng"]
        ge.clearComponents()
        bn = glyph_bounds_upright(layer, "N", is_oblique)
        bh_p = glyph_bounds_upright(layer, "_part.bottom_lfthook2_uc", is_oblique)
        if bn and bh_p:
            # Align hook's right stem with N's right stem
            ge.components.append(Component("N", (1, 0, 0, 1, 0, 0)))
            ge.components.append(Component("_part.bottom_lfthook2_uc", (1, 0, 0, 1, bn[2] - bh_p[2], 0)))
            pen_e = BoundsPen(layer)
            ge.draw(pen_e)
            if pen_e.bounds:
                shift_glyph_x(ge, CENTER_X - (pen_e.bounds[0] + pen_e.bounds[2]) / 2.0)
        ge.width = MONO_WIDTH

    # Fit composite fractions, percent, perthousand, degC, degF inside [35, MONO_WIDTH - 35]
    SPECIAL_SCALE_COMPOSITES = (
        "percent", "perthousand", "onehalf", "onequarter", "threequarters",
        "onethird", "twothirds", "oneeighth", "threeeighths", "fiveeighths",
        "seveneighths", "degreecentigrade", "degreefarenheit",
    )
    for gname in SPECIAL_SCALE_COMPOSITES:
        if gname in layer and layer[gname].components:
            g = layer[gname]
            # Restore exact relative layout before scaling by undoing step-2 shifts
            for comp in g.components:
                b0_c = orig_bounds.get(comp.baseGlyph)
                b1_c = glyph_bounds_upright(layer, comp.baseGlyph, is_oblique)
                if b0_c and b1_c:
                    dx_c = ((b1_c[0] + b1_c[2]) - (b0_c[0] + b0_c[2])) / 2.0
                    if abs(dx_c) > 1e-6:
                        m_t = list(comp.transformation)
                        m_t[4] -= dx_c
                        comp.transformation = tuple(m_t)
            b_sp = glyph_bounds_upright(layer, gname, is_oblique)
            if b_sp:
                bx0, _, bx1, _ = b_sp
                bw = bx1 - bx0
                if bw > max_w:
                    sx = max_w / bw
                    cx_old = (bx0 + bx1) / 2.0
                    for comp in g.components:
                        m_t = list(comp.transformation)
                        m_t[0] *= sx
                        m_t[4] = CENTER_X + (m_t[4] - cx_old) * sx
                        comp.transformation = tuple(m_t)
                b_sp2 = glyph_bounds_upright(layer, gname, is_oblique)
                if b_sp2:
                    bw2 = b_sp2[2] - b_sp2[0]
                    if bw2 > max_w:
                        sx2 = max_w / bw2
                        cx2 = (b_sp2[0] + b_sp2[2]) / 2.0
                        for comp in g.components:
                            m_t = list(comp.transformation)
                            m_t[0] *= sx2
                            m_t[4] = CENTER_X + (m_t[4] - cx2) * sx2
                            comp.transformation = tuple(m_t)
                        b_sp2 = glyph_bounds_upright(layer, gname, is_oblique)
                    shift_glyph_x(g, CENTER_X - (b_sp2[0] + b_sp2[2]) / 2.0)
            g.width = MONO_WIDTH

    # 4. Remaining composite glyphs (accented letters, .case wrappers, etc.)
    SKIP_COMPOSITES = set(SPECIAL_SCALE_COMPOSITES) | {
        "Dbar1", "Eng", "quotedbl", "ellipsis", "uni22EF", "IJ", "ij",
        "zero", "zero.slash", "zero.dot", "zero.tf", "zero.tf.slash", "zero.tf.dot",
        "one.tf", "one.foot.tf",
    }
    for g in layer:
        if g.name in SKIP_COMPOSITES or g.name.startswith("_") or g.contours or not g.components:
            continue
        g.width = MONO_WIDTH
        base_name = g.components[0].baseGlyph
        if len(g.components) == 1:
            # 1-component wrappers (Dcroat, Eth, two.dnom..nine.dnom, periodcentered, .case)
            # Zero out any leftover horizontal offset from proportional Allo only if outside cell, else center cleanly
            b_wrap = glyph_bounds_upright(layer, g.name, is_oblique)
            if b_wrap:
                if b_wrap[0] < 15.0 or b_wrap[2] > MONO_WIDTH - 15.0:
                    shift_glyph_x(g, CENTER_X - (b_wrap[0] + b_wrap[2]) / 2.0)
        else:
            if g.name == "hcircumflex":
                bm = list(g.components[0].transformation)
                bm[4] = 0.0
                g.components[0].transformation = tuple(bm)
            reposition_composite_marks(layer, g.name, is_oblique)

        # Final safety clamp so NO composite's bounding box clips outside [15, MONO_WIDTH - 15]
        b_fin = glyph_bounds_upright(layer, g.name, is_oblique)
        if b_fin:
            bw_fin = b_fin[2] - b_fin[0]
            if bw_fin > MONO_WIDTH - 40.0:
                sx_fin = (MONO_WIDTH - 40.0) / bw_fin
                cx_fin = (b_fin[0] + b_fin[2]) / 2.0
                for comp in g.components:
                    m_t = list(comp.transformation)
                    m_t[0] *= sx_fin
                    m_t[4] = CENTER_X + (m_t[4] - cx_fin) * sx_fin
                    comp.transformation = tuple(m_t)
                b_fin = glyph_bounds_upright(layer, g.name, is_oblique)
            if b_fin[0] < 15.0:
                shift_glyph_x(g, min(20.0 - b_fin[0], (MONO_WIDTH - 15.0) - b_fin[2]))
            elif b_fin[2] > MONO_WIDTH - 15.0:
                shift_glyph_x(g, max((MONO_WIDTH - 20.0) - b_fin[2], 15.0 - b_fin[0]))

    # 5. Build coding ligatures + box drawing + block elements + powerline
    if not is_sparse:
        build_coding_ligature_glyphs(layer, metrics, is_oblique)
        build_box_drawing_and_blocks(layer, metrics)

    # 6. Strip legacy PUA unicodes (0xE000..0xF8FF) except our Powerline glyphs
    if not is_sparse:
        powerline_names = {"uniE0A0", "uniE0A1", "uniE0A2", "uniE0B0", "uniE0B1", "uniE0B2", "uniE0B3"}
        for g in layer:
            if g.unicodes and g.name not in powerline_names:
                g.unicodes = [u for u in g.unicodes if not (0xE000 <= u <= 0xF8FF)]


def update_fontinfo_and_lib(font: ufoLib2.Font, family: str, style: str, ps_name: str, full_name: str, is_oblique: bool) -> None:
    info = font.info
    info.familyName = family
    info.openTypeNamePreferredFamilyName = family
    info.styleMapFamilyName = family
    info.styleName = style
    info.openTypeNamePreferredSubfamilyName = style
    info.postscriptFontName = ps_name
    info.postscriptFullName = full_name
    info.openTypeNameUniqueID = full_name
    info.postscriptIsFixedPitch = True
    # PANOSE Proportion = 9 (Monospaced)
    panose = list(info.openTypeOS2Panose or [2, 0, 0, 9, 0, 0, 0, 2, 0, 4])
    panose[3] = 9
    info.openTypeOS2Panose = panose
    if is_oblique:
        info.italicAngle = SLANT_ANGLE
        info.postscriptSlantAngle = SLANT_ANGLE

    # Empty kerning & groups for monospace
    font.kerning.clear()
    font.groups.clear()

    # Update glyphOrder & openTypeCategories
    order = [g for g in font.lib.get("public.glyphOrder", []) if g not in DROP_GLYPHS]
    existing = set(order)
    for g in font:
        if g.name not in existing:
            order.append(g.name)
            existing.add(g.name)
    font.lib["public.glyphOrder"] = order

    cats = dict(font.lib.get("public.openTypeCategories", {}))
    for d in DROP_GLYPHS:
        cats.pop(d, None)
    cats.update(OPENTYPE_CATEGORIES_EXTRA)
    for g in font:
        if g.name not in cats and (g.name.startswith("uni25") or g.name.startswith("uniE0") or "." in g.name):
            cats[g.name] = "base"
    font.lib["public.openTypeCategories"] = cats


def generate_designspace_and_masters() -> None:
    if MONO_MASTERS_DIR.exists():
        shutil.rmtree(MONO_MASTERS_DIR)
    MONO_MASTERS_DIR.mkdir(parents=True, exist_ok=True)

    src_ds = DesignSpaceDocument.fromfile(SRC_DIR / "Allo.designspace")
    mono_ds = DesignSpaceDocument()

    # Copy axes: opsz (14..32), wght (100..900), ROND (0..100)
    for ax in src_ds.axes:
        if ax.tag == "opsz":
            new_ax = AxisDescriptor(
                tag="opsz",
                name="Optical size",
                minimum=14,
                maximum=32,
                default=14,
                axisLabels=copy.deepcopy([lbl for lbl in ax.axisLabels if lbl.userValue >= 14]),
            )
            mono_ds.addAxis(new_ax)
        else:
            mono_ds.addAxis(copy.deepcopy(ax))

    processed_ufos: dict[str, ufoLib2.Font] = {}
    for src in src_ds.sources:
        loc = dict(src.location)
        if loc["Optical size"] < 14:
            continue
        src_ufo_name = Path(src.filename).name
        mono_ufo_name = src_ufo_name.replace("Allo-", "AlloMono-")
        mono_ufo_path = MONO_MASTERS_DIR / mono_ufo_name
        wght = int(loc["Weight"])

        if src_ufo_name not in processed_ufos:
            font = ufoLib2.Font.open(SRC_DIR / src.filename)
            master_metrics = extract_metrics(font.layers.defaultLayer, is_oblique=False)
            transform_layer_to_mono(font.layers.defaultLayer, weight=wght, is_oblique=False, metrics=master_metrics, is_sparse=False)
            style = "Regular" if src.styleName == "Regular" else src.styleName
            update_fontinfo_and_lib(
                font,
                family="Allo Mono Variable",
                style=style,
                ps_name="AlloMonoVariable-" + style.replace(" ", ""),
                full_name=f"Allo Mono Variable {style}",
                is_oblique=False,
            )
            font.features.text = build_mono_features(font.features.text)
            processed_ufos[src_ufo_name] = font

        font = processed_ufos[src_ufo_name]
        if src.layerName:
            transform_layer_to_mono(font.layers[src.layerName], weight=wght, is_oblique=False, metrics=master_metrics, is_sparse=True)

        new_src = SourceDescriptor(
            filename=f"mono-masters/{mono_ufo_name}",
            name=src.name,
            familyName="Allo Mono Variable",
            styleName=src.styleName,
            location=loc,
            layerName=src.layerName,
        )
        mono_ds.addSource(new_src)

    for ufo_name, font in processed_ufos.items():
        mono_ufo_name = ufo_name.replace("Allo-", "AlloMono-")
        font.save(MONO_MASTERS_DIR / mono_ufo_name, overwrite=True)

    for inst in src_ds.instances:
        new_inst = InstanceDescriptor(
            familyName="Allo Mono Variable",
            styleName=inst.styleName,
            postScriptFontName=inst.postScriptFontName.replace("AlloVariable", "AlloMonoVariable") if inst.postScriptFontName else None,
            location=dict(inst.location),
        )
        mono_ds.addInstance(new_inst)

    mono_ds.write(MONO_DS_PATH)
    print(f"Generated {len(processed_ufos)} mono master UFOs and {MONO_DS_PATH.name}")


def parse_weight_from_stem(stem: str) -> int:
    if "Thin" in stem:
        return 100
    if "ExtraLight" in stem:
        return 200
    if "Light" in stem:
        return 300
    if "Medium" in stem:
        return 500
    if "SemiBold" in stem:
        return 600
    if "ExtraBold" in stem:
        return 800
    if "Bold" in stem:
        return 700
    if "Black" in stem:
        return 900
    return 400


def generate_statics() -> None:
    if MONO_STATIC_DIR.exists():
        shutil.rmtree(MONO_STATIC_DIR)
    MONO_STATIC_DIR.mkdir(parents=True, exist_ok=True)

    static_paths = sorted((SRC_DIR / "static").glob("*.ufo"))
    for path in static_paths:
        stem = path.stem
        mono_stem = stem.replace("Allo-", "AlloMono-")
        is_oblique = "Oblique" in stem
        wght = parse_weight_from_stem(stem)
        style_part = mono_stem.split("-", 1)[1]
        # Match Allo's style naming ("BoldOblique" -> "Bold Oblique" or "Oblique")
        style_nice = style_part.replace("Oblique", " Oblique").strip()

        font = ufoLib2.Font.open(path)
        if is_oblique:
            # Allo Oblique static UFOs have decomposed composites; restore component structure from matching upright UFO
            # so rebuilt/condensed mono base glyphs (I, i, l, j, r, f, t, W, M, O, ...) propagate to all accented forms.
            upright_stem = stem.replace("Oblique", "") or "Regular"
            if upright_stem == "Allo-":
                upright_stem = "Allo-Regular"
            upright_ufo = ufoLib2.Font.open(SRC_DIR / "static" / f"{upright_stem}.ufo")
            for ug in upright_ufo:
                if ug.components and not ug.contours and ug.name in font:
                    og = font[ug.name]
                    og.clearContours()
                    og.clearComponents()
                    for comp in ug.components:
                        t = list(comp.transformation)
                        t[4] += t[5] * SLANT_TAN
                        og.components.append(Component(comp.baseGlyph, tuple(t)))
        static_metrics = extract_metrics(font.layers.defaultLayer, is_oblique=is_oblique)
        transform_layer_to_mono(font.layers.defaultLayer, weight=wght, is_oblique=is_oblique, metrics=static_metrics, is_sparse=False)
        update_fontinfo_and_lib(
            font,
            family="Allo Mono",
            style=style_nice,
            ps_name=mono_stem,
            full_name=f"Allo Mono {style_nice}",
            is_oblique=is_oblique,
        )
        font.features.text = build_mono_features(font.features.text)
        font.save(MONO_STATIC_DIR / f"{mono_stem}.ufo", overwrite=True)

    print(f"Generated {len(static_paths)} mono static UFOs in {MONO_STATIC_DIR.relative_to(ROOT)}")


def main() -> None:
    generate_designspace_and_masters()
    generate_statics()


if __name__ == "__main__":
    main()
