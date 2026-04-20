# React + TypeScript + Vite

## S3 + AWS env setup

1. Copy `.env.example` to `.env`.
2. Replace dummy values with your real AWS values (all use `VITE_` prefixes).
3. Start frontend with `npm run dev` and backend printing server with `npm run server`.

Notes:

- S3 access is now handled in the React app (browser).
- The Node server is only used for `/printimage`, `/test`, and `/fetch-for-print` (loads a presigned S3 image for printing so the browser does not need S3 CORS for that `fetch`).
- Ensure your S3 bucket CORS allows `GET`, `PUT`, and `HEAD` from your app origin (for local dev: `http://localhost:5173`).

### Raspberry Pi (printer server + Vite separately)

Run **two terminals** (or two systemd units):

1. Print API: **`./scripts/start-print-server.sh`** or **`npm run server`**
2. UI: **`./scripts/start-ui.sh`** or **`npm run dev -- --host`**

Vite proxies `/printimage` and `/test` to **`http://localhost:3000`**. Leave **`VITE_API_BASE` empty** in `.env` so printing uses that proxy (same origin as the UI). The print server listens only on **loopback** so phones use **`http://<pi-ip>:5173`**, not port 3000.

Optional systemd: **`scripts/print-server.service.example`** and **`scripts/vite-ui.service.example`** (two separate services).

- **`VITE_*`** values come from `.env` when Vite starts; restart dev after changing them.
- Add those browser origins to **S3 CORS**, e.g. `http://192.168.1.50:5173` and `http://raspberrypi.local:5173`.

#### Boot on power-on (systemd)

1. Install dependencies once on the Pi: **`npm ci`** in the project folder (and ensure **`.env`** has your `VITE_*` values).
2. Copy the examples (edit **`WorkingDirectory`** and **`User`** if your user is not `pi`):
   - `sudo cp scripts/print-server.service.example /etc/systemd/system/print-server.service`
   - `sudo cp scripts/vite-ui.service.example /etc/systemd/system/vite-ui.service`
3. If **`npm`** is not at `/usr/bin/npm` (e.g. you use **nvm**), change **`ExecStart`** in both units to the full path from **`which npm`** on the Pi, or use:
   `ExecStart=/bin/bash -lc 'cd /home/pi/printerGUI && npm run server'` (same idea for `npm run dev`).
4. Enable and start:
   - `sudo systemctl daemon-reload`
   - `sudo systemctl enable --now print-server.service`
   - `sudo systemctl enable --now vite-ui.service`
5. Check: **`systemctl status print-server`** and **`systemctl status vite-ui`**. The UI should be at **`http://localhost:5173`** on the Pi and **`http://<pi-ip>:5173`** on your LAN.

#### Fullscreen browser on the Pi (kiosk)

Use **Raspberry Pi OS with desktop** (or any desktop where you auto-login). After graphical login, autostart Chromium:

1. Copy **`scripts/kiosk-autostart.desktop.example`** to **`~/.config/autostart/printer-kiosk.desktop`** (see comments inside that file).
2. Adjust **`Exec`**: use **`chromium`** or **`chromium-browser`** depending on what is installed (`which chromium`).
3. The **`sleep 10`** gives Vite time to start after boot; increase if the page loads blank.
4. Reboot or log out and back in. Exit kiosk temporarily with **Alt+F4** or **Ctrl+W** depending on the browser.

If the Pi has **no monitor** (headless), fullscreen on the device itself does not apply; open **`http://<pi-ip>:5173`** from another computer or phone instead.

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type-aware lint rules:

```js
export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...

      // Remove tseslint.configs.recommended and replace with this
      tseslint.configs.recommendedTypeChecked,
      // Alternatively, use this for stricter rules
      tseslint.configs.strictTypeChecked,
      // Optionally, add this for stylistic rules
      tseslint.configs.stylisticTypeChecked,

      // Other configs...
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```

You can also install [eslint-plugin-react-x](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-x) and [eslint-plugin-react-dom](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-dom) for React-specific lint rules:

```js
// eslint.config.js
import reactX from 'eslint-plugin-react-x'
import reactDom from 'eslint-plugin-react-dom'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...
      // Enable lint rules for React
      reactX.configs['recommended-typescript'],
      // Enable lint rules for React DOM
      reactDom.configs.recommended,
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```
