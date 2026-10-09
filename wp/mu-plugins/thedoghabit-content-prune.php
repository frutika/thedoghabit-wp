<?php
/**
 * Plugin Name: The Dog Habit — Pročišćavanje sadržaja
 * Description: 301 preusmjeravanja spojenih duplikata i noindex za slabe članke.
 *
 * Kontekst (10/2026): AdSense je odbio stranicu kao "sadržaj niske vrijednosti";
 * Google je od 125 članaka indeksirao 59, a 90 sam odbio. Plan po članku (GSC
 * podaci za 3 mj.) je u PR-u koji je dodao ovaj fajl.
 *
 * - THEDOGHABIT_REDIRECTS: duplikat => članak u koji je spojen (301). Radi po
 *   putanji, pa vrijedi i ako se duplikat kasnije prebaci u draft ili obriše.
 * - THEDOGHABIT_NOINDEX: članci s 0 pojavljivanja u 3 mj., stariji od 3 tjedna.
 *   noindex,follow + izbačeni iz Rank Math sitemapa. Kad se članak preradi
 *   (vlastiti Short, iskustvo, slike), makni ga s popisa.
 *
 * Sve je ovdje, ne u bazi: Rank Math bulk "Set to noindex" na ovoj instalaciji
 * ne upisuje meta, a ovako je promjena pregledna i vraća se jednim revertom.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

const THEDOGHABIT_REDIRECTS = [
	'how-establish-consistent-dog-bedtime-routine' => 'dog-bedtime-routine-help-sleep',
	'best-anxiety-dog-beds-anxious-dogs' => 'best-calming-dog-beds-anxious-dogs',
	'adult-dog-whining-causes-beyond-attention' => 'dog-whining-excessively-causes-solutions',
	'best-elevated-dog-feeders-digestion' => 'best-elevated-feeding-station-dogs',
	'why-sleep-matters-dog-sleep-needs' => 'how-much-sleep-do-dogs-need',
	'loose-leash-walking-dogs-guide' => 'leash-training-stop-dog-pulling',
	'leash-manners-dogs-train-polite-walking' => 'leash-training-stop-dog-pulling',
	'advanced-leash-training-stop-pulling' => 'leash-training-stop-dog-pulling',
	'new-puppy-first-weeks-guide' => 'first-day-with-puppy-guide',
	'puppy-biting-behavior-play-vs-aggression' => 'why-puppy-biting-normal',
	'puppy-biting-during-training-stop' => 'why-puppy-biting-normal',
	'puppy-play-biting-vs-aggression' => 'why-puppy-biting-normal',
	'puppy-socialization-window-guide' => 'puppy-socialization-after-16-weeks',
	'teach-dog-wait-at-door' => 'teach-dog-wait-command-doors',
	'best-waterproof-dog-beds-messy-incontinent' => 'best-washable-dog-beds-easy-clean',
];

const THEDOGHABIT_NOINDEX = [
	'best-hands-free-dog-leash-running-hiking',
	'leash-reactive-dog-training-guide',
	'best-long-line-leashes-dogs-recall',
	'stop-dog-lunging-on-leash',
	'how-to-stop-dog-begging-at-table',
	'why-dog-paces-back-and-forth',
	'post-exercise-routine-dogs-settle-calm',
	'touch-command-dogs-training-guide',
	'meal-schedule-affects-potty-routine',
	'when-to-spay-neuter-puppy-guide',
	'teach-dog-heel-on-left',
	'best-sniff-mat-dogs-enrichment',
	'dog-shaking-when-excited-normal',
	'dog-panting-at-night-causes',
	'dog-morning-routine-prevent-separation-anxiety',
	'dog-wont-heel-on-walks-why',
	'how-to-stop-dog-eating-poop',
	'best-dog-poop-bags-eco-friendly',
	'how-to-puppy-proof-backyard-escape-prevention',
	'best-rope-toys-dogs-safe-play',
	'how-to-trim-dog-nails-home',
	'puppy-swimming-water-safety-guide',
	'dog-yawning-meaning-explained',
	'best-dog-clippers-home-grooming-guide',
	'how-often-bathe-dog-breed-guide',
	'reverse-sneezing-dogs-guide',
	'why-dog-stares-at-you',
	'teach-dog-drop-it-command-training',
	'best-reflective-dog-collar-nighttime-safety',
	'puppy-first-vet-visit-guide',
	'dog-prey-drive-breed-differences-management',
	'how-stop-dog-nipping-during-play',
	'dog-jumping-on-couch-how-to-stop',
	'dog-licking-paws-constantly-when-worry',
	'puppy-teething-age-timeline-guide',
	'teach-dog-leave-it-command',
	'why-dog-sniffing-walks',
	'dog-sleeping-positions-meaning',
	'jack-russell-terrier-exercise-routine-daily-guide',
	'jack-russell-terrier-energy-level-never-tired',
	'jack-russell-terrier-separation-anxiety-causes-fixes',
	'puppy-vaccination-schedule-guide',
	'dog-mental-enrichment-activities-keep-happy',
	'how-to-create-dog-feeding-schedule',
	'dog-daily-routine-guide',
	'how-much-exercise-dogs-need',
	'how-to-stop-dog-jumping',
	'why-does-my-dog-eat-grass',
	'best-harness-jack-russell-terrier',
	'why-dog-suddenly-aggressive-causes',
	'gps-dog-tracker-review-worth-it',
	'resource-guarding-dog-causes-fixes',
	'understanding-dog-body-language-signals',
	'slow-feeder-dog-bowl-help',
	'separation-anxiety-dogs-signs-help',
	'dog-crate-size-guide-choose-right',
	'why-dog-barks-everything-triggers',
	'crate-training-dog-week-by-week',
];

function thedoghabit_request_slug() {
	$path = (string) wp_parse_url( $_SERVER['REQUEST_URI'] ?? '', PHP_URL_PATH );
	return trim( $path, '/' );
}

add_action( 'template_redirect', function () {
	$slug = thedoghabit_request_slug();
	if ( isset( THEDOGHABIT_REDIRECTS[ $slug ] ) ) {
		wp_safe_redirect( home_url( '/' . THEDOGHABIT_REDIRECTS[ $slug ] . '/' ), 301, 'TheDogHabit' );
		exit;
	}
}, 1 );

function thedoghabit_is_noindexed( $post ) {
	$slug = $post ? get_post_field( 'post_name', $post ) : '';
	return $slug && ( in_array( $slug, THEDOGHABIT_NOINDEX, true ) || isset( THEDOGHABIT_REDIRECTS[ $slug ] ) );
}

// Rank Math ispisuje robots meta; core wp_robots je rezerva ako Rank Math nije aktivan.
add_filter( 'rank_math/frontend/robots', function ( $robots ) {
	if ( is_singular( 'post' ) && thedoghabit_is_noindexed( get_queried_object() ) ) {
		$robots['index'] = 'noindex';
	}
	return $robots;
} );

add_filter( 'wp_robots', function ( $robots ) {
	if ( is_singular( 'post' ) && thedoghabit_is_noindexed( get_queried_object() ) ) {
		unset( $robots['index'] );
		$robots['noindex'] = true;
	}
	return $robots;
} );

// Izbaci iz sitemapa: Rank Math i core WP sitemap.
// Rank Math XML sitemap ovdje šalje sirov redak iz $wpdb (stdClass), ne WP_Post,
// pa se provjerava preko ID-ja.
add_filter( 'rank_math/sitemap/entry', function ( $url, $type, $object ) {
	if ( 'post' === $type && is_object( $object ) && ! empty( $object->ID ) && thedoghabit_is_noindexed( (int) $object->ID ) ) {
		return false;
	}
	return $url;
}, 10, 3 );

add_filter( 'wp_sitemaps_posts_query_args', function ( $args, $post_type ) {
	if ( 'post' === $post_type ) {
		$names = array_merge( THEDOGHABIT_NOINDEX, array_keys( THEDOGHABIT_REDIRECTS ) );
		$ids   = get_posts( [ 'post_name__in' => $names, 'fields' => 'ids', 'numberposts' => -1 ] );
		$args['post__not_in'] = array_merge( $args['post__not_in'] ?? [], $ids );
	}
	return $args;
}, 10, 2 );
