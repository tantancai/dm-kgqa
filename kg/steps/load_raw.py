"""① load：把 DiaKG 原始 JSON 拆成 sentences / mentions / edges 三个 jsonl（data-model 第 ① 步）。

关系保持原始方向（head 为关系名前半部分的类型，如 ADE_Drug 的 head 是 ADE），按 Schema 调整方向留给 build。
"""
import json
from collections import Counter
from pathlib import Path


def _sentence_offset(sentence: dict, ent: dict) -> tuple[int, str]:
    """DiaKG 的实体偏移有两种口径：多数相对段落，少数相对句子。统一换算成相对句子的偏移。"""
    text, word = sentence["sentence"], ent["entity"]
    start = ent["start_idx"] - sentence["start_idx"]
    if start >= 0 and text[start:start + len(word)] == word:  # 负数下标会从句尾切片，可能误命中
        return start, "paragraph"
    if text[ent["start_idx"]:ent["start_idx"] + len(word)] == word:
        return ent["start_idx"], "sentence"
    raise ValueError(f"实体 {word!r} 在句子中定位失败：{text!r}")


def parse_doc(doc: dict) -> tuple[list, list, list, Counter]:
    sentences, mentions, edges, stats = [], [], [], Counter()
    for para in doc["paragraphs"]:
        for s in para["sentences"]:
            sid = f"diakg-{doc['doc_id']}-{para['paragraph_id']}-{s['sentence_id']}"
            sentences.append({"sid": sid, "doc_id": str(doc["doc_id"]), "text": s["sentence"]})
            by_id = {}
            for e in s["entities"]:
                start, basis = _sentence_offset(s, e)
                stats[f"offset_{basis}"] += 1
                m = {"mid": f"{sid}-{e['entity_id']}", "sid": sid, "text": e["entity"], "type": e["entity_type"],
                     "start": start, "end": start + len(e["entity"])}
                mentions.append(m)
                by_id[e["entity_id"]] = m
            for r in s["relations"]:
                h, t = by_id.get(r["head_entity_id"]), by_id.get(r["tail_entity_id"])
                if h is None or t is None:
                    stats["edge_missing_entity"] += 1
                    continue
                if r["relation_type"] != f"{h['type']}_{t['type']}":
                    stats["edge_type_mismatch"] += 1  # 只计数报告，不纠正
                edges.append({"eid": f"{sid}-{r['relation_id']}", "sid": sid, "type": r["relation_type"],
                              "head_mid": h["mid"], "tail_mid": t["mid"], "head": h["text"], "tail": t["text"],
                              "head_type": h["type"], "tail_type": t["type"]})
    return sentences, mentions, edges, stats


def _write_jsonl(path: Path, rows: list) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def run(raw_dir: Path, out_dir: Path) -> Counter:
    files = sorted(raw_dir.glob("*.json"), key=lambda p: int(p.stem))
    if not files:
        raise FileNotFoundError(f"{raw_dir} 下没有 DiaKG JSON，先按 v1.1-design 2.2 节放置原始数据")
    all_s, all_m, all_e, stats = [], [], [], Counter()
    for f in files:
        s, m, e, st = parse_doc(json.loads(f.read_text(encoding="utf-8")))
        all_s += s; all_m += m; all_e += e; stats += st
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_dir / "sentences.jsonl", all_s)
    _write_jsonl(out_dir / "mentions.jsonl", all_m)
    _write_jsonl(out_dir / "edges.jsonl", all_e)
    stats.update(docs=len(files), sentences=len(all_s), mentions=len(all_m), edges=len(all_e))
    for t, n in sorted(Counter(e["type"] for e in all_e).items()):
        stats[f"edges.{t}"] = n
    return stats
