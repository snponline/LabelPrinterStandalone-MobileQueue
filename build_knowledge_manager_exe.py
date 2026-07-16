"""Build a standalone KnowledgeManager.exe - separate from LabelPrinter.exe
on purpose, so this simple tool can be handed to staff without exposing the
rest of the label-printing program. Run from inside this folder:
    python build_knowledge_manager_exe.py
"""
import subprocess
import sys

subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pyinstaller"], check=True)

subprocess.run([
    sys.executable, "-m", "PyInstaller",
    "--onefile",
    "--windowed",
    "--name", "KnowledgeManager",
    "--noconfirm",
    "knowledge_manager.py",
], check=True)

print("\nBuilt: dist\\KnowledgeManager.exe")
