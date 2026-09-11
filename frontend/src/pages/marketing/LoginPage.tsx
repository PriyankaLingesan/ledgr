/**
 * Entry page.
 *
 * LEDGR does not implement authentication - there is no user table, no
 * session, no password check anywhere in the backend. Pretending otherwise
 * here would be dishonest, so this page says so plainly and offers the one
 * thing that is real: an operator name, recorded as `actor` on everything
 * that browser posts from here on (see `lib/api.ts`).
 */

import { ArrowRight } from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Section } from '../../components/marketing/MarketingLayout';
import { Button, Field, Input } from '../../components/ui/primitives';
import { getActor, setActor } from '../../lib/api';

export default function LoginPage() {
  const navigate = useNavigate();
  const [name, setName] = useState(() => {
    const current = getActor();
    return current === 'console' ? '' : current;
  });

  const enter = () => {
    setActor(name);
    navigate('/app');
  };

  return (
    <Section className="flex min-h-[70vh] items-center justify-center py-20">
      <div className="w-full max-w-sm">
        <h1 className="font-serif text-2xl font-medium text-ink">Enter the console</h1>
        <p className="mt-3 text-[0.9375rem] leading-relaxed text-ink-2">
          LEDGR is an internal ledger tool and does not have its own sign-in system - there is no
          account to create or password to enter. The name below is recorded as the actor on
          transactions and audit events you post, nothing more.
        </p>

        <div className="mt-8 flex flex-col gap-4">
          <Field label="Your name" htmlFor="actor-name" hint="Optional. Defaults to “console” if left blank.">
            <Input
              id="actor-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. priyanka"
              onKeyDown={(event) => event.key === 'Enter' && enter()}
              autoFocus
            />
          </Field>

          <Button variant="primary" size="lg" onClick={enter} className="justify-center">
            Continue to console
            <ArrowRight className="h-4 w-4" strokeWidth={2} />
          </Button>
        </div>
      </div>
    </Section>
  );
}
