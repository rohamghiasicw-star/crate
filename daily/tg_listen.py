#!/usr/bin/env python3
"""The always-on ear. Long-polls Telegram, takes taps AND free text, never sleeps.

Run under launchd, not by hand:
    launchctl kickstart -k gui/$(id -u)/com.rohamghiasi.addify.tglisten

WHY THIS RUNS ON THE MAC AND NOT ON GITHUB. Roham asked for GitHub minutes if possible. It
is not possible, and the arithmetic is the reason rather than a preference. A workflow
cannot hold a connection open, so "always listening" on Actions means a cron, and Actions
bills a MINIMUM OF ONE MINUTE PER RUN. Polling every 5 minutes is 288 runs a day, which is
288 minutes a day, which is 8,640 a month against a 2,000 minute free allowance. Even hourly
polling is 720 a month, over a third of the allowance, to read a chat. His account has
already been through this once: a runaway watcher burned the whole 2,000 on 2026-08-06 and
killed two bots that are still dead.

Long polling from this Mac costs nothing. getUpdates with timeout=50 holds one idle
connection, so the steady state is no CPU and no requests-per-second. Telegram keeps
undelivered updates for 24 hours and the offset is persisted here, so anything he taps or
types while the laptop is shut still arrives when it opens. GitHub keeps the one job it is
actually good at, the once-a-day dead man switch.

TWO KINDS OF INPUT, ONE OF THEM EXACT:
  * A button tap carries the clip id in its callback data. Unambiguous, written straight
    through as a verdict.
  * Free text is the thing he actually asked for: reply from bed without aiming. If he
    SWIPE-REPLIES to a card, Telegram hands us the message id of that card and we map it
    back to the exact clip through tg_sent.jsonl, so a reply is just as exact as a tap.
    Loose text with no reply lands in the inbox tagged with the most recent ungraded card
    as a guess, and a guess is never promoted to truth here - eval/harvest.py established
    that boundary for Konnor's texts and it holds for these.

Everything written from here is `state: proposed`. A machine deciding which clip a sentence
was about is a guess, and a guess that silently becomes ground truth would poison the corpus
this exists to fill.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "eval"))
import evallib as EV              # noqa: E402

ENVFILE = os.path.expanduser("~/client-reminders/.telegram_env")
OFFSET = os.path.join(HERE, "tg_offset.json")
SENT = os.path.join(HERE, "tg_sent.jsonl")
INBOX = os.path.join(HERE, "tg_inbox.jsonl")

POLL = 50               # seconds Telegram holds the connection open


def creds():
    env = {}
    for line in open(ENVFILE):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"]


def api(method, payload=None, timeout=POLL + 15):
    tok, _ = creds()
    url = "https://api.telegram.org/bot%s/%s" % (tok, method)
    req = (urllib.request.Request(url) if payload is None else
           urllib.request.Request(url, data=json.dumps(payload).encode(),
                                  headers={"Content-Type": "application/json"}))
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())


def _offset(v=None):
    if v is None:
        try:
            return json.load(open(OFFSET)).get("offset", 0)
        except Exception:
            return 0
    json.dump({"offset": v}, open(OFFSET, "w"))


def _sent_map():
    """message_id -> clip_id, so a swipe-reply binds to the right clip exactly."""
    m = {}
    for r in EV._read(SENT):
        if r.get("message_id") and r.get("clip_id"):
            m[r["message_id"]] = r["clip_id"]
    return m


def _latest_ungraded():
    graded = {v.get("clip_id") for v in EV.load_verdicts()}
    for r in reversed(EV._read(SENT)):
        if r.get("clip_id") and r["clip_id"] not in graded:
            return r["clip_id"]
    return None


# Only the unmistakable shapes are auto-read. Anything else goes to the inbox verbatim for
# a human pass. Guessing harder here buys nothing and risks a wrong label.
_YES = re.compile(r"^\s*(y|yes|yep|yeah|right|correct|nailed it|thats it|that's it|👍|✅)\s*[.!]*\s*$", re.I)
_NO = re.compile(r"^\s*(n|no|nope|wrong|nah|incorrect|👎|❌)\s*[.!]*\s*$", re.I)
_EDIT = re.compile(r"(right song.*wrong edit|wrong edit|not this edit|wrong version|different edit)", re.I)


def classify(text):
    if _EDIT.search(text or ""):
        return "wrong_transform"
    if _YES.match(text or ""):
        return "right"
    if _NO.match(text or ""):
        return "wrong"
    return None


def write_verdict(cid, verdict, text=None, channel="telegram"):
    EV.append(EV.VERDICTS, {
        "verdict_id": EV.vid(cid, verdict, str(time.time())),
        "clip_id": cid, "verdict": verdict, "by": "roham", "channel": channel,
        "state": "proposed", "note": (text or "")[:400], "when": int(time.time())})


def handle_callback(cb):
    parts = (cb.get("data") or "").split("|")
    if len(parts) != 3 or parts[0] != "v":
        return
    _, cid, verdict = parts
    api("answerCallbackQuery", {"callback_query_id": cb["id"],
                                "text": "logged: %s" % verdict}, timeout=20)
    if verdict != "skip":
        write_verdict(cid, verdict)
    msg = cb.get("message") or {}
    mark = {"right": "✅ RIGHT", "wrong": "❌ WRONG",
            "wrong_transform": "🎛 RIGHT SONG, WRONG EDIT", "skip": "🤷 skipped"}[verdict]
    try:
        api("editMessageText", {"chat_id": (msg.get("chat") or {}).get("id"),
                                "message_id": msg.get("message_id"),
                                "text": (msg.get("text") or "") + "\n\n*%s*" % mark,
                                "parse_mode": "Markdown",
                                "disable_web_page_preview": True}, timeout=20)
    except Exception:
        pass


def handle_text(msg):
    text = (msg.get("text") or "").strip()
    if not text or text.startswith("/"):
        return
    reply_to = (msg.get("reply_to_message") or {}).get("message_id")
    cid = _sent_map().get(reply_to) if reply_to else None
    guessed = cid is None
    if guessed:
        cid = _latest_ungraded()
    verdict = classify(text)

    EV.append(INBOX, {"text": text, "clip_id": cid, "guessed_clip": guessed,
                      "verdict": verdict, "message_id": msg.get("message_id"),
                      "when": int(time.time()), "state": "proposed"})

    # A clear yes/no aimed at a specific card is safe to record now. Anything vaguer, or
    # aimed at nothing in particular, waits for a human pass over the inbox.
    if cid and verdict and not guessed:
        write_verdict(cid, verdict, text, channel="telegram_reply")
        ack = "logged: %s" % verdict
    elif cid and verdict:
        ack = "read as %s, assumed the last card. Swipe-reply to a card to be exact." % verdict
    else:
        ack = "saved, I will read it properly on the next pass"
    try:
        api("sendMessage", {"chat_id": msg["chat"]["id"], "text": ack,
                            "reply_to_message_id": msg.get("message_id")}, timeout=20)
    except Exception:
        pass


def main():
    print("listening", flush=True)
    backoff = 1
    while True:
        try:
            r = api("getUpdates", {"offset": _offset() + 1, "timeout": POLL, "limit": 50})
            backoff = 1
            for u in r.get("result") or []:
                _offset(u["update_id"])
                try:
                    if u.get("callback_query"):
                        handle_callback(u["callback_query"])
                    elif u.get("message"):
                        handle_text(u["message"])
                except Exception as e:
                    print("handler error: %s" % e, flush=True)
        except urllib.error.HTTPError as e:
            # 409 means something else is also calling getUpdates. Only one consumer is
            # allowed, and losing that race silently would look like a dead bot.
            print("http %s%s" % (e.code, " CONFLICT: another poller is running"
                                 if e.code == 409 else ""), flush=True)
            time.sleep(min(60, backoff)); backoff *= 2
        except Exception as e:
            print("poll error: %s" % e, flush=True)
            time.sleep(min(60, backoff)); backoff *= 2


if __name__ == "__main__":
    sys.exit(main())
