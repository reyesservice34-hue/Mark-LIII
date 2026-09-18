import {
  Activity, AlertTriangle, BarChart3, Bell, Bot, Box, Briefcase, Calendar, Check, ChevronDown, ChevronLeft,
  ChevronRight, Code, Command, Cpu, FileText, Folder, GitBranch, Globe, HardDrive, Home, LayoutGrid,
  ListChecks, Mail, MessageCircle, MessageSquare, Mic, Plug, Radio, RefreshCw, ScrollText, Search, Send,
  Server, Settings, ShieldCheck, Sparkles, Square, Timer, Trash2, Upload, Workflow, X, Zap, Play, Pause,
  Download, Copy, RotateCcw, Paperclip, Filter, LogOut, Terminal, Network, Layers, Eye, CircleAlert, Wifi,
  WifiOff, ChevronUp, Plus, FolderPlus, Pencil, MoveRight, KeyRound, UserRound, Circle, Monitor,
  GraduationCap, BookOpen, CircleStop,
} from "lucide-react";
import type { ComponentType } from "react";

const MAP: Record<string, ComponentType<any>> = {
  home: Home, "message-square": MessageSquare, bot: Bot, "list-checks": ListChecks, workflow: Workflow,
  timer: Timer, server: Server, folder: Folder, plug: Plug, "scroll-text": ScrollText, bell: Bell,
  "bar-chart-3": BarChart3, settings: Settings, "shield-check": ShieldCheck, sparkles: Sparkles, code: Code,
  search: Search, "file-text": FileText, briefcase: Briefcase, mail: Mail, calendar: Calendar, cpu: Cpu,
  "git-branch": GitBranch, radio: Radio, "message-circle": MessageCircle, globe: Globe, box: Box,
  activity: Activity, "layout-grid": LayoutGrid, zap: Zap, "hard-drive": HardDrive, terminal: Terminal,
  network: Network, layers: Layers, mic: Mic, command: Command, monitor: Monitor,
  "graduation-cap": GraduationCap,
};

export function Icon({ name, size = 16, ...rest }: { name?: string; size?: number; className?: string; style?: any }) {
  const C = (name && MAP[name]) || Box;
  return <C size={size} strokeWidth={1.75} {...rest} />;
}

export {
  Activity, AlertTriangle, BarChart3, Bell, Bot, Box, Check, ChevronDown, ChevronLeft, ChevronRight, ChevronUp,
  Command, Cpu, FileText, Folder, HardDrive, Home, ListChecks, Mic, Plug, RefreshCw, ScrollText, Search, Send,
  Server, Settings, ShieldCheck, Sparkles, Square, Timer, Trash2, Upload, Workflow, X, Zap, Play, Pause, Download,
  Copy, RotateCcw, Paperclip, Filter, LogOut, Terminal, Network, Layers, Eye, CircleAlert, Wifi, WifiOff, Plus,
  FolderPlus, Pencil, MoveRight, KeyRound, UserRound, Circle, MessageSquare, Radio, Monitor,
  GraduationCap, BookOpen, CircleStop,
};
