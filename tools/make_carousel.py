#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
בונה קרוסלה: כמה תמונות 1080 על 1350 מקובץ תסריט אחד.
כל שקופית היא בדיוק תסריט של תמונה בודדת (כמו make_image.py).

שימוש:
    python3 tools/make_carousel.py tools/scripts/car-01.json images/

התסריט:
{
  "id": "car-01-choose-lecture",
  "slides": [ {"title": "...", "body": "...", "light": false}, ... ]
}

הפלט: images/<id>-1.jpg, images/<id>-2.jpg ... ורשימת הקבצים לתור.
"""

import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent
MAKE_IMAGE = ROOT / "make_image.py"


def main():
    if len(sys.argv) != 3:
        sys.exit("שימוש: make_carousel.py <תסריט.json> <תיקיית פלט>")
    script = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
    outdir = pathlib.Path(sys.argv[2])
    slides = script.get("slides") or []
    if not 2 <= len(slides) <= 10:
        sys.exit("קרוסלה צריכה בין 2 ל-10 שקופיות.")

    files = []
    with tempfile.TemporaryDirectory() as td:
        for i, slide in enumerate(slides, start=1):
            tmp = pathlib.Path(td) / f"slide{i}.json"
            tmp.write_text(json.dumps(slide, ensure_ascii=False), encoding="utf-8")
            name = f"{script['id']}-{i}.jpg"
            subprocess.run([sys.executable, str(MAKE_IMAGE), str(tmp), str(outdir / name)], check=True)
            files.append(name)

    print("\nלתור (content/queue.json):")
    print(json.dumps({"id": script["id"], "type": "carousel", "files": files},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
