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
from .rule_pkg import (
    RulePkgConflictError,
    RulePkgFormatError,
    RulePkgNotFoundError,
    RulePkgPermissionError,
    export_rule_package,
    import_rule_package,
    list_rule_packages,
    save_rule_package,
)
from .view_preset import (
    ViewPreset,
    ViewPresetConflictError,
    ViewPresetFormatError,
    ViewPresetNotFoundError,
    ViewPresetPermissionError,
    delete_view_preset,
    export_view_preset,
    get_view_preset,
    import_view_preset,
    list_view_presets,
    save_view_preset,
)
from .snapshot import (
    Snapshot,
    SnapshotConflictError,
    SnapshotFormatError,
    SnapshotNotFoundError,
    SnapshotPermissionError,
    SnapshotBatchNotFoundError,
    create_snapshot,
    delete_snapshot,
    export_snapshot,
    get_snapshot,
    import_snapshot,
    list_snapshots,
)
from .compare import (
    CompareConfig,
    CompareConfigConflictError,
    CompareConfigError,
    CompareConfigNotFoundError,
    CompareError,
    BatchNotFoundError,
    ExportConflictError,
    ExportPermissionError,
    compare_by_source,
    delete_compare_config,
    export_compare_result,
    get_compare_config,
    list_compare_configs,
    save_compare_config,
)
from .backup import (
    BackupConflictError,
    BackupCorruptedError,
    BackupFormatError,
    BackupNotFoundError,
    BackupPermissionError,
    BackupVersionMismatchError,
    apply_restore,
    create_backup,
    delete_backup,
    export_backup,
    import_backup,
    list_backups,
    preview_restore,
    show_backup,
)
from .plan import (
    Plan,
    PlanTaskItem,
    PlanConflictError,
    PlanFormatError,
    PlanNotFoundError,
    PlanPermissionError,
    delete_plan,
    export_plan,
    get_plan,
    import_plan,
    list_plans,
    run_plan,
    save_plan,
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


def _parse_csv_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def _apply_preset_and_cli_filters(
    args: argparse.Namespace,
    base_dir: str,
) -> tuple[dict, ViewPreset | None, str, str]:
    """合并预设参数和 CLI 参数。

    返回 (filter_kwargs, preset_obj, info_msg)
    filter_kwargs 用于 get_sorted_issues / export_report 调用
    规则：CLI 显式参数优先于预设
    """
    color = _use_color()
    info_parts: List[str] = []

    preset: ViewPreset | None = None
    preset_name = getattr(args, "preset", None)
    if preset_name:
        try:
            preset = get_view_preset(base_dir, preset_name)
            info_parts.append(f"套用预设「{_color(preset_name, C_CYAN, color)}」")
        except ViewPresetNotFoundError as e:
            print(_color(f"❌ 预设不存在: {e}", C_RED, color), file=sys.stderr)
            sys.exit(1)
        except ViewPresetFormatError as e:
            print(_color(f"❌ 预设格式错误: {e}", C_RED, color), file=sys.stderr)
            sys.exit(2)
        except ViewPresetPermissionError as e:
            print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
            sys.exit(4)

    cli_issue_types = _parse_csv_list(getattr(args, "type", None))
    cli_review_statuses = _parse_csv_list(getattr(args, "filter", None))
    cli_path_keyword = getattr(args, "path", "") or ""
    cli_sort_by = getattr(args, "sort_by", "") or ""
    cli_sort_order = getattr(args, "sort_order", "") or ""
    cli_reviewer = getattr(args, "reviewer", None) or ""

    issue_types: List[str] = []
    if cli_issue_types:
        issue_types = cli_issue_types
        info_parts.append(f"类型筛选(CLI): {','.join(issue_types)}")
    elif preset and preset.issue_types:
        issue_types = preset.issue_types
        info_parts.append(f"类型筛选(预设): {','.join(issue_types)}")

    review_statuses: List[str] = []
    if cli_review_statuses:
        review_statuses = cli_review_statuses
        info_parts.append(f"状态筛选(CLI): {','.join(review_statuses)}")
    elif preset and preset.review_statuses:
        review_statuses = preset.review_statuses
        info_parts.append(f"状态筛选(预设): {','.join(review_statuses)}")

    path_keyword = ""
    if cli_path_keyword:
        path_keyword = cli_path_keyword
        info_parts.append(f"路径关键字(CLI): {path_keyword}")
    elif preset and preset.path_keyword:
        path_keyword = preset.path_keyword
        info_parts.append(f"路径关键字(预设): {path_keyword}")

    sort_by = "type"
    if cli_sort_by:
        sort_by = cli_sort_by
        info_parts.append(f"排序字段(CLI): {sort_by}")
    elif preset and preset.sort_by:
        sort_by = preset.sort_by
        info_parts.append(f"排序字段(预设): {sort_by}")

    sort_order = "asc"
    if cli_sort_order:
        sort_order = cli_sort_order
        info_parts.append(f"排序方向(CLI): {sort_order}")
    elif preset and preset.sort_order:
        sort_order = preset.sort_order
        info_parts.append(f"排序方向(预设): {sort_order}")

    reviewer = ""
    if cli_reviewer:
        reviewer = cli_reviewer
    elif preset and preset.default_reviewer:
        reviewer = preset.default_reviewer
        info_parts.append(f"默认处理人(预设): {reviewer}")

    filter_kwargs = {
        "issue_types": issue_types,
        "review_statuses": review_statuses,
        "path_keyword": path_keyword,
        "sort_by": sort_by,
        "sort_order": sort_order,
    }

    info_msg = " | ".join(info_parts) if info_parts else ""
    return filter_kwargs, preset, info_msg, reviewer


def cmd_scan(args: argparse.Namespace) -> int:
    color = _use_color()
    rules_path = os.path.abspath(args.rules)
    data_dir = os.path.abspath(args.data_dir)

    try:
        rules = parse_rules_file(rules_path)
    except ConfigError as e:
        print(_color(f"❌ 配置格式错误: {e}", C_RED, color), file=sys.stderr)
        return 2

    batch_name_override = getattr(args, "batch_name", "") or ""
    if batch_name_override:
        rules.batch_name = batch_name_override.strip()

    batch_prefix = getattr(args, "batch_prefix", "") or ""
    if batch_prefix and not batch_name_override:
        rules.batch_name = f"{batch_prefix}{rules.batch_name}"

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

    filter_kwargs, preset, info_msg, preset_reviewer = _apply_preset_and_cli_filters(args, base_dir)

    reviewer = getattr(args, "reviewer", None) or preset_reviewer or _get_default_reviewer()
    print(f"当前处理人: {_color(reviewer, C_CYAN, color)}")
    if info_msg:
        print(_color(f"🔍 {info_msg}", C_BLUE, color))

    show_all = getattr(args, "all", False)

    while True:
        all_issues = state.get_sorted_issues(**filter_kwargs)
        pending_all = state.get_sorted_issues(
            issue_types=filter_kwargs.get("issue_types"),
            review_statuses=["pending"],
            path_keyword=filter_kwargs.get("path_keyword", ""),
            sort_by=filter_kwargs.get("sort_by", "type"),
            sort_order=filter_kwargs.get("sort_order", "asc"),
        )
        pending = [i for i in all_issues if i.status == ReviewStatus.PENDING]

        print()
        print(_color(f"📋 批次「{state.batch_name}」问题概览", C_BOLD, color))
        print(f"   总数(筛选后): {len(all_issues)}  待复核(筛选后): {len(pending)}")
        print()

        if show_all:
            display_issues = all_issues
        else:
            display_issues = pending or all_issues
        _print_issue_table(display_issues)
        print()
        print(_color("可用命令:", C_BOLD, color))
        print("  <编号>            标记指定问题（输入序号，如: 3）")
        print("  a / all           标记全部待复核")
        print("  u / undo          撤销上一步复核")
        print("  l / list          切换显示全部问题")
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
            show_all = not show_all
            mode = "全部" if show_all else "仅待复核"
            print(_color(f"已切换显示模式: {mode}", C_BLUE, color))
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
                saved = export_report(state, output, **filter_kwargs)
                print(_color(f"📄 报告已导出: {saved}", C_GREEN, color))
            except Exception as e:
                print(_color(f"❌ 导出失败: {e}", C_RED, color), file=sys.stderr)
            continue
        elif cmd in ("a", "all"):
            mark_all_pending(state, base_dir, reviewer, color)
            continue

        if cmd.isdigit():
            idx = int(cmd) - 1
            display_list = display_issues
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

    filter_kwargs, preset, info_msg, _ = _apply_preset_and_cli_filters(args, base_dir)
    if info_msg:
        print(_color(f"🔍 {info_msg}", C_BLUE, color))

    try:
        saved = export_report(state, output, fmt, **filter_kwargs)
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


def cmd_rule_save(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    rules_path = os.path.abspath(args.rules)

    try:
        rules = parse_rules_file(rules_path)
    except ConfigError as e:
        print(_color(f"❌ 配置格式错误: {e}", C_RED, color), file=sys.stderr)
        return 2

    try:
        pkg = save_rule_package(
            base_dir=base_dir,
            name=args.name,
            version=args.version,
            description=args.description,
            rules=rules,
            force=args.force,
        )
    except RulePkgFormatError as e:
        print(_color(f"❌ 规则包格式错误: {e}", C_RED, color), file=sys.stderr)
        return 2
    except RulePkgConflictError as e:
        print(_color(f"⚠️  {e}", C_YELLOW, color), file=sys.stderr)
        print(_color("   加 -f 覆盖，或修改 -n/-v 换个名称/版本保存。", C_GRAY, color), file=sys.stderr)
        return 3
    except RulePkgPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4

    print(_color(f"✅ 规则包已保存", C_GREEN, color))
    print(f"   名称: {_color(pkg.name, C_BOLD, color)}")
    print(f"   版本: {_color(pkg.version, C_CYAN, color)}")
    print(f"   说明: {pkg.description}")
    print(f"   规则数: {len(rules.required_files)} 条必需文件规则")
    print(f"   创建时间: {pkg.created_at}")
    return 0


def cmd_rule_list(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    packages = list_rule_packages(base_dir)
    if not packages:
        print(_color("（暂无规则包）", C_GRAY, color))
        print(_color("使用 rule-save 子命令保存第一个规则包", C_GRAY, color))
        return 0
    print(_color(f"{'#':>3}  {'名称':<20}  {'版本':<12}  {'规则数':>6}  说明", C_BOLD, color))
    print(_color("-" * 90, C_GRAY, color))
    for idx, pkg in enumerate(packages, 1):
        name = _color(pkg["name"][:20], C_CYAN, color)
        version = _color(pkg["version"][:12], C_YELLOW, color)
        rule_count = str(pkg.get("rule_count", 0))
        desc = pkg.get("description", "")
        print(f"{idx:>3}  {name:<20}  {version:<12}  {rule_count:>6}  {desc}")
        if pkg.get("updated_at"):
            print(f"     {_color('更新: ' + pkg['updated_at'], C_GRAY, color)}")
    return 0


def cmd_rule_export(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    output = args.output or f"{args.name}_{args.version}.rulepkg.json"
    try:
        saved = export_rule_package(
            base_dir=base_dir,
            name=args.name,
            version=args.version,
            output_path=output,
        )
    except RulePkgNotFoundError as e:
        print(_color(f"❌ 规则包不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except RulePkgFormatError as e:
        print(_color(f"❌ 规则包格式错误: {e}", C_RED, color), file=sys.stderr)
        return 2
    except RulePkgPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4
    print(_color(f"📄 规则包已导出: {saved}", C_GREEN, color))
    return 0


def cmd_rule_import(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    input_path = os.path.abspath(args.input)
    try:
        pkg = import_rule_package(
            base_dir=base_dir,
            input_path=input_path,
            force=args.force,
            rename_name=args.rename_name,
            rename_version=args.rename_version,
        )
    except RulePkgNotFoundError as e:
        print(_color(f"❌ 文件不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except RulePkgFormatError as e:
        print(_color(f"❌ 规则包格式错误: {e}", C_RED, color), file=sys.stderr)
        return 2
    except RulePkgConflictError as e:
        print(_color(f"⚠️  {e}", C_YELLOW, color), file=sys.stderr)
        print(_color("   加 -f 强制覆盖，或用 -N/-V 改名导入。", C_GRAY, color), file=sys.stderr)
        print(_color("   （原有规则包未被修改）", C_GRAY, color), file=sys.stderr)
        return 3
    except RulePkgPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4

    print(_color(f"✅ 规则包已导入", C_GREEN, color))
    print(f"   名称: {_color(pkg.name, C_BOLD, color)}")
    print(f"   版本: {_color(pkg.version, C_CYAN, color)}")
    print(f"   说明: {pkg.description}")
    return 0


def cmd_preset_save(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    issue_types = _parse_csv_list(getattr(args, "type", None))
    review_statuses = _parse_csv_list(getattr(args, "filter", None))
    try:
        preset = save_view_preset(
            base_dir=base_dir,
            name=args.name,
            description=getattr(args, "description", "") or "",
            issue_types=issue_types,
            review_statuses=review_statuses,
            path_keyword=getattr(args, "path", "") or "",
            sort_by=getattr(args, "sort_by", "type") or "type",
            sort_order=getattr(args, "sort_order", "asc") or "asc",
            default_reviewer=getattr(args, "reviewer", "") or "",
            force=getattr(args, "force", False),
        )
    except ViewPresetConflictError as e:
        print(_color(f"⚠️  {e}", C_YELLOW, color), file=sys.stderr)
        print(_color("   加 -f 覆盖，或换个名称保存。", C_GRAY, color), file=sys.stderr)
        return 3
    except ViewPresetFormatError as e:
        print(_color(f"❌ 预设格式错误: {e}", C_RED, color), file=sys.stderr)
        return 2
    except ViewPresetPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4

    print(_color(f"✅ 视图预设已保存", C_GREEN, color))
    print(f"   名称: {_color(preset.name, C_BOLD, color)}")
    if preset.description:
        print(f"   说明: {preset.description}")
    if preset.issue_types:
        print(f"   问题类型: {', '.join(preset.issue_types)}")
    if preset.review_statuses:
        print(f"   复核状态: {', '.join(preset.review_statuses)}")
    if preset.path_keyword:
        print(f"   路径关键字: {preset.path_keyword}")
    print(f"   排序方式: {preset.sort_by} / {preset.sort_order}")
    if preset.default_reviewer:
        print(f"   默认处理人: {preset.default_reviewer}")
    print(f"   创建时间: {preset.created_at}")
    return 0


def cmd_preset_list(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    try:
        presets = list_view_presets(base_dir)
    except ViewPresetFormatError as e:
        print(_color(f"❌ 预设索引格式错误: {e}", C_RED, color), file=sys.stderr)
        return 2
    except ViewPresetPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4

    if not presets:
        print(_color("（暂无视图预设）", C_GRAY, color))
        print(_color("使用 preset-save 子命令创建第一个预设", C_GRAY, color))
        return 0

    print(_color(f"{'#':>3}  {'名称':<18}  {'类型筛选':<18}  {'状态筛选':<18}  说明", C_BOLD, color))
    print(_color("-" * 90, C_GRAY, color))
    for idx, p in enumerate(presets, 1):
        name = _color(p["name"][:18], C_CYAN, color)
        types = ",".join(p.get("issue_types", []))
        statuses = ",".join(p.get("review_statuses", []))
        if not types:
            types = "(全部)"
        if not statuses:
            statuses = "(全部)"
        desc = p.get("description", "")
        print(f"{idx:>3}  {name:<18}  {types[:18]:<18}  {statuses[:18]:<18}  {desc}")
        extra_parts = []
        if p.get("path_keyword"):
            extra_parts.append(f"路径关键字: {p['path_keyword']}")
        extra_parts.append(f"排序: {p.get('sort_by', 'type')}/{p.get('sort_order', 'asc')}")
        if p.get("default_reviewer"):
            extra_parts.append(f"处理人: {p['default_reviewer']}")
        if p.get("updated_at"):
            extra_parts.append(f"更新: {p['updated_at']}")
        print(f"     {_color(' | '.join(extra_parts), C_GRAY, color)}")
    return 0


def cmd_preset_show(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    try:
        preset = get_view_preset(base_dir, args.name)
    except ViewPresetNotFoundError as e:
        print(_color(f"❌ 预设不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except ViewPresetFormatError as e:
        print(_color(f"❌ 预设格式错误: {e}", C_RED, color), file=sys.stderr)
        return 2
    except ViewPresetPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4

    print(_color(f"📋 视图预设「{preset.name}」详情", C_BOLD, color))
    if preset.description:
        print(f"  说明: {preset.description}")
    print(f"  问题类型筛选: {', '.join(preset.issue_types) if preset.issue_types else '(全部)'}")
    print(f"  复核状态筛选: {', '.join(preset.review_statuses) if preset.review_statuses else '(全部)'}")
    print(f"  路径关键字: {preset.path_keyword if preset.path_keyword else '(无)'}")
    print(f"  排序字段: {preset.sort_by}")
    print(f"  排序方向: {preset.sort_order}")
    print(f"  默认处理人: {preset.default_reviewer if preset.default_reviewer else '(无)'}")
    print(f"  创建时间: {preset.created_at}")
    print(f"  更新时间: {preset.updated_at}")
    return 0


def cmd_preset_delete(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    try:
        delete_view_preset(base_dir, args.name)
    except ViewPresetNotFoundError as e:
        print(_color(f"❌ 预设不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except ViewPresetPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4
    print(_color(f"✅ 已删除预设: {args.name}", C_GREEN, color))
    return 0


def cmd_preset_export(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    output = args.output or f"{args.name}.preset.json"
    try:
        saved = export_view_preset(
            base_dir=base_dir,
            name=args.name,
            output_path=output,
        )
    except ViewPresetNotFoundError as e:
        print(_color(f"❌ 预设不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except ViewPresetFormatError as e:
        print(_color(f"❌ 预设格式错误: {e}", C_RED, color), file=sys.stderr)
        return 2
    except ViewPresetPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4
    print(_color(f"📄 预设已导出: {saved}", C_GREEN, color))
    return 0


def cmd_preset_import(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    input_path = os.path.abspath(args.input)
    try:
        preset = import_view_preset(
            base_dir=base_dir,
            input_path=input_path,
            force=args.force,
            rename_name=args.rename_name,
        )
    except ViewPresetNotFoundError as e:
        print(_color(f"❌ 文件不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except ViewPresetFormatError as e:
        print(_color(f"❌ 预设格式错误: {e}", C_RED, color), file=sys.stderr)
        print(_color("   （原有预设未被修改）", C_GRAY, color), file=sys.stderr)
        return 2
    except ViewPresetConflictError as e:
        print(_color(f"⚠️  {e}", C_YELLOW, color), file=sys.stderr)
        print(_color("   加 -f 强制覆盖，或用 -N 改名导入。", C_GRAY, color), file=sys.stderr)
        print(_color("   （原有预设未被修改）", C_GRAY, color), file=sys.stderr)
        return 3
    except ViewPresetPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4

    print(_color(f"✅ 视图预设已导入", C_GREEN, color))
    print(f"   名称: {_color(preset.name, C_BOLD, color)}")
    if preset.description:
        print(f"   说明: {preset.description}")
    if preset.issue_types:
        print(f"   问题类型: {', '.join(preset.issue_types)}")
    if preset.review_statuses:
        print(f"   复核状态: {', '.join(preset.review_statuses)}")
    return 0


def _resolve_source_args(args, prefix: str) -> tuple[str, str]:
    """解析 --a/--a-latest / --b/--b-latest 参数，返回 (source, source_type)。"""
    name_val = getattr(args, f"{prefix}", None)
    latest_val = getattr(args, f"{prefix}_latest", None)
    if latest_val is not None:
        return (str(latest_val), "latest")
    if name_val:
        return (name_val, "name")
    return ("", "name")


def cmd_compare(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()

    source_a, type_a = _resolve_source_args(args, "a")
    source_b, type_b = _resolve_source_args(args, "b")

    if (not source_a) or (not source_b):
        print(_color("❌ 请指定两个对比来源：--a <批次名> 或 --a-latest N，同理 --b",
                     C_RED, color), file=sys.stderr)
        return 1

    try:
        result = compare_by_source(base_dir, source_a, source_b, type_a, type_b)
    except BatchNotFoundError as e:
        print(_color(f"❌ 批次不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except CompareConfigError as e:
        print(_color(f"❌ 配置错误: {e}", C_RED, color), file=sys.stderr)
        return 2
    except CompareError as e:
        print(_color(f"❌ 对比失败: {e}", C_RED, color), file=sys.stderr)
        return 2

    summary = result.summary()
    print(_color(f"📊 批次对比结果", C_BOLD, color))
    print(f"   批次 A: {_color(result.batch_a_name, C_CYAN, color)}"
          f"  ({result.batch_a_updated_at or '未知时间'})")
    print(f"   批次 B: {_color(result.batch_b_name, C_CYAN, color)}"
          f"  ({result.batch_b_updated_at or '未知时间'})")
    print()
    print(_color("📈 差异汇总:", C_BOLD, color))
    print(f"   {_color('新增', C_GREEN, color)}: {summary['added']}  "
          f"{_color('消失', C_RED, color)}: {summary['removed']}  "
          f"{_color('变化', C_YELLOW, color)}: {summary['changed']}  "
          f"{_color('未变', C_GRAY, color)}: {summary['unchanged']}")
    if summary['changed'] > 0:
        parts = []
        if summary['status_changed']:
            parts.append(f"状态变化: {summary['status_changed']}")
        if summary['reviewer_changed']:
            parts.append(f"处理人变化: {summary['reviewer_changed']}")
        if summary['type_changed']:
            parts.append(f"类型变化: {summary['type_changed']}")
        if summary['message_changed']:
            parts.append(f"描述变化: {summary['message_changed']}")
        if parts:
            print(f"   {_color(' | '.join(parts), C_GRAY, color)}")
    print()

    if result.added:
        print(_color("➕ 新增问题（B 有、A 无）:", C_GREEN, color))
        for issue in result.added:
            type_label = ISSUE_TYPE_LABELS.get(issue.type, issue.type.value)
            status_label = REVIEW_STATUS_LABELS.get(issue.status, issue.status.value)
            print(f"   [{type_label}] {issue.path} - {issue.message} ({status_label})")
        print()

    if result.removed:
        print(_color("➖ 消失问题（A 有、B 无）:", C_RED, color))
        for issue in result.removed:
            type_label = ISSUE_TYPE_LABELS.get(issue.type, issue.type.value)
            status_label = REVIEW_STATUS_LABELS.get(issue.status, issue.status.value)
            print(f"   [{type_label}] {issue.path} - {issue.message} ({status_label})")
        print()

    if result.changed:
        print(_color("🔄 变化问题:", C_YELLOW, color))
        for ch in result.changed:
            old = ch.old_issue
            new = ch.new_issue
            changes_str = ",".join(ch.change_types)
            path = new.path if new else (old.path if old else "")
            print(f"   {path}")
            print(f"     变化字段: {_color(changes_str, C_CYAN, color)}")
            if old and new:
                old_status = REVIEW_STATUS_LABELS.get(old.status, old.status.value)
                new_status = REVIEW_STATUS_LABELS.get(new.status, new.status.value)
                if old_status != new_status:
                    print(f"     状态: {old_status} → {new_status}")
                old_reviewer = old.reviewer or "(无)"
                new_reviewer = new.reviewer or "(无)"
                if old_reviewer != new_reviewer:
                    print(f"     处理人: {old_reviewer} → {new_reviewer}")
        print()

    output = getattr(args, "output", None)
    fmt = getattr(args, "format", "auto") or "auto"
    conflict = getattr(args, "conflict", "rename") or "rename"
    if output:
        try:
            saved = export_compare_result(result, output, fmt, conflict)
            print(_color(f"📄 对比结果已导出: {saved}", C_GREEN, color))
        except ExportConflictError as e:
            print(_color(f"⚠️  导出冲突: {e}", C_YELLOW, color), file=sys.stderr)
            return 3
        except ExportPermissionError as e:
            print(_color(f"❌ 导出权限错误: {e}", C_RED, color), file=sys.stderr)
            return 4
        except CompareError as e:
            print(_color(f"❌ 导出失败: {e}", C_RED, color), file=sys.stderr)
            return 2

    cfg_name = getattr(args, "save_config", None)
    if cfg_name:
        try:
            cfg = save_compare_config(
                base_dir=base_dir,
                name=cfg_name,
                description=getattr(args, "config_description", "") or "",
                source_a=source_a,
                source_b=source_b,
                source_a_type=type_a,
                source_b_type=type_b,
                export_format=fmt if fmt != "auto" else "json",
                export_path=output or "",
                conflict_strategy=conflict,
                force=getattr(args, "force", False),
            )
            print(_color(f"✅ 对比配置已保存: {cfg.name}", C_GREEN, color))
        except CompareConfigConflictError as e:
            print(_color(f"⚠️  {e}", C_YELLOW, color), file=sys.stderr)
            return 3
        except CompareConfigError as e:
            print(_color(f"❌ 保存配置失败: {e}", C_RED, color), file=sys.stderr)
            return 2

    return 0


def cmd_compare_run(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    cfg_name = args.name

    try:
        cfg = get_compare_config(base_dir, cfg_name)
    except CompareConfigNotFoundError as e:
        print(_color(f"❌ 对比配置不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except CompareConfigError as e:
        print(_color(f"❌ 对比配置损坏: {e}", C_RED, color), file=sys.stderr)
        return 2

    output = getattr(args, "output", None) or cfg.export_path
    fmt = getattr(args, "format", None) or cfg.export_format or "auto"
    conflict = getattr(args, "conflict", None) or cfg.conflict_strategy or "rename"

    override_a = getattr(args, "a", None)
    override_a_latest = getattr(args, "a_latest", None)
    override_b = getattr(args, "b", None)
    override_b_latest = getattr(args, "b_latest", None)

    if override_a is not None:
        source_a, type_a = override_a, "name"
    elif override_a_latest is not None:
        source_a, type_a = str(override_a_latest), "latest"
    else:
        source_a, type_a = cfg.source_a, cfg.source_a_type

    if override_b is not None:
        source_b, type_b = override_b, "name"
    elif override_b_latest is not None:
        source_b, type_b = str(override_b_latest), "latest"
    else:
        source_b, type_b = cfg.source_b, cfg.source_b_type

    if (not source_a) or (not source_b):
        print(_color("❌ 配置中缺少来源信息，请在保存时指定或运行时通过 --a/--b 覆盖",
                     C_RED, color), file=sys.stderr)
        return 1

    try:
        result = compare_by_source(base_dir, source_a, source_b, type_a, type_b)
    except BatchNotFoundError as e:
        print(_color(f"❌ 批次不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except CompareError as e:
        print(_color(f"❌ 对比失败: {e}", C_RED, color), file=sys.stderr)
        return 2

    summary = result.summary()
    print(_color(f"📊 批次对比结果（使用配置「{cfg.name}」）", C_BOLD, color))
    print(f"   批次 A: {_color(result.batch_a_name, C_CYAN, color)}"
          f"  ({result.batch_a_updated_at or '未知时间'})")
    print(f"   批次 B: {_color(result.batch_b_name, C_CYAN, color)}"
          f"  ({result.batch_b_updated_at or '未知时间'})")
    print(f"   {_color('新增', C_GREEN, color)}: {summary['added']}  "
          f"{_color('消失', C_RED, color)}: {summary['removed']}  "
          f"{_color('变化', C_YELLOW, color)}: {summary['changed']}  "
          f"{_color('未变', C_GRAY, color)}: {summary['unchanged']}")

    if output:
        try:
            saved = export_compare_result(result, output, fmt, conflict)
            print(_color(f"📄 对比结果已导出: {saved}", C_GREEN, color))
        except ExportConflictError as e:
            print(_color(f"⚠️  导出冲突: {e}", C_YELLOW, color), file=sys.stderr)
            return 3
        except ExportPermissionError as e:
            print(_color(f"❌ 导出权限错误: {e}", C_RED, color), file=sys.stderr)
            return 4
        except CompareError as e:
            print(_color(f"❌ 导出失败: {e}", C_RED, color), file=sys.stderr)
            return 2

    return 0


def cmd_compare_save(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    source_a, type_a = _resolve_source_args(args, "a")
    source_b, type_b = _resolve_source_args(args, "b")

    try:
        cfg = save_compare_config(
            base_dir=base_dir,
            name=args.name,
            description=getattr(args, "description", "") or "",
            source_a=source_a,
            source_b=source_b,
            source_a_type=type_a,
            source_b_type=type_b,
            export_format=getattr(args, "format", "json") or "json",
            export_path=getattr(args, "output", "") or "",
            conflict_strategy=getattr(args, "conflict", "rename") or "rename",
            force=getattr(args, "force", False),
        )
    except CompareConfigConflictError as e:
        print(_color(f"⚠️  {e}", C_YELLOW, color), file=sys.stderr)
        return 3
    except CompareConfigError as e:
        print(_color(f"❌ 保存配置失败: {e}", C_RED, color), file=sys.stderr)
        return 2

    print(_color(f"✅ 对比配置已保存", C_GREEN, color))
    print(f"   名称: {_color(cfg.name, C_BOLD, color)}")
    if cfg.description:
        print(f"   说明: {cfg.description}")
    if cfg.source_a:
        label_a = f"最近第 {cfg.source_a} 个" if cfg.source_a_type == "latest" else cfg.source_a
        label_b = f"最近第 {cfg.source_b} 个" if cfg.source_b_type == "latest" else cfg.source_b
        print(f"   来源 A: {label_a}")
        print(f"   来源 B: {label_b}")
    if cfg.export_format:
        print(f"   默认导出格式: {cfg.export_format}")
    if cfg.export_path:
        print(f"   默认导出路径: {cfg.export_path}")
    if cfg.conflict_strategy:
        strategy_label = {"overwrite": "覆盖", "rename": "自动改名", "refuse": "拒绝"}.get(
            cfg.conflict_strategy, cfg.conflict_strategy
        )
        print(f"   文件冲突策略: {strategy_label}")
    return 0


def cmd_compare_list(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    try:
        configs = list_compare_configs(base_dir)
    except CompareConfigError as e:
        print(_color(f"❌ 读取配置索引失败: {e}", C_RED, color), file=sys.stderr)
        return 2

    if not configs:
        print(_color("（暂无对比配置）", C_GRAY, color))
        print(_color("使用 compare-save 子命令创建第一个对比配置", C_GRAY, color))
        return 0

    print(_color(f"{'#':>3}  {'名称':<20}  {'A来源':<14}  {'B来源':<14}  说明", C_BOLD, color))
    print(_color("-" * 90, C_GRAY, color))
    for idx, cfg in enumerate(configs, 1):
        name = _color(cfg["name"][:20], C_CYAN, color)
        src_a = (f"最近{cfg['source_a']}" if cfg.get("source_a_type") == "latest"
                 else (cfg.get("source_a", "") or ""))[:14]
        src_b = (f"最近{cfg['source_b']}" if cfg.get("source_b_type") == "latest"
                 else (cfg.get("source_b", "") or ""))[:14]
        desc = cfg.get("description", "")
        print(f"{idx:>3}  {name:<20}  {src_a:<14}  {src_b:<14}  {desc}")
        if cfg.get("updated_at"):
            print(f"     {_color('更新: ' + cfg['updated_at'], C_GRAY, color)}")
    return 0


def cmd_compare_show(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    try:
        cfg = get_compare_config(base_dir, args.name)
    except CompareConfigNotFoundError as e:
        print(_color(f"❌ 对比配置不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except CompareConfigError as e:
        print(_color(f"❌ 对比配置损坏: {e}", C_RED, color), file=sys.stderr)
        return 2

    print(_color(f"📋 对比配置「{cfg.name}」详情", C_BOLD, color))
    if cfg.description:
        print(f"  说明: {cfg.description}")
    if cfg.source_a:
        label_a = f"最近第 {cfg.source_a} 个批次" if cfg.source_a_type == "latest" else cfg.source_a
        label_b = f"最近第 {cfg.source_b} 个批次" if cfg.source_b_type == "latest" else cfg.source_b
        print(f"  来源 A: {label_a}")
        print(f"  来源 B: {label_b}")
    if cfg.export_format:
        print(f"  默认导出格式: {cfg.export_format}")
    if cfg.export_path:
        print(f"  默认导出路径: {cfg.export_path}")
    if cfg.conflict_strategy:
        strategy_label = {"overwrite": "覆盖", "rename": "自动改名", "refuse": "拒绝"}.get(
            cfg.conflict_strategy, cfg.conflict_strategy
        )
        print(f"  文件冲突策略: {strategy_label}")
    print(f"  创建时间: {cfg.created_at}")
    print(f"  更新时间: {cfg.updated_at}")
    return 0


def cmd_compare_delete(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    try:
        delete_compare_config(base_dir, args.name)
    except CompareConfigNotFoundError as e:
        print(_color(f"❌ 对比配置不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except CompareConfigError as e:
        print(_color(f"❌ 删除失败: {e}", C_RED, color), file=sys.stderr)
        return 2
    print(_color(f"✅ 已删除对比配置: {args.name}", C_GREEN, color))
    return 0


def cmd_snapshot_create(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    try:
        snapshot = create_snapshot(
            base_dir,
            args.name,
            args.description or "",
            args.batch,
        )
    except SnapshotBatchNotFoundError as e:
        print(_color(f"❌ 来源批次不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except SnapshotConflictError as e:
        print(_color(f"❌ 快照名称冲突: {e}", C_RED, color), file=sys.stderr)
        print(_color("使用 --force 覆盖，或选一个新名称。", C_YELLOW, color), file=sys.stderr)
        return 2
    except SnapshotFormatError as e:
        print(_color(f"❌ 格式错误: {e}", C_RED, color), file=sys.stderr)
        return 3
    except SnapshotPermissionError as e:
        print(_color(f"❌ 权限不足: {e}", C_RED, color), file=sys.stderr)
        return 4
    except Exception as e:
        print(_color(f"❌ 创建失败: {e}", C_RED, color), file=sys.stderr)
        return 5
    print(_color(f"✅ 已创建快照: {snapshot.name}", C_GREEN, color))
    print(f"  来源批次: {snapshot.source_batch_name}")
    print(f"  问题数量: {snapshot.issue_count}")
    print(f"  状态分布: {snapshot.status_distribution}")
    print(f"  规则摘要: {snapshot.rules_summary()}")
    print(f"  创建时间: {snapshot.created_at}")
    return 0


def cmd_snapshot_list(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    try:
        snapshots = list_snapshots(base_dir)
    except SnapshotFormatError as e:
        print(_color(f"❌ 索引文件损坏: {e}", C_RED, color), file=sys.stderr)
        return 1
    except SnapshotPermissionError as e:
        print(_color(f"❌ 权限不足: {e}", C_RED, color), file=sys.stderr)
        return 2
    if not snapshots:
        print(_color("（暂无快照）", C_YELLOW, color))
        return 0
    print(_color(f"共 {len(snapshots)} 个快照：", C_BOLD, color))
    for i, s in enumerate(snapshots, 1):
        status_parts = [
            f"{REVIEW_STATUS_LABELS.get(k, k)} {v}"
            for k, v in s.get("status_distribution", {}).items()
        ]
        status_str = ", ".join(status_parts) if status_parts else "(无问题)"
        rules_str = (
            f"{s.get('source_rules_required_count', 0)} 必需 "
            f"{s.get('source_rules_naming_count', 0)} 命名"
        )
        print()
        print(f"{i}. {_color(s['name'], C_BOLD, color)}")
        if s.get("description"):
            print(f"   说明: {s['description']}")
        print(f"   来源批次: {s.get('source_batch_name', '')}")
        print(f"   创建时间: {s.get('created_at', '')}")
        print(f"   问题数量: {s.get('issue_count', 0)}")
        print(f"   状态分布: {status_str}")
        print(f"   规则摘要: {rules_str}")
    return 0


def cmd_snapshot_show(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    try:
        snapshot = get_snapshot(base_dir, args.name)
    except SnapshotNotFoundError as e:
        print(_color(f"❌ 快照不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except SnapshotFormatError as e:
        print(_color(f"❌ 快照文件损坏: {e}", C_RED, color), file=sys.stderr)
        return 2
    except SnapshotPermissionError as e:
        print(_color(f"❌ 权限不足: {e}", C_RED, color), file=sys.stderr)
        return 3
    print(_color(f"快照: {snapshot.name}", C_BOLD, color))
    print(f"  说明: {snapshot.description or '(无)'}")
    print(f"  来源批次: {snapshot.source_batch_name}")
    print(f"  来源数据目录: {snapshot.data_dir}")
    print(f"  来源规则文件: {snapshot.rules_path}")
    print(f"  创建时间: {snapshot.created_at}")
    print(f"  更新时间: {snapshot.updated_at}")
    print()
    print(_color("规则摘要:", C_BOLD, color))
    print(f"  {snapshot.rules_summary()}")
    print(f"  规则批次名: {snapshot.source_rules.get('batch_name', '')}")
    print(f"  规则版本哈希: {snapshot.source_rules.get('source_hash', '')}")
    print()
    print(_color(f"问题统计 ({snapshot.issue_count} 个问题):", C_BOLD, color))
    for status, count in snapshot.status_distribution.items():
        label = REVIEW_STATUS_LABELS.get(status, status)
        print(f"  {label}: {count}")
    if snapshot.issues and getattr(args, "verbose", False):
        print()
        print(_color("问题详情:", C_BOLD, color))
        for issue in snapshot.issues:
            status_label = REVIEW_STATUS_LABELS.get(
                issue.get("status", ""),
                issue.get("status", "unknown")
            )
            type_label = ISSUE_TYPE_LABELS.get(
                issue.get("type", ""),
                issue.get("type", "unknown")
            )
            print(f"  - [{type_label}] {issue.get('file_path', '')}: {issue.get('description', '')}")
            print(f"    状态: {status_label}")
            if issue.get("reviewer"):
                print(f"    复核人: {issue.get('reviewer', '')}")
            if issue.get("review_note"):
                print(f"    备注: {issue.get('review_note', '')}")
    return 0


def cmd_snapshot_export(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    if args.output:
        output_path = args.output
    else:
        output_path = os.path.join(base_dir, f"{args.name}.snapshot.json")
    try:
        output = export_snapshot(base_dir, args.name, output_path)
    except SnapshotNotFoundError as e:
        print(_color(f"❌ 快照不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except SnapshotPermissionError as e:
        print(_color(f"❌ 权限不足: {e}", C_RED, color), file=sys.stderr)
        return 2
    except Exception as e:
        print(_color(f"❌ 导出失败: {e}", C_RED, color), file=sys.stderr)
        return 3
    print(_color(f"✅ 已导出快照到: {output}", C_GREEN, color))
    return 0


def cmd_snapshot_import(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    strategy_map = {"overwrite": "overwrite", "rename": "rename", "refuse": "refuse"}
    strategy = strategy_map.get(args.conflict, "refuse")
    try:
        snapshot = import_snapshot(
            base_dir,
            args.input,
            conflict_strategy=strategy,
            rename_name=args.rename_name,
        )
    except SnapshotFormatError as e:
        print(_color(f"❌ 导入文件格式错误: {e}", C_RED, color), file=sys.stderr)
        return 2
    except SnapshotConflictError as e:
        print(_color(f"❌ 快照名称冲突: {e}", C_RED, color), file=sys.stderr)
        print(_color("使用 --conflict overwrite 覆盖，或 --conflict rename 自动改名。", C_YELLOW, color), file=sys.stderr)
        return 2
    except SnapshotPermissionError as e:
        print(_color(f"❌ 权限不足: {e}", C_RED, color), file=sys.stderr)
        return 3
    except SnapshotNotFoundError as e:
        print(_color(f"❌ 导入文件不存在: {e}", C_RED, color), file=sys.stderr)
        return 4
    except Exception as e:
        print(_color(f"❌ 导入失败: {e}", C_RED, color), file=sys.stderr)
        return 5
    strategy_label = {"overwrite": "覆盖", "rename": "自动改名", "refuse": "拒绝"}.get(strategy, strategy)
    print(_color(f"✅ 已导入快照: {snapshot.name}", C_GREEN, color))
    print(f"  冲突策略: {strategy_label}")
    print(f"  来源批次: {snapshot.source_batch_name}")
    print(f"  问题数量: {snapshot.issue_count}")
    print(f"  导入时间: {snapshot.updated_at}")
    return 0


def cmd_snapshot_delete(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    try:
        delete_snapshot(base_dir, args.name)
    except SnapshotNotFoundError as e:
        print(_color(f"❌ 快照不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except SnapshotPermissionError as e:
        print(_color(f"❌ 权限不足: {e}", C_RED, color), file=sys.stderr)
        return 2
    print(_color(f"✅ 已删除快照: {args.name}", C_GREEN, color))
    return 0


def cmd_backup_create(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    try:
        manifest, data = create_backup(
            base_dir=base_dir,
            name=args.name,
            description=args.description or "",
            include_batches=not args.no_batches,
            include_rule_packages=not args.no_rules,
            include_view_presets=not args.no_presets,
            include_snapshots=not args.no_snapshots,
            include_compare_configs=not args.no_compare,
        )
    except BackupFormatError as e:
        print(_color(f"❌ 格式错误: {e}", C_RED, color), file=sys.stderr)
        return 2
    except BackupConflictError as e:
        print(_color(f"⚠️  {e}", C_YELLOW, color), file=sys.stderr)
        print(_color("   换个名称创建，或先删除已有备份。", C_GRAY, color), file=sys.stderr)
        return 3
    except BackupPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4

    includes = []
    if manifest.include_batches:
        includes.append("批次历史")
    if manifest.include_rule_packages:
        includes.append("规则包")
    if manifest.include_view_presets:
        includes.append("视图预设")
    if manifest.include_snapshots:
        includes.append("快照")
    if manifest.include_compare_configs:
        includes.append("对比配置")

    print(_color(f"✅ 备份已创建: {manifest.name}", C_GREEN, color))
    print(f"   说明: {manifest.description or '(无)'}")
    print(f"   包含: {', '.join(includes)}")
    print(f"   内容: {manifest.content_summary}")
    print(f"   创建时间: {manifest.created_at}")
    return 0


def cmd_backup_list(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    try:
        backups = list_backups(base_dir)
    except BackupFormatError as e:
        print(_color(f"❌ 索引格式错误: {e}", C_RED, color), file=sys.stderr)
        return 2
    except BackupPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4

    if not backups:
        print(_color("（暂无备份）", C_GRAY, color))
        print(_color("使用 backup create 子命令创建第一个备份", C_GRAY, color))
        return 0

    print(_color(f"{'#':>3}  {'名称':<20}  {'大小':>10}  {'创建时间':<22}  包含内容", C_BOLD, color))
    print(_color("-" * 100, C_GRAY, color))
    for idx, b in enumerate(backups, 1):
        name = _color(b["name"][:20], C_CYAN, color)
        size_bytes = b.get("total_size_bytes", 0)
        from .backup import _human_readable_size
        size_str = _human_readable_size(size_bytes)
        created = b.get("created_at", "")[:19]
        includes = []
        if b.get("include_batches"):
            includes.append("批次")
        if b.get("include_rule_packages"):
            includes.append("规则包")
        if b.get("include_view_presets"):
            includes.append("预设")
        if b.get("include_snapshots"):
            includes.append("快照")
        if b.get("include_compare_configs"):
            includes.append("对比")
        inc_str = ",".join(includes) if includes else "(空)"
        print(f"{idx:>3}  {name:<20}  {size_str:>10}  {created:<22}  {inc_str}")
        if b.get("description"):
            print(f"     {_color(b['description'][:60], C_GRAY, color)}")
        if b.get("source_hostname"):
            print(f"     {_color('来源: ' + b.get('source_hostname', ''), C_GRAY, color)}")
    return 0


def cmd_backup_show(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    try:
        info = show_backup(base_dir, args.name)
    except BackupNotFoundError as e:
        print(_color(f"❌ 备份不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except BackupCorruptedError as e:
        print(_color(f"❌ 备份文件损坏: {e}", C_RED, color), file=sys.stderr)
        return 5
    except BackupVersionMismatchError as e:
        print(_color(f"❌ 版本不兼容: {e}", C_RED, color), file=sys.stderr)
        return 6
    except BackupPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4
    except BackupFormatError as e:
        print(_color(f"❌ 格式错误: {e}", C_RED, color), file=sys.stderr)
        return 2

    print(_color(f"📦 备份「{info['name']}」详情", C_BOLD, color))
    if info["description"]:
        print(f"  说明: {info['description']}")
    print(f"  体积: {info['total_size_human']} ({info['total_size_bytes']} 字节)")
    print(f"  创建时间: {info['created_at']}")
    print(f"  更新时间: {info['updated_at']}")
    print(f"  包含内容: {', '.join(info['includes']) if info['includes'] else '(无)'}")
    print(f"  内容摘要: {info['content_summary']}")
    print(f"  来源摘要: {info['source_summary'] or '(无)'}")
    print(f"  格式版本: v{info['format_version']}")
    return 0


def cmd_backup_export(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    fmt = getattr(args, "format", "json") or "json"
    output = args.output or f"{args.name}.backup.json"
    if fmt == "zip" and not output.endswith(".zip"):
        output = os.path.splitext(output)[0] + ".zip"
    try:
        saved = export_backup(base_dir, args.name, output, fmt)
    except BackupNotFoundError as e:
        print(_color(f"❌ 备份不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except BackupCorruptedError as e:
        print(_color(f"❌ 备份文件损坏: {e}", C_RED, color), file=sys.stderr)
        return 5
    except BackupPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4
    print(_color(f"📄 备份已导出: {saved}", C_GREEN, color))
    return 0


def cmd_backup_import(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    strategy_map = {"overwrite": "overwrite", "rename": "rename", "refuse": "refuse"}
    strategy = strategy_map.get(args.conflict, "refuse")
    try:
        manifest, data = import_backup(
            base_dir,
            args.input,
            conflict_strategy=strategy,
            rename_name=args.rename_name,
        )
    except BackupNotFoundError as e:
        print(_color(f"❌ 文件不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except BackupFormatError as e:
        print(_color(f"❌ 格式错误: {e}", C_RED, color), file=sys.stderr)
        return 2
    except BackupCorruptedError as e:
        print(_color(f"❌ 备份文件损坏: {e}", C_RED, color), file=sys.stderr)
        return 5
    except BackupVersionMismatchError as e:
        print(_color(f"❌ 版本不兼容: {e}", C_RED, color), file=sys.stderr)
        return 6
    except BackupConflictError as e:
        print(_color(f"⚠️  {e}", C_YELLOW, color), file=sys.stderr)
        strategy_label = {"overwrite": "覆盖", "rename": "自动改名", "refuse": "拒绝"}.get(strategy, strategy)
        print(_color(f"   当前策略: {strategy_label}", C_GRAY, color), file=sys.stderr)
        if strategy == "refuse":
            print(_color("   使用 --conflict overwrite 覆盖，或 --conflict rename 自动改名。", C_GRAY, color), file=sys.stderr)
        return 3
    except BackupPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4

    strategy_label = {"overwrite": "覆盖", "rename": "自动改名", "refuse": "拒绝"}.get(strategy, strategy)
    print(_color(f"✅ 备份已导入: {manifest.name}", C_GREEN, color))
    print(f"  冲突策略: {strategy_label}")
    print(f"  内容摘要: {manifest.content_summary}")
    print(f"  导入时间: {manifest.updated_at}")
    return 0


def cmd_backup_restore(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    conflict_strategy = args.conflict or "skip"

    try:
        diff = preview_restore(base_dir, args.name)
    except BackupNotFoundError as e:
        print(_color(f"❌ 备份不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except BackupCorruptedError as e:
        print(_color(f"❌ 备份文件损坏: {e}", C_RED, color), file=sys.stderr)
        return 5
    except BackupVersionMismatchError as e:
        print(_color(f"❌ 版本不兼容: {e}", C_RED, color), file=sys.stderr)
        return 6
    except BackupPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4
    except BackupFormatError as e:
        print(_color(f"❌ 格式错误: {e}", C_RED, color), file=sys.stderr)
        return 2

    print(_color(f"📋 备份「{args.name}」恢复差异预览", C_BOLD, color))
    print()

    total_new = 0
    total_conflict = 0
    total_unchanged = 0

    section_labels = {
        "batches": "批次历史",
        "rule_packages": "规则包",
        "view_presets": "视图预设",
        "snapshots": "快照",
        "compare_configs": "对比配置",
    }

    for section_key, section_diff in diff.get("sections", {}).items():
        label = section_labels.get(section_key, section_key)
        new_count = section_diff.get("new_count", 0)
        conflict_count = section_diff.get("conflict_count", 0)
        unchanged_count = section_diff.get("unchanged_count", 0)
        total_new += new_count
        total_conflict += conflict_count
        total_unchanged += unchanged_count

        print(f"  {_color(label, C_BOLD, color)}: "
              f"{_color('新增', C_GREEN, color)} {new_count}  "
              f"{_color('冲突', C_YELLOW, color)} {conflict_count}  "
              f"{_color('无变化', C_GRAY, color)} {unchanged_count}")

        for item in section_diff.get("new", []):
            print(f"    {_color('➕', C_GREEN, color)} {item['key']}")
        for item in section_diff.get("conflicting", []):
            print(f"    {_color('⚠️ ', C_YELLOW, color)} {item['key']}")
            print(f"       当前: {item.get('current_summary', '')}")
            print(f"       备份: {item.get('backup_summary', '')}")

    print()
    print(_color(f"汇总: 新增 {total_new}  冲突 {total_conflict}  无变化 {total_unchanged}", C_BOLD, color))

    if diff.get("has_conflicts"):
        print()
        print(_color("⚠️  检测到数据冲突！", C_YELLOW, color))
        strategy_labels = {
            "overwrite": "覆盖（备份覆盖当前）",
            "skip": "跳过（保留当前，仅添加新项）",
            "abort": "中止",
        }
        print(f"  冲突策略: {strategy_labels.get(conflict_strategy, conflict_strategy)}")

    if getattr(args, "dry_run", False):
        print()
        print(_color("（--dry-run 模式，未执行恢复操作）", C_GRAY, color))
        return 0

    print()
    if not _confirm("确认执行恢复？", default_no=True):
        print(_color("已取消恢复。", C_GRAY, color))
        return 0

    try:
        result = apply_restore(base_dir, args.name, conflict_strategy)
    except BackupConflictError as e:
        print(_color(f"❌ {e}", C_RED, color), file=sys.stderr)
        return 3
    except BackupPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4
    except Exception as e:
        print(_color(f"❌ 恢复失败: {e}", C_RED, color), file=sys.stderr)
        return 5

    print()
    print(_color("📊 恢复结果:", C_BOLD, color))
    for section_key, section_result in result.get("sections", {}).items():
        label = section_labels.get(section_key, section_key)
        added = section_result.get("added", [])
        skipped = section_result.get("skipped", [])
        overwritten = section_result.get("overwritten", [])
        errors = section_result.get("errors", [])
        parts = []
        if added:
            parts.append(f"新增 {len(added)}")
        if overwritten:
            parts.append(f"覆盖 {len(overwritten)}")
        if skipped:
            parts.append(f"跳过 {len(skipped)}")
        if errors:
            parts.append(_color(f"错误 {len(errors)}", C_RED, color))
        print(f"  {label}: {', '.join(parts) if parts else '(无操作)'}")
        for err in errors:
            print(f"    {_color(f'❌ {err}', C_RED, color)}")

    has_errors = any(
        section_result.get("errors")
        for section_result in result.get("sections", {}).values()
    )
    if has_errors:
        return 5

    print()
    print(_color("✅ 恢复完成", C_GREEN, color))
    return 0


def cmd_backup_delete(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    try:
        delete_backup(base_dir, args.name)
    except BackupNotFoundError as e:
        print(_color(f"❌ 备份不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except BackupPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4
    print(_color(f"✅ 已删除备份: {args.name}", C_GREEN, color))
    return 0


def cmd_plan_create(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()

    rules_paths = getattr(args, "rules", []) or []
    data_dirs = getattr(args, "data_dirs", []) or []
    task_names = getattr(args, "task_name", []) or []
    task_descs = getattr(args, "task_desc", []) or []

    if not rules_paths:
        print(_color("❌ 至少需要指定一个 --rules", C_RED, color), file=sys.stderr)
        return 2
    if not data_dirs:
        print(_color("❌ 至少需要指定一个 --data-dir", C_RED, color), file=sys.stderr)
        return 2
    if len(rules_paths) != len(data_dirs):
        print(_color(
            f"❌ --rules ({len(rules_paths)}) 与 --data-dir ({len(data_dirs)}) 数量必须一致",
            C_RED, color), file=sys.stderr)
        return 2

    tasks: List[PlanTaskItem] = []
    for i in range(len(rules_paths)):
        try:
            task = PlanTaskItem.new(
                rules_path=rules_paths[i],
                data_dir=data_dirs[i],
                name=task_names[i] if i < len(task_names) else "",
                description=task_descs[i] if i < len(task_descs) else "",
            )
            tasks.append(task)
        except PlanFormatError as e:
            print(_color(f"❌ 第 {i+1} 个任务项错误: {e}", C_RED, color), file=sys.stderr)
            return 2

    try:
        plan = save_plan(
            base_dir=base_dir,
            name=args.name,
            description=getattr(args, "description", "") or "",
            batch_prefix=getattr(args, "batch_prefix", "") or "",
            tasks=tasks,
            force=getattr(args, "force", False),
        )
    except PlanFormatError as e:
        print(_color(f"❌ 计划格式错误: {e}", C_RED, color), file=sys.stderr)
        return 2
    except PlanConflictError as e:
        print(_color(f"⚠️  {e}", C_YELLOW, color), file=sys.stderr)
        print(_color("   加 -f 覆盖，或换个名称保存。", C_GRAY, color), file=sys.stderr)
        return 3
    except PlanPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4

    print(_color(f"✅ 计划已创建", C_GREEN, color))
    print(f"   名称: {_color(plan.name, C_BOLD, color)}")
    if plan.description:
        print(f"   说明: {plan.description}")
    if plan.batch_prefix:
        print(f"   批次名前缀: {_color(plan.batch_prefix, C_CYAN, color)}")
    print(f"   任务数: {len(plan.tasks)}")
    for i, task in enumerate(plan.tasks, 1):
        task_name = task.name or f"任务{i}"
        print(f"   {i}. {_color(task_name, C_BOLD, color)}")
        if task.description:
            print(f"      说明: {task.description}")
        print(f"      规则: {task.rules_path}")
        print(f"      资料: {task.data_dir}")
    print(f"   创建时间: {plan.created_at}")
    return 0


def cmd_plan_list(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    try:
        plans = list_plans(base_dir)
    except PlanFormatError as e:
        print(_color(f"❌ 索引格式错误: {e}", C_RED, color), file=sys.stderr)
        return 2
    except PlanPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4

    if not plans:
        print(_color("（暂无计划）", C_GRAY, color))
        print(_color("使用 plan create 子命令创建第一个计划", C_GRAY, color))
        return 0

    print(_color(f"{'#':>3}  {'名称':<20}  {'任务数':>6}  {'前缀':<15}  说明", C_BOLD, color))
    print(_color("-" * 90, C_GRAY, color))
    for idx, p in enumerate(plans, 1):
        name = _color(p["name"][:20], C_CYAN, color)
        task_count = str(p.get("task_count", 0))
        prefix = (p.get("batch_prefix", "") or "")[:15]
        desc = p.get("description", "")
        print(f"{idx:>3}  {name:<20}  {task_count:>6}  {prefix:<15}  {desc}")
        if p.get("updated_at"):
            print(f"     {_color('更新: ' + p['updated_at'], C_GRAY, color)}")
    return 0


def cmd_plan_show(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    try:
        plan = get_plan(base_dir, args.name)
    except PlanNotFoundError as e:
        print(_color(f"❌ 计划不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except PlanFormatError as e:
        print(_color(f"❌ 计划文件损坏: {e}", C_RED, color), file=sys.stderr)
        return 2
    except PlanPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4

    print(_color(f"📋 计划「{plan.name}」详情", C_BOLD, color))
    if plan.description:
        print(f"  说明: {plan.description}")
    if plan.batch_prefix:
        print(f"  批次名前缀: {_color(plan.batch_prefix, C_CYAN, color)}")
    print(f"  工作区: {plan.workspace_dir or '(当前目录)'}")
    print(f"  创建时间: {plan.created_at}")
    print(f"  更新时间: {plan.updated_at}")
    print()
    print(_color(f"任务列表 ({len(plan.tasks)} 项):", C_BOLD, color))
    resolved = plan.resolve_paths()
    for i, (task, (abs_rules, abs_data)) in enumerate(zip(plan.tasks, resolved), 1):
        task_name = task.name or f"任务{i}"
        print(f"  {_color(f'{i}. {task_name}', C_BOLD, color)}")
        if task.description:
            print(f"     说明: {task.description}")
        print(f"     规则: {task.rules_path}")
        print(f"           → {abs_rules}")
        print(f"     资料: {task.data_dir}")
        print(f"           → {abs_data}")

    path_errors = plan.validate_paths()
    if path_errors:
        print()
        print(_color("⚠️  路径问题:", C_YELLOW, color))
        for idx, msg in path_errors:
            print(f"   任务 {idx+1}: {msg}")
    return 0


def cmd_plan_run(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()

    output_dir = getattr(args, "output_dir", None)
    no_merge = getattr(args, "no_merge", False)
    force_rescan = getattr(args, "force", False)
    export_format = getattr(args, "format", "csv") or "csv"

    try:
        summary = run_plan(
            base_dir=base_dir,
            name=args.name,
            output_dir=output_dir,
            no_merge=no_merge,
            force_rescan=force_rescan,
            export_format=export_format,
        )
    except PlanNotFoundError as e:
        print(_color(f"❌ 计划不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except PlanFormatError as e:
        print(_color(f"❌ 计划格式或路径错误: {e}", C_RED, color), file=sys.stderr)
        return 2
    except PlanPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4
    except Exception as e:
        print(_color(f"❌ 执行异常: {e}", C_RED, color), file=sys.stderr)
        return 5

    print()
    print(_color(f"📊 计划「{summary['plan_name']}」执行结果", C_BOLD, color))
    print(f"   总计: {summary['total']}  "
          f"{_color('成功', C_GREEN, color)}: {summary['success']}  "
          f"{_color('失败', C_RED, color)}: {summary['failed']}  "
          f"{_color('跳过', C_YELLOW, color)}: {summary['skipped']}")
    print()

    report_paths: List[str] = []
    for result in summary["results"]:
        idx = result["task_index"] + 1
        task_name = result["task_name"]
        batch_name = result["batch_name"]
        status = result["status"]
        report_path = result["report_path"]
        error_msg = result["error_message"]
        exit_code = result["exit_code"]

        if status == "success":
            status_color = C_GREEN
            status_label = "✅ 成功"
        elif status == "skipped":
            status_color = C_YELLOW
            status_label = "⚠️  跳过"
        else:
            status_color = C_RED
            status_label = "❌ 失败"

        print(f"  {_color(f'{idx}. {task_name}', C_BOLD, color)}")
        print(f"     批次: {batch_name}")
        print(f"     状态: {_color(status_label, status_color, color)} (退出码: {exit_code})")
        if report_path:
            print(f"     报告: {report_path}")
            report_paths.append(report_path)
        if error_msg:
            print(f"     {_color('错误: ' + error_msg, status_color, color)}")

    print()
    if report_paths:
        print(_color("📄 所有报告路径:", C_BOLD, color))
        for rp in report_paths:
            print(f"   {rp}")

    if summary["failed"] > 0:
        return 5
    return 0


def cmd_plan_export(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    output = args.output or f"{args.name}.plan.json"
    try:
        saved = export_plan(
            base_dir=base_dir,
            name=args.name,
            output_path=output,
        )
    except PlanNotFoundError as e:
        print(_color(f"❌ 计划不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except PlanFormatError as e:
        print(_color(f"❌ 计划格式错误: {e}", C_RED, color), file=sys.stderr)
        return 2
    except PlanPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4
    print(_color(f"📄 计划已导出: {saved}", C_GREEN, color))
    return 0


def cmd_plan_import(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    strategy_map = {"overwrite": "overwrite", "rename": "rename", "refuse": "refuse"}
    strategy = strategy_map.get(args.conflict, "refuse")
    try:
        plan = import_plan(
            base_dir,
            args.input,
            conflict_strategy=strategy,
            rename_name=args.rename_name,
        )
    except PlanNotFoundError as e:
        print(_color(f"❌ 导入文件不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except PlanFormatError as e:
        print(_color(f"❌ 导入文件格式错误: {e}", C_RED, color), file=sys.stderr)
        return 2
    except PlanConflictError as e:
        print(_color(f"❌ 计划名称冲突: {e}", C_RED, color), file=sys.stderr)
        print(_color("使用 --conflict overwrite 覆盖，或 --conflict rename 自动改名。", C_YELLOW, color), file=sys.stderr)
        return 3
    except PlanPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4
    except Exception as e:
        print(_color(f"❌ 导入失败: {e}", C_RED, color), file=sys.stderr)
        return 5

    strategy_label = {"overwrite": "覆盖", "rename": "自动改名", "refuse": "拒绝"}.get(strategy, strategy)
    print(_color(f"✅ 计划已导入", C_GREEN, color))
    print(f"   名称: {_color(plan.name, C_BOLD, color)}")
    print(f"   冲突策略: {strategy_label}")
    print(f"   说明: {plan.description or '(无)'}")
    print(f"   任务数: {len(plan.tasks)}")
    print(f"   导入时间: {plan.updated_at}")
    return 0


def cmd_plan_delete(args: argparse.Namespace) -> int:
    color = _use_color()
    base_dir = _get_base_dir()
    try:
        delete_plan(base_dir, args.name)
    except PlanNotFoundError as e:
        print(_color(f"❌ 计划不存在: {e}", C_RED, color), file=sys.stderr)
        return 1
    except PlanPermissionError as e:
        print(_color(f"❌ 权限错误: {e}", C_RED, color), file=sys.stderr)
        return 4
    print(_color(f"✅ 已删除计划: {args.name}", C_GREEN, color))
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
    p_scan.add_argument("--batch-prefix", default="",
                        help="批次名称前缀，添加到规则文件中定义的 batch_name 之前")
    p_scan.add_argument("--batch-name", default="",
                        help="批次名称，覆盖规则文件中定义的 batch_name")
    p_scan.set_defaults(func=cmd_scan)

    p_review = sub.add_parser("review", help="交互式复核（推荐）")
    p_review.add_argument("batch_name", help="批次名称")
    p_review.add_argument("--reviewer", "-r", help="处理人姓名（默认当前系统用户）")
    p_review.add_argument("--filter", "-f",
                          help="按复核状态过滤: pending,passed,ignored,todo 逗号分隔")
    p_review.add_argument("--type", "-t", dest="type",
                          help="按问题类型过滤: missing,naming,expired,duplicate,untracked 逗号分隔")
    p_review.add_argument("--path", "-p", help="按路径/描述关键字过滤（大小写不敏感子串匹配）")
    p_review.add_argument("--sort-by", choices=["type", "path", "status", "reviewed_at", "id"],
                          default="", help="排序字段（默认: type）")
    p_review.add_argument("--sort-order", choices=["asc", "desc"], default="",
                          help="排序方向（默认: asc）")
    p_review.add_argument("--preset", help="套用命名视图预设")
    p_review.add_argument("--all", "-a", action="store_true", help="默认显示全部问题（含已处理）")
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
    p_export.add_argument("--filter", "-F",
                          help="按复核状态过滤: pending,passed,ignored,todo 逗号分隔")
    p_export.add_argument("--type", "-t", dest="type",
                          help="按问题类型过滤: missing,naming,expired,duplicate,untracked 逗号分隔")
    p_export.add_argument("--path", "-p", help="按路径/描述关键字过滤（大小写不敏感子串匹配）")
    p_export.add_argument("--sort-by", choices=["type", "path", "status", "reviewed_at", "id"],
                          default="", help="排序字段（默认: type）")
    p_export.add_argument("--sort-order", choices=["asc", "desc"], default="",
                          help="排序方向（默认: asc）")
    p_export.add_argument("--preset", help="套用命名视图预设")
    p_export.set_defaults(func=cmd_export)

    p_list = sub.add_parser("list", help="列出所有历史批次")
    p_list.set_defaults(func=cmd_list)

    p_rule_save = sub.add_parser("rule-save", help="将当前 YAML/JSON 规则保存为命名规则包")
    p_rule_save.add_argument("rules", help="规则文件路径 (.yaml/.yml/.json)")
    p_rule_save.add_argument("--name", "-n", required=True, help="规则包名称")
    p_rule_save.add_argument("--version", "-v", required=True, help="规则包版本 (如 1.0.0)")
    p_rule_save.add_argument("--description", "-d", default="", help="规则包说明")
    p_rule_save.add_argument("--force", "-f", action="store_true",
                             help="强制覆盖已存在的同名同版本规则包")
    p_rule_save.set_defaults(func=cmd_rule_save)

    p_rule_list = sub.add_parser("rule-list", help="列出所有已保存的规则包")
    p_rule_list.set_defaults(func=cmd_rule_list)

    p_rule_export = sub.add_parser("rule-export", help="导出规则包为可分享的 JSON 文件")
    p_rule_export.add_argument("name", help="规则包名称")
    p_rule_export.add_argument("version", help="规则包版本")
    p_rule_export.add_argument("output", nargs="?", help="导出文件路径 (默认: <name>_<version>.rulepkg.json)")
    p_rule_export.set_defaults(func=cmd_rule_export)

    p_rule_import = sub.add_parser("rule-import", help="从导出文件导入规则包")
    p_rule_import.add_argument("input", help="规则包导出文件路径 (.rulepkg.json)")
    p_rule_import.add_argument("--force", "-f", action="store_true",
                               help="强制覆盖已存在的同名同版本规则包")
    p_rule_import.add_argument("--rename-name", "-N", help="重命名导入的规则包名称")
    p_rule_import.add_argument("--rename-version", "-V", help="重命名导入的规则包版本")
    p_rule_import.set_defaults(func=cmd_rule_import)

    p_preset_save = sub.add_parser("preset-save", help="保存筛选条件为命名视图预设")
    p_preset_save.add_argument("--name", "-n", required=True, help="预设名称")
    p_preset_save.add_argument("--description", "-d", default="", help="预设说明")
    p_preset_save.add_argument("--type", "-t", dest="type",
                               help="问题类型: missing,naming,expired,duplicate,untracked 逗号分隔")
    p_preset_save.add_argument("--filter", "-F",
                               help="复核状态: pending,passed,ignored,todo 逗号分隔")
    p_preset_save.add_argument("--path", "-p", help="路径/描述关键字")
    p_preset_save.add_argument("--sort-by", choices=["type", "path", "status", "reviewed_at", "id"],
                               default="type", help="排序字段（默认: type）")
    p_preset_save.add_argument("--sort-order", choices=["asc", "desc"], default="asc",
                               help="排序方向（默认: asc）")
    p_preset_save.add_argument("--reviewer", "-r", help="默认处理人")
    p_preset_save.add_argument("--force", action="store_true",
                               help="强制覆盖已存在的同名预设")
    p_preset_save.set_defaults(func=cmd_preset_save)

    p_preset_list = sub.add_parser("preset-list", help="列出所有已保存的视图预设")
    p_preset_list.set_defaults(func=cmd_preset_list)

    p_preset_show = sub.add_parser("preset-show", help="查看指定视图预设的详细内容")
    p_preset_show.add_argument("name", help="预设名称")
    p_preset_show.set_defaults(func=cmd_preset_show)

    p_preset_delete = sub.add_parser("preset-delete", help="删除指定视图预设")
    p_preset_delete.add_argument("name", help="预设名称")
    p_preset_delete.set_defaults(func=cmd_preset_delete)

    p_preset_export = sub.add_parser("preset-export", help="导出视图预设为可分享的 JSON 文件")
    p_preset_export.add_argument("name", help="预设名称")
    p_preset_export.add_argument("output", nargs="?", help="导出文件路径（默认: <name>.preset.json）")
    p_preset_export.set_defaults(func=cmd_preset_export)

    p_preset_import = sub.add_parser("preset-import", help="从导出文件导入视图预设")
    p_preset_import.add_argument("input", help="预设导出文件路径 (.preset.json)")
    p_preset_import.add_argument("--force", "-f", action="store_true",
                                 help="强制覆盖已存在的同名预设")
    p_preset_import.add_argument("--rename-name", "-N", help="重命名导入的预设名称")
    p_preset_import.set_defaults(func=cmd_preset_import)

    p_compare = sub.add_parser("compare", help="对比两个批次的差异（新增/消失/状态/处理人变化）")
    p_compare.add_argument("--a", help="批次 A 的名称（与 --a-latest 二选一）")
    p_compare.add_argument("--a-latest", type=int, help="批次 A 选最近第 N 个（1=最新）")
    p_compare.add_argument("--b", help="批次 B 的名称（与 --b-latest 二选一）")
    p_compare.add_argument("--b-latest", type=int, help="批次 B 选最近第 N 个（1=最新）")
    p_compare.add_argument("--output", "-o", help="导出结果文件路径")
    p_compare.add_argument("--format", "-f", choices=["json", "csv", "auto"], default="auto",
                           help="导出格式（默认根据扩展名自动判断）")
    p_compare.add_argument("--conflict", "-c", choices=["overwrite", "rename", "refuse"],
                           default="rename",
                           help="导出文件已存在时的处理策略（默认 rename 自动改名）")
    p_compare.add_argument("--save-config", "-s", help="同时将当前对比选项保存为命名配置")
    p_compare.add_argument("--config-description", "-d", default="", help="配置说明（配合 --save-config）")
    p_compare.add_argument("--force", action="store_true", help="保存配置时强制覆盖同名")
    p_compare.set_defaults(func=cmd_compare)

    p_compare_run = sub.add_parser("compare-run", help="按已保存的命名配置运行批次对比")
    p_compare_run.add_argument("name", help="对比配置名称")
    p_compare_run.add_argument("--a", help="覆盖批次 A 名称")
    p_compare_run.add_argument("--a-latest", type=int, help="覆盖批次 A 为最近第 N 个")
    p_compare_run.add_argument("--b", help="覆盖批次 B 名称")
    p_compare_run.add_argument("--b-latest", type=int, help="覆盖批次 B 为最近第 N 个")
    p_compare_run.add_argument("--output", "-o", help="覆盖导出结果文件路径")
    p_compare_run.add_argument("--format", "-f", choices=["json", "csv", "auto"],
                               help="覆盖导出格式")
    p_compare_run.add_argument("--conflict", "-c", choices=["overwrite", "rename", "refuse"],
                               help="覆盖冲突处理策略")
    p_compare_run.set_defaults(func=cmd_compare_run)

    p_compare_save = sub.add_parser("compare-save", help="将对比选项保存为可复用命名配置")
    p_compare_save.add_argument("--name", "-n", required=True, help="配置名称")
    p_compare_save.add_argument("--description", "-d", default="", help="配置说明")
    p_compare_save.add_argument("--a", help="批次 A 名称")
    p_compare_save.add_argument("--a-latest", type=int, help="批次 A 选最近第 N 个")
    p_compare_save.add_argument("--b", help="批次 B 名称")
    p_compare_save.add_argument("--b-latest", type=int, help="批次 B 选最近第 N 个")
    p_compare_save.add_argument("--format", "-f", choices=["json", "csv"], default="json",
                                help="默认导出格式")
    p_compare_save.add_argument("--output", "-o", default="", help="默认导出路径")
    p_compare_save.add_argument("--conflict", "-c", choices=["overwrite", "rename", "refuse"],
                                default="rename", help="默认冲突策略")
    p_compare_save.add_argument("--force", action="store_true", help="强制覆盖同名配置")
    p_compare_save.set_defaults(func=cmd_compare_save)

    p_compare_list = sub.add_parser("compare-list", help="列出所有已保存的对比配置")
    p_compare_list.set_defaults(func=cmd_compare_list)

    p_compare_show = sub.add_parser("compare-show", help="查看指定对比配置的详细内容")
    p_compare_show.add_argument("name", help="配置名称")
    p_compare_show.set_defaults(func=cmd_compare_show)

    p_compare_delete = sub.add_parser("compare-delete", help="删除指定对比配置")
    p_compare_delete.add_argument("name", help="配置名称")
    p_compare_delete.set_defaults(func=cmd_compare_delete)

    p_snapshot = sub.add_parser("snapshot", help="快照归档子命令（create/list/show/export/import/delete）")
    p_snapshot_sub = p_snapshot.add_subparsers(dest="snapshot_command", required=True)

    p_snap_create = p_snapshot_sub.add_parser("create", help="从批次创建快照")
    p_snap_create.add_argument("--name", "-n", required=True, help="快照名称")
    p_snap_create.add_argument("--description", "-d", default="", help="快照说明")
    p_snap_create.add_argument("--batch", "-b", required=True, help="来源批次名称")
    p_snap_create.set_defaults(func=cmd_snapshot_create)

    p_snap_list = p_snapshot_sub.add_parser("list", help="列出所有快照")
    p_snap_list.set_defaults(func=cmd_snapshot_list)

    p_snap_show = p_snapshot_sub.add_parser("show", help="查看快照详情")
    p_snap_show.add_argument("name", help="快照名称")
    p_snap_show.add_argument("--verbose", "-v", action="store_true", help="显示问题详情")
    p_snap_show.set_defaults(func=cmd_snapshot_show)

    p_snap_export = p_snapshot_sub.add_parser("export", help="导出快照为 JSON 文件")
    p_snap_export.add_argument("name", help="快照名称")
    p_snap_export.add_argument("output", nargs="?", help="导出文件路径（默认: <name>.snapshot.json）")
    p_snap_export.set_defaults(func=cmd_snapshot_export)

    p_snap_import = p_snapshot_sub.add_parser("import", help="从 JSON 文件导入快照")
    p_snap_import.add_argument("input", help="快照导出文件路径 (.snapshot.json)")
    p_snap_import.add_argument("--conflict", "-c", choices=["overwrite", "rename", "refuse"],
                               default="refuse",
                               help="同名冲突处理策略（默认 refuse 拒绝）")
    p_snap_import.add_argument("--rename-name", "-N", help="重命名导入的快照名称")
    p_snap_import.set_defaults(func=cmd_snapshot_import)

    p_snap_delete = p_snapshot_sub.add_parser("delete", help="删除快照")
    p_snap_delete.add_argument("name", help="快照名称")
    p_snap_delete.set_defaults(func=cmd_snapshot_delete)

    p_backup = sub.add_parser("backup", help="工作区备份与恢复（create/list/show/export/import/restore/delete）")
    p_backup_sub = p_backup.add_subparsers(dest="backup_command", required=True)

    p_bk_create = p_backup_sub.add_parser("create", help="创建工作区备份")
    p_bk_create.add_argument("--name", "-n", required=True, help="备份名称")
    p_bk_create.add_argument("--description", "-d", default="", help="备份说明")
    p_bk_create.add_argument("--no-batches", action="store_true", help="不包含批次历史")
    p_bk_create.add_argument("--no-rules", action="store_true", help="不包含规则包")
    p_bk_create.add_argument("--no-presets", action="store_true", help="不包含视图预设")
    p_bk_create.add_argument("--no-snapshots", action="store_true", help="不包含快照")
    p_bk_create.add_argument("--no-compare", action="store_true", help="不包含对比配置")
    p_bk_create.set_defaults(func=cmd_backup_create)

    p_bk_list = p_backup_sub.add_parser("list", help="列出所有备份")
    p_bk_list.set_defaults(func=cmd_backup_list)

    p_bk_show = p_backup_sub.add_parser("show", help="查看备份详情")
    p_bk_show.add_argument("name", help="备份名称")
    p_bk_show.set_defaults(func=cmd_backup_show)

    p_bk_export = p_backup_sub.add_parser("export", help="导出备份为 JSON 或 ZIP 文件")
    p_bk_export.add_argument("name", help="备份名称")
    p_bk_export.add_argument("output", nargs="?", help="导出文件路径（默认: <name>.backup.json）")
    p_bk_export.add_argument("--format", "-f", choices=["json", "zip"], default="json",
                              help="导出格式（默认: json）")
    p_bk_export.set_defaults(func=cmd_backup_export)

    p_bk_import = p_backup_sub.add_parser("import", help="从文件导入备份")
    p_bk_import.add_argument("input", help="备份导出文件路径 (.backup.json 或 .zip)")
    p_bk_import.add_argument("--conflict", "-c", choices=["overwrite", "rename", "refuse"],
                              default="refuse",
                              help="同名冲突处理策略（默认 refuse 拒绝）")
    p_bk_import.add_argument("--rename-name", "-N", help="重命名导入的备份名称")
    p_bk_import.set_defaults(func=cmd_backup_import)

    p_bk_restore = p_backup_sub.add_parser("restore", help="从备份恢复工作区数据（先预览差异再确认）")
    p_bk_restore.add_argument("name", help="备份名称")
    p_bk_restore.add_argument("--conflict", "-c", choices=["overwrite", "skip", "abort"],
                               default="skip",
                               help="冲突处理策略（默认 skip 跳过冲突项）")
    p_bk_restore.add_argument("--dry-run", action="store_true",
                               help="仅预览差异，不执行恢复操作")
    p_bk_restore.set_defaults(func=cmd_backup_restore)

    p_bk_delete = p_backup_sub.add_parser("delete", help="删除备份")
    p_bk_delete.add_argument("name", help="备份名称")
    p_bk_delete.set_defaults(func=cmd_backup_delete)

    p_plan = sub.add_parser("plan", help="批量检查计划（create/list/show/run/export/import/delete）")
    p_plan_sub = p_plan.add_subparsers(dest="plan_command", required=True)

    p_plan_create = p_plan_sub.add_parser("create", help="创建批量检查计划")
    p_plan_create.add_argument("--name", "-n", required=True, help="计划名称")
    p_plan_create.add_argument("--description", "-d", default="", help="计划说明")
    p_plan_create.add_argument("--batch-prefix", "-p", default="", help="批次名前缀（如 daily-）")
    p_plan_create.add_argument("--rules", action="append", required=True,
                               help="规则文件路径，可多次指定（与 --data-dir 一一对应）")
    p_plan_create.add_argument("--data-dir", dest="data_dirs", action="append", required=True,
                               help="资料目录路径，可多次指定")
    p_plan_create.add_argument("--task-name", action="append", default=[],
                               help="任务名称（按顺序对应）")
    p_plan_create.add_argument("--task-desc", action="append", default=[],
                               help="任务说明（按顺序对应）")
    p_plan_create.add_argument("--force", "-f", action="store_true",
                               help="强制覆盖已存在的同名计划")
    p_plan_create.set_defaults(func=cmd_plan_create)

    p_plan_list = p_plan_sub.add_parser("list", help="列出所有计划")
    p_plan_list.set_defaults(func=cmd_plan_list)

    p_plan_show = p_plan_sub.add_parser("show", help="查看计划详情")
    p_plan_show.add_argument("name", help="计划名称")
    p_plan_show.set_defaults(func=cmd_plan_show)

    p_plan_run = p_plan_sub.add_parser("run", help="运行计划，逐项执行扫描")
    p_plan_run.add_argument("name", help="计划名称")
    p_plan_run.add_argument("--output-dir", "-o", help="报告输出目录（默认工作区目录）")
    p_plan_run.add_argument("--format", "-f", choices=["csv", "html"], default="csv",
                            help="报告格式（默认 csv）")
    p_plan_run.add_argument("--no-merge", action="store_true",
                            help="不合并已有批次，遇到同名批次报错并跳过")
    p_plan_run.add_argument("--force", action="store_true",
                            help="强制重新扫描已存在的批次")
    p_plan_run.set_defaults(func=cmd_plan_run)

    p_plan_export = p_plan_sub.add_parser("export", help="导出计划为 JSON 文件")
    p_plan_export.add_argument("name", help="计划名称")
    p_plan_export.add_argument("output", nargs="?", help="导出文件路径（默认: <name>.plan.json）")
    p_plan_export.set_defaults(func=cmd_plan_export)

    p_plan_import = p_plan_sub.add_parser("import", help="从 JSON 文件导入计划")
    p_plan_import.add_argument("input", help="计划导出文件路径 (.plan.json)")
    p_plan_import.add_argument("--conflict", "-c", choices=["overwrite", "rename", "refuse"],
                               default="refuse",
                               help="同名冲突处理策略（默认 refuse 拒绝）")
    p_plan_import.add_argument("--rename-name", "-N", help="重命名导入的计划名称")
    p_plan_import.set_defaults(func=cmd_plan_import)

    p_plan_delete = p_plan_sub.add_parser("delete", help="删除计划")
    p_plan_delete.add_argument("name", help="计划名称")
    p_plan_delete.set_defaults(func=cmd_plan_delete)

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
