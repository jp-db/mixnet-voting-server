import { fileURLToPath, URL } from 'node:url';
import vue from '@vitejs/plugin-vue';
import { defineConfig } from 'vite';

// The voting protocol's WebAssembly build lives in the submodule
// (`make wasm` at the repository root builds evmlr-shuffle/wasm/build/).
const evmlrWasmDir = fileURLToPath(new URL('../evmlr-shuffle/wasm', import.meta.url));

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      '@evmlr': evmlrWasmDir,
    },
  },
  server: {
    fs: { allow: ['.', evmlrWasmDir] },
    proxy: { '/api': 'http://localhost:8000' },
  },
  worker: { format: 'es' },
});
