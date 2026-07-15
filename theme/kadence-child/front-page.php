<?php
/**
 * Front page: hero + newsletter signup + latest posts + category grid.
 */

get_header();

$tdh_hero_image    = get_theme_mod( 'tdh_hero_image', '' );
$tdh_hero_title    = get_theme_mod( 'tdh_hero_title', 'The Dog Habit' );
$tdh_hero_subtitle = get_theme_mod( 'tdh_hero_subtitle', 'Practical training, behavior tips, and daily habits for happier dogs.' );
?>

<div class="tdh-home">

	<section class="tdh-hero<?php echo $tdh_hero_image ? '' : ' tdh-hero-empty'; ?>">
		<div class="tdh-hero-inner">
			<?php if ( $tdh_hero_image ) : ?>
				<div class="tdh-hero-media">
					<img src="<?php echo esc_url( $tdh_hero_image ); ?>" alt="<?php echo esc_attr( $tdh_hero_title ); ?>" />
				</div>
			<?php endif; ?>
			<div class="tdh-hero-content">
				<h1 class="tdh-hero-title"><?php echo esc_html( $tdh_hero_title ); ?></h1>
				<p class="tdh-hero-excerpt"><?php echo esc_html( $tdh_hero_subtitle ); ?></p>
				<a href="<?php echo esc_url( home_url( '/blog/' ) ); ?>" class="tdh-pill-button">Browse all guides</a>
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
		<h2 class="tdh-section-title">Latest posts</h2>
		<?php
		$tdh_latest = new WP_Query( [
			'post_type'      => 'post',
			'post_status'    => 'publish',
			'posts_per_page' => 6,
		] );
		tdh_post_grid( $tdh_latest, 'New guides are on the way — check back soon.' );
		?>
	</section>

	<section class="tdh-categories">
		<h2 class="tdh-section-title">Browse by category</h2>
		<div class="tdh-category-grid">
			<?php
			$tdh_categories = get_categories( [
				'hide_empty' => false,
				'exclude'    => [ 1 ], // Uncategorized
			] );

			foreach ( $tdh_categories as $tdh_cat ) :
				$tdh_recent = get_posts( [
					'category'       => $tdh_cat->term_id,
					'posts_per_page' => 1,
					'post_status'    => 'publish',
				] );
				?>
				<a href="<?php echo esc_url( get_category_link( $tdh_cat->term_id ) ); ?>" class="tdh-category-card">
					<?php if ( $tdh_recent && has_post_thumbnail( $tdh_recent[0]->ID ) ) : ?>
						<div class="tdh-category-card-media"><?php echo get_the_post_thumbnail( $tdh_recent[0]->ID, 'medium' ); ?></div>
					<?php endif; ?>
					<span class="tdh-category-name"><?php echo esc_html( $tdh_cat->name ); ?></span>
					<?php if ( $tdh_cat->description ) : ?>
						<p class="tdh-category-desc"><?php echo esc_html( $tdh_cat->description ); ?></p>
					<?php endif; ?>
				</a>
			<?php endforeach; ?>
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
