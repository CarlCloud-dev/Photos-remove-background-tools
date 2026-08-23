// ============== 全局状态 ==============
import { createIcons, icons } from 'lucide';

const baseUrl = (window.backendAPI && window.backendAPI.baseUrl)
  ? window.backendAPI.baseUrl
  : 'http://127.0.0.1:49173';
let i18n = {};
let fileRef = null;
let sourceFilePath = null;
let outputPath = null;
let logEventSource = null;
let downloadEventSource = null;
let downloadCancellationPending = false;
let cudaRuntimeEventSource = null;
let cudaRuntimeCancellationPending = false;
let activeModelId = 'u2net';
let comparePosition = 50;
let previewBackdropMode = 'theme';
let taskProgressTimer = null;
let taskProgressValue = 0;
let modelPostParams = {};
let batchProcessing = false;
let batchOverlayTimer = null;
let batchFiles = [];
let backendReady = false;
let backendBootInFlight = false;
let backendBootStartedAt = 0;

// ============== 默认参数 ==============
const DEFAULT_PARAMS = {
  output_mode: 'rgba',
};

// ============== i18n ==============
function t(key, vars) {
  const parts = key.split('.');
  let val = i18n;
  for (const p of parts) {
    if (val && typeof val === 'object' && p in val) {
      val = val[p];
    } else {
      return key;
    }
  }
  if (typeof val !== 'string') return key;
  if (vars) {
    for (const k in vars) {
      val = val.replace('{' + k + '}', vars[k]);
    }
  }
  return val;
}

function applyI18n() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    el.textContent = t(key);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    const key = el.getAttribute('data-i18n-placeholder');
    el.setAttribute('placeholder', t(key));
  });
  document.title = t('topbar.title');
}

// ============== Toast ==============
function toast(kind, msg) {
  const container = document.getElementById('toastContainer');
  if (!container) return;
  const el = document.createElement('div');
  el.className = 'toast ' + (kind || '');
  el.textContent = msg;
  container.appendChild(el);
  requestAnimationFrame(() => el.classList.add('show'));
  setTimeout(() => {
    el.classList.remove('show');
    setTimeout(() => el.remove(), 350);
  }, 3000);
}

// ============== Modal ==============
function openModal(id) {
  const m = document.getElementById(id);
  if (m) m.style.display = 'flex';
}
function closeModal(id) {
  const m = document.getElementById(id);
  if (m) m.style.display = 'none';
}

function setBackendAvailability(ready) {
  backendReady = Boolean(ready);
  const startBtn = document.getElementById('startBtn');
  const runtimeNote = document.getElementById('modelRuntimeNote');
  const modelTabs = document.querySelectorAll('.model-tab');
  const isTaskPending = document.body.dataset.removeBgPending === '1';

  if (startBtn && !isTaskPending) startBtn.disabled = !backendReady || !activeModelIsReady();
  if (runtimeNote) runtimeNote.disabled = !backendReady;
  modelTabs.forEach((tab) => { tab.disabled = !backendReady; });
}

function renderBackendBootState({ title, detail, visible = true, slow = false, retry = false }) {
  const overlay = document.getElementById('backendBootOverlay');
  const titleEl = document.getElementById('backendBootTitle');
  const detailEl = document.getElementById('backendBootDetail');
  const retryBtn = document.getElementById('backendBootRetryBtn');
  if (!overlay) return;

  overlay.classList.toggle('is-hidden', !visible);
  overlay.classList.toggle('is-slow', slow);
  overlay.setAttribute('aria-busy', String(visible && !retry));
  if (titleEl) titleEl.textContent = title;
  if (detailEl) detailEl.textContent = detail;
  if (retryBtn) retryBtn.hidden = !retry;
}

function waitFor(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function waitForBackendReady() {
  if (backendBootInFlight) return null;
  backendBootInFlight = true;
  backendBootStartedAt = Date.now();
  setBackendAvailability(false);

  while (backendBootInFlight) {
    const elapsedSeconds = Math.floor((Date.now() - backendBootStartedAt) / 1000);
    const isSlow = elapsedSeconds >= 20;
    renderBackendBootState({
      title: isSlow ? '本地推理环境仍在准备' : '正在启动本地推理服务',
      detail: isSlow
        ? `已等待 ${elapsedSeconds} 秒；首次安装或 CUDA 运行时加载会更久，请继续等待。`
        : '正在连接本地服务，服务就绪后将自动检查模型。',
      slow: isSlow,
      retry: isSlow
    });

    const status = await refreshDeviceStatus();
    if (status) {
      backendBootInFlight = false;
      setBackendAvailability(true);
      renderBackendBootState({ title: '', detail: '', visible: false });
      return status;
    }
    await waitFor(700);
  }
  return null;
}

// ============== 进度与任务舞台 ==============
function setProgress(p) {
  const pct = Math.max(0, Math.min(100, p));
  const stageText = document.getElementById('stageProgressText');
  const startBtn = document.getElementById('startBtn');
  if (stageText) stageText.textContent = pct > 0 ? ('正在处理图片 · ' + Math.round(pct) + '%') : '正在准备任务…';
  if (startBtn && startBtn.classList.contains('is-processing')) {
    startBtn.style.setProperty('--task-progress', pct.toFixed(1) + '%');
    setStartButtonLabel('正在抠图 · ' + Math.round(pct) + '%');
  }
}

function startTaskProgressAnimation() {
  clearInterval(taskProgressTimer);
  taskProgressValue = 5;
  setProgress(taskProgressValue);
  taskProgressTimer = window.setInterval(() => {
    taskProgressValue = Math.min(92, taskProgressValue + Math.max(1, (92 - taskProgressValue) * 0.11));
    setProgress(taskProgressValue);
  }, 560);
}

function stopTaskProgressAnimation() {
  if (taskProgressTimer) window.clearInterval(taskProgressTimer);
  taskProgressTimer = null;
}

function resetProgress() {
  stopTaskProgressAnimation();
  taskProgressValue = 0;
  const stageText = document.getElementById('stageProgressText');
  const startBtn = document.getElementById('startBtn');
  if (stageText) stageText.textContent = '正在准备任务…';
  if (startBtn) startBtn.style.removeProperty('--task-progress');
}

function setPreviewProcessing(isProcessing) {
  const stage = document.getElementById('dropZone');
  if (stage) stage.classList.toggle('is-processing', Boolean(isProcessing));
}

function setProcessingOverlay(title, detail) {
  const titleNode = document.getElementById('processingTitle');
  const detailNode = document.getElementById('stageProgressText');
  if (titleNode) titleNode.textContent = title;
  if (detailNode) detailNode.textContent = detail;
}

function clearBatchOverlayTimer() {
  if (batchOverlayTimer) window.clearTimeout(batchOverlayTimer);
  batchOverlayTimer = null;
}

function setBatchProgress(current, total, filename) {
  const stage = document.getElementById('dropZone');
  const progressBar = document.getElementById('batchProgressBar');
  const completed = Math.max(0, Math.min(total, current - 1));
  const percent = total ? (completed / total) * 100 : 0;
  if (stage) stage.style.setProperty('--batch-progress', percent.toFixed(2) + '%');
  if (progressBar) progressBar.style.width = percent.toFixed(2) + '%';
  setProcessingOverlay('正在批量抠图', '第 ' + current + ' / ' + total + ' 张 · ' + filename);
}

function prepareBatchPreview() {
  clearBatchOverlayTimer();
  fileRef = null;
  sourceFilePath = null;
  clearResultPreview();
  const stage = document.getElementById('dropZone');
  const viewport = document.getElementById('compareViewport');
  const leftImage = document.getElementById('leftPreviewImg');
  const leftEmpty = document.getElementById('leftEmpty');
  if (stage) {
    stage.classList.remove('has-source');
    stage.classList.add('is-batch-processing');
    stage.style.setProperty('--batch-progress', '0%');
  }
  if (viewport) viewport.classList.remove('has-result', 'is-animated');
  if (leftImage) {
    leftImage.removeAttribute('src');
    leftImage.style.display = 'none';
  }
  if (leftEmpty) leftEmpty.style.display = 'none';
  setPreviewProcessing(true);
}

function finishBatchPreview(successCount, totalCount, outputDirectory) {
  const stage = document.getElementById('dropZone');
  const progressBar = document.getElementById('batchProgressBar');
  if (stage) stage.style.setProperty('--batch-progress', '100%');
  if (progressBar) progressBar.style.width = '100%';
  setProcessingOverlay('批量抠图完成', '已完成 ' + successCount + ' / ' + totalCount + ' 张' + (outputDirectory ? ' · 已保存至抠图结果文件夹' : ''));
  clearBatchOverlayTimer();
  batchOverlayTimer = window.setTimeout(() => {
    if (batchProcessing) return;
    if (stage) stage.classList.remove('is-batch-processing');
    setPreviewProcessing(false);
    setProcessingOverlay('正在分离主体与背景', '正在准备任务…');
    batchOverlayTimer = null;
  }, 1500);
}

function updatePreviewBackdrop() {
  const stage = document.getElementById('dropZone');
  const resolved = previewBackdropMode === 'theme'
    ? (document.documentElement.dataset.theme === 'light' ? 'light' : 'dark')
    : previewBackdropMode;
  if (stage) stage.dataset.previewBackdrop = resolved;
  document.querySelectorAll('#previewBackdropSwitcher [data-preview-backdrop]').forEach((button) => {
    const active = button.dataset.previewBackdrop === resolved;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-pressed', String(active));
  });
}

function initPreviewBackdropSwitcher() {
  document.querySelectorAll('#previewBackdropSwitcher [data-preview-backdrop]').forEach((button) => {
    button.addEventListener('click', () => {
      previewBackdropMode = button.dataset.previewBackdrop;
      updatePreviewBackdrop();
    });
  });
  updatePreviewBackdrop();
}

function updateRangeFill(range) {
  if (!range) return;
  const min = Number(range.min || 0);
  const max = Number(range.max || 100);
  const value = Number(range.value);
  const percent = max === min ? 0 : ((value - min) / (max - min)) * 100;
  range.style.setProperty('--slider-fill', Math.max(0, Math.min(100, percent)).toFixed(2) + '%');
}

function setStartButtonLabel(label) {
  const startBtn = document.getElementById('startBtn');
  const text = startBtn && startBtn.querySelector('span');
  if (text) text.textContent = label;
}

function getStartActionLabel() {
  if (!activeModelIsReady()) return '模型待接入';
  return batchFiles.length ? '开始批量抠图' : '开始抠图';
}

function syncBatchSelectionUi() {
  const batchBtn = document.getElementById('batchBtn');
  const batchBtnLabel = document.getElementById('batchBtnLabel');
  const startBtn = document.getElementById('startBtn');
  const selectedCount = batchFiles.length;
  const hasSelection = selectedCount > 0;

  if (batchBtn) {
    batchBtn.classList.toggle('is-batch-selected', hasSelection);
    batchBtn.title = hasSelection ? ('已选择 ' + selectedCount + ' 张图片，点击取消文件') : '选择多张图片进行批量抠图';
    batchBtn.setAttribute('aria-label', batchBtn.title);
  }
  if (batchBtnLabel) batchBtnLabel.textContent = hasSelection ? '取消文件' : '批量抠图';
  if (!batchProcessing && document.body.dataset.removeBgPending !== '1') {
    if (startBtn) startBtn.disabled = !backendReady || !activeModelIsReady();
    setStartButtonLabel(getStartActionLabel());
  }
}

function clearBatchSelection(notify = false) {
  const hadSelection = batchFiles.length > 0;
  batchFiles = [];
  const batchFileInput = document.getElementById('batchFileInput');
  if (batchFileInput) batchFileInput.value = '';
  syncBatchSelectionUi();
  if (notify && hadSelection) toast('info', '已取消待处理的批量文件。');
}

function queueBatchFiles(files) {
  const selectedFiles = Array.isArray(files) ? files : [];
  const validFiles = selectedFiles.filter((file) => file && file.type.startsWith('image/') && file.size <= 50 * 1024 * 1024);
  if (!validFiles.length) {
    toast('warn', '请选择至少一张不超过 50 MB 的图片。');
    return;
  }
  if (validFiles.some((file) => !getSourceFilePath(file))) {
    toast('warn', '无法读取源图片路径，请重启桌面应用后再使用批量抠图。');
    return;
  }
  batchFiles = validFiles;
  syncBatchSelectionUi();
  toast('success', '已选择 ' + validFiles.length + ' 张图片，点击“开始批量抠图”后执行。');
  if (validFiles.length !== selectedFiles.length) toast('warn', '已跳过不支持或超过 50 MB 的文件。');
}

// ============== 日志 ==============
function appendLog(level, msg) {
  const box = document.getElementById('logsContent');
  if (!box) return;
  const line = document.createElement('div');
  line.className = 'log-line log-' + (level || 'info');
  const time = new Date().toLocaleTimeString();
  line.textContent = '[' + time + '] ' + msg;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

function clearLogs() {
  const box = document.getElementById('logsContent');
  if (box) box.innerHTML = '';
}

// ============== 图片选择 & 预览 ==============
function setComparePosition(position, animate = false) {
  const viewport = document.getElementById('compareViewport');
  const beforeImg = document.getElementById('leftPreviewImg');
  const afterLayer = document.getElementById('afterLayer');
  const divider = document.getElementById('compareDivider');
  const handle = document.getElementById('compareHandle');
  if (!viewport || !beforeImg || !afterLayer || !divider) return;

  comparePosition = Math.max(0, Math.min(100, Number(position) || 0));
  viewport.classList.toggle('is-animated', Boolean(animate));
  const rightInset = (100 - comparePosition).toFixed(2) + '%';
  // 有结果时，原图只保留在左侧，透明 PNG 只保留在右侧。
  // 这样透明像素会露出棋盘底，而不会再次透出原图。
  beforeImg.style.clipPath = viewport.classList.contains('has-result')
    ? 'inset(0 ' + rightInset + ' 0 0)'
    : 'none';
  afterLayer.style.clipPath = 'inset(0 0 0 ' + comparePosition.toFixed(2) + '%)';
  divider.style.left = comparePosition.toFixed(2) + '%';
  if (handle) {
    handle.setAttribute('aria-valuenow', String(Math.round(comparePosition)));
    handle.setAttribute('aria-valuetext', '原图 ' + Math.round(comparePosition) + '%，抠图结果 ' + Math.round(100 - comparePosition) + '%');
  }
}

function clearResultPreview() {
  outputPath = null;
  const viewport = document.getElementById('compareViewport');
  const rightImg = document.getElementById('rightPreviewImg');
  const saveBtn = document.getElementById('saveBtn');
  if (viewport) viewport.classList.remove('has-result', 'is-animated');
  if (rightImg) {
    rightImg.onload = null;
    rightImg.removeAttribute('src');
    rightImg.style.display = 'none';
  }
  if (saveBtn) saveBtn.disabled = true;
  setComparePosition(50, false);
}

function revealResultPreview(previewUrl) {
  const viewport = document.getElementById('compareViewport');
  const rightImg = document.getElementById('rightPreviewImg');
  const saveBtn = document.getElementById('saveBtn');
  if (!viewport || !rightImg) return;

  let hasRevealed = false;
  const reveal = () => {
    if (hasRevealed) return;
    hasRevealed = true;
    rightImg.style.display = 'block';
    viewport.classList.add('has-result');
    // 从最右侧开始揭示结果图，动画结束后停在中间对比位置。
    setComparePosition(100, false);
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        setComparePosition(50, true);
        window.setTimeout(() => viewport.classList.remove('is-animated'), 900);
      });
    });
    if (saveBtn) saveBtn.disabled = false;
  };

  rightImg.onload = reveal;
  rightImg.onerror = () => toast('danger', '结果图片无法加载，请检查输出文件。');
  rightImg.src = previewUrl + '&v=' + Date.now();
  if (rightImg.complete && rightImg.naturalWidth) reveal();
}

