#!/usr/bin/env python3
"""Compile Allo from its editable, finished UFO masters. No drawing tools required."""
import hashlib
import json
import os
from pathlib import Path
import time
import zipfile

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / 'release.json').read_text())
os.environ.setdefault('SOURCE_DATE_EPOCH', str(CONFIG['sourceDateEpoch']))
from fontTools.designspaceLib import DesignSpaceDocument
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont
from ufo2ft import compileOTF, compileVariableTTF
import ufoLib2


def save(font, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    font.save(path)
    font.flavor = 'woff2'
    web = ROOT / 'fonts/webfonts' / (path.stem + '.woff2')
    web.parent.mkdir(parents=True, exist_ok=True)
    font.save(web)
    return [path, web]


def friendly_web(variable, var_stem='AlloVariable', family_label='Allo Variable'):
    """The lighter web font: the variable font with roundness fixed at Friendly (50), keeping
    optical size and weight; about half the size. Its own family, "<family_label> Friendly", so it never stands in
    for the full font. Web only (fonts/webfonts/<var_stem>Friendly.woff2)."""
    from fontTools.varLib.instancer import instantiateVariableFont
    font = instantiateVariableFont(variable, {'ROND': 50})
    friendly_stem = f'{var_stem}Friendly'
    friendly_label = f'{family_label} Friendly'
    for record in font['name'].names:
        text = record.toUnicode()
        new = text.replace(var_stem, friendly_stem).replace(family_label, friendly_label)
        if new != text:
            record.string = new
    font.flavor = 'woff2'
    web = ROOT / 'fonts/webfonts' / f'{friendly_stem}.woff2'
    font.save(web)
    return web


def drop_unreachable(tt):
    """Take out of a compiled font the glyphs nothing can reach: no character, no GSUB rule makes them,
    and no glyph that is reached uses them as a component (a CFF font has none: they're decomposed).
    They stay in the sources (the parts glyphs are built from, a duplicate ○). The font's subsetter
    does it, keeping every character, feature, name and table, and every master's deltas with their
    glyphs. The authoring build has the same function. Returns the names taken out."""
    from fontTools import subset
    options = subset.Options(
        layout_features=["*"], layout_scripts=["*"], name_IDs=["*"], name_languages=["*"], name_legacy=True,
        glyph_names=True, legacy_cmap=True, symbol_cmap=True, notdef_glyph=True, notdef_outline=True,
        hinting=True, drop_tables=[], passthrough_tables=True, prune_unicode_ranges=False,
        prune_codepage_ranges=False, recalc_bounds=False, recalc_timestamp=False, recalc_average_width=False,
        recalc_max_context=False)
    before = tt.getGlyphOrder()
    if "gvar" in tt:
        for name in before:
            tt["gvar"].variations.setdefault(name, [])
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=tt.getBestCmap())
    subsetter.subset(tt)
    kept = set(tt.getGlyphOrder())
    return [name for name in before if name not in kept]


WIN_PAST = ("circumflexacutecomb.cn", "circumflexgravecomb.cn", "circumflexhookcomb.cn", "circumflextildecomb.cn",
            "breveacutecomb.cn", "brevegravecomb.cn", "brevetildecomb.cn", "brevecomb_hookabovecomb", "dotbelow", "dotbelowcomb",
            "cedillacomb")
WIN_PAST_LIMIT = (2300, 560)


def check_win(fonts, win):
    """Every glyph in these fonts (UFOs, every layer) inside the Windows ascent and descent `win`, which all
    seven fonts share (so old Windows apps space them alike); raises if one would be cut off. Raise
    openTypeOS2WinAscent/Descent in every source then."""
    import math
    past_names = set(WIN_PAST)
    for f in fonts:
        for layer in f.layers:
            for g in layer:
                if any(c.baseGlyph in WIN_PAST for c in g.components): past_names.add(g.name)
    reach = {False: [0, 0], True: [0, 0]}
    for f in fonts:
        for layer in f.layers:
            for g in layer:
                past = g.name in past_names
                pen = BoundsPen(layer)
                g.draw(pen)
                if pen.bounds:
                    r = reach[past]
                    r[0], r[1] = max(r[0], math.ceil(pen.bounds[3])), max(r[1], math.ceil(-pen.bounds[1]))
    (hi, lo), (phi, plo) = reach[False], reach[True]
    if hi > win[0] or lo > win[1]:
        raise ValueError(f'glyphs reach {hi}/{lo} (ascent/descent), past the Windows metrics {win}')
    if phi > WIN_PAST_LIMIT[0] or plo > WIN_PAST_LIMIT[1]:
        raise ValueError(f'stacked accents or the dot below reach {phi}/{plo}, past WIN_PAST_LIMIT {WIN_PAST_LIMIT}')


