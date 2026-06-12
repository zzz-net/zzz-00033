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
| **备份与恢复** | 将工作区可迁移状态打成备份包，支持 JSON/ZIP 导出、跨机器导入、差异预览恢复 |

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
│   ├── rule_pkg.py             规则包管理（保存/导入/导出）
│   ├── view_preset.py          视图预设（筛选/排序/一键套用）
│   ├── snapshot.py             快照归档（创建/导入/导出）
│   ├── compare.py              批次对比（差异识别/配置/导出）
│   ├── backup.py               工作区备份与恢复（create/list/show/export/import/restore/delete）
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

## 📊 批次对比（跨 scan / review 看差异）

把两次 `scan` 或 `review` 后的结果拉出来对比，看**新增、消失、状态变化、处理人变化、规则命中变化**，追踪资料包在不同版本之间的演进。

### 匹配口径（稳定、可预期）

同一条记录在不同批次中会被稳定地匹配，以下差异都会被**正确合并**：
- **路径大小写**：`README.md` ≡ `readme.md` ≡ `README.MD`
- **相对/绝对路径**：`./docs/design.md` ≡ `D:\pkg\docs\design.md`
- **路径分隔符**：`docs\design.md` ≡ `docs/design.md`
- **重复文件**：同一 `group_key` 的重复文件按组匹配（与路径无关）
- 两条记录的 `type + 标准化路径` 一致即视为同一问题（或 `type + group_key` 对于重复文件）

### 差异类型

| 差异类型 | 含义 |
|---------|------|
| ➕ **新增** | 批次 B 有、批次 A 没有的问题 |
| ➖ **消失** | 批次 A 有、批次 B 没有的问题（已修复或删除） |
| 🔄 **变化** | 两批次都存在，但内容有变化 |
| 🟰 **未变** | 两批次中完全一致 |

变化会进一步细分：
- `status`：复核状态变化（待复核 → 已通过等）
- `reviewer`：处理人变化
- `type`：问题类型变化
- `message`：问题描述变化
- `note`：复核备注变化
- `detail`：详细信息变化

---

### 快速上手

#### ① 按批次名称对比

```bash
# 对比 batch-v1 和 batch-v2
dc compare --a batch-v1 --b batch-v2
```

输出示例：
```
📊 批次对比结果
   批次 A: batch-v1  (2026-06-10T10:30:00)
   批次 B: batch-v2  (2026-06-12T14:20:00)

📈 差异汇总:
   新增: 2  消失: 1  变化: 3  未变: 15
   状态变化: 2 | 处理人变化: 1

➕ 新增问题（B 有、A 无）:
   [缺失必备] docs/api.md - 必交付文档不存在 (待复核)
   [未跟踪文件] temp/debug.log - 工作区外文件 (待复核)

➖ 消失问题（A 有、B 无）:
   [缺失必备] CHANGELOG.md - 必交付文档不存在 (已通过)

🔄 变化问题:
   src/config.yaml
     变化字段: status,reviewer
     状态: 待复核 → 已通过
     处理人: (无) → 李工
```

#### ② 用「最近 N 次」选择批次

不用记批次名，直接对比最近两次扫描：

```bash
# 最近第 2 个 vs 最近第 1 个（默认最新）
dc compare --a-latest 2 --b-latest 1

# 简写：对比前两个（上一次 vs 最新）
dc compare --a-latest 2 --b-latest 1
```

---

### 保存对比配置（可复用）

经常对比的两个批次可以存成**命名配置**，一键运行，跨重启依然可用。

#### 保存配置

```bash
# 保存为 "每日对比" 配置
dc compare-save -n "每日对比" -d "例行每日两次扫描对比" \
    --a-latest 2 --b-latest 1 \
    -f json -o ./daily_diff.json -c rename
```

参数说明：
| 参数 | 说明 |
|-----|------|
| `-n, --name` | 配置名称（必填） |
| `-d, --description` | 配置说明 |
| `--a / --a-latest N` | 批次 A：名称或最近第 N 个 |
| `--b / --b-latest N` | 批次 B：名称或最近第 N 个 |
| `-f, --format` | 默认导出格式：`json` / `csv` |
| `-o, --output` | 默认导出路径 |
| `-c, --conflict` | 文件冲突策略：`rename` / `overwrite` / `refuse` |
| `--force` | 强制覆盖同名配置 |

#### 用保存的配置运行对比

