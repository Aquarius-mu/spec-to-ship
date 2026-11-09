#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
靶场自测驱动（参考实现）—— 把「写探针 → 发协议 → 发 GM → 拉日志 → 比对断言」固化下来。

**本脚本不含任何团队的地址、路径、账号**，用前先接一次配置：
  1. 复制 selftest.config.example.json 为 selftest.config.json，按注释填你团队的值；
  2. 或设环境变量 SELFTEST_<大写键名>（优先级高于配置文件）；
  3. 跑 `python selftest.py env` 体检，缺什么它会直接告诉你。

如果你的靶场工具链与这里假设的完全不同（没有 ssh、没有 lua 探针、走网页 API），
**不要改这个脚本去硬套** —— 照 references/target-range-adapter.md 的六件套契约
写你自己的驱动，SKILL.md 的验收流程一字不用改。

用法（全部子命令见 `python selftest.py -h`）：
  env                                    环境体检：ssh / 工作副本 / 二进制 / 协议新鲜度
  gen  Cmd_X_CS [Cmd_Y_CS ...]           从 ProtoLua 生成填好默认值的 lua 探针
  run  <探针名> [--uid N] [--zone N]      远端跑探针，回传输出
  gm   "<GM 命令>" [--zone N]             经 GameServer 转跨服发 GM
  log  <zone 或 跨服实例> [--svc ...]      拉 GameServer / CrossServer 日志
  build                                  编译（需 --yes 才真跑）
  deploy <zone> [zone ...]               发服（需 --yes 才真跑）
  check <plan.json> <输出文件>            按断言表自动比对 PASS/FAIL
  parse <输出文件>                        把探针输出解析成 JSON（便于二次加工）

约定：
  * 探针一律落探针目录，临时探针用 tmp_ 前缀（不进版本库）。
  * 断言表见 references/selftest-plan-template.md。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# --------------------------------------------------------------------------
# CONFIG —— 全部来自外部配置，脚本内不含任何团队的地址、路径、账号
#
# 优先级: 环境变量 SELFTEST_<大写键名> > selftest.config.json > 下面的缺省值
# 查找顺序: --config 指定 > $SELFTEST_CONFIG > 脚本同目录 > 当前目录
# 模板见同目录 selftest.config.example.json
# --------------------------------------------------------------------------
DEFAULTS = {
    # 空值 = 必须自己配，不配则用到它的命令会明确报错
    "ssh": "",
    "local_root": "",
    # 必须写绝对路径: 拼进远端命令时会被单引号包住, ~ 不展开
    "remote_root": "",
    "log_root": "",
    "log_file_main": "",
    "log_file_kind": "",
    # 项目专用的 shell 片段/命令模板，留空则该子命令不可用
    "bootstrap": "",
    "resolve_host_cmd": "",
    "cross_hop": "",
    "cross_user": "",
    "probe_run_cmd": "",
    "probe_raw_cmd": "",
    "build_cmd": "",
    "deploy_cmd": "",
    # 有通用缺省值的
    "test_tools": "",
    "division_prefix": "",
    "proto_ns": "",
    "env_query": "env=d",
    "log_dir": "{log_root}/{instance}/{svc}",
    "log_svc": {"game": "GameServer", "cross": "CrossServer"},
    "default_uid": 0,
    "default_zone": 0,
    "default_cross": 0,
}

CONFIG = dict(DEFAULTS)


def load_config(path=None):
    """按优先级装载配置；找不到文件就用缺省值，必需项由各命令自己校验。"""
    cands = []
    if path:
        cands.append(path)
    if os.environ.get("SELFTEST_CONFIG"):
        cands.append(os.environ["SELFTEST_CONFIG"])
    here = os.path.dirname(os.path.abspath(__file__))
    cands.append(os.path.join(here, "selftest.config.json"))
    cands.append(os.path.join(os.getcwd(), "selftest.config.json"))

    for p in cands:
        if not p or not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            die("配置文件解析失败 %s: %s" % (p, e))
        if not isinstance(data, dict):
            die("配置文件必须是 JSON 对象: %s" % p)
        CONFIG.update(data)
        CONFIG["_source"] = p
        break

    # 环境变量优先，容器类型不参与覆盖（无法可靠反序列化）
    for key, default in DEFAULTS.items():
        env = os.environ.get("SELFTEST_" + key.upper())
        if env and not isinstance(default, (dict, list)):
            CONFIG[key] = env
    for key in ("default_uid", "default_zone", "default_cross"):
        if CONFIG.get(key):
            CONFIG[key] = int(CONFIG[key])
    return CONFIG


