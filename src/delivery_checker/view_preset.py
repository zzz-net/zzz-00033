from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


VIEW_PRESETS_DIR_NAME = "view_presets"
INDEX_FILE_NAME = "index.json"
VIEW_PRESET_FILE_SUFFIX = ".preset.json"

VALID_ISSUE_TYPES = {"missing", "naming", "expired", "duplicate", "untracked"}
VALID_REVIEW_STATUSES = {"pending", "passed", "ignored", "todo"}
VALID_SORT_FIELDS = {"type", "path", "status", "reviewed_at", "created_at", "id"}
VALID_SORT_ORDERS = {"asc", "desc"}


class ViewPresetError(Exception):
    pass


class ViewPresetNotFoundError(ViewPresetError):
    pass


class ViewPresetConflictError(ViewPresetError):
    pass


class ViewPresetFormatError(ViewPresetError):
    pass


class ViewPresetPermissionError(ViewPresetError):
    pass


@dataclass
class ViewPreset:
    name: str
    description: str = ""
    issue_types: List[str] = field(default_factory=list)
    review_statuses: List[str] = field(default_factory=list)
    path_keyword: str = ""
    sort_by: str = "type"
    sort_order: str = "asc"
    default_reviewer: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "issue_types": list(self.issue_types),
            "review_statuses": list(self.review_statuses),
            "path_keyword": self.path_keyword,
            "sort_by": self.sort_by,
            "sort_order": self.sort_order,
            "default_reviewer": self.default_reviewer,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ViewPreset":
        required_fields = ["name"]
        missing = [f for f in required_fields if f not in data]
        if missing:
            raise ViewPresetFormatError(
                f"视图预设缺少必填字段: {', '.join(missing)}"
            )
        if not isinstance(data["name"], str) or not data["name"].strip():
            raise ViewPresetFormatError("视图预设 name 必须是非空字符串")

        issue_types_raw = data.get("issue_types", []) or []
        review_statuses_raw = data.get("review_statuses", []) or []

        if not isinstance(issue_types_raw, list):
            raise ViewPresetFormatError("issue_types 必须是数组")
        if not isinstance(review_statuses_raw, list):
            raise ViewPresetFormatError("review_statuses 必须是数组")

        issue_types = []
        for t in issue_types_raw:
            if not isinstance(t, str):
                raise ViewPresetFormatError(f"issue_types 元素必须是字符串: {t}")
            tv = t.strip().lower()
            if tv and tv not in VALID_ISSUE_TYPES:
                raise ViewPresetFormatError(
                    f"无效的问题类型: {tv}（有效值: {', '.join(sorted(VALID_ISSUE_TYPES))}）"
                )
            if tv and tv not in issue_types:
                issue_types.append(tv)

        review_statuses = []
        for s in review_statuses_raw:
            if not isinstance(s, str):
                raise ViewPresetFormatError(f"review_statuses 元素必须是字符串: {s}")
            sv = s.strip().lower()
            if sv and sv not in VALID_REVIEW_STATUSES:
                raise ViewPresetFormatError(
                    f"无效的复核状态: {sv}（有效值: {', '.join(sorted(VALID_REVIEW_STATUSES))}）"
                )
            if sv and sv not in review_statuses:
                review_statuses.append(sv)

        sort_by = str(data.get("sort_by", "type")).strip().lower() or "type"
        if sort_by not in VALID_SORT_FIELDS:
            raise ViewPresetFormatError(
                f"无效的排序字段: {sort_by}（有效值: {', '.join(sorted(VALID_SORT_FIELDS))}）"
            )

        sort_order = str(data.get("sort_order", "asc")).strip().lower() or "asc"
        if sort_order not in VALID_SORT_ORDERS:
            raise ViewPresetFormatError(
                f"无效的排序方向: {sort_order}（有效值: asc, desc）"
            )

        return cls(
            name=str(data["name"]).strip(),
            description=str(data.get("description", "") or ""),
            issue_types=issue_types,
            review_statuses=review_statuses,
            path_keyword=str(data.get("path_keyword", "") or ""),
            sort_by=sort_by,
            sort_order=sort_order,
            default_reviewer=str(data.get("default_reviewer", "") or ""),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )

    @classmethod
    def new(
        cls,
        name: str,
        description: str = "",
        issue_types: Optional[List[str]] = None,
        review_statuses: Optional[List[str]] = None,
        path_keyword: str = "",
        sort_by: str = "type",
        sort_order: str = "asc",
        default_reviewer: str = "",
    ) -> "ViewPreset":
        name = name.strip()
        if not name:
            raise ViewPresetFormatError("视图预设名称不能为空")

        issue_types_normalized = []
        if issue_types:
            for t in issue_types:
                tv = t.strip().lower()
                if tv:
                    if tv not in VALID_ISSUE_TYPES:
                        raise ViewPresetFormatError(
                            f"无效的问题类型: {tv}（有效值: {', '.join(sorted(VALID_ISSUE_TYPES))}）"
                        )
                    if tv not in issue_types_normalized:
                        issue_types_normalized.append(tv)

        review_statuses_normalized = []
        if review_statuses:
            for s in review_statuses:
                sv = s.strip().lower()
                if sv:
                    if sv not in VALID_REVIEW_STATUSES:
                        raise ViewPresetFormatError(
                            f"无效的复核状态: {sv}（有效值: {', '.join(sorted(VALID_REVIEW_STATUSES))}）"
                        )
                    if sv not in review_statuses_normalized:
                        review_statuses_normalized.append(sv)

        sort_by_norm = sort_by.strip().lower() or "type"
        if sort_by_norm not in VALID_SORT_FIELDS:
            raise ViewPresetFormatError(
                f"无效的排序字段: {sort_by_norm}（有效值: {', '.join(sorted(VALID_SORT_FIELDS))}）"
            )

        sort_order_norm = sort_order.strip().lower() or "asc"
        if sort_order_norm not in VALID_SORT_ORDERS:
            raise ViewPresetFormatError(
                f"无效的排序方向: {sort_order_norm}（有效值: asc, desc）"
            )

        now = datetime.now().isoformat(timespec="seconds")
        return cls(
            name=name,
            description=description,
            issue_types=issue_types_normalized,
            review_statuses=review_statuses_normalized,
            path_keyword=path_keyword,
            sort_by=sort_by_norm,
            sort_order=sort_order_norm,
            default_reviewer=default_reviewer,
            created_at=now,
            updated_at=now,
        )


