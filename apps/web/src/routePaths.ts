export function safeReturnPath(value: unknown): string {
  return typeof value === "string" && ["/chat", "/documents", "/access", "/audit"].includes(value)
    ? value : "/chat";
}
