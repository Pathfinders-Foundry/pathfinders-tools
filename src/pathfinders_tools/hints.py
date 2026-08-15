import sys
import os
import tempfile
import shutil

try:
    import ttfautohint
except ImportError:
    print("ERROR: ttfautohint-py no está instalado.")
    sys.exit(1)


def apply_hints(font_path: str) -> str:
    if not font_path.lower().endswith('.ttf'):
        return 'skip'
    fd, tmp_path = tempfile.mkstemp(suffix='.ttf')
    os.close(fd)
    try:
        ttfautohint.ttfautohint(in_file=font_path, out_file=tmp_path)
        shutil.move(tmp_path, font_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    return 'ok'


def main():
    if len(sys.argv) < 2:
        print("Uso: pathfinders-fix-hints archivo.ttf [archivo2.ttf ...]")
        sys.exit(1)

    paths = sys.argv[1:]
    errors = []

    for path in paths:
        if not os.path.isfile(path):
            print(f"  SKIP  {os.path.basename(path)} (no existe)")
            continue
        try:
            state = apply_hints(path)
            name = os.path.basename(path)
            print(f"  {'OK' if state == 'ok' else 'SKIP'}    {name}")
        except Exception as e:
            print(f"  ERROR {path}: {e}")
            errors.append(path)

    if errors:
        print(f"\n{len(errors)} error(es). Ver arriba.")
        sys.exit(1)
