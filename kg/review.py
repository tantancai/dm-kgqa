"""审核表的导出 / 合并复审 / 读回（v1.1-design 第 5 节，ADR-0009）。

与具体哪张表无关：各步（normalize、refine、diet）负责准备行和把读回的结论写进 kg/data/。
"""
import hashlib
import json
from collections import Counter
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.datavalidation import DataValidation

from kg.config import WORK

REVIEW_DIR = WORK / "review"
VERDICTS = ("对", "错", "存疑")
# 预标列之后固定追加的列；以 _ 开头的列隐藏
TAIL = ["复审结论", "复审改为", "复审理由", "需你确认", "结论", "改为", "备注", "_hash"]
KEEP = ["复审结论", "复审改为", "复审理由", "需你确认", "结论", "改为", "备注"]  # 行内容未变时沿用
SAMPLE_MOD = 10      # 复审一致的行抽 1/10 给人确认
ESCALATE = 0.05      # 抽检错误率超过它就提示升级到 30%（ADR-0009）


class ReviewError(ValueError):
    pass


def _hash(row: dict, columns: list[str]) -> str:
    return hashlib.sha256(json.dumps([row.get(c) for c in columns], ensure_ascii=False).encode()).hexdigest()[:16]


def _sampled(row_id: str) -> bool:
    return int(hashlib.sha256(row_id.encode()).hexdigest(), 16) % SAMPLE_MOD == 0


def _read(path: Path) -> tuple[list[str], list[dict]]:
    ws = load_workbook(path).active
    rows = list(ws.iter_rows(values_only=True))
    header = list(rows[0])
    return header, [{h: ("" if v is None else v) for h, v in zip(header, r)} for r in rows[1:]]


