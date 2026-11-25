"""排放批争用锁。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit

router = APIRouter(tags=["locks"])


def _persist(statement: str, params: tuple) -> None:
    """独立短事务落锁/争用痕，不随业务请求的回滚而丢失。"""
    with connect() as own:
        own.execute(statement, params)
        own.commit()


def require_lock(conn, lock_key: str, holder: str) -> None:
    row = conn.execute(
        "SELECT holder FROM effluent_locks WHERE lock_key=?", (lock_key,)
    ).fetchone()
    if row and row["holder"] != holder:
        # 争用痕独立提交：后到会话被顶回时，痕迹必须留库（后到只记争用痕）
        _persist(
            """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
               VALUES (?,?,?,?,?)""",
            (lock_key, holder, "争用失败", f"持有人={row['holder']}", now_iso()),
        )
        raise HTTPException(409, f"锁被占用：{lock_key} 持有人 {row['holder']}")
    if not row:
        _persist(
            "INSERT INTO effluent_locks(lock_key,holder,acquired_at) VALUES (?,?,?)",
            (lock_key, holder, now_iso()),
        )


def release(conn, lock_key: str, holder: str) -> None:
    _persist(
        "DELETE FROM effluent_locks WHERE lock_key=? AND holder=?",
        (lock_key, holder),
    )


@router.get("/api/locks")
def list_locks(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        locks = [dict(r) for r in conn.execute("SELECT * FROM effluent_locks").fetchall()]
        logs = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM contention_log ORDER BY id DESC LIMIT 50"
            ).fetchall()
        ]
        return {"locks": locks, "contention": logs}


@router.post("/api/locks/acquire")
def acquire(lock_key: str, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        require_lock(conn, lock_key, user)
        write_audit(conn, user, "lock_acquire", lock_key, {})
        conn.commit()
        return {"ok": True, "lock_key": lock_key, "holder": user}


@router.post("/api/locks/release")
def release_api(lock_key: str, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        release(conn, lock_key, user)
        write_audit(conn, user, "lock_release", lock_key, {})
        conn.commit()
        return {"ok": True}
