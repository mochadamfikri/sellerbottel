import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Landmark, QrCode, ShieldCheck } from "lucide-react";
import api, { fmtIDR, formatApiErrorDetail } from "../lib/api";
import { Switch } from "../components/ui/switch";

const COINS = ["USDT", "USDC"];
const NETS = [
  { key: "SOL", label: "Solana" },
  { key: "POL", label: "Polygon" },
  { key: "BNB", label: "BNB (BEP-20)" },
  { key: "AVAX", label: "Avalanche" },
];

const cls = "mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-100 font-mono focus:outline-none focus:border-cyan-500/60";

export default function SettingsPage() {
  const [s, setS] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.get("/admin/settings")
      .then(({ data }) => setS(data))
      .catch((err) => toast.error(formatApiErrorDetail(err.response?.data?.detail) || "Gagal memuat pengaturan."));
  }, []);

  if (!s) return <p className="text-slate-500 text-sm">Memuat...</p>;

  const save = async () => {
    setSaving(true);
    try {
      const { data } = await api.put("/admin/settings", {
        crypto_addresses: s.crypto_addresses,
        bank_name: s.bank_name,
        bank_account_number: s.bank_account_number,
        bank_account_holder: s.bank_account_holder,
        qris_enabled: !!s.qris_enabled,
        bank_enabled: !!s.bank_enabled,
        min_deposit_usd: parseFloat(s.min_deposit_usd),
        min_deposit_idr: parseFloat(s.min_deposit_idr),
        admin_telegram_id: String(s.admin_telegram_id || ""),
        rate_mode: s.rate_mode,
        manual_rate: parseFloat(s.manual_rate),
        max_deposit_usd: parseFloat(s.max_deposit_usd || 100000),
        max_deposit_idr: parseFloat(s.max_deposit_idr || 100000000),
        join_gate_enabled: !!s.join_gate_enabled,
        join_gate_fail_open: !!s.join_gate_fail_open,
        required_channels: s.required_channels || [],
      });
      setS(data);
      toast.success("Pengaturan disimpan.");
    } catch (err) {
      toast.error(formatApiErrorDetail(err.response?.data?.detail) || "Gagal menyimpan pengaturan.");
    } finally {
      setSaving(false);
    }
  };

  const setAddr = (key, val) => setS({ ...s, crypto_addresses: { ...s.crypto_addresses, [key]: val } });

  return (
    <div className="space-y-5">
      <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-4">
        <div>
          <h2 className="font-heading font-semibold">Gateway Pembayaran</h2>
          <p className="text-xs text-slate-500 mt-1">Atur metode deposit IDR yang tampil di bot. Jika keduanya OFF, bot menampilkan notifikasi gateway sedang mengalami gangguan.</p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="flex gap-3">
                <div className="w-9 h-9 rounded-lg bg-cyan-500/10 flex items-center justify-center"><QrCode size={18} className="text-cyan-400" /></div>
                <div>
                  <p className="font-semibold text-slate-200">QRIS +0.7% otomatis</p>
                  <p className="text-xs text-slate-500 mt-1">Nominal QR dibuat otomatis dan pembayaran dipantau oleh GoPay Merchant.</p>
                </div>
              </div>
              <Switch checked={!!s.qris_enabled} onCheckedChange={(v) => setS({ ...s, qris_enabled: v })} />
            </div>
            <div className="mt-3 text-xs">
              <span className={s.qris_enabled ? "text-emerald-400" : "text-slate-600"}>{s.qris_enabled ? "ON" : "OFF"}</span>
              {s.qris_enabled && <span className="text-slate-600 ml-2">Pastikan GOPAY_ENABLED di server aktif.</span>}
            </div>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="flex gap-3">
                <div className="w-9 h-9 rounded-lg bg-amber-500/10 flex items-center justify-center"><Landmark size={18} className="text-amber-400" /></div>
                <div>
                  <p className="font-semibold text-slate-200">Bank Transfer 0 fees — manual checking</p>
                  <p className="text-xs text-slate-500 mt-1">Transfer bank menggunakan bukti foto dan persetujuan admin.</p>
                </div>
              </div>
              <Switch checked={!!s.bank_enabled} onCheckedChange={(v) => setS({ ...s, bank_enabled: v })} />
            </div>
            <div className="mt-3 text-xs"><span className={s.bank_enabled ? "text-emerald-400" : "text-slate-600"}>{s.bank_enabled ? "ON" : "OFF"}</span></div>
          </div>
        </div>
      </div>

      <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5">
        <h2 className="font-heading font-semibold mb-1">Alamat Deposit Crypto</h2>
        <p className="text-xs text-slate-500 mb-4">Isi alamat wallet untuk setiap kombinasi koin × jaringan. Kosongkan jika tidak menerima.</p>
        {COINS.map((coin) => (
          <div key={coin} className="mb-4">
            <p className="text-sm font-semibold text-cyan-400 mb-2">{coin}</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {NETS.map((n) => (
                <div key={n.key}>
                  <label className="text-xs text-slate-400">{n.label}</label>
                  <input className={cls} placeholder={"Alamat " + coin + " di " + n.label}
                    value={s.crypto_addresses?.[coin + "_" + n.key] || ""}
                    onChange={(e) => setAddr(coin + "_" + n.key, e.target.value)} />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-3">
          <h2 className="font-heading font-semibold">Rekening Bank (Deposit IDR)</h2>
          <div><label className="text-xs text-slate-400">Nama Bank</label><input className={cls} placeholder="cth: BCA" value={s.bank_name || ""} onChange={(e) => setS({ ...s, bank_name: e.target.value })} /></div>
          <div><label className="text-xs text-slate-400">Nomor Rekening</label><input className={cls} value={s.bank_account_number || ""} onChange={(e) => setS({ ...s, bank_account_number: e.target.value })} /></div>
          <div><label className="text-xs text-slate-400">Atas Nama</label><input className={cls} value={s.bank_account_holder || ""} onChange={(e) => setS({ ...s, bank_account_holder: e.target.value })} /></div>
        </div>

        <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-3">
          <h2 className="font-heading font-semibold">Deposit & Admin</h2>
          <div className="grid grid-cols-2 gap-3">
            <div><label className="text-xs text-slate-400">Min. Deposit USD</label><input type="number" className={cls} value={s.min_deposit_usd} onChange={(e) => setS({ ...s, min_deposit_usd: e.target.value })} /></div>
            <div><label className="text-xs text-slate-400">Min. Deposit IDR</label><input type="number" className={cls} value={s.min_deposit_idr} onChange={(e) => setS({ ...s, min_deposit_idr: e.target.value })} /></div>
          </div>
          <div><label className="text-xs text-slate-400">Telegram ID Admin</label><input className={cls} value={s.admin_telegram_id || ""} onChange={(e) => setS({ ...s, admin_telegram_id: e.target.value })} /></div>

          <div className="pt-2 border-t border-slate-800 space-y-3">
            <h3 className="text-sm font-semibold">Batas Deposit</h3>
            <div className="grid grid-cols-2 gap-3">
              <div><label className="text-xs text-slate-400">Maks. Deposit USD</label><input type="number" className={cls} value={s.max_deposit_usd || ""} onChange={(e) => setS({ ...s, max_deposit_usd: e.target.value })} /></div>
              <div><label className="text-xs text-slate-400">Maks. Deposit IDR</label><input type="number" className={cls} value={s.max_deposit_idr || ""} onChange={(e) => setS({ ...s, max_deposit_idr: e.target.value })} /></div>
            </div>

            <h3 className="text-sm font-semibold">Kurs USD → IDR</h3>
            <div className="flex items-center justify-between">
              <span className="text-sm text-slate-400">Mode Manual</span>
              <Switch checked={s.rate_mode === "manual"} onCheckedChange={(v) => setS({ ...s, rate_mode: v ? "manual" : "auto" })} />
            </div>
            {s.rate_mode === "manual" ? (
              <div><label className="text-xs text-slate-400">Kurs Manual (1 USD = ? IDR)</label><input type="number" className={cls} value={s.manual_rate} onChange={(e) => setS({ ...s, manual_rate: e.target.value })} /></div>
            ) : (
              <p className="text-sm text-slate-400">Kurs otomatis saat ini: <span className="font-mono text-cyan-400">{fmtIDR(s.current_rate)}</span></p>
            )}
          </div>
        </div>
      </div>

      <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-4">
        <div className="flex items-center justify-between">
          <div><h2 className="font-heading font-semibold">Wajib Join Channel</h2><p className="text-xs text-slate-500">Bot akan memeriksa membership saat pengguna melakukan action.</p></div>
          <Switch checked={!!s.join_gate_enabled} onCheckedChange={(v) => setS({ ...s, join_gate_enabled: v })} />
        </div>
        <div className="flex items-center justify-between">
          <span className="text-sm text-slate-400">Fail-open saat Telegram error</span>
          <Switch checked={s.join_gate_fail_open !== false} onCheckedChange={(v) => setS({ ...s, join_gate_fail_open: v })} />
        </div>

        {(s.required_channels || []).map((ch, index) => (
          <div key={index} className="grid grid-cols-1 md:grid-cols-4 gap-2 p-3 rounded-lg border border-slate-800 bg-slate-950/50">
            <input className={cls} placeholder="Channel ID / @username" value={ch.channel_id || ""} onChange={(e) => {
              const next = [...(s.required_channels || [])];
              next[index] = { ...next[index], channel_id: e.target.value };
              setS({ ...s, required_channels: next });
            }} />
            <input className={cls} placeholder="Judul" value={ch.title || ""} onChange={(e) => {
              const next = [...(s.required_channels || [])];
              next[index] = { ...next[index], title: e.target.value };
              setS({ ...s, required_channels: next });
            }} />
            <input className={cls} placeholder="Invite link (private)" value={ch.invite_link || ""} onChange={(e) => {
              const next = [...(s.required_channels || [])];
              next[index] = { ...next[index], invite_link: e.target.value, enabled: next[index]?.enabled !== false };
              setS({ ...s, required_channels: next });
            }} />
            <button className="rounded-lg border border-rose-500/20 text-rose-400 hover:bg-rose-500/10 text-sm" onClick={() => setS({ ...s, required_channels: (s.required_channels || []).filter((_, i) => i !== index) })}>Hapus</button>
          </div>
        ))}

        {(s.required_channels || []).length < 3 && (
          <button className="text-sm text-cyan-400 hover:text-cyan-300" onClick={() => setS({
            ...s,
            required_channels: [...(s.required_channels || []), { channel_id: "", title: "", invite_link: "", enabled: true }]
          })}>+ Tambah channel</button>
        )}
      </div>

      <button onClick={save} disabled={saving} className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm font-semibold rounded-lg px-6 py-2.5">
        {saving ? "Menyimpan..." : "Simpan Pengaturan"}
      </button>
    </div>
  );
}
