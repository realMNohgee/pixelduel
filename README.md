# pixelduel ⚔️

**Pixel-by-pixel image comparison.** Zero dependencies, pure Python stdlib.

Compare two images at the pixel level with configurable tolerance thresholds and ASCII-art visual diffs. Full PNG parsing from scratch — no PIL, no Pillow, no external libs.

## One tool, many domains

| Domain | What pixelduel does for you |
|---|---|
| 🧪 **QA / Testing** | Visual regression testing — catch unintended UI changes pixel by pixel |
| 🤖 **Agentic AI** | Give AI agents the ability to compare screenshots and verify visual output |
| 🎨 **Design / UX** | Compare mockup revisions, detect even single-pixel differences |
| 🔧 **CI/CD Pipelines** | JSON output integrates with automated visual diff pipelines |
| 🏗️ **Build Systems** | Verify asset generation — sprites, icons, renders match expected output |

## Install

```bash
git clone git@github.com:realMNohgee/pixelduel.git
cd pixelduel
python3 pixelduel.py --help
```

## Quick start

```bash
# Compare two images and get statistics
python3 pixelduel.py diff screenshot_v1.png screenshot_v2.png

# Same comparison with ASCII-art visual diff
python3 pixelduel.py compare before.png after.png

# Tune sensitivity — ignore small differences
python3 pixelduel.py diff --threshold 25 img1.png img2.png

# Machine-readable JSON output
python3 pixelduel.py diff --format json img1.png img2.png
```

## How it works

pixelduel parses PNG files from scratch using Python's `struct` and `zlib` modules — no external image libraries needed. It reads the IHDR chunk for dimensions, decompresses IDAT chunks, reverses PNG filter algorithms, and compares raw RGB values pixel by pixel.

The `--threshold` flag controls sensitivity: a pixel is considered "different" only when at least one RGB channel differs by the threshold amount or more.

## License

MIT — see [LICENSE](LICENSE).

---

🧰 **[Tool on Hermtica Marketplace](https://hermtica.com/marketplace)** — the open, agent-agnostic marketplace for AI agent tools.
