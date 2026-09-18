#!/usr/bin/env python3
"""
JARVIS Web UI Server
Verbindet das moderne Dashboard mit der JARVIS Coordinator API
Läuft auf localhost:3000 für Remote-Zugriff auf JARVIS
"""

import os
import json
import logging
from flask import Flask, render_template, jsonify, request
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

# In-memory storage for dashboard
dashboard_data = {
    "tasks": [],
    "services": {},
    "uptime_seconds": 0
}

class JARVISWebUIManager:
    """Manages communication between Dashboard and JARVIS APIs."""

    def __init__(self):
        self.coordinator_url = JARVIS_COORDINATOR_URL
        self.ollama_url = OLLAMA_URL
        self.whatsapp_url = WHATSAPP_GATEWAY_URL
        self.tasks_file = Path("jarvis_dashboard_tasks.json")
        self.load_tasks()

    def load_tasks(self):
        """Load tasks from file."""
        if self.tasks_file.exists():
            with open(self.tasks_file) as f:
                dashboard_data["tasks"] = json.load(f)

    def save_tasks(self):
        """Save tasks to file."""
        with open(self.tasks_file, 'w') as f:
            json.dump(dashboard_data["tasks"], f, indent=2)

    def add_task(self, title: str, priority: str = "medium") -> dict:
        """Add new task to JARVIS."""
        task = {
            "id": datetime.now().timestamp(),
            "title": title,
            "completed": False,
            "priority": priority,
            "created": datetime.now().isoformat()
        }
        dashboard_data["tasks"].append(task)
        self.save_tasks()

        # Send to JARVIS as instruction
        try:
            response = requests.post(
                f"{self.coordinator_url}/process_instruction",
                json={
                    "user_id": "dashboard",
                    "instruction": f"Neue Aufgabe erstellt: {title}",
                    "channel": "web_dashboard"
                },
                timeout=10
            )
            logger.info(f"Task sent to JARVIS: {title}")
        except Exception as e:
            logger.warning(f"Could not notify JARVIS: {e}")

        return task

    def complete_task(self, task_id: float) -> dict:
        """Mark task as completed."""
        task = next((t for t in dashboard_data["tasks"] if t["id"] == task_id), None)
        if task:
            task["completed"] = True
            self.save_tasks()
            return task
        return None

    def delete_task(self, task_id: float) -> bool:
        """Delete task."""
        initial_count = len(dashboard_data["tasks"])
        dashboard_data["tasks"] = [t for t in dashboard_data["tasks"] if t["id"] != task_id]
        if len(dashboard_data["tasks"]) < initial_count:
            self.save_tasks()
            return True
        return False

    def get_service_status(self) -> dict:
        """Check status of all JARVIS services."""
        services = {
            "coordinator": {"url": self.coordinator_url, "port": 8000},
            "ollama": {"url": self.ollama_url, "port": 11434},
            "whatsapp_gateway": {"url": self.whatsapp_url, "port": 5000}
        }

        status = {}
        for name, config in services.items():
            try:
                response = requests.get(f"{config['url']}/health", timeout=2)
                status[name] = {
                    "status": "online" if response.status_code == 200 else "offline",
                    "latency_ms": response.elapsed.total_seconds() * 1000,
                    "port": config["port"]
                }
            except:
                status[name] = {
                    "status": "offline",
                    "latency_ms": 0,
                    "port": config["port"]
                }

        dashboard_data["services"] = status
        return status

    def send_instruction_to_jarvis(self, instruction: str) -> dict:
        """Send instruction to JARVIS Coordinator."""
        try:
            response = requests.post(
                f"{self.coordinator_url}/process_instruction",
                json={
                    "user_id": "web_dashboard",
                    "instruction": instruction,
                    "channel": "web_ui"
                },
                timeout=30
            )

            if response.status_code == 200:
                return response.json()
            else:
                return {"error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"error": str(e)}

# Initialize manager
manager = JARVISWebUIManager()

# ============================================================================
# API Routes
# ============================================================================

@app.route("/api/tasks", methods=["GET"])
def get_tasks():
    """Get all tasks."""
    return jsonify(dashboard_data["tasks"])

@app.route("/api/tasks", methods=["POST"])
def create_task():
    """Create new task."""
    data = request.get_json()
    task = manager.add_task(data.get("title", ""), data.get("priority", "medium"))
    return jsonify(task), 201

@app.route("/api/tasks/<task_id>", methods=["PUT"])
def update_task(task_id):
    """Update task (mark as complete)."""
    task_id = float(task_id)
    task = manager.complete_task(task_id)
    return jsonify(task) if task else jsonify({"error": "Not found"}), 404

@app.route("/api/tasks/<task_id>", methods=["DELETE"])
def delete_task(task_id):
    """Delete task."""
    task_id = float(task_id)
    success = manager.delete_task(task_id)
    return jsonify({"success": success}), 200 if success else 404

@app.route("/api/services", methods=["GET"])
def get_services():
    """Get service status."""
    status = manager.get_service_status()
    return jsonify(status)

@app.route("/api/coordinator/status", methods=["GET"])
def get_coordinator_status():
    """Get JARVIS Coordinator detailed status."""
    try:
        response = requests.get(
            f"{JARVIS_COORDINATOR_URL}/agent_status",
            timeout=5
        )
        if response.status_code == 200:
            return jsonify(response.json())
    except:
        pass
    return jsonify({"error": "Coordinator not available"}), 500

@app.route("/api/jarvis/process", methods=["POST"])
def process_instruction():
    """Send instruction to JARVIS."""
    data = request.get_json()
    instruction = data.get("instruction", "")

    if not instruction:
        return jsonify({"error": "No instruction provided"}), 400

    result = manager.send_instruction_to_jarvis(instruction)
    return jsonify(result)

@app.route("/api/dashboard/stats", methods=["GET"])
def get_dashboard_stats():
    """Get dashboard statistics."""
    services = manager.get_service_status()
    completed_tasks = sum(1 for t in dashboard_data["tasks"] if t["completed"])
    total_tasks = len(dashboard_data["tasks"])

    return jsonify({
        "timestamp": datetime.now().isoformat(),
        "services": services,
        "tasks": {
            "total": total_tasks,
            "completed": completed_tasks,
            "pending": total_tasks - completed_tasks
        },
        "system": {
            "coordinator": services.get("coordinator", {}),
            "ollama": services.get("ollama", {}),
            "gateway": services.get("whatsapp_gateway", {})
        }
    })

@app.route("/api/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "service": "JARVIS Web UI Server",
        "timestamp": datetime.now().isoformat(),
        "version": "2.0"
    })

