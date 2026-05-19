# webapp build
```bash
docker build -t "ais_webapp:latest" .
docker run -d --rm -p 5000:5000 ais_webapp:latest
```