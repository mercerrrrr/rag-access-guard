import { z } from "zod";

export const documentOriginSchema = z.strictObject({
  kind: z.enum(["official_public", "synthetic_demo", "user_upload"]),
  publisher: z.string().min(1).max(200),
  source_url: z.url({ protocol: /^https?$/ }).nullable(),
  retrieved_at: z.iso.datetime().nullable(), published_on: z.iso.date().nullable(),
  source_sha256: z.string().regex(/^[0-9a-f]{64}$/),
  transformation_revision: z.string().min(1).max(100),
}).readonly();
export type DocumentOrigin = z.infer<typeof documentOriginSchema>;
