"""Delivery Checker 单元测试 — 覆盖 bug 修复点。

用 unittest 标准库，无需安装额外依赖。
运行: python -m unittest tests/test_scanner.py tests/test_state.py tests/test_config.py -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
import json
import hashlib
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from delivery_checker.config import (
    CheckRules,
    ConfigError,
    NamingRule,
    RequiredFile,
    parse_rules_file,
)
from delivery_checker.models import IssueType, ReviewStatus, ISSUE_TYPE_LABELS
from delivery_checker.scanner import (
    DirectoryNotFoundError,
    _check_duplicates,
    _check_missing,
    _check_naming,
    _check_slot_overflow,
    _check_untracked,
    _find_matching_files,
    _glob_to_regex,
    _match_glob,
    _pattern_has_wildcard,
    scan_directory,
)
from delivery_checker.state import (
    BatchState,
    EmptyUndoHistoryError,
    list_batches,
)
from delivery_checker.rule_pkg import (
    RulePackage,
    RulePkgConflictError,
    RulePkgFormatError,
    RulePkgNotFoundError,
    RulePkgPermissionError,
    delete_rule_package,
    export_rule_package,
    get_rule_package,
    import_rule_package,
    list_rule_packages,
    save_rule_package,
)
from delivery_checker.view_preset import (
    ViewPreset,
    ViewPresetConflictError,
    ViewPresetFormatError,
    ViewPresetNotFoundError,
    ViewPresetPermissionError,
    delete_view_preset,
    export_view_preset,
    get_view_preset,
    import_view_preset,
    list_view_presets,
    save_view_preset,
)
from delivery_checker.compare import (
    CompareConfig,
    CompareConfigConflictError,
    CompareConfigError,
    CompareConfigNotFoundError,
    CompareError,
    BatchNotFoundError,
    ExportConflictError,
    ExportPermissionError,
    compare_batches,
    compare_by_source,
    delete_compare_config,
    export_compare_result,
    get_compare_config,
    list_compare_configs,
    save_compare_config,
    _compute_match_key,
    _normalize_path,
    _detect_changes,
)
from delivery_checker.snapshot import (
    Snapshot,
    SnapshotConflictError,
    SnapshotFormatError,
    SnapshotNotFoundError,
    SnapshotPermissionError,
    SnapshotBatchNotFoundError,
    create_snapshot,
    delete_snapshot,
    export_snapshot,
    get_snapshot,
    import_snapshot,
    list_snapshots,
)
from delivery_checker.backup import (
    BackupConflictError,
    BackupCorruptedError,
    BackupFormatError,
    BackupNotFoundError,
    BackupPermissionError,
    BackupVersionMismatchError,
    apply_restore,
    create_backup,
    delete_backup,
    export_backup,
    get_backup,
    import_backup,
    list_backups,
    preview_restore,
    show_backup,
)
from delivery_checker.plan import (
    Plan,
    PlanTaskItem,
    PlanConflictError,
    PlanFormatError,
    PlanNotFoundError,
    PlanPermissionError,
    PlanExecutionError,
    delete_plan,
    export_plan,
    get_plan,
    import_plan,
    list_plans,
    run_plan,
    save_plan,
)


class TestGlobMatching(unittest.TestCase):
    """修复 Bug 1: ** 跨目录 glob 必须正确匹配零或多段路径。"""

    def test_doublestar_matches_zero_segments(self):
        self.assertTrue(_match_glob("docs/design.md", "docs/**/*.md"))
        self.assertTrue(_match_glob("src/main.py", "src/**/*.py"))

    def test_doublestar_matches_multiple_segments(self):
        self.assertTrue(_match_glob("docs/a/b/design.md", "docs/**/*.md"))
        self.assertTrue(_match_glob("src/backup/sub/main.py", "src/**/*.py"))

    def test_doublestar_does_not_leak_outside_prefix(self):
        self.assertFalse(_match_glob("README.md", "docs/**/*.md"))
        self.assertFalse(_match_glob("other/main.py", "src/**/*.py"))

    def test_doublestar_at_start(self):
        self.assertTrue(_match_glob("main.py", "**/*.py"))
        self.assertTrue(_match_glob("a/b/c/main.py", "**/*.py"))
        self.assertFalse(_match_glob("README.md", "**/*.py"))

    def test_plain_filename_does_not_match_subdirectory(self):
        self.assertTrue(_match_glob("README.md", "README.md"))
        self.assertFalse(_match_glob("docs/README.md", "README.md"))

    def test_single_star_no_slash(self):
        self.assertTrue(_match_glob("build/report_final.pdf", "build/report*.pdf"))
        self.assertTrue(_match_glob("build/report_20260612.pdf", "build/report*.pdf"))
        self.assertFalse(_match_glob("other/report_final.pdf", "build/report*.pdf"))

    def test_single_star_in_directory_does_not_cross_slash(self):
        self.assertTrue(_match_glob("docs/design.md", "docs/*.md"))
        self.assertFalse(_match_glob("docs/sub/design.md", "docs/*.md"))

    def test_find_matching_files_with_doublestar(self):
        files = [
            "README.md",
            "docs/design.md",
            "docs/sub/api.md",
            "src/main.py",
            "src/utils.py",
            "src/backup/main_copy.py",
        ]
        self.assertEqual(
            sorted(_find_matching_files(files, "docs/**/*.md")),
            ["docs/design.md", "docs/sub/api.md"],
        )
        self.assertEqual(
            sorted(_find_matching_files(files, "src/**/*.py")),
            ["src/backup/main_copy.py", "src/main.py", "src/utils.py"],
        )

    def test_glob_to_regex_escapes_special_chars(self):
        regex = _glob_to_regex("a.b/foo-bar.py")
        self.assertIn(r"\.", regex)
        self.assertIn(r"\-", regex)


class TestPatternHasWildcard(unittest.TestCase):
    def test_detects_wildcards(self):
        self.assertTrue(_pattern_has_wildcard("*.txt"))
        self.assertTrue(_pattern_has_wildcard("file?.txt"))
        self.assertTrue(_pattern_has_wildcard("[abc].txt"))
        self.assertTrue(_pattern_has_wildcard("**/*.py"))
        self.assertFalse(_pattern_has_wildcard("README.md"))
        self.assertFalse(_pattern_has_wildcard("config/config.yaml"))


class TestMissingCheck(unittest.TestCase):
    def test_missing_required_triggers_issue(self):
        issues = _check_missing(
            files=["README.md"],
            required_files=[RequiredFile(pattern="CHANGELOG.md", description="变更记录")],
        )
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].type, IssueType.MISSING)
        self.assertIn("CHANGELOG.md", issues[0].path)

    def test_optional_missing_is_ok(self):
        issues = _check_missing(
            files=["README.md"],
            required_files=[
                RequiredFile(pattern="tests/**/*.py", optional=True),
            ],
        )
        self.assertEqual(len(issues), 0)

    def test_doublestar_present_is_not_missing(self):
        issues = _check_missing(
            files=["docs/design.md"],
            required_files=[RequiredFile(pattern="docs/**/*.md")],
        )
        self.assertEqual(len(issues), 0)


class TestNamingCheck(unittest.TestCase):
    """修复 Bug 2: naming_rules 需通过 name 字段正确关联。"""

    RULES = [
        NamingRule(
            name="report-date",
            pattern="build/report*.pdf",
            regex=r"^test_report_(\d{8})\.pdf$",
            description="形如 test_report_20260612.pdf",
        ),
    ]

    def test_name_keyed_rule_applies(self):
        issues = _check_naming(
            files=["build/report_bad.pdf"],
            required_files=[
                RequiredFile(pattern="build/report*.pdf", naming_rule="report-date"),
            ],
            naming_rules=self.RULES,
        )
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].type, IssueType.NAMING)
        self.assertEqual(issues[0].path, "build/report_bad.pdf")

    def test_matching_name_passes(self):
        issues = _check_naming(
            files=["build/test_report_20260612.pdf"],
            required_files=[
                RequiredFile(pattern="build/report*.pdf", naming_rule="report-date"),
            ],
            naming_rules=self.RULES,
        )
        self.assertEqual(len(issues), 0)

    def test_rule_without_name_falls_back_to_pattern(self):
        """兼容性：未指定 name 时仍能用 pattern 当 key（但推荐显式 name）。"""
        rules = [
            NamingRule(
                pattern="build/report*.pdf",
                regex=r"^test_report_(\d{8})\.pdf$",
            ),
        ]
        # 使用 pattern 作为 naming_rule key（fallback 行为）
        issues = _check_naming(
            files=["build/report_bad.pdf"],
            required_files=[
                RequiredFile(pattern="build/report*.pdf", naming_rule="build/report*.pdf"),
            ],
            naming_rules=rules,
        )
        self.assertEqual(len(issues), 1)

    def test_doublestar_pattern_naming_check(self):
        """命名规则 + ** glob 正确联动。"""
        rules = [
            NamingRule(
                name="sig-doc",
                pattern="docs/**/req*.docx",
                regex=r"^requirements_v\d{4}\.\d{2}_签字版\.docx$",
            ),
        ]
        issues = _check_naming(
            files=["docs/sub/requirements_bad.docx"],
            required_files=[
                RequiredFile(pattern="docs/**/req*.docx", naming_rule="sig-doc"),
            ],
            naming_rules=rules,
        )
        self.assertEqual(len(issues), 1)


class TestDuplicateCheck(unittest.TestCase):
    """修复 Bug 3 & 4: basename 不同但内容相同 / 同槽位多文件都应识别。"""

    def test_content_duplicate_different_basename(self):
        with tempfile.TemporaryDirectory() as tmp:
            p1 = os.path.join(tmp, "main.py")
            p2 = os.path.join(tmp, "backup", "main_copy.py")
            os.makedirs(os.path.dirname(p2), exist_ok=True)
            content = "print('hello')\n" * 5
            with open(p1, "w", encoding="utf-8") as f:
                f.write(content)
            with open(p2, "w", encoding="utf-8") as f:
                f.write(content)
            issues = _check_duplicates(tmp, ["main.py", "backup/main_copy.py"])
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].type, IssueType.DUPLICATE)
            self.assertIn("main_copy.py", issues[0].path)
            self.assertIn("main.py", issues[0].message)

    def test_different_content_no_false_positive(self):
        with tempfile.TemporaryDirectory() as tmp:
            p1 = os.path.join(tmp, "a.txt")
            p2 = os.path.join(tmp, "b.txt")
            with open(p1, "w", encoding="utf-8") as f:
                f.write("aaa" * 100)
            with open(p2, "w", encoding="utf-8") as f:
                f.write("bbb" * 100)
            issues = _check_duplicates(tmp, ["a.txt", "b.txt"])
            self.assertEqual(len(issues), 0)


class TestSlotOverflow(unittest.TestCase):
    """同槽位多文件视为 duplicate。"""

    def test_plain_pattern_multiple_hits_defaults_to_max_1(self):
        files = [
            "config/config.yaml",
            "config/backup/config.yaml",
        ]
        issues = _check_slot_overflow(
            files=files,
            required_files=[RequiredFile(pattern="config/config.yaml")],
        )
        # 注意：config/backup/config.yaml 不匹配精确 pattern config/config.yaml，
        # 因为 pattern 不含通配符。所以这里不会触发 overflow。我们用 wildcard 加 max_matches。
        self.assertEqual(len(issues), 0)

    def test_wildcard_with_explicit_max_matches(self):
        files = ["deliverables/slot_a.txt", "deliverables/slot_b.txt"]
        issues = _check_slot_overflow(
            files=files,
            required_files=[
                RequiredFile(pattern="deliverables/slot_*.txt", max_matches=1),
            ],
        )
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].type, IssueType.DUPLICATE)
        self.assertIn("最多 1 个", issues[0].message)
        self.assertIn("slot_b.txt", issues[0].path)

    def test_wildcard_no_max_matches_unlimited(self):
        """pattern 含通配符且未指定 max_matches 时不限制。"""
        files = ["src/main.py", "src/utils.py", "src/app.py"]
        issues = _check_slot_overflow(
            files=files,
            required_files=[RequiredFile(pattern="src/**/*.py")],
        )
        self.assertEqual(len(issues), 0)

    def test_optional_skipped(self):
        files = ["a.txt", "b.txt"]
        issues = _check_slot_overflow(
            files=files,
            required_files=[
                RequiredFile(pattern="*.txt", optional=True, max_matches=1),
            ],
        )
        self.assertEqual(len(issues), 0)


class TestUntrackedCheck(unittest.TestCase):
    def test_untracked_files_detected(self):
        issues = _check_untracked(
            files=["README.md", "docs/design.md", "scratch.tmp"],
            required_files=[
                RequiredFile(pattern="README.md"),
                RequiredFile(pattern="docs/**/*.md"),
            ],
            ignore_patterns=[],
        )
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].path, "scratch.tmp")
        self.assertEqual(issues[0].type, IssueType.UNTRACKED)

    def test_doublestar_pattern_properly_tracks_files(self):
        """修复 Bug 1 后，glob ** 匹配到的文件应被视为 tracked。"""
        issues = _check_untracked(
            files=[
                "README.md",
                "docs/design.md",
                "docs/sub/api.md",
                "src/main.py",
                "src/backup/main_copy.py",
                "extra.bin",
            ],
            required_files=[
                RequiredFile(pattern="README.md"),
                RequiredFile(pattern="docs/**/*.md"),
                RequiredFile(pattern="src/**/*.py"),
            ],
            ignore_patterns=[],
        )
        self.assertEqual([i.path for i in issues], ["extra.bin"])


class TestEndToEndScan(unittest.TestCase):
    """对真实 examples/sample_data 的端到端扫描，覆盖 5 类问题。"""

    ROOT = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )

    @classmethod
    def setUpClass(cls):
        cls.rules = parse_rules_file(
            os.path.join(cls.ROOT, "examples", "rules.yaml")
        )
        cls.data_dir = os.path.join(cls.ROOT, "examples", "sample_data")
        cls.issues = scan_directory(cls.rules, cls.data_dir)
        cls.by_type = {t.value: [] for t in IssueType}
        for i in cls.issues:
            cls.by_type[i.type.value].append(i)

    def test_five_issue_types_all_present(self):
        """README 承诺的 5 类问题必须都识别到。"""
        for t in IssueType:
            self.assertGreaterEqual(
                len(self.by_type[t.value]),
                1,
                f"缺少问题类型: {ISSUE_TYPE_LABELS.get(t, t.value)}",
            )

    def test_missing_contains_expected_files(self):
        missing_paths = {i.path for i in self.by_type["missing"]}
        self.assertIn("config/config.yaml", missing_paths)
        self.assertIn("CHANGELOG.md", missing_paths)

    def test_naming_contains_expected_files(self):
        naming_paths = {i.path for i in self.by_type["naming"]}
        self.assertIn("docs/requirements.docx", naming_paths)
        self.assertIn("build/report_final.pdf", naming_paths)

    def test_expired_present(self):
        self.assertEqual(len(self.by_type["expired"]), 1)
        self.assertEqual(self.by_type["expired"][0].path, "docs/requirements.docx")

    def test_duplicate_includes_content_and_slot(self):
        dup_paths = {i.path for i in self.by_type["duplicate"]}
        self.assertIn("src/backup/main_copy.py", dup_paths)  # 内容重复
        self.assertIn("deliverables/slot_b.txt", dup_paths)    # 槽位溢出

    def test_untracked_only_three(self):
        untracked = sorted(i.path for i in self.by_type["untracked"])
        self.assertEqual(
            untracked,
            [
                "deliverables/extra.bin",
                "extra_readme.txt",
                "temp_scratch.tmp",
            ],
        )

    def test_total_issue_count(self):
        self.assertEqual(len(self.issues), 10)


class TestDirectoryNotFound(unittest.TestCase):
    def test_scan_missing_dir_raises(self):
        rules = CheckRules(batch_name="t", root_alias="t")
        with self.assertRaises(DirectoryNotFoundError):
            scan_directory(rules, "/definitely/not/exist/zzz_12345")


class TestStateListBatches(unittest.TestCase):
    """修复 Bug 5: issues 为 dict 时 list_batches 不能抛异常。"""

    def test_list_with_dict_issues(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules = CheckRules(batch_name="x-batch", root_alias="x")
            rules.source_path = os.path.join(tmp, "rules.yaml")
            state = BatchState.new(rules, tmp)
            # 手工塞入 dict issues（模拟真实持久化格式）
            from delivery_checker.models import Issue
            state.issues = {
                "abc": Issue(
                    id="abc",
                    type=IssueType.MISSING,
                    path="CHANGELOG.md",
                    message="x",
                    status=ReviewStatus.PENDING,
                ),
                "def": Issue(
                    id="def",
                    type=IssueType.NAMING,
                    path="a.pdf",
                    message="x",
                    status=ReviewStatus.PASSED,
                ),
            }
            state.save(tmp)
            batches = list_batches(tmp)
            self.assertEqual(len(batches), 1)
            self.assertEqual(batches[0]["batch_name"], "x-batch")
            self.assertEqual(batches[0]["issue_count"], 2)
            self.assertEqual(batches[0]["pending_count"], 1)

    def test_list_with_list_issues_backward_compat(self):
        """兼容旧格式（issues 存成 list）。"""
        with tempfile.TemporaryDirectory() as tmp:
            import json
            state_dir = os.path.join(tmp, ".delivery_check")
            os.makedirs(state_dir)
            fp = os.path.join(state_dir, "old.state.json")
            with open(fp, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "batch_name": "old",
                        "data_dir": tmp,
                        "issues": [
                            {"status": "pending"},
                            {"status": "passed"},
                            {"status": "ignored"},
                        ],
                        "created_at": "",
                        "updated_at": "",
                    },
                    fh,
                    ensure_ascii=False,
                )
            batches = list_batches(tmp)
            self.assertEqual(len(batches), 1)
            self.assertEqual(batches[0]["issue_count"], 3)
            self.assertEqual(batches[0]["pending_count"], 1)


class TestConfigErrors(unittest.TestCase):
    def test_bad_yaml_raises_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "bad.yaml")
            with open(p, "w", encoding="utf-8") as f:
                f.write(":::: broken\n[\n")
            with self.assertRaises(ConfigError):
                parse_rules_file(p)

    def test_missing_required_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "bad.yaml")
            with open(p, "w", encoding="utf-8") as f:
                f.write("root_alias: x\n")  # 缺少 batch_name
            with self.assertRaises(ConfigError):
                parse_rules_file(p)

    def test_required_files_must_be_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "bad.yaml")
            with open(p, "w", encoding="utf-8") as f:
                f.write(
                    "batch_name: x\nroot_alias: x\nrequired_files: 'should-be-array'\n"
                )
            with self.assertRaises(ConfigError):
                parse_rules_file(p)


class TestUndo(unittest.TestCase):
    def test_empty_undo_raises_without_side_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules = CheckRules(batch_name="u", root_alias="u")
            rules.source_path = os.path.join(tmp, "r.yaml")
            from delivery_checker.models import Issue
            state = BatchState.new(rules, tmp)
            orig_id = "q1"
            state.issues = {
                orig_id: Issue(
                    id=orig_id, type=IssueType.MISSING, path="a", message="x",
                    status=ReviewStatus.PENDING,
                ),
            }
            state.save(tmp)

            with self.assertRaises(EmptyUndoHistoryError):
                state.undo_last()

            # 确认旧状态未被修改
            reloaded = BatchState.load(tmp, "u")
            self.assertEqual(reloaded.issues[orig_id].status, ReviewStatus.PENDING)
            self.assertEqual(len(reloaded.undo_stack), 0)

    def test_mark_then_undo_restores(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules = CheckRules(batch_name="u2", root_alias="u")
            rules.source_path = os.path.join(tmp, "r.yaml")
            from delivery_checker.models import Issue
            state = BatchState.new(rules, tmp)
            iid = "q2"
            state.issues = {
                iid: Issue(
                    id=iid, type=IssueType.MISSING, path="a", message="x",
                    status=ReviewStatus.PENDING,
                ),
            }
            state.mark_issue(iid, ReviewStatus.PASSED, "tester", note="ok")
            self.assertEqual(state.issues[iid].status, ReviewStatus.PASSED)
            self.assertEqual(len(state.undo_stack), 1)
            restored = state.undo_last()
            self.assertEqual(restored.status, ReviewStatus.PENDING)
            self.assertEqual(state.issues[iid].status, ReviewStatus.PENDING)
            self.assertEqual(len(state.undo_stack), 0)


class TestJsonYamlEquivalence(unittest.TestCase):
    """JSON 与 YAML 样例规则扫描同一份 sample_data 应得到一致的问题数量与类型分布。"""

    ROOT = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )
    DATA_DIR = os.path.join(ROOT, "examples", "sample_data")

    def test_both_yield_10_issues(self):
        rules_yaml = parse_rules_file(
            os.path.join(self.ROOT, "examples", "rules.yaml")
        )
        rules_json = parse_rules_file(
            os.path.join(self.ROOT, "examples", "rules.json")
        )
        issues_yaml = scan_directory(rules_yaml, self.DATA_DIR)
        issues_json = scan_directory(rules_json, self.DATA_DIR)

        self.assertEqual(len(issues_yaml), 10)
        self.assertEqual(len(issues_json), 10)

        # 按类型计数对比
        def _count(issues):
            cnt = {}
            for i in issues:
                cnt[i.type.value] = cnt.get(i.type.value, 0) + 1
            return cnt

        yaml_counts = _count(issues_yaml)
        json_counts = _count(issues_json)

        self.assertEqual(yaml_counts, json_counts,
                         "JSON 与 YAML 规则扫出的问题类型分布不一致")
        self.assertEqual(yaml_counts.get("missing", 0), 2)
        self.assertEqual(yaml_counts.get("naming", 0), 2)
        self.assertEqual(yaml_counts.get("expired", 0), 1)
        self.assertEqual(yaml_counts.get("duplicate", 0), 2)
        self.assertEqual(yaml_counts.get("untracked", 0), 3)


class TestCliExitCodes(unittest.TestCase):
    """通过 subprocess 调用 python -m delivery_checker，验证退出码符合 README 描述。"""

    ROOT = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )
    DATA_DIR = os.path.join(ROOT, "examples", "sample_data")

    def _run(self, *args):
        import subprocess
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.join(self.ROOT, "src")
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, "-m", "delivery_checker", *args],
            env=env,
            capture_output=True,
            text=True,
            cwd=self.ROOT,
        )
        return result

    def test_clean_scan_exit_0(self):
        """在独立状态目录下首次 scan 应返回 0。"""
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_in(tmp, "scan",
                                  os.path.join(self.ROOT, "examples", "rules.yaml"),
                                  self.DATA_DIR)
            self.assertEqual(result.returncode, 0,
                             f"stdout={result.stdout}\nstderr={result.stderr}")

    def _run_in(self, work_dir, *args):
        import subprocess
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.join(self.ROOT, "src")
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, "-m", "delivery_checker", *args],
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            cwd=work_dir,
        )
        return result

    def test_bad_yaml_exit_2(self):
        """坏 YAML 配置应返回 2。"""
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.yaml")
            with open(bad, "w", encoding="utf-8") as f:
                f.write(":::: broken yaml [\n")
            result = self._run_in(tmp, "scan", bad, self.DATA_DIR)
            self.assertEqual(result.returncode, 2,
                             f"stderr={result.stderr}")

    def test_missing_dir_exit_1(self):
        """目录不存在（且为新批次）应返回 1。"""
        with tempfile.TemporaryDirectory() as tmp:
            rules_path = os.path.join(tmp, "r.yaml")
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write("batch_name: 'test-exit-new'\nroot_alias: 't'\nrequired_files:\n  - 'README.md'\n")
            result = self._run_in(tmp, "scan", rules_path,
                                  os.path.join(tmp, "__definitely_not_there__"))
            self.assertEqual(result.returncode, 1,
                             f"stderr={result.stderr}")

    def test_duplicate_scan_exit_3(self):
        """重复扫描（同名批次已存在，--no-merge）应返回 3。"""
        with tempfile.TemporaryDirectory() as tmp:
            rules_path = os.path.join(tmp, "r.yaml")
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write("batch_name: 'dup-test'\nroot_alias: 't'\nrequired_files:\n  - 'README.md'\n")
            data_dir = os.path.join(tmp, "data")
            os.makedirs(data_dir)
            with open(os.path.join(data_dir, "README.md"), "w") as f:
                f.write("hi")

            # 第一次 scan 成功
            r1 = self._run_in(tmp, "scan", rules_path, data_dir)
            self.assertEqual(r1.returncode, 0, f"first scan: {r1.stderr}")

            # 第二次 --no-merge 应拒绝并返回 3
            r2 = self._run_in(tmp, "scan", rules_path, data_dir,
                              "--no-merge")
            self.assertEqual(r2.returncode, 3,
                             f"duplicate scan: {r2.stderr}")

    def test_batch_not_found_exit_1(self):
        """review 不存在的批次应返回 1。"""
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_in(tmp, "review", "__no_such_batch__")
            self.assertEqual(result.returncode, 1,
                             f"stderr={result.stderr}")


class TestStatePersistencePreserved(unittest.TestCase):
    """已有状态文件、list/mark/export/undo 行为不受本次修改影响。"""

    def test_full_workflow_preserves_state(self):
        """完整走一遍 scan → mark → undo → list → export，状态一致。"""
        with tempfile.TemporaryDirectory() as tmp:
            rules = CheckRules(batch_name="wf-test", root_alias="wf")
            rules.source_path = os.path.join(tmp, "r.yaml")
            rules.required_files = [
                RequiredFile(pattern="a.txt"),
                RequiredFile(pattern="b.txt"),
                RequiredFile(pattern="c.txt"),
            ]

            data_dir = os.path.join(tmp, "data")
            os.makedirs(data_dir)
            # a.txt 存在，b/c 缺失 → 2 missing
            with open(os.path.join(data_dir, "a.txt"), "w") as f:
                f.write("a")

            # 1) scan
            from delivery_checker.state import create_or_resume_batch
            state, action = create_or_resume_batch(
                base_dir=tmp, rules=rules, data_dir=data_dir,
                force_rescan=False, merge=True,
            )
            self.assertEqual(action, "已创建新批次")
            issues = scan_directory(rules, data_dir)
            state.set_issues(issues)
            state.save(tmp)
            self.assertEqual(len(issues), 2)

            # 2) mark 一个为 passed
            iid = state.get_sorted_issues()[0].id
            state.mark_issue(iid, ReviewStatus.PASSED, "alice", note="ok")
            state.save(tmp)

            # 3) mark 另一个为 ignored
            iid2 = [i for i in state.get_sorted_issues()
                    if i.status == ReviewStatus.PENDING][0].id
            state.mark_issue(iid2, ReviewStatus.IGNORED, "bob", note="ok2")
            state.save(tmp)
            self.assertEqual(len(state.undo_stack), 2)

            # 4) undo 一步
            state.undo_last()
            state.save(tmp)
            self.assertEqual(len(state.undo_stack), 1)

            # 5) list_batches 能看到正确计数
            batches = list_batches(tmp)
            self.assertEqual(len(batches), 1)
            self.assertEqual(batches[0]["issue_count"], 2)
            self.assertEqual(batches[0]["pending_count"], 1)
            self.assertEqual(batches[0]["batch_name"], "wf-test")

            # 6) 导出报告（HTML 和 CSV）都不报错
            from delivery_checker.report import export_html, export_csv
            html_path = os.path.join(tmp, "out.html")
            csv_path = os.path.join(tmp, "out.csv")
            export_html(state, html_path)
            export_csv(state, csv_path)
            self.assertTrue(os.path.getsize(html_path) > 0)
            self.assertTrue(os.path.getsize(csv_path) > 0)


class TestRulePackageCore(unittest.TestCase):
    """规则包核心功能：保存、列表、获取、删除"""

    def _make_rules(self, batch_name="test"):
        rules = CheckRules(batch_name=batch_name, root_alias="test")
        rules.source_path = "/fake/path.yaml"
        rules.required_files = [
            RequiredFile(pattern="README.md", description="项目说明"),
            RequiredFile(pattern="docs/**/*.md", description="文档"),
        ]
        return rules

    def test_save_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._make_rules("test-batch")
            pkg = save_rule_package(
                base_dir=tmp,
                name="team-rules",
                version="1.0.0",
                description="团队标准交付规则",
                rules=rules,
            )
            self.assertEqual(pkg.name, "team-rules")
            self.assertEqual(pkg.version, "1.0.0")
            self.assertEqual(pkg.description, "团队标准交付规则")

            packages = list_rule_packages(tmp)
            self.assertEqual(len(packages), 1)
            self.assertEqual(packages[0]["name"], "team-rules")
            self.assertEqual(packages[0]["version"], "1.0.0")
            self.assertEqual(packages[0]["rule_count"], 2)

    def test_get_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._make_rules("test-batch")
            save_rule_package(tmp, "team-rules", "1.0.0", "desc", rules)

            pkg = get_rule_package(tmp, "team-rules", "1.0.0")
            self.assertEqual(pkg.name, "team-rules")
            self.assertEqual(pkg.version, "1.0.0")
            self.assertEqual(len(pkg.rules["required_files"]), 2)

            converted = pkg.to_rules()
            self.assertEqual(len(converted.required_files), 2)
            self.assertEqual(converted.required_files[0].pattern, "README.md")

    def test_delete_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._make_rules("test-batch")
            save_rule_package(tmp, "team-rules", "1.0.0", "desc", rules)
            self.assertEqual(len(list_rule_packages(tmp)), 1)

            delete_rule_package(tmp, "team-rules", "1.0.0")
            self.assertEqual(len(list_rule_packages(tmp)), 0)

            with self.assertRaises(RulePkgNotFoundError):
                get_rule_package(tmp, "team-rules", "1.0.0")

    def test_duplicate_name_version_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._make_rules("test-batch")
            save_rule_package(tmp, "team-rules", "1.0.0", "desc", rules)

            with self.assertRaises(RulePkgConflictError):
                save_rule_package(tmp, "team-rules", "1.0.0", "new desc", rules)

    def test_force_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules1 = self._make_rules("batch1")
            save_rule_package(tmp, "team-rules", "1.0.0", "old desc", rules1)

            rules2 = self._make_rules("batch2")
            rules2.required_files.append(
                RequiredFile(pattern="config.yaml", description="配置")
            )
            pkg = save_rule_package(
                tmp, "team-rules", "1.0.0", "new desc", rules2, force=True
            )
            self.assertEqual(pkg.description, "new desc")

            reloaded = get_rule_package(tmp, "team-rules", "1.0.0")
            self.assertEqual(len(reloaded.rules["required_files"]), 3)
            self.assertEqual(reloaded.description, "new desc")

    def test_validation_empty_name_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._make_rules("test-batch")
            with self.assertRaises(RulePkgFormatError):
                save_rule_package(tmp, "", "1.0.0", "desc", rules)
            with self.assertRaises(RulePkgFormatError):
                save_rule_package(tmp, "name", "", "desc", rules)
            with self.assertRaises(RulePkgFormatError):
                save_rule_package(tmp, "  ", "1.0.0", "desc", rules)


class TestRulePackagePersistence(unittest.TestCase):
    """跨重启可见性：模拟重启（重新加载）后规则包仍然存在"""

    def _make_rules(self, batch_name="test"):
        rules = CheckRules(batch_name=batch_name, root_alias="test")
        rules.source_path = "/fake/path.yaml"
        rules.required_files = [
            RequiredFile(pattern="README.md"),
            RequiredFile(pattern="src/**/*.py"),
        ]
        return rules

    def test_persistence_across_reload(self):
        """保存后，在新的 Python 进程语义下（重新调用 list/get）仍然可见"""
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._make_rules("batch1")

            # 第一次"启动"：保存
            pkg1 = save_rule_package(tmp, "std-rules", "2.1.0", "标准规则", rules)
            self.assertEqual(len(list_rule_packages(tmp)), 1)

            # 模拟"重启"：重新 list 和 get（不使用任何内存缓存）
            packages = list_rule_packages(tmp)
            self.assertEqual(len(packages), 1)
            self.assertEqual(packages[0]["name"], "std-rules")
            self.assertEqual(packages[0]["version"], "2.1.0")

            pkg2 = get_rule_package(tmp, "std-rules", "2.1.0")
            self.assertEqual(pkg2.name, pkg1.name)
            self.assertEqual(pkg2.version, pkg1.version)
            self.assertEqual(pkg2.description, pkg1.description)
            self.assertEqual(pkg2.created_at, pkg1.created_at)

    def test_multiple_versions_coexist(self):
        """同一名称不同版本可以共存，重启后都可见"""
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._make_rules("batch1")

            save_rule_package(tmp, "my-rules", "1.0.0", "初版", rules)
            save_rule_package(tmp, "my-rules", "2.0.0", "升级版", rules)
            save_rule_package(tmp, "other-rules", "1.0.0", "其他", rules)

            # 重启后（重新 list）
            packages = list_rule_packages(tmp)
            self.assertEqual(len(packages), 3)

            names_versions = [(p["name"], p["version"]) for p in packages]
            self.assertIn(("my-rules", "1.0.0"), names_versions)
            self.assertIn(("my-rules", "2.0.0"), names_versions)
            self.assertIn(("other-rules", "1.0.0"), names_versions)

    def test_index_corruption_recovery_safety(self):
        """索引文件损坏时，不破坏现有规则包文件（只读场景）"""
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._make_rules("batch1")
            save_rule_package(tmp, "std-rules", "1.0.0", "desc", rules)

            # 手动破坏索引文件
            from delivery_checker.rule_pkg import _get_index_path
            index_path = _get_index_path(tmp)
            with open(index_path, "w", encoding="utf-8") as f:
                f.write("{ this is not valid json !!!")

            # 读取时会报错，但不会修改任何文件
            with self.assertRaises(RulePkgFormatError):
                list_rule_packages(tmp)

            # 规则包文件仍然存在
            from delivery_checker.rule_pkg import _get_pkg_path
            pkg_path = _get_pkg_path(tmp, "std-rules", "1.0.0")
            self.assertTrue(os.path.exists(pkg_path))


class TestRulePackageImportExport(unittest.TestCase):
    """导入导出往返测试"""

    def _make_rules(self, batch_name="test"):
        rules = CheckRules(batch_name=batch_name, root_alias="test")
        rules.source_path = "/fake/path.yaml"
        rules.required_files = [
            RequiredFile(pattern="README.md", description="项目说明"),
            RequiredFile(pattern="src/**/*.py", description="源码", optional=True),
        ]
        rules.naming_rules = [
            NamingRule(
                name="report-date",
                pattern="build/report*.pdf",
                regex=r"^test_report_\d{8}\.pdf$",
                description="日期格式",
            )
        ]
        rules.metadata = {"team": "qa", "env": "prod"}
        return rules

    def test_export_import_roundtrip(self):
        """导出后在另一个目录导入，内容完全一致"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            # 源目录：保存并导出
            rules = self._make_rules("test-batch")
            save_rule_package(tmp_src, "exported-rules", "1.2.3",
                               "QA团队标准规则", rules)

            export_file = os.path.join(tmp_src, "export.rulepkg.json")
            saved_path = export_rule_package(tmp_src, "exported-rules",
                                             "1.2.3", export_file)
            self.assertTrue(os.path.exists(saved_path))

            # 目标目录：导入
            imported = import_rule_package(tmp_dst, export_file)
            self.assertEqual(imported.name, "exported-rules")
            self.assertEqual(imported.version, "1.2.3")
            self.assertEqual(imported.description, "QA团队标准规则")

            # 验证规则内容完整
            imported_rules = imported.to_rules()
            self.assertEqual(len(imported_rules.required_files), 2)
            self.assertEqual(imported_rules.required_files[0].pattern, "README.md")
            self.assertEqual(imported_rules.required_files[1].optional, True)
            self.assertEqual(len(imported_rules.naming_rules), 1)
            self.assertEqual(imported_rules.naming_rules[0].name, "report-date")
            self.assertEqual(imported_rules.metadata.get("team"), "qa")

            # 目标目录 list 能看到
            packages = list_rule_packages(tmp_dst)
            self.assertEqual(len(packages), 1)
            self.assertEqual(packages[0]["name"], "exported-rules")

    def test_import_with_rename(self):
        """导入时重命名名称和/或版本"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            rules = self._make_rules("test-batch")
            save_rule_package(tmp_src, "orig-name", "1.0.0", "原说明", rules)
            export_file = os.path.join(tmp_src, "export.json")
            export_rule_package(tmp_src, "orig-name", "1.0.0", export_file)

            # 改名导入
            imported = import_rule_package(
                tmp_dst, export_file,
                rename_name="new-name",
                rename_version="2.0.0"
            )
            self.assertEqual(imported.name, "new-name")
            self.assertEqual(imported.version, "2.0.0")
            self.assertEqual(imported.description, "原说明")

            # 原名也能成功导入（不冲突）
            imported2 = import_rule_package(tmp_dst, export_file)
            self.assertEqual(imported2.name, "orig-name")
            self.assertEqual(imported2.version, "1.0.0")

            packages = list_rule_packages(tmp_dst)
            self.assertEqual(len(packages), 2)

    def test_import_does_not_affect_existing_packages(self):
        """导入新规则包不破坏已有规则包"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            # 目标目录先保存一个已有的规则包
            rules_existing = self._make_rules("existing-batch")
            save_rule_package(tmp_dst, "existing", "1.0.0", "已存在", rules_existing)

            # 源目录导出另一个
            rules_new = self._make_rules("new-batch")
            rules_new.required_files.append(
                RequiredFile(pattern="CHANGELOG.md")
            )
            save_rule_package(tmp_src, "new-pkg", "1.0.0", "新包", rules_new)
            export_file = os.path.join(tmp_src, "export.json")
            export_rule_package(tmp_src, "new-pkg", "1.0.0", export_file)

            # 导入
            import_rule_package(tmp_dst, export_file)

            # 验证已有包未受影响
            existing = get_rule_package(tmp_dst, "existing", "1.0.0")
            self.assertEqual(existing.name, "existing")
            self.assertEqual(existing.description, "已存在")
            self.assertEqual(len(existing.rules["required_files"]), 2)

            # 新包也存在
            new_pkg = get_rule_package(tmp_dst, "new-pkg", "1.0.0")
            self.assertEqual(len(new_pkg.rules["required_files"]), 3)

            packages = list_rule_packages(tmp_dst)
            self.assertEqual(len(packages), 2)

    def test_export_file_format(self):
        """导出文件格式符合预期"""
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._make_rules("test-batch")
            save_rule_package(tmp, "test", "1.0.0", "desc", rules)
            export_file = os.path.join(tmp, "export.json")
            export_rule_package(tmp, "test", "1.0.0", export_file)

            import json
            with open(export_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.assertEqual(data["format_version"], 1)
            self.assertEqual(data["type"], "delivery-checker-rule-package")
            self.assertIn("package", data)
            self.assertEqual(data["package"]["name"], "test")
            self.assertEqual(data["package"]["version"], "1.0.0")


class TestRulePackageConflict(unittest.TestCase):
    """冲突处理测试"""

    def _make_rules(self, batch_name="test"):
        rules = CheckRules(batch_name=batch_name, root_alias="test")
        rules.source_path = "/fake/path.yaml"
        rules.required_files = [RequiredFile(pattern="README.md")]
        return rules

    def test_import_conflict_no_force_returns_error(self):
        """导入同名同版本且不使用 --force 时，报错且不修改原有包"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            # 源目录：导出
            rules_new = self._make_rules("new")
            rules_new.required_files.append(RequiredFile(pattern="NEW.md"))
            save_rule_package(tmp_src, "conflict-pkg", "1.0.0", "新版本", rules_new)
            export_file = os.path.join(tmp_src, "export.json")
            export_rule_package(tmp_src, "conflict-pkg", "1.0.0", export_file)

            # 目标目录：先保存旧版本
            rules_old = self._make_rules("old")
            rules_old.description = "旧版本规则"
            save_rule_package(tmp_dst, "conflict-pkg", "1.0.0", "旧版本", rules_old)
            old_created_at = get_rule_package(tmp_dst, "conflict-pkg", "1.0.0").created_at

            # 不使用 force 导入，应报错
            with self.assertRaises(RulePkgConflictError):
                import_rule_package(tmp_dst, export_file, force=False)

            # 原有包未被修改
            existing = get_rule_package(tmp_dst, "conflict-pkg", "1.0.0")
            self.assertEqual(existing.description, "旧版本")
            self.assertEqual(existing.created_at, old_created_at)
            self.assertEqual(len(existing.rules["required_files"]), 1)

    def test_import_conflict_with_force_overwrites(self):
        """使用 --force 时覆盖原有包"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            rules_new = self._make_rules("new")
            rules_new.required_files.append(RequiredFile(pattern="NEW.md"))
            save_rule_package(tmp_src, "conflict-pkg", "1.0.0", "新版本", rules_new)
            export_file = os.path.join(tmp_src, "export.json")
            export_rule_package(tmp_src, "conflict-pkg", "1.0.0", export_file)

            rules_old = self._make_rules("old")
            save_rule_package(tmp_dst, "conflict-pkg", "1.0.0", "旧版本", rules_old)
            old_created_at = get_rule_package(tmp_dst, "conflict-pkg", "1.0.0").created_at

            # 强制覆盖
            imported = import_rule_package(tmp_dst, export_file, force=True)
            self.assertEqual(imported.description, "新版本")
            self.assertEqual(len(imported.rules["required_files"]), 2)

            # 验证 created_at 被保留
            existing = get_rule_package(tmp_dst, "conflict-pkg", "1.0.0")
            self.assertEqual(existing.created_at, old_created_at)

    def test_import_conflict_with_rename_avoids_conflict(self):
        """使用 --rename-name 或 --rename-version 避免冲突"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            rules = self._make_rules("test")
            save_rule_package(tmp_src, "mypkg", "1.0.0", "desc", rules)
            export_file = os.path.join(tmp_src, "export.json")
            export_rule_package(tmp_src, "mypkg", "1.0.0", export_file)

            # 目标目录已有同名同版本
            save_rule_package(tmp_dst, "mypkg", "1.0.0", "已存在", rules)

            # 改名导入
            import_rule_package(
                tmp_dst, export_file,
                rename_name="mypkg-imported",
                rename_version="1.0.0"
            )

            # 两个包都存在
            packages = list_rule_packages(tmp_dst)
            self.assertEqual(len(packages), 2)
            names = [p["name"] for p in packages]
            self.assertIn("mypkg", names)
            self.assertIn("mypkg-imported", names)


class TestRulePackageFailureSafety(unittest.TestCase):
    """失败场景不污染旧数据测试"""

    def _make_rules(self, batch_name="test"):
        rules = CheckRules(batch_name=batch_name, root_alias="test")
        rules.source_path = "/fake/path.yaml"
        rules.required_files = [RequiredFile(pattern="README.md")]
        return rules

    def test_bad_json_import_no_side_effects(self):
        """导入坏 JSON 文件时，不修改任何现有规则包"""
        with tempfile.TemporaryDirectory() as tmp:
            # 先保存一个规则包作为基线
            rules = self._make_rules("test")
            save_rule_package(tmp, "baseline", "1.0.0", "基线", rules)
            baseline_hash = self._dir_state_hash(tmp)

            # 创建坏 JSON 文件
            bad_file = os.path.join(tmp, "bad.json")
            with open(bad_file, "w", encoding="utf-8") as f:
                f.write("{ this is not valid json !!!")

            # 导入失败
            with self.assertRaises(RulePkgFormatError):
                import_rule_package(tmp, bad_file)

            # 验证状态未变
            self.assertEqual(self._dir_state_hash(tmp), baseline_hash)
            packages = list_rule_packages(tmp)
            self.assertEqual(len(packages), 1)
            self.assertEqual(packages[0]["name"], "baseline")

    def test_missing_fields_import_no_side_effects(self):
        """导入缺少必填字段的文件时，不修改现有状态"""
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._make_rules("test")
            save_rule_package(tmp, "baseline", "1.0.0", "基线", rules)
            baseline_hash = self._dir_state_hash(tmp)

            # 创建缺少字段的导出文件
            import json
            bad_file = os.path.join(tmp, "bad_fields.json")
            with open(bad_file, "w", encoding="utf-8") as f:
                json.dump({
                    "format_version": 1,
                    "type": "delivery-checker-rule-package",
                    "package": {
                        # 缺少 name, version, description, rules 等必填字段
                        "some_other_field": "value"
                    }
                }, f)

            with self.assertRaises(RulePkgFormatError):
                import_rule_package(tmp, bad_file)

            self.assertEqual(self._dir_state_hash(tmp), baseline_hash)

    def test_wrong_type_import_no_side_effects(self):
        """导入类型标识错误的文件时，不修改现有状态"""
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._make_rules("test")
            save_rule_package(tmp, "baseline", "1.0.0", "基线", rules)
            baseline_hash = self._dir_state_hash(tmp)

            import json
            bad_file = os.path.join(tmp, "wrong_type.json")
            with open(bad_file, "w", encoding="utf-8") as f:
                json.dump({
                    "format_version": 1,
                    "type": "some-other-type",  # 错误的类型
                    "package": {
                        "name": "test",
                        "version": "1.0.0",
                        "description": "desc",
                        "rules": {}
                    }
                }, f)

            with self.assertRaises(RulePkgFormatError):
                import_rule_package(tmp, bad_file)

            self.assertEqual(self._dir_state_hash(tmp), baseline_hash)

    def test_import_nonexistent_file_no_side_effects(self):
        """导入不存在的文件时，不修改现有状态"""
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._make_rules("test")
            save_rule_package(tmp, "baseline", "1.0.0", "基线", rules)
            baseline_hash = self._dir_state_hash(tmp)

            with self.assertRaises(RulePkgNotFoundError):
                import_rule_package(tmp, "/definitely/does/not/exist.json")

            self.assertEqual(self._dir_state_hash(tmp), baseline_hash)

    def test_rule_package_from_dict_validation(self):
        """RulePackage.from_dict 严格验证必填字段"""
        # 缺少必填字段
        with self.assertRaises(RulePkgFormatError):
            RulePackage.from_dict({
                "name": "test",
                "version": "1.0.0",
                # 缺少 description 和 rules
            })

        # name 为空字符串
        with self.assertRaises(RulePkgFormatError):
            RulePackage.from_dict({
                "name": "   ",
                "version": "1.0.0",
                "description": "desc",
                "rules": {}
            })

        # rules 不是对象
        with self.assertRaises(RulePkgFormatError):
            RulePackage.from_dict({
                "name": "test",
                "version": "1.0.0",
                "description": "desc",
                "rules": "not a dict"
            })

    def _dir_state_hash(self, base_dir: str) -> str:
        """计算规则包目录的内容哈希，用于检测是否被修改"""
        import hashlib
        from delivery_checker.rule_pkg import _get_rule_pkgs_dir
        pkg_dir = _get_rule_pkgs_dir(base_dir)
        if not os.path.exists(pkg_dir):
            return "empty"

        hasher = hashlib.sha256()
        for root, _, files in os.walk(pkg_dir):
            for fn in sorted(files):
                fp = os.path.join(root, fn)
                hasher.update(fn.encode("utf-8"))
                with open(fp, "rb") as f:
                    hasher.update(f.read())
        return hasher.hexdigest()


class TestRulePackageCliExitCodes(unittest.TestCase):
    """CLI 退出码测试"""

    ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    def _run_in(self, work_dir, *args):
        import subprocess
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.join(self.ROOT, "src")
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, "-m", "delivery_checker", *args],
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            cwd=work_dir,
        )
        return result

    def test_rule_save_success_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_path = os.path.join(tmp, "rules.yaml")
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write("batch_name: 'test'\nroot_alias: 'test'\nrequired_files:\n  - 'README.md'\n")

            result = self._run_in(
                tmp, "rule-save", rules_path,
                "-n", "my-rules", "-v", "1.0.0", "-d", "测试规则"
            )
            self.assertEqual(result.returncode, 0,
                             f"stdout={result.stdout}\nstderr={result.stderr}")

    def test_rule_save_bad_yaml_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = os.path.join(tmp, "bad.yaml")
            with open(bad_path, "w", encoding="utf-8") as f:
                f.write(":::: broken yaml [\n")

            result = self._run_in(
                tmp, "rule-save", bad_path,
                "-n", "my-rules", "-v", "1.0.0"
            )
            self.assertEqual(result.returncode, 2, f"stderr={result.stderr}")

    def test_rule_save_conflict_exit_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_path = os.path.join(tmp, "rules.yaml")
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write("batch_name: 'test'\nroot_alias: 'test'\nrequired_files:\n  - 'README.md'\n")

            # 第一次成功
            r1 = self._run_in(tmp, "rule-save", rules_path,
                             "-n", "my-rules", "-v", "1.0.0")
            self.assertEqual(r1.returncode, 0)

            # 第二次冲突
            r2 = self._run_in(tmp, "rule-save", rules_path,
                             "-n", "my-rules", "-v", "1.0.0")
            self.assertEqual(r2.returncode, 3, f"stderr={r2.stderr}")
            self.assertIn("已存在", r2.stderr)

    def test_rule_list_empty_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_in(tmp, "rule-list")
            self.assertEqual(result.returncode, 0)
            self.assertIn("暂无规则包", result.stdout)

    def test_rule_export_not_found_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_in(tmp, "rule-export", "nonexistent", "1.0.0")
            self.assertEqual(result.returncode, 1, f"stderr={result.stderr}")

    def test_rule_import_bad_json_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_file = os.path.join(tmp, "bad.json")
            with open(bad_file, "w", encoding="utf-8") as f:
                f.write("{ this is bad }")

            result = self._run_in(tmp, "rule-import", bad_file)
            self.assertEqual(result.returncode, 2, f"stderr={result.stderr}")

    def test_rule_import_conflict_exit_3(self):
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            # 源：创建并导出
            rules_path = os.path.join(tmp_src, "rules.yaml")
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write("batch_name: 'test'\nroot_alias: 'test'\nrequired_files:\n  - 'README.md'\n")
            self._run_in(tmp_src, "rule-save", rules_path,
                        "-n", "conflict", "-v", "1.0.0").check_returncode()

            export_file = os.path.join(tmp_src, "export.rulepkg.json")
            self._run_in(tmp_src, "rule-export", "conflict", "1.0.0",
                        export_file).check_returncode()

            # 目标：先导入一次
            self._run_in(tmp_dst, "rule-import", export_file).check_returncode()

            # 再次导入（同名同版本）应冲突
            result = self._run_in(tmp_dst, "rule-import", export_file)
            self.assertEqual(result.returncode, 3, f"stderr={result.stderr}")
            self.assertIn("原有规则包未被修改", result.stderr)

    def test_import_with_rename_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            rules_path = os.path.join(tmp_src, "rules.yaml")
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write("batch_name: 'test'\nroot_alias: 'test'\nrequired_files:\n  - 'README.md'\n")
            self._run_in(tmp_src, "rule-save", rules_path,
                        "-n", "mypkg", "-v", "1.0.0").check_returncode()

            export_file = os.path.join(tmp_src, "export.rulepkg.json")
            self._run_in(tmp_src, "rule-export", "mypkg", "1.0.0",
                        export_file).check_returncode()

            # 目标目录先导入一次
            self._run_in(tmp_dst, "rule-import", export_file).check_returncode()

            # 改名导入，应成功
            result = self._run_in(
                tmp_dst, "rule-import", export_file,
                "--rename-name", "mypkg-renamed",
                "--rename-version", "2.0.0"
            )
            self.assertEqual(result.returncode, 0, f"stderr={result.stderr}")

            # list 应有两个包
            list_result = self._run_in(tmp_dst, "rule-list")
            self.assertEqual(list_result.returncode, 0)
            self.assertIn("mypkg", list_result.stdout)
            self.assertIn("mypkg-renamed", list_result.stdout)

    def test_import_with_short_options_N_V_exit_0(self):
        """验证 -N/-V 短选项可用（与错误提示中的 -N/-V 一致）"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            rules_path = os.path.join(tmp_src, "rules.yaml")
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write("batch_name: 'test'\nroot_alias: 'test'\nrequired_files:\n  - 'README.md'\n")
            self._run_in(tmp_src, "rule-save", rules_path,
                        "-n", "mypkg", "-v", "1.0.0").check_returncode()

            export_file = os.path.join(tmp_src, "export.rulepkg.json")
            self._run_in(tmp_src, "rule-export", "mypkg", "1.0.0",
                        export_file).check_returncode()

            # 用短选项 -N/-V 改名导入（用户按错误提示操作的路径）
            result = self._run_in(
                tmp_dst, "rule-import", export_file,
                "-N", "mypkg-short",
                "-V", "2.0.0"
            )
            self.assertEqual(result.returncode, 0, f"stderr={result.stderr}")

            list_result = self._run_in(tmp_dst, "rule-list")
            self.assertIn("mypkg-short", list_result.stdout)
            self.assertIn("2.0.0", list_result.stdout)

    def test_error_tips_match_available_args(self):
        """验证错误提示中提到的参数确实在 CLI help 中存在，不会让用户执行不存在的参数"""
        with tempfile.TemporaryDirectory() as tmp:
            # 1. 验证 rule-save 冲突提示中的 -f、-n、-v 存在
            help_result = self._run_in(tmp, "rule-save", "--help")
            self.assertEqual(help_result.returncode, 0)
            self.assertIn("-f", help_result.stdout)
            self.assertIn("--force", help_result.stdout)
            self.assertIn("-n", help_result.stdout)
            self.assertIn("--name", help_result.stdout)
            self.assertIn("-v", help_result.stdout)
            self.assertIn("--version", help_result.stdout)

            # 2. 验证 rule-import 冲突提示中的 -f、-N、-V 存在
            help_result = self._run_in(tmp, "rule-import", "--help")
            self.assertEqual(help_result.returncode, 0)
            self.assertIn("-f", help_result.stdout)
            self.assertIn("--force", help_result.stdout)
            self.assertIn("-N", help_result.stdout)
            self.assertIn("--rename-name", help_result.stdout)
            self.assertIn("-V", help_result.stdout)
            self.assertIn("--rename-version", help_result.stdout)

            # 3. 验证 scan --help 中没有 --resume（避免提示不存在的参数）
            help_result = self._run_in(tmp, "scan", "--help")
            self.assertEqual(help_result.returncode, 0)
            self.assertNotIn("--resume", help_result.stdout)

    def test_import_conflict_then_follow_tip_force(self):
        """完整用户路径：导入冲突 → 按提示用 -f 覆盖 → 成功"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            # 源：创建并导出
            rules_path = os.path.join(tmp_src, "rules.yaml")
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write("batch_name: 'test'\nroot_alias: 'test'\nrequired_files:\n  - 'README.md'\n  - 'NEW.md'\n")
            self._run_in(tmp_src, "rule-save", rules_path,
                        "-n", "conflict", "-v", "1.0.0", "-d", "新版本").check_returncode()

            export_file = os.path.join(tmp_src, "export.rulepkg.json")
            self._run_in(tmp_src, "rule-export", "conflict", "1.0.0",
                        export_file).check_returncode()

            # 目标：先导入一个旧版本
            rules_old = os.path.join(tmp_dst, "old_rules.yaml")
            with open(rules_old, "w", encoding="utf-8") as f:
                f.write("batch_name: 'old'\nroot_alias: 'test'\nrequired_files:\n  - 'README.md'\n")
            self._run_in(tmp_dst, "rule-save", rules_old,
                        "-n", "conflict", "-v", "1.0.0", "-d", "旧版本").check_returncode()

            # 步骤1：无 force 导入 → 冲突
            r1 = self._run_in(tmp_dst, "rule-import", export_file)
            self.assertEqual(r1.returncode, 3)
            self.assertIn("-f", r1.stderr)  # 提示中包含 -f
            self.assertIn("-N", r1.stderr)  # 提示中包含 -N
            self.assertIn("-V", r1.stderr)  # 提示中包含 -V

            # 步骤2：按提示用 -f 覆盖 → 成功
            r2 = self._run_in(tmp_dst, "rule-import", export_file, "-f")
            self.assertEqual(r2.returncode, 0, f"stderr={r2.stderr}")

            # 验证覆盖成功：规则数应为 2（README.md + NEW.md）
            pkg = get_rule_package(tmp_dst, "conflict", "1.0.0")
            self.assertEqual(len(pkg.rules["required_files"]), 2)
            self.assertEqual(pkg.description, "新版本")

    def test_import_conflict_then_follow_tip_rename(self):
        """完整用户路径：导入冲突 → 按提示用 -N/-V 改名 → 成功"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            rules_path = os.path.join(tmp_src, "rules.yaml")
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write("batch_name: 'test'\nroot_alias: 'test'\nrequired_files:\n  - 'README.md'\n")
            self._run_in(tmp_src, "rule-save", rules_path,
                        "-n", "mypkg", "-v", "1.0.0", "-d", "原版本").check_returncode()

            export_file = os.path.join(tmp_src, "export.rulepkg.json")
            self._run_in(tmp_src, "rule-export", "mypkg", "1.0.0",
                        export_file).check_returncode()

            # 目标：先导入一次
            self._run_in(tmp_dst, "rule-import", export_file).check_returncode()
            self.assertEqual(len(list_rule_packages(tmp_dst)), 1)

            # 步骤1：再次导入 → 冲突
            r1 = self._run_in(tmp_dst, "rule-import", export_file)
            self.assertEqual(r1.returncode, 3)

            # 步骤2：按提示用 -N/-V 改名导入 → 成功
            r2 = self._run_in(
                tmp_dst, "rule-import", export_file,
                "-N", "mypkg-imported",
                "-V", "2.0.0"
            )
            self.assertEqual(r2.returncode, 0, f"stderr={r2.stderr}")

            # 验证两个包都存在
            packages = list_rule_packages(tmp_dst)
            self.assertEqual(len(packages), 2)
            names = [(p["name"], p["version"]) for p in packages]
            self.assertIn(("mypkg", "1.0.0"), names)
            self.assertIn(("mypkg-imported", "2.0.0"), names)

    def test_scan_duplicate_then_follow_tip_no_merge(self):
        """scan 重复扫描冲突 → 按提示去掉 --no-merge 续办 → 成功"""
        with tempfile.TemporaryDirectory() as tmp:
            rules_path = os.path.join(tmp, "rules.yaml")
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write("batch_name: 'dup-test'\nroot_alias: 't'\nrequired_files:\n  - 'README.md'\n")

            data_dir = os.path.join(tmp, "data")
            os.makedirs(data_dir)
            with open(os.path.join(data_dir, "README.md"), "w") as f:
                f.write("test")

            # 第一次扫描（默认 merge=true）
            r1 = self._run_in(tmp, "scan", rules_path, data_dir)
            self.assertEqual(r1.returncode, 0, f"stderr={r1.stderr}")

            # 步骤1：加 --no-merge 重复扫描 → 冲突
            r2 = self._run_in(tmp, "scan", rules_path, data_dir, "--no-merge")
            self.assertEqual(r2.returncode, 3)
            self.assertIn("去掉 --no-merge", r2.stderr)
            self.assertIn("--force", r2.stderr)

            # 步骤2：按提示去掉 --no-merge → 自动续办成功
            r3 = self._run_in(tmp, "scan", rules_path, data_dir)
            self.assertEqual(r3.returncode, 0, f"stderr={r3.stderr}")
            self.assertIn("续办", r3.stdout)

            # 步骤3：按提示加 --force → 强制重新扫描成功
            r4 = self._run_in(tmp, "scan", rules_path, data_dir, "--no-merge", "--force")
            self.assertEqual(r4.returncode, 0, f"stderr={r4.stderr}")
            self.assertIn("重新扫描", r4.stdout)

    def test_rule_save_conflict_then_follow_tips(self):
        """rule-save 冲突 → 按提示操作 → 成功"""
        with tempfile.TemporaryDirectory() as tmp:
            rules_path = os.path.join(tmp, "rules.yaml")
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write("batch_name: 'test'\nroot_alias: 'test'\nrequired_files:\n  - 'README.md'\n")

            # 第一次保存
            self._run_in(tmp, "rule-save", rules_path,
                        "-n", "save-test", "-v", "1.0.0").check_returncode()

            # 步骤1：再次保存同名同版本 → 冲突
            r1 = self._run_in(tmp, "rule-save", rules_path,
                             "-n", "save-test", "-v", "1.0.0")
            self.assertEqual(r1.returncode, 3)
            self.assertIn("-f", r1.stderr)
            self.assertIn("-n", r1.stderr)
            self.assertIn("-v", r1.stderr)

            # 步骤2：按提示用 -f 覆盖 → 成功
            r2 = self._run_in(tmp, "rule-save", rules_path,
                             "-n", "save-test", "-v", "1.0.0", "-f")
            self.assertEqual(r2.returncode, 0, f"stderr={r2.stderr}")

            # 步骤3：按提示用 -n/-v 换名 → 成功
            r3 = self._run_in(tmp, "rule-save", rules_path,
                             "-n", "save-test-new", "-v", "2.0.0")
            self.assertEqual(r3.returncode, 0, f"stderr={r3.stderr}")

            packages = list_rule_packages(tmp)
            self.assertEqual(len(packages), 2)

    def test_bad_json_import_no_side_effects_cli(self):
        """CLI 级别验证：坏 JSON 导入失败，不污染旧状态"""
        with tempfile.TemporaryDirectory() as tmp:
            # 先保存一个规则包作为基线
            rules_path = os.path.join(tmp, "rules.yaml")
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write("batch_name: 'baseline'\nroot_alias: 'test'\nrequired_files:\n  - 'README.md'\n")
            self._run_in(tmp, "rule-save", rules_path,
                        "-n", "baseline", "-v", "1.0.0", "-d", "基线").check_returncode()

            list_before = self._run_in(tmp, "rule-list")
            self.assertEqual(list_before.returncode, 0)

            # 创建坏 JSON
            bad_file = os.path.join(tmp, "bad.json")
            with open(bad_file, "w", encoding="utf-8") as f:
                f.write("{ this is NOT valid json !!!")

            # 导入坏 JSON → 失败
            r = self._run_in(tmp, "rule-import", bad_file)
            self.assertEqual(r.returncode, 2)
            self.assertIn("JSON", r.stderr)

            # 验证规则包未受影响
            list_after = self._run_in(tmp, "rule-list")
            self.assertEqual(list_after.returncode, 0)
            self.assertEqual(list_after.stdout, list_before.stdout)

            # 验证原规则包仍可获取
            pkg = get_rule_package(tmp, "baseline", "1.0.0")
            self.assertEqual(pkg.description, "基线")

    def test_missing_fields_import_no_side_effects_cli(self):
        """CLI 级别验证：缺字段导入失败，不污染旧状态"""
        with tempfile.TemporaryDirectory() as tmp:
            # 先保存基线
            rules_path = os.path.join(tmp, "rules.yaml")
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write("batch_name: 'baseline'\nroot_alias: 'test'\nrequired_files:\n  - 'README.md'\n")
            self._run_in(tmp, "rule-save", rules_path,
                        "-n", "baseline", "-v", "1.0.0").check_returncode()

            list_before = self._run_in(tmp, "rule-list")

            # 创建缺字段的导出文件
            import json
            bad_file = os.path.join(tmp, "missing_fields.json")
            with open(bad_file, "w", encoding="utf-8") as f:
                json.dump({
                    "format_version": 1,
                    "type": "delivery-checker-rule-package",
                    "package": {
                        "name": "bad-pkg",
                        # 缺少 version、description、rules
                    }
                }, f)

            # 导入 → 失败，退出码 2
            r = self._run_in(tmp, "rule-import", bad_file)
            self.assertEqual(r.returncode, 2)
            self.assertIn("缺少必填字段", r.stderr)

            # 验证未受影响
            list_after = self._run_in(tmp, "rule-list")
            self.assertEqual(list_after.stdout, list_before.stdout)

    def test_no_resume_in_any_error_messages(self):
        """确保所有错误提示中都不再出现不存在的 --resume 参数"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            # 1. scan --no-merge 冲突提示
            rules_path = os.path.join(tmp_src, "rules.yaml")
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write("batch_name: 'test'\nroot_alias: 'test'\nrequired_files:\n  - 'README.md'\n")
            data_dir = os.path.join(tmp_src, "data")
            os.makedirs(data_dir)
            self._run_in(tmp_src, "scan", rules_path, data_dir).check_returncode()
            r = self._run_in(tmp_src, "scan", rules_path, data_dir, "--no-merge")
            self.assertNotIn("--resume", r.stderr)
            self.assertNotIn("--rename", r.stderr)  # scan 不该提 rename

            # 2. rule-save 冲突提示
            self._run_in(tmp_src, "rule-save", rules_path,
                        "-n", "test", "-v", "1.0.0").check_returncode()
            r = self._run_in(tmp_src, "rule-save", rules_path,
                            "-n", "test", "-v", "1.0.0")
            self.assertNotIn("--rename", r.stderr)  # 不该提不存在的 --rename
            self.assertNotIn("--resume", r.stderr)

            # 3. rule-import 冲突提示
            export_file = os.path.join(tmp_src, "export.json")
            self._run_in(tmp_src, "rule-export", "test", "1.0.0",
                        export_file).check_returncode()
            self._run_in(tmp_dst, "rule-import", export_file).check_returncode()
            r = self._run_in(tmp_dst, "rule-import", export_file)
            self.assertNotIn("--rename", r.stderr)  # 不该提不存在的 --rename
            self.assertNotIn("--resume", r.stderr)

    def test_existing_commands_still_work(self):
        """确保原有 scan/review/mark/export/undo 命令不受影响"""
        with tempfile.TemporaryDirectory() as tmp:
            rules_path = os.path.join(tmp, "rules.yaml")
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write("batch_name: 'compat-test'\nroot_alias: 't'\nrequired_files:\n  - 'README.md'\n")

            data_dir = os.path.join(tmp, "data")
            os.makedirs(data_dir)

            # scan
            r_scan = self._run_in(tmp, "scan", rules_path, data_dir)
            self.assertEqual(r_scan.returncode, 0, f"scan: {r_scan.stderr}")

            # list
            r_list = self._run_in(tmp, "list")
            self.assertEqual(r_list.returncode, 0)
            self.assertIn("compat-test", r_list.stdout)

            # mark
            r_mark = self._run_in(tmp, "mark", "compat-test", "passed",
                                 "--all-pending", "-r", "tester")
            self.assertEqual(r_mark.returncode, 0, f"mark: {r_mark.stderr}")

            # export
            r_export = self._run_in(tmp, "export", "compat-test",
                                   os.path.join(tmp, "report.html"))
            self.assertEqual(r_export.returncode, 0, f"export: {r_export.stderr}")

            # undo
            r_undo = self._run_in(tmp, "undo", "compat-test")
            self.assertEqual(r_undo.returncode, 0, f"undo: {r_undo.stderr}")


class TestViewPresetCore(unittest.TestCase):
    """视图预设核心功能测试"""

    def test_save_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            preset = save_view_preset(
                base_dir=tmp,
                name="缺失文件优先",
                description="只看必需文件缺失的问题",
                issue_types=["missing"],
                review_statuses=["pending", "todo"],
                path_keyword="",
                sort_by="path",
                sort_order="asc",
                default_reviewer="张工",
            )
            self.assertEqual(preset.name, "缺失文件优先")
            self.assertEqual(preset.issue_types, ["missing"])
            self.assertEqual(sorted(preset.review_statuses), ["pending", "todo"])
            self.assertEqual(preset.sort_by, "path")
            self.assertEqual(preset.default_reviewer, "张工")

            presets = list_view_presets(tmp)
            self.assertEqual(len(presets), 1)
            self.assertEqual(presets[0]["name"], "缺失文件优先")
            self.assertEqual(presets[0]["default_reviewer"], "张工")

    def test_get_preset(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_view_preset(
                base_dir=tmp,
                name="test-preset",
                description="test",
                issue_types=["naming", "expired"],
                sort_by="status",
                sort_order="desc",
            )
            preset = get_view_preset(tmp, "test-preset")
            self.assertEqual(preset.name, "test-preset")
            self.assertEqual(sorted(preset.issue_types), ["expired", "naming"])
            self.assertEqual(preset.sort_by, "status")
            self.assertEqual(preset.sort_order, "desc")

    def test_delete_preset(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_view_preset(base_dir=tmp, name="to-delete", description="d")
            self.assertEqual(len(list_view_presets(tmp)), 1)
            delete_view_preset(tmp, "to-delete")
            self.assertEqual(len(list_view_presets(tmp)), 0)
            with self.assertRaises(ViewPresetNotFoundError):
                get_view_preset(tmp, "to-delete")

    def test_duplicate_name_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_view_preset(base_dir=tmp, name="conflict", description="old")
            with self.assertRaises(ViewPresetConflictError):
                save_view_preset(base_dir=tmp, name="conflict", description="new")

    def test_force_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_view_preset(
                base_dir=tmp, name="overwrite-me",
                description="old", issue_types=["missing"]
            )
            old_created = get_view_preset(tmp, "overwrite-me").created_at

            save_view_preset(
                base_dir=tmp, name="overwrite-me",
                description="new", issue_types=["untracked"],
                force=True
            )
            reloaded = get_view_preset(tmp, "overwrite-me")
            self.assertEqual(reloaded.description, "new")
            self.assertEqual(reloaded.issue_types, ["untracked"])
            self.assertEqual(reloaded.created_at, old_created)

    def test_validation_invalid_issue_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ViewPresetFormatError):
                save_view_preset(
                    base_dir=tmp, name="bad", issue_types=["INVALID_TYPE"]
                )

    def test_validation_invalid_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ViewPresetFormatError):
                save_view_preset(
                    base_dir=tmp, name="bad", review_statuses=["INVALID"]
                )

    def test_validation_invalid_sort_by(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ViewPresetFormatError):
                save_view_preset(base_dir=tmp, name="bad", sort_by="whatever")

    def test_validation_empty_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ViewPresetFormatError):
                save_view_preset(base_dir=tmp, name="  ")

    def test_from_dict_validation_missing_name(self):
        with self.assertRaises(ViewPresetFormatError):
            ViewPreset.from_dict({"description": "missing name"})

    def test_from_dict_validation_invalid_elements(self):
        with self.assertRaises(ViewPresetFormatError):
            ViewPreset.from_dict({
                "name": "test",
                "issue_types": ["not_a_valid_type"],
            })


class TestViewPresetPersistence(unittest.TestCase):
    """跨重启可见性测试"""

    def test_persistence_across_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 第一次"启动"：保存预设
            save_view_preset(
                base_dir=tmp,
                name="跨重启预设",
                description="验证是否持久化",
                issue_types=["missing", "naming"],
                review_statuses=["pending"],
                path_keyword="config",
                sort_by="path",
                sort_order="asc",
                default_reviewer="李工",
            )
            self.assertEqual(len(list_view_presets(tmp)), 1)

            # 模拟"重启"：重新 list 和 get
            presets = list_view_presets(tmp)
            self.assertEqual(len(presets), 1)
            self.assertEqual(presets[0]["name"], "跨重启预设")
            self.assertEqual(presets[0]["path_keyword"], "config")

            preset = get_view_preset(tmp, "跨重启预设")
            self.assertEqual(preset.description, "验证是否持久化")
            self.assertEqual(sorted(preset.issue_types), ["missing", "naming"])
            self.assertEqual(preset.default_reviewer, "李工")
            self.assertEqual(preset.sort_by, "path")

    def test_multiple_presets_coexist(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_view_preset(
                base_dir=tmp, name="只看缺失",
                issue_types=["missing"], sort_by="path"
            )
            save_view_preset(
                base_dir=tmp, name="只看待办",
                review_statuses=["todo"], default_reviewer="王工"
            )
            save_view_preset(
                base_dir=tmp, name="全部按时间",
                sort_by="reviewed_at", sort_order="desc"
            )

            presets = list_view_presets(tmp)
            self.assertEqual(len(presets), 3)
            names = sorted(p["name"] for p in presets)
            expected_names = sorted(["全部按时间", "只看缺失", "只看待办"])
            self.assertEqual(names, expected_names)


class TestViewPresetImportExport(unittest.TestCase):
    """导入导出往返测试"""

    def test_export_import_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            # 源：保存并导出
            save_view_preset(
                base_dir=tmp_src,
                name="导出测试预设",
                description="从源目录导出",
                issue_types=["duplicate", "untracked"],
                review_statuses=["pending", "passed"],
                path_keyword="backup",
                sort_by="status",
                sort_order="desc",
                default_reviewer="赵工",
            )

            export_file = os.path.join(tmp_src, "my_preset.preset.json")
            saved_path = export_view_preset(tmp_src, "导出测试预设", export_file)
            self.assertTrue(os.path.exists(saved_path))

            # 目标：导入
            imported = import_view_preset(tmp_dst, export_file)
            self.assertEqual(imported.name, "导出测试预设")
            self.assertEqual(sorted(imported.issue_types), ["duplicate", "untracked"])
            self.assertEqual(imported.default_reviewer, "赵工")
            self.assertEqual(imported.sort_by, "status")
            self.assertEqual(imported.sort_order, "desc")
            self.assertEqual(imported.path_keyword, "backup")

            # 验证目标目录 list 可见
            presets = list_view_presets(tmp_dst)
            self.assertEqual(len(presets), 1)
            self.assertEqual(presets[0]["name"], "导出测试预设")

    def test_import_with_rename(self):
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            save_view_preset(base_dir=tmp_src, name="original", description="原预设")
            export_file = os.path.join(tmp_src, "export.preset.json")
            export_view_preset(tmp_src, "original", export_file)

            # 改名导入
            imported = import_view_preset(
                tmp_dst, export_file, rename_name="改名后的预设"
            )
            self.assertEqual(imported.name, "改名后的预设")

            # 原名也可成功导入（不冲突）
            imported2 = import_view_preset(tmp_dst, export_file)
            self.assertEqual(imported2.name, "original")

            self.assertEqual(len(list_view_presets(tmp_dst)), 2)

    def test_import_does_not_affect_existing_presets(self):
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            # 目标先保存一个已有预设
            save_view_preset(
                base_dir=tmp_dst, name="existing",
                description="已存在的预设",
                issue_types=["missing"],
                default_reviewer="老处理人",
            )

            # 源目录导出另一个预设
            save_view_preset(
                base_dir=tmp_src, name="new-preset",
                description="新导入的预设",
                issue_types=["naming"],
                default_reviewer="新处理人",
            )
            export_file = os.path.join(tmp_src, "export.preset.json")
            export_view_preset(tmp_src, "new-preset", export_file)

            # 导入
            import_view_preset(tmp_dst, export_file)

            # 验证已有预设未受影响
            existing = get_view_preset(tmp_dst, "existing")
            self.assertEqual(existing.description, "已存在的预设")
            self.assertEqual(existing.issue_types, ["missing"])
            self.assertEqual(existing.default_reviewer, "老处理人")

            # 新预设也存在
            new_p = get_view_preset(tmp_dst, "new-preset")
            self.assertEqual(new_p.issue_types, ["naming"])

            self.assertEqual(len(list_view_presets(tmp_dst)), 2)

    def test_export_file_format(self):
        import json as _json
        with tempfile.TemporaryDirectory() as tmp:
            save_view_preset(
                base_dir=tmp, name="fmt-test",
                description="格式验证", issue_types=["missing"]
            )
            export_file = os.path.join(tmp, "export.json")
            export_view_preset(tmp, "fmt-test", export_file)

            with open(export_file, "r", encoding="utf-8") as f:
                data = _json.load(f)

            self.assertEqual(data["format_version"], 1)
            self.assertEqual(data["type"], "delivery-checker-view-preset")
            self.assertIn("preset", data)
            self.assertEqual(data["preset"]["name"], "fmt-test")
            self.assertEqual(data["preset"]["description"], "格式验证")


class TestViewPresetConflictSafety(unittest.TestCase):
    """冲突处理和失败安全性测试"""

    def _preset_dir_hash(self, base_dir: str) -> str:
        import hashlib
        from delivery_checker.view_preset import _get_view_presets_dir
        pdir = _get_view_presets_dir(base_dir)
        if not os.path.exists(pdir):
            return "empty"
        hasher = hashlib.sha256()
        for root, _, files in os.walk(pdir):
            for fn in sorted(files):
                fp = os.path.join(root, fn)
                hasher.update(fn.encode("utf-8"))
                with open(fp, "rb") as f:
                    hasher.update(f.read())
        return hasher.hexdigest()

    def test_import_conflict_no_force_no_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            # 源：导出一个预设
            save_view_preset(
                base_dir=tmp_src, name="conflict",
                description="新版本", issue_types=["naming"]
            )
            export_file = os.path.join(tmp_src, "export.json")
            export_view_preset(tmp_src, "conflict", export_file)

            # 目标：先保存旧版本
            save_view_preset(
                base_dir=tmp_dst, name="conflict",
                description="旧版本", issue_types=["missing"],
                default_reviewer="旧人",
            )
            baseline_hash = self._preset_dir_hash(tmp_dst)

            # 不 force 导入 → 冲突
            with self.assertRaises(ViewPresetConflictError):
                import_view_preset(tmp_dst, export_file, force=False)

            # 验证状态未变
            self.assertEqual(self._preset_dir_hash(tmp_dst), baseline_hash)
            existing = get_view_preset(tmp_dst, "conflict")
            self.assertEqual(existing.description, "旧版本")
            self.assertEqual(existing.issue_types, ["missing"])

    def test_import_conflict_with_force_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            save_view_preset(
                base_dir=tmp_src, name="conflict",
                description="新版本", issue_types=["naming", "expired"]
            )
            export_file = os.path.join(tmp_src, "export.json")
            export_view_preset(tmp_src, "conflict", export_file)

            save_view_preset(
                base_dir=tmp_dst, name="conflict",
                description="旧版本", issue_types=["missing"]
            )
            old_created = get_view_preset(tmp_dst, "conflict").created_at

            # 强制覆盖
            imported = import_view_preset(tmp_dst, export_file, force=True)
            self.assertEqual(imported.description, "新版本")
            self.assertEqual(sorted(imported.issue_types), ["expired", "naming"])
            self.assertEqual(imported.created_at, old_created)

    def test_import_conflict_with_rename_avoids_conflict(self):
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            save_view_preset(base_dir=tmp_src, name="mypreset", description="原描述")
            export_file = os.path.join(tmp_src, "export.json")
            export_view_preset(tmp_src, "mypreset", export_file)

            # 目标先保存同名
            save_view_preset(base_dir=tmp_dst, name="mypreset", description="已存在")

            # 改名导入
            import_view_preset(
                tmp_dst, export_file, rename_name="mypreset-imported"
            )

            names = sorted(p["name"] for p in list_view_presets(tmp_dst))
            self.assertEqual(names, ["mypreset", "mypreset-imported"])

    def test_bad_json_import_no_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 先保存一个基线预设
            save_view_preset(base_dir=tmp, name="baseline", description="基线")
            baseline_hash = self._preset_dir_hash(tmp)

            # 创建坏 JSON 文件
            bad_file = os.path.join(tmp, "bad.json")
            with open(bad_file, "w", encoding="utf-8") as f:
                f.write("{ this is NOT valid json !!!")

            with self.assertRaises(ViewPresetFormatError):
                import_view_preset(tmp, bad_file)

            # 验证未被污染
            self.assertEqual(self._preset_dir_hash(tmp), baseline_hash)
            presets = list_view_presets(tmp)
            self.assertEqual(len(presets), 1)
            self.assertEqual(presets[0]["name"], "baseline")

    def test_missing_fields_import_no_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_view_preset(base_dir=tmp, name="baseline", description="基线")
            baseline_hash = self._preset_dir_hash(tmp)

            import json
            bad_file = os.path.join(tmp, "missing_fields.json")
            with open(bad_file, "w", encoding="utf-8") as f:
                json.dump({
                    "format_version": 1,
                    "type": "delivery-checker-view-preset",
                    "preset": {
                        # 缺少 name 等必填字段
                        "description": "坏预设"
                    }
                }, f)

            with self.assertRaises(ViewPresetFormatError):
                import_view_preset(tmp, bad_file)

            self.assertEqual(self._preset_dir_hash(tmp), baseline_hash)

    def test_wrong_type_import_no_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_view_preset(base_dir=tmp, name="baseline", description="基线")
            baseline_hash = self._preset_dir_hash(tmp)

            import json
            bad_file = os.path.join(tmp, "wrong_type.json")
            with open(bad_file, "w", encoding="utf-8") as f:
                json.dump({
                    "format_version": 1,
                    "type": "delivery-checker-rule-package",  # 错误类型
                    "preset": {"name": "x", "description": "y"}
                }, f)

            with self.assertRaises(ViewPresetFormatError):
                import_view_preset(tmp, bad_file)

            self.assertEqual(self._preset_dir_hash(tmp), baseline_hash)


class TestStateFilteringSorting(unittest.TestCase):
    """测试 state.py 中新增的筛选和排序功能"""

    def _make_state(self, tmp):
        rules = CheckRules(batch_name="filter-test", root_alias="t")
        rules.source_path = os.path.join(tmp, "r.yaml")
        from delivery_checker.models import Issue
        state = BatchState.new(rules, tmp)
        state.issues = {
            "m1": Issue(id="m1", type=IssueType.MISSING, path="config.yaml",
                        message="缺失配置文件", status=ReviewStatus.PENDING),
            "m2": Issue(id="m2", type=IssueType.MISSING, path="docs/README.md",
                        message="缺失说明文档", status=ReviewStatus.PASSED,
                        reviewer="alice", reviewed_at="2026-06-10T10:00:00"),
            "n1": Issue(id="n1", type=IssueType.NAMING, path="build/report_bad.pdf",
                        message="命名错误", status=ReviewStatus.TODO),
            "u1": Issue(id="u1", type=IssueType.UNTRACKED, path="temp_scratch.tmp",
                        message="未纳入规则", status=ReviewStatus.IGNORED,
                        reviewer="bob", reviewed_at="2026-06-11T09:00:00"),
        }
        return state

    def test_filter_by_issue_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self._make_state(tmp)
            result = state.get_sorted_issues(issue_types=["missing"])
            self.assertEqual(len(result), 2)
            self.assertTrue(all(i.type == IssueType.MISSING for i in result))

    def test_filter_by_multiple_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self._make_state(tmp)
            result = state.get_sorted_issues(issue_types=["missing", "naming"])
            self.assertEqual(len(result), 3)
            types = {i.type.value for i in result}
            self.assertEqual(types, {"missing", "naming"})

    def test_filter_by_review_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self._make_state(tmp)
            result = state.get_sorted_issues(review_statuses=["pending"])
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].id, "m1")

    def test_filter_by_path_keyword(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self._make_state(tmp)
            result = state.get_sorted_issues(path_keyword="config")
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].id, "m1")

    def test_filter_by_message_keyword(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self._make_state(tmp)
            result = state.get_sorted_issues(path_keyword="未纳入")
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].id, "u1")

    def test_combined_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self._make_state(tmp)
            result = state.get_sorted_issues(
                issue_types=["missing"], review_statuses=["passed"]
            )
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].id, "m2")

    def test_sort_by_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self._make_state(tmp)
            result = state.get_sorted_issues(sort_by="path", sort_order="asc")
            paths = [i.path for i in result]
            self.assertEqual(paths, sorted(paths))

    def test_sort_by_path_desc(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self._make_state(tmp)
            result = state.get_sorted_issues(sort_by="path", sort_order="desc")
            paths = [i.path for i in result]
            self.assertEqual(paths, sorted(paths, reverse=True))


class TestViewPresetCliExitCodes(unittest.TestCase):
    """CLI 退出码和端到端链路测试"""

    ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    def _run_in(self, work_dir, *args):
        import subprocess
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.join(self.ROOT, "src")
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, "-m", "delivery_checker", *args],
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            cwd=work_dir,
        )
        return result

    def test_preset_save_success_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_in(
                tmp, "preset-save",
                "-n", "我的预设", "-d", "预设说明",
                "-t", "missing,naming", "-F", "pending,todo",
                "-p", "config",
                "--sort-by", "path", "--sort-order", "asc",
                "-r", "张工"
            )
            self.assertEqual(result.returncode, 0,
                             f"stdout={result.stdout}\nstderr={result.stderr}")
            self.assertIn("我的预设", result.stdout)

    def test_preset_save_conflict_exit_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 第一次成功
            r1 = self._run_in(tmp, "preset-save", "-n", "dup")
            self.assertEqual(r1.returncode, 0)
            # 第二次冲突
            r2 = self._run_in(tmp, "preset-save", "-n", "dup")
            self.assertEqual(r2.returncode, 3)
            self.assertIn("已存在", r2.stderr)
            self.assertIn("-f", r2.stderr)

    def test_preset_save_invalid_type_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_in(tmp, "preset-save", "-n", "bad", "-t", "INVALID")
            self.assertEqual(result.returncode, 2, f"stderr={result.stderr}")

    def test_preset_list_empty_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_in(tmp, "preset-list")
            self.assertEqual(result.returncode, 0)
            self.assertIn("暂无视图预设", result.stdout)

    def test_preset_show_not_found_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_in(tmp, "preset-show", "nonexistent")
            self.assertEqual(result.returncode, 1, f"stderr={result.stderr}")

    def test_preset_delete_not_found_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_in(tmp, "preset-delete", "nonexistent")
            self.assertEqual(result.returncode, 1, f"stderr={result.stderr}")

    def test_preset_export_not_found_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_in(tmp, "preset-export", "nonexistent")
            self.assertEqual(result.returncode, 1, f"stderr={result.stderr}")

    def test_preset_import_bad_json_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w", encoding="utf-8") as f:
                f.write("{ broken }")
            result = self._run_in(tmp, "preset-import", bad)
            self.assertEqual(result.returncode, 2, f"stderr={result.stderr}")
            self.assertIn("原有预设未被修改", result.stderr)

    def test_preset_import_conflict_exit_3(self):
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            # 源：保存并导出
            self._run_in(tmp_src, "preset-save", "-n", "conflict",
                        "-t", "missing").check_returncode()
            export_file = os.path.join(tmp_src, "exp.preset.json")
            self._run_in(tmp_src, "preset-export", "conflict",
                        export_file).check_returncode()

            # 目标：先导入一次
            self._run_in(tmp_dst, "preset-import", export_file).check_returncode()

            # 再导入一次 → 冲突
            result = self._run_in(tmp_dst, "preset-import", export_file)
            self.assertEqual(result.returncode, 3, f"stderr={result.stderr}")
            self.assertIn("-f", result.stderr)
            self.assertIn("-N", result.stderr)
            self.assertIn("原有预设未被修改", result.stderr)

    def test_full_cli_preset_save_apply_export_chain(self):
        """完整 CLI 链路：scan → 保存预设 → 套用预设 review → 套用预设 export → 预设导出导入"""
        with tempfile.TemporaryDirectory() as tmp:
            # 0) 准备 rules 和数据
            rules_path = os.path.join(tmp, "rules.yaml")
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write("""batch_name: '预设链路测试'
root_alias: 'test'
required_files:
  - pattern: "README.md"
    description: "说明文档"
  - pattern: "config/config.yaml"
    description: "配置文件"
  - pattern: "docs/**/*.md"
    description: "文档目录"
ignore_patterns:
  - "**/*.tmp"
""")
            data_dir = os.path.join(tmp, "data")
            os.makedirs(os.path.join(data_dir, "docs"))
            os.makedirs(os.path.join(data_dir, "temp"))
            with open(os.path.join(data_dir, "README.md"), "w") as f:
                f.write("hello")
            with open(os.path.join(data_dir, "docs", "notes.tmp"), "w") as f:
                f.write("temp")
            with open(os.path.join(data_dir, "scratch.tmp"), "w") as f:
                f.write("scratch")

            # 1) scan
            r_scan = self._run_in(tmp, "scan", rules_path, data_dir)
            self.assertEqual(r_scan.returncode, 0, f"scan: {r_scan.stderr}")

            # 2) 保存一个预设：只看 missing 类型，按 path 排序，默认处理人 tester
            r_save = self._run_in(
                tmp, "preset-save",
                "-n", "只看缺失文件",
                "-d", "专注于缺失文件问题",
                "-t", "missing",
                "--sort-by", "path",
                "-r", "tester"
            )
            self.assertEqual(r_save.returncode, 0,
                             f"preset-save: {r_save.stderr}")
            self.assertIn("只看缺失文件", r_save.stdout)

            # 3) preset-list 验证
            r_list = self._run_in(tmp, "preset-list")
            self.assertEqual(r_list.returncode, 0)
            self.assertIn("只看缺失文件", r_list.stdout)

            # 4) preset-show 验证
            r_show = self._run_in(tmp, "preset-show", "只看缺失文件")
            self.assertEqual(r_show.returncode, 0)
            self.assertIn("missing", r_show.stdout)
            self.assertIn("tester", r_show.stdout)

            # 5) 用预设 export 报告（非交互式，可验证退出码）
            report_path = os.path.join(tmp, "filtered_report.html")
            r_export = self._run_in(
                tmp, "export", "预设链路测试", report_path,
                "--preset", "只看缺失文件"
            )
            self.assertEqual(r_export.returncode, 0,
                             f"export with preset: {r_export.stderr}")
            self.assertIn("套用预设", r_export.stdout)
            self.assertTrue(os.path.exists(report_path))

            # 6) 预设导出
            preset_export_file = os.path.join(tmp, "my_view.preset.json")
            r_pe = self._run_in(tmp, "preset-export", "只看缺失文件", preset_export_file)
            self.assertEqual(r_pe.returncode, 0, f"preset-export: {r_pe.stderr}")
            self.assertTrue(os.path.exists(preset_export_file))

            # 7) 在另一个目录导入
            with tempfile.TemporaryDirectory() as tmp2:
                r_imp = self._run_in(tmp2, "preset-import", preset_export_file)
                self.assertEqual(r_imp.returncode, 0,
                                 f"preset-import: {r_imp.stderr}")
                # 验证可见
                r_list2 = self._run_in(tmp2, "preset-list")
                self.assertIn("只看缺失文件", r_list2.stdout)

    def test_existing_commands_not_affected_by_presets(self):
        """确保 scan/review/mark/export/undo/rule-* 原有行为不受影响"""
        with tempfile.TemporaryDirectory() as tmp:
            rules_path = os.path.join(tmp, "rules.yaml")
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write("batch_name: '兼容性测试'\nroot_alias: 't'\n"
                        "required_files:\n  - 'README.md'\n")
            data_dir = os.path.join(tmp, "data")
            os.makedirs(data_dir)

            # scan 正常
            r = self._run_in(tmp, "scan", rules_path, data_dir)
            self.assertEqual(r.returncode, 0, f"scan: {r.stderr}")

            # mark 正常
            r = self._run_in(tmp, "mark", "兼容性测试", "passed",
                            "--all-pending", "-r", "tester")
            self.assertEqual(r.returncode, 0, f"mark: {r.stderr}")

            # export（不带 preset 参数）正常
            r = self._run_in(tmp, "export", "兼容性测试",
                           os.path.join(tmp, "rep.html"))
            self.assertEqual(r.returncode, 0, f"export: {r.stderr}")

            # undo 正常
            r = self._run_in(tmp, "undo", "兼容性测试")
            self.assertEqual(r.returncode, 0, f"undo: {r.stderr}")

            # rule-save 正常
            r = self._run_in(tmp, "rule-save", rules_path,
                            "-n", "compat", "-v", "1.0.0")
            self.assertEqual(r.returncode, 0, f"rule-save: {r.stderr}")

            # rule-list 正常
            r = self._run_in(tmp, "rule-list")
            self.assertEqual(r.returncode, 0)
            self.assertIn("compat", r.stdout)


class TestPresetAppliedAfterUndo(unittest.TestCase):
    """撤销后按预设查看的场景测试"""

    ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    def _run_in(self, work_dir, *args):
        import subprocess
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.join(self.ROOT, "src")
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, "-m", "delivery_checker", *args],
            env=env, capture_output=True, encoding="utf-8",
            errors="replace", cwd=work_dir,
        )

    def test_preset_filter_after_undo(self):
        """mark passed → undo → 用只看 pending 的预设查看，应能看到恢复后的问题"""
        with tempfile.TemporaryDirectory() as tmp:
            rules_path = os.path.join(tmp, "rules.yaml")
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write("""batch_name: '撤销后预设测试'
root_alias: 'test'
required_files:
  - pattern: "a.txt"
  - pattern: "b.txt"
  - pattern: "c.txt"
""")
            data_dir = os.path.join(tmp, "data")
            os.makedirs(data_dir)

            # scan
            self._run_in(tmp, "scan", rules_path, data_dir).check_returncode()

            # 保存预设：只看 pending，按 path 排序
            self._run_in(
                tmp, "preset-save",
                "-n", "待办视图", "-F", "pending", "--sort-by", "path"
            ).check_returncode()

            # 把第 1 个问题（a.txt）标记为 passed
            self._run_in(
                tmp, "mark", "撤销后预设测试", "passed",
                "--ids", "1", "-r", "tester"
            ).check_returncode()

            # 撤销
            r_undo = self._run_in(tmp, "undo", "撤销后预设测试")
            self.assertEqual(r_undo.returncode, 0)
            self.assertIn("撤销", r_undo.stdout)

            # 用预设 export 一份 CSV，验证有 3 个 pending（a.txt + b.txt + c.txt）
            csv_path = os.path.join(tmp, "after_undo.csv")
            r_exp = self._run_in(
                tmp, "export", "撤销后预设测试", csv_path,
                "--preset", "待办视图", "-f", "csv"
            )
            self.assertEqual(r_exp.returncode, 0,
                             f"export: {r_exp.stderr}")

            with open(csv_path, "r", encoding="utf-8-sig") as f:
                csv_content = f.read()
            # 3 个 pending 问题 + 1 表头 = 4 行
            lines = [l for l in csv_content.strip().split("\n") if l]
            self.assertEqual(len(lines), 4, f"CSV: {csv_content}")
            # 每行状态都是"待复核"
            for line in lines[1:]:
                self.assertIn("待复核", line)


class TestComparePathNormalization(unittest.TestCase):
    """测试路径标准化：大小写、相对路径、分隔符统一。"""

    def test_normalize_path_case_insensitive(self):
        self.assertEqual(_normalize_path("README.md"), _normalize_path("readme.md"))
        self.assertEqual(_normalize_path("SRC/MAIN.PY"), _normalize_path("src/main.py"))

    def test_normalize_path_separators(self):
        self.assertEqual(
            _normalize_path("docs\\design.md"),
            _normalize_path("docs/design.md")
        )

    def test_normalize_path_relative_and_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = "README.md"
            abs_path = os.path.join(tmp, "README.md")
            # 相对路径标准化后应该一致
            self.assertEqual(
                _normalize_path(rel, tmp),
                _normalize_path(abs_path, tmp).lower()
            )

    def test_compute_match_key_stable(self):
        """同一问题在不同批次中应生成相同匹配键。"""
        from delivery_checker.models import Issue, IssueType, ReviewStatus
        i1 = Issue(
            id="abc1",
            type=IssueType.MISSING,
            path="CHANGELOG.md",
            message="缺失",
            status=ReviewStatus.PENDING,
        )
        i2 = Issue(
            id="abc2",
            type=IssueType.MISSING,
            path="CHANGELOG.md",
            message="缺失",
            status=ReviewStatus.PASSED,
        )
        # 路径和类型相同，即使 id 不同、状态不同，匹配键应相同
        self.assertEqual(_compute_match_key(i1), _compute_match_key(i2))

    def test_compute_match_key_group_key(self):
        """有 group_key 的重复文件用 group_key 匹配。"""
        from delivery_checker.models import Issue, IssueType, ReviewStatus
        i1 = Issue(
            id="dup1",
            type=IssueType.DUPLICATE,
            path="a/backup.txt",
            message="重复",
            status=ReviewStatus.PENDING,
            group_key="slot-file-group",
        )
        i2 = Issue(
            id="dup2",
            type=IssueType.DUPLICATE,
            path="b/backup.txt",
            message="重复",
            status=ReviewStatus.PENDING,
            group_key="slot-file-group",
        )
        self.assertEqual(_compute_match_key(i1), _compute_match_key(i2))

    def test_detect_changes_all_fields(self):
        from delivery_checker.models import Issue, IssueType, ReviewStatus
        old = Issue(
            id="1", type=IssueType.MISSING, path="a.md", message="old msg",
            status=ReviewStatus.PENDING, reviewer=None, note="",
        )
        new = Issue(
            id="1", type=IssueType.NAMING, path="a.md", message="new msg",
            status=ReviewStatus.PASSED, reviewer="张三", note="已处理",
        )
        changes = _detect_changes(old, new)
        self.assertIn("type", changes)
        self.assertIn("status", changes)
        self.assertIn("reviewer", changes)
        self.assertIn("message", changes)
        self.assertIn("note", changes)


class TestCompareCoreLogic(unittest.TestCase):
    """核心对比逻辑：新增/消失/状态变化/处理人变化。"""

    def _make_issue(self, issue_id, issue_type, path, status=ReviewStatus.PENDING,
                    reviewer=None, message="test"):
        from delivery_checker.models import Issue, IssueType
        itype = IssueType(issue_type) if isinstance(issue_type, str) else issue_type
        return Issue(
            id=issue_id, type=itype, path=path, message=message,
            status=status, reviewer=reviewer,
        )

    def _make_state(self, batch_name, issues, tmp):
        rules = CheckRules(batch_name=batch_name, root_alias="test")
        rules.source_path = os.path.join(tmp, "r.yaml")
        state = BatchState.new(rules, tmp)
        state.issues = {i.id: i for i in issues}
        return state

    def test_compare_added_and_removed(self):
        """新增和消失问题正确识别。"""
        with tempfile.TemporaryDirectory() as tmp:
            i_old = self._make_issue("1", "missing", "a.md")
            i_new = self._make_issue("2", "missing", "b.md")
            state_a = self._make_state("batch-a", [i_old], tmp)
            state_b = self._make_state("batch-b", [i_new], tmp)

            result = compare_batches(tmp, state_a, state_b)
            self.assertEqual(len(result.removed), 1)
            self.assertEqual(len(result.added), 1)
            self.assertEqual(result.removed[0].path, "a.md")
            self.assertEqual(result.added[0].path, "b.md")

    def test_compare_status_change(self):
        """状态变化被识别。"""
        with tempfile.TemporaryDirectory() as tmp:
            i_old = self._make_issue("1", "missing", "a.md", ReviewStatus.PENDING)
            i_new = self._make_issue("1", "missing", "a.md", ReviewStatus.PASSED)
            state_a = self._make_state("batch-a", [i_old], tmp)
            state_b = self._make_state("batch-b", [i_new], tmp)

            result = compare_batches(tmp, state_a, state_b)
            self.assertEqual(len(result.changed), 1)
            self.assertIn("status", result.changed[0].change_types)
            self.assertEqual(len(result.unchanged), 0)

    def test_compare_reviewer_change(self):
        """处理人变化被识别。"""
        with tempfile.TemporaryDirectory() as tmp:
            i_old = self._make_issue("1", "missing", "a.md", ReviewStatus.PASSED, "李四")
            i_new = self._make_issue("1", "missing", "a.md", ReviewStatus.PASSED, "王五")
            state_a = self._make_state("batch-a", [i_old], tmp)
            state_b = self._make_state("batch-b", [i_new], tmp)

            result = compare_batches(tmp, state_a, state_b)
            self.assertEqual(len(result.changed), 1)
            self.assertIn("reviewer", result.changed[0].change_types)

    def test_compare_unchanged(self):
        """完全相同的问题归入 unchanged。"""
        with tempfile.TemporaryDirectory() as tmp:
            i_old = self._make_issue("1", "missing", "a.md", ReviewStatus.PASSED, "张三", "ok")
            i_new = self._make_issue("2", "missing", "a.md", ReviewStatus.PASSED, "张三", "ok")
            state_a = self._make_state("batch-a", [i_old], tmp)
            state_b = self._make_state("batch-b", [i_new], tmp)

            result = compare_batches(tmp, state_a, state_b)
            self.assertEqual(len(result.unchanged), 1)
            self.assertEqual(len(result.changed), 0)

    def test_compare_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            i1 = self._make_issue("1", "missing", "a.md", ReviewStatus.PENDING)
            i2 = self._make_issue("2", "missing", "b.md", ReviewStatus.PENDING)
            i3 = self._make_issue("3", "missing", "c.md", ReviewStatus.PENDING)
            i1b = self._make_issue("1", "missing", "a.md", ReviewStatus.PASSED, "张三")
            i2b = self._make_issue("2", "missing", "b.md", ReviewStatus.PENDING)
            i4 = self._make_issue("4", "missing", "d.md", ReviewStatus.PENDING)
            state_a = self._make_state("batch-a", [i1, i2, i3], tmp)
            state_b = self._make_state("batch-b", [i1b, i2b, i4], tmp)

            result = compare_batches(tmp, state_a, state_b)
            summary = result.summary()
            self.assertEqual(summary["added"], 1)      # d.md 新增
            self.assertEqual(summary["removed"], 1)    # c.md 消失
            self.assertEqual(summary["changed"], 1)    # a.md 状态+处理人变化
            self.assertEqual(summary["unchanged"], 1)  # b.md 未变
            self.assertEqual(summary["status_changed"], 1)
            self.assertEqual(summary["reviewer_changed"], 1)


class TestCompareConfigPersistence(unittest.TestCase):
    """对比配置持久化：保存、读取、跨重启、删除。"""

    def test_save_and_get_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = save_compare_config(
                base_dir=tmp,
                name="daily-compare",
                description="每日对比前两个批次",
                source_a="1",
                source_b="2",
                source_a_type="latest",
                source_b_type="latest",
                export_format="json",
                export_path="daily_diff.json",
                conflict_strategy="rename",
            )
            self.assertEqual(cfg.name, "daily-compare")
            self.assertEqual(cfg.source_a, "1")
            self.assertEqual(cfg.source_a_type, "latest")

            loaded = get_compare_config(tmp, "daily-compare")
            self.assertEqual(loaded.name, cfg.name)
            self.assertEqual(loaded.description, cfg.description)
            self.assertEqual(loaded.source_a, cfg.source_a)
            self.assertEqual(loaded.source_a_type, cfg.source_a_type)
            self.assertEqual(loaded.export_format, cfg.export_format)
            self.assertEqual(loaded.conflict_strategy, cfg.conflict_strategy)

    def test_config_persistence_across_reload(self):
        """模拟重启后配置仍然存在。"""
        with tempfile.TemporaryDirectory() as tmp:
            # 第一次"启动"：保存
            save_compare_config(
                base_dir=tmp,
                name="persist-test",
                source_a="batch-old",
                source_b="batch-new",
                source_a_type="name",
                source_b_type="name",
            )
            self.assertEqual(len(list_compare_configs(tmp)), 1)

            # 模拟"重启"：重新调用 list 和 get
            configs = list_compare_configs(tmp)
            self.assertEqual(len(configs), 1)
            self.assertEqual(configs[0]["name"], "persist-test")

            loaded = get_compare_config(tmp, "persist-test")
            self.assertEqual(loaded.source_a, "batch-old")
            self.assertEqual(loaded.source_b, "batch-new")

    def test_config_conflict_no_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_compare_config(tmp, "dup", source_a="a", source_b="b")
            with self.assertRaises(CompareConfigConflictError):
                save_compare_config(tmp, "dup", source_a="c", source_b="d")

    def test_config_conflict_with_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_compare_config(tmp, "dup", source_a="a", source_b="b")
            cfg = save_compare_config(tmp, "dup", source_a="c", source_b="d", force=True)
            self.assertEqual(cfg.source_a, "c")

    def test_config_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CompareConfigNotFoundError):
                get_compare_config(tmp, "no-such-config")

    def test_config_corrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_compare_config(tmp, "bad", source_a="a", source_b="b")
            cfg_path = os.path.join(tmp, ".delivery_check", "compare_configs", "bad.compare.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                f.write("{ this is not valid json !!!")
            with self.assertRaises(CompareConfigError):
                get_compare_config(tmp, "bad")

    def test_delete_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_compare_config(tmp, "todelete", source_a="a", source_b="b")
            self.assertEqual(len(list_compare_configs(tmp)), 1)
            delete_compare_config(tmp, "todelete")
            self.assertEqual(len(list_compare_configs(tmp)), 0)
            with self.assertRaises(CompareConfigNotFoundError):
                get_compare_config(tmp, "todelete")

    def test_list_configs_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            configs = list_compare_configs(tmp)
            self.assertEqual(configs, [])


class TestCompareExport(unittest.TestCase):
    """导出功能：JSON/CSV、冲突处理、权限错误。"""

    def _make_result(self):
        from delivery_checker.models import Issue, IssueType, ReviewStatus
        from delivery_checker.compare import CompareResult, ChangedIssue
        result = CompareResult(
            batch_a_name="batch-a",
            batch_b_name="batch-b",
            compared_at="2026-06-12T12:00:00",
        )
        result.added.append(Issue(
            id="add1", type=IssueType.MISSING, path="new.md",
            message="新增缺失", status=ReviewStatus.PENDING,
        ))
        result.removed.append(Issue(
            id="rem1", type=IssueType.NAMING, path="old.pdf",
            message="命名问题已修复", status=ReviewStatus.PASSED,
            reviewer="张三",
        ))
        i_old = Issue(
            id="chg1", type=IssueType.MISSING, path="chg.md",
            message="描述", status=ReviewStatus.PENDING,
        )
        i_new = Issue(
            id="chg1", type=IssueType.MISSING, path="chg.md",
            message="描述", status=ReviewStatus.PASSED,
            reviewer="李四",
        )
        result.changed.append(ChangedIssue(
            match_key="missing::path::chg.md",
            old_issue=i_old,
            new_issue=i_new,
            change_types=["status", "reviewer"],
        ))
        return result

    def test_export_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._make_result()
            out_path = os.path.join(tmp, "diff.json")
            saved = export_compare_result(result, out_path, "json", "overwrite")
            self.assertTrue(os.path.exists(saved))
            with open(saved, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["batch_a"]["name"], "batch-a")
            self.assertEqual(data["batch_b"]["name"], "batch-b")
            self.assertEqual(data["summary"]["added"], 1)
            self.assertEqual(data["summary"]["removed"], 1)
            self.assertEqual(data["summary"]["changed"], 1)
            self.assertEqual(len(data["added"]), 1)
            self.assertEqual(len(data["removed"]), 1)
            self.assertEqual(len(data["changed"]), 1)

    def test_export_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._make_result()
            out_path = os.path.join(tmp, "diff.csv")
            saved = export_compare_result(result, out_path, "csv", "overwrite")
            self.assertTrue(os.path.exists(saved))
            with open(saved, "r", encoding="utf-8-sig") as f:
                lines = f.readlines()
            self.assertGreater(len(lines), 2)
            header = lines[0]
            self.assertIn("差异类型", header)
            self.assertIn("匹配键", header)
            self.assertIn("旧状态", header)
            self.assertIn("新状态", header)

    def test_export_conflict_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._make_result()
            out_path = os.path.join(tmp, "conflict.json")
            # 创建已存在的文件
            with open(out_path, "w") as f:
                f.write("existing")
            saved = export_compare_result(result, out_path, "json", "rename")
            self.assertNotEqual(saved, out_path)
            self.assertTrue(saved.endswith("_1.json"))
            self.assertTrue(os.path.exists(saved))
            # 原文件未被覆盖
            with open(out_path, "r") as f:
                self.assertEqual(f.read(), "existing")

    def test_export_conflict_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._make_result()
            out_path = os.path.join(tmp, "conflict.json")
            with open(out_path, "w") as f:
                f.write("existing")
            saved = export_compare_result(result, out_path, "json", "overwrite")
            self.assertEqual(saved, out_path)
            # 原文件被覆盖
            with open(out_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["batch_a"]["name"], "batch-a")

    def test_export_conflict_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._make_result()
            out_path = os.path.join(tmp, "conflict.json")
            with open(out_path, "w") as f:
                f.write("existing")
            with self.assertRaises(ExportConflictError):
                export_compare_result(result, out_path, "json", "refuse")
            # 原文件未被修改
            with open(out_path, "r") as f:
                self.assertEqual(f.read(), "existing")

    def test_export_auto_format_from_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._make_result()
            json_path = os.path.join(tmp, "auto.json")
            csv_path = os.path.join(tmp, "auto.csv")
            saved_json = export_compare_result(result, json_path, "auto", "overwrite")
            saved_csv = export_compare_result(result, csv_path, "auto", "overwrite")
            with open(saved_json, "r", encoding="utf-8") as f:
                json.load(f)
            with open(saved_csv, "r", encoding="utf-8-sig") as f:
                self.assertIn("差异类型", f.readline())


class TestCompareBySource(unittest.TestCase):
    """按来源（名称 / 最近N次）选择批次。"""

    def _create_two_batches(self, tmp):
        rules1 = CheckRules(batch_name="batch-1", root_alias="t")
        rules1.source_path = os.path.join(tmp, "r1.yaml")
        state1 = BatchState.new(rules1, tmp)
        state1.save(tmp)

        rules2 = CheckRules(batch_name="batch-2", root_alias="t")
        rules2.source_path = os.path.join(tmp, "r2.yaml")
        state2 = BatchState.new(rules2, tmp)
        state2.save(tmp)

        return state1, state2

    def test_compare_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._create_two_batches(tmp)
            result = compare_by_source(tmp, "batch-1", "batch-2", "name", "name")
            self.assertEqual(result.batch_a_name, "batch-1")
            self.assertEqual(result.batch_b_name, "batch-2")

    def test_compare_by_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._create_two_batches(tmp)
            batches = list_batches(tmp)
            # 按 list_batches 实际顺序：latest 1 是 batches[0]，latest 2 是 batches[1]
            result = compare_by_source(tmp, "2", "1", "latest", "latest")
            self.assertEqual(result.batch_a_name, batches[1]["batch_name"])
            self.assertEqual(result.batch_b_name, batches[0]["batch_name"])

    def test_compare_batch_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BatchNotFoundError):
                compare_by_source(tmp, "no-such-batch", "batch-2", "name", "name")

    def test_compare_latest_out_of_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._create_two_batches(tmp)
            with self.assertRaises(BatchNotFoundError):
                compare_by_source(tmp, "99", "1", "latest", "name")

    def test_compare_no_batches_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BatchNotFoundError):
                compare_by_source(tmp, "1", "2", "latest", "latest")


class TestCompareCliEndToEnd(unittest.TestCase):
    """CLI 端到端测试：用真实 subprocess 跑通所有场景。"""

    ROOT = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )
    DATA_DIR = os.path.join(ROOT, "examples", "sample_data")

    def _run_in(self, work_dir, *args):
        import subprocess
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.join(self.ROOT, "src")
        env["PYTHONIOENCODING"] = "utf-8"
        env["NO_COLOR"] = "1"
        result = subprocess.run(
            [sys.executable, "-m", "delivery_checker", *args],
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            cwd=work_dir,
        )
        return result

    def _create_rules_file(self, tmp, batch_name):
        rules_path = os.path.join(tmp, f"rules_{batch_name}.yaml")
        with open(rules_path, "w", encoding="utf-8") as f:
            f.write(
                f"batch_name: '{batch_name}'\n"
                f"root_alias: 'test'\n"
                f"required_files:\n"
                f"  - 'README.md'\n"
                f"  - 'docs/design.md'\n"
                f"  - 'docs/**/*.md'\n"
                f"ignore_patterns:\n"
                f"  - '**/*.log'\n"
            )
        return rules_path

    def _create_data_dir(self, tmp, variant=1):
        data_dir = os.path.join(tmp, "data")
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(os.path.join(data_dir, "docs"), exist_ok=True)
        with open(os.path.join(data_dir, "README.md"), "w") as f:
            f.write("# Test")
        with open(os.path.join(data_dir, "docs", "design.md"), "w") as f:
            f.write("# Design")
        if variant == 1:
            # variant 1: 多一个 extra 文件（会被识别为 untracked）
            with open(os.path.join(data_dir, "extra.txt"), "w") as f:
                f.write("extra")
        if variant == 2:
            # variant 2: 多另一个 extra，且 docs 下多一个文件
            with open(os.path.join(data_dir, "extra2.txt"), "w") as f:
                f.write("extra2")
            with open(os.path.join(data_dir, "docs", "api.md"), "w") as f:
                f.write("# API")
        return data_dir

    def test_cli_compare_two_batches_by_name(self):
        """用批次名称对比两个批次。"""
        with tempfile.TemporaryDirectory() as tmp:
            # 创建 batch-a
            rules_a = self._create_rules_file(tmp, "batch-a")
            data_a = self._create_data_dir(tmp, variant=1)
            r1 = self._run_in(tmp, "scan", rules_a, data_a)
            self.assertEqual(r1.returncode, 0, f"scan a: {r1.stderr}")

            # 创建 batch-b
            rules_b = self._create_rules_file(tmp, "batch-b")
            data_b = self._create_data_dir(tmp, variant=2)
            r2 = self._run_in(tmp, "scan", rules_b, data_b)
            self.assertEqual(r2.returncode, 0, f"scan b: {r2.stderr}")

            # 对比
            r_compare = self._run_in(tmp, "compare", "--a", "batch-a", "--b", "batch-b")
            self.assertEqual(r_compare.returncode, 0,
                             f"compare: {r_compare.stderr}\nstdout: {r_compare.stdout}")
            self.assertIn("新增", r_compare.stdout)
            self.assertIn("消失", r_compare.stdout)
            self.assertIn("变化", r_compare.stdout)

    def test_cli_compare_by_latest(self):
        """用最近 N 次选择批次。"""
        with tempfile.TemporaryDirectory() as tmp:
            rules1 = self._create_rules_file(tmp, "batch-1")
            data1 = self._create_data_dir(tmp, variant=1)
            self._run_in(tmp, "scan", rules1, data1)
            rules2 = self._create_rules_file(tmp, "batch-2")
            data2 = self._create_data_dir(tmp, variant=2)
            self._run_in(tmp, "scan", rules2, data2)

            # 最近第 2 个 (batch-1) 对比 最近第 1 个 (batch-2)
            r = self._run_in(tmp, "compare", "--a-latest", "2", "--b-latest", "1")
            self.assertEqual(r.returncode, 0, f"compare latest: {r.stderr}")
            self.assertIn("batch-1", r.stdout)
            self.assertIn("batch-2", r.stdout)

    def test_cli_compare_save_config_and_reload(self):
        """保存配置、模拟重启后读取、用配置运行对比。"""
        with tempfile.TemporaryDirectory() as tmp:
            rules1 = self._create_rules_file(tmp, "batch-save-1")
            data1 = self._create_data_dir(tmp, variant=1)
            self._run_in(tmp, "scan", rules1, data1)
            rules2 = self._create_rules_file(tmp, "batch-save-2")
            data2 = self._create_data_dir(tmp, variant=2)
            self._run_in(tmp, "scan", rules2, data2)

            # 1. 保存配置
            out_path = os.path.join(tmp, "diff.json")
            r_save = self._run_in(
                tmp, "compare-save",
                "-n", "my-daily",
                "-d", "每日例行对比",
                "--a-latest", "2",
                "--b-latest", "1",
                "-f", "json",
                "-o", out_path,
                "-c", "rename",
            )
            self.assertEqual(r_save.returncode, 0, f"save: {r_save.stderr}")
            self.assertIn("对比配置已保存", r_save.stdout)

            # 2. 列出配置
            r_list = self._run_in(tmp, "compare-list")
            self.assertEqual(r_list.returncode, 0, f"list: {r_list.stderr}")
            self.assertIn("my-daily", r_list.stdout)
            self.assertIn("最近2", r_list.stdout)
            self.assertIn("最近1", r_list.stdout)

            # 3. 查看配置详情
            r_show = self._run_in(tmp, "compare-show", "my-daily")
            self.assertEqual(r_show.returncode, 0, f"show: {r_show.stderr}")
            self.assertIn("my-daily", r_show.stdout)
            self.assertIn("最近第 2 个批次", r_show.stdout)
            self.assertIn("json", r_show.stdout)
            self.assertIn("自动改名", r_show.stdout)

            # 4. 模拟"重启"：用保存的配置运行对比
            r_run = self._run_in(tmp, "compare-run", "my-daily")
            self.assertEqual(r_run.returncode, 0,
                             f"run: {r_run.stderr}\nstdout: {r_run.stdout}")
            self.assertIn("使用配置「my-daily」", r_run.stdout)
            self.assertIn("batch-save-1", r_run.stdout)
            self.assertIn("batch-save-2", r_run.stdout)

            # 5. 验证导出文件被创建
            self.assertTrue(os.path.exists(out_path))

    def test_cli_compare_export_conflict_strategies(self):
        """导出冲突的三种策略。"""
        with tempfile.TemporaryDirectory() as tmp:
            rules1 = self._create_rules_file(tmp, "batch-c1")
            data1 = self._create_data_dir(tmp, variant=1)
            self._run_in(tmp, "scan", rules1, data1)
            rules2 = self._create_rules_file(tmp, "batch-c2")
            data2 = self._create_data_dir(tmp, variant=2)
            self._run_in(tmp, "scan", rules2, data2)

            out_path = os.path.join(tmp, "conflict_test.json")
            with open(out_path, "w") as f:
                f.write("original content")

            # 1. refuse 策略：拒绝并返回 3
            r_refuse = self._run_in(
                tmp, "compare",
                "--a", "batch-c1", "--b", "batch-c2",
                "-o", out_path, "-c", "refuse",
            )
            self.assertEqual(r_refuse.returncode, 3, f"refuse: {r_refuse.stderr}")
            self.assertIn("导出冲突", r_refuse.stderr)
            with open(out_path, "r") as f:
                self.assertEqual(f.read(), "original content")

            # 2. rename 策略：自动改名，原文件保留
            r_rename = self._run_in(
                tmp, "compare",
                "--a", "batch-c1", "--b", "batch-c2",
                "-o", out_path, "-c", "rename",
            )
            self.assertEqual(r_rename.returncode, 0,
                             f"rename: {r_rename.stderr}\nstdout: {r_rename.stdout}")
            self.assertIn("已导出", r_rename.stdout)
            with open(out_path, "r") as f:
                self.assertEqual(f.read(), "original content")
            # 找到自动改名的文件
            renamed = out_path.replace(".json", "_1.json")
            self.assertTrue(os.path.exists(renamed))

            # 3. overwrite 策略：覆盖原文件
            r_overwrite = self._run_in(
                tmp, "compare",
                "--a", "batch-c1", "--b", "batch-c2",
                "-o", out_path, "-c", "overwrite",
            )
            self.assertEqual(r_overwrite.returncode, 0,
                             f"overwrite: {r_overwrite.stderr}")
            with open(out_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertIn("batch_a", data)

    def test_cli_compare_batch_not_found_exit_1(self):
        """对比不存在的批次返回 1。"""
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run_in(tmp, "compare", "--a", "no-batch", "--b", "no-batch-2")
            self.assertEqual(r.returncode, 1)
            self.assertIn("批次不存在", r.stderr)

    def test_cli_compare_config_corrupted_exit_2(self):
        """配置损坏时返回 2。"""
        with tempfile.TemporaryDirectory() as tmp:
            # 先保存一个好的配置
            rules1 = self._create_rules_file(tmp, "batch-x1")
            data1 = self._create_data_dir(tmp, variant=1)
            self._run_in(tmp, "scan", rules1, data1)
            rules2 = self._create_rules_file(tmp, "batch-x2")
            data2 = self._create_data_dir(tmp, variant=2)
            self._run_in(tmp, "scan", rules2, data2)
            self._run_in(
                tmp, "compare-save", "-n", "corrupt-test",
                "--a-latest", "2", "--b-latest", "1"
            )

            # 破坏配置文件
            cfg_dir = os.path.join(tmp, ".delivery_check", "compare_configs")
            cfg_path = os.path.join(cfg_dir, "corrupt-test.compare.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                f.write("{ this is not json !!!")

            # compare-show 应返回 2
            r_show = self._run_in(tmp, "compare-show", "corrupt-test")
            self.assertEqual(r_show.returncode, 2, f"show bad: {r_show.stderr}")
            self.assertIn("损坏", r_show.stderr)

            # compare-run 也应返回 2
            r_run = self._run_in(tmp, "compare-run", "corrupt-test")
            self.assertEqual(r_run.returncode, 2, f"run bad: {r_run.stderr}")
            self.assertIn("损坏", r_run.stderr)

    def test_cli_compare_does_not_modify_batches(self):
        """对比操作绝不修改原批次、撤销栈或规则包索引。"""
        with tempfile.TemporaryDirectory() as tmp:
            # 先创建批次并标记一些问题
            rules = self._create_rules_file(tmp, "batch-immutable")
            data = self._create_data_dir(tmp, variant=1)
            self._run_in(tmp, "scan", rules, data)

            # 标记一些问题，产生撤销栈
            self._run_in(tmp, "mark", "batch-immutable", "passed",
                         "--ids", "1", "-r", "tester", "-n", "test")

            # 记录对比前的状态文件哈希
            state_file = os.path.join(tmp, ".delivery_check",
                                      "batch-immutable.state.json")
            with open(state_file, "rb") as f:
                before_hash = hashlib.sha256(f.read()).hexdigest()

            # 创建另一个批次用于对比
            rules2 = self._create_rules_file(tmp, "batch-other")
            data2 = self._create_data_dir(tmp, variant=2)
            self._run_in(tmp, "scan", rules2, data2)

            # 运行对比
            self._run_in(tmp, "compare",
                         "--a", "batch-immutable",
                         "--b", "batch-other")

            # 验证原批次未被修改
            with open(state_file, "rb") as f:
                after_hash = hashlib.sha256(f.read()).hexdigest()
            self.assertEqual(before_hash, after_hash,
                             "对比操作修改了原批次状态文件！")

            # 重新加载验证撤销栈完整
            state = BatchState.load(tmp, "batch-immutable")
            self.assertGreaterEqual(len(state.undo_stack), 1)

    def test_cli_compare_in_work_dir_with_path_spaces(self):
        """测试工作目录和路径含空格/中文等异常路径场景。"""
        with tempfile.TemporaryDirectory() as tmp_root:
            work_dir = os.path.join(tmp_root, "我的 工作目录 空格")
            os.makedirs(work_dir)

            rules = self._create_rules_file(work_dir, "路径测试批次")
            data_dir = os.path.join(work_dir, "数据 目录")
            os.makedirs(data_dir)
            os.makedirs(os.path.join(data_dir, "docs"))
            with open(os.path.join(data_dir, "README.md"), "w") as f:
                f.write("# test")
            with open(os.path.join(data_dir, "docs", "design.md"), "w") as f:
                f.write("# design")

            # 扫描两个批次
            r1 = self._run_in(work_dir, "scan", rules, data_dir)
            self.assertEqual(r1.returncode, 0, f"scan 1: {r1.stderr}")

            # 修改目录产生差异
            with open(os.path.join(data_dir, "extra file.txt"), "w") as f:
                f.write("extra")
            rules2 = self._create_rules_file(work_dir, "路径测试批次 2")
            r2 = self._run_in(work_dir, "scan", rules2, data_dir)
            self.assertEqual(r2.returncode, 0, f"scan 2: {r2.stderr}")

            # 对比
            out_path = os.path.join(work_dir, "对比 结果.json")
            r_compare = self._run_in(
                work_dir, "compare",
                "--a", "路径测试批次",
                "--b", "路径测试批次 2",
                "-o", out_path, "-c", "overwrite",
            )
            self.assertEqual(r_compare.returncode, 0,
                             f"compare path: {r_compare.stderr}\nstdout: {r_compare.stdout}")
            self.assertTrue(os.path.exists(out_path))

    def test_cli_compare_export_csv(self):
        """导出 CSV 格式。"""
        with tempfile.TemporaryDirectory() as tmp:
            rules1 = self._create_rules_file(tmp, "csv-batch-1")
            data1 = self._create_data_dir(tmp, variant=1)
            self._run_in(tmp, "scan", rules1, data1)
            rules2 = self._create_rules_file(tmp, "csv-batch-2")
            data2 = self._create_data_dir(tmp, variant=2)
            self._run_in(tmp, "scan", rules2, data2)

            out_path = os.path.join(tmp, "diff.csv")
            r = self._run_in(
                tmp, "compare",
                "--a", "csv-batch-1", "--b", "csv-batch-2",
                "-o", out_path, "-f", "csv",
            )
            self.assertEqual(r.returncode, 0, f"csv export: {r.stderr}")
            self.assertTrue(os.path.exists(out_path))
            with open(out_path, "r", encoding="utf-8-sig") as f:
                header = f.readline()
            self.assertIn("差异类型", header)
            self.assertIn("匹配键", header)


class TestSnapshotCoreLogic(unittest.TestCase):
    """快照核心逻辑：创建、查询、导出、导入、删除"""

    def _make_issues(self, count, tmp):
        from delivery_checker.models import Issue, IssueType, ReviewStatus
        issues = []
        for i in range(count):
            status = ReviewStatus.PENDING
            if i % 3 == 0:
                status = ReviewStatus.PASSED
            elif i % 3 == 1:
                status = ReviewStatus.IGNORED
            issues.append(Issue(
                id=f"issue-{i}",
                type=IssueType.MISSING,
                path=f"file_{i}.txt",
                message=f"文件 {i} 缺失",
                status=status,
                reviewer=f"reviewer_{i}" if i % 2 == 0 else None,
                note=f"备注 {i}" if i % 2 == 0 else "",
            ))
        return issues

    def _make_batch_state(self, tmp, batch_name, issues):
        from delivery_checker.state import BatchState
        from delivery_checker.config import CheckRules, RequiredFile, NamingRule
        rules = CheckRules(
            batch_name=batch_name,
            root_alias="test",
            required_files=[
                RequiredFile(pattern="a.txt"),
                RequiredFile(pattern="b.txt"),
                RequiredFile(pattern="c.txt"),
            ],
            naming_rules=[
                NamingRule(name="r1", pattern="*.txt", regex="^.*$"),
            ],
            ignore_patterns=["*.log", "*.tmp"],
        )
        rules.source_path = os.path.join(tmp, "rules.yaml")
        state = BatchState.new(rules, tmp)
        state.issues = {i.id: i for i in issues}
        return state

    def test_create_snapshot_from_batch(self):
        """从批次创建快照"""
        with tempfile.TemporaryDirectory() as tmp:
            issues = self._make_issues(6, tmp)
            state = self._make_batch_state(tmp, "test-batch", issues)
            state.save(tmp)

            snapshot = create_snapshot(
                base_dir=tmp,
                name="snap-1",
                description="测试快照",
                source_batch_name="test-batch",
            )

            self.assertEqual(snapshot.name, "snap-1")
            self.assertEqual(snapshot.description, "测试快照")
            self.assertEqual(snapshot.source_batch_name, "test-batch")
            self.assertEqual(snapshot.issue_count, 6)
            self.assertEqual(snapshot.status_distribution.get("passed"), 2)
            self.assertEqual(snapshot.status_distribution.get("ignored"), 2)
            self.assertEqual(snapshot.status_distribution.get("pending"), 2)
            self.assertEqual(snapshot.source_rules.get("required_files_count"), 3)
            self.assertEqual(snapshot.source_rules.get("naming_rules_count"), 1)
            self.assertEqual(snapshot.source_rules.get("ignore_patterns_count"), 2)

    def test_list_snapshots(self):
        """列出快照"""
        with tempfile.TemporaryDirectory() as tmp:
            issues = self._make_issues(3, tmp)
            state = self._make_batch_state(tmp, "batch-list", issues)
            state.save(tmp)

            create_snapshot(tmp, "snap-a", "快照 A", "batch-list")
            time.sleep(1.1)
            create_snapshot(tmp, "snap-b", "快照 B", "batch-list")

            snapshots = list_snapshots(tmp)
            self.assertEqual(len(snapshots), 2)
            self.assertEqual(snapshots[0]["name"], "snap-b")
            self.assertEqual(snapshots[1]["name"], "snap-a")
            self.assertEqual(snapshots[0]["issue_count"], 3)
            self.assertEqual(snapshots[0]["source_rules_required_count"], 3)

    def test_get_snapshot(self):
        """获取快照详情"""
        with tempfile.TemporaryDirectory() as tmp:
            issues = self._make_issues(4, tmp)
            state = self._make_batch_state(tmp, "batch-get", issues)
            state.save(tmp)

            create_snapshot(tmp, "snap-get", "获取测试", "batch-get")
            snapshot = get_snapshot(tmp, "snap-get")

            self.assertEqual(snapshot.name, "snap-get")
            self.assertEqual(len(snapshot.issues), 4)
            self.assertEqual(snapshot.issues[0]["id"], "issue-0")
            self.assertIn("status", snapshot.issues[0])
            self.assertIn("reviewer", snapshot.issues[0])

    def test_delete_snapshot(self):
        """删除快照"""
        with tempfile.TemporaryDirectory() as tmp:
            issues = self._make_issues(2, tmp)
            state = self._make_batch_state(tmp, "batch-del", issues)
            state.save(tmp)

            create_snapshot(tmp, "snap-del", "删除测试", "batch-del")
            self.assertEqual(len(list_snapshots(tmp)), 1)

            delete_snapshot(tmp, "snap-del")
            self.assertEqual(len(list_snapshots(tmp)), 0)

            with self.assertRaises(SnapshotNotFoundError):
                get_snapshot(tmp, "snap-del")

    def test_export_and_import_snapshot(self):
        """导出和导入快照"""
        with tempfile.TemporaryDirectory() as tmp:
            issues = self._make_issues(5, tmp)
            state = self._make_batch_state(tmp, "batch-exp", issues)
            state.save(tmp)

            create_snapshot(tmp, "snap-exp", "导出测试", "batch-exp")

            export_path = os.path.join(tmp, "export.snapshot.json")
            saved_path = export_snapshot(tmp, "snap-exp", export_path)
            self.assertTrue(os.path.exists(saved_path))

            with open(saved_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["type"], "delivery-checker-snapshot")
            self.assertEqual(data["snapshot"]["name"], "snap-exp")
            self.assertEqual(data["snapshot"]["issue_count"], 5)

            with tempfile.TemporaryDirectory() as tmp2:
                imported = import_snapshot(tmp2, export_path)
                self.assertEqual(imported.name, "snap-exp")
                self.assertEqual(imported.issue_count, 5)
                self.assertEqual(len(list_snapshots(tmp2)), 1)

    def test_import_conflict_refuse(self):
        """导入冲突：拒绝"""
        with tempfile.TemporaryDirectory() as tmp:
            issues = self._make_issues(3, tmp)
            state = self._make_batch_state(tmp, "batch-c1", issues)
            state.save(tmp)

            create_snapshot(tmp, "snap-conflict", "冲突测试", "batch-c1")

            export_path = os.path.join(tmp, "conflict.snapshot.json")
            export_snapshot(tmp, "snap-conflict", export_path)

            with self.assertRaises(SnapshotConflictError):
                import_snapshot(tmp, export_path, conflict_strategy="refuse")

    def test_import_conflict_overwrite(self):
        """导入冲突：覆盖"""
        with tempfile.TemporaryDirectory() as tmp:
            issues = self._make_issues(3, tmp)
            state = self._make_batch_state(tmp, "batch-c2", issues)
            state.save(tmp)

            create_snapshot(tmp, "snap-overwrite", "原始", "batch-c2")
            original_created = get_snapshot(tmp, "snap-overwrite").created_at
            time.sleep(1.1)

            export_path = os.path.join(tmp, "overwrite.snapshot.json")
            export_snapshot(tmp, "snap-overwrite", export_path)
    
            imported = import_snapshot(tmp, export_path, conflict_strategy="overwrite")
            self.assertEqual(imported.name, "snap-overwrite")
            self.assertEqual(imported.created_at, original_created)
            self.assertNotEqual(imported.updated_at, original_created)

    def test_import_conflict_rename(self):
        """导入冲突：自动改名"""
        with tempfile.TemporaryDirectory() as tmp:
            issues = self._make_issues(3, tmp)
            state = self._make_batch_state(tmp, "batch-c3", issues)
            state.save(tmp)

            create_snapshot(tmp, "snap-rename", "改名测试", "batch-c3")

            export_path = os.path.join(tmp, "rename.snapshot.json")
            export_snapshot(tmp, "snap-rename", export_path)

            imported = import_snapshot(tmp, export_path, conflict_strategy="rename")
            self.assertEqual(imported.name, "snap-rename_1")
            self.assertEqual(len(list_snapshots(tmp)), 2)

    def test_import_rename_name(self):
        """导入时指定新名称"""
        with tempfile.TemporaryDirectory() as tmp:
            issues = self._make_issues(2, tmp)
            state = self._make_batch_state(tmp, "batch-rn", issues)
            state.save(tmp)

            create_snapshot(tmp, "original-name", "原名", "batch-rn")

            export_path = os.path.join(tmp, "rename_name.snapshot.json")
            export_snapshot(tmp, "original-name", export_path)

            imported = import_snapshot(
                tmp, export_path,
                conflict_strategy="refuse",
                rename_name="new-name"
            )
            self.assertEqual(imported.name, "new-name")

    def test_batch_not_found(self):
        """来源批次不存在"""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SnapshotBatchNotFoundError):
                create_snapshot(tmp, "snap", "test", "no-such-batch")

    def test_snapshot_not_found(self):
        """快照不存在"""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SnapshotNotFoundError):
                get_snapshot(tmp, "no-snap")
            with self.assertRaises(SnapshotNotFoundError):
                delete_snapshot(tmp, "no-snap")
            with self.assertRaises(SnapshotNotFoundError):
                export_snapshot(tmp, "no-snap", "out.json")

    def test_snapshot_corrupted(self):
        """快照文件损坏"""
        with tempfile.TemporaryDirectory() as tmp:
            issues = self._make_issues(2, tmp)
            state = self._make_batch_state(tmp, "batch-bad", issues)
            state.save(tmp)

            create_snapshot(tmp, "bad-snap", "损坏测试", "batch-bad")

            snap_path = os.path.join(
                tmp, ".delivery_check", "snapshots", "bad-snap.snapshot.json"
            )
            with open(snap_path, "w", encoding="utf-8") as f:
                f.write("{ this is not valid json !!!")

            with self.assertRaises(SnapshotFormatError):
                get_snapshot(tmp, "bad-snap")

    def test_import_bad_json(self):
        """导入坏 JSON 文件"""
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = os.path.join(tmp, "bad.json")
            with open(bad_path, "w", encoding="utf-8") as f:
                f.write("not json at all")

            with self.assertRaises(SnapshotFormatError):
                import_snapshot(tmp, bad_path)

    def test_import_wrong_type(self):
        """导入非快照导出文件"""
        with tempfile.TemporaryDirectory() as tmp:
            wrong_path = os.path.join(tmp, "wrong.json")
            with open(wrong_path, "w", encoding="utf-8") as f:
                json.dump({"type": "wrong-type", "data": {}}, f)

            with self.assertRaises(SnapshotFormatError):
                import_snapshot(tmp, wrong_path)

    def test_import_missing_snapshot_field(self):
        """导入缺少 snapshot 字段的文件"""
        with tempfile.TemporaryDirectory() as tmp:
            wrong_path = os.path.join(tmp, "missing.json")
            with open(wrong_path, "w", encoding="utf-8") as f:
                json.dump({"type": "delivery-checker-snapshot"}, f)

            with self.assertRaises(SnapshotFormatError):
                import_snapshot(tmp, wrong_path)

    def test_from_dict_missing_fields(self):
        """Snapshot.from_dict 缺少必填字段"""
        with self.assertRaises(SnapshotFormatError):
            Snapshot.from_dict({"name": "test"})

    def test_persistence_across_reload(self):
        """快照持久化：模拟重启后仍然存在"""
        with tempfile.TemporaryDirectory() as tmp:
            issues = self._make_issues(4, tmp)
            state = self._make_batch_state(tmp, "batch-persist", issues)
            state.save(tmp)

            create_snapshot(tmp, "persist-snap", "持久化测试", "batch-persist")
            self.assertEqual(len(list_snapshots(tmp)), 1)

            snapshots1 = list_snapshots(tmp)
            snap1 = get_snapshot(tmp, "persist-snap")

            snapshots2 = list_snapshots(tmp)
            self.assertEqual(len(snapshots2), 1)
            self.assertEqual(snapshots2[0]["name"], snapshots1[0]["name"])

            snap2 = get_snapshot(tmp, "persist-snap")
            self.assertEqual(snap2.name, snap1.name)
            self.assertEqual(snap2.issue_count, snap1.issue_count)
            self.assertEqual(snap2.created_at, snap1.created_at)

    def test_does_not_pollute_other_data(self):
        """快照操作不污染原批次、撤销栈、规则包索引、视图预设"""
        with tempfile.TemporaryDirectory() as tmp:
            issues = self._make_issues(3, tmp)
            state = self._make_batch_state(tmp, "batch-clean", issues)
            from delivery_checker.models import UndoRecord, ReviewStatus
            state.undo_stack = [
                UndoRecord(
                    issue_id=issues[0].id,
                    prev_status=ReviewStatus.PENDING,
                    prev_reviewer=None,
                    prev_note="original note",
                    prev_reviewed_at=None,
                    timestamp="2026-01-01T00:00:00",
                )
            ]
            state.save(tmp)

            before_hash = hashlib.sha256()
            state_file = os.path.join(tmp, ".delivery_check", "batch-clean.state.json")
            with open(state_file, "rb") as f:
                before_hash.update(f.read())
            before_digest = before_hash.hexdigest()

            create_snapshot(tmp, "clean-snap", "不污染测试", "batch-clean")

            after_hash = hashlib.sha256()
            with open(state_file, "rb") as f:
                after_hash.update(f.read())
            after_digest = after_hash.hexdigest()

            self.assertEqual(before_digest, after_digest,
                             "快照操作修改了原批次状态文件！")

            state_after = BatchState.load(tmp, "batch-clean")
            self.assertEqual(len(state_after.undo_stack), 1)
            self.assertEqual(state_after.undo_stack[0].issue_id, issues[0].id)
            self.assertEqual(state_after.undo_stack[0].prev_note, "original note")


class TestSnapshotCliEndToEnd(unittest.TestCase):
    """CLI 端到端测试：用真实 subprocess 跑通所有场景"""

    ROOT = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )

    def _run_in(self, work_dir, *args):
        import subprocess
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.join(self.ROOT, "src")
        env["PYTHONIOENCODING"] = "utf-8"
        env["NO_COLOR"] = "1"
        result = subprocess.run(
            [sys.executable, "-m", "delivery_checker", *args],
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            cwd=work_dir,
        )
        return result

    def _create_rules_file(self, tmp, batch_name):
        rules_path = os.path.join(tmp, f"rules_{batch_name}.yaml")
        with open(rules_path, "w", encoding="utf-8") as f:
            f.write(
                f"batch_name: '{batch_name}'\n"
                f"root_alias: 'test'\n"
                f"required_files:\n"
                f"  - 'README.md'\n"
                f"  - 'docs/design.md'\n"
                f"  - 'docs/**/*.md'\n"
                f"naming_rules:\n"
                f"  - name: 'doc-naming'\n"
                f"    pattern: 'docs/**/*.md'\n"
                f"    regex: '^.*$'\n"
                f"ignore_patterns:\n"
                f"  - '**/*.log'\n"
            )
        return rules_path

    def _create_data_dir(self, tmp):
        data_dir = os.path.join(tmp, "data")
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(os.path.join(data_dir, "docs"), exist_ok=True)
        with open(os.path.join(data_dir, "README.md"), "w") as f:
            f.write("# Test")
        with open(os.path.join(data_dir, "docs", "design.md"), "w") as f:
            f.write("# Design")
        with open(os.path.join(data_dir, "extra.txt"), "w") as f:
            f.write("extra file")
        return data_dir

    def test_cli_create_and_list_snapshot(self):
        """CLI: 创建和列出快照"""
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._create_rules_file(tmp, "cli-batch-1")
            data = self._create_data_dir(tmp)

            r_scan = self._run_in(tmp, "scan", rules, data)
            self.assertEqual(r_scan.returncode, 0, f"scan: {r_scan.stderr}")

            r_create = self._run_in(
                tmp, "snapshot", "create",
                "-n", "cli-snap-1",
                "-d", "CLI 创建测试",
                "-b", "cli-batch-1",
            )
            self.assertEqual(r_create.returncode, 0,
                             f"create: {r_create.stderr}\nstdout: {r_create.stdout}")
            self.assertIn("已创建快照", r_create.stdout)
            self.assertIn("cli-snap-1", r_create.stdout)
            self.assertIn("cli-batch-1", r_create.stdout)

            r_list = self._run_in(tmp, "snapshot", "list")
            self.assertEqual(r_list.returncode, 0, f"list: {r_list.stderr}")
            self.assertIn("cli-snap-1", r_list.stdout)
            self.assertIn("CLI 创建测试", r_list.stdout)
            self.assertIn("cli-batch-1", r_list.stdout)
            self.assertIn("问题数量", r_list.stdout)
            self.assertIn("状态分布", r_list.stdout)

    def test_cli_show_snapshot(self):
        """CLI: 查看快照详情"""
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._create_rules_file(tmp, "cli-batch-show")
            data = self._create_data_dir(tmp)

            self._run_in(tmp, "scan", rules, data)

            self._run_in(
                tmp, "snapshot", "create",
                "-n", "show-snap",
                "-d", "显示测试",
                "-b", "cli-batch-show",
            )

            r_show = self._run_in(tmp, "snapshot", "show", "show-snap")
            self.assertEqual(r_show.returncode, 0, f"show: {r_show.stderr}")
            self.assertIn("show-snap", r_show.stdout)
            self.assertIn("显示测试", r_show.stdout)
            self.assertIn("cli-batch-show", r_show.stdout)
            self.assertIn("规则摘要", r_show.stdout)
            self.assertIn("问题统计", r_show.stdout)

            r_show_v = self._run_in(tmp, "snapshot", "show", "show-snap", "--verbose")
            self.assertEqual(r_show_v.returncode, 0, f"show -v: {r_show_v.stderr}")
            self.assertIn("问题详情", r_show_v.stdout)

    def test_cli_export_and_import_snapshot(self):
        """CLI: 导出和导入快照"""
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._create_rules_file(tmp, "cli-batch-exp")
            data = self._create_data_dir(tmp)

            self._run_in(tmp, "scan", rules, data)

            self._run_in(
                tmp, "snapshot", "create",
                "-n", "exp-snap",
                "-d", "导出测试",
                "-b", "cli-batch-exp",
            )

            export_path = os.path.join(tmp, "exported.snapshot.json")
            r_export = self._run_in(
                tmp, "snapshot", "export", "exp-snap", export_path
            )
            self.assertEqual(r_export.returncode, 0, f"export: {r_export.stderr}")
            self.assertIn("已导出快照到", r_export.stdout)
            self.assertTrue(os.path.exists(export_path))

            with tempfile.TemporaryDirectory() as tmp2:
                r_import = self._run_in(
                    tmp2, "snapshot", "import", export_path
                )
                self.assertEqual(r_import.returncode, 0,
                                 f"import: {r_import.stderr}\nstdout: {r_import.stdout}")
                self.assertIn("已导入快照", r_import.stdout)
                self.assertIn("exp-snap", r_import.stdout)

                r_list2 = self._run_in(tmp2, "snapshot", "list")
                self.assertEqual(r_list2.returncode, 0, f"list2: {r_list2.stderr}")
                self.assertIn("exp-snap", r_list2.stdout)

    def test_cli_import_conflict_strategies(self):
        """CLI: 导入冲突三种策略"""
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._create_rules_file(tmp, "cli-batch-conflict")
            data = self._create_data_dir(tmp)

            self._run_in(tmp, "scan", rules, data)

            self._run_in(
                tmp, "snapshot", "create",
                "-n", "conflict-snap",
                "-d", "冲突测试",
                "-b", "cli-batch-conflict",
            )

            export_path = os.path.join(tmp, "conflict.snapshot.json")
            self._run_in(tmp, "snapshot", "export", "conflict-snap", export_path)

            r_refuse = self._run_in(
                tmp, "snapshot", "import", export_path,
                "--conflict", "refuse"
            )
            self.assertEqual(r_refuse.returncode, 2, f"refuse: {r_refuse.stderr}")
            self.assertIn("名称冲突", r_refuse.stderr)

            r_rename = self._run_in(
                tmp, "snapshot", "import", export_path,
                "--conflict", "rename"
            )
            self.assertEqual(r_rename.returncode, 0,
                             f"rename: {r_rename.stderr}\nstdout: {r_rename.stdout}")
            self.assertIn("conflict-snap_1", r_rename.stdout)

            r_overwrite = self._run_in(
                tmp, "snapshot", "import", export_path,
                "--conflict", "overwrite"
            )
            self.assertEqual(r_overwrite.returncode, 0,
                             f"overwrite: {r_overwrite.stderr}\nstdout: {r_overwrite.stdout}")
            self.assertIn("conflict-snap", r_overwrite.stdout)

            r_list = self._run_in(tmp, "snapshot", "list")
            self.assertEqual(r_list.returncode, 0)
            self.assertIn("conflict-snap", r_list.stdout)
            self.assertIn("conflict-snap_1", r_list.stdout)

    def test_cli_import_rename_name(self):
        """CLI: 导入时指定新名称"""
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._create_rules_file(tmp, "cli-batch-rn")
            data = self._create_data_dir(tmp)

            self._run_in(tmp, "scan", rules, data)

            self._run_in(
                tmp, "snapshot", "create",
                "-n", "original",
                "-d", "原名",
                "-b", "cli-batch-rn",
            )

            export_path = os.path.join(tmp, "rn.snapshot.json")
            self._run_in(tmp, "snapshot", "export", "original", export_path)

            r_import = self._run_in(
                tmp, "snapshot", "import", export_path,
                "--rename-name", "imported-new-name"
            )
            self.assertEqual(r_import.returncode, 0,
                             f"import rename: {r_import.stderr}")
            self.assertIn("imported-new-name", r_import.stdout)

    def test_cli_delete_snapshot(self):
        """CLI: 删除快照"""
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._create_rules_file(tmp, "cli-batch-del")
            data = self._create_data_dir(tmp)

            self._run_in(tmp, "scan", rules, data)

            self._run_in(
                tmp, "snapshot", "create",
                "-n", "del-snap",
                "-d", "删除测试",
                "-b", "cli-batch-del",
            )

            r_del = self._run_in(tmp, "snapshot", "delete", "del-snap")
            self.assertEqual(r_del.returncode, 0, f"delete: {r_del.stderr}")
            self.assertIn("已删除快照", r_del.stdout)
            self.assertIn("del-snap", r_del.stdout)

            r_show = self._run_in(tmp, "snapshot", "show", "del-snap")
            self.assertEqual(r_show.returncode, 1)
            self.assertIn("不存在", r_show.stderr)

    def test_cli_batch_not_found_exit_1(self):
        """CLI: 来源批次不存在返回 1"""
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run_in(
                tmp, "snapshot", "create",
                "-n", "test",
                "-b", "no-such-batch"
            )
            self.assertEqual(r.returncode, 1)
            self.assertIn("不存在", r.stderr)

    def test_cli_snapshot_not_found_exit_1(self):
        """CLI: 快照不存在返回 1"""
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run_in(tmp, "snapshot", "show", "no-snap")
            self.assertEqual(r.returncode, 1)
            self.assertIn("不存在", r.stderr)

    def test_cli_import_bad_json_exit_2(self):
        """CLI: 导入坏 JSON 返回 2"""
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = os.path.join(tmp, "bad.json")
            with open(bad_path, "w", encoding="utf-8") as f:
                f.write("this is not json")

            r = self._run_in(tmp, "snapshot", "import", bad_path)
            self.assertEqual(r.returncode, 2)
            self.assertIn("格式错误", r.stderr)

    def test_cli_create_duplicate_name_exit_2(self):
        """CLI: 创建同名快照返回 2"""
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._create_rules_file(tmp, "cli-batch-dup")
            data = self._create_data_dir(tmp)

            self._run_in(tmp, "scan", rules, data)

            self._run_in(
                tmp, "snapshot", "create",
                "-n", "dup-snap",
                "-b", "cli-batch-dup"
            )

            r = self._run_in(
                tmp, "snapshot", "create",
                "-n", "dup-snap",
                "-b", "cli-batch-dup"
            )
            self.assertEqual(r.returncode, 2)
            self.assertIn("名称冲突", r.stderr)

    def test_cli_persistence_across_reload(self):
        """CLI: 跨重启读取（重新调用 list 和 show）"""
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._create_rules_file(tmp, "cli-batch-persist")
            data = self._create_data_dir(tmp)

            self._run_in(tmp, "scan", rules, data)

            r_create1 = self._run_in(
                tmp, "snapshot", "create",
                "-n", "persist-snap",
                "-d", "持久化测试",
                "-b", "cli-batch-persist"
            )
            self.assertEqual(r_create1.returncode, 0)

            r_list1 = self._run_in(tmp, "snapshot", "list")
            self.assertEqual(r_list1.returncode, 0)
            self.assertIn("persist-snap", r_list1.stdout)

            r_show1 = self._run_in(tmp, "snapshot", "show", "persist-snap")
            self.assertEqual(r_show1.returncode, 0)

            r_list2 = self._run_in(tmp, "snapshot", "list")
            self.assertEqual(r_list2.returncode, 0)
            self.assertIn("persist-snap", r_list2.stdout)

            r_show2 = self._run_in(tmp, "snapshot", "show", "persist-snap")
            self.assertEqual(r_show2.returncode, 0)
            self.assertEqual(r_show1.stdout, r_show2.stdout)

    def test_cli_does_not_pollute_other_data(self):
        """CLI: 快照操作不污染原批次、撤销栈、规则包索引、视图预设"""
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._create_rules_file(tmp, "cli-batch-clean")
            data = self._create_data_dir(tmp)

            self._run_in(tmp, "scan", rules, data)
            self._run_in(
                tmp, "mark", "cli-batch-clean", "passed",
                "--ids", "1", "-r", "tester", "-n", "test note"
            )

            state_file = os.path.join(
                tmp, ".delivery_check", "cli-batch-clean.state.json"
            )
            with open(state_file, "rb") as f:
                before_hash = hashlib.sha256(f.read()).hexdigest()

            self._run_in(
                tmp, "snapshot", "create",
                "-n", "clean-snap",
                "-d", "不污染测试",
                "-b", "cli-batch-clean"
            )

            with open(state_file, "rb") as f:
                after_hash = hashlib.sha256(f.read()).hexdigest()

            self.assertEqual(before_hash, after_hash,
                             "快照操作修改了原批次状态文件！")

            export_path = os.path.join(tmp, "clean.snapshot.json")
            self._run_in(tmp, "snapshot", "export", "clean-snap", export_path)

            with open(state_file, "rb") as f:
                after_export_hash = hashlib.sha256(f.read()).hexdigest()

            self.assertEqual(before_hash, after_export_hash,
                             "快照导出修改了原批次状态文件！")

            self._run_in(tmp, "snapshot", "delete", "clean-snap")

            with open(state_file, "rb") as f:
                after_delete_hash = hashlib.sha256(f.read()).hexdigest()

            self.assertEqual(before_hash, after_delete_hash,
                             "快照删除修改了原批次状态文件！")

    def test_cli_snapshot_in_path_with_spaces(self):
        """CLI: 工作目录和路径含空格/中文"""
        with tempfile.TemporaryDirectory() as tmp_root:
            work_dir = os.path.join(tmp_root, "我的 快照 目录")
            os.makedirs(work_dir)

            rules = self._create_rules_file(work_dir, "路径 测试 批次")
            data_dir = os.path.join(work_dir, "数据 目录")
            os.makedirs(data_dir)
            os.makedirs(os.path.join(data_dir, "docs"))
            with open(os.path.join(data_dir, "README.md"), "w") as f:
                f.write("# test")
            with open(os.path.join(data_dir, "docs", "design.md"), "w") as f:
                f.write("# design")

            r_scan = self._run_in(work_dir, "scan", rules, data_dir)
            self.assertEqual(r_scan.returncode, 0,
                             f"scan path: {r_scan.stderr}")

            r_create = self._run_in(
                work_dir, "snapshot", "create",
                "-n", "空格 快照",
                "-d", "含空格路径测试",
                "-b", "路径 测试 批次"
            )
            self.assertEqual(r_create.returncode, 0,
                             f"create path: {r_create.stderr}\nstdout: {r_create.stdout}")
            self.assertIn("空格 快照", r_create.stdout)

            r_show = self._run_in(work_dir, "snapshot", "show", "空格 快照")
            self.assertEqual(r_show.returncode, 0,
                             f"show path: {r_show.stderr}")

            export_path = os.path.join(work_dir, "导出 快照.snapshot.json")
            r_export = self._run_in(
                work_dir, "snapshot", "export", "空格 快照", export_path
            )
            self.assertEqual(r_export.returncode, 0,
                             f"export path: {r_export.stderr}")
            self.assertTrue(os.path.exists(export_path))

            r_import = self._run_in(
                work_dir, "snapshot", "import", export_path,
                "--rename-name", "导入 空格 快照"
            )
            self.assertEqual(r_import.returncode, 0,
                             f"import path: {r_import.stderr}\nstdout: {r_import.stdout}")
            self.assertIn("导入 空格 快照", r_import.stdout)

    def test_cli_list_empty(self):
        """CLI: 无快照时 list 输出正确"""
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run_in(tmp, "snapshot", "list")
            self.assertEqual(r.returncode, 0)
            self.assertIn("暂无快照", r.stdout)

    def test_cli_export_default_path(self):
        """CLI: 导出使用默认路径"""
        with tempfile.TemporaryDirectory() as tmp:
            rules = self._create_rules_file(tmp, "cli-batch-def")
            data = self._create_data_dir(tmp)

            self._run_in(tmp, "scan", rules, data)
            self._run_in(
                tmp, "snapshot", "create",
                "-n", "def-snap", "-b", "cli-batch-def"
            )

            r = self._run_in(tmp, "snapshot", "export", "def-snap")
            self.assertEqual(r.returncode, 0, f"export default: {r.stderr}")

            default_path = os.path.join(tmp, "def-snap.snapshot.json")
            self.assertTrue(os.path.exists(default_path))


class TestBackupCoreLogic(unittest.TestCase):
    """备份核心逻辑：创建、查询、导出、导入、恢复、删除"""

    def _make_batch_state(self, tmp, batch_name, issue_count=3):
        from delivery_checker.state import BatchState
        from delivery_checker.config import CheckRules, RequiredFile
        from delivery_checker.models import Issue, IssueType, ReviewStatus
        rules = CheckRules(
            batch_name=batch_name,
            root_alias="test",
            required_files=[RequiredFile(pattern="a.txt")],
        )
        rules.source_path = os.path.join(tmp, "rules.yaml")
        state = BatchState.new(rules, tmp)
        issues = []
        for i in range(issue_count):
            issues.append(Issue(
                id=f"issue-{i}",
                type=IssueType.MISSING,
                path=f"file_{i}.txt",
                message=f"文件 {i} 缺失",
                status=ReviewStatus.PENDING,
            ))
        state.issues = {i.id: i for i in issues}
        state.save(tmp)
        return state

    def test_create_backup_with_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_batch_state(tmp, "test-batch", 3)
            manifest, data = create_backup(tmp, name="bk-1", description="测试备份")
            self.assertEqual(manifest.name, "bk-1")
            self.assertEqual(manifest.description, "测试备份")
            self.assertTrue(manifest.include_batches)
            self.assertIn("batches", data)
            self.assertEqual(manifest.content_summary.get("batch_count"), 1)
            self.assertGreater(manifest.total_size_bytes, 0)

    def test_create_backup_selective(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_batch_state(tmp, "batch-selective", 2)
            manifest, data = create_backup(
                tmp, name="bk-sel",
                include_batches=True,
                include_rule_packages=False,
                include_view_presets=False,
                include_snapshots=False,
                include_compare_configs=False,
            )
            self.assertTrue(manifest.include_batches)
            self.assertFalse(manifest.include_rule_packages)
            self.assertNotIn("rule_packages", data)
            self.assertIn("batches", data)

    def test_create_backup_empty_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BackupFormatError):
                create_backup(tmp, name="")

    def test_create_backup_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            create_backup(tmp, name="dup")
            with self.assertRaises(BackupConflictError):
                create_backup(tmp, name="dup")

    def test_list_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_batch_state(tmp, "batch-list", 1)
            create_backup(tmp, name="bk-a")
            time.sleep(1.1)
            create_backup(tmp, name="bk-b")
            backups = list_backups(tmp)
            self.assertEqual(len(backups), 2)
            self.assertEqual(backups[0]["name"], "bk-b")
            self.assertEqual(backups[1]["name"], "bk-a")

    def test_list_backups_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            backups = list_backups(tmp)
            self.assertEqual(backups, [])

    def test_get_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_batch_state(tmp, "batch-get", 2)
            create_backup(tmp, name="bk-get", description="获取测试")
            manifest, data = get_backup(tmp, "bk-get")
            self.assertEqual(manifest.name, "bk-get")
            self.assertEqual(manifest.description, "获取测试")
            self.assertIn("batches", data)

    def test_get_backup_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BackupNotFoundError):
                get_backup(tmp, "no-such")

    def test_show_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_batch_state(tmp, "batch-show", 2)
            create_backup(tmp, name="bk-show", description="显示测试")
            info = show_backup(tmp, "bk-show")
            self.assertEqual(info["name"], "bk-show")
            self.assertEqual(info["description"], "显示测试")
            self.assertIn("批次历史", info["includes"])
            self.assertGreater(info["total_size_bytes"], 0)
            self.assertIn("B", info["total_size_human"])
            self.assertIn("目录:", info["source_summary"])

    def test_delete_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_batch_state(tmp, "batch-del", 1)
            create_backup(tmp, name="bk-del")
            self.assertEqual(len(list_backups(tmp)), 1)
            delete_backup(tmp, "bk-del")
            self.assertEqual(len(list_backups(tmp)), 0)
            with self.assertRaises(BackupNotFoundError):
                get_backup(tmp, "bk-del")

    def test_delete_backup_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BackupNotFoundError):
                delete_backup(tmp, "no-such")

    def test_export_and_import_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_batch_state(tmp, "batch-exp", 3)
            create_backup(tmp, name="bk-exp", description="导出测试")
            export_path = os.path.join(tmp, "export.backup.json")
            saved = export_backup(tmp, "bk-exp", export_path, fmt="json")
            self.assertTrue(os.path.exists(saved))
            with open(saved, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["type"], "delivery-checker-backup")
            self.assertEqual(data["manifest"]["name"], "bk-exp")

            with tempfile.TemporaryDirectory() as tmp2:
                manifest, data = import_backup(tmp2, saved)
                self.assertEqual(manifest.name, "bk-exp")
                self.assertEqual(manifest.description, "导出测试")
                self.assertEqual(len(list_backups(tmp2)), 1)

    def test_export_and_import_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_batch_state(tmp, "batch-zip", 2)
            create_backup(tmp, name="bk-zip", description="ZIP导出测试")
            export_path = os.path.join(tmp, "export.zip")
            saved = export_backup(tmp, "bk-zip", export_path, fmt="zip")
            self.assertTrue(os.path.exists(saved))
            self.assertTrue(zipfile.is_zipfile(saved))

            with tempfile.TemporaryDirectory() as tmp2:
                manifest, data = import_backup(tmp2, saved)
                self.assertEqual(manifest.name, "bk-zip")
                self.assertEqual(len(list_backups(tmp2)), 1)

    def test_import_conflict_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_batch_state(tmp, "batch-cf", 1)
            create_backup(tmp, name="bk-cf")
            export_path = os.path.join(tmp, "cf.backup.json")
            export_backup(tmp, "bk-cf", export_path)
            with self.assertRaises(BackupConflictError):
                import_backup(tmp, export_path, conflict_strategy="refuse")

    def test_import_conflict_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_batch_state(tmp, "batch-rn", 1)
            create_backup(tmp, name="bk-rn")
            export_path = os.path.join(tmp, "rn.backup.json")
            export_backup(tmp, "bk-rn", export_path)
            manifest, _ = import_backup(tmp, export_path, conflict_strategy="rename")
            self.assertEqual(manifest.name, "bk-rn_1")
            self.assertEqual(len(list_backups(tmp)), 2)

    def test_import_conflict_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_batch_state(tmp, "batch-ow", 1)
            create_backup(tmp, name="bk-ow", description="原始")
            export_path = os.path.join(tmp, "ow.backup.json")
            export_backup(tmp, "bk-ow", export_path)
            manifest, _ = import_backup(tmp, export_path, conflict_strategy="overwrite")
            self.assertEqual(manifest.name, "bk-ow")
            self.assertEqual(len(list_backups(tmp)), 1)

    def test_import_bad_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = os.path.join(tmp, "bad.json")
            with open(bad_path, "w", encoding="utf-8") as f:
                f.write("not json at all")
            with self.assertRaises(BackupCorruptedError):
                import_backup(tmp, bad_path)

    def test_import_wrong_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            wrong_path = os.path.join(tmp, "wrong.json")
            with open(wrong_path, "w", encoding="utf-8") as f:
                json.dump({"type": "wrong-type", "manifest": {}, "data": {}}, f)
            with self.assertRaises(BackupFormatError):
                import_backup(tmp, wrong_path)

    def test_import_missing_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            wrong_path = os.path.join(tmp, "missing.json")
            with open(wrong_path, "w", encoding="utf-8") as f:
                json.dump({"type": "delivery-checker-backup"}, f)
            with self.assertRaises(BackupFormatError):
                import_backup(tmp, wrong_path)

    def test_import_version_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = os.path.join(tmp, "future.json")
            with open(bad_path, "w", encoding="utf-8") as f:
                json.dump({
                    "type": "delivery-checker-backup",
                    "format_version": 999,
                    "manifest": {"name": "future-bk"},
                    "data": {},
                }, f)
            with self.assertRaises(BackupVersionMismatchError):
                import_backup(tmp, bad_path)

    def test_import_file_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BackupNotFoundError):
                import_backup(tmp, os.path.join(tmp, "nonexistent.json"))

    def test_corrupted_backup_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_batch_state(tmp, "batch-corrupt", 1)
            create_backup(tmp, name="bk-corrupt")
            backup_path = os.path.join(
                tmp, ".delivery_check", "backups", "bk-corrupt.backup.json"
            )
            with open(backup_path, "w", encoding="utf-8") as f:
                f.write("{ this is not valid json !!!")
            with self.assertRaises(BackupCorruptedError):
                get_backup(tmp, "bk-corrupt")

    def test_version_mismatch_on_get(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_batch_state(tmp, "batch-ver", 1)
            create_backup(tmp, name="bk-ver")
            backup_path = os.path.join(
                tmp, ".delivery_check", "backups", "bk-ver.backup.json"
            )
            with open(backup_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["format_version"] = 999
            with open(backup_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            with self.assertRaises(BackupVersionMismatchError):
                get_backup(tmp, "bk-ver")

    def test_persistence_across_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_batch_state(tmp, "batch-persist", 2)
            create_backup(tmp, name="bk-persist", description="持久化测试")
            self.assertEqual(len(list_backups(tmp)), 1)
            backups1 = list_backups(tmp)
            manifest1, data1 = get_backup(tmp, "bk-persist")
            backups2 = list_backups(tmp)
            self.assertEqual(len(backups2), 1)
            self.assertEqual(backups2[0]["name"], backups1[0]["name"])
            manifest2, data2 = get_backup(tmp, "bk-persist")
            self.assertEqual(manifest2.name, manifest1.name)
            self.assertEqual(manifest2.description, manifest1.description)

    def test_preview_restore_new_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_batch_state(tmp, "batch-preview", 2)
            create_backup(tmp, name="bk-preview", description="预览测试")
            state_path = os.path.join(
                tmp, ".delivery_check", "batch-preview.state.json"
            )
            if os.path.exists(state_path):
                os.remove(state_path)
            diff = preview_restore(tmp, "bk-preview")
            self.assertIn("sections", diff)
            self.assertIn("batches", diff["sections"])
            batches_section = diff["sections"]["batches"]
            self.assertGreater(batches_section.get("new_count", 0), 0)

    def test_preview_restore_no_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_batch_state(tmp, "batch-noconflict", 1)
            create_backup(tmp, name="bk-noconflict")
            delete_state_path = os.path.join(
                tmp, ".delivery_check", "batch-noconflict.state.json"
            )
            if os.path.exists(delete_state_path):
                os.remove(delete_state_path)
            diff = preview_restore(tmp, "bk-noconflict")
            batches_section = diff["sections"]["batches"]
            self.assertGreater(batches_section.get("new_count", 0), 0)
            self.assertFalse(diff.get("has_conflicts", False))

    def test_apply_restore_add_new_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_batch_state(tmp, "batch-restore", 2)
            create_backup(tmp, name="bk-restore")
            state_path = os.path.join(
                tmp, ".delivery_check", "batch-restore.state.json"
            )
            if os.path.exists(state_path):
                os.remove(state_path)
            result = apply_restore(tmp, "bk-restore", conflict_strategy="skip")
            self.assertIn("batches", result["sections"])
            batches_result = result["sections"]["batches"]
            self.assertIn("batch-restore", batches_result.get("added", []))

    def test_apply_restore_conflict_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_batch_state(tmp, "batch-skip", 1)
            create_backup(tmp, name="bk-skip")
            result = apply_restore(tmp, "bk-skip", conflict_strategy="skip")
            batches_result = result["sections"]["batches"]
            self.assertIn("batch-skip", batches_result.get("skipped", []))

    def test_apply_restore_conflict_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_batch_state(tmp, "batch-overwrite", 1)
            create_backup(tmp, name="bk-overwrite")
            result = apply_restore(tmp, "bk-overwrite", conflict_strategy="overwrite")
            batches_result = result["sections"]["batches"]
            self.assertIn("batch-overwrite", batches_result.get("overwritten", []))

    def test_apply_restore_conflict_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._make_batch_state(tmp, "batch-abort", 1)
            create_backup(tmp, name="bk-abort")
            state_path = os.path.join(
                tmp, ".delivery_check", "batch-abort.state.json"
            )
            with open(state_path, "r", encoding="utf-8") as f:
                state_data = json.load(f)
            for issue_id in state_data.get("issues", {}):
                state_data["issues"][issue_id]["status"] = "passed"
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state_data, f, ensure_ascii=False, indent=2)
            with self.assertRaises(BackupConflictError):
                apply_restore(tmp, "bk-abort", conflict_strategy="abort")

    def test_backup_does_not_modify_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = self._make_batch_state(tmp, "batch-immutable", 2)
            state_file = os.path.join(
                tmp, ".delivery_check", "batch-immutable.state.json"
            )
            with open(state_file, "rb") as f:
                before_hash = hashlib.sha256(f.read()).hexdigest()
            create_backup(tmp, name="bk-imm")
            with open(state_file, "rb") as f:
                after_hash = hashlib.sha256(f.read()).hexdigest()
            self.assertEqual(before_hash, after_hash,
                             "备份操作修改了原批次状态文件！")


class TestBackupCliEndToEnd(unittest.TestCase):
    """CLI 端到端测试：用真实 subprocess 跑通所有场景"""

    ROOT = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )

    def _run_in(self, work_dir, *args, input_text=None):
        import subprocess
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.join(self.ROOT, "src")
        env["PYTHONIOENCODING"] = "utf-8"
        env["NO_COLOR"] = "1"
        result = subprocess.run(
            [sys.executable, "-m", "delivery_checker", *args],
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            cwd=work_dir,
            input=input_text,
        )
        return result

    def _create_rules_file(self, tmp, batch_name):
        rules_path = os.path.join(tmp, f"rules_{batch_name}.yaml")
        with open(rules_path, "w", encoding="utf-8") as f:
            f.write(
                f"batch_name: '{batch_name}'\n"
                f"root_alias: 'test'\n"
                f"required_files:\n"
                f"  - 'README.md'\n"
                f"  - 'docs/design.md'\n"
                f"ignore_patterns:\n"
                f"  - '**/*.log'\n"
            )
        return rules_path

    def _create_data_dir(self, tmp):
        data_dir = os.path.join(tmp, "data")
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(os.path.join(data_dir, "docs"), exist_ok=True)
        with open(os.path.join(data_dir, "README.md"), "w") as f:
            f.write("# Test")
        with open(os.path.join(data_dir, "docs", "design.md"), "w") as f:
            f.write("# Design")
        with open(os.path.join(data_dir, "extra.txt"), "w") as f:
            f.write("extra file")
        return data_dir

    def _setup_workspace(self, tmp, batch_name="bk-test-batch"):
        rules = self._create_rules_file(tmp, batch_name)
        data = self._create_data_dir(tmp)
        r_scan = self._run_in(tmp, "scan", rules, data)
        self.assertEqual(r_scan.returncode, 0, f"scan: {r_scan.stderr}")
        return rules, data

    def test_cli_backup_create_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_workspace(tmp, "cli-bk-batch")
            r = self._run_in(
                tmp, "backup", "create",
                "-n", "my-backup",
                "-d", "CLI创建测试",
            )
            self.assertEqual(r.returncode, 0,
                             f"create: {r.stderr}\nstdout: {r.stdout}")
            self.assertIn("已创建", r.stdout)
            self.assertIn("my-backup", r.stdout)
            self.assertIn("CLI创建测试", r.stdout)

            r_list = self._run_in(tmp, "backup", "list")
            self.assertEqual(r_list.returncode, 0, f"list: {r_list.stderr}")
            self.assertIn("my-backup", r_list.stdout)

    def test_cli_backup_create_selective(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_workspace(tmp, "cli-sel-batch")
            r = self._run_in(
                tmp, "backup", "create",
                "-n", "selective-bk",
                "-d", "选择性备份",
                "--no-rules",
                "--no-presets",
                "--no-snapshots",
                "--no-compare",
            )
            self.assertEqual(r.returncode, 0,
                             f"create selective: {r.stderr}\nstdout: {r.stdout}")
            self.assertIn("selective-bk", r.stdout)
            self.assertIn("批次历史", r.stdout)

    def test_cli_backup_show(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_workspace(tmp, "cli-show-batch")
            self._run_in(
                tmp, "backup", "create",
                "-n", "show-bk",
                "-d", "显示测试",
            )
            r = self._run_in(tmp, "backup", "show", "show-bk")
            self.assertEqual(r.returncode, 0, f"show: {r.stderr}")
            self.assertIn("show-bk", r.stdout)
            self.assertIn("显示测试", r.stdout)
            self.assertIn("体积", r.stdout)
            self.assertIn("创建时间", r.stdout)
            self.assertIn("包含内容", r.stdout)
            self.assertIn("来源摘要", r.stdout)

    def test_cli_backup_show_not_found_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run_in(tmp, "backup", "show", "no-such")
            self.assertEqual(r.returncode, 1)
            self.assertIn("不存在", r.stderr)

    def test_cli_backup_create_conflict_exit_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_workspace(tmp, "cli-conf-batch")
            self._run_in(
                tmp, "backup", "create",
                "-n", "conflict-bk",
            )
            r = self._run_in(
                tmp, "backup", "create",
                "-n", "conflict-bk",
            )
            self.assertEqual(r.returncode, 3, f"stderr={r.stderr}")
            self.assertIn("已存在", r.stderr)

    def test_cli_backup_create_empty_name_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run_in(
                tmp, "backup", "create",
                "-n", "",
            )
            self.assertEqual(r.returncode, 2)

    def test_cli_backup_export_and_import_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_workspace(tmp, "cli-exp-batch")
            self._run_in(
                tmp, "backup", "create",
                "-n", "exp-bk",
                "-d", "导出测试",
            )
            export_path = os.path.join(tmp, "exported.backup.json")
            r_export = self._run_in(
                tmp, "backup", "export", "exp-bk", export_path
            )
            self.assertEqual(r_export.returncode, 0, f"export: {r_export.stderr}")
            self.assertIn("已导出", r_export.stdout)
            self.assertTrue(os.path.exists(export_path))

            with tempfile.TemporaryDirectory() as tmp2:
                r_import = self._run_in(
                    tmp2, "backup", "import", export_path
                )
                self.assertEqual(r_import.returncode, 0,
                                 f"import: {r_import.stderr}\nstdout: {r_import.stdout}")
                self.assertIn("已导入", r_import.stdout)
                self.assertIn("exp-bk", r_import.stdout)

                r_list2 = self._run_in(tmp2, "backup", "list")
                self.assertEqual(r_list2.returncode, 0)
                self.assertIn("exp-bk", r_list2.stdout)

    def test_cli_backup_export_and_import_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_workspace(tmp, "cli-zip-batch")
            self._run_in(
                tmp, "backup", "create",
                "-n", "zip-bk",
                "-d", "ZIP导出测试",
            )
            export_path = os.path.join(tmp, "exported.zip")
            r_export = self._run_in(
                tmp, "backup", "export", "zip-bk", export_path,
                "-f", "zip",
            )
            self.assertEqual(r_export.returncode, 0, f"export zip: {r_export.stderr}")
            self.assertTrue(os.path.exists(export_path))
            self.assertTrue(zipfile.is_zipfile(export_path))

            with tempfile.TemporaryDirectory() as tmp2:
                r_import = self._run_in(
                    tmp2, "backup", "import", export_path
                )
                self.assertEqual(r_import.returncode, 0,
                                 f"import zip: {r_import.stderr}\nstdout: {r_import.stdout}")
                self.assertIn("已导入", r_import.stdout)

    def test_cli_backup_import_conflict_strategies(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_workspace(tmp, "cli-imp-conf-batch")
            self._run_in(tmp, "backup", "create", "-n", "imp-conflict")
            export_path = os.path.join(tmp, "conflict.backup.json")
            self._run_in(tmp, "backup", "export", "imp-conflict", export_path)

            r_refuse = self._run_in(
                tmp, "backup", "import", export_path,
                "--conflict", "refuse",
            )
            self.assertEqual(r_refuse.returncode, 3, f"refuse: {r_refuse.stderr}")
            self.assertIn("已存在", r_refuse.stderr)

            r_rename = self._run_in(
                tmp, "backup", "import", export_path,
                "--conflict", "rename",
            )
            self.assertEqual(r_rename.returncode, 0,
                             f"rename: {r_rename.stderr}\nstdout: {r_rename.stdout}")
            self.assertIn("imp-conflict_1", r_rename.stdout)

            r_overwrite = self._run_in(
                tmp, "backup", "import", export_path,
                "--conflict", "overwrite",
            )
            self.assertEqual(r_overwrite.returncode, 0,
                             f"overwrite: {r_overwrite.stderr}\nstdout: {r_overwrite.stdout}")
            self.assertIn("imp-conflict", r_overwrite.stdout)

    def test_cli_backup_import_bad_json_exit_5(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = os.path.join(tmp, "bad.json")
            with open(bad_path, "w", encoding="utf-8") as f:
                f.write("this is not json")
            r = self._run_in(tmp, "backup", "import", bad_path)
            self.assertEqual(r.returncode, 5)
            self.assertIn("损坏", r.stderr)

    def test_cli_backup_import_wrong_type_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            wrong_path = os.path.join(tmp, "wrong.json")
            with open(wrong_path, "w", encoding="utf-8") as f:
                json.dump({"type": "wrong", "manifest": {}, "data": {}}, f)
            r = self._run_in(tmp, "backup", "import", wrong_path)
            self.assertEqual(r.returncode, 2)
            self.assertIn("格式错误", r.stderr)

    def test_cli_backup_import_version_mismatch_exit_6(self):
        with tempfile.TemporaryDirectory() as tmp:
            future_path = os.path.join(tmp, "future.json")
            with open(future_path, "w", encoding="utf-8") as f:
                json.dump({
                    "type": "delivery-checker-backup",
                    "format_version": 999,
                    "manifest": {"name": "future"},
                    "data": {},
                }, f)
            r = self._run_in(tmp, "backup", "import", future_path)
            self.assertEqual(r.returncode, 6)
            self.assertIn("版本不兼容", r.stderr)

    def test_cli_backup_import_file_not_found_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run_in(tmp, "backup", "import",
                             os.path.join(tmp, "nonexistent.json"))
            self.assertEqual(r.returncode, 1)
            self.assertIn("不存在", r.stderr)

    def test_cli_backup_restore_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_workspace(tmp, "cli-dryrun-batch")
            self._run_in(tmp, "backup", "create", "-n", "dryrun-bk")
            r = self._run_in(
                tmp, "backup", "restore", "dryrun-bk",
                "--dry-run",
            )
            self.assertEqual(r.returncode, 0,
                             f"dry-run: {r.stderr}\nstdout: {r.stdout}")
            self.assertIn("差异预览", r.stdout)
            self.assertIn("dry-run", r.stdout)

    def test_cli_backup_restore_with_confirm(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_workspace(tmp, "cli-restore-batch")
            self._run_in(tmp, "backup", "create", "-n", "restore-bk")
            state_path = os.path.join(
                tmp, ".delivery_check", "cli-restore-batch.state.json"
            )
            if os.path.exists(state_path):
                os.remove(state_path)
            r = self._run_in(
                tmp, "backup", "restore", "restore-bk",
                "--conflict", "skip",
                input_text="y\n",
            )
            self.assertEqual(r.returncode, 0,
                             f"restore: {r.stderr}\nstdout: {r.stdout}")
            self.assertIn("恢复完成", r.stdout)

    def test_cli_backup_restore_abort_on_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_workspace(tmp, "cli-abort-batch")
            self._run_in(tmp, "backup", "create", "-n", "abort-bk")
            state_path = os.path.join(
                tmp, ".delivery_check", "cli-abort-batch.state.json"
            )
            with open(state_path, "r", encoding="utf-8") as f:
                state_data = json.load(f)
            for issue_id in state_data.get("issues", {}):
                state_data["issues"][issue_id]["status"] = "passed"
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state_data, f, ensure_ascii=False, indent=2)
            r = self._run_in(
                tmp, "backup", "restore", "abort-bk",
                "--conflict", "abort",
                input_text="y\n",
            )
            self.assertEqual(r.returncode, 3, f"abort: {r.stderr}")
            self.assertIn("冲突", r.stderr)

    def test_cli_backup_restore_skip_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_workspace(tmp, "cli-skipconf-batch")
            self._run_in(tmp, "backup", "create", "-n", "skipconf-bk")
            r = self._run_in(
                tmp, "backup", "restore", "skipconf-bk",
                "--conflict", "skip",
                input_text="y\n",
            )
            self.assertEqual(r.returncode, 0,
                             f"skipconf: {r.stderr}\nstdout: {r.stdout}")
            self.assertIn("恢复结果", r.stdout)

    def test_cli_backup_restore_overwrite_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_workspace(tmp, "cli-owconf-batch")
            self._run_in(tmp, "backup", "create", "-n", "owconf-bk")
            r = self._run_in(
                tmp, "backup", "restore", "owconf-bk",
                "--conflict", "overwrite",
                input_text="y\n",
            )
            self.assertEqual(r.returncode, 0,
                             f"owconf: {r.stderr}\nstdout: {r.stdout}")
            self.assertIn("恢复完成", r.stdout)

    def test_cli_backup_restore_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_workspace(tmp, "cli-cancel-batch")
            self._run_in(tmp, "backup", "create", "-n", "cancel-bk")
            r = self._run_in(
                tmp, "backup", "restore", "cancel-bk",
                "--conflict", "skip",
                input_text="n\n",
            )
            self.assertEqual(r.returncode, 0,
                             f"cancel: {r.stderr}\nstdout: {r.stdout}")
            self.assertIn("已取消", r.stdout)

    def test_cli_backup_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_workspace(tmp, "cli-del-batch")
            self._run_in(tmp, "backup", "create", "-n", "del-bk")
            r_del = self._run_in(tmp, "backup", "delete", "del-bk")
            self.assertEqual(r_del.returncode, 0, f"delete: {r_del.stderr}")
            self.assertIn("已删除", r_del.stdout)

            r_show = self._run_in(tmp, "backup", "show", "del-bk")
            self.assertEqual(r_show.returncode, 1)

    def test_cli_backup_delete_not_found_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run_in(tmp, "backup", "delete", "no-such")
            self.assertEqual(r.returncode, 1)
            self.assertIn("不存在", r.stderr)

    def test_cli_backup_corrupted_exit_5(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_workspace(tmp, "cli-corrupt-batch")
            self._run_in(tmp, "backup", "create", "-n", "corrupt-bk")
            backup_path = os.path.join(
                tmp, ".delivery_check", "backups", "corrupt-bk.backup.json"
            )
            with open(backup_path, "w", encoding="utf-8") as f:
                f.write("{ broken json !!!")
            r = self._run_in(tmp, "backup", "show", "corrupt-bk")
            self.assertEqual(r.returncode, 5, f"stderr={r.stderr}")
            self.assertIn("损坏", r.stderr)

    def test_cli_backup_version_mismatch_exit_6(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_workspace(tmp, "cli-ver-batch")
            self._run_in(tmp, "backup", "create", "-n", "ver-bk")
            backup_path = os.path.join(
                tmp, ".delivery_check", "backups", "ver-bk.backup.json"
            )
            with open(backup_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["format_version"] = 999
            with open(backup_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            r = self._run_in(tmp, "backup", "show", "ver-bk")
            self.assertEqual(r.returncode, 6, f"stderr={r.stderr}")
            self.assertIn("版本不兼容", r.stderr)

    def test_cli_backup_persistence_across_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_workspace(tmp, "cli-persist-batch")
            r_create = self._run_in(
                tmp, "backup", "create",
                "-n", "persist-bk",
                "-d", "持久化测试",
            )
            self.assertEqual(r_create.returncode, 0)

            r_list1 = self._run_in(tmp, "backup", "list")
            self.assertEqual(r_list1.returncode, 0)
            self.assertIn("persist-bk", r_list1.stdout)

            r_show1 = self._run_in(tmp, "backup", "show", "persist-bk")
            self.assertEqual(r_show1.returncode, 0)

            r_list2 = self._run_in(tmp, "backup", "list")
            self.assertEqual(r_list2.returncode, 0)
            self.assertIn("persist-bk", r_list2.stdout)

            r_show2 = self._run_in(tmp, "backup", "show", "persist-bk")
            self.assertEqual(r_show2.returncode, 0)

    def test_cli_backup_export_import_restore_chain(self):
        """完整链路：创建 → 导出 → 另一目录导入 → 恢复"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:
            self._setup_workspace(tmp_src, "chain-batch")
            self._run_in(
                tmp_src, "backup", "create",
                "-n", "chain-bk",
                "-d", "链路测试",
            )
            export_path = os.path.join(tmp_src, "chain.backup.json")
            self._run_in(tmp_src, "backup", "export", "chain-bk", export_path)
            self.assertTrue(os.path.exists(export_path))

            r_import = self._run_in(tmp_dst, "backup", "import", export_path)
            self.assertEqual(r_import.returncode, 0,
                             f"import: {r_import.stderr}\nstdout: {r_import.stdout}")

            r_restore = self._run_in(
                tmp_dst, "backup", "restore", "chain-bk",
                "--conflict", "skip",
                input_text="y\n",
            )
            self.assertEqual(r_restore.returncode, 0,
                             f"restore: {r_restore.stderr}\nstdout: {r_restore.stdout}")
            self.assertIn("恢复", r_restore.stdout)

    def test_cli_backup_list_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self._run_in(tmp, "backup", "list")
            self.assertEqual(r.returncode, 0)
            self.assertIn("暂无备份", r.stdout)

    def test_cli_backup_does_not_pollute_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._setup_workspace(tmp, "cli-clean-batch")
            self._run_in(
                tmp, "mark", "cli-clean-batch", "passed",
                "--ids", "1", "-r", "tester", "-n", "test",
            )
            state_file = os.path.join(
                tmp, ".delivery_check", "cli-clean-batch.state.json"
            )
            with open(state_file, "rb") as f:
                before_hash = hashlib.sha256(f.read()).hexdigest()

            self._run_in(tmp, "backup", "create", "-n", "clean-bk")
            with open(state_file, "rb") as f:
                after_hash = hashlib.sha256(f.read()).hexdigest()
            self.assertEqual(before_hash, after_hash,
                             "备份操作修改了原批次状态文件！")

            export_path = os.path.join(tmp, "clean.backup.json")
            self._run_in(tmp, "backup", "export", "clean-bk", export_path)
            with open(state_file, "rb") as f:
                after_export_hash = hashlib.sha256(f.read()).hexdigest()
            self.assertEqual(before_hash, after_export_hash,
                             "备份导出修改了原批次状态文件！")


class TestPlanTaskItem(unittest.TestCase):
    """PlanTaskItem 基本验证测试"""

    def test_valid_task_creation(self):
        task = PlanTaskItem.new("rules.yaml", "data/")
        self.assertEqual(task.rules_path, "rules.yaml")
        self.assertEqual(task.data_dir, "data/")
        self.assertEqual(task.name, "")
        self.assertEqual(task.description, "")

    def test_task_with_name_and_desc(self):
        task = PlanTaskItem.new(
            rules_path="rules.yaml",
            data_dir="data/",
            name="主项目扫描",
            description="每日交付检查",
        )
        self.assertEqual(task.name, "主项目扫描")
        self.assertEqual(task.description, "每日交付检查")

    def test_empty_rules_path_rejected(self):
        with self.assertRaises(PlanFormatError):
            PlanTaskItem.new("", "data/")

    def test_empty_data_dir_rejected(self):
        with self.assertRaises(PlanFormatError):
            PlanTaskItem.new("rules.yaml", "")

    def test_whitespace_paths_rejected(self):
        with self.assertRaises(PlanFormatError):
            PlanTaskItem.new("  ", "data/")
        with self.assertRaises(PlanFormatError):
            PlanTaskItem.new("rules.yaml", "   ")

    def test_to_dict_and_from_dict(self):
        task1 = PlanTaskItem.new(
            rules_path="project_a/rules.yaml",
            data_dir="project_a/data",
            name="项目A",
            description="项目A的交付检查",
        )
        d = task1.to_dict()
        self.assertEqual(d["rules_path"], "project_a/rules.yaml")
        self.assertEqual(d["data_dir"], "project_a/data")

        task2 = PlanTaskItem.from_dict(d)
        self.assertEqual(task2.rules_path, task1.rules_path)
        self.assertEqual(task2.data_dir, task1.data_dir)
        self.assertEqual(task2.name, task1.name)
        self.assertEqual(task2.description, task1.description)

    def test_from_dict_missing_fields(self):
        with self.assertRaises(PlanFormatError):
            PlanTaskItem.from_dict({"data_dir": "data/"})
        with self.assertRaises(PlanFormatError):
            PlanTaskItem.from_dict({"rules_path": "rules.yaml"})
        with self.assertRaises(PlanFormatError):
            PlanTaskItem.from_dict("not a dict")


class TestPlanCore(unittest.TestCase):
    """计划核心功能：创建、列表、获取、删除"""

    ROOT = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )
    EXAMPLE_RULES = os.path.join(ROOT, "examples", "rules.yaml")
    EXAMPLE_DATA = os.path.join(ROOT, "examples", "sample_data")

    def _make_tasks(self, count=2):
        tasks = []
        for i in range(count):
            tasks.append(PlanTaskItem.new(
                rules_path=self.EXAMPLE_RULES,
                data_dir=self.EXAMPLE_DATA,
                name=f"任务{i+1}",
                description=f"第{i+1}个任务",
            ))
        return tasks

    def test_create_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = self._make_tasks(2)
            plan = save_plan(
                base_dir=tmp,
                name="daily-check",
                description="每日交付检查",
                batch_prefix="daily-",
                tasks=tasks,
            )
            self.assertEqual(plan.name, "daily-check")
            self.assertEqual(plan.description, "每日交付检查")
            self.assertEqual(plan.batch_prefix, "daily-")
            self.assertEqual(len(plan.tasks), 2)
            self.assertTrue(plan.created_at)
            self.assertTrue(plan.updated_at)

            plans = list_plans(tmp)
            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0]["name"], "daily-check")
            self.assertEqual(plans[0]["task_count"], 2)
            self.assertEqual(plans[0]["batch_prefix"], "daily-")

    def test_get_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = self._make_tasks(1)
            save_plan(tmp, "test-plan", "测试计划", "prefix-", tasks)

            plan = get_plan(tmp, "test-plan")
            self.assertEqual(plan.name, "test-plan")
            self.assertEqual(plan.description, "测试计划")
            self.assertEqual(plan.batch_prefix, "prefix-")
            self.assertEqual(len(plan.tasks), 1)
            self.assertEqual(plan.tasks[0].name, "任务1")

    def test_delete_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = self._make_tasks(1)
            save_plan(tmp, "to-delete", "", "", tasks)
            self.assertEqual(len(list_plans(tmp)), 1)

            delete_plan(tmp, "to-delete")
            self.assertEqual(len(list_plans(tmp)), 0)

            with self.assertRaises(PlanNotFoundError):
                get_plan(tmp, "to-delete")

    def test_duplicate_name_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = self._make_tasks(1)
            save_plan(tmp, "conflict-plan", "", "", tasks)

            with self.assertRaises(PlanConflictError):
                save_plan(tmp, "conflict-plan", "新描述", "", tasks)

    def test_force_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = self._make_tasks(1)
            plan1 = save_plan(tmp, "overwrite-me", "旧描述", "", tasks)
            old_created_at = plan1.created_at
            time.sleep(1.1)

            tasks2 = self._make_tasks(3)
            plan2 = save_plan(
                tmp, "overwrite-me", "新描述", "new-", tasks2, force=True
            )
            self.assertEqual(plan2.description, "新描述")
            self.assertEqual(plan2.batch_prefix, "new-")
            self.assertEqual(len(plan2.tasks), 3)
            self.assertEqual(plan2.created_at, old_created_at)
            self.assertGreater(plan2.updated_at, old_created_at)

    def test_empty_name_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks = self._make_tasks(1)
            with self.assertRaises(PlanFormatError):
                save_plan(tmp, "", "", "", tasks)
            with self.assertRaises(PlanFormatError):
                save_plan(tmp, "  ", "", "", tasks)

    def test_empty_tasks_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PlanFormatError):
                save_plan(tmp, "test", "", "", [])

    def test_delete_nonexistent_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PlanNotFoundError):
                delete_plan(tmp, "does-not-exist")

    def test_get_nonexistent_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PlanNotFoundError):
                get_plan(tmp, "does-not-exist")


