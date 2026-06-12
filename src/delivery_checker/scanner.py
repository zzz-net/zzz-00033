from __future__ import annotations

import fnmatch
import hashlib
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .config import CheckRules, RequiredFile, NamingRule
from .models import Issue, IssueType


class DirectoryNotFoundError(Exception):
    """目标目录不存在"""
    pass


def _should_ignore(rel_path: str, ignore_patterns: List[str]) -> bool:
    parts = rel_path.replace("\\", "/").split("/")
    for pat in ignore_patterns:
        if fnmatch.fnmatch(rel_path.replace("\\", "/"), pat):
            return True
        for part in parts:
            if fnmatch.fnmatch(part, pat):
                return True
    return False


def _walk_files(root: str, ignore_patterns: List[str]) -> List[str]:
    result: List[str] = []
    root_path = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir == ".":
            rel_dir = ""
        for d in list(dirnames):
            full_rel = os.path.join(rel_dir, d) if rel_dir else d
            if _should_ignore(full_rel, ignore_patterns):
                dirnames.remove(d)
        for fn in filenames:
            full_rel = os.path.join(rel_dir, fn) if rel_dir else fn
            if _should_ignore(full_rel, ignore_patterns):
                continue
            result.append(full_rel.replace("\\", "/"))
    return result


_GLOB_DOUBLESTAR_MARK = "\x00DS\x00"


def _glob_to_regex(pattern: str) -> str:
    """将 glob 模式转换为 regex，正确支持 **。

    规则：
      **  -> 匹配任意字符（包括 /），0 或多次
      *   -> 匹配除 / 外的任意字符，0 或多次
      ?   -> 匹配除 / 外的单个字符
      [...] -> 保持字符类语义
    """
    pat = pattern.replace("\\", "/")
    result = ""
    i = 0
    in_char_class = False
    while i < len(pat):
        c = pat[i]
        if in_char_class:
            result += c
            if c == "]":
                in_char_class = False
            i += 1
            continue
        if c == "[":
            in_char_class = True
            result += c
            i += 1
            continue
        if c == "*":
            if i + 1 < len(pat) and pat[i + 1] == "*":
                result += _GLOB_DOUBLESTAR_MARK
                i += 2
                if i < len(pat) and pat[i] == "/":
                    i += 1
                continue
            result += "[^/]*"
            i += 1
            continue
        if c == "?":
            result += "[^/]"
            i += 1
            continue
        result += re.escape(c)
        i += 1
    result = result.replace(_GLOB_DOUBLESTAR_MARK, ".*")
    return "^" + result + "$"


def _match_glob(path: str, pattern: str) -> bool:
    p = path.replace("\\", "/")
    pat = pattern.replace("\\", "/")
    try:
        regex = _glob_to_regex(pat)
        if re.match(regex, p):
            return True
    except re.error:
        return False
    return False


def _find_matching_files(files: List[str], pattern: str) -> List[str]:
    return [f for f in files if _match_glob(f, pattern)]


