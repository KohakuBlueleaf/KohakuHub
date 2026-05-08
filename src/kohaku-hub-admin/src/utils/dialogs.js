/**
 * Thin wrappers around Element Plus's imperative singletons (ElMessage and
 * ElMessageBox).
 *
 * The motivation is testability: vitest's ``vi.mock("element-plus", ...)``
 * does not intercept the singleton imports the page uses (see vitest issue
 * with deep-tree dependencies that ship via an extra ``node_modules`` layer
 * under ``src/kohaku-hub-admin``). Routing every dialog through a module
 * under ``@/utils`` lets tests mock this file in the standard way and assert
 * on confirmations, success toasts and error toasts deterministically.
 */

import { ElMessage, ElMessageBox } from "element-plus";

export function showSuccess(message) {
  return ElMessage.success(message);
}

export function showError(message) {
  return ElMessage.error(message);
}

export function showWarning(message) {
  return ElMessage.warning(message);
}

export function showInfo(message) {
  return ElMessage.info(message);
}

/**
 * Show a confirmation dialog. Resolves when the operator clicks the confirm
 * button, rejects otherwise (e.g. cancel or backdrop click). Callers should
 * wrap the call in ``try / catch`` and treat rejection as cancel.
 *
 * @param {string} title
 * @param {string} message
 * @param {Object} [options]
 * @param {string} [options.confirmText="Confirm"]
 * @param {string} [options.cancelText="Cancel"]
 * @param {string} [options.type="warning"]
 * @returns {Promise<void>}
 */
export function confirmDialog(title, message, options = {}) {
  return ElMessageBox.confirm(message, title, {
    confirmButtonText: options.confirmText || "Confirm",
    cancelButtonText: options.cancelText || "Cancel",
    type: options.type || "warning",
  });
}
