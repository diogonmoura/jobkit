"""Two-way sync between applications.md and the tracker artifact's database.

The artifact database can only be read and written by a Claude session (the
ArtifactData tool), so this module does the merge and leaves the transport to
the caller:

    1. Claude exports the `apps` collection to a directory (out_dir) and notes
       each document's version from the listing in a {id: version} JSON file.
    2. `jobkit sync sync --db-dir DIR --push-dir OUT --versions F` merges, rewrites
       applications.md in place, and writes the documents the database still
       needs into OUT, printing a ready-to-send ArtifactData batch.
    3. Claude sends that batch, then runs `jobkit sync commit`.

State from the last successful sync lives in .jobkit-sync-state.json. The
merge is three-way against it: a field changed on only one side takes that
side's value; changed on both, the side with the later Last contact wins and
ties go to applications.md. State is only committed after the push succeeds,
so a failed push is simply retried on the next run.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

import jobkit_paths
import jobtrack

Key = Tuple[str, str]
STATE_NAME = ".jobkit-sync-state.json"
COLLECTION = "apps"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def doc_id(company: str, applied: str) -> str:
    return "{}-{}".format(slug(company), applied)


def key_str(key: Key) -> str:
    return key[0] + "\x1f" + key[1]


def _norm_posting(value: str) -> str:
    return "" if value in ("", jobtrack.PLACEHOLDER) else value


def md_record(app: jobtrack.Application) -> dict:
    return {
        "company": app.company,
        "role": app.role,
        "applied": app.applied.isoformat(),
        "cv": "" if app.cv == jobtrack.PLACEHOLDER else app.cv,
        "status": app.status,
        "last": app.last_contact.isoformat(),
        "posting": _norm_posting(app.posting),
        "events": [{"d": e.date.isoformat(), "t": e.text} for e in app.events],
    }


def load_db_dir(path: str, versions: Dict[str, int]) -> Dict[Key, Tuple[str, dict, Optional[int]]]:
    """Read exported documents: key -> (doc id, record, version)."""
    out: Dict[Key, Tuple[str, dict, Optional[int]]] = {}
    for file in sorted(glob.glob(os.path.join(path, "**", "*.json"), recursive=True)):
        with open(file, encoding="utf-8") as handle:
            raw = json.load(handle)
        data = raw.get("data", raw) if isinstance(raw, dict) else None
        if not isinstance(data, dict) or "company" not in data:
            continue
        ident = raw.get("id") or os.path.splitext(os.path.basename(file))[0]
        version = versions.get(ident)
        record = {
            "company": data.get("company", ""),
            "role": data.get("role", ""),
            "applied": data.get("applied", ""),
            "cv": data.get("cv", ""),
            "status": data.get("status", "applied"),
            "last": data.get("last") or data.get("applied", ""),
            "posting": _norm_posting(data.get("posting", "")),
            "events": [
                {"d": e["d"], "t": e["t"]}
                for e in data.get("events", [])
                if isinstance(e, dict) and "d" in e and "t" in e
            ],
        }
        out[(record["company"], record["role"])] = (ident, record, version)
    return out


def _event_set(events: List[dict]) -> set:
    return {(e["d"], e["t"]) for e in events}


def merge_records(base: Optional[dict], md: dict, db: dict) -> dict:
    """Three-way merge of one application. `base` is None when never synced."""
    merged = dict(md)

    def pick(field: str) -> str:
        b = base.get(field) if base else None
        md_changed = base is not None and md[field] != b
        db_changed = base is not None and db[field] != b
        if md[field] == db[field]:
            return md[field]
        if base is not None and db_changed and not md_changed:
            return db[field]
        if base is not None and md_changed and not db_changed:
            return md[field]
        return db[field] if db["last"] > md["last"] else md[field]

    for field in ("status", "cv", "posting"):
        merged[field] = pick(field)
    merged["last"] = max(md["last"], db["last"])
    seen = _event_set(md["events"])
    events = list(md["events"])
    for e in db["events"]:
        if (e["d"], e["t"]) not in seen:
            events.append(e)
            seen.add((e["d"], e["t"]))
    merged["events"] = events
    return merged


def apply_to_markdown(text: str, md_apps: Dict[Key, jobtrack.Application],
                      merged: Dict[Key, dict]) -> str:
    """Bring applications.md in line with the merged records."""
    for key, rec in merged.items():
        company, role = key
        app = md_apps.get(key)
        if app is None:
            new = jobtrack.Application(
                company=company, role=role,
                applied=datetime.date.fromisoformat(rec["applied"]),
                cv=rec["cv"], status=rec["status"],
                last_contact=datetime.date.fromisoformat(rec["last"]),
                posting=rec["posting"],
            )
            text = jobtrack.append_application(text, new)
            for e in rec["events"]:
                text = jobtrack.append_event(
                    text, company, role, datetime.date.fromisoformat(e["d"]), e["t"])
            continue
        if rec["status"] != app.status:
            text = jobtrack.set_status(text, company, role, rec["status"])
        # CV and Posting are merged fields too. Leaving them out of the file made the next sync read the
        # stale cell as a local edit and push it back over the database's newer value.
        md_now = md_record(app)
        for field, column in (("cv", jobtrack.COL_CV), ("posting", jobtrack.COL_POSTING)):
            value = rec[field]
            if value != md_now[field] and "|" not in value and "\n" not in value:
                text = jobtrack._set_cell(text, company, role, column, value or jobtrack.PLACEHOLDER)
        have = {(e.date.isoformat(), e.text) for e in app.events}
        for e in rec["events"]:
            if (e["d"], e["t"]) not in have:
                text = jobtrack.append_event(
                    text, company, role, datetime.date.fromisoformat(e["d"]), e["t"])
        if rec["last"] > app.last_contact.isoformat():
            text = jobtrack._set_cell(text, company, role,
                                      jobtrack.COL_LAST_CONTACT, rec["last"])
    return text


def _merge(md_path: str, db_docs, state: Dict[str, dict]) -> Tuple[str, str, Dict[Key, dict]]:
    """Merge applications.md with the database side: (old text, new text, merged records). Writes nothing."""
    text = jobtrack.read_log(md_path)
    md_apps = {a.key: a for a in jobtrack.parse_log(text)}
    md_recs = {k: md_record(a) for k, a in md_apps.items()}
    merged: Dict[Key, dict] = {}
    for key in sorted(set(md_recs) | set(db_docs)):
        base = state.get(key_str(key))
        if key in md_recs and key in db_docs:
            merged[key] = merge_records(base, md_recs[key], db_docs[key][1])
        elif key in md_recs:
            merged[key] = md_recs[key]
        else:
            merged[key] = db_docs[key][1]
    return text, apply_to_markdown(text, md_apps, merged), merged


def run_sync(md_path: str, db_dir: str, state_path: str, push_dir: str,
             versions: Optional[Dict[str, int]] = None) -> dict:
    db_docs = load_db_dir(db_dir, versions or {})
    state: Dict[str, dict] = {}
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as handle:
            state = json.load(handle)
    text, new_text, merged = _merge(md_path, db_docs, state)
    md_changed = new_text != text
    if md_changed:
        jobtrack.write_log_atomic(md_path, new_text)

    os.makedirs(push_dir, exist_ok=True)
    writes = []
    for key, rec in merged.items():
        existing = db_docs.get(key)
        if existing and existing[1] == rec:
            continue
        ident = existing[0] if existing else doc_id(rec["company"], rec["applied"])
        path = os.path.join(push_dir, ident + ".json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(rec, handle)
        entry = {"op": "set", "collection": COLLECTION, "doc_id": ident, "file_path": path}
        if existing and existing[2] is not None:
            entry["if_version"] = existing[2]
        elif existing:
            entry["needs_version"] = True
        writes.append(entry)

    pending = {key_str(k): r for k, r in merged.items()}
    with open(state_path + ".pending", "w", encoding="utf-8") as handle:
        json.dump(pending, handle, indent=1, sort_keys=True)
    return {"md_changed": md_changed, "writes": writes, "records": len(merged)}


def sync_web(client, user_id: str, md_path: str, state_path: str, attempts: int = 3) -> dict:
    """Two-way sync of applications.md with the web app's jobkit_apps.

    Each machine (a laptop, a scheduled GitHub Action) merges against its own snapshot of what it last
    synced, in a file next to applications.md. A shared snapshot would make a copy that is merely behind
    look like an edit back to the old value. A runner without a disk commits the snapshot together with
    applications.md, so if that commit is lost both go back together and nothing reads as an edit.

    Each attempt pulls, merges and pushes what changed; an update that matches no row means the web app
    edited it after the pull, so the attempt starts over instead of overwriting it. applications.md and
    the snapshot are written only once every push landed.
    """
    import jobkit_web

    state: Dict[str, dict] = {}
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as handle:
            state = json.load(handle)
    for _ in range(attempts):
        db_docs = client.pull_apps(user_id)
        text, new_text, merged = _merge(md_path, db_docs, state)
        pushed, clean = 0, True
        for key, rec in merged.items():
            existing = db_docs.get(key)
            if existing is None:
                client.insert_app(user_id, doc_id(rec["company"], rec["applied"]), rec)
            elif existing[1] == rec:
                continue
            elif not client.update_app_if_unchanged(user_id, existing[0], existing[2], rec):
                clean = False
                break
            pushed += 1
        if clean:
            break
    else:
        raise jobkit_web.WebError("conflicting edits, try again")
    md_changed = new_text != text
    if md_changed:
        jobtrack.write_log_atomic(md_path, new_text)
    jobtrack.write_log_atomic(state_path, json.dumps({key_str(k): r for k, r in merged.items()},
                                                     indent=1, sort_keys=True))
    return {"md_changed": md_changed, "pushed": pushed, "records": len(merged)}


FOLLOWING = ("following", "all")


def build_following(watchlist_path: str, state_path: str, follow_path: str,
                    today: datetime.date) -> dict:
    """The dashboard's Following panel, as one document."""
    import jobtrack_watch

    watchlist = jobtrack_watch.parse_watchlist(jobtrack.read_log(watchlist_path))
    rows = jobtrack_watch.following_records(
        watchlist, jobtrack_watch.load_state(state_path), jobtrack_watch.load_follows(follow_path)
    )
    return {"updated": today.isoformat(), "companies": rows}