```bash
# 直接按配置运行
dc compare-run "每日对比"

# 运行时覆盖部分参数（CLI 优先）
dc compare-run "每日对比" -o ./today_diff.json -c overwrite
```

#### 配置管理

```bash
# 列出所有对比配置
dc compare-list

# 查看配置详情
dc compare-show "每日对比"

# 删除配置
dc compare-delete "每日对比"
```

#### 配置文件结构

```
.delivery_check/
└── compare_configs/
    ├── index.json                 # 持久化索引（原子写入）
    └── 每日对比.compare.json       # 配置本体
```

- 配置文件都是原子写入（先写 tmp → move 替换）
- **绝不改动**原批次状态文件、撤销栈、规则包索引
- 重启后配置依然存在

---

### 导出对比结果（JSON / CSV）

#### 直接导出

```bash
# 对比并导出 JSON（扩展名自动识别）
dc compare --a batch-v1 --b batch-v2 -o ./diff.json

# 对比并导出 CSV
dc compare --a batch-v1 --b batch-v2 -o ./diff.csv

# 显式指定格式
dc compare --a batch-v1 --b batch-v2 -o ./diff -f csv
```

#### 文件冲突处理

导出文件已存在时，用 `-c, --conflict` 选择处理策略：

```bash
# ① 拒绝：报错退出，不修改任何文件（默认策略）
dc compare --a batch-v1 --b batch-v2 -o ./diff.json -c refuse
# ⚠️  导出冲突: 导出文件已存在（拒绝覆盖）: .../diff.json
# 退出码: 3

# ② 自动改名：生成 diff_1.json、diff_2.json...（推荐）
dc compare --a batch-v1 --b batch-v2 -o ./diff.json -c rename
# 📄 对比结果已导出: ./diff_1.json
# 退出码: 0

# ③ 覆盖：直接覆盖原文件
dc compare --a batch-v1 --b batch-v2 -o ./diff.json -c overwrite
# 📄 对比结果已导出: ./diff.json
# 退出码: 0
```

#### JSON 导出结构

```json
{
  "batch_a": {"name": "batch-v1", "updated_at": "2026-06-10T10:30:00"},
  "batch_b": {"name": "batch-v2", "updated_at": "2026-06-12T14:20:00"},
  "compared_at": "2026-06-12T15:00:00",
  "summary": {
    "added": 2, "removed": 1, "changed": 3, "unchanged": 15,
    "status_changed": 2, "reviewer_changed": 1,
    "type_changed": 0, "message_changed": 1
  },
  "added": [/* 新增的问题列表 */],
  "removed": [/* 消失的问题列表 */],
  "changed": [
    {
      "match_key": "missing::path::src/config.yaml",
      "change_types": ["status", "reviewer"],
      "old": {/* 批次 A 中的问题 */},
      "new": {/* 批次 B 中的问题 */}
    }
  ],
  "unchanged": [/* 未变化的问题列表 */]
}
```

#### CSV 导出结构

| 差异类型 | 匹配键 | 旧类型 | 新类型 | 旧路径 | 新路径 | 旧状态 | 新状态 | 旧处理人 | 新处理人 | 旧描述 | 新描述 | 变化字段 |
|---------|-------|-------|-------|-------|-------|-------|-------|---------|---------|-------|-------|---------|
| 新增 | ... | | 缺失必备 | | docs/api.md | | 待复核 | | | | 必交付文档不存在 | |
| 消失 | ... | 缺失必备 | | CHANGELOG.md | | 已通过 | | 张工 | | 必交付文档不存在 | | |
| 变化 | missing::path::src/config.yaml | 缺失必备 | 缺失必备 | src/config.yaml | src/config.yaml | 待复核 | 已通过 | (无) | 李工 | 缺失 | 缺失 | status,reviewer |

---

### 错误码与边界处理

| 退出码 | 场景 | 示例 |
|-------|------|------|
| **0** | 成功 | 对比完成 / 配置保存成功 / 导出成功 |
| **1** | 批次不存在 / 配置不存在 | `dc compare --a no-such-batch --b batch-v2` → 批次不存在 |
| **2** | 配置损坏 / 格式错误 / 权限错误 | 配置 JSON 损坏、目标目录无写权限 |
| **3** | 导出文件已存在（拒绝策略） / 配置同名冲突 | `-c refuse` 时文件已存在 |

#### 典型场景示例

