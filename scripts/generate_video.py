#!/usr/bin/env python3
"""generate_video.py — thedoghabit.com YouTube Shorts pipeline.

Uzima najstariji objavljeni WP post koji još nema video (state u videos.json),
generira kratku video skriptu preko Lumenta internog endpointa (tool
'short_video_script' — mora postojati na Lumenta strani), voiceover preko
edge-tts (besplatan, bez API ključa; word timestampovi iz istog poziva postaju
titlovi), vertikalne slike preko Leonardo API-ja, i montira 9:16 MP4 (1080x1920,
Ken Burns zoom + hardcoded titlovi) preko ffmpeg-a.

Rezultat: videos/<slug>/<slug>.mp4 + <slug>.json (YT naslov/opis/tagovi za
ručni upload). --upload šalje video na YouTube preko Data API v3 (multipart
upload, OAuth refresh token iz .env-a) — po defaultu kao 'private' dok
kvaliteta nije potvrđena (YT_PRIVACY=public za javno).

Jednokratni OAuth setup za upload: --yt-auth ispiše URL za browser, uhvati
redirect na 127.0.0.1:8765 i ispiše YT_REFRESH_TOKEN za .env. Prije toga u
Google Cloud konzoli treba OAuth client (Desktop app) s YouTube Data API v3;
dok aplikacija nije verificirana, uploadi preko API-ja ostaju locked private.

--dry-run: mock post + mock skripta + placeholder slike, ali PRAVI edge-tts i
ffmpeg — proizvede stvarni MP4 za provjeru montaže bez ijednog API ključa.

Vanjske ovisnosti: Pillow (već na hostu), edge-tts (pip install edge-tts),
ffmpeg + ffprobe na PATH-u (ili FFMPEG_BIN/FFPROBE_BIN u .env-u).
"""
import argparse
import asyncio
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from PIL import Image

PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_DIR / ".env"
STATE_PATH = PROJECT_DIR / "videos.json"
OUTPUT_DIR = PROJECT_DIR / "videos"
PLACEHOLDER_IMAGE = PROJECT_DIR / "assets" / "placeholder-dog.jpg"
LOG_PATH = PROJECT_DIR / "logs" / "generate_video.log"

LEONARDO_STYLE_ID = "111dc692-d470-4eec-b791-3475abac4c46"

# Rotacija pasmina: jedan video = jedna pasmina (konzistentna kroz sve kadrove),
# sljedeći video sljedeća pasmina — redoslijed određuje broj već obrađenih
# videa u videos.json. Opisi uključuju boju dlake radi konzistentnih slika.
VIDEO_BREEDS = [
    "golden retriever with a light golden coat",
    "black and white border collie",
    "chocolate brown labrador retriever",
    "french bulldog with a fawn coat",
    "german shepherd with a classic black and tan coat",
    "tricolor beagle",
    "pembroke welsh corgi with a red and white coat",
    "australian shepherd with a blue merle coat",
    "small white and tan smooth-coat jack russell terrier",
    "dachshund with a smooth red coat",
    "siberian husky with a grey and white coat and blue eyes",
    "cavalier king charles spaniel with a chestnut and white coat",
]
JACK_RUSSELL_BREED = "small white and tan smooth-coat jack russell terrier"

VIDEO_W, VIDEO_H = 1080, 1920
FPS = 30
TARGET_SECONDS = 55  # ciljna duljina naracije (Shorts limit je 3 min, sweet spot <60 s)

DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) thedoghabit-generate-video/1.0"


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


