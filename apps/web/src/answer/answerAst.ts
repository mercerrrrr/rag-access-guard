export type AnswerInline =
  | { readonly kind: "text"; readonly text: string }
  | { readonly kind: "code"; readonly text: string }
  | { readonly kind: "emphasis" | "strong"; readonly children: readonly AnswerInline[] };

export type AnswerBlock =
  | { readonly kind: "paragraph"; readonly children: readonly AnswerInline[] }
  | { readonly kind: "code"; readonly text: string }
  | { readonly kind: "list"; readonly ordered: boolean; readonly start: number;
      readonly items: readonly (readonly AnswerBlock[])[] };
