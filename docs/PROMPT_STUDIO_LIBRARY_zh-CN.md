# Prompt Studio 公共词典库

这是独立于个人快捷标签的离线候选词典。原有 `prompt_studio/storage/group_tags` 不移动、不删除、不自动重写。

## 怎么使用

1. 打开 Prompt Studio，点击顶部的 **词典库**。
2. 输入英文、中文译名或别名的开头，可搭配 Danbooru 类型、作品和语义分组筛选。分类使用“分类库 → 分类页 → 细分类”三级联动；选择 `服装与配饰（Danbooru Wiki）` 后可继续进入鞋类、裙装、裤装、头饰、袜类、配饰、制服或时尚风格。当前搜索是前缀搜索，不是任意位置模糊搜索。
3. 默认按热度显示，每页 40 项。界面显示当前页、总页数和总条数，可直接输入页码跳转，也可用 A-Z / 0-9 快速缩小范围。
4. 点 **详情 / 相关** 查看译名、别名、来源和最多 20 条共现参考。
5. 明确选择 **插入正向 / 插入反向 / 插入当前 XML 字段**，再点击词条的 **插入**。XML 模式需先打开一个字段编辑器。
6. 已有提示词的交互标签上悬停可出现 **查词**，直接定位到公共库。

浏览不会修改提示词。共现只代表源数据里一起出现过，不是译名、必然属性，也不会自动加入提示词。

如需保留为个人常用项，在详情中选 **加入我的快捷标签**，选择原库已有分组并确认。已存在的标签不覆盖；旧界面同时修改过分组时拒绝旧版本写入。重新打开 Prompt Studio 可见新增项。

A/B 对照按钮会优先查询同一个本地词典，缺词沿原有 HY-MT 路径处理。多义别名不自动猜人物，需到词典详情确认。对照不修改实际输出文本，模型调用仍遵守队列忙时禁用的保护。

## 安装与更新索引

词典库本体（`manifest.json` + `catalog-<版本>.sqlite3`，约 1.6 GiB）不随 Git 源码分发，作为 Hugging Face Dataset 资源单独发布。普通用户**不要**自己构建，直接用安装器：

```powershell
python -B scripts/install_tag_library.py
```

请使用 ComfyUI 实际运行的 Python 执行。默认从 Hugging Face 仓库 `onglon114514/ComfyUI-Studio-Suite-Resources` 的 `danbooru_library/` 子目录下载；已经是最新版时会直接提示并退出，不重复下载。

常用参数：

```powershell
python -B scripts/install_tag_library.py --check     # 只报告是否有更新，不下载
python -B scripts/install_tag_library.py --force     # 即使版本相同也重新下载校验
python -B scripts/install_tag_library.py --revision <分支或tag>   # 指定 HF revision
```

`--source` 接受三种形态：

- 缺省或 Hugging Face 仓库 id（如 `onglon114514/ComfyUI-Studio-Suite-Resources`）：走 HF 的 LFS 元数据校验大小与 SHA-256；
- `http(s)://` 镜像地址：从镜像根目录取 `manifest.json` 与 catalog，若镜像同时提供 `resource_files.json`（含每个文件的 size/sha256）则按它校验；
- 本地目录路径：离线安装。把发布目录里的 `manifest.json` 和 `catalog-<版本>.sqlite3` 成套拷贝到一个文件夹，再执行 `python -B scripts/install_tag_library.py --source "D:\path\to\dir"`。离线目录只有大小与可读性校验，没有 SHA-256 时会明确给出提示。

校验与替换的行为边界：

- 下载先落到 `.<文件名>.part`，通过大小、SHA-256（来源提供时）和 SQLite 可读性三重校验后才原子替换为正式文件；`manifest.json` 永远最后写入。
- 任何一步失败都会保留现有可用库，退出码非 0，并打印“已保留现有词典库”。残留的 `.part` 文件仅供下次断点续传，可手动删除。
- 升级不会删除旧 catalog，新旧文件并存；确认新库可用后可自行清理旧版本。
- 安装器**绝不自动构建词典库**。`manifest.json` 与 catalog 必须成套，不能只复制 manifest。

### 自行构建（仅发布方/进阶）

在节点目录，用 ComfyUI 使用的 Python 执行：

```powershell
python -B scripts/build_tag_library.py
```

脚本只读取已存在的本地资源，不联网、不下载模型。缺资源会在清单中标出；少量内置资源也能建小库，完整覆盖需要另外安装资源文件。可用 `--small` 主动跳过角色、画师、共现等大资源。

```powershell
python -B scripts/build_tag_library.py --source-package "源节点目录" --output-dir "目标节点目录/resources/danbooru_library"
```

索引目录包含 `categories.v1.json`、`manifest.json` 和 manifest 指向的 `catalog-<版本>.sqlite3`。不要把尚未完成的 `.building.sqlite3` 当成资源包。构建请在跑图空闲时手动执行，预留数 GB 磁盘空间；更新先构建新库，再原子切换清单，失败不替换旧库；确认没有构建进程后才能人工处理遗留的 `build.lock`。

构建完成后如何上传到 Hugging Face 供安装器分发，见 [HUGGINGFACE_RESOURCES.md](HUGGINGFACE_RESOURCES.md)。

## 当前覆盖与限制

2026-09-08 的本地资源构建：1,708,300 个并集词条、503,339 条别名关联、3,236,959 条共现关系、64,731 个作品索引和 646 个分类，数据库约 1.61 GiB。完整底座来自 2026-04-08 的固定 Danbooru API 元数据快照：1,711,015 个源记录中排除 3,862 个 deprecated、6 个畸形名称并合并 8 个重复 canonical，得到 1,707,139 个有效唯一 tag。它是固定日期快照，不是实时全站同步。

官方类别已区分 general、artist、copyright、character、meta；本地额外词条缺少类别时仍保留“未确认”。新增 Wiki 服装导航选取 39 个页面并形成 611 个实用分组、2,937 个去重成员，采用 Wiki 章节骨架和人工中文归并，不冒充严格官方分类。鞋类分为原人工组、Wiki 款式组、Danbooru active implication 关系组和明确标注“未审核”的名称候选组，来源和可信度保持分离。来源显示名称不保证都是已经审核的中文译名。

大型 generated 别名 JSON 尚未纳入；角色 JSONL、双语 CSV、画师触发词和小型人工别名已经纳入。无法确认语料来源的 `tag_count` 不混作全站频次，作品字段不凭空拆成新的标准标签。缺共现或译名不妨碍普通查询和插入。

浏览器只接收分页结果，不读取整库；关闭面板撤销请求并删除卡片 DOM，没有词典后台轮询、持续动画或模型预热。Task Agent 的旧共现调用暂未切换到这个索引，后续单独回归后再接入。

## 本轮验证

20 项索引单元测试、2 项 Wiki 解析测试、6 项 API 测试和已有词典/双语回归。浏览器覆盖三级分类、总页数、页码跳转、A-Z、作品、类型、正反向和 XML 插入、显式快捷库写入、过期请求丢弃、缺索引提示；连续打开关闭 50 次，DOM 数量保持不变。

真实大资源的 HTTP 查询已单独测量，本机这一轮分页/中英文搜索/共现查询约 16-31 毫秒（不含前端防抖及绘制时间），并在独立浏览器连接当前 ComfyUI 验证；不运行模型、不写入实际用户工作流或快捷库。这验证了新功能的有界加载，不代表已证明 Chrome 的所有原生崩溃原因消失。
