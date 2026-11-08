"""SQLite 草稿持久化及事务。"""

import json
import sqlite3
from contextlib import closing

from src.domain.scheduling import ToolError, request_key


def prepare_drafts(db):
    """创建草稿表，并保留旧版本已有草稿。"""
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'drafts'"
    ).fetchone()
    if exists:
        columns = {row[1] for row in db.execute("PRAGMA table_info(drafts)")}
        if "active" not in columns:
            db.execute("ALTER TABLE drafts RENAME TO drafts_previous")
            db.execute(
                "CREATE TABLE drafts (id INTEGER PRIMARY KEY, request TEXT NOT NULL, payload TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)"
            )
            db.execute(
                "INSERT INTO drafts (id, request, payload) SELECT id, request, payload FROM drafts_previous"
            )
            db.execute("DROP TABLE drafts_previous")
    else:
        db.execute(
            "CREATE TABLE drafts (id INTEGER PRIMARY KEY, request TEXT NOT NULL, payload TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)"
        )
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS active_request ON drafts(request) WHERE active = 1"
    )


class DraftRepository:
    """通过 SQLite 保存、去重并检查草稿占位。"""

    def __init__(self, db_path):
        self.db_path = db_path

    def occupied_times(self, key):
        try:
            with closing(sqlite3.connect(self.db_path)) as db, db:
                prepare_drafts(db)
                return [
                    json.loads(payload)["suggested_time"]
                    for saved_key, payload in db.execute(
                        "SELECT request, payload FROM drafts WHERE active = 1"
                    )
                    if request_key(json.loads(saved_key)) != key
                ]
        except sqlite3.Error as exc:
            raise ToolError(str(exc)) from exc

    def save(self, key, payload, occupied_times):
        try:
            with closing(sqlite3.connect(self.db_path)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                prepare_drafts(db)
                existing = next(
                    (
                        (draft_id, stored)
                        for draft_id, saved_key, stored in db.execute(
                            "SELECT id, request, payload FROM drafts WHERE active = 1"
                        )
                        if request_key(json.loads(saved_key)) == key
                    ),
                    None,
                )
                if existing:
                    draft_id, stored = existing
                    return {**json.loads(stored), "draft_id": draft_id, "draft_status": "saved"}
                occupied = set(occupied_times)
                occupied.update(
                    json.loads(row[0])["suggested_time"]
                    for row in db.execute("SELECT payload FROM drafts WHERE active = 1")
                )
                if payload["suggested_time"] in occupied:
                    raise ToolError("建议时段已占用，请重新查询")
                db.execute(
                    "INSERT OR IGNORE INTO drafts (request, payload) VALUES (?, ?)",
                    (key, json.dumps(payload, ensure_ascii=False)),
                )
                draft_id, stored = db.execute(
                    "SELECT id, payload FROM drafts WHERE request = ? AND active = 1", (key,)
                ).fetchone()
            return {**json.loads(stored), "draft_id": draft_id, "draft_status": "saved"}
        except sqlite3.Error as exc:
            raise ToolError(str(exc)) from exc


def confirm_draft(db_path, draft_id):
    """确认用户明确同意的待确认草稿。"""
    try:
        with closing(sqlite3.connect(db_path)) as db, db:
            prepare_drafts(db)
            row = db.execute(
                "SELECT payload FROM drafts WHERE id = ? AND active = 1", (draft_id,)
            ).fetchone()
            if not row:
                raise ToolError("草稿不存在")
            payload = json.loads(row[0])
            if payload.get("confirmation_status") != "pending":
                raise ToolError("草稿不是待确认状态")
            payload["confirmation_status"] = "confirmed"
            db.execute(
                "UPDATE drafts SET payload = ? WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), draft_id),
            )
            return {**payload, "draft_id": draft_id, "draft_status": "saved"}
    except sqlite3.Error as exc:
        raise ToolError(str(exc)) from exc


def cancel_draft(db_path, draft_id):
    """取消有效草稿并释放其时段。"""
    try:
        with closing(sqlite3.connect(db_path)) as db, db:
            prepare_drafts(db)
            row = db.execute(
                "SELECT payload FROM drafts WHERE id = ? AND active = 1", (draft_id,)
            ).fetchone()
            if not row:
                raise ToolError("有效草稿不存在")
            payload = json.loads(row[0])
            db.execute("UPDATE drafts SET active = 0 WHERE id = ?", (draft_id,))
            return {**payload, "draft_id": draft_id, "draft_status": "cancelled"}
    except sqlite3.Error as exc:
        raise ToolError(str(exc)) from exc
