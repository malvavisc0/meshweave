/* My Dashboard functionality - extracted from my.html */
(function () {
    'use strict';

    var CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
    var _activeJobsTimer = null;
    var _pfLastTrigger = null;
    var _jobsState = { status: '', q: '', cursor: null, limit: 25 };

    // Product management functions
    function openProductForm(p) {
        _pfLastTrigger = document.activeElement || null;
        var pf = document.getElementById('product-form');
        pf.classList.remove('hidden');
        pf.setAttribute('aria-hidden', 'false');
        document.getElementById('pf-title').textContent = p ? 'Edit Product' : 'New Product';
        document.getElementById('pf-id').value = (p && p.id) || '';
        document.getElementById('pf-name').value = (p && p.name) || '';
        document.getElementById('pf-website').value = (p && p.website) || '';
        document.getElementById('pf-description').value = (p && p.description) || '';
        document.getElementById('pf-contact').value = (p && p.contact_info) || '';
        var nameInput = document.getElementById('pf-name');
        var nameErr = document.getElementById('pf-name-err');
        var descInput = document.getElementById('pf-description');
        var descErr = document.getElementById('pf-description-err');
        var pfGlobal = document.getElementById('pf-global');
        if (nameInput) nameInput.setAttribute('aria-invalid', 'false');
        if (nameErr) nameErr.textContent = '';
        if (descInput) descInput.setAttribute('aria-invalid', 'false');
        if (descErr) descErr.textContent = '';
        if (pfGlobal) pfGlobal.textContent = '';
        try { nameInput && nameInput.focus(); } catch (_) { }
        try { pf.addEventListener('keydown', _productFormKeydown); } catch (_) { }
    }

    function closeProductForm() {
        var pf = document.getElementById('product-form');
        pf.classList.add('hidden');
        pf.setAttribute('aria-hidden', 'true');
        try { pf.removeEventListener('keydown', _productFormKeydown); } catch (_) { }
        if (_pfLastTrigger && typeof _pfLastTrigger.focus === 'function') {
            try { _pfLastTrigger.focus(); } catch (_) { }
        }
    }

    function renderProducts(items) {
        var tb = document.getElementById('products-tbody');
        if (!tb) return;
        if (!items || !items.length) {
            tb.innerHTML = '<tr><td colspan="5"><em class="small">No products yet. Click "New Product".</em></td></tr>';
            return;
        }
        tb.innerHTML = '';
        items.forEach(function (p) {
            var tr = document.createElement('tr');
            tr.innerHTML =
                '<td>' + escapeHtml(p.name || '') + '</td>' +
                '<td>' + ((safeUrl(p.website || '') && p.website) ? ('<a href="' + safeUrl(p.website) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(p.website) + '</a>') : '-') + '</td>' +
                '<td><small>' + escapeHtml(p.updated_at || '') + '</small></td>' +
                '<td><small>' + escapeHtml(p.contact_info || '-') + '</small></td>' +
                '<td><button class="btn btn-sm" onclick="openProductFormFromJson(\'' + encodeURIComponent(JSON.stringify(p)) + '\')">Edit</button></td>';
            tb.appendChild(tr);
        });
    }

    function loadProducts() {
        apiJson('/api/products', 'GET').then(function (res) {
            renderProducts((res && res.items) || []);
        }).catch(function (e) {
            var tb = document.getElementById('products-tbody');
            if (tb) {
                tb.innerHTML = '<tr><td colspan="5"><em class="small">Sign in to manage products.</em></td></tr>';
            }
        });
    }

    function saveProduct() {
        var id = document.getElementById('pf-id').value.trim();
        var name = document.getElementById('pf-name').value.trim();
        var website = document.getElementById('pf-website').value.trim();
        var description = document.getElementById('pf-description').value.trim();
        var contact = document.getElementById('pf-contact').value.trim();

        var pfGlobal = document.getElementById('pf-global');
        var nameInput = document.getElementById('pf-name');
        var nameErr = document.getElementById('pf-name-err');
        var descInput = document.getElementById('pf-description');
        var descErr = document.getElementById('pf-description-err');
        if (pfGlobal) pfGlobal.textContent = '';
        if (nameErr) nameErr.textContent = '';
        if (descErr) descErr.textContent = '';
        if (nameInput) nameInput.setAttribute('aria-invalid', 'false');
        if (descInput) descInput.setAttribute('aria-invalid', 'false');

        var firstInvalid = null;
        if (!name) {
            if (nameErr) nameErr.textContent = 'Name is required.';
            if (nameInput) nameInput.setAttribute('aria-invalid', 'true');
            firstInvalid = firstInvalid || nameInput;
        }
        if (!description) {
            if (descErr) descErr.textContent = 'Description is required.';
            if (descInput) descInput.setAttribute('aria-invalid', 'true');
            firstInvalid = firstInvalid || descInput;
        }
        if (firstInvalid) { try { firstInvalid.focus(); } catch (_) { }; if (pfGlobal) pfGlobal.textContent = 'Please correct the highlighted fields.'; return; }
        // Validate contact info if provided (format: Name <email@example.com>)
        if (contact && !/^[^<>\n]+ <[^<>\s@]+@[^<>\s@]+\.[^<>\s@]+>$/.test(contact)) {
            if (pfGlobal) pfGlobal.textContent = 'Contact Info must look like: Name <email@example.com>';
            return;
        }

        var body = {
            name: name,
            website: website || null,
            description: description,
            contact_info: contact || null
        };

        var url = '/api/products' + (id ? ('/' + encodeURIComponent(id)) : '');
        var method = id ? 'PUT' : 'POST';

        apiJson(url, method, body).then(function () {
            closeProductForm();
            loadProducts();
            try { trackEvent(method === 'POST' ? 'product_create_success' : 'product_update_success'); } catch (_) { }
        }).catch(function (e) {
            var nameInput = document.getElementById('pf-name');
            var nameErr = document.getElementById('pf-name-err');
            var pfGlobal = document.getElementById('pf-global');
            if (e && e.status === 409) {
                if (nameErr) nameErr.textContent = (e.body && e.body.detail) ? String(e.body.detail).replace(/_/g, ' ') : 'Product with this name already exists.';
                if (nameInput) { nameInput.setAttribute('aria-invalid', 'true'); try { nameInput.focus(); } catch (_) { } }
            } else if (e && e.status === 400) {
                if (pfGlobal) pfGlobal.textContent = 'Invalid fields. Please check required fields.';
            } else if (e && e.status === 401) {
                if (pfGlobal) pfGlobal.textContent = 'Sign in to save products.';
            } else {
                if (pfGlobal) pfGlobal.textContent = 'Unable to save product.';
            }
        });
    }

    function openProductFormFromJson(js) {
        try {
            var p = JSON.parse(decodeURIComponent(js));
            openProductForm(p);
        } catch (_) {
            openProductForm(null);
        }
    }

    // Safe URL helper: allow only http/https
    function safeUrl(u) {
        try {
            var s = String(u || '').trim();
            if (!s) return '';
            var l = s.toLowerCase();
            if (l.startsWith('http://') || l.startsWith('https://')) return s;
            return '';
        } catch (_) { return ''; }
    }

    // Quick Stats
    function loadQuickStats() {
        apiJson('/api/my/quick-stats', 'GET').then(function (d) {
            var a = document.getElementById('qs-analyses');
            var e = document.getElementById('qs-emails');
            var p = document.getElementById('qs-prospects');
            if (a) a.textContent = (d && typeof d.analyses_completed === 'number') ? d.analyses_completed : '0';
            if (e) e.textContent = (d && typeof d.emails_extracted === 'number') ? d.emails_extracted : '0';
            if (p) p.textContent = (d && typeof d.prospects_added === 'number') ? d.prospects_added : '0';
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
        var ids = collectActiveJobs();
        if (_activeJobsTimer) { clearInterval(_activeJobsTimer); _activeJobsTimer = null; }
        if (ids.length) {
            tickActiveJobs(ids);
            _activeJobsTimer = setInterval(function () { tickActiveJobs(ids); try { trackEvent('job_poll_tick'); } catch (_) { } }, 4000);
        }
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
            if (pageMsg) pageMsg.textContent = nc ? 'More available' : '';
            _qsSet({ status: status || null, q: q || null, cursor: null });
        }).catch(function () {
            if (msg) msg.textContent = 'Unable to load jobs.';
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
            var pageMsg = document.getElementById('jobs-page-msg');
            if (pageMsg) pageMsg.textContent = _jobsState.cursor ? 'More available' : '';
            var msgEl = document.getElementById('jobs-status-msg'); if (msgEl) msgEl.textContent = items.length + ' jobs';
            _qsSet({ status: _jobsState.status || null, q: _jobsState.q || null, cursor: _jobsState.cursor || null });
        }).catch(function () {
            var msgEl = document.getElementById('jobs-status-msg'); if (msgEl) msgEl.textContent = 'Unable to load jobs.';
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

    // Drawer focus management
    function _productFormKeydown(e) {
        if (e.key === 'Escape') { try { closeProductForm(); } catch (_) { } }
        if (e.key === 'Tab') {
            var pf = document.getElementById('product-form');
            var focusable = pf.querySelectorAll('button, [href], input, textarea, select, [tabindex]:not([tabindex="-1"])');
            focusable = Array.prototype.slice.call(focusable).filter(function (el) { return !el.disabled && el.offsetParent !== null; });
            if (!focusable.length) return;
            var first = focusable[0], last = focusable[focusable.length - 1];
            if (e.shiftKey && document.activeElement === first) { last.focus(); e.preventDefault(); }
            else if (!e.shiftKey && document.activeElement === last) { first.focus(); e.preventDefault(); }
        }
    }

    // Initialize the dashboard
    function initDashboard() {
        try { trackEvent('dashboard_view'); } catch (_) { }
        loadProducts();
        loadQuickStats();
        _readInitialJobsState();
        var applyBtn = document.getElementById('jobs-apply'); if (applyBtn) applyBtn.addEventListener('click', applyJobFilters);
        var nextBtn = document.getElementById('jobs-next'); if (nextBtn) nextBtn.addEventListener('click', nextJobsPage);
        var bulkBtn = document.getElementById('jobs-bulk-retry'); if (bulkBtn) bulkBtn.addEventListener('click', bulkRetry);
        // Initial fetch to ensure server-side table is synced with filters (if any)
        fetchJobs(false);
        setupActiveJobsPolling();
    }

    // Expose functions to global scope for HTML onclick attributes
    window.openProductForm = openProductForm;
    window.closeProductForm = closeProductForm;
    window.saveProduct = saveProduct;
    window.openProductFormFromJson = openProductFormFromJson;

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initDashboard);
    } else {
        initDashboard();
    }
})();