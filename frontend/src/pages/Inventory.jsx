import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Upload, Plus, Trash2, RefreshCw, Database, PackageCheck } from "lucide-react";
import api, { formatApiErrorDetail } from "../lib/api";

const cls = "w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-cyan-500/60";

export default function Inventory() {
  const [products, setProducts] = useState([]);
  const [pid, setPid] = useState("");
  const [meta, setMeta] = useState({ schema: [], items: [], available: 0, reserved: 0, sold: 0 });
  const [status, setStatus] = useState("available");
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [manualData, setManualData] = useState({});
  const [fileName, setFileName] = useState("");
  const actionLock = useRef(false);
  const fileInputRef = useRef(null);

  const selectedProduct = useMemo(() => products.find((p) => p._id === pid) || null, [products, pid]);

  const loadProducts = useCallback(async () => {
    try {
      const { data } = await api.get("/admin/products");
      const digital = data.filter((p) => p.product_kind !== "service");
      setProducts(digital);
      if (!pid && digital.length) setPid(digital[0]._id);
      if (pid && !digital.some((p) => p._id === pid)) setPid(digital[0]?._id || "");
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || "Gagal memuat product inventory.");
    }
  }, [pid]);

  const loadInventory = useCallback(async () => {
    if (!pid) {
      setMeta({ schema: [], items: [], available: 0, reserved: 0, sold: 0 });
      return;
    }
    try {
      const { data } = await api.get("/admin/products/" + pid + "/inventory", { params: { status } });
      setMeta(data);
      const next = {};
      (data.schema || []).forEach((field) => { next[field] = ""; });
      setManualData((current) => Object.keys(current).length ? current : next);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || "Gagal memuat inventory.");
    }
  }, [pid, status]);

  useEffect(() => { loadProducts(); }, [loadProducts]);
  useEffect(() => { loadInventory(); }, [loadInventory]);

  const selectProduct = (value) => {
    setPid(value);
    setFile(null);
    setFileName("");
    setPreview(null);
    setManualData({});
  };

  const validateFile = async (selectedFile = file, selectedPid = pid) => {
    if (!selectedFile && fileInputRef.current?.files?.[0]) selectedFile = fileInputRef.current.files[0];
    if (!selectedFile && fileInputRef.current?.files?.[0]) selectedFile = fileInputRef.current.files[0];
    if (!selectedPid || !selectedFile) {
      toast.error("Pilih product dan file inventory terlebih dahulu.");
      return;
    }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("content", "");
      fd.append("file", selectedFile, selectedFile.name);
      const { data } = await api.post("/admin/products/" + selectedPid + "/inventory/validate", fd);
      setPreview(data);
      const next = {};
      (data.schema || []).forEach((field) => { next[field] = ""; });
      setManualData(next);
      toast.success("Valid: " + data.valid_count + " · Duplikat: " + data.duplicate_count);
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || "Validasi gagal.");
    } finally {
      setBusy(false);
    }
  };

  const importFile = async (selectedFile = file, selectedPid = pid) => {
    if (!selectedPid || !selectedFile) {
      toast.error("Pilih product dan file inventory terlebih dahulu.");
      return;
    }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("content", "");
      fd.append("file", selectedFile, selectedFile.name);
      const { data } = await api.post("/admin/products/" + selectedPid + "/inventory/import", fd);
      toast.success("Inventory masuk: " + data.created + " item · dilewati: " + data.skipped);
      setFile(null);
      setFileName("");
      setPreview(null);
      await loadProducts();
      await loadInventory();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || "Import inventory gagal.");
    } finally {
      setBusy(false);
    }
  };

  const addManual = async () => {
    if (!pid) return;
    setBusy(true);
    try {
      await api.post("/admin/products/" + pid + "/inventory/manual", { data: manualData });
      toast.success("1 data inventory ditambahkan.");
      const cleared = {};
      (meta.schema || []).forEach((field) => { cleared[field] = ""; });
      setManualData(cleared);
      await loadProducts();
      await loadInventory();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || "Gagal menambah data inventory.");
    } finally {
      setBusy(false);
    }
  };

  const removeItem = async (itemId) => {
    if (!window.confirm("Hapus item inventory yang masih tersedia ini?")) return;
    try {
      await api.delete("/admin/products/" + pid + "/inventory/" + itemId);
      toast.success("Item dihapus dari inventory.");
      await loadProducts();
      await loadInventory();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || "Gagal menghapus item.");
    }
  };

  const selectedStock = selectedProduct?.stock_mode === "manual"
    ? Math.min(Number(selectedProduct?.manual_stock ?? selectedProduct?.stock ?? 0), Number(meta.available ?? 0))
    : Number(meta.available ?? 0);

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_auto_auto_auto] gap-3 items-end">
        <div>
          <label className="text-xs text-slate-400">Pilih product inventory</label>
          <select className={cls} value={pid} onChange={(e) => selectProduct(e.target.value)}>
            <option value="">Pilih product</option>
            {products.map((p) => <option key={p._id} value={p._id}>{p.name}</option>)}
          </select>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/70 px-4 py-2.5">
          <div className="text-[10px] uppercase tracking-widest text-slate-500">Available</div>
          <div className="font-mono font-bold text-emerald-400">{selectedStock}</div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/70 px-4 py-2.5">
          <div className="text-[10px] uppercase tracking-widest text-slate-500">Reserved</div>
          <div className="font-mono font-bold text-amber-400">{meta.reserved}</div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/70 px-4 py-2.5">
          <div className="text-[10px] uppercase tracking-widest text-slate-500">Sold</div>
          <div className="font-mono font-bold text-slate-300">{meta.sold}</div>
        </div>
      </div>

      {selectedProduct && (
        <div className="bg-slate-900/70 border border-slate-800 rounded-xl px-4 py-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-slate-200 font-semibold"><Database size={16} className="text-cyan-400" /> {selectedProduct.name}</div>
            <p className="text-xs text-slate-500 mt-1">Semua upload dan input manual di halaman ini hanya masuk ke product yang sedang dipilih.</p>
          </div>
          <div className="text-xs text-slate-500">Schema: <span className="text-slate-300 font-mono">{(meta.schema || []).join(" · ") || "Belum ada"}</span></div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-4">
          <div>
            <h2 className="font-heading font-semibold flex items-center gap-2"><Upload size={17} className="text-emerald-400" /> Upload Bulk Inventory</h2>
            <p className="text-xs text-slate-500 mt-1">Pilih product dulu, lalu upload file. XLSX/CSV memakai baris pertama sebagai nama field.</p>
          </div>
          <input
            type="file"
            accept=".xlsx,.csv,.txt"
            ref={fileInputRef}
            className={cls}
            onChange={(e) => {
              const selected = e.target.files?.[0] || null;
              setFile(selected);
              setFileName(selected?.name || "");
              setPreview(null);
            }}
          />
          {fileName && <p className="text-xs text-cyan-400 mt-2 break-all">File dipilih: {fileName}</p>}
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (actionLock.current || busy) return;
                actionLock.current = true;
                Promise.resolve(validateFile(file, pid)).finally(() => { actionLock.current = false; });
              }}
              className="flex-1 px-4 py-2.5 rounded-lg border border-slate-700 text-slate-200 disabled:opacity-40"
            >
              {busy ? "Memproses..." : "Validasi"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (actionLock.current || busy) return;
                actionLock.current = true;
                Promise.resolve(importFile(file, pid)).finally(() => { actionLock.current = false; });
              }}
              className="flex-1 px-4 py-2.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white disabled:opacity-40"
            >
              {busy ? "Memproses..." : "Import Bulk"}
            </button>
          </div>
          {preview && (
            <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-xs text-slate-300 space-y-1">
              <div>Schema: <b>{(preview.schema || []).join(" · ")}</b></div>
              <div>Valid baru: <b>{preview.valid_count}</b> · Duplikat: <b>{preview.duplicate_count}</b></div>
              {!!preview.preview?.length && <div className="mt-2 text-slate-500">Preview record: {JSON.stringify(preview.preview[0])}</div>}
            </div>
          )}
        </div>

        <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-4">
          <div>
            <h2 className="font-heading font-semibold flex items-center gap-2"><Plus size={17} className="text-cyan-400" /> Input Data Manual</h2>
            <p className="text-xs text-slate-500 mt-1">Form ini memakai field inventory milik product yang sedang dipilih.</p>
          </div>
          {!meta.schema?.length ? (
            <div className="rounded-lg border border-dashed border-slate-800 p-4 text-sm text-slate-500">Schema belum tersedia. Upload satu file XLSX/CSV dulu untuk menentukan field product ini.</div>
          ) : (
            <>
              <div className="space-y-3">
                {meta.schema.map((field) => (
                  <div key={field}>
                    <label className="text-xs text-slate-400">{field}</label>
                    <input
                      className={cls}
                      value={manualData[field] || ""}
                      onChange={(e) => setManualData({ ...manualData, [field]: e.target.value })}
                      placeholder={field}
                    />
                  </div>
                ))}
              </div>
              <button type="button" disabled={!pid || busy || !Object.values(manualData).some((v) => String(v || "").trim())} onClick={addManual} className="w-full px-4 py-2.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-semibold disabled:opacity-40">Simpan 1 Data</button>
            </>
          )}
        </div>
      </div>

      <div className="bg-slate-900/80 border border-slate-800 rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-800 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-heading font-semibold flex items-center gap-2"><PackageCheck size={17} className="text-cyan-400" /> Data Inventory</h2>
            <p className="text-xs text-slate-500 mt-1">Item tersedia dapat dihapus. Data item yang sudah terjual tidak ditampilkan kembali di panel untuk mencegah kebocoran kredensial.</p>
          </div>
          <div className="flex gap-2">
            <select className="bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="available">Available</option>
              <option value="reserved">Reserved</option>
              <option value="sold">Sold</option>
              <option value="all">Semua</option>
            </select>
            <button type="button" onClick={loadInventory} className="p-2 rounded-lg border border-slate-800 text-slate-400 hover:text-cyan-400" title="Refresh"><RefreshCw size={16} /></button>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-xs text-slate-500 uppercase">
                <th className="px-5 py-3">Data</th>
                <th className="px-5 py-3">Status</th>
                <th className="px-5 py-3">Order</th>
                <th className="px-5 py-3">Telegram ID</th>
                <th className="px-5 py-3">Tanggal</th>
                <th className="px-5 py-3 text-right">Aksi</th>
              </tr>
            </thead>
            <tbody>
              {(meta.items || []).map((item) => (
                <tr key={item._id} className="border-b border-slate-800/60 align-top">
                  <td className="px-5 py-3">
                    {item.item ? (
                      <div className="space-y-1 text-xs font-mono">
                        {(meta.schema || Object.keys(item.item)).map((field) => (
                          <div key={field}><span className="text-slate-500">{field}:</span> <span className="text-slate-200 break-all">{item.item?.[field] ?? ""}</span></div>
                        ))}
                      </div>
                    ) : (
                      <span className="text-xs text-slate-600">Data disembunyikan untuk item terjual.</span>
                    )}
                  </td>
                  <td className="px-5 py-3">
                    <span className={
                      item.status === "available" ? "text-emerald-400" :
                      item.status === "reserved" ? "text-amber-400" : "text-slate-400"
                    }>{item.status}</span>
                  </td>
                  <td className="px-5 py-3 font-mono text-xs text-slate-400">{item.order_id || "—"}</td>
                  <td className="px-5 py-3 font-mono text-xs text-slate-400">{item.user_tid || "—"}</td>
                  <td className="px-5 py-3 text-xs text-slate-500">{item.sold_at || item.created_at}</td>
                  <td className="px-5 py-3 text-right">
                    {item.status === "available" && (
                      <button type="button" onClick={() => removeItem(item._id)} className="p-2 rounded-lg text-slate-500 hover:text-rose-400 hover:bg-rose-500/10" title="Hapus item"><Trash2 size={15} /></button>
                    )}
                  </td>
                </tr>
              ))}
              {!meta.items?.length && <tr><td colSpan={6} className="px-5 py-10 text-center text-slate-500">Belum ada data inventory untuk product ini.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
