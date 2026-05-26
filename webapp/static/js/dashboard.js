/* Dashboard functionality — redesigned */
(function () {
    'use strict';

    var CSRF_TOKEN = window.CSRF_TOKEN ||
        document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';

    // ── Domain filtering (§19.1) ──
    function initDomainFilter() {
        var input = document.getElementById('dash-q');
        var select = document.getElementById('dash-status');
        var count = document.getElementById('dash-count');
        var list = document.getElementById('sites-list');
        if (!input || !select || !list) return;

        function filter() {
            var q = (input.value || '').toLowerCase().trim();
            var s = select.value || '';
            var cards = list.querySelectorAll('.site-card');
            var visible = 0;
            cards.forEach(function (card) {
                var domain = (card.getAttribute('data-domain') || '').toLowerCase();
                var status = card.getAttribute('data-status') || '';
                var match = (!q || domain.indexOf(q) !== -1) &&
                    (!s || status === s);
                card.style.display = match ? '' : 'none';
                if (match) visible++;
            });
            if (count) {
                count.textContent = visible + ' of ' + cards.length;
            }
        }

        input.addEventListener('input', filter);
        select.addEventListener('change', filter);
        filter();
    }

    function initScoreBars() {
        var fills = document.querySelectorAll('.score-line-bar-fill[data-score-width]');
        fills.forEach(function (fill) {
            var value = Number(fill.getAttribute('data-score-width') || 0);
            if (!Number.isFinite(value)) value = 0;
            value = Math.max(0, Math.min(100, value));
            fill.style.setProperty('--score-width', value);
        });
    }

    // ── Status polling for running/pending cards (§19.4) ──
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
        initScoreBars();
        startPolling();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initDashboard);
    } else {
        initDashboard();
    }
})();