```bash
# 场景 1：批次不存在
dc compare --a no-batch --b batch-v2
# ❌ 批次不存在: 批次 no-batch 不存在
# 退出码: 1

# 场景 2：目标目录无写权限
dc compare --a batch-v1 --b batch-v2 -o /protected/diff.json
# ❌ 导出权限错误: 目标目录无写权限: /protected
# 退出码: 4

# 场景 3：配置文件损坏
echo "this is not json" > .delivery_check/compare_configs/bad.compare.json
dc compare-show bad
# ❌ 对比配置损坏: 对比配置文件损坏（JSON 解析失败）
# 退出码: 2

# 场景 4：配置同名（不加 -f）
dc compare-save -n "每日对比" --a batch-v1 --b batch-v2
# ⚠️  对比配置「每日对比」已存在，加 -f 覆盖或换名称
# 退出码: 3
```

> **重要保证**：对比操作是**只读**的，**绝不会修改原批次、撤销栈或规则包索引**。即使对比过程中出错，已有批次数据完整无损。

---

### 常用工作流示例

#### 工作流 1：每日版本对比

```bash
# 早上扫描一次
dc scan rules.yaml ./pkg_v1 --name morning-scan

# 下午扫描一次
dc scan rules.yaml ./pkg_v2 --name afternoon-scan

# 对比看差异并保存配置
dc compare --a morning-scan --b afternoon-scan \
    -s daily-check -d "每日早晚版本对比" \
    -o daily_diff.csv -c rename

# 之后每天只需一键运行
dc compare-run daily-check
```

#### 工作流 2：复核前后对比

```bash
# 扫描 → 得到 batch-raw
dc scan rules.yaml ./pkg --name batch-raw

# 多人复核完一批 → 得到 batch-reviewed
# ... mark / review 操作 ...

# 对比看哪些问题已处理
dc compare --a batch-raw --b batch-reviewed -o review_progress.json
# 可以快速统计：多少问题状态变化了、多少处理人分配了
```

#### 工作流 3：保存配置 + 跨重启使用

```bash
# 第一次：保存对比配置
dc compare-save -n "回归测试对比" -d "每轮回归后对比" \
    --a-latest 2 --b-latest 1 \
    -o regression_diff.json -c rename

# 第二天：重启后直接用（配置已持久化）
dc compare-run "回归测试对比"
# ✅ 自动找到最近两个批次对比，输出到 regression_diff_1.json
```

---

## 📸 交付快照归档

把某次扫描后的批次、规则摘要和当前复核状态打包成**可留档的快照**，用于审计回溯、交付证据留存、跨环境迁移。所有快照保存在 `.delivery_check/snapshots/` 目录，**跨重启依然可用**，且**绝不会污染原批次、撤销栈、规则包索引或视图预设**。

### 快照内容

每次创建快照时会完整记录：

| 内容 | 说明 |
|------|------|
| 批次问题清单 | 所有问题的完整状态（含复核人、备注、状态、时间戳） |
| 规则摘要 | 必需文件/命名规则/忽略规则数量、规则哈希、规则源路径 |
| 复核状态分布 | pending / passed / ignored / todo 各状态数量统计 |
| 元数据 | 快照名称、说明、来源批次、创建/更新时间 |

### 快速上手

#### 1. 创建快照

```bash
# 从批次「交付样例-2026-Q2」创建快照，命名为「Q2-final-review」
dc.bat snapshot create ^
  -n "Q2-final-review" ^
  -d "Q2 交付最终复核完成，共 10 个问题，8 项通过 2 项豁免" ^
  -b "交付样例-2026-Q2"
```

**输出：**
```
✅ 已创建快照: Q2-final-review
  来源批次: 交付样例-2026-Q2
  问题数量: 10
  状态分布: {'pending': 0, 'passed': 8, 'ignored': 2, 'todo': 0}
  规则摘要: 3 条必需文件规则, 2 条命名规则, 3 条忽略规则
  创建时间: 2026-06-12T15:30:00
```

#### 2. 列出所有快照

```bash
dc.bat snapshot list
```

**输出：**
```
共 2 个快照：

1. Q2-final-review
   说明: Q2 交付最终复核完成
   来源批次: 交付样例-2026-Q2
   创建时间: 2026-06-12T15:30:00
   问题数量: 10
   状态分布: 待复核 0, 已通过 8, 已忽略 2, 待补充 0
   规则摘要: 3 必需 2 命名

2. Q2-mid-review
   说明: Q2 中期复核快照
   来源批次: 交付样例-2026-Q2
   创建时间: 2026-06-10T10:15:00
   问题数量: 10
   状态分布: 待复核 5, 已通过 3, 已忽略 0, 待补充 2
   规则摘要: 3 必需 2 命名
```

