#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""校验案子流程的进度文件与产出物，不过门禁就报错。

用法:
    python check_progress.py <功能目录>

检查:
    1. PROGRESS.md 必填字段齐全
    2. 需求规格.md 的 R-xx 是否都带出处
    3. 字段映射表是否存在且无"缺"
    4. 覆盖表是否 100% 有 file:line
    5. 交付报告是否有「自证 12 问」表，且没有「只确认不修」
"""
import os
import re
import sys

REQUIRED_PROGRESS = [
    ("档位", r"档位[:：]\s*(轻|中|重)"),
    ("案子", r"案子[:：]\s*\S"),
    ("代码根", r"代码根[:：]\s*\S"),
    ("当前阶段", r"当前阶段[:：]\s*[①②③④⑤⑥⑦]"),
    ("已完成", r"已完成[:：]"),
    ("门禁", r"本阶段门禁[:：]\s*\S"),
    ("下一步", r"下一步读[:：]\s*\S"),
    ("阻塞", r"阻塞[:：]\s*\S"),
    ("更新时间", r"更新时间[:：]\s*\d{4}-\d{2}-\d{2}"),
]

R_ID = re.compile(r"^\s*[-*]?\s*(R-\d{2,})", re.M)
R_ANY = re.compile(r"R-\d{2,}")
# 一条 R-xx 是否带出处：同一行或紧跟两行内出现"来源/出处/§"
SOURCE_HINT = re.compile(r"来源|出处|§|正文|画板|内嵌表|评论|表\s*t_")


def read(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def check_progress(d):
    p = os.path.join(d, "PROGRESS.md")
    s = read(p)
    if s is None:
        return ["缺 PROGRESS.md —— 每阶段切换必须写一次"], 0
    errs = [f"PROGRESS.md 缺字段: {name}" for name, pat in REQUIRED_PROGRESS if not re.search(pat, s)]
    return errs, 1


def check_requirements(d):
    p = os.path.join(d, "需求规格.md")
    s = read(p)
    if s is None:
        return ["缺 需求规格.md"], 0
    lines = s.splitlines()
    ids = []
    no_src = []
    for i, ln in enumerate(lines):
        m = R_ID.match(ln)
        if not m:
            continue
        rid = m.group(1)
        ids.append(rid)
        window = " ".join(lines[i:i + 3])
        if not SOURCE_HINT.search(window):
            no_src.append(rid)
    errs = []
    if not ids:
        errs.append("需求规格.md 里没有任何 R-xx")
    if no_src:
        errs.append(f"{len(no_src)} 条 R-xx 没写出处: {', '.join(no_src[:8])}")
    dup = {x for x in ids if ids.count(x) > 1}
    if dup:
        errs.append(f"R-xx 编号重复: {', '.join(sorted(dup)[:8])}")
    return errs, len(ids)


def declared_skip(d, stage):
    """PROGRESS.md 里是否显式声明跳过某阶段。"""
    s = read(os.path.join(d, "PROGRESS.md")) or ""
    return bool(re.search(rf"跳过[^\n]*{stage}", s))


def check_mapping(d):
    p = os.path.join(d, "字段映射.md")
    s = read(p)
    if s is None:
        if declared_skip(d, "③"):
            return [], 0          # 已声明跳过，不算违规
        return ["缺 字段映射.md（案子有界面时必须产出；确实没有请写“跳过 ③”）"], 0
    rows = [ln for ln in s.splitlines()
            if ln.strip().startswith("|") and "---" not in ln]
    miss = [ln for ln in rows if re.search(r"\|\s*缺\s*\|\s*$", ln)]
    errs = []
    if miss:
        errs.append(f'字段映射有 {len(miss)} 行结论为"缺"，必须为 0')
    return errs, len(rows)


def check_coverage(d):
    for name in ("覆盖表.md", "交付.md"):
        p = os.path.join(d, name)
        s = read(p)
        if s is None:
            continue
        rows = [ln for ln in s.splitlines()
                if R_ANY.search(ln) and ln.strip().startswith("|")]
        if not rows:
            continue
        bad = [ln for ln in rows if not re.search(r"\.(cpp|h|IDL|py|php)\s*:\d+", ln)]
        errs = []
        if bad:
            errs.append(f"覆盖表有 {len(bad)} 行没写 file:line")
        return errs, len(rows)
    return ["缺 覆盖表（交付前必产出）"], 0


ATTEST_HEAD = re.compile(r"自证\s*12\s*问|12\s*问自证")
# "只确认不修"：出现确认/定位/根因，却没有任何修复动作词
CONFIRM_WORDS = re.compile(r"(确认是|定位到|根因是|问题在于|已确认)")
FIX_WORDS = re.compile(r"(已修|修复|改成|改为|已改|diff|commit|revision)")


def check_attestation(d):
    """交付报告必须有自证 12 问表；不许只确认不修。"""
    cands = [f for f in os.listdir(d) if f.endswith(".md") and "交付" in f]
    if not cands:
        cands = [f for f in os.listdir(d) if f.endswith(".md")]
    texts = []
    for f in cands:
        t = read(os.path.join(d, f))
        if t:
            texts.append((f, t))
    if not texts:
        return ["缺 交付报告（.md）"], 0

    errs, n = [], 0
    found_attest = False
    for name, t in texts:
        m = ATTEST_HEAD.search(t)
        if m:
            found_attest = True
            n += 1
            # 表格里 12 行是否都有内容（粗检：至少 8 行非空结论列）
            block = t[m.end():][:3000]
            rows = [l for l in block.splitlines() if l.strip().startswith("|") and "---" not in l]
            filled = [l for l in rows[1:] if l.count("|") >= 5 and l.strip("| ").split("|")[-1].strip()]
            if len(filled) < 8:
                errs.append(f"{name}: 自证表只有 {len(filled)} 行有结论，应 ≥ 8 行")
        # 只确认不修
        for line in t.splitlines():
            if CONFIRM_WORDS.search(line) and not FIX_WORDS.search(line):
                errs.append(f"{name}: 疑似「只确认不修」→ {line.strip()[:60]}")
    if not found_attest:
        errs.append("缺「自证 12 问」表——不许只给结论不给自证")
    return errs, n


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    d = sys.argv[1]
    if not os.path.isdir(d):
        print(f"目录不存在: {d}")
        return 2

    total_err = 0
    for label, fn in [("进度", check_progress), ("需求", check_requirements),
                      ("字段映射", check_mapping), ("覆盖表", check_coverage),
                      ("自证", check_attestation)]:
        errs, n = fn(d)
        mark = "PASS" if not errs else "FAIL"
        print(f"[{mark}] {label}（{n} 项）")
        for e in errs:
            print(f"    - {e}")
        total_err += len(errs)

    print()
    print("全部通过" if total_err == 0 else f"共 {total_err} 项未过")
    return 0 if total_err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
