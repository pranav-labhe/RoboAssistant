# 🚀 Voice Gateway — Local & AWS EC2 (`t4g.small`) Deployment Guide

## 1. Local Laptop Mode (Default for Breadboard Gates 6–12)

1. Your outside-repo secrets file is already configured at:
   * `C:\Users\DELL\.robo_secrets\gateway.env` (loaded automatically by `gateway/app.py`)
   * `C:\Users\DELL\.robo_secrets\robot_secrets.h` (included automatically by PlatformIO via `-IC:/Users/DELL/.robo_secrets`)
2. Start the Gateway on your laptop:
   ```powershell
   cd c:\Robo\gateway
   uvicorn app:app --host 0.0.0.0 --port 8002
   ```
3. Verify health from PowerShell:
   ```powershell
   curl.exe -i http://127.0.0.1:8002/health -H "Authorization: Bearer <YOUR_GATEWAY_BEARER_TOKEN>"
   ```

---

## 2. AWS EC2 (`t4g.small` ARM64, 2 GiB RAM) Co-Deployment Alongside Chit

Your EC2 instance (`15.252.205.123` / `api.chitt.online`) runs **Chit** (`pranav.chit.api` on `127.0.0.1:8000` and `pranav.chit.ui` on `127.0.0.1:8501`) behind **Caddy v2.11.4**.

### Step A — Verify Swap Is Active on EC2 (2 GiB RAM Guardrail)
```bash
free -m
# If Swap shows 0B, create a 2 GiB swapfile:
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### Step B — Build & Run the Gateway Container with a 192 MB Memory Cap
Copy `c:\Robo\gateway` to `/home/ubuntu/robo-gateway` on EC2, create `/home/ubuntu/.robo_secrets/gateway.env` (`chmod 600`), and run:

```bash
cd /home/ubuntu/robo-gateway
docker build -f Containerfile -t pranav.robo.gateway:latest .

docker run -d \
  --name pranav.robo.gateway \
  --restart unless-stopped \
  --memory=192m \
  --memory-swap=384m \
  --env-file /home/ubuntu/.robo_secrets/gateway.env \
  -p 127.0.0.1:8002:8002 \
  pranav.robo.gateway:latest
```

Measure actual memory consumption alongside Chit:
```bash
docker stats --no-stream
```

### Step C — Add `/voice/*` Route to `/etc/caddy/Caddyfile`
Edit `/etc/caddy/Caddyfile` inside your `api.chitt.online` block so `/voice/*` proxies to port `8002` with unbuffered streaming (`flush_interval -1`):

```caddyfile
api.chitt.online {
    # Voice Robot Gateway (/voice/health, /voice/echo, /voice/talk)
    handle_path /voice/* {
        reverse_proxy 127.0.0.1:8002 {
            flush_interval -1
        }
    }

    # Existing Chit API routes
    handle {
        reverse_proxy 127.0.0.1:8000
    }
}
```

Reload Caddy with zero downtime:
```bash
sudo systemctl reload caddy
```

### Step D — Point the Robot to EC2 HTTPS
In `C:\Users\DELL\.robo_secrets\robot_secrets.h`, set:
```cpp
#define GATEWAY_BASE_URL "https://api.chitt.online/voice"
```
When ready to switch the robot's brain from Gemini to **Chit**, change one line in `gateway.env`:
```ini
LLM_PROVIDER=chit
```
