"""② normalize：实体归一化，产出别名候选并导出 alias 审核表（data-model 第 ② 步，v1.1-design 4 节）。

每个实体出现的原文 → 规范名，依次经过：括号拆分 → 写法统一 → 缩写映射 → 相似名称聚类（LLM 判同）。
药物类别（第 4 小步）在 T5 的 drug_class 中处理。
"""
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from rapidfuzz import fuzz, process

from kg import llm, review

# DiaKG 实体类型 → 图谱标签；不在表中的类型（剂量、分型、机制等）不建节点
NODE_LABEL = {"Disease": "Disease", "Drug": "Drug", "Symptom": "Symptom", "Test": "Test", "Test_items": "Test",
              "ADE": "ADE", "Treatment": "Treatment", "Operation": "Treatment", "Anatomy": "Anatomy", "Reason": "Reason"}
LABEL_CN = {"Disease": "疾病", "Drug": "药物", "Symptom": "症状", "Test": "检查/指标", "ADE": "不良反应",
            "Treatment": "治疗", "Anatomy": "器官/部位", "Reason": "病因"}
MAX_LEN = 20            # 拆分后仍超过 20 字的不建节点
SIM_CUTOFF = 85         # RapidFuzz ratio，初始值
MAX_GROUP = 15          # 交给 LLM 的一组最多 15 个名称
TOP_ABBR = 60           # 无括号来源的高频缩写交 LLM 补齐
ABBR_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]*")
PAREN_RE = re.compile(r"^(.*?)\s*\(([^()]*)\)(.*)$")
HAS_CN = re.compile(r"[一-鿿]")


def nfkc(text: str) -> str:
    """全角转半角（括号、字母、数字、逗号）；去掉空白，但保留两个英文字母之间的空格（"1 型糖尿病"→"1型糖尿病"）。"""
    return re.sub(r"(?<![A-Za-z])\s+|\s+(?![A-Za-z])", "", unicodedata.normalize("NFKC", text))


def split_paren(text: str) -> tuple[str, list[str]] | None:
    """`中文名(英文全称，缩写)` → (中文名, [英文全称, 缩写])；括号在中间时别名带上后缀。

    括号内每一段必须是英文/缩写、"商品名X" 或 "即X"，否则不拆（返回 None，括号内容语义不明）。
    """
    m = PAREN_RE.match(nfkc(text))
    if not m:
        return None
    pre, inner, post = m.group(1).strip(), m.group(2), m.group(3).strip()
    if not pre:
        return None
    aliases = []
    for part in (p.strip() for p in re.split(r"[,;；、]", inner)):
        if not part:
            continue
        if part.startswith("商品名"):
            aliases.append(part[3:].strip())
        elif part.startswith("即"):
            aliases.append(part[1:].strip())
        elif not HAS_CN.search(part):
            aliases.append(part + post if post and ABBR_RE.fullmatch(part) else part)
        else:
            return None
    return pre + post, [a for a in aliases if a]


def unify(name: str) -> str:
    """规则统一写法：去掉"类药物/类药"中的"药物/药"。"""
    return re.sub(r"类药物?$", "类", name)


def digit_guard(abbr: str, canonical: str) -> bool:
    """缩写里的数字必须出现在规范名里，挡住 T2DM → 糖尿病 这类泛化映射。"""
    return all(d in canonical for d in re.findall(r"\d", abbr))


def is_abbr(text: str) -> bool:
    return bool(ABBR_RE.fullmatch(text)) and any(c.isupper() for c in text)


def collect(mentions_path: Path, sentences_path: Path):
    """→ {(label, 原文): 出现次数}，{(label, 原文): 一条例句}"""
    sent = {}
    for line in sentences_path.read_text(encoding="utf-8").splitlines():
        s = json.loads(line)
        sent[s["sid"]] = s["text"]
    counts, example = Counter(), {}
    for line in mentions_path.read_text(encoding="utf-8").splitlines():
        m = json.loads(line)
        label = NODE_LABEL.get(m["type"])
        if label:
            counts[(label, m["text"])] += 1
            example.setdefault((label, m["text"]), sent[m["sid"]][:80])
    return counts, example


