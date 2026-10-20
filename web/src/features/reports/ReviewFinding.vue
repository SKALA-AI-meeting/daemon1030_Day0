<script setup>
defineProps({ finding: { type: Object, required: true }, sources: { type: Object, default: () => ({}) } })
function sourceLabel(source) {
  if (!source) return '원본 기록'
  if (source.kind === 'final_section') return `최종 제출물 · ${source.title}`
  if (source.kind === 'reference_document') return `참고 자료 · ${source.title}`
  return `경과 ${Math.floor(source.elapsed / 60)}분 · ${source.kind === 'document.updated' ? '문서 변경' : '대화·상황 기록'}`
}
</script>
<template>
  <div class="review-finding"><p>{{ finding.text }}</p><details v-if="finding.evidence?.length"><summary>판단 근거 {{ finding.evidence.length }}개 확인</summary><blockquote v-for="(citation,index) in finding.evidence" :key="citation.source_id + index"><small>{{ sourceLabel(sources[citation.source_id]) }}</small><p>{{ citation.quote }}</p></blockquote></details></div>
</template>
<style scoped>
.review-finding > p { margin-bottom: 8px; font-size: 13px; line-height: 1.9; color: #414b60; white-space: pre-wrap; }
.review-finding details { font-size: 11px; margin-bottom: 12px; }
.review-finding summary { cursor: pointer; color: #4876ef; }
.review-finding blockquote { margin: 12px 0; padding: 12px 16px; border-left: 2px solid #b1c7fa; background: #f7faff; }
.review-finding blockquote small { font-size: 10px; color: #68788f; }
.review-finding blockquote p { font-size: 12px; color: #52627d; margin: 6px 0 0; white-space: pre-wrap; }
</style>
