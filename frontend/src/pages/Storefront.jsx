import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { Search, ShoppingBag, UserRound, ArrowRight, Plus, Minus, LogOut, LogIn, UserPlus, Menu, PackageCheck, PackageSearch, PackageX, TrendingUp, Sparkles, Headset, ShieldCheck, CreditCard, Clock3, ClipboardList, Trash2, X, QrCode } from "lucide-react";
import api, { fmtIDR, fmtUSD, formatApiErrorDetail } from "../lib/api";

const page = "min-h-screen bg-[#f7f8f6] text-slate-800";
const button = "inline-flex items-center justify-center gap-2 rounded-lg bg-emerald-800 px-5 py-3 font-semibold text-white shadow-sm transition hover:bg-emerald-900 focus:outline-none focus:ring-2 focus:ring-emerald-700 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
const secondary = "inline-flex items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-4 py-2.5 font-semibold text-slate-700 transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-emerald-700 focus:ring-offset-2";
const input = "w-full rounded-lg border border-slate-300 bg-white px-4 py-3 text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-emerald-700 focus:ring-2 focus:ring-emerald-700/15";

function Header({ count, profile, onLogout }) {
  const [open, setOpen] = useState(false);
  const authLinks = profile
    ? <button type="button" onClick={() => { setOpen(false); onLogout?.(); }} className="inline-flex items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 hover:border-rose-200 hover:bg-rose-50 hover:text-rose-700"><LogOut size={16}/> Keluar</button>
    : <div className="flex items-center gap-2"><Link className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-100" to="/store/login"><LogIn size={16}/> Masuk</Link><Link className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-800 px-3 py-2 text-sm font-semibold text-white hover:bg-emerald-900" to="/store/register"><UserPlus size={16}/> Daftar</Link></div>;
  return <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/95 backdrop-blur">
    <div className="mx-auto flex max-w-7xl items-center justify-between gap-3 px-4 py-3 sm:px-6 lg:px-8">
      <Link to="/store" className="flex min-w-0 items-center gap-2.5 text-emerald-950">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-emerald-900 text-[11px] font-black tracking-tight text-white shadow-sm">IDSE</span>
        <span className="min-w-0"><span className="block truncate text-sm font-bold tracking-tight sm:text-base">IDSE Digital Product</span><span className="hidden text-[10px] font-medium tracking-wide text-slate-500 sm:block">TOKO PRODUK DIGITAL</span></span>
      </Link>
      <nav className="hidden items-center gap-3 text-sm font-semibold text-slate-600 xl:flex">
        <Link className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-2 hover:bg-emerald-50 hover:text-emerald-800" to="/store/products"><PackageSearch size={17}/> Produk</Link>
        <Link className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-2 hover:bg-emerald-50 hover:text-emerald-800" to="/store/deposit"><CreditCard size={17}/> Deposit</Link>
        <Link className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-2 hover:bg-emerald-50 hover:text-emerald-800" to="/store/transactions"><Clock3 size={17}/> Transaksi</Link>
        <Link className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-2 hover:bg-emerald-50 hover:text-emerald-800" to="/store/orders"><ClipboardList size={17}/> Pesanan</Link>
      </nav>
      <div className="flex shrink-0 items-center gap-2">
        <Link aria-label={`Keranjang, ${count} item`} className="inline-flex items-center gap-1 rounded-lg px-2 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-100" to="/store/cart"><ShoppingBag size={19}/><span>{count}</span></Link>
        {profile && <Link aria-label="Profil pelanggan" className="rounded-lg p-2 text-slate-700 hover:bg-slate-100" to="/store/profile"><UserRound size={19}/></Link>}
        <div className="hidden xl:block">{authLinks}</div>
        <button aria-label={open ? "Tutup menu" : "Buka menu"} aria-expanded={open} className="rounded-lg p-2 text-slate-700 hover:bg-slate-100 xl:hidden" onClick={() => setOpen((value) => !value)}>{open ? <X size={19}/> : <Menu size={19}/>}</button>
      </div>
    </div>
    {open && <nav className="animate-in fade-in slide-in-from-top-2 duration-150 grid gap-1 border-t border-slate-100 bg-white px-4 py-3 text-sm font-medium text-slate-700 motion-reduce:animate-none xl:hidden">
      {[["Produk", "/store/products"], ["Deposit", "/store/deposit"], ["Transaksi", "/store/transactions"], ["Pesanan", "/store/orders"], ...(profile ? [["Akun", "/store/profile"]] : []), ...(!profile ? [["Masuk", "/store/login"], ["Daftar", "/store/register"]] : [])].map(([label, href]) => <Link key={href} className="rounded-lg px-3 py-2 hover:bg-slate-50" onClick={() => setOpen(false)} to={href}>{label}</Link>)}
      {profile && <button type="button" onClick={() => { setOpen(false); onLogout?.(); }} className="flex items-center gap-2 rounded-lg px-3 py-2 text-left font-semibold text-rose-700 hover:bg-rose-50"><LogOut size={16}/> Keluar</button>}
    </nav>}
  </header>;
}

function money(product, currency) {
  const value = product[`price_${currency.toLowerCase()}_current`] ?? product[`price_${currency.toLowerCase()}`];
  return currency === "USD" ? fmtUSD(value) : fmtIDR(value);
}

function productTypeLabel(product) {
  if (product?.product_kind === "service") return "Jasa Payment";
  const kind = String(product?.delivery_type || product?.product_type || "").toLowerCase();
  if (kind.includes("account") || kind.includes("akun")) return "Akun Digital";
  if (kind.includes("session")) return "Session File";
  if (kind.includes("license") || kind.includes("lisensi")) return "Lisensi Digital";
  if (kind.includes("file")) return "File Digital";
  return "Produk Digital";
}

function ProductArtwork({ product }) {
  const isService = product?.product_kind === "service";
  const Icon = isService ? Headset : PackageCheck;
  const initials = String(product?.name || "IDSE").trim().split(/\s+/).slice(0, 2).map((word) => word[0]).join("").toUpperCase();
  return <div aria-hidden="true" className="relative flex h-full w-full items-center justify-center overflow-hidden bg-[#eef4ef]">
    <div className="absolute -right-10 -top-12 h-40 w-40 rounded-full bg-white/70"/>
    <div className="absolute -bottom-14 -left-12 h-44 w-44 rounded-full border-[24px] border-emerald-900/[.04]"/>
    <div className="relative flex max-w-[85%] flex-col items-center text-center">
      <span className="grid h-16 w-16 place-items-center rounded-2xl border border-emerald-900/10 bg-white text-emerald-900 shadow-sm"><Icon size={31} strokeWidth={1.6}/></span>
      <span className="mt-3 max-w-full truncate text-2xl font-black tracking-[.12em] text-emerald-950">{initials || "IDSE"}</span>
      <span className="mt-1 max-w-full truncate text-[10px] font-semibold uppercase tracking-[.16em] text-slate-500">{productTypeLabel(product)}</span>
    </div>
  </div>;
}

function ProductCard({ product, add }) {
  const minimum = Math.max(1, Number(product.minimum_purchase_qty || 1));
  const noStock = product.stock != null && Number(product.stock) < minimum;
  return <article className="group overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm transition hover:-translate-y-0.5 hover:shadow-md">
    <Link to={`/store/product/${product._id}`} className="relative block aspect-square overflow-hidden bg-slate-100">
      <ProductArtwork product={product}/>
      {product.image_url && <img src={product.image_url} alt={product.name} className="absolute inset-0 h-full w-full bg-white object-contain p-3 transition group-hover:scale-[1.02] sm:p-5" loading="lazy" onError={(event) => { event.currentTarget.style.display = "none"; }}/ >}
      <div className="absolute left-3 top-3 flex flex-wrap gap-1.5"><span className="rounded-md border border-slate-200 bg-white/95 px-2.5 py-1 text-[11px] font-semibold text-slate-700">{productTypeLabel(product)}</span><span className={`rounded-md border border-white/70 bg-white/95 px-2.5 py-1 text-[11px] font-semibold ${noStock ? "text-rose-700" : "text-emerald-800"}`}>{noStock ? "Out of Stock" : "Ready Stock"}</span></div>
    </Link>
    <div className="p-4 sm:p-5">
      <Link to={`/store/product/${product._id}`} className="line-clamp-1 font-semibold text-slate-900 hover:text-emerald-800">{product.name}</Link>
      <p className="mt-2 line-clamp-2 min-h-10 text-sm leading-5 text-slate-500">{product.description || "Produk pilihan IDSE Digital Product."}</p>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <span className={`text-xs font-medium ${noStock ? "text-rose-700" : "text-slate-500"}`}>{noStock ? "Stok belum mencukupi" : product.stock == null ? "Stok tersedia" : `Stok ${product.stock}`}</span>
        {minimum > 1 && <span className="text-xs text-slate-500">Min. {minimum} pcs</span>}
      </div>
      <div className="mt-4 flex items-end justify-between gap-3 border-t border-slate-100 pt-3">
        <div><p className="text-[11px] text-slate-500">Harga mulai</p><p className="font-bold text-emerald-900">{money(product, "IDR")}</p></div>
        <button className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-900 hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-45" onClick={() => add(product)} disabled={noStock} aria-label={`Tambah ${product.name} ke keranjang`}><Plus size={16}/> Tambah</button>
      </div>
    </div>
  </article>;
}