function handleFile(file) {
  if (!file) return;
  if (batchProcessing) {
    toast('warn', '批量任务进行中，请等待完成。');
    return;
  }
  if (!file.type.startsWith('image/')) {
    toast('warn', t('errors.1004'));
    return;
  }
  if (file.size > 50 * 1024 * 1024) {
    toast('warn', t('errors.1005'));
    return;
  }
  clearBatchSelection();
  fileRef = file;
  // Electron 28 的 File 对象携带真实文件路径；网页环境则不启用同目录自动保存。
  sourceFilePath = getSourceFilePath(file);
  clearResultPreview();
  const stage = document.getElementById('dropZone');
  const viewport = document.getElementById('compareViewport');
  if (stage) stage.classList.add('has-source');
  if (viewport) viewport.classList.remove('has-result', 'is-animated');
  const reader = new FileReader();
  reader.onload = (e) => {
    const url = e.target.result;
    const img = new Image();
    img.onload = () => {
      const leftImg = document.getElementById('leftPreviewImg');
      const leftEmpty = document.getElementById('leftEmpty');
      const leftInfo = document.getElementById('leftInfo');
      if (leftImg) {
        leftImg.src = url;
        leftImg.style.display = 'block';
      }
      if (leftEmpty) leftEmpty.style.display = 'none';
      if (leftInfo) {
        leftInfo.textContent = t('left.imageInfo', { w: img.naturalWidth, h: img.naturalHeight });
      }
    };
    img.onerror = () => {
      toast('danger', t('errors.1001'));
    };
    img.src = url;
  };
  reader.onerror = () => toast('danger', t('errors.1001'));
  reader.readAsDataURL(file);
}

function initImageHandlers() {
  const fileInput = document.getElementById('fileInput');
  const batchFileInput = document.getElementById('batchFileInput');
  const selectBtn = document.getElementById('selectBtn');
  const batchBtn = document.getElementById('batchBtn');
  const dropZone = document.getElementById('dropZone');

  if (selectBtn && fileInput) {
    selectBtn.addEventListener('click', () => fileInput.click());
  }
  if (fileInput) {
    fileInput.addEventListener('change', (e) => {
      const f = e.target.files && e.target.files[0];
      handleFile(f);
      e.target.value = '';
    });
  }
  if (batchBtn && batchFileInput) {
    batchBtn.addEventListener('click', () => {
      if (batchProcessing) return;
      if (batchFiles.length) {
        clearBatchSelection(true);
        return;
      }
      batchFileInput.click();
    });
  }
  if (batchFileInput) {
    batchFileInput.addEventListener('change', (event) => {
      const files = Array.from(event.target.files || []);
      event.target.value = '';
      queueBatchFiles(files);
    });
  }
  if (dropZone) {
    dropZone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dropZone.classList.add('dragover');
    });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
    dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropZone.classList.remove('dragover');
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      handleFile(f);
    });
  }
}

// ============== 参数面板 ==============
const DEFAULT_ALPHA_MATTING_PARAMS = Object.freeze({
  alpha_matting_enabled: false,
  alpha_matting_foreground_threshold: 240,
  alpha_matting_background_threshold: 10,
  alpha_matting_erode_size: 10,
});

function getDefaultModelPostParams(meta) {
  return Object.assign({}, meta.postprocess, DEFAULT_ALPHA_MATTING_PARAMS);
}

function loadParams() {
  const saved = readSavedParams();
  const stored = saved.model_post_params || {};
  activeModelId = MODEL_META[saved.selected_model] ? saved.selected_model : 'u2net';
  for (const [modelId, meta] of Object.entries(MODEL_META)) {
    const value = stored[modelId] || {};
    const defaults = getDefaultModelPostParams(meta);
    modelPostParams[modelId] = {
      threshold: Number.isFinite(Number(value.threshold)) ? Math.max(0, Math.min(1, Number(value.threshold))) : defaults.threshold,
      feather: Number.isFinite(Number(value.feather)) ? Math.max(0, Math.min(15, Number(value.feather))) : defaults.feather,
      edge_refine: Number.isFinite(Number(value.edge_refine)) ? Math.max(0, Math.min(4, Number(value.edge_refine))) : defaults.edge_refine,
      alpha_matting_enabled: value.alpha_matting_enabled === true ? true : defaults.alpha_matting_enabled,
      alpha_matting_foreground_threshold: Number.isFinite(Number(value.alpha_matting_foreground_threshold)) ? Math.max(1, Math.min(255, Number(value.alpha_matting_foreground_threshold))) : defaults.alpha_matting_foreground_threshold,
      alpha_matting_background_threshold: Number.isFinite(Number(value.alpha_matting_background_threshold)) ? Math.max(0, Math.min(254, Number(value.alpha_matting_background_threshold))) : defaults.alpha_matting_background_threshold,
      alpha_matting_erode_size: Number.isFinite(Number(value.alpha_matting_erode_size)) ? Math.max(0, Math.min(30, Number(value.alpha_matting_erode_size))) : defaults.alpha_matting_erode_size,
    };
    if (Object.prototype.hasOwnProperty.call(defaults, 'ben2_refine_foreground')) {
      modelPostParams[modelId].ben2_refine_foreground = value.ben2_refine_foreground === true ? true : defaults.ben2_refine_foreground;
    }
    if (Object.prototype.hasOwnProperty.call(defaults, 'inspyrenet_dynamic_resize')) {
      modelPostParams[modelId].inspyrenet_dynamic_resize = value.inspyrenet_dynamic_resize !== false;
    }
  }
}

