/* My Dashboard functionality - extracted from my.html */
(function () {
    'use strict';

    var CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
    var _activeJobsTimer = null;
    var _jobsRefreshTimer = null;
    var _jobsState = { status: '', q: '', cursor: null, limit: 25 };


    // Quick Stats
    function loadQuickStats() {
        apiJson('/api/my/quick-stats', 'GET').then(function (d) {
            var a = document.getElementById('qs-analyses');
            var e = document.getElementById('qs-emails');
            var p = document.getElementById('qs-prospects');
            var pr = document.getElementById('qs-products');
            if (a) a.textContent = (d && typeof d.analyses_completed === 'number') ? d.analyses_completed : '0';
            if (e) e.textContent = (d && typeof d.emails_extracted === 'number') ? d.emails_extracted : '0';
            if (p) p.textContent = (d && typeof d.prospects_added === 'number') ? d.prospects_added : '0';
            if (pr) pr.textContent = (d && typeof d.products_count === 'number') ? d.products_count : '0';
            try { trackEvent('quick_stats_loaded'); } catch (_) { }
        }).catch(function () { /* silent */ });
    }

    // Active Jobs polling
    function collectActiveJobs() {
        var list = document.getElementById('active-jobs-list');
        var empty = document.getElementById('active-jobs-empty');
        if (!list) return [];
        list.innerHTML = '';
        var rows = document.querySelectorAll('tbody tr[data-crawl-id][data-status="running"]');
        var ids = [];
        rows.forEach(function (row) {
            var id = row.getAttribute('data-crawl-id');
            if (!id) return;
            ids.push(id);
            var item = document.createElement('div');
            item.setAttribute('data-crawl-id', id);
            item.innerHTML =
                '<div class="small"><code>' + escapeHtml(id) + '</code></div>' +
                '<div class="progress" style="background:#eee;height:10px;border-radius:4px;overflow:hidden;margin:4px 0;">' +
                '<div id="pb-' + id + '" class="progress-bar" style="width:0%;height:10px;background:#4caf50;"></div>' +
                '</div>' +
                '<div class="small">Last updated: <span id="pbt-' + id + '">—</span></div>';
            list.appendChild(item);
        });
        if (empty) empty.style.display = ids.length ? 'none' : '';
        return ids;
    }

    function tickActiveJobs(ids) {
        if (!ids || !ids.length) return;
        ids.forEach(function (id) {
            apiJson('/api/progress/' + encodeURIComponent(id), 'GET').then(function (d) {
                var status = (d && (d.status || '')).toLowerCase();
                // If job is no longer running, remove its progress card and refresh the jobs table
                if (status && status !== 'running') {
                    var list = document.getElementById('active-jobs-list');
                    var empty = document.getElementById('active-jobs-empty');
                    var card = (list ? list.querySelector('[data-crawl-id="' + id + '"]') : null);
                    if (card && card.parentNode) { card.parentNode.removeChild(card); }
                    if (empty) { empty.style.display = (list && list.children.length) ? 'none' : ''; }
                    // Schedule a lightweight refresh of the jobs table so the row status/actions update
                    refreshJobsSoon();
                    return;
                }
                // Update progress UI
                var pb = document.getElementById('pb-' + id);
                var pbt = document.getElementById('pbt-' + id);
                if (pb) {
                    var visited = (d && typeof d.visited_pages === 'number') ? d.visited_pages : 0;
                    var maxp = (d && d.limits && typeof d.limits.max_pages === 'number') ? d.limits.max_pages : null;
                    var pct = (maxp && maxp > 0) ? Math.min(100, Math.floor((visited / maxp) * 100)) : 0;
                    pb.style.width = pct + '%';
                }
                if (pbt) { pbt.textContent = (d && d.last_updated) ? d.last_updated : ''; }
            }).catch(function () { /* ignore */ });
        });
    }

    function setupActiveJobsPolling() {
        if (_activeJobsTimer) { clearInterval(_activeJobsTimer); _activeJobsTimer = null; }
        function pollOnce() {
            var ids = collectActiveJobs();
            if (ids.length) {
                tickActiveJobs(ids);
            } else {
                // Keep "no active jobs" message accurate
                var empty = document.getElementById('active-jobs-empty');
                if (empty) empty.style.display = '';
            }
        }
        // Initial poll
        pollOnce();
        // Recollect IDs and poll each tick so UI stays in sync
        _activeJobsTimer = setInterval(function () {
            pollOnce();
            try { trackEvent('job_poll_tick'); } catch (_) { }
        }, 4000);
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
            tb.innerHTML = '<tr><td colspan="6"><em class="small">No jobs found.</em></td></tr>';
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
        setupActiveJobsPolling();
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
        setupActiveJobsPolling();
    }


    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initDashboard);
    } else {
        initDashboard();
    }
})();