#!/usr/bin/env python3
"""
check_foundry_spec.py — Verifica OTFs/TTFs compilados contra la Pathfinders Foundry Spec.

Severidades:
  FAIL  — metadata incorrecta (nameIDs). Bloquea release. Daisuke corrige en source.
  WARN  — cobertura de caracteres incompleta. Informativo. Octavio decide.

Uso:
    python3 check_foundry_spec.py font1.otf font2.otf ...
    python3 check_foundry_spec.py --spec /ruta/a/spec.toml font1.otf ...
"""

import argparse
import re
import sys
import tomllib
from pathlib import Path

from fontTools.ttLib import TTFont

SPEC_DEFAULT = Path(__file__).parent / "pathfinders-foundry-spec.toml"

NAMEID_LABEL = {
    0:  "Copyright (nameID 0)",
    7:  "Trademark (nameID 7)",
    8:  "Manufacturer (nameID 8)",
    9:  "Designer (nameID 9)",
    11: "Vendor URL (nameID 11)",
    13: "License Description (nameID 13)",
    14: "License URL (nameID 14)",
}

FIELD_TO_NAMEID = {
    "manufacturer":       8,
    "designer":           9,
    "vendor_url":         11,
    "license_description": 13,
    "license_url":        14,
}


def get_name(font, name_id):
    table = font["name"]
    record = table.getName(name_id, 3, 1) or table.getName(name_id, 1, 0)
    return record.toUnicode() if record else None


def is_trial(font):
    ps = get_name(font, 6) or ""
    fam = get_name(font, 1) or ""
    return "Trial" in ps or "Trial" in fam


def check_font(path, spec):
    fails = []
    warns = []

    font = TTFont(path)
    trial = is_trial(font)
    nameids = spec["nameids"]
    rules = spec["rules"]

    # ── METADATA — FAIL ───────────────────────────────────────────────────────

    empty_nids = set()
    for nid in rules["required_nameids"]:
        val = get_name(font, nid)
        if not val or not val.strip():
            empty_nids.add(nid)
            fails.append(f"{NAMEID_LABEL.get(nid, f'nameID {nid}')} ausente o vacío")

    copyright_val = get_name(font, 0) or ""
    pattern = nameids.get("copyright_pattern", "")
    if 0 not in empty_nids and pattern and not re.match(pattern, copyright_val):
        fails.append(
            f"Copyright (nameID 0) no cumple el patrón canónico\n"
            f"    Esperado:   {nameids.get('copyright_template', pattern)}\n"
            f"    Encontrado: {copyright_val!r}"
        )

    trademark_val = (get_name(font, 7) or "").strip()
    tm_pattern = nameids.get("trademark_pattern", "")
    if 7 not in empty_nids and tm_pattern:
        m = re.match(tm_pattern, trademark_val)
        family = (get_name(font, 16) or get_name(font, 1) or "").strip()
        if not m or not family.startswith(m.group(1)):
            fails.append(
                f"Trademark (nameID 7) no cumple el patrón canónico\n"
                f"    Esperado:   {nameids.get('trademark_template', tm_pattern)} "
                f"({{family}} = comienzo de {family!r})\n"
                f"    Encontrado: {trademark_val!r}"
            )

    for field in rules["exact_match"]:
        nid = FIELD_TO_NAMEID.get(field)
        if nid is None or nid in empty_nids:
            continue
        expected = nameids.get(field, "")
        actual = (get_name(font, nid) or "").strip()
        if actual != expected.strip():
            fails.append(
                f"{NAMEID_LABEL.get(nid, field)} incorrecto\n"
                f"    Esperado:   {expected!r}\n"
                f"    Encontrado: {actual!r}"
            )

    # ── COVERAGE — WARN ───────────────────────────────────────────────────────

    cmap = set(font.getBestCmap().keys()) if font.getBestCmap() else set()

    if trial:
        required = set(spec["trial"]["glyph_set_unicodes"])
        label = "Trial glyph set"
    else:
        required = set(spec["coverage"]["latin_required"])
        label = "Latin coverage"

    missing = sorted(required - cmap)
    if missing:
        missing_fmt = ", ".join(f"U+{cp:04X}" for cp in missing)
        warns.append(f"{label}: {len(missing)} codepoint(s) faltante(s): {missing_fmt}")

    return trial, fails, warns


def main():
    parser = argparse.ArgumentParser(
        description="Verifica OTFs/TTFs contra la Pathfinders Foundry Spec."
    )
    parser.add_argument("fonts", nargs="+", metavar="FONT")
    parser.add_argument("--spec", default=str(SPEC_DEFAULT), metavar="TOML")
    args = parser.parse_args()

    spec_path = Path(args.spec)
    if not spec_path.exists():
        print(f"ERROR: spec no encontrada: {spec_path}", file=sys.stderr)
        sys.exit(2)

    with open(spec_path, "rb") as f:
        spec = tomllib.load(f)

    any_fail = False

    for font_path in args.fonts:
        path = Path(font_path)

        try:
            trial, fails, warns = check_font(path, spec)
        except Exception as e:
            print(f"\n── {path.name} ──")
            print(f"  ERROR al leer el archivo: {e}")
            any_fail = True
            continue

        tag = " [Trial]" if trial else ""
        print(f"\n── {path.name}{tag} ──")

        if not fails and not warns:
            print("  OK")
            continue

        for msg in fails:
            any_fail = True
            lines = msg.splitlines()
            print(f"  FAIL  {lines[0]}")
            for line in lines[1:]:
                print(f"        {line}")

        for msg in warns:
            lines = msg.splitlines()
            print(f"  WARN  {lines[0]}")
            for line in lines[1:]:
                print(f"        {line}")

    print()
    if any_fail:
        print("RESULTADO: FAIL — corregir metadata antes del release.")
        sys.exit(1)
    else:
        print("RESULTADO: OK (warnings son informativos, no bloquean).")
        sys.exit(0)


if __name__ == "__main__":
    main()
