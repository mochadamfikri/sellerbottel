import { useEffect, useState } from "react";
import { Send, RefreshCw, Image as ImageIcon } from "lucide-react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

const cls = "w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-cyan-500/60";
const initial = { message: "", product_ids: [], summaries: {}, target: "chats" };

export default function Broadcasts() {
  const [form, setForm] = useState(initial);
  const [products, setProducts] = useState([]);
  const [history, setHistory] = useState([]);
  const [stockEvents, setStockEvents] = useState([]);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [search, setSearch] = useState("");

  const load = () => {
    api.get("/admin/products").then(({ data }) => setProducts(data || [])).catch(() => {});
    api.get("/admin/broadcasts").then(({ data }) => setHistory(data || [])).catch(() => {});
    api.get("/admin/broadcasts/stock-events").then(({ data }) => setStockEvents(data || [])).catch(() => {});
  };
  useEffect(() => { load(); }, []);

  const change = (value) => { setForm(value); setPreview(null); };
  const toggleProduct = (product) => {
    const selected = form.product_ids.includes(product._id);
    if (!selected && form.product_ids.length >= 10) {
      toast.error("Maksimal 10 produk dalam satu gambar.");
      return;
    }
    const next = selected ? form.product_ids.filter((id) => id !== product._id) : [...form.product_ids, product._id];
    const summaries = { ...form.summaries };
    if (!selected && !summaries[product._id]) summaries[product._id] = String(product.description || "").replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim().slice(0, 180);
    change({ ...form, product_ids: next, summaries });
  };

  const run = async (action) => {
    if (!form.message.trim() && !form.product_ids.length) {
      toast.error("Tulis pesan atau pilih produk.");
      return;
    }
    setBusy(true);
    try {
      const { data } = await api.post(`/admin/broadcasts/compose/${action}`, form);
      if (action === "preview") setPreview(data);
      if (action === "test") toast.success("Pesan tes dikirim ke admin Telegram.");
      if (action === "send") {
        const failedChats = (data.chat_results || []).filter((row) => !row.ok);
        if (failedChats.length) toast.error(`${failedChats.length} channel/grup gagal menerima pesan. Periksa riwayat dan izin bot.`);
        else toast.success(`Broadcast dikirim. ${data.queued_users || 0} pengguna masuk antrean.`);
        setForm(initial);
        setPreview(null);
        load();
      }
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || "Broadcast gagal.");
    } finally {
      setBusy(false);
    }
  };

  const available = products.filter((product) => product.active !== false &&
    (product.product_kind === "service" || product.stock == null || product.stock > 0));
  const filtered = available.filter((product) => product.name?.toLowerCase().includes(search.toLowerCase()));
  const plainPreview = (preview?.text || "").replace(/<[^>]*>/g, "");

  return (
    <div className="space-y-5">
      <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5 space-y-5">
        <div>
          <h2 className="font-heading text-lg font-semibold flex items-center gap-2"><Send size={19} className="text-cyan-400" /> Broadcast</h2>
          <p className="text-sm text-slate-400 mt-1">Satu pesan dan satu gambar untuk produk pilihan. Periksa hasilnya sebelum mengirim.</p>
        </div>
        <div>
          <label className="text-sm text-slate-300">Pesan utama</label>
          <textarea rows={4} maxLength={1000} className={cls} placeholder="Tulis pesan untuk pembeli (opsional jika memilih produk)..." value={form.message} onChange={(event) => change({ ...form, message: event.target.value })} />
        </div>
        <div>
          <div className="flex items-center justify-between gap-3 mb-2">
            <label className="text-sm text-slate-300">Pilih produk ({form.product_ids.length}/10)</label>
            <input className={`${cls} max-w-xs`} placeholder="Cari produk..." value={search} onChange={(event) => setSearch(event.target.value)} />
          </div>
          <div className="max-h-64 overflow-auto rounded-lg border border-slate-800 divide-y divide-slate-800">
            {filtered.map((product) => (
              <label key={product._id} className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-slate-800/50">
                <input type="checkbox" checked={form.product_ids.includes(product._id)} onChange={() => toggleProduct(product)} />
                <span className="flex-1 text-sm text-slate-200">{product.name}</span>
                <span className="text-xs text-slate-500">{product.product_kind === "service" ? "Unlimited" : `Stok ${product.stock}`}</span>
              </label>
            ))}
            {!filtered.length && <p className="p-3 text-sm text-slate-500">Tidak ada produk aktif dengan stok tersedia.</p>}
          </div>
        </div>
        {!!form.product_ids.length && (
          <div className="space-y-3">
            <p className="text-xs text-slate-400">Ringkasan otomatis dari deskripsi produk. Kamu bisa menyuntingnya untuk gambar dan pesan.</p>
            {form.product_ids.map((id) => {
              const product = products.find((item) => item._id === id);
              return <div key={id}>
                <label className="text-xs text-cyan-300">{product?.name || "Produk"}</label>
                <textarea rows={2} maxLength={180} className={cls} value={form.summaries[id] || ""} onChange={(event) => change({ ...form, summaries: { ...form.summaries, [id]: event.target.value } })} />
              </div>;
            })}
          </div>
        )}
        <div>
          <label className="text-sm text-slate-300">Kirim ke</label>
          <select className={cls} value={form.target} onChange={(event) => change({ ...form, target: event.target.value })}>
            <option value="chats">Channel dan grup terhubung</option>
            <option value="users">Semua pengguna bot</option>
            <option value="both">Channel, grup, dan semua pengguna</option>
          </select>
        </div>
        <button type="button" disabled={busy} onClick={() => run("preview")} className="w-full rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-50 py-3 font-semibold">{busy ? "Memproses..." : "Lihat Pratinjau"}</button>
      </div>

      {preview && <div className="rounded-xl border border-cyan-500/30 bg-slate-900/80 p-5 space-y-4">
        <h3 className="font-semibold flex items-center gap-2"><ImageIcon size={18} className="text-cyan-400" /> Pratinjau sebelum kirim</h3>
        {preview.image_data_url && <img src={preview.image_data_url} alt="Pratinjau gabungan produk" className="w-full max-w-lg rounded-lg border border-slate-700" />}
        <pre className="whitespace-pre-wrap rounded-lg bg-slate-950 border border-slate-800 p-3 text-sm text-slate-200 max-h-72 overflow-auto">{plainPreview}</pre>
        <p className="text-sm text-slate-400">Tujuan: {preview.chats?.length || 0} channel/grup dan {preview.user_count || 0} pengguna.</p>
        <div className="flex flex-wrap gap-2">
          <button type="button" disabled={busy} onClick={() => run("test")} className="rounded-lg bg-slate-700 hover:bg-slate-600 px-4 py-2.5 disabled:opacity-50">Kirim Tes ke Admin</button>
          <button type="button" disabled={busy} onClick={() => {
            if (window.confirm(`Kirim ke ${preview.chats?.length || 0} channel/grup dan ${preview.user_count || 0} pengguna?`)) run("send");
          }} className="rounded-lg bg-cyan-600 hover:bg-cyan-700 px-4 py-2.5 font-semibold disabled:opacity-50">Kirim Broadcast</button>
        </div>
      </div>}

      <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5">
        <div className="flex justify-between mb-4"><h3 className="font-semibold">Riwayat Broadcast</h3><button type="button" onClick={load} title="Refresh"><RefreshCw size={16} /></button></div>
        <div className="space-y-2">
          {history.map((item) => <div key={item._id} className="border border-slate-800 rounded-lg p-3">
            <div className="flex justify-between text-xs text-slate-500"><span>{item.status}</span><span>{item.success || 0} berhasil · {item.failed || 0} gagal</span></div>
            <p className="text-sm text-slate-200 mt-1 whitespace-pre-wrap line-clamp-3">{String(item.text || "").replace(/<[^>]*>/g, "")}</p>
          </div>)}
          {!history.length && <p className="text-sm text-slate-500">Belum ada broadcast.</p>}
        </div>
      </div>

      <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5">
        <div className="flex justify-between mb-4"><h3 className="font-semibold">Notifikasi Stok</h3><button type="button" onClick={load} title="Refresh"><RefreshCw size={16} /></button></div>
        <div className="space-y-2">
          {stockEvents.map((event) => <div key={event._id} className="border border-slate-800 rounded-lg p-3 flex items-center justify-between gap-3">
            <div className="text-sm text-slate-200">
              <span className="font-semibold">{event.event_type === "restocked" ? "Restock" : "Stok habis"}</span>
              <span className="text-slate-400"> · {event.from_count} → {event.to_count} · {event.status}</span>
              <p className="text-xs text-slate-500">{event.product_name || event.product_id} · {event.delivered?.length || 0}/{event.targets?.length || 0} tujuan terkirim</p>
            </div>
            {event.status === "pending" && <button type="button" disabled={busy} className="rounded-lg bg-slate-700 px-3 py-1.5 text-xs disabled:opacity-50" onClick={async () => {
              setBusy(true);
              try {
                await api.post(`/admin/broadcasts/stock-events/${event._id}/retry`);
                toast.success("Pengiriman ulang diproses.");
                load();
              } catch (err) { toast.error(formatApiErrorDetail(err.response?.data?.detail) || "Gagal mencoba ulang."); }
              finally { setBusy(false); }
            }}>Coba Lagi</button>}
          </div>)}
          {!stockEvents.length && <p className="text-sm text-slate-500">Belum ada perubahan stok yang perlu diumumkan.</p>}
        </div>
      </div>
    </div>
  );
}
