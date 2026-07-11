"""Build a standalone LabelPrinter.exe with PyInstaller - no Python needed on
the target machine. Run this from inside the project folder:  python build_exe.py
"""
import subprocess
import sys

subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pyinstaller"], check=True)

subprocess.run([
    sys.executable, "-m", "PyInstaller",
    "--onefile",
    "--windowed",
    "--name", "LabelPrinter",
    "--noconfirm",
    "run.py",
], check=True)

print("\nBuilt: dist\\LabelPrinter.exe")
