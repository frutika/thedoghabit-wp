#!/usr/bin/env python3
"""generate_video.py — thedoghabit.com YouTube Shorts pipeline.

Uzima najstariji objavljeni WP post koji još nema video (state u videos.json),
generira kratku (~25 s) video skriptu preko Lumenta internog endpointa (tool
'short_video_script' — mora postojati na Lumenta strani), voiceover preko
edge-tts (besplatan, bez API ključa; word timestampovi iz istog poziva postaju
titlovi), vertikalne stock video klipove preko Pexels API-ja (fallback: slike), i montira 9:16 MP4 (1080x1920,
Ken Burns zoom + hardcoded titlovi) preko ffmpeg-a.

Rezultat: videos/<slug>/<slug>.mp4 + <slug>.json (YT naslov/opis/tagovi za
ručni upload). --upload šalje video na YouTube preko Data API v3 (multipart
upload, OAuth refresh token iz .env-a) — po defaultu kao 'private' dok
kvaliteta nije potvrđena (YT_PRIVACY=public za javno).

Jednokratni OAuth setup za upload: --yt-auth ispiše URL za browser, uhvati
redirect na 127.0.0.1:8765 i ispiše YT_REFRESH_TOKEN za .env. Prije toga u
Google Cloud konzoli treba OAuth client (Desktop app) s YouTube Data API v3;
dok aplikacija nije verificirana, uploadi preko API-ja ostaju locked private.

--own-clip <snimka.mp4> --post-slug <slug> --clip-note "<što se vidi>": vlastita,
već izrezana snimka (8-59 s) umjesto Pexelsa. Lumenta piše naraciju za tu snimku
(opis iz --clip-note, broj riječi iz trajanja), edge-tts glas + titlovi idu preko
nje, originalni zvuk tiho ispod (--clip-audio-volume, 0 = bez). Dalje isto kao
stock: sidecar JSON, --upload, embed na članku; stari Short istog članka ostaje
zapisan kao replaced_youtube_id. Primjer:
  python3 scripts/generate_video.py --own-clip ~/tery-sljunak.mp4 \
    --post-slug why-dog-sniffing-walks --upload \
    --clip-note "Tery, our Jack Russell puppy, first time on a pebble beach, sniffing everything"

--dry-run: mock post + mock skripta + placeholder slike, ali PRAVI edge-tts i
ffmpeg — proizvede stvarni MP4 za provjeru montaže bez ijednog API ključa.

Vanjske ovisnosti: Pillow (već na hostu), edge-tts >= 7.3 (pip install -U edge-tts),
ffmpeg + ffprobe na PATH-u (ili FFMPEG_BIN/FFPROBE_BIN u .env-u).
"""
import argparse
import asyncio
import base64
import io
import json
import os
import re
import shutil
import socket
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
HEARTBEAT_PATH = PROJECT_DIR / "video_heartbeat.json"
OUTPUT_DIR = PROJECT_DIR / "videos"
PLACEHOLDER_IMAGE = PROJECT_DIR / "assets" / "placeholder-dog.jpg"
LOG_PATH = PROJECT_DIR / "logs" / "generate_video.log"

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
# 25 s, ne 55: YT Analytics (28 dana do 2026-09-26) pokazuje prosjek gledanja
# 19-24 s, a pola publike ode do ~12. sekunde. Kraći video = veći postotak
# odgledanog, a to je signal po kojem Shorts feed gura video dalje.
TARGET_SECONDS = 25

# Validacija Lumenta skripte (validate_script) — neovisna o modelu iza Lumente.
# Pragovi slijede iz iste retencije: pola publike ode do ~12. s, cilj je 25 s
# (~2,7 riječi/s izmjereni edge-tts tempo -> ~65 riječi). Skripta koja ih
# prekrši NE ide u render: bolje nijedan video nego 50 s video koji gubi
# publiku (Haiku je davao 117-128 riječi za cilj od 25 s).
SCRIPT_MIN_SEGMENTS = 4
SCRIPT_MAX_SEGMENTS = 6
SCRIPT_MAX_WORDS = 70          # ~26 s naracije; malo zraka iznad 63 riječi koje Lumenta cilja
SCRIPT_MAX_HOOK_WORDS = 10     # hook mora stati u ~2-3 s, prije nego što pola publike ode
SCRIPT_MAX_TITLE_CHARS = 70    # YT Shorts feed reže naslov; kratko pitanje, ne SEO naslov
IMAGE_PROMPT_MAX_WORDS = 6     # Pexels video search: kratki upiti s radnjom vraćaju više pogodaka
# Hashtagovi koje pipeline sam dodaje na kraj opisa — ne smiju se ponoviti.
PIPELINE_HASHTAGS = ["#shorts", "#dogs", "#dogtraining"]
# Imena pasmina koja se miču iz image_prompta (duža imena prvo, da
# "jack russell terrier" ne ostavi "terrier"). Pexels s pasminom u upitu
# često vrati 0 vertikalnih klipova; pas je ionako JRT kroz VIDEO_BREEDS.
BREED_NAMES = [
    "cavalier king charles spaniel", "pembroke welsh corgi", "jack russell terrier",
    "golden retriever", "labrador retriever", "german shepherd", "australian shepherd",
    "border collie", "french bulldog", "siberian husky", "jack russell",
    "retriever", "terrier", "shepherd", "bulldog", "labrador", "corgi", "collie",
    "husky", "beagle", "dachshund", "spaniel", "poodle", "chihuahua", "pug", "jrt",
]
# Riječi bez vrijednosti za pretragu; miču se prije skraćivanja na 6 riječi.
IMAGE_PROMPT_STOPWORDS = {"a", "an", "the", "with", "of", "and", "its", "their", "his", "her"}

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


def _conn_diag(e):
    """Obitelj adresa i adrese veze koja je STVARNO dala ovaj odgovor.

    urllib bira IPv6 ili IPv4 po redoslijedu getaddrinfo (ovdje IPv6 prvi) i
    ta se odluka inače nigdje ne vidi — pitanje "blokira li Cloudflare samo
    IPv6?" bi se moglo odgovoriti tek pokusom, a blok je intermitentan pa se
    ne da izazvati. Ovako sljedeći stvarni blok sam nosi odgovor u logu.
    Čita privatne atribute (fp.fp.raw._sock), zato je omotano: ako se struktura
    promijeni, dijagnostika samo postane "family=?", retry ne smije pasti."""
    try:
        s = e.fp.fp.raw._sock
        fam = "IPv6" if s.family == socket.AF_INET6 else "IPv4"
        return f"family={fam} src={s.getsockname()[0]} dst={s.getpeername()[0]}"
    except Exception:
        return "family=?"


