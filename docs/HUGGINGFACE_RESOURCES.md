# Hugging Face Resources

这份说明用于把 `ComfyUI Studio Suite` 的大资源单独托管到 Hugging Face。

推荐的 Hugging Face 仓库类型：

- `Dataset`

推荐的仓库名：

- `ComfyUI-Studio-Suite-Resources`

推荐的完整地址：

- `https://huggingface.co/datasets/onglon114514/ComfyUI-Studio-Suite-Resources`

## 为什么要单独托管

这些资源不适合直接放在 GitHub 代码仓库里：

- 文件大
- 很多是生成物或统计表
- 更适合用 Git LFS 分发
- 不需要每个拉代码的人都同步

## 建议托管到 HF 的资源

主要是这些文件：

- `danbooru_character_aliases.generated.json`
- `danbooru_character_webui.normalized.jsonl`
- `danbooru_character_webui.xlsx`
- `danbooru_tags_cooccurrence.csv`
- `danbooru_artist_wildcard-D站画师列表.txt`
- `tag count.xlsx`
- `tag_count_tags_统计.json`
- `tag_count_tags_统计.jsonl`
- `tag_count_channels_统计.json`
- `tag_count_channels_统计.jsonl`
- `Danbooru服装查询资源_本地版_2026-05-26.jsonl`
- `Danbooru服装查询资源_层级版_2026-05-26.txt`
- `服装生成模板_system_legacy.md`
- `danbooru_character_webui.columns.json`

## 一键生成本地 HF 资源目录

在项目根目录执行：

```powershell
python scripts/build_hf_resource_bundle.py
```

默认会生成：

```text
dist/comfyui_studio_suite_hf_resources
```

这个目录里会自动包含：

- 要上传的资源文件
- `README.md`
- `.gitattributes`
- `resource_bundle_manifest.json`

## 在 Hugging Face 网页先创建仓库

你当前账号：

- `https://huggingface.co/onglon114514`

下一步在网页创建一个 Dataset 仓库，建议名字：

- `ComfyUI-Studio-Suite-Resources`

## Git LFS 上传步骤

进入打包后的目录：

```powershell
cd dist/comfyui_studio_suite_hf_resources
```

初始化仓库并提交：

```powershell
git init
git lfs install
git add .
git commit -m "Initial resource release"
git branch -M main
```

添加 Hugging Face 远端：

```powershell
git remote add origin https://huggingface.co/datasets/onglon114514/ComfyUI-Studio-Suite-Resources
```

推送：

```powershell
git push -u origin main
```

## 登录说明

如果命令行推送时要求认证：

- `Username` 填你的 Hugging Face 用户名
- `Password` 不填网页登录密码
- 这里应填 Hugging Face 的 `User Access Token`

也就是说，浏览器登录态不能直接替代 Git 推送认证。

## 推送成功后怎么接回主项目

主代码仓库 README 可以再补一个资源下载入口，例如：

- Hugging Face Dataset: `onglon114514/ComfyUI-Studio-Suite-Resources`

节点用户下载后，只需要把文件放回主项目的 `resources/` 目录，文件名保持一致即可。

## 当前结论

最稳的方案不是把资源和代码混在一个 GitHub 仓库里，而是：

- GitHub 仓库存代码和轻资源
- Hugging Face Dataset 仓库存大资源
- 两边通过 README 和文档互相说明
