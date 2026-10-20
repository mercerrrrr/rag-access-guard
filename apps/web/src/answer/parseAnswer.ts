import MarkdownIt, { type Token } from "markdown-it";
import type { AnswerBlock, AnswerInline } from "./answerAst";

const parser = new MarkdownIt({ html: false, linkify: false, typographer: false })
  .disable(["link", "image", "autolink", "reference", "html_block", "html_inline", "table", "heading", "lheading"]);
const byteLimit = 64 * 1024;
const nodeLimit = 10000;
const depthLimit = 32;

class ProjectionError extends Error {}

class Projection {
  private count = 0;

  private countNode(depth: number): void {
    this.count += 1;
    if (this.count > nodeLimit || depth > depthLimit) throw new ProjectionError();
  }

  add<T extends AnswerBlock | AnswerInline>(node: T, depth: number): T {
    this.countNode(depth);
    return node;
  }

  inlines(tokens: readonly Token[], depth: number): readonly AnswerInline[] {
    let index = 0;
    const read = (level: number, closing: string | null): AnswerInline[] => {
      if (level > depthLimit) throw new ProjectionError();
      const nodes: AnswerInline[] = [];
      while (index < tokens.length) {
        const token = tokens[index++];
        if (!token) throw new ProjectionError();
        if (token.type === closing) return nodes;
        switch (token.type) {
          case "text": nodes.push(this.add({ kind: "text", text: token.content }, level)); break;
          case "code_inline": nodes.push(this.add({ kind: "code", text: token.content }, level)); break;
          case "softbreak":
          case "hardbreak": nodes.push(this.add({ kind: "text", text: "\n" }, level)); break;
          case "em_open":
          case "strong_open": {
            const kind = token.type === "em_open" ? "emphasis" : "strong";
            const children = read(level + 1, token.type === "em_open" ? "em_close" : "strong_close");
            nodes.push(this.add({ kind, children }, level));
            break;
          }
          default: throw new ProjectionError();
        }
      }
      if (closing !== null) throw new ProjectionError();
      return nodes;
    };
    return read(depth, null);
  }

  blocks(tokens: readonly Token[]): readonly AnswerBlock[] {
    let index = 0;
    const expect = (type: string): void => {
      if (tokens[index++]?.type !== type) throw new ProjectionError();
    };
    const read = (depth: number, closing: string | null): AnswerBlock[] => {
      if (depth > depthLimit) throw new ProjectionError();
      const nodes: AnswerBlock[] = [];
      while (index < tokens.length) {
        const token = tokens[index++];
        if (!token) throw new ProjectionError();
        if (token.type === closing) return nodes;
        switch (token.type) {
          case "paragraph_open": {
            const inline = tokens[index++];
            if (inline?.type !== "inline" || !inline.children) throw new ProjectionError();
            const children = this.inlines(inline.children, depth + 1);
            expect("paragraph_close");
            nodes.push(this.add({ kind: "paragraph", children }, depth));
            break;
          }
          case "code_block":
          case "fence": nodes.push(this.add({ kind: "code", text: token.content }, depth)); break;
          case "bullet_list_open":
          case "ordered_list_open": {
            const ordered = token.type === "ordered_list_open";
            const end = ordered ? "ordered_list_close" : "bullet_list_close";
            const start = Number(token.attrGet("start") ?? 1);
            if (!Number.isSafeInteger(start) || start < 1) throw new ProjectionError();
            const items: (readonly AnswerBlock[])[] = [];
            while (tokens[index]?.type !== end) {
              expect("list_item_open");
              this.countNode(depth + 1);
              items.push(read(depth + 1, "list_item_close"));
            }
            expect(end);
            nodes.push(this.add({ kind: "list", ordered, start, items }, depth));
            break;
          }
          default: throw new ProjectionError();
        }
      }
      if (closing !== null) throw new ProjectionError();
      return nodes;
    };
    return read(1, null);
  }
}

function plain(text: string): readonly AnswerBlock[] {
  return [{ kind: "paragraph", children: [{ kind: "text", text }] }];
}

export function parseAnswer(text: string): readonly AnswerBlock[] {
  if (new TextEncoder().encode(text).byteLength > byteLimit) return plain(text);
  try {
    return new Projection().blocks(parser.parse(text, {}));
  } catch {
    // A tokenizer failure must leave the complete authorized answer readable.
    return plain(text);
  }
}
