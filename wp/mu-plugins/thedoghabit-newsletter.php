<?php
/**
 * Plugin Name: The Dog Habit — Newsletter capture
 * Description: Sprema email prijave s naslovnice u vlastitu tablicu (bez vanjskog
 *              servisa). REST: POST /wp-json/thedoghabit/v1/subscribe. Popis +
 *              CSV export pod Tools -> Subscribers.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

const TDH_NL_DB_VERSION = '1';
const TDH_NL_OPTION     = 'tdh_newsletter_db_version';

function tdh_nl_table() {
	global $wpdb;
	return $wpdb->prefix . 'tdh_subscribers';
}

// mu-plugin nema activation hook — kreiraj tablicu jednom, guardano opcijom.
add_action( 'init', function () {
	if ( get_option( TDH_NL_OPTION ) === TDH_NL_DB_VERSION ) {
		return;
	}
	global $wpdb;
	require_once ABSPATH . 'wp-admin/includes/upgrade.php';
	$table   = tdh_nl_table();
	$charset = $wpdb->get_charset_collate();
	dbDelta(
		"CREATE TABLE {$table} (
			id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
			email VARCHAR(190) NOT NULL,
			source VARCHAR(100) NOT NULL DEFAULT 'homepage',
			ip VARCHAR(45) NOT NULL DEFAULT '',
			created_at DATETIME NOT NULL,
			PRIMARY KEY (id),
			UNIQUE KEY email (email)
		) {$charset};"
	);
	update_option( TDH_NL_OPTION, TDH_NL_DB_VERSION );
} );

add_action( 'rest_api_init', function () {
	register_rest_route( 'thedoghabit/v1', '/subscribe', [
		'methods'             => 'POST',
		'permission_callback' => '__return_true',
		'callback'            => 'tdh_nl_subscribe',
		'args'                => [
			'email'   => [ 'type' => 'string', 'required' => true ],
			'website' => [ 'type' => 'string', 'required' => false ], // honeypot
		],
	] );
} );

function tdh_nl_client_ip() {
	// Iza Cloudflarea pravi IP je u CF-Connecting-IP; fallback REMOTE_ADDR.
	$ip = $_SERVER['HTTP_CF_CONNECTING_IP'] ?? $_SERVER['REMOTE_ADDR'] ?? '';
	return substr( sanitize_text_field( $ip ), 0, 45 );
}

function tdh_nl_subscribe( WP_REST_Request $req ) {
	// Honeypot: boti popune skriveno 'website' polje -> tiho "uspjeh", bez zapisa.
	if ( trim( (string) $req->get_param( 'website' ) ) !== '' ) {
		return new WP_REST_Response( [ 'ok' => true ], 200 );
	}

	$email = sanitize_email( (string) $req->get_param( 'email' ) );
	if ( ! $email || ! is_email( $email ) ) {
		return new WP_REST_Response( [ 'ok' => false, 'error' => 'Please enter a valid email address.' ], 400 );
	}

	// Lagani rate-limit po IP-u (max 5 / 10 min).
	$ip = tdh_nl_client_ip();
	if ( $ip ) {
		$key = 'tdh_nl_rl_' . md5( $ip );
		$n   = (int) get_transient( $key );
		if ( $n >= 5 ) {
			return new WP_REST_Response( [ 'ok' => false, 'error' => 'Too many attempts. Please try again later.' ], 429 );
		}
		set_transient( $key, $n + 1, 10 * MINUTE_IN_SECONDS );
	}

	global $wpdb;
	// INSERT IGNORE: duplikat emaila (UNIQUE) tretiramo kao uspjeh (idempotentno).
	$wpdb->query(
		$wpdb->prepare(
			'INSERT IGNORE INTO ' . tdh_nl_table() . ' (email, source, ip, created_at) VALUES (%s, %s, %s, %s)',
			$email,
			'homepage',
			$ip,
			current_time( 'mysql' )
		)
	);

	return new WP_REST_Response( [ 'ok' => true ], 200 );
}

// Admin: Tools -> Subscribers (popis + CSV export).
add_action( 'admin_menu', function () {
	add_management_page( 'Subscribers', 'Subscribers', 'manage_options', 'tdh-subscribers', 'tdh_nl_admin_page' );
} );

function tdh_nl_admin_page() {
	if ( ! current_user_can( 'manage_options' ) ) {
		return;
	}
	global $wpdb;
	$table      = tdh_nl_table();
	$count      = (int) $wpdb->get_var( "SELECT COUNT(*) FROM {$table}" );
	$rows       = $wpdb->get_results( "SELECT email, source, created_at FROM {$table} ORDER BY created_at DESC LIMIT 100" );
	$export_url = wp_nonce_url( admin_url( 'admin-post.php?action=tdh_export_subscribers' ), 'tdh_export_subscribers' );

	echo '<div class="wrap"><h1>Subscribers</h1>';
	echo '<p><strong>' . esc_html( $count ) . '</strong> total &nbsp; ';
	echo '<a class="button button-primary" href="' . esc_url( $export_url ) . '">Export CSV</a></p>';
	echo '<table class="widefat striped"><thead><tr><th>Email</th><th>Source</th><th>Date</th></tr></thead><tbody>';
	if ( $rows ) {
		foreach ( $rows as $r ) {
			echo '<tr><td>' . esc_html( $r->email ) . '</td><td>' . esc_html( $r->source ) . '</td><td>' . esc_html( $r->created_at ) . '</td></tr>';
		}
	} else {
		echo '<tr><td colspan="3">No subscribers yet.</td></tr>';
	}
	echo '</tbody></table>';
	if ( $count > 100 ) {
		echo '<p>Showing latest 100. Use Export CSV for all.</p>';
	}
	echo '</div>';
}

add_action( 'admin_post_tdh_export_subscribers', function () {
	if ( ! current_user_can( 'manage_options' ) || ! check_admin_referer( 'tdh_export_subscribers' ) ) {
		wp_die( 'Not allowed.' );
	}
	global $wpdb;
	$rows = $wpdb->get_results( 'SELECT email, source, ip, created_at FROM ' . tdh_nl_table() . ' ORDER BY created_at DESC', ARRAY_A );
	nocache_headers();
	header( 'Content-Type: text/csv; charset=utf-8' );
	header( 'Content-Disposition: attachment; filename=thedoghabit-subscribers-' . gmdate( 'Ymd' ) . '.csv' );
	$out = fopen( 'php://output', 'w' );
	fputcsv( $out, [ 'email', 'source', 'ip', 'created_at' ] );
	foreach ( $rows as $r ) {
		fputcsv( $out, $r );
	}
	fclose( $out );
	exit;
} );
