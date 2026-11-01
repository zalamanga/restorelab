# Bukti verifikasi RestoreLab

Pemeriksaan lokal dilakukan pada 1 November 2026 di Windows 10, Python 3.13.1, Node.js 22.14.0 dan Docker Desktop dengan Linux containers. Semua data pesanan adalah data sintetis.

## GitHub Actions

[Run 41303604728](https://github.com/zalamanga/restorelab/actions/runs/41303604728) berhasil pada source [`439b22e`](https://github.com/zalamanga/restorelab/tree/439b22e80fd312963cadbbe93e2263f0f6c44290). Runner Ubuntu 24.04 menjalankan 49 unit test, recovery native melalui HTTP, penolakan backup rusak, delapan kasus browser bersama dan satu capture dokumentasi. Semua tahap tersebut lulus, termasuk pemeriksaan dependensi npm.

Pengujian Linux menemukan perbedaan izin secret bind mount yang tidak muncul di Windows. Perbaikan mempertahankan file host `0600`: startup backup membaca secret sebagai root, kemudian menurunkan UID sebelum SQL dan backup. Fixture container terisolasi pada image yang sama membuktikan pengguna PostgreSQL tidak bisa membaca file dummy tersebut secara langsung, tetapi menerima password melalui environment setelah privilege drop. Query dan backup pada fixture berjalan dengan UID 70. CI kemudian memverifikasi backup dan recovery PostgreSQL yang sebenarnya.

## Hasil lokal

| Pemeriksaan | Hasil dan bukti |
|---|---|
| Unit, konkurensi dan HTTP | 49 lulus, 0 gagal, 0 dilewati; `test-results/unit-summary.json`. Termasuk proses Python terpisah untuk owner lock, request bersamaan, ledger persisten, framing HTTP dan penolakan laporan yang diubah. |
| Physical backup dan WAL melalui engine | Latihan `9221cc21f2564a9bad1659a75e3c3cbb` mencapai 100, 103, 93 lalu 103 pesanan. Fingerprint sebelum insiden sama dengan hasil recovery. |
| Job melalui HTTP | Latihan `295fe852e5924b7d9a3527aa9295ec6b` verified. POST ulang dengan request ID yang sama memakai job yang sama. Unduhan laporan cocok dengan SHA-256 `e91d467ed66627311e42d03f179dcfc4404a0e18a6d6f4d6527916a1d044b1a8`. |
| Tombol dashboard ke PostgreSQL asli | Latihan `3a17dceffa1f4bcca5c9c40ed6804147` dijalankan dari checkbox dan tombol browser sampai verified, lalu laporannya diunduh dan diperiksa. |
| Source terbaru setelah pindah folder | Controller dijalankan ulang dari lokasi final, lalu latihan `86cf0b1e589b4689ae87793f5de3f72d` lulus. Fingerprint cocok, recovery pause pada target, dan marker insiden tidak ikut dipulihkan. SHA-256 laporan `30ddab36d0b917cd94366dd2551f654255b2c0b0a795f33b4872823719d4f531`. |
| Backup rusak | Salinan backup terpisah `0cd6d2703599452d9c4dd07ceeb1b218` sengaja diubah pada `base/PG_VERSION`. Diagnostic membuktikan `pg_verifybackup` sendiri menolak file tersebut. Backup asli valid sebelum dan setelah pengujian. Database sumber tidak diubah oleh tes ini. |
| Browser | Delapan kasus unik lulus: tujuh pada suite awal, satu diulang setelah selector tes disclosure diperjelas. Perbaikannya hanya pada selector tes, bukan aplikasi. CI menjalankan kedelapan kasus bersama. |
| Screenshot | Satu tes capture lulus. Gambar hasil verified, bukti teknis dan mobile berasal dari job PostgreSQL asli; gambar running diambil saat job native melalui UI berlangsung. |

File JSON lokal di atas diabaikan Git. Workflow mengunggah hasil yang dihasilkan runner ke artifact terbatas JSON/PNG. File secret, ledger, diagnostik proses dan cache tidak diunggah.

## Apa yang benar-benar dibuktikan

Backup fisik berisi 100 pesanan. Tiga pesanan baru dibuat setelah backup. Sepuluh pesanan kemudian dihapus setelah named restore point. Recovery harus memulihkan ketiga pesanan baru dan sepuluh ID yang hilang, tetapi mengecualikan marker insiden. Engine memeriksa manifest, fingerprint seluruh kolom pesanan yang diserialisasi secara kanonis, system identifier, run identity, LSN, status in-recovery dan replay yang sedang pause.

Jumlah baris saja tidak cukup. Manifest valid juga tidak cukup. Kedua pemeriksaan tersebut dilengkapi pembandingan isi dan bukti native PostgreSQL pada cluster recovery yang terpisah.

Enam tes UI memakai respons fixture yang diberi label `UI fixture` untuk membuat kondisi offline, quota, interrupted dan respons terlambat secara deterministik. Dua tes browser lainnya membaca atau menjalankan recovery native. Fixture bukan bukti recovery database.

## Cakupan tampilan

Browser menguji lebar 320, 390, 768, 1024 dan 1440 piksel, keyboard, reduced motion, pemulihan koneksi, fokus saat polling, pemilihan riwayat dan teks berbahaya sebagai teks biasa. Screenshot akhir diperiksa langsung untuk keterbacaan dan susunan desktop/mobile.

Kontras pasangan warna normal paling rendah yang diukur adalah 4,90:1 pada tombol disabled; teks muted 5,76:1, primer 8,06:1 dan label status paling rendah 6,23:1. Ini adalah pemeriksaan pasangan warna UI, bukan sertifikasi aksesibilitas produk.

## Batas hasil

Durasi di laporan adalah pengukuran pada komputer dan job tersebut. `recovery_seconds` mencakup penyiapan clone, startup serta pemeriksaan hasil. Angka tersebut bukan benchmark replay WAL murni dan bukan janji RTO/RPO produksi.

Lab memakai satu skenario dan named restore point. Tidak ada input timestamp bebas, database eksternal atau promosi otomatis. Histori verified menyimpan hasil pada waktu pengujian; restart controller tidak menguji ulang clone lama.

## Menjalankan pemeriksaan lagi

Lihat [README](../README.md) untuk prasyarat dan startup. Unit test tidak membutuhkan Docker. Tes native membutuhkan Docker Linux containers dan menambah data backup/WAL. Tes browser lengkap juga memulai satu latihan native baru. Capture hanya membaca hasil verified yang sudah ada.

```powershell
python tests/run_unit.py
python tests/native_verify.py --timeout 600
python tests/native_corruption.py
npm run test:browser
npm run screenshots
```

Jalankan hanya saat tidak ada drill lain yang aktif. Batas percobaan controller dihitung persisten, termasuk job failed/interrupted; tidak ada reset atau penghapusan volume otomatis.
