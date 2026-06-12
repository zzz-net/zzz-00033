# 📦 本地资料包交付检查工具（Delivery Checker）

**交付物合规自查 CLI 工具**：读取 YAML/JSON 规则文件，扫描资料目录，识别 5 类交付问题，支持交互式复核、撤销、续办、导出 HTML/CSV 报告。

---

## ✨ 核心能力

| 功能 | 说明 |
|------|------|
| **5 类问题识别** | 必需文件缺失、命名不符合规范、文件已过期、内容重复、未纳入规则 |
| **复核三态** | 通过 / 忽略 / 待补充，可填写处理人和备注 |
| **撤销栈** | 支持多步撤销，空历史时明确提示且不清旧状态 |
| **可续办** | 重启后打开同一批次，复核状态、撤销历史、导出结果完全一致 |
| **规则格式** | 同时支持 YAML（`.yaml/.yml`）和 JSON（`.json`） |
| **报告导出** | 美观 HTML（卡片+表格）与 Excel 兼容 CSV（UTF-8-BOM） |
| **边界容错** | 配置格式错误、目录不存在、重复扫描、规则不一致、空撤销 均有明确提示，**不清除已有批次状态** |

---

## 🗂️ 项目结构

```
zzz-00033/
├── dc.bat / dc.sh              启动脚本（推荐使用，自动设置 PYTHONPATH 与编码）
├── requirements.txt            Python 依赖（PyYAML）
├── src/delivery_checker/
│   ├── __init__.py
│   ├── __main__.py             python -m delivery_checker 入口
│   ├── cli.py                  CLI 交互层（scan/review/mark/undo/export/list）
│   ├── config.py               规则解析（YAML/JSON + 配置校验）
│   ├── scanner.py              扫描引擎（5 类问题识别）
│   ├── state.py                状态持久化（批次/撤销栈/续办）
│   ├── report.py               报告导出（HTML/CSV）
│   └── models.py               数据模型
├── examples/
│   ├── rules.yaml              样例规则（YAML）
│   ├── rules.json              样例规则（JSON）
│   └── sample_data/            样例资料目录（故意包含 5 类问题）
└── .delivery_check/            状态文件目录（自动创建，每个批次 1 个 JSON）
```

---

## 🚀 快速开始

### 1. 环境要求

- Python 3.10+（推荐 3.12）
- 依赖：`PyYAML >= 6.0`（仅在使用 YAML 规则时需要）

```bash
# Windows PowerShell / CMD
python -m pip install -r requirements.txt
```

### 2. 快速调用（两种方式）

**方式 A：推荐 — 使用启动脚本**（自动设置 PYTHONPATH + 编码）

```bash
# Windows
dc.bat --help

# macOS / Linux
chmod +x dc.sh
./dc.sh --help
```

**方式 B：纯 Python**（需手动设置环境变量）

```bash
# Windows PowerShell
$env:PYTHONPATH="$PWD\src"; $env:PYTHONIOENCODING="utf-8"
python -m delivery_checker --help

# macOS / Linux
export PYTHONPATH="$PWD/src" PYTHONIOENCODING=utf-8
python -m delivery_checker --help
```

> 以下文档全部使用 `dc.bat`，在 macOS/Linux 替换为 `./dc.sh`，或使用方式 B。

---

## 📋 完整操作链路

下面是**推荐的标准流程**（所有命令均使用样例目录 `examples/` 验证过）。

### 步骤 1：准备规则与资料目录

工具自带样例（可直接跳过此步体验）：

| 资源 | 路径 | 说明 |
|------|------|------|
| 规则(YAML) | `examples/rules.yaml` | 推荐格式，含注释 |
| 规则(JSON) | `examples/rules.json` | 等价 JSON 版 |
| 样例资料 | `examples/sample_data/` | 故意包含全部 5 类问题 |

### 步骤 2：首次扫描 → 创建批次

```bash
dc.bat scan examples\rules.yaml examples\sample_data
```

**预期输出**（节选）：

