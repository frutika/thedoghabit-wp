<?php
/**
 * The Dog Habit — Kadence child theme.
 */

add_action( 'wp_enqueue_scripts', function () {
	wp_enqueue_style(
		'tdh-google-fonts',
		'https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Inter:wght@400;500;600;700&display=swap',
		[],
		null
	);

	wp_enqueue_style(
		'kadence-child-style',
		get_stylesheet_uri(),
		[ 'kadence-global', 'kadence-header', 'kadence-content', 'kadence-footer' ],
		wp_get_theme()->get( 'Version' )
	);
}, 20 );
