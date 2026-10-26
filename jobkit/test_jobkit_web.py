from __future__ import annotations

import io
import json
import os
import socket
import unittest
import urllib.error
from unittest import mock

import jobkit_config
import jobkit_web as web

USER = "u-1"
ROW = {"user_id": USER, "id": "acme-2026-09-01", "company": "Acme", "role": "PM", "applied": "2026-09-01",
       "cv": "cv_acme", "status": "applied", "last": None, "posting": "", "cv_md": "x",
       "events": [{"d": "2026-09-02", "t": "ack"}], "updated_at": "2026-10-21T10:00:00.123+00:00"}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, request, timeout=None):
        body = request.data.decode("utf-8") if request.data else None
        self.requests.append((request.get_method(), request.full_url, dict(request.header_items()),
                              json.loads(body) if body else None))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return FakeResponse(b"" if answer is None else json.dumps(answer).encode("utf-8"))


def client(*answers):
    opener = FakeOpener(*answers)
    return web.Client("https://ref.supabase.co", "sb_secret_k", opener=opener), opener


class TestClient(unittest.TestCase):
    def test_owner_id(self):
        c, opener = client([{"owner_user_id": USER}])
        self.assertEqual(c.owner_id(), USER)
        self.assertIn("/rest/v1/jobkit_instance?select=owner_user_id", opener.requests[0][1])
        self.assertEqual(opener.requests[0][2]["Apikey"], "sb_secret_k")

    def test_owner_id_missing_raises(self):
        c, _ = client([])
        with self.assertRaises(web.WebError) as ctx:
            c.owner_id()
        self.assertIn("no owner", str(ctx.exception))

    def test_pull_apps_maps_rows_and_skips_considering(self):
        c, opener = client([ROW, dict(ROW, id="b", company="Beta", status="considering")])
        docs = c.pull_apps(USER)
        self.assertEqual(list(docs), [("Acme", "PM")])
        ident, record, version = docs[("Acme", "PM")]
        self.assertEqual((ident, version), ("acme-2026-09-01", ROW["updated_at"]))
        self.assertEqual(record, {"company": "Acme", "role": "PM", "applied": "2026-09-01", "cv": "cv_acme",
                                  "status": "applied", "last": "2026-09-01", "posting": "",
                                  "events": [{"d": "2026-09-02", "t": "ack"}]})
        self.assertIn("user_id=eq.u-1", opener.requests[0][1])
        self.assertEqual(c.web_only_keys, {("Acme", "PM")})  # ROW carries cv_md
        self.assertEqual(c.hidden_keys, {("Beta", "PM"): ("b", ROW["updated_at"])})

    def test_pull_apps_tolerates_odd_dates(self):
        rows = [dict(ROW, last="2026-09-03T08:00:00+00:00",
                     events=[{"d": None, "t": "x"}, {"d": "2026-09-04T10:00:00Z", "t": "call"}, {"d": "soon", "t": "y"}]),
                dict(ROW, id="b", company="Beta", applied="not a date")]
        c, _ = client(rows)
        docs = c.pull_apps(USER)
        self.assertEqual(list(docs), [("Acme", "PM")])
        record = docs[("Acme", "PM")][1]
        self.assertEqual(record["last"], "2026-09-03")
        self.assertEqual(record["events"], [{"d": "2026-09-04", "t": "call"}])

    def test_insert_app_sends_user_and_id(self):
        c, opener = client(None)
        c.insert_app(USER, "acme-2026-09-01", {"company": "Acme"})
        method, url, _, body = opener.requests[0]
        self.assertEqual((method, body), ("POST", {"user_id": USER, "id": "acme-2026-09-01", "company": "Acme"}))

    def test_update_app_is_conditional_and_quoted(self):
        c, opener = client([ROW], [])
        self.assertTrue(c.update_app_if_unchanged(USER, "a&b", ROW["updated_at"], {"status": "ack"}))
        self.assertFalse(c.update_app_if_unchanged(USER, "a&b", ROW["updated_at"], {"status": "ack"}))
        url = opener.requests[0][1]
        self.assertIn("id=eq.a%26b", url)
        self.assertIn("updated_at=eq.2026-10-21T10%3A00%3A00.123%2B00%3A00", url)
        self.assertEqual(opener.requests[0][2]["Prefer"], "return=representation")

    def test_delete_app_is_conditional(self):
        c, opener = client([ROW], [])
        self.assertTrue(c.delete_app_if_unchanged(USER, "acme-2026-09-01", ROW["updated_at"]))
        self.assertFalse(c.delete_app_if_unchanged(USER, "acme-2026-09-01", ROW["updated_at"]))
        method, url, _, _ = opener.requests[0]
        self.assertEqual(method, "DELETE")
        self.assertIn("updated_at=eq.2026-10-21T10%3A00%3A00.123%2B00%3A00", url)

    def test_upsert_openings_adds_user(self):
        c, opener = client(None)
        c.upsert_openings(USER, [{"external_key": "k", "company": "EDP"}])
        self.assertEqual(opener.requests[0][3], [{"external_key": "k", "company": "EDP", "user_id": USER}])
        self.assertEqual(opener.requests[0][2]["Prefer"], "resolution=merge-duplicates,return=minimal")

    def test_upsert_openings_skips_empty(self):
        c, opener = client()
        c.upsert_openings(USER, [])
        self.assertEqual(opener.requests, [])

    def test_errors_become_web_error_with_short_reason(self):
        for error, reason in ((urllib.error.HTTPError("u", 401, "x", {}, io.BytesIO(b"{}")), "HTTP 401"),
                              (urllib.error.URLError("nodename nor servname"), "nodename nor servname"),
                              (socket.timeout(), "timed out")):
            c, _ = client(error)
            with self.assertRaises(web.WebError) as ctx:
                c.owner_id()
            self.assertEqual(str(ctx.exception), reason)

    def test_postgrest_message_is_shown(self):
        body = b'{"code":"PGRST204","message":"Unknown field: x","details":null}'
        c, _ = client(urllib.error.HTTPError("u", 400, "x", {}, io.BytesIO(body)))
        with self.assertRaises(web.WebError) as ctx:
            c.insert_app(USER, "a", {"x": 1})
        self.assertEqual((str(ctx.exception), ctx.exception.status), ("HTTP 400: Unknown field: x", 400))

    def test_unreadable_error_body_gives_the_status_alone(self):
        for fp in (None, io.BytesIO(b"<html>oops</html>"), io.BytesIO(b'["x"]')):
            c, _ = client(urllib.error.HTTPError("u", 500, "x", {}, fp))
            with self.assertRaises(web.WebError) as ctx:
                c.owner_id()
            self.assertEqual(str(ctx.exception), "HTTP 500")

    def test_sends_user_agent_and_records_target(self):
        c, opener = client([{"owner_user_id": USER}])
        c.owner_id()
        self.assertEqual(opener.requests[0][2]["User-agent"], "jobkit-engine/1")
        self.assertEqual(web.Client("HTTPS://Ref.Supabase.co/", "k").target, "https://ref.supabase.co")

    def test_client_from_config_without_key_raises(self):
        config = jobkit_config.Config(supabase_url="https://ref.supabase.co", supabase_env="/none/x.env")
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(web.WebError) as ctx:
                web.client_from_config(config)
        self.assertEqual(str(ctx.exception), "no secret key (set SUPABASE_SECRET_KEY)")


