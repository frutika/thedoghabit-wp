# TheDogHabit.com — Spec za AI Blog Automatizaciju

**Brief za Claude Code sesiju na Hostinger VPS-u.** Cilj: potpuno automatiziran SEO blog u niši pasa (dog training, dog behavior, praktični problemi, rješenja, oprema, navike) s automatskom distribucijom na društvene mreže.

---

## 1. Arhitektura

```
[Cron na VPS-u, 1x dnevno]
        │
        ▼
[Python/Node skripta: generate_post]
        │
        ├─► Lumenta API (SEO Blog Post tool) ──► članak + meta + slug + FAQ
        ├─► Leonardo API ──► featured slika
        └─► WordPress REST API ──► objava posta (status: publish)
                │
                ▼ (RSS / webhook trigger)
        [Make.com scenarij]
                │
                ├─► Lumenta API (Instagram Caption tool) ─► IG post
                ├─► Facebook Page post
                └─► Pinterest pin
```

Podjela odgovornosti:
- **VPS (postojeći Hostinger, 1 vCPU / 4 GB RAM / 50 GB):** WordPress + cron skripta. Ima ~24 GB slobodnog diska i 97% slobodnog RAM-a — dovoljno.
- **Lumenta (lumenta.shop, vlastiti SaaS):** pisac. Treba dodati interni API endpoint (vidi §4).
- **Leonardo API:** featured slike.
- **Make.com (free plan, 1.000 op/mj):** distribucija na društvene mreže. Samo to.
- **Claude Code:** razvoj i održavanje — NE runtime.

---

## 2. WordPress setup (VPS)

1. Novi site na postojećem serveru: `thedoghabit.com` — zaseban vhost (ili Docker container ako su postojeći projekti u Dockeru) + zasebna MySQL baza `thedoghabit`.
2. DNS: domena registrirana na Cloudflare Registraru → Cloudflare proxy uključen (CDN + zaštita, rasterećuje 1 vCPU).
3. SSL: Cloudflare Full (Strict) + origin cert, ili Let's Encrypt.
4. Cache: LiteSpeed Cache ako je LiteSpeed server, inače WP Super Cache.
5. Pluginovi (minimum): SEO plugin (Rank Math ili Yoast), cache, Wordfence ili sličan.
6. WP REST API: kreirati Application Password za korisnika `autopost` (role: Author) — koristi ga skripta.
7. Permalink struktura: `/%postname%/`.

### Kategorije (dog niša)

| Slug | Kategorija | Primjeri tema |
|---|---|---|
| `training` | Dog Training | leash training, recall, sit/stay, potty training |
| `behavior` | Dog Behavior | barking, separation anxiety, aggression, chewing |
| `problems` | Common Problems & Fixes | "why does my dog...", pulling, jumping |
| `gear` | Gear & Equipment | harnesses, crates, toys, recenzije (buduća affiliate zarada) |
| `habits` | Daily Habits & Routines | feeding schedule, exercise, sleep, enrichment |
| `puppies` | Puppy Basics | socialization, first weeks, biting |

---

## 3. Cron skripta `generate_post`

Lokacija: `/opt/thedoghabit/` (ili uz postojeće projekte). Jezik: Python 3 (requests) ili Node — što je već na serveru.

### Tijek

1. **Odabir teme:** iz `topics.json` — redovita queue lista tema s poljima `{title_seed, category, keywords[], status}`. Skripta uzima prvu s `status: pending`, nakon objave označi `done`. Kad queue padne ispod 10, logiraj upozorenje (ili auto-generiraj nove teme dodatnim Lumenta/API pozivom).
2. **Generiranje članka:** POST na Lumenta interni endpoint (§4) s temom, kategorijom, keywordima, `language: en`, `tone: professional/casual mix`. Očekivani output: naslov, full article (HTML/markdown), meta title, meta description, slug, FAQ blok, interni link anchori.
3. **Featured slika:** Leonardo API — prompt izveden iz naslova (fotorealistična slika psa u kontekstu teme, bez teksta na slici). Download → upload na WP media (`/wp-json/wp/v2/media`).
4. **Objava:** POST `/wp-json/wp/v2/posts` — title, content (uključi FAQ na kraju), slug, category ID, featured_media ID, meta polja za SEO plugin, `status: publish`.
5. **Interno linkanje:** iz anchora koje Lumenta vrati, poveži na 2–3 postojeća posta iste kategorije (dohvati preko WP REST search).

