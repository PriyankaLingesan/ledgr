import type { ReactNode } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';

import { AppShell } from './components/layout/AppShell';
import { MarketingLayout } from './components/marketing/MarketingLayout';
import { RequireAuth, RequirePermission } from './lib/auth';
import type { Permission } from './lib/opsTypes';
import AboutPage from './pages/AboutPage';
import AccountDetailPage from './pages/AccountDetailPage';
import AccountsPage from './pages/AccountsPage';
import AlertsPage from './pages/AlertsPage';
import ApiKeysPage from './pages/admin/ApiKeysPage';
import ApprovalsPage from './pages/ApprovalsPage';
import AuditPage from './pages/AuditPage';
import DashboardPage from './pages/DashboardPage';
import FlightRecorderPage from './pages/FlightRecorderPage';
import InboxPage from './pages/InboxPage';
import IntelligencePage from './pages/IntelligencePage';
import InvestigationDetailPage from './pages/InvestigationDetailPage';
import InvestigationsPage from './pages/InvestigationsPage';
import LearningGuidePage from './pages/LearningGuidePage';
import LedgerPage from './pages/LedgerPage';
import ReconciliationPage from './pages/ReconciliationPage';
import SettingsPage from './pages/admin/SettingsPage';
import SystemHealthPage from './pages/SystemHealthPage';
import UsersPage from './pages/admin/UsersPage';
import AboutMarketingPage from './pages/marketing/AboutPage';
import LandingPage from './pages/marketing/LandingPage';
import LoginPage from './pages/marketing/LoginPage';
import NewTransactionPage from './pages/NewTransactionPage';
import TransactionDetailPage from './pages/TransactionDetailPage';
import TransactionsPage from './pages/TransactionsPage';

/**
 * A console route: must be signed in, and (optionally) the role must hold
 * `permission`. The check is a UX guard - the API enforces the same rule on
 * every request regardless of what the browser renders.
 */
function Console({ children, permission }: { children: ReactNode; permission?: Permission }) {
  return (
    <RequireAuth>
      <AppShell>
        {permission ? <RequirePermission permission={permission}>{children}</RequirePermission> : children}
      </AppShell>
    </RequireAuth>
  );
}

export default function App() {
  return (
    <Routes>
      {/* Marketing site */}
      <Route element={<MarketingLayout />}>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/about" element={<AboutMarketingPage />} />
      </Route>

      {/* Console */}
      <Route
        path="/app"
        element={
          <Console permission="dashboard:read">
            <DashboardPage />
          </Console>
        }
      />
      <Route
        path="/app/learn"
        element={
          <Console permission="dashboard:read">
            <LearningGuidePage />
          </Console>
        }
      />
      <Route
        path="/app/inbox"
        element={
          <Console permission="dashboard:read">
            <InboxPage />
          </Console>
        }
      />
      <Route
        path="/app/accounts"
        element={
          <Console permission="accounts:read">
            <AccountsPage />
          </Console>
        }
      />
      <Route
        path="/app/accounts/:accountId"
        element={
          <Console permission="accounts:read">
            <AccountDetailPage />
          </Console>
        }
      />
      <Route
        path="/app/transactions"
        element={
          <Console permission="transactions:read">
            <TransactionsPage />
          </Console>
        }
      />
      <Route
        path="/app/transactions/new"
        element={
          <Console>
            <NewTransactionPage />
          </Console>
        }
      />
      <Route
        path="/app/transactions/:transactionId"
        element={
          <Console permission="transactions:read">
            <TransactionDetailPage />
          </Console>
        }
      />
      <Route
        path="/app/approvals"
        element={
          <Console permission="drafts:read">
            <ApprovalsPage />
          </Console>
        }
      />
      <Route
        path="/app/ledger"
        element={
          <Console permission="transactions:read">
            <LedgerPage />
          </Console>
        }
      />
      <Route
        path="/app/flight-recorder"
        element={
          <Console permission="accounts:read">
            <FlightRecorderPage />
          </Console>
        }
      />
      <Route
        path="/app/investigations"
        element={
          <Console permission="investigations:read">
            <InvestigationsPage />
          </Console>
        }
      />
      <Route
        path="/app/investigations/:caseId"
        element={
          <Console permission="investigations:read">
            <InvestigationDetailPage />
          </Console>
        }
      />
      <Route
        path="/app/reconciliation"
        element={
          <Console permission="reconciliation:read">
            <ReconciliationPage />
          </Console>
        }
      />
      <Route
        path="/app/alerts"
        element={
          <Console permission="alerts:read">
            <AlertsPage />
          </Console>
        }
      />
      <Route
        path="/app/audit"
        element={
          <Console permission="audit:read">
            <AuditPage />
          </Console>
        }
      />
      <Route
        path="/app/system"
        element={
          <Console permission="system:read">
            <SystemHealthPage />
          </Console>
        }
      />
      <Route
        path="/app/intelligence"
        element={
          <Console permission="ai:use">
            <IntelligencePage />
          </Console>
        }
      />
      <Route
        path="/app/admin/users"
        element={
          <Console permission="users:manage">
            <UsersPage />
          </Console>
        }
      />
      <Route
        path="/app/admin/api-keys"
        element={
          <Console permission="api_keys:manage">
            <ApiKeysPage />
          </Console>
        }
      />
      <Route
        path="/app/admin/settings"
        element={
          <Console permission="org:manage">
            <SettingsPage />
          </Console>
        }
      />
      <Route
        path="/app/about"
        element={
          <Console>
            <AboutPage />
          </Console>
        }
      />
      <Route path="/app/*" element={<Navigate to="/app" replace />} />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
