from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
import hashlib


class ConfigError(Exception):
    """配置文件错误"""
    pass


@dataclass
class RequiredFile:
    pattern: str
    description: str = ""
    optional: bool = False
    naming_rule: Optional[str] = None
    expiry_date: Optional[str] = None
    max_matches: Optional[int] = None


@dataclass
class NamingRule:
    pattern: str
    regex: str
    name: str = ""
    description: str = ""


@dataclass
class CheckRules:
    batch_name: str
    root_alias: str
    required_files: List[RequiredFile] = field(default_factory=list)
    naming_rules: List[NamingRule] = field(default_factory=list)
    ignore_patterns: List[str] = field(default_factory=list)
    expiry_date: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    source_path: str = ""
    source_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "batch_name": self.batch_name,
            "root_alias": self.root_alias,
            "required_files": [
                {
                    "pattern": rf.pattern,
                    "description": rf.description,
                    "optional": rf.optional,
                    "naming_rule": rf.naming_rule,
                    "expiry_date": rf.expiry_date,
                    "max_matches": rf.max_matches,
                }
                for rf in self.required_files
            ],
            "naming_rules": [
                {
                    "pattern": nr.pattern,
                    "regex": nr.regex,
                    "name": nr.name,
                    "description": nr.description,
                }
                for nr in self.naming_rules
            ],
            "ignore_patterns": list(self.ignore_patterns),
            "expiry_date": self.expiry_date,
            "metadata": dict(self.metadata),
            "source_path": self.source_path,
            "source_hash": self.source_hash,
        }


def _parse_required_file(data: Any) -> RequiredFile:
    if isinstance(data, str):
        return RequiredFile(pattern=data)
    if not isinstance(data, dict):
        raise ConfigError(f"required_files 条目格式错误: {data}")
    if "pattern" not in data:
        raise ConfigError(f"required_files 条目缺少 pattern 字段: {data}")
    return RequiredFile(
        pattern=data["pattern"],
        description=data.get("description", ""),
        optional=bool(data.get("optional", False)),
        naming_rule=data.get("naming_rule"),
        expiry_date=data.get("expiry_date"),
        max_matches=data.get("max_matches"),
    )


def _parse_naming_rule(data: Any) -> NamingRule:
    if not isinstance(data, dict):
        raise ConfigError(f"naming_rules 条目格式错误: {data}")
    if "pattern" not in data or "regex" not in data:
        raise ConfigError(f"naming_rules 条目必须包含 pattern 和 regex: {data}")
    name = str(data.get("name", "") or data.get("pattern", ""))
    return NamingRule(
        pattern=data["pattern"],
        regex=data["regex"],
        name=name,
        description=data.get("description", ""),
    )


def parse_rules_file(path: str) -> CheckRules:
    if not os.path.exists(path):
        raise ConfigError(f"规则文件不存在: {path}")

    abs_path = os.path.abspath(path)
    ext = os.path.splitext(path)[1].lower()

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        raise ConfigError(f"读取规则文件失败: {e}")

    source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    try:
        if ext in (".yaml", ".yml"):
            try:
                import yaml
            except ImportError:
                raise ConfigError("解析 YAML 规则需要 PyYAML，请先安装: pip install PyYAML")
            raw = yaml.safe_load(content)
        elif ext == ".json":
            raw = json.loads(content)
        else:
            raise ConfigError(f"不支持的规则文件格式: {ext}（仅支持 .yaml/.yml/.json）")
    except ConfigError:
        raise
    except Exception as e:
        raise ConfigError(f"解析{ext.upper().strip('.')}格式失败: {e}")

    if not isinstance(raw, dict):
        raise ConfigError("规则文件根节点必须是对象（mapping/dict）")

    if "batch_name" not in raw:
        raise ConfigError("规则缺少必填字段 batch_name")
    if "root_alias" not in raw:
        raise ConfigError("规则缺少必填字段 root_alias")

    required_files: List[RequiredFile] = []
    if "required_files" in raw:
        if not isinstance(raw["required_files"], list):
            raise ConfigError("required_files 必须是数组")
        for item in raw["required_files"]:
            required_files.append(_parse_required_file(item))

    naming_rules: List[NamingRule] = []
    if "naming_rules" in raw:
        if not isinstance(raw["naming_rules"], list):
            raise ConfigError("naming_rules 必须是数组")
        for item in raw["naming_rules"]:
            naming_rules.append(_parse_naming_rule(item))

    ignore_patterns: List[str] = []
    if "ignore_patterns" in raw:
        if not isinstance(raw["ignore_patterns"], list):
            raise ConfigError("ignore_patterns 必须是数组")
        ignore_patterns = [str(p) for p in raw["ignore_patterns"]]

    expiry_date = raw.get("expiry_date")
    if expiry_date is not None:
        expiry_str = str(expiry_date)
        try:
            datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
        except ValueError:
            raise ConfigError(f"expiry_date 格式不正确，需为 ISO 格式日期: {expiry_str}")
        expiry_date = expiry_str

    metadata = raw.get("metadata", {}) or {}
    if not isinstance(metadata, dict):
        raise ConfigError("metadata 必须是对象")

    return CheckRules(
        batch_name=str(raw["batch_name"]),
        root_alias=str(raw["root_alias"]),
        required_files=required_files,
        naming_rules=naming_rules,
        ignore_patterns=ignore_patterns,
        expiry_date=expiry_date,
        metadata=metadata,
        source_path=abs_path,
        source_hash=source_hash,
    )
