"""
Post-procesado TTF después de ttfautohint.

  A. head.flags bit 3 (integer_ppem_if_hinted)
  B. OS/2.usWinDescent >= abs(yMin)

Debe ejecutarse DESPUÉS de pathfinders-fix-hints.
"""

import sys
import os
from fontTools.ttLib import TTFont


def fix_a_head_flags(font: TTFont) -> int:
    head = font['head']
    if head.flags & 0x0008:
        return 0
    head.flags |= 0x0008
    return 1


def fix_b_win_descent(font: TTFont) -> int:
    y_min_abs = abs(font['head'].yMin)
    os2 = font['OS/2']
    if y_min_abs > os2.usWinDescent:
        os2.usWinDescent = y_min_abs
        return 1
    return 0


def process(font_path: str) -> dict:
    font = TTFont(font_path)
    a = fix_a_head_flags(font)
    b = fix_b_win_descent(font)
    if a + b > 0:
        font.save(font_path)
    return {'a': a, 'b': b}


def main():
    if len(sys.argv) < 2:
        print("Uso: pathfinders-fix-postprocess archivo.ttf [archivo2.ttf ...]")
        sys.exit(1)

    paths = sys.argv[1:]
    errors = []
    counts = {'a': 0, 'b': 0}

    for path in paths:
        if not os.path.isfile(path):
            print(f"  SKIP  {os.path.basename(path)} (no existe)")
            continue
        try:
            result = process(path)
            name = os.path.basename(path)
            tags = []
            if result['a']:
                tags.append('head.flags')
                counts['a'] += 1
            if result['b']:
                tags.append('winDescent')
                counts['b'] += 1
            print(f"  {'OK' if tags else '--'}    {name}: {', '.join(tags) or 'sin cambios'}")
        except Exception as e:
            print(f"  ERROR {path}: {e}")
            errors.append(path)

    print(f"\n  Resumen: head.flags={counts['a']}  winDescent={counts['b']}")

    if errors:
        print(f"  {len(errors)} error(es). Ver arriba.")
        sys.exit(1)
