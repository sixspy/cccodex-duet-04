from app.db import 连接
from app.modules.对齐窗口裁剪 import 核心 as 窗裁

def 轨规() -> dict:
    conn = 连接()
    rows = conn.execute("SELECT 键, 值 FROM 轨规").fetchall()
    conn.close()
    return {r["键"]: r["值"] for r in rows}

def 改轨规(体: dict) -> dict:
    # 改窗与跑对齐互斥：同一把窗锁 + BEGIN IMMEDIATE。
    # 窗键（对齐窗起/对齐窗止/窗外形态）变更同事务把旧裁剪行置 invalid，
    # 窗口与差表对不上时由 valid=0 可区分，重跑对齐后恢复。
    窗裁.校验窗体(体)
    with 窗裁.窗锁:
        conn = 连接()
        try:
            conn.execute("BEGIN IMMEDIATE")
            旧 = {
                r["键"]: r["值"]
                for r in conn.execute("SELECT 键, 值 FROM 轨规").fetchall()
            }
            for k, v in 体.items():
                conn.execute(
                    "INSERT INTO 轨规(键, 值) VALUES (?, ?) ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
                    (k, str(v)),
                )
            新 = {**旧, **{k: str(v) for k, v in 体.items()}}
            窗裁.校验窗序(新.get("对齐窗起", ""), 新.get("对齐窗止", ""))
            if any(k in 体 and 旧.get(k, "") != str(体[k]) for k in 窗裁.窗键):
                conn.execute("UPDATE 窗口裁剪 SET valid=0 WHERE valid<>0")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return 轨规()

def 源列表() -> list[dict]:
    conn = 连接()
    rows = conn.execute("SELECT * FROM 源登记 ORDER BY 轨别, 源键").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def 左列表(限制: int = 300) -> list[dict]:
    conn = 连接()
    rows = conn.execute("SELECT * FROM 左轨流水 ORDER BY 处理时 DESC LIMIT ?", (限制,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def 右列表(限制: int = 300) -> list[dict]:
    conn = 连接()
    rows = conn.execute("SELECT * FROM 右轨流水 ORDER BY 处理时 DESC LIMIT ?", (限制,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def 差列表() -> list[dict]:
    conn = 连接()
    rows = conn.execute("SELECT * FROM 对齐差 ORDER BY 编号 DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def 悬列表() -> list[dict]:
    conn = 连接()
    rows = conn.execute("SELECT * FROM 悬账单 ORDER BY 编号 DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def 结论列表() -> list[dict]:
    conn = 连接()
    rows = conn.execute("SELECT * FROM 对拍结论 ORDER BY 编号 DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def 概览() -> dict:
    conn = 连接()
    g = 轨规()
    out = {
        "站名": g.get("站名", "对拍"),
        "骨架": g.get("骨架", ""),
        "皮": g.get("皮", ""),
        "去皮": g.get("去皮", ""),
        "左笔数": conn.execute("SELECT COUNT(*) AS c FROM 左轨流水").fetchone()["c"],
        "右笔数": conn.execute("SELECT COUNT(*) AS c FROM 右轨流水").fetchone()["c"],
        "对齐差数": conn.execute("SELECT COUNT(*) AS c FROM 对齐差").fetchone()["c"],
        "悬账数": conn.execute("SELECT COUNT(*) AS c FROM 悬账单").fetchone()["c"],
        "结论数": conn.execute("SELECT COUNT(*) AS c FROM 对拍结论").fetchone()["c"],
        "旁路数": conn.execute("SELECT COUNT(*) AS c FROM 旁路账册").fetchone()["c"],
        "窗外标数": conn.execute("SELECT COUNT(*) AS c FROM 对齐差 WHERE 窗外=1").fetchone()["c"],
        "裁剪失效数": conn.execute("SELECT COUNT(*) AS c FROM 窗口裁剪 WHERE valid=0").fetchone()["c"],
    }
    conn.close()
    return out