def require(keys):
    """命令入口校验必需配置，缺失时给出能照做的提示。"""
    missing = [k for k in keys if not CONFIG.get(k)]
    if missing:
        die("缺少配置: %s\n请在 selftest.config.json 里补齐（模板见同目录 "
            "selftest.config.example.json），或设环境变量 %s"
            % (" / ".join(missing),
               " / ".join("SELFTEST_" + k.upper() for k in missing)))

# IDL.lua 的类型码
TYPE_NAMES = {
    0: "void", 1: "bool", 2: "char", 3: "int8", 4: "uint8", 5: "int16", 6: "uint16",
    7: "int32", 8: "uint32", 9: "int64", 10: "uint64", 11: "float", 12: "double",
    13: "string", 14: "vector", 15: "map", 16: "enum", 17: "struct",
}
INT_TYPES = {2, 3, 4, 5, 6, 7, 8, 9, 10}
# --------------------------------------------------------------------------


def proto_prefix():
    """生成的 lua 里协议结构的前缀，按项目命名空间配（如 GameProto.）。"""
    ns = CONFIG.get("proto_ns")
    return (ns + ".") if ns else ""


def die(msg, code=1):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(code)


# ==========================================================================
# 一、SSH 桥
# ==========================================================================
SSH_NOISE = ("post-quantum", "store now", "openssh.com/pq", "may need to be upgraded")


def ssh(cmd, timeout=180, check=True):
    """在远端工作副本执行 shell 命令，返回 (stdout, stderr, rc)。"""
    argv = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
            CONFIG["ssh"], cmd]
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        die("ssh 超时 (%ss): %s" % (timeout, cmd))
    # 服务端 printIdl 会夹 \x00 填充, 带上会让 grep 把输出判成二进制文件
    out = proc.stdout.decode("utf-8", "replace").replace("\x00", "")
    err = "\n".join(
        line for line in proc.stderr.decode("utf-8", "replace").replace("\x00", "").splitlines()
        if not any(n in line for n in SSH_NOISE)
    )
    if check and proc.returncode != 0:
        die("远端命令失败 (rc=%d): %s\n%s" % (proc.returncode, cmd, err))
    return out, err, proc.returncode


def remote_tools_dir():
    return "%s/%s" % (CONFIG["remote_root"], CONFIG["test_tools"])


def local_tools_dir():
    return os.path.join(CONFIG["local_root"], *CONFIG["test_tools"].split("/"))


def q(s):
    """远端 shell 单引号转义。"""
    return "'" + str(s).replace("'", "'\\''") + "'"


def checked_int(v, what):
    """区服/实例号只可能是数字，挡命令注入。"""
    if not re.fullmatch(r"\d+", str(v)):
        die("%s 必须是纯数字: %r" % (what, v))
    return str(v)


def safe_name(v, what):
    """文件名/探针名白名单，挡路径穿越与 query 注入。"""
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", str(v)):
        die("%s 只允许字母数字下划线连字符: %r" % (what, v))
    return str(v)


def inner_quote(s):
    """跨机命令的二次转义：130 的 shell 还要再解析一层。"""
    for ch in ("\\", "`", '"', "$"):
        s = s.replace(ch, "\\" + ch)
    return s


def resolve_save_path(p):
    """输出路径规范化，展开 ~ 并给出绝对路径便于审计。"""
    return os.path.realpath(os.path.expanduser(p))


