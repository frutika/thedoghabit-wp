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

	// filemtime umjesto fiksnog theme Version stringa — Cloudflare kešira
	// style.css po URL-u (uklj. ?ver=), pa statična verzija znači da izmjene
	// ostanu nevidljive do isteka edge cachea (do 4h). filemtime mijenja URL
	// na svaki save, što garantira cache-miss i svjež fetch odmah.
	wp_enqueue_style(
		'kadence-child-style',
		get_stylesheet_uri(),
		[ 'kadence-global', 'kadence-header', 'kadence-content', 'kadence-footer' ],
		filemtime( get_stylesheet_directory() . '/style.css' )
	);

	wp_enqueue_script(
		'tdh-cookie-consent',
		get_stylesheet_directory_uri() . '/cookie-consent.js',
		[],
		filemtime( get_stylesheet_directory() . '/cookie-consent.js' ),
		true
	);
}, 20 );

/**
 * Cookie consent banner + settings modal (GDPR). Markup ide na svaku stranicu
 * (stranice su keširane pa PHP ne zna stanje pristanka) — vidljivošću upravlja
 * cookie-consent.js preko localStorage 'tdh_consent'. Banner je hidden po
 * defaultu da nema bljeska kod posjetitelja s pristankom.
 */
add_action( 'wp_footer', function () {
	?>
	<div id="tdh-cookie-banner" hidden>
		<div class="tdh-cc-banner-inner">
			<p class="tdh-cc-text">
				We use essential cookies to make this site work. With your consent, we may also
				use analytics cookies to understand how the site is used. Read more in our
				<a href="<?php echo esc_url( home_url( '/cookie-policy/' ) ); ?>">Cookie Policy</a>.
			</p>
			<div class="tdh-cc-actions">
				<button type="button" class="tdh-pill-button tdh-cc-accept">Accept all</button>
				<button type="button" class="tdh-cc-btn-secondary tdh-cc-essential">Essential only</button>
				<button type="button" class="tdh-cc-link tdh-cc-open-settings">Cookie settings</button>
			</div>
		</div>
	</div>
	<div id="tdh-cookie-modal" hidden>
		<div class="tdh-cc-modal-card" role="dialog" aria-modal="true" aria-labelledby="tdh-cc-modal-title">
			<button type="button" class="tdh-cc-close" aria-label="Close">&times;</button>
			<h2 id="tdh-cc-modal-title">Cookie settings</h2>
			<p class="tdh-cc-modal-sub">
				Manage how this site may use cookies and similar technologies. Essential cookies
				cannot be turned off because the site does not work without them. See our
				<a href="<?php echo esc_url( home_url( '/cookie-policy/' ) ); ?>">Cookie Policy</a> for details.
			</p>
			<div class="tdh-cc-category">
				<div class="tdh-cc-category-info">
					<h3>Essential</h3>
					<p>Security, load balancing, and saving your cookie preferences.</p>
				</div>
				<span class="tdh-cc-always">Always active</span>
			</div>
			<div class="tdh-cc-category">
				<div class="tdh-cc-category-info">
					<h3>Analytics</h3>
					<p>Help us understand how visitors use the site. We currently use no analytics
					cookies; if we introduce them, they will load only with your consent.</p>
				</div>
				<label class="tdh-cc-switch">
					<input type="checkbox" id="tdh-cc-analytics" />
					<span class="tdh-cc-slider"></span>
				</label>
			</div>
			<div class="tdh-cc-modal-actions">
				<button type="button" class="tdh-pill-button tdh-cc-save">Save preferences</button>
				<button type="button" class="tdh-cc-btn-secondary tdh-cc-modal-accept">Accept all</button>
			</div>
		</div>
	</div>
	<?php
} );

// Preconnect na font CDN-ove — bez ovoga browser otkriva fonts.gstatic.com
// tek nakon što parsira Google Fonts CSS, što gura fontove (i FCP) unatrag.
add_filter( 'wp_resource_hints', function ( $urls, $relation_type ) {
	if ( 'preconnect' === $relation_type ) {
		$urls[] = [ 'href' => 'https://fonts.googleapis.com' ];
		$urls[] = [
			'href'        => 'https://fonts.gstatic.com',
			'crossorigin' => 'anonymous',
		];
	}
	return $urls;
}, 10, 2 );

/**
 * Homepage hero — editable via Customizer so content updates don't need code changes.
 */
add_action( 'customize_register', function ( $wp_customize ) {
	$wp_customize->add_section( 'tdh_hero', [
		'title'    => 'Homepage Hero',
		'priority' => 30,
	] );

	$wp_customize->add_setting( 'tdh_hero_image', [
		'default'           => '',
		'sanitize_callback' => 'esc_url_raw',
	] );
	$wp_customize->add_control( new WP_Customize_Image_Control( $wp_customize, 'tdh_hero_image', [
		'label'   => 'Hero Image',
		'section' => 'tdh_hero',
	] ) );

	$wp_customize->add_setting( 'tdh_hero_title', [
		'default'           => 'The Dog Habit',
		'sanitize_callback' => 'sanitize_text_field',
	] );
	$wp_customize->add_control( 'tdh_hero_title', [
		'label'   => 'Hero Title',
		'section' => 'tdh_hero',
		'type'    => 'text',
	] );

	$wp_customize->add_setting( 'tdh_hero_subtitle', [
		'default'           => 'Practical training, behavior tips, and daily habits for happier dogs.',
		'sanitize_callback' => 'sanitize_text_field',
	] );
	$wp_customize->add_control( 'tdh_hero_subtitle', [
		'label'   => 'Hero Subtitle',
		'section' => 'tdh_hero',
		'type'    => 'text',
	] );
} );