function readSavedParams() {
  try {
    const raw = localStorage.getItem('rmbg_params');
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (_) {
    return {};
  }
}

function saveParams(params) {
  try { localStorage.setItem('rmbg_params', JSON.stringify(params)); } catch (_) {}
}

function saveWorkspaceParams() {
  saveParams({ selected_model: activeModelId, model_post_params: modelPostParams });
}

function collectParams() {
  const thresholdRange = document.getElementById('thresholdRange');
  const featherRange = document.getElementById('featherRange');
  const edgeRange = document.getElementById('edgeRange');
  const alphaMattingEnabled = document.getElementById('alphaMattingEnabled');
  const alphaMattingForeground = document.getElementById('alphaMattingForegroundRange');
  const alphaMattingBackground = document.getElementById('alphaMattingBackgroundRange');
  const alphaMattingErode = document.getElementById('alphaMattingErodeRange');
  const ben2RefineForeground = document.getElementById('ben2RefineForeground');
  const inspyrenetDynamicResize = document.getElementById('inspyrenetDynamicResize');
  const modelMeta = MODEL_META[activeModelId];
  const current = modelPostParams[activeModelId] || modelMeta.postprocess;
  const threshold = thresholdRange ? Number(thresholdRange.value) : current.threshold;
  const feather = featherRange ? Number(featherRange.value) : current.feather;
  const edgeRefine = edgeRange ? Number(edgeRange.value) : current.edge_refine;
  const alphaMattingEnabledValue = alphaMattingEnabled ? Boolean(alphaMattingEnabled.checked) : Boolean(current.alpha_matting_enabled);
  const alphaMattingForegroundValue = alphaMattingForeground ? Number(alphaMattingForeground.value) : current.alpha_matting_foreground_threshold;
  const alphaMattingBackgroundValue = alphaMattingBackground ? Number(alphaMattingBackground.value) : current.alpha_matting_background_threshold;
  const alphaMattingErodeValue = alphaMattingErode ? Number(alphaMattingErode.value) : current.alpha_matting_erode_size;
  const ben2RefineForegroundValue = ben2RefineForeground ? Boolean(ben2RefineForeground.checked) : Boolean(current.ben2_refine_foreground);
  const inspyrenetDynamicResizeValue = inspyrenetDynamicResize ? Boolean(inspyrenetDynamicResize.checked) : current.inspyrenet_dynamic_resize !== false;
  modelPostParams[activeModelId] = Object.assign({}, current, {
    threshold,
    feather,
    edge_refine: edgeRefine,
    alpha_matting_enabled: alphaMattingEnabledValue,
    alpha_matting_foreground_threshold: alphaMattingForegroundValue,
    alpha_matting_background_threshold: alphaMattingBackgroundValue,
    alpha_matting_erode_size: alphaMattingErodeValue,
    ben2_refine_foreground: ben2RefineForegroundValue,
    inspyrenet_dynamic_resize: inspyrenetDynamicResizeValue,
  });

  const params = {
    threshold,
    feather,
    edge_refine: edgeRefine,
    alpha_matting_enabled: alphaMattingEnabledValue,
    alpha_matting_foreground_threshold: alphaMattingForegroundValue,
    alpha_matting_background_threshold: alphaMattingBackgroundValue,
    alpha_matting_erode_size: alphaMattingErodeValue,
    ben2_refine_foreground: ben2RefineForegroundValue,
    inspyrenet_dynamic_resize: inspyrenetDynamicResizeValue,
    // 工作台固定输出透明 PNG，避免界面提供与实际流程不一致的格式选择。
    output_mode: 'rgba',
    model_id: activeModelId,
    // 前端不再暴露尺寸选择；每个模型始终以其最高质量规格推理。
    input_size: modelMeta ? modelMeta.inputSize : undefined,
  };
  saveWorkspaceParams();
  return params;
}

// ============== 模型选择、对比滑杆与窗口控制 ==============
const MODEL_META = {
  u2net: {
    label: 'U²-Net',
    fileTag: 'U2Net',
    runtime: '经典显著主体分割',
    inputSize: 320,
    postprocess: { threshold: 0.5, feather: 1, edge_refine: 1 },
  },
  rmbg20: {
    label: 'RMBG-2.0',
    fileTag: 'RMBG20',
    runtime: 'BRIA 高精度主体分离',
    inputSize: 1024,
    postprocess: { threshold: 0.5, feather: 1, edge_refine: 1 },
  },
  birefnet: {
    label: 'BiRefNet',
    fileTag: 'BiRefNet',
    runtime: '高分辨率通用主体分离',
    inputSize: 1024,
    postprocess: { threshold: 0.5, feather: 1, edge_refine: 1 },
  },
  ben2: {
    label: 'BEN2',
    fileTag: 'BEN2',
    runtime: '置信引导边缘精修',
    inputSize: 1024,
    postprocess: { threshold: 0.5, feather: 1, edge_refine: 1, ben2_refine_foreground: false },
    supportsAlphaMatting: false,
  },
  inspyrenet: {
    label: 'InSPyReNet',
    fileTag: 'InSPyReNet',
    runtime: '高分辨率显著目标分离',
    inputSize: 1024,
    postprocess: { threshold: 0.5, feather: 1, edge_refine: 1, inspyrenet_dynamic_resize: true },
  },
};

function getModelFileTag(modelId = activeModelId) {
  return (MODEL_META[modelId] && MODEL_META[modelId].fileTag) || 'RemoveBG';
}

function renderModelParameterPanel(meta) {
  const title = document.getElementById('modelParamsTitle');
  const body = document.getElementById('modelParamsBody');
  if (!body) return;
  if (title) title.textContent = '边缘优化';
  body.replaceChildren();

  const values = modelPostParams[activeModelId] || meta.postprocess;
  const controls = [
    { id: 'thresholdRange', numberId: 'thresholdValue', label: '前景阈值', hint: '提高可收紧主体范围', value: values.threshold, min: 0, max: 1, step: 0.01, key: 'threshold', decimals: 2 },
    { id: 'featherRange', numberId: 'featherValue', label: '边缘羽化', hint: '柔化透明边缘', value: values.feather, min: 0, max: 15, step: 1, key: 'feather', decimals: 0 },
    { id: 'edgeRange', numberId: 'edgeValue', label: '边缘细化', hint: '强化主体轮廓', value: values.edge_refine, min: 0, max: 4, step: 1, key: 'edge_refine', decimals: 0 },
  ];
  controls.forEach((item) => {
    const control = document.createElement('div');
    control.className = 'parameter-control';
    control.innerHTML = '<div class="parameter-head"><label class="parameter-label" for="' + item.id + '"><span>' + item.label + '</span><small>' + item.hint + '</small></label><input id="' + item.numberId + '" class="param-number" type="number" min="' + item.min + '" max="' + item.max + '" step="' + item.step + '" inputmode="decimal" /></div><input type="range" id="' + item.id + '" class="param-slider" min="' + item.min + '" max="' + item.max + '" step="' + item.step + '" />';
    body.append(control);
    const range = control.querySelector('#' + item.id);
    const number = control.querySelector('#' + item.numberId);
    if (!range || !number) return;
    range.value = String(item.value);
    const update = (nextValue) => {
      modelPostParams[activeModelId] = Object.assign({}, modelPostParams[activeModelId] || meta.postprocess, { [item.key]: nextValue });
      range.value = String(nextValue);
      number.value = item.decimals ? nextValue.toFixed(item.decimals) : String(nextValue);
      updateRangeFill(range);
    };
    update(Number(range.value));
    range.addEventListener('input', () => update(Number(range.value)));
    range.addEventListener('change', collectParams);
    number.addEventListener('change', () => {
      const raw = Number(number.value);
      const nextValue = Number.isFinite(raw) ? Math.max(item.min, Math.min(item.max, raw)) : item.value;
      update(nextValue);
      collectParams();
    });
  });

  const appendModelToggle = (id, key, titleText, hint, enabled) => {
    const option = document.createElement('div');
    option.className = 'alpha-matting-control model-feature-control';
    option.innerHTML = '<div class="alpha-matting-head"><div class="alpha-matting-title"><strong>' + titleText + '</strong><small>' + hint + '</small></div><label class="alpha-matting-toggle" for="' + id + '" aria-label="' + titleText + '"><input id="' + id + '" type="checkbox" /><span class="alpha-matting-switch" aria-hidden="true"></span></label></div>';
    body.append(option);
    const input = option.querySelector('#' + id);
    if (!input) return;
    input.checked = Boolean(enabled);
    input.addEventListener('change', () => {
      modelPostParams[activeModelId] = Object.assign({}, modelPostParams[activeModelId] || meta.postprocess, { [key]: input.checked });
      collectParams();
    });
  };

  if (activeModelId === 'ben2') {
    appendModelToggle(
      'ben2RefineForeground',
      'ben2_refine_foreground',
      'BEN2 前景精修',
      '官方前景重建，改善发丝与半透明边缘；会增加处理时间',
      values.ben2_refine_foreground
    );
  }
  if (activeModelId === 'inspyrenet') {
    appendModelToggle(
      'inspyrenetDynamicResize',
      'inspyrenet_dynamic_resize',
      '动态细节推理',
      '按原图比例推理以保留细节；超大图会更耗时并占用更多显存',
      values.inspyrenet_dynamic_resize !== false
    );
  }

  if (meta.supportsAlphaMatting === false) return;

  const alphaControl = document.createElement('div');
  alphaControl.className = 'alpha-matting-control is-ready';
  alphaControl.innerHTML = '<div class="alpha-matting-head"><div class="alpha-matting-title"><strong>Alpha Matting 精修</strong><small>适用于发丝、薄纱等半透明边缘</small></div><label class="alpha-matting-toggle" for="alphaMattingEnabled" aria-label="启用 Alpha Matting 精修"><input id="alphaMattingEnabled" type="checkbox" /><span class="alpha-matting-switch" aria-hidden="true"></span></label></div><div class="alpha-matting-settings"><div class="alpha-matting-setting"><div class="parameter-head"><label class="parameter-label" for="alphaMattingForegroundRange"><span>确定前景</span><small>高于此值保留主体</small></label><input id="alphaMattingForegroundValue" class="param-number" type="number" min="1" max="255" step="1" /></div><input id="alphaMattingForegroundRange" type="range" class="param-slider" min="1" max="255" step="1" /></div><div class="alpha-matting-setting"><div class="parameter-head"><label class="parameter-label" for="alphaMattingBackgroundRange"><span>确定背景</span><small>低于此值视为背景</small></label><input id="alphaMattingBackgroundValue" class="param-number" type="number" min="0" max="254" step="1" /></div><input id="alphaMattingBackgroundRange" type="range" class="param-slider" min="0" max="254" step="1" /></div><div class="alpha-matting-setting"><div class="parameter-head"><label class="parameter-label" for="alphaMattingErodeRange"><span>边缘收缩</span><small>收紧不确定的边缘</small></label><input id="alphaMattingErodeValue" class="param-number" type="number" min="0" max="30" step="1" /></div><input id="alphaMattingErodeRange" type="range" class="param-slider" min="0" max="30" step="1" /></div></div>';
  body.append(alphaControl);

  const alphaEnabled = alphaControl.querySelector('#alphaMattingEnabled');
  const alphaSettings = alphaControl.querySelector('.alpha-matting-settings');
  const setAlphaSettingsState = () => {
    const enabled = Boolean(alphaEnabled && alphaEnabled.checked);
    alphaControl.classList.toggle('is-enabled', enabled);
    if (alphaSettings) alphaSettings.querySelectorAll('input').forEach((input) => { input.disabled = !enabled; });
  };
  if (alphaEnabled) {
    alphaEnabled.checked = Boolean(values.alpha_matting_enabled);
    alphaEnabled.addEventListener('change', () => {
      modelPostParams[activeModelId] = Object.assign({}, modelPostParams[activeModelId] || meta.postprocess, { alpha_matting_enabled: alphaEnabled.checked });
      setAlphaSettingsState();
      collectParams();
    });
  }
  const alphaRanges = [
    { id: 'alphaMattingForegroundRange', numberId: 'alphaMattingForegroundValue', key: 'alpha_matting_foreground_threshold', value: values.alpha_matting_foreground_threshold },
    { id: 'alphaMattingBackgroundRange', numberId: 'alphaMattingBackgroundValue', key: 'alpha_matting_background_threshold', value: values.alpha_matting_background_threshold },
    { id: 'alphaMattingErodeRange', numberId: 'alphaMattingErodeValue', key: 'alpha_matting_erode_size', value: values.alpha_matting_erode_size },
  ];
  alphaRanges.forEach((item) => {
    const range = alphaControl.querySelector('#' + item.id);
    const number = alphaControl.querySelector('#' + item.numberId);
    if (!range || !number) return;
    range.value = String(item.value);
    const update = (nextValue) => {
      modelPostParams[activeModelId] = Object.assign({}, modelPostParams[activeModelId] || meta.postprocess, { [item.key]: nextValue });
      range.value = String(nextValue);
      number.value = String(nextValue);
      updateRangeFill(range);
    };
    update(Number(range.value));
    range.addEventListener('input', () => update(Number(range.value)));
    range.addEventListener('change', collectParams);
    number.addEventListener('change', () => {
      const raw = Number(number.value);
      const nextValue = Number.isFinite(raw) ? Math.max(Number(range.min), Math.min(Number(range.max), raw)) : item.value;
      update(nextValue);
      collectParams();
    });
  });
  setAlphaSettingsState();
}

function activeModelIsReady() {
  return Boolean(MODEL_META[activeModelId]);
}

function updateModelSelection(tab, notifyUnavailable = false) {
  if (!tab) return;
  const id = tab.dataset.model;
  const meta = MODEL_META[id];
  if (!meta) return;

  activeModelId = id;
  renderModelParameterPanel(meta);
  const tabs = Array.from(document.querySelectorAll('.model-tab'));
  tabs.forEach((item, index) => {
    const active = item === tab;
    item.classList.toggle('is-active', active);
    item.setAttribute('aria-selected', String(active));
    if (active) {
      const indicator = document.getElementById('modelTabIndicator');
      if (indicator) indicator.style.transform = 'translateX(' + (index * 100) + '%)';
    }
  });
  saveWorkspaceParams();

  const note = document.getElementById('modelRuntimeNote');
  const startBtn = document.getElementById('startBtn');
  if (note) {
    note.classList.remove('is-ready');
    note.title = meta.label + ' 正在检测本地模型';
    const text = note.querySelector('.runtime-inline-text');
    if (text) text.textContent = meta.label + ' · 检测中';
  }
  if (startBtn && document.body.dataset.removeBgPending !== '1') startBtn.disabled = !backendReady;
  setStartButtonLabel(getStartActionLabel());
  refreshDeviceStatus().then((status) => renderModelRuntimeStatus(status)).catch(() => {});
  if (notifyUnavailable) toast('success', '已切换至 ' + meta.label + '。');
}

function renderModelRuntimeStatus(status) {
  const note = document.getElementById('modelRuntimeNote');
  const meta = MODEL_META[activeModelId];
  if (!note || !meta || !status || status.model_id !== activeModelId) return;
  const ready = Boolean(status.model_cached);
  note.classList.toggle('is-ready', ready);
  note.title = ready ? meta.label + ' 已在本地就绪' : meta.label + ' 需要下载模型';
  const text = note.querySelector('.runtime-inline-text');
  if (text) text.textContent = ready ? meta.label + ' · 本地' : meta.label + ' · 需下载';
}

function initModelSwitcher() {
  const tabs = Array.from(document.querySelectorAll('.model-tab'));
  tabs.forEach(tab => tab.addEventListener('click', () => updateModelSelection(tab, true)));
  updateModelSelection(tabs.find((tab) => tab.dataset.model === activeModelId) || tabs[0]);
}

function initParameterActions() {
  const resetButton = document.getElementById('resetParamsBtn');
  if (!resetButton) return;
  resetButton.addEventListener('click', () => {
    const meta = MODEL_META[activeModelId];
    if (!meta) return;
    modelPostParams[activeModelId] = getDefaultModelPostParams(meta);
    saveWorkspaceParams();
    renderModelParameterPanel(meta);
    toast('success', meta.label + ' 已恢复默认参数。');
  });
}

function initComparison() {
  const viewport = document.getElementById('compareViewport');
  const divider = document.getElementById('compareDivider');
  const handle = document.getElementById('compareHandle');
  if (!viewport || !divider || !handle) return;

  let activePointerId = null;

  const updateFromPointer = (clientX) => {
    const rect = viewport.getBoundingClientRect();
    if (!rect.width) return;
    setComparePosition(((clientX - rect.left) / rect.width) * 100, false);
  };

  const startDrag = (event) => {
    if (!viewport.classList.contains('has-result')) return;
    event.preventDefault();
    event.stopPropagation();
    activePointerId = event.pointerId;
    divider.setPointerCapture?.(event.pointerId);
    viewport.classList.remove('is-animated');
    updateFromPointer(event.clientX);
  };

  const drag = (event) => {
    if (event.pointerId === activePointerId) updateFromPointer(event.clientX);
  };

  const finishDrag = (event) => {
    if (event.pointerId !== activePointerId) return;
    if (divider.hasPointerCapture?.(event.pointerId)) divider.releasePointerCapture(event.pointerId);
    activePointerId = null;
  };

  divider.addEventListener('pointerdown', startDrag);
  divider.addEventListener('pointermove', drag);
  divider.addEventListener('pointerup', finishDrag);
  divider.addEventListener('pointercancel', finishDrag);
  window.addEventListener('pointermove', drag);
  window.addEventListener('pointerup', finishDrag);
  window.addEventListener('pointercancel', finishDrag);
  handle.addEventListener('keydown', (event) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    setComparePosition(comparePosition + (event.key === 'ArrowLeft' ? -4 : 4), false);
  });
}

