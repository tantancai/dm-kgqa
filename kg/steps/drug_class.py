"""② 第 4 小步 drug-class：药物 → 标准类别（v1.1-design 附录 A，ADR-0009）。

每个 Drug 规范名分三种：
- 本身是类别名（如"磺脲类药物""基础胰岛素"）→ kind=alias，图谱中作为 DrugClass 节点；
- 具体药物 → kind=member，(Drug)-[:BELONGS_TO]->(DrugClass)，可属于多个类别；
- 否定类别（"非促泌剂类"）→ 不建节点。
判断顺序：指南名单（含剂型写法）→ 类别名规则 → 词根规则 → 类别名特征兜底 → LLM 预标（交导师确认，确认前不写入）。
指南名单放最前，避免"甘精胰岛素""…GLP-1类似物利拉鲁肽"这类具体药被当成类别名。
"""
import csv
import json
import re
from collections import Counter
from pathlib import Path

from kg import llm, review

# 标准类别 → 同时属于的上位类（附录 A.1、A.2）
PARENTS = {
    "磺脲类": ["胰岛素促泌剂"], "格列奈类": ["胰岛素促泌剂"],
    "GLP-1受体激动剂": ["肠促胰素类降糖药"], "GIP/GLP-1双受体激动剂": ["肠促胰素类降糖药"],
    **{s: ["胰岛素"] for s in ["超短效胰岛素类似物", "短效胰岛素", "长效胰岛素", "预混胰岛素", "预混胰岛素类似物", "双胰岛素类似物"]},
    "中效胰岛素": ["胰岛素", "基础胰岛素"], "长效胰岛素类似物": ["胰岛素", "基础胰岛素"],
}
GLUCOSE_CLASSES = ["双胍类", "磺脲类", "格列奈类", "α-糖苷酶抑制剂", "噻唑烷二酮类", "DPP-4抑制剂", "SGLT2抑制剂",
                   "GLP-1受体激动剂", "GIP/GLP-1双受体激动剂", "葡萄糖激酶激活剂", "PPAR泛激动剂", "胰岛素",
                   "超短效胰岛素类似物", "短效胰岛素", "中效胰岛素", "长效胰岛素", "长效胰岛素类似物", "预混胰岛素",
                   "预混胰岛素类似物", "双胰岛素类似物", "基础胰岛素", "胰岛素促泌剂", "肠促胰素类降糖药"]

