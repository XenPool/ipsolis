/**
 * Portal i18n runtime.
 *
 * - Loads locale JSON from /locales/{lang}.json
 * - Replaces text on [data-i18n="key"] elements
 * - Translates attributes via [data-i18n-attr-<attr>="key"]  (e.g. data-i18n-attr-placeholder)
 * - Re-applies on htmx:afterSwap so fragments picked up dynamically also translate
 * - Persists the chosen language in localStorage.portal_lang
 * - Exposes window.i18n and window.t for ad-hoc use
 */
(function () {
  "use strict";

  const SUPPORTED = ["en", "de", "fr", "es", "it"];
  const DEFAULT_LANG = "en";
  const STORAGE_KEY = "portal_lang";
  const LOCALE_URL = (lang) => "/locales/" + lang + ".json";

  const cache = Object.create(null);
  let current = detect();
  let fallbackData = null;

  function detect() {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored && SUPPORTED.indexOf(stored) !== -1) return stored;
    } catch (_) { /* storage may be disabled */ }
    const nav = (navigator.language || navigator.userLanguage || "").slice(0, 2).toLowerCase();
    return SUPPORTED.indexOf(nav) !== -1 ? nav : DEFAULT_LANG;
  }

  async function loadLocale(lang) {
    if (cache[lang]) return cache[lang];
    const res = await fetch(LOCALE_URL(lang), { cache: "no-cache" });
    if (!res.ok) throw new Error("Locale " + lang + " failed: HTTP " + res.status);
    cache[lang] = await res.json();
    return cache[lang];
  }

  function lookup(obj, key) {
    if (!obj) return undefined;
    const parts = key.split(".");
    let cur = obj;
    for (let i = 0; i < parts.length; i++) {
      if (cur == null || typeof cur !== "object") return undefined;
      cur = cur[parts[i]];
    }
    return cur;
  }

  function interpolate(str, params) {
    if (!params || typeof str !== "string") return str;
    return str.replace(/\{(\w+)\}/g, function (m, k) {
      return params[k] !== undefined ? String(params[k]) : m;
    });
  }

  function t(key, params) {
    let v = lookup(cache[current], key);
    if (v === undefined) v = lookup(fallbackData, key);
    if (v === undefined) return key;
    return interpolate(v, params);
  }

  function apply(root) {
    const scope = root && root.querySelectorAll ? root : document;

    scope.querySelectorAll("[data-i18n]").forEach(function (el) {
      const key = el.getAttribute("data-i18n");
      if (!key) return;
      const rawParams = el.getAttribute("data-i18n-params");
      let params;
      if (rawParams) {
        try { params = JSON.parse(rawParams); } catch (_) { params = undefined; }
      }
      el.textContent = t(key, params);
    });

    scope.querySelectorAll("*").forEach(function (el) {
      const attrs = el.attributes;
      for (let i = 0; i < attrs.length; i++) {
        const a = attrs[i];
        if (a.name.indexOf("data-i18n-attr-") === 0) {
          const target = a.name.slice("data-i18n-attr-".length);
          el.setAttribute(target, t(a.value));
        }
      }
    });

    scope.querySelectorAll("[data-i18n-date]").forEach(function (el) {
      const iso = el.getAttribute("data-i18n-date");
      if (!iso) return;
      const opts = el.getAttribute("data-i18n-date-opts");
      let parsed;
      try { parsed = opts ? JSON.parse(opts) : undefined; } catch (_) { parsed = undefined; }
      el.textContent = formatDate(iso, parsed);
    });

    scope.querySelectorAll("[data-i18n-number]").forEach(function (el) {
      const raw = el.getAttribute("data-i18n-number");
      const n = Number(raw);
      if (!Number.isFinite(n)) return;
      const opts = el.getAttribute("data-i18n-number-opts");
      let parsed;
      try { parsed = opts ? JSON.parse(opts) : undefined; } catch (_) { parsed = undefined; }
      el.textContent = formatNumber(n, parsed);
    });

    document.documentElement.setAttribute("lang", current);
  }

  function formatDate(value, opts) {
    const d = (value instanceof Date) ? value : new Date(value);
    if (isNaN(d.getTime())) return String(value);
    return new Intl.DateTimeFormat(current, opts || { year: "numeric", month: "short", day: "numeric" }).format(d);
  }

  function formatNumber(n, opts) {
    return new Intl.NumberFormat(current, opts || undefined).format(n);
  }

  async function setLanguage(lang) {
    if (SUPPORTED.indexOf(lang) === -1) return;
    await loadLocale(lang);
    current = lang;
    try { localStorage.setItem(STORAGE_KEY, lang); } catch (_) {}
    apply();
    document.dispatchEvent(new CustomEvent("i18n:changed", { detail: { lang: lang } }));
  }

  async function init() {
    try {
      fallbackData = await loadLocale(DEFAULT_LANG);
      if (current !== DEFAULT_LANG) {
        try { await loadLocale(current); }
        catch (_) { current = DEFAULT_LANG; }
      }
    } catch (e) {
      console.warn("[i18n] failed to load default locale:", e);
      return;
    }
    apply();

    if (document.body) {
      document.body.addEventListener("htmx:afterSwap", function (e) {
        apply(e.target || document);
      });
    }
  }

  window.i18n = {
    t: t,
    setLanguage: setLanguage,
    getLanguage: function () { return current; },
    getSupported: function () { return SUPPORTED.slice(); },
    formatDate: formatDate,
    formatNumber: formatNumber,
    apply: apply
  };
  window.t = t;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
