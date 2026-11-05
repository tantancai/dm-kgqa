from collections import Counter

import pytest

from kg.steps import normalize as nz


@pytest.mark.parametrize("text, expected", [
    ("糖尿病周围神经病(diabetic peripheral neuropathy，DPN)", ("糖尿病周围神经病", ["diabetic peripheral neuropathy", "DPN"])),
    ("糖尿病周围神经病（DPN）", ("糖尿病周围神经病", ["DPN"])),                      # 全角括号
    ("二肽基肽酶-4(DPP-4)抑制剂", ("二肽基肽酶-4抑制剂", ["DPP-4抑制剂"])),            # 括号在中间
    ("西格列汀(sitagliptin,商品名捷诺维)", ("西格列汀", ["sitagliptin", "捷诺维"])),
    ("糖调节受损(IGR，即糖尿病前期)", ("糖调节受损", ["IGR", "糖尿病前期"])),
    ("肾小球硬化症(基·威氏综合征)", None),                                            # 括号内是中文说明，不拆
    ("2型糖尿病", None),
])
def test_split_paren(text, expected):
    assert nz.split_paren(text) == expected


def test_nfkc_spaces():
    assert nz.nfkc("1 型糖尿病") == "1型糖尿病" and nz.nfkc("2 hPG") == "2hPG" and nz.nfkc(" ＨｂＡ１ｃ ") == "HbA1c"
    assert nz.nfkc("diabetic peripheral neuropathy") == "diabetic peripheral neuropathy"


def test_unify_and_digit_guard():
    assert nz.unify("磺脲类药物") == "磺脲类" and nz.unify("磺脲类药") == "磺脲类" and nz.unify("二甲双胍") == "二甲双胍"
    assert nz.digit_guard("T2DM", "2型糖尿病") and not nz.digit_guard("T2DM", "糖尿病")


def test_t2dm_maps_only_to_type2(monkeypatch):
    counts = Counter({("Disease", "2型糖尿病(T2DM)"): 3, ("Disease", "T2DM"): 5, ("Disease", "糖尿病(DM)"): 2})
    rows, _, _ = nz.build_candidates(counts, {}, use_llm=False)
    got = {r["alias"]: r["canonical"] for r in rows}
    assert got["T2DM"] == "2型糖尿病" and got["DM"] == "糖尿病"


def test_abbr_with_wrong_digit_is_not_mapped():
    counts = Counter({("Disease", "糖尿病(T2DM)"): 3, ("Disease", "T2DM"): 5})  # 标注把 T2DM 挂到了"糖尿病"
    rows, _, stats = nz.build_candidates(counts, {}, use_llm=False)
    assert "T2DM" not in {r["alias"] for r in rows} and stats["缩写被数字规则拦下"] == 1


def test_llm_cluster_merges_only_valid_output(monkeypatch):
    counts = Counter({("Drug", "磺脲类药物"): 5, ("Drug", "磺脉类"): 1, ("Drug", "磺脲类"): 4, ("Drug", "二甲双胍"): 9})
    calls = []

    def fake(prompt, **_):
        calls.append(prompt)
        return {"groups": [{"canonical": "磺脲类", "members": ["磺脉类", "磺脲类"]}]}
    monkeypatch.setattr(nz.llm, "chat_json", fake)
    monkeypatch.setattr(nz, "SIM_CUTOFF", 60)
    rows, _, stats = nz.build_candidates(counts, {}, use_llm=True)
    got = {r["alias"]: (r["canonical"], r["source"]) for r in rows}
    assert got["磺脉类"] == ("磺脲类", "llm") and got["磺脲类药物"] == ("磺脲类", "rule")
    assert "二甲双胍" not in got and len(calls) == 1


def test_llm_invalid_output_means_no_merge(monkeypatch):
    counts = Counter({("Drug", "磺脉类"): 1, ("Drug", "磺脲类"): 4})
    monkeypatch.setattr(nz, "SIM_CUTOFF", 60)
    monkeypatch.setattr(nz.llm, "chat_json", lambda p, **_: {"groups": [{"canonical": "磺酰脲类", "members": ["磺脉类", "磺脲类"]}]})
    rows, _, stats = nz.build_candidates(counts, {}, use_llm=True)
    assert rows == [] and stats["聚类LLM输出不合规（不合并）"] == 1


def test_long_names_excluded():
    long = "这是一个超过二十个字的很长很长的整句式实体名称示例文本"
    rows, long_names, _ = nz.build_candidates(Counter({("Treatment", long + "(X)"): 1}), {}, use_llm=False)
    assert rows == [] and long_names == [("Treatment", long + "(X)")]


def test_cluster_merge_dropping_digit_is_blocked(monkeypatch):
    counts = Counter({("Disease", "2型糖尿病肾病"): 1, ("Disease", "糖尿病肾病"): 4})
    monkeypatch.setattr(nz, "SIM_CUTOFF", 60)
    monkeypatch.setattr(nz.llm, "chat_json", lambda p, **_: {"groups": [{"canonical": "糖尿病肾病", "members": ["2型糖尿病肾病", "糖尿病肾病"]}]})
    rows, _, stats = nz.build_candidates(counts, {}, use_llm=True)
    assert rows == [] and stats["聚类被数字规则拦下"] == 1


def test_write_alias_csv(tmp_path):
    decided = [
        {"label": "Disease", "alias": "T2DM", "canonical": "2型糖尿病", "source": "rule", "final": "对", "by": "cross_review", "改为": ""},
        {"label": "Test", "alias": "MRI", "canonical": "磁共振血管造影", "source": "llm", "final": "错", "by": "human", "改为": "磁共振成像"},
        {"label": "Drug", "alias": "磺胺类", "canonical": "磺脲类", "source": "llm", "final": "存疑", "by": "human", "改为": ""},
    ]
    stats = nz.write_alias_csv(decided, tmp_path / "alias.csv")
    lines = (tmp_path / "alias.csv").read_text(encoding="utf-8-sig").splitlines()
    assert lines == ["label,alias,canonical,source,reviewed", "Disease,T2DM,2型糖尿病,rule,false", "Test,MRI,磁共振成像,manual,true"]
    assert stats == {"写入": 2, "存疑未写入": 1}
