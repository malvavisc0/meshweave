/* Prospects page functionality - mirrors Products UX (table + single form), with contacts panel + CSV export */
(function () {
  'use strict';

  var STATUS_VALUES = ['shortlisted', 'contacted', 'replied', 'won', 'lost'];

  // Form drawer helpers (similar to products.js)
  function openProspectForm(p) {
    var prfTitle = document.getElementById('prf-title'); if (prfTitle) prfTitle.textContent = p && p.id ? 'Edit Prospect' : 'New Prospect';
    setVal('prf-id', (p && p.id) || '');
    setVal('prf-domain', (p && p.domain) || '');
    setVal('prf-title-input', (p && p.title) || '');
    setVal('prf-url', (p && p.url) || '');
    setVal('prf-status', (p && p.status) || '');
    setVal('prf-tags', (p && p.tags) || '');
    setVal('prf-notes', (p && p.notes) || '');

    // when editing, domain is informational (upsert uses domain for create)
    try {
      var domEl = document.getElementById('prf-domain');
      if (domEl) domEl.disabled = !!(p && p.id);
    } catch (_){}

    // clear validation
    clearErrors();
    try { document.getElementById('prf-domain')?.focus(); } catch (_){}
  }

  function closeProspectForm() {
    setVal('prf-id', '');
    setVal('prf-domain', '');
    setVal('prf-title-input', '');
    setVal('prf-url', '');
    setVal('prf-status', '');
    setVal('prf-tags', '');
    setVal('prf-notes', '');

    try {
      var domEl = document.getElementById('prf-domain');
      if (domEl) domEl.disabled = false;
    } catch (_){}
    clearErrors();
  }

  function setVal(id, v) { var el = document.getElementById(id); if (el) el.value = v || ''; }
  function getVal(id) { return (document.getElementById(id)?.value || '').trim(); }

  function setErr(id, msg) {
    var input = document.getElementById(id.replace('-err', '')) || document.getElementById(id.replace('prf-', 'prf-').replace('-err', ''));
    var err = document.getElementById(id);
    if (err) err.textContent = msg || '';
    if (input) input.setAttribute('aria-invalid', msg ? 'true' : 'false');
  }

  function clearErrors() {
    ['prf-domain','prf-title-input','prf-url','prf-status','prf-tags','prf-notes'].forEach(function(id){
      var el = document.getElementById(id); if (el) el.setAttribute('aria-invalid','false');
    });
    ['prf-domain-err','prf-title-err','prf-url-err','prf-status-err','prf-tags-err','prf-notes-err','prf-global'].forEach(function(id){
      var el = document.getElementById(id); if (el) el.textContent = '';
    });
  }

  function statusBadge(s) {
    var v = String(s || '').toLowerCase();
    return v ? '<span class="badge">' + escapeHtml(v) + '</span>' : '-';
  }

  function statusSelectHtml(value) {
    var val = String(value || '').toLowerCase();
    var opts = STATUS_VALUES.map(function (s) {
      var sel = (s === val) ? ' selected' : '';
      return '<option value="' + escapeHtml(s) + '"' + sel + '>' + escapeHtml(s) + '</option>';
    }).join('');
    return '<select class="inp pr-edit-status">' + opts + '</select>';
  }

  function renderProspects(items) {
    var tb = document.getElementById('prospects-tbody');
    if (!tb) return;
    if (!items || !items.length) {
      tb.innerHTML = '<tr><td colspan="8"><em class="small">No prospects yet. Use the form below to add one.</em></td></tr>';
      return;
    }
    tb.innerHTML = '';
    items.forEach(function (p) {
      var tr = document.createElement('tr');
      tr.setAttribute('data-id', p.id || '');
      var urlCell = (safeUrl(p.url || '') && p.url)
        ? ('<a href="' + safeUrl(p.url) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(p.url) + '</a>')
        : (p.url ? escapeHtml(p.url) : '-');

      tr.innerHTML =
        '<td>' + escapeHtml(p.domain || '') + '</td>' +
        '<td>' + escapeHtml(p.title || '') + '</td>' +
        '<td>' + urlCell + '</td>' +
        '<td>' + statusBadge(p.status || '') + '</td>' +
        '<td><small>' + escapeHtml(p.tags || '') + '</small></td>' +
        '<td><small>' + escapeHtml(p.notes || '') + '</small></td>' +
        '<td><small>' + escapeHtml(p.created_at || '') + '</small></td>' +
        '<td>' +
          '<button class="btn btn-sm pr-edit">Edit</button> ' +
          '<button class="btn btn-sm pr-contacts">Contacts</button> ' +
          '<a class="btn btn-sm btn-link" href="/api/prospects/' + encodeURIComponent(p.id || '') + '/contacts.csv" download>CSV</a> ' +
          '<button class="btn btn-sm btn-danger-soft pr-del">Delete</button>' +
        '</td>';

      tb.appendChild(tr);

      // contacts row (collapsed)
      var trc = document.createElement('tr');
      trc.className = 'contacts-row hidden';
      trc.setAttribute('data-for', p.id || '');
      trc.innerHTML =
        '<td colspan="8">' +
          '<div class="contacts-panel">' +
            '<div class="small mb-1"><strong>Contacts</strong></div>' +
            '<div class="form-grid">' +
              '<label>Email<br><input type="email" class="inp c-email" placeholder="name@example.com"></label>' +
              '<label>Role/Title<br><input type="text" class="inp c-role" placeholder="e.g. Founder"></label>' +
              '<label>Source URL<br><input type="url" class="inp c-source" placeholder="https://..."></label>' +
              '<label>Tags<br><input type="text" class="inp c-tags" placeholder="comma,separated"></label>' +
            '</div>' +
            '<div class="mt-1 toolbar">' +
              '<button class="btn btn-sm btn-add-contact">Add Contact</button>' +
              '<button class="btn btn-sm btn-contacts-refresh">Refresh</button>' +
              '<span class="small c-status" role="status" aria-live="polite"></span>' +
            '</div>' +
            '<div class="contacts-list mt-1 small"></div>' +
          '</div>' +
        '</td>';
      tb.appendChild(trc);
    });

    attachTableHandlers();
  }

  function attachTableHandlers() {
    var tb = document.getElementById('prospects-tbody');
    if (!tb) return;
    tb.querySelectorAll('.pr-edit').forEach(function (b) {
      b.addEventListener('click', function (e) {
        var tr = e.target.closest('tr');
        if (!tr) return;
        var id = tr.getAttribute('data-id') || '';
        // open form populated by fetching row data from current cells
        var p = extractProspectFromRow(tr);
        p.id = id;
        openProspectForm(p);
      });
    });
    tb.querySelectorAll('.pr-del').forEach(function (b) {
      b.addEventListener('click', handleDeleteProspect);
    });
    tb.querySelectorAll('.pr-contacts').forEach(function (b) {
      b.addEventListener('click', toggleContactsRow);
    });
    tb.querySelectorAll('.btn-add-contact').forEach(function (b) {
      b.addEventListener('click', handleAddContact);
    });
    tb.querySelectorAll('.btn-contacts-refresh').forEach(function (b) {
      b.addEventListener('click', handleRefreshContacts);
    });
  }

  function extractProspectFromRow(tr) {
    var tds = tr.querySelectorAll('td');
    return {
      domain: (tds[0]?.textContent || '').trim(),
      title: (tds[1]?.textContent || '').trim(),
      url: (tds[2]?.textContent || '').trim(),
      status: (tds[3]?.textContent || '').trim(),
      tags: (tds[4]?.textContent || '').trim(),
      notes: (tds[5]?.textContent || '').trim()
    };
  }

  function handleDeleteProspect(e) {
    var tr = e.target.closest('tr');
    if (!tr) return;
    var id = tr.getAttribute('data-id') || '';
    if (!id) return;
    if (!confirm('Delete this prospect?')) return;
    apiJson('/api/prospects/' + encodeURIComponent(id), 'DELETE').then(function () {
      var trc = document.querySelector('tr.contacts-row[data-for="' + CSS.escape(id) + '"]');
      try { tr.remove(); } catch (_){}
      if (trc) { try { trc.remove(); } catch (_){ } }
    }).catch(function () {
      alert('Unable to delete prospect');
    });
  }

  function toggleContactsRow(e) {
    var tr = e.target.closest('tr');
    if (!tr) return;
    var id = tr.getAttribute('data-id') || '';
    if (!id) return;
    var trc = document.querySelector('tr.contacts-row[data-for="' + CSS.escape(id) + '"]');
    if (!trc) return;
    var wasHidden = trc.classList.contains('hidden');
    trc.classList.toggle('hidden');
    if (wasHidden) {
      loadContacts(id, trc);
    }
  }

  function handleAddContact(e) {
    var trc = e.target.closest('tr.contacts-row');
    if (!trc) return;
    var id = trc.getAttribute('data-for') || '';
    if (!id) return;
    var email = (trc.querySelector('.c-email')?.value || '').trim().toLowerCase();
    var role = (trc.querySelector('.c-role')?.value || '').trim();
    var source = (trc.querySelector('.c-source')?.value || '').trim();
    var tags = (trc.querySelector('.c-tags')?.value || '').trim();
    var statusEl = trc.querySelector('.c-status');
    if (statusEl) statusEl.textContent = 'Adding…';
    apiJson('/api/prospects/' + encodeURIComponent(id) + '/contacts', 'POST', {
      email: email, role_title: role || null, source_url: source || null, tags: tags || null
    }).then(function () {
      if (statusEl) statusEl.textContent = 'Added.';
      loadContacts(id, trc);
    }).catch(function (err) {
      if (statusEl) statusEl.textContent = 'Failed' + (err && err.status ? ' (' + err.status + ')' : '');
    });
  }

  function handleRefreshContacts(e) {
    var trc = e.target.closest('tr.contacts-row');
    if (!trc) return;
    var id = trc.getAttribute('data-for') || '';
    if (!id) return;
    loadContacts(id, trc);
  }

  function loadContacts(id, trc) {
    var statusEl = trc.querySelector('.c-status');
    if (statusEl) statusEl.textContent = 'Loading…';
    apiJson('/api/prospects/' + encodeURIComponent(id) + '/contacts', 'GET').then(function (res) {
      var items = (res && res.items) || [];
      renderContactsList(trc, items);
      if (statusEl) statusEl.textContent = '';
    }).catch(function () {
      if (statusEl) statusEl.textContent = 'Load failed';
    });
  }

  function renderContactsList(trc, items) {
    var list = trc.querySelector('.contacts-list');
    if (!list) return;
    if (!items || !items.length) {
      list.innerHTML = '<em class="muted">No contacts yet.</em>';
      return;
    }
    var html = '<ul class="unstyled">';
    items.forEach(function (c) {
      html += '<li data-contact-id="' + escapeHtml(c.id || '') + '">';
      html += '<strong>' + escapeHtml(c.email || '') + '</strong>';
      if (c.role_title) html += ' — ' + escapeHtml(c.role_title);
      if (c.tags) html += ' [' + escapeHtml(c.tags) + ']';
      if (c.source_url) html += ' <a href="' + escapeHtml(c.source_url) + '" target="_blank" rel="noopener">source</a>';
      if (c.social_url) html += ' <a href="' + escapeHtml(c.social_url) + '" target="_blank" rel="noopener">social</a>';
      html += ' <button class="btn btn-danger-soft btn-contact-del" data-id="' + escapeHtml(c.id || '') + '">Delete</button>';
      html += '</li>';
    });
    html += '</ul>';
    list.innerHTML = html;
    list.querySelectorAll('.btn-contact-del').forEach(function (b) {
      b.addEventListener('click', function (e) {
        var li = e.target.closest('li[data-contact-id]');
        var cid = li?.getAttribute('data-contact-id') || '';
        var id = trc.getAttribute('data-for') || '';
        if (!cid || !id) return;
        if (!confirm('Delete this contact?')) return;
        var statusEl = trc.querySelector('.c-status');
        if (statusEl) statusEl.textContent = 'Deleting…';
        apiJson('/api/prospects/' + encodeURIComponent(id) + '/contacts/' + encodeURIComponent(cid), 'DELETE').then(function () {
          try { li.remove(); } catch (_){}
          if (statusEl) statusEl.textContent = 'Deleted.';
        }).catch(function (err) {
          if (statusEl) statusEl.textContent = 'Delete failed' + (err && err.status ? ' (' + err.status + ')' : '');
        });
      });
    });
  }

  function loadProspects() {
    // reuse default limit, no filters for now (page UX parity with products)
    apiJson('/api/prospects?limit=50', 'GET').then(function (res) {
      renderProspects((res && res.items) || []);
    }).catch(function () {
      var tb = document.getElementById('prospects-tbody');
      if (tb) tb.innerHTML = '<tr><td colspan="8"><em class="small">Sign in to manage prospects.</em></td></tr>';
    });
  }

  function saveProspect() {
    clearErrors();
    var id = getVal('prf-id');
    var domain = getVal('prf-domain').toLowerCase();
    var title = getVal('prf-title-input');
    var url = getVal('prf-url');
    var status = getVal('prf-status');
    var tags = getVal('prf-tags');
    var notes = getVal('prf-notes');

    var globalEl = document.getElementById('prf-global');
    if (globalEl) globalEl.textContent = '';

    if (!id && !domain) {
      setErr('prf-domain-err', 'Domain is required.');
      try { document.getElementById('prf-domain')?.focus(); } catch (_){}
      if (globalEl) globalEl.textContent = 'Please correct the highlighted fields.';
      return;
    }

    var method, urlPath, body;
    if (id) {
      method = 'PATCH';
      urlPath = '/api/prospects/' + encodeURIComponent(id);
      body = { title: title || null, url: url || null, status: status || null, tags: tags || null, notes: notes || null };
    } else {
      method = 'POST';
      urlPath = '/api/prospects';
      body = { domain: domain, title: title || null, url: url || null, status: status || null, tags: tags || null, notes: notes || null };
    }

    apiJson(urlPath, method, body).then(function () {
      closeProspectForm();
      loadProspects();
    }).catch(function (e) {
      if (e && e.status === 400) {
        setErr('prf-domain-err', 'Invalid domain.');
        if (globalEl) globalEl.textContent = 'Invalid fields. Please check inputs.';
      } else if (e && e.status === 401) {
        if (globalEl) globalEl.textContent = 'Sign in to save prospects.';
      } else {
        if (globalEl) globalEl.textContent = 'Unable to save prospect.';
      }
    });
  }

  function openProspectFormFromJson(js) {
    try {
      var p = JSON.parse(decodeURIComponent(js));
      openProspectForm(p);
    } catch (_) {
      openProspectForm(null);
    }
  }

  // Safe URL helper: allow only http/https
  function safeUrl(u) {
    try {
      var s = String(u || '').trim();
      if (!s) return '';
      var l = s.toLowerCase();
      if (l.startsWith('http://') || l.startsWith('https://')) return s;
      return '';
    } catch (_) { return ''; }
  }

  function initProspectsPage() {
    try { trackEvent('prospects_view'); } catch (_){}
    loadProspects();
  }

  // Expose functions for onclick
  window.openProspectForm = openProspectForm;
  window.closeProspectForm = closeProspectForm;
  window.saveProspect = saveProspect;
  window.openProspectFormFromJson = openProspectFormFromJson;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initProspectsPage);
  } else {
    initProspectsPage();
  }
})();