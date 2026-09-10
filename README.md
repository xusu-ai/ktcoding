# KtCoding — Online Code Showcase & Playground

A lightweight online code editor, preview, and sharing platform with AI-powered coding assistance.

## ✨ Features

- **Live Code Editor** — Edit HTML, CSS, and JavaScript with syntax highlighting
- **Real-time Preview** — See your changes instantly as you type
- **Device Simulation** — Preview on desktop, tablet (Xiaomi), and mobile (Apple) layouts
- **One-click Share** — Publish your code snippets and share via URL
- **AI Coding Assistant** — Built-in chatbot powered by LLMs to generate code from natural language descriptions
- **Works Gallery** — Browse and discover community-created projects
- **Zero Dependencies** — Pure frontend, no build tools or frameworks required

## 🚀 Quick Start

### Option 1: Direct Run

```bash
python3 server.py
```

Running `python3 server.py` directly listens on `127.0.0.1:3000` by default. In production, a systemd unit starts it with `SHOWCODE_PORT=13000` (listening on `127.0.0.1:13000`), served to the outside world through an nginx reverse proxy.

### Option 2: Deploy Script (Recommended)

```bash
sudo ./deploy.sh
```

This chains nginx configuration, reloads it, and starts the backend in one step.

## 🗂️ Project Structure

```
ktcoding/
├── index.html               # Homepage — Kuaitu Compute nav page
├── ui.html            # Main application — code editor & gallery UI
├── quota.html               # Compute quota (placeholder page)
├── guide.html               # Compute access guide (placeholder page)
├── server.py                # Python backend (stdlib only, handles /api/save)
├── projects/                # Saved projects land here (auto-created)
├── nginx.ktcoding.conf     # nginx site config (drop-in for new servers)
├── deploy.sh                # all-in-one deploy/ops script
├── README.md                # Project documentation (Chinese on master, English on github branch)
├── README.en.md             # English version (source for the github branch)
└── README.zh.md             # Chinese backup
```

## 🔁 Migration to a New Server

The business logic is decoupled from nginx: nginx only serves static files and reverse proxies, all application logic lives in `server.py`.

```bash
# 1) On the target server: install nginx and Python
sudo apt update && sudo apt install -y nginx python3

# 2) Copy the entire ktcoding/ directory to the target server (any path, e.g. /opt/ktcoding)
sudo mkdir -p /opt && sudo cp -r ktcoding /opt/

# 3) Run the deployment script
cd /opt/ktcoding && sudo ./deploy.sh
```

`deploy.sh` sub-commands:

| Command | Purpose |
|---------|---------|
| `sudo ./deploy.sh` | Full deploy: link nginx config + reload + start backend |
| `sudo ./deploy.sh start` / `stop` / `restart` / `status` | Backend operations |
| `sudo ./deploy.sh service` | Install as a systemd service (no separate .service file needed) |

### Port & Directory Configuration

Only two changes needed in `nginx.ktcoding.conf`:

- `root /ktcoding;` — change to your actual directory path
- `proxy_pass http://127.0.0.1:13000/api/;` — backend port (production uses 13000, kept in sync with PORT in deploy.sh)

The backend port defaults to 3000 and is configurable via the `SHOWCODE_PORT`/`SHOWCODE_BIND` environment variables (production systemd uses 13000); keep the `proxy_pass` port in `nginx.ktcoding.conf` in sync.

## 🧰 Tech Stack

- **Frontend** — Vanilla HTML/CSS/JavaScript (no frameworks)
- **Backend** — Python 3 `http.server` (stdlib, zero dependencies)
- **AI API** — OpenAI-compatible endpoints (configurable)
- **Deployment** — systemd, supports reverse proxy (Nginx)

## 🤖 AI Coding Assistant

The built-in AI assistant connects to OpenAI-compatible API endpoints:

| Model | Endpoint | Status |
|-------|----------|--------|
| Qwen3.8-27B | `:8106/v1` | Ready |
| deepseek-v4-flash | `:8104/v1` | Ready |

The assistant maintains conversation context, generates complete HTML pages, and supports code extraction and execution directly in the editor.

## 🌐 Deployment

### Nginx Reverse Proxy

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:13000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### Port Configuration

The backend port defaults to 3000. Override it with the `SHOWCODE_PORT` environment variable (and `SHOWCODE_BIND` for the bind address); production systemd sets `SHOWCODE_PORT=13000`. Example:

```bash
SHOWCODE_PORT=13000 SHOWCODE_BIND=127.0.0.1 python3 server.py
```

## 📄 License

MIT License

---

*Built with ❤️ for developers who love to show and share their code.*
