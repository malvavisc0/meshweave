
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
    // Logged-in state and capability flags from server
    let LOGGED_IN = !!(__ctx.logged_in);
    const CAN_CHAT = !!(__ctx.can_chat);
    const CAN_SELECT_PAGES = !!(__ctx.can_select_pages);
    var PROSPECT_ID = null; var PROSPECT_SOCIALS = [];
    const SELECTED_PAGES = new Set();
    const SELECTED_SECTIONS = []; // {url, heading, snippet}
    var LAST_PRODUCT_ID = (function(){ try { return localStorage.getItem('pb:last_product_id') || ''; } catch(_) { return ''; } })();
    // Pages pager state (initialized on first render)
    var PAGES_PAGER = null;

    // Download markdown
    function downloadMarkdown() {
        try {
            var pre = document.getElementById('markdown-content');
            if (!pre) { alert('No markdown available'); return false; }
            var md = (pre.textContent || pre.innerText || '').toString();
            if (!md || md.trim().length === 0) { alert('No markdown available'); return false; }
            var blob = new Blob([md], {type: 'text/markdown;charset=utf-8'});
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a'); a.href = url; a.download = 'content.md';
            document.body.appendChild(a); a.click(); document.body.removeChild(a);
            URL.revokeObjectURL(url);
        } catch (e) { alert('Unable to download markdown'); }
        return false;
    }

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
    function getMainMarkdown() {
        var pre = document.getElementById('markdown-content');
        try { return (pre && (pre.textContent || pre.innerText) || '').toString(); } catch(_) { return ''; }
    }
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

                // Initial checkbox checked state mirrors inclusion set
                var isIncluded = SELECTED_PAGES.has(url);
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
                                SELECTED_PAGES.add(url);
                            } else {
                                SELECTED_PAGES.delete(url);
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
                            if (cb.checked) { SELECTED_PAGES.add(url); } else { SELECTED_PAGES.delete(url); }
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
            var allow = {
                'linkedin.com':'LinkedIn', 'www.linkedin.com':'LinkedIn',
                'x.com':'Twitter', 'twitter.com':'Twitter', 'www.twitter.com':'Twitter',
                'facebook.com':'Facebook','www.facebook.com':'Facebook',
                'instagram.com':'Instagram','www.instagram.com':'Instagram',
                'youtube.com':'YouTube','www.youtube.com':'YouTube',
                'tiktok.com':'TikTok','www.tiktok.com':'TikTok',
                'github.com':'GitHub','www.github.com':'GitHub'
            };
            var groups={};
            (LINKS_EXTERNAL||[]).forEach(function(u){
                try{ var h=(new URL(u)).hostname.toLowerCase(); if(!allow[h]) return; var plat=allow[h]; groups[plat]=groups[plat]||new Set(); groups[plat].add(u); }catch(_){}
            });
            var html='';
            Object.keys(groups).sort().forEach(function(plat){
                var urls=Array.from(groups[plat].values());
                html += '<div class="mb-2"><div class="small"><strong>'+plat+'</strong> <span class="small">('+(urls.length)+')</span></div><ul class="domain-list">';
                urls.forEach(function(u){
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
    function extractOutlineFromMarkdown(md){
        try{
            var out=[]; if(!md) return out;
            var re=/^#{1,3}\s+(.+)$/gm, m;
            while((m=re.exec(md))!==null){
                var txt=(m[1]||'').trim();
                var start=m.index;
                var level=1; var hashes = md.slice(m.index).match(/^#+/); if(hashes){ level = Math.min(3, hashes[0].length); }
                out.push({level:level, text:txt, startIndex:start});
            }
            // compute endIndex for each
            out.forEach(function(h,i){
                var next = out.slice(i+1).find(function(k){ return k.level<=h.level; });
                h.endIndex = next ? next.startIndex : md.length;
            });
            return out;
        }catch(_){ return []; }
    }
    function getSectionSnippet(md, node){
        try{
            if(!node) return '';
            var s = Math.max(0, Number(node.startIndex||0));
            var e = Math.min(md.length, Number(node.endIndex==null ? md.length : node.endIndex));
            var slice = md.slice(s,e);
            var MAX=3000;
            if (slice.length>MAX) slice = slice.slice(0, MAX) + "\n\n[...]";
            return slice;
        }catch(_){ return ''; }
    }
    function bucketsByLength(len){
        if (len < 2000) return 'short';
        if (len >= 8000) return 'long';
        return 'medium';
    }
    function renderOutline(md, url){
        try{
            var ul = document.getElementById('page-outline'); if(!ul) return;
            if(!md){ ul.innerHTML = '<li class="small">No outline</li>'; return; }
            var nodes = extractOutlineFromMarkdown(md);
            if(!nodes.length){ ul.innerHTML = '<li class="small">No headings</li>'; return; }
            var html='';
            nodes.forEach(function(n, idx){
                var checked = SELECTED_SECTIONS.some(function(ss){ return (ss.url===url) && (ss.heading===n.text); });
                html += '<li>' +
                    '<label class="small">' +
                    '<input type="checkbox" class="outline-include" data-url="'+jsStr(url)+'" data-h-index="'+idx+'" '+(checked?'checked':'')+'>' +
                    '<span class="ml-1">'+ (n.level===1?'# ':n.level===2?'## ':'### ') + escapeHtml(n.text) + '</span>' +
                    '</label>' +
                '</li>';
            });
            ul.innerHTML = html;
        }catch(_){}
    }
    function currentSelectedPageUrl(){
        try{
            var li=document.querySelector('#pages-list li.active') || document.querySelector('#pages-list li input[type=checkbox]:checked')?.closest('li');
            return li ? (li.getAttribute('data-url')||'') : '';
        }catch(_){ return ''; }
    }
    function pbIncludeCurrentPage(){
        try{
            var url = currentSelectedPageUrl();
            if (!url) { alert('Select a page first'); return; }
            if (SELECTED_PAGES.has(url)) { SELECTED_PAGES.delete(url); } else { SELECTED_PAGES.add(url); }
            renderPages();
        }catch(_){}
    }
    function pbIncludeSelectedSections(){
        try{
            var url = currentSelectedPageUrl();
            if (!url) { alert('Select a page first'); return; }
            var pre=document.getElementById('page-markdown'); var md=(pre && (pre.textContent||pre.innerText)||'');
            var nodes = extractOutlineFromMarkdown(md);
            var boxes = document.querySelectorAll('#page-outline .outline-include');
            var added=0, removed=0;
            boxes.forEach(function(cb){
                var idx = Number(cb.getAttribute('data-h-index')||'-1'); if (Number.isNaN(idx) || idx<0 || idx>=nodes.length) return;
                var node = nodes[idx];
                var existsIdx = SELECTED_SECTIONS.findIndex(function(ss){ return (ss.url===url) && (ss.heading===node.text); });
                if (cb.checked && existsIdx === -1) {
                    SELECTED_SECTIONS.push({url:url, heading:node.text, snippet:getSectionSnippet(md, node)});
                    added++;
                } else if (!cb.checked && existsIdx !== -1) {
                    SELECTED_SECTIONS.splice(existsIdx,1);
                    removed++;
                }
            });
            if (added||removed) alert('Sections updated: +'+added+' / -'+removed);
        }catch(_){}
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
            'Key sections:',
            '{sections}',
            '',
            'Write a persuasive, tailored pitch.'
        ].join('\n'),
        outreach_email: [
            'Draft a first-touch outreach email introducing {product_name}.',
            'Tone: {tone}',
            'CTA: {cta}',
            'Length: {length}',
            '',
            'Context (site pages and sections):',
            'Sources:',
            '{sources}',
            '',
            'Sections (quoted):',
            '{sections}',
            '',
            'Use {contact_info} for the signature/contact details.'
        ].join('\n'),
        weaknesses: [
            'Analyze weaknesses and opportunities for the target site. Use the following context:',
            'Sources:',
            '{sources}',
            '',
            'Sections:',
            '{sections}',
            '',
            'Summarize clearly and concisely for executive review.'
        ].join('\n'),
        clarity_check: [
            'Assess clarity for the selected page/sections:',
            'Sources:',
            '{sources}',
            '',
            'Sections:',
            '{sections}',
            '',
            'Identify confusing parts and propose concise improvements. Keep in bullet points.'
        ].join('\n')
    };
    var TONE_PRESETS = [
        'Professional',
        'Friendly',
        'Conversational',
        'Enthusiastic',
        'Concise',
        'Analytical',
        'Persuasive',
        'Empathetic',
        'Confident',
        'Playful'
    ];
    function openPromptBuilder(){
        try{
            var el=document.getElementById('prompt-builder'); if(!el) return;
            el.style.display='flex'; el.setAttribute('aria-hidden','false'); PB_OPEN=true;
            // load products (once per open)
            pbLoadProducts();
            // ensure template options exist
            var ts = document.getElementById('pb-template');
            if (ts && !ts.options.length) {
                ['sales_pitch','outreach_email','weaknesses','clarity_check'].forEach(function(k){
                    var opt=document.createElement('option'); opt.value=k; opt.textContent={
                        sales_pitch:'Sales Pitch', outreach_email:'Outreach Email', weaknesses:'Weaknesses & Opportunities', clarity_check:'Clarity Check'
                    }[k] || k;
                    ts.appendChild(opt);
                });
            }
        }catch(_){}
    }
    function closePromptBuilder(){
        try{
            var el=document.getElementById('prompt-builder'); if(!el) return;
            el.style.display='none'; el.setAttribute('aria-hidden','true'); PB_OPEN=false;
        }catch(_){}
    }
    function togglePromptBuilderSize(){
        PB_LARGE = !PB_LARGE;
        var el=document.getElementById('prompt-builder'); if(!el) return;
        el.style.height = PB_LARGE ? '70vh' : '420px';
        el.style.width = PB_LARGE ? '600px' : '360px';
    }
    function pbLoadProducts(){
        apiJson('/api/products','GET').then(function(r){
            var items = (r && r.items) || [];
            PB_PRODUCTS = items;
            var sel = document.getElementById('pb-product'); if(!sel) return;
            sel.innerHTML='';
            items.forEach(function(p){
                var opt=document.createElement('option');
                opt.value=p.id; opt.textContent=p.name;
                sel.appendChild(opt);
            });
            // select last used or first
            var chosen = LAST_PRODUCT_ID && items.find(function(p){return p.id===LAST_PRODUCT_ID;}) ? LAST_PRODUCT_ID : (items[0] && items[0].id) || '';
            if (chosen) sel.value = chosen;
            if (chosen) { try{ localStorage.setItem('pb:last_product_id', chosen); }catch(_){ } }
            pbRenderVarsForSelected();
            sel.onchange = function(){
                try{ localStorage.setItem('pb:last_product_id', sel.value || ''); }catch(_){}
                pbRenderVarsForSelected();
            };
        }).catch(function(e){
            // not signed in or no products
            var sel = document.getElementById('pb-product'); if(sel){ sel.innerHTML = ''; }
            document.getElementById('pb-vars').innerHTML = '<div class="small">Sign in and create a Product to use Prompt Builder.</div>';
        });
    }
    function pbRenderVarsForSelected(){
        var sel = document.getElementById('pb-product'); var pid = sel ? sel.value : '';
        var p = PB_PRODUCTS.find(function(x){ return x.id===pid; }) || {};
        var d = (p.defaults || {});
        var curTone = (d.tone || p.tone || '') || '';
        var toneOpts = (TONE_PRESETS || []).map(function(t){
            var selected = (String(curTone).toLowerCase() === String(t).toLowerCase()) ? ' selected' : '';
            return '<option value="'+jsStr(t)+'"'+selected+'>'+escapeHtml(t)+'</option>';
        }).join('');
        var html = ''
            + '<div class="small mt-1" style="display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:8px;">'
            + '  <label>Tone<br><select id="pb-var-tone">'+ toneOpts +'</select></label>'
            + '  <label>CTA<br><input type="text" id="pb-var-cta" value="'+jsStr(d.cta||'')+'"></label>'
            + '  <label>Length<br><input type="text" id="pb-var-length" value="'+jsStr(d.length||'')+'"></label>'
            + '</div>';
        document.getElementById('pb-vars').innerHTML = html;
    }
    function generatePrompt(tid, vars, selectedPages, selectedSections){
        var tpl = BUILTIN_TEMPLATES[tid] || '';
        var sources_md = Array.from(selectedPages).map(function(u){ return '- ' + u; }).join('\n');
        var sections_md = selectedSections.map(function(s){ return '### ' + s.heading + '\n\n' + s.snippet.trim(); }).join('\n\n');

        // Resolve selected product and map product variables
        var sel = document.getElementById('pb-product');
        var pid = sel ? (sel.value || '') : '';
        var p = PB_PRODUCTS.find(function(x){ return x.id === pid; }) || {};

        var prodVars = {
            product_name: p.name || '',
            product_website: p.website || '',
            product_description: p.description || '',
            icp: p.icp || '',
            pricing: p.pricing || '',
            contact_info: p.contact_info || ''
        };

        var map = Object.assign({}, prodVars, vars, {sources: sources_md, sections: sections_md});
        return tpl.replace(/\{([a-zA-Z0-9_]+)\}/g, function(_m, k){ return (map[k] == null ? '' : String(map[k])); });
    }
    function pbCollectVars(){
        function gv(id){ var el=document.getElementById(id); return el ? (el.value||'') : ''; }
        return {
            tone: gv('pb-var-tone'),
            cta: gv('pb-var-cta'),
            length: gv('pb-var-length')
        };
    }
    function pbBuild(){
        var tid = (function(){ var el=document.getElementById('pb-template'); return el ? (el.value||'sales_pitch') : 'sales_pitch'; })();
        var vars = pbCollectVars();
        var text = generatePrompt(tid, vars, SELECTED_PAGES, SELECTED_SECTIONS);
        var out = document.getElementById('pb-output'); if (out) out.value = text;
        var warn = document.getElementById('pb-length-warning');
        if (warn) warn.classList.toggle('hidden', !(text && text.length > 50000));
    }
    function pbCopyText(){
        try{
            var out=document.getElementById('pb-output'); var text=(out && out.value)||'';
            if (navigator.clipboard && window.isSecureContext) {
                navigator.clipboard.writeText(text).then(function(){ alert('Copied'); }, function(){ legacyCopy(text); });
            } else { legacyCopy(text); }
        } catch(_) {}
    }
    function pbCopyMarkdown(){ pbCopyText(); }
    function pbDownloadMarkdown(){
        try{
            var out=document.getElementById('pb-output'); var md=(out && out.value)||'';
            if(!md.trim()){ alert('Nothing to download'); return; }
            var tid = (function(){ var el=document.getElementById('pb-template'); return el ? (el.value||'sales_pitch') : 'sales_pitch'; })();
            var fname = (BASE_DOMAIN || 'site') + '-' + tid + '.md';
            var blob=new Blob([md],{type:'text/markdown;charset=utf-8'}); var u=URL.createObjectURL(blob); var a=document.createElement('a'); a.href=u; a.download=fname; document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(u);
        }catch(_){}
    }

    /* Mini compose helpers and shortcuts */
    function getQueryParam(name){
        try {
            var url = new URL(window.location.href);
            return url.searchParams.get(name);
        } catch(_) { return null; }
    }
    function miniLoadProducts(){
        apiJson('/api/products','GET').then(function(r){
            PB_PRODUCTS = (r && r.items) || [];
            var sel = document.getElementById('mini-product'); if (!sel) return;
            sel.innerHTML = '';
 
            // small inline helper to show a hint under the Product select
            function setHintHtml(html){
                try{
                    var hint = document.getElementById('mini-product-hint');
                    if (!hint) {
                        hint = document.createElement('div');
                        hint.id = 'mini-product-hint';
                        hint.className = 'small';
                        var parent = sel.parentNode;
                        if (parent) parent.appendChild(hint);
                    }
                    hint.innerHTML = html || '';
                    hint.style.display = html ? '' : 'none';
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
                // CTA uses DB-backed /api/products result (not template JSON). Show action to add a Product.
                setHintHtml(
                    '<div class="mt-1">' +
                    'No products yet. ' +
                    '<a class="btn btn-primary" href="/products" aria-label="Add a new product">Add Product</a>' +
                    '</div>'
                );
                setComposeVisibility(false);
            } else {
                setHintHtml('');
                setComposeVisibility(true);
            }
 
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
        }).catch(function(e){
            var sel = document.getElementById('mini-product'); if (sel) sel.innerHTML = '';
 
            function setHintHtml(html){
                try{
                    var hint = document.getElementById('mini-product-hint');
                    if (!hint) {
                        hint = document.createElement('div');
                        hint.id = 'mini-product-hint';
                        hint.className = 'small';
                        if (sel && sel.parentNode) sel.parentNode.appendChild(hint);
                    }
                    hint.innerHTML = html || '';
                    hint.style.display = html ? '' : 'none';
                }catch(_){}
            }
            function setComposeVisibility(hasProducts){
                try{
                    var cfg = document.querySelector('#compose-chat .config-grid');
                    var sa  = document.querySelector('#compose-chat .structured-actions');
                    var notices = document.getElementById('compose-notices');
                    var showCompose = (!!CAN_SELECT_PAGES) && !!hasProducts;
                    [cfg, sa].forEach(function(el){ if (el) el.style.display = showCompose ? '' : 'none'; });
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
 
            if (e && e.status === 401) {
                setHintHtml('Sign in to use shortcuts.');
                setComposeVisibility(false);
            } else {
                setHintHtml(
                    '<div class="mt-1">' +
                    'No products yet. ' +
                    '<a class="btn btn-primary" href="/products" aria-label="Add a new product">Add Product</a>' +
                    '</div>'
                );
                setComposeVisibility(false);
            }
        });
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
        var sources_md = Array.from(SELECTED_PAGES).map(function(u){ return '- ' + u; }).join('\n');
        var sections_md = SELECTED_SECTIONS.map(function(s){ return '### ' + s.heading + '\n\n' + s.snippet.trim(); }).join('\n\n');
        // Optional shorten toggle
        try{
            if (document.getElementById('mini-shorten') && document.getElementById('mini-shorten').checked) {
                sections_md = SELECTED_SECTIONS.map(function(s){
                    var sn = s.snippet.replace(/\s+/g,' ').trim();
                    if (sn.length > 220) sn = sn.slice(0, 220) + ' [...]';
                    return '- ' + s.heading + ': ' + sn;
                }).join('\n');
            }
        }catch(_){}
        var map = {
            product_name: p.name || '',
            product_website: p.website || '',
            product_description: p.description || '',
            icp: p.icp || '',
            pricing: p.pricing || '',
            contact_info: p.contact_info || '',
            tone: tone, cta: cta, length: length,
            sources: sources_md, sections: sections_md
        };
        return tpl.replace(/\{([a-zA-Z0-9_]+)\}/g, function(_m, k){ return (map[k] == null ? '' : String(map[k])); });
    }
    function openChatAndPrefill(text){
        try{
            openChat();
            var input = document.getElementById('chat-question');
            if (input) input.value = text || '';
            if (navigator.clipboard && window.isSecureContext && text) {
                navigator.clipboard.writeText(text).then(function(){}, function(){});
            }
            alert('Draft generated and copied');
        }catch(_){}
    }
    function generateAndOpenChat(templateId){
        try{
            // ensure products loaded or try load
            if (!PB_PRODUCTS || PB_PRODUCTS.length === 0) {
                miniLoadProducts();
                setTimeout(function(){ // naive retry shortly
                    var txt = buildPromptFromMini(templateId);
                    openChatAndPrefill(txt);
                }, 300);
                return;
            }
            var txt = buildPromptFromMini(templateId);
            openChatAndPrefill(txt);
        }catch(_){}
    }
    // Deep link: preset auto-generate
    function tryPresetAutoGenerate(){
        try{
            var preset = getQueryParam('preset');
            if (preset && BUILTIN_TEMPLATES[preset]) {
                // Load products then generate structured output into results panel
                if (!PB_PRODUCTS || PB_PRODUCTS.length===0) {
                    miniLoadProducts();
                    setTimeout(function(){ runStructured(preset); }, 300);
                } else {
                    runStructured(preset);
                }
            }
        }catch(_){}
    }

    /* ----- Emails preview helpers ----- */
    function scrollToFullEmails() {
        try {
            var el = document.getElementById('m-leads') || document.getElementById('leads-tbody');
            if (el && el.scrollIntoView) el.scrollIntoView({behavior:'smooth', block:'start'});
        } catch(_) {}
    }
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
    function showAddEmailForm(){
        var el = document.getElementById('add-email-inline'); if (el) el.classList.remove('hidden');
        try { document.getElementById('new-email-inline').focus(); } catch(_){}
    }
    function hideAddEmailForm(){
        var el = document.getElementById('add-email-inline'); if (el) el.classList.add('hidden');
    }
    function addInlineEmail(){
        var e = (document.getElementById('new-email-inline').value || '').trim().toLowerCase();
        if (!e) { alert('Email is required'); return; }
        saveAddedLeadInline(e);
    }
    function saveAddedLeadInline(email) {
        prospectEnsure().then(function(pid){
            return apiJson('/api/prospects/'+encodeURIComponent(pid)+'/contacts','POST',{ email: email });
        }).then(function(){
            alert('Email added');
            hideAddEmailForm();
        }).catch(function(err){
            if (err && err.status === 401) alert('Sign in to add');
            else alert('Unable to add email');
        });
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
 
            // Add only current slice URLs
            (st.sliceUrls || []).forEach(function(url){
                if (url) SELECTED_PAGES.add(url);
            });
 
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
                if (url) SELECTED_PAGES.delete(url);
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
    function updateSelectionCount(){
        var count = Array.from(SELECTED_PAGES.values()).length;
        try { var el=document.getElementById('selected-count'); if (el) el.textContent = String(count); } catch(_){}
        try { var ce=document.getElementById('chat-page-count'); if (ce) ce.textContent = String(count); } catch(_){}
        // Row selected styles based on checkbox state
        document.querySelectorAll('#pages-list li').forEach(function(li){
            var cb = li.querySelector('input[type=checkbox]');
            li.classList.toggle('selected', !!(cb && cb.checked));
        });
        // Enable/disable Ask button strictly based on having at least one selected page
        try {
            var sendBtn = document.getElementById('chat-send');
            if (sendBtn) sendBtn.disabled = (count === 0);
        } catch(_){}
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
            SELECTED_PAGES.clear();
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
            addChatMessage('ai', 'Chat is coming soon.');
        } catch(e) { alert('Unable to generate content'); }
    }

    /* ----- Chat skeleton (Pass 1 = coming soon) ----- */
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
    function sendChatMessage(){
        var q = (document.getElementById('chat-question').value || '').trim();
        if (!q) return;
        var n = Array.from(SELECTED_PAGES.values()).length;
        addChatMessage('user', q);
        if (n === 0) {
            addChatMessage('ai', 'Please select some pages first.');
            return;
        }
        // Coming soon in Pass 1
        addChatMessage('ai', 'Chat is coming soon. Use Structured Actions for now.');
    }

    function runClarityAssessmentForCurrentPage(){
        try{
            var pre = document.getElementById('page-markdown');
            var md = (pre && (pre.textContent || pre.innerText) || '').toString().trim();
            if (!md) { alert('No markdown to assess'); return; }
            var url = currentSelectedPageUrl();
            var sources_md = url ? ('- ' + url) : '';
            var tpl = (BUILTIN_TEMPLATES && BUILTIN_TEMPLATES.clarity_check) || [
                'Assess clarity for the selected page/sections:',
                'Sources:',
                '{sources}',
                '',
                'Sections:',
                '{sections}',
                '',
                'Identify confusing parts and propose concise improvements. Keep in bullet points.'
            ].join('\n');
            var prompt = tpl.replace(/\{sources\}/g, sources_md).replace(/\{sections\}/g, '### Page Content\n\n' + md);
            addChatMessage('user', prompt);
            addChatMessage('ai', 'Chat is coming soon.');
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
        return String(s).replace(/[&<>"']/g, function(c){
            return {'&':'&','<':'<','>':'>','"':'"',"'":'&#39;'}[c];
        });
    }

    /* Keep existing code below */

    // Mobile tabs
    (function() {
        var mtabs = document.querySelectorAll('.mobile-tab');
        function activate(targetSel, btn) {
            document.querySelectorAll('.mobile-section').forEach(function(sec){ sec.classList.remove('active'); sec.style.display='none'; });
            var t = document.querySelector(targetSel); if (t) { t.classList.add('active'); t.style.display='block'; }
            mtabs.forEach(function(b){ b.setAttribute('aria-selected','false'); });
            btn.setAttribute('aria-selected','true');
        }
        mtabs.forEach(function(btn){
            btn.addEventListener('click', function(){
                activate(this.getAttribute('data-target'), this);
            });
        });
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

    // Links Explorer rendering

    // Pages panel removed

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

        // Load mini compose products (chat-only; section rendered when CAN_CHAT)
        if (CAN_CHAT) {
            miniLoadProducts();
            // Deep link presets only make sense for owners (Shortcuts owner-only)
            if (CAN_SELECT_PAGES) { tryPresetAutoGenerate(); }
        }

        // Wire prospects + claim
        try { var pt = document.getElementById('prospect-toggle'); if (pt) pt.addEventListener('click', prospectToggle); } catch(_){}
        try { setupClaimEligibility(); } catch(_){}

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
    window.pbIncludeCurrentPage = pbIncludeCurrentPage;
    window.pbIncludeSelectedSections = pbIncludeSelectedSections;
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
})();
