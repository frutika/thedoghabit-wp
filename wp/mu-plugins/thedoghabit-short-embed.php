<?php
/**
 * Plugin Name: The Dog Habit — Short embed
 * Description: Na vrhu članka prikazuje vlastiti YouTube Short iz post meta
 *              'thedoghabit_youtube_id' (postavlja ga generate_video.py nakon uploada).
 *
 * Zašto: AdSense je odbio stranicu kao "sadržaj niske vrijednosti". Short s kanala
 * je jedini sadržaj u članku koji je stvarno naš, pa ide na vrh.
 *
 * Click-to-load fasada: dok posjetitelj ne klikne, ne učitava se ništa s YouTubea
 * (ni iframe ni thumbnail), pa nema kolačića trećih strana i stranica ostaje brza.
 * Klik učitava youtube-nocookie.com iframe s autoplayom.
 *
 * Meta je izvan contenta (kao thedoghabit_faq u thedoghabit-rest-meta.php): kses
 * Authorima striga <iframe> iz contenta, a ovako se embed može maknuti/zamijeniti
 * bez diranja teksta članka.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

const THEDOGHABIT_YT_META = 'thedoghabit_youtube_id';

function thedoghabit_valid_yt_id( $value ) {
	$value = trim( (string) $value );
	return preg_match( '/^[A-Za-z0-9_-]{11}$/', $value ) ? $value : '';
}

add_action( 'init', function () {
	register_post_meta( 'post', THEDOGHABIT_YT_META, [
		'show_in_rest'      => true,
		'single'            => true,
		'type'              => 'string',
		'sanitize_callback' => 'thedoghabit_valid_yt_id',
		'auth_callback'     => function () {
			return current_user_can( 'edit_posts' );
		},
	] );
}, 20 );

add_filter( 'the_content', function ( $content ) {
	if ( ! is_singular( 'post' ) || ! in_the_loop() || ! is_main_query() ) {
		return $content;
	}
	$id = thedoghabit_valid_yt_id( get_post_meta( get_the_ID(), THEDOGHABIT_YT_META, true ) );
	if ( ! $id ) {
		return $content;
	}

	$src = 'https://www.youtube-nocookie.com/embed/' . $id . '?autoplay=1&rel=0&playsinline=1';
	ob_start();
	?>
	<figure class="tdh-short" data-src="<?php echo esc_url( $src ); ?>">
		<button type="button" class="tdh-short-play" aria-label="Play the short video for this article">
			<span class="tdh-short-icon" aria-hidden="true">&#9654;</span>
			<span class="tdh-short-label">Watch the 30-second version</span>
		</button>
		<figcaption>From our YouTube channel. The video loads from YouTube only when you press play.</figcaption>
	</figure>
	<?php
	return ob_get_clean() . $content;
}, 5 );

add_action( 'wp_head', function () {
	if ( ! is_singular( 'post' ) ) {
		return;
	}
	?>
	<style>
		.tdh-short{margin:0 auto 2rem;max-width:340px}
		.tdh-short-play,.tdh-short iframe{display:flex;width:100%;aspect-ratio:9/16;border:0;border-radius:14px}
		.tdh-short-play{flex-direction:column;align-items:center;justify-content:center;gap:.75rem;cursor:pointer;
			background:linear-gradient(160deg,#2b2118,#5a4330);color:#fff;font:600 1rem/1.3 Inter,system-ui,sans-serif}
		.tdh-short-play:hover .tdh-short-icon,.tdh-short-play:focus-visible .tdh-short-icon{transform:scale(1.08)}
		.tdh-short-icon{display:grid;place-items:center;width:64px;height:64px;border-radius:50%;background:#e2614c;
			font-size:1.5rem;padding-left:4px;transition:transform .15s}
		.tdh-short figcaption{margin-top:.5rem;font-size:.8rem;color:#6b6b6b;text-align:center}
	</style>
	<script>
	document.addEventListener('click', function (e) {
		var btn = e.target.closest('.tdh-short-play');
		if (!btn) return;
		var fig = btn.closest('.tdh-short');
		var f = document.createElement('iframe');
		f.src = fig.getAttribute('data-src');
		f.title = 'Short video for this article';
		f.allow = 'autoplay; encrypted-media; picture-in-picture';
		f.allowFullscreen = true;
		btn.replaceWith(f);
	});
	</script>
	<?php
} );
