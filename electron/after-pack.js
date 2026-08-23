/*
 * electron-builder normally edits the Windows executable icon itself.  That
 * path unpacks winCodeSign's macOS helper files and can fail on machines where
 * creating symbolic links is not permitted.  We leave that feature disabled
 * in package.json and update just the application's PE icon resource locally.
 */
const { spawnSync } = require('node:child_process');
const path = require('node:path');

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== 'win32') {
    return;
  }

  const executableName = `${context.packager.appInfo.productFilename}.exe`;
  const executablePath = path.join(context.appOutDir, executableName);
  const iconPath = path.join(__dirname, 'resources', 'icon.ico');
  const scriptPath = path.join(__dirname, 'embed-icon.ps1');
  const result = spawnSync(
    'powershell.exe',
    [
      '-NoLogo',
      '-NoProfile',
      '-ExecutionPolicy',
      'Bypass',
      '-File',
      scriptPath,
      '-ExecutablePath',
      executablePath,
      '-IconPath',
      iconPath,
    ],
    { encoding: 'utf8', windowsHide: true },
  );

  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(
      `Unable to write the application icon to ${executableName}: ${result.stderr || result.stdout}`,
    );
  }
};
