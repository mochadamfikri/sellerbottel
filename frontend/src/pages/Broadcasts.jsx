import { useCallback, useEffect, useState } from "react";
import { Send, RefreshCw, Megaphone } from "lucide-react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

export default function Broadcasts() {
  const [form, setForm] = useState({ text: "", lang: "all", status: "active", search: "", button_text: "", button_url: "" });
  const [photo, setPhoto] = useState(null);
  const [history, setHistory] = useState([]);
  const [busy, setBusy] = useState(false);
  const [channelMode, setChannelMode] = useState("manual");
  const [channelText, setChannelText] = useState("");
  const [productPreview, setProductPreview] = useState("");
  const [autoTarget, setAutoTarget] = useState("channel");
  const [autoContent, setAutoContent] = useState("both");
  const [autoPreview, setAutoPreview] = useState("");

  const load = () => api.get("/admin/broadcasts").then(({ data }) => setHistory(data));
  const loadProductPreview = useCallback(async () => {
    try {
      const { data } = await api.get("/admin/broadcasts/channel-product-preview");
      setProductPreview(data.text || "");
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    }
  };

  const loadAutoPreview = async () => {
    try {
      const { data } = await api.get("/admin/broadcasts/auto-preview", { params: { content: autoContent } });
      setAutoPreview(data.text || "");
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    }
  };

  useEffect(() => {
    if (channelMode === "products") loadProductPreview();
    if (channelMode === "auto") loadAutoPreview();
  }, [channelMode, autoContent]);

  useEffect(() => { load(); }, []);

  const submit = async () => {
    if (!form.text.trim()) return;
    setBusy(true);
    try {
      const previewFd = new FormData();
      previewFd.append("lang", form.lang); previewFd.append("status", form.status); previewFd.append("search", form.search);
      const { data: preview } = await api.post("/admin/broadcasts/preview", previewFd);
      if (!window.confirm(`Broadcast akan dikirim ke ${preview.total} pengguna. Lanjutkan?`)) return;

      const fd = new FormData();
      Object.entries(form).forEach(([k, v]) => fd.append(k, v));
      if (photo) fd.append("photo", photo);
      await api.post("/admin/broadcasts", fd);
      toast.success("Broadcast dimulai.");
      setForm({ ...form, text: "" });
      setPhoto(null);
      load();
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail));
    } finally { setBusy(false); }
  };

  const cls = "w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-cyan-500/60";
  return (
    <>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-4">
          <div className="flex items-center gap-2"><Send size={18} className="text-cyan-400" /><h2 className="font-heading font-semibold">Broadcast Baru</h2></div>
          <textarea rows={8} className={cls} placeholder="Pesan promosi..." value={form.text} onChange={(e) => setForm({ ...form, text: e.target.value })} />
          <div className="grid grid-cols-2 gap-3">
            <select className={cls} value={form.lang} onChange={(e) => setForm({ ...form, lang: e.target.value })}><option value="all">Semua bahasa</option><option value="id">Indonesia</option><option value="en">English</option></select>
            <select className={cls} value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}><option value="active">Pengguna aktif</option><option value="all">Semua</option><option value="frozen">Dibekukan</option></select>
          </div>
          <input className={cls} placeholder="Cari username/nama (opsional)" value={form.search} onChange={(e) => setForm({ ...form, search: e.target.value })} />
          <input type="file" accept="image/*" className={cls} onChange={(e) => setPhoto(e.target.files?.[0] || null)} />
          <div className="grid grid-cols-2 gap-3">
            <input className={cls} placeholder="Teks tombol" value={form.button_text} onChange={(e) => setForm({ ...form, button_text: e.target.value })} />
            <input className={cls} placeholder="URL tombol" value={form.button_url} onChange={(e) => setForm({ ...form, button_url: e.target.value })} />
          </div>
          <button onClick={submit} disabled={busy || !form.text.trim()} className="w-full bg-cyan-600 hover:bg-cyan-700 disabled:opacity-50 text-white font-semibold rounded-lg py-2.5">{busy ? "Mengirim..." : "Mulai Broadcast"}</button>
        </div>
        <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-4">
          <div className="flex items-center gap-2">
            <Megaphone size={18} className="text-cyan-400" />
            <h2 className="font-heading font-semibold">Broadcast ke Channel / Pengguna</h2>
          </div>
          <p className="text-xs text-slate-500">Manual dan Product Aktif / Tersedia tetap dikirim ke channel. Broadcast otomatis dapat dikirim ke channel atau semua pengguna bot.</p>
          <select className={cls} value={channelMode} onChange={(e) => setChannelMode(e.target.value)}>
            <option value="manual">1. Manual</option>
            <option value="products">2. Broadcast Product Aktif / Tersedia</option>
            <option value="auto">3. Broadcast Otomatis</option>
          </select>
          {channelMode === "manual" ? (
            <textarea rows={8} className={cls} placeholder="Tulis pesan untuk channel..." value={channelText} onChange={(e) => setChannelText(e.target.value)} />
          ) : channelMode === "products" ? (
            <div>
              <p className="text-xs text-slate-400 mb-2">Preview pesan otomatis:</p>
              <pre className="whitespace-pre-wrap text-sm text-slate-200 bg-slate-950 border border-slate-800 rounded-lg p-4 max-h-80 overflow-auto">{productPreview}</pre>
              <p className="text-xs text-slate-500 mt-2">Digital hanya ditampilkan jika stock &gt; 0. Produk jasa ditampilkan sebagai Unlimited.</p>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-slate-400">Kirim ke</label>
                  <select className={cls} value={autoTarget} onChange={(e) => setAutoTarget(e.target.value)}>
                    <option value="channel">📢 Channel</option>
                    <option value="users">👥 Semua Pengguna</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-slate-400">Yang dibroadcast</label>
                  <select className={cls} value={autoContent} onChange={(e) => setAutoContent(e.target.value)}>
                    <option value="discount">🏷️ Harga Diskon</option>
                    <option value="stock">📦 Stock Tersedia</option>
                    <option value="both">🏷️ Harga Diskon + 📦 Stock Tersedia</option>
                  </select>
                </div>
              </div>
              <div>
                <p className="text-xs text-slate-400 mb-2">Preview pesan:</p>
                <pre className="whitespace-pre-wrap text-sm text-slate-200 bg-slate-950 border border-slate-800 rounded-lg p-4 max-h-80 overflow-auto">{autoPreview}</pre>
                <p className="text-xs text-slate-500 mt-2">Harga diskon dihitung dari harga aktif saat broadcast. Untuk pengguna, broadcast masuk queue dan pengguna yang sudah memblokir bot dilewati.</p>
              </div>
            </div>
          )}
          <button
            type="button"
            disabled={busy || (channelMode === "manual" && !channelText.trim())}
            onClick={async () => {
              setBusy(true);
              try {
                const fd = new FormData();
                fd.append("mode", channelMode);
                if (channelMode === "manual") {
                  fd.append("text", channelText);
                } else if (channelMode === "auto") {
                  fd.append("target", autoTarget);
                  fd.append("content", autoContent);
                }
                await api.post("/admin/broadcasts/channel", fd);
                toast.success(channelMode === "auto" && autoTarget === "users"
                  ? "Broadcast ke semua pengguna masuk antrian."
                  : "Broadcast berhasil dikirim.");
                if (channelMode === "manual") setChannelText("");
                else if (channelMode === "products") await loadProductPreview();
                else await loadAutoPreview();
                load();
              } catch (err) {
                toast.error(formatApiErrorDetail(err.response?.data?.detail));
              } finally {
                setBusy(false);
              }
            }}
            className="w-full bg-cyan-600 hover:bg-cyan-700 disabled:opacity-50 text-white font-semibold rounded-lg py-2.5"
          >
            {busy ? "Mengirim..." : "📢 Kirim Broadcast"}
          </button>
        </div>
        <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5">
          <div className="flex items-center justify-between mb-4"><h2 className="font-heading font-semibold">Riwayat</h2><button onClick={load} className="p-2 text-slate-400 hover:text-slate-100"><RefreshCw size={15} /></button></div>
          <div className="space-y-2">
            {history.map((b) => (
              <div key={b._id} className="border border-slate-800 rounded-lg p-3">
                <div className="flex justify-between gap-2"><span className="text-xs text-slate-500">{b.status}</span><span className="font-mono text-xs text-slate-500">{b.success || 0}✓ {b.failed || 0}✗</span></div>
                <p className="text-sm text-slate-200 mt-1 whitespace-pre-wrap line-clamp-3">{b.text}</p>
              </div>
            ))}
            {!history.length && <p className="text-sm text-slate-500">Belum ada broadcast.</p>}
          </div>
        </div>
      </div>
    </>
  );
}
