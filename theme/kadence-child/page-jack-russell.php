<?php
/**
 * Breed hub: Jack Russell Terrier — all posts tagged "jack-russell".
 */

get_header();
?>

<div class="tdh-home tdh-breed-hub">

	<section class="tdh-hero tdh-hero-empty">
		<div class="tdh-hero-inner">
			<div class="tdh-hero-content">
				<span class="tdh-hero-cat">Breed Guide</span>
				<h1 class="tdh-hero-title">Jack Russell Terrier</h1>
				<p class="tdh-hero-excerpt">Everything about living with a Jack Russell Terrier — training the high-energy genius, common behavior quirks, gear that survives them, and daily routines that actually tire them out.</p>
			</div>
		</div>
	</section>

	<section class="tdh-latest-posts">
		<div class="tdh-latest-posts-inner">
			<h2 class="tdh-section-title">Jack Russell Terrier guides</h2>
			<?php
			$tdh_jrt = new WP_Query( [
				'post_type'      => 'post',
				'post_status'    => 'publish',
				'posts_per_page' => 12,
				'tag'            => 'jack-russell',
			] );
			tdh_post_grid( $tdh_jrt, 'New Jack Russell Terrier guides are on the way — check back soon.' );
			?>
		</div>
	</section>

</div>

<?php
get_footer();