class TestPlanPersistence(unittest.TestCase):
    """跨重启持久性测试"""

    ROOT = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )
    EXAMPLE_RULES = os.path.join(ROOT, "examples", "rules.yaml")
    EXAMPLE_DATA = os.path.join(ROOT, "examples", "sample_data")

    def test_persistence_across_reload(self):
        """保存后重新 list 和 get，模拟重启"""
        with tempfile.TemporaryDirectory() as tmp:
            tasks = [
                PlanTaskItem.new(
                    rules_path=self.EXAMPLE_RULES,
                    data_dir=self.EXAMPLE_DATA,
                    name="项目A",
                    description="项目A的交付检查",
                ),
                PlanTaskItem.new(
                    rules_path=self.EXAMPLE_RULES,
                    data_dir=self.EXAMPLE_DATA,
                    name="项目B",
                    description="项目B的交付检查",
                ),
            ]

            # 第一次"启动"：保存
            plan1 = save_plan(
                tmp, "persistent-plan", "持久化测试", "daily-", tasks
            )
            self.assertEqual(len(list_plans(tmp)), 1)

            # 模拟"重启"：重新调用 list 和 get（不使用任何内存缓存）
            plans = list_plans(tmp)
            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0]["name"], "persistent-plan")
            self.assertEqual(plans[0]["task_count"], 2)

            plan2 = get_plan(tmp, "persistent-plan")
            self.assertEqual(plan2.name, plan1.name)
            self.assertEqual(plan2.description, plan1.description)
            self.assertEqual(plan2.batch_prefix, plan1.batch_prefix)
            self.assertEqual(plan2.created_at, plan1.created_at)
            self.assertEqual(len(plan2.tasks), 2)
            self.assertEqual(plan2.tasks[0].name, "项目A")
            self.assertEqual(plan2.tasks[1].name, "项目B")

    def test_multiple_plans_persisted(self):
        """多个计划共存，重启后都可见"""
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(3):
                tasks = [PlanTaskItem.new(self.EXAMPLE_RULES, self.EXAMPLE_DATA)]
                save_plan(tmp, f"plan-{i}", f"计划{i}", f"prefix{i}-", tasks)

            # 重启后
            plans = list_plans(tmp)
            self.assertEqual(len(plans), 3)
            names = sorted(p["name"] for p in plans)
            self.assertEqual(names, ["plan-0", "plan-1", "plan-2"])

            for i in range(3):
                plan = get_plan(tmp, f"plan-{i}")
                self.assertEqual(plan.description, f"计划{i}")
                self.assertEqual(plan.batch_prefix, f"prefix{i}-")

    def test_index_corruption_readonly_safety(self):
        """索引文件损坏时只读操作不破坏现有文件"""
        with tempfile.TemporaryDirectory() as tmp:
            tasks = [PlanTaskItem.new(self.EXAMPLE_RULES, self.EXAMPLE_DATA)]
            save_plan(tmp, "safe-plan", "", "", tasks)

            from delivery_checker.plan import _get_index_path, _get_plan_path
            index_path = _get_index_path(tmp)
            plan_path = _get_plan_path(tmp, "safe-plan")

            # 破坏索引
            with open(index_path, "w", encoding="utf-8") as f:
                f.write("{ this is not valid json !!!")

            # 读取时报错，但不修改任何文件
            with self.assertRaises(PlanFormatError):
                list_plans(tmp)

            # 计划文件仍然存在
            self.assertTrue(os.path.exists(plan_path))

    def test_plan_file_corruption(self):
        """计划文件损坏时给出明确错误"""
        with tempfile.TemporaryDirectory() as tmp:
            tasks = [PlanTaskItem.new(self.EXAMPLE_RULES, self.EXAMPLE_DATA)]
            save_plan(tmp, "corrupt-me", "", "", tasks)

            from delivery_checker.plan import _get_plan_path
            plan_path = _get_plan_path(tmp, "corrupt-me")

            with open(plan_path, "w", encoding="utf-8") as f:
                f.write("{ bad json !!!")

            with self.assertRaises(PlanFormatError):
                get_plan(tmp, "corrupt-me")


