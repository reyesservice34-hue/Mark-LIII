#!/usr/bin/env python3
"""
JARVIS Web UI Server - MODERN DESIGN
Moderner, cooles Dashboard mit Glassmorphism & Animationen
"""

import os
import json
import logging
from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Configuration
JARVIS_COORDINATOR_URL = os.getenv("JARVIS_COORDINATOR_URL", "http://localhost:8000")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
WHATSAPP_GATEWAY_URL = os.getenv("WHATSAPP_GATEWAY_URL", "http://localhost:5000")

dashboard_data = {"tasks": [], "services": {}, "uptime_seconds": 0}

class JARVISWebUIManager:
    def __init__(self):
        self.coordinator_url = JARVIS_COORDINATOR_URL
        self.ollama_url = OLLAMA_URL
        self.whatsapp_url = WHATSAPP_GATEWAY_URL
        self.tasks_file = Path("jarvis_dashboard_tasks.json")
        self.load_tasks()

    def load_tasks(self):
        if self.tasks_file.exists():
            with open(self.tasks_file) as f:
                dashboard_data["tasks"] = json.load(f)

    def save_tasks(self):
        with open(self.tasks_file, 'w') as f:
            json.dump(dashboard_data["tasks"], f, indent=2)

    def add_task(self, title: str, priority: str = "medium") -> dict:
        task = {
            "id": datetime.now().timestamp(),
            "title": title,
            "completed": False,
            "priority": priority,
            "created": datetime.now().isoformat()
        }
        dashboard_data["tasks"].append(task)
        self.save_tasks()
        return task

    def complete_task(self, task_id: float) -> dict:
        task = next((t for t in dashboard_data["tasks"] if t["id"] == task_id), None)
        if task:
            task["completed"] = True
            self.save_tasks()
            return task
        return None

    def delete_task(self, task_id: float) -> bool:
        initial_count = len(dashboard_data["tasks"])
        dashboard_data["tasks"] = [t for t in dashboard_data["tasks"] if t["id"] != task_id]
        if len(dashboard_data["tasks"]) < initial_count:
            self.save_tasks()
            return True
        return False

    def get_service_status(self) -> dict:
        services = {
            "coordinator": {"url": self.coordinator_url, "port": 8000, "name": "Coordinator", "icon": "⚙️"},
            "ollama": {"url": self.ollama_url, "port": 11434, "name": "Ollama LLM", "icon": "🧠"},
            "whatsapp_gateway": {"url": self.whatsapp_url, "port": 5000, "name": "WhatsApp", "icon": "💬"}
        }

        status = {}
        for name, config in services.items():
            try:
                response = requests.get(f"{config['url']}/health", timeout=2)
                status[name] = {
                    "status": "online" if response.status_code == 200 else "offline",
                    "latency_ms": response.elapsed.total_seconds() * 1000,
                    "port": config["port"],
                    "icon": config["icon"],
                    "name": config["name"]
                }
            except:
                status[name] = {
                    "status": "offline",
                    "latency_ms": 0,
                    "port": config["port"],
                    "icon": config["icon"],
                    "name": config["name"]
                }

        dashboard_data["services"] = status
        return status

    def send_instruction_to_jarvis(self, instruction: str) -> dict:
        try:
            response = requests.post(
                f"{self.coordinator_url}/process_instruction",
                json={"user_id": "web_dashboard", "instruction": instruction, "channel": "web_ui"},
                timeout=30
            )
            return response.json() if response.status_code == 200 else {"error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"error": str(e)}

manager = JARVISWebUIManager()

@app.route("/api/tasks", methods=["GET"])
def get_tasks():
    return jsonify(dashboard_data["tasks"])

@app.route("/api/tasks", methods=["POST"])
def create_task():
    data = request.get_json()
    task = manager.add_task(data.get("title", ""), data.get("priority", "medium"))
    return jsonify(task), 201

@app.route("/api/tasks/<task_id>", methods=["PUT"])
def update_task(task_id):
    task = manager.complete_task(float(task_id))
    return (jsonify(task), 200) if task else (jsonify({"error": "Not found"}), 404)

@app.route("/api/tasks/<task_id>", methods=["DELETE"])
def delete_task(task_id):
    success = manager.delete_task(float(task_id))
    return (jsonify({"success": success}), 200) if success else (jsonify({"success": False}), 404)

@app.route("/api/services", methods=["GET"])
def get_services():
    return jsonify(manager.get_service_status())

@app.route("/api/jarvis/process", methods=["POST"])
def process_instruction():
    data = request.get_json()
    instruction = data.get("instruction", "")
    return (jsonify({"error": "No instruction"}), 400) if not instruction else jsonify(manager.send_instruction_to_jarvis(instruction))

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "JARVIS Web UI Server", "timestamp": datetime.now().isoformat(), "version": "2.1"})

