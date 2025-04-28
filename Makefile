local-prod:
	@echo "Building and starting Docker containers using .env.development.local..."
	docker compose --env-file .env.development.local up -d --build | tee docker_up_build.log
	@echo "Docker containers are up and running in detached mode."

native-dev:
	cd apps/native && pnpm run web