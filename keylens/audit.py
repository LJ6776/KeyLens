"""KeyLens 静态审计：在**不运行**程序的前提下，用 AST 扫出常见致命问题。

用法（项目根目录）：
    python -m keylens.audit          # 有问题时返回 1
    python keylens\\audit.py         # 同样可用

它检查的是"必然出错"的类别，而不是风格：
  1. 语法错误（compile 每个文件）
  2. 未定义的名字（NameError）
  3. 不存在的 import 目标（ImportError）
  4. 调用当前文件里已定义、但没有该方法/属性的类（AttributeError）
  5. 顶层裸的 `if __name__ == "__main__":` 保护
  6. runpy 桥接的反递归写法（re-exec 自身会无限套娃）
  7. 指向已不存在文件的路径引用

局限（必须知道）：它不做类型推断、不跨包跟踪属性、不执行分支。
它能抓住"写错了名字 / 导错了模块 / 方法不存在 / 桥接套娃"，
但抓不住逻辑错误、竞态、Win32 返回值处理不当这类问题。
"""

from __future__ import annotations

import ast
import os
import re
import sys
from typing import Dict, Iterable, List, Optional, Set, Tuple

PKG = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PKG)

BUILTINS = set(dir(__builtins__)) if isinstance(__builtins__, type(os)) else set(dir(__builtins__))
BUILTINS |= {"__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__", "__builtins__", "__debug__"}

# Win32 / ctypes 里常见但我不会去推断的属性名白名单（避免误报）。
SKIP_ATTRS = {
    "argtypes", "restype", "errcheck", "value", "contents", "raw", "from_buffer",
    "use_last_error", "c_int", "c_void_p", "POINTER", "Structure", "WINFUNCTYPE",
    "byref", "cast", "size", "sizeof", "create_unicode_buffer", "create_string_buffer",
    "WinDLL", "windll", "get_last_error", "set_last_error", "addressof", "string_at",
    "int", "str", "float", "bool", "sum", "max", "min", "len", "list", "dict", "set",
    "tuple", "range", "enumerate", "zip", "sorted", "print", "open", "round", "abs",
    "isinstance", "getattr", "setattr", "hasattr", "type", "super", "repr", "format",
    "join", "append", "extend", "get", "items", "keys", "values", "pop", "add",
    "startswith", "endswith", "split", "rsplit", "strip", "lower", "upper", "replace",
    "format_map", "isdigit", "isalpha", "read", "write", "close", "flush", "seek",
    "decode", "encode", "copy", "update", "insert", "remove", "index", "count",
    "sort", "reverse", "intersection", "difference", "union", "now", "strftime",
    "timestamp", "fromtimestamp", "makedirs", "exists", "isfile", "isdir", "listdir",
    "getsize", "dirname", "basename", "abspath", "join_bytes", "sleep", "time",
    "monotonic", "perf_counter", "randint", "uniform", "choice", "choices", "shuffle",
    "seed", "to_dict", "asdict", "fields", "now_utc",
}


class Issue:
    def __init__(self, level: str, path: str, line: int, kind: str, detail: str):
        self.level = level  # "error" | "warn"
        self.path = path
        self.line = line
        self.kind = kind
        self.detail = detail

    def render(self) -> str:
        rel = os.path.relpath(self.path, ROOT)
        return "[%s] %s:%d  %s  %s" % (self.level.upper(), rel, self.line, self.kind, self.detail)


def iter_py_files() -> List[str]:
    found: List[str] = []
    for dirpath, dirnames, filenames in os.walk(PKG):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if name.endswith(".py") and not name.startswith("audit"):
                found.append(os.path.join(dirpath, name))
    return found


# ---------------------------------------------------------------- 1. 语法


def check_syntax(path: str, source: str) -> Tuple[Optional[ast.Module], List[Issue]]:
    issues: List[Issue] = []
    try:
        tree = ast.parse(source, filename=path)
        return tree, issues
    except SyntaxError as exc:
        issues.append(Issue("error", path, exc.lineno or 0, "语法错误", str(exc)))
        return None, issues


# ---------------------------------------------------------------- 2/3/4