# 类别名 → 标准类别。按顺序匹配，先具体后宽泛（附录 A.1～A.4）
CLASS_PATTERNS = [
    (r"非磺脲类胰岛素促泌剂|格列奈类", "格列奈类"),               # 指南原文"格列奈类药物为非磺脲类胰岛素促泌剂"，须先于否定类别
    (r"^非.*促泌剂|^非胰岛素促泌剂", None),                      # 否定类别，不建节点
    (r"双胰岛素", "双胰岛素类似物"), (r"预混.*类似物", "预混胰岛素类似物"), (r"^预混(人)?胰岛素$", "预混胰岛素"),
    (r"^(超短效|速效)胰岛素(类似物)?$", "超短效胰岛素类似物"), (r"^(短效(人)?|常规|普通)胰岛素$", "短效胰岛素"),
    (r"^中效胰岛素$", "中效胰岛素"), (r"长效.*胰岛素类似物", "长效胰岛素类似物"), (r"^长效胰岛素$", "长效胰岛素"),
    (r"^基础胰岛素$", "基础胰岛素"),
    (r"^(胰岛素|胰岛素类似物|人胰岛素|动物胰岛素|胰岛素制剂|外源性胰岛素|餐时胰岛素|餐前胰岛素|短效/速效胰岛素|中效或长效胰岛素|基础\+餐时胰岛素)$", "胰岛素"),
    (r"GIP/GLP", "GIP/GLP-1双受体激动剂"),
    (r"磺(脲|脉|酰脲)类", "磺脲类"),                              # 必须带"类"：妥拉磺脲是具体药
    (r"^格列汀类?$", "DPP-4抑制剂"), (r"^格列净类?$", "SGLT2抑制剂"),   # 统称，不是具体药
    (r"^(胰岛素促泌剂.*|促泌剂|短效促泌剂|中长效促泌剂|促胰岛素分泌剂)$", "胰岛素促泌剂"),
    (r"糖苷(酶|醇)抑制剂|葡萄糖苷酶抑制剂", "α-糖苷酶抑制剂"),
    (r"噻唑烷二酮|TZD|格列酮类|胰岛素增敏剂|PPAR-γ受体激动剂", "噻唑烷二酮类"),
    (r"DPP-?4|DDP-4|二肽基肽酶", "DPP-4抑制剂"),
    (r"SGLT-?2|钠-葡萄糖(共|协同)转运蛋白2", "SGLT2抑制剂"),
    (r"GLP[-—]?1|胰高血糖素样肽-?1", "GLP-1受体激动剂"),
    (r"肠促胰素", "肠促胰素类降糖药"),
    (r"葡萄糖激酶激活剂", "葡萄糖激酶激活剂"), (r"PPAR泛激动剂", "PPAR泛激动剂"), (r"^双胍类", "双胍类"),
    # 非降糖药（附录 A.3）：只合并写法
    (r"他汀(?<!伐他汀)(类)?|还原酶抑制剂\(他汀类\)", "他汀类"),
    (r"贝特类|苯氧芳酸类", "贝特类"), (r"胆固醇吸收抑制剂", "胆固醇吸收抑制剂"),
    (r"前蛋白转化酶枯草溶菌素9|PCSK9", "PCSK9抑制剂"), (r"^烟酸类", "烟酸类"), (r"树脂类|胆汁酸螯合剂", "胆酸螯合剂"),
    (r"转换酶抑制剂|^ACEI$", "ACEI"), (r"II受体拮抗剂|Ⅱ受体拮抗剂|^ARB(类)?$", "ARB"),
    (r"钙通道阻滞剂|钙拮抗剂|CCB|二氢吡啶类", "钙通道阻滞剂"), (r"β受体阻滞剂", "β受体阻滞剂"), (r"噻嗪类", "噻嗪类利尿剂"),
    (r"黄嘌呤氧化酶抑制剂", "黄嘌呤氧化酶抑制剂"), (r"尿酸重吸收抑制剂", "尿酸排泄促进剂"),
    (r"COX|环氧化酶|非甾体抗炎药", "非甾体抗炎药"), (r"醛糖还原酶抑制剂", "醛糖还原酶抑制剂"),
    (r"免疫抑制剂", "免疫抑制剂"), (r"喹诺酮类", "喹诺酮类"),
]
CLASS_LIKE = re.compile(r"类|抑制剂|激动剂|促泌剂|增敏剂|阻滞剂|拮抗剂|类似物|分泌剂|胰岛素$")
DOUBT_NAMES = {"磺胺类": "可能是\"磺脲类\"的错别字，也可能是磺胺类抗菌药，需看原句（附录 A.4）"}

