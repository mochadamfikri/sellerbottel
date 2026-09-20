import { useEffect, useState } from "react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

export default function Inventory() {
  const [products, setProducts] = useState([]);
  const [pid, setPid] = useState("");
  const [content, setContent] = useState("");
  const [items, setItems] = useState([]);
  const [preview, setPreview] = useState(null);
  const [file, setFile] = useState(null);

  const loadProducts = () => api.get("/admin/products").then(({ data }) => { setProducts(data.filter((p) => p.delivery_type === "inventory")); if (!pid && data.length) setPid((data.find((p) => p.delivery_type === "inventory") || {}). _id || ""); });
  const loadItems = () => pid && api.get("/admin/products/" + pid + "/inventory", { params: { status: "all" } }).then(({ data }) => setItems(data));
  useEffect(() => { loadProducts(); }, []);
  useEffect(() => { loadItems(); }, [pid]);

  const validate = async () => {
    try {
      const input = { content };
      const { data } = await api.post("/admin/products/" + pid + "/inventory/validate", input);
      setPreview(data);
    } catch (err) { toast.error(formatApiErrorDetail(err.response?.data?.detail)); }
  };

  const importItems = async () => {
    try {
      const fd = new FormData();
      fd.append("content", content);
      if (file) fd.append("file", file);
      await api.post("/admin/products/" + pid + "/inventory/import", fd);
      toast.success("Inventory berhasil ditambahkan");
      setContent(""); setFile(null); setPreview(null); loadItems(); loadProducts();
    } catch (err) { toast.error(formatApiErrorDetail(err.response?.data?.detail)); }
  };

  const cls = "w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-100";
  return (
    <div className="space-y-4">
      <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-3">
        <select className={cls} value={pid} onChange={(e) => setPid(e.target.value)}>
          <option value="">Pilih product inventory</option>
          {products.map((p) => <option key={p._id} value={p._id}>{p.name}</option>)}
        </select>
        <textarea rows={10} className={cls} placeholder="email:password satu per baris" value={content} onChange={(e) => setContent(e.target.value)} />
        <input type="file" accept=".txt,.csv,.xlsx" className={cls} onChange={(e) => setFile(e.target.files?.[0] || null)} />
        <div className="flex gap-2"><button disabled={!pid || (!content.trim() && !file)} onClick={validate} className="px-4 py-2 rounded-lg border border-slate-700 text-slate-200">Validasi Preview</button><button disabled={!pid || (!content.trim() && !file)} onClick={importItems} className="px-4 py-2 rounded-lg bg-emerald-600 text-white">Import</button></div>
        {preview && <div className="text-sm text-slate-400">Valid: <b>{preview.valid_count}</b> · Duplikat: <b>{preview.duplicate_count}</b></div>}
      </div>
      <div className="bg-slate-900/80 border border-slate-800 rounded-xl overflow-x-auto">
        <table className="w-full text-left text-sm"><thead><tr className="border-b border-slate-800 text-xs text-slate-500 uppercase"><th className="px-4 py-3">Item</th><th className="px-4 py-3">Status</th><th className="px-4 py-3">Order</th><th className="px-4 py-3">User</th><th className="px-4 py-3">Tanggal</th></tr></thead>
        <tbody>{items.map((i) => <tr key={i._id} className="border-b border-slate-800/60"><td className="px-4 py-3 font-mono text-xs">{i.item || "—"}</td><td className="px-4 py-3">{i.status}</td><td className="px-4 py-3 font-mono text-xs">{i.order_id || "—"}</td><td className="px-4 py-3">{i.user_tid || "—"}</td><td className="px-4 py-3 text-xs text-slate-500">{i.sold_at || i.created_at}</td></tr>)}</tbody></table>
      </div>
    </div>
  );
}
