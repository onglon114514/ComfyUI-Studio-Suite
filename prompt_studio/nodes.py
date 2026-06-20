import locale


def _is_chinese_locale():
    value = locale.getdefaultlocale()[0] or ""
    return value.lower().startswith("zh")


class PromptStudioOutput:
    CATEGORY = "Studio Suite/Prompt Studio"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("positive", "negative")
    FUNCTION = "encode"
    DESCRIPTION = "Prompt Studio merged output node with an in-canvas editor for positive and negative prompts."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "positive": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "placeholder": "Positive prompt" if not _is_chinese_locale() else "正向提示词",
                    },
                ),
                "negative": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "placeholder": "Negative prompt" if not _is_chinese_locale() else "反向提示词",
                    },
                ),
            }
        }

    def encode(self, positive, negative):
        return (positive, negative)


class PromptStudioPositiveOutput:
    CATEGORY = "Studio Suite/Prompt Studio"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("positive",)
    FUNCTION = "encode"
    DESCRIPTION = "Single positive prompt output with Prompt Studio editor support."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "positive": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "placeholder": "Positive prompt" if not _is_chinese_locale() else "正向提示词",
                    },
                )
            }
        }

    def encode(self, positive):
        return (positive,)


class PromptStudioNegativeOutput:
    CATEGORY = "Studio Suite/Prompt Studio"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("negative",)
    FUNCTION = "encode"
    DESCRIPTION = "Single negative prompt output with Prompt Studio editor support."

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "negative": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "placeholder": "Negative prompt" if not _is_chinese_locale() else "反向提示词",
                    },
                )
            }
        }

    def encode(self, negative):
        return (negative,)


NODE_CLASS_MAPPINGS = {
    "PromptStudioOutput": PromptStudioOutput,
    "PromptStudioPositiveOutput": PromptStudioPositiveOutput,
    "PromptStudioNegativeOutput": PromptStudioNegativeOutput,
}


if _is_chinese_locale():
    NODE_DISPLAY_NAME_MAPPINGS = {
        "PromptStudioOutput": "Prompt Studio 双提示词输出",
        "PromptStudioPositiveOutput": "Prompt Studio 正向输出",
        "PromptStudioNegativeOutput": "Prompt Studio 反向输出",
    }
else:
    NODE_DISPLAY_NAME_MAPPINGS = {
        "PromptStudioOutput": "Prompt Studio Output",
        "PromptStudioPositiveOutput": "Prompt Studio Positive Output",
        "PromptStudioNegativeOutput": "Prompt Studio Negative Output",
    }


