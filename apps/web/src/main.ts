import "@fluentui/web-components/button.js";
import "@fluentui/web-components/divider.js";

import { webLightTheme } from "@fluentui/tokens";
import { setTheme } from "@fluentui/web-components";
import { createApp } from "vue";

import App from "@/App.vue";
import { createAppRouter } from "@/router";
import { sessionApi } from "@/api/session";
import { createSessionState } from "@/composables/useSession";
import { sessionKey } from "@/composables/sessionContext";
import "@/styles/tokens.css";
import "@/styles/base.css";
import "@/styles/session.css";

setTheme(webLightTheme);

const session = createSessionState(sessionApi);
createApp(App).provide(sessionKey, session).use(createAppRouter(undefined, session)).mount("#app");
