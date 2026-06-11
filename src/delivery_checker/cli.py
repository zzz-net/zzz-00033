from __future__ import annotations

import argparse
import getpass
import os
import sys
from typing import List, Optional

from .config import CheckRules, ConfigError, parse_rules_file
from .models import (
    Issue,
    IssueType,
    ReviewStatus,
    ISSUE_TYPE_LABELS,
    REVIEW_STATUS_LABELS,
)
from .report import export_report
from .scanner import DirectoryNotFoundError, scan_directory
from .state import (
    BatchState,
    DuplicateScanError,
    EmptyUndoHistoryError,
    RulesMismatchError,
    StateError,
    create_or_resume_batch,
    list_batches,
)


C_RESET = "\033[0m"
C_RED = "\033[91m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_BLUE = "\033[94m"
C_MAGENTA = "\033[95m"
C_CYAN = "\033[96m"
C_GRAY = "\033[90m"
C_BOLD = "\033[1m"

TYPE_COLORS = {
    IssueType.MISSING: C_RED,
    IssueType.NAMING: C_YELLOW,
    IssueType.EXPIRED: C_MAGENTA,
    IssueType.DUPLICATE: C_YELLOW,
    IssueType.UNTRACKED: C_BLUE,
}

STATUS_COLORS = {
    ReviewStatus.PENDING: C_GRAY,
    ReviewStatus.PASSED: C_GREEN,
    ReviewStatus.IGNORED: C_GRAY,
    ReviewStatus.TODO: C_RED,
}


def _color(text: str, color: str, enable: bool = True) -> str:
    if not enable:
        return text
    return f"{color}{text}{C_RESET}"


def _use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _safe_print(*args, **kwargs) -> None:
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        fp = kwargs.get("file", sys.stdout)
        new_args = []
        for a in args:
            if isinstance(a, str):
                a = a.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
                    sys.stdout.encoding or "utf-8", errors="replace"
                )
            new_args.append(a)
        print(*new_args, **kwargs)


def _print_banner() -> None:
    color = _use_color()
    _safe_print()
    line = _color("=" * 60, C_CYAN, color)
    title_text = "本地资料包交付检查工具"
    try:
        "📦".encode(sys.stdout.encoding or "ascii")
        title_text = "📦 " + title_text
    except (UnicodeEncodeError, LookupError):
        title_text = "[*] " + title_text
    title = _color(title_text, C_BOLD + C_CYAN, color)
    _safe_print(line)
    _safe_print(f"  {title}")
    _safe_print(line)
    _safe_print()


def _get_base_dir() -> str:
    return os.getcwd()


