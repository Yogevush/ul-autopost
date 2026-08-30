#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
פרסום אוטומטי לאינסטגרם ולדף הפייסבוק של יוניק הרצאות.
רץ ב-GitHub Actions. לא תלוי במחשב, בדפדפן או בקלוד.

לוח השידורים נקבע ב-content/schedule.json לפי ימי השבוע.
סוגי משבצת בלוח:
    "reel"      ריל (וידאו אנכי). באינסטגרם יוצא כריל, בפייסבוק כריל של הדף.
    "post"      פוסט: קרוסלה אם יש בתור, ואם אין, תמונה בודדת.
    "carousel"  קרוסלה בלבד.
    "image"     תמונה בודדת בלבד.
    null        לא מפרסמים היום.

חלון פרסום: מ-hour_israel ובמשך window_hours שעות. גיטהאב מאחר לפעמים
בהפעלת משימות מתוזמנות, לפעמים בשעות. החלון דואג שהאיחור לא יבטל את הפוסט.
בכל מקרה יוצא לכל היותר פוסט אחד ביום (state/published.json).

פעם ביום נשמרת גם תמונת מצב של מספר העוקבים ושל ביצועי הפוסטים
(state/metrics.json), כדי שאפשר יהיה לראות את הצמיחה לאורך זמן.

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
from datetime import datetime, timedelta

API_VERSION = "v23.0"
GRAPH = f"https://graph.facebook.com/{API_VERSION}"
RUPLOAD = f"https://rupload.facebook.com/video-upload/{API_VERSION}"
ROOT = pathlib.Path(__file__).parent
QUEUE = ROOT / "content" / "queue.json"
SCHEDULE = ROOT / "content" / "schedule.json"
STATE = ROOT / "state" / "published.json"
METRICS = ROOT / "state" / "metrics.json"
ISRAEL = zoneinfo.ZoneInfo("Asia/Jerusalem")
LOW_STOCK = 3
METRICS_DAYS = 21          # כמה ימים אחורה מרעננים ביצועי פוסטים


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


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path, data):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- זיהוי הדף וחשבון האינסטגרם ----------

def resolve_targets(token, page_id_hint=None):
    """מזהה את הדף, את אסימון הדף ואת חשבון האינסטגרם.
    מקבל גם אסימון דף (לא פג, מומלץ) וגם אסימון משתמש (פג אחרי 60 יום).

    הערה חשובה: הדף של יוניק הרצאות לא מוחזר בכלל ב-me/accounts, כנראה בגלל
    הבעלות עליו במטא. לכן כשיש מזהה דף מפורש (page_id בקובץ הלוח או משתנה
    הסביבה PAGE_ID) פונים אליו ישירות ולא מחפשים אותו ברשימה."""
    want = os.environ.get("PAGE_ID") or page_id_hint

    if want:
        page = call(want, {"access_token": token, "fields": "id,name,access_token"})
        page_id = page["id"]
        page_token = page.get("access_token") or token
        name = page.get("name", "")
        if not page.get("access_token"):
            log("שים לב: לא התקבל אסימון דף נפרד, משתמשים באסימון שניתן כמו שהוא.")
    else:
        me = call("me", {"access_token": token, "fields": "id,name,category"})
        if me.get("category"):
            page_id, page_token, name = me["id"], token, me.get("name", "")
        else:
            pages = call("me/accounts", {"access_token": token,
                                         "fields": "id,name,access_token"}).get("data") or []
            if not pages:
                raise SystemExit("האסימון לא מחזיר אף דף פייסבוק. בדוק את ההרשאות שלו, "
                                 "או הגדר page_id בקובץ content/schedule.json.")
            page = pages[0]
            page_id, page_token, name = page["id"], page["access_token"], page["name"]
            log("שים לב: זה אסימון משתמש שפג אחרי 60 יום. עדיף להחליף לאסימון דף שלא פג.")

    iba = (call(page_id, {"access_token": page_token,
                          "fields": "instagram_business_account"})
           .get("instagram_business_account") or {}).get("id")
    if not iba:
        raise SystemExit(f"לדף {name} אין חשבון אינסטגרם עסקי מקושר.")
    log(f"דף: {name} ({page_id}) | אינסטגרם: {iba}")
    return page_id, page_token, iba


