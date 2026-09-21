import js from "@eslint/js";
import vue from "eslint-plugin-vue";
import tseslint from "typescript-eslint";
import vueParser from "vue-eslint-parser";

export default tseslint.config(
  {
    ignores: ["dist/**", "coverage/**", "node_modules/**"],
  },
  {
    files: ["**/*.js"],
    extends: [js.configs.recommended],
  },
  {
    files: ["**/*.ts"],
    extends: [js.configs.recommended, ...tseslint.configs.strictTypeChecked],
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
  ...vue.configs["flat/recommended"],
  {
    files: ["**/*.vue"],
    extends: [...tseslint.configs.strictTypeChecked],
    languageOptions: {
      parser: vueParser,
      parserOptions: {
        extraFileExtensions: [".vue"],
        parser: tseslint.parser,
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      "vue/block-order": ["error", { "order": ["script", "template", "style"] }],
      "vue/component-api-style": ["error", ["script-setup"]],
      "vue/component-name-in-template-casing": ["error", "PascalCase"],
      "vue/multi-word-component-names": "off",
    },
  },
);
