import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useApi } from "@/lib/useApi";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { bytes, relative } from "@/lib/format";
import { Folder, FileText, Upload, FolderPlus, Download, Trash2, Pencil, MoveRight, Search, Eye } from "@/lib/icons";
import { EmptyState, ErrorState, Modal, Panel, Skeleton } from "@/components/ui";
import { toast } from "@/lib/toast";

interface Entry { path: string; name: string; is_dir: boolean; size: number; modified_at: string; mime: string; }

export default function FilesPage() {
  const { can } = useAuth();
  const [params] = useSearchParams();
  const [path, setPath] = useState("");
  const [q, setQ] = useState("");
  const dir = useApi<{ path: string; entries: Entry[]; parent: string | null }>(q ? null : `/api/files?path=${encodeURIComponent(path)}`, { refreshOn: ["file.changed"] });
  const search = useApi<{ results: Entry[] }>(q ? `/api/files/search?q=${encodeURIComponent(q)}` : null);
  const recent = useApi<{ files: any[]; usage: any }>("/api/files/recent?limit=12", { refreshOn: ["file.changed"] });
  const [preview, setPreview] = useState<{ entry: Entry; content?: string; truncated?: boolean } | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  useEffect(() => { if (params.get("focus") === "search") searchInput.current?.focus(); }, [params]);

  const entries = q ? search.data?.results || [] : dir.data?.entries || [];
  const dl = (e: Entry) => `${api.base}/api/files/download?path=${encodeURIComponent(e.path)}`;

  const upload = async (list: FileList | null) => {
    if (!list?.length) return;
    setUploading(true);
    try { for (const f of Array.from(list)) { const form = new FormData(); form.append("file", f); form.append("path", path || "uploads"); await api.upload("/api/files/upload", form); } toast({ title: "Upload complete", tone: "ok" }); dir.reload(); }
    catch (e: any) { toast({ title: "Upload failed", body: e.message, tone: "err" }); }
    finally { setUploading(false); if (fileInput.current) fileInput.current.value = ""; }
  };
  const mkdir = async () => { const name = window.prompt("Folder name"); if (name) { try { await api.post("/api/files/mkdir", { path: `${path ? path + "/" : ""}${name}` }); dir.reload(); } catch (e: any) { toast({ title: "Failed", body: e.message, tone: "err" }); } } };
  const rename = async (e: Entry) => { const name = window.prompt("New name", e.name); if (name && name !== e.name) { try { await api.post("/api/files/rename", { path: e.path, new_name: name }); dir.reload(); } catch (err: any) { toast({ title: "Rename failed", body: err.message, tone: "err" }); } } };
  const move = async (e: Entry) => { const dest = window.prompt("Move to folder (relative to workspace)", path); if (dest != null) { try { await api.post("/api/files/move", { path: e.path, destination: dest }); dir.reload(); } catch (err: any) { toast({ title: "Move failed", body: err.message, tone: "err" }); } } };
  const remove = async (e: Entry) => { if (window.confirm(`Move “${e.name}” to trash?`)) { try { await api.post("/api/files/delete", { path: e.path }); dir.reload(); } catch (err: any) { toast({ title: "Delete failed", body: err.message, tone: "err" }); } } };
  const open = async (e: Entry) => {
    if (e.is_dir) { setQ(""); setPath(e.path); return; }
    if (e.mime.startsWith("image/") || e.mime === "application/pdf") { setPreview({ entry: e }); return; }
    try { const r = await api.get(`/api/files/preview?path=${encodeURIComponent(e.path)}`); setPreview({ entry: e, content: r.content, truncated: r.truncated }); }
    catch (err: any) { toast({ title: "Preview failed", body: err.message, tone: "err" }); }
  };
  const crumbs = path ? path.split("/") : [];

  return (
    <div className="page" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); if (can("operator")) upload(e.dataTransfer.files); }}>
      <div className="page-head">
        <div><div className="eyebrow">Workspace · {recent.data ? `${recent.data.usage.files} files · ${bytes(recent.data.usage.bytes)}` : ""}</div><h1>Files</h1></div>
        <div className="actions">
          <div className="row" style={{ gap: 6 }}><Search size={14} style={{ color: "var(--text-3)" }} /><input ref={searchInput} className="input" style={{ width: 220 }} placeholder="Search by name" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search files" /></div>
          {can("operator") && <><input ref={fileInput} type="file" multiple hidden onChange={(e) => upload(e.target.files)} /><button className="btn" onClick={mkdir}><FolderPlus />Folder</button><button className="btn primary" onClick={() => fileInput.current?.click()} disabled={uploading}>{uploading ? <span className="spinner" /> : <Upload />}Upload</button></>}
        </div>
      </div>
      <ErrorState error={dir.error || search.error} retry={() => dir.reload(false)} />
      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 3fr) minmax(0, 1fr)" }}>
        <Panel title={<span className="row" style={{ gap: 4 }}><button className="btn sm ghost" onClick={() => { setQ(""); setPath(""); }}>workspace</button>{crumbs.map((c, i) => <span key={i} className="row" style={{ gap: 4 }}><span className="muted">/</span><button className="btn sm ghost" onClick={() => setPath(crumbs.slice(0, i + 1).join("/"))}>{c}</button></span>)}</span>} flush foot="Only the configured workspace is reachable; deletions go to .trash inside it.">
          {(q ? search.loading && !search.data : dir.loading && !dir.data) ? <div className="panel-body"><Skeleton rows={5} /></div> : entries.length === 0 ? <EmptyState icon={<Folder size={26} />} title={q ? "No files match" : "Empty folder"}>{q ? "" : "Upload files here or let an agent create documents."}</EmptyState> : (
            <table className="table">
              <thead><tr><th>Name</th><th>Size</th><th>Modified</th><th /></tr></thead>
              <tbody>
                {!q && dir.data?.parent != null && <tr className="clickable" onClick={() => setPath(dir.data!.parent!)}><td colSpan={4} className="muted">..</td></tr>}
                {entries.map((e) => (
                  <tr key={e.path} className="clickable" onClick={() => open(e)}>
                    <td className="row">{e.is_dir ? <Folder size={15} style={{ color: "var(--accent)" }} /> : <FileText size={15} style={{ color: "var(--text-3)" }} />}<span>{q ? e.path : e.name}</span></td>
                    <td className="num small muted">{e.is_dir ? "—" : bytes(e.size)}</td><td className="small muted">{relative(e.modified_at)}</td>
                    <td className="row" style={{ justifyContent: "flex-end", gap: 2 }} onClick={(ev) => ev.stopPropagation()}>
                      {!e.is_dir && <a className="btn icon ghost sm" href={dl(e)} title="Download"><Download /></a>}
                      {!e.is_dir && <button className="btn icon ghost sm" onClick={() => open(e)} title="Preview"><Eye /></button>}
                      {can("operator") && <><button className="btn icon ghost sm" onClick={() => rename(e)} title="Rename"><Pencil /></button><button className="btn icon ghost sm" onClick={() => move(e)} title="Move"><MoveRight /></button><button className="btn icon ghost sm" onClick={() => remove(e)} title="Delete"><Trash2 /></button></>}
                    </td>
                  </tr>))}
              </tbody>
            </table>)}
        </Panel>
        <Panel title="Recent & generated" icon={<FileText size={15} />} flush>
          {!recent.data ? <div className="panel-body"><Skeleton /></div> : recent.data.files.length === 0 ? <EmptyState title="Nothing yet" /> : <div className="list">{recent.data.files.map((f) => <div key={f.id} className="list-item" style={{ padding: "8px 12px" }}><div className="grow small" style={{ minWidth: 0 }}><a className="truncate" style={{ display: "block", color: "inherit" }} href={`${api.base}/api/files/download?path=${encodeURIComponent(f.path)}`}>{f.path}</a><div className="tiny muted">{f.source} · {bytes(f.size)} · {relative(f.updated_at)}{f.task_id && <> · <a href={`/tasks/${f.task_id}`}>task</a></>}{!f.exists && <span style={{ color: "var(--warn)" }}> · missing</span>}</div></div></div>)}</div>}
        </Panel>
      </div>
      {preview && (
        <Modal title={preview.entry.name} onClose={() => setPreview(null)} wide foot={<a className="btn" href={dl(preview.entry)}><Download />Download</a>}>
          {preview.entry.mime.startsWith("image/") ? <img src={`${dl(preview.entry)}&inline=1`} alt={preview.entry.name} style={{ maxWidth: "100%", borderRadius: 8 }} /> :
            preview.entry.mime === "application/pdf" ? <iframe title={preview.entry.name} src={`${dl(preview.entry)}&inline=1`} style={{ width: "100%", height: "60vh", border: 0, borderRadius: 8, background: "#fff" }} /> :
              <pre className="md" style={{ maxHeight: "60vh", overflow: "auto", whiteSpace: "pre-wrap" }}>{preview.content}{preview.truncated && "\n…[truncated]"}</pre>}
        </Modal>
      )}
    </div>
  );
}
