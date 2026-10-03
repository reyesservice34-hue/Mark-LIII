import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "@/lib/auth";
import "@/design/base.css";
import "@/design/polish.css";
import "@/design/modern.css";
import "@/design/dashboard-layout.css";
import "@/design/noir-glass.css";
import "@/design/command-theme.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);

// Registers the no-op service worker (public/sw.js) that makes this page
// installable. A registration failure never blocks the application.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").then((reg) => {
      const activate = () => reg.waiting?.postMessage({ type: "SKIP_WAITING" });
      if (reg.waiting) activate();
      reg.addEventListener("updatefound", () => {
        reg.installing?.addEventListener("statechange", () => {
          if (reg.waiting) activate();
        });
      });
      window.setInterval(() => void reg.update(), 5 * 60 * 1000);
    }).catch(() => { /* not installable, still usable */ });
    let reloading = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (reloading) return;
      reloading = true;
      window.location.reload();
    });
  });
}
