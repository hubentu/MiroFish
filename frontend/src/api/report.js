import service from './index'

/**
 * 开始报告生成
 * @param {Object} data - { simulation_id, force_regenerate? }
 */
export const generateReport = (data) => {
  return service.post('/api/report/generate', data)
}

/**
 * 获取报告生成状态
 * @param {string} reportId
 */
export const getReportStatus = (reportId) => {
  return service.get(`/api/report/generate/status`, { params: { report_id: reportId } })
}

/**
 * 获取 Agent 日志（增量）
 * @param {string} reportId
 * @param {number} fromLine - 从第几行开始获取
 */
export const getAgentLog = (reportId, fromLine = 0) => {
  return service.get(`/api/report/${reportId}/agent-log`, { params: { from_line: fromLine } })
}

/**
 * 获取控制台日志（增量）
 * @param {string} reportId
 * @param {number} fromLine - 从第几行开始获取
 */
export const getConsoleLog = (reportId, fromLine = 0) => {
  return service.get(`/api/report/${reportId}/console-log`, { params: { from_line: fromLine } })
}

/**
 * 获取报告详情
 * @param {string} reportId
 */
export const getReport = (reportId) => {
  return service.get(`/api/report/${reportId}`)
}

/**
 * 获取已生成的报告章节（导入后无 agent-log 时用）
 * @param {string} reportId
 */
export const getReportSections = (reportId) => {
  return service.get(`/api/report/${reportId}/sections`)
}

/**
 * 与 Report Agent 对话
 * @param {Object} data - { simulation_id, message, chat_history? }
 */
export const chatWithReport = (data) => {
  return service.post('/api/report/chat', data)
}

/**
 * 导出可转移的报告包。
 * Blob 响应绕过只处理 JSON 的 axios 响应拦截器。
 * @param {string} reportId
 */
export const exportReportPackage = async (reportId) => {
  const baseURL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'
  const response = await fetch(`${baseURL}/api/report/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ report_id: reportId })
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new Error(error.error || error.message || `Export failed (${response.status})`)
  }

  return response.blob()
}

/**
 * 导入可转移的报告包。
 * @param {File} file
 */
export const importReportPackage = (file) => {
  const form = new FormData()
  form.append('file', file)
  // Content-Type left unset so axios interceptor + browser set multipart boundary
  return service.post('/api/report/import', form)
}
