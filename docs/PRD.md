# Kebutuhan RestoreLab

RestoreLab adalah workstation latihan lokal untuk membuktikan pemulihan data PostgreSQL. Operator menjalankan satu skenario sintetis, melihat urutan operasi native, kemudian memeriksa hasil pembandingan data. Source aplikasi dan data controller berada pada direktori proyek; database, backup fisik, arsip WAL dan clone berada pada volume Docker khusus proyek `restorelab`.

## Hasil yang harus dibuktikan

1. Fixture dimulai dengan 100 pesanan sintetis.
2. Physical base backup dibuat dan manifest diperiksa menggunakan alat PostgreSQL.
3. Tiga pesanan ditambahkan sesudah backup selesai. Ketiganya wajib ditemukan pada hasil pemulihan sehingga keberhasilan tidak bisa berasal dari menyalin backup awal saja.
4. Named restore point dibuat sebelum penghapusan. Pesanan 11 sampai 20 kemudian dihapus dan marker insiden ditulis.
5. Segmen WAL yang diperlukan diarsipkan dan dipakai oleh cluster pemulihan terpisah.
6. PostgreSQL pemulihan berhenti pada target bernama dan tetap dalam recovery paused/read-only.
7. Hasil mempunyai 103 pesanan, fingerprint isi pesanan sama dengan sebelum insiden, sepuluh ID yang dihapus kembali, tiga pesanan pascabackup tersedia, dan marker insiden tidak ada.

## Pengendali latihan

Hanya satu latihan boleh aktif. Pengulangan `request_id` yang sama membuka hasil permintaan pertama, termasuk setelah controller dimulai ulang. Permintaan baru saat latihan aktif ditolak. Batas default lima percobaan tersimpan, termasuk gagal dan terputus, membatasi penggunaan normal melalui dashboard. Histori, backup dan arsip tidak dipangkas otomatis.

Controller memiliki direktori datanya secara eksklusif lintas proses. Saat startup, percobaan lama yang masih queued/running ditandai interrupted. Operator memulai percobaan baru secara eksplisit; aplikasi tidak mengulang insiden lama secara otomatis. Sisa container tools dari proses yang terputus menahan latihan berikutnya agar pekerjaan native tidak tumpang tindih.

Status verified hanya diterbitkan setelah pemeriksaan native dan validasi laporan controller berhasil. Percobaan gagal/terputus tidak menyediakan unduhan laporan keberhasilan. Bukti terminal tidak ditimpa oleh retry.

## Antarmuka

Dashboard harus memperlihatkan scope data sintetis, konfirmasi sebelum memulai, fase yang sedang berjalan, histori, pembandingan empat snapshot, waktu pemulihan yang diukur, serta detail target dan fingerprint yang dapat dibuka. Koneksi terputus tidak menghapus tampilan hasil terakhir atau menghasilkan status berhasil. Pengiriman dengan hasil HTTP yang belum diketahui menggunakan ID permintaan yang sama ketika dicoba ulang.

UI menggunakan bahasa Indonesia, label nyata dan kontrol keyboard. Tampilan harus bekerja pada ponsel dan desktop. Screenshot dokumentasi berasal dari latihan PostgreSQL asli; fixture browser hanya untuk tes keadaan UI dan diberi label dalam pengujian.

## Ruang lingkup

Tidak ada URL database eksternal, SQL bebas, pemilihan volume, unggahan backup, atau penghapusan volume melalui HTTP. Dashboard ditujukan untuk komputer lokal tepercaya dan tidak mempunyai login multiuser. Bind loopback, validasi Host/Origin, serta body JSON terbatas mengatur perimeter aplikasi lokal.

Skenario yang disediakan memakai satu named restore point. Pemilihan timestamp sembarang, penjadwalan backup produksi, replikasi aktif, failover, enkripsi penyimpanan, dan rotasi arsip belum disediakan. Fingerprint membandingkan tabel pesanan sintetis; keberhasilan tidak mengklaim identitas seluruh katalog PostgreSQL. Waktu pemulihan adalah hasil pengukuran latihan, bukan jaminan RTO/RPO produksi.
