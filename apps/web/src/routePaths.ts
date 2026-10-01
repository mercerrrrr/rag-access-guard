import { z } from "zod";

export function safeReturnPath(value: unknown): string {
  if (typeof value !== "string") return "/chat";
  if (["/chat", "/documents", "/access", "/audit"].includes(value)) return value;
  return value.startsWith("/chat/") && z.uuid().safeParse(value.slice(6)).success ? value : "/chat";
}
