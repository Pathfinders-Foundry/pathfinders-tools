#!/usr/bin/env python3
"""check_weight_class.py — Pathfinders Capa B: OS/2 usWeightClass

Verifica que el usWeightClass de cada fuente corresponda al nombre de su peso
y que el mismo peso tenga el mismo valor en todos los archivos: todos los
cuts, upright e italic, full y Trial.

fontspector (perfil universal) no lo comprueba en estáticos: su único check
de usWeightClass (`opentype/weight_class_fvar`) es para variables, y
`googlefonts/weightclass` está en el perfil de Google Fonts.

El peso se lee de nameID 17 (o nameID 2 si no hay 17), sin "Italic".

Valores por defecto (spec Pathfinders):
  Thin/Hairline 100 · Ultralight/Extralight 200 · Light 300 · Regular 400
  Medium 500 · Semibold 600 · Bold 700 · Extrabold 800 · Black/Heavy 900

Pesos no estándar (p. ej. Book) o valores distintos se declaran en el
project-spec.toml del proyecto:

  [weight_class]
  Book = 350

FAIL — valor distinto del esperado, peso sin valor definido, o el mismo peso
       con valores distintos entre archivos
Exit codes: 0 sin FAILs · 1 al menos un FAIL

Usage:
    python3 check_weight_class.py $(find build/otf -name "*.otf" | sort)
    python3 check_weight_class.py --spec qa/project-spec.toml build/otf/*.otf
"""

import sys
import tomllib
from collections import defaultdict
from pathlib import Path

from fontTools.ttLib import TTFont

DEFAULTS = {
    "Thin": 100, "Hairline": 100,
    "Ultralight": 200, "Extralight": 200,
    "Light": 300,
    "Regular": 400,
    "Medium": 500,
    "Semibold": 600, "Demibold": 600,
    "Bold": 700,
    "Extrabold": 800, "Ultrabold": 800,
    "Black": 900, "Heavy": 900,
}


def _weight_name(font: TTFont) -> str:
    name = font["name"]
    style = (name.getDebugName(17) or name.getDebugName(2) or "").strip()
    weight = " ".join(w for w in style.split() if w != "Italic")
    return weight or "Regular"


def _load_expected(spec: Path | None) -> dict[str, int]:
    expected = dict(DEFAULTS)
    if spec and spec.exists():
        with open(spec, "rb") as f:
            expected.update(tomllib.load(f).get("weight_class", {}))
    return expected


def main() -> None:
    args = sys.argv[1:]
    spec = None
    if "--spec" in args:
        i = args.index("--spec")
        spec = Path(args[i + 1])
        del args[i : i + 2]
    elif Path("qa/project-spec.toml").exists():
        spec = Path("qa/project-spec.toml")
    if not args:
        print("Usage: check_weight_class.py [--spec project-spec.toml] <font> [...]", file=sys.stderr)
        sys.exit(2)

    expected = _load_expected(spec)
    by_weight: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    fails = 0

    for path in args:
        font = TTFont(path, lazy=True)
        weight = _weight_name(font)
        value = font["OS/2"].usWeightClass
        font.close()
        fname = Path(path).name
        by_weight[weight][value].append(fname)
        want = expected.get(weight)
        if want is None:
            print(f"FAIL  {fname}: peso '{weight}' sin valor definido "
                  f"(añádelo a [weight_class] en project-spec.toml) — usWeightClass {value}")
            fails += 1
        elif value != want:
            print(f"FAIL  {fname}: '{weight}' usWeightClass {value}, esperado {want}")
            fails += 1

    for weight, values in sorted(by_weight.items(), key=lambda kv: min(kv[1])):
        if len(values) > 1:
            detail = "; ".join(f"{v}: {len(fs)} archivos (p. ej. {fs[0]})" for v, fs in sorted(values.items()))
            print(f"FAIL  '{weight}' con valores distintos entre archivos — {detail}")
            fails += 1
        else:
            (v, fs), = values.items()
            print(f"      '{weight}' = {v} en {len(fs)} archivos (coherente entre archivos)")

    print(f"\nResumen: {fails} FAIL")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