def _prompt(message: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        result = input(f"{message}{suffix}: ").strip()
    except EOFError:
        result = ""
    return result if result else default


def _confirm(message: str, default_no: bool = True) -> bool:
    default = "y/N" if default_no else "Y/n"
    while True:
        ans = input(f"{message} ({default}): ").strip().lower()
        if not ans:
            return not default_no
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("请输入 y 或 n")


def _print_issue_table(issues: List[Issue], filters: Optional[set] = None) -> None:
    color = _use_color()
    if not issues:
        print(_color("（无问题）", C_GRAY, color))
        return
    print(f"{'#':>4}  {'类型':<8}  {'状态':<6}  路径 / 描述")
    print(_color("-" * 80, C_GRAY, color))
    for idx, issue in enumerate(issues, 1):
        if filters and issue.status.value not in filters:
            continue
        type_text = ISSUE_TYPE_LABELS.get(issue.type, issue.type.value)
        status_text = REVIEW_STATUS_LABELS.get(issue.status, issue.status.value)
        type_col = TYPE_COLORS.get(issue.type, "")
        status_col = STATUS_COLORS.get(issue.status, "")
        num = _color(f"{idx:>4}", C_GRAY, color)
        t = _color(f"{type_text:<8}", type_col, color)
        s = _color(f"{status_text:<6}", status_col, color)
        print(f"{num}  {t}  {s}  {issue.path}")
        if issue.message:
            print(f"      {_color('', C_GRAY, color)}{_color(issue.message, C_GRAY, color)}")


def _get_default_reviewer() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    env_user = os.environ.get("DELIVERY_CHECKER_REVIEWER", "")
    return env_user or user or "unknown"


def cmd_scan(args: argparse.Namespace) -> int:
    color = _use_color()
    rules_path = os.path.abspath(args.rules)
    data_dir = os.path.abspath(args.data_dir)

    try:
        rules = parse_rules_file(rules_path)
    except ConfigError as e:
        print(_color(f"❌ 配置格式错误: {e}", C_RED, color), file=sys.stderr)
        return 2

    base_dir = _get_base_dir()

    try:
        state, action = create_or_resume_batch(
            base_dir=base_dir,
            rules=rules,
            data_dir=data_dir,
            force_rescan=args.force,
            merge=not args.no_merge,
        )
    except (DuplicateScanError, RulesMismatchError) as e:
        print(_color(f"⚠️  {e}", C_YELLOW, color), file=sys.stderr)
        return 3
    except StateError as e:
        print(_color(f"❌ 状态错误: {e}", C_RED, color), file=sys.stderr)
        return 4

    try:
        issues = scan_directory(rules, data_dir)
    except DirectoryNotFoundError as e:
        print(_color(f"❌ {e}", C_RED, color), file=sys.stderr)
        print(_color("   （注意：扫描失败不会清除已有批次状态）", C_GRAY, color), file=sys.stderr)
        return 1

    allow_merge = "续办" in action or "重新扫描" in action
    state.set_issues(issues, allow_merge=allow_merge)
    state.save(base_dir)

    total = len(issues)
    pending = sum(1 for i in issues if i.status == ReviewStatus.PENDING)
    passed = sum(1 for i in issues if i.status == ReviewStatus.PASSED)
    ignored = sum(1 for i in issues if i.status == ReviewStatus.IGNORED)
    todo = sum(1 for i in issues if i.status == ReviewStatus.TODO)

    print(_color(f"✅ {action}", C_GREEN, color))
    print(f"   批次名称: {_color(rules.batch_name, C_BOLD, color)}")
    print(f"   资料目录: {data_dir}")
    print(f"   规则文件: {rules_path}")
    print()
    print(_color("📊 扫描结果统计:", C_BOLD, color))
    print(f"   总计问题: {total}   "
          f"{_color('待复核', STATUS_COLORS[ReviewStatus.PENDING], color)}: {pending}   "
          f"{_color('通过', STATUS_COLORS[ReviewStatus.PASSED], color)}: {passed}   "
          f"{_color('忽略', STATUS_COLORS[ReviewStatus.IGNORED], color)}: {ignored}   "
          f"{_color('待补充', STATUS_COLORS[ReviewStatus.TODO], color)}: {todo}")
    print()
    print(_color("使用以下命令继续:", C_CYAN, color))
    print(f"   {_color(f'python -m delivery_checker review {rules.batch_name}', C_BOLD, color)}   交互式复核")
    print(f"   {_color(f'python -m delivery_checker mark {rules.batch_name} ...', C_BOLD, color)}    批量标记")
    print(f"   {_color(f'python -m delivery_checker export {rules.batch_name}', C_BOLD, color)}   导出报告")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    batch_name = args.batch_name

    if not BatchState.exists(base_dir, batch_name):
        print(_color(f"❌ 批次不存在: {batch_name}", C_RED, color), file=sys.stderr)
        return 1
    state = BatchState.load(base_dir, batch_name)

    reviewer = args.reviewer or _get_default_reviewer()
    print(f"当前处理人: {_color(reviewer, C_CYAN, color)}")

    while True:
        issues = state.get_sorted_issues()
        pending = [i for i in issues if i.status == ReviewStatus.PENDING]

        print()
        print(_color(f"📋 批次「{state.batch_name}」问题概览", C_BOLD, color))
        print(f"   总数: {len(issues)}  待复核: {len(pending)}")
        print()

        filter_set = None
        if args.filter:
            filter_set = set(args.filter.split(","))

        if args.all:
            display_issues = issues
        else:
            display_issues = pending or issues
        _print_issue_table(display_issues, filter_set)
        print()
        print(_color("可用命令:", C_BOLD, color))
        print("  <编号>            标记指定问题（输入序号，如: 3）")
        print("  a / all           标记全部待复核")
        print("  u / undo          撤销上一步复核")
        print("  l / list          显示全部问题（含已处理）")
        print("  e / export <文件> 导出报告（如 report.html）")
        print("  q / quit          退出（自动保存）")
        print()

        try:
            raw = input(_color("复核命令> ", C_CYAN, color)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not raw:
            continue
        parts = raw.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("q", "quit", "exit"):
            break
        elif cmd in ("l", "list"):
            args.all = True
            continue
        elif cmd in ("u", "undo"):
            try:
                issue = state.undo_last()
                state.save(base_dir)
                print(_color(f"↩️  已撤销: {issue.path} "
                             f"(恢复为 {REVIEW_STATUS_LABELS[issue.status]})",
                             C_GREEN, color))
            except EmptyUndoHistoryError as e:
                print(_color(f"⚠️  {e}", C_YELLOW, color))
                print(_color("   （当前没有可撤销的复核记录）", C_GRAY, color))
            continue
        elif cmd in ("e", "export"):
            output = arg or f"{state.batch_name}_report.html"
            try:
                saved = export_report(state, output)
                print(_color(f"📄 报告已导出: {saved}", C_GREEN, color))
            except Exception as e:
                print(_color(f"❌ 导出失败: {e}", C_RED, color), file=sys.stderr)
            continue
        elif cmd in ("a", "all"):
            mark_all_pending(state, base_dir, reviewer, color)
            continue

        if cmd.isdigit():
            idx = int(cmd) - 1
            display_list = pending if not args.all else issues
            if filter_set:
                display_list = [i for i in display_list if i.status.value in filter_set]
            if 0 <= idx < len(display_list):
                target = display_list[idx]
                do_mark_one(state, base_dir, target, reviewer, color)
            else:
                print(_color(f"⚠️  编号超出范围 (1-{len(display_list)})", C_YELLOW, color))
        else:
            print(_color(f"⚠️  未知命令: {cmd}", C_YELLOW, color))

    state.save(base_dir)
    print(_color("💾 状态已保存，下次可通过同样命令继续。", C_GREEN, color))
    return 0


def do_mark_one(
    state: BatchState,
    base_dir: str,
    issue: Issue,
    reviewer: str,
    color: bool,
) -> None:
    print()
    print(_color(f"▶ 问题详情:", C_BOLD, color))
    print(f"  类型: {_color(ISSUE_TYPE_LABELS.get(issue.type, issue.type.value), TYPE_COLORS.get(issue.type, ''), color)}")
    print(f"  路径: {issue.path}")
    print(f"  描述: {issue.message}")
    if issue.detail:
        print(f"  详情: {_color(issue.detail, C_GRAY, color)}")
    if issue.status != ReviewStatus.PENDING:
        print(f"  当前状态: {_color(REVIEW_STATUS_LABELS[issue.status], STATUS_COLORS[issue.status], color)}")
        if issue.reviewer:
            print(f"  上次处理人: {issue.reviewer} @ {issue.reviewed_at}")
    print()
    print(_color("选择操作:", C_BOLD, color))
    print("  1) 通过    (p)")
    print("  2) 忽略    (i)")
    print("  3) 待补充  (t)")
    print("  4) 取消    (q)")
    choice = input("请选择 [1]: ").strip().lower() or "1"
    choice_map = {
        "1": ReviewStatus.PASSED, "p": ReviewStatus.PASSED,
        "2": ReviewStatus.IGNORED, "i": ReviewStatus.IGNORED,
        "3": ReviewStatus.TODO, "t": ReviewStatus.TODO,
    }
    status = choice_map.get(choice)
    if status is None:
        if choice in ("4", "q", "cancel"):
            print(_color("已取消标记。", C_GRAY, color))
            return
        print(_color("⚠️  无效选项，已取消。", C_YELLOW, color))
        return
    note = _prompt("备注（可留空）", "")
    state.mark_issue(issue.id, status, reviewer, note)
    state.save(base_dir)
    print(_color(f"✓ 已标记为「{REVIEW_STATUS_LABELS[status]}」", C_GREEN, color))


def mark_all_pending(
    state: BatchState,
    base_dir: str,
    reviewer: str,
    color: bool,
) -> None:
    pending = [i for i in state.get_sorted_issues() if i.status == ReviewStatus.PENDING]
    if not pending:
        print(_color("没有待复核的问题。", C_GRAY, color))
        return
    print(f"共有 {len(pending)} 个待复核问题")
    print("  1) 全部通过")
    print("  2) 全部忽略")
    print("  3) 全部待补充")
    print("  q) 取消")
    choice = input("请选择 [q]: ").strip().lower() or "q"
    choice_map = {
        "1": ReviewStatus.PASSED,
        "2": ReviewStatus.IGNORED,
        "3": ReviewStatus.TODO,
    }
    status = choice_map.get(choice)
    if status is None:
        return
    if not _confirm(f"确定将 {len(pending)} 个问题全部标记为「{REVIEW_STATUS_LABELS[status]}」？", default_no=True):
        return
    count = 0
    for issue in pending:
        state.mark_issue(issue.id, status, reviewer, "批量标记")
        count += 1
    state.save(base_dir)
    print(_color(f"✓ 已批量标记 {count} 个问题", C_GREEN, color))


def cmd_mark(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    batch_name = args.batch_name
    if not BatchState.exists(base_dir, batch_name):
        print(_color(f"❌ 批次不存在: {batch_name}", C_RED, color), file=sys.stderr)
        return 1
    state = BatchState.load(base_dir, batch_name)
    issues = state.get_sorted_issues()

    reviewer = args.reviewer or _get_default_reviewer()
    status_name = args.status.lower()
    status_map = {
        "passed": ReviewStatus.PASSED, "pass": ReviewStatus.PASSED, "p": ReviewStatus.PASSED,
        "ignored": ReviewStatus.IGNORED, "ignore": ReviewStatus.IGNORED, "i": ReviewStatus.IGNORED,
        "todo": ReviewStatus.TODO, "t": ReviewStatus.TODO,
        "pending": ReviewStatus.PENDING, "reset": ReviewStatus.PENDING,
    }
    status = status_map.get(status_name)
    if status is None:
        print(_color(f"❌ 未知状态: {args.status}（支持 passed/ignored/todo/pending）", C_RED, color), file=sys.stderr)
        return 2

    targets = []
    if args.ids:
        for raw in args.ids.split(","):
            raw = raw.strip()
            if not raw:
                continue
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(issues):
                    targets.append(issues[idx].id)
                else:
                    print(_color(f"⚠️  编号超出范围: {raw}", C_YELLOW, color))
            else:
                matches = [i for i in issues if i.id.startswith(raw)]
                if len(matches) == 1:
                    targets.append(matches[0].id)
                elif not matches:
                    print(_color(f"⚠️  未找到问题: {raw}", C_YELLOW, color))
                else:
                    print(_color(f"⚠️  前缀 {raw} 匹配到多个问题，请提供更精确前缀", C_YELLOW, color))

    if args.all_pending:
        for i in issues:
            if i.status == ReviewStatus.PENDING:
                targets.append(i.id)

    note = args.note or ""
    count = 0
    for tid in set(targets):
        if tid in state.issues:
            state.mark_issue(tid, status, reviewer, note)
            count += 1
    state.save(base_dir)
    print(_color(f"✓ 已标记 {count} 个问题为「{REVIEW_STATUS_LABELS[status]}」", C_GREEN, color))
    return 0


def cmd_undo(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    batch_name = args.batch_name
    if not BatchState.exists(base_dir, batch_name):
        print(_color(f"❌ 批次不存在: {batch_name}", C_RED, color), file=sys.stderr)
        return 1
    state = BatchState.load(base_dir, batch_name)
    if not state.undo_stack:
        print(_color("⚠️  撤销历史为空，没有可撤销的复核操作", C_YELLOW, color))
        print(_color("   （旧状态未被修改）", C_GRAY, color))
        return 0
    steps = max(1, min(args.steps, len(state.undo_stack)))
    for _ in range(steps):
        issue = state.undo_last()
        print(_color(f"↩️  撤销: {issue.path} → {REVIEW_STATUS_LABELS[issue.status]}", C_GREEN, color))
    state.save(base_dir)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    batch_name = args.batch_name
    if not BatchState.exists(base_dir, batch_name):
        print(_color(f"❌ 批次不存在: {batch_name}", C_RED, color), file=sys.stderr)
        return 1
    state = BatchState.load(base_dir, batch_name)
    fmt = args.format or "auto"
    output = args.output or f"{batch_name}_report.html"
    try:
        saved = export_report(state, output, fmt)
        print(_color(f"📄 报告已导出: {saved}", C_GREEN, color))
    except Exception as e:
        print(_color(f"❌ 导出失败: {e}", C_RED, color), file=sys.stderr)
        return 2
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    batches = list_batches(base_dir)
    if not batches:
        print(_color("（暂无历史批次）", C_GRAY, color))
        print(_color("使用 scan 子命令创建第一个批次", C_GRAY, color))
        return 0
    print(_color(f"{'#':>3}  {'批次名称':<20}  {'待复核':>6}  {'总计':>5}  资料目录", C_BOLD, color))
    print(_color("-" * 90, C_GRAY, color))
    for idx, b in enumerate(batches, 1):
        n = _color(b["batch_name"][:20], C_CYAN, color)
        pending = _color(str(b["pending_count"]), C_YELLOW, color)
        total = str(b["issue_count"])
        print(f"{idx:>3}  {n:<20}  {pending:>6}  {total:>5}  {b['data_dir']}")
        if b["updated_at"]:
            print(f"     {_color('更新: ' + b['updated_at'], C_GRAY, color)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="delivery-checker",
        description="本地资料包交付检查工具：扫描、复核、续办、导出",
    )
    parser.add_argument("--no-color", action="store_true", help="禁用彩色输出")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="根据规则扫描资料目录（自动续办已有批次）")
    p_scan.add_argument("rules", help="规则文件路径 (.yaml/.yml/.json)")
    p_scan.add_argument("data_dir", help="资料目录路径")
    p_scan.add_argument("--force", action="store_true",
                        help="强制重新扫描（保留原有复核状态）")
    p_scan.add_argument("--no-merge", action="store_true",
                        help="不合并已有批次，遇到同名批次直接报错")
    p_scan.set_defaults(func=cmd_scan)

    p_review = sub.add_parser("review", help="交互式复核（推荐）")
    p_review.add_argument("batch_name", help="批次名称")
    p_review.add_argument("--reviewer", "-r", help="处理人姓名（默认当前系统用户）")
    p_review.add_argument("--filter", "-f",
                          help="按状态过滤显示: pending,passed,ignored,todo 逗号分隔")
    p_review.add_argument("--all", "-a", action="store_true", help="默认显示全部问题")
    p_review.set_defaults(func=cmd_review)

    p_mark = sub.add_parser("mark", help="非交互式批量标记问题状态")
    p_mark.add_argument("batch_name", help="批次名称")
    p_mark.add_argument("status", help="状态: passed / ignored / todo / pending")
    p_mark.add_argument("--ids", "-i", help="问题编号或ID前缀，逗号分隔（如 1,3,7）")
    p_mark.add_argument("--all-pending", action="store_true", help="标记所有待复核项")
    p_mark.add_argument("--note", "-n", default="", help="备注文字")
    p_mark.add_argument("--reviewer", "-r", help="处理人姓名")
    p_mark.set_defaults(func=cmd_mark)

    p_undo = sub.add_parser("undo", help="撤销最近的复核操作")
    p_undo.add_argument("batch_name", help="批次名称")
    p_undo.add_argument("--steps", "-n", type=int, default=1, help="撤销步数（默认 1）")
    p_undo.set_defaults(func=cmd_undo)

    p_export = sub.add_parser("export", help="导出 HTML 或 CSV 报告")
    p_export.add_argument("batch_name", help="批次名称")
    p_export.add_argument("output", nargs="?", help="输出文件路径（默认 <批次>_report.html）")
    p_export.add_argument("--format", "-f", choices=["html", "csv", "auto"], default="auto",
                          help="导出格式（默认根据扩展名自动判断）")
    p_export.set_defaults(func=cmd_export)

    p_list = sub.add_parser("list", help="列出所有历史批次")
    p_list.set_defaults(func=cmd_list)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "no_color", False):
        os.environ["NO_COLOR"] = "1"
    _print_banner()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        _safe_print()
        _safe_print(_color("已中断。状态在中断前已自动保存。", C_YELLOW, _use_color()))
        return 130


if __name__ == "__main__":
    sys.exit(main())
