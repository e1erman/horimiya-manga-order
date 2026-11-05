"""Build HorimiyaMangaOrder.exe and the release zip into dist/.

    pip install pyinstaller
    python tools/build_exe.py
"""
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from horimiya_order import __version__  # noqa: E402

DIST = ROOT / "dist"
SEP = ";" if sys.platform == "win32" else ":"


def main() -> None:
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "HorimiyaMangaOrder",
        "--paths", str(ROOT),
        "--add-data", f"{ROOT / 'order.json'}{SEP}.",
        "--add-data", f"{ROOT / 'horimiya_gui.pyw'}{SEP}.",
        "--distpath", str(DIST), "--workpath", str(ROOT / "build"), "--specpath", str(ROOT / "build"),
        str(ROOT / "tools" / "exe_entry.py"),
    ], check=True)

    zip_path = DIST / f"horimiya-manga-order-v{__version__}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("horimiya_order.py", "horimiya_gui.pyw", "order.json", "README.md", "LICENSE"):
            z.write(ROOT / name, f"horimiya-manga-order/{name}")
    print(f"\nBuilt {DIST / 'HorimiyaMangaOrder.exe'}\n      {zip_path}")


if __name__ == "__main__":
    main()
