/* Result Analysis JavaScript - Extracted from result.html */
(function () {
    'use strict';

    // Data from server (loaded from JSON script tag)
    var __dataEl = document.getElementById('analysis-data');
    var __ctx = {};
    try { __ctx = JSON.parse(__dataEl ? (__dataEl.textContent || __dataEl.innerText || '{}') : '{}'); } catch (e) { __ctx = {}; }
    const BASE_DOMAIN = __ctx.base_domain || '';
    const EMAILS_UNIQUE = __ctx.emails_unique || [];
    const EMAILS_BY_URL = __ctx.emails_by_url || {};
    const EMAILS_SOURCES_RAW = __ctx.emails_sources || [];
    const LINKS_INTERNAL = __ctx.links_internal || [];
    const LINKS_EXTERNAL = __ctx.links_external || [];
    const TOP_EXTERNAL_DOMAINS = __ctx.top_external_domains || [];
    const PAGES = __ctx.pages || [];
    const USER_ID = __ctx.user_id || '';
    const CRAWL_ID = __ctx.crawl_id || '';
    // Owner toggles
    const LISTED = !!(__ctx.listed);
    const SHARE_URL = __ctx.share_url || '';
    // Logged-in state from server
    let LOGGED_IN = !!(__ctx.logged_in);
    var PROSPECT_ID = null; var PROSPECT_SOCIALS = [];
    // Pages pager state (initialized on first render)
    var PAGES_PAGER = null;


    /* Prospect helpers */
    function apiJson(url, method, body) {
        return fetch(url, {
            method: method || 'GET',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: body ? JSON.stringify(body) : undefined
        }).then(async function (r) {
            var data = null;
            try { data = await r.json(); } catch (_) { }
            if (!r.ok) { throw { status: r.status, body: data }; }
            return data || {};
        });
    }

    /* Owner toggles */
    function setListed(enabled) {
        if (!CRAWL_ID) return Promise.reject(new Error('No crawl ID'));
        return apiJson('/analysis/' + CRAWL_ID + '/set-listed', 'POST', { listed: !!enabled });
    }
    function setShare(enabled) {
        if (!CRAWL_ID) return Promise.reject(new Error('No crawl ID'));
        return apiJson('/analysis/' + CRAWL_ID + '/set-share', 'POST', { enabled: !!enabled });
    }
    function copyShareUrl() {
        if (SHARE_URL) {
            copyLink(SHARE_URL);
        }
    }
    function siteRoot() {
        var d = (BASE_DOMAIN || '').replace(/^www\./, '');
        return d ? ('https://' + d + '/') : (window.location.origin + '/');
    }
    function setProspectStatusChip(text) {
        try {
            var chip = document.getElementById('prospect-status-chip');
            if (chip) chip.textContent = text || 'Shortlisted';
        } catch (_) { }
    }

    function prospectEnsure() {
        if (PROSPECT_ID) return Promise.resolve(PROSPECT_ID);
        var domain = (BASE_DOMAIN || '').toLowerCase();
        if (!domain) return Promise.reject(new Error('Missing base domain'));
        return apiJson('/api/prospects', 'POST', {
            domain: domain,
            url: siteRoot(),
            status: 'shortlisted'
        }).then(function (row) {
            PROSPECT_ID = row.id || null;
            PROSPECT_SOCIALS = Array.isArray(row.socials) ? row.socials.slice(0) : [];
            setProspectStatusChip((row.status || 'shortlisted').charAt(0).toUpperCase() + (row.status || 'shortlisted').slice(1));
            return PROSPECT_ID;
        });
    }
    function prospectToggle() {
        prospectEnsure().then(function () {
            alert('Prospect saved');
        }).catch(function (e) {
            if (e && e.status === 401) alert('Sign in to save prospect');
            else alert('Unable to save prospect');
        });
    }
    function openProspectManage() {
        var status = prompt('Prospect status (shortlisted/contacted/replied/won/lost):', 'shortlisted');
        if (!status) return;
        var tags = prompt('Tags (comma-separated):', '') || '';
        var notes = prompt('Notes:', '') || '';
        prospectEnsure().then(function (pid) {
            return apiJson('/api/prospects/' + encodeURIComponent(pid), 'PATCH', {
                status: (status || '').trim().toLowerCase(),
                tags: tags,
                notes: notes
            });
        }).then(function (row) {
            setProspectStatusChip((row.status || 'shortlisted').charAt(0).toUpperCase() + (row.status || 'shortlisted').slice(1));
            alert('Prospect updated');
        }).catch(function (e) {
            if (e && e.status === 401) alert('Sign in to manage prospect');
            else alert('Unable to update prospect');
        });
    }
    function attachProspectSocial(platform, url) {
        prospectEnsure().then(function (pid) {
            // merge unique by url
            var list = Array.isArray(PROSPECT_SOCIALS) ? PROSPECT_SOCIALS.slice(0) : [];
            var exists = list.some(function (x) { return (x && (x.url || '').toLowerCase()) === String(url || '').toLowerCase(); });
            if (!exists) list.push({ platform: (platform || '').toLowerCase(), url: url });
            return apiJson('/api/prospects/' + encodeURIComponent(pid), 'PATCH', { socials: list });
        }).then(function (row) {
            PROSPECT_SOCIALS = Array.isArray(row.socials) ? row.socials.slice(0) : [];
            alert('Attached to prospect');
        }).catch(function (e) {
            if (e && e.status === 401) alert('Sign in to attach');
            else alert('Unable to attach to prospect');
        });
    }
    function attachContactSocial(url) {
        var email = prompt('Contact email to attach this social URL to:', '');
        if (!email) return;
        prospectEnsure().then(function (pid) {
            return apiJson('/api/prospects/' + encodeURIComponent(pid) + '/contacts', 'POST', {
                email: email,
                social_url: url
            });
        }).then(function () {
            alert('Attached to contact');
        }).catch(function (e) {
            if (e && e.status === 409) alert('Contact already exists');
            else if (e && e.status === 401) alert('Sign in to attach');
            else alert('Unable to attach to contact');
        });
    }
    function addEmailToProspect(email, sourceUrl) {
        if (!email) return;
        prospectEnsure().then(function (pid) {
            return apiJson('/api/prospects/' + encodeURIComponent(pid) + '/contacts', 'POST', {
                email: email,
                source_url: sourceUrl || ''
            });
        }).then(function () {
            alert('Email added to prospect');
        }).catch(function (e) {
            if (e && e.status === 409) alert('Email already added');
            else if (e && e.status === 401) alert('Sign in to add');
            else alert('Unable to add email');
        });
    }
    function claimAnalysis() {
        try {
            var key = __ctx.public_key || '';
            if (!key) { alert('Not claimable'); return; }
            apiJson('/api/claim/public/' + encodeURIComponent(key), 'POST', {}).then(function (res) {
                alert('Claimed');
                if (res && res.id) { window.location.href = '/analysis/' + res.id; }
            }).catch(function (e) {
                if (e && e.status === 400) alert('Not eligible yet');
                else if (e && e.status === 409) alert('Already claimed or not claimable');
                else if (e && e.status === 401) alert('Sign in to claim');
                else alert('Unable to claim');
            });
        } catch (_) { }
    }

    /* Pages helpers */
    /* Helpers for Pages filtering and preview */
    function setPreviewHint(msg) {
        try {
            var id = 'page-preview-hint';
            var el = document.getElementById(id);
            if (!el) {
                el = document.createElement('div'); el.id = id; el.className = 'small';
                var pre = document.getElementById('page-markdown'); var parent = pre ? pre.parentNode : null;
                if (parent) parent.appendChild(el);
            }
            el.textContent = msg || '';
            el.style.display = msg ? '' : 'none';
        } catch (_) { }
    }
    function setActiveRowByUrl(url) {
        try {
            document.querySelectorAll('#pages-list li').forEach(function (li) {
                li.classList.toggle('active', (li.getAttribute('data-url') || '') === url);
            });
        } catch (_) { }
    }
    function previewPageByUrl(url) {
        try {
            var p = (PAGES || []).find(function (x) { return (x && (x.url || '') === url); });
            var md = (p && (p.markdown || '').trim()) || '';
            if (md) {
                renderMarkdown(md);
                setPreviewHint('');
            } else {
                renderMarkdown('');
                setPreviewHint('No per-page markdown available.');
            }
            setActiveRowByUrl(url);
        } catch (_) { }
    }
    /* ----- Pages panel (per-page stats table) ----- */
    function _pageSizeScore(cm) {
        // AEO A2 content_structure word-count scoring
        var w = (cm && Number(cm.words)) || 0;
        if (w < 200) return 10;
        if (w < 500) return 30;
        if (w < 1000) return 50;
        if (w < 2000) return 70;
        return 90;
    }
    function _structureScore(hd) {
        // AEO A2 content_structure heading scoring
        var score = 0;
        var h1c = (hd && Number(hd.h1_count)) || 0;
        var dep = (hd && Number(hd.depth)) || 0;
        var tot = (hd && Number(hd.total)) || 0;
        if (h1c === 1) score += 40;
        else if (h1c > 1) score += 10;
        if (dep >= 2) score += 30;
        else if (dep >= 1) score += 15;
        if (tot >= 5) score += 30;
        else if (tot >= 2) score += 15;
        return Math.min(100, score);
    }
    function _scoreCls(s) {
        if (s >= 70) return 'stat-ok';
        if (s >= 40) return 'stat-warn';
        return 'stat-err';
    }
    function _pageShortPath(url) {
        try {
            var u = new URL(url);
            var p = u.pathname || '/';
            if (p.endsWith('/')) p = p.slice(0, -1);
            if (p === '' || p === '/') return '/';
            var parts = p.split('/').filter(Boolean);
            if (parts.length === 0) return '/';
            return '/' + parts.slice(-2).join('/') + (p.endsWith('/') ? '/' : '');
        } catch (_) {
            return url;
        }
    }
    function _pageDomain(url) {
        try { return new URL(url).hostname || ''; } catch (_) { return ''; }
    }
    function _pageTitle(p) {
        return (p && p.page && p.page.title) || '';
    }
    function _pageMetaDesc(p) {
        return (p && p.page && p.page.description) || '';
    }
    function _collectTypes(obj, types) {
        if (!obj || typeof obj !== 'object') return;
        var t = obj['@type'];
        if (t) {
            if (Array.isArray(t)) {
                t.forEach(function (x) { if (x) types.add(String(x)); });
            } else {
                types.add(String(t));
            }
        }
        // Handle @graph arrays
        if (Array.isArray(obj['@graph'])) {
            obj['@graph'].forEach(function (item) { _collectTypes(item, types); });
        }
        // Recurse into nested objects (but skip arrays and primitives)
        Object.keys(obj).forEach(function (k) {
            if (k === '@type' || k === '@graph') return;
            var v = obj[k];
            if (Array.isArray(v)) {
                v.forEach(function (item) { _collectTypes(item, types); });
            } else if (v && typeof v === 'object') {
                _collectTypes(v, types);
            }
        });
    }

    function _schemaTypes(p) {
        var jsonld = (p && p.page && p.page.jsonld) || [];
        var types = new Set();
        jsonld.forEach(function (s) { _collectTypes(s, types); });
        return Array.from(types);
    }
    function _richnessIcons(cm) {
        var icons = [];
        var lists = (cm && Number(cm.lists)) || 0;
        var tables = (cm && Number(cm.tables)) || 0;
        var code = (cm && Number(cm.code_blocks)) || 0;
        if (lists > 0) icons.push('<span title="' + lists + ' list' + (lists > 1 ? 's' : '') + '">&#x1f4cb;</span>');
        if (tables > 0) icons.push('<span title="' + tables + ' table' + (tables > 1 ? 's' : '') + '">&#x1f4ca;</span>');
        if (code > 0) icons.push('<span title="' + code + ' code block' + (code > 1 ? 's' : '') + '">&#x1f4bb;</span>');
        return icons.join(' ') || '—';
    }

    function renderPages() {
        try {
            var ul = document.getElementById('pages-list'); if (!ul) return;

            if (!PAGES_PAGER) {
                PAGES_PAGER = {
                    term: '', page: 1, pageSize: 5, totalPages: 1, ul: ul,
                    pagerEl: null, prevBtn: null, nextBtn: null, pageLabel: null, sliceUrls: []
                };
            } else {
                PAGES_PAGER.ul = ul;
                if (PAGES_PAGER.pageSize == null) PAGES_PAGER.pageSize = 5;
                if (!Array.isArray(PAGES_PAGER.sliceUrls)) PAGES_PAGER.sliceUrls = [];
            }

            var rows = (PAGES || []).filter(function (p) { return !!p; });
            if ((!rows || rows.length === 0) && Array.isArray(LINKS_INTERNAL) && LINKS_INTERNAL.length > 0) {
                rows = LINKS_INTERNAL.map(function (u) {
                    return { url: u, markdown: '', page: {}, headings: {}, content_metrics: {}, links: {}, emails: {} };
                });
            }
            var term = String(PAGES_PAGER.term || '').toLowerCase().trim();
            var filtered = term ? rows.filter(function (p) {
                return String(p.url || '').toLowerCase().indexOf(term) !== -1 ||
                    String(_pageTitle(p) || '').toLowerCase().indexOf(term) !== -1;
            }) : rows;

            PAGES_PAGER.totalPages = Math.max(1, Math.ceil(filtered.length / PAGES_PAGER.pageSize));
            if (PAGES_PAGER.page < 1) PAGES_PAGER.page = 1;
            if (PAGES_PAGER.page > PAGES_PAGER.totalPages) PAGES_PAGER.page = PAGES_PAGER.totalPages;

            var start = (PAGES_PAGER.page - 1) * PAGES_PAGER.pageSize;
            var end = Math.min(filtered.length, start + PAGES_PAGER.pageSize);
            var slice = filtered.slice(start, end);
            PAGES_PAGER.sliceUrls = slice.map(function (p) { return p.url || ''; });

            // Render stats table as a <ul> styled with domain-list
            ul.innerHTML = '';
            // Header row
            var headLi = document.createElement('li');
            headLi.className = 'domain-list-header pages-header-row';
            headLi.innerHTML =
                '<span class="ph-page">Page</span>' +
                '<span class="ph-words">Words</span>' +
                '<span class="ph-headings">Headings</span>' +
                '<span class="ph-scss">Str</span>' +
                '<span class="ph-schema">Schema</span>' +
                '<span class="ph-rich">Rich</span>';
            ul.appendChild(headLi);

            slice.forEach(function (p) {
                var li = document.createElement('li');
                li.className = 'pages-row';
                var url = p.url || '';
                li.setAttribute('data-url', url);
                var path = _pageShortPath(url);
                var dom = _pageDomain(url);
                var title = _pageTitle(p) || '';
                var cm = p.content_metrics || {};
                var hd = p.headings || {};
                var ws = (cm.words != null) ? Number(cm.words) : null;
                var wsc = ws != null ? _pageSizeScore(cm) : null;
                var scs = _structureScore(hd);
                var schemas = _schemaTypes(p);
                var schemaLabel = schemas.length > 0 ? schemas.slice(0, 2).join(', ') : 'none';
                var richness = _richnessIcons(cm);
                var totalHd = (hd && Number(hd.total)) || 0;
                var maxDepth = (hd && Number(hd.depth)) || 0;
                var hdLabel = totalHd ? totalHd + ' · d' + maxDepth : '—';

                li.innerHTML =
                    '<span class="ps-page" title="' + escapeHtml(url) + '"><span class="ps-path">' + escapeHtml(path) + '</span><span class="ps-title">' + escapeHtml(title) + '</span><span class="ps-dom">' + escapeHtml(dom) + '</span></span>' +
                    '<span class="ps-words ' + (wsc != null ? _scoreCls(wsc) : '') + '">' + (ws != null ? ws.toLocaleString() : '—') + '</span>' +
                    '<span class="ps-headings">' + escapeHtml(hdLabel) + '</span>' +
                    '<span class="ps-scss ' + _scoreCls(scs) + '">' + scs + '</span>' +
                    '<span class="ps-schema small">' + escapeHtml(schemaLabel) + '</span>' +
                    '<span class="ps-rich">' + richness + '</span>';

                li.addEventListener('click', function () {
                    previewPageByUrl(url);
                    try { trackEvent('page_preview'); } catch (_) { }
                });

                ul.appendChild(li);
            });

            try { var pc = document.getElementById('pages-count'); if (pc) pc.textContent = String(filtered.length); } catch (_) { }

            var firstWithMd = slice.find(function (p) { return !!((p.markdown || '').trim().length); });
            // Don't auto-preview — show summary hint instead
            if (firstWithMd) {
                // Still render the markdown panel but keep it empty until selected
                // (or show the first page to match old behaviour)
                previewPageByUrl(firstWithMd.url || '');
            } else {
                renderMarkdown('');
                setPreviewHint(filtered.length ? 'No per-page markdown available.' : 'No pages to display.');
            }

            renderPagesControls();
            updatePagesControls();
        } catch (_) { }
    }
    function renderPagesControls() {
        try {
            var st = PAGES_PAGER; if (!st || !st.ul) return;
            if (!st.pagerEl) {
                var nav = document.createElement('div');
                nav.id = 'pages-pager';
                nav.setAttribute('role', 'navigation');
                nav.setAttribute('aria-label', 'Pages pagination');
                nav.className = 'small mt-1';
                try {
                    nav.style.display = 'flex';
                    nav.style.justifyContent = 'center';
                    nav.style.alignItems = 'center';
                    nav.style.gap = '8px';
                } catch (_) { }

                var prev = document.createElement('button');
                prev.type = 'button'; prev.className = 'btn btn-sm'; prev.textContent = 'Prev';
                prev.setAttribute('aria-controls', st.ul.id || 'pages-list');

                var label = document.createElement('span');
                label.className = 'ml-1 mr-1';
                label.setAttribute('aria-live', 'polite');
                label.textContent = 'Page ' + st.page + ' of ' + st.totalPages;

                var next = document.createElement('button');
                next.type = 'button'; next.className = 'btn btn-sm'; next.textContent = 'Next';
                next.setAttribute('aria-controls', st.ul.id || 'pages-list');

                prev.addEventListener('click', function () {
                    if (PAGES_PAGER.page > 1) {
                        PAGES_PAGER.page -= 1;
                        renderPages();
                    }
                });
                next.addEventListener('click', function () {
                    if (PAGES_PAGER.page < PAGES_PAGER.totalPages) {
                        PAGES_PAGER.page += 1;
                        renderPages();
                    }
                });

                nav.appendChild(prev);
                nav.appendChild(label);
                nav.appendChild(next);

                st.ul.parentNode.appendChild(nav);
                st.pagerEl = nav; st.prevBtn = prev; st.nextBtn = next; st.pageLabel = label;
            }
        } catch (_) { }
    }

    function updatePagesControls() {
        try {
            var st = PAGES_PAGER; if (!st || !st.pagerEl) return;
            if (st.prevBtn) st.prevBtn.disabled = (st.page <= 1);
            if (st.nextBtn) st.nextBtn.disabled = (st.page >= st.totalPages);
            if (st.pageLabel) st.pageLabel.textContent = 'Page ' + st.page + ' of ' + st.totalPages;
        } catch (_) { }
    }

    /* Markdown rendering (client-side with marked + DOMPurify) */
    var markdownRawVisible = false;

    function renderMarkdownHtml(md) {
        var contentEl = document.getElementById('page-content');
        var preEl = document.getElementById('page-markdown');
        if (!contentEl || !preEl) return;

        if (markdownRawVisible) {
            // Show raw markdown, hide rendered
            preEl.textContent = (md || '');
            preEl.style.display = '';
            contentEl.style.display = 'none';
        } else {
            // Show rendered HTML, hide raw
            preEl.textContent = (md || '');
            preEl.style.display = 'none';
            contentEl.style.display = '';
            if (typeof marked !== 'undefined') {
                var html = marked.parse(md || '', { breaks: true, gfm: true });
                if (typeof DOMPurify !== 'undefined') {
                    contentEl.innerHTML = DOMPurify.sanitize(html, {
                        ALLOWED_TAGS: [
                            'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                            'p', 'ul', 'ol', 'li', 'blockquote', 'code', 'pre',
                            'a', 'strong', 'em', 'del', 'hr',
                            'table', 'thead', 'tbody', 'tr', 'th', 'td',
                            'img', 'br'
                        ],
                        ALLOWED_ATTR: ['href', 'src', 'alt', 'title', 'style']
                    });
                } else {
                    contentEl.innerHTML = html;
                }
            } else {
                // Fallback: show raw if libraries not loaded
                preEl.style.display = '';
                contentEl.style.display = 'none';
            }
        }
    }

    function toggleMarkdownView() {
        markdownRawVisible = !markdownRawVisible;
        var btn = document.getElementById('toggle-raw-btn');
        if (btn) btn.textContent = markdownRawVisible ? 'View rendered' : 'View raw';
        // Re-render current page
        var activeLi = document.querySelector('#pages-list li.active');
        if (activeLi) {
            var url = activeLi.getAttribute('data-url');
            var p = (PAGES || []).find(function (x) { return (x && (x.url || '') === url); });
            renderMarkdownHtml((p && (p.markdown || '').trim()) || '');
        }
    }

    function renderMarkdown(md) {
        var pre = document.getElementById('page-markdown'); if (pre) { pre.textContent = (md || ''); }
        renderMarkdownHtml(md);
    }
    /* Include/copy/download actions for Pages */
    function copyCurrentPage() {
        try {
            var pre = document.getElementById('page-markdown'); var text = (pre && (pre.textContent || pre.innerText) || '');
            if (navigator.clipboard && window.isSecureContext) {
                navigator.clipboard.writeText(text).then(function () { alert('Copied'); }, function () { legacyCopy(text); });
            } else { legacyCopy(text); }
        } catch (_) { try { legacyCopy(''); } catch (__) { } }
    }
    function downloadCurrentPage() {
        try {
            var pre = document.getElementById('page-markdown'); var md = (pre && (pre.textContent || pre.innerText) || '');
            if (!md.trim()) { alert('Nothing to download'); return; }
            var li = document.querySelector('#pages-list li.active') || null;
            var url = li ? (li.getAttribute('data-url') || '') : '';
            var fname = (url ? (url.replace(/[^a-z0-9]+/gi, '_').replace(/^_+|_+$/g, '') || 'page') : 'page') + '.md';
            var blob = new Blob([md], { type: 'text/markdown;charset=utf-8' }); var u = URL.createObjectURL(blob); var a = document.createElement('a'); a.href = u; a.download = fname; document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(u);
        } catch (_) { }
    }

    /* Social grouping (client-side over external links) */
    function renderSocial() {
        try {
            var cont = document.getElementById('social-groups'); if (!cont) return;

            // Strip tracking params and resolve known redirectors (best-effort, no network)
            function stripTracking(u) {
                try {
                    var url = new URL(u);
                    var host = (url.hostname || '').toLowerCase();
                    // Known redirectors that embed target in query
                    if (host === 'l.facebook.com' || host === 'l.instagram.com') {
                        var real = url.searchParams.get('u') || url.searchParams.get('url');
                        if (real) { return new URL(real); }
                        return null; // drop opaque redirectors
                    }
                    // Common trackers
                    var del = [];
                    url.searchParams.forEach(function (_v, k) {
                        var lk = String(k || '').toLowerCase();
                        if (lk.startsWith('utm_') || lk === 'fbclid' || lk === 'gclid' || lk === 'mc_cid' || lk === 'mc_eid' || lk === 'igshid') del.push(k);
                    });
                    del.forEach(function (k) { url.searchParams.delete(k); });
                    url.hash = '';
                    return url;
                } catch (_) { return null; }
            }

            // Canonicalize host/scheme and unify aliases (e.g., twitter -> x.com, youtu.be -> youtube.com)
            function canonicalize(url) {
                try {
                    url.protocol = 'https:';
                    var host = (url.hostname || '').toLowerCase();
                    var path = url.pathname || '/';

                    // Host aliases
                    if (host === 'www.x.com') host = 'x.com';
                    if (host === 'twitter.com' || host === 'www.twitter.com' || host === 'm.twitter.com' || host === 'mobile.twitter.com') host = 'x.com';
                    if (host === 'www.linkedin.com') host = 'linkedin.com';
                    if (host === 'fb.com' || host === 'fb.me' || host === 'm.facebook.com' || host === 'www.facebook.com') host = 'facebook.com';
                    if (host === 'www.instagram.com') host = 'instagram.com';
                    if (host === 'www.youtube.com' || host === 'm.youtube.com') host = 'youtube.com';
                    if (host === 'youtu.be') {
                        var id = path.replace(/^\/+/, '');
                        if (id) {
                            url = new URL('https://youtube.com/watch?v=' + encodeURIComponent(id));
                            host = 'youtube.com';
                        } else {
                            host = 'youtube.com';
                        }
                        path = url.pathname || '/';
                    }
                    if (host === 'www.tiktok.com' || host === 'vm.tiktok.com') host = 'tiktok.com';
                    if (host === 'gist.github.com') host = 'github.com';
                    if (host === 'discord.gg' || host === 'www.discord.gg') host = 'discord.gg';
                    if (host === 'discord.com' || host === 'www.discord.com') host = 'discord.com';
                    if (host === 'telegram.me' || host === 'www.telegram.me') host = 't.me';

                    url.hostname = host;
                    // Trim trailing slash except root
                    if ((url.pathname || '').length > 1 && url.pathname.endsWith('/')) {
                        url.pathname = url.pathname.replace(/\/+$/, '');
                    }
                    return url;
                } catch (_) { return null; }
            }

            function platformForHost(host) {
                if (!host) return null;
                if (host === 'x.com' || host === 't.co') return 'Twitter';
                if (host === 'linkedin.com' || host === 'lnkd.in') return 'LinkedIn';
                if (host === 'facebook.com' || host === 'fb.com' || host === 'fb.me') return 'Facebook';
                if (host === 'instagram.com') return 'Instagram';
                if (host === 'youtube.com' || host === 'youtu.be') return 'YouTube';
                if (host === 'tiktok.com' || host === 'vm.tiktok.com') return 'TikTok';
                if (host === 'github.com') return 'GitHub';
                if (host === 'threads.net') return 'Threads';
                if (host === 'bsky.app') return 'Bluesky';
                if (host === 'reddit.com' || host === 'old.reddit.com' || host === 'm.reddit.com') return 'Reddit';
                if (host === 'discord.gg' || host === 'discord.com') return 'Discord';
                if (host === 't.me') return 'Telegram';
                if (host.endsWith('mastodon.social') || host.indexOf('mastodon.') !== -1) return 'Mastodon';
                return null;
            }

            // Determine whether link is profile-like, share/intent (discard), or other
            function classifyKind(url, platform) {
                var p = String(url.pathname || '/');
                if (platform === 'Twitter') {
                    if (/^\/[A-Za-z0-9_]{1,15}$/.test(p)) return 'profile';
                    if (p.startsWith('/intent/')) return 'discard';
                    return 'other';
                }
                if (platform === 'LinkedIn') {
                    if (/^\/(company|in|school)\//.test(p)) return 'profile';
                    if (p.startsWith('/share') || p.indexOf('/sharing') !== -1) return 'discard';
                    return 'other';
                }
                if (platform === 'Facebook') {
                    if (/^\/(pages|people|profile\.php|groups)\//.test(p)) return 'profile';
                    if (p.indexOf('sharer.php') !== -1) return 'discard';
                    return 'other';
                }
                if (platform === 'Instagram') {
                    if (/^\/(p|reel)\//.test(p)) return 'other';
                    if (/^\/[A-Za-z0-9._]+$/.test(p)) return 'profile';
                    return 'other';
                }
                if (platform === 'YouTube') {
                    if (/^\/(channel|user|@)/.test(p)) return 'profile';
                    if (p.startsWith('/watch') || p.startsWith('/shorts')) return 'other';
                    return 'other';
                }
                if (platform === 'TikTok') {
                    if (/^\/@/.test(p)) return 'profile';
                    if (/\/video\//.test(p)) return 'other';
                    return 'other';
                }
                if (platform === 'GitHub') {
                    if (/^\/[A-Za-z0-9_.-]+$/.test(p)) return 'profile';
                    return 'other';
                }
                if (platform === 'Threads' || platform === 'Bluesky' || platform === 'Reddit') {
                    if (platform === 'Reddit' && /^\/(r|u)\//.test(p)) return 'profile';
                    if (/^\/@[A-Za-z0-9_.-]+$/.test(p)) return 'profile';
                    return 'other';
                }
                if (platform === 'Discord') {
                    if (/\/invite\//.test(p)) return 'other';
                    return 'other';
                }
                if (platform === 'Telegram') {
                    if (/^\/[A-Za-z0-9_]+$/.test(p)) return 'profile';
                    if (/\/joinchat\//.test(p)) return 'other';
                    return 'other';
                }
                if (platform === 'Mastodon') {
                    if (/^\/@/.test(p)) return 'profile';
                    return 'other';
                }
                return 'other';
            }

            function classifySocial(u) {
                var url = stripTracking(u);
                if (!url) return null;
                url = canonicalize(url);
                if (!url) return null;
                var host = (url.hostname || '').toLowerCase();
                var platform = platformForHost(host);
                if (!platform) return null;
                var kind = classifyKind(url, platform);
                if (kind === 'discard') return null;
                return { platform: platform, url: url.toString(), kind: (kind || 'other') };
            }

            // Aggregate external links across all pages + top-level
            var all = [];
            try {
                var uniq = new Set();
                (LINKS_EXTERNAL || []).forEach(function (u) { if (u) uniq.add(u); });
                (PAGES || []).forEach(function (p) {
                    try {
                        var arr = (p && p.links && Array.isArray(p.links.external)) ? p.links.external : [];
                        arr.forEach(function (u) { if (u) uniq.add(u); });
                    } catch (_) { }
                });
                all = Array.from(uniq.values());
            } catch (_) {
                all = LINKS_EXTERNAL || [];
            }

            var groups = {}; // platform -> {profiles:Set, other:Set}
            all.forEach(function (u) {
                try {
                    var c = classifySocial(u);
                    if (!c) return;
                    if (!groups[c.platform]) groups[c.platform] = { profiles: new Set(), other: new Set() };
                    groups[c.platform][c.kind === 'profile' ? 'profiles' : 'other'].add(c.url);
                } catch (_) { }
            });

            var html = '';
            Object.keys(groups).sort().forEach(function (plat) {
                var profiles = Array.from(groups[plat].profiles.values());
                var others = Array.from(groups[plat].other.values());
                var total = profiles.length + others.length;
                html += '<div class="mb-2"><div class="section-subheader">' + plat + ' <span class="small">(' + (total) + ')</span></div><ul class="domain-list domain-list-social">';
                // Profiles first
                profiles.forEach(function (u) {
                    var acts = '<span class="social-actions"><a href="' + encodeURI(u) + '" target="_blank" rel="noopener" class="btn btn-sm">↗</a><button class="btn btn-sm" onclick="copyLink(\'' + jsStr(u) + '\')">Copy</button>';
                    if (LOGGED_IN) {
                        acts += '<button class="btn btn-sm" onclick="attachProspectSocial(\'' + jsStr(plat.toLowerCase()) + '\', \'' + jsStr(u) + '\')">Attach to Prospect</button><button class="btn btn-sm" onclick="attachContactSocial(\'' + jsStr(u) + '\')">Attach to Contact</button>';
                    }
                    acts += '</span>';
                    html += '<li><span><a href="' + encodeURI(u) + '" target="_blank" rel="noopener">' + escapeHtml(u) + '</a></span>' + acts + '</li>';
                });
                // Other links
                others.forEach(function (u) {
                    var acts = '<span class="social-actions"><a href="' + encodeURI(u) + '" target="_blank" rel="noopener" class="btn btn-sm">↗</a><button class="btn btn-sm" onclick="copyLink(\'' + jsStr(u) + '\')">Copy</button>';
                    if (LOGGED_IN) {
                        acts += '<button class="btn btn-sm" onclick="attachProspectSocial(\'' + jsStr(plat.toLowerCase()) + '\', \'' + jsStr(u) + '\')">Attach to Prospect</button><button class="btn btn-sm" onclick="attachContactSocial(\'' + jsStr(u) + '\')">Attach to Contact</button>';
                    }
                    acts += '</span>';
                    html += '<li><span><a href="' + encodeURI(u) + '" target="_blank" rel="noopener">' + escapeHtml(u) + '</a></span>' + acts + '</li>';
                });
                html += '</ul></div>';
            });
            cont.innerHTML = html || '<div class="small">No social links detected.</div>';
        } catch (_) { }
    }

    /* ----- Links panel (external domains) pagination ----- */
    var LINKS_PAGER = null;

    function setupLinksPagination() {
        try {
            // Data and DOM targets
            if (!Array.isArray(TOP_EXTERNAL_DOMAINS) || TOP_EXTERNAL_DOMAINS.length === 0) return; // keep SSR empty state
            var sec = document.getElementById('m-stats'); if (!sec) return;
            var ul = sec.querySelector('ul.domain-list'); if (!ul) return;
            if (!ul.id) ul.id = 'ext-domains-list';

            var totalPages = Math.max(1, Math.ceil(TOP_EXTERNAL_DOMAINS.length / 10));
            LINKS_PAGER = {
                items: TOP_EXTERNAL_DOMAINS.slice(0),
                pageSize: 10,
                page: 1,
                totalPages: totalPages,
                ul: ul,
                pagerEl: null,
                prevBtn: null,
                nextBtn: null,
                pageLabel: null
            };

            renderLinksListSlice();
            // Always render controls; buttons are disabled as needed
            renderLinksControls();
        } catch (_) { }
    }

    function renderLinksListSlice() {
        try {
            var st = LINKS_PAGER; if (!st || !st.ul) return;
            var ul = st.ul;
            // Rebuild list: header row + page slice
            ul.innerHTML = '';
            var head = document.createElement('li');
            head.className = 'domain-list-header';
            head.innerHTML = '<span>URL</span><span class="domain-list-count">Count</span>';
            ul.appendChild(head);

            var start = (st.page - 1) * st.pageSize;
            var end = Math.min(st.items.length, start + st.pageSize);
            for (var i = start; i < end; i++) {
                var td = st.items[i] || {};
                var li = document.createElement('li');

                var left = document.createElement('span');
                var dom = String(td.domain || '');
                if (dom) {
                    var a = document.createElement('a');
                    a.href = 'https://' + dom;
                    a.target = '_blank';
                    a.rel = 'nofollow noopener';
                    a.textContent = dom;
                    left.appendChild(a);
                } else {
                    left.textContent = '';
                }

                var right = document.createElement('span');
                right.className = 'domain-list-count';
                right.textContent = String(td.count == null ? '' : td.count);

                li.appendChild(left);
                li.appendChild(right);
                ul.appendChild(li);
            }
        } catch (_) { }
    }

    function renderLinksControls() {
        try {
            var st = LINKS_PAGER; if (!st || !st.ul) return;
            if (!st.pagerEl) {
                var nav = document.createElement('div');
                nav.id = 'ext-domains-pager';
                nav.setAttribute('role', 'navigation');
                nav.setAttribute('aria-label', 'Links pagination');
                nav.className = 'small mt-1';
                // Center the controls
                try {
                    nav.style.display = 'flex';
                    nav.style.justifyContent = 'center';
                    nav.style.alignItems = 'center';
                    nav.style.gap = '8px';
                } catch (_) { }

                var prev = document.createElement('button');
                prev.type = 'button'; prev.className = 'btn btn-sm'; prev.textContent = 'Prev';
                prev.setAttribute('aria-controls', st.ul.id);

                var label = document.createElement('span');
                label.className = 'ml-1 mr-1';
                label.setAttribute('aria-live', 'polite');
                label.textContent = 'Page ' + st.page + ' of ' + st.totalPages;

                var next = document.createElement('button');
                next.type = 'button'; next.className = 'btn btn-sm'; next.textContent = 'Next';
                next.setAttribute('aria-controls', st.ul.id);

                prev.addEventListener('click', function () {
                    if (LINKS_PAGER.page > 1) {
                        LINKS_PAGER.page -= 1;
                        renderLinksListSlice();
                        updateLinksControls();
                    }
                });
                next.addEventListener('click', function () {
                    if (LINKS_PAGER.page < LINKS_PAGER.totalPages) {
                        LINKS_PAGER.page += 1;
                        renderLinksListSlice();
                        updateLinksControls();
                    }
                });

                nav.appendChild(prev);
                nav.appendChild(label);
                nav.appendChild(next);

                st.ul.parentNode.appendChild(nav);
                st.pagerEl = nav; st.prevBtn = prev; st.nextBtn = next; st.pageLabel = label;
            }
            updateLinksControls();
        } catch (_) { }
    }

    function updateLinksControls() {
        try {
            var st = LINKS_PAGER; if (!st || !st.pagerEl) return;
            // Always show controls; disable unavailable actions
            if (st.prevBtn) st.prevBtn.disabled = (st.page <= 1);
            if (st.nextBtn) st.nextBtn.disabled = (st.page >= st.totalPages);
            if (st.pageLabel) st.pageLabel.textContent = 'Page ' + st.page + ' of ' + st.totalPages;
        } catch (_) { }
    }

    /* ----- Emails preview helpers ----- */
    function copyEmail(email) {
        try { navigator.clipboard.writeText(email); showToast('Email copied'); } catch (_) { }
    }
    function copyAllEmails() {
        try {
            var all = (EMAILS_UNIQUE || []).join('\n');
            navigator.clipboard.writeText(all).then(function () { showToast('All emails copied'); });
        } catch (_) { }
    }
    function exportEmailsCsv() {
        try {
            var rows = [['email', 'first_url', 'found_as', 'domain']];
            var srcMap = buildEmailSourceMap();
            (EMAILS_UNIQUE || []).forEach(function (e) {
                var dom = (e.split('@')[1] || '').toLowerCase();
                var src = srcMap[e] || { foundAs: [], firstUrl: '' };
                rows.push([e, src.firstUrl || '', (src.foundAs || []).join(','), dom]);
            });
            var csv = rows.map(function (r) { return r.map(csvCell).join(','); }).join('\n');
            downloadBlob(csv, 'emails.csv', 'text/csv;charset=utf-8');
        } catch (_) { alert('Unable to export'); }
    }
    function csvCell(s) { var t = String(s == null ? '' : s); return /[",\n]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t; }
    function downloadBlob(text, filename, mime) {
        var blob = new Blob([text], { type: mime || 'text/plain;charset=utf-8' });
        var u = URL.createObjectURL(blob); var a = document.createElement('a'); a.href = u; a.download = filename || 'download.txt';
        document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(u);
    }

    /* ----- Pages enhancements ----- */
    function filterPages(term) {
        try {
            if (!PAGES_PAGER) {
                PAGES_PAGER = {
                    term: '',
                    page: 1,
                    pageSize: 15,
                    totalPages: 1,
                    ul: document.getElementById('pages-list'),
                    pagerEl: null, prevBtn: null, nextBtn: null, pageLabel: null,
                    sliceUrls: []
                };
            } else {
                PAGES_PAGER.ul = document.getElementById('pages-list') || PAGES_PAGER.ul;
            }
            PAGES_PAGER.term = String(term || '');
            PAGES_PAGER.page = 1; // reset to first page on new search
            renderPages();
        } catch (_) { }
    }

    /* Small helpers */
    function showToast(message) {
        try {
            var toast = document.createElement('div');
            toast.textContent = message;
            toast.style.cssText = 'position:fixed;top:20px;right:20px;background:var(--brand);color:white;padding:8px 16px;border-radius:4px;z-index:1000';
            document.body.appendChild(toast);
            setTimeout(function () { toast.remove(); }, 1600);
        } catch (_) { }
    }
    function escapeHtml(s) {
        if (typeof window !== 'undefined' && typeof window.escapeHtml === 'function') {
            return window.escapeHtml(s);
        }
        try {
            var div = document.createElement('div');
            div.textContent = String(s == null ? '' : s);
            return div.innerHTML;
        } catch (_) {
            return String(s == null ? '' : s);
        }
    }

    // Mobile tabs (activate only on small screens)
    (function () {
        var isMobile = false;
        try { isMobile = !!(window.matchMedia && window.matchMedia('(max-width: 768px)').matches); } catch (_) { }
        var mtabs = document.querySelectorAll('.mobile-tab');

        function hideAllMobileSections() {
            try {
                document.querySelectorAll('.mobile-section').forEach(function (sec) {
                    sec.classList.remove('active');
                    sec.style.display = 'none';
                });
            } catch (_) { }
        }

        if (!isMobile) {
            // Ensure mobile sections are not visible on desktop (clear any inline styles)
            hideAllMobileSections();
            return;
        }

        function activate(targetSel, btn) {
            // Hide all sections
            hideAllMobileSections();
            // Show target section (mobile only)
            var t = document.querySelector(targetSel);
            if (t) {
                try { t.classList.remove('hidden'); } catch (_) { }
                t.classList.add('active');
                t.style.display = 'block';
            }
            // Update tab aria state
            mtabs.forEach(function (b) { b.setAttribute('aria-selected', 'false'); });
            if (btn) btn.setAttribute('aria-selected', 'true');
        }
        // Click handlers
        mtabs.forEach(function (btn) {
            btn.addEventListener('click', function () {
                var tgt = this.getAttribute('data-target');
                if (tgt) activate(tgt, this);
            });
        });
        // Initial activation: prefer aria-selected="true", fallback to first tab
        try {
            var initial = document.querySelector('.mobile-tabs .mobile-tab[aria-selected="true"]') || mtabs[0];
            if (initial) {
                var target = initial.getAttribute('data-target');
                if (target) activate(target, initial);
            }
        } catch (_) { }
    })();

    // Simple email validation heuristics
    var DISPOSABLE_DOMAINS = new Set(['mailinator.com', '10minutemail.com', 'tempmail.email', 'yopmail.com', 'guerrillamail.com']);
    function emailStatus(email, baseDomain) {
        var re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!re.test(email)) return { label: 'Invalid', cls: 'status-invalid' };
        var dom = (email.split('@')[1] || '').toLowerCase();
        if (DISPOSABLE_DOMAINS.has(dom)) return { label: 'Disposable', cls: 'status-disposable' };
        baseDomain = (baseDomain || '').toLowerCase();
        if (baseDomain && (dom === baseDomain || dom.endsWith('.' + baseDomain))) return { label: 'Valid', cls: 'status-valid' };
        return { label: 'Unknown', cls: 'status-unknown' };
    }

    // Leads helpers
    function buildEmailSourceMap() {
        var map = {};
        EMAILS_SOURCES_RAW.forEach(function (x) {
            var key = x.email;
            if (!map[key]) map[key] = { foundAs: new Set(), firstUrl: x.url || '' };
            (x.found_as || []).forEach(function (f) { map[key].foundAs.add(f); });
            if (!map[key].firstUrl && x.url) map[key].firstUrl = x.url;
        });
        Object.keys(map).forEach(function (k) { map[k].foundAs = Array.from(map[k].foundAs.values()); });
        return map;
    }
    function computeMentions(email) {
        var c = 0;
        Object.keys(EMAILS_BY_URL).forEach(function (u) {
            var arr = EMAILS_BY_URL[u] || [];
            if (arr.indexOf(email) !== -1) c += 1;
        });
        return c;
    }
    function firstSourceUrlFallback(email) {
        for (var u in EMAILS_BY_URL) {
            if ((EMAILS_BY_URL[u] || []).indexOf(email) !== -1) return u;
        }
        return window.location.href;
    }
    function applyLeadFilters(row) {
        var domainFilter = (document.getElementById('lead-filter-domain').value || '').toLowerCase().trim();
        var types = Array.from(document.querySelectorAll('.lead-type:checked')).map(function (cb) { return cb.value; });
        var rowDom = (row.getAttribute('data-domain') || '').toLowerCase();
        var rowFound = (row.getAttribute('data-foundas') || '').split(',').filter(Boolean);
        if (domainFilter && rowDom.indexOf(domainFilter) === -1) return false;
        if (types.length > 0) {
            var any = rowFound.some(function (t) { return types.indexOf(t) !== -1; });
            if (!any) return false;
        }
        return true;
    }
    function renderLeads() {
        var tbody = document.getElementById('leads-tbody');
        if (!tbody) return;
        tbody.innerHTML = '';
        var srcMap = buildEmailSourceMap();
        EMAILS_UNIQUE.forEach(function (email) {
            var dom = (email.split('@')[1] || '').toLowerCase();
            var mentions = computeMentions(email);
            var foundAs = (srcMap[email] && srcMap[email].foundAs) ? srcMap[email].foundAs : [];
            var fUrl = (srcMap[email] && srcMap[email].firstUrl) ? srcMap[email].firstUrl : firstSourceUrlFallback(email);
            var st = emailStatus(email, BASE_DOMAIN);

            var tr = document.createElement('tr');
            tr.setAttribute('data-email', email);
            tr.setAttribute('data-domain', dom);
            tr.setAttribute('data-foundas', foundAs.join(','));
            var actionsHtml = '<button class="btn btn-sm" onclick="copyLink(\'' + jsStr(email) + '\')">Copy</button>';
            if (LOGGED_IN) {
                actionsHtml += ' <button class="btn btn-sm" onclick="addEmailToProspect(\'' + jsStr(email) + '\', \'' + jsStr(fUrl) + '\')">Add to Prospect</button>';
            }
            tr.innerHTML =
                '<td><input type="checkbox" class="lead-select"></td>' +
                '<td><code>' + escapeHtml(email) + '</code></td>' +
                '<td>' + mentions + '</td>' +
                '<td title="' + escapeHtml(foundAs.length ? ('Found on: ' + fUrl + '; as: ' + foundAs.join(',')) : 'N/A') + '">' + (foundAs.join(',') || '-') + '</td>' +
                '<td><span class="status-chip ' + st.cls + '">' + st.label + '</span></td>' +
                '<td>' + escapeHtml(dom || '-') + '</td>' +
                '<td>' + actionsHtml + '</td>';
            if (applyLeadFilters(tr)) tbody.appendChild(tr);
        });
        // Hook select all
        var sa = document.getElementById('lead-select-all');
        if (sa) {
            sa.checked = false;
            sa.onchange = function () {
                tbody.querySelectorAll('.lead-select').forEach(function (cb) { cb.checked = sa.checked; });
            };
        }
        // Toggle empty state message
        try {
            var empty = document.getElementById('leads-empty');
            if (empty) empty.classList.toggle('hidden', tbody.children.length > 0);
        } catch (_) { }
    }
    function clearLeadFilters() {
        var df = document.getElementById('lead-filter-domain'); if (df) df.value = '';
        document.querySelectorAll('.lead-type:checked').forEach(function (cb) { cb.checked = false; });
        renderLeads();
    }
    // Add Lead (client-only)
    function openAddLead() { document.getElementById('add-lead-form').style.display = 'block'; }
    function closeAddLead() { document.getElementById('add-lead-form').style.display = 'none'; }
    function saveAddedLead() {
        var e = (document.getElementById('add-email').value || '').trim().toLowerCase();
        var s = (document.getElementById('add-source').value || '').trim();
        var u = (document.getElementById('add-social').value || '').trim();
        var r = (document.getElementById('add-role').value || '').trim();
        var t = (document.getElementById('add-tags').value || '').trim();
        if (!e) { alert('Email is required'); return; }
        prospectEnsure().then(function (pid) {
            return apiJson('/api/prospects/' + encodeURIComponent(pid) + '/contacts', 'POST', {
                email: e,
                source_url: s || '',
                social_url: u || '',
                role_title: r || '',
                tags: t || ''
            });
        }).then(function () {
            if (EMAILS_UNIQUE.indexOf(e) === -1) EMAILS_UNIQUE.push(e);
            EMAILS_SOURCES_RAW.push({ email: e, url: s || window.location.href, found_as: [] });
            closeAddLead();
            renderLeads();
            try { trackEvent('add_lead_success'); } catch (_) { }
            alert('Contact added');
        }).catch(function (err) {
            if (err && err.status === 409) alert('Contact already exists');
            else if (err && err.status === 401) alert('Sign in to add');
            else alert('Unable to add contact');
        });
    }



    // Utilities
    function jsStr(s) { return String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'"); }
    function csv(s) { var t = String(s == null ? '' : s); if (/[",\n]/.test(t)) return '"' + t.replace(/"/g, '""') + '"'; return t; }

    function fmtMs(ms) {
        if (!ms || ms < 0) return '0s';
        var s = Math.floor(ms / 1000);
        if (s < 60) return s + 's';
        var m = Math.floor(s / 60), r = s % 60;
        return m + 'm ' + r + 's';
    }
    var __progressTimer = null;
    function startProgressPolling(crawlId) {
        if (!crawlId) return;
        function tick() {
            fetch('/api/progress/' + encodeURIComponent(crawlId), { credentials: 'same-origin' })
                .then(function (r) {
                    if (!r.ok) { throw { status: r.status }; }
                    return r.json();
                })
                .then(function (j) {
                    try {
                        var v = Number(j.visited_pages || 0), lim = (j.limits && Number(j.limits.max_pages)) || null;
                        var st = (String(j.status || '').toLowerCase());
                        // Status + counters
                        var ps = document.getElementById('progress-status'); if (ps) ps.textContent = String(j.status || '');
                        var pv = document.getElementById('progress-visited'); if (pv) pv.textContent = String(v);
                        var pt = document.getElementById('progress-total'); if (pt) pt.textContent = lim ? String(lim) : '?';
                        var pel = document.getElementById('progress-elapsed'); if (pel) pel.textContent = fmtMs(Number(j.elapsed_ms || 0));
                        // ETA and budget
                        var etaEl = document.getElementById('progress-eta');
                        if (etaEl) {
                            var etaMs = (j.est_remaining_ms == null ? null : Number(j.est_remaining_ms));
                            etaEl.textContent = (etaMs != null && !Number.isNaN(etaMs)) ? fmtMs(etaMs) : '—';
                        }
                        var budEl = document.getElementById('progress-budget');
                        var rem = (j.time_budget_remaining_ms == null ? null : Number(j.time_budget_remaining_ms));
                        if (budEl) budEl.textContent = (rem != null && !Number.isNaN(rem)) ? ('Budget left: ' + fmtMs(rem)) : '';
                        // Found-so-far counters (best-effort)
                        try {
                            var el;
                            el = document.getElementById('progress-emails'); if (el) el.textContent = String(j.emails_so_far || 0);
                            el = document.getElementById('progress-links-int'); if (el) el.textContent = String(j.links_internal_so_far || 0);
                            el = document.getElementById('progress-domains-ext'); if (el) el.textContent = String(j.external_domains_so_far || 0);
                        } catch (_) { }
                        // Progress bar
                        var pct = 0;
                        if (lim && lim > 0) pct = Math.max(0, Math.min(100, Math.round((v / lim) * 100)));
                        var bar = document.getElementById('progress-bar'); if (bar) bar.style.width = pct + '%';
                        // Finalizing condition (site scope): budget exhausted but status still running
                        if (st === 'running' && rem != null && !Number.isNaN(rem) && Number(rem) <= 0) {
                            if (ps) ps.textContent = 'finalizing…';
                            if (__progressTimer) { clearInterval(__progressTimer); __progressTimer = null; }
                            setTimeout(function () { location.reload(); }, 1000);
                            return;
                        }
                        // Stop on terminal states
                        if (st !== 'running' && st !== 'pending') {
                            if (__progressTimer) { clearInterval(__progressTimer); __progressTimer = null; }
                            setTimeout(function () { location.reload(); }, 800);
                        }
                    } catch (e) { }
                })
                .catch(function (_err) {
                    try {
                        if (__progressTimer) { clearInterval(__progressTimer); __progressTimer = null; }
                        var ps = document.getElementById('progress-status'); if (ps) ps.textContent = 'unavailable';
                    } catch (_) { }
                    setTimeout(function () { location.reload(); }, 1500);
                });
        }
        tick();
        __progressTimer = setInterval(tick, 2000);
    }

    /* Claim eligibility UI */
    function setupClaimEligibility() {
        try {
            var btn = document.getElementById('claim-btn');
            var label = document.getElementById('claim-status');
            var createdAt = __ctx.created_at || '';
            var minHours = Number(__ctx.claim_min_hours == null ? 24 : __ctx.claim_min_hours);
            if (!btn || !label || !__ctx.public_key) return;

            function update() {
                var now = new Date();
                var created = createdAt ? new Date(createdAt) : now;
                var eligibleAt = new Date(created.getTime() + (minHours * 3600000));
                var ms = eligibleAt - now;
                if (ms <= 0) {
                    btn.disabled = false;
                    btn.setAttribute('aria-disabled', 'false');
                    label.textContent = 'You can claim this analysis.';
                    return true;
                }
                btn.disabled = true;
                btn.setAttribute('aria-disabled', 'true');
                var s = Math.max(0, Math.floor(ms / 1000));
                var m = Math.floor(s / 60);
                var r = s % 60;
                label.textContent = 'Eligible in ' + (m > 0 ? (m + 'm ') : '') + (r + 's');
                return false;
            }

            update();
            btn.addEventListener('click', claimAnalysis);
            var timer = setInterval(function () { if (update()) { try { clearInterval(timer); } catch (_) { } } }, 1000);
        } catch (_) { }
    }

    /* Public progress polling by short key */
    function startPublicProgressPolling(pubKey) {
        if (!pubKey) return;
        function tick() {
            fetch('/api/progress/public/' + encodeURIComponent(pubKey))
                .then(function (r) {
                    if (!r.ok) { throw { status: r.status }; }
                    return r.json();
                })
                .then(function (j) {
                    try {
                        var v = Number(j.visited_pages || 0), lim = (j.limits && Number(j.limits.max_pages)) || null;
                        var st = (String(j.status || '').toLowerCase());
                        // Status + counters
                        var ps = document.getElementById('progress-status'); if (ps) ps.textContent = String(j.status || '');
                        var pv = document.getElementById('progress-visited'); if (pv) pv.textContent = String(v);
                        var pt = document.getElementById('progress-total'); if (pt) pt.textContent = lim ? String(lim) : '?';
                        var pel = document.getElementById('progress-elapsed'); if (pel) pel.textContent = fmtMs(Number(j.elapsed_ms || 0));
                        // ETA and budget
                        var etaEl = document.getElementById('progress-eta');
                        if (etaEl) {
                            var etaMs = (j.est_remaining_ms == null ? null : Number(j.est_remaining_ms));
                            etaEl.textContent = (etaMs != null && !Number.isNaN(etaMs)) ? fmtMs(etaMs) : '—';
                        }
                        var budEl = document.getElementById('progress-budget');
                        var rem = (j.time_budget_remaining_ms == null ? null : Number(j.time_budget_remaining_ms));
                        if (budEl) budEl.textContent = (rem != null && !Number.isNaN(rem)) ? ('Budget left: ' + fmtMs(rem)) : '';
                        // Found-so-far counters (best-effort)
                        try {
                            var el;
                            el = document.getElementById('progress-emails'); if (el) el.textContent = String(j.emails_so_far || 0);
                            el = document.getElementById('progress-links-int'); if (el) el.textContent = String(j.links_internal_so_far || 0);
                            el = document.getElementById('progress-domains-ext'); if (el) el.textContent = String(j.external_domains_so_far || 0);
                        } catch (_) { }
                        // Progress bar
                        var pct = 0;
                        if (lim && lim > 0) pct = Math.max(0, Math.min(100, Math.round((v / lim) * 100)));
                        var bar = document.getElementById('progress-bar'); if (bar) bar.style.width = pct + '%';
                        // Finalizing condition (site scope)
                        if (st === 'running' && rem != null && !Number.isNaN(rem) && Number(rem) <= 0) {
                            if (ps) ps.textContent = 'finalizing…';
                            if (__progressTimer) { clearInterval(__progressTimer); __progressTimer = null; }
                            setTimeout(function () { location.reload(); }, 1000);
                            return;
                        }
                        // Stop on terminal states
                        if (st !== 'running' && st !== 'pending') {
                            if (__progressTimer) { clearInterval(__progressTimer); __progressTimer = null; }
                            setTimeout(function () { location.reload(); }, 800);
                        }
                    } catch (e) { }
                })
                .catch(function (err) {
                    try {
                        if (__progressTimer) { clearInterval(__progressTimer); __progressTimer = null; }
                        var ps = document.getElementById('progress-status'); if (ps) ps.textContent = 'unavailable';
                    } catch (_) { }
                    // Single refresh to avoid loops; SSR will render final state or 404
                    setTimeout(function () { location.reload(); }, 1500);
                });
        }
        tick();
        __progressTimer = setInterval(tick, 2000);
    }

    // Init
    (function init() {
        renderLeads();
        var _lfd = document.getElementById('lead-filter-domain');
        if (_lfd) { _lfd.addEventListener('input', renderLeads); }
        document.querySelectorAll('.lead-type').forEach(function (cb) { cb.addEventListener('change', renderLeads); });
        renderPages();
        // Pages filters listeners
        renderSocial();
        // Links (external domains) pagination - JS only
        try { setupLinksPagination(); } catch (_) { }

        // Wire prospects + claim
        try { var pt = document.getElementById('prospect-toggle'); if (pt) pt.addEventListener('click', prospectToggle); } catch (_) { }
        try { setupClaimEligibility(); } catch (_) { }

        // Wire owner toggles
        try {
            var listedToggle = document.getElementById('listed-toggle');
            var listedBtn = document.getElementById('listed-btn');
            if (listedToggle && listedBtn) {
                listedBtn.addEventListener('click', function () {
                    var enabled = listedToggle.checked;
                    setListed(enabled).then(function () {
                        showToast('Listed status updated');
                    }).catch(function (e) {
                        showToast('Failed to update listed status');
                    });
                });
            }
            var shareToggle = document.getElementById('share-toggle');
            var shareBtn = document.getElementById('share-btn');
            if (shareToggle && shareBtn) {
                shareBtn.addEventListener('click', function () {
                    var enabled = shareToggle.checked;
                    setShare(enabled).then(function (res) {
                        if (res.share_key) {
                            SHARE_URL = '/analysis/shared/' + res.share_key;
                        }
                        showToast('Share status updated');
                    }).catch(function (e) {
                        showToast('Failed to update share status');
                    });
                });
            }
            var copyBtn = document.getElementById('copy-share-btn');
            if (copyBtn) {
                copyBtn.addEventListener('click', copyShareUrl);
            }
        } catch (_) { }

        // Start progress polling (prefer public when available to work for anonymous viewers and non-owners)
        try {
            var _pubKey = __ctx.public_key || '';
            var _cid = __ctx.crawl_id || '';
            var _st = String(__ctx.status || '').toLowerCase();
            if (_pubKey && (_st === 'pending' || _st === 'running')) {
                startPublicProgressPolling(_pubKey);
            } else if (_cid && (_st === 'pending' || _st === 'running')) {
                startProgressPolling(_cid);
            }
        } catch (_) { }
    })();
})();