### Raspored

```
cron: 0 7 * * *   # 1 post dnevno u 07:00 UTC (globalna publika, jutro EU / noć US)
```
Kasnije po potrebi 2x dnevno. Dodati random delay 0–30 min da objave ne izgledaju robotski.

### Pouzdanost

- Retry s backoffom (3 pokušaja) na svaki API poziv.
- Ako bilo koji korak padne: NE objavljivati polovičan post; logirati u `/var/log/thedoghabit.log` i poslati alert (email ili Telegram bot).
- Idempotentnost: prije objave provjeri postoji li već post s istim slugom.
- Secrets u `.env` (LUMENTA_API_KEY, LEONARDO_API_KEY, WP_APP_PASSWORD) — nikad u kodu.

---

## 4. Lumenta: interni API endpoint (za dodati u Lumentu)

```
POST /api/v1/generate
Authorization: Bearer <api_key>
{
  "tool": "seo_blog_post" | "instagram_caption" | "facebook_ad" | ...,
  "input": {
    "topic": "...",
    "keywords": ["..."],
    "audience": "dog owners, global, English-speaking",
    "tone": "professional",
    "language": "en"
  }
}
→ 200: { "output": { ...structured sections... }, "credits_used": 1, "credits_remaining": n }
```

Napomene:
- API key po korisniku, scope na postojeći credit sustav.
- Ovo kasnije postaje javni Lumenta feature ("API access" — Business plan differentiator).
- Za blog tool osigurati da output sadrži: `title, article_html, meta_title, meta_description, slug, faq[], internal_anchors[]`.

---

## 5. Make.com scenarij (social distribucija)

Trigger: **RSS modul** na `https://thedoghabit.com/feed/` (provjera svakih 15 min, free plan) ili WP webhook.

Koraci po novom postu:
1. HTTP modul → Lumenta API `instagram_caption` (input: naslov + excerpt posta) → caption + hashtagovi.
2. Facebook Pages modul → objavi link + caption.
3. Instagram for Business modul → objavi featured sliku + caption.
4. Pinterest modul → pin s featured slikom + link (Pinterest je jak za dog nišu — dugoročni traffic).

Procjena operacija: 1 post/dan × ~6 op = ~180 op/mj → staje u free plan (limit 1.000).

Preduvjeti (ručno, jednom): FB Page + IG Business account povezani, Pinterest business account.

---

## 6. SEO temelji (postaviti odmah)

- Google Search Console + sitemap (SEO plugin je generira) — submit prvi dan.
- Schema: Article + FAQPage (iz FAQ bloka; Rank Math to radi automatski).
- `robots.txt` normalan, ne blokirati ništa.
- Obavezne statične stranice: About, Contact, Privacy Policy, Affiliate Disclosure (za budući gear affiliate).
- E-E-A-T: autor persona s bio stranicom ("The Dog Habit Team"), disclaimeri kod behavior tema ("consult a certified trainer/vet for serious issues").

---

## 7. Redoslijed izvedbe (za Claude Code sesiju)

1. [ ] Registrirati domenu thedoghabit.com (Cloudflare Registrar) — **korisnik ručno**
2. [ ] WordPress instalacija + vhost/container + DB na VPS-u
3. [ ] Cloudflare DNS + SSL + cache plugin
4. [ ] WP: kategorije, statične stranice, SEO plugin, Application Password
5. [ ] Lumenta: interni `/api/v1/generate` endpoint + API key
6. [ ] Skripta `generate_post` + `topics.json` s prvih 30 tema
7. [ ] Test: ručno pokretanje skripte → 1 probni post (status: draft) → pregled kvalitete → tek onda `publish` + cron
8. [ ] Make.com scenarij + povezivanje social računa — **korisnik ručno autorizira**
9. [ ] Google Search Console + sitemap
10. [ ] Monitoring: log + alert na fail

## 8. Troškovi pogona

| Stavka | Trošak |
|---|---|
| VPS | €0 (postojeći Hostinger) |
| Domena | ~$10/god |
| Lumenta | vlastiti SaaS (samo underlying AI API potrošnja) |
| Leonardo API | ~$9/mj (Basic) ili pay-per-use |
| Make.com | €0 (free plan) |
