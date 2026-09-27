import "vue-router";

declare module "vue-router" {
  interface RouteMeta {
    adminOnly?: boolean;
    contextMessage?: string;
    contextTitle?: string;
  }
}

export {};
