import { useEffect, useState } from "react";
import { BadgePercent, Megaphone, Users, Send, RefreshCw, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

const cls = "w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-cyan-500/60";

const emptyCoupon = {
  code: "", type: "percent", value: "", currency: "IDR", quota_total: "", per_user_limit: 1,
  min_purchase: 0, product_ids: [], starts_at: "", ends_at: "", active: true,
};

export default function Promotions() {
  const [tab, setTab] = useState("overview");
  const [summary, setSummary] = useState({});
  const [coupons, setCoupons] = useState([]);
  const [form, setForm] = useState(emptyCoupon);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const [s, c] = await Promise.all([
        api.get("/admin/promo/summary"),
        api.get("/admin/promo/coupons"),
      ]);
      setSummary(s.data);
      setCoupons(c.data);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    }
  };

  useEffect(() => { load(); }, []);

  const createCoupon = async () => {
    if (!form.code.trim() || !Number(form.value)) {
      toast.error("Kode dan nilai kupon wajib diisi.");
      return;
    }
    setBusy(true);
    try {
      await api.post("/admin/promo/coupons", {
        ...form,
        code: form.code.trim().toUpperCase(),
        value: Number(form.value),
        quota_total: form.quota_total ? Number(form.quota_total) : null,
        per_user_limit: Number(form.per_user_limit || 1),
        min_purchase: Number(form.min_purchase || 0),
        product_ids: [],
        starts_at: form.starts_at ? new Date(form.starts_at).toISOString() : null,
        ends_at: form.ends_at ? new Date(form.ends_at).toISOString() : null,
      });
      toast.success("Kupon dibuat.");
      setForm(emptyCoupon);
      await load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const removeCoupon = async (id) => {
    if (!window.confirm("Hapus kupon ini?")) return;
    try {
      await api.delete("/admin/promo/coupons/" + id);
      await load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    }
  };

  const tabs = [
    ["overview", "Ringkasan"],
    ["coupons", "Kupon"],
    ["campaigns", "Kampanye"],
    ["accounts", "Akun Telegram"],
    ["prospects", "Prospek"],
    ["groups", "Posting Grup"],
    ["results", "Hasil"],
  ];

  return (
    <div className="space-y-5">
      <div className="flex gap-2 overflow-x-auto pb-1">
        {tabs.map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)}
            className={"px-4 py-2 rounded-lg text-sm whitespace-nowrap border " +
              (tab === id ? "border-cyan-500/30 bg-cyan-500/10 text-cyan-300" : "border-slate-800 text-slate-400 hover:text-slate-200")}>
            {label}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
            {[
              ["Kupon", summary.coupons || 0, BadgePercent],
              ["Prospek", summary.prospects || 0, Users],
              ["Kampanye", summary.campaigns || 0, Megaphone],
              ["Akun Telegram", summary.telegram_accounts || 0, Send],
              ["Grup", summary.groups || 0, Megaphone],
            ].map(([label, value, Icon]) => (
              <div key={label} className="bg-slate-900/80 border border-slate-800 rounded-xl p-4">
                <Icon size={17} className="text-cyan-400 mb-3" />
                <p className="text-2xl font-semibold">{value}</p>
                <p className="text-xs text-slate-500 mt-1">{label}</p>
              </div>
            ))}
          </div>
          <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5">
            <h2 className="font-heading font-semibold">Promosi / Cari Pelanggan</h2>
            <p className="text-sm text-slate-400 mt-2">
              Modul sudah terpasang di Admin Panel. Pengiriman MTProto, antrean follow-up,
              sinkron grup, atribusi, dan laporan akan memakai antrean database serta kontrol opt-out.
            </p>
          </div>
        </>
      )}

      {tab === "coupons" && (
        <div className="grid grid-cols-1 xl:grid-cols-[420px_1fr] gap-5">
          <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-4">
            <div className="flex items-center gap-2"><BadgePercent size={18} className="text-cyan-400" /><h2 className="font-heading font-semibold">Buat Kupon</h2></div>
            <input className={cls} placeholder="Kode kupon, contoh: SEPTEMBER10" value={form.code} onChange={e => setForm({...form, code:e.target.value})} />
            <div className="grid grid-cols-2 gap-2">
              <select className={cls} value={form.type} onChange={e => setForm({...form,type:e.target.value})}>
                <option value="percent">Persen (%)</option><option value="fixed">Nominal</option>
              </select>
              <input type="number" className={cls} placeholder="Nilai" value={form.value} onChange={e => setForm({...form,value:e.target.value})} />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <select className={cls} value={form.currency} onChange={e => setForm({...form,currency:e.target.value})}>
                <option value="IDR">IDR</option><option value="USD">USD</option>
              </select>
              <input type="number" className={cls} min="1" placeholder="Batas / user" value={form.per_user_limit} onChange={e => setForm({...form,per_user_limit:e.target.value})} />
            </div>
            <input type="number" className={cls} min="0" placeholder="Kuota total (kosong = unlimited)" value={form.quota_total} onChange={e => setForm({...form,quota_total:e.target.value})} />
            <input type="number" className={cls} min="0" placeholder="Minimum belanja" value={form.min_purchase} onChange={e => setForm({...form,min_purchase:e.target.value})} />
            <div className="grid grid-cols-2 gap-2">
              <input type="datetime-local" className={cls} value={form.starts_at} onChange={e => setForm({...form,starts_at:e.target.value})} />
              <input type="datetime-local" className={cls} value={form.ends_at} onChange={e => setForm({...form,ends_at:e.target.value})} />
            </div>
            <button disabled={busy} onClick={createCoupon} className="w-full bg-cyan-600 hover:bg-cyan-700 disabled:opacity-50 rounded-lg py-2.5 font-semibold flex items-center justify-center gap-2">
              <Plus size={16} /> Buat Kupon
            </button>
          </div>

          <div className="bg-slate-900/80 border border-slate-800 rounded-xl overflow-x-auto">
            <div className="px-5 py-4 border-b border-slate-800 flex items-center justify-between">
              <div><h2 className="font-heading font-semibold">Kupon</h2><p className="text-xs text-slate-500 mt-1">{coupons.length} kupon.</p></div>
              <button onClick={load} className="p-2 text-slate-400 hover:text-slate-100"><RefreshCw size={15}/></button>
            </div>
            <table className="w-full text-left text-sm">
              <thead><tr className="border-b border-slate-800 text-xs text-slate-500">
                <th className="px-4 py-3">Kode</th><th className="px-4 py-3">Diskon</th><th className="px-4 py-3">Pemakaian</th><th className="px-4 py-3">Status</th><th />
              </tr></thead>
              <tbody>
                {coupons.map(c => (
                  <tr key={c._id} className="border-b border-slate-800/70">
                    <td className="px-4 py-3 font-mono">{c.code}</td>
                    <td className="px-4 py-3">{c.type === "percent" ? c.value + "%" : c.currency + " " + c.value}</td>
                    <td className="px-4 py-3">{c.used_count || 0}{c.quota_total == null ? "" : " / " + c.quota_total}</td>
                    <td className="px-4 py-3">{c.active ? "Aktif" : "Nonaktif"}</td>
                    <td className="px-4 py-3 text-right"><button onClick={() => removeCoupon(c._id)} className="text-rose-400"><Trash2 size={15}/></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!coupons.length && <p className="p-5 text-sm text-slate-500">Belum ada kupon.</p>}
          </div>
        </div>
      )}

      {tab !== "overview" && tab !== "coupons" && (
        <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-8 text-center">
          <div className="mx-auto w-12 h-12 rounded-xl bg-cyan-500/10 flex items-center justify-center mb-4"><Megaphone className="text-cyan-400" /></div>
          <h2 className="font-heading font-semibold text-lg">{tabs.find(x => x[0] === tab)?.[1]}</h2>
          <p className="text-sm text-slate-500 mt-2">Fondasi modul sudah tersedia. Bagian ini akan diaktifkan bertahap sesuai urutan implementasi.</p>
        </div>
      )}
    </div>
  );
}
