from __future__ import annotations

import csv
import json
import os
import shutil
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .models import Issue, ReviewStatus
from .state import BatchState, list_batches


COMPARE_CONFIG_DIR_NAME = "compare_configs"
COMPARE_CONFIG_SUFFIX = ".compare.json"
COMPARE_INDEX_FILE = "index.json"


class CompareError(Exception):
    """对比操作通用错误"""
    pass


class BatchNotFoundError(CompareError):
    """指定批次不存在"""
    pass


class CompareConfigError(CompareError):
    """对比配置格式错误"""
    pass


class CompareConfigNotFoundError(CompareError):
    """对比配置不存在"""
    pass


class CompareConfigConflictError(CompareError):
    """对比配置名称冲突"""
    pass


class ExportConflictError(CompareError):
    """导出文件已存在冲突"""
    pass


class ExportPermissionError(CompareError):
    """导出目录无写权限"""
    pass


@dataclass
class ChangedIssue:
    """表示一条有变化的问题记录及其差异"""
    match_key: str
    old_issue: Optional[Issue] = None
    new_issue: Optional[Issue] = None
    change_types: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "match_key": self.match_key,
            "change_types": self.change_types,
            "old": self.old_issue.to_dict() if self.old_issue else None,
            "new": self.new_issue.to_dict() if self.new_issue else None,
        }


@dataclass
class CompareResult:
    """批次对比的完整结果"""
    batch_a_name: str
    batch_b_name: str
    batch_a_updated_at: str = ""
    batch_b_updated_at: str = ""
    compared_at: str = ""
    added: List[Issue] = field(default_factory=list)
    removed: List[Issue] = field(default_factory=list)
    changed: List[ChangedIssue] = field(default_factory=list)
    unchanged: List[Issue] = field(default_factory=list)

    def summary(self) -> Dict[str, int]:
        status_changed = sum(1 for c in self.changed if "status" in c.change_types)
        reviewer_changed = sum(1 for c in self.changed if "reviewer" in c.change_types)
        type_changed = sum(1 for c in self.changed if "type" in c.change_types)
        message_changed = sum(1 for c in self.changed if "message" in c.change_types)
        return {
            "added": len(self.added),
            "removed": len(self.removed),
            "changed": len(self.changed),
            "unchanged": len(self.unchanged),
            "status_changed": status_changed,
            "reviewer_changed": reviewer_changed,
            "type_changed": type_changed,
            "message_changed": message_changed,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "batch_a": {"name": self.batch_a_name, "updated_at": self.batch_a_updated_at},
            "batch_b": {"name": self.batch_b_name, "updated_at": self.batch_b_updated_at},
            "compared_at": self.compared_at,
            "summary": self.summary(),
            "added": [i.to_dict() for i in self.added],
            "removed": [i.to_dict() for i in self.removed],
            "changed": [c.to_dict() for c in self.changed],
            "unchanged": [i.to_dict() for i in self.unchanged],
        }


@dataclass
class CompareConfig:
    """可复用的对比配置"""
    name: str
    description: str = ""
    source_a: str = ""
    source_b: str = ""
    source_a_type: str = "name"
    source_b_type: str = "name"
    export_format: str = "json"
    export_path: str = ""
    conflict_strategy: str = "rename"
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "source_a": self.source_a,
            "source_b": self.source_b,
            "source_a_type": self.source_a_type,
            "source_b_type": self.source_b_type,
            "export_format": self.export_format,
            "export_path": self.export_path,
            "conflict_strategy": self.conflict_strategy,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CompareConfig":
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            source_a=data.get("source_a", ""),
            source_b=data.get("source_b", ""),
            source_a_type=data.get("source_a_type", "name"),
            source_b_type=data.get("source_b_type", "name"),
            export_format=data.get("export_format", "json"),
            export_path=data.get("export_path", ""),
            conflict_strategy=data.get("conflict_strategy", "rename"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


def _get_compare_config_dir(base_dir: str) -> str:
    from .state import STATE_DIR_NAME
    return os.path.join(base_dir, STATE_DIR_NAME, COMPARE_CONFIG_DIR_NAME)


def _get_compare_index_path(base_dir: str) -> str:
    return os.path.join(_get_compare_config_dir(base_dir), COMPARE_INDEX_FILE)


def _get_compare_config_path(base_dir: str, name: str) -> str:
    safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name)
    return os.path.join(_get_compare_config_dir(base_dir), f"{safe_name}{COMPARE_CONFIG_SUFFIX}")


