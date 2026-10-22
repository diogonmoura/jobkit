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
        self.assertEqual(c.hidden_keys, {("Beta", "PM")})

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

    def test_client_from_config_without_key_raises(self):
        config = jobkit_config.Config(supabase_url="https://ref.supabase.co", supabase_env="/none/x.env")
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(web.WebError) as ctx:
                web.client_from_config(config)
        self.assertEqual(str(ctx.exception), "no secret key")


if __name__ == "__main__":
    unittest.main()