function setMaximizedClass(isMaximized) {
  document.body.classList.toggle('is-maximized', Boolean(isMaximized));
}

function applyTheme(theme, persist = true) {
  const nextTheme = theme === 'light' ? 'light' : 'dark';
  document.documentElement.dataset.theme = nextTheme;
  document.querySelectorAll('[data-theme]').forEach((button) => {
    const active = button.dataset.theme === nextTheme;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  if (persist) {
    try { localStorage.setItem('rmbg-theme', nextTheme); } catch (_) {}
  }
  if (previewBackdropMode === 'theme') updatePreviewBackdrop();
}

function initThemeSwitcher() {
  const savedTheme = document.documentElement.dataset.theme;
  applyTheme(savedTheme === 'light' ? 'light' : 'dark', false);
  document.querySelectorAll('#themeSwitcher [data-theme]').forEach((button) => {
    button.addEventListener('click', () => applyTheme(button.dataset.theme));
  });
}

function initWindowControls() {
  const controls = window.windowControls;
  if (!controls) return;
  controls.isMaximized().then(setMaximizedClass).catch(() => {});
  document.querySelectorAll('[data-window-action]').forEach((button) => {
    button.addEventListener('click', async () => {
      const action = button.dataset.windowAction;
      if (action === 'minimize') await controls.minimize();
      if (action === 'maximize') setMaximizedClass(await controls.toggleMaximize());
      if (action === 'close') await controls.close();
    });
  });
  const titlebar = document.querySelector('.app-titlebar');
  if (titlebar) {
    titlebar.addEventListener('dblclick', async (event) => {
      if (event.target.closest('.no-drag')) return;
      setMaximizedClass(await controls.toggleMaximize());
    });
  }
}

// ============== 开始抠图 ==============
function closeEventSource(es) {
  try { if (es) es.close(); } catch (_) {}
}

let lastModelDownloadInfo = null;

function getModelDownloadSources(info) {
  const sources = info && Array.isArray(info.download_sources) ? info.download_sources : [];
  const domestic = sources.find((source) => source && source.id === 'domestic') || {
    id: 'domestic',
    title: '国内下载',
    name: (info && info.source_name) || 'ModelScope 国内镜像',
    url: (info && info.source_url) || 'https://modelscope.cn/models/briaai/RMBG-2.0/files',
  };
  const global = sources.find((source) => source && source.id === 'global') || {
    id: 'global',
    title: '国外官方',
    name: '暂未提供国外官方来源',
    url: '',
  };
  return { domestic, global };
}

function updateModelDownloadDialog(info) {
  lastModelDownloadInfo = info || null;
  const title = document.getElementById('downloadModalTitle');
  const domesticSource = document.getElementById('modelDownloadDomesticSource');
  const globalSource = document.getElementById('modelDownloadGlobalSource');
  const path = document.getElementById('modelDownloadPath');
  const fileList = document.querySelector('.model-file-list');
  const sources = getModelDownloadSources(info);
  if (title) title.textContent = '需要下载 ' + ((info && info.model_label) || (MODEL_META[activeModelId] && MODEL_META[activeModelId].label) || '模型') + ' 模型';
  if (domesticSource) domesticSource.textContent = sources.domestic.name || 'ModelScope 国内镜像';
  if (globalSource) globalSource.textContent = sources.global.name || '暂未提供国外官方来源';
  if (path) path.textContent = (info && info.target_dir) || '请先确认模型缓存目录';
  if (fileList && info && Array.isArray(info.required_files) && info.required_files.length) {
    fileList.replaceChildren(...info.required_files.map((name) => {
      const item = document.createElement('span');
      item.textContent = name === 'model.safetensors' ? name + '（大文件，请预留空间）' : name;
      return item;
    }));
  }
}

async function openActiveModelDownloadDialog() {
  if (!backendReady) {
    void waitForBackendReady();
    return false;
  }
  const status = await refreshDeviceStatus();
  if (!status) {
    setBackendAvailability(false);
    void waitForBackendReady();
    return false;
  }
  if (status.model_cached) {
    toast('success', '当前模型文件已就绪。');
    return true;
  }
  updateModelDownloadDialog(status.model_download || { target_dir: status.model_cache_dir });
  openModal('downloadModal');
  return false;
}

function openModelDownloadSource(sourceId) {
  const source = getModelDownloadSources(lastModelDownloadInfo)[sourceId];
  const url = source && typeof source.url === 'string' ? source.url.trim() : '';
  if (!url) {
    toast('warn', '当前模型暂未提供此下载来源。');
    return;
  }
  if (window.backendAPI && typeof window.backendAPI.openExternal === 'function') {
    window.backendAPI.openExternal(url).catch(() => toast('danger', '无法打开下载网页，请复制链接后在浏览器中访问。'));
  } else {
    window.open(url, '_blank', 'noopener');
  }
}

function setAutoDownloadButton(label, downloading = false) {
  const button = document.getElementById('downloadAutoBtn');
  const text = document.getElementById('downloadAutoLabel');
  if (text) text.textContent = label;
  if (button) {
    button.disabled = downloading;
    button.classList.toggle('is-downloading', downloading);
  }
  const cancelButton = document.getElementById('downloadCancelBtn');
  if (cancelButton) {
    cancelButton.disabled = downloadCancellationPending;
    cancelButton.textContent = downloading
      ? (downloadCancellationPending ? '正在取消…' : '取消下载')
      : '暂不下载';
  }
  const checkButton = document.getElementById('checkModelBtn');
  if (checkButton) checkButton.disabled = downloading;
}

function finishModelDownload(message, level = 'danger') {
  closeEventSource(downloadEventSource);
  downloadEventSource = null;
  downloadCancellationPending = false;
  setAutoDownloadButton('自动下载');
  const startBtn = document.getElementById('startBtn');
  if (startBtn) {
    startBtn.disabled = !backendReady || !activeModelIsReady();
    setStartButtonLabel(getStartActionLabel());
  }
  if (message) toast(level, message);
}

async function cancelModelDownload() {
  if (!downloadEventSource) {
    closeModal('downloadModal');
    return;
  }
  if (downloadCancellationPending) return;

  downloadCancellationPending = true;
  setAutoDownloadButton('正在停止下载…', true);
  try {
    const response = await fetch(baseUrl + '/api/download/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model_id: activeModelId }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.code !== 0) {
      throw new Error(payload.message || 'cancel request failed');
    }

    closeEventSource(downloadEventSource);
    downloadEventSource = null;
    downloadCancellationPending = false;
    setAutoDownloadButton('自动下载');
    const startBtn = document.getElementById('startBtn');
    if (startBtn) {
      startBtn.disabled = !backendReady || !activeModelIsReady();
      setStartButtonLabel(getStartActionLabel());
    }
    closeModal('downloadModal');
    toast('info', payload.message || '下载已取消，未完成文件已清理。');
  } catch (error) {
    downloadCancellationPending = false;
    // 不关闭 SSE，用户可再次取消或继续等待；避免网络瞬断时让后台下载失去可见状态。
    setAutoDownloadButton('下载中（取消未确认）', true);
    toast('danger', (error && error.message) || '无法确认取消下载，请重试。');
  }
}

function beginModelDownload() {
  const startBtn = document.getElementById('startBtn');
  let completed = false;
  downloadCancellationPending = false;
  // 下载过程保留弹窗，只让弹窗中的确认按钮展示真实进度；不触发右侧处理动画。
  setAutoDownloadButton('正在连接 0%', true);
  if (startBtn) {
    startBtn.disabled = true;
    setStartButtonLabel('等待模型下载');
  }

  closeEventSource(downloadEventSource);
  try {
    downloadEventSource = new EventSource(baseUrl + '/api/download/events?confirm=1&model_id=' + encodeURIComponent(activeModelId));
    downloadEventSource.addEventListener('message', (ev) => {
      if (downloadCancellationPending) return;
      try {
        const data = JSON.parse(ev.data);
        if (data.event === 'progress') {
          setAutoDownloadButton('下载中 ' + Math.round(data.percent || 0) + '%', true);
        } else if (data.event === 'error') {
          completed = true;
          finishModelDownload(data.message || t('errors.downloadError'));
        } else if (data.event === 'done') {
          completed = true;
          closeEventSource(downloadEventSource);
          downloadEventSource = null;
          setAutoDownloadButton('下载完成');
          window.setTimeout(() => {
            closeModal('downloadModal');
            if (batchFiles.length) beginBatchRemoveBg(batchFiles);
            else beginRemoveBg();
          }, 260);
        } else if (data.event === 'cancelled') {
          completed = true;
          finishModelDownload(data.message || '下载已取消，未完成文件已清理。', 'info');
        }
      } catch (_) {
        completed = true;
        finishModelDownload(t('errors.downloadError'));
      }
    });
    downloadEventSource.addEventListener('error', () => {
      if (!completed && !downloadCancellationPending) finishModelDownload('模型下载连接中断，请重试或改为手动下载。');
    });
  } catch (_) {
    finishModelDownload('无法创建下载连接，请使用 ModelScope 手动下载。');
  }
}

async function fetchCudaRuntimeInfo() {
  const response = await fetch(baseUrl + '/api/runtime/cuda/status');
  if (!response.ok) throw new Error('cuda runtime status request failed');
  return response.json();
}

function updateCudaRuntimeDialog(info) {
  const title = document.getElementById('cudaRuntimeModalTitle');
  const description = document.getElementById('cudaRuntimeModalDescription');
  const domestic = document.getElementById('cudaRuntimeDomesticSource');
  const global = document.getElementById('cudaRuntimeGlobalSource');
  const path = document.getElementById('cudaRuntimePath');
  const hint = document.getElementById('cudaRuntimeSpaceHint');
  const fileList = document.getElementById('cudaRuntimeFileList');
  const autoButton = document.getElementById('cudaRuntimeAutoBtn');
  const sources = Array.isArray(info && info.sources) ? info.sources : [];
  const domesticSource = sources.find((source) => source.id === 'domestic') || {};
  const globalSource = sources.find((source) => source.id === 'global') || {};
  if (title) title.textContent = info && info.runtime_ready ? 'CUDA 加速运行时已就绪' : '需要安装 CUDA 加速运行时';
  if (description) description.textContent = info && info.gpu_detected
    ? ('已检测到 ' + (info.gpu_name || 'NVIDIA GPU') + '。安装完成后应用会自动重启并启用 GPU。')
    : '未检测到可用 NVIDIA GPU。可以查看下载来源，但当前设备无法启用 CUDA 加速。';
  if (domestic) domestic.textContent = domesticSource.name || '国内镜像';
  if (global) global.textContent = globalSource.name || 'PyTorch 官方来源';
  if (path) path.textContent = (info && info.runtime_dir) || '运行时目录不可用';
  if (hint) hint.textContent = '需要约 ' + ((info && info.estimated_space_gib) || 8) + ' GB 可用空间；当前可用 ' + ((info && info.free_space_gib) || 0) + ' GB。';
  if (fileList) {
    const wheels = (info && info.required_wheels) || [];
    fileList.replaceChildren(...wheels.map((name) => {
      const item = document.createElement('span');
      item.textContent = name;
      return item;
    }));
  }
  if (autoButton) autoButton.disabled = !info || !info.gpu_detected || Boolean(info.runtime_ready);
}

function openCudaRuntimeSource(sourceId) {
  const modal = document.getElementById('cudaRuntimeModal');
  const info = modal && modal._runtimeInfo;
  const source = info && Array.isArray(info.sources) && info.sources.find((item) => item.id === sourceId);
  if (!source || !source.url) {
    toast('warn', '当前 CUDA 运行时暂未提供此下载来源。');
    return;
  }
  if (window.backendAPI && typeof window.backendAPI.openExternal === 'function') {
    window.backendAPI.openExternal(source.url).catch(() => toast('danger', '无法打开下载网页，请复制链接后在浏览器中访问。'));
  } else {
    window.open(source.url, '_blank', 'noopener');
  }
}

async function openCudaRuntimeDialog() {
  try {
    const info = await fetchCudaRuntimeInfo();
    const modal = document.getElementById('cudaRuntimeModal');
    if (modal) modal._runtimeInfo = info;
    updateCudaRuntimeDialog(info);
    openModal('cudaRuntimeModal');
    return info;
  } catch (_) {
    toast('danger', '无法检测 CUDA 运行时，请确认后端服务已启动。');
    return null;
  }
}

function setCudaRuntimeButton(label, downloading = false) {
  const button = document.getElementById('cudaRuntimeAutoBtn');
  const text = document.getElementById('cudaRuntimeAutoLabel');
  const cancel = document.getElementById('cudaRuntimeCancelBtn');
  if (text) text.textContent = label;
  if (button) {
    button.disabled = downloading;
    button.classList.toggle('is-downloading', downloading);
  }
  if (cancel) cancel.textContent = downloading ? (cudaRuntimeCancellationPending ? '正在取消…' : '取消下载') : '暂不安装';
}

async function cancelCudaRuntimeDownload() {
  if (!cudaRuntimeEventSource) {
    closeModal('cudaRuntimeModal');
    return;
  }
  cudaRuntimeCancellationPending = true;
  setCudaRuntimeButton('正在停止下载…', true);
  try {
    await fetch(baseUrl + '/api/runtime/cuda/cancel', { method: 'POST' });
    closeEventSource(cudaRuntimeEventSource);
    cudaRuntimeEventSource = null;
    cudaRuntimeCancellationPending = false;
    setCudaRuntimeButton('自动下载并启用');
    closeModal('cudaRuntimeModal');
    toast('info', 'CUDA 运行时下载已取消，未完成文件已清理。');
  } catch (_) {
    cudaRuntimeCancellationPending = false;
    setCudaRuntimeButton('下载中（取消未确认）', true);
    toast('danger', '无法确认取消 CUDA 下载，请重试。');
  }
}

function beginCudaRuntimeDownload() {
  let completed = false;
  cudaRuntimeCancellationPending = false;
  setCudaRuntimeButton('正在连接 0%', true);
  closeEventSource(cudaRuntimeEventSource);
  try {
    cudaRuntimeEventSource = new EventSource(baseUrl + '/api/runtime/cuda/events?confirm=1');
    cudaRuntimeEventSource.addEventListener('message', async (event) => {
      if (cudaRuntimeCancellationPending || completed) return;
      try {
        const data = JSON.parse(event.data);
        if (data.event === 'progress') {
          setCudaRuntimeButton('下载中 ' + Math.round(data.percent || 0) + '%', true);
        } else if (data.event === 'installing') {
          setCudaRuntimeButton('正在解压并安装 CUDA 运行时…', true);
        } else if (data.event === 'error') {
          completed = true;
          closeEventSource(cudaRuntimeEventSource);
          cudaRuntimeEventSource = null;
          setCudaRuntimeButton('自动下载并启用');
          toast('danger', data.message || 'CUDA 运行时下载失败。');
        } else if (data.event === 'cancelled') {
          completed = true;
          closeEventSource(cudaRuntimeEventSource);
          cudaRuntimeEventSource = null;
          setCudaRuntimeButton('自动下载并启用');
          toast('info', data.message || 'CUDA 运行时下载已取消。');
        } else if (data.event === 'complete') {
          completed = true;
          closeEventSource(cudaRuntimeEventSource);
          cudaRuntimeEventSource = null;
          setCudaRuntimeButton('安装完成，正在重启…', true);
          persistSettings({ device: 'cuda' });
          await fetch(baseUrl + '/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ DEVICE: 'cuda' }) }).catch(() => {});
          closeModal('cudaRuntimeModal');
          if (window.backendAPI && typeof window.backendAPI.relaunch === 'function') {
            window.backendAPI.relaunch();
          } else {
            toast('success', 'CUDA 运行时已安装，请重启应用后启用 GPU。');
          }
        }
      } catch (_) {
        completed = true;
        closeEventSource(cudaRuntimeEventSource);
        cudaRuntimeEventSource = null;
        setCudaRuntimeButton('自动下载并启用');
        toast('danger', 'CUDA 运行时下载响应无法解析。');
      }
    });
    cudaRuntimeEventSource.addEventListener('error', () => {
      if (!completed && !cudaRuntimeCancellationPending) {
        completed = true;
        closeEventSource(cudaRuntimeEventSource);
        cudaRuntimeEventSource = null;
        setCudaRuntimeButton('自动下载并启用');
        toast('danger', 'CUDA 运行时下载连接中断，请重试或使用手动来源。');
      }
    });
  } catch (_) {
    setCudaRuntimeButton('自动下载并启用');
    toast('danger', '无法创建 CUDA 下载连接。');
  }
}

async function startRemoveBg() {
  if (!backendReady) {
    void waitForBackendReady();
    return;
  }
  if (batchProcessing) {
    toast('warn', '批量任务进行中，请等待完成。');
    return;
  }
  if (batchFiles.length) {
    beginBatchRemoveBg(batchFiles);
    return;
  }
  if (!activeModelIsReady()) {
    toast('warn', '当前模型不可用，请重新选择模型。');
    return;
  }
  const status = await refreshDeviceStatus();
  if (!status) {
    setBackendAvailability(false);
    void waitForBackendReady();
    return;
  }
  if (!status.model_cached) {
    updateModelDownloadDialog(status.model_download || { target_dir: status.model_cache_dir });
    openModal('downloadModal');
    return;
  }
  if (!fileRef) {
    toast('warn', t('middle.pleaseSelectFirst'));
    return;
  }

  beginRemoveBg();
}

function beginRemoveBg() {
  const startBtn = document.getElementById('startBtn');
  clearBatchOverlayTimer();
  const stage = document.getElementById('dropZone');
  if (stage) stage.classList.remove('is-batch-processing');
  setProcessingOverlay('正在分离主体与背景', '正在准备任务…');
  setPreviewProcessing(true);

  // 准备 SSE 日志
  closeEventSource(logEventSource);
  try {
    logEventSource = new EventSource(baseUrl + '/api/logs/events');
    logEventSource.addEventListener('message', (ev) => {
      try {
        const d = JSON.parse(ev.data);
        appendLog(d.level || 'info', d.message || ev.data);
      } catch (_) {
        appendLog('info', ev.data);
      }
    });
    logEventSource.addEventListener('error', () => closeEventSource(logEventSource));
  } catch (e) {
    appendLog('warn', t('errors.logsUnavailable'));
  }

  if (startBtn) {
    startBtn.disabled = true;
    startBtn.classList.add('is-processing');
  }
  startTaskProgressAnimation();
  doRemoveBgRequest();
}

async function doRemoveBgRequest() {
  const startBtn = document.getElementById('startBtn');

  // 防止重复发起
  if (document.body.dataset.removeBgPending === '1') return;
  document.body.dataset.removeBgPending = '1';

  try {
    const params = collectParams();
    const modelLabel = (MODEL_META[params.model_id] && MODEL_META[params.model_id].label) || params.model_id;
    appendLog('info', '模型=' + modelLabel + '，官方规格=' + params.input_size + ' px，' + ((params.model_id === 'rmbg20' || params.model_id === 'birefnet') ? ('Alpha 阈值=' + params.threshold) : '原生 Alpha 输出') + '，输出=' + params.output_mode);

    const fd = createRemoveBgFormData(fileRef, params);

    const resp = await fetch(baseUrl + '/api/removebg', { method: 'POST', body: fd });
    const respBody = await resp.json().catch(() => ({}));
    const payload = (respBody && respBody.data) ? respBody.data : (respBody || {});

    if (resp.ok && payload && payload.output_path) {
      setProgress(100);
      outputPath = payload.output_path;
      const previewUrl = baseUrl + '/api/preview?path=' + encodeURIComponent(payload.output_path);
      revealResultPreview(previewUrl);
      const autoSavedPath = await autoSaveResultToSourceDir();
      if (autoSavedPath) {
        appendLog('info', '已自动保存：' + autoSavedPath);
        toast('success', '抠图完成，已自动保存到源文件目录。');
      } else {
        appendLog('info', t('middle.resultTip'));
        toast('success', '抠图完成，已显示处理结果。');
      }
    } else {
      const code = (respBody && respBody.code) ? String(respBody.code) : '9999';
      const backendMsg = (respBody && typeof respBody.message === 'string') ? respBody.message : '';
      const friendly = t('errors.' + code);
      // toast 面向用户：友好提示；日志面向排查：追加后端原文
      toast('danger', friendly);
      if (backendMsg && backendMsg !== friendly) {
        appendLog('error', friendly + '（后端原始：错误码 ' + code + ' - ' + backendMsg + '）');
      } else {
        appendLog('error', friendly);
      }
    }
  } catch (err) {
    toast('danger', t('errors.9999'));
    appendLog('error', (err && err.message) ? err.message : t('errors.networkError'));
  } finally {
    document.body.dataset.removeBgPending = '0';
    stopTaskProgressAnimation();
    setProgress(100);
    setPreviewProcessing(false);
    if (startBtn) {
      startBtn.disabled = !backendReady || !activeModelIsReady();
      startBtn.classList.remove('is-processing');
      setStartButtonLabel(getStartActionLabel());
    }
    setTimeout(() => resetProgress(), 350);
    closeEventSource(logEventSource);
    closeEventSource(downloadEventSource);
  }
}

function getSourceFilePath(file) {
  return file && typeof file.path === 'string' && file.path ? file.path : null;
}

function createRemoveBgFormData(file, params) {
  const fd = new FormData();
  fd.append('image', file, file.name);
  fd.append('threshold', String(params.threshold));
  fd.append('feather', String(params.feather));
  fd.append('edge_refine', String(params.edge_refine));
  fd.append('alpha_matting_enabled', String(params.alpha_matting_enabled));
  fd.append('alpha_matting_foreground_threshold', String(params.alpha_matting_foreground_threshold));
  fd.append('alpha_matting_background_threshold', String(params.alpha_matting_background_threshold));
  fd.append('alpha_matting_erode_size', String(params.alpha_matting_erode_size));
  fd.append('ben2_refine_foreground', String(Boolean(params.ben2_refine_foreground)));
  fd.append('inspyrenet_dynamic_resize', String(Boolean(params.inspyrenet_dynamic_resize)));
  fd.append('output_mode', params.output_mode);
  fd.append('model_id', params.model_id);
  return fd;
}

function setBatchTaskControls(isBusy) {
  const startBtn = document.getElementById('startBtn');
  const batchBtn = document.getElementById('batchBtn');
  const selectBtn = document.getElementById('selectBtn');
  const saveBtn = document.getElementById('saveBtn');
  if (startBtn) startBtn.disabled = isBusy || !backendReady || !activeModelIsReady();
  if (batchBtn) batchBtn.disabled = isBusy;
  if (selectBtn) selectBtn.disabled = isBusy;
  if (saveBtn) saveBtn.disabled = true;
  document.querySelectorAll('.model-tab').forEach((tab) => { tab.disabled = isBusy; });
  if (!isBusy) syncBatchSelectionUi();
}

async function processBatchFile(file, params) {
  const response = await fetch(baseUrl + '/api/removebg', { method: 'POST', body: createRemoveBgFormData(file, params) });
  const responseBody = await response.json().catch(() => ({}));
  const payload = (responseBody && responseBody.data) ? responseBody.data : (responseBody || {});
  if (!response.ok || !payload.output_path) {
    const code = (responseBody && responseBody.code) ? String(responseBody.code) : '9999';
    return { ok: false, message: t('errors.' + code) };
  }
  const saveOutput = window.backendAPI && window.backendAPI.saveOutputToBatchDir;
  if (typeof saveOutput !== 'function') {
    return { ok: false, message: '当前桌面环境未加载批量保存组件，请重启应用后重试。' };
  }
  const sourcePath = getSourceFilePath(file);
  if (!sourcePath) return { ok: false, message: '无法读取源图片路径，批量任务仅支持桌面版文件选择。' };
  const saved = await saveOutput({ sourcePath, outputPath: payload.output_path, modelId: params.model_id });
  return { ok: true, path: saved && saved.path, directory: saved && saved.directory };
}

async function beginBatchRemoveBg(files) {
  if (batchProcessing) return;
  const selectedFiles = Array.isArray(files) ? files : [];
  const validFiles = selectedFiles.filter((file) => file && file.type.startsWith('image/') && file.size <= 50 * 1024 * 1024);
  if (!validFiles.length) {
    toast('warn', '请选择至少一张不超过 50 MB 的图片。');
    return;
  }
  if (validFiles.length !== selectedFiles.length) toast('warn', '已跳过不支持或超过 50 MB 的文件。');
  if (validFiles.some((file) => !getSourceFilePath(file))) {
    toast('warn', '无法读取源图片路径，请重启桌面应用后再使用批量抠图。');
    return;
  }
  if (!activeModelIsReady()) {
    toast('warn', '当前模型不可用，请重新选择模型。');
    return;
  }
  const status = await refreshDeviceStatus();
  if (!status) {
    toast('danger', t('errors.backendDown'));
    return;
  }
  if (!status.model_cached) {
    updateModelDownloadDialog(status.model_download || { target_dir: status.model_cache_dir });
    openModal('downloadModal');
    return;
  }

  batchProcessing = true;
  batchFiles = [];
  syncBatchSelectionUi();
  const params = collectParams();
  const modelLabel = (MODEL_META[params.model_id] && MODEL_META[params.model_id].label) || params.model_id;
  let successCount = 0;
  let outputDirectory = '';
  prepareBatchPreview();
  setBatchTaskControls(true);
  appendLog('info', '开始批量抠图：' + validFiles.length + ' 张 · 模型=' + modelLabel);
  try {
    for (let index = 0; index < validFiles.length; index += 1) {
      const file = validFiles[index];
      setBatchProgress(index + 1, validFiles.length, file.name);
      try {
        const result = await processBatchFile(file, params);
        if (result.ok) {
          successCount += 1;
          outputDirectory = outputDirectory || result.directory || '';
          appendLog('info', '第 ' + (index + 1) + ' / ' + validFiles.length + ' 张已保存：' + (result.path || file.name));
        } else {
          appendLog('error', '第 ' + (index + 1) + ' / ' + validFiles.length + ' 张失败：' + result.message);
        }
      } catch (error) {
        appendLog('error', '第 ' + (index + 1) + ' / ' + validFiles.length + ' 张失败：' + ((error && error.message) || '未知错误'));
      }
      const progressBar = document.getElementById('batchProgressBar');
      const stage = document.getElementById('dropZone');
      const percent = ((index + 1) / validFiles.length) * 100;
      if (progressBar) progressBar.style.width = percent.toFixed(2) + '%';
      if (stage) stage.style.setProperty('--batch-progress', percent.toFixed(2) + '%');
    }
  } finally {
    batchProcessing = false;
    setBatchTaskControls(false);
    finishBatchPreview(successCount, validFiles.length, outputDirectory);
    const failedCount = validFiles.length - successCount;
    toast(failedCount ? 'warn' : 'success', '批量抠图完成：成功 ' + successCount + ' 张' + (failedCount ? ('，失败 ' + failedCount + ' 张') : '') + '。');
  }
}

// ============== 保存结果 ==============
async function saveResult() {
  if (!outputPath) {
    toast('warn', t('right.noResult'));
    return;
  }
  const previewUrl = baseUrl + '/api/preview?path=' + encodeURIComponent(outputPath);
  try {
    const resp = await fetch(previewUrl);
    if (!resp.ok) throw new Error('fetch failed');
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const baseName = (fileRef && fileRef.name) ? fileRef.name.replace(/\.[^.]+$/, '') : 'image';
    const ext = '.png';
    a.href = url;
    a.download = baseName + '_' + getModelFileTag() + ext;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    toast('success', t('right.saved', { p: a.download }));
  } catch (err) {
    toast('danger', t('errors.9999'));
    appendLog('error', (err && err.message) ? err.message : t('errors.saveFailed'));
  }
}

// ============== 设置 ==============
function readSettings() {
  try {
    const raw = localStorage.getItem('rmbg_settings');
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (_) {
    return {};
  }
}

function persistSettings(patch) {
  const next = Object.assign({}, readSettings(), patch || {});
  delete next.cacheDir;
  try { localStorage.setItem('rmbg_settings', JSON.stringify(next)); } catch (_) {}
  return next;
}

function normalizeDevice(value) {
  return value === 'cuda' ? 'cuda' : 'cpu';
}

function getSelectedDevice() {
  const trigger = document.getElementById('deviceSelectTrigger');
  return normalizeDevice(trigger && trigger.dataset.value);
}

function setSelectedDevice(value) {
  const device = normalizeDevice(value);
  const trigger = document.getElementById('deviceSelectTrigger');
  const text = document.getElementById('deviceSelectValue');
  const labels = {
    cuda: '使用 CUDA 加速',
    cpu: '仅使用 CPU',
  };
  if (trigger) trigger.dataset.value = device;
  if (text) text.textContent = labels[device];
  document.querySelectorAll('#deviceOptions [data-device-value]').forEach((option) => {
    const selected = option.dataset.deviceValue === device;
    option.classList.toggle('is-selected', selected);
    option.setAttribute('aria-selected', String(selected));
  });
}

function closeDeviceSelect() {
  const root = document.getElementById('deviceSelect');
  const trigger = document.getElementById('deviceSelectTrigger');
  const menu = document.getElementById('deviceOptions');
  if (root) root.classList.remove('is-open');
  if (trigger) trigger.setAttribute('aria-expanded', 'false');
  if (menu) menu.setAttribute('aria-hidden', 'true');
}

function initDeviceSelect() {
  const root = document.getElementById('deviceSelect');
  const trigger = document.getElementById('deviceSelectTrigger');
  const menu = document.getElementById('deviceOptions');
  if (!root || !trigger || !menu) return;
  trigger.addEventListener('click', () => {
    const open = !root.classList.contains('is-open');
    root.classList.toggle('is-open', open);
    trigger.setAttribute('aria-expanded', String(open));
    menu.setAttribute('aria-hidden', String(!open));
  });
  menu.querySelectorAll('[data-device-value]').forEach((option) => {
    option.addEventListener('click', async () => {
      if (option.disabled) return;
      if (option.dataset.deviceValue === 'cuda') {
        const info = await fetchCudaRuntimeInfo().catch(() => null);
        if (!info || !info.runtime_ready) {
          if (info) {
            const modal = document.getElementById('cudaRuntimeModal');
            if (modal) modal._runtimeInfo = info;
            updateCudaRuntimeDialog(info);
            openModal('cudaRuntimeModal');
          } else {
            toast('danger', '无法检测 CUDA 运行时，请确认后端服务已启动。');
          }
          closeDeviceSelect();
          return;
        }
      }
      setSelectedDevice(option.dataset.deviceValue);
      closeDeviceSelect();
      trigger.focus();
    });
  });
  document.addEventListener('pointerdown', (event) => {
    if (!root.contains(event.target)) closeDeviceSelect();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !root.classList.contains('is-open')) return;
    closeDeviceSelect();
    trigger.focus();
  });
}

async function autoSaveResultToSourceDir() {
  const settings = readSettings();
  if (!settings.autoSave || !outputPath || !sourceFilePath) return null;
  const saveOutput = window.backendAPI && window.backendAPI.saveOutputToSourceDir;
  if (typeof saveOutput !== 'function') {
    appendLog('warn', '当前运行环境无法自动保存到源文件目录。');
    return null;
  }
  try {
    const saved = await saveOutput({ sourcePath: sourceFilePath, outputPath, modelId: activeModelId });
    return saved && typeof saved.path === 'string' ? saved.path : null;
  } catch (error) {
    appendLog('warn', '自动保存失败：' + ((error && error.message) || '未知错误'));
    toast('warn', '抠图完成，但自动保存失败。');
    return null;
  }
}

async function loadSettings() {
  const cfg = readSettings();
  // 兼容旧版前端曾保存的 "gpu" 值；后端实际设备名为 "cuda"。
  const device = cfg.device === 'gpu' ? 'cuda' : (cfg.device || 'cpu');
  setSelectedDevice(device);
  const autoSaveEnabled = document.getElementById('autoSaveEnabled');
  if (autoSaveEnabled) autoSaveEnabled.checked = cfg.autoSave === true;

  try {
    const response = await fetch(baseUrl + '/api/config');
    if (!response.ok) throw new Error('config load failed');
    const payload = await response.json();
    const backendSettings = payload && payload.settings;
    if (!backendSettings || typeof backendSettings !== 'object') return;
    const backendDevice = backendSettings.DEVICE === 'gpu' ? 'cuda' : backendSettings.DEVICE;
    if (backendDevice === 'cpu' || backendDevice === 'cuda') {
      setSelectedDevice(backendDevice);
    }
    renderDeviceStatus(payload.device_info);
  } catch (_) {
    // 离线或后端启动中时保留 localStorage 中的上次值，状态卡会给出实际原因。
  }
}

function renderDeviceStatus(info) {
  const box = document.getElementById('deviceStatus');
  const title = document.getElementById('deviceStatusTitle');
  const detail = document.getElementById('deviceStatusDetail');
  const cudaOption = document.querySelector('#deviceOptions [data-device-value="cuda"]');
  if (!box || !title || !detail || !info) return;

  const usingGpu = info.actual_device === 'cuda';
  const gpuName = info.gpu_name || 'NVIDIA GPU';
  const requested = info.requested_device || 'cpu';
  box.classList.toggle('is-gpu', usingGpu);
  box.classList.toggle('is-cpu', !usingGpu);
  if (usingGpu) {
    title.textContent = '实际推理设备：GPU · ' + gpuName;
    detail.textContent = 'PyTorch ' + (info.torch_version || '') + ' · CUDA ' + (info.cuda_build || '已启用');
  } else {
    title.textContent = requested === 'cpu' ? '实际推理设备：CPU（已按设置）' : '实际推理设备：CPU（GPU 不可用）';
    detail.textContent = info.fallback_reason || ('PyTorch ' + (info.torch_version || '未检测到'));
  }
  if (cudaOption) {
    // Keep CUDA selectable in the CPU base edition: choosing it opens the
    // runtime installer instead of silently disabling a promised feature.
    cudaOption.disabled = false;
    cudaOption.classList.remove('is-disabled');
    cudaOption.setAttribute('aria-disabled', 'false');
  }
}

async function refreshDeviceStatus() {
  try {
    const status = await fetchBackendStatus(activeModelId);
    renderDeviceStatus(status.device_info);
    renderModelRuntimeStatus(status);
    return status;
  } catch (_) {
    renderDeviceStatus({ requested_device: 'cpu', actual_device: 'cpu', fallback_reason: '后端未连接，暂时无法检测设备' });
    return null;
  }
}

async function promptMissingModelOnStartup() {
  return waitForBackendReady();
}

async function fetchBackendStatus(modelId = activeModelId) {
  const response = await fetch(baseUrl + '/api/status?model_id=' + encodeURIComponent(modelId));
  if (!response.ok) throw new Error('status request failed');
  return response.json();
}

async function saveSettings() {
  const device = getSelectedDevice();
  const autoSaveEnabled = document.getElementById('autoSaveEnabled');
  if (device === 'cuda') {
    const runtime = await fetchCudaRuntimeInfo().catch(() => null);
    if (!runtime || !runtime.runtime_ready) {
      if (runtime) {
        const modal = document.getElementById('cudaRuntimeModal');
        if (modal) modal._runtimeInfo = runtime;
        updateCudaRuntimeDialog(runtime);
        openModal('cudaRuntimeModal');
      } else {
        toast('danger', '无法检测 CUDA 运行时，请确认后端服务已启动。');
      }
      return;
    }
  }
  persistSettings({ device, autoSave: Boolean(autoSaveEnabled && autoSaveEnabled.checked) });

  try {
    const response = await fetch(baseUrl + '/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        DEVICE: device
      })
    });
    if (!response.ok) throw new Error('config save failed');
    const payload = await response.json();
    renderDeviceStatus(payload.device_info);
  } catch (_) {
    await refreshDeviceStatus();
  }
  toast('success', t('settings.savedTip'));
  closeModal('settingsModal');
}

