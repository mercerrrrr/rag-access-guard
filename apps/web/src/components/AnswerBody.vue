<script setup lang="ts">
import { computed } from "vue";
import { parseAnswer } from "@/answer/parseAnswer";
import AnswerBlocks from "@/components/AnswerBlocks.vue";
const props = defineProps<{ readonly text: string }>();
const nodes = computed(() => parseAnswer(props.text));
</script>

<template>
  <div class="answer-body">
    <AnswerBlocks :nodes="nodes" />
  </div>
</template>
