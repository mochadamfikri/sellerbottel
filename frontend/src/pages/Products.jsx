import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Plus, Pencil, Trash2, FileText, Link2, KeyRound } from "lucide-react";
import api, { fmtUSD, fmtIDR, formatApiErrorDetail } from "../lib/api";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "../components/ui/dialog";
import { Switch } from "../components/ui/switch";

const typeIcons = { file: FileText, link: Link2, license: KeyRound };
const typeLabels = { file: "File", link: "Link", license: "Kode Lisensi" };
const empty = { name: "", description: "", price_usd: "", price_idr: "", delivery_type: "link", content: "", active: true };

export default function Products() {
  const [products, setProducts] = useState([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(empty);
  const [editId, setEditId] = useState(null);
  const [file, setFile] = useState(null);
  const [saving, setSaving] = useState(false);

  const load = () => api.get("/admin/products").then(({ data }) => setProducts(data));
  useEffect(() => { load(); }, []);

  const openCreate = () => { setForm(empty); setEditId(null); setFile(null); setOpen(true); };
  const openEdit = (p) => {
    setForm({ name: p.name, description: p.description || "", price_usd: p.price_usd, price_idr: p.price_idr || "", delivery_type: p.delivery_type, content: p.content || "", active: p.active });
    setEditId(p._id); setFile(null); setOpen(true);
  };

  const save = async () => {
    setSaving(true);
    const fd = new FormData();
    fd.append("name", form.name);
    fd.append("description", form.description);
    fd.append("price_usd", form.price_usd);
    if (form.price_idr) fd.append("price_idr", form.price_idr);
    fd.append("delivery_type", form.delivery_type);
    fd.append("content", form.content);
    fd.append("active", form.active);
    if (file) fd.append("file", file);
    try {
      if (editId) await api.put(`/admin/products/${editId}`, fd);
      else await api.post("/admin/products", fd);
      toast.success(editId ? "Produk diperbarui" : "Produk ditambahkan");
      setOpen(false);
      load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    }
    setSaving(false);
  };

  const toggle = async (p) => {
    await api.patch(`/admin/products/${p._id}/toggle`);
    toast.success(p.active ? "Produk dinonaktifkan" : "Produk diaktifkan");
    load();
  };

  const remove = async (p) => {
    if (!window.confirm(`Hapus produk "${p.name}"?`)) return;
    await api.delete(`/admin/products/${p._id}`);
    toast.success("Produk dihapus");
    load();
  };

  const inputCls = "mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-cyan-500/60";

  return (
    <>
      <div className="flex justify-between items-center">
        <p className="text-sm text-slate-400">{products.length} produk</p>
        <button data-testid="add-product-button" onClick={openCreate} className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold rounded-lg px-4 py-2 transition-colors">
          <Plus size={16} /> Tambah Produk
        </button>
      </div>

      <div data-testid="product-list-table" className="bg-slate-900/60 border border-slate-800 rounded-xl overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-xs text-slate-500 uppercase tracking-wide">
              <th className="px-4 py-3">Produk</th><th className="px-4 py-3">Tipe</th>
              <th className="px-4 py-3">Harga USD</th><th className="px-4 py-3">Harga IDR</th>
              <th className="px-4 py-3">Aktif</th><th className="px-4 py-3 text-right">Aksi</th>
            </tr>
          </thead>
          <tbody>
            {products.map((p) => {
              const Icon = typeIcons[p.delivery_type] || FileText;
              return (
                <tr key={p._id} className="border-b border-slate-800/60 hover:bg-slate-800/30">
                  <td className="px-4 py-3">
                    <p className="font-medium text-slate-200">{p.name}</p>
                    <p className="text-xs text-slate-500 line-clamp-1">{p.description}</p>
                  </td>
                  <td className="px-4 py-3"><span className="flex items-center gap-1.5 text-slate-300"><Icon size={14} className="text-cyan-400" />{typeLabels[p.delivery_type]}</span></td>
                  <td className="px-4 py-3 font-mono">{fmtUSD(p.price_usd)}</td>
                  <td className="px-4 py-3 font-mono text-slate-400">{p.price_idr ? fmtIDR(p.price_idr) : <span className="text-slate-600 text-xs">auto kurs</span>}</td>
                  <td className="px-4 py-3">
                    <Switch data-testid={`product-active-switch-${p._id}`} checked={p.active} onCheckedChange={() => toggle(p)} />
                  </td>
                  <td className="px-4 py-3 text-right space-x-1">
                    <button data-testid={`edit-product-btn-${p._id}`} onClick={() => openEdit(p)} className="p-2 rounded-lg text-slate-400 hover:text-cyan-400 hover:bg-slate-800"><Pencil size={15} /></button>
                    <button data-testid={`delete-product-btn-${p._id}`} onClick={() => remove(p)} className="p-2 rounded-lg text-slate-400 hover:text-rose-400 hover:bg-slate-800"><Trash2 size={15} /></button>
                  </td>
                </tr>
              );
            })}
            {products.length === 0 && <tr><td colSpan={6} className="px-4 py-10 text-center text-slate-500">Belum ada produk. Klik "Tambah Produk".</td></tr>}
          </tbody>
        </table>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="bg-slate-900 border-slate-800 text-slate-100 max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader><DialogTitle className="font-heading">{editId ? "Edit Produk" : "Tambah Produk"}</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-slate-400">Nama Produk</label>
              <input data-testid="product-name-input" className={inputCls} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div>
              <label className="text-xs text-slate-400">Deskripsi</label>
              <textarea data-testid="product-description-input" className={inputCls} rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-slate-400">Harga USD</label>
                <input data-testid="product-price-usd-input" type="number" step="0.01" className={inputCls} value={form.price_usd} onChange={(e) => setForm({ ...form, price_usd: e.target.value })} />
              </div>
              <div>
                <label className="text-xs text-slate-400">Harga IDR (opsional)</label>
                <input data-testid="product-price-idr-input" type="number" className={inputCls} placeholder="auto dari kurs" value={form.price_idr} onChange={(e) => setForm({ ...form, price_idr: e.target.value })} />
              </div>
            </div>
            <div>
              <label className="text-xs text-slate-400">Tipe Pengiriman</label>
              <select data-testid="product-delivery-type-select" className={inputCls} value={form.delivery_type} onChange={(e) => setForm({ ...form, delivery_type: e.target.value })}>
                <option value="link">Link</option>
                <option value="license">Kode Lisensi</option>
                <option value="file">File</option>
              </select>
            </div>
            {form.delivery_type === "file" ? (
              <div>
                <label className="text-xs text-slate-400">Upload File Produk</label>
                <input data-testid="product-file-input" type="file" className={inputCls} onChange={(e) => setFile(e.target.files[0])} />
                {editId && !file && <p className="text-xs text-slate-500 mt-1">Kosongkan jika tidak ingin mengganti file.</p>}
              </div>
            ) : (
              <div>
                <label className="text-xs text-slate-400">{form.delivery_type === "link" ? "Link Produk" : "Kode Lisensi"}</label>
                <textarea data-testid="product-content-input" className={inputCls} rows={2} value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} />
              </div>
            )}
            <div className="flex items-center gap-2">
              <Switch checked={form.active} onCheckedChange={(v) => setForm({ ...form, active: v })} />
              <span className="text-sm text-slate-300">Aktif</span>
            </div>
            <button data-testid="save-product-button" onClick={save} disabled={saving || !form.name || !form.price_usd}
              className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm font-semibold rounded-lg py-2.5 transition-colors">
              {saving ? "Menyimpan..." : "Simpan Produk"}
            </button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
