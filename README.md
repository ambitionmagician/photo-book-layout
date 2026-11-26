# photo-book-layout — photo folder to print-ready PDF book (Python)

A complete Python 3 CLI (single .py file, argparse; Pillow allowed, no other deps) that takes a folder of JPG/PNG photos, reads EXIF dates when present, sorts chronologically, and lays out a print-ready PDF photo book (US Letter and A4 options) with one or two photos per page and captions from filename or EXIF description, via a hand-rolled minimal PDF writer or Pillow's PDF save — your choice, must actually work. Flags: --size, --per-page, --out, --title. --help required. The deliverable is the single .py file.

Produced by the Azure Foundry bench (draft gpt-6.2-astra-1, perfected by Kimi-K2.7-Code, QC by DeepSeek-V4-Pro) under Dr. Brent Allen Jensen's production order of Nov 25, 2026. Local gates: files: photo-book-layout.py 11793 B.
