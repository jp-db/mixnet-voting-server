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
    // The ballot box on :8000. Run it with BALLOT_BOX_PUBLIC_URL=http://localhost:5173:
    // that is the origin the browser sends, which the ballot box checks.
    proxy: { '/api': 'http://localhost:8000', '/auth': 'http://localhost:8000' },
  },
  worker: { format: 'es' },
});
