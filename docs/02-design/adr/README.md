# 架构决策记录（ADR）

每个重要的技术决定写一份，回答"当时为什么这么选"。已经采纳的 ADR 不再修改；决定变了，就新写一份，并在旧的那份上标明"已被 ADR-xxxx 取代"。

| 编号 | 决定 | 状态 |
|---|---|---|
| [0001](0001-split-gateway-and-ai-service.md) | V1 起网关与 AI 服务分成两个进程，只通过 HTTP 契约通信 | 已采纳 |
| [0002](0002-parameterized-cypher.md) | Text2Cypher 一律生成参数化 Cypher | 已采纳 |
| [0003](0003-python-first-then-go-gateway.md) | 先用 Python 实现全部功能，V3 再把网关换成 Go | 已采纳 |
| [0004](0004-llm-failover-in-ai-service.md) | 多家 LLM API 的切换放在 AI 服务，而不是网关 | 已采纳 |
| [0005](0005-uniapp-frontend.md) | 前端用 uni-app，一套代码编译成 H5 和微信小程序 | 已采纳 |
| [0006](0006-entity-set-accuracy.md) | 准确率按实体集合自动计算，不依赖 LLM 打分 | 已采纳 |
| [0007](0007-diakg-as-kg-source.md) | 以 DiaKG 为图谱主数据，指南饮食知识为补充，不使用 MIMIC | 已采纳 |
| [0008](0008-graph-neighborhood-fallback.md) | 级别 3 由多跳 Agent 改为图邻域证据回退，并借鉴引用校验和约束抽取 | 已采纳 |
| [0009](0009-review-scope-guideline-and-cross-model.md) | 药物归类改为依据指南核对；三张审核表改为"交叉复审 + 人工裁决分歧" | 已采纳 |

## 模板

```markdown
# ADR-xxxx：<决定>

- 状态：提议 / 已采纳 / 已被 ADR-yyyy 取代
- 日期：YYYY-MM-DD
- 关联：REQ-xx、Issue #n

## 背景
要解决什么问题，有哪些约束。

## 可选方案
| 方案 | 优点 | 缺点 |

## 决定
选了哪个。

## 理由与代价
为什么选它；接受了哪些代价。
```
