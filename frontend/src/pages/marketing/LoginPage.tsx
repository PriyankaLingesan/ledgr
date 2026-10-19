/**
 * Sign in, or create an account (a new organization with you as its owner).
 *
 * Credentials are verified by the server (`POST /auth/login`, `/auth/register`);
 * this page only collects them. The server answers every sign-in failure with the
 * same generic message - it never reveals whether an email exists - and throttles
 * attempts per client. A successful login sets an httpOnly session cookie: the
 * browser holds no credential that page scripts can read.
 */

import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, ArrowRight } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';

import { Section } from '../../components/marketing/MarketingLayout';
import { Button, Field, Input, Select, Spinner } from '../../components/ui/primitives';
import { ApiError, request } from '../../lib/api';
import { useAuth } from '../../lib/auth';
import { cn } from '../../lib/cn';
import type { AuthConfig } from '../../lib/opsTypes';

const CURRENCIES = ['USD', 'EUR', 'GBP', 'JPY', 'INR', 'AUD', 'CAD', 'CHF', 'SGD'];

function useAuthConfig() {
  return useQuery({
    queryKey: ['auth-config'],
    queryFn: () => request<AuthConfig>('/auth/config', { public: true }),
    retry: false,
    staleTime: 0,
  });
}

function Notice({ tone, children }: { tone: 'warning' | 'error'; children: React.ReactNode }) {
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      className={
        tone === 'error'
          ? 'flex items-start gap-2 rounded-sm border border-negative/30 bg-negative-soft px-3 py-2.5 text-[0.8125rem] text-negative'
          : 'flex items-start gap-2 rounded-sm border border-warning/30 bg-warning-soft px-3 py-2.5 text-[0.8125rem] text-warning'
      }
    >
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.9} />
      <span>{children}</span>
    </div>
  );
}

function message(cause: unknown, fallback: string): string {
  if (cause instanceof ApiError) {
    if (cause.code === 'rate_limited') return 'Too many attempts. Wait a moment and try again.';
    return cause.message;
  }
  return fallback;
}

function SignInForm() {
  const { login, endedReason } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const destination = (location.state as { from?: string } | null)?.from ?? '/app';

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(email.trim(), password);
      navigate(destination, { replace: true });
    } catch (cause) {
      setError(message(cause, 'Sign in failed. Please try again.'));
      setPassword('');
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="mt-6 flex flex-col gap-4" noValidate>
      {endedReason === 'expired' && !error && (
        <Notice tone="warning">Your session expired. Please sign in again.</Notice>
      )}
      {error && <Notice tone="error">{error}</Notice>}

      <Field label="Email" htmlFor="email" required>
        <Input
          id="email"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          autoFocus
          required
        />
      </Field>
      <Field label="Password" htmlFor="password" required>
        <Input
          id="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
        />
      </Field>

      <Button
        type="submit"
        variant="primary"
        size="lg"
        className="justify-center"
        disabled={busy || !email || !password}
        icon={busy ? <Spinner className="text-current" /> : undefined}
      >
        Sign in
        {!busy && <ArrowRight className="h-4 w-4" strokeWidth={2} />}
      </Button>
    </form>
  );
}

