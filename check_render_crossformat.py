#!/usr/bin/env python3
"""check_render_crossformat.py — Pathfinders Release QA: render cross-format

Envoltorio para lanzar desde los skills de QA con python3 sin instalar el paquete.
La implementación y la documentación (tolerancias, modos, exit codes) están en
src/pathfinders_tools/render.py, instalada como `pathfinders-check-render`.

Usage:
    python3 check_render_crossformat.py build/otf build/ttf build/woff build/woff2
    python3 check_render_crossformat.py --previous ../prev/fonts --republish fonts/
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from pathfinders_tools.render import main  # noqa: E402

if __name__ == "__main__":
    main()
