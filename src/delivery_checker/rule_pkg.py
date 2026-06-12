from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from .config import CheckRules


RULE_PKGS_DIR_NAME = "rule_packages"
INDEX_FILE_NAME = "index.json"
RULE_PKG_FILE_SUFFIX = ".json"


class RulePackageError(Exception):
    """规则包操作基础错误"""
    pass


class RulePkgNotFoundError(RulePackageError):
    """规则包不存在"""
    pass


class RulePkgConflictError(RulePackageError):
    """规则包名称+版本冲突"""
    pass


class RulePkgFormatError(RulePackageError):
    """规则包格式错误（坏JSON、缺字段等）"""
    pass


class RulePkgPermissionError(RulePackageError):
    """文件/目录权限不足"""
    pass


@dataclass
class RulePackage:
    """规则包数据模型"""
    name: str
    version: str
    description: str
    rules: Dict[str, Any]
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "rules": self.rules,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RulePackage":
        required_fields = ["name", "version", "description", "rules"]
        missing = [f for f in required_fields if f not in data]
        if missing:
            raise RulePkgFormatError(
                f"规则包缺少必填字段: {', '.join(missing)}"
            )
        if not isinstance(data["name"], str) or not data["name"].strip():
            raise RulePkgFormatError("规则包 name 必须是非空字符串")
        if not isinstance(data["version"], str) or not data["version"].strip():
            raise RulePkgFormatError("规则包 version 必须是非空字符串")
        if not isinstance(data["description"], str):
            raise RulePkgFormatError("规则包 description 必须是字符串")
        if not isinstance(data["rules"], dict):
            raise RulePkgFormatError("规则包 rules 必须是对象")
        return cls(
            name=str(data["name"]).strip(),
            version=str(data["version"]).strip(),
            description=str(data["description"]),
            rules=dict(data["rules"]),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )

    @classmethod
    def from_rules(
        cls,
        name: str,
        version: str,
        description: str,
        rules: CheckRules,
    ) -> "RulePackage":
        now = datetime.now().isoformat(timespec="seconds")
        return cls(
            name=name.strip(),
            version=version.strip(),
            description=description,
            rules=rules.to_dict(),
            created_at=now,
            updated_at=now,
        )

    def to_rules(self) -> CheckRules:
        """将规则包中的 rules 转换回 CheckRules 对象"""
        from .config import RequiredFile, NamingRule

        rules_dict = self.rules
        required_files = []
        for rf in rules_dict.get("required_files", []):
            required_files.append(RequiredFile(
                pattern=rf["pattern"],
                description=rf.get("description", ""),
                optional=bool(rf.get("optional", False)),
                naming_rule=rf.get("naming_rule"),
                expiry_date=rf.get("expiry_date"),
                max_matches=rf.get("max_matches"),
            ))
        naming_rules = []
        for nr in rules_dict.get("naming_rules", []):
            naming_rules.append(NamingRule(
                pattern=nr["pattern"],
                regex=nr["regex"],
                name=nr.get("name", ""),
                description=nr.get("description", ""),
            ))
        return CheckRules(
            batch_name=rules_dict.get("batch_name", self.name),
            root_alias=rules_dict.get("root_alias", self.name),
            required_files=required_files,
            naming_rules=naming_rules,
            ignore_patterns=list(rules_dict.get("ignore_patterns", [])),
            expiry_date=rules_dict.get("expiry_date"),
            metadata=dict(rules_dict.get("metadata", {})),
            source_path=rules_dict.get("source_path", ""),
            source_hash=rules_dict.get("source_hash", ""),
        )


def _safe_filename(name: str, version: str) -> str:
    """生成安全的文件名，避免路径遍历和非法字符"""
    def _safe(s: str) -> str:
        return "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in s)
    return f"{_safe(name)}_{_safe(version)}{RULE_PKG_FILE_SUFFIX}"


def _get_rule_pkgs_dir(base_dir: str) -> str:
    from .state import STATE_DIR_NAME
    return os.path.join(base_dir, STATE_DIR_NAME, RULE_PKGS_DIR_NAME)


def _get_index_path(base_dir: str) -> str:
    return os.path.join(_get_rule_pkgs_dir(base_dir), INDEX_FILE_NAME)


def _get_pkg_path(base_dir: str, name: str, version: str) -> str:
    return os.path.join(_get_rule_pkgs_dir(base_dir), _safe_filename(name, version))


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    """原子写入 JSON 文件：先写 tmp，再 move 替换"""
    dir_path = os.path.dirname(path)
    try:
        os.makedirs(dir_path, exist_ok=True)
    except (PermissionError, OSError) as e:
        raise RulePkgPermissionError(f"无法创建目录 {dir_path}: {e}")

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
        raise RulePkgPermissionError(f"写入文件失败 {tmp_path}: {e}")

    try:
        shutil.move(tmp_path, path)
    except (PermissionError, OSError) as e:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        raise RulePkgPermissionError(f"替换文件失败 {path}: {e}")


