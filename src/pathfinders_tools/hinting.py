"""Hinting declarativo: qué estilos van sin ttfautohint y por qué.

Problema (Seaspray, 2026-10-22): ttfautohint deja a altura 0 los trazos
horizontales de <= 8 u a 1000 UPM (9 u ya sobreviven), a cualquier ppem hasta el
hinting-limit (200). En pesos con hairlines (Thin, Stencil...) desaparecen
guiones, barras y serifas finas en todo renderizador que ejecute las
instrucciones TrueType (Windows). No hay opción de ttfautohint que lo evite
(probadas: -a nnn/sss/qqq, -x 0, -p, -s, -H, -W, -l/-r, control file, -f none -S).
La solución es no hintar esos estilos (TTF sin instrucciones, con gasp de
smoothing sin gridfit).

Configuración, en qa/project-spec.toml:

    [ttf]
    unhinted = ["*-Thin*", "*Stencil*"]   # patrones fnmatch sobre el nombre de archivo
                                          # sin extensión (p. ej. SeasprayMC-ThinItalic)

Comandos (se instalan con pathfinders-tools):

  pathfinders-hint-plan --spec S (--designspace DS | --source X.glyphspackage) (--hinted | --unhinted)
      Imprime la regex de instancias (para `fontmake -i`) de cada grupo; vacía si
      el grupo no tiene instancias en ese designspace. El Makefile compila el
      grupo hinted con `-a` y el unhinted sin él.

  pathfinders-check-hinting [--max-u 8] [--min-len 20] [--spec S] [--ttf DIR] FUENTES...
      Detector: lista por estilo los glifos con trazos horizontales <= max-u
      (a 1000 UPM; se escala con el UPM). Con --spec: avisa si un estilo con
      esos trazos no está en `unhinted` (WARN, no FAIL: el detector es conservador,
      y la prueba definitiva es pathfinders-check-render sobre la TTF hinted: un
      estilo con 1-2 glifos marginales puede seguir hinted). Con --ttf: verifica que las TTF
      declaradas unhinted no llevan instrucciones (fpgm/prep/cvt) y que las
      demás sí. También avisa de un estilo `unhinted` sin trazos finos: es
      una elección de diseño válida, pero conviene saberlo.
"""

import argparse
import fnmatch
import re
import sys
import tomllib
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont

MAX_U = 8.0      # grosor máximo (u a 1000 UPM) que ttfautohint colapsa
MIN_LEN_U = 20.0  # longitud mínima del segmento horizontal para contar como trazo


# ── spec ──────────────────────────────────────────────────────────────────────

def load_patterns(spec: str | None) -> list[str]:
    if not spec or not Path(spec).exists():
        return []
    with open(spec, "rb") as f:
        cfg = tomllib.load(f).get("ttf", {})
    pats = cfg.get("unhinted", [])
    if not isinstance(pats, list) or not all(isinstance(p, str) for p in pats):
        raise SystemExit(f"{spec}: [ttf] unhinted debe ser una lista de patrones")
    return pats


def is_unhinted(stem: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(stem, p) for p in patterns)


# ── plan para el Makefile ─────────────────────────────────────────────────────

def plan_main() -> None:
    ap = argparse.ArgumentParser(description="Regex de instancias hinted/unhinted de un designspace")
    ap.add_argument("--spec", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--designspace", help="designspace del que leer las instancias")
    src.add_argument("--source", help=".glyphs/.glyphspackage (builds con `fontmake -g`)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--hinted", action="store_true")
    g.add_argument("--unhinted", action="store_true")
    args = ap.parse_args()
    pats = load_patterns(args.spec)
    names = []
    if args.designspace:
        root = ET.parse(args.designspace).getroot()
        for inst in root.iter("instance"):
            stem = Path(inst.get("filename", "")).stem or inst.get("name", "")
            if is_unhinted(stem, pats) == args.unhinted:
                names.append(re.escape(inst.get("name")))
    else:
        import glyphsLib
        font = glyphsLib.GSFont(args.source)
        for inst in font.instances:
            if not inst.active:
                continue
            fam = inst.familyName or font.familyName
            stem = f"{fam}-{inst.name}".replace(" ", "")
            if is_unhinted(stem, pats) == args.unhinted:
                names.append(re.escape(f"{fam} {inst.name}"))
    if names:
        print("^(" + "|".join(names) + ")$")


# ── detector de trazos finos ──────────────────────────────────────────────────

def _contours(glyphset, name):
    pen = DecomposingRecordingPen(glyphset)
    glyphset[name].draw(pen)
    cur, out = [], []
    for op, args in pen.value:
        if op == "moveTo":
            cur = [args[0]]
        elif op in ("lineTo", "curveTo", "qCurveTo"):
            if args and args[-1] is not None:
                cur.append(args[-1])
        elif op in ("closePath", "endPath"):
            if len(cur) > 2:
                out.append(cur)
            cur = []
    return out


def _area(pts):
    return sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1])) / 2


