#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
history_search.py —— 问用户之前，先搜历史会话

治"这个问题我明明答过"：把本地全部会话归档里的**用户发言**建成索引，
按关键词检索，命中即视为已决，写进台账，不许再问。

用法:
    python history_search.py "退赛 补发 离线"        # 全部关键词都命中
    python history_search.py "935" --any             # 任一关键词命中
    python history_search.py "阵营 匹配" --limit 30
    python history_search.py --rebuild               # 强制重建索引
    python history_search.py --stats                 # 看索引覆盖范围

默认自动发现:
    ~/.claude/projects/**/*.jsonl
    ~/.hermes/sessions/**/*.json
    ~/.codex/sessions/**/*.jsonl.zst      (需 pip install zstandard)

索引缓存: <skill>/.cache/history_index.jsonl
找不到任何归档时直接退出，不报错（换团队也能用）。
仅依赖标准库；zstandard 可选。
"""
import argparse
import json
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(SKILL_DIR, ".cache")
INDEX_PATH = os.path.join(CACHE_DIR, "history_index.jsonl")
CACHE_MAX_AGE = 7 * 24 * 3600


def home(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)


def discover_sources():
    """返回 [(path, kind)]，kind in {claude, sdk, codex}"""
    out = []
    roots = [
        (home(".claude", "projects"), "claude"),
        (home(".hermes", "sessions"), "sdk"),
        (home(".codex", "sessions"), "codex"),
    ]
    for root, kind in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _, files in os.walk(root):
            if "subagents" in dirpath.replace("\\", "/"):
                continue
            for f in files:
                if kind == "codex" and f.endswith(".jsonl.zst"):
                    out.append((os.path.join(dirpath, f), kind))
                elif kind in ("claude",) and f.endswith(".jsonl"):
                    out.append((os.path.join(dirpath, f), kind))
                elif kind == "sdk" and f.endswith(".json"):
                    out.append((os.path.join(dirpath, f), kind))
    return out


def clean(t):
    if not isinstance(t, str):
        return ""
    t = t.strip()
    if not t or len(t) < 4:
        return ""
    if t.startswith("<") and t.endswith(">"):
        return ""
    if "[Request interrupted" in t and len(t) < 80:
        return ""
    return t


def iter_claude(path):
    try:
        fh = open(path, encoding="utf-8", errors="ignore")
    except OSError:
        return
    for line in fh:
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") != "user" or d.get("isSidechain"):
            continue
        c = (d.get("message") or {}).get("content")
        if isinstance(c, str):
            txt = clean(c)
        elif isinstance(c, list):
            txt = clean(" ".join(x.get("text", "") for x in c
                                 if isinstance(x, dict) and x.get("type") == "text"))
        else:
            continue
        if txt:
            yield {"ts": d.get("timestamp", ""), "cwd": d.get("cwd", ""), "text": txt}


def iter_sdk(path):
    try:
        o = json.load(open(path, encoding="utf-8", errors="ignore"))
    except Exception:
        return
    cwd = o.get("cwd", "")
    for it in o.get("transcript", []) or []:
        if it.get("role") != "user":
            continue
        c = it.get("content")
        if isinstance(c, str):
            txt = clean(c)
        elif isinstance(c, list):
            txt = clean(" ".join(x.get("text", "") for x in c
                                 if isinstance(x, dict) and x.get("type") == "text"))
        else:
            txt = ""
        if txt:
            yield {"ts": it.get("createdAt", ""), "cwd": cwd, "text": txt}


def iter_codex(path):
    try:
        import zstandard
    except ImportError:
        return
    try:
        with open(path, "rb") as fh:
            data = zstandard.ZstdDecompressor().stream_reader(fh).read().decode("utf-8", "ignore")
    except Exception:
        return
    for line in data.splitlines():
        try:
            o = json.loads(line)
        except Exception:
            continue
        p = o.get("payload", {}) or {}
        typ = o.get("type", "")
        txt = ""
        if typ == "response_item" and p.get("role") == "user":
            c = p.get("content")
            txt = clean(" ".join(x.get("text", "") for x in c if isinstance(x, dict))) \
                if isinstance(c, list) else clean(str(c))
        elif typ == "event_msg" and p.get("type") == "user_message":
            txt = clean(str(p.get("message", "")))
        if txt:
            yield {"ts": o.get("timestamp", ""), "cwd": "", "text": txt}


def build_index():
    os.makedirs(CACHE_DIR, exist_ok=True)
    srcs = discover_sources()
    if not srcs:
        print("[history_search] 未发现任何会话归档，跳过。", file=sys.stderr)
        return 0
    n = 0
    with open(INDEX_PATH, "w", encoding="utf-8") as out:
        for path, kind in srcs:
            it = iter_claude(path) if kind == "claude" else \
                iter_sdk(path) if kind == "sdk" else iter_codex(path)
            for r in it:
                out.write(json.dumps(r, ensure_ascii=False) + "\n")
                n += 1
    print("[history_search] 已建索引：%d 条用户发言，源 %d 个文件" % (n, len(srcs)),
          file=sys.stderr)
    return n


def ensure_index(rebuild=False):
    if rebuild or not os.path.exists(INDEX_PATH):
        return build_index()
    age = time.time() - os.path.getmtime(INDEX_PATH)
    if age > CACHE_MAX_AGE:
        print("[history_search] 索引已过期，重建中……", file=sys.stderr)
        return build_index()
    return None


def load_index():
    rows = []
    with open(INDEX_PATH, encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def main():
    ap = argparse.ArgumentParser(description="问用户之前先搜历史会话")
    ap.add_argument("keywords", nargs="*", help="搜索关键词，空格分隔")
    ap.add_argument("--any", action="store_true", help="任一关键词命中即可（默认全部命中）")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--days", type=int, default=0, help="只看最近 N 天；0=不限")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    ensure_index(args.rebuild)

    if args.stats:
        rows = load_index()
        if not rows:
            print("索引为空")
            return 0
        ts = sorted(r.get("ts", "") for r in rows if r.get("ts"))
        print("用户发言总数: %d" % len(rows))
        print("时间范围    : %s ~ %s" % (ts[0][:10], ts[-1][:10]))
        return 0

    if not args.keywords:
        if args.rebuild:
            return 0
        ap.print_help()
        return 1

    rows = load_index()
    if not rows:
        print("[history_search] 索引为空：没有可检索的会话归档。")
        return 0

    cutoff = ""
    if args.days > 0:
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%S",
                               time.localtime(time.time() - args.days * 86400))

    kws = [k.lower() for k in args.keywords]
    hits = []
    for r in rows:
        ts = r.get("ts", "")
        if cutoff and ts and ts < cutoff:
            continue
        t = (r.get("text") or "").lower()
        ok = any(k in t for k in kws) if args.any else all(k in t for k in kws)
        if ok:
            hits.append(r)

    hits.sort(key=lambda r: r.get("ts", ""), reverse=True)
    print("[history_search] 命中 %d 条，显示最近 %d 条\n" % (len(hits), min(len(hits), args.limit)))
    for r in hits[:args.limit]:
        text = (r.get("text") or "").replace("\n", " ")
        print("%s | %s | %s" % (r.get("ts", "")[:16], r.get("cwd", "")[-40:], text[:220]))
    if not hits:
        print("未命中——可以进入案子和配置表检索（R1–R5）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
