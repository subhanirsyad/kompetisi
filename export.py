"""export.py - Susun hasil rekap menjadi satu file Excel yang rapi (untuk laporan / arsip audit)."""
from __future__ import annotations

import io
from datetime import datetime

import pandas as pd

import engine as E

NAVY, GOLD, LIGHT = "#0F2A47", "#C8A24A", "#F3F6FA"
MONEY_HINT = ("(Rp)", "Hadiah", "GMV")


def _rule_text(ch: E.Challenge, opt: E.Options) -> str:
    if ch.category == E.CAT_LS:
        return f"min {ch.min_total_hours or 0:g} jam total; min {ch.min_session_hours or 0:g} jam/sesi"
    if ch.category == E.CAT_SV:
        d = f"; hari posting berbeda >= {opt.min_post_days_sv}" if opt.min_post_days_sv else ""
        return f"min {ch.min_sv or 0} SV unik{d}"
    return f"min GMV {E.fmt_rp(ch.min_gmv or 0)}; min {ch.min_content or 0} SV / LS (live >= {ch.min_session_hours or 0:g} jam)"


def _tag(df: pd.DataFrame, ch: E.Challenge) -> pd.DataFrame:
    df = df.copy()
    df.insert(0, "Periode", ch.period_label)
    df.insert(0, "Tier", E.GROUP_LABEL[ch.tier_group])
    df.insert(0, "Kategori", E.CATEGORY_SHORT[ch.category])
    df.insert(0, "Brand", ch.brand)
    return df