def _crossings(glyphs, name, at, vertical=False):
    """Where the line y = at (vertical: x = at) crosses the glyph's outline, in order along it."""
    from fontTools.misc.bezierTools import calcCubicParameters, solveCubic
    pen = DecomposingRecordingPen(glyphs)
    glyphs[name].draw(pen)
    k = 0 if vertical else 1
    hits, start, last = [], None, None
    for op, pts in pen.value:
        if op == "moveTo":
            start = last = pts[0]
            continue
        if op in ("closePath", "endPath"):
            pts = (start,)
        if op != "curveTo":
            (x0, y0), (x1, y1) = last, pts[-1]
            pts = ((x0 + (x1 - x0) / 3, y0 + (y1 - y0) / 3), (x0 + 2 * (x1 - x0) / 3, y0 + 2 * (y1 - y0) / 3), pts[-1])
        a, b, c, d = calcCubicParameters(last, *pts)
        for t in solveCubic(a[k], b[k], c[k], d[k] - at):
            if 0 <= t < 1:
                hits.append(((a[1 - k] * t + b[1 - k]) * t + c[1 - k]) * t + d[1 - k])
        last = pts[-1]
    return sorted(hits)


def hint_values(fonts):
    """PostScript hinting values for the static CFF fonts, measured from the uprights (TTFonts, keyed by
    weight name): one set of alignment zones for the whole family, so the weights align together, and
    each weight's stems. Returns {weight name: Private dict values}."""
    zones = None
    stems = {}
    for name, tt in fonts.items():
        glyphs = tt.getGlyphSet()

        def box(g):
            pen = BoundsPen(glyphs)
            glyphs[g].draw(pen)
            return pen.bounds

        low = lambda names: min(box(g)[1] for g in names)
        high = lambda names: max(box(g)[3] for g in names)
        over = -low("o O zero e s c".split())
        xh, cap = box("x")[3], min(box(g)[3] for g in ("H", "one"))
        asc = min(box(g)[3] for g in "b d h k l".split())
        here = [(-over, 0), (xh, high("o e s c".split())), (cap, high(["O", "zero"])),
                (asc, asc + over),
                (low(["g", "j"]), max(box(g)[1] for g in "p q y".split()))]
        zones = here if zones is None else [(min(a, c), max(b, d)) for (a, b), (c, d) in zip(zones, here)]
        n = _crossings(glyphs, "n", xh / 2)
        h = _crossings(glyphs, "H", cap / 4)
        o = _crossings(glyphs, "o", sum(box("o")[::2]) / 2, vertical=True)
        bar = _crossings(glyphs, "H", sum(box("H")[::2]) / 2, vertical=True)
        lower_v, upper_v, lower_h, upper_h = (round(s[1] - s[0]) for s in (n, h, o, bar))
        stems[name] = {"StdVW": lower_v, "StdHW": lower_h,
                       "StemSnapV": sorted({lower_v, upper_v}), "StemSnapH": sorted({lower_h, upper_h})}
    zones = [(round(a), round(b)) for a, b in zones]
    blues, other = sorted(zones[:4]), zones[4:]
    edges = [v for zone in blues for v in zone]
    assert edges == sorted(edges) and len(set(edges)) == len(edges), ("overlapping zones", blues)
    blues, other = [v for zone in blues for v in zone], [v for zone in other for v in zone]
    scale = round(0.75 / max(b - a for a, b in zones), 6)
    return {name: {"BlueValues": blues, "OtherBlues": other, "BlueScale": scale} | s for name, s in stems.items()}


def hint_otfs(pairs):
    """Autohint the static CFF fonts in place (AFDKO's otfautohint), so stems snap to whole pixels
    where a rasteriser uses hints (Windows, FreeType); macOS ignores them. `pairs`: {weight name:
    (upright path, oblique path)}. First each Private dict gets hint_values' zones and stems; an
    oblique takes its upright's (slanting keeps heights and the horizontal widths of stems). Then
    otfautohint writes stems and hint masks, and the font is subroutinized again (otfautohint
    leaves every charstring flat, about twice the size). Outlines are untouched."""
    import cffsubr
    from afdko.otfautohint.__main__ import main as otfautohint
    values = hint_values({name: TTFont(upright) for name, (upright, _) in pairs.items()})
    for name, paths in pairs.items():
        for path in paths:
            tt = TTFont(path)
            private = tt["CFF "].cff.topDictIndex[0].Private
            for key, value in values[name].items():
                setattr(private, key, value)
            tt.save(path)
            if otfautohint([str(path), "--ignore-fontinfo", "--traceback", "-p", "1"]):
                raise RuntimeError(f"otfautohint failed on {path}")
            tt = TTFont(path)
            cffsubr.subroutinize(tt)
            tt.save(path)


