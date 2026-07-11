(function () {
    "use strict";

    const ROOT_ID = "newbie_xml_wizard_root";
    const SETTINGS_KEY = "newbieXmlWizardSettings";
    const SYSTEM_PREFIX = "You are an assistant designed to generate high-quality anime images with the highest degree of image-text alignment based on xml format textual prompts. <Prompt Start>";
    const MAX_SECTION_COUNT = 20;
    const MAX_FIELDS_PER_SECTION = 40;

    const GENERAL_FIELD_PRESETS = [
        { label: "数量", tag: "count" },
        { label: "画风", tag: "style" },
        { label: "背景", tag: "background" },
        { label: "光影", tag: "lighting" },
        { label: "画面情绪、氛围", tag: "atmosphere" },
        { label: "质量", tag: "quality" },
        { label: "各种物品（包括武器、饰品等等）", tag: "objects" },
        { label: "其他（未包含的任何类型）", tag: "other" }
    ];

    const CHARACTER_FIELD_PRESETS = [
        { label: "角色", tag: "n" },
        { label: "角色别名", tag: "name" },
        { label: "性别/人数标签", tag: "gender" },
        { label: "外观特征", tag: "appearance" },
        { label: "服装", tag: "clothing" },
        { label: "表情", tag: "expression" },
        { label: "动作、姿势", tag: "action" },
        { label: "角色位置", tag: "position" }
    ];

    const state = {
        root: null,
        modeOpen: false,
        step: 1,
        motionDirection: "forward",
        settings: {
            sectionCount: 3
        },
        sections: [],
        output: "",
        targetPromptType: "positive",
        routeStack: [],
        editorTarget: null,
        editorDraft: "",
        editorInitialValue: "",
        bridgeInput: null,
        bridgeOriginalValue: "",
        bridgeSyncEnabled: true,
        dragState: null,
        archives: [],
        selectedArchiveId: "default",
        archiveDraftName: "",
        nextSectionNumber: 1,
        nextFieldNumber: 1
    };

    function ready(callback) {
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", callback, { once: true });
        } else {
            callback();
        }
    }

    ready(init);

    function init() {
        state.root = document.getElementById(ROOT_ID);
        if (!state.root) {
            return;
        }

        mergeSettings(loadLocalSettings());
        if (!state.sections.length) {
            state.sections = createDefaultSections(1);
        }
        normalizeAllState();
        ensureArchives();
        render();
        bindEvents();
        bindBridgeInput();
        loadRemoteSettings();
    }

    function render() {
        state.root.innerHTML = [
            '<div class="newbie-xml-shell' + (state.editorTarget ? " is-editor-open" : "") + '" data-newbie-shell data-motion="' + escapeHtml(state.motionDirection) + '">',
            '  <div class="newbie-xml-header">',
            '    <div class="newbie-xml-title">NewBie XML</div>',
            '    <div class="newbie-xml-actions">',
            '      <button type="button" class="newbie-xml-mode" data-action="normal-mode">普通 PromptUI</button>',
            '      <button type="button" class="newbie-xml-mode" data-action="newbie-mode">NewBie XML 模式</button>',
            '    </div>',
            '  </div>',
            '  <div class="newbie-xml-body">',
            '    <div class="newbie-xml-stepbar">',
            stepButton(1, "1 一级分区"),
            stepButton(2, "2 分区与字段"),
            stepButton(3, "3 XML"),
            '    </div>',
            '    <div class="newbie-xml-scroll" data-newbie-scroll>',
            '      <div class="newbie-xml-status" data-newbie-status>' + escapeHtml(statusText()) + '</div>',
            '      <div class="newbie-xml-warning" data-target-warning></div>',
            archiveBarHtml(),
            paneCount(),
            paneWorkflow(),
            paneOutput(),
            '    </div>',
            '  </div>',
            '</div>'
        ].join("\n");

        syncMode();
        syncStep();
        syncTargetWarning();
        syncEditorMirror();
    }

    function stepButton(step, label) {
        return '<button type="button" class="newbie-xml-step' + (state.step === step ? " is-active" : "") + '" data-step="' + step + '">' + label + "</button>";
    }

    function archiveBarHtml() {
        return [
            '<div class="newbie-xml-archivebar">',
            '  <div class="newbie-xml-archive-controls">',
            '    <label class="newbie-xml-inline-field is-compact">',
            '      <span class="newbie-xml-editor-label">模板存档</span>',
            '      <select class="newbie-xml-inline-input" data-archive-select>',
            archiveOptionsHtml(),
            '      </select>',
            '    </label>',
            '    <label class="newbie-xml-inline-field is-compact">',
            '      <span class="newbie-xml-editor-label">新存档名称</span>',
            '      <input class="newbie-xml-inline-input" type="text" data-archive-name value="' + escapeHtml(state.archiveDraftName) + '" placeholder="例如：双角色自然语言模板">',
            '    </label>',
            '  </div>',
            '  <div class="newbie-xml-row is-inline-start">',
            '    <button type="button" class="newbie-xml-button" data-action="archive-load">加载存档</button>',
            '    <button type="button" class="newbie-xml-button is-primary" data-action="archive-save-new">保存为新存档</button>',
            '    <button type="button" class="newbie-xml-button" data-action="archive-save-overwrite"' + (isSelectedArchiveLocked() ? " disabled" : "") + '>覆盖当前存档</button>',
            '    <button type="button" class="newbie-xml-button" data-action="archive-delete"' + (isSelectedArchiveLocked() ? " disabled" : "") + '>删除当前存档</button>',
            '  </div>',
            '</div>'
        ].join("\n");
    }

    function archiveOptionsHtml() {
        return state.archives.map(function (archive) {
            return '<option value="' + escapeHtml(archive.id) + '"' + (archive.id === state.selectedArchiveId ? " selected" : "") + '>' + escapeHtml(archive.name) + (archive.locked ? "（默认）" : "") + '</option>';
        }).join("");
    }

    function paneCount() {
        return [
            '<div class="newbie-xml-pane' + (state.step === 1 ? " is-active" : "") + '" data-pane="1">',
            '  <div class="newbie-xml-surface" data-motion="' + escapeHtml(state.motionDirection) + '">',
            '    <div class="newbie-xml-section-title">先设置一级分区数量</div>',
            '    <div class="newbie-xml-copy">一级分区代表 &lt;image&gt; 下的直接子标签。二号页面里你可以拖动一级分区顺序，并在每个一级分区里自由增减、拖动二级字段。</div>',
            '    <div class="newbie-xml-count-panel">',
            '      <div class="newbie-xml-count-row">',
            '        <button type="button" class="newbie-xml-icon-button" data-action="decrease-section-count" aria-label="减少一级分区">减少分区</button>',
            '        <input class="newbie-xml-count-input" type="number" min="1" max="' + MAX_SECTION_COUNT + '" data-section-count value="' + state.settings.sectionCount + '">',
            '        <button type="button" class="newbie-xml-icon-button" data-action="increase-section-count" aria-label="增加一级分区">新增分区</button>',
            '      </div>',
            '      <div class="newbie-xml-count-hint">当前会保留 ' + state.settings.sectionCount + ' 个一级分区。默认包含全局块、caption 和角色块，新增分区会自动生成可编辑的空模板。</div>',
            '    </div>',
            '    <div class="newbie-xml-copy">如果你已经搭好一套模板，可以随时用上面的“模板存档”保存下来，后续直接加载复用。</div>',
            '    <div class="newbie-xml-row">',
            '      <button type="button" class="newbie-xml-button is-primary" data-action="go-step-2">确认</button>',
            '    </div>',
            '  </div>',
            '</div>'
        ].join("\n");
    }

    function paneWorkflow() {
        return [
            '<div class="newbie-xml-pane' + (state.step === 2 ? " is-active" : "") + '" data-pane="2">',
            workflowSurfaceHtml(),
            workflowFooterHtml(),
            '</div>'
        ].join("\n");
    }

    function workflowSurfaceHtml() {
        if (state.editorTarget) {
            return editorSurfaceHtml();
        }

        const route = currentRoute();
        if (!route) {
            return workflowHubHtml();
        }
        return sectionRouteHtml(route);
    }

    function workflowFooterHtml() {
        if (state.editorTarget) {
            return [
                '<div class="newbie-xml-row is-editor-row">',
                '  <button type="button" class="newbie-xml-button" data-action="close-editor">返回上一页</button>',
                '  <button type="button" class="newbie-xml-button is-primary" data-action="close-editor">完成</button>',
                '</div>'
            ].join("\n");
        }

        if (currentRoute()) {
            return [
                '<div class="newbie-xml-row">',
                '  <button type="button" class="newbie-xml-button" data-action="route-back">返回一级分区</button>',
                '  <button type="button" class="newbie-xml-button is-primary" data-action="route-back">完成</button>',
                '</div>'
            ].join("\n");
        }

        return [
            '<div class="newbie-xml-row">',
            '  <button type="button" class="newbie-xml-button" data-action="back-to-step-1">上一步</button>',
            '  <button type="button" class="newbie-xml-button is-primary" data-action="go-step-3">完成并汇总 XML</button>',
            '</div>'
        ].join("\n");
    }

    function workflowHubHtml() {
        return [
            '<div class="newbie-xml-surface" data-motion="' + escapeHtml(state.motionDirection) + '">',
            '  <div class="newbie-xml-section-title">二号页面</div>',
            '  <div class="newbie-xml-copy">这里统一管理所有一级分区。拖动卡片可以调整一级标签顺序，点击卡片进入该分区后，可以继续拖动和增减二级字段。</div>',
            '  <div class="newbie-xml-nav-layout">',
            '    <div class="newbie-xml-nav-block">',
            '      <div class="newbie-xml-nav-title-row">',
            '        <div class="newbie-xml-nav-title">一级分区排序</div>',
            '        <button type="button" class="newbie-xml-mini-button" data-action="quick-add-section" title="新增空白一级分区">新增分区</button>',
            '      </div>',
            '      <div class="newbie-xml-nav-grid" data-reorder-container="sections">',
            sectionHubButtonsHtml(),
            '      </div>',
            '    </div>',
            '  </div>',
            '</div>'
        ].join("\n");
    }

    function sectionHubButtonsHtml() {
        return state.sections.map(function (section) {
            return sectionButtonHtml(section);
        }).join("");
    }

    function sectionButtonHtml(section) {
        const modeLabel = section.kind === "text" ? "纯文本" : "二级字段";
        const ruleLabel = section.rule === "natural" ? "自然语言" : "Tag";
        const deleteDisabled = state.sections.length <= 1 ? ' disabled' : '';
        return [
            '<div class="newbie-xml-nav-card-shell">',
            '  <div class="newbie-xml-card-tools" aria-label="一级分区操作">',
            '    <button type="button" class="newbie-xml-mini-button" data-action="duplicate-section" data-section-id="' + escapeHtml(section.id) + '" title="复制一级分区">复制</button>',
            '    <button type="button" class="newbie-xml-mini-button" data-action="delete-section" data-section-id="' + escapeHtml(section.id) + '" title="删除一级分区"' + deleteDisabled + '>删除</button>',
            '  </div>',
            '  <button type="button" class="newbie-xml-nav-button" data-action="open-section" draggable="true" data-reorder-group="sections" data-reorder-key="' + escapeHtml(section.id) + '" data-section-id="' + escapeHtml(section.id) + '" title="拖动调整一级分区顺序">',
            '    <span class="newbie-xml-nav-label">' + escapeHtml(section.label) + '</span>',
            '    <div class="newbie-xml-nav-meta">',
            '      <code>&lt;' + escapeHtml(effectiveTag(section.tag, "section")) + '&gt;</code>',
            '      <span class="newbie-xml-drag-chip">' + escapeHtml(modeLabel) + '</span>',
            '      <span class="newbie-xml-drag-chip is-muted">' + escapeHtml(ruleLabel) + '</span>',
            '    </div>',
            '    <small>' + escapeHtml(sectionSummary(section)) + '</small>',
            '  </button>',
            '</div>'
        ].join("\n");
    }

    function sectionRouteHtml(route) {
        const section = getSectionById(route.sectionId);
        if (!section) {
            return workflowHubHtml();
        }

        return [
            '<div class="newbie-xml-surface" data-motion="' + escapeHtml(state.motionDirection) + '">',
            '  <button type="button" class="newbie-xml-backlink" data-action="route-back">返回一级分区</button>',
            '  <div class="newbie-xml-section-title">' + escapeHtml(section.label) + '</div>',
            '  <div class="newbie-xml-copy">这里是一级分区设置。可以修改一级标签名、切换块规则、复制整个一级分区；二级字段支持自由新增、删除和拖动排序。</div>',
            sectionMetaHtml(section),
            sectionBodyHtml(section),
            '</div>'
        ].join("\n");
    }

    function sectionMetaHtml(section) {
        return [
            '<div class="newbie-xml-editor-panel newbie-xml-section-meta">',
            '  <div class="newbie-xml-inline-grid">',
            '    <label class="newbie-xml-inline-field">',
            '      <span class="newbie-xml-editor-label">一级分区名称</span>',
            '      <input class="newbie-xml-inline-input" type="text" data-section-label data-section-id="' + escapeHtml(section.id) + '" value="' + escapeHtml(section.label) + '">',
            '    </label>',
            '    <label class="newbie-xml-inline-field">',
            '      <span class="newbie-xml-editor-label">一级 XML 标签</span>',
            '      <input class="newbie-xml-inline-input" type="text" data-section-tag data-section-id="' + escapeHtml(section.id) + '" value="' + escapeHtml(section.tag) + '">',
            '    </label>',
            '  </div>',
            '  <div class="newbie-xml-row is-inline-start">',
            '    <button type="button" class="newbie-xml-button' + (section.kind === "container" ? " is-primary" : "") + '" data-action="set-section-kind" data-section-id="' + escapeHtml(section.id) + '" data-kind="container">二级字段模式</button>',
            '    <button type="button" class="newbie-xml-button' + (section.kind === "text" ? " is-primary" : "") + '" data-action="set-section-kind" data-section-id="' + escapeHtml(section.id) + '" data-kind="text">纯文本模式</button>',
            '  </div>',
            '  <div class="newbie-xml-row is-inline-start">',
            '    <button type="button" class="newbie-xml-button' + (section.rule === "tag" ? " is-primary" : "") + '" data-action="set-section-rule" data-section-id="' + escapeHtml(section.id) + '" data-rule="tag">Tag 规则</button>',
            '    <button type="button" class="newbie-xml-button' + (section.rule === "natural" ? " is-primary" : "") + '" data-action="set-section-rule" data-section-id="' + escapeHtml(section.id) + '" data-rule="natural">自然语言规则</button>',
            '    <button type="button" class="newbie-xml-button" data-action="duplicate-section" data-section-id="' + escapeHtml(section.id) + '">复制一级分区</button>',
            '  </div>',
            '  <div class="newbie-xml-copy">规则说明：Tag 规则会把 `hand up` 规范成 `hand_up`；自然语言规则会把 `hand_up` 还原成 `hand up`。</div>',
            '</div>'
        ].join("\n");
    }

    function sectionBodyHtml(section) {
        if (section.kind === "text") {
            return [
                '<div class="newbie-xml-field-card-grid is-single">',
                textFieldCardHtml(section),
                '</div>'
            ].join("\n");
        }

        return [
            '<div class="newbie-xml-row is-inline-start">',
            '  <button type="button" class="newbie-xml-button is-primary" data-action="add-field" data-section-id="' + escapeHtml(section.id) + '">新增二级字段</button>',
            '  <div class="newbie-xml-copy">当前有 ' + section.fields.length + ' 个二级字段，拖动卡片可以调整它们在一级分区中的 XML 顺序。</div>',
            '</div>',
            section.fields.length ? [
                '<div class="newbie-xml-field-card-grid" data-reorder-container="fields:' + escapeHtml(section.id) + '">',
                section.fields.map(function (field) {
                    return fieldCardStackHtml(section, field);
                }).join(""),
                '</div>'
            ].join("\n") : '<div class="newbie-xml-empty-state">当前一级分区还没有二级字段，点击“新增二级字段”开始创建。</div>'
        ].join("\n");
    }

    function textFieldCardHtml(section) {
        return [
            '<button type="button" class="newbie-xml-field-card" data-action="open-editor" data-target-type="section-text" data-section-id="' + escapeHtml(section.id) + '">',
            '  <span class="newbie-xml-field-title">正文内容</span>',
            '  <code>&lt;' + escapeHtml(effectiveTag(section.tag, "section")) + '&gt;</code>',
            '  <small>' + escapeHtml(fieldPreviewFromText(section.text, true, section.rule)) + '</small>',
            '</button>'
        ].join("\n");
    }

    function fieldCardStackHtml(section, field) {
        return [
            '<div class="newbie-xml-field-stack">',
            '  <button type="button" class="newbie-xml-field-card" data-action="open-editor" data-target-type="field" data-section-id="' + escapeHtml(section.id) + '" data-field-id="' + escapeHtml(field.id) + '" draggable="true" data-reorder-group="fields:' + escapeHtml(section.id) + '" data-reorder-key="' + escapeHtml(field.id) + '" title="拖动调整二级字段顺序">',
            '    <span class="newbie-xml-field-title">' + escapeHtml(field.label) + '</span>',
            '    <div class="newbie-xml-nav-meta">',
            '      <code>&lt;' + escapeHtml(effectiveTag(field.tag, "field")) + '&gt;</code>',
            '      <span class="newbie-xml-drag-chip">拖动排序</span>',
            '    </div>',
            '    <small>' + escapeHtml(fieldPreviewFromText(field.value, false, section.rule)) + '</small>',
            '  </button>',
            '  <button type="button" class="newbie-xml-mini-button" data-action="remove-field" data-section-id="' + escapeHtml(section.id) + '" data-field-id="' + escapeHtml(field.id) + '">删除字段</button>',
            '</div>'
        ].join("\n");
    }

    function editorSurfaceHtml() {
        const target = state.editorTarget;
        if (!target) {
            return workflowHubHtml();
        }

        return [
            '<div class="newbie-xml-surface is-editor" data-motion="' + escapeHtml(state.motionDirection) + '">',
            '  <button type="button" class="newbie-xml-backlink" data-action="close-editor">返回字段页</button>',
            '  <div class="newbie-xml-section-title">' + escapeHtml(editorTitle()) + '</div>',
            fieldEditorMetaHtml(target),
            '  <div class="newbie-xml-editor-panel">',
            '    <label class="newbie-xml-editor-label">' + escapeHtml(editorLabelLine()) + '</label>',
            '    <textarea class="newbie-xml-textarea newbie-xml-editor-input" data-editor-input>' + escapeHtml(state.editorDraft) + '</textarea>',
            '    <div class="newbie-xml-editor-preview" data-editor-live>' + escapeHtml(fieldPreviewFromText(state.editorDraft, target.type === "section-text", sectionRuleForTarget(target))) + '</div>',
            '  </div>',
            '</div>'
        ].join("\n");
    }

    function fieldEditorMetaHtml(target) {
        if (target.type !== "field") {
            return "";
        }

        const field = getFieldByTarget(target);
        if (!field) {
            return "";
        }

        return [
            '<div class="newbie-xml-editor-panel newbie-xml-section-meta">',
            '  <div class="newbie-xml-inline-grid">',
            '    <label class="newbie-xml-inline-field">',
            '      <span class="newbie-xml-editor-label">二级字段名称</span>',
            '      <input class="newbie-xml-inline-input" type="text" data-editor-field-label data-section-id="' + escapeHtml(target.sectionId) + '" data-field-id="' + escapeHtml(target.fieldId) + '" value="' + escapeHtml(field.label) + '">',
            '    </label>',
            '    <label class="newbie-xml-inline-field">',
            '      <span class="newbie-xml-editor-label">二级 XML 标签</span>',
            '      <input class="newbie-xml-inline-input" type="text" data-editor-field-tag data-section-id="' + escapeHtml(target.sectionId) + '" data-field-id="' + escapeHtml(target.fieldId) + '" value="' + escapeHtml(field.tag) + '">',
            '    </label>',
            '  </div>',
            '</div>'
        ].join("\n");
    }

    function paneOutput() {
        return [
            '<div class="newbie-xml-pane' + (state.step === 3 ? " is-active" : "") + '" data-pane="3">',
            '  <div class="newbie-xml-surface" data-motion="' + escapeHtml(state.motionDirection) + '">',
            '    <div class="newbie-xml-preview" data-output-preview>' + buildOutputPreview() + '</div>',
            '    <div class="newbie-xml-section-title">XML</div>',
            '    <textarea class="newbie-xml-textarea newbie-xml-output" data-output>' + escapeHtml(state.output) + '</textarea>',
            '    <div class="newbie-xml-row">',
            '      <button type="button" class="newbie-xml-button" data-action="back-to-step-2">上一步</button>',
            '      <button type="button" class="newbie-xml-button is-primary" data-action="write-positive">写回正向提示词</button>',
            '    </div>',
            '  </div>',
            '</div>'
        ].join("\n");
    }

    function bindEvents() {
        state.root.addEventListener("click", function (event) {
            const stepButtonEl = event.target.closest("[data-step]");
            const actionButton = event.target.closest("[data-action]");
            if (stepButtonEl) {
                handleStepClick(Number(stepButtonEl.getAttribute("data-step")));
                return;
            }
            if (!actionButton) {
                return;
            }
            handleAction(actionButton.getAttribute("data-action"), actionButton);
        });

        state.root.addEventListener("change", function (event) {
            if (event.target.hasAttribute("data-section-count")) {
                setSectionCount(event.target.value);
                return;
            }
            if (event.target.hasAttribute("data-archive-select")) {
                state.selectedArchiveId = event.target.value;
                const archive = getSelectedArchive();
                state.archiveDraftName = archive && !archive.locked ? archive.name : "";
                saveLocalSettings();
                render();
            }
        });

        state.root.addEventListener("input", function (event) {
            if (event.target.hasAttribute("data-section-count")) {
                setSectionCount(event.target.value, true);
                return;
            }
            if (event.target.hasAttribute("data-output")) {
                state.output = event.target.value;
                return;
            }
            if (event.target.hasAttribute("data-archive-name")) {
                state.archiveDraftName = event.target.value;
                return;
            }
            if (event.target.hasAttribute("data-editor-input")) {
                setEditorDraft(event.target.value, true);
                return;
            }
            if (event.target.hasAttribute("data-section-label")) {
                updateSectionMeta(event.target.getAttribute("data-section-id"), "label", event.target.value);
                return;
            }
            if (event.target.hasAttribute("data-section-tag")) {
                updateSectionMeta(event.target.getAttribute("data-section-id"), "tag", event.target.value);
                return;
            }
            if (event.target.hasAttribute("data-editor-field-label")) {
                updateFieldMeta(event.target.getAttribute("data-section-id"), event.target.getAttribute("data-field-id"), "label", event.target.value);
                return;
            }
            if (event.target.hasAttribute("data-editor-field-tag")) {
                updateFieldMeta(event.target.getAttribute("data-section-id"), event.target.getAttribute("data-field-id"), "tag", event.target.value);
            }
        });

        state.root.addEventListener("dragstart", handleDragStart);
        state.root.addEventListener("dragover", handleDragOver);
        state.root.addEventListener("drop", handleDrop);
        state.root.addEventListener("dragend", clearDragState);

        window.addEventListener("message", function (event) {
            if (!event.data || (event.data.handel !== "openWeiLinPrompt" && event.data.handel !== "responeseWeiLinPrompt")) {
                return;
            }
            state.targetPromptType = event.data.nodeName === "WeiLinComfyUIPromptAllInOneNeg" ? "negative" : "positive";
            syncTargetWarning();
        });
    }

    function bindBridgeInput() {
        const input = getPositiveInput();
        if (!input || typeof input.addEventListener !== "function") {
            return;
        }
        if (state.bridgeInput === input) {
            return;
        }
        if (state.bridgeInput) {
            state.bridgeInput.removeEventListener("input", handleBridgeInput);
        }
        state.bridgeInput = input;
        state.bridgeInput.addEventListener("input", handleBridgeInput);
    }

    function handleBridgeInput(event) {
        if (!state.editorTarget) {
            return;
        }
        state.editorDraft = event.target.value;
        syncEditorMirror();
    }

    function handleStepClick(step) {
        if (step === 1) {
            setStep(1, { direction: "back", resetRoute: true });
        } else if (step === 2) {
            setStep(2, { direction: state.step === 1 ? "forward" : "back", resetRoute: true });
        } else if (step === 3) {
            buildXmlAndOpenOutput("forward");
        }
    }

    function handleAction(action, source) {
        if (action === "newbie-mode") {
            state.modeOpen = true;
            render();
        } else if (action === "normal-mode") {
            closeEditor({ commit: true, restorePrompt: true, render: false });
            state.modeOpen = false;
            state.step = 1;
            state.routeStack = [];
            state.motionDirection = "back";
            render();
        } else if (action === "go-step-2") {
            setStep(2, { direction: "forward", resetRoute: true });
        } else if (action === "back-to-step-1") {
            setStep(1, { direction: "back", resetRoute: true });
        } else if (action === "go-step-3") {
            buildXmlAndOpenOutput("forward");
        } else if (action === "back-to-step-2") {
            setStep(2, { direction: "back", resetRoute: true });
        } else if (action === "write-positive") {
            writePositivePrompt();
        } else if (action === "increase-section-count") {
            setSectionCount(state.settings.sectionCount + 1);
        } else if (action === "decrease-section-count") {
            setSectionCount(state.settings.sectionCount - 1);
        } else if (action === "open-section") {
            openRoute({ type: "section", sectionId: source.getAttribute("data-section-id") }, "forward");
        } else if (action === "route-back") {
            closeRoute();
        } else if (action === "open-editor") {
            openEditor(readTargetFromElement(source));
        } else if (action === "close-editor") {
            closeEditor({ commit: true, restorePrompt: true, render: true, direction: "back" });
        } else if (action === "add-field") {
            addFieldToSection(source.getAttribute("data-section-id"));
        } else if (action === "remove-field") {
            removeFieldFromSection(source.getAttribute("data-section-id"), source.getAttribute("data-field-id"));
        } else if (action === "set-section-kind") {
            setSectionKind(source.getAttribute("data-section-id"), source.getAttribute("data-kind"));
        } else if (action === "set-section-rule") {
            setSectionRule(source.getAttribute("data-section-id"), source.getAttribute("data-rule"));
        } else if (action === "duplicate-section") {
            duplicateSection(source.getAttribute("data-section-id"));
        } else if (action === "delete-section") {
            deleteSection(source.getAttribute("data-section-id"));
        } else if (action === "quick-add-section") {
            addBlankSection();
        } else if (action === "archive-load") {
            loadSelectedArchive();
        } else if (action === "archive-save-new") {
            saveAsNewArchive();
        } else if (action === "archive-save-overwrite") {
            overwriteSelectedArchive();
        } else if (action === "archive-delete") {
            deleteSelectedArchive();
        }
    }

    function buildXmlAndOpenOutput(direction) {
        closeEditor({ commit: true, restorePrompt: true, render: false });
        state.output = buildXml();
        setStep(3, { direction: direction || "forward", resetRoute: true, outputReady: true });
    }

    function setStep(step, options) {
        const settings = options || {};
        if (step !== 2) {
            closeEditor({ commit: true, restorePrompt: true, render: false });
        }
        if (settings.resetRoute) {
            state.routeStack = [];
        }
        if (step === 3 && !settings.outputReady) {
            state.output = buildXml();
        }
        state.motionDirection = settings.direction || (step > state.step ? "forward" : "back");
        state.step = step;
        render();
    }

    function setSectionCount(value, deferRender) {
        const nextValue = clampSectionCount(value);
        if (state.settings.sectionCount === nextValue) {
            return;
        }

        state.settings.sectionCount = nextValue;
        if (state.sections.length > nextValue) {
            trimSections(nextValue);
        } else {
            while (state.sections.length < nextValue) {
                state.sections.push(createBlankContainerSection());
            }
        }
        normalizeAllState();
        saveSettings();
        if (!deferRender) {
            state.motionDirection = "forward";
            render();
        }
    }

    function trimSections(nextValue) {
        const removedSections = state.sections.slice(nextValue);
        const removedIds = removedSections.map(function (section) { return section.id; });
        state.sections = state.sections.slice(0, nextValue);

        const route = currentRoute();
        if (route && removedIds.indexOf(route.sectionId) !== -1) {
            state.routeStack = [];
        }
        if (state.editorTarget && removedIds.indexOf(state.editorTarget.sectionId) !== -1) {
            closeEditor({ commit: false, restorePrompt: true, render: false });
        }
    }

    function openRoute(route, direction) {
        closeEditor({ commit: true, restorePrompt: true, render: false });
        state.motionDirection = direction || "forward";
        state.routeStack.push(route);
        render();
    }

    function closeRoute() {
        closeEditor({ commit: true, restorePrompt: true, render: false });
        if (!state.routeStack.length) {
            return;
        }
        state.motionDirection = "back";
        state.routeStack.pop();
        render();
    }

    function currentRoute() {
        return state.routeStack.length ? state.routeStack[state.routeStack.length - 1] : null;
    }

    function openEditor(target) {
        bindBridgeInput();
        closeEditor({ commit: true, restorePrompt: true, render: false });
        state.motionDirection = "forward";
        state.editorTarget = target;
        state.editorDraft = draftTextForTarget(target);
        state.editorInitialValue = state.editorDraft;
        state.bridgeOriginalValue = state.bridgeInput ? state.bridgeInput.value : "";
        setBridgeSyncEnabled(false);
        pushBridgeValue(state.editorDraft);
        render();
    }

    function closeEditor(options) {
        const settings = options || {};
        if (!state.editorTarget) {
            return;
        }

        if (settings.commit) {
            commitEditorDraft();
        }
        if (settings.restorePrompt !== false) {
            restorePromptBridge();
        }

        state.editorTarget = null;
        state.editorDraft = "";
        state.editorInitialValue = "";
        state.motionDirection = settings.direction || state.motionDirection;
        if (settings.render) {
            render();
        }
    }

    function commitEditorDraft() {
        const target = state.editorTarget;
        if (!target) {
            return;
        }

        if (target.type === "section-text") {
            const section = getSectionById(target.sectionId);
            if (!section) {
                return;
            }
            section.text = normalizeDraftForTarget(target, state.editorDraft);
            if (hasDraftChanged(target, state.editorDraft, state.editorInitialValue)) {
                section.touched = true;
            }
            saveSettings();
            return;
        }

        const field = getFieldByTarget(target);
        if (!field) {
            return;
        }
        field.value = normalizeDraftForTarget(target, state.editorDraft);
        if (hasDraftChanged(target, state.editorDraft, state.editorInitialValue)) {
            field.touched = true;
        }
        saveSettings();
    }

    function restorePromptBridge() {
        if (!state.bridgeInput) {
            return;
        }
        setBridgeSyncEnabled(true);
        state.bridgeInput.value = state.bridgeOriginalValue;
        state.bridgeInput.dispatchEvent(new Event("input", { bubbles: true }));
        state.bridgeOriginalValue = "";
    }

    function setBridgeSyncEnabled(enabled) {
        if (!state.bridgeInput || typeof window.updateValue !== "function") {
            state.bridgeSyncEnabled = enabled;
            return;
        }
        if (enabled && !state.bridgeSyncEnabled) {
            state.bridgeInput.addEventListener("input", window.updateValue);
        } else if (!enabled && state.bridgeSyncEnabled) {
            state.bridgeInput.removeEventListener("input", window.updateValue);
        }
        state.bridgeSyncEnabled = enabled;
    }

    function pushBridgeValue(value) {
        if (!state.bridgeInput) {
            return;
        }
        state.bridgeInput.value = value;
        state.bridgeInput.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function setEditorDraft(value, syncBridge) {
        state.editorDraft = value;
        if (syncBridge) {
            pushBridgeValue(value);
        }
        syncEditorMirror();
    }

    function syncEditorMirror() {
        if (!state.root || !state.editorTarget) {
            return;
        }
        const editorInput = state.root.querySelector("[data-editor-input]");
        if (editorInput && editorInput.value !== state.editorDraft) {
            editorInput.value = state.editorDraft;
        }
        const editorPreview = state.root.querySelector("[data-editor-live]");
        if (editorPreview) {
            editorPreview.textContent = fieldPreviewFromText(state.editorDraft, state.editorTarget.type === "section-text", sectionRuleForTarget(state.editorTarget));
        }
    }

    function syncMode() {
        const shell = state.root.querySelector("[data-newbie-shell]");
        if (!shell) {
            return;
        }
        shell.classList.toggle("is-open", state.modeOpen);
        state.root.querySelectorAll("[data-action='newbie-mode']").forEach(function (button) {
            button.classList.toggle("is-active", state.modeOpen);
        });
        state.root.querySelectorAll("[data-action='normal-mode']").forEach(function (button) {
            button.classList.toggle("is-active", !state.modeOpen);
        });
        document.documentElement.classList.toggle("newbie-xml-mode-open", state.modeOpen);
        document.documentElement.classList.toggle("newbie-xml-editor-open", state.modeOpen && state.step === 2 && !!state.editorTarget);
        document.body.classList.toggle("newbie-xml-mode-open", state.modeOpen);
        document.body.classList.toggle("newbie-xml-editor-open", state.modeOpen && state.step === 2 && !!state.editorTarget);
    }

    function syncStep() {
        state.root.querySelectorAll("[data-step]").forEach(function (button) {
            button.classList.toggle("is-active", Number(button.getAttribute("data-step")) === state.step);
        });
        state.root.querySelectorAll("[data-pane]").forEach(function (pane) {
            pane.classList.toggle("is-active", Number(pane.getAttribute("data-pane")) === state.step);
        });
    }

    function syncTargetWarning() {
        const warning = state.root && state.root.querySelector("[data-target-warning]");
        const writeButton = state.root && state.root.querySelector("[data-action='write-positive']");
        const negativeOnly = state.targetPromptType === "negative";
        if (warning) {
            warning.textContent = negativeOnly ? "当前打开的是负向节点，NewBie XML 只写回正向提示词。" : "";
        }
        if (writeButton) {
            writeButton.disabled = negativeOnly;
        }
    }

    function statusText() {
        if (state.step === 1) {
            return "先确定一级分区数量，再进入层级化 NewBie XML 导航页。";
        }
        if (state.step === 2 && state.editorTarget) {
            return "当前处于字段编辑状态：上方输入框与下方 PromptUI 会实时同步。";
        }
        if (state.step === 2) {
            return "先拖动一级分区，再进入每个一级分区对二级字段做自由增减和排序。";
        }
        return "系统提示词会自动置顶，其余 XML 会严格按照一级、二级两层排序结果输出。";
    }

    function buildXml() {
        const lines = [SYSTEM_PREFIX, "", "<image>"];
        state.sections.forEach(function (section) {
            appendSectionXml(lines, section);
        });
        lines.push("</image>");
        return lines.join("\n");
    }

    function appendSectionXml(lines, section) {
        const sectionTag = effectiveTag(section.tag, "section");
        if (section.kind === "text") {
            if (section.touched && String(section.text || "").trim()) {
                lines.push(xmlLine(sectionTag, section.text.trim(), 2));
            }
            return;
        }

        const fieldLines = [];
        section.fields.forEach(function (field) {
            if (field.touched && String(field.value || "").trim()) {
                fieldLines.push(xmlLine(effectiveTag(field.tag, "field"), field.value, 4));
            }
        });

        if (!fieldLines.length) {
            return;
        }

        lines.push(xmlLineStart(sectionTag, 2));
        fieldLines.forEach(function (line) { lines.push(line); });
        lines.push(xmlLineEnd(sectionTag, 2));
    }

    function buildOutputPreview() {
        if (!state.output) {
            return '<div class="newbie-xml-preview-empty">点击二号页面的完成后，会在这里生成 XML 预览。</div>';
        }

        const rows = [];
        state.sections.forEach(function (section) {
            if (section.kind === "text") {
                if (section.touched && String(section.text || "").trim()) {
                    rows.push('<div class="newbie-xml-preview-item"><span>一级</span><b>&lt;' + escapeHtml(effectiveTag(section.tag, "section")) + '&gt;</b></div>');
                }
                return;
            }

            const outputFields = section.fields.filter(function (field) {
                return field.touched && String(field.value || "").trim();
            });
            if (!outputFields.length) {
                return;
            }
            rows.push('<div class="newbie-xml-preview-item"><span>一级</span><b>&lt;' + escapeHtml(effectiveTag(section.tag, "section")) + '&gt;</b></div>');
            outputFields.forEach(function (field) {
                rows.push('<div class="newbie-xml-preview-item is-child"><span>二级</span><b>&lt;' + escapeHtml(effectiveTag(field.tag, "field")) + '&gt;</b></div>');
            });
        });

        if (!rows.length) {
            rows.push('<div class="newbie-xml-preview-empty">当前还没有会写入 XML 的字段。</div>');
        }

        return [
            '<div class="newbie-xml-preview-grid">',
            '  <div class="newbie-xml-preview-section">',
            '    <div class="newbie-xml-preview-title">输出顺序</div>',
            rows.join(""),
            '  </div>',
            '  <div class="newbie-xml-preview-section">',
            '    <div class="newbie-xml-preview-title">系统提示词</div>',
            '    <div>' + escapeHtml(SYSTEM_PREFIX) + '</div>',
            '  </div>',
            '</div>'
        ].join("\n");
    }

    function writePositivePrompt() {
        if (state.targetPromptType === "negative") {
            syncTargetWarning();
            return;
        }
        if (!state.output) {
            state.output = buildXml();
            render();
        }
        const input = getPositiveInput();
        input.value = state.output;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        notifyPromptHost(state.output);
        window.setTimeout(closePromptWindowAfterWrite, 0);
    }

    function notifyPromptHost(positiveValue) {
        const randomId = localStorage.getItem("weilin_prompt_randomid");
        if (!randomId || !window.top || window.top === window) {
            return;
        }

        window.top.postMessage({
            handel: "changeWeiLinPrompt",
            g_value: positiveValue,
            n_value: getNegativeInput().value || "",
            randomid: randomId
        }, "*");
    }

    function closePromptWindowAfterWrite() {
        if (typeof window.closeDialog === "function") {
            window.closeDialog();
            return;
        }

        const randomId = localStorage.getItem("weilin_prompt_randomid");
        if (!randomId || !window.top || window.top === window) {
            return;
        }

        window.top.postMessage({
            handel: "closeWeilinPromptBox",
            randomid: randomId
        }, "*");
    }

    function readTargetFromElement(element) {
        return {
            type: element.getAttribute("data-target-type"),
            sectionId: element.getAttribute("data-section-id"),
            fieldId: element.getAttribute("data-field-id") || ""
        };
    }

    function draftTextForTarget(target) {
        if (target.type === "section-text") {
            const section = getSectionById(target.sectionId);
            return section ? section.text : "";
        }
        const field = getFieldByTarget(target);
        return field ? field.value : "";
    }

    function editorTitle() {
        if (!state.editorTarget) {
            return "";
        }
        const section = getSectionById(state.editorTarget.sectionId);
        if (!section) {
            return "";
        }
        if (state.editorTarget.type === "section-text") {
            return "编辑 " + section.label;
        }
        const field = getFieldByTarget(state.editorTarget);
        return field ? "编辑 " + field.label : "";
    }

    function editorLabelLine() {
        if (!state.editorTarget) {
            return "";
        }
        const section = getSectionById(state.editorTarget.sectionId);
        if (!section) {
            return "";
        }
        if (state.editorTarget.type === "section-text") {
            return section.label + " <" + effectiveTag(section.tag, "section") + ">";
        }
        const field = getFieldByTarget(state.editorTarget);
        if (!field) {
            return "";
        }
        return section.label + " / " + field.label + " <" + effectiveTag(field.tag, "field") + ">";
    }

    function sectionSummary(section) {
        if (section.kind === "text") {
            return fieldPreviewFromText(section.text, true, section.rule);
        }
        const filled = section.fields.filter(function (field) {
            return !!String(field.value || "").trim();
        }).length;
        if (!filled) {
            return "未填写二级字段";
        }
        return "已填写 " + filled + " / " + section.fields.length + " 个二级字段";
    }

    function fieldPreviewFromText(text, isTextBlock, rule) {
        const raw = String(text || "").trim();
        if (!raw) {
            return "点击开始填写";
        }
        const normalized = normalizeTextWithRule(raw, rule || "tag", !!isTextBlock);
        return truncateText(normalized, 72);
    }

    function truncateText(text, maxLength) {
        const limit = maxLength || 72;
        if (text.length <= limit) {
            return text;
        }
        return text.slice(0, limit - 1) + "…";
    }

    function getSectionById(sectionId) {
        return state.sections.find(function (section) {
            return section.id === sectionId;
        }) || null;
    }

    function getFieldByTarget(target) {
        const section = getSectionById(target.sectionId);
        if (!section) {
            return null;
        }
        return section.fields.find(function (field) {
            return field.id === target.fieldId;
        }) || null;
    }

    function updateSectionMeta(sectionId, key, value) {
        const section = getSectionById(sectionId);
        if (!section) {
            return;
        }
        section[key] = value;
        saveLocalSettings();
    }

    function updateFieldMeta(sectionId, fieldId, key, value) {
        const field = getFieldByTarget({ sectionId: sectionId, fieldId: fieldId });
        if (!field) {
            return;
        }
        field[key] = value;
        saveLocalSettings();
    }

    function setSectionRule(sectionId, rule) {
        const section = getSectionById(sectionId);
        if (!section || (rule !== "tag" && rule !== "natural") || section.rule === rule) {
            return;
        }
        section.rule = rule;
        if (section.kind === "text") {
            section.text = normalizeTextWithRule(section.text, section.rule, true);
        } else {
            section.fields.forEach(function (field) {
                field.value = normalizeTextWithRule(field.value, section.rule, false);
            });
        }
        saveSettings();
        render();
    }

    function setSectionKind(sectionId, kind) {
        const section = getSectionById(sectionId);
        if (!section || (kind !== "container" && kind !== "text") || section.kind === kind) {
            return;
        }
        section.kind = kind;
        if (kind === "container" && !section.fields.length) {
            section.fields.push(createField("子类1", "field_1"));
        }
        saveSettings();
        render();
    }

    function duplicateSection(sectionId) {
        const section = getSectionById(sectionId);
        if (!section || state.sections.length >= MAX_SECTION_COUNT) {
            return;
        }
        const copy = cloneSection(section);
        copy.id = createSectionId();
        copy.label = section.label + " 副本";
        copy.fields = copy.fields.map(function (field) {
            field.id = createFieldId();
            return field;
        });
        const index = state.sections.findIndex(function (item) {
            return item.id === sectionId;
        });
        state.sections.splice(index + 1, 0, copy);
        state.settings.sectionCount = clampSectionCount(state.sections.length);
        saveSettings();
        render();
    }

    function addBlankSection() {
        if (state.sections.length >= MAX_SECTION_COUNT) {
            return;
        }
        state.sections.push(createBlankContainerSection());
        state.settings.sectionCount = clampSectionCount(state.sections.length);
        saveSettings();
        render();
    }

    function deleteSection(sectionId) {
        if (state.sections.length <= 1) {
            return;
        }
        const index = state.sections.findIndex(function (section) {
            return section.id === sectionId;
        });
        if (index === -1) {
            return;
        }
        state.sections.splice(index, 1);
        state.settings.sectionCount = clampSectionCount(state.sections.length);

        const route = currentRoute();
        if (route && route.sectionId === sectionId) {
            state.routeStack = [];
        }
        if (state.editorTarget && state.editorTarget.sectionId === sectionId) {
            closeEditor({ commit: false, restorePrompt: true, render: false });
        }

        saveSettings();
        render();
    }

    function addFieldToSection(sectionId) {
        const section = getSectionById(sectionId);
        if (!section || section.kind !== "container" || section.fields.length >= MAX_FIELDS_PER_SECTION) {
            return;
        }
        section.fields.push(createField("子类" + (section.fields.length + 1), "field_" + (section.fields.length + 1)));
        saveSettings();
        render();
    }

    function removeFieldFromSection(sectionId, fieldId) {
        const section = getSectionById(sectionId);
        if (!section || section.kind !== "container") {
            return;
        }
        const nextFields = section.fields.filter(function (field) {
            return field.id !== fieldId;
        });
        if (nextFields.length === section.fields.length) {
            return;
        }
        section.fields = nextFields;
        if (state.editorTarget && state.editorTarget.fieldId === fieldId) {
            closeEditor({ commit: false, restorePrompt: true, render: false });
        }
        saveSettings();
        render();
    }

    function handleDragStart(event) {
        const element = event.target.closest("[data-reorder-group][data-reorder-key]");
        if (!element || state.step !== 2 || state.editorTarget) {
            return;
        }
        state.dragState = {
            group: element.getAttribute("data-reorder-group"),
            key: element.getAttribute("data-reorder-key")
        };
        element.classList.add("is-dragging");
        if (event.dataTransfer) {
            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData("text/plain", state.dragState.key);
        }
    }

    function handleDragOver(event) {
        if (!state.dragState) {
            return;
        }
        const card = event.target.closest("[data-reorder-group][data-reorder-key]");
        const container = event.target.closest("[data-reorder-container]");

        if (card && card.getAttribute("data-reorder-group") === state.dragState.group) {
            event.preventDefault();
            markDropTarget(card, null);
            if (event.dataTransfer) {
                event.dataTransfer.dropEffect = "move";
            }
            return;
        }

        if (container && container.getAttribute("data-reorder-container") === state.dragState.group) {
            event.preventDefault();
            markDropTarget(null, container);
            if (event.dataTransfer) {
                event.dataTransfer.dropEffect = "move";
            }
        }
    }

    function handleDrop(event) {
        if (!state.dragState) {
            return;
        }
        const card = event.target.closest("[data-reorder-group][data-reorder-key]");
        const container = event.target.closest("[data-reorder-container]");
        const dragState = state.dragState;
        clearDragDecorations();

        if (card && card.getAttribute("data-reorder-group") === dragState.group) {
            event.preventDefault();
            reorderItems(dragState.group, dragState.key, card.getAttribute("data-reorder-key"));
            clearDragState();
            return;
        }

        if (container && container.getAttribute("data-reorder-container") === dragState.group) {
            event.preventDefault();
            reorderItems(dragState.group, dragState.key, null);
        }

        clearDragState();
    }

    function reorderItems(group, sourceKey, targetKey) {
        if (targetKey === sourceKey) {
            return;
        }
        if (group === "sections") {
            reorderSections(sourceKey, targetKey);
            return;
        }
        if (group.indexOf("fields:") === 0) {
            reorderFields(group.slice("fields:".length), sourceKey, targetKey);
        }
    }

    function reorderSections(sourceId, targetId) {
        const order = state.sections.slice();
        const sourceIndex = order.findIndex(function (section) { return section.id === sourceId; });
        if (sourceIndex === -1) {
            return;
        }
        const moving = order.splice(sourceIndex, 1)[0];
        if (!targetId) {
            order.push(moving);
        } else {
            const targetIndex = order.findIndex(function (section) { return section.id === targetId; });
            if (targetIndex === -1) {
                order.push(moving);
            } else {
                order.splice(targetIndex, 0, moving);
            }
        }
        state.sections = order;
        saveSettings();
        render();
    }

    function reorderFields(sectionId, sourceId, targetId) {
        const section = getSectionById(sectionId);
        if (!section) {
            return;
        }
        const fields = section.fields.slice();
        const sourceIndex = fields.findIndex(function (field) { return field.id === sourceId; });
        if (sourceIndex === -1) {
            return;
        }
        const moving = fields.splice(sourceIndex, 1)[0];
        if (!targetId) {
            fields.push(moving);
        } else {
            const targetIndex = fields.findIndex(function (field) { return field.id === targetId; });
            if (targetIndex === -1) {
                fields.push(moving);
            } else {
                fields.splice(targetIndex, 0, moving);
            }
        }
        section.fields = fields;
        saveSettings();
        render();
    }

    function markDropTarget(card, container) {
        clearDragDecorations();
        if (card) {
            card.classList.add("is-drop-target");
        }
        if (container) {
            container.classList.add("is-drop-target");
        }
    }

    function clearDragDecorations() {
        if (!state.root) {
            return;
        }
        state.root.querySelectorAll(".is-drop-target, .is-dragging").forEach(function (element) {
            element.classList.remove("is-drop-target", "is-dragging");
        });
    }

    function clearDragState() {
        clearDragDecorations();
        state.dragState = null;
    }

    function ensureArchives() {
        if (!Array.isArray(state.archives) || !state.archives.length) {
            state.archives = [createArchive("默认存档", state.sections, true)];
            state.selectedArchiveId = state.archives[0].id;
            state.archiveDraftName = "";
            return;
        }

        const hasDefault = state.archives.some(function (archive) {
            return archive.locked;
        });
        if (!hasDefault) {
            state.archives.unshift(createArchive("默认存档", state.sections, true));
        }

        if (!getSelectedArchive()) {
            state.selectedArchiveId = state.archives[0].id;
        }
        if (getSelectedArchive() && getSelectedArchive().locked) {
            state.archiveDraftName = "";
        }
    }

    function createArchive(name, sections, locked) {
        return {
            id: "archive_" + Date.now() + "_" + Math.floor(Math.random() * 100000),
            name: name,
            locked: !!locked,
            sections: cloneSections(sections)
        };
    }

    function getSelectedArchive() {
        return state.archives.find(function (archive) {
            return archive.id === state.selectedArchiveId;
        }) || null;
    }

    function isSelectedArchiveLocked() {
        const archive = getSelectedArchive();
        return !archive || !!archive.locked;
    }

    function loadSelectedArchive() {
        const archive = getSelectedArchive();
        if (!archive) {
            return;
        }
        closeEditor({ commit: true, restorePrompt: true, render: false });
        state.sections = cloneSections(archive.sections);
        normalizeAllState();
        state.settings.sectionCount = clampSectionCount(state.sections.length);
        state.routeStack = [];
        state.motionDirection = "forward";
        saveSettings();
        render();
    }

    function saveAsNewArchive() {
        const name = String(state.archiveDraftName || "").trim() || ("模板存档 " + (state.archives.length + 1));
        const archive = createArchive(name, state.sections, false);
        state.archives.push(archive);
        state.selectedArchiveId = archive.id;
        state.archiveDraftName = archive.name;
        saveSettings();
        render();
    }

    function overwriteSelectedArchive() {
        const archive = getSelectedArchive();
        if (!archive || archive.locked) {
            return;
        }
        archive.sections = cloneSections(state.sections);
        archive.name = String(state.archiveDraftName || "").trim() || archive.name;
        saveSettings();
        render();
    }

    function deleteSelectedArchive() {
        const archive = getSelectedArchive();
        if (!archive || archive.locked) {
            return;
        }
        state.archives = state.archives.filter(function (item) {
            return item.id !== archive.id;
        });
        state.selectedArchiveId = state.archives.length ? state.archives[0].id : "default";
        state.archiveDraftName = "";
        ensureArchives();
        saveSettings();
        render();
    }

    function cloneSection(section) {
        return JSON.parse(JSON.stringify(section));
    }

    function cloneSections(sections) {
        return JSON.parse(JSON.stringify(sections || []));
    }

    function createDefaultSections(characterCount) {
        const sections = [
            createContainerSection("全局类区", "general_tags", GENERAL_FIELD_PRESETS),
            createTextSection("自然语言描述", "caption")
        ];
        for (let index = 0; index < Math.max(1, characterCount || 1); index += 1) {
            sections.push(createContainerSection("角色" + (index + 1), "character_" + (index + 1), CHARACTER_FIELD_PRESETS));
        }
        return sections;
    }

    function createBlankContainerSection() {
        const number = state.nextSectionNumber;
        const section = createContainerSection("一级分区" + number, "section_" + number, [
            { label: "子类1", tag: "field_1" }
        ]);
        return section;
    }

    function createContainerSection(label, tag, presets) {
        const sectionId = createSectionId();
        return {
            id: sectionId,
            label: label,
            tag: tag,
            kind: "container",
            rule: "tag",
            text: "",
            touched: false,
            fields: (presets || []).map(function (preset) {
                return createField(preset.label, preset.tag);
            })
        };
    }

    function createTextSection(label, tag) {
        return {
            id: createSectionId(),
            label: label,
            tag: tag,
            kind: "text",
            rule: "natural",
            text: "",
            touched: false,
            fields: []
        };
    }

    function createField(label, tag) {
        return {
            id: createFieldId(),
            label: label,
            tag: tag,
            value: "",
            touched: false
        };
    }

    function createSectionId() {
        const id = "section_" + state.nextSectionNumber;
        state.nextSectionNumber += 1;
        return id;
    }

    function createFieldId() {
        const id = "field_" + state.nextFieldNumber;
        state.nextFieldNumber += 1;
        return id;
    }

    function normalizeAllState() {
        const usedSectionIds = {};
        const usedFieldIds = {};

        state.sections = (Array.isArray(state.sections) ? state.sections : []).map(function (section, sectionIndex) {
            const normalizedSection = normalizeSection(section, sectionIndex, usedSectionIds, usedFieldIds);
            return normalizedSection;
        });

        state.settings.sectionCount = clampSectionCount(state.sections.length || 1);
        if (!state.sections.length) {
            state.sections = createDefaultSections(1);
            state.settings.sectionCount = state.sections.length;
        }
    }

    function normalizeSection(section, sectionIndex, usedSectionIds, usedFieldIds) {
        const fallbackLabel = "一级分区" + (sectionIndex + 1);
        const sectionId = uniqueId(section && section.id, usedSectionIds, "section", sectionIndex + 1);
        const kind = section && section.kind === "text" ? "text" : "container";
        const normalized = {
            id: sectionId,
            label: section && section.label ? String(section.label) : fallbackLabel,
            tag: section && section.tag ? String(section.tag) : "section_" + (sectionIndex + 1),
            kind: kind,
            rule: section && section.rule === "natural" ? "natural" : (kind === "text" ? "natural" : "tag"),
            text: section && section.text ? String(section.text) : "",
            touched: !!(section && section.touched),
            fields: []
        };

        if (kind === "container") {
            const fields = Array.isArray(section && section.fields) ? section.fields : [];
            normalized.fields = fields.map(function (field, fieldIndex) {
                return normalizeField(field, fieldIndex, usedFieldIds);
            });
        }

        return normalized;
    }

    function normalizeField(field, fieldIndex, usedFieldIds) {
        return {
            id: uniqueId(field && field.id, usedFieldIds, "field", fieldIndex + 1),
            label: field && field.label ? String(field.label) : "子类" + (fieldIndex + 1),
            tag: field && field.tag ? String(field.tag) : "field_" + (fieldIndex + 1),
            value: field && field.value ? String(field.value) : "",
            touched: !!(field && field.touched)
        };
    }

    function uniqueId(candidate, used, prefix, fallbackNumber) {
        let value = String(candidate || "").trim();
        if (!value || used[value]) {
            value = prefix + "_" + fallbackNumber;
            while (used[value]) {
                fallbackNumber += 1;
                value = prefix + "_" + fallbackNumber;
            }
        }
        used[value] = true;
        updateIdCounters(value);
        return value;
    }

    function updateIdCounters(id) {
        const sectionMatch = /^section_(\d+)$/.exec(id);
        if (sectionMatch) {
            state.nextSectionNumber = Math.max(state.nextSectionNumber, Number(sectionMatch[1]) + 1);
        }
        const fieldMatch = /^field_(\d+)$/.exec(id);
        if (fieldMatch) {
            state.nextFieldNumber = Math.max(state.nextFieldNumber, Number(fieldMatch[1]) + 1);
        }
    }

    function hasDraftChanged(target, draft, initialDraft) {
        return normalizeDraftForTarget(target, draft) !== normalizeDraftForTarget(target, initialDraft);
    }

    function sectionRuleForTarget(target) {
        const section = target ? getSectionById(target.sectionId) : null;
        return section ? section.rule : "tag";
    }

    function normalizeDraftForTarget(target, draft) {
        const section = getSectionById(target.sectionId);
        const rule = section ? section.rule : "tag";
        return normalizeTextWithRule(draft, rule, target.type === "section-text");
    }

    function normalizeTextWithRule(text, rule, isTextBlock) {
        const raw = String(text || "").trim();
        if (!raw) {
            return "";
        }
        if (rule === "natural") {
            return isTextBlock ? humanizeText(raw) : joinTags(splitTags(raw).map(humanizeText));
        }
        return isTextBlock ? joinTags(splitTags(raw).map(normalizeTagToken)) : joinTags(splitTags(raw).map(normalizeTagToken));
    }

    function normalizeTagToken(text) {
        return String(text || "")
            .trim()
            .replace(/[_\s]+/g, "_")
            .replace(/^_+|_+$/g, "")
            .toLowerCase();
    }

    function humanizeText(text) {
        return String(text || "")
            .replace(/_/g, " ")
            .replace(/\s+/g, " ")
            .trim();
    }

    function splitTags(text) {
        const seen = {};
        return String(text || "")
            .split(/[\n,]+/)
            .map(function (tag) { return tag.trim(); })
            .filter(Boolean)
            .filter(function (tag) {
                const lookup = normalizeLookup(tag);
                if (seen[lookup]) {
                    return false;
                }
                seen[lookup] = true;
                return true;
            });
    }

    function joinTags(tags) {
        return (tags || []).filter(Boolean).join(", ");
    }

    function normalizeLookup(text) {
        return String(text || "")
            .trim()
            .replace(/^["']|["']$/g, "")
            .replace(/^[({\[]+|[)}\]]+$/g, "")
            .replace(/\s+/g, "_")
            .toLowerCase();
    }

    function xmlLine(tag, value, indent) {
        return indentSpaces(indent) + "<" + tag + ">" + escapeXmlText(value) + "</" + tag + ">";
    }

    function xmlLineStart(tag, indent) {
        return indentSpaces(indent) + "<" + tag + ">";
    }

    function xmlLineEnd(tag, indent) {
        return indentSpaces(indent) + "</" + tag + ">";
    }

    function indentSpaces(indent) {
        return new Array(indent + 1).join(" ");
    }

    function effectiveTag(value, fallback) {
        const raw = String(value || "").trim();
        const cleaned = raw
            .replace(/\s+/g, "_")
            .replace(/[^A-Za-z0-9_:-]/g, "")
            .replace(/^[^A-Za-z_]+/, "");
        return cleaned || fallback;
    }

    function escapeXmlText(value) {
        return escapeUnescapedParens(String(value || ""))
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    }

    function escapeUnescapedParens(value) {
        let result = "";
        for (let index = 0; index < value.length; index += 1) {
            const char = value.charAt(index);
            const previous = index > 0 ? value.charAt(index - 1) : "";
            if ((char === "(" || char === ")") && previous !== "\\") {
                result += "\\" + char;
            } else {
                result += char;
            }
        }
        return result;
    }

    function escapeHtml(value) {
        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function clampSectionCount(value) {
        const numberValue = Number(value);
        if (!Number.isFinite(numberValue)) {
            return 1;
        }
        return Math.max(1, Math.min(MAX_SECTION_COUNT, Math.round(numberValue)));
    }

    async function loadRemoteSettings() {
        try {
            const response = await fetch("/weilin/physton_prompt/get_data?key=" + encodeURIComponent(SETTINGS_KEY));
            const json = await response.json();
            if (json && json.data) {
                mergeSettings(json.data);
                normalizeAllState();
                ensureArchives();
                saveLocalSettings();
                render();
            }
        } catch (error) {
            /* local storage is enough for offline editing */
        }
    }

    async function saveSettings() {
        saveLocalSettings();
        try {
            await fetch("/weilin/physton_prompt/set_data", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    key: SETTINGS_KEY,
                    data: exportSettings()
                })
            });
        } catch (error) {
            /* localStorage keeps the setting when the backend route is unavailable. */
        }
    }

    function loadLocalSettings() {
        try {
            return JSON.parse(localStorage.getItem(SETTINGS_KEY) || "null");
        } catch (error) {
            return null;
        }
    }

    function saveLocalSettings() {
        try {
            localStorage.setItem(SETTINGS_KEY, JSON.stringify(exportSettings()));
        } catch (error) {
            /* ignore storage quota errors */
        }
    }

    function exportSettings() {
        return {
            sectionCount: state.settings.sectionCount,
            sections: state.sections,
            archives: state.archives,
            selectedArchiveId: state.selectedArchiveId
        };
    }

    function mergeSettings(settings) {
        if (!settings || typeof settings !== "object") {
            return;
        }
        if (Array.isArray(settings.sections)) {
            state.sections = settings.sections;
        } else if (settings.characterCount !== undefined) {
            state.sections = createDefaultSections(clampSectionCount(settings.characterCount));
        }
        if (settings.sectionCount !== undefined) {
            state.settings.sectionCount = clampSectionCount(settings.sectionCount);
        } else if (state.sections.length) {
            state.settings.sectionCount = clampSectionCount(state.sections.length);
        }
        if (Array.isArray(settings.archives)) {
            state.archives = settings.archives;
        }
        if (settings.selectedArchiveId) {
            state.selectedArchiveId = settings.selectedArchiveId;
        }
    }

    function getPositiveInput() {
        return document.getElementById("weilin_prompt_text_input") || {
            value: "",
            addEventListener: function () {},
            removeEventListener: function () {},
            dispatchEvent: function () {}
        };
    }

    function getNegativeInput() {
        return document.getElementById("weilin_prompt_text_neg_input") || {
            value: ""
        };
    }
})();
