/* Dashboard functionality — redesigned */
(function () {
    'use strict';

    var CSRF_TOKEN = window.CSRF_TOKEN ||
        document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';

    // ── Status filtering ──
    function initDomainFilter() {
        var select = document.getElementById('dash-status');
        var count = document.getElementById('dash-count');
        var list = document.getElementById('sites-list');
        if (!select || !list) return;

        function filter() {
            var s = select.value || '';
            var cards = list.querySelectorAll('.site-card');
            var visible = 0;
            cards.forEach(function (card) {
                var status = card.getAttribute('data-status') || '';
                var match = (!s || status === s);
                card.style.display = match ? '' : 'none';
                if (match) visible++;
            });
            if (count) {
                count.textContent = visible + ' of ' + cards.length;
            }
        }

        select.addEventListener('change', filter);
        filter();
    }

    // ── Status polling for running/pending cards ──
    var _pollTimer = null;
    var _pollDeadline = null;

    function pollActiveJobs() {
        var cards = document.querySelectorAll(
            '.site-card[data-status="running"], .site-card[data-status="pending"]'
        );
        if (!cards.length) {
            stopPolling();
            return;
        }

        // Stop after max duration
        if (_pollDeadline && Date.now() > _pollDeadline) {
            stopPolling();
            return;
        }

        var ids = [];
        cards.forEach(function (card) {
            var href = card.querySelector('.site-card-domain');
            if (href) {
                var m = href.getAttribute('href').match(/\/analysis\/(.+)$/);
                if (m) ids.push(m[1]);
            }
        });

        if (!ids.length) return;

        Promise.all(ids.map(function (id) {
            return fetch('/api/status/' + encodeURIComponent(id))
                .then(function (r) { return r.json(); });
        })).then(function (results) {
            var changed = false;
            results.forEach(function (r) {
                var cardsFor = document.querySelectorAll(
                    '.site-card[data-status="running"], .site-card[data-status="pending"]'
                );
                cardsFor.forEach(function (card) {
                    var href = card.querySelector('.site-card-domain');
                    if (href && href.getAttribute('href')
                        .indexOf(r.id) !== -1) {
                        if (r.status !== card.getAttribute('data-status')) {
                            changed = true;
                        }
                    }
                });
            });
            if (changed) {
                window.location.reload();
            }
        }).catch(function () {
            // silent
        });
    }

    function startPolling() {
        var cards = document.querySelectorAll(
            '.site-card[data-status="running"], .site-card[data-status="pending"]'
        );
        if (!cards.length) return;

        _pollDeadline = Date.now() + 10 * 60 * 1000; // 10 minutes
        _pollTimer = setInterval(pollActiveJobs, 10000);
        pollActiveJobs();
    }

    function stopPolling() {
        if (_pollTimer) {
            clearInterval(_pollTimer);
            _pollTimer = null;
        }
        _pollDeadline = null;
    }

    // ── Init ──
    function initDashboard() {
        initDomainFilter();
        startPolling();
        initApiKeys();
    }

    function initApiKeys() {
        var create = document.getElementById('api-key-create');
        var status = document.getElementById('api-key-status');
        if (!create) return;
        create.addEventListener('click', function () {
            create.disabled = true;
            if (status) status.textContent = 'Creating…';
            fetch('/api/keys', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF_TOKEN },
                body: JSON.stringify({ name: 'Dashboard key' })
            }).then(function (response) {
                if (!response.ok) throw new Error('Unable to create key');
                return response.json();
            }).then(function (data) {
                var reveal = document.getElementById('api-key-reveal');
                var value = document.getElementById('api-key-value');
                if (value) value.textContent = data.key;
                if (reveal) reveal.hidden = false;
                if (status) status.textContent = 'Key created.';
                create.disabled = false;
            }).catch(function () {
                if (status) status.textContent = 'Could not create the key. Try again.';
                create.disabled = false;
            });
        });
        var copy = document.getElementById('api-key-copy');
        if (copy) copy.addEventListener('click', function () {
            navigator.clipboard.writeText(document.getElementById('api-key-value').textContent)
                .then(function () { if (status) status.textContent = 'Key copied.'; })
                .catch(function () { if (status) status.textContent = 'Could not copy. Select the key text manually.'; });
        });
        document.querySelectorAll('.api-key-revoke').forEach(function (button) {
            button.addEventListener('click', function () {
                if (!window.confirm('Revoke this API key? Existing integrations will stop working.')) return;
                fetch('/api/keys/' + encodeURIComponent(button.dataset.keyId) + '/revoke', {
                    method: 'POST', headers: { 'X-CSRF-Token': CSRF_TOKEN }
                }).then(function (response) {
                    if (!response.ok) throw new Error('Unable to revoke key');
                    if (status) status.textContent = 'Key revoked.';
                    var row = button.closest('.api-key-row');
                    row.classList.add('api-key-row--revoked');
                    button.outerHTML = '<span class="small">Revoked</span>';
                }).catch(function () {
                    if (status) status.textContent = 'Could not revoke the key. Try again.';
                });
            });
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initDashboard);
    } else {
        initDashboard();
    }
})();
