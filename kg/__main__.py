"""python -m kg <命令>。各步命令随 V1.1-T2～T10 逐个加入（v1.1-design 第 3.1 节）。"""
import argparse
import sys

from kg.config import DIAKG_DIR, GUIDES_DIR, load_env


def cmd_check(_args) -> int:
    """检查环境：原始数据是否就位、Neo4j 能否连上。"""
    ok = True
    n_json = len(list(DIAKG_DIR.glob("*.json")))
    n_pdf = len(list(GUIDES_DIR.glob("*.pdf")))
    print(f"DiaKG JSON: {n_json} 个（期望 41）")
    print(f"指南 PDF: {n_pdf} 个（期望 2）")
    ok &= n_json == 41 and n_pdf == 2

    env = load_env()
    from neo4j import GraphDatabase

    try:
        with GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USER"], env["NEO4J_PASSWORD"])) as driver:
            driver.verify_connectivity()  # 连不上时立即失败，避免 execute_query 重试十几秒
            info = driver.execute_query("CALL dbms.components() YIELD versions, edition RETURN versions[0] AS v, edition AS e").records[0]
            print(f"Neo4j: {info['v']} {info['e']}")
    except Exception as e:  # 连接失败只报告，不抛出
        print(f"Neo4j: 连接失败 {type(e).__name__}: {e}")
        ok = False
    print("环境检查通过" if ok else "环境检查未通过")
    return 0 if ok else 1


def cmd_load(_args) -> int:
    from kg.config import WORK
    from kg.steps import load_raw

    for k, v in sorted(load_raw.run(DIAKG_DIR, WORK).items()):
        print(f"{k}: {v}")
    return 0


def cmd_review(args) -> int:
    from pathlib import Path

    from kg import review

    try:
        if args.action == "merge":
            stats = review.merge(args.table, Path(args.file))
        else:
            _, stats = review.decide(args.table)
    except (review.ReviewError, FileNotFoundError) as e:
        print(f"失败：{e}")
        return 1
    for k, v in stats.items():
        print(f"{k}: {v}")
    if stats.get("抽检错误率超标"):
        print("一致项抽检错误率超过 5%：按 ADR-0009 把该表抽检比例提高到 30%，并换复审模型重跑")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kg", description="dm-kgqa 图谱构建流水线")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="检查原始数据与 Neo4j 连接").set_defaults(func=cmd_check)
    sub.add_parser("load", help="① 读取 DiaKG，写 kg/work/ 下三个 jsonl").set_defaults(func=cmd_load)
    rv = sub.add_parser("review", help="审核表：merge 合并复审输出；import 读回结论")
    rv_sub = rv.add_subparsers(dest="action", required=True)
    m = rv_sub.add_parser("merge", help="把复审模型的 JSONL 输出合并进审核表")
    m.add_argument("table")
    m.add_argument("file")
    rv_sub.add_parser("import", help="读回审核表，确定最终结论").add_argument("table")
    rv.set_defaults(func=cmd_review)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
