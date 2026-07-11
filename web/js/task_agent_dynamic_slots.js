import { app } from "/scripts/app.js";

const SLOT_CONFIGS = {
  TaskAgentTaskModuleComposerNode: [
    { count: "task_module_count", prefix: "task_module_", min: 1, max: 12 },
  ],
  TaskAgentResourceBundleMergeNode: [
    { count: "resource_bundle_count", prefix: "resource_bundle_json_", min: 1, max: 12 },
  ],
  TaskAgentContextComposerNode: [
    { count: "world_book_count", prefix: "world_book_text_", min: 2, max: 8 },
    { count: "world_book_count", prefix: "world_book_path_", min: 2, max: 8 },
    { count: "regex_rules_count", prefix: "regex_rules_text_", min: 2, max: 8 },
    { count: "regex_rules_count", prefix: "regex_rules_path_", min: 2, max: 8 },
  ],
};

const MODEL_SOURCE_NODES = new Set([
  "TaskAgentTagUtilityNode",
  "TaskAgentLocalLLMTextToolNode",
  "TaskAgentBackendWorkerNode",
]);

const BACKEND_PROVIDER_NODES = new Set([
  "TaskAgentTagUtilityNode",
  "TaskAgentBackendWorkerNode",
]);

function widgetByName(node, name) {
  return node.widgets?.find((widget) => widget.name === name);
}

function inputIndexByName(node, name) {
  return node.inputs?.findIndex((input) => input.name === name) ?? -1;
}

function addInputAtIndex(node, input, index) {
  node.addInput(input.name, input.type);
  const addedInput = node.inputs[node.inputs.length - 1];
  Object.assign(addedInput, input);
  if (index < node.inputs.length - 1) {
    node.inputs.splice(node.inputs.length - 1, 1);
    node.inputs.splice(index, 0, addedInput);
  }
}

function asCount(value, fallback, min, max) {
  const number = Number.parseInt(value, 10);
  if (!Number.isFinite(number)) return fallback;
  return Math.max(min, Math.min(max, number));
}

function setWidgetVisible(widget, visible) {
  if (!widget) return;
  if (!widget.__studioSuiteOriginalType) {
    widget.__studioSuiteOriginalType = widget.type;
    widget.__studioSuiteOriginalComputeSize = widget.computeSize;
    widget.__studioSuiteOriginalDraw = widget.draw;
    widget.__studioSuiteOriginalHidden = widget.hidden;
    widget.__studioSuiteOriginalDisabled = widget.disabled;
  }
  if (visible) {
    widget.type = widget.__studioSuiteOriginalType;
    widget.computeSize = widget.__studioSuiteOriginalComputeSize;
    widget.draw = widget.__studioSuiteOriginalDraw;
    widget.hidden = widget.__studioSuiteOriginalHidden;
    widget.disabled = widget.__studioSuiteOriginalDisabled;
  } else {
    widget.type = "studio_suite_hidden";
    widget.hidden = true;
    widget.disabled = true;
    widget.computeSize = () => [0, 0];
    widget.draw = () => {};
  }
}

function setModelSourceVisibility(node) {
  if (!MODEL_SOURCE_NODES.has(node.comfyClass || node.type)) return;
  const sourceWidget = widgetByName(node, "model_source");
  const providerWidget = widgetByName(node, "backend_provider");
  const source = String(sourceWidget?.value || "").trim();
  const provider = String(providerWidget?.value || "").trim();
  const isCustomPath = source === "custom_path";
  const isAttachBackend = ["lm_studio", "vllm", "custom_openai_compat"].includes(provider);
  const usesLocalModelSelection = !isAttachBackend;

  setWidgetVisible(widgetByName(node, "model_source"), usesLocalModelSelection);
  setWidgetVisible(widgetByName(node, "backend_profile"), usesLocalModelSelection && !isCustomPath);
  setWidgetVisible(widgetByName(node, "custom_model_path"), usesLocalModelSelection && isCustomPath);
  setWidgetVisible(widgetByName(node, "custom_mmproj_path"), usesLocalModelSelection && isCustomPath);
}

