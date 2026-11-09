"""Durable media operations. A committed submission claim always precedes HTTP."""
import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from uuid import uuid4, UUID

from app.storage import data_path


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier(value):
    return str(UUID(value))


@contextmanager
def database():
    with closing(sqlite3.connect(data_path("media.db"), timeout=10)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise


def rows(db):
    return [json.loads(row[0]) for row in db.execute("SELECT body FROM jobs ORDER BY rowid DESC")]


def save(db, job):
    job["updated_at"] = now()
    db.execute("INSERT OR REPLACE INTO jobs VALUES (?, ?)", (job["id"], json.dumps(job)))
    return job


def create(kind, prompt, **fields):
    job = dict(id=str(uuid4()), kind=kind, prompt=prompt, state="review", progress=0,
               created_at=now(), updated_at=now(), message="", provider_id=None,
               cost_cents=0, reserved=False, output=False, **fields)
    with database() as db:
        save(db, job)
    return job


def get(job_id, db=None):
    job_id = identifier(job_id)
    if db is None:
        with database() as connection:
            return get(job_id, connection)
    row = db.execute("SELECT body FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise ValueError("Media job was not found.")
    return json.loads(row[0])


def update(job_id, **changes):
    with database() as db:
        job = get(job_id, db)
        job.update(changes)
        return save(db, job)


def listing():
    with database() as db:
        return rows(db)


def claim(job_id):
    with database() as db:
        job = get(job_id, db)
        if job["state"] != "review":
            return job, False
        month = now()[:7]
        reserved = sum(row["cost_cents"] for row in rows(db)
                       if row["reserved"] and row.get("submitted_at", "").startswith(month))
        if reserved + job["cost_cents"] > 5000:
            raise ValueError("The $50 monthly LocalMind media allowance is reserved. Check provider billing before continuing.")
        job.update(state="submitting", reserved=True, submitted_at=now(),
                   message="Sending this reviewed request once.")
        save(db, job)
        return job, True


def folder(job_id):
    path = data_path("media") / identifier(job_id)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path
