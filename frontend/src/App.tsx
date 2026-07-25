import { BrowserRouter, Navigate, Route, Routes } from "react-router";

import Shell from "./components/Shell";
import { useAuth } from "./lib/auth";
import { ProgressProvider } from "./lib/progress";
import Accounts from "./pages/Accounts";
import ChangePassword from "./pages/ChangePassword";
import Dashboard from "./pages/Dashboard";
import Files from "./pages/Files";
import Jobs from "./pages/Jobs";
import Login from "./pages/Login";
import Runs from "./pages/Runs";
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

  // Finche la password iniziale non e stata cambiata il backend rifiuta ogni altra
  // rotta, quindi l'interfaccia non mostra nemmeno il resto dell'applicazione.
  if (user.must_change_password) return <ChangePassword />;

  return (
    <ProgressProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<Dashboard />} />
            <Route path="jobs" element={<Jobs />} />
            <Route path="accounts" element={<Accounts />} />
            <Route path="files" element={<Files />} />
            <Route path="runs" element={<Runs />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ProgressProvider>
  );
}
