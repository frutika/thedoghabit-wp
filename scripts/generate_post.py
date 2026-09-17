#!/usr/bin/env python3
"""generate_post.py — thedoghabit.com cron skripta (spec §3-4).

Uzima prvu 'pending' temu iz topics.json, generira članak preko Lumenta
internog endpointa (§4), featured sliku preko Pexels API-ja, i objavljuje
na WordPress preko REST API-ja. HTTP pozivi idu preko urllib-a (stdlib);
jedina vanjska ovisnost je Pillow, za normalizaciju featured slike prije
uploada (već instaliran na hostu).

Post ide kao "draft" po defaultu; --publish ga objavljuje odmah (cron
koristi --publish nakon što je kvaliteta ručno potvrđena). --dry-run
zaobilazi Lumenta i Pexels pozive mock/placeholder podacima, za
testiranje cijelog tijeka bez API ključeva.
"""
import argparse
import base64
import io
import json
import mimetypes
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_DIR / ".env"
TOPICS_PATH = PROJECT_DIR / "topics.json"
PLACEHOLDER_IMAGE = PROJECT_DIR / "assets" / "placeholder-dog.jpg"
LOG_PATH = PROJECT_DIR / "logs" / "generate_post.log"

CATEGORY_NAMES = {
    "training": "Dog Training",
    "behavior": "Dog Behavior",
    "problems": "Common Problems & Fixes",
    "gear": "Gear & Equipment",
    "habits": "Daily Habits & Routines",
    "puppies": "Puppy Basics",
}

# Breed-specifičan sadržaj (npr. JRT hub) traži Pexels sliku te pasmine
# umjesto generičkog "dog" upita.
BREED_TAGS = {"jack-russell"}


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def load_env(path):
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def load_topics():
    return json.loads(TOPICS_PATH.read_text())


def save_topics(topics):
    TOPICS_PATH.write_text(json.dumps(topics, indent=2) + "\n")


def slugify(title):
    import re
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) thedoghabit-generate-post/1.0"


