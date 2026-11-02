# 数据设计：图谱 Schema 与数据加工

- 状态：**已批准**（2026-11-02）
- 对应需求：REQ-02、REQ-04、REQ-05、REQ-06、REQ-24、REQ-25、REQ-27
- 依据：ADR-0007（数据来源）、ADR-0008（证据回退、约束抽取）、architecture.md 第二节 V1

## 1. 数据现状（DiaKG 盘点，2026-11-02）

| 项目 | 数量 |
|---|---|
| 文档 / 句子 | 41 / 3,498 |
| 去重实体 / 去重三元组 | 4,929 / 4,101 |
| 实体类型 / 关系类型 | 18 / 15 |

**各关系的数量**（头实体 → 尾实体）：

| 关系 | 数量 | 关系 | 数量 |
|---|---|---|---|
| Anatomy_Disease | 1,794 | Symptom_Disease | 305 |
| Drug_Disease | 1,506 | Amount_Drug | 237 |
| Test_items_Disease | 1,292 | Method_Drug | 220 |
| Class_Disease | 1,236 | Reason_Disease | 184 |
| ADE_Drug | 777 | Pathogenesis_Disease | 138 |
| Treatment_Disease | 388 | Frequency_Drug | 136 |
| Test_Disease | 319 | Duration_Drug | 69 |
| | | Operation_Disease / ADE_Disease | 40 / 2 |

`Test_Value`、`Level` 两类实体没有任何关系与它们相连。

**发现的问题**：

1. **同义实体没有合并**。缩写有 137 个，其中 T2DM 出现 401 次、HUA 162 次、CKD 118 次。另有大量"中文名(英文全称，缩写)"形式的实体，例如"糖尿病周围神经病(diabetic peripheral neuropathy，DPN)"。药物类名的写法也不统一，比如"磺脲类药物"和"磺脲类"、"他汀类药物"和"他汀"。
2. **"药物—疾病"关系分不清适用还是禁用**。在 1,506 条 Drug_Disease 关系对应的原句里，含"禁用"的有 107 句，"禁忌"109 句，"慎用"92 句，"不推荐"87 句，"减量"92 句，"停用"51 句。粗略估计，约三成的边其实是"禁用 / 慎用 / 有条件使用"。
3. **整句式的长实体**。超过 15 个字的实体共 362 个，集中在 Test_Value、Drug、Disease、Treatment 等类型，其中多数是"中文名(英文，缩写)"的格式，可以用规则拆开；真正的长短语主要出现在 Pathogenesis、Reason、Treatment 中。
4. **没有饮食知识**。只有第 40 份文档（超重 / 肥胖医学营养治疗）里有少量相关内容。
5. **剂量相关数据**。Amount、Frequency、Duration 三类共 442 条边，属于拒答范围（REQ-06）。

## 2. 图谱 Schema

### 2.1 节点

| 标签 | 来源 | 属性 | 说明 |
|---|---|---|---|
| `Disease` | DiaKG Disease | `name`, `aliases[]` | 疾病与并发症。"糖尿病"和"2型糖尿病"是**两个不同节点**，不合并 |
| `Drug` | DiaKG Drug | `name`, `aliases[]`, `routes[]` | `routes` 是给药方式，来自 Method_Drug，例如口服、皮下注射 |
| `Symptom` | DiaKG Symptom | `name`, `aliases[]` | |
| `Test` | DiaKG Test + Test_items | `name`, `aliases[]` | 检查项目（眼底检查）和检测指标（HbA1c）合并为一类 |
| `ADE` | DiaKG ADE | `name`, `aliases[]` | 药物不良反应 |
| `Treatment` | DiaKG Treatment + Operation + 指南饮食原则 | `name`, `aliases[]`, `category` | `category` 取值：生活方式 / 饮食 / 手术 / 其他 |
| `DrugClass` | DiaKG 中的药物类别名合并而来（新增） | `name`, `aliases[]` | 约 15 个标准类别，例如磺脲类、DPP-4 抑制剂；错别字和变体写法记作别名 |
| `Anatomy` | DiaKG Anatomy | `name` | 受累器官或部位，例如肾、眼 |
| `Reason` | DiaKG Reason | `name` | 病因或危险因素 |
| `Food` | **指南抽取（新增）** | `name`, `aliases[]`, `category`, `gi_level` | `category` 取值：主食 / 水果 / 蔬菜 / 肉蛋奶 / 饮品 / 其他；`gi_level` 取值：低 / 中 / 高 / 空，来自食养指南附录 1 表 1.1，不收具体 GI 数值 |
| `Doc` | DiaKG 文档、指南 | `id`, `title`, `source` | `source` 取值：diakg / guideline_2024 / food_guide_2023 |
| `Sentence` | 原句 | `id`, `text`, `doc_id` | 回答依据和级别 3 回退都要用到 |

