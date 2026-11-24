import os
from pathlib import Path
_tmp = Path(__file__).resolve().parent / "_tmp"
_tmp.mkdir(exist_ok=True)
os.environ["数据目录"] = str(_tmp)
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")

import threading

import pytest

from app import seed
from app.services import 左装载, 右装载, 对齐, 结论, 目录
from app.modules.对齐窗口裁剪 import 核心 as 窗裁

窗起 = "2026-11-23T00:00:00Z"
窗止 = "2026-11-23T23:59:59Z"


def setup_function():
    db = _tmp / "对拍.db"
    if db.exists():
        db.unlink()
    seed.初始化库()


def 灌双轨():
    左装载.装一批({"事件": [
        {"事件编号": "L1", "业务键": "K内双", "金额分": 100, "源键": "渠甲", "事件时": "2026-11-23T10:00:00Z"},
        {"事件编号": "L2", "业务键": "K外左", "金额分": 200, "源键": "渠甲", "事件时": "2026-11-20T10:00:00Z"},
        {"事件编号": "L3", "业务键": "K内不符", "金额分": 300, "源键": "渠甲", "事件时": "2026-11-23T11:00:00Z"},
    ]})
    右装载.装一批({"事件": [
        {"事件编号": "R1", "业务键": "K内双", "金额分": 100, "源键": "核乙", "事件时": "2026-11-23T10:00:01Z"},
        {"事件编号": "R2", "业务键": "K内不符", "金额分": 250, "源键": "核乙", "事件时": "2026-11-23T11:00:01Z"},
        {"事件编号": "R3", "业务键": "K外右", "金额分": 50, "源键": "核乙", "事件时": "2026-11-20T10:00:02Z"},
    ]})


def 设窗(形态="旁路"):
    目录.改轨规({"对齐窗起": 窗起, "对齐窗止": 窗止, "窗外形态": 形态})


def 差按键():
    out = {}
    for r in 目录.差列表():
        out.setdefault(r["业务键"], []).append(r)
    return out


def test_旁路形态_窗外进旁路账册不进现行差():
    设窗("旁路")
    灌双轨()
    r = 对齐.跑对齐()
    assert r["形态"] == "旁路"
    assert r["纳入"] == 4          # L1/L3/R1/R2 在窗内
    assert r["旁路"] == 2          # L2/R3 窗外
    assert r["窗外标"] == 0
    差 = 差按键()
    assert [x["差型"] for x in 差["K内双"]] == ["对齐"]
    assert [x["差型"] for x in 差["K内不符"]] == ["金额不符"]
    assert "K外左" not in 差        # 窗外仅左：旁路形态无现行差
    assert "K外右" not in 差
    assert all(x["窗外"] == 0 for rows in 差.values() for x in rows)
    旁 = 窗裁.旁路列表()
    assert {(p["业务键"], p["轨别"], p["事件编号"]) for p in 旁} == {("K外左", "左", "L2"), ("K外右", "右", "R3")}
    assert all(p["窗起"] == 窗起 and p["窗止"] == 窗止 for p in 旁)
    assert 目录.悬列表() == []      # 窗内双边皆齐，无悬账
    g = 结论.跑一次()
    assert g["是否通过"] is True


def test_打标形态_窗外仍写差并打窗外标():
    设窗("打标")
    灌双轨()
    r = 对齐.跑对齐()
    assert r["形态"] == "打标"
    assert r["旁路"] == 0
    assert r["窗外标"] == 2
    assert 窗裁.旁路列表() == []    # 打标形态不写旁路账册
    差 = 差按键()
    # 窗外仅左：打标形态有窗外差行，且与窗内行可区分
    assert [(x["差型"], x["窗外"]) for x in 差["K外左"]] == [("右缺", 1)]
    assert [(x["差型"], x["窗外"]) for x in 差["K外右"]] == [("左缺", 1)]
    assert all(x["窗外"] == 0 for x in 差["K内双"] + 差["K内不符"])
    g = 结论.跑一次()
    assert g["是否通过"] is True


def test_窗外键在现行表是否出现可按形态区分():
    灌双轨()
    设窗("旁路")
    对齐.跑对齐()
    assert "K外左" not in 差按键()
    设窗("打标")
    对齐.跑对齐()
    行 = 差按键()["K外左"]
    assert len(行) == 1 and 行[0]["窗外"] == 1


def test_裁剪只读接口字段():
    设窗("旁路")
    灌双轨()
    对齐.跑对齐()
    rows = 窗裁.裁剪列表()
    assert len(rows) == 6
    for r in rows:
        for k in ("业务键", "事件时", "窗起", "窗止", "命运", "valid"):
            assert k in r
    命运 = {(r["事件编号"], r["命运"]) for r in rows}
    assert ("L2", "旁路") in 命运 and ("L1", "纳入") in 命运
    assert all(r["valid"] == 1 for r in rows)


