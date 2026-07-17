/**
 * The Dog Habit — lagani cookie consent (bez plugina).
 *
 * Pristanak živi u localStorage ('tdh_consent': {essential, analytics, ts}).
 * Banner se prikazuje dok pristanak ne postoji; "Cookie settings" (footer link
 * na #cookie-settings) otvara modal i naknadno. Analitike trenutno NEMA na
 * sajtu — kategorija postoji da budući skript može provjeriti pristanak:
 *   window.tdhConsent()          → {essential, analytics, ts} | null
 *   document event 'tdh-consent' → detail = novi pristanak
 */
(function () {
	var KEY = 'tdh_consent';

	function getConsent() {
		try {
			return JSON.parse(localStorage.getItem(KEY));
		} catch (e) {
			return null;
		}
	}

	window.tdhConsent = getConsent;

	var banner = document.getElementById('tdh-cookie-banner');
	var modal = document.getElementById('tdh-cookie-modal');
	var analyticsToggle = document.getElementById('tdh-cc-analytics');
	if (!banner || !modal) {
		return;
	}

	function saveConsent(analytics) {
		var consent = { essential: true, analytics: !!analytics, ts: Date.now() };
		try {
			localStorage.setItem(KEY, JSON.stringify(consent));
		} catch (e) { /* private mode — banner će se opet pojaviti */ }
		banner.hidden = true;
		closeModal();
		document.dispatchEvent(new CustomEvent('tdh-consent', { detail: consent }));
	}

	function openModal() {
		var current = getConsent();
		analyticsToggle.checked = !!(current && current.analytics);
		modal.hidden = false;
		document.body.classList.add('tdh-cc-modal-open');
	}

	function closeModal() {
		modal.hidden = true;
		document.body.classList.remove('tdh-cc-modal-open');
	}

	if (!getConsent()) {
		banner.hidden = false;
	}

	banner.querySelector('.tdh-cc-accept').addEventListener('click', function () {
		saveConsent(true);
	});
	banner.querySelector('.tdh-cc-essential').addEventListener('click', function () {
		saveConsent(false);
	});
	banner.querySelector('.tdh-cc-open-settings').addEventListener('click', function (e) {
		e.preventDefault();
		openModal();
	});

	modal.querySelector('.tdh-cc-save').addEventListener('click', function () {
		saveConsent(analyticsToggle.checked);
	});
	modal.querySelector('.tdh-cc-modal-accept').addEventListener('click', function () {
		saveConsent(true);
	});
	modal.querySelector('.tdh-cc-close').addEventListener('click', closeModal);
	modal.addEventListener('click', function (e) {
		if (e.target === modal) {
			closeModal();
		}
	});
	document.addEventListener('keydown', function (e) {
		if (e.key === 'Escape' && !modal.hidden) {
			closeModal();
		}
	});

	// Footer link "Cookie settings" (custom link na #cookie-settings) — otvara
	// modal s bilo koje stranice, bez obzira na spremljeni pristanak.
	document.addEventListener('click', function (e) {
		var link = e.target.closest && e.target.closest('a[href$="#cookie-settings"]');
		if (link) {
			e.preventDefault();
			openModal();
		}
	});
})();
