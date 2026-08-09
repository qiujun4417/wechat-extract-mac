// static/theme.js — Shared dark/light theme system
(function() {
    const STORAGE_KEY = 'app_theme';

    function getPreferredTheme() {
        const saved = localStorage.getItem(STORAGE_KEY);
        if (saved) return saved;
        // Migrate from old scraper_theme key
        const oldKey = localStorage.getItem('scraper_theme');
        if (oldKey) {
            localStorage.setItem(STORAGE_KEY, oldKey);
            localStorage.removeItem('scraper_theme');
            return oldKey;
        }
        return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }

    function applyTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        // Update toggle button if it exists
        const btn = document.getElementById('themeToggle');
        if (btn) btn.textContent = theme === 'dark' ? '🌙' : '☀️';
    }

    // Apply immediately (before DOM ready to prevent flash)
    applyTheme(getPreferredTheme());

    // Also update button once DOM is ready (in case script loads before button exists)
    document.addEventListener('DOMContentLoaded', function() {
        var theme = document.documentElement.getAttribute('data-theme') || 'dark';
        var btn = document.getElementById('themeToggle');
        if (btn) btn.textContent = theme === 'dark' ? '🌙' : '☀️';
    });

    // Global toggle function
    window.toggleTheme = function() {
        var current = document.documentElement.getAttribute('data-theme') || 'dark';
        var next = current === 'dark' ? 'light' : 'dark';
        localStorage.setItem(STORAGE_KEY, next);
        applyTheme(next);
    };
})();