function statusLabel(value) {
  return ({ delivered: "Selesai", service_waiting: "Diproses", pending_payment: "Menunggu pembayaran", paid: "Dibayar", pending: "Menunggu", approved: "Berhasil", expired: "Kedaluwarsa", failed: "Gagal", delivery_failed: "Perlu bantuan", rejected: "Ditolak", cancelled: "Dibatalkan", refunded: "Dikembalikan" })[value] || value || "—";
}

function dateLabel(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" });
}

export default function Storefront({ view = "home" }) {
  const [products, setProducts] = useState([]);
  const [productLoading, setProductLoading] = useState(true);
  const [cart, setCart] = useState(() => { try { return JSON.parse(localStorage.getItem("store_cart") || "[]"); } catch { return []; } });
  const [search, setSearch] = useState("");
  const [productFilter, setProductFilter] = useState("all");
  const [profile, setProfile] = useState(null);
  const [orders, setOrders] = useState([]);
  const [transactions, setTransactions] = useState([]);
  const currency = "IDR";
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [botName, setBotName] = useState("");
  const [contactConfig, setContactConfig] = useState({ whatsapp_contact_number: "", telegram_contact_target: "" });
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [resetMode, setResetMode] = useState(false);
  const [linkCode, setLinkCode] = useState("");
  const [checkoutKey, setCheckoutKey] = useState("");
  const [paymentMethod, setPaymentMethod] = useState("qris");
  const [couponCode, setCouponCode] = useState("");
  const [depositAmount, setDepositAmount] = useState("50000");
  const [deposit, setDeposit] = useState(null);
  const [depositRows, setDepositRows] = useState([]);
  const [quote, setQuote] = useState(null);
  const [quoteError, setQuoteError] = useState("");
  const [quoteLoading, setQuoteLoading] = useState(false);
  const [checkoutPayment, setCheckoutPayment] = useState(null);
  const [qrisDialogOpen, setQrisDialogOpen] = useState(false);
  const [expiryTick, setExpiryTick] = useState(0);
  const [selected, setSelected] = useState(null);
  const [transactionFilter, setTransactionFilter] = useState("all");
  const navigate = useNavigate();
  const location = useLocation();
  const { productId } = useParams();

  useEffect(() => {
    const deadlines = [deposit, checkoutPayment, selected]
      .filter((payment) => payment?.status === "pending" || payment?.status === "pending_payment")
      .map((payment) => Date.parse(payment.expires_at || ""))
      .filter((deadline) => Number.isFinite(deadline) && deadline > Date.now());
    if (!deadlines.length) return undefined;
    const timer = window.setTimeout(() => setExpiryTick((value) => value + 1), Math.min(...deadlines) - Date.now() + 25);
    return () => window.clearTimeout(timer);
  }, [deposit, checkoutPayment, selected, expiryTick]);

  const loadProducts = useCallback(async (term = "") => {
    setProductLoading(true);
    try { const { data } = await api.get("/store/products", { params: term ? { search: term } : {} }); setProducts(data); setError(""); }
    catch (e) { setError(formatApiErrorDetail(e.response?.data?.detail) || "Katalog belum dapat dimuat."); }
    finally { setProductLoading(false); }
  }, []);

  const loadProfile = useCallback(async () => {
    const { data } = await api.get("/store/me");
    setProfile(data);
    return data;
  }, []);

  useEffect(() => { loadProducts(search); }, [loadProducts, search]);
  useEffect(() => { localStorage.setItem("store_cart", JSON.stringify(cart)); }, [cart]);
  useEffect(() => { api.get("/store/config").then(({data}) => { setBotName(data.telegram_bot_username || ""); setContactConfig(data); }).catch(() => {}); }, []);
  useEffect(() => { loadProfile().catch(() => setProfile(null)); }, [loadProfile]);
  useEffect(() => {
    if (!["profile", "orders", "deposit", "transactions"].includes(view)) return;
    loadProfile().catch(() => navigate("/store/login", { state: { from: location.pathname } }));
    if (view === "orders") api.get("/store/orders").then(({data}) => setOrders(data)).catch(() => {});
    if (view === "transactions") api.get("/store/transactions").then(({data}) => setTransactions(data)).catch((e) => setError(formatApiErrorDetail(e.response?.data?.detail)));
    if (view === "deposit") api.get("/store/deposits").then(({data}) => setDepositRows(data)).catch(() => {});
  }, [view, loadProfile, navigate, location.pathname]);

  useEffect(() => {
    if (view !== "orders") return undefined;
    const timer = window.setInterval(async () => {
      try { const { data } = await api.get("/store/orders"); setOrders(data); }
      catch (_) {}
    }, 10000);
    return () => window.clearInterval(timer);
  }, [view]);

  useEffect(() => {
    if (!deposit || deposit.status !== "pending") return undefined;
    const timer = window.setInterval(async () => {
      try {
        const { data } = await api.get("/store/deposits");
        setDepositRows(data);
        const current = data.find((row) => row.deposit_id === deposit.deposit_id);
        if (current && current.status !== "pending") {
          setDeposit((value) => ({ ...value, status: current.status }));
          if (current.status === "approved") loadProfile().catch(() => {});
        }
      } catch (_) {}
    }, 5000);
    return () => window.clearInterval(timer);
  }, [deposit, loadProfile]);

  useEffect(() => {
    if (view !== "cart" || !cart.length) { setQuote(null); setQuoteError(""); setQuoteLoading(false); return undefined; }
    let active = true;
    setQuoteLoading(true);
    const timer = window.setTimeout(async () => {
      try {
        const { data } = await api.post("/store/quote", {
          items: cart, currency: "IDR", coupon_code: couponCode.trim() || null,
        });
        if (active) { setQuote(data); setQuoteError(""); }
      } catch (err) {
        if (active) { setQuote(null); setQuoteError(formatApiErrorDetail(err.response?.data?.detail) || "Harga keranjang belum dapat dihitung."); }
      } finally { if (active) setQuoteLoading(false); }
    }, 180);
    return () => { active = false; window.clearTimeout(timer); };
  }, [view, cart, couponCode]);

  useEffect(() => {
    if (!checkoutPayment || checkoutPayment.status !== "pending_payment") return undefined;
    const timer = window.setInterval(async () => {
      try {
        const { data } = await api.get("/store/orders");
        setOrders(data);
        const current = data.find((row) => row._id === checkoutPayment._id);
        if (current && current.status !== "pending_payment") {
          setCheckoutPayment((value) => value ? { ...value, status: current.status } : value);
          if (current.status === "delivered") setNotice("Pembayaran QRIS terverifikasi. Pesanan selesai; periksa email Anda.");
          else if (current.status === "service_waiting") setNotice("Pembayaran terverifikasi. Pesanan layanan sedang diproses.");
          else if (current.status === "delivery_failed") setNotice("Pembayaran terverifikasi, tetapi pengiriman perlu bantuan tim. Hubungi admin.");
        }
      } catch (_) {}
    }, 5000);
    return () => window.clearInterval(timer);
  }, [checkoutPayment]);

  const filteredProducts = useMemo(() => {
    const isOut = (product) => product.stock != null && Number(product.stock) < Math.max(1, Number(product.minimum_purchase_qty || 1));
    let result = products.filter((product) => {
      if (productFilter === "out") return isOut(product);
      if (productFilter === "ready") return !isOut(product);
      if (productFilter === "service") return product.product_kind === "service";
      return true;
    });
    if (productFilter === "bestseller") {
      return result.sort((a, b) => Number(b.sales_count || 0) - Number(a.sales_count || 0) || Number(isOut(a)) - Number(isOut(b)));
    }
    return result.sort((a, b) => Number(isOut(a)) - Number(isOut(b)));
  }, [products, productFilter]);
  const promotedProducts = useMemo(() => products.filter((product) => Number(product.discount_idr || 0) > 0), [products]);
  const depositExpired = deposit?.status === "pending" && Number.isFinite(Date.parse(deposit.expires_at || "")) && Date.parse(deposit.expires_at || "") <= Date.now();
  const checkoutExpired = checkoutPayment?.status === "pending_payment" && Number.isFinite(Date.parse(checkoutPayment.expires_at || "")) && Date.parse(checkoutPayment.expires_at || "") <= Date.now();
  const setCartAndResetKey = (next) => { setCheckoutKey(""); setCart(next); };
  const add = (product) => setCartAndResetKey((items) => {
    const minimum = Math.max(1, Number(product.minimum_purchase_qty || 1));
    const existing = items.find((item) => item.pid === product._id);
    if (existing) {
      return items.map((item) => item.pid === product._id
        ? { ...item, qty: Math.min(product.stock == null ? 100 : product.stock, Math.max(minimum, item.qty + 1)) }
        : item);
    }
    return [...items, { pid: product._id, qty: minimum }];
  });
  const changeQty = (pid, delta) => setCartAndResetKey((items) => items.map((item) => {
    if (item.pid !== pid) return item;
    const product = products.find((row) => row._id === pid);
    const max = product?.stock == null ? 100 : Number(product.stock);
    const minimum = Math.max(1, Number(product?.minimum_purchase_qty || 1));
    return { ...item, qty: Math.max(minimum, Math.min(max, item.qty + delta)) };
  }).filter((item) => item.qty > 0));
  const removeCartItem = (pid) => setCartAndResetKey((items) => items.filter((item) => item.pid !== pid));

  const submitAccountForm = async (event) => {
    event.preventDefault(); setError(""); setBusy(true);
    try {
      if (resetMode) {
        if (!codeSent) { await api.post("/store/password/request-code", {email}); setCodeSent(true); setNotice("Jika email terdaftar, kode reset akan dikirim."); }
        else { await api.post("/store/password/reset", {email, code, password}); setResetMode(false); setCodeSent(false); setCode(""); setPassword(""); setNotice("Kata sandi berhasil diubah. Silakan masuk."); }
      } else if (view === "register") {
        if (!codeSent) { await api.post("/store/register/request-code", {email}); setCodeSent(true); setNotice("Kode verifikasi terkirim. Periksa inbox dan folder spam."); }
        else { await api.post("/store/register/verify", {email, code, password}); await loadProfile(); navigate("/store/profile"); }
      } else { await api.post("/store/login", {email, password}); await loadProfile(); navigate(location.state?.from || "/store/profile"); }
    } catch (e) { setError(formatApiErrorDetail(e.response?.data?.detail) || "Permintaan gagal. Periksa kembali data yang dimasukkan."); }
    finally { setBusy(false); }
  };

  const createLinkCode = async () => {
    try { const {data} = await api.post("/store/link-code"); setLinkCode(data.code); setBotName(data.bot_username || botName); setError(""); }
    catch (e) { setError(formatApiErrorDetail(e.response?.data?.detail)); }
  };

  const checkout = async () => {
    if (!profile) { navigate("/store/login", { state: { from: "/store/cart" } }); return; }
    const invalid = cart.find((item) => {
      const product = products.find((row) => row._id === item.pid);
      return !product || item.qty < Math.max(1, Number(product.minimum_purchase_qty || 1)) || (product.stock != null && item.qty > product.stock);
    });
    if (invalid) { setError("Periksa minimum pembelian dan stok setiap produk di keranjang."); return; }
    const key = checkoutKey || (window.crypto?.randomUUID ? window.crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);
    setCheckoutKey(key); setBusy(true); setError("");
    try {
      const {data} = await api.post("/store/checkout", {items: cart, currency: "IDR", payment_method: paymentMethod, coupon_code: couponCode.trim() || null, idempotency_key: key});
      setCartAndResetKey([]); setCouponCode(""); setCheckoutKey(""); setQuote(null);
      if (data.status === "pending_payment" && data.qr_image) { setCheckoutPayment(data); setQrisDialogOpen(true); }
      else { setNotice(`Pesanan ${data.invoice_id} berstatus ${statusLabel(data.status)}.`); navigate("/store/orders"); }
    } catch (e) { if (e.response?.status === 409 || (paymentMethod === "balance" && e.response?.status === 400)) setCheckoutKey(""); setError(formatApiErrorDetail(e.response?.data?.detail) || "Checkout gagal. Periksa saldo, stok, atau kupon lalu coba lagi."); }
    finally { setBusy(false); }
  };

  const createDeposit = async (event) => {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    try {
      const {data} = await api.post("/store/deposits", {amount_idr: Number(depositAmount)});
      setDeposit(data);
      setNotice("QRIS dibuat. Bayar sesuai nominal yang tertera sebelum kedaluwarsa.");
    } catch (e) { setError(formatApiErrorDetail(e.response?.data?.detail) || "QRIS gagal dibuat."); }
    finally { setBusy(false); }
  };

  const logout = async () => { await api.post("/store/logout").catch(() => {}); setProfile(null); navigate("/store"); };
  const cartTotal = cart.reduce((sum, item) => {
    const product = products.find((row) => row._id === item.pid);
    const price = product?.[`price_${currency.toLowerCase()}_current`] ?? product?.[`price_${currency.toLowerCase()}`] ?? 0;
    return sum + Number(price || 0) * Number(item.qty || 0);
  }, 0);
  const focusedProduct = products.find((product) => product._id === productId);
  const contactItems = checkoutPayment?.items?.length
    ? checkoutPayment.items.map((item) => ({ name: item.name || "Produk", qty: Number(item.qty || 1), unit_price: Number(item.unit_price || 0), subtotal: Number(item.subtotal || 0) }))
    : view === "deposit"
      ? [{ name: "Deposit saldo IDR", qty: 1, unit_price: Number(deposit?.amount || depositAmount || 0), subtotal: Number(deposit?.amount || depositAmount || 0) }]
      : view === "detail" && focusedProduct
        ? [{ name: focusedProduct.name, qty: Number(cart.find((item) => item.pid === focusedProduct._id)?.qty || 1), unit_price: Number(focusedProduct.price_idr_current ?? focusedProduct.price_idr ?? 0), subtotal: Number(focusedProduct.price_idr_current ?? focusedProduct.price_idr ?? 0) * Number(cart.find((item) => item.pid === focusedProduct._id)?.qty || 1) }]
        : cart.map((item) => {
          const product = products.find((row) => row._id === item.pid);
          const unit = Number(product?.price_idr_current ?? product?.price_idr ?? 0);
          return { name: product?.name || "Produk", qty: Number(item.qty || 1), unit_price: unit, subtotal: unit * Number(item.qty || 1) };
        });
  const contactName = profile?.first_name || profile?.email || "belum masuk";
  const contactQuantity = contactItems.reduce((sum, item) => sum + item.qty, 0);
  const contactTotal = view === "deposit" ? Number(deposit?.amount || depositAmount || 0) : (checkoutPayment?.total || quote?.total || contactItems.reduce((sum, item) => sum + item.subtotal, 0));
  const contactPaymentMethod = view === "deposit" ? "QRIS (deposit saldo)" : checkoutPayment ? `QRIS (${statusLabel(checkoutPayment.status)})` : cart.length ? "QRIS saat checkout" : "Belum dipilih";
  const contactProductText = contactItems.length
    ? contactItems.map((item) => `${item.name} — harga/unit ${fmtIDR(item.unit_price)}, jumlah ${item.qty}, subtotal ${fmtIDR(item.subtotal)}`).join("\n")
    : "Belum dipilih (silakan tulis produk yang ditanyakan)";
  const whatsappMessage = `Halo, saya ${contactName}, ingin menanyakan/memesan product:\n${contactProductText}\nHarga: ${fmtIDR(contactTotal)}\nTotal barang: ${contactQuantity}\nPembayaran: ${contactPaymentMethod}\nTolong segera di cek/di proses ya. Terimakasih.`;
  const reopenQris = async (orderId) => {
    try { const { data } = await api.get(`/store/orders/${orderId}/qris`); setCheckoutPayment(data); setQrisDialogOpen(true); setSelected(null); }
    catch (err) { setError(formatApiErrorDetail(err.response?.data?.detail) || "QRIS pesanan tidak dapat ditampilkan."); }
  };

  const content = (() => {
    if (["login", "register"].includes(view)) {
      const registering = view === "register";
      const title = resetMode ? "Reset kata sandi" : registering ? "Buat akun pengguna" : "Masuk ke akun";
      return <section className="mx-auto max-w-lg px-4 py-10 sm:px-6 sm:py-16">
        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-9">
          <p className="text-xs font-bold uppercase tracking-[.18em] text-emerald-800">IDSE Digital Product</p>
          <h1 className="mt-3 text-3xl font-bold tracking-tight text-slate-900">{title}</h1>
          <p className="mt-3 leading-6 text-slate-600">{resetMode ? "Kami akan mengirim kode reset ke email Anda." : registering ? "Daftar menggunakan email terverifikasi dan kata sandi. Telegram dapat dihubungkan nanti dari halaman akun." : "Masuk menggunakan email dan kata sandi akun IDSE Anda."}</p>
          <form className="mt-7 space-y-4" onSubmit={submitAccountForm}>
            <label className="block text-sm font-medium">Email<input className={`${input} mt-1.5`} type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
            {(!resetMode || codeSent) && <label className="block text-sm font-medium">{resetMode ? "Kata sandi baru" : "Kata sandi"}<input className={`${input} mt-1.5`} type="password" minLength={8} autoComplete={registering || resetMode ? "new-password" : "current-password"} required value={password} onChange={(e) => setPassword(e.target.value)} /></label>}
            {codeSent && <label className="block text-sm font-medium">Kode 6 digit<input className={`${input} mt-1.5`} inputMode="numeric" pattern="[0-9]{6}" maxLength={6} required value={code} onChange={(e) => setCode(e.target.value)} /></label>}
            {error && <p role="alert" className="rounded-lg bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}
            {notice && <p role="status" className="rounded-lg bg-emerald-50 p-3 text-sm text-emerald-900">{notice}</p>}
            <button className={`${button} w-full`} disabled={busy}>{busy ? "Memproses…" : resetMode ? (codeSent ? "Simpan kata sandi" : "Kirim kode reset") : registering ? (codeSent ? "Verifikasi dan buat akun" : "Kirim kode verifikasi") : "Masuk"}<ArrowRight size={17}/></button>
          </form>
          {!registering && !resetMode && <button className="mt-4 text-sm font-semibold text-emerald-800 hover:underline" onClick={() => {setResetMode(true);setCodeSent(false);setCode("");setNotice("");setError("");}}>Lupa kata sandi?</button>}
          {resetMode && <button className="mt-4 block text-sm font-semibold text-emerald-800 hover:underline" onClick={() => {setResetMode(false);setCodeSent(false);setCode("");setNotice("");setError("");}}>Kembali ke masuk</button>}
          {!resetMode && <p className="mt-7 border-t border-slate-100 pt-5 text-sm text-slate-600">{registering ? "Sudah punya akun? " : "Belum punya akun? "}<Link className="font-semibold text-emerald-800 hover:underline" to={registering ? "/store/login" : "/store/register"}>{registering ? "Masuk" : "Daftar"}</Link></p>}
        </div>
      </section>;
    }

    if (view === "cart") return <section className="mx-auto max-w-7xl px-4 py-8 sm:px-6 sm:py-12 lg:px-8">
      <div className="flex flex-wrap items-end justify-between gap-3"><div><p className="text-xs font-bold uppercase tracking-widest text-emerald-800">Belanja</p><h1 className="mt-2 text-3xl font-bold">Keranjang</h1></div><Link to="/store/products" className="text-sm font-semibold text-emerald-800 hover:underline">Lanjut pilih produk</Link></div>
      {cart.length ? <div className="mt-7 grid gap-6 lg:grid-cols-[1fr_350px]">
        <div className="space-y-3">{cart.map((item) => { const product = products.find((p) => p._id === item.pid); const min = Math.max(1, Number(product?.minimum_purchase_qty || 1)); const low = item.qty < min; return <article key={item.pid} className="grid grid-cols-[76px_1fr] gap-3 rounded-xl border border-slate-200 bg-white p-3 sm:grid-cols-[100px_1fr] sm:gap-5 sm:p-5">
          <div className="relative aspect-square overflow-hidden rounded-lg bg-slate-100"><ProductArtwork product={product}/>{product?.image_url && <img src={product.image_url} alt={product.name} className="absolute inset-0 h-full w-full bg-white object-contain p-1" onError={(e) => {e.currentTarget.style.display="none";}}/>}</div>
          <div className="min-w-0"><div className="flex items-start justify-between gap-2"><div><p className="font-semibold text-slate-900">{product?.name || "Produk tidak tersedia"}</p><p className="mt-1 text-sm text-slate-500">{product ? fmtIDR(product.price_idr_current ?? product.price_idr) + " / unit" : "Produk sudah tidak tersedia"}</p></div><button className="rounded-md p-2 text-slate-400 hover:bg-rose-50 hover:text-rose-700" aria-label="Hapus produk dari keranjang" onClick={() => removeCartItem(item.pid)}><Trash2 size={17}/></button></div>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-2"><button className="rounded-md border border-slate-300 p-2 disabled:opacity-40" aria-label="Kurangi jumlah" onClick={() => changeQty(item.pid, -1)} disabled={item.qty <= min}><Minus size={15}/></button><span className="min-w-8 text-center font-semibold">{item.qty}</span><button className="rounded-md border border-slate-300 p-2 disabled:opacity-40" aria-label="Tambah jumlah" onClick={() => changeQty(item.pid, 1)} disabled={product?.stock != null && item.qty >= product.stock}><Plus size={15}/></button></div><p className="font-semibold text-slate-800">{fmtIDR(quote?.items?.find((line) => line.product_id === item.pid)?.subtotal ?? (product?.price_idr_current || 0) * item.qty)}</p></div>
            {low && <p className="mt-2 text-xs font-medium text-rose-700">Minimum pembelian {min} pcs untuk produk ini.</p>}{product?.stock != null && <p className="mt-1 text-xs text-slate-500">Tersedia {product.stock}</p>}
          </div>
        </article>; })}</div>
        <aside className="h-fit rounded-xl border border-slate-200 bg-white p-5 shadow-sm lg:sticky lg:top-24">
          <h2 className="text-lg font-semibold">Ringkasan pesanan</h2><p className="mt-2 text-sm text-slate-500">Pembayaran web menggunakan QRIS dalam Rupiah.</p>
          <fieldset className="mt-4 space-y-2"><legend className="text-sm font-semibold text-slate-700">Metode pembayaran</legend><label className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 ${paymentMethod === "qris" ? "border-emerald-700 bg-emerald-50" : "border-slate-200 bg-white"}`}><input type="radio" name="store-payment-method" value="qris" checked={paymentMethod === "qris"} onChange={() => setPaymentMethod("qris")} className="mt-1 accent-emerald-800"/><span><b className="block text-sm text-slate-900">QRIS All Payment</b><span className="mt-1 block text-xs text-slate-500">Bayar langsung melalui e-wallet atau mobile banking.</span></span></label><label className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 ${paymentMethod === "balance" ? "border-emerald-700 bg-emerald-50" : "border-slate-200 bg-white"}`}><input type="radio" name="store-payment-method" value="balance" checked={paymentMethod === "balance"} onChange={() => setPaymentMethod("balance")} className="mt-1 accent-emerald-800"/><span><b className="block text-sm text-slate-900">Saldo IDR</b><span className="mt-1 block text-xs text-slate-500">{profile ? `Tersedia ${fmtIDR(profile.balance_idr || 0)} · ${profile.telegram_linked ? "saldo web dan Telegram digabung" : "menggunakan saldo web"}` : "Masuk untuk membayar dengan saldo akun."}</span></span></label></fieldset>
          <label className="mt-4 block text-sm font-medium">Kode kupon (opsional)<input className={`${input} mt-1.5`} value={couponCode} onChange={(e) => setCouponCode(e.target.value.toUpperCase())} maxLength={32} placeholder="Masukkan kode kupon" /></label>
          <div className="mt-5 space-y-2 border-t border-slate-100 pt-4 text-sm"><div className="flex justify-between"><span className="text-slate-500">Subtotal</span><span>{fmtIDR(quote?.subtotal ?? cartTotal)}</span></div>{Number(quote?.discount_total || 0) > 0 && <div className="flex justify-between text-emerald-800"><span>Diskon</span><span>−{fmtIDR(quote.discount_total)}</span></div>}<div className="flex justify-between border-t border-slate-100 pt-3 text-base font-bold"><span>Total pesanan</span><span>{fmtIDR(quote?.total ?? cartTotal)}</span></div></div>
          {quoteLoading && <p role="status" className="mt-2 text-xs text-slate-500">Memeriksa stok dan harga terbaru…</p>}
          {quoteError && <p role="alert" className="mt-3 rounded-lg bg-rose-50 p-3 text-sm text-rose-800">{quoteError}</p>}
          {quote?.coupon_error && <p role="alert" className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">{quote.coupon_error}</p>}
          {error && <p role="alert" className="mt-4 rounded-lg bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}
          <button className={`${button} mt-5 w-full`} onClick={checkout} disabled={busy || quoteLoading || Boolean(quoteError) || Boolean(quote?.coupon_error) || cart.some((item) => {const product = products.find((p) => p._id === item.pid); return !product || item.qty < Math.max(1, Number(product.minimum_purchase_qty || 1)) || (product.stock != null && item.qty > product.stock);})}>{busy ? "Memproses pembayaran…" : paymentMethod === "balance" ? <><CreditCard size={18}/> Bayar dengan Saldo</> : <><QrCode size={18}/> Bayar dengan QRIS</>}<ArrowRight size={17}/></button>
          <Link className="mt-3 flex items-center justify-center gap-2 text-sm font-semibold text-emerald-800" to="/store/deposit"><CreditCard size={16}/> Deposit saldo Telegram melalui QRIS</Link>
          <p className="mt-4 text-xs leading-5 text-slate-500">Checkout saldo memakai saldo web jika Telegram belum ditautkan. Setelah ditautkan, saldo web digabung satu kali ke saldo Telegram dan checkout memakai saldo gabungan. QRIS dibayar langsung dan diverifikasi otomatis.</p>
        </aside>
      </div> : <div className="mt-8 rounded-xl border border-slate-200 bg-white p-10 text-center"><ShoppingBag className="mx-auto text-slate-300" size={35}/><p className="mt-3 text-slate-600">Keranjang Anda masih kosong.</p><Link className={`${button} mt-5`} to="/store/products">Jelajahi produk</Link></div>}
    </section>;

    if (view === "profile") return <section className="mx-auto max-w-4xl px-4 py-8 sm:px-6 sm:py-12">
      <p className="text-xs font-bold uppercase tracking-widest text-emerald-800">Akun IDSE</p><h1 className="mt-2 text-3xl font-bold">Profil</h1>
      {profile && <div className="mt-6 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="flex items-center gap-4 border-b border-slate-100 p-5 sm:p-7"><div className="flex h-14 w-14 items-center justify-center rounded-full bg-emerald-100 text-lg font-bold text-emerald-900">{String(profile.email || "ID").slice(0, 2).toUpperCase()}</div><div className="min-w-0"><p className="break-all font-semibold text-slate-900">{profile.email}</p><p className="mt-1 text-sm text-slate-500">Akun pengguna</p></div></div>
        <div className="grid gap-3 p-5 sm:grid-cols-2 sm:p-7"><div className="rounded-lg border border-slate-200 bg-slate-50 p-4"><p className="text-sm text-slate-500">Saldo IDR</p><p className="mt-1 text-xl font-bold">{fmtIDR(profile.balance_idr)}</p></div><div className="rounded-lg border border-slate-200 bg-slate-50 p-4"><p className="text-sm text-slate-500">Saldo USD</p><p className="mt-1 text-xl font-bold">{fmtUSD(profile.balance_usd)}</p></div></div>
        <div className="mx-5 rounded-lg border border-slate-200 p-4 sm:mx-7"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="font-semibold">Telegram</p><p className="mt-1 text-sm text-slate-600">{profile.telegram_linked ? `Terhubung${profile.username ? ` · @${profile.username}` : ""}` : "Belum terhubung · opsional"}</p>{profile.telegram_linked && <p className="mt-1 text-xs text-slate-500">ID Telegram: {profile.telegram_id}. Saldo terhubung memakai saldo Telegram.</p>}</div><span className={`rounded-full px-3 py-1 text-xs font-semibold ${profile.telegram_linked ? "bg-emerald-100 text-emerald-900" : "bg-slate-100 text-slate-600"}`}>{profile.telegram_linked ? "Connected" : "Not connected"}</span></div>
          {!profile.telegram_linked && <><p className="mt-3 text-sm leading-6 text-slate-600">Akun toko dapat dipakai tanpa Telegram. Jika ditautkan, saldo web yang ada dipindahkan satu kali ke saldo Telegram. Setelah itu pembayaran saldo memakai saldo gabungan.</p><button className={`${secondary} mt-3`} onClick={createLinkCode}>Buat kode penghubung</button>{linkCode && <div className="mt-3 rounded-lg bg-slate-50 p-3 text-sm"><p>Buka {botName ? <a className="font-semibold text-emerald-800 underline" href={`https://t.me/${botName}`} target="_blank" rel="noreferrer">@{botName}</a> : "bot Telegram IDSE"} dan kirim perintah berikut:</p><code className="mt-2 block break-all font-mono font-semibold">/link {linkCode}</code><p className="mt-2 text-xs text-slate-500">Kode berlaku 10 menit. Muat ulang profil setelah menyelesaikan tautan.</p></div>}</>}
        </div>
        <div className="flex flex-wrap gap-2 p-5 sm:p-7"><Link className={button} to="/store/deposit">Deposit saldo</Link><Link className={secondary} to="/store/transactions">Riwayat transaksi</Link><Link className={secondary} to="/store/orders">Pesanan</Link><button className="ml-auto inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-slate-500 hover:bg-slate-50" onClick={logout}><LogOut size={16}/> Keluar</button></div>
      </div>}
    </section>;

    if (view === "deposit") return <section className="mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-12">
      <p className="text-xs font-bold uppercase tracking-widest text-emerald-800">Saldo</p><h1 className="mt-2 text-3xl font-bold">Deposit melalui QRIS</h1><p className="mt-3 max-w-2xl leading-6 text-slate-600">Pembayaran diperiksa otomatis oleh sistem. Saldo hanya ditambahkan setelah transaksi QRIS terverifikasi.</p>
      <div className="mt-7 grid gap-6 lg:grid-cols-[1fr_360px]">
        <div className="rounded-xl border border-slate-200 bg-white p-5 sm:p-7"><h2 className="text-lg font-semibold">Buat permintaan deposit</h2><form className="mt-5" onSubmit={createDeposit}><label className="block text-sm font-medium">Nominal saldo (IDR)<input className={`${input} mt-1.5`} type="number" min="10000" max="10000000" step="1000" required value={depositAmount} onChange={(e) => setDepositAmount(e.target.value)} /></label><p className="mt-2 text-xs text-slate-500">Nominal Rp10.000–Rp10.000.000. QRIS menampilkan total pembayaran termasuk biaya dan kode unik.</p>{error && <p role="alert" className="mt-4 rounded-lg bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}{notice && <p role="status" className="mt-4 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-900">{notice}</p>}<button className={`${button} mt-5 w-full sm:w-auto`} disabled={busy}>{busy ? "Membuat QRIS…" : "Buat QRIS"}<ArrowRight size={17}/></button></form>
          {deposit && <div className="mt-7 rounded-xl border border-emerald-200 bg-emerald-50/50 p-4 sm:p-6"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-sm font-semibold">QRIS All Payment · {deposit.deposit_id}</p><p className="mt-1 text-xs text-slate-500">Batas waktu {dateLabel(deposit.expires_at)}</p></div><span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-900">{statusLabel(depositExpired ? "expired" : deposit.status)}</span></div><div className="mt-4 grid gap-3 sm:grid-cols-2"><div className="rounded-lg bg-white p-3"><p className="text-xs text-slate-500">Saldo ditambahkan</p><p className="mt-1 font-bold">{fmtIDR(deposit.amount)}</p></div><div className="rounded-lg bg-white p-3"><p className="text-xs text-slate-500">Bayar tepat sejumlah</p><p className="mt-1 font-bold text-emerald-900">{fmtIDR(deposit.payment_amount)}</p></div></div>{deposit.status === "pending" && !depositExpired ? <div className="mt-5 flex flex-col items-center rounded-lg bg-white p-4"><img className="h-64 w-64 max-w-full object-contain" src={deposit.qr_image} alt="QRIS All Payment"/><p className="mt-3 text-center text-sm font-semibold">Cara pembayaran</p><p className="mt-1 text-center text-sm text-slate-600">Pindai melalui aplikasi e-wallet atau mobile banking yang mendukung QRIS.</p><p className="mt-3 text-center text-xs font-semibold text-amber-800">⏳ QR hanya berlaku {deposit.expires_in_minutes || 5} menit.</p></div> : depositExpired || deposit.status === "expired" ? <p role="status" className="mt-5 rounded-lg bg-amber-50 p-4 text-sm text-amber-900">Kode QR sudah tidak berlaku karena waktu pembayaran habis. Silakan minta QR baru untuk deposit.</p> : <p role="status" className="mt-5 rounded-lg bg-emerald-50 p-4 text-sm text-emerald-900">Status deposit: {statusLabel(deposit.status)}.</p>}</div>}
        </div>
        <aside className="h-fit rounded-xl border border-slate-200 bg-white p-5"><h2 className="font-semibold">Status deposit terakhir</h2><div className="mt-4 space-y-3">{depositRows.slice(0, 5).map((row) => <div key={row.deposit_id} className="rounded-lg bg-slate-50 p-3"><div className="flex justify-between gap-2"><span className="break-all font-mono text-xs">{row.deposit_id}</span><span className="text-xs font-semibold">{statusLabel(row.status)}</span></div><div className="mt-2 flex justify-between text-sm"><span>{fmtIDR(row.credited_amount || row.amount)}</span><span className="text-slate-500">{dateLabel(row.created_at)}</span></div></div>)}{!depositRows.length && <p className="text-sm text-slate-500">Belum ada deposit.</p>}</div><Link className="mt-5 inline-block text-sm font-semibold text-emerald-800 hover:underline" to="/store/transactions">Lihat semua transaksi →</Link></aside>
      </div>
    </section>;

    if (view === "transactions" || view === "orders") {
      const rows = view === "orders" ? orders.map((order) => ({...order, type: "order", reference: order.invoice_id, amount: order.total})) : transactions.filter((row) => transactionFilter === "all" || row.type === transactionFilter);
      return <section className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-12">
        <div className="flex flex-wrap items-end justify-between gap-3"><div><p className="text-xs font-bold uppercase tracking-widest text-emerald-800">Akun</p><h1 className="mt-2 text-3xl font-bold">{view === "orders" ? "Riwayat pesanan" : "Riwayat transaksi"}</h1></div>{view === "transactions" && <div className="flex gap-2">{[["all", "Semua"], ["deposit", "Deposit"], ["order", "Pesanan"]].map(([value, label]) => <button key={value} className={`rounded-lg border px-3 py-2 text-sm font-medium ${transactionFilter === value ? "border-emerald-800 bg-emerald-50 text-emerald-900" : "border-slate-200 bg-white text-slate-600"}`} onClick={() => setTransactionFilter(value)}>{label}</button>)}</div>}</div>
        {notice && <p role="status" className="mt-5 rounded-lg bg-emerald-50 p-4 text-sm text-emerald-900">{notice}</p>}
        <div className="mt-6 overflow-hidden rounded-xl border border-slate-200 bg-white">
          <div className="hidden grid-cols-[1.2fr_1fr_1fr_1fr_1fr] gap-3 border-b border-slate-200 bg-slate-50 px-5 py-3 text-xs font-semibold uppercase tracking-wide text-slate-500 sm:grid"><span>Referensi</span><span>Jenis</span><span>Tanggal</span><span>Jumlah</span><span>Status</span></div>
          {rows.map((row) => <button key={row.id || row._id} onClick={() => setSelected(row)} className="grid w-full gap-2 border-b border-slate-100 px-4 py-4 text-left transition hover:bg-slate-50 sm:grid-cols-[1.2fr_1fr_1fr_1fr_1fr] sm:items-center sm:gap-3 sm:px-5"><span className="font-mono text-sm font-semibold text-emerald-900">{row.reference || row.invoice_id || "—"}</span><span className="text-sm text-slate-600">{row.type === "deposit" ? `Deposit · ${row.payment_method || "QRIS"}` : "Pesanan"}</span><span className="text-xs text-slate-500">{dateLabel(row.created_at)}</span><span className="text-sm font-semibold">{row.currency === "USD" ? fmtUSD(row.amount) : fmtIDR(row.amount)}</span><span className="w-fit rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-700">{statusLabel(row.status)}</span></button>)}
          {!rows.length && <div className="px-5 py-12 text-center text-sm text-slate-500">Belum ada {view === "orders" ? "pesanan" : "transaksi"}.</div>}
        </div>
        {selected && <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/40 p-0 sm:items-center sm:p-4" role="presentation" onClick={(e) => { if (e.target === e.currentTarget) setSelected(null); }}><section role="dialog" aria-modal="true" aria-labelledby="transaction-title" className="max-h-[90vh] w-full overflow-y-auto rounded-t-2xl bg-white p-5 shadow-xl sm:max-w-xl sm:rounded-2xl sm:p-7"><div className="flex items-start justify-between gap-4"><div><p className="text-xs font-bold uppercase tracking-widest text-emerald-800">Detail transaksi</p><h2 id="transaction-title" className="mt-2 break-all text-xl font-bold">{selected.reference || selected.invoice_id}</h2></div><button className="rounded-lg p-2 hover:bg-slate-100" onClick={() => setSelected(null)} aria-label="Tutup"><X size={18}/></button></div><div className="mt-5 grid gap-3 sm:grid-cols-2"><div className="rounded-lg bg-slate-50 p-3"><p className="text-xs text-slate-500">Status</p><p className="mt-1 font-semibold">{statusLabel(selected.status)}</p></div><div className="rounded-lg bg-slate-50 p-3"><p className="text-xs text-slate-500">Waktu</p><p className="mt-1 text-sm font-medium">{dateLabel(selected.created_at)}</p></div><div className="rounded-lg bg-slate-50 p-3"><p className="text-xs text-slate-500">Jumlah</p><p className="mt-1 font-semibold">{selected.currency === "USD" ? fmtUSD(selected.amount) : fmtIDR(selected.amount)}</p></div><div className="rounded-lg bg-slate-50 p-3"><p className="text-xs text-slate-500">Pembayaran</p><p className="mt-1 font-semibold">{selected.payment_method || (selected.type === "deposit" ? "QRIS" : "Saldo")}</p></div></div>{selected.status === "pending_payment" && selected.payment_method === "qris" && <button className={`${button} mt-4 w-full`} onClick={() => reopenQris(selected._id)}><QrCode size={17}/> Tampilkan QRIS untuk bayar</button>}{selected.items?.length > 0 && <div className="mt-5"><h3 className="font-semibold">Produk</h3><div className="mt-2 space-y-2">{selected.items.map((item, idx) => <div key={idx} className="rounded-lg border border-slate-200 p-3"><p className="font-medium">{item.name} × {item.qty}</p><p className="mt-1 text-sm text-slate-500">{selected.currency === "USD" ? fmtUSD(item.unit_price) : fmtIDR(item.unit_price)} / unit · Subtotal {selected.currency === "USD" ? fmtUSD(item.subtotal) : fmtIDR(item.subtotal)}</p></div>)}</div></div>}{selected.delivery_email_status && <p className="mt-4 text-sm text-slate-600">Status email produk: {statusLabel(selected.delivery_email_status === "sent" ? "delivered" : selected.delivery_email_status)}</p>}</section></div>}
      </section>;
    }

    if (view === "detail") {
      const product = products.find((row) => row._id === productId);
      if (!product) return <section className="mx-auto max-w-7xl px-4 py-12 sm:px-6 lg:px-8"><Link className="text-sm text-emerald-800" to="/store/products">← Kembali ke katalog</Link><p className="mt-6 rounded-xl bg-white p-8 text-center text-slate-500">Produk tidak ditemukan atau sedang tidak tersedia.</p></section>;
      const min = Math.max(1, Number(product.minimum_purchase_qty || 1));
      const noStock = product.stock === 0 || (product.stock != null && product.stock < min);
      return <section className="mx-auto grid max-w-7xl gap-8 px-4 py-8 sm:px-6 sm:py-12 md:grid-cols-2 lg:px-8"><div className="relative aspect-square overflow-hidden rounded-xl border border-slate-200 bg-slate-100"><ProductArtwork product={product}/>{product.image_url && <img src={product.image_url} alt={product.name} className="absolute inset-0 h-full w-full bg-white object-contain p-5" onError={(e) => {e.currentTarget.style.display="none";}}/>}</div><div className="self-center"><Link className="text-sm font-semibold text-emerald-800 hover:underline" to="/store/products">← Semua produk</Link><p className="mt-7 text-xs font-semibold uppercase tracking-widest text-slate-500">{productTypeLabel(product)}</p><h1 className="mt-2 text-3xl font-bold text-slate-900 sm:text-4xl">{product.name}</h1><p className="mt-5 whitespace-pre-line leading-7 text-slate-600">{product.description || "Produk pilihan dari IDSE Digital Product."}</p><p className="mt-7 text-2xl font-bold text-emerald-900">{money(product, currency)}</p><p className={`mt-2 text-sm ${noStock ? "text-rose-700" : "text-slate-500"}`}>{noStock ? "Stok tidak mencukupi minimum pembelian" : product.stock == null ? "Stok tersedia" : `${product.stock} tersedia`}</p>{min > 1 && <p className="mt-2 text-sm font-medium text-slate-600">Minimum pembelian {min} pcs</p>}<button className={`${button} mt-7 w-full sm:w-auto`} onClick={() => add(product)} disabled={noStock}>Tambah ke keranjang <Plus size={17}/></button></div></section>;
    }

    if (view === "products") return <section className="mx-auto max-w-7xl px-4 py-8 sm:px-6 sm:py-12 lg:px-8"><div className="flex flex-wrap items-end justify-between gap-4"><div><p className="text-xs font-bold uppercase tracking-widest text-emerald-800">Katalog IDSE</p><h1 className="mt-2 text-3xl font-bold">Temukan produk</h1></div><label className="relative block w-full sm:max-w-sm"><Search className="absolute left-3 top-3.5 text-slate-400" size={18}/><input className={`${input} pl-10`} placeholder="Cari nama atau deskripsi produk" value={search} onChange={(e) => setSearch(e.target.value)}/></label></div><CatalogFilter value={productFilter} setValue={setProductFilter}/>{error && <p role="alert" className="mt-5 rounded-lg bg-rose-50 p-4 text-rose-800">{error}</p>}{productLoading ? <p role="status" className="mt-7 rounded-xl bg-white p-8 text-center text-slate-500">Memuat produk…</p> : <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">{filteredProducts.map((p) => <ProductCard key={p._id} product={p} add={add}/>)}</div>}{!productLoading && !error && !filteredProducts.length && <p className="mt-6 rounded-xl bg-white p-8 text-center text-slate-500">Produk tidak ditemukan.</p>}</section>;

    return <>
      <section className="border-b border-slate-200 bg-white"><div className="mx-auto grid max-w-7xl gap-8 px-4 py-12 sm:px-6 sm:py-16 md:grid-cols-[1.15fr_.85fr] md:items-center lg:px-8 lg:py-20"><div><p className="text-xs font-bold uppercase tracking-[.2em] text-emerald-800">IDSE Digital Product</p><h1 className="mt-4 max-w-2xl text-4xl font-bold leading-tight tracking-tight text-slate-950 sm:text-5xl">Produk digital, lebih mudah ditemukan dan dibeli.</h1><p className="mt-5 max-w-xl text-base leading-7 text-slate-600">Jelajahi katalog, cek ketersediaan, lalu bayar langsung melalui QRIS. Status pesanan diperbarui setelah pembayaran terverifikasi.</p><form className="mt-7 flex max-w-xl gap-2" onSubmit={(e) => {e.preventDefault();navigate("/store/products");}}><label className="relative min-w-0 flex-1"><Search className="absolute left-3 top-3.5 text-slate-400" size={18}/><input className={`${input} pl-10`} placeholder="Cari produk digital" value={search} onChange={(e) => setSearch(e.target.value)}/></label><button className={button}>Cari</button></form><Link to="/store/products" className="mt-4 inline-flex items-center gap-2 text-sm font-semibold text-emerald-800 hover:underline">Lihat semua produk <ArrowRight size={16}/></Link></div><div className="grid gap-3 sm:grid-cols-3 md:grid-cols-1"><TrustItem icon={<PackageCheck/>} title="Ketersediaan tercatat" text="Stok katalog mengikuti inventory yang tersedia."/><TrustItem icon={<ShieldCheck/>} title="QRIS terverifikasi" text="Pesanan hanya diproses setelah pembayaran dikonfirmasi."/><TrustItem icon={<Clock3/>} title="Status pesanan jelas" text="Riwayat menampilkan proses dan hasil order."/></div></div></section>
      <section className="mx-auto max-w-7xl px-4 py-10 sm:px-6 sm:py-14 lg:px-8"><div className="flex flex-wrap items-end justify-between gap-4"><div><p className="text-xs font-bold uppercase tracking-widest text-emerald-800">Jelajahi</p><h2 className="mt-2 text-2xl font-bold">Pilihan produk</h2></div><Link className="text-sm font-semibold text-emerald-800 hover:underline" to="/store/products">Buka katalog →</Link></div><CatalogFilter value={productFilter} setValue={setProductFilter}/>
        {promotedProducts.length > 0 && <div className="mt-10"><SectionTitle eyebrow="Harga promo aktif" title="Penawaran saat ini" href="/store/products"/><div className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{promotedProducts.slice(0,3).map((p)=><ProductCard key={p._id} product={p} add={add}/>)}</div></div>}
        <div className="mt-10"><SectionTitle eyebrow="Pilihan terbaru" title="Produk tersedia" href="/store/products"/>{error ? <p role="alert" className="mt-5 rounded-lg bg-rose-50 p-4 text-rose-800">{error}</p> : productLoading ? <p className="mt-5 rounded-xl bg-white p-8 text-center text-slate-500">Memuat produk…</p> : <div className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{filteredProducts.slice(0, 6).map((p) => <ProductCard key={p._id} product={p} add={add}/>)}</div>}{!productLoading && !error && !products.length && <p className="mt-5 rounded-xl bg-white p-8 text-center text-slate-500">Belum ada produk aktif.</p>}</div>
      </section>
    </>;
  })();

  return <div className={`${page} storefront-font`}><Header count={cart.reduce((sum, item) => sum + item.qty, 0)} profile={profile} onLogout={logout}/>{content}<footer className="border-t border-slate-200 bg-white"><div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-4 py-7 text-sm text-slate-500 sm:px-6 lg:px-8"><span>© {new Date().getFullYear()} IDSE Digital Product</span><div className="flex flex-wrap gap-4"><Link to="/store/products">Produk</Link><Link to="/store/deposit">Deposit</Link><Link to="/store/transactions">Transaksi</Link>{profile && <Link to="/store/profile">Akun</Link>}</div></div></footer>
    {qrisDialogOpen && checkoutPayment && <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/45 p-0 sm:items-center sm:p-4" role="presentation" onClick={(event) => { if (event.target === event.currentTarget) setQrisDialogOpen(false); }}><section role="dialog" aria-modal="true" aria-labelledby="qris-title" className="max-h-[92vh] w-full overflow-y-auto rounded-t-2xl bg-white p-5 shadow-2xl sm:max-w-lg sm:rounded-2xl sm:p-7"><div className="flex items-start justify-between gap-4"><div><p className="text-xs font-bold uppercase tracking-widest text-emerald-800">Pembayaran terverifikasi otomatis</p><h2 id="qris-title" className="mt-2 text-xl font-bold text-slate-900">QRIS All Payment</h2></div><button aria-label="Tutup" className="rounded-lg p-2 text-slate-500 hover:bg-slate-100" onClick={() => setQrisDialogOpen(false)}><X size={18}/></button></div><div className="mt-4 grid grid-cols-2 gap-3"><div className="rounded-lg bg-slate-50 p-3"><p className="text-xs text-slate-500">Invoice</p><p className="mt-1 break-all font-mono text-sm font-semibold">{checkoutPayment.invoice_id}</p></div><div className="rounded-lg bg-slate-50 p-3"><p className="text-xs text-slate-500">Status</p><p className="mt-1 text-sm font-semibold">{statusLabel(checkoutExpired ? "expired" : checkoutPayment.status)}</p></div></div>
      {checkoutPayment.status === "pending_payment" && !checkoutExpired ? <><div className="mt-4 flex justify-between rounded-lg border border-emerald-100 bg-emerald-50 p-4"><span className="text-sm text-slate-600">Bayar tepat sejumlah</span><b className="text-lg text-emerald-950">{fmtIDR(checkoutPayment.payment_amount)}</b></div><p className="mt-2 text-xs text-slate-500">QR hanya berlaku {checkoutPayment.expires_in_minutes || 5} menit, sampai {dateLabel(checkoutPayment.expires_at)}. Nominal sudah termasuk biaya layanan dan kode unik.</p>{checkoutPayment.qr_image && <div className="mt-4 flex flex-col items-center rounded-xl border border-slate-200 p-4"><img className="h-64 w-64 max-w-full object-contain" src={checkoutPayment.qr_image} alt="QRIS All Payment"/><p className="mt-3 text-center text-sm font-medium text-slate-800">Cara pembayaran</p><p className="mt-1 text-center text-sm text-slate-600">Pindai melalui aplikasi e-wallet atau mobile banking yang mendukung QRIS.</p><p className="mt-1 text-center text-xs text-slate-500">Status pembayaran akan diperiksa otomatis.</p></div>}</> : checkoutExpired || checkoutPayment.status === "expired" ? <p role="status" className="mt-4 rounded-lg bg-amber-50 p-4 text-sm text-amber-900">Kode QR sudah tidak berlaku karena waktu pembayaran habis. Silakan buat checkout baru untuk meminta QRIS baru.</p> : <p role="status" className="mt-4 rounded-lg bg-emerald-50 p-4 text-sm text-emerald-900">{checkoutPayment.status === "delivered" ? "Pembayaran terverifikasi. Rincian pesanan dikirim ke email." : checkoutPayment.status === "service_waiting" ? "Pembayaran terverifikasi. Pesanan layanan sedang diproses." : checkoutPayment.status === "delivery_failed" ? "Pembayaran terverifikasi, tetapi tim perlu memeriksa pengiriman." : `Status pembayaran: ${statusLabel(checkoutPayment.status)}.`}</p>}
      <div className="mt-5 flex flex-wrap justify-end gap-2"><button className={secondary} onClick={() => { setQrisDialogOpen(false); navigate("/store/orders"); }}>Lihat pesanan</button><button className={button} onClick={() => setQrisDialogOpen(false)}>Tutup</button></div></section></div>}
    <ContactBubbles whatsappNumber={contactConfig.whatsapp_contact_number} telegramTarget={contactConfig.telegram_contact_target} message={whatsappMessage} />
  </div>;
}

function CatalogFilter({ value, setValue }) {
  const options = [["all", "Semua", Sparkles], ["bestseller", "Terlaris", TrendingUp], ["ready", "Ready Stock", PackageCheck], ["out", "Out of Stock", PackageX], ["service", "Jasa Payment", Headset]];
  return <div className="mt-5 flex gap-2 overflow-x-auto pb-1" role="group" aria-label="Filter produk"><div className="flex min-w-max gap-2">{options.map(([key, label, Icon]) => <button key={key} onClick={() => setValue(key)} aria-pressed={value === key} className={`inline-flex shrink-0 items-center gap-2 rounded-full border px-4 py-2 text-sm font-medium transition ${value === key ? "border-emerald-800 bg-emerald-800 text-white" : "border-slate-200 bg-white text-slate-600 hover:border-emerald-300 hover:text-emerald-900"}`}><Icon size={15}/>{label}</button>)}</div></div>;
}

function ContactBubbles({ whatsappNumber, telegramTarget, message }) {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    if (!whatsappNumber && !telegramTarget) return undefined;
    const expiryKey = "idse_contact_bubbles_expires_at";
    const dismissedKey = "idse_contact_bubbles_dismissed_until";
    const now = Date.now();
    let expiry = 0;
    let dismissedUntil = 0;
    try {
      expiry = Number(window.localStorage.getItem(expiryKey) || 0);
      dismissedUntil = Number(window.localStorage.getItem(dismissedKey) || 0);
      if (!expiry || expiry <= now) {
        expiry = now + 2 * 60 * 1000;
        window.localStorage.setItem(expiryKey, String(expiry));
        window.localStorage.removeItem(dismissedKey);
        dismissedUntil = 0;
      }
    } catch (_) { expiry = now + 2 * 60 * 1000; }
    if (dismissedUntil > now) { setVisible(false); return undefined; }
    setVisible(true);
    const timer = window.setTimeout(() => {
      setVisible(false);
      try { window.localStorage.removeItem(expiryKey); window.localStorage.removeItem(dismissedKey); } catch (_) {}
    }, Math.max(0, expiry - now));
    return () => window.clearTimeout(timer);
  }, [whatsappNumber, telegramTarget]);

  const close = () => {
    setVisible(false);
    try {
      const expiry = Number(window.localStorage.getItem("idse_contact_bubbles_expires_at") || 0);
      window.localStorage.setItem("idse_contact_bubbles_dismissed_until", String(expiry || (Date.now() + 2 * 60 * 1000)));
    } catch (_) {}
  };
  if (!visible) return null;
  const digits = String(whatsappNumber || "").replace(/\D/g, "");
  const waNumber = digits.startsWith("0") ? `62${digits.slice(1)}` : digits;
  const waHref = waNumber ? `https://wa.me/${waNumber}?text=${encodeURIComponent(message)}` : "";
  let tgHref = String(telegramTarget || "").trim();
  if (/^@?[A-Za-z0-9_]{5,32}$/.test(tgHref)) tgHref = `https://t.me/${tgHref.replace(/^@/, "")}`;
  if (!/^https:\/\/(t\.me|telegram\.me)\//i.test(tgHref)) tgHref = "";
  if (!waHref && !tgHref) return null;
  return <div className="fixed bottom-5 right-4 z-40 flex flex-col items-end gap-2 sm:bottom-7 sm:right-7" aria-label="Kontak toko">
    <button type="button" aria-label="Tutup tombol kontak" title="Tutup" onClick={close} className="grid h-8 w-8 place-items-center rounded-full border border-slate-200 bg-white text-slate-500 shadow-md transition hover:bg-slate-100"><X size={16}/></button>
    {waHref && <a href={waHref} target="_blank" rel="noreferrer" aria-label="Hubungi IDSE melalui WhatsApp" title="WhatsApp" className="grid h-14 w-14 place-items-center rounded-full bg-[#25D366] text-white shadow-lg transition hover:scale-105 hover:shadow-xl focus:outline-none focus:ring-4 focus:ring-emerald-300"><WhatsAppLogo/></a>}
    {tgHref && <a href={tgHref} target="_blank" rel="noreferrer" aria-label="Hubungi IDSE melalui Telegram" title="Telegram" className="grid h-14 w-14 place-items-center rounded-full bg-[#229ED9] text-white shadow-lg transition hover:scale-105 hover:shadow-xl focus:outline-none focus:ring-4 focus:ring-sky-300"><TelegramLogo/></a>}
  </div>;
}

function WhatsAppLogo() {
  return <svg viewBox="0 0 32 32" width="30" height="30" aria-hidden="true" fill="currentColor"><path d="M16.04 3.2A12.7 12.7 0 0 0 5.19 22.5L3.5 28.7l6.35-1.67A12.7 12.7 0 1 0 16.04 3.2Zm0 23.08a10.35 10.35 0 0 1-5.27-1.44l-.38-.22-3.77.99 1-3.67-.25-.38a10.37 10.37 0 1 1 8.67 4.72Zm5.69-7.77c-.31-.16-1.83-.9-2.12-1s-.49-.16-.7.16c-.2.31-.8 1-1 1.21-.19.2-.36.23-.67.08-.31-.16-1.3-.48-2.47-1.52-.91-.81-1.53-1.81-1.71-2.12-.18-.31-.02-.48.14-.64.14-.14.31-.36.47-.54.15-.18.2-.31.31-.52.1-.2.05-.39-.03-.54-.08-.16-.7-1.68-.96-2.3-.25-.6-.5-.52-.7-.53h-.6c-.2 0-.54.08-.82.39-.28.31-1.08 1.05-1.08 2.56s1.1 2.97 1.26 3.18c.15.2 2.16 3.3 5.24 4.63.73.31 1.3.5 1.75.64.73.23 1.4.2 1.93.12.59-.09 1.82-.75 2.08-1.48.25-.72.25-1.35.18-1.48-.08-.13-.28-.21-.59-.36Z"/></svg>;
}

function TelegramLogo() {
  return <svg viewBox="0 0 24 24" width="29" height="29" aria-hidden="true" fill="currentColor"><path d="M21.7 4.2 18.5 20c-.24 1.12-.9 1.4-1.82.87l-5.03-3.7-2.43 2.34c-.27.28-.5.5-1.03.5l.36-5.1 9.28-8.39c.4-.36-.09-.56-.62-.2L5.75 13.55.78 12c-1.08-.34-1.1-1.08.23-1.6L20.45 2.8c.9-.33 1.68.22 1.25 1.4Z"/></svg>;
}

function SectionTitle({ eyebrow, title, href }) {
  return <div className="flex items-end justify-between gap-3"><div><p className="text-xs font-bold uppercase tracking-widest text-emerald-800">{eyebrow}</p><h2 className="mt-2 text-2xl font-bold">{title}</h2></div>{href && <Link className="text-sm font-semibold text-emerald-800 hover:underline" to={href}>Lihat semua →</Link>}</div>;
}

function TrustItem({ icon, title, text }) {
  return <div className="flex gap-3 rounded-xl border border-slate-200 bg-slate-50 p-4"><span className="text-emerald-800">{icon}</span><div><p className="text-sm font-semibold text-slate-800">{title}</p><p className="mt-1 text-xs leading-5 text-slate-500">{text}</p></div></div>;
}
