/* DEPRECATED: Replaced by /static/js/site-crawl.js
   This shim remains to ensure any older templates still initialize the Site Crawl form.
*/
(function () {
  'use strict';
  function _init() {
    try {
      if (typeof window.initSiteCrawlForm === 'function') {
        window.initSiteCrawlForm();
        return;
      }
      // If the shared script isn't loaded yet, load it and then init.
      var s = document.createElement('script');
      s.src = '/static/js/site-crawl.js';
      s.async = true;
      s.onload = function () {
        try { if (typeof window.initSiteCrawlForm === 'function') window.initSiteCrawlForm(); } catch (_){}
      };
      document.head.appendChild(s);
    } catch (_){}
  }
  if (document.readyState === 'loading') {
    try { document.addEventListener('DOMContentLoaded', _init, { once: true }); } catch (_){ document.addEventListener('DOMContentLoaded', _init); }
  } else {
    _init();
  }
})();