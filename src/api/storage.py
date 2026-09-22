"""Durable UI checkpoints. Core application records stay in the existing store."""
import json
from uuid import uuid4

from fastapi import HTTPException
from src.applications.store import now
from src.database import db


def initialize():
    with db.get_connection() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS web_workflows (
            id TEXT PRIMARY KEY, data TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        conn.commit()


def create(data):
    identifier = uuid4().hex
    timestamp = now()
    with db.get_connection() as conn:
        conn.execute("INSERT INTO web_workflows VALUES(?,?,0,?,?)",
                     (identifier, json.dumps(data), timestamp, timestamp))
        conn.commit()
    return get(identifier)


def decode(row):
    return {**json.loads(row["data"]), "id": row["id"], "revision": row["revision"],
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


def get(identifier):
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM web_workflows WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise HTTPException(404, "Workflow not found")
    return decode(row)


def all_workflows():
    with db.get_connection() as conn:
        return [decode(row) for row in conn.execute("SELECT * FROM web_workflows ORDER BY updated_at DESC")]


def update(identifier, changes, *, revision=None, claim=False):
    with db.get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM web_workflows WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise HTTPException(404, "Workflow not found")
        data = json.loads(row["data"])
        if revision is not None and row["revision"] != revision:
            raise HTTPException(409, "This workflow changed in another tab. Refresh before continuing.")
        if claim and data.get("status") == "running":
            raise HTTPException(409, "This workflow is already processing")
        data.update(changes)
        conn.execute("UPDATE web_workflows SET data=?,revision=revision+1,updated_at=? WHERE id=?",
                     (json.dumps(data), now(), identifier))
        conn.commit()
    return get(identifier)


def recover_interrupted():
    # A restart never retries a send. The existing send ledger resolves duplicate/uncertain sends.
    for flow in all_workflows():
        if flow.get("status") == "running":
            update(flow["id"], {"status": "error", "error":
                   "Processing stopped when the server restarted. Your edits are saved. Review and retry this step. "
                   "If this was a send, check Gmail Sent first; uncertain sends are protected against duplicates."})
