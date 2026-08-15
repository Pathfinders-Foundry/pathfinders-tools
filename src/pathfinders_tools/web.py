import sys
import argparse
from pathlib import Path
from fontTools.ttLib import TTFont


def ttf_to_woff(ttf_path: Path, out_dir: Path) -> Path:
    out_path = out_dir / ttf_path.with_suffix('.woff').name
    font = TTFont(str(ttf_path))
    font.flavor = 'woff'
    font.save(str(out_path))
    return out_path


def ttf_to_woff2(ttf_path: Path, out_dir: Path) -> Path:
    from fontTools.ttLib.woff2 import compress
    out_path = out_dir / ttf_path.with_suffix('.woff2').name
    compress(str(ttf_path), str(out_path))
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Convierte TTF a WOFF y/o WOFF2")
    parser.add_argument('--input',  required=True, help="Directorio con archivos TTF")
    parser.add_argument('--woff',   help="Directorio de salida para WOFF")
    parser.add_argument('--woff2',  help="Directorio de salida para WOFF2")
    args = parser.parse_args()

    if not args.woff and not args.woff2:
        print("Error: especifica al menos --woff o --woff2")
        sys.exit(1)

    in_dir = Path(args.input)
    ttf_files = sorted(in_dir.glob('*.ttf'))

    if not ttf_files:
        print(f"No se encontraron archivos .ttf en {in_dir}")
        sys.exit(1)

    woff_dir  = Path(args.woff)  if args.woff  else None
    woff2_dir = Path(args.woff2) if args.woff2 else None

    if woff_dir:
        woff_dir.mkdir(parents=True, exist_ok=True)
    if woff2_dir:
        woff2_dir.mkdir(parents=True, exist_ok=True)

    errors = []
    for ttf in ttf_files:
        if woff_dir:
            try:
                out = ttf_to_woff(ttf, woff_dir)
                print(f"  WOFF   {ttf.name} → {out.name}")
            except Exception as e:
                print(f"  ERROR  {ttf.name} → WOFF: {e}")
                errors.append(ttf)
        if woff2_dir:
            try:
                out = ttf_to_woff2(ttf, woff2_dir)
                print(f"  WOFF2  {ttf.name} → {out.name}")
            except Exception as e:
                print(f"  ERROR  {ttf.name} → WOFF2: {e}")
                errors.append(ttf)

    print(f"\n{len(ttf_files)} TTF procesados — {len(errors)} error(es)")
    if errors:
        sys.exit(1)
