"""
pathfinders-fix-trial-subset — recorta las fuentes Trial al glyph set exacto.

Por qué existe: fontmake aplica "Keep Glyphs" llamando al subsetter de fontTools
con layout_features=["*"] y layout_closure activado (fontmake/font_project.py,
subset_otf_from_ufo). El cierre GSUB vuelve a meter todo glifo alcanzable por
sustitución desde los conservados (ligaduras, .fina, numr/dnom, sups/subs,
ordinales...). Resultado: Trials con ~200 glifos en vez de 69. fontmake no
permite desactivarlo, así que se re-subsetea el binario con layout_closure=False:
se conservan las features (kern, liga fi/fl...) pero solo sobre los glifos del set.

Glyph set:
  - por defecto, el set canónico Pathfinders (69 glifos);
  - si qa/project-spec.toml define [trial] keep_glyphs, se usa esa lista.
Los nombres son de source (Glyphs); se resuelven a nombres de producción del binario.

Uso:
  pathfinders-fix-trial-subset [--spec qa/project-spec.toml] build/otf/*.otf build/ttf/*.ttf
Solo procesa archivos cuyo nombre contiene "Trial"; el resto se ignora.
"""

import argparse
import sys
import tomllib
from pathlib import Path

from fontTools import subset
from fontTools.agl import toUnicode
from fontTools.ttLib import TTFont

CANONICAL_TRIAL_GLYPHS = (
    "A B C D E F G H I J K L M N O P Q R S T U V W X Y Z "
    "a b c d e f g h i j k l m n o p q r s t u v w x y z "
    "fi fl "
    "zero one two three four five six seven eight nine "
    "space nbspace period comma hyphen"
).split()

# Nombres de Glyphs cuyo codepoint no deduce el AGL.
GLYPHS_NAME_UNICODE = {
    "nbspace": 0x00A0,
    "softhyphen": 0x00AD,
    "fi": 0xFB01,
    "fl": 0xFB02,
}


def load_keep_glyphs(spec_path):
    if spec_path and Path(spec_path).exists():
        with open(spec_path, "rb") as f:
            trial = tomllib.load(f).get("trial", {})
        if trial.get("keep_glyphs"):
            return list(trial["keep_glyphs"]), f"{spec_path} [trial] keep_glyphs"
    return list(CANONICAL_TRIAL_GLYPHS), "set canónico Pathfinders"


def source_name_unicode(name):
    if name in GLYPHS_NAME_UNICODE:
        return GLYPHS_NAME_UNICODE[name]
    u = toUnicode(name)
    return ord(u) if len(u) == 1 else None


def resolve(font, names):
    """Nombre de source → nombre de glifo en el binario (production name)."""
    order = set(font.getGlyphOrder())
    cmap = font.getBestCmap()
    resolved, missing = [], []
    for name in names:
        if name in order:
            resolved.append(name)
            continue
        base, dot, suffix = name.partition(".")
        cp = source_name_unicode(base)
        binary_base = cmap.get(cp) if cp is not None else None
        candidate = f"{binary_base}{dot}{suffix}" if binary_base else None
        if candidate and candidate in order:
            resolved.append(candidate)
        else:
            missing.append(name)
    return resolved, missing


def subset_trial(path, keep):
    font = TTFont(path)
    include, missing = resolve(font, keep)
    if missing:
        raise ValueError(f"glifos del set no encontrados en el binario: {', '.join(missing)}")
    before = len(font.getGlyphOrder())

    opt = subset.Options()
    # mismas opciones que fontmake (subset_otf_from_ufo) salvo layout_closure
    opt.name_IDs = ["*"]
    opt.name_legacy = True
    opt.name_languages = ["*"]
    opt.layout_features = ["*"]
    opt.layout_closure = False
    opt.notdef_outline = True
    opt.recalc_bounds = True
    opt.recalc_timestamp = True
    opt.canonical_order = True
    opt.glyph_names = True
    opt.hinting = True
    opt.legacy_kern = True
    # xAvgCharWidth sobre los glifos reales del Trial, no los del font completo
    opt.recalc_average_width = True

    subsetter = subset.Subsetter(options=opt)
    subsetter.populate(glyphs=include)
    subsetter.subset(font)
    font.save(path)
    return before, len(font.getGlyphOrder())


def main():
    ap = argparse.ArgumentParser(description="Recorta las fuentes Trial al glyph set exacto")
    ap.add_argument("--spec", default="qa/project-spec.toml",
                    help="project-spec.toml del proyecto (default: qa/project-spec.toml)")
    ap.add_argument("fonts", nargs="+")
    args = ap.parse_args()

    keep, origin = load_keep_glyphs(args.spec)
    trials = [Path(p) for p in args.fonts if "Trial" in Path(p).name]
    print(f"Trial subset: {len(keep)} glifos ({origin}), {len(trials)} archivos")

    errors = 0
    for p in trials:
        try:
            before, after = subset_trial(str(p), keep)
            print(f"  {p.name}: {before} → {after}")
        except Exception as e:
            print(f"  ERROR  {p.name}: {e}")
            errors += 1
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
