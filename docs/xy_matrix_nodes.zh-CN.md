# Studio Suite XY 测试节点

这组节点用于做参数矩阵测试，不绑定旧的效率加载器，也不接管模型加载链。推荐结构是把主生成链和调度链分开：主生成链用 `Studio Suite XY Parameter Input` 接收变化参数，调度轴按 `slot_name` 注入每个格子的值。节点编号方式只用于兼容旧工作流。

## 基本串接

1. 在原工作流里保留正常生图链路。
2. 最后把图片接到 `Independent Result Writer (Proxy)`。
3. 在需要变化的参数前放置 `Studio Suite XY Parameter Input`，填写唯一的 `slot_name`，再把对应类型输出连到主生成节点。
4. 新建一个或两个新版 XY 轴，轴里的 slot 名与参数入口保持一致。
5. 把轴节点接到 `Studio Suite XY Matrix`。
6. 把 `matrix_json` 接到 `Studio Suite XY Queue`。
7. 工作流里只有一个 Writer Proxy 时，`writer_node_id` 留空即可自动识别。
8. 运行 `Studio Suite XY Queue`。默认会在所有子任务结束后自动生成汇总图，不需要再手动运行 Grid Builder。

XY 子任务只执行 `Independent Result Writer (Proxy)` 所在的生成链。工作流中的其它预览、文本展示或分析输出不会被每个格子重复触发。

## 推荐：参数入口分离主工作流

节点：`Studio Suite XY Parameter Input`

它的作用与 `Independent Load Image Path` 类似：留在实际生成链里，调度器只在复制出来的子任务中改写 `value_json`。例如 LoRA Loader 前放三个入口：

```text
slot_name=lora_name           string  -> LoRA Loader lora_name
slot_name=lora_model_strength float   -> LoRA Loader strength_model
slot_name=lora_clip_strength  float   -> LoRA Loader strength_clip
```

内置节点的下拉框/数值框默认是 widget 时，先右键该字段并选择“转换为输入（Convert widget to input）”，再连接参数入口。文件名等枚举字段使用通用 `value` 输出，强度、步数等数值优先使用 `float` 或 `int` 输出。

`value_json` 是不运行 XY 时的默认值。文件名要写成 JSON 字符串，例如 `"my_lora.safetensors"`；数字可直接写 `1.0`。

通用参数使用 `Studio Suite XY Axis - Parameter Slot`，只要轴和参数入口的 `slot_name` 相同即可，不需要填写节点编号。

## 旧工作流：节点编号与 Target Bridge

旧轴节点仍保留 `target_node_id`，旧版 Target Bridge 也继续注册，以保证已有工作流可以加载。新工作流不建议继续依赖节点编号和 `target_ref`。

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

## 推荐：LoRA 对比轴

节点：`Studio Suite XY Axis - LoRA Compare`

这个节点把 LoRA 文件与强度区间合并成一个轴，适合直接测试“多个保存点 × 多个强度”：

```text
strength_mode: range
start_strength: 0.6
end_strength: 1.0
strength_steps: 5
```

以上会生成 `0.6、0.7、0.8、0.9、1.0` 五档。每个 LoRA 都会展开这五档。若只比较不同 LoRA，设置 `strength_mode=fixed` 即可。原 `Studio Suite XY Axis - LoRA Strength` 保留，适合把强度单独放在 X 或 Y 轴。

## 输出文件

`Studio Suite XY Queue` 会在输出目录写入：

```text
xy_manifest_YYYYMMDD_HHMMSS.json
```

默认 `auto_build_grid=true` 时，队列会把内部 Finalizer 放在全部格子之后，最后一个格子完成后自动保存 `xy_grid_<run_id>.png`。`Studio Suite XY Grid Builder` 仍保留，用于重新排版旧 manifest 或手动补做图表。

## 注意

- 推荐使用 `slot_name` 参数入口；只有旧轴才需要填写 `target_node_id`。
- `input_names` 必须填该节点真实输入字段名，不是 UI 翻译名。
- 如果采样器/调度器名称不在当前 ComfyUI 可用列表里，子任务会在 prompt 校验阶段失败。
- XY 队列每个格子都会独立提交 prompt，适合配合 `cleanup_after_save` 做长批量稳定测试。
