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

POZNATO OGRANIČENJE, namjerno neriješeno ovdje: ovaj watchdog dijeli
Telegram bot/kanal s onim što nadzire (isti send_alert obrazac), i sam je
lokalni cron proces koji može tiho prestati raditi (reboot, cron nije
reloadan, promijenjen python path, neuhvaćena iznimka) bez da to itko
primijeti — uklj. slučaj kad je cijeli VPS mrtav, gdje nijedna lokalna
skripta ništa ne može javiti. Pravo rješenje je vanjski dead man's switch
(npr. healthchecks.io ping nakon svakog uspješnog uploada) koji sam
nadzire odsutnost pinga, neovisno o ovom serveru i ovom Telegram kanalu.
Ovaj skript je namjerno privremeni korak prema tome, ne zamjena za to.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_DIR / ".env"
STATE_PATH = PROJECT_DIR / "videos.json"
ALERT_STATE_PATH = PROJECT_DIR / "silence_alarm_state.json"
LOG_PATH = PROJECT_DIR / "logs" / "check_video_silence.log"

SILENCE_THRESHOLD_HOURS = 48
RE_ALERT_AFTER_HOURS = 24  # nakon prvog alarma, ponovi tek dnevno dok traje


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


def send_telegram(msg, env):
    """Šalje Telegram alert, vraća je li stvarno isporučen.

    NIJE tiho best-effort: poziva se iz maybe_alert(), koji smije upisati
    "alarm poslan" u alert-state (i time ušutkati sljedećih
    RE_ALERT_AFTER_HOURS) SAMO ako je poruka stvarno otišla. Da ova
    funkcija guta grešku bez povratne vrijednosti, watchdog bi na pokvaren
    token/kanal trajno prešutio samog sebe — isti kvar zbog kojeg
    postoji, samo sad proizveden vlastitim dedup mehanizmom.
    """
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log("  Telegram nije konfiguriran (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID) — alert preskočen.")
        return False
    try:
        payload = json.dumps({"chat_id": chat_id, "text": f"[thedoghabit-video] {msg}"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        log(f"  Telegram alert nije poslan: {e}")
        return False


def last_successful_upload():
    """Najnoviji videos.json zapis sa status=='uploaded', po 'created' polju.

    Vraća (datetime, None) na uspjeh, ili (None, razlog) kad se stanje ne
    može pouzdano odrediti — nedostajući fajl, neispravan JSON, ili fajl bez
    ijednog uploadanog zapisa. Sve tri se tretiraju kao alarm-vrijedne, ne
    kao tih izlaz: na ovoj (već uhodanoj, ne novoj) instalaciji nijedna od
    njih ne znači "sve u redu", nego da nešto sprječava i samo praćenje.
    """
    if not STATE_PATH.exists():
        return None, f"{STATE_PATH.name} ne postoji"
    try:
        entries = json.loads(STATE_PATH.read_text())
    except json.JSONDecodeError as e:
        return None, f"{STATE_PATH.name} nije valjan JSON ({e})"
    if not isinstance(entries, list):
        return None, f"{STATE_PATH.name} nema očekivani oblik (lista zapisa)"
    uploaded = [e for e in entries if e.get("status") == "uploaded" and e.get("created")]
    if not uploaded:
        return None, f"{STATE_PATH.name} nema nijedan zapis sa status=='uploaded'"
    latest = max(uploaded, key=lambda e: e["created"])
    try:
        return datetime.strptime(latest["created"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc), None
    except ValueError as e:
        return None, f"najnoviji 'created' zapis se ne parsira ({e})"


def load_alert_state():
    if not ALERT_STATE_PATH.exists():
        return None
    try:
        data = json.loads(ALERT_STATE_PATH.read_text())
        return datetime.fromisoformat(data["last_alert_sent_at"])
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        log(f"  silence_alarm_state.json nije čitljiv ({e}) — tretiram kao da alarm još nije poslan.")
        return None


def save_alert_state(when):
    ALERT_STATE_PATH.write_text(json.dumps({"last_alert_sent_at": when.isoformat()}))


def clear_alert_state():
    if ALERT_STATE_PATH.exists():
        ALERT_STATE_PATH.unlink()
        log("  Stanje oporavljeno — silence_alarm_state.json obrisan, sljedeća epizoda kreće od 48h praga.")


def maybe_alert(message, env):
    """Šalje Telegram alert samo ako nije već poslan u zadnjih RE_ALERT_AFTER_HOURS
    — bez ovoga, svakih 6h dok traje tišina znači 4 alerta dnevno unedogled,
    što je točno obrazac koji vodi do utišavanja kanala (isti onaj koji je
    tjedan dana tišine prošao neopaženo)."""
    now = datetime.now(timezone.utc)
    last_alert = load_alert_state()
    if last_alert is not None and (now - last_alert) < timedelta(hours=RE_ALERT_AFTER_HOURS):
        log(f"  Alarm bi trebao ići, ali zadnji je poslan prije {(now - last_alert).total_seconds() / 3600:.1f}h — preskačem (re-alert tek nakon {RE_ALERT_AFTER_HOURS}h).")
        return
    if send_telegram(message, env):
        save_alert_state(now)
        log("  Alarm poslan.")
    else:
        log("  Alarm NIJE poslan — stanje nije spremljeno, idući pokušaj za 6h (sljedeći cron run), ne za 24h.")


def announce_recovery(last, hours_silent, env):
    """Javi da je pipeline opet uploadao, ako je za tu epizodu alarm ikad poslan.

    Bez ovoga alarm je jednosmjeran: upozorenje stigne u 06:00, popravak se
    dogodi u 12:34, a onaj tko čita alarm ne zna da je već neistinit (2026-09-18
    upravo to). Poruka o oporavku razlikuje alarm kojem se vjeruje od onoga koji
    se nauči ignorirati.

    Stanje se briše SAMO ako je poruka stvarno otišla — isto pravilo kao u
    maybe_alert(): ako send_telegram() padne, stanje ostaje pa idući cron run
    (za 6h) pokuša ponovno, umjesto da se oporavak tiho proguta.
    Bez postojećeg stanja (alarm nikad nije poslan) nema što opozvati, pa se
    ne šalje ništa.
    """
    if not ALERT_STATE_PATH.exists():
        return
    last_alert = load_alert_state()
    alert_part = (
        f" Alarm poslan {last_alert.strftime('%Y-%m-%d %H:%M')} UTC više ne vrijedi."
        if last_alert is not None else " Prethodni alarm više ne vrijedi."
    )
    msg = (
        f"OPORAVAK — video upload opet radi. Zadnji uspješan upload: "
        f"{last.strftime('%Y-%m-%d %H:%M')} UTC ({hours_silent:.1f}h unatrag).{alert_part}"
    )
    if send_telegram(msg, env):
        log("  Oporavak javljen.")
        clear_alert_state()
    else:
        log("  Oporavak NIJE javljen — stanje zadržano, idući pokušaj za 6h (sljedeći cron run).")


def main():
    env = load_env(ENV_PATH)
    last, unknown_reason = last_successful_upload()

    if last is None:
        log(f"Ne mogu odrediti zadnji uspješan upload: {unknown_reason}.")
        maybe_alert(
            f"UPOZORENJE — ne mogu odrediti stanje video pipelinea ({unknown_reason}). "
            f"Ovo samo po sebi treba provjeru, neovisno od stvarnog stanja pipelinea.",
            env,
        )
        return 0

    hours_silent = (datetime.now(timezone.utc) - last).total_seconds() / 3600
    log(f"Zadnji uspješan upload: {last.isoformat()} ({hours_silent:.1f}h unatrag).")

    if hours_silent >= SILENCE_THRESHOLD_HOURS:
        maybe_alert(
            f"UPOZORENJE — nema uspješnog video uploada {hours_silent:.0f}h "
            f"(zadnji: {last.strftime('%Y-%m-%d %H:%M')} UTC). Provjeri generate_video_cron.log.",
            env,
        )
    else:
        announce_recovery(last, hours_silent, env)

    return 0


if __name__ == "__main__":
    sys.exit(main())
