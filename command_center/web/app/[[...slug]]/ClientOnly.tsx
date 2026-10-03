"use client";
import dynamic from "next/dynamic";

// The Command Center is a client-only app (History-API router, cookie auth,
// EventSource); it is never rendered on the server.
const ClientApp = dynamic(() => import("./ClientApp"), { ssr: false });

export default function ClientOnly() {
  return <ClientApp />;
}
