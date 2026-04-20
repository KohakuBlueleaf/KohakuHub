import { defineComponent, h } from "vue";

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
};

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
          href: typeof props.to === "string" ? props.to : JSON.stringify(props.to),
        },
        slots.default ? slots.default() : [],
      );
  },
});
