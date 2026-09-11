import { Link } from 'react-router-dom';

import { Wordmark } from '../brand/Wordmark';

export function MarketingFooter() {
  return (
    <footer className="border-t border-border">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-10 sm:flex-row sm:items-center sm:justify-between">
        <Wordmark tagline="Double-entry ledger system" />
        <nav className="flex flex-wrap gap-x-6 gap-y-2 text-[0.875rem] text-ink-2">
          <a href="/#product" className="hover:text-ink">
            Product
          </a>
          <a href="/#architecture" className="hover:text-ink">
            Architecture
          </a>
          <Link to="/about" className="hover:text-ink">
            About
          </Link>
          <Link to="/app" className="hover:text-ink">
            Console
          </Link>
        </nav>
      </div>
    </footer>
  );
}
