<script setup>
/**
 * ProbeReportView — render a chain-tester ProbeReport timeline.
 *
 * Used by both modes of the chain tester (Draft simulate + Live real
 * probe) on ``fallback-sources.vue`` so the timeline rendering stays
 * in one place and the two modes share styles + behaviour. The
 * ``data-testid-prefix`` prop scopes the data-testid attributes per
 * mode (``sim-probe-...`` vs ``real-probe-...``) so vitest selectors
 * don't collide when both modes have rendered a report.
 *
 * The report shape mirrors the backend ``ProbeReport`` (see
 * ``src/kohakuhub/api/fallback/core.py``) plus the ``kind`` field
 * the simulate endpoint annotates onto each attempt
 * (``"local"`` for the local hop, ``"fallback"`` for the source
 * hops). For the real-probe mode the attempts come from
 * ``runFallbackProbe`` which also stamps ``kind`` from the
 * X-Chain-Trace decoder, so the same component renders both.
 */
defineProps({
  report: {
    type: Object,
    required: true,
  },
  // testId prefix applied to every data-testid this component emits
  // — pass "sim-probe" for the draft-simulate panel and "real-probe"
  // for the live-real-probe panel so both timelines coexist on the
  // same page without selector collision.
  dataTestidPrefix: {
    type: String,
    default: "probe",
  },
  // Caller supplies the el-tag colour mapper since it's shared with
  // the surrounding page (e.g. the page itself uses
  // ``decisionTagType`` elsewhere too).
  decisionTagType: {
    type: Function,
    required: true,
  },
});
</script>

<template>
  <div class="probe-report" :data-testid="`${dataTestidPrefix}-report`">
    <div class="probe-report-summary">
      <strong>Final outcome:</strong>
      <el-tag
        :type="decisionTagType(report.final_outcome)"
        :data-testid="`${dataTestidPrefix}-final-outcome`"
      >
        {{ report.final_outcome }}
      </el-tag>
      <span v-if="report.bound_source" class="probe-bound-source">
        bound to
        <code :data-testid="`${dataTestidPrefix}-bound-source`">
          {{ report.bound_source.name || report.bound_source.url || "local" }}
        </code>
      </span>
      <span class="probe-duration">
        {{ report.duration_ms }} ms total
      </span>
    </div>
    <div class="probe-attempts">
      <div
        v-for="(att, idx) in report.attempts"
        :key="idx"
        class="probe-attempt"
        :class="{ 'probe-attempt-local': att.kind === 'local' }"
        :data-testid="`${dataTestidPrefix}-attempt-${idx}`"
      >
        <div class="probe-attempt-line">
          <el-tag
            v-if="att.kind"
            size="small"
            :type="att.kind === 'local' ? 'info' : ''"
            class="probe-attempt-kind"
          >
            {{ att.kind }}
          </el-tag>
          <el-tag :type="decisionTagType(att.decision)" size="small">
            {{ att.decision }}
          </el-tag>
          <code class="probe-attempt-source">
            {{ att.source_name }}
          </code>
          <span class="probe-attempt-method">
            {{ att.method }}
          </span>
          <span class="probe-attempt-status">
            <span v-if="att.status_code">{{ att.status_code }}</span>
            <span v-else class="probe-attempt-error">no response</span>
          </span>
          <span v-if="att.x_error_code" class="probe-attempt-xerror">
            X-Error-Code: {{ att.x_error_code }}
          </span>
          <span class="probe-attempt-ms">
            {{ att.duration_ms }} ms
          </span>
        </div>
        <div v-if="att.upstream_path" class="probe-attempt-path">
          <span class="probe-label">Upstream:</span>
          <code>{{ att.upstream_path }}</code>
        </div>
        <div
          v-if="att.response_headers && Object.keys(att.response_headers).length > 0"
          class="probe-attempt-headers"
          :data-testid="`${dataTestidPrefix}-attempt-${idx}-headers`"
        >
          <span class="probe-label">Response headers:</span>
          <code
            v-for="(val, key) in att.response_headers"
            :key="key"
            class="probe-attempt-header"
          >
            {{ key }}: {{ val }}
          </code>
        </div>
        <details
          v-if="att.response_body_preview"
          class="probe-attempt-body"
        >
          <summary>
            Response body preview ({{
              att.response_body_preview.length
            }}
            chars)
          </summary>
          <pre :data-testid="`${dataTestidPrefix}-attempt-${idx}-body`">{{ att.response_body_preview }}</pre>
        </details>
        <div v-if="att.error" class="probe-attempt-error-detail">
          {{ att.error }}
        </div>
      </div>
    </div>

    <div
      v-if="report.final_response"
      class="probe-final-response"
      :data-testid="`${dataTestidPrefix}-final-response`"
    >
      <h4 class="probe-final-title">
        Final response (what a production caller would see)
      </h4>
      <div class="probe-final-status">
        <span class="probe-label">Status:</span>
        <strong>{{ report.final_response.status_code }}</strong>
      </div>
      <div
        v-if="report.final_response.headers && Object.keys(report.final_response.headers).length > 0"
        class="probe-final-headers"
      >
        <span class="probe-label">Headers:</span>
        <code
          v-for="(val, key) in report.final_response.headers"
          :key="key"
          class="probe-attempt-header"
        >
          {{ key }}: {{ val }}
        </code>
      </div>
      <details
        v-if="report.final_response.body_preview"
        open
        class="probe-attempt-body"
      >
        <summary>Body</summary>
        <pre :data-testid="`${dataTestidPrefix}-final-body`">{{ report.final_response.body_preview }}</pre>
      </details>
    </div>
  </div>
