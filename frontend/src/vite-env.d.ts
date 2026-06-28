/// <reference types="vite/client" />

// Canonical Pragma ships its base styling as CSS-only packages (no type defs);
// these declarations let us side-effect-import them without TS complaints.
declare module "@canonical/styles";
declare module "@canonical/styles/fonts";
