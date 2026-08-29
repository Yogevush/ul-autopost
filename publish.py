#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
פרסום אוטומטי לאינסטגרם ולדף הפייסבוק של יוניק הרצאות.
רץ ב-GitHub Actions. לא תלוי במחשב, בדפדפן או בקלוד.

לוח השידורים נקבע ב-content/schedule.json לפי ימי השבוע.
כברירת מחדל: שלוש תמונות בשבוע וריל אחד.

סוד יחיד שנדרש: META_TOKEN
מומלץ אסימון של דף פייסבוק, כזה לא פג תוקף.
הסקריפט גוזר ממנו לבד את מזהה הדף ואת חשבון האינסטגרם.
"""

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zoneinfo
from datetime import datetime

GRAPH = "https://graph.facebook.com/v23.0"
ROOT = pathlib.Path(__file__).parent
QUEUE = ROOT / "content" / "queue.json"
SCHEDULE = ROOT / "content" / "schedule.json"
STATE = ROOT / "state" / "published.json"
ISRAEL = zoneinfo.ZoneInfo("Asia/Jerusalem")
LOW_STOCK = 3


def log(msg):
    print(msg, flush=True)


def call(path, params=None, post=False, timeout=120):
    params = dict(params or {})
    url = f"{GRAPH}/{path.lstrip('/')}"
    if post:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(params).encode(), method="POST")
    else:
        req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        # לעולם לא להדפיס את האסימון עצמו ללוג
        raise SystemExit(f"שגיאת מטא ב-{path}: HTTP {e.code}\n{e.read().decode()[:800]}")


def resolve_targets(token):
    """מזהה לבד את הדף, את אסימון הדף ואת חשבון האינסטגרם.
    מקבל גם אסימון דף (לא פג, מומלץ) וגם אסימון משתמש (פג אחרי 60 יום)."""
    me = call("me", {"access_token": token, "fields": "id,name,category"})
    if me.get("category"):
        page_id, page_token, name = me["id"], token, me.get("name", "")
    else:
        pages = call("me/accounts", {"access_token": token,
                                     "fields": "id,name,access_token"}).get("data") or []
        if not pages:
            raise SystemExit("האסימון לא מחזיר אף דף פייסבוק. בדוק את ההרשאות שלו.")
        want = os.environ.get("PAGE_ID")
        page = next((p for p in pages if p["id"] == want), pages[0])
        page_id, page_token, name = page["id"], page["access_token"], page["name"]
        log("שים לב: זה אסימון משתמש שפג אחרי 60 יום. עדיף להחליף לאסימון דף שלא פג.")

    iba = (call(page_id, {"access_token": page_token,
                          "fields": "instagram_business_account"})
           .get("instagram_business_account") or {}).get("id")
    if not iba:
        raise SystemExit(f"לדף {name} אין חשבון אינסטגרם עסקי מקושר.")
    log(f"דף: {name} ({page_id}) | אינסטגרם: {iba}")
    return page_id, page_token, iba


def wait_for_container(container, token, minutes):
    """ממתין שאינסטגרם תסיים לעבד את המדיה."""
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        st = call(container, {"fields": "status_code,status", "access_token": token})
        code = st.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise SystemExit(f"אינסטגרם דחתה את המדיה: {code} | {st.get('status')}")
        time.sleep(10)
    raise SystemExit(f"המדיה לא סיימה עיבוד תוך {minutes} דקות.")


def publish_instagram(ig_id, token, post, media_url):
    if post["type"] == "reel":
        params = {"media_type": "REELS", "video_url": media_url,
                  "share_to_feed": "true", "caption": post["caption_instagram"],
                  "access_token": token}
        wait_minutes = 6
    else:
        params = {"image_url": media_url, "caption": post["caption_instagram"],
                  "access_token": token}
        wait_minutes = 3

    container = call(f"{ig_id}/media", params, post=True)["id"]
    wait_for_container(container, token, wait_minutes)
    return call(f"{ig_id}/media_publish",
                {"creation_id": container, "access_token": token}, post=True)["id"]


def publish_facebook(page_id, page_token, post, media_url):
    if post["type"] == "reel":
        r = call(f"{page_id}/videos",
                 {"file_url": media_url, "description": post["caption_facebook"],
                  "access_token": page_token}, post=True)
        return r.get("id")
    r = call(f"{page_id}/photos",
             {"url": media_url, "caption": post["caption_facebook"],
              "access_token": page_token}, post=True)
    return r.get("post_id") or r.get("id")


def open_issue(title, body):
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("REPO")
    if not (tok and repo):
        return
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"https://api.github.com/repos/{repo}/issues",
            data=json.dumps({"title": title, "body": body}).encode(),
            headers={"Authorization": f"Bearer {tok}",
                     "Accept": "application/vnd.github+json"}, method="POST"), timeout=30)
    except Exception as e:
        log(f"לא הצלחתי לפתוח דיווח: {e}")


def todays_slot(now, schedule):
    """מחזיר את סוג התוכן שאמור לצאת היום, או None אם היום לא יום פרסום."""
    # שני=0 ... ראשון=6 בפייתון. ממירים לשמות ברורים.
    names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    return schedule["days"].get(names[now.weekday()])


def main():
    token = os.environ.get("META_TOKEN", "").strip()
    if not token:
        raise SystemExit("חסר הסוד META_TOKEN בהגדרות המאגר.")

    schedule = json.loads(SCHEDULE.read_text(encoding="utf-8"))
    now = datetime.now(ISRAEL)
    forced = os.environ.get("FORCE", "").lower() == "true"

    if not forced and now.hour != schedule["hour_israel"]:
        log(f"השעה בישראל {now:%H:%M}, לא שעת הפרסום. יוצא.")
        return

    want = os.environ.get("FORCE_TYPE") or todays_slot(now, schedule)
    if not want:
        log(f"{now:%A} הוא לא יום פרסום בלוח. יוצא.")
        return

    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"published": []}
    done = {p["id"] for p in state["published"]}
    today = now.strftime("%Y-%m-%d")

    if any(p.get("date") == today for p in state["published"]):
        log("כבר פורסם היום. יוצא.")
        return

    pending = [p for p in queue if p["id"] not in done and not p.get("hold")]
    ready = [p for p in pending if p["type"] == want]
    if not ready:
        open_issue(f"אין תוכן מסוג {want} בתור",
                   f"לוח השידורים מבקש {want} היום ואין כזה ב-content/queue.json.")
        raise SystemExit(f"אין תוכן מסוג {want} בתור.")

    post = ready[0]
    repo, ref = os.environ["REPO"], os.environ.get("REF", "main")
    folder = "videos" if post["type"] == "reel" else "images"
    # GitHub Pages מגיש וידאו עם סוג התוכן הנכון, raw מגיש אותו כזרם בתים.
    # לכן וידאו הולך דרך Pages ותמונות יכולות ללכת בשתי הדרכים.
    base = schedule.get("media_base")
    if not base:
        owner, name = repo.split("/")
        base = f"https://{owner.lower()}.github.io/{name}"
    media_url = f"{base.rstrip('/')}/{folder}/{post['file']}"
    log(f"מפרסם {post['type']}: {post['id']}\n{media_url}")

    page_id, page_token, ig_id = resolve_targets(token)

    ig_post_id = publish_instagram(ig_id, page_token, post, media_url)
    log(f"אינסטגרם פורסם: {ig_post_id}")

    fb_post_id = None
    if post.get("caption_facebook"):
        try:
            fb_post_id = publish_facebook(page_id, page_token, post, media_url)
            log(f"פייסבוק פורסם: {fb_post_id}")
        except SystemExit as e:
            log(f"פייסבוק נכשל אבל אינסטגרם עבר: {e}")

    state["published"].append({
        "id": post["id"], "type": post["type"], "date": today,
        "time": now.strftime("%H:%M"),
        "instagram_post_id": ig_post_id, "facebook_post_id": fb_post_id,
    })
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    for kind in ("image", "reel"):
        left = len([p for p in pending if p["type"] == kind and p["id"] != post["id"]])
        log(f"נשארו בתור מסוג {kind}: {left}")
        if left == LOW_STOCK:
            open_issue(f"מלאי {kind} אוזל", f"נשארו {left} פריטים מסוג {kind} בתור.")


if __name__ == "__main__":
    sys.exit(main())
