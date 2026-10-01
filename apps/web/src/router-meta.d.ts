import "vue-router";

declare module "vue-router" {
  interface RouteMeta {
    chat?: boolean;
    adminOnly?: boolean;
    contextMessage?: string;
    contextTitle?: string;
  }
}

export {};
