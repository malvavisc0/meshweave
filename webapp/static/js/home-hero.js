/* Home hero interactions: Momentum Bar count-up
   - Counts up stats in the Momentum Bar when it enters the viewport
   - Respects prefers-reduced-motion
   - Idempotent and tiny (no deps)
*/
(function () {
  'use strict';

  function toNumber(el) {
    try {
      var t = String(el.getAttribute('data-target') || '0').replace(/[, ]+/g, '');
      var n = parseInt(t, 10);
      return isFinite(n) ? n : 0;
    } catch (_) { return 0; }
  }

  function setText(el, value) {
    try { el.textContent = Number(value).toLocaleString(); } catch (_) { el.textContent = String(value); }
  }

  function countUp(el, target, duration) {
    if (!el) return;
    if (el.dataset && el.dataset.counted === '1') { setText(el, target); return; }
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setText(el, target);
      if (el.dataset) el.dataset.counted = '1';
      return;
    }
    var start = 0;
    var startTs = 0;
    function step(ts) {
      if (!startTs) startTs = ts;
      var p = Math.min(1, (ts - startTs) / duration);
      // ease-out (cubic)
      var eased = 1 - Math.pow(1 - p, 3);
      var val = Math.round(start + (target - start) * eased);
      setText(el, val);
      if (p < 1) {
        try { requestAnimationFrame(step); } catch (_) { setTimeout(function () { step(Date.now()); }, 16); }
      } else {
        setText(el, target);
        if (el.dataset) el.dataset.counted = '1';
      }
    }
    try { requestAnimationFrame(step); } catch (_) { step(Date.now()); }
  }

  function initCountups() {
    try {
      var bar = document.querySelector('.stats-bar');
      if (!bar) return;
      var nums = bar.querySelectorAll('[data-countup]');
      if (!nums || nums.length === 0) return;

      var run = function () {
        nums.forEach(function (n, i) {
          var target = toNumber(n);
          countUp(n, target, 600 + (i * 150));
        });
      };

      // If already visible, run immediately; else observe
      var rect = bar.getBoundingClientRect();
      var inView = rect.top < (window.innerHeight || 0) && rect.bottom > 0;
      if (inView) {
        run();
        return;
      }

      if ('IntersectionObserver' in window) {
        var once = false;
        var io = new IntersectionObserver(function (entries) {
          entries.forEach(function (e) {
            if (!once && e.isIntersecting) {
              once = true;
              try { io.disconnect(); } catch (_) { }
              run();
            }
          });
        }, { threshold: 0.15 });
        io.observe(bar);
      } else {
        // Fallback: run after a short delay
        setTimeout(run, 500);
      }
    } catch (_) { /* noop */ }
  }

  /* ── Rotating hero headline ── */
  var headlines = [
    "Your customers already use AI to find answers. But AI can't find you 👀",
    "Every day your customers ask AI for answers. But your site is invisible 🫥",
    "ChatGPT, Perplexity, and Claude are right now ignoring half your website 🚨",
    "AI answers your customers' questions. It's citing your competitors 💔",
    "Your competitors are being cited by AI. Meanwhile, you are being ignored 👻"
  ];

  function initHeadlineRotation() {
    try {
      var el = document.querySelector('[data-hero-headline]');
      if (!el || !headlines.length) return;
      el.textContent = headlines[Math.floor(Math.random() * headlines.length)];
    } catch (_) { /* noop */ }
  }

  function initAll() {
    initHeadlineRotation();
    initCountups();
  }

  if (document.readyState === 'loading') {
    try { document.addEventListener('DOMContentLoaded', initAll, { once: true }); } catch (_) { document.addEventListener('DOMContentLoaded', initAll); }
  } else {
    initAll();
  }
})();
