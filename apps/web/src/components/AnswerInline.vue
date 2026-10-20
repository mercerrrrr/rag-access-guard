<script setup lang="ts">
import type { AnswerInline as AnswerInlineNode } from "@/answer/answerAst";
defineProps<{ readonly nodes: readonly AnswerInlineNode[] }>();
</script>

<template>
  <template
    v-for="(node, index) in nodes"
    :key="index"
  >
    <template v-if="node.kind === 'text'">
      {{ node.text }}
    </template>
    <code v-else-if="node.kind === 'code'">{{ node.text }}</code>
    <em v-else-if="node.kind === 'emphasis'"><AnswerInline :nodes="node.children" /></em>
    <strong v-else><AnswerInline :nodes="node.children" /></strong>
  </template>
</template>
