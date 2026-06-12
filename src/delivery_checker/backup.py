from __future__ import annotations

import json
import os
import platform
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from . import __version__


BACKUPS_DIR_NAME = "backups"
INDEX_FILE_NAME = "index.json"
BACKUP_FILE_SUFFIX = ".backup.json"
LOG_FILE_NAME = "backups.log"

BACKUP_FORMAT_VERSION = 1


def _write_log(base_dir: str, level: str, message: str) -> None:
    try:
        log_dir = _get_backups_dir(base_dir)
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, LOG_FILE_NAME)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{timestamp} [{level}] {message}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


class BackupError(Exception):
    pass


class BackupNotFoundError(BackupError):
    pass


class BackupConflictError(BackupError):
    pass


class BackupFormatError(BackupError):
    pass


class BackupPermissionError(BackupError):
    pass


class BackupVersionMismatchError(BackupError):
    pass


class BackupCorruptedError(BackupError):
    pass


@dataclass
class BackupManifest:
    name: str
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    source_dir: str = ""
    source_hostname: str = ""
    tool_version: str = ""
    format_version: int = BACKUP_FORMAT_VERSION
    include_batches: bool = True
    include_rule_packages: bool = True
    include_view_presets: bool = True
    include_snapshots: bool = True
    include_compare_configs: bool = True
    content_summary: Dict[str, int] = field(default_factory=dict)
    total_size_bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_dir": self.source_dir,
            "source_hostname": self.source_hostname,
            "tool_version": self.tool_version,
            "format_version": self.format_version,
            "include_batches": self.include_batches,
            "include_rule_packages": self.include_rule_packages,
            "include_view_presets": self.include_view_presets,
            "include_snapshots": self.include_snapshots,
            "include_compare_configs": self.include_compare_configs,
            "content_summary": dict(self.content_summary),
            "total_size_bytes": self.total_size_bytes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BackupManifest:
        if not isinstance(data, dict):
            raise BackupFormatError("manifest 必须是对象")
        if not isinstance(data.get("name"), str) or not data["name"].strip():
            raise BackupFormatError("manifest.name 必须是非空字符串")
        return cls(
            name=str(data["name"]).strip(),
            description=str(data.get("description", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            source_dir=str(data.get("source_dir", "")),
            source_hostname=str(data.get("source_hostname", "")),
            tool_version=str(data.get("tool_version", "")),
            format_version=int(data.get("format_version", BACKUP_FORMAT_VERSION)),
            include_batches=bool(data.get("include_batches", True)),
            include_rule_packages=bool(data.get("include_rule_packages", True)),
            include_view_presets=bool(data.get("include_view_presets", True)),
            include_snapshots=bool(data.get("include_snapshots", True)),
            include_compare_configs=bool(data.get("include_compare_configs", True)),
            content_summary=dict(data.get("content_summary", {})),
            total_size_bytes=int(data.get("total_size_bytes", 0)),
        )


def _safe_filename(name: str) -> str:
    def _safe(s: str) -> str:
        return "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in s)
    return f"{_safe(name)}{BACKUP_FILE_SUFFIX}"


def _get_backups_dir(base_dir: str) -> str:
    from .state import STATE_DIR_NAME
    return os.path.join(base_dir, STATE_DIR_NAME, BACKUPS_DIR_NAME)


def _get_index_path(base_dir: str) -> str:
    return os.path.join(_get_backups_dir(base_dir), INDEX_FILE_NAME)


def _get_backup_path(base_dir: str, name: str) -> str:
    return os.path.join(_get_backups_dir(base_dir), _safe_filename(name))


def _atomic_write_json(path: str, data: Any) -> None:
    dir_path = os.path.dirname(path)
    try:
        os.makedirs(dir_path, exist_ok=True)
    except (PermissionError, OSError) as e:
        raise BackupPermissionError(f"无法创建目录 {dir_path}: {e}")

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
        raise BackupPermissionError(f"写入文件失败 {tmp_path}: {e}")

    try:
        shutil.move(tmp_path, path)
    except (PermissionError, OSError) as e:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        raise BackupPermissionError(f"替换文件失败 {path}: {e}")


def _read_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise BackupNotFoundError(f"文件不存在: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except (PermissionError, OSError) as e:
        raise BackupPermissionError(f"读取文件失败 {path}: {e}")
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise BackupFormatError(f"JSON 解析失败 {path}: {e}")


def _load_index(base_dir: str) -> Dict[str, Any]:
    index_path = _get_index_path(base_dir)
    if not os.path.exists(index_path):
        return {"version": 1, "backups": []}
    data = _read_json(index_path)
    if not isinstance(data, dict) or "backups" not in data:
        raise BackupFormatError(f"索引文件格式错误: {index_path}")
    if not isinstance(data["backups"], list):
        raise BackupFormatError(f"索引文件 backups 必须是数组: {index_path}")
    return data


def _save_index(base_dir: str, index: Dict[str, Any]) -> None:
    _atomic_write_json(_get_index_path(base_dir), index)


def _find_in_index(index: Dict[str, Any], name: str) -> Optional[int]:
    for i, info in enumerate(index.get("backups", [])):
        if isinstance(info, dict) and info.get("name") == name:
            return i
    return None


def _generate_rename_name(base_dir: str, original_name: str) -> str:
    index = _load_index(base_dir)
    existing_names = {
        info.get("name") for info in index.get("backups", []) if isinstance(info, dict)
    }
    counter = 1
    while True:
        candidate = f"{original_name}_{counter}"
        if candidate not in existing_names:
            return candidate
        counter += 1


def _collect_batches(base_dir: str) -> Dict[str, Any]:
    from .state import STATE_DIR_NAME, STATE_FILE_SUFFIX
    state_dir = os.path.join(base_dir, STATE_DIR_NAME)
    if not os.path.isdir(state_dir):
        return {}
    batches: Dict[str, Any] = {}
    for fn in sorted(os.listdir(state_dir)):
        if not fn.endswith(STATE_FILE_SUFFIX):
            continue
        fp = os.path.join(state_dir, fn)
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            batch_name = data.get("batch_name", fn[: -len(STATE_FILE_SUFFIX)])
            batches[batch_name] = data
        except Exception:
            continue
    return batches


def _collect_rule_packages(base_dir: str) -> Dict[str, Any]:
    from .rule_pkg import _get_rule_pkgs_dir, _get_index_path as _rp_index_path
    rp_dir = _get_rule_pkgs_dir(base_dir)
    if not os.path.isdir(rp_dir):
        return {"index": {}, "packages": {}}
    index_data: Dict[str, Any] = {}
    idx_path = _rp_index_path(base_dir)
    if os.path.exists(idx_path):
        try:
            index_data = _read_json(idx_path)
        except Exception:
            index_data = {}
    packages: Dict[str, Any] = {}
    if os.path.isdir(rp_dir):
        for fn in os.listdir(rp_dir):
            if fn == INDEX_FILE_NAME or not fn.endswith(".json"):
                continue
            fp = os.path.join(rp_dir, fn)
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    packages[fn] = json.load(f)
            except Exception:
                continue
    return {"index": index_data, "packages": packages}


def _collect_view_presets(base_dir: str) -> Dict[str, Any]:
    from .view_preset import _get_view_presets_dir, _get_index_path as _vp_index_path
    vp_dir = _get_view_presets_dir(base_dir)
    if not os.path.isdir(vp_dir):
        return {"index": {}, "presets": {}}
    index_data: Dict[str, Any] = {}
    idx_path = _vp_index_path(base_dir)
    if os.path.exists(idx_path):
        try:
            index_data = _read_json(idx_path)
        except Exception:
            index_data = {}
    presets: Dict[str, Any] = {}
    if os.path.isdir(vp_dir):
        for fn in os.listdir(vp_dir):
            if fn == INDEX_FILE_NAME:
                continue
            if not fn.endswith(".preset.json"):
                continue
            fp = os.path.join(vp_dir, fn)
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    presets[fn] = json.load(f)
            except Exception:
                continue
    return {"index": index_data, "presets": presets}


def _collect_snapshots(base_dir: str) -> Dict[str, Any]:
    from .snapshot import _get_snapshots_dir, _get_index_path as _sn_index_path
    sn_dir = _get_snapshots_dir(base_dir)
    if not os.path.isdir(sn_dir):
        return {"index": {}, "snapshots": {}}
    index_data: Dict[str, Any] = {}
    idx_path = _sn_index_path(base_dir)
    if os.path.exists(idx_path):
        try:
            index_data = _read_json(idx_path)
        except Exception:
            index_data = {}
    snapshots: Dict[str, Any] = {}
    if os.path.isdir(sn_dir):
        for fn in os.listdir(sn_dir):
            if fn in (INDEX_FILE_NAME, "snapshots.log"):
                continue
            if not fn.endswith(".snapshot.json"):
                continue
            fp = os.path.join(sn_dir, fn)
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    snapshots[fn] = json.load(f)
            except Exception:
                continue
    return {"index": index_data, "snapshots": snapshots}


def _collect_compare_configs(base_dir: str) -> Dict[str, Any]:
    from .compare import _get_compare_config_dir, _get_compare_index_path as _cc_index_path
    cc_dir = _get_compare_config_dir(base_dir)
    if not os.path.isdir(cc_dir):
        return {"index": {}, "configs": {}}
    index_data: Dict[str, Any] = {}
    idx_path = _cc_index_path(base_dir)
    if os.path.exists(idx_path):
        try:
            index_data = _read_json(idx_path)
        except Exception:
            index_data = {}
    configs: Dict[str, Any] = {}
    if os.path.isdir(cc_dir):
        for fn in os.listdir(cc_dir):
            if fn == INDEX_FILE_NAME:
                continue
            if not fn.endswith(".compare.json"):
                continue
            fp = os.path.join(cc_dir, fn)
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    configs[fn] = json.load(f)
            except Exception:
                continue
    return {"index": index_data, "configs": configs}


def _compute_content_summary(data: Dict[str, Any], manifest: BackupManifest) -> Dict[str, int]:
    summary: Dict[str, int] = {}
    if manifest.include_batches:
        summary["batch_count"] = len(data.get("batches", {}))
    if manifest.include_rule_packages:
        summary["rule_package_count"] = len(data.get("rule_packages", {}).get("packages", {}))
    if manifest.include_view_presets:
        summary["view_preset_count"] = len(data.get("view_presets", {}).get("presets", {}))
    if manifest.include_snapshots:
        summary["snapshot_count"] = len(data.get("snapshots", {}).get("snapshots", {}))
    if manifest.include_compare_configs:
        summary["compare_config_count"] = len(data.get("compare_configs", {}).get("configs", {}))
    return summary


def create_backup(
    base_dir: str,
    name: str,
    description: str = "",
    include_batches: bool = True,
    include_rule_packages: bool = True,
    include_view_presets: bool = True,
    include_snapshots: bool = True,
    include_compare_configs: bool = True,
) -> Tuple[BackupManifest, Dict[str, Any]]:
    name = name.strip()
    if not name:
        raise BackupFormatError("备份名称不能为空")
    description = description or ""

    _write_log(base_dir, "INFO", f"创建备份请求: name={name}")

    index = _load_index(base_dir)
    existing_idx = _find_in_index(index, name)
    if existing_idx is not None:
        _write_log(base_dir, "ERROR", f"创建失败: 备份名称冲突 {name}")
        raise BackupConflictError(f"备份「{name}」已存在。")

    now = datetime.now().isoformat(timespec="seconds")
    manifest = BackupManifest(
        name=name,
        description=description,
        created_at=now,
        updated_at=now,
        source_dir=os.path.abspath(base_dir),
        source_hostname=platform.node(),
        tool_version=__version__,
        format_version=BACKUP_FORMAT_VERSION,
        include_batches=include_batches,
        include_rule_packages=include_rule_packages,
        include_view_presets=include_view_presets,
        include_snapshots=include_snapshots,
        include_compare_configs=include_compare_configs,
    )

    data: Dict[str, Any] = {}
    if include_batches:
        data["batches"] = _collect_batches(base_dir)
    if include_rule_packages:
        data["rule_packages"] = _collect_rule_packages(base_dir)
    if include_view_presets:
        data["view_presets"] = _collect_view_presets(base_dir)
    if include_snapshots:
        data["snapshots"] = _collect_snapshots(base_dir)
    if include_compare_configs:
        data["compare_configs"] = _collect_compare_configs(base_dir)

    manifest.content_summary = _compute_content_summary(data, manifest)

    backup_file = {
        "format_version": BACKUP_FORMAT_VERSION,
        "type": "delivery-checker-backup",
        "manifest": manifest.to_dict(),
        "data": data,
    }

    backup_path = _get_backup_path(base_dir, name)
    try:
        _atomic_write_json(backup_path, backup_file)
    except Exception as e:
        _write_log(base_dir, "ERROR", f"创建失败: 写入文件失败 {backup_path}: {e}")
        raise

    manifest.total_size_bytes = os.path.getsize(backup_path)

    backup_info = _manifest_to_index_info(manifest, os.path.basename(backup_path))

    try:
        index["backups"].append(backup_info)
        _save_index(base_dir, index)
    except Exception as e:
        _write_log(base_dir, "ERROR", f"创建失败: 更新索引失败，开始回滚: {e}")
        try:
            if os.path.exists(backup_path):
                os.remove(backup_path)
        except Exception:
            pass
        raise BackupPermissionError(f"更新索引失败，已回滚备份文件: {e}")

    _write_log(base_dir, "INFO", f"备份创建成功: {name}, summary={manifest.content_summary}")
    return manifest, data


def _manifest_to_index_info(manifest: BackupManifest, filename: str) -> Dict[str, Any]:
    return {
        "name": manifest.name,
        "description": manifest.description,
        "created_at": manifest.created_at,
        "updated_at": manifest.updated_at,
        "source_dir": manifest.source_dir,
        "source_hostname": manifest.source_hostname,
        "tool_version": manifest.tool_version,
        "format_version": manifest.format_version,
        "include_batches": manifest.include_batches,
        "include_rule_packages": manifest.include_rule_packages,
        "include_view_presets": manifest.include_view_presets,
        "include_snapshots": manifest.include_snapshots,
        "include_compare_configs": manifest.include_compare_configs,
        "content_summary": dict(manifest.content_summary),
        "total_size_bytes": manifest.total_size_bytes,
        "file": filename,
    }


def list_backups(base_dir: str) -> List[Dict[str, Any]]:
    try:
        index = _load_index(base_dir)
    except Exception as e:
        _write_log(base_dir, "ERROR", f"列出备份失败: 读取索引失败: {e}")
        raise
    backups = []
    for info in index.get("backups", []):
        if isinstance(info, dict):
            backups.append(dict(info))
    backups.sort(key=lambda b: b.get("created_at", ""), reverse=True)
    return backups


def get_backup(base_dir: str, name: str) -> Tuple[BackupManifest, Dict[str, Any]]:
    _write_log(base_dir, "DEBUG", f"读取备份: {name}")
    index = _load_index(base_dir)
    idx = _find_in_index(index, name)
    if idx is None:
        _write_log(base_dir, "ERROR", f"读取备份失败: 不存在 {name}")
        raise BackupNotFoundError(f"备份不存在: {name}")

    backup_path = _get_backup_path(base_dir, name)
    try:
        backup_file = _read_json(backup_path)
    except BackupFormatError as e:
        _write_log(base_dir, "ERROR", f"读取备份失败: 文件损坏 {backup_path}: {e}")
        raise BackupCorruptedError(f"备份文件损坏: {e}")
    except Exception as e:
        _write_log(base_dir, "ERROR", f"读取备份失败: {name}: {e}")
        raise

    if not isinstance(backup_file, dict):
        raise BackupCorruptedError("备份文件根节点必须是对象")

    raw_manifest = backup_file.get("manifest")
    if not isinstance(raw_manifest, dict):
        raise BackupCorruptedError("备份缺少 manifest 字段")

    manifest = BackupManifest.from_dict(raw_manifest)

    fv = backup_file.get("format_version", 0)
    if fv > BACKUP_FORMAT_VERSION:
        raise BackupVersionMismatchError(
            f"备份格式版本 v{fv} 高于当前工具支持版本 v{BACKUP_FORMAT_VERSION}，请升级工具"
        )

    data = backup_file.get("data", {})
    if not isinstance(data, dict):
        raise BackupCorruptedError("备份 data 字段必须是对象")

    if os.path.exists(backup_path):
        manifest.total_size_bytes = os.path.getsize(backup_path)

    return manifest, data


def show_backup(base_dir: str, name: str) -> Dict[str, Any]:
    manifest, data = get_backup(base_dir, name)

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

    size_bytes = manifest.total_size_bytes
    if size_bytes == 0:
        backup_path = _get_backup_path(base_dir, name)
        if os.path.exists(backup_path):
            size_bytes = os.path.getsize(backup_path)

    size_str = _human_readable_size(size_bytes)

    source_parts = []
    if manifest.source_dir:
        source_parts.append(f"目录: {manifest.source_dir}")
    if manifest.source_hostname:
        source_parts.append(f"主机: {manifest.source_hostname}")
    if manifest.tool_version:
        source_parts.append(f"工具版本: {manifest.tool_version}")

    return {
        "name": manifest.name,
        "description": manifest.description,
        "created_at": manifest.created_at,
        "updated_at": manifest.updated_at,
        "total_size_bytes": size_bytes,
        "total_size_human": size_str,
        "includes": includes,
        "content_summary": dict(manifest.content_summary),
        "source_summary": " | ".join(source_parts),
        "format_version": manifest.format_version,
    }


def _human_readable_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def delete_backup(base_dir: str, name: str) -> None:
    _write_log(base_dir, "INFO", f"删除备份请求: {name}")
    index = _load_index(base_dir)
    idx = _find_in_index(index, name)
    if idx is None:
        _write_log(base_dir, "ERROR", f"删除失败: 备份不存在 {name}")
        raise BackupNotFoundError(f"备份不存在: {name}")

    backup_path = _get_backup_path(base_dir, name)
    old_index_data = json.dumps(index, ensure_ascii=False, indent=2)

    try:
        del index["backups"][idx]
        _save_index(base_dir, index)
    except Exception as e:
        _write_log(base_dir, "ERROR", f"删除失败: 更新索引失败，开始回滚: {e}")
        try:
            _atomic_write_json(_get_index_path(base_dir), json.loads(old_index_data))
        except Exception:
            pass
        raise BackupPermissionError(f"更新索引失败: {e}")

    if os.path.exists(backup_path):
        try:
            os.remove(backup_path)
        except Exception as e:
            _write_log(base_dir, "ERROR", f"删除失败: 文件删除失败 {backup_path}: {e}")
            raise BackupPermissionError(f"索引已更新，但删除备份文件失败 {backup_path}: {e}")
    _write_log(base_dir, "INFO", f"备份删除成功: {name}")


def export_backup(
    base_dir: str,
    name: str,
    output_path: str,
    fmt: str = "json",
) -> str:
    _write_log(base_dir, "INFO", f"导出备份请求: {name} -> {output_path}, fmt={fmt}")
    manifest, data = get_backup(base_dir, name)

    export_data = {
        "format_version": BACKUP_FORMAT_VERSION,
        "type": "delivery-checker-backup",
        "manifest": manifest.to_dict(),
        "data": data,
    }

    abs_output = os.path.abspath(output_path)

    if fmt == "zip":
        try:
            os.makedirs(os.path.dirname(abs_output), exist_ok=True)
        except (PermissionError, OSError) as e:
            raise BackupPermissionError(f"无法创建目录 {os.path.dirname(abs_output)}: {e}")
        tmp_zip = abs_output + ".tmp"
        try:
            with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("backup.json", json.dumps(export_data, ensure_ascii=False, indent=2))
        except (PermissionError, OSError) as e:
            if os.path.exists(tmp_zip):
                try:
                    os.remove(tmp_zip)
                except Exception:
                    pass
            raise BackupPermissionError(f"写入 ZIP 失败 {tmp_zip}: {e}")
        try:
            shutil.move(tmp_zip, abs_output)
        except (PermissionError, OSError) as e:
            if os.path.exists(tmp_zip):
                try:
                    os.remove(tmp_zip)
                except Exception:
                    pass
            raise BackupPermissionError(f"替换文件失败 {abs_output}: {e}")
    else:
        try:
            _atomic_write_json(abs_output, export_data)
        except Exception as e:
            _write_log(base_dir, "ERROR", f"导出失败: 写入文件失败 {abs_output}: {e}")
            raise

    _write_log(base_dir, "INFO", f"备份导出成功: {name} -> {abs_output}")
    return abs_output


def import_backup(
    base_dir: str,
    input_path: str,
    conflict_strategy: str = "refuse",
    rename_name: Optional[str] = None,
) -> Tuple[BackupManifest, Dict[str, Any]]:
    _write_log(
        base_dir, "INFO",
        f"导入备份请求: {input_path}, strategy={conflict_strategy}, rename={rename_name}"
    )

    if conflict_strategy not in ("overwrite", "rename", "refuse"):
        raise BackupFormatError(
            f"无效的冲突处理策略: {conflict_strategy}（有效值: overwrite, rename, refuse）"
        )

    abs_input = os.path.abspath(input_path)
    if not os.path.exists(abs_input):
        _write_log(base_dir, "ERROR", f"导入失败: 文件不存在 {abs_input}")
        raise BackupNotFoundError(f"导入文件不存在: {abs_input}")

    export_data: Dict[str, Any]
    if zipfile.is_zipfile(abs_input):
        try:
            with zipfile.ZipFile(abs_input, "r") as zf:
                names = zf.namelist()
                backup_entry = None
                for n in names:
                    if n.endswith("backup.json") and not n.startswith("__MACOSX"):
                        backup_entry = n
                        break
                if backup_entry is None:
                    raise BackupFormatError("ZIP 文件中未找到 backup.json")
                content = zf.read(backup_entry).decode("utf-8")
                export_data = json.loads(content)
        except (zipfile.BadZipFile, json.JSONDecodeError) as e:
            _write_log(base_dir, "ERROR", f"导入失败: ZIP 文件损坏 {abs_input}: {e}")
            raise BackupCorruptedError(f"ZIP 文件损坏或格式错误: {e}")
        except BackupFormatError:
            raise
        except Exception as e:
            _write_log(base_dir, "ERROR", f"导入失败: 读取 ZIP 失败 {abs_input}: {e}")
            raise BackupCorruptedError(f"读取 ZIP 失败: {e}")
    else:
        try:
            export_data = _read_json(abs_input)
        except BackupNotFoundError:
            raise
        except BackupFormatError as e:
            _write_log(base_dir, "ERROR", f"导入失败: JSON 格式错误 {abs_input}: {e}")
            raise BackupCorruptedError(f"JSON 格式错误: {e}")

    if not isinstance(export_data, dict):
        raise BackupCorruptedError("导入文件根节点必须是对象")
    if export_data.get("type") != "delivery-checker-backup":
        raise BackupFormatError("导入文件不是有效的 delivery-checker 备份文件")
    if "manifest" not in export_data or not isinstance(export_data["manifest"], dict):
        raise BackupFormatError("导入文件缺少 manifest 字段")
    if "data" not in export_data or not isinstance(export_data["data"], dict):
        raise BackupFormatError("导入文件缺少 data 字段")

    fv = export_data.get("format_version", 0)
    if fv > BACKUP_FORMAT_VERSION:
        _write_log(base_dir, "ERROR", f"导入失败: 版本不兼容 v{fv}")
        raise BackupVersionMismatchError(
            f"备份格式版本 v{fv} 高于当前工具支持版本 v{BACKUP_FORMAT_VERSION}，请升级工具"
        )

    try:
        manifest = BackupManifest.from_dict(export_data["manifest"])
    except BackupFormatError as e:
        _write_log(base_dir, "ERROR", f"导入失败: manifest 格式错误: {e}")
        raise

    data = export_data["data"]

    new_name = rename_name.strip() if rename_name else manifest.name
    if not new_name:
        raise BackupFormatError("备份名称不能为空")

    index = _load_index(base_dir)
    existing_idx = _find_in_index(index, new_name)

    if existing_idx is not None:
        if conflict_strategy == "refuse":
            _write_log(base_dir, "ERROR", f"导入失败: 名称冲突且策略为 refuse: {new_name}")
            raise BackupConflictError(f"备份「{new_name}」已存在。")
        elif conflict_strategy == "rename":
            new_name = _generate_rename_name(base_dir, new_name)
            _write_log(base_dir, "INFO", f"导入: 自动重命名为 {new_name}")
            existing_idx = None
        elif conflict_strategy == "overwrite":
            _write_log(base_dir, "INFO", f"导入: 覆盖已有备份 {new_name}")

    manifest.name = new_name
    manifest.updated_at = datetime.now().isoformat(timespec="seconds")
    if existing_idx is not None:
        old_info = index["backups"][existing_idx]
        manifest.created_at = str(old_info.get("created_at", manifest.created_at))

    backup_file = {
        "format_version": BACKUP_FORMAT_VERSION,
        "type": "delivery-checker-backup",
        "manifest": manifest.to_dict(),
        "data": data,
    }

    backup_path = _get_backup_path(base_dir, new_name)
    try:
        _atomic_write_json(backup_path, backup_file)
    except Exception as e:
        _write_log(base_dir, "ERROR", f"导入失败: 写入文件失败 {backup_path}: {e}")
        raise

    manifest.total_size_bytes = os.path.getsize(backup_path) if os.path.exists(backup_path) else 0

    backup_info = _manifest_to_index_info(manifest, os.path.basename(backup_path))

    old_index_data = json.dumps(index, ensure_ascii=False, indent=2)
    try:
        if existing_idx is not None:
            index["backups"][existing_idx] = backup_info
        else:
            index["backups"].append(backup_info)
        _save_index(base_dir, index)
    except Exception as e:
        _write_log(base_dir, "ERROR", f"导入失败: 更新索引失败，开始回滚: {e}")
        try:
            if os.path.exists(backup_path):
                os.remove(backup_path)
        except Exception:
            pass
        try:
            _atomic_write_json(_get_index_path(base_dir), json.loads(old_index_data))
        except Exception:
            pass
        raise BackupPermissionError(f"更新索引失败，已回滚: {e}")

    _write_log(base_dir, "INFO", f"备份导入成功: {new_name}")
    return manifest, data


def preview_restore(
    base_dir: str,
    name: str,
) -> Dict[str, Any]:
    manifest, data = get_backup(base_dir, name)

    diff: Dict[str, Any] = {
        "backup_name": name,
        "sections": {},
    }

    if manifest.include_batches:
        diff["sections"]["batches"] = _diff_batches(base_dir, data.get("batches", {}))
    if manifest.include_rule_packages:
        diff["sections"]["rule_packages"] = _diff_rule_packages(
            base_dir, data.get("rule_packages", {})
        )
    if manifest.include_view_presets:
        diff["sections"]["view_presets"] = _diff_view_presets(
            base_dir, data.get("view_presets", {})
        )
    if manifest.include_snapshots:
        diff["sections"]["snapshots"] = _diff_snapshots(
            base_dir, data.get("snapshots", {})
        )
    if manifest.include_compare_configs:
        diff["sections"]["compare_configs"] = _diff_compare_configs(
            base_dir, data.get("compare_configs", {})
        )

    has_conflicts = False
    for section in diff["sections"].values():
        if section.get("conflicting"):
            has_conflicts = True
            break
    diff["has_conflicts"] = has_conflicts

    return diff


def _diff_batches(base_dir: str, backup_batches: Dict[str, Any]) -> Dict[str, Any]:
    current = _collect_batches(base_dir)
    return _compute_diff(current, backup_batches, "batch_name")


def _diff_rule_packages(base_dir: str, backup_rp: Dict[str, Any]) -> Dict[str, Any]:
    current_rp = _collect_rule_packages(base_dir)
    current = {}
    for fn, content in current_rp.get("packages", {}).items():
        name = content.get("name", fn)
        version = content.get("version", "")
        current[f"{name}:{version}"] = content
    backup = {}
    for fn, content in backup_rp.get("packages", {}).items():
        name = content.get("name", fn)
        version = content.get("version", "")
        backup[f"{name}:{version}"] = content
    return _compute_diff(current, backup, "name:version")


def _diff_view_presets(base_dir: str, backup_vp: Dict[str, Any]) -> Dict[str, Any]:
    current_vp = _collect_view_presets(base_dir)
    current = {}
    for fn, content in current_vp.get("presets", {}).items():
        name = content.get("name", fn)
        current[name] = content
    backup = {}
    for fn, content in backup_vp.get("presets", {}).items():
        name = content.get("name", fn)
        backup[name] = content
    return _compute_diff(current, backup, "name")


def _diff_snapshots(base_dir: str, backup_sn: Dict[str, Any]) -> Dict[str, Any]:
    current_sn = _collect_snapshots(base_dir)
    current = {}
    for fn, content in current_sn.get("snapshots", {}).items():
        name = content.get("name", fn)
        current[name] = content
    backup = {}
    for fn, content in backup_sn.get("snapshots", {}).items():
        name = content.get("name", fn)
        backup[name] = content
    return _compute_diff(current, backup, "name")


def _diff_compare_configs(base_dir: str, backup_cc: Dict[str, Any]) -> Dict[str, Any]:
    current_cc = _collect_compare_configs(base_dir)
    current = {}
    for fn, content in current_cc.get("configs", {}).items():
        name = content.get("name", fn)
        current[name] = content
    backup = {}
    for fn, content in backup_cc.get("configs", {}).items():
        name = content.get("name", fn)
        backup[name] = content
    return _compute_diff(current, backup, "name")


def _compute_diff(
    current: Dict[str, Any],
    backup: Dict[str, Any],
    key_label: str,
) -> Dict[str, Any]:
    new_items = []
    conflicting = []
    unchanged = []

    current_keys = set(current.keys())
    backup_keys = set(backup.keys())

    for key in sorted(backup_keys - current_keys):
        new_items.append({"key": key, "label": key_label, "data_summary": _summarize_item(backup[key])})

    for key in sorted(backup_keys & current_keys):
        if json.dumps(current[key], sort_keys=True) != json.dumps(backup[key], sort_keys=True):
            conflicting.append({
                "key": key,
                "label": key_label,
                "current_summary": _summarize_item(current[key]),
                "backup_summary": _summarize_item(backup[key]),
            })
        else:
            unchanged.append({"key": key, "label": key_label})

    return {
        "new": new_items,
        "conflicting": conflicting,
        "unchanged": unchanged,
        "new_count": len(new_items),
        "conflict_count": len(conflicting),
        "unchanged_count": len(unchanged),
    }


def _summarize_item(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item)[:80]
    parts = []
    for k in ("name", "batch_name", "version", "description"):
        v = item.get(k)
        if v:
            parts.append(f"{k}={v}")
    if not parts:
        s = json.dumps(item, ensure_ascii=False)
        if len(s) > 80:
            s = s[:77] + "..."
        return s
    return " | ".join(parts)


def apply_restore(
    base_dir: str,
    name: str,
    conflict_strategy: str = "skip",
) -> Dict[str, Any]:
    _write_log(base_dir, "INFO", f"恢复备份请求: {name}, strategy={conflict_strategy}")

    if conflict_strategy not in ("overwrite", "skip", "abort"):
        raise BackupFormatError(
            f"无效的冲突处理策略: {conflict_strategy}（有效值: overwrite, skip, abort）"
        )

    manifest, data = get_backup(base_dir, name)

    result: Dict[str, Any] = {
        "backup_name": name,
        "sections": {},
        "errors": [],
    }

    if conflict_strategy == "abort":
        diff = preview_restore(base_dir, name)
        if diff.get("has_conflicts"):
            _write_log(base_dir, "ERROR", f"恢复失败: 存在冲突且策略为 abort")
            raise BackupConflictError(
                "恢复中止：存在数据冲突，使用 --conflict overwrite 或 skip 处理冲突"
            )

    if manifest.include_batches:
        r = _restore_batches(base_dir, data.get("batches", {}), conflict_strategy)
        result["sections"]["batches"] = r
    if manifest.include_rule_packages:
        r = _restore_rule_packages(
            base_dir, data.get("rule_packages", {}), conflict_strategy
        )
        result["sections"]["rule_packages"] = r
    if manifest.include_view_presets:
        r = _restore_view_presets(
            base_dir, data.get("view_presets", {}), conflict_strategy
        )
        result["sections"]["view_presets"] = r
    if manifest.include_snapshots:
        r = _restore_snapshots(base_dir, data.get("snapshots", {}), conflict_strategy)
        result["sections"]["snapshots"] = r
    if manifest.include_compare_configs:
        r = _restore_compare_configs(
            base_dir, data.get("compare_configs", {}), conflict_strategy
        )
        result["sections"]["compare_configs"] = r

    _write_log(base_dir, "INFO", f"恢复备份完成: {name}")
    return result


def _restore_batches(
    base_dir: str,
    backup_batches: Dict[str, Any],
    conflict_strategy: str,
) -> Dict[str, Any]:
    from .state import STATE_DIR_NAME, STATE_FILE_SUFFIX, BatchState

    current = _collect_batches(base_dir)
    added = []
    skipped = []
    overwritten = []
    errors = []

    for batch_name, batch_data in backup_batches.items():
        safe_name = "".join(
            c if c.isalnum() or c in ("-", "_") else "_" for c in batch_name
        )
        state_path = os.path.join(base_dir, STATE_DIR_NAME, f"{safe_name}{STATE_FILE_SUFFIX}")
        exists = batch_name in current

        if exists:
            if conflict_strategy == "skip":
                skipped.append(batch_name)
                continue
            elif conflict_strategy == "overwrite":
                try:
                    BatchState.from_dict(batch_data)
                    _atomic_write_json(state_path, batch_data)
                    overwritten.append(batch_name)
                except Exception as e:
                    errors.append(f"批次 {batch_name}: {e}")
            else:
                skipped.append(batch_name)
        else:
            try:
                BatchState.from_dict(batch_data)
                _atomic_write_json(state_path, batch_data)
                added.append(batch_name)
            except Exception as e:
                errors.append(f"批次 {batch_name}: {e}")

    return {"added": added, "skipped": skipped, "overwritten": overwritten, "errors": errors}


def _restore_rule_packages(
    base_dir: str,
    backup_rp: Dict[str, Any],
    conflict_strategy: str,
) -> Dict[str, Any]:
    from .rule_pkg import _get_rule_pkgs_dir, _get_index_path as _rp_index_path

    rp_dir = _get_rule_pkgs_dir(base_dir)
    added = []
    skipped = []
    overwritten = []
    errors = []

    idx_path = _rp_index_path(base_dir)
    current_index: Dict[str, Any] = {}
    if os.path.exists(idx_path):
        try:
            current_index = _read_json(idx_path)
        except Exception:
            current_index = {"version": 1, "packages": []}

    backup_index = backup_rp.get("index", {})
    backup_packages = backup_rp.get("packages", {})

    for fn, pkg_data in backup_packages.items():
        name = pkg_data.get("name", "")
        version = pkg_data.get("version", "")
        key = f"{name}:{version}"

        exists = any(
            isinstance(p, dict) and p.get("name") == name and p.get("version") == version
            for p in current_index.get("packages", [])
        )

        if exists:
            if conflict_strategy == "skip":
                skipped.append(key)
                continue
            elif conflict_strategy == "overwrite":
                try:
                    pkg_path = os.path.join(rp_dir, fn)
                    _atomic_write_json(pkg_path, pkg_data)
                    overwritten.append(key)
                except Exception as e:
                    errors.append(f"规则包 {key}: {e}")
            else:
                skipped.append(key)
        else:
            try:
                pkg_path = os.path.join(rp_dir, fn)
                _atomic_write_json(pkg_path, pkg_data)
                added.append(key)
            except Exception as e:
                errors.append(f"规则包 {key}: {e}")

    if backup_index and (added or overwritten):
        try:
            merged = _merge_rp_index(current_index, backup_index, added, overwritten)
            _atomic_write_json(idx_path, merged)
        except Exception as e:
            errors.append(f"更新规则包索引: {e}")

    return {"added": added, "skipped": skipped, "overwritten": overwritten, "errors": errors}


def _merge_rp_index(
    current_index: Dict[str, Any],
    backup_index: Dict[str, Any],
    added: List[str],
    overwritten: List[str],
) -> Dict[str, Any]:
    current_pkgs = {f"{p.get('name','')}:{p.get('version','')}": p for p in current_index.get("packages", []) if isinstance(p, dict)}
    backup_pkgs = {f"{p.get('name','')}:{p.get('version','')}": p for p in backup_index.get("packages", []) if isinstance(p, dict)}

    for key in added + overwritten:
        if key in backup_pkgs:
            current_pkgs[key] = backup_pkgs[key]

    return {
        "version": current_index.get("version", 1),
        "packages": list(current_pkgs.values()),
    }


def _restore_view_presets(
    base_dir: str,
    backup_vp: Dict[str, Any],
    conflict_strategy: str,
) -> Dict[str, Any]:
    from .view_preset import _get_view_presets_dir, _get_index_path as _vp_index_path

    vp_dir = _get_view_presets_dir(base_dir)
    added = []
    skipped = []
    overwritten = []
    errors = []

    idx_path = _vp_index_path(base_dir)
    current_index: Dict[str, Any] = {}
    if os.path.exists(idx_path):
        try:
            current_index = _read_json(idx_path)
        except Exception:
            current_index = {"version": 1, "presets": []}

    backup_presets = backup_vp.get("presets", {})

    for fn, preset_data in backup_presets.items():
        name = preset_data.get("name", "")
        exists = any(
            isinstance(p, dict) and p.get("name") == name
            for p in current_index.get("presets", [])
        )

        if exists:
            if conflict_strategy == "skip":
                skipped.append(name)
                continue
            elif conflict_strategy == "overwrite":
                try:
                    preset_path = os.path.join(vp_dir, fn)
                    _atomic_write_json(preset_path, preset_data)
                    overwritten.append(name)
                except Exception as e:
                    errors.append(f"预设 {name}: {e}")
            else:
                skipped.append(name)
        else:
            try:
                preset_path = os.path.join(vp_dir, fn)
                _atomic_write_json(preset_path, preset_data)
                added.append(name)
            except Exception as e:
                errors.append(f"预设 {name}: {e}")

    if added or overwritten:
        try:
            merged = _merge_simple_index(current_index, backup_vp.get("index", {}), "presets", added + overwritten)
            _atomic_write_json(idx_path, merged)
        except Exception as e:
            errors.append(f"更新视图预设索引: {e}")

    return {"added": added, "skipped": skipped, "overwritten": overwritten, "errors": errors}


def _restore_snapshots(
    base_dir: str,
    backup_sn: Dict[str, Any],
    conflict_strategy: str,
) -> Dict[str, Any]:
    from .snapshot import _get_snapshots_dir, _get_index_path as _sn_index_path

    sn_dir = _get_snapshots_dir(base_dir)
    added = []
    skipped = []
    overwritten = []
    errors = []

    idx_path = _sn_index_path(base_dir)
    current_index: Dict[str, Any] = {}
    if os.path.exists(idx_path):
        try:
            current_index = _read_json(idx_path)
        except Exception:
            current_index = {"version": 1, "snapshots": []}

    backup_snapshots = backup_sn.get("snapshots", {})

    for fn, snap_data in backup_snapshots.items():
        name = snap_data.get("name", "")
        exists = any(
            isinstance(s, dict) and s.get("name") == name
            for s in current_index.get("snapshots", [])
        )

        if exists:
            if conflict_strategy == "skip":
                skipped.append(name)
                continue
            elif conflict_strategy == "overwrite":
                try:
                    snap_path = os.path.join(sn_dir, fn)
                    _atomic_write_json(snap_path, snap_data)
                    overwritten.append(name)
                except Exception as e:
                    errors.append(f"快照 {name}: {e}")
            else:
                skipped.append(name)
        else:
            try:
                snap_path = os.path.join(sn_dir, fn)
                _atomic_write_json(snap_path, snap_data)
                added.append(name)
            except Exception as e:
                errors.append(f"快照 {name}: {e}")

    if added or overwritten:
        try:
            merged = _merge_simple_index(
                current_index, backup_sn.get("index", {}), "snapshots", added + overwritten
            )
            _atomic_write_json(idx_path, merged)
        except Exception as e:
            errors.append(f"更新快照索引: {e}")

    return {"added": added, "skipped": skipped, "overwritten": overwritten, "errors": errors}


def _restore_compare_configs(
    base_dir: str,
    backup_cc: Dict[str, Any],
    conflict_strategy: str,
) -> Dict[str, Any]:
    from .compare import _get_compare_config_dir, _get_compare_index_path as _cc_index_path

    cc_dir = _get_compare_config_dir(base_dir)
    added = []
    skipped = []
    overwritten = []
    errors = []

    idx_path = _cc_index_path(base_dir)
    current_index: Dict[str, Any] = {}
    if os.path.exists(idx_path):
        try:
            current_index = _read_json(idx_path)
        except Exception:
            current_index = {"version": 1, "configs": []}

    backup_configs = backup_cc.get("configs", {})

    for fn, cfg_data in backup_configs.items():
        name = cfg_data.get("name", "")
        exists = any(
            isinstance(c, dict) and c.get("name") == name
            for c in current_index.get("configs", [])
        )

        if exists:
            if conflict_strategy == "skip":
                skipped.append(name)
                continue
            elif conflict_strategy == "overwrite":
                try:
                    cfg_path = os.path.join(cc_dir, fn)
                    _atomic_write_json(cfg_path, cfg_data)
                    overwritten.append(name)
                except Exception as e:
                    errors.append(f"对比配置 {name}: {e}")
            else:
                skipped.append(name)
        else:
            try:
                cfg_path = os.path.join(cc_dir, fn)
                _atomic_write_json(cfg_path, cfg_data)
                added.append(name)
            except Exception as e:
                errors.append(f"对比配置 {name}: {e}")

    if added or overwritten:
        try:
            merged = _merge_simple_index(
                current_index, backup_cc.get("index", {}), "configs", added + overwritten
            )
            _atomic_write_json(idx_path, merged)
        except Exception as e:
            errors.append(f"更新对比配置索引: {e}")

    return {"added": added, "skipped": skipped, "overwritten": overwritten, "errors": errors}


def _merge_simple_index(
    current_index: Dict[str, Any],
    backup_index: Dict[str, Any],
    items_key: str,
    affected_names: List[str],
) -> Dict[str, Any]:
    current_items = {}
    key_field = "name"
    for item in current_index.get(items_key, []):
        if isinstance(item, dict):
            current_items[item.get(key_field, "")] = item

    for item in backup_index.get(items_key, []):
        if isinstance(item, dict):
            item_name = item.get(key_field, "")
            if item_name in affected_names:
                current_items[item_name] = item

    return {
        "version": current_index.get("version", 1),
        items_key: list(current_items.values()),
    }
