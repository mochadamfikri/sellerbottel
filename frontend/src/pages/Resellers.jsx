import { useEffect, useState } from "react";
import { Bot, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

const cls = "w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-cyan-500/60";
const idr = (value) => `Rp ${Number(value || 0).toLocaleString("id-ID")}`;

export default function Resellers() {
  const [config, setConfig] = useState({ enabled: false, bot_price_idr: 0, admin_fee_idr: 0, platform_fee_idr: 0, wholesale_reduction_idr: 2000 });
  const [bots, setBots] = useState([]);
  const [payouts, setPayouts] = useState([]);
  const [detail, setDetail] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const [settings, botList, payoutList] = await Promise.all([
        api.get("/admin/resellers/settings"), api.get("/admin/resellers"), api.get("/admin/resellers/payouts"),
      ]);
      setConfig(settings.data);
      setBots(botList.data || []);
      setPayouts(payoutList.data || []);
    } catch (err) { toast.error(formatApiErrorDetail(err.response?.data?.detail) || "Gagal memuat bot reseller."); }
  };
  useEffect(() => { load(); }, []);

  const openBot = async (id) => {
    try { setDetail((await api.get(`/admin/resellers/${id}`)).data); }
    catch (err) { toast.error(formatApiErrorDetail(err.response?.data?.detail) || "Detail gagal dimuat."); }
  };
  const save = async () => {
    setBusy(true);
    try {
      setConfig((await api.put("/admin/resellers/settings", config)).data);
      toast.success("Pengaturan reseller disimpan.");
    } catch (err) { toast.error(formatApiErrorDetail(err.response?.data?.detail) || "Gagal menyimpan."); }
    finally { setBusy(false); }
  };
  const act = async (url, body = {}) => {
    setBusy(true);
    try {
      await api.post(url, body);
      toast.success("Berhasil diproses.");
      await load();
      if (detail) await openBot(detail._id);
    } catch (err) { toast.error(formatApiErrorDetail(err.response?.data?.detail) || "Aksi gagal."); }
    finally { setBusy(false); }
  };

  const settingFields = [
    ["bot_price_idr", "Harga bot reseller / bulan"],
    ["admin_fee_idr", "Admin fee / bulan"],
    ["platform_fee_idr", "Admin platform / bulan"],
    ["wholesale_reduction_idr", "Potongan modal dari harga pusat"],
  ];

  return <div className="space-y-5">
    <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5 space-y-4">
      <div><h2 className="font-heading text-lg font-semibold flex items-center gap-2"><Bot size={20} className="text-cyan-400" /> Bot Reseller</h2>
        <p className="text-sm text-slate-400 mt-1">Atur langganan bulanan dan modal produk. Semua pembayaran pembeli masuk ke sistem pusat.</p></div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={config.enabled} onChange={(event) => setConfig({ ...config, enabled: event.target.checked })} /> Buka pendaftaran bot reseller</label>
      <div className="grid gap-3 sm:grid-cols-2">{settingFields.map(([key, label]) => <div key={key}>
        <label className="text-xs text-slate-300">{label} (IDR)</label>
        <input type="number" min="0" className={cls} value={config[key]} onChange={(event) => setConfig({ ...config, [key]: Number(event.target.value) })} />
      </div>)}</div>
      <p className="text-xs text-slate-400">Total langganan: <b>{idr(config.bot_price_idr + config.admin_fee_idr + config.platform_fee_idr)}</b> per bulan. Modal = harga pusat − {idr(config.wholesale_reduction_idr)}; markup owner tetap mengikuti perubahan harga pusat.</p>
      <p className="text-xs text-amber-300">Bot reseller tanpa penjualan berbayar selama 14 hari akan nonaktif otomatis. Biaya langganan yang sudah dibayar tidak dikembalikan.</p>
      <button disabled={busy} onClick={save} className="rounded-lg bg-cyan-600 hover:bg-cyan-700 px-4 py-2.5 text-sm font-semibold disabled:opacity-50">Simpan Pengaturan</button>
    </div>

    <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5">
      <div className="flex justify-between mb-4"><h3 className="font-semibold">Bot Terdaftar ({bots.length})</h3><button onClick={load} title="Refresh"><RefreshCw size={16} /></button></div>
      <div className="space-y-2">{bots.map((bot) => <button key={bot._id} onClick={() => openBot(bot._id)} className="w-full text-left rounded-lg border border-slate-800 bg-slate-950/50 p-3 hover:border-cyan-700">
        <div className="flex flex-wrap justify-between gap-2"><span className="font-semibold text-slate-100">@{bot.username}</span><span className="text-xs text-cyan-300">{bot.status}</span></div>
        <p className="text-xs text-slate-400 mt-1">Owner {bot.owner_tid} · Admin {bot.admin_tid || "-"} · {bot.user_count} pengguna · {bot.completed_orders} pesanan selesai</p>
        <p className="text-xs text-slate-500 mt-1">Penjualan {idr(bot.sales_idr)} · Komisi {idr(bot.profit_idr)} · Aktif sampai {bot.expires_at?.slice(0, 10) || "-"}</p>
        <p className="text-xs text-slate-500 mt-1">Penjualan terakhir {bot.last_sale_at?.slice(0, 16) || "Belum ada"}{bot.inactivation_reason ? ` · ${bot.inactivation_reason}` : ""}</p>
      </button>)}{!bots.length && <p className="text-sm text-slate-500">Belum ada bot reseller.</p>}</div>
    </div>

    {detail && <div className="rounded-xl border border-cyan-500/30 bg-slate-900/80 p-5 space-y-4">
      <div className="flex justify-between gap-3"><h3 className="font-semibold">Detail @{detail.username}</h3><button onClick={() => setDetail(null)} className="text-sm text-slate-400">Tutup</button></div>
      <div className="grid gap-3 sm:grid-cols-3 text-sm">
        <div className="rounded-lg bg-slate-950 p-3">Pengguna<br /><b>{detail.user_count}</b></div>
        <div className="rounded-lg bg-slate-950 p-3">Penjualan selesai<br /><b>{idr(detail.sales_idr)}</b></div>
        <div className="rounded-lg bg-slate-950 p-3">Komisi belum cair<br /><b>{idr(detail.commission_balance?.pending_payout || 0)}</b></div>
      </div>
      <p className="text-xs text-slate-400">Admin ID {detail.admin_tid} · Markup default {idr(detail.default_markup_idr)} · {detail.price_count} harga produk khusus</p>
      <p className="text-xs text-slate-400">Penjualan terakhir: {detail.last_sale_at?.slice(0, 16) || "Belum ada"} · Siklus dibayar: {detail.last_cycle_paid_at?.slice(0, 16) || "-"}{detail.inactivation_reason ? ` · ${detail.inactivation_reason}` : ""}</p>
      {detail.status === "active" && <button disabled={busy} onClick={() => act(`/admin/resellers/${detail._id}/pause`)} className="rounded-lg bg-amber-700 px-3 py-2 text-sm disabled:opacity-50">Jeda Bot</button>}
      {detail.status === "paused" && <button disabled={busy} onClick={() => act(`/admin/resellers/${detail._id}/resume`)} className="rounded-lg bg-emerald-700 px-3 py-2 text-sm disabled:opacity-50">Aktifkan Lagi</button>}
      <div><h4 className="font-medium mb-2">Pengguna terbaru</h4><div className="max-h-36 overflow-auto text-xs text-slate-400 space-y-1">{detail.recent_users?.map((user) => <p key={user.telegram_id}>{user.first_name || user.username || "Pengguna"} · {user.telegram_id} · {user.last_seen_at?.slice(0, 16)}</p>)}{!detail.recent_users?.length && <p>Belum ada pengguna.</p>}</div></div>
      <div><h4 className="font-medium mb-2">Pesanan terbaru</h4><div className="max-h-40 overflow-auto text-xs text-slate-400 space-y-1">{detail.recent_orders?.map((order) => <div key={order._id} className="flex flex-wrap justify-between gap-2"><span>{order.invoice_id} · {order.user_tid} · {order.status} · {idr(order.total)}</span>{order.status === "service_waiting" && <button disabled={busy} onClick={() => act(`/admin/resellers/${detail._id}/orders/${order._id}/complete`)} className="text-cyan-300">Tandai jasa selesai</button>}</div>)}{!detail.recent_orders?.length && <p>Belum ada pesanan.</p>}</div></div>
    </div>}

    <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5 space-y-4">
      <h3 className="font-semibold">Pencairan Komisi</h3>
      <p className="text-xs text-slate-400">Transfer manual setelah menerima pengingat dari bot pusat. Bank transfer dianjurkan. E-wallet dipotong Rp2.500.</p>
      {payouts.map((payout) => <div key={payout._id} className="rounded-lg border border-slate-800 p-3 text-sm">
        <div className="flex flex-wrap justify-between gap-2"><b>@{payout.bot_username}</b><span className="text-xs text-cyan-300">{payout.status}</span></div>
        <p className="text-xs text-slate-400 mt-1">Owner {payout.owner_tid} · Kotor {idr(payout.amount)} · Fee {idr(payout.transfer_fee)} · <b>Transfer {idr(payout.net_amount || payout.amount)}</b></p>
        <p className="text-xs text-slate-400 mt-1">{payout.destination?.type} {payout.destination?.provider} · {payout.destination?.number} · {payout.destination?.name}</p>
        {payout.status === "pending_transfer" && <div className="flex gap-2 mt-3">
          <button disabled={busy} onClick={() => { const reference = window.prompt("Masukkan referensi transfer setelah uang dikirim:"); if (reference !== null) act(`/admin/resellers/payouts/${payout._id}/paid`, { transfer_reference: reference }); }} className="rounded-lg bg-emerald-700 px-3 py-1.5 text-xs disabled:opacity-50">Tandai Sudah Transfer</button>
          <button disabled={busy} onClick={() => { if (window.confirm("Batalkan permintaan pencairan ini? Komisi kembali ke saldo tertunda.")) act(`/admin/resellers/payouts/${payout._id}/cancel`); }} className="rounded-lg bg-slate-700 px-3 py-1.5 text-xs disabled:opacity-50">Batalkan</button>
        </div>}
      </div>)}
      {!payouts.length && <p className="text-sm text-slate-500">Belum ada permintaan pencairan.</p>}
    </div>
  </div>;
}