def apply_rules(counts: Counter):
    """括号拆分 + 写法统一 + 括号来源的缩写映射。返回 每个原文的中间规范名、新增别名、缩写表、统计。"""
    stats = Counter()
    canon, extra = {}, []            # extra: (label, alias, canonical, rule)
    abbr_votes = defaultdict(Counter)  # (label, 缩写) → {规范名: 次数}
    for (label, text), n in counts.items():
        name, rule = nfkc(text), []
        if name != text:
            rule.append("全半角/空格")
        sp = split_paren(text)
        if sp:
            name, aliases = sp
            rule.append("括号拆分")
            stats["括号拆分"] += 1
            for a in aliases:
                extra.append((label, a, unify(name), "括号别名"))
                if is_abbr(a):
                    abbr_votes[(label, a)][unify(name)] += n
        elif "(" in nfkc(text):
            stats["括号未拆（内含中文说明）"] += 1
        u = unify(name)
        if u != name:
            rule.append("类药物→类")
        canon[(label, text)] = (u, rule)

    abbr_map = {}
    for key, votes in abbr_votes.items():
        best, _ = votes.most_common(1)[0]
        if not digit_guard(key[1], best):
            stats["缩写被数字规则拦下"] += 1
            continue
        abbr_map[key] = best
        if len(votes) > 1:
            stats["缩写多义（取最高频）"] += 1
    return canon, extra, abbr_map, stats