# ==========================================================================
# 二、ProtoLua 解析（离线拿到全部协议的字段骨架）
# ==========================================================================
class LuaStructs(object):
    """把 ProtoLua/*.lua 里 `X = IDL.IdlStruct('X')` 的定义解析成字段表。"""

    STRUCT_RE = re.compile(r"^([A-Za-z_]\w*)\s*=\s*IDL\.IdlStruct\('([\w:]+)'\)\s*$", re.M)

    def __init__(self, lua_dir):
        self.lua_dir = lua_dir
        self.fields = {}      # name -> [(field, type_spec), ...]
        self.source = {}      # name -> 文件名
        self._load()

    def _load(self):
        if not os.path.isdir(self.lua_dir):
            die("协议定义目录不存在: %s\n先在远端重新生成协议定义（见项目适配层）" % self.lua_dir)
        for fname in sorted(os.listdir(self.lua_dir)):
            if not fname.endswith(".lua"):
                continue
            path = os.path.join(self.lua_dir, fname)
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for m in self.STRUCT_RE.finditer(text):
                name = m.group(1)
                body = self._grab_definition(text, m.end(), name)
                if body is None:
                    continue
                self.fields[name] = self._parse_body(body)
                self.source[name] = fname

    @staticmethod
    def _grab_definition(text, start, name):
        """取 `NAME.Definition = { ... }` 的花括号体（跳过字符串内的花括号）。"""
        idx = text.find(name + ".Definition", start)
        if idx < 0:
            return None
        i = text.find("{", idx)
        if i < 0:
            return None
        depth, in_str, quote, j = 0, False, "", i
        while j < len(text):
            ch = text[j]
            if in_str:
                if ch == "\\":
                    j += 2
                    continue
                if ch == quote:
                    in_str = False
            elif ch in "\"'":
                in_str, quote = True, ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[i:j + 1]
            j += 1
        return None

    @staticmethod
    def _parse_body(body):
        """`{ 'a', a = {0,0,8,0}, b = {1,0,IDL.IdlVector(8),nil} }` -> [(a, spec), ...]

        注意用 split_top 而非 str.split: 类型里的 `IDL.IdlMap(8, CmdX)` 含逗号。
        """
        entries = []
        for m in re.finditer(r"([A-Za-z_]\w*)\s*=\s*\{([^{}]*)\}", body):
            parts = split_top(m.group(2))
            if len(parts) < 3:
                continue
            try:
                tag = int(parts[0])
            except ValueError:
                tag = 0
            entries.append((tag, m.group(1), normalize_type(parts[2])))
        entries.sort(key=lambda e: e[0])
        return [(name, spec) for _tag, name, spec in entries]

    def spec_of(self, name):
        return self.fields.get(name)

    def has(self, name):
        return name in self.fields


def split_top(s):
    """按顶层逗号切分（忽略括号内的逗号）。"""
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [x.strip() for x in out]


def normalize_type(t):
    t = t.strip()
    if t.isdigit():
        code = int(t)
        # 14/15/17 是 vector/map/struct 的类型码, 光有码没具体类型 = 解析不了
        if code in (14, 15, 17):
            return ("unknown", t)
        return ("scalar", code)
    m = re.match(r"IDL\.IdlVector\((.*)\)$", t)
    if m:
        return ("vector", normalize_type(m.group(1)))
    m = re.match(r"IDL\.IdlMap\((.*)\)$", t)
    if m:
        parts = split_top(m.group(1))
        if len(parts) == 2:
            return ("map", normalize_type(parts[0]), normalize_type(parts[1]))
    m = re.match(r"IDL\.IdlStruct\(['\"](\w+)['\"]\)$", t)
    if m:
        return ("struct", m.group(1))
    ns = CONFIG.get("proto_ns")
    if ns:
        t = t.replace("_G." + ns + ".", "")
    return ("struct", t.replace("_G.", ""))


def type_label(spec):
    kind = spec[0]
    if kind == "scalar":
        return TYPE_NAMES.get(spec[1], "type%d" % spec[1])
    if kind == "vector":
        return "vector<%s>" % type_label(spec[1])
    if kind == "map":
        return "map<%s,%s>" % (type_label(spec[1]), type_label(spec[2]))
    if kind == "struct":
        return spec[1]
    return "?"


def default_literal(spec, structs):
    """给标量产出一个 lua 字面量默认值。"""
    kind = spec[0]
    if kind != "scalar":
        return "0"
    code = spec[1]
    if code == 1:
        return "false"
    if code == 13:
        return '""'
    if code == 11 or code == 12:
        return "0"
    return "0"


# ==========================================================================
# 三、探针生成
# ==========================================================================
def gen_probe(structs, cmd_name, uid, out_name, max_depth=2):
    if not structs.has(cmd_name):
        die("ProtoLua 里没有结构 %s\n"
            "  可能原因: ① IDL 刚改、没跑 gen_lua.sh  ② 名字拼错" % cmd_name)
    sub = cmd_name.replace("Cmd_", "", 1)
    if sub.endswith("_CS"):
        sub = sub[:-3]
    lines = [
        "require 'TestGame'",
        "",
        "-- 由 selftest.py 生成 · 改完值后执行:",
        "--   python selftest.py run %s %d %d" % (out_name, uid, CONFIG["default_zone"]),
        "-- 结构来源: ProtoLua/%s" % structs.source.get(cmd_name, "?"),
        "",
        "local ACC = %d" % uid,
        "",
        "local req = %s%s()" % (proto_prefix(), cmd_name),
        "",
    ]
    body = _emit_fields(structs, "req", cmd_name, lines, max_depth, indent="")
    if not body:
        lines.append("-- (无入参, 直接发)")
        lines.append("")
    lines.append("")
    lines.append("dogameAndPrint(req, ACC)")
    lines.append("")
    return "\n".join(lines)


