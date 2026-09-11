import { Navigate, Route, Routes } from 'react-router-dom';

import { AppShell } from './components/layout/AppShell';
import { MarketingLayout } from './components/marketing/MarketingLayout';
import AboutPage from './pages/AboutPage';
import AccountDetailPage from './pages/AccountDetailPage';
import AccountsPage from './pages/AccountsPage';
import AuditPage from './pages/AuditPage';
import DashboardPage from './pages/DashboardPage';
import LedgerPage from './pages/LedgerPage';
import AboutMarketingPage from './pages/marketing/AboutPage';
import LandingPage from './pages/marketing/LandingPage';
import LoginPage from './pages/marketing/LoginPage';
import NewTransactionPage from './pages/NewTransactionPage';
import TransactionDetailPage from './pages/TransactionDetailPage';
import TransactionsPage from './pages/TransactionsPage';

function Console({ children }: { children: React.ReactNode }) {
  return <AppShell>{children}</AppShell>;
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
      <Route path="/app" element={<Console><DashboardPage /></Console>} />
      <Route path="/app/accounts" element={<Console><AccountsPage /></Console>} />
      <Route path="/app/accounts/:accountId" element={<Console><AccountDetailPage /></Console>} />
      <Route path="/app/transactions" element={<Console><TransactionsPage /></Console>} />
      <Route path="/app/transactions/new" element={<Console><NewTransactionPage /></Console>} />
      <Route
        path="/app/transactions/:transactionId"
        element={<Console><TransactionDetailPage /></Console>}
      />
      <Route path="/app/ledger" element={<Console><LedgerPage /></Console>} />
      <Route path="/app/audit" element={<Console><AuditPage /></Console>} />
      <Route path="/app/about" element={<Console><AboutPage /></Console>} />
      <Route path="/app/*" element={<Navigate to="/app" replace />} />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
