import json

import pytest

from kg.steps.load_raw import parse_doc, run


def _doc(sentence_start=10, ent_start=12, relations=None):
    # 段落："前缀十个字的内容啊。二甲双胍可用于2型糖尿病。"，第二句从段落偏移 10 开始
    s = "二甲双胍可用于2型糖尿病。"
    return {"doc_id": "7", "paragraphs": [{"paragraph_id": 0, "paragraph": "x" * 10 + s, "sentences": [{
        "sentence_id": 1, "sentence": s, "start_idx": sentence_start, "end_idx": sentence_start + len(s),
        "entities": [
            {"entity_id": "T0", "entity": "二甲双胍", "entity_type": "Drug", "start_idx": sentence_start, "end_idx": sentence_start + 4},
            {"entity_id": "T1", "entity": "2型糖尿病", "entity_type": "Disease", "start_idx": ent_start, "end_idx": ent_start + 5},
        ],
        "relations": relations if relations is not None else
        [{"relation_type": "Drug_Disease", "relation_id": "R0", "head_entity_id": "T0", "tail_entity_id": "T1"}],
    }]}]}


def test_ids_offsets_and_edge():
    sentences, mentions, edges, stats = parse_doc(_doc(ent_start=17))
    assert sentences == [{"sid": "diakg-7-0-1", "doc_id": "7", "text": "二甲双胍可用于2型糖尿病。"}]
    assert [(m["mid"], m["start"], m["end"]) for m in mentions] == [("diakg-7-0-1-T0", 0, 4), ("diakg-7-0-1-T1", 7, 12)]
    assert edges[0]["eid"] == "diakg-7-0-1-R0"
    assert (edges[0]["head"], edges[0]["tail"], edges[0]["type"]) == ("二甲双胍", "2型糖尿病", "Drug_Disease")
    assert stats["offset_paragraph"] == 2 and stats["edge_type_mismatch"] == 0


def test_sentence_relative_offset_is_converted():
    # T1 的 start_idx=7 是相对句子的口径
    _, mentions, _, stats = parse_doc(_doc(ent_start=7))
    assert mentions[1]["start"] == 7 and stats["offset_sentence"] == 1


def test_unlocatable_entity_raises():
    with pytest.raises(ValueError):
        parse_doc(_doc(ent_start=99))


def test_type_mismatch_counted_not_fixed():
    rel = [{"relation_type": "Disease_Drug", "relation_id": "R0", "head_entity_id": "T0", "tail_entity_id": "T1"}]
    _, _, edges, stats = parse_doc(_doc(ent_start=17, relations=rel))
    assert stats["edge_type_mismatch"] == 1 and edges[0]["type"] == "Disease_Drug"


def test_run_writes_three_files(tmp_path):
    raw = tmp_path / "raw"; raw.mkdir()
    (raw / "7.json").write_text(json.dumps(_doc(ent_start=17), ensure_ascii=False), encoding="utf-8")
    stats = run(raw, tmp_path / "work")
    assert stats["docs"] == 1 and stats["edges.Drug_Disease"] == 1
    lines = (tmp_path / "work" / "mentions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["text"] == "二甲双胍"


def test_run_without_files_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        run(tmp_path, tmp_path / "work")
