import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "sonner";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { Layout } from "./components/Layout";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import Products from "./pages/Products";
import Deposits from "./pages/Deposits";
import UsersPage from "./pages/Users";
import SettingsPage from "./pages/SettingsPage";

function Protected({ children, title }) {
  const { user } = useAuth();
  if (user === null) return <div className="min-h-screen bg-[#0B0F17] flex items-center justify-center text-slate-500">Memuat...</div>;
  if (user === false) return <Navigate to="/login" replace />;
  return <Layout title={title}>{children}</Layout>;
}

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<Protected title="Ringkasan"><Overview /></Protected>} />
          <Route path="/products" element={<Protected title="Kelola Produk"><Products /></Protected>} />
          <Route path="/deposits" element={<Protected title="Kelola Deposit"><Deposits /></Protected>} />
          <Route path="/users" element={<Protected title="Kelola Pengguna"><UsersPage /></Protected>} />
          <Route path="/settings" element={<Protected title="Pengaturan"><SettingsPage /></Protected>} />
        </Routes>
      </BrowserRouter>
      <Toaster position="top-right" theme="dark" richColors />
    </AuthProvider>
  );
}

export default App;
