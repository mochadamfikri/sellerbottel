import { useCallback, useEffect, useMemo, useState } from "react";
import { BadgePercent, Ban, Check, Megaphone, RefreshCw, Send, Trash2, Upload, UserPlus, Users } from "lucide-react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

const cls = "w-full bg-slate-950 border border-slate-800 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-cyan-500/60";
const tabs = [
  ["overview", "Ringkasan"],
  ["accounts", "Akun Telegram"],
  ["prospects", "Prospek"],
  ["campaigns", "Kampanye"],
  ["groups", "Posting Grup"],
  ["coupons", "Kupon"],
  ["results", "Hasil"],
];

const emptyCoupon = { code: "", type: "percent", value: "", currency: "IDR", quota_total: "", per_user_limit: 1, min_purchase: 0 };
const emptyCampaign = { name: "", template: "", source_code: "", bot_link: "", account_ids: [], approval_required: true, daily_limit: 20, min_interval_seconds: 300, send_window_start: "09:00", send_window_end: "21:00" };

export default function Promotions() {
  const [tab, setTab] = useState("overview");
  const [summary, setSummary] = useState({});
  const [accounts, setAccounts] = useState([]);
  const [prospects, setProspects] = useState([]);
  const [campaigns, setCampaigns] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [groups, setGroups] = useState([]);
  const [coupons, setCoupons] = useState([]);
  const [results, setResults] = useState({});
  const [sources, setSources] = useState([]);
  const [events, setEvents] = useState([]);
  const [busy, setBusy] = useState(false);

  const [phone, setPhone] = useState("");
  const [pendingAccount, setPendingAccount] = useState("");
  const [otp, setOtp] = useState("");
  const [twofa, setTwofa] = useState("");
  const [manual, setManual] = useState({ tg_user_id: "", username: "", name: "", notes: "" });
  const [source, setSource] = useState({ code: "", kind: "campaign", label: "" });
  const [coupon, setCoupon] = useState(emptyCoupon);
  const [campaign, setCampaign] = useState(emptyCampaign);
  const [post, setPost] = useState({ account_id: "", group_id: "", message: "" });

  const error = (e) => toast.error(formatApiErrorDetail(e.response?.data?.detail) || "Terjadi kesalahan.");
  const activeAccounts = useMemo(() => accounts.filter((a) => a.status === "active"), [accounts]);

  const load = useCallback(async () => {
    try {
      const [s, a, p, c, j, g, co, r, src, ev] = await Promise.all([
        api.get("/admin/promo/summary"),
        api.get("/admin/promo/accounts"),
        api.get("/admin/promo/prospects"),
        api.get("/admin/promo/campaigns"),
        api.get("/admin/promo/jobs"),
        api.get("/admin/promo/groups"),
        api.get("/admin/promo/coupons"),
        api.get("/admin/promo/results"),
        api.get("/admin/promo/results/sources"),
        api.get("/admin/promo/events"),
      ]);
      setSummary(s.data); setAccounts(a.data); setProspects(p.data); setCampaigns(c.data);
      setJobs(j.data); setGroups(g.data); setCoupons(co.data); setResults(r.data);
      setSources(src.data); setEvents(ev.data);
    } catch (e) { error(e); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const startLogin = async () => {
    if (!phone.trim()) return toast.error("Nomor Telegram wajib diisi.");
    setBusy(true);
    try {
      const r = await api.post("/admin/promo/accounts/login/start", { phone: phone.trim() });
      setPendingAccount(r.data.account_id);
      toast.success("OTP Telegram dikirim.");
    } catch (e) { error(e); } finally { setBusy(false); }
  };

  const verifyLogin = async () => {
    if (!pendingAccount || !otp.trim()) return toast.error("OTP wajib diisi.");
    setBusy(true);
    try {
      const r = await api.post("/admin/promo/accounts/login/" + pendingAccount + "/verify", { code: otp.trim(), password: twofa || null });
      if (r.data.status === "password_required") {
        toast.info("Akun meminta password 2FA. Masukkan password lalu klik Verifikasi lagi.");
        return;
      }
      toast.success("Akun Telegram tersambung.");
      setPendingAccount(""); setPhone(""); setOtp(""); setTwofa(""); await load();
    } catch (e) { error(e); } finally { setBusy(false); }
  };

  const accountAction = async (id, action) => {
    try {
      const r = await api.post("/admin/promo/accounts/" + id + "/" + action);
      toast.success(r.data.status || "Berhasil.");
      await load();
    } catch (e) { error(e); }
  };

  const revoke = async (id) => {
    if (!window.confirm("Cabut session akun ini?")) return;
    try { await api.delete("/admin/promo/accounts/" + id); await load(); } catch (e) { error(e); }
  };

  const importPrivate = async (id) => {
    try {
      const r = await api.post("/admin/promo/accounts/" + id + "/import-private");
      toast.success("Import selesai: " + (r.data.added || 0) + " prospek.");
      await load();
    } catch (e) { error(e); }
  };

  const syncGroups = async (id) => {
    try {
      const r = await api.post("/admin/promo/accounts/" + id + "/sync-groups");
      toast.success("Grup tersinkron: " + (r.data.groups || 0));
      await load();
    } catch (e) { error(e); }
  };

  const addManualProspect = async () => {
    if (!manual.tg_user_id) return toast.error("Telegram ID wajib diisi.");
    try {
      await api.post("/admin/promo/prospects/manual", { ...manual, tg_user_id: Number(manual.tg_user_id) });
      toast.success("Prospek ditambahkan.");
      setManual({ tg_user_id: "", username: "", name: "", notes: "" }); await load();
    } catch (e) { error(e); }
  };

  const createSource = async () => {
    if (!source.code || !source.kind || !source.label) return toast.error("Source code, jenis, dan label wajib diisi.");
    try {
      await api.post("/admin/promo/sources", source);
      toast.success("Source dibuat.");
      setSource({ code: "", kind: "campaign", label: "" }); await load();
    } catch (e) { error(e); }
  };

  const createCoupon = async () => {
    if (!coupon.code.trim() || !Number(coupon.value)) return toast.error("Kode dan nilai kupon wajib diisi.");
    try {
      await api.post("/admin/promo/coupons", {
        ...coupon,
        code: coupon.code.trim().toUpperCase(),
        value: Number(coupon.value),
        quota_total: coupon.quota_total ? Number(coupon.quota_total) : null,
        per_user_limit: Number(coupon.per_user_limit || 1),
        min_purchase: Number(coupon.min_purchase || 0),
        product_ids: [],
        active: true,
      });
      toast.success("Kupon dibuat."); setCoupon(emptyCoupon); await load();
    } catch (e) { error(e); }
  };

  const deleteCoupon = async (id) => {
    if (!window.confirm("Hapus kupon ini?")) return;
    try { await api.delete("/admin/promo/coupons/" + id); await load(); } catch (e) { error(e); }
  };

  const createCampaign = async () => {
    if (!campaign.name.trim() || !campaign.template.trim()) return toast.error("Nama dan template wajib diisi.");\n    if (!campaign.account_ids.length) return toast.error("Pilih minimal satu akun Telegram aktif.");
    try {
      await api.post("/admin/promo/campaigns", campaign);
      toast.success("Campaign dibuat."); setCampaign(emptyCampaign); await load();
    } catch (e) { error(e); }
  };

  const enqueue = async (id) => {
    try {
      const r = await api.post("/admin/promo/campaigns/" + id + "/enqueue");
      toast.success((r.data.queued || 0) + " job masuk antrean."); await load();
    } catch (e) { error(e); }
  };

  const approve = async (id) => {
    try { await api.post("/admin/promo/jobs/" + id + "/approve"); await load(); } catch (e) { error(e); }
  };

  const stopCampaign = async (id) => {
    if (!window.confirm("Hentikan campaign dan batalkan queue yang belum dikirim?")) return;
    try { await api.patch("/admin/promo/campaigns/" + id + "/stop"); await load(); } catch (e) { error(e); }
  };

  const optOut = async (tgUserId) => {
    try {
      await api.post("/admin/promo/opt-out", { tg_user_id: Number(tgUserId), reason: "admin" });
      toast.success("Prospek masuk suppression list."); await load();
    } catch (e) { error(e); }
  };

  const postGroup = async () => {
    if (!post.account_id || !post.group_id || !post.message.trim()) return toast.error("Akun, grup, dan pesan wajib diisi.");
    try {
      await api.post("/admin/promo/groups/" + post.group_id + "/post", { ...post, group_id: Number(post.group_id) });
      toast.success("Posting grup terkirim."); setPost({ ...post, message: "" });
    } catch (e) { error(e); }
  };

  return (
    <div className="space-y-5">
      <div className="flex gap-2 overflow-x-auto pb-1">
        {tabs.map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)} className={"px-4 py-2 rounded-lg text-sm whitespace-nowrap border " + (tab === id ? "border-cyan-500/30 bg-cyan-500/10 text-cyan-300" : "border-slate-800 text-slate-400 hover:text-slate-200")}>{label}</button>
        ))}
        <button onClick={load} className="ml-auto p-2 text-slate-400 hover:text-slate-100" title="Refresh"><RefreshCw size={16} /></button>
      </div>

      {tab === "overview" && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
            {[
              ["Kupon", summary.coupons || 0, BadgePercent],
              ["Prospek", summary.prospects || 0, Users],
              ["Kampanye", summary.campaigns || 0, Megaphone],
              ["Akun Telegram", summary.telegram_accounts || 0, Send],
              ["Grup", summary.groups || 0, Users],
            ].map(([label, value, Icon]) => <div key={label} className="bg-slate-900/80 border border-slate-800 rounded-xl p-4"><Icon size={17} className="text-cyan-400 mb-3" /><p className="text-2xl font-semibold">{value}</p><p className="text-xs text-slate-500 mt-1">{label}</p></div>)}
          </div>
          <div className="grid lg:grid-cols-2 gap-5">
            <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5">
              <h2 className="font-heading font-semibold">Kontrol Pengiriman</h2>
              <p className="text-sm text-slate-400 mt-2">Interval minimum 5 menit, batas harian, jam pengiriman, approval, suppression/opt-out, dan penghentian saat Telegram memberi pembatasan.</p>
            </div>
            <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5">
              <h2 className="font-heading font-semibold">Status Hasil</h2>
              <div className="grid grid-cols-3 gap-3 mt-4 text-sm">
                <div><b>{results.sent || 0}</b><p className="text-slate-500">Terkirim</p></div>
                <div><b>{results.replied || 0}</b><p className="text-slate-500">Balas</p></div>
                <div><b>{results.customers || 0}</b><p className="text-slate-500">Customer</p></div>
              </div>
            </div>
          </div>
        </>
      )}

      {tab === "accounts" && (
        <div className="space-y-5">
          <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-3">
            <h2 className="font-semibold">Tambah Akun Telegram</h2>
            <div className="grid md:grid-cols-3 gap-2">
              <input className={cls} placeholder="+628..." value={phone} onChange={(e) => setPhone(e.target.value)} />
              <button disabled={busy} onClick={startLogin} className="bg-cyan-600 hover:bg-cyan-700 disabled:opacity-50 rounded-lg">Kirim OTP</button>
              <input className={cls} placeholder="OTP" value={otp} onChange={(e) => setOtp(e.target.value)} />
            </div>
            {pendingAccount && <div className="grid md:grid-cols-2 gap-2"><input className={cls} type="password" placeholder="Password 2FA jika diminta" value={twofa} onChange={(e) => setTwofa(e.target.value)} /><button disabled={busy} onClick={verifyLogin} className="bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 rounded-lg">Verifikasi</button></div>}
          </div>
          <div className="bg-slate-900/80 border border-slate-800 rounded-xl overflow-x-auto">
            <table className="w-full text-sm"><thead><tr className="border-b border-slate-800 text-xs text-slate-500"><th className="p-3 text-left">Akun</th><th>Status</th><th>Telegram ID</th><th>Aksi</th></tr></thead>
              <tbody>{accounts.map((a) => <tr key={a._id} className="border-b border-slate-800/70"><td className="p-3">{a.name || a.username || a.phone || "-"}</td><td>{a.status}</td><td>{a.tg_user_id || "-"}</td><td className="p-3 flex gap-2 justify-center"><button onClick={() => accountAction(a._id, "check")} title="Cek"><RefreshCw size={15} /></button><button onClick={() => importPrivate(a._id)} title="Import private chat"><Upload size={15} /></button><button onClick={() => syncGroups(a._id)} title="Sync grup"><Users size={15} /></button><button onClick={() => revoke(a._id)} title="Cabut" className="text-rose-400"><Trash2 size={15} /></button></td></tr>)}</tbody>
            </table>
            {!accounts.length && <p className="p-5 text-sm text-slate-500">Belum ada akun.</p>}
          </div>
        </div>
      )}

      {tab === "prospects" && (
        <div className="space-y-5">
          <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-3">
            <div className="flex items-center gap-2"><UserPlus size={17} className="text-cyan-400" /><h2 className="font-semibold">Tambah Prospek Manual</h2></div>
            <div className="grid md:grid-cols-4 gap-2"><input className={cls} placeholder="Telegram ID" value={manual.tg_user_id} onChange={(e) => setManual({ ...manual, tg_user_id: e.target.value })} /><input className={cls} placeholder="Username" value={manual.username} onChange={(e) => setManual({ ...manual, username: e.target.value })} /><input className={cls} placeholder="Nama" value={manual.name} onChange={(e) => setManual({ ...manual, name: e.target.value })} /><button onClick={addManualProspect} className="bg-cyan-600 rounded-lg">Tambah</button></div>
            <input className={cls} placeholder="Catatan" value={manual.notes} onChange={(e) => setManual({ ...manual, notes: e.target.value })} />
          </div>
          <div className="bg-slate-900/80 border border-slate-800 rounded-xl overflow-x-auto">
            <table className="w-full text-sm"><thead><tr className="border-b border-slate-800 text-xs text-slate-500"><th className="p-3 text-left">Nama</th><th>Username</th><th>Status</th><th>Source</th><th>Aksi</th></tr></thead><tbody>{prospects.map((p) => <tr key={p._id} className="border-b border-slate-800/70"><td className="p-3">{p.name || "-"}</td><td>{p.username ? "@" + p.username : "-"}</td><td>{p.status}</td><td>{p.source?.label || "-"}</td><td><button onClick={() => optOut(p.tg_user_id)} title="Opt-out" className="text-rose-400"><Ban size={15} /></button></td></tr>)}</tbody></table>
            {!prospects.length && <p className="p-5 text-sm text-slate-500">Belum ada prospek.</p>}
          </div>
        </div>
      )}

      {tab === "campaigns" && (
        <div className="space-y-5">
          <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-3">
            <h2 className="font-semibold">Buat Campaign</h2>
            <input className={cls} placeholder="Nama campaign" value={campaign.name} onChange={(e) => setCampaign({ ...campaign, name: e.target.value })} />
            <textarea className={cls} rows="5" placeholder="Pesan. Variabel: {nama} {username} {bot_link}" value={campaign.template} onChange={(e) => setCampaign({ ...campaign, template: e.target.value })} />
            <div className="grid md:grid-cols-4 gap-2"><input className={cls} placeholder="Source code" value={campaign.source_code} onChange={(e) => setCampaign({ ...campaign, source_code: e.target.value })} /><input className={cls} placeholder="Bot link" value={campaign.bot_link} onChange={(e) => setCampaign({ ...campaign, bot_link: e.target.value })} /><input className={cls} type="number" min="1" max="100" placeholder="Limit/hari" value={campaign.daily_limit} onChange={(e) => setCampaign({ ...campaign, daily_limit: Number(e.target.value) })} /><input className={cls} type="number" min="300" value={campaign.min_interval_seconds} onChange={(e) => setCampaign({ ...campaign, min_interval_seconds: Number(e.target.value) })} /></div>
            <div className="grid md:grid-cols-2 gap-2"><input className={cls} type="time" value={campaign.send_window_start} onChange={(e) => setCampaign({ ...campaign, send_window_start: e.target.value })} /><input className={cls} type="time" value={campaign.send_window_end} onChange={(e) => setCampaign({ ...campaign, send_window_end: e.target.value })} /></div>\n            <div className="space-y-2"><p className="text-xs text-slate-500">Akun pengirim</p>{activeAccounts.length ? activeAccounts.map((a) => <label key={a._id} className="flex items-center gap-2 text-sm text-slate-300"><input type="checkbox" checked={campaign.account_ids.includes(a._id)} onChange={(e) => setCampaign({ ...campaign, account_ids: e.target.checked ? [...campaign.account_ids, a._id] : campaign.account_ids.filter((id) => id !== a._id) })} />{a.name || a.username || a.tg_user_id}</label>) : <p className="text-xs text-amber-400">Belum ada akun Telegram aktif.</p>}</div>
            <label className="flex items-center gap-2 text-sm text-slate-300"><input type="checkbox" checked={campaign.approval_required} onChange={(e) => setCampaign({ ...campaign, approval_required: e.target.checked })} /> Perlu approval sebelum pengiriman</label>
            <button onClick={createCampaign} className="bg-cyan-600 hover:bg-cyan-700 rounded-lg px-4 py-2">Buat Campaign</button>
          </div>
          {campaigns.map((c) => <div key={c._id} className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 flex flex-wrap gap-3 items-center justify-between"><div><b>{c.name}</b><p className="text-xs text-slate-500 mt-1">{c.status} · {c.daily_limit}/hari · ≥{c.min_interval_seconds}s · {c.approval_required ? "approval" : "auto"}</p></div><div className="flex gap-2"><button onClick={() => enqueue(c._id)} className="bg-cyan-700 px-3 py-2 rounded-lg text-sm">Buat Queue</button><button onClick={() => stopCampaign(c._id)} className="bg-rose-700 px-3 py-2 rounded-lg text-sm">Stop</button></div></div>)}
          <div className="bg-slate-900/80 border border-slate-800 rounded-xl overflow-x-auto"><div className="p-4 border-b border-slate-800 font-semibold">Approval Queue</div><table className="w-full text-sm"><thead><tr className="border-b border-slate-800 text-xs text-slate-500"><th className="p-3 text-left">Prospek</th><th>Campaign</th><th>Status</th><th>Schedule</th><th>Aksi</th></tr></thead><tbody>{jobs.map((j) => <tr key={j._id} className="border-b border-slate-800/70"><td className="p-3">{j.prospect_id}</td><td>{j.campaign_id}</td><td>{j.status}</td><td>{j.scheduled_at || "-"}</td><td>{j.status === "queued" && <button onClick={() => approve(j._id)} className="text-emerald-400"><Check size={16} /></button>}</td></tr>)}</tbody></table>{!jobs.length && <p className="p-5 text-sm text-slate-500">Queue kosong.</p>}</div>
        </div>
      )}

      {tab === "groups" && (
        <div className="space-y-5">
          <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-3"><h2 className="font-semibold">Posting Grup — manual approval</h2><select className={cls} value={post.account_id} onChange={(e) => setPost({ ...post, account_id: e.target.value })}><option value="">Pilih akun</option>{activeAccounts.map((a) => <option key={a._id} value={a._id}>{a.name || a.username || a.tg_user_id}</option>)}</select><select className={cls} value={post.group_id} onChange={(e) => setPost({ ...post, group_id: e.target.value })}><option value="">Pilih grup</option>{groups.map((g) => <option key={g._id} value={g.chat_id}>{g.title}</option>)}</select><textarea className={cls} rows="5" placeholder="Pesan grup" value={post.message} onChange={(e) => setPost({ ...post, message: e.target.value })} /><button onClick={postGroup} className="bg-cyan-600 rounded-lg py-2">Posting</button></div>
          <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5"><h2 className="font-semibold">Grup tersinkron</h2><div className="mt-3 space-y-2">{groups.map((g) => <div key={g._id} className="flex justify-between border-b border-slate-800 py-2 text-sm"><span>{g.title || g.chat_id}</span><span className="text-slate-500">{g.account_id}</span></div>)}{!groups.length && <p className="text-sm text-slate-500">Belum ada grup.</p>}</div></div>
        </div>
      )}

      {tab === "coupons" && (
        <div className="space-y-5">
          <div className="grid xl:grid-cols-[420px_1fr] gap-5"><div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-3"><h2 className="font-semibold">Buat Kupon</h2><input className={cls} placeholder="Kode" value={coupon.code} onChange={(e) => setCoupon({ ...coupon, code: e.target.value })} /><div className="grid grid-cols-2 gap-2"><select className={cls} value={coupon.type} onChange={(e) => setCoupon({ ...coupon, type: e.target.value })}><option value="percent">Persen</option><option value="fixed">Nominal</option></select><input className={cls} type="number" placeholder="Nilai" value={coupon.value} onChange={(e) => setCoupon({ ...coupon, value: e.target.value })} /></div><div className="grid grid-cols-2 gap-2"><select className={cls} value={coupon.currency} onChange={(e) => setCoupon({ ...coupon, currency: e.target.value })}><option>IDR</option><option>USD</option></select><input className={cls} type="number" min="1" placeholder="Limit/user" value={coupon.per_user_limit} onChange={(e) => setCoupon({ ...coupon, per_user_limit: e.target.value })} /></div><input className={cls} type="number" min="0" placeholder="Kuota total" value={coupon.quota_total} onChange={(e) => setCoupon({ ...coupon, quota_total: e.target.value })} /><input className={cls} type="number" min="0" placeholder="Minimum belanja" value={coupon.min_purchase} onChange={(e) => setCoupon({ ...coupon, min_purchase: e.target.value })} /><button onClick={createCoupon} className="w-full bg-cyan-600 rounded-lg py-2">Buat Kupon</button></div><div className="bg-slate-900/80 border border-slate-800 rounded-xl overflow-x-auto"><table className="w-full text-sm"><thead><tr className="border-b border-slate-800"><th className="p-3 text-left">Kode</th><th>Pemakaian</th><th>Status</th><th /></tr></thead><tbody>{coupons.map((c) => <tr key={c._id} className="border-b border-slate-800/70"><td className="p-3 font-mono">{c.code}</td><td>{c.used_count || 0}{c.quota_total == null ? "" : " / " + c.quota_total}</td><td>{c.active ? "Aktif" : "Nonaktif"}</td><td><button onClick={() => deleteCoupon(c._id)} className="text-rose-400"><Trash2 size={15} /></button></td></tr>)}</tbody></table>{!coupons.length && <p className="p-5 text-sm text-slate-500">Belum ada kupon.</p>}</div></div>
          <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-3"><h2 className="font-semibold">Traffic Source</h2><div className="grid md:grid-cols-4 gap-2"><input className={cls} placeholder="code" value={source.code} onChange={(e) => setSource({ ...source, code: e.target.value })} /><input className={cls} placeholder="jenis" value={source.kind} onChange={(e) => setSource({ ...source, kind: e.target.value })} /><input className={cls} placeholder="label" value={source.label} onChange={(e) => setSource({ ...source, label: e.target.value })} /><button onClick={createSource} className="bg-cyan-600 rounded-lg">Tambah Source</button></div><div className="text-xs text-slate-500">{sources.length} source terdaftar.</div></div>
        </div>
      )}

      {tab === "results" && (
        <div className="space-y-5">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">{Object.entries(results).map(([key, value]) => <div key={key} className="bg-slate-900/80 border border-slate-800 rounded-xl p-5"><p className="text-2xl font-semibold">{value}</p><p className="text-xs text-slate-500 mt-1">{key}</p></div>)}</div>
          <div className="grid lg:grid-cols-2 gap-5">
            <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5"><h2 className="font-semibold mb-3">Pembelian per Source</h2>{sources.map((s) => <div key={s._id} className="flex justify-between border-b border-slate-800 py-2 text-sm"><span>{s.label} <span className="text-slate-500">({s.code})</span></span><span className="text-slate-500">source</span></div>)}{!sources.length && <p className="text-sm text-slate-500">Belum ada source.</p>}</div>
            <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5"><h2 className="font-semibold mb-3">Event Terbaru</h2>{events.slice(0, 20).map((e) => <div key={e._id} className="border-b border-slate-800 py-2 text-xs"><b>{e.type}</b><span className="text-slate-500 ml-2">{e.created_at}</span></div>)}{!events.length && <p className="text-sm text-slate-500">Belum ada event.</p>}</div>
          </div>
        </div>
      )}
    </div>
  );
}
