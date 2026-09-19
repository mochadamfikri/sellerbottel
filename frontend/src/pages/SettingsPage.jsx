import { useEffect, useState } from "react";
import { toast } from "sonner";
import api, { fmtIDR, formatApiErrorDetail } from "../lib/api";
import { Switch } from "../components/ui/switch";

const COINS = ["USDT", "USDC"];
const NETS = [
  { key: "SOL", label: "Solana" }, { key: "POL", label: "Polygon" },
  { key: "BNB", label: "BNB (BEP-20)" }, { key: "AVAX", label: "Avalanche" },
];

export default function SettingsPage() {
  const [s, setS] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => { api.get("/admin/settings").then(({ data }) => setS(data)); }, []);

  if (!s) return <p className="text-slate-500 text-sm">Memuat...</p>;

  const save = async () => {
    setSaving(true);
    try {
      const { data } = await api.put("/admin/settings", {
        crypto_addresses: s.crypto_addresses,
        bank_name: s.bank_name, bank_account_number: s.bank_account_number, bank_account_holder: s.bank_account_holder,
        min_deposit_usd: parseFloat(s.min_deposit_usd), min_deposit_idr: parseFloat(s.min_deposit_idr),
        admin_telegram_id: String(s.admin_telegram_id || ""), rate_mode: s.rate_mode, manual_rate: parseFloat(s.manual_rate),
      });
      setS(data);
      toast.success("Pengaturan disimpan");
    } catch (err) { toast.error(formatApiErrorDetail(err.response?.data?.detail)); }
    setSaving(false);
  };

  const inputCls = "mt-1 w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-100 font-mono focus:outline-none focus:border-cyan-500/60";
  const setAddr = (key, val) => setS({ ...s, crypto_addresses: { ...s.crypto_addresses, [key]: val } });

  return (
    <>
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
                  <input data-testid={`crypto-address-input-${coin}-${n.key}`} className={inputCls} placeholder={`Alamat ${coin} di ${n.label}`}
                    value={s.crypto_addresses?.[`${coin}_${n.key}`] || ""} onChange={(e) => setAddr(`${coin}_${n.key}`, e.target.value)} />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-3">
          <h2 className="font-heading font-semibold">Rekening Bank (Deposit IDR)</h2>
          <div>
            <label className="text-xs text-slate-400">Nama Bank</label>
            <input data-testid="bank-name-input" className={inputCls} placeholder="cth: BCA" value={s.bank_name || ""} onChange={(e) => setS({ ...s, bank_name: e.target.value })} />
          </div>
          <div>
            <label className="text-xs text-slate-400">Nomor Rekening</label>
            <input data-testid="bank-account-number-input" className={inputCls} value={s.bank_account_number || ""} onChange={(e) => setS({ ...s, bank_account_number: e.target.value })} />
          </div>
          <div>
            <label className="text-xs text-slate-400">Atas Nama</label>
            <input data-testid="bank-account-holder-input" className={inputCls} value={s.bank_account_holder || ""} onChange={(e) => setS({ ...s, bank_account_holder: e.target.value })} />
          </div>
        </div>

        <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-3">
          <h2 className="font-heading font-semibold">Deposit & Admin</h2>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-slate-400">Min. Deposit USD ($)</label>
              <input data-testid="min-deposit-usd-input" type="number" className={inputCls} value={s.min_deposit_usd} onChange={(e) => setS({ ...s, min_deposit_usd: e.target.value })} />
            </div>
            <div>
              <label className="text-xs text-slate-400">Min. Deposit IDR (Rp)</label>
              <input data-testid="min-deposit-idr-input" type="number" className={inputCls} value={s.min_deposit_idr} onChange={(e) => setS({ ...s, min_deposit_idr: e.target.value })} />
            </div>
          </div>
          <div>
            <label className="text-xs text-slate-400">Telegram ID Admin (untuk notifikasi)</label>
            <input data-testid="admin-telegram-id-input" className={inputCls} value={s.admin_telegram_id || ""} onChange={(e) => setS({ ...s, admin_telegram_id: e.target.value })} />
          </div>

          <div className="pt-2 border-t border-slate-800">
            <h3 className="text-sm font-semibold mb-2">Kurs USD → IDR</h3>
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm text-slate-400">Mode Manual</span>
              <Switch data-testid="exchange-rate-mode-toggle" checked={s.rate_mode === "manual"} onCheckedChange={(v) => setS({ ...s, rate_mode: v ? "manual" : "auto" })} />
            </div>
            {s.rate_mode === "manual" ? (
              <div>
                <label className="text-xs text-slate-400">Kurs Manual (1 USD = ? IDR)</label>
                <input data-testid="manual-exchange-rate-input" type="number" className={inputCls} value={s.manual_rate} onChange={(e) => setS({ ...s, manual_rate: e.target.value })} />
              </div>
            ) : (
              <p className="text-sm text-slate-400">Kurs otomatis saat ini: <span className="font-mono text-cyan-400">{fmtIDR(s.current_rate)}</span> <span className="text-xs text-slate-600">(diperbarui tiap jam)</span></p>
            )}
          </div>
        </div>
      </div>

      <button data-testid="save-settings-button" onClick={save} disabled={saving}
        className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm font-semibold rounded-lg px-6 py-2.5 transition-colors">
        {saving ? "Menyimpan..." : "Simpan Pengaturan"}
      </button>
    </>
  );
}