#### 3. 查看快照详情

```bash
# 基本信息
dc.bat snapshot show "Q2-final-review"

# 包含问题详情
dc.bat snapshot show "Q2-final-review" --verbose
```

**输出（基本信息）：**
```
快照: Q2-final-review
  说明: Q2 交付最终复核完成
  来源批次: 交付样例-2026-Q2
  来源数据目录: D:\work\delivery\sample_data
  来源规则文件: D:\work\delivery\rules.yaml
  创建时间: 2026-06-12T15:30:00
  更新时间: 2026-06-12T15:30:00

规则摘要:
  3 条必需文件规则, 2 条命名规则, 3 条忽略规则
  规则批次名: 交付样例-2026-Q2
  规则版本哈希: a1b2c3d4...

问题统计 (10 个问题):
  待复核: 0
  已通过: 8
  已忽略: 2
  待补充: 0
```

#### 4. 导出快照（JSON 格式）

```bash
# 导出到指定路径
dc.bat snapshot export "Q2-final-review" .\archives\q2_final.snapshot.json

# 使用默认路径（<name>.snapshot.json）
dc.bat snapshot export "Q2-final-review"
```

**输出：**
```
✅ 已导出快照到: D:\work\delivery\archives\q2_final.snapshot.json
```

#### 5. 导入快照

```bash
# 默认策略：同名拒绝
dc.bat snapshot import .\archives\q2_final.snapshot.json

# 同名时覆盖
dc.bat snapshot import .\archives\q2_final.snapshot.json --conflict overwrite

# 同名时自动改名（如 Q2-final-review_1）
dc.bat snapshot import .\archives\q2_final.snapshot.json --conflict rename

# 导入时重命名
dc.bat snapshot import .\archives\q2_final.snapshot.json ^
  --rename-name "Q2-final-review-archive"
```

**场景 A：同名拒绝**
```
❌ 快照名称冲突: 快照「Q2-final-review」已存在。
使用 --conflict overwrite 覆盖，或 --conflict rename 自动改名。
```
退出码：2

**场景 B：自动改名成功**
```
✅ 已导入快照: Q2-final-review_1
  冲突策略: 自动改名
  来源批次: 交付样例-2026-Q2
  问题数量: 10
  导入时间: 2026-06-13T09:00:00
```

#### 6. 删除快照

```bash
dc.bat snapshot delete "Q2-mid-review"
```

**输出：**
```
✅ 已删除快照: Q2-mid-review
```

### 快照文件结构

```
.delivery_check/
  snapshots/
    index.json              # 快照索引（元数据列表）
    snapshots.log           # 操作日志（所有快照操作的审计记录）
    Q2-final-review.snapshot.json    # 快照数据本体
    Q2-mid-review.snapshot.json
```

**索引文件：** 记录所有快照的元数据（名称、说明、来源批次、问题数量、状态分布、创建时间等），重启后仍可列出。

**操作日志：** `snapshots.log` 记录所有快照操作（创建、删除、导入、导出）和错误，用于审计追踪。

**原子写入保证：** 所有写入操作先写 `.tmp` 再 `shutil.move` 替换，中途崩溃不会损坏文件。

### 导出文件格式

```json
{
  "format_version": 1,
  "type": "delivery-checker-snapshot",
  "snapshot": {
    "name": "Q2-final-review",
    "description": "Q2 交付最终复核完成",
    "source_batch_name": "交付样例-2026-Q2",
    "source_rules": {
      "batch_name": "交付样例-2026-Q2",
      "root_alias": "交付资料包",
      "required_files_count": 3,
      "naming_rules_count": 2,
      "ignore_patterns_count": 3,
      "expiry_date": "2026-06-30",
      "source_path": "D:\\work\\delivery\\rules.yaml",
      "source_hash": "a1b2c3d4..."
    },
    "issues": [
      {
        "id": "missing::path::README.md",
        "type": "missing",
        "file_path": "README.md",
        "description": "必交付文档不存在",
        "status": "passed",
        "reviewer": "张三",
        "review_note": "已确认",
        "reviewed_at": "2026-06-12T14:20:00"
      }
    ],
    "status_distribution": {
      "pending": 0,
      "passed": 8,
      "ignored": 2,
      "todo": 0
    },
    "issue_count": 10,
    "data_dir": "D:\\work\\delivery\\sample_data",
    "rules_path": "D:\\work\\delivery\\rules.yaml",
    "created_at": "2026-06-12T15:30:00",
    "updated_at": "2026-06-12T15:30:00"
  }
}
```

