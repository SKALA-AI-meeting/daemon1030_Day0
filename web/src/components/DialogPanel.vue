<script setup>
import { onMounted, ref } from 'vue'
defineProps({ title: { type: String, required: true } })
const emit = defineEmits(['close'])
const dialog = ref(null)
onMounted(() => dialog.value.showModal())
</script>
<template><dialog ref="dialog" class="app-dialog" :aria-label="title" @cancel.prevent="emit('close')" @click.self="emit('close')"><div class="dialog-content"><div class="review-heading"><h2>{{ title }}</h2><button class="icon-close" aria-label="닫기" @click="emit('close')">×</button></div><slot /></div></dialog></template>
