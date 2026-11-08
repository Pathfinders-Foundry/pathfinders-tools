"""pathfinders-check-open-corners — Pathfinders QA: open corners llegan al binario

Glyphs aplica "Remove Open Corners" (glyphsLib.filters.eraseOpenCorners) al
compilar. Si fontmake se lanza con `--filter X` sin `'...'`, ese filtro del
source se sustituye y las esquinas abiertas salen como triangulitos en la
fuente (bug de Tiphares/Seaspray con TTF). Ninguno de los otros checks lo ve:
el triángulo mide unas pocas unidades.

Qué hace, por instancia:

1. Interpola la instancia desde el designspace (ufo2ft Instantiator, sin
   redondear, como fontmake por defecto) y localiza los open corners con el mismo
   criterio que glyphsLib (EraseOpenCornersPen), sin modificar el contorno.
2. Aplica EraseOpenCornersFilter a la instancia y calcula si el centroide de
   cada triangulito debería tener tinta una vez borrada la esquina.
3. Compara con la tinta real en el binario (OTF, TTF, WOFF, WOFF2). Si no
   coincide, el triángulo ha llegado a la fuente: FAIL.

La interpolación se hace una sola vez por instancia y se reutiliza para todos
los formatos de ese estilo; las instancias se reparten entre procesos (--jobs).

Emparejamiento binario <-> instancia: por postScriptFontName, por nombre de
archivo de la instancia o por Familia-Estilo sin espacios, contra el nameID 6
y el nombre de archivo del binario. Lo que no empareja (o empareja con más de
una instancia) se declara UNCHECKED con su motivo; nunca se da por bueno. Los
Trial no tienen instancia propia (son un recorte de la fuente completa): salen
como UNCHECKED, o con --trial-as-base se comparan contra la instancia completa
y los glifos que el recorte no contiene cuentan como no exportados.

Estados por archivo:
  PASS       todos los open corners evaluables son correctos
  FAIL       algún triángulo ha llegado al binario
  UNCHECKED  no se ha podido comprobar (sin instancia, ilegible, VF, ...)
  NOT_EVALUABLE  hay open corners pero todos son demasiado pequeños (< MIN_AREA)
                 para decidir con coordenadas enteras. Nunca cuenta como PASS.

LIMITACIÓN: el check valida cada glifo con contorno propio, y los compuestos
(acento sobre base) a través de su glifo base. No examina los open corners que
solo aparecerían al transformar o combinar componentes: si el build los aplana,
el compuesto hereda el resultado de la base, que sí se comprueba.

Uso:
  pathfinders-check-open-corners -d build/designspace/Seaspray-uprights-main.designspace \\
      -d build/designspace/Seaspray-italics-main.designspace build/otf/*.otf
  pathfinders-check-open-corners -d X.designspace build/ttf --json --html oc.html

Exit 1 si hay algún FAIL (con --strict, también si algún archivo no Trial queda
UNCHECKED).
"""

import argparse
import html
import json
import logging
import os
import random
import re
import sys
import time
import zlib
from concurrent.futures import ProcessPoolExecutor, as_completed

MIN_AREA = 2.0  # u²: por debajo, el centro no se puede evaluar con coordenadas enteras
FORMATS = {".otf": "OTF", ".ttf": "TTF", ".woff": "WOFF", ".woff2": "WOFF2"}

# Caché por proceso: designspace -> (instances, instantiator)
_LOADED = {}


# --------------------------------------------------------------------------
# Detección de open corners (misma decisión que EraseOpenCornersPen)
# --------------------------------------------------------------------------

