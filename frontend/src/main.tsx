import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// Canonical Pragma design-system base styles + fonts (issue #40 — UI rebuild).
import "@canonical/styles";
import "@canonical/styles/fonts";

import App from "./App.tsx";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
