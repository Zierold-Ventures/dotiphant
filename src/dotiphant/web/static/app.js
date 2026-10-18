// Dotiphant viewer: small progressive enhancements. Pages work without this file; it adds
// favorites, filters, selection with Copy as Markdown, load older, refresh status and a lightbox.
(() => {
  'use strict';
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const root = document.documentElement;
  // Sent with every request to /api/ and every POST: the viewer refuses them without it.
  const TOKEN = ($('meta[name="dotiphant-token"]') || {}).content || '';

  // --- helpers -----------------------------------------------------------------------
  // Screen readers hear status changes through one live region that is always present.
  let sayTimer;
  function say(text) {
    const live = $('#announce');
    if (!live) return;
    live.textContent = '';
    clearTimeout(sayTimer);
    sayTimer = setTimeout(() => { live.textContent = text; }, 100);
  }

  let toastTimer;
  function toast(text) {
    const t = $('#toast');
    if (!t) return;
    say(text);
    clearTimeout(t.leaveTimer);
    t.classList.remove('done', 'leaving');
    t.textContent = text;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => leave(t, () => { t.classList.remove('done'); }), 3500 + text.length * 40);
  }

  // Hide an element after its exit animation (see .leaving in app.css). Without motion the
  // animation is off, so the short wait just passes.
  function leave(el, then) {
    el.classList.add('leaving');
    clearTimeout(el.leaveTimer);
    el.leaveTimer = setTimeout(() => {
      el.hidden = true;
      el.classList.remove('leaving');
      if (then) then();
    }, 200);
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.append(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
  }

  async function postJSON(url, body) {
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

  // --- favorites ---------------------------------------------------------------------
  document.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-star]');
    if (!b) return;
    e.preventDefault();
    b.classList.remove('pop');
    void b.offsetWidth; // restart the pop on a quick second click
    b.classList.add('pop');
    const { ok, data } = await postJSON('/api/favorite', { chat_id: b.dataset.star });
    if (!ok) return toast(data.error || 'Could not save the favorite.');
    if (b.dataset.reload) {
      // Keep the keyboard focus on this star across the reload.
      try { sessionStorage.setItem('dotiphant-focus', b.dataset.star); } catch { /* private mode */ }
      return location.reload();
    }
    b.classList.toggle('on', data.starred);
    b.title = data.starred ? 'Remove from favorites' : 'Add to favorites';
    b.setAttribute('aria-pressed', String(data.starred));
  });
  try {
    const id = sessionStorage.getItem('dotiphant-focus');
    sessionStorage.removeItem('dotiphant-focus');
    const star = id && $$('[data-star]').find((x) => x.dataset.star === id);
    if (star) star.focus();
  } catch { /* private mode */ }

  // --- AI access: the "Visible to AI apps" switch and the mode on the Connect AI page ----
  document.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-ai-chat]');
    if (!b || b.disabled) return;
    e.preventDefault();
    const want = b.getAttribute('aria-checked') !== 'true';
    b.disabled = true;
    const { ok, data } = await postJSON('/api/ai-access', { chat_id: b.dataset.aiChat, visible: want });
    b.disabled = false;
    b.focus();
    if (!ok) return toast(data.error || 'Could not change AI access.');
    b.setAttribute('aria-checked', String(data.visible));
    b.title = data.visible ? 'AI apps can read this chat. Click to hide it from them.'
      : 'Hidden from AI apps. Click to let them read this chat.';
    toast(data.visible ? 'AI apps can read this chat now.' : 'Hidden from AI apps. They can no longer read this chat.');
  });
  for (const b of $$('[data-ai-mode]')) {
    b.addEventListener('click', async () => {
      const { ok, data } = await postJSON('/api/ai-mode', { mode: b.dataset.aiMode });
      if (!ok) return toast(data.error || 'Could not change AI access.');
      const note = data.mode === 'all' ? 'AI apps can read all chats except hidden ones.'
        : 'AI apps can read only the chats you choose.';
      try { sessionStorage.setItem('dotiphant-note', note); } catch { /* private mode */ }
      location.reload();
    });
  }

  // --- home: filter chats by name and kind --------------------------------------------
  const filter = $('#chat-filter');
  if (filter) {
    let kind = '';
    let sayFilter;
    const apply = () => {
      const s = filter.value.trim().toLowerCase();
      let shown = 0;
      for (const ul of $$('.chats')) {
        let inList = 0;
        for (const li of $$('li', ul)) {
          const hit = (!s || li.dataset.title.includes(s)) && (!kind || li.dataset.kind === kind);
          li.hidden = !hit;
          if (hit) inList++;
        }
        ul.hidden = !inList;
        const head = ul.previousElementSibling;
        if (head && head.matches('h2.section')) head.hidden = !inList;
        shown += inList;
      }
      const none = $('#no-match');
      if (none) none.hidden = shown > 0;
      clearTimeout(sayFilter);
      sayFilter = setTimeout(() => say(shown ? `${shown} chat${shown === 1 ? '' : 's'} shown.` : 'No chats match.'), 600);
    };
    filter.addEventListener('input', apply);
    for (const b of $$('[data-kind-filter]')) {
      b.addEventListener('click', () => {
        kind = b.dataset.kindFilter;
        for (const o of $$('[data-kind-filter]')) {
          o.classList.toggle('on', o === b);
          o.setAttribute('aria-pressed', String(o === b));
        }
        apply();
      });
    }
  }

  // --- keyboard: "/" focuses the page's search, arrows move through lists -------------
  const typing = (el) => el && (el.isContentEditable || /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName));
  addEventListener('keydown', (e) => {
    if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey || typing(document.activeElement)) return;
    const field = $('[aria-keyshortcuts="/"]');
    if (!field) return;
    e.preventDefault();
    field.focus();
    field.select();
  });
  // Home rows and search hits: ArrowDown and ArrowUp move between links, Enter opens one.
  const roveLinks = () => $$('[data-rove] > li > a').filter((a) => !a.closest('[hidden]'));
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
    const links = roveLinks();
    if (!links.length) return;
    const here = document.activeElement;
    const i = links.indexOf(here && here.closest('[data-rove] > li > a'));
    const fromFilter = here && here.id === 'chat-filter';
    if (i < 0 && !fromFilter) return;
    let next;
    if (i < 0) next = e.key === 'ArrowDown' ? links[0] : null;
    else next = links[i + (e.key === 'ArrowDown' ? 1 : -1)];
    if (!next && i === 0 && e.key === 'ArrowUp' && filter) next = filter;
    if (!next) return;
    e.preventDefault();
    next.focus();
  });

  // --- forms that submit on change ----------------------------------------------------
  // Typing a date or arrowing through a list fires change before the person is done, so
  // changes made with the keyboard wait for Enter.
  for (const el of $$('[data-autosubmit]')) {
    let typedAt = 0;
    const submit = () => { if (el.value || el.tagName === 'SELECT') el.form.submit(); };
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); submit(); }
      else if (e.key !== 'Tab') typedAt = Date.now();
    });
    el.addEventListener('change', () => { if (Date.now() - typedAt > 1000) submit(); });
  }

  // --- copy buttons ------------------------------------------------------------------
  document.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-copy-target],[data-copy-url]');
    if (!b) return;
    e.preventDefault();
    let text;
    if (b.dataset.copyTarget) {
      const src = document.getElementById(b.dataset.copyTarget);
      text = src ? src.textContent : '';
    } else {
      const r = await fetch(b.dataset.copyUrl, { credentials: 'same-origin', headers: { 'X-Dotiphant-Token': TOKEN } });
      if (!r.ok) return toast('Could not load the messages.');
      text = await r.text();
    }
    await copyText(text);
    say('Copied to the clipboard.');
    const label = b.textContent;
    b.textContent = 'Copied';
    setTimeout(() => { b.textContent = label; }, 1500);
  });

  // --- lightbox ----------------------------------------------------------------------
  const box = $('#lightbox');
  let opener = null;
  const closeBox = () => {
    box.hidden = true;
    $('img', box).removeAttribute('src');
    if (opener) opener.focus();
    opener = null;
  };
  document.addEventListener('click', (e) => {
    const a = e.target.closest('[data-zoom]');
    if (!a || !box || e.metaKey || e.ctrlKey || document.body.classList.contains('picking')) return;
    e.preventDefault();
    const img = $('img', box);
    img.src = a.getAttribute('href');
    img.alt = ($('img', a) || {}).alt || '';
    box.hidden = false;
    opener = a;
    box.focus();
  });
  if (box) {
    box.addEventListener('click', closeBox);
    box.addEventListener('keydown', (e) => {
      if (e.key === 'Tab') e.preventDefault();
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); closeBox(); }
    });
  }
  addEventListener('keydown', (e) => { if (e.key === 'Escape' && box && !box.hidden) closeBox(); });

  // --- background jobs: refresh and media download -----------------------------------
  const job = $('#job');
  let polling = false;
  let sawRunning = job && job.dataset.running === '1';
  // Report only the job this page started (matched by id), or a sync seen running here
  // (matched by its started_at). Results of earlier runs never show up as this job's result.
  let myJob = null;
  let seenStart = null;

  function jobText(s) {
    if (s.job === 'media') return 'Downloading attachments...';
    const parts = ['Refreshing'];
    if (s.chats_total) parts.push(`${s.chats_done || 0}/${s.chats_total}`);
    if (s.chat) parts.push(s.chat);
    else if (s.stage) parts.push(String(s.stage).replace(/_/g, ' '));
    return parts.join(' ') + '...';
  }

  // "Ready to search." is the one sign-off, and it follows only a sync that brought new messages.
  function syncNote(n, chats) {
    if (!n) return 'All caught up. No new messages.';
    const checked = chats ? `${chats.toLocaleString()} chat${chats === 1 ? '' : 's'} checked, ` : '';
    return `Sync done: ${checked}${n.toLocaleString()} new message${n === 1 ? '' : 's'}. Ready to search.`;
  }

  // A successful sync shows as a toast with the lens mark (see .toast.done in app.css).
  function doneToast(text) {
    const t = $('#toast');
    toast(text);
    if (t) t.classList.add('done');
  }

  function setButtons(disabled) {
    for (const b of $$('[data-refresh],[data-media]')) b.disabled = disabled;
  }

  async function poll() {
    if (!job) return;
    let s;
    try {
      s = await (await fetch('/api/status', { credentials: 'same-origin', headers: { 'X-Dotiphant-Token': TOKEN } })).json();
    } catch {
      return setTimeout(poll, 4000);
    }
    setButtons(!!s.running);
    if (s.running) {
      sawRunning = true;
      if (!myJob && s.started_at) seenStart = s.started_at;
      polling = true;
      job.className = 'job busy';
      job.textContent = jobText(s);
      return setTimeout(poll, 2000);
    }
    polling = false;
    job.className = 'job';
    job.title = '';
    if (!sawRunning) return;
    const lj = s.last_job;
    const mine = !!(myJob && lj && lj.id === myJob);
    if (mine && !lj.done) { polling = true; return setTimeout(poll, 1000); }
    sawRunning = false;
    let kind = 'sync', failed = false, why = '', n = 0;
    if (mine) {
      kind = lj.kind;
      failed = lj.ok === false;
      why = lj.message || '';
      n = lj.new_messages || 0;
    } else if (seenStart && s.started_at === seenStart) {
      failed = s.state === 'error';
      why = s.message || '';
      n = s.new_messages || 0;
    }
    myJob = null;
    seenStart = null;
    if (failed) {
      why = why || 'Details are in logs/sync.log in the Dotiphant folder.';
      const what = kind === 'media' ? 'Download failed.' : 'Refresh failed.';
      job.classList.add('error');
      job.textContent = what;
      job.title = why;
      toast(`${what} ${why}`);
      return;
    }
    let note;
    if (kind === 'media') note = 'Download finished.';
    else note = syncNote(n, s.chats_done || 0) + (mine && lj.partial && why ? ` ${why}` : '');
    if (!document.body.classList.contains('picking')) {
      try { sessionStorage.setItem('dotiphant-note', note); } catch { /* private mode */ }
      return location.reload();
    }
    if (kind !== 'media') doneToast(note);
    say(note);
    job.textContent = note + ' ';
    const a = document.createElement('a');
    a.href = '';
    a.textContent = 'Reload';
    job.append(a);
  }

  async function startJob(url, body) {
    setButtons(true);
    const { ok, data } = await postJSON(url, body);
    if (!ok) toast(data.message || data.error || 'Could not start the refresh.');
    if (data.demo) return setButtons(false);
    if (ok) say(url.endsWith('/media') ? 'Downloading attachments.' : 'Refreshing.');
    myJob = ok && data.job ? data.job.id : null;
    sawRunning = true;
    if (!polling) poll();
  }

  for (const b of $$('[data-refresh]')) {
    b.addEventListener('click', () => startJob('/api/refresh', { chat_ids: b.dataset.refresh ? [b.dataset.refresh] : [] }));
  }
  for (const b of $$('[data-media]')) {
    // A file's own button carries its message id and fetches just that file.
    b.addEventListener('click', () => startJob('/api/media', b.dataset.mediaMsg
      ? { chat_id: b.dataset.media, message_id: b.dataset.mediaMsg }
      : { chat_id: b.dataset.media }));
  }
  // "Download automatically": saved per chat. Turning it on also fetches what is missing now.
  for (const box of $$('[data-auto-download]')) {
    box.addEventListener('change', async () => {
      const on = box.checked;
      const { ok, data } = await postJSON('/api/auto-download', { chat_id: box.dataset.autoDownload, on });
      if (!ok) { box.checked = !on; return toast(data.error || 'Could not save that.'); }
      if (on && $('[data-media]:not([data-media-msg])')) return startJob('/api/media', { chat_id: box.dataset.autoDownload });
      toast(on ? 'New attachments in this chat will download with every sync.' : 'Attachments in this chat download only when you ask.');
    });
  }
  if (job) {
    let note = null;
    try { note = sessionStorage.getItem('dotiphant-note'); sessionStorage.removeItem('dotiphant-note'); } catch { /* ignore */ }
    if (note && job.dataset.running !== '1') {
      if (note.startsWith('Sync done') || note.startsWith('All caught up')) {
        doneToast(note);
      } else {
        job.textContent = note;
        say(note);
        setTimeout(() => { if (!polling && job.textContent === note) job.textContent = ''; }, 8000);
      }
    }
    if (job.dataset.running === '1') poll();
  }

  // --- chat page ---------------------------------------------------------------------
  const main = $('main[data-chat]');
  const list = $('#messages');
  if (!main || !list) return;

  // Selection and Copy as Markdown.
  const bar = $('.pickbar');
  const count = bar && $('[data-count]', bar);
  let lastPicked = null;
  const msgs = () => $$('article.msg', list);
  const sync = () => {
    const sel = msgs().filter((a) => a.classList.contains('sel'));
    for (const a of msgs()) a.querySelector('.pick').checked = a.classList.contains('sel');
    if (!bar) return;
    const shown = !bar.hidden && !bar.classList.contains('leaving');
    const was = shown ? parseInt(count.textContent, 10) || 0 : 0;
    if (sel.length) {
      clearTimeout(bar.leaveTimer);
      bar.classList.remove('leaving');
      bar.hidden = false;
    } else if (shown) {
      leave(bar);
    }
    document.body.classList.toggle('picking', sel.length > 0);
    count.textContent = `${sel.length} selected`;
    if (sel.length !== was) {
      say(sel.length ? `${sel.length} selected. Copy as Markdown is at the end of the page.` : 'Selection cleared.');
    }
  };
  const clear = () => {
    // Focus would vanish with the bar, so move it back to the last picked message.
    const back = bar && bar.contains(document.activeElement) && lastPicked && lastPicked.isConnected && lastPicked;
    for (const a of msgs()) a.classList.remove('sel');
    lastPicked = null;
    sync();
    if (back) back.querySelector('.pick').focus();
  };
  const pick = (card, on, shift) => {
    const all = msgs();
    const i = all.indexOf(card);
    const j = lastPicked ? all.indexOf(lastPicked) : -1;
    if (shift && j >= 0) {
      for (let k = Math.min(i, j); k <= Math.max(i, j); k++) all[k].classList.toggle('sel', on);
    } else {
      card.classList.toggle('sel', on);
    }
    lastPicked = card;
    sync();
  };
  const plain = (e) => !e.target.closest('a,button,input,video,audio,select');
  const picking = () => document.body.classList.contains('picking');

  list.addEventListener('mousedown', (e) => {
    if (e.shiftKey && (picking() || e.target.closest('.pick'))) e.preventDefault();
  });
  list.addEventListener('click', (e) => {
    const boxEl = e.target.closest('.pick');
    if (boxEl) return pick(boxEl.closest('article'), boxEl.checked, e.shiftKey);
    const card = e.target.closest('article.msg');
    if (card && picking() && plain(e) && !String(getSelection())) {
      return pick(card, !card.classList.contains('sel'), e.shiftKey);
    }
    const day = e.target.closest('.daypill');
    if (day) {
      const ms = [];
      for (let n = day.parentElement.nextElementSibling; n && !n.classList.contains('day'); n = n.nextElementSibling) {
        if (n.matches('article.msg')) ms.push(n);
      }
      const on = !ms.every((m) => m.classList.contains('sel'));
      for (const m of ms) m.classList.toggle('sel', on);
      if (ms.length) lastPicked = ms[ms.length - 1];
      sync();
    }
  });
  addEventListener('keydown', (e) => { if (e.key === 'Escape' && picking()) clear(); });

  if (bar) {
    bar.addEventListener('click', async (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.act === 'clear') return clear();
      // Send ids as the strings they are: WhatsApp chat ids are larger than a JavaScript
      // number holds exactly, and a rounded id finds no message.
      const sel = msgs().filter((a) => a.classList.contains('sel'))
        .map((a) => [a.dataset.chat, a.dataset.id]);
      const { ok, data } = await postJSON('/api/markdown', { chat_id: main.dataset.chat, messages: sel });
      if (!ok) return toast(data.error || 'Could not build the Markdown.');
      if (!data.count || data.count !== sel.length) {
        return toast('Could not find the selected messages. Reload the page and try again.');
      }
      await copyText(data.markdown);
      say(`Copied ${sel.length} message${sel.length === 1 ? '' : 's'} as Markdown.`);
      const label = b.textContent;
      b.textContent = 'Copied';
      setTimeout(() => { b.textContent = label; }, 1500);
    });
  }

  // Load older messages in place, keeping the scroll position.
  list.addEventListener('click', async (e) => {
    const a = e.target.closest('[data-older]');
    if (!a || e.metaKey || e.ctrlKey) return;
    e.preventDefault();
    a.textContent = 'Loading...';
    const url = a.getAttribute('href') + (a.getAttribute('href').includes('?') ? '&' : '?') + 'partial=1';
    const r = await fetch(url, { credentials: 'same-origin', headers: { 'X-Dotiphant-Token': TOKEN } });
    if (!r.ok) { a.textContent = 'Load older messages'; return toast('Could not load older messages.'); }
    const tpl = document.createElement('template');
    tpl.innerHTML = await r.text();
    const frag = tpl.content;
    for (const d of $$('.day', frag)) {
      const dup = document.getElementById(d.id);
      if (dup) dup.remove();
    }
    const added = $$('article.msg', frag).length;
    const first = frag.firstElementChild;
    const hadFocus = document.activeElement === a;
    const before = root.scrollHeight;
    a.remove();
    list.prepend(frag);
    scrollBy(0, root.scrollHeight - before);
    // Keep keyboard users in place: focus the new "Load older" link, or the first loaded item.
    if (hadFocus && first) {
      if (!first.matches('a')) first.tabIndex = -1;
      first.focus({ preventScroll: true });
    }
    say(`Loaded ${added} older message${added === 1 ? '' : 's'}.`);
    sync();
    railDays = $$('.day', list);
  });

  // Start at the newest message, or at the linked one.
  const target = (list.dataset.at && document.getElementById(list.dataset.at)) ||
    (location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1))));
  if (target) {
    target.classList.add('target');
    target.scrollIntoView({ block: target.classList.contains('day') ? 'start' : 'center' });
  } else if (main.hasAttribute('data-latest')) {
    const bottom = () => scrollTo(0, root.scrollHeight);
    bottom();
    addEventListener('load', bottom);
  }

  // Keep the date rail in step with scrolling.
  let railDays = $$('.day', list);
  const links = new Map($$('.rail .days a').map((a) => [a.dataset.day, a]));
  if (!links.size) return;
  for (const a of $$('.rail a')) {
    a.addEventListener('click', (e) => {
      const id = a.hash.slice(1);
      const el = document.getElementById(id);
      if (el) { e.preventDefault(); el.scrollIntoView(); history.replaceState(null, '', '#' + id); }
    });
  }
  let cur = null;
  let tick = false;
  function update() {
    tick = false;
    const y = 130; // just below the sticky masthead and chat bar
    let lo = 0, hi = railDays.length - 1, i = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (railDays[mid].getBoundingClientRect().top <= y) { i = mid; lo = mid + 1; } else hi = mid - 1;
    }
    if (innerHeight + scrollY >= root.scrollHeight - 2) i = railDays.length - 1;
    const a = links.get((railDays[Math.max(i, 0)] || {}).id);
    if (!a || a === cur) return;
    if (cur) { cur.classList.remove('active'); cur.removeAttribute('aria-current'); cur.closest('.month').classList.remove('open'); }
    a.classList.add('active');
    a.setAttribute('aria-current', 'location');
    a.closest('.month').classList.add('open');
    a.scrollIntoView({ block: 'nearest' });
    cur = a;
  }
  addEventListener('scroll', () => { if (!tick) { tick = true; requestAnimationFrame(update); } }, { passive: true });
  update();
})();