# ============================================================================
# HTML Dashboard Routes
# ============================================================================

@app.route("/", methods=["GET"])
def serve_dashboard():
    """Serve JARVIS Command Center Dashboard."""
    dashboard_html = """
    <!DOCTYPE html>
    <html lang="de">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>🤖 JARVIS Command Center</title>
        <style>
            * {
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }

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
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, var(--darker) 0%, #1a1f3a 100%);
                color: #ffffff;
                overflow-x: hidden;
                min-height: 100vh;
            }

            .container {
                display: grid;
                grid-template-columns: 250px 1fr;
                gap: 20px;
                padding: 20px;
                max-width: 1800px;
                margin: 0 auto;
            }

            .sidebar {
                background: rgba(15, 23, 42, 0.85);
                backdrop-filter: blur(20px);
                border: 1px solid rgba(0, 217, 255, 0.3);
                border-radius: 15px;
                box-shadow: 0 8px 32px rgba(0, 217, 255, 0.1);
                border-radius: 15px;
                padding: 20px;
                height: fit-content;
                position: sticky;
                top: 20px;
            }

            .sidebar h2 {
                font-size: 18px;
                margin-bottom: 20px;
                color: var(--primary);
            }

            .sidebar-item {
                padding: 12px 15px;
                margin: 8px 0;
                background: rgba(0, 217, 255, 0.05);
                border-left: 3px solid transparent;
                border-radius: 8px;
                cursor: pointer;
                transition: all 0.3s ease;
                font-size: 14px;
            }

            .sidebar-item:hover {
                background: rgba(0, 217, 255, 0.15);
                border-left-color: var(--primary);
            }

            .sidebar-item.active {
                background: rgba(0, 217, 255, 0.25);
                border-left-color: var(--primary);
                font-weight: bold;
            }

            .main-content {
                display: grid;
                grid-template-rows: auto 1fr;
                gap: 20px;
            }

            .header {
                background: rgba(10, 14, 39, 0.8);
                backdrop-filter: blur(10px);
                border: 1px solid rgba(0, 217, 255, 0.2);
                border-radius: 15px;
                padding: 25px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }

            .header h1 {
                font-size: 32px;
                color: var(--primary);
            }

            .status-indicator {
                display: inline-block;
                width: 12px;
                height: 12px;
                background: var(--success);
                border-radius: 50%;
                margin-right: 8px;
                animation: pulse 2s infinite;
            }

            @keyframes pulse {
                0%, 100% { opacity: 1; }
                50% { opacity: 0.5; }
            }

            .card {
                background: rgba(10, 14, 39, 0.8);
                backdrop-filter: blur(10px);
                border: 1px solid rgba(0, 217, 255, 0.2);
                border-radius: 15px;
                padding: 25px;
                transition: all 0.3s ease;
            }

            .card h3 {
                font-size: 18px;
                margin-bottom: 15px;
                color: var(--primary);
            }

            .input-group {
                display: flex;
                gap: 10px;
                margin-bottom: 15px;
            }

            .input-group input {
                flex: 1;
                background: rgba(0, 217, 255, 0.05);
                border: 1px solid rgba(0, 217, 255, 0.3);
                border-radius: 10px;
                padding: 12px 15px;
                color: #fff;
            }

            .input-group input:focus {
                outline: none;
                background: rgba(0, 217, 255, 0.1);
                border-color: var(--primary);
            }

            .btn {
                padding: 12px 25px;
                border: none;
                border-radius: 10px;
                background: linear-gradient(135deg, var(--primary), var(--accent));
                color: #000;
                font-weight: bold;
                cursor: pointer;
                transition: all 0.3s ease;
            }

            .btn:hover {
                transform: scale(1.05);
            }

            .task-item {
                background: rgba(0, 217, 255, 0.05);
                border: 1px solid rgba(0, 217, 255, 0.1);
                border-radius: 10px;
                padding: 15px;
                margin-bottom: 12px;
                display: flex;
                align-items: center;
                gap: 15px;
            }

            .task-checkbox {
                width: 20px;
                height: 20px;
                border: 2px solid var(--primary);
                border-radius: 5px;
                cursor: pointer;
                transition: all 0.3s ease;
            }

            .task-checkbox.checked {
                background: var(--success);
                border-color: var(--success);
            }

            .task-content {
                flex: 1;
            }

            .task-title {
                font-weight: bold;
            }

            .service {
                display: flex;
                justify-content: space-between;
                padding: 12px;
                background: rgba(0, 217, 255, 0.05);
                border-radius: 8px;
                margin-bottom: 10px;
            }

            .service-dot {
                width: 8px;
                height: 8px;
                border-radius: 50%;
                background: var(--success);
                margin-right: 10px;
            }

            .service-dot.offline {
                background: var(--danger);
            }

            .grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
                gap: 20px;
            }

            @media (max-width: 1024px) {
                .container {
                    grid-template-columns: 1fr;
                }
                .sidebar {
                    position: relative;
                    top: 0;
                }
                .grid {
                    grid-template-columns: 1fr;
                }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="sidebar">
                <h2>⚙️ Navigation</h2>
                <div class="sidebar-item active" onclick="switchTab('tasks')">📋 Aufgaben</div>
                <div class="sidebar-item" onclick="switchTab('services')">🔧 Services</div>
                <div class="sidebar-item" onclick="switchTab('jarvis')">🤖 JARVIS</div>
            </div>

            <div class="main-content">
                <div class="header">
                    <div>
                        <h1>🤖 JARVIS Command Center</h1>
                        <p style="color: #888; margin-top: 5px;">Web UI Connected to Local JARVIS</p>
                    </div>
                    <div style="text-align: center;">
                        <span class="status-indicator"></span><span style="color: #888;">ONLINE</span>
                    </div>
                </div>

                <div class="grid">
                    <div id="tasks-tab" class="card">
                        <h3>📋 Meine Aufgaben</h3>
                        <div class="input-group">
                            <input type="text" id="taskInput" placeholder="Neue Aufgabe..." />
                            <button class="btn" onclick="addTask()">+ HINZUFÜGEN</button>
                        </div>
                        <div id="taskList"></div>
                    </div>

                    <div id="services-tab" class="card" style="display: none;">
                        <h3>🔧 Service Status</h3>
                        <div id="serviceList"></div>
                        <button class="btn" onclick="refreshServices()" style="width: 100%; margin-top: 15px;">🔄 AKTUALISIEREN</button>
                    </div>

                    <div id="jarvis-tab" class="card" style="display: none;">
                        <h3>🤖 JARVIS Coordinator</h3>
                        <div class="input-group">
                            <input type="text" id="instructionInput" placeholder="Anweisung an JARVIS..." />
                            <button class="btn" onclick="sendInstruction()">➤ SENDEN</button>
                        </div>
                        <div id="jarvisResponse" style="background: rgba(0, 0, 0, 0.3); border: 1px solid var(--primary); border-radius: 10px; padding: 15px; font-size: 13px; max-height: 300px; overflow-y: auto;"></div>
                    </div>

                    <div class="card">
                        <h3>ℹ️ System Info</h3>
                        <div id="systemInfo" style="font-size: 13px; line-height: 1.8;"></div>
                    </div>
                </div>
            </div>
        </div>

        <script>
            // API Communication
            const API_URL = window.location.origin + '/api';

            function switchTab(tab) {
                document.querySelectorAll('[id$="-tab"]').forEach(el => el.style.display = 'none');
                document.getElementById(tab + '-tab').style.display = 'block';
                document.querySelectorAll('.sidebar-item').forEach(el => el.classList.remove('active'));
                event.target.classList.add('active');
            }

            function renderTasks() {
                fetch(API_URL + '/tasks')
                    .then(r => r.json())
                    .then(tasks => {
                        const list = document.getElementById('taskList');
                        list.innerHTML = tasks.map(task => `
                            <div class="task-item">
                                <div class="task-checkbox ${task.completed ? 'checked' : ''}" onclick="toggleTask(${task.id})"></div>
                                <div class="task-content">
                                    <div class="task-title">${task.title}</div>
                                    <div style="font-size: 12px; color: #888;">${new Date(task.created).toLocaleString('de-DE')}</div>
                                </div>
                                <button class="btn" onclick="deleteTask(${task.id})" style="padding: 8px 12px; font-size: 12px;">×</button>
                            </div>
                        `).join('');
                    });
            }

            function addTask() {
                const title = document.getElementById('taskInput').value;
                if (title.trim()) {
                    fetch(API_URL + '/tasks', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ title, priority: 'medium' })
                    }).then(() => {
                        document.getElementById('taskInput').value = '';
                        renderTasks();
                    });
                }
            }

            function toggleTask(id) {
                fetch(API_URL + '/tasks/' + id, { method: 'PUT' })
                    .then(() => renderTasks());
            }

            function deleteTask(id) {
                fetch(API_URL + '/tasks/' + id, { method: 'DELETE' })
                    .then(() => renderTasks());
            }

            function refreshServices() {
                fetch(API_URL + '/services')
                    .then(r => r.json())
                    .then(services => {
                        const list = document.getElementById('serviceList');
                        list.innerHTML = Object.entries(services).map(([name, status]) => `
                            <div class="service">
                                <div>
                                    <div style="display: flex; align-items: center;">
                                        <div class="service-dot ${status.status === 'online' ? '' : 'offline'}"></div>
                                        <span>${name}</span>
                                    </div>
                                    <div style="font-size: 12px; color: #888; margin-left: 18px;">Port ${status.port}</div>
                                </div>
                                <div style="text-align: right;">
                                    <div style="color: var(--primary);">${status.latency_ms.toFixed(0)}ms</div>
                                    <div style="font-size: 12px; color: #888;">${status.status.toUpperCase()}</div>
                                </div>
                            </div>
                        `).join('');
                    });
            }

            function sendInstruction() {
                const instruction = document.getElementById('instructionInput').value;
                if (instruction.trim()) {
                    document.getElementById('instructionInput').value = '';
                    const response_div = document.getElementById('jarvisResponse');
                    response_div.innerHTML = '<span style="color: #888;">⏳ Verarbeitung...</span>';

                    fetch(API_URL + '/jarvis/process', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ instruction })
                    }).then(r => r.json())
                      .then(data => {
                          response_div.innerHTML = '<pre>' + JSON.stringify(data, null, 2) + '</pre>';
                      });
                }
            }

            function updateSystemInfo() {
                fetch(API_URL + '/dashboard/stats')
                    .then(r => r.json())
                    .then(stats => {
                        const info = document.getElementById('systemInfo');
                        info.innerHTML = `
                            <div>📊 <strong>Aufgaben:</strong> ${stats.tasks.completed}/${stats.tasks.total} abgeschlossen</div>
                            <div>🔧 <strong>Services:</strong> <span style="color: var(--success);">ALLE ONLINE</span></div>
                            <div>⚡ <strong>API Latenz:</strong> ${(stats.system.coordinator?.latency_ms || 0).toFixed(0)}ms</div>
                            <div>🧠 <strong>Engine:</strong> Ollama (Mistral 7B)</div>
                        `;
                    });
            }

            // Initialize
            renderTasks();
            refreshServices();
            updateSystemInfo();
            setInterval(refreshServices, 5000);
            setInterval(updateSystemInfo, 5000);
        </script>
    </body>
    </html>
    """
    return dashboard_html

# ============================================================================
# Main
# ============================================================================

def main():
    print("\n" + "="*70)
    print("🤖 JARVIS WEB UI SERVER")
    print("="*70)
    print("\n📡 Configuration:")
    print(f"   JARVIS Coordinator: {JARVIS_COORDINATOR_URL}")
    print(f"   Ollama LLM:         {OLLAMA_URL}")
    print(f"   WhatsApp Gateway:   {WHATSAPP_GATEWAY_URL}")
    print("\n🌐 Web UI Access:")
    print(f"   URL: http://localhost:3000")
    print(f"   API: http://localhost:3000/api")
    print("\n📚 API Endpoints:")
    print(f"   GET  /api/tasks           - Get all tasks")
    print(f"   POST /api/tasks           - Create task")
    print(f"   GET  /api/services        - Get service status")
    print(f"   GET  /api/dashboard/stats - Get dashboard stats")
    print(f"   POST /api/jarvis/process  - Send instruction to JARVIS")
    print("\n" + "="*70 + "\n")

    # Start server
    print("🚀 Starting Web UI Server on localhost:3000...")
    app.run(host="0.0.0.0", port=3000, debug=False)


if __name__ == "__main__":
    main()
