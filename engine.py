"""
engine.py - Logika rekap Monthly Competition (PC x WEI).

Murni pandas, TIDAK bergantung pada Streamlit, supaya bisa diuji dan dipakai
ulang dari script lain.

Alur besar
----------
1. parse_challenges()        : baca tab "Monthly Competition" -> daftar Challenge
2. tier_map_from_frames()    : baca kolom Username & Tiering Acquire tiap tab brand (INT)
3. std_* ()                  : rapikan RAW LS / RAW SV / TAP LS / TAP SV ke bentuk standar
4. run_challenges()          : hitung juara tiap challenge (Most LS, Most SV, Highest GMV)
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# Konstanta
# --------------------------------------------------------------------------- #
CAT_LS, CAT_SV, CAT_GMV = "MOST_LS", "MOST_SV", "HIGHEST_GMV"
CATEGORY_LABEL = {
    CAT_LS: "Most Session Live Streaming",
    CAT_SV: "Most Short Video",
    CAT_GMV: "Creator Highest GMV",
}
CATEGORY_SHORT = {CAT_LS: "Most LS", CAT_SV: "Most SV", CAT_GMV: "Highest GMV"}
GROUP_LABEL = {"INCUBATION": "Incubation", "RISING": "Rising", "TOP": "Top"}
GROUP_PRIORITY = {"TOP": 0, "RISING": 1, "INCUBATION": 2}  # kalau username dobel, ambil tier tertinggi

DEFAULT_BRAND_ALIASES = {
    "PEPSODENT": ["pepsodent"],
    "CLOSE UP": ["close up", "closeup"],
    "LIFEBUOY": ["lifebuoy"],
    "DOVE SCL": ["dove skin cleansing", "dove scl", "dove (scl)"],
    "LUX": ["lux"],
    "REXONA": ["rexona"],
    "DOVE DEO": ["dove deodorant", "dove deo", "dove (deo)"],
}

# Tier di kolom M halaman brand  ->  Tier/Group di Monthly Competition
DEFAULT_TIER_GROUP_MAP = {
    "content": "INCUBATION",
    "incubation": "INCUBATION",
    "rising": "RISING",
    "top": "TOP",
    "tier 1+": "TOP",
    "tier1+": "TOP",
}

# File TAP tidak punya kolom Brand -> turunkan dari nama produk (urutan penting)
DEFAULT_BRAND_PRODUCT_RULES = [
    ("DOVE DEO", r"dove.*(\bdeo|roll\s*-?on|anti-?\s*perspirant|ketiak|underarm)"),
    ("DOVE SCL", r"\bdove\b"),
    ("PEPSODENT", r"pepsodent"),
    ("CLOSE UP", r"close\s*-?\s*up"),
    ("LIFEBUOY", r"lifebuoy"),
    ("LUX", r"\blux\b"),
    ("REXONA", r"rexona"),
]

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "mei": 5, "may": 5, "jun": 6, "jul": 7,
    "agu": 8, "aug": 8, "agt": 8, "sep": 9, "okt": 10, "oct": 10, "nov": 11, "des": 12, "dec": 12,
}

# Kolom minimum yang dibutuhkan dari tiap sumber (dipakai saat membaca file besar)
RAW_LS_COLS = ["Source", "Live Date", "Creator name", "Brand", "Livestream name", "Livestream room ID",
               "Live Start", "Live End", "Duration", "Creator LIVE-attributed GMV"]
RAW_SV_COLS = ["Source", "Post date", "Video title", "Video ID", "Creator name", "Brand",
               "Creator video-attributed GMV"]
TAP_LS_COLS = ["Date", "Campaign ID", "Creator name", "Product ID", "Product name", "Livestream room ID", "Livestream name",
               "LIVE time info", "Duration", "Creator LIVE-attributed GMV"]
TAP_SV_COLS = ["Date", "Campaign ID", "Creator name", "Product ID", "Product name", "Video ID", "Video name", "Post time",
               "Affiliate video-attributed GMV"]


# --------------------------------------------------------------------------- #
# Opsi perhitungan
# --------------------------------------------------------------------------- #
@dataclass
class Options:
    year: int = 2026
    hashtag_mode: str = "all"            # "all" = semua hashtag wajib | "any" = minimal satu
    strip_month_suffix: bool = True      # abaikan akhiran bulan (SEPT/OKT/OCT) pada hashtag
    autocorrect_sheet_hashtag: bool = True  # koreksi MostLS<->MostSV yang tertukar di sheet
    mention_policy: str = "mechanism"    # "mechanism" | "always" | "never"
    default_mention: str = "dailycarebyunilever"
    filter_brand: bool = True
    require_tier: bool = True
    min_post_days_sv: int = 25           # 0 = tidak diterapkan
    min_content_mode: str = "atau"       # "atau" (10 SV ATAU 10 LS) | "gabungan" (SV+LS >= 10)
    require_post_in_period: bool = True  # Highest GMV: video/live harus diposting/mulai dalam periode
    near_miss_extra: int = 5             # tampilkan kandidat review sampai peringkat N + extra
    unique_winner: bool = True           # satu creator hanya boleh juara sekali (lintas brand & kategori)
    fill_mode: str = "any"               # "strict" | "minimum" (syarat minimum longgar) | "all" (+ kurang hashtag/mention) | "any" (+ tanpa hashtag)
    aliases: dict = field(default_factory=lambda: dict(DEFAULT_BRAND_ALIASES))
    tier_group_map: dict = field(default_factory=lambda: dict(DEFAULT_TIER_GROUP_MAP))
    product_rules: list = field(default_factory=lambda: list(DEFAULT_BRAND_PRODUCT_RULES))


# --------------------------------------------------------------------------- #
# Helper umum
# --------------------------------------------------------------------------- #
def _isnull(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def _alnum(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def fmt_rp(x) -> str:
    try:
        return "Rp " + f"{float(x):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "-"


def fmt_hours(x) -> str:
    try:
        h = float(x)
    except (TypeError, ValueError):
        return "-"
    return f"{int(h)} jam {int(round((h - int(h)) * 60)):02d} mnt"


def normalize_brand(label, aliases: Optional[dict] = None) -> str:
    """'Close Up (OC)', 'CLOSE UP', 'closeup' -> 'CLOSE UP' dst."""
    aliases = aliases or DEFAULT_BRAND_ALIASES
    if _isnull(label):
        return ""
    key = _alnum(label)
    if not key:
        return ""
    for canon, al in aliases.items():
        if key == _alnum(canon) or key in {_alnum(a) for a in al}:
            return canon
    pairs = sorted(
        ((_alnum(a), canon) for canon, al in aliases.items() for a in list(al) + [canon]),
        key=lambda x: -len(x[0]),
    )
    for a, canon in pairs:
        if a and a in key:
            return canon
    if "dove" in key:
        return "DOVE DEO" if "deo" in key else "DOVE SCL"
    return str(label).strip().upper()


def norm_username(u) -> str:
    if _isnull(u):
        return ""
    return re.sub(r"\s+", "", str(u).strip().lower().lstrip("@"))


def norm_username_series(s: pd.Series) -> pd.Series:
    return (s.fillna("").astype(str).str.strip().str.lower()
            .str.lstrip("@").str.replace(r"\s+", "", regex=True))


def parse_money(x) -> float:
    """'Rp17.773.322' / '84,182,289' / 'Rp. 1.500.000' / 12345 -> float."""
    if _isnull(x):
        return 0.0
    if isinstance(x, (int, float, np.number)):
        return float(x)
    s = re.sub(r"(?i)rp\.?", "", str(x)).replace(" ", "").strip()
    if s in {"", "-", "--"}:
        return 0.0
    if re.fullmatch(r"-?\d+", s):
        return float(s)
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
        return float(s.replace(".", ""))
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", s):
        return float(s.replace(",", ""))
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+,\d+", s):
        return float(s.replace(".", "").replace(",", "."))
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    digits = re.sub(r"[^\d]", "", s)
    return float(digits) if digits else 0.0


def money_series(s: pd.Series) -> pd.Series:
    num = pd.to_numeric(s, errors="coerce")
    txt = s.fillna("").astype(str).str.strip()
    mask = num.isna() & (txt != "")
    if mask.any():
        num.loc[mask] = s[mask].map(parse_money)
    return num.fillna(0.0).astype(float)


def parse_duration_h(s: pd.Series) -> pd.Series:
    """'5h34min' / '2h1min' / '38min' / '21s' -> jam (float). NaN bila tidak terbaca."""
    t = s.fillna("").astype(str).str.lower()
    h = pd.to_numeric(t.str.extract(r"(\d+)\s*h")[0], errors="coerce")
    m = pd.to_numeric(t.str.extract(r"(\d+)\s*min")[0], errors="coerce")
    sec = pd.to_numeric(t.str.extract(r"(\d+)\s*s(?![a-z])")[0], errors="coerce")
    out = h.fillna(0) + m.fillna(0) / 60.0 + sec.fillna(0) / 3600.0
    return out.where(h.notna() | m.notna() | sec.notna())


def _finish_sessions(g: pd.DataFrame) -> pd.DataFrame:
    """Durasi = End-Start; bila jam selesai kosong (live masih berjalan) pakai kolom Duration."""
    g = g.copy()
    g["end_missing"] = g["end"].isna()
    calc = (g["end"] - g["start"]).dt.total_seconds() / 3600.0
    g["duration_h"] = calc.where(~g["end_missing"], g["dur_h"]).clip(lower=0).fillna(0.0)
    g["end"] = g["end"].fillna(g["start"] + pd.to_timedelta(g["duration_h"], unit="h"))
    return g.drop(columns=["dur_h"])


def _col(df: pd.DataFrame, *names, required: bool = True):
    low = {re.sub(r"\s+", " ", str(c)).strip().lower(): c for c in df.columns}
    for n in names:
        k = re.sub(r"\s+", " ", n).strip().lower()
        if k in low:
            return low[k]
    if required:
        raise KeyError(f"Kolom {names[0]!r} tidak ditemukan. Kolom yang ada: {list(df.columns)[:30]}")
    return None


def _first_nonempty(s: pd.Series):
    for v in s:
        if isinstance(v, str) and v.strip():
            return v
    return ""


def frame_from_values(values: list, header: bool = True) -> pd.DataFrame:
    """List-of-lists (mis. hasil gspread, bisa ragged) -> DataFrame string."""
    if not values:
        return pd.DataFrame()
    width = max(len(r) for r in values)
    rows = [list(r) + [""] * (width - len(r)) for r in values]
    if not header:
        return pd.DataFrame(rows)
    cols, seen = [], Counter()
    for i, c in enumerate(rows[0]):
        c = str(c).strip() or f"_c{i}"
        seen[c] += 1
        cols.append(c if seen[c] == 1 else f"{c}.{seen[c] - 1}")
    return pd.DataFrame(rows[1:], columns=cols)


# --------------------------------------------------------------------------- #
# Hashtag & mention
# --------------------------------------------------------------------------- #
_MENTION_GLUE = re.compile(r"mention\s*(?=@)", re.I)       # "...SquadMention@dailycare..."
_HASHTAG = re.compile(r"#(\w+)")
_MONTH_SUFFIX = re.compile(r"(september|sept|sep|oktober|october|okt|oct|oc|agustus|august|agu|aug|november|nov)$")


def canon_tag(tag: str, strip_month: bool = True) -> str:
    t = tag.lower()
    if strip_month:
        s = _MONTH_SUFFIX.sub("", t)
        if len(s) >= 3:
            t = s
    return t


def hashtags_of(text, strip_month: bool = True) -> frozenset:
    if _isnull(text):
        return frozenset()
    t = _MENTION_GLUE.sub(" ", str(text))
    return frozenset(canon_tag(m, strip_month) for m in _HASHTAG.findall(t))


def mention_regex(handle: str) -> re.Pattern:
    """'@dailycarebyunilever' cocok dengan '@Daily Care by Unilever' maupun '@dailycarebyunilever'."""
    chars = [re.escape(c) for c in str(handle).lstrip("@") if c.isalnum()]
    return re.compile(r"@[\s._]*" + r"[\s._]*".join(chars), re.I)


# --------------------------------------------------------------------------- #
# Challenge
# --------------------------------------------------------------------------- #
@dataclass
class Challenge:
    key: str
    sheet_row: int
    brand: str
    brand_label: str
    category: str
    tier_group: str
    hashtags: list
    mentions: list
    require_mention: bool
    period_start: pd.Timestamp
    period_end: pd.Timestamp
    period_label: str
    min_total_hours: Optional[float] = None
    min_session_hours: Optional[float] = None
    min_sv: Optional[int] = None
    min_gmv: Optional[float] = None
    min_content: Optional[int] = None
    n_winners: int = 0
    prizes: list = field(default_factory=list)
    prize_total_declared: Optional[float] = None
    announce: str = ""
    mechanism: str = ""
    hashtags_sheet: list = field(default_factory=list)   # hashtag asli dari sheet (sebelum koreksi)
    warnings: list = field(default_factory=list)

    @property
    def title(self) -> str:
        return f"{self.brand} - {CATEGORY_SHORT[self.category]} - {GROUP_LABEL.get(self.tier_group, self.tier_group)}"

    def req_canon(self, strip_month: bool) -> list:
        out = []
        for h in self.hashtags:
            c = canon_tag(h.lower().lstrip("#"), strip_month)
            if c not in out:
                out.append(c)
        return out


def _detect_category(label: str) -> Optional[str]:
    t = str(label).lower()
    if "gmv" in t:
        return CAT_GMV
    if "short" in t or "video" in t:
        return CAT_SV
    if "live" in t or "session" in t:
        return CAT_LS
    return None


def _detect_group(label: str) -> Optional[str]:
    t = str(label).lower()
    if "incu" in t:
        return "INCUBATION"
    if "rising" in t:
        return "RISING"
    if "top" in t or "tier" in t:
        return "TOP"
    return None


def parse_period(text, year: int):
    t = str(text).strip()
    m = re.match(r"^\s*(\d{1,2})\s*[-–]\s*(\d{1,2})\s+([A-Za-z]+)", t)
    if m:
        d1, d2, mon = int(m.group(1)), int(m.group(2)), MONTHS.get(m.group(3)[:3].lower())
        m1 = m2 = mon
    else:
        m = re.match(r"^\s*(\d{1,2})\s+([A-Za-z]+)\s*[-–]\s*(\d{1,2})\s+([A-Za-z]+)", t)
        if not m:
            return None
        d1, m1 = int(m.group(1)), MONTHS.get(m.group(2)[:3].lower())
        d2, m2 = int(m.group(3)), MONTHS.get(m.group(4)[:3].lower())
    if not m1 or not m2:
        return None
    start = pd.Timestamp(year=year, month=m1, day=d1)
    end = pd.Timestamp(year=year, month=m2, day=d2) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
    return start, end, t


def parse_prizes(text) -> list:
    prizes = {}
    for line in str(text).splitlines():
        m = re.search(r"(?:rank\s*)?(\d+)\s*[-:]\s*(?:rp\.?\s*)?([\d.,]+)", line, re.I)
        if m:
            prizes[int(m.group(1))] = parse_money(m.group(2).rstrip(".,"))
    return [prizes[k] for k in sorted(prizes)]


def _num(pattern: str, text: str, cast=float, money: bool = False):
    m = re.search(pattern, text, re.I)
    if not m:
        return None
    raw = m.group(1).rstrip(".,")
    try:
        v = parse_money(raw) if money else float(raw.replace(",", "."))
    except ValueError:
        return None
    return cast(v)


def find_header_row(df: pd.DataFrame, must_have=("brand", "mechanism"), limit: int = 30) -> Optional[int]:
    for i in range(min(len(df), limit)):
        vals = {str(v).strip().lower() for v in df.iloc[i].tolist()}
        if all(m in vals for m in must_have):
            return i
    return None


def parse_challenges(df: pd.DataFrame, opt: Options):
    """df = isi tab Monthly Competition tanpa header (header=None) atau dengan header baris pertama."""
    h = find_header_row(df)
    if h is None:
        raise ValueError("Header tab Monthly Competition (kolom Brand & Mechanism) tidak ditemukan.")
    hdr, seen = [], Counter()
    for i, v in enumerate(df.iloc[h].tolist()):
        v = str(v).strip().lower() or f"_c{i}"
        seen[v] += 1
        hdr.append(v if seen[v] == 1 else f"{v}.{seen[v] - 1}")
    body = df.iloc[h + 1:].copy()
    body.columns = hdr
    body = body.reset_index(drop=True)
    g = lambda r, k: ("" if _isnull(r.get(k)) else str(r.get(k)))  # noqa: E731

    challenges, problems, keys = [], [], Counter()
    for j, r in body.iterrows():
        brand_label, cat_label, mech = g(r, "brand").strip(), g(r, "category activity"), g(r, "mechanism")
        if not brand_label or not cat_label.strip() or not mech.strip():
            continue
        sheet_row = h + 2 + j
        cat, grp = _detect_category(cat_label), _detect_group(g(r, "tier/group"))
        per = parse_period(g(r, "periode"), opt.year)
        if not cat or not grp or not per:
            problems.append(f"Baris {sheet_row} ({brand_label}) dilewati: kategori/tier/periode tidak terbaca.")
            continue
        brand = normalize_brand(brand_label, opt.aliases)
        tags_sheet = [t.lower() for t in _HASHTAG.findall(g(r, "hashtag"))]
        mentions = [m.lower() for m in re.findall(r"@([\w.]+)", g(r, "hashtag"))]
        prizes = parse_prizes(g(r, "hadiah"))
        n_win = len(prizes) or int((re.search(r"(\d+)\s*winners?", g(r, "hadiah"), re.I) or [0, 0])[1] or 0)
        total_decl = parse_money(g(r, "value")) or None
        ch = Challenge(
            key="", sheet_row=sheet_row, brand=brand, brand_label=brand_label, category=cat, tier_group=grp,
            hashtags=list(dict.fromkeys(tags_sheet)), hashtags_sheet=list(dict.fromkeys(tags_sheet)),
            mentions=list(dict.fromkeys(mentions)), require_mention="mention" in mech.lower(),
            period_start=per[0], period_end=per[1], period_label=per[2],
            n_winners=n_win, prizes=prizes, prize_total_declared=total_decl,
            announce=g(r, "tanggal pengumuman"), mechanism=mech,
        )
        ch.min_total_hours = _num(r"live\s*streaming\s+minimal\s+(\d+(?:[.,]\d+)?)\s*jam", mech)
        ch.min_session_hours = (_num(r"minimal\s+(\d+(?:[.,]\d+)?)\s*jam\s*/\s*sesi", mech)
                                or _num(r"per\s*sesi\s+(\d+(?:[.,]\d+)?)\s*jam", mech))
        ch.min_sv = _num(r"minimal\s+(\d+)\s*SV", mech, int)
        ch.min_gmv = _num(r"minimum\s+gmv\s*rp\.?\s*([\d.,]+)", mech, money=True)
        ch.min_content = _num(r"minimum\s+upload\s+(\d+)", mech, int)

        # ---- cek kewajaran data sheet -------------------------------------------------
        if cat == CAT_SV and any("mostls" in t for t in ch.hashtags):
            ch.warnings.append("Hashtag Most SV memuat 'MostLS' (kemungkinan salah salin dari challenge LS).")
            if opt.autocorrect_sheet_hashtag:
                ch.hashtags = [t.replace("mostls", "mostsv") for t in ch.hashtags]
                ch.warnings.append("-> Dikoreksi otomatis menjadi 'MostSV' (bisa dimatikan di Parameter).")
        if cat == CAT_LS and any("mostsv" in t for t in ch.hashtags):
            ch.warnings.append("Hashtag Most LS memuat 'MostSV' (kemungkinan salah salin dari challenge SV).")
            if opt.autocorrect_sheet_hashtag:
                ch.hashtags = [t.replace("mostsv", "mostls") for t in ch.hashtags]
                ch.warnings.append("-> Dikoreksi otomatis menjadi 'MostLS' (bisa dimatikan di Parameter).")
        if ch.hashtags and _alnum(brand) not in " ".join(_alnum(t) for t in ch.hashtags):
            ch.warnings.append(f"Tidak ada hashtag yang memuat nama brand {brand}.")
        if not ch.hashtags:
            ch.warnings.append("Tidak ada hashtag terbaca - filter hashtag tidak diterapkan.")
        m_prod = re.search(r"produk\s+([A-Za-z ]+)", mech)
        if m_prod:
            mp = normalize_brand(m_prod.group(1).strip(), opt.aliases)
            if mp in opt.aliases and mp != brand:
                ch.warnings.append(f"Mechanism menyebut produk '{m_prod.group(1).strip()}' padahal brand {brand} "
                                   "(kemungkinan salah salin; brand dipakai dari kolom Brand).")
        if prizes and total_decl and abs(sum(prizes) - total_decl) > 1:
            ch.warnings.append(f"Total hadiah {fmt_rp(sum(prizes))} != kolom Value {fmt_rp(total_decl)}.")
        if cat == CAT_LS and not ch.min_total_hours:
            ch.warnings.append("Syarat minimal total jam LS tidak terbaca dari mechanism.")
        if cat == CAT_SV and not ch.min_sv:
            ch.warnings.append("Syarat minimal jumlah SV tidak terbaca dari mechanism.")
        if cat == CAT_GMV and not ch.min_gmv:
            ch.warnings.append("Syarat minimal GMV tidak terbaca dari mechanism.")

        base = f"{brand}|{cat}|{grp}|{per[2]}"
        keys[base] += 1
        ch.key = base if keys[base] == 1 else f"{base}#{keys[base]}"
        challenges.append(ch)
    return challenges, problems


# --------------------------------------------------------------------------- #
# Tier mapping (halaman brand di INT)
# --------------------------------------------------------------------------- #
def _find_tier_header(df: pd.DataFrame):
    for i in range(min(len(df), 40)):
        row = [str(v).strip().lower() for v in df.iloc[i].tolist()]
        if "username" in row:
            cu = row.index("username")
            ct = next((k for k, v in enumerate(row) if "tiering acquire" in v), None)
            if ct is None and len(row) > 12:
                ct = 12  # kolom M
            return i, cu, ct
    return None, None, None


def tier_map_from_frames(frames: dict, opt: Options):
    """frames: {nama tab: DataFrame header=None}. Return (tier_map, diagnosa)."""
    out, diag = [], []
    for tab, df in frames.items():
        brand = normalize_brand(tab, opt.aliases)
        h, cu, ct = _find_tier_header(df)
        if h is None or ct is None:
            diag.append({"Tab": tab, "Brand": brand, "Baris": 0, "Catatan": "Header Username / Tiering Acquire tidak ditemukan"})
            continue
        sub = df.iloc[h + 1:, [cu, ct]].copy()
        sub.columns = ["u", "t"]
        sub["u"] = sub["u"].fillna("").astype(str).str.strip()
        sub = sub[(sub["u"] != "") & ~sub["u"].str.lower().str.startswith("http")]
        sub["creator_key"] = norm_username_series(sub["u"])
        sub["tier_raw"] = sub["t"].fillna("").astype(str).str.strip()
        sub["group"] = sub["tier_raw"].str.lower().map(opt.tier_group_map)
        sub["brand"] = brand
        unknown = sub[sub["group"].isna() & (sub["tier_raw"] != "")]["tier_raw"].value_counts().to_dict()
        note = f"tier tidak dikenali: {unknown}" if unknown else ""
        diag.append({"Tab": tab, "Brand": brand, "Baris": len(sub),
                     **{GROUP_LABEL[g]: int((sub["group"] == g).sum()) for g in GROUP_LABEL},
                     "Tanpa tier": int((sub["tier_raw"] == "").sum()), "Catatan": note})
        out.append(sub[["brand", "creator_key", "tier_raw", "group"]])
    tm = pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["brand", "creator_key", "tier_raw", "group"])
    return tm, pd.DataFrame(diag)


def build_tier_lookup(tier_map: Optional[pd.DataFrame]) -> dict:
    if tier_map is None or tier_map.empty:
        return {}
    t = tier_map.dropna(subset=["group"]).copy()
    t["_p"] = t["group"].map(GROUP_PRIORITY)
    t = t.sort_values("_p", kind="mergesort")
    key = t["brand"] + "|" + t["creator_key"]
    s = pd.Series(t["group"].values, index=key.values)
    return s[~s.index.duplicated(keep="first")].to_dict()


# --------------------------------------------------------------------------- #
# Standarisasi data
# --------------------------------------------------------------------------- #
def infer_brand(product: pd.Series, rules: list) -> pd.Series:
    names = pd.unique(product.fillna("").astype(str))
    cache = {}
    for n in names:
        low = n.lower()
        cache[n] = next((b for b, rx in rules if re.search(rx, low)), "UNKNOWN")
    return product.fillna("").astype(str).map(cache)


def _gmv_max_over_sources(d: pd.DataFrame, keys: list, src_col: Optional[str]) -> pd.Series:
    """GMV per entitas = sum dalam satu source, lalu ambil max antar source (hindari dobel TAP vs SC)."""
    if src_col is None:
        return d.groupby(keys, sort=False)["gmv"].sum()
    per_src = d.groupby(keys + [src_col], sort=False)["gmv"].sum()
    return per_src.groupby(level=list(range(len(keys))), sort=False).max()


def std_raw_ls(df: pd.DataFrame, opt: Options) -> pd.DataFrame:
    """RAW LS -> satu baris per (creator, brand, room ID)."""
    c = dict(
        creator=_col(df, "Creator name"), brand=_col(df, "Brand"), name=_col(df, "Livestream name"),
        room=_col(df, "Livestream room ID"), st=_col(df, "Live Start"), en=_col(df, "Live End"),
        gmv=_col(df, "Creator LIVE-attributed GMV", required=False), src=_col(df, "Source", required=False),
        dur=_col(df, "Duration", required=False),
    )
    d = pd.DataFrame({
        "creator": df[c["creator"]].fillna("").astype(str).str.strip(),
        "brand": df[c["brand"]].map(lambda x: normalize_brand(x, opt.aliases)),
        "name": df[c["name"]].fillna("").astype(str),
        "room_id": df[c["room"]].fillna("").astype(str).str.strip(),
        "start": pd.to_datetime(df[c["st"]], errors="coerce"),
        "end": pd.to_datetime(df[c["en"]], errors="coerce"),
        "dur_h": parse_duration_h(df[c["dur"]]) if c["dur"] else np.nan,
        "gmv": money_series(df[c["gmv"]]) if c["gmv"] else 0.0,
        "src": df[c["src"]].fillna("").astype(str) if c["src"] else "",
    })
    d["creator_key"] = norm_username_series(d["creator"])
    d = d[(d["room_id"] != "") & (d["creator_key"] != "") & d["start"].notna()]
    d = d.assign(_ne=d["name"].str.strip().eq("")).sort_values("_ne", kind="mergesort")
    keys = ["creator_key", "brand", "room_id"]
    g = d.groupby(keys, sort=False).agg(creator=("creator", "first"), name=("name", "first"),
                                        start=("start", "min"), end=("end", "max"), dur_h=("dur_h", "max"))
    g["gmv"] = _gmv_max_over_sources(d, keys, "src" if c["src"] else None)
    return _finish_sessions(g.reset_index())


def std_raw_sv(df: pd.DataFrame, opt: Options) -> pd.DataFrame:
    """RAW SV -> satu baris per (creator, brand, Video ID)."""
    c = dict(
        creator=_col(df, "Creator name"), brand=_col(df, "Brand"), title=_col(df, "Video title"),
        vid=_col(df, "Video ID"), post=_col(df, "Post date"),
        gmv=_col(df, "Creator video-attributed GMV", required=False), src=_col(df, "Source", required=False),
    )
    d = pd.DataFrame({
        "creator": df[c["creator"]].fillna("").astype(str).str.strip(),
        "brand": df[c["brand"]].map(lambda x: normalize_brand(x, opt.aliases)),
        "title": df[c["title"]].fillna("").astype(str),
        "video_id": df[c["vid"]].fillna("").astype(str).str.strip(),
        "post_dt": pd.to_datetime(df[c["post"]], errors="coerce"),
        "gmv": money_series(df[c["gmv"]]) if c["gmv"] else 0.0,
        "src": df[c["src"]].fillna("").astype(str) if c["src"] else "",
    })
    d["creator_key"] = norm_username_series(d["creator"])
    d = d[(d["video_id"] != "") & (d["creator_key"] != "") & d["post_dt"].notna()]
    d = d.assign(_ne=d["title"].str.strip().eq("")).sort_values("_ne", kind="mergesort")
    keys = ["creator_key", "brand", "video_id"]
    g = d.groupby(keys, sort=False).agg(creator=("creator", "first"), title=("title", "first"),
                                        post_dt=("post_dt", "min"))
    g["gmv"] = _gmv_max_over_sources(d, keys, "src" if c["src"] else None)
    return g.reset_index()


_LIVE_TIME = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\s*-\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})?)?")


def std_tap_ls(df: pd.DataFrame, opt: Options) -> pd.DataFrame:
    """TAP LS -> baris per (creator, brand, room ID, tanggal)."""
    df = df[df[_col(df, "Date")].astype(str).str.lower() != "summary"]
    ti = df[_col(df, "LIVE time info")].fillna("").astype(str).str.extract(_LIVE_TIME)
    d = pd.DataFrame({
        "creator": df[_col(df, "Creator name")].fillna("").astype(str).str.strip(),
        "brand": infer_brand(df[_col(df, "Product name")], opt.product_rules),
        "name": df[_col(df, "Livestream name")].fillna("").astype(str),
        "room_id": df[_col(df, "Livestream room ID")].fillna("").astype(str).str.strip(),
        "start": pd.to_datetime(ti[0], errors="coerce"),
        "end": pd.to_datetime(ti[1], errors="coerce"),
        "dur_h": parse_duration_h(df[_col(df, "Duration", required=False)]) if _col(df, "Duration", required=False) else np.nan,
        "date": pd.to_datetime(df[_col(df, "Date")], errors="coerce"),
        "gmv": money_series(df[_col(df, "Creator LIVE-attributed GMV")]),
    })
    d["creator_key"] = norm_username_series(d["creator"])
    d = d[(d["room_id"] != "") & (d["creator_key"] != "") & d["start"].notna() & d["date"].notna()]
    d = d.assign(_ne=d["name"].str.strip().eq("")).sort_values("_ne", kind="mergesort")
    g = d.groupby(["creator_key", "brand", "room_id", "date"], sort=False).agg(
        creator=("creator", "first"), name=("name", "first"), start=("start", "min"),
        end=("end", "max"), dur_h=("dur_h", "max"), gmv=("gmv", "sum")).reset_index()
    return _finish_sessions(g)


def std_tap_sv(df: pd.DataFrame, opt: Options) -> pd.DataFrame:
    """TAP SV -> baris per (creator, brand, video ID, tanggal)."""
    df = df[df[_col(df, "Date")].astype(str).str.lower() != "summary"]
    d = pd.DataFrame({
        "creator": df[_col(df, "Creator name")].fillna("").astype(str).str.strip(),
        "brand": infer_brand(df[_col(df, "Product name")], opt.product_rules),
        "title": df[_col(df, "Video name")].fillna("").astype(str),
        "video_id": df[_col(df, "Video ID")].fillna("").astype(str).str.strip(),
        "post_dt": pd.to_datetime(df[_col(df, "Post time")], errors="coerce"),
        "date": pd.to_datetime(df[_col(df, "Date")], errors="coerce"),
        "gmv": money_series(df[_col(df, "Affiliate video-attributed GMV")]),
    })
    d["creator_key"] = norm_username_series(d["creator"])
    d = d[(d["video_id"] != "") & (d["creator_key"] != "") & d["post_dt"].notna() & d["date"].notna()]
    d = d.assign(_ne=d["title"].str.strip().eq("")).sort_values("_ne", kind="mergesort")
    return d.groupby(["creator_key", "brand", "video_id", "date"], sort=False).agg(
        creator=("creator", "first"), title=("title", "first"), post_dt=("post_dt", "min"),
        gmv=("gmv", "sum")).reset_index()


def sessions_from_tap_ls(ls_rows: pd.DataFrame) -> pd.DataFrame:
    """Cadangan bila RAW LS tidak ada: satu baris per (creator, brand, room)."""
    keys = ["creator_key", "brand", "room_id"]
    if "end_missing" not in ls_rows:
        ls_rows = ls_rows.assign(end_missing=False)
    g = ls_rows.groupby(keys, sort=False).agg(creator=("creator", "first"), name=("name", "first"),
                                              start=("start", "min"), end=("end", "max"), gmv=("gmv", "sum"),
                                              end_missing=("end_missing", "max"), duration_h=("duration_h", "max"))
    return g.reset_index()


def videos_from_tap_sv(sv_rows: pd.DataFrame) -> pd.DataFrame:
    keys = ["creator_key", "brand", "video_id"]
    return sv_rows.groupby(keys, sort=False).agg(creator=("creator", "first"), title=("title", "first"),
                                                 post_dt=("post_dt", "min"), gmv=("gmv", "sum")).reset_index()


def coverage(df: Optional[pd.DataFrame], col: str):
    if df is None or df.empty or col not in df:
        return None
    s = df[col].dropna()
    return (s.min().normalize(), s.max().normalize()) if len(s) else None


# --------------------------------------------------------------------------- #
# Analisis teks (hashtag + mention)
# --------------------------------------------------------------------------- #
def mention_regexes(ch: Challenge, opt: Options) -> list:
    need = ch.require_mention if opt.mention_policy == "mechanism" else (opt.mention_policy == "always")
    if not need:
        return []
    return [mention_regex(h) for h in (ch.mentions or [opt.default_mention])]


def annotate_text(d: pd.DataFrame, col: str, ch: Challenge, opt: Options) -> pd.DataFrame:
    req = ch.req_canon(opt.strip_month_suffix)
    rxs = mention_regexes(ch, opt)
    tbl = {}
    for t in pd.unique(d[col].fillna("").astype(str)):
        tags = hashtags_of(t, opt.strip_month_suffix)
        missing = tuple(x for x in req if x not in tags)
        tbl[t] = (len(req) - len(missing), missing, all(rx.search(t) for rx in rxs) if rxs else True)
    t = d[col].fillna("").astype(str)
    present = t.map(lambda x: tbl[x][0]).astype(int)
    d = d.assign(_present=present,
                 _missing=t.map(lambda x: tbl[x][1]),
                 _mention_ok=t.map(lambda x: tbl[x][2]).astype(bool))
    n = len(req)
    if n == 0:
        tag_ok = pd.Series(True, index=d.index)
        relaxed = tag_ok
    else:
        tag_ok = (d["_present"] == n) if opt.hashtag_mode == "all" else (d["_present"] >= 1)
        relaxed = d["_present"] >= max(1, math.ceil(n / 2))
    return d.assign(_strict=tag_ok & d["_mention_ok"], _relaxed=relaxed)


def _apply_tier(d: pd.DataFrame, ch: Challenge, lookup: dict, opt: Options):
    if not opt.require_tier:
        return d
    grp = (d["brand"] + "|" + d["creator_key"]).map(lookup)
    return d[grp == ch.tier_group]


def _fn(f: list, label: str, d: pd.DataFrame):
    f.append({"Langkah": label, "Baris/konten": int(len(d)),
              "Creator unik": int(d["creator_key"].nunique()) if len(d) else 0})


# --------------------------------------------------------------------------- #
# Hasil
# --------------------------------------------------------------------------- #
@dataclass
class ChallengeResult:
    challenge: Challenge
    leaderboard: pd.DataFrame = field(default_factory=pd.DataFrame)
    winners: pd.DataFrame = field(default_factory=pd.DataFrame)
    review: pd.DataFrame = field(default_factory=pd.DataFrame)
    funnel: pd.DataFrame = field(default_factory=pd.DataFrame)
    summary: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    error: Optional[str] = None
    skipped: pd.DataFrame = field(default_factory=pd.DataFrame)   # creator yang dilewati karena sudah juara di challenge lain


def _rank(lb: pd.DataFrame, sort_cols: list, opt: Optional["Options"] = None) -> pd.DataFrame:
    """
    Urutan: level kepatuhan dulu (2 = memenuhi semua syarat, 1 = hashtag ok tapi syarat minimum belum terpenuhi,
    0 = kurang hashtag/mention), baru performa. Mode 'strict' hanya memberi peringkat pada level 2;
    mode lain memberi peringkat pada semua creator supaya slot hadiah selalu terisi.
    """
    lb = lb.copy()
    if "level" not in lb.columns:
        lb["level"] = np.where(lb["memenuhi"], 2, 1)
    lb = lb.sort_values(["level"] + sort_cols, ascending=[False] * (1 + len(sort_cols)), kind="mergesort")
    lb = lb.reset_index(drop=True)
    n_rank = len(lb) if (opt is not None and opt.fill_mode != "strict") else int((lb["level"] == 2).sum())
    rank = np.full(len(lb), np.nan)
    rank[:n_rank] = np.arange(1, n_rank + 1)
    lb["rank"] = rank
    return lb


def _append_relaxed(lb: pd.DataFrame, rl: Optional[pd.DataFrame], sort_cols: list, issues: pd.Series, opt: "Options",
                    rl_none: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """
    Mode 'all'/'any': creator yang hashtag/mention-nya kurang ikut peringkat (level 0), di bawah yang lengkap.
    Mode 'any': creator yang sama sekali tidak mencantumkan hashtag challenge (rl_none = semua konten brand/periode/tier)
    ikut di level -1, supaya slot hadiah terisi walau tak ada yang mencantumkan hashtag.
    """
    if opt.fill_mode not in ("all", "any"):
        return lb
    use_rl = rl is not None and not rl.empty
    use_none = opt.fill_mode == "any" and rl_none is not None and not rl_none.empty
    if not use_rl and not use_none:
        return lb
    base = lb.copy()
    base["level"] = np.where(base["memenuhi"].astype(bool), 2, 1) if len(base) else pd.Series(dtype=int)
    parts = [base.astype(object) if base.empty else base]
    seen = set(base["creator_key"]) if len(base) else set()
    if use_rl:
        extra = rl[~rl["creator_key"].isin(seen)].copy()
        if len(extra):
            extra["memenuhi"] = False
            extra["level"] = 0
            extra["status"] = extra["creator_key"].map(
                lambda ck: ("Kurang hashtag/mention: " + issues.get(ck, "")).rstrip(": ") if str(issues.get(ck, "")) else "Tidak lolos filter hashtag/mention")
            parts.append(extra)
            seen |= set(extra["creator_key"])
    if use_none:
        none = rl_none[~rl_none["creator_key"].isin(seen)].copy()
        if len(none):
            none["memenuhi"] = False
            none["level"] = -1
            none["status"] = "Tidak mencantumkan hashtag challenge"
            parts.append(none)
    if len(parts) == 1:
        return _rank(base, sort_cols, opt)
    comb = pd.concat(parts, ignore_index=True)
    for c in sort_cols:                              # kolom performa yang tidak ada di lb kosong -> 0
        if c not in comb.columns:
            comb[c] = 0.0
        comb[c] = pd.to_numeric(comb[c], errors="coerce").fillna(0.0)
    return _rank(comb, sort_cols, opt)


def _winner_notes(ch: Challenge, lb: pd.DataFrame, opt: "Options") -> list:
    win = lb[lb["rank"].notna() & (lb["rank"] <= ch.n_winners)] if len(lb) else lb
    q = int(lb["memenuhi"].sum()) if len(lb) else 0
    out = []
    if opt.fill_mode == "strict":
        if q < ch.n_winners:
            out.append(f"Hanya {q} creator memenuhi syarat untuk {ch.n_winners} hadiah.")
        return out
    unq = int((~win["memenuhi"].astype(bool)).sum()) if len(win) else 0
    if unq:
        out.append(f"{unq} dari {len(win)} pemenang belum memenuhi syarat penuh (slot diisi dari performa terbaik); lihat kolom Status.")
    if "level" in win.columns and len(win):
        nh = int((win["level"] == -1).sum())
        if nh:
            out.append(f"{nh} pemenang sama sekali tidak mencantumkan hashtag challenge (diisi dari performa).")
    if len(win) < ch.n_winners:
        out.append(f"Hanya {len(win)} dari {ch.n_winners} slot hadiah terisi karena tidak ada creator lain yang ikut.")
    return out


def _prize(ch: Challenge, rank) -> float:
    if pd.isna(rank) or rank > ch.n_winners:
        return np.nan
    i = int(rank) - 1
    return ch.prizes[i] if i < len(ch.prizes) else np.nan


def _review_issues(rows: pd.DataFrame, req_display: dict, mention_needed: bool) -> pd.Series:
    """Ringkas kenapa konten gagal strict: hashtag apa yang kurang / tanpa mention."""
    def one(g: pd.DataFrame) -> str:
        miss, no_m = Counter(), 0
        for mm, ok in zip(g["_missing"], g["_mention_ok"]):
            for t in mm:
                miss[t] += 1
            if mention_needed and not ok:
                no_m += 1
        parts = [f"kurang #{req_display.get(t, t)} ({n}x)" for t, n in miss.most_common(3)]
        if no_m:
            parts.append(f"tanpa mention ({no_m}x)")
        return "; ".join(parts)
    return rows.groupby("creator_key").apply(one)


def _tie_note(lb: pd.DataFrame, ch: Challenge, col: str, notes: list):
    q = int(lb["rank"].notna().sum())
    n = ch.n_winners
    if n and q > n and lb.iloc[n - 1][col] == lb.iloc[n][col]:
        notes.append(f"Nilai {col} seri di batas peringkat {n}/{n + 1}; cek tie-break manual.")


def _finalize(ch, cat, lb, funnel, review, notes, opt, total_content, total_gmv, result_extra=""):
    lb = lb.copy()
    lb["hadiah"] = lb["rank"].map(lambda r: _prize(ch, r))
    winners = lb[lb["rank"].notna() & (lb["rank"] <= ch.n_winners)].copy()
    summary = {
        "participants": int(len(lb)),
        "qualified": int(lb["memenuhi"].sum()),
        "winners": int(len(winners)),
        "total_content": float(total_content),
        "total_gmv": float(total_gmv),
        "prize_paid": float(winners["hadiah"].fillna(0).sum()),
        "result_text": result_extra,
    }
    summary["unqualified_winners"] = int((~winners["memenuhi"].astype(bool)).sum()) if len(winners) else 0
    notes.extend(_winner_notes(ch, lb, opt))
    if len(review) and len(winners):
        review = review[~review["creator_key"].isin(winners["creator_key"])].reset_index(drop=True)
    return ChallengeResult(challenge=ch, leaderboard=lb, winners=winners, review=review,
                           funnel=pd.DataFrame(funnel), summary=summary, notes=notes)


def _near_miss(relaxed_lb, strict_lb, ch, opt, cols, issues):
    """Kandidat yang bisa masuk top-N seandainya typo hashtag/mention dimaklumi."""
    if relaxed_lb.empty:
        return pd.DataFrame()
    win_keys = set(strict_lb.loc[strict_lb["rank"].notna() & (strict_lb["rank"] <= ch.n_winners), "creator_key"])
    r = relaxed_lb[relaxed_lb["memenuhi"] & ~relaxed_lb["creator_key"].isin(win_keys)]
    r = r[r["rank"] <= ch.n_winners + opt.near_miss_extra]
    if r.empty:
        return pd.DataFrame()
    s = strict_lb.set_index("creator_key")
    out = r[["creator_key", "username"] + cols].copy()
    out.insert(2, "peringkat_relaxed", r["rank"].astype(int).values)
    out["peringkat_strict"] = out["creator_key"].map(s["rank"])
    out["status_strict"] = out["creator_key"].map(s["status"]).fillna("Tidak lolos filter hashtag/mention")
    out["catatan"] = out["creator_key"].map(issues).fillna("")
    return out.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Evaluator: Most LS
# --------------------------------------------------------------------------- #
def eval_most_ls(ch: Challenge, sessions: Optional[pd.DataFrame], lookup: dict, opt: Options) -> ChallengeResult:
    if sessions is None:
        return ChallengeResult(ch, error="Data LS (RAW LS / TAP LS) belum dimuat.")
    f, notes = [], []
    _fn(f, "Semua sesi LS di data", sessions)
    d = sessions
    if opt.filter_brand:
        d = d[d["brand"] == ch.brand]
        _fn(f, f"Brand = {ch.brand}", d)
    d = d[(d["start"] >= ch.period_start) & (d["start"] <= ch.period_end)]
    _fn(f, f"Live dimulai dalam periode {ch.period_label}", d)
    if opt.require_tier:
        d = _apply_tier(d, ch, lookup, opt)
        _fn(f, f"Creator bertier {GROUP_LABEL[ch.tier_group]} di brand ini", d)
    d = annotate_text(d, "name", ch, opt)
    strict = d[d["_strict"]]
    _fn(f, "Hashtag + mention sesuai (nama live)", strict)
    min_sess = ch.min_session_hours or 0.0
    min_tot = ch.min_total_hours or 0.0

    def build(rows):
        valid = rows[rows["duration_h"] >= min_sess]
        a = rows.groupby("creator_key", sort=False).agg(username=("creator", "first"), sesi_hashtag=("room_id", "nunique"),
                                                        jam_semua=("duration_h", "sum"))
        v = valid.groupby("creator_key", sort=False).agg(total_jam=("duration_h", "sum"), sesi_valid=("room_id", "nunique"),
                                                         terpanjang=("duration_h", "max"), gmv=("gmv", "sum"))
        lb = a.join(v, how="left")
        for c in ["total_jam", "sesi_valid", "terpanjang", "gmv"]:
            lb[c] = lb[c].fillna(0)
        lb["memenuhi"] = (lb["total_jam"] >= min_tot) & (lb["sesi_valid"] > 0)
        lb["status"] = np.where(
            lb["memenuhi"], "Memenuhi syarat",
            np.where(lb["sesi_valid"] == 0, f"Belum ada sesi >= {min_sess:g} jam",
                     "Kurang " + (min_tot - lb["total_jam"]).round(1).astype(str) + " jam (" +
                     lb["total_jam"].round(1).astype(str) + f"/{min_tot:g})"))
        return _rank(lb.reset_index(), ["total_jam", "sesi_valid", "jam_semua", "gmv"], opt)

    valid = strict[strict["duration_h"] >= min_sess]
    _fn(f, f"Durasi sesi >= {min_sess:g} jam", valid)
    if "end_missing" in valid and valid["end_missing"].any():
        notes.append(f"{int(valid['end_missing'].sum())} sesi live belum punya jam selesai saat data diambil; "
                     "durasinya diperkirakan dari kolom Duration.")
    lb = build(strict) if len(strict) else pd.DataFrame(columns=[
        "creator_key", "username", "sesi_hashtag", "jam_semua", "total_jam", "sesi_valid", "terpanjang", "gmv", "memenuhi", "status", "rank"])
    review = pd.DataFrame()
    rel = d[d["_relaxed"]]
    rl, issues = None, pd.Series(dtype=str)
    if len(rel) and len(rel) > len(strict):
        rl = build(rel)
        extra = rel[~rel["_strict"]]
        issues = _review_issues(extra, {c: h for c, h in zip(ch.req_canon(opt.strip_month_suffix), ch.hashtags)},
                                bool(mention_regexes(ch, opt))) if len(extra) else pd.Series(dtype=str)
        lb_for_nm = lb if len(lb) else pd.DataFrame({"creator_key": [], "rank": [], "status": []})
        review = _near_miss(rl, lb_for_nm, ch, opt, ["total_jam", "sesi_valid"], issues)
    rl_none = build(d) if (opt.fill_mode == "any" and len(d)) else None
    lb = _append_relaxed(lb, rl, ["total_jam", "sesi_valid", "jam_semua", "gmv"], issues, opt, rl_none)
    _tie_note(lb, ch, "total_jam", notes) if len(lb) else None

    total_ls = int(lb["sesi_hashtag"].sum()) if len(lb) else 0
    res_text = f"{len(lb)} creator\n{total_ls} LS"
    return _finalize(ch, CAT_LS, lb, f, review, notes, opt, total_ls, lb["gmv"].sum() if len(lb) else 0, res_text)


# --------------------------------------------------------------------------- #
# Evaluator: Most SV
# --------------------------------------------------------------------------- #
def eval_most_sv(ch: Challenge, videos: Optional[pd.DataFrame], lookup: dict, opt: Options) -> ChallengeResult:
    if videos is None:
        return ChallengeResult(ch, error="Data SV (RAW SV / TAP SV) belum dimuat.")
    f, notes = [], []
    _fn(f, "Semua video unik di data", videos)
    d = videos
    if opt.filter_brand:
        d = d[d["brand"] == ch.brand]
        _fn(f, f"Brand = {ch.brand}", d)
    d = d[(d["post_dt"] >= ch.period_start) & (d["post_dt"] <= ch.period_end)]
    _fn(f, f"Diposting dalam periode {ch.period_label}", d)
    if opt.require_tier:
        d = _apply_tier(d, ch, lookup, opt)
        _fn(f, f"Creator bertier {GROUP_LABEL[ch.tier_group]} di brand ini", d)
    d = annotate_text(d, "title", ch, opt)
    strict = d[d["_strict"]]
    _fn(f, "Hashtag + mention sesuai (caption)", strict)
    min_sv = ch.min_sv or 0
    min_days = opt.min_post_days_sv or 0

    def build(rows):
        rows = rows.assign(_day=rows["post_dt"].dt.normalize())
        lb = rows.groupby("creator_key", sort=False).agg(
            username=("creator", "first"), jumlah_sv=("video_id", "nunique"), hari_posting=("_day", "nunique"),
            caption_unik=("title", "nunique"), gmv=("gmv", "sum"))
        lb["memenuhi"] = (lb["jumlah_sv"] >= max(min_sv, 1)) & (lb["hari_posting"] >= min_days)

        def status(r):
            if r["memenuhi"]:
                return "Memenuhi syarat"
            p = []
            if r["jumlah_sv"] < min_sv:
                p.append(f"SV {int(r['jumlah_sv'])}/{min_sv}")
            if r["hari_posting"] < min_days:
                p.append(f"hari posting {int(r['hari_posting'])}/{min_days}")
            return "Belum memenuhi: " + ", ".join(p)
        lb["status"] = lb.apply(status, axis=1) if len(lb) else ""
        return _rank(lb.reset_index(), ["jumlah_sv", "hari_posting", "gmv"], opt)

    lb = build(strict) if len(strict) else pd.DataFrame(columns=[
        "creator_key", "username", "jumlah_sv", "hari_posting", "caption_unik", "gmv", "memenuhi", "status", "rank"])
    review = pd.DataFrame()
    rel = d[d["_relaxed"]]
    rl, issues = None, pd.Series(dtype=str)
    if len(rel) and len(rel) > len(strict):
        rl = build(rel)
        extra = rel[~rel["_strict"]]
        issues = _review_issues(extra, {c: h for c, h in zip(ch.req_canon(opt.strip_month_suffix), ch.hashtags)},
                                bool(mention_regexes(ch, opt))) if len(extra) else pd.Series(dtype=str)
        lb_for_nm = lb if len(lb) else pd.DataFrame({"creator_key": [], "rank": [], "status": []})
        review = _near_miss(rl, lb_for_nm, ch, opt, ["jumlah_sv", "hari_posting"], issues)
    rl_none = build(d) if (opt.fill_mode == "any" and len(d)) else None
    lb = _append_relaxed(lb, rl, ["jumlah_sv", "hari_posting", "gmv"], issues, opt, rl_none)
    _tie_note(lb, ch, "jumlah_sv", notes) if len(lb) else None

    total_sv = int(lb["jumlah_sv"].sum()) if len(lb) else 0
    res_text = f"{len(lb)} creator\n{total_sv} SV"
    return _finalize(ch, CAT_SV, lb, f, review, notes, opt, total_sv, lb["gmv"].sum() if len(lb) else 0, res_text)


# --------------------------------------------------------------------------- #
# Evaluator: Creator Highest GMV
# --------------------------------------------------------------------------- #
def eval_highest_gmv(ch: Challenge, sv_rows: Optional[pd.DataFrame], ls_rows: Optional[pd.DataFrame],
                     lookup: dict, opt: Options) -> ChallengeResult:
    if sv_rows is None and ls_rows is None:
        return ChallengeResult(ch, error="Data TAP SV / TAP LS belum dimuat.")
    f, notes = [], []
    if sv_rows is None or ls_rows is None:
        notes.append("Hanya salah satu file TAP (SV atau LS) yang dimuat; GMV sisi lain dianggap 0.")
    ps, pe = ch.period_start, ch.period_end

    # ---- sisi SV
    s = sv_rows if sv_rows is not None else pd.DataFrame(columns=["creator_key", "creator", "brand", "video_id", "title", "post_dt", "date", "gmv"])
    _fn(f, "Baris GMV SV di data (per video-hari)", s)
    if opt.filter_brand:
        s = s[s["brand"] == ch.brand]
        _fn(f, f"SV: produk brand {ch.brand}", s)
    s = s[(s["date"] >= ps) & (s["date"] <= pe)]
    if opt.require_post_in_period:
        s = s[(s["post_dt"] >= ps) & (s["post_dt"] <= pe)]
    _fn(f, f"SV: diposting & ber-GMV dalam periode {ch.period_label}", s)
    if opt.require_tier:
        s = _apply_tier(s, ch, lookup, opt)
        _fn(f, f"SV: creator bertier {GROUP_LABEL[ch.tier_group]}", s)
    s = annotate_text(s, "title", ch, opt)
    _fn(f, "SV: hashtag (+mention) sesuai", s[s["_strict"]])

    # ---- sisi LS
    l = ls_rows if ls_rows is not None else pd.DataFrame(columns=["creator_key", "creator", "brand", "room_id", "name", "start", "end", "duration_h", "date", "gmv"])
    _fn(f, "Baris GMV LS di data (per room-hari)", l)
    if opt.filter_brand:
        l = l[l["brand"] == ch.brand]
        _fn(f, f"LS: produk brand {ch.brand}", l)
    l = l[(l["date"] >= ps) & (l["date"] <= pe)]
    if opt.require_post_in_period:
        l = l[(l["start"] >= ps) & (l["start"] <= pe)]
    _fn(f, f"LS: live dimulai & ber-GMV dalam periode {ch.period_label}", l)
    if opt.require_tier:
        l = _apply_tier(l, ch, lookup, opt)
        _fn(f, f"LS: creator bertier {GROUP_LABEL[ch.tier_group]}", l)
    l = annotate_text(l, "name", ch, opt)
    min_sess = ch.min_session_hours or 0.0
    l_ok = l[l["_strict"] & (l["duration_h"] >= min_sess)]
    _fn(f, f"LS: hashtag sesuai & sesi >= {min_sess:g} jam", l_ok)
    # mode longgar: sesi pendek tetap dihitung GMV-nya (sebagai performa), tapi tidak untuk syarat jumlah LS
    l_use = l_ok if opt.fill_mode == "strict" else l[l["_strict"]]

    min_gmv, min_c = ch.min_gmv or 0.0, ch.min_content or 0

    def build(sr, lr):
        a = sr.groupby("creator_key", sort=False).agg(username=("creator", "first"), gmv_sv=("gmv", "sum"),
                                                      jumlah_sv=("video_id", "nunique"), caption_unik=("title", "nunique"))
        b = lr.groupby("creator_key", sort=False).agg(username_ls=("creator", "first"), gmv_ls=("gmv", "sum"),
                                                      jumlah_ls=("room_id", "nunique"))
        lv = lr[lr["duration_h"] >= min_sess].groupby("creator_key", sort=False).agg(ls_valid=("room_id", "nunique"))
        b = b.join(lv, how="left")
        b["ls_valid"] = b["ls_valid"].fillna(0)
        lb = a.join(b, how="outer")
        lb["username"] = lb["username"].fillna(lb["username_ls"])
        lb = lb.drop(columns=["username_ls"])
        for c in ["gmv_sv", "jumlah_sv", "caption_unik", "gmv_ls", "jumlah_ls", "ls_valid"]:
            lb[c] = lb[c].fillna(0)
        lb["gmv_total"] = lb["gmv_sv"] + lb["gmv_ls"]
        if opt.min_content_mode == "gabungan":
            content_ok = (lb["jumlah_sv"] + lb["ls_valid"]) >= min_c
        else:
            content_ok = (lb["jumlah_sv"] >= min_c) | (lb["ls_valid"] >= min_c)
        lb["memenuhi"] = (lb["gmv_total"] >= max(min_gmv, 0.01)) & content_ok

        def status(r):
            if r["memenuhi"]:
                return "Memenuhi syarat"
            p = []
            if r["gmv_total"] < min_gmv:
                p.append(f"GMV {fmt_rp(r['gmv_total'])} < {fmt_rp(min_gmv)}")
            if not ((r["jumlah_sv"] >= min_c) or (r["ls_valid"] >= min_c) if opt.min_content_mode != "gabungan"
                    else (r["jumlah_sv"] + r["ls_valid"]) >= min_c):
                p.append(f"konten SV {int(r['jumlah_sv'])} / LS {int(r['jumlah_ls'])} (min {min_c})")
            return "Belum memenuhi: " + "; ".join(p)
        lb["status"] = lb.apply(status, axis=1) if len(lb) else ""
        return _rank(lb.reset_index(), ["gmv_total"], opt)

    sv_ok = s[s["_strict"]]
    cols = ["creator_key", "username", "gmv_total", "gmv_sv", "gmv_ls", "jumlah_sv", "jumlah_ls", "ls_valid", "caption_unik",
            "memenuhi", "status", "rank"]
    lb = build(sv_ok, l_use) if (len(sv_ok) or len(l_use)) else pd.DataFrame(columns=cols)

    review = pd.DataFrame()
    s_rel = s[s["_relaxed"]]
    l_rel = l[l["_relaxed"]] if opt.fill_mode != "strict" else l[l["_relaxed"] & (l["duration_h"] >= min_sess)]
    rl, issues = None, pd.Series(dtype=str)
    if (len(s_rel) > len(sv_ok)) or (len(l_rel) > len(l_use)):
        rl = build(s_rel, l_rel)
        extra = pd.concat([s_rel[~s_rel["_strict"]][["creator_key", "_missing", "_mention_ok"]],
                           l_rel[~l_rel["_strict"]][["creator_key", "_missing", "_mention_ok"]]])
        issues = _review_issues(extra, {c: h for c, h in zip(ch.req_canon(opt.strip_month_suffix), ch.hashtags)},
                                bool(mention_regexes(ch, opt))) if len(extra) else pd.Series(dtype=str)
        lb_for_nm = lb if len(lb) else pd.DataFrame({"creator_key": [], "rank": [], "status": []})
        review = _near_miss(rl, lb_for_nm, ch, opt, ["gmv_total", "jumlah_sv", "jumlah_ls"], issues)
    rl_none = build(s, l) if (opt.fill_mode == "any" and (len(s) or len(l))) else None
    lb = _append_relaxed(lb, rl, ["gmv_total"], issues, opt, rl_none)
    _tie_note(lb, ch, "gmv_total", notes) if len(lb) else None

    total_gmv = float(lb["gmv_total"].sum()) if len(lb) else 0.0
    total_content = int(lb["jumlah_sv"].sum() + lb["jumlah_ls"].sum()) if len(lb) else 0
    return _finalize(ch, CAT_GMV, lb, f, review, notes, opt, total_content, total_gmv, _gmv_result_text(ch, lb, total_gmv))


def _gmv_result_text(ch: Challenge, lb: pd.DataFrame, total_gmv: float) -> str:
    """Teks kolom 'result' Highest GMV (format sheet): GMV tiap pemenang, total, partisipasi."""
    win = lb[lb["rank"].notna() & (lb["rank"] <= ch.n_winners)] if len(lb) else lb
    lines = [f"{chr(97 + i)} {fmt_rp(v)}" for i, v in enumerate(win["gmv_total"])]
    if len(win):
        lines.append(f"total gmv a-{chr(96 + len(win))} {fmt_rp(win['gmv_total'].sum())}")
    lines += ["", f"partisipasi {len(lb)} creator", f"menghasilkan {fmt_rp(total_gmv)} gmv"]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Satu creator = satu juara (lintas brand & kategori)
# --------------------------------------------------------------------------- #
def challenge_label(ch: Challenge) -> str:
    return f"{ch.brand} · {CATEGORY_SHORT[ch.category]} · {GROUP_LABEL[ch.tier_group]} ({ch.period_label})"


def apply_unique_winner(results: list, opt: Optional[Options] = None) -> list:
    """
    Creator yang jadi juara di satu challenge tidak boleh juara lagi di challenge lain.
    Slot hadiah diproses dari yang NILAINYA TERBESAR: tiap slot diisi creator berperingkat tertinggi
    yang belum menerima hadiah di challenge mana pun. Challenge lain turun ke peringkat berikutnya.
    Creator yang memenuhi syarat penuh (level 2) dibagikan dulu; slot yang masih kosong diisi creator
    yang belum memenuhi syarat minimum (level 1), lalu yang kurang hashtag/mention (level 0), lalu yang tanpa hashtag (level -1).
    Hasil (leaderboard, winners, summary, result_text) dihitung ulang di tempat.
    """
    opt = opt or Options()
    live = [r for r in results if not r.error and len(r.leaderboard)]
    levels = {}                                # id(r) -> {level: [creator_key terurut peringkat]}
    cand_all, orig_rank = {}, {}
    for r in live:
        lb = r.leaderboard
        if "level" not in lb.columns:
            lb = lb.assign(level=np.where(lb["memenuhi"].astype(bool), 2, 1))
            r.leaderboard = lb
        q = lb[lb["rank"].notna()].sort_values("rank")
        cand_all[id(r)] = list(q["creator_key"])
        levels[id(r)] = {lv: list(q.loc[q["level"] == lv, "creator_key"]) for lv in (2, 1, 0, -1)}
        for ck, rk in zip(q["creator_key"], q["rank"]):
            orig_rank[(id(r), ck)] = int(rk)

    slots = []                                 # (-nilai efektif, urutan challenge, nomor slot)
    for ri, r in enumerate(live):
        p = list(r.challenge.prizes)
        for k in range(r.challenge.n_winners):
            eff = max([x for x in p[k:] if not pd.isna(x)] or [0])     # tidak naik ke bawah, jaga urutan peringkat
            slots.append((-float(eff), ri, k))
    slots.sort()

    awarded, picks = {}, {id(r): [] for r in live}
    for lv in (2, 1, 0, -1):
        for _, ri, k in slots:
            r = live[ri]
            if len(picks[id(r)]) > k:
                continue
            for ck in levels[id(r)][lv]:
                if ck not in awarded:
                    awarded[ck] = ri
                    picks[id(r)].append(ck)
                    break

    for ri, r in enumerate(live):
        ch, lb = r.challenge, r.leaderboard.copy()
        mine = picks[id(r)]
        lost = [ck for ck in cand_all[id(r)] if awarded.get(ck, ri) != ri]
        rest = [ck for ck in cand_all[id(r)] if ck not in mine and ck not in lost]
        order = {ck: i + 1 for i, ck in enumerate(mine + rest)}
        lb["rank"] = lb["creator_key"].map(order).astype(float)
        lb["memenuhi"] = lb["memenuhi"].astype(bool) & ~lb["creator_key"].isin(lost)
        sk_rows = []
        for ck in lost:
            other = live[awarded[ck]]
            slot_no = picks[id(other)].index(ck)
            pr = other.challenge.prizes[slot_no] if slot_no < len(other.challenge.prizes) else np.nan
            name = lb.loc[lb["creator_key"] == ck, "username"].iloc[0]
            lb.loc[lb["creator_key"] == ck, "status"] = f"Sudah juara di {challenge_label(other.challenge)}"
            if orig_rank[(id(r), ck)] <= ch.n_winners:           # hanya yang sebenarnya akan menang di sini
                sk_rows.append({"username": name, "peringkat_asli": orig_rank[(id(r), ck)],
                                "hadiah_terlewat": _prize(ch, orig_rank[(id(r), ck)]),
                                "juara_di": challenge_label(other.challenge), "hadiah_diambil": pr})
        lb = lb.sort_values(["rank"], na_position="last", kind="mergesort").reset_index(drop=True)
        lb["hadiah"] = lb["rank"].map(lambda x: _prize(ch, x))
        r.leaderboard = lb
        r.winners = lb[lb["rank"].notna() & (lb["rank"] <= ch.n_winners)].copy()
        r.skipped = pd.DataFrame(sk_rows, columns=["username", "peringkat_asli", "hadiah_terlewat", "juara_di", "hadiah_diambil"])
        r.summary["qualified"] = int(lb["memenuhi"].sum())
        r.summary["winners"] = int(len(r.winners))
        r.summary["prize_paid"] = float(r.winners["hadiah"].fillna(0).sum())
        r.summary["unqualified_winners"] = int((~r.winners["memenuhi"].astype(bool)).sum()) if len(r.winners) else 0
        if ch.category == CAT_GMV:
            r.summary["result_text"] = _gmv_result_text(ch, lb, r.summary["total_gmv"])
        r.notes = [n for n in r.notes if not (n.startswith("Hanya ") or "pemenang belum memenuhi syarat penuh" in n
                                              or "creator unggul di sini" in n)]
        r.notes.extend(_winner_notes(ch, lb, opt))
        if len(r.skipped):
            r.notes.append(f"{len(r.skipped)} creator unggul di sini tetapi sudah juara di challenge lain; slot diteruskan ke peringkat berikutnya.")
    return results


# --------------------------------------------------------------------------- #
# Tampilan (kolom siap-baca)
# --------------------------------------------------------------------------- #
DISPLAY_COLS = {
    CAT_LS: [("rank", "Rank"), ("username", "Username"), ("total_jam", "Total Jam LS (sesi valid)"), ("jam_semua", "Total Jam Semua Sesi"), ("sesi_valid", "Sesi Valid"),
             ("terpanjang", "Sesi Terpanjang (jam)"), ("sesi_hashtag", "Sesi ber-hashtag"), ("gmv", "GMV (Rp)"),
             ("status", "Status"), ("hadiah", "Hadiah (Rp)")],
    CAT_SV: [("rank", "Rank"), ("username", "Username"), ("jumlah_sv", "Jumlah SV (unik)"), ("hari_posting", "Hari Posting"),
             ("caption_unik", "Caption Unik"), ("gmv", "GMV (Rp)"), ("status", "Status"), ("hadiah", "Hadiah (Rp)")],
    CAT_GMV: [("rank", "Rank"), ("username", "Username"), ("gmv_total", "GMV Total (Rp)"), ("gmv_sv", "GMV SV (Rp)"),
              ("gmv_ls", "GMV LS (Rp)"), ("jumlah_sv", "Jumlah SV"), ("jumlah_ls", "Jumlah LS"),
              ("caption_unik", "Caption Unik"), ("status", "Status"), ("hadiah", "Hadiah (Rp)")],
}


def display_table(res: ChallengeResult, which: str = "leaderboard") -> pd.DataFrame:
    df = getattr(res, which)
    if df is None or df.empty:
        return pd.DataFrame(columns=[n for _, n in DISPLAY_COLS[res.challenge.category]])
    spec = [(k, n) for k, n in DISPLAY_COLS[res.challenge.category] if k in df.columns]
    out = df[[k for k, _ in spec]].copy()
    out.columns = [n for _, n in spec]
    for c in out.columns:
        if "jam" in c.lower() and "Status" not in c:
            out[c] = out[c].astype(float).round(2)
    return out


# --------------------------------------------------------------------------- #
# Orkestrasi
# --------------------------------------------------------------------------- #
def run_challenges(challenges: list, data: dict, tier_map: Optional[pd.DataFrame], opt: Options,
                   categories: Optional[set] = None) -> list:
    """
    data: {"raw_ls": sessions, "raw_sv": videos, "tap_ls": ls_rows, "tap_sv": sv_rows}  (None bila tidak ada)
    Most LS / Most SV memakai RAW (cadangan: turunan TAP). Highest GMV memakai TAP.
    """
    lookup = build_tier_lookup(tier_map)
    ls = data.get("raw_ls")
    sv = data.get("raw_sv")
    if ls is None and data.get("tap_ls") is not None:
        ls = sessions_from_tap_ls(data["tap_ls"])
    if sv is None and data.get("tap_sv") is not None:
        sv = videos_from_tap_sv(data["tap_sv"])
    cov = {
        "ls": coverage(ls, "start"), "sv": coverage(sv, "post_dt"),
        "tap_ls": coverage(data.get("tap_ls"), "date"), "tap_sv": coverage(data.get("tap_sv"), "date"),
    }
    results = []
    for ch in challenges:
        if categories and ch.category not in categories:
            continue
        if ch.category == CAT_LS:
            r, cv = eval_most_ls(ch, ls, lookup, opt), [cov["ls"]]
        elif ch.category == CAT_SV:
            r, cv = eval_most_sv(ch, sv, lookup, opt), [cov["sv"]]
        else:
            r = eval_highest_gmv(ch, data.get("tap_sv"), data.get("tap_ls"), lookup, opt)
            cv = [cov["tap_sv"], cov["tap_ls"]]
        for c in cv:
            if c is not None and (c[0] > ch.period_start.normalize() or c[1] < ch.period_end.normalize()):
                r.notes.append(f"Data hanya mencakup {c[0]:%d %b}-{c[1]:%d %b}, sedangkan periode challenge {ch.period_label}. "
                               "Hasil belum final.")
                break
        if opt.require_tier and not lookup:
            r.notes.append("Tier mapping kosong - semua creator tersaring. Muat tab brand INT atau matikan 'Wajib sesuai tier'.")
        results.append(r)
    if opt.unique_winner:
        apply_unique_winner(results, opt)
    return results


def results_overview(results: list) -> pd.DataFrame:
    rows = []
    for r in results:
        ch, s = r.challenge, r.summary
        top = ""
        if r.error:
            top = r.error
        elif len(r.winners):
            w = r.winners.iloc[0]
            top = str(w["username"])
        rows.append({
            "Brand": ch.brand, "Kategori": CATEGORY_SHORT[ch.category], "Tier": GROUP_LABEL[ch.tier_group],
            "Periode": ch.period_label, "Peserta": s.get("participants", 0), "Memenuhi syarat": s.get("qualified", 0),
            "Hadiah tersedia": ch.n_winners, "Pemenang": s.get("winners", 0), "Juara 1": top,
            "Total konten": s.get("total_content", 0), "Total GMV (Rp)": s.get("total_gmv", 0),
            "Catatan": len(r.notes) + len(ch.warnings), "Key": ch.key,
        })
    return pd.DataFrame(rows)