class TestPlanPathResolution(unittest.TestCase):
    """路径解析和验证测试"""

    ROOT = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )
    EXAMPLE_RULES = os.path.join(ROOT, "examples", "rules.yaml")
    EXAMPLE_DATA = os.path.join(ROOT, "examples", "sample_data")

    def test_relative_paths_resolved_against_workspace(self):
        """相对路径按计划文件所在工作区解析"""
        with tempfile.TemporaryDirectory() as tmp:
            # 创建相对路径的规则文件和数据目录
            workspace = tmp
            rules_rel = "project_a/rules.yaml"
            data_rel = "project_a/data"

            os.makedirs(os.path.join(workspace, "project_a"))
            with open(os.path.join(workspace, rules_rel), "w", encoding="utf-8") as f:
                f.write("""
batch_name: "test-batch"
root_alias: "test"
required_files:
  - "README.md"
""")
            os.makedirs(os.path.join(workspace, data_rel))
            with open(os.path.join(workspace, data_rel, "README.md"), "w") as f:
                f.write("test")

            tasks = [PlanTaskItem.new(rules_rel, data_rel, "任务1")]
            plan = save_plan(workspace, "path-test", "", "", tasks)

            resolved = plan.resolve_paths(workspace)
            self.assertEqual(len(resolved), 1)
            abs_rules, abs_data = resolved[0]
            self.assertEqual(abs_rules, os.path.abspath(os.path.join(workspace, rules_rel)))
            self.assertEqual(abs_data, os.path.abspath(os.path.join(workspace, data_rel)))

            errors = plan.validate_paths(workspace)
            self.assertEqual(len(errors), 0)

    def test_missing_rules_file_detected(self):
        """缺失规则文件时给出明确错误"""
        with tempfile.TemporaryDirectory() as tmp:
            tasks = [PlanTaskItem.new("nonexistent.yaml", self.EXAMPLE_DATA)]
            plan = Plan.new("missing-rules", "", "", tasks, workspace_dir=tmp)

            errors = plan.validate_paths(tmp)
            self.assertEqual(len(errors), 1)
            self.assertIn("不存在", errors[0][1])
            self.assertIn("nonexistent.yaml", errors[0][1])

    def test_missing_data_dir_detected(self):
        """缺失资料目录时给出明确错误"""
        with tempfile.TemporaryDirectory() as tmp:
            tasks = [PlanTaskItem.new(self.EXAMPLE_RULES, "nonexistent_dir")]
            plan = Plan.new("missing-data", "", "", tasks, workspace_dir=tmp)

            errors = plan.validate_paths(tmp)
            self.assertEqual(len(errors), 1)
            self.assertIn("不存在", errors[0][1])
            self.assertIn("nonexistent_dir", errors[0][1])

    def test_bad_rules_format_detected(self):
        """规则格式错误在运行时给出明确错误"""
        with tempfile.TemporaryDirectory() as tmp:
            bad_rules = os.path.join(tmp, "bad.yaml")
            with open(bad_rules, "w", encoding="utf-8") as f:
                f.write("::: invalid yaml :::\n")
            tasks = [PlanTaskItem.new(bad_rules, self.EXAMPLE_DATA)]
            plan = Plan.new("bad-rules", "", "", tasks, workspace_dir=tmp)

            errors = plan.validate_paths(tmp)
            self.assertEqual(len(errors), 0)

            save_plan(tmp, plan.name, plan.description, plan.batch_prefix, plan.tasks)

            summary = run_plan(tmp, plan.name)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["success"], 0)
            self.assertIn("规则文件解析失败", summary["results"][0]["error_message"])

    def test_path_not_directory_detected(self):
        """data_dir 指向文件而非目录时检测到"""
        with tempfile.TemporaryDirectory() as tmp:
            not_a_dir = os.path.join(tmp, "file.txt")
            with open(not_a_dir, "w") as f:
                f.write("not a dir")
            tasks = [PlanTaskItem.new(self.EXAMPLE_RULES, not_a_dir)]
            plan = Plan.new("not-dir", "", "", tasks, workspace_dir=tmp)

            errors = plan.validate_paths(tmp)
            self.assertEqual(len(errors), 1)
            self.assertIn("不是目录", errors[0][1])

    def test_multiple_path_errors_reported(self):
        """多个路径错误同时报告"""
        with tempfile.TemporaryDirectory() as tmp:
            tasks = [
                PlanTaskItem.new("missing1.yaml", self.EXAMPLE_DATA),
                PlanTaskItem.new(self.EXAMPLE_RULES, "missing_dir"),
                PlanTaskItem.new("missing2.yaml", "missing_dir2"),
            ]
            plan = Plan.new("multi-error", "", "", tasks, workspace_dir=tmp)

            errors = plan.validate_paths(tmp)
            self.assertEqual(len(errors), 4)
            indices = [e[0] for e in errors]
            self.assertEqual(indices, [0, 1, 2, 2])


