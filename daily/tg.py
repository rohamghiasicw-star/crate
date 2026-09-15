#!/usr/bin/env python3
"""Telegram: send a clip card with tap-to-grade buttons, and collect the taps.

    python3 tg.py --send            newest run -> one card per clip
    python3 tg.py --collect         drain taps into eval/verdicts.jsonl
    python3 tg.py --collect --watch poll until interrupted

WHY TELEGRAM AND NOT THE EMAIL FORM. Roham's own description of when he grades: waking up,
or in the bathroom. A prefilled web form costs an app switch, a page load and a submit. An
inline keyboard costs one tap without leaving the chat. The email still goes out because it
is the better read on a laptop, but the buttons that matter live here.

NO PUBLIC ENDPOINT. Callback taps are collected by polling getUpdates from this Mac, so
there is no webhook, no tunnel and nothing to expose. The offset is persisted, so a tap made
while the Mac was asleep is still there when it wakes.
"""
import argparse
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "eval"))
import evallib as EV              # noqa: E402

ENVFILE = os.path.expanduser("~/client-reminders/.telegram_env")
OFFSET = os.path.join(HERE, "tg_offset.json")
SENT = os.path.join(HERE, "tg_sent.jsonl")
RUNS = os.path.join(HERE, "runs")


def creds():
    env = {}
    for line in open(ENVFILE):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"]


def api(method, payload=None, timeout=40):
    tok, _ = creds()
    url = "https://api.telegram.org/bot%s/%s" % (tok, method)
    if payload is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())


def card(row, i, n):
    b, crown = row.get("base") or {}, row.get("crown") or {}
    cands = [c for c in (row.get("candidates") or [])[1:4] if c.get("title")]
    L = ["*Addify* · %d of %d" % (i, n), ""]
    L.append("Engine says: *%s*" % (b.get("base_song") or "nothing"))
    if b.get("speed") and b["speed"] != "as posted":
        L.append("Read as: _%s_" % b["speed"])
    L.append("Sound page: _%s_" % (row.get("sound_title") or "unnamed original"))
    L.append("%s plays" % format(row.get("plays") or 0, ","))
    if crown.get("title"):
        L += ["", "*Best guess at the exact upload*",
              "[%s](%s)" % (crown["title"][:60], crown.get("url")),
              "match %s" % crown.get("core")]
    if cands:
        L += ["", "*Other contenders*"]
        L += ["· [%s](%s) — match %s" % (c["title"][:46], c["url"], c.get("core"))
              for c in cands]
    L += ["", "[Open the clip](%s)" % row["url"]]
    return "\n".join(L)


def keyboard(cid):
    return {"inline_keyboard": [
        [{"text": "✅ Right", "callback_data": "v|%s|right" % cid},
         {"text": "❌ Wrong", "callback_data": "v|%s|wrong" % cid}],
        [{"text": "🎛 Right song, wrong edit", "callback_data": "v|%s|wrong_transform" % cid}],
        [{"text": "🤷 Skip", "callback_data": "v|%s|skip" % cid}]]}


def send_run(path, only_unlabelled=True):
    rows = [r for r in EV._read(path) if r.get("clip_id")]
    if only_unlabelled:
        graded = {v.get("clip_id") for v in EV.load_verdicts()}
        rows = [r for r in rows if r["clip_id"] not in graded]
    _, chat = creds()
    for i, r in enumerate(rows, 1):
        out = api("sendMessage", {"chat_id": chat, "text": card(r, i, len(rows)),
                                  "parse_mode": "Markdown",
                                  "disable_web_page_preview": True,
                                  "reply_markup": keyboard(r["clip_id"])})
        EV.append(SENT, {"clip_id": r["clip_id"], "message_id":
                         (out.get("result") or {}).get("message_id"), "when": int(time.time())})
        time.sleep(0.4)          # Telegram is happy at this rate and unhappy above it
    return len(rows)


def _offset(v=None):
    if v is None:
        try:
            return json.load(open(OFFSET)).get("offset", 0)
        except Exception:
            return 0
    json.dump({"offset": v}, open(OFFSET, "w"))


def collect():
    """Drain taps. Each becomes a verdict row and the card is edited to show the answer."""
    got = 0
    while True:
        r = api("getUpdates", {"offset": _offset() + 1, "timeout": 0, "limit": 100})
        ups = r.get("result") or []
        if not ups:
            break
        for u in ups:
            _offset(u["update_id"])
            cb = u.get("callback_query")
            if not cb:
                continue
            data = (cb.get("data") or "").split("|")
            if len(data) != 3 or data[0] != "v":
                continue
            _, cid, verdict = data
            api("answerCallbackQuery", {"callback_query_id": cb["id"],
                                        "text": "logged: %s" % verdict})
            if verdict != "skip":
                EV.append(EV.VERDICTS, {
                    "verdict_id": EV.vid(cid, verdict, str(int(time.time()))),
                    "clip_id": cid, "verdict": verdict, "by": "roham",
                    "channel": "telegram", "state": "proposed",
                    "when": int(time.time())})
            msg = cb.get("message") or {}
            mark = {"right": "✅ marked RIGHT", "wrong": "❌ marked WRONG",
                    "wrong_transform": "🎛 marked RIGHT SONG, WRONG EDIT",
                    "skip": "🤷 skipped"}[verdict]
            try:
                api("editMessageText", {
                    "chat_id": msg.get("chat", {}).get("id"),
                    "message_id": msg.get("message_id"),
                    "text": (msg.get("text") or "") + "\n\n*%s*" % mark,
                    "parse_mode": "Markdown", "disable_web_page_preview": True})
            except Exception:
                pass
            got += 1
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--run")
    a = ap.parse_args()
    if a.send:
        import glob
        p = a.run or sorted(glob.glob(os.path.join(RUNS, "*.jsonl")))[-1]
        print("sent %d cards from %s" % (send_run(p), os.path.basename(p)))
    if a.collect:
        while True:
            n = collect()
            print("collected %d verdict(s)" % n)
            if not a.watch:
                break
            time.sleep(10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
