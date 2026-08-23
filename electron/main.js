const { app, BrowserWindow, dialog, ipcMain, shell } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');

const APP_TITLE = '智能抠图整合工具';
const isDev = !app.isPackaged;
const devWindowIcon = path.join(__dirname, 'resources', 'icon.ico');

function resolveWindowIcon() {
  return isDev ? devWindowIcon : path.join(process.resourcesPath, 'icon.ico');
}

app.setName(APP_TITLE);
app.setAppUserModelId('com.rmbg.smart-cutout');

// 安装版的 Electron localStorage、缓存与崩溃记录也必须保存在安装目录，
// 不能静默写到 %APPDATA%。
if (!isDev) {
  const installRoot = path.dirname(process.execPath);
  const profileRoot = path.join(installRoot, 'profile');
  app.setPath('userData', profileRoot);
  app.setPath('sessionData', path.join(profileRoot, 'session'));
  app.setPath('logs', path.join(installRoot, 'logs', 'electron'));
  app.setPath('crashDumps', path.join(profileRoot, 'crashes'));
  app.setPath('temp', path.join(installRoot, 'temp', 'electron'));
}

let mainWindow = null;
let pythonProcess = null;
let restartCount = 0;
let devBackendWatcher = null;
let devRestartTimer = null;
let intentionalDevRestart = false;
const MAX_RESTART = 5;
const RESTART_DELAY_MS = 3000;

function logMain(msg) {
  const ts = new Date().toISOString();
  const line = `[main][${ts}] ${msg}`;
  // 同时输出到控制台
  console.log(line);
}

/**
 * 为 backend 计算用户数据根目录（供 config.py 中的 REMOVE_BG_ROOT 覆盖使用）。
 *
 * 安装版将 models/logs/output/config 都固定在 exe 同级。
 * 若安装目录不可写，必须明确报错，绝不回退到 AppData。
 */
function resolveAppRootForBackend() {
  const exeDir = path.dirname(process.execPath);
  const probeName = `.rmbg_write_test_${process.pid}`;
  const probePath = path.join(exeDir, probeName);
  try {
    // Step A: 尝试在 exeDir 直接写入+删除探针文件
    fs.writeFileSync(probePath, 'ok', { flag: 'w', mode: 0o600 });
    fs.unlinkSync(probePath);
    logMain(`[env] exe 目录可写，REMOVE_BG_ROOT=exeDir=${exeDir}`);
    return exeDir;
  } catch (e) {
    const reason = e && e.message ? e.message : String(e);
    logMain(`[env] 安装目录不可写（${exeDir}）：${reason}`);
    throw new Error(`安装目录不可写：${exeDir}\n请将应用重新安装到可写位置后重试。`);
  }
}

