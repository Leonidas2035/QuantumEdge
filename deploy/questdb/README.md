# QuestDB (Docker) - 16GB profile

This directory provides a docker-compose stack for QuestDB with a 16GB memory limit and a conservative baseline config.

## Start

```bash
docker compose -f deploy/questdb/docker-compose.yml up -d
```

Or use helpers:

```bash
./deploy/questdb/up.sh
```

## Stop

```bash
docker compose -f deploy/questdb/docker-compose.yml down
```

Or use helpers:

```bash
./deploy/questdb/down.sh
```

## Health

```bash
curl -f http://localhost:9003/health
# or
curl -f "http://localhost:9000/exec?query=select+1"
```

If the container lacks `wget` the built-in healthcheck may fail; adjust `healthcheck.test` as needed.

## Ports

- 9000: HTTP console + REST (/exec, /imp)
- 9003: Health endpoint (/health)
- 9009: ILP TCP
- 8812: Postgres wire protocol

## Data location

Data is stored in the Docker volume `questdb_data` mapped to `/var/lib/questdb`.

To inspect the volume:

```bash
docker volume inspect questdb_data
```

## Config

- `deploy/questdb/conf/server.conf` contains conservative defaults.
- Tune in Stage 7 based on ingestion benchmarks.
