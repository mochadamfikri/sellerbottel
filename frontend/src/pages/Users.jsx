import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Wallet, Snowflake, Sun } from "lucide-react";
import api, { fmtUSD, fmtIDR, fmtDate, formatApiErrorDetail } from "../lib/api";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "../components/ui/dialog";

export default function UsersPage() {
  const [users, setUsers] = useState([]);
  const [adjust, setAdjust] = useState(null);
  const [adjForm, setAdjForm] = useState({ currency: "USD", amount: "", reason: "" });
  const [freeze, setFreeze] = useState(null);
  const [freezeReason, setFreezeReason] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => api.get("/admin/users").then(({ data }) => setUsers(data));
  useEffect(() => { load(); }, []);

  const doAdjust = async () => {
    setBusy(true);
    try {
      await api.post(`/admin/users/${adjust.telegram_id}/adjust`, { ...adjForm, amount: parseFloat(adjForm.amount) });
      toast.success("Saldo disesuaikan");
      setAdjust(null);
      load();
    } catch (err) { toast.error(formatApiErrorDetail(err.response?.data?.detail)); }
    setBusy(false);
  };

  const doFreeze = async () => {
    setBusy(true);
    try {
      await api.post(`/admin/users/${freeze.telegram_id}/freeze`, { reason: freezeReason });
      toast.success("Pengguna dibekukan");
      setFreeze(null); setFreezeReason("");
      load();
    } catch (err) { toast.error(formatApiErrorDetail(err.response?.data?.detail)); }
    setBusy(false);
  };

  const doUnfreeze = async (u) => {
    try {
      await api.post(`/admin/users/${u.telegram_id}/unfreeze`);
      toast.success("Blokir dibuka");
      load();
    } catch (err) { toast.error(formatApiErrorDetail(err.response?.data?.detail)); }
  };

  const inputCls = "mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-cyan-500/60";

  return (
    <>
      <div data-testid="user-list-table" className="bg-slate-900/60 border border-slate-800 rounded-xl overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-xs text-slate-500 uppercase tracking-wide">
              <th className="px-4 py-3">Pengguna</th><th className="px-4 py-3">Mata Uang</th>
              <th className="px-4 py-3">Saldo USD</th><th className="px-4 py-3">Saldo IDR</th>
              <th className="px-4 py-3">Order</th><th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Bergabung</th><th className="px-4 py-3 text-right">Aksi</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u._id} className="border-b border-slate-800/60 hover:bg-slate-800/30">
                <td className="px-4 py-3">
                  <p className="text-slate-200">{u.first_name || "-"}</p>
                  <p className="text-xs text-slate-500 font-mono">{u.username ? `@${u.username}` : ""} {u.telegram_id}</p>
                </td>
                <td className="px-4 py-3 font-mono text-slate-300">{u.currency || "-"}</td>
                <td className="px-4 py-3 font-mono">{fmtUSD(u.balance_usd)}</td>
                <td className="px-4 py-3 font-mono">{fmtIDR(u.balance_idr)}</td>
                <td className="px-4 py-3 font-mono text-slate-400">{u.purchase_count}</td>
                <td className="px-4 py-3">
                  {u.frozen ? (
                    <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded border bg-red-500/15 text-red-400 border-red-500/30">Dibekukan</span>
                  ) : (
                    <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded border bg-cyan-500/15 text-cyan-400 border-cyan-500/30">Aktif</span>
                  )}
                </td>
                <td className="px-4 py-3 text-xs text-slate-500">{fmtDate(u.created_at)}</td>
                <td className="px-4 py-3 text-right">
                  <div className="flex gap-1.5 justify-end">
                    <button data-testid={`adjust-balance-btn-${u.telegram_id}`} title="Sesuaikan saldo"
                      onClick={() => { setAdjust(u); setAdjForm({ currency: u.currency || "USD", amount: "", reason: "" }); }}
                      className="p-2 rounded-lg text-slate-400 hover:text-cyan-400 hover:bg-slate-800"><Wallet size={15} /></button>
                    {u.frozen ? (
                      <button data-testid={`unban-user-btn-${u.telegram_id}`} title="Buka blokir" onClick={() => doUnfreeze(u)}
                        className="p-2 rounded-lg text-emerald-400 hover:bg-emerald-500/10"><Sun size={15} /></button>
                    ) : (
                      <button data-testid={`ban-user-btn-${u.telegram_id}`} title="Bekukan" onClick={() => { setFreeze(u); setFreezeReason(""); }}
                        className="p-2 rounded-lg text-slate-400 hover:text-rose-400 hover:bg-slate-800"><Snowflake size={15} /></button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {users.length === 0 && <tr><td colSpan={8} className="px-4 py-10 text-center text-slate-500">Belum ada pengguna bot.</td></tr>}
          </tbody>
        </table>
      </div>

      <Dialog open={!!adjust} onOpenChange={() => setAdjust(null)}>
        <DialogContent className="bg-slate-900 border-slate-800 text-slate-100 max-w-sm">
          <DialogHeader><DialogTitle className="font-heading">Sesuaikan Saldo</DialogTitle></DialogHeader>
          {adjust && (
            <div className="space-y-3">
              <p className="text-sm text-slate-400">{adjust.first_name} — {adjust.telegram_id}</p>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-slate-400">Mata Uang</label>
                  <select data-testid="adjust-currency-select" className={inputCls} value={adjForm.currency} onChange={(e) => setAdjForm({ ...adjForm, currency: e.target.value })}>
                    <option value="USD">USD</option><option value="IDR">IDR</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-slate-400">Jumlah (+/-)</label>
                  <input data-testid="adjust-amount-input" type="number" step="0.01" className={inputCls} placeholder="cth: 10 atau -5" value={adjForm.amount} onChange={(e) => setAdjForm({ ...adjForm, amount: e.target.value })} />
                </div>
              </div>
              <div>
                <label className="text-xs text-slate-400">Alasan</label>
                <input data-testid="adjust-reason-input" className={inputCls} value={adjForm.reason} onChange={(e) => setAdjForm({ ...adjForm, reason: e.target.value })} />
              </div>
              <button data-testid="confirm-adjust-button" onClick={doAdjust} disabled={busy || !adjForm.amount}
                className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm font-semibold rounded-lg py-2.5">
                {busy ? "Memproses..." : "Simpan Penyesuaian"}
              </button>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={!!freeze} onOpenChange={() => setFreeze(null)}>
        <DialogContent className="bg-slate-900 border-slate-800 text-slate-100 max-w-sm">
          <DialogHeader><DialogTitle className="font-heading">Bekukan Pengguna</DialogTitle></DialogHeader>
          {freeze && (
            <div className="space-y-3">
              <p className="text-sm text-slate-400">{freeze.first_name} — {freeze.telegram_id}</p>
              <div>
                <label className="text-xs text-slate-400">Alasan (opsional, dikirim ke pengguna)</label>
                <textarea data-testid="freeze-reason-input" rows={2} className={inputCls} value={freezeReason} onChange={(e) => setFreezeReason(e.target.value)} />
              </div>
              <button data-testid="confirm-freeze-button" onClick={doFreeze} disabled={busy}
                className="w-full bg-rose-600 hover:bg-rose-700 disabled:opacity-50 text-white text-sm font-semibold rounded-lg py-2.5">
                {busy ? "Memproses..." : "Bekukan Akun"}
              </button>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
