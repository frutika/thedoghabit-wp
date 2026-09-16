#!/usr/bin/env python3
"""check_video_silence.py — alarm ako nijedan video nije uspješno uploadan
zadnjih 48h, bez obzira na uzrok.

generate_video.py već šalje Telegram alert na SVAKI pad (send_alert u
--upload putanji), ali to je po-pokušaju signal — netko mora zbrajati te
poruke da primijeti da je tišina predugo trajala. Ovaj skript umjesto toga
gleda unatrag kroz videos.json i javlja samo kad prag tišine bude probijen,
neovisno o tome je li uzrok Pexels, Anthropic, YouTube upload, ili nešto
nepredviđeno — upravo ta zadnja kategorija je razlog zašto postoji (tjedan
dana tišine u rujnu 2026, otkriveno slučajno, ne ovim alarmom).

Namjerno POSEBAN skript i poseban cron unos, ne dio generate_video.py-a: ako
generate_video.py ikad postane potpuno neupotrebljiv (npr. syntax/import
greška prije nego stigne do svog vlastitog send_alert poziva), ovaj i dalje
radi jer ne ovisi o ijednoj njegovoj funkciji, samo o videos.json state
fileu koji generate_video.py piše.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_DIR / ".env"
STATE_PATH = PROJECT_DIR / "videos.json"
LOG_PATH = PROJECT_DIR / "logs" / "check_video_silence.log"

SILENCE_THRESHOLD_HOURS = 48


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def load_env(path):
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def send_alert(msg, env):
    """Best-effort Telegram alert — isti obrazac kao generate_video.py's send_alert."""
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("  Telegram nije konfiguriran (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID) — alert preskočen.")
        return
    try:
        payload = json.dumps({"chat_id": chat_id, "text": f"[thedoghabit-video] {msg}"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        log(f"  Telegram alert nije poslan: {e}")


def last_successful_upload():
    """Najnoviji videos.json zapis sa status=='uploaded', po 'created' polju.

    Vraća None ako fajl ne postoji ili nema nijedan uploadan zapis (npr. na
    posve novoj instalaciji) — main() to tretira kao "ne mogu procijeniti",
    NE kao "48h+ tišina", da prazna/nova instalacija ne pošalje lažan alarm
    prije nego ijedan video uopće postoji.
    """
    if not STATE_PATH.exists():
        return None
    entries = json.loads(STATE_PATH.read_text())
    uploaded = [e for e in entries if e.get("status") == "uploaded" and e.get("created")]
    if not uploaded:
        return None
    latest = max(uploaded, key=lambda e: e["created"])
    return datetime.strptime(latest["created"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def main():
    env = load_env(ENV_PATH)
    last = last_successful_upload()

    if last is None:
        log("Nema nijednog zapisa sa status=='uploaded' u videos.json — ne mogu izračunati tišinu.")
        return 0

    hours_silent = (datetime.now(timezone.utc) - last).total_seconds() / 3600
    log(f"Zadnji uspješan upload: {last.isoformat()} ({hours_silent:.1f}h unatrag).")

    if hours_silent >= SILENCE_THRESHOLD_HOURS:
        send_alert(
            f"UPOZORENJE — nema uspješnog video uploada {hours_silent:.0f}h "
            f"(zadnji: {last.strftime('%Y-%m-%d %H:%M')} UTC). Provjeri generate_video_cron.log.",
            env,
        )
        log("  Alarm poslan (tišina >= threshold).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
