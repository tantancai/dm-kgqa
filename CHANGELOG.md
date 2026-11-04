# Changelog

格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号对应 git tag（`v1.0`、`v2a`、`v2b`、`v3`）。

## [Unreleased]

### Added
- 项目文档初始化：立项说明、PRD、架构设计与版本规划、ADR 0001–0008、接口契约初稿、测试计划。
- GitHub Issue / PR 模板。
- 数据设计 `data-model.md`：图谱 Schema（含药物类别、食物 GI 分级）、六步加工流水线、人工审核方案；饮食知识来源确定为 2024 版防治指南与 2023 年版食养指南。
- V1 总设计 `iterations/v1-design.md`：四个子阶段及退出标准、冻结的模块接口、主流程与异常分支、评测目标、E2E 用例清单；设计书规则补充"总设计 + 分阶段设计"。
- V1.1 分阶段设计 `iterations/v1.1-design.md`（含附录 A 标准药物类别清单）与审核表复审提示词 `v1.1-review-prompts.md`。
- ADR-0009：药物归类依据指南核对；三张审核表改为交叉复审 + 人工裁决分歧。

### Changed
- 测试计划：关系名改为 `CONTRAINDICATED_IN`，测试集新增 `diet` / `contra` 标签，开发集与测试集分开。
- `.env.example` 新增问答链路配置项。
- data-model：审核量改为约 6～9 小时，别名表路径统一为 `kg/data/alias.csv`。
- 开发约定：不再建 Issue、里程碑和标签；每个子阶段一个分支，提交信息带任务号（如 V1.1-T2）和需求号，子阶段完成后一个 PR 合并。
