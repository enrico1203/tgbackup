import { BrowserRouter, Navigate, Route, Routes } from "react-router";

import Shell from "./components/Shell";
import { useAuth } from "./lib/auth";
import { ProgressProvider } from "./lib/progress";
import Accounts from "./pages/Accounts";
import ChangePassword from "./pages/ChangePassword";
import Dashboard from "./pages/Dashboard";
import Downloads from "./pages/Downloads";
import Explorer from "./pages/Explorer";
import Export from "./pages/Export";
import Files from "./pages/Files";
import Jobs from "./pages/Jobs";
import Login from "./pages/Login";
import Maintenance from "./pages/Maintenance";
import Runs from "./pages/Runs";
import Settings from "./pages/Settings";
import TelegramBots from "./pages/TelegramBots";
import { Spinner } from "./components/ui";

export default function App() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="auth-screen">
        <Spinner size={26} />
      </div>
    );
  }

  if (!user) return <Login />;

  // Until the initial password has been changed the backend rejects every other route,
  // so the interface does not even show the rest of the application.
  if (user.must_change_password) return <ChangePassword />;

  return (
    <ProgressProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<Dashboard />} />
            <Route path="jobs" element={<Jobs />} />
            <Route path="downloads" element={<Downloads />} />
            <Route path="accounts" element={<Accounts />} />
            <Route path="bots" element={<TelegramBots />} />
            <Route path="explorer" element={<Explorer />} />
            <Route path="files" element={<Files />} />
            <Route path="runs" element={<Runs />} />
            <Route path="export" element={<Export />} />
            <Route path="maintenance" element={<Maintenance />} />
            <Route path="settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ProgressProvider>
  );
}
