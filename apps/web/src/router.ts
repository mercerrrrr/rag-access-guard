import {
  createRouter,
  createWebHistory,
  type RouteRecordRaw,
  type RouterHistory,
} from "vue-router";

import { sessionApi } from "@/api/session";
import { createSessionState, type SessionState } from "@/composables/useSession";
import { safeReturnPath } from "@/routePaths";

const sectionRoutes: RouteRecordRaw[] = [
  {
    path: "/chat",
    name: "chat",
    component: () => import("@/views/ChatView.vue"),
    meta: {
      chat: true,
      contextTitle: "Источники ответа",
      contextMessage:
        "Источники появятся после ответа с проверяемым происхождением.",
    },
  },
  {
    path: "/chat/:threadId",
    name: "chat-thread",
    component: () => import("@/views/ChatView.vue"),
    meta: { chat: true, contextTitle: "Источники ответа" },
  },
  {
    path: "/documents",
    name: "documents",
    component: () => import("@/views/DocumentsView.vue"),
  },
  {
    path: "/access",
    name: "access",
    meta: { adminOnly: true },
    component: () => import("@/views/PermissionsView.vue"),
  },
  {
    path: "/audit",
    name: "audit",
    meta: { adminOnly: true },
    component: () => import("@/views/SectionView.vue"),
    props: {
      title: "Аудит",
      description: "Журнал решений и изменений политики безопасности.",
      heading: "Журнал аудита ещё не подключён",
      message:
        "События будут отображаться здесь после появления защищённых операций.",
    },
  },
];

const developmentRoutes: RouteRecordRaw[] = import.meta.env.DEV
  ? [
      {
        path: "/__design-system",
        name: "design-system",
        component: () => import("@/dev/DesignSystemPreview.vue"),
      },
    ]
  : [];

const routes: RouteRecordRaw[] = [
  { path: "/login", name: "login", component: () => import("@/views/LoginView.vue") },
  { path: "/", redirect: "/chat" },
  ...sectionRoutes,
  ...developmentRoutes,
  { path: "/:pathMatch(.*)*", redirect: "/chat" },
];

export const createAppRouter = (
  history: RouterHistory = createWebHistory(import.meta.env.BASE_URL),
  session: SessionState = createSessionState(sessionApi),
) => {
  const router = createRouter({
    history,
    routes,
  });
  router.beforeEach(async (to) => {
    if (import.meta.env.DEV && to.name === "design-system") return;
    if (session.status.value === "loading" || to.name !== "login") await session.refresh();
    if (to.name === "login") {
      if (session.status.value === "authenticated") return safeReturnPath(to.query["returnTo"]);
      return;
    }
    if (session.status.value !== "authenticated") {
      return { name: "login", query: { returnTo: safeReturnPath(to.path) } };
    }
    if (to.meta.adminOnly && !session.user.value?.is_admin) return "/chat";
  });
  router.afterEach((to) => {
    document.title = `${to.name === "login" ? "Вход" : "Корпоративный поиск"} — RAG Access Guard`;
  });
  return router;
};