### 错误码与边界处理

| 退出码 | 场景 | 示例 |
|-------|------|------|
| **0** | 成功 | 创建/列出/查看/导出/导入/删除成功 |
| **1** | 快照不存在 / 来源批次不存在 / 导入文件不存在 | `snapshot show no-snapshot` → 快照不存在 |
| **2** | 快照文件损坏 / JSON 格式错误 / 名称冲突（refuse 策略） | 导入坏 JSON 文件 |
| **3** | 快照名称冲突（创建时） | 创建同名快照 |
| **4** | 权限不足（读/写/删除失败） | 目标目录无写权限 |
| **5** | 其他创建/导入失败 | 来源批次读取失败 |

#### 典型场景示例

```bash
# 场景 1：来源批次不存在
dc.bat snapshot create -n test -b no-such-batch
# ❌ 来源批次不存在: 来源批次不存在: no-such-batch
# 退出码: 1

# 场景 2：导入坏 JSON 文件
echo "this is not json" > bad.snapshot.json
dc.bat snapshot import bad.snapshot.json
# ❌ 导入文件格式错误: JSON 解析失败 bad.snapshot.json: Expecting property name...
# 退出码: 2

# 场景 3：快照文件损坏（手动破坏）
echo "not valid" > .delivery_check/snapshots/corrupt.snapshot.json
# 手动在 index.json 中添加对应条目后
dc.bat snapshot show corrupt
# ❌ 快照文件损坏: 快照缺少必填字段: name, description, ...
# 退出码: 2

# 场景 4：同名创建冲突
dc.bat snapshot create -n "Q2-final-review" -b "交付样例-2026-Q2"
# ❌ 快照名称冲突: 快照「Q2-final-review」已存在。
# 使用 --force 覆盖，或选一个新名称。
# 退出码: 2
```

> **重要保证**：快照操作是**完全隔离**的，**绝不会修改原批次、撤销栈、规则包索引或视图预设**。即使快照操作失败，原有数据完整无损。

### 常用工作流示例

#### 工作流 1：交付前留档

```bash
# 1. 扫描并复核
dc.bat scan rules.yaml ./delivery_pkg --name release-v2
# ... 交互式复核 ...

# 2. 复核完成后创建快照留档
dc.bat snapshot create ^
  -n "release-v2-final" ^
  -d "v2 版本交付前最终快照，所有问题已处理" ^
  -b "release-v2"

# 3. 导出交付给客户
dc.bat snapshot export "release-v2-final" ./deliverables/release_v2_snapshot.json
```

#### 工作流 2：跨环境迁移快照

```bash
# 开发机：导出快照
dc.bat snapshot export "release-v2-final" ./transfer/release_v2.snapshot.json

# 服务器：导入快照
scp ./transfer/release_v2.snapshot.json server:/work/archives/
ssh server "cd /work && dc.bat snapshot import ./archives/release_v2.snapshot.json"

# 服务器：验证导入
dc.bat snapshot list
dc.bat snapshot show "release-v2-final"
```

#### 工作流 3：审计回溯（跨重启可用）

```bash
# 周一：创建快照
dc.bat snapshot create -n "week1-audit" -d "第 1 周审计快照" -b "audit-week1"

# 周五：重启后仍可读取
dc.bat snapshot list
# （week1-audit 仍在列表中）

dc.bat snapshot show "week1-audit" --verbose
# 可以查看当时每个问题的状态、复核人、备注
```

---

## 💾 工作区备份与恢复

把检查工具的可迁移状态（批次历史、规则包、视图预设、快照、对比配置）打成备份包，也能在另一台机器或另一份工作目录里恢复。所有备份保存在 `.delivery_check/backups/` 目录，**跨重启依然可用**，且**备份操作绝不会修改原数据**。

### 备份包含内容

创建备份时可选择包含以下内容（默认全部包含）：

