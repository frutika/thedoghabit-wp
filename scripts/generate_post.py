#!/usr/bin/env python3
"""generate_post.py — thedoghabit.com cron skripta (spec §3-4).

Uzima prvu 'pending' temu iz topics.json, generira članak preko Lumenta
internog endpointa (§4), featured sliku preko Leonardo API-ja, i objavljuje
na WordPress preko REST API-ja. Bez vanjskih pip ovisnosti (samo stdlib) —
na serveru nema postavljenog pip/venv-a, a HTTP pozivi ovdje ne trebaju ništa
osim urllib-a.

Sigurnosna zadrška: post je UVIJEK status "draft" osim ako je proslijeđen
--publish. --dry-run zaobilazi Lumenta i Leonardo pozive mock/placeholder
podacima, za testiranje cijelog tijeka bez API ključeva.
"""
import argparse
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

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
    # Leonardo (Cloudflare-fronted) vraća 403/error 1010 na urllib-ov default
    # "Python-urllib/x.y" User-Agent — bot-zaštita ga prepoznaje i blokira.
    merged_headers = {"User-Agent": DEFAULT_USER_AGENT, **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=merged_headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return resp.status, body


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

    return retry(do_call, what="Lumenta generate")


def call_leonardo(title, env, dry_run):
    if dry_run:
        log("  [dry-run] koristim placeholder sliku umjesto Leonardo API-ja")
        return PLACEHOLDER_IMAGE.read_bytes(), "image/jpeg"

    def do_call():
        # Leonardo generacija je asinkrona: POST vraća generationId, GET se polla
        # dok status ne postane COMPLETE (obično par sekundi).
        prompt = (f"Photorealistic photo of a dog, context: {title}. "
                  "Natural lighting, no text, no watermark.")
        headers = {
            "Authorization": f"Bearer {env['LEONARDO_API_KEY']}",
            "Content-Type": "application/json",
        }
        payload = json.dumps({
            "prompt": prompt,
            "num_images": 1,
            "width": 1024,
            "height": 768,
        }).encode()
        _, body = http_request("POST", "https://cloud.leonardo.ai/api/rest/v1/generations",
                                headers=headers, data=payload)
        generation_id = json.loads(body)["sdGenerationJob"]["generationId"]

        status_url = f"https://cloud.leonardo.ai/api/rest/v1/generations/{generation_id}"
        for _ in range(30):
            time.sleep(4)
            _, poll_body = http_request("GET", status_url, headers=headers)
            record = json.loads(poll_body)["generations_by_pk"]
            if record["status"] == "COMPLETE":
                image_url = record["generated_images"][0]["url"]
                _, img_bytes = http_request("GET", image_url)
                return img_bytes, "image/jpeg"
            if record["status"] == "FAILED":
                raise RuntimeError(f"Leonardo generacija {generation_id} je FAILED")
        raise RuntimeError(f"Leonardo generacija {generation_id} nije završila na vrijeme")

    return retry(do_call, what="Leonardo image generation")


def wp_auth_header(env):
    token = base64.b64encode(f"{env['WP_USER']}:{env['WP_APP_PASSWORD']}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def upload_media(image_bytes, filename, content_type, env):
    def do_upload():
        headers = wp_auth_header(env)
        headers["Content-Type"] = content_type
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        status, body = http_request("POST", f"{env['WP_URL']}/wp-json/wp/v2/media",
                                     headers=headers, data=image_bytes)
        return json.loads(body)["id"]

    return retry(do_upload, what="WP media upload")


def get_categories(env):
    status, body = http_request("GET", f"{env['WP_URL']}/wp-json/wp/v2/categories?per_page=100")
    return {c["slug"]: c["id"] for c in json.loads(body)}


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
    status, body = http_request("GET", f"{env['WP_URL']}/wp-json/wp/v2/posts?slug={slug}")
    return len(json.loads(body)) > 0


def get_related_posts(category_id, env, limit=3):
    url = f"{env['WP_URL']}/wp-json/wp/v2/posts?categories={category_id}&per_page={limit}&orderby=date"
    status, body = http_request("GET", url)
    return [{"title": p["title"]["rendered"], "link": p["link"]} for p in json.loads(body)]


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


def create_post(title, slug, content, excerpt, category_id, media_id, status, env, tag_ids=None):
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
                         help="mock Lumenta/Leonardo odgovori, uvijek draft, bez pravih API poziva")
    parser.add_argument("--publish", action="store_true",
                         help="objavi kao 'publish' umjesto 'draft' (ignorira se u --dry-run)")
    args = parser.parse_args()

    env = load_env(ENV_PATH)
    topics = load_topics()

    pending = [t for t in topics if t["status"] == "pending"]
    if not pending:
        log("Nema 'pending' tema u topics.json — ništa za objaviti.")
        sys.exit(1)
    if len(pending) < 10:
        log(f"UPOZORENJE: samo {len(pending)} pending tema preostalo u topics.json.")

    topic = pending[0]
    log(f"Odabrana tema: '{topic['title_seed']}' ({topic['category']})")

    try:
        article = call_lumenta(topic, env, args.dry_run)
        slug = article["slug"] or slugify(article["title"])

        if post_exists(slug, env):
            log(f"Post sa slugom '{slug}' već postoji — preskačem (idempotentnost).")
            sys.exit(0)

        image_bytes, content_type = call_leonardo(article["title"], env, args.dry_run)
        ext = mimetypes.guess_extension(content_type) or ".jpg"
        media_id = upload_media(image_bytes, f"{slug}{ext}", content_type, env)
        log(f"  slika uploadana, media_id={media_id}")

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
        )
        log(f"Post kreiran: id={post['id']} status={post['status']} link={post.get('link')}")

    except Exception as e:
        log(f"GREŠKA — post NIJE objavljen: {e}")
        sys.exit(1)

    topic["status"] = "done"
    save_topics(topics)
    log("Tema označena kao 'done' u topics.json.")


if __name__ == "__main__":
    main()
