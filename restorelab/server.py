import argparse
import json
import os
from pathlib import Path
import signal
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import uuid

from .service import Service
from .store import RUN_ID, StoreError


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    server_version = "RestoreLab"

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_arguments):
        pass

    def _reply(self, status, body, kind="application/json; charset=utf-8", headers=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD" and status != 204:
            self.wfile.write(body)

    def _error(self, status, code, message):
        self._reply(status, {"error":{"code":code,"message":message}})

    def _perimeter(self, mutation=False):
        port = self.server.server_address[1]
        hosts = self.headers.get_all("Host", [])
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if len(hosts) != 1 or hosts[0].lower() not in allowed:
            self._error(403, "host_rejected", "Host permintaan tidak diizinkan.")
            return False
        try:
            target = urlsplit(self.path)
        except ValueError:
            self._error(400, "invalid_target", "Alamat permintaan tidak valid.")
            return False
        if target.scheme or target.netloc or target.query or target.fragment or not self.path.startswith("/") or self.path.startswith("//"):
            self._error(400, "invalid_target", "Alamat permintaan tidak valid.")
            return False
        if mutation:
            origins = self.headers.get_all("Origin", [])
            if len(origins) != 1 or origins[0].lower() != "http://" + hosts[0].lower() or self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
                self._error(403, "origin_rejected", "Permintaan perubahan harus berasal dari halaman RestoreLab ini.")
                return False
        return True

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._perimeter():
            return
        try:
            path = self.path
            if path == "/health":
                self._reply(200, {"status":"ok","app":"restorelab","process_id":os.getpid(),"instance":self.server.instance})
            elif path == "/api/state":
                self._reply(200, self.server.service.state())
            elif path.startswith("/api/runs/"):
                parts = path.split("/")
                if len(parts) not in (4,5) or not RUN_ID.fullmatch(parts[3]) or (len(parts)==5 and parts[4]!="report"):
                    raise StoreError("not_found", "Alamat bukti tidak ditemukan.", 404)
                if len(parts) == 5:
                    report = self.server.service.get_report(parts[3])
                    self._reply(200, report, headers={"Content-Disposition":f'attachment; filename="restorelab-{parts[3]}.json"'})
                else:
                    self._reply(200, self.server.service.get_run(parts[3]))
            elif path == "/favicon.ico":
                self._reply(204, b"", "image/x-icon")
            elif path in {"/", "/styles.css", "/app.js"}:
                filename, kind = {"/":("index.html","text/html; charset=utf-8"),"/styles.css":("styles.css","text/css; charset=utf-8"),"/app.js":("app.js","text/javascript; charset=utf-8")}[path]
                self._reply(200, (self.server.service.root / "www" / filename).read_bytes(), kind)
            else:
                self._error(404, "not_found", "Halaman tidak ditemukan.")
        except StoreError as error:
            self._error(error.status, error.code, str(error))
        except (OSError, ValueError):
            self._error(503, "not_ready", "Bukti atau berkas aplikasi belum tersedia.")
        except sqlite3.Error:
            self._error(503, "storage_unavailable", "Penyimpanan bukti belum tersedia. Mulai ulang controller setelah masalah penyimpanan diperbaiki.")

    def do_POST(self):
        if not self._perimeter(mutation=True):
            self.close_connection = True
            return
        if self.path != "/api/runs":
            self.close_connection = True
            self._error(404, "not_found", "Aksi tidak ditemukan.")
            return
        try:
            if self.headers.get_all("Transfer-Encoding"):
                raise StoreError("invalid_body", "Transfer body tidak didukung.", 400)
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isdigit():
                raise StoreError("invalid_length", "Panjang JSON harus dinyatakan sekali.", 400)
            size = int(lengths[0])
            if size > 4096:
                raise StoreError("body_too_large", "JSON permintaan terlalu besar.", 413)
            if size < 1:
                raise StoreError("invalid_body", "JSON permintaan kosong.", 400)
            types = self.headers.get_all("Content-Type", [])
            if len(types) != 1 or self.headers.get_content_type() != "application/json" or (self.headers.get_content_charset() or "utf-8").lower() != "utf-8":
                raise StoreError("unsupported_media", "Gunakan JSON UTF-8.", 415)
            self.connection.settimeout(5)
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError("Incomplete body")
            def invalid_constant(_value):
                raise ValueError("Non-finite JSON")
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=invalid_constant)
            if not isinstance(value, dict) or set(value) != {"request_id","scenario","acknowledge"} or value["acknowledge"] != "SYNTHETIC_ONLY" or value["scenario"] != "delete_orders":
                raise ValueError("Invalid request shape")
            run, replayed = self.server.service.enqueue(value["request_id"], value["scenario"])
            self._reply(200 if replayed else 202, run)
        except StoreError as error:
            self.close_connection = True
            self._error(error.status, error.code, str(error))
        except (ValueError, UnicodeError, TimeoutError, OSError, RecursionError):
            self.close_connection = True
            self._error(400, "invalid_json", "Isi JSON tidak valid atau belum lengkap.")
        except sqlite3.Error:
            self.close_connection = True
            self._error(503, "storage_unavailable", "Penyimpanan bukti belum tersedia. Permintaan ini belum dinyatakan berhasil.")

    def do_OPTIONS(self):
        if self._perimeter():
            self._error(405, "method_not_allowed", "Metode tidak diizinkan.")

    do_PUT = do_OPTIONS
    do_PATCH = do_OPTIONS
    do_DELETE = do_OPTIONS


def make_server(service, host="127.0.0.1", port=8140):
    if host != "127.0.0.1":
        raise ValueError("RestoreLab binds only to IPv4 loopback")
    server = Server((host, port), Handler)
    server.service, server.instance = service, uuid.uuid4().hex
    return server


def main():
    parser = argparse.ArgumentParser(description="RestoreLab local recovery workstation")
    parser.add_argument("--port", type=int, default=8140)
    parser.add_argument("--max-runs", type=int, default=5)
    arguments = parser.parse_args()
    if not 1024 <= arguments.port <= 65535:
        parser.error("port must be 1024..65535")
    root = Path(__file__).resolve().parents[1]
    service = Service(root, max_runs=arguments.max_runs)
    service.start()
    server = None
    try:
        server = make_server(service, port=arguments.port)
        def shutdown(_signal, _frame):
            threading.Thread(target=server.shutdown, daemon=True).start()
        for name in ("SIGINT", "SIGTERM"):
            signal.signal(getattr(signal,name),shutdown)
        print(f"RestoreLab: http://127.0.0.1:{arguments.port}", flush=True)
        server.serve_forever(poll_interval=0.25)
    finally:
        if server is not None:
            server.server_close()
        service.close()


if __name__ == "__main__":
    main()
