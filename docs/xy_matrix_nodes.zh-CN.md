# Studio Suite XY 测试节点

这组节点用于做参数矩阵测试，不绑定旧的效率加载器，也不接管模型加载链。它的逻辑是：先指定“要修改哪个节点 ID 的哪个输入”，再由 XY 队列复制当前工作流，为每个格子提交一个独立子任务。

## 基本串接

1. 在原工作流里保留正常生图链路。
2. 最后把图片接到 `Independent Result Writer (Proxy)`。
3. 新建一个或两个 `Studio Suite XY Axis ...` 节点。
4. 把轴节点接到 `Studio Suite XY Matrix`。
5. 把 `matrix_json` 接到 `Studio Suite XY Queue`。
6. 在 `Studio Suite XY Queue` 的 `writer_node_id` 填 `Independent Result Writer (Proxy)` 的节点编号。
7. 运行 `Studio Suite XY Queue`，它会提交每个格子的独立子任务。
8. 子任务完成后，用 `Studio Suite XY Grid Builder` 读取 manifest 生成汇总图。

## 用连线指定目标节点

多数轴节点都保留了 `target_node_id` 手填方式，但也支持用 `target_ref` 连线指定目标。连接后 `target_ref` 优先于手填编号。

普通 LoRA Loader 推荐接法：

```text
LoraLoader MODEL -> Studio Suite XY Target Bridge - Model/Clip MODEL -> 后续模型链
LoraLoader CLIP  -> Studio Suite XY Target Bridge - Model/Clip CLIP  -> 后续文本编码链
Studio Suite XY Target Bridge - Model/Clip target_ref -> LoRA File / LoRA Strength 轴 target_ref
```

只有 MODEL 的 LoRA Loader 推荐接法：

```text
LoraLoaderModelOnly MODEL -> Studio Suite XY Target Bridge - Model MODEL -> 后续模型链
Studio Suite XY Target Bridge - Model target_ref -> LoRA File / LoRA Strength 轴 target_ref
```

这个桥接节点不改变模型或 CLIP，只是从工作流连接关系里反查上游 LoRA Loader 的节点编号。多个 LoRA 堆叠时，把桥接节点放在你要测试的那个 LoRA Loader 后面。

## 通用轴

节点：`Studio Suite XY Axis - Generic`

适合测试 `steps`、`cfg`、denoise、seed 等单输入参数，也可以一次改多个输入。

单输入示例：

```text
20
30
40
```

或带标签：

```text
low steps|18
mid steps|28
high steps|40
```

多输入示例，`input_names` 填 `width,height`：

```text
square|1024|1024
portrait|1024|1536
wide|1536|1024
```

## 采样器/调度器轴

节点：`Studio Suite XY Axis - Sampler/Scheduler`

目标一般是 KSampler 或兼容采样节点。默认输入名是：

```text
sampler_name
scheduler
```

每行格式：

```text
显示名|sampler_name|scheduler
```

## FreeU 轴

节点：`Studio Suite XY Axis - FreeU`

目标是 FreeU 节点。默认输入名是：

```text
b1,b2,s1,s2
```

每行格式：

```text
显示名|b1|b2|s1|s2
```

## LoRA 强度轴

节点：`Studio Suite XY Axis - LoRA Strength`

目标是已有的 LoRA Loader 或兼容 LoRA 节点。默认输入名是：

```text
strength_model
strength_clip
```

`strengths_text` 支持逗号或换行：

```text
0, 0.25, 0.5, 0.75, 1.0
```

当前版本只测试 LoRA 总强度。LoRA 分层/分块强度需要后续新增专用 LoRA Loader，不建议在第一版里强行塞进通用轴。

## LoRA 文件轴

节点：`Studio Suite XY Axis - LoRA File`

适合测试同一训练任务保存出来的多个 LoRA 文件，例如 20 个不同 step/epoch/checkpoint。它会修改已有 LoRA Loader 的 `lora_name` 输入。

常用填法：

```text
target_node_id: 填 LoRA Loader 节点编号
lora_name_input: lora_name
include_filter: 训练名关键词或通配符
exclude_filter: 不想测试的关键词，可留空
limit: 0 表示不限制
```

`include_filter` 支持逗号分隔的关键词或通配符：

```text
my_character_lora
my_character_lora*.safetensors
epoch_*, step_*
```

如果不想自动扫描，也可以在 `lora_names_text` 手动写：

```text
epoch 10|my_lora_epoch10.safetensors
epoch 20|my_lora_epoch20.safetensors
epoch 30|my_lora_epoch30.safetensors
```

推荐测试方式：

```text
X轴：Studio Suite XY Axis - LoRA File
Y轴：Studio Suite XY Axis - LoRA Strength
```

这样可以同时看“哪个 LoRA 保存时间段更好”和“哪个加载权重更合适”。

## 输出文件

`Studio Suite XY Queue` 会在输出目录写入：

```text
xy_manifest_YYYYMMDD_HHMMSS.json
```

`Studio Suite XY Grid Builder` 可以读取这个 manifest，把已完成的格子拼成汇总图。建议子任务全部完成后再运行 Grid Builder。

## 注意

- `target_node_id` 必须填 ComfyUI 节点右上角显示的编号。
- `input_names` 必须填该节点真实输入字段名，不是 UI 翻译名。
- 如果采样器/调度器名称不在当前 ComfyUI 可用列表里，子任务会在 prompt 校验阶段失败。
- XY 队列每个格子都会独立提交 prompt，适合配合 `cleanup_after_save` 做长批量稳定测试。