### 2.2 关系

| 关系 | 方向 | 来源 | 说明 |
|---|---|---|---|
| `INDICATED_FOR` | Drug → Disease | Drug_Disease 细分 | 适用、推荐使用 |
| `CONTRAINDICATED_IN` | Drug → Disease | Drug_Disease 细分 | 禁用、禁忌 |
| `CAUTION_IN` | Drug → Disease | Drug_Disease 细分 | 慎用、需减量、需监测 |
| `MENTIONED_WITH` | Drug → Disease | Drug_Disease 细分 | 原句只是同时提到，判断不了适用还是禁用。**问答时不作为"能不能用"的依据** |
| `BELONGS_TO` | Drug → DrugClass | `drug_class.csv` | 具体药物所属类别；类别本身在 DiaKG 里的关系挂在 DrugClass 节点上 |
| `HAS_ADE` | Drug → ADE | ADE_Drug | |
| `HAS_SYMPTOM` | Disease → Symptom | Symptom_Disease | |
| `NEEDS_TEST` | Disease → Test | Test_Disease + Test_items_Disease | |
| `TREATED_BY` | Disease → Treatment | Treatment_Disease + Operation_Disease + 饮食原则 | |
| `AFFECTS` | Disease → Anatomy | Anatomy_Disease | |
| `CAUSED_BY` | Disease → Reason | Reason_Disease | |
| `RECOMMENDED_FOR` | Food → Disease | 指南抽取 | 推荐吃 |
| `LIMITED_FOR` | Food → Disease | 指南抽取 | 限量吃、要选时机 |
| `AVOIDED_IN` | Food → Disease | 指南抽取 | 避免吃 |

**每条关系都带的属性**：
- `sids[]`：出处句子的 ID，至少 1 个。同一个三元组在多句里出现时合并（ADR-0008）。
- `condition`：适用条件，可以为空，例如"eGFR<30 ml/min 时"。只有细分后的药物关系和饮食关系才有这个属性，由 LLM 从原句中摘出。
- `reviewed`：是否经过人工确认（布尔值）。

**不入库的数据**：
- Amount、Frequency、Duration（剂量相关，属于拒答范围）。
- Class_Disease（"2型"这类分型已经包含在疾病名里，重复）。
- Pathogenesis（发病机制，偏学术，对 ToC 用户价值低，而且长短语多）。
- ADE_Disease（只有 2 条）。
- Test_Value、Level（没有关系与之相连）。

这些数据所在的**原句全部保留**为 Sentence 节点，级别 3 回退时仍然可以召回到。

### 2.3 约束与索引

```cypher
// 每个实体标签：name 唯一
CREATE CONSTRAINT disease_name IF NOT EXISTS FOR (n:Disease) REQUIRE n.name IS UNIQUE;
// …Drug / DrugClass / Symptom / Test / ADE / Treatment / Anatomy / Reason / Food 同理
CREATE CONSTRAINT sentence_id IF NOT EXISTS FOR (s:Sentence) REQUIRE s.id IS UNIQUE;
// 实体链接用的全文索引：name 和 aliases 都参与匹配（中文分析器）
CREATE FULLTEXT INDEX entity_names IF NOT EXISTS
FOR (n:Disease|Drug|DrugClass|Symptom|Test|ADE|Treatment|Food) ON EACH [n.name, n.aliases]
OPTIONS {indexConfig: {`fulltext.analyzer`: 'cjk'}};
```

### 2.4 提示词用的 Schema 说明

`kg/schema.yaml` 是给 Text2Cypher 提示词用的唯一 Schema 来源，同时也是 V2a Schema 裁剪的输入。每个标签和每种关系都写一句中文语义说明，并配一个示例。例如：

```yaml
relations:
  CONTRAINDICATED_IN:
    from: Drug
    to: Disease
    desc: 该药物在此疾病或状态下禁用
    example: "二甲双胍禁用于哪些情况？ → MATCH (d:Drug {name:$drug})-[r:CONTRAINDICATED_IN]->(x) RETURN x.name, r.condition"
```

## 3. 加工流水线

所有脚本放在 `kg/` 下。每一步的输出都是**可以审查的文件**，Neo4j 由最后一步一次性构建，可以随时删库重建。

```
raw DiaKG json ──① load_raw ──▶ mentions / edges / sentences（中间文件，不入库）
                    ② normalize ──▶ alias.csv、drug_class.csv（人工确认）
                    ③ refine_drug_relations ──▶ drug_relation_labels.csv（人工审核）
两份指南 PDF ─────── ④ extract_diet ──▶ diet_triples.jsonl（人工逐条校验）
                    ⑤ build_graph ──▶ Neo4j（MERGE 幂等写入 + 约束 + 索引）
                    ⑥ stats ──▶ 图谱统计报告（附在 PR 里）
```