function setBackendProviderVisibility(node) {
  if (!BACKEND_PROVIDER_NODES.has(node.comfyClass || node.type)) return;
  const providerWidget = widgetByName(node, "backend_provider");
  const provider = String(providerWidget?.value || "").trim();
  const needsUrl = ["isolated_worker", "lm_studio", "vllm", "custom_openai_compat"].includes(provider);
  const usesLlamaCppPythonParams = ["config_default", "llama_cpp_python_inproc", "private_llama_cpp_worker"].includes(provider);

  setWidgetVisible(widgetByName(node, "gateway_url"), needsUrl);
  setWidgetVisible(widgetByName(node, "llama_cpp_python_n_gpu_layers"), usesLlamaCppPythonParams);
  setWidgetVisible(widgetByName(node, "llama_cpp_python_n_batch"), usesLlamaCppPythonParams);
  setWidgetVisible(widgetByName(node, "llama_cpp_python_threads"), usesLlamaCppPythonParams);
}

function setInputVisible(node, name, visible) {
  node.__studioSuiteHiddenInputs ||= {};
  if (visible) {
    const cached = node.__studioSuiteHiddenInputs[name];
    if (cached && inputIndexByName(node, name) === -1) {
      addInputAtIndex(node, cached.input, Math.min(cached.index, node.inputs?.length ?? 0));
    }
    delete node.__studioSuiteHiddenInputs[name];
    return true;
  }

  const index = inputIndexByName(node, name);
  if (index < 0) return false;
  const input = node.inputs[index];
  if (input?.link != null) return true;
  node.__studioSuiteHiddenInputs[name] = { input: { ...input }, index };
  node.removeInput(index);
  return false;
}

function updateDynamicSlots(node) {
  const configs = SLOT_CONFIGS[node.comfyClass || node.type];
  if (configs) {
    for (const config of configs) {
      const countWidget = widgetByName(node, config.count);
      const count = asCount(countWidget?.value, config.min - 1, 1, config.max);
      for (let index = config.min; index <= config.max; index += 1) {
        const name = `${config.prefix}${index}`;
        const visible = index <= count;
        const actualVisible = setInputVisible(node, name, visible);
        setWidgetVisible(widgetByName(node, name), actualVisible);
      }
    }
  }
  setModelSourceVisibility(node);
  setBackendProviderVisibility(node);
  node.setSize?.(node.computeSize());
  app.graph?.setDirtyCanvas(true, true);
}

function installDynamicSlotNode(nodeType, nodeData) {
  if (
    !SLOT_CONFIGS[nodeData.name]
    && !MODEL_SOURCE_NODES.has(nodeData.name)
    && !BACKEND_PROVIDER_NODES.has(nodeData.name)
  ) return;

  const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
  nodeType.prototype.onNodeCreated = function () {
    originalOnNodeCreated?.apply(this, arguments);
    if (SLOT_CONFIGS[nodeData.name]) {
      this.addWidget("button", "刷新槽位显示", null, () => updateDynamicSlots(this));
    }
    for (const config of SLOT_CONFIGS[nodeData.name] || []) {
      const countWidget = widgetByName(this, config.count);
      if (!countWidget || countWidget.__studioSuiteDynamicInstalled) continue;
      const originalCallback = countWidget.callback;
      countWidget.callback = (...args) => {
        originalCallback?.apply(countWidget, args);
        updateDynamicSlots(this);
      };
      countWidget.__studioSuiteDynamicInstalled = true;
    }
    const modelSourceWidget = widgetByName(this, "model_source");
    if (modelSourceWidget && !modelSourceWidget.__studioSuiteSourceVisibilityInstalled) {
      const originalCallback = modelSourceWidget.callback;
      modelSourceWidget.callback = (...args) => {
        originalCallback?.apply(modelSourceWidget, args);
        updateDynamicSlots(this);
      };
      modelSourceWidget.__studioSuiteSourceVisibilityInstalled = true;
    }
    const backendProviderWidget = widgetByName(this, "backend_provider");
    if (backendProviderWidget && !backendProviderWidget.__studioSuiteBackendVisibilityInstalled) {
      const originalCallback = backendProviderWidget.callback;
      backendProviderWidget.callback = (...args) => {
        originalCallback?.apply(backendProviderWidget, args);
        updateDynamicSlots(this);
      };
      backendProviderWidget.__studioSuiteBackendVisibilityInstalled = true;
    }
    requestAnimationFrame(() => updateDynamicSlots(this));
  };

  const originalOnConfigure = nodeType.prototype.onConfigure;
  nodeType.prototype.onConfigure = function () {
    originalOnConfigure?.apply(this, arguments);
    requestAnimationFrame(() => updateDynamicSlots(this));
  };
}

app.registerExtension({
  name: "ComfyUI.StudioSuite.TaskAgentDynamicSlots",
  beforeRegisterNodeDef(nodeType, nodeData) {
    installDynamicSlotNode(nodeType, nodeData);
  },
});
