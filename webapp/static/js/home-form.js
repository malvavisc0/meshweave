/* Home form functionality - extracted from home.html */
(function () {
    'use strict';

    const siteForm = document.getElementById('site-form');
    const siteInput = document.getElementById('site-input');
    const siteDomain = document.getElementById('site-domain');
    const sitePrivate = document.getElementById('site-private');
    const sitePublic = document.getElementById('site-public');
    const siteFeedback = document.getElementById('domain-feedback');
    let __firedValidEvent = false;

    // Function to extract domain
    function toDomain(value) {
        try {
            let v = (value || '').trim();
            if (!v) return '';
            if (!/^https?:\/\//i.test(v)) v = 'https://' + v;
            const u = new URL(v);
            let host = (u.hostname || '').toLowerCase();
            if (host.startsWith('www.')) host = host.slice(4);
            return host;
        } catch (e) {
            return (value || '').trim().toLowerCase();
        }
    }

    // Autofocus input on load
    try { if (siteInput && siteInput.focus) siteInput.focus(); } catch (_) { }

    // Analytics: focus event
    try {
        if (siteInput) {
            siteInput.addEventListener('focus', function () { try { trackEvent('domain_focus'); } catch (_) { } });
        }
    } catch (_) { }

    // Real-time validation feedback
    try {
        if (siteInput && siteFeedback) {
            siteInput.addEventListener('input', function (e) {
                const raw = String(e.target.value || '');
                const dom = toDomain(raw);
                if (dom && dom.includes('.')) {
                    siteFeedback.className = 'small validation-success';
                    siteFeedback.textContent = '✓ Valid domain';
                    try { siteInput.setAttribute('aria-invalid', 'false'); } catch (_) { }
                    if (!__firedValidEvent) { try { trackEvent('domain_valid_input'); } catch (_) { } __firedValidEvent = true; }
                } else if (raw.trim().length > 0) {
                    siteFeedback.className = 'small validation-error';
                    siteFeedback.textContent = 'Enter a valid domain like example.com';
                    try { siteInput.setAttribute('aria-invalid', 'true'); } catch (_) { }
                } else {
                    siteFeedback.className = 'small';
                    siteFeedback.textContent = '';
                    try { siteInput.setAttribute('aria-invalid', 'false'); } catch (_) { }
                }
            });
        }
    } catch (_) { }

    // Site form submit
    siteForm.addEventListener('submit', function (e) {
        try { trackEvent('submit_click'); } catch (_) { }
        const val = siteInput.value.trim();
        const dom = toDomain(val);
        if (!dom || !dom.includes('.')) {
            e.preventDefault();
            siteInput.setCustomValidity('Enter a valid domain like example.com');
            siteInput.reportValidity();
            try { siteInput.setAttribute('aria-invalid', 'true'); } catch (_) { }
            setTimeout(() => {
                try { siteInput.setCustomValidity(''); siteInput.setAttribute('aria-invalid', 'false'); } catch (_) { }
            }, 2000);
            return;
        }
        siteDomain.value = dom;
        try { siteInput.setAttribute('aria-invalid', 'false'); } catch (_) { }
        // For anonymous users allow private override
        if (sitePublic) { sitePublic.value = (sitePrivate && sitePrivate.checked) ? '' : '1'; }
    });

})();