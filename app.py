# app.py — Hugging Face Spaces entry point
# HF Spaces expects app.py at the root and listens on port 7860.
# This file simply delegates to the real UI module.
import os
os.environ.setdefault("PORT", "7860")
os.environ.setdefault("HOST", "0.0.0.0")

from ui.app import main
if __name__ == "__main__":
    main()