def test_改窗使旧裁剪失效_重跑恢复():
    设窗("旁路")
    灌双轨()
    对齐.跑对齐()
    assert 窗裁.失效数() == 0
    目录.改轨规({"对齐窗止": "2026-11-24T00:00:00Z"})
    assert 窗裁.失效数() == 6
    g = 结论.跑一次()
    assert g["是否通过"] is False   # 窗口与差表对不上，结论贯验拦截
    assert any("失效" in p for p in g["问题"])
    对齐.跑对齐()
    assert 窗裁.失效数() == 0
    assert 结论.跑一次()["是否通过"] is True


def test_改轨规不动窗键不使失效():
    设窗("旁路")
    灌双轨()
    对齐.跑对齐()
    目录.改轨规({"金额容差分": "5"})
    assert 窗裁.失效数() == 0
    目录.改轨规({"对齐窗起": 窗起, "对齐窗止": 窗止, "窗外形态": "旁路"})  # 同值重写
    assert 窗裁.失效数() == 0


def test_改窗校验():
    with pytest.raises(ValueError):
        目录.改轨规({"对齐窗起": "不是时间"})
    with pytest.raises(ValueError):
        目录.改轨规({"窗外形态": "别的"})
    with pytest.raises(ValueError):
        目录.改轨规({"对齐窗起": "2026-11-24T00:00:00Z", "对齐窗止": "2026-11-23T00:00:00Z"})


def test_裁剪写差旁路同成败(monkeypatch):
    设窗("旁路")
    灌双轨()
    原编排 = 窗裁.编排

    def 炸(conn, 规, 时刻):
        原编排(conn, 规, 时刻)
        raise RuntimeError("模拟中途失败")

    monkeypatch.setattr(窗裁, "编排", 炸)
    with pytest.raises(RuntimeError):
        对齐.跑对齐()
    # 整体回滚：差、旁路、裁剪行都不留半截
    assert 目录.差列表() == []
    assert 目录.悬列表() == []
    assert 窗裁.旁路列表() == []
    assert 窗裁.裁剪列表() == []
    monkeypatch.undo()
    r = 对齐.跑对齐()               # 失败后可重跑，不留残态
    assert r["旁路"] == 2


def test_改窗与跑对齐并发不双落():
    灌双轨()
    目录.改轨规({"对齐窗起": 窗起, "对齐窗止": 窗止})
    错 = []

    def 跑():
        for _ in range(6):
            try:
                对齐.跑对齐()
            except Exception as e:  # noqa: BLE001
                错.append(e)

    def 改():
        for i in range(6):
            try:
                目录.改轨规({"窗外形态": "打标" if i % 2 else "旁路"})
            except Exception as e:  # noqa: BLE001
                错.append(e)

    线程 = [threading.Thread(target=跑), threading.Thread(target=改)]
    for t in 线程:
        t.start()
    for t in 线程:
        t.join()
    assert not 错
    # 不得旁路与现行差双落：两形态产物不同时非空
    旁路数 = len(窗裁.旁路列表())
    窗外标数 = len([r for r in 目录.差列表() if r["窗外"] == 1])
    assert not (旁路数 > 0 and 窗外标数 > 0)
    if 窗裁.失效数() == 0:
        # 末次为跑对齐：账面与当前形态一致
        if 目录.轨规().get("窗外形态") == "旁路":
            assert 旁路数 == 2 and 窗外标数 == 0
        else:
            assert 旁路数 == 0 and 窗外标数 == 2
    else:
        # 末次为改窗：旧裁剪行失效可区分，结论贯验拦截
        g = 结论.跑一次()
        assert g["是否通过"] is False
        assert any("失效" in p for p in g["问题"])


def test_未配窗默认口径不变():
    灌双轨()
    r = 对齐.跑对齐()
    assert r["窗起"] == "" and r["窗止"] == ""
    assert r["纳入"] == 6 and r["旁路"] == 0 and r["窗外标"] == 0
    差 = 差按键()
    assert [x["差型"] for x in 差["K外左"]] == ["右缺"]   # 未配窗：全部纳入，基线口径
    assert all(x["窗外"] == 0 for rows in 差.values() for x in rows)
    assert len(目录.悬列表()) == 2
    assert 结论.跑一次()["是否通过"] is True


def test_轨规种子与窗规默认齐全():
    规 = 目录.轨规()
    assert 规["骨架"] and 规["皮"] and 规["去皮"]       # 窗规默认不挤掉基线种子
    assert 规["金额容差分"] == "0"
    assert 规["对齐窗起"] == "" and 规["对齐窗止"] == ""
    assert 规["窗外形态"] == "旁路"


def test_窗边界含端点():
    目录.改轨规({"对齐窗起": "2026-11-23T10:00:00Z", "对齐窗止": "2026-11-23T10:00:01Z"})
    左装载.装一批({"事件": [
        {"事件编号": "L1", "业务键": "K端", "金额分": 100, "源键": "渠甲", "事件时": "2026-11-23T10:00:00Z"},
    ]})
    右装载.装一批({"事件": [
        {"事件编号": "R1", "业务键": "K端", "金额分": 100, "源键": "核乙", "事件时": "2026-11-23T10:00:01Z"},
    ]})
    r = 对齐.跑对齐()
    assert r["纳入"] == 2 and r["旁路"] == 0   # 端点含窗内
    assert [x["差型"] for x in 差按键()["K端"]] == ["对齐"]
