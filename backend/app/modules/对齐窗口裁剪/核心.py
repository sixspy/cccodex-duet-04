"""对齐窗口裁剪：装载入库后的窗口裁剪编排。

跑对齐时按事件时相对窗起/窗止择定每条流水的命运：
  纳入      —— 事件时落在窗内（或未配窗），进现行对齐集；
  旁路      —— 窗外形态=旁路：窗外事件进旁路账册，不进对齐差现行集；
  打标窗外  —— 窗外形态=打标：窗外事件仍写对齐差并打窗外标，不写旁路账册。

硬约束：
  同成败 —— 裁剪行、对齐差、旁路账册在同一事务提交，中途失败整体回滚；
  互斥   —— 改窗（轨规窗键）与跑对齐共用 窗锁，且均 BEGIN IMMEDIATE，
            不致留半截，也不致双成功覆盖窗界；
  不双落 —— 一次运行只落一种形态产物：旁路账册与窗外标差行不同时非空。
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import timezone

from app.db import 连接
from app.services.时间 import 解析

# 改窗与跑对齐互斥：进程内锁（FastAPI 同步处理器跑在线程池，共享此锁）。
窗锁 = threading.RLock()

窗键 = ("对齐窗起", "对齐窗止", "窗外形态")
默认窗规 = {"对齐窗起": "", "对齐窗止": "", "窗外形态": "旁路"}
形态集 = ("旁路", "打标")

建窗表 = """
CREATE TABLE IF NOT EXISTS 窗口裁剪 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  业务键 TEXT NOT NULL,
  轨别 TEXT NOT NULL,
  事件编号 TEXT NOT NULL DEFAULT '',
  事件时 TEXT NOT NULL,
  窗起 TEXT NOT NULL DEFAULT '',
  窗止 TEXT NOT NULL DEFAULT '',
  命运 TEXT NOT NULL,
  valid INTEGER NOT NULL DEFAULT 1,
  时刻 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 旁路账册 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  业务键 TEXT NOT NULL,
  轨别 TEXT NOT NULL,
  事件编号 TEXT NOT NULL DEFAULT '',
  金额分 INTEGER NOT NULL DEFAULT 0,
  事件时 TEXT NOT NULL,
  窗起 TEXT NOT NULL DEFAULT '',
  窗止 TEXT NOT NULL DEFAULT '',
  原因 TEXT NOT NULL DEFAULT '窗外旁路',
  时刻 TEXT NOT NULL
);
"""


def 建表(conn: sqlite3.Connection) -> None:
    """窗口裁剪/旁路账册建表，并给对齐差补 窗外 标列（老库迁移）。"""
    conn.executescript(建窗表)
    try:
        conn.execute("ALTER TABLE 对齐差 ADD COLUMN 窗外 INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # 列已存在


def 确保窗规(conn: sqlite3.Connection) -> None:
    for k, v in 默认窗规.items():
        conn.execute(
            "INSERT INTO 轨规(键, 值) VALUES (?, ?) ON CONFLICT(键) DO NOTHING",
            (k, v),
        )


def 解析时(s: str):
    """ISO 时间 → 带时区 datetime；空串/不可解析 → None。"""
    s = (s or "").strip()
    if not s:
        return None
    try:
        dt = 解析(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def 读窗(规: dict) -> tuple[str, str, str]:
    窗起 = str(规.get("对齐窗起", "") or "").strip()
    窗止 = str(规.get("对齐窗止", "") or "").strip()
    形态 = str(规.get("窗外形态", "旁路") or "旁路").strip()
    if 形态 not in 形态集:
        形态 = "旁路"
    return 窗起, 窗止, 形态


def 在窗内(事件时: str, 起dt, 止dt) -> bool:
    """窗起/窗止含边界；未配窗或事件时不可解析 → 纳入（不裁剪）。"""
    if 起dt is None and 止dt is None:
        return True
    t = 解析时(事件时)
    if t is None:
        return True
    if 起dt is not None and t < 起dt:
        return False
    if 止dt is not None and t > 止dt:
        return False
    return True


def 校验窗体(体: dict) -> None:
    """改窗入参校验，不合法抛 ValueError（路由转 400）。"""
    for k in ("对齐窗起", "对齐窗止"):
        if k in 体:
            v = str(体[k] or "").strip()
            if v and 解析时(v) is None:
                raise ValueError(f"{k}无法解析为时间:{体[k]}")
    if "窗外形态" in 体:
        v = str(体["窗外形态"] or "").strip()
        if v not in 形态集:
            raise ValueError(f"窗外形态须为 旁路/打标:{体['窗外形态']}")


def 校验窗序(窗起: str, 窗止: str) -> None:
    起dt, 止dt = 解析时(窗起), 解析时(窗止)
    if 起dt is not None and 止dt is not None and 起dt > 止dt:
        raise ValueError("对齐窗起晚于对齐窗止")


def 编排(conn: sqlite3.Connection, 规: dict, 时刻: str) -> dict:
    """窗口裁剪编排主链路：判定命运 → 窗内对齐 → 窗外落旁路账册或打标差行。

    调用方须已持 窗锁 并开好事务；本函数只写不提交，与差/旁路同成败。
    """
    容差 = int(规.get("金额容差分", "0") or "0")
    窗起, 窗止, 形态 = 读窗(规)
    起dt, 止dt = 解析时(窗起), 解析时(窗止)

    # 清上次运行产物（基线口径：每次重算）
    conn.execute("DELETE FROM 对齐差")
    conn.execute("DELETE FROM 悬账单")
    conn.execute("DELETE FROM 旁路账册")
    conn.execute("DELETE FROM 窗口裁剪")

    内左汇总: dict[str, int] = {}
    内左代表: dict[str, str] = {}
    内右汇总: dict[str, int] = {}
    内右代表: dict[str, str] = {}
    外左: list = []
    外右: list = []
    纳入 = 0

    def 裁剪(行, 轨别: str, 内汇总: dict, 内代表: dict, 外列: list) -> None:
        nonlocal 纳入
        键 = 行["业务键"]
        if 在窗内(行["事件时"], 起dt, 止dt):
            命运 = "纳入"
            纳入 += 1
            内汇总[键] = 内汇总.get(键, 0) + int(行["金额分"])
            内代表[键] = 行["事件编号"]
        else:
            命运 = "旁路" if 形态 == "旁路" else "打标窗外"
            外列.append(行)
        conn.execute(
            """INSERT INTO 窗口裁剪(业务键,轨别,事件编号,事件时,窗起,窗止,命运,valid,时刻)
               VALUES (?,?,?,?,?,?,?,1,?)""",
            (键, 轨别, 行["事件编号"], 行["事件时"], 窗起, 窗止, 命运, 时刻),
        )

    for r in conn.execute("SELECT * FROM 左轨流水 WHERE 命运='已入左'").fetchall():
        裁剪(r, "左", 内左汇总, 内左代表, 外左)
    for r in conn.execute("SELECT * FROM 右轨流水 WHERE 命运='已入右'").fetchall():
        裁剪(r, "右", 内右汇总, 内右代表, 外右)

    # 窗内双边：对齐或不符入差；单边入差并悬账（基线口径，窗外=0）
    窗内键 = set(内左汇总) | set(内右汇总)
    对齐 = 0
    差 = 0
    悬 = 0
    for k in sorted(窗内键):
        有左 = k in 内左汇总
        有右 = k in 内右汇总
        if 有左 and 有右:
            la, ra = 内左汇总[k], 内右汇总[k]
            if abs(la - ra) <= 容差:
                conn.execute(
                    "INSERT INTO 对齐差(业务键,左金额分,右金额分,差型,说明,时刻,窗外) VALUES (?,?,?,?,?,?,0)",
                    (k, la, ra, "对齐", "双边金额在容差内", 时刻),
                )
                对齐 += 1
            else:
                conn.execute(
                    "INSERT INTO 对齐差(业务键,左金额分,右金额分,差型,说明,时刻,窗外) VALUES (?,?,?,?,?,?,0)",
                    (k, la, ra, "金额不符", f"左{la}-右{ra}", 时刻),
                )
                差 += 1
        elif 有左:
            conn.execute(
                "INSERT INTO 对齐差(业务键,左金额分,右金额分,差型,说明,时刻,窗外) VALUES (?,?,?,?,?,?,0)",
                (k, 内左汇总[k], None, "右缺", "仅左轨有键", 时刻),
            )
            conn.execute(
                "INSERT INTO 悬账单(业务键,悬边,金额分,事件编号,原因,时刻) VALUES (?,?,?,?,?,?)",
                (k, "左", 内左汇总[k], 内左代表.get(k, ""), "右轨缺键", 时刻),
            )
            悬 += 1
            差 += 1
        else:
            conn.execute(
                "INSERT INTO 对齐差(业务键,左金额分,右金额分,差型,说明,时刻,窗外) VALUES (?,?,?,?,?,?,0)",
                (k, None, 内右汇总[k], "左缺", "仅右轨有键", 时刻),
            )
            conn.execute(
                "INSERT INTO 悬账单(业务键,悬边,金额分,事件编号,原因,时刻) VALUES (?,?,?,?,?,?)",
                (k, "右", 内右汇总[k], 内右代表.get(k, ""), "左轨缺键", 时刻),
            )
            悬 += 1
            差 += 1

    # 窗外余量：旁路形态进旁路账册（无现行差）；打标形态写对齐差并打窗外标
    旁路数 = 0
    窗外标 = 0
    if 形态 == "旁路":
        for 轨别, 外列 in (("左", 外左), ("右", 外右)):
            for 行 in 外列:
                conn.execute(
                    """INSERT INTO 旁路账册(业务键,轨别,事件编号,金额分,事件时,窗起,窗止,原因,时刻)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (行["业务键"], 轨别, 行["事件编号"], int(行["金额分"]), 行["事件时"],
                     窗起, 窗止, "窗外旁路", 时刻),
                )
                旁路数 += 1
    else:
        外左汇总: dict[str, int] = {}
        外右汇总: dict[str, int] = {}
        for 行 in 外左:
            外左汇总[行["业务键"]] = 外左汇总.get(行["业务键"], 0) + int(行["金额分"])
        for 行 in 外右:
            外右汇总[行["业务键"]] = 外右汇总.get(行["业务键"], 0) + int(行["金额分"])
        for k in sorted(set(外左汇总) | set(外右汇总)):
            有左 = k in 外左汇总
            有右 = k in 外右汇总
            if 有左 and 有右:
                la, ra = 外左汇总[k], 外右汇总[k]
                if abs(la - ra) <= 容差:
                    差型, 说明 = "对齐", "窗外：双边金额在容差内"
                else:
                    差型, 说明 = "金额不符", f"窗外：左{la}-右{ra}"
                conn.execute(
                    "INSERT INTO 对齐差(业务键,左金额分,右金额分,差型,说明,时刻,窗外) VALUES (?,?,?,?,?,?,1)",
                    (k, la, ra, 差型, 说明, 时刻),
                )
            elif 有左:
                conn.execute(
                    "INSERT INTO 对齐差(业务键,左金额分,右金额分,差型,说明,时刻,窗外) VALUES (?,?,?,?,?,?,1)",
                    (k, 外左汇总[k], None, "右缺", "窗外：仅左轨有键", 时刻),
                )
            else:
                conn.execute(
                    "INSERT INTO 对齐差(业务键,左金额分,右金额分,差型,说明,时刻,窗外) VALUES (?,?,?,?,?,?,1)",
                    (k, None, 外右汇总[k], "左缺", "窗外：仅右轨有键", 时刻),
                )
            窗外标 += 1

    return {
        "键总数": len(窗内键),
        "对齐": 对齐,
        "差与悬": 差,
        "悬账": 悬,
        "容差分": 容差,
        "时刻": 时刻,
        "窗起": 窗起,
        "窗止": 窗止,
        "形态": 形态,
        "纳入": 纳入,
        "旁路": 旁路数,
        "窗外标": 窗外标,
        "窗外键数": len({r["业务键"] for r in 外左} | {r["业务键"] for r in 外右}),
    }


def 裁剪列表(限制: int = 500) -> list[dict]:
    """窗口裁剪只读接口：业务键、事件时、窗起窗止、命运、valid。"""
    conn = 连接()
    rows = conn.execute(
        "SELECT * FROM 窗口裁剪 ORDER BY 编号 DESC LIMIT ?", (限制,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def 旁路列表(限制: int = 500) -> list[dict]:
    conn = 连接()
    rows = conn.execute(
        "SELECT * FROM 旁路账册 ORDER BY 编号 DESC LIMIT ?", (限制,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def 失效数() -> int:
    conn = 连接()
    n = conn.execute("SELECT COUNT(*) AS c FROM 窗口裁剪 WHERE valid=0").fetchone()["c"]
    conn.close()
    return n
