import json

import pytest
from openpyxl import load_workbook

from kg import review as rv

COLS = ["alias", "canonical"]


def _rows():
    return [{"id": f"a{i}", "alias": f"别名{i}", "canonical": f"规范{i}"} for i in range(20)]


def _set(path, row_id, **values):
    wb = load_workbook(path)
    ws = wb.active
    header = [c.value for c in ws[1]]
    for row in ws.iter_rows(min_row=2):
        if row[0].value == row_id:
            for k, v in values.items():
                row[header.index(k)].value = v
    wb.save(path)


def _merge(tmp_path, lines):
    out = tmp_path / "out.jsonl"
    out.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    return rv.merge("alias", out, review_dir=tmp_path)


def test_export_writes_xlsx_and_jsonl(tmp_path):
    path = rv.export("alias", _rows(), COLS, review_dir=tmp_path)
    ws = load_workbook(path).active
    header = [c.value for c in ws[1]]
    assert header[:3] == ["id", "alias", "canonical"] and "结论" in header and ws.max_row == 21
    assert ws.column_dimensions[ws.cell(1, header.index("_hash") + 1).column_letter].hidden
    assert len((tmp_path / "alias.jsonl").read_text(encoding="utf-8").splitlines()) == 20


def test_export_rejects_duplicate_ids(tmp_path):
    with pytest.raises(rv.ReviewError):
        rv.export("alias", _rows() + [_rows()[0]], COLS, review_dir=tmp_path)


def test_reexport_keeps_decisions_only_for_unchanged_rows(tmp_path):
    path = rv.export("alias", _rows(), COLS, review_dir=tmp_path)
    _set(path, "a0", 结论="对")
    _set(path, "a1", 结论="对")
    rows = _rows()
    rows[1]["canonical"] = "改过的规范名"
    rv.export("alias", rows, COLS, review_dir=tmp_path)
    got = {r["id"]: r["结论"] for r in rv._read(path)[1]}
    assert got["a0"] == "对" and got["a1"] == ""


def test_merge_validates_whole_file(tmp_path):
    rv.export("alias", _rows(), COLS, review_dir=tmp_path)
    with pytest.raises(rv.ReviewError):
        _merge(tmp_path, [{"id": "a0", "verdict": "对"}, {"id": "nope", "verdict": "对"}])
    with pytest.raises(rv.ReviewError):
        _merge(tmp_path, [{"id": "a0", "verdict": "可能"}])
    with pytest.raises(rv.ReviewError):
        _merge(tmp_path, [{"id": "a0", "verdict": "对"}, {"id": "a0", "verdict": "错"}])
    assert all(r["复审结论"] == "" for r in rv._read(tmp_path / "alias.xlsx")[1])  # 拒绝时不写入


def test_merge_marks_disagreements_and_samples(tmp_path):
    rv.export("alias", _rows(), COLS, review_dir=tmp_path)
    lines = [{"id": f"a{i}", "verdict": "对", "reason": ""} for i in range(1, 20)]
    lines.append({"id": "a0", "verdict": "错", "fix": "正确名", "reason": "范围不同"})
    stats = _merge(tmp_path, lines)
    rows = {r["id"]: r for r in rv._read(tmp_path / "alias.xlsx")[1]}
    assert rows["a0"]["需你确认"] == "是" and json.loads(rows["a0"]["复审改为"]) == {"fix": "正确名"}
    sampled = [i for i in range(1, 20) if rows[f"a{i}"]["需你确认"] == "是"]
    assert sampled == [i for i in range(1, 20) if rv._sampled(f"a{i}")]
    assert stats["复审错"] == 1 and stats["需你确认"] == 1 + len(sampled)


def test_decide_requires_human_where_needed(tmp_path):
    rv.export("alias", _rows(), COLS, review_dir=tmp_path)
    _merge(tmp_path, [{"id": f"a{i}", "verdict": "对" if i else "错"} for i in range(20)])
    with pytest.raises(rv.ReviewError):
        rv.decide("alias", review_dir=tmp_path)  # a0 和抽检行还没填结论


def test_decide_final_results_and_rates(tmp_path):
    path = rv.export("alias", _rows(), COLS, review_dir=tmp_path)
    _merge(tmp_path, [{"id": f"a{i}", "verdict": "对" if i else "错"} for i in range(20)])
    rows = {r["id"]: r for r in rv._read(path)[1]}
    need = [i for i, r in rows.items() if r["需你确认"] == "是"]
    _set(path, "a0", 结论="错")  # 错但没填改为
    with pytest.raises(rv.ReviewError):
        rv.decide("alias", review_dir=tmp_path)
    _set(path, "a0", 结论="错", 改为="正确名")
    for i in need:
        if i != "a0":
            _set(path, i, 结论="对")
    decided, stats = rv.decide("alias", review_dir=tmp_path)
    by = {d["id"]: (d["final"], d["by"]) for d in decided}
    assert by["a0"] == ("错", "human") and len(decided) == 20
    assert all(by[f"a{i}"] == ("对", "cross_review") for i in range(1, 20) if f"a{i}" not in need)
    assert stats["人工错"] == 1 and stats["抽检错"] == 0 and stats["抽检错误率超标"] == 0
    assert (tmp_path / "alias.decided.jsonl").exists()


def test_decide_flags_sample_error_rate(tmp_path):
    path = rv.export("alias", _rows(), COLS, review_dir=tmp_path)
    _merge(tmp_path, [{"id": f"a{i}", "verdict": "对"} for i in range(20)])
    need = [r["id"] for r in rv._read(path)[1] if r["需你确认"] == "是"]
    assert need, "测试数据里应至少抽中一行"
    for i in need:
        _set(path, i, 结论="存疑")
    _, stats = rv.decide("alias", review_dir=tmp_path)
    assert stats["抽检错误率超标"] == 1


def test_review_prompt_comes_from_approved_doc():
    for name in ("alias", "drug_relation", "diet"):
        assert rv.PLACEHOLDER in rv.review_prompt(name)


def test_cross_merges_good_batches_and_drops_bad(tmp_path, monkeypatch):
    rv.export("alias", _rows(), COLS, review_dir=tmp_path)
    monkeypatch.setattr(rv, "review_prompt", lambda name: "P " + rv.PLACEHOLDER)

    def fake(prompt, provider):
        assert provider == "kimi"
        ids = [json.loads(x)["id"] for x in prompt.split(rv.AUTO_NOTE)[0][2:].splitlines()]
        if "a10" in ids:  # 第二批漏掉一行 → 整批丢弃
            ids = ids[:-1]
        return {"results": [{"id": i, "verdict": "对"} for i in ids]}
    import kg.llm
    monkeypatch.setattr(kg.llm, "chat_json", fake)
    stats = rv.cross("alias", batch=10, review_dir=tmp_path)
    assert stats["批次成功"] == 1 and stats["批次输出id不符（丢弃）"] == 1 and stats["未复审行"] == 10
    done = [r for r in rv._read(tmp_path / "alias.xlsx")[1] if r["复审结论"]]
    assert len(done) == 10
