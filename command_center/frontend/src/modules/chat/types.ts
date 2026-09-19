export interface Attachment { path: string; name: string; mime: string; size: number; }
export interface Message {
  id: string; conversation_id: string; role: "user" | "assistant" | "system"; content: string; blocks: Step[];
  status: "complete" | "streaming" | "error" | "stopped"; run_id?: string | null; created_at: string;
  meta: { attachments?: Attachment[]; error?: string; agent_id?: string; usage?: any; actor?: string; via?: string };
}
export interface Conversation { id: string; title: string; created_at: string; updated_at: string; archived: boolean; message_count?: number; preview?: string; }
export interface Step { ts: string; kind: string; text: string; tool?: string; ok?: boolean; agent_id?: string; approval_id?: string; child_run_id?: string; delegate_agent_id?: string; }
export interface RunState { id: string; status: string; label: string; agent_id: string; steps: Step[]; toolCalls: { tool: string; ok?: boolean; pending: boolean; output?: string }[]; }
