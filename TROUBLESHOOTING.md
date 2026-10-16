# Docker installation troubleshooting

See [the complete installation guide](docs/docker-install.md) for configuration, services, feature prerequisites and migration.

- Run `docker compose ps -a` and `docker compose logs --tail 100` after a startup failure.
- Run `./scripts/install-docker.sh --check` to exercise database/vector operations, S3 round trips, Redis pub/sub, search results and Chromium.
- If a model is missing, verify its OpenAI-compatible API URL and credentials in Admin > Connections. Use `host.docker.internal` for a service on the Docker host.
- If search returns no results, inspect `docker compose logs searxng` and the server's outbound access to search engines. JSON API health alone does not establish usable search results.
- If RAG fails after changing embedding models, match the pgvector dimensions and rebuild affected knowledge collections.
- Persisted administrator settings take precedence over initial environment defaults. Change them in the administrator UI when updating an existing installation.
- Image builds need access to package registries, Hugging Face and Chromium downloads. Check Docker's proxy/DNS configuration when pull or dependency downloads time out.
- Keep `.env` and all data volumes when recreating containers. Never use `down -v` to troubleshoot a production instance.
