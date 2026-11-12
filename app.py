"""
app.py - PC x WEI | Monthly Competition Recap  (Streamlit, dijalankan lokal)

Jalankan :  streamlit run app.py
Pengaturan yang sering diubah ada di blok CONFIG di bawah ini (tidak perlu file config terpisah).
"""
# =============================================================================
# CONFIG - ubah di sini
# =============================================================================
CONFIG = {
    "APP_TITLE": "PC × WEI · Monthly Competition Recap",
    "APP_SUBTITLE": "Rekap juara challenge: Most Live Streaming · Most Short Video · Creator Highest GMV",
    "ORG_CREDIT": "Warna Emas Indonesia · Tim Data Analyst",
    "LOGO_PATH": "assets/logo.png",            # opsional; kalau file ada, tampil di sidebar
    "YEAR": 2026,

    # --- Google Sheets (mode "Google Sheets") ---
    "INT_SPREADSHEET": "https://docs.google.com/spreadsheets/d/1uG-NEUU5NgkSkvLyHanLBuXiYr4fcEaoZdnO8pbBlrM/edit",
    "RAW_SPREADSHEET": "https://docs.google.com/spreadsheets/d/1MiYHTWLTuWBtTKHDHSqxTCtiWRlHK3xN5DcSid-pwgY/edit",
    "TAB_COMPETITION": "Monthly Competition",
    "TAB_RAW_LS": "RAW LS",
    "TAB_RAW_SV": "Raw SV",
    "RANGE_COMPETITION": "A1:P200",
    "RANGE_RAW_LS": "A:P",        # kolom Source ... Duration
    "RANGE_RAW_SV": "A:L",        # kolom Source ... Creator video-attributed GMV
    "RANGE_TIER": "A1:M",         # Username (kolom C) dan Tiering Acquire (kolom M)
    # file kredensial Google, dicoba berurutan (di folder app.py atau path penuh).
    # JSON service account -> dipakai langsung; JSON OAuth client -> login lewat browser.
    "CREDENTIAL_FILES": ["service_account.json", "credentials.json"],

    # --- tab brand di INT yang dipakai sebagai tier mapping (kolom M) ---
    "BRAND_TABS": {
        "PEPSODENT": "Pepsodent (OC)", "CLOSE UP": "Close Up (OC)", "LIFEBUOY": "Lifebuoy (SCL)",
        "DOVE SCL": "Dove (SCL)", "LUX": "Lux (SCL)", "DOVE DEO": "Dove (Deo)", "REXONA": "Rexona (Deo)",
    },

    # --- aturan (bisa disesuaikan) ---
    # Tier di kolom M halaman brand  ->  Tier/Group di Monthly Competition
    "TIER_GROUP_MAP": {"content": "INCUBATION", "incubation": "INCUBATION", "rising": "RISING",
                       "top": "TOP", "tier 1+": "TOP", "tier1+": "TOP"},
    "BRAND_ALIASES": {
        "PEPSODENT": ["pepsodent"], "CLOSE UP": ["close up", "closeup"], "LIFEBUOY": ["lifebuoy"],
        "DOVE SCL": ["dove skin cleansing", "dove scl", "dove (scl)"], "LUX": ["lux"], "REXONA": ["rexona"],
        "DOVE DEO": ["dove deodorant", "dove deo", "dove (deo)"],
    },
    # File TAP tidak punya kolom Brand -> brand diturunkan dari Product name (urutan penting)
    "BRAND_PRODUCT_RULES": [
        ("DOVE DEO", r"dove.*(\bdeo|roll\s*-?on|anti-?\s*perspirant|ketiak|underarm)"),
        ("DOVE SCL", r"\bdove\b"), ("PEPSODENT", r"pepsodent"), ("CLOSE UP", r"close\s*-?\s*up"),
        ("LIFEBUOY", r"lifebuoy"), ("LUX", r"\blux\b"), ("REXONA", r"rexona"),
    ],

    # --- nilai awal parameter di sidebar ---
    "DEFAULTS": {
        "hashtag_mode": "Semua hashtag wajib",
        "strip_month_suffix": True,        # SEPT / OKT / OCT dianggap sama
        "autocorrect_sheet_hashtag": True, # #MostLS... yang salah salin di challenge SV dikoreksi
        "mention_policy": "Ikuti mechanism",
        "default_mention": "dailycarebyunilever",
        "filter_brand": True,
        "require_tier": True,
        "min_post_days_sv": 25,            # 50 SV / 2 SV per hari (contoh di mechanism); 0 = tidak diterapkan
        "min_content_mode": "10 SV ATAU 10 LS",
        "require_post_in_period": True,
        "near_miss_extra": 5,
        "fill_mode": "Performa saja",      # "Ketat" | "Syarat minimum longgar" | "Longgar penuh" | "Performa saja" (slot hadiah selalu terisi)
        "unique_winner": True,             # 1 creator hanya boleh juara 1x (lintas brand & kategori); slot terbesar diisi dulu
    },
}
# =============================================================================

import html
import os
import re
import time

import pandas as pd
import streamlit as st

import engine as E
import export as X
import io_sources as IO

