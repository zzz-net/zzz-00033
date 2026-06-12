"""Delivery Checker 单元测试 — 覆盖 bug 修复点。

用 unittest 标准库，无需安装额外依赖。
运行: python -m unittest tests/test_scanner.py tests/test_state.py tests/test_config.py -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
