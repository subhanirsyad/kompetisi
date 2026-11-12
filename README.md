# PC × WEI · Monthly Competition Recap

Aplikasi Streamlit lokal untuk merekap juara challenge di tab **Monthly Competition**
(sheet "[INT] PC X WEI - Oct 2026"): **Most Live Streaming**, **Most Short Video**, **Creator Highest GMV**.

Warna Emas Indonesia · Tim Data Analyst

## Menjalankan

```bash
pip install -r requirements.txt
streamlit run app.py
```

Pengaturan yang sering diubah (URL sheet, nama tab, range, tab brand, alias brand, default parameter) ada di blok `CONFIG` paling atas `app.py`.

## Alur pemakaian

1. **Setup Data**
   - ① *Challenge & Tier*: tab Monthly Competition + tab brand INT (Pepsodent (OC) … Rexona (Deo), kolom Username dan kolom M).
   - ② *RAW Activity*: tab `RAW LS` dan `Raw SV` dari sheet RAW (untuk Most LS / Most SV).
   - ③ *TAP*: export TAP LS dan TAP SV full month (untuk Highest GMV). Boleh banyak file; baris yang tumpang tindih diambil dari file terakhir.
   - ④ *Review challenge*: cek dan edit hashtag tiap challenge, atau nonaktifkan challenge tertentu.
2. Klik **▶ Hitung Rekap** di sidebar.
3. Buka halaman hasil (Ringkasan, Most LS, Most SV, Highest GMV), lalu **Export & Audit** untuk mengunduh Excel.

Excel hasil export diawali sheet **Recap** (format sama dengan sheet "October Competition [PC]" > Recap: blok per brand, Incubation LS/SV, Best Content, Rising, Top). Kolom payout (G-M) hanya header dan Best Content dikosongkan karena diisi manual. Sheet audit (Ringkasan, Pemenang, Leaderboard, dst.) tetap ada setelahnya.

Sumber data bisa **Upload file** (tanpa setup Google) atau **Google Sheets**.
Untuk file besar, CSV jauh lebih cepat dibaca daripada .xlsx.

## Akses Google Sheets (opsional)

Taruh file kredensial di folder yang sama dengan `app.py`. Nama file yang dicari (berurutan) ada di `CONFIG["CREDENTIAL_FILES"]`:
`service_account.json`, lalu `credentials.json`.

- **Service account** (JSON berisi `"type": "service_account"`): dipakai langsung. Share kedua spreadsheet ke email `client_email` di file itu (cukup Viewer). Bila belum di-share, aplikasi menampilkan email yang harus di-share.
- **OAuth client** (JSON berisi `"installed"`): browser terbuka untuk login Google pada pemakaian pertama.
- Tanpa file kredensial, pakai mode **Upload file**.

## Aturan perhitungan

| Hal | Aturan |
|---|---|
| Tier | Kolom M halaman brand: Incubation → Tier Content (Incubation), Rising → Rising, Top dan Tier 1+ → Top. Bila satu username muncul di beberapa tier, dipakai yang tertinggi. |
| Most LS | Durasi sesi = Live End − Live Start (bila Live End kosong, pakai kolom Duration). Sesi dihitung unik per Livestream room ID. Hanya sesi ber-hashtag, sesuai brand, dan sepanjang ≥ minimal jam/sesi yang dihitung. Peringkat = total jam terbanyak. |
| Most SV | Peringkat = jumlah Video ID unik ber-hashtag dalam periode, minimal SV sesuai mechanism. Opsional: minimal hari posting berbeda (default 25 hari). |
| Highest GMV | GMV SV + GMV LS dari TAP, hanya konten ber-hashtag dalam periode. Syarat: minimum GMV dan minimum upload (default "10 SV ATAU 10 LS"). |
| Hashtag | Diambil dari kolom Hashtag challenge. Dicocokkan di *Video title* / *Livestream name* (case-insensitive). Akhiran bulan (SEPT / OKT / OCT) diabaikan. |
| Mention | `@dailycarebyunilever` wajib hanya bila mechanism menyebut "mention" (bisa diubah di parameter). |
| Brand TAP | File TAP tidak punya kolom Brand, jadi diturunkan dari *Product name* (aturan di `CONFIG["BRAND_PRODUCT_RULES"]`). |

## Asumsi yang memengaruhi juara (cek dan sesuaikan di panel Parameter)

- Minimal hari posting Most SV = 25 hari (50 SV ÷ 2 SV/hari dari contoh di mechanism). Set 0 untuk mematikan.
- Highest GMV: "10vt / 10x live streaming" dibaca sebagai 10 SV **atau** 10 LS.
- Highest GMV hanya menghitung video yang diposting / live yang dimulai dalam periode challenge.
- GMV dihitung berdasarkan tanggal atribusi di file TAP.

## Kejanggalan di sheet sumber (ditandai otomatis)

- Beberapa baris Most SV memakai hashtag `#MostLS…` (salah salin). Dikoreksi otomatis; bisa dimatikan.
- Hashtag di sheet memakai akhiran `SEPT` / `OKT` yang bercampur dengan `OCT`.
- Teks mechanism beberapa brand masih menyebut produk "Lifebuoy". Muncul sebagai catatan di halaman challenge.
- Total hadiah per challenge dibandingkan dengan kolom Value; selisih ditandai.

## Struktur

```
app.py            UI Streamlit (CONFIG di atas)
engine.py         perhitungan (pandas murni, tanpa Streamlit)
io_sources.py     pembaca CSV/XLSX/Google Sheets
export.py         pembuat workbook Excel hasil
tests/            pytest (engine, I/O, smoke test UI dengan stub Streamlit)
.streamlit/       config.toml (batas upload 2 GB, tema)
```

Tes: `pytest -q`
