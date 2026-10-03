"use client";
import { StrictMode, useEffect } from "react";
import App from "@/App";
import { AuthProvider } from "@/lib/auth";

// Registers the service worker (public/sw.js) that makes this page
// installable. A registration failure never blocks the application.
function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return undefined;
  let reloading = false;
  let timer: number | undefined;
  const onController = () => {
    if (reloading) return;
    reloading = true;
    window.location.reload();
  };
  navigator.serviceWorker.register("/sw.js").then((reg) => {
    const activate = () => reg.waiting?.postMessage({ type: "SKIP_WAITING" });
    if (reg.waiting) activate();
    reg.addEventListener("updatefound", () => {
      reg.installing?.addEventListener("statechange", () => {
        if (reg.waiting) activate();
      });
    });
    timer = window.setInterval(() => void reg.update(), 5 * 60 * 1000);
  }).catch(() => { /* not installable, still usable */ });
  navigator.serviceWorker.addEventListener("controllerchange", onController);
  return () => {
    if (timer) window.clearInterval(timer);
    navigator.serviceWorker.removeEventListener("controllerchange", onController);
  };
}

export default function ClientApp() {
  useEffect(registerServiceWorker, []);
  return (
    <StrictMode>
      <AuthProvider>
        <App />
      </AuthProvider>
    </StrictMode>
  );
}
