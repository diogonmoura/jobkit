"""The web app's Supabase, as the engine sees it: applications and postings for its scan.

Talks to Supabase's REST API (PostgREST) with the secret key from jobkit_config.supabase_secret_key, which
bypasses Row Level Security, so every query names the owner's user id itself. Stdlib only.

Application updates are conditional on the row's updated_at as last read (a trigger moves it on every
change), so an edit made in the web app between a pull and a push is never overwritten: the update matches
no row and the caller pulls again.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

import jobkit_config

Key = Tuple[str, str]
DbDocs = Dict[Key, Tuple[str, dict, Optional[str]]]
# Shortlisted jobs in the web app; not applications, so they stay out of applications.md.
WEB_ONLY_STATUSES = ("considering",)


class WebError(Exception):
    """Any failure talking to the web app's Supabase. The message is a short reason for a warning."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def _quote(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")


def _record(row: dict) -> dict:
    applied = row.get("applied") or ""
    return {
        "company": row.get("company") or "",
        "role": row.get("role") or "",
        "applied": applied,
        "cv": row.get("cv") or "",
        "status": row.get("status") or "applied",
        "last": row.get("last") or applied,
        "posting": row.get("posting") or "",
        "events": [{"d": e["d"], "t": e["t"]} for e in row.get("events") or []
                   if isinstance(e, dict) and "d" in e and "t" in e],
    }


class Client:
    def __init__(self, url: str, key: str, opener=urllib.request.urlopen, timeout: int = 20):
        self.base = url.rstrip("/") + "/rest/v1"
        self.key = key
        self.opener = opener
        self.timeout = timeout

    def _call(self, method: str, path: str, body=None, prefer: str = ""):
        headers = {"apikey": self.key, "Authorization": "Bearer " + self.key,
                   "Content-Type": "application/json", "Accept": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.opener(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            raise WebError("HTTP {}".format(error.code), status=error.code) from None
        except urllib.error.URLError as error:
            raise WebError(str(error.reason)) from None
        except (socket.timeout, TimeoutError):
            raise WebError("timed out") from None
        except OSError as error:
            raise WebError(str(error) or type(error).__name__) from None
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            raise WebError("unreadable response") from None

    def owner_id(self) -> str:
        rows = self._call("GET", "/jobkit_instance?select=owner_user_id&limit=1") or []
        owner = rows[0].get("owner_user_id") if rows else None
        if not owner:
            raise WebError("no owner yet; finish /setup in the web app first")
        return str(owner)

    def pull_apps(self, user_id: str) -> DbDocs:
        rows = self._call("GET", "/jobkit_apps?select=*&user_id=eq." + _quote(user_id)) or []
        docs: DbDocs = {}
        for row in rows:
            if row.get("status") in WEB_ONLY_STATUSES:
                continue
            record = _record(row)
            docs[(record["company"], record["role"])] = (str(row["id"]), record, row.get("updated_at"))
        return docs

    def insert_app(self, user_id: str, app_id: str, record: dict) -> None:
        self._call("POST", "/jobkit_apps", dict({"user_id": user_id, "id": app_id}, **record),
                   prefer="return=minimal")

    def delete_app_if_unchanged(self, user_id: str, app_id: str, updated_at: Optional[str]) -> bool:
        path = "/jobkit_apps?user_id=eq.{}&id=eq.{}&updated_at=eq.{}".format(
            _quote(user_id), _quote(app_id), _quote(updated_at or ""))
        return bool(self._call("DELETE", path, prefer="return=representation"))

    def update_app_if_unchanged(self, user_id: str, app_id: str, updated_at: Optional[str], record: dict) -> bool:
        path = "/jobkit_apps?user_id=eq.{}&id=eq.{}&updated_at=eq.{}".format(
            _quote(user_id), _quote(app_id), _quote(updated_at or ""))
        return bool(self._call("PATCH", path, record, prefer="return=representation"))

    def upsert_openings(self, user_id: str, rows: List[dict]) -> None:
        if not rows:
            return
        self._call("POST", "/jobkit_external_openings", [dict(row, user_id=user_id) for row in rows],
                   prefer="resolution=merge-duplicates,return=minimal")


def client_from_config(config: jobkit_config.Config) -> Client:
    key = jobkit_config.supabase_secret_key(config)
    if not key:
        raise WebError("no secret key")
    if not config.supabase_url:
        raise WebError("no supabase_url in jobkit.toml")
    return Client(config.supabase_url, key)
