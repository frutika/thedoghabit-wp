#!/usr/bin/env python3
"""Primjena ručne prerade članka iz content/rework/<slug>/ na WordPress.

Mapa prerade sadrži:
  post.html  — novi sadržaj; {{img:KLJUČ}} mjesta za slike
  meta.json  — post_id, slug, post_title, meta (rank_math_*, thedoghabit_*), images

Bez --apply radi samo provjeru i ispiše plan (ništa ne dira u WP-u).
S --apply:
  1. sigurnosna kopija postojećeg sadržaja, naslova i meta polja u /root/backups/
  2. slike: auto-orient, makni EXIF (GPS/model), max 1200 px, JPEG q82 → wp media import
  3. {{img:KLJUČ}} → <figure> s wp_get_attachment_image (srcset, width/height, lazy)
  4. wp post update (sadržaj, naslov) + meta polja
  5. obriše Rank Math sitemap cache datoteke (invalidate_storage ih ne briše)

Ponovni --apply ne uvozi slike ponovno: ID-ovi medija spremaju se u .media.json
u mapi prerade (nije u gitu, vezano uz ovaj server).

Pokretanje (na VPS-u, iz /opt/thedoghabit):
  python3 scripts/apply_rework.py content/rework/best-toys-jack-russell-terrier
  python3 scripts/apply_rework.py content/rework/best-toys-jack-russell-terrier --apply
"""
import argparse
import html
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # --root mijenja (docker compose + assets)
IMG_RE = re.compile(r"\{\{img:([A-Za-z0-9_-]+)\}\}")
BACKUP_ROOT = Path("/root/backups")


def wp(*args, stdin=None):
    cmd = ["docker", "compose", "exec", "-T", "wordpress", "wp", "--allow-root", *args]
    res = subprocess.run(cmd, cwd=ROOT, input=stdin, capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"wp {' '.join(args[:3])} pao: {res.stderr.strip() or res.stdout.strip()}")
    return res.stdout.strip()


def load(rework_dir):
    meta = json.loads((rework_dir / "meta.json").read_text(encoding="utf-8"))
    body = (rework_dir / "post.html").read_text(encoding="utf-8")
    keys = IMG_RE.findall(body)
    images = meta.get("images", {})
    errors = []
    if len(keys) != len(set(keys)):
        errors.append("isti {{img:…}} ključ se ponavlja u post.html")
    for k in keys:
        if k not in images:
            errors.append(f"{{{{img:{k}}}}} nema zapis u meta.json images")
        elif not (ROOT / images[k]["src"]).is_file():
            errors.append(f"slika ne postoji: {images[k]['src']}")
        elif not images[k].get("alt"):
            errors.append(f"slika {k} nema alt")
    for k in images:
        if k not in keys:
            errors.append(f"slika {k} je u meta.json, ali je nema u post.html")
    if "<h2>FAQ</h2>" not in body:
        # affiliate mu-plugin ubacuje Recommended Gear ispred ovog doslovnog markera
        errors.append("post.html nema doslovni '<h2>FAQ</h2>' marker")
    if errors:
        sys.exit("Provjera pala:\n  " + "\n  ".join(errors))
    return meta, body, keys


def prepare_image(src, out):
    subprocess.run(["convert", str(src), "-auto-orient", "-strip", "-resize", "1200x1200>",
                    "-quality", "82", "-sampling-factor", "4:2:0", "-interlace", "JPEG", str(out)],
                   check=True)


def backup(post_id, slug):
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = BACKUP_ROOT / f"rework-{slug}-{ts}"
    d.mkdir(parents=True)
    (d / "post.json").write_text(wp("post", "get", str(post_id), "--format=json"), encoding="utf-8")
    (d / "meta.json").write_text(wp("post", "meta", "list", str(post_id), "--format=json"), encoding="utf-8")
    return d


def import_images(meta, keys, rework_dir):
    state_file = rework_dir / ".media.json"
    media = json.loads(state_file.read_text()) if state_file.exists() else {}
    post_id = str(meta["post_id"])
    with tempfile.TemporaryDirectory() as tmp:
        for k in keys:
            if k in media:
                print(f"  slika {k}: već uvezena (id {media[k]})")
                continue
            img = meta["images"][k]
            local = Path(tmp) / f"{meta['slug']}-{k}.jpg"
            prepare_image(ROOT / img["src"], local)
            remote = f"/tmp/{local.name}"
            subprocess.run(["docker", "compose", "cp", str(local), f"wordpress:{remote}"],
                           cwd=ROOT, check=True, capture_output=True)
            att = wp("media", "import", remote, f"--post_id={post_id}", f"--title={img['alt']}",
                     f"--alt={img['alt']}", f"--caption={img.get('caption', '')}", "--porcelain")
            subprocess.run(["docker", "compose", "exec", "-T", "wordpress", "rm", "-f", remote],
                           cwd=ROOT, check=True)
            media[k] = int(att)
            state_file.write_text(json.dumps(media, indent=2))  # nakon svake, da prekid ne udvostruči
            print(f"  slika {k}: uvezena (id {att}, {local.stat().st_size // 1024} KB)")
    return media


def figure(att_id, img):
    tag = wp("eval", f'echo wp_get_attachment_image({int(att_id)}, "large", false, ["loading" => "lazy"]);')
    cap = img.get("caption", "")
    cap_html = f"<figcaption>{html.escape(cap)}</figcaption>" if cap else ""
    return f'<figure class="wp-block-image size-large tdh-own-photo">{tag}{cap_html}</figure>'


def main():
    global ROOT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rework_dir", type=Path)
    ap.add_argument("--apply", action="store_true", help="stvarno primijeni (inače samo provjera)")
    ap.add_argument("--root", type=Path, default=ROOT,
                    help="deploy mapa s docker-compose.yml i assets/ (zadano: repo skripte)")
    a = ap.parse_args()
    ROOT = a.root.resolve()
    rework_dir = a.rework_dir.resolve()

    meta, body, keys = load(rework_dir)
    post_id = meta["post_id"]
    current_slug = wp("post", "get", str(post_id), "--field=post_name")
    if current_slug != meta["slug"]:
        sys.exit(f"post {post_id} ima slug '{current_slug}', očekivan '{meta['slug']}'")

    words = len(re.sub(r"<[^>]+>", " ", IMG_RE.sub("", body)).split())
    print(f"Post {post_id} ({meta['slug']}): {words} riječi, {len(keys)} slika, "
          f"meta polja: {', '.join(meta.get('meta', {}))}")
    if not a.apply:
        print("Provjera prošla. Ništa nije promijenjeno (dodaj --apply).")
        return

    bdir = backup(post_id, meta["slug"])
    print(f"Sigurnosna kopija: {bdir}")
    media = import_images(meta, keys, rework_dir)
    content = IMG_RE.sub(lambda m: figure(media[m.group(1)], meta["images"][m.group(1)]), body)

    wp("post", "update", str(post_id), "-", f"--post_title={meta['post_title']}", stdin=content)
    for key, value in meta.get("meta", {}).items():
        v = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        wp("post", "meta", "update", str(post_id), key, v)
    print("Sadržaj, naslov i meta ažurirani.")

    wp("eval", 'foreach (glob(WP_CONTENT_DIR . "/uploads/rank-math/rank_math_*.xml") ?: [] as $f) unlink($f);')
    print("Rank Math sitemap cache obrisan.")
    print(f"Povrat: {bdir}/post.json i meta.json")


if __name__ == "__main__":
    main()
