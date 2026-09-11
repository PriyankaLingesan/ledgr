/**
 * Landing page.
 *
 * Typography, spacing and thin rules carry the page - no gradients, no 3D
 * imagery, no floating decorative cards. The serif is reserved for this page
 * (and the About page) only; the console stays plainly legible in Inter.
 */

import {
  ArrowRight,
  ClipboardCheck,
  Database,
  FileStack,
  Layers,
  ScanSearch,
  Server,
  ShieldCheck,
  Split,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { Section } from '../../components/marketing/MarketingLayout';
import { Button } from '../../components/ui/primitives';

const GUARANTEES = [
  {
    icon: Split,
    title: 'Double-entry validation',
    body: 'Every transaction must balance. Total debits are checked against total credits before a single row is written - both in the service layer and again as a database constraint.',
  },
  {
    icon: Layers,
    title: 'Atomic posting',
    body: 'A transaction and every one of its ledger entries are written together, in a single database transaction, or not written at all. There is no partially-posted state.',
  },
  {
    icon: ClipboardCheck,
    title: 'Idempotent requests',
    body: 'Every posting can carry an idempotency key. A retried request after a timeout replays the original result instead of creating a second transaction.',
  },
  {
    icon: FileStack,
    title: 'Append-only ledger',
    body: 'Posted ledger entries are never edited or deleted, enforced by the database itself. A correction is made by posting a new, linked reversing transaction.',
  },
  {
    icon: ScanSearch,
    title: 'Audit trail',
    body: 'Every posting, reversal, and account change is recorded with its actor and request identifier, so the full history behind any balance can be traced.',
  },
];

const STEPS = [
  'Create transaction',
  'Validate entries',
  'Post atomically',
  'Record ledger entries',
  'Create audit trail',
];

const ARCHITECTURE = [
  { icon: Server, label: 'Web client', detail: 'React console and API consumers' },
  { icon: Layers, label: 'FastAPI', detail: 'Request validation and routing' },
  { icon: ShieldCheck, label: 'Service layer', detail: 'Financial invariants are enforced here' },
  { icon: Database, label: 'PostgreSQL', detail: 'Constraints, locks, and append-only storage' },
];

export default function LandingPage() {
  const navigate = useNavigate();

  return (
    <>
      {/* Hero */}
      <Section className="pt-16 pb-20 sm:pt-24 sm:pb-28">
        <div className="max-w-3xl">
          <p className="mb-5 text-[0.9375rem] font-medium text-accent-ink">LEDGR</p>
          <h1 className="font-serif text-[2.25rem] font-medium leading-[1.15] tracking-tight text-ink sm:text-[3rem]">
            Financial operations, built on correct ledgers.
          </h1>
          <p className="mt-6 max-w-xl text-lg leading-relaxed text-ink-2">
            LEDGR is a double-entry ledger system designed to make financial transactions
            reliable, traceable, balanced, and auditable.
          </p>
          <div className="mt-9 flex flex-wrap items-center gap-3">
            <Button variant="primary" size="lg" onClick={() => navigate('/app')}>
              Open console
              <ArrowRight className="h-4 w-4" strokeWidth={2} />
            </Button>
            <Button size="lg" onClick={() => document.getElementById('product')?.scrollIntoView()}>
              Learn more
            </Button>
          </div>
        </div>
      </Section>

      <div className="border-t border-border" />

      {/* Guarantees / features */}
      <Section id="product">
        <div className="max-w-2xl">
          <h2 className="font-serif text-[1.75rem] font-medium text-ink sm:text-3xl">
            Built for financial correctness
          </h2>
          <p className="mt-3 text-[1.0625rem] leading-relaxed text-ink-2">
            These are not aspirations - they are the invariants the system enforces on every
            request, checked in the service layer and backed by the database.
          </p>
        </div>

        <div className="mt-12 divide-y divide-border border-t border-border">
          {GUARANTEES.map((item) => (
            <div key={item.title} className="grid grid-cols-1 gap-4 py-7 sm:grid-cols-[2fr_3fr]">
              <div className="flex items-start gap-3">
                <item.icon className="mt-0.5 h-5 w-5 shrink-0 text-accent-ink" strokeWidth={1.6} />
                <h3 className="text-lg font-medium text-ink">{item.title}</h3>
              </div>
              <p className="text-[0.9375rem] leading-relaxed text-ink-2">{item.body}</p>
            </div>
          ))}
        </div>
      </Section>

      <div className="border-t border-border" />

      {/* How it works */}
      <Section>
        <h2 className="font-serif text-[1.75rem] font-medium text-ink sm:text-3xl">How it works</h2>
        <p className="mt-3 max-w-2xl text-[1.0625rem] leading-relaxed text-ink-2">
          Every posting follows the same path, whether it comes from the console or a direct API
          call.
        </p>

        <ol className="mt-12 flex flex-col gap-0 sm:flex-row sm:items-stretch sm:gap-0">
          {STEPS.map((step, index) => (
            <li key={step} className="flex flex-1 items-center">
              <div className="flex w-full items-center gap-3 py-3 sm:flex-col sm:items-start sm:gap-4 sm:py-0">
                <span className="num flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-border-strong text-sm text-ink-2">
                  {index + 1}
                </span>
                <span className="text-[0.9375rem] font-medium text-ink sm:mt-1">{step}</span>
              </div>
              {index < STEPS.length - 1 && (
                <ArrowRight
                  className="mx-2 hidden h-4 w-4 shrink-0 text-ink-3 sm:block"
                  strokeWidth={1.6}
                />
              )}
            </li>
          ))}
        </ol>
      </Section>

      <div className="border-t border-border" />

      {/* Architecture */}
      <Section id="architecture">
        <div className="grid grid-cols-1 gap-12 lg:grid-cols-[1fr_1.2fr]">
          <div>
            <h2 className="font-serif text-[1.75rem] font-medium text-ink sm:text-3xl">
              Architecture
            </h2>
            <p className="mt-3 text-[0.9375rem] leading-relaxed text-ink-2">
              A conventional, unglamorous stack, on purpose. Financial correctness comes from
              where the rules live, not from the number of services involved.
            </p>
            <p className="mt-4 text-[0.9375rem] leading-relaxed text-ink-2">
              Every invariant - balanced entries, atomic posting, idempotency, immutability - is
              enforced in the service layer and, wherever the database can express it, a second
              time as a constraint. The client is never trusted to get the accounting right.
            </p>
          </div>

          <div className="flex flex-col">
            {ARCHITECTURE.map((layer, index) => (
              <div key={layer.label}>
                <div className="flex items-center gap-4 rounded-md border border-border bg-surface px-5 py-4">
                  <layer.icon className="h-5 w-5 shrink-0 text-accent-ink" strokeWidth={1.6} />
                  <div>
                    <p className="text-[0.9375rem] font-medium text-ink">{layer.label}</p>
                    <p className="text-[0.8125rem] text-ink-2">{layer.detail}</p>
                  </div>
                </div>
                {index < ARCHITECTURE.length - 1 && (
                  <div className="flex justify-center py-1.5">
                    <div className="h-5 w-px bg-border-strong" />
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </Section>

      <div className="border-t border-border" />

      {/* Closing CTA */}
      <Section className="text-center">
        <h2 className="font-serif text-[1.75rem] font-medium text-ink sm:text-3xl">
          See it working, end to end.
        </h2>
        <p className="mx-auto mt-3 max-w-lg text-[1.0625rem] leading-relaxed text-ink-2">
          Post a balanced transaction and watch it become ledger entries, a derived balance, and
          an audit trail.
        </p>
        <div className="mt-8 flex justify-center">
          <Button variant="primary" size="lg" onClick={() => navigate('/app')}>
            Open console
            <ArrowRight className="h-4 w-4" strokeWidth={2} />
          </Button>
        </div>
      </Section>
    </>
  );
}
