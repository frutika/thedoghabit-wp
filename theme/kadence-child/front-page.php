<?php
/**
 * Front page: hero + newsletter signup + latest posts + category grid.
 */

get_header();

$tdh_hero_image    = get_theme_mod( 'tdh_hero_image', '' );
$tdh_hero_title    = get_theme_mod( 'tdh_hero_title', 'The Dog Habit' );
$tdh_hero_subtitle = get_theme_mod( 'tdh_hero_subtitle', 'Practical training, behavior tips, and daily habits for happier dogs.' );
$tdh_blog_url       = get_permalink( get_option( 'page_for_posts' ) ) ?: home_url( '/blog/' );
?>

<div class="tdh-home">

	<section class="tdh-hero<?php echo $tdh_hero_image ? '' : ' tdh-hero-empty'; ?>">
		<div class="tdh-hero-inner">
			<?php if ( $tdh_hero_image ) : ?>
				<div class="tdh-hero-media">
					<?php
					// LCP element: preko attachment ID-a dobivamo srcset (mobitel
					// povuče manju verziju umjesto full JPEG-a) i width/height
					// (rezerviran prostor = bez CLS-a); fetchpriority=high jer je
					// ovo najveći above-the-fold element.
					$tdh_hero_id = attachment_url_to_postid( $tdh_hero_image );
					if ( $tdh_hero_id ) {
						echo wp_get_attachment_image( $tdh_hero_id, 'large', false, [
							'fetchpriority' => 'high',
							'loading'       => 'eager',
							'decoding'      => 'async',
							'alt'           => $tdh_hero_title,
							'sizes'         => '(max-width: 767px) 100vw, 600px',
						] );
					} else {
						// Customizer URL koji nije u Media Library — bez srcset-a,
						// ali barem s prioritetom.
						?>
						<img src="<?php echo esc_url( $tdh_hero_image ); ?>" alt="<?php echo esc_attr( $tdh_hero_title ); ?>" fetchpriority="high" decoding="async" />
						<?php
					}
					?>
				</div>
			<?php endif; ?>
			<div class="tdh-hero-content">
				<h1 class="tdh-hero-title"><?php echo esc_html( $tdh_hero_title ); ?></h1>
				<p class="tdh-hero-excerpt"><?php echo esc_html( $tdh_hero_subtitle ); ?></p>
				<a href="<?php echo esc_url( $tdh_blog_url ); ?>" class="tdh-pill-button">Browse all guides</a>
			</div>
		</div>
	</section>

	<section class="tdh-newsletter">
		<div class="tdh-newsletter-inner">
			<h2 class="tdh-section-title">Get new guides in your inbox</h2>
			<p class="tdh-newsletter-sub">Practical dog training and behavior tips, about once a week. No spam.</p>
			<form class="tdh-newsletter-form">
				<input type="email" name="email" class="tdh-newsletter-input" placeholder="you@example.com" required />
				<button type="submit" class="tdh-pill-button">Subscribe</button>
			</form>
			<p class="tdh-newsletter-note" hidden>Thanks! We'll be in touch soon.</p>
		</div>
	</section>

	<section class="tdh-latest-posts">
		<div class="tdh-latest-posts-inner">
			<h2 class="tdh-section-title">Latest posts</h2>
			<?php
			$tdh_latest = new WP_Query( [
				'post_type'      => 'post',
				'post_status'    => 'publish',
				'posts_per_page' => 6,
			] );
			tdh_post_grid( $tdh_latest, 'New guides are on the way — check back soon.' );
			?>
		</div>
	</section>

	<section class="tdh-categories">
		<div class="tdh-categories-inner">
		<h2 class="tdh-section-title">Browse by category</h2>
		<div class="tdh-category-grid">
			<?php
			$tdh_categories = get_categories( [
				'hide_empty' => false,
				'exclude'    => [ 1 ], // Uncategorized
			] );

			foreach ( $tdh_categories as $tdh_cat ) :
				?>
				<a href="<?php echo esc_url( get_category_link( $tdh_cat->term_id ) ); ?>" class="tdh-category-card">
					<span class="tdh-category-name"><?php echo esc_html( $tdh_cat->name ); ?></span>
					<span class="tdh-category-count">
						<?php
						printf(
							/* translators: %d: number of posts in this category */
							esc_html( _n( '%d guide', '%d guides', $tdh_cat->count, 'kadence-child' ) ),
							(int) $tdh_cat->count
						);
						?>
					</span>
				</a>
			<?php endforeach; ?>
			</div>
		</div>
	</section>

</div>

<script>
document.addEventListener('DOMContentLoaded', function () {
	var form = document.querySelector('.tdh-newsletter-form');
	if (!form) return;
	form.addEventListener('submit', function (e) {
		e.preventDefault();
		form.hidden = true;
		var note = document.querySelector('.tdh-newsletter-note');
		if (note) note.hidden = false;
	});
});
</script>

<?php
get_footer();
