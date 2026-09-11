/**
 * About LEDGR.
 *
 * Text, typography and restraint do the work here - no imagery, no diagrams,
 * no illustration. Shared by the public `/about` route and the console's
 * `/app/about`.
 */

import { Instagram, Layers, Linkedin, RefreshCw, Scale, ShieldCheck } from 'lucide-react';

import { DEVELOPER } from '../../lib/site';

const GUARANTEES = [
  {
    icon: Scale,
    title: 'Balanced transactions',
    body: 'Every transaction must satisfy debits = credits, checked before anything is written and enforced again by the database.',
    equation: true,
  },
  {
    icon: Layers,
    title: 'Atomic posting',
    body: 'A transaction and its ledger entries are committed together as one database operation - all of it, or none of it.',
  },
  {
    icon: RefreshCw,
    title: 'Idempotent requests',
    body: 'Retrying a request with the same idempotency key does not accidentally create a duplicate transaction.',
  },
  {
    icon: ShieldCheck,
    title: 'Append-only records',
    body: 'Posted ledger entries cannot be silently edited or deleted. Corrections are represented through new, linked transactions.',
  },
];

export function AboutContent() {
  return (
    <div className="mx-auto max-w-3xl px-6 py-16 sm:py-24">
      <h1 className="font-serif text-[2rem] font-medium leading-tight text-ink sm:text-[2.5rem]">
        About LEDGR
      </h1>
      <p className="mt-5 text-lg leading-relaxed text-ink-2">
        LEDGR is a double-entry ledger system designed for financial correctness, traceability,
        and auditability.
      </p>
      <p className="mt-4 max-w-2xl text-[0.9375rem] leading-relaxed text-ink-2">
        It is built to handle financial transactions reliably, maintaining the invariants a
        ledger depends on - balanced entries, atomic writes, and a history that cannot be quietly
        altered - regardless of what is asking it to record them.
      </p>

      <h2 className="mt-16 font-serif text-2xl font-medium text-ink">What LEDGR guarantees</h2>
      <div className="mt-6 grid grid-cols-1 gap-px overflow-hidden rounded-md border border-border bg-border sm:grid-cols-2">
        {GUARANTEES.map((item) => (
          <div key={item.title} className="hover-lift relative z-0 hover:z-10 bg-surface px-6 py-6">
            <item.icon className="h-4.5 w-4.5 text-accent-ink" strokeWidth={1.6} />
            <h3 className="mt-3 text-[0.9375rem] font-medium text-ink">{item.title}</h3>
            <p className="mt-1.5 text-[0.8125rem] leading-relaxed text-ink-2">{item.body}</p>
            {item.equation && (
              <p className="num mt-3 text-[0.8125rem] font-medium text-accent-ink">
                Debits = Credits
              </p>
            )}
          </div>
        ))}
      </div>

      <h2 className="mt-16 font-serif text-2xl font-medium text-ink">
        Built for financial correctness
      </h2>
      <p className="mt-4 max-w-2xl text-[0.9375rem] leading-relaxed text-ink-2">
        Critical financial rules are not trusted to the frontend. Correctness - balance
        validation, atomicity, idempotency, immutability - is enforced in the backend service
        layer and, wherever the database can express it, a second time as a constraint there.
        Whatever client is calling the API, the rules hold.
      </p>

      <h2 className="mt-16 font-serif text-2xl font-medium text-ink">Developed by</h2>
      <div className="hover-lift mt-6 flex items-center justify-between gap-4 rounded-md border border-border bg-surface px-6 py-6">
        <div className="min-w-0">
          <p className="text-[1.0625rem] font-medium text-ink">{DEVELOPER.name}</p>
          <p className="mt-1.5 max-w-md text-[0.8125rem] leading-relaxed text-ink-2">
            LEDGR is a full-stack project focused on exploring how reliable, traceable, and
            auditable financial transaction systems can be built.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <a
            href={DEVELOPER.linkedin}
            target="_blank"
            rel="noreferrer"
            aria-label="LinkedIn"
            className="flex h-9 w-9 items-center justify-center rounded-sm text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
          >
            <Linkedin className="h-4.5 w-4.5" strokeWidth={1.7} />
          </a>
          <a
            href={DEVELOPER.instagram}
            target="_blank"
            rel="noreferrer"
            aria-label="Instagram"
            className="flex h-9 w-9 items-center justify-center rounded-sm text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
          >
            <Instagram className="h-4.5 w-4.5" strokeWidth={1.7} />
          </a>
        </div>
      </div>
    </div>
  );
}
