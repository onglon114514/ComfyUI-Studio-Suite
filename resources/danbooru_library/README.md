# Prompt Studio Danbooru 词典资源

此目录保存 Prompt Studio 公共词典的分类骨架、活动清单和生成的只读
SQLite 数据库。个人快捷标签仍位于
`prompt_studio/storage/group_tags/zh_CN.yaml`，构建器不会移动、删除或重写它。

## 运行时文件

- `manifest.json` 指向当前 `catalog-<version>.sqlite3`，两者必须成套分发。
- Prompt Studio 只读 SQLite；不会在浏览器中加载全库，也不需要 PyArrow。
- GitHub 源码包默认排除大型 SQLite。发布时应将当前 manifest 与对应数据库放入
  Hugging Face 资源包，不能只上传 manifest。
- 旧数据库不会自动删除，以保护仍在读取旧版本的请求。确认 ComfyUI 和构建器均已
  停止后，维护者才可清理 manifest 未引用的旧版本。

## 当前数据边界

当前完整底座来自
[`baton4ik/danbooru-tag-metadata-snapshot`](https://huggingface.co/datasets/baton4ik/danbooru-tag-metadata-snapshot)
的固定版本 `e81b8d2e62fc46dfe70ebbb0223420d6bb94cafe`。该数据集标注 MIT，
并说明数据来自 Danbooru tags、tag aliases 和 tag implications API。

- 源快照共有 1,711,015 行 canonical tag。
- 交互库排除 3,862 个 deprecated tag、6 个畸形超长/控制字符名称，并合并
  8 个重复 canonical，得到 1,707,139 个官方有效唯一 tag。
- 只导入 38,207 条可解析的 active alias；retired/deleted alias 不用于自动匹配。
- 保留 41,149 条 active implication，用于可追溯的语义导航。
- 本地并集还保留已有译名、角色、画师、服装和共现资源，因此总数会略高于官方
快照唯一数。每个来源的精确 SHA256、行数和筛选结果以 `manifest.json` 为准。

服装细分类另外来自
[`SAkizuki/danbooru_wikis_2026`](https://huggingface.co/datasets/SAkizuki/danbooru_wikis_2026)
固定版本 `10f97c0fdc749898c6393e08a97591338d1c6204` 的
`wiki_pages_260310.parquet`。构建器从 39 个维护者选定页面提取 Wiki 章节，
建立“服装与配饰 → 页面/部位 → 款式/颜色/材质/动作”的实用导航。它是便于写
提示词的人工维护分类，不宣称是 Danbooru 官方本体；同一 tag 可以合理出现在多个
分类中。

“完整底座”表示该固定日期快照中的有效 canonical tag，不表示实时同步 Danbooru，
也不表示每个 tag 都有中文译名或人工语义分类。Danbooru API 类别仅包括 general、
artist、copyright、character、meta，不能代替动作、服装等细粒度主题分类。

## 分类可信度

- 原有服装分组是人工资源映射，保持不变。
- `服装与配饰（Danbooru Wiki）` 使用 Wiki 章节骨架与人工中文归并；当前有
  611 个分组、2,937 个去重成员，覆盖鞋、上下装、裙装、袖型、腿袜、手套、头饰、
  首饰、泳装、内衣、传统服饰、制服和时尚风格等。
- `鞋类（Danbooru 关系）` 从 active implication 关系反向推导，来源可追溯。
- `鞋类候选（至少 10 图，未审核）` 只按名称边界和最低 post count 建议，可能
  仍有误收，不能当成官方分类。
- 作品筛选来自 copyright tag 和角色资源携带的 copyright 元数据。
- 官方类型、作品、A-Z、热度排序和页码筛选都在 SQLite 中执行，不会把百万条记录
  传给浏览器。

## 维护者重建

基础本地资源构建：

```powershell
python -B scripts/build_tag_library.py
```

导入 qdlabs 的筛选快照：

```powershell
python -B scripts/augment_tag_library_catalog.py <tags.jsonl>
```

导入当前完整 Parquet 快照：

```powershell
python -B scripts/augment_tag_library_full_snapshot.py <snapshot-directory>
```

导入 Wiki 服装分类：

```powershell
python -B scripts/augment_tag_library_wiki_taxonomy.py <wiki_pages_260310.parquet>
```

完整快照构建阶段需要 PyArrow，但这是维护依赖，不是节点运行依赖。所有构建均先写
新数据库，完成 `quick_check`、外键检查和 fsync 后再原子切换 manifest；临时排序
目录固定在资源目录所在磁盘，避免占满系统盘。