def exact_small(tt, d):
    """The optical axis's small end is the text size with every glyph moved d units each side (release.json
    "smallShift"). Compiling leaves rounding noise in the tuples below the text size; this replaces them with
    the exact move: each point of a glyph with an advance d across, its advance 2d wider, composites' offsets
    so each part lands with its glyph (zero-width marks stay). Edited in place."""
    from fontTools.misc.roundTools import otRound
    from fontTools.ttLib.tables.TupleVariation import TupleVariation
    glyf, gvar, metrics = tt['glyf'], tt['gvar'], tt['hmtx'].metrics
    moves = {name: (d if metrics[name][0] else 0) for name in tt.getGlyphOrder()}
    for name in tt.getGlyphOrder():
        kept = [v for v in gvar.variations.get(name, []) if v.axes.get('opsz', (0, 0, 0))[1] >= 0]
        g = glyf[name]
        coords, control = glyf._getCoordinatesAndControls(name, metrics)
        if moves[name] or (g.isComposite() and any(moves[c.glyphName] for c in g.components)):
            dg = moves[name]
            if g.isComposite():
                deltas = []
                for c in g.components:
                    xx, xy = (c.transform[0][0], c.transform[0][1]) if hasattr(c, 'transform') else (1, 0)
                    deltas.append((otRound(dg - xx * moves[c.glyphName]), otRound(-xy * moves[c.glyphName])))
            else:
                deltas = [(dg, 0)] * (len(coords) - 4)
            deltas += [(0, 0), (2 * dg, 0), (0, 0), (0, 0)]
            tv = TupleVariation({'opsz': (-1.0, -1.0, 0.0)}, deltas)
            tv.optimize(coords, control.endPts, isComposite=g.isComposite())
            kept.append(tv)
        if kept:
            gvar.variations[name] = kept
        else:
            gvar.variations.pop(name, None)


SMART_DROPOUT = False


def smooth_unhinted(tt):
    '''The font has no TrueType hints: ask for smoothing at every size (gasp), so Windows doesn't pick a
    mode by size, and turn on smart dropout control (prep), so thin parts don't vanish at small sizes.
    The same tables as gftools fix-nonhinting; maxp's stack depth counts the one value prep pushes at a
    time (the spec counts prep there; fontTools doesn't).'''
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables import ttProgram
    tt['gasp'] = newTable('gasp')
    tt['gasp'].gaspRange = {0xFFFF: 15}
    if SMART_DROPOUT:
        tt['prep'] = newTable('prep')
        tt['prep'].program = ttProgram.Program()
        tt['prep'].program.fromBytecode(b'\xb8\x01\xff\x85\xb0\x04\x8d')
        tt['maxp'].maxStackElements = max(tt['maxp'].maxStackElements, 1)


WEIGHT_NAMES = ('Thin', 'ExtraLight', 'Light', 'Regular', 'Medium', 'SemiBold', 'Bold', 'ExtraBold', 'Black')

