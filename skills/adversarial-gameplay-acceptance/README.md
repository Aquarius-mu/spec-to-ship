# 对抗性业务验收（adversarial-gameplay-acceptance）

> 给游戏服务端做**上线前验收**的 skill：模拟玩家跑一遍，回答四个问题 ——
> **实现有没有 bug？和策划方案差多少？能不能直接给玩家？玩家会不会踩坑？**

---

## 这是什么

一个可执行的验收程序，不是一份检查清单。两种形态一体：

| 形态 | 前提 | 证据 |
|---|---|---|
| **A 案头审计** | 无（永远可做） | 读码推演，标 `[推断]` |
| **B 靶场实跑** | 有可达环境 | **实测回包 / 状态 / 日志**，标 `[实测]` |

**有环境就必须实跑。** 跑不了要写清卡在哪，不许用"应该没问题"糊过去。

## 给谁用

- **开发**：功能写完自己验，别等人测
- **QA**：按清单逐条跑，有机械兑账
- **策划**：对照需求看实现差在哪
- **任何同学**：接入自己团队的靶场就能用

---

## 怎么开始

### 第一次用（接入自己团队，约 30 分钟）

1. **读 `SKILL.md`** —— 全文，重点看开头的「最终目的」和「验收链路」
2. **采集环境** —— 照 `references/environment-intake.md` 的九类清单，
   把靶场信息问清楚（或直接问团队里最熟的人）
3. **填锚点** —— 复制 `references/project-anchors-template.md` 为 `project-anchors.md`，填三部分：
   框架回包机制 + 靶场六件套入口 + **落地判据**（动作 → 怎么算真的生效了）
4. **配脚本**（可选）—— 复制 `scripts/selftest.config.example.json` 为 `selftest.config.json`，
   填地址 / 路径 / 实例号；或照 `references/target-range-adapter.md` 的契约写自己的驱动
5. **跑一次体检** —— `python scripts/selftest.py env`，缺什么它会直接告诉你

### 之后每次验收

直接说「验收一下这个功能」，或「模拟玩家跑一下 XX」。
SKILL.md 会自动加载，流程先过 **Step -1 需求锚定**，再走 Step 0→7。

> **清单不能手列**：脚本扫代码只能覆盖"代码里有的"，需求侧要按文档章节树穷举（章节数 = RQ 组数），
> 再双向对账（代码有文档无 = 加戏 / 文档有代码无 = 漏实现）。见「覆盖度闸门」。

> **发协议前必须先交三样**：期望表（每个字段该等于什么）/ 双视角对账 / 推送清单。
> 没有期望表的"实测通过"只是"字段非空"，`verify_report.py` 会直接打回。

> **Step 7 是修复闭环**：定位 → 改码 → 编译 → 发服 → 重测 → 直到通过。
> 报出问题不是终点。编译/发服的命令与落地判据在你的 `project-anchors.md` ⑤，
> 填了才能自动跑；没填会先问你一次并存档。

> **分档，不一刀切**：小改动 30 秒（需求出处 + 完成定义）；有案子的完整功能才走
> R-xx 清单 + 缺口分流 + 对账矩阵。流程过重本身就是问题——数据里"慢/臃肿"被抱怨 131 次。

---

## 目录结构

```
SKILL.md                      ← 主入口，全文读完
README.md                     ← 本文件
references/
  design-rationale.md         ← 每条规则的真实事故依据 + 对自身方案的辩证（先读这个）
  self-research.md            ← 提问闸门：七路检索、什么该问/不该问、提问格式
  requirement-gate.md         ← Step -1：需求锚定（分档 / 缺口分流 / 已决台账 / 对账 / 错误模式台账）
  environment-intake.md       ← 开工第 0 步：环境采集九类 + 环境指纹
  target-range-adapter.md     ← 靶场六件套契约 + 跨进程取证 + 保真度三档
  adversarial-scenarios.md    ← 十二类对抗场景卡
  multi-agent-acceptance.md   ← 多 Agent 编排（三视角 + 专职攻击者）
  feedback-path-table-template.md
  selftest-plan-template.md   ← 断言表 + 蜕变关系写法
  report-template.md          ← 案头报告模板
  selftest-report-template.md ← 靶场报告模板
  quick-checklist.md          ← 10 分钟快速版
  example-walkthrough.md      ← 教学示例（完全脱敏）
  project-anchors-template.md ← 锚点模板（填这个）
  project-anchors.md          ← ⚠️ 适配层：某团队填写版，**对外分发时删除**
  adapter-example.md             ← ⚠️ 适配层：某团队参考实现，**对外分发时删除**
scripts/
  selftest.py                 ← 靶场驱动参考实现（无任何团队默认值）
  selftest.config.example.json← 配置模板（复制成 selftest.config.json 填）
  message_map.py              ← 按正则扫出全部消息/推送/回调，生成待验清单
  message-map.example.json    ← 正则配置模板（按项目命名约定改）
  history_search.py           ← 问用户前先搜历史会话（治"这个问题我明明答过"）
  verify_report.py            ← 报告形式闸门（机械校验，未过不许交付）
  rsh.py                      ← 远程执行助手（参数/口令全走环境变量）
```

---

## 对外分发时

**方法层可分发，适配层不外发。**

| 文件 | 能对外发？ |
|---|---|
| `SKILL.md`、`README.md`、通用 `references/*.md`、`scripts/*` | ✅ |
| `references/project-anchors.md` | ❌ 含真实路径与框架行为 |
| `references/adapter-example.md` | ❌ 含 IP / 账号 / 专有命令 |
| `scripts/selftest.config.json` | ❌ 含你的地址与账号 |
| `scripts/__pycache__/` | ❌ 删掉再打包 |

打包命令见 `references/environment-intake.md` §分发。

---

## 核心理念（读不下去时的速记）

1. **结论必须携带它成立的条件** —— 换人、换机、换天能复现才算数
2. **假 bug 比真 bug 多** —— 下结论前先过「假 bug 四问」（观测 / 数据态 / 时序 / 局部性）
3. **没有标准答案就用关系验** —— 蜕变关系（双视角对称最实用）
4. **动作 ≠ 生效** —— 命令返回成功只证明参数解析通过
5. **"我跑过了"必须能指向一条证据** —— 指不出来的，就是没跑
6. **技术是手段不是目的** —— 答不上那四个问题的技术，不要用
7. **需求残缺是验收无效的根因** —— 先锚定需求，再谈跑通；带缺口验收等于白跑
8. **能自己找到的，绝不问人** —— 七路检索跑完才许开口；问用户是最后手段
8. **验收发现必须回流需求** —— 只修代码不补需求，同类 bug 会换个功能复发
9. **不许重复提问** —— 问之前先查案子、配置表、已决台账

---

## 与相邻能力的边界

| 能力 | 负责 | 不负责 |
|---|---|---|
| 需求提取 | 文档 → 结构化需求 + 开发侧缺口 | 验收 |
| 需求驱动开发 | 需求 → 实现覆盖率 100% | 玩家反馈闭环、靶场实测 |
| **本 skill** | 需求锚定 → 实现 → **玩家看到什么**；反向质检需求 | 代码质量、规范 |
| 代码审查 | 代码质量与安全 | 业务反馈闭环 |

**本 skill 不依赖任何其他 skill**：Step -1 可单独使用；团队有专用提取/开发 skill 时按顺序衔接。

> 本机可选搭档（不影响分发）：`case-reader`、`spec-to-ship`、`game-code-review`。
