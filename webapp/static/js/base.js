/* Global helpers shared across pages */
(function () {
  'use strict';

  // Consistent HTML escaping
  function escapeHtml(s) {
    try {
      var div = document.createElement('div');
      div.textContent = String(s == null ? '' : s);
      return div.innerHTML;
    } catch (_){
      // Fallback: best-effort stringify
      return String(s == null ? '' : s);
    }
  }

    // Lightweight tracking helper using Beacon API when available
    function trackEvent(event, action, type, surface) {
      try {
        var params = new URLSearchParams();
        if (event) params.set('event', event);
        if (type) params.set('type', type);
        if (surface) params.set('surface', surface);
        if (action != null && action !== '') {
          if (typeof action === 'object') {
            try { params.set('meta', JSON.stringify(action)); } catch (_) {}
          } else {
            params.set('action', String(action));
          }
        }
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
  window.escapeHtml = escapeHtml;
  window.trackEvent = trackEvent;
  window.apiJson = apiJson;

  // Delegate tracking handler on links/buttons
  try {
    document.addEventListener('click', function(e){
      var t = e.target || null;
      var el = (t && t.closest) ? t.closest('a[data-track]') : null;
      if (!el) return;
      try {
        var ev = el.getAttribute('data-track') || '';
        var surf = el.getAttribute('data-track-surface') ||
          (el.closest('[data-surface]') ? el.closest('[data-surface]').getAttribute('data-surface') : '');
        trackEvent(ev, null, null, surf);
      } catch (_){}
    }, true);
  } catch (_){}

  // Funnel nudge: dismissal ("not now" — resurfaces after a cooldown)
  // and CTA beacons (which gate offer was taken).
  try {
    document.addEventListener('click', function(e){
      var t = e.target || null;
      var btn = (t && t.closest) ? t.closest('.funnel-nudge-dismiss') : null;
      var cta = (t && t.closest) ? t.closest('[data-nudge-cta]') : null;
      if (cta) {
        try {
          var img = new Image();
          img.src = '/api/track?event=nudge_cta_click&surface=' +
            encodeURIComponent(cta.getAttribute('data-nudge-cta') || '');
        } catch (_) {}
        return;
      }
      if (!btn) return;
      var nudge = btn.getAttribute('data-nudge-name') || '';
      var panel = btn.closest('.funnel-nudge');
      try {
        var meta = document.querySelector('meta[name="csrf-token"]');
        fetch('/api/funnel/nudge/dismiss', {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRF-Token': meta ? meta.getAttribute('content') : ''
          },
          body: JSON.stringify({nudge: nudge})
        }).then(function(){ if (panel) panel.remove(); })
          .catch(function(){ if (panel) panel.remove(); });
      } catch (_) {}
    }, true);
  } catch (_){}

  // Confirmation guard for destructive forms (data-confirm="Message")
  try {
    document.addEventListener('submit', function(e){
      var form = e.target;
      if (!form || !form.getAttribute || !form.hasAttribute('data-confirm')) return;
      if (!window.confirm(form.getAttribute('data-confirm') || 'Are you sure?')) {
        e.preventDefault();
      }
    }, true);
  } catch (_){}

})();
