"""pathfinders-check-render — Pathfinders QA: render cross-format

Comprueba que lo que se PINTA es igual en todos los formatos del mismo estilo.
Los checks de fontspector y de Capas B/C validan los datos declarados de cada
archivo por separado; ninguno compara el resultado renderizado entre formatos.
Por eso el bug de Tiphares v1.000 (fix_ttf_outlines.py conservaba el LSB viejo
del hmtx: glifos de TTF/WOFF/WOFF2 desplazados hasta 108 u) pasó el Release QA.

Para cada estilo (archivos con el mismo nombre base), la OTF es la referencia y
se comparan contra ella la TTF, la WOFF y la WOFF2:

1. Shaping (HarfBuzz, en unidades de fuente): cada carácter del cmap por
   separado, todos los pares de letras/cifras básicas presentes y una lista de
   pares y palabras frecuentes (kerning, ligaduras, contextuales). Glifos,
   avances y offsets deben ser IDÉNTICOS (tolerancia 0): hmtx y GPOS no cambian
   con la conversión de contornos.

2. Raster (FreeType, todos los glifos del glyph order, también los que solo se
   alcanzan por GSUB). Cada glifo se rasteriza en su origen y se compara:
     · desplazamiento = distancia entre los centroides de tinta
     · error de contorno = |A−B| integrado / perímetro de A (desplazamiento
       medio del borde)
     · avance renderizado
   a) Sin hinting, 128 ppem — OTF vs TTF/WOFF/WOFF2:
        desplazamiento ≤ 2 u · error de contorno ≤ 1 u   (u a 1000 UPM,
        se escalan con el UPM)
      Justificación: fontmake convierte cúbicas a cuadráticas (cu2qu) con
      error máximo 0,001 em = 1 u a 1000 UPM, así que el desplazamiento medio
      del borde no puede pasar de 1 u. El centroide de glifos finos o pequeños
      (paréntesis, acentos) se mueve algo más con ese mismo error: 2 u.
      Medido sobre las 84 fuentes de Tiphares 1.000 correctas: máx. 1,42 u
      (parenright Light Italic) y 0,68 u. La release con el bug de LSB da
      3–108 u; el desplazamiento mínimo del bug (3 u) mide 2,8–3,0 u de
      centroide y 1,2–1,5 u de borde, y también se detecta.
      Si un proyecto con pesos muy finos da falsos positivos (centroide > 2 u
      con borde < 1 u), se sube la tolerancia en su project-spec.toml con la
      excepción documentada en el CLAUDE.md:
        [render_crossformat]
        shift_u = 2.5   # a 1000 UPM
        edge_u = 1.0
   b) Con hinting, 24 y 72 ppem — OTF (hinter CFF) vs TTF (ttfautohint):
        desplazamiento ≤ 2,5 px · error de contorno ≤ 3 px · avance ± 1 px
      Dos hinters distintos alinean cada borde a la rejilla por su cuenta (cada
      uno hasta ~1 px con stems y overshoots), así que no pueden coincidir
      píxel a píxel. Medido en Tiphares correcta: máx. 1,82 px y 2,03 px. Esta
      pasada caza fallos groseros del hinting (instrucciones que deforman o
      mueven glifos, avances hinted rotos). 16 ppem no se usa: en trazos de
      1 px (guiones, macrones) el error relativo no es significativo.
   c) WOFF y WOFF2 contra la TTF, con y sin hinting: bitmaps y avances
      IDÉNTICOS (tolerancia 0). La compresión es sin pérdidas; cualquier
      diferencia es un error de empaquetado.

Modo opcional --previous DIR: compara cada archivo con el del mismo nombre y
formato de la versión publicada anterior (mismo shaping y raster, tolerancia
0 en todo). Por defecto las diferencias son WARN (en una versión nueva pueden
ser cambios de diseño: revisar la lista). Con --republish (re-publicación de
la misma versión, sin cambios de diseño) son FAIL.

Es determinista (sin aleatoriedad, orden fijo, misma versión de FreeType) y
paraleliza por estilo (--jobs). Si un estilo no tiene OTF, la referencia es su
TTF (WARN). Los archivos .zip se ignoran.

FAIL — shaping distinto entre formatos; glifo desplazado o alterado fuera de
       tolerancia; glifo ausente o vacío en un formato; WOFF/WOFF2 ≠ TTF;
       nombre base duplicado en el mismo formato; (--republish) cualquier
       diferencia con la versión anterior
WARN — estilo sin OTF o con un solo formato; glifos de más en un formato
       (no se pintan distinto, pero el glyph set no coincide); (--previous sin
       --republish) diferencias con la versión anterior
Exit codes: 0 sin FAILs · 1 al menos un FAIL · 2 error de uso

Requisitos: fonttools[woff], uharfbuzz, freetype-py, numpy

Se instala con pathfinders-tools como comando `pathfinders-check-render` (corre en
build-web y en el CI de cada proyecto). check_render_crossformat.py, en la raíz
del repo, es el mismo check para lanzarlo con python3 desde los skills de QA.

Usage:
    pathfinders-check-render build/otf build/ttf build/woff build/woff2
    python3 check_render_crossformat.py build/otf build/ttf build/woff build/woff2
    python3 check_render_crossformat.py fonts/Tiphares-1.000 fonts/Tiphares-Trials-1.000
    python3 check_render_crossformat.py --previous ../prev/fonts build/
    python3 check_render_crossformat.py --previous ../prev/fonts --republish fonts/
    python3 check_render_crossformat.py --json build/qa/render-crossformat.json build/
    python3 check_render_crossformat.py --spec qa/project-spec.toml build/
"""

