#!/usr/bin/env python3
"""gen_fontspector_config.py — Pathfinders: configuración por archivo de los checks
de fontspector que solo funcionan configurados.

fontspector trae seis checks que se saltan (SKIP) si no se configuran:
opentype/vendor_id, universal/required_name_ids, field_values, has_features,
has_glyphs y has_unicodes. Su configuración es global o por archivo, pero no
combina las dos, y en Pathfinders hay valores que cambian por archivo
(usWinDescent upright/italic, usWeightClass por peso, glyph set de los Trial).
Este script la genera por archivo a partir de qa/project-spec.toml:

  [fontspector]
  vendor_id = "PTHF"
  required_name_ids = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14]
  trial_features = ["kern", "liga"]          # has_features de los Trial
  [fontspector.field_values]                 # todos los archivos
  "hhea.ascender" = 950
  [fontspector.field_values_italic]          # solo italics (sobrescribe)
  "OS/2.usWinDescent" = 500

Además usa, del mismo project-spec:
  - usWeightClass por peso: tabla Pathfinders + [weight_class]
  - has_features de los full: [features] required
  - has_glyphs: [trial] keep_glyphs (nombres de producción) en todos los archivos
  - has_unicodes: full → Set [charset] max_set de qa/check_charset.py menos
    excluded_codepoints; Trial → codepoints de keep_glyphs

Usage:
    python3 gen_fontspector_config.py --out build/qa/fontspector-config.toml \\
        $(find build/otf -name "*.otf" | sort)
    fontspector --profile universal --configuration build/qa/fontspector-config.toml \\
        $(find build/otf -name "*.otf" | sort)
"""

import importlib.util
import json
import sys
import tomllib
from pathlib import Path

from fontTools.ttLib import TTFont

CHECKS = [
    "opentype/vendor_id",
    "universal/required_name_ids",
    "field_values",
    "has_features",
    "has_glyphs",
    "has_unicodes",
]

WEIGHT_DEFAULTS = {
    "Thin": 100, "Hairline": 100, "Ultralight": 200, "Extralight": 200,
    "Light": 300, "Regular": 400, "Medium": 500, "Semibold": 600,
    "Demibold": 600, "Bold": 700, "Extrabold": 800, "Ultrabold": 800,
    "Black": 900, "Heavy": 900,
}


def _weight_name(font):
    name = font["name"]
    style = (name.getDebugName(17) or name.getDebugName(2) or "").strip()
    return " ".join(w for w in style.split() if w != "Italic") or "Regular"


def _is_italic(font):
    return bool(font["OS/2"].fsSelection & 1 or font["head"].macStyle & 2)


def _is_trial(font):
    name = font["name"]
    return "Trial" in (name.getDebugName(16) or name.getDebugName(1) or "")


def _charset(path: Path, max_set: int) -> set[int]:
    spec = importlib.util.spec_from_file_location("check_charset", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return set(mod.SET_REQUIRED[max_set])


def _trial_glyphs(names):
    try:
        from glyphsLib.glyphdata import get_glyph
    except ImportError:
        return list(names), set()
    prod, unis = [], set()
    for n in names:
        g = get_glyph(n)
        prod.append(g.production_name or n)
        if g.unicode:
            unis.add(int(g.unicode, 16))
    return prod, unis


def _toml_value(v):
    return json.dumps(v, ensure_ascii=False)


def main() -> None:
    args = sys.argv[1:]
    opts = {"--spec": "qa/project-spec.toml", "--charset": "qa/check_charset.py", "--out": None}
    for k in list(opts):
        if k in args:
            i = args.index(k)
            opts[k] = args[i + 1]
            del args[i : i + 2]
    if not args or not opts["--out"]:
        print("Usage: gen_fontspector_config.py --out <file.toml> [--spec ...] [--charset ...] <fonts>",
              file=sys.stderr)
        sys.exit(2)

    with open(opts["--spec"], "rb") as f:
        spec = tomllib.load(f)
    fs = spec.get("fontspector", {})
    weights = dict(WEIGHT_DEFAULTS, **spec.get("weight_class", {}))
    full_features = spec.get("features", {}).get("required", [])
    trial_features = fs.get("trial_features", ["kern"])
    keep = spec.get("trial", {}).get("keep_glyphs", [])
    keep_prod, keep_unis = _trial_glyphs(keep)
    excluded = {int(c.replace("U+", ""), 16) for c in spec.get("charset", {}).get("excluded_codepoints", [])}
    max_set = spec.get("charset", {}).get("max_set")
    full_unis = (_charset(Path(opts["--charset"]), max_set) - excluded) if max_set else set()

    out = [
        "# GENERADO por gen_fontspector_config.py desde qa/project-spec.toml — no editar a mano",
        f"explicit_checks = {_toml_value(CHECKS)}",
        "",
        '["opentype/vendor_id"]',
        f"vendor_id = {_toml_value(fs['vendor_id'])}",
        "",
        '["universal/required_name_ids"]',
        f"required_name_ids = {_toml_value(fs['required_name_ids'])}",
        "",
    ]
    feats, glyphs, unis, fields = [], [], [], []
    for path in args:
        font = TTFont(path, lazy=True)
        fname = Path(path).name
        trial, italic = _is_trial(font), _is_italic(font)
        values = dict(fs.get("field_values", {}))
        if italic:
            values.update(fs.get("field_values_italic", {}))
        w = weights.get(_weight_name(font))
        if w is not None:
            values["OS/2.usWeightClass"] = w
        font.close()

        feats.append(f'"{fname}" = {_toml_value(trial_features if trial else full_features)}')
        if keep_prod:
            glyphs.append(f'"{fname}" = {_toml_value(keep_prod)}')
        u = sorted(keep_unis if trial else full_unis)
        if u:
            unis.append(f'"{fname}" = [{", ".join(hex(c) for c in u)}]')
        fields.append(f'[field_values."{fname}"]')
        fields += [f'"{k}" = {_toml_value(v)}' for k, v in values.items()]
        fields.append("")

    out += ["[has_features]", *feats, ""]
    if glyphs:
        out += ["[has_glyphs]", *glyphs, ""]
    if unis:
        out += ["[has_unicodes]", *unis, ""]
    out += fields

    Path(opts["--out"]).parent.mkdir(parents=True, exist_ok=True)
    Path(opts["--out"]).write_text("\n".join(out))
    print(f"{opts['--out']}: {len(args)} archivos")


if __name__ == "__main__":
    main()
