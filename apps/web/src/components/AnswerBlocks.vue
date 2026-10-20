<script setup lang="ts">
import type { AnswerBlock as AnswerBlockNode } from "@/answer/answerAst";
import AnswerInline from "@/components/AnswerInline.vue";
defineProps<{ readonly nodes: readonly AnswerBlockNode[] }>();
</script>

<template>
  <template
    v-for="(node, index) in nodes"
    :key="index"
  >
    <p v-if="node.kind === 'paragraph'">
      <AnswerInline :nodes="node.children" />
    </p>
    <pre v-else-if="node.kind === 'code'"><code>{{ node.text }}</code></pre>
    <ol
      v-else-if="node.ordered"
      :start="node.start"
    >
      <li
        v-for="(item, itemIndex) in node.items"
        :key="itemIndex"
      >
        <AnswerBlocks :nodes="item" />
      </li>
    </ol>
    <ul v-else>
      <li
        v-for="(item, itemIndex) in node.items"
        :key="itemIndex"
      >
        <AnswerBlocks :nodes="item" />
      </li>
    </ul>
  </template>
</template>