# ---------- אינסטגרם ----------

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


def ig_extra(post):
    """פרמטרים משותפים: שותפים לפוסט (עד שלושה שמות משתמש באינסטגרם).
    שותף שמאשר את ההזמנה מקבל את הפוסט גם בפרופיל שלו, מול העוקבים שלו."""
    extra = {}
    collabs = [c.lstrip("@") for c in (post.get("collaborators") or []) if c][:3]
    if collabs:
        extra["collaborators"] = json.dumps(collabs)
    return extra


def publish_instagram(ig_id, token, post, media_urls):
    if post["type"] == "reel":
        params = {"media_type": "REELS", "video_url": media_urls[0],
                  "share_to_feed": "true", "caption": post["caption_instagram"],
                  "access_token": token, **ig_extra(post)}
        wait_minutes = 6
    elif post["type"] == "carousel":
        children = []
        for url in media_urls:
            children.append(call(f"{ig_id}/media",
                                 {"image_url": url, "is_carousel_item": "true",
                                  "access_token": token}, post=True)["id"])
        params = {"media_type": "CAROUSEL", "children": ",".join(children),
                  "caption": post["caption_instagram"], "access_token": token,
                  **ig_extra(post)}
        wait_minutes = 4
    else:
        params = {"image_url": media_urls[0], "caption": post["caption_instagram"],
                  "access_token": token, **ig_extra(post)}
        wait_minutes = 3

    container = call(f"{ig_id}/media", params, post=True)["id"]
    wait_for_container(container, token, wait_minutes)
    return call(f"{ig_id}/media_publish",
                {"creation_id": container, "access_token": token}, post=True)["id"]


# ---------- פייסבוק ----------

def fb_reel(page_id, page_token, post, media_url):
    """ריל אמיתי של הדף (לא סתם וידאו בפיד). רילים הם מה שפייסבוק מפיצה
    היום גם למי שלא עוקב אחרי הדף."""
    start = call(f"{page_id}/video_reels",
                 {"upload_phase": "start", "access_token": page_token}, post=True)
    video_id = start["video_id"]

    req = urllib.request.Request(f"{RUPLOAD}/{video_id}", data=b"", method="POST",
                                 headers={"Authorization": f"OAuth {page_token}",
                                          "file_url": media_url})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            up = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"העלאת הריל לפייסבוק נכשלה: HTTP {e.code}\n{e.read().decode()[:800]}")
    if not up.get("success"):
        raise SystemExit(f"העלאת הריל לפייסבוק לא אושרה: {up}")

    # ממתינים שההעלאה תיקלט לפני הפרסום
    deadline = time.time() + 4 * 60
    while time.time() < deadline:
        st = call(video_id, {"fields": "status", "access_token": page_token}).get("status") or {}
        phase = (st.get("uploading_phase") or {}).get("status")
        if phase == "complete":
            break
        if phase == "error":
            raise SystemExit(f"פייסבוק דיווחה על שגיאה בהעלאת הריל: {st}")
        time.sleep(10)

    call(f"{page_id}/video_reels",
         {"upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
          "description": post["caption_facebook"], "access_token": page_token}, post=True)
    return video_id


