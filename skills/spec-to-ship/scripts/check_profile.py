#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""校验项目画像是否填实，半空的画像不许开跑。

用法:
    python check_profile.py references/profile/<项目>.md
"""
import os
import re
import sys

REQUIRED_SECTIONS = [
    "基本信息", "代码根", "构建与发布", "协议层",
    "提交与版本", "日志", "调试与测试工具", "数据与配置",
    "项目专有铁律", "换项目检查表",
]

# 明显是占位/未填的信号
PLACEHOLDER = re.compile(r"^\s*(\|\s*\|?)*\s*\|?\s*$")

# 关键字段：必须出现且后面有实质内容
KEY_FIELDS = [
    ("代码根", r"代码根"),
    ("编译命令", r"编译命令"),
    ("日志根路径", r"日志根路径"),
    ("收包证据", r"服务端收到了什么|收包证据"),
    ("回包证据", r"客户端收到了什么|回包证据"),
]


def read(p):
    with open(p, encoding="utf-8", errors="ignore") as f:
        return f.read()


def section_body(text, title):
    """取某个二级标题下的正文。"""
    m = re.search(rf"^##\s*[一二三四五六七八九十]+、\s*{re.escape(title)}.*$", text, re.M)
    if not m:
        return None
    start = m.end()
    nxt = re.search(r"^##\s", text[start:], re.M)
    end = start + nxt.start() if nxt else len(text)
    return text[start:end]


def table_rows(body):
    """取表格数据行（排除表头分隔线）。"""
    rows = []
    for ln in body.splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        # 分隔线必须含 '-'；纯空行 | | | | 不算分隔线
        if "-" in s and re.fullmatch(r"\|[\s\-:|]+\|?", s):
            continue
        rows.append(s)
    return rows


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    p = sys.argv[1]
    if not os.path.exists(p):
        print(f"画像不存在: {p}")
        return 2

    text = read(p)
    errs = []

    # 1. 章节齐全
    for sec in REQUIRED_SECTIONS:
        if section_body(text, sec) is None:
            errs.append(f"缺章节：{sec}")

    # 2. 关键字段
    for name, pat in KEY_FIELDS:
        if not re.search(pat, text):
            errs.append(f"缺关键字段：{name}")

    # 3. 表格不能只有表头（至少一行有实质内容）
    for sec in ["代码根", "构建与发布", "日志", "项目专有铁律"]:
        body = section_body(text, sec)
        if body is None:
            continue
        rows = table_rows(body)
        filled = 0
        for r in rows[1:]:          # rows[0] 是表头，不算已填
            cells = [c.strip() for c in r.strip("|").split("|")]
            if any(c and not c.startswith("<") for c in cells[1:]):
                filled += 1
        if filled == 0:
            errs.append(f"章节「{sec}」的表一行都没填")

    # 4. 换项目检查表必须全部打勾
    body = section_body(text, "换项目检查表") or ""
    unchecked = body.count("- [ ]")
    if unchecked:
        errs.append(f"换项目检查表还有 {unchecked} 项未打勾")

    # 5. 铁律至少 3 条
    body = section_body(text, "项目专有铁律") or ""
    n_rules = 0
    for r in table_rows(body):
        cells = [c.strip() for c in r.strip("|").split("|")]
        if len(cells) >= 3 and cells[1] and cells[1] not in ("铁律",):
            n_rules += 1
    if n_rules < 3:
        errs.append(f"项目铁律只有 {n_rules} 条，建议至少 3 条（填不出就先跑，踩坑后补）")

    print(f"画像：{os.path.basename(p)}")
    print(f"章节：{len(REQUIRED_SECTIONS) - sum(1 for e in errs if e.startswith('缺章节'))}/{len(REQUIRED_SECTIONS)}")
    print(f"铁律：{n_rules} 条")
    print()
    if errs:
        for e in errs:
            print(f"[FAIL] {e}")
        print()
        print(f"共 {len(errs)} 项未过 —— 画像没填实，别开跑")
        return 1
    print("全部通过，可以开跑")
    return 0


if __name__ == "__main__":
    sys.exit(main())