def _make_finder():
    from fontTools.misc.bezierTools import segmentSegmentIntersections
    from fontTools.pens.basePen import BasePen
    from glyphsLib.filters.eraseOpenCorners import _pointIsLeftOfLine

    class Finder(BasePen):
        def __init__(self):
            super().__init__(None)
            self.segments = []
            self.found = []

        def _moveTo(self, p):
            self.segments = []

        def _operate(self, *pts):
            self.segments.append((self._getCurrentPoint(), *pts))

        _lineTo = _curveToOne = _qCurveToOne = _operate

        def _closePath(self):
            if self._getCurrentPoint() != self.segments[0][0]:
                self.segments.append((self._getCurrentPoint(), self.segments[0][0]))
            self.scan()

        _endPath = _closePath

        def scan(self):
            segs = self.segments
            ix = 0
            while len(segs) > 2 and ix < len(segs):
                nxt = (ix + 1) % len(segs)
                if len(segs[ix]) != 2:
                    ix += 1
                    continue
                if _pointIsLeftOfLine(segs[ix], segs[ix - 1][-2]) or _pointIsLeftOfLine(segs[ix], segs[nxt][1]):
                    ix += 1
                    continue
                hits = [i for i in segmentSegmentIntersections(segs[ix - 1], segs[nxt]) if 0 <= i.t1 <= 1 and 0 <= i.t2 <= 1]
                if not hits:
                    ix += 1
                    continue
                t1, t2 = 1 - hits[0].t1, hits[0].t2
                ok = ((t1 < 0.5 and t2 < 0.5) or (t1 < 0.3 and t2 < 0.99) or (t1 < 0.99 and t2 < 0.3)) and t1 > 0.001 and t2 > 0.001
                if ok:
                    self.found.append((tuple(hits[0].pt), segs[ix][0], segs[ix][1]))
                ix += 1

    return Finder


def _area(tri):
    (a, b), (c, d), (e, f) = tri
    return abs((c - a) * (f - b) - (e - a) * (d - b)) / 2


def _centroid(tri):
    return (sum(p[0] for p in tri) / 3, sum(p[1] for p in tri) / 3)


def _ink(glyph_set, name, point):
    from fontTools.pens.pointInsidePen import PointInsidePen
    pen = PointInsidePen(glyph_set, point)
    glyph_set[name].draw(pen)
    return pen.getWinding() != 0


def _svg(glyph_set, name, tri, status):
    from fontTools.pens.svgPathPen import SVGPathPen
    pen = SVGPathPen(glyph_set)
    glyph_set[name].draw(pen)
    xs, ys = [p[0] for p in tri], [p[1] for p in tri]
    margin = max(24, 3 * max(max(xs) - min(xs), max(ys) - min(ys)))
    x, y = min(xs) - margin, min(ys) - margin
    w, h = max(xs) - min(xs) + 2 * margin, max(ys) - min(ys) + 2 * margin
    box = f"{x:.1f} {-(y + h):.1f} {w:.1f} {h:.1f}"
    pts = " ".join(f"{p[0]:.1f},{-p[1]:.1f}" for p in tri)
    color = "#d00" if status == "FAIL" else "#0a7"
    return (
        f'<svg viewBox="{box}" width="150" height="150"><g transform="scale(1,-1)"><path d="{pen.getCommands()}" fill="#111"/></g>'
        f'<polygon points="{pts}" fill="none" stroke="{color}" stroke-width="{w / 150:.2f}"/></svg>'
    )


# --------------------------------------------------------------------------
# Designspaces e instancias
# --------------------------------------------------------------------------

