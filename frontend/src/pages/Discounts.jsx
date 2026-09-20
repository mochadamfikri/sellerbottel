import { useEffect, useState } from "react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

const empty = { name: "", product_ids: [], mode: "percent", value: "", min_qty: 1, max_qty: "", active: true, priority: 0 };

export default function Discounts() {
  const [discounts, setDiscounts] = useState([]);
  const [products, setProducts] = useState([]);
  const [form, setForm] = useState(empty);
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    const d = await api.get("/admin/discounts");
    const p = await api.get("/admin/products");
    setDiscounts(d.data);
    setProducts(p.data);
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    setBusy(true);
    try {
      const payload = {
        ...form,
        value: parseFloat(form.value),
        min_qty: parseInt(form.min_qty || 1, 10),
        max_qty: form.max_qty ? parseInt(form.max_qty, 10) : null,
        priority: parseInt(form.priority || 0, 10),
      };
      if (editing) await api.put("/admin/discounts/" + editing, payload);
      else await api.post("/admin/discounts", payload);
      toast.success(editing ? "Discount diperbarui" : "Discount dibuat");
      setForm(empty); setEditing(null); load();
    } catch (err) { toast.error(formatApiErrorDetail(err.response?.data?.detail)); }
    finally { setBusy(false); }
  };

  const toggle = async (d) => { await api.patch("/admin/discounts/" + d._id + "/toggle"); load(); };
  const remove = async (d) => { if (window.confirm("Hapus discount ini?")) { await api.delete("/admin/discounts/" + d._id); load(); } };
  const cls = "w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-cyan-500/60";

  return (
    <>
      <div className="grid grid-cols-1 lg:grid-cols-[380px_1fr] gap-5">
        <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-3">
          <h2 className="font-heading font-semibold">{editing ? "Edit Discount" : "Tambah Discount"}</h2>
          <input className={cls} placeholder="Nama discount" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <select className={cls} value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value })}><option value="percent">Persen (%)</option><option value="fixed">Nominal / unit</option></select>
          <input className={cls} type="number" placeholder="Nilai" value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} />
          <div className="grid grid-cols-2 gap-2">
            <input className={cls} type="number" min="1" placeholder="Min qty" value={form.min_qty} onChange={(e) => setForm({ ...form, min_qty: e.target.value })} />
            <input className={cls} type="number" min="1" placeholder="Max qty" value={form.max_qty} onChange={(e) => setForm({ ...form, max_qty: e.target.value })} />
          </div>
          <input className={cls} type="number" placeholder="Priority" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} />
          <select className={cls} multiple size={8} value={form.product_ids} onChange={(e) => setForm({ ...form, product_ids: Array.from(e.target.selectedOptions).map((o) => o.value) })}>
            {products.map((p) => <option key={p._id} value={p._id}>{p.name}</option>)}
          </select>
          <p className="text-xs text-slate-500">Kosongkan pilihan produk = berlaku ke semua produk.</p>
          <div className="flex gap-2">
            <button onClick={save} disabled={busy || !form.name || !form.value} className="flex-1 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-semibold rounded-lg py-2.5">{editing ? "Update" : "Buat"}</button>
            {editing && <button onClick={() => { setEditing(null); setForm(empty); }} className="px-4 rounded-lg border border-slate-800 text-slate-300">Batal</button>}
          </div>
        </div>
        <div className="bg-slate-900/80 border border-slate-800 rounded-xl overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead><tr className="border-b border-slate-800 text-xs text-slate-500 uppercase"><th className="px-4 py-3">Nama</th><th className="px-4 py-3">Mode</th><th className="px-4 py-3">Qty</th><th className="px-4 py-3">Produk</th><th className="px-4 py-3">Status</th><th className="px-4 py-3 text-right">Aksi</th></tr></thead>
            <tbody>
              {discounts.map((d) => (
                <tr key={d._id} className="border-b border-slate-800/60">
                  <td className="px-4 py-3">{d.name}<div className="text-xs text-slate-500">prioritas {d.priority}</div></td>
                  <td className="px-4 py-3 font-mono">{d.mode === "percent" ? d.value + "%" : d.value}</td>
                  <td className="px-4 py-3">{d.min_qty}{d.max_qty ? "–" + d.max_qty : "+"}</td>
                  <td className="px-4 py-3 text-xs">{d.product_ids?.length ? d.product_ids.length + " produk" : "Semua"}</td>
                  <td className="px-4 py-3">{d.active ? "Aktif" : "Off"}</td>
                  <td className="px-4 py-3 text-right space-x-1">
                    <button className="text-cyan-400 text-xs" onClick={() => { setEditing(d._id); setForm({ ...d, value: String(d.value), max_qty: d.max_qty || "", product_ids: d.product_ids || [] }); }}>Edit</button>
                    <button className="text-slate-400 text-xs" onClick={() => toggle(d)}>Toggle</button>
                    <button className="text-rose-400 text-xs" onClick={() => remove(d)}>Hapus</button>
                  </td>
                </tr>
              ))}
              {!discounts.length && <tr><td colSpan={6} className="px-4 py-10 text-center text-slate-500">Belum ada discount.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
