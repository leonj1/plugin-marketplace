PORT := 8081
IMAGE := droid-plugin-marketplace
CONTAINER := droid-plugin-marketplace

.PHONY: build start stop restart status test

build:
	docker build -t $(IMAGE) .

start:
	@if docker ps --format '{{.Names}}' | grep -q '^$(CONTAINER)$$'; then \
		echo "Container already running on http://localhost:$(PORT)"; \
	else \
		docker run -d --name $(CONTAINER) -p $(PORT):80 $(IMAGE); \
		echo "Server started on http://localhost:$(PORT)"; \
	fi

stop:
	@if docker ps -a --format '{{.Names}}' | grep -q '^$(CONTAINER)$$'; then \
		docker rm -f $(CONTAINER) >/dev/null && echo "Server stopped"; \
	else \
		echo "Server is not running"; \
	fi

restart: stop start

status:
	@if docker ps --format '{{.Names}}' | grep -q '^$(CONTAINER)$$'; then \
		echo "Server running on http://localhost:$(PORT)"; \
	else \
		echo "Server is not running"; \
	fi

test:
	@MARKETPLACE_URL=http://localhost:$(PORT) bash test-clone.sh
