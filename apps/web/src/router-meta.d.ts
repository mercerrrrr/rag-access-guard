import "vue-router";

declare module "vue-router" {
  interface RouteMeta {
    contextMessage?: string;
    contextTitle?: string;
  }
}

export {};