def _normalize_path(path: str, base_dir: Optional[str] = None) -> str:
    """标准化路径：处理大小写、相对路径、分隔符差异。"""
    p = path.replace("\\", "/")
    if base_dir and not os.path.isabs(p):
        base_abs = os.path.abspath(base_dir).replace("\\", "/")
        p = os.path.normpath(os.path.join(base_abs, p)).replace("\\", "/")
    p = os.path.normpath(p).replace("\\", "/")
    return p.lower()


def _compute_match_key(issue: Issue, base_dir_a: Optional[str] = None, base_dir_b: Optional[str] = None) -> str:
    """计算稳定的匹配键，处理路径大小写、相对路径和重复文件。

    优先级：
    1. 如果有 group_key（同槽位重复文件标识），用 (type, group_key)
    2. 否则用 (type, normalized_path) 作为匹配键
    """
    issue_type = issue.type.value if hasattr(issue.type, "value") else str(issue.type)

    if issue.group_key:
        return f"{issue_type}::group::{issue.group_key.lower()}"

    norm_path = _normalize_path(issue.path)
    return f"{issue_type}::path::{norm_path}"


def _detect_changes(old: Issue, new: Issue) -> List[str]:
    """检测两条记录之间的变化类型。"""
    changes: List[str] = []

    old_type = old.type.value if hasattr(old.type, "value") else str(old.type)
    new_type = new.type.value if hasattr(new.type, "value") else str(new.type)
    if old_type != new_type:
        changes.append("type")

    old_status = old.status.value if hasattr(old.status, "value") else str(old.status)
    new_status = new.status.value if hasattr(new.status, "value") else str(new.status)
    if old_status != new_status:
        changes.append("status")

    if (old.reviewer or "") != (new.reviewer or ""):
        changes.append("reviewer")

    if (old.message or "") != (new.message or ""):
        changes.append("message")

    if (old.note or "") != (new.note or ""):
        changes.append("note")

    if (old.detail or "") != (new.detail or ""):
        changes.append("detail")

    return changes


def _resolve_batch_source(base_dir: str, source: str, source_type: str) -> BatchState:
    """根据来源类型解析批次。

    source_type:
      - "name": 按批次名称查找
      - "latest-N": 取最近第 N 个（source 为 "1", "2" 等）
    """
    if source_type == "latest":
        try:
            n = int(source) if source.isdigit() else 1
        except ValueError:
            n = 1
        batches = list_batches(base_dir)
        if not batches:
            raise BatchNotFoundError("没有任何历史批次可供选择")
        if n < 1 or n > len(batches):
            raise BatchNotFoundError(
                f"最近批次索引超出范围: 最近只有 {len(batches)} 个批次，请求第 {n} 个"
            )
        batch_name = batches[n - 1]["batch_name"]
        return BatchState.load(base_dir, batch_name)
    elif source_type == "name":
        if not BatchState.exists(base_dir, source):
            raise BatchNotFoundError(f"批次不存在: {source}")
        return BatchState.load(base_dir, source)
    else:
        raise CompareError(f"未知的来源类型: {source_type}")


