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

    function renderPages() {
        try {
            var ul = document.getElementById('pages-list'); if (!ul) return;

            if (!PAGES_PAGER) {
                PAGES_PAGER = {
                    term: '', page: 1, pageSize: 7, totalPages: 1, ul: ul,
                    pagerEl: null, prevBtn: null, nextBtn: null, pageLabel: null, sliceUrls: []
                };
            } else {
                PAGES_PAGER.ul = ul;
                if (PAGES_PAGER.pageSize == null) PAGES_PAGER.pageSize = 7;
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
            // Header row - explicit metric columns
            var headLi = document.createElement('li');
            headLi.className = 'domain-list-header pages-header-row';
            headLi.innerHTML =
                '<span class="ph-page">Page</span>' +
                '<span class="ph-metric">Words</span>' +
                '<span class="ph-metric">Sentences</span>' +
                '<span class="ph-metric">Paragraphs</span>' +
                '<span class="ph-tokens" style="text-align: right; margin-right: 12px;">~Tokens</span>';
            ul.appendChild(headLi);

            slice.forEach(function (p) {
                var li = document.createElement('li');
                li.className = 'pages-row';
                var url = p.url || '';
                li.setAttribute('data-url', url);
                var path = _pageShortPath(url);
                var title = _pageTitle(p) || '';
                var cm = p.content_metrics || {};
                var words = (cm.words != null) ? Number(cm.words) : 0;
                var paragraphs = (cm.paragraphs != null) ? Number(cm.paragraphs) : 0;
                var md = String(p.markdown || '');
                var sentenceMatches = md.match(/[^.!?\n]+[.!?]+(?=\s|$)/g);
                var sentences = sentenceMatches ? sentenceMatches.length : 0;
                var tokens = Math.round(words * 1.33);

                li.innerHTML =
                    '<span class="ps-page" title="' + escapeHtml(url) + '"><span class="ps-path">' + escapeHtml(path) + '</span><span class="ps-title">' + escapeHtml(title) + '</span></span>' +
                    '<span class="ps-metric">' + (words ? words.toLocaleString() : '—') + '</span>' +
                    '<span class="ps-metric">' + (sentences ? sentences.toLocaleString() : '—') + '</span>' +
                    '<span class="ps-metric">' + (paragraphs ? paragraphs.toLocaleString() : '—') + '</span>' +
                    '<span class="ps-tokens" style="text-align: right; margin-right: 12px;">' + (tokens ? tokens.toLocaleString() : '—') + '</span>';

                li.addEventListener('click', function () {
                    previewPageByUrl(url);
                    try { trackEvent('page_preview'); } catch (_) { }
                });

                ul.appendChild(li);
            });

            try { var pc = document.getElementById('pages-count'); if (pc) pc.textContent = String(filtered.length); } catch (_) { }

            var firstWithMd = slice.find(function (p) { return !!((p.markdown || '').trim().length); });
            if (firstWithMd) {
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

        // Clear inline style display changes that can clash
        preEl.style.display = '';
        contentEl.style.display = '';

        if (markdownRawVisible) {
            // Show raw markdown, hide rendered using classList toggling (bulletproof against CSS !important rules on .hidden)
            preEl.textContent = (md || '');
            preEl.classList.remove('hidden');
            contentEl.classList.add('hidden');
        } else {
            // Show rendered HTML, hide raw using classList toggling (bulletproof)
            preEl.textContent = (md || '');
            preEl.classList.add('hidden');
            contentEl.classList.remove('hidden');
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
                preEl.classList.remove('hidden');
                contentEl.classList.add('hidden');
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

            function extractHandle(u, platform) {
                try {
                    var url = new URL(u);
                    var p = url.pathname || '/';
                    if (p.endsWith('/')) p = p.slice(0, -1);
                    var parts = p.split('/').filter(Boolean);
                    if (parts.length === 0) return url.hostname;

                    if (platform === 'Twitter') {
                        return '@' + parts[0];
                    }
                    if (platform === 'LinkedIn') {
                        if (parts[0] === 'company' || parts[0] === 'in' || parts[0] === 'school') {
                            return parts[1] || parts[0];
                        }
                        return parts[0];
                    }
                    if (platform === 'Facebook') {
                        if (parts[0] === 'pages' || parts[0] === 'people' || parts[0] === 'groups') {
                            return parts[1] || parts[0];
                        }
                        return parts[0];
                    }
                    if (platform === 'Instagram') {
                        return '@' + parts[0];
                    }
                    if (platform === 'YouTube') {
                        return parts[0];
                    }
                    if (platform === 'TikTok') {
                        return parts[0];
                    }
                    if (platform === 'GitHub') {
                        if (parts.length > 1) {
                            return parts[0] + '/' + parts[1];
                        }
                        return '@' + parts[0];
                    }
                    if (platform === 'Reddit') {
                        if (parts[0] === 'r' || parts[0] === 'u') {
                            return parts[0] + '/' + (parts[1] || '');
                        }
                        return parts[0];
                    }
                    if (platform === 'Threads' || platform === 'Bluesky' || platform === 'Telegram' || platform === 'Mastodon') {
                        return '@' + parts[0].replace(/^@/, '');
                    }
                    return parts.join('/');
                } catch (_) {
                    return u;
                }
            }

            var html = '<div class="social-grid">';
            Object.keys(groups).sort().forEach(function (plat) {
                var profiles = Array.from(groups[plat].profiles.values());
                var others = Array.from(groups[plat].other.values());
                var allLinks = profiles.concat(others);

                allLinks.forEach(function (u) {
                    var handle = extractHandle(u, plat);
                    var cleanUrl = u.replace(/^https?:\/\//i, '');
                    var platClass = 'brand-' + plat.toLowerCase();

                    var acts = '<div class="social-card-actions">';
                    acts += '<a href="' + encodeURI(u) + '" target="_blank" rel="noopener" class="btn btn-sm btn-tertiary">Open ↗</a>';
                    acts += '<button class="btn btn-sm btn-ghost" onclick="copyLink(\'' + jsStr(u) + '\')">Copy</button>';
                    if (LOGGED_IN) {
                        acts += '<button class="btn btn-sm btn-ghost" onclick="attachProspectSocial(\'' + jsStr(plat.toLowerCase()) + '\', \'' + jsStr(u) + '\')">Attach to Prospect</button>';
                        acts += '<button class="btn btn-sm btn-ghost" onclick="attachContactSocial(\'' + jsStr(u) + '\')">Attach to Contact</button>';
                    }
                    acts += '</div>';

                    html += '<div class="social-card-v2 ' + platClass + '">';
                    html += '  <div class="social-card-v2-header">';
                    html += '    <span class="social-plat-badge">' + plat + '</span>';
                    html += '    <span class="social-handle-lbl">' + escapeHtml(handle) + '</span>';
                    html += '  </div>';
                    html += '  <div class="social-card-v2-body">';
                    html += '    <a href="' + encodeURI(u) + '" target="_blank" rel="noopener" class="social-url-link">' + escapeHtml(cleanUrl) + '</a>';
                    html += '  </div>';
                    html += '  ' + acts;
                    html += '</div>';
                });
            });
            html += '</div>';
            cont.innerHTML = html || '<div class="small">No social links detected.</div>';
        } catch (_) { }
    }

    /* ----- Emails preview helpers ----- */
    function renderLeads() {
        var tbody = document.getElementById('leads-tbody');
        if (!tbody) return;
        tbody.innerHTML = '';

        if (!EMAILS_UNIQUE || EMAILS_UNIQUE.length === 0) {
            var empty = document.getElementById('leads-empty');
            if (empty) empty.classList.remove('hidden');
            return;
        }

        EMAILS_UNIQUE.forEach(function (email) {
            // Find which page this email was found on
            var foundOn = '';
            for (var url in EMAILS_BY_URL) {
                if (EMAILS_BY_URL[url] && EMAILS_BY_URL[url].indexOf(email) >= 0) {
                    foundOn = url.replace(/^https?:\/\/[^/]+/, '') || '/';
                    break;
                }
            }
            // Find source type (mailto, text, obfuscated)
            var sourceType = 'text';
            EMAILS_SOURCES_RAW.forEach(function (s) {
                if (s.email === email && s.found_as) sourceType = s.found_as;
            });

            var tr = document.createElement('tr');
            tr.innerHTML =
                '<td><code>' + escapeHtml(email) + '</code></td>' +
                '<td class="small">' + escapeHtml(foundOn) + '</td>';
            tbody.appendChild(tr);
        });

        // Toggle empty state message
        try {
            var empty = document.getElementById('leads-empty');
            if (empty) empty.classList.toggle('hidden', tbody.children.length > 0);
        } catch (_) { }
    }

    /* ----- Pages enhancements ----- */
    function filterPages(term) {
        try {
            if (!PAGES_PAGER) {
                PAGES_PAGER = {
                    term: '',
                    page: 1,
                    pageSize: 7,
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

    // Utilities
    function jsStr(s) { return String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'"); }
    function csv(s) { var t = String(s == null ? '' : s); if (/[",\n]/.test(t)) return '"' + t.replace(/"/g, '""') + '"'; return t; }

    // Init
    (function init() {
        renderLeads();
        renderPages();
        renderSocial();

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
                            // Update global for copy
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

        // Scroll helpers for score sections
        try {
            window.scrollToSection = function (id) {
                var el = document.getElementById(id);
                if (el) {
                    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
            };
        } catch (_) { }

        // Bind broken scoping functions globally (window scope) to activate inline onclick handlers
        window.toggleMarkdownView = toggleMarkdownView;
        window.copyCurrentPage = copyCurrentPage;
        window.downloadCurrentPage = downloadCurrentPage;
        window.previewPageByUrl = previewPageByUrl;
        window.filterPages = filterPages;
    })();
})();
