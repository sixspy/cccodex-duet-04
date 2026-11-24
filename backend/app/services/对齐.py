"""同业务键对齐：窗口裁剪编排 → 双边金额比对，落对齐差 + 悬账单（+ 旁路账册）。"""
from __future__ import annotations
from app.db import 连接
from app.seed import 此刻
from app.modules.对齐窗口裁剪 import 核心 as 窗裁


def 跑对齐() -> dict:
    # 改窗与跑对齐互斥：持窗锁 + BEGIN IMMEDIATE，不留半截、不双成功覆盖窗界。
    # 裁剪行、对齐差、悬账、旁路账册同一事务提交，同成败。
    with 窗裁.窗锁:
        conn = 连接()
        try:
            conn.execute("BEGIN IMMEDIATE")
            规 = {
                r["键"]: r["值"]
                for r in conn.execute("SELECT 键, 值 FROM 轨规").fetchall()
            }
            结果 = 窗裁.编排(conn, 规, 此刻())
            conn.commit()
            return 结果
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