ZIP_README = '''
  ALLO  {version} ............................................ type specimen

  A friendly sans-serif typeface by Josh Puckett.
  Nine weights with obliques, and a variable font with three axes.


  AXES  (variable font)
    wght   100 ●━━━━━━━━━○━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━● 900
                         400
    opsz     9 ●━━━━━━━━━○━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━● 32
               small     14 text                     display
    ROND     0 ●━━━━━━━━━━━━━━━━━━━━━━━○━━━━━━━━━━━━━━━━━━━━● 100
               sharp               friendly               round

    Display (32) is drawn and spaced for headlines; below 14 only the
    spacing opens up. The static fonts are Text size, Friendly.


  STYLES
    100 Thin        400 Regular     700 Bold
    200 ExtraLight  500 Medium      800 ExtraBold
    300 Light       600 SemiBold    900 Black
                                    + an Oblique of each


  FEATURES
    ss01   Single-story a       a, and its accented forms
    ss02   Disambiguation       I l 1 0  →  serifed I, tailed l,
                                footed 1, slashed zero
    ss03   Open figures         4 6 9 with open forms
    cv01   Footed 1             cv02   Slashed zero
    cv03   Tailed l             cv04   Serifed I
    cv05   Dotted zero

    tnum   Tabular figures      every figure the same width
    zero   Slashed zero
    frac   Fractions            1/2 → ½   (1 1/2 and 11/14/2026 stay)
    sups   Superscript          2 → ²
    subs   Subscript            2 → ₂
    numr   Numerators           dnom   Denominators
    case   Capital forms        punctuation and signs sit with
                                capitals: (HELLO) [A–Z] ¿QUÉ?
    liga   Ligatures            tt ff tf ft share one crossbar
    calt   Contextual           automatic, on by default:
             typed arrows       a -> b   a <- b   a --> b
             lifting            (A) [1] −$4.50 +2% ¡HOLA! @ACME
    kern   Kerning

    Arrows need a space or nothing either side, so p->next stays code.
    Letter-spacing turns ligatures off in most browsers.


  CHARACTERS  ({characters})
    Latin        A–Z a–z, accented forms, Vietnamese, ẞ ß Æ æ Œ œ
    Figures      0–9  ⁰¹²³⁴⁵⁶⁷⁸⁹  ₀₁₂₃₄₅₆₇₈₉  ½ ⅓ ¼ ⅔ ¾ ⅛ ⅜ ⅝ ⅞
    Punctuation  ! ? ¡ ¿ … · • † ‡ § ¶ ‘ ’ “ ” « » ‹ › ‐ – — ‒
    Currency     $ ¢ £ ¥ € ₹ ₩ ₽ ₺ ₪ ฿ ₫ ₴ ₦ ₱ ₿ ƒ
    Math         + − × ÷ = ≠ ≈ < > ≤ ≥ ± ∞ ¬ ° ′ ″ ‰ № ℃ ℉
    Arrows       ← → ↑ ↓ ↔ ↕ ↖ ↗ ↘ ↙ ⇐ ⇒ ⇔ ↩ ↵ ↻ ↺
    Keys         ⌘ ⌥ ⇧ ⌃ ⎋ ⇥ ⌫ ⌦ ⏎ ␣ ⌕
    Shapes       ● ○ ■ □ ▲ ▼ ▶ ◀ ◆ ★ ♥ ⚠ ✓ ✕ ✗ ❮ ❯


  FILES
    fonts/otf/        18 static fonts. Install these on your computer.
    fonts/variable/   AlloVariable.ttf, for apps with variable fonts.
                      Its family is "Allo Variable", so it installs
                      beside the static fonts.
    fonts/webfonts/   The same fonts as WOFF2, for websites, plus
                      AlloVariableFriendly.woff2: the variable font
                      with roundness fixed at Friendly (50). About half
                      the size; it has no ROND axis.
    OFL.txt           The license.

    Mac       double-click a font, then Install
    Windows   select the files, right-click, Install


  WEB
    @font-face {
      font-family: "Allo";
      src: url("AlloVariable.woff2") format("woff2");
      font-weight: 100 900;
    }
    body { font-family: "Allo", sans-serif; }
    /* Optical size follows the font size on its own.
       To keep Text everywhere: font-optical-sizing: none; */
    .round { font-variation-settings: "ROND" 100; }

    Not changing roundness? Use AlloVariableFriendly.woff2 instead:
    about half the size, optical size and weight still work, and
    roundness stays at 50 ("ROND" does nothing).


  ....................................................................
  Josh Puckett  ·  joshpuckett.me  ·  github.com/joshpuckett/allo
  SIL Open Font License 1.1
'''


MONO_ZIP_README = '''
  ALLO MONO  {version} ....................................... type specimen

  A friendly monospace typeface for coding and terminals, based on Allo.
  Fixed 1060-unit cell width (0.518 em), nine weights with obliques,
  and a variable font with three axes.


  AXES  (variable font)
    wght   100 ●━━━━━━━━━○━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━● 900
                         400
    opsz    14 ○━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━● 32
               text                                     display
    ROND     0 ●━━━━━━━━━━━━━━━━━━━━━━━○━━━━━━━━━━━━━━━━━━━━● 100
               sharp               friendly               round


  FEATURES
    ss01   Single-story a       a, and its accented forms
    ss02   Disambiguation       enabled by default (serifed I, i, l, j;
                                footed 1; slashed zero)
    ss03   Open figures         4 6 9 with open forms
    cv05   Dotted zero          cv06   Plain zero       cv07   Serifless I
    calt   Coding ligatures     ->  <-  -->  <--  <->  =>  <=>
                                !=  !==  ==  ===  <=  >=
                                (1-to-1 cell-preserving, grid-safe)

    Includes Unicode Box Drawing (U+2500–U+257F), Block Elements
    (U+2580–U+259F), and Powerline symbols (U+E0A0–U+E0A2, U+E0B0–U+E0B3).


  FILES
    fonts/otf/        18 static fonts (AlloMono-*.otf). Install for editors/terminals.
    fonts/variable/   AlloMonoVariable.ttf ("Allo Mono Variable").
    fonts/webfonts/   WOFF2 fonts + AlloMonoVariableFriendly.woff2 (ROND=50).
    OFL.txt           SIL Open Font License 1.1.
'''