function CreateAccountForm({ codeRequired }: { codeRequired: boolean }) {
  const { register } = useAuth();
  const navigate = useNavigate();

  const [organization, setOrganization] = useState('');
  const [currency, setCurrency] = useState('USD');
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const tooShort = password.length > 0 && password.length < 10;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await register({
        organization_name: organization.trim(),
        base_currency: currency,
        full_name: fullName.trim(),
        email: email.trim(),
        password,
        signup_code: code.trim() || null,
      });
      navigate('/app', { replace: true });
    } catch (cause) {
      setError(message(cause, 'Could not create the account. Please try again.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="mt-6 flex flex-col gap-4" noValidate>
      <p className="text-[0.8125rem] leading-relaxed text-ink-2">
        You will be the <strong className="font-medium text-ink">owner</strong> of a new, private
        organization. Its data is isolated from every other organization, and you can invite
        teammates afterwards.
      </p>
      {error && <Notice tone="error">{error}</Notice>}

      <Field label="Organization name" htmlFor="org" required>
        <Input
          id="org"
          value={organization}
          onChange={(event) => setOrganization(event.target.value)}
          placeholder="e.g. Northwind Payments"
          autoFocus
        />
      </Field>
      <Field label="Base currency" htmlFor="currency">
        <Select id="currency" value={currency} onChange={(event) => setCurrency(event.target.value)}>
          {CURRENCIES.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Your name" htmlFor="full-name" required>
        <Input
          id="full-name"
          autoComplete="name"
          value={fullName}
          onChange={(event) => setFullName(event.target.value)}
        />
      </Field>
      <Field label="Email" htmlFor="reg-email" required>
        <Input
          id="reg-email"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
      </Field>
      <Field
        label="Password"
        htmlFor="reg-password"
        required
        hint="At least 10 characters. Stored only as a salted scrypt hash."
        error={tooShort ? 'Use at least 10 characters.' : null}
      >
        <Input
          id="reg-password"
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </Field>
      {codeRequired && (
        <Field label="Signup code" htmlFor="signup-code" required hint="Provided by whoever runs this deployment.">
          <Input
            id="signup-code"
            type="password"
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
        </Field>
      )}

      <Button
        type="submit"
        variant="primary"
        size="lg"
        className="justify-center"
        disabled={
          busy ||
          !organization.trim() ||
          !fullName.trim() ||
          !email ||
          password.length < 10 ||
          (codeRequired && !code)
        }
        icon={busy ? <Spinner className="text-current" /> : undefined}
      >
        Create organization
        {!busy && <ArrowRight className="h-4 w-4" strokeWidth={2} />}
      </Button>
    </form>
  );
}

export default function LoginPage() {
  const { status } = useAuth();
  const config = useAuthConfig();
  const [mode, setMode] = useState<'signin' | 'register'>('signin');

  if (status === 'signed-in') return <Navigate to="/app" replace />;

  const signupEnabled = config.data?.signup_enabled ?? false;
  const active = signupEnabled ? mode : 'signin';

  return (
    <Section className="flex min-h-[70vh] items-center justify-center py-16">
      <div className="w-full max-w-sm">
        <h1 className="font-serif text-2xl font-medium text-ink">
          {active === 'signin' ? 'Sign in to LEDGR' : 'Create your organization'}
        </h1>
        <p className="mt-2 text-[0.9375rem] leading-relaxed text-ink-2">
          Financial Ledger & Reconciliation Platform
        </p>

        {signupEnabled && (
          <div role="tablist" aria-label="Account" className="mt-6 grid grid-cols-2 rounded-sm border border-border p-0.5">
            {(
              [
                ['signin', 'Sign in'],
                ['register', 'Create account'],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={active === key}
                onClick={() => setMode(key)}
                className={cn(
                  'rounded-xs px-3 py-1.5 text-[0.8125rem] font-medium transition-colors',
                  active === key ? 'bg-accent-soft text-accent-ink' : 'text-ink-3 hover:text-ink',
                )}
              >
                {label}
              </button>
            ))}
          </div>
        )}

        {config.isLoading ? (
          <div className="mt-10 flex items-center gap-2 text-sm text-ink-3">
            <Spinner /> Checking the server…
          </div>
        ) : config.isError ? (
          <div className="mt-8">
            <Notice tone="error">
              Cannot reach the LEDGR API. Check that the backend is running and try again.
            </Notice>
            <Button className="mt-4" onClick={() => config.refetch()}>
              Retry
            </Button>
          </div>
        ) : active === 'register' ? (
          <CreateAccountForm codeRequired={config.data?.signup_code_required ?? false} />
        ) : (
          <SignInForm />
        )}

        {config.data && !signupEnabled && (
          <p className="mt-6 text-[0.8125rem] text-ink-3">
            Self-service signup is turned off on this deployment. Ask an organization owner to invite you.
          </p>
        )}
      </div>
    </Section>
  );
}
