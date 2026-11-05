from collections import Counter

import pytest

from kg.steps import drug_class as dc


@pytest.mark.parametrize("name, expected", [
    ("格列美脲", ("member", "磺脲类", "guideline")),
    ("格列齐特缓释片", ("member", "磺脲类", "guideline")),          # 剂型写法
    ("盐酸二甲双胍", ("member", "双胍类", "guideline")),
    ("甘精胰岛素", ("member", "长效胰岛素类似物", "guideline")),     # 不能被当成类别名
    ("精蛋白锌门冬胰岛素", ("member", "预混胰岛素类似物", "guideline")),  # 先于"门冬胰岛素"
    ("门冬胰岛素", ("member", "超短效胰岛素类似物", "guideline")),
    ("长效人GLP-1类似物利拉鲁肽", ("member", "GLP-1受体激动剂", "guideline")),
    ("磺脲类药物", ("alias", "磺脲类", "rule")),
    ("磺脉类", ("alias", "磺脲类", "rule")),                         # 错别字
    ("非磺脲类胰岛素促泌剂", ("alias", "格列奈类", "rule")),
    ("非胰岛素促泌剂", ("negative", None, "rule")),
    ("基础胰岛素", ("alias", "基础胰岛素", "rule")),
    ("速效胰岛素类似物", ("alias", "超短效胰岛素类似物", "rule")),
    ("胰岛素增敏剂", ("alias", "噻唑烷二酮类", "rule")),
    ("中等强度他汀", ("alias", "他汀类", "rule")),
    ("阿托伐他汀", ("member", "他汀类", "rule")),                     # 具体他汀不是类别名
    ("思格列净", ("member", "SGLT2抑制剂", "rule")),
    ("甲苯磺丁脲", ("member", "磺脲类", "rule")),
    ("氯沙坦", ("member", "ARB", "rule")),
    ("醛固酮受体拮抗剂", ("alias", "醛固酮受体拮抗剂", "rule")),     # 未列出的非降糖类别原样保留
    ("妥拉磺脲", ("member", "磺脲类", "rule")),                       # 具体磺脲类药，不是类别名
    ("格列汀", ("alias", "DPP-4抑制剂", "rule")),                     # 统称
    ("阿司匹林", ("unknown", None, "")),
])
def test_judge(name, expected):
    assert dc.judge(name) == expected


def test_parents_multi_membership():
    assert dc.with_parents("磺脲类") == ["磺脲类", "胰岛素促泌剂"]
    assert dc.with_parents("长效胰岛素类似物") == ["长效胰岛素类似物", "胰岛素", "基础胰岛素"]
    assert dc.with_parents("DPP-4抑制剂") == ["DPP-4抑制剂"]  # 指南不把 DPP-4i 归入肠促胰素类


def test_classify_rows_and_llm_goes_to_advisor(monkeypatch):
    names = Counter({"格列美脲": 5, "磺脲类": 9, "磺胺类": 2, "非胰岛素促泌剂": 1, "格列奇特": 1, "阿司匹林": 3})
    monkeypatch.setattr(dc.llm, "chat_json", lambda p, **_: {"results": [
        {"name": "格列奇特", "drug_class": "磺脲类", "reason": "疑为格列齐特"}, {"name": "阿司匹林", "drug_class": None}]})
    rows, table, stats = dc.classify(names, ["格列美脲属于磺脲类药物"], use_llm=True)
    got = {(r["drug"], r["drug_class"], r["kind"]) for r in rows}
    assert got == {("格列美脲", "磺脲类", "member"), ("格列美脲", "胰岛素促泌剂", "member"), ("磺脲类", "磺脲类", "alias")}
    t = {r["drug"]: r for r in table}
    assert t["格列奇特"]["需导师确认"] == "是" and t["磺胺类"]["需导师确认"] == "是"   # LLM 预标与存疑名称不写入 csv
    assert t["格列美脲"]["evidence"] == "格列美脲属于磺脲类药物"
    assert stats["否定类别（不建节点）"] == 1 and stats["未归类（非降糖药或无法判断）"] == 1
