"""Build a standalone LabelPrinter.exe with PyInstaller - no Python needed on
the target machine. Run this from inside the project folder:  python build_exe.py
"""
import subprocess
import sys

subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pyinstaller"], check=True)

# warranty_page.html is loaded at runtime by local_server.py — must ship inside
# the onefile bundle (Windows separator for --add-data is ';').
#
# Excludes below: none of this app's own code imports pandas/scipy/numba/
# llvmlite - they were getting swept into the onefile bundle anyway (PyInstaller's
# static analysis over-including something's optional/transitive dependency
# chain in this shared environment), ballooning the exe from ~60MB to ~145MB
# for dead weight. Verified nothing in this project imports them before adding
# these - if a future feature genuinely needs one, remove its line here.
subprocess.run([
    sys.executable, "-m", "PyInstaller",
    "--onefile",
    "--windowed",
    "--name", "LabelPrinter",
    "--noconfirm",
    "--add-data", "warranty_page.html;.",
    "--exclude-module", "pandas",
    "--exclude-module", "scipy",
    "--exclude-module", "numba",
    "--exclude-module", "llvmlite",
    "run.py",
], check=True)

print("\nBuilt: dist\\LabelPrinter.exe")
