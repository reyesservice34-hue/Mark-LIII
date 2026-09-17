/**
 * Frontend module registry.
 *
 * The backend decides which modules exist and where they sit in navigation
 * (GET /api/auth/me → modules). The frontend maps a module id to a lazily
 * loaded page. A new module = one entry here + one folder under modules/.
 * Modules the backend does not report never appear in the sidebar.
 */
import { lazy, type LazyExoticComponent, type ComponentType } from "react";

export interface FrontendModule {
  id: string;
  component: LazyExoticComponent<ComponentType<any>>;
  routes?: string[];                 // extra route patterns relative to the module path
}

const registry = new Map<string, FrontendModule>();

export function registerModule(mod: FrontendModule) { registry.set(mod.id, mod); }
export function getModule(id: string) { return registry.get(id); }
export function allModules() { return [...registry.values()]; }

registerModule({ id: "home", component: lazy(() => import("@/modules/home/HomePage")) });
registerModule({ id: "chat", component: lazy(() => import("@/modules/chat/ChatPage")), routes: [":conversationId"] });
registerModule({ id: "agents", component: lazy(() => import("@/modules/agents/AgentsPage")), routes: [":agentId"] });
registerModule({ id: "tasks", component: lazy(() => import("@/modules/tasks/TasksPage")), routes: [":taskId"] });
registerModule({ id: "workflows", component: lazy(() => import("@/modules/workflows/WorkflowsPage")) });
registerModule({ id: "automations", component: lazy(() => import("@/modules/automations/AutomationsPage")) });
registerModule({ id: "server", component: lazy(() => import("@/modules/server/ServerPage")) });
registerModule({ id: "files", component: lazy(() => import("@/modules/files/FilesPage")) });
registerModule({ id: "integrations", component: lazy(() => import("@/modules/integrations/IntegrationsPage")) });
registerModule({ id: "logs", component: lazy(() => import("@/modules/logs/LogsPage")) });
registerModule({ id: "notifications", component: lazy(() => import("@/modules/notifications/NotificationsPage")) });
registerModule({ id: "approvals", component: lazy(() => import("@/modules/approvals/ApprovalsPage")), routes: [":approvalId"] });
registerModule({ id: "analytics", component: lazy(() => import("@/modules/analytics/AnalyticsPage")) });
registerModule({ id: "settings", component: lazy(() => import("@/modules/settings/SettingsPage")) });
