#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_report.py —— 对抗性验收报告的铁律机械校验器

三条铁律里"每条发现必须带 文件:行号"靠自觉容易松,本脚本做交付前的硬闸门:
报告不满足结构要求就不许交付。它不懂业务,只查形式——形式不过,内容免谈。

两种取证形态各有一套章节要求,脚本按内容自动识别:

  【案头审计】(读码推演)  环境指纹/结论/框架机制确认/需求对照/反馈路径表/发现清单/
                          对抗场景卡/需确认项/覆盖率声明
     - P0/P1/P2 段落里每条发现(每个 bullet 块)必须含 文件:行号 定位
     - 反馈路径表的数据行必须含证据定位(缺失记 WARN)
     - 覆盖率声明非空,且"审了/没审"两面都写到

  【靶场实跑】(真发消息)  环境与版本/断言表/三路取证/发现/未实测项/结论
     - 断言表的每个数据行必须有来源、有结果标记(✅/❌/⚠️/⬜)
     - 发现块必须带 文件:行号 或复现命令
     - 未实测项必须非空,且不许出现"应该没问题"这类空话

  两种形态都要求【环境指纹】非空,且至少写清 代码版本/二进制部署时间/环境名/目标实例
  中的两项——没有指纹的结论离开这台机器就不可信(见 SKILL.md 第零原则 0.2)。

用法:
    python verify_report.py 报告.md            # 自动识别形态
    python verify_report.py 报告.md --form runtime   # 强制指定
