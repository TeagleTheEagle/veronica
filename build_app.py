"""Build script to package VERONICA into a standalone Windows executable.

This script uses PyInstaller to bundle the Python environment and all 
necessary assets (models, UI, data) into a single .exe file.
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path

# --- Configuration ---
APP_NAME = "VERONICA"
ENTRY_POINT = "veronica.py"
ICON_PATH = Path("launcher/veronica.ico")
DIST_DIR = Path(__file__).parent / "dist"
BUILD_DIR = Path(__file__).parent / "build"

# Assets that must be bundled inside the EXE
# Format: (source_path, destination_path_inside_exe)
ASSETS = [
    ("ui", "ui"),
    ("models", "models"),
    ("data", "data"),
    (".env", "."),
]

def check_pyinstaller():
    """Ensure PyInstaller is installed in the current environment."""
    try:
        import PyInstaller
    except ImportError:
        print("PyInstaller not found. Installing...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)

def get_collect_data():
    """Build the --add-data arguments for PyInstaller."""
    data_args = []
    for src, dest in ASSETS:
        src_path = Path(src)
        if src_path.exists():
            # PyInstaller format: "source;dest" on Windows
            data_args.extend(["--add-data", f"{src};{dest}"])
        else:
            print(f"Warning: Asset {src} not found, skipping.")
    return data_args

def build():
    """Execute the PyInstaller build process."""
    check_pyinstaller()
    
    # Clear previous builds
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)

    # Find the pyinstaller executable path
    import PyInstaller
    pyinstaller_path = os.path.dirname(PyInstaller.__file__)
    # Usually pyinstaller is a module, but the CLI is a separate script in the Scripts folder
    # We will try to find it via sys.executable -m PyInstaller
    
    # Construct the PyInstaller command using 'python -m PyInstaller' for maximum compatibility
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",                # Bundle into a directory for maximum stability
        # "--windowed",               # DISABLED for debugging to see console errors
        "--name", APP_NAME,         # Name of the resulting EXE
        "--clean",                  # Clean PyInstaller cache
    ]

    # Explicitly include hidden imports that PyInstaller might miss
    hidden_imports = ["agent", "safety", "gmail", "calendar_feed", "subagents", "middleware", "context_state"]
    for imp in hidden_imports:
        cmd.extend(["--hidden-import", imp])

    # Add icon if it exists
    if ICON_PATH.exists():
        cmd.extend(["--icon", str(ICON_PATH)])
    
    # Add assets
    # Ensure we use absolute paths for PyInstaller to find the assets correctly
    base_path = Path(__file__).parent.absolute()
    data_args = []
    for src, dest in ASSETS:
        src_path = base_path / src
        if src_path.exists():
            data_args.extend(["--add-data", f"{src_path};{dest}"])
        else:
            print(f"Warning: Asset {src_path} not found, skipping.")
    cmd.extend(data_args)
    
    # Entry point - Use absolute path
    cmd.append(str(base_path / ENTRY_POINT))

    print(f"Building {APP_NAME} executable...")
    try:
        subprocess.run(cmd, check=True)
        print(f"\nBuild successful! Your app is located at: {DIST_DIR / f'{APP_NAME}.exe'}")
    except subprocess.CalledProcessError as e:
        print(f"\nBuild failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    build()
