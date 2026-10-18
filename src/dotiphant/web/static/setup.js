// Dotiphant browser setup (/setup). Each step posts JSON to /api/setup/... and moves on by
// loading the next step's page. The login step polls /api/setup/login while it waits.
(() => {
  'use strict';
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const root = $('#setup');
  const TOKEN = ($('meta[name="dotiphant-token"]') || {}).content || '';
  if (!root) return;

  async function post(url, body) {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Dotiphant-Token': TOKEN },
      body: JSON.stringify(body || {}),
      credentials: 'same-origin',
    });
    let data = {};
    try { data = await r.json(); } catch { /* empty */ }
    return { ok: r.ok, status: r.status, data };
  }

  // Screen readers hear progress through the page's live region (#announce in base.html).
  let sayTimer;
  function say(text) {
    const live = $('#announce');
    if (!live) return;
    live.textContent = '';
    clearTimeout(sayTimer);
    sayTimer = setTimeout(() => { live.textContent = text; }, 100);
  }

  // Shows (or clears) a form's error and ties it to the field it is about.
  let errorSeq = 0;
  function showError(form, text, field) {
    const el = $('.form-error', form);
    if (!el) return;
    if (!el.id) el.id = 'form-error-' + (++errorSeq);
    el.textContent = text || '';
    el.hidden = !text;
    for (const f of $$('[aria-invalid]', form)) { f.removeAttribute('aria-invalid'); f.removeAttribute('aria-describedby'); }
    if (text && field) {
      field.setAttribute('aria-invalid', 'true');
      field.setAttribute('aria-describedby', el.id);
      field.focus();
    }
  }

  // Shows (or clears) the message under one field; returns the field when it has a problem.
  function fieldError(field, text) {
    const el = document.getElementById(field.id + '-error');
    if (el) { el.textContent = text; el.hidden = !text; }
    if (text) {
      field.setAttribute('aria-invalid', 'true');
      if (el) field.setAttribute('aria-describedby', el.id);
      return field;
    }
    field.removeAttribute('aria-invalid');
    field.removeAttribute('aria-describedby');
    return null;
  }

  // While a form is saving: buttons off, and the submit button says what is happening.
  function busy(form, on) {
    for (const b of $$('button[type=submit],button:not([type])', form)) b.disabled = on;
    const submit = $('button[type=submit]', form);
    if (!submit) return;
    if (on) {
      submit.dataset.label = submit.textContent;
      submit.textContent = submit.dataset.busy || 'Saving...';
    } else if (submit.dataset.label) {
      submit.textContent = submit.dataset.label;
    }
  }

  const go = (step) => { location.href = '/setup?step=' + step; };

  // Show/hide toggles ("Replace them").
  for (const b of $$('[data-show]')) {
    b.addEventListener('click', () => {
      const show = document.getElementById(b.dataset.show);
      const hide = b.dataset.hide && document.getElementById(b.dataset.hide);
      if (show) show.hidden = false;
      if (hide) hide.hidden = true;
      const first = show && $('input', show);
      if (first) first.focus();
    });
  }

  // --- 1. credentials ----------------------------------------------------------------
  const creds = $('#creds-form');
  if (creds) {
    creds.addEventListener('submit', async (e) => {
      e.preventDefault();
      const id = creds.api_id.value.trim();
      const hash = creds.api_hash.value.trim();
      showError(creds, '');
      // Check both fields at once, so one submit shows every problem.
      const bad = [
        fieldError(creds.api_id, /^\d{3,12}$/.test(id) ? '' : 'The api_id is a number with 3 to 12 digits, like 1234567.'),
        fieldError(creds.api_hash, /^[0-9a-fA-F]{32}$/.test(hash) ? '' : 'The api_hash is 32 characters of 0-9 and a-f.'),
      ].filter(Boolean);
      if (bad.length) return bad[0].focus();
      busy(creds, true);
      const { ok, data } = await post('/api/setup/credentials', { api_id: id, api_hash: hash });
      creds.api_hash.value = '';
      busy(creds, false);
      if (!ok) return showError(creds, data.error || 'Could not save them.');
      go('login');
    });
  }

  // --- 2. login ----------------------------------------------------------------------
  const login = $('#login');
  if (login && root.dataset.loggedIn !== '1') {
    const img = $('#qr');
    const cancelRow = $('[data-cancel-row]', login);
    let shownQr = null;
    let timer = null;
    let phoneMode = false;
    let lastState = null;

    function panel(name) {
      for (const p of $$('[data-panel]', login)) p.hidden = p.dataset.panel !== name;
      // If the focused control was just hidden, keep keyboard focus in the login card.
      const a = document.activeElement;
      if (a && login.contains(a) && a.closest('[hidden]')) $('h1', login).focus();
    }

    // The QR image lives under /api/, which needs the page token header, so an <img src> cannot
    // load it directly. Fetch it with the header and show it as a data: URL (allowed by the CSP).
    const qrBar = $('[data-qr-bar]', login);
    const qrTime = $('[data-qr-time]', login);
    let qrTick = null;
    async function showQr(url, expiresAt) {
      try {
        const r = await fetch(url, { credentials: 'same-origin', headers: { 'X-Dotiphant-Token': TOKEN } });
        if (!r.ok || url !== shownQr) return;
        img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(await r.text());
      } catch { return; }
      clearInterval(qrTick);
      if (!expiresAt) return;
      const total = Math.max(1, expiresAt * 1000 - Date.now());
      const tick = () => {
        const left = Math.max(0, expiresAt * 1000 - Date.now());
        if (qrBar) qrBar.style.transform = `scaleX(${(left / total).toFixed(3)})`;
        if (qrTime) qrTime.textContent = left > 0 ? `New code in ${Math.ceil(left / 1000)} s` : 'Getting a new code...';
        if (left <= 0) clearInterval(qrTick);
      };
      tick();
      qrTick = setInterval(tick, 250);
    }

    function render(s) {
      const state = s.state || 'idle';
      login.dataset.state = state;
      if (state === 'idle' || state === 'cancelled') {
        panel(phoneMode ? 'phone' : 'starting');
      } else {
        panel(state);
      }
      cancelRow.hidden = !['waiting_scan', 'need_password', 'need_code', 'checking'].includes(state);
      if (state === 'waiting_scan' && s.qr_svg_url && s.qr_svg_url !== shownQr) {
        shownQr = s.qr_svg_url;
        showQr(s.qr_svg_url, s.expires_at);
      }
      for (const f of $$('form[data-answer]', login)) {
        const hint = $('.hint', f);
        if (hint) hint.textContent = s.hint || '';
      }
      if (state !== lastState) {
        const note = { waiting_scan: 'QR code ready. Scan it with the Telegram app, or log in with your phone number instead.',
          need_code: 'Telegram sent you a login code.', need_password: 'Type your two-step verification password.',
          checking: 'Checking...' }[state];
        if (note) say(note);
        const input = state === 'need_password' ? $('[name=password]', login)
          : state === 'need_code' ? $('[name=code]', login) : null;
        if (input) { input.value = ''; input.focus(); }
      }
      if (state === 'done') {
        $('[data-name]', login).textContent = s.name || 'you';
        if (state !== lastState) say(`Logged in as ${s.name || 'you'}. Opening the next step.`);
        setTimeout(() => go('archive'), 1500);
      }
      if (state === 'error') {
        $('[data-error]', login).textContent = s.error || 'The login failed.';
        $('[data-back-creds]', login).hidden = s.back_to !== 'credentials';
        // One primary action: "Try again", unless the fix is new credentials.
        $('[data-panel=error] [data-login-restart]', login).classList.toggle('primary', s.back_to !== 'credentials');
      }
      lastState = state;
    }

    async function poll() {
      clearTimeout(timer);
      let s;
      try {
        s = await (await fetch('/api/setup/login', { credentials: 'same-origin', headers: { 'X-Dotiphant-Token': TOKEN } })).json();
      } catch {
        timer = setTimeout(poll, 3000);
        return;
      }
      render(s);
      if (['starting', 'waiting_scan', 'need_password', 'need_code', 'checking'].includes(s.state)) {
        timer = setTimeout(poll, 1500);
      }
      return s;
    }

    async function start(method, extra) {
      phoneMode = method === 'phone';
      const { ok, data } = await post('/api/setup/login', { action: 'start', method, restart: true, ...(extra || {}) });
      if (!ok) {
        if (data.field === 'phone') return showError($('#phone-form'), data.error, $('[name=phone]', login));
        return render({ state: 'error', error: data.error, back_to: data.back_to });
      }
      render(data);
      poll();
    }

    for (const b of $$('[data-login-method]', login)) {
      b.addEventListener('click', async () => {
        if (b.dataset.loginMethod === 'phone') {
          phoneMode = true;
          await post('/api/setup/login', { action: 'cancel' });
          clearTimeout(timer);
          render({ state: 'idle' });
          $('[name=phone]', login).focus();
        } else {
          start('qr');
        }
      });
    }
    for (const b of $$('[data-login-restart]', login)) {
      b.addEventListener('click', async () => {
        await post('/api/setup/login', { action: 'cancel' });
        if (phoneMode) { clearTimeout(timer); render({ state: 'idle' }); } else start('qr');
      });
    }

    $('#phone-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = e.currentTarget;
      showError(f, '');
      const phone = f.phone.value.trim();
      if (!/^\+?[\d\s().-]{6,24}$/.test(phone)) return showError(f, 'Type the phone number with its country code, like +4915112345678.', f.phone);
      busy(f, true);
      await start('phone', { phone });
      busy(f, false);
    });

    for (const f of $$('form[data-answer]', login)) {
      f.addEventListener('submit', async (e) => {
        e.preventDefault();
        const kind = f.dataset.answer;
        const input = $(`[name=${kind}]`, f);
        const value = input.value;
        if (!value.trim()) return;
        input.value = '';
        busy(f, true);
        const { ok, data } = await post('/api/setup/login', { action: kind, [kind]: value });
        busy(f, false);
        if (!ok && data.state) render(data);
        else if (!ok) $('.hint', f).textContent = data.error || 'That did not work.';
        else render(data);
        poll();
      });
    }

    // Resume a running login after a reload; otherwise start with a QR code.
    (async () => {
      const s = await poll();
      if (!s) return;
      if (s.state === 'idle' || s.state === 'cancelled') start('qr');
      else if (s.method === 'phone') phoneMode = true;
    })();
  }

  // --- 3. what to archive --------------------------------------------------------------
  const archive = $('#archive-form');
  if (archive) {
    const days = archive.history_days;
    days.addEventListener('focus', () => { archive.querySelector('[name=span][value=days]').checked = true; });
    archive.addEventListener('submit', async (e) => {
      e.preventDefault();
      const all = archive.querySelector('[name=span][value=all]').checked;
      const n = Number(days.value);
      if (!all && (!Number.isInteger(n) || n < 1)) return showError(archive, 'Type a number of days, like 365, or choose all history.', days);
      busy(archive, true);
      const { ok, data } = await post('/api/setup/settings', {
        people: archive.people.checked, groups: archive.groups.checked, channels: archive.channels.checked,
        history_days: all ? 'all' : n,
        ai_access: (archive.querySelector('[name=ai_access]:checked') || {}).value || 'all',
      });
      busy(archive, false);
      if (!ok) return showError(archive, data.error || 'Could not save.');
      go('sync');
    });
  }

  // --- 4. first sync -----------------------------------------------------------------
  const sync = $('#sync');
  if (sync) {
    const box = $('[data-sync-progress]', sync);
    const bar = $('progress', sync);
    const text = $('[data-sync-text]', sync);
    const startBtn = $('[data-sync-start]', sync);
    const next = $('[data-sync-next]', sync);
    const lens = $('[data-sync-lens]', sync);
    let seen = false;
    let lastStage = null;

    const fmt = (n) => Number(n || 0).toLocaleString('en-US');
    const figChats = $('[data-sync-chats]', sync);
    const figOf = $('[data-sync-of]', sync);
    const figMsgs = $('[data-sync-msgs]', sync);
    const figs = (s) => {
      if (figChats) figChats.textContent = fmt(s.chats_done);
      if (figOf) figOf.textContent = s.chats_total ? ` of ${fmt(s.chats_total)}` : '';
      if (figMsgs) figMsgs.textContent = fmt(s.new_messages);
    };

    const label = (s) => {
      const parts = [];
      const stage = { connecting: 'Connecting to Telegram', inventory: 'Listing your chats', recent: 'Fetching new messages',
        history: 'Loading older history', media: 'Downloading media' }[s.stage] || 'Working';
      parts.push(s.state === 'waiting' ? 'Telegram asked us to slow down, waiting' : stage);
      if (s.chats_total) parts.push(`${s.chats_done || 0} of ${s.chats_total} chats`);
      if (s.new_messages) parts.push(`${Number(s.new_messages).toLocaleString()} messages`);
      return parts.join(', ') + '...';
    };

    async function watch() {
      let s;
      try { s = await (await fetch('/api/status', { credentials: 'same-origin', headers: { 'X-Dotiphant-Token': TOKEN } })).json(); } catch { return setTimeout(watch, 3000); }
      if (s.running) {
        seen = true;
        box.hidden = false;
        if (lens) { lens.hidden = false; lens.classList.add('walking'); }
        startBtn.hidden = true;
        next.textContent = 'Continue while it runs';
        next.classList.add('primary');
        if (s.chats_total) { bar.max = s.chats_total; bar.value = s.chats_done || 0; } else bar.removeAttribute('value');
        figs(s);
        text.textContent = label(s);
        // Announce stage changes only; the counts update too often to read out.
        if (s.stage !== lastStage) { lastStage = s.stage; say(label(s)); }
        return setTimeout(watch, 1500);
      }
      if (!seen) return;
      if (lens) lens.classList.remove('walking');
      const lj = s.last_job;
      if (lj && lj.ok === false) {
        text.textContent = 'The sync stopped: ' + (lj.message || 'see logs/sync.log in the Dotiphant folder.');
        say(text.textContent);
        startBtn.hidden = false;
        startBtn.textContent = 'Try again';
        return;
      }
      if (s.state === 'error') {
        text.textContent = 'The sync stopped: ' + (s.message || 'see logs/sync.log in the Dotiphant folder.');
        say(text.textContent);
        startBtn.hidden = false;
        startBtn.textContent = 'Try again';
        return;
      }
      bar.max = 1; bar.value = 1;
      figs(s);
      const n = s.new_messages || 0;
      const c = s.chats_done || 0;
      text.textContent = `Sync done: ${c.toLocaleString()} chat${c === 1 ? '' : 's'} checked, ${n.toLocaleString()} new message${n === 1 ? '' : 's'}. Ready to search.`;
      say(text.textContent);
      next.textContent = 'Continue';
    }

    startBtn.addEventListener('click', async () => {
      const refocus = document.activeElement === startBtn;
      startBtn.disabled = true;
      showError(sync, '');
      const { ok, data } = await post('/api/setup/sync');
      startBtn.disabled = false;
      if (!ok && !(data.status && data.status.running)) return showError(sync, data.message || data.error || 'Could not start the sync.');
      seen = true;
      box.hidden = false;
      if (lens) { lens.hidden = false; lens.classList.add('walking'); }
      startBtn.hidden = true;
      if (refocus) next.focus();
      text.textContent = 'Starting...';
      say('Sync started.');
      watch();
    });
    watch();
  }

  // --- 5. connect and background refresh ------------------------------------------------
  for (const b of $$('[data-connect]')) {
    b.addEventListener('click', async () => {
      b.disabled = true;
      const out = $(`[data-result="${b.dataset.connect}"]`);
      const { ok, data } = await post('/api/setup/connect', { name: b.dataset.connect });
      out.hidden = false;
      out.textContent = (ok && data.message) || data.error || 'Something went wrong.';
      out.classList.toggle('bad', !(ok && data.ok));
      say(out.textContent);
      if (ok && data.ok) b.textContent = 'Connected';
      else b.disabled = false;
    });
  }
  const sched = $('[data-schedule]');
  if (sched) {
    sched.addEventListener('change', async () => {
      sched.disabled = true;
      const out = $('[data-schedule-result]');
      const { ok, data } = await post('/api/setup/schedule', { on: sched.checked });
      sched.disabled = false;
      out.hidden = false;
      out.textContent = (ok && data.message) || data.error || 'Something went wrong.';
      say(out.textContent);
      if (!(ok && data.ok)) sched.checked = !sched.checked;
    });
  }
})();
