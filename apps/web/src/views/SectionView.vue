<script setup lang="ts">
import PageHeader from "@/components/PageHeader.vue";
import SectionPlaceholder from "@/components/SectionPlaceholder.vue";

defineProps<{
  description: string;
  heading: string;
  message: string;
  title: string;
}>();
</script>

<template>
  <div class="section-view">
    <PageHeader
      :description="description"
      :title="title"
    />
    <SectionPlaceholder
      :heading="heading"
      :message="message"
    />
  </div>
</template>

<style scoped>
.section-view {
  min-block-size: 100%;
}
</style>
