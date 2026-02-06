"""Small Tkinter helper for collecting a visible password."""

from __future__ import annotations

import tkinter as tk


def prompt_password() -> str:
    """
    Open a minimal GUI window asking the user to enter a password.

    The password is visible (no masking) to keep dependencies minimal and avoid
    obscure clipboard interactions. Returns the entered string.
    """
    result = {"value": ""}
    width, height = 150, 90

    root = tk.Tk()
    root.title("Secrets")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    # Center the window on the current screen.
    root.update_idletasks()
    x = (root.winfo_screenwidth() // 2) - (width // 2)
    y = (root.winfo_screenheight() // 2) - (height // 2)
    root.geometry(f"{width}x{height}+{x}+{y}")

    label = tk.Label(root, text="Enter password:")
    label.pack(pady=(10, 5))

    entry = tk.Entry(root, show="")
    entry.pack(pady=(0, 5))
    entry.focus_set()

    def submit(event=None):
        result["value"] = entry.get()
        root.quit()

    button = tk.Button(root, text="Enter", command=submit)
    button.pack(pady=(0, 10))

    entry.bind("<Return>", submit)
    root.protocol("WM_DELETE_WINDOW", submit)

    root.mainloop()
    root.destroy()
    return result["value"]
