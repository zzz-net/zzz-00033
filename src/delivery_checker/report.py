from __future__ import annotations

import csv
import html
import os
from datetime import datetime
from typing import List, Dict, Any

from .models import (
    Issue,
    IssueType,
    ReviewStatus,
    ISSUE_TYPE_LABELS,
    REVIEW_STATUS_LABELS,
)
from .state import BatchState


def _stat_counts(issues: List[Issue]) -> Dict[str, Any]:
    by_type: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for it in IssueType:
        by_type[it.value] = 0
    for st in ReviewStatus:
        by_status[st.value] = 0
    for issue in issues:
        by_type[issue.type.value] += 1
        by_status[issue.status.value] += 1
    return {
        "total": len(issues),
        "by_type": by_type,
        "by_status": by_status,
    }


def export_csv(
    state: BatchState,
    output_path: str,
    issue_types: Optional[List[str]] = None,
    review_statuses: Optional[List[str]] = None,
    path_keyword: str = "",
    sort_by: str = "type",
    sort_order: str = "asc",
) -> str:
    issues = state.get_sorted_issues(
        issue_types=issue_types,
        review_statuses=review_statuses,
        path_keyword=path_keyword,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "编号", "问题类型", "路径", "问题描述", "详细说明",
            "复核状态", "处理人", "备注", "复核时间",
        ])
        for idx, issue in enumerate(issues, 1):
            writer.writerow([
                idx,
                ISSUE_TYPE_LABELS.get(issue.type, issue.type.value),
                issue.path,
                issue.message,
                issue.detail,
                REVIEW_STATUS_LABELS.get(issue.status, issue.status.value),
                issue.reviewer or "",
                issue.note,
                issue.reviewed_at or "",
            ])
    return os.path.abspath(output_path)


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; margin: 0; padding: 24px; background: #f5f7fa; color: #303133; }}
  .container {{ max-width: 1200px; margin: 0 auto; }}
  h1 {{ margin: 0 0 8px; font-size: 24px; }}
  .meta {{ color: #909399; font-size: 13px; margin-bottom: 20px; }}
  .meta span {{ margin-right: 20px; }}
  .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 24px; }}
  .stat-card {{ background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }}
  .stat-card .num {{ font-size: 28px; font-weight: 600; margin-bottom: 4px; }}
  .stat-card .label {{ font-size: 13px; color: #606266; }}
  .section-title {{ font-size: 16px; font-weight: 600; margin: 24px 0 12px; padding-left: 10px; border-left: 4px solid #409eff; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }}
  th, td {{ padding: 10px 12px; text-align: left; font-size: 13px; border-bottom: 1px solid #ebeef5; vertical-align: top; }}
  th {{ background: #fafafa; font-weight: 600; color: #606266; white-space: nowrap; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #fafcff; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; font-weight: 500; }}
  .type-missing {{ background: #fef0f0; color: #f56c6c; }}
  .type-naming {{ background: #fdf6ec; color: #e6a23c; }}
  .type-expired {{ background: #fde2e2; color: #c45656; }}
  .type-duplicate {{ background: #faecd8; color: #b88230; }}
  .type-untracked {{ background: #ecf5ff; color: #409eff; }}
  .status-pending {{ background: #f4f4f5; color: #909399; }}
  .status-passed {{ background: #f0f9eb; color: #67c23a; }}
  .status-ignored {{ background: #f4f4f5; color: #606266; }}
  .status-todo {{ background: #fef0f0; color: #f56c6c; }}
  .idx {{ color: #909399; font-variant-numeric: tabular-nums; }}
  .path {{ font-family: "SF Mono", Consolas, monospace; color: #303133; word-break: break-all; }}
  .detail {{ color: #606266; margin-top: 4px; font-size: 12px; }}
  .reviewer {{ color: #409eff; }}
  .empty {{ text-align: center; padding: 40px; color: #909399; background: #fff; border-radius: 8px; }}
</style>
</head>
<body>
<div class="container">
  <h1>{title}</h1>
  <div class="meta">
    <span>批次：{batch_name}</span>
    <span>资料目录：{data_dir}</span>
    <span>导出时间：{export_time}</span>
    <span>总问题数：{total_count}</span>
  </div>

  {summary_html}

  {table_html}
</div>
</body>
</html>
"""


def export_html(
    state: BatchState,
    output_path: str,
    issue_types: Optional[List[str]] = None,
    review_statuses: Optional[List[str]] = None,
    path_keyword: str = "",
    sort_by: str = "type",
    sort_order: str = "asc",
) -> str:
    issues = state.get_sorted_issues(
        issue_types=issue_types,
        review_statuses=review_statuses,
        path_keyword=path_keyword,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    stats = _stat_counts(issues)

    stat_cards = []
    labels = [
        ("total", "总计", "#303133"),
        (IssueType.MISSING.value, ISSUE_TYPE_LABELS[IssueType.MISSING], "#f56c6c"),
        (IssueType.NAMING.value, ISSUE_TYPE_LABELS[IssueType.NAMING], "#e6a23c"),
        (IssueType.EXPIRED.value, ISSUE_TYPE_LABELS[IssueType.EXPIRED], "#c45656"),
        (IssueType.DUPLICATE.value, ISSUE_TYPE_LABELS[IssueType.DUPLICATE], "#b88230"),
        (IssueType.UNTRACKED.value, ISSUE_TYPE_LABELS[IssueType.UNTRACKED], "#409eff"),
    ]
    status_labels = [
        (ReviewStatus.PENDING.value, REVIEW_STATUS_LABELS[ReviewStatus.PENDING], "#909399"),
        (ReviewStatus.PASSED.value, REVIEW_STATUS_LABELS[ReviewStatus.PASSED], "#67c23a"),
        (ReviewStatus.IGNORED.value, REVIEW_STATUS_LABELS[ReviewStatus.IGNORED], "#606266"),
        (ReviewStatus.TODO.value, REVIEW_STATUS_LABELS[ReviewStatus.TODO], "#f56c6c"),
    ]
    all_labels = labels + status_labels[1:]
    for key, label_text, color in all_labels:
        count = stats["total"] if key == "total" else (
            stats["by_type"].get(key, 0) if key in IssueType._value2member_map_
            else stats["by_status"].get(key, 0)
        )
        stat_cards.append(
            f'<div class="stat-card"><div class="num" style="color:{color}">{count}</div>'
            f'<div class="label">{html.escape(label_text)}</div></div>'
        )
    summary_html = "<div class=\"summary\">" + "".join(stat_cards) + "</div>"

    if not issues:
        table_html = '<div class="section-title">问题列表</div><div class="empty">没有发现问题 🎉</div>'
    else:
        rows = []
        for idx, issue in enumerate(issues, 1):
            type_cls = f"type-{issue.type.value}"
            status_cls = f"status-{issue.status.value}"
            type_label = ISSUE_TYPE_LABELS.get(issue.type, issue.type.value)
            status_label = REVIEW_STATUS_LABELS.get(issue.status, issue.status.value)
            detail_cell = ""
            if issue.detail:
                detail_cell = f'<div class="detail">{html.escape(issue.detail)}</div>'
            reviewer_cell = ""
            if issue.reviewer:
                reviewer_cell = f'<span class="reviewer">{html.escape(issue.reviewer)}</span>'
                if issue.reviewed_at:
                    reviewer_cell += f'<div class="detail">{html.escape(issue.reviewed_at)}</div>'
            note_cell = html.escape(issue.note) if issue.note else ""
            rows.append(
                f"<tr>"
                f'<td class="idx">{idx}</td>'
                f'<td><span class="badge {type_cls}">{html.escape(type_label)}</span></td>'
                f'<td class="path">{html.escape(issue.path)}</td>'
                f'<td>{html.escape(issue.message)}{detail_cell}</td>'
                f'<td><span class="badge {status_cls}">{html.escape(status_label)}</span></td>'
                f"<td>{reviewer_cell}</td>"
                f"<td>{note_cell}</td>"
                f"</tr>"
            )
        table_html = (
            '<div class="section-title">问题列表</div>'
            "<table><thead><tr>"
            "<th style=\"width:48px\">#</th>"
            "<th style=\"width:120px\">类型</th>"
            "<th style=\"width:220px\">路径</th>"
            "<th>问题描述</th>"
            "<th style=\"width:96px\">状态</th>"
            "<th style=\"width:140px\">处理人</th>"
            "<th>备注</th>"
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )

    html_content = _HTML_TEMPLATE.format(
        title=html.escape(f"交付检查报告 - {state.batch_name}"),
        batch_name=html.escape(state.batch_name),
        data_dir=html.escape(state.data_dir),
        export_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total_count=len(issues),
        summary_html=summary_html,
        table_html=table_html,
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    return os.path.abspath(output_path)


def export_report(
    state: BatchState,
    output_path: str,
    fmt: str = "auto",
    issue_types: Optional[List[str]] = None,
    review_statuses: Optional[List[str]] = None,
    path_keyword: str = "",
    sort_by: str = "type",
    sort_order: str = "asc",
) -> str:
    fmt = fmt.lower()
    if fmt == "auto":
        ext = os.path.splitext(output_path)[1].lower()
        if ext == ".csv":
            fmt = "csv"
        elif ext in (".htm", ".html"):
            fmt = "html"
        else:
            fmt = "html"
    if fmt == "csv":
        return export_csv(
            state, output_path,
            issue_types=issue_types,
            review_statuses=review_statuses,
            path_keyword=path_keyword,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    elif fmt == "html":
        if not output_path.lower().endswith((".html", ".htm")):
            output_path += ".html"
        return export_html(
            state, output_path,
            issue_types=issue_types,
            review_statuses=review_statuses,
            path_keyword=path_keyword,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    else:
        raise ValueError(f"不支持的导出格式: {fmt}")
