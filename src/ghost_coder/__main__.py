"""Entry point for `python -m ghost_coder` and the `ghost-coder` console script.

Imports stay inside main(): on Windows each child process (broker, listener, typer) re-imports
the main module, and importing the UI stack there would cost every child ~1.5 s and extra memory.
"""


def main() -> None:
    from ghost_coder.app import main as app_main

    app_main()


if __name__ == "__main__":
    main()