def following_write(doc: dict, push_dir: str, current: Optional[dict],
                    version: Optional[int]) -> Optional[dict]:
    """The batch entry that publishes `doc`, or None when the database already has it.

    `updated` is ignored in the comparison so an unchanged list is not
    rewritten every day just because the date moved.
    """
    if current is not None and current.get("companies") == doc["companies"]:
        return None
    os.makedirs(push_dir, exist_ok=True)
    path = os.path.join(push_dir, "following-all.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(doc, handle)
    entry = {"op": "set", "collection": FOLLOWING[0], "doc_id": FOLLOWING[1], "file_path": path}
    if version is not None:
        entry["if_version"] = version
    return entry


OFFLINE_WARNING = "warning: could not sync with the web app ({}); using local applications.md"


def web_state_path(machine: str) -> str:
    """This machine's sync snapshot, next to applications.md."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", machine).strip("-") or "default"
    return jobkit_paths.data_file(".jobkit-web-sync-{}.json".format(safe))


def run_web_sync(md_path: Optional[str] = None) -> dict:
    """`jobkit sync web`: sync this machine's applications.md with the web app. Raises WebError."""
    import jobkit_config
    import jobkit_web

    config = jobkit_config.load()
    client = jobkit_web.client_from_config(config)
    return sync_web(client, client.owner_id(), md_path or jobkit_paths.data_file("applications.md"),
                    web_state_path(jobkit_config.sync_machine(config)))


def auto_sync_web() -> bool:
    """Sync around a tracker command when the web app is the backend. Never raises.

    Offline, a missing key or a broken config turn into one warning line and the command carries on with
    the local file. Returns False only when a sync was due and failed.
    """
    import jobkit_config
    import jobkit_web

    try:
        if jobkit_config.load().tracker_backend != "web":
            return True
        run_web_sync()
    except (jobkit_web.WebError, jobkit_config.ConfigError, OSError, ValueError) as error:
        print(OFFLINE_WARNING.format(error), file=sys.stderr)
        return False
    return True


def commit(state_path: str) -> bool:
    pending = state_path + ".pending"
    if not os.path.exists(pending):
        return False
    os.replace(pending, state_path)
    return True


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="jobkit sync")
    parser.add_argument("--file", default=jobkit_paths.data_file("applications.md"))
    parser.add_argument("--state", default=jobkit_paths.state_file(STATE_NAME))
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync")
    s.add_argument("--db-dir", required=True)
    s.add_argument("--push-dir", required=True)
    s.add_argument("--versions", help="JSON file mapping doc id to its version")
    sub.add_parser("commit")
    sub.add_parser("web", help="sync applications.md with the web app (backend = \"web\")")
    f = sub.add_parser("following", help="print the write that refreshes the Following panel")
    f.add_argument("--push-dir", required=True)
    f.add_argument("--current", help="exported following/all document, if it exists")
    f.add_argument("--version", type=int, help="its version from the export")
    args = parser.parse_args(argv)
    if args.cmd == "sync":
        versions = {}
        if args.versions:
            with open(args.versions, encoding="utf-8") as handle:
                versions = json.load(handle)
        result = run_sync(args.file, args.db_dir, args.state, args.push_dir, versions)
        print(json.dumps(result))
        return 0
    if args.cmd == "web":
        import jobkit_web

        try:
            result = run_web_sync(args.file)
        except jobkit_web.WebError as error:
            print("error: {}".format(error), file=sys.stderr)
            return 1
        print(json.dumps(result))
        return 0
    if args.cmd == "following":
        import jobtrack_watch

        doc = build_following(
            jobtrack_watch.default_watchlist_path(), jobtrack_watch.default_state_path(),
            jobtrack_watch.default_follow_path(), datetime.date.today(),
        )
        current = None
        if args.current and os.path.exists(args.current):
            with open(args.current, encoding="utf-8") as handle:
                raw = json.load(handle)
            current = raw.get("data", raw) if isinstance(raw, dict) else None
        write = following_write(doc, args.push_dir, current, args.version)
        print(json.dumps({"writes": [write] if write else [], "companies": len(doc["companies"])}))
        return 0
    print("committed" if commit(args.state) else "nothing pending")
    return 0


if __name__ == "__main__":
    sys.exit(main())
