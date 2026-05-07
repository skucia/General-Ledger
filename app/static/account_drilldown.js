// Drill-down modal logic. Used by both Trial Balance and Balance Sheet.
//
// Reads the as-of date from the modal element's data-as-of attribute,
// fetches /reports/account-detail/{account_number}?as_of=..., and renders
// each line into the modal table.
//
// Columns (left to right):
//   Date | Reference | Description | DR | CR | Balance | Attachment
//
// Rows are built via createElement / textContent / setAttribute so user
// data (description, filename, etc.) is auto-escaped — no string-concat
// HTML around untrusted values.

(function () {
  const modal = document.getElementById('account-modal');
  if (!modal) return;
  // Two-attribute handover from each report's template:
  //   data-to-date: always set (the upper-bound date)
  //   data-from-date: set only by the P&L report (range mode)
  // The shared /reports/account-detail endpoint accepts either shape.
  const toDate = modal.dataset.toDate || '';
  const fromDate = modal.dataset.fromDate || '';
  const tbody = document.getElementById('modal-tbody');
  const titleEl = document.getElementById('modal-title');
  const subtitleEl = document.getElementById('modal-subtitle');
  const emptyEl = document.getElementById('modal-empty');
  const closeBtn = modal.querySelector('.modal-close');

  function buildCell(text, className) {
    const td = document.createElement('td');
    if (className) td.className = className;
    td.textContent = text == null ? '' : String(text);
    return td;
  }

  function buildAttachmentCell(line) {
    const td = document.createElement('td');
    td.className = 'att-col';
    if (line.attachment_filename) {
      const a = document.createElement('a');
      a.href = '/attachments/' + encodeURIComponent(line.transaction_id);
      a.target = '_blank';        // fallback when JS doesn't intercept
      a.rel = 'noopener';
      a.title = line.attachment_filename;  // tooltip = original filename
      a.className = 'attachment-link';
      a.textContent = '📎';
      // Stash the data the viewer needs.
      a.dataset.transactionId = line.transaction_id;
      a.dataset.filename = line.attachment_filename;
      a.addEventListener('click', handleAttachmentClick);
      td.appendChild(a);
    } else if (fileInput) {
      // No attachment yet — full users get an "+ Add" link that opens the
      // shared file picker for this transaction id. View-only users (no
      // fileInput rendered) just get an empty cell.
      const addLink = document.createElement('a');
      addLink.href = '#';
      addLink.className = 'add-attachment-link';
      addLink.textContent = '+ Add';
      addLink.title = 'Add attachment';
      addLink.dataset.transactionId = line.transaction_id;
      addLink.addEventListener('click', handleAddAttachmentClick);
      td.appendChild(addLink);
    }
    return td;
  }

  // Intercept plain left-clicks to open the in-app viewer modal. Cmd/Ctrl/
  // Shift/middle-click fall through to the link's normal behaviour, so the
  // user always has an escape hatch to open the file in a new tab.
  function handleAttachmentClick(e) {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
    e.preventDefault();
    const link = e.currentTarget;
    showAttachmentViewer(link.dataset.transactionId, link.dataset.filename);
  }

  // --- Attachment viewer modal --------------------------------------------

  const viewer = document.getElementById('attachment-viewer');
  const viewerBody = document.getElementById('viewer-body');
  const viewerTitle = document.getElementById('viewer-title');
  const viewerNewTab = document.getElementById('viewer-newtab');
  const viewerCloseBtn = viewer ? viewer.querySelector('.modal-close') : null;
  const viewerReplaceBtn = document.getElementById('viewer-replace');
  const viewerDeleteBtn = document.getElementById('viewer-delete');

  // Shared hidden file input. Present only for full users — view-only users
  // never see "+ Add" or Replace. dataset.mode tells the change-handler
  // which path to take ('add' or 'replace') so the same input serves both.
  const fileInput = document.getElementById('attachment-file-input');

  // Remembers the last-opened drill-down context so we can re-fetch and
  // re-render the modal after an attachment add / replace / delete.
  let currentAccount = null;  // { number, name, type_label } from last fetch

  async function refreshDrillDown() {
    if (!currentAccount) return;
    const params = new URLSearchParams();
    if (toDate) params.set('to_date', toDate);
    if (fromDate) params.set('from_date', fromDate);
    const url = '/reports/account-detail/' + encodeURIComponent(currentAccount.number)
              + '?' + params.toString();
    try {
      const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
      if (!res.ok) return;  // best-effort refresh; leave modal as-is on error
      renderDetail(await res.json());
    } catch (_) { /* ignore — refresh is best-effort */ }
  }

  // Lowercase extension list -> rendering strategy.
  const IMAGE_EXTS = new Set(['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp']);

  // PDF.js (loaded lazily on first PDF open). We use the legacy 3.x UMD build
  // from a pinned CDN URL — gives us a global `pdfjsLib` without ESM gymnastics.
  // Rendering each page to a <canvas> bypasses Chrome's "Download PDFs"
  // setting entirely (the browser never realises it's a PDF — it's just
  // JavaScript painting pixels).
  const PDFJS_VERSION = '3.11.174';
  const PDFJS_BASE = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@' + PDFJS_VERSION + '/build/';
  let pdfjsPromise = null;

  function loadPdfJs() {
    if (pdfjsPromise) return pdfjsPromise;
    pdfjsPromise = new Promise(function (resolve, reject) {
      const s = document.createElement('script');
      s.src = PDFJS_BASE + 'pdf.min.js';
      s.onload = function () {
        if (!window.pdfjsLib) return reject(new Error('pdfjsLib failed to load'));
        window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_BASE + 'pdf.worker.min.js';
        resolve(window.pdfjsLib);
      };
      s.onerror = function () { reject(new Error('Could not load PDF.js from CDN')); };
      document.head.appendChild(s);
    });
    return pdfjsPromise;
  }

  async function renderPdfIntoBody(url, container, fileLoadId) {
    container.replaceChildren();
    const loading = document.createElement('div');
    loading.className = 'viewer-fallback';
    loading.textContent = 'Loading PDF…';
    container.appendChild(loading);

    try {
      const pdfjsLib = await loadPdfJs();
      // Bail out if the user closed/changed the viewer while we were loading.
      if (container.dataset.loadId !== fileLoadId) return;

      const pdf = await pdfjsLib.getDocument(url).promise;
      if (container.dataset.loadId !== fileLoadId) return;

      container.replaceChildren();
      const wrap = document.createElement('div');
      wrap.className = 'pdf-pages';
      container.appendChild(wrap);

      // Render pages sequentially. Scale 1.5 keeps text readable on Retina
      // without blowing memory on huge documents.
      for (let i = 1; i <= pdf.numPages; i++) {
        if (container.dataset.loadId !== fileLoadId) return;
        const page = await pdf.getPage(i);
        const viewport = page.getViewport({ scale: 1.5 });
        const canvas = document.createElement('canvas');
        canvas.className = 'pdf-page';
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        wrap.appendChild(canvas);
        await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise;
      }
    } catch (err) {
      if (container.dataset.loadId !== fileLoadId) return;
      container.replaceChildren();
      const errDiv = document.createElement('div');
      errDiv.className = 'viewer-fallback';
      const p1 = document.createElement('p');
      p1.textContent = 'Could not render PDF: ' + (err && err.message ? err.message : err);
      const p2 = document.createElement('p');
      const dl = document.createElement('a');
      dl.href = url;
      dl.target = '_blank';
      dl.rel = 'noopener';
      dl.textContent = 'Open or download in new tab';
      p2.appendChild(dl);
      errDiv.appendChild(p1);
      errDiv.appendChild(p2);
      container.appendChild(errDiv);
    }
  }

  function fileExt(name) {
    if (!name) return '';
    const m = name.toLowerCase().match(/\.([a-z0-9]+)$/);
    return m ? m[1] : '';
  }

  function showAttachmentViewer(transactionId, filename) {
    if (!viewer) return;
    const url = '/attachments/' + encodeURIComponent(transactionId);

    // Stash the txn id so Replace / Delete know what they're acting on.
    viewer.dataset.transactionId = transactionId;
    viewerTitle.textContent = filename || 'Attachment';
    viewerNewTab.href = url;
    viewerNewTab.title = filename || '';
    viewerBody.replaceChildren();

    // Each open gets a unique load id so an in-flight async render can tell
    // it's been superseded (user closed, opened a different file, etc.) and
    // bail out without touching the DOM.
    const loadId = String(Date.now()) + ':' + Math.random();
    viewerBody.dataset.loadId = loadId;

    const ext = fileExt(filename);
    if (ext === 'pdf') {
      // Fire and forget — renderPdfIntoBody fills the body asynchronously.
      renderPdfIntoBody(url, viewerBody, loadId);
    } else if (IMAGE_EXTS.has(ext)) {
      const img = document.createElement('img');
      img.src = url;
      img.alt = filename || 'attachment';
      viewerBody.appendChild(img);
    } else {
      // Unknown type — fall back to an explanation + new-tab link.
      const wrap = document.createElement('div');
      wrap.className = 'viewer-fallback';
      const p1 = document.createElement('p');
      p1.textContent = 'In-app preview is not available for ' + (ext ? '.' + ext : 'this file type') + '.';
      const p2 = document.createElement('p');
      const dl = document.createElement('a');
      dl.href = url;
      dl.target = '_blank';
      dl.rel = 'noopener';
      dl.textContent = 'Open or download in new tab';
      p2.appendChild(dl);
      wrap.appendChild(p1);
      wrap.appendChild(p2);
      viewerBody.appendChild(wrap);
    }

    if (typeof viewer.showModal === 'function') {
      viewer.showModal();
    } else {
      viewer.setAttribute('open', '');
    }
  }

  if (viewerCloseBtn) {
    viewerCloseBtn.addEventListener('click', function () { viewer.close(); });
  }
  if (viewer) {
    // Click on the dialog backdrop closes the viewer.
    viewer.addEventListener('click', function (e) {
      if (e.target === viewer) viewer.close();
    });
    // When the viewer closes, drop the iframe/img/canvas content so the file
    // stops loading and frees memory. Also bumps the load id so any
    // in-flight PDF render bails out instead of mutating a closed dialog.
    viewer.addEventListener('close', function () {
      viewerBody.dataset.loadId = '__closed__';
      viewerBody.replaceChildren();
    });
  }

  // --- Add / Replace / Delete attachment ---------------------------------

  async function uploadAttachment(transactionId, file) {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch('/attachments/' + encodeURIComponent(transactionId), {
      method: 'POST',
      body: fd,
    });
    if (!res.ok) {
      let msg = 'Upload failed (HTTP ' + res.status + ').';
      try {
        const body = await res.json();
        if (body && body.detail) msg = body.detail;
      } catch (_) { /* non-JSON error body */ }
      throw new Error(msg);
    }
    return res.json();
  }

  async function deleteAttachmentRequest(transactionId) {
    const res = await fetch(
      '/attachments/' + encodeURIComponent(transactionId) + '/delete',
      { method: 'POST' }
    );
    if (!res.ok) {
      throw new Error('Delete failed (HTTP ' + res.status + ').');
    }
    return res.json();
  }

  function handleAddAttachmentClick(e) {
    e.preventDefault();
    if (!fileInput) return;
    const txnId = e.currentTarget.dataset.transactionId;
    fileInput.dataset.mode = 'add';
    fileInput.dataset.transactionId = txnId;
    fileInput.value = '';        // allow re-picking the same file later
    fileInput.click();
  }

  function handleViewerReplaceClick() {
    if (!fileInput || !viewer.dataset.transactionId) return;
    fileInput.dataset.mode = 'replace';
    fileInput.dataset.transactionId = viewer.dataset.transactionId;
    fileInput.value = '';
    fileInput.click();
  }

  async function handleFileInputChange() {
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    const txnId = fileInput.dataset.transactionId;
    const mode = fileInput.dataset.mode;
    try {
      await uploadAttachment(txnId, file);
    } catch (err) {
      alert(err.message);
      return;
    }
    if (mode === 'replace') viewer.close();
    await refreshDrillDown();
  }

  async function handleViewerDeleteClick() {
    const txnId = viewer.dataset.transactionId;
    if (!txnId) return;
    if (!confirm('Delete this attachment? The file cannot be recovered.')) return;
    try {
      await deleteAttachmentRequest(txnId);
    } catch (err) {
      alert(err.message);
      return;
    }
    viewer.close();
    await refreshDrillDown();
  }

  if (fileInput) {
    fileInput.addEventListener('change', handleFileInputChange);
  }
  if (viewerReplaceBtn) {
    viewerReplaceBtn.addEventListener('click', handleViewerReplaceClick);
  }
  if (viewerDeleteBtn) {
    viewerDeleteBtn.addEventListener('click', handleViewerDeleteClick);
  }

  // ----------------------------------------------------------------------

  function buildLineRow(line) {
    const tr = document.createElement('tr');
    tr.appendChild(buildCell(line.date_dmy));
    tr.appendChild(buildCell(line.transaction_reference, 'ref-cell'));
    tr.appendChild(buildCell(line.description));
    tr.appendChild(buildCell(line.dr, 'amt'));
    tr.appendChild(buildCell(line.cr, 'amt'));
    tr.appendChild(buildCell(line.balance, 'amt'));
    tr.appendChild(buildAttachmentCell(line));
    return tr;
  }

  function renderDetail(data) {
    currentAccount = data.account;  // remembered so attachment ops can refresh
    titleEl.textContent = 'Account ' + data.account.number + ' — ' + data.account.name;
    // Subtitle adapts to the mode the report is using:
    //   range mode (P&L)  -> "From dd/mm/yyyy to dd/mm/yyyy"
    //   up-to mode (TB/BS) -> "Up to dd/mm/yyyy"
    const dateLabel = data.from_dmy
      ? 'From ' + data.from_dmy + ' to ' + data.to_dmy
      : 'Up to ' + data.to_dmy;
    subtitleEl.textContent = dateLabel + ' · ' + data.account.type_label;
    tbody.innerHTML = '';
    if (!data.lines || data.lines.length === 0) {
      emptyEl.hidden = false;
      return;
    }
    emptyEl.hidden = true;
    const frag = document.createDocumentFragment();
    for (const line of data.lines) {
      frag.appendChild(buildLineRow(line));
    }
    tbody.appendChild(frag);
  }

  document.querySelectorAll('a.tb-drill').forEach(function (link) {
    link.addEventListener('click', async function (e) {
      e.preventDefault();
      const acct = link.dataset.account;
      if (!acct) return;
      const params = new URLSearchParams();
      if (toDate) params.set('to_date', toDate);
      if (fromDate) params.set('from_date', fromDate);
      const url = '/reports/account-detail/' + encodeURIComponent(acct)
                + '?' + params.toString();
      try {
        const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
        if (!res.ok) {
          alert('Could not load account detail (HTTP ' + res.status + ').');
          return;
        }
        const data = await res.json();
        renderDetail(data);
        if (typeof modal.showModal === 'function') {
          modal.showModal();
        } else {
          modal.setAttribute('open', '');
        }
        modal.querySelector('.modal-body').scrollTop = 0;
      } catch (err) {
        alert('Network error: ' + err.message);
      }
    });
  });

  closeBtn.addEventListener('click', function () { modal.close(); });

  // Click on the dialog backdrop (i.e. on the dialog itself rather than
  // its inner .dialog-content) closes the modal.
  modal.addEventListener('click', function (e) {
    if (e.target === modal) modal.close();
  });

  // Wire up any server-rendered .attachment-link elements (e.g. the 📎
  // links in the Journal Listing's per-transaction headers). Drill-down
  // popup links built dynamically by buildAttachmentCell get their handler
  // attached at creation time; this loop handles the static ones.
  document.querySelectorAll('a.attachment-link[data-transaction-id]').forEach(function (link) {
    link.addEventListener('click', handleAttachmentClick);
  });
})();
