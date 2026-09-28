/**
 * Entry page.
 *
 * Writes now require a real, server-verified API key (see `lib/api.ts` and
 * `app.services.auth` on the backend) - not a login system with accounts or
 * passwords, but not decorative either: a request without a valid key gets
 * a genuine 401 from every write endpoint. This page is where that key is
 * pasted in, plus an optional display name recorded nowhere but this
 * browser's own `localStorage`.
 */

import { ArrowRight } from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Section } from '../../components/marketing/MarketingLayout';
import { Button, Field, Input } from '../../components/ui/primitives';
import { getActor, getApiKey, setActor, setApiKey } from '../../lib/api';

export default function LoginPage() {
  const navigate = useNavigate();
  const [name, setName] = useState(() => {
    const current = getActor();
    return current === 'console' ? '' : current;
  });
  const [apiKey, setApiKeyInput] = useState(() => getApiKey() ?? '');

  const enter = () => {
    setActor(name);
    setApiKey(apiKey);
    navigate('/app');
  };

  return (
    <Section className="flex min-h-[70vh] items-center justify-center py-20">
      <div className="w-full max-w-sm">
        <h1 className="font-serif text-2xl font-medium text-ink">Enter the console</h1>
        <p className="mt-3 text-[0.9375rem] leading-relaxed text-ink-2">
          LEDGR has no account system of its own - there's nothing to sign up for - but writes do
          require a real API key, verified against the database on every request. Reads work
          without one; posting, correcting, or reconciling anything will fail with a 401 until a
          valid key is set below.
        </p>

        <div className="mt-8 flex flex-col gap-4">
          <Field
            label="API key"
            htmlFor="api-key"
            hint="Minted via `python -m app.scripts.create_api_key <label>` on the backend, or printed once by the demo seed script on first startup."
          >
            <Input
              id="api-key"
              type="password"
              value={apiKey}
              onChange={(event) => setApiKeyInput(event.target.value)}
              placeholder="ledgr_..."
              onKeyDown={(event) => event.key === 'Enter' && enter()}
              autoFocus
            />
          </Field>

          <Field
            label="Display name"
            htmlFor="actor-name"
            hint="Cosmetic only - shown in this browser's UI, not sent anywhere. The key above determines who writes are attributed to."
          >
            <Input
              id="actor-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. priyanka"
              onKeyDown={(event) => event.key === 'Enter' && enter()}
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