def _groups(names: list[str]) -> list[list[str]]:
    """相似度 ≥ SIM_CUTOFF 的名称并查集成组，只返回 ≥2 个名称的组。"""
    parent = list(range(len(names)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, n in enumerate(names):  # 不用 process.cdist：它依赖 numpy
        for _, _, j in process.extract(n, names, scorer=fuzz.ratio, score_cutoff=SIM_CUTOFF, limit=None):
            if i < j:
                parent[find(i)] = find(j)
    comps = defaultdict(list)
    for i, n in enumerate(names):
        comps[find(i)].append(n)
    out = []
    for c in comps.values():
        c.sort()
        # shortcut: 超大组按排序切块，块之间的同义关系会漏；第一轮结果里出现再调阈值
        out += [c[k:k + MAX_GROUP] for k in range(0, len(c), MAX_GROUP) if len(c[k:k + MAX_GROUP]) > 1]
    return out


GROUP_PROMPT = """以下是从中国糖尿病临床指南中标注出的一组【{label}】名称，它们写法相近。请判断哪些名称指的是同一个医学概念。

规则：
1. 只有含义和范围完全相同才算同一概念：错别字、全称与简称、措辞差异算同一概念；范围不同不算（"2型糖尿病"≠"糖尿病"，"糖尿病肾病"≠"肾病"），分型、分期、程度不同也不算。
2. 每个名称必须恰好出现在一个组里；与其他都不同的名称单独成组。
3. 每组的 canonical 必须是该组 members 中的一个，选最规范、最完整的中文写法。
4. 只输出 JSON：{{"groups": [{{"canonical": "...", "members": ["...", "..."]}}]}}

名称及例句：
{items}"""

ABBR_PROMPT = """在中国糖尿病临床指南中，缩写"{abbr}"被标注为【{label}】。例句：
{examples}

请从下面的候选规范名称中，选出与"{abbr}"含义完全相同的一个；范围不同（如 T2DM 对应"2型糖尿病"而不是"糖尿病"）不算。候选中没有就填 null。
候选：{candidates}
只输出 JSON：{{"canonical": "候选中的一个名称或 null"}}"""


def judge_group(label: str, names: list[str], example: dict) -> dict[str, str] | None:
    """LLM 判同；返回 {成员: 规范名}。输出不合规（漏名、重复、规范名不在成员中）时返回 None，即不合并。"""
    items = "\n".join(f"- {n}：{example.get(n, '')}" for n in names)
    try:
        out = llm.chat_json(GROUP_PROMPT.format(label=LABEL_CN[label], items=items))
    except llm.LLMError:
        return None
    mapping = {}
    for g in out.get("groups", []) if isinstance(out.get("groups"), list) else []:
        members, c = g.get("members"), g.get("canonical")
        if not isinstance(members, list) or c not in members:
            return None
        for m in members:
            if m in mapping:
                return None
            mapping[m] = c
    return mapping if set(mapping) == set(names) else None


def resolve_abbr(label: str, abbr: str, examples: list[str], candidates: list[str]) -> str | None:
    prompt = ABBR_PROMPT.format(abbr=abbr, label=LABEL_CN[label], examples="\n".join(f"- {e}" for e in examples[:3]),
                                candidates="、".join(candidates))
    try:
        c = llm.chat_json(prompt).get("canonical")
    except llm.LLMError:
        return None
    return c if c in candidates and digit_guard(abbr, c) else None


def build_candidates(counts: Counter, example: dict, use_llm: bool = True):
    """→ (别名候选行, 不建节点的长名称, 统计)。每行：label、alias、canonical、source、rule、count、example。"""
    canon, extra, abbr_map, stats = apply_rules(counts)
    final = {k: v[0] for k, v in canon.items()}
    source = {k: ("rule" if v[1] else "") for k, v in canon.items()}
    rules = {k: list(v[1]) for k, v in canon.items()}

    # 缩写：括号里定义过的直接映射；高频且没有定义的交 LLM，从同类规范名中选
    by_label_names = defaultdict(set)
    for (label, _), name in final.items():
        if not is_abbr(name):
            by_label_names[label].add(name)
    undefined = Counter()
    for (label, text), n in counts.items():
        a = final[(label, text)]
        if is_abbr(a):
            if (label, a) in abbr_map:
                final[(label, text)] = abbr_map[(label, a)]
                source[(label, text)] = "rule"
                rules[(label, text)].append("括号缩写")
            else:
                undefined[(label, a)] += n
    if use_llm:
        for (label, a), _ in undefined.most_common(TOP_ABBR):
            exs = [example[k] for k in counts if k[0] == label and final[k] == a and k in example]
            c = resolve_abbr(label, a, exs, sorted(by_label_names[label]))
            stats["缩写交LLM"] += 1
            if c:
                stats["缩写LLM补齐"] += 1
                for k in counts:
                    if k[0] == label and final[k] == a:
                        final[k] = c
                        source[k] = "llm"
                        rules[k].append("缩写LLM")

    # 相似名称聚类：在各标签的规范名之间做
    if use_llm:
        for label in sorted(by_label_names):
            names = sorted({v for (lb, _), v in final.items() if lb == label and len(v) <= MAX_LEN})
            for g in _groups(names):
                stats["聚类组"] += 1
                ex = {n: next((example[k] for k in counts if k[0] == label and final[k] == n and k in example), "") for n in g}
                mapping = judge_group(label, g, ex)
                if mapping is None:
                    stats["聚类LLM输出不合规（不合并）"] += 1
                    continue
                for m, c in list(mapping.items()):
                    if not digit_guard(m, c):  # "2型糖尿病肾病"→"糖尿病肾脏病" 会丢掉分型，不合并
                        mapping[m] = m
                        stats["聚类被数字规则拦下"] += 1
                for k in counts:
                    if k[0] == label and final[k] in mapping and mapping[final[k]] != final[k]:
                        final[k] = mapping[final[k]]
                        source[k] = "llm"
                        rules[k].append("聚类LLM")

    rows, long_names = {}, set()
    for (label, text), n in counts.items():
        c = final[(label, text)]
        if len(c) > MAX_LEN:
            long_names.add((label, text))
            continue
        if c != text:
            rows[(label, text)] = {"label": label, "alias": text, "canonical": c, "source": source[(label, text)] or "rule",
                                   "rule": "+".join(rules[(label, text)]), "count": n, "example": example.get((label, text), "")}
    for label, a, c, rule in extra:
        c = next((final[k] for k in canon if k[0] == label and canon[k][0] == c), c)  # 规范名若被聚类改过，跟着改
        if is_abbr(a) and not digit_guard(a, c):
            continue  # 与缩写映射同一规则：T2DM 不能挂到"糖尿病"
        if a != c and (label, a) not in rows and len(c) <= MAX_LEN:
            rows[(label, a)] = {"label": label, "alias": a, "canonical": c, "source": "rule", "rule": rule, "count": 0, "example": ""}
    stats["别名候选"] = len(rows)
    stats["长名称不建节点"] = len(long_names)
    out = sorted(rows.values(), key=lambda r: (r["label"], r["canonical"], r["alias"]))
    for r in out:
        r["id"] = f"alias-{r['label']}-{r['alias']}"
    return out, sorted(long_names), stats


COLUMNS = ["label", "alias", "canonical", "source", "rule", "count", "example"]


def run(work: Path, use_llm: bool = True) -> Counter:
    counts, example = collect(work / "mentions.jsonl", work / "sentences.jsonl")
    rows, long_names, stats = build_candidates(counts, example, use_llm)
    review.export("alias", rows, COLUMNS, review_dir=work / "review")
    (work / "long_names.jsonl").write_text(
        "".join(json.dumps({"label": l, "text": t}, ensure_ascii=False) + "\n" for l, t in long_names), encoding="utf-8")
    stats["实体名称（去重）"] = len(counts)
    return stats


def write_alias_csv(decided: list[dict], path: Path) -> Counter:
    """把 review import 的结论写成 kg/data/alias.csv。存疑的行不写入（等导师确认）。"""
    import csv

    stats = Counter()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # 带 BOM，Excel 直接打开不乱码
        w = csv.writer(f)
        w.writerow(["label", "alias", "canonical", "source", "reviewed"])
        for d in sorted(decided, key=lambda d: (d["label"], d["alias"])):
            if d["final"] == "存疑":
                stats["存疑未写入"] += 1
                continue
            fixed = d["final"] == "错"
            w.writerow([d["label"], d["alias"], str(d["改为"]).strip() if fixed else d["canonical"],
                        "manual" if fixed else d["source"], str(d["by"] == "human").lower()])
            stats["写入"] += 1
    return stats
