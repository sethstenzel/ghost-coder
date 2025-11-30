import os
import sys
import argparse

# For folder selection dialog
import tkinter as tk
from tkinter import filedialog, messagebox

# File extensions to check
TEXT_EXTENSIONS = [
    # Python
    ".py", ".pyi", ".pyw", ".pyx", ".pxd", ".pxi",

    # Python packaging / metadata
    ".txt", ".md", ".rst", ".cfg", ".ini", ".toml",
    ".yaml", ".yml", ".json", ".csv", ".tsv",

    # Web / UI / JS / NiceGUI-related
    ".html", ".htm", ".css",
    ".js", ".mjs", ".cjs",
    ".ts", ".tsx", ".jsx",

    # Templates
    ".jinja", ".j2", ".tmpl", ".template",
    ".vue", ".svelte", ".xml", ".svg",

    # Shell / scripting / configs
    ".env", ".sh", ".bash", ".bsh", ".zsh",
    ".bat", ".cmd",
    ".ps1", ".psm1", ".psd1",
    ".reg",

    # C-family source code
    ".c", ".h", ".cpp", ".hpp", ".cc", ".hh",
    ".cxx", ".hxx",

    # Other languages (text-based)
    ".java", ".kt", ".go", ".rs", ".swift",
    ".php", ".rb", ".pl", ".lua", ".dart",

    # Build and config files
    ".gradle", ".properties",
    ".makefile", ".mk",
    ".manifest",
    ".dockerfile",
    ".service",
]


def is_text_file(path: str) -> bool:
    """Return True if the file should be checked based on extension."""
    _, ext = os.path.splitext(path)
    return ext.lower() in TEXT_EXTENSIONS


def check_utf8(path: str) -> bool:
    """Check if the file is valid UTF-8 encoded."""
    try:
        with open(path, "rb") as f:
            data = f.read()
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def convert_to_utf8(path: str, backup: bool = True) -> None:
    """
    Convert file to UTF-8 encoding.

    Tries to detect encoding using chardet; falls back to latin-1.
    Creates .backup if backup=True.
    """
    with open(path, "rb") as f:
        data = f.read()

    try:
        import chardet
        detected = chardet.detect(data)
        encoding = detected.get("encoding") or "latin-1"
    except ImportError:
        # Fallback if chardet is not installed
        encoding = "latin-1"

    if backup:
        backup_path = path + ".backup"
        # Avoid overwriting an existing backup
        if not os.path.exists(backup_path):
            os.rename(path, backup_path)
        else:
            # If backup exists, append a number
            n = 1
            while True:
                candidate = f"{path}.backup{n}"
                if not os.path.exists(candidate):
                    os.rename(path, candidate)
                    break
                n += 1

    text = data.decode(encoding, errors="replace")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def scan_directory(root: str, fix: bool = False):
    non_utf8_files = []

    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            fullpath = os.path.join(dirpath, filename)
            if '.' + str(fullpath.split('.')[-1]) in TEXT_EXTENSIONS:
                if is_text_file(fullpath):
                    if not check_utf8(fullpath):
                        non_utf8_files.append(fullpath)
                        if fix:
                            convert_to_utf8(fullpath)

    return non_utf8_files


def select_directory_dialog(title: str = "Select folder to scan") -> str | None:
    """Open a folder selection dialog and return the selected path or None."""
    root = tk.Tk()
    root.withdraw()  # Hide the main window

    # Optional: make dialog appear in front
    root.update_idletasks()
    root.attributes("-topmost", True)

    folder = filedialog.askdirectory(title=title)
    root.destroy()

    if not folder:
        return None
    return folder


def main():
    parser = argparse.ArgumentParser(
        description="Check for non-UTF-8 source files in a directory."
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Directory to scan (if omitted, a folder selection dialog will appear)"
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Automatically convert non-UTF-8 files to UTF-8 (creates .backup)"
    )
    args = parser.parse_args()

    # If no path is provided, use a folder selection dialog
    root_path = args.path
    if root_path is None:
        root_path = select_directory_dialog("Select the folder to scan for UTF-8")

        if root_path is None or root_path.strip() == "":
            print("No folder selected. Exiting.")
            sys.exit(1)

    if not os.path.isdir(root_path):
        print(f"Error: '{root_path}' is not a directory.")
        sys.exit(1)

    print(f"Scanning for non-UTF-8 source files in:\n  {root_path}\n")

    bad_files = scan_directory(root_path, fix=args.fix)

    if bad_files:
        print("❌ Found NON-UTF-8 files:")
        for f in bad_files:
            print("   -", f)

        if args.fix:
            print("\n✔ All above files have been converted to UTF-8.")
            print("  Backup copies were created with .backup extensions.")
        else:
            print("\nRun again with --fix to convert them automatically.\n")
        sys.exit(1)
    else:
        print("✔ All checked files are valid UTF-8.")
        sys.exit(0)


if __name__ == "__main__":
    main()