def thin_stems(font: TTFont, max_u: float = MAX_U, min_len: float = MIN_LEN_U) -> dict[str, float]:
    """glifo → grosor mínimo (u a 1000 UPM) de sus trazos horizontales <= max_u."""
    k = 1000.0 / font["head"].unitsPerEm
    max_f, len_f = max_u / k, min_len / k
    gs = font.getGlyphSet()
    found = {}
    for name in font.getGlyphOrder():
        cs = _contours(gs, name)
        if not cs:
            continue
        # Orientación de los contornos exteriores: signo del de mayor área.
        sign0 = 1 if _area(max(cs, key=lambda c: abs(_area(c)))) > 0 else -1
        up, down = [], []  # tramos horizontales con tinta encima / debajo: (y, x0, x1)
        for c in cs:
            for (x0, y0), (x1, y1) in zip(c, c[1:] + c[:1]):
                if y0 == y1 and abs(x1 - x0) >= len_f:
                    d = (x1 - x0) * sign0
                    (up if d > 0 else down).append((y0, min(x0, x1), max(x0, x1)))
        best = None
        for yl, a0, a1 in up:           # borde inferior del trazo (tinta encima)
            for yu, b0, b1 in down:     # borde superior (tinta debajo)
                t = yu - yl
                if 0 < t <= max_f and min(a1, b1) - max(a0, b0) >= len_f:
                    best = t if best is None else min(best, t)
        if best is not None:
            found[name] = round(best * k, 1)
    return found


def _scan(args):
    path, max_u, min_len = args
    f = TTFont(path, lazy=True)
    return Path(path).stem, thin_stems(f, max_u, min_len)


def main() -> None:
    ap = argparse.ArgumentParser(description="Detecta trazos horizontales que ttfautohint colapsa y verifica el plan unhinted")
    ap.add_argument("fonts", nargs="+", help="OTF/TTF o directorios (se usa una fuente por estilo, prefiriendo .otf)")
    ap.add_argument("--max-u", type=float, default=MAX_U, help=f"grosor máximo en u a 1000 UPM (def. {MAX_U:g})")
    ap.add_argument("--min-len", type=float, default=MIN_LEN_U, help=f"longitud mínima del trazo en u (def. {MIN_LEN_U:g})")
    ap.add_argument("--spec", help="project-spec.toml con [ttf] unhinted: FAIL si un estilo con trazos finos está hinted")
    ap.add_argument("--ttf", metavar="DIR", help="verifica que las TTF unhinted no llevan instrucciones y las demás sí")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--suggest", action="store_true", help="imprime los estilos con trazos finos como lista para [ttf] unhinted")
    args = ap.parse_args()

    files: dict[str, str] = {}
    for p in map(Path, args.fonts):
        cand = sorted(p.rglob("*")) if p.is_dir() else [p]
        for f in cand:
            if f.suffix.lower() in (".otf", ".ttf") and (f.stem not in files or f.suffix.lower() == ".otf"):
                files[f.stem] = str(f)
    if not files:
        print("No se encontraron OTF/TTF", file=sys.stderr)
        sys.exit(2)
    pats = load_patterns(args.spec)
    with ProcessPoolExecutor(args.jobs) as ex:
        res = dict(ex.map(_scan, [(p, args.max_u, args.min_len) for p in files.values()]))

    fails = warns = 0
    for stem in sorted(res):
        stems = res[stem]
        decl = is_unhinted(stem, pats)
        info = ""
        if stems:
            worst = sorted(stems.items(), key=lambda kv: kv[1])[:5]
            info = f"{len(stems)} glifos con trazos <= {args.max_u:g} u (mín. {worst[0][1]:g} u): " + \
                   ", ".join(f"{n} {t:g}" for n, t in worst)
        if args.spec:
            if stems and not decl:
                print(f"WARN  {stem}: {info} — hinted; ttfautohint puede colapsarlos. "
                      "Decide check-render (el detector es conservador); si falla, añadir a [ttf] unhinted")
                warns += 1
            elif decl and not stems:
                print(f"WARN  {stem}: declarado unhinted pero sin trazos <= {args.max_u:g} u")
                warns += 1
            else:
                print(f"PASS  {stem}: {'unhinted — ' + info if decl else 'hinted, sin trazos finos'}")
        elif stems:
            print(f"{stem}: {info}")

    if args.ttf:
        for p in sorted(Path(args.ttf).glob("*.ttf")):
            t = TTFont(p, lazy=True)
            has = [x for x in ("fpgm", "prep", "cvt ") if x in t]
            decl = is_unhinted(p.stem, pats)
            if decl and has:
                print(f"FAIL  {p.name}: declarado unhinted pero lleva {has}")
                fails += 1
            elif not decl and not has:
                print(f"FAIL  {p.name}: debería estar hinted y no lleva instrucciones")
                fails += 1
            elif decl:
                gasp = t["gasp"].gaspRange if "gasp" in t else None
                if gasp != {65535: 10}:
                    print(f"FAIL  {p.name}: unhinted con gasp {gasp} (se espera {{65535: 10}}: smoothing, sin gridfit)")
                    fails += 1

    if args.suggest:
        sug = sorted(s for s, v in res.items() if v)
        print("\nEstilos con trazos finos:", ", ".join(sug) if sug else "ninguno")
    print(f"\nResumen: {len(res)} estilos · {sum(1 for v in res.values() if v)} con trazos finos · {fails} FAIL · {warns} WARN")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
