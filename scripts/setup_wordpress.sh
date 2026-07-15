#!/bin/bash
# Idempotentan WP-CLI bootstrap za thedoghabit.com (§2 speca): core install,
# kategorije, statične stranice, SEO plugin, permalinks, autopost app password.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
set -a
source .env
set +a

NETWORK="thedoghabit_default"
WP_CONTAINER="thedoghabit-wordpress-1"
SITE_URL="http://thedoghabit.com"

wpcli() {
  # wp-config.php čita WORDPRESS_DB_* env varove u runtimeu (getenv_docker), pa ih
  # ad-hoc cli kontejner mora dobiti eksplicitno — inače pada na default host "mysql".
  # --user root: wordpress:cli (Alpine, www-data uid 82) inače ne može pisati u
  # wp-content koji je vlasništvo www-data uid 33 iz wordpress:php8.3-apache (Debian).
  docker run --rm --user root --network "$NETWORK" --volumes-from "$WP_CONTAINER" \
    -e WORDPRESS_DB_HOST=db \
    -e WORDPRESS_DB_NAME="$MYSQL_DATABASE" \
    -e WORDPRESS_DB_USER="$MYSQL_USER" \
    -e WORDPRESS_DB_PASSWORD="$MYSQL_PASSWORD" \
    -w /var/www/html wordpress:cli wp --allow-root "$@"
}

echo "==> Core install"
if wpcli core is-installed 2>/dev/null; then
  echo "already installed, skipping"
else
  wpcli core install \
    --url="$SITE_URL" \
    --title="The Dog Habit" \
    --admin_user="$WP_ADMIN_USER" \
    --admin_password="$WP_ADMIN_PASSWORD" \
    --admin_email="$WP_ADMIN_EMAIL" \
    --skip-email
fi

echo "==> Kategorije"
declare -A CATS=(
  [training]="Dog Training"
  [behavior]="Dog Behavior"
  [problems]="Common Problems & Fixes"
  [gear]="Gear & Equipment"
  [habits]="Daily Habits & Routines"
  [puppies]="Puppy Basics"
)
existing_cats="$(wpcli term list category --field=slug --format=csv)"
for slug in "${!CATS[@]}"; do
  if grep -qx "$slug" <<< "$existing_cats"; then
    echo "  $slug already exists"
  else
    wpcli term create category "${CATS[$slug]}" --slug="$slug"
  fi
done

echo "==> Statične stranice"
declare -A PAGES=(
  [about]="About|The Dog Habit Team shares practical, tested advice on dog training, behavior, and daily care. Our content is written to help everyday dog owners solve real problems — and reviewed with input from certified trainers where relevant."
  [contact]="Contact|Have a question or a topic suggestion? Reach us at hello@thedoghabit.com."
  [privacy-policy]="Privacy Policy|This site collects only the data necessary to operate (e.g. analytics, ad delivery). We do not sell personal information. Full policy details will be expanded here."
  [affiliate-disclosure]="Affiliate Disclosure|The Dog Habit participates in affiliate programs. Some gear and equipment posts contain affiliate links — if you purchase through them, we may earn a small commission at no extra cost to you. We only recommend products we believe are genuinely useful."
)
existing_pages="$(wpcli post list --post_type=page --field=post_name --format=csv)"
for slug in "${!PAGES[@]}"; do
  title="${PAGES[$slug]%%|*}"
  content="${PAGES[$slug]#*|}"
  if grep -qx "$slug" <<< "$existing_pages"; then
    echo "  $slug already exists"
  else
    wpcli post create --post_type=page --post_status=publish \
      --post_title="$title" --post_name="$slug" --post_content="$content"
  fi
done

echo "==> SEO plugin (Rank Math)"
if wpcli plugin is-installed seo-by-rank-math 2>/dev/null; then
  echo "  already installed"
else
  wpcli plugin install seo-by-rank-math --activate
fi

echo "==> Permalinks"
wpcli rewrite structure '/%postname%/' --hard
wpcli rewrite flush --hard

echo "==> autopost korisnik + Application Password"
if wpcli user get autopost 2>/dev/null; then
  echo "  autopost user already exists"
else
  wpcli user create autopost autopost@thedoghabit.com \
    --role=author --user_pass="$(openssl rand -base64 18)"
fi

if grep -q '^WP_APP_PASSWORD=.\+' .env; then
  echo "  WP_APP_PASSWORD already set in .env, skipping generation"
else
  APP_PW=$(wpcli user application-password create autopost generate_post_script --porcelain)
  # macOS/BSD sed nije relevantan ovdje (server je Linux) — GNU sed -i bez backupa
  sed -i "s|^WP_APP_PASSWORD=.*|WP_APP_PASSWORD=${APP_PW}|" .env
  echo "  Application Password generiran i upisan u .env"
fi

echo "==> Gotovo."