BASE = "https://app.example.com"
CONSIDERING = dict(ROW, id="b", company="Beta", status="considering")


def engine(*answers, url=BASE):
    opener = FakeOpener(*answers)
    return web.EngineClient(url, "tok", opener=opener), opener


class TestEngineClient(unittest.TestCase):
    def test_owner_id(self):
        c, opener = engine({"ownerId": USER})
        self.assertEqual(c.owner_id(), USER)
        method, url, headers, body = opener.requests[0]
        self.assertEqual((method, url, body), ("GET", BASE + "/api/engine/owner", None))
        self.assertEqual(headers["Authorization"], "Bearer tok")

    def test_trailing_slash_in_base_url(self):
        c, opener = engine({"ownerId": USER}, url=BASE + "/")
        c.owner_id()
        self.assertEqual(opener.requests[0][1], BASE + "/api/engine/owner")

    def test_owner_404_means_no_owner_yet(self):
        c, _ = engine(urllib.error.HTTPError("u", 404, "x", {}, io.BytesIO(b'{"error":"no owner"}')))
        with self.assertRaises(web.WebError) as ctx:
            c.owner_id()
        self.assertEqual(str(ctx.exception), "no owner yet; finish /setup in the web app first")

    def test_pull_apps_matches_client(self):
        rows = [ROW, CONSIDERING, dict(ROW, id="c", company="Gamma", cv_md=""),
                dict(ROW, id="d", company="Delta", cv_md="# tailored", last="2026-09-03T08:00:00+00:00"),
                dict(ROW, id="e", company="Eps", applied="not a date")]
        old, _ = client(rows)
        new, opener = engine({"rows": rows})
        self.assertEqual(new.pull_apps(USER), old.pull_apps(USER))
        self.assertEqual((new.web_only_keys, new.hidden_keys), (old.web_only_keys, old.hidden_keys))
        self.assertEqual(new.web_only_keys, {("Acme", "PM"), ("Delta", "PM"), ("Eps", "PM")})
        self.assertEqual(opener.requests[0][:2], ("GET", BASE + "/api/engine/apps"))

    def test_insert_app_sends_id_and_record_without_user(self):
        c, opener = engine({"inserted": True})
        self.assertIsNone(c.insert_app(USER, "acme-2026-09-01", {"company": "Acme"}))
        method, url, headers, body = opener.requests[0]
        self.assertEqual((method, url, body), ("POST", BASE + "/api/engine/apps",
                                               {"id": "acme-2026-09-01", "company": "Acme"}))
        self.assertEqual(headers["Authorization"], "Bearer tok")
        self.assertEqual(headers["Content-type"], "application/json")

    def test_insert_app_conflict_keeps_status(self):
        c, _ = engine(urllib.error.HTTPError("u", 409, "x", {}, io.BytesIO(b'{"error":"exists"}')))
        with self.assertRaises(web.WebError) as ctx:
            c.insert_app(USER, "a", {})
        self.assertEqual(ctx.exception.status, 409)

    def test_update_app_is_conditional_and_quoted(self):
        c, opener = engine({"updated": True}, {"updated": False})
        self.assertTrue(c.update_app_if_unchanged(USER, "a&b", ROW["updated_at"], {"status": "ack"}))
        self.assertFalse(c.update_app_if_unchanged(USER, "a&b", ROW["updated_at"], {"status": "ack"}))
        method, url, _, body = opener.requests[0]
        self.assertEqual(method, "PATCH")
        self.assertEqual(url, BASE + "/api/engine/apps/a%26b?updated_at=2026-10-21T10%3A00%3A00.123%2B00%3A00")
        self.assertEqual(body, {"status": "ack"})

    def test_delete_app_is_conditional(self):
        c, opener = engine({"deleted": True}, {"deleted": False})
        self.assertTrue(c.delete_app_if_unchanged(USER, "acme-2026-09-01", ROW["updated_at"]))
        self.assertFalse(c.delete_app_if_unchanged(USER, "acme-2026-09-01", ROW["updated_at"]))
        method, url, _, body = opener.requests[0]
        self.assertEqual((method, body), ("DELETE", None))
        self.assertEqual(url, BASE + "/api/engine/apps/acme-2026-09-01?updated_at=2026-10-21T10%3A00%3A00.123%2B00%3A00")

    def test_upsert_openings(self):
        c, opener = engine({"upserted": 1})
        c.upsert_openings(USER, [{"external_key": "k", "company": "EDP"}])
        method, url, _, body = opener.requests[0]
        self.assertEqual((method, url, body), ("POST", BASE + "/api/engine/openings",
                                               {"rows": [{"external_key": "k", "company": "EDP"}]}))

    def test_upsert_openings_skips_empty(self):
        c, opener = engine()
        c.upsert_openings(USER, [])
        self.assertEqual(opener.requests, [])

    def test_errors_become_web_error_with_short_reason(self):
        unauthorized = urllib.error.HTTPError("u", 401, "x", {}, io.BytesIO(b'{"error":"unauthorized"}'))
        rejected = "engine token rejected (check JOBKIT_ENGINE_TOKEN and the Worker's secret)"
        for error, reason in ((unauthorized, rejected),
                              (urllib.error.URLError("nodename nor servname"), "nodename nor servname"),
                              (socket.timeout(), "timed out")):
            c, _ = engine(error)
            with self.assertRaises(web.WebError) as ctx:
                c.owner_id()
            self.assertEqual(str(ctx.exception), reason)
        c, _ = engine(urllib.error.HTTPError("u", 401, "x", {}, io.BytesIO(b"{}")))
        with self.assertRaises(web.WebError) as ctx:
            c.owner_id()
        self.assertEqual(ctx.exception.status, 401)

    def test_worker_error_is_shown(self):
        c, _ = engine(urllib.error.HTTPError("u", 400, "x", {}, io.BytesIO(b'{"error":"Unknown field: x"}')))
        with self.assertRaises(web.WebError) as ctx:
            c.update_app_if_unchanged(USER, "a", "t1", {"x": 1})
        self.assertEqual((str(ctx.exception), ctx.exception.status), ("HTTP 400: Unknown field: x", 400))

    def test_long_error_is_cut_short(self):
        body = json.dumps({"error": "bad\n" + "x" * 500}).encode("utf-8")
        c, _ = engine(urllib.error.HTTPError("u", 400, "x", {}, io.BytesIO(body)))
        with self.assertRaises(web.WebError) as ctx:
            c.pull_apps(USER)
        self.assertTrue(str(ctx.exception).startswith("HTTP 400: bad xxx"))
        self.assertLessEqual(len(str(ctx.exception)), len("HTTP 400: ") + 200)

    def test_sends_user_agent_and_records_target(self):
        c, opener = engine({"ownerId": USER}, url="https://App.Example.com/jobs/")
        c.owner_id()
        self.assertEqual(opener.requests[0][2]["User-agent"], "jobkit-engine/1")
        self.assertEqual(c.target, "https://app.example.com/jobs")

    def test_non_json_body_is_unreadable(self):
        c = web.EngineClient(BASE, "tok", opener=lambda r, timeout=None: FakeResponse(b"<html>"))
        with self.assertRaises(web.WebError) as ctx:
            c.pull_apps(USER)
        self.assertEqual(str(ctx.exception), "unreadable response")


