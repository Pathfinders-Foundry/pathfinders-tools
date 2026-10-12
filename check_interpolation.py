#!/usr/bin/env python3
"""check_interpolation.py — Pathfinders preflight: compatibilidad de masters

Lista TODOS los glifos exportables que no se pueden interpolar, de una vez,
antes de compilar. fontmake se para en el primer glifo incompatible; esto
evita ir error a error por el CI.

Corre sobre uno o varios .designspace (los que genera el build del proyecto:
`make designspaces`, o `glyphs2ufo source/X.glyphspackage -m build/master_ufo`).
Usa `fontTools.varLib.interpolatable` y clasifica sus problemas:

  FAIL — rompe el build: path_count, node_count, node_incompatibility,
         open_path, missing (glifo ausente en un master no sparse)
  WARN — interpola pero puede salir mal: contour_order, wrong_start_point,
         underweight, overweight, kink

Los glifos con export = 0 (public.skipExportGlyphs) se ignoran.

Exit codes:
  0 — sin FAILs
  1 — al menos un FAIL

Usage:
    python3 check_interpolation.py build/designspace/*.designspace
    python3 check_interpolation.py --no-warn build/master_ufo/Familia.designspace
"""

import io
import json
import sys
from collections import defaultdict
from contextlib import redirect_stdout
from pathlib import Path

from fontTools.designspaceLib import DesignSpaceDocument
from fontTools.varLib import interpolatable

HARD = {"path_count", "node_count", "node_incompatibility", "open_path", "missing"}


def _skip_export(ds_path: str) -> set[str]:
    """Glifos no exportables: lib del designspace + lib de cada UFO master."""
    doc = DesignSpaceDocument.fromfile(ds_path)
    skip = set(doc.lib.get("public.skipExportGlyphs", []))
    import plistlib

    for src in doc.sources:
        lib = Path(src.path or "") / "lib.plist"
        if lib.exists():
            with open(lib, "rb") as f:
                skip |= set(plistlib.load(f).get("public.skipExportGlyphs", []))
    return skip


def _problems(ds_path: str) -> dict:
    buf = io.StringIO()
    with redirect_stdout(buf):
        interpolatable.main([ds_path, "--json", "--ignore-missing"])
    out = buf.getvalue().strip()
    return json.loads(out) if out else {}


def _short(master: str) -> str:
    return master.split("-", 2)[-1] if master.count("-") >= 2 else master


def main() -> None:
    args = sys.argv[1:]
    show_warn = "--no-warn" not in args
    paths = [a for a in args if not a.startswith("--")]
    if not paths:
        print("Usage: check_interpolation.py [--no-warn] <file.designspace> [...]", file=sys.stderr)
        sys.exit(2)

    fail_total = warn_total = 0
    for ds in paths:
        skip = _skip_export(ds)
        problems = _problems(ds)
        fails: dict[str, list] = defaultdict(list)
        warns: dict[str, list] = defaultdict(list)
        ignored = 0
        for glyph, plist in sorted(problems.items()):
            if glyph in skip:
                ignored += 1
                continue
            for p in plist:
                (fails if p["type"] in HARD else warns)[glyph].append(p)

        fail_total += len(fails)
        warn_total += len(warns)
        name = Path(ds).name
        status = "FAIL" if fails else "OK  "
        print(f"{status}  [{name}] {len(fails)} glifos incompatibles, "
              f"{len(warns)} con avisos, {ignored} no exportables ignorados")
        for glyph, plist in fails.items():
            for p in plist:
                where = f" contorno {p['path']}" if "path" in p else ""
                vals = f" ({p['value_1']} vs {p['value_2']})" if "value_1" in p else ""
                print(f"      FAIL {glyph}: {p['type']}{where}{vals} — "
                      f"{_short(p.get('master_1', '?'))} / {_short(p.get('master_2', '?'))}")
        if show_warn:
            for glyph, plist in warns.items():
                types = sorted({p["type"] for p in plist})
                print(f"      WARN {glyph}: {', '.join(types)}")

    print(f"\nResumen: {fail_total} glifos FAIL  {warn_total} glifos WARN")
    sys.exit(1 if fail_total else 0)


if __name__ == "__main__":
    main()