@app.route("/", methods=["GET"])
def serve_dashboard():
    dashboard_html = """
    <!DOCTYPE html>
    <html lang="de">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>🤖 JARVIS Command Center</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }

            :root {
                --primary: #00d9ff;
                --secondary: #ff006e;
                --dark: #0a0e27;
                --darker: #050912;
                --accent: #00f5ff;
                --success: #00ff88;
                --warning: #ffa500;
                --danger: #ff0055;
            }

            body {
                font-family: 'Inter', 'Segoe UI', sans-serif;
                background: linear-gradient(135deg, #0f1419 0%, #1a1f3a 50%, #2d1b4e 100%);
                color: #ffffff;
                overflow-x: hidden;
                min-height: 100vh;
            }

            .container { display: grid; grid-template-columns: 1fr; gap: 20px; padding: 30px; max-width: 1600px; margin: 0 auto; }

            .header {
                background: linear-gradient(135deg, rgba(0, 217, 255, 0.1), rgba(255, 0, 110, 0.05));
                backdrop-filter: blur(20px);
                border: 1px solid rgba(0, 217, 255, 0.2);
                border-radius: 20px;
                padding: 40px;
                text-align: center;
                position: relative;
                overflow: hidden;
            }

            .header::before {
                content: '';
                position: absolute;
                width: 400px;
                height: 400px;
                background: radial-gradient(circle, rgba(0, 217, 255, 0.1), transparent);
                top: -100px;
                right: -100px;
                border-radius: 50%;
            }

            .header h1 {
                font-size: 48px;
                background: linear-gradient(135deg, var(--primary), var(--accent));
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                background-clip: text;
                margin-bottom: 10px;
                font-weight: 800;
                position: relative;
                z-index: 1;
            }

            .header-status {
                display: flex;
                justify-content: center;
                align-items: center;
                gap: 10px;
                font-size: 14px;
                color: var(--success);
                position: relative;
                z-index: 1;
            }

            .status-dot {
                width: 10px;
                height: 10px;
                background: var(--success);
                border-radius: 50%;
                animation: pulse 2s infinite;
            }

            @keyframes pulse { 0%, 100% { opacity: 1; box-shadow: 0 0 10px var(--success); } 50% { opacity: 0.7; } }

            .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 25px; }

            .card {
                background: rgba(15, 23, 42, 0.7);
                backdrop-filter: blur(30px);
                border: 1px solid rgba(0, 217, 255, 0.2);
                border-radius: 20px;
                padding: 30px;
                transition: all 0.3s ease;
                position: relative;
                overflow: hidden;
            }

            .card::before {
                content: '';
                position: absolute;
                width: 100%;
                height: 100%;
                background: linear-gradient(135deg, rgba(0, 217, 255, 0.05), transparent);
                top: 0;
                left: 0;
            }

            .card:hover {
                border-color: rgba(0, 217, 255, 0.4);
                box-shadow: 0 8px 32px rgba(0, 217, 255, 0.15);
                transform: translateY(-5px);
            }

            .card h2 {
                font-size: 22px;
                color: var(--primary);
                margin-bottom: 20px;
                position: relative;
                z-index: 1;
            }

            .input-group {
                display: flex;
                gap: 10px;
                margin-bottom: 20px;
                position: relative;
                z-index: 1;
            }

            .input-group input {
                flex: 1;
                background: rgba(0, 217, 255, 0.08);
                border: 1px solid rgba(0, 217, 255, 0.3);
                border-radius: 12px;
                padding: 14px 18px;
                color: #fff;
                font-size: 14px;
                transition: all 0.3s ease;
            }

            .input-group input:focus {
                outline: none;
                background: rgba(0, 217, 255, 0.15);
                border-color: var(--primary);
                box-shadow: 0 0 20px rgba(0, 217, 255, 0.2);
            }

            .btn {
                padding: 12px 28px;
                border: none;
                border-radius: 12px;
                background: linear-gradient(135deg, var(--primary), var(--accent));
                color: #000;
                font-weight: 700;
                cursor: pointer;
                transition: all 0.3s ease;
                font-size: 14px;
            }

            .btn:hover {
                transform: scale(1.05);
                box-shadow: 0 8px 20px rgba(0, 217, 255, 0.3);
            }

            .task-item {
                background: rgba(0, 217, 255, 0.06);
                border: 1px solid rgba(0, 217, 255, 0.15);
                border-radius: 12px;
                padding: 16px;
                margin-bottom: 12px;
                display: flex;
                align-items: center;
                gap: 15px;
                transition: all 0.3s ease;
                position: relative;
                z-index: 1;
            }

            .task-item:hover {
                background: rgba(0, 217, 255, 0.12);
                border-color: rgba(0, 217, 255, 0.25);
            }

            .task-checkbox {
                width: 22px;
                height: 22px;
                border: 2px solid var(--primary);
                border-radius: 6px;
                cursor: pointer;
                transition: all 0.3s ease;
                flex-shrink: 0;
            }

            .task-checkbox.checked {
                background: var(--success);
                border-color: var(--success);
            }

            .task-content { flex: 1; }
            .task-title { font-weight: 600; font-size: 15px; }
            .task-priority { font-size: 12px; color: #888; margin-top: 4px; }

            .service-card {
                background: rgba(0, 217, 255, 0.06);
                border: 1px solid rgba(0, 217, 255, 0.15);
                border-radius: 12px;
                padding: 18px;
                margin-bottom: 12px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                transition: all 0.3s ease;
                position: relative;
                z-index: 1;
            }

            .service-card:hover {
                background: rgba(0, 217, 255, 0.12);
                border-color: rgba(0, 217, 255, 0.25);
            }

            .service-info { display: flex; gap: 12px; align-items: center; }
            .service-icon { font-size: 24px; }
            .service-status { display: flex; flex-direction: column; }
            .service-name { font-weight: 600; font-size: 14px; }
            .service-port { font-size: 12px; color: #888; }

            .status-badge {
                padding: 6px 12px;
                border-radius: 8px;
                font-size: 12px;
                font-weight: 600;
            }

            .status-badge.online {
                background: rgba(0, 255, 136, 0.2);
                color: var(--success);
                border: 1px solid var(--success);
            }

            .status-badge.offline {
                background: rgba(255, 0, 85, 0.2);
                color: var(--danger);
                border: 1px solid var(--danger);
            }

            @media (max-width: 768px) {
                .grid { grid-template-columns: 1fr; }
                .header { padding: 30px 20px; }
                .header h1 { font-size: 36px; }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🤖 JARVIS COMMAND CENTER</h1>
                <div class="header-status">
                    <span class="status-dot"></span>
                    <span>System ONLINE</span>
                </div>
            </div>

            <div class="grid">
                <div class="card">
                    <h2>📋 Aufgaben</h2>
                    <div class="input-group">
                        <input type="text" id="taskInput" placeholder="Neue Aufgabe eingeben...">
                        <button class="btn" onclick="addTask()">+ HINZUFÜGEN</button>
                    </div>
                    <div id="tasksList"></div>
                </div>

                <div class="card">
                    <h2>⚙️ Services</h2>
                    <div id="servicesList"></div>
                </div>
            </div>
        </div>

        <script>
            async function loadTasks() {
                const res = await fetch('/api/tasks');
                const tasks = await res.json();
                const html = tasks.map(t => `
                    <div class="task-item">
                        <div class="task-checkbox ${t.completed ? 'checked' : ''}" onclick="toggleTask(${t.id})"></div>
                        <div class="task-content">
                            <div class="task-title" style="${t.completed ? 'text-decoration: line-through; opacity: 0.6;' : ''}">${t.title}</div>
                            <div class="task-priority">Priorität: ${t.priority}</div>
                        </div>
                        <button class="btn" style="padding: 6px 12px; font-size: 12px;" onclick="deleteTask(${t.id})">✕</button>
                    </div>
                `).join('');
                document.getElementById('tasksList').innerHTML = html || '<p style="opacity: 0.6;">Keine Aufgaben noch</p>';
            }

            async function loadServices() {
                const res = await fetch('/api/services');
                const services = await res.json();
                const html = Object.entries(services).map(([key, svc]) => `
                    <div class="service-card">
                        <div class="service-info">
                            <div class="service-icon">${svc.icon}</div>
                            <div class="service-status">
                                <div class="service-name">${svc.name}</div>
                                <div class="service-port">Port: ${svc.port}</div>
                            </div>
                        </div>
                        <span class="status-badge ${svc.status}">${svc.status === 'online' ? '🟢 ONLINE' : '🔴 OFFLINE'}</span>
                    </div>
                `).join('');
                document.getElementById('servicesList').innerHTML = html;
            }

            async function addTask() {
                const input = document.getElementById('taskInput');
                if (input.value.trim()) {
                    await fetch('/api/tasks', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({title: input.value, priority: 'medium'})
                    });
                    input.value = '';
                    loadTasks();
                }
            }

            async function toggleTask(id) {
                await fetch(`/api/tasks/${id}`, {method: 'PUT'});
                loadTasks();
            }

            async function deleteTask(id) {
                await fetch(`/api/tasks/${id}`, {method: 'DELETE'});
                loadTasks();
            }

            document.getElementById('taskInput').addEventListener('keypress', e => {
                if (e.key === 'Enter') addTask();
            });

            loadTasks();
            loadServices();
            setInterval(() => { loadServices(); }, 5000);
        </script>
    </body>
    </html>
    """
    return dashboard_html

if __name__ == "__main__":
    print("\n" + "="*70)
    print("🚀 Starting JARVIS Web UI Server (MODERN DESIGN)")
    print("="*70)
    print("📱 Dashboard: http://localhost:3000")
    print("="*70 + "\n")
    app.run(host="0.0.0.0", port=3000, debug=False)