| 内容 | 说明 | 排除参数 |
|------|------|---------|
| 批次历史 | 所有 `.state.json` 文件（含复核状态、撤销栈） | `--no-batches` |
| 规则包 | 所有已保存的规则包及索引 | `--no-rules` |
| 视图预设 | 所有已保存的视图预设及索引 | `--no-presets` |
| 快照 | 所有已保存的快照及索引 | `--no-snapshots` |
| 对比配置 | 所有已保存的对比配置及索引 | `--no-compare` |

### 快速上手

#### 1. 创建备份

```bash
# 完整备份（包含全部内容）
dc.bat backup create -n "v2交付前备份" -d "v2版本交付前完整备份"

# 仅备份批次历史和规则包
dc.bat backup create -n "规则+批次" -d "仅规则和批次" ^
  --no-presets --no-snapshots --no-compare
```

**输出：**
```
✅ 备份已创建: v2交付前备份
   说明: v2版本交付前完整备份
   包含: 批次历史, 规则包, 视图预设, 快照, 对比配置
   内容: {'batch_count': 3, 'rule_package_count': 2, 'view_preset_count': 1, 'snapshot_count': 1, 'compare_config_count': 1}
   创建时间: 2026-06-12T16:00:00
```

#### 2. 列出所有备份

```bash
dc.bat backup list
```

**输出：**
```
  #  名称                  大小       创建时间               包含内容
----------------------------------------------------------------------------------------------------
  1  v2交付前备份          12.5 KB    2026-06-12T16:00:00    批次,规则包,预设,快照,对比
     v2版本交付前完整备份
     来源: MY-COMPUTER
```

#### 3. 查看备份详情

```bash
dc.bat backup show "v2交付前备份"
```

**输出：**
```
📦 备份「v2交付前备份」详情
  说明: v2版本交付前完整备份
  体积: 12.5 KB (12800 字节)
  创建时间: 2026-06-12T16:00:00
  更新时间: 2026-06-12T16:00:00
  包含内容: 批次历史, 规则包, 视图预设, 快照, 对比配置
  内容摘要: {'batch_count': 3, 'rule_package_count': 2, ...}
  来源摘要: 目录: D:\work\delivery | 主机: MY-COMPUTER | 工具版本: 1.0.0
  格式版本: v1
```

#### 4. 导出备份

```bash
# 导出为 JSON 文件
dc.bat backup export "v2交付前备份" ./transfer/backup.backup.json

# 导出为 ZIP 文件（推荐，体积更小）
dc.bat backup export "v2交付前备份" ./transfer/backup.zip -f zip
```

#### 5. 导入备份（另一台机器或工作目录）

```bash
# 导入（默认策略：同名拒绝）
dc.bat backup import ./transfer/backup.backup.json

# 同名时自动改名（如 v2交付前备份_1）
dc.bat backup import ./transfer/backup.backup.json --conflict rename

# 同名时覆盖已有备份
dc.bat backup import ./transfer/backup.backup.json --conflict overwrite

# 导入 ZIP 格式（自动识别）
dc.bat backup import ./transfer/backup.zip

# 导入时指定新名称
dc.bat backup import ./transfer/backup.backup.json --rename-name "新机器备份"
```

**场景 A：同名拒绝**
```
⚠️  备份「v2交付前备份」已存在。
   当前策略: 拒绝
   使用 --conflict overwrite 覆盖，或 --conflict rename 自动改名。
```
退出码：3

**场景 B：自动改名成功**
```
✅ 备份已导入: v2交付前备份_1
  冲突策略: 自动改名
  内容摘要: {'batch_count': 3, ...}
  导入时间: 2026-06-13T09:00:00
```

#### 6. 从备份恢复数据

恢复前会**先预览差异**，让你看清楚哪些是新增、哪些有冲突，再确认是否执行。

```bash
# 预览差异（不执行恢复）
dc.bat backup restore "v2交付前备份" --dry-run

# 恢复（跳过冲突项，仅添加新数据）
dc.bat backup restore "v2交付前备份" --conflict skip

# 恢复（备份覆盖当前冲突项）
dc.bat backup restore "v2交付前备份" --conflict overwrite

# 仅预览不执行
dc.bat backup restore "v2交付前备份" --dry-run
```

**预览输出示例：**
```
📋 备份「v2交付前备份」恢复差异预览

  批次历史: 新增 2  冲突 1  无变化 0
    ➕ batch-new-1
    ⚠️  batch-old
       当前: name=batch-old | ...
       备份: name=batch-old | ...
  规则包: 新增 0  冲突 0  无变化 2

汇总: 新增 2  冲突 1  无变化 2

⚠️  检测到数据冲突！
  冲突策略: 跳过（保留当前，仅添加新项）

确认执行恢复？ (y/N):
```

