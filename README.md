# PDF File Converter

Flask web app that converts BKT bank statement and POS PDFs to CSV.

## Run locally

### 1. Create a virtual environment (recommended)

```powershell
cd C:\Users\marig\EA-TECH\pdf_file_converter
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

### 3. Start the app

```powershell
python app.py
```

The app runs at **http://127.0.0.1:5000** (default). Use `PORT=8080 python app.py` to use another port.

### 4. Use the app

1. Open http://127.0.0.1:5000 in your browser.
2. Upload a PDF (bank statement or POS) or choose a file already in `uploads/`.
3. Download the generated CSV from `results/`.

---

**Without a venv:** ensure Python 3.8+ is available, then run `pip install -r requirements.txt` and `python app.py` from the project folder.

---

## Deploy to server (GitHub Actions)

Deploys on push to `main` or via **Run workflow** (Actions → Deploy to server).

### 1. Server setup

- **pm2** installed (e.g. `npm i -g pm2`)
- **Python 3** and **rsync** available
- SSH access with the deploy key (public key in `~/.ssh/authorized_keys`)

### 2. GitHub Secrets

In **Settings → Secrets and variables → Actions**, add:

| Secret | Description |
|--------|-------------|
| `SSH_PRIVATE_KEY` | **Base64-encoded** SSH **private** key. Run `cat ~/.ssh/id_rsa | base64 -w 0` (Linux) or `base64 < ~/.ssh/id_rsa | tr -d '\n'` (macOS), then paste the **one-line** output. The matching **public** key must be in the server’s `authorized_keys`. |
| `SERVER_HOST` | Server hostname or IP (e.g. `95.211.222.182`) |
| `SERVER_USER` | SSH user (e.g. `jenkins`, `ubuntu`) |
| `SERVER_PATH` | Deploy directory on the server (e.g. `/www/wwwroot/pdf-file-converter`). Will be created by rsync if missing. |

### 3. Deploy flow

1. **Rsync** copies the project to `SERVER_PATH` (excluding `.git`, `.venv`, `uploads`, `results`, etc.).
2. **SSH** into the server and runs `./install-test.sh`:
   - Creates `.venv`, installs deps, starts the app with **pm2** as `pdf-converter`.

App runs on port **5000** by default. Use nginx or another reverse proxy if you want HTTPS.

**Change port:**
- **Local:** `PORT=8080 python app.py` or set `PORT` before running.
- **Server (install-test.sh):** set `APP_PORT=8080` at the top of the script, or run `APP_PORT=8080 ./install-test.sh`.

**Deploy fails with "error in libcrypto" or "Permission denied (publickey)":**
- Use the **private** key (e.g. `id_rsa`), not the public (`id_rsa.pub`). Encode it: `cat ~/.ssh/id_rsa | base64 -w 0`, then put that **one line** in `SSH_PRIVATE_KEY`. Base64 avoids newline/encoding corruption from pasting.
- Ensure the server’s `~/.ssh/authorized_keys` contains the **public** key for the deploy user.