# 指南列出的具体药物（附录 A.1、A.2）；名称中包含这些药名的剂型（缓释片、盐酸盐等）同样归类
GUIDELINE_DRUGS = {
    "双胍类": ["二甲双胍"], "磺脲类": ["格列本脲", "格列美脲", "格列齐特", "格列吡嗪", "格列喹酮"],
    "格列奈类": ["瑞格列奈", "那格列奈", "米格列奈"], "α-糖苷酶抑制剂": ["阿卡波糖", "伏格列波糖", "米格列醇"],
    "噻唑烷二酮类": ["吡格列酮", "罗格列酮"], "DPP-4抑制剂": ["西格列汀", "沙格列汀", "维格列汀", "利格列汀", "阿格列汀"],
    "SGLT2抑制剂": ["达格列净", "恩格列净", "卡格列净", "艾托格列净", "恒格列净"],
    "GLP-1受体激动剂": ["贝那鲁肽", "艾塞那肽", "利司那肽", "利拉鲁肽", "司美格鲁肽", "度拉糖肽", "洛塞那肽"],
    "GIP/GLP-1双受体激动剂": ["替尔泊肽"], "葡萄糖激酶激活剂": ["多格列艾汀"], "PPAR泛激动剂": ["西格列他钠"],
    # 胰岛素：先匹配含"精蛋白锌门冬/赖脯"的预混类似物，再匹配单独的门冬/赖脯
    "双胰岛素类似物": ["德谷门冬双胰岛素"], "预混胰岛素类似物": ["精蛋白锌门冬胰岛素", "精蛋白锌赖脯胰岛素", "门冬胰岛素30"],
    "中效胰岛素": ["中性鱼精蛋白锌胰岛素", "低精蛋白锌胰岛素", "NPH"], "长效胰岛素": ["精蛋白锌胰岛素"],
    "超短效胰岛素类似物": ["门冬胰岛素", "赖脯胰岛素", "谷赖胰岛素"], "长效胰岛素类似物": ["甘精胰岛素", "地特胰岛素", "德谷胰岛素"],
}
# 词根前至少还要有一个字，"格列汀"本身是统称
ROOT_RULES = [(r".格列汀$", "DPP-4抑制剂"), (r".格列净$", "SGLT2抑制剂"), (r".格列奈$", "格列奈类"), (r".格列酮$", "噻唑烷二酮类"),
              (r".(鲁肽|那肽)$", "GLP-1受体激动剂"), (r"(格列|磺).*脲$", "磺脲类"),
              (r".伐他汀$", "他汀类"), (r".贝特$", "贝特类"), (r".普利$", "ACEI"), (r".沙坦$", "ARB"), (r".地平$", "钙通道阻滞剂"), (r".洛尔$", "β受体阻滞剂")]

LLM_PROMPT = """以下是中国糖尿病临床指南中标注为"药物"的名称（可能含错别字或截断）。请判断每个名称是否属于下列降糖药类别之一：
{classes}
规则：只有确定属于时才填类别；不是降糖药、无法判断、或名称残缺看不出是什么药的，填 null。不要猜。
只输出 JSON：{{"results": [{{"name": "原名称", "drug_class": "类别或 null", "reason": "不超过 20 字"}}]}}
名称：
{names}"""


def judge(name: str) -> tuple[str, str | None, str]:
    """→ (kind, 标准类别, 来源)。kind: member | alias | negative | unknown。"""
    for cls, drugs in GUIDELINE_DRUGS.items():
        if any(d in name for d in drugs):
            return "member", cls, "guideline"
    for pat, cls in CLASS_PATTERNS:
        if re.search(pat, name):
            return ("alias", cls, "rule") if cls else ("negative", None, "rule")
    for pat, cls in ROOT_RULES:
        if re.search(pat, name):
            return "member", cls, "rule"
    if CLASS_LIKE.search(name):
        return "alias", name, "rule"  # 没匹配上的非降糖类别：只合并写法，原样作为类别（附录 A.3）
    return "unknown", None, ""


def with_parents(cls: str) -> list[str]:
    return [cls] + PARENTS.get(cls, [])


def guideline_sentences(pdf: Path) -> list[str]:
    import fitz

    text = "".join(p.get_text() for p in fitz.open(pdf))
    text = re.sub(r"\s+", "", text).replace("⁃", "-")
    return [s for s in re.split(r"[。；]", text) if s]


def evidence(name: str, cls: str, sentences: list[str]) -> str:
    """指南中药名与类别名同句出现的第一句（截 120 字）；找不到返回空。"""
    keys = [cls, cls.replace("抑制剂", "i"), cls.replace("受体激动剂", "RA"), cls.rstrip("类")]
    for s in sentences:
        if name in s and any(k and k in s for k in keys):
            return s[:120]
    return ""


