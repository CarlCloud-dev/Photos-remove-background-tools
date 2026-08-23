import { defineConfig } from 'vite';
import fs from 'node:fs';
import path from 'node:path';

// ---------------------------------------------------------------------------
// Plugin 1: 将 zh-CN.json 内容以内联 <script type="application/json"> 的
//           方式注入 index.html。打包后 Electron 加载 file:// 协议时无需
//           任何网络请求即可读取中文文案，从根本上杜绝 CORS / 404。
// ---------------------------------------------------------------------------
function inlineI18nPlugin() {
  return {
    name: 'inline-i18n-json',
    transformIndexHtml(html) {
      const rendererDir = path.resolve(__dirname, 'renderer');
      const files = ['i18n/zh-CN.json'];
      const extras = [];
      for (const f of files) {
        const full = path.join(rendererDir, f);
        if (!fs.existsSync(full)) continue;
        const raw = fs.readFileSync(full, 'utf-8');
        try { JSON.parse(raw); } catch (e) {
          console.error('[inlineI18nPlugin] invalid JSON:', f, e);
          continue;
        }
        const id = 'inline-json-' + f.replace(/[^a-zA-Z0-9]/g, '-');
        extras.push(
          `<script id="${id}" data-src="${f}" type="application/json">${raw}</script>`
        );
      }
      if (!extras.length) return html;
      return html.replace('</head>', extras.join('\n') + '\n</head>');
    },
  };
}

// ---------------------------------------------------------------------------
// Plugin 2: 将 renderer/i18n/* 物理拷贝到 dist/i18n/*，作为 fetch() 路径
//           的兜底（例如 dev 模式 / 浏览器手动打开 dist/index.html）。
//           完全零依赖，避免 vite-plugin-static-copy 的 peer 版本冲突。
// ---------------------------------------------------------------------------
function copyI18nPlugin() {
  const srcDir = path.resolve(__dirname, 'renderer', 'i18n');
  function copyAll(outDir) {
    if (!fs.existsSync(srcDir)) return;
    const dstDir = path.resolve(outDir, 'i18n');
    fs.mkdirSync(dstDir, { recursive: true });
    for (const name of fs.readdirSync(srcDir)) {
      if (name.startsWith('.')) continue;
      const s = path.join(srcDir, name);
      if (!fs.statSync(s).isFile()) continue;
      fs.copyFileSync(s, path.join(dstDir, name));
    }
  }
  return {
    name: 'copy-i18n-folder',
    // build 时：在 closeBundle 钩子写入（outDir 已清空）
    closeBundle() {
      // vite 配置里 outDir 是 '../dist'（相对 renderer/ root），实际绝对路径：
      const outDir = path.resolve(__dirname, 'dist');
      copyAll(outDir);
    },
    // dev server 时：configureServer 中间件直接 serve 源目录
    configureServer(server) {
      server.middlewares.use('/i18n/', (req, res, next) => {
        const rel = decodeURIComponent((req.url || '/').replace(/^\//, ''));
        if (!rel) return next();
        const f = path.join(srcDir, rel);
        if (fs.existsSync(f) && fs.statSync(f).isFile()) {
          res.setHeader('Content-Type', 'application/json; charset=utf-8');
          fs.createReadStream(f).pipe(res);
          return;
        }
        next();
      });
    },
  };
}

export default defineConfig({
  root: 'renderer',
  base: './',
  plugins: [inlineI18nPlugin(), copyI18nPlugin()],
  server: {
    port: 49174,
    strictPort: true,
    host: '127.0.0.1',
  },
  build: {
    outDir: '../dist',
    emptyOutDir: true,
  },
});