def build_family(ds_path, static_dir, var_stem, family_label, static_prefix, small_shift=0):
    """Compile one font family (variable TTF + friendly WOFF2 + 18 static hinted OTFs & WOFF2s)."""
    ds = DesignSpaceDocument.fromfile(ds_path)
    ds.loadSourceFonts(ufoLib2.Font.open)
    for instance in ds.instances:
        instance.localisedStyleName = {'en': instance.styleName, **instance.localisedStyleName}
    statics = {path.stem: ufoLib2.Font.open(path) for path in sorted(static_dir.glob('*.ufo'))}
    default = ds.findDefault().font.info
    win = (default.openTypeOS2WinAscent, default.openTypeOS2WinDescent)
    assert all((f.info.openTypeOS2WinAscent, f.info.openTypeOS2WinDescent) == win for f in [s.font for s in ds.sources] + list(statics.values())), 'Windows metrics differ between sources'
    check_win([s.font for s in ds.sources] + list(statics.values()), win)
    variable = compileVariableTTF(ds, useProductionNames=False, flattenComponents=True)
    if small_shift:
        exact_small(variable, small_shift)
    drop_unreachable(variable)
    smooth_unhinted(variable)
    files = save(variable, ROOT / f'fonts/variable/{var_stem}.ttf')
    files.append(friendly_web(variable, var_stem=var_stem, family_label=family_label))
    otfs = {}
    for stem, ufo in statics.items():
        otfs[stem] = ROOT / 'fonts/otf' / (stem + '.otf')
        otfs[stem].parent.mkdir(parents=True, exist_ok=True)
        otf = compileOTF(ufo, removeOverlaps=False, useProductionNames=False)
        drop_unreachable(otf)
        otf.save(otfs[stem])
    hint_otfs({w: (otfs[f'{static_prefix}-{w}'], otfs[f'{static_prefix}-' + ('' if w == 'Regular' else w) + 'Oblique']) for w in WEIGHT_NAMES})
    for path in otfs.values():
        files += save(TTFont(path), path)
    return files


def write_archive(archive, prefix, files, readme_text):
    date = time.gmtime(int(os.environ['SOURCE_DATE_EPOCH']))[:6]
    entries = {str(path.relative_to(ROOT)): path.read_bytes() for path in files if path.name != 'SHA256SUMS'}
    entries['README.txt'] = readme_text.encode()
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(prefix + '/' + name, date)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)


def main():
    allo_files = build_family(
        ROOT / 'sources/Allo.designspace',
        ROOT / 'sources/static',
        'AlloVariable',
        'Allo Variable',
        'Allo',
        small_shift=CONFIG.get('smallShift', 0),
    )
    mono_files = []
    if (ROOT / 'sources/AlloMono.designspace').exists():
        mono_files = build_family(
            ROOT / 'sources/AlloMono.designspace',
            ROOT / 'sources/mono-static',
            'AlloMonoVariable',
            'Allo Mono Variable',
            'AlloMono',
            small_shift=0,
        )
    all_files = allo_files + mono_files
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in all_files}
    (ROOT / 'fonts/SHA256SUMS').write_text(''.join(f'{digest}  {name}\n' for name, digest in sorted(hashes.items())))
    allo_char_count = len(TTFont(ROOT / 'fonts/variable/AlloVariable.ttf').getBestCmap())
    readme_text = ZIP_README.replace('{version}', CONFIG['version']).replace('{characters}', str(allo_char_count))
    archive = ROOT / f"Allo-{CONFIG['version']}.zip"
    write_archive(archive, 'Allo-' + CONFIG['version'], allo_files + [ROOT / 'fonts/SHA256SUMS', ROOT / 'OFL.txt'], readme_text)
    if mono_files:
        mono_archive = ROOT / f"AlloMono-{CONFIG['version']}.zip"
        mono_readme = MONO_ZIP_README.replace('{version}', CONFIG['version'])
        write_archive(mono_archive, 'AlloMono-' + CONFIG['version'], mono_files + [ROOT / 'fonts/SHA256SUMS', ROOT / 'OFL.txt'], mono_readme)
    print(f'{len(hashes)} font files built; {archive.name}')


if __name__ == '__main__':
    main()