### ① 读取原始数据
把 DiaKG 的 JSON 拆成三张表：实体出现、三元组、句子。句子 ID 的格式为 `diakg-<doc>-<para>-<sent>`。

### ② 实体归一化
按顺序执行：
1. **括号拆分**（规则）：`中文名(英文全称，缩写)` 拆成规范名"中文名"，英文全称和缩写都记作别名。
2. **缩写映射**：第 1 步拆出的"缩写 → 中文名"对应关系会自动收集起来。剩下的高频缩写（按出现次数取前 60 个，覆盖 T2DM、HUA、CKD 等）人工补齐。例如 `T2DM → 2型糖尿病`，**不能映射到"糖尿病"**。
3. **写法统一**：先用规则生成候选，比如去掉"类药物"的"药物"后缀、统一全角半角；再按字符串相似度聚类，交给 LLM 判断"是不是同一个概念"，最后**人工确认**。
4. **药物类别**：Drug 实体中名称是类别的（含"类""抑制剂""促泌剂"等，约 200 个）移到 `DrugClass` 并合并成约 15 个标准类别；其余具体药名先按词根规则归类（如"列汀"→ DPP-4 抑制剂、"格列净"→ SGLT2 抑制剂），规则覆盖不到的交给 LLM 预标，最后人工审核，产出 `drug_class.csv`。
5. **长短语处理**：括号拆分后仍超过 20 个字的实体不建节点，只保留在原句里。

产出 `kg/alias.csv`，列为 `label, alias, canonical, source(rule|llm|manual), reviewed`。这份表同时是实体链接的别名表（REQ-02）。

### ③ "药物—疾病"关系细分（REQ-25）
- **输入**：每条 Drug_Disease 边，加上它对应的原句。
- **LLM 输出**（temperature 为 0，按 JSON Schema 约束）：`{label: INDICATED|CONTRAINDICATED|CAUTION|MENTION, condition: str|null, quote: str}`。
- **自动校验**：`quote` 必须是原句的子串，否则这条判为无效，退回人工处理。这样可以防止模型凭空编出依据。
- **人工审核**：
  - CONTRAINDICATED 和 CAUTION 两类**全部人工过一遍**，估计约 300～450 条。
  - 其余类别随机抽 20%。
  - 准确率 ≥ 95% 才算通过；不通过就修改提示词后重跑。
- 同一个"药物—疾病"对可以同时有适用和禁用两条边，比如二甲双胍和肾功能不全。两条都保留，靠 `condition` 区分；回答时两条都列出来，并提示"具体请遵医嘱"。

### ④ 饮食知识抽取（REQ-24）
- **来源**，两份指南分工：
  - 《中国糖尿病防治指南（2024 版）》"医学营养治疗"章节（中华医学会糖尿病学分会，《中华糖尿病杂志》2025 年第 17 卷第 1 期）：**原则**，以营养素和食物类别为主（碳水化合物供能比、全谷类、含糖饮料、酒精、钠盐、红肉、甜味剂等），几乎不涉及具体食物（全文"西瓜"0 次，"水果"1 次）。
  - 《成人糖尿病食养指南（2023 年版）》（国家卫生健康委办公厅，国卫办食品函〔2023〕5 号）：**具体食物**。抽取正文第三部分"食养原则和建议"（第六节除外）和附录 1"成人糖尿病患者的食物选择"，附录 1 表 1.1 的低 / 中 / 高 GI 分类写入 `Food.gi_level`。卫健委官网原链接已失效，本地文件取自高校网站转载，已核对封面、署名和生成日期（2023-01-18），SHA-256 前 16 位 `2e03cd9e06b1a1fa`。
  - 另外加上 DiaKG 第 40 份文档。
- **不抽取**：
  - 食养指南附录 3"不同地区食谱示例"：成套食谱，不是"某食物适合 / 不适合"的知识。
  - 食养指南第三部分第（六）节"食养有道，合理选择应用食药物质"：按中医证型推荐食材，需要先判断证型，接近诊断，与拒答边界（REQ-06）冲突。
  - 附录 2"常见食物交换表"：属于份量换算，接近剂量类问题，V1 不收。