def _safe_filename(name: str) -> str:
    def _safe(s: str) -> str:
        return "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in s)
    return f"{_safe(name)}{VIEW_PRESET_FILE_SUFFIX}"


def _get_view_presets_dir(base_dir: str) -> str:
    from .state import STATE_DIR_NAME
    return os.path.join(base_dir, STATE_DIR_NAME, VIEW_PRESETS_DIR_NAME)


def _get_index_path(base_dir: str) -> str:
    return os.path.join(_get_view_presets_dir(base_dir), INDEX_FILE_NAME)


def _get_preset_path(base_dir: str, name: str) -> str:
    return os.path.join(_get_view_presets_dir(base_dir), _safe_filename(name))


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    dir_path = os.path.dirname(path)
    try:
        os.makedirs(dir_path, exist_ok=True)
    except (PermissionError, OSError) as e:
        raise ViewPresetPermissionError(f"无法创建目录 {dir_path}: {e}")

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
        raise ViewPresetPermissionError(f"写入文件失败 {tmp_path}: {e}")

    try:
        shutil.move(tmp_path, path)
    except (PermissionError, OSError) as e:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        raise ViewPresetPermissionError(f"替换文件失败 {path}: {e}")


def _read_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise ViewPresetNotFoundError(f"文件不存在: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except (PermissionError, OSError) as e:
        raise ViewPresetPermissionError(f"读取文件失败 {path}: {e}")
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise ViewPresetFormatError(f"JSON 解析失败 {path}: {e}")


def _load_index(base_dir: str) -> Dict[str, Any]:
    index_path = _get_index_path(base_dir)
    if not os.path.exists(index_path):
        return {
            "version": 1,
            "presets": [],
        }
    data = _read_json(index_path)
    if not isinstance(data, dict) or "presets" not in data:
        raise ViewPresetFormatError(f"索引文件格式错误: {index_path}")
    if not isinstance(data["presets"], list):
        raise ViewPresetFormatError(f"索引文件 presets 必须是数组: {index_path}")
    return data


def _save_index(base_dir: str, index: Dict[str, Any]) -> None:
    _atomic_write_json(_get_index_path(base_dir), index)


def _find_in_index(index: Dict[str, Any], name: str) -> Optional[int]:
    for i, preset_info in enumerate(index.get("presets", [])):
        if (
            isinstance(preset_info, dict)
            and preset_info.get("name") == name
        ):
            return i
    return None


def save_view_preset(
    base_dir: str,
    name: str,
    description: str = "",
    issue_types: Optional[List[str]] = None,
    review_statuses: Optional[List[str]] = None,
    path_keyword: str = "",
    sort_by: str = "type",
    sort_order: str = "asc",
    default_reviewer: str = "",
    force: bool = False,
) -> ViewPreset:
    index = _load_index(base_dir)
    existing_idx = _find_in_index(index, name)

    if existing_idx is not None and not force:
        raise ViewPresetConflictError(
            f"视图预设「{name}」已存在。"
        )

    preset = ViewPreset.new(
        name=name,
        description=description,
        issue_types=issue_types,
        review_statuses=review_statuses,
        path_keyword=path_keyword,
        sort_by=sort_by,
        sort_order=sort_order,
        default_reviewer=default_reviewer,
    )

    if existing_idx is not None:
        old_info = index["presets"][existing_idx]
        preset.created_at = str(old_info.get("created_at", preset.created_at))

    preset_path = _get_preset_path(base_dir, name)
    _atomic_write_json(preset_path, preset.to_dict())

    preset_info = {
        "name": preset.name,
        "description": preset.description,
        "issue_types": preset.issue_types,
        "review_statuses": preset.review_statuses,
        "path_keyword": preset.path_keyword,
        "sort_by": preset.sort_by,
        "sort_order": preset.sort_order,
        "default_reviewer": preset.default_reviewer,
        "created_at": preset.created_at,
        "updated_at": preset.updated_at,
        "file": os.path.basename(preset_path),
    }

    try:
        if existing_idx is not None:
            index["presets"][existing_idx] = preset_info
        else:
            index["presets"].append(preset_info)
        _save_index(base_dir, index)
    except Exception as e:
        try:
            if os.path.exists(preset_path):
                os.remove(preset_path)
        except Exception:
            pass
        raise ViewPresetPermissionError(f"更新索引失败，已回滚预设文件: {e}")

    return preset


def list_view_presets(base_dir: str) -> List[Dict[str, Any]]:
    index = _load_index(base_dir)
    presets = []
    for preset_info in index.get("presets", []):
        if isinstance(preset_info, dict):
            presets.append(dict(preset_info))
    presets.sort(key=lambda p: (p.get("name", "")))
    return presets


def get_view_preset(base_dir: str, name: str) -> ViewPreset:
    index = _load_index(base_dir)
    idx = _find_in_index(index, name)
    if idx is None:
        raise ViewPresetNotFoundError(
            f"视图预设不存在: {name}"
        )
    preset_path = _get_preset_path(base_dir, name)
    data = _read_json(preset_path)
    return ViewPreset.from_dict(data)


def delete_view_preset(base_dir: str, name: str) -> None:
    index = _load_index(base_dir)
    idx = _find_in_index(index, name)
    if idx is None:
        raise ViewPresetNotFoundError(
            f"视图预设不存在: {name}"
        )

    preset_path = _get_preset_path(base_dir, name)
    old_index_data = json.dumps(index, ensure_ascii=False, indent=2)

    try:
        del index["presets"][idx]
        _save_index(base_dir, index)
    except Exception as e:
        try:
            _atomic_write_json(_get_index_path(base_dir), json.loads(old_index_data))
        except Exception:
            pass
        raise ViewPresetPermissionError(f"更新索引失败: {e}")

    if os.path.exists(preset_path):
        try:
            os.remove(preset_path)
        except Exception as e:
            raise ViewPresetPermissionError(
                f"索引已更新，但删除预设文件失败 {preset_path}: {e}"
            )


def export_view_preset(
    base_dir: str,
    name: str,
    output_path: str,
) -> str:
    preset = get_view_preset(base_dir, name)
    export_data = {
        "format_version": 1,
        "type": "delivery-checker-view-preset",
        "preset": preset.to_dict(),
    }
    abs_output = os.path.abspath(output_path)
    _atomic_write_json(abs_output, export_data)
    return abs_output


def import_view_preset(
    base_dir: str,
    input_path: str,
    force: bool = False,
    rename_name: Optional[str] = None,
) -> ViewPreset:
    abs_input = os.path.abspath(input_path)
    data = _read_json(abs_input)

    if not isinstance(data, dict):
        raise ViewPresetFormatError("导入文件格式错误：根节点必须是对象")
    if data.get("type") != "delivery-checker-view-preset":
        raise ViewPresetFormatError(
            "导入文件格式错误：不是有效的 delivery-checker 视图预设导出文件"
        )
    if "preset" not in data or not isinstance(data["preset"], dict):
        raise ViewPresetFormatError("导入文件格式错误：缺少 preset 字段")

    preset_data = data["preset"]
    preset = ViewPreset.from_dict(preset_data)

    new_name = rename_name.strip() if rename_name else preset.name
    if not new_name:
        raise ViewPresetFormatError("视图预设名称不能为空")

    return save_view_preset(
        base_dir=base_dir,
        name=new_name,
        description=preset.description,
        issue_types=preset.issue_types,
        review_statuses=preset.review_statuses,
        path_keyword=preset.path_keyword,
        sort_by=preset.sort_by,
        sort_order=preset.sort_order,
        default_reviewer=preset.default_reviewer,
        force=force,
    )
