from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .models import Issue, ReviewStatus
from .state import BatchState


SNAPSHOTS_DIR_NAME = "snapshots"
INDEX_FILE_NAME = "index.json"
SNAPSHOT_FILE_SUFFIX = ".snapshot.json"
LOG_FILE_NAME = "snapshots.log"


def _write_log(base_dir: str, level: str, message: str) -> None:
    try:
        log_dir = _get_snapshots_dir(base_dir)
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, LOG_FILE_NAME)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{timestamp} [{level}] {message}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


class SnapshotError(Exception):
    """快照操作基础错误"""
    pass


class SnapshotNotFoundError(SnapshotError):
    """快照不存在"""
    pass


class SnapshotConflictError(SnapshotError):
    """快照名称冲突"""
    pass


class SnapshotFormatError(SnapshotError):
    """快照格式错误（坏JSON、缺字段等）"""
    pass


class SnapshotPermissionError(SnapshotError):
    """文件/目录权限不足"""
    pass


class SnapshotBatchNotFoundError(SnapshotError):
    """来源批次不存在"""
    pass


@dataclass
class Snapshot:
    """快照数据模型"""
    name: str
    description: str
    source_batch_name: str
    source_rules: Dict[str, Any]
    issues: List[Dict[str, Any]]
    status_distribution: Dict[str, int]
    issue_count: int
    data_dir: str
    rules_path: str
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "source_batch_name": self.source_batch_name,
            "source_rules": self.source_rules,
            "issues": self.issues,
            "status_distribution": self.status_distribution,
            "issue_count": self.issue_count,
            "data_dir": self.data_dir,
            "rules_path": self.rules_path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Snapshot":
        required_fields = [
            "name", "description", "source_batch_name",
            "source_rules", "issues", "status_distribution",
            "issue_count", "data_dir", "rules_path"
        ]
        missing = [f for f in required_fields if f not in data]
        if missing:
            raise SnapshotFormatError(
                f"快照缺少必填字段: {', '.join(missing)}"
            )
        if not isinstance(data["name"], str) or not data["name"].strip():
            raise SnapshotFormatError("快照 name 必须是非空字符串")
        if not isinstance(data["description"], str):
            raise SnapshotFormatError("快照 description 必须是字符串")
        if not isinstance(data["source_batch_name"], str):
            raise SnapshotFormatError("快照 source_batch_name 必须是字符串")
        if not isinstance(data["source_rules"], dict):
            raise SnapshotFormatError("快照 source_rules 必须是对象")
        if not isinstance(data["issues"], list):
            raise SnapshotFormatError("快照 issues 必须是数组")
        if not isinstance(data["status_distribution"], dict):
            raise SnapshotFormatError("快照 status_distribution 必须是对象")
        if not isinstance(data["issue_count"], int):
            raise SnapshotFormatError("快照 issue_count 必须是整数")
        if not isinstance(data["data_dir"], str):
            raise SnapshotFormatError("快照 data_dir 必须是字符串")
        if not isinstance(data["rules_path"], str):
            raise SnapshotFormatError("快照 rules_path 必须是字符串")
        return cls(
            name=str(data["name"]).strip(),
            description=str(data["description"]),
            source_batch_name=str(data["source_batch_name"]),
            source_rules=dict(data["source_rules"]),
            issues=list(data["issues"]),
            status_distribution=dict(data["status_distribution"]),
            issue_count=int(data["issue_count"]),
            data_dir=str(data["data_dir"]),
            rules_path=str(data["rules_path"]),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )

    @classmethod
    def from_batch(
        cls,
        name: str,
        description: str,
        batch_state: BatchState,
    ) -> "Snapshot":
        name = name.strip()
        if not name:
            raise SnapshotFormatError("快照名称不能为空")

        issues_list = [i.to_dict() for i in batch_state.issues.values()]
        status_dist: Dict[str, int] = {}
        for issue in batch_state.issues.values():
            status_value = issue.status.value if isinstance(issue.status, ReviewStatus) else str(issue.status)
            status_dist[status_value] = status_dist.get(status_value, 0) + 1

        rules_snapshot = {
            "batch_name": batch_state.rules.get("batch_name", ""),
            "root_alias": batch_state.rules.get("root_alias", ""),
            "required_files_count": len(batch_state.rules.get("required_files", [])),
            "naming_rules_count": len(batch_state.rules.get("naming_rules", [])),
            "ignore_patterns_count": len(batch_state.rules.get("ignore_patterns", [])),
            "expiry_date": batch_state.rules.get("expiry_date"),
            "source_path": batch_state.rules.get("source_path", ""),
            "source_hash": batch_state.rules_hash,
        }

        now = datetime.now().isoformat(timespec="seconds")
        return cls(
            name=name,
            description=description,
            source_batch_name=batch_state.batch_name,
            source_rules=rules_snapshot,
            issues=issues_list,
            status_distribution=status_dist,
            issue_count=len(issues_list),
            data_dir=batch_state.data_dir,
            rules_path=batch_state.rules.get("source_path", ""),
            created_at=now,
            updated_at=now,
        )

    def rules_summary(self) -> str:
        parts = []
        if self.source_rules.get("required_files_count"):
            parts.append(f"{self.source_rules['required_files_count']} 条必需文件规则")
        if self.source_rules.get("naming_rules_count"):
            parts.append(f"{self.source_rules['naming_rules_count']} 条命名规则")
        if self.source_rules.get("ignore_patterns_count"):
            parts.append(f"{self.source_rules['ignore_patterns_count']} 条忽略规则")
        return ", ".join(parts) if parts else "(无规则信息)"


