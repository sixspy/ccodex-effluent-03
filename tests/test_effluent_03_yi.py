"""effluent-03 红测乙：对账不得把封批影子草稿算成已封。

盯防服务：对账服务 reconcile_svc。
预置夹具造假绿：开放旁路批次 SM-OF-E1-A-002（EX-C09 情景）旁路未结，
有人把封批意图只写成影子快照草稿（discharge_seals.status='影子草稿'）。
正确性口径（乙路径）：影子草稿只能停在草稿区，原子升正才算已封；
对账页“已封灯”只认正式区 status='已封批'，且与旁路开放互斥。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import app
from db import connect, init_db, now_iso
from locks import require_lock, release

OPEN_SAMPLE_CODE = "SM-OF-E1-A-002"
PERMIT_CODE = "DP-2026-E01"
DRAFT_REF = "SL-SM-OF-E1-A-002-G2-DRAFT"


@pytest.fixture()
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "effluent03_yi.db")
    init_db()
    # 造假绿夹具：旁路仍开放，封批意图仅落影子快照草稿，正式区零改
    with connect() as conn:
        sample = conn.execute(
            "SELECT id, generation FROM sample_batches WHERE sample_code=?",
            (OPEN_SAMPLE_CODE,),
        ).fetchone()
        sample_id = int(sample["id"])
        hold = conn.execute(
            "SELECT id FROM exceed_holds WHERE sample_id=? AND status='开放'",
            (sample_id,),
        ).fetchone()
        assert hold is not None, "夹具前提：旁路挂起仍开放"
        conn.execute(
            """INSERT INTO discharge_seals(sample_id,sample_generation,snapshot,
                   seal_ref,status,sealed_at)
                   VALUES (?,?,?,?,?,?)""",
            (
                sample_id,
                int(sample["generation"]) + 1,
                "COD=58.0 NH3-N=4.10 TP=0.330",
                DRAFT_REF,
                "影子草稿",
                now_iso(),
            ),
        )
        conn.commit()
    yield


def _auth(c: TestClient, user: str = "admin", password: str = "admin123") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": password}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def test_yi_shadow_draft_not_counted_as_sealed_in_reconcile(_db):
    c = TestClient(app)
    h = _auth(c)
    recon = c.get(f"/api/reconcile/{PERMIT_CODE}", headers=h).json()

    # 影子草稿不得计入已封数 / 不得出现在正式封批清单
    assert recon["seal_count"] == 1, "影子草稿被对账算成已封"
    assert all(s["seal_ref"] != DRAFT_REF for s in recon["seals"])
    assert all(
        s["sample_code"] != OPEN_SAMPLE_CODE for s in recon["seals"]
    ), "未结旁路的影子草稿不得挂正式封批"

    # 已封灯不得由影子草稿点亮：proof_ready 只由正式封批支撑；
    # 该许可对齐本身不齐时 proof_ready 为 False，关键是草稿没有抬高封批计数
    assert recon["seal_count"] == 1
    assert all(s["seal_ref"] != DRAFT_REF for s in recon["seals"])
    # 有正式封批但对齐不齐 -> proof_ready 仅随 all_ok，不受草稿影响
    assert recon["proof_ready"] is False
    assert recon["proof_ready"] == recon["balanced"]


def test_yi_proof_sealed_lamp_mutually_exclusive_with_open_bypass(_db):
    c = TestClient(app)
    h = _auth(c)
    proof = c.get(f"/api/proof/sample/{OPEN_SAMPLE_CODE}", headers=h).json()

    # 已封灯与旁路开放互斥：即使存在影子草稿，sealed 也必须为 False
    assert proof["sealed"] is False
    open_holds = [x for x in proof["holds"] if x["status"] == "开放"]
    assert len(open_holds) == 1
    # 证明体必须显式回传旁路开放标志，供封批页/对账页同源互斥渲染
    assert proof["bypass_open"] is True
    # 草稿可追责但不得冒名正式封批
    assert all(s["status"] != "已封批" for s in proof["seals"])
    assert any(s["status"] == "影子草稿" for s in proof["seals"])


def test_yi_draft_promotion_requires_closed_bypass_else_void(_db):
    """乙路径原子升正：旁路未闭时升正请求必须拒写且草稿作废，正式区零改。"""
    c = TestClient(app)
    h = _auth(c)
    with connect() as conn:
        sample_id = int(
            conn.execute(
                "SELECT id FROM sample_batches WHERE sample_code=?",
                (OPEN_SAMPLE_CODE,),
            ).fetchone()["id"]
        )
        formal_before = conn.execute(
            "SELECT COUNT(*) c FROM discharge_seals WHERE status='已封批'"
        ).fetchone()["c"]

    resp = c.post(
        "/api/seals/promote",
        headers=h,
        json={"sample_id": sample_id},
    )
    # 升正端点必须真实存在并按业务口径拒写（400），不允许缺端点 404 蒙混
    assert resp.status_code == 400, resp.text

    with connect() as conn:
        formal_after = conn.execute(
            "SELECT COUNT(*) c FROM discharge_seals WHERE status='已封批'"
        ).fetchone()["c"]
        assert formal_after == formal_before
        row = conn.execute(
            "SELECT status FROM discharge_seals WHERE seal_ref=?", (DRAFT_REF,)
        ).fetchone()
        # 未升正：草稿被作废，绝不允许变成已封批
        assert row["status"] == "草稿作废"
        gen = conn.execute(
            "SELECT generation, status FROM sample_batches WHERE id=?",
            (sample_id,),
        ).fetchone()
        assert gen["generation"] == 1
        assert gen["status"] == "旁路挂起"
