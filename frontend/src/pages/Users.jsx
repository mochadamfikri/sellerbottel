import { useCallback, useEffect, useState } from "react";
import { Wallet, Snowflake, Sun, Search } from "lucide-react";
import { toast } from "sonner";
import api, { fmtUSD, fmtIDR, fmtDate, formatApiErrorDetail } from "../lib/api";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "../components/ui/dialog";

const cls = "w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-cyan-500/60";

export default function UsersPage() {
  const [users, setUsers] = useState([]);
  const [search, setSearch] = useState("");
  const [adjust, setAdjust] = useState(null);
  const [adjForm, setAdjForm] = useState({ currency: "USD", amount: "", reason: "" });
  const [freeze, setFreeze] = useState(null);
  const [freezeReason, setFreezeReason] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (term = "") => {
    try {
      const endpoint = term.trim() ? "/admin/users/search" : "/admin/users/all";
      const { data } = await api.get(endpoint, term.trim() ? { params: { search: term } } : undefined);
      setUsers(data);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || "Gagal memuat pengguna.");
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => load(search), 250);
    return () => clearTimeout(timer);
  }, [search, load]);

  const doAdjust = async () => {
    setBusy(true);
    try {
      await api.post("/admin/users/" + adjust.telegram_id + "/adjust", { ...adjForm, amount: parseFloat(adjForm.amount) });
      toast.success("Saldo disesuaikan");
      setAdjust(null);
      await load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const doFreeze = async () => {
    setBusy(true);
    try {
      await api.post("/admin/users/" + freeze.telegram_id + "/freeze", { reason: freezeReason });
      toast.success("Pengguna dibekukan");
      setFreeze(null);
      setFreezeReason("");
      await load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const doUnfreeze = async (u) => {
    try {
      await api.post("/admin/users/" + u.telegram_id + "/unfreeze");
      toast.success("Blokir dibuka");
      await load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    }
  };

  return (
    <div className="space-y-4">
      <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-4">
        <label className="text-xs text-slate-400">Cari pengguna</label>
        <div className="relative mt-1">
          <Search size={16} className="absolute left-3 top-2.5 text-slate-600" />
          <input
            className={cls + " pl-9"}
            placeholder="Nama pengguna / username / ID Telegram"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <p className="text-xs text-slate-600 mt-2">Saat kosong, seluruh pengguna ditampilkan. Cari berdasarkan nama, @username, atau Telegram ID.</p>
        <p className="text-xs text-slate-500 mt-1">{users.length} pengguna ditampilkan</p>
      </div>

      <div className="bg-slate-900/60 border border-slate-800 rounded-xl overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-xs text-slate-500 uppercase tracking-wide">
              <th className="px-4 py-3">Nama</th>
              <th className="px-4 py-3">Username</th>
              <th className="px-4 py-3">Telegram ID</th>
              <th className="px-4 py-3">USD</th>
              <th className="px-4 py-3">IDR</th>
              <th className="px-4 py-3">Order</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Bergabung</th>
              <th className="px-4 py-3 text-right">Aksi</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u._id} className="border-b border-slate-800/60 hover:bg-slate-800/20">
                <td className="px-4 py-3 text-slate-200">{u.first_name || "-"}</td>
                <td className="px-4 py-3 font-mono text-xs text-slate-400">{u.username ? "@" + u.username : "—"}</td>
                <td className="px-4 py-3 font-mono text-xs text-slate-300">{u.telegram_id}</td>
                <td className="px-4 py-3 font-mono">{fmtUSD(u.balance_usd)}</td>
                <td className="px-4 py-3 font-mono">{fmtIDR(u.balance_idr)}</td>
                <td className="px-4 py-3 font-mono text-slate-400">{u.order_count || u.purchase_count || 0}</td>
                <td className="px-4 py-3">
                  {u.frozen
                    ? <span className="text-[10px] uppercase px-2 py-0.5 rounded border bg-red-500/15 text-red-400 border-red-500/30">Dibekukan</span>
                    : <span className="text-[10px] uppercase px-2 py-0.5 rounded border bg-cyan-500/15 text-cyan-400 border-cyan-500/30">Aktif</span>}
                </td>
                <td className="px-4 py-3 text-xs text-slate-500">{fmtDate(u.created_at)}</td>
                <td className="px-4 py-3 text-right">
                  <div className="flex justify-end gap-1.5">
                    <button onClick={() => { setAdjust(u); setAdjForm({ currency: u.currency || "USD", amount: "", reason: "" }); }} title="Sesuaikan saldo" className="p-2 rounded-lg text-slate-400 hover:text-cyan-400 hover:bg-slate-800"><Wallet size={15} /></button>
                    {u.frozen
                      ? <button onClick={() => doUnfreeze(u)} title="Buka blokir" className="p-2 rounded-lg text-emerald-400 hover:bg-emerald-500/10"><Sun size={15} /></button>
                      : <button onClick={() => { setFreeze(u); setFreezeReason(""); }} title="Bekukan" className="p-2 rounded-lg text-slate-400 hover:text-rose-400 hover:bg-slate-800"><Snowflake size={15} /></button>}
                  </div>
                </td>
              </tr>
            ))}
            {!users.length && <tr><td colSpan={9} className="px-4 py-10 text-center text-slate-500">Tidak ada pengguna yang cocok.</td></tr>}
          </tbody>
        </table>
      </div>

      <Dialog open={!!adjust} onOpenChange={() => setAdjust(null)}>
        <DialogContent className="bg-slate-900 border-slate-800 text-slate-100 max-w-sm">
          <DialogHeader><DialogTitle>Sesuaikan Saldo</DialogTitle></DialogHeader>
          {adjust && (
            <div className="space-y-3">
              <p className="text-sm text-slate-400">{adjust.first_name || "-"} · {adjust.username ? "@" + adjust.username : "—"} · {adjust.telegram_id}</p>
              <div className="grid grid-cols-2 gap-3">
                <div><label className="text-xs text-slate-400">Mata Uang</label><select className={cls} value={adjForm.currency} onChange={(e) => setAdjForm({ ...adjForm, currency: e.target.value })}><option value="USD">USD</option><option value="IDR">IDR</option></select></div>
                <div><label className="text-xs text-slate-400">Jumlah (+/-)</label><input type="number" step="0.01" className={cls} value={adjForm.amount} onChange={(e) => setAdjForm({ ...adjForm, amount: e.target.value })} /></div>
              </div>
              <input className={cls} placeholder="Alasan (opsional)" value={adjForm.reason} onChange={(e) => setAdjForm({ ...adjForm, reason: e.target.value })} />
              <button onClick={doAdjust} disabled={busy || !adjForm.amount} className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-semibold rounded-lg py-2.5">{busy ? "Memproses..." : "Simpan Penyesuaian"}</button>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={!!freeze} onOpenChange={() => setFreeze(null)}>
        <DialogContent className="bg-slate-900 border-slate-800 text-slate-100 max-w-sm">
          <DialogHeader><DialogTitle>Bekukan Pengguna</DialogTitle></DialogHeader>
          {freeze && (
            <div className="space-y-3">
              <p className="text-sm text-slate-400">{freeze.first_name || "-"} · {freeze.username ? "@" + freeze.username : "—"} · {freeze.telegram_id}</p>
              <textarea rows={3} className={cls} placeholder="Alasan (opsional, dikirim ke pengguna)" value={freezeReason} onChange={(e) => setFreezeReason(e.target.value)} />
              <button onClick={doFreeze} disabled={busy} className="w-full bg-rose-600 hover:bg-rose-700 disabled:opacity-50 text-white font-semibold rounded-lg py-2.5">{busy ? "Memproses..." : "Bekukan Akun"}</button>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
