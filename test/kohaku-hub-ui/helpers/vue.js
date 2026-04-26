import { defineComponent, h } from "vue";

const emitValueUpdate = (emit, value) => {
  emit("update:modelValue", value);
  emit("change", value);
  emit("input", value);
};

const passthroughDiv = (name, attrs = {}) =>
  defineComponent({
    name,
    setup(_, { slots }) {
      return () => h("div", attrs, slots.default ? slots.default() : []);
    },
  });

export const ElementPlusStubs = {
  ElTag: defineComponent({
    name: "ElTag",
    props: {
      type: { type: String, default: "" },
      size: { type: String, default: "" },
      effect: { type: String, default: "" },
    },
    setup(props, { slots }) {
      return () =>
        h(
          "span",
          {
            "data-el-tag": "true",
            "data-type": props.type,
            "data-size": props.size,
            "data-effect": props.effect,
          },
          slots.default ? slots.default() : [],
        );
    },
  }),
  ElButton: defineComponent({
    name: "ElButton",
    props: {
      type: { type: String, default: "" },
      size: { type: String, default: "" },
      loading: { type: Boolean, default: false },
      disabled: { type: Boolean, default: false },
      plain: { type: Boolean, default: false },
      text: { type: Boolean, default: false },
      circle: { type: Boolean, default: false },
    },
    emits: ["click"],
    setup(props, { slots, emit }) {
      return () =>
        h(
          "button",
          {
            type: "button",
            disabled: props.disabled || props.loading,
            "data-el-button": "true",
            "data-type": props.type,
            "data-size": props.size,
            "data-loading": String(props.loading),
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
      value: { type: [String, Number], default: undefined },
      type: { type: String, default: "text" },
      placeholder: { type: String, default: "" },
      size: { type: String, default: "" },
      showPassword: { type: Boolean, default: false },
      readonly: { type: Boolean, default: false },
      maxlength: { type: [String, Number], default: undefined },
    },
    emits: ["update:modelValue", "change", "input"],
    setup(props, { slots, emit, attrs }) {
      return () => {
        const tag = props.type === "textarea" ? "textarea" : "input";
        const value = props.modelValue ?? props.value ?? "";

        return h("div", { "data-el-input": "true" }, [
          slots.prepend
            ? h("span", { "data-slot": "prepend" }, slots.prepend())
            : null,
          slots.prefix
            ? h("span", { "data-slot": "prefix" }, slots.prefix())
            : null,
          h(tag, {
            ...attrs,
            value,
            type: props.type === "textarea" ? undefined : props.type,
            placeholder: props.placeholder,
            readonly: props.readonly,
            maxlength: props.maxlength,
            onInput: (event) => emitValueUpdate(emit, event.target.value),
            onChange: (event) => emitValueUpdate(emit, event.target.value),
          }),
          slots.append
            ? h("span", { "data-slot": "append" }, slots.append())
            : null,
        ]);
      };
    },
  }),
  ElSelect: defineComponent({
    name: "ElSelect",
    props: {
      modelValue: { type: [String, Number], default: "" },
      placeholder: { type: String, default: "" },
    },
    emits: ["update:modelValue", "change", "input"],
    setup(props, { slots, emit }) {
      return () =>
        h(
          "select",
          {
            value: props.modelValue,
            "data-el-select": "true",
            "aria-label": props.placeholder || "select",
            onChange: (event) => emitValueUpdate(emit, event.target.value),
          },
          slots.default ? slots.default() : [],
        );
    },
  }),
  ElOption: defineComponent({
    name: "ElOption",
    props: {
      label: { type: String, default: "" },
      value: { type: [String, Number], default: "" },
    },
    setup(props) {
      return () => h("option", { value: props.value }, props.label);
    },
  }),
  ElCheckbox: defineComponent({
    name: "ElCheckbox",
    props: {
      modelValue: { type: Boolean, default: false },
    },
    emits: ["update:modelValue", "change", "input"],
    setup(props, { slots, emit }) {
      return () =>
        h("label", { "data-el-checkbox": "true" }, [
          h("input", {
            type: "checkbox",
            checked: props.modelValue,
            onChange: (event) => emitValueUpdate(emit, event.target.checked),
          }),
          slots.default ? slots.default() : [],
        ]);
    },
  }),
  ElRadioGroup: defineComponent({
    name: "ElRadioGroup",
    props: {
      modelValue: { type: [String, Number, Boolean], default: "" },
    },
    emits: ["update:modelValue", "change", "input"],
    setup(_, { slots }) {
      return () =>
        h(
          "div",
          { "data-el-radio-group": "true" },
          slots.default ? slots.default() : [],
        );
    },
  }),
  ElRadioButton: defineComponent({
    name: "ElRadioButton",
    props: {
      value: { type: [String, Number, Boolean], default: "" },
    },
    setup(props, { slots }) {
      return () =>
        h(
          "button",
          {
            type: "button",
            "data-el-radio-button": "true",
            "data-value": String(props.value),
          },
          slots.default ? slots.default() : [],
        );
    },
  }),
  ElRadio: defineComponent({
    name: "ElRadio",
    props: {
      value: { type: [String, Number, Boolean], default: "" },
    },
    setup(props, { slots }) {
      return () =>
        h(
          "label",
          {
            "data-el-radio": "true",
            "data-value": String(props.value),
          },
          slots.default ? slots.default() : [],
        );
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
          ? h("section", { "data-el-dialog": props.title || "true" }, [
              props.title ? h("h2", props.title) : null,
              slots.default ? slots.default() : [],
              slots.footer ? h("footer", slots.footer()) : null,
            ])
          : null;
    },
  }),
  ElDrawer: defineComponent({
    name: "ElDrawer",
    props: {
      modelValue: { type: Boolean, default: false },
    },
    emits: ["update:modelValue"],
    setup(props, { slots }) {
      return () =>
        props.modelValue
          ? h(
              "aside",
              { "data-el-drawer": "true" },
              slots.default ? slots.default() : [],
            )
          : null;
    },
  }),
  ElForm: defineComponent({
    name: "ElForm",
    props: {
      model: { type: Object, default: () => ({}) },
      rules: { type: Object, default: () => ({}) },
      labelPosition: { type: String, default: "top" },
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
  ElDropdown: defineComponent({
    name: "ElDropdown",
    setup(_, { slots }) {
      return () =>
        h("div", { "data-el-dropdown": "true" }, [
          slots.default ? slots.default() : [],
          slots.dropdown ? slots.dropdown() : [],
        ]);
    },
  }),
  ElDropdownMenu: passthroughDiv("ElDropdownMenu", {
    "data-el-dropdown-menu": "true",
  }),
  ElDropdownItem: defineComponent({
    name: "ElDropdownItem",
    emits: ["click"],
    setup(_, { slots, emit }) {
      return () =>
        h(
          "button",
          {
            type: "button",
            "data-el-dropdown-item": "true",
            onClick: (event) => emit("click", event),
          },
          slots.default ? slots.default() : [],
        );
    },
  }),
  ElSkeleton: defineComponent({
    name: "ElSkeleton",
    props: {
      loading: { type: Boolean, default: false },
    },
    setup(_, { slots }) {
      return () =>
        h(
          "div",
          { "data-el-skeleton": "true" },
          slots.default ? slots.default() : [],
        );
    },
  }),
  ElBreadcrumb: passthroughDiv("ElBreadcrumb", {
    "data-el-breadcrumb": "true",
  }),
  ElBreadcrumbItem: passthroughDiv("ElBreadcrumbItem", {
    "data-el-breadcrumb-item": "true",
  }),
  ElAlert: defineComponent({
    name: "ElAlert",
    setup(_, { slots }) {
      return () =>
        h("section", { "data-el-alert": "true" }, [
          slots.title
            ? h("div", { "data-slot": "title" }, slots.title())
            : null,
          slots.default ? slots.default() : [],
        ]);
    },
  }),
  ElProgress: defineComponent({
    name: "ElProgress",
    props: {
      percentage: { type: Number, default: 0 },
      status: { type: String, default: "" },
      color: { type: String, default: "" },
      strokeWidth: { type: Number, default: 0 },
      format: { type: Function, default: null },
    },
    setup(props) {
      const label = props.format
        ? props.format(props.percentage)
        : `${props.percentage}%`;
      return () =>
        h(
          "div",
          {
            "data-el-progress": "true",
            "data-percentage": String(props.percentage),
            "data-status": props.status,
            "data-color": props.color,
            "data-stroke-width": String(props.strokeWidth),
          },
          label,
        );
    },
  }),
  ElIcon: passthroughDiv("ElIcon", { "data-el-icon": "true" }),
};

export const InvalidElFormStub = defineComponent({
  name: "ElForm",
  props: {
    model: { type: Object, default: () => ({}) },
    rules: { type: Object, default: () => ({}) },
    labelPosition: { type: String, default: "top" },
  },
  setup(_, { slots, expose }) {
    expose({
      validate(callback) {
        if (callback) {
          callback(false);
        }
        return Promise.resolve(false);
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
});

export const RouterLinkStub = defineComponent({
  name: "RouterLink",
  props: {
    to: {
      type: [String, Object],
      required: true,
    },
  },
  setup(props, { slots }) {
    return () =>
      h(
        "a",
        {
          "data-router-link": "true",
          href:
            typeof props.to === "string" ? props.to : JSON.stringify(props.to),
        },
        slots.default ? slots.default() : [],
      );
  },
});
