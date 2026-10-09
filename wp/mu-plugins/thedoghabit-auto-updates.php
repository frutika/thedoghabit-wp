<?php
/**
 * Plugin Name: The Dog Habit — Automatska ažuriranja
 * Description: Uključuje automatska ažuriranja jezgre (i major) i svih pluginova.
 *
 * Kontekst (10/2026): 23.–25.7. javni exploit (wp2shell) za WP 7.0.1 je kroz
 * neautenticirani REST batch + krivotvoreni customize_changeset napravio tri
 * admin računa. Jezgra je ručno nadograđena tek 19.8.; minor auto-update
 * pokriva samo 7.0.x, a pluginovi se nisu ažurirali sami uopće.
 *
 * Teme nisu uključene: kadence je u kontejneru u vlasništvu roota pa je WP
 * ne može prepisati.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

add_filter( 'allow_major_auto_core_updates', '__return_true' );
add_filter( 'allow_minor_auto_core_updates', '__return_true' );
add_filter( 'auto_update_plugin', '__return_true' );
