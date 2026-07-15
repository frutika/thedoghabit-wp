<?php
/**
 * Front page: hero with latest post + category grid.
 */

get_header();
?>

<div class="tdh-home">

	<?php
	$tdh_latest = new WP_Query( [
		'post_type'      => 'post',
		'post_status'    => 'publish',
		'posts_per_page' => 1,
	] );
	?>

	<?php if ( $tdh_latest->have_posts() ) : $tdh_latest->the_post(); ?>
		<section class="tdh-hero">
			<div class="tdh-hero-inner">
				<?php if ( has_post_thumbnail() ) : ?>
					<a href="<?php the_permalink(); ?>" class="tdh-hero-media"><?php the_post_thumbnail( 'large' ); ?></a>
				<?php endif; ?>
				<div class="tdh-hero-content">
					<?php
					$tdh_cats = get_the_category();
					if ( $tdh_cats ) :
						?>
						<span class="tdh-hero-cat"><?php echo esc_html( $tdh_cats[0]->name ); ?></span>
					<?php endif; ?>
					<h1 class="tdh-hero-title"><a href="<?php the_permalink(); ?>"><?php the_title(); ?></a></h1>
					<p class="tdh-hero-excerpt"><?php echo esc_html( wp_trim_words( get_the_excerpt(), 32 ) ); ?></p>
					<a href="<?php the_permalink(); ?>" class="tdh-pill-button">Read the latest post</a>
				</div>
			</div>
		</section>
		<?php wp_reset_postdata(); ?>
	<?php else : ?>
		<section class="tdh-hero tdh-hero-empty">
			<div class="tdh-hero-inner">
				<div class="tdh-hero-content">
					<h1 class="tdh-hero-title">The Dog Habit</h1>
					<p class="tdh-hero-excerpt">Practical training, behavior tips, and daily habits for happier dogs — new posts coming soon.</p>
				</div>
			</div>
		</section>
	<?php endif; ?>

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

<?php
get_footer();