def _emit_fields(structs, prefix, struct_name, lines, depth, indent):
    """递归把一个结构的字段写成 lua 赋值；返回是否有产出。"""
    fields = structs.spec_of(struct_name)
    if fields is None:
        lines.append("%s-- [!] 结构 %s 未在 ProtoLua 找到, 手补" % (indent, struct_name))
        return False
    produced = False
    for name, spec in fields:
        produced = _emit_one(structs, "%s.%s" % (prefix, name), name, spec,
                             lines, depth, indent) or produced
    return produced


def _emit_one(structs, access, name, spec, lines, depth, indent):
    kind = spec[0]
    if kind == "scalar":
        lines.append("%s%s = %s    -- %s" % (indent, access, default_literal(spec, structs),
                                             type_label(spec)))
        return True
    if kind == "struct":
        lines.append("%s%s = %s%s()" % (indent, access, proto_prefix(), spec[1]))
        if depth > 1:
            _emit_fields(structs, access, spec[1], lines, depth - 1, indent + "    ")
        else:
            lines.append("%s--   (%s 更深层字段略, 需要就手补)" % (indent, spec[1]))
        return True
    if kind == "vector":
        inner = spec[1]
        lines.append("%s-- %s: %s" % (indent, name, type_label(spec)))
        if inner[0] == "scalar":
            lines.append("%s%s[1] = %s" % (indent, access, default_literal(inner, structs)))
        elif inner[0] == "vector":
            sub = inner[1]
            inner_lit = default_literal(sub, structs) if sub[0] == "scalar" else "{}"
            lines.append("%s%s[1] = {%s}" % (indent, access, inner_lit))
        elif inner[0] == "struct":
            elem = "_e_%s" % name
            lines.append("%slocal %s = %s%s()" % (indent, elem, proto_prefix(), inner[1]))
            if depth > 1:
                _emit_fields(structs, elem, inner[1], lines, depth - 1, indent + "    ")
            lines.append("%s%s[1] = %s" % (indent, access, elem))
        else:
            lines.append("%s%s[1] = {}" % (indent, access))
        return True
    if kind == "map":
        key, val = spec[1], spec[2]
        lines.append("%s-- %s: %s" % (indent, name, type_label(spec)))
        val_lit = "{}"
        if val[0] == "scalar":
            val_lit = default_literal(val, structs)
        elif val[0] == "struct":
            elem = "_m_%s" % name
            lines.append("%slocal %s = %s%s()" % (indent, elem, proto_prefix(), val[1]))
            if depth > 1:
                _emit_fields(structs, elem, val[1], lines, depth - 1, indent + "    ")
            val_lit = elem
        lines.append("%s%s[1] = %s    -- key=%s" % (indent, access, val_lit, type_label(key)))
        return True
    lines.append("%s-- [!] %s 类型未知, 手补" % (indent, access))
    return False


# ==========================================================================
# 四、IDL.display 输出解析
# ==========================================================================
def _to_value(raw):
    raw = raw.strip()
    if raw == "":
        return ""
    if raw == "T":
        return True
    if raw == "F":
        return False
    if re.match(r"^-?\d+$", raw):
        return int(raw)
    try:
        return float(raw)
    except ValueError:
        return raw


def parse_display(text):
    """解析探针 stdout -> {结构名: 字段树}。

    IDL.display 形如:
        iState: 3
        stSchedule: {
            iStateEndTime: 1790258400
        }
        vMember: 32, [
            { iRoleId: 1 }
        ]
    """
    result = {}
    cur_struct = None
    cur_lines = []
    for line in text.splitlines():
        m = re.match(r"^--\s*Response\s+(\w+)\s*$", line)
        if m:
            if cur_struct:
                result[cur_struct] = _parse_tree(cur_lines)
            cur_struct, cur_lines = m.group(1), []
            continue
        m = re.match(r"^--\s*Push\s+(\w+)\s*$", line)
        if m:
            if cur_struct:
                result[cur_struct] = _parse_tree(cur_lines)
            cur_struct, cur_lines = m.group(1), []
            continue
        if cur_struct is not None:
            cur_lines.append(line)
    if cur_struct:
        result[cur_struct] = _parse_tree(cur_lines)
    return result


def _parse_tree(lines):
    """把缩进文本解析成嵌套 dict / list。"""
    node, _ = _parse_block(_strip_blank(lines), 0, 0)
    return node


def _strip_blank(lines):
    out = []
    for line in lines:
        if line.strip() == "":
            continue
        depth = len(line) - len(line.lstrip("\t"))
        out.append((depth, line.strip()))
    return out


OPEN_RE = re.compile(r"^([\w\[\]\.]+):\s*(\d+)?,?\s*([\{\[])$")
KV_RE = re.compile(r"^([\w\[\]\.]+):\s?(.*)$")


