import json
import re


class AnyType(str):
    def __ne__(self, value):
        return False


ANY_TYPE = AnyType("*")

SLOT_TYPES = [
    "IMAGE",
    "MASK",
    "FLOAT",
    "INT",
    "STRING",
    "BOOLEAN",
    "MODEL",
    "CLIP",
    "VAE",
    "CONDITIONING",
    "LATENT",
    "LORA_STACK",
    "CONTROL_NET",
]

# 类型对应表：用于运行时校验 value 的实际类型
_TYPE_CHECK_MAP = {
    "IMAGE": ("torch.Tensor",),
    "MASK": ("torch.Tensor",),
    "FLOAT": (float, int),
    "INT": (int,),
    "STRING": (str,),
    "BOOLEAN": (bool,),
    "MODEL": tuple(),  # 无法直接判断，放行
    "CLIP": tuple(),
    "VAE": tuple(),
    "CONDITIONING": (list, tuple),
    "LATENT": (dict,),
    "LORA_STACK": (list, tuple),
    "CONTROL_NET": tuple(),
}


def _detect_actual_type(value):
    """尽力检测 value 的实际 ComfyUI 类型名，检测不了返回 None"""
    import torch

    if isinstance(value, torch.Tensor):
        # Tensor 可能是 IMAGE 或 MASK，无法仅凭类型区分
        return None
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INT"
    if isinstance(value, float):
        return "FLOAT"
    if isinstance(value, str):
        return "STRING"
    if isinstance(value, (list, tuple)):
        return "CONDITIONING"
    if isinstance(value, dict):
        return "LATENT"
    return None


class StudioSuite_SlotSet:
    CATEGORY = "Studio Suite/Slot"
    RETURN_TYPES = (ANY_TYPE, "STRING")
    RETURN_NAMES = ("value", "slot_decl")
    FUNCTION = "route"
    DESCRIPTION = "发布槽位（路由/直通）。原样返回 value，同时输出槽位声明 JSON 供前端扫描。"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "slot_name": ("STRING", {"default": "slot1", "multiline": False}),
                "slot_type": (SLOT_TYPES, {"default": "IMAGE"}),
                "slot_tag": ("STRING", {"default": "", "multiline": False}),
                "group": ("STRING", {"default": "", "multiline": False}),
                "label": ("STRING", {"default": "", "multiline": False}),
            },
            "optional": {
                "value": (ANY_TYPE,),
            },
        }

    def route(self, slot_name, slot_type, slot_tag, group, label, value=None):
        # 运行时类型校验
        actual_type = _detect_actual_type(value)
        if actual_type and actual_type != slot_type:
            # 有些类型无法精确区分，只在能明确判断时才报错
            _ambiguous = {"IMAGE", "MASK"}
            if slot_type not in _ambiguous and actual_type not in _ambiguous:
                raise ValueError(
                    f'槽位 "{slot_name}" 声明为 {slot_type}，但收到 {actual_type}'
                )

        decl = {
            "name": slot_name,
            "type": slot_type,
            "tag": slot_tag,
            "group": group,
            "label": label,
        }
        return (value, json.dumps(decl, ensure_ascii=False))


class StudioSuite_SlotGet:
    CATEGORY = "Studio Suite/Slot"
    RETURN_TYPES = (ANY_TYPE, "STRING")
    RETURN_NAMES = ("value", "slot_ref")
    FUNCTION = "resolve"
    DESCRIPTION = (
        "取用槽位（前端期解析节点）。该节点需由前端在生成工作流时"
        "替换成指向 SlotSet 的连线，直接执行会抛异常。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "slot_name": ("STRING", {"default": "slot1", "multiline": False}),
                "accept_tags": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": "逗号分隔的标签列表，留空=只按类型匹配",
                    },
                ),
            },
        }

    def resolve(self, slot_name, accept_tags):
        ref = {"slot_name": slot_name, "accept_tags": accept_tags}
        raise RuntimeError(
            f'槽位取用节点 "{slot_name}" 是前端期解析节点，'
            f"需由前端在生成工作流时替换成指向 SlotSet 的连线，不能直接执行。"
            f" 引用: {json.dumps(ref, ensure_ascii=False)}"
        )

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # 避免 ComfyUI 因输入不变而跳过执行（虽然正常不会执行到这里）
        return float("nan")


NODE_CLASS_MAPPINGS = {
    "StudioSuite_SlotSet": StudioSuite_SlotSet,
    "StudioSuite_SlotGet": StudioSuite_SlotGet,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "StudioSuite_SlotSet": "槽位发布",
    "StudioSuite_SlotGet": "槽位取用",
}