def http_request(method, url, headers=None, data=None, timeout=30):
    merged_headers = {"User-Agent": DEFAULT_USER_AGENT, **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=merged_headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return resp.status, body


def send_alert(msg, env):
    """Pošalji alert na Telegram (spec §3 'Pouzdanost'). Best-effort: ako
    TELEGRAM_* varovi nisu postavljeni ili poziv padne, samo logiraj —
    alert nikad ne smije srušiti pipeline."""
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        payload = json.dumps({"chat_id": chat_id, "text": f"[thedoghabit] {msg}"}).encode()
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
            if isinstance(e, urllib.error.HTTPError):
                detail = e.read().decode(errors="replace")
                # cf-ray identifies which Cloudflare rule fired — without it,
                # a report to a provider's support has nothing to point at.
                # Gated on cf-ray being present so non-Cloudflare hosts
                # (Lumenta, WP) don't get a noisy all-None suffix.
                cf_ray = e.headers.get("cf-ray")
                if cf_ray:
                    detail += (
                        f" [cf-ray={cf_ray} mitigated={e.headers.get('cf-mitigated')} "
                        f"ratelimit={e.headers.get('x-ratelimit-remaining')}/{e.headers.get('x-ratelimit-limit')}]"
                    )
            else:
                detail = str(e)
            log(f"  pokušaj {attempt}/{attempts} za '{what}' nije uspio: {detail}")
            if attempt < attempts:
                time.sleep(base_delay * attempt)
    raise RuntimeError(f"'{what}' nije uspio nakon {attempts} pokušaja: {last_err}")


def call_lumenta(topic, env, dry_run):
    if dry_run:
        log("  [dry-run] koristim mock Lumenta odgovor")
        kw = topic["keywords"]
        title = topic["title_seed"]
        return {
            "title": title,
            "article_html": (
                f"<p>This is a dry-run mock article about <strong>{title}</strong>.</p>"
                f"<p>Key topics covered: {', '.join(kw)}.</p>"
                "<h2>What to know</h2>"
                "<p>Mock body content generated locally for pipeline testing — "
                "no Lumenta API call was made.</p>"
            ),
            "meta_title": title[:60],
            "meta_description": f"A practical guide covering {', '.join(kw)}.",
            "slug": slugify(title),
            "faq": [
                {"question": f"Is this a real article about {kw[0]}?",
                 "answer": "No — this is dry-run mock content used to test the publishing pipeline."},
                {"question": "How do I get real content?",
                 "answer": "Run the script without --dry-run once the Lumenta endpoint is live."},
            ],
            "internal_anchors": [],
        }

    def do_call():
        payload = json.dumps({
            "tool": "seo_blog_post",
            "input": {
                "topic": topic["title_seed"],
                "keywords": topic["keywords"],
                "audience": "dog owners, global, English-speaking",
                "tone": "professional",
                "language": "en",
            },
        }).encode()
        headers = {
            "Authorization": f"Bearer {env['LUMENTA_API_KEY']}",
            "Content-Type": "application/json",
        }
        status, body = http_request("POST", f"{env['LUMENTA_API_URL']}/api/v1/generate",
                                     headers=headers, data=payload)
        return json.loads(body)["output"]

    # Anthropic zna vraćati 529 Overloaded u prozorima od više minuta (viđeno
    # 17.7.2026. u 07:00 UTC — default 3x s 2-4 s razmaka svi padnu u isti
    # prozor); širi razmaci daju ~4.5 min pokrića prije odustajanja.
    return retry(do_call, attempts=4, base_delay=45, what="Lumenta generate")


def call_pexels_image(query, env, orientation="landscape"):
    """Besplatan Pexels API, bez strogog rate limita — stock foto po upitu.

    per_page=10 + nasumičan odabir umjesto uvijek prvog rezultata: Pexels
    vraća DETERMINISTIČKI isti top rezultat za isti upit svaki put, pa bi
    per_page=1 značio da svi postovi s istim (ili sličnim) upitom dijele
    identičnu sliku — otkriveno 2026-08-17, tri zaredom objavljena posta
    bez breed taga imala su bit-identičnu featured sliku jer je upit uvijek
    bio doslovno "dog".

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
        params = urllib.parse.urlencode({"query": query, "per_page": 10, "orientation": orientation})
        _, body = http_request("GET", f"https://api.pexels.com/v1/search?{params}",
                               headers={"Authorization": api_key})
        photos = json.loads(body).get("photos") or []
        if not photos:
            raise RuntimeError(f"Pexels nije vratio nijednu fotografiju za upit '{query}'.")
        return random.choice(photos)["src"]["large2x"]

    image_url = retry(do_search, what=f"Pexels search ('{query}')")

    def do_download():
        _, img_bytes = http_request("GET", image_url)
        return img_bytes

    return retry(do_download, what=f"Pexels image download ({image_url})")


# Kategorija iz topics.json -> konkretniji Pexels upit (relevantnije slike
# + prirodno različiti upiti po temi, umjesto da SVI netagirani postovi
# dijele isti generički "dog" upit).
CATEGORY_PEXELS_QUERY = {
    "training": "dog training",
    "behavior": "dog behavior",
    "gear": "dog gear accessories",
    "habits": "dog daily life",
    "puppies": "puppy",
}


def _pexels_query_for_post(tags, topic=None):
    breed_key = next((t for t in (tags or []) if t in BREED_TAGS), None)
    if breed_key:
        return breed_key.replace("-", " ") + " dog"

    category = (topic or {}).get("category")
    if category in CATEGORY_PEXELS_QUERY:
        return CATEGORY_PEXELS_QUERY[category]

    keywords = (topic or {}).get("keywords") or []
    if keywords:
        # Prvih par riječi prve ključne fraze (npr. "why does my dog sleep at
        # my feet" -> "why does my dog") - Pexels traži foto-opisne pojmove,
        # ne pune long-tail SEO fraze, koje često vrate 0 rezultata.
        return " ".join(keywords[0].split()[:4])

    return "dog"


def fetch_featured_image(title, env, dry_run, tags=None, topic=None):
    if dry_run:
        log("  [dry-run] koristim placeholder sliku umjesto Pexels API-ja")
        return PLACEHOLDER_IMAGE.read_bytes(), "image/jpeg"
    query = _pexels_query_for_post(tags, topic)
    return call_pexels_image(query, env, orientation="landscape"), "image/jpeg"


REFILL_THRESHOLD = 10
REFILL_COUNT = 20


def refill_topics(topics, env, dry_run):
    """Dopuni topics.json novim temama preko Lumenta 'blog_topic_ideas' toola
    kad queue padne ispod praga (spec §3, korak 1). Non-fatalno: ako poziv
    padne, logiraj + alert i nastavi s postojećim temama."""
    if dry_run:
        log("  [dry-run] preskačem auto-refill tema")
        return topics

    existing = [t["title_seed"] for t in topics]

    def do_call():
        payload = json.dumps({
            "tool": "blog_topic_ideas",
            "input": {
                "niche": ("dog training, behavior, common problems, gear, "
                          "daily habits, puppies — practical guides for everyday dog owners"),
                "categories": list(CATEGORY_NAMES.keys()),
                "existing_titles": existing,
                "count": REFILL_COUNT,
                "language": "en",
            },
        }).encode()
        headers = {
            "Authorization": f"Bearer {env['LUMENTA_API_KEY']}",
            "Content-Type": "application/json",
        }
        status, body = http_request("POST", f"{env['LUMENTA_API_URL']}/api/v1/generate",
                                     headers=headers, data=payload, timeout=120)
        return json.loads(body)["output"]["topics"]

    try:
        new_topics = retry(do_call, what="Lumenta topic refill")
    except Exception as e:
        log(f"  auto-refill nije uspio: {e}")
        send_alert(f"auto-refill tema nije uspio: {e}", env)
        return topics

    existing_lower = {t.lower() for t in existing}
    added = 0
    for nt in new_topics:
        title = (nt.get("title_seed") or nt.get("title") or "").strip()
        category = nt.get("category")
        if not title or title.lower() in existing_lower or category not in CATEGORY_NAMES:
            continue
        topics.append({
            "title_seed": title,
            "category": category,
            "keywords": nt.get("keywords") or [],
            "status": "pending",
        })
        existing_lower.add(title.lower())
        added += 1

    if added:
        save_topics(topics)
    log(f"  auto-refill: dodano {added} novih tema u topics.json.")
    return topics


def wp_auth_header(env):
    token = base64.b64encode(f"{env['WP_USER']}:{env['WP_APP_PASSWORD']}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


IMAGE_TARGET_WIDTH = 1280
IMAGE_TARGET_RATIO = 16 / 9  # 1.78 — unutar IG-ovog 1.91:1 limita
IMAGE_MAX_BYTES = 8 * 1024 * 1024


def process_image(image_bytes):
    """Normalizira featured sliku prije uploada: JPEG q85, 1280px širina,
    16:9 (center-crop na omjer pa resize). Pexels/placeholder slike dolaze
    u različitim omjerima i formatima (npr. hero je bio 1344x768) — ovo
    garantira dosljedan izlaz bez obzira na izvor."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = img.size
    current_ratio = width / height

    if current_ratio > IMAGE_TARGET_RATIO:
        new_width = round(height * IMAGE_TARGET_RATIO)
        left = (width - new_width) // 2
        img = img.crop((left, 0, left + new_width, height))
    elif current_ratio < IMAGE_TARGET_RATIO:
        new_height = round(width / IMAGE_TARGET_RATIO)
        top = (height - new_height) // 2
        img = img.crop((0, top, width, top + new_height))

    target_height = round(IMAGE_TARGET_WIDTH / IMAGE_TARGET_RATIO)
    img = img.resize((IMAGE_TARGET_WIDTH, target_height), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    jpeg_bytes = buf.getvalue()

    if len(jpeg_bytes) > IMAGE_MAX_BYTES:
        raise RuntimeError(
            f"Obrađena slika ima {len(jpeg_bytes)} bajtova — prelazi {IMAGE_MAX_BYTES} bajtova limit."
        )

    return jpeg_bytes, "image/jpeg"


def upload_media(image_bytes, filename, content_type, env):
    def do_upload():
        headers = wp_auth_header(env)
        headers["Content-Type"] = content_type
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        status, body = http_request("POST", f"{env['WP_URL']}/wp-json/wp/v2/media",
                                     headers=headers, data=image_bytes)
        data = json.loads(body)
        return data["id"], data["source_url"]

    return retry(do_upload, what="WP media upload")


# Pinterest jako favorizira vertikalne (2:3) pinove s tekstom preko slike —
# 16:9 featured slika tamo dobije djelić prikaza. Iz iste Pexels slike
# generiramo zaseban vertikalni pin (blog/IG i dalje koriste 16:9 featured).
PIN_WIDTH = 1000
PIN_HEIGHT = 1500
PIN_RATIO = PIN_WIDTH / PIN_HEIGHT  # 0.667

# Prvi postojeći font s liste; PIN_FONT env override ima prednost. DejaVu/
# Liberation su standardno na Debianu (VPS host). Fallback na bitmap default
# (ružan, ali nikad ne ruši pipeline).
FONT_CANDIDATES = [
    os.environ.get("PIN_FONT", ""),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]


def _load_font(size):
    for path in FONT_CANDIDATES:
        if path and os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap_text(draw, text, font, max_width):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def make_pin_image(image_bytes, title):
    """Vertikalna 2:3 (1000x1500) Pinterest pin-slika iz izvorne slike, s
    naslovom preko tamnog gradijenta u donjem dijelu (čitljivost + CTR)."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = img.size
    ratio = width / height

    if ratio > PIN_RATIO:
        new_width = round(height * PIN_RATIO)
        left = (width - new_width) // 2
        img = img.crop((left, 0, left + new_width, height))
    elif ratio < PIN_RATIO:
        new_height = round(width / PIN_RATIO)
        top = (height - new_height) // 2
        img = img.crop((0, top, width, top + new_height))
    img = img.resize((PIN_WIDTH, PIN_HEIGHT), Image.LANCZOS)

    draw = ImageDraw.Draw(img, "RGBA")

    # Tamni gradijent preko donjih ~42% za čitljivost teksta bez obzira na sliku.
    band_top = int(PIN_HEIGHT * 0.58)
    for y in range(band_top, PIN_HEIGHT):
        alpha = int(225 * (y - band_top) / (PIN_HEIGHT - band_top))
        draw.line([(0, y), (PIN_WIDTH, y)], fill=(0, 0, 0, alpha))

    margin = 70
    max_text_w = PIN_WIDTH - 2 * margin
    font_size = 70
    font = _load_font(font_size)
    lines = _wrap_text(draw, title, font, max_text_w)
    while len(lines) > 4 and font_size > 44:
        font_size -= 6
        font = _load_font(font_size)
        lines = _wrap_text(draw, title, font, max_text_w)

    line_h = font.getbbox("Ag")[3] + 16
    brand_gap = 70
    y = PIN_HEIGHT - margin - brand_gap - line_h * len(lines)
    for line in lines:
        # tanka sjena za kontrast na svijetlim slikama
        draw.text((margin + 2, y + 2), line, font=font, fill=(0, 0, 0, 160))
        draw.text((margin, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_h

    draw.text((margin, PIN_HEIGHT - margin - 42), "THEDOGHABIT.COM",
              font=_load_font(34), fill=(255, 209, 92, 255))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue(), "image/jpeg"


def get_categories(env):
    def do_call():
        status, body = http_request("GET", f"{env['WP_URL']}/wp-json/wp/v2/categories?per_page=100")
        return {c["slug"]: c["id"] for c in json.loads(body)}

    return retry(do_call, what="WP categories fetch")


def get_or_create_tag_id(slug, env):
    def do_lookup():
        status, body = http_request("GET", f"{env['WP_URL']}/wp-json/wp/v2/tags?slug={slug}")
        return json.loads(body)

    existing = retry(do_lookup, what=f"WP tag lookup '{slug}'")
    if existing:
        return existing[0]["id"]

    def do_create():
        payload = json.dumps({"name": slug, "slug": slug}).encode()
        headers = wp_auth_header(env)
        headers["Content-Type"] = "application/json"
        status, body = http_request("POST", f"{env['WP_URL']}/wp-json/wp/v2/tags",
                                     headers=headers, data=payload)
        return json.loads(body)["id"]

    return retry(do_create, what=f"WP tag create '{slug}'")


def post_exists(slug, env):
    def do_call():
        status, body = http_request("GET", f"{env['WP_URL']}/wp-json/wp/v2/posts?slug={slug}")
        return len(json.loads(body)) > 0

    return retry(do_call, what="WP post-exists check")


def get_related_posts(category_id, env, limit=3):
    def do_call():
        url = f"{env['WP_URL']}/wp-json/wp/v2/posts?categories={category_id}&per_page={limit}&orderby=date"
        status, body = http_request("GET", url)
        return [{"title": p["title"]["rendered"], "link": p["link"]} for p in json.loads(body)]

    return retry(do_call, what="WP related posts fetch")


def build_product_picks(topic, article):
    """Amazon search preporuke za gear postove ({name, query} parovi). Preferira
    'product_picks' iz Lumenta outputa (ako ih tool ikad počne vraćati); fallback
    su keywordi teme kao search upiti. Same linkove (i affiliate tag) gradi WP
    mu-plugin thedoghabit-affiliate.php u trenutku renderiranja — tako se tag
    može postaviti naknadno i vrijedi retroaktivno za sve gear postove."""
    import re

    picks = []
    for p in article.get("product_picks") or []:
        name = (p.get("name") or "").strip()
        query = (p.get("query") or name).strip()
        if name and query:
            picks.append({"name": name, "query": query})

    if not picks:
        taken = []
        for kw in topic.get("keywords") or []:
            # "best dog harness for pulling" → "Dog Harness For Pulling";
            # informacijski sufiksi ("buying guide", "review"...) nisu proizvodi.
            clean = re.sub(r"^(best|top|choosing|picking)\s+(an?\s+|the\s+)?", "",
                           kw.strip(), flags=re.IGNORECASE)
            clean = re.sub(r"\s+(buying guide|guide|reviews?|benefits|chart|recommendations?)$", "",
                           clean, flags=re.IGNORECASE).strip()
            lower = clean.lower()
            # Fraze koje opisuju psa/problem, a ne proizvod ("fast eating dog"),
            # i generičke fraze već pokrivene specifičnijim pickom ("harness"
            # nakon "no-pull harness") ne postaju kutije.
            if not clean or lower.endswith(("dog", "dogs")):
                continue
            if any(lower in t or t in lower for t in taken):
                continue
            taken.append(lower)
            name = re.sub(r"\bGps\b", "GPS", clean.title())
            picks.append({"name": name, "query": clean})

    return picks[:4]


def build_content(article_html, faq, related):
    parts = [article_html]

    if faq:
        parts.append("<h2>FAQ</h2>")
        for item in faq:
            parts.append(f"<h3>{item['question']}</h3><p>{item['answer']}</p>")

    if related:
        parts.append("<h2>Related posts</h2><ul>")
        for r in related:
            parts.append(f'<li><a href="{r["link"]}">{r["title"]}</a></li>')
        parts.append("</ul>")

    return "\n".join(parts)


def create_post(title, slug, content, excerpt, category_id, media_id, status, env, tag_ids=None, meta=None):
    def do_create():
        payload = json.dumps({
            "title": title,
            "slug": slug,
            "content": content,
            "excerpt": excerpt,
            "status": status,
            "categories": [category_id],
            "featured_media": media_id,
            "tags": tag_ids or [],
            "meta": meta or {},
        }).encode()
        headers = wp_auth_header(env)
        headers["Content-Type"] = "application/json"
        resp_status, body = http_request("POST", f"{env['WP_URL']}/wp-json/wp/v2/posts",
                                          headers=headers, data=payload)
        return json.loads(body)

    return retry(do_create, what="WP post create")


def main():
    parser = argparse.ArgumentParser(description="Generiraj i objavi jedan post na thedoghabit.com")
    parser.add_argument("--dry-run", action="store_true",
                         help="mock Lumenta/Pexels odgovori, uvijek draft, bez pravih API poziva")
    parser.add_argument("--publish", action="store_true",
                         help="objavi kao 'publish' umjesto 'draft' (ignorira se u --dry-run)")
    args = parser.parse_args()

    env = load_env(ENV_PATH)
    topics = load_topics()

    pending = [t for t in topics if t["status"] == "pending"]
    if len(pending) < REFILL_THRESHOLD:
        log(f"Queue ispod praga ({len(pending)}/{REFILL_THRESHOLD} pending) — pokrećem auto-refill.")
        topics = refill_topics(topics, env, args.dry_run)
        pending = [t for t in topics if t["status"] == "pending"]
    if not pending:
        log("Nema 'pending' tema u topics.json — ništa za objaviti.")
        send_alert("topics.json queue je prazan (refill nije pomogao) — post NIJE objavljen.", env)
        sys.exit(1)

    topic = pending[0]
    log(f"Odabrana tema: '{topic['title_seed']}' ({topic['category']})")

    try:
        article = call_lumenta(topic, env, args.dry_run)

        # Make.com HTTP modul slaže JSON body ručno (jsonString, bez escapanja)
        # iz RSS naslova i excerpta — dvostruki navodnik ili newline u tim
        # poljima ruši JSON i lomi IG/Pinterest granu (viđeno 17.7.2026.).
        # Sanitiziraj na izvoru: navodnici → apostrofi, newline → razmak.
        for key in ("title", "meta_title", "meta_description"):
            if article.get(key):
                article[key] = article[key].replace('"', "'").replace("\n", " ").strip()

        slug = article["slug"] or slugify(article["title"])

        if post_exists(slug, env):
            log(f"Post sa slugom '{slug}' već postoji — preskačem (idempotentnost).")
            if not args.dry_run:
                topic["status"] = "done"
                save_topics(topics)
                log("Tema označena kao 'done' u topics.json (post već postoji).")
            sys.exit(0)

        raw_image_bytes, content_type = fetch_featured_image(article["title"], env, args.dry_run, tags=topic.get("tags"), topic=topic)
        image_bytes, content_type = process_image(raw_image_bytes)
        log(f"  slika obrađena: {len(image_bytes)} bajtova, {content_type}")
        ext = mimetypes.guess_extension(content_type) or ".jpg"
        media_id, _ = upload_media(image_bytes, f"{slug}{ext}", content_type, env)
        log(f"  slika uploadana, media_id={media_id}")

        # Vertikalna pin-slika za Pinterest (feed je izloži u <media:content>).
        pin_url = None
        try:
            pin_bytes, pin_ct = make_pin_image(raw_image_bytes, article["title"])
            pin_ext = mimetypes.guess_extension(pin_ct) or ".jpg"
            _, pin_url = upload_media(pin_bytes, f"{slug}-pin{pin_ext}", pin_ct, env)
            log(f"  pin-slika uploadana: {pin_url}")
        except Exception as e:
            # Pin-slika nije kritična — bez nje Pinterest fallbacka na featured.
            log(f"  pin-slika preskočena (nije kritično): {e}")

        categories = get_categories(env)
        category_id = categories.get(topic["category"])
        if category_id is None:
            raise RuntimeError(f"Nepoznata kategorija '{topic['category']}' — nema je na WP-u.")

        related = get_related_posts(category_id, env, limit=3)
        content = build_content(article["article_html"], article.get("faq", []), related)

        tag_ids = [get_or_create_tag_id(slug, env) for slug in topic.get("tags", [])]

        status = "draft"
        if args.publish and not args.dry_run:
            status = "publish"

        # Rank Math meta + FAQ schema idu kao post meta — REST ih prihvaća
        # preko mu-plugina thedoghabit-rest-meta.php (register_post_meta).
        keywords = topic.get("keywords") or []
        meta = {
            "rank_math_title": article.get("meta_title") or article["title"],
            "rank_math_description": article.get("meta_description", ""),
            "rank_math_focus_keyword": keywords[0] if keywords else "",
            "thedoghabit_faq": json.dumps(article.get("faq") or [], ensure_ascii=False),
        }
        if pin_url:
            meta["thedoghabit_pin_image"] = pin_url

        # Gear postovi dobivaju "Recommended Gear" affiliate sekciju — renderira
        # je mu-plugin thedoghabit-affiliate.php iz ovog meta polja.
        if topic["category"] == "gear":
            picks = build_product_picks(topic, article)
            if picks:
                meta["thedoghabit_products"] = json.dumps(picks, ensure_ascii=False)
                log(f"  gear post: {len(picks)} product pick(ova) u thedoghabit_products meta.")

        post = create_post(
            title=article["title"],
            slug=slug,
            content=content,
            excerpt=article.get("meta_description", ""),
            category_id=category_id,
            media_id=media_id,
            status=status,
            env=env,
            tag_ids=tag_ids,
            meta=meta,
        )
        log(f"Post kreiran: id={post['id']} status={post['status']} link={post.get('link')}")

    except Exception as e:
        log(f"GREŠKA — post NIJE objavljen: {e}")
        send_alert(f"GREŠKA — post NIJE objavljen ('{topic['title_seed']}'): {e}", env)
        sys.exit(1)

    if args.dry_run:
        log("[dry-run] tema NIJE označena kao 'done' — topics.json netaknut.")
        return
    topic["status"] = "done"
    save_topics(topics)
    log("Tema označena kao 'done' u topics.json.")


if __name__ == "__main__":
    main()
