# Deploying to a free, always-on Oracle Cloud VM

This replaces the Render deployment with a self-managed server that never sleeps and costs $0 forever. You do the account/VM setup yourself (it needs your own identity/payment verification); I'll give you exact commands to paste for everything after that.

## Part 1 — Things only you can do (Oracle's own account/console)

### 1.1 Sign up for Oracle Cloud Free Tier
Go to [oracle.com/cloud/free](https://oracle.com/cloud/free) and sign up. You'll need:
- An email address and phone number (for verification)
- A payment card, for identity verification only — Oracle's Always Free resources are never billed unless you explicitly upgrade to a paid account later.

### 1.2 Create the VM instance
In the Oracle Cloud Console, go to **Compute → Instances → Create Instance**:
- **Name**: `treebot-server` (or anything)
- **Image**: Canonical **Ubuntu** (latest LTS — 22.04 or 24.04)
- **Shape**: click "Change shape" → choose **VM.Standard.E2.1.Micro** (AMD, "Always Free" eligible). This one over the ARM "A1.Flex" shape deliberately — E2.1.Micro is always available, while A1.Flex sometimes hits "out of capacity" errors for new signups.
- **Networking**: leave defaults (creates a new VCN), but check **"Assign a public IPv4 address"** is ticked.
- **Add SSH keys**: choose "Generate a key pair for me" and **download the private key file** (e.g. `ssh-key-....key`) — you'll need this to connect. Keep it somewhere safe.
- Click **Create**. Wait a few minutes for it to reach "Running" state.
- Note the instance's **Public IP address** shown on its details page.

### 1.3 Open ports 80 and 443 (Oracle blocks these by default)
Still in the instance's details page → click the **Subnet** link → click the **Default Security List** → **Add Ingress Rules**, add two rules:
- Source CIDR `0.0.0.0/0`, IP Protocol TCP, Destination Port Range `80`
- Source CIDR `0.0.0.0/0`, IP Protocol TCP, Destination Port Range `443`

(Port 22 for SSH should already be open by default.)

### 1.4 Set up the free DuckDNS subdomain
1. Go to [duckdns.org](https://www.duckdns.org) and sign in (GitHub/Google/etc.).
2. Under "add domain", type a subdomain name (e.g. `treebot-park` → becomes `treebot-park.duckdns.org`) and click "add domain".
3. Set its IP to your Oracle VM's public IP address from step 1.2, click "update ip".

That's the only manual setup — everything from here is commands to paste.

## Part 2 — Connect and set up the server

From your own terminal (Git Bash, since you already have it):

```bash
chmod 400 /path/to/your-downloaded-key.key
ssh -i /path/to/your-downloaded-key.key ubuntu@<VM_PUBLIC_IP>
```

Once connected (you're now on the VM), install Docker:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
exit
```

Reconnect (so the docker group membership takes effect):

```bash
ssh -i /path/to/your-downloaded-key.key ubuntu@<VM_PUBLIC_IP>
```

Clone the project and set up the environment file:

```bash
git clone https://github.com/Jinal-ds/treebot.git
cd treebot/treebot
cp .env.vm.example .env
nano .env
```

In `nano`, fill in real values for `POSTGRES_PASSWORD` (make one up, keep it safe), `HF_TOKEN` (your Hugging Face token), `BASE_URL` and `DOMAIN` (both `https://treebot-park.duckdns.org` / `treebot-park.duckdns.org` — use your actual DuckDNS subdomain from step 1.4). Save with `Ctrl+O`, `Enter`, exit with `Ctrl+X`.

Start everything:

```bash
docker compose up -d --build
```

First build takes a few minutes (installing `chromadb`'s dependencies). Caddy will automatically request a free HTTPS certificate for your domain the first time it starts — give it a minute.

## Part 3 — Verify

```bash
curl https://treebot-park.duckdns.org/healthz
```

Should return `{"status":"ok",...}`. Visit `https://treebot-park.duckdns.org/t/mango` (or any tree id) in a browser to confirm the full page loads — and test the speech-to-text mic button now that it's on real HTTPS.

## Keeping it running

- `docker compose ps` — check container status
- `docker compose logs -f app` — tail the app's logs
- `docker compose restart app` — restart just the app after a code change
- To deploy a code update: `git pull && docker compose up -d --build`
- The VM reboots automatically restart everything (`restart: unless-stopped` on every service) — no manual intervention needed after a power-cycle.