def _parse_block(items, i, blk):
    """解析一个 map 块（字段所在层级 = blk），返回 (dict, next_i)。

    层级约定（IDL.display 的输出）：
        key: {            深度 blk
        \t field: 1       深度 blk+1   <- 递归时 blk 传 blk+1
        }                 深度 blk
    """
    node = {}
    while i < len(items):
        d, text = items[i]
        # 结束符由它自己那一层消费: `key: {` 的 `}` 落在 key 所在深度, 比内容浅一层
        if text.startswith("}") or text.startswith("]"):
            if d <= blk:
                return node, i + 1
            return node, i
        if d < blk:
            return node, i
        if d > blk:
            i += 1
            continue
        m = OPEN_RE.match(text)
        if m:
            key, count, brace = m.group(1), m.group(2), m.group(3)
            i += 1
            if brace == "[":
                child, i = _parse_list(items, i, blk)
                node[key] = child
                if count is not None:
                    node[key + "_count"] = int(count)
            else:
                child, i = _parse_block(items, i, blk + 1)
                node[key] = child
            continue
        mv = KV_RE.match(text)
        if mv:
            key, raw = mv.group(1), mv.group(2).strip()
            # 空容器不换行, 是单行 `key: 0, []` / `key: {}`
            m0 = re.match(r"^(\d+)?,?\s*\[\]$", raw)
            if m0:
                node[key] = []
                if m0.group(1):
                    node[key + "_count"] = int(m0.group(1))
            elif raw == "{}":
                node[key] = {}
            else:
                node[key] = _to_value(raw)
            i += 1
            continue
        i += 1
    return node, i


def _parse_list(items, i, base):
    """解析一个列表（'[' 所在层级 = base），返回 (list, next_i)。

    元素形如 base+1 层的 `{` ... base+1 层的 `}`，内容在 base+2 层。
    """
    out = []
    while i < len(items):
        d, text = items[i]
        if d < base:
            return out, i
        if d > base:
            if text.startswith("{"):
                # 元素内容在 base+2 层; 元素的 `}` 由 _parse_block 消费
                child, i = _parse_block(items, i + 1, base + 2)
                out.append(child)
                continue
            out.append(_to_value(text))
            i += 1
            continue
        if text.startswith("]"):
            return out, i + 1
        out.append(_to_value(text))
        i += 1
    return out, i


def get_field(tree, path):
    """按 'a.b[0].c' 取字段。"""
    cur = tree
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if cur is None:
            return None
        if part.startswith("["):
            i = int(part[1:-1])
            if isinstance(cur, list) and i < len(cur):
                cur = cur[i]
            else:
                return None
        else:
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return None
    return cur


# ==========================================================================
# 五、断言比对
# ==========================================================================
def _cmp(op, actual, expect):
    if op == "exists":
        return actual is not None
    if actual is None:
        return False
    if op == "==":
        return actual == expect
    if op == "!=":
        return actual != expect
    if op in (">", ">=", "<", "<="):
        try:
            a, e = float(actual), float(expect)
        except (TypeError, ValueError):
            return False
        return {">": a > e, ">=": a >= e, "<": a < e, "<=": a <= e}[op]
    if op == "len==" or op == "len>=" or op == "len>":
        try:
            n = len(actual)
        except TypeError:
            return False
        return {"len==": n == expect, "len>=": n >= expect, "len>": n > expect}[op]
    if op == "in":
        return actual in expect
    if op == "contains":
        try:
            return expect in actual
        except TypeError:
            return False
    return False


def _fmt(v, limit=70):
    """断言行里的值摘要 —— 大列表/结构只给规模，别把整屏刷掉。"""
    if isinstance(v, list):
        return "list(len=%d)" % len(v)
    if isinstance(v, dict):
        return "dict(%s)" % ",".join(list(v)[:6])
    s = repr(v)
    return s if len(s) <= limit else s[:limit] + "..."


