/**
 * ip·Solis Tailwind config — reproduces the former Play-CDN inline config
 * (api/app/templates/_partials/theme_head.html) 1:1 so the build-time compiled
 * CSS is visually identical to the old runtime JIT.
 *
 * IMPORTANT — Tailwind v3 (matches the cdn.tailwindcss.com Play-CDN). Do NOT
 * upgrade to v4: the config format and dark-mode variant differ.
 *
 * The content globs MUST include the Python route files: several Tailwind
 * class strings are assembled in Python (_STATUS_COLORS / _STEP_COLORS /
 * _ASSET_STATUS_COLORS in routes/ui.py + routes/portal.py) and would otherwise
 * be dropped by an HTML-only scan. The safelist is a belt-and-suspenders guard
 * for that status-badge color matrix.
 *
 * Paths are relative to this file (api/), which is where the Docker "assets"
 * builder stage runs the Tailwind CLI.
 */
module.exports = {
  darkMode: 'class',
  content: [
    './app/templates/**/*.html',
    './app/routes/**/*.py',
  ],
  safelist: [
    // Status/step/asset badge backgrounds (light + dark) assembled in Python.
    { pattern: /^bg-(gray|slate|red|orange|amber|yellow|green|sky|blue|indigo)-(50|100)$/, variants: ['dark'] },
    { pattern: /^bg-(red|orange|amber|yellow|green|sky|blue|indigo|slate)-500\/(10|15)$/, variants: ['dark'] },
    // Badge text colors.
    { pattern: /^text-(gray|slate|red|orange|amber|yellow|green|sky|blue|indigo)-(200|300|400|500|600|700|800)$/, variants: ['dark'] },
    // Badge/alert borders.
    { pattern: /^border-(red|amber|green|slate|blue)-(200|300)$/, variants: ['dark'] },
    { pattern: /^border-(red|amber|green|slate|blue)-500\/30$/, variants: ['dark'] },
    // Custom CSS-variable-backed tokens (referenced everywhere; explicit avoids surprises).
    'bg-surface', 'bg-surface-raised', 'text-body', 'text-heading', 'text-muted', 'border-token-border',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
      },
      colors: {
        surface:          'rgb(var(--surface) / <alpha-value>)',
        'surface-raised': 'rgb(var(--surface-raised) / <alpha-value>)',
        body:             'rgb(var(--body) / <alpha-value>)',
        heading:          'rgb(var(--heading) / <alpha-value>)',
        muted:            'rgb(var(--muted) / <alpha-value>)',
        'token-border':   'rgb(var(--token-border) / <alpha-value>)',
      },
    },
  },
  plugins: [],
};
