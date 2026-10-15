"""
pathfinders-check-win-descent — OS/2.usWinDescent >= abs(head.yMin)

Sustituye a pathfinders-fix-postprocess (0.2.0), que subía usWinDescent en
silencio. Corregirlo en el binario escondía el problema y arrastró valores
falsos (Tiphares 1.000: winDescent calculado sobre contornos TTF rotos). La
métrica se fija en el source; este check solo comprueba y falla si no cubre
el glifo más bajo (Windows recorta lo que baja de usWinDescent).

Uso:
  pathfinders-check-win-descent build/otf/*.otf build/ttf/*.ttf
"""

import sys
import os
from fontTools.ttLib import TTFont


def check(font_path: str):
    font = TTFont(font_path, lazy=True)
    y_min_abs = abs(font['head'].yMin)
    win_descent = font['OS/2'].usWinDescent
    return win_descent >= y_min_abs, win_descent, y_min_abs


def main():
    if len(sys.argv) < 2:
        print("Uso: pathfinders-check-win-descent archivo.otf|ttf [...]")
        sys.exit(1)

    fails = 0
    for path in sys.argv[1:]:
        if not os.path.isfile(path):
            print(f"  SKIP  {os.path.basename(path)} (no existe)")
            continue
        ok, win_descent, y_min_abs = check(path)
        name = os.path.basename(path)
        if ok:
            print(f"  OK    {name}: usWinDescent {win_descent} >= |yMin| {y_min_abs}")
        else:
            print(f"  FAIL  {name}: usWinDescent {win_descent} < |yMin| {y_min_abs} — corregir en el source")
            fails += 1

    if fails:
        print(f"\n{fails} archivo(s) con usWinDescent insuficiente.")
        sys.exit(1)