def publish_facebook(page_id, page_token, post, media_urls):
    if post["type"] == "reel":
        try:
            return fb_reel(page_id, page_token, post, media_urls[0])
        except SystemExit as e:
            log(f"ריל של פייסבוק נכשל, מפרסם כווידאו רגיל במקום: {e}")
            r = call(f"{page_id}/videos",
                     {"file_url": media_urls[0], "description": post["caption_facebook"],
                      "access_token": page_token}, post=True)
            return r.get("id")

    if post["type"] == "carousel":
        media = []
        for url in media_urls:
            r = call(f"{page_id}/photos", {"url": url, "published": "false",
                                            "access_token": page_token}, post=True)
            media.append({"media_fbid": r["id"]})
        r = call(f"{page_id}/feed", {"message": post["caption_facebook"],
                                      "attached_media": json.dumps(media),
                                      "access_token": page_token}, post=True)
        return r.get("id")

    r = call(f"{page_id}/photos",
             {"url": media_urls[0], "caption": post["caption_facebook"],
              "access_token": page_token}, post=True)
    return r.get("post_id") or r.get("id")


# ---------- מדדים: עוקבים וביצועי פוסטים ----------

def collect_metrics(page_id, page_token, ig_id, state, today):
    """תמונת מצב יומית. מנסה, ואם משהו נכשל לא עוצר את הפרסום."""
    metrics = load_json(METRICS, {"days": {}, "posts": {}})
    metrics.setdefault("days", {})
    metrics.setdefault("posts", {})
    day = {}
    try:
        ig = call(ig_id, {"fields": "followers_count,media_count", "access_token": page_token})
        day["instagram_followers"] = ig.get("followers_count")
        day["instagram_posts"] = ig.get("media_count")
    except SystemExit as e:
        log(f"לא הצלחתי לקרוא עוקבים באינסטגרם: {e}")
    try:
        fb = call(page_id, {"fields": "followers_count,fan_count", "access_token": page_token})
        day["facebook_followers"] = fb.get("followers_count")
        day["facebook_likes"] = fb.get("fan_count")
    except SystemExit as e:
        log(f"לא הצלחתי לקרוא עוקבים בפייסבוק: {e}")
    metrics["days"][today] = day

    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=METRICS_DAYS)).strftime("%Y-%m-%d")
    for p in state["published"]:
        if p.get("date", "") < cutoff or not p.get("instagram_post_id"):
            continue
        entry = metrics["posts"].setdefault(p["id"], {"type": p["type"], "date": p["date"]})
        for metric_set in ("reach,views,likes,comments,saved,shares,total_interactions",
                           "reach,likes,comments,saved"):
            try:
                data = call(f"{p['instagram_post_id']}/insights",
                            {"metric": metric_set, "access_token": page_token}).get("data") or []
                entry["instagram"] = {d["name"]: (d.get("values") or [{}])[0].get("value")
                                      for d in data}
                entry["updated"] = today
                break
            except SystemExit as e:
                log(f"אין תובנות ל-{p['id']} עם {metric_set}: {str(e)[:120]}")
    save_json(METRICS, metrics)
    log(f"מדדים נשמרו: {day}")


# ---------- לוח השידורים ----------

def todays_slot(now, schedule):
    """מחזיר את סוג המשבצת של היום, או None אם היום לא יום פרסום."""
    names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    return schedule["days"].get(names[now.weekday()])


def in_window(now, schedule):
    start = int(schedule.get("hour_israel", 20))
    hours = int(schedule.get("window_hours", 1))
    return start <= now.hour < start + hours


def pick(pending, want):
    """בוחר את הפריט הראשון בתור שמתאים למשבצת. "post" מעדיף קרוסלה על תמונה.
    אם אין פריט מהסוג המבוקש, מחזיר פריט מסוג אחר (עדיף משהו מאשר כלום)
    ומסמן שזה היה תחליף."""
    # לכל משבצת: אילו סוגים נחשבים "בבית" (בלי דיווח), ואז התחליפים לפי סדר עדיפות.
    native, fallback = {
        "reel": (["reel"], ["carousel", "image"]),
        "post": (["carousel", "image"], ["reel"]),
        "carousel": (["carousel"], ["image", "reel"]),
        "image": (["image"], ["carousel", "reel"]),
    }.get(want, ([want], []))
    for kind in native:
        for p in pending:
            if p["type"] == kind:
                return p, False
    for kind in fallback:
        for p in pending:
            if p["type"] == kind:
                return p, True
    return None, False


