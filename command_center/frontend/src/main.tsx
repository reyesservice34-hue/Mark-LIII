import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "@/lib/auth";
import "@/design/base.css";
import "@/design/polish.css";
import "@/design/modern.css";

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
// installable — the pinnable icon in the browser's address bar. Never lets a
// registration failure (an older browser, a blocked worker) break the app.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").then((reg) => {
      // MIA lives on the server: new UI builds should arrive automatically.
      // Conversation/task/calendar data is server-side, so this only refreshes the shell.
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