```
✅ 已创建新批次
   批次名称: 交付样例-2026-Q2
   资料目录: D:\...\examples\sample_data
   规则文件: D:\...\examples\rules.yaml

📊 扫描结果统计:
   总计问题: 10   待复核: 10   通过: 0   忽略: 0   待补充: 0
```

> 同批次 5 类问题均被识别：
> - **缺失 2**：CHANGELOG.md、config/config.yaml
> - **命名 2**：docs/requirements.docx（不符合签字版命名）、build/report_final.pdf（不符合日期命名）
> - **过期 1**：docs/requirements.docx（mtime=2026-03-15 < 2026-06-01）
> - **重复 2**：src/backup/main_copy.py（与 src/main.py 内容相同）、deliverables/slot_b.txt（同槽位最多允许 1 份）
> - **未纳入 3**：extra_readme.txt、temp_scratch.tmp、deliverables/extra.bin

### 步骤 3：交互式复核

```bash
dc.bat review 交付样例-2026-Q2 --reviewer "张工"
```

进入交互式菜单，典型操作：

```
📋 批次「交付样例-2026-Q2」问题概览
   总数: 10  待复核: 10

   #  类型      状态    路径 / 描述
  1  必需文件缺失  待复核  CHANGELOG.md
  2  必需文件缺失  待复核  config/config.yaml
...

可用命令:
  <编号>            标记指定问题（输入序号，如: 3）
  a / all           标记全部待复核
  u / undo          撤销上一步复核
  l / list          显示全部问题（含已处理）
  e / export <文件> 导出报告（如 report.html）
  q / quit          退出（自动保存）

复核命令> 1
  → 选择操作: 1)通过 2)忽略 3)待补充 4)取消
  → 备注: 已确认客户邮件附了变更记录
  ✓ 已标记为「通过」
```

### 步骤 4：批量标记（非交互式，适合脚本）

```bash
# 把第 2、3 号问题标记为忽略（李四，附备注）
dc.bat mark "交付样例-2026-Q2" ignored --ids 2,3 --reviewer "李四" --note "客户豁免"

# 把所有待复核项批量标记为待补充
dc.bat mark "交付样例-2026-Q2" todo --all-pending --reviewer "王五" --note "汇总待需求补全"
```

### 步骤 5：撤销上一步

```bash
dc.bat undo "交付样例-2026-Q2"           # 撤销 1 步
dc.bat undo "交付样例-2026-Q2" --steps 3 # 撤销最多 3 步
```

**当撤销历史为空**时，会看到：

```
⚠️  撤销历史为空，没有可撤销的复核操作
   （旧状态未被修改）
```

> **重要**：无论撤销失败还是成功，都不会丢失已有复核状态。

### 步骤 6：导出报告

```bash
# HTML（默认，美观可视化卡片+表格）
dc.bat export "交付样例-2026-Q2" deliver_report.html

# CSV（Excel 兼容，UTF-8-BOM）
dc.bat export "交付样例-2026-Q2" deliver_report.csv -f csv

# 根据扩展名自动判断
dc.bat export "交付样例-2026-Q2" deliver_report.csv
```

导出文件路径会被打印，直接在资源管理器双击即可打开。

### 步骤 7：重启 → 继续同一批次（续办）

**场景**：第 2 天重新打开工具，继续上次未完成的复核。

```bash
# 方式 1：直接 scan 同一个 rules/data_dir  → 自动识别并续办
dc.bat scan examples\rules.yaml examples\sample_data
# 输出: ✅ 已打开已有批次继续工作（续办）

# 方式 2：先 list 查看 → 再 review
dc.bat list
# 输出历史批次列表及待复核数
dc.bat review "交付样例-2026-Q2"
```

> **保证**：重启前后复核状态、撤销历史、导出结果**完全一致**。状态文件保存在 `.delivery_check/<批次>.state.json`（原子写入）。

---

## 📦 团队规则包（Rule Package）

**场景**：团队有一套常用的扫描规则，希望在不同项目、不同资料包之间复用。
**方案**：将 YAML/JSON 规则保存为**命名规则包，支持跨目录导入导出，版本化管理。

### 规则包完整工作流