def _safe_filename(name: str) -> str:
    def _safe(s: str) -> str:
        return "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in s)
    return f"{_safe(name)}{SNAPSHOT_FILE_SUFFIX}"


def _get_snapshots_dir(base_dir: str) -> str:
    from .state import STATE_DIR_NAME
    return os.path.join(base_dir, STATE_DIR_NAME, SNAPSHOTS_DIR_NAME)


def _get_index_path(base_dir: str) -> str:
    return os.path.join(_get_snapshots_dir(base_dir), INDEX_FILE_NAME)


def _get_snapshot_path(base_dir: str, name: str) -> str:
    return os.path.join(_get_snapshots_dir(base_dir), _safe_filename(name))


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    dir_path = os.path.dirname(path)
    try:
        os.makedirs(dir_path, exist_ok=True)
    except (PermissionError, OSError) as e:
        raise SnapshotPermissionError(f"无法创建目录 {dir_path}: {e}")

    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except (PermissionError, OSError) as e:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        raise SnapshotPermissionError(f"写入文件失败 {tmp_path}: {e}")

    try:
        shutil.move(tmp_path, path)
    except (PermissionError, OSError) as e:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        raise SnapshotPermissionError(f"替换文件失败 {path}: {e}")


def _read_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise SnapshotNotFoundError(f"文件不存在: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except (PermissionError, OSError) as e:
        raise SnapshotPermissionError(f"读取文件失败 {path}: {e}")
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise SnapshotFormatError(f"JSON 解析失败 {path}: {e}")


def _load_index(base_dir: str) -> Dict[str, Any]:
    index_path = _get_index_path(base_dir)
    if not os.path.exists(index_path):
        return {
            "version": 1,
            "snapshots": [],
        }
    data = _read_json(index_path)
    if not isinstance(data, dict) or "snapshots" not in data:
        raise SnapshotFormatError(f"索引文件格式错误: {index_path}")
    if not isinstance(data["snapshots"], list):
        raise SnapshotFormatError(f"索引文件 snapshots 必须是数组: {index_path}")
    return data


def _save_index(base_dir: str, index: Dict[str, Any]) -> None:
    _atomic_write_json(_get_index_path(base_dir), index)


def _find_in_index(index: Dict[str, Any], name: str) -> Optional[int]:
    for i, snapshot_info in enumerate(index.get("snapshots", [])):
        if (
            isinstance(snapshot_info, dict)
            and snapshot_info.get("name") == name
        ):
            return i
    return None


def _generate_rename_name(base_dir: str, original_name: str) -> str:
    index = _load_index(base_dir)
    existing_names = {
        info.get("name") for info in index.get("snapshots", []) if isinstance(info, dict)}
    counter = 1
    while True:
        candidate = f"{original_name}_{counter}"
        if candidate not in existing_names:
            return candidate
        counter += 1


def create_snapshot(
    base_dir: str,
    name: str,
    description: str,
    source_batch_name: str,
) -> Snapshot:
    """从批次创建快照

    Args:
        base_dir: 工作目录
        name: 快照名称
        description: 快照说明
        source_batch_name: 来源批次名称

    Returns:
        创建后的 Snapshot 对象

    Raises:
        SnapshotBatchNotFoundError: 来源批次不存在
        SnapshotConflictError: 同名快照已存在
        SnapshotFormatError: 名称格式错误
        SnapshotPermissionError: 写入权限不足
    """
    name = name.strip()
    description = description or ""

    _write_log(base_dir, "INFO", f"创建快照请求: name={name}, source_batch={source_batch_name}")

    if not BatchState.exists(base_dir, source_batch_name):
        _write_log(base_dir, "ERROR", f"创建失败: 来源批次不存在 {source_batch_name}")
        raise SnapshotBatchNotFoundError(
            f"来源批次不存在: {source_batch_name}"
        )

    try:
        batch_state = BatchState.load(base_dir, source_batch_name)
    except Exception as e:
        _write_log(base_dir, "ERROR", f"创建失败: 读取批次失败 {source_batch_name}: {e}")
        raise SnapshotBatchNotFoundError(
            f"读取来源批次失败: {source_batch_name}: {e}"
        )

    index = _load_index(base_dir)
    existing_idx = _find_in_index(index, name)

    if existing_idx is not None:
        _write_log(base_dir, "ERROR", f"创建失败: 快照名称冲突 {name}")
        raise SnapshotConflictError(
            f"快照「{name}」已存在。"
        )

    snapshot = Snapshot.from_batch(name, description, batch_state)
    snapshot_path = _get_snapshot_path(base_dir, name)
    _atomic_write_json(snapshot_path, snapshot.to_dict())

    snapshot_info = {
        "name": snapshot.name,
        "description": snapshot.description,
        "source_batch_name": snapshot.source_batch_name,
        "issue_count": snapshot.issue_count,
        "status_distribution": snapshot.status_distribution,
        "rules_path": snapshot.rules_path,
        "data_dir": snapshot.data_dir,
        "source_rules_batch_name": snapshot.source_rules.get("batch_name", ""),
        "source_rules_required_count": snapshot.source_rules.get("required_files_count", 0),
        "source_rules_naming_count": snapshot.source_rules.get("naming_rules_count", 0),
        "created_at": snapshot.created_at,
        "updated_at": snapshot.updated_at,
        "file": os.path.basename(snapshot_path),
    }

    try:
        index["snapshots"].append(snapshot_info)
        _save_index(base_dir, index)
    except Exception as e:
        _write_log(base_dir, "ERROR", f"创建失败: 更新索引失败，开始回滚: {e}")
        try:
            if os.path.exists(snapshot_path):
                os.remove(snapshot_path)
        except Exception:
            pass
        raise SnapshotPermissionError(f"更新索引失败，已回滚快照文件: {e}")

    _write_log(base_dir, "INFO", f"快照创建成功: {name}, issues={snapshot.issue_count}")
    return snapshot


def list_snapshots(base_dir: str) -> List[Dict[str, Any]]:
    """列出所有快照

    Returns:
        快照信息列表，按创建时间倒序
    """
    try:
        index = _load_index(base_dir)
    except Exception as e:
        _write_log(base_dir, "ERROR", f"列出快照失败: 读取索引失败: {e}")
        raise
    snapshots = []
    for snapshot_info in index.get("snapshots", []):
        if isinstance(snapshot_info, dict):
            snapshots.append(dict(snapshot_info))
    snapshots.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    _write_log(base_dir, "DEBUG", f"列出快照: 共 {len(snapshots)} 个")
    return snapshots


def get_snapshot(base_dir: str, name: str) -> Snapshot:
    """获取指定的快照

    Raises:
        SnapshotNotFoundError: 快照不存在
        SnapshotFormatError: 快照文件格式错误
        SnapshotPermissionError: 读取权限不足
    """
    _write_log(base_dir, "DEBUG", f"读取快照: {name}")
    index = _load_index(base_dir)
    idx = _find_in_index(index, name)
    if idx is None:
        _write_log(base_dir, "ERROR", f"读取快照失败: 不存在 {name}")
        raise SnapshotNotFoundError(
            f"快照不存在: {name}"
        )

    snapshot_path = _get_snapshot_path(base_dir, name)
    try:
        data = _read_json(snapshot_path)
    except SnapshotFormatError as e:
        _write_log(base_dir, "ERROR", f"读取快照失败: 文件损坏 {snapshot_path}: {e}")
        raise
    except Exception as e:
        _write_log(base_dir, "ERROR", f"读取快照失败: {name}: {e}")
        raise
    _write_log(base_dir, "INFO", f"读取快照成功: {name}, issues={data.get('issue_count', 0)}")
    return Snapshot.from_dict(data)


def delete_snapshot(base_dir: str, name: str) -> None:
    """删除快照

    Raises:
        SnapshotNotFoundError: 快照不存在
        SnapshotPermissionError: 删除权限不足
    """
    _write_log(base_dir, "INFO", f"删除快照请求: {name}")
    index = _load_index(base_dir)
    idx = _find_in_index(index, name)
    if idx is None:
        _write_log(base_dir, "ERROR", f"删除失败: 快照不存在 {name}")
        raise SnapshotNotFoundError(
            f"快照不存在: {name}"
        )

    snapshot_path = _get_snapshot_path(base_dir, name)
    old_index_data = json.dumps(index, ensure_ascii=False, indent=2)

    try:
        del index["snapshots"][idx]
        _save_index(base_dir, index)
    except Exception as e:
        _write_log(base_dir, "ERROR", f"删除失败: 更新索引失败，开始回滚: {e}")
        try:
            _atomic_write_json(_get_index_path(base_dir), json.loads(old_index_data))
        except Exception:
            pass
        raise SnapshotPermissionError(f"更新索引失败: {e}")

    if os.path.exists(snapshot_path):
        try:
            os.remove(snapshot_path)
        except Exception as e:
            _write_log(base_dir, "ERROR", f"删除失败: 快照文件删除失败 {snapshot_path}: {e}")
            raise SnapshotPermissionError(
                f"索引已更新，但删除快照文件失败 {snapshot_path}: {e}"
            )
    _write_log(base_dir, "INFO", f"快照删除成功: {name}")


def export_snapshot(
    base_dir: str,
    name: str,
    output_path: str,
) -> str:
    """导出快照为单个 JSON 文件

    Args:
        base_dir: 工作目录
        name: 快照名称
        output_path: 导出文件路径

    Returns:
        实际导出的文件路径

    Raises:
        SnapshotNotFoundError: 快照不存在
        SnapshotPermissionError: 写入权限不足
    """
    _write_log(base_dir, "INFO", f"导出快照请求: {name} -> {output_path}")
    snapshot = get_snapshot(base_dir, name)
    export_data = {
        "format_version": 1,
        "type": "delivery-checker-snapshot",
        "snapshot": snapshot.to_dict(),
    }
    abs_output = os.path.abspath(output_path)
    try:
        _atomic_write_json(abs_output, export_data)
    except Exception as e:
        _write_log(base_dir, "ERROR", f"导出失败: 写入文件失败 {abs_output}: {e}")
        raise
    _write_log(base_dir, "INFO", f"快照导出成功: {name} -> {abs_output}")
    return abs_output


def import_snapshot(
    base_dir: str,
    input_path: str,
    conflict_strategy: str = "refuse",
    rename_name: Optional[str] = None,
) -> Snapshot:
    """从导出的 JSON 文件导入快照

    Args:
        base_dir: 工作目录
        input_path: 导入文件路径
        conflict_strategy: 冲突处理策略：overwrite/rename/refuse
        rename_name: 重命名导入的快照名称（None 则使用原名）

    Returns:
        导入后的 Snapshot 对象

    Raises:
        SnapshotFormatError: 导入文件格式错误
        SnapshotConflictError: 同名快照已存在且 strategy=refuse
        SnapshotPermissionError: 读取/写入权限不足
    """
    _write_log(base_dir, "INFO", f"导入快照请求: {input_path}, strategy={conflict_strategy}, rename={rename_name}")

    if conflict_strategy not in ("overwrite", "rename", "refuse"):
        _write_log(base_dir, "ERROR", f"导入失败: 无效的冲突策略 {conflict_strategy}")
        raise SnapshotFormatError(
            f"无效的冲突处理策略: {conflict_strategy}（有效值: overwrite, rename, refuse）"
        )

    abs_input = os.path.abspath(input_path)
    try:
        data = _read_json(abs_input)
    except SnapshotNotFoundError as e:
        _write_log(base_dir, "ERROR", f"导入失败: 文件不存在 {abs_input}: {e}")
        raise
    except SnapshotFormatError as e:
        _write_log(base_dir, "ERROR", f"导入失败: JSON 格式错误 {abs_input}: {e}")
        raise

    if not isinstance(data, dict):
        _write_log(base_dir, "ERROR", f"导入失败: 根节点不是对象 {abs_input}")
        raise SnapshotFormatError("导入文件格式错误：根节点必须是对象")
    if data.get("type") != "delivery-checker-snapshot":
        _write_log(base_dir, "ERROR", f"导入失败: 类型标记错误 {abs_input}: type={data.get('type')}")
        raise SnapshotFormatError(
            "导入文件格式错误：不是有效的 delivery-checker 快照导出文件"
        )
    if "snapshot" not in data or not isinstance(data["snapshot"], dict):
        _write_log(base_dir, "ERROR", f"导入失败: 缺少 snapshot 字段 {abs_input}")
        raise SnapshotFormatError("导入文件格式错误：缺少 snapshot 字段")

    snapshot_data = data["snapshot"]
    try:
        snapshot = Snapshot.from_dict(snapshot_data)
    except SnapshotFormatError as e:
        _write_log(base_dir, "ERROR", f"导入失败: 快照数据格式错误 {abs_input}: {e}")
        raise

    new_name = rename_name.strip() if rename_name else snapshot.name
    if not new_name:
        _write_log(base_dir, "ERROR", f"导入失败: 快照名称为空")
        raise SnapshotFormatError("快照名称不能为空")

    index = _load_index(base_dir)
    existing_idx = _find_in_index(index, new_name)

    if existing_idx is not None:
        if conflict_strategy == "refuse":
            _write_log(base_dir, "ERROR", f"导入失败: 名称冲突且策略为 refuse: {new_name}")
            raise SnapshotConflictError(
                f"快照「{new_name}」已存在。"
            )
        elif conflict_strategy == "rename":
            new_name = _generate_rename_name(base_dir, new_name)
            _write_log(base_dir, "INFO", f"导入: 自动重命名为 {new_name}")
            existing_idx = None
        elif conflict_strategy == "overwrite":
            _write_log(base_dir, "INFO", f"导入: 覆盖已有快照 {new_name}")

    snapshot.name = new_name
    snapshot.updated_at = datetime.now().isoformat(timespec="seconds")

    if existing_idx is not None:
        old_info = index["snapshots"][existing_idx]
        snapshot.created_at = str(old_info.get("created_at", snapshot.created_at))

    snapshot_path = _get_snapshot_path(base_dir, new_name)
    try:
        _atomic_write_json(snapshot_path, snapshot.to_dict())
    except Exception as e:
        _write_log(base_dir, "ERROR", f"导入失败: 写入快照文件失败 {snapshot_path}: {e}")
        raise

    snapshot_info = {
        "name": snapshot.name,
        "description": snapshot.description,
        "source_batch_name": snapshot.source_batch_name,
        "issue_count": snapshot.issue_count,
        "status_distribution": snapshot.status_distribution,
        "rules_path": snapshot.rules_path,
        "data_dir": snapshot.data_dir,
        "source_rules_batch_name": snapshot.source_rules.get("batch_name", ""),
        "source_rules_required_count": snapshot.source_rules.get("required_files_count", 0),
        "source_rules_naming_count": snapshot.source_rules.get("naming_rules_count", 0),
        "created_at": snapshot.created_at,
        "updated_at": snapshot.updated_at,
        "file": os.path.basename(snapshot_path),
    }

    old_index_data = json.dumps(index, ensure_ascii=False, indent=2)

    try:
        if existing_idx is not None:
            index["snapshots"][existing_idx] = snapshot_info
        else:
            index["snapshots"].append(snapshot_info)
        _save_index(base_dir, index)
    except Exception as e:
        _write_log(base_dir, "ERROR", f"导入失败: 更新索引失败，开始回滚: {e}")
        try:
            if os.path.exists(snapshot_path):
                os.remove(snapshot_path)
        except Exception:
            pass
        try:
            _atomic_write_json(_get_index_path(base_dir), json.loads(old_index_data))
        except Exception:
            pass
        raise SnapshotPermissionError(f"更新索引失败，已回滚: {e}")

    _write_log(base_dir, "INFO", f"快照导入成功: {new_name} (原名称: {snapshot_data.get('name', 'N/A')})")
    return snapshot