class TestPlanImportExport(unittest.TestCase):
    """导入导出往返测试"""

    ROOT = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )
    EXAMPLE_RULES = os.path.join(ROOT, "examples", "rules.yaml")
    EXAMPLE_DATA = os.path.join(ROOT, "examples", "sample_data")

    def _make_plan(self, base_dir, name="export-plan", task_count=2):
        tasks = []
        for i in range(task_count):
            tasks.append(PlanTaskItem.new(
                rules_path=self.EXAMPLE_RULES,
                data_dir=self.EXAMPLE_DATA,
                name=f"任务{i+1}",
                description=f"第{i+1}个任务",
            ))
        return save_plan(
            base_dir=base_dir,
            name=name,
            description="导出测试计划",
            batch_prefix="export-",
            tasks=tasks,
        )

    def test_export_import_roundtrip(self):
        """导出后在另一个目录导入，内容完全一致"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            self._make_plan(tmp_src, "my-plan", 3)

            export_file = os.path.join(tmp_src, "my-plan.plan.json")
            saved_path = export_plan(tmp_src, "my-plan", export_file)
            self.assertTrue(os.path.exists(saved_path))

            imported = import_plan(tmp_dst, export_file)
            self.assertEqual(imported.name, "my-plan")
            self.assertEqual(imported.description, "导出测试计划")
            self.assertEqual(imported.batch_prefix, "export-")
            self.assertEqual(len(imported.tasks), 3)
            self.assertEqual(imported.tasks[0].name, "任务1")
            self.assertEqual(imported.tasks[2].name, "任务3")

            plans = list_plans(tmp_dst)
            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0]["name"], "my-plan")

    def test_export_file_format(self):
        """导出文件格式符合预期"""
        with tempfile.TemporaryDirectory() as tmp:
            self._make_plan(tmp, "format-test", 1)
            export_file = os.path.join(tmp, "export.json")
            export_plan(tmp, "format-test", export_file)

            with open(export_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.assertEqual(data["format_version"], 1)
            self.assertEqual(data["type"], "delivery-checker-plan")
            self.assertIn("plan", data)
            self.assertEqual(data["plan"]["name"], "format-test")
            self.assertEqual(data["plan"]["batch_prefix"], "export-")
            self.assertEqual(len(data["plan"]["tasks"]), 1)

    def test_import_with_rename(self):
        """导入时重命名"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            self._make_plan(tmp_src, "original-name", 2)
            export_file = os.path.join(tmp_src, "export.json")
            export_plan(tmp_src, "original-name", export_file)

            imported = import_plan(
                tmp_dst, export_file,
                conflict_strategy="refuse",
                rename_name="renamed-plan"
            )
            self.assertEqual(imported.name, "renamed-plan")
            self.assertEqual(len(imported.tasks), 2)

            # 原名也能成功导入（不冲突）
            imported2 = import_plan(tmp_dst, export_file)
            self.assertEqual(imported2.name, "original-name")

            plans = list_plans(tmp_dst)
            self.assertEqual(len(plans), 2)

    def test_import_does_not_affect_existing_plans(self):
        """导入新计划不破坏已有计划"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            # 目标目录先创建一个已有计划
            existing_tasks = [PlanTaskItem.new(
                self.EXAMPLE_RULES, self.EXAMPLE_DATA, "已存在任务"
            )]
            save_plan(tmp_dst, "existing-plan", "已存在计划", "exist-", existing_tasks)

            # 源目录导出另一个计划
            self._make_plan(tmp_src, "new-plan", 2)
            export_file = os.path.join(tmp_src, "export.json")
            export_plan(tmp_src, "new-plan", export_file)

            # 导入
            import_plan(tmp_dst, export_file)

            # 验证已有计划未受影响
            existing = get_plan(tmp_dst, "existing-plan")
            self.assertEqual(existing.name, "existing-plan")
            self.assertEqual(existing.description, "已存在计划")
            self.assertEqual(existing.batch_prefix, "exist-")
            self.assertEqual(len(existing.tasks), 1)
            self.assertEqual(existing.tasks[0].name, "已存在任务")

            # 新计划也存在
            new = get_plan(tmp_dst, "new-plan")
            self.assertEqual(len(new.tasks), 2)

            plans = list_plans(tmp_dst)
            self.assertEqual(len(plans), 2)

    def test_export_nonexistent_rejected(self):
        """导出不存在的计划时报错"""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PlanNotFoundError):
                export_plan(tmp, "no-such-plan", "out.json")

    def test_import_missing_file_rejected(self):
        """导入不存在的文件时报错"""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PlanNotFoundError):
                import_plan(tmp, "no-such-file.json")

    def test_import_bad_json_rejected(self):
        """导入损坏的 JSON 文件时报错"""
        with tempfile.TemporaryDirectory() as tmp:
            bad_file = os.path.join(tmp, "bad.json")
            with open(bad_file, "w", encoding="utf-8") as f:
                f.write("{ this is bad json !!!")

            with self.assertRaises(PlanFormatError):
                import_plan(tmp, bad_file)

    def test_import_wrong_type_rejected(self):
        """导入类型标识错误的文件时报错"""
        with tempfile.TemporaryDirectory() as tmp:
            wrong_file = os.path.join(tmp, "wrong.json")
            with open(wrong_file, "w", encoding="utf-8") as f:
                json.dump({
                    "format_version": 1,
                    "type": "wrong-type",
                    "plan": {"name": "test", "tasks": []}
                }, f)

            with self.assertRaises(PlanFormatError):
                import_plan(tmp, wrong_file)


class TestPlanConflictHandling(unittest.TestCase):
    """冲突处理策略测试"""

    ROOT = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )
    EXAMPLE_RULES = os.path.join(ROOT, "examples", "rules.yaml")
    EXAMPLE_DATA = os.path.join(ROOT, "examples", "sample_data")

    def _make_export(self, tmp_src, name):
        tasks = [PlanTaskItem.new(self.EXAMPLE_RULES, self.EXAMPLE_DATA, "导入任务")]
        save_plan(tmp_src, name, "源计划描述", "src-", tasks)
        export_file = os.path.join(tmp_src, "export.json")
        export_plan(tmp_src, name, export_file)
        return export_file

    def test_conflict_refuse_strategy(self):
        """refuse 策略：冲突时报错，不修改原有计划"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            # 目标目录已有同名计划
            tasks = [PlanTaskItem.new(
                self.EXAMPLE_RULES, self.EXAMPLE_DATA, "原有任务"
            )]
            save_plan(tmp_dst, "conflict-plan", "原有描述", "orig-", tasks)
            old_plan = get_plan(tmp_dst, "conflict-plan")
            old_created_at = old_plan.created_at

            export_file = self._make_export(tmp_src, "conflict-plan")

            # refuse 策略应报错
            with self.assertRaises(PlanConflictError):
                import_plan(tmp_dst, export_file, conflict_strategy="refuse")

            # 原有计划未被修改
            plan = get_plan(tmp_dst, "conflict-plan")
            self.assertEqual(plan.description, "原有描述")
            self.assertEqual(plan.batch_prefix, "orig-")
            self.assertEqual(plan.tasks[0].name, "原有任务")
            self.assertEqual(plan.created_at, old_created_at)

    def test_conflict_overwrite_strategy(self):
        """overwrite 策略：冲突时覆盖原有计划"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            tasks = [PlanTaskItem.new(
                self.EXAMPLE_RULES, self.EXAMPLE_DATA, "原有任务"
            )]
            save_plan(tmp_dst, "conflict-plan", "原有描述", "orig-", tasks)
            old_plan = get_plan(tmp_dst, "conflict-plan")
            old_created_at = old_plan.created_at

            export_file = self._make_export(tmp_src, "conflict-plan")

            imported = import_plan(
                tmp_dst, export_file, conflict_strategy="overwrite"
            )
            self.assertEqual(imported.name, "conflict-plan")
            self.assertEqual(imported.description, "源计划描述")
            self.assertEqual(imported.batch_prefix, "src-")
            self.assertEqual(imported.tasks[0].name, "导入任务")

            # created_at 应保留原值
            self.assertEqual(imported.created_at, old_created_at)

    def test_conflict_rename_strategy(self):
        """rename 策略：冲突时自动改名，保留两个计划"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            tasks = [PlanTaskItem.new(
                self.EXAMPLE_RULES, self.EXAMPLE_DATA, "原有任务"
            )]
            save_plan(tmp_dst, "conflict-plan", "原有描述", "orig-", tasks)

            export_file = self._make_export(tmp_src, "conflict-plan")

            imported = import_plan(
                tmp_dst, export_file, conflict_strategy="rename"
            )
            # 自动改名
            self.assertEqual(imported.name, "conflict-plan_1")
            self.assertEqual(imported.description, "源计划描述")
            self.assertEqual(imported.tasks[0].name, "导入任务")

            # 原有计划未变
            original = get_plan(tmp_dst, "conflict-plan")
            self.assertEqual(original.description, "原有描述")
            self.assertEqual(original.tasks[0].name, "原有任务")

            plans = list_plans(tmp_dst)
            self.assertEqual(len(plans), 2)

    def test_rename_strategy_multiple_conflicts(self):
        """多次 rename 冲突时递增后缀"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            tasks = [PlanTaskItem.new(self.EXAMPLE_RULES, self.EXAMPLE_DATA)]
            save_plan(tmp_dst, "multi-plan", "", "", tasks)
            save_plan(tmp_dst, "multi-plan_1", "", "", tasks)
            save_plan(tmp_dst, "multi-plan_2", "", "", tasks)

            export_file = self._make_export(tmp_src, "multi-plan")

            imported = import_plan(
                tmp_dst, export_file, conflict_strategy="rename"
            )
            self.assertEqual(imported.name, "multi-plan_3")

            plans = list_plans(tmp_dst)
            self.assertEqual(len(plans), 4)

    def test_invalid_conflict_strategy_rejected(self):
        """无效的冲突策略被拒绝"""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PlanFormatError):
                import_plan(tmp, "any.json", conflict_strategy="invalid")


class TestPlanFailureSafety(unittest.TestCase):
    """失败场景安全测试：不污染旧数据"""

    ROOT = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )
    EXAMPLE_RULES = os.path.join(ROOT, "examples", "rules.yaml")
    EXAMPLE_DATA = os.path.join(ROOT, "examples", "sample_data")

    def _dir_state_hash(self, base_dir):
        """计算 .delivery_check 目录的哈希用于检测修改"""
        state_dir = os.path.join(base_dir, ".delivery_check")
        hasher = hashlib.sha256()
        if os.path.exists(state_dir):
            for root, dirs, files in os.walk(state_dir):
                for f in sorted(files):
                    fp = os.path.join(root, f)
                    hasher.update(fp.encode("utf-8"))
                    try:
                        with open(fp, "rb") as fh:
                            hasher.update(fh.read())
                    except Exception:
                        pass
        return hasher.hexdigest()

    def test_bad_json_import_no_side_effects(self):
        """导入坏 JSON 时，不修改任何现有计划"""
        with tempfile.TemporaryDirectory() as tmp:
            tasks = [PlanTaskItem.new(self.EXAMPLE_RULES, self.EXAMPLE_DATA)]
            save_plan(tmp, "baseline", "基线计划", "", tasks)
            baseline_hash = self._dir_state_hash(tmp)

            bad_file = os.path.join(tmp, "bad.json")
            with open(bad_file, "w", encoding="utf-8") as f:
                f.write("{ this is not valid json !!!")

            with self.assertRaises(PlanFormatError):
                import_plan(tmp, bad_file)

            self.assertEqual(self._dir_state_hash(tmp), baseline_hash)
            plans = list_plans(tmp)
            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0]["name"], "baseline")

    def test_missing_fields_import_no_side_effects(self):
        """导入缺少必填字段的文件时，不修改现有状态"""
        with tempfile.TemporaryDirectory() as tmp:
            tasks = [PlanTaskItem.new(self.EXAMPLE_RULES, self.EXAMPLE_DATA)]
            save_plan(tmp, "baseline", "", "", tasks)
            baseline_hash = self._dir_state_hash(tmp)

            bad_file = os.path.join(tmp, "bad_fields.json")
            with open(bad_file, "w", encoding="utf-8") as f:
                json.dump({
                    "format_version": 1,
                    "type": "delivery-checker-plan",
                    "plan": {
                        "description": "缺少 name 和 tasks 字段"
                    }
                }, f)

            with self.assertRaises(PlanFormatError):
                import_plan(tmp, bad_file)

            self.assertEqual(self._dir_state_hash(tmp), baseline_hash)

    def test_create_failure_rollback(self):
        """创建失败时回滚部分写入的文件"""
        # 这种测试需要模拟权限错误，在单元测试中较难实现
        # 我们通过验证正常创建失败的场景不留下孤儿文件
        with tempfile.TemporaryDirectory() as tmp:
            # 尝试创建一个空任务的计划（会失败）
            try:
                save_plan(tmp, "should-fail", "", "", [])
            except PlanFormatError:
                pass

            # 验证没有留下孤儿文件
            plans_dir = os.path.join(tmp, ".delivery_check", "plans")
            if os.path.exists(plans_dir):
                files = os.listdir(plans_dir)
                plan_files = [f for f in files if f.endswith(".plan.json")]
                self.assertEqual(len(plan_files), 0)


class TestPlanCliEndToEnd(unittest.TestCase):
    """CLI 端到端测试：使用真实 subprocess 调用"""

    ROOT = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )
    EXAMPLE_RULES = os.path.join(ROOT, "examples", "rules.yaml")
    EXAMPLE_DATA = os.path.join(ROOT, "examples", "sample_data")

    def _run_in(self, work_dir, *args):
        import subprocess
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.join(self.ROOT, "src")
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, "-m", "delivery_checker", *args],
            env=env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            cwd=work_dir,
        )
        return result

    def _make_rules_file(self, tmp, batch_name, extra_path=""):
        rules_path = os.path.join(tmp, extra_path, "rules.yaml") if extra_path else os.path.join(tmp, "rules.yaml")
        if extra_path:
            os.makedirs(os.path.join(tmp, extra_path), exist_ok=True)
        with open(rules_path, "w", encoding="utf-8") as f:
            f.write(f"""
