from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .config import CheckRules
from .models import Issue, ReviewStatus, UndoRecord


STATE_DIR_NAME = ".delivery_check"
STATE_FILE_SUFFIX = ".state.json"


class StateError(Exception):
    """状态操作错误"""
    pass


class EmptyUndoHistoryError(StateError):
    """撤销历史为空"""
    pass


class DuplicateScanError(StateError):
    """重复扫描已存在的批次"""
    pass


class RulesMismatchError(StateError):
    """规则文件与历史批次不一致"""
    pass


def _get_state_dir(base_dir: str) -> str:
    return os.path.join(base_dir, STATE_DIR_NAME)


def _state_file_path(base_dir: str, batch_name: str) -> str:
    safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in batch_name)
    return os.path.join(_get_state_dir(base_dir), f"{safe_name}{STATE_FILE_SUFFIX}")


def list_batches(base_dir: str) -> List[Dict[str, Any]]:
    state_dir = _get_state_dir(base_dir)
    if not os.path.isdir(state_dir):
        return []
    batches: List[Dict[str, Any]] = []
    for fn in sorted(os.listdir(state_dir)):
        if not fn.endswith(STATE_FILE_SUFFIX):
            continue
        try:
            with open(os.path.join(state_dir, fn), "r", encoding="utf-8") as f:
                data = json.load(f)
            issues_raw = data.get("issues", {})
            if isinstance(issues_raw, dict):
                issue_list = list(issues_raw.values())
            elif isinstance(issues_raw, list):
                issue_list = issues_raw
            else:
                issue_list = []
            issue_count = len(issue_list)
            pending_count = sum(
                1 for i in issue_list
                if isinstance(i, dict) and i.get("status") == ReviewStatus.PENDING.value
            )
            batches.append({
                "batch_name": data.get("batch_name", fn[:-len(STATE_FILE_SUFFIX)]),
                "data_dir": data.get("data_dir", ""),
                "rules_path": (data.get("rules", {}) or {}).get("source_path", ""),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "issue_count": issue_count,
                "pending_count": pending_count,
                "state_file": os.path.join(state_dir, fn),
            })
        except Exception as e:
            continue
    batches.sort(key=lambda b: b.get("updated_at", ""), reverse=True)
    return batches


