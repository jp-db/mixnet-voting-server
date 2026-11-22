/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue';
  const component: DefineComponent<object, object, unknown>;
  export default component;
}

// evmlr-shuffle/wasm/evmlr.mjs (plain JS); only what the booth uses is typed here.
declare module '@evmlr/evmlr.mjs' {
  export class EvmlrError extends Error {
    status: number;
  }
  export interface Evmlr {
    modulus: number;
    voteLen: number;
    cast(pp: Uint8Array, voterId: number | bigint | string, vote: number | number[]): Uint8Array<ArrayBuffer>;
    checkBallot(pp: Uint8Array, ballot: Uint8Array): number | bigint;
    verify(pp: Uint8Array, ballots: Uint8Array[], transcript: Uint8Array): Uint16Array[];
  }
  export function loadEvmlr(moduleArgs?: object): Promise<Evmlr>;
}