class TestClientFromConfig(unittest.TestCase):
    def pick(self, config, env):
        with mock.patch.dict(os.environ, env, clear=True):
            return web.client_from_config(config)

    def test_web_url_with_token_gives_engine_client(self):
        config = jobkit_config.Config(web_url=BASE, web_env="/none/x.env")
        self.assertIsInstance(self.pick(config, {"JOBKIT_ENGINE_TOKEN": "t"}), web.EngineClient)

    def test_web_url_without_token_raises(self):
        config = jobkit_config.Config(web_url=BASE, web_env="/none/x.env")
        with self.assertRaises(web.WebError) as ctx:
            self.pick(config, {})
        self.assertEqual(str(ctx.exception), "no engine token (set JOBKIT_ENGINE_TOKEN)")

    def test_supabase_only_gives_client(self):
        config = jobkit_config.Config(supabase_url="https://ref.supabase.co", supabase_env="/none/x.env")
        self.assertIsInstance(self.pick(config, {"SUPABASE_SECRET_KEY": "k"}), web.Client)

    def test_both_urls_prefer_engine(self):
        config = jobkit_config.Config(web_url=BASE, web_env="/none/x.env",
                                      supabase_url="https://ref.supabase.co", supabase_env="/none/x.env")
        self.assertIsInstance(self.pick(config, {"JOBKIT_ENGINE_TOKEN": "t", "SUPABASE_SECRET_KEY": "k"}),
                              web.EngineClient)

    def test_plain_http_web_url_is_refused(self):
        for url in ("http://jobs.example.com", "http://localhost.example.com", "ftp://jobs.example.com",
                    "jobs.example.com"):
            config = jobkit_config.Config(web_url=url, web_env="/none/x.env")
            with self.assertRaises(web.WebError) as ctx:
                self.pick(config, {"JOBKIT_ENGINE_TOKEN": "t"})
            self.assertEqual(str(ctx.exception), "web_url must start with https://")

    def test_plain_http_is_fine_on_this_machine(self):
        for url in ("http://localhost:8787", "http://127.0.0.1:8787/", "http://[::1]:8787", "HTTP://LOCALHOST"):
            config = jobkit_config.Config(web_url=url, web_env="/none/x.env")
            self.assertIsInstance(self.pick(config, {"JOBKIT_ENGINE_TOKEN": "t"}), web.EngineClient)

    def test_neither_url_raises(self):
        with self.assertRaises(web.WebError) as ctx:
            self.pick(jobkit_config.Config(), {})
        self.assertEqual(str(ctx.exception), "no web_url or supabase_url in jobkit.toml")


if __name__ == "__main__":
    unittest.main()
