# Danbooru 词典资源审计

审计日期：2026-09-03。子代理对开发项目与实际安装节点的相关资源做只读扫描，父任务复核关键字段样本、文件存在性和官方类别定义。本表是本机盘点，不是已发布资源包的可复现构建清单。

> 2026-09-08 后续更新：已引入固定版本
> [Danbooru Tag Metadata Snapshot](https://huggingface.co/datasets/baton4ik/danbooru-tag-metadata-snapshot)。
> 该 MIT 数据集声明来自 Danbooru tags、aliases、implications API，包含
> 1,711,015 个 tag 源记录、59,259 条 alias 和 49,334 条 implication。
> 当前交互库只采用有效非弃用 canonical、active alias 和 active implication；
> 精确哈希、排除项和版本见 `resources/danbooru_library/manifest.json`。
> 因此下文“类别证据不足”等结论只描述 2026-09-03 的旧本地资源审计状态。
>
> 2026-09-08 分类补充：已采用
> [Danbooru Wikis 2026](https://huggingface.co/datasets/SAkizuki/danbooru_wikis_2026)
> 固定版本的 268,908 页 Wiki 快照作为分类证据。当前由维护者选定 39 个服装与
> 配饰页面，提取章节并人工归并中文入口，生成 611 个实用分类和 2,937 个去重
> 成员。它解决“完整 tag 元数据只有五种官方类别、难以按款式查找”的问题，但仍是
> 面向 Prompt Studio 的导航体系，不宣称逐字复制 Danbooru Wiki 或构成严格本体。

原有 `group_tags`、配置和资源未修改；没有下载新的全站数据，没有实现新 UI。接入方案见 [公共词典库方案](DANBOORU_LIBRARY_PLAN_zh-CN.md)。

## 已确认规模

行数指数据记录，不含 CSV 表头。各行之间有重叠，不能直接相加。

| 资源 | 规模 | 可用字段与注意事项 |
| --- | ---: | --- |
| `prompt_studio/storage/autocomplete/autocomplete.txt` | 100,000 行 / 唯一词 | 词条、计数；不是全站完整导出 |
| `prompt_studio/storage/local_complete_tags/danbooru-0-zh.csv` | 23,799 行 / 唯一词 | 22,267 条含汉字，259 条存在多译名分隔符；译名字符串非空不代表全部是中文 |
| `resources/danbooru_character_webui.normalized.jsonl` | 244,932 记录 / 唯一角色键 | 展示名、作品、触发词、核心词、count/solo_count/url |
| `resources/danbooru_character_aliases.generated.json` | 244,932 角色键 | 1,134,894 条别名关联，834,345 个不同别名 |
| `prompt_studio/storage/cache/danbooru_character_aliases.sqlite3` | 244,980 别名行 / 244,932 角色 | aliases、metadata 两表，不能替代完整的多义别名库 |
| `resources/danbooru_artist_wildcard-D站画师列表.txt` | 418,559 行 / 候选键 | 197,593 条有第二列触发词；数据行缺少表头宣称的 count/url |
| `resources/danbooru_tags_cooccurrence.csv` | 3,236,959 行 / 31,060 端点词 | tag_a、tag_b、count；约 103 MB；重复边及反向重复未统计 |
| `resources/Danbooru服装查询资源_本地版_2026-05-26.jsonl` | 665 节点记录 | 1 根、21 分组、643 tag 记录，对应 632 个唯一 tag |
| `resources/tag_count_tags_统计.json` / `.jsonl` | 各 377,670 条 | 两种格式逐条内容一致；Tag Name / Count，语料来源待核验 |
| `resources/tag_count_channels_统计.json` / `.jsonl` | 各 80 条 | 两种格式逐条内容一致；Channel Name / Count，语料来源待核验 |

开发项目与实际节点的补全表、译名 CSV、服装 JSONL 和层级 TXT 在统一换行后内容一致。主要大资源只存在于实际节点，不能因为本机可用就认为 GitHub 下载包已包含它们。

## 并集与覆盖

- 补全、译名、角色、作品字段、画师和服装合并为 714,226 个候选键；再包含共现端点为 716,365 个。未合并用户快捷标签、别名字符串及来源未明的 tag_count 数据。这不是有效 canonical tag 数。
- 共现端点与补全词表交集为 26,440 个；译名 CSV 与补全词表交集为 22,685 个。
- 角色展示名中 240,260 条含汉字；核心词仅 33,599 条非空。含汉字不代表翻译已审核。
- 作品字段有 22,270 个唯一非空字符串，但不能假定每个字符串都是单一规范作品 tag。

## 导入前必须解决

1. **别名歧义**：50,726 个别名对应多个角色。当前 SQLite 的 alias 单列主键不能完整表达这种关系。新库需保存一对多候选，不在缺少作品限定时自动替用户选角色。
2. **角色异常键**：角色键中出现 3 个整数值；生成别名与角色 JSONL 的键集合各有 1 个不匹配项。具体原因待导入器隔离并报告，不能静默合并。
3. **分类为多对多**：服装数据有 11 个重复 tag ID，落在不同语义分组。应合并词条并保留所有分组关系，不能最后一行覆盖前一行。
4. **类别证据不足**：主要词表没有逐词官方 category。文件名或 tag 括号不够作为官方类别证据，无法确定的留 unknown。
5. **来源与计数不明**：计数的语料范围、时间与过滤规则尚未确认。共现最小计数为 100，但无法据此证明生成阈值。tag_count 中出现 noai/concept art/digital 2d，既不能认定为 Danbooru 全站统计，也不能在没有证据时直接称为本地训练集统计。
6. **分类资源经过筛选**：服装层级文件说明已去掉来源网页链接，部分分支有筛选，不能宣传为完整官方 Wiki tag_groups。

## 分类骨架与后续范围

`resources/danbooru_library/categories.v1.json` 区分原始类别、已有本地语义分组和待建设语义分组。已有服装组从原 JSONL 提取，保留来源；其他分组仅为设计骨架，不包含任何自动推测的全量 tag 分配。

官方 category 编码 0 general、1 artist、3 copyright、4 character、5 meta 的依据为 [Danbooru TagCategory 源码](https://raw.githubusercontent.com/danbooru/danbooru/master/app/logical/tag_category.rb)。这只证实类别定义，不证明现有词条已经逐个验证。

当前缺少可核验的官方快照日期、完整导出范围、逐词类别、官方别名状态与统一来源信息，因此只能称为“现有本地资源候选库”，不能称为“Danbooru 最新全集”。

两个 XLSX 本轮未统计；用户快捷 YAML 未做全量语义解析。后续先做流式导入、歧义记录、分页索引与已有资源可视化，再决定需要补齐哪些官方快照字段。不要以 LLM 猜测代替缺失的原始记录。
