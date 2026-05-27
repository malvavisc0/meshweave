/* Home hero interactions:
   - Canvas particle network (neural mesh)
   - Momentum Bar count-up
   - Rotating hero headline
   Respects prefers-reduced-motion · Idempotent · No deps
*/
(function () {
  'use strict';

  /* ── Particle Network ── */
  function initParticleNetwork() {
    var canvas = document.querySelector('.hero-particle-canvas');
    if (!canvas) return;
    var ctx = canvas.getContext('2d');
    if (!ctx) return;

    var reducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    var hero = canvas.parentElement;
    var particles = [];
    var PARTICLE_COUNT = 45;
    var CONNECT_DIST = 140;
    var MOUSE_DIST = 180;
    var mouse = { x: -9999, y: -9999 };
    var raf = 0;

    function resize() {
      var rect = hero.getBoundingClientRect();
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = rect.width * dpr;
      canvas.height = rect.height * dpr;
      canvas.style.width = rect.width + 'px';
      canvas.style.height = rect.height + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return rect;
    }

    function createParticles() {
      var rect = hero.getBoundingClientRect();
      particles = [];
      for (var i = 0; i < PARTICLE_COUNT; i++) {
        particles.push({
          x: Math.random() * rect.width,
          y: Math.random() * rect.height,
          vx: (Math.random() - 0.5) * 0.4,
          vy: (Math.random() - 0.5) * 0.4,
          r: 1.5 + Math.random() * 2.5,
          pulse: Math.random() * Math.PI * 2
        });
      }
    }

    function draw() {
      var rect = hero.getBoundingClientRect();
      var w = rect.width;
      var h = rect.height;
      ctx.clearRect(0, 0, w, h);

      var time = Date.now() * 0.001;

      // Update positions
      for (var i = 0; i < particles.length; i++) {
        var p = particles[i];
        p.x += p.vx;
        p.y += p.vy;
        p.pulse += 0.02;

        // Wrap around edges
        if (p.x < -10) p.x = w + 10;
        if (p.x > w + 10) p.x = -10;
        if (p.y < -10) p.y = h + 10;
        if (p.y > h + 10) p.y = -10;
      }

      // Draw connections
      for (var i = 0; i < particles.length; i++) {
        for (var j = i + 1; j < particles.length; j++) {
          var dx = particles[i].x - particles[j].x;
          var dy = particles[i].y - particles[j].y;
          var dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < CONNECT_DIST) {
            var alpha = (1 - dist / CONNECT_DIST) * 0.25;
            ctx.beginPath();
            ctx.moveTo(particles[i].x, particles[i].y);
            ctx.lineTo(particles[j].x, particles[j].y);
            ctx.strokeStyle = 'rgba(0, 163, 108, ' + alpha + ')';
            ctx.lineWidth = 0.6;
            ctx.stroke();
          }
        }

        // Mouse connections
        var mdx = particles[i].x - mouse.x;
        var mdy = particles[i].y - mouse.y;
        var mdist = Math.sqrt(mdx * mdx + mdy * mdy);
        if (mdist < MOUSE_DIST) {
          var malpha = (1 - mdist / MOUSE_DIST) * 0.4;
          ctx.beginPath();
          ctx.moveTo(particles[i].x, particles[i].y);
          ctx.lineTo(mouse.x, mouse.y);
          ctx.strokeStyle = 'rgba(0, 163, 108, ' + malpha + ')';
          ctx.lineWidth = 0.8;
          ctx.stroke();
        }
      }

      // Draw nodes
      for (var i = 0; i < particles.length; i++) {
        var p = particles[i];
        var glow = 0.3 + 0.3 * Math.sin(p.pulse);
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(0, 163, 108, ' + glow + ')';
        ctx.fill();

        // Outer glow for larger nodes
        if (p.r > 3) {
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.r + 4, 0, Math.PI * 2);
          ctx.fillStyle = 'rgba(0, 163, 108, ' + (glow * 0.15) + ')';
          ctx.fill();
        }
      }

      raf = requestAnimationFrame(draw);
    }

    // If reduced-motion, draw a single static frame
    if (reducedMotion) {
      resize();
      createParticles();
      // Draw once (no animation loop)
      var rect = hero.getBoundingClientRect();
      var w = rect.width;
      var h = rect.height;
      for (var i = 0; i < particles.length; i++) {
        for (var j = i + 1; j < particles.length; j++) {
          var dx = particles[i].x - particles[j].x;
          var dy = particles[i].y - particles[j].y;
          var dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < CONNECT_DIST) {
            var alpha = (1 - dist / CONNECT_DIST) * 0.15;
            ctx.beginPath();
            ctx.moveTo(particles[i].x, particles[i].y);
            ctx.lineTo(particles[j].x, particles[j].y);
            ctx.strokeStyle = 'rgba(0, 163, 108, ' + alpha + ')';
            ctx.lineWidth = 0.6;
            ctx.stroke();
          }
        }
        var p = particles[i];
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(0, 163, 108, 0.35)';
        ctx.fill();
      }
      return;
    }

    resize();
    createParticles();

    hero.addEventListener('mousemove', function (e) {
      var rect = hero.getBoundingClientRect();
      mouse.x = e.clientX - rect.left;
      mouse.y = e.clientY - rect.top;
    });

    hero.addEventListener('mouseleave', function () {
      mouse.x = -9999;
      mouse.y = -9999;
    });

    var resizeTimer;
    window.addEventListener('resize', function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        resize();
        createParticles();
      }, 150);
    });

    draw();
  }

  /* ── Count-up (stats bar) ── */
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

      var rect = bar.getBoundingClientRect();
      var inView = rect.top < (window.innerHeight || 0) && rect.bottom > 0;
      if (inView) { run(); return; }

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
        setTimeout(run, 500);
      }
    } catch (_) { /* noop */ }
  }

  /* ── Rotating hero headline ── */
  var headlines = [
    "Your site is visible to humans and invisible to AI buyers.",
    "If AI can't quote you, you don't own the answer.",
    "Tomorrow's buyer is an AI agent. It can't use your site.",
    "Competitors aren't better — they're just easier for AI to recommend.",
    "Your content is perfectly written and ignored by AI systems.",
    "Digital invisibility is the new funnel leak. Yours is open.",
    "AI systems are shaping demand. They are skipping your brand.",
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
    initParticleNetwork();
    initCountups();
  }

  if (document.readyState === 'loading') {
    try { document.addEventListener('DOMContentLoaded', initAll, { once: true }); } catch (_) { document.addEventListener('DOMContentLoaded', initAll); }
  } else {
    initAll();
  }
})();
