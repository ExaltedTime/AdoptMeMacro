# Adopt Me Macro
A very much vibe-coded attempt at automating the needs of an Adopt Me pet.

## Running the script
After navigating to the working directory, run
```
python main.py
```
Requires Windows, python and a lot of adjustments, since button placements are hard-coded right now.

The GUI's **Leave & rejoin** button also needs OCR: `pip install pytesseract` and install [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki). If `tesseract.exe` isn't on your PATH, set `TESSERACT_CMD` in `magic_numbers.py`. Everything else runs without it.

## Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - how the code works
- [docs/PLANNED.md](docs/PLANNED.md) - what's being worked on
