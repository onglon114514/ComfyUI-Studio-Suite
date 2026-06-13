import { app } from "/scripts/app.js";

function getContrastTextColor(hexColor) {
    if (typeof hexColor !== "string" || !/^#?[0-9a-fA-F]{6}$/.test(hexColor)) {
        return "#cccccc";
    }
    const hex = hexColor.replace("#", "");
    const r = parseInt(hex.substr(0, 2), 16);
    const g = parseInt(hex.substr(2, 2), 16);
    const b = parseInt(hex.substr(4, 2), 16);
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return luminance > 0.5 ? "#333333" : "#cccccc";
}

const ColorCodeWidget = {
    COLORCODE: (key, val) => {
        const widget = {};
        widget.y = 0;
        widget.name = key;
        widget.type = "COLORCODE";
        widget.options = { default: "#222222" };

        let initial = "#222222";
        if (Array.isArray(val) && val.length > 1 && val[1] && val[1].default) {
            initial = val[1].default;
        }
        if (typeof initial === "string" && /^#?[0-9a-fA-F]{6}$/.test(initial)) {
            if (!initial.startsWith("#")) {
                initial = "#" + initial;
            }
            widget.value = initial;
        } else {
            widget.value = "#222222";
        }

        widget.draw = function (ctx, node, widgetWidth, widgetY, height) {
            const drawHeight = 22;
            const margin = 15;
            const radius = 10;

            ctx.fillStyle = this.value;
            const x = margin;
            const y = widgetY + (height - drawHeight) / 2;
            const w = widgetWidth - margin * 2;
            const h = drawHeight;

            ctx.beginPath();
            ctx.moveTo(x + radius, y);
            ctx.lineTo(x + w - radius, y);
            ctx.arcTo(x + w, y, x + w, y + radius, radius);
            ctx.lineTo(x + w, y + h - radius);
            ctx.arcTo(x + w, y + h, x + w - radius, y + h, radius);
            ctx.lineTo(x + radius, y + h);
            ctx.arcTo(x, y + h, x, y + h - radius, radius);
            ctx.lineTo(x, y + radius);
            ctx.arcTo(x, y, x + radius, y, radius);
            ctx.closePath();
            ctx.fill();

            ctx.strokeStyle = "#555";
            ctx.lineWidth = 1;
            ctx.stroke();

            ctx.fillStyle = getContrastTextColor(this.value);
            ctx.font = "12px sans-serif";
            ctx.textAlign = "center";
            ctx.fillText(`${this.name} (${this.value})`, widgetWidth * 0.5, y + drawHeight * 0.65);
        };

        widget.mouse = function (e, pos, node) {
            if (e.type !== "pointerdown") {
                return false;
            }
            const margin = 15;
            if (pos[0] < margin || pos[0] > node.size[0] - margin) {
                return false;
            }

            const picker = document.createElement("input");
            picker.type = "color";
            picker.value = this.value;
            picker.style.position = "absolute";
            picker.style.left = "-9999px";
            picker.style.top = "-9999px";
            document.body.appendChild(picker);

            picker.addEventListener("change", () => {
                this.value = picker.value;
                node.graph._version++;
                node.setDirtyCanvas(true, true);
                picker.remove();
            });
            picker.click();
            return true;
        };

        widget.computeSize = function (width) {
            return [width, 22];
        };

        return widget;
    },
};

app.registerExtension({
    name: "SmartFillCropResize.colorWidget",
    getCustomWidgets() {
        return {
            COLORCODE: (node, inputName, inputData) => {
                return {
                    widget: node.addCustomWidget(ColorCodeWidget.COLORCODE(inputName, inputData)),
                    minWidth: 150,
                    minHeight: 22,
                };
            },
        };
    },
});