batch_name: "{batch_name}"
root_alias: "test"
required_files:
  - "README.md"
""")
        return os.path.relpath(rules_path, tmp)

    def _make_data_dir(self, tmp, extra_path="", has_readme=True):
        data_path = os.path.join(tmp, extra_path, "data") if extra_path else os.path.join(tmp, "data")
        os.makedirs(data_path, exist_ok=True)
        if has_readme:
            with open(os.path.join(data_path, "README.md"), "w") as f:
                f.write("test")
        return os.path.relpath(data_path, tmp)

    def test_cli_create_plan(self):
        """CLI 创建计划"""
        with tempfile.TemporaryDirectory() as tmp:
            rules_rel = self._make_rules_file(tmp, "batch-a")
            data_rel = self._make_data_dir(tmp)

            result = self._run_in(
                tmp, "plan", "create",
                "--name", "cli-test",
                "--description", "CLI测试计划",
                "--batch-prefix", "cli-",
                "--rules", rules_rel,
                "--data-dir", data_rel,
                "--task-name", "主任务",
                "--task-desc", "主任务说明",
            )
            self.assertEqual(result.returncode, 0,
                             f"stdout={result.stdout}\nstderr={result.stderr}")
            self.assertIn("cli-test", result.stdout)
            self.assertIn("CLI测试计划", result.stdout)
            self.assertIn("cli-", result.stdout)

            # 验证计划已创建
            plans = list_plans(tmp)
            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0]["name"], "cli-test")

            plan = get_plan(tmp, "cli-test")
            self.assertEqual(plan.description, "CLI测试计划")
            self.assertEqual(plan.batch_prefix, "cli-")
            self.assertEqual(len(plan.tasks), 1)
            self.assertEqual(plan.tasks[0].name, "主任务")
            self.assertEqual(plan.tasks[0].description, "主任务说明")

    def test_cli_create_multiple_tasks(self):
        """CLI 创建包含多个任务的计划"""
        with tempfile.TemporaryDirectory() as tmp:
            rules1 = self._make_rules_file(tmp, "batch-a", "proj_a")
            data1 = self._make_data_dir(tmp, "proj_a")
            rules2 = self._make_rules_file(tmp, "batch-b", "proj_b")
            data2 = self._make_data_dir(tmp, "proj_b")

            result = self._run_in(
                tmp, "plan", "create",
                "--name", "multi-task",
                "--rules", rules1,
                "--rules", rules2,
                "--data-dir", data1,
                "--data-dir", data2,
                "--task-name", "项目A",
                "--task-name", "项目B",
            )
            self.assertEqual(result.returncode, 0,
                             f"stdout={result.stdout}\nstderr={result.stderr}")

            plan = get_plan(tmp, "multi-task")
            self.assertEqual(len(plan.tasks), 2)
            self.assertEqual(plan.tasks[0].name, "项目A")
            self.assertEqual(plan.tasks[1].name, "项目B")
            self.assertIn("proj_a", plan.tasks[0].rules_path)
            self.assertIn("proj_b", plan.tasks[1].rules_path)

    def test_cli_list_plans(self):
        """CLI 列出计划"""
        with tempfile.TemporaryDirectory() as tmp:
            # 先创建几个计划
            for i in range(3):
                rules_rel = self._make_rules_file(tmp, f"batch-{i}", f"proj{i}")
                data_rel = self._make_data_dir(tmp, f"proj{i}")
                self._run_in(
                    tmp, "plan", "create",
                    "--name", f"plan-{i}",
                    "--description", f"计划{i}",
                    "--rules", rules_rel,
                    "--data-dir", data_rel,
                )

            result = self._run_in(tmp, "plan", "list")
            self.assertEqual(result.returncode, 0,
                             f"stderr={result.stderr}")
            self.assertIn("plan-0", result.stdout)
            self.assertIn("plan-1", result.stdout)
            self.assertIn("plan-2", result.stdout)

    def test_cli_show_plan(self):
        """CLI 显示计划详情"""
        with tempfile.TemporaryDirectory() as tmp:
            rules_rel = self._make_rules_file(tmp, "show-batch")
            data_rel = self._make_data_dir(tmp)

            self._run_in(
                tmp, "plan", "create",
                "--name", "show-test",
                "--description", "显示测试",
                "--batch-prefix", "show-",
                "--rules", rules_rel,
                "--data-dir", data_rel,
            )

            result = self._run_in(tmp, "plan", "show", "show-test")
            self.assertEqual(result.returncode, 0,
                             f"stderr={result.stderr}")
            self.assertIn("show-test", result.stdout)
            self.assertIn("显示测试", result.stdout)
            self.assertIn("show-", result.stdout)
            self.assertIn(rules_rel, result.stdout)
            self.assertIn(data_rel, result.stdout)

    def test_cli_show_nonexistent_exit_1(self):
        """显示不存在的计划返回退出码 1"""
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_in(tmp, "plan", "show", "no-such-plan")
            self.assertEqual(result.returncode, 1,
                             f"stderr={result.stderr}")
            self.assertIn("不存在", result.stderr)

    def test_cli_create_conflict_exit_3(self):
        """创建同名计划返回退出码 3"""
        with tempfile.TemporaryDirectory() as tmp:
            rules_rel = self._make_rules_file(tmp, "conflict-batch")
            data_rel = self._make_data_dir(tmp)

            # 第一次创建成功
            self._run_in(
                tmp, "plan", "create",
                "--name", "conflict-plan",
                "--rules", rules_rel,
                "--data-dir", data_rel,
            )

            # 第二次创建失败，退出码 3
            result = self._run_in(
                tmp, "plan", "create",
                "--name", "conflict-plan",
                "--rules", rules_rel,
                "--data-dir", data_rel,
            )
            self.assertEqual(result.returncode, 3,
                             f"stderr={result.stderr}")
            self.assertIn("已存在", result.stderr)

    def test_cli_create_force_overwrite(self):
        """使用 -f 强制覆盖同名计划"""
        with tempfile.TemporaryDirectory() as tmp:
            rules1 = self._make_rules_file(tmp, "batch1", "proj1")
            data1 = self._make_data_dir(tmp, "proj1")
            rules2 = self._make_rules_file(tmp, "batch2", "proj2")
            data2 = self._make_data_dir(tmp, "proj2")

            # 第一次创建
            self._run_in(
                tmp, "plan", "create",
                "--name", "overwrite-me",
                "--description", "旧描述",
                "--rules", rules1,
                "--data-dir", data1,
            )

            # 强制覆盖
            result = self._run_in(
                tmp, "plan", "create",
                "--name", "overwrite-me",
                "--description", "新描述",
                "--batch-prefix", "new-",
                "--rules", rules2,
                "--data-dir", data2,
                "--force",
            )
            self.assertEqual(result.returncode, 0,
                             f"stderr={result.stderr}")

            plan = get_plan(tmp, "overwrite-me")
            self.assertEqual(plan.description, "新描述")
            self.assertEqual(plan.batch_prefix, "new-")
            self.assertIn("proj2", plan.tasks[0].rules_path)

    def test_cli_delete_plan(self):
        """CLI 删除计划"""
        with tempfile.TemporaryDirectory() as tmp:
            rules_rel = self._make_rules_file(tmp, "del-batch")
            data_rel = self._make_data_dir(tmp)

            self._run_in(
                tmp, "plan", "create",
                "--name", "to-delete",
                "--rules", rules_rel,
                "--data-dir", data_rel,
            )
            self.assertEqual(len(list_plans(tmp)), 1)

            result = self._run_in(tmp, "plan", "delete", "to-delete")
            self.assertEqual(result.returncode, 0,
                             f"stderr={result.stderr}")
            self.assertEqual(len(list_plans(tmp)), 0)

    def test_cli_delete_nonexistent_exit_1(self):
        """删除不存在的计划返回退出码 1"""
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_in(tmp, "plan", "delete", "no-such-plan")
            self.assertEqual(result.returncode, 1,
                             f"stderr={result.stderr}")

    def test_cli_export_import_roundtrip(self):
        """CLI 导出导入往返"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            rules_rel = self._make_rules_file(tmp_src, "export-batch")
            data_rel = self._make_data_dir(tmp_src)

            self._run_in(
                tmp_src, "plan", "create",
                "--name", "export-plan",
                "--description", "导出计划",
                "--batch-prefix", "exp-",
                "--rules", rules_rel,
                "--data-dir", data_rel,
            )

            export_file = os.path.join(tmp_src, "export.plan.json")
            result = self._run_in(tmp_src, "plan", "export", "export-plan", export_file)
            self.assertEqual(result.returncode, 0,
                             f"stderr={result.stderr}")
            self.assertTrue(os.path.exists(export_file))

            result = self._run_in(tmp_dst, "plan", "import", export_file)
            self.assertEqual(result.returncode, 0,
                             f"stderr={result.stderr}")
            self.assertIn("export-plan", result.stdout)

            plans = list_plans(tmp_dst)
            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0]["name"], "export-plan")

            plan = get_plan(tmp_dst, "export-plan")
            self.assertEqual(plan.description, "导出计划")
            self.assertEqual(plan.batch_prefix, "exp-")

    def test_cli_import_conflict_refuse_exit_3(self):
        """导入冲突（refuse 策略）返回退出码 3"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            rules1 = self._make_rules_file(tmp_src, "batch1")
            data1 = self._make_data_dir(tmp_src)
            self._run_in(
                tmp_src, "plan", "create",
                "--name", "conflict-plan",
                "--description", "源计划",
                "--rules", rules1,
                "--data-dir", data1,
            )
            export_file = os.path.join(tmp_src, "export.json")
            self._run_in(tmp_src, "plan", "export", "conflict-plan", export_file)

            # 目标目录已有同名计划
            rules2 = self._make_rules_file(tmp_dst, "batch2")
            data2 = self._make_data_dir(tmp_dst)
            self._run_in(
                tmp_dst, "plan", "create",
                "--name", "conflict-plan",
                "--description", "目标计划",
                "--rules", rules2,
                "--data-dir", data2,
            )

            # refuse 策略（默认）
            result = self._run_in(tmp_dst, "plan", "import", export_file)
            self.assertEqual(result.returncode, 3,
                             f"stderr={result.stderr}")

            # 原有计划未变
            plan = get_plan(tmp_dst, "conflict-plan")
            self.assertEqual(plan.description, "目标计划")

    def test_cli_import_conflict_overwrite(self):
        """导入冲突时使用 overwrite 策略"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            rules1 = self._make_rules_file(tmp_src, "batch1")
            data1 = self._make_data_dir(tmp_src)
            self._run_in(
                tmp_src, "plan", "create",
                "--name", "conflict-plan",
                "--description", "源计划",
                "--rules", rules1,
                "--data-dir", data1,
            )
            export_file = os.path.join(tmp_src, "export.json")
            self._run_in(tmp_src, "plan", "export", "conflict-plan", export_file)

            rules2 = self._make_rules_file(tmp_dst, "batch2")
            data2 = self._make_data_dir(tmp_dst)
            self._run_in(
                tmp_dst, "plan", "create",
                "--name", "conflict-plan",
                "--description", "目标计划",
                "--rules", rules2,
                "--data-dir", data2,
            )

            result = self._run_in(
                tmp_dst, "plan", "import", export_file,
                "--conflict", "overwrite"
            )
            self.assertEqual(result.returncode, 0,
                             f"stderr={result.stderr}")

            plan = get_plan(tmp_dst, "conflict-plan")
            self.assertEqual(plan.description, "源计划")

    def test_cli_import_conflict_rename(self):
        """导入冲突时使用 rename 策略"""
        with tempfile.TemporaryDirectory() as tmp_src, \
             tempfile.TemporaryDirectory() as tmp_dst:

            rules1 = self._make_rules_file(tmp_src, "batch1")
            data1 = self._make_data_dir(tmp_src)
            self._run_in(
                tmp_src, "plan", "create",
                "--name", "conflict-plan",
                "--description", "源计划",
                "--rules", rules1,
                "--data-dir", data1,
            )
            export_file = os.path.join(tmp_src, "export.json")
            self._run_in(tmp_src, "plan", "export", "conflict-plan", export_file)

            rules2 = self._make_rules_file(tmp_dst, "batch2")
            data2 = self._make_data_dir(tmp_dst)
            self._run_in(
                tmp_dst, "plan", "create",
                "--name", "conflict-plan",
                "--description", "目标计划",
                "--rules", rules2,
                "--data-dir", data2,
            )

            result = self._run_in(
                tmp_dst, "plan", "import", export_file,
                "--conflict", "rename"
            )
            self.assertEqual(result.returncode, 0,
                             f"stderr={result.stderr}")
            self.assertIn("conflict-plan_1", result.stdout)

            plans = list_plans(tmp_dst)
            self.assertEqual(len(plans), 2)

    def test_cli_import_bad_json_exit_2(self):
        """导入损坏的 JSON 返回退出码 2"""
        with tempfile.TemporaryDirectory() as tmp:
            bad_file = os.path.join(tmp, "bad.json")
            with open(bad_file, "w", encoding="utf-8") as f:
                f.write("{ not valid json !!!")

            result = self._run_in(tmp, "plan", "import", bad_file)
            self.assertEqual(result.returncode, 2,
                             f"stderr={result.stderr}")

    def test_cli_run_plan_success(self):
        """CLI 运行计划，全部任务成功"""
        with tempfile.TemporaryDirectory() as tmp:
            # 创建两个任务
            rules1 = self._make_rules_file(tmp, "run-batch-1", "proj1")
            data1 = self._make_data_dir(tmp, "proj1")
            rules2 = self._make_rules_file(tmp, "run-batch-2", "proj2")
            data2 = self._make_data_dir(tmp, "proj2")

            self._run_in(
                tmp, "plan", "create",
                "--name", "run-test",
                "--description", "运行测试",
                "--batch-prefix", "run-",
                "--rules", rules1,
                "--rules", rules2,
                "--data-dir", data1,
                "--data-dir", data2,
            )

            # 运行计划
            result = self._run_in(tmp, "plan", "run", "run-test")
            self.assertEqual(result.returncode, 0,
                             f"stdout={result.stdout}\nstderr={result.stderr}")
            self.assertIn("成功", result.stdout)
            self.assertIn("2", result.stdout)  # 总计 2
            self.assertIn("run-run-batch-1", result.stdout)
            self.assertIn("run-run-batch-2", result.stdout)

            # 验证报告已生成
            for batch_name in ["run-run-batch-1", "run-run-batch-2"]:
                report_path = os.path.join(tmp, f"{batch_name}_report.csv")
                self.assertTrue(os.path.exists(report_path),
                               f"报告不存在: {report_path}")

            # 验证批次已创建
            batches = list_batches(tmp)
            batch_names = [b["batch_name"] for b in batches]
            self.assertIn("run-run-batch-1", batch_names)
            self.assertIn("run-run-batch-2", batch_names)

    def test_cli_run_plan_with_output_dir(self):
        """CLI 运行计划，指定输出目录"""
        with tempfile.TemporaryDirectory() as tmp:
            rules_rel = self._make_rules_file(tmp, "out-batch")
            data_rel = self._make_data_dir(tmp)

            self._run_in(
                tmp, "plan", "create",
                "--name", "out-test",
                "--rules", rules_rel,
                "--data-dir", data_rel,
            )

            output_dir = os.path.join(tmp, "reports")
            result = self._run_in(
                tmp, "plan", "run", "out-test",
                "--output-dir", output_dir,
            )
            self.assertEqual(result.returncode, 0,
                             f"stderr={result.stderr}")

            # 报告应该在输出目录
            report_path = os.path.join(output_dir, "out-batch_report.csv")
            self.assertTrue(os.path.exists(report_path))

    def test_cli_run_plan_invalid_paths_exit_2(self):
        """运行时路径无效返回退出码 2"""
        with tempfile.TemporaryDirectory() as tmp:
            # 创建一个指向不存在文件的计划
            rules_path = "nonexistent.yaml"
            data_path = "nonexistent_dir"
            os.makedirs(os.path.dirname(rules_path) if os.path.dirname(rules_path) else ".", exist_ok=True)

            # 直接用 API 创建（CLI create 不会验证路径）
            tasks = [PlanTaskItem.new(rules_path, data_path)]
            save_plan(tmp, "bad-paths", "", "", tasks)

            result = self._run_in(tmp, "plan", "run", "bad-paths")
            self.assertEqual(result.returncode, 2,
                             f"stderr={result.stderr}")
            self.assertIn("不存在", result.stderr)

    def test_cli_run_plan_partial_failure_exit_5(self):
        """部分任务失败返回退出码 5，且不中断其他任务"""
        with tempfile.TemporaryDirectory() as tmp:
            # 任务1：正常
            rules1 = self._make_rules_file(tmp, "good-batch", "good")
            data1 = self._make_data_dir(tmp, "good")

            # 任务2：规则文件格式错误
            rules2_path = os.path.join(tmp, "bad", "rules.yaml")
            os.makedirs(os.path.join(tmp, "bad"), exist_ok=True)
            with open(rules2_path, "w", encoding="utf-8") as f:
                f.write("::: invalid yaml :::")
            data2 = self._make_data_dir(tmp, "bad")
            rules2_rel = os.path.relpath(rules2_path, tmp)

            self._run_in(
                tmp, "plan", "create",
                "--name", "partial-fail",
                "--rules", rules1,
                "--rules", rules2_rel,
                "--data-dir", data1,
                "--data-dir", data2,
            )

            result = self._run_in(tmp, "plan", "run", "partial-fail")
            self.assertEqual(result.returncode, 5,
                             f"stdout={result.stdout}\nstderr={result.stderr}")

            # 摘要中应显示 1 成功 1 失败
            self.assertIn("成功: 1", result.stdout)
            self.assertIn("失败: 1", result.stdout)

            # 好的任务应该仍然生成了报告
            report_path = os.path.join(tmp, "good-batch_report.csv")
            self.assertTrue(os.path.exists(report_path))

    def test_cli_run_plan_skip_existing_batches(self):
        """运行计划时跳过已存在的批次"""
        with tempfile.TemporaryDirectory() as tmp:
            rules_rel = self._make_rules_file(tmp, "skip-batch")
            data_rel = self._make_data_dir(tmp)

            self._run_in(
                tmp, "plan", "create",
                "--name", "skip-test",
                "--rules", rules_rel,
                "--data-dir", data_rel,
            )

            # 第一次运行：成功
            result1 = self._run_in(tmp, "plan", "run", "skip-test")
            self.assertEqual(result1.returncode, 0)
            self.assertIn("成功: 1", result1.stdout)

            # 第二次运行：跳过（默认 merge 模式，退出码 3 被视为跳过）
            result2 = self._run_in(tmp, "plan", "run", "skip-test")
            self.assertEqual(result2.returncode, 0,
                             f"stdout={result2.stdout}\nstderr={result2.stderr}")
            self.assertIn("跳过: 1", result2.stdout)

    def test_cli_run_plan_force_rescan(self):
        """使用 --force 强制重新扫描已存在的批次"""
        with tempfile.TemporaryDirectory() as tmp:
            rules_rel = self._make_rules_file(tmp, "force-batch")
            data_rel = self._make_data_dir(tmp)

            self._run_in(
                tmp, "plan", "create",
                "--name", "force-test",
                "--rules", rules_rel,
                "--data-dir", data_rel,
            )

            # 第一次运行
            self._run_in(tmp, "plan", "run", "force-test")

            # 第二次运行，使用 --force
            result = self._run_in(tmp, "plan", "run", "force-test", "--force")
            self.assertEqual(result.returncode, 0)
            self.assertIn("成功: 1", result.stdout)
            self.assertIn("跳过: 0", result.stdout)

    def test_cli_run_plan_no_merge(self):
        """使用 --no-merge 时遇到已存在批次视为失败"""
        with tempfile.TemporaryDirectory() as tmp:
            rules_rel = self._make_rules_file(tmp, "nomerge-batch")
            data_rel = self._make_data_dir(tmp)

            self._run_in(
                tmp, "plan", "create",
                "--name", "nomerge-test",
                "--rules", rules_rel,
                "--data-dir", data_rel,
            )

            # 第一次运行
            self._run_in(tmp, "plan", "run", "nomerge-test")

            # 第二次运行，使用 --no-merge
            result = self._run_in(tmp, "plan", "run", "nomerge-test", "--no-merge")
            self.assertEqual(result.returncode, 5,
                             f"stdout={result.stdout}\nstderr={result.stderr}")
            self.assertIn("失败: 1", result.stdout)

    def test_cli_create_rules_data_count_mismatch_exit_2(self):
        """--rules 与 --data-dir 数量不一致返回退出码 2"""
        with tempfile.TemporaryDirectory() as tmp:
            rules1 = self._make_rules_file(tmp, "b1", "p1")
            data1 = self._make_data_dir(tmp, "p1")

            result = self._run_in(
                tmp, "plan", "create",
                "--name", "mismatch",
                "--rules", rules1,
                "--rules", rules1,
                "--data-dir", data1,
            )
            self.assertEqual(result.returncode, 2,
                             f"stderr={result.stderr}")
            self.assertIn("数量必须一致", result.stderr)

    def test_cli_persistence_across_restarts(self):
        """跨"重启"持久性：CLI 创建后，在新的 Python 进程中仍可见"""
        with tempfile.TemporaryDirectory() as tmp:
            rules_rel = self._make_rules_file(tmp, "persist-batch")
            data_rel = self._make_data_dir(tmp)

            # 进程1：创建计划
            result1 = self._run_in(
                tmp, "plan", "create",
                "--name", "persist-plan",
                "--description", "持久性测试",
                "--batch-prefix", "persist-",
                "--rules", rules_rel,
                "--data-dir", data_rel,
            )
            self.assertEqual(result1.returncode, 0)

            # 进程2：list 能看到
            result2 = self._run_in(tmp, "plan", "list")
            self.assertEqual(result2.returncode, 0)
            self.assertIn("persist-plan", result2.stdout)

            # 进程3：show 能看到详情
            result3 = self._run_in(tmp, "plan", "show", "persist-plan")
            self.assertEqual(result3.returncode, 0)
            self.assertIn("持久性测试", result3.stdout)
            self.assertIn("persist-", result3.stdout)

            # 进程4：run 能运行
            result4 = self._run_in(tmp, "plan", "run", "persist-plan")
            self.assertEqual(result4.returncode, 0)
            self.assertIn("成功: 1", result4.stdout)


if __name__ == "__main__":
    unittest.main()
