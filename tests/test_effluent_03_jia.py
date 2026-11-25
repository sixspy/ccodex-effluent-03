"""effluent-03 红测甲：旁路挂起仍开放时，封批写口正式快照零增并当场拒写。

盯防服务：封批服务 seal_svc。
预置夹具造假绿（EX-C09 情景）：夹具种子里 SM-OF-E1-A-002 带一条
status='开放' 的超标挂起（EX-SM-OF-E1-A-002-COD），再把批态提前置成“已裁定”，
制造“旁路仍开放、封批写口却放行”的脱节脏态——事故里正式快照就是这样落库的。
正确性口径（甲路径）：开放态与封批升代不得混用——封批请求必须被当场拒写，
discharge_seals 正式区零增、sample_batches 代次与批态零改，并留旁路禁封偏航痕。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import app
from db import connect, init_db

OPEN_SAMPLE_CODE = "SM-OF-E1-A-002"
PERMIT_CODE = "DP-2026-E01"


@pytest.fixture()
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "effluent03_jia.db")
    init_db()
    # 造假绿：旁路挂起仍开放，批态却被提前置为可封（状态与旁路标志脱节）
    with connect() as conn:
        sample_id = int(
            conn.execute(
                "SELECT id FROM sample_batches WHERE sample_code=?",
                (OPEN_SAMPLE_CODE,),
            ).fetchone()["id"]
        )
        hold = conn.execute(
            "SELECT id FROM exceed_holds WHERE sample_id=? AND status='开放'",
            (sample_id,),
        ).fetchone()
        assert hold is not None, "夹具前提：旁路挂起仍开放"
        conn.execute(
            "UPDATE sample_batches SET status='已裁定' WHERE id=?", (sample_id,)
        )
        conn.commit()
    yield


def _auth(c: TestClient, user: str = "admin", password: str = "admin123") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": password}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _open_sample_id() -> int:
    with connect() as conn:
        return int(
            conn.execute(
                "SELECT id FROM sample_batches WHERE sample_code=?",
                (OPEN_SAMPLE_CODE,),
            ).fetchone()["id"]
        )


def test_jia_open_bypass_seal_rejected_formal_snapshot_zero_growth(_db):
    c = TestClient(app)
    h = _auth(c)
    sample_id = _open_sample_id()

    with connect() as conn:
        before = conn.execute(
            "SELECT COUNT(*) c FROM discharge_seals"
        ).fetchone()["c"]
        gen_before = conn.execute(
            "SELECT generation, status FROM sample_batches WHERE id=?", (sample_id,)
        ).fetchone()

    resp = c.post("/api/seals", headers=h, json={"sample_id": sample_id})

    # 当场拒写：封批写口返回 4xx，不得升代落正式快照
    assert 400 <= resp.status_code < 500, resp.text

    with connect() as conn:
        # 正式快照区零增
        after = conn.execute(
            "SELECT COUNT(*) c FROM discharge_seals"
        ).fetchone()["c"]
        assert after == before
        # 该开放旁路批次下不得出现任何正式封批行
        leaked = conn.execute(
            "SELECT COUNT(*) c FROM discharge_seals WHERE sample_id=?",
            (sample_id,),
        ).fetchone()["c"]
        assert leaked == 0
        # 采样批次代次零改、批态保持可追责
        gen_after = conn.execute(
            "SELECT generation, status FROM sample_batches WHERE id=?", (sample_id,)
        ).fetchone()
        assert gen_after["generation"] == gen_before["generation"] == 1
        assert gen_after["status"] == gen_before["status"]
        # 旁路挂起本身不得被封批动作悄悄闭环
        still_open = conn.execute(
            "SELECT COUNT(*) c FROM exceed_holds WHERE sample_id=? AND status='开放'",
            (sample_id,),
        ).fetchone()["c"]
        assert still_open == 1


def test_jia_open_bypass_seal_records_yaw_audit_trail(_db):
    """偏离说明：拒写须记“旁路禁封偏航”并拒写，不允许静默吞掉。"""
    c = TestClient(app)
    h = _auth(c)
    sample_id = _open_sample_id()

    resp = c.post("/api/seals", headers=h, json={"sample_id": sample_id})
    assert resp.status_code == 400

    audit = c.get("/api/audit", headers=h).json()["items"]
    yaw = [
        a
        for a in audit
        if a["action"] == "seal_blocked_bypass_open"
        and OPEN_SAMPLE_CODE in (a.get("target") or "")
    ]
    assert yaw, "旁路开放仍申请封批，必须留旁路禁封偏航审计痕"
    assert "偏航" in (yaw[0].get("payload") or "")


def test_jia_native_pending_status_also_records_yaw(tmp_path, monkeypatch):
    """正常旁路批态（未篡改）下封批同样拒写并留偏航痕，不留状态校验捷径。"""
    monkeypatch.setattr("db.DB_PATH", tmp_path / "effluent03_jia_native.db")
    init_db()
    c = TestClient(app)
    h = _auth(c)
    sample_id = _open_sample_id()
    with connect() as conn:
        status = conn.execute(
            "SELECT status FROM sample_batches WHERE id=?", (sample_id,)
        ).fetchone()["status"]
    assert status == "旁路挂起"

    resp = c.post("/api/seals", headers=h, json={"sample_id": sample_id})
    assert resp.status_code == 400
    audit = c.get("/api/audit", headers=h).json()["items"]
    yaw = [
        a for a in audit if a["action"] == "seal_blocked_bypass_open"
        and OPEN_SAMPLE_CODE in (a.get("target") or "")
    ]
    assert yaw and "偏航" in yaw[0]["payload"]


def test_jia_proof_and_lineage_show_no_formal_seal_for_open_bypass(_db):
    """血缘/证明侧：未结旁路不得出现正式封批代。"""
    c = TestClient(app)
    h = _auth(c)
    sample_id = _open_sample_id()

    resp = c.post("/api/seals", headers=h, json={"sample_id": sample_id})
    assert resp.status_code == 400

    proof = c.get(f"/api/proof/sample/{OPEN_SAMPLE_CODE}", headers=h).json()
    assert proof["sealed"] is False
    assert proof["generation"] == 1
    assert all(s["status"] != "已封批" for s in proof["seals"])
    assert len([x for x in proof["holds"] if x["status"] == "开放"]) == 1

    # 对账页已封灯与旁路开放互斥（封批数不得因拒写而增加）
    recon = c.get(f"/api/reconcile/{PERMIT_CODE}", headers=h).json()
    assert all(
        s["sample_code"] != OPEN_SAMPLE_CODE for s in recon["seals"]
    )
