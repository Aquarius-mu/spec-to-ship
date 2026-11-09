#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""校验整套流程的依赖是否齐全，缺件不许开跑。

用法:
    python check_deps.py            # 默认查 ~/.claude
    python check_deps.py <claude根>
"""
import os
import re
import sys

SKILLS = [
    ("spec-to-ship", "入口：选档、读案、验收、交付"),
    ("spec-driven-dev", "③–⑥：字段映射、消息减法、实现、双 agent"),
    ("game-feature-playbook", "开工五张表 + bug 模式库"),
    ("game-message-protocol", "协议号段、IDL 语法、注册"),
    ("game-code-review", "代码审查六路"),
    ("adversarial-gameplay-acceptance", "实测验收 + 修复闭环"),
]

AGENTS = [
    ("case-reader.md", "读案"),
    ("code-review-security.md", "审查·安全"),
    ("code-review-pairing.md", "审查·配对"),
    ("code-review-logic.md", "审查·逻辑"),
    ("code-review-convention.md", "审查·规范"),
    ("code-review-readability.md", "审查·可读性"),
    ("code-review-requirements.md", "审查·需求"),
]

SCRIPTS = [
    ("skills/game-feature-playbook/scripts/audit_message_contract.py", "协议↔文档对账"),
    ("skills/game-code-review/scripts/review_setup.py", "审查 diff 收集"),
]


def frontmatter_name(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            head = f.read(2000)
    except OSError:
        return None
    m = re.match(r"^---\s*\n(.*?)\n---", head, re.S)
    if not m:
        return None
    m2 = re.search(r"^name:\s*(\S+)", m.group(1), re.M)
    return m2.group(1) if m2 else None


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.claude")
    if not os.path.isdir(root):
        print(f"根目录不存在: {root}")
        return 2

    errs = []
    ok = []

    print("== 依赖 skill ==")
    for name, role in SKILLS:
        p = os.path.join(root, "skills", name, "SKILL.md")
        if not os.path.exists(p):
            errs.append(f"缺 skill: {name}（{role}）")
            print(f"  [FAIL] {name}")
            continue
        fm = frontmatter_name(p)
        if fm is None:
            errs.append(f"{name}/SKILL.md 没有合法 frontmatter")
            print(f"  [FAIL] {name} —— frontmatter 异常")
        elif fm != name:
            errs.append(f"{name}/SKILL.md 的 name 是 '{fm}'，与目录名不一致（会导致触发不到）")
            print(f"  [FAIL] {name} —— name='{fm}' 不匹配")
        else:
            ok.append(name)
            print(f"  [ OK ] {name}")

    print("== 依赖 agent ==")
    for fn, role in AGENTS:
        p = os.path.join(root, "agents", fn)
        if os.path.exists(p):
            ok.append(fn)
            print(f"  [ OK ] {fn}")
        else:
            errs.append(f"缺 agent: {fn}（{role}）")
            print(f"  [FAIL] {fn}")

    print("== 依赖脚本 ==")
    for rel, role in SCRIPTS:
        p = os.path.join(root, rel)
        if os.path.exists(p):
            ok.append(rel)
            print(f"  [ OK ] {rel}")
        else:
            errs.append(f"缺脚本: {rel}（{role}）")
            print(f"  [FAIL] {rel}")

    print("== 项目画像 ==")
    prof = os.path.join(root, "skills", "spec-to-ship", "references", "profile")
    if os.path.isdir(prof):
        files = [f for f in os.listdir(prof) if f.endswith(".md") and not f.startswith("_")]
        if files:
            print(f"  [ OK ] 已填画像: {', '.join(files)}")
        else:
            errs.append("profile/ 下只有模板，没有填好的项目画像（跑 bootstrap.md 填一份）")
            print("  [FAIL] 没有填好的画像")
    else:
        errs.append("缺 profile 目录")
        print("  [FAIL] 缺 profile 目录")

    print()
    print(f"通过 {len(ok)} 项，未过 {len(errs)} 项")
    if errs:
        for e in errs:
            print(f"  - {e}")
        return 1
    print("依赖齐全")
    return 0


if __name__ == "__main__":
    sys.exit(main())