恢复冲突策略：

| 策略 | 说明 |
|------|------|
| `skip`（默认） | 跳过冲突项，保留当前数据，仅添加新项 |
| `overwrite` | 备份数据覆盖当前冲突项 |
| `abort` | 存在任何冲突即中止恢复 |

#### 7. 删除备份

```bash
dc.bat backup delete "v2交付前备份"
```

### 备份文件结构

```
.delivery_check/
  backups/
    index.json                     # 备份索引（元数据列表）
    backups.log                    # 操作日志（审计记录）
    v2交付前备份.backup.json        # 备份数据本体
```

**索引文件：** 记录所有备份的元数据（名称、说明、大小、创建时间、包含内容、来源等），重启后仍可列出。

**操作日志：** `backups.log` 记录所有备份操作（创建、删除、导入、导出、恢复）和错误，用于审计追踪。

**原子写入保证：** 所有写入操作先写 `.tmp` 再 `shutil.move` 替换，中途崩溃不会损坏文件。

### 导出文件格式

**JSON 格式：**
```json
{
  "format_version": 1,
  "type": "delivery-checker-backup",
  "manifest": {
    "name": "v2交付前备份",
    "description": "v2版本交付前完整备份",
    "created_at": "2026-06-12T16:00:00",
    "source_dir": "D:\\work\\delivery",
    "source_hostname": "MY-COMPUTER",
    "tool_version": "1.0.0",
    "format_version": 1,
    "include_batches": true,
    "include_rule_packages": true,
    "include_view_presets": true,
    "include_snapshots": true,
    "include_compare_configs": true,
    "content_summary": {"batch_count": 3, ...},
    "total_size_bytes": 12800
  },
  "data": {
    "batches": { ... },
    "rule_packages": { ... },
    "view_presets": { ... },
    "snapshots": { ... },
    "compare_configs": { ... }
  }
}
```

**ZIP 格式：** 包内含一个 `backup.json`，内容与 JSON 格式相同。

### 错误码与边界处理

| 退出码 | 场景 | 示例 |
|-------|------|------|
| **0** | 成功 | 创建/列出/查看/导出/导入/恢复/删除成功 |
| **1** | 备份不存在 / 导入文件不存在 | `backup show no-such` → 备份不存在 |
| **2** | 格式错误（坏 JSON / 缺字段 / 无效策略 / 空名称） | `backup create -n ""` → 格式错误 |
| **3** | 创建同名冲突 / 导入同名拒绝 / 恢复冲突中止 | `backup create -n dup` → 名称冲突 |
| **4** | 权限不足（读/写/删除失败） | 目标目录无写权限 |
| **5** | 备份文件损坏 | 手动破坏备份 JSON 后 show |
| **6** | 版本不兼容 | 导入未来版本备份 |

#### 典型场景示例

```bash
# 场景 1：备份文件损坏
# 手动破坏 .delivery_check/backups/corrupt.backup.json 后
dc.bat backup show corrupt
# ❌ 备份文件损坏: JSON 解析失败 ...
# 退出码: 5

# 场景 2：导入坏 JSON 文件
echo "not json" > bad.backup.json
dc.bat backup import bad.backup.json
# ❌ 备份文件损坏: JSON 格式错误 ...
# 退出码: 5

# 场景 3：导入非备份文件
dc.bat backup import some-other.json
# ❌ 格式错误: 导入文件不是有效的 delivery-checker 备份文件
# 退出码: 2

# 场景 4：导入版本不兼容
dc.bat backup import future-backup.json
# ❌ 版本不兼容: 备份格式版本 v999 高于当前工具支持版本 v1，请升级工具
# 退出码: 6

# 场景 5：目录无写权限
dc.bat backup create -n "test"
# ❌ 权限错误: 无法创建目录 ...: Permission denied
# 退出码: 4
```

> **重要保证**：备份操作是**只读**的，**绝不会修改原批次、撤销栈、规则包索引、视图预设或快照**。即使备份操作失败，原有数据完整无损。恢复操作需手动确认，冲突时不会直接覆盖。

### 常用工作流示例

#### 工作流 1：跨机器迁移

