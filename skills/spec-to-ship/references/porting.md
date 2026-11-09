# 迁移时要带走的全套依赖

> 这套流程不是一个 skill 在工作，是 **7 个 skill + 7 个 agent + 2 个脚本**。
> 只拷 `spec-to-ship` 会跑到第 ③ 步就断——因为下一步根本不存在。

---

## 一、依赖清单（实测，不是猜）

| # | 名称 | 类型 | 角色 | 缺了会怎样 |
|---|---|---|---|---|
| 1 | `spec-to-ship` | skill | 入口：选档、读案、验收、交付 | 整个流程没有起点 |
| 2 | `spec-driven-dev` | skill | ③–⑥：字段映射、消息减法、实现、双 agent | 案子落不了地 |
| 3 | `game-feature-playbook` | skill | 开工五张表 + bug 模式库 + 对账脚本 | 状态/推送/上限全漏 |
| 4 | `game-message-protocol` | skill | 协议怎么写：号段、IDL 语法、注册 | 协议必撞车 |
| 5 | `game-code-review` | skill | 代码审查六路 | 质量问题自己看不见 |
| 6 | `adversarial-gameplay-acceptance` | skill | 实测验收 + 修复闭环 | 只能推演，不能验收 |
| 7 | `case-reader` | agent | 读案：正文/画板/内嵌表/评论 | ① 读案没人干 |
| 8–13 | `code-review-security` / `-pairing` / `-logic` / `-convention` / `-readability` / `-requirements` | agent ×6 | `game-code-review` 的六个专项 | 审查退化成泛泛而谈 |
| 14 | `review_setup.py` | 脚本 | 在 `game-code-review/scripts/` | 审查拿不到带行号的 diff |

**还有 1 个可选**：`code-review`（主 agent），不用也不影响。

---

## 二、怎么拷

整目录拷，保持结构：

```
~/.claude/
├── skills/
│   ├── spec-to-ship/
│   ├── spec-driven-dev/
│   ├── game-feature-playbook/          # 含 scripts/audit_message_contract.py
│   ├── game-message-protocol/
│   ├── game-code-review/               # 含 scripts/review_setup.py
│   └── adversarial-gameplay-acceptance/
└── agents/
    ├── case-reader.md
    └── code-review-*.md                # 6 个
```

### 拷贝命令

```bash
# 源机器
cd ~/.claude
tar -czf spec-to-ship.tgz \
  skills/spec-to-ship \
  skills/spec-driven-dev \
  skills/game-feature-playbook \
  skills/game-message-protocol \
  skills/game-code-review \
  skills/adversarial-gameplay-acceptance \
  agents/case-reader.md \
  agents/code-review-*.md

# 目标机器
cd ~/.claude && tar -xzf spec-to-ship.tgz
```

### 不要拷的东西

| 路径 | 为什么 |
|---|---|
| `**/.cache/` | 生成的索引，可重建（`adversarial-` 下那个 25MB 的 `history_index.jsonl`） |
| `**/__pycache__/` | 字节码 |
| 任何 `reviewsession/` | 上一次审查的临时产物 |
| 任何 `outputs/` | 历史输出 |

**实测体积**：排除缓存后整套 **约 713KB**。

---

## 三、拷完先验依赖

```bash
python skills/spec-to-ship/scripts/check_deps.py
```

它检查：
- 7 个 skill 的 `SKILL.md` 存在且 frontmatter 有 `name`
- 7 个 agent 文件存在
- `name` 与目录名一致（不一致会导致触发不到）

**任一缺失就报 FAIL 并指出缺哪个。** 不要带着缺件往下跑。

---

## 四、跨机器还要确认的三件事

| 项 | 怎么确认 | 缺了怎么办 |
|---|---|---|
| **飞书 CLI** | `lark-cli --version` | 装 CLI 并 `lark-cli auth login` |
| **飞书凭据** | 读一个真实文档试读成功 | 需要重新授权，凭据不随 skill 迁移 |
| **代码库访问** | 能 ls 到代码根 / ssh 通 | 见 `bootstrap.md` Step 1 |

**凭据永远不随 skill 迁移**——别把 token 打进包里。

---

## 五、依赖之间的调用关系（断了会在哪一步暴露）

```
① 读案 ──→ case-reader agent
② 需求 ──→ （只读上一步产出的文件）
③ 字段映射 ──→ spec-driven-dev §2
④ 协议 ──→ spec-driven-dev §3-5 + game-message-protocol
⑤ 实现 ──→ spec-driven-dev §6-7 + game-feature-playbook 五张表
⑥ 审查 ──→ game-code-review ──→ 6 个 code-review-* agent + review_setup.py
⑦ 验收 ──→ adversarial-gameplay-acceptance
```

**断点定位**：

| 在第几步卡住 | 大概率缺 |
|---|---|
| ① 读案说"不知道读什么" | `case-reader` agent |
| ④ IDL 写不出来 / 号段撞车 | `game-message-protocol` |
| ⑥ 审查只有空话、没有 file:line | 6 个 `code-review-*` agent，或 `review_setup.py` |
| ⑦ "无法实测" | `adversarial-` 的 adapter 没填（项目专属） |

---

## 六、依赖里也有项目专属文件

| 文件 | 要不要换 |
|---|---|
| `adversarial-gameplay-acceptance/references/adapter-example.md` | ✅ 换成新项目的 adapter |
| `adversarial-gameplay-acceptance/references/project-anchors.md` | ✅ 换成新项目的锚点 |
| `spec-to-ship/references/profile/example-project.md` | ✅ 换成新项目示例 |
| `spec-to-ship/references/codebase-pitfalls.md` | ✅ 换成新仓库铁律 |
| 其余 | ❌ 通用 |

---

## 七、一句话

> **入口 skill 只是调度，真本事在依赖里。**
> **先验依赖，再填画像，最后跑冒烟。**
