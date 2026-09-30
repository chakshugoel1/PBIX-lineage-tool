"""Render the architecture DOT sources to SVG and PNG with Graphviz."""
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parent
DOT_FILES = sorted(ROOT.glob("*.dot"))


def main():
    dot = shutil.which("dot")
    if not dot:
        raise SystemExit("Graphviz 'dot' was not found on PATH.")
    for source in DOT_FILES:
        for fmt in ("svg", "png"):
            output = source.with_suffix(f".{fmt}")
            subprocess.run([dot, f"-T{fmt}", str(source), "-o", str(output)], check=True)
            print(f"Rendered {output.name}")


if __name__ == "__main__":
    main()