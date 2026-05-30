FROM nginx:stable

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         git fcgiwrap spawn-fcgi \
         python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY nginx.conf /etc/nginx/nginx.conf

COPY index.html /usr/share/nginx/html/index.html
COPY plugins/ /usr/share/nginx/html/plugins/
COPY .claude-plugin/ /usr/share/nginx/html/.claude-plugin/
COPY .factory-plugin/ /usr/share/nginx/html/.factory-plugin/
COPY Makefile /usr/share/nginx/html/Makefile
COPY repo.html /usr/share/nginx/html/repo.html
COPY admin.html /usr/share/nginx/html/admin.html

COPY ingest.py /opt/ingest/ingest.py
COPY start.sh /start.sh
RUN chmod +x /start.sh /opt/ingest/ingest.py

EXPOSE 80

CMD ["/start.sh"]