FILL_MODES = {
    "Ketat": ("strict", "Hanya creator yang memenuhi semua syarat yang bisa juara. Slot bisa kosong."),
    "Syarat minimum longgar": ("minimum", "Slot tetap diisi: yang memenuhi syarat lebih dulu, lalu creator ber-hashtag yang belum "
                                          "memenuhi syarat minimum (jam / SV / hari / GMV), urut performa terbaik."),
    "Longgar penuh": ("all", "Seperti di atas, ditambah creator yang kurang hashtag/mention bila slot masih kosong. "
                             "Semua pemenang yang tidak memenuhi syarat ditandai di kolom Status."),
    "Performa saja": ("any", "Seperti 'Longgar penuh', ditambah creator yang sama sekali tidak mencantumkan hashtag challenge "
                             "(brand, periode, dan tier tetap harus sesuai). Slot hadiah selalu terisi; yang patuh syarat tetap diurutkan lebih dulu."),
}
PAGES = ["1 · Setup Data", "2 · Ringkasan", "3 · Most Live Streaming", "4 · Most Short Video",
         "5 · Creator Highest GMV", "6 · Export & Audit"]
PAGE_CAT = {PAGES[2]: E.CAT_LS, PAGES[3]: E.CAT_SV, PAGES[4]: E.CAT_GMV}
NAVY, GOLD = "#0F2A47", "#C8A24A"

CSS = f"""
<style>
.block-container {{ padding-top: 1.3rem; max-width: 1400px; }}
h1, h2, h3, h4 {{ color: {NAVY}; letter-spacing: -0.01em; }}
.app-banner {{ background: linear-gradient(120deg, {NAVY} 0%, #1B4573 100%); color: #fff; padding: 18px 24px;
  border-radius: 14px; margin-bottom: 14px; border-bottom: 3px solid {GOLD}; }}
.app-banner .t {{ font-size: 1.5rem; font-weight: 700; margin: 0; }}
.app-banner .s {{ color: #D6E2F0; font-size: .92rem; margin-top: 2px; }}
.kpi {{ background: #fff; border: 1px solid #E3E9F1; border-radius: 12px; padding: 13px 16px;
  box-shadow: 0 1px 2px rgba(15,42,71,.05); height: 100%; }}
.kpi .l {{ font-size: .72rem; color: #5B6B7F; text-transform: uppercase; letter-spacing: .06em; }}
.kpi .v {{ font-size: 1.55rem; font-weight: 700; color: {NAVY}; line-height: 1.3; }}
.kpi .s {{ font-size: .78rem; color: #7A8798; }}
.chip {{ display: inline-block; padding: 3px 10px; margin: 2px 4px 2px 0; border-radius: 999px; background: #EEF3F9;
  color: {NAVY}; font-size: .8rem; border: 1px solid #D9E3EF; }}
.chip.ok {{ background: #E8F5EC; border-color: #BFE3CB; color: #1E6B3A; }}
.chip.warn {{ background: #FFF6E0; border-color: #F1DDA5; color: #8A6410; }}
.chip.bad {{ background: #FDECEC; border-color: #F3C2C2; color: #9B2C2C; }}
.tag {{ font-family: ui-monospace, Menlo, Consolas, monospace; font-size: .8rem; background: #F3F6FA;
  border: 1px solid #E1E8F1; border-radius: 6px; padding: 1px 7px; margin-right: 4px; display: inline-block; }}
.podium {{ border-radius: 14px; padding: 16px 12px; text-align: center; background: #fff; border: 1px solid #E3E9F1;
  border-top: 4px solid #A8B4C2; }}
.podium.p1 {{ border-top-color: {GOLD}; background: linear-gradient(#FFFBF0, #fff); }}
.podium.p3 {{ border-top-color: #C98F5B; }}
.podium .r {{ font-size: .75rem; color: #7A8798; text-transform: uppercase; letter-spacing: .08em; }}
.podium .u {{ font-size: 1.15rem; font-weight: 700; color: {NAVY}; word-break: break-all; }}
.podium .m {{ font-size: .95rem; color: #33475B; }}
.podium .p {{ font-size: .85rem; color: #8A6410; font-weight: 600; margin-top: 4px; }}
.rule {{ font-size: .88rem; color: #33475B; }}
.footer {{ color: #8A97A8; font-size: .78rem; text-align: center; margin-top: 28px; padding-top: 10px; border-top: 1px solid #E8EDF4; }}
section[data-testid="stSidebar"] [role="radiogroup"] > label {{ padding: 7px 10px; border-radius: 8px; margin-bottom: 2px; }}
section[data-testid="stSidebar"] [role="radiogroup"] > label:hover {{ background: #E4ECF6; }}
section[data-testid="stSidebar"] [role="radiogroup"] > label:has(input:checked) {{ background: {NAVY}; }}
section[data-testid="stSidebar"] [role="radiogroup"] > label:has(input:checked) * {{ color: #fff !important; }}
.side-brand {{ background: {NAVY}; color: #fff; border-radius: 12px; padding: 12px 14px; margin-bottom: 10px; border-bottom: 3px solid {GOLD}; }}
.side-brand .a {{ font-weight: 700; font-size: 1.02rem; }}
.side-brand .b {{ font-size: .74rem; color: #BFD0E4; }}
</style>
"""


# =============================================================================
# Helper tampilan
# =============================================================================
def esc(x) -> str:
    return html.escape(str(x))


def show_df(df: pd.DataFrame, **kw):
    """Kompatibel dengan Streamlit lama (use_container_width) maupun baru (width='stretch')."""
    kw.setdefault("hide_index", True)
    try:
        st.dataframe(df, width="stretch", **kw)
    except Exception:
        st.dataframe(df, use_container_width=True, **kw)


def kpi(label, value, sub=""):
    st.markdown(f'<div class="kpi"><div class="l">{esc(label)}</div><div class="v">{esc(value)}</div>'
                f'<div class="s">{esc(sub)}</div></div>', unsafe_allow_html=True)