</template>

<style scoped>
.probe-report {
  margin-top: 16px;
  padding: 12px 16px;
  background: var(--el-fill-color-lighter, #f8f8f8);
  border-radius: 8px;
}

.probe-report-summary {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 14px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.probe-bound-source code {
  font-family: var(--el-font-family-mono, monospace);
  background: var(--el-fill-color, #ececec);
  padding: 2px 6px;
  border-radius: 4px;
}

.probe-duration {
  color: var(--el-text-color-secondary, #909399);
  font-size: 12px;
  margin-left: auto;
}

.probe-attempts {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.probe-attempt {
  background: var(--el-bg-color, #fff);
  padding: 10px 12px;
  border-radius: 6px;
  border-left: 3px solid var(--el-border-color, #dcdfe6);
}

.probe-attempt-local {
  border-left-color: var(--el-color-info, #909399);
}

.probe-attempt-line {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  flex-wrap: wrap;
}

.probe-attempt-source {
  font-family: var(--el-font-family-mono, monospace);
  font-weight: 600;
}

.probe-attempt-method {
  color: var(--el-text-color-secondary, #909399);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.5px;
}

.probe-attempt-status {
  font-weight: 600;
}

.probe-attempt-error {
  color: var(--el-color-danger, #f56c6c);
}

.probe-attempt-xerror {
  font-size: 12px;
  color: var(--el-color-warning-dark-2, #b88230);
}

.probe-attempt-ms {
  margin-left: auto;
  color: var(--el-text-color-secondary, #909399);
  font-size: 11px;
}

.probe-attempt-path,
.probe-attempt-headers {
  margin-top: 6px;
  font-size: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: baseline;
}

.probe-attempt-path code,
.probe-attempt-header {
  font-family: var(--el-font-family-mono, monospace);
  background: var(--el-fill-color, #ececec);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
}

.probe-attempt-body {
  margin-top: 6px;
  font-size: 12px;
}

.probe-attempt-body summary {
  cursor: pointer;
  color: var(--el-color-primary, #409eff);
}

.probe-attempt-body pre {
  background: var(--el-fill-color, #ececec);
  padding: 8px;
  border-radius: 4px;
  overflow-x: auto;
  font-size: 11px;
  max-height: 240px;
}

.probe-attempt-error-detail {
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-color-danger, #f56c6c);
}

.probe-label {
  color: var(--el-text-color-secondary, #909399);
  font-size: 11px;
  font-weight: 600;
}

.probe-final-response {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--el-border-color, #dcdfe6);
}

.probe-final-title {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--el-text-color-primary, #303133);
}

.probe-final-status,
.probe-final-headers {
  margin-bottom: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: baseline;
  font-size: 12px;
}
</style>
