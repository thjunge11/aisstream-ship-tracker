## One-time EC2 setup (run once after launching the instance)
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