async function clearOutputImageCache() {
  const startBtn = document.getElementById('startBtn');
  if (batchProcessing || (startBtn && startBtn.classList.contains('is-processing'))) {
    toast('warn', '正在执行抠图任务，请完成后再清除图片缓存。');
    return;
  }
  if (!window.confirm('确定清除应用 output 文件夹中的全部结果图片吗？此操作无法恢复。')) return;

  const button = document.getElementById('clearOutputCacheBtn');
  if (button) button.disabled = true;
  try {
    const response = await fetch(baseUrl + '/api/output/clear', { method: 'POST' });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.code !== 0) {
      throw new Error(payload.message || 'clear output cache failed');
    }
    clearResultPreview();
    appendLog('info', payload.message || '已清除图片缓存。');
    toast('success', payload.message || '已清除图片缓存。');
  } catch (error) {
    toast('danger', (error && error.message) || '清除图片缓存失败。');
  } finally {
    if (button) button.disabled = false;
  }
}

// ============== 初始化 ==============
function initModals() {
  const settingsBtn = document.getElementById('settingsBtn');
  const logsBtn = document.getElementById('logsBtn');
  const aboutBtn = document.getElementById('aboutBtn');
  const settingsCancelBtn = document.getElementById('settingsCancelBtn');
  const logsCloseBtn = document.getElementById('logsCloseBtn');
  const settingsSaveBtn = document.getElementById('settingsSaveBtn');
  const aboutCloseBtn = document.getElementById('aboutCloseBtn');
  const startBtn = document.getElementById('startBtn');
  const saveBtn = document.getElementById('saveBtn');
  const clearBtn = document.getElementById('clearBtn');
  const clearOutputCacheBtn = document.getElementById('clearOutputCacheBtn');
  const downloadCancelBtn = document.getElementById('downloadCancelBtn');
  const openDomesticModelSourceBtn = document.getElementById('openDomesticModelSourceBtn');
  const openGlobalModelSourceBtn = document.getElementById('openGlobalModelSourceBtn');
  const checkModelBtn = document.getElementById('checkModelBtn');
  const downloadAutoBtn = document.getElementById('downloadAutoBtn');
  const cudaRuntimeCancelBtn = document.getElementById('cudaRuntimeCancelBtn');
  const cudaRuntimeAutoBtn = document.getElementById('cudaRuntimeAutoBtn');
  const openCudaRuntimeDomesticBtn = document.getElementById('openCudaRuntimeDomesticBtn');
  const openCudaRuntimeGlobalBtn = document.getElementById('openCudaRuntimeGlobalBtn');
  const autoSaveEnabled = document.getElementById('autoSaveEnabled');
  const modelRuntimeNote = document.getElementById('modelRuntimeNote');
  const backendBootRetryBtn = document.getElementById('backendBootRetryBtn');

  initDeviceSelect();

  if (settingsBtn) settingsBtn.addEventListener('click', () => { loadSettings(); openModal('settingsModal'); refreshDeviceStatus(); });
  if (logsBtn) logsBtn.addEventListener('click', () => openModal('logsModal'));
  if (aboutBtn) aboutBtn.addEventListener('click', () => openModal('aboutModal'));
  if (settingsCancelBtn) settingsCancelBtn.addEventListener('click', () => closeModal('settingsModal'));
  if (logsCloseBtn) logsCloseBtn.addEventListener('click', () => closeModal('logsModal'));
  if (settingsSaveBtn) settingsSaveBtn.addEventListener('click', saveSettings);
  if (aboutCloseBtn) aboutCloseBtn.addEventListener('click', () => closeModal('aboutModal'));
  if (autoSaveEnabled) autoSaveEnabled.addEventListener('change', () => {
    // 该开关与主题、模型参数一样即时写入；无需再点击“保存设置”。
    persistSettings({ autoSave: Boolean(autoSaveEnabled.checked) });
  });
  if (startBtn) startBtn.addEventListener('click', startRemoveBg);
  if (saveBtn) saveBtn.addEventListener('click', saveResult);
  if (clearBtn) clearBtn.addEventListener('click', clearLogs);
  if (clearOutputCacheBtn) clearOutputCacheBtn.addEventListener('click', clearOutputImageCache);
  if (downloadCancelBtn) downloadCancelBtn.addEventListener('click', cancelModelDownload);
  if (openDomesticModelSourceBtn) openDomesticModelSourceBtn.addEventListener('click', () => openModelDownloadSource('domestic'));
  if (openGlobalModelSourceBtn) openGlobalModelSourceBtn.addEventListener('click', () => openModelDownloadSource('global'));
  if (checkModelBtn) checkModelBtn.addEventListener('click', async () => {
    const status = await refreshDeviceStatus();
    if (status && status.model_cached) {
      closeModal('downloadModal');
      toast('success', '模型文件已就绪，现在可以开始抠图。');
    } else {
      updateModelDownloadDialog(status && (status.model_download || { target_dir: status.model_cache_dir }));
      toast('warn', '尚未检测到完整模型，请确认全部文件已放入指定目录。');
    }
  });
  if (downloadAutoBtn) downloadAutoBtn.addEventListener('click', () => {
    beginModelDownload();
  });
  if (cudaRuntimeCancelBtn) cudaRuntimeCancelBtn.addEventListener('click', cancelCudaRuntimeDownload);
  if (cudaRuntimeAutoBtn) cudaRuntimeAutoBtn.addEventListener('click', beginCudaRuntimeDownload);
  if (openCudaRuntimeDomesticBtn) openCudaRuntimeDomesticBtn.addEventListener('click', () => openCudaRuntimeSource('domestic'));
  if (openCudaRuntimeGlobalBtn) openCudaRuntimeGlobalBtn.addEventListener('click', () => openCudaRuntimeSource('global'));
  if (modelRuntimeNote) modelRuntimeNote.addEventListener('click', () => {
    openActiveModelDownloadDialog();
  });
  if (backendBootRetryBtn) backendBootRetryBtn.addEventListener('click', () => {
    if (!backendBootInFlight) void waitForBackendReady();
  });

  // 点击遮罩关闭
  ['settingsModal', 'logsModal', 'aboutModal', 'cudaRuntimeModal'].forEach(id => {
    const m = document.getElementById(id);
    if (!m) return;
    m.addEventListener('click', (e) => {
      if (e.target === m) closeModal(id);
    });
  });
}

