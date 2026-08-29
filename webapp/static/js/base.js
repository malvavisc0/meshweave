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
    function trackEvent(event, action, type) {
      try {
        var params = new URLSearchParams();
        if (event) params.set('event', event);
        if (type) params.set('type', type);
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
      try { trackEvent(el.getAttribute('data-track') || ''); } catch (_){}
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
