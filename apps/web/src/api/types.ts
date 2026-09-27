import { z } from "zod";

export const userSchema = z.object({
  id: z.uuid(), login: z.string(), display_name: z.string(), is_admin: z.boolean(),
}).readonly();
export const sessionSchema = z.object({ user: userSchema }).readonly();
export const csrfSchema = z.object({ csrf_token: z.string().min(1) }).readonly();
export const loginSchema = z.object({ user: userSchema, csrf_token: z.string().min(1) }).readonly();
export type UserDto = z.infer<typeof userSchema>;
export type SessionDto = z.infer<typeof sessionSchema>;
export type CsrfDto = z.infer<typeof csrfSchema>;
export type LoginDto = z.infer<typeof loginSchema>;

export interface SessionApi {
  me(signal: AbortSignal): Promise<SessionDto>;
  csrf(signal: AbortSignal): Promise<CsrfDto>;
  login(credentials: { readonly login: string; readonly password: string },
    csrfToken: string, signal: AbortSignal): Promise<LoginDto>;
  logout(csrfToken: string, signal: AbortSignal): Promise<void>;
}
