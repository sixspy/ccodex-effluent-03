"""effluent-03 红测争用：两路会话同时对同批抢封批须占批锁，后到只记争用痕。

盯防服务：批锁 locks（封批服务持锁路径）。
场景：会话 A（writer）先占 sample 批锁且不释放，会话 B（admin）再打同批封批，
必须被 409 顶回；败方不得落任何封批正式行，只在 contention_log 留一条争用痕。
配套并发脚本：scripts/ping_seal_lock.py（双会话打同批封批）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import app
from db import connect, init_db

SEALABLE_SAMPLE_CODE = "SM-OF-E1-B-001"


@pytest.fixture()
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "effluent03_zheng.db")
    init_db()
    yield


def _auth(c: TestClient, user: str, password: str) -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": password}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _sample_id(code: str) -> int:
    with connect() as conn:
        return int(
            conn.execute(
                "SELECT id FROM sample_batches WHERE sample_code=?", (code,)
            ).fetchone()["id"]
        )


def test_zheng_concurrent_seal_same_batch_lock_serializes(_db):
    c = TestClient(app)
    ha = _auth(c, "writer", "writer123")
    hb = _auth(c, "admin", "admin123")
    sample_id = _sample_id(SEALABLE_SAMPLE_CODE)
    lock_key = f"sample:{sample_id}"

    # 会话 A 先占批锁（模拟两路会话同时抢，A 先到持有）
    acq = c.post(f"/api/locks/acquire?lock_key={lock_key}", headers=ha)
    assert acq.status_code == 200

    with connect() as conn:
        seals_before = conn.execute(
            "SELECT COUNT(*) c FROM discharge_seals"
        ).fetchone()["c"]

    # 会话 B 后到抢同批封批：必须被锁顶回
    resp = c.post("/api/seals", headers=hb, json={"sample_id": sample_id})
    assert resp.status_code == 409, resp.text

    with connect() as conn:
        # 败方零封批正式行
        seals_after = conn.execute(
            "SELECT COUNT(*) c FROM discharge_seals"
        ).fetchone()["c"]
        assert seals_after == seals_before
        # 后到只记争用痕：一条针对该批锁的争用失败记录
        traces = conn.execute(
            "SELECT * FROM contention_log WHERE lock_key=? ORDER BY id",
            (lock_key,),
        ).fetchall()
        assert len(traces) == 1
        assert traces[0]["actor"] == "admin"
        assert traces[0]["action"] == "争用失败"

    # 锁仍在 A 手里，B 没有顶替持有人
    locks = c.get("/api/locks", headers=ha).json()
    holders = [x["holder"] for x in locks["locks"] if x["lock_key"] == lock_key]
    assert holders == ["writer"]

    # A 释放后自己封批成功，证明锁是串行化而不是误杀
    c.post(f"/api/locks/release?lock_key={lock_key}", headers=ha)
    ok = c.post("/api/seals", headers=ha, json={"sample_id": sample_id})
    assert ok.status_code == 200
    assert ok.json()["generation"] == 2


def test_zheng_contention_trace_persisted_after_rollback(_db):
    """争用痕必须在请求回滚后仍落库（后到只记争用痕，不得被回滚吞掉）。"""
    c = TestClient(app)
    ha = _auth(c, "writer", "writer123")
    hb = _auth(c, "admin", "admin123")
    sample_id = _sample_id("SM-OF-N3-A-001")
    lock_key = f"sample:{sample_id}"

    c.post(f"/api/locks/acquire?lock_key={lock_key}", headers=ha)
    resp = c.post("/api/seals", headers=hb, json={"sample_id": sample_id})
    assert resp.status_code == 409

    # 走只读接口复核争用痕已持久化
    view = c.get("/api/locks", headers=ha).json()
    mine = [x for x in view["contention"] if x["lock_key"] == lock_key]
    assert len(mine) == 1 and mine[0]["action"] == "争用失败"
