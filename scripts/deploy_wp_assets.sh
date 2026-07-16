#!/bin/bash
# Kopira child temu i mu-pluginove iz repoa u WP kontejner (docker cp) i
# postavlja www-data vlasništvo. Idempotentno — pokreni nakon svake izmjene
# u theme/ ili wp/mu-plugins/.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WP_CONTAINER="thedoghabit-wordpress-1"

echo "==> Tema (kadence-child)"
docker cp "$PROJECT_DIR/theme/kadence-child" "$WP_CONTAINER":/var/www/html/wp-content/themes/

echo "==> mu-plugins"
docker exec "$WP_CONTAINER" mkdir -p /var/www/html/wp-content/mu-plugins
for f in "$PROJECT_DIR"/wp/mu-plugins/*.php; do
  docker cp "$f" "$WP_CONTAINER":/var/www/html/wp-content/mu-plugins/
done

docker exec "$WP_CONTAINER" chown -R www-data:www-data \
  /var/www/html/wp-content/themes/kadence-child \
  /var/www/html/wp-content/mu-plugins

echo "==> .htaccess cache pravila"
# Blok živi izvan WP markera pa ga core ne dira; zamijeni postojeći (između
# naših BEGIN/END markera) sadržajem iz repoa da izmjene budu idempotentne.
docker exec "$WP_CONTAINER" sed -i '/# BEGIN TheDogHabit cache/,/# END TheDogHabit cache/d' /var/www/html/.htaccess
docker cp "$PROJECT_DIR/wp/htaccess-cache.conf" "$WP_CONTAINER":/tmp/htaccess-cache.conf
docker exec "$WP_CONTAINER" sh -c 'cat /tmp/htaccess-cache.conf >> /var/www/html/.htaccess && rm /tmp/htaccess-cache.conf && chown www-data:www-data /var/www/html/.htaccess'

echo "==> Deploy gotov."