```bash
# 机器 A：创建并导出备份
dc.bat backup create -n "项目迁移" -d "从开发机迁移到服务器"
dc.bat backup export "项目迁移" ./transfer/project.zip -f zip

# 机器 B：导入并恢复
dc.bat backup import ./transfer/project.zip
dc.bat backup restore "项目迁移" --conflict skip
```

#### 工作流 2：交付前快照+备份双保险

```bash
# 1. 创建快照（审计留档）
dc.bat snapshot create -n "v2-final" -b "release-v2"

# 2. 创建完整备份（可恢复）
dc.bat backup create -n "v2-交付前" -d "v2交付前完整备份"

# 3. 导出备份到安全位置
dc.bat backup export "v2-交付前" ./archives/v2-backup.zip -f zip
```

#### 工作流 3：跨重启使用（持久化）

```bash
# 第一次：创建备份
dc.bat backup create -n "daily" -d "每日备份"

# 重启后：备份仍在
dc.bat backup list
# （daily 仍在列表中）

dc.bat backup show "daily"
# 可以查看大小、内容、来源等完整信息
```

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
| `dc.bat compare --a <批次A> --b <批次B> [--a-latest N] [--b-latest N] [-o 输出] [-f json\|csv\|auto] [-c overwrite\|rename\|refuse] [-s 配置名]` | 批次对比（新增/消失/状态/处理人变化） |
| `dc.bat compare-save -n <名称> [-d 说明] [--a/--a-latest N] [--b/--b-latest N] [-f 格式] [-o 路径] [-c 策略] [--force]` | 保存对比配置 |
| `dc.bat compare-run <配置名> [--a/--b/--a-latest/--b-latest 覆盖] [-o 输出] [-f 格式] [-c 策略]` | 按命名配置运行对比 |
| `dc.bat compare-list` | 列出所有对比配置 |
| `dc.bat compare-show <名称>` | 查看对比配置详情 |
| `dc.bat compare-delete <名称>` | 删除对比配置 |
| `dc.bat snapshot create -n <名称> -d <说明> -b <批次>` | 从批次创建快照 |
| `dc.bat snapshot list` | 列出所有快照 |
| `dc.bat snapshot show <名称> [--verbose]` | 查看快照详情（可选显示问题详情） |
| `dc.bat snapshot export <名称> [输出路径]` | 导出快照为 JSON 文件 |
| `dc.bat snapshot import <文件> [--conflict overwrite\|rename\|refuse] [--rename-name <新名称>]` | 导入快照，支持三种冲突处理策略 |
| `dc.bat snapshot delete <名称>` | 删除快照 |
| `dc.bat backup create -n <名称> [-d 说明] [--no-batches] [--no-rules] [--no-presets] [--no-snapshots] [--no-compare]` | 创建工作区备份 |
| `dc.bat backup list` | 列出所有备份 |
| `dc.bat backup show <名称>` | 查看备份详情（体积/时间/内容/来源） |
| `dc.bat backup export <名称> [输出路径] [-f json\|zip]` | 导出备份为 JSON 或 ZIP 文件 |
| `dc.bat backup import <文件> [--conflict overwrite\|rename\|refuse] [--rename-name <新名称>]` | 导入备份，支持三种冲突处理策略 |
| `dc.bat backup restore <名称> [--conflict overwrite\|skip\|abort] [--dry-run]` | 从备份恢复数据（先预览差异再确认） |
| `dc.bat backup delete <名称>` | 删除备份 |
| `dc.bat --no-color ...` | 禁用彩色输出（日志/管道场景） |

**退出码说明**：

| 退出码 | 含义 |
|--------|------|
| 0 | 成功 |
| 1 | 目录不存在 / 文件不存在 / 预设不存在 / 批次不存在 / 快照不存在 / 备份不存在 / 运行时错误 |
| 2 | 规则配置格式/语义错误 / 规则包或预设或对比配置或快照或备份格式错误（坏 JSON / 缺字段） / 快照导入名称冲突（refuse 策略） / 备份名称为空 |
| 3 | 重复扫描 / 规则/目录不一致 / 规则包或预设或对比配置或快照或备份同名冲突 / 导出文件拒绝覆盖 / 恢复冲突中止 |
| 4 | 状态文件读写失败 / 权限不足 / 导出目录无写权限 / 快照或备份权限不足 |
| 5 | 快照创建/导入其他失败 / 备份文件损坏 |
| 6 | 备份版本不兼容 |
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