@dataclass
class BatchState:
    batch_name: str
    data_dir: str
    rules: Dict[str, Any]
    rules_hash: str
    issues: Dict[str, Issue] = field(default_factory=dict)
    undo_stack: List[UndoRecord] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def new(cls, rules: CheckRules, data_dir: str) -> "BatchState":
        now = datetime.now().isoformat(timespec="seconds")
        return cls(
            batch_name=rules.batch_name,
            data_dir=os.path.abspath(data_dir),
            rules=rules.to_dict(),
            rules_hash=rules.source_hash,
            created_at=now,
            updated_at=now,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "batch_name": self.batch_name,
            "data_dir": self.data_dir,
            "rules": self.rules,
            "rules_hash": self.rules_hash,
            "issues": {k: v.to_dict() for k, v in self.issues.items()},
            "undo_stack": [u.to_dict() for u in self.undo_stack],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BatchState":
        issues = {
            k: Issue.from_dict(v)
            for k, v in data.get("issues", {}).items()
        }
        undo_stack = [
            UndoRecord.from_dict(u)
            for u in data.get("undo_stack", [])
        ]
        return cls(
            batch_name=data["batch_name"],
            data_dir=data["data_dir"],
            rules=data.get("rules", {}),
            rules_hash=data.get("rules_hash", ""),
            issues=issues,
            undo_stack=undo_stack,
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )

    def save(self, base_dir: str) -> None:
        self.updated_at = datetime.now().isoformat(timespec="seconds")
        state_dir = _get_state_dir(base_dir)
        os.makedirs(state_dir, exist_ok=True)
        path = _state_file_path(base_dir, self.batch_name)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        shutil.move(tmp_path, path)

    @classmethod
    def load(cls, base_dir: str, batch_name: str) -> "BatchState":
        path = _state_file_path(base_dir, batch_name)
        if not os.path.exists(path):
            raise StateError(f"未找到批次: {batch_name}")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            raise StateError(f"读取状态文件失败: {e}")
        return cls.from_dict(data)

    @classmethod
    def exists(cls, base_dir: str, batch_name: str) -> bool:
        return os.path.exists(_state_file_path(base_dir, batch_name))

    def set_issues(self, issues: List[Issue], allow_merge: bool = False) -> None:
        """设置问题列表。allow_merge=True 时保留已有复核状态。"""
        new_map: Dict[str, Issue] = {}
        for issue in issues:
            if allow_merge and issue.id in self.issues:
                existing = self.issues[issue.id]
                issue.status = existing.status
                issue.reviewer = existing.reviewer
                issue.note = existing.note
                issue.reviewed_at = existing.reviewed_at
            new_map[issue.id] = issue
        self.issues = new_map

    def mark_issue(
        self,
        issue_id: str,
        status: ReviewStatus,
        reviewer: str,
        note: str = "",
    ) -> Issue:
        if issue_id not in self.issues:
            raise StateError(f"问题ID不存在: {issue_id}")
        issue = self.issues[issue_id]
        self.undo_stack.append(UndoRecord(
            issue_id=issue_id,
            prev_status=issue.status,
            prev_reviewer=issue.reviewer,
            prev_note=issue.note,
            prev_reviewed_at=issue.reviewed_at,
            timestamp=datetime.now().isoformat(timespec="seconds"),
        ))
        issue.status = status
        issue.reviewer = reviewer
        issue.note = note
        issue.reviewed_at = datetime.now().isoformat(timespec="seconds")
        return issue

    def undo_last(self) -> Issue:
        if not self.undo_stack:
            raise EmptyUndoHistoryError("撤销历史为空，没有可撤销的复核操作")
        record = self.undo_stack.pop()
        if record.issue_id not in self.issues:
            raise StateError(f"撤销失败：历史记录中的问题已不存在: {record.issue_id}")
        issue = self.issues[record.issue_id]
        issue.status = record.prev_status
        issue.reviewer = record.prev_reviewer
        issue.note = record.prev_note
        issue.reviewed_at = record.prev_reviewed_at
        return issue

    def get_sorted_issues(self) -> List[Issue]:
        order = [
            ("missing", 0),
            ("naming", 1),
            ("expired", 2),
            ("duplicate", 3),
            ("untracked", 4),
        ]
        order_map = {k: v for k, v in order}
        return sorted(
            self.issues.values(),
            key=lambda i: (order_map.get(i.type.value, 99), i.path, i.id),
        )


def create_or_resume_batch(
    base_dir: str,
    rules: CheckRules,
    data_dir: str,
    force_rescan: bool = False,
    merge: bool = True,
) -> tuple[BatchState, str]:
    """创建新批次或续办已有批次。
    返回 (状态, 操作描述)
    """
    batch_name = rules.batch_name

    if BatchState.exists(base_dir, batch_name):
        state = BatchState.load(base_dir, batch_name)

        if state.rules_hash != rules.source_hash:
            raise RulesMismatchError(
                f"当前规则文件与历史批次「{batch_name}」使用的规则不一致。\n"
                f"如需重新扫描请使用 --force 参数，或修改 batch_name 避免冲突。"
            )

        if os.path.abspath(data_dir) != state.data_dir:
            raise RulesMismatchError(
                f"资料目录与历史批次「{batch_name}」不一致。\n"
                f"历史目录: {state.data_dir}\n当前目录: {os.path.abspath(data_dir)}"
            )

        if force_rescan:
            return state, "已重新扫描（保留已有复核状态）"

        if not merge:
            raise DuplicateScanError(
                f"批次「{batch_name}」已存在，不能重复扫描（--no-merge 模式）。\n"
                f"去掉 --no-merge 即可自动续办，或加 --force 强制重新扫描。"
            )

        return state, "已打开已有批次继续工作（续办）"

    state = BatchState.new(rules, data_dir)
    return state, "已创建新批次"
