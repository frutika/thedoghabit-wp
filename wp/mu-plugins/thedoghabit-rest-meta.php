<?php
/**
 * Plugin Name: The Dog Habit — REST meta + FAQ schema
 * Description: Izlaže Rank Math SEO polja preko WP REST API-ja (za generate_post.py)
 *              i renderira FAQPage JSON-LD iz post meta polja 'thedoghabit_faq'.
 *
 * FAQ schema ide preko post meta + wp_head umjesto <script> bloka u contentu:
 * autopost korisnik je role Author, a kses filter Authorima striga <script>
 * tagove iz contenta pri spremanju — meta polje taj filter zaobilazi.
 */

add_action('init', function () {
    $auth = function () {
        return current_user_can('edit_posts');
    };

    foreach (['rank_math_title', 'rank_math_description', 'rank_math_focus_keyword'] as $key) {
        register_post_meta('post', $key, [
            'show_in_rest'      => true,
            'single'            => true,
            'type'              => 'string',
            'sanitize_callback' => 'sanitize_text_field',
            'auth_callback'     => $auth,
        ]);
    }

    // JSON string: [{"question": "...", "answer": "..."}, ...]
    register_post_meta('post', 'thedoghabit_faq', [
        'show_in_rest'      => true,
        'single'            => true,
        'type'              => 'string',
        'sanitize_callback' => function ($value) {
            $decoded = json_decode((string) $value, true);
            return is_array($decoded) ? wp_json_encode($decoded) : '';
        },
        'auth_callback'     => $auth,
    ]);
}, 20);

add_action('wp_head', function () {
    if (!is_single()) {
        return;
    }
    $raw = get_post_meta(get_the_ID(), 'thedoghabit_faq', true);
    if (!$raw) {
        return;
    }
    $faq = json_decode($raw, true);
    if (!is_array($faq)) {
        return;
    }

    $entities = [];
    foreach ($faq as $item) {
        if (empty($item['question']) || empty($item['answer'])) {
            continue;
        }
        $entities[] = [
            '@type'          => 'Question',
            'name'           => wp_strip_all_tags($item['question']),
            'acceptedAnswer' => [
                '@type' => 'Answer',
                'text'  => wp_kses_post($item['answer']),
            ],
        ];
    }
    if (!$entities) {
        return;
    }

    $schema = [
        '@context'   => 'https://schema.org',
        '@type'      => 'FAQPage',
        'mainEntity' => $entities,
    ];
    // wp_json_encode po defaultu escapa '/' pa '</script>' u odgovoru ne može
    // prerano zatvoriti ovaj tag.
    echo '<script type="application/ld+json">' . wp_json_encode($schema) . "</script>\n";
});
