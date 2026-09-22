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
import Broadcasts from "./pages/Broadcasts";
import Discounts from "./pages/Discounts";
import Messages from "./pages/Messages";
import Orders from "./pages/Orders";
import Inventory from "./pages/Inventory";
import Reports from "./pages/Reports";
import Promotions from "./pages/Promotions";

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
          <Route path="/orders" element={<Protected title="Orders"><Orders /></Protected>} />
          <Route path="/reports" element={<Protected title="Rekap & Laporan"><Reports /></Protected>} />
          <Route path="/inventory" element={<Protected title="Inventory"><Inventory /></Protected>} />
          <Route path="/deposits" element={<Protected title="Kelola Deposit"><Deposits /></Protected>} />
          <Route path="/users" element={<Protected title="Kelola Pengguna"><UsersPage /></Protected>} />
          <Route path="/settings" element={<Protected title="Pengaturan"><SettingsPage /></Protected>} />
          <Route path="/broadcasts" element={<Protected title="Broadcast"><Broadcasts /></Protected>} />
          <Route path="/discounts" element={<Protected title="Discount"><Discounts /></Protected>} />
          <Route path="/messages" element={<Protected title="Bot Messages"><Messages /></Protected>} />
          <Route path="/promotions" element={<Protected title="Promosi / Cari Pelanggan"><Promotions /></Protected>} />
        </Routes>
      </BrowserRouter>
      <Toaster position="top-right" theme="dark" richColors />
    </AuthProvider>
  );
}

export default App;
