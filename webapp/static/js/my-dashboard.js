/* Dashboard functionality - extracted from my.html */
(function () {
    'use strict';

    var CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
    var _jobsRefreshTimer = null;
    var _jobsState = { status: '', q: '', cursor: null, limit: 25 };


    // Quick Stats / Active Vulnerabilities
    function loadQuickStats() {
        apiJson('/api/my/quick-stats', 'GET').then(function (d) {
            var aeo = document.getElementById('qs-aeo-low');
            var geo = document.getElementById('qs-geo-low');
            var aax = document.getElementById('qs-aax-low');

            if (aeo) {
                var val = (d && typeof d.aeo_low === 'number') ? d.aeo_low : 0;
                aeo.textContent = val;
                if (val > 0) aeo.className = 'stat-v stat-err'; else aeo.className = 'stat-v';
            }
            if (geo) {
                var val = (d && typeof d.geo_low === 'number') ? d.geo_low : 0;
                geo.textContent = val;
                if (val > 0) geo.className = 'stat-v stat-err'; else geo.className = 'stat-v';
            }
            if (aax) {
                var val = (d && typeof d.aax_low === 'number') ? d.aax_low : 0;
                aax.textContent = val;
                if (val > 0) aax.className = 'stat-v stat-err'; else aax.className = 'stat-v';
            }
            try { trackEvent('quick_stats_loaded'); } catch (_) { }
        }).catch(function () { /* silent */ });
    }

    // Jobs filters, keyset pagination, and bulk retry
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
            var sel = document.getElementById('jobs-status'); if (sel) sel.value = _jobsState.status;
            var q = document.getElementById('jobs-q'); if (q) q.value = _jobsState.q;
        } catch (_) { }
    }

    function renderJobsList(items) {
        var tb = document.getElementById('my-jobs-tbody');
        if (!tb) return;
        if (!items || !items.length) {
            tb.innerHTML = '<tr><td colspan="6"><em class="small">No analyses yet. Start by analyzing a site above.</em></td></tr>';
            var selAll = document.getElementById('jobs-select-all'); if (selAll) selAll.checked = false;
            updateJobsBulkBar();
            return;
        }
        tb.innerHTML = '';
        items.forEach(function (it) {
            var tr = document.createElement('tr');
            tr.setAttribute('data-crawl-id', it.id || '');
            tr.setAttribute('data-status', it.status || '');
            var urlText = it.canonical_url || ((it.domain || '') + (it.path || '') + ((it.query && ('?' + it.query)) || ''));
            tr.innerHTML =
                '<td><input type="checkbox" class="jobs-select" data-id="' + escapeHtml(it.id || '') + '" aria-label="Select job ' + escapeHtml(it.id || '') + '"></td>' +
                '<td>' + escapeHtml(it.scope || '') + '</td>' +
                '<td><a href="/analysis/' + escapeHtml(it.id || '') + '">' + escapeHtml(urlText) + '</a></td>' +
                '<td>' + escapeHtml(it.status || '') + '</td>' +
                '<td><small>' + escapeHtml(it.updated_at || '') + '</small></td>' +
                '<td>' + ((it.status || '') !== 'running'
                    ? '<form method="post" action="/retry/' + escapeHtml(it.id || '') + '" class="inline">' +
                    (CSRF_TOKEN ? '<input type="hidden" name="csrf_token" value="' + escapeHtml(CSRF_TOKEN) + '">' : '') +
                    '<button type="submit" class="btn">Retry</button></form>'
                    : '<em>Running</em>') + '</td>';
            tb.appendChild(tr);
        });
        attachJobsSelectionHandlers();
    }

    function attachJobsSelectionHandlers() {
        var selAll = document.getElementById('jobs-select-all');
        var boxes = document.querySelectorAll('input.jobs-select');
        if (selAll) {
            selAll.onchange = function () { boxes.forEach(function (b) { b.checked = !!selAll.checked; }); updateJobsBulkBar(); };
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

    // Throttled refresh to update the jobs table when a running job finishes
    function refreshJobsSoon() {
        if (_jobsRefreshTimer) return;
        _jobsRefreshTimer = setTimeout(function () {
            _jobsRefreshTimer = null;
            fetchJobs(false);
        }, 300);
    }

    // Show/hide and enable/disable the Next button based on presence of a next cursor
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
        if (msg) msg.textContent = 'Loading…';
        var url = new URL('/api/my/jobs', window.location.origin);
        var status = _jobsState.status || '';
        var q = _jobsState.q || '';
        var cursor = (next === true) ? (_jobsState.cursor || null) : null; // when applying filters, reset cursor
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
        // Keep current filters; fetch next page and update cursor
        var msg = document.getElementById('jobs-status-msg'); if (msg) msg.textContent = 'Loading…';
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
            var msgEl = document.getElementById('jobs-status-msg'); if (msgEl) msgEl.textContent = items.length + ' jobs';
            _qsSet({ status: _jobsState.status || null, q: _jobsState.q || null, cursor: _jobsState.cursor || null });
        }).catch(function () {
            var msgEl = document.getElementById('jobs-status-msg'); if (msgEl) msgEl.textContent = 'Unable to load jobs.';
            _setNextButtonAvailability(false);
        });
    }

    function bulkRetry() {
        var boxes = Array.prototype.slice.call(document.querySelectorAll('input.jobs-select'));
        var ids = boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.getAttribute('data-id'); });
        if (!ids.length) return;
        try { trackEvent('bulk_retry_click'); } catch (_) { }
        apiJson('/api/my/jobs/bulk', 'POST', { operation: 'retry', ids: ids }).then(function (res) {
            // Refresh list to reflect pending statuses
            fetchJobs(false);
        }).catch(function () { /* ignore */ });
    }


    // Initialize the dashboard
    function initDashboard() {
        try { trackEvent('dashboard_view'); } catch (_) { }
        loadQuickStats();
        _readInitialJobsState();
        var applyBtn = document.getElementById('jobs-apply'); if (applyBtn) applyBtn.addEventListener('click', applyJobFilters);
        var nextBtn = document.getElementById('jobs-next'); if (nextBtn) nextBtn.addEventListener('click', nextJobsPage);
        var bulkBtn = document.getElementById('jobs-bulk-retry'); if (bulkBtn) bulkBtn.addEventListener('click', bulkRetry);
        // Hide "Next" until we know more
        _setNextButtonAvailability(false);
        // Initial fetch to ensure server-side table is synced with filters (if any)
        fetchJobs(false);
    }


    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initDashboard);
    } else {
        initDashboard();
    }
})();