function sendBackendStatus(win, status, extra = {}) {
  if (win && !win.isDestroyed()) {
    win.webContents.send('backend-status', { status, ...extra });
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1320,
    height: 860,
    minWidth: 1100,
    minHeight: 720,
    center: true,
    title: APP_TITLE,
    // 与 Windows exe 内嵌图标使用同一份 ICO，确保任务栏不回退为 Electron 默认图标。
    icon: resolveWindowIcon(),
    frame: false,
    backgroundColor: '#0d1016',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  if (isDev) {
    mainWindow.loadURL('http://127.0.0.1:49174');
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// 自绘标题栏的窗口控制。使用事件来源窗口，避免多窗口时误操作主窗口。
ipcMain.handle('window:minimize', (event) => {
  BrowserWindow.fromWebContents(event.sender)?.minimize();
});

ipcMain.handle('window:toggle-maximize', (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (!win) return false;
  if (win.isMaximized()) win.unmaximize();
  else win.maximize();
  return win.isMaximized();
});

ipcMain.handle('window:close', (event) => {
  BrowserWindow.fromWebContents(event.sender)?.close();
});

ipcMain.handle('window:is-maximized', (event) => {
  return BrowserWindow.fromWebContents(event.sender)?.isMaximized() || false;
});

ipcMain.handle('app:relaunch', () => {
  app.relaunch();
  app.exit(0);
  return true;
});

// 外部网页必须由主进程调用 shell 打开。preload 属于渲染器上下文，直接使用
// shell.openExternal 在部分 Electron 版本会静默失败；同时只允许安全的网页协议。
ipcMain.handle('shell:open-external', async (_event, rawUrl) => {
  let url;
  try {
    url = new URL(String(rawUrl));
  } catch (_) {
    throw new Error('下载链接无效');
  }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') {
    throw new Error('只允许打开 HTTP 或 HTTPS 下载链接');
  }
  await shell.openExternal(url.toString());
  return true;
});

const MODEL_FILE_TAGS = Object.freeze({
  u2net: 'U2Net',
  rmbg20: 'RMBG20',
  birefnet: 'BiRefNet',
  ben2: 'BEN2',
  inspyrenet: 'InSPyReNet'
});

function resolveOutputName(sourcePath, targetDirectory, modelId) {
  const parsed = path.parse(sourcePath);
  const safeName = (parsed.name || 'image').replace(/[\\/:*?"<>|]/g, '_');
  const directory = targetDirectory || parsed.dir;
  const modelTag = MODEL_FILE_TAGS[String(modelId || '')] || 'RemoveBG';
  let targetPath = path.join(directory, `${safeName}_${modelTag}.png`);
  let attempt = 1;
  while (fs.existsSync(targetPath)) {
    targetPath = path.join(directory, `${safeName}_${modelTag} (${attempt}).png`);
    attempt += 1;
  }
  return targetPath;
}

ipcMain.handle('result:save-source-directory', (_event, payload) => {
  const sourcePath = payload && typeof payload.sourcePath === 'string' ? payload.sourcePath : '';
  const outputPath = payload && typeof payload.outputPath === 'string' ? payload.outputPath : '';
  if (!sourcePath || !outputPath || !path.isAbsolute(sourcePath) || !path.isAbsolute(outputPath)) {
    throw new Error('缺少有效的源图片或处理结果路径');
  }
  if (path.extname(outputPath).toLowerCase() !== '.png' || !fs.existsSync(outputPath)) {
    throw new Error('处理结果 PNG 不存在');
  }
  const targetPath = resolveOutputName(sourcePath, undefined, payload && payload.modelId);
  fs.copyFileSync(outputPath, targetPath);
  return { path: targetPath };
});

ipcMain.handle('result:save-batch-directory', (_event, payload) => {
  const sourcePath = payload && typeof payload.sourcePath === 'string' ? payload.sourcePath : '';
  const outputPath = payload && typeof payload.outputPath === 'string' ? payload.outputPath : '';
  if (!sourcePath || !outputPath || !path.isAbsolute(sourcePath) || !path.isAbsolute(outputPath)) {
    throw new Error('缺少有效的源图片或处理结果路径');
  }
  if (path.extname(outputPath).toLowerCase() !== '.png' || !fs.existsSync(outputPath)) {
    throw new Error('处理结果 PNG 不存在');
  }
  const batchDirectory = path.join(path.dirname(sourcePath), '抠图结果');
  fs.mkdirSync(batchDirectory, { recursive: true });
  const targetPath = resolveOutputName(sourcePath, batchDirectory, payload && payload.modelId);
  fs.copyFileSync(outputPath, targetPath);
  return { path: targetPath, directory: batchDirectory };
});

function resolveDevPython(backendDir) {
  const configured = process.env.REMOVE_BG_PYTHON;
  const candidates = [
    configured && path.resolve(configured),
    process.platform === 'win32'
      ? path.join(backendDir, 'venv', 'Scripts', 'python.exe')
      : path.join(backendDir, 'venv', 'bin', 'python'),
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }

  throw new Error(
    '未找到开发环境 Python。请先运行 build_all.bat 创建 backend\\venv，' +
    '或设置 REMOVE_BG_PYTHON 指向 Python 可执行文件。'
  );
}

function startPythonBackend() {
  // 若已经在运行，先清理
  if (pythonProcess && !pythonProcess.killed) {
    return;
  }

  let cmd;
  let args = [];
  let cwd;
  // Packaged 模式下传给 backend.exe 的环境变量（REMOVE_BG_ROOT 覆盖 APP_ROOT）。
  // 必须在 if/else 外层声明，否则 block-scoped 的 const/let 块外不可访问。
  let packEnv = undefined;

  if (isDev) {
    // 开发模式：强制使用项目 venv，避免系统 Python 缺少 torch 等依赖。
    cwd = path.join(__dirname, '..', 'backend');
    cmd = resolveDevPython(cwd);
    args = ['app.py', '--port', '49173', '--no-reload'];
    logMain(`[python] 开发模式启动: ${cmd} ${args.join(' ')} (cwd=${cwd})`);
  } else {
    // 生产模式：使用 extraResources 中的打包可执行文件
    // 注入 REMOVE_BG_ROOT：所有可变数据都跟随安装目录。
    const exe = path.join(process.resourcesPath, 'backend', 'backend.exe');
    cmd = exe;
    args = ['--port', '49173'];
    cwd = path.join(process.resourcesPath, 'backend');
    const rootTarget = resolveAppRootForBackend();
    const tempRoot = path.join(rootTarget, 'temp');
    fs.mkdirSync(tempRoot, { recursive: true });
    packEnv = Object.assign({}, process.env, {
      REMOVE_BG_ROOT: rootTarget,
      TEMP: tempRoot,
      TMP: tempRoot
    });
    logMain(`[python] 生产模式启动: ${cmd} ${args.join(' ')}`);
  }

  const spawnOpts = {
    cwd: cwd,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true
  };
  // packaged场景：覆盖env带上REMOVE_BG_ROOT；dev场景保留默认env
  if (packEnv) spawnOpts.env = packEnv;

  try {
    pythonProcess = spawn(cmd, args, spawnOpts);
  } catch (err) {
    logMain(`[python] spawn 失败: ${err.message}`);
    scheduleRestartPython({ error: err.message });
    return;
  }

  // 子进程已创建不等于 Flask 接口已完成监听。真正可用状态由渲染端
  // 成功请求 /api/status 后确认，避免首次启动被误判为服务异常。
  sendBackendStatus(mainWindow, 'starting');

  pythonProcess.stdout.on('data', (data) => {
    const text = data.toString().trimEnd();
    if (text) {
      logMain(`[python][stdout] ${text}`);
    }
  });

  pythonProcess.stderr.on('data', (data) => {
    const text = data.toString().trimEnd();
    if (text) {
      logMain(`[python][stderr] ${text}`);
    }
  });

  pythonProcess.on('error', (err) => {
    logMain(`[python] error 事件: ${err.message}`);
    // The close handler schedules the retry.  Do not show a fatal renderer
    // error for a transient child-process failure that is still recoverable.
  });

  pythonProcess.on('close', (code, signal) => {
    logMain(`[python] 退出 code=${code} signal=${signal}`);
    pythonProcess = null;
    if (isDev && intentionalDevRestart) {
      intentionalDevRestart = false;
      restartCount = 0;
      setTimeout(() => {
        if (mainWindow && !mainWindow.isDestroyed()) startPythonBackend();
      }, 250);
      return;
    }
    scheduleRestartPython({ code, signal });
  });
}

function scheduleRestartPython(lastFailure = {}) {
  if (restartCount >= MAX_RESTART) {
    logMain(`[python] 已达最大重启次数 ${MAX_RESTART}，停止自动重启。`);
    sendBackendStatus(mainWindow, 'error', { attempts: restartCount, ...lastFailure });
    return;
  }
  restartCount += 1;
  logMain(`[python] 将在 ${RESTART_DELAY_MS / 1000}s 后自动重启（第 ${restartCount}/${MAX_RESTART} 次）`);
  sendBackendStatus(mainWindow, 'restarting', { attempt: restartCount, ...lastFailure });
  setTimeout(() => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      startPythonBackend();
    } else {
      logMain('[python] 窗口已销毁，跳过重启。');
    }
  }, RESTART_DELAY_MS);
}

function stopPythonBackend() {
  if (pythonProcess && !pythonProcess.killed) {
    logMain('[python] 正在终止子进程...');
    try {
      pythonProcess.kill('SIGTERM');
    } catch (e) {
      // 忽略
    }
    // 兜底：Windows 下 SIGTERM 可能无效，再发一次 SIGKILL
    setTimeout(() => {
      if (pythonProcess && !pythonProcess.killed) {
        try {
          pythonProcess.kill('SIGKILL');
        } catch (e) {
          // 忽略
        }
      }
    }, 2000);
  }
}

function restartDevBackend(changedPath) {
  if (!isDev) return;
  if (changedPath) logMain(`[dev] 后端源码已变更，重启服务: ${changedPath}`);
  if (!pythonProcess || pythonProcess.killed) {
    restartCount = 0;
    startPythonBackend();
    return;
  }
  intentionalDevRestart = true;
  stopPythonBackend();
}

function watchDevBackend() {
  if (!isDev || devBackendWatcher) return;
  const backendDir = path.join(__dirname, '..', 'backend');
  const ignoredSegments = new Set(['venv', 'build', 'dist', '__pycache__']);

  try {
    devBackendWatcher = fs.watch(backendDir, { recursive: true }, (_event, filename) => {
      if (!filename || path.extname(filename) !== '.py') return;
      const parts = String(filename).split(path.sep);
      if (parts.some((part) => ignoredSegments.has(part))) return;
      clearTimeout(devRestartTimer);
      devRestartTimer = setTimeout(() => restartDevBackend(String(filename)), 250);
    });
    logMain(`[dev] 已监听后端 Python 源码: ${backendDir}`);
  } catch (err) {
    logMain(`[dev] 后端热重启监听不可用: ${err.message}`);
  }
}

function stopDevBackendWatcher() {
  clearTimeout(devRestartTimer);
  devRestartTimer = null;
  if (devBackendWatcher) {
    devBackendWatcher.close();
    devBackendWatcher = null;
  }
}

app.whenReady().then(() => {
  if (!isDev) {
    try {
      resolveAppRootForBackend();
    } catch (error) {
      dialog.showErrorBox('安装目录不可写', error.message || String(error));
      app.quit();
      return;
    }
  }
  logMain('App ready, 创建主窗口...');
  createWindow();
  watchDevBackend();
  logMain('启动 Python 后端...');
  startPythonBackend();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  stopDevBackendWatcher();
  stopPythonBackend();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  stopDevBackendWatcher();
  stopPythonBackend();
});
