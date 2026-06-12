from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .config import parse_rules_file, ConfigError, CheckRules
from .state import BatchState, StateError


PLANS_DIR_NAME = "plans"
INDEX_FILE_NAME = "index.json"
PLAN_FILE_SUFFIX = ".plan.json"

VALID_CONFLICT_STRATEGIES = {"overwrite", "rename", "refuse"}


class PlanError(Exception):
    pass


class PlanNotFoundError(PlanError):
    pass


class PlanConflictError(PlanError):
    pass


class PlanFormatError(PlanError):
    pass


class PlanPermissionError(PlanError):
    pass


class PlanExecutionError(PlanError):
    pass


@dataclass
class PlanTaskItem:
    rules_path: str
    data_dir: str
    name: str = ""
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "rules_path": self.rules_path,
            "data_dir": self.data_dir,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanTaskItem":
        if not isinstance(data, dict):
            raise PlanFormatError("任务项必须是对象")
        rules_path = data.get("rules_path", "")
        data_dir = data.get("data_dir", "")
        if not rules_path:
            raise PlanFormatError("任务项缺少 rules_path")
        if not data_dir:
            raise PlanFormatError("任务项缺少 data_dir")
        return cls(
            name=str(data.get("name", "") or ""),
            description=str(data.get("description", "") or ""),
            rules_path=str(rules_path),
            data_dir=str(data_dir),
        )

    @classmethod
    def new(
        cls,
        rules_path: str,
        data_dir: str,
        name: str = "",
        description: str = "",
    ) -> "PlanTaskItem":
        if not rules_path.strip():
            raise PlanFormatError("rules_path 不能为空")
        if not data_dir.strip():
            raise PlanFormatError("data_dir 不能为空")
        return cls(
            name=name.strip(),
            description=description.strip(),
            rules_path=rules_path.strip(),
            data_dir=data_dir.strip(),
        )


@dataclass
class PlanTaskResult:
    task_index: int
    task_name: str
    batch_name: str
    status: str
    report_path: str = ""
    error_message: str = ""
    exit_code: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_index": self.task_index,
            "task_name": self.task_name,
            "batch_name": self.batch_name,
            "status": self.status,
            "report_path": self.report_path,
            "error_message": self.error_message,
            "exit_code": self.exit_code,
        }


