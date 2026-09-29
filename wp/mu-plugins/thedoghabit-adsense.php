<?php
/**
 * Plugin Name: The Dog Habit — AdSense
 * Description: AdSense verifikacijski/auto-ads script u <head> + /ads.txt.
 *
 * Publisher ID je jedno mjesto (konstanta ispod). Script ide na sve javne
 * stranice (ne admin, ne login) jer AdSense verifikacija provjerava početnu
 * stranicu, a auto ads trebaju isti tag svugdje.
 *
 * ads.txt: WP obrađuje /ads.txt kroz index.php (fajl ne postoji u webrootu),
 * pa ga ovdje posluživamo kao text/plain — bez diranja webroota u kontejneru.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

const THEDOGHABIT_ADSENSE_CLIENT = 'ca-pub-7184782062629906';

add_action( 'wp_head', function () {
	if ( is_admin() ) {
		return;
	}
	printf(
		'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=%s" crossorigin="anonymous"></script>' . "\n",
		esc_attr( THEDOGHABIT_ADSENSE_CLIENT )
	);
}, 1 );

add_action( 'init', function () {
	$path = wp_parse_url( $_SERVER['REQUEST_URI'] ?? '', PHP_URL_PATH );
	if ( '/ads.txt' !== $path ) {
		return;
	}
	$pub = substr( THEDOGHABIT_ADSENSE_CLIENT, strlen( 'ca-' ) ); // pub-XXXXXXXX
	status_header( 200 );
	header( 'Content-Type: text/plain; charset=utf-8' );
	header( 'Cache-Control: public, max-age=86400' );
	echo "google.com, {$pub}, DIRECT, f08c47fec0942fa0\n";
	exit;
}, 0 );