```
        项目 A 目录                    共享文件              项目 B 目录
┌─────────────────────┐          ┌────────────┐          ┌─────────────────────┐
│  1. rule-save    │ ───────▶ │ export │ ───────▶ │  3. rule-import   │
│  保存为规则包     │          │ .rulepkg│          │  导入规则包      │
│  (name+version) │          └────────┘          │                   │
└─────────────────────┘                              └─────────────────────┘
          │                                                │
          ▼                                                ▼
┌─────────────────────┐                      ┌─────────────────────┐
│  2. rule-list   │                      │  4. rule-list   │
│  查看本地规则包   │                      │  查看本地规则包   │
└─────────────────────┘                      └─────────────────────┘
```

### 步骤 1：保存规则包

把当前的 YAML/JSON 规则保存为带名称、版本、说明的规则包：

```bash
dc.bat rule-save examples\rules.yaml ^
  --name "standard-delivery" ^
  --version "1.0.0" ^
  --description "团队标准交付检查规则"
```

**输出**：
```
✅ 规则包已保存
   名称: standard-delivery
   版本: 1.0.0
   说明: 团队标准交付检查规则
   规则数: 9 条必需文件规则
```

### 步骤 2：查看已保存的规则包

```bash
dc.bat rule-list
```

**输出**：
```
  #  名称                    版本         规则数  说明
------------------------------------------------------------------------------------------
  1  standard-delivery     1.0.0          9  团队标准交付检查规则
     更新: 2026-06-12T12:11:44
```

### 步骤 3：导出规则包

```bash
# 导出到指定文件
dc.bat rule-export standard-delivery 1.0.0 standard-delivery.rulepkg.json

# 使用默认文件名（<name>_<version>.rulepkg.json）
dc.bat rule-export standard-delivery 1.0.0
```

导出的 `.rulepkg.json` 是单文件包含完整的规则包信息，可通过邮件、Git 等方式分享。

### 步骤 4：在另一个目录导入

```bash
# 基本导入（使用原名称版本）
dc.bat rule-import standard-delivery.rulepkg.json

# 导入时重命名（避免冲突）
dc.bat rule-import standard-delivery.rulepkg.json ^
  --rename-name "standard-delivery" ^
  --rename-version "1.1.0"

# 强制覆盖已存在的同名同版本
dc.bat rule-import standard-delivery.rulepkg.json --force
```

### 冲突处理示例：

**场景 A：同名同版本已存在**
```
⚠️  规则包「standard-delivery」版本「1.0.0」已存在。
   加 -f 强制覆盖，或用 -N/-V 改名导入。
   （原有规则包未被修改）
```
退出码：3

**改名导入示例：**
```bash
dc.bat rule-import standard-delivery.rulepkg.json ^
  -N "standard-delivery" -V "1.1.0"
```

**强制覆盖示例：**
```bash
dc.bat rule-import standard-delivery.rulepkg.json -f
```

**场景 B：导入坏 JSON 文件**
```
❌ 规则包格式错误: JSON 解析失败 bad.json: Expecting property name...
```
退出码：2，且**原有规则包不受任何影响**

### 规则包文件结构

所有规则包保存在当前工作目录的 `.delivery_check/rule_packages/ 子目录：

```
.delivery_check/
  rule_packages/
    index.json                 # 持久化索引（原子写入）
    standard-delivery_1.0.0.json   # 规则包本体
