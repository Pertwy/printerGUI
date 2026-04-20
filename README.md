# React + TypeScript + Vite

## S3 + AWS env setup

1. Copy `.env.example` to `.env`.
2. Replace dummy values with your real AWS values (all use `VITE_` prefixes).
3. Start frontend with `npm run dev` and backend printing server with `npm run server`.

Notes:

- S3 access is now handled in the React app (browser).
- The Node server is only used for `/printimage` and `/test`.
- Ensure your S3 bucket CORS allows `GET`, `PUT`, and `HEAD` from your app origin (for local dev: `http://localhost:5173`).

### Raspberry Pi (printer server + Vite separately)

Vite is configured with `host: true` so you can use the UI from **`http://<pi-ip>:5173`** on your LAN.

- Start both processes: **`./scripts/start-pi.sh`** or **`npm run start:pi`** (runs `npm run server`, then **`npm run dev -- --host`**).
- Optional boot service: adapt **`scripts/printer-gui.service.example`** into `/etc/systemd/system/printer-gui.service`.
- **`VITE_*`** values come from `.env` when Vite starts; restart dev after changing them.
- Add those browser origins to **S3 CORS**, e.g. `http://192.168.1.50:5173` and `http://raspberrypi.local:5173`.

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