def _write(path: Path, header: list[str], rows: list[dict]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append([r.get(h, "") for h in header])
    col = header.index("结论") + 1
    dv = DataValidation(type="list", formula1='"对,错,存疑"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"{ws.cell(1, col).column_letter}2:{ws.cell(1, col).column_letter}{len(rows) + 1}")
    for i, h in enumerate(header, 1):
        if h.startswith("_"):
            ws.column_dimensions[ws.cell(1, i).column_letter].hidden = True
    ws.freeze_panes = "B2"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def export(name: str, rows: list[dict], columns: list[str], review_dir: Path = REVIEW_DIR) -> Path:
    """rows 每行必须有唯一的 id。已有同名审核表时，内容没变的行沿用原来的复审和结论。"""
    ids = [r["id"] for r in rows]
    if len(set(ids)) != len(ids):
        raise ReviewError(f"{name}: id 有重复")
    header = ["id"] + columns + TAIL
    path = review_dir / f"{name}.xlsx"
    old = {r["id"]: r for r in _read(path)[1]} if path.exists() else {}
    out = []
    for r in rows:
        row = {h: r.get(h, "") for h in ["id"] + columns}
        row["_hash"] = _hash(r, columns)
        prev = old.get(r["id"])
        if prev and prev.get("_hash") == row["_hash"]:
            row.update({k: prev.get(k, "") for k in KEEP})
        out.append(row)
    _write(path, header, out)
    with (review_dir / f"{name}.jsonl").open("w", encoding="utf-8") as f:  # 交给复审模型的输入
        for r in out:
            f.write(json.dumps({h: r[h] for h in ["id"] + columns}, ensure_ascii=False) + "\n")
    return path


def merge(name: str, output_path: Path, review_dir: Path = REVIEW_DIR) -> Counter:
    """把复审模型输出的 JSONL 合并进审核表。任何一行不合格就整体拒绝，不写入。"""
    path = review_dir / f"{name}.xlsx"
    header, rows = _read(path)
    by_id = {r["id"]: r for r in rows}
    seen, results = set(), []
    for n, line in enumerate(output_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError as e:
            raise ReviewError(f"第 {n} 行不是合法 JSON：{e}") from e
        if not isinstance(o, dict) or o.get("id") not in by_id:
            raise ReviewError(f"第 {n} 行 id 不在审核表中：{line[:80]}")
        if o["id"] in seen:
            raise ReviewError(f"第 {n} 行 id 重复：{o['id']}")
        if o.get("verdict") not in VERDICTS:
            raise ReviewError(f"第 {n} 行 verdict 不合法：{o.get('verdict')!r}")
        seen.add(o["id"])
        results.append(o)

    stats = Counter()
    for o in results:
        r = by_id[o["id"]]
        fix = {k: v for k, v in o.items() if k not in ("id", "verdict", "reason", "evidence")}
        r["复审结论"], r["复审理由"] = o["verdict"], o.get("reason", "")
        r["复审改为"] = json.dumps(fix, ensure_ascii=False) if o["verdict"] == "错" else ""
        r["需你确认"] = "是" if o["verdict"] != "对" or _sampled(o["id"]) else "否"
        stats[f"复审{o['verdict']}"] += 1
        stats["需你确认"] += r["需你确认"] == "是"
    _write(path, header, rows)
    return stats


def decide(name: str, review_dir: Path = REVIEW_DIR) -> tuple[list[dict], Counter]:
    """读回审核表，确定每行的最终结论，写 <name>.decided.jsonl。

    最终结论 = 你填的"结论"；没填时，复审为"对"且不需要你确认的行记为"对"。其余情况报错，要求补填。
    """
    _, rows = _read(review_dir / f"{name}.xlsx")
    problems, decided, stats = [], [], Counter()
    for r in rows:
        human = r["结论"]
        if human and human not in VERDICTS:
            problems.append(f"{r['id']}: 结论只能是 对/错/存疑")
        elif human == "错" and not str(r["改为"]).strip():
            problems.append(f"{r['id']}: 结论为错时必须填写\"改为\"")
        elif human:
            stats[f"人工{human}"] += 1
            if r["复审结论"] == "对" and r["需你确认"] == "是":  # 一致项抽检
                stats["抽检"] += 1
                stats["抽检错"] += human != "对"
            decided.append({**r, "final": human, "by": "human"})
        elif r["复审结论"] == "对" and r["需你确认"] == "否":
            decided.append({**r, "final": "对", "by": "cross_review"})
        else:
            problems.append(f"{r['id']}: 需要你确认但\"结论\"为空" if r["需你确认"] == "是" else f"{r['id']}: 尚未复审，也没有人工结论")
    if problems:
        raise ReviewError(f"{name}: {len(problems)} 行不合格，例如：\n" + "\n".join(problems[:10]))

    human_total = sum(stats[f"人工{v}"] for v in VERDICTS)
    stats["通过率%"] = round(100 * stats["人工对"] / human_total, 1) if human_total else 0
    stats["抽检错误率超标"] = int(bool(stats["抽检"]) and stats["抽检错"] / stats["抽检"] > ESCALATE)
    with (review_dir / f"{name}.decided.jsonl").open("w", encoding="utf-8") as f:
        for d in decided:
            f.write(json.dumps({k: v for k, v in d.items() if not k.startswith("_")}, ensure_ascii=False) + "\n")
    return decided, stats


PROMPTS_MD = Path(__file__).resolve().parent.parent / "docs" / "02-design" / "iterations" / "v1.1-review-prompts.md"
PLACEHOLDER = "<在此粘贴 30～50 行 JSONL>"
AUTO_NOTE = '\n\n【自动化调用补充说明】本次通过 API 调用：请把所有行的输出 JSON 依次放进一个对象 {"results": [ ... ]} 中返回，其余要求不变。'


def review_prompt(name: str, md: Path = PROMPTS_MD) -> str:
    """从已批准的 v1.1-review-prompts.md 中取该表的提示词（标题含 `name` 的小节里的第一个 ```text 块）。"""
    text = md.read_text(encoding="utf-8")
    start = text.find(f"`{name}`")
    block = text.find("```text", start)
    if start < 0 or block < 0:
        raise ReviewError(f"{md.name} 中找不到 {name} 的提示词")
    body = text[block + len("```text"):text.find("```", block + 7)].strip()
    if PLACEHOLDER not in body:
        raise ReviewError(f"{name} 的提示词缺少数据占位符")
    return body


def cross(name: str, batch: int = 40, review_dir: Path = REVIEW_DIR, provider: str = "kimi") -> Counter:
    """自动交叉复审：按批把 <name>.jsonl 交给复审模型，合格的批写入 <name>.cross.jsonl 后 merge。

    某批输出的 id 与输入不一致时整批丢弃（不猜），这些行保持未复审，可重跑或手工复审。
    """
    from kg import llm

    template = review_prompt(name)
    lines = (review_dir / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
    stats, out = Counter(), []
    for k in range(0, len(lines), batch):
        chunk = lines[k:k + batch]
        ids = [json.loads(x)["id"] for x in chunk]
        try:
            res = llm.chat_json(template.replace(PLACEHOLDER, "\n".join(chunk)) + AUTO_NOTE, provider=provider).get("results")
        except llm.LLMError as e:
            stats["批次调用失败"] += 1
            print(f"第 {k // batch + 1} 批调用失败：{e}")
            continue
        if not isinstance(res, list) or sorted(str(r.get("id")) for r in res if isinstance(r, dict)) != sorted(ids):
            stats["批次输出id不符（丢弃）"] += 1
            continue
        out += res
        stats["批次成功"] += 1
    path = review_dir / f"{name}.cross.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out), encoding="utf-8")
    stats.update(merge(name, path, review_dir=review_dir))
    stats["未复审行"] = len(lines) - len(out)
    return stats
