from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid

from restorelab.server import make_server
from restorelab.service import Service
from tests.fakes import ControlledEngine


class HTTPTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path.cwd() / ".local" / "test-data"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="http-", dir=temporary_root)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        assets = self.root / "www"
        assets.mkdir()
        for name, content in (("index.html", "<!doctype html><title>HTTP fixture</title>"),
                              ("app.js", '"use strict";'), ("styles.css", "body{color:#111}")):
            (assets / name).write_text(content, encoding="utf-8")
        private = self.root / ".secrets"
        private.mkdir()
        (private / "password").write_text("SYNTHETIC-PRIVATE-FILE-SENTINEL", encoding="utf-8")
        self.engine = ControlledEngine()
        self.service = Service(self.root, engine=self.engine)
        self.service.start()
        self.server = make_server(self.service, host="127.0.0.1", port=0)
        self.port = self.server.server_address[1]
        self.host = f"127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.engine.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.service.close()

    def payload(self, request_id=None):
        return {"request_id": request_id or uuid.uuid4().hex,
                "scenario": "delete_orders", "acknowledge": "SYNTHETIC_ONLY"}

    def request(self, method, path, body=None, headers=None, origin=True, raw=None):
        content = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        supplied = list(headers or [])
        names = {name.lower() for name, _ in supplied}
        defaults = []
        if "host" not in names:
            defaults.append(("Host", self.host))
        if method == "POST" and origin and "origin" not in names:
            defaults.append(("Origin", f"http://{self.host}"))
        if method == "POST" and "content-type" not in names:
            defaults.append(("Content-Type", "application/json"))
        if content is not None and "content-length" not in names:
            defaults.append(("Content-Length", str(len(content))))
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.putrequest(method, path, skip_host=True)
            for name, value in defaults + supplied:
                connection.putheader(name, value)
            connection.endheaders(content)
            response = connection.getresponse()
            raw_body = response.read()
            try:
                parsed = json.loads(raw_body)
            except (ValueError, UnicodeDecodeError):
                parsed = None
            return response.status, dict(response.getheaders()), parsed, raw_body
        finally:
            connection.close()

    def terminal(self, run_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, _, run, _ = self.request("GET", f"/api/runs/{run_id}")
            self.assertEqual(status, 200)
            if run["status"] not in ("queued", "running"):
                return run
            time.sleep(0.01)
        self.fail("The controlled HTTP fixture job did not finish")

    def assert_error(self, response, expected):
        status, headers, body, raw = response
        self.assertEqual(status, expected)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertIsInstance(body.get("error", {}).get("code"), str)
        self.assertTrue(body["error"].get("message"))
        self.assertNotIn(b"Traceback", raw)
        self.assertNotIn(b"SYNTHETIC-PRIVATE-FILE-SENTINEL", raw)

    def test_health_and_repeated_state_reads_do_not_create_jobs(self):
        status, _, health, _ = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")
        first = self.request("GET", "/api/state")
        self.assertEqual(first[0], 200)
        for _ in range(3):
            self.assertEqual(self.request("GET", "/api/state")[2], first[2])
        self.assertEqual(first[2]["lab"]["used_runs"], 0)
        self.assertEqual(self.engine.calls, [])

    def test_static_allowlist_serves_only_application_assets(self):
        for path in ("/", "/app.js", "/styles.css"):
            with self.subTest(path=path):
                status, _, _, body = self.request("GET", path)
                self.assertEqual(status, 200)
                self.assertTrue(body)
        for path in ("/.secrets/password", "/.data/controller.sqlite", "/.runtime/config", "/README.md",
                     "/../.secrets/password", "/%2e%2e/.secrets/password", "/www/index.html"):
            with self.subTest(path=path):
                response = self.request("GET", path)
                self.assertIn(response[0], (400, 404))
                self.assertNotIn(b"SYNTHETIC-PRIVATE-FILE-SENTINEL", response[3])

    def test_unknown_run_and_mutation_route_have_json_not_found_errors(self):
        self.assert_error(self.request("GET", f"/api/runs/{uuid.uuid4().hex}"), 404)
        self.assert_error(self.request("POST", "/api/sql", self.payload()), 404)

    def test_malformed_absolute_request_target_has_safe_json_error(self):
        self.assert_error(self.request("GET", "http://["), 400)
        self.assertEqual(self.request("GET", "/health")[0], 200)
        self.assertEqual(self.engine.calls, [])

    def test_deep_json_below_body_limit_does_not_escape_the_handler(self):
        nested = b"[" * 1200 + b"0" + b"]" * 1200
        self.assertLess(len(nested), 4096)
        self.assert_error(self.request("POST", "/api/runs", raw=nested), 400)
        self.assertEqual(self.request("GET", "/api/state")[2]["lab"]["used_runs"], 0)
        self.assertEqual(self.engine.calls, [])

    def test_post_requires_same_origin(self):
        self.assert_error(self.request("POST", "/api/runs", self.payload(), origin=False), 403)
        for origin in ("null", "https://evil.invalid", f"http://{self.host}.evil.invalid", "http://127.0.0.1:1"):
            with self.subTest(origin=origin):
                self.assert_error(self.request("POST", "/api/runs", self.payload(), headers=[("Origin", origin)]), 403)
        self.assertEqual(self.service.state()["lab"]["used_runs"], 0)

    def test_duplicate_origin_headers_cannot_bypass_validation(self):
        self.assert_error(self.request("POST", "/api/runs", self.payload(), headers=[
            ("Origin", f"http://{self.host}"), ("Origin", "http://evil.invalid"),
        ]), 403)

    def test_host_rejects_external_names_and_wrong_bound_port(self):
        for host in ("evil.invalid", f"127.0.0.1:{self.port + 1}", f"localhost.evil.invalid:{self.port}"):
            with self.subTest(host=host):
                self.assert_error(self.request("POST", "/api/runs", self.payload(), headers=[
                    ("Host", host), ("Origin", f"http://{host}"),
                ]), 403)
        self.assertEqual(self.engine.calls, [])

    def test_localhost_host_requires_its_own_matching_origin(self):
        host = f"localhost:{self.port}"
        self.assert_error(self.request("POST", "/api/runs", self.payload(), headers=[("Host", host)]), 403)
        status, _, run, _ = self.request("POST", "/api/runs", self.payload(), headers=[
            ("Host", host), ("Origin", f"http://{host}"),
        ])
        self.assertEqual(status, 202)
        self.assertRegex(run["id"], r"^[0-9a-f]{32}$")

    def test_post_rejects_other_content_types(self):
        for content_type in ("text/plain", "application/x-www-form-urlencoded", "text/json"):
            with self.subTest(content_type=content_type):
                self.assert_error(self.request("POST", "/api/runs", self.payload(), headers=[
                    ("Content-Type", content_type),
                ]), 415)
        self.assertEqual(self.service.state()["lab"]["used_runs"], 0)

    def test_strict_json_rejects_duplicate_keys_nonfinite_and_invalid_utf8(self):
        request_id = uuid.uuid4().hex
        malformed = [
            b"{", b"[]", b"null", b"\xff\xfe",
            ('{"request_id":"%s","request_id":"%s","scenario":"delete_orders","acknowledge":"SYNTHETIC_ONLY"}' % (request_id, request_id)).encode(),
            ('{"request_id":"%s","scenario":NaN,"acknowledge":"SYNTHETIC_ONLY"}' % request_id).encode(),
            ('{"request_id":"%s","scenario":Infinity,"acknowledge":"SYNTHETIC_ONLY"}' % request_id).encode(),
        ]
        for raw in malformed:
            with self.subTest(body=raw):
                self.assert_error(self.request("POST", "/api/runs", raw=raw), 400)
        self.assertEqual(self.service.state()["lab"]["used_runs"], 0)

    def test_request_schema_rejects_extra_fields_wrong_acknowledgment_and_paths(self):
        invalid = [
            {}, dict(self.payload(), database_url="postgres://evil.invalid/private"),
            dict(self.payload(), sql="DELETE FROM orders"), dict(self.payload(), backup_path="../outside"),
            dict(self.payload(), acknowledge="yes"), dict(self.payload(), scenario="custom"),
            dict(self.payload(), request_id=str(uuid.uuid4())), dict(self.payload(), request_id="A" * 32),
            dict(self.payload(), request_id=123),
        ]
        for body in invalid:
            with self.subTest(body=body):
                self.assert_error(self.request("POST", "/api/runs", body), 400)
        self.assertEqual(self.service.state()["lab"]["used_runs"], 0)

    def test_oversized_content_length_is_rejected_before_body_read(self):
        self.assert_error(self.request("POST", "/api/runs", headers=[("Content-Length", "1000000000")]), 413)
        self.assertEqual(self.service.state()["lab"]["used_runs"], 0)

    def test_invalid_and_ambiguous_framing_is_rejected(self):
        for headers in (
            [("Content-Length", "-1")], [("Content-Length", "not-an-integer")],
            [("Content-Length", "0"), ("Content-Length", "1")],
            [("Transfer-Encoding", "chunked"), ("Content-Length", "0")],
        ):
            with self.subTest(headers=headers):
                self.assert_error(self.request("POST", "/api/runs", headers=headers), 400)

    def test_same_request_http_race_reuses_one_native_job(self):
        self.engine.release.clear()
        body = self.payload()
        barrier = threading.Barrier(2)
        def create():
            barrier.wait(timeout=3)
            return self.request("POST", "/api/runs", body)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: create(), range(2)))
        self.assertEqual(sorted(result[0] for result in results), [200, 202])
        self.assertEqual(len({result[2]["id"] for result in results}), 1)
        self.assertTrue(self.engine.started.wait(timeout=3))
        self.assertEqual(len(self.engine.calls), 1)
        self.assertEqual(self.service.state()["lab"]["used_runs"], 1)

    def test_distinct_http_request_race_cannot_start_two_active_jobs(self):
        self.engine.release.clear()
        barrier = threading.Barrier(2)
        def create():
            barrier.wait(timeout=3)
            return self.request("POST", "/api/runs", self.payload())
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: create(), range(2)))
        self.assertEqual(sorted(result[0] for result in results), [202, 409])
        self.assertTrue(self.engine.started.wait(timeout=3))
        self.assertEqual(len(self.engine.calls), 1)

    def test_report_download_is_gated_and_terminal_evidence_is_immutable(self):
        self.engine.release.clear()
        status, _, run, _ = self.request("POST", "/api/runs", self.payload())
        self.assertEqual(status, 202)
        self.assert_error(self.request("GET", f"/api/runs/{run['id']}/report"), 409)
        self.engine.release.set()
        completed = self.terminal(run["id"])
        self.assertEqual(completed["status"], "verified")
        first = self.request("GET", f"/api/runs/{run['id']}/report")
        self.assertEqual(first[0], 200)
        self.assertIn("attachment", first[1].get("Content-Disposition", ""))
        self.assertEqual(first[2], completed["report"])
        self.assertEqual(self.request("GET", f"/api/runs/{run['id']}/report")[3], first[3])
        self.assertEqual(self.request("GET", f"/api/runs/{run['id']}")[2], completed)

    def test_unexpected_worker_failure_stays_private_over_http(self):
        self.engine.error = RuntimeError("PRIVATE-DOCKER-STDERR-SENTINEL")
        _, _, run, _ = self.request("POST", "/api/runs", self.payload())
        completed = self.terminal(run["id"])
        self.assertEqual(completed["status"], "failed")
        for path in ("/api/state", f"/api/runs/{run['id']}", f"/api/runs/{run['id']}/report"):
            response = self.request("GET", path)
            self.assertNotIn(b"PRIVATE-DOCKER-STDERR-SENTINEL", response[3])
            self.assertNotIn(b"Traceback", response[3])


if __name__ == "__main__":
    unittest.main()
