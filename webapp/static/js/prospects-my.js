/* Prospects management on /my: list, filters, add, inline edit, contacts add, pagination (no bulk) */
(function () {
  'use strict';

  // Allowed status values (keep in sync with server defaults)
  var STATUS_VALUES = ['shortlisted', 'contacted', 'replied', 'won', 'lost'];

  // Local UI state (use dedicated query keys to avoid clashing with Jobs filters)
  var state = {
    status: '',
    q: '',
    tag: '',
    cursor: null,
    limit: 25
  };

  // Query string helpers (for p_* keys)
  function qsSet(params) {
    try {
      var usp = new URLSearchParams(window.location.search);
      Object.keys(params || {}).forEach(function (k) {
        var v = params[k];
        if (v == null || v === '') usp.delete(k);
        else usp.set(k, v);
      });
      var url = window.location.pathname + '?' + usp.toString();
      window.history.replaceState({}, '', url);
    } catch (_) {}
  }

  function readInitialState() {
    try {
      var usp = new URLSearchParams(window.location.search);
      state.status = usp.get('p_status') || '';
      state.q = usp.get('p_q') || '';
      state.tag = usp.get('p_tag') || '';
      state.cursor = usp.get('p_cursor') || null;

      var sel = document.getElementById('prospects-status'); if (sel) sel.value = state.status;
      var q = document.getElementById('prospects-q'); if (q) q.value = state.q;
      var tg = document.getElementById('prospects-tag'); if (tg) tg.value = state.tag;
    } catch (_) {}
  }

  // UI toggles for Next button and page message
  function setNextAvailability(hasNext) {
    var btn = document.getElementById('prospects-next');
    var msg = document.getElementById('prospects-page-msg');
    if (btn) {
      if (hasNext) {
        btn.classList.remove('hidden');
        btn.disabled = false;
        btn.setAttribute('aria-disabled', 'false');
      } else {
        btn.classList.add('hidden');
        btn.disabled = true;
        btn.setAttribute('aria-disabled', 'true');
      }
    }
    if (msg) msg.textContent = hasNext ? 'More available' : '';
  }

  function statusSelectHtml(value) {
    var val = String(value || '').toLowerCase();
    var opts = STATUS_VALUES.map(function (s) {
      var sel = (s === val) ? ' selected' : '';
      return '<option value="' + escapeHtml(s) + '"' + sel + '>' + escapeHtml(s) + '</option>';
    }).join('');
    return '<select class="inp inp-status">' + opts + '</select>';
  }

  // Render prospects table rows
  function renderProspects(items) {
    var tb = document.getElementById('prospects-tbody');
    if (!tb) return;

    if (!items || !items.length) {
      tb.innerHTML = '<tr><td colspan="8"><em class="small">No prospects found.</em></td></tr>';
      return;
    }

    tb.innerHTML = '';
    items.forEach(function (it) {
      var tr = document.createElement('tr');
      tr.setAttribute('data-id', it.id || '');
      tr.setAttribute('data-domain', it.domain || '');
      tr.setAttribute('data-created-at', it.created_at || '');
      // Basic row
      tr.innerHTML =
        '<td class="td-domain">' + escapeHtml(it.domain || '') + '</td>' +
        '<td class="td-title"><span class="val">' + escapeHtml(it.title || '') + '</span></td>' +
        '<td class="td-url"><span class="val">' + escapeHtml(it.url || '') + '</span></td>' +
        '<td class="td-status"><span class="val">' + escapeHtml(it.status || '') + '</span></td>' +
        '<td class="td-tags"><span class="val">' + escapeHtml(it.tags || '') + '</span></td>' +
        '<td class="td-notes"><span class="val">' + escapeHtml(it.notes || '') + '</span></td>' +
        '<td class="td-created"><small>' + escapeHtml(it.created_at || '') + '</small></td>' +
        '<td class="td-actions">' +
          '<button class="btn btn-edit">Edit</button>' +
          '<button class="btn btn-save hidden">Save</button>' +
          '<button class="btn btn-secondary btn-cancel hidden">Cancel</button>' +
          '<button class="btn btn-contacts">Contacts</button>' +
          '<a class="btn btn-link btn-export" href="/api/prospects/' + encodeURIComponent(it.id || '') + '/contacts.csv" download>Export CSV</a>' +
          '<button class="btn btn-danger-soft btn-delete-prospect">Delete</button>' +
        '</td>';

      tb.appendChild(tr);

      // Contacts row (collapsed by default)
      var trc = document.createElement('tr');
      trc.className = 'contacts-row hidden';
      trc.setAttribute('data-for', it.id || '');
      var colspan = 8;
      trc.innerHTML =
        '<td colspan="' + colspan + '">' +
          '<div class="contacts-panel">' +
            '<div class="small mb-1"><strong>Add contact</strong> (email required)</div>' +
            '<div class="grid-3">' +
              '<label class="small">Email<br><input type="email" class="inp c-email" placeholder="name@example.com"></label>' +
              '<label class="small">Role/Title<br><input type="text" class="inp c-role" placeholder="e.g. Founder"></label>' +
              '<label class="small">Source URL<br><input type="url" class="inp c-source" placeholder="https://..."></label>' +
            '</div>' +
            '<div class="grid-2 mt-1">' +
              '<label class="small">Tags<br><input type="text" class="inp c-tags" placeholder="comma,separated"></label>' +
              '<div class="small">&nbsp;<br><button class="btn btn-add-contact">Add Contact</button> ' +
                '<button class="btn btn-secondary btn-contacts-refresh">Refresh</button> ' +
                '<span class="small c-status" role="status" aria-live="polite"></span></div>' +
            '</div>' +
            '<div class="contacts-list mt-1 small"></div>' +
          '</div>' +
        '</td>';
      tb.appendChild(trc);
    });

    attachRowHandlers();
  }

  function enterEdit(tr) {
    if (!tr) return;
    tr.classList.add('editing');
    var titleTd = tr.querySelector('.td-title');
    var urlTd = tr.querySelector('.td-url');
    var statusTd = tr.querySelector('.td-status');
    var tagsTd = tr.querySelector('.td-tags');
    var notesTd = tr.querySelector('.td-notes');
    var actionsTd = tr.querySelector('.td-actions');

    var titleVal = (titleTd?.querySelector('.val')?.textContent) || '';
    var urlVal = (urlTd?.querySelector('.val')?.textContent) || '';
    var statusVal = (statusTd?.querySelector('.val')?.textContent) || '';
    var tagsVal = (tagsTd?.querySelector('.val')?.textContent) || '';
    var notesVal = (notesTd?.querySelector('.val')?.textContent) || '';

    if (titleTd) titleTd.innerHTML = '<input class="inp inp-title" type="text" value="' + escapeHtml(titleVal) + '">';
    if (urlTd) urlTd.innerHTML = '<input class="inp inp-url" type="url" value="' + escapeHtml(urlVal) + '">';
    if (statusTd) statusTd.innerHTML = statusSelectHtml(statusVal);
    if (tagsTd) tagsTd.innerHTML = '<input class="inp inp-tags" type="text" value="' + escapeHtml(tagsVal) + '" placeholder="comma,separated">';
    if (notesTd) notesTd.innerHTML = '<textarea class="inp inp-notes" rows="1">' + escapeHtml(notesVal) + '</textarea>';

    var btnEdit = actionsTd?.querySelector('.btn-edit');
    var btnSave = actionsTd?.querySelector('.btn-save');
    var btnCancel = actionsTd?.querySelector('.btn-cancel');
    var btnContacts = actionsTd?.querySelector('.btn-contacts');
    if (btnEdit) btnEdit.classList.add('hidden');
    if (btnContacts) btnContacts.classList.add('hidden');
    if (btnSave) btnSave.classList.remove('hidden');
    if (btnCancel) btnCancel.classList.remove('hidden');
  }

  function exitEdit(tr, restored) {
    if (!tr) return;
    tr.classList.remove('editing');
    var titleTd = tr.querySelector('.td-title');
    var urlTd = tr.querySelector('.td-url');
    var statusTd = tr.querySelector('.td-status');
    var tagsTd = tr.querySelector('.td-tags');
    var notesTd = tr.querySelector('.td-notes');
    var actionsTd = tr.querySelector('.td-actions');

    // restored is an object with the latest saved values; if absent, restore from inputs' original spans we stored in dataset? Simpler: refetch list row after save/cancel.
    // Here, if restored provided, render spans from restored; else, fallback to current input values
    function valFrom(selector, fallback) {
      var el = tr.querySelector(selector);
      if (!el) return fallback || '';
      var input = el.querySelector('input, textarea, select');
      if (input) return input.value || '';
      var span = el.querySelector('.val');
      return (span && span.textContent) || fallback || '';
    }

    var newVals = restored || {
      title: valFrom('.td-title', ''),
      url: valFrom('.td-url', ''),
      status: valFrom('.td-status', ''),
      tags: valFrom('.td-tags', ''),
      notes: valFrom('.td-notes', '')
    };

    if (titleTd) titleTd.innerHTML = '<span class="val">' + escapeHtml(newVals.title || '') + '</span>';
    if (urlTd) urlTd.innerHTML = '<span class="val">' + escapeHtml(newVals.url || '') + '</span>';
    if (statusTd) statusTd.innerHTML = '<span class="val">' + escapeHtml(newVals.status || '') + '</span>';
    if (tagsTd) tagsTd.innerHTML = '<span class="val">' + escapeHtml(newVals.tags || '') + '</span>';
    if (notesTd) notesTd.innerHTML = '<span class="val">' + escapeHtml(newVals.notes || '') + '</span>';

    var btnEdit = actionsTd?.querySelector('.btn-edit');
    var btnSave = actionsTd?.querySelector('.btn-save');
    var btnCancel = actionsTd?.querySelector('.btn-cancel');
    var btnContacts = actionsTd?.querySelector('.btn-contacts');
    if (btnEdit) btnEdit.classList.remove('hidden');
    if (btnContacts) btnContacts.classList.remove('hidden');
    if (btnSave) btnSave.classList.add('hidden');
    if (btnCancel) btnCancel.classList.add('hidden');
  }

  function handleEditClick(e) {
    var tr = e.target.closest('tr');
    if (!tr) return;
    enterEdit(tr);
  }

  function handleCancelClick(e) {
    var tr = e.target.closest('tr');
    if (!tr) return;
    exitEdit(tr, null);
  }

  function handleSaveClick(e) {
    var tr = e.target.closest('tr');
    if (!tr) return;
    var id = tr.getAttribute('data-id') || '';
    if (!id) return;

    var title = tr.querySelector('.inp-title')?.value || '';
    var url = tr.querySelector('.inp-url')?.value || '';
    var status = tr.querySelector('.inp-status')?.value || '';
    var tags = tr.querySelector('.inp-tags')?.value || '';
    var notes = tr.querySelector('.inp-notes')?.value || '';

    var payload = {
      title: title,
      url: url,
      status: status,
      tags: tags,
      notes: notes
    };

    var actionsTd = tr.querySelector('.td-actions');
    var saveBtn = actionsTd?.querySelector('.btn-save');
    if (saveBtn) saveBtn.disabled = true;

    apiJson('/api/prospects/' + encodeURIComponent(id), 'PATCH', payload).then(function (res) {
      var restored = {
        title: res.title || '',
        url: res.url || '',
        status: res.status || '',
        tags: res.tags || '',
        notes: res.notes || ''
      };
      exitEdit(tr, restored);
      var msg = document.getElementById('prospects-status-msg');
      if (msg) msg.textContent = 'Saved.';
      try { trackEvent('prospect_saved'); } catch (_) {}
    }).catch(function (err) {
      var msg = document.getElementById('prospects-status-msg');
      if (msg) msg.textContent = 'Save failed' + (err && err.status ? ' (' + err.status + ')' : '');
    }).finally(function () {
      if (saveBtn) saveBtn.disabled = false;
    });
  }

  function handleContactsToggle(e) {
    var tr = e.target.closest('tr');
    if (!tr) return;
    var id = tr.getAttribute('data-id') || '';
    if (!id) return;
    var trc = document.querySelector('tr.contacts-row[data-for="' + CSS.escape(id) + '"]');
    if (!trc) return;
    var wasHidden = trc.classList.contains('hidden');
    trc.classList.toggle('hidden');
    if (wasHidden) {
      // Load contacts when panel opens
      loadContacts(id, trc);
    }
  }

  function handleContactAddClick(e) {
    var trc = e.target.closest('tr.contacts-row');
    if (!trc) return;
    var forId = trc.getAttribute('data-for') || '';
    if (!forId) return;

    var email = trc.querySelector('.c-email')?.value || '';
    var role_title = trc.querySelector('.c-role')?.value || '';
    var source_url = trc.querySelector('.c-source')?.value || '';
    var tags = trc.querySelector('.c-tags')?.value || '';
    var statusEl = trc.querySelector('.c-status');

    if (statusEl) statusEl.textContent = 'Adding…';

    apiJson('/api/prospects/' + encodeURIComponent(forId) + '/contacts', 'POST', {
      email: email,
      role_title: role_title || null,
      source_url: source_url || null,
      tags: tags || null
    }).then(function () {
      if (statusEl) statusEl.textContent = 'Added.';
      try { trackEvent('contact_added'); } catch (_) {}
      // Refresh list after add
      loadContacts(forId, trc);
    }).catch(function (err) {
      if (statusEl) statusEl.textContent = 'Failed' + (err && err.status ? ' (' + err.status + ')' : '');
    });
  }

  function renderContactsList(trc, items) {
    var list = trc.querySelector('.contacts-list');
    var statusEl = trc.querySelector('.c-status');
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

    // attach delete handlers
    list.querySelectorAll('.btn-contact-del').forEach(function (b) {
      b.addEventListener('click', handleContactDeleteClick);
    });
    if (statusEl) statusEl.textContent = '';
  }

  function loadContacts(forId, trc) {
    var statusEl = trc.querySelector('.c-status');
    if (statusEl) statusEl.textContent = 'Loading…';
    apiJson('/api/prospects/' + encodeURIComponent(forId) + '/contacts', 'GET').then(function (res) {
      var items = (res && res.items) || [];
      renderContactsList(trc, items);
    }).catch(function () {
      if (statusEl) statusEl.textContent = 'Load failed';
    });
  }

  function handleContactDeleteClick(e) {
    var btn = e.target.closest('.btn-contact-del');
    if (!btn) return;
    var li = btn.closest('li[data-contact-id]');
    var trc = btn.closest('tr.contacts-row');
    if (!li || !trc) return;
    var cid = li.getAttribute('data-contact-id') || '';
    var forId = trc.getAttribute('data-for') || '';
    if (!cid || !forId) return;
    if (!confirm('Delete this contact?')) return;
    var statusEl = trc.querySelector('.c-status');
    if (statusEl) statusEl.textContent = 'Deleting…';
    apiJson('/api/prospects/' + encodeURIComponent(forId) + '/contacts/' + encodeURIComponent(cid), 'DELETE').then(function () {
      try { li.remove(); } catch (_){}
      if (statusEl) statusEl.textContent = 'Deleted.';
    }).catch(function (err) {
      if (statusEl) statusEl.textContent = 'Delete failed' + (err && err.status ? ' (' + err.status + ')' : '');
    });
  }

  function handleContactsRefresh(e) {
    var trc = e.target.closest('tr.contacts-row');
    if (!trc) return;
    var forId = trc.getAttribute('data-for') || '';
    if (!forId) return;
    loadContacts(forId, trc);
  }

  function deleteProspect(e) {
    var tr = e.target.closest('tr');
    if (!tr) return;
    var id = tr.getAttribute('data-id') || '';
    if (!id) return;
    if (!confirm('Delete this prospect?')) return;
    var msg = document.getElementById('prospects-status-msg');
    if (msg) msg.textContent = 'Deleting…';
    apiJson('/api/prospects/' + encodeURIComponent(id), 'DELETE').then(function () {
      // remove main row and its contacts row if present
      var trc = document.querySelector('tr.contacts-row[data-for="' + CSS.escape(id) + '"]');
      try { tr.remove(); } catch (_){}
      if (trc) { try { trc.remove(); } catch (_){ } }
      if (msg) msg.textContent = 'Deleted.';
      try { trackEvent('prospect_deleted'); } catch (_){}
    }).catch(function (err) {
      if (msg) msg.textContent = 'Delete failed' + (err && err.status ? ' (' + err.status + ')' : '');
    });
  }

  function attachRowHandlers() {
    var tb = document.getElementById('prospects-tbody');
    if (!tb) return;

    tb.querySelectorAll('.btn-edit').forEach(function (b) {
      b.addEventListener('click', handleEditClick);
    });
    tb.querySelectorAll('.btn-cancel').forEach(function (b) {
      b.addEventListener('click', handleCancelClick);
    });
    tb.querySelectorAll('.btn-save').forEach(function (b) {
      b.addEventListener('click', handleSaveClick);
    });
    tb.querySelectorAll('.btn-contacts').forEach(function (b) {
      b.addEventListener('click', handleContactsToggle);
    });
    tb.querySelectorAll('.btn-add-contact').forEach(function (b) {
      b.addEventListener('click', handleContactAddClick);
    });
    tb.querySelectorAll('.btn-contacts-refresh').forEach(function (b) {
      b.addEventListener('click', handleContactsRefresh);
    });
    tb.querySelectorAll('.btn-delete-prospect').forEach(function (b) {
      b.addEventListener('click', deleteProspect);
    });
  }

  // Fetch list with current filters
  function fetchProspects(next) {
    var msg = document.getElementById('prospects-status-msg');
    if (msg) msg.textContent = 'Loading…';

    var url = new URL('/api/prospects', window.location.origin);
    var st = state.status || '';
    var q = state.q || '';
    var tag = state.tag || '';
    var cursor = next === true ? (state.cursor || '') : ''; // reset cursor when applying filters

    if (st) url.searchParams.set('status', st);
    if (q) url.searchParams.set('q', q);
    if (tag) url.searchParams.set('tag', tag);
    if (cursor) url.searchParams.set('cursor', cursor);
    url.searchParams.set('limit', String(state.limit || 25));

    apiJson(url.pathname + '?' + url.searchParams.toString(), 'GET').then(function (res) {
      var items = (res && res.items) || [];
      renderProspects(items);
      var nc = (res && res.next_cursor) || null;
      state.cursor = nc;
      if (msg) msg.textContent = items.length + ' prospects';
      setNextAvailability(!!nc);
      qsSet({
        p_status: st || null,
        p_q: q || null,
        p_tag: tag || null,
        p_cursor: (next === true && nc) ? nc : null
      });
      try { trackEvent('prospects_loaded'); } catch (_) {}
    }).catch(function () {
      if (msg) msg.textContent = 'Unable to load prospects.';
      setNextAvailability(false);
    });
  }

  function applyProspectFilters() {
    var sel = document.getElementById('prospects-status');
    var q = document.getElementById('prospects-q');
    var tg = document.getElementById('prospects-tag');
    state.status = (sel && sel.value) || '';
    state.q = (q && q.value) || '';
    state.tag = (tg && tg.value) || '';
    state.cursor = null;
    fetchProspects(false);
  }

  function nextProspectsPage() {
    if (!state.cursor) return;
    var msg = document.getElementById('prospects-status-msg'); if (msg) msg.textContent = 'Loading…';
    var url = new URL('/api/prospects', window.location.origin);
    if (state.status) url.searchParams.set('status', state.status);
    if (state.q) url.searchParams.set('q', state.q);
    if (state.tag) url.searchParams.set('tag', state.tag);
    if (state.cursor) url.searchParams.set('cursor', state.cursor);
    url.searchParams.set('limit', String(state.limit || 25));

    apiJson(url.pathname + '?' + url.searchParams.toString(), 'GET').then(function (res) {
      var items = (res && res.items) || [];
      renderProspects(items);
      state.cursor = (res && res.next_cursor) || null;
      setNextAvailability(!!state.cursor);
      var msgEl = document.getElementById('prospects-status-msg'); if (msgEl) msgEl.textContent = items.length + ' prospects';
      qsSet({
        p_status: state.status || null,
        p_q: state.q || null,
        p_tag: state.tag || null,
        p_cursor: state.cursor || null
      });
    }).catch(function () {
      var msgEl = document.getElementById('prospects-status-msg'); if (msgEl) msgEl.textContent = 'Unable to load prospects.';
      setNextAvailability(false);
    });
  }

  function addProspect() {
    var dom = document.getElementById('prospect-domain');
    var url = document.getElementById('prospect-url');
    var title = document.getElementById('prospect-title');
    var statusSel = document.getElementById('prospect-status');
    var tags = document.getElementById('prospect-tags');
    var notes = document.getElementById('prospect-notes');
    var msg = document.getElementById('prospects-status-msg');

    var domain = (dom && dom.value || '').trim().toLowerCase();
    if (!domain) {
      if (msg) msg.textContent = 'Domain is required.';
      return;
    }
    var payload = {
      domain: domain,
      url: (url && url.value || '').trim() || null,
      title: (title && title.value || '').trim() || null,
      status: (statusSel && statusSel.value || '').trim() || null,
      tags: (tags && tags.value || '').trim() || null,
      notes: (notes && notes.value || '').trim() || null
    };

    var btn = document.getElementById('prospect-add');
    if (btn) btn.disabled = true;
    if (msg) msg.textContent = 'Adding…';

    apiJson('/api/prospects', 'POST', payload).then(function (_res) {
      // Reset add form inputs except status (keep last selection)
      try {
        if (dom) dom.value = '';
        if (url) url.value = '';
        if (title) title.value = '';
        if (tags) tags.value = '';
        if (notes) notes.value = '';
      } catch (_) {}
      if (msg) msg.textContent = 'Added.';
      try { trackEvent('prospect_added'); } catch (_) {}
      // Refresh list from first page to include new/updated row
      state.cursor = null;
      fetchProspects(false);
    }).catch(function (err) {
      if (msg) msg.textContent = 'Add failed' + (err && err.status ? ' (' + err.status + ')' : '');
    }).finally(function () {
      if (btn) btn.disabled = false;
    });
  }

  function initProspects() {
    // Wire filters and add form
    var applyBtn = document.getElementById('prospects-apply'); if (applyBtn) applyBtn.addEventListener('click', applyProspectFilters);
    var nextBtn = document.getElementById('prospects-next'); if (nextBtn) nextBtn.addEventListener('click', nextProspectsPage);
    var addBtn = document.getElementById('prospect-add'); if (addBtn) addBtn.addEventListener('click', addProspect);

    // Initialize filters from URL and perform initial fetch
    readInitialState();
    setNextAvailability(false);
    fetchProspects(false);
  }

  // Initialize when DOM is ready (only on /my page where scaffolding exists)
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initProspects);
  } else {
    initProspects();
  }
})();