```

**索引文件**：记录所有规则包的元数据（名称、版本、说明、创建时间、规则数量），重启后仍可列出。

**原子写入保证**：所有写入操作先写 `.tmp` 再 `shutil.move` 替换，中途崩溃不会损坏文件。

### 规则包导出文件格式

```json
{
  "format_version": 1,
  "type": "delivery-checker-rule-package",
  "package": {
    "name": "standard-delivery",
    "version": "1.0.0",
    "description": "团队标准交付检查规则",
    "created_at": "2026-06-12T12:11:44",
    "updated_at": "2026-06-12T12:11:44",
    "rules": { ... 完整的 CheckRules 对象 ... }
  }
}
```

---

## 🎛️ 视图预设（筛选/排序/默认处理人一键套用）

如果经常处理不同资料包，可把常用的筛选条件、排序方式、默认处理人存为**命名视图预设**，下次一键套用，不用每次输参数。所有预设会落到 `.delivery_check/view_presets/` 本地配置，**跨重启依然可用**。

### 预设内容字段

| 字段 | 说明 | 可选值 / 格式 |
|------|------|--------------|
| 问题类型 `-t` | 只看某些问题类型 | `missing,naming,expired,duplicate,untracked` 逗号分隔 |
| 复核状态 `-f` | 按复核状态过滤 | `pending,passed,ignored,todo` 逗号分隔 |
| 路径关键字 `-p` | 按路径或描述子串匹配 | 任意字符串 |
| 排序字段 `--sort-by` | 排序维度 | `type` / `path` / `status` / `reviewed_at` / `id` |
| 排序方向 `--sort-order` | 升序或降序 | `asc` / `desc` |
| 默认处理人 `-r` | 进入复核时的 reviewer | 任意字符串 |
| 说明 `-d` | 可读的预设用途备注 | 任意字符串 |

### 步骤 1：保存预设

```bash
# 示例 A：只看缺失文件，按路径排序，默认处理人「张工」
dc.bat preset-save ^
  -n "缺失文件优先" ^
  -d "专注资料包必需文件缺失问题" ^
  -t missing ^
  --sort-by path --sort-order asc ^
  -r "张工"

# 示例 B：只看待办 + 待复核，按状态倒序
dc.bat preset-save -n "今日待办" -f pending,todo --sort-by status --sort-order desc

# 示例 C：只看路径含 config 的文件（配置文件专区）
dc.bat preset-save -n "配置文件相关" -p "config"
```

**输出**：
```
✅ 视图预设已保存
   名称: 缺失文件优先
   说明: 专注资料包必需文件缺失问题
   问题类型: missing
   排序方式: path / asc
   默认处理人: 张工
   创建时间: 2026-06-12T...
```

### 步骤 2：查看 / 列出预设

```bash
# 列出所有预设（表格视图）
dc.bat preset-list

# 查看某个预设的完整详情
dc.bat preset-show "缺失文件优先"
```

### 步骤 3：在 review / export 中套用预设

套用预设使用 `--preset <名称>` 参数，支持在 `review`、`export` 命令上。

**参数组合规则（避免绕）：CLI 显式指定的参数优先于预设值，缺省时回退预设。** 屏幕会用一行提示清楚标出「哪些来自预设、哪些来自 CLI」。

```bash
# ① 纯预设（全部从预设取值）
dc.bat review "交付样例-2026-Q2" --preset "缺失文件优先"
# 屏幕提示：🔍 套用预设「缺失文件优先」| 类型筛选(预设): missing | 排序字段(预设): path | 默认处理人(预设): 张工

# ② 预设 + CLI 覆盖排序方式（CLI 优先）
dc.bat review "交付样例-2026-Q2" --preset "缺失文件优先" --sort-by reviewed_at --sort-order desc
# 屏幕提示：🔍 套用预设「缺失文件优先」| 类型筛选(预设): missing | 排序字段(CLI): reviewed_at | 排序方向(CLI): desc | ...

# ③ 预设 + 额外追加关键字（只看 config 相关的 missing）
dc.bat review "交付样例-2026-Q2" --preset "缺失文件优先" -p "config"

# ④ 非交互式套用预设导出报告
dc.bat export "交付样例-2026-Q2" report.html --preset "缺失文件优先"

# ⑤ 套用预设 + 覆盖状态 → 只看 passed 的 missing
dc.bat export "交付样例-2026-Q2" report.csv -f csv --preset "缺失文件优先" -f passed
```

> **撤销后按预设查看**：如果 `undo` 撤销了若干标记，再用 `--preset "今日待办"`（只看 `pending,todo`）review / export，立刻看到恢复后的待办清单。

### 步骤 4：导出 / 导入预设（团队共享）

```bash
# 导出预设为 JSON 文件（单文件，可分享）
dc.bat preset-export "缺失文件优先" missing-first.preset.json

