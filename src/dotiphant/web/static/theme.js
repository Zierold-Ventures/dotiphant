// Dotiphant theme: System, Light or Dark. Loaded without defer at the top of <head>, so the saved choice
// is on <html> before the page paints. Self-contained: pages without app.js (setup, errors) use it too.
// tokens.css reads data-theme="light" or "dark"; no attribute follows the system setting.
(() => {
  'use strict';
  const KEY = 'dotiphant-theme';
  const root = document.documentElement;
  const COLORS = { light: '#f2f1ed', dark: '#0d0e0f' };

  function saved() {
    try {
      const value = localStorage.getItem(KEY);
      return value === 'light' || value === 'dark' ? value : 'system';
    } catch {
      return 'system';
    }
  }

  function save(choice) {
    try {
      if (choice === 'system') localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, choice);
    } catch {
      // Storage blocked: the choice still applies to this page.
    }
  }

  // The browser bar color follows the page, not only the system setting.
  function tintBar(choice) {
    for (const meta of document.querySelectorAll('meta[name="theme-color"]')) {
      const own = (meta.getAttribute('media') || '').includes('dark') ? 'dark' : 'light';
      meta.setAttribute('content', COLORS[choice === 'system' ? own : choice]);
    }
  }

  function apply(choice) {
    if (choice === 'system') delete root.dataset.theme;
    else root.dataset.theme = choice;
    tintBar(choice);
  }

  function showChoice(choice) {
    for (const input of document.querySelectorAll('[data-theme-switch] input[type=radio]')) {
      input.checked = input.value === choice;
    }
  }

  apply(saved());

  document.addEventListener('DOMContentLoaded', () => {
    showChoice(saved());
    for (const group of document.querySelectorAll('[data-theme-switch]')) {
      group.hidden = false;
      group.addEventListener('change', (e) => {
        const choice = e.target.value;
        if (choice !== 'system' && choice !== 'light' && choice !== 'dark') return;
        save(choice);
        apply(choice);
      });
    }
  });

  // Another Dotiphant tab changed the theme.
  addEventListener('storage', (e) => {
    if (e.key !== KEY && e.key !== null) return;
    const choice = saved();
    apply(choice);
    showChoice(choice);
  });
})();