def _read_json(path: str) -> Dict[str, Any]:
    """安全读取 JSON 文件"""
    if not os.path.exists(path):
        raise RulePkgNotFoundError(f"文件不存在: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except (PermissionError, OSError) as e:
        raise RulePkgPermissionError(f"读取文件失败 {path}: {e}")
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise RulePkgFormatError(f"JSON 解析失败 {path}: {e}")


def _load_index(base_dir: str) -> Dict[str, Any]:
    """加载索引文件，不存在则返回空索引"""
    index_path = _get_index_path(base_dir)
    if not os.path.exists(index_path):
        return {
            "version": 1,
            "packages": [],
        }
    data = _read_json(index_path)
    if not isinstance(data, dict) or "packages" not in data:
        raise RulePkgFormatError(f"索引文件格式错误: {index_path}")
    if not isinstance(data["packages"], list):
        raise RulePkgFormatError(f"索引文件 packages 必须是数组: {index_path}")
    return data


def _save_index(base_dir: str, index: Dict[str, Any]) -> None:
    """原子保存索引文件"""
    _atomic_write_json(_get_index_path(base_dir), index)


def _find_in_index(
    index: Dict[str, Any],
    name: str,
    version: str,
) -> Optional[int]:
    """在索引中查找指定 name+version 的包，返回索引位置，找不到返回 None"""
    for i, pkg_info in enumerate(index.get("packages", [])):
        if (
            isinstance(pkg_info, dict)
            and pkg_info.get("name") == name
            and pkg_info.get("version") == version
        ):
            return i
    return None


def save_rule_package(
    base_dir: str,
    name: str,
    version: str,
    description: str,
    rules: CheckRules,
    force: bool = False,
) -> RulePackage:
    """保存规则包

    Args:
        base_dir: 工作目录（.delivery_check 所在目录）
        name: 规则包名称
        version: 规则包版本
        description: 规则包说明
        rules: CheckRules 对象
        force: 是否强制覆盖已存在的同名同版本包

    Returns:
        保存后的 RulePackage 对象

    Raises:
        RulePkgConflictError: 同名同版本已存在且 force=False
        RulePkgFormatError: 名称/版本格式错误
        RulePkgPermissionError: 写入权限不足
    """
    name = name.strip()
    version = version.strip()
    if not name:
        raise RulePkgFormatError("规则包名称不能为空")
    if not version:
        raise RulePkgFormatError("规则包版本不能为空")

    index = _load_index(base_dir)
    existing_idx = _find_in_index(index, name, version)

    if existing_idx is not None and not force:
        raise RulePkgConflictError(
            f"规则包「{name}」版本「{version}」已存在。"
        )

    pkg = RulePackage.from_rules(name, version, description, rules)
    pkg_path = _get_pkg_path(base_dir, name, version)

    # 先保存规则包文件
    _atomic_write_json(pkg_path, pkg.to_dict())

    # 更新索引
    pkg_info = {
        "name": name,
        "version": version,
        "description": description,
        "created_at": pkg.created_at,
        "updated_at": pkg.updated_at,
        "file": os.path.basename(pkg_path),
        "rule_count": len(rules.required_files),
    }

    try:
        if existing_idx is not None:
            # 保留原 created_at
            old_info = index["packages"][existing_idx]
            pkg_info["created_at"] = old_info.get("created_at", pkg.created_at)
            pkg.created_at = pkg_info["created_at"]
            index["packages"][existing_idx] = pkg_info
        else:
            index["packages"].append(pkg_info)
        _save_index(base_dir, index)
    except Exception as e:
        # 索引更新失败，尝试回滚已保存的规则包文件
        try:
            if os.path.exists(pkg_path):
                os.remove(pkg_path)
        except Exception:
            pass
        raise RulePkgPermissionError(f"更新索引失败，已回滚规则包文件: {e}")

    return pkg


def list_rule_packages(base_dir: str) -> List[Dict[str, Any]]:
    """列出所有规则包

    Returns:
        规则包信息列表，按 name+version 排序
    """
    index = _load_index(base_dir)
    packages = []
    for pkg_info in index.get("packages", []):
        if isinstance(pkg_info, dict):
            packages.append(dict(pkg_info))
    packages.sort(key=lambda p: (p.get("name", ""), p.get("version", "")))
    return packages


def get_rule_package(base_dir: str, name: str, version: str) -> RulePackage:
    """获取指定的规则包

    Raises:
        RulePkgNotFoundError: 规则包不存在
        RulePkgFormatError: 规则包文件格式错误
        RulePkgPermissionError: 读取权限不足
    """
    index = _load_index(base_dir)
    idx = _find_in_index(index, name, version)
    if idx is None:
        raise RulePkgNotFoundError(
            f"规则包不存在: name={name}, version={version}"
        )

    pkg_path = _get_pkg_path(base_dir, name, version)
    data = _read_json(pkg_path)
    return RulePackage.from_dict(data)


def delete_rule_package(base_dir: str, name: str, version: str) -> None:
    """删除规则包

    Raises:
        RulePkgNotFoundError: 规则包不存在
        RulePkgPermissionError: 删除权限不足
    """
    index = _load_index(base_dir)
    idx = _find_in_index(index, name, version)
    if idx is None:
        raise RulePkgNotFoundError(
            f"规则包不存在: name={name}, version={version}"
        )

    pkg_path = _get_pkg_path(base_dir, name, version)
    old_index_data = json.dumps(index, ensure_ascii=False, indent=2)

    try:
        del index["packages"][idx]
        _save_index(base_dir, index)
    except Exception as e:
        # 索引更新失败，回滚索引
        try:
            _atomic_write_json(_get_index_path(base_dir), json.loads(old_index_data))
        except Exception:
            pass
        raise RulePkgPermissionError(f"更新索引失败: {e}")

    # 索引更新成功后再删除规则包文件
    if os.path.exists(pkg_path):
        try:
            os.remove(pkg_path)
        except Exception as e:
            # 文件删除失败不影响索引（索引已删除），但给出提示
            raise RulePkgPermissionError(
                f"索引已更新，但删除规则包文件失败 {pkg_path}: {e}"
            )


def export_rule_package(
    base_dir: str,
    name: str,
    version: str,
    output_path: str,
) -> str:
    """导出规则包为单个 JSON 文件

    Args:
        base_dir: 工作目录
        name: 规则包名称
        version: 规则包版本
        output_path: 导出文件路径

    Returns:
        实际导出的文件路径

    Raises:
        RulePkgNotFoundError: 规则包不存在
        RulePkgPermissionError: 写入权限不足
    """
    pkg = get_rule_package(base_dir, name, version)
    export_data = {
        "format_version": 1,
        "type": "delivery-checker-rule-package",
        "package": pkg.to_dict(),
    }
    abs_output = os.path.abspath(output_path)
    _atomic_write_json(abs_output, export_data)
    return abs_output


def import_rule_package(
    base_dir: str,
    input_path: str,
    force: bool = False,
    rename_name: Optional[str] = None,
    rename_version: Optional[str] = None,
) -> RulePackage:
    """从导出的 JSON 文件导入规则包

    Args:
        base_dir: 工作目录
        input_path: 导入文件路径
        force: 是否强制覆盖已存在的同名同版本包
        rename_name: 重命名导入的包名（None 则使用原名）
        rename_version: 重命名导入的版本（None 则使用原版本）

    Returns:
        导入后的 RulePackage 对象

    Raises:
        RulePkgFormatError: 导入文件格式错误
        RulePkgConflictError: 同名同版本已存在且 force=False
        RulePkgPermissionError: 读取/写入权限不足
    """
    abs_input = os.path.abspath(input_path)
    data = _read_json(abs_input)

    # 验证导出文件格式
    if not isinstance(data, dict):
        raise RulePkgFormatError("导入文件格式错误：根节点必须是对象")
    if data.get("type") != "delivery-checker-rule-package":
        raise RulePkgFormatError(
            "导入文件格式错误：不是有效的 delivery-checker 规则包导出文件"
        )
    if "package" not in data or not isinstance(data["package"], dict):
        raise RulePkgFormatError("导入文件格式错误：缺少 package 字段")

    pkg_data = data["package"]
    pkg = RulePackage.from_dict(pkg_data)

    # 应用重命名
    new_name = rename_name.strip() if rename_name else pkg.name
    new_version = rename_version.strip() if rename_version else pkg.version
    new_description = pkg.description

    if not new_name:
        raise RulePkgFormatError("规则包名称不能为空")
    if not new_version:
        raise RulePkgFormatError("规则包版本不能为空")

    # 转换为 CheckRules 再保存（确保格式正确）
    rules = pkg.to_rules()
    return save_rule_package(
        base_dir=base_dir,
        name=new_name,
        version=new_version,
        description=new_description,
        rules=rules,
        force=force,
    )