def compare_batches(
    base_dir: str,
    batch_a: BatchState,
    batch_b: BatchState,
) -> CompareResult:
    """对比两个批次，返回详细差异结果。"""
    result = CompareResult(
        batch_a_name=batch_a.batch_name,
        batch_b_name=batch_b.batch_name,
        batch_a_updated_at=batch_a.updated_at,
        batch_b_updated_at=batch_b.updated_at,
        compared_at=datetime.now().isoformat(timespec="seconds"),
    )

    map_a: Dict[str, Issue] = {}
    dup_keys_a: set = set()
    for issue in batch_a.issues.values():
        key = _compute_match_key(issue, batch_a.data_dir)
        if key in map_a:
            dup_keys_a.add(key)
        map_a[key] = issue

    map_b: Dict[str, Issue] = {}
    dup_keys_b: set = set()
    for issue in batch_b.issues.values():
        key = _compute_match_key(issue, batch_b.data_dir)
        if key in map_b:
            dup_keys_b.add(key)
        map_b[key] = issue

    for key in dup_keys_a:
        for issue in batch_a.issues.values():
            if _compute_match_key(issue, batch_a.data_dir) == key:
                unique_key = f"{key}::id::{issue.id}"
                map_a[unique_key] = issue

    for key in dup_keys_b:
        for issue in batch_b.issues.values():
            if _compute_match_key(issue, batch_b.data_dir) == key:
                unique_key = f"{key}::id::{issue.id}"
                map_b[unique_key] = issue

    keys_a = set(map_a.keys())
    keys_b = set(map_b.keys())

    for key in keys_b - keys_a:
        result.added.append(map_b[key])

    for key in keys_a - keys_b:
        result.removed.append(map_a[key])

    for key in keys_a & keys_b:
        old_issue = map_a[key]
        new_issue = map_b[key]
        changes = _detect_changes(old_issue, new_issue)
        if changes:
            result.changed.append(ChangedIssue(
                match_key=key,
                old_issue=old_issue,
                new_issue=new_issue,
                change_types=changes,
            ))
        else:
            result.unchanged.append(new_issue)

    return result


def compare_by_source(
    base_dir: str,
    source_a: str,
    source_b: str,
    source_a_type: str = "name",
    source_b_type: str = "name",
) -> CompareResult:
    """通过来源说明（名称或最近N次）对比两个批次。"""
    batch_a = _resolve_batch_source(base_dir, source_a, source_a_type)
    batch_b = _resolve_batch_source(base_dir, source_b, source_b_type)
    return compare_batches(base_dir, batch_a, batch_b)


def _ensure_config_dir(base_dir: str) -> None:
    config_dir = _get_compare_config_dir(base_dir)
    try:
        os.makedirs(config_dir, exist_ok=True)
    except PermissionError as e:
        raise CompareConfigError(f"无法创建对比配置目录: {e}")


def _read_config_index(base_dir: str) -> Dict[str, Any]:
    index_path = _get_compare_config_index_path(base_dir)
    if not os.path.exists(index_path):
        return {"configs": {}}
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "configs" not in data:
            raise CompareConfigError("对比配置索引格式损坏")
        return data
    except json.JSONDecodeError as e:
        raise CompareConfigError(f"对比配置索引 JSON 解析失败: {e}")
    except PermissionError as e:
        raise CompareConfigError(f"无法读取对比配置索引: {e}")


def _write_config_index(base_dir: str, index: Dict[str, Any]) -> None:
    _ensure_config_dir(base_dir)
    index_path = _get_compare_config_index_path(base_dir)
    tmp_path = index_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
        shutil.move(tmp_path, index_path)
    except PermissionError as e:
        raise CompareConfigError(f"无法写入对比配置索引: {e}")


def _get_compare_config_index_path(base_dir: str) -> str:
    return _get_compare_index_path(base_dir)


def list_compare_configs(base_dir: str) -> List[Dict[str, Any]]:
    """列出所有已保存的对比配置。"""
    try:
        index = _read_config_index(base_dir)
    except CompareConfigError:
        return []
    configs = []
    for name, meta in index.get("configs", {}).items():
        configs.append({
            "name": name,
            "description": meta.get("description", ""),
            "source_a": meta.get("source_a", ""),
            "source_b": meta.get("source_b", ""),
            "source_a_type": meta.get("source_a_type", "name"),
            "source_b_type": meta.get("source_b_type", "name"),
            "created_at": meta.get("created_at", ""),
            "updated_at": meta.get("updated_at", ""),
        })
    configs.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
    return configs


