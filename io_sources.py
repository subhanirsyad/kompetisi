"""
io_sources.py - Membaca data dari Google Sheets (gspread) atau dari file upload.

Semua fungsi mengembalikan DataFrame string / frame standar engine, tidak memakai Streamlit,
sehingga bisa dipakai dari script biasa.
"""
from __future__ import annotations

import os
import re
from typing import Callable, Iterable, Optional

import pandas as pd

import engine as E

Progress = Optional[Callable[[str], None]]


def _say(cb: Progress, msg: str):
    if cb:
        cb(msg)


# --------------------------------------------------------------------------- #
# File upload / path
# --------------------------------------------------------------------------- #
def _rewind(f):
    if hasattr(f, "seek"):
        f.seek(0)


def read_table(file, usecols: Optional[Iterable[str]] = None, sheet: Optional[str] = None) -> pd.DataFrame:
    """CSV / XLSX -> DataFrame (semua string). Hanya kolom di `usecols` yang dibaca (hemat memori)."""
    name = getattr(file, "name", str(file)).lower()
    wanted = {c.strip().lower() for c in usecols} if usecols else None
    sel = (lambda c: str(c).strip().lower() in wanted) if wanted else None
    if name.endswith((".csv", ".txt", ".tsv")):
        df = pd.read_csv(file, dtype=str, keep_default_na=False, usecols=sel, encoding="utf-8-sig", low_memory=False)
        if df.shape[1] <= 1:                                   # kemungkinan pemisah ';' atau tab
            _rewind(file)
            sep = "\t" if name.endswith(".tsv") else ";"
            df = pd.read_csv(file, dtype=str, keep_default_na=False, usecols=sel, encoding="utf-8-sig", sep=sep, low_memory=False)
        return df
    xl = pd.ExcelFile(file)
    sh = sheet if sheet in xl.sheet_names else ("Custom report" if "Custom report" in xl.sheet_names else xl.sheet_names[0])
    return pd.read_excel(xl, sheet_name=sh, dtype=str, usecols=sel).fillna("")


def read_workbook_tabs(file, tabs: dict, max_col: int = 16) -> dict:
    """Baca beberapa tab saja dari workbook besar (read-only streaming). tabs: {kunci: nama_tab}."""
    from openpyxl import load_workbook
    wb = load_workbook(file, read_only=True, data_only=True)
    out = {}
    try:
        lower = {s.strip().lower(): s for s in wb.sheetnames}
        for key, tab in tabs.items():
            real = lower.get(tab.strip().lower())
            if real is None:
                continue
            rows = [["" if v is None else str(v) for v in r]
                    for r in wb[real].iter_rows(min_col=1, max_col=max_col, values_only=True)]
            out[key] = E.frame_from_values(rows, header=False)
    finally:
        wb.close()
    return out


def read_tap(files: list, kind: str, opt: E.Options, progress: Progress = None):
    """
    Satu/lebih file TAP (xlsx/csv) -> (frame standar, meta).

    Bila beberapa file saling tumpang tindih tanggalnya (mis. 1-15 Okt dan 10-31 Okt), baris dengan kunci yang sama
    (Date + Campaign ID + Creator + Product ID + Video/Room ID) hanya diambil dari file TERBARU (urutan upload).
    Baris kembar DI DALAM satu file tidak dibuang: video yang sama bisa muncul di dua campaign dengan GMV berbeda.
    """
    is_ls = kind == "ls"
    cols = E.TAP_LS_COLS if is_ls else E.TAP_SV_COLS
    key = ["Date", "Campaign ID", "Creator name", "Product ID", "Livestream room ID" if is_ls else "Video ID"]
    frames = []
    for i, f in enumerate(files):
        _say(progress, f"Membaca {getattr(f, 'name', f)} ...")
        d = read_table(f, usecols=cols)
        d["_f"] = i
        frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    before = len(df)
    found = [E._col(df, k, required=False) for k in key]
    if len(files) > 1 and all(found):
        latest = df.groupby(found, sort=False)["_f"].transform("max")
        df = df[df["_f"] == latest]
    _say(progress, "Merapikan data TAP ...")
    std = E.std_tap_ls(df, opt) if is_ls else E.std_tap_sv(df, opt)
    gcol = E._col(df, "Creator LIVE-attributed GMV" if is_ls else "Affiliate video-attributed GMV")
    body = df[df[E._col(df, "Date")].astype(str).str.lower() != "summary"]
    dropped = float(E.money_series(body[gcol]).sum() - std["gmv"].sum())
    meta = {"files": len(files), "rows_raw": before, "overlap_removed": before - len(df), "rows": len(std),
            "gmv_dropped": round(dropped, 2)}
    return std, meta


