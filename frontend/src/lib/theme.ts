import { useEffect, useState } from 'react';

const STORAGE_KEY = 'ledgr.theme';

function prefersDark(): boolean {
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

/**
 * Theme state, shared by the marketing site and the console.
 *
 * The `<head>` inline script in index.html already applies the right class
 * before first paint (so there is never a flash of the wrong theme); this
 * hook just keeps React and `localStorage` in sync with that decision.
 */
export function useTheme() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'));

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark);
    try {
      localStorage.setItem(STORAGE_KEY, dark ? 'dark' : 'light');
    } catch {
      /* storage may be unavailable; the theme still applies for this session */
    }
  }, [dark]);

  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = localStorage.getItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
    if (stored) return; // explicit choice already made - do not override it
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => setDark(prefersDark());
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, []);

  return { dark, toggle: () => setDark((value) => !value), setDark };
}