def save_compare_config(
    base_dir: str,
    name: str,
    description: str = "",
    source_a: str = "",
    source_b: str = "",
    source_a_type: str = "name",
    source_b_type: str = "name",
    export_format: str = "json",
    export_path: str = "",
    conflict_strategy: str = "rename",
    force: bool = False,
) -> CompareConfig:
    """保存对比配置，支持覆盖或拒绝冲突。"""
    if not name or not name.strip():
        raise CompareConfigError("对比配置名称不能为空")

    _ensure_config_dir(base_dir)
    index = _read_config_index(base_dir)
    now = datetime.now().isoformat(timespec="seconds")

    existing = name in index.get("configs", {})
    if existing and not force:
        raise CompareConfigConflictError(f"对比配置「{name}」已存在，加 -f 覆盖或换名称")

    cfg = CompareConfig(
        name=name.strip(),
        description=description,
        source_a=source_a,
        source_b=source_b,
        source_a_type=source_a_type,
        source_b_type=source_b_type,
        export_format=export_format,
        export_path=export_path,
        conflict_strategy=conflict_strategy,
        created_at=index["configs"].get(name, {}).get("created_at", now) if existing else now,
        updated_at=now,
    )

    config_path = _get_compare_config_path(base_dir, cfg.name)
    tmp_path = config_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(cfg.to_dict(), f, ensure_ascii=False, indent=2)
        shutil.move(tmp_path, config_path)
    except PermissionError as e:
        raise CompareConfigError(f"无法写入对比配置文件: {e}")

    if "configs" not in index:
        index["configs"] = {}
    index["configs"][cfg.name] = {
        "description": cfg.description,
        "source_a": cfg.source_a,
        "source_b": cfg.source_b,
        "source_a_type": cfg.source_a_type,
        "source_b_type": cfg.source_b_type,
        "export_format": cfg.export_format,
        "export_path": cfg.export_path,
        "conflict_strategy": cfg.conflict_strategy,
        "created_at": cfg.created_at,
        "updated_at": cfg.updated_at,
        "config_file": os.path.basename(config_path),
    }
    _write_config_index(base_dir, index)

    return cfg


def get_compare_config(base_dir: str, name: str) -> CompareConfig:
    """读取指定名称的对比配置。"""
    config_path = _get_compare_config_path(base_dir, name)
    if not os.path.exists(config_path):
        raise CompareConfigNotFoundError(f"对比配置不存在: {name}")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise CompareConfigError(f"对比配置文件损坏（JSON 解析失败）: {e}")
    except PermissionError as e:
        raise CompareConfigError(f"无法读取对比配置文件: {e}")
    try:
        return CompareConfig.from_dict(data)
    except (KeyError, TypeError) as e:
        raise CompareConfigError(f"对比配置字段缺失或格式错误: {e}")


def delete_compare_config(base_dir: str, name: str) -> None:
    """删除对比配置。"""
    config_path = _get_compare_config_path(base_dir, name)
    if not os.path.exists(config_path):
        raise CompareConfigNotFoundError(f"对比配置不存在: {name}")
    try:
        os.remove(config_path)
    except PermissionError as e:
        raise CompareConfigError(f"无法删除对比配置文件: {e}")

    try:
        index = _read_config_index(base_dir)
        if "configs" in index and name in index["configs"]:
            del index["configs"][name]
            _write_config_index(base_dir, index)
    except CompareConfigError:
        pass


def _resolve_export_path(
    output_path: str,
    fmt: str,
    conflict_strategy: str,
) -> str:
    """根据冲突策略（overwrite/rename/refuse）解析最终导出路径。"""
    if not output_path:
        default_name = f"compare_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        ext = ".json" if fmt == "json" else ".csv"
        output_path = default_name + ext

    abs_path = os.path.abspath(output_path)
    target_dir = os.path.dirname(abs_path)

    if target_dir:
        if not os.path.exists(target_dir):
            try:
                os.makedirs(target_dir, exist_ok=True)
            except PermissionError as e:
                raise ExportPermissionError(f"目标目录无法创建: {target_dir} ({e})")
        if not os.access(target_dir, os.W_OK):
            raise ExportPermissionError(f"目标目录无写权限: {target_dir}")

    if os.path.exists(abs_path):
        if conflict_strategy == "overwrite":
            return abs_path
        elif conflict_strategy == "refuse":
            raise ExportConflictError(f"导出文件已存在（拒绝覆盖）: {abs_path}")
        elif conflict_strategy == "rename":
            base, ext = os.path.splitext(abs_path)
            counter = 1
            while True:
                candidate = f"{base}_{counter}{ext}"
                if not os.path.exists(candidate):
                    return candidate
                counter += 1
        else:
            raise CompareError(f"未知的冲突处理策略: {conflict_strategy}")

    return abs_path


