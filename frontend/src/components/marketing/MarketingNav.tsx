import { Menu, Moon, Sun, X } from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { Wordmark } from '../brand/Wordmark';
import { useHealth } from '../../lib/queries';
import { useTheme } from '../../lib/theme';
import { Button, IconButton, StatusDot } from '../ui/primitives';

const LINKS = [
  { href: '/#product', label: 'Product' },
  { href: '/#architecture', label: 'Architecture' },
  { href: '/about', label: 'About' },
];

function StatusBar() {
  const health = useHealth();
  const up = health.data?.status === 'ok';

  return (
    <div className="hidden border-b border-border bg-surface-2 px-6 py-2 text-[0.8125rem] text-ink-2 sm:block">
      <div className="mx-auto flex max-w-6xl items-center justify-center gap-2 text-center">
        <StatusDot tone={health.isLoading ? 'neutral' : up ? 'positive' : 'negative'} pulse={up} />
        <span>
          {health.isLoading
            ? 'Checking system status…'
            : up
              ? 'All core services operational.'
              : "We're having trouble reaching the API right now."}
        </span>
        <span className="text-ink-3">·</span>
        <span>Every posted transaction is balanced and auditable.</span>
      </div>
    </div>
  );
}

export function MarketingNav() {
  const navigate = useNavigate();
  const { dark, toggle } = useTheme();
  const [open, setOpen] = useState(false);

  return (
    <div className="sticky top-0 z-30 bg-canvas">
      <StatusBar />
      <header className="border-b border-border">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-4">
          <Link to="/" aria-label="LEDGR home">
            <Wordmark />
          </Link>

          <nav className="hidden items-center gap-8 md:flex">
            {LINKS.map((link) => (
              <a
                key={link.href}
                href={link.href}
                className="text-[0.9375rem] text-ink-2 transition-colors hover:text-ink"
              >
                {link.label}
              </a>
            ))}
          </nav>

          <div className="hidden items-center gap-2 md:flex">
            <IconButton
              title={dark ? 'Switch to light theme' : 'Switch to dark theme'}
              aria-pressed={dark}
              onClick={toggle}
            >
              {dark ? <Sun className="h-4 w-4" strokeWidth={1.8} /> : <Moon className="h-4 w-4" strokeWidth={1.8} />}
            </IconButton>
            <Button onClick={() => navigate('/login')}>Sign in</Button>
            <Button variant="primary" onClick={() => navigate('/app')}>
              Open console
            </Button>
          </div>

          <button
            type="button"
            className="flex h-9 w-9 items-center justify-center rounded-sm text-ink-2 md:hidden"
            onClick={() => setOpen((v) => !v)}
            aria-label={open ? 'Close menu' : 'Open menu'}
            aria-expanded={open}
          >
            {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </button>
        </div>

        {open && (
          <div className="border-t border-border bg-surface px-6 py-4 md:hidden">
            <nav className="flex flex-col gap-3">
              {LINKS.map((link) => (
                <a
                  key={link.href}
                  href={link.href}
                  onClick={() => setOpen(false)}
                  className="text-[0.9375rem] text-ink-2 hover:text-ink"
                >
                  {link.label}
                </a>
              ))}
            </nav>
            <div className="mt-4 flex items-center gap-2">
              <Button className="flex-1" onClick={() => navigate('/login')}>
                Sign in
              </Button>
              <Button variant="primary" className="flex-1" onClick={() => navigate('/app')}>
                Open console
              </Button>
              <IconButton
                title={dark ? 'Switch to light theme' : 'Switch to dark theme'}
                onClick={toggle}
              >
                {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              </IconButton>
            </div>
          </div>
        )}
      </header>
    </div>
  );
}
