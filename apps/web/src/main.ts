import "@fluentui/web-components/button.js";
import "@fluentui/web-components/divider.js";

import { webLightTheme } from "@fluentui/tokens";
import { setTheme } from "@fluentui/web-components";
import { createApp } from "vue";

import App from "@/App.vue";
import { createAppRouter } from "@/router";
import "@/styles/tokens.css";
import "@/styles/base.css";

setTheme(webLightTheme);

createApp(App).use(createAppRouter()).mount("#app");
