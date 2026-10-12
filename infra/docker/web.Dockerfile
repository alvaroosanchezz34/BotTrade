# Frontend estático + Caddy (TLS y proxy inverso). Es el ÚNICO servicio expuesto.
FROM node:22-alpine AS build
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

FROM caddy:2-alpine
COPY --from=build /src/dist /srv
COPY infra/caddy/Caddyfile /etc/caddy/Caddyfile
