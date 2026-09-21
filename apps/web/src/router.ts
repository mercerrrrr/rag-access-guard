import {
  createRouter,
  createWebHistory,
  type RouteRecordRaw,
  type RouterHistory,
} from "vue-router";

const sectionRoutes: RouteRecordRaw[] = [
  {
    path: "/chat",
    name: "chat",
    component: () => import("@/views/SectionView.vue"),
    props: {
      title: "Новый диалог",
      description: "Рабочая область для вопросов к корпоративным документам.",
      heading: "Диалог пока пуст",
      message:
        "Функции вопросов и ответов появятся после настройки защищённого доступа и поиска.",
    },
    meta: {
      contextTitle: "Источники ответа",
      contextMessage:
        "Источники появятся после ответа с проверяемым происхождением.",
    },
  },
  {
    path: "/documents",
    name: "documents",
    component: () => import("@/views/SectionView.vue"),
    props: {
      title: "Документы",
      description: "Управление версиями и индексируемыми материалами.",
      heading: "Реестр документов ещё не подключён",
      message:
        "Загрузка, версии и состояние индексации будут добавлены на следующих этапах.",
    },
  },
  {
    path: "/access",
    name: "access",
    component: () => import("@/views/SectionView.vue"),
    props: {
      title: "Доступ",
      description: "Пользователи, роли и правила доступа к документам.",
      heading: "Настройки доступа ещё не подключены",
      message:
        "Управление прямыми и ролевыми разрешениями появится после реализации модели доступа.",
    },
  },
  {
    path: "/audit",
    name: "audit",
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
  { path: "/", redirect: "/chat" },
  ...sectionRoutes,
  ...developmentRoutes,
  { path: "/:pathMatch(.*)*", redirect: "/chat" },
];

export const createAppRouter = (
  history: RouterHistory = createWebHistory(import.meta.env.BASE_URL),
) =>
  createRouter({
    history,
    routes,
  });