def read_raw(file, kind: str, opt: E.Options, progress: Progress = None):
    """CSV/XLSX hasil export tab RAW LS / Raw SV -> (frame standar, meta)."""
    cols = E.RAW_LS_COLS if kind == "ls" else E.RAW_SV_COLS
    _say(progress, f"Membaca {getattr(file, 'name', file)} ...")
    df = read_table(file, usecols=cols)
    return _std_raw(df, kind, opt, progress)


def _std_raw(df: pd.DataFrame, kind: str, opt: E.Options, progress: Progress = None):
    _say(progress, "Merapikan data RAW ...")
    std = E.std_raw_ls(df, opt) if kind == "ls" else E.std_raw_sv(df, opt)
    return std, {"rows_raw": len(df), "rows": len(std)}


def tier_frames_from_csvs(files: list) -> dict:
    """CSV per tab brand: nama file dipakai untuk menebak brand (mis. 'Pepsodent (OC).csv')."""
    out = {}
    for f in files:
        stem = os.path.splitext(os.path.basename(getattr(f, "name", str(f))))[0]
        _rewind(f)
        out[stem] = pd.read_csv(f, dtype=str, keep_default_na=False, header=None, encoding="utf-8-sig", low_memory=False)
    return out


# --------------------------------------------------------------------------- #
# Google Sheets
# --------------------------------------------------------------------------- #
def spreadsheet_id(url_or_id: str) -> str:
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", url_or_id or "")
    return m.group(1) if m else (url_or_id or "").strip()


def _find_credentials(candidates) -> list:
    """Cari file kredensial Google: path apa adanya, lalu relatif ke folder aplikasi. Return [(path, info_json)]."""
    import json
    if isinstance(candidates, str):
        candidates = [candidates]
    here = os.path.dirname(os.path.abspath(__file__))
    found = []
    for c in candidates or []:
        for path in (c, os.path.join(here, c)):
            if path and os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8") as fh:
                        found.append((path, json.load(fh)))
                except (OSError, ValueError):
                    pass
                break
    return found


def credential_email(candidates) -> Optional[str]:
    """Email service account (untuk petunjuk 'share sheet ke ...'); None bila bukan service account."""
    for _, info in _find_credentials(candidates):
        if info.get("type") == "service_account":
            return info.get("client_email")
    return None


def gs_client(credential_files=None):
    """
    credential_files: satu path atau daftar path (dicoba berurutan).
    - JSON bertipe service_account  -> gspread.service_account
    - JSON OAuth client (installed/web) -> gspread.oauth (login lewat browser)
    - tidak ada file -> gspread.oauth() dengan lokasi default gspread
    """
    try:
        import gspread
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("Library gspread belum terpasang. Jalankan: pip install gspread google-auth") from e
    for path, info in _find_credentials(credential_files):
        if info.get("type") == "service_account":
            return gspread.service_account(filename=path)
        if "installed" in info or "web" in info:
            return gspread.oauth(credentials_filename=path)
    try:
        return gspread.oauth()
    except FileNotFoundError as e:
        raise RuntimeError("File kredensial Google tidak ditemukan. Taruh service account JSON di folder aplikasi "
                           "(nama sesuai CONFIG['CREDENTIAL_FILES']) atau pakai mode 'Upload file'.") from e


def _values(client, sid: str, tab: str, a1: str) -> list:
    ws = client.open_by_key(sid).worksheet(tab)
    return ws.get_values(a1)


def fetch_competition(client, sid: str, tab: str, a1: str) -> pd.DataFrame:
    return E.frame_from_values(_values(client, sid, tab, a1), header=False)


def fetch_tier_frames(client, sid: str, brand_tabs: dict, a1: str, progress: Progress = None) -> dict:
    out = {}
    for brand, tab in brand_tabs.items():
        _say(progress, f"Mengambil tier mapping: {tab} ...")
        out[tab] = E.frame_from_values(_values(client, sid, tab, a1), header=False)
    return out


def fetch_raw(client, sid: str, tab: str, a1: str, kind: str, opt: E.Options, progress: Progress = None):
    _say(progress, f"Mengambil {tab} dari Google Sheets (bisa 0,5-2 menit untuk data besar) ...")
    df = E.frame_from_values(_values(client, sid, tab, a1), header=True)
    return _std_raw(df, kind, opt, progress)
