import os
import tkinter as tk
from tkinter import filedialog, messagebox

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

def has_null_bytes(file_path, chunk_size=8192):
    """Return True if the file contains at least one null byte."""
    try:
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                if b"\x00" in chunk:
                    return True
    except OSError as e:
        print(f"Could not read {file_path}: {e}")
    return False

def main():
    # Set up a hidden root window for Tkinter
    root = tk.Tk()
    root.withdraw()

    folder = filedialog.askdirectory(title="Select folder to scan for null bytes")
    if not folder:
        print("No folder selected. Exiting.")
        return

    print(f"Scanning folder: {folder}\n")
    files_with_nulls = []

    for dirpath, dirnames, filenames in os.walk(folder):
        for name in filenames:
            file_path = os.path.join(dirpath, name)
            if '.' + str(file_path.split('.')[-1]) in TEXT_EXTENSIONS:
                if has_null_bytes(file_path):
                    files_with_nulls.append(file_path)
                    print(f"[NULL BYTES] {file_path}")
                else:
                    print(f"[OK]         {file_path}")

    if files_with_nulls:
        msg = "Files containing null bytes:\n\n" + "\n".join(files_with_nulls)
    else:
        msg = "No files with null bytes were found."

    # Show a simple summary dialog
    messagebox.showinfo("Scan complete", msg)

if __name__ == "__main__":
    main()