@dataclass
class Plan:
    name: str
    description: str = ""
    batch_prefix: str = ""
    tasks: List[PlanTaskItem] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    workspace_dir: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "batch_prefix": self.batch_prefix,
            "tasks": [t.to_dict() for t in self.tasks],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "workspace_dir": self.workspace_dir,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], workspace_dir: str = "") -> "Plan":
        required_fields = ["name"]
        missing = [f for f in required_fields if f not in data]
        if missing:
            raise PlanFormatError(
                f"计划缺少必填字段: {', '.join(missing)}"
            )
        if not isinstance(data["name"], str) or not data["name"].strip():
            raise PlanFormatError("计划 name 必须是非空字符串")

        tasks_raw = data.get("tasks", []) or []
        if not isinstance(tasks_raw, list):
            raise PlanFormatError("tasks 必须是数组")

        tasks: List[PlanTaskItem] = []
        for i, task_data in enumerate(tasks_raw):
            try:
                tasks.append(PlanTaskItem.from_dict(task_data))
            except PlanFormatError as e:
                raise PlanFormatError(f"第 {i+1} 个任务项格式错误: {e}")

        return cls(
            name=str(data["name"]).strip(),
            description=str(data.get("description", "") or ""),
            batch_prefix=str(data.get("batch_prefix", "") or ""),
            tasks=tasks,
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            workspace_dir=workspace_dir or str(data.get("workspace_dir", "") or ""),
        )

    @classmethod
    def new(
        cls,
        name: str,
        description: str = "",
        batch_prefix: str = "",
        tasks: Optional[List[PlanTaskItem]] = None,
        workspace_dir: str = "",
    ) -> "Plan":
        name = name.strip()
        if not name:
            raise PlanFormatError("计划名称不能为空")
        if not tasks:
            raise PlanFormatError("计划至少需要一个任务项")

        now = datetime.now().isoformat(timespec="seconds")
        return cls(
            name=name,
            description=description.strip(),
            batch_prefix=batch_prefix.strip(),
            tasks=tasks or [],
            created_at=now,
            updated_at=now,
            workspace_dir=workspace_dir,
        )

    def resolve_paths(self, base_dir: Optional[str] = None) -> List[Tuple[str, str]]:
        """将任务项中的相对路径解析为绝对路径。"""
        workspace = base_dir or self.workspace_dir or os.getcwd()
        resolved: List[Tuple[str, str]] = []
        for task in self.tasks:
            rules_path = os.path.abspath(os.path.join(workspace, task.rules_path))
            data_dir = os.path.abspath(os.path.join(workspace, task.data_dir))
            resolved.append((rules_path, data_dir))
        return resolved

    def validate_paths(self, base_dir: Optional[str] = None) -> List[Tuple[int, str]]:
        """验证所有路径是否存在，返回错误列表 [(task_index, error_message)]。"""
        errors: List[Tuple[int, str]] = []
        workspace = base_dir or self.workspace_dir or os.getcwd()
        for i, task in enumerate(self.tasks):
            rules_path = os.path.join(workspace, task.rules_path)
            data_dir = os.path.join(workspace, task.data_dir)
            if not os.path.exists(rules_path):
                errors.append((i, f"规则文件不存在: {rules_path}"))
            elif not os.path.isfile(rules_path):
                errors.append((i, f"rules_path 不是文件: {rules_path}"))
            if not os.path.exists(data_dir):
                errors.append((i, f"资料目录不存在: {data_dir}"))
            elif not os.path.isdir(data_dir):
                errors.append((i, f"data_dir 不是目录: {data_dir}"))
        return errors


def _safe_filename(name: str) -> str:
    def _safe(s: str) -> str:
        return "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in s)
    return f"{_safe(name)}{PLAN_FILE_SUFFIX}"


def _get_plans_dir(base_dir: str) -> str:
    from .state import STATE_DIR_NAME
    return os.path.join(base_dir, STATE_DIR_NAME, PLANS_DIR_NAME)


def _get_index_path(base_dir: str) -> str:
    return os.path.join(_get_plans_dir(base_dir), INDEX_FILE_NAME)


def _get_plan_path(base_dir: str, name: str) -> str:
    return os.path.join(_get_plans_dir(base_dir), _safe_filename(name))


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    dir_path = os.path.dirname(path)
    try:
        os.makedirs(dir_path, exist_ok=True)
    except (PermissionError, OSError) as e:
        raise PlanPermissionError(f"无法创建目录 {dir_path}: {e}")

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
        raise PlanPermissionError(f"写入文件失败 {tmp_path}: {e}")

    try:
        shutil.move(tmp_path, path)
    except (PermissionError, OSError) as e:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        raise PlanPermissionError(f"替换文件失败 {path}: {e}")