class Scope:
    """一个模块或函数的名字可见性。"""

    def __init__(self, parent: Optional["Scope"] = None):
        self.parent = parent
        self.names: Set[str] = set()

    def has(self, name: str) -> bool:
        scope: Optional[Scope] = self
        while scope is not None:
            if name in scope.names:
                return True
            scope = scope.parent
        return False


def collect_bound_names(node: ast.AST) -> Set[str]:
    """收集一个语句块里所有被绑定的名字（赋值、import、def、for、with…）。"""
    bound: Set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Import):
            for alias in child.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(child, ast.ImportFrom):
            for alias in child.names:
                if alias.name != "*":
                    bound.add(alias.asname or alias.name)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(child.name)
        elif isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
            bound.add(child.id)
        elif isinstance(child, ast.arg):
            bound.add(child.arg)
        elif isinstance(child, ast.ExceptHandler) and child.name:
            bound.add(child.name)
        elif isinstance(child, (ast.Global, ast.Nonlocal)):
            bound.update(child.names)
    return bound


def find_local_classes(tree: ast.Module) -> Dict[str, ast.ClassDef]:
    return {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}


def module_exports(tree: ast.Module) -> Set[str]:
    """模块顶层可见的名字（含 import 进来的、赋值的、def/class 定义的）。"""
    names: Set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.For, ast.With, ast.AsyncFor, ast.AsyncWith)):
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                    names.add(child.id)
        elif isinstance(node, ast.Try):
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                    names.add(child.id)
    return names


def build_local_module_map() -> Tuple[Dict[str, str], Dict[str, ast.Module], Dict[str, Set[str]], Set[str]]:
    """建立 模块名 -> (文件路径, AST, 顶层导出名集合)，以及"含本地类的模块名"集合。

    模块名按包内点号路径给出，例如 ``keylens.core.clipboard``；
    另外把常用的短名（``clipboard``、``tray``…）也算进去，兼容 ``from .core import clipboard``。
    """
    module_paths: Dict[str, str] = {}
    trees: Dict[str, ast.Module] = {}
    exports: Dict[str, Set[str]] = {}
    modules_with_classes: Set[str] = set()

    for dirpath, dirnames, filenames in os.walk(PKG):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, ROOT).replace(os.sep, ".")
            if rel.endswith(".__init__.py"):
                dotted = rel[: -len(".__init__.py")]
            else:
                dotted = rel[:-3]
            try:
                with open(full, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), filename=full)
            except (OSError, SyntaxError):
                continue
            module_paths[dotted] = full
            trees[dotted] = tree
            exports[dotted] = module_exports(tree)
            if any(isinstance(n, ast.ClassDef) for n in tree.body):
                modules_with_classes.add(dotted)
            # 短名别名：core.clipboard -> clipboard
            parts = dotted.split(".")
            if len(parts) >= 2:
                module_paths.setdefault(parts[-1], full)
                trees.setdefault(parts[-1], tree)
                exports.setdefault(parts[-1], exports[dotted])
    return module_paths, trees, exports, modules_with_classes


