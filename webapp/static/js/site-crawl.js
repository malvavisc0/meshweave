/* Shared Site Crawl form initializer (used on / and /my) */
(function () {
  'use strict';

  // Extract domain from user input
  function toDomain(value) {
    try {
      var v = (value || '').trim();
      if (!v) return '';
      if (!/^https?:\/\//i.test(v)) v = 'https://' + v;
      var u = new URL(v);
      var host = (u.hostname || '').toLowerCase();
      if (host.startsWith('www.')) host = host.slice(4);
      return host;
    } catch (e) {
      return (value || '').trim().toLowerCase();
    }
  }

  // Extract full normalized URL (with path) from user input
  function toFullUrl(value) {
    try {
      var v = (value || '').trim();
      if (!v) return '';
      if (!/^https?:\/\//i.test(v)) v = 'https://' + v;
      var u = new URL(v);
      var host = (u.hostname || '').toLowerCase();
      if (host.startsWith('www.')) host = host.slice(4);
      var path = u.pathname || '/';
      if (path !== '/' && path.endsWith('/')) path = path.slice(0, -1);
      return 'https://' + host + path;
    } catch (e) {
      return '';
    }
  }

  function initSiteCrawlForm() {
    try {
      var siteForm = document.getElementById('site-form');
      if (!siteForm) return; // Safe no-op if markup is absent
      if (siteForm.dataset && siteForm.dataset.initialized === '1') return; // idempotent
      if (siteForm.dataset) siteForm.dataset.initialized = '1';

      var siteInput = document.getElementById('site-input');
      var siteDomain = document.getElementById('site-domain');
      var sitePrivate = document.getElementById('site-private');
      var sitePublic = document.getElementById('site-public');
      var siteFeedback = document.getElementById('domain-feedback');
      var __firedValidEvent = false;

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
            var raw = String((e && e.target && e.target.value) || '');
            var dom = toDomain(raw);
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

      // Submit handler
      // Double-click guard
      var __submitting = false;

      siteForm.addEventListener('submit', function (e) {
        if (__submitting) { e.preventDefault(); return; }
        __submitting = true;
        try { trackEvent('submit_click'); } catch (_) { }
        var val = (siteInput && siteInput.value) ? siteInput.value.trim() : '';
        var dom = toDomain(val);
        if (!dom || !dom.includes('.')) {
          e.preventDefault();
          __submitting = false;
          if (siteInput) {
            try {
              siteInput.setCustomValidity('Enter a valid domain like example.com');
              siteInput.reportValidity();
              siteInput.setAttribute('aria-invalid', 'true');
            } catch (_) { }
            setTimeout(function () {
              try { siteInput.setCustomValidity(''); siteInput.setAttribute('aria-invalid', 'false'); } catch (_) { }
            }, 2000);
          }
          return;
        }
        if (siteDomain) siteDomain.value = dom;
        // Populate hidden url field with full URL (preserving path)
        var fullUrl = toFullUrl(val);
        var urlField = siteForm.querySelector('input[name="url"]');
        if (urlField && fullUrl) urlField.value = fullUrl;
        try { if (siteInput) siteInput.setAttribute('aria-invalid', 'false'); } catch (_) { }
        // Toggle public hidden field based on checkbox (runs for all users)
        if (sitePublic) { sitePublic.value = (sitePrivate && sitePrivate.checked) ? '' : '1'; }
      });
    } catch (_) { /* swallow */ }
  }

  // Expose as global initializer
  try { window.initSiteCrawlForm = initSiteCrawlForm; } catch (_) { }

  // Auto-init when markup exists
  function _auto() { try { initSiteCrawlForm(); } catch (_) { } }
  if (document.readyState === 'loading') {
    try { document.addEventListener('DOMContentLoaded', _auto, { once: true }); } catch (_) { document.addEventListener('DOMContentLoaded', _auto); }
  } else {
    _auto();
  }
})();
