/// <reference types="vite/client" />

// Declared rather than left to the index signature of ImportMetaEnv, which types every
// variable as `any`: the version is read in one place and compared with what Docker Hub
// publishes, and an `any` there would hide a typo behind a banner that never appears.
interface ImportMetaEnv {
  readonly VITE_APP_VERSION?: string;
}
