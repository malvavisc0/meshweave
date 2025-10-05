
/* Result Analysis JavaScript - Extracted from result.html */
(function() {
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
    // AI chat limits from server (with safe defaults)
    const MAX_PAGES = Number(__ctx.ai_chat_max_pages || 5);
    const MAX_CHARS_PER_PAGE = Number(__ctx.ai_chat_max_chars_per_page || 3000);
    const MAX_TOTAL_CHARS = Number(__ctx.ai_chat_max_total_chars || 15000);
    // Logged-in state and capability flags from server
    let LOGGED_IN = !!(__ctx.logged_in);
    const CAN_CHAT = !!(__ctx.can_chat);
    const CAN_SELECT_PAGES = !!(__ctx.can_select_pages);
    var PROSPECT_ID = null; var PROSPECT_SOCIALS = [];
    // Streaming state for Ask button gating
    var IS_STREAMING = false;
    // Abort controller for in-flight chat requests
    var CHAT_ABORT_CTRL = null;
    // Selection state: Map<url, markdown>
    const SELECTED_PAGE_CONTENT = new Map(); // url -> markdown content
    var LAST_PRODUCT_ID = (function(){ try { return localStorage.getItem('pb:last_product_id') || ''; } catch(_) { return ''; } })();
    // Pages pager state (initialized on first render)
    var PAGES_PAGER = null;


    // Chat drawer
    var CHAT_EL = null; var CHAT_LARGE = false;
    function openChat() {
        try {
            var sec = document.getElementById('compose-chat');
            if (sec && sec.scrollIntoView) sec.scrollIntoView({behavior:'smooth', block:'start'});
            var input = document.getElementById('chat-question');
            if (input && input.focus) input.focus();
        } catch(_){}
    }
    function closeChat() {
        CHAT_EL = CHAT_EL || document.getElementById('chat-drawer');
        CHAT_EL.style.display = 'none'; CHAT_EL.setAttribute('aria-hidden','true');
    }
    function toggleChatSize() {
        CHAT_LARGE = !CHAT_LARGE;
        CHAT_EL = CHAT_EL || document.getElementById('chat-drawer');
        CHAT_EL.style.height = CHAT_LARGE ? '70vh' : '420px';
        CHAT_EL.style.width = CHAT_LARGE ? '600px' : '360px';
    }

    /* Prospect helpers */
    function apiJson(url, method, body) {
        return fetch(url, {
            method: method || 'GET',
            headers: {'Content-Type': 'application/json'},
            credentials: 'same-origin',
            body: body ? JSON.stringify(body) : undefined
        }).then(async function(r){
            var data = null;
            try { data = await r.json(); } catch(_){}
            if (!r.ok) { throw {status: r.status, body: data}; }
            return data || {};
        });
    }
    function siteRoot() {
        var d = (BASE_DOMAIN || '').replace(/^www\./,'');
        return d ? ('https://'+d+'/') : (window.location.origin+'/');
    }
    function setProspectStatusChip(text){
        try {
            var chip = document.getElementById('prospect-status-chip');
            if (chip) chip.textContent = text || 'Shortlisted';
        } catch(_){}
    }

    function prospectEnsure(){
        if (PROSPECT_ID) return Promise.resolve(PROSPECT_ID);
        var domain = (BASE_DOMAIN || '').toLowerCase();
        if (!domain) return Promise.reject(new Error('Missing base domain'));
        return apiJson('/api/prospects','POST',{
            domain: domain,
            url: siteRoot(),
            status: 'shortlisted'
        }).then(function(row){
            PROSPECT_ID = row.id || null;
            PROSPECT_SOCIALS = Array.isArray(row.socials) ? row.socials.slice(0) : [];
            setProspectStatusChip((row.status||'shortlisted').charAt(0).toUpperCase()+ (row.status||'shortlisted').slice(1));
            return PROSPECT_ID;
        });
    }
    function prospectToggle(){
        prospectEnsure().then(function(){
            alert('Prospect saved');
        }).catch(function(e){
            if (e && e.status === 401) alert('Sign in to save prospect');
            else alert('Unable to save prospect');
        });
    }
    function openProspectManage(){
        var status = prompt('Prospect status (shortlisted/contacted/replied/won/lost):','shortlisted');
        if (!status) return;
        var tags = prompt('Tags (comma-separated):','') || '';
        var notes = prompt('Notes:','') || '';
        prospectEnsure().then(function(pid){
            return apiJson('/api/prospects/'+encodeURIComponent(pid), 'PATCH', {
                status: (status||'').trim().toLowerCase(),
                tags: tags,
                notes: notes
            });
        }).then(function(row){
            setProspectStatusChip((row.status||'shortlisted').charAt(0).toUpperCase()+ (row.status||'shortlisted').slice(1));
            alert('Prospect updated');
        }).catch(function(e){
            if (e && e.status === 401) alert('Sign in to manage prospect');
            else alert('Unable to update prospect');
        });
    }
    function attachProspectSocial(platform,url){
        prospectEnsure().then(function(pid){
            // merge unique by url
            var list = Array.isArray(PROSPECT_SOCIALS) ? PROSPECT_SOCIALS.slice(0) : [];
            var exists = list.some(function(x){ return (x && (x.url||'').toLowerCase()) === String(url||'').toLowerCase(); });
            if (!exists) list.push({platform: (platform||'').toLowerCase(), url: url});
            return apiJson('/api/prospects/'+encodeURIComponent(pid), 'PATCH', { socials: list });
        }).then(function(row){
            PROSPECT_SOCIALS = Array.isArray(row.socials) ? row.socials.slice(0) : [];
            alert('Attached to prospect');
        }).catch(function(e){
            if (e && e.status === 401) alert('Sign in to attach');
            else alert('Unable to attach to prospect');
        });
    }
    function attachContactSocial(url){
        var email = prompt('Contact email to attach this social URL to:', '');
        if (!email) return;
        prospectEnsure().then(function(pid){
            return apiJson('/api/prospects/'+encodeURIComponent(pid)+'/contacts','POST',{
                email: email,
                social_url: url
            });
        }).then(function(){
            alert('Attached to contact');
        }).catch(function(e){
            if (e && e.status === 409) alert('Contact already exists');
            else if (e && e.status === 401) alert('Sign in to attach');
            else alert('Unable to attach to contact');
        });
    }
    function addEmailToProspect(email, sourceUrl){
        if (!email) return;
        prospectEnsure().then(function(pid){
            return apiJson('/api/prospects/'+encodeURIComponent(pid)+'/contacts', 'POST', {
                email: email,
                source_url: sourceUrl || ''
            });
        }).then(function(){
            alert('Email added to prospect');
        }).catch(function(e){
            if (e && e.status === 409) alert('Email already added');
            else if (e && e.status === 401) alert('Sign in to add');
            else alert('Unable to add email');
        });
    }
    function claimAnalysis(){
        try {
            var key = __ctx.public_key || '';
            if (!key) { alert('Not claimable'); return; }
            apiJson('/api/claim/public/'+encodeURIComponent(key), 'POST', {}).then(function(res){
                alert('Claimed');
                if (res && res.id) { window.location.href = '/analysis/'+res.id; }
            }).catch(function(e){
                if (e && e.status === 400) alert('Not eligible yet');
                else if (e && e.status === 409) alert('Already claimed or not claimable');
                else if (e && e.status === 401) alert('Sign in to claim');
                else alert('Unable to claim');
            });
        } catch(_){}
    }

    /* Pages helpers */
    /* Helpers for Pages filtering and preview */
    function setPreviewHint(msg){
        try {
            var id='page-preview-hint';
            var el=document.getElementById(id);
            if (!el) {
                el=document.createElement('div'); el.id=id; el.className='small';
                var pre=document.getElementById('page-markdown'); var parent = pre ? pre.parentNode : null;
                if (parent) parent.appendChild(el);
            }
            el.textContent = msg || '';
            el.style.display = msg ? '' : 'none';
        } catch(_){}
    }
    function setActiveRowByUrl(url){
        try {
            document.querySelectorAll('#pages-list li').forEach(function(li){
                li.classList.toggle('active', (li.getAttribute('data-url')||'') === url);
            });
        } catch(_){}
    }
    function previewPageByUrl(url){
        try {
            var p = (PAGES||[]).find(function(x){ return (x && (x.url||'')===url); });
            var md = (p && (p.markdown||'').trim()) || '';
            if (md) {
                renderMarkdown(md);
                setPreviewHint('');
            } else {
                renderMarkdown('');
                setPreviewHint('No per-page markdown available.');
            }
            setActiveRowByUrl(url);
        } catch(_){}
    }
    function renderPages(){
        try{
            var ul = document.getElementById('pages-list'); if (!ul) return;

            // Initialize pager state if needed
            if (!PAGES_PAGER) {
                PAGES_PAGER = {
                    term: '',
                    page: 1,
                    pageSize: 15,
                    totalPages: 1,
                    ul: ul,
                    pagerEl: null,
                    prevBtn: null,
                    nextBtn: null,
                    pageLabel: null,
                    sliceUrls: []
                };
            } else {
                PAGES_PAGER.ul = ul;
                if (PAGES_PAGER.pageSize == null) PAGES_PAGER.pageSize = 15;
                if (!Array.isArray(PAGES_PAGER.sliceUrls)) PAGES_PAGER.sliceUrls = [];
            }

            // Filter dataset by search term (URL match)
            // Fallback: when no PAGES provided, use LINKS_INTERNAL as URL-only rows
            var rows = (PAGES || []).filter(function(p){ return !!p; });
            if ((!rows || rows.length === 0) && Array.isArray(LINKS_INTERNAL) && LINKS_INTERNAL.length > 0) {
                rows = LINKS_INTERNAL.map(function(u){ return { url: u, markdown: '' }; });
            }
            var term = String(PAGES_PAGER.term || '').toLowerCase().trim();
            var filtered = term
                ? rows.filter(function(p){
                    var u = String(p.url || '').toLowerCase();
                    return u.indexOf(term) !== -1;
                })
                : rows;

            // Compute pagination
            PAGES_PAGER.totalPages = Math.max(1, Math.ceil(filtered.length / PAGES_PAGER.pageSize));
            if (PAGES_PAGER.page < 1) PAGES_PAGER.page = 1;
            if (PAGES_PAGER.page > PAGES_PAGER.totalPages) PAGES_PAGER.page = PAGES_PAGER.totalPages;

            var start = (PAGES_PAGER.page - 1) * PAGES_PAGER.pageSize;
            var end = Math.min(filtered.length, start + PAGES_PAGER.pageSize);
            var slice = filtered.slice(start, end);

            // Track current slice URLs for Select All / Clear operations
            PAGES_PAGER.sliceUrls = slice.map(function(p){ return p.url || ''; });

            // Render current slice
            ul.innerHTML = '';
            slice.forEach(function(p){
                var li = document.createElement('li');
                var url = p.url || '';
                li.setAttribute('data-url', url);

                // Initial checkbox checked state mirrors inclusion map
                var isIncluded = SELECTED_PAGE_CONTENT.has(url);
                if (isIncluded) li.classList.add('selected');

                // Render checkbox; disable when selection is not allowed
                li.innerHTML = '<input type="checkbox" '+(CAN_SELECT_PAGES ? '' : 'disabled ') + (isIncluded?'checked':'')+'> <span class="small">'+escapeHtml(url)+'</span>';
 
                var cb = li.querySelector('input[type=checkbox]');
                if (cb) {
                    if (!CAN_SELECT_PAGES) {
                        cb.disabled = true;
                    } else {
                        cb.addEventListener('change', function(){
                            if (cb.checked) {
                                // Enforce max pages selection on client
                                if (SELECTED_PAGE_CONTENT.size >= MAX_PAGES) {
                                    cb.checked = false;
                                    try { showToast('Only ' + MAX_PAGES + ' pages can be selected'); } catch(_){}
                                    return;
                                }
                                // store markdown content at selection time
                                SELECTED_PAGE_CONTENT.set(url, ((p && (p.markdown||'').trim()) || ''));
                            } else {
                                SELECTED_PAGE_CONTENT.delete(url);
                            }
                            li.classList.toggle('selected', cb.checked);
                            updateSelectionCount();
                        });
                    }
                }

                li.addEventListener('click', function(e){
                    var target = e.target || {};
                    var tag = (target.tagName || '').toLowerCase();
                    if (tag === 'input') {
                        previewPageByUrl(url);
                        try { trackEvent('page_select_toggle'); } catch(_){}
                        return;
                    }
                    // If selection disabled: allow preview only, do not toggle selection
                    if (!CAN_SELECT_PAGES) {
                        previewPageByUrl(url);
                        try { trackEvent('page_preview'); } catch(_){}
                        return;
                    }
                    // Selection allowed: toggle selection (via checkbox) then preview
                    if (cb) {
                        cb.checked = !cb.checked;
                        try { cb.dispatchEvent(new Event('change')); } catch(_){
                            if (cb.checked) { SELECTED_PAGE_CONTENT.set(url, ((p && (p.markdown||'').trim()) || '')); } else { SELECTED_PAGE_CONTENT.delete(url); }
                            li.classList.toggle('selected', cb.checked);
                            updateSelectionCount();
                        }
                    }
                    previewPageByUrl(url);
                    try { trackEvent('page_select_toggle'); } catch(_){}
                });

                ul.appendChild(li);
            });

            // Update pages count (filtered)
            try { var pc = document.getElementById('pages-count'); if (pc) pc.textContent = String(filtered.length); } catch(_){}

            // Initial preview: first item with markdown on current slice; show hint if none
            var firstWithMd = slice.find(function(p){ return !!((p.markdown || '').trim().length); });
            if (firstWithMd) {
                previewPageByUrl(firstWithMd.url || '');
            } else {
                renderMarkdown('');
                setPreviewHint(filtered.length ? 'No per-page markdown available.' : 'No pages to display.');
            }

            // Pager controls (always visible; disabled as needed)
            renderPagesControls();
            updatePagesControls();

            // Ensure the "Included pages" counter reflects current inclusion set and checkboxes
            updateSelectionCount();
            if (!CAN_SELECT_PAGES) { enforceSelectionRestrictions(); }
        }catch(_){}
    }
    function renderPagesControls(){
        try{
            var st = PAGES_PAGER; if (!st || !st.ul) return;
            if (!st.pagerEl) {
                var nav = document.createElement('div');
                nav.id = 'pages-pager';
                nav.setAttribute('role','navigation');
                nav.setAttribute('aria-label','Pages pagination');
                nav.className = 'small mt-1';
                try {
                    nav.style.display = 'flex';
                    nav.style.justifyContent = 'center';
                    nav.style.alignItems = 'center';
                    nav.style.gap = '8px';
                } catch(_){}

                var prev = document.createElement('button');
                prev.type = 'button'; prev.className = 'btn btn-sm'; prev.textContent = 'Prev';
                prev.setAttribute('aria-controls', st.ul.id || 'pages-list');

                var label = document.createElement('span');
                label.className = 'ml-1 mr-1';
                label.setAttribute('aria-live','polite');
                label.textContent = 'Page ' + st.page + ' of ' + st.totalPages;

                var next = document.createElement('button');
                next.type = 'button'; next.className = 'btn btn-sm'; next.textContent = 'Next';
                next.setAttribute('aria-controls', st.ul.id || 'pages-list');

                prev.addEventListener('click', function(){
                    if (PAGES_PAGER.page > 1) {
                        PAGES_PAGER.page -= 1;
                        renderPages();
                    }
                });
                next.addEventListener('click', function(){
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
        }catch(_){}
    }

    function updatePagesControls(){
        try{
            var st = PAGES_PAGER; if (!st || !st.pagerEl) return;
            if (st.prevBtn) st.prevBtn.disabled = (st.page <= 1);
            if (st.nextBtn) st.nextBtn.disabled = (st.page >= st.totalPages);
            if (st.pageLabel) st.pageLabel.textContent = 'Page ' + st.page + ' of ' + st.totalPages;
        }catch(_){}
    }

    function renderMarkdown(md){
        var pre=document.getElementById('page-markdown'); if(pre){ pre.textContent = (md||''); }
    }
    /* Include/copy/download actions for Pages */
    function copyCurrentPage(){
        try {
            var pre=document.getElementById('page-markdown'); var text=(pre && (pre.textContent||pre.innerText)||'');
            if (navigator.clipboard && window.isSecureContext) {
                navigator.clipboard.writeText(text).then(function(){ alert('Copied'); }, function(){ legacyCopy(text); });
            } else { legacyCopy(text); }
        } catch(_) { try{ legacyCopy(''); }catch(__){} }
    }
    function downloadCurrentPage(){
        try {
            var pre=document.getElementById('page-markdown'); var md=(pre && (pre.textContent||pre.innerText)||'');
            if(!md.trim()){ alert('Nothing to download'); return; }
            var liSel = document.querySelector('#pages-list li input[type=checkbox]:checked');
            var li = liSel ? liSel.closest('li') : (document.querySelector('#pages-list li.active') || null);
            var url = li ? (li.getAttribute('data-url')||'') : '';
            var fname = (url ? (url.replace(/[^a-z0-9]+/gi,'_').replace(/^_+|_+$/g,'')||'page') : 'page') + '.md';
            var blob=new Blob([md],{type:'text/markdown;charset=utf-8'}); var u=URL.createObjectURL(blob); var a=document.createElement('a'); a.href=u; a.download=fname; document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(u);
        } catch(_) {}
    }

    /* Social grouping (client-side over external links) */
    function renderSocial(){
        try{
            var cont=document.getElementById('social-groups'); if(!cont) return;

            // Strip tracking params and resolve known redirectors (best-effort, no network)
            function stripTracking(u){
                try{
                    var url = new URL(u);
                    var host = (url.hostname||'').toLowerCase();
                    // Known redirectors that embed target in query
                    if (host === 'l.facebook.com' || host === 'l.instagram.com') {
                        var real = url.searchParams.get('u') || url.searchParams.get('url');
                        if (real) { return new URL(real); }
                        return null; // drop opaque redirectors
                    }
                    // Common trackers
                    var del = [];
                    url.searchParams.forEach(function(_v,k){
                        var lk = String(k||'').toLowerCase();
                        if (lk.startsWith('utm_') || lk === 'fbclid' || lk === 'gclid' || lk === 'mc_cid' || lk === 'mc_eid' || lk === 'igshid') del.push(k);
                    });
                    del.forEach(function(k){ url.searchParams.delete(k); });
                    url.hash = '';
                    return url;
                }catch(_){ return null; }
            }

            // Canonicalize host/scheme and unify aliases (e.g., twitter -> x.com, youtu.be -> youtube.com)
            function canonicalize(url){
                try{
                    url.protocol = 'https:';
                    var host = (url.hostname||'').toLowerCase();
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
                    if ((url.pathname||'').length > 1 && url.pathname.endsWith('/')) {
                        url.pathname = url.pathname.replace(/\/+$/,'');
                    }
                    return url;
                }catch(_){ return null; }
            }

            function platformForHost(host){
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
            function classifyKind(url, platform){
                var p = String(url.pathname||'/');
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

            function classifySocial(u){
                var url = stripTracking(u);
                if (!url) return null;
                url = canonicalize(url);
                if (!url) return null;
                var host = (url.hostname||'').toLowerCase();
                var platform = platformForHost(host);
                if (!platform) return null;
                var kind = classifyKind(url, platform);
                if (kind === 'discard') return null;
                return { platform: platform, url: url.toString(), kind: (kind || 'other') };
            }

            // Aggregate external links across all pages + top-level
            var all = [];
            try{
                var uniq = new Set();
                (LINKS_EXTERNAL||[]).forEach(function(u){ if(u) uniq.add(u); });
                (PAGES||[]).forEach(function(p){
                    try{
                        var arr = (p && p.links && Array.isArray(p.links.external)) ? p.links.external : [];
                        arr.forEach(function(u){ if(u) uniq.add(u); });
                    }catch(_){}
                });
                all = Array.from(uniq.values());
            }catch(_){
                all = LINKS_EXTERNAL || [];
            }

            var groups = {}; // platform -> {profiles:Set, other:Set}
            all.forEach(function(u){
                try{
                    var c = classifySocial(u);
                    if (!c) return;
                    if (!groups[c.platform]) groups[c.platform] = {profiles:new Set(), other:new Set()};
                    groups[c.platform][c.kind === 'profile' ? 'profiles' : 'other'].add(c.url);
                }catch(_){}
            });

            var html='';
            Object.keys(groups).sort().forEach(function(plat){
                var profiles = Array.from(groups[plat].profiles.values());
                var others   = Array.from(groups[plat].other.values());
                var total = profiles.length + others.length;
                html += '<div class="mb-2"><div class="small"><strong>'+plat+'</strong> <span class="small">('+(total)+')</span></div><ul class="domain-list">';
                // Profiles first
                profiles.forEach(function(u){
                    var acts = '<a href="'+encodeURI(u)+'" target="_blank" rel="noopener">↗</a> <button class="btn btn-sm" onclick="copyLink(\''+jsStr(u)+'\')">Copy</button>';
                    if (LOGGED_IN) {
                        acts += ' <button class="btn btn-sm" onclick="attachProspectSocial(\''+jsStr(plat.toLowerCase())+'\', \''+jsStr(u)+'\')">Attach to Prospect</button> <button class="btn btn-sm" onclick="attachContactSocial(\''+jsStr(u)+'\')">Attach to Contact</button>';
                    }
                    html += '<li><span>'+escapeHtml(u)+'</span><span>'+acts+'</span></li>';
                });
                // Other links
                others.forEach(function(u){
                    var acts = '<a href="'+encodeURI(u)+'" target="_blank" rel="noopener">↗</a> <button class="btn btn-sm" onclick="copyLink(\''+jsStr(u)+'\')">Copy</button>';
                    if (LOGGED_IN) {
                        acts += ' <button class="btn btn-sm" onclick="attachProspectSocial(\''+jsStr(plat.toLowerCase())+'\', \''+jsStr(u)+'\')">Attach to Prospect</button> <button class="btn btn-sm" onclick="attachContactSocial(\''+jsStr(u)+'\')">Attach to Contact</button>';
                    }
                    html += '<li><span>'+escapeHtml(u)+'</span><span>'+acts+'</span></li>';
                });
                html += '</ul></div>';
            });
            cont.innerHTML = html || '<div class="small">No social links detected.</div>';
        }catch(_){}
    }

    /* ----- Links panel (external domains) pagination ----- */
    var LINKS_PAGER = null;

    function setupLinksPagination(){
        try{
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
        } catch(_) {}
    }

    function renderLinksListSlice(){
        try{
            var st = LINKS_PAGER; if (!st || !st.ul) return;
            var ul = st.ul;
            // Rebuild list: header row + page slice
            ul.innerHTML = '';
            var head = document.createElement('li');
            head.className = 'small';
            head.style.fontWeight = '600';
            head.innerHTML = '<span class="small">URL</span> <span class="small">Count</span>';
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
                right.className = 'small';
                right.textContent = String(td.count == null ? '' : td.count);

                li.appendChild(left);
                li.appendChild(right);
                ul.appendChild(li);
            }
        } catch(_) {}
    }

    function renderLinksControls(){
        try{
            var st = LINKS_PAGER; if (!st || !st.ul) return;
            if (!st.pagerEl) {
                var nav = document.createElement('div');
                nav.id = 'ext-domains-pager';
                nav.setAttribute('role','navigation');
                nav.setAttribute('aria-label','Links pagination');
                nav.className = 'small mt-1';
                // Center the controls
                try {
                    nav.style.display = 'flex';
                    nav.style.justifyContent = 'center';
                    nav.style.alignItems = 'center';
                    nav.style.gap = '8px';
                } catch(_){}

                var prev = document.createElement('button');
                prev.type = 'button'; prev.className = 'btn btn-sm'; prev.textContent = 'Prev';
                prev.setAttribute('aria-controls', st.ul.id);

                var label = document.createElement('span');
                label.className = 'ml-1 mr-1';
                label.setAttribute('aria-live','polite');
                label.textContent = 'Page ' + st.page + ' of ' + st.totalPages;

                var next = document.createElement('button');
                next.type = 'button'; next.className = 'btn btn-sm'; next.textContent = 'Next';
                next.setAttribute('aria-controls', st.ul.id);

                prev.addEventListener('click', function(){
                    if (LINKS_PAGER.page > 1) {
                        LINKS_PAGER.page -= 1;
                        renderLinksListSlice();
                        updateLinksControls();
                    }
                });
                next.addEventListener('click', function(){
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
        } catch(_) {}
    }

    function updateLinksControls(){
        try{
            var st = LINKS_PAGER; if (!st || !st.pagerEl) return;
            // Always show controls; disable unavailable actions
            if (st.prevBtn) st.prevBtn.disabled = (st.page <= 1);
            if (st.nextBtn) st.nextBtn.disabled = (st.page >= st.totalPages);
            if (st.pageLabel) st.pageLabel.textContent = 'Page ' + st.page + ' of ' + st.totalPages;
        } catch(_) {}
    }

    /* Pages outline & prompt selection helpers */
    function currentSelectedPageUrl(){
        try{
            var li=document.querySelector('#pages-list li.active') || document.querySelector('#pages-list li input[type=checkbox]:checked')?.closest('li');
            return li ? (li.getAttribute('data-url')||'') : '';
        }catch(_){ return ''; }
    }

    /* Prompt Builder */
    var PB_PRODUCTS = [];
    var PB_OPEN = false;
    var PB_LARGE = false;
    var BUILTIN_TEMPLATES = {
        sales_pitch: [
            'You are writing a concise sales pitch for {product_name} to a potential customer visiting {product_website}.',
            'Product Summary:',
            '{product_description}',
            '',
            'ICP: {icp}',
            'Tone: {tone}',
            'CTA: {cta}',
            'Length: {length}',
            '',
            'Consider the following site context (summaries may be brief):',
            'Sources:',
            '{sources}',
            '',
            'Content snippets:',
            '{content}',
            '',
            'Write a persuasive, tailored pitch.'
        ].join('\n'),
        outreach_email: [
            'Draft a first-touch outreach email introducing {product_name}.',
            'Tone: {tone}',
            'CTA: {cta}',
            'Length: {length}',
            '',
            'Context (site pages):',
            'Sources:',
            '{sources}',
            '',
            'Content snippets:',
            '{content}',
            '',
            'Use {contact_info} for the signature/contact details.'
        ].join('\n'),
        weaknesses: [
            'Analyze weaknesses and opportunities for the target site. Use the following context:',
            'Sources:',
            '{sources}',
            '',
            'Content snippets:',
            '{content}',
            '',
            'Summarize clearly and concisely for executive review.'
        ].join('\n'),
        clarity_check: [
            'Assess clarity for the selected page(s):',
            'Sources:',
            '{sources}',
            '',
            'Content:',
            '{content}',
            '',
            'Identify confusing parts and propose concise improvements. Keep in bullet points.'
        ].join('\n')
    };
    /* pbLoadProducts removed: products are now provided server-side via __ctx.user_products */

    /* Mini compose helpers and shortcuts */
    function getQueryParam(name){
        try {
            var url = new URL(window.location.href);
            return url.searchParams.get(name);
        } catch(_) { return null; }
    }
    function miniApplyProductsVisibility(){
        var sel = document.getElementById('mini-product');
        if (sel) { sel.innerHTML = ''; }

        // small inline helper to show a hint under the Product select (or notices fallback)
        function setHintHtml(html){
            try{
                var hint = document.getElementById('mini-product-hint');
                var parent = (sel && sel.parentNode) || document.getElementById('compose-notices');
                if (!hint) {
                    hint = document.createElement('div');
                    hint.id = 'mini-product-hint';
                    hint.className = 'small';
                    if (parent) parent.appendChild(hint);
                }
                hint.innerHTML = html || '';
                hint.style.display = html ? '' : 'none';
                // Ensure the parent notices container is visible when we have content
                if (parent && parent.id === 'compose-notices') {
                    parent.style.display = html ? '' : 'none';
                }
            }catch(_){}
        }
        // Toggle Product-dependent compose surfaces (chat stays visible); owner-only Shortcuts/Product
        function setComposeVisibility(hasProducts){
            try{
                var cfg = document.querySelector('#compose-chat .config-grid');
                var sa  = document.querySelector('#compose-chat .structured-actions');
                var notices = document.getElementById('compose-notices');
                // Owner-only AND must have products to show compose controls
                var showCompose = (!!CAN_SELECT_PAGES) && !!hasProducts;
                [cfg, sa].forEach(function(el){ if (el) el.style.display = showCompose ? '' : 'none'; });
                // CTA when logged-in and no products (invite to add), regardless of ownership
                if (notices) {
                    if (LOGGED_IN && !hasProducts) {
                        notices.innerHTML =
                            '<div class="mt-1">' +
                            'No products yet. ' +
                            '<a class="btn btn-primary" href="/products" aria-label="Add a new product">Add Product</a>' +
                            '</div>';
                        notices.style.display = '';
                    } else {
                        notices.innerHTML = '';
                        notices.style.display = 'none';
                    }
                }
            }catch(_){}
        }

        if (!PB_PRODUCTS.length) {
            // Show CTA to add a Product.
            setHintHtml(
                '<div class="mt-1">' +
                'No products yet. ' +
                '<a class="btn btn-primary" href="/products" aria-label="Add a new product">Add Product</a>' +
                '</div>'
            );
            // Explicitly unhide notices container even if template hid config grid
            try{ var n = document.getElementById('compose-notices'); if (n) n.style.display = ''; }catch(_){}
            setComposeVisibility(false);
        } else {
            setHintHtml('');
            setComposeVisibility(true);
        }

        if (sel) {
            PB_PRODUCTS.forEach(function(p){
                var opt = document.createElement('option'); opt.value = p.id; opt.textContent = p.name; sel.appendChild(opt);
            });
            var chosen = LAST_PRODUCT_ID && PB_PRODUCTS.find(function(p){return p.id===LAST_PRODUCT_ID;}) ? LAST_PRODUCT_ID : (PB_PRODUCTS[0] && PB_PRODUCTS[0].id) || '';
            if (chosen) sel.value = chosen;
            miniApplyDefaultsForSelected();
            sel.onchange = function(){
                try{ localStorage.setItem('pb:last_product_id', sel.value || ''); }catch(_){}
                miniApplyDefaultsForSelected();
            };
        }
    }
    function miniApplyDefaultsForSelected(){
        try{
            var sel = document.getElementById('mini-product'); var pid = sel ? sel.value : '';
            var p = PB_PRODUCTS.find(function(x){return x.id===pid;}) || {};
            var d = (p.defaults || {});
            var toneSel = document.getElementById('mini-tone'); if (toneSel && (d.tone || p.tone)) toneSel.value = d.tone || p.tone;
            var cta = document.getElementById('mini-cta'); if (cta && d.cta) cta.value = d.cta;
            var len = document.getElementById('mini-length'); if (len && d.length) len.value = d.length;
        }catch(_){}
    }
    function buildPromptFromMini(templateId){
        var sel = document.getElementById('mini-product'); var pid = sel ? (sel.value || '') : '';
        var p = PB_PRODUCTS.find(function(x){ return x.id === pid; }) || {};
        var tone = (document.getElementById('mini-tone') && document.getElementById('mini-tone').value) || '';
        var cta = (document.getElementById('mini-cta') && document.getElementById('mini-cta').value) || '';
        var length = (document.getElementById('mini-length') && document.getElementById('mini-length').value) || '';
        var tpl = BUILTIN_TEMPLATES[templateId] || '';
        var urls = Array.from(SELECTED_PAGE_CONTENT.keys());
        var sources_md = urls.map(function(u){ return '- ' + u; }).join('\n');
        var shorten = false; try { shorten = !!(document.getElementById('mini-shorten') && document.getElementById('mini-shorten').checked); } catch(_){}
        var maxLen = shorten ? 220 : 600;
        var content_md = urls.map(function(u){
            var md = SELECTED_PAGE_CONTENT.get(u) || '';
            var sn = md.replace(/\s+/g,' ').trim();
            if (sn.length > maxLen) sn = sn.slice(0, maxLen) + ' [...]';
            return '### ' + u + '\n\n' + sn;
        }).join('\n\n');
        var map = {
            product_name: p.name || '',
            product_website: p.website || '',
            product_description: p.description || '',
            icp: p.icp || '',
            pricing: p.pricing || '',
            contact_info: p.contact_info || '',
            tone: tone, cta: cta, length: length,
            sources: sources_md, content: content_md
        };
        return tpl.replace(/\{([a-zA-Z0-9_]+)\}/g, function(_m, k){ return (map[k] == null ? '' : String(map[k])); });
    }
    // Deep link: preset auto-generate
    function tryPresetAutoGenerate(){
        try{
            var preset = getQueryParam('preset');
            if (preset && BUILTIN_TEMPLATES[preset]) {
                runStructured(preset);
            }
        }catch(_){}
    }

    /* ----- Emails preview helpers ----- */
    function copyEmail(email) {
        try { navigator.clipboard.writeText(email); showToast('Email copied'); } catch(_) {}
    }
    function copyAllEmails() {
        try {
            var all = (EMAILS_UNIQUE || []).join('\n');
            navigator.clipboard.writeText(all).then(function(){ showToast('All emails copied'); });
        } catch(_) {}
    }
    function exportEmailsCsv() {
        try {
            var rows = [['email','first_url','found_as','domain']];
            var srcMap = buildEmailSourceMap();
            (EMAILS_UNIQUE || []).forEach(function(e){
                var dom = (e.split('@')[1] || '').toLowerCase();
                var src = srcMap[e] || {foundAs:[], firstUrl:''};
                rows.push([e, src.firstUrl || '', (src.foundAs||[]).join(','), dom]);
            });
            var csv = rows.map(function(r){ return r.map(csvCell).join(','); }).join('\n');
            downloadBlob(csv, 'emails.csv', 'text/csv;charset=utf-8');
        } catch(_) { alert('Unable to export'); }
    }
    function csvCell(s){ var t=String(s==null?'':s); return /[",\n]/.test(t) ? '"' + t.replace(/"/g,'""') + '"' : t; }
    function downloadBlob(text, filename, mime){
        var blob=new Blob([text], {type: mime||'text/plain;charset=utf-8'});
        var u=URL.createObjectURL(blob); var a=document.createElement('a'); a.href=u; a.download=filename||'download.txt';
        document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(u);
    }

    /* ----- Pages enhancements ----- */
    function filterPages(term){
        try{
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
        } catch(_){}
    }
    function selectAllPages(){
        try{
            if (!CAN_SELECT_PAGES) return;
            var st = PAGES_PAGER; if (!st || !Array.isArray(st.sliceUrls)) return;
            var ul = st.ul || document.getElementById('pages-list'); if (!ul) return;
 
            // Add only current slice URLs, enforce max pages
            var limited = false;
            (st.sliceUrls || []).forEach(function(url){
                if (!url) return;
                if (SELECTED_PAGE_CONTENT.size >= MAX_PAGES) { limited = true; return; }
                var page = (PAGES||[]).find(function(x){ return x && (x.url||'')===url; });
                var md = (page && (page.markdown||'').trim()) || '';
                SELECTED_PAGE_CONTENT.set(url, md);
            });
            if (limited) {
                try { showToast('Only ' + MAX_PAGES + ' pages can be selected'); } catch(_){}
            }
 
            // Update visible checkboxes for current slice
            var current = new Set(st.sliceUrls || []);
            ul.querySelectorAll('li').forEach(function(li){
                var url = li.getAttribute('data-url') || '';
                if (!url || !current.has(url)) return;
                var cb = li.querySelector('input[type=checkbox]'); if (cb) cb.checked = true;
                li.classList.add('selected');
            });
 
            updateSelectionCount();
        } catch(_){}
    }
    function clearAllPages(){
        try{
            if (!CAN_SELECT_PAGES) return;
            var st = PAGES_PAGER; if (!st || !Array.isArray(st.sliceUrls)) return;
            var ul = st.ul || document.getElementById('pages-list'); if (!ul) return;
 
            // Remove only current slice URLs
            (st.sliceUrls || []).forEach(function(url){
                if (url) SELECTED_PAGE_CONTENT.delete(url);
            });
 
            // Update visible checkboxes for current slice
            var current = new Set(st.sliceUrls || []);
            ul.querySelectorAll('li').forEach(function(li){
                var url = li.getAttribute('data-url') || '';
                if (!url || !current.has(url)) return;
                var cb = li.querySelector('input[type=checkbox]'); if (cb) cb.checked = false;
                li.classList.remove('selected');
            });
 
            updateSelectionCount();
        } catch(_){}
    }
    function updateChatSendState(){
        try {
            var sendBtn = document.getElementById('chat-send');
            if (!sendBtn) return;
            var hasPages = (SELECTED_PAGE_CONTENT.size > 0);
            var qEl = document.getElementById('chat-question');
            var hasText = !!(qEl && qEl.value && qEl.value.trim().length > 0);
            var disabled = IS_STREAMING || !(hasPages && hasText);
            sendBtn.disabled = disabled;
            sendBtn.setAttribute('aria-disabled', disabled ? 'true' : 'false');
            if (IS_STREAMING) {
                sendBtn.title = 'Receiving answer...';
            } else {
                sendBtn.title = disabled ? (hasPages ? 'Type a question' : 'Select at least one page') : 'Ask';
            }
        } catch(_){}
    }

    function updateSelectionCount(){
        var count = SELECTED_PAGE_CONTENT.size;
        try { var el=document.getElementById('selected-count'); if (el) el.textContent = String(count); } catch(_){}
        try { var ce=document.getElementById('chat-page-count'); if (ce) ce.textContent = String(count); } catch(_){}
        // Update approximate selected chars vs budget
        try {
            var maxEl = document.getElementById('selected-chars-max');
            if (maxEl) maxEl.textContent = String(MAX_TOTAL_CHARS);
            var total = 0;
            SELECTED_PAGE_CONTENT.forEach(function(md){
                var t = String(md==null?'':md);
                var add = Math.min(MAX_CHARS_PER_PAGE, t.length);
                var rem = Math.max(0, MAX_TOTAL_CHARS - total);
                add = Math.min(add, rem);
                total += add;
            });
            var sc = document.getElementById('selected-chars'); if (sc) sc.textContent = String(total);
        } catch(_){}
        // Row selected styles based on checkbox state
        document.querySelectorAll('#pages-list li').forEach(function(li){
            var cb = li.querySelector('input[type=checkbox]');
            li.classList.toggle('selected', !!(cb && cb.checked));
        });
        // Sync Ask button state after any selection change
        try { updateChatSendState(); } catch(_){}
    }

    // When page selection is not allowed: hide controls and prevent any selection state
    function enforceSelectionRestrictions(){
        if (CAN_SELECT_PAGES) return;
        try {
            // Hide Select All / Clear and selection summary in the Pages toolbar
            var toolbar = document.querySelector('[aria-label="Pages Controls"]');
            if (toolbar) {
                toolbar.querySelectorAll('button').forEach(function(btn){ btn.style.display = 'none'; });
                var ss = toolbar.querySelector('.selection-summary');
                if (ss) ss.style.display = 'none';
            }
            // Clear selected set and enforce disabled, unchecked checkboxes
            SELECTED_PAGE_CONTENT.clear();
            document.querySelectorAll('#pages-list input[type=checkbox]').forEach(function(cb){
                cb.checked = false;
                cb.disabled = true;
            });
            updateSelectionCount();
        } catch(_){}
    }

    /* ----- Structured results & actions ----- */
    function runStructured(templateId) {
        try {
            var txt = buildPromptFromMini(templateId);
            addChatMessage('user', txt);
            var pages = Array.from(SELECTED_PAGE_CONTENT.values());
            startChatStream(txt, pages);
        } catch(e) { alert('Unable to generate content'); }
    }

    /* Chat helpers */
    function addChatMessage(sender, text){
        var messages = document.getElementById('chat-messages'); if (!messages) return;
        var d = document.createElement('div'); d.className = 'chat-message '+sender;
        d.innerHTML = '<div class="message-bubble">'+escapeHtml(text||'')+'</div>';
        messages.appendChild(d);
        messages.scrollTop = messages.scrollHeight;
    }
    function showTypingIndicator(){
        var messages = document.getElementById('chat-messages'); if (!messages) return;
        var d = document.createElement('div'); d.id='typing-indicator'; d.className='chat-message ai';
        d.innerHTML = '<div class="message-bubble"><em>Thinking...</em></div>';
        messages.appendChild(d);
        messages.scrollTop = messages.scrollHeight;
    }
    function hideTypingIndicator(){
        var t = document.getElementById('typing-indicator'); if (t) t.remove();
    }

    // Append a chat message rendering Markdown safely when libs are available
    function appendChatMessageMarkdown(sender, text){
        var messages = document.getElementById('chat-messages'); if (!messages) return;
        var d = document.createElement('div'); d.className = 'chat-message ' + (sender || 'ai');
        var bubble = document.createElement('div'); bubble.className = 'message-bubble';
        try {
            if (window.marked && window.DOMPurify) {
                try { if (window.marked.setOptions) window.marked.setOptions({breaks:true}); } catch(_){}
                var html = window.marked.parse(String(text == null ? '' : text));
                bubble.innerHTML = window.DOMPurify.sanitize(html);
            } else {
                bubble.textContent = String(text == null ? '' : text);
            }
        } catch(_){
            bubble.textContent = String(text == null ? '' : text);
        }
        d.appendChild(bubble);
        messages.appendChild(d);
        messages.scrollTop = messages.scrollHeight;
    }

    // Load persisted history and render into chat UI
    async function loadChatHistory(){
        try {
            if (!USER_ID || !CRAWL_ID) return;
            var url = '/api/ai/chat/' + encodeURIComponent(USER_ID) + '/' + encodeURIComponent(CRAWL_ID) + '/history';
            var resp = await fetch(url, {credentials:'same-origin'});
            if (!resp.ok) return;
            var j = await resp.json();
            var arr = (j && Array.isArray(j.messages)) ? j.messages : [];
            arr.forEach(function(m){
                var role = (m && m.role) === 'user' ? 'user' : 'ai';
                var content = (m && m.content) || '';
                appendChatMessageMarkdown(role, content);
            });
        } catch(_){}
    }

    // Clear conversation history (owner-only)
    function clearChatHistory(){
        try {
            if (!USER_ID || !CRAWL_ID) return;
            if (!confirm('Clear conversation?')) return;
            var url = '/api/ai/chat/' + encodeURIComponent(USER_ID) + '/' + encodeURIComponent(CRAWL_ID);
            apiJson(url, 'DELETE', {}).then(function(){
                try { var messages = document.getElementById('chat-messages'); if (messages) messages.innerHTML = ''; } catch(_){}
                try { showToast('Conversation cleared'); } catch(_){}
            }).catch(function(){
                alert('Unable to clear conversation');
            });
        } catch(_){}
    }

    // Shared streaming helper used by chat and clarity assessment
    async function startChatStream(message, pages){
        try {
            if (!message || !Array.isArray(pages)) { addChatMessage('ai','Invalid request.'); return; }
            if (IS_STREAMING) return;
            if (!USER_ID || !CRAWL_ID) { addChatMessage('ai','Chat unavailable: missing identifiers.'); return; }

            IS_STREAMING = true;
            try { updateChatSendState(); } catch(_){}
            showTypingIndicator();
            try { trackEvent('chat_stream_start', {kind:'start', pages_count: (Array.isArray(pages) ? pages.length : 0)}); } catch(_){}

            var url = '/api/ai/chat/' + encodeURIComponent(USER_ID) + '/' + encodeURIComponent(CRAWL_ID);
            var ctrl = new AbortController(); CHAT_ABORT_CTRL = ctrl;
            let resp;
            try {
                resp = await fetch(url, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    credentials: 'same-origin',
                    body: JSON.stringify({ message: message, pages: pages }),
                    signal: ctrl.signal
                });
            } catch (e) {
                hideTypingIndicator();
                addChatMessage('ai', 'Network error.');
                try { trackEvent('chat_stream_end', {status:'network_error'}); } catch(_){}
                IS_STREAMING = false;
                try { updateChatSendState(); } catch(_){}
                return;
            }

            if (!resp.ok) {
                hideTypingIndicator();
                var friendly = await parseFriendlyError(resp);
                addChatMessage('ai', friendly);
                try { trackEvent('chat_stream_end', {status:'http_error', http_status: (resp && resp.status) || 0}); } catch(_){}
                IS_STREAMING = false;
                try { updateChatSendState(); } catch(_){}
                return;
            }

            hideTypingIndicator();
            var messages = document.getElementById('chat-messages');
            var container = document.createElement('div'); container.className = 'chat-message ai';
            var bubble = document.createElement('div'); bubble.className = 'message-bubble'; bubble.textContent = '';
            container.appendChild(bubble);
            if (messages) { messages.appendChild(container); messages.scrollTop = messages.scrollHeight; }

            try {
                var reader = resp.body && resp.body.getReader ? resp.body.getReader() : null;
                var decoder = new TextDecoder();
                var __streamBuf = '';
                if (reader) {
                    while (true) {
                        const {done, value} = await reader.read();
                        if (done) break;
                        var chunk = decoder.decode(value || new Uint8Array(), {stream: true});
                        if (chunk && bubble) {
                            __streamBuf += chunk;
                            bubble.textContent += chunk;
                            if (messages) messages.scrollTop = messages.scrollHeight;
                        }
                    }
                } else {
                    var all = await resp.text();
                    __streamBuf = all || '';
                    bubble.textContent += (all || '');
                    if (messages) messages.scrollTop = messages.scrollHeight;
                }
                // After streaming completes, render Markdown safely if libraries are available
                try {
                    if (window.marked && window.DOMPurify && bubble) {
                        if (window.marked.setOptions) { window.marked.setOptions({ breaks: true }); }
                        var __html = window.marked.parse(__streamBuf || '');
                        var __safe = window.DOMPurify.sanitize(__html);
                        bubble.innerHTML = __safe;
                    }
                } catch(__mdErr) {}
            } catch (_e) {
                // ignore stream errors; partial content is already displayed
            } finally {
                try { trackEvent('chat_stream_end', {status:'ok'}); } catch(_){}
                IS_STREAMING = false;
                CHAT_ABORT_CTRL = null;
                try { updateChatSendState(); } catch(_){}
            }
        } finally {
            if (IS_STREAMING) {
                IS_STREAMING = false;
                try { updateChatSendState(); } catch(_){}
            }
        }
    }
    async function sendChatMessage(){
        try {
            var qEl = document.getElementById('chat-question');
            var q = (qEl && (qEl.value || '').trim()) || '';
            if (!q) return;
            if (IS_STREAMING) return; // Prevent concurrent sends

            var n = SELECTED_PAGE_CONTENT.size;
            addChatMessage('user', q);
            if (qEl) qEl.value = '';
            try { updateChatSendState(); } catch(_) {}

            if (n === 0) {
                addChatMessage('ai', 'Please select some pages first.');
                return;
            }
            if (!USER_ID || !CRAWL_ID) {
                addChatMessage('ai', 'Chat unavailable: missing identifiers.');
                return;
            }

            // Disable Ask while streaming
            IS_STREAMING = true;
            try { updateChatSendState(); } catch(_) {}
            showTypingIndicator();
            try { trackEvent('chat_stream_start', {kind:'send', pages_count: SELECTED_PAGE_CONTENT.size || 0}); } catch(_){}

            var pages = Array.from(SELECTED_PAGE_CONTENT.values());
            var url = '/api/ai/chat/' + encodeURIComponent(USER_ID) + '/' + encodeURIComponent(CRAWL_ID);
            var ctrl = new AbortController(); CHAT_ABORT_CTRL = ctrl;

            let resp;
            try {
                resp = await fetch(url, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    credentials: 'same-origin',
                    body: JSON.stringify({ message: q, pages: pages }),
                    signal: ctrl.signal
                });
            } catch (e) {
                hideTypingIndicator();
                addChatMessage('ai', 'Network error.');
                try { trackEvent('chat_stream_end', {status:'network_error'}); } catch(_){}
                return;
            }

            if (!resp.ok) {
                hideTypingIndicator();
                var friendly = await parseFriendlyError(resp);
                addChatMessage('ai', friendly);
                try { trackEvent('chat_stream_end', {status:'http_error', http_status: (resp && resp.status) || 0}); } catch(_){}
                return;
            }

            // Prepare AI message bubble to append streamed text into
            hideTypingIndicator();
            var messages = document.getElementById('chat-messages');
            var container = document.createElement('div'); container.className = 'chat-message ai';
            var bubble = document.createElement('div'); bubble.className = 'message-bubble'; bubble.textContent = '';
            container.appendChild(bubble);
            if (messages) { messages.appendChild(container); messages.scrollTop = messages.scrollHeight; }

            // Stream plaintext chunks
            try {
                var reader = resp.body && resp.body.getReader ? resp.body.getReader() : null;
                var decoder = new TextDecoder();
                var __streamBuf = '';
                if (reader) {
                    while (true) {
                        const {done, value} = await reader.read();
                        if (done) break;
                        var chunk = decoder.decode(value || new Uint8Array(), {stream: true});
                        if (chunk && bubble) {
                            __streamBuf += chunk;
                            bubble.textContent += chunk;
                            if (messages) messages.scrollTop = messages.scrollHeight;
                        }
                    }
                } else {
                    // Fallback: no reader (older browsers) - read as text
                    var all = await resp.text();
                    __streamBuf = all || '';
                    bubble.textContent += (all || '');
                    if (messages) messages.scrollTop = messages.scrollHeight;
                }
                // After streaming completes, render Markdown safely if libraries are available
                try {
                    if (window.marked && window.DOMPurify && bubble) {
                        if (window.marked.setOptions) { window.marked.setOptions({ breaks: true }); }
                        var __html = window.marked.parse(__streamBuf || '');
                        var __safe = window.DOMPurify.sanitize(__html);
                        bubble.innerHTML = __safe;
                    }
                } catch(__mdErr) {}
            } catch (streamErr) {
                // On streaming error, at least leave what we got
            } finally {
                try { trackEvent('chat_stream_end', {status:'ok'}); } catch(_){}
                IS_STREAMING = false;
                CHAT_ABORT_CTRL = null;
                try { updateChatSendState(); } catch(_) {}
            }
        } finally {
            // Ensure state flips back even if unexpected throw earlier
            if (IS_STREAMING) {
                IS_STREAMING = false;
                try { updateChatSendState(); } catch(_) {}
            }
        }
    }

    async function runClarityAssessmentForCurrentPage(){
        try{
            var pre = document.getElementById('page-markdown');
            var md = (pre && (pre.textContent || pre.innerText) || '').toString().trim();
            if (!md) { alert('No markdown to assess'); return; }

            var msg = 'Assess the clarity of the following page for a typical visitor. Is the text clear enough? Identify confusing parts and propose concise improvements.';
            addChatMessage('user', 'Clarity assessment for current page.');
            await startChatStream(msg, [md]);
        } catch(_) {}
    }

    /* Small helpers */
    function showToast(message) {
        try {
            var toast = document.createElement('div');
            toast.textContent = message;
            toast.style.cssText = 'position:fixed;top:20px;right:20px;background:var(--brand);color:white;padding:8px 16px;border-radius:4px;z-index:1000';
            document.body.appendChild(toast);
            setTimeout(function(){ toast.remove(); }, 1600);
        } catch(_) {}
    }
    function escapeHtml(s){
        if (typeof window !== 'undefined' && typeof window.escapeHtml === 'function') {
            return window.escapeHtml(s);
        }
        try {
            var div = document.createElement('div');
            div.textContent = String(s == null ? '' : s);
            return div.innerHTML;
        } catch (_){
            return String(s == null ? '' : s);
        }
    }

    // Map HTTP errors to friendly UI messages
    async function parseFriendlyError(resp) {
        try {
            var status = (resp && resp.status) || 0;
            var text = '';
            var detail = '';
            try {
                // Attempt JSON first
                var j = await resp.clone().json();
                detail = (j && j.detail) || '';
            } catch(_) {
                try { text = await resp.clone().text(); } catch(__) {}
            }
            var d = String(detail || text || '').toLowerCase();

            if (status === 401) return 'Unauthorized';
            if (status === 403) return 'Forbidden';

            if (status === 400) {
                if (d.indexOf('message is required') !== -1) {
                    return 'Please type a question.';
                }
                if (d.indexOf('pages must be a list') !== -1) {
                    return 'Invalid selection. Please reselect pages.';
                }
                if (d.indexOf('exceeds limit') !== -1 || d.indexOf('limit') !== -1) {
                    // Special-case pages limit
                    if (d.indexOf('pages') !== -1) {
                        return 'Limit exceeded: select up to ' + MAX_PAGES + ' pages.';
                    }
                    return 'Request exceeds allowed limits.';
                }
                return 'Invalid request.';
            }

            return 'Error ' + status;
        } catch(_) {
            var s = (resp && resp.status) || 0;
            return s ? ('Error ' + s) : 'Request failed';
        }
    }

    // Mobile tabs (activate only on small screens)
    (function() {
        var isMobile = false;
        try { isMobile = !!(window.matchMedia && window.matchMedia('(max-width: 768px)').matches); } catch(_){}
        var mtabs = document.querySelectorAll('.mobile-tab');

        function hideAllMobileSections() {
            try {
                document.querySelectorAll('.mobile-section').forEach(function(sec){
                    sec.classList.remove('active');
                    sec.style.display = 'none';
                });
            } catch(_){}
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
                try { t.classList.remove('hidden'); } catch(_){}
                t.classList.add('active');
                t.style.display = 'block';
            }
            // Update tab aria state
            mtabs.forEach(function(b){ b.setAttribute('aria-selected','false'); });
            if (btn) btn.setAttribute('aria-selected','true');
        }
        // Click handlers
        mtabs.forEach(function(btn){
            btn.addEventListener('click', function(){
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
        } catch(_){}
    })();

    // Simple email validation heuristics
    var DISPOSABLE_DOMAINS = new Set(['mailinator.com','10minutemail.com','tempmail.email','yopmail.com','guerrillamail.com']);
    function emailStatus(email, baseDomain) {
        var re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!re.test(email)) return {label:'Invalid', cls:'status-invalid'};
        var dom = (email.split('@')[1] || '').toLowerCase();
        if (DISPOSABLE_DOMAINS.has(dom)) return {label:'Disposable', cls:'status-disposable'};
        baseDomain = (baseDomain || '').toLowerCase();
        if (baseDomain && (dom === baseDomain || dom.endsWith('.' + baseDomain))) return {label:'Valid', cls:'status-valid'};
        return {label:'Unknown', cls:'status-unknown'};
    }

     // Leads helpers
    function buildEmailSourceMap() {
        var map = {};
        EMAILS_SOURCES_RAW.forEach(function(x) {
            var key = x.email;
            if (!map[key]) map[key] = {foundAs: new Set(), firstUrl: x.url || ''};
            (x.found_as || []).forEach(function(f){ map[key].foundAs.add(f); });
            if (!map[key].firstUrl && x.url) map[key].firstUrl = x.url;
        });
        Object.keys(map).forEach(function(k){ map[k].foundAs = Array.from(map[k].foundAs.values()); });
        return map;
    }
    function computeMentions(email) {
        var c = 0;
        Object.keys(EMAILS_BY_URL).forEach(function(u){
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
        var types = Array.from(document.querySelectorAll('.lead-type:checked')).map(function(cb){return cb.value;});
        var rowDom = (row.getAttribute('data-domain') || '').toLowerCase();
        var rowFound = (row.getAttribute('data-foundas') || '').split(',').filter(Boolean);
        if (domainFilter && rowDom.indexOf(domainFilter) === -1) return false;
        if (types.length > 0) {
            var any = rowFound.some(function(t){ return types.indexOf(t) !== -1; });
            if (!any) return false;
        }
        return true;
    }
    function renderLeads() {
        var tbody = document.getElementById('leads-tbody');
        if (!tbody) return;
        tbody.innerHTML = '';
        var srcMap = buildEmailSourceMap();
        EMAILS_UNIQUE.forEach(function(email) {
            var dom = (email.split('@')[1] || '').toLowerCase();
            var mentions = computeMentions(email);
            var foundAs = (srcMap[email] && srcMap[email].foundAs) ? srcMap[email].foundAs : [];
            var fUrl = (srcMap[email] && srcMap[email].firstUrl) ? srcMap[email].firstUrl : firstSourceUrlFallback(email);
            var st = emailStatus(email, BASE_DOMAIN);

            var tr = document.createElement('tr');
            tr.setAttribute('data-email', email);
            tr.setAttribute('data-domain', dom);
            tr.setAttribute('data-foundas', foundAs.join(','));
            var actionsHtml = '<button class="btn btn-sm" onclick="copyLink(\''+jsStr(email)+'\')">Copy</button>';
            if (LOGGED_IN) {
                actionsHtml += ' <button class="btn btn-sm" onclick="addEmailToProspect(\''+jsStr(email)+'\', \''+jsStr(fUrl)+'\')">Add to Prospect</button>';
            }
            tr.innerHTML =
                '<td><input type="checkbox" class="lead-select"></td>' +
                '<td><code>'+escapeHtml(email)+'</code></td>' +
                '<td>'+mentions+'</td>' +
                '<td title="'+escapeHtml(foundAs.length ? ('Found on: '+fUrl+'; as: '+foundAs.join(',')) : 'N/A')+'">'+(foundAs.join(',')||'-')+'</td>' +
                '<td><span class="status-chip '+st.cls+'">'+st.label+'</span></td>' +
                '<td>'+escapeHtml(dom || '-')+'</td>' +
                '<td>'+actionsHtml+'</td>';
            if (applyLeadFilters(tr)) tbody.appendChild(tr);
        });
        // Hook select all
        var sa = document.getElementById('lead-select-all');
        if (sa) {
            sa.checked = false;
            sa.onchange = function() {
                tbody.querySelectorAll('.lead-select').forEach(function(cb){ cb.checked = sa.checked; });
            };
        }
        // Toggle empty state message
        try {
            var empty = document.getElementById('leads-empty');
            if (empty) empty.classList.toggle('hidden', tbody.children.length > 0);
        } catch(_){}
    }
    function clearLeadFilters() {
        var df = document.getElementById('lead-filter-domain'); if (df) df.value = '';
        document.querySelectorAll('.lead-type:checked').forEach(function(cb){ cb.checked = false; });
        renderLeads();
    }
    // Add Lead (client-only)
    function openAddLead(){ document.getElementById('add-lead-form').style.display='block'; }
    function closeAddLead(){ document.getElementById('add-lead-form').style.display='none'; }
    function saveAddedLead() {
        var e = (document.getElementById('add-email').value || '').trim().toLowerCase();
        var s = (document.getElementById('add-source').value || '').trim();
        var u = (document.getElementById('add-social').value || '').trim();
        var r = (document.getElementById('add-role').value || '').trim();
        var t = (document.getElementById('add-tags').value || '').trim();
        if (!e) { alert('Email is required'); return; }
        prospectEnsure().then(function(pid){
            return apiJson('/api/prospects/'+encodeURIComponent(pid)+'/contacts','POST',{
                email: e,
                source_url: s || '',
                social_url: u || '',
                role_title: r || '',
                tags: t || ''
            });
        }).then(function(){
            if (EMAILS_UNIQUE.indexOf(e) === -1) EMAILS_UNIQUE.push(e);
            EMAILS_SOURCES_RAW.push({email:e, url:s || window.location.href, found_as: []});
            closeAddLead();
            renderLeads();
            try { trackEvent('add_lead_success'); } catch(_){}
            alert('Contact added');
        }).catch(function(err){
            if (err && err.status === 409) alert('Contact already exists');
            else if (err && err.status === 401) alert('Sign in to add');
            else alert('Unable to add contact');
        });
    }



    // Utilities
    function jsStr(s){ return String(s).replace(/\\/g,'\\\\').replace(/'/g,"\\'"); }
    function csv(s){ var t=String(s==null?'':s); if (/[",\n]/.test(t)) return '"'+t.replace(/"/g,'""')+'"'; return t; }

    function fmtMs(ms){
        if (!ms || ms < 0) return '0s';
        var s = Math.floor(ms/1000);
        if (s < 60) return s+'s';
        var m = Math.floor(s/60), r = s%60;
        return m+'m '+r+'s';
    }
    var __progressTimer = null;
    function startProgressPolling(crawlId){
        if (!crawlId) return;
        function tick(){
            fetch('/api/progress/'+encodeURIComponent(crawlId), {credentials:'same-origin'})
                .then(function(r){
                    if (!r.ok) { throw {status: r.status}; }
                    return r.json();
                })
                .then(function(j){
                    try {
                        var v = Number(j.visited_pages||0), lim = (j.limits&&Number(j.limits.max_pages))||null;
                        var st = (String(j.status||'').toLowerCase());
                        // Status + counters
                        var ps = document.getElementById('progress-status'); if (ps) ps.textContent = String(j.status||'');
                        var pv = document.getElementById('progress-visited'); if (pv) pv.textContent = String(v);
                        var pt = document.getElementById('progress-total'); if (pt) pt.textContent = lim ? String(lim) : '?';
                        var pel = document.getElementById('progress-elapsed'); if (pel) pel.textContent = fmtMs(Number(j.elapsed_ms||0));
                        // ETA and budget
                        var etaEl = document.getElementById('progress-eta');
                        if (etaEl) {
                            var etaMs = (j.est_remaining_ms==null ? null : Number(j.est_remaining_ms));
                            etaEl.textContent = (etaMs!=null && !Number.isNaN(etaMs)) ? fmtMs(etaMs) : '—';
                        }
                        var budEl = document.getElementById('progress-budget');
                        var rem = (j.time_budget_remaining_ms==null ? null : Number(j.time_budget_remaining_ms));
                        if (budEl) budEl.textContent = (rem!=null && !Number.isNaN(rem)) ? ('Budget left: '+fmtMs(rem)) : '';
                        // Found-so-far counters (best-effort)
                        try {
                            var el;
                            el = document.getElementById('progress-emails'); if (el) el.textContent = String(j.emails_so_far || 0);
                            el = document.getElementById('progress-links-int'); if (el) el.textContent = String(j.links_internal_so_far || 0);
                            el = document.getElementById('progress-domains-ext'); if (el) el.textContent = String(j.external_domains_so_far || 0);
                        } catch(_) {}
                        // Progress bar
                        var pct = 0;
                        if (lim && lim > 0) pct = Math.max(0, Math.min(100, Math.round((v/lim)*100)));
                        var bar = document.getElementById('progress-bar'); if (bar) bar.style.width = pct+'%';
                        // Finalizing condition (site scope): budget exhausted but status still running
                        if (st === 'running' && rem != null && !Number.isNaN(rem) && Number(rem) <= 0) {
                            if (ps) ps.textContent = 'finalizing…';
                            if (__progressTimer) { clearInterval(__progressTimer); __progressTimer = null; }
                            setTimeout(function(){ location.reload(); }, 1000);
                            return;
                        }
                        // Stop on terminal states
                        if (st !== 'running' && st !== 'pending') {
                            if (__progressTimer) { clearInterval(__progressTimer); __progressTimer = null; }
                            setTimeout(function(){ location.reload(); }, 800);
                        }
                    } catch(e){}
                })
                .catch(function(_err){
                    try {
                        if (__progressTimer) { clearInterval(__progressTimer); __progressTimer = null; }
                        var ps = document.getElementById('progress-status'); if (ps) ps.textContent = 'unavailable';
                    } catch(_) {}
                    setTimeout(function(){ location.reload(); }, 1500);
                });
        }
        tick();
        __progressTimer = setInterval(tick, 2000);
    }

    /* Claim eligibility UI */
    function setupClaimEligibility(){
        try {
            var btn = document.getElementById('claim-btn');
            var label = document.getElementById('claim-status');
            var createdAt = __ctx.created_at || '';
            var minHours = Number(__ctx.claim_min_hours == null ? 24 : __ctx.claim_min_hours);
            if (!btn || !label || !__ctx.public_key) return;

            function update(){
                var now = new Date();
                var created = createdAt ? new Date(createdAt) : now;
                var eligibleAt = new Date(created.getTime() + (minHours * 3600000));
                var ms = eligibleAt - now;
                if (ms <= 0) {
                    btn.disabled = false;
                    btn.setAttribute('aria-disabled','false');
                    label.textContent = 'You can claim this analysis.';
                    return true;
                }
                btn.disabled = true;
                btn.setAttribute('aria-disabled','true');
                var s = Math.max(0, Math.floor(ms/1000));
                var m = Math.floor(s/60);
                var r = s % 60;
                label.textContent = 'Eligible in ' + (m > 0 ? (m + 'm ') : '') + (r + 's');
                return false;
            }

            update();
            btn.addEventListener('click', claimAnalysis);
            var timer = setInterval(function(){ if (update()) { try{ clearInterval(timer); }catch(_){ } } }, 1000);
        } catch(_){}
    }

    /* Public progress polling by short key */
    function startPublicProgressPolling(pubKey){
        if (!pubKey) return;
        function tick(){
            fetch('/api/progress/public/'+encodeURIComponent(pubKey))
                .then(function(r){
                    if (!r.ok) { throw {status: r.status}; }
                    return r.json();
                })
                .then(function(j){
                    try {
                        var v = Number(j.visited_pages||0), lim = (j.limits&&Number(j.limits.max_pages))||null;
                        var st = (String(j.status||'').toLowerCase());
                        // Status + counters
                        var ps = document.getElementById('progress-status'); if (ps) ps.textContent = String(j.status||'');
                        var pv = document.getElementById('progress-visited'); if (pv) pv.textContent = String(v);
                        var pt = document.getElementById('progress-total'); if (pt) pt.textContent = lim ? String(lim) : '?';
                        var pel = document.getElementById('progress-elapsed'); if (pel) pel.textContent = fmtMs(Number(j.elapsed_ms||0));
                        // ETA and budget
                        var etaEl = document.getElementById('progress-eta');
                        if (etaEl) {
                            var etaMs = (j.est_remaining_ms==null ? null : Number(j.est_remaining_ms));
                            etaEl.textContent = (etaMs!=null && !Number.isNaN(etaMs)) ? fmtMs(etaMs) : '—';
                        }
                        var budEl = document.getElementById('progress-budget');
                        var rem = (j.time_budget_remaining_ms==null ? null : Number(j.time_budget_remaining_ms));
                        if (budEl) budEl.textContent = (rem!=null && !Number.isNaN(rem)) ? ('Budget left: '+fmtMs(rem)) : '';
                        // Found-so-far counters (best-effort)
                        try {
                            var el;
                            el = document.getElementById('progress-emails'); if (el) el.textContent = String(j.emails_so_far || 0);
                            el = document.getElementById('progress-links-int'); if (el) el.textContent = String(j.links_internal_so_far || 0);
                            el = document.getElementById('progress-domains-ext'); if (el) el.textContent = String(j.external_domains_so_far || 0);
                        } catch(_) {}
                        // Progress bar
                        var pct = 0;
                        if (lim && lim > 0) pct = Math.max(0, Math.min(100, Math.round((v/lim)*100)));
                        var bar = document.getElementById('progress-bar'); if (bar) bar.style.width = pct+'%';
                        // Finalizing condition (site scope)
                        if (st === 'running' && rem != null && !Number.isNaN(rem) && Number(rem) <= 0) {
                            if (ps) ps.textContent = 'finalizing…';
                            if (__progressTimer) { clearInterval(__progressTimer); __progressTimer = null; }
                            setTimeout(function(){ location.reload(); }, 1000);
                            return;
                        }
                        // Stop on terminal states
                        if (st !== 'running' && st !== 'pending') {
                            if (__progressTimer) { clearInterval(__progressTimer); __progressTimer = null; }
                            setTimeout(function(){ location.reload(); }, 800);
                        }
                    } catch(e){}
                })
                .catch(function(err){
                    try {
                        if (__progressTimer) { clearInterval(__progressTimer); __progressTimer = null; }
                        var ps = document.getElementById('progress-status'); if (ps) ps.textContent = 'unavailable';
                    } catch(_) {}
                    // Single refresh to avoid loops; SSR will render final state or 404
                    setTimeout(function(){ location.reload(); }, 1500);
                });
        }
        tick();
        __progressTimer = setInterval(tick, 2000);
    }

    // Init
    (function init(){
        renderLeads();
        var _lfd = document.getElementById('lead-filter-domain');
        if (_lfd) { _lfd.addEventListener('input', renderLeads); }
        document.querySelectorAll('.lead-type').forEach(function(cb){ cb.addEventListener('change', renderLeads); });
        renderPages();
        // Pages filters listeners
        renderSocial();
        // Links (external domains) pagination - JS only
        try { setupLinksPagination(); } catch(_){}

        // Load mini compose products from server data (no AJAX)
        if (CAN_CHAT) {
            PB_PRODUCTS = __ctx.user_products || [];
            miniApplyProductsVisibility();
            // Deep link presets only make sense for owners (Shortcuts owner-only)
            if (CAN_SELECT_PAGES) { tryPresetAutoGenerate(); }
        }

        // Wire prospects + claim
        try { var pt = document.getElementById('prospect-toggle'); if (pt) pt.addEventListener('click', prospectToggle); } catch(_){}
        try { setupClaimEligibility(); } catch(_){}

        // Ensure chat button state is correct
        updateSelectionCount();
        // Load persisted chat history (owner-only)
        try { if (CAN_CHAT && USER_ID && CRAWL_ID) { loadChatHistory(); } } catch(_){}
        try {
            if (!window.__CHAT_ABORT_WIRED) {
                window.addEventListener('beforeunload', function(){
                    try { if (CHAT_ABORT_CTRL) CHAT_ABORT_CTRL.abort(); } catch(_){}
                });
                window.__CHAT_ABORT_WIRED = true;
            }
        } catch(_){}

        // Wire chat input to enable/disable Ask button based on text and selection
        try {
            var _qEl = document.getElementById('chat-question');
            if (_qEl) {
                _qEl.addEventListener('input', function(){
                    try { updateChatSendState(); } catch(_){}
                });
            }
        } catch(_){}

        // Start progress polling (private by id or public by key)
        const hasId = !!(__ctx.crawl_id);
        const hasKey = !!(__ctx.public_key);
        const st = String(__ctx.status || '').toLowerCase();
        if (st === 'running' || st === 'pending') {
            if (hasId) {
                startProgressPolling(__ctx.crawl_id);
            } else if (hasKey) {
                startPublicProgressPolling(__ctx.public_key);
            }
        }
    })();

    // Expose functions used by inline onclick in templates
    window.openChat = openChat;
    window.closeChat = closeChat;
    window.toggleChatSize = toggleChatSize;
    window.copyCurrentPage = copyCurrentPage;
    window.downloadCurrentPage = downloadCurrentPage;
    window.filterPages = filterPages;
    window.selectAllPages = selectAllPages;
    window.clearAllPages = clearAllPages;
    window.openAddLead = openAddLead;
    window.closeAddLead = closeAddLead;
    window.saveAddedLead = saveAddedLead;
    window.claimAnalysis = claimAnalysis;
    window.runStructured = runStructured;
    window.runClarityAssessmentForCurrentPage = runClarityAssessmentForCurrentPage;
    window.attachProspectSocial = attachProspectSocial;
    window.attachContactSocial = attachContactSocial;
    window.addEmailToProspect = addEmailToProspect;
    window.clearChatHistory = clearChatHistory;
})();
