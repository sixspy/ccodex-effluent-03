"""排放封批升代快照。

旁路禁封口径（当班封死，仅允许两种写法，无第三写法）：
- 甲路径：旁路挂起仍开放时，封批写口当场拒写，正式快照区 discharge_seals
  零增、代次零改，并写“旁路禁封偏航”审计痕。
- 乙路径：封批意图只进影子快照草稿（status='影子草稿'）；旁路闭合后由
  /api/seals/promote 原子升正，升正瞬间才写正式区；旁路仍开放则草稿作废
  （status='草稿作废'），正式区零改。
旁路开放态与封批升代两边不得混用；禁止只改封批页角标颜色。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from locks import release, require_lock
from models import SealIn
from validators import metric_summary, validate_sample_sealable

router = APIRouter(tags=["seal"])

DRAFT_STATUS = "影子草稿"
VOID_STATUS = "草稿作废"
FORMAL_STATUS = "已封批"


class PromoteIn(BaseModel):
    sample_id: int


def _open_hold(conn, sample_id: int):
    return conn.execute(
        "SELECT id, hold_key FROM exceed_holds WHERE sample_id=? AND status='开放'",
        (sample_id,),
    ).fetchone()


def _record_bypass_yaw(conn, user: str, sample_code: str, sample_id: int, stage: str) -> None:
    """旁路禁封偏航痕：独立短事务提交，不随业务回滚丢失。"""
    with connect() as own:
        write_audit(
            own,
            user,
            "seal_blocked_bypass_open",
            sample_code,
            {
                "reason": "旁路禁封偏航：旁路挂起仍开放，封批升代拒写",
                "stage": stage,
                "sample_id": sample_id,
            },
        )
        own.commit()


@router.get("/api/seals")
def list_seals(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT d.*, s.sample_code FROM discharge_seals d
               JOIN sample_batches s ON s.id=d.sample_id
               ORDER BY d.id DESC"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/seals")
def create_seal(body: SealIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    lock_key = f"sample:{body.sample_id}"
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            sample = conn.execute(
                "SELECT * FROM sample_batches WHERE id=?", (body.sample_id,)
            ).fetchone()
            if not sample:
                raise HTTPException(404, "采样批次不存在")
            # 甲路径前置：旁路开放态与封批升代不得混用，先于批态校验当场拒写，
            # 任何旁路开放态下的封批尝试都必须留偏航痕
            open_hold = _open_hold(conn, body.sample_id)
            if open_hold:
                _record_bypass_yaw(
                    conn, user, sample["sample_code"], body.sample_id, "seal_write"
                )
                raise HTTPException(400, "旁路挂起未闭，禁止封批升代（旁路禁封偏航已记）")
            validate_sample_sealable(sample["status"])
            metrics = json.loads(sample["metrics"] or "{}")
            gen = int(sample["generation"]) + 1
            snap = body.snapshot or metric_summary(metrics)
            seal_ref = f"SL-{sample['sample_code']}-G{gen}"
            conn.execute(
                """INSERT INTO discharge_seals(sample_id,sample_generation,snapshot,seal_ref,status,sealed_at)
                   VALUES (?,?,?,?,?,?)""",
                (body.sample_id, gen, snap, seal_ref, FORMAL_STATUS, now_iso()),
            )
            conn.execute(
                """UPDATE sample_batches SET generation=?, status=? WHERE id=?""",
                (gen, FORMAL_STATUS, body.sample_id),
            )
            write_audit(
                conn,
                user,
                "seal",
                sample["sample_code"],
                {"seal_ref": seal_ref, "generation": gen, "snapshot": snap},
            )
            conn.commit()
            return {"seal_ref": seal_ref, "generation": gen, "snapshot": snap}
        finally:
            release(conn, lock_key, user)
            conn.commit()


@router.post("/api/seals/draft")
def create_seal_draft(body: SealIn, authorization: AuthHeader = None):
    """乙路径入口：封批意图只进影子快照草稿，正式区零改。"""
    user = require_writer(authorization)
    lock_key = f"sample:{body.sample_id}"
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            sample = conn.execute(
                "SELECT * FROM sample_batches WHERE id=?", (body.sample_id,)
            ).fetchone()
            if not sample:
                raise HTTPException(404, "采样批次不存在")
            existing_draft = conn.execute(
                """SELECT id FROM discharge_seals
                   WHERE sample_id=? AND status=?""",
                (body.sample_id, DRAFT_STATUS),
            ).fetchone()
            if existing_draft:
                raise HTTPException(400, "已有影子草稿待升正，勿重复落草")
            metrics = json.loads(sample["metrics"] or "{}")
            gen = int(sample["generation"]) + 1
            snap = body.snapshot or metric_summary(metrics)
            seal_ref = f"SL-{sample['sample_code']}-G{gen}-DRAFT"
            # 草稿不升代、不改批态，仅落影子区
            conn.execute(
                """INSERT INTO discharge_seals(sample_id,sample_generation,snapshot,seal_ref,status,sealed_at)
                   VALUES (?,?,?,?,?,?)""",
                (body.sample_id, gen, snap, seal_ref, DRAFT_STATUS, now_iso()),
            )
            write_audit(
                conn,
                user,
                "seal_draft",
                sample["sample_code"],
                {"seal_ref": seal_ref, "generation": gen, "snapshot": snap},
            )
            conn.commit()
            return {
                "seal_ref": seal_ref,
                "generation": gen,
                "status": DRAFT_STATUS,
                "promoted": False,
            }
        finally:
            release(conn, lock_key, user)
            conn.commit()


@router.post("/api/seals/promote")
def promote_seal_draft(body: PromoteIn, authorization: AuthHeader = None):
    """乙路径原子升正：旁路闭合才准升正；仍开放则草稿作废，正式区零改。"""
    user = require_writer(authorization)
    lock_key = f"sample:{body.sample_id}"
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            sample = conn.execute(
                "SELECT * FROM sample_batches WHERE id=?", (body.sample_id,)
            ).fetchone()
            if not sample:
                raise HTTPException(404, "采样批次不存在")
            draft = conn.execute(
                """SELECT * FROM discharge_seals
                   WHERE sample_id=? AND status=?""",
                (body.sample_id, DRAFT_STATUS),
            ).fetchone()
            if not draft:
                raise HTTPException(404, "无影子快照草稿可升正")
            open_hold = _open_hold(conn, body.sample_id)
            if open_hold:
                # 旁路未闭：封批草稿作废，正式区零改，留偏航痕
                conn.execute(
                    "UPDATE discharge_seals SET status=? WHERE id=?",
                    (VOID_STATUS, draft["id"]),
                )
                write_audit(
                    conn,
                    user,
                    "seal_draft_void",
                    sample["sample_code"],
                    {
                        "seal_ref": draft["seal_ref"],
                        "reason": "旁路禁封偏航：开放挂起未闭，封批草稿作废",
                        "hold_key": open_hold["hold_key"],
                    },
                )
                conn.commit()
                _record_bypass_yaw(
                    conn, user, sample["sample_code"], body.sample_id, "draft_promote"
                )
                raise HTTPException(
                    400, "旁路挂起未闭，封批草稿作废（旁路禁封偏航已记）"
                )
            # 原子升正：同一事务内草稿转正式 + 升代 + 批态翻转
            gen = int(draft["sample_generation"])
            conn.execute(
                "UPDATE discharge_seals SET status=?, sealed_at=? WHERE id=?",
                (FORMAL_STATUS, now_iso(), draft["id"]),
            )
            seal_ref = f"SL-{sample['sample_code']}-G{gen}"
            conn.execute(
                "UPDATE discharge_seals SET seal_ref=? WHERE id=?",
                (seal_ref, draft["id"]),
            )
            conn.execute(
                "UPDATE sample_batches SET generation=?, status=? WHERE id=?",
                (gen, FORMAL_STATUS, body.sample_id),
            )
            write_audit(
                conn,
                user,
                "seal_promote",
                sample["sample_code"],
                {"seal_ref": seal_ref, "generation": gen, "from_draft": draft["seal_ref"]},
            )
            conn.commit()
            return {
                "seal_ref": seal_ref,
                "generation": gen,
                "status": FORMAL_STATUS,
                "promoted": True,
            }
        finally:
            release(conn, lock_key, user)
            conn.commit()