function initBackendStatus() {
  if (window.backendAPI && typeof window.backendAPI.onBackendStatus === 'function') {
    window.backendAPI.onBackendStatus((event) => {
      const status = typeof event === 'string' ? event : event && event.status;
      if (status === 'starting' || status === 'running' || status === 'restarting') {
        setBackendAvailability(false);
        void waitForBackendReady();
        return;
      }
      if (status === 'error') {
        backendBootInFlight = false;
        setBackendAvailability(false);
        renderBackendBootState({
          title: '本地推理服务未能启动',
          detail: '服务已自动重试多次仍未就绪。请关闭应用后重新打开，或检查安装目录是否完整。',
          visible: true,
          slow: true,
          retry: true
        });
        appendLog('error', t('errors.backendDown'));
      }
    });
  }
}

// ============== 启动 ==============
async function loadI18n() {
  // Strategy: try three layers so i18n works EVERYWHERE (dev Vite, packaged
  // app.asar file://, manual browser open of dist/index.html) and NEVER lets
  // the user see raw keys like "topbar.title" or "startBtn".
  //
  //   1) <script type="application/json"> inlined by vite.config.js (build + dev)
  //      -> zero network, zero 404, zero CORS on file:// URLs.
  //   2) fetch('./i18n/zh-CN.json') fallback for dev or manual layouts that do
  //      not have the inline JSON.
  //   3) If both fail: do NOT call applyI18n at all -> index.html already carries
  //      Chinese innerHTML fallbacks inside every [data-i18n] element, so the UI
  //      remains 100% Chinese. This protects against unknown packaging issues.
  const INLINE_ID = 'inline-json-i18n-zh-CN-json';
  let inlineNode = null;
  try {
    // Find by exact id first; fall back to scanning any <script> for i18n/zh-CN.
    inlineNode = document.getElementById(INLINE_ID)
      || Array.from(document.querySelectorAll('script[type="application/json"][data-src*="zh-CN"]'))[0]
      || null;
  } catch (_) { /* ignore */ }

  if (inlineNode) {
    try {
      const parsed = JSON.parse(inlineNode.textContent || '{}');
      if (parsed && typeof parsed === 'object' && Object.keys(parsed).length) {
        i18n = parsed;
        return true;
      }
    } catch (_) {
      // Inline JSON malformed: fall through to fetch fallback.
      console.warn('i18n inline JSON parse failed, falling back to fetch()');
    }
  }

  try {
    // Always include a cache-buster so the packaged Electron app-asar reloads
    // a newly-compiled language file (instead of serving a stale cached copy
    // from Chromium's file:// cache after an upgrade).
    const bust = '?v=' + (window.i18nBuildStamp || new Date().getTime());
    const resp = await fetch('./i18n/zh-CN.json' + bust);
    if (resp.ok) {
      const parsed = await resp.json();
      if (parsed && typeof parsed === 'object' && Object.keys(parsed).length) {
        i18n = parsed;
        return true;
      }
    }
  } catch (e) {
    console.warn('i18n fetch failed (expected in no-network/package scenarios):', e);
  }

  return false;
}

async function bootstrap() {
  const loaded = await loadI18n();
  // Only replace element text if the dictionary was loaded successfully.
  // Otherwise keep the Chinese innerHTML fallbacks that are baked into index.html.
  if (loaded) {
    try { applyI18n(); } catch (e) { console.error('applyI18n failed', e); }
  } else {
    console.warn('[i18n] no dictionary loaded; keeping <html> Chinese fallbacks.');
    // Still update document title to the explicit Chinese fallback text.
    const titleAttr = document.querySelector('[data-i18n="topbar.title"]');
    if (titleAttr && titleAttr.textContent) {
      document.title = titleAttr.textContent;
    }
  }
  try { createIcons({ icons }); } catch (e) { console.warn('[icons] failed to render:', e); }
  loadParams();
  setBackendAvailability(false);
  initImageHandlers();
  initModelSwitcher();
  initComparison();
  initThemeSwitcher();
  initPreviewBackdropSwitcher();
  initParameterActions();
  initWindowControls();
  initModals();
  initBackendStatus();
  void promptMissingModelOnStartup();
  resetProgress();
}

bootstrap();
