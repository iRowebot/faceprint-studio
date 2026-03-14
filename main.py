"""FacePrint Studio — Entry point.

Run with:  python main.py
"""

import sys


def main() -> None:
    try:
        import cv2  # noqa: F401
    except ImportError:
        msg = (
            "OpenCV is not installed.\n\n"
            "Install with:\n"
            "  pip install opencv-python\n"
        )
        print(f"ERROR: {msg}", file=sys.stderr)
        try:
            import tkinter as tk
            import tkinter.messagebox as mb

            root = tk.Tk()
            root.withdraw()
            mb.showerror("Missing Dependency", msg)
            root.destroy()
        except Exception:
            pass
        sys.exit(1)

    from app import App

    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