def chips(items):
    st.markdown("".join(f'<span class="chip {k}">{esc(t)}</span>' for t, k in items), unsafe_allow_html=True)


def banner(title, subtitle=""):
    st.markdown(f'<div class="app-banner"><div class="t">{esc(title)}</div><div class="s">{esc(subtitle)}</div></div>',
                unsafe_allow_html=True)


def fmt_table(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        lc = str(c).lower()
        if c == "Rank":
            out[c] = out[c].map(lambda v: "-" if pd.isna(v) else str(int(v)))
        elif "(rp)" in lc:
            out[c] = out[c].map(lambda v: "" if pd.isna(v) else f"{float(v):,.0f}".replace(",", "."))
        elif "jam" in lc and c != "Status":
            out[c] = out[c].map(lambda v: "" if pd.isna(v) else f"{float(v):.2f}")
    return out


def ss():
    return st.session_state


def init_state():
    for k, v in {"data": {}, "meta": {}, "comp_raw": None, "tier_map": None, "tier_diag": None,
                 "results": None, "results_sig": None, "edits": {}, "xlsx": None, "gs_client": None}.items():
        if k not in st.session_state:
            st.session_state[k] = v


# =============================================================================
# Opsi (sidebar)
# =============================================================================
def sidebar_options() -> E.Options:
    D = CONFIG["DEFAULTS"]
    with st.sidebar.expander("Parameter aturan", expanded=False):
        fm = st.selectbox("Mode penentuan juara", list(FILL_MODES), index=list(FILL_MODES).index(D["fill_mode"]), key="p_fill",
                          help="\n\n".join(f"**{k}**: {v[1]}" for k, v in FILL_MODES.items()))
        hm = st.radio("Hashtag wajib", ["Semua hashtag wajib", "Minimal satu hashtag"],
                      index=0 if D["hashtag_mode"].startswith("Semua") else 1, key="p_hm")
        strip = st.checkbox("Abaikan akhiran bulan (SEPT / OKT / OCT)", D["strip_month_suffix"], key="p_strip",
                            help="Di data nyata creator memakai #...SEPT, #...OKT, dan #...OCT secara campur.")
        auto = st.checkbox("Koreksi hashtag MostLS/MostSV yang tertukar di sheet", D["autocorrect_sheet_hashtag"], key="p_auto",
                           help="Beberapa baris Most SV di sheet tertulis #MostLS... (salah salin).")
        mention = st.selectbox("Mention @dailycarebyunilever", ["Ikuti mechanism", "Selalu wajib", "Abaikan"],
                               index=["Ikuti mechanism", "Selalu wajib", "Abaikan"].index(D["mention_policy"]), key="p_mention")
        fbrand = st.checkbox("Produk harus sesuai brand challenge", D["filter_brand"], key="p_brand")
        ftier = st.checkbox("Creator harus sesuai tier mapping", D["require_tier"], key="p_tier")
        mdays = st.number_input("Min. hari posting berbeda (Most SV)", 0, 31, int(D["min_post_days_sv"]), key="p_days",
                                help="0 = tidak diterapkan. Mechanism: 'wajib post di hari yang berbeda (contoh 2 SV/hari)'.")
        cmode = st.radio("Syarat konten Highest GMV", ["10 SV ATAU 10 LS", "SV + LS gabungan ≥ 10"],
                         index=0 if D["min_content_mode"].startswith("10 SV") else 1, key="p_cmode")
        pip = st.checkbox("Highest GMV: video diposting / live dimulai dalam periode", D["require_post_in_period"], key="p_pip")
        uniq = st.checkbox("Satu creator hanya boleh juara sekali (lintas brand & kategori)", D["unique_winner"], key="p_uniq",
                           help="Creator yang unggul di beberapa challenge diberikan ke challenge dengan hadiah terbesar; "
                                "challenge lain turun ke peringkat berikutnya.")
        extra = st.slider("Kandidat review hingga peringkat N + ...", 0, 10, int(D["near_miss_extra"]), key="p_extra")
    return E.Options(
        year=CONFIG["YEAR"], hashtag_mode="all" if hm.startswith("Semua") else "any", strip_month_suffix=strip,
        autocorrect_sheet_hashtag=auto,
        mention_policy={"Ikuti mechanism": "mechanism", "Selalu wajib": "always", "Abaikan": "never"}[mention],
        default_mention=D["default_mention"], filter_brand=fbrand, require_tier=ftier, min_post_days_sv=int(mdays),
        min_content_mode="atau" if cmode.startswith("10 SV") else "gabungan", require_post_in_period=pip,
        near_miss_extra=int(extra), unique_winner=uniq, fill_mode=FILL_MODES[fm][0], aliases=CONFIG["BRAND_ALIASES"], tier_group_map=CONFIG["TIER_GROUP_MAP"],
        product_rules=CONFIG["BRAND_PRODUCT_RULES"])


# =============================================================================
# Challenge (parse + edit hashtag)
# =============================================================================
def parsed_challenges(opt: E.Options):
    comp = ss().get("comp_raw")
    if comp is None:
        return [], []
    try:
        return E.parse_challenges(comp, opt)
    except Exception as e:  # noqa: BLE001
        return [], [f"Gagal membaca tab Monthly Competition: {e}"]


def effective_challenges(opt: E.Options) -> list:
    out = []
    for ch in parsed_challenges(opt)[0]:
        ed = ss()["edits"].get(ch.key)
        if ed and ed["base"] == ch.hashtags:
            if not ed["active"]:
                continue
            ch.hashtags = list(ed["tags"])
        out.append(ch)
    return out


def signature(opt: E.Options) -> str:
    meta = tuple(sorted((k, v.get("rows", 0)) for k, v in ss()["meta"].items()))
    return repr((opt.hashtag_mode, opt.strip_month_suffix, opt.autocorrect_sheet_hashtag, opt.mention_policy,
                 opt.filter_brand, opt.require_tier, opt.min_post_days_sv, opt.min_content_mode,
                 opt.require_post_in_period, opt.near_miss_extra, opt.unique_winner, opt.fill_mode, meta, repr(ss()["edits"]),
                 0 if ss()["tier_map"] is None else len(ss()["tier_map"]),
                 0 if ss()["comp_raw"] is None else ss()["comp_raw"].shape))


def run_compute(opt: E.Options):
    chs = effective_challenges(opt)
    if not chs:
        st.sidebar.error("Belum ada challenge. Muat tab Monthly Competition di 'Setup Data'.")
        return
    if not ss()["data"]:
        st.sidebar.error("Belum ada data. Muat RAW LS/SV atau file TAP di 'Setup Data'.")
        return
    with st.spinner("Menghitung juara..."):
        t0 = time.time()
        ss()["results"] = E.run_challenges(chs, ss()["data"], ss()["tier_map"], opt)
        ss()["results_sig"] = signature(opt)
        ss()["xlsx"] = None
    st.sidebar.success(f"Selesai · {len(ss()['results'])} challenge · {time.time() - t0:.1f} dtk")


# =============================================================================
# Halaman 1 - Setup Data
# =============================================================================
def cov_text(df, col) -> str:
    c = E.coverage(df, col)
    return "-" if c is None else f"{c[0]:%d %b} - {c[1]:%d %b %Y}"


def register(name, source, df, col, extra=None):
    ss()["meta"][name] = {"sumber": source, "rows": int(len(df)), "cakupan": cov_text(df, col),
                          "dimuat": time.strftime("%H:%M:%S"), **(extra or {})}


def status_row():
    m = ss()["meta"]
    items = []
    n_ch = 0 if ss()["comp_raw"] is None else len(parsed_challenges(E.Options(year=CONFIG["YEAR"], aliases=CONFIG["BRAND_ALIASES"]))[0])
    items.append((f"Challenge: {n_ch}", "ok" if n_ch else "bad"))
    tm = ss()["tier_map"]
    items.append((f"Tier mapping: {0 if tm is None else len(tm):,}".replace(",", "."), "ok" if tm is not None and len(tm) else "warn"))
    for k in ["RAW LS", "RAW SV", "TAP LS", "TAP SV"]:
        items.append((f"{k}: {m[k]['rows']:,}".replace(",", ".") if k in m else f"{k}: belum", "ok" if k in m else "warn"))
    chips(items)


def gs_error(e) -> str:
    """Pesan error Google + petunjuk praktis (share sheet ke email service account)."""
    msg, low = str(e), str(e).lower()
    email = IO.credential_email(CONFIG["CREDENTIAL_FILES"])
    if email and any(k in low for k in ("403", "permission", "not found", "404", "spreadsheetnotfound")):
        msg += f"  ->  Share spreadsheet ke {email} (cukup Viewer), lalu coba lagi."
    return msg


def get_client():
    if ss().get("gs_client") is None:
        ss()["gs_client"] = IO.gs_client(CONFIG["CREDENTIAL_FILES"])
    return ss()["gs_client"]


def tab_challenge_tier(opt, mode):
    st.markdown("**Tab Monthly Competition** (daftar challenge) dan **tab brand** (Username + Tiering Acquire kolom M).")
    ph = st.empty()
    cb = lambda m: ph.info(m)  # noqa: E731
    if mode == "Google Sheets":
        url = st.text_input("Spreadsheet INT", CONFIG["INT_SPREADSHEET"], key="int_url")
        if st.button("Tarik dari Google Sheets", key="btn_int"):
            try:
                with st.spinner("Mengambil data INT..."):
                    cl, sid = get_client(), IO.spreadsheet_id(url)
                    comp = IO.fetch_competition(cl, sid, CONFIG["TAB_COMPETITION"], CONFIG["RANGE_COMPETITION"])
                    frames = IO.fetch_tier_frames(cl, sid, CONFIG["BRAND_TABS"], CONFIG["RANGE_TIER"], cb)
                ss()["comp_raw"] = comp
                ss()["tier_map"], ss()["tier_diag"] = E.tier_map_from_frames(frames, opt)
                ph.success("Challenge dan tier mapping berhasil dimuat.")
            except Exception as e:  # noqa: BLE001
                ph.error(f"Gagal mengambil dari Google Sheets: {gs_error(e)}")
    else:
        f = st.file_uploader("Workbook INT (.xlsx, hasil File > Download)", type=["xlsx"], key="up_int")
        with st.expander("Atau upload CSV terpisah"):
            f_comp = st.file_uploader("CSV tab Monthly Competition", type=["csv"], key="up_comp_csv")
            f_tiers = st.file_uploader("CSV tab brand (satu file per brand; nama file memuat nama brand)", type=["csv"],
                                       accept_multiple_files=True, key="up_tier_csv")
        if st.button("Muat dari file", key="btn_int_file"):
            try:
                with st.spinner("Membaca file..."):
                    if f is not None:
                        tabs = {"comp": CONFIG["TAB_COMPETITION"], **{b: t for b, t in CONFIG["BRAND_TABS"].items()}}
                        frames = IO.read_workbook_tabs(f, tabs, max_col=16)
                        comp = frames.pop("comp", None)
                        if comp is None:
                            raise ValueError(f"Tab '{CONFIG['TAB_COMPETITION']}' tidak ada di workbook.")
                        tier_frames = {CONFIG["BRAND_TABS"][b]: frames[b] for b in CONFIG["BRAND_TABS"] if b in frames}
                    elif f_comp is not None:
                        comp = pd.read_csv(f_comp, dtype=str, keep_default_na=False, header=None, encoding="utf-8-sig")
                        tier_frames = IO.tier_frames_from_csvs(f_tiers or [])
                    else:
                        raise ValueError("Pilih file workbook INT atau CSV Monthly Competition dulu.")
                ss()["comp_raw"] = comp
                ss()["tier_map"], ss()["tier_diag"] = E.tier_map_from_frames(tier_frames, opt)
                ph.success("Challenge dan tier mapping berhasil dimuat.")
            except Exception as e:  # noqa: BLE001
                ph.error(f"Gagal membaca file: {e}")
    diag = ss().get("tier_diag")
    if diag is not None and len(diag):
        st.markdown("**Ringkasan tier mapping per brand**")
        show_df(diag)


def tab_raw(opt, mode):
    st.markdown("Dipakai untuk **Most Live Streaming** (durasi dari Live Start - Live End) dan **Most Short Video** (Video ID unik).")
    ph = st.empty()
    cb = lambda m: ph.info(m)  # noqa: E731
    if mode == "Google Sheets":
        url = st.text_input("Spreadsheet RAW", CONFIG["RAW_SPREADSHEET"], key="raw_url")
        c1, c2 = st.columns(2)
        for col, kind, tab, rng, name in [(c1, "ls", CONFIG["TAB_RAW_LS"], CONFIG["RANGE_RAW_LS"], "RAW LS"),
                                          (c2, "sv", CONFIG["TAB_RAW_SV"], CONFIG["RANGE_RAW_SV"], "RAW SV")]:
            with col:
                if st.button(f"Tarik {name}", key=f"btn_{kind}_gs"):
                    try:
                        std, meta = IO.fetch_raw(get_client(), IO.spreadsheet_id(url), tab, rng, kind, opt, cb)
                        ss()["data"][f"raw_{kind}"] = std
                        register(name, "Google Sheets", std, "start" if kind == "ls" else "post_dt")
                        ph.success(f"{name}: {len(std):,} baris unik dimuat.".replace(",", "."))
                    except Exception as e:  # noqa: BLE001
                        ph.error(f"Gagal mengambil {name}: {gs_error(e)}")
    else:
        c1, c2 = st.columns(2)
        with c1:
            f_ls = st.file_uploader("RAW LS (.csv / .xlsx)", type=["csv", "xlsx"], key="up_raw_ls")
        with c2:
            f_sv = st.file_uploader("RAW SV (.csv / .xlsx)", type=["csv", "xlsx"], key="up_raw_sv")
        if st.button("Muat RAW dari file", key="btn_raw_file"):
            for f, kind, name in [(f_ls, "ls", "RAW LS"), (f_sv, "sv", "RAW SV")]:
                if f is None:
                    continue
                try:
                    with st.spinner(f"Membaca {name}..."):
                        std, meta = IO.read_raw(f, kind, opt, cb)
                    ss()["data"][f"raw_{kind}"] = std
                    register(name, f.name, std, "start" if kind == "ls" else "post_dt")
                    ph.success(f"{name}: {len(std):,} baris unik dimuat.".replace(",", "."))
                except Exception as e:  # noqa: BLE001
                    ph.error(f"Gagal membaca {name}: {e}")


def tab_tap(opt):
    st.markdown("Dipakai untuk **Creator Highest GMV**. Upload export TAP (full month 1-31 Okt). "
                "Boleh beberapa file; baris yang tumpang tindih diambil dari file terakhir.")
    ph = st.empty()
    cb = lambda m: ph.info(m)  # noqa: E731
    c1, c2 = st.columns(2)
    with c1:
        f_ls = st.file_uploader("TAP LS (.xlsx / .csv)", type=["xlsx", "csv"], accept_multiple_files=True, key="up_tap_ls")
    with c2:
        f_sv = st.file_uploader("TAP SV (.xlsx / .csv)", type=["xlsx", "csv"], accept_multiple_files=True, key="up_tap_sv")
    st.caption("Tips: CSV jauh lebih cepat dibaca daripada .xlsx untuk file ratusan ribu baris.")
    if st.button("Muat TAP", key="btn_tap"):
        for files, kind, name in [(f_ls, "ls", "TAP LS"), (f_sv, "sv", "TAP SV")]:
            if not files:
                continue
            try:
                with st.spinner(f"Membaca {name} (bisa beberapa menit untuk .xlsx besar)..."):
                    std, meta = IO.read_tap(files, kind, opt, cb)
                ss()["data"][f"tap_{kind}"] = std
                register(name, ", ".join(f.name for f in files), std, "date",
                         {"baris_mentah": meta["rows_raw"], "tumpang_tindih_dibuang": meta["overlap_removed"],
                          "gmv_hilang": meta["gmv_dropped"]})
                msg = f"{name}: {len(std):,} baris dimuat.".replace(",", ".")
                if abs(meta["gmv_dropped"]) > 0.5:
                    ph.warning(msg + f" Perhatian: GMV Rp {meta['gmv_dropped']:,.0f} tidak terbaca saat standarisasi.".replace(",", "."))
                else:
                    ph.success(msg + " Total GMV cocok dengan file asli.")
            except Exception as e:  # noqa: BLE001
                ph.error(f"Gagal membaca {name}: {e}")


def tab_review(opt):
    chs, problems = parsed_challenges(opt)
    for p in problems:
        st.warning(p)
    if not chs:
        st.info("Muat tab Monthly Competition dulu (tab ①).")
        return
    st.markdown("Periksa **hashtag yang dipakai** tiap challenge. Kolom *Hashtag* dan *Aktif* bisa diedit; "
                "baris bertanda di *Catatan* adalah kejanggalan di sheet sumber.")
    rows = []
    for ch in chs:
        ed = ss()["edits"].get(ch.key)
        ok = bool(ed and ed["base"] == ch.hashtags)
        tags = ed["tags"] if ok else ch.hashtags
        rows.append({"Aktif": ed["active"] if ok else True, "Brand": ch.brand, "Kategori": E.CATEGORY_SHORT[ch.category],
                     "Tier": E.GROUP_LABEL[ch.tier_group], "Periode": ch.period_label,
                     "Hashtag": " ".join("#" + t for t in tags), "Syarat": X._rule_text(ch, opt),
                     "Hadiah": f"{ch.n_winners} slot · {E.fmt_rp(sum(ch.prizes))}", "Catatan": " | ".join(ch.warnings)})
    base = pd.DataFrame(rows)
    edited = st.data_editor(
        base, hide_index=True, num_rows="fixed", key="chal_editor",
        disabled=["Brand", "Kategori", "Tier", "Periode", "Syarat", "Hadiah", "Catatan"],
        column_config={"Aktif": st.column_config.CheckboxColumn("Aktif", width="small"),
                       "Hashtag": st.column_config.TextColumn("Hashtag (dipakai)", width="large"),
                       "Catatan": st.column_config.TextColumn("Catatan", width="large")})
    new = {}
    for ch, (_, r) in zip(chs, edited.iterrows()):
        tags = list(dict.fromkeys(t.lower() for t in re.findall(r"#?(\w+)", str(r["Hashtag"]))))
        active = bool(r["Aktif"])
        if tags != ch.hashtags or not active:
            new[ch.key] = {"base": list(ch.hashtags), "tags": tags, "active": active}
    ss()["edits"] = new


def page_setup(opt):
    banner("Setup Data", "Muat challenge, tier mapping, dan data mentah, lalu klik ▶ Hitung Rekap di sidebar")
    status_row()
    mode = st.radio("Sumber data challenge & RAW", ["Upload file", "Google Sheets"], horizontal=True, key="src_mode")
    t1, t2, t3, t4 = st.tabs(["① Challenge & Tier", "② RAW Activity (LS / SV)", "③ TAP (Highest GMV)", "④ Review challenge"])
    with t1:
        tab_challenge_tier(opt, mode)
    with t2:
        tab_raw(opt, mode)
    with t3:
        tab_tap(opt)
    with t4:
        tab_review(opt)
    if ss()["meta"]:
        st.markdown("#### Data yang sudah dimuat")
        show_df(pd.DataFrame([{"Data": k, **v} for k, v in ss()["meta"].items()]))
        missing = [k for k in ("RAW LS", "RAW SV", "TAP LS", "TAP SV") if k not in ss()["meta"]]
        if missing:
            st.caption("Belum dimuat: " + ", ".join(missing) + ". Challenge yang butuh data itu akan ditandai 'belum dimuat'.")


# =============================================================================
# Halaman hasil
# =============================================================================
def need_results() -> bool:
    if ss()["results"] is None:
        st.info("Belum ada hasil. Muat data di **Setup Data**, lalu klik **▶ Hitung Rekap** di sidebar.")
        return False
    return True


def stale_warning(opt):
    if ss()["results_sig"] != signature(opt):
        st.warning("Parameter atau data berubah sejak perhitungan terakhir. Klik **▶ Hitung Rekap** untuk memperbarui.")


def page_summary(opt):
    banner("Ringkasan", "Semua challenge dalam satu tampilan")
    if not need_results():
        return
    stale_warning(opt)
    res = ss()["results"]
    ov = E.results_overview(res)
    slots = int(ov["Hadiah tersedia"].sum())
    filled = int(ov["Pemenang"].sum())
    paid = sum(r.summary.get("prize_paid", 0) for r in res)
    budget = sum(sum(r.challenge.prizes) for r in res)
    attn = sum(1 for r in res if r.error or r.notes)
    c = st.columns(5)
    with c[0]:
        kpi("Challenge dihitung", len(res), f"{attn} perlu perhatian")
    with c[1]:
        kpi("Slot hadiah terisi", f"{filled} / {slots}", f"{(filled / slots * 100 if slots else 0):.0f}% terisi")
    with c[2]:
        kpi("Hadiah dibayarkan", E.fmt_rp(paid), f"dari {E.fmt_rp(budget)}")
    with c[3]:
        kpi("Total peserta", f"{int(ov['Peserta'].sum()):,}".replace(",", "."), "creator ber-hashtag, antar challenge")
    with c[4]:
        kpi("Total GMV peserta", E.fmt_rp(ov["Total GMV (Rp)"].sum()), "dari konten yang lolos filter")
    st.write("")
    f1, f2 = st.columns(2)
    brands = f1.multiselect("Brand", sorted(ov["Brand"].unique()), default=[], key="sum_brand", placeholder="Semua brand")
    cats = f2.multiselect("Kategori", sorted(ov["Kategori"].unique()), default=[], key="sum_cat", placeholder="Semua kategori")
    v = ov
    if brands:
        v = v[v["Brand"].isin(brands)]
    if cats:
        v = v[v["Kategori"].isin(cats)]
    show = v.drop(columns=["Key"]).copy()
    show["Total GMV (Rp)"] = show["Total GMV (Rp)"].map(lambda x: f"{x:,.0f}".replace(",", "."))
    show_df(show)
    st.markdown("**Peserta per brand**")
    st.bar_chart(v.groupby("Brand")["Peserta"].sum())
    problems = [r for r in res if r.error or r.notes]
    if problems:
        with st.expander(f"Perlu perhatian ({len(problems)})"):
            for r in problems:
                st.markdown(f"**{r.challenge.title}** · {r.challenge.period_label}")
                for n in ([r.error] if r.error else []) + r.notes:
                    st.markdown(f"- {n}")


def podium(r: E.ChallengeResult):
    w = r.winners.head(3)
    cols = st.columns(3)
    cat = r.challenge.category
    for i, col in enumerate(cols):
        with col:
            if i >= len(w):
                st.markdown(f'<div class="podium"><div class="r">Juara {i + 1}</div><div class="u">-</div>'
                            f'<div class="m">belum ada</div></div>', unsafe_allow_html=True)
                continue
            row = w.iloc[i]
            if cat == E.CAT_LS:
                m = (f"{row['total_jam']:.1f} jam · {int(row['sesi_valid'])} sesi" if row["total_jam"] > 0
                     else f"{row.get('jam_semua', 0):.1f} jam (semua sesi)")
            elif cat == E.CAT_SV:
                m = f"{int(row['jumlah_sv'])} SV · {int(row['hari_posting'])} hari posting"
            else:
                m = E.fmt_rp(row["gmv_total"])
            prize = "" if pd.isna(row.get("hadiah")) else E.fmt_rp(row["hadiah"])
            flag = "" if bool(row.get("memenuhi", True)) else '<div class="m" style="color:#9B2C2C;font-size:.78rem">⚠ belum memenuhi syarat penuh</div>'
            st.markdown(f'<div class="podium p{i + 1}"><div class="r">Juara {i + 1}</div><div class="u">{esc(row["username"])}</div>'
                        f'<div class="m">{esc(m)}</div>{flag}<div class="p">{esc(prize)}</div></div>', unsafe_allow_html=True)


def render_challenge(r: E.ChallengeResult, opt: E.Options):
    ch = r.challenge
    chips([(f"Periode {ch.period_label}", ""), (f"Tier {E.GROUP_LABEL[ch.tier_group]}", ""),
           (f"Pengumuman {ch.announce}" if ch.announce else "Pengumuman -", ""),
           (f"{ch.n_winners} slot · {E.fmt_rp(sum(ch.prizes))}", "")])
    st.markdown('<div class="rule"><b>Syarat:</b> ' + esc(X._rule_text(ch, opt)) + "</div>", unsafe_allow_html=True)
    st.markdown("**Hashtag:** " + "".join(f'<span class="tag">#{esc(t)}</span>' for t in ch.hashtags) +
                ("<span class='tag'>@" + esc((ch.mentions or [opt.default_mention])[0]) + "</span>"
                 if E.mention_regexes(ch, opt) else ""), unsafe_allow_html=True)
    if r.error:
        st.error(r.error)
        return
    for n in r.notes:
        st.warning(n)
    if ch.warnings:
        with st.expander(f"Catatan data sheet ({len(ch.warnings)})"):
            for w in ch.warnings:
                st.markdown(f"- {w}")
    s = r.summary
    c = st.columns(5)
    with c[0]:
        kpi("Peserta", s["participants"], "creator ber-hashtag")
    with c[1]:
        kpi("Memenuhi syarat", s["qualified"], f"dari {s['participants']}")
    with c[2]:
        unq = s.get("unqualified_winners", 0)
        kpi("Pemenang", f"{s['winners']} / {ch.n_winners}", E.fmt_rp(s["prize_paid"]) + (f" · {unq} belum memenuhi syarat" if unq else ""))
    with c[3]:
        kpi("Total konten", f"{s['total_content']:,.0f}".replace(",", "."), {E.CAT_LS: "sesi LS", E.CAT_SV: "video unik", E.CAT_GMV: "SV + LS"}[ch.category])
    with c[4]:
        kpi("Total GMV", E.fmt_rp(s["total_gmv"]), "peserta")
    st.write("")
    podium(r)
    st.write("")
    st.markdown("#### Daftar pemenang")
    wt = fmt_table(E.display_table(r, "winners"))
    if wt.empty:
        st.info("Belum ada creator yang memenuhi syarat.")
    else:
        show_df(wt)
    t1, t2, t3, t4 = st.tabs(["Leaderboard lengkap", "Review hashtag / mention", "Audit filter", "Teks kolom result"])
    with t1:
        q = st.text_input("Cari username", "", key=f"q_{ch.key}")
        lb = E.display_table(r, "leaderboard")
        if q:
            lb = lb[lb["Username"].str.contains(q, case=False, na=False)]
        show_df(fmt_table(lb), height=420)
        st.caption(f"{len(lb)} creator. " + ("Rank '-' = belum memenuhi syarat." if opt.fill_mode == "strict" else
                   "Memenuhi syarat penuh diurutkan lebih dulu; sisanya urut performa. Rank '-' = sudah juara di challenge lain / tidak ikut peringkat."))
    with t2:
        if len(r.skipped):
            st.markdown("**Dilewati karena sudah juara di challenge lain** (hadiah di sini diteruskan ke peringkat berikutnya)")
            sk = r.skipped.rename(columns={"username": "Username", "peringkat_asli": "Peringkat di sini", "hadiah_terlewat": "Hadiah di sini (Rp)",
                                           "juara_di": "Juara di", "hadiah_diambil": "Hadiah di sana (Rp)"})
            show_df(fmt_table(sk))
        if r.review.empty:
            st.success("Tidak ada kandidat yang tertahan karena typo hashtag / mention.")
        else:
            st.markdown("Creator di bawah ini **tidak lolos aturan ketat**, tetapi akan masuk peringkat atas bila typo "
                        "hashtag / mention dimaklumi. Putuskan manual, jangan otomatis.")
            rv = r.review.drop(columns=["creator_key"], errors="ignore").copy()
            rv.columns = [c.replace("_", " ").title() for c in rv.columns]
            show_df(rv)
    with t3:
        show_df(r.funnel)
        st.bar_chart(r.funnel.set_index("Langkah")["Creator unik"])
    with t4:
        st.caption("Format mengikuti kolom 'result' di tab Monthly Competition.")
        st.code(s["result_text"], language=None)


def page_category(cat, opt):
    banner(E.CATEGORY_LABEL[cat], "Hasil per challenge")
    if not need_results():
        return
    stale_warning(opt)
    rs = [r for r in ss()["results"] if r.challenge.category == cat]
    if not rs:
        st.info("Tidak ada challenge kategori ini di hasil perhitungan.")
        return
    c1, c2 = st.columns(2)
    brands = c1.multiselect("Brand", sorted({r.challenge.brand for r in rs}), default=[], key=f"b_{cat}", placeholder="Semua brand")
    tiers = c2.multiselect("Tier", sorted({E.GROUP_LABEL[r.challenge.tier_group] for r in rs}), default=[], key=f"t_{cat}", placeholder="Semua tier")
    view = [r for r in rs if (not brands or r.challenge.brand in brands)
            and (not tiers or E.GROUP_LABEL[r.challenge.tier_group] in tiers)]
    if not view:
        st.info("Tidak ada challenge yang cocok dengan filter.")
        return
    labels = {r.challenge.key: f"{r.challenge.brand} · {E.GROUP_LABEL[r.challenge.tier_group]} · {r.challenge.period_label}" for r in view}
    key = st.selectbox("Challenge", list(labels), format_func=lambda k: labels[k], key=f"sel_{cat}")
    render_challenge(next(r for r in view if r.challenge.key == key), opt)


def page_export(opt):
    banner("Export & Audit", "Unduh rekap Excel dan lihat parameter yang dipakai")
    if not need_results():
        return
    stale_warning(opt)
    meta = {k: f"{v['rows']} baris · {v['cakupan']}" for k, v in ss()["meta"].items()}
    if st.button("Siapkan file Excel", key="btn_xlsx"):
        with st.spinner("Menyusun workbook..."):
            ss()["xlsx"] = X.build_workbook(ss()["results"], opt, meta)
    if ss()["xlsx"]:
        st.download_button("⬇ Unduh Rekap Monthly Competition (.xlsx)", ss()["xlsx"],
                           file_name=f"Rekap_Monthly_Competition_{time.strftime('%Y%m%d_%H%M')}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_xlsx")
        st.caption("Isi: Recap (format sheet October Competition, kolom payout kosong) · Ringkasan (siap ditempel ke Monthly Competition) · Pemenang · Leaderboard · Review Hashtag · Audit Filter · Juara Ganda · Catatan · Parameter.")
    st.markdown("#### Parameter yang dipakai")
    show_df(X.params_table(opt, meta).astype(str))
    st.markdown("#### Catatan sheet sumber")
    notes = X.build_tables(ss()["results"], opt)["Catatan"]
    if notes.empty:
        st.success("Tidak ada catatan.")
    else:
        show_df(notes)


# =============================================================================
# Main
# =============================================================================
def main():
    st.set_page_config(page_title=CONFIG["APP_TITLE"], page_icon="📊", layout="wide", initial_sidebar_state="expanded")
    st.markdown(CSS, unsafe_allow_html=True)
    init_state()
    with st.sidebar:
        if os.path.exists(CONFIG["LOGO_PATH"]):
            st.image(CONFIG["LOGO_PATH"])
        st.markdown(f'<div class="side-brand"><div class="a">{esc(CONFIG["APP_TITLE"])}</div>'
                    f'<div class="b">{esc(CONFIG["ORG_CREDIT"])}</div></div>', unsafe_allow_html=True)
        page = st.radio("Menu", PAGES, label_visibility="collapsed", key="page")
    opt = sidebar_options()
    if st.sidebar.button("▶ Hitung Rekap", type="primary", key="btn_run"):
        run_compute(opt)

    if page == PAGES[0]:
        page_setup(opt)
    elif page == PAGES[1]:
        page_summary(opt)
    elif page in PAGE_CAT:
        page_category(PAGE_CAT[page], opt)
    else:
        page_export(opt)
    st.markdown(f'<div class="footer">{esc(CONFIG["ORG_CREDIT"])} · {esc(CONFIG["APP_SUBTITLE"])}</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
