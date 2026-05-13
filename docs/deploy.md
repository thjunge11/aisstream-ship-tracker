## 1 — One-time EC2 setup (run once after launching the instance)
``` bash
# Install Docker and git
sudo apt-get update && sudo apt-get install -y docker.io git

# Install Docker Compose plugin
sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
     -o /usr/local/bin/docker-compose && sudo chmod +x /usr/local/bin/docker-compose

# Add user to docker group (re-login after this)
sudo usermod -aG docker $USER

# Clone the repo
git clone https://github.com/thjunge11/myCapstoneProject.git ~/myCapstoneProject
```
## 2 — Add 4 GitHub Secrets
Go to Settings → Secrets and variables → Actions in your repo and add:

| Secret | Value |
|---|---|
| `EC2_HOST` | EC2 public IP or DNS |
| `EC2_USERNAME` | `ubuntu` (Ubuntu AMI) or `ec2-user` |
| `EC2_SSH_KEY` | Full contents of your `.pem` file |
| `ENV_FILE` | Full contents of your local `.env` file |

## 3 — How it works
On every push to main the workflow:

- SSHes into the EC2 instance
- Runs git pull origin main
- Writes the .env from the ENV_FILE secret (secrets never touch git)
- Runs docker compose up --build -d --remove-orphans to rebuild changed images and restart the full stack
- Prunes dangling images

You can also trigger it manually via the Actions → Run workflow button (workflow_dispatch).

## Security note: 
Your .env currently has plaintext secrets. The ENV_FILE secret approach keeps them out of the repo while still injecting them on the EC2 at deploy time. Make sure the EC2 security group only allows inbound SSH (port 22) from trusted IPs.