def media_urls_for(post, base):
    folder = "videos" if post["type"] == "reel" else "images"
    files = post.get("files") or [post["file"]]
    return [f"{base.rstrip('/')}/{folder}/{f}" for f in files]


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


def main():
    token = os.environ.get("META_TOKEN", "").strip()
    if not token:
        raise SystemExit("חסר הסוד META_TOKEN בהגדרות המאגר.")

    schedule = load_json(SCHEDULE, None)
    if not schedule:
        raise SystemExit("חסר content/schedule.json")
    now = datetime.now(ISRAEL)
    today = now.strftime("%Y-%m-%d")
    forced = os.environ.get("FORCE", "").lower() == "true"

    if not forced and not in_window(now, schedule):
        log(f"השעה בישראל {now:%H:%M}, מחוץ לחלון הפרסום. יוצא.")
        return

    state = load_json(STATE, {"published": []})
    queue = load_json(QUEUE, [])
    want = os.environ.get("FORCE_TYPE") or todays_slot(now, schedule)

    page_id = page_token = ig_id = None

    # פעם ביום: תמונת מצב של עוקבים וביצועים. לא תלוי ביום פרסום.
    metrics = load_json(METRICS, {"days": {}})
    if not forced and today not in metrics.get("days", {}):
        page_id, page_token, ig_id = resolve_targets(token, schedule.get("page_id"))
        try:
            collect_metrics(page_id, page_token, ig_id, state, today)
        except Exception as e:  # המדדים לעולם לא עוצרים פרסום
            log(f"איסוף המדדים נכשל: {e}")

    if not want:
        log(f"{now:%A} הוא לא יום פרסום בלוח. יוצא.")
        return
    if any(p.get("date") == today for p in state["published"]):
        log("כבר פורסם היום. יוצא.")
        return

    done = {p["id"] for p in state["published"]}
    pending = [p for p in queue if p["id"] not in done and not p.get("hold")]
    post, substitute = pick(pending, want)
    if not post:
        open_issue("התור ריק", "לוח השידורים מבקש לפרסם היום ואין שום פריט ב-content/queue.json.")
        raise SystemExit("התור ריק.")
    if substitute:
        open_issue(f"אין תוכן מסוג {want} בתור",
                   f"לוח השידורים ביקש {want} היום. פורסם במקום זה {post['type']}: {post['id']}. "
                   f"כדאי להוסיף תוכן מסוג {want} לתור.")

    repo = os.environ.get("REPO", "")
    base = schedule.get("media_base")
    if not base:
        owner, name = repo.split("/")
        base = f"https://{owner.lower()}.github.io/{name}"
    media_urls = media_urls_for(post, base)
    log(f"מפרסם {post['type']}: {post['id']}\n" + "\n".join(media_urls))

    if not page_id:
        page_id, page_token, ig_id = resolve_targets(token, schedule.get("page_id"))

    ig_post_id = publish_instagram(ig_id, page_token, post, media_urls)
    log(f"אינסטגרם פורסם: {ig_post_id}")

    fb_post_id = None
    if post.get("caption_facebook"):
        try:
            fb_post_id = publish_facebook(page_id, page_token, post, media_urls)
            log(f"פייסבוק פורסם: {fb_post_id}")
        except SystemExit as e:
            log(f"פייסבוק נכשל אבל אינסטגרם עבר: {e}")

    state["published"].append({
        "id": post["id"], "type": post["type"], "date": today,
        "time": now.strftime("%H:%M"),
        "instagram_post_id": ig_post_id, "facebook_post_id": fb_post_id,
    })
    save_json(STATE, state)

    for kind in ("reel", "carousel", "image"):
        left = len([p for p in pending if p["type"] == kind and p["id"] != post["id"]])
        log(f"נשארו בתור מסוג {kind}: {left}")
        if left == LOW_STOCK:
            open_issue(f"מלאי {kind} אוזל", f"נשארו {left} פריטים מסוג {kind} בתור.")


if __name__ == "__main__":
    sys.exit(main())
