import { useEffect, useState } from "react";
import api, { fmtAmount, fmtDate, formatApiErrorDetail } from "../lib/api";
import { toast } from "sonner";

const statuses = ["all", "pending", "paid", "delivered", "delivery_failed", "failed", "refunded"];

export default function Orders() {
  const [status, setStatus] = useState("all");
  const [search, setSearch] = useState("");
  const [orders, setOrders] = useState([]);

  const load = () => api.get("/admin/orders", { params: { status, search } }).then(({ data }) => setOrders(data));
  useEffect(() => { load(); }, [status, search]);

  const refund = async (o) => {
    if (!window.confirm("Refund order ini dan kembalikan saldo?")) return;
    try {
      await api.post("/admin/orders/" + o._id + "/refund");
      toast.success("Order direfund");
      load();
    } catch (err) { toast.error(formatApiErrorDetail(err.response?.data?.detail)); }
  };

  return (
    <>
      <div className="flex gap-2 flex-wrap">
        {statuses.map((s) => <button key={s} onClick={() => setStatus(s)} className={status === s ? "px-3 py-1.5 rounded-lg border border-cyan-500/40 text-cyan-400 text-xs" : "px-3 py-1.5 rounded-lg border border-slate-800 text-slate-400 text-xs"}>{s}</button>)}
        <input className="ml-auto w-64 bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm" placeholder="Invoice / username / Telegram ID" value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>
      <div className="bg-slate-900/80 border border-slate-800 rounded-xl overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead><tr className="border-b border-slate-800 text-xs text-slate-500 uppercase"><th className="px-4 py-3">Invoice</th><th className="px-4 py-3">User</th><th className="px-4 py-3">Produk</th><th className="px-4 py-3">Total</th><th className="px-4 py-3">Status</th><th className="px-4 py-3">Tanggal</th><th className="px-4 py-3 text-right">Aksi</th></tr></thead>
          <tbody>
            {orders.map((o) => <tr key={o._id} className="border-b border-slate-800/60">
              <td className="px-4 py-3 font-mono text-cyan-400">{o.invoice_id}</td>
              <td className="px-4 py-3 text-xs">{o.username || o.user_tid}<div className="text-slate-500">{o.user_tid}</div></td>
              <td className="px-4 py-3 text-xs">{(o.items || []).map((i) => i.name + " ×" + i.qty).join(", ")}</td>
              <td className="px-4 py-3 font-mono">{fmtAmount(o.total, o.currency)}</td>
              <td className="px-4 py-3"><span className="text-xs uppercase">{o.status}</span></td>
              <td className="px-4 py-3 text-xs text-slate-500">{fmtDate(o.created_at)}</td>
              <td className="px-4 py-3 text-right">{(o.status === "failed" || o.status === "delivery_failed") && <button className="text-rose-400 text-xs" onClick={() => refund(o)}>Refund</button>}</td>
            </tr>)}
            {!orders.length && <tr><td colSpan={7} className="px-4 py-10 text-center text-slate-500">Belum ada order.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