def retry(fn, attempts=3, base_delay=2, what=""):
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            if isinstance(e, urllib.error.HTTPError):
                conn = _conn_diag(e)  # prije e.read(): čita soket odgovora
                detail = e.read().decode(errors="replace")
                # cf-ray identifies which Cloudflare rule fired — without it,
                # a report to a provider's support has nothing to point at.
                # Gated on cf-ray being present so non-Cloudflare hosts
                # (Lumenta, YouTube, WP) don't get a noisy all-None suffix.
                cf_ray = e.headers.get("cf-ray")
                if cf_ray:
                    detail += (
                        f" [cf-ray={cf_ray} mitigated={e.headers.get('cf-mitigated')} "
                        f"ratelimit={e.headers.get('x-ratelimit-remaining')}/{e.headers.get('x-ratelimit-limit')} {conn}]"
                    )
            else:
                detail = str(e)
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
    # Atomski zapis (tmp + os.replace): pad usred pisanja ne smije ostaviti napola
    # napisan videos.json — load_state() ga onda ne bi mogao parsirati i SVAKI
    # sljedeći run bi pao dok ga netko ručno ne popravi.
    tmp = STATE_PATH.with_name(STATE_PATH.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    os.replace(tmp, STATE_PATH)


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
    # Bez --post-slug: strateska selekcija (ne cisti FIFO). Prioritet po vidIQ
    # podacima: 2=sleep cluster (zlatni keyword), 1=behavior-problem (dokazani
    # pobjednici), 0=ostatak backloga. Unutar strateskih tiera najnoviji prvo
    # (svjez sadrzaj van odmah); backlog se drenira najstariji prvo. Parkirani
    # postovi (videos.json status parked) ispadaju kroz done_slugs.
    #
    # 'rendered' (renderiran, nikad uploadan) NIJE gotov: bez ovoga je to bilo stanje
    # bez izlaza — done_slugs ga je trajno isključivao pa video nikad nije izašao
    # (best-reflective-dog-collar-nighttime-safety stajao je mjesec dana). Takav
    # post ide na sam početak reda ("dovrši započeto") i renderira se iznova, pa
    # se stari fajl ne uploada; uspješan run zamjenjuje stari zapis u main().
    done_slugs = {v["slug"] for v in state if v.get("status") != "rendered"}
    rendered_slugs = {v["slug"] for v in state if v.get("status") == "rendered"}

    PER_PAGE = 100  # WP REST gornja granica; više se ne može, pa se straniči

    def fetch_page(page):
        url = (env["WP_URL"] + f"/wp-json/wp/v2/posts?per_page={PER_PAGE}&page={page}"
               "&orderby=date&order=asc&_fields=id,slug,link,title,content,excerpt,categories")
        req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read()), int(resp.headers.get("X-WP-TotalPages") or 0)

    # Bez straničenja skripta je vidjela samo NAJSTARIJIH 100 postova (sortirano
    # asc), pa je svaki post iza stotog bio nevidljiv — ni get_next_post ni
    # --post-slug ga nisu mogli naći, a "Nema objavljenih postova bez videa"
    # izlazi s kodom 0 bez alerta. Otkriveno 2026-09-18 (105 postova, 5 nevidljivih).
    posts, page = [], 1
    while True:
        batch, total_pages = retry(lambda p=page: fetch_page(p),
                                   what=f"WP posts fetch (str. {page})")
        posts += batch
        # Bez X-WP-TotalPages zaglavlja (0) nastavi dok stranica ne bude kraća
        # od PER_PAGE — bolje jedan suvišni poziv nego tiho odsjeći postove.
        more = page < total_pages if total_pages else len(batch) == PER_PAGE
        if not batch or not more:
            break
        page += 1

    if wanted_slug:
        return next((p for p in posts if p["slug"] == wanted_slug), None)

    undone = [p for p in posts if p["slug"] not in done_slugs]

    # Gear postovi ne idu u Shorts: "Best X" videi imaju najgore brojke na
    # kanalu (31-48 pregleda). Samo auto-odabir; --post-slug ih i dalje može
    # eksplicitno tražiti.
    gear_ids = _excluded_category_ids(env)
    if gear_ids:
        before = len(undone)
        undone = [p for p in undone if not set(p.get("categories") or []) & gear_ids]
        if before != len(undone):
            log(f"  preskačem {before - len(undone)} gear postova (VIDEO_EXCLUDE_CATEGORIES)")
    if not undone:
        return None

    resume = [p for p in undone if p["slug"] in rendered_slugs]
    if resume:
        log(f"  nastavljam nedovršen post (status rendered, nije uploadan): {resume[0]['slug']}")
        return resume[0]

    top = max(_post_priority(p) for p in undone)
    if top > 0:
        return [p for p in undone if _post_priority(p) == top][-1]
    return undone[0]


def _excluded_category_ids(env):
    """WP ID-evi kategorija koje ne idu u video (default: gear). Fail-open:
    ako dohvat padne, logira i ne filtrira — bolje gear video nego nijedan."""
    slugs = [s.strip() for s in env.get("VIDEO_EXCLUDE_CATEGORIES", "gear").split(",") if s.strip()]
    if not slugs:
        return set()
    url = env["WP_URL"] + "/wp-json/wp/v2/categories?_fields=id,slug&slug=" + ",".join(slugs)
    try:
        def do_call():
            _, body = http_request("GET", url)
            return json.loads(body)
        return {c["id"] for c in retry(do_call, what="WP categories fetch")}
    except Exception as e:
        log(f"  UPOZORENJE: ne mogu dohvatiti kategorije za isključivanje ({e}) — ne filtriram")
        return set()


_SLEEP_KW = ("sleep",)
_PROBLEM_KW = ("anxiety", "separation", "biting", "nipping", "barking", "aggress",
               "chewing", "digging", "jumping", "whining", "licking", "guarding",
               "counter", "pulling", "not listening", "reactive", "panic", "destructive")


def _post_priority(post):
    text = (post.get("slug", "") + " " + post.get("title", {}).get("rendered", "")).lower()
    if any(k in text for k in _SLEEP_KW):
        return 2
    if (any(k in text for k in _PROBLEM_KW)
            or "why does my dog" in text or "why is my dog" in text
            or "how to stop" in text):
        return 1
    return 0


# ---------------------------------------------------------------------------
# Lumenta — video skripta iz članka
# ---------------------------------------------------------------------------

def pick_breed(post_title, slug, state, env=None):
    """Jack Russell je stalni lik kanala (ista maskota kao na blogu) — rotacija
    pasmina je kanalu oduzimala prepoznatljivost. VIDEO_ROTATE_BREEDS=1 u .env-u
    vraća staru rotaciju kroz VIDEO_BREEDS."""
    if (env or {}).get("VIDEO_ROTATE_BREEDS") == "1":
        haystack = f"{post_title} {slug}".lower()
        if "jack russell" in haystack or "jack-russell" in haystack:
            return JACK_RUSSELL_BREED
        return VIDEO_BREEDS[len(state) % len(VIDEO_BREEDS)]
    return JACK_RUSSELL_BREED


