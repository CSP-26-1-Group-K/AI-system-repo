const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('node:path');

const DEFAULT_SERVER = process.env.HOMESENSE_SERVER_URL || 'http://127.0.0.1:8080';

if (process.platform === 'linux') {
  app.commandLine.appendSwitch('no-sandbox');
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 720,
    backgroundColor: '#05080d',
    title: 'HomeSense Client',
    fullscreen: process.env.HOMESENSE_KIOSK === '1',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      // The app connects to a user-configured DGX server during demos.
      // Command traffic is restricted by UI controls and server endpoints.
      webSecurity: false,
    },
  });

  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));

  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

app.whenReady().then(() => {
  ipcMain.handle('config:get-default-server', () => DEFAULT_SERVER);
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