def export_compare_result_json(
    result: CompareResult,
    output_path: str,
    conflict_strategy: str = "rename",
) -> str:
    """将对比结果导出为 JSON 文件。"""
    final_path = _resolve_export_path(output_path, "json", conflict_strategy)
    try:
        with open(final_path, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
    except PermissionError as e:
        raise ExportPermissionError(f"写入 JSON 失败: {e}")
    return final_path


def export_compare_result_csv(
    result: CompareResult,
    output_path: str,
    conflict_strategy: str = "rename",
) -> str:
    """将对比结果导出为 CSV 文件。"""
    final_path = _resolve_export_path(output_path, "csv", conflict_strategy)

    from .models import ISSUE_TYPE_LABELS, REVIEW_STATUS_LABELS, IssueType, ReviewStatus

    def _type_label(t):
        if hasattr(t, "value"):
            return ISSUE_TYPE_LABELS.get(IssueType(t.value), t.value)
        return ISSUE_TYPE_LABELS.get(IssueType(t), t)

    def _status_label(s):
        if hasattr(s, "value"):
            return REVIEW_STATUS_LABELS.get(ReviewStatus(s.value), s.value)
        return REVIEW_STATUS_LABELS.get(ReviewStatus(s), s)

    try:
        with open(final_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "差异类型", "匹配键",
                "旧类型", "新类型",
                "旧路径", "新路径",
                "旧状态", "新状态",
                "旧处理人", "新处理人",
                "旧描述", "新描述",
                "变化字段",
            ])

            for issue in result.added:
                writer.writerow([
                    "新增", issue.id,
                    "", _type_label(issue.type),
                    "", issue.path,
                    "", _status_label(issue.status),
                    "", issue.reviewer or "",
                    "", issue.message or "",
                    "",
                ])

            for issue in result.removed:
                writer.writerow([
                    "消失", issue.id,
                    _type_label(issue.type), "",
                    issue.path, "",
                    _status_label(issue.status), "",
                    issue.reviewer or "", "",
                    issue.message or "", "",
                    "",
                ])

            for changed in result.changed:
                old = changed.old_issue
                new = changed.new_issue
                writer.writerow([
                    "变化", changed.match_key,
                    _type_label(old.type) if old else "",
                    _type_label(new.type) if new else "",
                    old.path if old else "",
                    new.path if new else "",
                    _status_label(old.status) if old else "",
                    _status_label(new.status) if new else "",
                    old.reviewer or "" if old else "",
                    new.reviewer or "" if new else "",
                    old.message or "" if old else "",
                    new.message or "" if new else "",
                    ",".join(changed.change_types),
                ])

    except PermissionError as e:
        raise ExportPermissionError(f"写入 CSV 失败: {e}")
    return final_path


def export_compare_result(
    result: CompareResult,
    output_path: str = "",
    fmt: str = "auto",
    conflict_strategy: str = "rename",
) -> str:
    """统一导出入口，根据格式选择 JSON 或 CSV。"""
    fmt = fmt.lower()
    if fmt == "auto":
        ext = os.path.splitext(output_path)[1].lower() if output_path else ""
        if ext == ".csv":
            fmt = "csv"
        elif ext == ".json":
            fmt = "json"
        else:
            fmt = "json"

    if fmt == "json":
        return export_compare_result_json(result, output_path, conflict_strategy)
    elif fmt == "csv":
        if not output_path.lower().endswith(".csv"):
            output_path += ".csv"
        return export_compare_result_csv(result, output_path, conflict_strategy)
    else:
        raise CompareError(f"不支持的导出格式: {fmt}")
