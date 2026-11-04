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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kg", description="dm-kgqa 图谱构建流水线")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="检查原始数据与 Neo4j 连接").set_defaults(func=cmd_check)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