退出码: 0=通过(可有 WARN), 1=不通过(有 FAIL), 2=文件读取失败。
仅依赖 Python 标准库(3.6+)。
"""
import argparse
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REQUIRED_SECTIONS = [
    "环境指纹", "结论", "框架机制确认", "需求对照", "反馈路径表",
    "发现清单", "对抗场景卡", "需确认项", "覆盖率声明",
]

# 靶场实跑报告的必备章节(名字宽松匹配)
RUNTIME_SECTIONS = ["环境", "断言表", "三路取证", "发现", "未实测项", "结论"]
RESULT_MARKS = ("✅", "❌", "⚠️", "⬜")

# 环境指纹: 让结论"换人换机换天能不能复现"变得可回答(见 SKILL.md 第零原则 0.2)
FINGERPRINT_HEADS = ("环境指纹", "环境与版本")
FINGERPRINT_KEYS = ("代码版本", "二进制", "部署", "环境名", "目标实例", "测试角色", "数据态")
HEDGE_WORDS = ("应该没问题", "应该没问題", "大概没问题", "基本没问题", "无需验证")
PLACEHOLDERS = ("（留白）", "(留白)", "待补", "TBD", "todo", "TODO")

# 定位格式: 任意带扩展名的路径 + 冒号(中英文) + 行号(可带范围)
LOC_RE = re.compile(
    r"[\w\./\\\-]+\.\w{1,6}\s*[:：]\s*\d+(?:\s*[-–~]\s*\d+)?"
)

SEVERITY_RE = re.compile(r"^#{1,6}\s.*\bP[012]\b", re.M)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
BULLET_RE = re.compile(r"^\s*[-*]\s+")
TABLE_SEP_RE = re.compile(r"^\s*\|[\s\-:|]+\|\s*$")


def split_sections(text):
    """返回 [(heading_text, body_lines, start_lineno), ...]"""
    sections = []
    cur_head, cur_body, cur_start = "(文档开头)", [], 1
    for i, line in enumerate(text.splitlines(), 1):
        m = HEADING_RE.match(line)
        if m:
            sections.append((cur_head, cur_body, cur_start))
            cur_head, cur_body, cur_start = m.group(2).strip(), [], i
        else:
            cur_body.append(line)
    sections.append((cur_head, cur_body, cur_start))
    return sections


def bullet_blocks(body):
    """把 bullet 列表切成块: bullet 起始行 + 其后缩进/非 bullet 的延续行。"""
    blocks, cur = [], None
    for line in body:
        if BULLET_RE.match(line):
            if cur is not None:
                blocks.append(cur)
            cur = [line]
        elif cur is not None and (line.strip() == "" or line.startswith(" ")):
            cur.append(line)
        else:
            if cur is not None:
                blocks.append(cur)
                cur = None
    if cur is not None:
        blocks.append(cur)
    return blocks


def detect_form(text, forced=None):
    if forced:
        return forced
    if "靶场实跑" in text or "三路取证" in text or "断言表" in text:
        return "runtime"
    return "desk"


def check_fingerprint(sections, fails, oks):
    """环境指纹必须存在且写实——没有它，结论离开这台机器就不可信。"""
    for head, body, _ in sections:
        if not any(k in head for k in FINGERPRINT_HEADS):
            continue
        content = "\n".join(l for l in body if l.strip())
        if not content.strip():
            fails.append("「%s」为空——没有环境指纹的结论无法判断适用范围" % head.strip())
            return
        hits = [k for k in FINGERPRINT_KEYS if k in content]
        if len(hits) < 2:
            fails.append("「%s」写得太少(至少写清 代码版本/二进制部署时间/环境名/目标实例 中的两项)"
                         % head.strip())
        else:
            oks.append("环境指纹已写明 (%s)" % "、".join(hits[:4]))
        return
    fails.append("缺少「环境指纹」章节——每条结论必须携带它成立的条件"
                 "(见 SKILL.md 第零原则 0.2)")


def check_evidence(text, fails, oks):
    """两种形态通用：协议结论必须有收包证据；空回包必须给预期依据。"""
    proto = [l for l in text.splitlines() if "[协议实测]" in l or "[两者都有]" in l]
    if proto:
        if "commandstr" not in text and "recv cmd" not in text:
            fails.append("报告声称 [协议实测]，但没有 commandstr 收包日志或 recv cmd 证据"
                         "——无法证明客户端消息真的到达服务端（%d 条协议结论受影响）" % len(proto))
        else:
            oks.append("协议实测带收包证据 (%d 条)" % len(proto))

    if re.search(r"(回包|应答|response)[^\n]{0,12}(为空|是空)", text):
        if not re.search(r"(合法空|预期为空|空是预期)", text):
            fails.append("报告提到回包为空却没给预期依据——空回包必须说明为什么空，否则按 FAIL 处理")

    # 三条自曝漏点：期望表 / 双视角 / 推送
    claims_pass = re.search(r"(实测通过|逐字段对账|全部通过|字段一致)", text)
    if claims_pass and not re.search(r"(期望表|期望值|应等于|应为\s*\d)", text):
        fails.append("报告声称「实测通过/逐字段对账」，但全文没有期望值——"
                     "没有期望的'通过'只是'字段非空'，不能算验过")

    mentions_push = re.search(r"(Push|推送)", text, re.I)
    if proto and not mentions_push:
        fails.append("报告有协议结论但完全没提推送——Push 消息也必须进覆盖表")

    # 双视角：涉及对战/战报时必须有 A/B 双方的对账
    if re.search(r"(战报|胜负|攻方|守方|进攻队|防守队)", text) and not re.search(r"(双视角|双方|攻守互换|A 看到|守方看到)", text):
        fails.append("报告涉及对战/战报但没有双视角对账——A 看到的必须等于 B 看到的反面")


def check_runtime(text, sections, fails, warns, oks):
    """靶场实跑报告的校验规则。"""
    heads = [h for h, _, _ in sections]

    missing = [s for s in RUNTIME_SECTIONS if not any(s in h for h in heads)]
    if missing:
        fails.append("缺少必备章节: %s" % "、".join(missing))
    else:
        oks.append("必备章节齐全 (%d/%d)" % (len(RUNTIME_SECTIONS), len(RUNTIME_SECTIONS)))

    # 断言表: 每行要有来源 + 结果标记
    for head, body, start in sections:
        if "断言表" not in head:
            continue
        rownum, bad_src, bad_res = 0, 0, 0
        for line in body:
            s = line.strip()
            if not s.startswith("|") or TABLE_SEP_RE.match(s):
                continue
            rownum += 1
            if rownum == 1:
                continue
            cols = [c.strip() for c in s.strip("|").split("|")]
            if len(cols) < 4:
                fails.append("断言表第 %d 行列数不足(需要 #/断言/来源/结果/证据): %s"
                             % (rownum - 1, s[:60]))
                continue
            if not cols[2] or cols[2] in ("-", "—", "?"):
                bad_src += 1
            if not any(m in cols[3] for m in RESULT_MARKS):
                bad_res += 1
        if bad_src:
            fails.append("断言表有 %d 行没写来源(需求出处)——验收结论必须能反查需求" % bad_src)
        if bad_res:
            fails.append("断言表有 %d 行结果列没有标记(✅PASS/❌FAIL/⚠️环境/⬜未覆盖)" % bad_res)
        if rownum > 1 and not bad_src and not bad_res:
            oks.append("断言表 %d 条,来源与结果标记齐全" % (rownum - 1))
        break

    # 发现块: 必须有 文件:行号 或复现命令
    found_any = False
    for head, body, start in sections:
        if not re.search(r"\bP[012]\b", head):
            continue
        joined = head + "\n" + "\n".join(body)
        found_any = True
        if not LOC_RE.search(joined) and "selftest.py" not in joined:
            fails.append("发现「%s」缺少定位(文件:行号)或缺复现命令(约第 %d 行)"
                         % (head.strip()[:50], start))
    if found_any:
        oks.append("发现块均带定位或复现命令")
    else:
        warns.append("未找到 P0/P1/P2 发现条目——若确无发现, 也请写明「本轮无发现」")

    # 未实测项: 非空且不能糊
    for head, body, start in sections:
        if "未实测" not in head and "未覆盖" not in head:
            continue
        meaningful = [l for l in body
                      if l.strip() and not any(p in l for p in PLACEHOLDERS)]
        if not meaningful:
            fails.append("「未实测项」为空或只有占位——没测的部分必须逐条写明")
        else:
            oks.append("未实测项已写明")
        break

    # 空话扫描: 全文范围, 不只未实测项
    for w in HEDGE_WORDS:
        if w in text:
            fails.append("报告出现空话「%s」——没跑过就说没跑过, 不许含糊过去" % w)



def main():
    ap = argparse.ArgumentParser(description="验收报告铁律校验器")
    ap.add_argument("report", help="报告 markdown 文件路径")
    ap.add_argument("--form", choices=["auto", "desk", "runtime"], default="auto",
                    help="报告形态; 默认按内容自动识别")
    ap.add_argument("--coverage",
                    help="覆盖率清单 JSON；提供后强制校验 tested/total 与未测原因")
    args = ap.parse_args()

    try:
        # 外部路径先规范化，避免符号链接/.. 绕过
        with open(os.path.realpath(args.report), "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print("[错误] 无法读取报告: %s" % e)
        sys.exit(2)

    fails, warns, oks = [], [], []
    sections = split_sections(text)
    heads = [h for h, _, _ in sections]

    form = detect_form(text, None if args.form == "auto" else args.form)
    print("[形态] %s" % {"desk": "案头审计", "runtime": "靶场实跑"}[form])
    if form == "runtime":
        check_runtime(text, sections, fails, warns, oks)
        check_fingerprint(sections, fails, oks)
        check_evidence(text, fails, oks)
        for m in oks:
            print("[ OK ] %s" % m)
        for m in warns:
            print("[WARN] %s" % m)
        for m in fails:
            print("[FAIL] %s" % m)
        if fails:
            print("\n结论: 未通过 (%d FAIL, %d WARN) —— 修复 FAIL 后才能交付"
                  % (len(fails), len(warns)))
            sys.exit(1)
        print("\n结论: 通过 (%d WARN,交付前建议处理)" % len(warns) if warns
              else "\n结论: 通过,无告警")
        sys.exit(0)

    # 检查 1: 必备章节
    missing = [s for s in REQUIRED_SECTIONS
               if not any(s in h for h in heads)]
    if missing:
        fails.append("缺少必备章节: %s" % "、".join(missing))
    else:
        oks.append("必备章节齐全 (%d/%d)" % (len(REQUIRED_SECTIONS), len(REQUIRED_SECTIONS)))

    check_fingerprint(sections, fails, oks)
    check_evidence(text, fails, oks)

    # 检查 2: P0/P1/P2 每条发现必须带定位
    checked, located = 0, 0
    for head, body, start in sections:
        if not re.search(r"\bP[012]\b", head):
            continue
        for block in bullet_blocks(body):
            checked += 1
            joined = "\n".join(block)
            if LOC_RE.search(joined):
                located += 1
            else:
                first = block[0].strip()[:60]
                fails.append("发现缺少 文件:行号 定位 (约第 %d 行): %s"
                             % (start, first))
    if checked:
        oks.append("P0/P1/P2 发现 %d 条,%d 条带定位" % (checked, located))
    else:
        warns.append("未找到任何 P0/P1/P2 发现条目——若确无发现,也请写明'无'")

    # 检查 3: 反馈路径表数据行要有证据
    for head, body, start in sections:
        if "反馈路径表" not in head:
            continue
        rownum = 0
        for line in body:
            s = line.strip()
            if not s.startswith("|") or TABLE_SEP_RE.match(s):
                continue
            rownum += 1
            if rownum == 1:
                continue  # 表头
            if not LOC_RE.search(s) and "待验证" not in s and "无法验证" not in s:
                warns.append("反馈路径表第 %d 个数据行没有证据定位 (约第 %d 行)"
                             % (rownum, start))
        break

    # 检查 3.5: 需求对照表每一行必须挂 RQ 编号, 或显式标"加戏/范围外"
    # 治"验收发现挂不上需求"——挂不上通常是需求漏了, 而不是没问题
    for head, body, start in sections:
        if "需求对照" not in head and "需求输入闸门" not in head:
            continue
        rownum, unbound = 0, 0
        for line in body:
            s = line.strip()
            if not s.startswith("|") or TABLE_SEP_RE.match(s):
                continue
            rownum += 1
            if rownum == 1:
                continue
            key = s.strip("|").split("|")[0]
            bound = re.search(r"\bR-?\d+|RQ-?\d+", key)
            if not bound and not any(k in s for k in ("加戏", "范围外", "需求未写")):
                unbound += 1
        if unbound:
            fails.append("需求对照表有 %d 行没挂 RQ 编号(也没标加戏/范围外)——"
                         "挂不上的多半是需求漏了(约第 %d 行)" % (unbound, start))
        elif rownum > 1:
            oks.append("需求对照表 %d 行均挂到需求条目" % (rownum - 1))
        break

    # 检查 4: 覆盖率声明
    coverage_text = ""
    for head, body, _ in sections:
        if "覆盖率声明" not in head:
            continue
        content = [l for l in body if l.strip()]
        if not content:
            fails.append("覆盖率声明为空——没审的部分必须写明")
        else:
            joined = "\n".join(content)
            coverage_text = joined
            if not (("审了" in joined or "覆盖" in joined) and
                    ("没审" in joined or "未审" in joined or "未覆盖" in joined)):
                warns.append("覆盖率声明建议同时写明'审了什么'和'没审什么'两面")
        break

    # 检查 4b: 覆盖率数值必须与清单一致（提供 --coverage 时强制）
    if args.coverage:
        try:
            with open(os.path.realpath(args.coverage), "r", encoding="utf-8") as f:
                cov = json.load(f)
        except (OSError, ValueError) as e:
            fails.append("无法读取覆盖率清单: %s" % e)
            cov = None
        if cov is not None:
            total = cov.get("total")
            tested = cov.get("tested")
            items = cov.get("items", [])
            if not isinstance(total, int) or total <= 0:
                fails.append("覆盖率清单 total 必须为正整数")
            elif not isinstance(tested, int) or tested < 0 or tested > total:
                fails.append("覆盖率清单 tested 非法（0 <= tested <= total）")
            else:
                if not (str(tested) in coverage_text and str(total) in coverage_text):
                    fails.append("覆盖率声明与清单不一致：报告里必须同时出现 tested=%d 与 total=%d"
                                 % (tested, total))
                untested = [it for it in items
                            if str(it.get("status", "")).lower() != "tested"]
                no_reason = [it for it in untested if not str(it.get("reason", "")).strip()]
                if no_reason:
                    fails.append("有 %d 条未测项没写原因" % len(no_reason))
                oks.append("覆盖率清单：tested %d / total %d（未测 %d 条）"
                           % (tested, total, len(untested)))

    # 输出
    for m in oks:
        print("[ OK ] %s" % m)
    for m in warns:
        print("[WARN] %s" % m)
    for m in fails:
        print("[FAIL] %s" % m)

    if fails:
        print("\n结论: 未通过 (%d FAIL, %d WARN) —— 修复 FAIL 后才能交付" %
              (len(fails), len(warns)))
        sys.exit(1)
    print("\n结论: 通过 (%d WARN,交付前建议处理)" % len(warns) if warns
          else "\n结论: 通过,无告警")
    sys.exit(0)


if __name__ == "__main__":
    main()
