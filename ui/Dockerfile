FROM nginxinc/nginx-unprivileged:1.27-alpine

# Trivy (CI) fails on fixable HIGH/CRITICAL: take the latest Alpine patches.
USER root
RUN apk upgrade --no-cache

COPY nginx.conf /etc/nginx/nginx.conf
COPY index.html /usr/share/nginx/html/index.html

USER 101

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
