<?php
/**
 * Plugin Name: The Dog Habit — Affiliate gear
 * Description: Renderira "Recommended Gear" sekciju iz post meta 'thedoghabit_products'
 *              (JSON: [{"name","query","note"?}]) s Amazon search linkovima.
 *
 * Linkovi se grade u trenutku renderiranja, ne peku se u post content: affiliate
 * tag živi u optionu 'thedoghabit_amazon_tag' (wp option update thedoghabit_amazon_tag
 * xxxx-20) pa se postavljanjem taga jednom monetiziraju SVI postojeći gear postovi
 * retroaktivno. Dok tag nije postavljen linkovi su obični Amazon search linkovi
 * (bez tag parametra) i affiliate disclosure se ne prikazuje.
 */

add_action('init', function () {
    // JSON string: [{"name": "...", "query": "...", "note": "..."?}, ...]
    register_post_meta('post', 'thedoghabit_products', [
        'show_in_rest'      => true,
        'single'            => true,
        'type'              => 'string',
        'sanitize_callback' => function ($value) {
            $decoded = json_decode((string) $value, true);
            return is_array($decoded) ? wp_json_encode($decoded) : '';
        },
        'auth_callback'     => function () {
            return current_user_can('edit_posts');
        },
    ]);
}, 20);

// Prioritet 20: nakon wpautop-a (10), da sekcija ne prolazi kroz njega i da
// '<h2>FAQ</h2>' marker iz build_content() ostane doslovan za strpos ispod.
add_filter('the_content', function ($content) {
    if (!is_singular('post') || !in_the_loop() || !is_main_query()) {
        return $content;
    }

    $raw = get_post_meta(get_the_ID(), 'thedoghabit_products', true);
    if (!$raw) {
        return $content;
    }
    $products = json_decode($raw, true);
    if (!is_array($products)) {
        return $content;
    }

    $tag   = trim((string) get_option('thedoghabit_amazon_tag', ''));
    $items = '';
    foreach ($products as $product) {
        $name  = trim((string) ($product['name'] ?? ''));
        $query = trim((string) ($product['query'] ?? $name));
        if ($name === '' || $query === '') {
            continue;
        }
        $args = ['k' => $query];
        if ($tag !== '') {
            $args['tag'] = $tag;
        }
        $note = trim((string) ($product['note'] ?? ''));
        $items .= sprintf(
            '<div class="tdh-gear-item"><div class="tdh-gear-info"><h3 class="tdh-gear-name">%s</h3>%s</div>'
            . '<a class="tdh-pill-button tdh-gear-link" href="%s" target="_blank" rel="sponsored nofollow noopener">See top picks on Amazon</a></div>',
            esc_html($name),
            $note !== '' ? '<p class="tdh-gear-note">' . esc_html($note) . '</p>' : '',
            esc_url('https://www.amazon.com/s?' . http_build_query($args))
        );
    }
    if ($items === '') {
        return $content;
    }

    $section = '<div class="tdh-gear-section"><h2>Recommended Gear</h2>' . $items . '</div>';

    // Sekcija ide iznad FAQ-a ako ga post ima (build_content() ga uvijek piše
    // kao doslovni '<h2>FAQ</h2>'), inače na kraj članka.
    $pos = strpos($content, '<h2>FAQ</h2>');
    if ($pos !== false) {
        $content = substr($content, 0, $pos) . $section . "\n" . substr($content, $pos);
    } else {
        $content .= "\n" . $section;
    }

    // FTC/Amazon disclosure iznad sadržaja — samo kad su linkovi stvarno
    // affiliate (tag postavljen); Amazon traži disclosure PRIJE linkova.
    if ($tag !== '') {
        $disclosure = '<p class="tdh-affiliate-disclosure">This post contains affiliate links. '
            . 'If you buy through them, we may earn a small commission at no extra cost to you. '
            . '<a href="' . esc_url(home_url('/affiliate-disclosure/')) . '">Learn more</a>.</p>';
        $content = $disclosure . "\n" . $content;
    }

    return $content;
}, 20);
