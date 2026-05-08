import { defineComponent, h } from "vue";

const emitValueUpdate = (emit, value) => {
  emit("update:modelValue", value);
  emit("change", value);
  emit("input", value);
};

const passthroughDiv = (name, attrs = {}) =>
  defineComponent({
    name,
    props: {
      width: { type: String, default: "" },
      defaultActive: { type: String, default: "" },
      router: { type: Boolean, default: false },
      backgroundColor: { type: String, default: "" },
      textColor: { type: String, default: "" },
      activeTextColor: { type: String, default: "" },
      index: { type: String, default: "" },
    },
    setup(props, { slots }) {
      return () =>
        h(
          "div",
          {
            ...attrs,
            "data-width": props.width,
            "data-default-active": props.defaultActive,
            "data-router": String(props.router),
            "data-background-color": props.backgroundColor,
            "data-text-color": props.textColor,
            "data-active-text-color": props.activeTextColor,
            "data-index": props.index,
          },
          slots.default ? slots.default() : [],
        );
    },
  });

export const ElementPlusStubs = {
  ElButton: defineComponent({
    name: "ElButton",
    props: {
      type: { type: String, default: "" },
      size: { type: String, default: "" },
      loading: { type: Boolean, default: false },
      disabled: { type: Boolean, default: false },
      circle: { type: Boolean, default: false },
      nativeType: { type: String, default: "button" },
    },
    emits: ["click"],
    setup(props, { slots, emit }) {
      return () =>
        h(
          "button",
          {
            type: props.nativeType || "button",
            disabled: props.disabled || props.loading,
            "data-el-button": "true",
            "data-type": props.type,
            "data-size": props.size,
            "data-loading": String(props.loading),
            "data-circle": String(props.circle),
            onClick: (event) => emit("click", event),
          },
          slots.default ? slots.default() : [],
        );
    },
  }),
  ElInput: defineComponent({
    name: "ElInput",
    props: {
      modelValue: { type: [String, Number], default: "" },
      type: { type: String, default: "text" },
      placeholder: { type: String, default: "" },
      autocomplete: { type: String, default: "" },
    },
    emits: ["update:modelValue", "change", "input"],
    setup(props, { slots, emit }) {
      return () =>
        h("div", { "data-el-input": "true" }, [
          slots.prepend
            ? h("span", { "data-slot": "prepend" }, slots.prepend())
            : null,
          h("input", {
            value: props.modelValue ?? "",
            type: props.type,
            placeholder: props.placeholder,
            autocomplete: props.autocomplete,
            onInput: (event) => emitValueUpdate(emit, event.target.value),
            onChange: (event) => emitValueUpdate(emit, event.target.value),
          }),
        ]);
    },
  }),
  ElForm: defineComponent({
    name: "ElForm",
    props: {
      model: { type: Object, default: () => ({}) },
    },
    setup(_, { slots, expose }) {
      expose({
        validate(callback) {
          if (callback) {
            callback(true);
          }
          return Promise.resolve(true);
        },
        resetFields() {
          return undefined;
        },
      });

      return () =>
        h(
          "form",
          { "data-el-form": "true" },
          slots.default ? slots.default() : [],
        );
    },
  }),
  ElFormItem: passthroughDiv("ElFormItem", { "data-el-form-item": "true" }),
  ElAlert: defineComponent({
    name: "ElAlert",
    setup(_, { slots }) {
      return () =>
        h("section", { "data-el-alert": "true" }, [
          slots.default ? slots.default() : [],
        ]);
    },
  }),
  ElTag: defineComponent({
    name: "ElTag",
    props: {
      size: { type: String, default: "" },
      effect: { type: String, default: "" },
      type: { type: String, default: "" },
    },
    setup(props, { slots }) {
      return () =>
        h(
          "span",
          {
            "data-el-tag": "true",
            "data-size": props.size,
            "data-effect": props.effect,
            "data-type": props.type,
          },
          slots.default ? slots.default() : [],
        );
    },
  }),
  ElContainer: passthroughDiv("ElContainer", { "data-el-container": "true" }),
  ElAside: passthroughDiv("ElAside", { "data-el-aside": "true" }),
  ElHeader: passthroughDiv("ElHeader", { "data-el-header": "true" }),
  ElMain: passthroughDiv("ElMain", { "data-el-main": "true" }),
  ElMenu: passthroughDiv("ElMenu", { "data-el-menu": "true" }),
  ElMenuItem: passthroughDiv("ElMenuItem", { "data-el-menu-item": "true" }),
  ElCard: defineComponent({
    name: "ElCard",
    props: {
      shadow: { type: String, default: "" },
    },
    setup(props, { slots }) {
      return () =>
        h(
          "section",
          { "data-el-card": "true", "data-shadow": props.shadow },
          [
            slots.header
              ? h("header", { "data-el-card-header": "true" }, slots.header())
              : null,
            slots.default
              ? h("div", { "data-el-card-body": "true" }, slots.default())
              : null,
          ],
        );
    },
  }),
  ElEmpty: defineComponent({
    name: "ElEmpty",
    props: {
      description: { type: String, default: "" },
    },
    setup(props, { slots }) {
      return () =>
        h(
          "div",
          {
            "data-el-empty": "true",
            "data-description": props.description,
          },
          slots.default ? slots.default() : [],
        );
    },
  }),
  ElSelect: defineComponent({
    name: "ElSelect",
    props: {
      modelValue: { type: [String, Number, Array, Boolean], default: null },
      placeholder: { type: String, default: "" },
    },
    emits: ["update:modelValue", "change"],
    setup(props, { slots, emit }) {
      return () =>
        h(
          "select",
          {
            "data-el-select": "true",
            value: props.modelValue,
            placeholder: props.placeholder,
            onChange: (event) => {
              const raw = event.target.value;
              const parsed = raw === "" ? "" : Number.isNaN(Number(raw)) ? raw : Number(raw);
              emit("update:modelValue", parsed);
              emit("change", parsed);
            },
          },
          slots.default ? slots.default() : [],
        );
    },
  }),
  ElOption: defineComponent({
    name: "ElOption",
    props: {
      label: { type: String, default: "" },
      value: { type: [String, Number, Boolean], default: "" },
    },
    setup(props) {
      return () =>
        h("option", { value: props.value }, props.label);
    },
  }),
  ElTabs: defineComponent({
    name: "ElTabs",
    props: {
      modelValue: { type: String, default: "" },
    },
    emits: ["update:modelValue"],
    setup(props, { slots, emit }) {
      return () =>
        h(
          "div",
          {
            "data-el-tabs": "true",
            "data-active": props.modelValue,
            onClick: (event) => {
              const target = event.target.closest("[data-tab]");
              if (target) {
                emit("update:modelValue", target.getAttribute("data-tab"));
              }
            },
          },
          slots.default ? slots.default() : [],
        );
    },
  }),
  ElTabPane: defineComponent({
    name: "ElTabPane",
    props: {
      label: { type: String, default: "" },
      name: { type: String, default: "" },
    },
    setup(props, { slots }) {
      return () =>
        h(
          "section",
          { "data-tab": props.name, "data-tab-label": props.label },
          slots.default ? slots.default() : [],
        );
    },
  }),
  ElTable: defineComponent({
    name: "ElTable",
    props: {
      data: { type: Array, default: () => [] },
    },
    setup(props, { slots }) {
      return () => {
        const rendered = (props.data || []).map((row, index) =>
          h(
            "tr",
            { "data-el-table-row": String(index) },
            (slots.default ? slots.default() : []).map((vnode) =>
              vnode && vnode.props && vnode.props.label
                ? h(
                    "td",
                    { "data-col-label": vnode.props.label },
                    vnode.children && vnode.children.default
                      ? vnode.children.default({ row })
                      : String(row[vnode.props.prop] ?? ""),
                  )
                : null,
            ),
          ),
        );
        return h(
          "table",
          { "data-el-table": "true", "data-row-count": props.data?.length ?? 0 },
          rendered,
        );
      };
    },
  }),
  ElTableColumn: defineComponent({
    name: "ElTableColumn",
    props: {
      label: { type: String, default: "" },
      prop: { type: String, default: "" },
      width: { type: [String, Number], default: "" },
    },
    setup(props, { slots }) {
      // Render is delegated to ElTable, but we still need to expose slots so
      // that vnode.children.default works in the parent.
      return () =>
        h(
          "template",
          { "data-el-column": props.prop, "data-label": props.label },
          slots.default ? slots.default({}) : [],
        );
    },
  }),
  ElCheckbox: defineComponent({
    name: "ElCheckbox",
    props: {
      modelValue: { type: Boolean, default: false },
    },
    emits: ["update:modelValue", "change"],
    setup(props, { slots, emit }) {
      return () =>
        h("label", { "data-el-checkbox": "true" }, [
          h("input", {
            type: "checkbox",
            checked: !!props.modelValue,
            onChange: (event) => {
              emit("update:modelValue", event.target.checked);
              emit("change", event.target.checked);
            },
          }),
          slots.default ? slots.default() : null,
        ]);
    },
  }),
  ElInputNumber: defineComponent({
    name: "ElInputNumber",
    props: {
      modelValue: { type: [Number, String], default: null },
      placeholder: { type: String, default: "" },
    },
    emits: ["update:modelValue", "change"],
    setup(props, { emit }) {
      return () =>
        h("input", {
          type: "number",
          value: props.modelValue ?? "",
          placeholder: props.placeholder,
          "data-el-input-number": "true",
          onInput: (event) => {
            const raw = event.target.value;
            const parsed = raw === "" ? null : Number(raw);
            emit("update:modelValue", parsed);
            emit("change", parsed);
          },
        });
    },
  }),
  ElPagination: defineComponent({
    name: "ElPagination",
    props: {
      currentPage: { type: Number, default: 1 },
      pageSize: { type: Number, default: 10 },
      total: { type: Number, default: 0 },
    },
    emits: ["current-change", "update:currentPage"],
    setup(props) {
      return () =>
        h("nav", {
          "data-el-pagination": "true",
          "data-current": props.currentPage,
          "data-total": props.total,
        });
    },
  }),
  ElDialog: defineComponent({
    name: "ElDialog",
    props: {
      modelValue: { type: Boolean, default: false },
      title: { type: String, default: "" },
    },
    emits: ["update:modelValue"],
    setup(props, { slots }) {
      return () =>
        props.modelValue
          ? h(
              "section",
              { "data-el-dialog": "true", "data-title": props.title },
              [
                slots.default ? slots.default() : null,
                slots.footer ? h("footer", slots.footer()) : null,
              ],
            )
          : null;
    },
  }),
};
