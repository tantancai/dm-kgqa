import json

import pytest

from kg import review as rv
from kg.steps import refine as rf

S = "eGFR<30 ml/min 的患者禁用二甲双胍。"


def test_definitions_come_from_approved_doc():
    d = rf.label_definitions()
    assert all(lb in d for lb in rf.LABELS) and "不推荐 / 不建议" in d


@pytest.mark.parametrize("out, problems", [
    ({"label": "CONTRAINDICATED", "condition": "eGFR<30 ml/min", "quote": "禁用二甲双胍"}, []),
    ({"label": "CONTRAINDICATED", "condition": None, "quote": "禁用二甲双胍"}, []),
    ({"label": "BANNED", "condition": None, "quote": "禁用二甲双胍"}, ["标签不在白名单"]),
    ({"label": "CONTRAINDICATED", "condition": None, "quote": "二甲双胍禁用"}, ["quote 不是原句子串"]),       # 改写过
    ({"label": "CONTRAINDICATED", "condition": "eGFR<30ml/min", "quote": "禁用二甲双胍"}, ["condition 不是原句子串"]),  # 少了空格也不行
])
def test_check(out, problems):
    assert rf.check(out, S) == problems


def test_label_edges_flags_and_sorts(monkeypatch):
    edges = [{"eid": "e1", "sid": "s1", "head": "二甲双胍", "tail": "eGFR<30"},
             {"eid": "e2", "sid": "s1", "head": "二甲双胍", "tail": "eGFR<30"}]
    outs = {"e1": {"label": "CONTRAINDICATED", "condition": None, "quote": "禁用二甲双胍"},
            "e2": {"label": "INDICATED", "condition": None, "quote": "推荐二甲双胍"}}
    seen = iter(["e1", "e2"])
    monkeypatch.setattr(rf, "WORKERS", 1)
    monkeypatch.setattr(rf.llm, "chat_json", lambda p, **_: outs[next(seen)])
    rows, stats = rf.label_edges(edges, {"s1": S}, {("Drug", "二甲双胍"): "二甲双胍"}, "定义")
    assert [r["id"] for r in rows] == ["e2", "e1"]                       # 校验失败的置顶
    assert rows[0]["needs_manual"] == "quote 不是原句子串" and stats["needs_manual"] == 1


def test_needs_manual_forces_human_even_if_cross_review_agrees(tmp_path):
    rows = [{"id": f"r{i}", "label": "INDICATED", "needs_manual": "quote 不是原句子串" if i == 0 else ""} for i in range(5)]
    rv.export("drug_relation", rows, ["label", "needs_manual"], review_dir=tmp_path)
    out = tmp_path / "o.jsonl"
    out.write_text("\n".join(json.dumps({"id": f"r{i}", "verdict": "对"}) for i in range(5)), encoding="utf-8")
    rv.merge("drug_relation", out, review_dir=tmp_path)
    got = {r["id"]: r["需你确认"] for r in rv._read(tmp_path / "drug_relation.xlsx")[1]}
    assert got["r0"] == "是"


def test_parse_fix_and_write_csv(tmp_path):
    assert rf.parse_fix("caution；妊娠期") == ("CAUTION", "妊娠期") and rf.parse_fix("MENTION") == ("MENTION", "")
    with pytest.raises(rv.ReviewError):
        rf.parse_fix("禁用")
    base = {"drug_canonical": "二甲双胍", "disease_canonical": "肾功能不全", "quote": "禁用二甲双胍", "sid": "s1"}
    decided = [{**base, "id": "e1", "label": "CONTRAINDICATED", "condition": "", "final": "对", "by": "cross_review", "改为": ""},
               {**base, "id": "e2", "label": "INDICATED", "condition": "", "final": "错", "by": "human", "改为": "CAUTION；妊娠期"},
               {**base, "id": "e3", "label": "MENTION", "condition": "", "final": "存疑", "by": "human", "改为": ""}]
    stats = rf.write_labels_csv(decided, tmp_path / "l.csv")
    lines = (tmp_path / "l.csv").read_text(encoding="utf-8-sig").splitlines()
    assert lines[1] == "e1,二甲双胍,肾功能不全,CONTRAINDICATED,,禁用二甲双胍,s1,llm,对,false"
    assert lines[2] == "e2,二甲双胍,肾功能不全,CAUTION,妊娠期,禁用二甲双胍,s1,manual,错,true"
    assert len(lines) == 3 and stats["存疑未写入"] == 1
