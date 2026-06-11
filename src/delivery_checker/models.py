from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any


class IssueType(str, Enum):
    MISSING = "missing"
    NAMING = "naming"
    EXPIRED = "expired"
    DUPLICATE = "duplicate"
    UNTRACKED = "untracked"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    IGNORED = "ignored"
    TODO = "todo"


ISSUE_TYPE_LABELS = {
    IssueType.MISSING: "必需文件缺失",
    IssueType.NAMING: "命名不符合规范",
    IssueType.EXPIRED: "文件已过期",
    IssueType.DUPLICATE: "重复文件",
    IssueType.UNTRACKED: "未纳入规则的文件",
}

REVIEW_STATUS_LABELS = {
    ReviewStatus.PENDING: "待复核",
    ReviewStatus.PASSED: "通过",
    ReviewStatus.IGNORED: "忽略",
    ReviewStatus.TODO: "待补充",
}


@dataclass
class Issue:
    id: str
    type: IssueType
    path: str
    message: str
    detail: str = ""
    status: ReviewStatus = ReviewStatus.PENDING
    reviewer: Optional[str] = None
    note: str = ""
    reviewed_at: Optional[str] = None
    group_key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value if isinstance(self.type, IssueType) else self.type
        d["status"] = self.status.value if isinstance(self.status, ReviewStatus) else self.status
        d["type_label"] = ISSUE_TYPE_LABELS.get(IssueType(d["type"]), d["type"])
        d["status_label"] = REVIEW_STATUS_LABELS.get(ReviewStatus(d["status"]), d["status"])
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Issue":
        return cls(
            id=data["id"],
            type=IssueType(data["type"]),
            path=data["path"],
            message=data["message"],
            detail=data.get("detail", ""),
            status=ReviewStatus(data.get("status", ReviewStatus.PENDING.value)),
            reviewer=data.get("reviewer"),
            note=data.get("note", ""),
            reviewed_at=data.get("reviewed_at"),
            group_key=data.get("group_key"),
        )


@dataclass
class UndoRecord:
    issue_id: str
    prev_status: ReviewStatus
    prev_reviewer: Optional[str]
    prev_note: str
    prev_reviewed_at: Optional[str]
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "prev_status": self.prev_status.value,
            "prev_reviewer": self.prev_reviewer,
            "prev_note": self.prev_note,
            "prev_reviewed_at": self.prev_reviewed_at,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UndoRecord":
        return cls(
            issue_id=data["issue_id"],
            prev_status=ReviewStatus(data["prev_status"]),
            prev_reviewer=data.get("prev_reviewer"),
            prev_note=data.get("prev_note", ""),
            prev_reviewed_at=data.get("prev_reviewed_at"),
            timestamp=data["timestamp"],
        )