def _norm(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _strip_trial(key):
    return key.replace("trial", "")


def instance_keys(inst):
    """Claves normalizadas con las que se puede emparejar un binario con la instancia."""
    keys = set()
    ps = getattr(inst, "postScriptFontName", None)
    if ps:
        keys.add(_norm(ps))
    path = inst.filename or inst.path
    if path:
        keys.add(_norm(os.path.splitext(os.path.basename(path))[0]))
    if inst.familyName and inst.styleName:
        keys.add(_norm(f"{inst.familyName}{inst.styleName}"))
    return keys


def font_keys(path, ttfont):
    keys = {_norm(os.path.splitext(os.path.basename(path))[0])}
    ps = ttfont["name"].getDebugName(6) if ttfont is not None and "name" in ttfont else None
    if ps:
        keys.add(_norm(ps))
    return keys


def _load_designspace(path):
    if path not in _LOADED:
        import ufoLib2
        from fontTools.designspaceLib import DesignSpaceDocument
        from ufo2ft.instantiator import Instantiator
        logging.getLogger("ufo2ft").setLevel(logging.ERROR)
        doc = DesignSpaceDocument.fromfile(path)
        doc.loadSourceFonts(ufoLib2.Font.open)
        # round_geometry=False: fontmake no redondea la instancia salvo con --round-instances,
        # y los open corners se detectan sobre esa geometría con decimales. Con True se
        # pierden esquinas que el build sí borra (falsos FAIL en hairlines).
        _LOADED[path] = (doc, Instantiator.from_designspace(doc, round_geometry=False))
    return _LOADED[path]


# --------------------------------------------------------------------------
# Comprobación de una instancia contra todos sus binarios
# --------------------------------------------------------------------------

def check_instance(ds_path, inst_index, font_paths, want_html, sample):
    """Interpola la instancia una vez y la compara con cada binario. Devuelve una lista de resultados."""
    from fontTools.ttLib import TTFont
    from glyphsLib.filters.eraseOpenCorners import EraseOpenCornersFilter

    doc, instantiator = _load_designspace(ds_path)
    inst = doc.instances[inst_index]
    Finder = _make_finder()

    ufo = instantiator.generate_instance(inst)
    production = ufo.lib.get("public.postscriptNames", {})
    corners = []
    for glyph in ufo:
        for contour in glyph:
            finder = Finder()
            contour.draw(finder)
            corners += [(glyph.name, tri) for tri in finder.found]
    EraseOpenCornersFilter()(ufo)  # a partir de aquí, ufo es lo que debería llevar el binario

    tiny = [(n, t) for n, t in corners if _area(t) < MIN_AREA]
    evaluable = [(n, t) for n, t in corners if _area(t) >= MIN_AREA]
    expected = [_ink(ufo, n, _centroid(t)) for n, t in evaluable]
    common = {
        "designspace": ds_path,
        "instance": inst.name,
        "cut": inst.familyName,
        "weight": inst.styleName,
        "open_corners_source": len(corners),
        "glyphs_with_corners": len({n for n, _ in corners}),
        "tiny_not_evaluable": len(tiny),
        "tiny_glyphs": sorted({n for n, _ in tiny}),
    }

    results = []
    for fp in font_paths:
        res = dict(common, file=os.path.basename(fp), path=fp, format=FORMATS[os.path.splitext(fp)[1].lower()])
        try:
            tt = TTFont(fp, lazy=True)
            binary = tt.getGlyphSet()
            order = set(tt.getGlyphOrder())
        except Exception as exc:  # noqa: BLE001 - cualquier fallo de apertura se declara
            res.update(status="UNCHECKED", reason=f"no se pudo abrir: {exc.__class__.__name__}: {exc}")
            results.append(res)
            continue
        rows, missing = [], set()
        for (name, tri), exp in zip(evaluable, expected):
            out = production.get(name, name)
            if out not in order:
                missing.add(name)
                continue
            actual = _ink(binary, out, _centroid(tri))
            rows.append({"glyph": name, "output": out, "tri": tri, "ok": exp == actual})
        fails = [r for r in rows if not r["ok"]]
        res.update(
            evaluated=len(rows),
            fail=len(fails),
            fail_glyphs=sorted({r["glyph"] for r in fails}),
            fail_points=[{"glyph": r["glyph"], "at": [round(c, 1) for c in _centroid(r["tri"])]} for r in fails],
            not_exported=len(missing),
            not_exported_glyphs=sorted(missing)[:40],
        )
        if fails:
            res["status"] = "FAIL"
        elif not rows and corners:
            res["status"] = "NOT_EVALUABLE"
            res["reason"] = (
                "todos los open corners son demasiado pequeños (< %g u²) para evaluarlos" % MIN_AREA if tiny and not evaluable
                else "ningún glifo con open corners existe en el binario (nombres de glifo no coinciden o recorte)"
            )
        else:
            res["status"] = "PASS"
        if want_html:
            rng = random.Random(zlib.crc32(res["file"].encode()))
            oks = [r for r in rows if r["ok"]]
            picked = [(r, "FAIL") for r in fails] + [(r, "OK") for r in rng.sample(oks, min(sample, len(oks)))]
            res["cards"] = [
                {"glyph": r["glyph"], "status": st, "svg": _svg(binary, r["output"], r["tri"], st)} for r, st in picked
            ]
        results.append(res)
    return results


# --------------------------------------------------------------------------
# Emparejamiento y orquestación
# --------------------------------------------------------------------------

def expand_inputs(items):
    paths = []
    for item in items:
        if os.path.isdir(item):
            paths += sorted(os.path.join(item, f) for f in os.listdir(item) if os.path.splitext(f)[1].lower() in FORMATS)
        else:
            paths.append(item)
    return paths


def match_fonts(designspaces, fonts, trial_as_base):
    """Devuelve (tareas, no_comprobados). tareas: {(ds, índice): [rutas]}."""
    from fontTools.designspaceLib import DesignSpaceDocument
    from fontTools.ttLib import TTFont

    table = {}  # clave -> {(ds, idx)}
    for ds in designspaces:
        doc = DesignSpaceDocument.fromfile(ds)
        for idx, inst in enumerate(doc.instances):
            for key in instance_keys(inst):
                table.setdefault(key, set()).add((ds, idx))

    tasks, unchecked = {}, []
    for fp in fonts:
        base = os.path.basename(fp)
        entry = {"file": base, "path": fp, "status": "UNCHECKED"}
        ext = os.path.splitext(fp)[1].lower()
        if not os.path.isfile(fp):
            unchecked.append(dict(entry, format="?", reason="no existe"))
            continue
        if ext not in FORMATS:
            unchecked.append(dict(entry, format="?", reason=f"formato no soportado: {ext}"))
            continue
        entry["format"] = FORMATS[ext]
        try:
            font = TTFont(fp, lazy=True)
            keys = font_keys(fp, font)
            is_vf = "fvar" in font
        except Exception as exc:  # noqa: BLE001
            unchecked.append(dict(entry, reason=f"no se pudo abrir: {exc.__class__.__name__}: {exc}"))
            continue
        if is_vf:
            unchecked.append(dict(entry, reason="fuente variable: el check solo cubre estáticas"))
            continue
        is_trial = any("trial" in k for k in keys)
        candidates = set()
        for key in keys:
            candidates |= table.get(key, set())
            if trial_as_base and "trial" in key:
                candidates |= table.get(_strip_trial(key), set())
        if len(candidates) == 1:
            tasks.setdefault(next(iter(candidates)), []).append(fp)
        elif not candidates:
            reason = "Trial: sin instancia propia en los designspaces (usa --trial-as-base para compararlo con la completa)" if is_trial \
                else "sin instancia en los designspaces dados"
            unchecked.append(dict(entry, reason=reason, trial=is_trial))
        else:
            unchecked.append(dict(entry, reason=f"emparejamiento ambiguo ({len(candidates)} instancias)"))
    return tasks, unchecked


def run(designspaces, fonts, jobs, want_html, sample, trial_as_base, progress=True):
    tasks, unchecked = match_fonts(designspaces, fonts, trial_as_base)
    results = list(unchecked)
    started = time.time()
    done = 0
    if jobs <= 1 or len(tasks) <= 1:
        for (ds, idx), paths in tasks.items():
            results += check_instance(ds, idx, paths, want_html, sample)
            done += 1
            if progress:
                print(f"  [{done}/{len(tasks)}] instancias ({time.time() - started:.0f} s)", file=sys.stderr)
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = [pool.submit(check_instance, ds, idx, paths, want_html, sample) for (ds, idx), paths in sorted(tasks.items())]
            for fut in as_completed(futures):
                results += fut.result()
                done += 1
                if progress:
                    print(f"  [{done}/{len(tasks)}] instancias ({time.time() - started:.0f} s)", file=sys.stderr)
    results.sort(key=lambda r: (r.get("cut") or "~", r["file"]))
    return results, time.time() - started


# --------------------------------------------------------------------------
# Informes
# --------------------------------------------------------------------------

def totals(results):
    t = {s: sum(1 for r in results if r["status"] == s) for s in ("PASS", "FAIL", "UNCHECKED", "NOT_EVALUABLE")}
    t["files"] = len(results)
    t["fail_corners"] = sum(r.get("fail", 0) for r in results)
    t["evaluated_corners"] = sum(r.get("evaluated", 0) for r in results)
    t["tiny_not_evaluable"] = sum(r.get("tiny_not_evaluable", 0) for r in results if r["status"] != "UNCHECKED")
    return t


def print_report(results, elapsed):
    for r in results:
        st = r["status"]
        if st == "UNCHECKED":
            trial = " (Trial)" if r.get("trial") else ""
            print(f"  UNCHECKED  {r['file']}{trial}: {r['reason']}")
            continue
        extra = ""
        if r.get("tiny_not_evaluable"):
            extra += f", {r['tiny_not_evaluable']} no evaluables (< {MIN_AREA:g} u²)"
        if r.get("not_exported"):
            extra += f", {r['not_exported']} glifos con open corners no exportados"
        if st == "FAIL":
            shown = ", ".join(r["fail_glyphs"][:8]) + (" ..." if len(r["fail_glyphs"]) > 8 else "")
            print(f"  FAIL       {r['file']}: {r['fail']}/{r['evaluated']} triángulos en el binario ({shown}){extra}")
        elif st == "NOT_EVALUABLE":
            print(f"  NOT_EVAL   {r['file']}: {r['reason']}{extra}")
        else:
            note = "" if r["evaluated"] else " (sin open corners en el source)"
            print(f"  PASS       {r['file']}: {r['evaluated']} evaluados, 0 triángulos{note}{extra}")
    t = totals(results)
    print(
        f"\n{t['files']} archivos: {t['PASS']} PASS, {t['FAIL']} FAIL ({t['fail_corners']} triángulos de "
        f"{t['evaluated_corners']} evaluados), {t['NOT_EVALUABLE']} NOT_EVALUABLE, {t['UNCHECKED']} UNCHECKED; "
        f"{t['tiny_not_evaluable']} open corners no evaluables por tamaño. {elapsed:.0f} s."
    )


def write_html(path, results, max_fails):
    esc = html.escape
    t = totals(results)
    shown_fails = 0
    omitted = 0
    sections = []
    for r in results:
        head = f"{esc(r['file'])} · <b class=\"{r['status']}\">{r['status']}</b>"
        if r["status"] == "UNCHECKED":
            sections.append((r.get("cut") or "Sin instancia", f"<details><summary>{head}: {esc(r['reason'])}</summary></details>"))
            continue
        cards = []
        for c in r.get("cards", []):
            if c["status"] == "FAIL":
                if shown_fails >= max_fails:
                    omitted += 1
                    continue
                shown_fails += 1
            cards.append(f'<figure class="{c["status"]}">{c["svg"]}<figcaption>{esc(c["glyph"])} · {c["status"]}</figcaption></figure>')
        detail = f"{r['evaluated']} evaluados, {r['fail']} FAIL"
        if r["tiny_not_evaluable"]:
            detail += f", <b>{r['tiny_not_evaluable']} no evaluables (&lt; {MIN_AREA:g} u²)</b>"
        if r.get("not_exported"):
            detail += f", {r['not_exported']} glifos no exportados"
        if r["status"] == "NOT_EVALUABLE":
            detail += f" — {esc(r['reason'])}"
        tiny = ""
        if r["tiny_glyphs"]:
            tiny = f"<p class=small>No evaluables: {esc(', '.join(r['tiny_glyphs'][:60]))}{' ...' if len(r['tiny_glyphs']) > 60 else ''}</p>"
        open_attr = " open" if r["status"] in ("FAIL", "NOT_EVALUABLE") else ""
        sections.append((r.get("cut") or "", f"<details{open_attr}><summary>{head}: {detail}</summary>{tiny}<main>{''.join(cards)}</main></details>"))
    body, last = [], None
    for cut, block in sections:
        if cut != last:
            body.append(f"<h2>{esc(cut)}</h2>")
            last = cut
        body.append(block)
    note = f" Se muestran {shown_fails} FAIL; {omitted} omitidos por --max-fails." if omitted else f" Se muestran todos los FAIL ({shown_fails})."
    with open(path, "w", encoding="utf-8") as out:
        out.write(
            "<!doctype html><meta charset=utf-8><title>Open corners</title>"
            "<style>body{font:14px system-ui;margin:24px;background:#fff;color:#111}main{display:flex;flex-wrap:wrap;gap:10px;margin:8px 0 16px}"
            "figure{margin:0;border:1px solid #ddd;padding:6px}figure.FAIL{border:2px solid #d00}figcaption{font-size:12px;margin-top:4px}"
            "summary{cursor:pointer;margin:4px 0}.FAIL{color:#d00}.PASS{color:#0a7}.UNCHECKED,.NOT_EVALUABLE{color:#b60}.small{font-size:12px;color:#555}"
            "h2{margin-top:28px;border-bottom:1px solid #ccc}</style>"
            f"<h1>Open corners</h1><p>{t['files']} archivos: {t['PASS']} PASS, <b class=FAIL>{t['FAIL']} FAIL</b> ({t['fail_corners']} triángulos), "
            f"{t['NOT_EVALUABLE']} NOT_EVALUABLE, {t['UNCHECKED']} UNCHECKED. Rojo: triángulo que ha llegado al binario; verde: muestra de esquinas correctas "
            f"(por cut y peso).{note}</p>" + "".join(body)
        )


def main():
    ap = argparse.ArgumentParser(
        prog="pathfinders-check-open-corners",
        description="Comprueba que los open corners del source no llegan a las fuentes exportadas.",
    )
    ap.add_argument("fonts", nargs="+", help="OTF/TTF/WOFF/WOFF2 o carpetas que los contengan")
    ap.add_argument("-d", "--designspace", action="append", required=True, help="designspace de las instancias (repetible)")
    ap.add_argument("--json", action="store_true", help="informe en JSON por stdout")
    ap.add_argument("--html", metavar="FILE", help="hoja visual: todos los FAIL y una muestra por cut y peso")
    ap.add_argument("--sample", type=int, default=2, help="correctos de muestra por archivo en la hoja HTML (def. 2)")
    ap.add_argument("--max-fails", type=int, default=2000, help="tope de tarjetas FAIL en el HTML (def. 2000, lo omitido se indica)")
    ap.add_argument("--jobs", type=int, default=max(1, min(4, (os.cpu_count() or 2) // 2)), help="procesos en paralelo (una instancia por tarea)")
    ap.add_argument("--trial-as-base", action="store_true", help="compara los Trial contra la instancia completa")
    ap.add_argument("--strict", action="store_true", help="también exit 1 si un archivo no Trial queda UNCHECKED")
    args = ap.parse_args()

    for ds in args.designspace:
        if not os.path.isfile(ds):
            ap.error(f"designspace no existe: {ds}")
    fonts = expand_inputs(args.fonts)
    if not fonts:
        ap.error("no hay fuentes que comprobar")

    results, elapsed = run(
        [os.path.abspath(d) for d in args.designspace], fonts, args.jobs, bool(args.html), args.sample, args.trial_as_base,
        progress=not args.json,
    )
    if args.html:
        write_html(args.html, results, args.max_fails)
    if args.json:
        slim = [{k: v for k, v in r.items() if k != "cards"} for r in results]
        print(json.dumps({"totals": totals(results), "elapsed_s": round(elapsed, 1), "min_area": MIN_AREA, "files": slim}, ensure_ascii=False, indent=1))
    else:
        print_report(results, elapsed)
        if args.html:
            print(f"Hoja: {args.html}")

    t = totals(results)
    bad_unchecked = [r for r in results if r["status"] == "UNCHECKED" and not r.get("trial")]
    sys.exit(1 if t["FAIL"] or (args.strict and bad_unchecked) else 0)


if __name__ == "__main__":
    main()
