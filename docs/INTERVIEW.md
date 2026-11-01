# Menjelaskan RestoreLab saat interview

## Penjelasan sekitar satu menit

> RestoreLab adalah workstation lokal untuk menguji apakah backup PostgreSQL benar-benar bisa dipulihkan. Saya memakai Python untuk controller dan worker, SQLite untuk ledger pekerjaan, Docker Compose untuk cluster terisolasi, serta JavaScript untuk jurnal pemulihan.
>
> Skenarionya dimulai dengan 100 pesanan sintetis, kemudian physical backup dibuat. Saya menambahkan tiga pesanan setelah backup, membuat named restore point, lalu menghapus sepuluh pesanan. Engine memulihkan backup ke clone terpisah dan memutar ulang WAL sampai titik sebelum insiden.
>
> Keberhasilan tidak ditentukan oleh container hidup saja. Engine memeriksa 103 pesanan, fingerprint dan isi data, ketiga perubahan setelah backup, sepuluh pesanan yang kembali, serta marker insiden yang tidak ikut pulih. Semua bukti disimpan per latihan dan bisa diunduh sebagai JSON.

Gunakan kalimat itu hanya setelah kamu memahami alurnya. Jika ada bagian yang belum pernah kamu ubah sendiri, jelaskan sesuai pengalaman saat mempelajari dan mengerjakan repository ini.

## Persiapan demo