import argparse
import io
import json
import os
import sys
import tomllib
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import freetype
import numpy as np
import uharfbuzz as hb
from fontTools.ttLib import TTFont
from fontTools.ttLib.woff2 import decompress as woff2_decompress

FORMATS = ("otf", "ttf", "woff", "woff2")

UNHINTED_PPEM = 128
HINTED_PPEMS = (24, 72)

# Tolerancias (ver docstring). Las de unidades están a 1000 UPM y se escalan.
TOL_SHIFT_U = 2.0
TOL_EDGE_U = 1.0
TOL_ADV_UNHINTED_U = 0.5
TOL_SHIFT_PX_HINTED = 2.5
TOL_EDGE_PX_HINTED = 3.0
TOL_ADV_PX_HINTED = 1.0

# Pares y palabras frecuentes, además de todos los pares [A-Za-z0-9].
EXTRA_STRINGS = [
    "ff", "fi", "fl", "ffi", "ffl", "ft", "fj", "f)", "f]", "f}", "f?", "f!", "f*",
    "T.", "T,", "V.", "V,", "W.", "Y.", "Y,", "F.", "P.", "r.", "r,", "y.", "v.", "w.",
    "L'", "L’", "T:", "T;", "“A", "A”", "‘A", "A’", "«A", "A»", "(j", "[j", "{j",
    "1/2", "11", "1.", "1,", "7.", "4.", "€1", "$1", "£1", "#1", "%1", "1%", "1‰",
    "-A", "A-", "-T", "T-", "-V", "V-", "-Y", "Y-", "–T", "T–", "—T", "T—",
    "Hamburgefontsiv", "HAMBURGEFONTSIV", "office", "affluent", "waffle",
    "Ávila", "Æsir", "Œuvre", "Łódź", "Ťava", "ďalší", "Ľudo", "ľahko", "Ŧ", "Ħ",
    "çà", "ñí", "Ñandú", "Àò", "Ÿ", "İstanbul", "ığ", "ẞ", "ß", "ĳ", "Ĳ",
    "¿Qué?", "¡Sí!", "@a", "a@", "&a", "a&", "(A)", "[A]", "{A}", "/A/", "\\A\\",
]


# ── helpers ───────────────────────────────────────────────────────────────────

def _sfnt_bytes(path: Path) -> bytes:
    """Bytes sfnt del archivo (WOFF2 descomprimida con el decoder de fontTools)."""
    if path.suffix.lower() == ".woff2":
        out = io.BytesIO()
        with open(path, "rb") as f:
            woff2_decompress(f, out)
        return out.getvalue()
    if path.suffix.lower() == ".woff":
        font = TTFont(path)
        font.flavor = None
        out = io.BytesIO()
        font.save(out, reorderTables=False)
        return out.getvalue()
    return path.read_bytes()


