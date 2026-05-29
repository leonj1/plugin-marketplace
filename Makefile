PORT := 36287
PIDFILE := .server.pid

.PHONY: start stop status

start:
	@if [ -f $(PIDFILE) ] && kill -0 $$(cat $(PIDFILE)) 2>/dev/null; then \
		echo "Server already running on port $(PORT) (pid $$(cat $(PIDFILE)))"; \
	else \
		python3 -m http.server $(PORT) &>/dev/null & \
		echo $$! > $(PIDFILE); \
		echo "Server started on http://localhost:$(PORT) (pid $$!)"; \
	fi

stop:
	@if [ -f $(PIDFILE) ] && kill -0 $$(cat $(PIDFILE)) 2>/dev/null; then \
		kill $$(cat $(PIDFILE)); \
		rm -f $(PIDFILE); \
		echo "Server stopped"; \
	else \
		rm -f $(PIDFILE); \
		echo "Server is not running"; \
	fi

status:
	@if [ -f $(PIDFILE) ] && kill -0 $$(cat $(PIDFILE)) 2>/dev/null; then \
		echo "Server running on http://localhost:$(PORT) (pid $$(cat $(PIDFILE)))"; \
	else \
		echo "Server is not running"; \
	fi
