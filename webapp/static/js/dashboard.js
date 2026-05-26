/* Dashboard functionality */
(function () {
    'use strict';

    var CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
    var _jobsRefreshTimer = null;
    var _jobsState = { status: '', q: '', cursor: null, limit: 25 };

    function ratingClass(score) {
        if (score == null) return '';
        var s = parseFloat(score);
        if (s >= 80) return 'rating-good';
        if (s >= 60) return 'rating-ok';
        return 'rating-low';
    }

    function renderJobsList(items) {
        var tb = document.getElementById('my-jobs-tbody');
        if (!tb) return;
        if (!items || !items.length) {
            tb.innerHTML = '<tr><td colspan="8"><em class="small">No analyses yet. Start by analyzing a site above.</em></td></tr>';
            var selAll = document.getElementById('jobs-select-all');
            if (selAll) selAll.checked = false;
            updateJobsBulkBar();
            return;
        }
        tb.innerHTML = '';
        items.forEach(function (it) {
            var tr = document.createElement('tr');
            tr.setAttribute('data-crawl-id', it.id || '');
            tr.setAttribute('data-status', it.status || '');

            var statusMap = {
                succeeded: 'Done',
                failed: 'Fail',
                running: 'Running',
                pending: 'Pending'
            };
            var statusClassMap = {
                succeeded: 'badge-status-succeeded',
                failed: 'badge-status-failed',
                running: 'badge-status-running',
                pending: 'badge-status-pending'
            };
            var statusBadge = '<span class="badge ' +
                (statusClassMap[it.status] || '') + '">' +
                (statusMap[it.status] || it.status || '') + '</span>';

            var aeo = it.aeo_score != null ? parseFloat(it.aeo_score).toFixed(1) : '&mdash;';
            var geo = it.geo_score != null ? parseFloat(it.geo_score).toFixed(1) : '&mdash;';
            var aax = it.aax_score != null ? parseFloat(it.aax_score).toFixed(1) : '&mdash;';
            var aeoCls = it.aeo_score != null ? ratingClass(it.aeo_score) : '';
            var geoCls = it.geo_score != null ? ratingClass(it.geo_score) : '';
            var aaxCls = it.aax_score != null ? ratingClass(it.aax_score) : '';

            var aeoCell = '<td class="' + aeoCls + '">' + aeo;
            if (it.aeo_rating) aeoCell += '<br><small>' + escapeHtml(it.aeo_rating) + '</small>';
            aeoCell += '</td>';

            var geoCell = '<td class="' + geoCls + '">' + geo;
            if (it.geo_rating) geoCell += '<br><small>' + escapeHtml(it.geo_rating) + '</small>';
            geoCell += '</td>';

            var aaxCell = '<td class="' + aaxCls + '">' + aax + '</td>';

            var retryForm = (it.status || '') !== 'running'
                ? '<form method="post" action="/retry/' + escapeHtml(it.id || '') + '" class="inline">' +
                (CSRF_TOKEN ? '<input type="hidden" name="csrf_token" value="' + escapeHtml(CSRF_TOKEN) + '">' : '') +
                '<button type="submit" class="btn btn-sm">Retry</button></form>'
                : '';

            tr.innerHTML =
                '<td><input type="checkbox" class="jobs-select" data-id="' + escapeHtml(it.id || '') + '" aria-label="Select job ' + escapeHtml(it.id || '') + '"></td>' +
                '<td><a href="/analysis/' + escapeHtml(it.id || '') + '">' + escapeHtml(it.domain || '') + '</a></td>' +
                aeoCell +
                geoCell +
                aaxCell +
                '<td>' + statusBadge + '</td>' +
                '<td><small>' + escapeHtml(it.updated_at || '') + '</small></td>' +
                '<td>' +
                '<a href="/analysis/' + escapeHtml(it.id || '') + '" class="btn btn-sm">View</a> ' +
                '<a href="/api/analysis/' + escapeHtml(it.id || '') + '" class="btn btn-sm" target="_blank">Export</a> ' +
                retryForm +
                '</td>';
            tb.appendChild(tr);
        });
        attachJobsSelectionHandlers();
    }

    function attachJobsSelectionHandlers() {
        var selAll = document.getElementById('jobs-select-all');
        var boxes = document.querySelectorAll('input.jobs-select');
        if (selAll) {
            selAll.onchange = function () {
                boxes.forEach(function (b) { b.checked = !!selAll.checked; });
                updateJobsBulkBar();
            };
        }
        boxes.forEach(function (b) { b.onchange = updateJobsBulkBar; });
        updateJobsBulkBar();
    }

    function updateJobsBulkBar() {
        var boxes = Array.prototype.slice.call(document.querySelectorAll('input.jobs-select'));
        var selected = boxes.filter(function (b) { return b.checked; });
        var bar = document.getElementById('jobs-bulk-bar');
        var cnt = document.getElementById('jobs-bulk-count');
        if (cnt) cnt.textContent = (selected.length || 0) + ' selected';
        if (bar) {
            if (selected.length > 0) { bar.classList.remove('hidden'); }
            else { bar.classList.add('hidden'); }
        }
    }

    function refreshJobsSoon() {
        if (_jobsRefreshTimer) return;
        _jobsRefreshTimer = setTimeout(function () {
            _jobsRefreshTimer = null;
            fetchJobs(false);
        }, 300);
    }

    function _setNextButtonAvailability(hasNext) {
        var nextBtn = document.getElementById('jobs-next');
        var pageMsg = document.getElementById('jobs-page-msg');
        if (nextBtn) {
            if (hasNext) {
                nextBtn.classList.remove('hidden');
                nextBtn.disabled = false;
                nextBtn.setAttribute('aria-disabled', 'false');
            } else {
                nextBtn.classList.add('hidden');
                nextBtn.disabled = true;
                nextBtn.setAttribute('aria-disabled', 'true');
            }
        }
        if (pageMsg) pageMsg.textContent = hasNext ? 'More available' : '';
    }

    function fetchJobs(next) {
        var msg = document.getElementById('jobs-status-msg');
        var pageMsg = document.getElementById('jobs-page-msg');
        if (msg) msg.textContent = 'Loading\u2026';
        var url = new URL('/api/my/jobs', window.location.origin);
        var status = _jobsState.status || '';
        var q = _jobsState.q || '';
        var cursor = (next === true) ? (_jobsState.cursor || null) : null;
        if (status) url.searchParams.set('status', status);
        if (q) url.searchParams.set('q', q);
        if (cursor) url.searchParams.set('cursor', cursor);
        url.searchParams.set('limit', String(_jobsState.limit || 25));
        apiJson(url.pathname + '?' + url.searchParams.toString(), 'GET').then(function (res) {
            var items = (res && res.items) || [];
            renderJobsList(items);
            var nc = (res && res.next_cursor) || null;
            _jobsState.cursor = nc;
            if (msg) msg.textContent = items.length + ' jobs';
            _setNextButtonAvailability(!!nc);
            _qsSet({ status: status || null, q: q || null, cursor: null });
        }).catch(function () {
            if (msg) msg.textContent = 'Unable to load jobs.';
            _setNextButtonAvailability(false);
        });
    }

    function applyJobFilters() {
        var sel = document.getElementById('jobs-status');
        var q = document.getElementById('jobs-q');
        _jobsState.status = (sel && sel.value) || '';
        _jobsState.q = (q && q.value) || '';
        _jobsState.cursor = null;
        fetchJobs(false);
    }

    function nextJobsPage() {
        if (!_jobsState.cursor) return;
        var msg = document.getElementById('jobs-status-msg');
        if (msg) msg.textContent = 'Loading\u2026';
        var url = new URL('/api/my/jobs', window.location.origin);
        if (_jobsState.status) url.searchParams.set('status', _jobsState.status);
        if (_jobsState.q) url.searchParams.set('q', _jobsState.q);
        if (_jobsState.cursor) url.searchParams.set('cursor', _jobsState.cursor);
        url.searchParams.set('limit', String(_jobsState.limit || 25));
        apiJson(url.pathname + '?' + url.searchParams.toString(), 'GET').then(function (res) {
            var items = (res && res.items) || [];
            renderJobsList(items);
            _jobsState.cursor = (res && res.next_cursor) || null;
            _setNextButtonAvailability(!!_jobsState.cursor);
            var msgEl = document.getElementById('jobs-status-msg');
            if (msgEl) msgEl.textContent = items.length + ' jobs';
            _qsSet({ status: _jobsState.status || null, q: _jobsState.q || null, cursor: _jobsState.cursor || null });
        }).catch(function () {
            var msgEl = document.getElementById('jobs-status-msg');
            if (msgEl) msgEl.textContent = 'Unable to load jobs.';
            _setNextButtonAvailability(false);
        });
    }

    function bulkRetry() {
        var boxes = Array.prototype.slice.call(document.querySelectorAll('input.jobs-select'));
        var ids = boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.getAttribute('data-id'); });
        if (!ids.length) return;
        try { trackEvent('bulk_retry_click'); } catch (_) { }
        apiJson('/api/my/jobs/bulk', 'POST', { operation: 'retry', ids: ids }).then(function (res) {
            fetchJobs(false);
        }).catch(function () { /* ignore */ });
    }

    function _qsSet(params) {
        try {
            var usp = new URLSearchParams(window.location.search);
            Object.keys(params || {}).forEach(function (k) {
                var v = params[k];
                if (v == null || v === '') usp.delete(k); else usp.set(k, v);
            });
            var url = window.location.pathname + '?' + usp.toString();
            window.history.replaceState({}, '', url);
        } catch (_) { }
    }

    function _readInitialJobsState() {
        try {
            var usp = new URLSearchParams(window.location.search);
            _jobsState.status = usp.get('status') || '';
            _jobsState.q = usp.get('q') || '';
            _jobsState.cursor = usp.get('cursor') || null;
            var sel = document.getElementById('jobs-status');
            if (sel) sel.value = _jobsState.status;
            var q = document.getElementById('jobs-q');
            if (q) q.value = _jobsState.q;
        } catch (_) { }
    }

    // Event delegation for share link buttons
    document.addEventListener('click', function (e) {
        var btn = e.target.closest('[data-action="copy-share"]');
        if (!btn) return;
        var path = btn.getAttribute('data-url');
        if (!path) return;
        var url = window.location.origin + path;
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(url).then(function () {
                var orig = btn.textContent;
                btn.textContent = 'Copied!';
                setTimeout(function () { btn.textContent = orig; }, 1500);
            });
        }
    });

    function initDashboard() {
        try { trackEvent('dashboard_view'); } catch (_) { }
        _readInitialJobsState();
        var applyBtn = document.getElementById('jobs-apply');
        if (applyBtn) applyBtn.addEventListener('click', applyJobFilters);
        var nextBtn = document.getElementById('jobs-next');
        if (nextBtn) nextBtn.addEventListener('click', nextJobsPage);
        var bulkBtn = document.getElementById('jobs-bulk-retry');
        if (bulkBtn) bulkBtn.addEventListener('click', bulkRetry);
        _setNextButtonAvailability(false);
        fetchJobs(false);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initDashboard);
    } else {
        initDashboard();
    }
})();