def _file_hash(full_path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(full_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
    except Exception:
        return ""
    return h.hexdigest()


def _issue_id(issue_type: IssueType, path: str, extra: str = "") -> str:
    raw = f"{issue_type.value}:{path}:{extra}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _check_missing(
    files: List[str],
    required_files: List[RequiredFile],
) -> List[Issue]:
    issues: List[Issue] = []
    for rf in required_files:
        matches = _find_matching_files(files, rf.pattern)
        if not matches and not rf.optional:
            msg = f"缺少必需文件或目录匹配项: {rf.pattern}"
            if rf.description:
                msg += f"（{rf.description}）"
            issues.append(Issue(
                id=_issue_id(IssueType.MISSING, rf.pattern),
                type=IssueType.MISSING,
                path=rf.pattern,
                message=msg,
                detail=rf.description,
            ))
    return issues


def _check_naming(
    files: List[str],
    required_files: List[RequiredFile],
    naming_rules: List[NamingRule],
) -> List[Issue]:
    issues: List[Issue] = []

    rule_map: Dict[str, List[NamingRule]] = {}
    for nr in naming_rules:
        if nr.name:
            rule_map.setdefault(nr.name, []).append(nr)
        rule_map.setdefault(nr.pattern, []).append(nr)

    for rf in required_files:
        matches = _find_matching_files(files, rf.pattern)
        for m in matches:
            if rf.naming_rule:
                target_rules = rule_map.get(rf.naming_rule, [])
                if not target_rules:
                    continue
                basename = os.path.basename(m)
                passed = False
                for nr in target_rules:
                    if re.match(nr.regex, basename):
                        passed = True
                        break
                if not passed:
                    rule_desc = " 或 ".join(
                        f"{nr.description or nr.regex}" for nr in target_rules
                    )
                    issues.append(Issue(
                        id=_issue_id(IssueType.NAMING, m, rf.naming_rule),
                        type=IssueType.NAMING,
                        path=m,
                        message=f"文件命名不符合规则「{rf.naming_rule}」",
                        detail=f"要求: {rule_desc}",
                    ))
    return issues


def _check_expiry(
    root: str,
    files: List[str],
    required_files: List[RequiredFile],
    global_expiry: Optional[str],
) -> List[Issue]:
    issues: List[Issue] = []
    now = datetime.now()

    def check_file(rel_path: str, expiry_str: str):
        full_path = os.path.join(root, rel_path)
        if not os.path.isfile(full_path):
            return
        try:
            expiry_dt = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
            if expiry_dt.tzinfo is not None:
                return
        except ValueError:
            return
        mtime_ts = os.path.getmtime(full_path)
        mtime = datetime.fromtimestamp(mtime_ts)
        if mtime < expiry_dt:
            issues.append(Issue(
                id=_issue_id(IssueType.EXPIRED, rel_path, expiry_str),
                type=IssueType.EXPIRED,
                path=rel_path,
                message=f"文件修改时间早于要求的 {expiry_str}",
                detail=f"实际修改时间: {mtime.strftime('%Y-%m-%d %H:%M:%S')}",
            ))

    for rf in required_files:
        if rf.expiry_date:
            for m in _find_matching_files(files, rf.pattern):
                check_file(m, rf.expiry_date)
        elif global_expiry:
            for m in _find_matching_files(files, rf.pattern):
                check_file(m, global_expiry)

    return issues


def _check_duplicates(
    root: str,
    files: List[str],
) -> List[Issue]:
    size_map: Dict[int, List[str]] = {}
    issues: List[Issue] = []

    for f in files:
        full = os.path.join(root, f)
        if not os.path.isfile(full):
            continue
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        if size < 1:
            continue
        size_map.setdefault(size, []).append(f)

    for size, paths in size_map.items():
        if len(paths) < 2:
            continue
        real_hashes: Dict[str, List[str]] = {}
        for p in paths:
            h = _file_hash(os.path.join(root, p))
            if not h:
                continue
            real_hashes.setdefault(h, []).append(p)
        for h, dup_paths in real_hashes.items():
            if len(dup_paths) < 2:
                continue
            group_key = f"dup-{h[:8]}"
            base = dup_paths[0]
            for dup in dup_paths[1:]:
                issues.append(Issue(
                    id=_issue_id(IssueType.DUPLICATE, dup, h),
                    type=IssueType.DUPLICATE,
                    path=dup,
                    message=f"与 {base} 内容完全相同",
                    detail=f"文件大小: {size} bytes, 副本数: {len(dup_paths)}",
                    group_key=group_key,
                ))
    return issues


def _pattern_has_wildcard(pattern: str) -> bool:
    return any(c in pattern for c in "*?[")


def _check_slot_overflow(
    files: List[str],
    required_files: List[RequiredFile],
) -> List[Issue]:
    """检查同一个必需槽位匹配到超出上限的文件数（视为重复/冗余）。

    默认规则：
      - pattern 不含通配符 (*?[) 时默认 max_matches = 1（单文件槽位）
      - pattern 含通配符时默认 max_matches = None（不限制）
      - 可通过规则中 max_matches 字段显式覆盖
    """
    issues: List[Issue] = []
    for rf in required_files:
        if rf.optional:
            continue
        matches = _find_matching_files(files, rf.pattern)
        limit = rf.max_matches
        if limit is None:
            if not _pattern_has_wildcard(rf.pattern):
                limit = 1
            else:
                limit = None
        if limit is None:
            continue
        if len(matches) > limit:
            group_key = f"slot-{rf.pattern}"
            for extra in matches[limit:]:
                issues.append(Issue(
                    id=_issue_id(IssueType.DUPLICATE, extra, rf.pattern),
                    type=IssueType.DUPLICATE,
                    path=extra,
                    message=f"超出必需槽位「{rf.pattern}」的数量限制（最多 {limit} 个）",
                    detail=f"已匹配 {len(matches)} 个文件: {', '.join(matches)}",
                    group_key=group_key,
                ))
    return issues


def _check_untracked(
    files: List[str],
    required_files: List[RequiredFile],
    ignore_patterns: List[str],
) -> List[Issue]:
    issues: List[Issue] = []
    tracked: Set[str] = set()
    for rf in required_files:
        for m in _find_matching_files(files, rf.pattern):
            tracked.add(m)

    for f in files:
        if f in tracked:
            continue
        issues.append(Issue(
            id=_issue_id(IssueType.UNTRACKED, f),
            type=IssueType.UNTRACKED,
            path=f,
            message="文件不在规则清单中",
            detail="未被任何 required_files 规则匹配",
        ))
    return issues


def scan_directory(rules: CheckRules, data_dir: str) -> List[Issue]:
    if not os.path.exists(data_dir):
        raise DirectoryNotFoundError(f"资料目录不存在: {data_dir}")
    if not os.path.isdir(data_dir):
        raise DirectoryNotFoundError(f"路径不是目录: {data_dir}")

    files = _walk_files(data_dir, rules.ignore_patterns)

    issues: List[Issue] = []
    issues.extend(_check_missing(files, rules.required_files))
    issues.extend(_check_naming(files, rules.required_files, rules.naming_rules))
    issues.extend(_check_expiry(data_dir, files, rules.required_files, rules.expiry_date))
    issues.extend(_check_duplicates(data_dir, files))
    issues.extend(_check_slot_overflow(files, rules.required_files))
    issues.extend(_check_untracked(files, rules.required_files, rules.ignore_patterns))

    return issues
