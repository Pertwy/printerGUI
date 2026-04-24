# React + TypeScript + Vite

## S3 + AWS env setup

1. Copy `.env.example` to `.env`.
2. Replace dummy values with your real AWS values (all use `VITE_` prefixes).
3. Start frontend with `npm run dev` and backend printing server with `npm run server`.

Notes:

- S3 access is now handled in the React app (browser).
- Upload and delete are fully browser-to-S3 (no Node server required for those actions).
- The Node server is only used for `/printimage`, `/test`, and `/fetch-for-print` (for the Pi print flow).
- Ensure your S3 bucket CORS allows `GET`, `PUT`, `DELETE`, `HEAD`, and `OPTIONS` from your app origin (for local dev: `http://localhost:5173`).
- If using AWS SDK in browser, include permissive headers in S3 CORS (for example `AllowedHeaders: ["*"]`) so signed requests can pass preflight.

- **`VITE_*`** values come from `.env` when Vite starts; restart dev after changing them.
- Add those browser origins to **S3 CORS**, e.g. `http://192.168.1.50:5173` and `http://raspberrypi.local:5173`.

### Raspberry Pi Tkinter print page (lightweight alternative)

If Chromium + React is too heavy on the Pi, use a native Tkinter print page instead:

1. Keep using your React app on your computer for uploads (`Upload` page).
2. On the Pi, run the Node print server (`npm run server`).
3. Install Python deps once:
   - `python3 -m venv .venv`
   - `source .venv/bin/activate`
   - `pip install -r pi_tkinter/requirements.txt`
4. Run:
   - `python3 pi_tkinter/print_page.py`

This app reads the same `.env` values used by the React app (`VITE_AWS_REGION`, `VITE_S3_BUCKET`, `VITE_S3_UPLOAD_PREFIX`/`VITE_S3_LIST_PREFIX`, and AWS keys), lists recent images from S3, and sends print jobs through `http://localhost:3000/fetch-for-print` + `/printimage`.

Optional: set `PRINTER_API_BASE` if your print API is not local, for example:
`PRINTER_API_BASE=http://127.0.0.1:3000 python3 pi_tkinter/print_page.py`

If you see `ModuleNotFoundError: No module named '_tkinter'`:

- On Raspberry Pi OS: `sudo apt install -y python3-tk`
- On macOS (for local testing): use a Python build that ships with Tk (python.org installer is easiest).

#### Tkinter boot on power-on (systemd)

Use this if you want the Pi to boot directly into the Tkinter print UI instead of Chromium+React:

1. Make sure print API service is installed first:
   - `sudo cp scripts/print-server.service.example /etc/systemd/system/print-server.service`
2. Create Python venv and deps in the project:
   - `python3 -m venv .venv`
   - `source .venv/bin/activate`
   - `pip install -r pi_tkinter/requirements.txt`
3. Copy the Tkinter service:
   - `sudo cp scripts/tkinter-print-ui.service.example /etc/systemd/system/tkinter-print-ui.service`
4. Edit `WorkingDirectory`, `User`, and `ExecStart` if your username/path differs from `pi`.
5. Enable + start:
   - `sudo systemctl daemon-reload`
   - `sudo systemctl enable --now print-server.service`
   - `sudo systemctl enable --now tkinter-print-ui.service`
6. Check:
   - `systemctl status print-server`
   - `systemctl status tkinter-print-ui`

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
