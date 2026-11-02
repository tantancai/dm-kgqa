# Changelog

格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号对应 git tag（`v1.0`、`v2a`、`v2b`、`v3`）。

## [Unreleased]

### Added
- 项目文档初始化：立项说明、PRD、架构设计与版本规划、ADR 0001–0008、接口契约初稿、测试计划。
- GitHub Issue / PR 模板。
- 数据设计 `data-model.md`：图谱 Schema（含药物类别、食物 GI 分级）、六步加工流水线、人工审核方案；饮食知识来源确定为 2024 版防治指南与 2023 年版食养指南。
- V1 总设计 `iterations/v1-design.md`：四个子阶段及退出标准、冻结的模块接口、主流程与异常分支、评测目标、E2E 用例清单；设计书规则补充"总设计 + 分阶段设计"。

### Changed
- 测试计划：关系名改为 `CONTRAINDICATED_IN`，测试集新增 `diet` / `contra` 标签，开发集与测试集分开。
- `.env.example` 新增问答链路配置项。