1. Pastikan Docker Desktop memakai Linux containers dan Python 3.13 tersedia.
2. Jalankan `scripts/Start.ps1` dari folder repository.
3. Buka [http://127.0.0.1:8140](http://127.0.0.1:8140).
4. Periksa quota. Default-nya lima percobaan; latihan gagal dan terputus ikut dihitung.
5. Jika sudah ada hasil terverifikasi, buka hasil itu untuk menjelaskan bukti sebelum memulai latihan tambahan.

Startup tidak membuat job. Latihan baru hanya dimulai setelah konfirmasi operator atau POST native test. Satu latihan baru memakai ruang disk untuk backup dan clone.

## Urutan demo sekitar lima menit

| Bagian | Yang ditunjukkan dan dijelaskan |
|---|---|
| Skenario | Data sintetis, target sebelum penghapusan, dan recovery pada clone |
| Konfirmasi | Operator memahami bahwa fixture lab disiapkan ulang |
| Jurnal | Sembilan fase PostgreSQL dan waktu kejadian aktual |
| Riwayat | Request dan bukti tetap ada setelah controller restart |
| Perbandingan | 100 saat backup, 103 sebelum insiden, 93 sesudahnya, 103 pada clone |
| Temuan | Pesanan baru ikut pulih dan penanda insiden tidak ikut dipulihkan |
| Bukti teknis | Restore point, LSN, segmen WAL, fingerprint, dan replay yang pause |
| JSON | Unduh laporan dari backend, bukan screenshot angka yang diketik manual |

Jika kamu menunggu native recovery saat demo, jelaskan perbedaan base backup dan WAL. Jika koneksi bermasalah atau quota habis, gunakan riwayat terverifikasi yang sudah ada dan jelaskan bahwa percobaan baru belum dijalankan. Jangan menyebut pekerjaan gagal sebagai berhasil.

## Jawaban teknis yang perlu dipahami

### Mengapa physical backup dan WAL harus dipakai bersama?

Base backup merekam cluster pada proses backup. Tiga pesanan baru dibuat sesudahnya, jadi data itu tidak tersedia dari base backup saja. WAL membawa perubahan sesudah backup. PostgreSQL memutar ulang WAL sampai named restore point sebelum operasi penghapusan.

### Mengapa tidak cukup melihat `pg_verifybackup` berhasil?

Manifest yang valid membuktikan pemeriksaan integritas backup tersebut berhasil. Itu belum membuktikan arsip WAL lengkap sampai target atau hasil data sesuai kebutuhan. RestoreLab juga menjalankan recovery native pada clone dan memeriksa hasilnya.

### Mengapa hasilnya harus 103, bukan 100?

Target ditempatkan setelah tiga pesanan baru masuk. Kembali ke 100 berarti perubahan setelah backup belum pulih. Mendapatkan 103 saja juga belum cukup; isi dan fingerprint harus sama dengan snapshot sebelum insiden.

### Bagaimana memastikan insiden tidak ikut di-replay?

Engine menulis marker insiden setelah restore point. Pada clone, marker harus tidak ada. Engine juga memeriksa named target, active run, identitas cluster, replay yang pause, serta posisi replay terhadap target dan posisi insiden.

### Apa perbedaan SQLite dan PostgreSQL di proyek ini?

SQLite menyimpan ledger controller: request ID, antrean, status, event, dan laporan. PostgreSQL adalah database yang dibackup, diberi insiden sintetis, dan dipulihkan. WAL SQLite dan WAL PostgreSQL adalah mekanisme pada database berbeda; keduanya tidak saling dipertukarkan.

### Bagaimana mencegah klik ganda membuat dua recovery?

Client membuat request ID dan mempertahankannya saat hasil pengiriman belum diketahui. Di SQLite, request ID memiliki constraint unik. `Store.create()` mencari request yang sama di dalam transaksi sebelum membuat job baru. Unique index lain membatasi satu job `queued` atau `running`. Jadi perlindungannya tidak bergantung pada tombol disabled saja.

### Apa yang terjadi jika controller crash?

Pada startup berikutnya, job lama yang masih antre atau berjalan menjadi `interrupted`. Tidak ada replay otomatis karena pekerjaan sebelumnya mungkin sudah menghapus fixture atau membuat backup. Bukti disimpan untuk pemeriksaan. Engine juga menolak mulai jika tools native lama masih berjalan.

### Bagaimana menjaga operasi tetap pada data latihan?

Target Compose, database, user, schema, dan SQL ditentukan di kode. API tidak menerima URL database atau SQL bebas. Engine memeriksa versi mayor, marker lab, active run, system ID cluster dan arsip. Script menolak direktori target yang sudah ada atau volume nonkosong tanpa marker sesuai. Ini pembatasan workstation, bukan klaim bahwa aplikasi sudah cocok untuk operasi produksi.

### Apakah akun database sudah mengikuti least privilege?

Belum. Role `restorelab` dibuat sebagai superuser oleh image PostgreSQL untuk mengerjakan operasi cluster pada lab terisolasi. Jangan menyebutnya akun aplikasi dengan hak minimum. Pada produksi, saya perlu memisahkan akun aplikasi, backup/replikasi, dan operator, lalu menentukan privilege masing-masing sesuai pekerjaan yang diizinkan.

### Mengapa ada beberapa lock?

Owner file lock menjaga satu controller memiliki ledger. Transaksi serta constraint SQLite menjaga dedupe, quota, dan satu job aktif. Lock engine menjaga satu eksekusi native di dalam proses. Pemeriksaan tools lama menangani kemungkinan pekerjaan native tertinggal setelah controller mati.

### Apakah ini sistem backup produksi?

Proyek ini adalah latihan recovery lokal. Belum ada penjadwalan, retensi otomatis, enkripsi backup aplikasi, arsip di lokasi lain, banyak operator, promosi clone, maupun pengalihan trafik. Named restore point juga ditetapkan engine, bukan waktu insiden bebas yang dimasukkan pengguna.

### Apakah fingerprint membuktikan seluruh database sama?

Tidak. Fingerprint dihitung dari pesanan yang dibaca `_snapshot()` dengan urutan dan JSON kanonis, termasuk marker run pada row tersebut. Engine melakukan pemeriksaan tambahan pada marker dan identitas cluster. Hasilnya tidak menyatakan seluruh objek database atau semua file eksternal identik.

### Apakah angka waktu itu RTO dan RPO?

Angka yang tampil adalah waktu terukur pada lab ini. Timer recovery dimulai sebelum persiapan clone dan berakhir setelah pemeriksaan hasil, jadi bukan durasi replay saja. RTO/RPO produksi membutuhkan target bisnis, beban, volume, infrastruktur, dan pengujian operasi yang sesuai. Proyek ini tidak menjaminnya.

### Mengapa server tidak memakai login?

Server ditujukan untuk operator lokal, terikat pada `127.0.0.1`, dan memeriksa Host serta Origin untuk mutation. Ini tidak setara dengan autentikasi pengguna. Karena itu port tidak boleh dijadikan layanan publik melalui tunnel atau reverse proxy tanpa rancangan akses tambahan.

## Bagian kode yang dibuka saat diminta

| Pertanyaan interviewer | Mulai membaca di |
|---|---|
| Validasi request dan same-origin | [`server.py`](../restorelab/server.py), `Handler._perimeter()` dan `do_POST()` |
| Antrean dan request yang sama | [`store.py`](../restorelab/store.py), `Store.create()` dan `claim()` |
| Worker dan status gagal/terputus | [`service.py`](../restorelab/service.py), `Service._work()` dan `validate_report()` |
| Pembuktian pemulihan | [`engine.py`](../restorelab/engine.py), `Engine._execute()` pada fase `verify` |
| Physical backup | [`backup.sh`](../infrastructure/backup.sh) |
| Named-target recovery | [`prepare-recovery.sh`](../infrastructure/prepare-recovery.sh) |
| UI yang menangani retry | [`app.js`](../www/app.js), `refresh()` dan handler submit |
| Tes PostgreSQL asli | [`native_verify.py`](../tests/native_verify.py) dan [`native_corruption.py`](../tests/native_corruption.py) |

## Pengembangan yang masuk akal berikutnya

Jika proyek dikembangkan menuju kebutuhan tim, mulai dari autentikasi operator dan audit akses, isolasi instance yang lebih kuat, retensi terukur, serta penyimpanan arsip di lokasi lain. Setiap perubahan strategi recovery harus memiliki tes restore nyata. Tambahkan jadwal backup setelah proses pemulihannya terbukti dan batas operasinya jelas.

Untuk status tes dan bukti yang sudah dijalankan, gunakan [VERIFICATION.md](VERIFICATION.md). Jangan menyebut hasil CI atau jumlah tes dari ingatan jika kode dan bukti yang dibuka sudah berbeda.