def classify(names: Counter, sentences: list[str], use_llm: bool = True):
    """→ (写入 drug_class.csv 的行, 汇总/存疑表的行, 统计)"""
    rows, table, stats, unknown = [], [], Counter(), []
    for name, n in sorted(names.items(), key=lambda x: -x[1]):
        if name in DOUBT_NAMES:
            table.append({"id": name, "drug": name, "drug_class": "", "kind": "", "source": "doubt", "evidence": DOUBT_NAMES[name],
                          "count": n, "需导师确认": "是"})
            stats["存疑"] += 1
            continue
        kind, cls, src = judge(name)
        if kind == "negative":
            stats["否定类别（不建节点）"] += 1
            continue
        if kind == "alias":
            rows.append({"drug": name, "drug_class": cls, "kind": "alias", "source": "rule"})
            table.append({"id": name, "drug": name, "drug_class": cls, "kind": "alias", "source": "rule", "evidence": "", "count": n, "需导师确认": ""})
            stats["类别名"] += 1
            continue
        if kind == "unknown":
            unknown.append((name, n))
            continue
        ev = evidence(name, cls, sentences)
        for c in with_parents(cls):
            rows.append({"drug": name, "drug_class": c, "kind": "member", "source": src})
        table.append({"id": name, "drug": name, "drug_class": "、".join(with_parents(cls)), "kind": "member", "source": src,
                      "evidence": ev, "count": n, "需导师确认": ""})
        stats[f"具体药物（{src}）"] += 1
        stats["规则归类但指南中无证据句"] += src == "rule" and not ev

    if use_llm:
        allowed = [c for c in GLUCOSE_CLASSES if c not in ("胰岛素促泌剂", "肠促胰素类降糖药", "基础胰岛素")]
        for k in range(0, len(unknown), 40):
            chunk = unknown[k:k + 40]
            try:
                res = llm.chat_json(LLM_PROMPT.format(classes="、".join(allowed), names="\n".join(n for n, _ in chunk))).get("results", [])
            except llm.LLMError:
                res = []
            got = {r.get("name"): r for r in res if isinstance(r, dict)}
            for name, n in chunk:
                r = got.get(name) or {}
                if r.get("drug_class") in allowed:
                    table.append({"id": name, "drug": name, "drug_class": r["drug_class"], "kind": "member", "source": "llm",
                                  "evidence": r.get("reason", ""), "count": n, "需导师确认": "是"})
                    stats["LLM预标降糖药（交导师，暂不写入）"] += 1
                else:
                    stats["未归类（非降糖药或无法判断）"] += 1
    else:
        stats["未归类（非降糖药或无法判断）"] += len(unknown)
    return rows, table, stats


def drug_names(work: Path) -> Counter:
    """Drug 规范名及出现次数：用 normalize 的别名映射（审核后为 kg/data/alias.csv，审核前为候选表）。"""
    from kg.config import DATA

    alias = {}
    if (DATA / "alias.csv").exists():
        with (DATA / "alias.csv").open(encoding="utf-8-sig") as f:
            alias = {(r["label"], r["alias"]): r["canonical"] for r in csv.DictReader(f)}
    else:
        for line in (work / "review" / "alias.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            alias[(r["label"], r["alias"])] = r["canonical"]
    names = Counter()
    for line in (work / "mentions.jsonl").read_text(encoding="utf-8").splitlines():
        m = json.loads(line)
        if m["type"] == "Drug":
            name = alias.get(("Drug", m["text"]), m["text"])
            if len(name) <= 20:
                names[name] += 1
    return names


def run(work: Path, data: Path, pdf: Path, use_llm: bool = True) -> Counter:
    rows, table, stats = classify(drug_names(work), guideline_sentences(pdf), use_llm)
    data.mkdir(parents=True, exist_ok=True)
    with (data / "drug_class.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["drug", "drug_class", "kind", "source", "reviewed"])
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["drug_class"], r["drug"])):
            w.writerow({**r, "reviewed": "false"})
    review.export("drug_class", table, ["drug", "drug_class", "kind", "source", "evidence", "count", "需导师确认"],
                  review_dir=work / "review")
    per_class = Counter(r["drug_class"] for r in rows)
    stats.update({f"类别 {c}": n for c, n in sorted(per_class.items()) if c in GLUCOSE_CLASSES})
    return stats
