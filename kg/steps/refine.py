"""③ refine：Drug_Disease 边细分为 INDICATED / CONTRAINDICATED / CAUTION / MENTION（data-model 第 ③ 步，REQ-25）。

标签定义逐字取自已批准的 v1.1-review-prompts.md 第 2 节，预标与复审用同一套定义。
"""
import csv
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from kg import llm, review

LABELS = ("INDICATED", "CONTRAINDICATED", "CAUTION", "MENTION")
WORKERS = 8  # DeepSeek 没有每分钟请求上限，并发跑
COLUMNS = ["drug", "disease", "sentence", "label", "condition", "quote", "needs_manual", "drug_canonical", "disease_canonical", "sid"]


def label_definitions(md: Path = review.PROMPTS_MD) -> str:
    text = md.read_text(encoding="utf-8")
    start, end = text.find("【四种标签的定义】"), text.find("【判断规则】", text.find("【四种标签的定义】"))
    if start < 0 or end < 0:
        raise review.ReviewError(f"{md.name} 中找不到药物关系的标签定义")
    return text[start:end].strip()


PROMPT = """你是临床药学标注员。下面是从中国糖尿病临床指南中抽取的一句原文，其中标注了一个药物和一个疾病（或人群、状态）。请**只依据原句**判断该药物对该疾病 / 人群"能不能用"，打一个标签。

{definitions}

要求：
1. 只判断给出的这一对药物和疾病，不被同句其他药物的结论带偏；否定和条件要读全。
2. condition：原句中限定这条结论的条件（如"eGFR<30 ml/min 时""妊娠期"），必须逐字摘自原句；没有条件填 null。
3. quote：支持该标签的原句片段，必须逐字摘自原句，不能改写。
4. 只输出 JSON：{{"label": "INDICATED|CONTRAINDICATED|CAUTION|MENTION", "condition": "原句片段或 null", "quote": "原句片段"}}

药物：{drug}
疾病 / 人群：{disease}
原句：{sentence}"""


def check(out: dict, sentence: str) -> list[str]:
    """自动校验：标签在白名单内；quote、condition 是原句的严格子串。返回不合格原因。"""
    problems = []
    if out.get("label") not in LABELS:
        problems.append("标签不在白名单")
    quote = out.get("quote")
    if not isinstance(quote, str) or not quote or quote not in sentence:
        problems.append("quote 不是原句子串")
    cond = out.get("condition")
    if cond not in (None, "") and (not isinstance(cond, str) or cond not in sentence):
        problems.append("condition 不是原句子串")
    return problems


def label_edges(edges: list[dict], sentences: dict[str, str], alias: dict, definitions: str) -> tuple[list[dict], Counter]:
    def one(e):
        sentence = sentences[e["sid"]]
        prompt = PROMPT.format(definitions=definitions, drug=e["head"], disease=e["tail"], sentence=sentence)
        try:
            out = llm.chat_json(prompt)
        except llm.LLMError as err:
            out, problems = {}, [f"LLM 调用失败：{err}"]
        else:
            problems = check(out, sentence)
        return {"id": e["eid"], "drug": e["head"], "disease": e["tail"], "sentence": sentence,
                "label": out.get("label") if out.get("label") in LABELS else "",
                "condition": out.get("condition") or "", "quote": out.get("quote") or "",
                "needs_manual": "；".join(problems), "sid": e["sid"],
                "drug_canonical": alias.get(("Drug", e["head"]), e["head"]),
                "disease_canonical": alias.get(("Disease", e["tail"]), e["tail"])}

    with ThreadPoolExecutor(WORKERS) as pool:
        rows = list(pool.map(one, edges))
    stats = Counter(r["label"] or "无标签" for r in rows)
    stats["needs_manual"] = sum(bool(r["needs_manual"]) for r in rows)
    # needs_manual 置顶，再按标签分组（v1.1-design 5.2 节）
    rows.sort(key=lambda r: (not r["needs_manual"], LABELS.index(r["label"]) if r["label"] else -1, r["id"]))
    return rows, stats


def run(work: Path) -> Counter:
    sentences = {}
    for line in (work / "sentences.jsonl").read_text(encoding="utf-8").splitlines():
        s = json.loads(line)
        sentences[s["sid"]] = s["text"]
    edges = [e for e in map(json.loads, (work / "edges.jsonl").read_text(encoding="utf-8").splitlines())
             if e["type"] == "Drug_Disease"]
    from kg.steps.normalize import load_alias

    rows, stats = label_edges(edges, sentences, load_alias(work), label_definitions())
    review.export("drug_relation", rows, COLUMNS, review_dir=work / "review")
    stats["边数"] = len(rows)
    return stats


def parse_fix(text: str) -> tuple[str, str]:
    """审核表"改为"列：`标签` 或 `标签；条件`。"""
    label, _, cond = str(text).strip().partition("；")
    label = label.strip().upper()
    if label not in LABELS:
        raise review.ReviewError(f"\"改为\"中的标签不合法：{text!r}（应为 {'/'.join(LABELS)}，可加\"；条件\"）")
    return label, cond.strip()


def write_labels_csv(decided: list[dict], path: Path) -> Counter:
    """写 kg/data/drug_relation_labels.csv。存疑与 needs_manual 但未人工处理的行不写入。"""
    stats = Counter()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["edge_id", "drug", "disease", "label", "condition", "quote", "sid", "source", "review", "reviewed"])
        for d in sorted(decided, key=lambda d: d["id"]):
            if d["final"] == "存疑":
                stats["存疑未写入"] += 1
                continue
            if d["final"] == "错":
                label, cond = parse_fix(d["改为"])
                source = "manual"
            else:
                if not d["label"]:
                    stats["无标签未写入"] += 1
                    continue
                label, cond, source = d["label"], d["condition"], "llm"
            w.writerow([d["id"], d["drug_canonical"], d["disease_canonical"], label, cond, d["quote"], d["sid"],
                        source, d["final"], str(d["by"] == "human").lower()])
            stats[label] += 1
    return stats