# 在另一台机器 / 另一个工作目录导入
dc.bat preset-import missing-first.preset.json

# 导入时改名（避免同名冲突）
dc.bat preset-import missing-first.preset.json -N "我的缺失视图"

# 强制覆盖同名预设
dc.bat preset-import missing-first.preset.json -f
```

### 冲突处理示例

**场景 A：同名预设已存在（preset-save / preset-import）**
```
⚠️  视图预设「缺失文件优先」已存在。
   加 -f 覆盖，或换个名称保存。
```
退出码：**3**。原有预设**不被修改**。

**场景 B：导入文件是坏 JSON / 缺字段 / 格式错误**
```
❌ 预设格式错误: JSON 解析失败 bad.json: Expecting property name...
   （原有预设未被修改）
```
退出码：**2**。原有预设**不被污染**。

**场景 C：目录无写权限**
```
❌ 权限错误: 无法创建目录 ...: Permission denied
```
退出码：**4**。

### 视图预设文件结构

```
.delivery_check/
  view_presets/
    index.json                 # 持久化索引（原子写入）
    缺失文件优先.preset.json    # 预设本体
```

- 索引和预设都是原子写入（先写 tmp → move 替换）
- 批次状态目录（`*.state.json`）与规则包目录（`rule_packages/`）完全独立，预设操作绝不读取或修改它们

---

## ⚠️ 边界场景提示

所有边界场景都**不会清除既有批次状态**。

### 场景 1：重复扫描（同名批次已存在）

```
dc.bat scan examples\rules.yaml examples\sample_data --no-merge
```

输出：
```
⚠️  批次「交付样例-2026-Q2」已存在，不能重复扫描（--no-merge 模式）。
去掉 --no-merge 即可自动续办，或加 --force 强制重新扫描。
```

> 如果你想**重新扫描文件、但保留复核状态**，加 `--force`。

### 场景 2：资料目录不存在

```
dc.bat scan examples\rules.yaml nonexistent_dir
```

输出：
```
❌ 资料目录不存在: D:\...\nonexistent_dir
   （注意：扫描失败不会清除已有批次状态）
```

### 场景 3：规则格式错误（语法级）

YAML/JSON 解析失败时，提示行列号：
```
❌ 配置格式错误: 解析YAML格式失败: while parsing a flow node
  in "<unicode string>", line 2, column 3: ...
```

### 场景 4：规则语义错误

如 `required_files` 不是数组、缺少 `batch_name` 等：
```
❌ 配置格式错误: required_files 必须是数组
❌ 配置格式错误: 规则缺少必填字段 batch_name
```

### 场景 5：规则文件被修改后再次扫描同一批次

为防止误操作，工具会校验规则文件的哈希值：
```
⚠️  当前规则文件与历史批次「交付样例-2026-Q2」使用的规则不一致。
如需重新扫描请使用 --force 参数，或修改 batch_name 避免冲突。
```

### 场景 6：撤销历史为空

```
dc.bat undo "交付样例-2026-Q2"
```
输出：
```
⚠️  撤销历史为空，没有可撤销的复核操作
   （旧状态未被修改）
```

---

## 📐 规则文件格式说明

两种格式（YAML / JSON）完全等价，以下以 YAML 为例：

```yaml
# ====== 必填：批次唯一标识（决定状态文件名，续办时匹配） ======
batch_name: "交付样例-2026-Q2"
root_alias: "交付资料包"      # 业务别名，可自由命名

# ====== 可选：全局过期日期（ISO 格式，仅当单条未指定时生效） ======
expiry_date: "2026-01-01"

# ====== 可选：忽略模式（glob，匹配后文件不计入扫描） ======
ignore_patterns:
  - "**/.git/**"
  - "**/*.log"
  - "**/Thumbs.db"

