"""
Black Box Build v2 — compiles strategy package to .so binaries.
Output: engine/ package with .so files + plain support files.

Client flow: clone repo → edit configs → run. No source readable.
"""
import shutil
import sys
from pathlib import Path

from setuptools import setup
from Cython.Build import cythonize

ROOT = Path(__file__).resolve().parent
SRC_DIR = ROOT / "src_lib"      # Copy your lib/*.py files here
PKG_DIR = ROOT / "engine"       # Output package
DIST = ROOT / "dist"

# Clean
for d in [PKG_DIR, DIST]:
    if d.exists():
        shutil.rmtree(d)
PKG_DIR.mkdir(parents=True)
DIST.mkdir()

# Copy source files into engine/ package
if not SRC_DIR.exists() or not list(SRC_DIR.glob("*.py")):
    print("ERROR: Put your .py lib files in src_lib/ first.")
    sys.exit(1)

for f in sorted(SRC_DIR.glob("*.py")):
    shutil.copy(f, PKG_DIR / f.name)

# Create __init__.py (empty, just makes it a package)
(PKG_DIR / "__init__.py").write_text("# Black-box strategy engine\n")

print(f"Compiling {len(list(PKG_DIR.glob('*.py')))} files...")

setup(
    ext_modules=cythonize(
        [str(p) for p in PKG_DIR.glob("*.py") if p.name != "__init__.py"],
        compiler_directives={
            "language_level": "3",
            "boundscheck": False,
            "wraparound": False,
        },
    ),
    script_args=["build_ext", "--inplace"],
)

# Move .so files to DIST/ and clean up .c files
for f in ROOT.glob("*.so"):
    shutil.move(str(f), str(DIST / f.name))
for f in PKG_DIR.glob("*.c"):
    f.unlink()  # Remove intermediate C files (don't ship those)

# Copy __init__.py to dist
shutil.copy(PKG_DIR / "__init__.py", DIST / "__init__.py")

# Also copy any plain .py support files that weren't compiled
# (e.g., events.py — small utility, no strategy logic to hide)
KEEP_PLAIN = ["__init__.py"]  # add filenames here to keep as readable .py
for name in KEEP_PLAIN:
    src = PKG_DIR / name
    if src.exists() and not (DIST / name).exists():
        shutil.copy(src, DIST / name)

print(f"\nDone. Ship the {DIST.name}/ folder to client.")
print(f"Client imports: from engine import pr_engine, analyze_broker")
