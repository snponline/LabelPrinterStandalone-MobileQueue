"""Build a standalone LabelPrinter.exe with PyInstaller - no Python needed on
the target machine. Run this from inside the project folder:  python build_exe.py
"""
import subprocess
import sys

subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pyinstaller"], check=True)

# warranty_page.html is loaded at runtime by local_server.py — must ship inside
# the onefile bundle (Windows separator for --add-data is ';').
subprocess.run([
    sys.executable, "-m", "PyInstaller",
    "--onefile",
    "--windowed",
    "--name", "LabelPrinter",
    "--noconfirm",
    "--add-data", "warranty_page.html;.",
    "run.py",
], check=True)

print("\nBuilt: dist\\LabelPrinter.exe")