def _read_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise PlanNotFoundError(f"文件不存在: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except (PermissionError, OSError) as e:
        raise PlanPermissionError(f"读取文件失败 {path}: {e}")
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise PlanFormatError(f"JSON 解析失败 {path}: {e}")


def _load_index(base_dir: str) -> Dict[str, Any]:
    index_path = _get_index_path(base_dir)
    if not os.path.exists(index_path):
        return {
            "version": 1,
            "plans": [],
        }
    data = _read_json(index_path)
    if not isinstance(data, dict) or "plans" not in data:
        raise PlanFormatError(f"索引文件格式错误: {index_path}")
    if not isinstance(data["plans"], list):
        raise PlanFormatError(f"索引文件 plans 必须是数组: {index_path}")
    return data


def _save_index(base_dir: str, index: Dict[str, Any]) -> None:
    _atomic_write_json(_get_index_path(base_dir), index)


def _find_in_index(index: Dict[str, Any], name: str) -> Optional[int]:
    for i, plan_info in enumerate(index.get("plans", [])):
        if (
            isinstance(plan_info, dict)
            and plan_info.get("name") == name
        ):
            return i
    return None


def _generate_rename(name: str, base_dir: str, suffix: int = 1) -> str:
    new_name = f"{name}_{suffix}"
    index = _load_index(base_dir)
    if _find_in_index(index, new_name) is None:
        return new_name
    return _generate_rename(name, base_dir, suffix + 1)


def save_plan(
    base_dir: str,
    name: str,
    description: str = "",
    batch_prefix: str = "",
    tasks: Optional[List[PlanTaskItem]] = None,
    force: bool = False,
) -> Plan:
    index = _load_index(base_dir)
    existing_idx = _find_in_index(index, name)

    if existing_idx is not None and not force:
        raise PlanConflictError(
            f"计划「{name}」已存在。"
        )

    plan = Plan.new(
        name=name,
        description=description,
        batch_prefix=batch_prefix,
        tasks=tasks,
        workspace_dir=base_dir,
    )

    if existing_idx is not None:
        old_info = index["plans"][existing_idx]
        plan.created_at = str(old_info.get("created_at", plan.created_at))

    plan_path = _get_plan_path(base_dir, name)
    _atomic_write_json(plan_path, plan.to_dict())

    plan_info = {
        "name": plan.name,
        "description": plan.description,
        "batch_prefix": plan.batch_prefix,
        "task_count": len(plan.tasks),
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
        "file": os.path.basename(plan_path),
        "workspace_dir": plan.workspace_dir,
    }

    try:
        if existing_idx is not None:
            index["plans"][existing_idx] = plan_info
        else:
            index["plans"].append(plan_info)
        _save_index(base_dir, index)
    except Exception as e:
        try:
            if os.path.exists(plan_path):
                os.remove(plan_path)
        except Exception:
            pass
        raise PlanPermissionError(f"更新索引失败，已回滚计划文件: {e}")

    return plan


def list_plans(base_dir: str) -> List[Dict[str, Any]]:
    index = _load_index(base_dir)
    plans = []
    for plan_info in index.get("plans", []):
        if isinstance(plan_info, dict):
            plans.append(dict(plan_info))
    plans.sort(key=lambda p: (p.get("name", "")))
    return plans


def get_plan(base_dir: str, name: str) -> Plan:
    index = _load_index(base_dir)
    idx = _find_in_index(index, name)
    if idx is None:
        raise PlanNotFoundError(
            f"计划不存在: {name}"
        )
    plan_path = _get_plan_path(base_dir, name)
    data = _read_json(plan_path)
    return Plan.from_dict(data, workspace_dir=base_dir)


def delete_plan(base_dir: str, name: str) -> None:
    index = _load_index(base_dir)
    idx = _find_in_index(index, name)
    if idx is None:
        raise PlanNotFoundError(
            f"计划不存在: {name}"
        )

    plan_path = _get_plan_path(base_dir, name)
    old_index_data = json.dumps(index, ensure_ascii=False, indent=2)

    try:
        del index["plans"][idx]
        _save_index(base_dir, index)
    except Exception as e:
        try:
            _atomic_write_json(_get_index_path(base_dir), json.loads(old_index_data))
        except Exception:
            pass
        raise PlanPermissionError(f"更新索引失败: {e}")

    if os.path.exists(plan_path):
        try:
            os.remove(plan_path)
        except Exception as e:
            raise PlanPermissionError(
                f"索引已更新，但删除计划文件失败 {plan_path}: {e}"
            )


def export_plan(
    base_dir: str,
    name: str,
    output_path: str,
) -> str:
    plan = get_plan(base_dir, name)
    export_data = {
        "format_version": 1,
        "type": "delivery-checker-plan",
        "plan": plan.to_dict(),
    }
    abs_output = os.path.abspath(output_path)
    _atomic_write_json(abs_output, export_data)
    return abs_output


def import_plan(
    base_dir: str,
    input_path: str,
    conflict_strategy: str = "refuse",
    rename_name: Optional[str] = None,
) -> Plan:
    if conflict_strategy not in VALID_CONFLICT_STRATEGIES:
        raise PlanFormatError(
            f"无效的冲突策略: {conflict_strategy}（有效值: {', '.join(sorted(VALID_CONFLICT_STRATEGIES))}）"
        )

    abs_input = os.path.abspath(input_path)
    data = _read_json(abs_input)

    if not isinstance(data, dict):
        raise PlanFormatError("导入文件格式错误：根节点必须是对象")
    if data.get("type") != "delivery-checker-plan":
        raise PlanFormatError(
            "导入文件格式错误：不是有效的 delivery-checker 计划导出文件"
        )
    if "plan" not in data or not isinstance(data["plan"], dict):
        raise PlanFormatError("导入文件格式错误：缺少 plan 字段")

    plan_data = data["plan"]
    plan = Plan.from_dict(plan_data, workspace_dir=base_dir)

    new_name = rename_name.strip() if rename_name else plan.name
    if not new_name:
        raise PlanFormatError("计划名称不能为空")

    index = _load_index(base_dir)
    existing_idx = _find_in_index(index, new_name)

    if existing_idx is not None:
        if conflict_strategy == "refuse":
            raise PlanConflictError(
                f"计划「{new_name}」已存在。"
            )
        elif conflict_strategy == "rename":
            new_name = _generate_rename(new_name, base_dir)
        elif conflict_strategy == "overwrite":
            pass

    plan.name = new_name
    plan.workspace_dir = base_dir
    plan.updated_at = datetime.now().isoformat(timespec="seconds")

    plan_path = _get_plan_path(base_dir, new_name)
    _atomic_write_json(plan_path, plan.to_dict())

    plan_info = {
        "name": plan.name,
        "description": plan.description,
        "batch_prefix": plan.batch_prefix,
        "task_count": len(plan.tasks),
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
        "file": os.path.basename(plan_path),
        "workspace_dir": plan.workspace_dir,
    }

    try:
        existing_idx = _find_in_index(index, new_name)
        if existing_idx is not None:
            old_info = index["plans"][existing_idx]
            plan.created_at = str(old_info.get("created_at", plan.created_at))
            plan_info["created_at"] = plan.created_at
            index["plans"][existing_idx] = plan_info
        else:
            index["plans"].append(plan_info)
        _save_index(base_dir, index)
    except Exception as e:
        try:
            if os.path.exists(plan_path):
                os.remove(plan_path)
        except Exception:
            pass
        raise PlanPermissionError(f"更新索引失败，已回滚计划文件: {e}")

    return plan


def run_plan(
    base_dir: str,
    name: str,
    output_dir: Optional[str] = None,
    no_merge: bool = False,
    force_rescan: bool = False,
    export_format: str = "csv",
) -> Dict[str, Any]:
    plan = get_plan(base_dir, name)
    workspace = plan.workspace_dir or base_dir

    resolved_paths = plan.resolve_paths(workspace)
    path_errors = plan.validate_paths(workspace)
    if path_errors:
        error_msgs = "; ".join(f"任务 {idx+1}: {msg}" for idx, msg in path_errors)
        raise PlanFormatError(f"计划路径验证失败: {error_msgs}")

    if output_dir:
        output_dir = os.path.abspath(output_dir)
        try:
            os.makedirs(output_dir, exist_ok=True)
        except (PermissionError, OSError) as e:
            raise PlanPermissionError(f"无法创建输出目录 {output_dir}: {e}")
    else:
        output_dir = workspace

    try:
        test_file = os.path.join(output_dir, f".plan_write_test_{os.getpid()}")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
    except (PermissionError, OSError) as e:
        raise PlanPermissionError(f"输出目录无写权限 {output_dir}: {e}")

    results: List[PlanTaskResult] = []
    success_count = 0
    failed_count = 0
    skipped_count = 0

    for i, (task, (rules_path, data_dir)) in enumerate(zip(plan.tasks, resolved_paths)):
        task_name = task.name or f"任务{i+1}"
        batch_prefix = plan.batch_prefix or ""
        base_batch_name = ""

        try:
            rules = parse_rules_file(rules_path)
            base_batch_name = rules.batch_name
            if batch_prefix:
                rules.batch_name = f"{batch_prefix}{rules.batch_name}"
        except ConfigError as e:
            results.append(PlanTaskResult(
                task_index=i,
                task_name=task_name,
                batch_name="",
                status="failed",
                error_message=f"规则文件解析失败: {e}",
                exit_code=2,
            ))
            failed_count += 1
            continue

        if not force_rescan and not no_merge:
            try:
                BatchState.load(workspace, rules.batch_name)
                results.append(PlanTaskResult(
                    task_index=i,
                    task_name=task_name,
                    batch_name=rules.batch_name,
                    status="skipped",
                    error_message=f"批次已存在: {rules.batch_name}",
                    exit_code=3,
                ))
                skipped_count += 1
                continue
            except StateError:
                pass

        report_ext = ".csv" if export_format == "csv" else ".html"
        report_name = f"{rules.batch_name}_report{report_ext}"
        report_path = os.path.join(output_dir, report_name)

        final_batch_name = rules.batch_name
        scan_args = [
            sys.executable, "-m", "delivery_checker",
            "scan", rules_path, data_dir,
            "--batch-name", final_batch_name,
        ]
        if no_merge:
            scan_args.append("--no-merge")
        if force_rescan:
            scan_args.append("--force")

        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.join(os.path.dirname(__file__), "..")
        env["PYTHONIOENCODING"] = "utf-8"

        try:
            proc_result = subprocess.run(
                scan_args,
                env=env,
                capture_output=True,
                text=True,
                cwd=workspace,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as e:
            results.append(PlanTaskResult(
                task_index=i,
                task_name=task_name,
                batch_name=rules.batch_name,
                status="failed",
                error_message=f"执行扫描失败: {e}",
                exit_code=1,
            ))
            failed_count += 1
            continue

        if proc_result.returncode == 0:
            export_args = [
                sys.executable, "-m", "delivery_checker",
                "export", rules.batch_name, report_path,
                "--format", export_format,
            ]
            try:
                export_result = subprocess.run(
                    export_args,
                    env=env,
                    capture_output=True,
                    text=True,
                    cwd=workspace,
                    encoding="utf-8",
                    errors="replace",
                )
                if export_result.returncode == 0 and os.path.exists(report_path):
                    results.append(PlanTaskResult(
                        task_index=i,
                        task_name=task_name,
                        batch_name=rules.batch_name,
                        status="success",
                        report_path=report_path,
                        exit_code=0,
                    ))
                    success_count += 1
                else:
                    results.append(PlanTaskResult(
                        task_index=i,
                        task_name=task_name,
                        batch_name=rules.batch_name,
                        status="failed",
                        error_message=f"导出报告失败: {export_result.stderr.strip()}",
                        exit_code=export_result.returncode,
                    ))
                    failed_count += 1
            except Exception as e:
                results.append(PlanTaskResult(
                    task_index=i,
                    task_name=task_name,
                    batch_name=rules.batch_name,
                    status="failed",
                    error_message=f"导出报告异常: {e}",
                    exit_code=1,
                ))
                failed_count += 1
        elif proc_result.returncode == 3 and not no_merge:
            results.append(PlanTaskResult(
                task_index=i,
                task_name=task_name,
                batch_name=rules.batch_name,
                status="skipped",
                error_message=f"批次已存在: {proc_result.stderr.strip()}",
                exit_code=3,
            ))
            skipped_count += 1
        else:
            results.append(PlanTaskResult(
                task_index=i,
                task_name=task_name,
                batch_name=rules.batch_name,
                status="failed",
                error_message=f"扫描失败: {proc_result.stderr.strip()}",
                exit_code=proc_result.returncode,
            ))
            failed_count += 1

    summary = {
        "plan_name": plan.name,
        "total": len(plan.tasks),
        "success": success_count,
        "failed": failed_count,
        "skipped": skipped_count,
        "results": [r.to_dict() for r in results],
    }

    return summary
