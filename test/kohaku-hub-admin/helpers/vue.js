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
};
