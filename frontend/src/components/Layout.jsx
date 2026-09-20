import { NavLink, useNavigate } from "react-router-dom";
import { LayoutDashboard, Package, Wallet, Users, Settings, LogOut, Bot, Megaphone, Percent, MessageSquareText, ClipboardList, Boxes, BarChart3 } from "lucide-react";
import { useAuth } from "../context/AuthContext";

const nav = [
  { to: "/", label: "Ringkasan", icon: LayoutDashboard },
  { to: "/products", label: "Produk", icon: Package },
  { to: "/inventory", label: "Inventory", icon: Boxes },
  { to: "/orders", label: "Orders", icon: ClipboardList },
  { to: "/reports", label: "Rekap", icon: BarChart3 },
  { to: "/deposits", label: "Deposit", icon: Wallet },
  { to: "/users", label: "Pengguna", icon: Users },
  { to: "/discounts", label: "Discount", icon: Percent },
  { to: "/broadcasts", label: "Broadcast", icon: Megaphone },
  { to: "/messages", label: "Bot Messages", icon: MessageSquareText },
  { to: "/settings", label: "Pengaturan", icon: Settings },
];

export const Layout = ({ children, title }) => {
  const { logout } = useAuth();
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-[#0B0F17] text-slate-100 flex">
      <aside className="hidden md:flex w-[240px] flex-col fixed inset-y-0 border-r border-slate-800 bg-[#0D1220]">
        <div className="flex items-center gap-2.5 px-5 h-16 border-b border-slate-800">
          <div className="w-8 h-8 rounded-lg bg-cyan-500/15 border border-cyan-500/30 flex items-center justify-center">
            <Bot className="w-4.5 h-4.5 text-cyan-400" size={18} />
          </div>
          <div>
            <p className="font-heading font-bold text-sm tracking-tight">TokoBot</p>
            <p className="text-[10px] text-slate-500 font-mono uppercase tracking-widest">Admin Panel</p>
          </div>
        </div>
        <nav className="flex-1 p-3 space-y-1">
          {nav.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              data-testid={`nav-${label.toLowerCase()}`}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                  isActive ? "bg-cyan-500/10 text-cyan-400 border border-cyan-500/20" : "text-slate-400 hover:text-slate-100 hover:bg-slate-800/60"
                }`
              }
            >
              <Icon size={17} />
              {label}
            </NavLink>
          ))}
        </nav>
        <button
          data-testid="logout-button"
          onClick={async () => { await logout(); navigate("/login"); }}
          className="flex items-center gap-3 px-6 py-4 text-sm text-slate-400 hover:text-rose-400 border-t border-slate-800 transition-colors"
        >
          <LogOut size={17} /> Keluar
        </button>
      </aside>

      <div className="flex-1 md:ml-[240px]">
        <header className="sticky top-0 z-40 h-16 flex items-center justify-between px-4 sm:px-6 bg-slate-900/90 backdrop-blur-md border-b border-slate-800">
          <h1 className="font-heading text-lg font-bold tracking-tight">{title}</h1>
          <div className="md:hidden flex gap-2">
            {nav.map(({ to, icon: Icon }) => (
              <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => `p-2 rounded-lg ${isActive ? "text-cyan-400 bg-cyan-500/10" : "text-slate-400"}`}>
                <Icon size={18} />
              </NavLink>
            ))}
          </div>
        </header>
        <main className="p-4 sm:p-6 lg:p-8 space-y-6 max-w-7xl mx-auto">{children}</main>
      </div>
    </div>
  );
};