def call_lumenta_script(post_title, article_text, breed, env, dry_run, retry_reason=None,
                        target_seconds=None):
    """Skripta za Short: segments[] gdje je prvi hook a zadnji CTA, svaki sa
    svojim image_promptom. NAPOMENA: tool 'short_video_script' mora postojati
    na Lumenta internom endpointu (isti auth kao seo_blog_post)."""
    if dry_run:
        log("  [dry-run] koristim mock video skriptu")
        return {
            "yt_title": "Your dog can learn this in a week",
            "yt_description": f"Quick practical tips: {post_title}.",
            "yt_tags": ["dogs", "dog training", "puppy tips"],
            "segments": [
                {"text": "Your dog can learn this in a week.",
                 "image_prompt": "curious dog tilting head, close-up"},
                {"text": "Start with short sessions. Five minutes, twice a day, beats one long hour.",
                 "image_prompt": "person training a dog in a living room"},
                {"text": "Reward instantly. Timing matters more than the size of the treat.",
                 "image_prompt": "dog receiving a treat from owner's hand"},
                {"text": "Does your dog do this? Tell me in the comments.",
                 "image_prompt": "happy dog sitting next to owner outdoors"},
            ],
        }

    def do_call():
        payload = json.dumps({
            "tool": "short_video_script",
            "input": {
                "topic": post_title,
                "article_text": article_text,
                "target_seconds": target_seconds or TARGET_SECONDS,
                "audience": "dog owners, global, English-speaking",
                "language": "en",
                "breed": breed,
                # Lumenta nepoznata polja tiho ignorira (bira samo poznata),
                # pa je ovo sigurno; ostaje u payloadu radi traga/buduće podrške.
                **({"retry_reason": retry_reason} if retry_reason else {}),
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
# Validacija skripte — ne ovisi o tome koji model je iza Lumente
# ---------------------------------------------------------------------------

class ScriptValidationError(RuntimeError):
    pass


_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    "\U0000FE0F\U0000200D\U000020E3]+")
_MARKDOWN_RE = re.compile(r"[*_`#]+")


def clean_narration(text):
    """Tekst za TTS: bez markdowna (*them*), emojija i višestrukih razmaka —
    edge-tts bi ih čitao ili bi završili u titlovima."""
    text = _EMOJI_RE.sub("", text)
    text = _MARKDOWN_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


_BREED_RES = [re.compile(r"\b" + re.escape(b) + r"s?\b", re.I)
              for b in sorted(set(VIDEO_BREEDS) | set(BREED_NAMES), key=len, reverse=True)]


def clean_image_prompt(prompt):
    """Pexels upit: bez pasmina (cijeli VIDEO_BREEDS opisi pa imena), bez
    punila, <= IMAGE_PROMPT_MAX_WORDS riječi. Vraća (upit, maknute_pasmine)."""
    removed = []
    for rx in _BREED_RES:
        for m in rx.findall(prompt):
            removed.append(m.strip())
        prompt = rx.sub(" ", prompt)
    words = [w for w in re.sub(r"[^\w\s'-]", " ", prompt).split()
             if w.lower() not in IMAGE_PROMPT_STOPWORDS]
    # Bez imenice psa upit postaje "running in park" — Pexels vrati ljude.
    if not any(w.lower().rstrip("s") in ("dog", "puppy", "puppie", "pup") for w in words):
        words.insert(0, "dog")
    return " ".join(words[:IMAGE_PROMPT_MAX_WORDS]), removed


def count_words(text):
    return len(text.split())


def script_violations(script, max_words=None):
    """Kršenja pragova (lista opisa); prazna lista = skripta prolazi.
    max_words: gornja granica riječi naracije (default SCRIPT_MAX_WORDS); kod
    vlastite snimke ide iz trajanja snimke (own_clip_max_words)."""
    max_words = max_words or SCRIPT_MAX_WORDS
    problems = []
    segments = script.get("segments") or []
    n = len(segments)
    if not SCRIPT_MIN_SEGMENTS <= n <= SCRIPT_MAX_SEGMENTS:
        problems.append(f"{n} segmenata (dozvoljeno {SCRIPT_MIN_SEGMENTS}-{SCRIPT_MAX_SEGMENTS})")
    total = sum(count_words(s.get("text", "")) for s in segments)
    if total > max_words:
        problems.append(f"{total} riječi naracije (max {max_words})")
    if segments:
        hook = count_words(segments[0].get("text", ""))
        if hook > SCRIPT_MAX_HOOK_WORDS:
            problems.append(f"hook ima {hook} riječi (max {SCRIPT_MAX_HOOK_WORDS})")
    title = script.get("yt_title") or ""
    if not title.strip():
        problems.append("prazan yt_title")
    elif len(title) > SCRIPT_MAX_TITLE_CHARS:
        problems.append(f"yt_title ima {len(title)} znakova (max {SCRIPT_MAX_TITLE_CHARS})")
    return problems


def validate_script(script, max_words=None):
    """Čisti skriptu na mjestu i provjerava pragove. Diže ScriptValidationError
    za Lumenta {"error": ...} (bez retryja — isti input daje istu grešku) i
    ValueError s popisom kršenja kad pragovi padnu (pozivatelj radi retry)."""
    if script.get("error"):
        raise ScriptValidationError(f"Lumenta vratila grešku: {script['error']}")

    segments = [s for s in (script.get("segments") or [])
                if isinstance(s, dict) and (s.get("text") or "").strip()]
    for i, seg in enumerate(segments):
        cleaned = clean_narration(seg["text"])
        if cleaned != seg["text"]:
            log(f"  validacija: segment {i + 1} tekst očišćen ({seg['text']!r} -> {cleaned!r})")
            seg["text"] = cleaned
        prompt, removed = clean_image_prompt(seg.get("image_prompt") or "")
        if prompt != (seg.get("image_prompt") or ""):
            log(f"  validacija: segment {i + 1} image_prompt {seg.get('image_prompt')!r} -> {prompt!r}"
                + (f" (maknuto: {', '.join(removed)})" if removed else ""))
            seg["image_prompt"] = prompt
    script["segments"] = segments

    problems = script_violations(script, max_words)
    if problems:
        raise ValueError("; ".join(problems))
    return script


def get_valid_script(post_title, article_text, breed, env, dry_run,
                     max_words=None, target_seconds=None):
    """Lumenta skripta koja prolazi validate_script, uz JEDAN ponovni poziv
    ako pragovi padnu. Drugi pad = ScriptValidationError: predug video se ne
    renderira (main šalje alert i izlazi s 1)."""
    call = lambda reason=None: call_lumenta_script(  # noqa: E731
        post_title, article_text, breed, env, dry_run,
        retry_reason=reason, target_seconds=target_seconds)
    script = call()
    try:
        return validate_script(script, max_words)
    except ValueError as e:
        reason = str(e)
        log(f"  validacija: skripta ne prolazi ({reason}) — jedan ponovni poziv Lumente")
    script = call(reason)
    try:
        return validate_script(script, max_words)
    except ValueError as e:
        raise ScriptValidationError(f"skripta ni u drugom pokušaju ne prolazi validaciju: {e}")


def build_description(yt_description, post_link):
    """Opis za YT: tekst skripte + link + PIPELINE_HASHTAGS, bez ponavljanja
    hashtaga koje skripta već ima (usporedba bez obzira na velika slova)."""
    pipeline = {h.lower() for h in PIPELINE_HASHTAGS}
    seen = set()

    def keep(m):
        tag = m.group(0).lower()
        if tag in pipeline or tag in seen:
            return ""
        seen.add(tag)
        return m.group(0)

    body = re.sub(r"#\w+", keep, yt_description or "")
    body = re.sub(r"[ \t]{2,}", " ", body).strip()
    return f"{body}\n\nRead the full guide: {post_link}\n\n" + " ".join(PIPELINE_HASHTAGS)


# ---------------------------------------------------------------------------
# edge-tts — voiceover + word timestampovi
# ---------------------------------------------------------------------------

EDGE_TTS_MIN_VERSION = (7, 3)
EDGE_TTS_ATTEMPTS = 3


def synthesize_voice(text, out_path, env):
    """Vrati listu {start, end, text} po riječi (sekunde). edge-tts šalje
    WordBoundary evente uz audio stream; offset/duration su u 100 ns tickovima."""
    try:
        import edge_tts
    except ImportError:
        raise RuntimeError("edge-tts nije instaliran — pokreni: pip install -U edge-tts")

    # Microsoft je promijenio Sec-MS-GEC token (verzija "2-..."); starije
    # verzije edge-tts (<= 7.2.x, šalju "1-...") dobivaju 403 na websocketu.
    installed = getattr(edge_tts, "__version__", "0")
    if tuple(int(p) for p in re.findall(r"\d+", installed)[:2]) < EDGE_TTS_MIN_VERSION:
        raise RuntimeError(
            f"edge-tts {installed} je prestar (Microsoft vraća 403) — pokreni: "
            f"pip install -U 'edge-tts>={'.'.join(map(str, EDGE_TTS_MIN_VERSION))}'")

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

    # Neslužbeni endpoint povremeno odbije zahtjev (403/429/reset) —
    # par pokušaja s backoffom prije nego što cijeli video padne.
    for attempt in range(1, EDGE_TTS_ATTEMPTS + 1):
        words.clear()
        try:
            asyncio.run(run())
            break
        except Exception as e:
            if attempt == EDGE_TTS_ATTEMPTS:
                raise
            wait = 5 * attempt
            print(f"edge-tts pokušaj {attempt}/{EDGE_TTS_ATTEMPTS} pao ({e}); "
                  f"ponovno za {wait} s", file=sys.stderr)
            time.sleep(wait)
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
Style: Hook,Arial,84,&H00FFFFFF,&H00FFFFFF,&H003C64C8,&H00000000,-1,0,0,0,100,100,0,0,3,22,0,8,90,90,300,1
Style: Tag,Arial,58,&H00101010,&H00101010,&H0000D7FF,&H00000000,-1,0,0,0,100,100,0,0,3,14,0,8,90,90,220,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ass_ts(sec):
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


CAP_HIGHLIGHT = r"{\c&H00D7FF&\fscx108\fscy108}"   # žuta + blagi "pop" za riječ koja se izgovara


def ass_text(text):
    """Slobodan tekst (naslov) siguran za ASS: bez override zagrada i \\N trikova."""
    return re.sub(r"[{}\\]", "", text).strip()


def build_ass(words, path, overlays=None):
    """Titlovi u grupicama ≤3 riječi, s trenutno izgovorenom riječi istaknutom
    (karaoke) — oko prati riječ, što drži pažnju bolje od statičnog bloka.
    overlays: [(start, end, style, text)] — hook naslov, Tery oznaka."""
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
        texts = [ass_text(w["text"]).upper() for w in ch]
        for j in range(len(ch)):
            w_start = start if j == 0 else ch[j]["start"]
            w_end = ch[j + 1]["start"] if j + 1 < len(ch) else end
            if w_end <= w_start:
                continue
            line = " ".join(CAP_HIGHLIGHT + t + r"{\r}" if k == j else t
                            for k, t in enumerate(texts))
            events.append(f"Dialogue: 0,{ass_ts(w_start)},{ass_ts(w_end)},Cap,,0,0,0,,{line}")

    for o_start, o_end, style, text in overlays or []:
        events.append(f"Dialogue: 1,{ass_ts(o_start)},{ass_ts(o_end)},{style},,0,0,0,,{ass_text(text)}")

    path.write_text(ASS_HEADER + "\n".join(events) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Pexels — vertikalne slike po segmentu
# ---------------------------------------------------------------------------

def call_pexels_image(query, env, orientation="landscape"):
    """Besplatan Pexels API, bez strogog rate limita — stock foto po upitu.

    Dva odvojena retry() poziva, ne jedan: search (api.pexels.com) i download
    (images.pexels.com, CDN) su dva različita hosta na dvije različite
    Cloudflare zone — dijeljenje jedne 'Pexels image search' etikete na oba
    je jedno veče skrivalo koji je od njih stvarno pao (2026-09-17). Usput i
    jeftinije: ako padne samo download, retry ne plaća novi search poziv.
    """
    api_key = env.get("PEXELS_API_KEY")
    if not api_key:
        raise RuntimeError("PEXELS_API_KEY nije postavljen u .env.")

    def do_search():
        params = urllib.parse.urlencode({"query": query, "per_page": 1, "orientation": orientation})
        _, body = http_request("GET", f"https://api.pexels.com/v1/search?{params}",
                               headers={"Authorization": api_key})
        photos = json.loads(body).get("photos") or []
        if not photos:
            raise RuntimeError(f"Pexels nije vratio nijednu fotografiju za upit '{query}'.")
        return photos[0]["src"]["large2x"]

    image_url = retry(do_search, what=f"Pexels search ('{query}')")

    def do_download():
        _, img_bytes = http_request("GET", image_url)
        return img_bytes

    return retry(do_download, what=f"Pexels image download ({image_url})")


def call_pexels_video(query, env, min_width=720):
    """Vertikalni stock video klip s Pexelsa (isti API ključ i ista kvota kao
    foto search). Vraća bytes MP4-a. Bira najmanji mp4 koji je barem min_width
    širok — 4K fajlovi su nepotrebno teški za 1080x1920 izlaz."""
    api_key = env.get("PEXELS_API_KEY")
    if not api_key:
        raise RuntimeError("PEXELS_API_KEY nije postavljen u .env.")

    def do_search():
        params = urllib.parse.urlencode({"query": query, "per_page": 5,
                                         "orientation": "portrait", "size": "medium"})
        _, body = http_request("GET", f"https://api.pexels.com/videos/search?{params}",
                               headers={"Authorization": api_key})
        for video in json.loads(body).get("videos") or []:
            files = [f for f in video.get("video_files") or []
                     if f.get("file_type") == "video/mp4"
                     and (f.get("height") or 0) > (f.get("width") or 0)
                     and (f.get("width") or 0) >= min_width]
            if files:
                return min(files, key=lambda f: f["width"])["link"]
        raise RuntimeError(f"Pexels nije vratio vertikalni video za upit '{query}'.")

    video_url = retry(do_search, what=f"Pexels video search ('{query}')")

    def do_download():
        _, data = http_request("GET", video_url, timeout=120)
        return data

    return retry(do_download, what=f"Pexels video download ({video_url})")


def render_clip_segment(clip_name, duration, out_name, workdir, env):
    """Stock klip -> segment istih parametara kao render_segment (1080x1920,
    30 fps, h264 yuv420p, bez zvuka), da concat s '-c copy' radi i kad se
    klipovi i slike miješaju. -stream_loop pokriva klipove kraće od segmenta."""
    frames = max(int(round(duration * FPS)), FPS)
    vf = (f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
          f"crop={VIDEO_W}:{VIDEO_H},fps={FPS},setsar=1")
    run_cmd([env.get("FFMPEG_BIN") or "ffmpeg", "-y", "-stream_loop", "-1", "-i", clip_name,
             "-vf", vf, "-frames:v", frames, "-an",
             "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
             "-video_track_timescale", "15360",
             out_name],
            cwd=workdir, what=f"ffmpeg clip segment {out_name}")


# ---------------------------------------------------------------------------
# Tery tretman — prava snimka štenca u stock videu + glazba + hook naslov
# ---------------------------------------------------------------------------
# Zašto: kanal 27.8.–25.10.2026. — Tery Shortsi (prava snimka) drže 95–135 %
# prosječno odgledanog i donose pretplate, stock Shortsi ~40–65 % i skoro
# nijednu. Svaki video zato završava Teryjem koji gleda u kameru (CTA), a
# srednji segment čija se slika poklapa s nekom Tery kategorijom dobiva
# Tery klip umjesto Pexelsa (najviše jedan). Sve je best-effort: bez
# assets/tery ili assets/music pipeline radi kao prije.

TERY_DIR = PROJECT_DIR / "assets" / "tery"       # <kategorija>__<opis>_NN.mp4, 9:16
MUSIC_DIR = PROJECT_DIR / "assets" / "music"     # *.mp3 (YouTube Audio Library)
MUSIC_VOLUME = 0.10
HOOK_SECONDS = 2.6
TERY_OUTRO_CATEGORY = "gleda-u-kameru"
TERY_TOPIC_KW = {
    "spava": ("sleep", "bed", "nap", "rest", "tired", "crate", "night", "lying"),
    "grize": ("bit", "nip", "chew", "mouth", "teeth", "teething", "nibbl"),
    "jede-pije": ("eat", "food", "bowl", "drink", "water", "meal", "feed", "kibble"),
    "trci-igra": ("play", "run", "fetch", "toy", "zoom", "exercis", "energ", "chas"),
    "kaos": ("destroy", "steal", "chaos", "mess", "shred", "bottle"),
}
# Tery klip kraći od segmenta: prvo blagi slow-mo (štenac usporeno izgleda
# prirodno), ostatak zamrznut zadnji kadar — nikad vidljiva petlja.
TERY_MAX_SLOWMO = 1.35
TERY_MAX_FREEZE = 0.5


def tery_fits(clip_dur, duration):
    return clip_dur * TERY_MAX_SLOWMO + TERY_MAX_FREEZE >= duration


def tery_clips(category):
    return sorted(TERY_DIR.glob(f"{category}__*.mp4")) if TERY_DIR.is_dir() else []


def plan_tery(segments, durations, rotation, env):
    """{indeks segmenta: (Path klipa, oznaka)} — zadnji segment (CTA) uvijek
    Tery koji gleda u kameru, plus najviše jedan srednji po temi slike."""
    plan = {}
    outro = tery_clips(TERY_OUTRO_CATEGORY)
    if outro and len(segments) >= 2:
        plan[len(segments) - 1] = (outro[rotation % len(outro)], "TERY  ·  @thedoghabit")
    for i in range(1, len(segments) - 1):
        words = re.findall(r"[a-z]+", segments[i].get("image_prompt", "").lower())
        for category, kws in TERY_TOPIC_KW.items():
            if not any(w.startswith(k) for w in words for k in kws):
                continue
            clips = tery_clips(category)
            # Rotacija bira početni klip; uzima se prvi dovoljno dug.
            for k in range(len(clips)):
                clip = clips[(rotation + k) % len(clips)]
                try:
                    clip_dur, _ = probe_clip(clip, env)
                except Exception:
                    continue
                if tery_fits(clip_dur, durations[i]):
                    plan[i] = (clip, "REAL PUP: TERY")
                    return plan
    return plan


def render_tery_segment(clip_name, duration, out_name, workdir, env):
    """Kao render_clip_segment, ali bez petlje: Tery klip se ne smije vidljivo
    ponoviti — ako je kraći od segmenta, uspori se (do TERY_MAX_SLOWMO) pa
    se zadnji kadar zamrzne."""
    frames = max(int(round(duration * FPS)), FPS)
    try:
        clip_dur, _ = probe_clip(workdir / clip_name, env)
        slow = min(max(duration / clip_dur, 1.0), TERY_MAX_SLOWMO)
    except Exception:
        slow = 1.0
    vf = (f"setpts={slow:.3f}*PTS,"
          f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
          f"crop={VIDEO_W}:{VIDEO_H},fps={FPS},setsar=1,"
          f"tpad=stop_mode=clone:stop_duration={duration:.2f}")
    run_cmd([env.get("FFMPEG_BIN") or "ffmpeg", "-y", "-i", clip_name,
             "-vf", vf, "-frames:v", frames, "-an",
             "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
             "-video_track_timescale", "15360",
             out_name],
            cwd=workdir, what=f"ffmpeg tery segment {out_name}")


def pick_music(workdir, rotation, env):
    """Kopira jednu podlogu u workdir; vraća (ime, početni offset) ili None.
    Offset rotira kroz pjesmu da svaki video ne počinje istim taktom."""
    tracks = sorted(MUSIC_DIR.glob("*.mp3")) if MUSIC_DIR.is_dir() else []
    if not tracks:
        return None
    track = tracks[rotation % len(tracks)]
    try:
        dur, _ = probe_clip(track, env)
    except Exception:
        dur = 0
    offset = (rotation * 23) % max(int(dur) - 40, 1) if dur > 60 else 0
    shutil.copyfile(track, workdir / "music.mp3")
    return "music.mp3", offset


def fetch_segment_image(prompt, env, dry_run):
    if dry_run:
        log("  [dry-run] koristim placeholder sliku umjesto Pexels API-ja")
        return PLACEHOLDER_IMAGE.read_bytes()
    return call_pexels_image(prompt, env, orientation="portrait")


def process_image_vertical(image_bytes):
    """Center-crop na 9:16 pa resize na 1080x1920 — Pexels i placeholder
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
             "-video_track_timescale", "15360",
             out_name],
            cwd=workdir, what=f"ffmpeg segment {out_name}")


def assemble_video(segment_names, workdir, slug, env, music=None):
    """Concat segmenata + audio + burn-in titlovi u finalni MP4. ffmpeg se
    vrti s cwd=workdir i relativnim imenima — apsolutni Windows putevi u
    ass= filteru zahtijevaju escapanje dvotočke i backslasheva.
    music: (ime, offset) iz pick_music — tiho ispod naracije."""
    ffmpeg = env.get("FFMPEG_BIN") or "ffmpeg"

    concat_list = workdir / "concat.txt"
    concat_list.write_text("".join(f"file '{n}'\n" for n in segment_names))
    run_cmd([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", "concat.txt",
             "-c", "copy", "concat.mp4"],
            cwd=workdir, what="ffmpeg concat")

    final_name = f"{slug}.mp4"
    inputs = ["-i", "concat.mp4", "-i", "voice.mp3"]
    if music:
        music_name, offset = music
        inputs += ["-ss", str(offset), "-stream_loop", "-1", "-i", music_name]
        # amix dijeli ulaze s 2; volume=2 vraća glas na punu glasnoću (vidi
        # render_own_clip). duration=first: podloga staje s naracijom + fade.
        audio = ["-filter_complex",
                 f"[0:v]ass=subs.ass[v];[2:a]volume={MUSIC_VOLUME}[m];"
                 f"[1:a]apad=pad_dur=0.8[vo];"
                 f"[vo][m]amix=inputs=2:duration=first:dropout_transition=0,volume=2[a]",
                 "-map", "[v]", "-map", "[a]", "-shortest"]
    else:
        audio = ["-vf", "ass=subs.ass", "-map", "0:v", "-map", "1:a"]
    run_cmd([ffmpeg, "-y", *inputs, *audio,
             "-c:v", "libx264", "-preset", "medium", "-crf", "21",
             "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart",
             final_name],
            cwd=workdir, what="ffmpeg final render")
    return workdir / final_name


# ---------------------------------------------------------------------------
# Vlastita snimka (--own-clip) — prava snimka psa umjesto Pexels klipova
# ---------------------------------------------------------------------------
# Zašto: AdSense je odbio web zbog "sadržaja niske vrijednosti"; vlastite snimke
# (Tery) su jedini sadržaj koji AI/stock ne može proizvesti. Snimka se reže
# ručno (bez praznih kadrova); pipeline dodaje samo naraciju, titlove, opis,
# upload i embed na članku — isti put kao Pexels video.

WORDS_PER_SECOND = 2.7          # izmjereni edge-tts tempo (vidi SCRIPT_MAX_WORDS)
OWN_CLIP_MIN_SECONDS = 8
OWN_CLIP_MAX_SECONDS = 59       # Shorts limit je 60 s (s tpad zrakom na kraju)
OWN_CLIP_AUDIO_VOLUME = 0.15    # originalni zvuk snimke tiho ispod naracije


def probe_clip(path, env):
    """(trajanje u s, ima_audio) za vlastitu snimku."""
    out = run_cmd([env.get("FFPROBE_BIN") or "ffprobe", "-v", "error",
                   "-show_entries", "format=duration:stream=codec_type",
                   "-of", "json", path.name],
                  cwd=path.parent, what="ffprobe own clip")
    info = json.loads(out.decode())
    duration = float(info["format"]["duration"])
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams") or [])
    return duration, has_audio


def own_clip_max_words(duration):
    """Gornja granica riječi naracije da glas stane u snimku (~0,5 s zraka).
    Nikad iznad SCRIPT_MAX_WORDS — ista retencijska logika kao kod stocka."""
    return max(10, min(SCRIPT_MAX_WORDS, int((duration - 0.5) * WORDS_PER_SECOND)))


def own_clip_article_text(article_text, note, duration):
    """Lumenta ne vidi snimku i tiho ignorira nepoznata polja, pa opis snimke
    ide na početak article_text. Bez ovoga skripta opisuje generičnog psa, a
    naracija mora pratiti ono što se na snimci stvarno vidi."""
    header = (
        f"VIDEO FOOTAGE ({duration:.0f} s, real footage of our own dog, not stock): "
        f"{note.strip()}\n"
        "Write the narration for THIS footage: talk about what the viewer sees, "
        "then one practical takeaway from the article below. Do not describe "
        "scenes that are not in the footage. Image prompts are ignored.\n\n"
        "ARTICLE:\n")
    return header + article_text


def render_own_clip(clip_name, total_audio, workdir, slug, env, clip_volume):
    """Snimka -> finalni 9:16 MP4 s naracijom i titlovima. Ako je naracija
    dulja od snimke, zadnji kadar se zamrzne (tpad); originalni zvuk ide tiho
    ispod glasa (clip_volume 0 = bez njega)."""
    ffmpeg = env.get("FFMPEG_BIN") or "ffmpeg"
    clip_dur, has_audio = probe_clip(workdir / clip_name, env)
    out_dur = max(clip_dur, total_audio + 0.5)
    pad = out_dur - clip_dur

    vf = (f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
          f"crop={VIDEO_W}:{VIDEO_H},fps={FPS},setsar=1")
    if pad > 0.05:
        vf += f",tpad=stop_mode=clone:stop_duration={pad:.2f}"
    vf += ",ass=subs.ass"

    if has_audio and clip_volume > 0:
        # amix dijeli svaki ulaz s brojem ulaza; volume=2 to vraća (radi i na
        # starijem ffmpegu bez amix normalize=0).
        af = (f"[0:a]volume={clip_volume}[bg];"
              f"[bg][1:a]amix=inputs=2:duration=longest:dropout_transition=0,volume=2[a]")
    else:
        af = "[1:a]apad[a]"

    final_name = f"{slug}.mp4"
    run_cmd([ffmpeg, "-y", "-i", clip_name, "-i", "voice.mp3",
             "-filter_complex", f"[0:v]{vf}[v];{af}",
             "-map", "[v]", "-map", "[a]", "-t", f"{out_dur:.2f}",
             "-c:v", "libx264", "-preset", "medium", "-crf", "21",
             "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart",
             final_name],
            cwd=workdir, what="ffmpeg own clip render")
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


# ---------------------------------------------------------------------------
# Short na stranici članka (meta thedoghabit_youtube_id, wp/mu-plugins/thedoghabit-short-embed.php)
# ---------------------------------------------------------------------------

YT_META_KEY = "thedoghabit_youtube_id"


def yt_is_embeddable(youtube_id):
    """True ako je video javan ili unlisted (oEmbed vraća 200). Privatni ili
    obrisani video vraća 401/403/404 — takav embed bi na stranici bio prazan."""
    url = ("https://www.youtube.com/oembed?format=json&url="
           + urllib.parse.quote(f"https://www.youtube.com/shorts/{youtube_id}", safe=""))
    try:
        status, _ = http_request("GET", url, timeout=15)
        return status == 200
    except urllib.error.HTTPError:
        return False


YT_TITLE_META_KEY = "thedoghabit_youtube_title"
YT_DATE_META_KEY = "thedoghabit_youtube_date"


def clean_video_title(title):
    """Naslov za VideoObject schema: bez hashtagova (#shorts i sl.)."""
    return re.sub(r"\s*#\w+", "", title or "").strip()


def video_meta_for(entry):
    """Naslov i datum uploada za VideoObject schema. Naslov dolazi iz zapisa ili
    iz sidecar JSON-a koji main() zapisuje uz render (videos/<slug>/<slug>.json);
    datum i vrijeme iz 'created' (vrijeme uploada, UTC) kao ISO 8601 sa zonom."""
    title = entry.get("yt_title")
    if not title:
        sidecar = OUTPUT_DIR / entry.get("slug", "") / f"{entry.get('slug', '')}.json"
        try:
            title = json.loads(sidecar.read_text(encoding="utf-8")).get("yt_title")
        except (OSError, ValueError):
            title = None
    created = entry.get("created") or ""
    date = ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", created):
        # 'created' je UTC (vidi main()); Google traži i vremensku zonu.
        date = created.replace(" ", "T") + "+00:00"
    return clean_video_title(title), date


def wp_set_youtube_meta(env, post_id, youtube_id, title="", date=""):
    """Upiše YouTube ID (+ naslov i datum za VideoObject schema) u post meta.
    Treba WP_USER + WP_APP_PASSWORD (isti korisnik kao generate_post.py)."""
    token = base64.b64encode(f"{env['WP_USER']}:{env['WP_APP_PASSWORD']}".encode()).decode()
    meta = {YT_META_KEY: youtube_id}
    if title:
        meta[YT_TITLE_META_KEY] = title
    if date:
        meta[YT_DATE_META_KEY] = date
    body = json.dumps({"meta": meta}).encode()
    status, resp = http_request(
        "POST", f"{env['WP_URL']}/wp-json/wp/v2/posts/{post_id}",
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
        data=body, timeout=30)
    saved = (json.loads(resp).get("meta") or {}).get(YT_META_KEY)
    if saved != youtube_id:
        raise RuntimeError(f"WP nije spremio {YT_META_KEY} za post {post_id} (vraćeno: {saved!r})")
    return status


# 2 = uz ID se šalju i naslov + datum za VideoObject schema.
# 3 = datum je puni ISO 8601 s vremenom i zonom (Google upozorava na sam datum).
# Zapise s nižom verzijom (ili bez nje) backfill ponovno obrađuje.
EMBED_VERSION = 3


def embed_short(env, entry):
    """Best-effort: postavi embed za jedan videos.json zapis. Vraća razlog
    preskakanja ili None ako je embed postavljen. Nikad ne ruši run."""
    yid = entry.get("youtube_id")
    if not yid or not entry.get("post_id"):
        return "nema youtube_id/post_id"
    if not yt_is_embeddable(yid):
        return "video nije javan (privatan ili obrisan)"
    title, date = video_meta_for(entry)
    retry(lambda: wp_set_youtube_meta(env, entry["post_id"], yid, title, date),
          what="WP meta (youtube_id)")
    entry["embedded"] = True
    entry["embed_v"] = EMBED_VERSION
    return None


def backfill_embeds(env, state):
    done, skipped = 0, []
    for entry in state:
        if entry.get("embed_v", 0) >= EMBED_VERSION or entry.get("status") != "uploaded":
            continue
        try:
            reason = embed_short(env, entry)
        except Exception as e:  # jedan loš zapis ne smije zaustaviti ostale
            reason = f"greška: {e}"
        if reason:
            skipped.append((entry.get("slug"), reason))
        else:
            done += 1
            log(f"  embed: {entry['slug']} -> {entry['youtube_id']}")
    save_state(state)
    log(f"Backfill gotov: {done} postavljeno, {len(skipped)} preskočeno.")
    for slug, reason in skipped:
        log(f"  preskočeno {slug}: {reason}")
    return done, skipped


def main():
    parser = argparse.ArgumentParser(description="Generiraj YouTube Short iz thedoghabit.com posta")
    parser.add_argument("--dry-run", action="store_true",
                        help="mock post/skripta/slike, pravi TTS i ffmpeg — bez API ključeva")
    parser.add_argument("--post-slug", help="obradi točno ovaj WP slug (i ako već ima video)")
    parser.add_argument("--upload", action="store_true",
                        help="uploadaj na YouTube (YT_* varovi u .env-u; default privacy 'private')")
    parser.add_argument("--backfill-embeds", action="store_true",
                        help="postavi Short embed na sve članke čiji je video već uploadan (videos.json)")
    parser.add_argument("--yt-auth", action="store_true",
                        help="jednokratni OAuth flow — ispiše YT_REFRESH_TOKEN za .env")
    parser.add_argument("--own-clip", type=Path,
                        help="vlastita (već izrezana) snimka umjesto Pexels klipova; traži --post-slug i --clip-note")
    parser.add_argument("--clip-note",
                        help="što se vidi na snimci, npr. 'Tery, JRT puppy, first time on a pebble beach, sniffing everything'")
    parser.add_argument("--clip-audio-volume", type=float, default=OWN_CLIP_AUDIO_VOLUME,
                        help=f"glasnoća originalnog zvuka snimke ispod naracije (0-1, default {OWN_CLIP_AUDIO_VOLUME})")
    args = parser.parse_args()

    if args.own_clip:
        if not args.dry_run and not args.post_slug:
            parser.error("--own-clip traži --post-slug (članak na koji video vodi i gdje se ugrađuje)")
        if not (args.clip_note or "").strip():
            parser.error("--own-clip traži --clip-note (Lumenta ne vidi snimku)")
        if not args.own_clip.is_file():
            parser.error(f"--own-clip: datoteka ne postoji: {args.own_clip}")

    env = load_env(ENV_PATH)

    if args.yt_auth:
        yt_auth(env)
        return

    state = load_state()

    if args.backfill_embeds:
        backfill_embeds(env, state)
        return

    if args.dry_run:
        post = DRY_RUN_POST
    else:
        post = get_next_post(env, state, wanted_slug=args.post_slug)
    if not post:
        log("Nema objavljenih postova bez videa — ništa za napraviti.")
        # Trag za check_video_silence.py: tišina bez posla nije kvar. Piše se
        # SAMO ovdje — ako get_next_post padne, traga nema i alarm vrijedi.
        if not args.dry_run:
            HEARTBEAT_PATH.write_text(json.dumps(
                {"idle_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())}) + "\n")
        return

    post_title = strip_html(post["title"]["rendered"], limit=200)
    slug = post["slug"]
    log(f"Odabran post: '{post_title}' (slug={slug})")

    workdir = OUTPUT_DIR / slug
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        article_text = strip_html(post["content"]["rendered"])
        breed = pick_breed(post_title, slug, state, env)
        log(f"  pasmina za ovaj video: {breed}")
        max_words = target_seconds = None
        if args.own_clip:
            # Kopija u workdir: ffmpeg radi s relativnim imenima (vidi assemble_video).
            clip_name = "source" + (args.own_clip.suffix.lower() or ".mp4")
            shutil.copyfile(args.own_clip, workdir / clip_name)
            clip_dur, _ = probe_clip(workdir / clip_name, env)
            if not OWN_CLIP_MIN_SECONDS <= clip_dur <= OWN_CLIP_MAX_SECONDS:
                raise RuntimeError(f"snimka traje {clip_dur:.1f} s "
                                   f"(dozvoljeno {OWN_CLIP_MIN_SECONDS}-{OWN_CLIP_MAX_SECONDS} s)")
            max_words = own_clip_max_words(clip_dur)
            target_seconds = round(clip_dur)
            article_text = own_clip_article_text(article_text, args.clip_note, clip_dur)
            log(f"  vlastita snimka: {args.own_clip.name}, {clip_dur:.1f} s, max {max_words} riječi")
        script = get_valid_script(post_title, article_text, breed, env, args.dry_run,
                                  max_words=max_words, target_seconds=target_seconds)
        segments = script["segments"]
        # Cijela skripta (s image promptovima) uz video — bez ovoga se promptovi
        # gube pa se slike ne mogu ciljano popraviti/regenerirati naknadno.
        (workdir / "script.json").write_text(
            json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        log(f"  skripta: {len(segments)} segmenata, {sum(count_words(s['text']) for s in segments)} riječi naracije")

        narration = " ".join(s["text"].strip() for s in segments)
        words = synthesize_voice(narration, workdir / "voice.mp3", env)
        total_audio = audio_duration(workdir / "voice.mp3", env)
        log(f"  voiceover: {total_audio:.1f} s, {len(words)} riječi")

        if args.own_clip:
            build_ass(words, workdir / "subs.ass")
            final_path = render_own_clip(clip_name, total_audio, workdir, slug, env,
                                         args.clip_audio_volume)
        else:
            durations = allocate_durations(segments, total_audio + 0.5)
            rotation = len(state)
            tery_plan = plan_tery(segments, durations, rotation, env)
            music = pick_music(workdir, rotation, env)
            log(f"  Tery segmenti: {sorted(i + 1 for i in tery_plan) or 'nema'}; "
                f"glazba: {'da' if music else 'ne'}")
            starts = [sum(durations[:i]) for i in range(len(durations))]
            overlays = [(0, min(HOOK_SECONDS, durations[0]), "Hook", script["yt_title"])]
            overlays += [(starts[i], starts[i] + durations[i], "Tag", label)
                         for i, (_, label) in tery_plan.items()]
            build_ass(words, workdir / "subs.ass", overlays)
            final_path = render_stock_video(segments, durations, tery_plan, music,
                                            workdir, slug, env, args.dry_run)
        size_mb = final_path.stat().st_size / 1024 / 1024
        log(f"Video renderiran: {final_path} ({size_mb:.1f} MB)")

        description = build_description(script.get("yt_description", ""), post["link"])
        meta = {
            "post_id": post["id"],
            "slug": slug,
            "yt_title": script["yt_title"],
            "yt_description": description,
            "yt_tags": script.get("yt_tags") or [],
            # Prijedlog prikvačenog komentara — YT API ne podržava pin,
            # pa ide u sidecar za ručni copy-paste pri objavi.
            "pinned_comment": script.get("pinned_comment") or "",
            "video": final_path.name,
            "source": "own_clip" if args.own_clip else "stock",
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

    finish_run(env, state, post, post_title, slug, final_path, meta, youtube_id, args.dry_run)


def render_stock_video(segments, durations, tery_plan, music, workdir, slug, env, dry_run):
    """Pexels klipovi (fallback slike) po segmentu + naracija -> finalni MP4.
    tery_plan: {indeks: (klip, oznaka)} iz plan_tery — ti segmenti su Tery."""
    segment_names = []
    # Video klipovi umjesto statičnih slika: pas koji se kreće u prve dvije
    # sekunde zaustavlja skrolanje, Ken Burns zoom preko fotke ne.
    # VIDEO_CLIPS=0 u .env-u vraća samo slike. Klip je best-effort: ako
    # search/download/render padne, segment pada natrag na sliku.
    use_clips = env.get("VIDEO_CLIPS", "1") != "0" and not dry_run
    clip_count = 0
    for i, (seg, dur) in enumerate(zip(segments, durations)):
        seg_name = f"seg_{i}.mp4"
        kind = "slika"
        if i in tery_plan:
            try:
                tery_name = f"tery_{i}.mp4"
                shutil.copyfile(tery_plan[i][0], workdir / tery_name)
                render_tery_segment(tery_name, dur, seg_name, workdir, env)
                kind = f"Tery: {tery_plan[i][0].name}"
            except Exception as e:
                log(f"  segment {i + 1}: Tery klip nije uspio ({e}) — koristim stock")
        if kind == "slika" and use_clips:
            try:
                clip_name = f"clip_{i}.mp4"
                (workdir / clip_name).write_bytes(call_pexels_video(seg["image_prompt"], env))
                render_clip_segment(clip_name, dur, seg_name, workdir, env)
                kind = "klip"
                clip_count += 1
            except Exception as e:
                log(f"  segment {i + 1}: klip nije uspio ({e}) — koristim sliku")
        if kind == "slika":
            img_bytes = fetch_segment_image(seg["image_prompt"], env, dry_run)
            img_name = f"img_{i}.jpg"
            (workdir / img_name).write_bytes(process_image_vertical(img_bytes))
            render_segment(img_name, dur, seg_name, workdir, env)
        segment_names.append(seg_name)
        log(f"  segment {i + 1}/{len(segments)}: {dur:.1f} s ({kind})")
    if use_clips:
        log(f"  klipova: {clip_count}/{len(segments)}")
    return assemble_video(segment_names, workdir, slug, env, music=music)


def finish_run(env, state, post, post_title, slug, final_path, meta, youtube_id, dry_run):
    """State (videos.json), embed na članku i alert nakon uspješnog rendera."""
    if dry_run:
        log("[dry-run] state NIJE ažuriran — videos.json netaknut.")
        return

    previous = next((v for v in state if v["slug"] == slug), None)
    state = [v for v in state if v["slug"] != slug]
    entry = {
        "slug": slug,
        "post_id": post["id"],
        "status": "uploaded" if youtube_id else "rendered",
        "video": str(final_path.relative_to(PROJECT_DIR)),
        "youtube_id": youtube_id,
        "yt_title": meta["yt_title"],
        "source": meta.get("source", "stock"),
        "created": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
    }
    # Vlastita snimka često zamjenjuje stariji stock video istog članka — trag
    # starog YouTube ID-a ostaje, da se stari Short može ručno skloniti.
    if previous and previous.get("youtube_id") and previous.get("youtube_id") != youtube_id:
        entry["replaced_youtube_id"] = previous["youtube_id"]
    # Embed na stranici članka. Best-effort: video je već na YouTubeu, pa pad
    # ovdje ne smije označiti run kao neuspjeh — --backfill-embeds ga nadoknadi.
    embed_note = ""
    if youtube_id:
        try:
            reason = embed_short(env, entry)
            embed_note = "\nEmbed na članku: " + ("postavljen" if not reason else f"NIJE ({reason})")
        except Exception as e:
            embed_note = f"\nEmbed na članku: NIJE (greška: {e}) — pokreni --backfill-embeds"
        log(embed_note.strip())
    state.append(entry)
    save_state(state)
    alert = (f"Video spreman: '{post_title}'"
             + (" [vlastita snimka]" if entry["source"] == "own_clip" else "")
             + (f"\nYT: https://youtu.be/{youtube_id}" if youtube_id else
                f" → {final_path.name} (čeka ručni upload)"))
    if entry.get("replaced_youtube_id"):
        alert += f"\nZamijenjen stari Short: https://youtu.be/{entry['replaced_youtube_id']}"
    alert += embed_note
    if meta.get("pinned_comment"):
        alert += f"\n\n\U0001F4CC Prijedlog pina:\n{meta['pinned_comment']}"
    send_alert(alert, env)
    log("State ažuriran u videos.json.")


if __name__ == "__main__":
    main()
