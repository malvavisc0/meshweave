/* Products page functionality */
(function () {
    'use strict';

    var CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
    var _pfLastTrigger = null;

    function openProductForm(p) {
        _pfLastTrigger = document.activeElement || null;
        var pf = document.getElementById('product-form');
        if (!pf) return;
        var titleEl = document.getElementById('pf-title'); if (titleEl) titleEl.textContent = p ? 'Edit Product' : 'New Product';
        var idEl = document.getElementById('pf-id'); if (idEl) idEl.value = (p && p.id) || '';
        var nameEl = document.getElementById('pf-name'); if (nameEl) nameEl.value = (p && p.name) || '';
        var webEl = document.getElementById('pf-website'); if (webEl) webEl.value = (p && p.website) || '';
        var descEl = document.getElementById('pf-description'); if (descEl) descEl.value = (p && p.description) || '';
        var contactEl = document.getElementById('pf-contact'); if (contactEl) contactEl.value = (p && p.contact_info) || '';

        var nameErr = document.getElementById('pf-name-err');
        var webErr = document.getElementById('pf-website-err');
        var contactErr = document.getElementById('pf-contact-err');
        var descErr = document.getElementById('pf-description-err');
        var pfGlobal = document.getElementById('pf-global');

        if (nameEl) nameEl.setAttribute('aria-invalid', 'false');
        if (descEl) descEl.setAttribute('aria-invalid', 'false');
        if (webEl) webEl.setAttribute('aria-invalid', 'false');
        if (contactEl) contactEl.setAttribute('aria-invalid', 'false');

        if (nameErr) nameErr.textContent = '';
        if (webErr) webErr.textContent = '';
        if (contactErr) contactErr.textContent = '';
        if (descErr) descErr.textContent = '';
        if (pfGlobal) pfGlobal.textContent = '';

        try { nameEl && nameEl.focus(); } catch (_) { }
    }

    function closeProductForm() {
        var titleEl = document.getElementById('pf-title'); if (titleEl) titleEl.textContent = 'New Product';
        var idEl = document.getElementById('pf-id'); if (idEl) idEl.value = '';
        var nameEl = document.getElementById('pf-name'); if (nameEl) { nameEl.value = ''; nameEl.setAttribute('aria-invalid', 'false'); }
        var webEl = document.getElementById('pf-website'); if (webEl) { webEl.value = ''; webEl.setAttribute('aria-invalid', 'false'); }
        var descEl = document.getElementById('pf-description'); if (descEl) { descEl.value = ''; descEl.setAttribute('aria-invalid', 'false'); }
        var contactEl = document.getElementById('pf-contact'); if (contactEl) { contactEl.value = ''; contactEl.setAttribute('aria-invalid', 'false'); }

        var nameErr = document.getElementById('pf-name-err'); if (nameErr) nameErr.textContent = '';
        var webErr = document.getElementById('pf-website-err'); if (webErr) webErr.textContent = '';
        var contactErr = document.getElementById('pf-contact-err'); if (contactErr) contactErr.textContent = '';
        var descErr = document.getElementById('pf-description-err'); if (descErr) descErr.textContent = '';
        var pfGlobal = document.getElementById('pf-global'); if (pfGlobal) pfGlobal.textContent = '';

        try { nameEl && nameEl.focus(); } catch (_) { }
    }

    function renderProducts(items) {
        var tb = document.getElementById('products-tbody');
        if (!tb) return;
        if (!items || !items.length) {
            tb.innerHTML = '<tr><td colspan="5"><em class="small">No products yet. Use the form to add one.</em></td></tr>';
            return;
        }
        tb.innerHTML = '';
        items.forEach(function (p) {
            var tr = document.createElement('tr');
            var websiteCell = (safeUrl(p.website || '') && p.website)
                ? ('<a href="' + safeUrl(p.website) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(p.website) + '</a>')
                : '-';
            tr.innerHTML =
                '<td>' + escapeHtml(p.name || '') + '</td>' +
                '<td>' + websiteCell + '</td>' +
                '<td><small>' + escapeHtml(p.updated_at || '') + '</small></td>' +
                '<td><small>' + escapeHtml(p.contact_info || '-') + '</small></td>' +
                '<td><button class="btn btn-sm" onclick="openProductFormFromJson(\'' + encodeURIComponent(JSON.stringify(p)) + '\')">Edit</button></td>';
            tb.appendChild(tr);
        });
    }

    function loadProducts() {
        apiJson('/api/products', 'GET').then(function (res) {
            renderProducts((res && res.items) || []);
        }).catch(function () {
            var tb = document.getElementById('products-tbody');
            if (tb) {
                tb.innerHTML = '<tr><td colspan="5"><em class="small">Sign in to manage products.</em></td></tr>';
            }
        });
    }

    function saveProduct() {
        var id = (document.getElementById('pf-id')?.value || '').trim();
        var name = (document.getElementById('pf-name')?.value || '').trim();
        var website = (document.getElementById('pf-website')?.value || '').trim();
        var description = (document.getElementById('pf-description')?.value || '').trim();
        var contact = (document.getElementById('pf-contact')?.value || '').trim();

        var pfGlobal = document.getElementById('pf-global');
        var nameInput = document.getElementById('pf-name');
        var nameErr = document.getElementById('pf-name-err');
        var websiteInput = document.getElementById('pf-website');
        var websiteErr = document.getElementById('pf-website-err');
        var contactInput = document.getElementById('pf-contact');
        var contactErr = document.getElementById('pf-contact-err');
        var descInput = document.getElementById('pf-description');
        var descErr = document.getElementById('pf-description-err');
        if (pfGlobal) pfGlobal.textContent = '';
        if (nameErr) nameErr.textContent = '';
        if (websiteErr) websiteErr.textContent = '';
        if (contactErr) contactErr.textContent = '';
        if (descErr) descErr.textContent = '';
        if (nameInput) nameInput.setAttribute('aria-invalid', 'false');
        if (websiteInput) websiteInput.setAttribute('aria-invalid', 'false');
        if (contactInput) contactInput.setAttribute('aria-invalid', 'false');
        if (descInput) descInput.setAttribute('aria-invalid', 'false');

        var firstInvalid = null;
        if (!name) {
            if (nameErr) nameErr.textContent = 'Name is required.';
            if (nameInput) nameInput.setAttribute('aria-invalid', 'true');
            firstInvalid = firstInvalid || nameInput;
        }
        if (!website) {
            if (websiteErr) websiteErr.textContent = 'Website is required.';
            if (websiteInput) websiteInput.setAttribute('aria-invalid', 'true');
            firstInvalid = firstInvalid || websiteInput;
        }
        if (!description) {
            if (descErr) descErr.textContent = 'Description is required.';
            if (descInput) descInput.setAttribute('aria-invalid', 'true');
            firstInvalid = firstInvalid || descInput;
        }
        if (!contact) {
            if (contactErr) contactErr.textContent = 'Contact Info is required.';
            if (contactInput) contactInput.setAttribute('aria-invalid', 'true');
            firstInvalid = firstInvalid || contactInput;
        } else if (!/^[^<>\n]+ <[^<>\s@]+@[^<>\s@]+\.[^<>\s@]+>$/.test(contact)) {
            if (contactErr) contactErr.textContent = 'Contact Info must look like: Name <email@example.com>';
            if (contactInput) contactInput.setAttribute('aria-invalid', 'true');
            firstInvalid = firstInvalid || contactInput;
        }
        if (firstInvalid) {
            try { firstInvalid.focus(); } catch (_) { }
            if (pfGlobal) pfGlobal.textContent = 'Please correct the highlighted fields.';
            return;
        }

        var body = {
            name: name,
            website: website,
            description: description,
            contact_info: contact
        };

        var url = '/api/products' + (id ? ('/' + encodeURIComponent(id)) : '');
        var method = id ? 'PUT' : 'POST';

        apiJson(url, method, body).then(function () {
            closeProductForm(); // reset fields, keep form visible
            loadProducts();
            try { trackEvent(method === 'POST' ? 'product_create_success' : 'product_update_success'); } catch (_) { }
        }).catch(function (e) {
            var nameInput2 = document.getElementById('pf-name');
            var nameErr2 = document.getElementById('pf-name-err');
            var pfGlobal2 = document.getElementById('pf-global');
            if (e && e.status === 409) {
                if (nameErr2) nameErr2.textContent = (e.body && e.body.detail) ? String(e.body.detail).replace(/_/g, ' ') : 'Product with this name already exists.';
                if (nameInput2) { nameInput2.setAttribute('aria-invalid', 'true'); try { nameInput2.focus(); } catch (_) { } }
            } else if (e && e.status === 400) {
                if (pfGlobal2) pfGlobal2.textContent = 'Invalid fields. Please check required fields.';
            } else if (e && e.status === 401) {
                if (pfGlobal2) pfGlobal2.textContent = 'Sign in to save products.';
            } else {
                if (pfGlobal2) pfGlobal2.textContent = 'Unable to save product.';
            }
        });
    }

    function openProductFormFromJson(js) {
        try {
            var p = JSON.parse(decodeURIComponent(js));
            openProductForm(p);
        } catch (_) {
            openProductForm(null);
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

    // Drawer focus management
    function _productFormKeydown(e) {
        if (e.key === 'Escape') { try { closeProductForm(); } catch (_) { } }
        if (e.key === 'Tab') {
            var pf = document.getElementById('product-form');
            if (!pf) return;
            var focusable = pf.querySelectorAll('button, [href], input, textarea, select, [tabindex]:not([tabindex="-1"])');
            focusable = Array.prototype.slice.call(focusable).filter(function (el) { return !el.disabled && el.offsetParent !== null; });
            if (!focusable.length) return;
            var first = focusable[0], last = focusable[focusable.length - 1];
            if (e.shiftKey && document.activeElement === first) { last.focus(); e.preventDefault(); }
            else if (!e.shiftKey && document.activeElement === last) { first.focus(); e.preventDefault(); }
        }
    }

    function initProductsPage() {
        try { trackEvent('products_view'); } catch (_) { }
        loadProducts();
    }

    // Expose functions to global scope for HTML onclick attributes
    window.openProductForm = openProductForm;
    window.closeProductForm = closeProductForm;
    window.saveProduct = saveProduct;
    window.openProductFormFromJson = openProductFormFromJson;

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initProductsPage);
    } else {
        initProductsPage();
    }
})();