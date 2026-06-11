# 交付样例项目 README

这是一个用于演示 `delivery-checker` 的样例资料包。
本目录故意包含多种问题类型，用于验证工具功能。

## 问题预览（工具应能识别）

1. **必需文件缺失**：CHANGELOG.md、config/config.yaml
2. **命名不符合规范**：docs/requirements.docx、build/report_final.pdf
3. **文件已过期**：docs/requirements.docx（mtime 早于 2026-06-01，可通过 touch 改变）
4. **重复文件**：src/main.py 和 src/backup/main_copy.py 内容相同
5. **未纳入规则的文件**：extra_readme.txt、temp_scratch.tmp