# ====== 必填：必需文件清单 ======
required_files:
  # 最简形式：字符串 glob
  - "README.md"

  # 完整形式：对象，可附带规则
  - pattern: "docs/requirements.docx"
    description: "签字版需求文档"
    optional: false              # 设 true 则缺失不告警
    naming_rule: "signature-doc" # 关联下方命名规则的 name 字段
    expiry_date: "2026-06-01"   # 单独过期日（优先于全局）

  - pattern: "tests/**/*.py"
    description: "单元测试（可选）"
    optional: true

  - pattern: "deliverables/slot_*.txt"
    description: "交付槽位文件（同一槽位仅允许 1 份）"
    max_matches: 1              # 同规则匹配的文件数上限（默认：无通配时=1，含通配时=不限制）

# ====== 可选：命名规则（与 required_files 条目联动） ======
naming_rules:
  - name: "signature-doc"        # 推荐显式命名，与 required_files.naming_rule 对应
    pattern: "docs/**/requirements.docx"
    regex: "^requirements_(v\\d{4}\\.\\d{2})_签字版\\.docx$"
    description: "形如 requirements_v2026.02_签字版.docx"

  - name: "report-date"
    pattern: "build/report*.pdf"
    regex: "^test_report_(\\d{8})\\.pdf$"
    description: "形如 test_report_20260612.pdf"

# ====== 可选：任意元数据（会保存在状态中） ======
metadata:
  client: "示例客户"
  project: "XX 系统交付"
  owner: "交付组"
```

### 支持的 glob 语法

- `*` 匹配任意字符（**不跨**目录分隔符 `/`）
- `**` 匹配零或任意层级子目录（可匹配零段路径，例如 `docs/**/*.md` 既能匹配 `docs/a.md` 也能匹配 `docs/sub/a.md`）
- `?` 匹配单个字符
- `[...]` 字符类
- 匹配基于**完整相对路径**，不会仅用 basename 回退

### 命名规则正则

使用 Python `re.match`（从文件名开头匹配），对**文件 basename（不含路径）** 进行校验。

### max_matches 行为

当一个 `required_files[i]` 条目匹配到多个文件时：
- 若 pattern **不含**通配符（如 `README.md`），默认 `max_matches=1`，多余文件按「重复」告警
- 若 pattern **含**通配符（如 `docs/**/*.md`），默认不限制（匹配多少都可以）
- 可显式设置 `max_matches` 覆盖默认行为（例如样例中的 `slot_*.txt` 限制为 1）

---

## 💾 状态文件与续办一致性

所有状态保存在当前工作目录的 `.delivery_check/` 子目录：

```
.delivery_check/
  交付样例-2026-Q2.state.json     # 某一批次的完整状态
