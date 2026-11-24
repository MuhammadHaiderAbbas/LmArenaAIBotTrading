import { BrowserRouter, HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./features/auth/AuthContext";
import { AccountProvider } from "./features/account/AccountContext";
import { Layout, RequireAuth } from "./components/Layout";
import { ToastProvider } from "./components/ui";
import { Login } from "./pages/Login";
import { Register } from "./pages/Register";
import { Overview } from "./pages/Overview";
import { Markets } from "./pages/Markets";
import { Execution } from "./pages/Execution";
import { Risk } from "./pages/Risk";
import { AIDesk } from "./pages/AIDesk";
import { Models } from "./pages/Models";
import { Research } from "./pages/Research";
import { Strategies } from "./pages/Strategies";
import { Portfolio } from "./pages/Portfolio";
import { Wallet } from "./pages/Wallet";
import { History } from "./pages/History";
import { Analytics } from "./pages/Analytics";
import { Operations } from "./pages/Operations";
import { Readiness } from "./pages/Readiness";
import { Settings } from "./pages/Settings";
import { Admin } from "./pages/Admin";
import { IS_DEMO } from "./services/api";

// GitHub Pages cannot rewrite deep links to index.html, so the demo build uses
// hash routing. Server-hosted builds use the History API.
const Router = IS_DEMO ? HashRouter : BrowserRouter;

export function App() {
  return (
    <Router>
      <ToastProvider>
        <AuthProvider>
          <AccountProvider>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route path="/register" element={<Register />} />
              <Route
                element={
                  <RequireAuth>
                    <Layout />
                  </RequireAuth>
                }
              >
                <Route path="/overview" element={<Overview />} />
                <Route path="/markets" element={<Markets />} />
                <Route path="/trading" element={<Execution />} />
                <Route path="/risk" element={<Risk />} />
                <Route path="/research" element={<Research />} />
                <Route path="/ai" element={<AIDesk />} />
                <Route path="/models" element={<Models />} />
                <Route path="/strategies" element={<Strategies />} />
                <Route path="/portfolio" element={<Portfolio />} />
                <Route path="/wallet" element={<Wallet />} />
                <Route path="/history" element={<History />} />
                <Route path="/analytics" element={<Analytics />} />
                <Route path="/operations" element={<Operations />} />
                <Route path="/readiness" element={<Readiness />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="/admin" element={<Admin />} />
              </Route>
              <Route path="/" element={<Navigate to="/overview" replace />} />
              <Route path="*" element={<Navigate to="/overview" replace />} />
            </Routes>
          </AccountProvider>
        </AuthProvider>
      </ToastProvider>
    </Router>
  );
}