def check_cross_module_attrs(
    path: str,
    tree: ast.Module,
    module_paths: Dict[str, str],
    exports: Dict[str, Set[str]],
    modules_with_classes: Set[str],
) -> List[Issue]:
    """检查 `模块对象.属性` 是否真的存在。

    只查"基名绑定到某个本地模块"的情况（``from .core import clipboard`` 或
    ``import keylens.core.clipboard as clipboard``）。刻意**不碰**实例属性，
    这样不会对 ``obj.method()`` 误报。
    """
    issues: List[Issue] = []

    # 找出本文件里绑定到本地模块的名字
    local_module_names: Dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in exports:
                    local_module_names[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            base_parts = []
            if node.level:
                here = os.path.relpath(os.path.dirname(path), ROOT).replace(os.sep, ".")
                if here and here != ".":
                    base_parts.append(here)
            if node.module:
                base_parts.append(node.module)
            for alias in node.names:
                full = ".".join(base_parts + [alias.name])
                if full in exports:
                    local_module_names[alias.asname or alias.name] = full
                elif base_parts and ".".join(base_parts) in exports:
                    # from .core import clipboard 形式：模块对象本身
                    full = ".".join(base_parts + [alias.name])
                    if full in exports:
                        local_module_names[alias.asname or alias.name] = full

    if not local_module_names:
        return issues

    allowed = {"__file__", "__name__", "__doc__", "__all__", "__path__", "__package__"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        module_name = local_module_names.get(node.value.id)
        if not module_name:
            continue
        if node.attr in allowed:
            continue
        target_exports = exports.get(module_name, set())
        if node.attr in target_exports:
            continue
        # 基名首字母大写 => 更可能是"某个类的实例"（例如 TrayIcon），
        # 实例属性不在模块命名空间里，跳过以免误报。
        if node.value.id[:1].isupper():
            continue
        issues.append(Issue(
            "error", path, node.lineno, "跨模块属性不存在",
            "%s.%s（该模块未定义这个名字）" % (node.value.id, node.attr),
        ))
    return issues


def class_members(cls: ast.ClassDef) -> Set[str]:
    members: Set[str] = set()
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            members.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    members.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            members.add(node.target.id)
    # 继承自基类时无法推断，标记为"有基类"，调用方放宽检查。
    if cls.bases:
        members.add("*inherited*")
    return members


def check_names(path: str, tree: ast.Module) -> List[Issue]:
    issues: List[Issue] = []
    module_scope = Scope()
    module_scope.names |= collect_bound_names(tree)
    module_scope.names |= BUILTINS
    module_scope.names.add("self")

    classes = find_local_classes(tree)
    members = {name: class_members(cls) for name, cls in classes.items()}

    def visit(node: ast.AST, scope: Scope) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            inner = Scope(scope)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inner.names.add(node.name)
                for arg in list(node.args.args) + list(node.args.kwonlyargs) + list(node.args.posonlyargs):
                    inner.names.add(arg.arg)
                if node.args.vararg:
                    inner.names.add(node.args.vararg.arg)
                if node.args.kwarg:
                    inner.names.add(node.args.kwarg.arg)
            inner.names |= collect_bound_names(node)
            for child in ast.iter_child_nodes(node):
                visit(child, inner)
            return

        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if not scope.has(node.id):
                issues.append(Issue("error", path, node.lineno, "未定义的名字", "`%s`" % node.id))

        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            base = node.value.id
            attr = node.attr
            if attr not in SKIP_ATTRS:
                # 只对本文件内定义的类做属性检查；基类未知时跳过。
                for cls_name, cls_members in members.items():
                    if base == cls_name or base in ("self", "cls"):
                        if "*inherited*" not in cls_members and attr not in cls_members:
                            issues.append(
                                Issue("error", path, node.lineno, "属性不存在",
                                      "%s.%s" % (base, attr))
                            )
                        break

        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    for child in ast.iter_child_nodes(tree):
        visit(child, module_scope)
    return issues


def check_imports(path: str, tree: ast.Module) -> List[Issue]:
    """检查 from X import Y 里的 Y 是否真的存在于目标模块（仅限本包内）。"""
    issues: List[Issue] = []
    pkg_dir = os.path.dirname(path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0:
            continue  # 标准库/第三方，交给运行时
        parts = list(node.module.split(".")) if node.module else []
        base = pkg_dir
        for _ in range(max(0, node.level - 1)):
            base = os.path.dirname(base)
        target = os.path.join(base, *parts) if parts else base
        candidate = target + ".py"
        if not os.path.isfile(candidate):
            candidate = os.path.join(target, "__init__.py")
        if not os.path.isfile(candidate):
            issues.append(Issue("error", path, node.lineno, "import 目标不存在",
                                "%s%s" % ("." * node.level, node.module or "")))
            continue
        try:
            with open(candidate, encoding="utf-8") as fh:
                target_tree = ast.parse(fh.read(), filename=candidate)
        except (OSError, SyntaxError):
            continue
        exported = set(collect_bound_names(target_tree))
        exported |= {"__all__"}
        for alias in node.names:
            if alias.name == "*":
                continue
            if alias.name not in exported:
                issues.append(Issue("error", path, node.lineno, "import 的名字不存在",
                                    "from %s import %s" % (node.module or ".", alias.name)))
    return issues


# ---------------------------------------------------------------- 5. main 保护


def check_main_guard(path: str, tree: ast.Module) -> List[Issue]:
    issues: List[Issue] = []
    has_guard = False
    for node in tree.body:
        if isinstance(node, ast.If):
            src = ast.unparse(node.test) if hasattr(ast, "unparse") else ""
            if "__name__" in src and "__main__" in src:
                has_guard = True
    # 只有"作为脚本运行有意义"的文件才要求保护：有顶层可执行语句的。
    top_level_calls = [
        n for n in tree.body
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
    ]
    if top_level_calls and not has_guard:
        issues.append(Issue("warn", path, top_level_calls[0].lineno, "缺少 __main__ 保护",
                            "顶层直接执行代码，被 import 时会顺带跑一遍"))
    return issues


# ---------------------------------------------------------------- 6. runpy 桥接


def check_runpy_bridge(path: str, source: str, tree: ast.Module) -> List[Issue]:
    """re-exec 自身的桥接：run_name='__main__' 加上裸的 __main__ 保护会无限套娃。"""
    issues: List[Issue] = []
    uses_runpy = "runpy.run_module" in source
    if not uses_runpy:
        return issues
    run_name_main = bool(re.search(r"run_name\s*=\s*['\"]__main__['\"]", source))
    if not run_name_main:
        return issues
    # 桥接块必须被 `if __name__ == "__main__"` 包住，且必须用 alter_sys=True 之外
    # 的方式避免重复触发：检查桥接调用是否位于 __main__ 保护内部。
    guarded_lines: Set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            src = ast.unparse(node.test) if hasattr(ast, "unparse") else ""
            if "__name__" in src and "__main__" in src:
                for child in ast.walk(node):
                    guarded_lines.add(getattr(child, "lineno", -1))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "run_module" and node.lineno not in guarded_lines:
                issues.append(Issue(
                    "error", path, node.lineno, "runpy 桥接未加保护",
                    "run_module(run_name='__main__') 不在 __main__ 保护内，直接运行会递归重入",
                ))
    return issues


# ---------------------------------------------------------------- 7. 路径引用


def check_stale_paths(path: str, source: str) -> List[Issue]:
    """找出指向已不存在文件的引用（例如迁移后残留的旧路径）。"""
    issues: List[Issue] = []
    pattern = re.compile(r"[\w\\/.-]+\.(?:bat|ps1|py|ico|pfx|json|md)")
    for line_no, line in enumerate(source.splitlines(), start=1):
        if line.lstrip().startswith("#") and "rem " in line.lower():
            continue
        for match in pattern.finditer(line):
            token = match.group(0).strip("`'\"(),")
            if token.startswith(("http", "%", "-")):
                continue
            if not token.endswith((".bat", ".ps1", ".ico", ".pfx", ".json")):
                continue  # .py / .md 引用太杂，不做判断
            candidates = [
                os.path.join(ROOT, token),
                os.path.join(PKG, token),
                os.path.join(ROOT, token.replace("/", os.sep)),
            ]
            if any(os.path.exists(c) for c in candidates):
                continue
            issues.append(Issue("warn", path, line_no, "引用了不存在的文件", token))
    return issues


# ---------------------------------------------------------------- 主流程


def run() -> int:
    all_issues: List[Issue] = []
    files = iter_py_files()
    print("静态审计：%d 个 .py 文件，根目录 %s\n" % (len(files), ROOT))

    module_paths, _trees, exports, modules_with_classes = build_local_module_map()
    print("建立模块符号表：%d 个模块\n" % len(exports))

    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
        except OSError as exc:
            all_issues.append(Issue("error", path, 0, "无法读取", str(exc)))
            continue

        tree, issues = check_syntax(path, source)
        all_issues.extend(issues)
        if tree is None:
            continue
        all_issues.extend(check_names(path, tree))
        all_issues.extend(check_imports(path, tree))
        all_issues.extend(check_main_guard(path, tree))
        all_issues.extend(check_runpy_bridge(path, source, tree))
        all_issues.extend(check_stale_paths(path, source))
        all_issues.extend(
            check_cross_module_attrs(path, tree, module_paths, exports, modules_with_classes)
        )

    errors = [i for i in all_issues if i.level == "error"]
    warns = [i for i in all_issues if i.level == "warn"]

    for issue in errors + warns:
        print(issue.render())

    print("\n" + "=" * 60)
    print("error %d 项，warn %d 项" % (len(errors), len(warns)))
    if not errors:
        print("没有发现必然出错的问题。注意：这不等于没有 bug ——")
        print("逻辑错误、竞态、Win32 返回值处理不当，静态审计看不出来。")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(run())
