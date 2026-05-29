FROM nginx:alpine
COPY index.html /usr/share/nginx/html/index.html
COPY plugins/ /usr/share/nginx/html/plugins/
COPY .claude-plugin/ /usr/share/nginx/html/.claude-plugin/
EXPOSE 80
