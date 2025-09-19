/* Global helpers shared across pages */
(function () {
  'use strict';

  function getMeta(name) {
    try {
      var el = document.querySelector('meta[name="' + name + '"]');
      return el ? (el.getAttribute('content') || '') : '';
    } catch (_) {
      return '';
    }
  }

  function getCsrfToken() {
    return getMeta('csrf-token') || '';
  }

  // Consistent HTML escaping
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&')
      .replace(/</g, '<')
      .replace(/>/g, '>')
      .replace(/"/g, '"')
      .replace(/'/g, '&#39;');
  }

  function legacyCopy(text) {
    try {
      var ta = document.createElement('textarea');
      ta.value = text; ta.setAttribute('readonly','');
      ta.style.position = 'absolute'; ta.style.left = '-9999px';
      document.body.appendChild(ta); ta.select();
      var ok = document.execCommand('copy');
      document.body.removeChild(ta);
      if (ok) alert('Link copied'); else prompt('Copy this link:', text);
    } catch (e) { prompt('Copy this link:', text); }
  }

  function copyLink(url) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(url).then(function () { alert('Link copied'); }, function () { legacyCopy(url); });
      } else {
        legacyCopy(url);
      }
    } catch (e) { legacyCopy(url); }
    try { trackEvent('share_click', null, 'copy'); } catch (err) {}
    return false;
  }

  // Lightweight tracking helper using Beacon API when available
  function trackEvent(event, action, type) {
    try {
      var params = new URLSearchParams();
      if (event) params.set('event', event);
      if (action) params.set('action', action);
      if (type) params.set('type', type);
      var url = '/api/track?' + params.toString();
      if (navigator.sendBeacon) {
        var blob = new Blob([], {type: 'text/plain'});
        navigator.sendBeacon(url, blob);
      } else {
        fetch(url, {method:'GET', credentials:'same-origin'});
      }
    } catch (e) {}
  }

  // Common JSON fetch wrapper with uniform error handling
  function apiJson(url, method, body) {
    return fetch(url, {
      method: method || 'GET',
      headers: {'Content-Type': 'application/json'},
      credentials: 'same-origin',
      body: body ? JSON.stringify(body) : undefined
    }).then(async function(r){
      var data = null;
      try { data = await r.json(); } catch (_){}
      if (!r.ok) { throw {status:r.status, body:data}; }
      return data || {};
    });
  }

  // Expose minimal globals required by templates and route scripts
  window.copyLink = copyLink;
  window.legacyCopy = legacyCopy;
  window.escapeHtml = escapeHtml;
  window.trackEvent = trackEvent;
  window.apiJson = apiJson;
  window.getCsrfToken = getCsrfToken;

  // Delegate tracking and copy handlers on links/buttons
  try {
    document.addEventListener('click', function(e){
      var t = e.target || null;
      var el = (t && t.closest) ? t.closest('a[data-track], [data-copy]') : null;
      if (!el) return;
      // tracking
      if (el.hasAttribute('data-track')) {
        try { trackEvent(el.getAttribute('data-track') || ''); } catch (_){}
      }
      // copy handler
      if (el.hasAttribute('data-copy')) {
        e.preventDefault();
        var val = el.getAttribute('data-copy') || '';
        try { copyLink(val); } catch (_){}
      }
    }, true);
  } catch (_){}

})();