def http_request(method, url, headers=None, data=None, timeout=30):
    merged_headers = {"User-Agent": DEFAULT_USER_AGENT, **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=merged_headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return resp.status, body


def send_alert(msg, env):
    """Best-effort Telegram alert — nikad ne smije srušiti pipeline."""
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        payload = json.dumps({"chat_id": chat_id, "text": f"[thedoghabit-video] {msg}"}).encode()
        http_request("POST", f"https://api.telegram.org/bot{token}/sendMessage",
                     headers={"Content-Type": "application/json"}, data=payload, timeout=10)
    except Exception as e:
        log(f"  Telegram alert nije poslan: {e}")


def retry(fn, attempts=3, base_delay=2, what=""):
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            detail = e.read().decode(errors="replace") if isinstance(e, urllib.error.HTTPError) else str(e)
            log(f"  pokušaj {attempt}/{attempts} za '{what}' nije uspio: {detail}")
            if attempt < attempts:
                time.sleep(base_delay * attempt)
    raise RuntimeError(f"'{what}' nije uspio nakon {attempts} pokušaja: {last_err}")


def run_cmd(cmd, cwd, what):
    """Pokreni vanjski alat (ffmpeg/ffprobe); na grešci digni s repom stderr-a."""
    # stdin=DEVNULL: ffmpeg bez toga čita stdin (interaktivne komande) i zna
    # progutati input pozivatelja kad se skripta vrti kroz pipe/ssh.
    proc = subprocess.run([str(c) for c in cmd], cwd=str(cwd), capture_output=True,
                          stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        tail = proc.stderr.decode(errors="replace")[-800:]
        raise RuntimeError(f"'{what}' nije uspio (exit {proc.returncode}): {tail}")
    return proc.stdout


# ---------------------------------------------------------------------------
# State (videos.json) — koji postovi već imaju video
# ---------------------------------------------------------------------------

def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return []


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


# ---------------------------------------------------------------------------
# WordPress — izvor sadržaja
# ---------------------------------------------------------------------------

def strip_html(html_text, limit=6000):
    text = re.sub(r"<[^>]+>", " ", html_text)
    import html as html_mod
    text = html_mod.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def get_next_post(env, state, wanted_slug=None):
    """Najstariji objavljeni post bez videa (ili točno određeni preko
    --post-slug). Najstariji prvo, da se backlog videa puni istim redom
    kojim je blog rastao."""
    done_slugs = {v["slug"] for v in state}

    def do_call():
        url = (f"{env['WP_URL']}/wp-json/wp/v2/posts?per_page=100&orderby=date&order=asc"
               "&_fields=id,slug,link,title,content,excerpt")
        status, body = http_request("GET", url)
        return json.loads(body)

    posts = retry(do_call, what="WP posts fetch")
    for p in posts:
        if wanted_slug:
            if p["slug"] == wanted_slug:
                return p
            continue
        if p["slug"] not in done_slugs:
            return p
    return None


# ---------------------------------------------------------------------------
# Lumenta — video skripta iz članka
# ---------------------------------------------------------------------------

def pick_breed(post_title, slug, state):
    """JRT hub postovi uvijek dobivaju JRT maskotu (konzistentno s blogom);
    ostali rotiraju kroz VIDEO_BREEDS po broju dosad obrađenih videa."""
    haystack = f"{post_title} {slug}".lower()
    if "jack russell" in haystack or "jack-russell" in haystack:
        return JACK_RUSSELL_BREED
    return VIDEO_BREEDS[len(state) % len(VIDEO_BREEDS)]


def call_lumenta_script(post_title, article_text, breed, env, dry_run):
    """Skripta za Short: segments[] gdje je prvi hook a zadnji CTA, svaki sa
    svojim image_promptom. NAPOMENA: tool 'short_video_script' mora postojati
    na Lumenta internom endpointu (isti auth kao seo_blog_post)."""
    if dry_run:
        log("  [dry-run] koristim mock video skriptu")
        return {
            "yt_title": f"{post_title} #shorts",
            "yt_description": f"Quick practical tips: {post_title}.",
            "yt_tags": ["dogs", "dog training", "puppy tips"],
            "segments": [
                {"text": "Did you know most dogs can learn this in under a week?",
                 "image_prompt": "curious dog tilting head, close-up"},
                {"text": "Start with short sessions. Five minutes, twice a day, beats one long hour.",
                 "image_prompt": "person training a dog in a living room"},
                {"text": "Reward instantly. Timing matters more than the size of the treat.",
                 "image_prompt": "dog receiving a treat from owner's hand"},
                {"text": "Want the full step-by-step plan? Link in the description.",
                 "image_prompt": "happy dog sitting next to owner outdoors"},
            ],
        }

    def do_call():
        payload = json.dumps({
            "tool": "short_video_script",
            "input": {
                "topic": post_title,
                "article_text": article_text,
                "target_seconds": TARGET_SECONDS,
                "audience": "dog owners, global, English-speaking",
                "language": "en",
                "breed": breed,
            },
        }).encode()
        headers = {
            "Authorization": f"Bearer {env['LUMENTA_API_KEY']}",
            "Content-Type": "application/json",
        }
        status, body = http_request("POST", f"{env['LUMENTA_API_URL']}/api/v1/generate",
                                     headers=headers, data=payload, timeout=120)
        return json.loads(body)["output"]

    # Isti široki razmaci kao u generate_post.py — Anthropic 529 prozori
    # znaju trajati više minuta.
    return retry(do_call, attempts=4, base_delay=45, what="Lumenta video script")


# ---------------------------------------------------------------------------
# edge-tts — voiceover + word timestampovi
# ---------------------------------------------------------------------------

def synthesize_voice(text, out_path, env):
    """Vrati listu {start, end, text} po riječi (sekunde). edge-tts šalje
    WordBoundary evente uz audio stream; offset/duration su u 100 ns tickovima."""
    try:
        import edge_tts
    except ImportError:
        raise RuntimeError("edge-tts nije instaliran — pokreni: pip install edge-tts")

    voice = env.get("EDGE_TTS_VOICE") or "en-US-AndrewMultilingualNeural"
    words = []

    async def run():
        # edge-tts >= 7 defaultno šalje samo SentenceBoundary evente —
        # bez ovoga nema word timestampova za titlove.
        comm = edge_tts.Communicate(text, voice, rate="+8%", boundary="WordBoundary")
        with open(out_path, "wb") as f:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    words.append({
                        "start": chunk["offset"] / 1e7,
                        "end": (chunk["offset"] + chunk["duration"]) / 1e7,
                        "text": chunk["text"],
                    })

    asyncio.run(run())
    if not words:
        raise RuntimeError("edge-tts nije vratio word boundary evente — provjeri glas/tekst.")
    return words


def audio_duration(path, env):
    out = run_cmd([env.get("FFPROBE_BIN") or "ffprobe", "-v", "error",
                   "-show_entries", "format=duration",
                   "-of", "default=noprint_wrappers=1:nokey=1", path.name],
                  cwd=path.parent, what="ffprobe duration")
    return float(out.decode().strip())


# ---------------------------------------------------------------------------
# Titlovi (.ass) — grupice do 3 riječi, veliki centrirani caption
# ---------------------------------------------------------------------------

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial,88,&H00FFFFFF,&H00FFFFFF,&H00101010,&H64000000,-1,0,0,0,100,100,0,0,1,7,0,2,60,60,640,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ass_ts(sec):
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def build_ass(words, path):
    chunks = []
    current = []
    for w in words:
        if current and (len(current) >= 3 or w["start"] - current[-1]["end"] > 0.6):
            chunks.append(current)
            current = []
        current.append(w)
    if current:
        chunks.append(current)

    events = []
    for i, ch in enumerate(chunks):
        start = ch[0]["start"]
        end = chunks[i + 1][0]["start"] if i + 1 < len(chunks) else ch[-1]["end"] + 0.4
        text = " ".join(w["text"] for w in ch).upper()
        events.append(f"Dialogue: 0,{ass_ts(start)},{ass_ts(end)},Cap,,0,0,0,,{text}")

    path.write_text(ASS_HEADER + "\n".join(events) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Leonardo — vertikalne slike po segmentu
# ---------------------------------------------------------------------------

def call_leonardo_image(prompt, env, dry_run):
    if dry_run:
        log("  [dry-run] koristim placeholder sliku umjesto Leonardo API-ja")
        return PLACEHOLDER_IMAGE.read_bytes()

    def do_call():
        headers = {
            "Authorization": f"Bearer {env['LEONARDO_API_KEY']}",
            "Content-Type": "application/json",
        }
        payload = json.dumps({
            "model": "lucid-origin",
            "public": True,
            "parameters": {
                "prompt": (f"{prompt}, photorealistic, natural lighting, "
                           "vertical composition, no text, no watermark"),
                "quantity": 1,
                "width": 768,
                "height": 1344,
                "prompt_enhance": "OFF",
                "style_ids": [LEONARDO_STYLE_ID],
            },
        }).encode()
        # v2 create + v1 poll — isti pattern kao generate_post.py.
        _, body = http_request("POST", "https://cloud.leonardo.ai/api/rest/v2/generations",
                                headers=headers, data=payload)
        generation_id = json.loads(body)["generate"]["generationId"]

        status_url = f"https://cloud.leonardo.ai/api/rest/v1/generations/{generation_id}"
        for _ in range(30):
            time.sleep(4)
            _, poll_body = http_request("GET", status_url, headers=headers)
            record = json.loads(poll_body)["generations_by_pk"]
            if record["status"] == "COMPLETE":
                image_url = record["generated_images"][0]["url"]
                _, img_bytes = http_request("GET", image_url)
                return img_bytes
            if record["status"] == "FAILED":
                raise RuntimeError(f"Leonardo generacija {generation_id} je FAILED")
        raise RuntimeError(f"Leonardo generacija {generation_id} nije završila na vrijeme")

    return retry(do_call, what="Leonardo image generation")


def process_image_vertical(image_bytes):
    """Center-crop na 9:16 pa resize na 1080x1920 — Leonardo i placeholder
    dolaze u raznim omjerima, ffmpeg-u treba konzistentan input."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = img.size
    target_ratio = VIDEO_W / VIDEO_H

    current_ratio = width / height
    if current_ratio > target_ratio:
        new_width = round(height * target_ratio)
        left = (width - new_width) // 2
        img = img.crop((left, 0, left + new_width, height))
    elif current_ratio < target_ratio:
        new_height = round(width / target_ratio)
        top = (height - new_height) // 2
        img = img.crop((0, top, width, top + new_height))

    img = img.resize((VIDEO_W, VIDEO_H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# ffmpeg montaža
# ---------------------------------------------------------------------------

def allocate_durations(segments, total):
    """Trajanje segmenta proporcionalno duljini teksta (aproksimacija tempu
    naracije), minimalno 1.5 s, skalirano natrag na ukupno trajanje."""
    weights = [max(len(s["text"]), 1) for s in segments]
    wsum = sum(weights)
    durs = [max(total * w / wsum, 1.5) for w in weights]
    scale = total / sum(durs)
    return [d * scale for d in durs]


def render_segment(image_name, duration, out_name, workdir, env):
    frames = max(int(round(duration * FPS)), FPS)
    # Anti-jitter kombinacija: zoom kao linearna funkcija framea ('1+k*on'
    # umjesto 'zoom+k' — akumulacija sa zaokruživanjem po frameu vidljivo
    # trese sliku) + zoompan renderiran u 2x rezoluciji pa downscale, da se
    # subpixel greška ispegla.
    vf = (f"scale={VIDEO_W * 2}:{VIDEO_H * 2},"
          f"zoompan=z='min(1+0.0008*on,1.20)'"
          f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
          f":d={frames}:s={VIDEO_W * 2}x{VIDEO_H * 2}:fps={FPS},"
          f"scale={VIDEO_W}:{VIDEO_H}")
    run_cmd([env.get("FFMPEG_BIN") or "ffmpeg", "-y", "-i", image_name,
             "-vf", vf, "-frames:v", frames,
             "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
             out_name],
            cwd=workdir, what=f"ffmpeg segment {out_name}")


def assemble_video(segment_names, workdir, slug, env):
    """Concat segmenata + audio + burn-in titlovi u finalni MP4. ffmpeg se
    vrti s cwd=workdir i relativnim imenima — apsolutni Windows putevi u
    ass= filteru zahtijevaju escapanje dvotočke i backslasheva."""
    ffmpeg = env.get("FFMPEG_BIN") or "ffmpeg"

    concat_list = workdir / "concat.txt"
    concat_list.write_text("".join(f"file '{n}'\n" for n in segment_names))
    run_cmd([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", "concat.txt",
             "-c", "copy", "concat.mp4"],
            cwd=workdir, what="ffmpeg concat")

    final_name = f"{slug}.mp4"
    run_cmd([ffmpeg, "-y", "-i", "concat.mp4", "-i", "voice.mp3",
             "-vf", "ass=subs.ass",
             "-map", "0:v", "-map", "1:a",
             "-c:v", "libx264", "-preset", "medium", "-crf", "21",
             "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart",
             final_name],
            cwd=workdir, what="ffmpeg final render")
    return workdir / final_name


# ---------------------------------------------------------------------------
# YouTube Data API v3 — OAuth + upload
# ---------------------------------------------------------------------------

YT_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YT_REDIRECT = "http://127.0.0.1:8765"


def yt_auth(env):
    """Jednokratni interaktivni OAuth flow: ispiše URL, čeka redirect na
    127.0.0.1:8765, zamijeni code za refresh token i ispiše ga za .env.
    Pokreće se lokalno (treba browser), ne na VPS-u."""
    client_id = env.get("YT_CLIENT_ID")
    client_secret = env.get("YT_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("Postavi YT_CLIENT_ID i YT_CLIENT_SECRET u .env prije --yt-auth "
              "(Google Cloud konzola → OAuth client, tip 'Desktop app', "
              "s uključenim YouTube Data API v3).")
        sys.exit(1)

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": YT_REDIRECT,
        "response_type": "code",
        "scope": YT_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    })
    print("\nOtvori u browseru i odobri pristup:\n\n" + auth_url + "\n")
    print("Čekam redirect na 127.0.0.1:8765 ...")

    from http.server import BaseHTTPRequestHandler, HTTPServer
    captured = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            captured["code"] = (qs.get("code") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("Gotovo — možeš zatvoriti ovaj tab.".encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 8765), Handler)
    while "code" not in captured:
        server.handle_request()
    server.server_close()

    if not captured["code"]:
        print("Nisam dobio authorization code — pokušaj ponovno.")
        sys.exit(1)

    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": captured["code"],
        "grant_type": "authorization_code",
        "redirect_uri": YT_REDIRECT,
    }).encode()
    _, body = http_request("POST", "https://oauth2.googleapis.com/token",
                           headers={"Content-Type": "application/x-www-form-urlencoded"},
                           data=data)
    token = json.loads(body)
    refresh = token.get("refresh_token")
    if not refresh:
        print(f"Google nije vratio refresh_token (odgovor: {token}) — "
              "revokiraj pristup na myaccount.google.com/permissions i ponovi.")
        sys.exit(1)
    print("\nDodaj u .env:\n\nYT_REFRESH_TOKEN=" + refresh + "\n")


def yt_access_token(env):
    def do_call():
        data = urllib.parse.urlencode({
            "client_id": env["YT_CLIENT_ID"],
            "client_secret": env["YT_CLIENT_SECRET"],
            "refresh_token": env["YT_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        }).encode()
        _, body = http_request("POST", "https://oauth2.googleapis.com/token",
                               headers={"Content-Type": "application/x-www-form-urlencoded"},
                               data=data)
        return json.loads(body)["access_token"]

    return retry(do_call, what="YT token refresh")


def yt_upload(video_path, meta, env):
    token = yt_access_token(env)
    # YT naslov: max 100 znakova, < i > nisu dozvoljeni.
    title = re.sub(r"[<>]", "", meta["yt_title"])[:100]
    snippet = json.dumps({
        "snippet": {
            "title": title,
            "description": meta["yt_description"],
            "tags": meta.get("yt_tags") or [],
            "categoryId": "15",  # Pets & Animals
        },
        "status": {
            "privacyStatus": env.get("YT_PRIVACY") or "private",
            "selfDeclaredMadeForKids": False,
        },
    }, ensure_ascii=False).encode()

    boundary = f"thedoghabit-{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
        + snippet
        + f"\r\n--{boundary}\r\nContent-Type: video/mp4\r\n\r\n".encode()
        + video_path.read_bytes()
        + f"\r\n--{boundary}--\r\n".encode()
    )

    def do_call():
        url = ("https://www.googleapis.com/upload/youtube/v3/videos"
               "?uploadType=multipart&part=snippet,status")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        }
        _, resp = http_request("POST", url, headers=headers, data=body, timeout=600)
        return json.loads(resp)["id"]

    return retry(do_call, what="YouTube upload")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

DRY_RUN_POST = {
    "id": 0,
    "slug": "dry-run-video",
    "link": "https://thedoghabit.com/",
    "title": {"rendered": "How to Teach a Dog to Sit and Stay"},
    "content": {"rendered": "<p>Dry-run mock content.</p>"},
}


def main():
    parser = argparse.ArgumentParser(description="Generiraj YouTube Short iz thedoghabit.com posta")
    parser.add_argument("--dry-run", action="store_true",
                        help="mock post/skripta/slike, pravi TTS i ffmpeg — bez API ključeva")
    parser.add_argument("--post-slug", help="obradi točno ovaj WP slug (i ako već ima video)")
    parser.add_argument("--upload", action="store_true",
                        help="uploadaj na YouTube (YT_* varovi u .env-u; default privacy 'private')")
    parser.add_argument("--yt-auth", action="store_true",
                        help="jednokratni OAuth flow — ispiše YT_REFRESH_TOKEN za .env")
    args = parser.parse_args()

    env = load_env(ENV_PATH)

    if args.yt_auth:
        yt_auth(env)
        return

    state = load_state()

    if args.dry_run:
        post = DRY_RUN_POST
    else:
        post = get_next_post(env, state, wanted_slug=args.post_slug)
    if not post:
        log("Nema objavljenih postova bez videa — ništa za napraviti.")
        return

    post_title = strip_html(post["title"]["rendered"], limit=200)
    slug = post["slug"]
    log(f"Odabran post: '{post_title}' (slug={slug})")

    workdir = OUTPUT_DIR / slug
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        article_text = strip_html(post["content"]["rendered"])
        breed = pick_breed(post_title, slug, state)
        log(f"  pasmina za ovaj video: {breed}")
        script = call_lumenta_script(post_title, article_text, breed, env, args.dry_run)
        segments = script["segments"]
        if not segments:
            raise RuntimeError("Lumenta skripta nema segmenata.")
        # Cijela skripta (s image promptovima) uz video — bez ovoga se promptovi
        # gube pa se slike ne mogu ciljano popraviti/regenerirati naknadno.
        (workdir / "script.json").write_text(
            json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        log(f"  skripta: {len(segments)} segmenata")

        narration = " ".join(s["text"].strip() for s in segments)
        words = synthesize_voice(narration, workdir / "voice.mp3", env)
        total_audio = audio_duration(workdir / "voice.mp3", env)
        log(f"  voiceover: {total_audio:.1f} s, {len(words)} riječi")

        build_ass(words, workdir / "subs.ass")

        segment_names = []
        durations = allocate_durations(segments, total_audio + 0.5)
        for i, (seg, dur) in enumerate(zip(segments, durations)):
            img_bytes = call_leonardo_image(seg["image_prompt"], env, args.dry_run)
            img_name = f"img_{i}.jpg"
            (workdir / img_name).write_bytes(process_image_vertical(img_bytes))
            seg_name = f"seg_{i}.mp4"
            render_segment(img_name, dur, seg_name, workdir, env)
            segment_names.append(seg_name)
            log(f"  segment {i + 1}/{len(segments)}: {dur:.1f} s")

        final_path = assemble_video(segment_names, workdir, slug, env)
        size_mb = final_path.stat().st_size / 1024 / 1024
        log(f"Video renderiran: {final_path} ({size_mb:.1f} MB)")

        description = (script["yt_description"].strip()
                       + f"\n\nRead the full guide: {post['link']}"
                       + "\n\n#shorts #dogs #dogtraining")
        meta = {
            "post_id": post["id"],
            "slug": slug,
            "yt_title": script["yt_title"],
            "yt_description": description,
            "yt_tags": script.get("yt_tags") or [],
            "video": final_path.name,
        }
        (workdir / f"{slug}.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        youtube_id = None
        if args.upload and not args.dry_run:
            youtube_id = yt_upload(final_path, meta, env)
            log(f"Uploadano na YouTube: https://youtu.be/{youtube_id} "
                f"(privacy={env.get('YT_PRIVACY') or 'private'})")

    except Exception as e:
        log(f"GREŠKA — video NIJE dovršen: {e}")
        send_alert(f"GREŠKA — video NIJE dovršen ('{post_title}'): {e}", env)
        sys.exit(1)

    if args.dry_run:
        log("[dry-run] state NIJE ažuriran — videos.json netaknut.")
        return

    state = [v for v in state if v["slug"] != slug]
    state.append({
        "slug": slug,
        "post_id": post["id"],
        "status": "uploaded" if youtube_id else "rendered",
        "video": str(final_path.relative_to(PROJECT_DIR)),
        "youtube_id": youtube_id,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    save_state(state)
    send_alert(f"Video spreman: '{post_title}' → {final_path.name}"
               + (f" (YT: https://youtu.be/{youtube_id})" if youtube_id else " (čeka ručni upload)"),
               env)
    log("State ažuriran u videos.json.")


if __name__ == "__main__":
    main()