```

状态 JSON 包含：

| 字段 | 说明 |
|------|------|
| `issues` | 所有问题对象，含 `status/reviewer/note/reviewed_at` |
| `undo_stack` | 撤销栈（逐条记录前一状态快照） |
| `rules` | 规则快照 + `rules_hash`，用于一致性校验 |
| `created_at / updated_at` | 时间戳 |

**写入策略**：先写入 `.tmp`，再 `shutil.move` 原子替换，避免中途崩溃损坏文件。

**一致性保证**：
- 同一批次的所有复核状态和撤销历史**在 scan/review/mark/undo/export 命令间完全持久化**
- 重启后 `scan` 或 `review` 自动读取状态文件
- 规则文件被修改后会拒绝扫描（使用 `--force` 可强制重扫，仍保留复核状态）

---

## 🔧 命令速查

| 命令 | 说明 |
|------|------|
| `dc.bat scan <rules> <data_dir> [--force] [--no-merge]` | 扫描，自动创建/续办批次 |
| `dc.bat review <batch> [-r 处理人] [-f 状态过滤] [-t 类型过滤] [-p 关键字] [--sort-by ...] [--sort-order ...] [--preset <名称>] [-a]` | 交互式复核（支持预设 + 筛选） |
| `dc.bat mark <batch> <passed\|ignored\|todo\|pending> [--ids 1,3] [--all-pending] [-n 备注] [-r 处理人]` | 批量标记 |
| `dc.bat undo <batch> [--steps N]` | 撤销最近 N 步复核 |
| `dc.bat export <batch> [输出文件] [-f html\|csv\|auto] [-F 状态过滤] [-t 类型过滤] [-p 关键字] [--preset <名称>]` | 导出报告（支持预设 + 筛选） |
| `dc.bat list` | 列出所有历史批次 |
| `dc.bat rule-save <rules> --name <name> --version <ver> [--description <desc>] [--force]` | 保存规则为命名规则包 |
| `dc.bat rule-list` | 列出所有已保存的规则包 |
| `dc.bat rule-export <name> <version> [output]` | 导出规则包为可分享文件 |
| `dc.bat rule-import <file> [-f] [-N <name>] [-V <version>]` | 导入规则包，支持覆盖或改名 |
| `dc.bat preset-save -n <名称> [-d 说明] [-t 类型] [-f 状态] [-p 关键字] [--sort-by ...] [--sort-order ...] [-r 处理人] [-f 覆盖]` | 保存视图预设 |
| `dc.bat preset-list` | 列出所有视图预设 |
| `dc.bat preset-show <名称>` | 查看某预设完整详情 |
| `dc.bat preset-delete <名称>` | 删除视图预设 |
| `dc.bat preset-export <名称> [output]` | 导出预设为可分享 JSON |
| `dc.bat preset-import <file> [-f] [-N 新名称]` | 导入预设，支持覆盖或改名 |
| `dc.bat --no-color ...` | 禁用彩色输出（日志/管道场景） |

**退出码说明**：

| 退出码 | 含义 |
|--------|------|
| 0 | 成功 |
| 1 | 目录不存在 / 文件不存在 / 预设不存在 / 运行时错误 |
| 2 | 规则配置格式/语义错误 / 规则包或预设格式错误（坏 JSON / 缺字段） |
| 3 | 重复扫描 / 规则/目录不一致 / 规则包或预设同名冲突（拒绝执行，未修改旧状态） |
| 4 | 状态文件读写失败 / 权限不足 |
| 130 | Ctrl+C 中断 |

---

## ✅ 自检脚本（一键跑通端到端）

在项目根目录执行以下命令，30 秒内完成完整链路验证：

```bash
# 1) 首次扫描
dc.bat scan examples\rules.yaml examples\sample_data

# 2) 批量标记几个问题
dc.bat mark "交付样例-2026-Q2" passed --ids 1 -r "张三" -n "已确认"
dc.bat mark "交付样例-2026-Q2" ignored --ids 2 -r "李四" -n "客户豁免"
dc.bat mark "交付样例-2026-Q2" todo --ids 3 -r "王五" -n "需需求补全"

# 3) 导出两份报告
dc.bat export "交付样例-2026-Q2" examples\result.html
dc.bat export "交付样例-2026-Q2" examples\result.csv

# 4) 续办验证：重新 scan，统计显示 1通过+1忽略+1待补充（总数 10）
dc.bat scan examples\rules.yaml examples\sample_data

# 5) 撤销 1 步 + 撤销空历史验证
dc.bat undo "交付样例-2026-Q2"
dc.bat undo "交付样例-2026-Q2"
dc.bat undo "交付样例-2026-Q2"
```

---

## 📝 常见问题

**Q: 规则文件我写了 `required_files: tests/**`，但扫描到大量 test_*.py 都报了未纳入规则？**  
A: `tests/**` 是目录匹配；请使用 `tests/**/*.py` 这种明确的文件 glob。

**Q: 我修改了资料目录，想让工具重新扫描但不想丢失之前的复核结论？**  
A: 使用 `dc.bat scan rules.yaml data_dir --force`，它会：重新扫描文件 → 按问题 ID 匹配已有复核状态 → 结果合并。

**Q: 为什么同一个问题在两次扫描间复核状态丢失了？**  
A: 问题 ID 由（类型+路径+规则名+内容哈希）计算。如果资料目录中文件路径或规则的 pattern 变了，会被视为新问题。建议路径和 pattern 先固化再复核。

**Q: 我在多台机器间同步状态，应该复制哪些文件？**  
A: 整个 `.delivery_check/` 目录 + 规则文件本身（建议把 rules 文件纳入版本管理）。

---

## 📄 License & Notice

本工具为本地离线工具，不联网、不上传任何文件内容。所有状态与报告均保存在你指定的本地路径下。
