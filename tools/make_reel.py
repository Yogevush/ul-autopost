#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
בונה ריל מונפש לאינסטגרם מקובץ תסריט, בלי מצלמה.
רץ בענן בסביבת העבודה של קלוד, לא ב-GitHub Actions.

שימוש:
    python3 tools/make_reel.py tools/scripts/reel-01.json videos/reel-01.mp4

הפלט: MP4, H.264 + AAC, 1080x1920, 30 פריימים לשנייה.
בדיוק המפרט שאינסטגרם דורשת לריל.
"""

import base64
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "tools" / "reel_template.html"
FONT_DIR = ROOT / "node_modules" / "@fontsource" / "heebo" / "files"
FPS = 30
W, H = 1080, 1920


def font_data_url(weight):
    p = FONT_DIR / f"heebo-hebrew-{weight}-normal.woff2"
    if not p.exists():
        sys.exit(f"חסר פונט: {p}\nהרץ: npm install @fontsource/heebo")
    return "data:font/woff2;base64," + base64.b64encode(p.read_bytes()).decode()


def build_html(script):
    html = TEMPLATE.read_text(encoding="utf-8")
    for w in (400, 700, 800):
        html = html.replace(f"FONT_{w}", font_data_url(w))
    inject = (f"<script>window.__SCENES__={json.dumps(script['scenes'], ensure_ascii=False)};"
              f"window.__DURATION__={script['duration']};</script>")
    return html.replace("<script>\n// SCENES", inject + "\n<script>\n// SCENES")


def render_frames(html, duration, outdir):
    from playwright.sync_api import sync_playwright
    page_file = outdir / "page.html"
    page_file.write_text(html, encoding="utf-8")
    total = int(duration * FPS)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--force-device-scale-factor=1"])
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        page.goto(page_file.as_uri())
        page.wait_for_function("window.__ready__ === true")
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(400)

        for i in range(total):
            page.evaluate(f"window.render({i / FPS})")
            page.screenshot(path=str(outdir / f"f{i:05d}.png"))
            if i % 60 == 0:
                print(f"  פריים {i}/{total}", flush=True)
        browser.close()
    return total


def encode(outdir, dest, duration):
    dest.parent.mkdir(parents=True, exist_ok=True)
    # פס קול שקט. אינסטגרם מעדיפה ריל עם ערוץ אודיו תקין.
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-framerate", str(FPS), "-i", str(outdir / "f%05d.png"),
        "-f", "lavfi", "-t", str(duration), "-i", "anullsrc=r=48000:cl=stereo",
        "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
        "-pix_fmt", "yuv420p", "-r", str(FPS), "-g", str(FPS * 2),
        "-b:v", "6M", "-maxrate", "8M", "-bufsize", "12M",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
        "-movflags", "+faststart", "-shortest", str(dest),
    ], check=True)


def verify(dest):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_name,width,height,r_frame_rate:format=duration,size",
         "-of", "json", str(dest)], capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    size_mb = int(info["format"]["size"]) / 1e6
    dur = float(info["format"]["duration"])
    codecs = {s["codec_name"] for s in info["streams"]}
    vid = next(s for s in info["streams"] if s.get("width"))

    problems = []
    if size_mb > 95:
        problems.append(f"הקובץ {size_mb:.0f} מגה, מעל המגבלה של אינסטגרם")
    if not (3 <= dur <= 90):
        problems.append(f"אורך {dur:.1f} שניות, מחוץ לטווח של ריל")
    if (vid["width"], vid["height"]) != (W, H):
        problems.append(f"רזולוציה {vid['width']}x{vid['height']} ולא {W}x{H}")
    if "h264" not in codecs or "aac" not in codecs:
        problems.append(f"קודקים לא תקינים: {codecs}")

    print(f"\nנוצר: {dest}")
    print(f"  {vid['width']}x{vid['height']} | {dur:.1f} שניות | {size_mb:.1f} מגה | {sorted(codecs)}")
    if problems:
        sys.exit("בעיות:\n  " + "\n  ".join(problems))
    print("  עומד בכל דרישות אינסטגרם לריל.")


def main():
    if len(sys.argv) != 3:
        sys.exit("שימוש: make_reel.py <תסריט.json> <פלט.mp4>")
    script = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
    dest = pathlib.Path(sys.argv[2])
    html = build_html(script)
    with tempfile.TemporaryDirectory() as td:
        outdir = pathlib.Path(td)
        print(f"מרנדר {script['duration']} שניות...")
        render_frames(html, script["duration"], outdir)
        print("מקודד...")
        encode(outdir, dest, script["duration"])
    verify(dest)


if __name__ == "__main__":
    main()