def run_check(plan_path, out_path):
    plan_path = os.path.realpath(plan_path)
    out_path = os.path.realpath(out_path)
    with open(plan_path, encoding="utf-8") as fh:
        plan = json.load(fh)
    with open(out_path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    parsed = parse_display(text)
    rows, n_pass, n_fail, n_miss = [], 0, 0, 0
    for item in plan.get("asserts", []):
        cmd = item.get("cmd")
        tree = parsed.get(cmd)
        if tree is None:
            rows.append((item.get("id", "?"), "MISS", "回包里没有 %s" % cmd, item))
            n_miss += 1
            continue
        actual = get_field(tree, item.get("field", ""))
        ok = _cmp(item.get("op", "=="), actual, item.get("value"))
        rows.append((item.get("id", "?"), "PASS" if ok else "FAIL",
                     "%s = %s (期望 %s %s)" % (item.get("field", ""), _fmt(actual),
                                               item.get("op", "=="), _fmt(item.get("value"))),
                     item))
        n_pass += ok
        n_fail += (not ok)
    print("== 断言比对: %s ==" % plan.get("title", plan_path))
    print("来源: %s" % plan.get("source", "(未标注需求来源)"))
    for rid, status, detail, item in rows:
        mark = {"PASS": "[PASS]", "FAIL": "[FAIL]", "MISS": "[MISS]"}[status]
        print("%s %-6s %s | %s" % (mark, rid, item.get("desc", ""), detail))
    print("-" * 60)
    print("PASS=%d FAIL=%d MISS=%d / 共 %d 条" % (n_pass, n_fail, n_miss, len(rows)))
    if n_fail or n_miss:
        print(">>> 未全通过: FAIL/MISS 条目必须给证据或改断言, 不许静默跳过")
        return 1
    print(">>> 全部通过")
    return 0


# ==========================================================================
# 六、子命令
# ==========================================================================
def cmd_env(args):
    print("== 环境体检 ==")
    print("配置来源   : %s" % CONFIG.get("_source", "(无配置文件，用缺省值)"))
    missing = [k for k in ("ssh", "local_root", "remote_root") if not CONFIG.get(k)]
    if missing:
        print("缺少必需配置: %s" % " / ".join(missing))
        print("先复制 selftest.config.example.json 为 selftest.config.json 并填写，")
        print("或设环境变量 %s" % " / ".join("SELFTEST_" + k.upper() for k in missing))
        return 1
    print("ssh 目标   : %s" % CONFIG["ssh"])
    out, _, rc = ssh("echo OK && hostname && whoami", check=False)
    print("ssh 连通   : %s" % ("OK" if "OK" in out else "失败"))
    print("远端身份   : %s" % " ".join(out.split()[1:]))
    tf = remote_tools_dir()
    out, _, _ = ssh("cd %s && ls -1 2>&1 | head -8" % q(tf), check=False)
    print("探测工具   : %s" % ("\n             ".join(out.strip().splitlines()) or "缺失"))
    out, _, _ = ssh("ls -l %s/ProtoLua/*.lua 2>&1 | head -3" % q(tf), check=False)
    print("协议定义   : %s" % (out.strip() or "缺失 -> 先在远端重新生成协议定义"))
    out, _, _ = ssh("date '+%Y-%m-%d %H:%M:%S'", check=False)
    print("远端时间   : %s" % out.strip())
    print("本地副本   : %s" % CONFIG["local_root"])
    return 0


def cmd_gen(args):
    require(["test_tools", "local_root"])
    structs = LuaStructs(os.path.join(local_tools_dir(), "ProtoLua"))
    out_dir = os.path.join(local_tools_dir(), "ProtoLua")
    os.makedirs(out_dir, exist_ok=True)
    uid = args.uid or CONFIG["default_uid"]
    for cmd_name in args.cmds:
        sub = cmd_name.replace("Cmd_", "", 1)
        if sub.endswith("_CS"):
            sub = sub[:-3]
        out_name = safe_name(args.out or ("tmp_" + sub), "探针名")
        text = gen_probe(structs, cmd_name, uid, out_name, max_depth=args.depth)
        path = os.path.join(out_dir, out_name + ".lua")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print("生成 %s  (%s)" % (path, cmd_name))
        n = len([l for l in text.splitlines()
                 if l.strip().startswith("req.") and "=" in l])
        print("     待填字段 %d 个（类型注释在行尾）, 可直接改值" % n)
    print("\n下一步: 编辑探针 -> python selftest.py run <探针名> --uid <uid> --zone <zone>")
    return 0


def cmd_run(args):
    require(["test_tools", "remote_root", "probe_run_cmd"])
    uid = args.uid or CONFIG["default_uid"]
    zone = args.zone or CONFIG["default_zone"]
    if not uid or not zone:
        die("缺少 uid/zone：命令行传 --uid/--zone，或在配置里设 default_uid/default_zone")
    name = safe_name(args.probe.replace(".lua", ""), "探针名")
    if not os.path.exists(os.path.join(local_tools_dir(), "ProtoLua", name + ".lua")):
        die("探针不存在: ProtoLua/%s.lua" % name)
    cmd = CONFIG["probe_run_cmd"].format(
        tools=remote_tools_dir(), name=q(name), uid=uid, zone=zone)
    out, err, rc = ssh(cmd, timeout=args.timeout, check=False)
    if args.raw:
        print(out, end="")
    else:
        print("== 探针 %s | uid=%d zone=%d ==" % (name, uid, zone))
        print(out.rstrip())
    if err.strip():
        print("-- stderr --\n%s" % err.rstrip(), file=sys.stderr)
    if args.save:
        save_path = resolve_save_path(args.save)
        # --save 就是操作者指定输出位置，属有意设计
        with open(save_path, "w", encoding="utf-8") as fh:  # nosemgrep
            fh.write(out)
        print("\n输出已存: %s" % save_path)
    return 0 if rc == 0 else rc


def cmd_gm(args):
    require(["test_tools", "remote_root", "probe_raw_cmd"])
    zone = args.zone or CONFIG["default_zone"]
    if not zone:
        die("缺少 zone：命令行传 --zone，或在配置里设 default_zone")
    gm_req = " ".join(args.gm_cmd)
    if "&" in gm_req or "\n" in gm_req:
        die("GM 命令不能含 & 或换行（会篡改 payload 字段）")
    prefix = (CONFIG["env_query"] + "&") if CONFIG.get("env_query") else ""
    payload = "%sop=gm&req=%s&division=%s%s" % (
        prefix, gm_req, CONFIG["division_prefix"], zone)
    if args.user:
        payload += "&username=" + urllib.parse.quote(args.user)
    cmd = CONFIG["probe_raw_cmd"].format(tools=remote_tools_dir(), payload=q(payload))
    out, err, rc = ssh(cmd, timeout=args.timeout, check=False)
    print("== GM @ zone %d ==" % zone)
    print("$ %s" % gm_req)
    print(out.rstrip())
    if err.strip():
        print("-- stderr --\n%s" % err.rstrip(), file=sys.stderr)
    return rc


def _bootstrap():
    """配置里的 shell 前置片段；模板里不能整体套 q()，$ 要留给远端展开。"""
    b = CONFIG.get("bootstrap") or ""
    return b.format(root=CONFIG["remote_root"]) if b else ""


def resolve_host(svc, target):
    """按配置模板查实例所在机器（模板通常调项目自带的定位命令，需输出 IP=<addr>）。"""
    if not CONFIG.get("resolve_host_cmd"):
        return None
    target = checked_int(target, "区服/跨服实例号")
    parts = [_bootstrap(), CONFIG["resolve_host_cmd"].format(svc=svc, t=target)]
    out, _, _ = ssh(" && ".join(p for p in parts if p), check=False)
    m = re.search(r"IP=([0-9.]+)", out)
    return m.group(1) if m else None


def cmd_log(args):
    require(["test_tools", "remote_root", "log_root", "log_dir", "log_file_main", "log_file_kind"])
    svc = (CONFIG.get("log_svc") or {}).get(args.svc, args.svc)
    target = checked_int(args.target, "区服/跨服实例号")
    kind = safe_name(args.kind, "日志 kind")
    logdir = CONFIG["log_dir"].format(
        log_root=CONFIG["log_root"], instance=CONFIG["division_prefix"] + target,
        svc=svc, target=target)
    if kind == "main":
        pick = "f=%s/%s" % (logdir, CONFIG["log_file_main"].format(svc=svc))
    else:
        pick = "f=$(ls -t %s/%s 2>/dev/null | head -1)" % (
            logdir, CONFIG["log_file_kind"].format(svc=svc, kind=kind))
    inner = '%s; tail -n %d "$f"' % (pick, args.tail)
    if args.grep:
        if "'" in args.grep or "\n" in args.grep:
            die("--grep 不能含单引号或换行（多层远程引号会断）")
        inner += " | grep -E '%s'" % args.grep

    host = None if args.local else resolve_host(svc, target)
    boot = _bootstrap()
    if host is None:
        cmd, where = inner, "本机"
    else:
        # 跨机: inner 要活着穿过跳板机的 shell 到目标机, 先整层转义
        cmd = CONFIG["cross_hop"].format(
            host=host, user=CONFIG.get("cross_user") or "", inner=inner_quote(inner))
        where = host
    if boot:
        cmd = boot + " && " + cmd
    out, err, rc = ssh(cmd, check=False)
    print("== %s @ %s | %s/%s ==" % (logdir, where, svc, args.kind))
    print(out.rstrip() if out.strip() else "(空 / 文件不存在)")
    if err.strip():
        print("-- stderr --\n%s" % err.rstrip(), file=sys.stderr)
    return rc


def _gate(args, what, cmdline):
    if not args.yes:
        print("== 预演（未执行）==")
        print("将要执行: %s" % cmdline)
        print("这会%s。确认无误后加 --yes 重跑。" % what)
        return 1
    print("== 执行: %s ==" % cmdline)
    out, err, rc = ssh("cd %s && %s" % (q(CONFIG["remote_root"]), cmdline),
                       timeout=args.timeout, check=False)
    print(out.rstrip()[-4000:] if out.strip() else "(无输出)")
    if err.strip():
        print("-- stderr --\n%s" % err.rstrip()[-2000:], file=sys.stderr)
    return rc


def cmd_build(args):
    require(["remote_root", "build_cmd"])
    return _gate(args, "编译整个工程（耗时数分钟，会占用编译机）", CONFIG["build_cmd"])


def cmd_deploy(args):
    require(["remote_root", "deploy_cmd"])
    zones = " ".join(checked_int(z, "区服号") for z in args.zones)
    msg = (" -m %s" % q(args.msg)) if args.msg else ""
    return _gate(args, "重启这些实例，可能打断其他人正在用的环境",
                 CONFIG["deploy_cmd"].format(zones=zones, msg=msg))


def cmd_parse(args):
    with open(os.path.realpath(args.file), encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    print(json.dumps(parse_display(text), ensure_ascii=False, indent=2, default=str))
    return 0


# ==========================================================================
def build_parser():
    p = argparse.ArgumentParser(prog="selftest.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    p.add_argument("--config", help="配置文件路径（默认找脚本同目录/当前目录的 selftest.config.json）")

    sp = sub.add_parser("env", help="环境体检")
    sp.set_defaults(func=cmd_env)

    sp = sub.add_parser("gen", help="从 ProtoLua 生成 lua 探针")
    sp.add_argument("cmds", nargs="+", help="如 Cmd_<功能>_SetArrange_CS")
    sp.add_argument("--uid", type=int, help="探针里写死的角色 uid")
    sp.add_argument("--out", help="输出探针名（默认 tmp_<Cmd去掉前缀后缀>）")
    sp.add_argument("--depth", type=int, default=2, help="嵌套结构展开层数, 默认 2")
    sp.set_defaults(func=cmd_gen)

    sp = sub.add_parser("run", help="远端跑探针")
    sp.add_argument("probe", help="ProtoLua 下的探针名（可不带 .lua）")
    sp.add_argument("--uid", type=int)
    sp.add_argument("--zone", type=int)
    sp.add_argument("--timeout", type=int, default=120)
    sp.add_argument("--raw", action="store_true", help="原样输出")
    sp.add_argument("--save", help="把原始输出存到文件（配合 check 用）")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("gm", help="发 GM 命令（GS 侧发, 自动转跨服）")
    sp.add_argument("gm_cmd", nargs="+", help='如 <GM命令> [参数...]')
    sp.add_argument("--zone", type=int)
    sp.add_argument("--user", help="GM 操作人（只影响后台日志）")
    sp.add_argument("--timeout", type=int, default=120)
    sp.set_defaults(func=cmd_gm)

    sp = sub.add_parser("log", help="拉 GameServer / CrossServer 日志（自动定位承载机器）")
    sp.add_argument("target", help="zone 号（svc=game）或跨服实例号（svc=cross）")
    sp.add_argument("--svc", choices=["game", "cross"], default="game")
    sp.add_argument("--kind", default="main",
                    help="main / alarm / command / cross_game / mfwerror（默认 main）")
    sp.add_argument("--tail", type=int, default=50)
    sp.add_argument("--grep", help="再过滤一层（正则, 不能含单引号）")
    sp.add_argument("--local", action="store_true", help="只在 130 本机读, 不做跨机跳转")
    sp.set_defaults(func=cmd_log)

    sp = sub.add_parser("build", help="编译（需 --yes）")
    sp.add_argument("--yes", action="store_true")
    sp.add_argument("--timeout", type=int, default=1800)
    sp.set_defaults(func=cmd_build)

    sp = sub.add_parser("deploy", help="发服（需 --yes）")
    sp.add_argument("zones", nargs="+")
    sp.add_argument("-m", "--msg", help="本次更新内容（进发布记录/告警）")
    sp.add_argument("--yes", action="store_true")
    sp.add_argument("--timeout", type=int, default=1800)
    sp.set_defaults(func=cmd_deploy)

    sp = sub.add_parser("check", help="按断言表比对探针输出")
    sp.add_argument("plan", help="断言表 JSON")
    sp.add_argument("output", help="run --save 存下的输出文件")
    sp.set_defaults(func=lambda a: run_check(a.plan, a.output))

    sp = sub.add_parser("parse", help="解析探针输出为 JSON")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_parse)
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    load_config(getattr(args, "config", None))
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