/**
 * Shared post-card markup for grids (homepage latest posts, breed hub pages).
 * Must be called inside a WP_Query loop (relies on the_post() global state).
 */
function tdh_post_card() {
	ob_start();
	?>
	<a href="<?php the_permalink(); ?>" class="tdh-post-card">
		<?php if ( has_post_thumbnail() ) : ?>
			<div class="tdh-post-card-media"><?php the_post_thumbnail( 'medium_large' ); ?></div>
		<?php endif; ?>
		<?php
		$tdh_card_cats = get_the_category();
		if ( $tdh_card_cats ) :
			?>
			<span class="tdh-post-card-cat"><?php echo esc_html( $tdh_card_cats[0]->name ); ?></span>
		<?php endif; ?>
		<h3 class="tdh-post-card-title"><?php the_title(); ?></h3>
		<p class="tdh-post-card-excerpt"><?php echo esc_html( wp_trim_words( get_the_excerpt(), 18 ) ); ?></p>
	</a>
	<?php
	return ob_get_clean();
}

/**
 * Renders a post grid from a WP_Query, or a fallback message when it's empty.
 */
function tdh_post_grid( WP_Query $query, $empty_text ) {
	if ( $query->have_posts() ) {
		echo '<div class="tdh-post-grid">';
		while ( $query->have_posts() ) {
			$query->the_post();
			echo tdh_post_card(); // phpcs:ignore -- escaped inside tdh_post_card()
		}
		wp_reset_postdata();
		echo '</div>';
	} else {
		echo '<p class="tdh-empty-note">' . esc_html( $empty_text ) . '</p>';
	}
}

/**
 * WordPress's default RSS feed doesn't expose the featured image anywhere
 * (no <enclosure>, no <img> in content:encoded) — Make.com's RSS module
 * needs it to offer a mappable "Photo URL" for the Instagram/Pinterest
 * modules (spec.md §5). Emit both <enclosure> AND <media:content> — RSS
 * automation tools vary in which one they parse into a clean URL field,
 * emitting both maximizes the chance Make picks one up cleanly.
 */
add_action( 'rss2_ns', function () {
	echo 'xmlns:media="http://search.yahoo.com/mrss/"' . "\n";
} );

add_action( 'rss2_item', function () {
	if ( ! has_post_thumbnail() ) {
		return;
	}

	$thumbnail_id = get_post_thumbnail_id();
	$image_url    = get_the_post_thumbnail_url( get_the_ID(), 'large' );
	$image_path   = get_attached_file( $thumbnail_id );
	$filesize     = $image_path ? filesize( $image_path ) : 0;
	$mime_type    = get_post_mime_type( $thumbnail_id );
	$image_meta   = wp_get_attachment_image_src( $thumbnail_id, 'large' );

	// Featured (16:9) ide u <enclosure> — Instagram grana u Make-u koristi ovo polje.
	printf(
		'<enclosure url="%s" length="%d" type="%s" />' . "\n",
		esc_url( $image_url ),
		(int) $filesize,
		esc_attr( $mime_type )
	);

	$pin_url = get_post_meta( get_the_ID(), 'thedoghabit_pin_image', true );

	// DRUGI <enclosure> = vertikalna pin-slika (2:3) za Pinterest. Make RSS modul
	// parsira enclosure-e u niz, pa Pinterest grana mapira Enclosures[2], a
	// Instagram ostaje na Enclosures[1] (featured 16:9).
	//
	// Fallback na featured je namjeran: polje MORA uvijek biti popunjeno. Kad je
	// Pinterest jednom dobio prazan URL (media:content mapiranje), Make je pao na
	// validaciji i sam DEAKTIVIRAO cijeli scenarij (25.7.2026.).
	$pin_enclosure_url = $pin_url ?: $image_url;
	$pin_attachment_id = $pin_url ? attachment_url_to_postid( $pin_url ) : $thumbnail_id;
	$pin_path          = $pin_attachment_id ? get_attached_file( $pin_attachment_id ) : '';
	$pin_size          = $pin_path && file_exists( $pin_path ) ? filesize( $pin_path ) : 0;

	printf(
		'<enclosure url="%s" length="%d" type="image/jpeg" />' . "\n",
		esc_url( $pin_enclosure_url ),
		(int) $pin_size
	);

	// Pinterest voli vertikalne slike: ako postoji dedicirana pin-slika
	// (thedoghabit_pin_image, 2:3 s tekstom), izloži NJU i u <media:content>.
	// Inače fallback na featured (16:9).
	if ( $pin_url ) {
		printf(
			'<media:content url="%s" type="image/jpeg" medium="image" width="1000" height="1500" />' . "\n",
			esc_url( $pin_url )
		);
	} else {
		printf(
			'<media:content url="%s" type="%s" medium="image" width="%d" height="%d" />' . "\n",
			esc_url( $image_url ),
			esc_attr( $mime_type ),
			$image_meta ? (int) $image_meta[1] : 0,
			$image_meta ? (int) $image_meta[2] : 0
		);
	}
} );