- 两份指南结论不一致时，两条都保留并分别标注出处，不做裁决。
- **约束抽取**：在提示词里限定实体类型（Food、Treatment(饮食)、Disease）、关系类型（RECOMMENDED_FOR / LIMITED_FOR / AVOIDED_IN / TREATED_BY）和 3～5 条示例，每段原文调用一次 LLM。
- **自动校验**：和第 ③ 步相同，`quote` 必须是原文的子串；实体类型和关系类型不在白名单里的直接丢弃。
- **人工校验**：每一条都要审，预计 150～300 条（含 GI 分类）。审核通过后，原句作为 `Sentence` 写入，`doc_id` 指向指南。

### ⑤ 构建图谱
- 先建约束和索引。
- 节点和关系一律用 `MERGE` 写入，重复运行结果不变；`sids` 取并集。
- 构建完成后执行自检：
  - 每条关系都有 ≥ 1 个 sid。
  - 不存在剂量类数据。
  - 高频缩写全部都能链接到实体。
- 构建完成后给图谱打版本号 `kg-v1`。测试集冻结时要记录对应的图谱版本。

### ⑥ 统计报告
各标签的节点数、各关系的边数、别名数，以及各类别人工审核的数量和通过率。这份报告附在 PR 里，也作为论文"数据"一节的素材。

## 4. 哪些数据进仓库

| 数据 | 是否入库 | 理由 |
|---|---|---|
| DiaKG 原始 JSON、指南 PDF | **否**（`kg/raw/` 已忽略） | 体积大；指南原文有版权 |
| `alias.csv`、`drug_class.csv`、`drug_relation_labels.csv`、`diet_triples.jsonl` | **是** | 都是人工审核的成果，需要版本管理；体积小（预计 < 1 MB） |
| 加工脚本、`schema.yaml` | 是 | |
| Neo4j 数据文件 | 否 | 可以用脚本重建 |

入库的派生数据统一放在 `kg/data/`，并附 `kg/data/LICENSE.md`：说明数据派生自 DiaKG，许可为 CC BY-NC，写明署名和所做的修改（ADR-0007）。`diet_triples.jsonl` 里只保留单句引文，不收录指南全文。

## 5. 工期对应（architecture.md V1 排期）

| 周次 | 步骤 |
|---|---|
| 第 1 周 | ① 读取原始数据、Neo4j 环境、本文档定稿 |
| 第 2～3 周 | ② 归一化、③ 关系细分（含人工审核）、`schema.yaml`、⑤ 首次构建 |
| 第 4 周 | ④ 饮食抽取与校验、⑤ 重建、⑥ 统计报告，打 `kg-v1` |

人工审核由项目作者本人完成，均为估算，实际数量以第 ③④ 步产出为准：

| 内容 | 条数 | 方式 |
|---|---|---|
| 禁用 / 慎用边 | 约 300～400 | 全审 |
| 其余药物—疾病边 | 约 220（1,100 条的 20%） | 抽审 |
| 别名表（含 Anatomy 同义合并） | 约 150～250 | 全审 |
| 药物类别映射 `drug_class.csv` | 糖尿病用药约 150 条全审，其他抽查 | 全审 + 抽查 |
| 饮食三元组（含 GI 分类） | 约 150～300 | 全审 |

按每小时 100～150 条估算，合计约 8～11 小时，分 4～5 次安排在第 2～4 周。

## 6. 待决问题

| 问题 | 影响 | 需要谁决定 |
|---|---|---|
| ~~指南用 2020 版还是 2024 版~~ | 已定：2024 版，PDF 已放在本地数据集目录（不入库） | 已决定（2026-11-02） |
| ~~具体食物问题（"能吃西瓜吗"）要不要补第二个来源~~ | 已定：补《成人糖尿病食养指南（2023 年版）》；GI 只收低 / 中 / 高分类；食谱示例和中医食药物质一节不抽取（见第 ④ 步） | 已决定（2026-11-02） |
| ~~要不要建药物类别层级~~ | 已定：建。DiaKG 的 690 个 Drug 实体中约 200 个本身就是类别名（含"磺脉类""映岛素促泌剂"等错别字），合并为约 15 个标准类别；其余具体药名用规则（如"格列""列汀""格列净"等词根）加 LLM 预先归类，产出 `drug_class.csv`，新增关系 `(Drug)-[:BELONGS_TO]->(DrugClass)`。人工审核约 2～3 小时 | 已决定（2026-11-02） |
| ~~`Anatomy` 是否保留~~ | 已定：保留。188 个器官名在第 ② 步做同义合并，1,794 条边不细分、不审核。人工约 0.5 小时 | 已决定（2026-11-02） |
| ~~人工审核由谁做~~ | 已定：由项目作者本人审核，不能由 LLM 自审（REQ-25 要求人工抽检）。我准备 Excel 审核表（原句 / 预标签 / 对·错·存疑），存疑项提交导师 | 已决定（2026-11-02） |