class Loaded:
    def __init__(self, path: Path):
        self.path = path
        self.data = _sfnt_bytes(path)
        tt = TTFont(io.BytesIO(self.data), lazy=True)
        self.order = tt.getGlyphOrder()
        self.upem = tt["head"].unitsPerEm
        self.cmap = tt.getBestCmap() or {}
        tt.close()
        self._ft = None

    def ft(self) -> freetype.Face:
        if self._ft is None:
            self._ft = freetype.Face(io.BytesIO(self.data))
        return self._ft


def _test_strings(cmap: dict[int, str]) -> list[str]:
    chars = [chr(cp) for cp in sorted(cmap) if cp >= 0x20 and not 0xD800 <= cp < 0xE000]
    basic = [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789" if ord(c) in cmap]
    strings = list(chars)
    strings += [a + b for a in basic for b in basic]
    strings += [s for s in EXTRA_STRINGS if all(ord(c) in cmap for c in s)]
    return strings


def shape_all(font: Loaded, strings: list[str]) -> list[tuple]:
    face = hb.Face(font.data)
    hbfont = hb.Font(face)
    hbfont.scale = (font.upem, font.upem)
    out = []
    for s in strings:
        buf = hb.Buffer()
        buf.add_str(s)
        buf.guess_segment_properties()
        hb.shape(hbfont, buf, {})
        out.append(tuple(
            (font.order[i.codepoint], p.x_advance, p.y_advance, p.x_offset, p.y_offset)
            for i, p in zip(buf.glyph_infos, buf.glyph_positions)
        ))
    return out


def render_all(font: Loaded, ppem: int, hinted: bool) -> dict[str, tuple]:
    """name → (left, top, bitmap float32 0..1 o None, avance px)."""
    face = font.ft()
    face.set_pixel_sizes(0, ppem)
    flags = freetype.FT_LOAD_RENDER | freetype.FT_LOAD_NO_BITMAP
    flags |= freetype.FT_LOAD_TARGET_NORMAL if hinted else freetype.FT_LOAD_NO_HINTING
    out = {}
    for gid, name in enumerate(font.order):
        face.load_glyph(gid, flags)
        g = face.glyph
        b = g.bitmap
        bm = None
        if b.width and b.rows:
            raw = np.frombuffer(bytes(b.buffer), dtype=np.uint8).reshape(b.rows, b.pitch)[:, : b.width]
            if raw.any():
                bm = raw.astype(np.float32) / 255.0
        out[name] = (g.bitmap_left, g.bitmap_top, bm, g.advance.x / 64.0)
    return out


def _centroid(left: int, top: int, bm: np.ndarray) -> tuple[float, float]:
    m = bm.sum()
    cx = (bm.sum(axis=0) * np.arange(bm.shape[1])).sum() / m
    cy = (bm.sum(axis=1) * np.arange(bm.shape[0])).sum() / m
    return left + cx + 0.5, top - cy - 0.5


def compare_glyph(a: tuple, b: tuple) -> tuple[float, float, float, str | None]:
    """→ (desplazamiento px, error de contorno px, Δavance px, problema)."""
    dadv = abs(a[3] - b[3])
    if a[2] is None and b[2] is None:
        return 0.0, 0.0, dadv, None
    if a[2] is None or b[2] is None:
        return 0.0, 0.0, dadv, "vacío en un formato y con tinta en el otro"
    if a[0] == b[0] and a[1] == b[1] and a[2].shape == b[2].shape and np.array_equal(a[2], b[2]):
        return 0.0, 0.0, dadv, None
    ax, ay = _centroid(*a[:3])
    bx, by = _centroid(*b[:3])
    shift = float(np.hypot(ax - bx, ay - by))
    # 1 px de margen: sin él, el perímetro no cuenta los bordes exteriores de la tinta
    # y en trazos de 1–2 px (guiones, underscore) el error de borde se infla.
    x0 = min(a[0], b[0]) - 1
    x1 = max(a[0] + a[2].shape[1], b[0] + b[2].shape[1]) + 1
    y1 = max(a[1], b[1]) + 1
    y0 = min(a[1] - a[2].shape[0], b[1] - b[2].shape[0]) - 1
    ca = np.zeros((y1 - y0, x1 - x0), np.float32)
    cb = np.zeros_like(ca)
    for canvas, (left, top, bm, _) in ((ca, a), (cb, b)):
        h, w = bm.shape
        canvas[y1 - top : y1 - top + h, left - x0 : left - x0 + w] = bm
    perimeter = np.abs(np.diff(ca, axis=0)).sum() + np.abs(np.diff(ca, axis=1)).sum()
    edge = float(np.abs(ca - cb).sum() / max(perimeter, 1.0))
    return shift, edge, dadv, None


def compare_renders(ref: dict, tgt: dict, names: list[str], scale: float,
                    tol_shift: float, tol_edge: float, tol_adv: float) -> list[dict]:
    """Glifos fuera de tolerancia. scale convierte px → unidad del informe."""
    bad = []
    for n in names:
        if n not in tgt:
            bad.append({"glyph": n, "issue": "ausente"})
            continue
        shift, edge, dadv, issue = compare_glyph(ref[n], tgt[n])
        shift, edge, dadv = shift * scale, edge * scale, dadv * scale
        if issue or shift > tol_shift or edge > tol_edge or dadv > tol_adv:
            bad.append({"glyph": n, "shift": round(shift, 2), "edge": round(edge, 2),
                        "dadv": round(dadv, 2), **({"issue": issue} if issue else {})})
    bad.sort(key=lambda d: (-("issue" in d), -max(d.get("shift", 0), d.get("edge", 0), d.get("dadv", 0))))
    return bad


def compare_shaping(ref: list, tgt: list, strings: list[str]) -> list[dict]:
    return [{"text": s, "ref": r, "tgt": t} for s, r, t in zip(strings, ref, tgt) if r != t]


# ── por estilo ────────────────────────────────────────────────────────────────

def _check(label: str, ref: Loaded, tgt: Loaded, strings: list[str], ref_shapes: list,
           exact: bool, cache: dict, tol_u: tuple[float, float]) -> list[dict]:
    """Compara tgt contra ref en shaping + raster sin y con hinting."""
    results = []
    k = 1000.0 / ref.upem  # unidades de fuente → u a 1000 UPM (tolerancias)
    names = ref.order

    diffs = compare_shaping(ref_shapes, shape_all(tgt, strings), strings)
    results.append({"check": "shaping", "label": label, "file": tgt.path.name,
                    "n": len(diffs), "items": diffs[:20], "unit": "u"})

    def rend(font, ppem, hinted):
        key = (str(font.path), ppem, hinted)
        if key not in cache:
            cache[key] = render_all(font, ppem, hinted)
        return cache[key]

    to_units = ref.upem / UNHINTED_PPEM
    if exact:
        tol = (0.0, 0.0, 0.0)
    else:
        tol = (tol_u[0] / k, tol_u[1] / k, TOL_ADV_UNHINTED_U / k)
    bad = compare_renders(rend(ref, UNHINTED_PPEM, False), rend(tgt, UNHINTED_PPEM, False),
                          names, to_units, *tol)
    results.append({"check": f"sin hinting @{UNHINTED_PPEM}ppem", "label": label,
                    "file": tgt.path.name, "n": len(bad), "items": bad[:20], "unit": "u"})

    for ppem in HINTED_PPEMS:
        tol = (0.0, 0.0, 0.0) if exact else (TOL_SHIFT_PX_HINTED, TOL_EDGE_PX_HINTED, TOL_ADV_PX_HINTED)
        bad = compare_renders(rend(ref, ppem, True), rend(tgt, ppem, True), names, 1.0, *tol)
        results.append({"check": f"hinting @{ppem}ppem", "label": label, "file": tgt.path.name,
                        "n": len(bad), "items": bad[:20], "unit": "px"})
    return results


def check_style(job: dict) -> dict:
    stem, files, previous, republish = job["stem"], job["files"], job["previous"], job["republish"]
    report = {"stem": stem, "fails": [], "warns": [], "passes": []}
    try:
        fonts = {fmt: Loaded(Path(p)) for fmt, p in files.items()}
        cache: dict = {}
        ref_fmt = "otf" if "otf" in fonts else "ttf" if "ttf" in fonts else None
        if ref_fmt is None:
            report["warns"].append({"msg": f"sin OTF ni TTF ({', '.join(sorted(fonts))}): "
                                           "solo se compara contra la versión anterior"})
        elif ref_fmt != "otf":
            report["warns"].append({"msg": "sin OTF: referencia = TTF"})
        if len(fonts) == 1 and not previous:
            report["warns"].append({"msg": f"un solo formato ({next(iter(fonts))}): nada que comparar"})

        def run(label, ref, tgt, exact, bucket_fail):
            strings = _test_strings(ref.cmap)
            ref_shapes = shape_all(ref, strings)
            if ref.order != tgt.order:
                missing = sorted(set(ref.order) - set(tgt.order))
                extra = sorted(set(tgt.order) - set(ref.order))
                if missing:
                    (report["fails"] if bucket_fail else report["warns"]).append(
                        {"check": "glyph set", "label": label, "file": tgt.path.name,
                         "msg": f"faltan {len(missing)} glifos de la referencia: {missing[:8]}"})
                if extra:
                    # Glifos de más no cambian lo que se pinta (si fueran alcanzables desde el
                    # cmap o GSUB, lo detecta el shaping): WARN para revisar el glyph set.
                    report["warns"].append(
                        {"check": "glyph set", "label": label, "file": tgt.path.name,
                         "msg": f"{len(extra)} glifos que no están en la referencia "
                                f"(no afectan al render; revisar glyph set): {extra[:8]}"})
            for r in _check(label, ref, tgt, strings, ref_shapes, exact, cache, job["tol_u"]):
                if r["n"]:
                    (report["fails"] if bucket_fail else report["warns"]).append(r)
                else:
                    report["passes"].append(r)

        if ref_fmt:
            ref = fonts[ref_fmt]
            for fmt in FORMATS:
                if fmt == ref_fmt or fmt not in fonts:
                    continue
                # WOFF/WOFF2 se comparan con la TTF (idénticas); la TTF con la OTF (tolerancia).
                # Por transitividad, WOFF/WOFF2 quedan también dentro de tolerancia frente a la OTF.
                if fmt in ("woff", "woff2") and "ttf" in fonts and ref_fmt != "ttf":
                    run(f"{fmt.upper()} vs TTF", fonts["ttf"], fonts[fmt], True, True)
                else:
                    exact = ref_fmt == "ttf" and fmt in ("woff", "woff2")
                    run(f"{fmt.upper()} vs {ref_fmt.upper()}", ref, fonts[fmt], exact, True)

        for fmt, ppath in (previous or {}).items():
            if fmt in fonts:
                run(f"{fmt.upper()} vs versión anterior", Loaded(Path(ppath)), fonts[fmt], True, republish)
    except Exception as e:  # un archivo ilegible es un FAIL, no un crash de todo el lote
        report["fails"].append({"msg": f"error al procesar: {type(e).__name__}: {e}"})
    return report


# ── main ──────────────────────────────────────────────────────────────────────

def collect(paths: list[str]) -> tuple[dict[str, dict[str, str]], list[str]]:
    groups: dict[str, dict[str, str]] = defaultdict(dict)
    dupes = []
    files = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(f for f in p.rglob("*") if f.is_file())
        elif p.is_file():
            files.append(p)
    for f in files:
        fmt = f.suffix.lower().lstrip(".")
        if fmt not in FORMATS or f.name.startswith("."):
            continue
        if fmt in groups[f.stem] and groups[f.stem][fmt] != str(f):
            dupes.append(f"{f.stem}.{fmt}: {groups[f.stem][fmt]} y {f}")
            continue
        groups[f.stem][fmt] = str(f)
    return dict(sorted(groups.items())), dupes


def _fmt_item(it: dict, unit: str) -> str:
    if "text" in it:
        return repr(it["text"])
    if "issue" in it:
        return f"{it['glyph']} ({it['issue']})"
    parts = []
    if it.get("shift"):
        parts.append(f"Δ{it['shift']:g}")
    if it.get("edge"):
        parts.append(f"borde {it['edge']:g}")
    if it.get("dadv"):
        parts.append(f"avance {it['dadv']:g}")
    return f"{it['glyph']} {' '.join(parts)} {unit}".strip()


def _fmt_result(r: dict) -> str:
    if "n" not in r:
        prefix = f"{r['file']} [{r['label']}] " if "file" in r else ""
        return prefix + r["msg"]
    what = "cadenas con shaping distinto" if r["check"] == "shaping" else "glifos fuera de tolerancia"
    sample = ", ".join(_fmt_item(it, r["unit"]) for it in r["items"][:6])
    return f"{r['file']} [{r['label']} · {r['check']}]: {r['n']} {what} — {sample}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Render cross-format: OTF vs TTF/WOFF/WOFF2 (HarfBuzz + FreeType)")
    ap.add_argument("paths", nargs="+", help="archivos o directorios (se buscan .otf/.ttf/.woff/.woff2 recursivamente)")
    ap.add_argument("--previous", metavar="DIR", help="versión publicada anterior: compara cada archivo con el mismo nombre y formato")
    ap.add_argument("--republish", action="store_true", help="con --previous: re-publicación, cualquier diferencia es FAIL")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1, help="procesos en paralelo (por estilo)")
    ap.add_argument("--json", metavar="FILE", help="escribe el informe completo en JSON")
    ap.add_argument("--spec", metavar="TOML", help="project-spec.toml con [render_crossformat] (por defecto qa/project-spec.toml si existe)")
    args = ap.parse_args()
    if args.republish and not args.previous:
        ap.error("--republish requiere --previous")

    groups, dupes = collect(args.paths)
    if not groups:
        print("No se encontraron fuentes .otf/.ttf/.woff/.woff2", file=sys.stderr)
        sys.exit(2)
    prev_groups = {}
    if args.previous:
        prev_groups, prev_dupes = collect([args.previous])
        dupes += [f"(anterior) {d}" for d in prev_dupes]

    tol_u = (TOL_SHIFT_U, TOL_EDGE_U)
    spec = Path(args.spec) if args.spec else Path("qa/project-spec.toml")
    if spec.exists():
        with open(spec, "rb") as f:
            cfg = tomllib.load(f).get("render_crossformat", {})
        tol_u = (float(cfg.get("shift_u", TOL_SHIFT_U)), float(cfg.get("edge_u", TOL_EDGE_U)))
        if tol_u != (TOL_SHIFT_U, TOL_EDGE_U):
            print(f"NOTA  tolerancia sin hinting del proyecto ({spec}): desplazamiento ≤ {tol_u[0]:g} u, "
                  f"borde ≤ {tol_u[1]:g} u (por defecto {TOL_SHIFT_U:g} / {TOL_EDGE_U:g})")
    elif args.spec:
        ap.error(f"no existe {spec}")

    jobs = [{"stem": s, "files": f, "previous": prev_groups.get(s), "republish": args.republish, "tol_u": tol_u}
            for s, f in groups.items()]
    if args.jobs > 1:
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            reports = list(ex.map(check_style, jobs))
    else:
        reports = [check_style(j) for j in jobs]

    fails = len(dupes)
    warns = 0
    for d in dupes:
        print(f"FAIL  nombre base duplicado en el mismo formato — {d}")
    if args.previous:
        for s in sorted(set(prev_groups) - set(groups)):
            print(f"WARN  {s}: estaba en la versión anterior y no en la actual")
            warns += 1
        for s in sorted(set(groups) - set(prev_groups)):
            print(f"WARN  {s}: nuevo, sin equivalente en la versión anterior")
            warns += 1
    for rep in reports:
        fmts = "/".join(f.upper() for f in FORMATS if f in groups[rep["stem"]])
        if not rep["fails"] and not rep["warns"]:
            print(f"PASS  {rep['stem']} ({fmts}): {len(rep['passes'])} comparaciones idénticas o dentro de tolerancia")
            continue
        for r in rep["fails"]:
            print(f"FAIL  {rep['stem']}: {_fmt_result(r)}")
        for r in rep["warns"]:
            print(f"WARN  {rep['stem']}: {_fmt_result(r)}")
        fails += len(rep["fails"])
        warns += len(rep["warns"])

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        with open(args.json, "w") as f:
            json.dump({"freetype": ".".join(map(str, freetype.version())), "harfbuzz": hb.__version__,
                       "tolerances": {"unhinted_ppem": UNHINTED_PPEM, "hinted_ppems": HINTED_PPEMS,
                                      "shift_u_at_1000upm": tol_u[0], "edge_u_at_1000upm": tol_u[1],
                                      "adv_unhinted_u_at_1000upm": TOL_ADV_UNHINTED_U,
                                      "shift_px_hinted": TOL_SHIFT_PX_HINTED, "edge_px_hinted": TOL_EDGE_PX_HINTED,
                                      "adv_px_hinted": TOL_ADV_PX_HINTED},
                       "duplicates": dupes, "styles": reports}, f, ensure_ascii=False, indent=1, default=str)

    print(f"\nResumen: {len(reports)} estilos · {fails} FAIL · {warns} WARN "
          f"(FreeType {'.'.join(map(str, freetype.version()))}, HarfBuzz {hb.__version__})")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