def _concat(frames: list) -> pd.DataFrame:
    frames = [f for f in frames if f is not None and not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def build_tables(results: list, opt: E.Options) -> dict:
    summary, winners, board, review, funnel, notes, dupes = [], [], [], [], [], [], []
    for r in results:
        ch, s = r.challenge, r.summary
        summary.append({
            "Brand": ch.brand, "Kategori": E.CATEGORY_SHORT[ch.category], "Tier/Group": E.GROUP_LABEL[ch.tier_group],
            "Periode": ch.period_label, "Tanggal Pengumuman": ch.announce,
            "Hashtag (dipakai)": " ".join("#" + h for h in ch.hashtags), "Syarat": _rule_text(ch, opt),
            "Affiliates Submission": s.get("participants", 0), "Memenuhi Syarat": s.get("qualified", 0),
            "Slot Hadiah": ch.n_winners, "Pemenang": s.get("winners", 0),
            "Pemenang Belum Memenuhi Syarat": s.get("unqualified_winners", 0),
            "Total Konten (SV/LS)": s.get("total_content", 0), "Total GMV (Rp)": s.get("total_gmv", 0),
            "Hadiah Dibayarkan (Rp)": s.get("prize_paid", 0), "Result (untuk kolom result)": s.get("result_text", ""),
            "Status": r.error or ("OK" if not r.notes else "Cek catatan"),
        })
        if r.error:
            notes.append({"Brand": ch.brand, "Challenge": ch.title, "Jenis": "ERROR", "Catatan": r.error})
            continue
        winners.append(_tag(E.display_table(r, "winners"), ch))
        board.append(_tag(E.display_table(r, "leaderboard"), ch))
        if not r.review.empty:
            rv = r.review.rename(columns={
                "username": "Username", "peringkat_relaxed": "Peringkat jika typo dimaklumi", "peringkat_strict": "Peringkat saat ini",
                "status_strict": "Status saat ini", "catatan": "Alasan tidak lolos filter", "total_jam": "Total Jam LS",
                "sesi_valid": "Sesi Valid", "jumlah_sv": "Jumlah SV", "hari_posting": "Hari Posting", "gmv_total": "GMV Total (Rp)",
                "jumlah_ls": "Jumlah LS"}).drop(columns=["creator_key"], errors="ignore")
            review.append(_tag(rv, ch))
        f = r.funnel.copy()
        funnel.append(_tag(f, ch))
        if len(r.skipped):
            dupes.append(_tag(r.skipped.rename(columns={
                "username": "Username", "peringkat_asli": "Peringkat di challenge ini", "hadiah_terlewat": "Hadiah di sini (Rp)",
                "juara_di": "Juara di challenge", "hadiah_diambil": "Hadiah di sana (Rp)"}), ch))
        for n in r.notes:
            notes.append({"Brand": ch.brand, "Challenge": ch.title, "Jenis": "Catatan hasil", "Catatan": n})
    for r in results:
        for w in r.challenge.warnings:
            notes.append({"Brand": r.challenge.brand, "Challenge": r.challenge.title, "Jenis": "Data sheet", "Catatan": w})
    return {
        "Ringkasan": pd.DataFrame(summary), "Pemenang": _concat(winners), "Leaderboard": _concat(board),
        "Review Hashtag": _concat(review), "Audit Filter": _concat(funnel), "Juara Ganda": _concat(dupes),
        "Catatan": pd.DataFrame(notes),
    }


# ---------------------------------------------------------------------------
# Sheet "Recap" - layout mengikuti sheet "October Competition [PC]" > Recap
# ---------------------------------------------------------------------------
PAYOUT_HEADERS = ["Nama Lengkap", "Nomor HP", "Nama Rekening Bank", "No Rekening Bank", "Link Invoice", "NPWP", "KTP"]
RECAP_PLAN = [
    ("INCUBATION", E.CAT_LS, "Incubation LS"), ("INCUBATION", E.CAT_SV, "INCUBATION SV"), ("INCUBATION", "BEST", "BEST CONTENT"),
    ("RISING", E.CAT_LS, "RISING LS"), ("RISING", E.CAT_SV, "RISING SV"), ("RISING", E.CAT_GMV, "RISING CREATOR"),
    ("TOP", E.CAT_LS, "TOP LS"), ("TOP", E.CAT_SV, "TOP SV"), ("TOP", E.CAT_GMV, "TOP CREATOR"),
]


def _rp_dot(x) -> str:
    return "Rp" + f"{float(x):,.0f}".replace(",", ".")


def _rp_comma(x) -> str:
    return "Rp" + f"{float(x):,.0f}"


def _recap_header(ch: E.Challenge) -> str:
    if ch.category == E.CAT_LS:
        top = f"Min {ch.min_total_hours or 0:g} jam Live ({ch.min_session_hours or 0:g} jam/sesi)"
    elif ch.category == E.CAT_SV:
        top = f"Min {ch.min_sv or 0} SV"
    else:
        top = f"Min GMV {_rp_dot(ch.min_gmv or 0)}; min {ch.min_content or 0} SV/LS (LS min {ch.min_session_hours or 0:g} jam/sesi)"
    tags = " ".join("#" + h.lstrip("#") for h in ch.hashtags)
    return f"{top}\n{tags}".strip()


def build_recap_sheet(wb, results: list, name: str = "Recap"):
    """Tulis sheet Recap. Kolom payout (G-M) hanya header; data pribadi diisi manual oleh tim."""
    ws = wb.add_worksheet(name)
    F = lambda **k: wb.add_format({"font_name": "Arial", "font_size": 10, "valign": "vcenter", **k})
    f_nav = F(bold=True, font_color="white", bg_color=NAVY, align="center", border=1)
    f_title = F(bold=True, font_size=12, font_color="white", bg_color=NAVY)
    f_brand = F(bold=True, bg_color=GOLD)
    f_head = F(bold=True, bg_color=LIGHT, border=1, text_wrap=True, valign="top")
    f_cell = F(border=1)
    f_dur = F(border=1, num_format="[h]:mm:ss", align="right")
    f_num = F(border=1, num_format="0", align="right")
    f_txt_r = F(border=1, align="right")
    f_pay = F(bold=True, font_color="white", bg_color=NAVY, border=1, text_wrap=True, align="center")
    ws.set_column(0, 0, 2); ws.set_column(1, 1, 52); ws.set_column(2, 2, 28); ws.set_column(3, 4, 18); ws.set_column(5, 5, 3)
    ws.set_column(6, 12, 20)

    ok = [r for r in results if not r.error]
    brands_by_tier = {}
    for r in results:
        b = brands_by_tier.setdefault(r.challenge.tier_group, [])
        if r.challenge.brand not in b:
            b.append(r.challenge.brand)

    row, last_tier = 0, None
    for tier, cat, title in RECAP_PLAN:
        if cat == "BEST":
            blocks = [r for r in ok if r.challenge.tier_group == tier and r.challenge.category == E.CAT_LS]
        else:
            blocks = [r for r in ok if r.challenge.tier_group == tier and r.challenge.category == cat]
        if not blocks:
            continue
        blocks.sort(key=lambda r: (brands_by_tier[tier].index(r.challenge.brand), r.challenge.period_start))
        if tier != last_tier:
            for i, b in enumerate(brands_by_tier[tier]):
                ws.write(row, 1 + i, b, f_nav)
            row += 1
            last_tier = tier
        ws.write(row, 1, title, f_title)
        ws.write(row, 2, "", f_title); ws.write(row, 3, "", f_title); ws.write(row, 4, "", f_title)
        for i, h in enumerate(PAYOUT_HEADERS):
            ws.write(row, 6 + i, h, f_pay)
        row += 1
        seen_best = set()
        for r in blocks:
            ch = r.challenge
            if cat == "BEST":
                if ch.brand in seen_best:
                    continue
                seen_best.add(ch.brand)
                ws.write(row, 1, ch.brand, f_brand); ws.write(row, 2, ch.period_label, f_brand)
                ws.write(row + 1, 1, "", f_head); ws.write(row + 1, 2, "Username", f_head); ws.write(row + 1, 3, "Hadiah", f_head)
                for k in range(3):
                    ws.write(row + 2 + k, 1, k + 1, f_num); ws.write(row + 2 + k, 2, "", f_cell); ws.write(row + 2 + k, 3, "", f_cell)
                row += 6
                continue
            ws.write(row, 1, ch.brand, f_brand); ws.write(row, 2, ch.period_label, f_brand)
            heads = {E.CAT_LS: ("Username", "Total Jam LS", "Hadiah"), E.CAT_SV: ("Username", "Jumlah SV", "Hadiah"),
                     E.CAT_GMV: ("Username", "Total GMV", "Hadiah")}[ch.category]
            ws.set_row(row + 1, 30)
            ws.write(row + 1, 1, _recap_header(ch), f_head)
            for i, h in enumerate(heads):
                ws.write(row + 1, 2 + i, h, f_head)
            row += 2
            w = r.winners if r.winners is not None else pd.DataFrame()
            for _, x in w.iterrows():
                ws.write(row, 1, "", f_cell)
                ws.write(row, 2, x.get("username", ""), f_cell)
                if ch.category == E.CAT_LS:
                    hrs = x.get("total_jam", 0) or 0
                    hrs = hrs if hrs > 0 else (x.get("jam_semua", 0) or 0)
                    ws.write_number(row, 3, float(hrs) / 24.0, f_dur)
                elif ch.category == E.CAT_SV:
                    ws.write_number(row, 3, int(x.get("jumlah_sv", 0) or 0), f_num)
                else:
                    ws.write(row, 3, _rp_comma(x.get("gmv_total", 0) or 0), f_txt_r)
                prize = x.get("hadiah", None)
                ws.write(row, 4, _rp_dot(prize) if pd.notna(prize) else "", f_txt_r)
                row += 1
            row += 1
    return ws


def params_table(opt: E.Options, meta: dict) -> pd.DataFrame:
    rows = [
        ("Dibuat pada", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Tahun", opt.year),
        ("Mode hashtag", "Semua hashtag wajib" if opt.hashtag_mode == "all" else "Minimal satu hashtag"),
        ("Abaikan akhiran bulan (SEPT/OKT/OCT)", opt.strip_month_suffix),
        ("Koreksi otomatis MostLS<->MostSV di sheet", opt.autocorrect_sheet_hashtag),
        ("Kebijakan mention", {"mechanism": "Ikuti mechanism", "always": "Selalu wajib", "never": "Abaikan"}[opt.mention_policy]),
        ("Filter brand (produk harus brand challenge)", opt.filter_brand),
        ("Wajib sesuai tier mapping", opt.require_tier),
        ("Min. hari posting berbeda (Most SV)", opt.min_post_days_sv),
        ("Syarat konten Highest GMV", "10 SV ATAU 10 LS" if opt.min_content_mode == "atau" else "SV + LS gabungan"),
        ("Highest GMV: video/live harus mulai dalam periode", opt.require_post_in_period),
        ("Satu creator hanya juara sekali (hadiah terbesar diprioritaskan)", opt.unique_winner),
        ("Mode penentuan juara", {"strict": "Ketat", "minimum": "Syarat minimum longgar", "all": "Longgar penuh", "any": "Performa saja"}[opt.fill_mode]),
    ]
    for k, v in meta.items():
        rows.append((f"Data: {k}", v))
    return pd.DataFrame(rows, columns=["Parameter", "Nilai"])


def build_workbook(results: list, opt: E.Options, meta: dict) -> bytes:
    tables = build_tables(results, opt)
    tables["Parameter"] = params_table(opt, meta)
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="xlsxwriter") as xw:
        wb = xw.book
        build_recap_sheet(wb, results)
        head = wb.add_format({"bold": True, "font_color": "white", "bg_color": NAVY, "border": 1, "valign": "vcenter", "text_wrap": True})
        money = wb.add_format({"num_format": "#,##0"})
        dec = wb.add_format({"num_format": "0.00"})
        intf = wb.add_format({"num_format": "0"})
        wrap = wb.add_format({"text_wrap": True, "valign": "top"})
        for name, df in tables.items():
            if df.empty:
                df = pd.DataFrame({"Info": ["Tidak ada data"]})
            df = df.copy()
            df.to_excel(xw, sheet_name=name, index=False, na_rep="")
            ws = xw.sheets[name]
            ws.freeze_panes(1, 0)
            ws.autofilter(0, 0, max(len(df), 1), len(df.columns) - 1)
            ws.set_row(0, 30)
            for i, col in enumerate(df.columns):
                ws.write(0, i, col, head)
                sample = df[col].head(200).fillna("").astype(str)
                longest = sample.str.len().max() if len(sample) else 8
                longest = 8 if pd.isna(longest) else int(longest)
                width = min(max(len(str(col)) + 2, longest + 2), 60)
                fmt = None
                if str(col) == "Rank":
                    fmt = intf
                elif pd.api.types.is_numeric_dtype(df[col]) and any(h in str(col) for h in MONEY_HINT):
                    fmt = money
                elif pd.api.types.is_float_dtype(df[col]) and "jam" in str(col).lower():
                    fmt = dec
                elif col in ("Result (untuk kolom result)", "Catatan", "Hashtag (dipakai)", "Alasan tidak lolos filter", "Syarat", "Status"):
                    fmt, width = wrap, min(max(width, 28), 70)
                ws.set_column(i, i, width, fmt)
    return bio.getvalue()
