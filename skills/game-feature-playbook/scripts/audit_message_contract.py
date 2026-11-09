#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_message_contract.py —— 协议契约机械对账器

解决的问题：消息指南文档与 IDL 代码长期漂移，人工核对必漏。
本脚本做两件事：

  1. 从 IDL 提取所有 struct / 字段 / CmdId；
  2. 与文档（markdown 或纯文本）做双向点名：
     - 文档提到、IDL 没有 → 幽灵字段/消息
     - IDL 有、文档没提   → 可能遗漏（WARN，需人工判断是否客户端可见）

用法：
    python audit_message_contract.py --IDL a.IDL b.IDL --doc guide.md
    python audit_message_contract.py --IDL a.IDL --doc guide.md --prefix Cmd_Act_Example
    python audit_message_contract.py --IDL a.IDL --json

退出码：0=无幽灵项；1=发现幽灵项（文档写了代码没有）。
仅依赖标准库。
"""

import argparse
import json
import os
import re
import sys

STRUCT_RE = re.compile(r'^\s*struct\s+(\w+)\s*\{?')
# 类型可能含空格（unsigned int / map<K, V>），用贪婪类型 + 最后标识符做字段名
FIELD_RE = re.compile(r'^\s*(\d+)\s+(?:optional|required)\s+(.+)\s+(\w+)\s*;\s*(.*)$')
ENUM_ITEM_RE = re.compile(r'^\s*(\w+)\s*=\s*(\d+)\s*,?(.*)$')
ENUM_BLOCK_RE = re.compile(r'^\s*enum\s+(\w+)\s*\{?')
COMMENT_FIELD_RE = re.compile(r'`?(i[A-Z]\w+|b[A-Z]\w+|s[A-Z]\w+|v[A-Z]\w+|st[A-Z]\w+|m[A-Z]\w+)`?')

# 文档里常被提到、但不是协议字段的词
NOISE = {
    'iRet', 'iActivityId', 'iMineId', 'iMapId', 'iUid', 'iZoneId', 'iRoleId',
    'iIndex', 'iCount', 'iNum', 'iTime', 'iType', 'iLevel', 'iState',
}


def parse_IDL(paths):
    structs = {}   # name -> [(tag, type, field, comment)]
    enums = {}     # name -> {item: value}
    cur_struct = None
    cur_enum = None
    depth = 0

    for path in paths:
        if not os.path.exists(path):
            print('[WARN] IDL 不存在: %s' % path, file=sys.stderr)
            continue
        with open(path, encoding='utf-8', errors='ignore') as fh:
            for line in fh:
                m = STRUCT_RE.match(line)
                if m:
                    cur_struct = m.group(1)
                    structs.setdefault(cur_struct, [])
                    cur_enum = None
                    depth = line.count('{') - line.count('}')
                    continue
                m = ENUM_BLOCK_RE.match(line)
                if m:
                    cur_enum = m.group(1)
                    enums.setdefault(cur_enum, {})
                    cur_struct = None
                    depth = line.count('{') - line.count('}')
                    continue

                if cur_struct is not None:
                    fm = FIELD_RE.match(line)
                    if fm:
                        structs[cur_struct].append(
                            (fm.group(1), fm.group(2).strip(), fm.group(3), fm.group(4).strip()))
                    depth += line.count('{') - line.count('}')
                    if depth <= 0 and '}' in line:
                        cur_struct = None
                elif cur_enum is not None:
                    em = ENUM_ITEM_RE.match(line)
                    if em:
                        enums[cur_enum][em.group(1)] = em.group(2)
                    depth += line.count('{') - line.count('}')
                    if depth <= 0 and '}' in line:
                        cur_enum = None
    return structs, enums


def audit(structs, enums, doc_text, prefix):
    doc = doc_text
    all_fields = set()
    all_structs = set()
    all_cmdids = set()

    for sname, fields in structs.items():
        if prefix and prefix not in sname:
            continue
        all_structs.add(sname)
        for _tag, _typ, fname, _c in fields:
            all_fields.add(fname)
    for ename, items in enums.items():
        for item in items:
            if prefix and prefix not in item:
                continue
            all_cmdids.add(item)

    # 1) 文档提到但 IDL 没有的结构名 / CmdId
    ghost_structs = []
    for m in re.finditer(r'\b(Cmd[A-Za-z0-9_]+)\b', doc):
        name = m.group(1)
        if prefix and prefix not in name:
            continue
        if name in all_structs or name in all_cmdids:
            continue
        if re.match(r'^CmdId', name) and name in all_cmdids:
            continue
        if re.match(r'^Cmd(Id)?_?[A-Z]', name):
            ghost_structs.append(name)

    # 2) 文档提到但 IDL 没有的字段名
    ghost_fields = []
    for name in sorted(set(COMMENT_FIELD_RE.findall(doc))):
        if name in NOISE:
            continue
        if name not in all_fields:
            ghost_fields.append(name)

    # 3) 文档中出现的 struct，逐字段检查是否被提到
    #    只查"文档确实在讲的结构"，避免全库 5000 个 struct 的噪声
    mentioned_structs = set()
    for sname in all_structs:
        if sname in doc:
            mentioned_structs.add(sname)

    missing_fields = []
    for sname in mentioned_structs:
        for _tag, _typ, fname, _c in structs.get(sname, []):
            if fname in NOISE:
                continue
            if fname not in doc:
                missing_fields.append('%s.%s' % (sname, fname))

    return {
        'ghost_structs': sorted(set(ghost_structs)),
        'ghost_fields': ghost_fields,
        'missing_fields': sorted(set(missing_fields)),
        'struct_count': len(all_structs),
        'field_count': len(all_fields),
        'cmdid_count': len(all_cmdids),
    }


def main():
    ap = argparse.ArgumentParser(description='协议契约机械对账器')
    ap.add_argument('--IDL', nargs='*', default=[], help='一个或多个 IDL 文件')
    ap.add_argument('--idl-dir', default='', help='扫描该目录下所有 .IDL（含子目录）')
    ap.add_argument('--doc', required=True, help='消息指南文档（md/txt）')
    ap.add_argument('--prefix', default='', help='只检查名字含该前缀的项，如 Cmd_Act_Example')
    ap.add_argument('--json', action='store_true', help='JSON 输出')
    ap.add_argument('--show-missing', action='store_true',
                    help='同时列出 IDL 有、文档未提的字段（默认只报幽灵项）')
    ap.add_argument('--ignore', default='', help='逗号分隔的已知可忽略字段名')
    args = ap.parse_args()

    if not args.IDL and not args.IDL_dir:
        print('[错误] 至少提供 --IDL 或 --idl-dir', file=sys.stderr)
        return 2
    if not os.path.exists(args.doc):
        print('[错误] 文档不存在: %s' % args.doc, file=sys.stderr)
        return 2

    IDL_paths = list(args.IDL)
    if args.IDL_dir:
        for dp, _dn, fn in os.walk(args.IDL_dir):
            for f in fn:
                if f.endswith('.IDL'):
                    IDL_paths.append(os.path.join(dp, f))

    doc_text = open(args.doc, encoding='utf-8', errors='ignore').read()
    structs, enums = parse_IDL(IDL_paths)
    result = audit(structs, enums, doc_text, args.prefix)

    ignore = {x.strip() for x in args.ignore.split(',') if x.strip()}
    result['ghost_fields'] = [x for x in result['ghost_fields'] if x not in ignore]
    result['ghost_structs'] = [x for x in result['ghost_structs'] if x not in ignore]
    result['missing_fields'] = [x for x in result['missing_fields'] if x.split('.')[-1] not in ignore]

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print('结构体 %d / 字段 %d / CmdId %d'
              % (result['struct_count'], result['field_count'], result['cmdid_count']))
        print()
        print('[FAIL] 文档提到、IDL 不存在（幽灵）: %d' % len(result['ghost_structs']))
        for x in result['ghost_structs']:
            print('   -', x)
        print('[FAIL] 文档提到、IDL 不存在（幽灵字段）: %d' % len(result['ghost_fields']))
        for x in result['ghost_fields']:
            print('   -', x)
        if args.show_missing:
            print('[WARN] IDL 有、文档未提（可能遗漏）: %d' % len(result['missing_fields']))
            for x in result['missing_fields'][:200]:
                print('   -', x)
            if len(result['missing_fields']) > 200:
                print('   ... 另有 %d 条' % (len(result['missing_fields']) - 200))
        else:
            print('[INFO] IDL 有、文档未提: %d（加 --show-missing 查看）'
                  % len(result['missing_fields']))

    return 1 if (result['ghost_structs'] or result['ghost_fields']) else 0


if __name__ == '__main__':
    sys.exit(main())
