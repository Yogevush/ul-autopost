#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
בונה תמונת פוסט לאינסטגרם, 1080 על 1350, ג'ייפג.
אינסטגרם מקבלת ג'ייפג בלבד דרך הממשק התכנותי, לא פי-אן-ג'י.

שימוש:
    python3 tools/make_image.py tools/scripts/post-03.json images/post-03.jpg
"""

import base64
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "tools" / "post_template.html"
FONT_DIR = ROOT / "node_modules" / "@fontsource" / "heebo" / "files"
W, H = 1080, 1350


def font_data_url(weight):
    p = FONT_DIR / f"heebo-hebrew-{weight}-normal.woff2"
    if not p.exists():
        sys.exit(f"חסר פונט: {p}\nהרץ: npm install @fontsource/heebo")
    return "data:font/woff2;base64," + base64.b64encode(p.read_bytes()).decode()


def main():
    if len(sys.argv) != 3:
        sys.exit("שימוש: make_image.py <תוכן.json> <פלט.jpg>")
    data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
    dest = pathlib.Path(sys.argv[2])
    dest.parent.mkdir(parents=True, exist_ok=True)

    html = TEMPLATE.read_text(encoding="utf-8")
    for w in (400, 700, 800):
        html = html.replace(f"FONT_{w}", font_data_url(w))
    html = html.replace("<script>\nconst D",
                        f"<script>window.__POST__={json.dumps(data, ensure_ascii=False)};</script>\n<script>\nconst D")

    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory() as td:
        page_file = pathlib.Path(td) / "page.html"
        page_file.write_text(html, encoding="utf-8")
        png = pathlib.Path(td) / "out.png"
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            page.goto(page_file.as_uri())
            page.wait_for_function("window.__ready__ === true")
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(400)
            page.screenshot(path=str(png))
            browser.close()
        # המרה לג'ייפג איכותי, בלי שקיפות, בדיוק מה שאינסטגרם מקבלת
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(png),
                        "-pix_fmt", "yuvj420p", "-q:v", "2", str(dest)], check=True)

    size_kb = dest.stat().st_size / 1024
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_name,width,height", "-of", "json", str(dest)],
                         capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    print(f"נוצר: {dest}\n  {s['width']}x{s['height']} | {s['codec_name']} | {size_kb:.0f} קילובייט")
    if (s["width"], s["height"]) != (W, H) or s["codec_name"] != "mjpeg":
        sys.exit("הפלט לא בפורמט הנכון.")
    if size_kb > 8000:
        sys.exit("הקובץ גדול מדי לאינסטגרם.")
    print("  עומד בדרישות אינסטגרם.")


if __name__ == "__main__":
    main()
